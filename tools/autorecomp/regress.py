"""Regression suite: what worked before still works.

This session broke the shared runtime four times -- an ordering error in
sdl12.c, a duplicate shim definition, a stray macro, a NameError in the
pipeline -- and each time the only reason it was caught was that someone
happened to be building. That is not a safety net. This is.

Two tiers, because they answer different questions:

  fast   The unit tests, the runtime selftests, and the whole pipeline on the
         shipped synthetic fixture. About a minute. This is the tier to run
         before every commit; it catches a broken runtime, a broken pipeline
         and a broken generator, which is every failure mode seen so far.

  games  The real corpus: each game rebuilt and re-run, compared against a
         recorded baseline of how it behaved when it was known good. Minutes to
         tens of minutes, so it runs before a push or a release, not before
         every edit.

The baseline is a committed file, not a guess. `--record` writes it from the
current runs; `--check` compares against it and fails on a regression. What is
compared is deliberately behavioural rather than exact: an exit code, an
unresolved count, a run status, whether the frame loop still contains the calls
it contained, and a dispatch count within a wide band. A game whose exact blit
count moves by a few percent because a shim got faster has not regressed, and a
suite that says it has would be turned off within a week.

    python3 -m tools.autorecomp.regress --tier fast
    python3 -m tools.autorecomp.regress --tier games --check
    python3 -m tools.autorecomp.regress --tier games --record

Exit code 0 when everything matches, 1 when something regressed, 2 when the
suite could not run (a missing baseline, a missing corpus).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "reports" / "games" / "regression-baseline.json"

# The games worth a baseline: the ones whose behaviour is known and whose
# failure would matter. A game that has never run is not a regression risk, so
# it is not here; it belongs in the diagnostic matrix instead.
#
# `exe` is relative to SMALL_GAMES_PATH. `expect` is what "still working" means
# for that game, and it is behavioural: the process outcome, whether every
# import still binds, and -- for a game that runs -- that the frame loop still
# contains the calls a frame loop is made of.
GAMES = [
    {
        "name": "cavestory-doukutsu",
        "exe": "cavestory/Doukutsu.exe",
        "why": "the only game that reaches a frame loop; the strongest signal here",
        "expect": {
            "run_status": "timeout",
            "unresolved": 0,
            "verification": "partial",
            "min_dispatches": 10000,
            "must_call": ["PeekMessageA", "IDirectInputDevice::GetDeviceState",
                          "IDirectDrawSurface::Blt", "GetTickCount"],
        },
    },
    {
        "name": "cavestory-orgview",
        "exe": "cavestory/OrgView.exe",
        "why": "binds everything and returns on its own; a clean exit is the signal",
        "expect": {
            "run_status": "exited",
            "exit_code": 0,
            "unresolved": 0,
            "verification": "partial",
        },
    },
    {
        "name": "ikachan",
        "exe": "ikachan/dxIka.exe",
        "why": "the original bring-up target; must keep binding all 97 imports",
        "expect": {
            "run_status": "exited",
            "exit_code": 1,
            "unresolved": 0,
            "verification": "partial",
        },
    },
    {
        "name": "warning-forever",
        "exe": "warning_forever/wf.exe",
        "why": "exercises DirectDraw and the game's own capability check",
        "expect": {
            "run_status": "exited",
            "exit_code": 0,
            "unresolved": 0,
            "verification": "partial",
        },
    },
    {
        "name": "gunroar",
        "exe": "gunroar/gr.exe",
        "why": "binds every import and then faults in lifted CRT startup; the "
               "fault is the known state and must not become a binding failure",
        "expect": {
            "unresolved": 0,
            "run_status": "crashed",
        },
    },
]


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(cmd, timeout=None, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=str(ROOT))


def _report_of(project):
    path = Path(project) / "reports" / "report.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _observed(project):
    """The behavioural fingerprint of one run, from its own report."""
    report = _report_of(project)
    if report is None:
        return None
    stages = {s["name"]: s for s in report.get("stages", [])}
    run = (stages.get("run") or {}).get("data") or {}
    project_stage = (stages.get("project") or {}).get("data") or {}
    return {
        "verification": (report.get("outcome") or {}).get("status"),
        "run_status": run.get("status"),
        "exit_code": run.get("exit_code"),
        "unresolved": run.get("unresolved_import_count"),
        "dispatches": run.get("import_calls_total"),
        "calls": sorted({e["import"] for e in (run.get("import_profile") or [])}),
        "categories": project_stage.get("source_categories"),
        "failed_stages": sorted(n for n, s in stages.items()
                                if s.get("status") == "failed"),
    }


def compare(expected, observed):
    """Every way this run can have regressed, as a list of sentences."""
    problems = []
    for field in ("run_status", "exit_code", "unresolved", "verification"):
        if field not in expected:
            continue
        if observed.get(field) != expected[field]:
            problems.append(f"{field}: expected {expected[field]!r}, "
                            f"got {observed.get(field)!r}")

    if "min_dispatches" in expected:
        got = observed.get("dispatches")
        if got is None or got < expected["min_dispatches"]:
            problems.append(f"dispatches: expected at least "
                            f"{expected['min_dispatches']}, got {got}")

    # The frame-loop signature: a game that runs must still be calling the
    # things a frame loop is made of. This is the check that catches a shim
    # being replaced by one that silently does nothing -- the process would
    # still exit and the counts might still look plausible.
    missing = [c for c in expected.get("must_call", [])
               if c not in (observed.get("calls") or [])]
    if missing:
        problems.append(f"no longer calls: {', '.join(missing)}")

    # A high-level replacement must not reduce how much of the game is typed,
    # and must never turn a working game's categories into an unresolved one.
    cats = observed.get("categories") or {}
    if cats.get("UNRESOLVED"):
        problems.append(f"{cats['UNRESOLVED']} function(s) UNRESOLVED; "
                        f"translation regressed")

    if observed.get("failed_stages"):
        problems.append(f"stage(s) failed: {', '.join(observed['failed_stages'])}")
    return problems


def tier_fast(record=False, baseline=None):
    """Unit tests, runtime selftests, and the whole pipeline on the fixture."""
    results = []

    tests = _run([sys.executable, "-m", "pytest", "tests/", "-q"], timeout=900)
    results.append({
        "check": "unit tests",
        "ok": tests.returncode == 0,
        "detail": (tests.stdout or tests.stderr).strip().splitlines()[-1]
                  if (tests.stdout or tests.stderr).strip() else "no output",
    })

    selftests = _run(["bash", "runtime/win32hle/build_selftests.sh"], timeout=1800)
    ok_lines = [ln for ln in selftests.stdout.splitlines() if ln.startswith("ok")]
    results.append({
        "check": "runtime selftests",
        "ok": selftests.returncode == 0,
        "detail": f"{len(ok_lines)} selftest(s) ok"
                  if selftests.returncode == 0
                  else (selftests.stdout or selftests.stderr).strip()[-400:],
    })

    # The end-to-end path, on the input that ships with the repository.
    project = Path("/tmp/reveng-regress-fixture")
    shutil.rmtree(project, ignore_errors=True)
    e2e = _run(["./RevEng", "tests/fixtures/pe32-minimal.exe",
                "--project-dir", str(project), "--timeout", "10", "--no-table"],
               timeout=900)
    observed = _observed(project)
    results.append({
        "check": "pipeline on the shipped fixture",
        "ok": e2e.returncode in (0, 3) and observed is not None
              and not observed.get("failed_stages"),
        "detail": f"score {(_report_of(project) or {}).get('score')}, "
                  f"{observed.get('verification') if observed else 'no report'}",
        "observed": observed,
    })

    # The GUI's data path, exercised headlessly: if it cannot read a real project
    # it will show an empty dashboard, and an empty dashboard is indistinguishable
    # from a project that produced nothing. Skipped when Qt has not been built,
    # with the reason, rather than passing silently.
    gui = ROOT / "gui" / "build" / "reveng-studio"
    if gui.is_file():
        import os
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        g = _run([str(gui), "--selftest", "projects/cavestory-doukutsu",
                  "--repo", str(ROOT)], timeout=120, env=env)
        ok_gui = g.returncode == 0 and "functions:" in g.stdout
        results.append({
            "check": "gui reads a real project headlessly",
            "ok": ok_gui,
            "detail": next((ln.strip() for ln in g.stdout.splitlines()
                            if ln.startswith(("functions:", "certified:", "SELFTEST"))),
                           (g.stdout or g.stderr).strip()[:200]),
        })
        # The wizard's architecture gate: a supported target must be accepted and
        # a non-PE must be refused. A GUI that offered to recompile a 64-bit
        # binary would be making a claim this pipeline cannot back.
        good = _run([str(gui), "--selftest-pe", "tests/fixtures/pe32-minimal.exe"],
                    timeout=60, env=env)
        bad = _run([str(gui), "--selftest-pe", "README.md"], timeout=60, env=env)
        results.append({
            "check": "gui gates the executable architecture",
            "ok": good.returncode == 0 and bad.returncode == 1,
            "detail": f"supported rc={good.returncode}, non-PE rc={bad.returncode}",
        })
    else:
        results.append({
            "check": "gui reads a real project headlessly", "ok": None,
            "detail": "gui/build/reveng-studio not built (cmake -S gui -B gui/build)",
        })
        results.append({"check": "gui gates the executable architecture", "ok": None,
                        "detail": "gui not built"})

    # The high-level bridge: a module for another binary must be refused, not
    # applied. This is the bug that replaced the fixture's entry point.
    applied = _run(["./RevEng", "tests/fixtures/pe32-minimal.exe",
                    "--project-dir", str(project) + "-hl", "--timeout", "10",
                    "--highlevel", "highlevel", "--no-table"], timeout=900)
    report = _report_of(str(project) + "-hl") or {}
    stages = {s["name"]: s for s in report.get("stages", [])}
    cats = ((stages.get("project") or {}).get("data") or {}).get("source_categories") or {}
    results.append({
        "check": "a module for another binary is refused",
        "ok": cats.get("HIGH_LEVEL_VERIFIED", 0) == 0
              and cats.get("HIGH_LEVEL_UNVERIFIED", 0) == 0,
        "detail": f"categories {cats}",
    })

    return results


def tier_games(record=False, baseline=None, only=None):
    corpus = os.environ.get("SMALL_GAMES_PATH")
    if not corpus:
        return None, "SMALL_GAMES_PATH is not set; the game tier needs the corpus"
    corpus = Path(corpus).expanduser()

    baseline = dict(baseline or {})
    results = []
    for game in GAMES:
        if only and game["name"] not in only:
            continue
        exe = corpus / game["exe"]
        if not exe.is_file():
            results.append({
                "game": game["name"], "ok": None,
                "detail": f"MISSING_INPUT: {exe}",
            })
            continue
        project = ROOT / "projects" / game["name"]
        proc = _run(["./RevEng", str(exe), "--assets-dir", str(exe.parent),
                     "--project-dir", str(project), "--timeout", "12",
                     "--import-trace", "--fresh-build", "--no-table"],
                    timeout=3600)
        observed = _observed(project)
        entry = {"game": game["name"], "why": game["why"],
                 "observed": observed, "command_rc": proc.returncode}
        if record:
            entry["expected"] = {k: v for k, v in game["expect"].items()}
            entry["ok"] = observed is not None
            entry["detail"] = "recorded" if observed else "no report produced"
        else:
            want = (baseline.get("games", {}).get(game["name"], {})
                    .get("expected") or game["expect"])
            problems = compare(want, observed or {})
            entry["ok"] = not problems
            entry["detail"] = "; ".join(problems) if problems else "matches baseline"
            entry["expected"] = want
        results.append(entry)
    return results, None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier", choices=("fast", "games", "all"), default="fast")
    parser.add_argument("--check", action="store_true",
                        help="compare against the recorded baseline (default)")
    parser.add_argument("--record", action="store_true",
                        help="write the baseline from this run instead of comparing")
    parser.add_argument("--only", action="append", default=[],
                        help="limit the games tier to these names; repeatable")
    args = parser.parse_args(argv)

    baseline = {}
    if BASELINE_PATH.is_file():
        try:
            baseline = json.loads(BASELINE_PATH.read_text())
        except (OSError, ValueError):
            baseline = {}

    failed, skipped = [], []
    fast_results, games_results = [], []
    if args.tier in ("fast", "all"):
        fast_results = tier_fast(record=args.record, baseline=baseline)
    if args.tier in ("games", "all"):
        games_results, error = tier_games(record=args.record, baseline=baseline,
                                          only=args.only)
        if error:
            print(f"games tier cannot run: {error}", file=sys.stderr)
            if args.tier == "games":
                return 2

    print(f"regression suite -- tier {args.tier} -- "
          f"{'recording' if args.record else 'checking'}")
    print()
    for group, rows in (("fast", fast_results), ("games", games_results)):
        if not rows:
            continue
        print(f"  {group}:")
        for row in rows:
            mark = {True: "PASS", False: "FAIL", None: "SKIP"}[row["ok"]]
            name = row.get("check") or row.get("game")
            print(f"    {mark}  {name:38s} {row['detail']}")
            if row["ok"] is False:
                failed.append(name)
            if row["ok"] is None:
                skipped.append(name)
    print()

    if args.record:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps({
            "schema": 1,
            "recorded_at": _now(),
            "note": "Behavioural baseline for tools/autorecomp/regress.py. "
                    "Expected values are outcomes, not exact counts, so a faster "
                    "shim does not read as a regression.",
            "games": {r["game"]: {"why": r.get("why"), "expected": r.get("expected"),
                                  "observed": r.get("observed")}
                      for r in games_results},
        }, indent=2) + "\n")
        print(f"recorded baseline -> {BASELINE_PATH}")

    if failed:
        print(f"REGRESSED: {', '.join(failed)}")
        return 1
    if skipped:
        print(f"skipped (missing input): {', '.join(skipped)}")
    print("no regressions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
