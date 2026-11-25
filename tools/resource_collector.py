#!/usr/bin/env python3
"""Measure the peak RSS and wall time of a caller-supplied command (#26).

This is a *collector*, not a reporter and not a speed claim. It runs the exact
command given on the command line and records two readings taken around it:

* ``wall_seconds``   -- ``time.perf_counter`` delta (always measurable).
* ``peak_rss_bytes`` -- the variance of
  ``resource.getrusage(RUSAGE_CHILDREN).ru_maxrss`` between a snapshot taken
  before the command and one taken after it.

``ru_maxrss`` is a high-water mark over *all* reaped children of this process,
so the variance is a lower bound on the child's own peak when some earlier
child already set a higher mark. The method is recorded in the output; nothing
is inferred, estimated or invented.

No path is built in: the command, the output path and the optional timeout all
come from the caller. If ``resource`` is unavailable (non-POSIX), or a reading
is absent or malformed, the corresponding field is ``None`` and the record is
``NOT TESTED`` / ``INVALID`` -- never zero, "n/a" or a plausible-looking guess.
Negative, boolean and non-finite (NaN/inf) numbers are explicitly rejected as
tampering.

Usage::

    python3 -m tools.resource_collector --out <result.json> -- <cmd> [args...]
    python3 -m tools.resource_collector --verify <result.json> [--strict]
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import resource as _resource
except ImportError:  # pragma: no cover - non-POSIX hosts
    _resource = None

SCHEMA_VERSION = 1
GENERATOR = "tools/resource_collector.py"

MEASURED = "MEASURED"
NOT_TESTED = "NOT TESTED"
INVALID = "INVALID"

METHOD = (
    "wall=time.perf_counter delta; "
    "peak_rss_bytes=variance (after-before) of "
    "resource.getrusage(RUSAGE_CHILDREN).ru_maxrss "
    "(darwin: bytes, other POSIX: KiB scaled to bytes); "
    "high-water variance is a lower bound on one child's peak"
)

_USE_DEFAULT = object()


def _valid_number(value: Any) -> bool:
    """True only for a real, finite, non-negative int/float (bool is not one)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    if isinstance(value, float):
        return math.isfinite(value) and value >= 0.0
    return False


def _valid_bytes(value: Any) -> bool:
    """Byte counts and rusage snapshots must be exact non-negative integers."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _rss_scale() -> int:
    """macOS reports ru_maxrss in bytes; Linux/other POSIX in KiB."""
    return 1 if sys.platform == "darwin" else 1024


def _read_maxrss(module: Any, who: int, label: str) -> tuple[int | None, str | None]:
    """Read one ru_maxrss snapshot as bytes.

    Returns (bytes, None) on success, (None, None) when the reading is absent,
    and (None, finding) when a present value is malformed (tampering).
    """
    try:
        raw = module.getrusage(who).ru_maxrss
    except (AttributeError, OSError, TypeError, ValueError):
        return None, None
    if not _valid_number(raw):
        return None, f"{label} ru_maxrss {raw!r} is not a finite non-negative number"
    return int(raw) * _rss_scale(), None


def _base_record(command: list[str]) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "status": NOT_TESTED,
        "method": METHOD,
        "platform": sys.platform,
        "python": platform.python_version(),
        "command": list(command),
        "returncode": "MISSING_INPUT",
        "wall_seconds": None,
        "peak_rss_bytes": None,
        "rusage_before_bytes": None,
        "rusage_after_bytes": None,
        "findings": [],
    }


def collect(command: list[str], *, timeout: int | None = None,
            resource_module: Any = _USE_DEFAULT) -> dict:
    """Run *command* and return a resource-evidence record.

    The caller supplies the command and optional timeout; no path or command
    is hard-coded. ``resource_module`` is injectable for tests. The returned
    dict is deterministic in structure and serialisation (no timestamps); the
    numbers themselves are the readings taken during this one run.
    """
    command = [str(part) for part in command]
    record = _base_record(command)
    module = _resource if resource_module is _USE_DEFAULT else resource_module

    if module is None or not hasattr(module, "RUSAGE_CHILDREN"):
        record["findings"].append(
            "resource.getrusage(RUSAGE_CHILDREN) unavailable on this platform")
        return record
    if not command:
        record["findings"].append("no command supplied")
        return record

    who = module.RUSAGE_CHILDREN
    before, finding = _read_maxrss(module, who, "before")
    if finding is not None:
        record["status"] = INVALID
        record["findings"].append(finding)
        return record
    if before is None:
        record["findings"].append("before RUSAGE_CHILDREN snapshot unavailable")
        return record
    record["rusage_before_bytes"] = before

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              errors="replace", timeout=timeout, check=False)
        rc, timed_out = proc.returncode, False
    except subprocess.TimeoutExpired:
        rc, timed_out = "TIMEOUT", True
    except OSError as exc:
        record["returncode"] = "COMMAND_FAILED"
        record["findings"].append(f"command could not start: {type(exc).__name__}")
        return record
    wall = round(time.perf_counter() - t0, 4)
    record["returncode"] = "TIMEOUT" if timed_out else rc

    after, finding = _read_maxrss(module, who, "after")
    if finding is not None:
        record["status"] = INVALID
        record["findings"].append(finding)
        return record
    if after is None:
        record["findings"].append("after RUSAGE_CHILDREN snapshot unavailable")
        return record
    record["rusage_after_bytes"] = after

    if not _valid_number(wall):
        record["status"] = INVALID
        record["findings"].append(f"wall_seconds {wall!r} is not a finite non-negative number")
        return record
    if after < before:
        record["status"] = INVALID
        record["findings"].append(
            f"after ru_maxrss ({after}) is below before ({before}); variance is negative")
        return record

    record["wall_seconds"] = wall
    record["peak_rss_bytes"] = after - before
    record["status"] = MEASURED
    return record


def validate_record(record: dict) -> dict:
    """Re-check a record's numbers; return a corrected copy, never mutate input.

    Used by the collector itself (via the fields it produced) and by ``--verify``
    on a JSON file supplied by the caller. A present negative, boolean or
    non-finite value, or a high-water reversal, is flagged ``INVALID`` and its
    value is removed so it can never be shown as a clean measurement.
    """
    if not isinstance(record, dict):
        raise ValueError("expected a JSON object")
    out = json.loads(json.dumps(record))
    findings = list(out.get("findings") or [])
    problems: list[str] = []

    if out.get("status") == MEASURED and (
            out.get("wall_seconds") is None or out.get("peak_rss_bytes") is None):
        problems.append("status MEASURED but a metric is missing")

    for key in ("peak_rss_bytes", "rusage_before_bytes", "rusage_after_bytes"):
        value = out.get(key)
        if value is not None and not _valid_bytes(value):
            problems.append(f"{key} {value!r} is not a non-negative integer")
            out[key] = None

    wall = out.get("wall_seconds")
    if wall is not None and not _valid_number(wall):
        problems.append(f"wall_seconds {wall!r} is not a finite non-negative number")
        out["wall_seconds"] = None

    before, after = out.get("rusage_before_bytes"), out.get("rusage_after_bytes")
    if before is not None and after is not None and after < before:
        problems.append(f"rusage_after_bytes ({after}) below rusage_before_bytes ({before})")

    # Only newly found numeric problems change the verdict: a NOT TESTED
    # record's reason finding is honest evidence, not tampering.
    if problems:
        out["status"] = INVALID
    out["findings"] = findings + problems
    return out


def dumps_json(record: dict) -> str:
    """Canonical JSON: sorted keys, fixed indentation, trailing newline."""
    return json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, help="write the JSON record here")
    ap.add_argument("--timeout", type=int,
                    help="optional positive timeout in seconds for the command")
    ap.add_argument("--verify", type=Path,
                    help="re-validate an existing record instead of running a command")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when the record is INVALID")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)

    if args.verify is not None:
        try:
            raw = json.loads(args.verify.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"resource_collector: {exc}", file=sys.stderr)
            return 2
        try:
            record = validate_record(raw)
        except (TypeError, ValueError) as exc:
            print(f"resource_collector: {exc}", file=sys.stderr)
            return 2
    else:
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            print("resource_collector: no command supplied (use -- <cmd> [args...])",
                  file=sys.stderr)
            return 2
        if args.timeout is not None and args.timeout <= 0:
            print("resource_collector: --timeout must be positive", file=sys.stderr)
            return 2
        record = validate_record(collect(command, timeout=args.timeout))

    text = dumps_json(record)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 1 if args.strict and record["status"] == INVALID else 0


if __name__ == "__main__":
    raise SystemExit(main())
