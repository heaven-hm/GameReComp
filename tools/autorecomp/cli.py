"""autorecomp command line: PE -> source -> native binary -> run -> verify."""

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import gamesdb  # noqa: E402
from tools.autorecomp import __version__  # noqa: E402
from tools.autorecomp.pipeline import Pipeline, run_pipeline  # noqa: E402

EXIT_VERIFIED = 0
EXIT_STAGE_FAILED = 2
EXIT_UNVERIFIED = 3


def _database_path(pe_path):
    for env in ("SMALL_GAMES_PATH", "LARGE_GAMES_PATH"):
        root = os.environ.get(env)
        if not root:
            continue
        try:
            return str(Path(pe_path).resolve().relative_to(
                Path(root).expanduser().resolve()))
        except (ValueError, OSError):
            continue
    return Path(pe_path).name


def _brief(text, limit=160):
    first = str(text).strip().splitlines()[0] if str(text).strip() else ""
    return first if len(first) <= limit else first[:limit - 3] + "..."


def _progress(evt):
    if evt.get("event") == "stage-start":
        print(f"==> {evt['stage']}", flush=True)
    elif evt.get("event") == "stage-end":
        status = evt.get("status", "?")
        mark = "ok" if status == "ok" else status
        print(f"    {mark:7s} {evt.get('seconds', 0):6.1f}s  "
              f"{evt['stage']}: {_brief(evt.get('message', ''))}", flush=True)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="RevEng",
        description="Windows PE -> PCRecomp C -> native executable -> run -> "
                    "verification reports. Game data stays where the user "
                    "supplied it; nothing is downloaded.")
    parser.add_argument("pe_file", help="original Windows .exe (PE32)")
    parser.add_argument("--project-dir", help="output project directory "
                        "(default: projects/<name>)")
    parser.add_argument("--assets-dir", help="working directory for the "
                        "rebuilt binary (default: the executable's directory)")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="seconds to let the rebuilt binary run (default 30)")
    parser.add_argument("--interactive", action="store_true",
                        help="use real SDL video/audio instead of dummy drivers")
    parser.add_argument("--oracle", help="JSON file with process-level oracle "
                        "checks: exit_code, stdout_contains, stderr_contains")
    parser.add_argument("--jobs", type=int, help="parallel compile jobs")
    parser.add_argument("--split", type=int, default=1000,
                        help="functions per generated source unit (default 1000)")
    parser.add_argument("--split-bytes", type=int, default=8 * 1024 * 1024,
                        help="soft cap on generated text per source unit "
                             "(default 8 MiB)")
    parser.add_argument("--build-timeout", type=float, default=None,
                        help="hard deadline for configure+build in seconds "
                             "(default: adaptive, 600 s + 5 s/MiB of generated "
                             "source capped at 3600 s; 0 disables)")
    parser.add_argument("--reuse-build", action="store_true",
                        help="adopt the existing build/<name> binary when its "
                             "recorded source digest matches the freshly "
                             "generated source, skipping recompilation; "
                             "refuses (fail-closed) on any mismatch")
    parser.add_argument("--import-trace", action="store_true",
                        help="record every import the rebuilt process dispatches "
                             "(RECOMP_TRACE) and put the call profile in the "
                             "report; evidence that a run reached a game loop "
                             "rather than a spin, at some cost in speed")
    parser.add_argument("--fresh-build", action="store_true",
                        help="delete build/ before configuring, so a cached "
                             "CMakeCache.txt cannot pin paths from an "
                             "uninstalled or moved dependency (Homebrew keg "
                             "moves are the common case)")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--no-run", action="store_true",
                        help="build but do not execute the rebuilt binary")
    parser.add_argument("--no-table", action="store_true",
                        help="do not record this run in table.md")
    parser.add_argument("--json", action="store_true",
                        help="print the final report JSON")
    parser.add_argument("--version", action="version",
                        version=f"RevEng {__version__}")
    return parser


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "readability":
        from .readability import main as readability_main  # noqa: PLC0415
        return readability_main(argv[1:])

    args = build_parser().parse_args(argv)
    pe_path = Path(args.pe_file).expanduser()
    if not pe_path.is_file():
        print(f"error: input executable not found: {pe_path}", file=sys.stderr)
        return EXIT_STAGE_FAILED

    pipeline = Pipeline(
        pe_path, project_dir=args.project_dir, assets_dir=args.assets_dir,
        timeout=args.timeout, headless=not args.interactive,
        oracle=args.oracle, jobs=args.jobs, split=args.split,
        split_bytes=args.split_bytes, build_timeout=args.build_timeout,
        build=not args.no_build, run_binary=not args.no_run,
        reuse_build=args.reuse_build, fresh_build=args.fresh_build,
        import_trace=args.import_trace,
        on_event=_progress)
    try:
        run_pipeline(pipeline)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    report_path = pipeline.project_dir / "reports" / "report.json"
    report = {}
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError):
        pass
    verification = (report.get("outcome") or {}).get("status", "not-run")
    score = report.get("score", pipeline.score())
    failed = [s["name"] for s in pipeline.stages if s["status"] == "failed"]

    print()
    print(f"project:      {pipeline.project_dir}")
    print(f"score:        {score}/100")
    print(f"verification: {verification}")
    if failed:
        print(f"failed stages: {', '.join(failed)}")
    print(f"report:       {pipeline.project_dir / 'reports' / 'report.html'}")

    if not args.no_table and (pipeline.project_dir / "reports" / "report.json").exists():
        try:
            gamesdb.record_result(pipeline.project_dir,
                                  relative_path=_database_path(pe_path))
            table = gamesdb.write_table()
            print(f"database:     {table}")
        except (OSError, ValueError) as exc:
            print(f"warning: could not update table.md: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(report, indent=2))

    if failed:
        return EXIT_STAGE_FAILED
    if verification in ("scenario-pass", "verified-playable-scope"):
        return EXIT_VERIFIED
    return EXIT_UNVERIFIED


if __name__ == "__main__":
    sys.exit(main())
