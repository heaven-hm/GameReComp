"""The sequential corpus sweep must attempt every row and journal each result."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import full_corpus_sweep  # noqa: E402


class FullSweepTests(unittest.TestCase):
    def test_every_row_is_attempted_and_journaled(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            inv = root / "inv.json"
            inv.write_text(json.dumps({"rows": [
                {"path": str(root / "a.exe")}, {"path": str(root / "b.exe")}]}))
            seen = []

            def fake(exe, out, timeout, build_timeout):
                seen.append(exe.name)
                return {"name": exe.stem, "status": "PASS" if exe.stem == "a" else "BLOCKED",
                        "reason": ""}

            with mock.patch.object(full_corpus_sweep.oracle_sweep, "sweep_one",
                                   side_effect=fake):
                rc = full_corpus_sweep.main(["--inventory", str(inv),
                                             "--out", str(root / "out")])
            self.assertEqual(rc, 0)
            self.assertEqual(seen, ["a.exe", "b.exe"])
            lines = (root / "out" / "results.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), 2)
            summary = json.loads((root / "out" / "summary.json").read_text())
            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["counts"]["PASS"], 1)
            self.assertEqual(summary["counts"]["BLOCKED"], 1)


if __name__ == "__main__":
    unittest.main()
