"""Synthetic-evidence tests for the Windows -> macOS status report.

Run with: python3 -m pytest tests/test_win_to_mac_report.py -q

Every input is written into tmp_path: no corpus, repository path or evidence
file from the host is read. The tampering cases are meta-tests -- a changed
evidence file, a flipped oracle flag, a swapped hash or an unsupported PE
family must never produce a PASS or a supported row.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import win_to_mac_report as wmr

FIXED_TIME = "2026-11-25T00:00:00Z"
INPUT_SHA = "a" * 64
NATIVE_SHA = "b" * 64
OTHER_SHA = "c" * 64
EVIDENCE_BYTES = b"original vs native menu frame 12"
FRAME_SHA = "d" * 64


def write_json(path: Path, data: object) -> str:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return str(path)


def pe_fact(name: str = "arith-calc", *, sha: str | None = INPUT_SHA,
            pe_type: str = "PE32", architecture: str = "i386",
            status: str = "ok", **extra: object) -> dict:
    fact = {
        "source": "small_games",
        "relative_path": f"games/{name}.exe",
        "status": status,
        "sha256": sha,
        "architecture": architecture,
        "pe_type": pe_type,
        "subsystem": "WINDOWS_GUI",
    }
    fact.update(extra)
    return fact


def corpus_row(name: str = "arith-calc", *, sha: str | None = INPUT_SHA,
               native_sha: str | None = NATIVE_SHA,
               status: str = "PARTIAL", launched: bool = True,
               frame: bool = True, **extra: object) -> dict:
    row = {
        "game": name,
        "status": status,
        "reasons": ["two independently verified non-blank frames"],
        "original": {"path": f"inputs/{name}.exe", "sha256": sha, "pe_type": "PE32"},
        "native": {"path": f"build/{name}", "sha256": native_sha},
        "pipeline": {
            "native_sha256": native_sha,
            "verification_status": "partial",
            "stages": [{"name": "compile", "status": "ok"},
                       {"name": "run", "status": "ok"}],
        },
        "launch": {"executed": launched, "exit_code": 0},
        "frames": {"example": {"sha256": FRAME_SHA, "distinct_colors": 12}
                   if frame else None},
        "scenario": {"declared": False, "complete": False},
    }
    row.update(extra)
    return row


def oracle_record(tmp_path: Path, name: str = "arith-calc", *,
                  verified: object = True, oracle_name: str = "original-windows",
                  input_sha: str | None = INPUT_SHA,
                  native_sha: str | None = NATIVE_SHA,
                  evidence_bytes: bytes | None = None,
                  claim_sha: str | None = None,
                  omit_evidence: bool = False,
                  scenario: str = "boot-to-menu") -> dict:
    evidence = tmp_path / "oracle-evidence.json"
    if omit_evidence:
        evidence.unlink(missing_ok=True)
    else:
        evidence.write_bytes(EVIDENCE_BYTES if evidence_bytes is None else evidence_bytes)
    # The claim is always the digest of the *expected* bytes, so changing the
    # file (or removing it) is a tampering case, not a self-consistent claim.
    declared = (hashlib.sha256(EVIDENCE_BYTES).hexdigest()
                if claim_sha is None else claim_sha)
    return {
        "game": name,
        "oracle": oracle_name,
        "oracle_verified": verified,
        "input_sha256": input_sha,
        "native_sha256": native_sha,
        "scenario": scenario,
        "scenario_evidence": "menus match at frame 12",
        "evidence_path": "oracle-evidence.json",
        "evidence_sha256": declared,
    }


def run_report(tmp_path: Path, *, acceptance: dict | list[dict] | None = None,
               facts: dict | list[dict] | None = None,
               oracle: dict | list[dict] | None = None,
               bad_json: str | None = None, markdown: bool = False,
               generated_at: str = FIXED_TIME) -> tuple[dict, bytes]:
    args: list[str] = []
    if acceptance is not None:
        rows = acceptance if isinstance(acceptance, list) else [acceptance]
        args += ["--corpus-acceptance", write_json(
            tmp_path / "acceptance.json", {"schema": 1, "games": rows})]
    if facts is not None:
        entries = facts if isinstance(facts, list) else [facts]
        args += ["--pe-facts", write_json(
            tmp_path / "inventory.json", {"schema": 1, "entries": entries})]
    if oracle is not None:
        records = oracle if isinstance(oracle, list) else [oracle]
        args += ["--oracle-evidence", write_json(
            tmp_path / "oracle.json", {"records": records})]
    if bad_json is not None:
        bad = tmp_path / "broken.json"
        bad.write_text(bad_json, encoding="utf-8")
        args += ["--pe-facts", str(bad)]
    out = tmp_path / "report.json"
    argv = [*args, "--generated-at", generated_at, "--out", str(out)]
    if markdown:
        argv += ["--markdown", str(tmp_path / "report.md")]
    assert wmr.main(argv) == 0
    return json.loads(out.read_text(encoding="utf-8")), out.read_bytes()


def row_for(report: dict, target: str = "arith-calc") -> dict:
    found = [row for row in report["rows"] if row["target"] == target]
    assert len(found) == 1, [(row["target"], row["status"]) for row in report["rows"]]
    return found[0]


# ---------------------------------------------------------------------------
# Shape, determinism and rendering
# ---------------------------------------------------------------------------

def test_report_has_supported_and_deferred_rows(tmp_path):
    report, _ = run_report(
        tmp_path,
        acceptance=corpus_row(),
        facts=[pe_fact(), pe_fact("oracle64", sha=OTHER_SHA,
                                  pe_type="PE32+", architecture="amd64")],
        oracle=oracle_record(tmp_path),
        markdown=True,
    )
    assert report["source_os"] == "Windows" and report["target_os"] == "macOS"
    assert report["summary"]["total"] == 2
    assert report["summary"]["supported"] == 1
    assert report["summary"]["deferred"] == 1
    assert row_for(report)["status"] == "PASS"
    assert row_for(report, "oracle64")["status"] == "DEFERRED"
    assert all(row["status"] in wmr.STATUSES for row in report["rows"])
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "# Windows -> macOS status report" in text
    assert "## Supported PE32 x86 rows" in text
    assert "## Deferred rows (unsupported architectures)" in text
    assert "| oracle64 | x64 | **DEFERRED** |" in text
    assert "## Limits" in text


def test_output_is_byte_deterministic_with_fixed_timestamp(tmp_path):
    _, first = run_report(tmp_path, acceptance=corpus_row(), facts=pe_fact(),
                          oracle=oracle_record(tmp_path))
    _, second = run_report(tmp_path, acceptance=corpus_row(), facts=pe_fact(),
                           oracle=oracle_record(tmp_path))
    assert first == second
    assert b'"generated_at": "2026-11-25T00:00:00Z"' in first


def test_no_hardcoded_machine_paths_in_tool():
    source = Path(wmr.__file__).read_text(encoding="utf-8")
    for prefix in ("/Users/", "/home/", "/tmp/", "C:\\", "/private/var/"):
        assert prefix not in source


def test_missing_evidence_input_is_rejected(tmp_path):
    with pytest.raises(SystemExit) as exc:
        wmr.main(["--out", str(tmp_path / "report.json")])
    assert exc.value.code == 2


def test_invalid_json_is_recorded_and_cannot_pass(tmp_path):
    report, _ = run_report(tmp_path, acceptance=corpus_row(),
                           bad_json="{ not json ]")
    bad = [item for item in report["inputs"] if item["status"] == "INVALID"]
    assert len(bad) == 1 and bad[0]["kind"] == "pe-facts"
    assert report["summary"]["by_status"]["PASS"] == 0
    assert row_for(report)["status"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Supported x86 rows: evidence ladder and the sole PASS gate
# ---------------------------------------------------------------------------

def test_partial_without_oracle(tmp_path):
    report, _ = run_report(tmp_path, acceptance=corpus_row(), facts=pe_fact())
    row = row_for(report)
    assert row["status"] == "PARTIAL"
    assert row["checks"]["pass_eligible"] is False
    assert row["checks"]["supported_arch"] is True
    assert report["summary"]["by_status"]["PASS"] == 0
    assert any("no validated oracle" in reason for reason in row["reasons"])


def test_written_pass_status_is_advisory_only(tmp_path):
    report, _ = run_report(tmp_path, acceptance=corpus_row(status="PASS"),
                           facts=pe_fact())
    row = row_for(report)
    assert row["corpus_status"] == "PASS"
    assert row["status"] == "PARTIAL"
    assert report["summary"]["by_status"]["PASS"] == 0


def test_not_tested_with_no_corpus_evidence(tmp_path):
    report, _ = run_report(tmp_path, facts=pe_fact())
    row = row_for(report)
    assert row["status"] == "NOT TESTED"
    assert "no corpus acceptance or oracle evidence" in row["reason"]


def test_valid_oracle_yields_pass_with_full_provenance(tmp_path):
    report, _ = run_report(tmp_path, acceptance=corpus_row(), facts=pe_fact(),
                           oracle=oracle_record(tmp_path))
    row = row_for(report)
    assert row["status"] == "PASS"
    assert row["checks"]["pass_eligible"] is True
    assert row["checks"]["evidence_hash_matches"] is True
    assert row["checks"]["oracle_hashes_match"] is True
    assert row["hashes"]["actual_evidence_sha256"] == hashlib.sha256(
        EVIDENCE_BYTES).hexdigest()
    assert row["oracle"]["name"] == "original-windows"
    assert row["oracle"]["verified"] is True
    assert report["summary"]["by_status"]["PASS"] == 1


@pytest.mark.parametrize("mutate, expected_check", [
    ({"verified": False}, "has_real_oracle_flag"),
    ({"verified": "true"}, "has_real_oracle_flag"),
    ({"oracle_name": "dummy-device"}, "has_real_oracle_name"),
    ({"oracle_name": "windows-oracle-unexecuted"}, "has_real_oracle_name"),
    ({"oracle_name": "headless"}, "has_real_oracle_name"),
    ({"input_sha": OTHER_SHA}, "hashes_agree_across_sources"),
    ({"native_sha": OTHER_SHA}, "hashes_agree_across_sources"),
    ({"scenario": ""}, "has_scenario"),
    ({"claim_sha": OTHER_SHA}, "evidence_hash_matches"),
    ({"omit_evidence": True}, "evidence_hash_matches"),
    ({"evidence_bytes": b"tampered frame bytes"}, "evidence_hash_matches"),
])
def test_tampered_or_invalid_oracle_never_passes(tmp_path, mutate, expected_check):
    report, _ = run_report(tmp_path, acceptance=corpus_row(), facts=pe_fact(),
                           oracle=oracle_record(tmp_path, **mutate))
    row = row_for(report)
    assert report["summary"]["by_status"]["PASS"] == 0
    assert row["status"] != "PASS"
    assert row["checks"][expected_check] is False
    assert row["checks"]["pass_eligible"] is False
    assert any("PASS withheld" in reason or reason.startswith("FAIL:")
               for reason in row["reasons"])


def test_corpus_failure_is_fail_and_refusal_is_blocked(tmp_path):
    failed, _ = run_report(tmp_path, acceptance=corpus_row(status="FAIL"),
                           facts=pe_fact())
    assert row_for(failed)["status"] == "FAIL"
    refused, _ = run_report(
        tmp_path,
        acceptance=corpus_row(refusal={"refused": True, "verdict": "REFUSED"}),
        facts=pe_fact(), oracle=oracle_record(tmp_path))
    assert row_for(refused)["status"] == "BLOCKED"
    assert refused["summary"]["by_status"]["PASS"] == 0


def test_hash_conflict_between_corpus_and_pe_fact_is_fail(tmp_path):
    report, _ = run_report(tmp_path, acceptance=corpus_row(sha=OTHER_SHA),
                           facts=pe_fact(), oracle=oracle_record(tmp_path))
    row = row_for(report)
    assert row["status"] == "FAIL"
    assert row["checks"]["hashes_agree_across_sources"] is False
    assert report["summary"]["by_status"]["PASS"] == 0


def test_failed_pipeline_stage_withholds_pass(tmp_path):
    report, _ = run_report(
        tmp_path,
        acceptance=corpus_row(pipeline={"native_sha256": NATIVE_SHA,
                                        "stages": [{"name": "lift", "status": "error"}]}),
        facts=pe_fact(), oracle=oracle_record(tmp_path))
    row = row_for(report)
    assert row["status"] != "PASS"
    assert row["checks"]["no_failed_stage"] is False
    assert report["summary"]["by_status"]["PASS"] == 0


def test_embedded_oracle_in_acceptance_matrix_row_can_pass(tmp_path):
    evidence = tmp_path / "embedded-evidence.json"
    evidence.write_bytes(EVIDENCE_BYTES)
    row = {
        "game": "arith-calc", "os": "Windows", "arch": "x86", "status": "PARTIAL",
        "hashes": {"input_sha256": INPUT_SHA, "native_sha256": NATIVE_SHA,
                   "evidence_sha256": hashlib.sha256(EVIDENCE_BYTES).hexdigest()},
        "checks": {"launch_evidenced": True, "hashes_agree_across_sources": True,
                   "has_real_oracle_flag": True, "has_real_oracle_name": True},
        "original": {"sha256": INPUT_SHA, "pe_type": "PE32"},
        "native": {"sha256": NATIVE_SHA},
        "launch": {"executed": True, "exit_code": 0},
        "scenario": {"name": "boot-to-menu", "evidence": "menus match at frame 12"},
        "oracle": {"name": "original-windows", "verified": True, "valid": True,
                   "evidence_path": "embedded-evidence.json",
                   "input_sha256": INPUT_SHA, "native_sha256": NATIVE_SHA},
    }
    report, _ = run_report(tmp_path, acceptance=row, facts=pe_fact())
    found = row_for(report)
    assert found["status"] == "PASS"
    assert found["checks"]["evidence_hash_matches"] is True


# ---------------------------------------------------------------------------
# Unsupported families are DEFERRED, never supported rows
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fact, family", [
    (pe_fact("oracle64", sha=OTHER_SHA, pe_type="PE32+", architecture="amd64"), "x64"),
    (pe_fact("dotnet-app", sha=OTHER_SHA, is_dotnet=True), ".NET"),
    (pe_fact("legacy16", sha=OTHER_SHA, pe_type="NE", architecture="NE"), "Win16"),
    (pe_fact("mystery", sha=OTHER_SHA, pe_type="PE32", architecture="mips"), "unknown"),
])
def test_unsupported_arch_is_deferred_even_with_valid_oracle(tmp_path, fact, family):
    name = fact["relative_path"].split("/")[-1].removesuffix(".exe")
    oracle = oracle_record(tmp_path, name=name)
    report, _ = run_report(tmp_path, acceptance=corpus_row(name=name), facts=fact,
                           oracle=oracle)
    row = row_for(report, name)
    assert row["family"] == family
    assert row["status"] == "DEFERRED"
    assert row["deferred"] is True and row["supported"] is False
    assert row["checks"]["pass_eligible"] is False
    assert row["checks"]["supported_arch"] is False
    assert "attached oracle claim is ignored" in row["reason"]
    assert report["summary"]["by_status"]["PASS"] == 0


def test_corpus_without_pe_facts_is_blocked(tmp_path):
    report, _ = run_report(tmp_path, acceptance=corpus_row(),
                           oracle=oracle_record(tmp_path))
    row = row_for(report)
    assert row["status"] == "BLOCKED"
    assert row["checks"]["has_pe_fact"] is False
    assert "no PE facts supplied" in row["reason"]
    assert report["summary"]["by_status"]["PASS"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
