"""Fail-closed tests for the Frida-free verifier (issue #171).

Every test builds synthetic evidence from real bytes in ``tmp_path``: a small
PE-like original, a thin Mach-O header, a scenario JSON, a difftest-shaped
Unicorn report, a unicorn_diff_adapter record, a windows_oracle capture detail
and a windows_oracle record. No game, Frida, Wine, CrossOver, Unicorn or
network host is used. The verifier only reads those files and recomputes hashes.

The assertions prove that a fabricated digest or flag can never raise a verdict
to PASS, that a written ``PASS``/``MATCH`` is advisory only, and that an absent
Frida status is an explicit BLOCKED rather than a silent bypass.
"""
from __future__ import annotations

import copy
import hashlib
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import frida_free_verifier as fv  # noqa: E402

FIXED_TIME = "2026-01-01T00:00:00Z"
ORIGINAL_BYTES = b"MZ synthetic original windows executable\x00\x01"
NATIVE_BYTES = b"\xcf\xfa\xed\xfe" + struct.pack("<I", 0x0100000C) + b"\x00" * 24
SCENARIO = {"name": "boot-to-menu", "args": [], "target": {"os": "Windows", "arch": "x86"}}
FRIDA_REASON = (
    "PermissionDeniedError: unable to access process with pid 4242 from the "
    "current user account"
)
ADDRESSES = ["0x00401000", "0x00401120"]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_report(path: Path, *, cases: int = 4, verdict: str = "agree",
                 tool: str = fv.DIFTEST_TOOL, rows: list[str] | None = None) -> None:
    """Write a difftest.py --project shaped report with real driven cases."""
    verdicts = rows if rows is not None else [verdict] * len(ADDRESSES)
    doc = {
        "tool": tool,
        "what_it_proves": "the project bodies and the original bytes agree on "
                          "the compared fields over the driven entry states",
        "what_it_does_not_prove": "anything about other entry states or the game",
        "addresses": [],
    }
    for index, (address, row_verdict) in enumerate(zip(ADDRESSES, verdicts)):
        doc["addresses"].append({
            "address": address, "verdict": row_verdict,
            "pe": "synthetic-original.exe",
            "pe_sha256": hashlib.sha256(ORIGINAL_BYTES).hexdigest(),
            "bytes_read": 32,
            "bytes_sha256": hashlib.sha256(f"body-{index}".encode()).hexdigest(),
            "diverged_cases": 0 if row_verdict.startswith("agree") else 1,
            "input_set": {"cases": cases, "named_addresses": []},
            "cases": [
                {"case": number, "seed": f"0x{number + 1:08X}",
                 "agree": row_verdict.startswith("agree"), "fields": []}
                for number in range(cases)
            ],
        })
    path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")


def write_unicorn_record(path: Path, report: Path, *, status: str = "OK",
                         agreement: str = "full", reason: str | None = None) -> None:
    """Write a unicorn_diff_adapter record derived from the report on disk."""
    doc = json.loads(report.read_text(encoding="utf-8"))
    rows = doc["addresses"]
    verdicts = [row["verdict"] for row in rows]
    record = {
        "schema": "unicorn_diff_adapter/1", "status": status, "reason": reason,
        "blocked_class": None, "agreement": agreement,
        "tool": doc["tool"],
        "command": [sys.executable, "tools/lift/difftest.py", "--project",
                    "synthetic-project", "--pe", "synthetic-original.exe",
                    "--json", str(report)],
        "project": "synthetic-project", "pe": "synthetic-original.exe",
        "addresses": [row["address"] for row in rows], "cases": 8,
        "flags": False, "report_path": str(report),
        "addresses_tested": [row["address"] for row in rows],
        "counts": {
            "addresses_tested": len(rows),
            "agree": sum(1 for verdict in verdicts if verdict.startswith("agree")),
            "diverge": sum(1 for verdict in verdicts if verdict.startswith("diverge")),
            "original_faulted": sum(1 for v in verdicts if v == "original-faulted"),
            "other": sum(1 for v in verdicts if not v.startswith("agree")
                         and not v.startswith("diverge") and v != "original-faulted"),
        },
        "verdicts": [{"address": row["address"], "verdict": row["verdict"]}
                     for row in rows],
        "report_sha256": sha256_file(report),
        "report_tool": doc["tool"], "tool_exit_code": 0,
    }
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def write_capture(path: Path, original: Path, native: Path, scenario: Path, *,
                  status: str = "CAPTURED", exit_code: int = 0,
                  process: dict | None = None) -> None:
    """Write a windows_oracle capture detail describing an executed process."""
    detail = {
        "schema": 1, "generator": "tools/windows_oracle.py", "status": status,
        "game": "arith-calc", "game_id": "arith-calc", "scenario": "boot-to-menu",
        "endpoint": "cli-supplied://synthetic-oracle",
        "process": {"pid": 4242, "name": "arith-calc.exe"}
        if process is None else process,
        "exit_code": exit_code,
        "original": {"path": str(original), "sha256": sha256_file(original)},
        "native": {"path": str(native), "sha256": sha256_file(native)},
        "input_script": {"path": str(scenario), "sha256": sha256_file(scenario)},
        "screenshots": [], "audio": [], "logs": [],
    }
    path.write_text(json.dumps(detail, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def write_oracle_record(path: Path, capture: Path, original: Path, native: Path,
                        *, status: str = "CAPTURED", oracle: str = "windows-oracle",
                        verified: bool = True) -> None:
    """Write a windows_oracle record bound to the capture detail on disk."""
    record = {
        "schema": 1, "generator": "tools/windows_oracle.py", "status": status,
        "verification": "CAPTURED" if status == "CAPTURED" else "NOT TESTED",
        "reason": None, "oracle": oracle, "artifacts_dir": None,
        "original_sha256": sha256_file(original),
        "native_sha256": sha256_file(native),
        "input_script_sha256": sha256_file(capture),
        "screenshots": [], "audio": [], "logs": [],
        "records": [{
            "game": "arith-calc", "game_id": "arith-calc",
            "sha256": sha256_file(original), "native_sha256": sha256_file(native),
            "oracle": oracle, "oracle_verified": verified,
            "scenario": "boot-to-menu",
            "scenario_evidence": "synthetic process-level oracle capture",
            "evidence_path": str(capture), "evidence_sha256": sha256_file(capture),
            "input_script_sha256": sha256_file(capture),
        }],
    }
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


@pytest.fixture
def evidence(tmp_path):
    original = tmp_path / "original.exe"
    original.write_bytes(ORIGINAL_BYTES)
    native = tmp_path / "native.bin"
    native.write_bytes(NATIVE_BYTES)
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps(SCENARIO), encoding="utf-8")
    report = tmp_path / "difftest-report.json"
    write_report(report)
    unicorn = tmp_path / "unicorn-record.json"
    write_unicorn_record(unicorn, report)
    capture = tmp_path / "capture.json"
    write_capture(capture, original, native, scenario)
    oracle = tmp_path / "oracle-record.json"
    write_oracle_record(oracle, capture, original, native)
    return SimpleNamespace(tmp=tmp_path, original=original, native=native,
                           scenario=scenario, report=report, unicorn=unicorn,
                           capture=capture, oracle=oracle, out=tmp_path / "report.json")


def make_claim(ev) -> dict:
    return {
        "schema": 1,
        "hashes": {
            "original": sha256_file(ev.original),
            "native": sha256_file(ev.native),
            "scenario": sha256_file(ev.scenario),
            "unicorn_report": sha256_file(ev.report),
            "oracle_evidence": sha256_file(ev.capture),
        },
        "frida": {"status": "BLOCKED", "reason": FRIDA_REASON},
        "unicorn": {"status": "OK", "agreement": "full", "nonvacuous": True,
                    "distinct_eax": 7, "cases": 8},
        "oracle": {"status": "CAPTURED", "name": "windows-oracle",
                   "executed": True, "verified": True},
        "verdict": {"status": "PASS",
                    "reasons": ["all gates recomputed from supplied evidence"]},
    }


def run(ev, claim=None, **overrides) -> dict:
    options = {
        "original": ev.original, "native": ev.native, "scenario": ev.scenario,
        "unicorn": ev.unicorn, "oracle": ev.oracle,
        "generated_at": FIXED_TIME,
    }
    options.update(overrides)
    return fv.verify(make_claim(ev) if claim is None else claim, **options)


def codes(report: dict) -> set[str]:
    return {reason["code"] for reason in report["verdict"]["reasons"]}


# ---------------------------------------------------------------------------
# The documented fallback can actually pass
# ---------------------------------------------------------------------------
def test_complete_frida_free_evidence_passes(evidence):
    report = run(evidence)
    assert report["verdict"]["status"] == "PASS", report["verdict"]["reasons"]
    assert all(report["checks"].values())
    assert report["method"] == "frida-free-fallback"
    assert report["frida"]["status"] == "BLOCKED"
    assert report["frida"]["reason"] == FRIDA_REASON
    assert report["oracle"]["executed"] is True
    assert report["oracle"]["bound_to_cli_artifacts"] is True
    assert report["unicorn"]["difftest_facts"]["nonvacuous"] is True
    assert report["unicorn"]["difftest_facts"]["distinct_seeds"] == 4
    assert all(item["match"] for item in report["hashes"].values())
    assert report["summary"]["accepted"] is True
    assert "ALL_GATES_SATISFIED" in codes(report)


def test_cli_report_and_markdown_are_canonical(evidence, capsys):
    claim_path = evidence.tmp / "claim.json"
    claim_path.write_text(json.dumps(make_claim(evidence)), encoding="utf-8")
    markdown = evidence.tmp / "report.md"
    args = ["--claim", str(claim_path), "--original", str(evidence.original),
            "--native", str(evidence.native), "--scenario", str(evidence.scenario),
            "--unicorn", str(evidence.unicorn), "--oracle", str(evidence.oracle),
            "--out", str(evidence.out), "--markdown", str(markdown),
            "--generated-at", FIXED_TIME]
    assert fv.main(args) == fv.EXIT_OK
    assert "verdict: PASS" in capsys.readouterr().out
    text = evidence.out.read_text(encoding="utf-8")
    assert text == fv.canonical_json(json.loads(text))
    assert "## Gates" in markdown.read_text(encoding="utf-8")
    assert "frida-free-fallback" in text


# ---------------------------------------------------------------------------
# Missing Frida is explicit, and no fake capture is manufactured
# ---------------------------------------------------------------------------
def test_missing_frida_status_is_explicitly_blocked(evidence):
    claim = make_claim(evidence)
    del claim["frida"]
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "BLOCKED"
    assert "FRIDA_STATUS_MISSING" in codes(report)
    assert report["frida"] == {"status": "BLOCKED",
                               "reason": "no Frida status recorded",
                               "fallback": "frida-free-fallback"}
    assert report["checks"]["frida_status_recorded"] is False
    assert report["oracle"]["status"] == "PASS"  # oracle evidence is still real
    assert report["verdict"]["status"] not in ("PASS", "PARTIAL")


def test_frida_blocked_reason_is_verbatim_not_a_match(evidence):
    report = run(evidence)
    assert report["frida"]["reason"] == FRIDA_REASON
    assert "MATCH" not in json.dumps(report)
    assert report["claimed_verdict"]["status"] == "PASS"  # advisory only


def test_written_frida_pass_is_not_evidence(evidence):
    claim = make_claim(evidence)
    claim["frida"] = {"status": "PASS", "reason": "claimed without a capture record"}
    report = run(evidence, claim)
    assert "UNVERIFIED_FRIDA_CLAIM" in codes(report)
    assert report["verdict"]["status"] != "PASS"
    assert report["frida"]["status"] == "NOT TESTED"


# ---------------------------------------------------------------------------
# Fabricated or mismatched hashes can never pass
# ---------------------------------------------------------------------------
def test_fabricated_hash_is_a_hard_failure(evidence):
    claim = make_claim(evidence)
    claim["hashes"]["original"] = "f" * 64
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "FAIL"
    assert "HASH_MISMATCH" in codes(report)
    assert report["hashes"]["original"]["match"] is False


def test_malformed_hash_is_a_hard_failure(evidence):
    claim = make_claim(evidence)
    claim["hashes"]["native"] = "not-a-sha256"
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "FAIL"
    assert "FABRICATED_HASH" in codes(report)


def test_tampered_artifact_after_the_claim_fails(evidence):
    claim = make_claim(evidence)
    report_before = run(evidence, claim)
    assert report_before["verdict"]["status"] == "PASS"
    evidence.original.write_bytes(ORIGINAL_BYTES + b"tampered")
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "FAIL"
    assert "HASH_MISMATCH" in codes(report)
    assert report["oracle"]["bound_to_cli_artifacts"] is False


def test_tampered_oracle_capture_hash_fails(evidence):
    evidence.capture.write_bytes(evidence.capture.read_bytes() + b"\n")
    report = run(evidence)
    assert report["verdict"]["status"] == "FAIL"
    assert "HASH_MISMATCH" in codes(report)
    assert report["oracle"]["status"] != "PASS"


def test_tampered_unicorn_report_hash_fails(evidence):
    evidence.report.write_bytes(evidence.report.read_bytes() + b"\n")
    report = run(evidence)
    assert report["verdict"]["status"] == "FAIL"
    assert "HASH_MISMATCH" in codes(report)
    assert report["checks"]["nonvacuous_unicorn_agreement"] is False


# ---------------------------------------------------------------------------
# Fabricated Unicorn flags cannot pass
# ---------------------------------------------------------------------------
def test_fabricated_nonvacuous_flag_fails(evidence):
    write_report(evidence.report, cases=1)
    write_unicorn_record(evidence.unicorn, evidence.report)
    claim = make_claim(evidence)
    claim["hashes"]["unicorn_report"] = sha256_file(evidence.report)
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "FAIL"
    assert "FABRICATED_FLAG" in codes(report)
    assert report["unicorn"]["difftest_facts"]["nonvacuous"] is False


def test_nothing_compared_is_never_pass(evidence):
    write_report(evidence.report, rows=["nothing-compared", "agree"])
    write_unicorn_record(evidence.unicorn, evidence.report)
    claim = make_claim(evidence)
    claim["hashes"]["unicorn_report"] = sha256_file(evidence.report)
    report = run(evidence, claim)
    assert report["verdict"]["status"] != "PASS"
    assert "FABRICATED_FLAG" in codes(report)
    assert report["unicorn"]["difftest_facts"]["nothing_compared"] == 1


def test_fabricated_distinct_eax_flag_fails(evidence):
    claim = make_claim(evidence)
    claim["unicorn"]["distinct_eax"] = 1
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "FAIL"
    assert "FABRICATED_FLAG" in codes(report)


def test_honest_vacuous_claim_is_not_pass(evidence):
    claim = make_claim(evidence)
    claim["unicorn"]["nonvacuous"] = False
    report = run(evidence, claim)
    assert report["verdict"]["status"] != "PASS"
    assert "VACUOUS_UNICORN" in codes(report)


def test_unicorn_adapter_blocked_is_not_upgraded(evidence):
    write_unicorn_record(evidence.unicorn, evidence.report, status="BLOCKED",
                         reason="unicorn reference machine is not installed",
                         agreement=None)
    report = run(evidence)
    assert report["verdict"]["status"] == "FAIL"  # claim still asserts OK
    assert "FABRICATED_FLAG" in codes(report)
    assert "UNICORN_BLOCKED" in codes(report)


# ---------------------------------------------------------------------------
# Fabricated or absent oracles cannot pass
# ---------------------------------------------------------------------------
def test_oracle_executed_flag_without_a_process_fails(evidence):
    write_capture(evidence.capture, evidence.original, evidence.native,
                  evidence.scenario, status="BLOCKED", process={})
    write_oracle_record(evidence.oracle, evidence.capture, evidence.original,
                        evidence.native)
    claim = make_claim(evidence)
    claim["hashes"]["oracle_evidence"] = sha256_file(evidence.capture)
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "FAIL"
    assert "FABRICATED_FLAG" in codes(report)
    assert report["oracle"]["process_present"] is False


def test_dummy_oracle_name_fails(evidence):
    claim = make_claim(evidence)
    claim["oracle"]["name"] = "dummy"
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "FAIL"
    assert "FABRICATED_ORACLE" in codes(report)


def test_oracle_not_executed_is_blocked(evidence):
    claim = make_claim(evidence)
    claim["oracle"]["executed"] = False
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "BLOCKED"
    assert "ORACLE_NOT_EXECUTED" in codes(report)


def test_no_oracle_evidence_is_build_only(evidence):
    claim = make_claim(evidence)
    claim["oracle"]["executed"] = False
    claim["oracle"]["verified"] = False
    claim["hashes"]["oracle_evidence"] = None
    report = run(evidence, claim, oracle=None)
    assert report["verdict"]["status"] == "BUILD ONLY"
    assert "NO_ORACLE_EVIDENCE" in codes(report)
    assert report["oracle"]["status"] != "PASS"
    assert report["hashes"]["native"]["match"] is True


def test_oracle_bound_to_a_different_artifact_fails(evidence):
    other = evidence.tmp / "other.exe"
    other.write_bytes(b"MZ a different original")
    write_capture(evidence.capture, other, evidence.native, evidence.scenario)
    write_oracle_record(evidence.oracle, evidence.capture, evidence.original,
                        evidence.native)
    claim = make_claim(evidence)
    claim["hashes"]["oracle_evidence"] = sha256_file(evidence.capture)
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "FAIL"
    assert "ORACLE_ARTIFACT_MISMATCH" in codes(report)
    assert report["oracle"]["bound_to_cli_artifacts"] is False


# ---------------------------------------------------------------------------
# Written verdicts are advisory and never grant PASS
# ---------------------------------------------------------------------------
def test_legacy_match_verdict_never_passes(evidence):
    claim = make_claim(evidence)
    claim["verdict"]["status"] = "MATCH"
    report = run(evidence, claim)
    assert "OLD_STATUS_STRING" in codes(report)
    assert report["verdict"]["status"] != "PASS"
    assert report["policy"]["no_fake_match"] is True


def test_written_pass_does_not_rescue_a_failed_gate(evidence):
    claim = make_claim(evidence)
    claim["hashes"]["scenario"] = "0" * 64
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "FAIL"
    assert "CLAIM_NOT_EVIDENCED" in codes(report)


def test_no_claim_is_not_tested(evidence):
    report = fv.verify(None, original=evidence.original, native=evidence.native,
                       scenario=evidence.scenario, unicorn=evidence.unicorn,
                       oracle=evidence.oracle, generated_at=FIXED_TIME)
    assert report["verdict"]["status"] == "NOT TESTED"
    assert "INVALID_CLAIM" in codes(report)
    assert report["summary"]["accepted"] is False


def test_wrong_schema_is_not_tested(evidence):
    claim = make_claim(evidence)
    claim["schema"] = 2
    report = run(evidence, claim)
    assert report["verdict"]["status"] == "NOT TESTED"
    assert "INVALID_SCHEMA" in codes(report)


def test_strict_cli_exit_code_is_nonzero_without_pass(evidence, capsys):
    claim = make_claim(evidence)
    claim["frida"] = {"status": "BLOCKED", "reason": ""}
    claim_path = evidence.tmp / "claim.json"
    claim_path.write_text(json.dumps(claim), encoding="utf-8")
    args = ["--claim", str(claim_path), "--original", str(evidence.original),
            "--native", str(evidence.native), "--scenario", str(evidence.scenario),
            "--unicorn", str(evidence.unicorn), "--oracle", str(evidence.oracle),
            "--out", str(evidence.out), "--strict"]
    assert fv.main(args) == fv.EXIT_STRICT
    assert "verdict:" in capsys.readouterr().out
    report = json.loads(evidence.out.read_text(encoding="utf-8"))
    assert report["verdict"]["status"] != "PASS"
    assert "INCOMPLETE_EVIDENCE" in codes(report)


def test_verifier_needs_no_runner_on_path(evidence, monkeypatch):
    monkeypatch.setenv("PATH", "")
    report = run(evidence)
    assert report["verdict"]["status"] == "PASS"
    assert "no_runner_or_emulator_dependency" in report["policy"]


if __name__ == "__main__":
    pytest.main([__file__])
