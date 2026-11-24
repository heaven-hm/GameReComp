"""The sweep must hash a native run's combined output and survive bad binaries."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.oracle_sweep import combined_hash  # noqa: E402


class SweepTests(unittest.TestCase):
    def test_combined_hash_captures_stdout_and_exit(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            script = d / "native"
            script.write_text("#!/bin/sh\nprintf 'hello\\n'\nexit 7\n")
            script.chmod(0o755)
            rec = combined_hash(script, d, 5)
            self.assertEqual(rec["exit_code"], 7)
            self.assertIsNotNone(rec["output_sha256"])
            self.assertEqual(rec["bytes"], 6)

    def test_missing_binary_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as t:
            rec = combined_hash(Path(t) / "missing", Path(t), 5)
            self.assertEqual(rec["exit_code"], "MISSING")
            self.assertIsNone(rec["output_sha256"])


if __name__ == "__main__":
    unittest.main()
