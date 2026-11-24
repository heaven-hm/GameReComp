#!/usr/bin/env python3
"""Run the ORIGINAL PE32 for process-level differential evidence.

PRIMARY runner: ``highball run <exe> [args]`` -- Highball is the product that
owns the Wine engine, bottle, renderer (dxvk/Metal) and sync mode. Every
comparison of an original against a native port must use it when available.

FALLBACK only: a raw ``wine <path>`` invocation, used solely when the highball
CLI is not installed. Every record says which runner produced it, so a fallback
run is never silently presented as a Highball run. Both are Wine-based, not
retail Windows; reports must say so and must not claim gameplay equivalence.
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
# Highball prints its own launch banner and completion line to stdout. They are
# runner chatter, not guest output, and are anchored so real app lines survive.
HIGHBALL_NOISE = re.compile(
    r"^Launching .+ in bottle .+\[engine: .+renderer: .+\]\.\.\.$"
    r"|^exit=\d+ after \d+s . log: .+$", re.IGNORECASE)
RUNNER_NOISE = re.compile(
    r"freetype|or equal to 2\.0\.5|msync:|wineboot:|import_dll|ZwLoadDriver|"
    r"failed to open|wine:|err:|^Launching .+ in bottle .+\[engine: .+renderer: .+\]\.\.\.$"
    r"|^exit=\d+ after \d+s . log: .+$", re.IGNORECASE)

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


def find_highball() -> str | None:
    """The Highball CLI bridge. Use it for the original side whenever present."""
    override = os.environ.get("RECOMP_HIGHBALL")
    if override:
        return override
    if os.environ.get("RECOMP_FORCE_WINE_FALLBACK"):
        return None
    return shutil.which("highball")


def find_bottle() -> Path:
    """Wine prefix for the labelled raw-Wine fallback only (Highball owns its
    own bottle). An isolated prefix avoids a shared wineserver whose WINEMSYNC
    mode disagrees with the client -- a real cause of spurious exit 1."""
    override = os.environ.get("RECOMP_WINE_BOTTLE")
    return Path(override) if override else DEFAULT_BOTTLE


def run_original(wine: str | None, bottle: Path, exe: Path, args: list[str],
                 timeout: int, env=None, stdin_data: bytes | None = None,
                 runner: str | None = None) -> dict:
    """Run the original PE through ``highball run`` (fallback: raw wine).

    ``runner`` may force "highball" or "wine"; otherwise Highball is used when
    installed. The returned record always names the runner actually used.
    """
    if not exe.is_file():
        raise ValueError(f"original executable not found: {exe}")
    highball = find_highball()
    use_hb = (runner == "highball" or (runner is None and highball is not None))
    if use_hb and not highball:
        raise ValueError("highball runner requested but highball CLI not found")
    if not use_hb and not wine:
        raise ValueError("raw wine fallback requested but no wine engine found")

    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    if use_hb:
        # Highball selects the bottle, engine, renderer and sync mode itself.
        cmd = [highball, "run", str(exe)] + list(args)
        cwd = str(exe.parent)
        run_env = dict(os.environ if env is None else env)
    else:
        # Labelled fallback only: copy into the prefix and run by Windows path.
        target_dir = bottle / "drive_c" / "recomp-oracle"
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exe, target_dir / exe.name)
        win_path = "C:\\recomp-oracle\\" + exe.name
        cmd = [wine, win_path] + list(args)
        cwd = str(bottle / "drive_c")
        run_env = dict(os.environ if env is None else env)
        run_env.update({
            "WINEPREFIX": str(bottle),
            "WINEDEBUG": "-all",
            "WINEDLLOVERRIDES": "winemenubuilder.exe=d",
        })

    # Wine spawns helper processes that inherit the pipe handles; Popen with a
    # timeout can therefore block after wine itself was killed. Always kill the
    # whole process group and drain the pipes.
    proc = subprocess.Popen(cmd, cwd=cwd, env=run_env,
                            stdin=(subprocess.PIPE if stdin_data is not None
                                   else subprocess.DEVNULL),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            start_new_session=True)
    payload = None if stdin_data is None else stdin_data.decode("utf-8", "replace")
    try:
        out, err = proc.communicate(input=payload, timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # Wine helpers may keep the inherited pipes open after the guest
            # was killed. Keep the bytes captured so far and report BLOCKED;
            # a timeout is never a successful run.
            out = exc.output or ""
            err = exc.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
        rc = "TIMEOUT"
    record = {"exit_code": rc, "stdout": out or "", "stderr": err or "",
              "sha256": sha, "command": cmd, "wine": wine,
              "highball": highball if use_hb else None,
              "runner": "highball" if use_hb else "wine-fallback",
              "bottle": str(bottle)}
    if rc == "TIMEOUT":
        record.update({"status": "BLOCKED", "blocked_class": "environment",
                       "reason": "guest process and its Wine helpers timed out; "
                                 "no verified stdout was produced"})
    return record


def oracle_from_run(run: dict, stdout_lines: int = 6,
                    stderr_lines: int = 4) -> dict:
    """Turn the recorded run into the JSON schema pipeline.py --oracle reads.

    Only non-empty, non-ANSI lines become needles; an empty needle would match
    every output and would be a fake check.
    """
    def needles(text, limit, drop_wine_noise=False):
        out = []
        for line in text.splitlines():
            if RUNNER_NOISE.search(line):
                continue
            clean = ANSI.sub("", line).strip()
            # The original runs from C:\recomp-oracle in the Wine bottle; a
            # native binary prints no such path. Normalise it away so the
            # oracle cannot require a Wine-only string (keensolver's usage
            # line was rejected for exactly this reason).
            clean = clean.replace("C:\\recomp-oracle\\", "").replace(
                "C:/recomp-oracle/", "")
            if clean and clean not in out:
                out.append(clean[:200])
            if len(out) >= limit:
                break
        return out

    runner = run.get("runner", "wine-fallback")
    source = ("original PE32 executed via `highball run` (Highball Wine engine; "
              "process-level, not retail Windows)" if runner == "highball" else
              "original PE32 executed via raw WINE FALLBACK (highball unavailable; "
              "process-level, not retail Windows)")
    doc = {"source": source, "runner": runner,
           "sha256": run["sha256"], "wine": run["wine"],
           "stdout_contains": needles(run["stdout"], stdout_lines),
           "stderr_contains": needles(run["stderr"], stderr_lines)}
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
    highball = find_highball()
    if not wine and not highball:
        print("error: neither highball nor a Wine engine is available", file=sys.stderr)
        return 2
    try:
        run = run_original(wine, args.bottle, args.exe, args.arg, args.timeout)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    doc = oracle_from_run(run)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"original {args.exe.name} via {run['runner']}: exit={run['exit_code']} "
          f"stdout={len(run['stdout'])}B stderr={len(run['stderr'])}B")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
