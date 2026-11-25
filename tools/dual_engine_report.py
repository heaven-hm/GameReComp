#!/usr/bin/env python3
"""Honest dual-engine report from CLI-supplied Frida, Unicorn and oracle evidence.

This tool joins three independent, CLI-supplied evidence documents into a single
fixed-vocabulary report:

* ``--frida-status``: a Frida status document. A real capture counts only when
  its evidence file is present and its claimed SHA-256 is recomputed here. A
  blocked attach may be accompanied by a documented equivalent (for example a
  process-level substitute oracle); that equivalent counts only when its own
  evidence file is present and its hash is recomputed here. The host, the Frida
  versions and the exact block reason are carried verbatim from the document.
* ``--unicorn-difftest``: either a ``tools.unicorn_diff_adapter`` record or a
  raw ``tools/lift/difftest.py`` JSON report. Agreement counts are recomputed
  from the address rows, and a claimed report hash is recomputed from the bytes
  on disk. A vacuous run (one case, or no distinct output) is never agreement.
* ``--oracle-records``: Windows-oracle process records. Every claimed digest
  (evidence file, original, native, input script and captured artifacts) is
  recomputed from CLI-supplied paths before it can support a gate.
* ``--hybrid-record``: optional pre-existing hybrid record. A hybrid record
  stating BLOCKED is never upgraded to MATCH, even when every other gate would
  otherwise pass.

Nothing is discovered, defaulted or baked in: every path, version, host label
and verdict comes from the command line or from the supplied documents. This
module does not attach to a process, run Unicorn, contact an oracle endpoint,
launch a game or edit an issue.

Verdict vocabulary: PASS, PARTIAL, BLOCKED, NOT TESTED.

    PASS        only when a Frida capture (or a recomputed documented
                equivalent), non-vacuous recomputed Unicorn agreement and
                recomputed oracle hashes are all present, and no supplied
                hybrid record is BLOCKED.
    PARTIAL     some positive evidence exists but at least one gate is absent
                or unverified.
    BLOCKED     an engine is explicitly blocked, evidence was altered, or a
                supplied hybrid record is BLOCKED.
    NOT TESTED  no usable evidence was supplied at all.

Usage::

    python3 -m tools.dual_engine_report \
        --frida-status <status.json> \
        --unicorn-difftest <difftest.json> \
        --oracle-records <oracle.json> \
        [--hybrid-record <hybrid.json>] \
        --out <report.json> [--markdown <report.md>] \
        [--target <label>] [--generated-at 2026-01-01T00:00:00Z] [--strict]
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
GENERATOR = "tools/dual_engine_report.py"

PASS = "PASS"
PARTIAL = "PARTIAL"
BLOCKED = "BLOCKED"
NOT_TESTED = "NOT TESTED"
STATUSES = (PASS, PARTIAL, NOT_TESTED, BLOCKED)
# Least to most conservative. The final verdict is the most conservative cap
# of every reason, so an optimistic claim can never raise a demoted row.
STATUS_RANK = {PASS: 0, PARTIAL: 1, NOT_TESTED: 2, BLOCKED: 3}

CAPTURED_STATUSES = frozenset({
    "OK", "PASS", "PASSED", "CAPTURED", "RAN", "SUCCESS", "MATCH",
})
SHA_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
VERSION_RE = re.compile(r"\b\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?\b")
ARTIFACT_KINDS = ("screenshots", "audio", "logs")

SCOPE = (
    "CPU/process-level dual-engine evidence only; not a 100% compatibility, "
    "gameplay, frame or audio claim"
)
POLICY = {
    "written_statuses_are_advisory_only": True,
    "all_hashes_recomputed_from_cli_supplied_files": True,
    "pass_requires_frida_capture_or_documented_equivalent": True,
    "pass_requires_nonvacuous_unicorn_agreement": True,
    "pass_requires_recomputed_oracle_hashes": True,
    "hybrid_blocked_never_upgraded_to_match": True,
    "process_level_equivalent_is_not_a_100_percent_claim": True,
    "claim_scope": SCOPE,
}


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------
def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_sha(value: Any) -> str | None:
    """A real 64-hex digest in lower case, or None (never a guess)."""
    return value.strip().lower() if isinstance(value, str) and SHA_RE.fullmatch(
        value.strip()) else None


def sha256_file(path: Path | None) -> str | None:
    digest = hashlib.sha256()
    if path is None or not Path(path).is_file():
        return None
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def resolve_path(raw: Any, base_dir: Path) -> Path | None:
    """Resolve a CLI/document path; relative paths are relative to the JSON."""
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_absolute() else base_dir / path


def _first(mapping: Any, *keys: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def status_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def canonical_json(doc: Any) -> str:
    return json.dumps(doc, indent=2, sort_keys=True,
                      ensure_ascii=False, allow_nan=False) + "\n"


def empty_verification() -> dict:
    return {"path": None, "claimed_sha256": None, "actual_sha256": None,
            "recomputed": False, "tampered": False}


def verify_evidence(raw: Any, base_dir: Path) -> dict:
    """Recompute one claimed hash; a mismatch is tampering, never an upgrade."""
    raw = raw if isinstance(raw, dict) else {}
    path = resolve_path(_first(raw, "path", "evidence_path", "evidence_file", "file"),
                        base_dir)
    claimed = norm_sha(_first(raw, "sha256", "hash", "evidence_sha256", "actual_sha256"))
    actual = sha256_file(path)
    return {
        "path": str(path) if path else None,
        "claimed_sha256": claimed,
        "actual_sha256": actual,
        "recomputed": bool(claimed and actual and claimed == actual),
        "tampered": bool(claimed and actual and claimed != actual),
    }


def load_json_sources(paths: list[str], kind: str) -> tuple[list[dict], list[dict]]:
    """Read CLI-supplied JSON, hashing each input file for provenance."""
    loaded: list[dict] = []
    entries: list[dict] = []
    for raw in paths:
        path = Path(raw).expanduser()
        entry: dict[str, Any] = {
            "kind": kind, "path": str(path), "sha256": sha256_file(path),
            "status": "OK",
        }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, (dict, list)):
                raise ValueError("expected a JSON object or list")
        except (OSError, ValueError, UnicodeError) as exc:
            entry.update(status="INVALID", error=f"{type(exc).__name__}: {exc}")
            entries.append(entry)
            continue
        entries.append(entry)
        loaded.append({"data": data, "source": entry})
    return loaded, entries


# ---------------------------------------------------------------------------
# Frida status document
# ---------------------------------------------------------------------------
def parse_versions(raw: dict) -> list[str]:
    """Collect version tokens from the document without any baked-in version."""
    texts: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, list):
            for item in value:
                add(item)

    for key in ("versions", "version"):
        add(raw.get(key))
    env = raw.get("environment") if isinstance(raw.get("environment"), dict) else {}
    for key in ("consumer", "frida_version", "frida_versions", "version",
                "versions", "os", "system"):
        add(env.get(key))
    host = raw.get("host") if isinstance(raw.get("host"), dict) else {}
    for key in ("version", "versions", "os", "name"):
        add(host.get(key))
    for attempt in (raw.get("attempts") or []):
        if isinstance(attempt, dict):
            add(attempt.get("version"))
            add(attempt.get("error"))
    return sorted({match for text in texts for match in VERSION_RE.findall(text)})


def normalize_frida(doc: Any, source: dict) -> dict:
    base = Path(source["path"]).parent
    raw = doc.get("frida") if isinstance(doc, dict) and isinstance(
        doc.get("frida"), dict) else doc
    raw = raw if isinstance(raw, dict) else {}
    state = status_name(_first(raw, "status", "verdict"))
    if state in CAPTURED_STATUSES:
        state = "CAPTURED"
    elif state == BLOCKED:
        state = BLOCKED
    else:
        state = NOT_TESTED

    env = raw.get("environment") if isinstance(raw.get("environment"), dict) else {}
    host = _first(raw, "host_os", "os") or _first(env, "os", "host_os", "system",
                                                   "macos_version", "version")
    attempts: list[dict] = []
    for item in (raw.get("attempts") or []):
        if not isinstance(item, dict):
            continue
        attempts.append({
            "command": str(item.get("command") or ""),
            "error": str(_first(item, "error", "stderr", "detail") or ""),
            "exit_code": item.get("exit_code"),
            "evidence": verify_evidence(
                {"path": item.get("evidence_file"), "sha256": item.get("sha256")},
                base),
        })

    capture_raw = raw.get("capture") if isinstance(raw.get("capture"), dict) else {}
    call_count = capture_raw.get("call_count", capture_raw.get("count"))
    calls = capture_raw.get("calls")
    capture = {
        "evidence_text": str(_first(capture_raw, "evidence", "observation") or ""),
        "calls": calls if isinstance(calls, list) else [],
        "call_count": call_count if isinstance(call_count, int)
        and not isinstance(call_count, bool) else None,
        "file": verify_evidence(capture_raw, base) if capture_raw else empty_verification(),
    }

    eq_raw = _first(raw, "substitute_oracle", "documented_equivalent", "equivalent",
                    "process_level_oracle")
    eq = eq_raw if isinstance(eq_raw, dict) else {}
    equivalent = {
        "present": bool(eq),
        "engine": str(_first(eq, "engine", "name") or ""),
        "status": status_name(_first(eq, "status", "verification")),
        "observation": str(_first(eq, "observation", "evidence") or ""),
        "file": verify_evidence(eq, base) if eq else empty_verification(),
    }
    return {
        "status": state,
        "reason": str(_first(raw, "reason", "root_cause_of_block", "error") or ""),
        "host": str(host) if host else None,
        "versions": parse_versions(raw),
        "consumer": str(_first(env, "consumer", "frida_version") or ""),
        "attempts": attempts,
        "capture": capture,
        "equivalent": equivalent,
        "source": source["path"],
    }


def frida_gate(record: dict) -> tuple[bool, bool, list[tuple[str, str, str]]]:
    """Return (gate_ok, via_equivalent, reasons) for one Frida record."""
    reasons: list[tuple[str, str, str]] = []
    capture = record["capture"]
    equivalent = record["equivalent"]
    capture_ok = (
        record["status"] == "CAPTURED"
        and bool(capture["calls"] or capture["call_count"] or capture["evidence_text"])
        and capture["file"]["recomputed"]
    )
    equivalent_ok = bool(
        equivalent["present"] and equivalent["status"] in CAPTURED_STATUSES
        and equivalent["file"]["recomputed"]
    )
    if capture["file"]["tampered"]:
        reasons.append(("FRIDA_TAMPERED", BLOCKED,
                        f"Frida capture evidence hash does not match recomputed bytes "
                        f"({capture['file']['claimed_sha256']} != "
                        f"{capture['file']['actual_sha256']})"))
    if equivalent["file"]["tampered"]:
        reasons.append(("FRIDA_TAMPERED", BLOCKED,
                        f"Frida documented-equivalent evidence hash does not match "
                        f"recomputed bytes ({equivalent['file']['claimed_sha256']} != "
                        f"{equivalent['file']['actual_sha256']})"))
    for attempt in record["attempts"]:
        if attempt["evidence"]["tampered"]:
            reasons.append(("FRIDA_TAMPERED", BLOCKED,
                            f"Frida attempt evidence hash does not match recomputed "
                            f"bytes: {attempt['evidence']['path']}"))
    detail = "; ".join(filter(None, (
        record["reason"],
        f"host {record['host']}" if record["host"] else "",
        "Frida versions " + ", ".join(record["versions"]) if record["versions"] else "",
    ))) or "no reason recorded"
    if record["status"] == BLOCKED and equivalent_ok and not capture_ok:
        reasons.append(("FRIDA_BLOCKED_EQUIVALENT_ACCEPTED", PASS,
                        f"Frida BLOCKED ({detail}); using recomputed documented "
                        f"equivalent {equivalent['engine'] or 'process-level oracle'}; "
                        f"process-level only, not a 100% claim"))
    elif record["status"] == BLOCKED and not equivalent_ok:
        reasons.append(("FRIDA_BLOCKED", BLOCKED, f"Frida BLOCKED: {detail}"))
    elif not capture_ok and not equivalent_ok:
        reasons.append(("FRIDA_CAPTURE_UNVERIFIED", PARTIAL,
                        "PASS withheld: no recomputed Frida capture and no recomputed "
                        "documented equivalent"))
    return capture_ok or equivalent_ok, equivalent_ok and not capture_ok, reasons


# ---------------------------------------------------------------------------
# Unicorn difftest document
# ---------------------------------------------------------------------------
def normalize_unicorn(doc: Any, source: dict) -> dict:
    base = Path(source["path"]).parent
    raw = doc.get("unicorn") if isinstance(doc, dict) and isinstance(
        doc.get("unicorn"), dict) else doc
    raw = raw if isinstance(raw, dict) else {}
    is_adapter = (raw.get("schema") == "unicorn_diff_adapter/1"
                  or isinstance(raw.get("verdicts"), list))
    rows_raw = raw.get("verdicts") if is_adapter else raw.get("addresses")
    rows_raw = rows_raw if isinstance(rows_raw, list) else []
    rows: list[dict] = []
    for item in rows_raw:
        if not isinstance(item, dict):
            continue
        eax = _first(item, "eax", "eax_out", "result")
        rows.append({
            "address": str(_first(item, "address", "va") or ""),
            "verdict": str(_first(item, "verdict", "status") or ""),
            "eax": eax if isinstance(eax, int) and not isinstance(eax, bool) else None,
            "signature": str(_first(item, "signature", "output", "regs") or ""),
        })

    counts = {
        "addresses_tested": len(rows),
        "agree": sum(1 for row in rows if row["verdict"].startswith("agree")),
        "diverge": sum(1 for row in rows if row["verdict"].startswith("diverge")),
        "original_faulted": sum(1 for row in rows if row["verdict"] in (
            "original-faulted", "faulted")),
    }
    counts["other"] = len(rows) - counts["agree"] - counts["diverge"] - counts[
        "original_faulted"]
    claimed_counts = raw.get("counts") if isinstance(raw.get("counts"), dict) else {}
    counts_match = (all(claimed_counts.get(key) == value for key, value in counts.items())
                    if claimed_counts else None)

    report = verify_evidence({
        "path": _first(raw, "report_path", "path"),
        "sha256": _first(raw, "report_sha256", "sha256"),
    }, base)
    state = status_name(raw.get("status"))
    if state in CAPTURED_STATUSES:
        state = "OK"
    elif state == BLOCKED:
        state = BLOCKED
    else:
        state = NOT_TESTED

    distinct = raw.get("distinct_eax")
    distinct_eax = distinct if isinstance(distinct, int) and not isinstance(
        distinct, bool) else None
    seen_eax = {row["eax"] for row in rows if row["eax"] is not None}
    seen_signatures = {row["signature"] for row in rows if row["signature"]}
    declared = raw.get("nonvacuous")
    if declared is False:
        nonvacuous = False
    else:
        nonvacuous = len(rows) >= 2 and bool(
            declared is True
            or (distinct_eax is not None and distinct_eax >= 2)
            or len(seen_eax) >= 2
            or len(seen_signatures) >= 2
        )

    agreement = bool(
        state == "OK" and len(rows) >= 2 and counts["agree"] == len(rows)
        and counts["diverge"] == 0 and counts["original_faulted"] == 0
        and counts["other"] == 0
    )
    recomputed = bool(report["recomputed"] or (not is_adapter and bool(rows)))
    return {
        "status": state,
        "reason": str(raw.get("reason") or ""),
        "shape": "unicorn_diff_adapter/1" if is_adapter else "difftest-report",
        "addresses": [row["address"] for row in rows],
        "counts": counts,
        "claimed_counts": dict(claimed_counts),
        "counts_match": counts_match,
        "report": report,
        "seeds": list(raw.get("seeds")) if isinstance(raw.get("seeds"), list) else [],
        "cases": raw.get("cases"),
        "nonvacuous": nonvacuous,
        "distinct_eax": distinct_eax,
        "distinct_eax_from_rows": len(seen_eax),
        "distinct_signatures": len(seen_signatures),
        "agreement": agreement,
        "recomputed": recomputed,
        "source": source["path"],
    }


def unicorn_gate(record: dict) -> tuple[bool, list[tuple[str, str, str]]]:
    """Return (gate_ok, reasons) for one Unicorn record."""
    reasons: list[tuple[str, str, str]] = []
    if record["counts_match"] is False:
        reasons.append(("UNICORN_TAMPERED", BLOCKED,
                        f"Unicorn claimed counts {record['claimed_counts']} do not "
                        f"match recomputed counts {record['counts']}"))
    if record["report"]["tampered"]:
        reasons.append(("UNICORN_TAMPERED", BLOCKED,
                        f"Unicorn report hash does not match recomputed bytes "
                        f"({record['report']['claimed_sha256']} != "
                        f"{record['report']['actual_sha256']})"))
    if record["status"] == BLOCKED:
        reasons.append(("UNICORN_BLOCKED", BLOCKED,
                        f"Unicorn BLOCKED: {record['reason'] or 'no reason recorded'}"))
    elif not record["agreement"]:
        reasons.append(("UNICORN_NO_AGREEMENT", PARTIAL,
                        "PASS withheld: Unicorn did not report clean agreement across "
                        f"at least two cases (recomputed counts {record['counts']})"))
    if not record["nonvacuous"]:
        reasons.append(("UNICORN_VACUOUS", BLOCKED,
                        "PASS withheld: Unicorn agreement is vacuous (no distinct "
                        "entry state/output was evidenced)"))
    if not record["recomputed"]:
        reasons.append(("UNICORN_UNVERIFIED", PARTIAL,
                        "PASS withheld: Unicorn agreement was not recomputed from a "
                        "report file or address rows"))
    ok = bool(record["agreement"] and record["nonvacuous"] and record["recomputed"]
              and record["counts_match"] is not False
              and not record["report"]["tampered"] and record["status"] != BLOCKED)
    return ok, reasons


# ---------------------------------------------------------------------------
# Windows-oracle process records
# ---------------------------------------------------------------------------
def normalize_oracle(doc: Any, source: dict) -> dict:
    base = Path(source["path"]).parent
    if isinstance(doc, list):
        rows = doc
        doc = {}
    elif isinstance(doc, dict):
        rows = _first(doc, "records", "games", "scenarios")
        rows = rows if isinstance(rows, list) else []
    else:
        rows = []
        doc = {}

    entries: list[dict] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        evidence = verify_evidence({
            "path": _first(raw, "evidence_path", "path"),
            "sha256": _first(raw, "evidence_sha256", "sha256"),
        }, base)
        detail: dict = {}
        detail_error: str | None = None
        if evidence["path"]:
            try:
                parsed = json.loads(Path(evidence["path"]).read_text(encoding="utf-8"))
                detail = parsed if isinstance(parsed, dict) else {}
            except (OSError, ValueError, UnicodeError) as exc:
                detail_error = f"{type(exc).__name__}: {exc}"

        hashes: dict[str, dict] = {}
        for key in ("original", "native", "input_script"):
            declared_detail = detail.get(key) if isinstance(detail.get(key), dict) else {}
            path = resolve_path(_first(declared_detail, "path", "file"), base)
            claimed = norm_sha(_first(declared_detail, "sha256", "hash")) or norm_sha(
                raw.get(f"{key}_sha256"))
            actual = sha256_file(path)
            top = norm_sha(raw.get(f"{key}_sha256"))
            detail_sha = norm_sha(_first(declared_detail, "sha256", "hash"))
            disagree = bool(top and detail_sha and top != detail_sha)
            hashes[key] = {
                "path": str(path) if path else None,
                "claimed_sha256": claimed,
                "actual_sha256": actual,
                "recomputed": bool(claimed and actual and claimed == actual)
                and not disagree,
                "tampered": bool((claimed and actual and claimed != actual) or disagree),
            }

        artifacts_dir = resolve_path(_first(raw, "artifacts_dir")
                                     or doc.get("artifacts_dir"), base)
        artifacts: list[dict] = []
        for kind in ARTIFACT_KINDS:
            listed = detail.get(kind)
            if not isinstance(listed, list):
                continue
            for item in listed:
                if not isinstance(item, dict):
                    continue
                rel = str(item.get("path") or "")
                relative = Path(rel)
                safe = (not relative.is_absolute() and len(relative.parts) == 2
                        and relative.parts[0] == kind)
                path = artifacts_dir / relative if safe and artifacts_dir else None
                claimed = norm_sha(_first(item, "sha256", "hash"))
                actual = sha256_file(path)
                artifacts.append({
                    "kind": kind,
                    "path": str(path) if path else None,
                    "claimed_sha256": claimed,
                    "actual_sha256": actual,
                    "recomputed": bool(claimed and actual and claimed == actual),
                    "tampered": bool(claimed and actual and claimed != actual),
                })

        state = status_name(_first(raw, "status") or doc.get("status"))
        if state in CAPTURED_STATUSES:
            state = "CAPTURED"
        elif state == BLOCKED:
            state = BLOCKED
        else:
            state = NOT_TESTED
        verified = raw.get("oracle_verified") is True or status_name(
            doc.get("verification")) == "CAPTURED"
        process = detail.get("process") if isinstance(detail.get("process"), dict) else {}
        process = process or (raw.get("process") if isinstance(raw.get("process"), dict)
                              else {})
        tampered = evidence["tampered"] or any(
            item["tampered"] for item in (*hashes.values(), *artifacts))
        valid = bool(
            state == "CAPTURED" and verified and process and evidence["recomputed"]
            and not tampered and not detail_error
            and all(item["recomputed"] for item in hashes.values())
            and artifacts and all(item["recomputed"] for item in artifacts)
        )
        entries.append({
            "oracle": str(_first(raw, "oracle", "name") or "windows-oracle"),
            "game": str(_first(raw, "game", "game_id") or ""),
            "scenario": str(raw.get("scenario") or ""),
            "status": state,
            "verified": verified,
            "reason": str(_first(raw, "reason") or doc.get("reason") or ""),
            "evidence": evidence,
            "detail_error": detail_error,
            "hashes": hashes,
            "artifacts": artifacts,
            "tampered": tampered,
            "valid": valid,
        })
    return {"records": entries, "source": source["path"]}


def oracle_gate(record: dict) -> tuple[bool, list[tuple[str, str, str]]]:
    """Return (gate_ok, reasons) for one Windows-oracle document."""
    reasons: list[tuple[str, str, str]] = []
    entries = record["records"]
    if not entries:
        reasons.append(("ORACLE_MISSING", PARTIAL,
                        "PASS withheld: no Windows-oracle process record supplied"))
        return False, reasons
    for index, entry in enumerate(entries):
        if entry["tampered"]:
            for key, item in entry["hashes"].items():
                if item["tampered"]:
                    reasons.append(("ORACLE_TAMPERED", BLOCKED,
                                    f"oracle[{index}] {key} hash does not match "
                                    f"recomputed bytes ({item['claimed_sha256']} != "
                                    f"{item['actual_sha256']})"))
            if entry["evidence"]["tampered"]:
                reasons.append(("ORACLE_TAMPERED", BLOCKED,
                                f"oracle[{index}] evidence hash does not match "
                                f"recomputed bytes"))
            for item in entry["artifacts"]:
                if item["tampered"]:
                    reasons.append(("ORACLE_TAMPERED", BLOCKED,
                                    f"oracle[{index}] artifact {item['path']} hash does "
                                    f"not match recomputed bytes"))
        if entry["status"] == BLOCKED:
            reasons.append(("ORACLE_BLOCKED", BLOCKED,
                            f"oracle[{index}] BLOCKED: {entry['reason'] or 'no reason'}"))
    valid = [entry for entry in entries if entry["valid"]]
    if not any(reason[1] == BLOCKED for reason in reasons) and not valid:
        first = entries[0]
        missing = [key for key, item in first["hashes"].items()
                   if not item["recomputed"]]
        detail = f"oracle status {first['status']}, verified={first['verified']}"
        if first["detail_error"]:
            detail += f", detail {first['detail_error']}"
        if missing:
            detail += ", unverified hashes: " + ", ".join(missing)
        reasons.append(("ORACLE_UNVERIFIED", PARTIAL,
                        f"PASS withheld: no recomputable Windows-oracle process record "
                        f"({detail})"))
    return bool(valid) and not any(reason[1] == BLOCKED for reason in reasons), reasons


# ---------------------------------------------------------------------------
# Hybrid record (advisory, never an upgrade)
# ---------------------------------------------------------------------------
def normalize_hybrid(doc: Any, source: dict) -> dict:
    raw = doc if isinstance(doc, dict) else {}
    verdict = raw.get("verdict") if isinstance(raw.get("verdict"), dict) else {}
    state = status_name(_first(raw, "status", "hybrid_status")
                        or verdict.get("status"))
    return {
        "status": state,
        "reason": str(_first(raw, "reason", "detail") or ""),
        "process_level_match": bool(raw.get("process_level_match")),
        "match_claim": bool(raw.get("match_claim")),
        "source": source["path"],
    }


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------
def build_report(frida: list[dict], unicorn: list[dict], oracle: list[dict],
                 hybrid: list[dict], *, generated_at: str,
                 source_paths: list[dict], target: str | None = None) -> dict:
    """Join all supplied, already-normalised evidence into one conservative report."""
    reasons: list[dict[str, str]] = []

    def add(code: str, cap: str, message: str) -> None:
        reasons.append({"code": code, "cap": cap, "message": message})

    for entry in source_paths:
        if entry.get("status") != "OK":
            add("INPUT_UNREADABLE", BLOCKED,
                f"{entry['kind']} input {entry['path']}: "
                f"{entry.get('error') or entry.get('status')}")

    gates = {"frida": False, "unicorn": False, "oracle": False,
             "hybrid_no_upgrade": True}
    frida_via_equivalent = False
    if not frida and not unicorn and not oracle and not hybrid:
        add("NO_INPUT", NOT_TESTED, "no Frida, Unicorn, oracle or hybrid evidence supplied")
    for record in frida:
        ok, via_equivalent, found = frida_gate(record)
        gates["frida"] = gates["frida"] or ok
        frida_via_equivalent = frida_via_equivalent or via_equivalent
        for code, cap, message in found:
            add(code, cap, message)
    if frida and not gates["frida"]:
        add("FRIDA_GATE_WITHHELD", PARTIAL,
            "PASS withheld: no Frida record satisfied the capture/equivalent gate")
    for record in unicorn:
        ok, found = unicorn_gate(record)
        gates["unicorn"] = gates["unicorn"] or ok
        for code, cap, message in found:
            add(code, cap, message)
    for record in oracle:
        ok, found = oracle_gate(record)
        gates["oracle"] = gates["oracle"] or ok
        for code, cap, message in found:
            add(code, cap, message)

    blocked_hybrid = [record for record in hybrid if record["status"] == BLOCKED]
    if blocked_hybrid:
        gates["hybrid_no_upgrade"] = False
        for record in blocked_hybrid:
            detail = "; ".join(filter(None, (
                record["reason"],
                "process-level match was requested" if record["process_level_match"] else "",
            ))) or "no reason recorded"
            add("HYBRID_BLOCKED_NOT_UPGRADED", BLOCKED,
                f"supplied hybrid record is BLOCKED ({detail}); not upgraded to MATCH")

    caps = [reason["cap"] for reason in reasons]
    if BLOCKED in caps:
        verdict = BLOCKED
    elif all(gates.values()) and frida and unicorn and oracle:
        verdict = PASS
    elif any(gates[key] for key in ("frida", "unicorn", "oracle")) or hybrid:
        verdict = PARTIAL
    else:
        verdict = NOT_TESTED

    hybrid_status = BLOCKED if verdict == BLOCKED else verdict
    match_claim = hybrid_status == PASS
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason["code"]] = counts.get(reason["code"], 0) + 1
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at,
        "target": target,
        "verdict": {"status": verdict, "reasons": reasons},
        "gates": gates,
        "hybrid": {
            "status": hybrid_status,
            "match_claim": match_claim,
            "process_level_only": gates["frida"] and frida_via_equivalent,
            "blocked_record_count": len(blocked_hybrid),
        },
        "frida": frida,
        "unicorn": unicorn,
        "oracle": oracle,
        "hybrid_records": hybrid,
        "inputs": sorted(source_paths, key=lambda item: (item["kind"], item["path"])),
        "policy": POLICY,
        "summary": {
            "reason_count": len(reasons),
            "by_reason_code": dict(sorted(counts.items())),
            "claim_scope": SCOPE,
        },
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def _md(value: Any) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    verdict = report["verdict"]
    lines = [
        "# Dual-engine report",
        "",
        f"Verdict: **{verdict['status']}**  ",
        f"Generated: {_md(report['generated_at'])}  ",
        f"Target: {_md(report['target'])}  ",
        f"Schema: {report['schema']}",
        "",
        f"> Scope: {SCOPE}",
        "",
        "## Gates",
        "",
        "| Gate | Result |",
        "| --- | --- |",
    ]
    for key, ok in report["gates"].items():
        lines.append(f"| {key} | {'PASS' if ok else 'WITHHELD'} |")
    lines += ["", f"Hybrid status: **{report['hybrid']['status']}** "
                  f"(match claim: {report['hybrid']['match_claim']}, "
                  f"process-level equivalent: {report['hybrid']['process_level_only']})",
              "", "## Reasons (exact)", ""]
    if verdict["reasons"]:
        lines += ["| Code | Cap | Detail |", "| --- | --- | --- |"]
        for reason in verdict["reasons"]:
            lines.append(f"| {_md(reason['code'])} | {_md(reason['cap'])} | "
                         f"{_md(reason['message'])} |")
    else:
        lines.append("None.")
    lines += ["", "## Inputs", "", "| Kind | Path | Status | SHA-256 |",
              "| --- | --- | --- | --- |"]
    for item in report["inputs"]:
        lines.append(f"| {_md(item['kind'])} | `{_md(item['path'])}` | "
                     f"{_md(item.get('status'))} | `{_md(item.get('sha256'))}` |")
    lines += ["", "No process was attached, no oracle endpoint was contacted and no "
                  "issue was modified by this reporter.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frida-status", action="append", default=[], metavar="JSON",
                    help="Frida status document (repeatable)")
    ap.add_argument("--unicorn-difftest", action="append", default=[], metavar="JSON",
                    help="Unicorn adapter record or difftest report (repeatable)")
    ap.add_argument("--oracle-records", action="append", default=[], metavar="JSON",
                    help="Windows-oracle process records (repeatable)")
    ap.add_argument("--hybrid-record", action="append", default=[], metavar="JSON",
                    help="pre-existing hybrid record; BLOCKED is never upgraded")
    ap.add_argument("--target", default=None, help="optional target label for the report")
    ap.add_argument("--out", required=True, type=Path, help="write the JSON report here")
    ap.add_argument("--markdown", type=Path, help="optionally write a Markdown report")
    ap.add_argument("--generated-at", default=None,
                    help="ISO UTC timestamp override for deterministic output")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero unless the verdict is PASS")
    args = ap.parse_args(argv)

    inputs: list[dict] = []
    frida: list[dict] = []
    unicorn: list[dict] = []
    oracle: list[dict] = []
    hybrid: list[dict] = []
    for paths, kind, normalizer, out in (
        (args.frida_status, "frida-status", normalize_frida, frida),
        (args.unicorn_difftest, "unicorn-difftest", normalize_unicorn, unicorn),
        (args.oracle_records, "oracle-records", normalize_oracle, oracle),
        (args.hybrid_record, "hybrid-record", normalize_hybrid, hybrid),
    ):
        loaded, entries = load_json_sources(paths, kind)
        inputs.extend(entries)
        for wrapped in loaded:
            out.append(normalizer(wrapped["data"], wrapped["source"]))

    report = build_report(
        frida, unicorn, oracle, hybrid,
        generated_at=args.generated_at or utc_now(),
        source_paths=inputs, target=args.target,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(canonical_json(report), encoding="utf-8", newline="\n")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report), encoding="utf-8", newline="\n")

    status = report["verdict"]["status"]
    print(f"verdict: {status}; reasons: {len(report['verdict']['reasons'])}")
    print(f"report: {args.out}")
    return 1 if args.strict and status != PASS else 0


if __name__ == "__main__":
    raise SystemExit(main())
