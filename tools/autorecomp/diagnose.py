"""Permissive-boot diagnostic sweep: which import does each game reach first?

Strict boot refuses to start when any IAT import is unresolved, so a title that
is missing one shim tells us nothing about the shim it would have reached first.
This tool runs each rebuilt binary with RECOMP_PERMISSIVE=1, which binds
unresolved imports to the runtime's self-naming stub: the guest runs until it
calls an unimplemented import, then the runtime prints

    [win32hle] FATAL: unimplemented import <NAME> (0x<VA>) called from guest 0x<CALLER>

and aborts. That name is the first blocker the running program actually hit -
measured from a real process launch, never inferred.

The output is deliberately blunt about failure. A project that cannot be built
or launched, or that runs past the timeout without reaching an unimplemented
import, records that raw outcome and no import name.

    python3 tools/autorecomp/diagnose.py --timeout 15
    python3 tools/autorecomp/diagnose.py --timeout 15 --build   # rebuild first
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PROJECTS_DIR = ROOT / "projects"
DEFAULT_JSON = ROOT / "reports" / "games" / "diagnostic-sweep.json"
DEFAULT_MD = ROOT / "reports" / "games" / "diagnostic-sweep.md"
DEFAULT_RESULTS = ROOT / "reports" / "games" / "results.json"

# hle_fatal("unimplemented import %s (0x%08X) called from guest 0x%08X", ...)
UNIMPLEMENTED_RE = re.compile(
    r"unimplemented import\s+(?P<name>\S+)\s+"
    r"\((?P<va>0x[0-9A-Fa-f]+)\)\s+"
    r"called from guest\s+(?P<caller>0x[0-9A-Fa-f]+)")
# "[host] <path> booted: entry 0x... , N unresolved imports"
BOOTED_RE = re.compile(r"booted: entry\s+(?P<entry>0x[0-9A-Fa-f]+),\s+"
                       r"(?P<unresolved>\d+)\s+unresolved imports")
# "[pe] bound N imports, M unresolved"
BOUND_RE = re.compile(r"bound\s+(?P<bound>\d+)\s+imports,\s+"
                      r"(?P<unresolved>\d+)\s+unresolved")

TAIL_CHARS = 4000


def parse_first_import(text):
    """First `unimplemented import` hle_fatal in `text`, or None.

    Pure string function: it reports only what the runtime actually printed.
    """
    match = UNIMPLEMENTED_RE.search(text or "")
    if match is None:
        return None
    return {
        "import": match.group("name"),
        "stub_va": match.group("va"),
        "caller": match.group("caller"),
        "line": match.group(0),
    }


def parse_boot_line(text):
    """Entry VA and unresolved-import count from the runtime's boot line."""
    match = BOOTED_RE.search(text or "")
    if match is None:
        return None
    return {"entry": match.group("entry"),
            "unresolved_iat_imports": int(match.group("unresolved"))}


def parse_bound_line(text):
    """Loader's static IAT bind summary, if the loader printed one."""
    match = BOUND_RE.search(text or "")
    if match is None:
        return None
    return {"iat_bound": int(match.group("bound")),
            "iat_unresolved": int(match.group("unresolved"))}


def classify(exit_code, timed_out, hit):
    """A blunt outcome label. `hit` is the parsed unimplemented-import dict."""
    if timed_out:
        return "timeout"
    if hit is not None:
        return "reached-unimplemented-import"
    if exit_code == 0:
        return "exited-clean"
    if exit_code is not None and exit_code < 0:
        return "crashed"
    return "exited-error"


def signal_name(exit_code):
    """'SIGBUS' for a process killed by signal 10, else None."""
    if exit_code is None or exit_code >= 0:
        return None
    try:
        return signal.Signals(-exit_code).name
    except (ValueError, AttributeError):
        return f"signal {-exit_code}"


UNRESOLVED_RE = re.compile(r"\[pe\] unresolved import (\S+)")


def strict_measurement(binary, pe_path, timeout=10.0):
    """Boot the same binary *without* RECOMP_PERMISSIVE.

    The permissive loader line always reads "0 unresolved" because the stub
    resolver counts a stub as a resolved import, so the honest unresolved
    count comes from a strict launch. This is a second real launch, not a
    re-reading of the first one's output.
    """
    run = run_permissive(binary, pe_path, timeout=timeout, permissive=False)
    text = run["stdout"] + "\n" + run["stderr"]
    boot = parse_boot_line(text) or {}
    bound = parse_bound_line(text) or {}
    names = UNRESOLVED_RE.findall(text)
    return {
        "exit_code": run["exit_code"],
        "timed_out": run["timed_out"],
        "launch_error": run["launch_error"],
        "unresolved_iat_imports": boot.get("unresolved_iat_imports"),
        "iat_bound": bound.get("iat_bound"),
        "iat_unresolved": bound.get("iat_unresolved"),
        "unresolved_names": sorted(set(names))[:80],
        "unresolved_name_count": len(set(names)),
        "duration_seconds": run["duration_seconds"],
    }


TRACE_RE = re.compile(r"^\[hle\]\s+(\S+)", re.MULTILINE)


def pinned_runtime(project_dir):
    """Which runtime snapshot this project carries.

    A project has its own copy of the runtime, taken when it was generated or
    last re-synced. Rows measured against different snapshots are not directly
    comparable, so each row records the one it used.
    """
    rt = Path(project_dir) / "src" / "runtime" / "win32hle"
    if not rt.is_dir():
        return None
    host = rt / "host.c"
    digest = None
    if host.exists():
        import hashlib
        digest = hashlib.sha256(host.read_bytes()).hexdigest()
    return {
        "files": len([p for p in rt.iterdir() if p.suffix in (".c", ".h")]),
        "host_c_sha256": digest,
        "has_msvcrt": (rt / "msvcrt.c").exists(),
        "has_gl32": (rt / "gl32.c").exists(),
        "has_sdlmix": (rt / "sdlmix.c").exists(),
        "has_sdl12": (rt / "sdl12.c").exists(),
    }


def trace_run(binary, pe_path, timeout=10.0, tail=12, top=12):
    """Relaunch with RECOMP_TRACE=1 and profile the imports the guest called.

    A crash inside lifted code leaves no clue in the normal log: no stub was
    called, so nothing names itself. A run that reaches the deadline looks the
    same whether it is a game loop or a spin. The dispatch profile is what
    separates those cases - a frame loop shows a message pump, a timer and a
    present, a spin shows one call repeating.
    """
    from collections import Counter
    run = run_permissive(binary, pe_path, timeout=timeout,
                         env_extra={"RECOMP_TRACE": "1"})
    text = run["stdout"] + "\n" + run["stderr"]
    names = TRACE_RE.findall(text)
    counts = Counter(names)
    return {
        "exit_code": run["exit_code"],
        "timed_out": run["timed_out"],
        "dispatched_import_count": len(names),
        "distinct_imports": len(counts),
        "top_imports": [{"import": name, "calls": calls}
                        for name, calls in counts.most_common(top)],
        "last_dispatched": names[-tail:],
        "stderr_tail": run["stderr"][-TAIL_CHARS:],
    }


# The pipeline's own vocabulary, so a row here can be compared with a row in
# reports/games/results.json. `permissive_outcome` keeps this tool's precise
# value; run_status is the comparable classification.
RUN_STATUS = {
    "timeout": "timeout",           # still running at the deadline: it looped
    "exited-clean": "exited",
    "exited-error": "exited",       # returned on its own, with a nonzero code
    "crashed": "crashed",
    "reached-unimplemented-import": "boot-failed",
    "no-binary": "not-run",
    "launch-failed": "not-run",
    "build-failed": "not-run",
}


def run_status_of(outcome):
    """The pipeline-comparable status for one measured outcome."""
    return RUN_STATUS.get(outcome, "unknown")


def load_baseline(path):
    """Per-executable unresolved counts recorded before the current work.

    Keyed by PE sha256, not by directory name, because the same game can be
    measured from differently named project directories.
    """
    if not path:
        return None
    try:
        record = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    by_sha = record.get("by_pe_sha256") or {}
    return {"source": record.get("source"), "captured_at": record.get("captured_at"),
            "by_pe_sha256": by_sha}


def compare_baseline(rows, baseline):
    """Per game: recorded unresolved count, the strict count now, the delta."""
    if not baseline:
        return []
    by_sha = baseline["by_pe_sha256"]
    out = []
    for row in rows:
        entry = by_sha.get(row.get("pe_sha256"))
        strict = row.get("strict") or {}
        now = strict.get("unresolved_iat_imports")
        before = entry.get("unresolved_import_count") if entry else None
        out.append({
            "project": row["project"],
            "pe": row.get("pe"),
            "baseline_unresolved": before,
            "baseline_recorded_at": entry.get("recorded_at") if entry else None,
            "baseline_provenance": entry.get("provenance") if entry else None,
            "now_unresolved": now,
            "delta": (now - before) if (before is not None and now is not None) else None,
            "at_zero_unresolved": now == 0,
            "outcome": row["outcome"],
        })
    return out


def project_pe_path(project_dir):
    """The original PE a project was generated from, from project.json."""
    cfg = project_dir / "project.json"
    try:
        record = json.loads(cfg.read_text())
    except (OSError, ValueError):
        return None, None
    pe = (record.get("pe") or {})
    return pe.get("path"), record.get("name")


def project_pe_info(project_dir):
    """PE path, type, sha256 and CMake target name from project.json."""
    try:
        record = json.loads((Path(project_dir) / "project.json").read_text())
    except (OSError, ValueError):
        return None
    pe = record.get("pe") or {}
    if not pe.get("path"):
        return None
    return {"path": pe["path"], "pe_type": pe.get("pe_type"),
            "sha256": pe.get("sha256"), "name": record.get("name")}


def canonical_project_dirs(results_path=None):
    """Project dir per corpus game, from the committed sweep record.

    reports/games/results.json names the project directory each recorded run
    was measured from (its `evidence` field). Several project directories can
    exist for one game - the generator's default names a project after the
    executable stem, and a run can pass --project-dir - so that record is the
    authority on which directory is the measured one. Returns {sha256: dir}.
    """
    results_path = Path(results_path or DEFAULT_RESULTS)
    try:
        record = json.loads(results_path.read_text())
    except (OSError, ValueError):
        return {}
    canonical = {}
    for run in record.get("runs") or []:
        evidence = run.get("evidence") or ""
        parts = Path(evidence).parts
        if len(parts) < 2 or parts[0] != "projects":
            continue
        sha = run.get("input_sha256")
        if sha:
            canonical[sha] = parts[1]
    return canonical


def binary_path(project_dir, name=None):
    """The built binary: build/<cmake target name> (falls back to dir name)."""
    if not name:
        _pe, name = project_pe_path(project_dir)
    if name and (project_dir / "build" / name).exists():
        return project_dir / "build" / name
    return project_dir / "build" / project_dir.name


def _is_stem_name(project_dir, pe_path):
    """True when the directory is just the sanitized executable stem."""
    stem = Path(pe_path).stem
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in stem)
    return project_dir.name == safe


def discover_projects(projects_dir, only=None, canonical=None, dedupe=True,
                      include_fixtures=False):
    """One entry per PE32 game, with duplicate project dirs collapsed.

    `only` filters by directory name and bypasses deduplication (an explicit
    request names the exact tree to measure). Otherwise duplicate directories
    for the same PE are collapsed to the one the corpus record names, falling
    back to the more descriptive directory name. Projects built from a PE that
    lives inside this repository (tests/fixtures) are not corpus games and are
    skipped unless `include_fixtures`.
    """
    projects_dir = Path(projects_dir)
    groups = {}
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        info = project_pe_info(project_dir)
        if not info or info["pe_type"] != "PE32":
            continue
        if not include_fixtures:
            try:
                Path(info["path"]).resolve().relative_to(ROOT)
                continue        # a repo-internal fixture, not a corpus game
            except ValueError:
                pass
        if only:
            if project_dir.name in only:
                groups.setdefault(("only", project_dir.name), []).append(project_dir)
            continue
        key = info["sha256"] or str(Path(info["path"]).resolve())
        groups.setdefault(key, []).append(project_dir)

    found, duplicates = [], []
    for key, dirs in groups.items():
        if len(dirs) == 1 or not dedupe:
            found.extend(dirs)
            continue
        descriptive = [d for d in dirs
                       if not _is_stem_name(d, project_pe_info(d)["path"])]
        built = [d for d in dirs if binary_path(d).exists()]

        chosen, reason = None, None
        if canonical:
            for candidate in dirs:
                info = project_pe_info(candidate)
                if info and canonical.get(info["sha256"]) == candidate.name:
                    chosen, reason = candidate, "corpus-record"
                    break
        if chosen is not None and not binary_path(chosen).exists() and built:
            # The record names a tree that is not built right now; measuring
            # the sibling that is beats reporting no result for the game.
            chosen = sorted(built)[0]
            reason = "corpus-record tree has no binary; used the built sibling"
        if chosen is None:
            if built:
                chosen, reason = sorted(built)[0], "built sibling"
            else:
                chosen = sorted(descriptive or dirs)[0]
                reason = "descriptive name (no binary in any duplicate)"
        found.append(chosen)
        duplicates.append({
            "project": chosen.name,
            "pe": project_pe_info(chosen)["path"],
            "chosen": chosen.name,
            "chosen_because": reason,
            "also_present": sorted(d.name for d in dirs if d != chosen),
        })
    found.sort(key=lambda d: d.name)
    return found, duplicates


def _decode(raw):
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")


def run_permissive(binary, pe_path, cwd=None, timeout=15.0, env_extra=None,
                   headless=True, permissive=True):
    """Launch one rebuilt binary permissively; capture its real output.

    The launch mirrors the pipeline's own run stage: the original PE is passed
    as an absolute path, the working directory is the game's asset directory,
    and the SDL drivers are the dummy ones so a diagnostic sweep cannot open a
    window on the user's desktop.

    Returns a dict of measured facts. Never raises for a bad launch: a failure
    to start is itself the result and is recorded verbatim.
    """
    pe_abs = str(Path(pe_path).resolve())
    result = {
        "binary": str(binary),
        "pe": pe_abs,
        "argv": [str(binary), pe_abs],
        "cwd": str(cwd) if cwd else None,
        "timeout_seconds": timeout,
        "exit_code": None,
        "timed_out": False,
        "duration_seconds": None,
        "stdout": "",
        "stderr": "",
        "launch_error": None,
    }
    env = dict(os.environ)
    if permissive:
        env["RECOMP_PERMISSIVE"] = "1"
    else:
        env.pop("RECOMP_PERMISSIVE", None)
    if headless:
        env["SDL_VIDEODRIVER"] = "dummy"
        env["SDL_AUDIODRIVER"] = "dummy"
    if env_extra:
        env.update(env_extra)

    # The game's own directory is the working directory: it opens its data
    # files by relative path, exactly as the pipeline's run stage does.
    run_cwd = str(cwd or Path(pe_abs).parent)
    result["cwd"] = run_cwd
    started = time.time()
    try:
        proc = subprocess.Popen(
            [str(binary), pe_abs],
            cwd=run_cwd, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            start_new_session=True)
    except OSError as exc:
        result["launch_error"] = f"{type(exc).__name__}: {exc}"
        result["duration_seconds"] = round(time.time() - started, 2)
        return result

    try:
        out, err = proc.communicate(timeout=timeout)
        result["exit_code"] = proc.returncode
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        # Kill the whole session: the guest may have spawned children.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
    result["duration_seconds"] = round(time.time() - started, 2)
    result["stdout"] = _decode(out)
    result["stderr"] = _decode(err)
    return result


def measure_project(project_dir, timeout=15.0, strict_check=True,
                    trace_crashes=True):
    """Boot one project permissively and return its measured row.

    With `strict_check`, the same binary is also launched once without
    RECOMP_PERMISSIVE, because the permissive loader line cannot report a
    meaningful unresolved-import count: its resolver stubs everything, so the
    count is always 0. The strict launch is a second real process.
    """
    project_dir = Path(project_dir)
    pe_path, name = project_pe_path(project_dir)
    row = {
        "project": project_dir.name,
        "name": name,
        "pe": pe_path,
        "pe_sha256": (project_pe_info(project_dir) or {}).get("sha256"),
        "binary": None,
        "binary_exists": False,
        "outcome": "no-binary",
        "first_import": None,
        "stub_va": None,
        "caller": None,
        "exit_code": None,
        "signal": None,
        "timed_out": False,
        "duration_seconds": None,
        "boot_line": None,
        "iat_bind": None,
        "run_status": None,
        "strict": None,
        "import_profile": None,
        "runtime": pinned_runtime(project_dir),
        "crash_trace": None,
        "raw_error": None,
        "stderr_tail": "",
    }
    if not pe_path:
        row["raw_error"] = f"{project_dir}/project.json is missing or unreadable"
        return row
    if not Path(pe_path).exists():
        row["raw_error"] = f"original PE not found at {pe_path}"
        return row

    binary = binary_path(project_dir, name)
    row["binary"] = str(binary)
    if not binary.exists():
        row["raw_error"] = f"rebuilt binary not found at {binary}"
        return row
    row["binary_exists"] = True

    strict = (strict_measurement(binary, pe_path, timeout=min(timeout, 10.0))
              if strict_check else None)
    run = run_permissive(binary, pe_path, timeout=timeout)
    row["exit_code"] = run["exit_code"]
    row["timed_out"] = run["timed_out"]
    row["duration_seconds"] = run["duration_seconds"]
    row["stderr_tail"] = run["stderr"][-TAIL_CHARS:]
    if run["launch_error"]:
        row["raw_error"] = run["launch_error"]
        row["outcome"] = "launch-failed"
        return row

    combined = run["stdout"] + "\n" + run["stderr"]
    hit = parse_first_import(combined)
    if hit:
        row["first_import"] = hit["import"]
        row["stub_va"] = hit["stub_va"]
        row["caller"] = hit["caller"]
    row["boot_line"] = parse_boot_line(combined)
    row["iat_bind"] = parse_bound_line(combined)
    row["outcome"] = classify(run["exit_code"], run["timed_out"], hit)
    row["run_status"] = run_status_of(row["outcome"])
    row["signal"] = signal_name(run["exit_code"])
    if row["outcome"] == "timeout":
        row["raw_error"] = (f"no unimplemented import within {timeout:g}s; "
                            f"process still running, killed")
    elif row["outcome"] == "crashed":
        row["raw_error"] = (f"killed by {row['signal']} after boot; no "
                            f"unimplemented import reached")
    elif row["outcome"] == "exited-error":
        last = [ln for ln in run["stderr"].strip().splitlines() if ln.strip()]
        row["raw_error"] = last[-1] if last else f"exit {run['exit_code']}"
    if strict is not None:
        row["strict"] = strict
    if row["outcome"] == "crashed" and trace_crashes:
        row["crash_trace"] = trace_run(binary, pe_path,
                                       timeout=min(timeout, 10.0))
    if row["outcome"] == "timeout" and trace_crashes:
        # A run that reaches the deadline entered something; the dispatch
        # profile says whether that something is a game loop.
        row["import_profile"] = trace_run(binary, pe_path, timeout=timeout)
    return row


def _run_step(cmd, timeout):
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
        rc = proc.returncode
        tail = (proc.stdout + proc.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return {"command": " ".join(cmd), "returncode": None,
                "seconds": round(time.time() - started, 1),
                "log_tail": f"timed out after {timeout}s"}
    return {"command": " ".join(cmd), "returncode": rc,
            "seconds": round(time.time() - started, 1), "log_tail": tail}


def build_project(project_dir, jobs=None, timeout=900, clean=False):
    """Reconfigure and build one project in its own build dir.

    An existing CMakeCache.txt pins absolute paths to library kegs, so a
    Homebrew upgrade that moves a keg (ffmpeg 9.0.2 -> 9.0.2_1 here) strands
    the cache and the build dies with either "No rule to make target
    .../libavformat.dylib" or "Imported target PkgConfig::FFMPEG includes
    non-existent path". Reconfiguring is cheap, so it always runs; if the
    configure still fails, the build dir is discarded and regenerated, which
    re-resolves every path through pkg-config.
    """
    project_dir = Path(project_dir)
    build_dir = project_dir / "build"
    configure = ["cmake", "-S", str(project_dir), "-B", str(build_dir)]
    build = ["cmake", "--build", str(build_dir)]
    if jobs:
        build += ["-j", str(jobs)]

    log = []
    if clean and build_dir.exists():
        shutil.rmtree(build_dir)
    steps = [configure, build]
    for index, cmd in enumerate(steps):
        step = _run_step(cmd, timeout)
        log.append(step)
        if step["returncode"] == 0:
            continue
        if index == 0 and build_dir.exists():
            # Stale cache: throw it away and configure from scratch.
            shutil.rmtree(build_dir)
            retry = _run_step(configure, timeout)
            retry["command"] += "   # after discarding a stale CMakeCache"
            log.append(retry)
            if retry["returncode"] != 0:
                return {"ok": False, "steps": log,
                        "command": retry["command"],
                        "returncode": retry["returncode"],
                        "seconds": retry["seconds"],
                        "log_tail": retry["log_tail"]}
            rebuilt = _run_step(build, timeout)
            log.append(rebuilt)
            if rebuilt["returncode"] != 0:
                return {"ok": False, "steps": log,
                        "command": rebuilt["command"],
                        "returncode": rebuilt["returncode"],
                        "seconds": rebuilt["seconds"],
                        "log_tail": rebuilt["log_tail"]}
            return {"ok": True, "steps": log}
        return {"ok": False, "steps": log, "command": step["command"],
                "returncode": step["returncode"], "seconds": step["seconds"],
                "log_tail": step["log_tail"]}
    return {"ok": True, "steps": log}


def write_main_c(project_dir):
    """Rewrite the generated entry point from the current MAIN_C template.

    The template lives in projectgen.py; regenerating the whole project would
    re-translate the PE, so only src/main.c is refreshed and the project is
    rebuilt in place from its own build directory.
    """
    from tools.autorecomp.projectgen import MAIN_C
    path = Path(project_dir) / "src" / "main.c"
    if not path.parent.exists():
        return False
    path.write_text(MAIN_C)
    return True


def sync_runtime(project_dir, runtime_root):
    """Replace a project's pinned runtime with a copy from `runtime_root`.

    A project carries its own copy of the runtime, so rows measured against
    different copies are not comparable. Copying from one directory - a git
    checkout of a named commit - makes every row provably the same runtime.
    Selftest sources are skipped, as projectgen's own copy step does.
    """
    import hashlib
    runtime_root = Path(runtime_root)
    win_src = runtime_root / "win32hle"
    rec_src = runtime_root / "recomp32"
    win_dst = Path(project_dir) / "src" / "runtime" / "win32hle"
    rec_dst = Path(project_dir) / "src" / "runtime" / "recomp32"
    if not win_src.is_dir() or not win_dst.is_dir():
        return None
    copied = 0
    for path in sorted(win_src.iterdir()):
        if path.suffix not in (".c", ".h") or path.name.endswith("_selftest.c"):
            continue
        shutil.copy2(path, win_dst / path.name)
        copied += 1
    for path in sorted(rec_src.iterdir()):
        if path.suffix in (".c", ".h"):
            shutil.copy2(path, rec_dst / path.name)
            copied += 1
    host = win_dst / "host.c"
    return {
        "source": str(runtime_root),
        "files_copied": copied,
        "host_c_sha256": (hashlib.sha256(host.read_bytes()).hexdigest()
                          if host.exists() else None),
    }


def rank_imports(rows):
    """Imports ranked by how many games each one blocks, then by name."""
    counts = {}
    games = {}
    for row in rows:
        name = row.get("first_import")
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
        games.setdefault(name, []).append(row["project"])
    ranked = [{"import": name, "games_blocked": count,
               "games": sorted(games[name])}
              for name, count in counts.items()]
    ranked.sort(key=lambda r: (-r["games_blocked"], r["import"]))
    return ranked


def build_report(rows, generated_at, command, timeout, duplicates=None,
                 runtime_source=None, baseline=None, commit=None):
    """The JSON document: measured rows plus the ranked blocker table."""
    return {
        "schema": 1,
        "generated_at": generated_at,
        "command": command,
        "timeout_seconds": timeout,
        "method": ("each rebuilt binary is launched as "
                   "`<binary> <absolute PE path>` from the game's own "
                   "directory, with RECOMP_PERMISSIVE=1 and the dummy SDL "
                   "video/audio drivers (the pipeline's run-stage settings); "
                   "the runtime binds unresolved imports to a self-naming "
                   "stub that aborts on first call, so first_import is what "
                   "the running guest actually reached. The permissive "
                   "loader line always reads 0 unresolved because a stub "
                   "counts as resolved, so each binary is launched a second "
                   "time without RECOMP_PERMISSIVE and that strict run's "
                   "unresolved count is recorded under `strict`. run_status "
                   "is the pipeline-comparable classification of the "
                   "permissive run (timeout = still running at the deadline, "
                   "exited = returned on its own, crashed = killed by a "
                   "signal, boot-failed = aborted at an unimplemented "
                   "import); permissive_outcome keeps this tool's precise "
                   "value"),
        "projects_measured": len(rows),
        "projects_reaching_import": sum(1 for r in rows if r.get("first_import")),
        "outcomes": {o: sum(1 for r in rows if r["outcome"] == o)
                     for o in sorted({r["outcome"] for r in rows})},
        "run_statuses": {st: sum(1 for r in rows if r.get("run_status") == st)
                         for st in sorted({r.get("run_status") for r in rows})},
        "commit": commit,
        "runtime_source": runtime_source,
        "baseline": baseline,
        "before_after": compare_baseline(rows, baseline),
        "duplicate_project_dirs": duplicates or [],
        "ranked_first_imports": rank_imports(rows),
        "runs": rows,
    }


def render_markdown(report):
    rows = report["runs"]
    out = []
    out.append("# Permissive-boot diagnostic sweep")
    out.append("")
    out.append(f"Generated {report['generated_at']} by "
               f"`{report['command']}` (timeout "
               f"{report['timeout_seconds']:g}s per game).")
    if report.get("commit"):
        out.append("")
        out.append(f"Measured against the runtime at commit "
                   f"`{report['commit']}`.")
    out.append("")
    out.append("Each rebuilt binary is started with `RECOMP_PERMISSIVE=1`, "
               "which binds unresolved imports to the runtime's self-naming "
               "stub. The guest runs until it calls an unimplemented import; "
               "the runtime then prints and aborts:")
    out.append("")
    out.append("```")
    out.append("[win32hle] FATAL: unimplemented import <NAME> (0x<STUB>) "
               "called from guest 0x<CALLER>")
    out.append("```")
    out.append("")
    out.append("The name in that line is the first import the running program "
               "reached - measured from a real launch, not inferred. Strict "
               "binding remains the default when the variable is unset.")
    out.append("")
    out.append(f"Measured: {report['projects_measured']} projects, "
               f"{report['projects_reaching_import']} reached an unimplemented "
               f"import. Outcomes: "
               + ", ".join(f"{k} {v}" for k, v in report["outcomes"].items())
               + ".")
    out.append("")

    out.append("## Ranked first-reached imports")
    out.append("")
    ranked = report["ranked_first_imports"]
    if not ranked:
        out.append("No project reached an unimplemented import.")
    else:
        out.append("| # | import | games blocked | games |")
        out.append("|---|--------|---------------|-------|")
        for i, entry in enumerate(ranked, 1):
            out.append(f"| {i} | `{entry['import']}` | "
                       f"{entry['games_blocked']} | "
                       f"{', '.join(entry['games'])} |")
    out.append("")

    out.append("## Per-game rows")
    out.append("")
    out.append("`strict` is a second launch of the same binary without "
               "`RECOMP_PERMISSIVE`; it is the only launch that reports a "
               "meaningful unresolved-import count.")
    out.append("")
    out.append("| project | run status | outcome | exit | first import | stub VA "
               "| caller | strict exit | strict unresolved | pinned runtime |")
    out.append("|---------|------------|---------|------|--------------|--------"
               "|--------|-------------|-------------------|----------------|")
    for row in rows:
        exit_code = row["exit_code"]
        if row["timed_out"]:
            exit_code = f"{exit_code} (timeout)"
        strict = row.get("strict") or {}
        strict_exit = strict.get("exit_code")
        if strict.get("timed_out"):
            strict_exit = f"{strict_exit} (timeout)"
        strict_unresolved = strict.get("unresolved_iat_imports")
        out.append(f"| {row['project']} | {row.get('run_status') or '-'} | "
                   f"{row['outcome']} | "
                   f"{exit_code} | "
                   f"{('`' + row['first_import'] + '`') if row['first_import'] else '-'} | "
                   f"{row['stub_va'] or '-'} | {row['caller'] or '-'} | "
                   f"{strict_exit if strict_exit is not None else '-'} | "
                   f"{strict_unresolved if strict_unresolved is not None else '-'} | "
                   f"{(row.get('runtime') or {}).get('host_c_sha256', '?')[:8]} |")
    out.append("")

    unmeasured = [r for r in rows
                  if r["outcome"] in ("no-binary", "launch-failed")]
    if unmeasured:
        out.append("## Projects that could not be launched")
        out.append("")
        for row in unmeasured:
            out.append(f"- **{row['project']}**: {row['raw_error']}")
        out.append("")

    unresolved_without_import = [r for r in rows
                                 if not r["first_import"]
                                 and r["outcome"] not in ("no-binary", "launch-failed")]
    if unresolved_without_import:
        out.append("## Ran without reaching an unimplemented import")
        out.append("")
        for row in unresolved_without_import:
            out.append(f"- **{row['project']}**: {row['outcome']}"
                       + (f" - {row['raw_error']}" if row["raw_error"] else ""))
        out.append("")

    before_after = report.get("before_after") or []
    if before_after:
        baseline = report.get("baseline") or {}
        out.append("## Before / after")
        out.append("")
        out.append(f"Baseline: {baseline.get('source') or 'unspecified'}"
                   + (f" (captured {baseline['captured_at']})"
                      if baseline.get("captured_at") else "") + ".")
        out.append("")
        out.append("| project | baseline unresolved | now (strict) | delta "
                   "| 0 unresolved | permissive outcome |")
        out.append("|---------|--------------------:|-------------:|------:"
                   "|:------------:|--------------------|")
        for entry in before_after:
            delta = entry["delta"]
            delta_text = "-" if delta is None else (f"{delta:+d}" if delta else "0")
            out.append(f"| {entry['project']} | "
                       f"{entry['baseline_unresolved'] if entry['baseline_unresolved'] is not None else '-'} | "
                       f"{entry['now_unresolved'] if entry['now_unresolved'] is not None else '-'} | "
                       f"{delta_text} | "
                       f"{'yes' if entry.get('at_zero_unresolved') else ''} | "
                       f"{entry['outcome']} |")
        out.append("")

        # The rows closest to actually running are the ones with nothing left
        # unresolved; list them by count so the next slice can be picked.
        ranked_rows = [e for e in before_after if e["now_unresolved"] is not None]
        ranked_rows.sort(key=lambda e: e["now_unresolved"])
        out.append("### Closest to a real boot (by unresolved count now)")
        out.append("")
        for entry in ranked_rows:
            mark = " **0 unresolved**" if entry.get("at_zero_unresolved") else ""
            out.append(f"- `{entry['project']}`: {entry['now_unresolved']} "
                       f"unresolved, permissive outcome {entry['outcome']}{mark}")
        out.append("")
        noted = [e for e in before_after if e.get("baseline_provenance")]
        if noted:
            out.append("Baseline provenance notes:")
            out.append("")
            for entry in noted:
                out.append(f"- **{entry['project']}**: {entry['baseline_provenance']}")
            out.append("")

    looping = [r for r in rows if r.get("import_profile")]
    if looping:
        out.append("## Games that reached the deadline (entered something)")
        out.append("")
        out.append("`timeout` means the process was still running at the "
                   "deadline. The dispatch profile from a `RECOMP_TRACE=1` "
                   "relaunch is what separates a game loop from a spin: a loop "
                   "shows a message pump, a timer and a present, a spin shows "
                   "one call repeating.")
        out.append("")
        for row in looping:
            profile = row["import_profile"]
            top = ", ".join(f"`{e['import']}` x{e['calls']}"
                            for e in profile["top_imports"][:8])
            out.append(f"- **{row['project']}**: "
                       f"{profile['dispatched_import_count']} dispatches, "
                       f"{profile['distinct_imports']} distinct - {top}")
        out.append("")

    traced = [r for r in rows if r.get("crash_trace")]
    if traced:
        out.append("## Crashed games: last imports the guest reached")
        out.append("")
        out.append("A crash in lifted code names no import, so each crashed "
                   "game was relaunched with `RECOMP_TRACE=1`. The last "
                   "dispatch before the fault is the prime suspect.")
        out.append("")
        for row in traced:
            trace = row["crash_trace"]
            last = trace["last_dispatched"]
            out.append(f"- **{row['project']}** ({row['signal']}, "
                       f"{trace['dispatched_import_count']} imports dispatched): "
                       + (" -> ".join(f"`{n}`" for n in last) if last
                          else "no import was dispatched at all"))
        out.append("")

    out.append("Raw stderr for every row is in "
               "`reports/games/diagnostic-sweep.json` (`stderr_tail`).")
    out.append("")

    duplicates = report.get("duplicate_project_dirs") or []
    if duplicates:
        out.append("## Duplicate project directories collapsed")
        out.append("")
        out.append("Several project directories exist for one executable "
                   "(the generator's default names a project after the "
                   "executable stem). One row per game is reported; which "
                   "directory was measured, and why, is recorded per game:")
        out.append("")
        for dup in duplicates:
            out.append(f"- measured `{dup['chosen']}` "
                       f"({dup.get('chosen_because', 'unspecified')}); "
                       f"also present: "
                       f"{', '.join('`' + d + '`' for d in dup['also_present'])}")
        out.append("")
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="diagnose",
        description="Boot each rebuilt PE32 project permissively and record "
                    "the first unimplemented import the guest actually reached.")
    parser.add_argument("--projects-dir", default=str(DEFAULT_PROJECTS_DIR),
                        help="directory holding generated projects")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="seconds to let each game run (default: 15)")
    parser.add_argument("--json", default=str(DEFAULT_JSON),
                        help="JSON output path")
    parser.add_argument("--markdown", default=str(DEFAULT_MD),
                        help="markdown summary output path")
    parser.add_argument("--only", action="append", default=None,
                        help="restrict to this project (repeatable)")
    parser.add_argument("--build", action="store_true",
                        help="refresh src/main.c from the generator template "
                             "and rebuild each project before measuring")
    parser.add_argument("--jobs", type=int, default=None,
                        help="parallel compile jobs for --build")
    parser.add_argument("--commit", default=None,
                        help="the commit the frozen runtime came from; recorded "
                             "in the report so every row names its provenance")
    parser.add_argument("--baseline", default=None,
                        help="JSON snapshot of per-executable unresolved counts "
                             "recorded before this work (keyed by PE sha256); "
                             "adds a before/after section")
    parser.add_argument("--runtime-source", default=None,
                        help="with --build: replace each project's pinned "
                             "runtime with a copy from this directory (a "
                             "checkout of one commit), so every row is "
                             "measured against the same runtime")
    parser.add_argument("--build-timeout", type=float, default=900.0,
                        help="seconds allowed per project build")
    parser.add_argument("--quiet", action="store_true",
                        help="only print the ranked table")
    parser.add_argument("--no-dedupe", action="store_true",
                        help="measure every PE32 project directory, including "
                             "duplicate directories for the same executable")
    parser.add_argument("--include-fixtures", action="store_true",
                        help="also measure projects built from a PE inside "
                             "this repository (tests/fixtures)")
    parser.add_argument("--no-crash-trace", action="store_true",
                        help="skip the RECOMP_TRACE=1 relaunch of a crashed "
                             "game (the one that lists the last shims reached)")
    parser.add_argument("--no-strict-check", action="store_true",
                        help="skip the second, strict launch of each binary "
                             "(the one that yields the real unresolved count)")
    args = parser.parse_args(argv)

    canonical = canonical_project_dirs()
    projects, duplicates = discover_projects(
        args.projects_dir, only=args.only, canonical=canonical,
        dedupe=not args.no_dedupe, include_fixtures=args.include_fixtures)
    if not projects:
        print(f"no PE32 projects under {args.projects_dir}", file=sys.stderr)
        return 1
    for dup in duplicates:
        print(f"[duplicate] {dup['pe']}: measuring {dup['chosen']}, "
              f"also present {', '.join(dup['also_present'])}", file=sys.stderr)

    rows = []
    for project_dir in projects:
        if args.build:
            if args.runtime_source:
                synced = sync_runtime(project_dir, args.runtime_source)
                if synced is None:
                    print(f"[skip] {project_dir.name}: cannot sync runtime",
                          file=sys.stderr)
                    continue
            if not write_main_c(project_dir):
                print(f"[skip] {project_dir.name}: no src/ tree", file=sys.stderr)
                continue
            build = build_project(project_dir, jobs=args.jobs,
                                  timeout=args.build_timeout)
            if not build["ok"]:
                print(f"[build-failed] {project_dir.name}: "
                      f"{build['log_tail'][-300:]}", file=sys.stderr)
                rows.append({
                    "project": project_dir.name, "name": None, "pe": None,
                    "binary": None, "binary_exists": False,
                    "outcome": "build-failed", "first_import": None,
                    "stub_va": None, "caller": None, "exit_code": None,
                    "timed_out": False, "duration_seconds": None,
                    "boot_line": None, "iat_bind": None,
                    "raw_error": (f"build failed: {build.get('command')} "
                                  f"rc={build.get('returncode')}"),
                    "stderr_tail": build["log_tail"],
                })
                continue
        row = measure_project(project_dir, timeout=args.timeout,
                              strict_check=not args.no_strict_check,
                              trace_crashes=not args.no_crash_trace)
        rows.append(row)
        if not args.quiet:
            print(f"[{row['outcome']}] {row['project']}: "
                  f"{row['first_import'] or row['raw_error'] or 'no import reached'}"
                  f" (exit {row['exit_code']}, {row['duration_seconds']}s)")

    import datetime
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    command = "python3 tools/autorecomp/diagnose.py --timeout " \
              f"{args.timeout:g}" + (" --build" if args.build else "") \
              + (f" --commit {args.commit}" if args.commit else "")
    runtime_source = None
    if args.runtime_source:
        import hashlib
        host = Path(args.runtime_source) / "win32hle" / "host.c"
        runtime_source = {
            "path": str(args.runtime_source),
            "host_c_sha256": (hashlib.sha256(host.read_bytes()).hexdigest()
                              if host.exists() else None),
        }
    report = build_report(rows, generated_at, command, args.timeout,
                          duplicates=duplicates, runtime_source=runtime_source,
                          baseline=load_baseline(args.baseline),
                          commit=args.commit)

    json_path = Path(args.json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2) + "\n")
    md_path = Path(args.markdown)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report))

    ranked = report["ranked_first_imports"]
    if ranked:
        print("\nranked first-reached imports (games blocked):")
        for entry in ranked:
            print(f"  {entry['games_blocked']:>2}  {entry['import']}")
    else:
        print("\nno project reached an unimplemented import")
    print(f"\nwrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
