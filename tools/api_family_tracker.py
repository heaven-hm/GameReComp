#!/usr/bin/env python3
"""Evidence-led per-family API status tracker (CLI-supplied JSON only).

This tool reads exactly two JSON documents supplied on the command line:

* **coverage JSON** - behavior evidence for individual APIs (normally derived
  from ``tools.api_coverage`` output *enriched* with real selftest / frame-proof
  / oracle / runtime-trace references); and
* **family definition JSON** - the authoritative list of families and the APIs
  each family is expected to provide, plus optional integrity pins.

It writes a deterministic JSON report and a deterministic Markdown report.  It
never scans the repository, the corpus, the game or the network: every verdict
comes from the two supplied documents and nothing else.

Honesty rules
-------------
* A family is ``implemented`` only when **every** API it declares carries valid
  behavior evidence.  ``partial`` means some APIs have evidence and some do not;
  ``not_implemented`` means none do; ``deferred`` means the family is game-owned
  and therefore explicitly out of scope.  An explicit ``not implemented`` list
  is emitted for every family, including deferred ones.
* ``registered`` / ``implemented`` / ``status`` flags, registration counts and
  bare API names are **ignored**.  They cannot manufacture evidence or status.
  The status in every output row is recomputed from evidence and is labelled
  ``status_source: recomputed_from_evidence``.
* Missing evidence is an honest gap, never a pass.  **Malformed** evidence (not
  an object, a trivial token, a failing verdict, or a digest mismatch) fails
  closed: the CLI exits 2 and writes no report at all.
* **No aggregate coverage percentage is emitted anywhere**, so no deferred
  family or explicit gap can be hidden by a blanket number.  Exit 0 means the
  honest report was written; it is not a completion/PASS claim.

Family definition schema (strict; anything else is rejected)
-------------------------------------------------------------

::

    {
      "schema_version": "1.0.0",
      "coverage_sha256": "<optional: sha256 of the raw coverage file bytes>",
      "families": [
        {
          "family": "kernel32.dll",
          "kind": "system",                  # "system" | "game-owned"
          "status": "implemented",           # optional UNTRUSTED hint, ignored
          "apis": [
            {
              "name": "ExitProcess",
              "evidence_sha256": "<optional sha256 pin of the evidence object>"
            }
          ]
        }
      ]
    }

``kind: game-owned`` makes the family ``deferred``; no flag can override that.
A non-empty ``apis`` list is required for every family (a vacuously empty
family is never reported as ``implemented``).

Coverage schema (strict; anything else is rejected)
----------------------------------------------------

Both forms may be mixed.  Duplicate API names with conflicting evidence or
family declarations are rejected as ambiguous.

::

    {
      "schema_version": "1.2.0",
      "apis": [
        {
          "name": "ExitProcess",
          "family": "kernel32.dll",          # optional cross-check
          "evidence": {"selftest": "tests/test_win32hle.py::test_exit_process"},
          "implemented": true,               # UNTRUSTED, ignored
          "registered": true                 # UNTRUSTED, ignored
        }
      ]
    }

    {
      "schema_version": "1.2.0",
      "families": [
        {
          "family": "kernel32.dll",
          "apis": [{"name": "ExitProcess", "evidence": {"oracle": "reports/x.json"}}],
          "not_implemented": [{"name": "BrandNewApi", "files": 2}]
        }
      ]
    }

Evidence keys that count as behavior proof: ``selftest``, ``test``, ``test_id``,
``frame_proof``, ``oracle``, ``oracle_result``, ``runtime_trace``, ``witness``,
``artifact``.  Each must be a non-empty, non-trivial reference string (or a
non-empty list of such strings).  An evidence object whose ``result`` /
``status`` / ``verdict`` says fail/error/skip is rejected.  Flags are never
evidence.  An ``evidence_sha256`` pin is SHA-256 over the canonical JSON of the
evidence object (sorted keys, compact separators); a mismatch means tampered
evidence and fails closed.

Usage
-----

::

    python3 -m tools.api_family_tracker \
        --coverage /path/to/coverage-enriched.json \
        --families /path/to/families.json \
        --json-out /path/to/family-status.json \
        --markdown-out /path/to/family-status.md

    # Positional inputs; output paths default to the coverage path with
    # ".family-tracker.json" / ".family-tracker.md" appended.
    python3 tools/api_family_tracker.py coverage.json families.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = "1.0.0"

KNOWN_KINDS = frozenset({"system", "game-owned"})
DEFERRED_KINDS = frozenset({"game-owned"})
STATUSES = ("implemented", "partial", "not_implemented", "deferred")
STATUS_ORDER = {status: index for index, status in enumerate(STATUSES)}

# Only these keys qualify as behavior evidence.  Registration/verdict flags
# are deliberately excluded: they are the fabrication this tool defends against.
EVIDENCE_PROOF_KEYS = (
    "selftest", "test", "test_id", "frame_proof", "oracle", "oracle_result",
    "runtime_trace", "witness", "artifact",
)
TRIVIAL_REFERENCES = frozenset({
    "pass", "passed", "true", "yes", "1", "ok", "implemented", "registered",
    "done", "none", "n/a", "na",
})
FAIL_RESULTS = frozenset({"fail", "failed", "failure", "false", "error",
                          "skip", "skipped", "none"})
VERDICT_KEYS = ("result", "status", "verdict")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")

# Deterministic placeholder: no wall-clock time in the default output.
NO_TIMESTAMP = "not time-stamped (deterministic)"


class InputError(ValueError):
    """Malformed, ambiguous or untrustworthy input (fail closed)."""


# --------------------------------------------------------------------------- #
# Loading / canonical hashing
# --------------------------------------------------------------------------- #

def load_json(path: Path | str, label: str) -> tuple[dict, str]:
    """Read and JSON-parse ``path``; return ``(document, sha256-of-raw-bytes)``.

    Any read, decode or parse failure raises :class:`InputError` so the CLI
    fails closed instead of guessing.
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read {label} {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(f"{label} {path} is not UTF-8: {exc}") from exc
    try:
        doc = json.loads(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise InputError(f"{label} {path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise InputError(f"{label} {path}: top level must be a JSON object")
    return doc, hashlib.sha256(raw).hexdigest()


def canonical_bytes(value) -> bytes:
    """Stable byte encoding used for evidence digest pins."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def evidence_digest(evidence) -> str:
    """SHA-256 pin of an evidence object, independent of key insertion order."""
    return hashlib.sha256(canonical_bytes(evidence)).hexdigest()


def _nonempty_string(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{where} must be a non-empty string")
    return value.strip()


def _sha256_pin(value, where: str) -> str | None:
    if value is None:
        return None
    pin = _nonempty_string(value, where).lower()
    if not SHA256_RE.match(pin):
        raise InputError(f"{where} must be a 64-character SHA-256 hex digest")
    return pin


def _is_reference(value) -> bool:
    """True for a concrete, non-trivial behavior-evidence reference."""
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in TRIVIAL_REFERENCES
    if isinstance(value, list):
        return bool(value) and all(_is_reference(item) for item in value)
    return False


def evidence_supports_implementation(evidence) -> bool:
    """True only for explicit behavior evidence, never for a flag.

    The function is intentionally total (never raises): validation in
    :func:`validate_evidence` is what turns a malformed object into a hard
    failure.  Here a malformed/failed object simply does not qualify.
    """
    if not isinstance(evidence, dict):
        return False
    for key in VERDICT_KEYS:
        value = evidence.get(key)
        if isinstance(value, str) and value.strip().lower() in FAIL_RESULTS:
            return False
    return any(_is_reference(evidence.get(key)) for key in EVIDENCE_PROOF_KEYS)


def validate_evidence(evidence, where: str) -> dict | None:
    """Validate *present* evidence; absence (``None``) is an honest gap.

    Raises :class:`InputError` for anything malformed, self-contradictory or
    flag-only.  Returning ``None`` means "no evidence was supplied", which the
    status rules handle as a gap rather than an input error.
    """
    if evidence is None:
        return None
    if not isinstance(evidence, dict):
        raise InputError(f"{where}.evidence must be an object when present")
    for key in VERDICT_KEYS:
        value = evidence.get(key)
        if isinstance(value, str) and value.strip().lower() in FAIL_RESULTS:
            raise InputError(f"{where}.evidence has failing {key}={value!r}")
    if not evidence_supports_implementation(evidence):
        raise InputError(
            f"{where}.evidence has no valid behavior-evidence reference; "
            "flags, bare names and trivial tokens are not evidence")
    for key in EVIDENCE_PROOF_KEYS:
        if key in evidence and not _is_reference(evidence[key]):
            raise InputError(f"{where}.evidence.{key} must be a non-empty "
                             "reference string or a list of them")
    return evidence


# --------------------------------------------------------------------------- #
# Input validation / normalisation
# --------------------------------------------------------------------------- #

def validate_definition(doc: dict) -> dict:
    """Validate the family definition; return a normalized internal structure.

    Untrusted hint keys (``status``, ``registered``, ``implemented``,
    ``critical``, ...) are accepted for compatibility but never influence a
    verdict.
    """
    _nonempty_string(doc.get("schema_version"), "schema_version")
    coverage_pin = _sha256_pin(doc.get("coverage_sha256"), "coverage_sha256")
    raw_families = doc.get("families")
    if not isinstance(raw_families, list) or not raw_families:
        raise InputError("families must be a non-empty list")

    families: list[dict] = []
    seen_families: set[str] = set()
    for fi, raw_family in enumerate(raw_families):
        where = f"families[{fi}]"
        if not isinstance(raw_family, dict):
            raise InputError(f"{where} must be an object")
        name = _nonempty_string(raw_family.get("family"), f"{where}.family")
        if name in seen_families:
            raise InputError(f"duplicate family {name!r}; refuse ambiguous input")
        seen_families.add(name)

        kind = _nonempty_string(raw_family.get("kind"), f"{where}.kind").lower().replace("_", "-")
        if kind not in KNOWN_KINDS:
            raise InputError(f"{where}.kind: unknown kind {raw_family.get('kind')!r}; "
                             f"expected one of {sorted(KNOWN_KINDS)}")

        raw_apis = raw_family.get("apis")
        if not isinstance(raw_apis, list) or not raw_apis:
            raise InputError(f"{where}.apis must be a non-empty list; an empty "
                             "family is never implemented")
        apis: list[dict] = []
        seen_apis: set[str] = set()
        for ai, raw_api in enumerate(raw_apis):
            awhere = f"{where}.apis[{ai}]"
            if not isinstance(raw_api, dict):
                raise InputError(f"{awhere} must be an object")
            api_name = _nonempty_string(raw_api.get("name"), f"{awhere}.name")
            if api_name in seen_apis:
                raise InputError(f"duplicate API {api_name!r} in family {name!r}")
            seen_apis.add(api_name)
            apis.append({
                "name": api_name,
                "evidence_sha256": _sha256_pin(raw_api.get("evidence_sha256"),
                                               f"{awhere}.evidence_sha256"),
            })
        families.append({"family": name, "kind": kind, "apis": apis})
    return {"coverage_sha256": coverage_pin, "families": families}


def _ingest_api_record(raw, where: str, declared_family: str | None,
                       records: dict[str, dict]) -> None:
    if not isinstance(raw, dict):
        raise InputError(f"{where} must be an object")
    name = _nonempty_string(raw.get("name"), f"{where}.name")
    family = None
    if raw.get("family") is not None:
        family = _nonempty_string(raw["family"], f"{where}.family")
    evidence = validate_evidence(raw.get("evidence"), where)
    # Flags such as implemented/registered/status are deliberately ignored.
    record = {"family": declared_family or family, "evidence": evidence}
    if name in records:
        previous = records[name]
        if previous["family"] and record["family"] and previous["family"] != record["family"]:
            raise InputError(f"conflicting family declarations for API {name!r}")
        if canonical_bytes(previous["evidence"]) != canonical_bytes(record["evidence"]):
            raise InputError(f"conflicting evidence for API {name!r}; refuse ambiguous input")
        record["family"] = previous["family"] or record["family"]
    records[name] = record


def validate_coverage(doc: dict) -> dict:
    """Validate the coverage/evidence document; return normalized records.

    Explicit ``not_implemented`` entries are collected as *labels* only: they
    tag a missing API as ``explicit_gap`` instead of ``no_behavior_evidence``
    and never change the status by themselves.
    """
    _nonempty_string(doc.get("schema_version"), "schema_version")
    raw_apis = doc.get("apis", [])
    raw_families = doc.get("families", [])
    if not isinstance(raw_apis, list):
        raise InputError("apis must be a list when present")
    if not isinstance(raw_families, list):
        raise InputError("families must be a list when present")
    # An empty evidence document is valid: every declared API is an honest gap,
    # never a pass.  Only a pinned API makes missing evidence a hard failure
    # (see build_report).

    records: dict[str, dict] = {}
    for ai, raw_api in enumerate(raw_apis):
        _ingest_api_record(raw_api, f"apis[{ai}]", None, records)
    gaps: dict[str, set[str]] = {}
    for fi, raw_family in enumerate(raw_families):
        fwhere = f"families[{fi}]"
        if not isinstance(raw_family, dict):
            raise InputError(f"{fwhere} must be an object")
        family = _nonempty_string(raw_family.get("family"), f"{fwhere}.family")
        raw_family_apis = raw_family.get("apis", [])
        if not isinstance(raw_family_apis, list):
            raise InputError(f"{fwhere}.apis must be a list")
        for ai, raw_api in enumerate(raw_family_apis):
            _ingest_api_record(raw_api, f"{fwhere}.apis[{ai}]", family, records)
        raw_missing = raw_family.get("not_implemented", [])
        if not isinstance(raw_missing, list):
            raise InputError(f"{fwhere}.not_implemented must be a list")
        for mi, raw_gap in enumerate(raw_missing):
            mwhere = f"{fwhere}.not_implemented[{mi}]"
            if isinstance(raw_gap, dict):
                gap_name = _nonempty_string(raw_gap.get("name"), f"{mwhere}.name")
            else:
                gap_name = _nonempty_string(raw_gap, mwhere)
            gaps.setdefault(family, set()).add(gap_name)
    return {"records": records, "gaps": gaps}


# --------------------------------------------------------------------------- #
# Status decisions / report
# --------------------------------------------------------------------------- #

def _reason(name: str, family: str, gaps: dict[str, set[str]],
            coverage: dict) -> str:
    if name in gaps.get(family, set()):
        return "explicit_gap"
    if coverage["records"].get(name, {}).get("evidence") is None:
        return "no_behavior_evidence"
    return "evidenced"


def build_report(definition: dict, coverage: dict, *,
                 coverage_path: Path | str | None = None,
                 coverage_sha256: str | None = None,
                 definition_path: Path | str | None = None,
                 definition_sha256: str | None = None,
                 generated_at: str | None = None) -> dict:
    """Build the deterministic, evidence-led per-family status report.

    Every status is recomputed from the supplied behavior evidence.  Pinned
    evidence is verified here; a missing or mismatched pin raises
    :class:`InputError` (fail closed) rather than producing a report.
    """
    defn = validate_definition(definition)
    cov = validate_coverage(coverage)

    if defn["coverage_sha256"] is not None:
        if coverage_sha256 is None:
            raise InputError("coverage_sha256 pin needs a CLI-supplied coverage file")
        if defn["coverage_sha256"] != coverage_sha256.lower():
            raise InputError("coverage_sha256 pin mismatch: coverage JSON was modified")

    expected: dict[str, tuple[str, str | None]] = {}
    for family in defn["families"]:
        for api in family["apis"]:
            expected[api["name"]] = (family["family"], api["evidence_sha256"])

    for name, record in cov["records"].items():
        if name not in expected:
            raise InputError(f"coverage API {name!r} is not declared in the family definition")
        family_name, _pin = expected[name]
        if record["family"] and record["family"] != family_name:
            raise InputError(f"coverage API {name!r} declares family "
                             f"{record['family']!r}, expected {family_name!r}")

    for name, (_family_name, pin) in expected.items():
        evidence = cov["records"].get(name, {}).get("evidence")
        if pin is None:
            continue
        if evidence is None:
            raise InputError(f"pinned evidence missing for API {name!r}; fail closed")
        actual = evidence_digest(evidence)
        if actual != pin:
            raise InputError(f"tampered evidence for API {name!r}: "
                             f"expected sha256 {pin}, got {actual}")

    output_families: list[dict] = []
    counts = {"families": 0, "apis": 0, "implemented": 0, "partial": 0,
              "not_implemented": 0, "deferred": 0, "missing_apis": 0}
    for family in defn["families"]:
        implemented_rows: list[dict] = []
        missing_rows: list[dict] = []
        for api in family["apis"]:
            name = api["name"]
            reason = _reason(name, family["family"], cov["gaps"], cov)
            if reason == "evidenced":
                implemented_rows.append({
                    "name": name,
                    "evidence": cov["records"][name]["evidence"],
                    "evidence_sha256": evidence_digest(cov["records"][name]["evidence"]),
                })
            else:
                missing_rows.append({"name": name, "reason": reason})
        implemented_rows.sort(key=lambda row: row["name"])
        missing_rows.sort(key=lambda row: row["name"])

        if family["kind"] in DEFERRED_KINDS:
            status = "deferred"
        elif not missing_rows:
            status = "implemented"
        elif implemented_rows:
            status = "partial"
        else:
            status = "not_implemented"

        counts["families"] += 1
        counts["apis"] += len(family["apis"])
        counts[status] += 1
        counts["missing_apis"] += len(missing_rows)
        output_families.append({
            "family": family["family"],
            "kind": family["kind"],
            "critical": family["kind"] == "system",
            "status": status,
            "status_source": "recomputed_from_evidence",
            "implemented": implemented_rows,
            "missing_apis": missing_rows,
            "implemented_count": len(implemented_rows),
            "missing_count": len(missing_rows),
        })

    output_families.sort(key=lambda f: (STATUS_ORDER[f["status"]],
                                        -f["missing_count"], f["family"]))
    deferred = sorted(f["family"] for f in output_families
                      if f["status"] == "deferred")
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "python3 -m tools.api_family_tracker",
        "coverage": str(coverage_path) if coverage_path is not None else None,
        "coverage_sha256": coverage_sha256,
        "family_definition": str(definition_path) if definition_path is not None else None,
        "family_definition_sha256": definition_sha256,
        "generated_at": generated_at or NO_TIMESTAMP,
        "policy": {
            "status_rule": ("implemented requires evidence for every declared API; "
                            "partial means some APIs have evidence; not_implemented "
                            "means none do; game-owned families are deferred"),
            "evidence_rule": ("a selftest/test/test_id/frame_proof/oracle/"
                              "oracle_result/runtime_trace/witness/artifact reference "
                              "is required; flags, bare names and trivial tokens never "
                              "qualify; malformed evidence and pin mismatches fail closed"),
            "no_percentage": True,
            "completion_claim": False,
        },
        "counts": counts,
        "families": output_families,
        "deferred_families": deferred,
        "verdict": ("REPORT ONLY - statuses recomputed from supplied behavior evidence; "
                    "not a completion claim"),
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def render_json(report: dict) -> str:
    """Canonical JSON text: sorted keys, stable ordering, trailing newline."""
    return json.dumps(report, indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"


def render_markdown(report: dict) -> str:
    """Deterministic Markdown report: explicit missing APIs, no percentages."""
    c = report["counts"]
    p = report["policy"]
    L: list[str] = []
    L.append("# API family status (evidence-led, CLI-supplied JSON)")
    L.append("")
    L.append(f"- Coverage input: `{report['coverage']}`")
    L.append(f"- Coverage SHA-256: `{report['coverage_sha256']}`")
    L.append(f"- Family definition: `{report['family_definition']}`")
    L.append(f"- Family definition SHA-256: `{report['family_definition_sha256']}`")
    L.append(f"- Schema: `{report['schema_version']}`")
    L.append(f"- Generated: `{report['generated_at']}`")
    L.append(f"- Tool: `{report['tool']}`")
    L.append("")
    L.append(f"**Verdict:** {report['verdict']}.")
    L.append("")
    L.append(f"**Status rule:** {p['status_rule']}.")
    L.append("")
    L.append(f"**Evidence rule:** {p['evidence_rule']}.")
    L.append("")
    L.append("Registration only proves a name is present. This report makes no "
             "completion claim and reports no aggregate percentage, so no "
             "deferred family or gap can be hidden by a headline number.")
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append("| Metric | Value |")
    L.append("|---|---|")
    L.append(f"| Families | {c['families']} |")
    L.append(f"| Declared APIs | {c['apis']} |")
    L.append(f"| Implemented families | {c['implemented']} |")
    L.append(f"| Partial families | {c['partial']} |")
    L.append(f"| Not-implemented families | {c['not_implemented']} |")
    L.append(f"| Deferred families | {c['deferred']} |")
    L.append(f"| Explicit missing APIs | {c['missing_apis']} |")
    L.append("")
    L.append("## Per-family status (explicit)")
    L.append("")
    L.append("| Family | Kind | Status | Implemented APIs | Missing APIs |")
    L.append("|---|---|---|---|---|")
    for fam in report["families"]:
        impl = ", ".join(f"`{row['name']}`" for row in fam["implemented"]) or "(none)"
        missing = ", ".join(f"`{row['name']}`" for row in fam["missing_apis"]) or "(none)"
        L.append(f"| `{fam['family']}` | {fam['kind']} | {fam['status']} | "
                 f"{impl} | {missing} |")
    L.append("")
    for fam in report["families"]:
        L.append(f"### `{fam['family']}` - {fam['status']} "
                 f"({fam['kind']}{', critical' if fam['critical'] else ''})")
        L.append("")
        L.append("- Status source: "
                 f"`{fam['status_source']}` (input status flags are ignored)")
        L.append("- Implemented (behavior evidence):")
        if fam["implemented"]:
            for row in fam["implemented"]:
                evidence = json.dumps(row["evidence"], sort_keys=True)
                L.append(f"  - `{row['name']}` - sha256: "
                         f"`{row['evidence_sha256']}`, evidence: `{evidence}`")
        else:
            L.append("  - (none)")
        L.append("- Missing APIs (explicit):")
        if fam["missing_apis"]:
            for row in fam["missing_apis"]:
                L.append(f"  - `{row['name']}` - reason: `{row['reason']}`")
        else:
            L.append("  - (none)")
        L.append("")
    deferred = report["deferred_families"]
    L.append("## Deferred families")
    L.append("")
    if deferred:
        L.append("Deferred families (explicit, never hidden by a percentage): "
                 + ", ".join(f"`{d}`" for d in deferred) + ".")
    else:
        L.append("No deferred families.")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("coverage_pos", nargs="?", metavar="COVERAGE",
                   help="coverage/evidence JSON (alternative to --coverage)")
    p.add_argument("definition_pos", nargs="?", metavar="FAMILIES",
                   help="family definition JSON (alternative to --families)")
    p.add_argument("--coverage", dest="coverage_opt", type=Path,
                   help="coverage/evidence JSON to read (required)")
    p.add_argument("--families", dest="definition_opt", type=Path,
                   help="family definition JSON to read (required)")
    p.add_argument("--json-out", type=Path, default=None,
                   help="JSON report path (default: <coverage>.family-tracker.json)")
    p.add_argument("--markdown-out", type=Path, default=None,
                   help="Markdown report path (default: <coverage>.family-tracker.md)")
    p.add_argument("--generated-at", default=None,
                   help="optional provenance timestamp; omitted by default so "
                        "output stays deterministic")
    args = p.parse_args(argv)

    coverage_path = args.coverage_opt or args.coverage_pos
    definition_path = args.definition_opt or args.definition_pos
    if coverage_path is None or definition_path is None:
        print("api_family_tracker: --coverage and --families (or both positional "
              "paths) are required", file=sys.stderr)
        return 2
    coverage_path = Path(coverage_path)
    definition_path = Path(definition_path)
    json_out = args.json_out or coverage_path.with_suffix(
        coverage_path.suffix + ".family-tracker.json")
    markdown_out = args.markdown_out or coverage_path.with_suffix(
        coverage_path.suffix + ".family-tracker.md")

    try:
        coverage_doc, coverage_digest = load_json(coverage_path, "coverage JSON")
        definition_doc, definition_digest = load_json(definition_path,
                                                       "family definition JSON")
        report = build_report(
            definition_doc, coverage_doc,
            coverage_path=coverage_path, coverage_sha256=coverage_digest,
            definition_path=definition_path, definition_sha256=definition_digest,
            generated_at=args.generated_at)
        markdown = render_markdown(report)
        json_text = render_json(report)
    except InputError as exc:
        print(f"api_family_tracker: {exc}", file=sys.stderr)
        return 2

    # Defense in depth: no blanket percentage may escape into an artifact,
    # even if a future renderer change introduces one.
    if PERCENT_RE.search(markdown) or PERCENT_RE.search(json_text):
        print("api_family_tracker: internal error: percentage in output",
              file=sys.stderr)
        return 2

    try:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json_text, encoding="utf-8")
        markdown_out.parent.mkdir(parents=True, exist_ok=True)
        markdown_out.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        print(f"api_family_tracker: cannot write output: {exc}", file=sys.stderr)
        return 2

    c = report["counts"]
    print(f"wrote {json_out} and {markdown_out} "
          f"({c['families']} families, {c['implemented']} implemented, "
          f"{c['partial']} partial, {c['not_implemented']} not implemented, "
          f"{c['deferred']} deferred, {c['missing_apis']} explicit missing APIs); "
          "not a completion claim")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
