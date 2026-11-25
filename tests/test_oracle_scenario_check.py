"""Fail-closed tests for the Windows-oracle scenario validator.

Every test builds synthetic evidence from real bytes in ``tmp_path``: a small
PE-like original, a thin native header, an input script, a few artifact files, a
``windows_oracle`` capture detail and a scenario record bound to them. No game,
endpoint, Wine host, network or issue tracker is touched. Tampering with a byte
on disk or a field in JSON must keep the validator from granting PASS, and an
``executed=true`` flag on its own must never be enough.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import oracle_scenario_check as osc  # noqa: E402

ORIGINAL_BYTES = b"MZ synthetic original windows executable\x00\x01"
NATIVE_BYTES = b"\xcf\xfa\xed\xfe" + b"\x00" * 28
INPUT_BYTES = b'{"script": ["boot", "menu"]}\n'
ARTIFACT_BYTES = {
    "screenshots/000-frame.png": b"\x89PNG synthetic frame",
    "audio/000-audio.wav": b"RIFF synthetic audio",
    "logs/000-stdout.log": b"hello from guest\n",
}
PROCESS_EXIT = 7
_DEFAULT_PROCESS = object()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    return digest(Path(path).read_bytes())


def artifact_entries() -> dict[str, list[dict]]:
    entries: dict[str, list[dict]] = {kind: [] for kind in osc.ARTIFACT_KINDS}
    for rel, data in ARTIFACT_BYTES.items():
        kind = rel.split("/", 1)[0]
        entries[kind].append({"name": Path(rel).name, "path": rel,
                              "sha256": digest(data), "bytes": len(data)})
    return entries


def write_detail(path: Path, *, original: Path, native: Path, script: Path,
                 status: str = "CAPTURED", process: object = _DEFAULT_PROCESS,
                 exit_code: int | str | None = PROCESS_EXIT) -> None:
    detail = {
        "schema": 1, "generator": "tools/windows_oracle.py", "status": status,
        "game": "synthetic", "game_id": "synthetic", "scenario": "boot-to-menu",
        "endpoint": "cli-supplied://synthetic-oracle",
        "process": ({"pid": 4242, "name": "synthetic.exe"}
                    if process is _DEFAULT_PROCESS else process),
        "exit_code": exit_code,
        "original": {"path": str(original), "sha256": file_digest(original)},
        "native": {"path": str(native), "sha256": file_digest(native)},
        "input_script": {"path": str(script), "sha256": file_digest(script)},
    }
    detail.update(artifact_entries())
    path.write_text(json.dumps(detail, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def write_record(path: Path, detail: Path, *, original: Path, native: Path,
                 script: Path, status: str = "CAPTURED", oracle: str = "windows-oracle",
                 executed: bool = True, verified: bool = True) -> None:
    record = {
        "schema": 1, "generator": "tools/windows_oracle.py", "status": status,
        "verification": "CAPTURED" if status == "CAPTURED" else "NOT TESTED",
        "reason": None, "oracle": oracle, "executed": executed,
        "artifacts_dir": "cli-supplied",
        "original_sha256": file_digest(original),
        "native_sha256": file_digest(native),
        "input_script_sha256": file_digest(script),
        "records": [{
            "game": "synthetic", "game_id": "synthetic",
            "sha256": file_digest(original), "native_sha256": file_digest(native),
            "oracle": oracle, "oracle_verified": verified,
            "scenario": "boot-to-menu",
            "scenario_evidence": "synthetic process-level capture",
            "evidence_path": str(detail), "evidence_sha256": file_digest(detail),
            "input_script_sha256": file_digest(script),
        }],
    }
    record.update(artifact_entries())
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path: Path, doc: dict) -> None:
    Path(path).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")


@pytest.fixture
def evidence(tmp_path):
    original = tmp_path / "original.exe"
    original.write_bytes(ORIGINAL_BYTES)
    native = tmp_path / "native.bin"
    native.write_bytes(NATIVE_BYTES)
    script = tmp_path / "input-script.json"
    script.write_bytes(INPUT_BYTES)
    artifacts = tmp_path / "artifacts"
    for rel, data in ARTIFACT_BYTES.items():
        target = artifacts / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    detail = tmp_path / "capture.json"
    record = tmp_path / "scenario.json"

    def refresh_detail(**kwargs):
        write_detail(detail, original=original, native=native, script=script, **kwargs)

    def refresh_record(**kwargs):
        write_record(record, detail, original=original, native=native,
                     script=script, **kwargs)

    refresh_detail()
    refresh_record()

    def check():
        return osc.check_record(record, original=original, native=native,
                                input_script=script, artifacts_dir=artifacts)

    return SimpleNamespace(tmp=tmp_path, original=original, native=native,
                           script=script, artifacts=artifacts, detail=detail,
                           record=record, check=check,
                           refresh_detail=refresh_detail, refresh_record=refresh_record)


def codes(report: dict) -> set[str]:
    return {reason["code"] for reason in report["reasons"]}


def test_complete_record_passes(evidence):
    report = evidence.check()
    assert report["status"] == "PASS"
    assert report["exit_code"] == 0
    assert report["reasons"] == []
    assert all(status == "PASS" for status in report["checks"].values())
    assert report["oracle"]["captured"] is True
    assert report["oracle"]["process_exit_code"] == PROCESS_EXIT
    assert report["hashes"]["original"] == digest(ORIGINAL_BYTES)
    assert report["issue_closed"] is False


def test_tampered_artifact_is_fail(evidence):
    target = evidence.artifacts / "screenshots" / "000-frame.png"
    target.write_bytes(b"\x89PNG TAMPERED frame bytes")
    report = evidence.check()
    assert report["status"] == "FAIL"
    assert "HASH_MISMATCH" in codes(report)
    assert report["checks"]["artifact_hashes"] == "FAIL"


def test_tampered_evidence_detail_is_fail(evidence):
    detail = load_json(evidence.detail)
    detail["process"]["pid"] = 9999  # record still carries the old digest
    save_json(evidence.detail, detail)
    report = evidence.check()
    assert report["status"] == "FAIL"
    assert "HASH_MISMATCH" in codes(report)
    assert report["checks"]["evidence_hash"] == "FAIL"


@pytest.mark.parametrize("patch,expected", [
    ({"process": {}}, "FABRICATED_FLAG"),
    ({"process": None}, "FABRICATED_FLAG"),
    ({"exit_code": "0"}, "FABRICATED_EXIT_CODE"),
    ({"exit_code": None}, "FABRICATED_FLAG"),
    ({"status": "BLOCKED"}, "FABRICATED_FLAG"),
])
def test_flag_only_pass_is_refused(evidence, patch, expected):
    """All flags true, but no captured process / integer exit => never PASS."""
    evidence.refresh_detail(**patch)
    evidence.refresh_record()  # fresh hashes: only the missing evidence is wrong
    report = evidence.check()
    assert report["status"] in ("FAIL", "BLOCKED")
    assert expected in codes(report)
    if expected == "FABRICATED_FLAG":
        assert report["oracle"]["executed"] is True  # the flag really was set


def test_missing_executed_flag_is_blocked(evidence):
    evidence.refresh_record(executed=False)
    report = evidence.check()
    assert report["status"] == "BLOCKED"
    assert "ORACLE_NOT_EXECUTED" in codes(report)
    assert report["checks"]["executed_flag"] == "BLOCKED"


def test_wrong_cli_original_is_fail(evidence):
    other = evidence.tmp / "different-original.exe"
    other.write_bytes(b"MZ a completely different executable")
    report = osc.check_record(evidence.record, original=other,
                              native=evidence.native, input_script=evidence.script,
                              artifacts_dir=evidence.artifacts)
    assert report["status"] == "FAIL"
    assert "HASH_MISMATCH" in codes(report)
    assert report["checks"]["original_hash"] == "FAIL"


def test_missing_input_script_hash_is_blocked(evidence):
    record = load_json(evidence.record)
    del record["input_script_sha256"]
    save_json(evidence.record, record)
    report = evidence.check()
    assert report["status"] == "BLOCKED"
    assert "HASH_MISSING" in codes(report)
    assert report["checks"]["input_script_hash"] == "BLOCKED"


@pytest.mark.parametrize("oracle,expected", [
    ("dummy", "ORACLE_NOT_REAL"),
    ("no-oracle", "ORACLE_NOT_REAL"),
    ("", "ORACLE_UNNAMED"),
])
def test_only_named_real_oracle_accepted(evidence, oracle, expected):
    evidence.refresh_record(oracle=oracle)
    detail = load_json(evidence.detail)
    detail["oracle"] = oracle
    save_json(evidence.detail, detail)
    evidence.refresh_record(oracle=oracle)  # rebind evidence hash after detail edit
    report = evidence.check()
    assert expected in codes(report)
    assert report["status"] in ("FAIL", "BLOCKED")


def test_fabricated_evidence_hash_is_fail(evidence):
    record = load_json(evidence.record)
    record["records"][0]["evidence_sha256"] = "deadbeef"
    save_json(evidence.record, record)
    report = evidence.check()
    assert report["status"] == "FAIL"
    assert "FABRICATED_HASH" in codes(report)
    assert report["checks"]["evidence_hash"] == "FAIL"


def test_unsafe_artifact_path_is_fail(evidence):
    detail = load_json(evidence.detail)
    detail["screenshots"][0]["path"] = "../screenshots/000-frame.png"
    save_json(evidence.detail, detail)
    record = load_json(evidence.record)
    record["screenshots"][0]["path"] = "../screenshots/000-frame.png"
    record["records"][0]["evidence_sha256"] = file_digest(evidence.detail)
    save_json(evidence.record, record)
    report = evidence.check()
    assert report["status"] == "FAIL"
    assert "UNSAFE_ARTIFACT_PATH" in codes(report)


def test_written_pass_claim_does_not_help(evidence):
    evidence.refresh_detail(process={})
    evidence.refresh_record()
    record = load_json(evidence.record)
    record["verdict"] = {"status": "PASS", "reason": "executed on retail Windows"}
    record["oracle_verified"] = True
    save_json(evidence.record, record)
    report = evidence.check()
    assert report["status"] != "PASS"
    assert report["issue_closed"] is False


def test_cli_exit_codes_and_report(evidence, capsys):
    original = evidence.record
    base = [str(original), "--original", str(evidence.original),
            "--native", str(evidence.native), "--input-script", str(evidence.script),
            "--artifacts-dir", str(evidence.artifacts)]
    out = evidence.tmp / "verdict.json"

    assert osc.main(["--record"] + base + ["--out", str(out)]) == 0
    assert load_json(out)["status"] == "PASS"
    capsys.readouterr()

    (evidence.artifacts / "logs" / "000-stdout.log").write_bytes(b"tampered\n")
    assert osc.main(["--record"] + base + ["--out", str(out)]) == 1
    assert load_json(out)["status"] == "FAIL"
    capsys.readouterr()

    (evidence.artifacts / "logs" / "000-stdout.log").write_bytes(
        ARTIFACT_BYTES["logs/000-stdout.log"])
    evidence.refresh_record(executed=False)
    assert osc.main(["--record"] + base + ["--out", str(out)]) == 2
    assert load_json(out)["status"] == "BLOCKED"


def test_validator_has_no_network_dependency():
    source = Path(osc.__file__).read_text(encoding="utf-8")
    for module in ("urllib", "socket", "http.client", "requests", "subprocess"):
        assert module not in source


if __name__ == "__main__":
    pytest.main([__file__])
