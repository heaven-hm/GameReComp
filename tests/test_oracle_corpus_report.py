"""Tests for the deterministic, CLI-driven oracle corpus report generator.

Every record and every evidence file is synthetic and created in ``tmp_path``.
No game, host, endpoint or issue tracker is touched. Tampering with an evidence
byte or a claimed digest must withhold a PROVEN verdict, and a GUI or gameplay
scenario must never appear as a proven console scenario.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import oracle_corpus_report as ocr  # noqa: E402

STDOUT_BYTES = b"arith:start\nprimes<200=46\narith:done\n"
MARKER_BYTES = b"input=1,hello world,3\nresult=aGVsbG8gd29ybGQA\nGoodbye!\n"
ORACLE = "original-windows-process"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_evidence(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def console_record(game: str, evidence: Path, data: bytes, *, scenario: str,
                   claim_stdout: bool = False, **overrides: object) -> dict:
    record: dict = {
        "game": game,
        "kind": "console",
        "scenario": scenario,
        "claim": "PROVEN",
        "oracle": ORACLE,
        "matched": True,
        "evidence": {"path": str(evidence), "sha256": digest(data)},
    }
    if claim_stdout:
        record["claimed_stdout_sha256"] = digest(data)
    record.update(overrides)
    return record


def write_records(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "records": records}, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")
    return path


def codes(report: dict) -> set[str]:
    return {reason["code"] for reason in report["reasons"]}


def test_two_console_scenarios_are_proven_and_untested_games_are_listed(tmp_path):
    ev1 = write_evidence(tmp_path / "evidence" / "arith.log", STDOUT_BYTES)
    ev2 = write_evidence(tmp_path / "evidence" / "b64.log", MARKER_BYTES)
    records = [
        console_record("arith-calc.exe", ev1, STDOUT_BYTES,
                       scenario="byte-identical-stdout", claim_stdout=True),
        console_record("base64.exe", ev2, MARKER_BYTES,
                       scenario="scripted-stdin-marker"),
        {"game": "tetris.exe", "kind": "gui", "scenario": "boot-to-menu",
         "claim": "UNTESTED"},
        {"game": "doom.exe", "kind": "gameplay", "scenario": "frame",
         "claim": "UNTESTED"},
    ]
    path = write_records(tmp_path / "records.json", records)

    report = ocr.build_report(path)

    assert report["status"] == "OK"
    assert report["exit_code"] == 0
    assert report["counts"] == {
        "proven_console_scenarios": 2, "untested_games": 2,
        "rejected_claims": 0, "out_of_scope": 0,
    }
    proven = report["proven_console_scenarios"]
    assert [entry["game"] for entry in proven] == ["arith-calc.exe", "base64.exe"]
    assert all(entry["verdict"].startswith("PROVEN") for entry in proven)
    assert all(entry["recomputed_sha256"] == entry["claimed_sha256"]
               for entry in proven)
    assert proven[0]["claimed_stdout_sha256"] == digest(STDOUT_BYTES)
    assert [entry["game"] for entry in report["untested_games"]] == [
        "doom.exe", "tetris.exe"]
    assert all(entry["verdict"] == "UNTESTED"
               for entry in report["untested_games"])
    assert report["no_issue_actions"] is True
    assert report["read_only"] is True
    assert report["source"] == str(path)
    assert report["evidence_root"] == str(tmp_path)


def test_report_is_byte_stable_for_identical_inputs(tmp_path):
    ev = write_evidence(tmp_path / "evidence" / "out.log", STDOUT_BYTES)
    path = write_records(tmp_path / "records.json", [
        console_record("b.exe", ev, STDOUT_BYTES, scenario="z-last"),
        console_record("a.exe", ev, STDOUT_BYTES, scenario="y-first"),
    ])
    first = json.dumps(ocr.build_report(path), indent=2, sort_keys=True) + "\n"
    second = json.dumps(ocr.build_report(path), indent=2, sort_keys=True) + "\n"
    assert first == second
    report = ocr.build_report(path)
    assert [entry["game"] for entry in report["proven_console_scenarios"]] == [
        "a.exe", "b.exe"]


def test_tampered_evidence_bytes_withhold_proven(tmp_path):
    ev = write_evidence(tmp_path / "evidence" / "out.log", STDOUT_BYTES)
    path = write_records(tmp_path / "records.json", [
        console_record("arith-calc.exe", ev, STDOUT_BYTES,
                       scenario="byte-identical-stdout", claim_stdout=True),
    ])
    # Tamper with the bytes on disk after the record was assembled.
    ev.write_bytes(STDOUT_BYTES + b"tampered\n")

    report = ocr.build_report(path)

    assert report["status"] == "FAIL"
    assert report["exit_code"] == 1
    assert report["counts"]["proven_console_scenarios"] == 0
    assert report["counts"]["rejected_claims"] == 1
    assert {"HASH_MISMATCH", "STDOUT_HASH_MISMATCH"} <= codes(report)
    assert all(entry["verdict"] == "REJECTED"
               for entry in report["rejected_claims"])


def test_tampered_claim_digest_withholds_proven(tmp_path):
    ev = write_evidence(tmp_path / "evidence" / "out.log", STDOUT_BYTES)
    record = console_record("base64.exe", ev, MARKER_BYTES,
                            scenario="scripted-stdin-marker")
    record["evidence"]["sha256"] = "0" * 64  # fabricated digest in the record
    path = write_records(tmp_path / "records.json", [record])

    report = ocr.build_report(path)

    assert report["status"] == "FAIL"
    assert "HASH_MISMATCH" in codes(report)
    assert report["counts"]["proven_console_scenarios"] == 0


def test_missing_evidence_is_blocked_not_assumed(tmp_path):
    ev = tmp_path / "evidence" / "absent.log"  # never written
    path = write_records(tmp_path / "records.json", [
        console_record("arith-calc.exe", ev, STDOUT_BYTES,
                       scenario="byte-identical-stdout"),
    ])

    report = ocr.build_report(path)

    assert report["status"] == "BLOCKED"
    assert report["exit_code"] == 2
    assert "EVIDENCE_FILE_MISSING" in codes(report)
    assert report["counts"]["proven_console_scenarios"] == 0


@pytest.mark.parametrize("change", [
    {"oracle": "dummy-device"},       # fake oracle
    {"matched": False},               # comparison did not match
    {"claim": "PROVEN", "evidence": {"path": "../escape.log", "sha256": "a" * 64}},
])
def test_fabricated_claims_are_refused(tmp_path, change):
    ev = write_evidence(tmp_path / "evidence" / "out.log", STDOUT_BYTES)
    record = console_record("arith-calc.exe", ev, STDOUT_BYTES,
                            scenario="byte-identical-stdout")
    record.update(change)
    path = write_records(tmp_path / "records.json", [record])

    report = ocr.build_report(path)

    assert report["status"] in {"FAIL", "BLOCKED"}
    assert report["counts"]["proven_console_scenarios"] == 0
    assert report["counts"]["rejected_claims"] == 1


def test_gui_and_gameplay_proven_claims_are_out_of_scope(tmp_path):
    ev = write_evidence(tmp_path / "evidence" / "out.log", STDOUT_BYTES)
    gui = console_record("tetris.exe", ev, STDOUT_BYTES, scenario="boot-to-menu",
                         kind="gui")
    gameplay = console_record("doom.exe", ev, STDOUT_BYTES, scenario="frame-ok",
                              kind="gameplay")
    path = write_records(tmp_path / "records.json", [gui, gameplay])

    report = ocr.build_report(path)

    assert report["counts"]["proven_console_scenarios"] == 0
    assert report["counts"]["out_of_scope"] == 2
    assert [entry["game"] for entry in report["out_of_scope"]] == [
        "doom.exe", "tetris.exe"]
    assert all(entry["verdict"] == "OUT_OF_SCOPE"
               for entry in report["out_of_scope"])
    scope_text = json.dumps(report["limits"]).lower()
    assert "gui" in scope_text and "gameplay" in scope_text
    assert "do not generalize" in scope_text


def test_contradictory_untested_record_is_refused(tmp_path):
    path = write_records(tmp_path / "records.json", [
        {"game": "tetris.exe", "kind": "gui", "scenario": "boot",
         "claim": "UNTESTED", "matched": True},
    ])
    report = ocr.build_report(path)
    assert "CONTRADICTORY_UNTESTED" in codes(report)
    assert report["counts"]["untested_games"] == 0


def test_conflicting_duplicate_game_is_fail_closed(tmp_path):
    ev = write_evidence(tmp_path / "evidence" / "out.log", STDOUT_BYTES)
    path = write_records(tmp_path / "records.json", [
        console_record("arith-calc.exe", ev, STDOUT_BYTES, scenario="one"),
        {"game": "arith-calc.exe", "kind": "console", "scenario": "two",
         "claim": "UNTESTED"},
    ])
    report = ocr.build_report(path)
    assert report["status"] == "FAIL"
    assert "DUPLICATE_GAME" in codes(report)
    assert report["counts"]["proven_console_scenarios"] == 0
    assert report["counts"]["untested_games"] == 0
    assert report["counts"]["rejected_claims"] == 2


def test_evidence_root_override_is_cli_supplied(tmp_path):
    ev = write_evidence(tmp_path / "elsewhere" / "out.log", STDOUT_BYTES)
    record = console_record("arith-calc.exe", Path("out.log"), STDOUT_BYTES,
                            scenario="byte-identical-stdout")
    record["evidence"]["path"] = "out.log"  # relative, not absolute
    path = write_records(tmp_path / "records.json", [record])
    elsewhere = tmp_path / "elsewhere"

    report = ocr.build_report(path)
    assert report["status"] == "BLOCKED"  # not under the records directory

    overridden = ocr.build_report(path, root=elsewhere)
    assert overridden["status"] == "OK"
    assert overridden["counts"]["proven_console_scenarios"] == 1
    assert overridden["evidence_root"] == str(elsewhere)
    assert overridden["proven_console_scenarios"][0]["evidence_path"] == str(ev)


def test_cli_writes_report_and_returns_exit_code(tmp_path):
    ev = write_evidence(tmp_path / "evidence" / "out.log", STDOUT_BYTES)
    path = write_records(tmp_path / "records.json", [
        console_record("arith-calc.exe", ev, STDOUT_BYTES,
                       scenario="byte-identical-stdout"),
    ])
    out = tmp_path / "reports" / "report.json"

    exit_code = ocr.main(["--records", str(path), "--label", "synthetic",
                          "--out", str(out)])

    assert exit_code == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["label"] == "synthetic"
    assert saved["counts"]["proven_console_scenarios"] == 1
    assert any("do not generalize" in text for text in saved["limits"])


def test_unreadable_records_file_is_blocked(tmp_path):
    report = ocr.build_report(tmp_path / "missing.json")
    assert report["status"] == "BLOCKED"
    assert "RECORDS_UNREADABLE" in codes(report)
    assert all(not report[key] for key in ocr.CATEGORIES)
