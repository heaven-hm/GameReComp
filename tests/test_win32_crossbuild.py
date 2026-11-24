"""Windows cross-build must produce real MZ output and fail closed otherwise."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.win32_crossbuild import cross_build, find_cc


class CrossBuildTests(unittest.TestCase):
    def test_real_cross_build_produces_pe32(self):
        if not find_cc(32):
            self.skipTest("i686 MinGW not installed")
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            src = root / "hello.c"
            src.write_text("#include <stdio.h>\nint main(void){puts(\"X\");return 0;}\n")
            rec = cross_build(root, root / "hello.exe", 32, [src], 120)
            self.assertEqual(rec["status"], "OK")
            self.assertEqual(rec["pe"], "PE32")
            self.assertEqual((root / "hello.exe").read_bytes()[:2], b"MZ")

    def test_missing_project_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            rec = cross_build(root / "missing", root / "x.exe", 32, [], 5)
            self.assertEqual(rec["status"], "BLOCKED")

    def test_compiler_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            src = root / "bad.c"
            src.write_text("this is not C\n")
            rec = cross_build(root, root / "x.exe", 32, [src], 120)
            self.assertEqual(rec["status"], "FAIL")
            self.assertIn("compiler_output", rec)


if __name__ == "__main__":
    unittest.main()
