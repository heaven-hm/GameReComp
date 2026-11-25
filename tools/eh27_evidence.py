#!/usr/bin/env python3
"""Join issue #27 EH evidence and fail closed on every missing or altered fact.

This tool joins three *CLI-supplied* evidence kinds and never runs a game, a
native, Frida or Unicorn itself:

1. ``--eh-selftest``: EH selftest results (the JSON record emitted by
   ``tools.selftest_evidence_collector`` or a raw ``build_selftests.sh`` log).
   It must show the DMD throw/catch fixtures passing.
2. ``--dmd-report``: a DMD object validation report (JSON). It must validate the
   throw object's vptr/ClassInfo/base chain and a resumed guest catch.
3. ``--run`` plus ``--run-log``/``--frame``: a real game run record, the actual
   log bytes and the actual frame bytes. Every hash in the record is recomputed
   from those bytes; every fact is re-derived.

Verdicts (with exact reason codes in the output):

* ``RESOLVED`` -- a caught guest DMD exception that resumed execution **and**
  at least one proven non-blank frame whose SHA-256 was recomputed from the
  supplied bytes, with clean EH selftests and a valid DMD object report.
* ``PARTIAL`` -- positive but incomplete evidence: clean EH selftests with no
  real game run, or a verified run that caught and resumed but supplied no
  provable frame. Selftests alone are never RESOLVED.
* ``BLOCKED`` -- no usable evidence at all ("no run and no selftests"), a
  tampered/contradictory record, failed selftests/checks, a run that did not
  catch or did not resume, or a blank frame. There is no FAIL or PASS verdict
  here: a negative result is reported as BLOCKED with the exact reason.

Nothing in any input is promoted to a verdict by being *claimed*. Top-level
``verdict``/``pass``/``promotion`` claims are treated as tampering. Output is
deterministic: sorted keys, sorted reasons, no timestamps, no environment.

Usage::

    python3 -m tools.eh27_evidence \
        --eh-selftest <record.json|build-selftests.log> \
        [--eh-selftest-log <original.log>] \
        --dmd-report <report.json> \
        --run <run.json> --run-log <run.log> --frame <image> ... \
        [--out <verdict.json>]

Input schemas (all hashes are 64 lowercase/uppercase hex)::

    --eh-selftest: {"schema_version": 1,
                    "events": [{"name": "<name>_selftest.c", "status": "ok"}, ...],
                    "counts": {"ok": N, "FAIL": 0, "BUILD FAIL": 0,
                               "skip": 0, "total": N},
                    "source": {"sha256": "<log sha256>"}}   # optional
      or a raw build_selftests.sh log ("ok  <name>_selftest.c" etc.).
      The four required fixtures are dmd_throw_selftest.c,
      dmd_throw_object_selftest.c, eh_validation_selftest.c and
      win32hle_selftest.c; each must be "ok".
    --dmd-report: {"schema_version": 1, "exception_code": "0xE0440001",
                   "target": {"name": "gr.exe", "sha256": "<exe sha256>"},
                   "checks": [{"id": "<check>", "status": "PASS"}, ...],
                   "dispatch": {"caught": true, "resumed_guest": true,
                                "catch_va": "0x...", "reason": "..."}}
      Six checks are required: object_vptr_nonnull, classinfo_nonnull,
      classinfo_readable, base_chain_acyclic, catch_esp_in_guest_stack,
      wellformed_throw_dispatches.
    --run: {"schema_version": 1, "game": "Gunroar",
            "exe": {"name": "gr.exe", "sha256": "<exe sha256>"},
            "log_sha256": "<sha256 of the --run-log bytes>",
            "log_events": [{"code": "DMD_EXCEPTION_CAUGHT", "line": 2},
                           {"code": "GUEST_EXECUTION_RESUMED", "line": 3}],
            "frames": [{"path": "frame-0000.bmp", "sha256": "<frame sha256>"}]}

Exit status: 0 only for RESOLVED; 1 for PARTIAL or BLOCKED; 2 for a CLI error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:  # `python3 -m tools.eh27_evidence` and `python3 tools/eh27_evidence.py`
    from .frame_evidence_check import frame_info as _frame_info
except ImportError:  # pragma: no cover - exercised only by direct script use
    try:
        from frame_evidence_check import frame_info as _frame_info
    except ImportError:  # pragma: no cover
        _frame_info = None

SCHEMA_VERSION = 1
TOOL = "tools/eh27_evidence.py"
ISSUE = 27
DMD_CODE = "0xE0440001"

# Each of these fixtures must pass: they jointly show a DMD throw reaching a
# guest catch (also through a checkpoint-less tail-call frame) and refusing a
# malformed throw object instead of resuming into a null vtable.
EH_REQUIRED = (
    "dmd_throw_selftest.c",
    "dmd_throw_object_selftest.c",
    "eh_validation_selftest.c",
    "win32hle_selftest.c",
)
DMD_REQUIRED_CHECKS = (
    "object_vptr_nonnull",
    "classinfo_nonnull",
    "classinfo_readable",
    "base_chain_acyclic",
    "catch_esp_in_guest_stack",
    "wellformed_throw_dispatches",
)
EH_STATUSES = ("ok", "FAIL", "BUILD FAIL", "skip")
EH_COUNT_KEYS = EH_STATUSES + ("total",)
EH_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+_selftest\.c$")
EH_LINE_RE = re.compile(
    r"^(ok|FAIL|BUILD FAIL|skip)[ \t]+([A-Za-z0-9_.-]+_selftest\.c)"
    r"(?:[ \t]+\(([^()\r\n]*)\))?[ \t]*$")

# Canonical run-log markers. A positive fact is only set when the marker is
# present in the actual log bytes; a negative marker is always reported too.
DMD_CAUGHT_RE = re.compile(
    r"\b(DMD_EXCEPTION_CAUGHT|dispatched to guest catch)\b", re.IGNORECASE)
RESUMED_RE = re.compile(
    r"\b(GUEST_EXECUTION_RESUMED|resumed (?:at|to|into) (?:the |a )?"
    r"(?:guest )?catch)\b", re.IGNORECASE)
NEGATIVE_RE = re.compile(
    r"\b(DMD_THROW_NOT_DISPATCHED|EXCEPTION_NOT_CAUGHT|UNHANDLED_"
    r"EXCEPTION|FATAL_SIGNAL|SIGBUS|SIGSEGV)\b"
    r"|DMD throw NOT dispatched|no guest catch was resumed"
    r"|guest exceptions are not handled", re.IGNORECASE)
HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# Findings that mean an input contradicts itself or the supplied bytes. They
# force BLOCKED even if every other fact would otherwise resolve the issue.
TAMPER_CODES = frozenset({
    "EH_COUNT_MISMATCH", "FAKE_PASS_CLAIM", "EH_SELFTEST_LOG_HASH_MISMATCH",
    "DMD_COUNT_MISMATCH", "DMD_EXE_HASH_INVALID", "RUN_EXE_HASH_INVALID",
    "RUN_LOG_HASH_INVALID", "FRAME_CLAIM_INVALID", "EXE_HASH_MISMATCH",
    "RUN_LOG_HASH_MISMATCH", "LOG_EVENT_MISMATCH", "LOG_EVENT_LINE_MISMATCH",
    "FRAME_HASH_MISMATCH", "FRAME_NAME_AMBIGUOUS",
})
# An explicit failure in a supplied input is stronger than "partial": it must
# never be relabelled as a merely incomplete positive result.
NEGATIVE_CODES = frozenset({
    "EH_SELFTEST_FAILED", "EH_SELFTEST_SKIPPED", "DMD_CHECK_FAILED",
    "DMD_DISPATCH_NOT_CAUGHT", "DMD_DISPATCH_NOT_RESUMED",
    "RUN_NEGATIVE_EVIDENCE", "FRAME_BLANK", "FRAME_UNDECODABLE",
})
# A selftest record may carry a source-log hash without the log itself being
# supplied. That is an honest, non-blocking limitation: the results are still
# usable as selftest evidence, and a supplied log is always cross-checked.
NONBLOCKING_CODES = frozenset({"EH_SELFTEST_LOG_MISSING"})


def blocking_findings(findings: list[dict]) -> list[dict]:
    return [item for item in findings if item["code"] not in NONBLOCKING_CODES]

LIMITS = (
    "RESOLVED means issue #27's synthetic EH fixtures passed, a DMD object "
    "report validated the throw metadata, and a real run log plus frame bytes "
    "show a caught guest exception, resumed execution and a non-blank frame "
    "with a recomputed SHA-256. It is not a Windows execution oracle.",
    "Selftests alone are PARTIAL; no run and no selftests is BLOCKED. A "
    "claimed PASS/RESOLVED/promotion in an input is tampering, never evidence.",
    "All hashes are recomputed from CLI-supplied bytes. Missing, mismatched, "
    "blank or undecodable evidence is never copied from a claim.",
    "No game, runtime, Frida or Unicorn is executed here; no clock, host path, "
    "environment or network value enters the verdict.",
)


def reason(code: str, detail: str) -> dict:
    return {"code": code, "detail": detail}


def dedupe(reasons: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for item in reasons:
        key = (item["code"], item["detail"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return sorted(out, key=lambda item: (item["code"], item["detail"]))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_hex64(value: Any) -> bool:
    return isinstance(value, str) and HEX64_RE.fullmatch(value) is not None


def canonical_json(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True,
                      allow_nan=False) + "\n"


def load_input(path: Path, code: str, *, allow_text: bool = False
               ) -> tuple[Any, list[dict]]:
    """Read one CLI input, returning (object-or-text, findings)."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, [reason(f"{code}_UNREADABLE", f"{path}: {exc}")]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, [reason(f"{code}_INVALID", f"{path}: not UTF-8: {exc}")]

    def reject_constant(value: str) -> None:
        raise ValueError(f"{value} is not valid JSON evidence")

    try:
        return json.loads(text, parse_constant=reject_constant), []
    except ValueError as exc:
        if allow_text:
            return ("__TEXT__", text), []
        return None, [reason(f"{code}_INVALID", f"{path}: {exc}")]


def fake_pass_findings(obj: dict, code: str) -> list[dict]:
    """Refuse any input that declares its own success verdict."""
    findings: list[dict] = []
    for key in ("verdict", "game_verdict", "resolved", "pass", "promotion",
                "promoted", "game_pass"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip().upper() in {
                "PASS", "RESOLVED", "TRUE", "YES"}:
            findings.append(reason(
                "FAKE_PASS_CLAIM",
                f"{code} claims {key}={value!r}; only this tool emits a verdict"))
        elif value is True:
            findings.append(reason(
                "FAKE_PASS_CLAIM",
                f"{code} claims {key}=true; only this tool emits a verdict"))
    return findings


# ---------------------------------------------------------------------------
# EH selftest evidence
# ---------------------------------------------------------------------------
def parse_selftest_log(text: str) -> tuple[list[dict], list[dict]]:
    """Count only build_selftests.sh summary lines; ignore per-check 'ok'."""
    findings: list[dict] = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return [], [reason("EH_SELFTEST_LOG_EMPTY", "EH selftest log is empty")]
    events: list[dict] = []
    seen: set[str] = set()
    for lineno, line in enumerate(text.split("\n"), 1):
        match = EH_LINE_RE.fullmatch(line.strip())
        if match is None:
            continue
        status, name, why = match.groups()
        if name in seen:
            findings.append(reason(
                "EH_SELFTEST_DUPLICATE",
                f"line {lineno}: duplicate {name}; log is contradictory"))
            continue
        seen.add(name)
        if status == "skip" and (why is None or not why.strip()):
            findings.append(reason(
                "EH_SELFTEST_SKIPPED", f"line {lineno}: skip of {name} has no reason"))
        events.append({"name": name, "status": status,
                       "reason": why.strip() if why else None, "line": lineno})
    if not events:
        findings.append(reason(
            "EH_SELFTEST_LOG_NO_EVENTS",
            "no ok/FAIL/BUILD FAIL/skip selftest summary lines in log"))
    return events, findings


def count_events(events: list[dict]) -> dict[str, int]:
    counts = {status: 0 for status in EH_STATUSES}
    for event in events:
        if event.get("status") in counts:
            counts[event["status"]] += 1
    counts["total"] = len(events)
    return counts


def parse_eh_selftest(obj: Any, log_bytes: bytes | None
                      ) -> tuple[dict, list[dict]]:
    facts: dict[str, Any] = {"available": False, "clean": False,
                            "events": [], "counts": {}}
    findings: list[dict] = []
    if obj is None:
        return facts, findings
    facts["available"] = True
    events: list[dict] = []
    counts: dict[str, int] = {}

    if (isinstance(obj, tuple) and len(obj) == 2 and obj[0] == "__TEXT__"):
        events, log_findings = parse_selftest_log(obj[1])
        findings.extend(log_findings)
    elif isinstance(obj, dict):
        findings.extend(fake_pass_findings(obj, "EH selftest record"))
        raw_events = obj.get("events")
        if not isinstance(raw_events, list) or not raw_events:
            findings.append(reason(
                "EH_SELFTEST_NO_EVENTS", "EH selftest record has no events"))
            raw_events = []
        events: list[dict] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_events):
            if not isinstance(raw, dict):
                findings.append(reason(
                    "EH_SELFTEST_EVENT_INVALID", f"events[{index}] is not an object"))
                continue
            name, status = raw.get("name"), raw.get("status")
            if not isinstance(name, str) or EH_NAME_RE.fullmatch(name) is None:
                findings.append(reason(
                    "EH_SELFTEST_EVENT_INVALID",
                    f"events[{index}] name {name!r} is not a *_selftest.c file"))
                continue
            if name in seen:
                findings.append(reason(
                    "EH_SELFTEST_DUPLICATE", f"events[{index}] repeats {name}"))
                continue
            if status not in EH_STATUSES:
                findings.append(reason(
                    "EH_SELFTEST_EVENT_INVALID",
                    f"events[{index}] status {status!r} is not an EH outcome"))
                continue
            seen.add(name)
            why = raw.get("reason")
            if status == "skip" and (not isinstance(why, str) or not why.strip()):
                findings.append(reason(
                    "EH_SELFTEST_SKIPPED", f"{name} skipped without a reason"))
            events.append({"name": name, "status": status,
                           "reason": why.strip() if isinstance(why, str) else None,
                           "line": raw.get("line")})
        counts = count_events(events)
        supplied = obj.get("counts")
        if supplied is not None:
            if not isinstance(supplied, dict):
                findings.append(reason(
                    "EH_COUNT_MISMATCH", "EH selftest counts is not an object"))
            else:
                for key in EH_COUNT_KEYS:
                    if key in supplied and supplied[key] != counts[key]:
                        findings.append(reason(
                            "EH_COUNT_MISMATCH",
                            f"counts[{key!r}]={supplied[key]!r} disagrees with "
                            f"the supplied events ({counts[key]})"))
        source = obj.get("source")
        if isinstance(source, dict):
            claim = source.get("sha256")
            if claim is not None:
                if not is_hex64(claim):
                    findings.append(reason(
                        "EH_SELFTEST_LOG_HASH_INVALID",
                        "EH selftest source.sha256 is not a 64-hex digest"))
                elif log_bytes is None:
                    findings.append(reason(
                        "EH_SELFTEST_LOG_MISSING",
                        "EH selftest record claims a log hash but --eh-selftest-log "
                        "was not supplied"))
                elif claim.lower() != sha256_bytes(log_bytes):
                    findings.append(reason(
                        "EH_SELFTEST_LOG_HASH_MISMATCH",
                        "EH selftest source.sha256 does not match the supplied "
                        "log bytes (tampered)"))
    else:
        findings.append(reason(
            "EH_SELFTEST_INVALID", "EH selftest input is not a JSON object or log"))

    statuses = {event["name"]: event["status"] for event in events}
    for name in EH_REQUIRED:
        status = statuses.get(name)
        if status is None:
            findings.append(reason(
                "EH_SELFTEST_MISSING", f"required EH fixture {name} was not supplied"))
        elif status != "ok":
            findings.append(reason(
                "EH_SELFTEST_FAILED" if status != "skip" else "EH_SELFTEST_SKIPPED",
                f"required EH fixture {name} has status {status}"))
    for event in events:
        if event["status"] in ("FAIL", "BUILD FAIL"):
            findings.append(reason(
                "EH_SELFTEST_FAILED",
                f"{event['name']} reported {event['status']}"))

    facts["events"] = sorted(events, key=lambda event: event["name"])
    facts["counts"] = counts if isinstance(obj, dict) else count_events(events)
    facts["clean"] = not blocking_findings(findings) and all(
        statuses.get(name) == "ok" for name in EH_REQUIRED)
    return facts, findings


# ---------------------------------------------------------------------------
# DMD object validation report
# ---------------------------------------------------------------------------
def parse_dmd_report(obj: Any) -> tuple[dict, list[dict]]:
    facts: dict[str, Any] = {"available": False, "valid": False,
                            "exception_code": None, "exe_sha256": None,
                            "checks": [], "dispatch": {}}
    findings: list[dict] = []
    if obj is None:
        return facts, findings
    facts["available"] = True
    if not isinstance(obj, dict):
        return facts, [reason("DMD_REPORT_INVALID", "DMD report is not a JSON object")]
    findings.extend(fake_pass_findings(obj, "DMD object report"))

    code = obj.get("exception_code")
    facts["exception_code"] = code if isinstance(code, str) else None
    if not isinstance(code, str) or code.lower() != DMD_CODE.lower():
        findings.append(reason(
            "DMD_EXCEPTION_CODE_MISMATCH",
            f"exception_code {code!r} is not {DMD_CODE}"))

    target = obj.get("target")
    if not isinstance(target, dict) or not is_hex64(target.get("sha256")):
        findings.append(reason(
            "DMD_EXE_HASH_INVALID",
            "DMD report target.sha256 is missing or not a 64-hex digest"))
    else:
        facts["exe_sha256"] = target["sha256"].lower()

    raw_checks = obj.get("checks")
    checks: list[dict] = []
    if not isinstance(raw_checks, list):
        findings.append(reason("DMD_CHECKS_INVALID", "DMD report checks is not a list"))
        raw_checks = []
    by_id: dict[str, str] = {}
    for index, raw in enumerate(raw_checks):
        if not isinstance(raw, dict):
            findings.append(reason(
                "DMD_CHECKS_INVALID", f"checks[{index}] is not an object"))
            continue
        ident, status = raw.get("id"), raw.get("status")
        if not isinstance(ident, str) or not isinstance(status, str):
            findings.append(reason(
                "DMD_CHECKS_INVALID", f"checks[{index}] needs string id and status"))
            continue
        if ident in by_id:
            findings.append(reason(
                "DMD_CHECK_DUPLICATE", f"check {ident!r} appears more than once"))
            continue
        by_id[ident] = status.upper()
        checks.append({"id": ident, "status": status.upper()})
    for ident in DMD_REQUIRED_CHECKS:
        status = by_id.get(ident)
        if status is None:
            findings.append(reason(
                "DMD_CHECK_MISSING", f"required DMD validation {ident} is absent"))
        elif status != "PASS":
            findings.append(reason(
                "DMD_CHECK_FAILED", f"DMD validation {ident} reported {status}"))
    passed = sum(1 for item in checks if item["status"] == "PASS")
    if "checks_passed" in obj and obj["checks_passed"] != passed:
        findings.append(reason(
            "DMD_COUNT_MISMATCH",
            f"checks_passed={obj['checks_passed']!r} disagrees with {passed} PASS checks"))

    dispatch = obj.get("dispatch")
    if not isinstance(dispatch, dict):
        findings.append(reason(
            "DMD_DISPATCH_INVALID", "DMD report dispatch is not an object"))
        dispatch = {}
    if dispatch.get("caught") is not True:
        findings.append(reason(
            "DMD_DISPATCH_NOT_CAUGHT",
            "DMD report does not prove a caught guest exception"))
    if dispatch.get("resumed_guest") is not True:
        findings.append(reason(
            "DMD_DISPATCH_NOT_RESUMED",
            "DMD report does not prove resumed guest execution"))
    if "reason" in dispatch and (not isinstance(dispatch["reason"], str)
                                or not dispatch["reason"].strip()):
        findings.append(reason(
            "DMD_DISPATCH_INVALID", "DMD dispatch reason must be a non-empty string"))

    facts["checks"] = sorted(checks, key=lambda item: item["id"])
    facts["dispatch"] = {key: dispatch.get(key) for key in
                         ("caught", "resumed_guest", "catch_va", "reason")}
    facts["valid"] = not findings and all(
        by_id.get(ident) == "PASS" for ident in DMD_REQUIRED_CHECKS) and (
        dispatch.get("caught") is True and dispatch.get("resumed_guest") is True)
    return facts, findings


# ---------------------------------------------------------------------------
# Real game run record, log and frames
# ---------------------------------------------------------------------------
def parse_run(obj: Any, log_bytes: bytes | None,
              frame_paths: list[Path]) -> tuple[dict, list[dict]]:
    facts: dict[str, Any] = {
        "available": False, "log_verified": False, "caught": False,
        "resumed": False, "exe_sha256": None, "log_sha256": None,
        "frames_claimed": 0, "verified_nonblank": 0, "frames": [],
    }
    findings: list[dict] = []
    if obj is None:
        return facts, findings
    facts["available"] = True
    if not isinstance(obj, dict):
        return facts, [reason("RUN_RECORD_INVALID", "run record is not a JSON object")]
    findings.extend(fake_pass_findings(obj, "game run record"))

    exe = obj.get("exe")
    if isinstance(exe, dict) and is_hex64(exe.get("sha256")):
        facts["exe_sha256"] = exe["sha256"].lower()
    else:
        findings.append(reason(
            "RUN_EXE_HASH_INVALID",
            "run record exe.sha256 is missing or not a 64-hex digest"))

    claim = obj.get("log_sha256")
    if not is_hex64(claim):
        findings.append(reason(
            "RUN_LOG_HASH_INVALID",
            "run record log_sha256 is missing or not a 64-hex digest"))
    else:
        facts["log_sha256"] = claim.lower()
    if log_bytes is None:
        findings.append(reason(
            "RUN_LOG_MISSING", "no --run-log bytes were supplied for the game run"))
    elif claim is None or not is_hex64(claim):
        findings.append(reason(
            "RUN_LOG_UNVERIFIABLE", "cannot verify the log without a valid hash claim"))
    else:
        actual = sha256_bytes(log_bytes)
        if actual != claim.lower():
            findings.append(reason(
                "RUN_LOG_HASH_MISMATCH",
                f"log sha256 {actual} does not match the run claim {claim.lower()} "
                "(tampered)"))
        else:
            facts["log_verified"] = True
            try:
                text = log_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                findings.append(reason(
                    "RUN_LOG_INVALID_TEXT", f"run log is not UTF-8: {exc}"))
                text = ""
            if NEGATIVE_RE.search(text):
                findings.append(reason(
                    "RUN_NEGATIVE_EVIDENCE",
                    "run log contains a not-dispatched/unhandled/fatal signal marker"))
            if DMD_CODE.lower() in text.lower() and DMD_CAUGHT_RE.search(text):
                facts["caught"] = True
            if RESUMED_RE.search(text):
                facts["resumed"] = True
            if not facts["caught"]:
                findings.append(reason(
                    "EXCEPTION_NOT_CAUGHT",
                    f"run log has no verified DMD_EXCEPTION_CAUGHT marker for {DMD_CODE}"))
            if not facts["resumed"]:
                findings.append(reason(
                    "EXECUTION_NOT_RESUMED",
                    "run log has no verified GUEST_EXECUTION_RESUMED marker"))
            events = obj.get("log_events", [])
            if events is not None and not isinstance(events, list):
                findings.append(reason(
                    "LOG_EVENTS_INVALID", "run record log_events is not a list"))
                events = []
            for index, event in enumerate(events or []):
                if not isinstance(event, dict) or not isinstance(
                        event.get("code"), str) or not event["code"].strip():
                    findings.append(reason(
                        "LOG_EVENTS_INVALID", f"log_events[{index}] needs a code string"))
                    continue
                code = event["code"].strip()
                if code not in text:
                    findings.append(reason(
                        "LOG_EVENT_MISMATCH",
                        f"claimed log event {code!r} is absent from the run log (tampered)"))
                    continue
                line = event.get("line")
                if line is not None:
                    lines = text.split("\n")
                    if (not isinstance(line, int) or isinstance(line, bool)
                            or line < 1 or line > len(lines)
                            or code not in lines[line - 1]):
                        findings.append(reason(
                            "LOG_EVENT_LINE_MISMATCH",
                            f"claimed log event {code!r} is not on log line {line!r}"))

    raw_frames = obj.get("frames", [])
    if not isinstance(raw_frames, list):
        findings.append(reason("FRAME_CLAIMS_INVALID", "run frames is not a list"))
        raw_frames = []
    supplied: dict[str, Path] = {}
    for path in frame_paths:
        name = path.name
        if name in supplied:
            findings.append(reason(
                "FRAME_NAME_AMBIGUOUS", f"two --frame inputs are named {name!r}"))
        else:
            supplied[name] = path
    facts["frames_claimed"] = len(raw_frames)
    for index, raw in enumerate(raw_frames):
        if not isinstance(raw, dict):
            findings.append(reason(
                "FRAME_CLAIM_INVALID", f"frames[{index}] is not an object"))
            continue
        name, hash_claim = raw.get("path"), raw.get("sha256")
        if not isinstance(name, str) or not name.strip() or not is_hex64(hash_claim):
            findings.append(reason(
                "FRAME_CLAIM_INVALID",
                f"frames[{index}] needs a path and a 64-hex sha256 claim"))
            continue
        path = supplied.get(Path(name).name)
        if path is None:
            findings.append(reason(
                "FRAME_MISSING", f"no --frame file supplied for claimed frame {name!r}"))
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            findings.append(reason("FRAME_UNREADABLE", f"{path}: {exc}"))
            continue
        digest = sha256_bytes(data)
        if digest != hash_claim.lower():
            findings.append(reason(
                "FRAME_HASH_MISMATCH",
                f"{path.name}: actual sha256 {digest} != claim {hash_claim.lower()} "
                "(tampered)"))
            continue
        if _frame_info is None:  # pragma: no cover - dependency always present here
            findings.append(reason(
                "FRAME_UNDECODABLE", "frame decoder tools.frame_evidence_check unavailable"))
            continue
        info = _frame_info(data)
        if "error" in info:
            findings.append(reason(
                "FRAME_UNDECODABLE", f"{path.name}: {info['error']}"))
            continue
        blank = info.get("blank") is True or info.get("distinct_colors", 0) <= 1
        facts["frames"].append({
            "path": path.name, "sha256": digest, "format": info.get("format"),
            "width": info.get("width"), "height": info.get("height"),
            "distinct_colors": info.get("distinct_colors"), "blank": blank,
        })
        if blank:
            findings.append(reason(
                "FRAME_BLANK",
                f"{path.name}: {info.get('distinct_colors')} distinct colour(s); "
                "a blank frame is not render evidence"))
        else:
            facts["verified_nonblank"] += 1
    if raw_frames and not supplied:
        findings.append(reason(
            "FRAME_FILES_MISSING",
            "run claims frames but no --frame bytes were supplied for proof"))
    facts["frames"].sort(key=lambda item: item["path"])
    return facts, findings


def cross_check(dmd: dict, run: dict) -> list[dict]:
    if (dmd["available"] and run["available"] and dmd["exe_sha256"]
            and run["exe_sha256"] and dmd["exe_sha256"] != run["exe_sha256"]):
        return [reason(
            "EXE_HASH_MISMATCH",
            f"DMD report target {dmd['exe_sha256']} disagrees with run exe "
            f"{run['exe_sha256']} (tampered)")]
    return []


def missing_facts(eh: dict, dmd: dict, run: dict) -> list[dict]:
    """Exact reasons for each fact that is absent or unverified."""
    missing: list[dict] = []
    if not eh["clean"]:
        missing.append(reason(
            "EH_EVIDENCE_INCOMPLETE", "EH selftest evidence is missing or not clean"))
    if not dmd["valid"]:
        missing.append(reason(
            "DMD_EVIDENCE_INCOMPLETE", "DMD object validation is missing or not clean"))
    if not run["log_verified"]:
        missing.append(reason(
            "RUN_LOG_UNVERIFIED", "the real game run log hash was not verified"))
    if not run["caught"]:
        missing.append(reason(
            "EXCEPTION_NOT_CAUGHT", "no verified caught guest DMD exception"))
    if not run["resumed"]:
        missing.append(reason(
            "EXECUTION_NOT_RESUMED", "no verified resumed guest execution"))
    if not run["verified_nonblank"]:
        missing.append(reason(
            "NO_NONBLANK_FRAME", "no proven non-blank frame with a verified hash"))
    return missing


def decide(eh: dict, dmd: dict, run: dict, findings: list[dict]
           ) -> tuple[str, list[dict]]:
    """Return (verdict, exact reasons) from derived facts only."""
    findings = dedupe(findings)
    any_input = eh["available"] or dmd["available"] or run["available"]
    if not any_input:
        return "BLOCKED", dedupe(findings + [reason(
            "NO_EVIDENCE",
            "no EH selftest results, DMD object report or game run was supplied")])
    if any(item["code"] in TAMPER_CODES for item in findings):
        return "BLOCKED", dedupe(findings + [reason(
            "EVIDENCE_TAMPERED",
            "an input contradicts its own counts or the supplied bytes; no "
            "verdict can be resolved from tampered evidence")])
    if any(item["code"] in NEGATIVE_CODES for item in findings):
        return "BLOCKED", dedupe(findings + missing_facts(eh, dmd, run) + [reason(
            "NEGATIVE_EVIDENCE",
            "a supplied input reports a failed check, skipped fixture, blank "
            "frame or unsuccessful run; not a partial pass")])

    resolved = (eh["clean"] and dmd["valid"] and run["log_verified"]
                and run["caught"] and run["resumed"] and run["verified_nonblank"] >= 1)
    if resolved and not blocking_findings(findings):
        return "RESOLVED", dedupe(findings)

    if not run["available"]:
        if eh["clean"]:
            return "PARTIAL", dedupe(findings + [
                reason("SELFTEST_ONLY_NO_RUN",
                       "EH selftests are clean but no real game run was joined; "
                       "selftests alone never resolve issue #27"),
                reason("NO_RUN_EVIDENCE",
                       "no --run record or --run-log/hash was supplied"),
            ])
        return "BLOCKED", dedupe(findings + [reason(
            "NO_RUN_EVIDENCE", "no real game run was supplied and EH selftests "
            "are not clean")])

    if (run["log_verified"] and run["caught"] and run["resumed"]
            and not run["verified_nonblank"]
            and not any(item["code"] in {"FRAME_BLANK", "FRAME_UNDECODABLE"}
                        for item in findings)):
        return "PARTIAL", dedupe(findings + [reason(
            "FRAME_PROOF_MISSING",
            "the run caught and resumed but no supplied frame was proven "
            "non-blank with a recomputed hash")])

    return "BLOCKED", dedupe(findings + missing_facts(eh, dmd, run))


def build_verdict(eh: dict, dmd: dict, run: dict, findings: list[dict],
                  label: str) -> dict:
    verdict, reasons = decide(eh, dmd, run, findings)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "issue": ISSUE,
        "label": label,
        "verdict": verdict,
        "reasons": reasons,
        "evidence": {"eh_selftest": eh, "dmd_object": dmd, "game_run": run},
        "limits": list(LIMITS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eh-selftest", type=Path,
                        help="EH selftest JSON record or build_selftests.sh log")
    parser.add_argument("--eh-selftest-log", type=Path,
                        help="original selftest log bytes for hash cross-check")
    parser.add_argument("--dmd-report", type=Path,
                        help="DMD object validation report JSON")
    parser.add_argument("--run", type=Path, help="real game run record JSON")
    parser.add_argument("--run-log", type=Path, help="actual game run log bytes")
    parser.add_argument("--frame", action="append", type=Path, default=[],
                        help="actual frame file (repeatable; matched by filename)")
    parser.add_argument("--out", type=Path, help="write the verdict JSON here")
    parser.add_argument("--label", default="issue #27 EH/DMD evidence join")
    args = parser.parse_args(argv)

    findings: list[dict] = []
    eh_obj = None
    if args.eh_selftest is not None:
        eh_obj, errs = load_input(args.eh_selftest, "EH_SELFTEST", allow_text=True)
        findings.extend(errs)
    eh_log = None
    if args.eh_selftest_log is not None:
        try:
            eh_log = args.eh_selftest_log.read_bytes()
        except OSError as exc:
            findings.append(reason(
                "EH_SELFTEST_LOG_UNREADABLE", f"{args.eh_selftest_log}: {exc}"))
    eh, errs = parse_eh_selftest(eh_obj, eh_log)
    findings.extend(errs)

    dmd_obj = None
    if args.dmd_report is not None:
        dmd_obj, errs = load_input(args.dmd_report, "DMD_REPORT")
        findings.extend(errs)
    dmd, errs = parse_dmd_report(dmd_obj)
    findings.extend(errs)

    run_obj = None
    if args.run is not None:
        run_obj, errs = load_input(args.run, "RUN_RECORD")
        findings.extend(errs)
    run_log = None
    if args.run_log is not None:
        try:
            run_log = args.run_log.read_bytes()
        except OSError as exc:
            findings.append(reason(
                "RUN_LOG_UNREADABLE", f"{args.run_log}: {exc}"))
    run, errs = parse_run(run_obj, run_log, args.frame)
    findings.extend(errs)
    findings.extend(cross_check(dmd, run))

    doc = build_verdict(eh, dmd, run, findings, args.label)
    text = canonical_json(doc)
    if args.out is not None:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"{TOOL}: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
    print(text, end="")
    return 0 if doc["verdict"] == "RESOLVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
