"""Tests for tools.issue_ledger: fail-closed, deterministic closure gating.

Run with: python3 -m pytest tests/test_issue_ledger.py -q

Every case builds synthetic issue JSON and evidence files under tmp_path, then
checks that a CLOSED issue without a real 100%-acceptance evidence file is a
PREMATURE_CLOSE finding and that PASS is emitted only when the full evidence
chain is present. No test touches the network or any real GitHub issue.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import issue_ledger as il

ROOT = Path(__file__).resolve().parent.parent


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_issues(tmp_path: Path, rows: list[dict], name: str = "issues.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return path


def issue(number: int, state: str = "CLOSED", **extra) -> dict:
    row = {
        "number": number,
        "title": f"Issue {number}",
        "state": state,
        "stateReason": "COMPLETED",
        "closedAt": "2026-11-25T00:00:00Z",
    }
    row.update(extra)
    return row


def make_evidence(tmp_path: Path, number: int, *, dirname: str = "evidence",
                  name: str | None = None, acceptance_100=True,
                  criteria: list[dict] | None = None,
                  with_artifacts: bool = True,
                  claim_hash: str | None = None,
                  add_hash_claim: bool = False,
                  claim_issue: int | None = None) -> Path:
    """Create a synthetic 100%-acceptance evidence file (and its artifacts)."""
    edir = tmp_path / dirname
    edir.mkdir(parents=True, exist_ok=True)
    if criteria is None:
        criteria = [
            {"id": "C1", "description": "criterion one", "met": True,
             "evidence": f"artifact-{number}-1.txt"},
            {"id": "C2", "description": "criterion two", "met": True,
             "evidence": f"artifact-{number}-2.txt"},
        ]
    if with_artifacts:
        for criterion in criteria:
            artifact = criterion.get("evidence")
            if isinstance(artifact, str):
                target = edir / artifact
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"proof for {number}\n", encoding="utf-8")
    data = {
        "schema_version": 1,
        "issue": number if claim_issue is None else claim_issue,
        "acceptance_100": acceptance_100,
        "acceptance_criteria": criteria,
    }
    if add_hash_claim:
        data["sha256"] = claim_hash if claim_hash is not None else "pending"
    path = edir / (name or f"issue-{number}.json")
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if add_hash_claim and claim_hash is None:
        data["sha256"] = digest(path.read_bytes())
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def run_ledger(tmp_path: Path, issues_path: Path, evidence_dir: Path,
               *, label: str = "test ledger",
               out_name: str = "ledger.md", json_name: str = "ledger.json"
               ) -> tuple[int, str, dict]:
    out = tmp_path / out_name
    json_out = tmp_path / json_name
    rc = il.main(["--issues", str(issues_path),
                  "--evidence-dir", str(evidence_dir),
                  "--out", str(out), "--json", str(json_out),
                  "--label", label])
    markdown = out.read_text(encoding="utf-8") if out.is_file() else ""
    report = (json.loads(json_out.read_text(encoding="utf-8"))
              if json_out.is_file() else {})
    return rc, markdown, report


def only_row(report: dict) -> dict:
    assert len(report["rows"]) == 1
    return report["rows"][0]


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def test_primitives_are_exact_and_fail_closed(tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"abc")
    assert il.sha256_file(blob) == digest(b"abc")
    assert il.sha256_file(tmp_path / "absent") is None
    assert il.is_sha256(digest(b"abc"))
    assert not il.is_sha256("short") and not il.is_sha256(None)
    assert il.issue_number(12) == 12 and il.issue_number(" 7 ") == 7
    assert il.issue_number(True) is None and il.issue_number("junk") is None
    assert il.normalize_state("closed") == "CLOSED"
    assert il.normalize_state("OPEN") == "OPEN"
    assert il.normalize_state("MERGED") is None


def test_load_issues_rejects_non_arrays_and_non_objects(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        il.load_issues(bad)
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        il.load_issues(bad)


# --------------------------------------------------------------------------
# premature close detection
# --------------------------------------------------------------------------
def test_closed_issue_without_evidence_is_premature_close(tmp_path):
    issues = write_issues(tmp_path, [issue(12)])
    edir = tmp_path / "empty-evidence"
    edir.mkdir()
    rc, markdown, report = run_ledger(tmp_path, issues, edir)

    assert rc == 1
    assert report["overall"] == "FAIL"
    assert report["premature_close_count"] == 1
    assert only_row(report)["verdict"] == "PREMATURE_CLOSE"
    assert report["findings"] == [{
        "type": "PREMATURE_CLOSE", "issue": 12,
        "detail": only_row(report)["reason"],
    }]
    assert "PREMATURE_CLOSE" in markdown
    assert "#12 is CLOSED but no 100%-acceptance evidence file" in markdown


def test_closed_issue_with_partial_mixed_results_keeps_each_verdict(tmp_path):
    issues = write_issues(tmp_path, [issue(1), issue(2, state="OPEN"), issue(3)])
    edir = tmp_path / "evidence"
    edir.mkdir()
    make_evidence(tmp_path, 3)  # only #3 is backed by evidence
    rc, _, report = run_ledger(tmp_path, issues, edir)

    assert rc == 1
    by_number = {row["number"]: row["verdict"] for row in report["rows"]}
    assert by_number == {1: "PREMATURE_CLOSE", 2: "OPEN", 3: "PASS"}
    assert report["counts"] == {
        "PASS": 1, "PREMATURE_CLOSE": 1, "OPEN": 1, "BLOCKED": 0}
    assert [f["issue"] for f in report["findings"]] == [1]


# --------------------------------------------------------------------------
# honest PASS gating
# --------------------------------------------------------------------------
def test_closed_issue_with_valid_evidence_is_pass(tmp_path):
    issues = write_issues(tmp_path, [issue(12)])
    evidence = make_evidence(tmp_path, 12)
    rc, markdown, report = run_ledger(tmp_path, issues, evidence.parent)

    assert rc == 0
    assert report["overall"] == "PASS"
    assert report["findings"] == []
    row = only_row(report)
    assert row["verdict"] == "PASS"
    assert row["evidence_file"] == evidence.relative_to(evidence.parent).as_posix()
    assert row["evidence_sha256"] == digest(evidence.read_bytes())
    assert f"| PASS | 1 |" in markdown
    assert "None. No CLOSED issue lacks a valid 100%-acceptance record." in markdown


@pytest.mark.parametrize("mutation", [
    "no_100_claim",
    "false_100_claim",
    "unmet_criterion",
    "missing_artifact",
    "empty_criteria",
    "unreadable_claim",
    "hash_mismatch",
    "wrong_issue_number",
])
def test_pass_is_gated_on_full_evidence_chain(tmp_path, mutation):
    """Every way evidence can be short of 100% must stay PREMATURE_CLOSE."""
    issues = write_issues(tmp_path, [issue(12)])
    kwargs = {}
    if mutation == "no_100_claim":
        kwargs["acceptance_100"] = None
    elif mutation == "false_100_claim":
        kwargs["acceptance_100"] = False
    elif mutation == "unmet_criterion":
        kwargs["criteria"] = [
            {"id": "C1", "description": "ok", "met": True,
             "evidence": "artifact-12-1.txt"},
            {"id": "C2", "description": "not done", "met": False,
             "evidence": "artifact-12-2.txt"},
        ]
    elif mutation == "missing_artifact":
        kwargs["criteria"] = [
            {"id": "C1", "description": "missing proof", "met": True,
             "evidence": "does-not-exist.txt"},
        ]
        kwargs["with_artifacts"] = False
    elif mutation == "empty_criteria":
        kwargs["criteria"] = []
    elif mutation == "unreadable_claim":
        kwargs = {"add_hash_claim": True, "claim_hash": "not-a-sha256"}
    elif mutation == "hash_mismatch":
        kwargs = {"add_hash_claim": True, "claim_hash": "0" * 64}
    elif mutation == "wrong_issue_number":
        kwargs = {"claim_issue": 99}

    evidence = make_evidence(tmp_path, 12, **kwargs)
    rc, _, report = run_ledger(tmp_path, issues, evidence.parent)

    assert rc == 1, mutation
    assert report["overall"] == "FAIL", mutation
    assert only_row(report)["verdict"] == "PREMATURE_CLOSE", mutation
    assert report["findings"][0]["type"] == "PREMATURE_CLOSE", mutation


def test_no_criteria_field_is_premature_close(tmp_path):
    issues = write_issues(tmp_path, [issue(12)])
    edir = tmp_path / "evidence"
    edir.mkdir()
    path = edir / "issue-12.json"
    path.write_text(json.dumps({
        "schema_version": 1, "issue": 12, "acceptance_100": True,
    }), encoding="utf-8")
    rc, _, report = run_ledger(tmp_path, issues, edir)
    assert rc == 1 and only_row(report)["verdict"] == "PREMATURE_CLOSE"
    assert "no non-empty acceptance_criteria list" in only_row(report)["reason"]


def test_duplicate_evidence_for_one_issue_is_premature_close(tmp_path):
    issues = write_issues(tmp_path, [issue(12)])
    make_evidence(tmp_path, 12, name="issue-12.json")
    make_evidence(tmp_path, 12, name="duplicate-12.json")
    rc, _, report = run_ledger(tmp_path, issues, tmp_path / "evidence")
    assert rc == 1 and only_row(report)["verdict"] == "PREMATURE_CLOSE"
    assert "ambiguous" in only_row(report)["reason"]


def test_relative_and_absolute_artifacts_are_resolved(tmp_path):
    issues = write_issues(tmp_path, [issue(4), issue(5)])
    edir = tmp_path / "evidence"
    edir.mkdir()
    absolute = edir / "absolute-proof.txt"
    absolute.write_text("ok", encoding="utf-8")
    (edir / "issue-4.json").write_text(json.dumps({
        "schema_version": 1, "issue": 4, "acceptance_100": True,
        "acceptance_criteria": [
            {"id": "A", "description": "abs", "met": True,
             "evidence": str(absolute)},
        ],
    }), encoding="utf-8")
    nested = edir / "nested"
    nested.mkdir()
    (nested / "proof.txt").write_text("ok", encoding="utf-8")
    (edir / "issue-5.json").write_text(json.dumps({
        "schema_version": 1, "issue": 5, "acceptance_100": True,
        "acceptance_criteria": [
            {"id": "A", "description": "rel to evidence file", "met": True,
             "evidence": "nested/proof.txt"},
        ],
    }), encoding="utf-8")
    rc, _, report = run_ledger(tmp_path, issues, edir)
    assert rc == 0
    assert {row["verdict"] for row in report["rows"]} == {"PASS"}


def test_unreadable_and_unattributed_json_are_not_evidence(tmp_path):
    issues = write_issues(tmp_path, [issue(12)])
    edir = tmp_path / "evidence"
    edir.mkdir()
    (edir / "broken.json").write_text("{not json", encoding="utf-8")
    (edir / "unrelated.json").write_text(
        json.dumps({"acceptance_100": True}), encoding="utf-8")
    rc, _, report = run_ledger(tmp_path, issues, edir)
    assert rc == 1 and only_row(report)["verdict"] == "PREMATURE_CLOSE"


# --------------------------------------------------------------------------
# OPEN / malformed rows
# --------------------------------------------------------------------------
def test_open_issue_is_not_flagged_even_without_evidence(tmp_path):
    issues = write_issues(tmp_path, [issue(8, state="OPEN", stateReason=None,
                                           closedAt=None)])
    edir = tmp_path / "empty"
    edir.mkdir()
    rc, _, report = run_ledger(tmp_path, issues, edir)
    assert rc == 0
    assert report["overall"] == "PASS"
    row = only_row(report)
    assert row["verdict"] == "OPEN" and row["state"] == "OPEN"
    assert report["findings"] == []


def test_malformed_rows_are_blocked_findings(tmp_path):
    issues = write_issues(tmp_path, [
        {"title": "no number", "state": "CLOSED"},
        {"number": 3, "title": "bad state", "state": "MERGED"},
    ])
    edir = tmp_path / "empty"
    edir.mkdir()
    rc, _, report = run_ledger(tmp_path, issues, edir)
    assert rc == 1
    assert report["counts"]["BLOCKED"] == 2
    assert [f["type"] for f in report["findings"]] == ["BLOCKED", "BLOCKED"]
    # Rows without a number sort last, deterministically.
    assert [f["issue"] for f in report["findings"]] == [3, None]


# --------------------------------------------------------------------------
# determinism / CLI / limits
# --------------------------------------------------------------------------
def test_output_is_byte_stable_and_order_independent(tmp_path):
    issues = write_issues(tmp_path, [issue(2), issue(1), issue(3, state="OPEN")])
    make_evidence(tmp_path, 1)
    edir = tmp_path / "evidence"

    # Use the same output paths so the recorded exact command is identical.
    rc1, md1, rep1 = run_ledger(tmp_path, issues, edir)
    json_bytes1 = (tmp_path / "ledger.json").read_bytes()
    # Rewrite the same issues path with a different row order; output must not change.
    write_issues(tmp_path, [issue(3, state="OPEN"), issue(1), issue(2)])
    rc2, md2, rep2 = run_ledger(tmp_path, issues, edir)
    json_bytes2 = (tmp_path / "ledger.json").read_bytes()
    assert rc1 == rc2 == 1
    assert md1 == md2
    assert rep1 == rep2
    assert json_bytes1 == json_bytes2
    assert [row["number"] for row in rep1["rows"]] == [1, 2, 3]


def test_empty_and_bad_inputs_are_refused_without_vacuous_pass(tmp_path):
    issues = write_issues(tmp_path, [])
    edir = tmp_path / "empty"
    edir.mkdir()
    rc, md, report = run_ledger(tmp_path, issues, edir)
    assert rc == 2 and md == "" and report == {}

    issues = write_issues(tmp_path, [issue(1)])
    rc = il.main(["--issues", str(tmp_path / "missing.json"),
                  "--evidence-dir", str(edir), "--out", str(tmp_path / "x.md"),
                  "--json", str(tmp_path / "x.json")])
    assert rc == 2
    rc = il.main(["--issues", str(issues),
                  "--evidence-dir", str(tmp_path / "no-dir"),
                  "--out", str(tmp_path / "x.md"),
                  "--json", str(tmp_path / "x.json")])
    assert rc == 2


def test_cli_entry_point_subprocess(tmp_path):
    issues = write_issues(tmp_path, [issue(9)])
    edir = tmp_path / "empty"
    edir.mkdir()
    out = tmp_path / "cli.md"
    json_out = tmp_path / "cli.json"
    cmd = [sys.executable, "-m", "tools.issue_ledger",
           "--issues", str(issues), "--evidence-dir", str(edir),
           "--out", str(out), "--json", str(json_out)]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 1
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["premature_close_count"] == 1
    assert "PREMATURE_CLOSE" in out.read_text(encoding="utf-8")


def test_tool_makes_no_network_calls():
    source = (ROOT / "tools" / "issue_ledger.py").read_text(encoding="utf-8")
    for forbidden in ("import requests", "import urllib", "import socket",
                      "subprocess", "urlopen", "requests.get", "gh issue edit"):
        assert forbidden not in source
