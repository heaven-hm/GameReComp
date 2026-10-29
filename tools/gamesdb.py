"""Games database: renders table.md from corpus metadata, legacy baseline,
upstream PCRecomp claims and measured autorecomp runs.

PASS /100 is the autorecomp pipeline score: weighted stage completion
(inspect 10, recover 10, translate 10, project 10, compile 20, run 15,
verify 15, report 10), where a partial verification earns 8/15. A score
only rises when the corresponding stage actually succeeded for the
recorded input hash. Rows marked with a dagger are upstream project
claims, not measurements made here.
"""

import argparse
import json
import os
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

    pe_path = Path(report["input"])
    outcome = report.get("outcome") or {}
    run = outcome.get("run") or {}
    host = (outcome.get("target") or {}).get("label") or ""
    arch = f"{inspect.get('machine', '?')} ({inspect.get('pe_type', '?')})"
    entry = {
        "relative_path": str(relative_path or pe_path.name),
        "game": TITLES.get(pe_path.parent.name, pe_path.parent.name),
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
    out.append("`PASS /100` is the autorecomp pipeline score for the measured "
               "input: weighted stage completion (inspect 10, recover 10, "
               "translate 10, project 10, compile 20, run 15, verify 15, "
               "report 10; partial verification 8/15). A stage only counts when "
               "it succeeded for the recorded hash. Untested rows are 0.")
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
                   "`./autorecomp <game.exe>` to add the first row.")
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render table.md games database")
    parser.add_argument("--table", default=str(TABLE_PATH))
    parser.add_argument("--inventory", default=str(INVENTORY_PATH))
    parser.add_argument("--results", default=str(RESULTS_PATH))
    args = parser.parse_args(argv)
    path = write_table(Path(args.table), inventory_path=Path(args.inventory),
                       results_path=Path(args.results))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
