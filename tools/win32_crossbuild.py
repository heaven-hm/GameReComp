#!/usr/bin/env python3
"""Cross-compile recompiler-generated C source to a Windows PE.

This is SOURCE recompilation, not Mach-O binary translation: the generated C
is compiled with MinGW-w64 for x86 (PE32) or x86-64 (PE32+). The win32hle
runtime is POSIX-based, so a project that includes it will fail here until it
is ported; those failures are reported verbatim, never hidden behind a fake
PE or a success status.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

CC32 = "i686-w64-mingw32-gcc"
CC64 = "x86_64-w64-mingw32-gcc"


def find_cc(bits: int) -> str | None:
    return shutil.which(CC32 if bits == 32 else CC64)


def cross_build(project: Path, out: Path, bits: int = 32,
                sources: list[Path] | None = None, timeout: int = 600) -> dict:
    cc = find_cc(bits)
    if not cc:
        return {"status": "BLOCKED", "reason": f"{CC32 if bits == 32 else CC64} not found"}
    if not project.is_dir():
        return {"status": "BLOCKED", "reason": f"project not found: {project}"}
    srcs = sources or sorted(project.glob("*.c")) + sorted(project.glob("src/**/*.c"))
    if not srcs:
        return {"status": "BLOCKED", "reason": "no C sources found"}
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [cc, "-O2", "-o", str(out)] + [str(s) for s in srcs]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "BLOCKED", "reason": "compiler timeout", "command": cmd}
    if p.returncode != 0:
        return {"status": "FAIL", "reason": "cross-compilation failed",
                "compiler_output": p.stdout[-4000:], "command": cmd}
    if not out.is_file() or out.read_bytes()[:2] != b"MZ":
        return {"status": "FAIL", "reason": "compiler succeeded but no MZ output",
                "compiler_output": p.stdout[-2000:]}
    return {"status": "OK", "binary": str(out), "bytes": out.stat().st_size,
            "pe": "PE32" if bits == 32 else "PE32+", "sources": [str(s) for s in srcs],
            "note": "source-level cross-build; no game runtime validation implied"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--bits", type=int, choices=(32, 64), default=32)
    ap.add_argument("--source", action="append", type=Path)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--out-json", type=Path)
    args = ap.parse_args(argv)
    rec = cross_build(args.project, args.out, args.bits, args.source, args.timeout)
    text = json.dumps(rec, indent=2) + "\n"
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(text)
    print(text)
    return 0 if rec["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
