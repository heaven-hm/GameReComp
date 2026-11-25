"""Deterministic guardrails for docs/DECOMP_REUSE_DECISION.md (issue #5).

The decision document is the explicit evidence-based defer for issue #5.  These
checks keep it fail-closed: every inspected family must carry a disposition with
a licence and a portability reason, every cited evidence path must exist in the
repository and be referenced in the prose, no unqualified perfect-completion
claim may appear, and issue #5 must remain OPEN.  No game is run and no backend
or renderer is built here.

Run with: python3 -m pytest tests/test_decomp_reuse_decision.py -q
"""

from __future__ import annotations

import json
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "DECOMP_REUSE_DECISION.md"
ISSUES = ROOT / "docs" / "closure-audit" / "ISSUES.json"

EXPECTED_DISPOSITIONS = {
    "igi-recomp": "PORTABLE-REUSE-ONLY",
    "IGI-WEB": "INSPECTED-NOT-COPIED",
    "PCRecomp": "BASELINE-IN-PLACE",
    "revng": "PROVIDER-WRAPPER-ONLY",
    "NFS recomp": "DEFERRED-NO-EVIDENCE",
}

FENCED_JSON = re.compile(r"```json[ \t]*\n(.*?)\n```", re.DOTALL)
PIN_RE = re.compile(r"^[0-9a-f]{40}$")

# Copied conventions from tests/test_acceptance_docs.py: a perfect-completion
# token is allowed only when the surrounding sentence explicitly negates it.
PERCENT_100 = re.compile(r"100\s*%")
NEGATION = re.compile(
    r"(?:\b(?:not|no|never|none|zero|without|cannot|n't|under|below|less|"
    r"rather|avoid|exclud\w*|refus\w*|deni\w*|non|counterexampl\w*|"
    r"refut\w*|disprov\w*|debunk\w*|impossible|unverifi\w*)\b)|<|!=",
    re.IGNORECASE,
)
ISSUE_CLOSED = re.compile(
    r"(?:\b(?:issue|issues|ticket|tickets)\b[^.\n]{0,50}"
    r"\b(?:closed|resolved|fixed|done|fully\s+complete)\b"
    r"|\b(?:closes?|fixes?|resolves?)\s+#?\d+\b"
    r"|\b#\d+\b[^.\n]{0,30}\b(?:is|are|was|were|has\s+been|have\s+been)\s+"
    r"(?:now\s+)?(?:closed|resolved|fixed|done)\b"
    r"|\bissues?[- ]closed\b)",
    re.IGNORECASE,
)


def read_doc() -> str:
    assert DOC.is_file(), f"missing {DOC.relative_to(ROOT)}"
    return DOC.read_text(encoding="utf-8", errors="replace")


def load_record() -> dict:
    text = read_doc()
    blocks = []
    for match in FENCED_JSON.finditer(text):
        try:
            blocks.append(json.loads(match.group(1)))
        except json.JSONDecodeError as exc:
            raise AssertionError(f"invalid JSON decision record: {exc}") from exc
    records = [block for block in blocks if block.get("issue") == 5]
    assert len(records) == 1, "expected exactly one JSON decision record for issue 5"
    return records[0]


def prose_without_json(text: str) -> str:
    """Human-readable part of the doc, used to prove paths are cited in prose."""
    return FENCED_JSON.sub("", text)


def sentence(text: str, start: int, end: int) -> str:
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


def offending_lines(text: str, pattern: re.Pattern) -> list[tuple[int, str]]:
    found = []
    for match in pattern.finditer(text):
        probe = sentence(text, match.start(), match.end())
        probe = probe.replace("fail-closed", "").replace("fail closed", "")
        if NEGATION.search(probe):
            continue
        number = text.count("\n", 0, match.start()) + 1
        found.append((number, text.splitlines()[number - 1].strip()[:180]))
    return found


class DecompReuseDecisionTest(unittest.TestCase):
    def test_decision_doc_has_one_machine_readable_record(self):
        record = load_record()
        self.assertEqual(record["decision"], "evidence-based defer")
        self.assertEqual(record["status"], "OPEN")
        self.assertIs(record["no_new_backend_or_renderer"], True)

    def test_every_inspected_family_has_its_exact_disposition(self):
        record = load_record()
        families = record["families"]
        self.assertEqual({f["family"] for f in families}, set(EXPECTED_DISPOSITIONS))
        self.assertEqual(len(families), len(EXPECTED_DISPOSITIONS))
        prose = prose_without_json(read_doc())
        for family in families:
            name = family["family"]
            with self.subTest(family=name):
                self.assertEqual(family["disposition"], EXPECTED_DISPOSITIONS[name])
                self.assertIn(name, prose, f"{name} missing from decision prose")
                self.assertIsInstance(family["licence"], str)
                self.assertTrue(family["licence"].strip(), f"{name}: no licence")
                for field in ("reuse", "portability_reason"):
                    self.assertIsInstance(family[field], str)
                    self.assertGreaterEqual(
                        len(family[field].strip()), 20, f"{name}: empty {field}"
                    )

    def test_pins_and_repositories_are_present_or_explicitly_absent(self):
        families = {f["family"]: f for f in load_record()["families"]}
        for name, family in families.items():
            with self.subTest(family=name):
                if name == "NFS recomp":
                    self.assertIsNone(family["repository"])
                    self.assertIsNone(family["pin"])
                else:
                    self.assertTrue(
                        family["repository"].startswith("https://github.com/"),
                        f"{name}: missing upstream repository",
                    )
                    self.assertRegex(family["pin"], PIN_RE, f"{name}: bad pin")
        for name in ("igi-recomp", "IGI-WEB", "PCRecomp"):
            self.assertIn("MIT", families[name]["licence"], name)
        self.assertIn("rev.ng", families["revng"]["licence"])

    def test_every_cited_evidence_path_exists_and_is_referenced_in_prose(self):
        record = load_record()
        prose = prose_without_json(read_doc())
        paths = list(record["issue_evidence"])
        for family in record["families"]:
            paths.extend(family["evidence"])
        self.assertTrue(paths, "decision record cites no evidence paths")
        for raw in paths:
            with self.subTest(path=raw):
                self.assertIsInstance(raw, str)
                path = Path(raw)
                self.assertFalse(path.is_absolute(), f"absolute evidence path: {raw}")
                self.assertNotIn("..", path.parts, f"escaping evidence path: {raw}")
                self.assertTrue((ROOT / path).is_file(), f"missing evidence: {raw}")
                self.assertIn(raw, prose, f"{raw} cited in JSON but absent from prose")

    def test_no_fake_100_percent_pass_claim(self):
        text = read_doc()
        bad = offending_lines(text, PERCENT_100)
        self.assertFalse(
            bad, f"decision doc asserts a perfect-completion percentage: {bad}"
        )
        self.assertIsNone(
            re.search(r"100\s*%\s*(?:PASS|pass|complete|success)", text),
            "decision doc contains a 100%-style PASS claim",
        )

    def test_no_issue_closure_claim_and_issue_5_stays_open(self):
        text = read_doc()
        bad = offending_lines(text, ISSUE_CLOSED)
        self.assertFalse(bad, f"decision doc claims issue closure: {bad}")
        data = json.loads(ISSUES.read_text(encoding="utf-8"))
        issue5 = next(item for item in data if item["number"] == 5)
        self.assertEqual(issue5["state"], "OPEN")
        self.assertIn("defer", text.lower())

    def test_conclusion_rules_out_game_code_and_assets(self):
        record = load_record()
        text = read_doc()
        self.assertIn("## Conclusion", text)
        for phrase in ("no game-specific engine code", "no game assets"):
            self.assertIn(phrase, record["conclusion"].lower(), phrase)
        self.assertIn("no game-specific engine code", text)
        self.assertIn("no game assets", text)

    def test_defer_adds_no_backend_or_renderer(self):
        text = read_doc()
        self.assertIn("no new backend or renderer", text)
        for forbidden in ("new renderer implemented", "new graphics backend added"):
            self.assertNotIn(forbidden, text.lower())


if __name__ == "__main__":
    unittest.main()
