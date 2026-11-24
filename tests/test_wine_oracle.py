"""Oracle extraction must not invent needles or capture Wine's own chatter."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import wine_oracle  # noqa: E402
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

    def test_wine_bottle_path_is_normalised(self):
        doc = oracle_from_run({"exit_code": 0, "sha256": "d" * 64,
                               "wine": "wine", "stdout": "",
                               "stderr": "usage: C:\\recomp-oracle\\keen.exe [-g | -v] <id>\n"})
        self.assertEqual(doc["stderr_contains"],
                         ["usage: keen.exe [-g | -v] <id>"])

    def test_empty_lines_are_never_needles(self):
        doc = oracle_from_run({"exit_code": 1, "sha256": "c" * 64,
                               "wine": "wine", "stdout": "\n  \n\x1b[0m\n",
                               "stderr": ""})
        self.assertEqual(doc["stdout_contains"], [])
        self.assertEqual(doc["exit_code"], 1)

    def test_engine_timeout_is_an_explicit_blocked_record(self):
        # The process body is substituted, not the timeout/BLOCKED policy:
        # a highball run that cannot finish must never be reported as OK.
        timeout = subprocess.TimeoutExpired(
            ["highball", "run"], 3, output="partial out", stderr="partial err")
        proc = mock.Mock(pid=1)
        proc.communicate.side_effect = [timeout, timeout]  # run + drain
        with mock.patch.dict(os.environ, {"RECOMP_HIGHBALL": "/fake/highball"}), \
             mock.patch.object(wine_oracle.subprocess, "Popen", return_value=proc), \
             mock.patch.object(wine_oracle.os, "killpg") as killpg:
            run = wine_oracle.run_original(None, ROOT, Path(__file__), [], 3,
                                           stdin_data=b"")
        self.assertEqual(run["status"], "BLOCKED")
        self.assertEqual(run["blocked_class"], "environment")
        self.assertEqual(run["exit_code"], "TIMEOUT")
        self.assertEqual(run["runner"], "highball")
        self.assertEqual(run["command"][:2], ["/fake/highball", "run"])
        self.assertEqual(run["stdout"], "partial out")
        self.assertIn("within 3s", run["reason"])
        self.assertEqual(killpg.call_count, 1)
        self.assertEqual(proc.communicate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
