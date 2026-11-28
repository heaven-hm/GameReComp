"""Hybrid differential must attempt every engine and fail closed on faults."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import hybrid_diff, wine_oracle  # noqa: E402


def env_blocked(reason="no frida"):
    return {"status": "BLOCKED", "reason": reason,
            "blocked_class": "environment"}


def fault_blocked(reason="original bytes faulted"):
    return {"status": "BLOCKED", "reason": reason, "blocked_class": "fault"}


class HybridTests(unittest.TestCase):
    def test_blocked_engine_prevents_match_by_default(self):
        exe = Path("/tmp/x.exe")
        with mock.patch.object(hybrid_diff, "unicorn_signal",
                               return_value={"status": "OK", "cases": 2}), \
             mock.patch.object(hybrid_diff, "frida_signal",
                               return_value=env_blocked()), \
             mock.patch.object(hybrid_diff.input_replay_diff, "diff_one",
                               return_value={"status": "MATCH"}):
            rec = hybrid_diff.hybrid_one(exe, Path("/tmp/native"), b"", timeout=2)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertIn("frida", rec["reason"])

    def test_process_level_match_is_opt_in_and_disclosed(self):
        exe = Path("/tmp/x.exe")
        with mock.patch.object(hybrid_diff, "unicorn_signal",
                               return_value={"status": "OK"}), \
             mock.patch.object(hybrid_diff, "frida_signal",
                               return_value=env_blocked("PermissionDeniedError: taskgated")), \
             mock.patch.object(hybrid_diff.input_replay_diff, "diff_one",
                               return_value={"status": "MATCH"}):
            strict = hybrid_diff.hybrid_one(exe, Path("/tmp/native"), b"", timeout=2)
            loose = hybrid_diff.hybrid_one(exe, Path("/tmp/native"), b"", timeout=2,
                                           process_level_match=True)
        self.assertEqual(strict["status"], "BLOCKED")
        self.assertEqual(loose["status"], "MATCH")
        self.assertEqual(loose["matched_engines"], ["unicorn", "native"])
        self.assertEqual(loose["blocked_engines"], ["frida"])
        self.assertIn("PermissionDeniedError: taskgated", loose["reason"])
        self.assertIn("NOT a 100%", loose["reason"])
        self.assertTrue(loose["process_level_only"])

    def test_fault_engine_is_never_allowlisted(self):
        exe = Path("/tmp/x.exe")
        with mock.patch.object(hybrid_diff, "unicorn_signal",
                               return_value=fault_blocked("UC_ERR_READ_UNMAPPED")), \
             mock.patch.object(hybrid_diff, "frida_signal",
                               return_value=env_blocked()), \
             mock.patch.object(hybrid_diff.input_replay_diff, "diff_one",
                               return_value={"status": "MATCH"}):
            rec = hybrid_diff.hybrid_one(exe, Path("/tmp/native"), b"", timeout=2,
                                         process_level_match=True)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertIn("unicorn", rec["reason"])

    def test_divergence_is_never_upgraded(self):
        exe = Path("/tmp/x.exe")
        with mock.patch.object(hybrid_diff, "unicorn_signal",
                               return_value={"status": "OK"}), \
             mock.patch.object(hybrid_diff, "frida_signal",
                               return_value={"status": "OK"}), \
             mock.patch.object(hybrid_diff.input_replay_diff, "diff_one",
                               return_value={"status": "DIVERGED", "reason": "stdout differs"}):
            rec = hybrid_diff.hybrid_one(exe, Path("/tmp/native"), b"", timeout=2,
                                         live_frida=True, process_level_match=True)
        self.assertEqual(rec["status"], "DIVERGED")
        self.assertEqual(rec["diverged_engines"], ["native"])

    def test_vacuous_unicorn_signature_refused(self):
        exe = Path("/tmp/x.exe")
        with mock.patch.object(hybrid_diff, "unicorn_signal",
                               return_value={"status": "OK", "nonvacuous": False}), \
             mock.patch.object(hybrid_diff, "frida_signal", return_value={"status": "OK"}), \
             mock.patch.object(hybrid_diff.input_replay_diff, "diff_one",
                               return_value={"status": "MATCH"}):
            rec = hybrid_diff.hybrid_one(exe, Path("/tmp/native"), b"", timeout=2,
                                         function_va=0x4010A0, live_frida=True,
                                         process_level_match=True)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertIn("non-vacuous", rec["reason"])

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
        # Neither the Highball CLI nor a raw Wine engine exists. Exercise the
        # real bounded probe (not a canned record): its BLOCKED reason must say
        # exactly which engine is missing, and a failed capture must never
        # carry an invented Frida call trace.
        with mock.patch.object(wine_oracle, "find_wine", return_value=None), \
             mock.patch.object(wine_oracle, "find_highball", return_value=None):
            rec = hybrid_diff.frida_signal(Path("/tmp/x.exe"), timeout=5)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertIn("wine not found", rec["reason"])
        self.assertIn("Highball", rec["reason"])
        self.assertEqual(rec["blocked_class"], "environment")
        self.assertNotIn("calls", rec)
        self.assertNotIn("counts", rec)

    def test_native_absent_is_blocked_not_skip(self):
        exe = Path("/tmp/x.exe")
        with mock.patch.object(hybrid_diff, "unicorn_signal",
                               return_value={"status": "OK"}), \
             mock.patch.object(hybrid_diff, "frida_signal", return_value={"status": "OK"}):
            rec = hybrid_diff.hybrid_one(exe, None, b"", timeout=2)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertEqual(rec["native"]["status"], "BLOCKED")

    def test_frida_signal_preserves_exact_bounded_reason(self):
        with mock.patch.object(hybrid_diff.frida_probe, "bounded_probe",
                               return_value={"status": "BLOCKED",
                                             "reason": "NotSupportedError: dyld probe",
                                             "killed_on_timeout": False}) as probe:
            rec = hybrid_diff.frida_signal(Path("/tmp/x.exe"), 5, ["WriteFile"], live=True)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertEqual(rec["blocked_class"], "environment")
        self.assertEqual(rec["reason"], "NotSupportedError: dyld probe")
        probe.assert_called_once()
        self.assertIn("script_sha256", rec)


if __name__ == "__main__":
    unittest.main()
