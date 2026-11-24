"""Oracle extraction must not invent needles or capture Wine's own chatter."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.wine_oracle import oracle_from_run  # noqa: E402


class OracleTests(unittest.TestCase):
    def test_strips_ansi_and_filters_wine_noise(self):
        run = {"exit_code": 0, "sha256": "a" * 64, "wine": "wine",
               "stdout": "\x1b[36mMenu 1\x1b[0m\nMenu 2\n",
               "stderr": "Wine cannot find the FreeType font library.\n"
                         "msync: up and running\nreal game error\n"}
        doc = oracle_from_run(run)
        self.assertEqual(doc["exit_code"], 0)
        self.assertEqual(doc["stdout_contains"], ["Menu 1", "Menu 2"])
        self.assertEqual(doc["stderr_contains"], ["real game error"])
        self.assertNotIn("FreeType", str(doc))

    def test_timeout_has_no_exit_code(self):
        doc = oracle_from_run({"exit_code": "TIMEOUT", "sha256": "b" * 64,
                               "wine": "wine", "stdout": "", "stderr": ""})
        self.assertNotIn("exit_code", doc)

    def test_empty_lines_are_never_needles(self):
        doc = oracle_from_run({"exit_code": 1, "sha256": "c" * 64,
                               "wine": "wine", "stdout": "\n  \n\x1b[0m\n",
                               "stderr": ""})
        self.assertEqual(doc["stdout_contains"], [])
        self.assertEqual(doc["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
