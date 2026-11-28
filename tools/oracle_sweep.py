#!/usr/bin/env python3
"""Sweep local PE32 apps through the Wine oracle -> native build -> acceptance flow.

Fail-closed: an app that hangs, needs a missing DLL, or does not reproduce its
original's process observables gets a recorded reason, never a PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from tools import wine_oracle


def combined_hash(binary: Path, cwd: Path, timeout: int) -> dict:
    try:
        p = subprocess.run([str(binary)], cwd=str(cwd), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, check=False)
        text = p.stdout
        return {"exit_code": p.returncode,
                "output_sha256": hashlib.sha256(text).hexdigest(),
                "bytes": len(text)}
    except subprocess.TimeoutExpired as exc:
        return {"exit_code": "TIMEOUT", "output_sha256": None,
                "bytes": len(exc.stdout or b"")}
    except OSError as exc:
        return {"exit_code": "MISSING", "output_sha256": None,
                "bytes": 0, "reason": str(exc)}


def sweep_one(exe: Path, out_root: Path, timeout: int, build_timeout: int) -> dict:
    name = exe.stem
    row = {"exe": str(exe), "name": name}
    oracle_path = out_root / f"{name}-oracle.json"
    try:
        run = wine_oracle.run_original(wine_oracle.find_wine(),
                                       wine_oracle.DEFAULT_BOTTLE, exe, [], timeout)
    except Exception as exc:
        row.update(status="BLOCKED", reason=f"oracle run: {exc}")
        return row
    if run["exit_code"] == "TIMEOUT":
        row.update(status="BLOCKED", reason="original hangs under Wine without input")
        return row
    oracle = wine_oracle.oracle_from_run(run)
    oracle_path.parent.mkdir(parents=True, exist_ok=True)
    oracle_path.write_text(json.dumps(oracle, indent=2) + "\n")
    row["original"] = {"exit_code": run["exit_code"], "sha256": run["sha256"],
                       "stdout_bytes": len(run["stdout"])}

    project = out_root / f"{name}-project"
    log = out_root / f"{name}-build.log"
    cmd = [str(Path.cwd() / "GameReComp"), str(exe), "--project-dir", str(project),
           "--no-table", "--oracle", str(oracle_path), "--timeout", "8"]
    with open(log, "w") as lf:
        built = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT,
                               timeout=build_timeout, check=False)
    report = project / "reports" / "report.json"
    if not report.is_file():
        row.update(status="BLOCKED", reason=f"build failed (exit {built.returncode})")
        return row
    doc = json.loads(report.read_text())
    outcome = doc.get("outcome", {})
    row["pipeline"] = {"score": doc.get("score"), "verification": outcome.get("status")}
    if outcome.get("status") != "scenario-pass":
        row.update(status="FAIL", reason=outcome.get("reason", "no scenario-pass"))
        return row

    binary = project / "build" / name
    if not binary.is_file():
        row.update(status="BLOCKED", reason="native binary missing")
        return row
    observed = combined_hash(binary, exe.parent, timeout)
    scenario = out_root / f"{name}-scenario.json"
    scenario.write_text(json.dumps({"args": [], "expected_exit": observed["exit_code"],
                                    "expected_output_sha256": observed["output_sha256"]},
                                   indent=2) + "\n")
    acc = out_root / f"{name}-acceptance.json"
    acc_cmd = [sys.executable, "-m", "tools.corpus_acceptance",
               "--original", f"{name}={exe}", "--native", f"{name}={binary}",
               "--pipeline-report", f"{name}={report}", "--kind", f"{name}=cli",
               "--scenario", f"{name}={scenario}", "--out", str(out_root / f"{name}-run"),
               "--json", str(acc)]
    subprocess.run(acc_cmd, stdout=subprocess.DEVNULL,
                   stderr=subprocess.STDOUT, timeout=build_timeout, check=False)
    if acc.is_file():
        a = json.loads(acc.read_text())
        summary = a.get("summary", {}).get("by_status", {})
        row["acceptance"] = summary
        row["status"] = "PASS" if summary.get("PASS", 0) >= 1 and summary.get("FAIL", 0) == 0 else "FAIL"
    else:
        row.update(status="BLOCKED", reason="no acceptance report")
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe", action="append", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--build-timeout", type=int, default=600)
    args = ap.parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for exe in args.exe:
        print(f"[{exe.name}] sweeping...", flush=True)
        row = sweep_one(exe, out, args.timeout, args.build_timeout)
        rows.append(row)
        print(f"[{exe.name}] {row['status']} {row.get('reason', '')}", flush=True)
    (out / "sweep.json").write_text(json.dumps({"schema": 1, "rows": rows}, indent=2) + "\n")
    print("PASS:", sum(1 for r in rows if r["status"] == "PASS"), "/", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
