"""Regression test: difftest16's memory hash must be 32-bit on every host.

The generated C harness used `unsigned long crc`, which is 64-bit on LP64
macOS/Linux while the Python reference masks every FNV step to 32 bits. The
low 32 bits agreed, but the printed values did not, so *every* instruction in
a real NE segment was reported as "memory differs" (1382/1406 on SHIPEDIT.EXE
segment 1). This test would have caught that before the fix.

Run: python3 tools/lift/test_difftest16_crc.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# add ax,1 / add al,bl / xor ax,ax / nop -- all testable, none touch memory,
# so any "memory differs" finding is a harness bug, not a lifted-code bug.
BYTES = bytes.fromhex("05 01 00 00 d8 31 c0 90")


class Difftest16CRC(unittest.TestCase):
    def test_source_uses_32bit_hash(self):
        src = (HERE / "difftest16.py").read_text(encoding="utf-8")
        self.assertIn("uint32_t crc = 0;", src)
        self.assertIn("crc = crc * 16777619u ^ w;", src)
        self.assertNotIn("unsigned long crc = 0;", src)

    def test_end_to_end_no_false_memory_diff(self):
        try:
            import unicorn  # noqa: F401
        except ImportError:
            self.skipTest("unicorn not installed")
        if not any(shutil.which(c) for c in ("gcc", "clang", "cc")):
            self.skipTest("no C compiler installed")
        with tempfile.TemporaryDirectory(prefix="dt16_crc_") as d:
            raw = os.path.join(d, "tiny.bin")
            with open(raw, "wb") as f:
                f.write(BYTES)
            r = subprocess.run(
                [sys.executable, str(HERE / "difftest16.py"),
                 "--raw", raw, "--vectors", "1"],
                capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("all agree", r.stdout)
        self.assertNotIn("memory differs", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
