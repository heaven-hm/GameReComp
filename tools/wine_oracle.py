#!/usr/bin/env python3
"""Generate a process-level oracle by running the ORIGINAL PE32 under Wine.

This is deliberately only a process oracle: exit code plus substrings of the
original binary's stdout/stderr. It is an independent execution of the shipped
executable (via the local Highball/CrossOver Wine engine), not a rebuild, but
it is still Wine rather than retail Windows -- reports that consume it must say
so rather than claiming gameplay equivalence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
WINE_NOISE = re.compile(
    r"freetype|or equal to 2\.0\.5|msync:|wineboot:|import_dll|ZwLoadDriver|"
    r"failed to open|wine:|err:", re.IGNORECASE)

DEFAULT_WINE = Path.home() / "Library/Application Support/Highball/engines" / \
    "x64-crossover26.3-r27/engine/bin/wine"
DEFAULT_BOTTLE = Path.home() / "Library/Application Support/Highball/bottles/Games"


def find_wine() -> str | None:
    override = os.environ.get("RECOMP_WINE")
    if override:
        return override
    if DEFAULT_WINE.is_file():
        return str(DEFAULT_WINE)
    return shutil.which("wine")


def run_original(wine: str, bottle: Path, exe: Path, args: list[str],
                 timeout: int, env=None) -> dict:
    """Copy the exe into the Wine bottle and run it by a Windows-style path."""
    if not exe.is_file():
        raise ValueError(f"original executable not found: {exe}")
    target_dir = bottle / "drive_c" / "recomp-oracle"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / exe.name
    shutil.copy2(exe, target)
    win_path = "C:\\recomp-oracle\\" + exe.name
    run_env = dict(os.environ if env is None else env)
    run_env.update({
        "WINEPREFIX": str(bottle),
        "WINEDEBUG": "-all",
        "WINEDLLOVERRIDES": "winemenubuilder.exe=d",
    })
    cmd = [wine, win_path] + args
    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    # Wine spawns helper processes that inherit the pipe handles; subprocess.run
    # with a timeout can therefore block forever after wine itself was killed.
    # Use Popen + communicate and always drain/kill the process group.
    proc = subprocess.Popen(cmd, cwd=str(bottle / "drive_c"), env=run_env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            proc.kill()
        out, err = proc.communicate(timeout=10)
        rc = "TIMEOUT"
    return {"exit_code": rc, "stdout": out or "", "stderr": err or "",
            "sha256": sha, "command": cmd, "wine": wine}


def oracle_from_run(run: dict, stdout_lines: int = 6,
                    stderr_lines: int = 4) -> dict:
    """Turn the recorded run into the JSON schema pipeline.py --oracle reads.

    Only non-empty, non-ANSI lines become needles; an empty needle would match
    every output and would be a fake check.
    """
    def needles(text, limit, drop_wine_noise=False):
        out = []
        for line in text.splitlines():
            if drop_wine_noise and WINE_NOISE.search(line):
                continue
            clean = ANSI.sub("", line).strip()
            if clean and clean not in out:
                out.append(clean[:200])
            if len(out) >= limit:
                break
        return out

    doc = {"source": "original PE32 executed under Wine (process-level, not retail Windows)",
           "sha256": run["sha256"], "wine": run["wine"],
           "stdout_contains": needles(run["stdout"], stdout_lines),
           "stderr_contains": needles(run["stderr"], stderr_lines,
                                      drop_wine_noise=True)}
    if isinstance(run["exit_code"], int):
        doc["exit_code"] = run["exit_code"]
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe", required=True, type=Path, help="original Windows PE32")
    ap.add_argument("--out", required=True, type=Path, help="oracle JSON to write")
    ap.add_argument("--bottle", type=Path, default=DEFAULT_BOTTLE)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--arg", action="append", default=[], help="argument to the original")
    args = ap.parse_args(argv)
    wine = find_wine()
    if not wine:
        print("error: no Wine engine found (set RECOMP_WINE)", file=sys.stderr)
        return 2
    try:
        run = run_original(wine, args.bottle, args.exe, args.arg, args.timeout)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    doc = oracle_from_run(run)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"original {args.exe.name}: exit={run['exit_code']} "
          f"stdout={len(run['stdout'])}B stderr={len(run['stderr'])}B")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
