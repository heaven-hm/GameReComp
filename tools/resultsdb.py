"""Rebuild reports/games/results.json from the projects' own reports.

The corpus database drifted once already: rows recorded before a day's runtime work
kept saying "launch-failed" for games that now run, which is the dangerous
direction -- a database that understates the pipeline while the README describes it
correctly. Two published numbers disagreeing is worse than either being merely old.

The repair is not to hand-edit the rows. It is to derive every row from the run that
produced it: each project's reports/report.json plus the binary's hash, stamped with
the time the row was read. If a project has no report, the row is not written at
all -- a missing measurement is reported as missing, never inherited from an older
file.

    python3 -m tools.resultsdb            # rewrite the database
    python3 -m tools.resultsdb --check    # say what would change, write nothing
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "reports" / "games" / "results.json"
CORPUS = Path("/Users/haseeb-mir/Downloads/recomp-apps/games")


def _sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _project_for(exe, corpus):
    """The project directory the pipeline writes for this executable."""
    from tools.autorecomp.projectgen import executable_name
    # The pipeline names a project after the executable's stem. Using the parent
    # directory here (which is how the corpus happens to be laid out) reads a
    # directory the run never wrote, and every row comes back missing.
    return ROOT / "projects" / executable_name(exe)


def row_for(exe, corpus, now):
    """One database row, or None when the run left no record."""
    project = _project_for(exe, corpus)
    report = project / "reports" / "report.json"
    try:
        data = json.loads(report.read_text())
    except (OSError, ValueError):
        return None
    stages = {s.get("name"): s for s in data.get("stages", [])}
    run = (stages.get("run") or {}).get("data") or {}
    inspect = (stages.get("inspect") or {}).get("data") or {}
    return {
        "relative_path": str(exe.relative_to(corpus)) if corpus in exe.parents else exe.name,
        "game": exe.parent.name.replace("-", " ").replace("_", " ").title(),
        "filename": exe.name,
        "arch": inspect.get("arch") or inspect.get("machine"),
        "os": "Windows",
        "score": data.get("score"),
        "verification": (data.get("outcome") or {}).get("status"),
        "status": (data.get("outcome") or {}).get("status"),
        "run_status": run.get("status"),
        "exit_code": run.get("exit_code"),
        "host": f"{__import__('platform').system()} {__import__('platform').machine()}",
        "input_sha256": _sha256(exe),
        "evidence": str(report.relative_to(ROOT)),
        "project_dir": str(project.relative_to(ROOT)),
        "generated_at": now,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--results", default=str(RESULTS))
    ap.add_argument("--check", action="store_true",
                    help="report what would change, write nothing")
    args = ap.parse_args(argv)

    corpus = Path(args.corpus)
    if not corpus.is_dir():
        print(f"corpus not found: {corpus}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(ROOT))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, missing = [], []
    for exe in sorted(corpus.rglob("*.exe")):
        row = row_for(exe, corpus, now)
        if row is None:
            missing.append(str(exe.relative_to(corpus)))
        else:
            rows.append(row)

    old = {}
    try:
        old = {r["relative_path"]: r for r in json.loads(Path(args.results).read_text())["runs"]}
    except (OSError, ValueError, KeyError):
        pass

    changed = []
    for r in rows:
        o = old.get(r["relative_path"])
        if not o or (o.get("score"), o.get("verification")) != (r["score"], r["verification"]):
            changed.append(r)

    print(f"measured {len(rows)} of {len(rows) + len(missing)} executable(s)")
    print(f"rows whose outcome changed: {len(changed)}")
    for r in changed:
        o = old.get(r["relative_path"], {})
        print(f"  {r['relative_path']:<44s} {o.get('score', '?'):>3} {str(o.get('verification')):14s}"
              f" -> {r['score']:>3} {str(r['verification'])}")
    if missing:
        print(f"no report (not written rather than inherited): {len(missing)}")
        for m in missing[:10]:
            print(f"  MISSING_INPUT {m}")

    if args.check:
        return 0

    payload = {"schema": 1, "runs": rows}
    Path(args.results).parent.mkdir(parents=True, exist_ok=True)
    Path(args.results).write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {args.results} ({len(rows)} rows, all derived from their own reports)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
