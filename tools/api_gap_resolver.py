#!/usr/bin/env python3
"""Deterministic per-API gap resolver driven only by CLI-supplied input.

The tool reads three independent documents supplied on the command line:

* ``--missing``  -- missing-API records (the names under investigation),
* ``--shims``    -- a shim registration table (names a runtime claims to answer),
* ``--evidence`` -- optional behavior-evidence index (API name -> evidence file
  plus the SHA-256 that file is expected to have).

It writes one deterministic status row per supplied API name:

``RESOLVED``
    Only when the API name is *exactly* present in the shim registration table
    **and** a behavior-evidence file for that exact name is supplied, exists,
    contains a behavior reference, is not marked failed, and whose bytes hash
    to the SHA-256 declared in the CLI-supplied evidence index (hash recomputed
    at run time; a declared hash is never trusted on its own).
``UNPROVEN``
    The shim is registered, but no qualifying behavior evidence was supplied
    (missing record, unreadable file, hash mismatch, wrong API inside the file,
    no behavior reference, or an explicit failed/skipped result).
``MISSING``
    No exact shim registration exists for the name.  Evidence alone can never
    turn a name into a resolved shim.

Honesty rules
-------------
* No name is resolvable by itself.  The tool has no built-in list of APIs and
  performs no fuzzy, prefix, substring, case-folded or "close keyword"
  matching: a name with a different suffix or case never resolves another
  name, and two names from one library family do not share status.
* ``status`` / ``resolved`` / ``implemented`` hints inside the input documents
  are untrusted and are ignored for the verdict.
* All file paths come from CLI arguments or CLI-supplied documents.  There are
  no repository, corpus, game or machine-specific paths in this module.
* Output is deterministic: rows sorted by name, no timestamps, no aggregate
  percentage and no completion/PASS claim.  Exit 0 means the report was
  written, not that any API is actually implemented.
* Malformed input fails closed (exit 2, no output written).

Usage
-----
::

    python3 -m tools.api_gap_resolver \
        --missing /path/to/missing.json \
        --shims /path/to/shims.json \
        --evidence /path/to/evidence-index.json \
        --out /path/to/status.json

Omit ``--out`` to write the canonical JSON to stdout.  ``--markdown-out`` is
optional and writes the same data as a Markdown table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
STATUSES = ("RESOLVED", "UNPROVEN", "MISSING")
NO_TIMESTAMP = "omitted-for-determinism"

HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
FAIL_WORDS = frozenset({
    "fail", "failed", "failure", "error", "errored", "skip", "skipped",
    "unproven", "missing", "todo", "pending", "blocked",
})
BEHAVIOR_KEYS = ("ref", "selftest", "test", "test_id", "trace", "oracle",
                 "witness", "artifact")
RESULT_KEYS = ("result", "status", "outcome")


class InputError(Exception):
    """Raised for malformed or ambiguous CLI-supplied input (fail closed)."""


# --------------------------------------------------------------------------- #
# Loading / validation
# --------------------------------------------------------------------------- #

def load_json(path: Path | str, label: str) -> tuple[dict, str]:
    """Read a UTF-8 JSON object from ``path``; return (document, sha256)."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read {label} {path}: {exc}") from exc
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InputError(f"{label} {path} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise InputError(f"{label} {path}: top level must be a JSON object")
    return doc, hashlib.sha256(raw).hexdigest()


def read_evidence_bytes(path: Path | str) -> bytes | None:
    """Read an evidence file's raw bytes; return None when it cannot be read."""
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def _nonempty_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{where} must be a non-empty string")
    return value  # kept verbatim: matching is exact, never normalized


def _require_schema(doc: dict, where: str) -> None:
    _nonempty_string(doc.get("schema_version"), f"{where}.schema_version")


def validate_missing(doc: dict) -> list[dict]:
    """Validate missing-API records; untrusted status hints are ignored."""
    _require_schema(doc, "missing")
    raw_apis = doc.get("apis")
    if not isinstance(raw_apis, list) or not raw_apis:
        raise InputError("missing.apis must be a non-empty list")
    apis: list[dict] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_apis):
        where = f"missing.apis[{index}]"
        if not isinstance(raw, dict):
            raise InputError(f"{where} must be an object")
        name = _nonempty_string(raw.get("name"), f"{where}.name")
        if name in seen:
            raise InputError(f"duplicate missing API {name!r}; refuse ambiguous input")
        seen.add(name)
        module = raw.get("module")
        if module is not None:
            module = _nonempty_string(module, f"{where}.module")
        apis.append({"name": name, "module": module})
    return apis


def validate_shims(doc: dict) -> set[str]:
    """Validate the shim registration table; exact strings become the lookup set."""
    _require_schema(doc, "shims")
    raw_shims = doc.get("shims")
    if not isinstance(raw_shims, list):
        raise InputError("shims.shims must be a list")
    registered: set[str] = set()
    for index, raw in enumerate(raw_shims):
        registered.add(_nonempty_string(raw, f"shims.shims[{index}]"))
    return registered


def validate_evidence(doc: dict | None) -> dict[str, dict]:
    """Validate the evidence index into an exact-name -> record mapping."""
    if doc is None:
        return {}
    _require_schema(doc, "evidence")
    raw_records = doc.get("records")
    if not isinstance(raw_records, list):
        raise InputError("evidence.records must be a list")
    records: dict[str, dict] = {}
    for index, raw in enumerate(raw_records):
        where = f"evidence.records[{index}]"
        if not isinstance(raw, dict):
            raise InputError(f"{where} must be an object")
        api = _nonempty_string(raw.get("api"), f"{where}.api")
        if api in records:
            raise InputError(f"duplicate evidence record for {api!r}; refuse ambiguous input")
        path = _nonempty_string(raw.get("path"), f"{where}.path")
        declared = _nonempty_string(raw.get("sha256"), f"{where}.sha256")
        if not HASH_RE.fullmatch(declared):
            raise InputError(f"{where}.sha256 must be 64 hexadecimal characters")
        records[api] = {"api": api, "path": path, "sha256": declared}
    return records


# --------------------------------------------------------------------------- #
# Evidence verification
# --------------------------------------------------------------------------- #

def _failed(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in FAIL_WORDS


def _behavior_ref(doc: dict) -> str | None:
    """Return a non-empty behavior reference, or None if none is present."""
    behavior = doc.get("behavior")
    if isinstance(behavior, str) and behavior.strip():
        return behavior
    if isinstance(behavior, dict):
        for key in BEHAVIOR_KEYS:
            value = behavior.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def verify_evidence(record: dict, api_name: str) -> tuple[bool, str, dict]:
    """Re-hash an evidence file and check it is valid, matching behavior proof.

    Returns ``(qualifies, reason, detail)``.  The hash is always recomputed from
    bytes; a declared hash only serves as the value to compare against.
    """
    detail: dict[str, Any] = {
        "evidence_path": record["path"],
        "sha256_expected": record["sha256"],
        "sha256_recomputed": None,
        "behavior_ref": None,
    }
    raw = read_evidence_bytes(record["path"])
    if raw is None:
        return False, "evidence_file_unreadable", detail
    recomputed = hashlib.sha256(raw).hexdigest()
    detail["sha256_recomputed"] = recomputed
    if recomputed.lower() != record["sha256"].lower():
        return False, "evidence_hash_mismatch", detail

    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False, "evidence_invalid_json", detail
    if not isinstance(doc, dict):
        return False, "evidence_not_behavior_proof", detail

    if doc.get("api") != api_name:
        return False, "evidence_api_mismatch", detail

    ref = _behavior_ref(doc)
    if ref is None:
        return False, "evidence_not_behavior_proof", detail
    detail["behavior_ref"] = ref

    behavior = doc.get("behavior")
    for key in RESULT_KEYS:
        if _failed(doc.get(key)) or (isinstance(behavior, dict) and _failed(behavior.get(key))):
            return False, "evidence_failed_result", detail
    return True, "verified_behavior_evidence", detail


# --------------------------------------------------------------------------- #
# Report construction / rendering
# --------------------------------------------------------------------------- #

def build_status(missing_doc: dict, shims_doc: dict, evidence_doc: dict | None = None,
                 *, missing_path: Path | str | None = None,
                 shims_path: Path | str | None = None,
                 evidence_path: Path | str | None = None,
                 input_hashes: dict[str, str] | None = None) -> dict:
    """Build the deterministic, evidence-led per-API status report."""
    apis = validate_missing(missing_doc)
    registered = validate_shims(shims_doc)
    records = validate_evidence(evidence_doc)
    hashes = dict(input_hashes or {})

    rows: list[dict] = []
    for api in sorted(apis, key=lambda item: item["name"]):
        name = api["name"]
        row: dict[str, Any] = {
            "name": name,
            "module": api["module"],
            "registered": name in registered,
            "evidence_supplied": name in records,
            "status": "MISSING",
            "reason": "no_registered_shim",
            "evidence_path": None,
            "sha256_expected": None,
            "sha256_recomputed": None,
            "behavior_ref": None,
        }
        if name in records:
            row["evidence_path"] = records[name]["path"]
            row["sha256_expected"] = records[name]["sha256"]
        if name in registered:
            if name not in records:
                row.update(status="UNPROVEN", reason="no_evidence_record")
            else:
                qualifies, reason, detail = verify_evidence(records[name], name)
                row.update(detail)
                if qualifies:
                    row.update(status="RESOLVED", reason=reason)
                else:
                    row.update(status="UNPROVEN", reason=reason)
        rows.append(row)

    counts = {status: sum(1 for row in rows if row["status"] == status)
              for status in STATUSES}
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "python3 -m tools.api_gap_resolver",
        "inputs": {
            "missing": str(missing_path) if missing_path is not None else None,
            "shims": str(shims_path) if shims_path is not None else None,
            "evidence": str(evidence_path) if evidence_path is not None else None,
        },
        "input_sha256": hashes,
        "generated_at": NO_TIMESTAMP,
        "policy": {
            "status_rule": ("RESOLVED requires an exact registered shim and a "
                            "hash-verified behavior-evidence file; otherwise "
                            "UNPROVEN (shim, no proof) or MISSING (no shim)"),
            "matching": "exact name equality; no fuzzy, prefix, substring or case-folded matching",
            "hash_rule": "evidence SHA-256 is recomputed from file bytes and compared to the CLI-supplied declaration",
            "hints_ignored": ["status", "resolved", "implemented"],
            "no_percentage": True,
            "completion_claim": False,
        },
        "counts": {"apis": len(rows), **counts},
        "verdict": ("PROVISIONAL - statuses reflect only CLI-supplied registrations "
                    "and hash-verified behavior evidence; not a completion claim"),
        "apis": rows,
    }


def render_json(report: dict) -> str:
    """Canonical JSON text (sorted keys, stable ordering, trailing newline)."""
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_markdown(report: dict) -> str:
    """Render a deterministic Markdown table with no percentage or PASS claim."""
    c = report["counts"]
    L = [
        "# API gap resolution (CLI-supplied input)",
        "",
        f"- Tool: `{report['tool']}`",
        f"- Schema: `{report['schema_version']}`",
        f"- Verdict: {report['verdict']}.",
        f"- Inputs: {report['inputs']}",
        f"- Status rule: {report['policy']['status_rule']}.",
        f"- Matching: {report['policy']['matching']}.",
        f"- Hash rule: {report['policy']['hash_rule']}.",
        f"- Counts: RESOLVED={c['RESOLVED']}, UNPROVEN={c['UNPROVEN']}, "
        f"MISSING={c['MISSING']} (of {c['apis']} supplied names)",
        "",
        "| API | Status | Reason | Registered | Behavior ref |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["apis"]:
        ref = row["behavior_ref"] or "-"
        L.append(f"| `{row['name']}` | {row['status']} | {row['reason']} | "
                 f"{str(row['registered']).lower()} | `{ref}` |")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--missing", type=Path, required=True,
                   help="JSON file of missing-API records (required)")
    p.add_argument("--shims", type=Path, required=True,
                   help="JSON file with the shim registration table (required)")
    p.add_argument("--evidence", type=Path, default=None,
                   help="optional JSON evidence index (api -> file + sha256)")
    p.add_argument("--out", type=Path, default=None,
                   help="JSON status output path (default: stdout)")
    p.add_argument("--markdown-out", type=Path, default=None,
                   help="optional Markdown status output path")
    args = p.parse_args(argv)

    try:
        missing_doc, missing_hash = load_json(args.missing, "missing")
        shims_doc, shims_hash = load_json(args.shims, "shims")
        evidence_doc, evidence_hash = (load_json(args.evidence, "evidence")
                                       if args.evidence is not None else (None, None))
        report = build_status(
            missing_doc, shims_doc, evidence_doc,
            missing_path=args.missing, shims_path=args.shims,
            evidence_path=args.evidence,
            input_hashes={
                "missing": missing_hash,
                "shims": shims_hash,
                "evidence": evidence_hash,
            },
        )
        json_text = render_json(report)
        markdown_text = render_markdown(report)
    except InputError as exc:
        print(f"api_gap_resolver: {exc}", file=sys.stderr)
        return 2

    try:
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json_text, encoding="utf-8")
        else:
            sys.stdout.write(json_text)
        if args.markdown_out is not None:
            args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_out.write_text(markdown_text, encoding="utf-8")
    except OSError as exc:
        print(f"api_gap_resolver: cannot write output: {exc}", file=sys.stderr)
        return 2

    c = report["counts"]
    print(f"api_gap_resolver: wrote report "
          f"(RESOLVED={c['RESOLVED']}, UNPROVEN={c['UNPROVEN']}, "
          f"MISSING={c['MISSING']}); not a completion claim", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
