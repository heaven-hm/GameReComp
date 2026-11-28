"""Deterministic tests for the fail-closed OS capability matrix generator.

Run with: python3 -m pytest tests/test_os_capability_matrix.py -q

Every fixture is written into tmp_path: no game corpus, repository path or
evidence file from the host environment is read by these tests. The tampering
cases are meta-tests -- a hand-written PASS, a flipped oracle flag, a swapped
hash or a modified evidence file must never yield a matrix PASS.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import os_capability_matrix as ocm

FIXED_TIME = "2026-11-24T00:00:00Z"
INPUT_SHA = "a" * 64
NATIVE_SHA = "b" * 64
OTHER_SHA = "c" * 64
EVIDENCE_BYTES = b"original vs native menu frame 12"


def write_json(path: Path, data: object) -> str:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return str(path)


def acceptance_row(tmp_path: Path, *, os_name: str = "Windows", arch: str = "x86",
                   name: str = "arith-calc", status: str = "PARTIAL",
                   input_sha: str | None = INPUT_SHA,
                   native_sha: str | None = NATIVE_SHA,
                   oracle_input_sha: str | None = None,
                   oracle_native_sha: str | None = None,
                   oracle: bool = True, verified: object = True,
                   oracle_name: str = "original-windows",
                   scenario: str = "boot-to-menu",
                   scenario_text: str = "menus match at frame 12",
                   evidence_bytes: bytes | None = None,
                   evidence_sha: str | None = None,
                   evidence_path: str | None = None,
                   hashes_agree: bool = True, launched: bool = True) -> dict:
    evidence = tmp_path / f"evidence-{name}.json"
    evidence.write_bytes(EVIDENCE_BYTES if evidence_bytes is None else evidence_bytes)
    declared = (hashlib.sha256(evidence.read_bytes()).hexdigest()
                if evidence_sha is None else evidence_sha)
    ep = str(evidence) if evidence_path is None else evidence_path
    row = {
        "game": name,
        "os": os_name,
        "arch": arch,
        "status": status,
        "reasons": ["evidence recorded by corpus harness"],
        "hashes": {
            "input_sha256": input_sha,
            "native_sha256": native_sha,
            "evidence_sha256": declared,
            "agree": hashes_agree,
            "by_source": {"corpus-acceptance": input_sha},
        },
        "checks": {
            "launch_evidenced": launched,
            "hashes_agree_across_sources": hashes_agree,
            "has_real_oracle_flag": verified is True,
            "has_real_oracle_name": oracle_name in ocm.REAL_ORACLES,
        },
        "corpus_status": status,
        "original": {"sha256": input_sha, "pe_type": "PE32" if arch == "x86" else "PE32+"},
        "native": {"sha256": native_sha},
        "launch": {"executed": launched, "exit_code": 0},
        "scenario": {"name": scenario, "evidence": scenario_text},
    }
    if oracle:
        row["oracle"] = {
            "name": oracle_name,
            "verified": verified,
            "valid": True,
            "evidence_path": ep,
            "input_sha256": oracle_input_sha if oracle_input_sha is not None else input_sha,
            "native_sha256": oracle_native_sha if oracle_native_sha is not None else native_sha,
            "source": "oracle.json",
        }
    return row


def refusal_report(*, os_name: str = "Windows", arch: str = "x64",
                   verdict: str = "REFUSED", causes: dict | None = None,
                   error: str | None = None, native: bool = False,
                   reason: str = "") -> dict:
    counts = causes if causes is not None else {"LIFT_ERROR": 2}
    return {
        "schema": "gamerecomp.refusals.v1",
        "os": os_name,
        "arch": arch,
        "verdict": verdict,
        "refused": verdict == "REFUSED",
        "fail_closed": True,
        "counts": {name: count for name, count in counts.items()},
        "causes": {name: {"count": count, "items": [], "sources": []}
                   for name, count in counts.items()},
        "total": sum(counts.values()),
        "native_binary": {"existing": ["build/game"]} if native else {},
        "error": error,
        "reason": reason,
    }


def pipeline_report(*, os_name: str = "Windows", arch: str = "x86",
                    status: str = "partial", reason: str = "launch only",
                    input_sha: str | None = INPUT_SHA,
                    native_sha: str | None = NATIVE_SHA,
                    stages: list[dict] | None = None,
                    gaps: list[str] | None = None,
                    run_status: str = "exited") -> dict:
    stages = stages if stages is not None else [
        {"name": "compile", "status": "ok"},
        {"name": "run", "status": "ok"},
    ]
    return {
        "schema": 1,
        "tool": "GameReComp autorecomp",
        "target": {"os": os_name, "arch": arch},
        "stages": stages,
        "outcome": {"status": status, "reason": reason,
                    "oracle_gaps": gaps if gaps is not None else []},
        "run": {"status": run_status},
        "manifest": {
            "input_sha256": input_sha,
            "binary": {"sha256": native_sha, "path": "build/game"} if native_sha else None,
        },
    }


def run_matrix(tmp_path: Path, *, acceptance: dict | list[dict] | None = None,
               refusal: dict | list[dict] | None = None,
               pipeline: dict | list[dict] | None = None,
               target_map: dict | None = None,
               default_os: str | None = None, default_arch: str | None = None,
               bad_acceptance: str | None = None, markdown: bool = False,
               generated_at: str = FIXED_TIME) -> tuple[dict, bytes]:
    args: list[str] = []
    if acceptance is not None:
        rows = acceptance if isinstance(acceptance, list) else [acceptance]
        args += ["--acceptance-matrix", write_json(tmp_path / "acceptance.json",
                                                   {"schema": 1, "rows": rows})]
    if refusal is not None:
        reports = refusal if isinstance(refusal, list) else [refusal]
        args += ["--refusal-test", write_json(tmp_path / "refusal.json", reports)]
    if pipeline is not None:
        reports = pipeline if isinstance(pipeline, list) else [pipeline]
        args += ["--pipeline-report", write_json(tmp_path / "pipeline.json", reports)]
    if target_map is not None:
        args += ["--target-map", write_json(tmp_path / "targets.json", target_map)]
    if default_os:
        args += ["--default-os", default_os]
    if default_arch:
        args += ["--default-arch", default_arch]
    if bad_acceptance is not None:
        bad = tmp_path / "bad-acceptance.json"
        bad.write_text(bad_acceptance, encoding="utf-8")
        args += ["--acceptance-matrix", str(bad)]
    out = tmp_path / "matrix.json"
    argv = [*args, "--generated-at", generated_at, "--out", str(out)]
    if markdown:
        argv += ["--markdown", str(tmp_path / "matrix.md")]
    assert ocm.main(argv) == 0
    return json.loads(out.read_text(encoding="utf-8")), out.read_bytes()


def cell(report: dict, os_name: str = "Windows", arch: str = "x86") -> dict:
    found = [entry for entry in report["cells"]
             if entry["os"] == os_name and entry["arch"] == arch]
    assert len(found) == 1
    return found[0]


# ---------------------------------------------------------------------------
# Shape, determinism and rendering
# ---------------------------------------------------------------------------

def test_all_twelve_cells_have_status_reason_and_evidence_path(tmp_path):
    report, _ = run_matrix(tmp_path, pipeline=pipeline_report())
    assert len(report["cells"]) == 12
    assert [c["key"] for c in report["cells"][:4]] == [
        "Windows|x86", "Windows|x64", "Windows|.NET", "Windows|Win16"]
    assert report["summary"]["total"] == 12
    assert report["summary"]["by_os"] == {"Windows": 4, "macOS": 4, "Linux": 4}
    assert report["summary"]["by_arch"] == {"x86": 3, "x64": 3, ".NET": 3, "Win16": 3}
    for entry in report["cells"]:
        assert entry["status"] in ocm.STATUSES
        assert entry["reason"]
    assert cell(report)["status"] == "PARTIAL"
    assert cell(report)["evidence_path"] == str(tmp_path / "pipeline.json")
    assert cell(report, "macOS", ".NET")["status"] == "NOT TESTED"
    assert cell(report, "macOS", ".NET")["evidence_path"] is None


def test_output_is_byte_deterministic_with_fixed_timestamp(tmp_path):
    _, first = run_matrix(tmp_path, acceptance=acceptance_row(tmp_path))
    _, second = run_matrix(tmp_path, acceptance=acceptance_row(tmp_path))
    assert first == second
    assert b'"generated_at": "2026-11-24T00:00:00Z"' in first


def test_markdown_has_matrix_rows_and_cell_details(tmp_path):
    report, _ = run_matrix(tmp_path, acceptance=acceptance_row(
        tmp_path, oracle=False), markdown=True)
    text = (tmp_path / "matrix.md").read_text(encoding="utf-8")
    assert "| OS \\ Arch | x86 | x64 | .NET | Win16 |" in text
    assert "| Windows | PARTIAL | NOT TESTED | NOT TESTED | NOT TESTED |" in text
    assert "### Windows / x86 — PARTIAL" in text
    assert str(tmp_path / "acceptance.json") in text
    assert "Summary: PASS: 0; PARTIAL: 1; " in text
    assert report["summary"]["by_status"]["PARTIAL"] == 1


# ---------------------------------------------------------------------------
# PASS gate: only validated oracle evidence can create PASS
# ---------------------------------------------------------------------------

def test_valid_oracle_yields_pass_with_full_provenance(tmp_path):
    report, _ = run_matrix(tmp_path, acceptance=acceptance_row(
        tmp_path, status="PASS"))
    entry = cell(report)
    assert entry["status"] == "PASS"
    assert entry["checks"]["pass_eligible"] is True
    assert entry["checks"]["hashes_agree_across_sources"] is True
    assert entry["checks"]["oracle_hashes_match_cell"] is True
    assert entry["hashes"]["input_sha256"] == INPUT_SHA
    assert entry["hashes"]["native_sha256"] == NATIVE_SHA
    assert entry["hashes"]["evidence_sha256"] == hashlib.sha256(EVIDENCE_BYTES).hexdigest()
    assert entry["oracle"]["name"] == "original-windows"
    assert entry["oracle"]["verified"] is True
    assert entry["oracle"]["scenario"] == "boot-to-menu"
    assert entry["evidence_path"] == str(tmp_path / "evidence-arith-calc.json")
    assert any("validated executed oracle" in r for r in entry["reasons"])
    assert report["summary"]["by_status"]["PASS"] == 1


@pytest.mark.parametrize("mutate, expected_check", [
    ({"verified": False}, "has_real_oracle_flag"),
    ({"verified": "true"}, "has_real_oracle_flag"),
    ({"oracle_name": "dummy-device"}, "has_real_oracle_name"),
    ({"oracle_name": "windows-oracle-unexecuted"}, "has_real_oracle_name"),
    ({"oracle_name": "original"}, "has_real_oracle_name"),
    ({"oracle_input_sha": OTHER_SHA}, "oracle_hashes_match_cell"),
    ({"oracle_native_sha": OTHER_SHA}, "oracle_hashes_match_cell"),
    ({"scenario": ""}, "has_scenario"),
    ({"scenario_text": ""}, "has_scenario_evidence"),
    ({"evidence_sha": OTHER_SHA}, "evidence_hash_matches"),
    ({"evidence_bytes": b"tampered frame bytes",
      "evidence_sha": hashlib.sha256(EVIDENCE_BYTES).hexdigest()},
     "evidence_hash_matches"),
    ({"evidence_path": "MISSING"}, "evidence_hash_matches"),
])
def test_tampered_or_missing_oracle_never_passes(tmp_path, mutate, expected_check):
    if mutate.get("evidence_path") == "MISSING":
        mutate = {**mutate, "evidence_path": str(tmp_path / "does-not-exist")}
    report, _ = run_matrix(
        tmp_path, acceptance=acceptance_row(tmp_path, status="PASS", **mutate))
    entry = cell(report)
    assert report["summary"]["by_status"]["PASS"] == 0
    assert entry["status"] != "PASS"
    assert entry["checks"][expected_check] is False
    assert entry["checks"]["pass_eligible"] is False
    assert any("PASS withheld" in reason for reason in entry["reasons"])


def test_raw_oracle_evidence_record_yields_pass(tmp_path):
    evidence = tmp_path / "raw-evidence.json"
    evidence.write_bytes(EVIDENCE_BYTES)
    record = {
        "game": "arith-calc",
        "os": "win32",
        "arch": "i386",
        "status": "PASS",
        "sha256": INPUT_SHA,
        "native_sha256": NATIVE_SHA,
        "oracle": "original-windows",
        "oracle_verified": True,
        "scenario": "boot-to-menu",
        "scenario_evidence": "menus match at frame 12",
        "evidence_path": str(evidence),
        "evidence_sha256": hashlib.sha256(EVIDENCE_BYTES).hexdigest(),
    }
    report, _ = run_matrix(tmp_path, acceptance=record)
    entry = cell(report)
    assert entry["status"] == "PASS"
    assert entry["oracle"]["name"] == "original-windows"
    assert entry["oracle"]["scenario"] == "boot-to-menu"


def test_written_pass_status_alone_is_not_a_pass(tmp_path):
    report, _ = run_matrix(tmp_path, acceptance=acceptance_row(
        tmp_path, status="PASS", oracle=False))
    entry = cell(report)
    assert report["summary"]["by_status"]["PASS"] == 0
    assert entry["status"] == "PARTIAL"
    assert "no validated oracle" in entry["reason"]
    assert entry["checks"]["pass_eligible"] is False


def test_claim_with_no_hashes_is_not_tested(tmp_path):
    report, _ = run_matrix(tmp_path, acceptance=acceptance_row(
        tmp_path, status="PASS", input_sha=None, native_sha=None,
        oracle=False, launched=False))
    assert cell(report)["status"] == "NOT TESTED"
    assert report["summary"]["by_status"]["PASS"] == 0


def test_conflicting_hashes_across_sources_are_fail(tmp_path):
    report, _ = run_matrix(
        tmp_path,
        acceptance=acceptance_row(tmp_path, status="PASS"),
        pipeline=pipeline_report(input_sha=OTHER_SHA),
    )
    entry = cell(report)
    assert entry["status"] == "FAIL"
    assert entry["checks"]["hashes_agree_across_sources"] is False
    assert "SHA-256 values conflict" in entry["reason"]
    assert report["summary"]["by_status"]["PASS"] == 0


# ---------------------------------------------------------------------------
# Refusals and pipeline reports can only cap
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs, expected", [
    ({"causes": {"LIFT_ERROR": 2}}, "FAIL"),
    ({"causes": {"UNRESOLVED_IMPORT": 3}}, "FAIL"),
    ({"error": "unreadable generated C"}, "BLOCKED"),
    ({"reason": "no Linux runner available", "causes": {}}, "BLOCKED"),
    ({"causes": {"MISSING_NATIVE_BINARY": 1}}, "BUILD ONLY"),
])
def test_typed_refusals_cap_the_cell(tmp_path, kwargs, expected):
    report, _ = run_matrix(tmp_path, refusal=refusal_report(**kwargs))
    assert cell(report, "Windows", "x64")["status"] == expected
    assert cell(report, "Windows", "x64")["checks"]["no_refusal"] is False


def test_accepted_refusal_alone_is_not_tested(tmp_path):
    report, _ = run_matrix(tmp_path, refusal=refusal_report(
        verdict="ACCEPTED", causes={}, native=False))
    entry = cell(report, "Windows", "x64")
    assert entry["status"] == "NOT TESTED"
    assert entry["checks"]["no_refusal"] is True
    assert report["summary"]["by_status"]["PASS"] == 0


def test_pipeline_scenario_pass_claim_is_partial_without_oracle(tmp_path):
    report, _ = run_matrix(tmp_path, pipeline=pipeline_report(
        status="scenario-pass", reason="declared CLI scenario passed"))
    entry = cell(report)
    assert entry["status"] == "PARTIAL"
    assert "no validated oracle" in entry["reason"]
    assert report["summary"]["by_status"]["PASS"] == 0


def test_pipeline_failed_stage_is_fail(tmp_path):
    report, _ = run_matrix(tmp_path, pipeline=pipeline_report(stages=[
        {"name": "compile", "status": "ok"},
        {"name": "run", "status": "error"},
    ]))
    entry = cell(report)
    assert entry["status"] == "FAIL"
    assert entry["checks"]["no_failed_pipeline_stage"] is False


def test_pipeline_oracle_gaps_withhold_pass(tmp_path):
    report, _ = run_matrix(
        tmp_path,
        acceptance=acceptance_row(tmp_path, status="PASS"),
        pipeline=pipeline_report(status="scenario-pass", gaps=["no frame comparison"]),
    )
    entry = cell(report)
    assert report["summary"]["by_status"]["PASS"] == 0
    assert entry["status"] == "PARTIAL"
    assert entry["checks"]["no_oracle_gaps"] is False
    assert "oracle gaps" in entry["reason"]


# ---------------------------------------------------------------------------
# Target resolution, provenance and source hygiene
# ---------------------------------------------------------------------------

def test_unmapped_records_are_reported_without_guessing(tmp_path):
    report, _ = run_matrix(tmp_path, pipeline=pipeline_report(
        os_name="darwin", arch="arm64", status="scenario-pass"))
    assert report["summary"]["record_count"] == 0
    assert report["summary"]["by_status"]["PASS"] == 0
    assert len(report["unmapped"]) == 1
    assert "arm64" not in report["arches"]
    assert "architecture" in report["unmapped"][0]["reason"]
    assert all(c["status"] == "NOT TESTED" for c in report["cells"])


def test_target_map_assigns_but_cannot_grant_pass(tmp_path):
    row = acceptance_row(tmp_path, status="PASS", oracle=False)
    row.pop("os")
    row.pop("arch")
    report, _ = run_matrix(
        tmp_path, acceptance=row,
        target_map={"arith-calc": {"os": "Linux", "arch": "x86"}})
    assert cell(report, "Linux", "x86")["status"] == "PARTIAL"
    assert report["summary"]["by_status"]["PASS"] == 0
    assert cell(report, "Windows", "x86")["status"] == "NOT TESTED"


def test_target_map_only_chooses_the_cell(tmp_path):
    row = acceptance_row(tmp_path, status="PASS")
    row.pop("os")
    row.pop("arch")
    row["original"].pop("pe_type")
    report, _ = run_matrix(
        tmp_path, acceptance=row,
        target_map={"arith-calc": {"os": "Linux", "arch": ".NET"}})
    assert cell(report, "Linux", ".NET")["status"] == "PASS"
    assert report["summary"]["by_status"]["PASS"] == 1
    assert cell(report, "Linux", "x86")["status"] == "NOT TESTED"


def test_defaults_fill_missing_target_metadata(tmp_path):
    report = pipeline_report(status="partial")
    report.pop("target")
    matrix, _ = run_matrix(tmp_path, pipeline=report,
                           default_os="macOS", default_arch=".NET")
    assert cell(matrix, "macOS", ".NET")["status"] == "PARTIAL"
    assert matrix["unmapped"] == []


def test_invalid_json_input_is_recorded_and_cannot_pass(tmp_path):
    report, _ = run_matrix(tmp_path, bad_acceptance="{ this is not json ]")
    bad = [item for item in report["inputs"] if item["status"] == "INVALID"]
    assert len(bad) == 1
    assert bad[0]["kind"] == "acceptance-matrix"
    assert report["summary"]["by_status"]["PASS"] == 0
    assert all(c["status"] == "NOT TESTED" for c in report["cells"])


def test_tool_source_contains_no_hardcoded_machine_paths(tmp_path):
    source = Path(ocm.__file__).read_text(encoding="utf-8")
    for prefix in ("/Users/", "/home/", "/tmp/", "C:\\", "/private/var/"):
        assert prefix not in source


def test_missing_evidence_input_is_rejected():
    with pytest.raises(SystemExit) as exc:
        ocm.main(["--out", "matrix.json"])
    assert exc.value.code == 2
