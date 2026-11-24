"""The 1,000-binary sweep evidence must be complete and self-consistent."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StaticSweepEvidenceTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "reports" / "pe32-1000-static.json"
        if not path.is_file():
            self.skipTest("sweep evidence not present")
        self.doc = json.loads(path.read_text())

    def test_exactly_1000_rows_all_ok(self):
        self.assertEqual(self.doc["count"], 1000)
        self.assertEqual(len(self.doc["rows"]), 1000)
        self.assertEqual(self.doc["summary"].get("STATIC_OK"), 1000)

    def test_every_row_has_hash_and_pe32(self):
        for row in self.doc["rows"]:
            self.assertEqual(row["status"], "STATIC_OK")
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(row["pe_type"], "PE32")
            self.assertGreater(row["bytes"], 1024)

    def test_paths_are_absolute_and_distinct(self):
        paths = [r["path"] for r in self.doc["rows"]]
        self.assertEqual(len(paths), len(set(paths)))
        for p in paths:
            self.assertTrue(Path(p).is_absolute())


if __name__ == "__main__":
    unittest.main()
