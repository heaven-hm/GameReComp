#!/usr/bin/env python3
"""Sequential, fail-closed full sweep of every row in a static inventory.

For each original PE32: run it under Wine/Highball, rebuild the native port with
that oracle, reproduce the native run, and classify PASS / FAIL / BLOCKED with a
reason. Nothing is skipped or silently dropped; generated projects are removed
after each row so a 500-app sweep does not fill the host disk.

Progress is appended to results.jsonl after every binary, so a stop or OOM
still leaves usable evidence for everything already attempted.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from tools import oracle_sweep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inventory", required=True, type=Path,
                    help="JSON with a rows[] list, each having a path field")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--build-timeout", type=int, default=900)
    ap.add_argument("--limit", type=int, default=0, help="0 = all rows")
    args = ap.parse_args(argv)

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    doc = json.loads(args.inventory.read_text())
    rows = doc["rows"]
    if args.limit:
        rows = rows[: args.limit]
    journal = out / "results.jsonl"
    counts = {"PASS": 0, "FAIL": 0, "BLOCKED": 0}

    for i, src in enumerate(rows, 1):
        exe = Path(src["path"])
        name = exe.stem
        # Keep names unique when several directories contain the same exe.
        tag = f"{i:04d}-{name}"
        started = time.time()
        try:
            row = oracle_sweep.sweep_one(exe, out, args.timeout, args.build_timeout)
        except Exception as exc:  # never skip a binary silently
            row = {"exe": str(exe), "name": name, "status": "BLOCKED",
                   "reason": f"sweep exception: {type(exc).__name__}: {str(exc)[:200]}"}
        row["tag"] = tag
        row["seconds"] = round(time.time() - started, 1)
        status = row.get("status", "BLOCKED")
        counts[status] = counts.get(status, 0) + 1
        with open(journal, "a") as f:
            f.write(json.dumps(row) + "\n")
        # Remove the generated tree; the proof JSON/report already survive.
        project = out / f"{name}-project"
        if project.is_dir():
            shutil.rmtree(project, ignore_errors=True)
        print(f"[{i}/{len(rows)}] {name:28} {status:8} "
              f"{row.get('reason', '')[:90]} ({row['seconds']}s)", flush=True)
    summary = {"schema": 1, "total": len(rows), "counts": counts,
               "journal": str(journal)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
