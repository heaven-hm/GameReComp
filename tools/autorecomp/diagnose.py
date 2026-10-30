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


def project_pe_path(project_dir):
    """The original PE a project was generated from, from project.json."""
    cfg = project_dir / "project.json"
    try:
        record = json.loads(cfg.read_text())
    except (OSError, ValueError):
        return None, None
    pe = (record.get("pe") or {})
    return pe.get("path"), record.get("name")


def binary_path(project_dir, name=None):
    """The built binary: build/<cmake target name> (falls back to dir name)."""
    if not name:
        _pe, name = project_pe_path(project_dir)
    if name and (project_dir / "build" / name).exists():
        return project_dir / "build" / name
    return project_dir / "build" / project_dir.name


def discover_projects(projects_dir, only=None):
    """Every PE32 project directory that has a project.json, sorted by name."""
    found = []
    for project_dir in sorted(Path(projects_dir).iterdir()):
        if not project_dir.is_dir():
            continue
        if only and project_dir.name not in only:
            continue
        pe_path, name = project_pe_path(project_dir)
        if not pe_path:
            continue
        if (project_dir / "project.json").read_text().find('"PE32"') < 0:
            continue
        found.append(project_dir)
    return found


def _decode(raw):
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")


def run_permissive(binary, pe_path, cwd=None, timeout=15.0, env_extra=None):
    """Launch one rebuilt binary permissively; capture its real output.

    Returns a dict of measured facts. Never raises for a bad launch: a failure
    to start is itself the result and is recorded verbatim.
    """
    result = {
        "binary": str(binary),
        "pe": str(pe_path),
        "argv": [str(binary), str(Path(pe_path).name)],
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
    env["RECOMP_PERMISSIVE"] = "1"
    if env_extra:
        env.update(env_extra)

    # The game's own directory is the working directory: it opens its data
    # files by relative path, exactly as the pipeline's run stage does.
    run_cwd = str(cwd or Path(pe_path).parent)
    result["cwd"] = run_cwd
    started = time.time()
    try:
        proc = subprocess.Popen(
            [str(binary), Path(pe_path).name],
            cwd=run_cwd, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
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


def measure_project(project_dir, timeout=15.0):
    """Boot one project permissively and return its measured row."""
    project_dir = Path(project_dir)
    pe_path, name = project_pe_path(project_dir)
    row = {
        "project": project_dir.name,
        "name": name,
        "pe": pe_path,
        "binary": None,
        "binary_exists": False,
        "outcome": "no-binary",
        "first_import": None,
        "stub_va": None,
        "caller": None,
        "exit_code": None,
        "timed_out": False,
        "duration_seconds": None,
        "boot_line": None,
        "iat_bind": None,
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
    if row["outcome"] == "timeout":
        row["raw_error"] = (f"no unimplemented import within {timeout:g}s; "
                            f"process still running, killed")
    elif row["outcome"] in ("crashed", "exited-error"):
        last = [ln for ln in run["stderr"].strip().splitlines() if ln.strip()]
        row["raw_error"] = last[-1] if last else f"exit {run['exit_code']}"
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


def build_report(rows, generated_at, command, timeout):
    """The JSON document: measured rows plus the ranked blocker table."""
    return {
        "schema": 1,
        "generated_at": generated_at,
        "command": command,
        "timeout_seconds": timeout,
        "method": ("each rebuilt binary is launched with RECOMP_PERMISSIVE=1 "
                   "from the game's own directory; the runtime binds "
                   "unresolved imports to a self-naming stub that aborts on "
                   "first call, so first_import is what the running guest "
                   "actually reached"),
        "projects_measured": len(rows),
        "projects_reaching_import": sum(1 for r in rows if r.get("first_import")),
        "outcomes": {o: sum(1 for r in rows if r["outcome"] == o)
                     for o in sorted({r["outcome"] for r in rows})},
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
    out.append("| project | outcome | exit | first import | stub VA | caller |")
    out.append("|---------|---------|------|--------------|---------|--------|")
    for row in rows:
        exit_code = row["exit_code"]
        if row["timed_out"]:
            exit_code = f"{exit_code} (timeout)"
        out.append(f"| {row['project']} | {row['outcome']} | "
                   f"{exit_code} | "
                   f"{('`' + row['first_import'] + '`') if row['first_import'] else '-'} | "
                   f"{row['stub_va'] or '-'} | {row['caller'] or '-'} |")
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

    out.append("Raw stderr for every row is in "
               "`reports/games/diagnostic-sweep.json` (`stderr_tail`).")
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
    parser.add_argument("--build-timeout", type=float, default=900.0,
                        help="seconds allowed per project build")
    parser.add_argument("--quiet", action="store_true",
                        help="only print the ranked table")
    args = parser.parse_args(argv)

    projects = discover_projects(args.projects_dir, only=args.only)
    if not projects:
        print(f"no PE32 projects under {args.projects_dir}", file=sys.stderr)
        return 1

    rows = []
    for project_dir in projects:
        if args.build:
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
        row = measure_project(project_dir, timeout=args.timeout)
        rows.append(row)
        if not args.quiet:
            print(f"[{row['outcome']}] {row['project']}: "
                  f"{row['first_import'] or row['raw_error'] or 'no import reached'}"
                  f" (exit {row['exit_code']}, {row['duration_seconds']}s)")

    import datetime
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    command = "python3 tools/autorecomp/diagnose.py --timeout " \
              f"{args.timeout:g}" + (" --build" if args.build else "")
    report = build_report(rows, generated_at, command, args.timeout)

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
