import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from tools.corpus.inventory import build_inventory, render_markdown


class CorpusInventoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "game_a").mkdir()
        (self.root / "game_b" / "sub").mkdir(parents=True)
        (self.root / "game_a" / "GameA.EXE").write_bytes(b"MZnot-a-real-pe")
        (self.root / "game_b" / "sub" / "tool.exe").write_bytes(b"MZalso-not-a-pe")
        (self.root / "game_a" / "readme.txt").write_text("not an exe")

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovers_executables_and_hashes_them(self):
        report = build_inventory({"small_games": str(self.root),
                                  "large_games": None})
        paths = [e["relative_path"] for e in report["entries"]]
        self.assertEqual(paths, ["game_a/GameA.EXE", "game_b/sub/tool.exe"])
        for entry in report["entries"]:
            data = (self.root / entry["relative_path"]).read_bytes()
            self.assertEqual(entry["sha256"],
                             hashlib.sha256(data).hexdigest())
        self.assertFalse(report["binaries_included"])

    def test_missing_large_games_is_reported_not_guessed(self):
        report = build_inventory({"small_games": str(self.root),
                                  "large_games": None})
        large = [s for s in report["sources"] if s["source"] == "large_games"][0]
        self.assertEqual(large["status"], "MISSING_INPUT")
        self.assertIn("LARGE_GAMES_PATH", large["detail"])

    def test_nonexistent_root_is_missing_input(self):
        report = build_inventory({"small_games": str(self.root / "nope"),
                                  "large_games": None})
        self.assertEqual(report["sources"][0]["status"], "MISSING_INPUT")
        self.assertEqual(report["entries"], [])

    def test_invalid_pe_recorded_without_aborting(self):
        report = build_inventory({"small_games": str(self.root),
                                  "large_games": None})
        self.assertTrue(all(e["status"] != "ok" for e in report["entries"]))
        self.assertEqual(report["summary"]["invalid"], 2)

    def test_markdown_mentions_missing_input_and_paths(self):
        report = build_inventory({"small_games": str(self.root),
                                  "large_games": None})
        md = render_markdown(report)
        self.assertIn("MISSING_INPUT", md)
        self.assertIn("game_a/GameA.EXE", md)


if __name__ == "__main__":
    unittest.main()
