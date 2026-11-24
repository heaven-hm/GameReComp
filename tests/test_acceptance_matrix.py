"""Deterministic tests for the fail-closed acceptance matrix generator.

Run with: python3 -m pytest tests/test_acceptance_matrix.py -q

Every fixture is written into tmp_path: no game corpus, repository path or
evidence file from the host environment is read by these tests.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import acceptance_matrix as am

FIXED_TIME = "2026-11-24T00:00:00Z"
INPUT_SHA = "a" * 64
NATIVE_SHA = "b" * 64
OTHER_SHA = "c" * 64


def write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def corpus_game(name: str = "Cave Story", *, input_sha: str | None = INPUT_SHA,
                native_sha: str | None = NATIVE_SHA, status: str = "PARTIAL",
                launched: bool = True, scenario: bool = False) -> dict:
    return {
        "game": name,
        "status": status,
        "reasons": ["evidence recorded by corpus harness"],
        "original": {"path": f"/evidence/{name}.exe" if input_sha else None,
                     "sha256": input_sha, "architecture": "i386"},
        "native": {"path": f"/evidence/{name}.bin", "sha256": native_sha},
        "pipeline": {"status": "ok", "verification_status": "scenario-pass",
                     "native_sha256": native_sha, "claims_verified": True},
        "launch": {"executed": launched, "exit_code": 0},
        "frames": {"example": {"path": "/evidence/frame.png", "sha256": "d" * 64,
                               "distinct_colors": 3713}},
        "scenario": {"declared": scenario, "complete": scenario},
    }


def oracle_record(tmp_path: Path, *, game: str = "Cave Story",
                  input_sha: str | None = INPUT_SHA,
                  native_sha: str | None = NATIVE_SHA,
                  oracle: str = "original-windows", verified: object = True,
                  scenario: str = "boot-to-menu", evidence_text: str = "menus match",
                  evidence_bytes: bytes = b"original vs native menu frame 12",
                  evidence_sha: str | None = None) -> dict:
    evidence = tmp_path / "scenario-evidence.json"
    evidence.write_bytes(evidence_bytes)
    record = {
        "game": game,
        "sha256": input_sha,
        "native_sha256": native_sha,
        "oracle": oracle,
        "oracle_verified": verified,
        "scenario": scenario,
        "scenario_evidence": evidence_text,
        "evidence_path": str(evidence),
    }
    if evidence_sha is None:
        record["evidence_sha256"] = hashlib.sha256(evidence_bytes).hexdigest()
    else:
        record["evidence_sha256"] = evidence_sha
    return record


def matrix_doc(*rows: tuple[str, str, str | None], section: str = "This repository (measured)",
               claim: bool = False) -> str:
    lines = [f"## {section}", "",
             "| Name | OS | Arch | Port details | Working status |",
             "| --- | --- | --- | --- | --- |"]
    for name, status, sha in rows:
        suffix = "†" if claim else ""
        detail = f"binary hash `{sha}`" if sha else "port details"
        lines.append(f"| {name}{suffix} | Windows | i386 | {detail} | {status} |")
    return "\n".join(lines) + "\n"


def run_matrix(tmp_path: Path, *, corpus: dict | None = None,
               coverage: dict | None = None, docs: list[str] | None = None,
               oracle: dict | None = None, bad_oracle: str | None = None,
               bad_doc: str | None = None, generated_at: str = FIXED_TIME,
               markdown: bool = False) -> tuple[dict, bytes]:
    args: list[str] = []
    if corpus is not None:
        args += ["--corpus-acceptance", str(write_json(tmp_path / "corpus.json", corpus))]
    if coverage is not None:
        args += ["--api-coverage", str(write_json(tmp_path / "coverage.json", coverage))]
    for index, text in enumerate(docs or []):
        path = tmp_path / f"matrix-{index}.md"
        path.write_text(text, encoding="utf-8")
        args += ["--game-matrix", str(path)]
    if oracle is not None:
        args += ["--oracle-evidence", str(write_json(tmp_path / "oracle.json",
                                                     {"records": [oracle]}))]
    if bad_oracle is not None:
        path = tmp_path / "broken-oracle.json"
        path.write_text(bad_oracle, encoding="utf-8")
        args += ["--oracle-evidence", str(path)]
    if bad_doc is not None:
        args += ["--game-matrix", bad_doc]
    out = tmp_path / "matrix.json"
    argv = [*args, "--generated-at", generated_at, "--out", str(out)]
    if markdown:
        argv += ["--markdown", str(tmp_path / "matrix.md")]
    assert am.main(argv) == 0
    return json.loads(out.read_text(encoding="utf-8")), out.read_bytes()


def status_of(report: dict, game: str = "Cave Story") -> str:
    rows = [row for row in report["rows"] if row["game"] == game]
    assert rows, report["rows"]
    return rows[0]["status"]


# ---------------------------------------------------------------------------
# Happy path and determinism
# ---------------------------------------------------------------------------

def test_valid_oracle_yields_pass_with_full_provenance(tmp_path):
    report, _ = run_matrix(
        tmp_path,
        corpus={"games": [corpus_game(status="PARTIAL")]},
        oracle=oracle_record(tmp_path),
        docs=[matrix_doc(("Cave Story", "PASS - scenario oracle", INPUT_SHA))],
    )
    row = next(r for r in report["rows"] if r["game"] == "Cave Story")
    assert row["status"] == "PASS"
    assert row["checks"]["pass_eligible"] is True
    assert row["hashes"]["agree"] is True
    assert row["hashes"]["input_sha256"] == INPUT_SHA
    assert row["hashes"]["native_sha256"] == NATIVE_SHA
    assert row["scenario"]["name"] == "boot-to-menu"
    assert row["oracle"]["name"] == "original-windows"
    assert any("validated original-Windows oracle" in r for r in row["reasons"])


def test_output_is_byte_deterministic_with_fixed_timestamp(tmp_path):
    _, first = run_matrix(tmp_path, corpus={"games": [corpus_game()]},
                          docs=[matrix_doc(("Cave Story", "PARTIAL - launched", None))])
    _, second = run_matrix(tmp_path, corpus={"games": [corpus_game()]},
                           docs=[matrix_doc(("Cave Story", "PARTIAL - launched", None))])
    assert first == second
    assert b'"generated_at": "2026-11-24T00:00:00Z"' in first


def test_markdown_summary_is_generated_from_the_same_rows(tmp_path):
    report, _ = run_matrix(tmp_path, corpus={"games": [corpus_game()]}, markdown=True)
    text = (tmp_path / "matrix.md").read_text(encoding="utf-8")
    assert "| Cave Story | PARTIAL |" in text
    assert f"Input SHA-256" in text
    assert report["summary"]["by_status"]["PARTIAL"] == 1


# ---------------------------------------------------------------------------
# Meta-tests: fabrication and missing oracle can never yield PASS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mutate, expected_check", [
    ({"input_sha": OTHER_SHA}, "oracle_input_matches_corpus"),
    ({"native_sha": OTHER_SHA}, "oracle_native_matches_corpus"),
    ({"verified": False}, "has_real_oracle_flag"),
    ({"verified": "true"}, "has_real_oracle_flag"),
    ({"oracle": "dummy-device"}, "has_real_oracle_name"),
    ({"oracle": "windows-oracle-unexecuted"}, "has_real_oracle_name"),
    ({"oracle": "original"}, "has_real_oracle_name"),
    ({"scenario": ""}, "has_scenario"),
    ({"evidence_text": ""}, "has_scenario_evidence"),
    ({"evidence_sha": OTHER_SHA}, "evidence_hash_matches"),
])
def test_fabricated_or_missing_oracle_gates_never_pass(tmp_path, mutate, expected_check):
    record = oracle_record(tmp_path, **mutate)
    report, _ = run_matrix(
        tmp_path,
        corpus={"games": [corpus_game(status="PASS")]},
        docs=[matrix_doc(("Cave Story", "PASS - scenario oracle", INPUT_SHA))],
        oracle=record,
    )
    assert report["summary"]["by_status"]["PASS"] == 0
    row = next(r for r in report["rows"] if r["game"] == "Cave Story")
    assert row["status"] != "PASS"
    assert row["checks"][expected_check] is False
    assert row["checks"]["pass_eligible"] is False
    assert any("PASS withheld" in reason for reason in row["reasons"])


def test_missing_oracle_module_does_not_pass(tmp_path):
    report, _ = run_matrix(
        tmp_path,
        corpus={"games": [corpus_game(status="PASS")]},
        docs=[matrix_doc(("Cave Story", "PASS - in game", INPUT_SHA))],
    )
    assert report["summary"]["by_status"]["PASS"] == 0
    row = next(r for r in report["rows"] if r["game"] == "Cave Story")
    assert row["status"] == "PARTIAL"
    assert "no oracle evidence record supplied" in " ".join(row["reasons"])


def test_oracle_claim_without_corpus_evidence_is_not_pass(tmp_path):
    report, _ = run_matrix(tmp_path, oracle=oracle_record(tmp_path))
    assert report["summary"]["by_status"]["PASS"] == 0
    assert report["rows"][0]["status"] == "NOT TESTED"
    assert "no corpus acceptance record" in " ".join(report["rows"][0]["reasons"])


def test_malformed_evidence_file_reference_never_passes(tmp_path):
    record = oracle_record(tmp_path)
    record["evidence_path"] = str(tmp_path / "does-not-exist")
    report, _ = run_matrix(tmp_path, corpus={"games": [corpus_game(status="PASS")]},
                           oracle=record)
    assert report["summary"]["by_status"]["PASS"] == 0
    row = report["rows"][0]
    assert row["checks"]["has_evidence_file"] is False
    assert row["checks"]["evidence_hash_matches"] is False


# ---------------------------------------------------------------------------
# Fail-closed status ladder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("game_kwargs, doc_status, expected", [
    ({"status": "BUILD ONLY", "launched": False, "native_sha": NATIVE_SHA},
     None, "BUILD ONLY"),
    ({"status": "BLOCKED", "input_sha": None, "native_sha": None},
     None, "BLOCKED"),
    ({"status": "FAIL", "launched": True}, None, "FAIL"),
    ({"status": "PARTIAL", "launched": True}, None, "PARTIAL"),
    ({"status": "NOT TESTED", "launched": False, "native_sha": None},
     None, "NOT TESTED"),
])
def test_status_ladder_is_evidence_driven(tmp_path, game_kwargs, doc_status, expected):
    docs = [matrix_doc(("Cave Story", doc_status, None))] if doc_status else None
    report, _ = run_matrix(tmp_path, corpus={"games": [corpus_game(**game_kwargs)]},
                           docs=docs)
    assert status_of(report) == expected


def test_conflicting_hashes_between_sources_are_fail(tmp_path):
    report, _ = run_matrix(
        tmp_path,
        corpus={"games": [corpus_game()]},
        docs=[matrix_doc(("Cave Story", "PARTIAL - launched", OTHER_SHA))],
    )
    row = report["rows"][0]
    assert row["status"] == "FAIL"
    assert row["checks"]["hashes_agree_across_sources"] is False
    assert "SHA-256 conflicts" in " ".join(row["reasons"])


def test_upstream_claim_is_not_evidence(tmp_path):
    report, _ = run_matrix(
        tmp_path,
        docs=[matrix_doc(("Fury³", "In game! 1,945 functions", None),
                         section="Upstream PCRecomp projects (claims)",
                         claim=True)],
    )
    row = report["rows"][0]
    assert row["status"] == "NOT TESTED"
    assert row["checks"]["upstream_claim_only"] is True
    assert "upstream claim" in " ".join(row["reasons"])


def test_addendum_not_tested_rows_are_not_tested(tmp_path):
    doc = """## Agent 3 - ordered game acceptance addendum

| # | Game | Original executable | SHA-256 | Native build | Runtime | Graphics | Input | Audio | Gameplay | Modding | Evidence / blocker |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 16 | Hitman: Codename 47 | — | — | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | BLOCKED: no Windows game files |
"""
    report, _ = run_matrix(tmp_path, docs=[doc])
    assert report["summary"]["by_status"]["NOT TESTED"] == 1
    assert report["rows"][0]["game"] == "Hitman: Codename 47"


def test_api_coverage_missing_imports_withhold_pass(tmp_path):
    coverage = {"inputs": {INPUT_SHA: {
        "input_sha256": INPUT_SHA,
        "missing": {"user32.dll": ["MessageBoxW"]},
    }}}
    report, _ = run_matrix(
        tmp_path,
        corpus={"games": [corpus_game(status="PASS")]},
        coverage=coverage,
        oracle=oracle_record(tmp_path),
    )
    assert report["summary"]["by_status"]["PASS"] == 0
    row = report["rows"][0]
    assert row["status"] == "PARTIAL"
    assert row["checks"]["no_unresolved_system_imports"] is False
    assert "unresolved system imports" in " ".join(row["reasons"])


def test_valid_coverage_entry_alone_never_creates_pass(tmp_path):
    coverage = {"inputs": {INPUT_SHA: {"input_sha256": INPUT_SHA, "missing": {}}}}
    report, _ = run_matrix(tmp_path, corpus={"games": [corpus_game()]},
                           coverage=coverage)
    row = report["rows"][0]
    assert row["status"] == "PARTIAL"
    assert row["checks"]["has_api_coverage_record"] is True
    assert row["api_coverage"]["registration_only"] is True


def test_invalid_json_input_is_recorded_and_cannot_pass(tmp_path):
    report, _ = run_matrix(tmp_path, corpus={"games": [corpus_game(status="PASS")]},
                           bad_oracle="{ this is not json ]")
    assert report["summary"]["by_status"]["PASS"] == 0
    bad = [entry for entry in report["inputs"] if entry["status"] == "INVALID"]
    assert len(bad) == 1
    assert bad[0]["kind"] == "oracle-evidence"


def test_doc_only_build_only_row_is_reported_as_build_only(tmp_path):
    report, _ = run_matrix(
        tmp_path,
        docs=[matrix_doc(("Cave Story", "BUILD ONLY - native binary, no launch", None))],
    )
    assert report["rows"][0]["status"] == "BUILD ONLY"


def test_unreadable_matrix_doc_has_one_invalid_provenance_entry(tmp_path):
    missing = str(tmp_path / "missing-matrix.md")
    report, _ = run_matrix(tmp_path, corpus={"games": [corpus_game()]}, bad_doc=missing)
    entries = [entry for entry in report["inputs"]
               if entry["path"] == missing]
    assert len(entries) == 1
    assert entries[0]["status"] == "INVALID"
    assert report["summary"]["by_status"]["PASS"] == 0


def test_doc_parse_recognises_status_sha_and_claim(tmp_path):
    text = matrix_doc(("Cave Story", "PASS - scenario", INPUT_SHA)) + matrix_doc(
        ("Fury³", "In game!", None), section="Upstream PCRecomp projects (claims)",
        claim=True)
    rows = am.parse_game_matrix(text)
    assert [row["status"] for row in rows] == ["PASS", "NOT TESTED"]
    assert rows[0]["sha256"] == INPUT_SHA
    assert rows[1]["upstream_claim"] is True
