"""Real-engine regression evidence for the hybrid harness.

No engine result is mocked here: the Unicorn and native-replay tests execute the
real arith-calc.exe from ~/Downloads/recomp-apps and the real pipeline-built
native binary. They skip when those real artifacts are absent, and the committed
report is validated for provenance either way.

Set RECOMP_P6_NATIVE to the pipeline-built native binary. For the Wine replay
test also set RECOMP_WINE_BOTTLE to an isolated prefix: a shared wineserver left
in a different WINEMSYNC mode by another process makes Wine exit 1 with no
stdout, which is an environment fault, not a guest divergence.
"""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import frida_probe, hybrid_diff, input_replay_diff  # noqa: E402

EXE = Path(os.environ.get(
    "RECOMP_P6_EXE",
    str(Path.home() / "Downloads/recomp-apps/windows/x86/apps/arith-calc.exe")))
NATIVE = Path(os.environ["RECOMP_P6_NATIVE"]) if os.environ.get("RECOMP_P6_NATIVE") else None
FIB_VA = 0x4010A0  # real fib() inside arith-calc.exe's .text, not the PE entry
SEEDS = [1, 2, 3, 4, 5, 6, 7, 8]
EVIDENCE = ROOT / "reports" / "hybrid-arith-calc.json"


class RealEvidenceTests(unittest.TestCase):
    def test_committed_report_has_real_dual_engine_evidence(self):
        doc = json.loads(EVIDENCE.read_text())
        self.assertEqual(doc["status"], "MATCH")
        self.assertEqual(doc["matched_engines"], ["unicorn", "native"])
        self.assertEqual(doc["blocked_engines"], ["frida"])
        self.assertIn("NOT a 100%", doc["reason"])
        # Frida was really attempted; a precise exception, never a silent skip.
        self.assertTrue(doc["frida"]["reason"])
        self.assertIn("Error:", doc["frida"]["reason"])
        self.assertEqual(doc["frida"]["blocked_class"], "environment")
        self.assertIn("timeout_s", doc["frida"])
        # Unicorn ran original bytes with a varying signature.
        self.assertEqual(doc["unicorn"]["function"], hex(FIB_VA))
        self.assertTrue(doc["unicorn"]["nonvacuous"])
        self.assertGreaterEqual(doc["unicorn"]["distinct_eax"], 2)
        # Both stdout capture hashes are real, non-empty and identical.
        orig = doc["native"]["original"]["stdout_hash"]
        nat = doc["native"]["native"]["stdout_hash"]
        self.assertEqual(orig, nat)
        self.assertNotEqual(orig, hashlib.sha256(b"").hexdigest())
        self.assertGreaterEqual(len(doc["native"]["original_lines"]), 5)
        self.assertEqual(len(doc["artifacts"]["original"]["sha256"]), 64)
        if EXE.is_file():  # verify provenance when the real input is available
            self.assertEqual(doc["artifacts"]["original"]["sha256"],
                             hashlib.sha256(EXE.read_bytes()).hexdigest())

    @unittest.skipUnless(EXE.is_file(), f"real executable not present: {EXE}")
    def test_unicorn_real_fib_function_and_entry_root_cause(self):
        good = hybrid_diff.unicorn_signal(EXE, va=FIB_VA, seeds=SEEDS)
        self.assertEqual(good["status"], "OK", good.get("reason", ""))
        self.assertEqual(good["section"], ".text")
        self.assertEqual(good["named"], [])
        self.assertTrue(good["nonvacuous"])
        self.assertGreaterEqual(good["distinct_eax"], 5)
        self.assertGreater(len(set(good["signatures"])), 1)
        # The PE entry itself is the thing that faults: it calls unresolved IAT
        # imports, which is why targeting a real internal function is required.
        entry = hybrid_diff.unicorn_signal(EXE)
        self.assertEqual(entry["status"], "BLOCKED")
        self.assertEqual(entry["blocked_class"], "fault")
        self.assertIn("unmapped", entry["reason"].lower())

    @unittest.skipUnless(EXE.is_file() and NATIVE and NATIVE.is_file(),
                         "real native binary not provided (set RECOMP_P6_NATIVE)")
    def test_native_replay_matches_wine_original(self):
        rec = input_replay_diff.diff_one(EXE, NATIVE, b"", [], EXE.parent, 20)
        self.assertEqual(rec["status"], "MATCH", rec)
        self.assertEqual(rec["original"]["exit_code"], 0)
        self.assertEqual(rec["native"]["exit_code"], 0)
        self.assertEqual(rec["original"]["stdout_hash"], rec["native"]["stdout_hash"])

    def test_frida_probe_is_hard_bounded_on_timeout(self):
        # Only the child body is substituted: the timeout/kill logic under test
        # is the real production code. The result must stay BLOCKED, never OK.
        sleepy = "import time; time.sleep(30)"
        with mock.patch.object(frida_probe, "_FRIDA_WORKER", sleepy):
            rec = frida_probe.bounded_probe("rpc.exports = { calls: [] };", [],
                                            EXE, timeout=1, root=ROOT)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(rec["killed_on_timeout"])
        self.assertIn("worker produced no result", rec["reason"])


if __name__ == "__main__":
    unittest.main()
