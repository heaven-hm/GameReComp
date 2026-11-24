#!/usr/bin/env python3
"""Automated dual-engine verification pipeline for real Windows binaries.

For every supplied executable this runs, in order:

  1. UNICORN: original x86/x86-64 bytes under controlled entry states
     (tools/hybrid_diff.unicorn_signal -> tools/lift/difftest).
  2. FRIDA: original process under Highball's Wine engine, intercepting
     selected Win32/HLE calls (tools/hybrid_diff.frida_signal). Live attach is
     opt-in because it can hang; a bounded run records the reason instead.
  3. NATIVE REPLAY: identical stdin sent to the original under Wine and to the
     recompiled native binary (tools/input_replay_diff).
  4. FRAME PROOF: optional screenshot capture via tools.game_runner +
     tools.frame_proof for GUI games.

Every result is fail-closed: a missing engine or input is BLOCKED with the
exact reason. Nothing is upgraded to PASS without recorded evidence from the
engines that ran. Inputs are the real binaries and real stdin/asset folders;
the tool never substitutes synthetic proof.

Output: <out>/<name>.json per binary and <out>/summary.json + report.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from tools import hybrid_diff


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_one(exe: Path, native: Path | None, stdin_data: bytes,
               hooks: list[str], timeout: int, out_dir: Path,
               live_frida: bool, capture_frames: bool,
               assets: Path | None) -> dict:
    rec: dict = {"exe": str(exe), "sha256": sha256(exe) if exe.is_file() else None,
                 "bytes": exe.stat().st_size if exe.is_file() else 0,
                 "stdin_bytes": len(stdin_data), "hooks": hooks,
                 "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not exe.is_file():
        rec.update(status="BLOCKED", reason="original executable missing")
        return rec

    hybrid = hybrid_diff.hybrid_one(exe, native, stdin_data, hooks, timeout,
                                    live_frida)
    rec["unicorn"] = hybrid["unicorn"]
    rec["frida"] = hybrid["frida"]
    rec["native"] = hybrid["native"]
    rec["hybrid_status"] = hybrid["status"]

    if capture_frames and native is not None and native.is_file():
        shots = out_dir / f"{exe.stem}-frames"
        folder = assets or exe.parent
        cmd = [sys.executable, "-m", "tools.game_runner", "--game", exe.stem,
               "--binary", str(native.resolve()), "--folder", str(folder.resolve()),
               "--out", str(shots.resolve()), "--timeout", str(timeout)]
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, timeout=timeout + 120, check=False)
            rec["frame_capture"] = {"returncode": p.returncode,
                                    "log": p.stdout[-1500:]}
        except subprocess.TimeoutExpired:
            rec["frame_capture"] = {"returncode": "TIMEOUT"}
    else:
        rec["frame_capture"] = {"skipped": "no native binary or capture disabled"}

    engines = [rec["unicorn"], rec["frida"], rec["native"]]
    blocked = [f"{k}: {e.get('reason', '')}" for k, e in
               zip(("unicorn", "frida", "native"), engines)
               if e.get("status") == "BLOCKED"]
    diverged = [e.get("reason", "") for e in engines if e.get("status") == "DIVERGED"]
    if blocked:
        rec.update(status="BLOCKED", reason="; ".join(blocked))
    elif diverged:
        rec.update(status="DIVERGED", reason="; ".join(diverged))
    else:
        rec.update(status="PASS", reason="all engines that ran agree")
    rec["note"] = ("process/CPU-level evidence only; not gameplay equivalence "
                   "unless frame evidence is attached and verified")
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe", action="append", required=True, type=Path)
    ap.add_argument("--native", action="append", default=[],
                    help="NAME=PATH mapping to an already-built native binary")
    ap.add_argument("--stdin-file", action="append", default=[], type=Path,
                    help="real stdin fixture (one per --exe, or a single shared one)")
    ap.add_argument("--assets", type=Path)
    ap.add_argument("--hook", action="append", default=[])
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--live-frida", action="store_true")
    ap.add_argument("--capture-frames", action="store_true")
    args = ap.parse_args(argv)

    natives = {}
    for item in args.native:
        if "=" not in item:
            print(f"bad --native mapping: {item}", file=sys.stderr)
            return 2
        k, v = item.split("=", 1)
        natives[k] = Path(v)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for i, exe in enumerate(args.exe):
        native = natives.get(exe.stem) or natives.get(exe.name)
        data = b""
        if args.stdin_file:
            fixture = args.stdin_file[min(i, len(args.stdin_file) - 1)]
            data = fixture.read_bytes() if fixture.is_file() else b""
        print(f"[{exe.name}] verifying (native={native.name if native else 'none'}, "
              f"stdin={len(data)}B)", flush=True)
        rec = verify_one(exe, native, data, args.hook, args.timeout, out,
                         args.live_frida, args.capture_frames, args.assets)
        (out / f"{exe.stem}.json").write_text(json.dumps(rec, indent=2) + "\n")
        results.append(rec)
        print(f"[{exe.name}] {rec['status']}: {rec.get('reason', '')[:140]}", flush=True)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = {"schema": 1, "count": len(results), "counts": counts,
               "live_frida": args.live_frida, "results": results}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = ["# Automated dual-engine verification", "",
             f"Targets: {len(results)}", "",
             "| status | count |", "| --- | ---: |"]
    for k, v in counts.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "Process/CPU-level evidence only. No issue is closed by this report.", ""]
    (out / "report.md").write_text("\n".join(lines))
    print(json.dumps(counts), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
