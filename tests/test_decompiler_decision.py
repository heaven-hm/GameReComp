"""Deterministic guardrails for the issue #4 decompiler decision doc.

The decision is evidence-based and fail-closed: the doc must name all six
surveyed tools, give each one a verdict and a reason, must not carry an
unqualified PASS / perfect-percentage claim, and must cite reproducible
artifacts that actually exist in this repository. No decompiler is invoked by
these tests and no external source is consulted.

Run with: python3 -m pytest tests/test_decompiler_decision.py -q
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "DECOMPILER_DECISION.md"

EXPECTED_TOOLS = ("Anvil", "Omill", "Anvill", "Rellic", "McSema", "RetDec")

# A verdict that would assert adoption is forbidden without a measured gate.
INTEGRATION_VERDICTS = {"INTEGRATE", "ADOPT", "ENABLED", "YES", "PASS"}

PERCENT_100 = re.compile(r"100\s*%", re.IGNORECASE)
PASS_TOKEN = re.compile(r"\bPASS\b", re.IGNORECASE)
# A claim is qualified only when the same line negates it or frames it as a
# gate/limit rather than as a result. Mirrors tests/test_acceptance_docs.py.
QUALIFIER = re.compile(
    r"\b(?:no|not|never|none|zero|without|cannot|do|does|isn't|gate|gates|"
    r"requires|required|unverified|unproven|untested|refus\w*|exclud\w*|"
    r"deni\w*|limit\w*|defer\w*|fail\w*)\b",
    re.IGNORECASE,
)
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)

# Every path listed here must be present at HEAD and cited in the doc. They are
# the local audits the per-tool reasons are built from, so citing a vanished or
# invented artifact cannot satisfy the guardrail.
REQUIRED_ARTIFACTS = (
    "tools/revng_probe.py",
    "tests/test_revng_probe.py",
    "docs/feasibility/X64_LIFTER_GATE.md",
    "docs/ISSUE_WORK_LEDGER.md",
    "docs/ISSUE_FINAL_STATUS.md",
    "docs/ISSUE_STATUS.md",
    "docs/closure-audit/ISSUES.json",
)
REPRO_COMMANDS = (
    "python3 -m tools.revng_probe --compact",
    "python3 -m pytest tests/test_revng_probe.py tests/test_decompiler_decision.py -q",
    "git show 93b9765^:docs/REVNG_INTEGRATION_AUDIT.md",
)


class DecompilerDecisionDocTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assertTrue(DOC.is_file(), f"missing decision doc: {DOC}")
        cls.text = DOC.read_text(encoding="utf-8", errors="replace")

    def matrix(self):
        blocks = JSON_BLOCK.findall(self.text)
        self.assertEqual(len(blocks), 1, "expected exactly one ```json decision matrix")
        parsed = json.loads(blocks[0])
        self.assertIsInstance(parsed, dict)
        return parsed

    def test_doc_names_every_surveyed_tool(self):
        for tool in EXPECTED_TOOLS:
            with self.subTest(tool=tool):
                self.assertIn(tool, self.text)

    def test_matrix_has_exact_tool_set_and_no_integrations(self):
        data = self.matrix()
        self.assertEqual(data.get("schema_version"), 1)
        self.assertEqual(data.get("issue"), 4)
        self.assertEqual(data.get("decision"), "DEFER_ALL")
        self.assertEqual(data.get("integrated_tools"), [])
        tools = data.get("tools")
        self.assertIsInstance(tools, list)
        names = [entry.get("tool") for entry in tools]
        self.assertEqual(len(names), len(EXPECTED_TOOLS))
        self.assertEqual(set(names), set(EXPECTED_TOOLS))
        self.assertEqual(len(set(names)), len(names), "duplicate tool entries")

    def test_each_tool_records_a_verdict_and_a_reason(self):
        for entry in self.matrix()["tools"]:
            name = entry["tool"]
            with self.subTest(tool=name):
                verdict = entry.get("verdict")
                reason = entry.get("reason")
                self.assertIsInstance(verdict, str)
                self.assertIsInstance(reason, str)
                self.assertTrue(verdict.strip(), f"{name}: empty verdict")
                self.assertTrue(reason.strip(), f"{name}: empty reason")
                self.assertGreaterEqual(len(reason.strip()), 40, f"{name}: thin reason")
                # The decision is uniformly DEFER; nothing is adopted and no
                # gate is claimed as met.
                self.assertEqual(verdict, "DEFER", f"{name}: unexpected verdict")
                self.assertNotIn(verdict.upper(), INTEGRATION_VERDICTS)
                self.assertEqual(entry.get("gates_met"), [], f"{name}: gate claimed met")

                # Every reason must stand on at least one real, cited artifact.
                cited = entry.get("artifacts")
                self.assertIsInstance(cited, list)
                self.assertTrue(cited, f"{name}: no cited artifacts")
                for artifact in cited:
                    self.assertIsInstance(artifact, str)
                    self.assertFalse(Path(artifact).is_absolute(), artifact)
                    self.assertTrue((ROOT / artifact).is_file(), f"{name}: missing {artifact}")
                    self.assertIn(artifact, reason, f"{name}: {artifact} not tied to reason")

    def test_no_unqualified_pass_or_perfect_percentage_claim(self):
        for lineno, line in enumerate(self.text.splitlines(), start=1):
            for token, pattern in (("100%", PERCENT_100), ("PASS", PASS_TOKEN)):
                if pattern.search(line):
                    self.assertTrue(
                        QUALIFIER.search(line),
                        f"line {lineno}: unqualified {token} claim: {line.strip()}",
                    )
        # A PASS verdict inside the machine-readable matrix would be a claim
        # even if the prose happened to carry a qualifier elsewhere.
        for entry in self.matrix()["tools"]:
            self.assertNotEqual(entry["verdict"].upper(), "PASS", entry["tool"])

    def test_cites_reproducible_existing_artifact_paths(self):
        for artifact in REQUIRED_ARTIFACTS:
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, self.text, "uncited artifact")
                self.assertTrue((ROOT / artifact).is_file(), "artifact path does not resolve")
        for command in REPRO_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(command, self.text)


if __name__ == "__main__":
    unittest.main()
