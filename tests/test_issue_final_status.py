"""Self-guard for docs/ISSUE_FINAL_STATUS.md (the #1-#28 final status ledger).

The ledger is a readout, not a closure report. These tests fail if:

* any issue is claimed CLOSED / fixed / resolved / complete, in prose or in the
  machine-readable block;
* a perfect-completion ("100%") claim is made without an explicit negation;
* any row claims the game-oracle gate A10 or a bare PASS;
* any of the 28 rows is missing a GitHub state, highest evidence gate, exact
  remaining blocker or verification command;
* the GitHub state is anything other than OPEN, or a closedAt value is set.

The doc is authored by one agent and consumed by coordinators, so the test is
deliberately strict about structure and deliberately tolerant of honest
negations ("No issue is closed", "does not claim 100%").

Run with: python3 -m pytest tests/test_issue_final_status.py -q
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "ISSUE_FINAL_STATUS.md"

BEGIN = "<!-- BEGIN MACHINE-READABLE LEDGER -->"
END = "<!-- END MACHINE-READABLE LEDGER -->"

EXPECTED_NUMBERS = set(range(1, 29))

# An affirmative closure assertion must be accompanied by an in-sentence
# negation cue to be honest ("No issue is closed", "does not claim 100%").
NEGATION = re.compile(
    r"\b(?:no|not|never|none|zero|without|cannot|n't|under|below|less|"
    r"rather|avoid|exclud\w*|refus\w*|deni\w*|non|counterexampl\w*|"
    r"refut\w*|disprov\w*|debunk\w*|impossible|unverifi\w*|fails?|"
    r"unmet|absent|missing)\b|!=",
    re.IGNORECASE,
)

CLOSURE_CLAIMS = (
    re.compile(
        r"\b(?:issue|issues|ticket|tickets)\b[^\n.]{0,70}"
        r"\b(?:closed|resolved|fixed|done|complete|completed|merged)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:closes?|fixes?|resolves?)\s+#\d+\b", re.IGNORECASE),
    re.compile(
        r"#\d+\b[^\n.]{0,45}\b(?:is|are|was|were|has\s+been|have\s+been|now)\s+"
        r"(?:fully\s+|already\s+|officially\s+)?"
        r"(?:closed|resolved|fixed|done|complete)\b",
        re.IGNORECASE,
    ),
)

PERCENT_100 = re.compile(r"100\s*%")


def _read_doc():
    assert DOC.is_file(), f"missing final status ledger: {DOC}"
    return DOC.read_text(encoding="utf-8")


def _json_block(text):
    assert BEGIN in text and END in text, "machine-readable ledger markers missing"
    block = text.split(BEGIN, 1)[1].split(END, 1)[0]
    m = re.search(r"```json\s*(.*?)\s*```", block, re.DOTALL)
    assert m, "machine-readable ledger is not a ```json fenced block"
    return json.loads(m.group(1))


def _norm(value):
    """Treat typographic dashes as their ASCII equivalent for equality checks."""
    return (
        value.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("`", "")
        .replace("**", "")
        .strip()
    )


def _sentence_containing(text, match):
    """Return the sentence-ish slice around a match (newline/./!/?) delimited."""
    start = max(
        text.rfind("\n", 0, match.start()),
        text.rfind(".", 0, match.start()),
        text.rfind("!", 0, match.start()),
        text.rfind("?", 0, match.start()),
    ) + 1
    end_candidates = [
        p
        for p in (
            text.find("\n", match.end()),
            text.find(".", match.end()),
            text.find("!", match.end()),
            text.find("?", match.end()),
        )
        if p != -1
    ]
    end = min(end_candidates) if end_candidates else len(text)
    return text[start:end]


def _unnegated_matches(text, pattern):
    return [
        (m, _sentence_containing(text, m))
        for m in pattern.finditer(text)
        if not NEGATION.search(_sentence_containing(text, m))
    ]


def test_doc_exists_and_names_sources():
    text = _read_doc()
    assert "#1" in text and "#28" in text
    for token in (
        "gh issue list --state all --limit 40",
        "docs/ISSUE_EXECUTION_PLAN.md",
        "docs/GAME_MATRIX.md",
        "c3c943028031d08386e70c70ab189619e6ae25c0",
    ):
        assert token in text, f"missing provenance token: {token}"


def test_machine_readable_block_is_valid_and_complete():
    data = _json_block(_read_doc())
    assert data["schema_version"] == 1
    assert data["document"] == "docs/ISSUE_FINAL_STATUS.md"
    assert data["readout_only"] is True

    v = data["verification"]
    assert v["state_query"] == "gh issue list --state all --limit 40"
    assert "--json number,title,state,stateReason,closedAt" in v["json_query"]
    assert v["rows"] == 28
    assert v["all_open"] is True
    assert v["closure_count"] == 0
    assert v["complete_count"] == 0
    assert v["a10_count"] == 0

    issues = data["issues"]
    assert len(issues) == 28
    assert {i["number"] for i in issues} == EXPECTED_NUMBERS
    assert [i["number"] for i in issues] == sorted(EXPECTED_NUMBERS)


def test_no_issue_claimed_closed_or_complete_in_json():
    for issue in _json_block(_read_doc())["issues"]:
        n = issue["number"]
        assert issue["github_state"] == "OPEN", f"#{n} is not recorded as OPEN"
        assert issue["closed_at"] is None, f"#{n} has a closedAt value"
        assert issue["complete"] is False, f"#{n} is marked complete"
        assert issue["remaining_blocker"], f"#{n} has no remaining blocker"


def test_every_row_has_gate_blocker_and_verification_command():
    for issue in _json_block(_read_doc())["issues"]:
        n = issue["number"]
        for field in ("title", "highest_gate", "remaining_blocker", "verification_command"):
            value = issue.get(field)
            assert isinstance(value, str) and value.strip(), (
                f"#{n} has empty or missing {field}"
            )
        gate = issue["highest_gate"]
        assert "A10" not in gate, f"#{n} claims the original-vs-native oracle gate A10"
        assert not re.search(r"\bPASS\b|\bCOMPLETE\b", gate, re.IGNORECASE), (
            f"#{n} claims a PASS/COMPLETE gate"
        )
        cmd = issue["verification_command"]
        assert ("pytest" in cmd or "verify.sh" in cmd or "build_selftests.sh" in cmd), (
            f"#{n} has no reproducible verification command: {cmd!r}"
        )


def test_markdown_table_matches_json_and_has_all_four_required_columns():
    text = _read_doc()
    data = _json_block(text)
    by_number = {i["number"]: i for i in data["issues"]}

    rows = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("| # | Issue"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            if rows:
                break  # table ended
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or set(cells[0]) <= set("-: "):
            continue
        rows.append(cells)

    assert len(rows) == 28, f"expected 28 markdown rows, found {len(rows)}"
    for cells in rows:
        assert len(cells) == 6, f"unexpected column count: {cells}"
        number = int(re.search(r"\d+", cells[0]).group())
        assert number in EXPECTED_NUMBERS
        issue = by_number[number]
        # GitHub state | highest gate | remaining blocker | verification command
        assert cells[2] == "OPEN", f"#{number} markdown state is {cells[2]!r}"
        assert _norm(cells[3]) == _norm(issue["highest_gate"]), f"#{number} gate drift"
        assert _norm(cells[4]) == _norm(issue["remaining_blocker"]), (
            f"#{number} blocker drift"
        )
        assert _norm(cells[5]) == _norm(issue["verification_command"]), (
            f"#{number} command drift"
        )
    assert {int(re.search(r"\d+", r[0]).group()) for r in rows} == EXPECTED_NUMBERS


def test_no_unnegated_issue_closure_claim_in_prose():
    text = _read_doc()
    bad = [
        (m, s)
        for pattern in CLOSURE_CLAIMS
        for m, s in _unnegated_matches(text, pattern)
    ]
    assert not bad, "unnegated issue-closure claim(s): " + "; ".join(
        repr(s.strip()) for _, s in bad[:5]
    )


def test_no_unnegated_perfect_completion_claim():
    text = _read_doc()
    bad = _unnegated_matches(text, PERCENT_100)
    assert not bad, "unnegated 100% claim(s): " + "; ".join(
        repr(s.strip()) for _, s in bad[:5]
    )
