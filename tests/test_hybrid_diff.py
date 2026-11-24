"""Hybrid differential must always attempt both engines and fail closed."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import hybrid_diff  # noqa: E402


class HybridTests(unittest.TestCase):
    def test_blocked_engine_prevents_match(self):
        exe = Path("/tmp/x.exe")
        with mock.patch.object(hybrid_diff, "unicorn_signal",
                               return_value={"status": "OK", "cases": 2}), \
             mock.patch.object(hybrid_diff, "frida_signal",
                               return_value={"status": "BLOCKED", "reason": "no frida"}), \
             mock.patch.object(hybrid_diff.input_replay_diff, "diff_one",
                               return_value={"status": "MATCH"}):
            rec = hybrid_diff.hybrid_one(exe, Path("/tmp/native"), b"", timeout=2)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertIn("frida", rec["reason"])

    def test_all_engines_agree_is_match(self):
        exe = Path("/tmp/x.exe")
        with mock.patch.object(hybrid_diff, "unicorn_signal",
                               return_value={"status": "OK"}), \
             mock.patch.object(hybrid_diff, "frida_signal",
                               return_value={"status": "OK", "calls": []}), \
             mock.patch.object(hybrid_diff.input_replay_diff, "diff_one",
                               return_value={"status": "MATCH"}):
            rec = hybrid_diff.hybrid_one(exe, Path("/tmp/native"), b"", timeout=2,
                                         live_frida=True)
        self.assertEqual(rec["status"], "MATCH")

    def test_frida_wine_missing_reason_is_specific(self):
        with mock.patch.object(hybrid_diff.wine_oracle, "find_wine",
                               return_value=None):
            rec = hybrid_diff.frida_signal(Path("/tmp/x.exe"), timeout=1)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertIn("wine not found", rec["reason"])
        self.assertIn("Highball", rec["reason"])

    def test_native_absent_is_blocked_not_skip(self):
        exe = Path("/tmp/x.exe")
        with mock.patch.object(hybrid_diff, "unicorn_signal",
                               return_value={"status": "OK"}), \
             mock.patch.object(hybrid_diff, "frida_signal",
                               return_value={"status": "OK"}):
            rec = hybrid_diff.hybrid_one(exe, None, b"", timeout=2)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertEqual(rec["native"]["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
