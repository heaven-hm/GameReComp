#!/usr/bin/env python3
"""Machine-readable adapter around ``tools/lift/difftest.py --project``.

The orchestrator needs ONE honest record per batch of function addresses: which
addresses were driven, how many agreed / diverged / faulted on the reference
machine, the exact command that was run, and the hash of the tool's report.

This module does NOT reimplement the differential test. It invokes the existing
tool (``tools/lift/difftest.py``) as a subprocess, consumes the JSON report that
tool writes, and reduces that report to counts. Every claim in the returned
record is derived from a report that was actually produced by the tool run.

Fail-closed rules (a blocked record carries no agreement claim at all):

* missing project directory, missing PE file, or no addresses -> BLOCKED
* Unicorn (the tool's reference machine) not installed -> BLOCKED
* ``difftest.py`` missing, or the runner could not be launched -> BLOCKED
* no report written, non-JSON / structurally invalid report -> BLOCKED
* report bytes changed after the tool wrote them (tampering) -> BLOCKED
* report's ``tool`` tag or address set does not match the run -> BLOCKED

A non-zero tool exit code of 1 is NOT a run failure: difftest.py exits 1 when
any address diverged or faulted, which is an expected, reportable result.

The runner is injectable so the test suite can prove each of these properties
with a fake tool, without needing a real PE or a C compiler.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
DIFTEST = ROOT / "tools" / "lift" / "difftest.py"
EXPECTED_TOOL = "tools/lift/difftest.py --project"

AGREE_PREFIX = "agree"
DIVERGE_PREFIX = "diverge"
ORIGINAL_FAULTED = "original-faulted"

# The tool uses exit 1 to signal "some address did not agree". Anything else
# from a real run means the harness itself did not complete.
EXPECTED_EXIT_CODES = (0, 1)


class AdapterInputError(Exception):
    """Caller supplied something that cannot be tested (bad path/args)."""


class ToolMissing(Exception):
    """The difftest.py tool could not be found or launched."""


@dataclass(frozen=True)
class DiffOutcome:
    """What a runner observed from the tool.

    ``report_bytes`` is the EXACT byte string the tool claims it wrote to the
    report path. The adapter compares it to the bytes it reads back from disk;
    a difference means the report was modified after the tool produced it.
    """

    returncode: int
    stdout: str = ""
    stderr: str = ""
    report_bytes: bytes = b""


Runner = Callable[[Sequence[str], Path], DiffOutcome]


def subprocess_runner(command: Sequence[str], report_path: Path) -> DiffOutcome:
    """Default runner: execute difftest.py and capture the report it wrote."""
    if not DIFTEST.is_file():
        raise ToolMissing(f"difftest tool not found: {DIFTEST}")
    proc = subprocess.run(list(command), capture_output=True, text=True)
    written = report_path.read_bytes() if report_path.is_file() else b""
    return DiffOutcome(proc.returncode, proc.stdout, proc.stderr, written)


def _unicorn_available() -> bool:
    """True only when the tool's reference machine can actually be imported."""
    try:
        return importlib.util.find_spec("unicorn") is not None
    except (ImportError, ValueError):
        return False


def _normalise_address(value) -> str:
    """Render an address exactly as difftest.py renders it in its report."""
    try:
        va = int(str(value), 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise AdapterInputError(f"invalid address: {value!r}") from exc
    if va < 0:
        raise AdapterInputError(f"negative address: {value!r}")
    return f"0x{va:08X}"


def _blocked(reason: str, cls: str, **extra) -> dict:
    rec = {
        "schema": "unicorn_diff_adapter/1",
        "status": "BLOCKED",
        "reason": reason,
        "blocked_class": cls,
        "agreement": None,
        "addresses_tested": [],
        "counts": {"addresses_tested": 0, "agree": 0, "diverge": 0,
                   "original_faulted": 0, "other": 0},
        "verdicts": [],
    }
    rec.update(extra)
    return rec


def build_command(project: Path, pe: Path, addresses: Sequence[str],
                  cases: int, flags: bool, report_path: Path) -> list[str]:
    """The exact argv used to drive difftest.py (recorded verbatim in the result)."""
    command = [sys.executable, str(DIFTEST), "--project", str(project),
               "--pe", str(pe)]
    for address in addresses:
        command += ["--address", address]
    command += ["--cases", str(cases)]
    if flags:
        command.append("--flags")
    command += ["--json", str(report_path)]
    return command


def run_unicorn_diff(project, pe, addresses, cases: int = 8, flags: bool = False,
                     report_path=None, runner: Runner | None = None) -> dict:
    """Drive difftest.py over ``addresses`` and reduce its report to a record.

    ``runner`` is injectable for tests; production uses ``subprocess_runner``.
    The returned dict is always machine-readable (JSON-serialisable).
    """
    runner = runner or subprocess_runner

    # --- input validation: fail closed before launching anything -----------
    try:
        project_path = Path(project)
        pe_path = Path(pe)
    except TypeError:
        return _blocked("project and pe must be paths", "input")
    if not project_path.is_dir():
        return _blocked(f"project directory not found: {project_path}", "input")
    if not pe_path.is_file():
        return _blocked(f"PE file not found: {pe_path}", "input")
    if not addresses:
        return _blocked("no addresses supplied: pass at least one --address", "input")
    try:
        normalised = [_normalise_address(a) for a in addresses]
    except AdapterInputError as exc:
        return _blocked(str(exc), "input")
    try:
        cases = int(cases)
    except (TypeError, ValueError):
        return _blocked(f"invalid cases: {cases!r}", "input")
    if cases < 1:
        return _blocked("cases must be >= 1", "input")
    if report_path is None:
        return _blocked("report path is required (the tool must write a report)", "input")
    report = Path(report_path)
    if not _unicorn_available():
        return _blocked("unicorn (the reference machine) is not installed", "environment")

    command = build_command(project_path, pe_path, normalised, cases,
                            bool(flags), report)

    common = {
        "tool": EXPECTED_TOOL,
        "command": list(command),
        "command_string": shlex.join(command),
        "project": str(project_path),
        "pe": str(pe_path),
        "addresses": list(normalised),
        "cases": cases,
        "flags": bool(flags),
        "report_path": str(report),
    }

    # --- run the tool -------------------------------------------------------
    try:
        outcome = runner(command, report)
    except ToolMissing as exc:
        return _blocked(str(exc), "tool", **common)
    except Exception as exc:  # a runner crash is never an agreement
        return _blocked(f"difftest runner failed: {type(exc).__name__}: {exc}",
                        "tool", **common)
    if outcome.returncode not in EXPECTED_EXIT_CODES:
        return _blocked(f"difftest exited {outcome.returncode} "
                        f"(expected {EXPECTED_EXIT_CODES})", "tool", **common,
                        tool_exit_code=outcome.returncode,
                        tool_stderr=outcome.stderr[:2000])

    # --- consume the report, authenticating it against what the tool wrote --
    if not report.is_file():
        return _blocked("difftest wrote no report", "report", **common,
                        tool_exit_code=outcome.returncode)
    disk_bytes = report.read_bytes()
    if not disk_bytes:
        return _blocked("difftest report is empty", "report", **common,
                        tool_exit_code=outcome.returncode)
    if disk_bytes != outcome.report_bytes:
        return _blocked("tampered report: bytes on disk differ from the report "
                        "the tool wrote", "report", **common,
                        report_sha256=hashlib.sha256(disk_bytes).hexdigest())
    try:
        document = json.loads(disk_bytes)
    except (UnicodeError, ValueError) as exc:
        return _blocked(f"difftest report is not valid JSON: {exc}", "report",
                        **common, report_sha256=hashlib.sha256(disk_bytes).hexdigest())
    if not isinstance(document, dict):
        return _blocked("difftest report is not a JSON object", "report", **common,
                        report_sha256=hashlib.sha256(disk_bytes).hexdigest())
    if document.get("tool") != EXPECTED_TOOL:
        return _blocked(f"difftest report tool tag is {document.get('tool')!r}, "
                        f"expected {EXPECTED_TOOL!r}", "report", **common,
                        report_sha256=hashlib.sha256(disk_bytes).hexdigest())
    rows = document.get("addresses")
    if not isinstance(rows, list) or not rows:
        return _blocked("difftest report has no address rows", "report", **common,
                        report_sha256=hashlib.sha256(disk_bytes).hexdigest())
    reported = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("address"), str) \
                or not isinstance(row.get("verdict"), str):
            return _blocked("difftest report has a malformed address row", "report",
                            **common, report_sha256=hashlib.sha256(disk_bytes).hexdigest())
        reported.append(row)
    if [r["address"] for r in reported] != normalised:
        return _blocked("difftest report addresses do not match the addresses "
                        "requested", "report", **common,
                        report_sha256=hashlib.sha256(disk_bytes).hexdigest())

    # --- reduce to counts (the only place an agreement claim can originate) --
    verdicts = [{"address": r["address"], "verdict": r["verdict"]}
                for r in reported]
    agree = sum(1 for r in reported if r["verdict"].startswith(AGREE_PREFIX))
    diverge = sum(1 for r in reported if r["verdict"].startswith(DIVERGE_PREFIX))
    faulted = sum(1 for r in reported if r["verdict"] == ORIGINAL_FAULTED)
    other = len(reported) - agree - diverge - faulted
    total = len(reported)
    if agree == total:
        agreement = "full"
    elif agree == 0:
        agreement = "none"
    else:
        agreement = "partial"

    return {
        **common,
        "schema": "unicorn_diff_adapter/1",
        "status": "OK",
        "blocked_class": None,
        "reason": None,
        "agreement": agreement,
        "addresses_tested": list(normalised),
        "counts": {
            "addresses_tested": total,
            "agree": agree,
            "diverge": diverge,
            "original_faulted": faulted,
            "other": other,
        },
        "verdicts": verdicts,
        "tool_exit_code": outcome.returncode,
        "report_sha256": hashlib.sha256(disk_bytes).hexdigest(),
        "report_tool": document.get("tool"),
        "tool_stdout_tail": outcome.stdout[-2000:],
        "tool_stderr_tail": outcome.stderr[-2000:],
        "what_it_proves": document.get("what_it_proves"),
        "what_it_does_not_prove": document.get("what_it_does_not_prove"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True, help="generated project directory")
    ap.add_argument("--pe", required=True, help="the original PE the bytes come from")
    ap.add_argument("--address", action="append", default=[],
                    help="a VA to compare (repeatable)")
    ap.add_argument("--cases", type=int, default=8,
                    help="entry states to drive (default 8)")
    ap.add_argument("--flags", action="store_true",
                    help="also compare the arithmetic flags (difftest.py --flags)")
    ap.add_argument("--report", "--json", dest="report", required=True,
                    help="where difftest.py writes its JSON report")
    ap.add_argument("--output", help="write the adapter record here (default: stdout)")
    args = ap.parse_args(argv)

    record = run_unicorn_diff(args.project, args.pe, args.address, args.cases,
                              args.flags, args.report)
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    # 0 = a record was produced; 2 = fail-closed. Diverge/fault are data in the
    # record, not a failure of the adapter itself.
    return 0 if record["status"] == "OK" else 2


if __name__ == "__main__":
    sys.exit(main())
