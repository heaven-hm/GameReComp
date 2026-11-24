#!/usr/bin/env python3
"""Differential input replay: same keystrokes/stdin to original and native port.

The ORIGINAL Windows executable runs under the local Highball/CrossOver Wine
engine (Wine is Highball's engine, used directly so the original is an
independent execution, not a rebuild). The NATIVE port is the recompiled macOS
ARM64 binary. Both receive byte-identical stdin and the same arguments; exit
code and normalised stdout are compared. A mismatch is a real divergence, not
a failure of the harness.

This is process-level differential evidence. It does not prove gameplay,
frame or audio equivalence for graphical games.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from tools import wine_oracle


def run_native(binary: Path, args: list[str], stdin_data: bytes,
               cwd: Path, timeout: int) -> dict:
    proc = subprocess.Popen([str(binary)] + args, cwd=str(cwd),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = proc.communicate(input=stdin_data, timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        import os
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            proc.kill()
        out, err = proc.communicate(timeout=10)
        rc = "TIMEOUT"
    return {"exit_code": rc, "stdout": out.decode("utf-8", "replace"),
            "stderr": err.decode("utf-8", "replace")}


def normalise(text: str) -> str:
    lines = []
    for line in wine_oracle.ANSI.sub("", text).splitlines():
        if wine_oracle.WINE_NOISE.search(line):
            continue
        clean = line.replace("C:\\recomp-oracle\\", "").replace(
            "C:/recomp-oracle/", "").strip()
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def diff_one(original: Path, binary: Path, stdin_data: bytes,
             args: list[str], cwd: Path, timeout: int) -> dict:
    wine = wine_oracle.find_wine()
    if not wine:
        return {"status": "BLOCKED", "reason": "no Wine/Highball engine found"}
    if not original.is_file() or not binary.is_file():
        return {"status": "BLOCKED", "reason": "missing original or native binary"}
    orig = wine_oracle.run_original(wine, wine_oracle.find_bottle(), original,
                                    args, timeout, stdin_data=stdin_data)
    nat = run_native(binary, args, stdin_data, cwd, timeout)
    o, n = normalise(orig["stdout"]), normalise(nat["stdout"])
    result = {
        "original": {"exit_code": orig["exit_code"], "sha256": orig["sha256"],
                     "stdout_hash": hashlib.sha256(o.encode()).hexdigest()},
        "native": {"exit_code": nat["exit_code"],
                   "stdout_hash": hashlib.sha256(n.encode()).hexdigest()},
        "stdin_bytes": len(stdin_data), "args": args,
        "original_lines": o.splitlines()[:20], "native_lines": n.splitlines()[:20],
    }
    if orig["exit_code"] == "TIMEOUT" or nat["exit_code"] == "TIMEOUT":
        result.update(status="BLOCKED", reason="one side timed out")
    elif orig["exit_code"] != nat["exit_code"]:
        result.update(status="DIVERGED", reason=f"exit {orig['exit_code']} vs {nat['exit_code']}")
    elif o != n:
        result.update(status="DIVERGED", reason="stdout differs after normalisation")
    else:
        result["status"] = "MATCH"
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original", required=True, type=Path)
    ap.add_argument("--native", required=True, type=Path)
    ap.add_argument("--input", default="", help="stdin text sent to both")
    ap.add_argument("--input-file", type=Path)
    ap.add_argument("--arg", action="append", default=[])
    ap.add_argument("--cwd", type=Path)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    data = (args.input_file.read_bytes() if args.input_file
            else args.input.encode())
    cwd = args.cwd or args.original.parent
    rec = diff_one(args.original, args.native, data, args.arg, cwd, args.timeout)
    print(json.dumps(rec, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rec, indent=2) + "\n")
    return 0 if rec["status"] == "MATCH" else 1


if __name__ == "__main__":
    sys.exit(main())
