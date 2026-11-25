#!/usr/bin/env python3
"""Capture deterministic Windows-side oracle evidence via the Highball CLI.

Highball owns the Wine engine, bottle, renderer and sync mode, so this wrapper
invokes exactly ``highball run -b <bottle> <exe> [args...]`` and feeds an
optional stdin script to the child's stdin.  It records the exact command, the
process exit code, raw stdout/stderr (with SHA-256), and a normalized
guest-output block (Highball banners, ANSI codes and Wine chatter removed).

FAIL CLOSED: when Highball is missing, cannot start / reports it cannot run the
guest, or times out, the result is a ``BLOCKED`` record (with a reason), never
an ``OK`` run and never fabricated guest output.  Raw bytes and their hashes
are still written so a blocked attempt is auditable.

DETERMINISM: given the same exe, args, stdin and runner result, the manifest is
byte-for-byte stable (no wall-clock timestamps).  The process runner is an
injectable callable, so tests can supply a fake runner and never invoke Wine.

SCOPE: console scenarios only.  This is a process-level Wine oracle, not retail
Windows, and it does NOT establish gameplay, graphics, audio or input
equivalence for any game.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SCHEMA = 1
DEFAULT_BOTTLE = "Games"
DEFAULT_TIMEOUT = 60
CAPTURE_JSON = "capture.json"
STDOUT_RAW = "stdout.raw"
STDERR_RAW = "stderr.raw"
STDIN_RAW = "stdin.raw"
GUEST_OUTPUT = "guest_output.txt"

LIMITS = ("console scenarios only; process-level Wine oracle via Highball, "
          "not retail Windows and not gameplay/graphics/audio/input equivalence")

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Highball prints these itself (stdout is block-buffered when piped, so they
# may land before OR after the guest output) -- anchored so guest lines survive.
HIGHBALL_BANNER_RE = re.compile(
    r"^Launching .+ in bottle .+\[engine: .+renderer: .+\]\.\.\.$", re.IGNORECASE)
HIGHBALL_ARGS_RE = re.compile(r"^Arguments: .+$", re.IGNORECASE)
HIGHBALL_EXIT_RE = re.compile(r"^exit=\d+ after \d+s . log: .+$", re.IGNORECASE)
WINE_NOISE_RE = re.compile(
    r"freetype|or equal to 2\.0\.5|msync:|wineboot:|import_dll|ZwLoadDriver|"
    r"failed to open|wine:|err:", re.IGNORECASE)
HIGHBALL_UNAVAILABLE_RE = re.compile(
    r"^error: (?:no bottles found|bottle .+ not found|wine binary not found|"
    r"no program specified)", re.IGNORECASE | re.MULTILINE)


class HighballUnavailable(RuntimeError):
    """The Highball binary could not be started."""


@dataclass
class RunOutcome:
    """Binary-safe result of one guest run (also what fake runners return)."""

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_highball() -> str | None:
    """Resolve the Highball CLI. ``RECOMP_HIGHBALL`` overrides PATH lookup."""
    override = os.environ.get("RECOMP_HIGHBALL")
    if override:
        return override
    return shutil.which("highball")


def normalize_guest_output(stdout_text: str, stderr_text: str) -> str:
    """Strip runner chatter, ANSI and Wine noise; keep real guest lines.

    stdout lines are emitted first, then stderr lines, so the result is
    deterministic even though the two pipes are captured separately.
    """
    def clean(text: str) -> list[str]:
        kept: list[str] = []
        for line in text.splitlines():
            if (HIGHBALL_BANNER_RE.match(line) or HIGHBALL_ARGS_RE.match(line)
                    or HIGHBALL_EXIT_RE.match(line)):
                continue
            if WINE_NOISE_RE.search(line):
                continue
            line = ANSI_RE.sub("", line).rstrip()
            if not line.strip():
                continue
            kept.append(line)
        return kept

    lines = clean(stdout_text) + clean(stderr_text)
    return "\n".join(lines) + "\n" if lines else ""


def run_highball(command: list[str], *, stdin: bytes, timeout: int,
                 cwd: str) -> RunOutcome:
    """Real process runner. Kills the whole process group on timeout.

    Wine spawns helper processes that inherit the pipe handles, so a bare
    ``communicate(timeout=...)`` can block after wine itself was killed.  The
    group kill plus bounded drain keeps a timeout honest and bounded.
    """
    try:
        proc = subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        raise HighballUnavailable(f"cannot start highball: {exc}") from exc

    try:
        out, err = proc.communicate(input=stdin, timeout=timeout)
        return RunOutcome(exit_code=proc.returncode, stdout=out or b"",
                          stderr=err or b"", timed_out=False)
    except subprocess.TimeoutExpired as exc:
        out, err = exc.output or b"", exc.stderr or b""
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            out2, err2 = proc.communicate(timeout=10)
            out, err = out or out2, err or err2
        except subprocess.TimeoutExpired:
            pass  # keep bounded partial output; it is not claimed as a result
        return RunOutcome(exit_code=None, stdout=out, stderr=err, timed_out=True)


def _write_record(out_dir: Path, rec: dict, stdout_b: bytes, stderr_b: bytes,
                  stdin_b: bytes) -> dict:
    """Persist raw bytes + manifest and attach hashes/normalized guest output."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / STDOUT_RAW).write_bytes(stdout_b)
    (out_dir / STDERR_RAW).write_bytes(stderr_b)
    (out_dir / STDIN_RAW).write_bytes(stdin_b)
    rec["raw"] = {
        "stdout": {"file": STDOUT_RAW, "bytes": len(stdout_b),
                   "sha256": _sha(stdout_b)},
        "stderr": {"file": STDERR_RAW, "bytes": len(stderr_b),
                   "sha256": _sha(stderr_b)},
        "stdin": {"file": STDIN_RAW, "bytes": len(stdin_b),
                  "sha256": _sha(stdin_b)},
    }
    block = normalize_guest_output(
        stdout_b.decode("utf-8", "replace"),
        stderr_b.decode("utf-8", "replace"))
    (out_dir / GUEST_OUTPUT).write_text(block, encoding="utf-8")
    rec["guest_output"] = block
    rec["guest_output_sha256"] = _sha(block.encode("utf-8"))
    (out_dir / CAPTURE_JSON).write_text(
        json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return rec


def capture(exe: Path | str, *, args=(), stdin: bytes = b"", out_dir: Path | str,
            timeout: int = DEFAULT_TIMEOUT, bottle: str = DEFAULT_BOTTLE,
            highball: str | None = None, runner=None, cwd: str | None = None) -> dict:
    """Capture one Highball oracle run into ``out_dir``.

    ``runner`` is injectable: it must be a callable
    ``(command, *, stdin, timeout, cwd) -> RunOutcome``.  When omitted, the real
    subprocess runner is used.  The returned manifest is JSON-serialisable.
    """
    if runner is None:
        runner = run_highball
    out_dir = Path(out_dir)
    exe = Path(exe)
    stdin_b = bytes(stdin)
    if highball is None:
        highball = find_highball()

    work_dir = cwd or (str(exe.parent) if exe.is_absolute() else str(Path.cwd()))
    rec: dict = {
        "schema": SCHEMA,
        "tool": "wine_oracle_capture",
        "status": "OK",
        "blocked_class": None,
        "reason": None,
        "runner": "highball",
        "highball": highball,
        "bottle": str(bottle),
        "exe": str(exe),
        "exe_sha256": _sha(exe.read_bytes()) if exe.is_file() else None,
        "args": [str(a) for a in args],
        "timeout": timeout,
        "cwd": work_dir,
        "limits": LIMITS,
    }
    if highball:
        command = [str(highball), "run", "-b", str(bottle), str(exe),
                   *[str(a) for a in args]]
        rec["command"] = command
        rec["command_display"] = shlex.join(command)
    else:
        rec["command"] = None
        rec["command_display"] = None

    # Fail closed, in priority order: bad input -> missing -> cannot run -> timeout.
    if not exe.is_file():
        rec.update(status="BLOCKED", blocked_class="invalid-input",
                   reason=f"executable not found: {exe}")
        return _write_record(out_dir, rec, b"", b"", stdin_b)
    if not highball:
        rec.update(status="BLOCKED", blocked_class="highball-missing",
                   reason="highball CLI not found (set RECOMP_HIGHBALL or --highball)")
        return _write_record(out_dir, rec, b"", b"", stdin_b)

    try:
        outcome = runner(rec["command"], stdin=stdin_b, timeout=timeout,
                         cwd=work_dir)
    except HighballUnavailable as exc:
        rec.update(status="BLOCKED", blocked_class="highball-unavailable",
                   reason=str(exc))
        return _write_record(out_dir, rec, b"", b"", stdin_b)
    except OSError as exc:
        rec.update(status="BLOCKED", blocked_class="highball-unavailable",
                   reason=f"highball invocation failed: {exc}")
        return _write_record(out_dir, rec, b"", b"", stdin_b)

    rec["exit_code"] = outcome.exit_code
    rec["timed_out"] = outcome.timed_out
    if outcome.timed_out:
        rec.update(status="BLOCKED", blocked_class="timeout",
                   reason=(f"highball produced no result within {timeout}s; "
                           "partial output not claimed"))
    else:
        combined = (outcome.stdout.decode("utf-8", "replace") + "\n"
                    + outcome.stderr.decode("utf-8", "replace"))
        if HIGHBALL_UNAVAILABLE_RE.search(combined):
            rec.update(status="BLOCKED", blocked_class="highball-unavailable",
                       reason="highball reported it could not run the guest")
    return _write_record(out_dir, rec, outcome.stdout, outcome.stderr, stdin_b)


def verify_capture(out_dir: Path | str) -> dict:
    """Recompute hashes from the stored raw files and detect any tampering."""
    out_dir = Path(out_dir)
    manifest = out_dir / CAPTURE_JSON
    if not manifest.is_file():
        return {"ok": False, "problems": [f"missing {CAPTURE_JSON}"]}
    try:
        rec = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"ok": False, "problems": [f"unreadable manifest: {exc}"]}

    problems: list[str] = []
    for key in ("stdout", "stderr", "stdin"):
        meta = rec.get("raw", {}).get(key, {})
        path = out_dir / meta.get("file", f"{key}.raw")
        if not path.is_file():
            problems.append(f"missing raw file for {key}")
            continue
        data = path.read_bytes()
        if len(data) != meta.get("bytes"):
            problems.append(f"{key} byte-count mismatch")
        if _sha(data) != meta.get("sha256"):
            problems.append(f"{key} sha256 mismatch")
    if _sha(rec.get("guest_output", "").encode("utf-8")) != \
            rec.get("guest_output_sha256"):
        problems.append("guest_output sha256 mismatch")
    return {"ok": not problems, "problems": problems,
            "status": rec.get("status")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, help="Windows executable to run under Highball")
    ap.add_argument("--arg", action="append", default=[],
                    help="argument to the guest (repeatable)")
    ap.add_argument("--stdin", "--stdin-file", dest="stdin_file", type=Path,
                    help="file whose bytes feed guest stdin (the input script)")
    ap.add_argument("--stdin-text", help="inline text fed to guest stdin")
    ap.add_argument("--out", type=Path, help="output directory for evidence files")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help=f"run timeout in seconds (default {DEFAULT_TIMEOUT})")
    ap.add_argument("--bottle", default=DEFAULT_BOTTLE,
                    help=f"Highball bottle (default {DEFAULT_BOTTLE})")
    ap.add_argument("--highball", help="override path to the highball CLI")
    ap.add_argument("--verify", metavar="DIR", type=Path,
                    help="verify an existing capture directory instead of running")
    args = ap.parse_args(argv)

    if args.verify is not None:
        result = verify_capture(args.verify)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if args.exe is None or args.out is None:
        ap.error("--exe and --out are required unless --verify is given")
    if args.stdin_file is not None and args.stdin_text is not None:
        ap.error("--stdin-file and --stdin-text are mutually exclusive")

    if args.stdin_file is not None:
        stdin_b = args.stdin_file.read_bytes()
    elif args.stdin_text is not None:
        stdin_b = args.stdin_text.encode("utf-8")
    else:
        stdin_b = b""

    rec = capture(args.exe, args=args.arg, stdin=stdin_b, out_dir=args.out,
                  timeout=args.timeout, bottle=args.bottle,
                  highball=args.highball)
    summary = {k: rec.get(k) for k in (
        "status", "blocked_class", "reason", "command_display", "exit_code",
        "timeout")}
    print(json.dumps(summary, indent=2))
    print(f"evidence: {Path(args.out) / CAPTURE_JSON}")
    return 0 if rec["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
