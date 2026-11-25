"""Synthetic-record tests for the fail-closed game scenario matrix.

Run with: python3 -m pytest tests/test_game_scenario_matrix.py -q

Every fixture is created under ``tmp_path``; no host game, evidence path or
repository document is read by the assertions. All hashes are computed from
those synthetic bytes, and every PASS is built from a real oracle-bearing
record with an existing evidence file.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import game_scenario_matrix as gsm

FIXED_TIME = "2026-11-25T00:00:00Z"
REPO_ROOT = Path(__file__).resolve().parents[1]

ORIGINAL_BYTES = b"synthetic original Windows PE bytes for TMNF"
NATIVE_BYTES = b"synthetic native binary bytes for TMNF"
EVIDENCE_BYTES = b'{"synthetic": "race-to-finish frame hashes"}'
OTHER_BYTES = b"tampered bytes"

ORIGINAL_SHA = hashlib.sha256(ORIGINAL_BYTES).hexdigest()
NATIVE_SHA = hashlib.sha256(NATIVE_BYTES).hexdigest()
EVIDENCE_SHA = hashlib.sha256(EVIDENCE_BYTES).hexdigest()
OTHER_SHA = hashlib.sha256(OTHER_BYTES).hexdigest()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def write_evidence(tmp_path: Path, *, name: str = "evidence.json",
                   data: bytes = EVIDENCE_BYTES) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def write_original(tmp_path: Path, *, name: str = "TmForever.exe",
                   data: bytes = ORIGINAL_BYTES) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def valid_record(tmp_path: Path, *, issue: int = 23,
                 game: str = "TrackMania Nations Forever",
                 evidence_path: Path | None = None,
                 evidence_sha: str = EVIDENCE_SHA,
                 input_sha: str = ORIGINAL_SHA,
                 native_sha: str | None = NATIVE_SHA,
                 verified: object = True,
                 executed: object = True,
                 oracle: str = "original-windows",
                 scenario: str = "race-to-finish",
                 scenario_evidence: str = "finish frames match at tick 900",
                 **extra: object) -> dict:
    evidence = evidence_path or write_evidence(tmp_path)
    record: dict[str, object] = {
        "issue": issue,
        "game": game,
        "sha256": input_sha,
        "native_sha256": native_sha,
        "oracle": oracle,
        "oracle_verified": verified,
        "executed": executed,
        "scenario": scenario,
        "scenario_evidence": scenario_evidence,
        "evidence_path": str(evidence),
        "evidence_sha256": evidence_sha,
    }
    record.update(extra)
    return record


def run_matrix(tmp_path: Path, records: dict[str, list[dict]] | None = None,
               *, issues: str | None = None, generated_at: str = FIXED_TIME,
               markdown: bool = False, use_subprocess: bool = False,
               raw_files: dict[str, str] | None = None) -> tuple[dict, bytes, str, int]:
    """Write record files under tmp_path and run the CLI into its outputs."""
    paths: list[str] = []
    for name, entries in (records or {"records.json": []}).items():
        path = write_json(tmp_path / name, {"records": entries})
        paths.append(str(path))
    for name, text in (raw_files or {}).items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        paths.append(str(path))
    out = tmp_path / "matrix.json"
    md_path = tmp_path / "matrix.md"
    argv: list[str] = []
    for path in paths:
        argv += ["--scenario-records", path]
    if issues is not None:
        argv += ["--issues", issues]
    argv += ["--generated-at", generated_at, "--out", str(out)]
    if markdown:
        argv += ["--markdown", str(md_path)]
    if use_subprocess:
        proc = subprocess.run(
            [sys.executable, "-m", "tools.game_scenario_matrix", *argv],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False)
        rc = proc.returncode
    else:
        rc = gsm.main(argv)
    payload = out.read_bytes()
    text = md_path.read_text(encoding="utf-8") if markdown else ""
    return json.loads(payload), payload, text, rc


def row_of(matrix: dict, issue: int = 23) -> dict:
    rows = [row for row in matrix["rows"] if row["issue"] == issue]
    assert len(rows) == 1, matrix["rows"]
    return rows[0]


# ---------------------------------------------------------------------------
# PASS gate
# ---------------------------------------------------------------------------

def test_valid_oracle_record_reaches_pass_and_others_are_not_tested(tmp_path):
    matrix, _, _, rc = run_matrix(
        tmp_path, {"records.json": [valid_record(tmp_path)]})
    assert rc == 0
    row = row_of(matrix, 23)
    assert row["status"] == "PASS"
    assert row["pass_eligible"] is True
    assert row["scenario"]["id"] == "race-to-finish"
    assert row["oracle"]["name"] == "original-windows"
    assert row["oracle"]["verified"] is True
    assert row["hashes"]["evidence_sha256"] == EVIDENCE_SHA
    assert row["hashes"]["actual_evidence_sha256"] == EVIDENCE_SHA
    assert any("recomputed and matched" in reason for reason in row["reasons"])
    assert matrix["summary"]["by_status"] == {
        "PASS": 1, "PARTIAL": 0, "BLOCKED": 0, "NOT TESTED": 4}
    for issue in (20, 21, 22, 24):
        assert row_of(matrix, issue)["status"] == "NOT TESTED"


def test_relative_evidence_path_and_alias_fields_still_pass(tmp_path):
    write_evidence(tmp_path)
    record = {
        "issue_number": "#23",
        "game": "TrackMania Nations Forever",
        "original_sha256": ORIGINAL_SHA,
        "native_sha256": NATIVE_SHA,
        "oracle_name": "retail-windows",
        "oracle_verified": True,
        "scenario_id": "race-to-finish",
        "evidence_text": "finish frames match at tick 900",
        "evidence_path": "evidence.json",
        "evidence_sha256": EVIDENCE_SHA,
    }
    matrix, _, _, _ = run_matrix(tmp_path, {"records.json": [record]})
    row = row_of(matrix, 23)
    assert row["status"] == "PASS"
    assert row["hashes"]["input_sha256"] == ORIGINAL_SHA


# ---------------------------------------------------------------------------
# Tamper and missing-evidence detection
# ---------------------------------------------------------------------------

def test_tampered_evidence_file_is_partial_not_pass(tmp_path):
    evidence = write_evidence(tmp_path)
    matrix, _, _, _ = run_matrix(
        tmp_path, {"records.json": [valid_record(tmp_path, evidence_path=evidence)]})
    assert row_of(matrix, 23)["status"] == "PASS"

    # Change the bytes behind the recorded scenario id; the claim stays the same.
    evidence.write_bytes(OTHER_BYTES)
    matrix, _, _, _ = run_matrix(
        tmp_path, {"records.json": [valid_record(tmp_path, evidence_path=evidence)]})
    row = row_of(matrix, 23)
    assert row["status"] != "PASS"
    assert row["status"] == "PARTIAL"
    assert row["pass_eligible"] is False
    assert row["hashes"]["evidence_sha256"] == EVIDENCE_SHA
    assert row["hashes"]["actual_evidence_sha256"] == OTHER_SHA
    assert any("does not match the file" in reason for reason in row["reasons"])


def test_missing_evidence_file_is_partial_not_pass(tmp_path):
    missing = tmp_path / "deleted-evidence.json"
    matrix, _, _, _ = run_matrix(
        tmp_path, {"records.json": [valid_record(tmp_path, evidence_path=missing)]})
    row = row_of(matrix, 23)
    assert row["status"] == "PARTIAL"
    assert row["hashes"]["actual_evidence_sha256"] is None
    assert row["hashes"]["evidence_sha256"] == EVIDENCE_SHA
    assert any("missing" in reason for reason in row["reasons"])
    assert row["pass_eligible"] is False


def test_recomputed_original_path_tamper_withholds_pass(tmp_path):
    original = write_original(tmp_path)
    good = valid_record(tmp_path, original_path=str(original))
    matrix, _, _, _ = run_matrix(tmp_path, {"records.json": [good]})
    assert row_of(matrix, 23)["status"] == "PASS"
    assert row_of(matrix, 23)["hashes"]["original_path_sha256"] == ORIGINAL_SHA

    original.write_bytes(OTHER_BYTES)
    matrix, _, _, _ = run_matrix(
        tmp_path, {"records.json": [valid_record(tmp_path,
                                                original_path=str(original))]})
    row = row_of(matrix, 23)
    assert row["status"] == "PARTIAL"
    assert row["hashes"]["original_path_sha256"] == OTHER_SHA
    assert any("does not match recomputed" in reason for reason in row["reasons"])


def test_missing_original_path_file_is_partial(tmp_path):
    record = valid_record(tmp_path,
                          original_path=str(tmp_path / "absent-original.exe"))
    matrix, _, _, _ = run_matrix(tmp_path, {"records.json": [record]})
    row = row_of(matrix, 23)
    assert row["status"] == "PARTIAL"
    assert any("missing or unreadable" in reason for reason in row["reasons"])


def test_conflicting_hashes_across_records_withhold_pass(tmp_path):
    record = valid_record(tmp_path)
    second = {"issue": 23, "game": "TrackMania Nations Forever",
              "sha256": OTHER_SHA, "status": "PARTIAL"}
    matrix, _, _, _ = run_matrix(
        tmp_path, {"records.json": [record], "extra.json": [second]})
    row = row_of(matrix, 23)
    assert row["status"] == "PARTIAL"
    assert any("input SHA-256 differs" in text for text in row["conflicts"])
    assert row["checks"]["hashes_agree_across_sources"] is False


def test_native_hash_missing_withholds_pass(tmp_path):
    matrix, _, _, _ = run_matrix(
        tmp_path, {"records.json": [valid_record(tmp_path, native_sha=None)]})
    row = row_of(matrix, 23)
    assert row["status"] == "PARTIAL"
    assert row["checks"]["has_native_sha256"] is False
    assert any("native binary SHA-256" in reason for reason in row["reasons"])


def test_fake_or_unexecuted_oracle_withholds_pass(tmp_path):
    for oracle, verified, executed in (
        ("dummy-device", True, True),          # rejected allow-list name
        ("original-windows", "true", True),    # string is not boolean true
        ("original-windows", True, False),     # explicit not executed
    ):
        matrix, _, _, _ = run_matrix(tmp_path, {"records.json": [
            valid_record(tmp_path, oracle=oracle, verified=verified,
                         executed=executed)]})
        row = row_of(matrix, 23)
        assert row["status"] == "PARTIAL", (oracle, verified, executed)
        assert row["pass_eligible"] is False


# ---------------------------------------------------------------------------
# Doc claims and blockers
# ---------------------------------------------------------------------------

def test_doc_pass_claim_alone_is_not_tested(tmp_path):
    doc_only = {
        "issue": 23,
        "game": "TrackMania Nations Forever",
        "status": "PASS",
        "source": "docs/GAME_MATRIX.md",
    }
    matrix, _, _, _ = run_matrix(tmp_path, {"records.json": [doc_only]})
    row = row_of(matrix, 23)
    assert row["status"] == "NOT TESTED"
    assert row["pass_eligible"] is False
    assert row["advisory_claims"][0]["status"] == "PASS"
    assert any("advisory" in reason for reason in row["reasons"])


def test_doc_pass_claim_does_not_upgrade_partial_oracle(tmp_path):
    record = valid_record(tmp_path, verified=False, claimed_status="PASS",
                          source="docs/GAME_MATRIX.md")
    matrix, _, _, _ = run_matrix(tmp_path, {"records.json": [record]})
    row = row_of(matrix, 23)
    assert row["status"] == "PARTIAL"
    assert any("oracle_verified is not the boolean true" in reason
               for reason in row["reasons"])
    assert any("advisory" in reason for reason in row["reasons"])


def test_recorded_blocker_is_blocked_even_with_oracle_fields(tmp_path):
    record = valid_record(tmp_path, issue=20, game="Hitman: Contracts",
                          blocked=True, blocker="no owned Windows binary")
    matrix, _, _, _ = run_matrix(tmp_path, {"records.json": [record]})
    row = row_of(matrix, 20)
    assert row["status"] == "BLOCKED"
    assert row["pass_eligible"] is False
    assert any("blocked" in reason.lower() for reason in row["reasons"])


def test_upstream_claim_flag_is_recorded_but_never_evidence(tmp_path):
    record = valid_record(tmp_path, upstream_claim=True, status="PASS")
    matrix, _, _, _ = run_matrix(tmp_path, {"records.json": [record]})
    row = row_of(matrix, 23)
    assert row["status"] == "PASS"  # the *oracle* passes, not the doc claim
    assert row["advisory_claims"][0]["upstream_claim"] is True


def test_multiple_oracle_candidates_are_partial(tmp_path):
    first = valid_record(tmp_path)
    second = valid_record(tmp_path, scenario="second-scenario",
                          evidence_path=write_evidence(tmp_path, name="second.json"))
    matrix, _, _, _ = run_matrix(
        tmp_path, {"records.json": [first], "second.json": [second]})
    row = row_of(matrix, 23)
    assert row["status"] == "PARTIAL"
    assert row["checks"]["single_oracle_candidate"] is False
    assert any("more than one oracle-bearing" in reason for reason in row["reasons"])


def test_invalid_json_input_is_recorded_and_cannot_pass(tmp_path):
    matrix, _, _, rc = run_matrix(
        tmp_path, raw_files={"broken.json": "{ not json ]"})
    assert rc == 0
    invalid = [entry for entry in matrix["inputs"]
               if entry["status"] == "INVALID"]
    assert len(invalid) == 1
    assert matrix["summary"]["by_status"]["PASS"] == 0


def test_scope_can_be_narrowed_and_missing_issue_is_not_tested(tmp_path):
    matrix, _, _, _ = run_matrix(
        tmp_path, {"records.json": [valid_record(tmp_path)]},
        issues="22,23")
    assert [row["issue"] for row in matrix["rows"]] == [22, 23]
    assert row_of(matrix, 22)["status"] == "NOT TESTED"


# ---------------------------------------------------------------------------
# Determinism, CLI and scope limits
# ---------------------------------------------------------------------------

def test_json_and_markdown_are_byte_deterministic(tmp_path):
    kwargs = {
        "records": {"records.json": [valid_record(tmp_path)]},
        "markdown": True,
    }
    run_matrix(tmp_path, **kwargs)
    first_bytes = (tmp_path / "matrix.json").read_bytes()
    first_md = (tmp_path / "matrix.md").read_text(encoding="utf-8")
    run_matrix(tmp_path, **kwargs)
    assert (tmp_path / "matrix.json").read_bytes() == first_bytes
    assert (tmp_path / "matrix.md").read_text(encoding="utf-8") == first_md
    assert b'"generated_at": "2026-11-25T00:00:00Z"' in first_bytes


def test_cli_entry_point_end_to_end(tmp_path):
    matrix, _, _, rc = run_matrix(
        tmp_path, {"records.json": [valid_record(tmp_path)]},
        use_subprocess=True)
    assert rc == 0
    assert len(matrix["rows"]) == 5
    assert matrix["summary"]["by_status"]["PASS"] == 1


def test_output_declares_no_issue_closure_and_explicit_limits(tmp_path):
    matrix, _, _, _ = run_matrix(
        tmp_path, {"records.json": [valid_record(tmp_path)]})
    assert matrix["issues_closed"] == 0
    assert matrix["policy"]["closes_or_advances_issues"] is False
    assert any("No GitHub issue is closed" in text
               for text in matrix["limitations"])


def test_module_has_no_hard_coded_game_or_evidence_paths(tmp_path):
    source = (REPO_ROOT / "tools" / "game_scenario_matrix.py").read_text(
        encoding="utf-8")
    for forbidden in ("/Users/", "/Downloads/", "TmForever.exe",
                      "Program Files", "Documents/"):
        assert forbidden not in source
