"""Machine-checked closure gate for GitHub issues #1-#28.

The two issue docs are readouts, not closure reports. This test enforces the
project rule that an issue is never marked complete without evidence:

  complete == True  =>  evidence_file exists in the repo
                        AND every acceptance criterion is met
                        AND each met criterion cites an existing artifact.

The gate lives in the `<!-- BEGIN CLOSURE GATE -->` JSON block of
docs/ISSUE_WORK_LEDGER.md and is cross-checked against that document's
machine-readable readout ledger and against docs/ISSUE_STATUS.md.

Run with: python3 -m pytest tests/test_issue_closure_gate.py -q
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "ISSUE_WORK_LEDGER.md"
STATUS = ROOT / "docs" / "ISSUE_STATUS.md"

BEGIN = "<!-- BEGIN CLOSURE GATE -->"
END = "<!-- END CLOSURE GATE -->"
EXPECTED_NUMBERS = set(range(1, 29))


def _ledger_text():
    assert LEDGER.is_file(), f"missing issue work ledger: {LEDGER}"
    return LEDGER.read_text(encoding="utf-8")


def _gate_block(text):
    assert BEGIN in text and END in text, "closure-gate markers missing from ledger"
    block = text.split(BEGIN, 1)[1].split(END, 1)[0]
    m = re.search(r"```json\s*(.*?)\s*```", block, re.DOTALL)
    assert m, "closure gate is not a ```json fenced block"
    return json.loads(m.group(1))


def _all_json_blocks(text):
    return [json.loads(m.group(1)) for m in
            re.finditer(r"```json\s*(.*?)\s*```", text, re.DOTALL)]


def _readout_block(text):
    blocks = [b for b in _all_json_blocks(text)
              if isinstance(b, dict) and b.get("readout_only") is True]
    assert len(blocks) == 1, "expected exactly one readout ledger JSON block"
    return blocks[0]


def _gate():
    return _gate_block(_ledger_text())


def _exists(rel):
    return isinstance(rel, str) and bool(rel.strip()) and (ROOT / rel).is_file()


def test_gate_block_is_valid_and_complete():
    gate = _gate()
    assert gate["schema_version"] == 1
    assert gate["document"] == "docs/ISSUE_WORK_LEDGER.md"
    assert gate["closure_gate"] is True
    assert "rule" in gate and gate["rule"].strip()
    assert gate["evidence_file_pattern"] == "evidence/issues/issue-NN.json"
    assert "summary" in gate and isinstance(gate["summary"], dict)


def test_all_28_issues_are_present_with_unique_evidence_files():
    gate = _gate()
    issues = gate["issues"]
    assert len(issues) == 28, f"expected 28 gate rows, found {len(issues)}"
    numbers = [i["number"] for i in issues]
    assert numbers == sorted(EXPECTED_NUMBERS), f"gate numbers are wrong: {numbers}"
    assert len(set(numbers)) == 28, "duplicate issue numbers in gate"

    paths = [i.get("evidence_file") for i in issues]
    assert len(set(paths)) == 28, "evidence_file paths must be unique per issue"
    for i in issues:
        n = i["number"]
        assert re.fullmatch(r"evidence/issues/issue-\d{2}\.json",
                            i["evidence_file"]), (
            f"#{n} evidence_file does not follow the documented pattern"
        )
        assert i["evidence_file"].endswith(f"{n:02d}.json"), (
            f"#{n} evidence_file does not match its issue number"
        )


def test_every_issue_has_criteria_with_explicit_met_flags():
    for issue in _gate()["issues"]:
        n = issue["number"]
        assert issue["github_state"] in ("OPEN", "CLOSED"), f"#{n} bad github_state"
        assert isinstance(issue["complete"], bool), f"#{n} complete is not boolean"
        criteria = issue.get("acceptance_criteria")
        assert isinstance(criteria, list) and criteria, f"#{n} has no criteria"
        ids = [c.get("id") for c in criteria]
        assert len(ids) == len(set(ids)), f"#{n} has duplicate criterion ids"
        for c in criteria:
            assert isinstance(c, dict), f"#{n} has a malformed criterion"
            for field in ("id", "description", "met", "evidence"):
                assert field in c, f"#{n} criterion missing {field}"
            assert isinstance(c["met"], bool), f"#{n}/{c['id']} met is not boolean"
            assert isinstance(c["description"], str) and c["description"].strip(), (
                f"#{n}/{c['id']} has no description"
            )


def test_no_issue_marked_complete_without_evidence_and_all_criteria_met():
    """Core closure gate: complete => evidence file exists AND all criteria met."""
    for issue in _gate()["issues"]:
        n = issue["number"]
        if not issue["complete"]:
            continue  # an OPEN issue may have arbitrary unmet criteria
        assert _exists(issue["evidence_file"]), (
            f"#{n} is marked complete but its evidence file "
            f"{issue['evidence_file']} does not exist"
        )
        unmet = [c["id"] for c in issue["acceptance_criteria"] if not c["met"]]
        assert not unmet, f"#{n} is marked complete with unmet criteria: {unmet}"
        for c in issue["acceptance_criteria"]:
            assert _exists(c["evidence"]), (
                f"#{n}/{c['id']} is met but cites missing evidence {c['evidence']!r}"
            )


def test_met_criterion_always_cites_existing_evidence():
    """A criterion cannot be flipped to met without an artifact to back it."""
    for issue in _gate()["issues"]:
        n = issue["number"]
        for c in issue["acceptance_criteria"]:
            if c["met"]:
                assert _exists(c["evidence"]), (
                    f"#{n}/{c['id']} is met without an existing evidence artifact"
                )


def test_gate_agrees_with_readout_ledger_and_summary():
    gate = _gate()
    readout = _readout_block(_ledger_text())
    readouts = {i["number"]: i for i in readout["issues"]}
    assert set(readouts) == EXPECTED_NUMBERS, "readout ledger is incomplete"

    complete_count = 0
    all_open = True
    for issue in gate["issues"]:
        n = issue["number"]
        assert issue["github_state"] == readouts[n]["github_state"], (
            f"#{n} github_state drift between gate and readout ledger"
        )
        assert issue["complete"] == readouts[n]["complete"], (
            f"#{n} complete flag drift between gate and readout ledger"
        )
        if issue["complete"]:
            complete_count += 1
        if issue["github_state"] != "OPEN":
            all_open = False

    summary = gate["summary"]
    assert summary["issues"] == 28
    assert summary["complete_count"] == complete_count, "complete_count drift"
    assert summary["all_open"] is all_open, "all_open drift"


def test_status_doc_points_at_the_gate():
    text = STATUS.read_text(encoding="utf-8")
    assert "docs/ISSUE_WORK_LEDGER.md" in text
    assert "tests/test_issue_closure_gate.py" in text
    assert "BEGIN CLOSURE GATE" in text
    for token in ("gameplay", "Linux runner", "11 CLOSED", "0e0e5de"):
        assert token in text, f"status doc is missing the gate caveat token: {token}"
