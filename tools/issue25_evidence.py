#!/usr/bin/env python3
"""Read-only acceptance-evidence auditor for issue #25 (multi-lifter fallback).

This tool *joins* reports produced elsewhere and decides whether the evidence
supplied on the command line satisfies every acceptance criterion of issue
#25. It never runs a provider, a lifter, a differential oracle, ``gh`` or the
network, and it never changes an issue's state. Its only verdicts are
``CLOSABLE`` (the supplied evidence passes this gate) and ``OPEN`` (it does
not); ``CLOSABLE`` is a permission to close, not an act of closure. No
GitHub auto-closing keyword is emitted in any report or on stdout/stderr.

Inputs (all JSON, all supplied on the command line, nothing discovered by
walking the tree):

* ``--selection NAME=PATH`` -- a decision JSON as written by
  ``python3 tools/lift/multilifter.py --json PATH``. NAME is a stable label
  used by evidence records. Repeatable; names must be unique.
* ``--diff-gate NAME=PATH`` -- a report as written by
  ``python3 tests/diff/differential_gate.py --json PATH``. Repeatable;
  names must be unique and must not collide with selection names.
* ``--evidence PATH`` -- one sealed acceptance record, or a JSON array of
  sealed records, with the schema below. Repeatable.

Evidence record schema (``schema_version`` 1)::

    {
      "schema_version": 1,
      "sha256": "<digest of this record with 'sha256' removed>",
      "criterion": "<one of the ids in CRITERIA>",
      "selection": "<--selection name>",     # selection-backed criteria
      "diff_gate": "<--diff-gate name>",     # gate-backed criteria
      "check": "<check name>",               # negative_* criteria
      "classification": "UNRESOLVED",        # both_fail_unresolved
      "verified": false,                     # both_fail_unresolved
      "runs": ["name1", "name2"],             # determinism
      "repeat_count": 2,                      # determinism
      "flag": "ISSUE25_MULTILIFTER",          # opt_in_no_default_regression
      "flag_default": false,
      "default_pipeline_unchanged": true,
      "baseline": {"path": "...", "sha256": "..."},
      "default_run": {"path": "...", "sha256": "..."},
      "source_quality": {"verdict": "PASS", "basis": "static review"},
      "behavior_quality": {"verdict": "SCOPED_PASS", "basis": "differential_gate",
                           "scope": "...", "claims_full_game_equivalence": false}
    }

Only the fields used by a record's criterion matter; the ``sha256`` seal
covers *all* of them, so editing any field without resealing is detected.

Integrity rules (no fabricated PASS):

* Every supplied file's SHA-256 is recomputed from its bytes and recorded.
* Every ``sha256`` claim carried by a supplied JSON document is checked
  against the canonical digest of that document with the claim removed.
* Cited artifacts (``baseline``, ``default_run``) must exist; their SHA-256
  claims are checked against bytes recomputed from disk.
* A selection report is only accepted as internally consistent: PROMOTE
  requires a chosen lifter that is actually PASS with zero diverged cases;
  FAILED/BLOCKED require a null chosen lifter; statuses and counts must be
  well formed. A gate report is only accepted when ``ok`` agrees with its
  ``checks``. A claim inside a record never replaces a derivation from a
  linked report: the tool recomputes the decision from the selection and
  the check results from the gate.

Usage::

    python3 tools/issue25_evidence.py \\
        --selection revng_pass=reports/revng_selection.json \\
        --selection fallback=reports/pcrecomp_selection.json \\
        --selection both_fail=reports/unresolved_selection.json \\
        --diff-gate gate=reports/diff_gate.json \\
        --evidence reports/issue25_evidence.json \\
        --json reports/issue25_audit.json --out reports/issue25_audit.md

Exit codes: 0 = CLOSABLE, 1 = OPEN, 2 = the audit could not run (bad CLI
arguments or unreadable input). A missing file is never a PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1
GENERATOR = "tools/issue25_evidence.py"
GATE = "ISSUE25_EVIDENCE_ONLY_CANNOT_CLOSE"
VERDICTS = ("CLOSABLE", "OPEN")

CRITERIA = (
    "revng_selection",
    "pcrecomp_fallback_after_revng_fail",
    "both_fail_unresolved",
    "negative_pointer",
    "negative_stack",
    "negative_callback",
    "determinism",
    "opt_in_no_default_regression",
    "source_behavior_separation",
)

NEGATIVE_CHECKS = {
    "negative_pointer": "pointer-break-detected",
    "negative_stack": "stack-purge-break-detected",
    "negative_callback": "callback-break-detected",
}

SELECTION_STATUSES = frozenset(
    {"PASS", "DIVERGED", "FAULTED", "NO_BODY", "UNVERIFIED"})
SELECTION_DECISIONS = frozenset({"PROMOTE", "FAILED", "BLOCKED"})
SOURCE_VERDICTS = frozenset({"PASS", "PARTIAL", "FAIL", "NOT_TESTED"})
BEHAVIOR_VERDICTS = frozenset(
    {"SCOPED_PASS", "PARTIAL", "FAIL", "NOT_TESTED"})

_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
CLOSING_KEYWORD = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:#\d+|gh-\d+)"
)

LIMITS = (
    "The tool reports CLOSABLE or OPEN only. It never closes, reopens, "
    "comments on or edits any issue, calls gh or uses the network; it reads "
    "only CLI-supplied files and writes only the two CLI-supplied report "
    "paths. No GitHub auto-closing keyword is emitted.",
    "CLOSABLE means the supplied evidence satisfies this gate, not that an "
    "issue was or will be closed, and not that a game is fully equivalent.",
    "This is a joiner over existing reports. It does not invoke rev.ng, "
    "PCRecomp lift32, difftest, a compiler or a live game. A synthetic report "
    "that satisfies the schema can produce CLOSABLE; provenance of the "
    "reports themselves is outside this tool's reach.",
    "Every supplied file's SHA-256 is recomputed from bytes; every embedded "
    "sha256 claim is checked against a canonical digest with that claim "
    "removed, and cited artifact hashes are recomputed from disk. Optional "
    "seals on tool reports are advisory when absent but binding when present; "
    "evidence records must be sealed.",
    "A selection PROMOTE is accepted only when the chosen lifter is PASS with "
    "zero diverged cases and at least one compared case; FAILED/BLOCKED "
    "require a null chosen lifter. No booleans in an evidence record can "
    "override a linked report.",
    "Passes are scoped agreements on the cases the supplied reports name. "
    "The tool does not claim universal semantic equivalence, whole-game "
    "correctness, or HIGH_LEVEL_VERIFIED status, and it rejects any evidence "
    "record that does.",
    "Determinism means byte-identical repeated run reports plus identical "
    "derived decisions. It says nothing about a machine/setup not supplied.",
)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def sha256_file(path: Path) -> str | None:
    """Exact SHA-256 of a file's bytes, or None when it cannot be read."""
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX.match(value))


def canonical_digest(data: Any) -> str:
    """Digest of a JSON document with its own ``sha256`` claim removed."""
    payload = ({k: v for k, v in data.items() if k != "sha256"}
               if isinstance(data, Mapping) else data)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_json(path: Path, what: str) -> Any:
    """Read one UTF-8 JSON file with a stable, reproducible error message."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"{what} {path}: cannot read JSON: {exc}") from exc


def resolve_path(value: str, base: Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else base / candidate


def add(reasons: list[dict[str, str]], code: str, detail: str) -> None:
    reasons.append({"code": code, "detail": detail})


def seal_errors(data: Any, label: str, *, required: bool,
                reasons: list[dict[str, str]]) -> None:
    """Check a document's self-seal; append fail-closed reason codes."""
    claim = data.get("sha256") if isinstance(data, Mapping) else None
    if claim is None:
        if required:
            add(reasons, "SHA256_SEAL_MISSING", f"{label} has no sha256 claim")
        return
    if not is_sha256(claim):
        add(reasons, "SHA256_SEAL_MALFORMED",
            f"{label} has a malformed sha256 claim: {claim!r}")
        return
    actual = canonical_digest(data)
    if claim.lower() != actual:
        add(reasons, "SHA256_SEAL_MISMATCH",
            f"{label} claimed {claim} but recomputed {actual}")


def parse_specs(values: Sequence[str], kind: str) -> tuple[dict[str, Path], list[str]]:
    """Parse ``NAME=PATH`` CLI specs into an ordered map plus errors."""
    result: dict[str, Path] = {}
    errors: list[str] = []
    for spec in values:
        name, sep, value = spec.partition("=")
        name = name.strip()
        if not sep or not name or not value.strip():
            errors.append(f"invalid --{kind} spec {spec!r}; expected NAME=PATH")
            continue
        if name in result:
            errors.append(f"duplicate --{kind} name {name!r}")
            continue
        result[name] = Path(value)
    return result, errors


# --------------------------------------------------------------------------
# input normalisation
# --------------------------------------------------------------------------
def normalise_selection(name: str, path: Path) -> dict[str, Any]:
    reasons: list[dict[str, str]] = []
    item: dict[str, Any] = {
        "name": name, "path": str(path), "sha256": None, "data": None,
        "decision": None, "chosen_lifter": None, "lifters": (),
        "valid": False, "reasons": reasons,
    }
    digest = sha256_file(path)
    item["sha256"] = digest
    if digest is None:
        add(reasons, "SELECTION_UNREADABLE", f"selection {name!r} is not a readable file: {path}")
        return item
    try:
        data = load_json(path, "selection")
    except ValueError as exc:
        add(reasons, "SELECTION_INVALID_JSON", str(exc))
        return item
    item["data"] = data
    seal_errors(data, f"selection {name!r}", required=False, reasons=reasons)
    if not isinstance(data, Mapping):
        add(reasons, "SELECTION_NOT_OBJECT", f"selection {name!r} is not a JSON object")
        return item

    decision = data.get("decision")
    if decision not in SELECTION_DECISIONS:
        add(reasons, "SELECTION_BAD_DECISION",
            f"selection {name!r} has decision {decision!r}, expected one of "
            f"{sorted(SELECTION_DECISIONS)}")
    chosen = data.get("chosen_lifter")
    if chosen is not None and (not isinstance(chosen, str) or not chosen.strip()):
        add(reasons, "SELECTION_BAD_CHOSEN", f"selection {name!r} has invalid chosen_lifter {chosen!r}")
    raw_lifters = data.get("lifters")
    if not isinstance(raw_lifters, list) or not raw_lifters:
        add(reasons, "SELECTION_NO_LIFTERS", f"selection {name!r} has no lifter rows")
        return item

    lifters: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, raw in enumerate(raw_lifters):
        label = f"selection {name!r} lifters[{index}]"
        if not isinstance(raw, Mapping):
            add(reasons, "LIFTER_BAD_ROW", f"{label} is not an object")
            continue
        lname = raw.get("name")
        if not isinstance(lname, str) or not lname.strip() or lname in seen_names:
            add(reasons, "LIFTER_BAD_NAME", f"{label} has invalid or duplicate name {lname!r}")
            continue
        seen_names.add(lname)
        status = raw.get("status")
        if status not in SELECTION_STATUSES:
            add(reasons, "LIFTER_BAD_STATUS", f"{label} has invalid status {status!r}")
            continue
        diverged = raw.get("diverged_cases")
        total = raw.get("total_cases")
        if (not isinstance(diverged, int) or isinstance(diverged, bool) or diverged < 0
                or not isinstance(total, int) or isinstance(total, bool) or total < 0):
            add(reasons, "LIFTER_BAD_COUNTS",
                f"{label} has invalid case counts diverged={diverged!r} total={total!r}")
            continue
        lifters.append({
            "name": lname, "status": status, "diverged_cases": diverged,
            "total_cases": total, "verdict": raw.get("verdict"),
        })

    item["decision"] = decision
    item["chosen_lifter"] = chosen
    item["lifters"] = tuple(lifters)

    if decision == "PROMOTE":
        if not isinstance(chosen, str) or not chosen.strip():
            add(reasons, "PROMOTE_WITHOUT_CHOSEN",
                f"selection {name!r} is PROMOTE without a chosen_lifter")
        else:
            winner = next((l for l in lifters if l["name"] == chosen), None)
            if winner is None:
                add(reasons, "CHOSEN_LIFTER_MISSING",
                    f"selection {name!r} chose {chosen!r} but has no such lifter row")
            elif winner["status"] != "PASS":
                add(reasons, "CHOSEN_LIFTER_NOT_PASS",
                    f"selection {name!r} chose {chosen!r} with status {winner['status']}")
            elif winner["diverged_cases"] != 0:
                add(reasons, "CHOSEN_LIFTER_DIVERGED",
                    f"selection {name!r} chose {chosen!r} with "
                    f"{winner['diverged_cases']} diverged case(s)")
            elif winner["total_cases"] <= 0:
                add(reasons, "NO_CASES_COMPARED",
                    f"selection {name!r} chose {chosen!r} with zero compared cases")
    elif decision in ("FAILED", "BLOCKED") and chosen is not None:
        add(reasons, "NON_PROMOTE_HAS_CHOSEN",
            f"selection {name!r} is {decision} but names chosen_lifter {chosen!r}")

    item["valid"] = not reasons
    return item


def normalise_gate(name: str, path: Path) -> dict[str, Any]:
    reasons: list[dict[str, str]] = []
    item: dict[str, Any] = {
        "name": name, "path": str(path), "sha256": None, "data": None,
        "ok": None, "checks": {}, "decisions": {}, "valid": False,
        "reasons": reasons,
    }
    digest = sha256_file(path)
    item["sha256"] = digest
    if digest is None:
        add(reasons, "GATE_UNREADABLE", f"diff gate {name!r} is not a readable file: {path}")
        return item
    try:
        data = load_json(path, "diff gate")
    except ValueError as exc:
        add(reasons, "GATE_INVALID_JSON", str(exc))
        return item
    item["data"] = data
    seal_errors(data, f"diff gate {name!r}", required=False, reasons=reasons)
    if not isinstance(data, Mapping):
        add(reasons, "GATE_NOT_OBJECT", f"diff gate {name!r} is not a JSON object")
        return item

    ok = data.get("ok")
    if not isinstance(ok, bool):
        add(reasons, "GATE_BAD_OK", f"diff gate {name!r} has non-boolean ok {ok!r}")
    item["ok"] = ok

    raw_checks = data.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        add(reasons, "GATE_NO_CHECKS", f"diff gate {name!r} has no check rows")
    else:
        checks: dict[str, bool] = {}
        for index, raw in enumerate(raw_checks):
            label = f"diff gate {name!r} checks[{index}]"
            if (not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str)
                    or not raw["name"].strip() or not isinstance(raw.get("ok"), bool)):
                add(reasons, "GATE_BAD_CHECK", f"{label} has no name/boolean ok")
                continue
            if raw["name"] in checks:
                add(reasons, "GATE_DUPLICATE_CHECK", f"{label} repeats check {raw['name']!r}")
                continue
            checks[raw["name"]] = raw["ok"]
        item["checks"] = checks
        if isinstance(ok, bool) and ok != all(checks.values()):
            add(reasons, "GATE_INCONSISTENT",
                f"diff gate {name!r} claims ok={ok} but checks are {checks}")

    decisions = data.get("decisions", {})
    if isinstance(decisions, Mapping) and all(
            isinstance(k, str) and v in SELECTION_DECISIONS
            for k, v in decisions.items()):
        item["decisions"] = dict(decisions)
    elif decisions != {}:
        add(reasons, "GATE_BAD_DECISIONS",
            f"diff gate {name!r} has invalid decisions {decisions!r}")

    item["valid"] = not reasons
    return item


def normalise_evidence(path: Path) -> list[dict[str, Any]]:
    """Return sealed evidence records with their structural errors attached."""
    digest = sha256_file(path)
    records: list[dict[str, Any]] = []
    if digest is None:
        records.append({
            "path": str(path), "index": 0, "sha256": None, "criterion": None,
            "record": None, "reasons": [{"code": "EVIDENCE_UNREADABLE",
                                         "detail": f"evidence file is not readable: {path}"}],
        })
        return records
    try:
        data = load_json(path, "evidence")
    except ValueError as exc:
        records.append({
            "path": str(path), "index": 0, "sha256": digest, "criterion": None,
            "record": None, "reasons": [{"code": "EVIDENCE_INVALID_JSON",
                                         "detail": str(exc)}],
        })
        return records

    raw_records = data if isinstance(data, list) else [data]
    if not raw_records:
        records.append({
            "path": str(path), "index": 0, "sha256": digest, "criterion": None,
            "record": None,
            "reasons": [{"code": "EVIDENCE_EMPTY", "detail": f"evidence file {path} is empty"}],
        })
        return records

    for index, raw in enumerate(raw_records):
        reasons: list[dict[str, str]] = []
        criterion = None
        if isinstance(raw, Mapping):
            criterion = raw.get("criterion")
            if raw.get("schema_version") != SCHEMA_VERSION:
                add(reasons, "EVIDENCE_BAD_SCHEMA",
                    f"evidence {path}[{index}] has schema_version "
                    f"{raw.get('schema_version')!r}, expected {SCHEMA_VERSION}")
            if criterion not in CRITERIA:
                add(reasons, "EVIDENCE_BAD_CRITERION",
                    f"evidence {path}[{index}] has unknown criterion {criterion!r}")
                criterion = None
            seal_errors(raw, f"evidence {path}[{index}]", required=True,
                        reasons=reasons)
        else:
            add(reasons, "EVIDENCE_BAD_RECORD",
                f"evidence {path}[{index}] is not a JSON object")
            raw = None
        records.append({
            "path": str(path), "index": index, "sha256": digest,
            "criterion": criterion, "record": raw, "reasons": reasons,
        })
    return records


# --------------------------------------------------------------------------
# criterion checks
# --------------------------------------------------------------------------
def _selection_link(rec: Mapping[str, Any], selections: Mapping[str, dict],
                    add_reason) -> dict[str, Any] | None:
    name = rec.get("selection")
    if not isinstance(name, str) or not name.strip():
        add_reason("SELECTION_NOT_CITED", "evidence record has no 'selection' name")
        return None
    selected = selections.get(name)
    if selected is None:
        add_reason("SELECTION_NOT_SUPPLIED", f"selection {name!r} was not supplied")
        return None
    if not selected["valid"]:
        for reason in selected["reasons"]:
            add_reason(reason["code"], reason["detail"])
        return None
    return selected


def _gate_link(rec: Mapping[str, Any], gates: Mapping[str, dict],
               add_reason) -> dict[str, Any] | None:
    name = rec.get("diff_gate")
    if not isinstance(name, str) or not name.strip():
        add_reason("GATE_NOT_CITED", "evidence record has no 'diff_gate' name")
        return None
    gate = gates.get(name)
    if gate is None:
        add_reason("GATE_NOT_SUPPLIED", f"diff gate {name!r} was not supplied")
        return None
    if not gate["valid"]:
        for reason in gate["reasons"]:
            add_reason(reason["code"], reason["detail"])
        return None
    return gate


def _gate_ok(gate: Mapping[str, Any], add_reason) -> None:
    if gate["ok"] is not True:
        add_reason("GATE_NOT_OK", f"diff gate {gate['name']!r} has ok={gate['ok']!r}")
    for check, passed in sorted(gate["checks"].items()):
        if passed is not True:
            add_reason("GATE_CHECK_FAILED",
                       f"diff gate {gate['name']!r} check {check!r} is not ok")


def _lifter(selected: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    return next((l for l in selected["lifters"] if l["name"] == name), None)


def check_revng(rec: Mapping[str, Any], selections: Mapping[str, dict],
                gates: Mapping[str, dict], base: Path, add_reason) -> dict[str, Any]:
    sel = _selection_link(rec, selections, add_reason)
    if sel is None:
        return {}
    if sel["decision"] != "PROMOTE" or sel["chosen_lifter"] != "revng":
        add_reason("REVNG_NOT_SELECTED",
                   f"selection {sel['name']!r} is {sel['decision']} with "
                   f"chosen_lifter={sel['chosen_lifter']!r}; expected PROMOTE/revng")
    lifter = _lifter(sel, "revng")
    if lifter is None:
        add_reason("REVNG_LIFTER_MISSING",
                   f"selection {sel['name']!r} has no 'revng' lifter row")
        return {}
    names = [l["name"] for l in sel["lifters"]]
    if names[0] != "revng":
        add_reason("REVNG_NOT_FIRST_PRIORITY",
                   f"selection {sel['name']!r} places {names[0]!r} before 'revng'")
    if lifter["status"] != "PASS":
        add_reason("REVNG_STATUS_NOT_PASS",
                   f"revng status is {lifter['status']} in selection {sel['name']!r}")
    if lifter["diverged_cases"] != 0:
        add_reason("REVNG_DIVERGED_CASES",
                   f"revng has {lifter['diverged_cases']} diverged case(s) in "
                   f"selection {sel['name']!r}")
    if lifter["total_cases"] <= 0:
        add_reason("NO_CASES_COMPARED",
                   f"revng compared {lifter['total_cases']} case(s) in selection {sel['name']!r}")
    return {"selection": sel["name"], "selected": sel["chosen_lifter"],
            "recomputed_decision": sel["decision"], "sha256": sel["sha256"]}


def check_fallback(rec: Mapping[str, Any], selections: Mapping[str, dict],
                   gates: Mapping[str, dict], base: Path, add_reason) -> dict[str, Any]:
    sel = _selection_link(rec, selections, add_reason)
    if sel is None:
        return {}
    if sel["decision"] != "PROMOTE" or sel["chosen_lifter"] != "pcrecomp":
        add_reason("PCRECOMP_NOT_SELECTED",
                   f"selection {sel['name']!r} is {sel['decision']} with "
                   f"chosen_lifter={sel['chosen_lifter']!r}; expected PROMOTE/pcrecomp")
    revng = _lifter(sel, "revng")
    pcrecomp = _lifter(sel, "pcrecomp")
    if revng is None:
        add_reason("REVNG_LIFTER_MISSING",
                   f"selection {sel['name']!r} has no 'revng' row to justify fallback")
    elif revng["status"] == "PASS":
        add_reason("FALLBACK_WITHOUT_REVNG_FAILURE",
                   f"revng is PASS in selection {sel['name']!r}; PCRecomp may only "
                   "be used after revng fails")
    if pcrecomp is None:
        add_reason("PCRECOMP_LIFTER_MISSING",
                   f"selection {sel['name']!r} has no 'pcrecomp' row")
        return {}
    if pcrecomp["status"] != "PASS":
        add_reason("PCRECOMP_STATUS_NOT_PASS",
                   f"pcrecomp status is {pcrecomp['status']} in selection {sel['name']!r}")
    if pcrecomp["diverged_cases"] != 0:
        add_reason("PCRECOMP_DIVERGED_CASES",
                   f"pcrecomp has {pcrecomp['diverged_cases']} diverged case(s) in "
                   f"selection {sel['name']!r}")
    if pcrecomp["total_cases"] <= 0:
        add_reason("NO_CASES_COMPARED",
                   f"pcrecomp compared {pcrecomp['total_cases']} case(s) in selection {sel['name']!r}")
    names = [l["name"] for l in sel["lifters"]]
    if "revng" in names and "pcrecomp" in names and names.index("revng") > names.index("pcrecomp"):
        add_reason("FALLBACK_ORDER_INVALID",
                   f"selection {sel['name']!r} ranks pcrecomp before revng")
    reason = rec.get("fallback_reason")
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        add_reason("FALLBACK_REASON_EMPTY",
                   f"fallback_reason in evidence {rec.get('_label')} is empty")
    return {"selection": sel["name"], "revng_status": revng["status"] if revng else None,
            "pcrecomp_status": pcrecomp["status"], "sha256": sel["sha256"]}


def check_unresolved(rec: Mapping[str, Any], selections: Mapping[str, dict],
                     gates: Mapping[str, dict], base: Path, add_reason) -> dict[str, Any]:
    sel = _selection_link(rec, selections, add_reason)
    if sel is None:
        return {}
    if sel["decision"] == "PROMOTE" or sel["chosen_lifter"] is not None:
        add_reason("NOT_UNRESOLVED",
                   f"selection {sel['name']!r} is {sel['decision']} with "
                   f"chosen_lifter={sel['chosen_lifter']!r}; both-fail must be UNRESOLVED")
    if len(sel["lifters"]) < 2:
        add_reason("TOO_FEW_LIFTERS",
                   f"selection {sel['name']!r} has {len(sel['lifters'])} lifter(s); "
                   "both-fail needs at least two")
    if any(l["status"] == "PASS" for l in sel["lifters"]):
        add_reason("VERIFICATION_LABEL_PRESENT",
                   f"selection {sel['name']!r} contains a PASS lifter; not both-fail")
    if rec.get("classification") != "UNRESOLVED":
        add_reason("CLASSIFICATION_NOT_UNRESOLVED",
                   f"evidence claims classification {rec.get('classification')!r}; "
                   "expected the literal 'UNRESOLVED'")
    if rec.get("verified") is not False:
        add_reason("VERIFICATION_LABEL_PRESENT",
                   f"evidence has verified={rec.get('verified')!r}; expected boolean false")
    label = rec.get("label")
    if isinstance(label, str) and "verified" in label.lower():
        add_reason("VERIFICATION_LABEL_PRESENT",
                   f"evidence label {label!r} claims a verified result")
    return {"selection": sel["name"], "recomputed_classification": "UNRESOLVED",
            "sha256": sel["sha256"]}


def check_negative(criterion: str, rec: Mapping[str, Any],
                   selections: Mapping[str, dict], gates: Mapping[str, dict],
                   base: Path, add_reason) -> dict[str, Any]:
    gate = _gate_link(rec, gates, add_reason)
    if gate is None:
        return {}
    _gate_ok(gate, add_reason)
    expected = NEGATIVE_CHECKS[criterion]
    check = rec.get("check")
    if check != expected:
        add_reason("NEGATIVE_CHECK_WRONG",
                   f"evidence for {criterion} cites check {check!r}; expected {expected!r}")
        return {"diff_gate": gate["name"], "sha256": gate["sha256"]}
    if check not in gate["checks"]:
        add_reason("NEGATIVE_CHECK_MISSING",
                   f"diff gate {gate['name']!r} has no check named {check!r}")
    elif gate["checks"][check] is not True:
        add_reason("NEGATIVE_CHECK_FAILED",
                   f"diff gate {gate['name']!r} check {check!r} is not ok")
    if "FAILED" not in gate["decisions"].values():
        add_reason("NEGATIVE_CANDIDATE_NOT_REJECTED",
                   f"diff gate {gate['name']!r} has no FAILED selector decision for the broken body")
    return {"diff_gate": gate["name"], "check": check,
            "decisions": gate["decisions"], "sha256": gate["sha256"]}


def check_determinism(rec: Mapping[str, Any], selections: Mapping[str, dict],
                      gates: Mapping[str, dict], base: Path, add_reason) -> dict[str, Any]:
    runs = rec.get("runs")
    if (not isinstance(runs, list) or len(runs) < 2
            or not all(isinstance(r, str) and r.strip() for r in runs)
            or len(set(runs)) != len(runs)):
        add_reason("DETERMINISM_TOO_FEW_RUNS",
                   f"determinism evidence cites runs={runs!r}; expected two or more "
                   "distinct non-empty names")
        runs = runs if isinstance(runs, list) else []
    repeat = rec.get("repeat_count")
    if (not isinstance(repeat, int) or isinstance(repeat, bool)
            or repeat < 2 or repeat != len(runs)):
        add_reason("DETERMINISM_COUNT_MISMATCH",
                   f"repeat_count={repeat!r} but {len(runs)} run(s) were cited")
    kinds: list[str] = []
    digests: list[str | None] = []
    derived: list[Any] = []
    for name in runs:
        sel, gate = selections.get(name), gates.get(name)
        if sel is not None and gate is not None:
            add_reason("DETERMINISM_AMBIGUOUS_RUN", f"run name {name!r} names two reports")
            continue
        if sel is not None:
            kinds.append("selection")
            if not sel["valid"]:
                for reason in sel["reasons"]:
                    add_reason(reason["code"], reason["detail"])
            digests.append(sel["sha256"])
            derived.append((sel["decision"], sel["chosen_lifter"],
                            tuple((l["name"], l["status"], l["diverged_cases"],
                                   l["total_cases"]) for l in sel["lifters"])))
        elif gate is not None:
            kinds.append("diff_gate")
            if not gate["valid"]:
                for reason in gate["reasons"]:
                    add_reason(reason["code"], reason["detail"])
            digests.append(gate["sha256"])
            derived.append((gate["ok"], tuple(sorted(gate["checks"].items())),
                            tuple(sorted(gate["decisions"].items()))))
        else:
            add_reason("RUN_NOT_SUPPLIED", f"determinism run {name!r} was not supplied")
    if len(set(kinds)) > 1:
        add_reason("DETERMINISM_RUN_KIND_MISMATCH",
                   f"runs mix report kinds: {sorted(set(kinds))}")
    if len(digests) > 1 and len(set(digests)) > 1:
        add_reason("DETERMINISM_DIGEST_MISMATCH",
                   "recomputed report SHA-256 digests differ across runs: "
                   + ", ".join(str(d) for d in digests))
    if len(derived) > 1 and len(set(derived)) > 1:
        add_reason("DETERMINISM_DECISION_MISMATCH",
                   "derived decisions/checks differ across runs")
    return {"runs": list(runs), "sha256": digests[0] if digests else None,
            "distinct_digests": len(set(digests))}


def check_opt_in(rec: Mapping[str, Any], selections: Mapping[str, dict],
                 gates: Mapping[str, dict], base: Path, add_reason) -> dict[str, Any]:
    flag = rec.get("flag")
    if not isinstance(flag, str) or not flag.strip():
        add_reason("FEATURE_FLAG_MISSING", "opt-in evidence has no non-empty 'flag'")
    if rec.get("flag_default") is not False:
        add_reason("FEATURE_FLAG_DEFAULT_NOT_FALSE",
                   f"flag_default={rec.get('flag_default')!r}; expected boolean false")
    if rec.get("default_pipeline_unchanged") is not True:
        add_reason("DEFAULT_PIPELINE_NOT_UNCHANGED",
                   f"default_pipeline_unchanged={rec.get('default_pipeline_unchanged')!r}; "
                   "expected boolean true")
    digests: dict[str, str] = {}
    for role in ("baseline", "default_run"):
        spec = rec.get(role)
        if (not isinstance(spec, Mapping) or not isinstance(spec.get("path"), str)
                or not spec["path"].strip() or not is_sha256(spec.get("sha256"))):
            add_reason("ARTIFACT_BAD_SPEC",
                       f"{role} needs a non-empty path and a valid sha256 claim")
            continue
        target = resolve_path(spec["path"], base)
        recomputed = sha256_file(target)
        if recomputed is None:
            add_reason("ARTIFACT_MISSING",
                       f"{role} cites unreadable file {spec['path']!r}")
            continue
        digests[role] = recomputed
        if spec["sha256"].lower() != recomputed:
            add_reason("ARTIFACT_SHA256_MISMATCH",
                       f"{role} claimed {spec['sha256']} but recomputed {recomputed}")
    if len(digests) == 2 and digests["baseline"] != digests["default_run"]:
        add_reason("DEFAULT_PIPELINE_CHANGED",
                   f"default-run bytes {digests['default_run']} differ from baseline "
                   f"{digests['baseline']} with the feature flag off")
    return {"flag": flag, "artifacts": digests}


def check_quality(rec: Mapping[str, Any], selections: Mapping[str, dict],
                  gates: Mapping[str, dict], base: Path, add_reason) -> dict[str, Any]:
    source = rec.get("source_quality")
    behavior = rec.get("behavior_quality")
    if not isinstance(source, Mapping) or not isinstance(behavior, Mapping):
        add_reason("QUALITY_NOT_SEPARATE",
                   "evidence needs separate source_quality and behavior_quality objects")
        return {}
    s_verdict, b_verdict = source.get("verdict"), behavior.get("verdict")
    s_basis, b_basis = source.get("basis"), behavior.get("basis")
    if s_verdict not in SOURCE_VERDICTS:
        add_reason("SOURCE_QUALITY_BAD_VERDICT",
                   f"source_quality.verdict={s_verdict!r} is not in {sorted(SOURCE_VERDICTS)}")
    if not isinstance(s_basis, str) or not s_basis.strip():
        add_reason("SOURCE_QUALITY_BASIS_MISSING",
                   "source_quality has no non-empty basis")
    if b_verdict not in BEHAVIOR_VERDICTS:
        add_reason("BEHAVIOR_QUALITY_BAD_VERDICT",
                   f"behavior_quality.verdict={b_verdict!r} is not in "
                   f"{sorted(BEHAVIOR_VERDICTS)}; a bare PASS is not a scoped behavioral claim")
    if not isinstance(b_basis, str) or not b_basis.strip():
        add_reason("BEHAVIOR_QUALITY_BASIS_MISSING",
                   "behavior_quality has no non-empty basis")
    elif b_basis != "differential_gate":
        add_reason("BEHAVIOR_NOT_GATE_BACKED",
                   f"behavior_quality.basis={b_basis!r}; expected 'differential_gate'")
    if isinstance(s_basis, str) and s_basis == b_basis:
        add_reason("QUALITY_NOT_SEPARATE",
                   f"source and behavior quality share the same basis {s_basis!r}")
    scope = behavior.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        add_reason("BEHAVIOR_SCOPE_MISSING",
                   "behavior_quality has no non-empty scope statement")
    if behavior.get("claims_full_game_equivalence") is not False:
        add_reason("FULL_GAME_EQUIVALENCE_CLAIM",
                   "behavior_quality.claims_full_game_equivalence is not boolean false")
    gate = _gate_link(rec, gates, add_reason)
    if gate is not None:
        _gate_ok(gate, add_reason)
        return {"source_quality": s_verdict, "behavior_quality": b_verdict,
                "diff_gate": gate["name"], "sha256": gate["sha256"]}
    return {"source_quality": s_verdict, "behavior_quality": b_verdict}


CHECKS = {
    "revng_selection": check_revng,
    "pcrecomp_fallback_after_revng_fail": check_fallback,
    "both_fail_unresolved": check_unresolved,
    "negative_pointer": lambda r, s, g, b, a: check_negative("negative_pointer", r, s, g, b, a),
    "negative_stack": lambda r, s, g, b, a: check_negative("negative_stack", r, s, g, b, a),
    "negative_callback": lambda r, s, g, b, a: check_negative("negative_callback", r, s, g, b, a),
    "determinism": check_determinism,
    "opt_in_no_default_regression": check_opt_in,
    "source_behavior_separation": check_quality,
}


# --------------------------------------------------------------------------
# report assembly
# --------------------------------------------------------------------------
def build_report(selections: dict[str, dict], gates: dict[str, dict],
                 evidence: Sequence[dict[str, Any]], *, inputs: dict[str, Any],
                 command: str, input_errors: Sequence[str]) -> dict[str, Any]:
    by_criterion: dict[str, list[dict[str, Any]]] = {c: [] for c in CRITERIA}
    for record in evidence:
        if record["criterion"] in by_criterion:
            by_criterion[record["criterion"]].append(record)

    rows: list[dict[str, Any]] = []
    for criterion in CRITERIA:
        reasons: list[dict[str, str]] = []
        observed: dict[str, Any] = {}
        records = by_criterion[criterion]
        label = ", ".join(f"{Path(r['path']).name}[{r['index']}]" for r in records)
        if not records:
            add(reasons, "NO_EVIDENCE",
                f"no evidence record for acceptance criterion {criterion!r}")
        elif len(records) > 1:
            add(reasons, "DUPLICATE_EVIDENCE",
                f"{len(records)} evidence records claim {criterion!r}: {label}")
        else:
            record = records[0]
            for reason in record["reasons"]:
                add(reasons, reason["code"], reason["detail"])
            if not reasons and isinstance(record["record"], Mapping):
                payload = dict(record["record"])
                payload["_label"] = label
                observed = CHECKS[criterion](
                    payload, selections, gates, Path(record["path"]).parent,
                    lambda code, detail: add(reasons, code, detail))
        rows.append({
            "criterion": criterion,
            "verdict": "PASS" if not reasons else "FAIL",
            "reason_codes": [r["code"] for r in reasons],
            "reasons": reasons,
            "evidence": label or None,
            "observed": observed,
        })

    counts = {verdict: sum(1 for r in rows if r["verdict"] == verdict)
              for verdict in ("PASS", "FAIL")}
    findings = [
        {"criterion": r["criterion"], "reason_codes": r["reason_codes"],
         "details": [reason["detail"] for reason in r["reasons"]]}
        for r in rows if r["verdict"] == "FAIL"
    ]
    overall = ("CLOSABLE" if counts["FAIL"] == 0 and not input_errors
               else "OPEN")
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "gate": GATE,
        "issue": 25,
        "overall": overall,
        "verdicts": {"CLOSABLE": 1 if overall == "CLOSABLE" else 0,
                     "OPEN": 1 if overall == "OPEN" else 0},
        "counts": counts,
        "criteria": rows,
        "findings": findings,
        "inputs": inputs,
        "input_errors": list(input_errors),
        "commands": {"exact": command},
        "limits": list(LIMITS),
    }


def md_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\r", " ").replace("\n", " ")


def md_code(value: str) -> str:
    fence = "`" * (max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def render_markdown(report: dict[str, Any]) -> str:
    out = [
        "# Issue #25 acceptance-evidence audit",
        "",
        f"- Gate: {md_code(report['gate'])}",
        f"- Generator: {md_code(report['generator'])} "
        f"(schema {report['schema_version']})",
        "",
        f"**Overall: {report['overall']}** -- this tool reports CLOSABLE or OPEN "
        "only; it does not change any issue's state.",
        "",
        "| Verdict | Count |",
        "|---|---:|",
    ]
    for verdict in VERDICTS:
        out.append(f"| {verdict} | {report['verdicts'][verdict]} |")
    out += ["", "| Criterion | Verdict | Reason codes | Evidence |",
            "|---|---|---|---|"]
    for row in report["criteria"]:
        out.append("| {c} | {v} | {codes} | {ev} |".format(
            c=md_cell(row["criterion"]), v=md_cell(row["verdict"]),
            codes=md_cell(", ".join(row["reason_codes"])),
            ev=md_cell(md_code(row["evidence"]) if row["evidence"] else "—"),
        ))
    out += ["", "## Findings", ""]
    if report["findings"] or report["input_errors"]:
        out.append("| Criterion | Reason codes | Details |")
        out.append("|---|---|---|")
        for finding in report["findings"]:
            out.append("| {c} | {codes} | {details} |".format(
                c=md_cell(finding["criterion"]),
                codes=md_cell(", ".join(finding["reason_codes"])),
                details=md_cell("; ".join(finding["details"])),
            ))
        for error in report["input_errors"]:
            out.append(f"| — | INPUT_ERROR | {md_cell(error)} |")
    else:
        out.append("None. Every supplied acceptance criterion passes the gate.")
    out += ["", "## Recomputed input hashes", "",
            "| Kind | Name | Path | SHA-256 |", "|---|---|---|---|"]
    for kind, name, spec in (
            ("selection", k, v) for k, v in report["inputs"]["selections"].items()):
        out.append(f"| {kind} | {md_cell(name)} | "
                   f"{md_cell(md_code(spec['path']))} | {md_cell(spec['sha256'] or '—')} |")
    for kind, name, spec in (
            ("diff_gate", k, v) for k, v in report["inputs"]["diff_gates"].items()):
        out.append(f"| {kind} | {md_cell(name)} | "
                   f"{md_cell(md_code(spec['path']))} | {md_cell(spec['sha256'] or '—')} |")
    for spec in report["inputs"]["evidence"]:
        out.append(f"| evidence | — | {md_cell(md_code(spec['path']))} | "
                   f"{md_cell(spec['sha256'] or '—')} |")
    out += ["", "## Reproduction command", "", md_code(report["commands"]["exact"]),
            "", "## Limits", ""]
    out.extend(f"- {limit}" for limit in report["limits"])
    out.append("")
    return "\n".join(out)


def assert_no_closing_keywords(*texts: str) -> None:
    for text in texts:
        match = CLOSING_KEYWORD.search(text)
        if match:
            raise ValueError(f"refusing to emit auto-closing keyword: {match.group(0)!r}")


def make_command(args: argparse.Namespace) -> str:
    parts = ["python3", "tools/issue25_evidence.py"]
    for spec in args.selection:
        parts += ["--selection", shlex.quote(spec)]
    for spec in args.diff_gate:
        parts += ["--diff-gate", shlex.quote(spec)]
    for spec in args.evidence:
        parts += ["--evidence", shlex.quote(str(spec))]
    parts += ["--json", shlex.quote(str(args.json_out)),
              "--out", shlex.quote(str(args.out))]
    return " ".join(parts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selection", action="append", default=[], metavar="NAME=PATH",
                        help="multilifter decision report (repeatable, priority order)")
    parser.add_argument("--diff-gate", dest="diff_gate", action="append", default=[],
                        metavar="NAME=PATH", help="differential gate report (repeatable)")
    parser.add_argument("--evidence", action="append", default=[], type=Path,
                        metavar="PATH", help="sealed acceptance evidence JSON (repeatable)")
    parser.add_argument("--json", dest="json_out", required=True, type=Path,
                        help="write the machine-readable audit here")
    parser.add_argument("--out", required=True, type=Path,
                        help="write the deterministic Markdown audit here")
    args = parser.parse_args(argv)

    input_errors: list[str] = []
    selection_paths, spec_errors = parse_specs(args.selection, "selection")
    input_errors.extend(spec_errors)
    gate_paths, spec_errors = parse_specs(args.diff_gate, "diff-gate")
    input_errors.extend(spec_errors)
    collision = sorted(set(selection_paths) & set(gate_paths))
    if collision:
        input_errors.append(f"names used for both selection and diff gate: {collision}")
        for name in collision:
            selection_paths.pop(name)
            gate_paths.pop(name)
    if not args.evidence:
        input_errors.append("no --evidence file supplied; refusing a vacuous PASS")
    for path in args.evidence:
        if not path.is_file():
            input_errors.append(f"evidence file is not a readable file: {path}")

    selections = {name: normalise_selection(name, path)
                  for name, path in selection_paths.items()}
    gates = {name: normalise_gate(name, path) for name, path in gate_paths.items()}
    evidence: list[dict[str, Any]] = []
    for path in args.evidence:
        if path.is_file():
            evidence.extend(normalise_evidence(path))

    report = build_report(
        selections, gates, evidence,
        inputs={
            "selections": {
                name: {"path": item["path"], "sha256": item["sha256"]}
                for name, item in selections.items()
            },
            "diff_gates": {
                name: {"path": item["path"], "sha256": item["sha256"]}
                for name, item in gates.items()
            },
            "evidence": [{"path": item["path"], "sha256": item["sha256"]}
                         for item in evidence],
        },
        command=make_command(args),
        input_errors=input_errors,
    )

    json_text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    markdown = render_markdown(report)
    try:
        assert_no_closing_keywords(json_text, markdown)
    except ValueError as exc:
        print(f"issue25_evidence: {exc}", file=sys.stderr)
        return 2
    try:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json_text, encoding="utf-8")
        args.out.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        print(f"issue25_evidence: cannot write report: {exc}", file=sys.stderr)
        return 2

    counts = report["counts"]
    print(f"wrote {args.out} and {args.json_out} "
          f"(PASS={counts['PASS']}, FAIL={counts['FAIL']}, overall={report['overall']}; "
          "no issue state was changed by this tool)")
    return 0 if report["overall"] == "CLOSABLE" else 1


if __name__ == "__main__":
    sys.exit(main())
