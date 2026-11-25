"""Deterministic tests for the fail-closed rev.ng source-quality evaluator.

No rev.ng download, network access, GitHub Actions run or game asset is used.
A small on-disk evidence bundle is built in a temporary directory; the tool must
recompute every hash and verdict from those files and refuse to emit
``SOURCE_IMPROVED`` when any gate is missing, divergent, unreadable or tampered.

    python3 -m pytest tests/test_revng_source_quality.py -q
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import revng_source_quality as sq  # noqa: E402
from tools.issue25_evidence import canonical_digest  # noqa: E402

TOOL = ROOT / "tools" / "revng_source_quality.py"
ADDRESS = "0x00401000"

BASELINE_C = """#include <stdint.h>
void sub_00401000(void) {
    uint32_t v0 = MEM32(0x00404100U);
    v0 = v0 + 0xfffffffeU;
    MEM32(0x00404104U) = v0;
    g_eax = v0;
    return;
}
"""

REVNG_C = """#include <stdint.h>
_ABI(Microsoft_x86_cdecl)
generic32_t function_0x401000_Code_x86(void) {
    generic32_t input_value = *(generic32_t *)0x00404100;
    generic32_t adjusted_total = input_value + 0xfffffffeU;
    *(generic32_t *)0x00404104 = adjusted_total;
    if (adjusted_total != 0U) {
        return adjusted_total;
    }
    return adjusted_total;
}
"""


def difftest_report(verdict: str = "agree-return", agree: bool = True) -> dict:
    cases = [{"case": i, "agree": agree, "fields": []} for i in range(8)]
    return {
        "tool": "tools/lift/difftest.py --project",
        "addresses": [{
            "address": ADDRESS,
            "verdict": verdict,
            "diverged_cases": 0 if agree else 1,
            "cases": cases,
        }],
    }


def decision(revng_verdict: str = "agree-return") -> dict:
    return {
        "decision": "PROMOTE",
        "chosen_lifter": "revng",
        "lifters": [
            {"name": "revng", "status": "PASS", "verdict": revng_verdict,
             "address": ADDRESS, "diverged_cases": 0, "total_cases": 8,
             "reasons": ["agrees with zero diverged cases over 8 case(s)"]},
            {"name": "pcrecomp", "status": "DIVERGED", "verdict": "diverge",
             "address": ADDRESS, "diverged_cases": 1, "total_cases": 8,
             "reasons": ["diverged"]},
        ],
        "reasons": ["promoted 'revng'"],
    }


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _json(path: Path, data: dict) -> Path:
    return _write(path, json.dumps(data, indent=2) + "\n")


def bundle(root: Path) -> dict[str, Path]:
    """Build a complete, internally consistent on-disk evidence bundle."""
    fixture = _write(root / "gate.exe", "MZ" + "\x00" * 126)
    files = {
        "fixture": fixture,
        "baseline_c": _write(root / "baseline.c", BASELINE_C),
        "revng_c": _write(root / "revng.c", REVNG_C),
        "baseline_difftest": _json(root / "baseline.json", difftest_report()),
        "revng_difftest": _json(root / "revng.json", difftest_report()),
        "decision": _json(root / "decision.json", decision()),
        "baseline_compile_rc": _write(root / "baseline.compile.rc", "0\n"),
        "revng_compile_rc": _write(root / "revng.compile.rc", "0\n"),
        "out": root / "SOURCE_QUALITY.json",
    }
    return files


def args_for(files: dict[str, Path], out: Path | None = None) -> list[str]:
    return [
        "--fixture", str(files["fixture"]), "--address", ADDRESS,
        "--baseline-c", str(files["baseline_c"]),
        "--revng-c", str(files["revng_c"]),
        "--baseline-difftest", str(files["baseline_difftest"]),
        "--revng-difftest", str(files["revng_difftest"]),
        "--decision", str(files["decision"]),
        "--baseline-compile-rc", str(files["baseline_compile_rc"]),
        "--revng-compile-rc", str(files["revng_compile_rc"]),
        "--expected-fixture-sha256", hashlib.sha256(
            files["fixture"].read_bytes()).hexdigest(),
        "--out", str(out or files["out"]),
        "--json",
    ]


def run_tool(argv: list[str]) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(TOOL), *argv],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, json.loads(proc.stdout)


def codes(report: dict) -> set[str]:
    return {row["code"] for row in report["reasons"]}


def test_source_signals_use_the_repos_readability_tooling():
    baseline = sq.source_signals(BASELINE_C)
    revng = sq.source_signals(REVNG_C)
    assert "tools/autorecomp/readability.py" in revng["signals_from"]
    assert "docs/SOURCE_PROVENANCE.md" in revng["signals_from"]
    assert baseline["named_locals"] == [], baseline
    assert revng["named_locals"] == ["adjusted_total", "input_value"]
    assert baseline["goto_count"] == revng["goto_count"] == 0
    assert baseline["recovered_types"] == []
    assert "generic32_t" in revng["recovered_types"]
    assert revng["non_goto_control_statements"] > baseline["non_goto_control_statements"]
    assert revng["identifier_quality"] > baseline["identifier_quality"]
    # The repo's own helper records symbol facts beside our counts.
    assert "sub_00401000" in baseline["repo_readability_exported_symbols"]
    assert revng["repo_readability_top_level_definitions"]


def test_compare_signals_rule_is_strict_and_explains_each_criterion():
    baseline, revng = sq.source_signals(BASELINE_C), sq.source_signals(REVNG_C)
    improved, rows = sq.compare_signals(baseline, revng)
    assert improved and len(rows) == 6 and all(row["met"] for row in rows)
    worse, _ = sq.compare_signals(revng, baseline)
    assert worse is False
    # A text with no identifiers is not a readability improvement.
    empty = sq.source_signals("")
    improved_empty, rows_empty = sq.compare_signals(baseline, empty)
    assert improved_empty is False
    assert any(row["met"] is False for row in rows_empty)


def test_real_bundle_yields_a_sealed_source_improved_report(tmp_path):
    files = bundle(tmp_path)
    rc, report = run_tool(args_for(files))
    assert rc == 0, report
    assert report["verdict"] == sq.SOURCE_IMPROVED
    assert report["reasons"] == [], report["reasons"]
    assert report["fixture"]["bytes"] == len(files["fixture"].read_bytes())
    assert report["candidates"]["revng"]["compile_rc"] == 0
    assert report["difftest"]["revng"]["cases_driven"] == 8
    assert report["difftest"]["revng"]["diverged_cases"] == 0
    assert report["decision"]["chosen_lifter"] == "revng"
    assert canonical_digest({k: v for k, v in report.items() if k != "sha256"}) == \
        report["sha256"]
    assert files["out"].is_file()
    assert "Not HIGH_LEVEL_VERIFIED" in report["claim_scope"]
    assert "not 100%" in report["claim_scope"]
    assert "not gameplay equivalence" in report["claim_scope"]


def test_missing_compile_evidence_is_not_proven(tmp_path):
    files = bundle(tmp_path)
    files["revng_compile_rc"].unlink()
    rc, report = run_tool(args_for(files))
    assert rc != 0
    assert report["verdict"] == sq.NOT_PROVEN
    assert "REVNG_COMPILE_NOT_PROVEN" in codes(report)
    assert report["candidates"]["revng"]["compile_rc"] is None


def test_diverged_difftest_is_not_proven(tmp_path):
    files = bundle(tmp_path)
    _json(files["revng_difftest"], difftest_report(verdict="diverge", agree=False))
    rc, report = run_tool(args_for(files))
    assert rc != 0
    assert report["verdict"] == sq.NOT_PROVEN
    assert "REVNG_DIFFTEST_NOT_AGREE" in codes(report)
    assert "REVNG_DIFFTEST_DIVERGED_CASES" in codes(report)
    assert report["difftest"]["revng"]["diverged_cases"] == 8


def test_missing_readability_evidence_is_not_proven(tmp_path):
    files = bundle(tmp_path)
    _write(files["revng_c"], "")
    rc, report = run_tool(args_for(files))
    assert rc != 0
    assert report["verdict"] == sq.NOT_PROVEN
    assert "READABILITY_SIGNALS_MISSING" in codes(report)
    assert any(code == "READABILITY_NOT_BETTER" for code in codes(report))


@pytest.mark.parametrize("claimed_key", ["revng-sha256", "baseline-sha256",
                                         "expected-fixture-sha256"])
def test_tampered_hash_claims_are_detected(tmp_path, claimed_key):
    files = bundle(tmp_path)
    argv = args_for(files)
    flag = "--" + claimed_key
    if flag in argv:
        argv[argv.index(flag) + 1] = "0" * 64
    else:
        argv += [flag, "0" * 64]
    rc, report = run_tool(argv)
    assert rc != 0
    assert report["verdict"] == sq.NOT_PROVEN
    assert any("HASH_MISMATCH" in code for code in codes(report))


def test_tampered_output_report_breaks_its_seal(tmp_path):
    files = bundle(tmp_path)
    rc, report = run_tool(args_for(files))
    assert rc == 0
    report["verdict"] = sq.SOURCE_IMPROVED  # no field change, but reserialise
    original = json.loads(files["out"].read_text(encoding="utf-8"))
    original["difftest"]["revng"]["cases_driven"] = 999  # tamper after sealing
    assert original["sha256"] != canonical_digest(
        {k: v for k, v in original.items() if k != "sha256"})


def test_inline_fake_reports_are_not_accepted_as_files(tmp_path):
    files = bundle(tmp_path)
    argv = args_for(files)
    index = argv.index("--revng-difftest") + 1
    argv[index] = json.dumps(difftest_report())  # JSON text, not a path
    rc, report = run_tool(argv)
    assert rc != 0
    assert report["verdict"] == sq.NOT_PROVEN
    assert "REVNG_DIFFTEST_REPORT_MISSING" in codes(report)


def test_decision_must_promote_the_exact_revng_report(tmp_path):
    files = bundle(tmp_path)
    _json(files["decision"], {
        "decision": "PROMOTE", "chosen_lifter": "pcrecomp",
        "lifters": [{"name": "pcrecomp", "status": "PASS", "verdict": "agree",
                     "diverged_cases": 0, "total_cases": 8}],
    })
    rc, report = run_tool(args_for(files))
    assert rc != 0
    assert report["verdict"] == sq.NOT_PROVEN
    assert "DECISION_NOT_REVNG" in codes(report)
    assert "DECISION_REVNG_ROW_MISSING" in codes(report)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
