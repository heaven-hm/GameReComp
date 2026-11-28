"""Games database: renders table.md from corpus metadata, legacy baseline,
upstream PCRecomp claims and measured autorecomp runs.

PASS /100 is the GameReComp pipeline score: weighted work, not a verdict.
inspect 10, recover 10, translate 10, project 10, compile 20, report 10,
and the outcome-dependent half: run 15 (full when the rebuilt process
launched on its own, half when it launched and then faulted, none when it
never reached its entry point) and verify 15 (full for a verified scenario,
8 for a launch without an oracle). A stage only counts when it succeeded
for the recorded input hash, so a binary that never boots scores 70, not 85.
The verification status is the honest outcome; the score can be higher than
it. Rows marked with a dagger are upstream project claims, not measurements.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = REPO_ROOT / "reports" / "corpus" / "small-games-inventory.json"
RESULTS_PATH = REPO_ROOT / "reports" / "games" / "results.json"
TABLE_PATH = REPO_ROOT / "table.md"

TITLES = {
    "cannonball": "Cannonball",
    "cavestory": "Cave Story",
    "generally": "GeneRally",
    "gunroar": "Gunroar",
    "herocore": "Hero Core",
    "ikachan": "Ikachan",
    "noiz2sa": "Noiz2sa",
    "opentyrian": "OpenTyrian",
    "parsec47": "Parsec47",
    "pixeltetris": "Pixeltetris",
    "rrootage": "rRootage",
    "snake": "Snake",
    "torus_trooper": "Torus Trooper",
    "tumiki_fighters": "Tumiki Fighters",
    "warning_forever": "Warning Forever",
    "gconv": "Resource_Convertor",
    "pq": "Progress Quest",
    "putty-w32": "PuTTY",
    "psftp-w32": "PSFTP",
    "pageant-w32": "Pageant",
    "busybox-w32": "BusyBox",
}

# Outcomes recorded by the legacy engine on 2026-10-29. They are context
# for the same binaries, not a score for this pipeline.
LEGACY_OUTCOMES = {
    "cavestory/Doukutsu.exe":
        "legacy engine: lift stopped at iretd 0x41000d; follow-up lifted "
        "27,341/27,341 and built, 35 imports missing",
    "generally/GeneRally.exe":
        "legacy engine: lift stopped at cvtpd2ps 0x406a89",
    "warning_forever/wf.exe":
        "legacy engine: lift stopped at cmpltpd 0x434830",
    "herocore/herocore gm7.exe":
        "legacy engine: lift stopped at arpl 0x40113a",
    "ikachan/dxIka.exe":
        "legacy engine: built, exited 3 at kernel32.dll!OpenMutexA; "
        "PCRecomp experiment reached the title screen",
    "noiz2sa/noiz2sa.exe":
        "legacy engine: lift stopped at arpl; follow-up lifted "
        "384,200/384,200 and built, 60 imports missing",
    "rrootage/rr.exe":
        "legacy engine: lift stopped at arpl; follow-up 354,584/354,601 "
        "(17 movd unsupported)",
    "torus_trooper/tt.exe":
        "legacy engine: lift stopped at jcxz; follow-up lifted "
        "126,497/126,497, 102 imports missing",
    "parsec47/p47.exe":
        "legacy engine: lift stopped at jcxz 0x4246df",
    "gunroar/gr.exe":
        "legacy engine: lift stopped at jcxz 0x42fceb",
    "tumiki_fighters/tf.exe":
        "legacy engine: lift stopped at jcxz 0x41c69b",
    "cannonball/cannonball.exe":
        "legacy engine: lift stopped at cvttpd2dq 0x14002fe9c",
    "opentyrian/opentyrian.exe":
        "legacy engine: lift stopped at cmpnless 0x140008e44",
    "pixeltetris/Pixeltetris.exe":
        "legacy engine: lift stopped at movntps 0x14005cc00",
    "snake/snake-windows-x86_64.exe":
        "legacy engine: built, exited 3 at _get_initial_narrow_environment",
}

# One row per upstream PCRecomp project. `claim` quotes the status its own
# README gives; `score` is a rubric mapping of that claim, never a result
# measured by this repository.
UPSTREAM = [
    ("wholeearth", "The Electronic Whole Earth Catalog", "1988", "68k", "Classic Mac OS",
     "Format project - no PC binary; classic-HFS lister + macrecomp", 0, "not recorded"),
    ("civ", "Civilization", "1991", "x86-16", "DOS",
     "Runs! 672 functions, interactive boot/menu, 164K lines", 70, "not recorded"),
    ("operationneptune", "Operation Neptune", "1991/1998", "x86 (PE32)", "Windows",
     "Plays! CRT -> WinMain -> opening -> in the submarine", 80, "not recorded"),
    ("skifree", "SkiFree", "1991", "x86 (Win16/Win32)", "Windows",
     "Playable rebuild from decompiled C, cross-platform + extras", 80, "ski32.exe"),
    ("missileattack", "Missile Attack!", "1992", "x86-16 (NE)", "Windows 3.x",
     "P0 fixture: 87 KB NE, one code segment, 21 KB of code", 10, "not recorded"),
    ("tim", "The Even More Incredible Machine", "1993", "x86-16 (NE)", "Windows 3.x",
     "P0 - 27 code segments, imports only KERNEL/USER/GDI", 10, "not recorded"),
    ("bolo", "Bolo Adventures III", "1993", "x86-16", "DOS",
     "Boots & runs! 932 functions, 70K lines, EGA/DOS on SDL2", 70, "not recorded"),
    ("coaster", "Roller Coaster Construction Set", "1993", "x86-16", "DOS",
     "Boots - 886 functions lifted, runs MSC startup into main()", 50, "not recorded"),
    ("dinopark", "DinoPark Tycoon", "1993", "x86-16", "DOS",
     "Boots! ~90K lines; renders .PIC screens + .ACT dinosaurs", 55, "not recorded"),
    ("elfish", "El-Fish", "1993", "x86-16 (NE)", "Windows 3.x",
     "Lifted & links - 2,236 functions, 121 segments", 40, "not recorded"),
    ("msbus", "The Magic School Bus Explores the Human Body", "1994", "x86-16 (NE)", "Windows 3.x",
     "P0 - ~60 KB code driving 190 MB of content", 10, "not recorded"),
    ("worldempire", "World Empire", "1994", "x86-16 (VB p-code)", "Windows 3.x",
     "Runs! 99/99 segments, 14,826 functions, 226K lines", 70, "not recorded"),
    ("tv", "Terminal Velocity", "1995", "x86-32 (DOS/4GW LE)", "DOS",
     "P0 - needs the LE/LX front end the toolbox lacks", 10, "not recorded"),
    ("bob", "Microsoft Bob", "1995", "x86-16 (NE)", "Windows 95",
     "Bring-up - modules lift link-clean, InitInstance runs", 25, "not recorded"),
    ("hellbender", "Hellbender", "1996", "x86 (PE32)", "Windows",
     "Bring-up - 5,262 functions, 0 errors, 507 import bridges", 25, "not recorded"),
    ("fury3", "Fury³", "1995", "x86 (PE32)", "Windows",
     "Playable! Flies the canyon - 1,945 functions, SDL2+imgui", 85, "not recorded"),
    ("mtm", "Monster Truck Madness 1 + 2", "1996/1998", "x86 (PE32)", "Windows",
     "P0 - renderer split into named DLLs", 10, "not recorded"),
    ("catz", "Catz", "1996", "x86-16 (NE)", "Windows 3.x/9x",
     "Runs! Win32 window, original frame loop, toys and saving work", 70, "not recorded"),
    ("encarta", "Encarta 97 Encyclopedia", "1996", "x86 (PE32)", "Windows",
     "Runs! Whole app lifted (7,326 fns); hybrid MFC boundary", 75, "not recorded"),
    ("simcity2000", "SimCity 2000 (CD / Win95)", "1996", "x86 (PE32)", "Windows",
     "Plays (Windows); win32hle bring-up runs MFC startup into main window", 80, "not recorded"),
    ("gta", "Grand Theft Auto", "1997", "x86", "DOS",
     "Builds & runs - 4,094 functions, 444K lines, runtime bringup", 55, "not recorded"),
    ("pod", "POD Gold", "1997", "x86 (PE32)", "Windows",
     "Compiles & links - 3,405 functions, 0 lift errors, 422K lines", 40, "not recorded"),
    ("ejay", "Dance eJay 1 + 2", "1997", "x86 (Win16/32)", "Windows",
     "Plays and draws! intro decode, 22,050 Hz audio, cursor animation", 80, "not recorded"),
    ("xvt", "X-Wing vs TIE Fighter + Balance of Power", "1997", "x86 (PE32)", "Windows",
     "Boots to a window - 1,790 functions; DirectDraw next", 50, "not recorded"),
    ("fallout1-re", "Fallout", "1997", "x86 (PE32)", "Windows",
     "Fork - native + HTML5 web port, multiplayer", 65, "not recorded"),
    ("fallout2-re", "Fallout 2", "1998", "x86 (PE32)", "Windows",
     "Fork - decompilation ~complete (alexbatalov upstream)", 60, "not recorded"),
    ("trespasser", "Jurassic Park: Trespasser", "1998", "x86 (PE32)", "Windows",
     "P0 - reconnaissance; ships a linker map", 10, "not recorded"),
    ("nocturne", "Nocturne", "1999", "x86 (PE32)", "Windows",
     "Phase 7 - 6,027 functions, 0 errors; real window, 42 MB image mapped", 45, "not recorded"),
    ("tiberiansun-recomp", "C&C: Tiberian Sun + Firestorm", "1999", "x86 (PE32)", "Windows",
     "Runs natively on Linux (win32hle): movies, music, menus, skirmish", 85, "not recorded"),
    ("mechwarrior3-recomp", "MechWarrior 3", "1999", "x86 (PE32)", "Windows",
     "Compiles - 2,805 functions, 0 lift errors, 158K lines", 40, "not recorded"),
    ("recoil-recomp", "Recoil", "1999", "x86 (PE32)", "Windows",
     "Compiles - 3,490 functions, 0 lift errors, 321K lines", 40, "not recorded"),
    ("xwa", "X-Wing Alliance", "1999", "x86 (PE32)", "Windows",
     "Active - D3D11 port, concourse UI runs, 2,702 functions", 60, "not recorded"),
    ("sof", "Soldier of Fortune", "2000", "x86 (PE32)", "Windows",
     "Active - SDL2 port, 8 subsystems, full maps render", 60, "not recorded"),
    ("gunman", "Gunman Chronicles", "2000", "x86 (PE32)", "Windows",
     "Phase 2 - 3,990 functions, weapons/entities rebuilt", 30, "not recorded"),
    ("redalert2-recomp", "C&C: Red Alert 2 + Yuri's Revenge", "2000", "x86 (PE32)", "Windows",
     "Runs natively on Linux (win32hle), Bink movies, D3D11 presenter", 85, "not recorded"),
    ("heavymetal", "Heavy Metal: FAKK2", "2000", "x86 (PE32)", "Windows",
     "Foundation - 57 source files, core systems scaffolded", 15, "not recorded"),
    ("crimsonskies", "Crimson Skies", "2000", "x86 (PE32)", "Windows",
     "Compiles & links - 6,232 functions, 826K lines", 40, "not recorded"),
    ("forcecommander", "Star Wars: Force Commander", "2000", "x86 (PE32)", "Windows",
     "In game! briefing room renders in 3D - 39,038 functions, 10.7M lines", 90, "not recorded"),
    ("bw", "Black & White", "2001", "x86 (PE32)", "Windows",
     "Active - all 569 types done, 10 Hz game loop runs", 55, "not recorded"),
    ("omfbg", "One Must Fall: Battlegrounds", "2003", "x86 (PE32 DLLs)", "Windows",
     "P0 - 10,374 mangled C++ exports; SafeDisc covers 1%", 10, "not recorded"),
    ("bw2", "Black & White 2", "2005", "x86 (PE32)", "Windows",
     "P0 partial - white.exe (21.7 MB) not extracted", 5, "white.exe"),
    ("rol", "Rise of Nations: Rise of Legends", "2006", "x86 (PE32)", "Windows",
     "Phase 3 - 13.25 MB, 25,513 vtable-only functions, no RTTI", 20, "not recorded"),
]


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def load_results(results_path=RESULTS_PATH):
    data = _load_json(results_path, {})
    runs = data.get("runs", [])
    return runs if isinstance(runs, list) else []


def _relative_to_repo(path):
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except (ValueError, OSError):
        return str(path)


def record_result(project_dir, relative_path=None, results_path=RESULTS_PATH):
    """Record the completed pipeline report of project_dir as one DB row."""
    project_dir = Path(project_dir).resolve()
    report = _load_json(project_dir / "reports" / "report.json", None)
    inspect = _load_json(project_dir / "reports" / "inspect.json", {})
    if not report or "outcome" not in report:
        raise ValueError(f"no completed pipeline report in {project_dir}")
    if not inspect:
        # A failed inspect never writes inspect.json; keep its findings anyway.
        inspect = next((s.get("data") or {} for s in report.get("stages", [])
                        if s.get("name") == "inspect"), {})

    pe_path = Path(report["input"])
    folder = pe_path.parent.name
    if folder.lower() in ("win32", "win64", "tools", "games", "apps", "recomp-apps"):
        game_name = TITLES.get(pe_path.stem.lower(), pe_path.stem)
    else:
        game_name = TITLES.get(folder, folder)
    outcome = report.get("outcome") or {}
    run = outcome.get("run") or {}
    host = (outcome.get("target") or {}).get("label") or ""
    arch = f"{inspect.get('machine', '?')} ({inspect.get('pe_type', '?')})"
    entry = {
        "relative_path": str(relative_path or pe_path.name),
        "game": game_name,
        "filename": pe_path.name,
        "arch": arch,
        "os": "Windows",
        "score": int(report.get("score") or 0),
        "verification": outcome.get("status", "not-run"),
        "status": outcome.get("reason", ""),
        "run_status": run.get("status"),
        "exit_code": run.get("exit_code"),
        "host": host,
        "input_sha256": inspect.get("sha256"),
        "evidence": _relative_to_repo(project_dir / "reports" / "report.html"),
        # The project tree the row was measured from, so the matrix can read
        # the port's own numbers back out of its report.json.
        "project_dir": _relative_to_repo(project_dir),
        "port_details": _port_details({"project_dir": _relative_to_repo(project_dir)}),
        "generated_at": report.get("generated_at", _now()),
    }

    runs = load_results(results_path)
    key = (entry["input_sha256"] or "", entry["filename"])
    runs = [r for r in runs
            if (r.get("input_sha256") or "", r.get("filename")) != key]
    runs.append(entry)
    runs.sort(key=lambda r: r.get("generated_at", ""), reverse=True)
    results_path = Path(results_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps({"schema": 1, "runs": runs}, indent=2) + "\n")
    return entry


def _esc(text):
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _corpus_rows(inventory):
    rows = []
    for entry in inventory.get("entries", []):
        rel = entry.get("relative_path", "?")
        folder = rel.split("/", 1)[0]
        filename = rel.split("/")[-1]
        if entry.get("status") != "ok":
            rows.append({
                "game": TITLES.get(folder, folder),
                "filename": filename,
                "arch": "not a PE",
                "os": "DOS",
                "score": 0,
                "status": "not a PE executable: data file shipped with the game",
                "evidence": f"sha256 {entry.get('sha256', '?')[:12]}",
                "hash": entry.get("sha256", ""),
                "kind": "invalid",
            })
            continue
        if entry.get("pe_type") == "PE32+":
            status = "not yet run; PE32+ backend not implemented (pipeline fails closed)"
        else:
            status = LEGACY_OUTCOMES.get(rel, "not yet run by the autorecomp pipeline")
        rows.append({
            "game": TITLES.get(folder, folder),
            "filename": filename,
            "arch": f"{entry.get('architecture', '?')} ({entry.get('pe_type', '?')})",
            "os": "Windows",
            "score": 0,
            "status": status,
            "evidence": f"sha256 {entry.get('sha256', '?')[:12]}",
            "hash": entry.get("sha256", ""),
            "kind": "pe32plus" if entry.get("pe_type") == "PE32+" else "valid",
        })
    return rows


def _measured_rows(runs, known_hashes):
    rows = []
    for run in runs:
        status = f"{run.get('verification', 'not-run')}: {run.get('status', '')}".strip()
        evidence = run.get("evidence", "")
        if ((run.get("verification") or "not-run") in EVIDENCE_PASS_STATUSES
                and not evidence_is_committed(run)):
            status = f"NO COMMITTED EVIDENCE - {status}"
            evidence = f"{evidence} (not a committed proof file)"
        if run.get("host"):
            evidence = f"{evidence} (host: {run['host']})"
        rows.append({
            "game": run.get("game", "?"),
            "filename": run.get("filename", "?"),
            "arch": run.get("arch", "?"),
            "os": run.get("os", "Windows"),
            "score": int(run.get("score") or 0),
            "status": status,
            "evidence": evidence,
            "hash": run.get("input_sha256", ""),
            "kind": "measured",
        })
    # Only offer "not yet measured" rows for corpus entries without a run.
    rows.extend(r for r in known_hashes
                if not any(run.get("input_sha256") == r["hash"] for run in runs))
    return rows


def _table(rows):
    lines = ["| Game | Filename | Arch | OS | PASS /100 | Status | Evidence |",
             "| --- | --- | --- | --- | ---: | --- | --- |"]
    for row in rows:
        score = f"{row['score']}†" if row.get("upstream") else str(row["score"])
        lines.append(
            f"| {_esc(row['game'])} | {_esc(row['filename'])} | {_esc(row['arch'])} "
            f"| {_esc(row['os'])} | {score} | {_esc(row['status'])} "
            f"| {_esc(row['evidence'])} |")
    return "\n".join(lines)


MATRIX_PATH = REPO_ROOT / "docs" / "GAME_MATRIX.md"

# Verification status -> the plain-language verdict a reader wants. The
# statuses are the pipeline's fail-closed set (docs/VERIFICATION.md); nothing
# here promotes one.
STATUS_VERDICT = {
    "scenario-pass": "PASS - scenario verified",
    "verified-playable-scope": "PASS - playable within the recorded scope",
    "partial": "PARTIAL - launches, no scenario oracle",
    "launch-failed": "FAIL - rebuilt process cannot boot",
    "compile-failed": "FAIL - generated source does not compile",
    "trace-diverged": "FAIL - trace diverged from the reference",
    "not-run": "NOT RUN",
    "skipped": "NOT RUN - fail-closed",
}


def _project_stage_data(project_dir):
    """Read the stage data a project's report.json carries, or {}."""
    report = _load_json(Path(project_dir) / "reports" / "report.json", {})
    stages = {s.get("name"): s for s in report.get("stages", [])}
    return report, stages


def _format_target_label(label):
    if not label:
        return ""
    if "Darwin arm64" in label:
        return "macOS arm64 (native)"
    return label.replace(" (host build)", "").strip()


def _port_details(run):
    """One line describing what the port actually produced, from measured data.

    Recovered function count, generated source size, the rebuilt binary and its
    size, and how many imports the runtime could not bind. Every number comes
    from the project's own report.json; a missing stage leaves its slot out
    rather than guessing.
    """
    project_dir = run.get("project_dir")
    if not project_dir:
        # Rows recorded before project_dir was stored: recover it from the
        # evidence path (projects/<name>/reports/report.html).
        evidence = run.get("evidence") or ""
        if evidence.endswith("/reports/report.html"):
            project_dir = evidence[: -len("/reports/report.html")]
    if not project_dir:
        return run.get("port_details") or "-"
    _report, stages = _project_stage_data(project_dir)
    if not stages:
        cached = run.get("port_details") or "no report.json"
        host_lbl = _format_target_label(run.get("host") or "")
        if host_lbl and "binary " in cached and host_lbl not in cached:
            if "; low-level " in cached:
                cached = cached.replace("; low-level ", f" [{host_lbl}]; low-level ", 1)
            else:
                cached = f"{cached}; {host_lbl}"
        return cached

    bits = []
    recover = (stages.get("recover") or {}).get("data") or {}
    if recover.get("count") is not None:
        bits.append(f"{recover['count']} fns recovered")
    translate = (stages.get("translate") or {}).get("data") or {}
    if translate.get("source_units") is not None:
        mib = translate.get("source_bytes", 0) / (1024 * 1024)
        bits.append(f"{translate['source_units']} units / {mib:.1f} MiB C")
    compile_data = (stages.get("compile") or {}).get("data") or {}
    if compile_data.get("binary"):
        binary = Path(compile_data["binary"])
        size = compile_data.get("binary_bytes") or (
            binary.stat().st_size if binary.exists() else 0)
        target_lbl = _format_target_label(
            ((_report.get("outcome") or {}).get("target") or {}).get("label")
            or run.get("host") or "")
        target_note = f" [{target_lbl}]" if target_lbl else ""
        bits.append(f"binary {binary.name} {size / 1024:.0f} KiB{target_note}")
    run_data = (stages.get("run") or {}).get("data") or {}
    bound = run_data.get("bound_imports")
    unresolved = run_data.get("unresolved_import_count")
    if bound is not None:
        bits.append(f"{bound} imports bound"
                    + (f", {unresolved} unresolved" if unresolved else ""))
    cats = ((stages.get("project") or {}).get("data") or {}).get("source_categories")
    if cats:
        bits.append(f"low-level {cats.get('LOW_LEVEL_FALLBACK', 0)}"
                    f"/ high-level verified {cats.get('HIGH_LEVEL_VERIFIED', 0)}")
    # Where the port stopped, so a failed row says why rather than listing only
    # the stages that happened to run before it.
    stopped = [s for s in stages.values() if s.get("status") == "failed"]
    if stopped:
        names = [s.get("name") for s in
                 sorted(stopped, key=lambda s: s.get("name"))]
        first = stopped[0]
        bits.append(f"{'/'.join(names)} failed: "
                    f"{_esc(str(first.get('message') or ''))[:100]}")
    return "; ".join(bits) if bits else "-"


def _matrix_table(rows):
    lines = ["| Name | OS | Arch | Port details | Frame proof | Working status |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        lines.append(
            f"| {_esc(row['game'])} | {_esc(row['os'])} | {_esc(row['arch'])} "
            f"| {_esc(row['details'])} | {_esc(row.get('frames', '-'))} "
            f"| {_esc(row['status'])} |")
    return "\n".join(lines)


FRAMES_PATH = REPO_ROOT / "reports" / "frames"

# The fail-closed statuses that claim a process reached its entry point.
# Such a row must point at a proof file that exists in this repository; a
# git-ignored projects/** build log is not a committed proof.
EVIDENCE_PASS_STATUSES = frozenset(
    ("scenario-pass", "verified-playable-scope", "partial"))

_TRACKED_CACHE = None


def _tracked_files(repo_root=REPO_ROOT):
    """Git-tracked paths in this checkout, cached for the whole render."""
    global _TRACKED_CACHE
    if _TRACKED_CACHE is None:
        try:
            out = subprocess.run(
                ["git", "-C", str(repo_root), "ls-files", "-z"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=True).stdout
            _TRACKED_CACHE = {p.decode("utf-8", "replace")
                              for p in out.split(b"\0") if p}
        except (OSError, subprocess.CalledProcessError):
            _TRACKED_CACHE = set()
    return _TRACKED_CACHE


def evidence_path(run, repo_root=REPO_ROOT):
    """Normalise a run's evidence string to a repo-relative path, or None.

    Older rows append ``(host: ...)`` to the path, and some point at absolute
    paths on a build machine. Only relative paths inside this repository can
    be committed proof files.
    """
    raw = str(run.get("evidence") or "").strip()
    if not raw:
        return None
    raw = raw.split(" (host:", 1)[0].strip()
    path = Path(raw)
    if path.is_absolute():
        return None
    try:
        return path.resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def evidence_is_committed(run, repo_root=REPO_ROOT, tracked=None):
    """True only when evidence names a tracked file that exists in the tree."""
    rel = evidence_path(run, repo_root)
    if not rel:
        return False
    if tracked is None:
        tracked = _tracked_files(repo_root)
    return rel in tracked and (Path(repo_root) / rel).is_file()


def audit_evidence(runs, repo_root=REPO_ROOT):
    """Rows claiming PASS/PARTIAL without a committed proof file."""
    tracked = _tracked_files(repo_root)
    return [r for r in runs
            if (r.get("verification") or "not-run") in EVIDENCE_PASS_STATUSES
            and not evidence_is_committed(r, repo_root, tracked)]


def load_frame_proofs(root=FRAMES_PATH):
    """Load frame-proof reports produced by tools.frame_proof.

    A row gets frame evidence only from a checked-in proof JSON; a bare
    screenshot or a claim in prose never populates this column.
    """
    proofs = []
    root = Path(root)
    if not root.is_dir():
        return proofs
    for path in sorted(root.rglob("frame-proof.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        frames = doc.get("frames", [])
        nonblank = [f for f in frames if not f.get("blank")]
        labels = (str(doc.get("filename", "")) + " " +
                  " ".join(f"{f.get('game', '')} {f.get('path', '')}"
                           for f in frames)).lower()
        proofs.append({
            "path": str(path),
            "total": len(frames),
            "nonblank": len(nonblank),
            "labels": labels,
        })
    return proofs


def frame_proof_for(proofs, filename):
    """Return a short frame-proof cell for one executable, or '-'."""
    stem = Path(filename or "").stem.lower()
    for proof in proofs:
        if stem and stem in proof["labels"]:
            if proof["nonblank"]:
                return f"{proof['nonblank']}/{proof['total']} non-blank frames"
            return f"0/{proof['total']} non-blank (blank frames)"
    return "-"


def render_matrix(inventory_path=INVENTORY_PATH, results_path=RESULTS_PATH):
    """The all-games matrix: this corpus, the upstream PCRecomp projects, and
    every row's working status in plain language."""
    inventory = _load_json(inventory_path, {})
    runs = load_results(results_path)
    tracked = _tracked_files()
    proofs = load_frame_proofs()
    corpus = _corpus_rows(inventory) if inventory else []
    known = [r for r in corpus if r["kind"] in ("valid", "pe32plus")]
    invalid = [r for r in corpus if r["kind"] == "invalid"]

    rows = []
    for run in sorted(runs, key=lambda r: (r.get("game", "?"),
                                           r.get("filename", "?"))):
        status = run.get("verification") or "not-run"
        committed = evidence_is_committed(run, tracked=tracked)
        verdict = STATUS_VERDICT.get(status, status)
        reason = run.get("status") or ""
        details = _port_details(run)
        if status in EVIDENCE_PASS_STATUSES and not committed:
            # Fail closed: a PASS/PARTIAL claim must be backed by a file that
            # is actually in this repository, not a git-ignored build tree.
            verdict = "NOT RUN - no committed evidence"
            details = ("unsupported measurement: evidence path is not a "
                       "committed proof file; " + details)
        elif reason and status not in ("scenario-pass", "verified-playable-scope"):
            verdict = f"{verdict} - {reason}"
        game = run.get("game", "?")
        filename = run.get("filename", "?")
        display_name = (f"{game} ({filename})"
                        if filename and game.lower() != Path(filename).stem.lower()
                        else game)
        rows.append({
            "game": display_name,
            "filename": filename,
            "os": run.get("os", "Windows"),
            "arch": run.get("arch", "?"),
            "details": details,
            "frames": frame_proof_for(proofs, filename),
            "status": verdict,
            "measured": True,
            "supported": committed,
            "verification": status,
        })

    measured_files = {r["filename"] for r in rows}
    for row in sorted(known, key=lambda r: (r["game"], r["filename"])):
        if row["filename"] in measured_files:
            continue
        rows.append({
            "game": row["game"],
            "filename": row["filename"],
            "os": row["os"],
            "arch": row["arch"],
            "details": "not run by this pipeline"
                       + (" (PE32+ is fail-closed today)"
                          if row["kind"] == "pe32plus" else ""),
            "frames": "-",
            "status": "NOT RUN",
            "measured": False,
        })

    for row in sorted(invalid, key=lambda r: (r["game"], r["filename"])):
        rows.append({
            "game": row["game"],
            "filename": row["filename"],
            "os": row["os"],
            "arch": row["arch"],
            "details": "data file shipped with the game, not an executable",
            "frames": "-",
            "status": "NOT APPLICABLE",
            "measured": False,
        })

    upstream = sorted(UPSTREAM, key=lambda u: (-u[6], u[1]))

    out = []
    out.append("# Game matrix")
    out.append("")
    out.append("Every game this product knows about, in one table: the locally "
               "supplied corpus this repository measures, and the upstream "
               "PCRecomp projects whose status is quoted from their own README. "
               "No game binary or asset is stored in this repository.")
    out.append("")
    out.append("**Working status** is this pipeline's fail-closed verdict "
               "(`docs/VERIFICATION.md`), not an impression: `PASS` requires a "
               "recorded scenario; `PARTIAL` means the rebuilt process launched "
               "but no independent scenario was run; `FAIL` means it did not get "
               "that far. `NOT RUN` is exactly that. Upstream rows are claims, "
               "marked `†`, and are never counted as this product's results.")
    out.append("")
    out.append("**Frame proof** comes only from a checked-in "
               "`reports/frames/**/frame-proof.json` produced by "
               "`tools.frame_proof`: it counts frames with real pixel content "
               "and refuses single-colour buffers. It is render evidence, not "
               "an oracle — it never promotes a row to `PASS` on its own.")
    out.append("")
    out.append("**Port details** are read from each project's own "
               "`reports/report.json`: functions recovered, generated C, the "
               "rebuilt binary, imports the runtime bound, and the "
               "low-level/high-level split. `LOW_LEVEL_FALLBACK` functions are "
               "register-transfer C, not reconstructed high-level source. A "
               "row whose `evidence` points into the git-ignored `projects/` "
               "tree is shown as NOT RUN, because it has no committed proof.")
    out.append("")
    out.append(f"Updated {_now()} by `python -m tools.gamesdb --matrix`.")
    # Count what is actually evidenced. The old headline counted every
    # 'partial' row as "running natively" even when it exited non-zero, had
    # hundreds of unresolved imports, or had no committed proof at all.
    evidenced = [r for r in rows if r.get("measured") and r.get("supported")]
    running = [r for r in evidenced if r.get("verification")
               in EVIDENCE_PASS_STATUSES]
    zero_unresolved = [r for r in running
                       if "0 unresolved" in ((r.get("details") or "")
                                             + " " + (r.get("status") or ""))]
    if runs:
        out.append("")
        out.append(
            f"**Evidence-backed results:** **{len(evidenced)} / {len(runs)}** "
            f"recorded rows point at a committed proof file; "
            f"**{len(running)}** of those carry a PASS/PARTIAL verdict "
            f"(**{len(zero_unresolved)}** explicitly report `0` unresolved "
            f"imports). Other rows are NOT RUN / FAIL here even when a local "
            f"build log exists outside version control.")
    out.append("")
    out.append("## This repository (measured)")
    out.append("")
    out.append(_matrix_table([r for r in rows if r["measured"]])
               if any(r["measured"] for r in rows)
               else "Nothing measured yet.")
    out.append("")
    out.append("## This repository (not measured)")
    out.append("")
    out.append(_matrix_table([r for r in rows if not r["measured"]])
               if any(not r["measured"] for r in rows)
               else "Every corpus entry has been measured.")
    out.append("")
    out.append("## Upstream PCRecomp projects (claims, not measurements)")
    out.append("")
    out.append("Status text is quoted from each project's own README; the "
               "architecture and OS columns are what that README states. These "
               "rows are context for the corpus above, never evidence that this "
               "pipeline reproduces them.")
    out.append("")
    up_rows = [{
        "game": f"[{title}](https://github.com/sp00nznet/{slug})",
        "filename": filename,
        "os": os_name,
        "arch": arch,
        "details": f"{era}; upstream README claim",
        "frames": "-",
        "status": f"{claim}†",
    } for slug, title, era, arch, os_name, claim, _score, filename in upstream]
    out.append(_matrix_table(up_rows))
    out.append("")
    return "\n".join(out)


def write_matrix(matrix_path=MATRIX_PATH, **kwargs):
    matrix_path = Path(matrix_path)
    text = render_matrix(**kwargs)
    marker = "## Captured frames"
    if matrix_path.exists():
        existing = matrix_path.read_text()
        if marker in existing:
            text = text.rstrip() + "\n\n---\n\n" + existing[existing.index(marker):].rstrip() + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(matrix_path.parent), prefix=".matrix-",
                               suffix=".md")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, matrix_path)
    except BaseException:
        os.unlink(tmp)
        raise
    return matrix_path


def render_table(inventory_path=INVENTORY_PATH, results_path=RESULTS_PATH):
    inventory = _load_json(inventory_path, {})
    runs = load_results(results_path)
    corpus = _corpus_rows(inventory) if inventory else []
    known = [r for r in corpus if r["kind"] in ("valid", "pe32plus")]
    invalid = [r for r in corpus if r["kind"] == "invalid"]

    measured = _measured_rows(runs, known)
    unmeasured = sorted((r for r in measured if r["kind"] != "measured"),
                        key=lambda r: (r["game"], r["filename"]))
    measured = sorted((r for r in measured if r["kind"] == "measured"),
                      key=lambda r: (-r["score"], r["game"], r["filename"]))

    upstream = []
    for slug, title, era, arch, os_name, claim, score, filename in UPSTREAM:
        upstream.append({
            "game": f"[{title}](https://github.com/sp00nznet/{slug})",
            "filename": filename,
            "arch": arch,
            "os": os_name,
            "score": score,
            "status": claim,
            "evidence": f"upstream README ({era})",
            "upstream": True,
        })
    upstream.sort(key=lambda r: (-r["score"], r["game"]))

    best = measured[0] if measured else None
    measured_count = len(runs)
    pe_count = sum(1 for r in known if r["kind"] == "valid")
    plus_count = sum(1 for r in known if r["kind"] == "pe32plus")

    out = []
    out.append("# Game database")
    out.append("")
    out.append("Every game this project knows about: locally supplied corpus, the "
               "legacy engine baseline for the same binaries, and the upstream "
               "PCRecomp project table. Filenames and hashes only - no game "
               "binaries or assets are stored here.")
    out.append("")
    out.append("`PASS /100` is the GameReComp pipeline score for the measured "
               "input: weighted work, not a verdict. inspect 10, recover 10, "
               "translate 10, project 10, compile 20, report 10, run 15 (full "
               "when the rebuilt process launched on its own, half when it "
               "launched then faulted, none when it never reached its entry), "
               "verify 15 (full for a verified scenario, 8 for a launch without "
               "an oracle). A stage only counts when it succeeded for the "
               "recorded hash, so a binary that never boots scores 70, not 85. "
               "Untested rows are 0.")
    out.append("")
    out.append("`†` marks a score derived from the upstream project's own README "
               "status; it was not measured by this repository and is not a "
               "verification claim. Legacy-engine outcomes are context only.")
    out.append("")
    out.append(f"Updated {_now()} by `python -m tools.gamesdb`. "
               f"Measured {measured_count}; local corpus {pe_count} PE32 + "
               f"{plus_count} PE32+ executable(s), "
               f"{len(invalid)} non-PE data file(s); "
               f"upstream projects {len(upstream)}.")
    if best:
        out.append("")
        out.append(f"Best measured result: **{_esc(best['game'])}** "
                   f"({_esc(best['filename'])}) at {best['score']}/100, "
                   f"{_esc(best['status'])}.")
    out.append("")
    out.append("## Measured by this repository")
    out.append("")
    if measured:
        out.append(_table(measured))
    else:
        out.append("No autorecomp runs recorded yet. Run "
                   "`./GameReComp <game.exe>` to add the first row.")
    out.append("")
    out.append("## Local corpus (not yet measured)")
    out.append("")
    out.append(_table(unmeasured) if unmeasured else "All corpus entries have been measured.")
    out.append("")
    if invalid:
        out.append("### Corpus files that are not PE executables")
        out.append("")
        out.append(_table(invalid))
        out.append("")
    out.append("## Upstream PCRecomp projects (claims only)")
    out.append("")
    out.append("Statuses below are quoted from the PCRecomp README's project "
               "table. The row score is a rubric mapping of that claim "
               "(plays/playable 80-90, runs 70-75, boots 50-55, compiles 40, "
               "bring-up/active 25-60, P0/recon 5-20), not a measurement made "
               "here and not an independent verification.")
    out.append("")
    out.append(_table(upstream))
    out.append("")
    return "\n".join(out)


def write_table(table_path=TABLE_PATH, **kwargs):
    table_path = Path(table_path)
    text = render_table(**kwargs)
    fd, tmp = tempfile.mkstemp(dir=str(table_path.parent), prefix=".table-",
                               suffix=".md")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, table_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return table_path


README_PATH = REPO_ROOT / "README.md"
README_BEGIN = "<!-- GAME-MATRIX:BEGIN -->"
README_END = "<!-- GAME-MATRIX:END -->"


def sync_readme(measured_rows, readme_path=README_PATH):
    """Keep the README's copy of the measured table equal to the matrix.

    The README carries the same rows between two markers so a reader sees the
    table without following a link. Regenerating the matrix rewrites that block
    from the same source, so the two can never drift apart. The markers stay in
    place; only what is between them is replaced.
    """
    readme_path = Path(readme_path)
    try:
        text = readme_path.read_text()
    except OSError:
        return None
    begin, end = README_BEGIN, README_END
    if begin in text and end in text:
        head, rest = text.split(begin, 1)
        _old, tail = rest.split(end, 1)
    else:
        # First run on a README written before the markers existed: put them
        # around the table that is already there.
        m = re.search(r"(^\| Name \| OS \| Arch \| Port details \| Working status \|\n"
                      r"(?:\|.*\n)+)", text, re.M)
        if not m:
            return None
        head = text[:m.start()]
        tail = text[m.end():]
    readme_path.write_text(
        f"{head}{begin}\n{measured_rows}\n{end}{tail}")
    return readme_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render table.md games database")
    parser.add_argument("--table", default=str(TABLE_PATH))
    parser.add_argument("--matrix", nargs="?", const=str(MATRIX_PATH),
                        default=None,
                        help="also render the all-games matrix to this path "
                             "(default: docs/GAME_MATRIX.md when --matrix is "
                             "given without a value)")
    parser.add_argument("--no-readme", action="store_true",
                        help="do not update the README's GAME-MATRIX block")
    parser.add_argument("--inventory", default=str(INVENTORY_PATH))
    parser.add_argument("--results", default=str(RESULTS_PATH))
    parser.add_argument("--record", action="append", default=[],
                        help="record a completed project directory into results.json")
    parser.add_argument("--audit", action="store_true",
                        help="exit non-zero if a PASS/PARTIAL row lacks a "
                             "committed proof file (no files are rewritten)")
    args = parser.parse_args(argv)
    if args.audit:
        bad = audit_evidence(load_results(Path(args.results)))
        for run in bad:
            print(f"unsupported {run.get('game')} ({run.get('filename')}): "
                  f"{run.get('evidence')!r}")
        print(f"audit: {len(bad)} unsupported PASS/PARTIAL row(s)")
        return 1 if bad else 0
    for proj in args.record:
        if (Path(proj) / "reports" / "report.json").is_file():
            record_result(proj, results_path=Path(args.results))
    path = write_table(Path(args.table), inventory_path=Path(args.inventory),
                       results_path=Path(args.results))
    print(f"wrote {path}")
    if args.matrix is not None:
        target = args.matrix or str(MATRIX_PATH)
        matrix = write_matrix(Path(target), inventory_path=Path(args.inventory),
                              results_path=Path(args.results))
        print(f"wrote {matrix}")
    if not args.no_readme:
        rendered = render_matrix(inventory_path=Path(args.inventory),
                                 results_path=Path(args.results))
        m = re.search(r"## This repository \(measured\)\n\n(.*?)\n\n## This repository "
                      r"\(not measured\)", rendered, re.S)
        if m:
            synced = sync_readme(m.group(1).strip())
            if synced:
                print(f"updated {synced} (measured table block)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
