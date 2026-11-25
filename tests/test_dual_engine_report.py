"""Synthetic-data tests for the dual-engine reporter.

Run with: python3 -m pytest tests/test_dual_engine_report.py -q

Every input is created under tmp_path. No host corpus, game, evidence path or
repository document is read. All hashes in the fixtures are real digests of
bytes written here, and every tamper case changes a claim without changing the
bytes, so the reporter must catch it by recomputation.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from tools import dual_engine_report as der

FIXED_TIME = "2026-11-25T00:00:00Z"

EQUIVALENT_BYTES = b"synthetic process-level observation"
ATTEMPT_BYTES = b"synthetic frida denial stderr"
ORIGINAL_BYTES = b"synthetic original PE bytes"
NATIVE_BYTES = b"synthetic native binary bytes"
INPUT_BYTES = b"synthetic scripted input bytes"
FRAME_BYTES = b"synthetic captured frame bytes"

HOST = "macOS 27.2.1"
CONSUMER = ("frida 17.24.3 (python), frida CLI 17.24.0; "
            "retested with frida 17.27.5")
BLOCK_REASON = ("PermissionDeniedError: unable to access process with pid 99569 "
                "from the current user account")
UNICORN_ROWS = [
    {"address": "0x00401000", "verdict": "agree-cases=1", "eax": 11,
     "signature": "eax=11"},
    {"address": "0x00401020", "verdict": "agree-cases=2", "eax": 23,
     "signature": "eax=23"},
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def make_frida(tmp_path: Path, *, with_equivalent: bool = True,
               tamper_equivalent: bool = False) -> Path:
    attempt = tmp_path / "denial.txt"
    attempt.write_bytes(ATTEMPT_BYTES)
    equivalent_file = tmp_path / "equivalent.log"
    equivalent_file.write_bytes(EQUIVALENT_BYTES)
    doc = {
        "engine": "frida",
        "status": "BLOCKED",
        "reason": BLOCK_REASON,
        "environment": {"os": HOST, "consumer": CONSUMER},
        "attempts": [{
            "command": "frida.attach(<synthetic-pid>)",
            "error": BLOCK_REASON,
            "exit_code": 3,
            "evidence_file": str(attempt),
            "sha256": digest(ATTEMPT_BYTES),
        }],
    }
    if with_equivalent:
        doc["substitute_oracle"] = {
            "engine": "synthetic Wine process oracle",
            "status": "RAN",
            "observation": "process ran without the denied dialog",
            "path": str(equivalent_file),
            "sha256": (digest(b"tampered evidence")
                       if tamper_equivalent else digest(EQUIVALENT_BYTES)),
        }
    return write_json(tmp_path / "frida-status.json", doc)


def make_unicorn(tmp_path: Path, *, raw: bool = False,
                 nonvacuous: bool = True, tamper_counts: bool = False,
                 tamper_hash: bool = False, blocked: bool = False) -> Path:
    report_bytes = (json.dumps({
        "tool": "tools/lift/difftest.py --project",
        "addresses": UNICORN_ROWS,
    }, indent=2) + "\n").encode("utf-8")
    report_file = tmp_path / "difftest-report.json"
    report_file.write_bytes(report_bytes)
    if raw:
        doc = {
            "tool": "tools/lift/difftest.py --project",
            "addresses": UNICORN_ROWS,
            "status": "OK",
            "counts": {"addresses_tested": 99 if tamper_counts else 2,
                       "agree": 2, "diverge": 0, "original_faulted": 0, "other": 0},
        }
        return write_json(tmp_path / "difftest.json", doc)
    doc = {
        "schema": "unicorn_diff_adapter/1",
        "status": "BLOCKED" if blocked else "OK",
        "reason": "synthetic Unicorn block" if blocked else "",
        "counts": {
            "addresses_tested": 99 if tamper_counts else 2,
            "agree": 2, "diverge": 0, "original_faulted": 0, "other": 0,
        },
        "verdicts": UNICORN_ROWS,
        "report_path": str(report_file),
        "report_sha256": (digest(b"tampered report")
                          if tamper_hash else digest(report_bytes)),
        "nonvacuous": nonvacuous,
        "distinct_eax": 2 if nonvacuous else 1,
        "seeds": [1, 2],
        "cases": 2,
    }
    return write_json(tmp_path / "unicorn.json", doc)


def make_oracle(tmp_path: Path, *, tamper: str | None = None) -> Path:
    artifacts_dir = tmp_path / "artifacts"
    frame = artifacts_dir / "screenshots" / "000-frame.png"
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(FRAME_BYTES)
    original = tmp_path / "demo.exe"
    original.write_bytes(ORIGINAL_BYTES)
    native = tmp_path / "demo-native"
    native.write_bytes(NATIVE_BYTES)
    script = tmp_path / "input.json"
    script.write_bytes(INPUT_BYTES)

    screenshot_sha = (digest(b"tampered artifact")
                      if tamper == "artifact" else digest(FRAME_BYTES))
    original_sha = (digest(b"tampered original")
                    if tamper == "original" else digest(ORIGINAL_BYTES))
    detail = {
        "schema": 1,
        "status": "CAPTURED",
        "game": "demo",
        "scenario": "scenario-a",
        "process": {"name": "demo.exe", "exit_code": 0},
        "original": {"path": str(original), "sha256": original_sha},
        "native": {"path": str(native), "sha256": digest(NATIVE_BYTES)},
        "input_script": {"path": str(script), "sha256": digest(INPUT_BYTES)},
        "screenshots": [{"name": "frame.png", "path": "screenshots/000-frame.png",
                         "sha256": screenshot_sha}],
        "audio": [],
        "logs": [],
    }
    detail_path = write_json(tmp_path / "capture-detail.json", detail)
    evidence_sha = (digest(b"tampered detail")
                    if tamper == "evidence" else digest(detail_path.read_bytes()))
    record = {
        "schema": 1,
        "status": "CAPTURED",
        "verification": "CAPTURED",
        "oracle": "windows-oracle",
        "artifacts_dir": str(artifacts_dir),
        "original_sha256": digest(ORIGINAL_BYTES),
        "native_sha256": digest(NATIVE_BYTES),
        "input_script_sha256": digest(INPUT_BYTES),
        "records": [{
            "game": "demo",
            "scenario": "scenario-a",
            "oracle": "windows-oracle",
            "oracle_verified": True,
            "sha256": digest(ORIGINAL_BYTES),
            "native_sha256": digest(NATIVE_BYTES),
            "input_script_sha256": digest(INPUT_BYTES),
            "evidence_path": str(detail_path),
            "evidence_sha256": evidence_sha,
        }],
    }
    return write_json(tmp_path / "oracle.json", record)


def make_hybrid(tmp_path: Path, *, status: str = "BLOCKED") -> Path:
    return write_json(tmp_path / "hybrid.json", {
        "status": status,
        "reason": "hybrid BLOCKED: Frida attach denied; no live capture",
        "process_level_match": True,
        "match_claim": status == "MATCH",
    })


def run_reporter(tmp_path: Path, *, frida: Path | None, unicorn: Path | None,
                 oracle: Path | None, hybrid: Path | None = None,
                 markdown: bool = False) -> tuple[dict, Path]:
    argv = ["--out", str(tmp_path / "report.json"),
            "--generated-at", FIXED_TIME]
    for flag, path in (("--frida-status", frida), ("--unicorn-difftest", unicorn),
                       ("--oracle-records", oracle), ("--hybrid-record", hybrid)):
        if path is not None:
            argv += [flag, str(path)]
    if markdown:
        argv += ["--markdown", str(tmp_path / "report.md")]
    assert der.main(argv) == 0
    out = tmp_path / "report.json"
    return json.loads(out.read_text(encoding="utf-8")), out


def reason_codes(report: dict) -> set[str]:
    return {reason["code"] for reason in report["verdict"]["reasons"]}


def test_pass_only_with_all_recomputed_gates(tmp_path: Path) -> None:
    frida = make_frida(tmp_path)
    unicorn = make_unicorn(tmp_path)
    oracle = make_oracle(tmp_path)
    report, _ = run_reporter(tmp_path, frida=frida, unicorn=unicorn, oracle=oracle)

    assert report["verdict"]["status"] == der.PASS
    assert all(report["gates"].values())
    assert report["hybrid"]["status"] == der.PASS
    assert report["hybrid"]["match_claim"] is True
    assert report["hybrid"]["process_level_only"] is True
    assert "17.24.3" in report["frida"][0]["versions"]
    assert "17.27.5" in report["frida"][0]["versions"]
    assert HOST in report["frida"][0]["host"]
    assert report["frida"][0]["equivalent"]["file"]["recomputed"] is True
    assert report["unicorn"][0]["counts"]["agree"] == 2
    assert report["oracle"][0]["records"][0]["valid"] is True
    assert reason_codes(report) == {"FRIDA_BLOCKED_EQUIVALENT_ACCEPTED"}


def test_full_frida_capture_is_not_process_level_only(tmp_path: Path) -> None:
    frida_path = make_frida(tmp_path)
    doc = json.loads(frida_path.read_text(encoding="utf-8"))
    capture_file = tmp_path / "capture.log"
    capture_file.write_bytes(b"synthetic frida calls")
    doc["status"] = "CAPTURED"
    doc["capture"] = {"calls": ["MessageBoxA"], "path": str(capture_file),
                      "sha256": digest(b"synthetic frida calls")}
    frida_path = write_json(tmp_path / "frida-captured.json", doc)
    report, _ = run_reporter(
        tmp_path, frida=frida_path, unicorn=make_unicorn(tmp_path),
        oracle=make_oracle(tmp_path))
    assert report["verdict"]["status"] == der.PASS
    assert report["hybrid"]["process_level_only"] is False


@pytest.mark.parametrize("which", ["frida", "unicorn-hash", "unicorn-counts",
                                   "oracle-evidence", "oracle-artifact",
                                   "oracle-original"])
def test_tampered_claims_are_detected_and_never_pass(tmp_path: Path,
                                                      which: str) -> None:
    frida = make_frida(tmp_path, tamper_equivalent=(which == "frida"))
    unicorn = make_unicorn(tmp_path, tamper_hash=(which == "unicorn-hash"),
                           tamper_counts=(which == "unicorn-counts"))
    oracle = make_oracle(tmp_path, tamper={
        "oracle-evidence": "evidence", "oracle-artifact": "artifact",
        "oracle-original": "original",
    }.get(which))
    report, _ = run_reporter(tmp_path, frida=frida, unicorn=unicorn, oracle=oracle)
    assert report["verdict"]["status"] == der.BLOCKED
    assert "TAMPERED" in " ".join(reason_codes(report))
    assert report["hybrid"]["status"] == der.BLOCKED
    assert report["hybrid"]["match_claim"] is False


def test_vacuous_unicorn_agreement_is_refused(tmp_path: Path) -> None:
    report, _ = run_reporter(
        tmp_path, frida=make_frida(tmp_path),
        unicorn=make_unicorn(tmp_path, nonvacuous=False),
        oracle=make_oracle(tmp_path))
    assert report["verdict"]["status"] == der.BLOCKED
    assert "UNICORN_VACUOUS" in reason_codes(report)
    assert report["gates"]["unicorn"] is False


def test_blocked_unicorn_is_not_reported_as_agreement(tmp_path: Path) -> None:
    report, _ = run_reporter(
        tmp_path, frida=make_frida(tmp_path),
        unicorn=make_unicorn(tmp_path, blocked=True),
        oracle=make_oracle(tmp_path))
    assert report["verdict"]["status"] == der.BLOCKED
    assert "UNICORN_BLOCKED" in reason_codes(report)
    assert any("synthetic Unicorn block" in reason["message"]
               for reason in report["verdict"]["reasons"])


def test_hybrid_blocked_is_never_upgraded_to_match(tmp_path: Path) -> None:
    report, md_path = run_reporter(
        tmp_path, frida=make_frida(tmp_path), unicorn=make_unicorn(tmp_path),
        oracle=make_oracle(tmp_path),
        hybrid=make_hybrid(tmp_path, status="BLOCKED"), markdown=True)
    assert report["gates"]["frida"] and report["gates"]["unicorn"]
    assert report["gates"]["oracle"]
    assert report["verdict"]["status"] == der.BLOCKED
    assert report["hybrid"]["status"] == der.BLOCKED
    assert report["hybrid"]["match_claim"] is False
    assert report["hybrid"]["blocked_record_count"] == 1
    assert "HYBRID_BLOCKED_NOT_UPGRADED" in reason_codes(report)
    assert report["verdict"]["status"] != "MATCH"
    assert report["hybrid"]["status"] != "MATCH"
    text = md_path.read_text(encoding="utf-8")
    assert "HYBRID_BLOCKED_NOT_UPGRADED" in text
    assert "not upgraded to MATCH" in text


def test_frida_blocked_without_equivalent_is_blocked_with_reasons(
        tmp_path: Path) -> None:
    report, _ = run_reporter(
        tmp_path, frida=make_frida(tmp_path, with_equivalent=False),
        unicorn=make_unicorn(tmp_path), oracle=make_oracle(tmp_path))
    assert report["verdict"]["status"] == der.BLOCKED
    assert report["gates"]["frida"] is False
    reason = next(r for r in report["verdict"]["reasons"]
                  if r["code"] == "FRIDA_BLOCKED")
    assert BLOCK_REASON in reason["message"]
    assert HOST in reason["message"]
    assert "17.24.3" in reason["message"]
    assert "17.27.5" in reason["message"]


def test_missing_inputs_are_not_tested(tmp_path: Path) -> None:
    report, _ = run_reporter(tmp_path, frida=None, unicorn=None, oracle=None)
    assert report["verdict"]["status"] == der.NOT_TESTED
    assert reason_codes(report) == {"NO_INPUT"}


def test_raw_difftest_report_rows_are_recomputed(tmp_path: Path) -> None:
    report, _ = run_reporter(
        tmp_path, frida=make_frida(tmp_path),
        unicorn=make_unicorn(tmp_path, raw=True),
        oracle=make_oracle(tmp_path))
    assert report["verdict"]["status"] == der.PASS
    assert report["unicorn"][0]["shape"] == "difftest-report"
    assert report["unicorn"][0]["counts"]["agree"] == 2
    other = tmp_path / "rerun"
    other.mkdir()
    report, _ = run_reporter(
        other, frida=make_frida(other),
        unicorn=make_unicorn(other, raw=True, tamper_counts=True),
        oracle=make_oracle(other))
    assert report["verdict"]["status"] == der.BLOCKED
    assert "UNICORN_TAMPERED" in reason_codes(report)


def test_repeated_runs_are_byte_deterministic(tmp_path: Path) -> None:
    frida = make_frida(tmp_path)
    unicorn = make_unicorn(tmp_path)
    oracle = make_oracle(tmp_path)

    def render(out: Path) -> bytes:
        rc = der.main([
            "--out", str(out), "--generated-at", FIXED_TIME,
            "--frida-status", str(frida),
            "--unicorn-difftest", str(unicorn),
            "--oracle-records", str(oracle),
        ])
        assert rc == 0
        return out.read_bytes()

    assert render(tmp_path / "first.json") == render(tmp_path / "second.json")


def test_strict_exit_status_refuses_non_pass(tmp_path: Path) -> None:
    argv = ["--out", str(tmp_path / "report.json"),
            "--hybrid-record", str(make_hybrid(tmp_path)),
            "--generated-at", FIXED_TIME, "--strict"]
    assert der.main(argv) == 1


def test_source_has_no_baked_in_paths_or_issue_close_keywords() -> None:
    source = Path(der.__file__).read_text(encoding="utf-8")
    assert "/Users/" not in source
    assert "/tmp/" not in source
    assert not re.search(
        r"(?i)\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#\d+", source)
    assert der.POLICY["hybrid_blocked_never_upgraded_to_match"] is True
