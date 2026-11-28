"""Automated dual-engine pipeline must be fail-closed and use real inputs."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import auto_verify


class AutoVerifyTests(unittest.TestCase):
    def test_missing_original_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            rec = auto_verify.verify_one(root / "missing.exe", None, b"", [],
                                         5, root, False, False, None)
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertIsNone(rec["sha256"])

    def test_blocked_engine_prevents_pass(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            exe = root / "game.exe"
            exe.write_bytes(b"MZ" + b"\0" * 100)
            with mock.patch.object(auto_verify.hybrid_diff, "hybrid_one",
                                   return_value={"unicorn": {"status": "OK"},
                                                 "frida": {"status": "BLOCKED",
                                                           "reason": "no attach"},
                                                 "native": {"status": "MATCH"},
                                                 "status": "BLOCKED"}):
                rec = auto_verify.verify_one(exe, None, b"real input", [], 5,
                                             root, False, False, None)
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertIn("frida", rec["reason"])
            self.assertEqual(rec["stdin_bytes"], len(b"real input"))

    def test_all_engines_agree_is_pass(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            exe = root / "game.exe"
            exe.write_bytes(b"MZ" + b"\0" * 100)
            with mock.patch.object(auto_verify.hybrid_diff, "hybrid_one",
                                   return_value={"unicorn": {"status": "OK"},
                                                 "frida": {"status": "OK"},
                                                 "native": {"status": "MATCH"},
                                                 "status": "MATCH"}):
                rec = auto_verify.verify_one(exe, None, b"", [], 5, root,
                                             False, False, None)
            self.assertEqual(rec["status"], "PASS")
            self.assertEqual(rec["sha256"], auto_verify.sha256(exe))


if __name__ == "__main__":
    unittest.main()
