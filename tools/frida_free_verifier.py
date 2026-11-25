#!/usr/bin/env python3
"""Fail-closed Frida-free verifier: Unicorn difftest + process-level oracle.

This is the *documented fallback* for hosts where Frida cannot attach (issue
#171). It joins two independent evidence sources and never substitutes a
written claim for a fact:

* ``--unicorn`` -- a record produced by ``tools/unicorn_diff_adapter.py``. Its
  ``report_path`` (or the explicit ``--unicorn-report``) must name a real
  ``tools/lift/difftest.py --project`` report with non-vacuous agreement.
* ``--oracle`` -- a record produced by ``tools/windows_oracle.py``. Its
  ``records[0].evidence_path`` (or ``--oracle-evidence``) must be a real
  capture detail describing an executed original process with a hashable
  exit code and artifacts.

Every digest in ``--claim`` is recomputed from a CLI-supplied file. The
original, native and scenario paths are authoritative; evidence paths are used
verbatim when absolute and resolved relative to the record that declares them
when relative. A malformed, missing or mismatched digest is a hard FAIL, an
optimistic ``verdict`` field is advisory only, and the legacy ``MATCH`` string
can never become a PASS.

PASS requires all of these at once:

* a real executed original-process oracle (captured detail with a process and
  an integer exit code, bound to the same original and native artifacts),
* matching original / native / scenario / Unicorn-report / oracle-evidence
  SHA-256 values recomputed here,
* non-vacuous Unicorn agreement (a difftest tool report with at least two
  distinct seeded cases that all agree, no divergence, no fault, no
  ``nothing-compared`` row), and
* an explicit Frida status in the claim. A missing Frida status is BLOCKED;
  a Frida ``BLOCKED`` reason is preserved verbatim and labels the result as
  the Frida-free fallback. A written Frida PASS is not accepted as evidence.

Anything else is capped at PARTIAL / BUILD ONLY / FAIL / BLOCKED / NOT TESTED
with the exact reason for withholding PASS. "No Wine/CrossOver dependency"
means exactly that: this module reads JSON files and hashes bytes. It imports
no game runner, launches no process, and does not look for Wine, CrossOver or
any other emulator executable. The oracle's runner provenance is part of the
supplied evidence, not something this verifier provides.

Usage::

    python3 -m tools.frida_free_verifier \\
        --claim evidence/frida-free/claim.json \\
        --original <original.exe> --native <native.bin> \\
        --scenario <scenario.json> \\
        --unicorn evidence/unicorn-record.json \\
        --oracle evidence/oracle-record.json \\
        --out evidence/frida-free/report.json \\
        [--unicorn-report <difftest.json>] [--oracle-evidence <capture.json>] \\
        [--markdown <report.md>] [--generated-at 2026-01-01T00:00:00Z] \\
        [--strict]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/frida_free_verifier.py"
METHOD = "frida-free-fallback"
DIFTEST_TOOL = "tools/lift/difftest.py --project"

STATUSES = ("PASS", "PARTIAL", "BUILD ONLY", "FAIL", "BLOCKED", "NOT TESTED")
# Least to most conservative. FAIL outranks BLOCKED: fabricated evidence is a
# stronger refusal than an absent engine.
STATUS_RANK = {
    "PASS": 0, "PARTIAL": 1, "BUILD ONLY": 2, "NOT TESTED": 3,
    "BLOCKED": 4, "FAIL": 5,
}
HASH_KINDS = ("original", "native", "scenario", "unicorn_report",
              "oracle_evidence")

# The allow-list from tools/os_capability_matrix.py, inlined so this verifier
# stays a pure JSON/hash reader with no runner imports.
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
LEGACY_VERDICTS = frozenset({
    "MATCH", "DIVERGED", "OK", "SUCCESS", "PASSED", "FAILED", "GREEN", "RED",
    "N/A", "NA", "YES", "NO", "TRUE", "FALSE", "100", "100%", "PASS (100%)",
})

GATES = ("real_executed_oracle", "hashes_match", "nonvacuous_unicorn_agreement",
         "frida_status_recorded")
GATE_REASONS = {
    "real_executed_oracle": "no real executed original-process oracle",
    "hashes_match": "original/native/scenario/evidence hashes do not match",
    "nonvacuous_unicorn_agreement": "no non-vacuous Unicorn agreement",
    "frida_status_recorded": "no explicit Frida status was recorded",
}
SCOPE = ("CPU/process-level Frida-free evidence only; not a 100% compatibility, "
         "gameplay, frame or audio claim")
POLICY = {
    "method": METHOD,
    "verdict_vocabulary": list(STATUSES),
    "all_hash_values_recomputed_from_cli_paths": True,
    "legacy_status_strings_are_demoted": True,
    "no_fake_match": True,
    "no_runner_or_emulator_dependency": True,
    "frida_bypass_requires_explicit_frida_blocked_reason": True,
    "claim_scope": SCOPE,
}

SHA_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
EXIT_OK = 0
EXIT_STRICT = 1


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------
def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_sha(value: Any) -> str | None:
    """A real 64-hex digest in lower case, or None (never a guess)."""
    return value.strip().lower() if isinstance(value, str) and SHA_RE.fullmatch(
        value.strip()) else None


def norm_token(value: Any) -> str:
    token = " ".join(str(value or "").replace("†", "").split()).lower()
    token = token.replace("_", "-").replace(" ", "-")
    return re.sub(r"-+", "-", token).strip("-")


def norm_oracle(value: Any) -> str:
    return norm_token(value)


def sha256_file(path: Path | str) -> str | None:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def canonical_json(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False) + "\n"


def write_canonical(doc: Any, path: Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_json(doc), encoding="utf-8", newline="\n")
    return target


def resolve_evidence_path(raw: Any, base: Path | None) -> Path | None:
    """CLI/absolute paths are verbatim; declared relative paths use base."""
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute() or base is None:
        return path
    return base / path


def load_document(path: Path | str | None, kind: str,
                  inputs: list[dict]) -> tuple[Any, dict, str | None]:
    """Read a CLI-supplied JSON record; unreadable inputs fail closed."""
    entry: dict[str, Any] = {
        "kind": kind, "path": str(path) if path is not None else None,
        "sha256": sha256_file(path) if path is not None else None,
        "status": "ABSENT",
    }
    inputs.append(entry)
    if path is None or not Path(path).is_file():
        entry["status"] = "MISSING"
        return None, entry, f"{kind} file not found: {path}"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        entry["status"] = "INVALID"
        message = f"{kind} is not valid JSON: {type(exc).__name__}: {exc}"
        return None, entry, message
    entry["status"] = "OK"
    return data, entry, None


def max_status(statuses: list[str]) -> str:
    return max(statuses, key=lambda s: STATUS_RANK[s]) if statuses else "PASS"


# ---------------------------------------------------------------------------
# Evidence analysis
# ---------------------------------------------------------------------------
def analyse_difftest(document: Any) -> dict:
    """Recompute non-vacuity facts from a difftest report, never from claims."""
    facts = {
        "tool_ok": False, "rows": 0, "agree_rows": 0, "cases": 0,
        "distinct_seeds": 0, "all_cases_agree": False, "bytes_rows": 0,
        "nothing_compared": 0, "nonvacuous": False,
    }
    if not isinstance(document, dict) or document.get("tool") != DIFTEST_TOOL:
        return facts
    facts["tool_ok"] = True
    rows = document.get("addresses")
    if not isinstance(rows, list) or not rows:
        return facts
    facts["rows"] = len(rows)
    seeds: set[str] = set()
    cases = 0
    agree_rows = 0
    bytes_rows = 0
    nothing = 0
    all_agree = True
    for row in rows:
        if not isinstance(row, dict):
            all_agree = False
            continue
        verdict = str(row.get("verdict") or "")
        if verdict.startswith("agree"):
            agree_rows += 1
        else:
            all_agree = False
        if verdict == "nothing-compared":
            nothing += 1
        try:
            if int(row.get("bytes_read", 0)) > 0:
                bytes_rows += 1
        except (TypeError, ValueError):
            pass
        driven = row.get("cases")
        if not isinstance(driven, list) or not driven:
            all_agree = False
            continue
        for case in driven:
            cases += 1
            if not isinstance(case, dict) or case.get("agree") is not True:
                all_agree = False
                continue
            seed = case.get("seed")
            if isinstance(seed, str) and seed.strip():
                seeds.add(seed.strip().lower())
    facts.update({
        "agree_rows": agree_rows, "cases": cases,
        "distinct_seeds": len(seeds), "all_cases_agree": all_agree and cases > 0,
        "bytes_rows": bytes_rows, "nothing_compared": nothing,
        "nonvacuous": bool(agree_rows == len(rows) and cases >= 2
                           and len(seeds) >= 2 and bytes_rows >= 1
                           and all_agree and nothing == 0),
    })
    return facts


def _minimal_report(reasons: list[dict], generated_at: str) -> dict:
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason["code"]] = counts.get(reason["code"], 0) + 1
    status = max_status([reason["status"] for reason in reasons])
    return {
        "schema": SCHEMA_VERSION, "generator": GENERATOR,
        "generated_at": generated_at, "method": METHOD, "status": status,
        "verdict": {
            "status": status, "reasons": reasons,
            "reason_codes": [reason["code"] for reason in reasons],
            "gates": {gate: False for gate in GATES},
            "decision": "no claim could be validated",
        },
        "checks": {gate: False for gate in GATES},
        "frida": {"status": "BLOCKED", "reason": "no claim was supplied",
                  "fallback": METHOD},
        "unicorn": {"status": "NOT TESTED", "reason": "no evidence"},
        "oracle": {"status": "NOT TESTED", "reason": "no evidence"},
        "hashes": {kind: {"path": None, "claimed": None, "actual": None,
                          "match": False} for kind in HASH_KINDS},
        "inputs": [], "claim_scope": SCOPE, "policy": POLICY,
        "claimed_verdict": {"status": None, "reasons": []},
        "summary": {
            "accepted": False, "reason_count": len(reasons),
            "by_reason_code": dict(sorted(counts.items())),
            "artifact_count": 0, "claim_scope": SCOPE,
        },
    }


def verify(claim: Any, *, original: Path | str | None = None,
           native: Path | str | None = None,
           scenario: Path | str | None = None,
           unicorn: Path | str | None = None,
           oracle: Path | str | None = None,
           unicorn_report: Path | str | None = None,
           oracle_evidence: Path | str | None = None,
           generated_at: str | None = None) -> dict:
    """Validate a claim against CLI-supplied evidence; return a fixed report."""
    reasons: list[dict[str, str]] = []
    inputs: list[dict[str, Any]] = []

    def add(code: str, status: str, message: str) -> None:
        reasons.append({"code": code, "status": status, "message": message})

    if not isinstance(claim, dict):
        add("INVALID_CLAIM", "NOT TESTED", "claim must be a JSON object")
        return _minimal_report(reasons, generated_at or utc_now())
    if claim.get("schema") != SCHEMA_VERSION:
        add("INVALID_SCHEMA", "NOT TESTED",
            f"expected schema {SCHEMA_VERSION}, got {claim.get('schema')!r}")
        return _minimal_report(reasons, generated_at or utc_now())

    claimed_hashes = claim.get("hashes") if isinstance(claim.get("hashes"), dict) else {}
    hash_doc: dict[str, Any] = {}

    def check_hash(kind: str, path: Path | str | None, claimed: Any, *,
                   missing_code: str = "MISSING_EVIDENCE",
                   missing_status: str = "BLOCKED") -> dict[str, Any]:
        rec: dict[str, Any] = {
            "path": str(path) if path is not None else None,
            "claimed": norm_sha(claimed), "actual": None, "match": False,
        }
        if path is None or not Path(path).is_file():
            add(missing_code, missing_status,
                f"{kind}: artifact path missing or not a file: {path}")
            return rec
        actual = sha256_file(path)
        rec["actual"] = actual
        inputs.append({"kind": kind, "path": str(path), "sha256": actual,
                       "status": "OK" if actual else "UNREADABLE"})
        if actual is None:
            add("MISSING_EVIDENCE", "BLOCKED", f"{kind}: artifact unreadable: {path}")
        elif claimed is None:
            add("MISSING_HASH", "BLOCKED", f"{kind}: no SHA-256 claim")
        elif rec["claimed"] is None:
            add("FABRICATED_HASH", "FAIL",
                f"{kind}: {claimed!r} is not a 64-hex SHA-256 value")
        elif rec["claimed"] != actual:
            add("HASH_MISMATCH", "FAIL",
                f"{kind}: claimed {rec['claimed']} != recomputed {actual}")
        else:
            rec["match"] = True
        return rec

    for kind, path in (("original", original), ("native", native),
                       ("scenario", scenario)):
        hash_doc[kind] = check_hash(kind, path, claimed_hashes.get(kind))

    unicorn_doc, u_entry, u_error = load_document(unicorn, "unicorn", inputs)
    oracle_doc, o_entry, o_error = load_document(oracle, "oracle", inputs)
    if u_error:
        add("MISSING_EVIDENCE", "BLOCKED", u_error)
    if o_error and oracle is not None:
        add("MISSING_EVIDENCE", "BLOCKED", o_error)

    u_path = resolve_evidence_path(
        unicorn_report if unicorn_report is not None else
        (unicorn_doc.get("report_path") if isinstance(unicorn_doc, dict) else None),
        Path(unicorn).parent if unicorn is not None else None)
    o_path = resolve_evidence_path(
        oracle_evidence if oracle_evidence is not None else
        ((oracle_doc.get("records") or [{}])[0].get("evidence_path")
         if isinstance(oracle_doc, dict)
         and isinstance(oracle_doc.get("records"), list)
         and oracle_doc["records"]
         and isinstance(oracle_doc["records"][0], dict) else None),
        Path(oracle).parent if oracle is not None else None)
    hash_doc["unicorn_report"] = check_hash(
        "unicorn_report", u_path, claimed_hashes.get("unicorn_report"))
    # A missing oracle is an honest BUILD ONLY, not a fabricated hash blocker.
    hash_doc["oracle_evidence"] = check_hash(
        "oracle_evidence", o_path, claimed_hashes.get("oracle_evidence"),
        missing_code="NO_ORACLE_EVIDENCE" if oracle is None else "MISSING_EVIDENCE",
        missing_status="BUILD ONLY" if oracle is None else "BLOCKED")

    # -- Frida: absence is explicit, never silently upgraded ----------------
    frida_raw = claim.get("frida")
    frida_status = str(frida_raw.get("status") or "").strip().upper() if isinstance(
        frida_raw, dict) else ""
    frida_reason = str(frida_raw.get("reason") or "").strip() if isinstance(
        frida_raw, dict) else ""
    if not frida_status:
        add("FRIDA_STATUS_MISSING", "BLOCKED",
            "no Frida status recorded: cannot distinguish an unavailable Frida "
            "from an omitted capture")
        frida_rec = {"status": "BLOCKED",
                     "reason": "no Frida status recorded", "fallback": METHOD}
    elif frida_status in ("BLOCKED", "FAILED", "UNAVAILABLE", "NOT TESTED"):
        frida_rec = {"status": "BLOCKED",
                     "reason": frida_reason or "frida BLOCKED without an exact reason",
                     "fallback": METHOD}
        if not frida_reason:
            add("INCOMPLETE_EVIDENCE", "PARTIAL",
                "frida: BLOCKED without an exact reason")
    elif frida_status in ("PASS", "CAPTURED", "OK", "MATCH"):
        frida_rec = {"status": "NOT TESTED",
                     "reason": "unverified Frida claim is not evidence in a "
                               "Frida-free verifier", "fallback": METHOD}
        add("UNVERIFIED_FRIDA_CLAIM", "PARTIAL",
            "frida: a written PASS is not accepted without a Frida capture record")
    else:
        frida_rec = {"status": "NOT TESTED",
                     "reason": f"unsupported Frida status {frida_status!r}",
                     "fallback": METHOD}
        add("INVALID_FRIDA_STATUS", "BLOCKED",
            f"frida: unsupported status {frida_status!r}")
    frida_ok = frida_rec["status"] == "BLOCKED" and bool(frida_reason)

    # -- Unicorn: recompute agreement and non-vacuity from the report -------
    unicorn_raw = claim.get("unicorn") if isinstance(claim.get("unicorn"), dict) else {}
    u_record_status = str(unicorn_doc.get("status") or "").strip().upper() if isinstance(
        unicorn_doc, dict) else ""
    u_reason = str(unicorn_doc.get("reason") or "").strip() if isinstance(
        unicorn_doc, dict) else ""
    claimed_u_status = str(unicorn_raw.get("status") or "").strip().upper()
    if u_record_status not in ("OK", "PASS"):
        add("UNICORN_BLOCKED", "BLOCKED",
            f"unicorn: {u_reason or 'no OK adapter record'} "
            f"(adapter status {u_record_status or 'absent'})")
        if claimed_u_status in ("OK", "PASS"):
            add("FABRICATED_FLAG", "FAIL",
                "unicorn: claim asserts agreement though the adapter record "
                "does not status OK")
    if u_path is not None and Path(u_path).is_file():
        try:
            report_doc = json.loads(Path(u_path).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            report_doc = None
            add("UNICORN_REPORT_INVALID", "BLOCKED",
                f"unicorn: difftest report is not valid JSON: {type(exc).__name__}")
    else:
        report_doc = None
    facts = analyse_difftest(report_doc)
    if u_path is not None and Path(u_path).is_file() and not facts["tool_ok"]:
        add("UNICORN_REPORT_TAG", "BLOCKED",
            f"unicorn: report tool tag is not {DIFTEST_TOOL!r}")
    adapter_report_hash_ok = False
    if isinstance(unicorn_doc, dict) and isinstance(unicorn_doc.get("report_sha256"), str):
        claimed_report_sha = norm_sha(unicorn_doc["report_sha256"])
        actual_report_sha = hash_doc["unicorn_report"]["actual"]
        if claimed_report_sha is None:
            add("FABRICATED_HASH", "FAIL",
                "unicorn: adapter report_sha256 is not a 64-hex SHA-256 value")
        elif not actual_report_sha or claimed_report_sha != actual_report_sha:
            add("HASH_MISMATCH", "FAIL",
                f"unicorn: adapter report_sha256 {claimed_report_sha} != "
                f"recomputed {actual_report_sha}")
        else:
            adapter_report_hash_ok = True
    elif isinstance(unicorn_doc, dict):
        add("INCOMPLETE_EVIDENCE", "PARTIAL", "unicorn: no report_sha256 in adapter record")

    counts = unicorn_doc.get("counts") if isinstance(unicorn_doc, dict) else {}
    counts = counts if isinstance(counts, dict) else {}
    clean_counts = (
        counts.get("addresses_tested") == facts["rows"] and facts["rows"] >= 1
        and counts.get("agree") == facts["agree_rows"]
        and counts.get("diverge") == 0 and counts.get("original_faulted") == 0
        and counts.get("other") == 0)
    if isinstance(unicorn_doc, dict) and counts and not clean_counts:
        add("UNICORN_COUNT_MISMATCH", "FAIL",
            f"unicorn: adapter counts {counts} disagree with the difftest report "
            f"(rows={facts['rows']}, agree={facts['agree_rows']})")

    agreement = str(unicorn_raw.get("agreement") or "").strip().lower()
    adapter_agreement = str(unicorn_doc.get("agreement") or "").strip().lower() if isinstance(
        unicorn_doc, dict) else ""
    if agreement not in ("full", "ok") or adapter_agreement != "full":
        add("UNICORN_AGREEMENT", "BLOCKED",
            f"unicorn: full agreement required "
            f"(claim={agreement or 'absent'}, adapter={adapter_agreement or 'absent'})")
    if claimed_u_status in ("OK", "PASS") and not facts["all_cases_agree"]:
        add("FABRICATED_FLAG", "FAIL",
            "unicorn: agreement claimed though the difftest report has "
            "diverging, faulted or empty rows")

    nonvacuous = unicorn_raw.get("nonvacuous")
    if nonvacuous is True and not facts["nonvacuous"]:
        add("FABRICATED_FLAG", "FAIL",
            "unicorn: nonvacuous=true asserted but the difftest report has "
            "fewer than two distinct agreeing seeded cases")
    if nonvacuous is not True:
        add("VACUOUS_UNICORN", "BLOCKED", "unicorn: nonvacuous=true required for PASS")
    distinct = unicorn_raw.get("distinct_eax")
    if distinct is None:
        add("INCOMPLETE_EVIDENCE", "PARTIAL", "unicorn: no distinct_eax declared")
    elif isinstance(distinct, bool) or not isinstance(distinct, int) or distinct < 2:
        add("FABRICATED_FLAG", "FAIL",
            f"unicorn: distinct_eax={distinct!r} is vacuous or not an integer >= 2")
    elif distinct > facts["cases"]:
        add("FABRICATED_FLAG", "FAIL",
            f"unicorn: distinct_eax={distinct} exceeds the {facts['cases']} "
            "driven cases in the report")

    unicorn_ok = (
        u_record_status in ("OK", "PASS") and agreement in ("full", "ok")
        and adapter_agreement == "full" and clean_counts and facts["nonvacuous"]
        and nonvacuous is True and isinstance(distinct, int) and not isinstance(distinct, bool)
        and distinct >= 2 and hash_doc["unicorn_report"]["match"]
        and adapter_report_hash_ok)
    unicorn_rec = {
        "status": "PASS" if unicorn_ok else ("BLOCKED" if u_record_status not in
                                             ("OK", "PASS") else "NOT TESTED"),
        "adapter_status": u_record_status or None,
        "reason": u_reason or None,
        "agreement": adapter_agreement or agreement or None,
        "counts": counts or None,
        "nonvacuous_claim": nonvacuous,
        "nonvacuous_recomputed": facts["nonvacuous"],
        "distinct_eax_claim": distinct,
        "difftest_facts": facts,
        "report_path": str(u_path) if u_path else None,
        "report_sha256": hash_doc["unicorn_report"]["actual"],
    }

    # -- Oracle: a real executed original process, not a written flag -------
    oracle_raw = claim.get("oracle") if isinstance(claim.get("oracle"), dict) else {}
    o_status = str(oracle_doc.get("status") or "").strip().upper() if isinstance(
        oracle_doc, dict) else ""
    entry = ((oracle_doc.get("records") or [{}])[0]
             if isinstance(oracle_doc, dict)
             and isinstance(oracle_doc.get("records"), list)
             and oracle_doc["records"]
             and isinstance(oracle_doc["records"][0], dict) else {})
    detail: Any = None
    if o_path is not None and Path(o_path).is_file():
        try:
            detail = json.loads(Path(o_path).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            add("ORACLE_EVIDENCE_INVALID", "BLOCKED",
                f"oracle: capture detail is not valid JSON: {type(exc).__name__}")
    entry_hash_ok = False
    if isinstance(oracle_doc, dict) and isinstance(entry.get("evidence_sha256"), str):
        claimed_evidence_sha = norm_sha(entry["evidence_sha256"])
        actual_evidence_sha = hash_doc["oracle_evidence"]["actual"]
        if claimed_evidence_sha is None:
            add("FABRICATED_HASH", "FAIL",
                "oracle: evidence_sha256 is not a 64-hex SHA-256 value")
        elif not actual_evidence_sha or claimed_evidence_sha != actual_evidence_sha:
            add("HASH_MISMATCH", "FAIL",
                f"oracle: evidence_sha256 {claimed_evidence_sha} != recomputed "
                f"{actual_evidence_sha}")
        else:
            entry_hash_ok = True
    elif isinstance(oracle_doc, dict) and o_status == "CAPTURED":
        add("INCOMPLETE_EVIDENCE", "PARTIAL", "oracle: no evidence_sha256 in record")

    oracle_record_name = oracle_doc.get("oracle") if isinstance(oracle_doc, dict) else None
    name = norm_oracle(oracle_raw.get("name") or oracle_raw.get("oracle")
                       or oracle_record_name)
    executed = oracle_raw.get("executed")
    verified = oracle_raw.get("verified", entry.get("oracle_verified"))
    claim_status = str(oracle_raw.get("status") or "").strip().upper()
    detail_status = str(detail.get("status") or "").strip().upper() if isinstance(
        detail, dict) else ""
    exit_code = detail.get("exit_code") if isinstance(detail, dict) else None
    has_process = bool(detail.get("process")) if isinstance(detail, dict) else False
    captured = (o_status == "CAPTURED" and detail_status == "CAPTURED"
                and has_process and isinstance(exit_code, int)
                and not isinstance(exit_code, bool))
    if name in NO_ORACLES and (executed is True or verified is True):
        add("FABRICATED_ORACLE", "FAIL",
            f"oracle: {name or '<empty>'} is not a real original-process oracle")
    if (executed is True or verified is True) and not captured:
        add("FABRICATED_FLAG", "FAIL",
            "oracle: executed/verified asserted without a captured process and "
            "integer exit code in the evidence detail")
    if oracle is not None:
        if o_status and o_status != "CAPTURED":
            add("ORACLE_NOT_CAPTURED", "BLOCKED",
                f"oracle: record status is {o_status}, not CAPTURED")
        if claim_status and claim_status != "CAPTURED":
            add("ORACLE_NOT_CAPTURED", "BLOCKED",
                f"oracle: claim status is {claim_status}, not CAPTURED")
        if not captured:
            add("ORACLE_NO_PROCESS", "BLOCKED",
                "oracle: no captured process with an integer exit code")
        if executed is not True:
            add("ORACLE_NOT_EXECUTED", "BLOCKED", "oracle: executed=true required for PASS")
        if verified is not True:
            add("ORACLE_NOT_VERIFIED", "BLOCKED", "oracle: verified=true required for PASS")
        if name not in REAL_ORACLES or name in NO_ORACLES:
            add("ORACLE_NOT_REAL", "BLOCKED",
                f"oracle: {name or 'no name'} is not a real executed-oracle name")

    # Bind the capture to the exact artifacts supplied on the CLI.
    bound = captured and hash_doc["original"]["match"] and hash_doc["native"]["match"]
    if captured:
        for label, detail_key, record_key in (
                ("original", "original", "original_sha256"),
                ("native", "native", "native_sha256")):
            actual = hash_doc[label]["actual"]
            detail_hash = norm_sha((detail.get(detail_key) or {}).get("sha256")) if isinstance(
                detail.get(detail_key), dict) else None
            record_hash = norm_sha(oracle_doc.get(record_key)) if isinstance(
                oracle_doc, dict) else None
            if detail_hash != actual or record_hash != actual:
                bound = False
                add("ORACLE_ARTIFACT_MISMATCH", "FAIL",
                    f"oracle: {label} hash in the capture/record disagrees with "
                    "the CLI-supplied artifact")
    oracle_ok = bool(captured and bound and entry_hash_ok
                     and hash_doc["oracle_evidence"]["match"]
                     and executed is True and verified is True
                     and name in REAL_ORACLES and name not in NO_ORACLES
                     and entry.get("oracle_verified") is True)
    if captured and entry.get("oracle_verified") is not True:
        add("ORACLE_NOT_VERIFIED", "BLOCKED",
            "oracle: capture record does not attest oracle_verified=true")
    oracle_rec = {
        "status": "PASS" if oracle_ok else ("CAPTURED" if captured else "BLOCKED"),
        "name": name or None, "executed": executed is True, "verified": verified is True,
        "record_status": o_status or None, "detail_status": detail_status or None,
        "process_present": has_process, "exit_code": exit_code if captured else None,
        "bound_to_cli_artifacts": bound,
        "evidence_path": str(o_path) if o_path else None,
        "evidence_sha256": hash_doc["oracle_evidence"]["actual"],
    }

    # -- the written verdict is advisory only -------------------------------
    verdict_raw = claim.get("verdict") if isinstance(claim.get("verdict"), dict) else {}
    written_status = str(verdict_raw.get("status") or "").strip().upper()
    if written_status in LEGACY_VERDICTS:
        add("OLD_STATUS_STRING", "NOT TESTED",
            f"legacy verdict status {written_status!r} rejected; no fake MATCH")
    elif written_status and written_status not in STATUSES:
        add("INVALID_STATUS", "NOT TESTED",
            f"unsupported verdict status {written_status!r}")

    gates = {
        "real_executed_oracle": oracle_ok,
        "hashes_match": all(hash_doc[kind]["match"] for kind in HASH_KINDS),
        "nonvacuous_unicorn_agreement": unicorn_ok,
        "frida_status_recorded": frida_ok,
    }
    status = max_status([reason["status"] for reason in reasons])
    if written_status in STATUSES and STATUS_RANK[written_status] > STATUS_RANK[status]:
        add("CLAIM_DOWNGRADED", written_status,
            f"written verdict {written_status!r} is more conservative than the "
            f"evidence-derived {status}")
        status = written_status
    elif written_status == "PASS" and status != "PASS":
        add("CLAIM_NOT_EVIDENCED", status,
            "written PASS claim is not supported by the validated evidence")
    if not reasons and all(gates.values()):
        status = "PASS"

    failures = [reason["message"] for reason in reasons if reason["status"] == "FAIL"]
    blockers = [reason["message"] for reason in reasons if reason["status"] == "BLOCKED"]
    if failures:
        decision = "PASS withheld: fabricated, tampered or contradictory evidence"
    elif blockers:
        decision = "PASS withheld: required evidence or status is unavailable"
    elif status == "PASS":
        decision = ("PASS: real executed oracle, recomputed hashes and non-vacuous "
                    "Unicorn agreement; Frida recorded as BLOCKED (fallback)")
    elif not any(gates.values()) and hash_doc["native"]["match"]:
        decision = "native build exists but no process-level oracle was executed"
    else:
        decision = "PASS withheld: " + "; ".join(
            GATE_REASONS[gate] for gate in GATES if not gates[gate])
    if status == "PASS":
        add("ALL_GATES_SATISFIED", "PASS", decision)

    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason["code"]] = counts.get(reason["code"], 0) + 1
    return {
        "schema": SCHEMA_VERSION, "generator": GENERATOR,
        "generated_at": generated_at or utc_now(),
        "method": METHOD, "status": status,
        "verdict": {
            "status": status, "reasons": reasons,
            "reason_codes": [reason["code"] for reason in reasons],
            "gates": gates, "decision": decision,
        },
        "checks": gates, "frida": frida_rec, "unicorn": unicorn_rec,
        "oracle": oracle_rec, "hashes": hash_doc,
        "inputs": sorted(inputs, key=lambda item: (item["kind"], item["path"] or "")),
        "claim_scope": SCOPE, "policy": POLICY,
        "claimed_verdict": {
            "status": written_status or None,
            "reasons": [str(x) for x in verdict_raw.get("reasons", [])]
            if isinstance(verdict_raw.get("reasons"), list) else [],
        },
        "summary": {
            "accepted": status in ("PASS", "PARTIAL"),
            "reason_count": len(reasons),
            "by_reason_code": dict(sorted(counts.items())),
            "artifact_count": len(inputs), "claim_scope": SCOPE,
        },
    }


# ---------------------------------------------------------------------------
# Markdown renderer and CLI
# ---------------------------------------------------------------------------
def _md(value: Any) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    verdict = report["verdict"]
    lines = [
        "# Frida-free verification", "",
        f"Generated: {_md(report['generated_at'])}  ",
        f"Method: `{METHOD}`  ",
        f"Verdict: **{verdict['status']}**", "",
        f"> Scope: {SCOPE}", "",
        "## Gates", "", "| Gate | Satisfied |", "| --- | --- |",
    ]
    for gate, ok in verdict["gates"].items():
        lines.append(f"| {gate} | {ok} |")
    lines += ["", "## Reasons", "", "| Code | Cap | Detail |", "| --- | --- |", ]
    for reason in verdict["reasons"]:
        lines.append(f"| {_md(reason['code'])} | {_md(reason['status'])} | "
                     f"{_md(reason['message'])} |")
    lines += ["", f"Frida: {_md(report['frida']['status'])} — "
                  f"{_md(report['frida']['reason'])}",
              f"Unicorn: {_md(report['unicorn']['status'])}; "
              f"non-vacuous recomputed: "
              f"{_md(report['unicorn']['difftest_facts']['nonvacuous'])}",
              f"Oracle: {_md(report['oracle']['status'])}; "
              f"executed: {_md(report['oracle']['executed'])}", "",
              f"Decision: {_md(verdict['decision'])}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--claim", required=True, type=Path, help="claim JSON to validate")
    ap.add_argument("--original", required=True, type=Path, help="original PE32 x86 executable")
    ap.add_argument("--native", required=True, type=Path, help="recompiled native binary")
    ap.add_argument("--scenario", required=True, type=Path, help="scenario JSON")
    ap.add_argument("--unicorn", required=True, type=Path,
                    help="unicorn_diff_adapter record JSON")
    ap.add_argument("--oracle", type=Path, help="windows_oracle record JSON (omit for BUILD ONLY)")
    ap.add_argument("--unicorn-report", type=Path, help="override the adapter's difftest report path")
    ap.add_argument("--oracle-evidence", type=Path, help="override the oracle's capture detail path")
    ap.add_argument("--out", required=True, type=Path, help="write canonical report JSON here")
    ap.add_argument("--markdown", type=Path, help="optionally write a Markdown summary")
    ap.add_argument("--generated-at", help="ISO UTC timestamp override for deterministic output")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero unless the verdict is a full PASS")
    args = ap.parse_args(argv)

    try:
        claim = json.loads(args.claim.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        claim = None  # a broken claim is a reportable NOT TESTED result
    report = verify(claim, original=args.original, native=args.native,
                    scenario=args.scenario, unicorn=args.unicorn,
                    oracle=args.oracle, unicorn_report=args.unicorn_report,
                    oracle_evidence=args.oracle_evidence,
                    generated_at=args.generated_at or utc_now())
    write_canonical(report, args.out)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    status = report["verdict"]["status"]
    print(f"verdict: {status}; reasons: {len(report['verdict']['reasons'])}")
    print(f"report: {args.out}")
    if args.strict and status != "PASS":
        return EXIT_STRICT
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
