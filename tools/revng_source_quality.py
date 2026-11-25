"""Fail-closed source-quality evidence for one real rev.ng candidate (issue #25).

Running rev.ng and printing a selection report is not proof that its output is
*better source*. This tool evaluates a single function on a single real fixture
and only emits ``SOURCE_IMPROVED`` when every gate below is backed by an
on-disk, hash-checked artifact produced by the repo's own tools:

1. the PE and both C candidates exist; their exact SHA-256 and byte counts are
   recomputed from bytes and cross-checked against any supplied claim;
2. each candidate has a *measured* compile return code of 0;
3. each candidate has a real ``tools/lift/difftest.py --project --json`` report
   with at least one driven case, an ``agree*`` verdict and zero diverged cases;
4. ``tools/lift/multilifter.py`` promoted ``revng`` on those exact reports;
5. structured-C signals measured from the two C files by the repo's own
   readability tooling are strictly better for rev.ng under the documented
   rule in :func:`compare_signals`.

A missing artifact, a fabricated inline verdict, a hash mismatch, a failed
compile, a divergence or a worse readability signal yields ``NOT PROVEN`` with
the exact reason codes. The report is self-sealed with the canonical digest
from ``tools/issue25_evidence.py``; changing a number in it invalidates the
seal. Nothing here runs rev.ng, changes issue state, or claims
``HIGH_LEVEL_VERIFIED``, gameplay equivalence, 100% or a general source-quality
improvement.

    python3 tools/revng_source_quality.py \
        --fixture gate.exe --baseline-c baseline.c --revng-c revng.c \
        --baseline-difftest baseline.json --revng-difftest revng.json \
        --decision decision.json \
        --baseline-compile-rc baseline.compile.rc \
        --revng-compile-rc revng.compile.rc \
        --out proof/revng/SOURCE_QUALITY.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.autorecomp import readability as repo_readability  # noqa: E402
from tools.issue25_evidence import canonical_digest  # noqa: E402

SCHEMA = 1
TOOL = "tools/revng_source_quality.py"
SOURCE_IMPROVED = "SOURCE_IMPROVED"
NOT_PROVEN = "NOT PROVEN"
CLAIM_SCOPE = (
    "one fixture, one function address, the two exact C hashes and the exact "
    "difftest reports named in this record. Not HIGH_LEVEL_VERIFIED, not "
    "gameplay equivalence, not 100%, and not a general source-quality claim "
    "for any other function, provider or build."
)

_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_DECL = re.compile(
    r"^[ \t]*(?:const[ \t]+|static[ \t]+|volatile[ \t]+)*"
    r"(?:[A-Za-z_][\w \t\*]*?)[ \t]+([A-Za-z_]\w*)[ \t]*(?:=|;|\[)",
    re.MULTILINE)
_STRUCT_TYPE = re.compile(r"\b(?:struct|union|enum)\s+([A-Za-z_]\w*)")
_NAMED_TYPEDEF = re.compile(r"\b([A-Za-z_]\w*_t)\b")
_CONTROL = ("if", "for", "while", "switch", "do")

# Mechanical names are emitted by a lifter and carry no reader intent. They are
# counted separately from names a provider actually recovered or chose.
_STDINT_TYPES = frozenset({
    "uint8_t", "uint16_t", "uint32_t", "uint64_t", "int8_t", "int16_t",
    "int32_t", "int64_t", "size_t", "ssize_t", "intptr_t", "uintptr_t",
    "ptrdiff_t", "bool",
})
_CONTROL_KW = frozenset(_CONTROL) | {
    "return", "sizeof", "else", "void", "int", "char", "short", "long",
    "float", "double", "unsigned", "signed", "const", "volatile", "static",
    "extern", "inline", "typedef", "struct", "union", "enum",
}

def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX.match(value))


def file_facts(path: Path) -> dict[str, Any] | None:
    """Recompute SHA-256 and byte count from bytes; never trust a filename."""
    try:
        digest = hashlib.sha256()
        size = 0
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
    except (OSError, ValueError):
        return None
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": size}


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_rc(path: Path) -> int | None:
    """Read a measured return code; an absent/unparseable file reports None."""
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        return int(text.splitlines()[-1].strip())
    except (OSError, ValueError, IndexError):
        return None


def as_int(value: Any, default: int) -> int:
    """Convert a JSON number to int without treating a measured zero as absent."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _reason(reasons: list[dict[str, str]], code: str, detail: str) -> None:
    reasons.append({"code": code, "detail": detail})


def _mechanical(name: str) -> bool:
    """True for a name a lifter generated rather than a name it recovered."""
    if name in _CONTROL_KW or name in _STDINT_TYPES:
        return True
    if re.fullmatch(r"(?:v|var|tmp|loc)\d+", name):
        return True
    if re.fullmatch(r"g_[A-Za-z0-9_]+", name):
        return True
    if re.fullmatch(r"(?:sub|function)_0x?[0-9A-Fa-f]+(?:(?:_Code_\w+)|(?:\w*))", name):
        return True
    if re.fullmatch(r"generic\d+_t", name):
        return True
    return name.startswith("recomp_") or name.startswith("MEM") or name in {
        "ADDR", "A32", "APTR", "RETC", "RETCV", "RECOMP_FLAGS_OUT", "_rt",
        "main", "uint32_t", "result",
    }


def source_signals(source: str) -> dict[str, Any]:
    """Structured-C signals measured from C text with the repo's own helpers.

    ``tools/autorecomp/readability.py`` supplies the C-noise stripper, the
    top-level definition detector and the exported-symbol extractor. The
    identifier/structure counts below are defined here so the numbers in the
    report are reproducible without running any external tool.
    """
    text = source or ""
    clean = repo_readability._strip_c_noise(text)
    identifiers = _IDENT.findall(clean)
    meaningful = sorted({name for name in identifiers if not _mechanical(name)})

    # Names bound by a declaration on its own line (locals and parameters).
    declared = sorted({m.group(1) for m in _DECL.finditer(clean)
                       if not _mechanical(m.group(1))})
    params: set[str] = set()
    for match in re.finditer(
            r"\b[A-Za-z_]\w*\s*\(([^()]*)\)\s*\{", clean, re.DOTALL):
        for part in match.group(1).split(","):
            found = re.findall(r"\b([A-Za-z_]\w*)\b", part)
            if found and not _mechanical(found[-1]):
                params.add(found[-1])

    struct_types = sorted(set(_STRUCT_TYPE.findall(clean)))
    typedefs = sorted(set(_NAMED_TYPEDEF.findall(clean)) - _STDINT_TYPES)
    recovered_types = sorted(set(struct_types) | set(typedefs))

    return {
        "source_bytes": len(source.encode("utf-8")),
        "named_locals": declared,
        "named_parameters": sorted(params),
        "non_goto_control_statements": sum(
            len(re.findall(rf"\b{kw}\b\s*\(", clean)) for kw in _CONTROL),
        "goto_count": len(re.findall(r"\bgoto\b\s*\w+", clean)),
        "recovered_types": recovered_types,
        "recovered_structs": struct_types,
        "identifier_quality": (
            round(len(meaningful) / len(identifiers), 6) if identifiers else 0.0),
        "non_mechanical_identifiers": meaningful,
        "total_identifiers": len(identifiers),
        # Recorded from the repo's own readability module, not reinvented here.
        "repo_readability_top_level_definitions": sorted(
            repo_readability._top_level_function_names(text)),
        "repo_readability_exported_symbols": sorted(
            repo_readability.extract_exported_symbols(text)),
        "signals_from": [TOOL, "tools/autorecomp/readability.py",
                         "docs/SOURCE_PROVENANCE.md"],
    }


def _metric(signals: Mapping[str, Any], key: str) -> Any:
    value = signals.get(key)
    if isinstance(value, list):
        return len(value)
    return value


def compare_signals(baseline: Mapping[str, Any], revng: Mapping[str, Any]
                    ) -> tuple[bool, list[dict[str, Any]]]:
    """The documented, deterministic readability improvement rule.

    ``SOURCE_IMPROVED`` requires all of: at least one extra named local or
    parameter, no more ``goto`` statements, at least as much structured
    control flow, at least as many recovered types/structs, and a strictly
    higher non-mechanical identifier ratio. Every number is recorded in the
    report even when the rule fails.
    """
    criteria_def = (
        ("named_identifier_count", "named_locals", lambda b, r: r > b),
        ("named_parameter_count", "named_parameters", lambda b, r: r >= b),
        ("goto_not_worse", "goto_count", lambda b, r: r <= b),
        ("structured_control_not_worse", "non_goto_control_statements",
         lambda b, r: r >= b),
        ("recovered_types_not_worse", "recovered_types", lambda b, r: r >= b),
        ("identifier_quality_strictly_better", "identifier_quality",
         lambda b, r: r > b),
    )
    rows: list[dict[str, Any]] = []
    for name, key, predicate in criteria_def:
        b, r = _metric(baseline, key), _metric(revng, key)
        rows.append({"name": name, "baseline": b, "revng": r,
                     "met": bool(predicate(b, r))})
    naming = (rows[0]["met"] and rows[1]["met"])
    improved = naming and all(row["met"] for row in rows)
    return improved, rows


def validate_difftest(path: Path, address: str | None) -> tuple[dict[str, Any], list[str]]:
    """Validate a real difftest --project report; never accept inline JSON."""
    problems: list[str] = []
    facts = file_facts(path)
    if facts is None:
        return {}, [f"DIFFTEST_REPORT_MISSING: {path}"]
    summary: dict[str, Any] = dict(facts)
    try:
        data = read_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return summary, [f"DIFFTEST_REPORT_UNREADABLE: {path}: {exc}"]
    if not isinstance(data, Mapping):
        return summary, ["DIFFTEST_REPORT_SHAPE: expected a JSON object"]
    summary["tool"] = data.get("tool")
    if data.get("tool") != "tools/lift/difftest.py --project":
        problems.append(f"DIFFTEST_TOOL_MISMATCH: tool={data.get('tool')!r}")
    rows = data.get("addresses")
    if not isinstance(rows, list) or not rows:
        return summary, problems + ["DIFFTEST_NO_ADDRESS_ROWS: no measured rows"]
    total_cases = diverged = 0
    verdicts: list[str] = []
    addresses: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            problems.append("DIFFTEST_ROW_SHAPE: a row is not an object")
            continue
        verdict = str(row.get("verdict", ""))
        verdicts.append(verdict)
        if not verdict.startswith("agree"):
            problems.append(f"DIFFTEST_NOT_AGREE: verdict={verdict!r}")
        if row.get("address") is not None:
            addresses.append(str(row["address"]))
        cases = row.get("cases")
        if not isinstance(cases, list) or not cases:
            problems.append("DIFFTEST_NO_CASES: a row has no driven cases")
            continue
        for case in cases:
            total_cases += 1
            if not isinstance(case, Mapping) or case.get("agree") is not True:
                diverged += 1
        if as_int(row.get("diverged_cases"), 0) != 0:
            problems.append("DIFFTEST_DIVERGED_FIELD: row reports diverged cases")
    if diverged:
        problems.append(f"DIFFTEST_DIVERGED_CASES: {diverged}/{total_cases}")
    if address and addresses and address not in addresses:
        problems.append(f"DIFFTEST_ADDRESS_MISMATCH: expected {address}, got {addresses}")
    summary.update({
        "verdicts": verdicts, "addresses": addresses,
        "cases_driven": total_cases, "diverged_cases": diverged,
    })
    return summary, problems


def validate_decision(path: Path, revng_report: Mapping[str, Any]
                      ) -> tuple[dict[str, Any], list[str]]:
    """Require a real multilifter decision that promoted revng on this report."""
    problems: list[str] = []
    facts = file_facts(path)
    if facts is None:
        return {}, [f"DECISION_MISSING: {path}"]
    summary: dict[str, Any] = dict(facts)
    try:
        data = read_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return summary, [f"DECISION_UNREADABLE: {path}: {exc}"]
    if not isinstance(data, Mapping):
        return summary, ["DECISION_SHAPE: expected a JSON object"]
    summary.update({
        "decision": data.get("decision"), "chosen_lifter": data.get("chosen_lifter"),
    })
    if data.get("decision") != "PROMOTE":
        problems.append(f"DECISION_NOT_PROMOTE: decision={data.get('decision')!r}")
    if data.get("chosen_lifter") != "revng":
        problems.append(
            f"DECISION_NOT_REVNG: chosen_lifter={data.get('chosen_lifter')!r}")
    rows = data.get("lifters")
    revng_rows = [row for row in rows if isinstance(row, Mapping)
                  and row.get("name") == "revng"] if isinstance(rows, list) else []
    if not revng_rows:
        problems.append("DECISION_REVNG_ROW_MISSING")
        return summary, problems
    row = revng_rows[0]
    summary["revng_row"] = {
        "status": row.get("status"), "verdict": row.get("verdict"),
        "diverged_cases": row.get("diverged_cases"),
        "total_cases": row.get("total_cases"),
    }
    if row.get("status") != "PASS":
        problems.append(f"DECISION_REVNG_NOT_PASS: status={row.get('status')!r}")
    if as_int(row.get("diverged_cases"), -1) != 0:
        problems.append("DECISION_REVNG_DIVERGED")
    if row.get("verdict") not in tuple(revng_report.get("verdicts", ())):
        problems.append("DECISION_VERDICT_MISMATCH: not from the supplied report")
    if as_int(row.get("total_cases"), -1) != as_int(
            revng_report.get("cases_driven"), -2):
        problems.append("DECISION_CASE_COUNT_MISMATCH: not from the supplied report")
    return summary, problems


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    reasons: list[dict[str, str]] = []

    def add(code: str, detail: str) -> None:
        _reason(reasons, code, detail)

    fixture = file_facts(Path(args.fixture))
    if fixture is None:
        add("FIXTURE_MISSING", str(args.fixture))
        fixture = {"path": args.fixture, "sha256": None, "bytes": None}
    elif args.expected_fixture_sha256 and fixture["sha256"].lower() != \
            args.expected_fixture_sha256.lower():
        add("FIXTURE_HASH_MISMATCH",
            f"claimed {args.expected_fixture_sha256} vs {fixture['sha256']}")

    candidates: dict[str, Any] = {}
    for name, path, claimed in (
        ("revng", args.revng_c, args.revng_sha256),
        ("pcrecomp", args.baseline_c, args.baseline_sha256),
    ):
        facts = file_facts(Path(path))
        if facts is None:
            add(f"{name.upper()}_C_MISSING", str(path))
            facts = {"path": path, "sha256": None, "bytes": None}
        elif claimed and facts["sha256"].lower() != claimed.lower():
            add(f"{name.upper()}_HASH_MISMATCH",
                f"claimed {claimed} vs {facts['sha256']}")
        rc_path = (args.baseline_compile_rc if name == "pcrecomp"
                   else args.revng_compile_rc)
        rc = read_rc(Path(rc_path))
        facts["compile_rc"] = rc
        facts["compile_rc_path"] = rc_path
        if rc != 0:
            add(f"{name.upper()}_COMPILE_NOT_PROVEN",
                f"measured rc={rc!r} from {rc_path} (0 required)")
        candidates[name] = facts

    difftest: dict[str, Any] = {}
    for name, path in (("revng", args.revng_difftest),
                       ("pcrecomp", args.baseline_difftest)):
        summary, problems = validate_difftest(Path(path), args.address)
        difftest[name] = summary
        for problem in problems:
            add(f"{name.upper()}_{problem.split(':', 1)[0]}",
                f"{path}: {problem}")

    decision, problems = validate_decision(Path(args.decision),
                                           difftest.get("revng", {}))
    for problem in problems:
        add(problem.split(":", 1)[0], str(args.decision))

    signals: dict[str, Any] = {}
    for name, path in (("pcrecomp", args.baseline_c), ("revng", args.revng_c)):
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
            add(f"{name.upper()}_SOURCE_UNREADABLE", str(path))
        signals[name] = source_signals(text)
    improved, criteria = compare_signals(signals["pcrecomp"], signals["revng"])
    if not signals["pcrecomp"]["total_identifiers"] or \
            not signals["revng"]["total_identifiers"]:
        add("READABILITY_SIGNALS_MISSING",
            "one candidate has no parseable identifiers to compare")
    for row in criteria:
        if not row["met"]:
            add("READABILITY_NOT_BETTER",
                f"{row['name']}: baseline={row['baseline']} revng={row['revng']}")

    verdict = SOURCE_IMPROVED if not reasons and improved else NOT_PROVEN
    if reasons and improved:
        add("EVIDENCE_GATE_FAILED",
            "readability improved but compile/difftest/decision/hash evidence failed")

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "tool": TOOL,
        "issue": 25,
        "verdict": verdict,
        "claim_scope": CLAIM_SCOPE,
        "fixture": fixture,
        "function_address": args.address,
        "candidates": candidates,
        "difftest": difftest,
        "decision": decision,
        "readability": signals,
        "criteria": criteria,
        "reasons": reasons,
    }
    # The claim scope is a fixed, reviewed string: it deliberately *names* the
    # forbidden claims in order to disclaim them, so it is never scanned here.
    report["reasons"] = reasons
    if verdict == SOURCE_IMPROVED and reasons:
        report["verdict"] = NOT_PROVEN
    report["sha256"] = canonical_digest(report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, help="the real PE32 fixture")
    parser.add_argument("--address", default="0x00401000",
                        help="the function address compared by both candidates")
    parser.add_argument("--baseline-c", required=True,
                        help="existing PCRecomp C for this function")
    parser.add_argument("--revng-c", required=True,
                        help="C emitted by rev.ng for this fixture")
    parser.add_argument("--baseline-difftest", required=True,
                        help="real difftest --project JSON for the baseline")
    parser.add_argument("--revng-difftest", required=True,
                        help="real difftest --project JSON for revng")
    parser.add_argument("--decision", required=True,
                        help="real multilifter decision JSON")
    parser.add_argument("--baseline-compile-rc", required=True,
                        help="file containing the measured compile rc of the baseline")
    parser.add_argument("--revng-compile-rc", required=True,
                        help="file containing the measured compile rc of revng")
    parser.add_argument("--expected-fixture-sha256", default="")
    parser.add_argument("--baseline-sha256", default="")
    parser.add_argument("--revng-sha256", default="")
    parser.add_argument("--out", help="write the sealed JSON report here")
    parser.add_argument("--json", action="store_true", help="print the JSON report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate(args)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    if args.json or not args.out:
        print(text, end="")
    print(f"revng source quality: {report['verdict']}", file=sys.stderr)
    return 0 if report["verdict"] == SOURCE_IMPROVED else 1


if __name__ == "__main__":
    sys.exit(main())
