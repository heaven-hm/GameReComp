#!/usr/bin/env python3
"""Deterministic per-family missing-API report from a CLI-supplied coverage JSON.

This tool never scans the repository, the corpus or the game.  It reads exactly
one JSON document supplied on the command line (normally the output of
``tools.api_coverage``, optionally enriched with per-API behavior evidence) and
writes two deterministic artifacts: a JSON report and a Markdown report.

Honesty rules
-------------
* An API is ``implemented`` **only** when its record carries behavior evidence
  (a selftest / test id / frame-proof / oracle / runtime-trace / witness
  reference).  A ``registered`` flag, an ``implemented`` flag, a top-level
  ``status: "implemented"`` hint or a bare name are all ignored.  Registration
  proves a name is present, not that the function behaves correctly.
* Every family is reported with an explicit ``not_implemented``,
  ``deferred`` or ``implemented`` status.  A family with any gap is
  ``not_implemented`` even if some of its APIs have evidence.
* Game-owned families are ``deferred`` and are always listed explicitly.
* No aggregate coverage percentage is emitted anywhere.  A blanket percentage
  would hide deferred families and gaps.
* Malformed input fails closed: the CLI exits 2 and writes no report.  A
  successfully generated report is **not** a completion/PASS claim; exit 0
  only means the report was written.

Input schema (strict; anything else is rejected)
------------------------------------------------
::

    {
      "schema_version": "1.2.0",          # required, non-empty string
      "families": [                       # required, non-empty list
        {
          "family": "kernel32.dll",       # required, non-empty string
          "kind": "system",               # required: "system" | "game-owned"
          "critical": true,               # optional bool (derived from kind)
          "status": "implemented",        # optional, UNTRUSTED hint, ignored
          "apis": [                       # optional list
            {
              "name": "ExitProcess",      # required, non-empty string
              "registered": true,         # optional, UNTRUSTED, ignored
              "implemented": true,        # optional, UNTRUSTED, ignored
              "evidence": {               # optional; required for implemented
                "selftest": "tests/test_win32hle.py::test_exit_process"
              }
            }
          ],
          "not_implemented": [            # optional list
            {"name": "BrandNewApi", "files": 1}
          ]
        }
      ]
    }

Evidence keys that count as behavior proof: ``selftest``, ``test``,
``test_id``, ``frame_proof``, ``oracle``, ``oracle_result``, ``runtime_trace``,
``witness``, ``artifact``.  Each must be a non-empty reference string (or a
non-empty list of such strings).  ``registered`` / ``implemented`` / ``status``
keys are explicitly *not* evidence.  An evidence object whose ``result`` or
``status`` says fail/error/skip does not qualify.

Usage
-----
::

    python3 -m tools.api_missing_report --input /path/to/api-coverage.json \
        --json-out /path/to/missing.json \
        --markdown-out /path/to/missing.md

    # Output paths default to <input>.missing.json / <input>.missing.md.
    python3 tools/api_missing_report.py --input /path/to/api-coverage.json
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
STATUSES = ("not_implemented", "deferred", "implemented")

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
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")

# Deterministic placeholder: no wall-clock time in the default output.
NO_TIMESTAMP = "not time-stamped (deterministic)"


class InputError(ValueError):
    """Malformed or untrustworthy input document (fail closed)."""


# --------------------------------------------------------------------------- #
# Input loading / validation
# --------------------------------------------------------------------------- #

def load_input(path: Path | str) -> tuple[dict, str]:
    """Read and JSON-parse ``path``; return (document, sha256-of-raw-bytes).

    Any read or parse failure raises :class:`InputError` so callers fail closed.
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read input {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(f"input {path} is not UTF-8: {exc}") from exc
    try:
        doc = json.loads(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise InputError(f"input {path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise InputError(f"input {path}: top level must be a JSON object")
    return doc, hashlib.sha256(raw).hexdigest()


def _nonempty_string(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{where} must be a non-empty string")
    return value.strip()


def _normalize_kind(value, where: str) -> str:
    kind = _nonempty_string(value, where).lower().replace("_", "-")
    if kind not in KNOWN_KINDS:
        raise InputError(f"{where}: unknown kind {value!r}; "
                         f"expected one of {sorted(KNOWN_KINDS)}")
    return kind


def _files_of(record: dict, where: str) -> int | None:
    if "files" not in record:
        return None
    value = record["files"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InputError(f"{where}.files must be a non-negative integer")
    return value


def validate_document(doc: dict) -> dict:
    """Validate the input document and return a normalized internal structure.

    ``status`` / ``registered`` / ``implemented`` hints are validated for shape
    only and never influence the verdict.
    """
    _nonempty_string(doc.get("schema_version"), "schema_version")
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
        kind = _normalize_kind(raw_family.get("kind"), f"{where}.kind")

        critical = kind == "system"
        if "critical" in raw_family:
            value = raw_family["critical"]
            if not isinstance(value, bool):
                raise InputError(f"{where}.critical must be a boolean")
            critical = value
        if "status" in raw_family and not isinstance(raw_family["status"], str):
            raise InputError(f"{where}.status hint must be a string")

        raw_apis = raw_family.get("apis", [])
        if not isinstance(raw_apis, list):
            raise InputError(f"{where}.apis must be a list")
        raw_missing = raw_family.get("not_implemented", [])
        if not isinstance(raw_missing, list):
            raise InputError(f"{where}.not_implemented must be a list")
        if not raw_apis and not raw_missing:
            raise InputError(f"{where}: supply apis and/or not_implemented entries")

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
            evidence = raw_api.get("evidence")
            if evidence is not None and not isinstance(evidence, dict):
                raise InputError(f"{awhere}.evidence must be an object when present")
            apis.append({
                "name": api_name,
                "evidence": dict(evidence) if isinstance(evidence, dict) else None,
                "files": _files_of(raw_api, awhere),
            })

        missing: list[dict] = []
        seen_missing: set[str] = set()
        for mi, raw_gap in enumerate(raw_missing):
            mwhere = f"{where}.not_implemented[{mi}]"
            if not isinstance(raw_gap, dict):
                raise InputError(f"{mwhere} must be an object")
            gap_name = _nonempty_string(raw_gap.get("name"), f"{mwhere}.name")
            if gap_name in seen_missing:
                raise InputError(f"duplicate not_implemented API {gap_name!r} "
                                 f"in family {name!r}")
            seen_missing.add(gap_name)
            missing.append({"name": gap_name,
                            "files": _files_of(raw_gap, mwhere)})

        families.append({
            "family": name,
            "kind": kind,
            "critical": critical,
            "apis": apis,
            "not_implemented": missing,
        })
    return {"families": families}


# --------------------------------------------------------------------------- #
# Evidence / status decisions
# --------------------------------------------------------------------------- #

def _is_reference(value) -> bool:
    """True for a concrete, non-trivial behavior-evidence reference."""
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in TRIVIAL_REFERENCES
    if isinstance(value, list):
        return bool(value) and all(_is_reference(item) for item in value)
    return False


def evidence_supports_implementation(evidence) -> bool:
    """True only for explicit behavior evidence, never for a flag.

    A fabricated ``{"implemented": true}`` / ``{"registered": true}`` has no
    proof key and is rejected.  Missing evidence (``None``) is also rejected,
    which is the meta-test for this tool.
    """
    if not isinstance(evidence, dict):
        return False
    for key in ("result", "status"):
        if key in evidence:
            value = evidence[key]
            if isinstance(value, str) and value.strip().lower() in FAIL_RESULTS:
                return False
    return any(_is_reference(evidence.get(key)) for key in EVIDENCE_PROOF_KEYS)


def _reason(api: dict, missing_names: set[str]) -> str:
    if api["name"] in missing_names:
        return "explicit_gap"
    if not evidence_supports_implementation(api["evidence"]):
        return "no_behavior_evidence"
    return "evidenced"


def build_report(doc: dict, *, input_path: Path | str | None = None,
                 input_sha256: str | None = None,
                 generated_at: str | None = None) -> dict:
    """Build the deterministic, evidence-led missing-API report."""
    normalized = validate_document(doc)
    output_families: list[dict] = []
    total_apis = implemented_total = not_implemented_total = 0

    for family in normalized["families"]:
        missing_names = {row["name"] for row in family["not_implemented"]}
        files_by_name = {row["name"]: row["files"]
                         for row in family["not_implemented"]}

        implemented_rows: list[dict] = []
        not_implemented_rows: list[dict] = []
        for api in family["apis"]:
            reason = _reason(api, missing_names)
            row = {"name": api["name"], "reason": reason,
                   "files": files_by_name.get(api["name"], api["files"])}
            if reason == "evidenced":
                row["evidence"] = api["evidence"]
                implemented_rows.append(row)
            else:
                not_implemented_rows.append(row)

        for gap in family["not_implemented"]:
            if gap["name"] not in {api["name"] for api in family["apis"]}:
                not_implemented_rows.append({
                    "name": gap["name"], "reason": "explicit_gap",
                    "files": gap["files"],
                })

        implemented_rows.sort(key=lambda r: r["name"])
        not_implemented_rows.sort(key=lambda r: r["name"])
        if family["kind"] in DEFERRED_KINDS:
            status = "deferred"
        elif not_implemented_rows:
            status = "not_implemented"
        else:
            status = "implemented"

        total_apis += len(implemented_rows) + len(not_implemented_rows)
        implemented_total += len(implemented_rows)
        not_implemented_total += len(not_implemented_rows)
        output_families.append({
            "family": family["family"],
            "kind": family["kind"],
            "critical": family["critical"],
            "status": status,
            "status_source": "recomputed_from_evidence",
            "implemented_count": len(implemented_rows),
            "not_implemented_count": len(not_implemented_rows),
            "implemented": implemented_rows,
            "not_implemented": not_implemented_rows,
        })

    output_families.sort(key=lambda f: (STATUSES.index(f["status"]),
                                        -f["not_implemented_count"],
                                        f["family"]))
    deferred = sorted(f["family"] for f in output_families
                      if f["status"] == "deferred")
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "python3 -m tools.api_missing_report",
        "input": str(input_path) if input_path is not None else None,
        "input_sha256": input_sha256,
        "generated_at": generated_at or NO_TIMESTAMP,
        "policy": {
            "status_rule": ("implemented requires behavior evidence; a registration "
                            "flag or status hint is ignored; any gap makes the "
                            "family not_implemented; game-owned families are deferred"),
            "evidence_rule": ("a selftest/test/test_id/frame_proof/oracle/"
                              "oracle_result/runtime_trace/witness/artifact reference "
                              "is required; flags never qualify"),
            "no_percentage": True,
            "completion_claim": False,
        },
        "counts": {
            "families": len(output_families),
            "apis": total_apis,
            "implemented": implemented_total,
            "not_implemented": not_implemented_total,
            "deferred_families": len(deferred),
            "critical_families": sum(1 for f in output_families if f["critical"]),
        },
        "families": output_families,
        "deferred_families": deferred,
        "verdict": ("INCOMPLETE - registration alone is not implementation evidence; "
                    "see per-family not_implemented entries"),
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def render_markdown(report: dict) -> str:
    """Render a deterministic Markdown report; no percentages, no PASS claim."""
    c = report["counts"]
    p = report["policy"]
    L: list[str] = []
    L.append("# Missing API report (CLI-supplied coverage JSON)")
    L.append("")
    L.append(f"- Input: `{report['input']}`")
    L.append(f"- Input SHA-256: `{report['input_sha256']}`")
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
    L.append(f"| APIs | {c['apis']} |")
    L.append(f"| Implemented (behavior evidence) | {c['implemented']} |")
    L.append(f"| Not implemented (no evidence or explicit gap) | "
             f"{c['not_implemented']} |")
    L.append(f"| Deferred families | {c['deferred_families']} |")
    L.append(f"| Critical (system) families | {c['critical_families']} |")
    L.append("")
    L.append("## Per-family status (explicit)")
    L.append("")
    L.append("| Family | Kind | Status | Implemented | Not implemented |")
    L.append("|---|---|---|---|---|")
    for fam in report["families"]:
        impl = ", ".join(f"`{r['name']}`" for r in fam["implemented"]) or "(none)"
        miss = ", ".join(f"`{r['name']}`" for r in fam["not_implemented"]) or "(none)"
        L.append(f"| `{fam['family']}` | {fam['kind']} | {fam['status']} | "
                 f"{impl} | {miss} |")
    L.append("")
    for fam in report["families"]:
        L.append(f"### `{fam['family']}` - {fam['status']} "
                 f"({fam['kind']}{', critical' if fam['critical'] else ''})")
        L.append("")
        L.append("- Implemented (behavior evidence):")
        if fam["implemented"]:
            for row in fam["implemented"]:
                evidence = json.dumps(row["evidence"], sort_keys=True)
                L.append(f"  - `{row['name']}` - evidence: `{evidence}`")
        else:
            L.append("  - (none)")
        L.append("- Not implemented:")
        if fam["not_implemented"]:
            for row in fam["not_implemented"]:
                files = row["files"] if row["files"] is not None else "?"
                L.append(f"  - `{row['name']}` - reason: `{row['reason']}`, "
                         f"files: {files}")
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


def render_json(report: dict) -> str:
    """Canonical JSON text (sorted keys, stable ordering, trailing newline)."""
    return json.dumps(report, indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input_pos", nargs="?", metavar="INPUT",
                   help="API coverage JSON (alternative to --input)")
    p.add_argument("--input", dest="input_opt", type=Path,
                   help="API coverage JSON to read (required)")
    p.add_argument("--json-out", type=Path, default=None,
                   help="JSON report path (default: <input>.missing.json)")
    p.add_argument("--markdown-out", type=Path, default=None,
                   help="Markdown report path (default: <input>.missing.md)")
    p.add_argument("--generated-at", default=None,
                   help="optional provenance timestamp; omitted by default so "
                        "output stays deterministic")
    args = p.parse_args(argv)

    input_path = args.input_opt or args.input_pos
    if input_path is None:
        print("api_missing_report: --input (or positional INPUT) is required",
              file=sys.stderr)
        return 2
    input_path = Path(input_path)
    json_out = args.json_out or input_path.with_suffix(
        input_path.suffix + ".missing.json")
    markdown_out = args.markdown_out or input_path.with_suffix(
        input_path.suffix + ".missing.md")

    try:
        doc, digest = load_input(input_path)
        report = build_report(doc, input_path=input_path, input_sha256=digest,
                              generated_at=args.generated_at)
        markdown = render_markdown(report)
        json_text = render_json(report)
    except InputError as exc:
        print(f"api_missing_report: {exc}", file=sys.stderr)
        return 2

    # Defense in depth: never let a blanket percentage or completion claim
    # escape into an artifact, even if a future renderer change adds one.
    if PERCENT_RE.search(markdown) or PERCENT_RE.search(json_text):
        print("api_missing_report: internal error: percentage in output",
              file=sys.stderr)
        return 2

    try:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json_text, encoding="utf-8")
        markdown_out.parent.mkdir(parents=True, exist_ok=True)
        markdown_out.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        print(f"api_missing_report: cannot write output: {exc}", file=sys.stderr)
        return 2

    c = report["counts"]
    print(f"wrote {json_out} and {markdown_out} "
          f"({c['families']} families, {c['not_implemented']} not implemented, "
          f"{c['deferred_families']} deferred); not a completion claim")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
