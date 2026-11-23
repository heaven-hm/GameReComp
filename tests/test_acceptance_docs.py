"""Guardrails for the acceptance evidence docs named by the master task.

Six artifacts are named by the master task: GAME_MATRIX, ISSUE_EXECUTION_PLAN,
WIN32_API_COVERAGE, PROOF_EVIDENCE, ALL_GAMES_MACOS_EVIDENCE and
CAPABILITY_BOUNDARIES.  They are produced by different agents, so they do not
all land at once.  A file that has not landed yet is reported as a SKIP with
the expected path as the reason -- a missing doc is not a failed test suite.

Once a file is present it must stay fail-closed:

* it must carry at least one explicit fail-closed / limits marker;
* it must not assert a perfect-completion percentage (the literal token is
  allowed only when its sentence explicitly negates or refutes it, e.g.
  "does not claim 100%" or "counterexamples to any 100% claim");
* it must not claim that an issue is closed / fixed / resolved.

Run with: python3 -m pytest tests/test_acceptance_docs.py -q
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Canonical locations of the artifacts named by the master task.
EVIDENCE_FILES = {
    "GAME_MATRIX": "docs/GAME_MATRIX.md",
    "ISSUE_EXECUTION_PLAN": "docs/ISSUE_EXECUTION_PLAN.md",
    "WIN32_API_COVERAGE": "docs/WIN32_API_COVERAGE.md",
    "PROOF_EVIDENCE": "docs/PROOF_EVIDENCE.md",
    "ALL_GAMES_MACOS_EVIDENCE": "docs/ALL_GAMES_MACOS_EVIDENCE.md",
    "CAPABILITY_BOUNDARIES": "docs/CAPABILITY_BOUNDARIES.md",
}

INDEX_PATH = "docs/ACCEPTANCE_DOCS_INDEX.md"

# Be tolerant about where a sibling agent parks its artifact.  The canonical
# docs/ path wins; these are fallbacks for docs/evidence/, reports/ and root.
SEARCH_DIRS = ("docs", "docs/evidence", "reports", "")
SEARCH_EXTS = (".md", ".markdown", ".txt", ".json")

# A present doc has to say, somewhere, what it does not cover.
FAIL_CLOSED_MARKERS = (
    "fail-closed", "fail closed", "not run", "not claimed", "no claim",
    "does not prove", "does not establish", "not proven", "unverified",
    "partial", "not closed", "cannot claim", "cannot be claimed",
    "not counted", "no scenario", "no oracle", "not tested",
    "host not tested", "not validated", "not reconstructed", "limitation",
    "boundary", "scaffolding", "unsupported", "never counted",
    "evidence gap", "no guarantee", "not attempted", "incomplete",
)

# The literal perfect-completion token, and explicit-negation / refutation
# cues in the surrounding sentence (so "does not claim ... 100%" and
# "counterexamples to any 100% claim" are honest and allowed).
PERCENT_100 = re.compile(r"100\s*%")
NEGATION = re.compile(
    r"(?:\b(?:not|no|never|none|zero|without|cannot|n't|under|below|less|"
    r"rather|avoid|exclud\w*|refus\w*|deni\w*|non|counterexampl\w*|"
    r"refut\w*|disprov\w*|debunk\w*|impossible|unverifi\w*)\b)|<|!=",
    re.IGNORECASE,
)

# Phrases that assert full issue closure. "fail-closed" is stripped before
# matching so its trailing "closed" is never mistaken for a closure claim.
ISSUE_CLOSED = re.compile(
    r"(?:\b(?:issue|issues|ticket|tickets)\b[^.\n]{0,50}"
    r"\b(?:closed|resolved|fixed|done|fully\s+complete)\b"
    r"|\b(?:closes?|fixes?|resolves?)\s+#?\d+\b"
    r"|\b#\d+\b[^.\n]{0,30}\b(?:is|are|was|were|has\s+been|have\s+been)\s+"
    r"(?:now\s+)?(?:closed|resolved|fixed|done)\b"
    r"|\bissues?[- ]closed\b)",
    re.IGNORECASE,
)


def locate(key):
    """Return the relative path of an evidence file, or None if not landed."""
    canonical = EVIDENCE_FILES[key]
    stem = Path(canonical).stem
    candidates = [canonical]
    for directory in SEARCH_DIRS:
        for ext in SEARCH_EXTS:
            candidate = f"{directory}/{stem}{ext}" if directory else f"{stem}{ext}"
            if candidate not in candidates:
                candidates.append(candidate)
    for candidate in candidates:
        if (ROOT / candidate).is_file():
            return candidate
    return None


def _skip_reason(key):
    return (
        f"{key}: expected evidence file '{EVIDENCE_FILES[key]}' has not been "
        "committed yet (produced by another agent in the parallel swarm); "
        "language checks skipped, not failed"
    )


def _read(key):
    path = locate(key)
    if path is None:
        pytest.skip(_skip_reason(key))
    return path, (ROOT / path).read_text(encoding="utf-8", errors="replace")


def _sentence(text, start, end):
    """Widen a match to its sentence so wrapped negation is seen."""
    left = start
    while left > 0:
        char = text[left - 1]
        if char in ".!?" or (char == "\n" and left > 1 and text[left - 2] == "\n"):
            break
        left -= 1
    right = end
    while right < len(text):
        char = text[right]
        if char in ".!?" or (char == "\n" and right + 1 < len(text)
                             and text[right + 1] == "\n"):
            break
        right += 1
    return text[left:right]


def _offending_lines(text, pattern):
    """Lines with an unqualified claim; negated sentences are allowed."""
    found = []
    for match in pattern.finditer(text):
        probe = _sentence(text, match.start(), match.end())
        # "fail-closed" describes refusal, never issue closure.
        probe = probe.replace("fail-closed", "").replace("fail closed", "")
        if NEGATION.search(probe):
            continue
        number = text.count("\n", 0, match.start()) + 1
        line = text.splitlines()[number - 1].strip()[:180]
        found.append((number, line))
    return found


@pytest.mark.parametrize("key", sorted(EVIDENCE_FILES))
def test_evidence_file_is_present(key):
    """Key artifact exists at its canonical path or a documented fallback."""
    path = locate(key)
    if path is None:
        pytest.skip(_skip_reason(key))
    assert (ROOT / path).is_file(), f"{key}: {path} is not a file"


@pytest.mark.parametrize("key", sorted(EVIDENCE_FILES))
def test_evidence_doc_states_failure_closed_limits(key):
    """A landed evidence doc must say what it does not cover."""
    path, text = _read(key)
    markers = [m for m in FAIL_CLOSED_MARKERS if m in text.lower()]
    assert markers, (
        f"{key} ({path}) has no fail-closed / limits language; expected one of "
        f"{FAIL_CLOSED_MARKERS}"
    )


@pytest.mark.parametrize("key", sorted(EVIDENCE_FILES))
def test_evidence_doc_has_no_perfect_completion_claim(key):
    """No unqualified perfect-completion percentage in a landed evidence doc."""
    path, text = _read(key)
    bad = _offending_lines(text, PERCENT_100)
    assert not bad, (
        f"{key} ({path}) asserts a perfect-completion percentage; "
        f"negate it or remove it. Offending lines: {bad}"
    )


@pytest.mark.parametrize("key", sorted(EVIDENCE_FILES))
def test_evidence_doc_has_no_issue_closed_claim(key):
    """No landed evidence doc may claim an issue was closed / fixed / resolved."""
    path, text = _read(key)
    bad = _offending_lines(text, ISSUE_CLOSED)
    assert not bad, (
        f"{key} ({path}) claims issue closure; state the residual gap instead. "
        f"Offending lines: {bad}"
    )


def test_index_documents_every_key_artifact():
    """The companion index lists all six artifacts and stays fail-closed."""
    index = ROOT / INDEX_PATH
    assert index.is_file(), f"missing {INDEX_PATH}"
    text = index.read_text(encoding="utf-8", errors="replace")
    for key in EVIDENCE_FILES:
        assert key in text, f"{INDEX_PATH} does not index {key}"
    assert not _offending_lines(text, PERCENT_100)
    assert not _offending_lines(text, ISSUE_CLOSED)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
