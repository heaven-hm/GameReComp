#!/usr/bin/env python3
"""Fail-closed validator for Windows-oracle scenario records.

This tool reads a scenario record (the JSON written by ``tools/windows_oracle.py``)
and the artifact paths supplied on the command line, then recomputes every digest
locally. It never contacts an endpoint, launches a process, edits a record, writes
an artifact or closes an issue: it only reads JSON and hashes bytes.

PASS is granted only when the record is complete and internally consistent:

* an explicit ``executed=true`` flag, backed by a captured process with a real
  integer exit code in the evidence detail (a bare flag is FABRICATED_FLAG/FAIL),
* a named, real executed-oracle (the allow-list used by the acceptance matrix),
* a recorded input-script SHA-256, and
* original/native SHA-256 values bound to the exact CLI-supplied files,
* the evidence-file hash and every screenshot/audio/log hash recomputed and
  matching both the record and the capture detail.

A missing field is BLOCKED; a present-but-wrong field (bad digest, wrong type,
mismatch, unsafe path, fake oracle) is FAIL. FAIL outranks BLOCKED. The CLI exit
code is 0 for PASS, 1 for FAIL and 2 for BLOCKED, so a written ``PASS`` claim in
the record can never turn a rejected record into a success.

Usage::

    python3 -m tools.oracle_scenario_check \\
        --record <scenario.json> --original <original.exe> \\
        --native <native.bin> --input-script <script.json> \\
        --artifacts-dir <artifacts-dir> [--detail <capture.json>] \\
        [--out <report.json>]

Scope: this proves local record integrity and hash consistency only. It cannot
attest that an endpoint was genuinely retail Windows, that a process really ran
there, or that gameplay/frame/audio equivalence holds; endpoint provenance and
observability remain the operator's responsibility. No issue is closed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/oracle_scenario_check.py"

# Same allow-list as tools.acceptance_matrix / tools.frida_free_verifier. Inlined
# so this validator stays a pure JSON/hash reader with no runner imports.
REAL_ORACLES = frozenset({
    "original-windows", "original-windows-process", "windows-oracle",
    "windows-original", "windows-process", "original-process",
    "original-vs-native", "original-game", "retail-windows", "retail-original",
})
NO_ORACLES = frozenset({
    "", "none", "null", "n/a", "na", "unknown", "unspecified", "not-supplied",
    "dummy", "dummy-device", "dummy-device-audio", "headless", "sdl-dummy",
    "stub", "fake", "fake-success", "emulator", "unit-test", "selftest",
    "windows-oracle-unexecuted", "no-oracle",
})

ARTIFACT_KINDS = ("screenshots", "audio", "logs")
STATUS_RANK = {"PASS": 0, "BLOCKED": 1, "FAIL": 2}
EXIT_BY_STATUS = {"PASS": 0, "FAIL": 1, "BLOCKED": 2}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

CHECK_NAMES = (
    "record", "record_status", "named_oracle", "executed_flag", "oracle_verified",
    "integer_exit_code", "captured_process", "evidence_hash", "artifact_hashes",
    "original_hash", "native_hash", "input_script_hash",
)

SCOPE = ("local record integrity and recomputed SHA-256 consistency only; not "
         "an endpoint-authenticity, gameplay, frame or audio equivalence claim")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Exact SHA-256 of a real file; raise OSError when unreadable (never guess)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm_sha(value: Any) -> str | None:
    """Return a canonical lowercase 64-hex digest, or None if not one."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if SHA256_RE.fullmatch(text) else None


def norm_name(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def first(*values: Any) -> Any:
    """First non-None value (so an explicit False is not skipped over)."""
    for value in values:
        if value is not None:
            return value
    return None


class Collector:
    """Accumulate reasons and per-check worst status."""

    def __init__(self) -> None:
        self.reasons: list[dict] = []
        self.checks: dict[str, str] = {name: "PASS" for name in CHECK_NAMES}

    def add(self, code: str, status: str, message: str, check: str | None = None) -> None:
        self.reasons.append({"code": code, "status": status, "message": message})
        if check is not None:
            self.set_check(check, status)

    def set_check(self, name: str, status: str) -> None:
        current = self.checks.get(name, "PASS")
        if STATUS_RANK[status] > STATUS_RANK[current]:
            self.checks[name] = status

    def block_all(self, status: str) -> None:
        for name in CHECK_NAMES:
            self.checks[name] = status

    @property
    def status(self) -> str:
        if any(r["status"] == "FAIL" for r in self.reasons):
            return "FAIL"
        if any(r["status"] == "BLOCKED" for r in self.reasons):
            return "BLOCKED"
        return "PASS"


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return None, "not a JSON object"
    return data, None


def _resolve_evidence(record_path: Path, entry: dict, override: Path | None) -> Path | None:
    if override is not None:
        return Path(override)
    raw = entry.get("evidence_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw.strip())
    return path if path.is_absolute() else Path(record_path).parent / path


def check_record(record_path: Path, *, original: Path, native: Path,
                 input_script: Path, artifacts_dir: Path,
                 detail_path: Path | None = None) -> dict:
    """Validate one scenario record against CLI-supplied artifacts.

    Returns a machine-readable report; no file is written and no process is run.
    """
    col = Collector()
    record_path = Path(record_path)
    artifacts_dir = Path(artifacts_dir)
    doc, error = _load_json(record_path)
    if error is not None:
        col.add("RECORD_UNREADABLE", "BLOCKED", f"record: cannot read/parse: {error}", "record")
        col.block_all("BLOCKED")
        return _report(col, {}, {}, {}, None)

    records = doc.get("records")
    if (not isinstance(records, list) or len(records) != 1
            or not isinstance(records[0], dict)):
        col.add("NO_ORACLE_ENTRY", "BLOCKED",
                "record: no single oracle-evidence entry (nothing was captured)", "record")
        entry: dict = {}
    else:
        entry = records[0]

    evidence_path = _resolve_evidence(record_path, entry, detail_path)
    detail, detail_error = (None, "no evidence_path in record")
    if evidence_path is not None:
        detail, detail_error = _load_json(evidence_path)
        if detail_error is not None:
            detail_error = f"evidence detail is unreadable: {detail_error}"

    # --- record status ------------------------------------------------------
    record_status = str(doc.get("status") or "").strip().upper()
    if record_status != "CAPTURED":
        col.add("RECORD_NOT_CAPTURED", "BLOCKED",
                f"record: status {record_status or '<missing>'!r} is not CAPTURED",
                "record_status")
    verification = str(doc.get("verification") or "").strip().upper()
    if verification and verification != "CAPTURED":
        col.add("RECORD_NOT_CAPTURED", "BLOCKED",
                f"record: verification {verification!r} is not CAPTURED", "record_status")

    # --- named oracle -------------------------------------------------------
    name = norm_name(first(doc.get("oracle"), entry.get("oracle"),
                           detail.get("oracle") if detail else None))
    if not name:
        col.add("ORACLE_UNNAMED", "BLOCKED", "oracle: no named oracle", "named_oracle")
    elif name in NO_ORACLES or name not in REAL_ORACLES:
        col.add("ORACLE_NOT_REAL", "FAIL",
                f"oracle: {name!r} is not a real executed-oracle name", "named_oracle")

    # --- explicit flags -----------------------------------------------------
    executed = first(doc.get("executed"), entry.get("executed"),
                     detail.get("executed") if detail else None)
    if executed is not True:
        col.add("ORACLE_NOT_EXECUTED", "BLOCKED",
                "oracle: executed=true required for PASS", "executed_flag")
    verified = first(entry.get("oracle_verified"), doc.get("oracle_verified"),
                     detail.get("oracle_verified") if detail else None)
    if verified is not True:
        col.add("ORACLE_NOT_VERIFIED", "BLOCKED",
                "record: oracle_verified=true required for PASS", "oracle_verified")

    # --- captured process + integer exit code -------------------------------
    detail_status = (str(detail.get("status") or "").strip().upper()
                     if detail else "")
    process = detail.get("process") if detail else None
    has_process = isinstance(process, dict) and bool(process)
    process_exit = detail.get("exit_code") if detail else None
    integer_exit = isinstance(process_exit, int) and not isinstance(process_exit, bool)
    if detail is None:
        col.add("EVIDENCE_UNREADABLE", "BLOCKED", f"oracle: {detail_error}",
                "captured_process")
    else:
        if detail_status != "CAPTURED":
            col.add("EVIDENCE_NOT_CAPTURED", "BLOCKED",
                    f"oracle: evidence detail status {detail_status or '<missing>'!r} "
                    "is not CAPTURED", "captured_process")
        if not has_process:
            col.add("ORACLE_NO_PROCESS", "BLOCKED",
                    "oracle: no captured process in the evidence detail",
                    "captured_process")
        if process_exit is None:
            col.add("ORACLE_NO_EXIT_CODE", "BLOCKED",
                    "oracle: no exit_code in the evidence detail", "integer_exit_code")
        elif not integer_exit:
            col.add("FABRICATED_EXIT_CODE", "FAIL",
                    f"oracle: exit_code {process_exit!r} is not an integer",
                    "integer_exit_code")
    if doc.get("exit_code") is not None:
        if not integer_exit or doc["exit_code"] != process_exit:
            col.add("EXIT_CODE_DISAGREEMENT", "FAIL",
                    "oracle: record exit_code disagrees with the evidence detail",
                    "integer_exit_code")

    captured = bool(record_status == "CAPTURED" and detail is not None
                    and detail_status == "CAPTURED" and has_process and integer_exit)
    if executed is True and not captured:
        col.add("FABRICATED_FLAG", "FAIL",
                "oracle: executed=true asserted without a captured process and "
                "integer exit code in the evidence detail", "executed_flag")

    # --- evidence-file hash -------------------------------------------------
    evidence_sha: str | None = None
    if evidence_path is None:
        col.add("EVIDENCE_PATH_MISSING", "BLOCKED", "record: no evidence_path",
                "evidence_hash")
    else:
        try:
            evidence_sha = sha256_file(evidence_path)
        except OSError:
            col.add("EVIDENCE_FILE_MISSING", "BLOCKED",
                    f"oracle: evidence file is missing: {evidence_path}",
                    "evidence_hash")
        claimed = norm_sha(entry.get("evidence_sha256"))
        if entry.get("evidence_sha256") is None:
            col.add("EVIDENCE_HASH_MISSING", "BLOCKED",
                    "oracle: no evidence_sha256 in record", "evidence_hash")
        elif claimed is None:
            col.add("FABRICATED_HASH", "FAIL",
                    "oracle: evidence_sha256 is not a 64-hex SHA-256 value",
                    "evidence_hash")
        elif evidence_sha is not None and claimed != evidence_sha:
            col.add("HASH_MISMATCH", "FAIL",
                    f"oracle: evidence_sha256 {claimed} != recomputed {evidence_sha}",
                    "evidence_hash")

    # --- artifact hashes (screenshots / audio / logs) -----------------------
    artifact_counts: dict[str, int] = {}
    for kind in ARTIFACT_KINDS:
        listed = doc.get(kind)
        detailed = detail.get(kind) if detail else None
        if not isinstance(listed, list):
            col.add("ARTIFACT_LIST_MISSING", "BLOCKED",
                    f"record: no {kind} list", "artifact_hashes")
            listed = []
        if not isinstance(detailed, list):
            col.add("ARTIFACT_LIST_MISSING", "BLOCKED",
                    f"oracle: no {kind} list in evidence detail", "artifact_hashes")
            detailed = []
        artifact_counts[kind] = len(detailed)
        by_path = {str(a.get("path", "")): a for a in listed if isinstance(a, dict)}
        for artifact in detailed:
            if not isinstance(artifact, dict):
                col.add("ARTIFACT_INVALID", "FAIL",
                        f"oracle: {kind} has a non-object entry", "artifact_hashes")
                continue
            rel_raw = str(artifact.get("path", ""))
            relative = Path(rel_raw)
            parts = relative.parts
            if (relative.is_absolute() or ".." in parts or len(parts) != 2
                    or parts[0] != kind):
                col.add("UNSAFE_ARTIFACT_PATH", "FAIL",
                        f"oracle: unsafe {kind} artifact path {rel_raw!r}",
                        "artifact_hashes")
                continue
            counterpart = by_path.get(rel_raw)
            if counterpart is None:
                col.add("ARTIFACT_LIST_MISMATCH", "FAIL",
                        f"oracle: {rel_raw} is not listed in the record",
                        "artifact_hashes")
                continue
            actual_sha: str | None = None
            actual_bytes: int | None = None
            target = artifacts_dir / relative
            try:
                data = target.read_bytes()
                actual_bytes = len(data)
                actual_sha = sha256_bytes(data)
            except OSError:
                col.add("ARTIFACT_FILE_MISSING", "BLOCKED",
                        f"oracle: artifact file is missing: {target}", "artifact_hashes")
            for claimed_raw in (artifact.get("sha256"), counterpart.get("sha256")):
                claimed = norm_sha(claimed_raw)
                if claimed_raw is None or counterpart.get("sha256") is None:
                    col.add("ARTIFACT_HASH_MISSING", "BLOCKED",
                            f"oracle: no {rel_raw} SHA-256 in record/detail",
                            "artifact_hashes")
                elif claimed is None:
                    col.add("FABRICATED_HASH", "FAIL",
                            f"oracle: {rel_raw} SHA-256 is not 64-hex",
                            "artifact_hashes")
                elif actual_sha is not None and claimed != actual_sha:
                    col.add("HASH_MISMATCH", "FAIL",
                            f"oracle: {rel_raw} SHA-256 {claimed} != "
                            f"recomputed {actual_sha}", "artifact_hashes")
            declared_size = first(artifact.get("bytes"), counterpart.get("bytes"))
            if declared_size is None:
                col.add("ARTIFACT_BYTES_MISSING", "BLOCKED",
                        f"oracle: no byte count for {rel_raw}",
                        "artifact_hashes")
            elif not isinstance(declared_size, int) or isinstance(declared_size, bool):
                col.add("FABRICATED_BYTES", "FAIL",
                        f"oracle: byte count for {rel_raw} is not an integer",
                        "artifact_hashes")
            elif actual_bytes is not None and declared_size != actual_bytes:
                col.add("BYTES_MISMATCH", "FAIL",
                        f"oracle: byte count for {rel_raw} is {declared_size}, "
                        f"actual {actual_bytes}", "artifact_hashes")
        detail_paths = {str(a.get("path", "")) for a in detailed if isinstance(a, dict)}
        for extra in by_path:
            if extra and extra not in detail_paths:
                col.add("ARTIFACT_LIST_MISMATCH", "FAIL",
                        f"record: {kind}/{extra} is not listed in the evidence detail",
                        "artifact_hashes")

    # --- bind original / native / input_script to the CLI files -------------
    hashes = {
        "evidence": evidence_sha,
        "original": _check_binding(col, "original", original, doc.get("original_sha256"),
                                   entry.get("sha256"), detail, "original"),
        "native": _check_binding(col, "native", native, doc.get("native_sha256"),
                                 entry.get("native_sha256"), detail, "native"),
        "input_script": _check_binding(
            col, "input_script", input_script, doc.get("input_script_sha256"),
            entry.get("input_script_sha256"), detail, "input_script"),
    }
    return _report(col, hashes, artifact_counts,
                   {"name": name or None, "executed": executed is True,
                    "verified": verified is True, "captured": captured,
                    "record_status": record_status or None,
                    "detail_status": detail_status or None,
                    "process_present": has_process,
                    "process_exit_code": process_exit if integer_exit else None,
                    "evidence_path": str(evidence_path) if evidence_path else None},
                   detail)


def _check_binding(col: Collector, label: str, path: Path, record_claim: Any,
                   entry_claim: Any, detail: dict | None, detail_key: str) -> str | None:
    check = f"{label}_hash"
    try:
        actual = sha256_file(path)
    except OSError:
        col.add("INPUT_FILE_MISSING", "BLOCKED",
                f"{label}: CLI-supplied file is missing: {path}", check)
        return None
    claims = [record_claim, entry_claim]
    detail_entry = detail.get(detail_key) if detail else None
    if not isinstance(detail_entry, dict):
        col.add("DETAIL_HASH_MISSING", "BLOCKED",
                f"oracle: evidence detail has no {label} hash", check)
    else:
        claims.append(detail_entry.get("sha256"))
    for claim in claims:
        if claim is None:
            col.add("HASH_MISSING", "BLOCKED",
                    f"{label}: no recorded SHA-256 to bind to the CLI file", check)
        elif norm_sha(claim) is None:
            col.add("FABRICATED_HASH", "FAIL",
                    f"{label}: recorded SHA-256 is not 64-hex", check)
        elif norm_sha(claim) != actual:
            col.add("HASH_MISMATCH", "FAIL",
                    f"{label}: recorded SHA-256 {norm_sha(claim)} != recomputed {actual}",
                    check)
    return actual


def _report(col: Collector, hashes: dict, artifact_counts: dict,
            oracle: dict, detail: dict | None) -> dict:
    status = col.status
    summary = {
        "PASS": "complete, consistent Windows-oracle record; every hash recomputed",
        "FAIL": "PASS withheld: fabricated, tampered or contradictory evidence",
        "BLOCKED": "PASS withheld: required evidence or field unavailable",
    }[status]
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "status": status,
        "exit_code": EXIT_BY_STATUS[status],
        "reason": summary,
        "reasons": col.reasons,
        "checks": col.checks,
        "oracle": oracle,
        "hashes": hashes,
        "artifact_counts": artifact_counts,
        "scope": SCOPE,
        "issue_closed": False,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record", required=True, type=Path,
                    help="Windows-oracle scenario record JSON to validate")
    ap.add_argument("--original", required=True, type=Path,
                    help="original Windows executable to bind by SHA-256")
    ap.add_argument("--native", required=True, type=Path,
                    help="native binary to bind by SHA-256")
    ap.add_argument("--input-script", required=True, type=Path,
                    help="recorded input script to bind by SHA-256")
    ap.add_argument("--artifacts-dir", required=True, type=Path,
                    help="directory holding the recorded screenshot/audio/log files")
    ap.add_argument("--detail", type=Path, default=None,
                    help="CLI-supplied capture detail; defaults to the record's "
                         "evidence_path resolved relative to the record")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the machine-readable verdict JSON here")
    args = ap.parse_args(argv)

    report = check_record(
        args.record, original=args.original, native=args.native,
        input_script=args.input_script, artifacts_dir=args.artifacts_dir,
        detail_path=args.detail,
    )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    for reason in report["reasons"]:
        print(f"{reason['status']}: {reason['message']}", file=sys.stderr)
    print(f"{report['status']}: {report['reason']}")
    if args.out is not None:
        print(f"report: {args.out}")
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
