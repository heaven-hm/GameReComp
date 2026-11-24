"""Proof checker must find real artifacts and never upgrade a status."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.proof_checker import check

ROOT = Path(__file__).resolve().parents[1]


class ProofCheckerTests(unittest.TestCase):
    def test_repository_artifacts_are_present(self):
        doc = check(ROOT)
        self.assertEqual(doc["counts"]["screenshots"], 5)
        self.assertEqual(doc["gaps"], [])
        self.assertIn("not 100%", doc["classification"])

    def test_missing_artifact_is_a_gap(self):
        with tempfile.TemporaryDirectory() as t:
            doc = check(Path(t))
        self.assertTrue(doc["gaps"])
        self.assertEqual(doc["hybrid_status"], "MISSING")

    def test_hybrid_status_is_reported_verbatim(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for rel in (__import__("tools.proof_checker", fromlist=["REQUIRED_TOOLS"]).REQUIRED_TOOLS
                        + __import__("tools.proof_checker", fromlist=["SCREENSHOTS"]).SCREENSHOTS
                        + __import__("tools.proof_checker", fromlist=["EVIDENCE"]).EVIDENCE):
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                if rel == "reports/hybrid-tetris.json":
                    p.write_text(json.dumps({"status": "BLOCKED"}))
                else:
                    p.write_text("{}" if rel.endswith(".json") else "x")
            doc = check(root)
            self.assertEqual(doc["hybrid_status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
