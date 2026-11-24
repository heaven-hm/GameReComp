"""Source-level Linux-host -> Windows-target cross-build must fail closed."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.linux_to_windows import (
    NO_REAL_SOURCE_DIAG, discover_sources, find_cc, linux_to_windows,
)
from tools.wine_oracle import find_wine


class LinuxToWindowsTests(unittest.TestCase):
    def _source_or_skip(self):
        sources = discover_sources()
        if not sources:
            self.skipTest("no pre-existing C source on this host")
        return sources[0]["path"]

    def test_blocked_when_no_real_source_exists(self):
        """No pre-existing source must produce an exact BLOCKED diagnostic."""
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "never.exe"
            with mock.patch("tools.linux_to_windows.discover_sources",
                            return_value=[]):
                rec = linux_to_windows(out=out, run=False)
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(rec["code"], "NO_REAL_SOURCE")
            self.assertEqual(rec["reason"], NO_REAL_SOURCE_DIAG)
            self.assertFalse(out.exists(), "BLOCKED path must not write a PE")

    def test_blocked_for_missing_explicit_source(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "never.exe"
            rec = linux_to_windows(source=Path(t) / "absent.c", out=out,
                                   run=False)
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(rec["code"], "SOURCE_NOT_FOUND")
            self.assertFalse(out.exists())

    def test_blocked_for_source_without_main(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            src = root / "no_main.c"
            src.write_text("int helper(void) { return 1; }\n")
            rec = linux_to_windows(source=src, out=root / "never.exe",
                                   run=False)
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(rec["code"], "SOURCE_NO_MAIN")
            self.assertFalse((root / "never.exe").exists())

    def test_discovery_never_invents_a_source(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.assertEqual(discover_sources([root]), [])
            self.assertEqual(list(root.iterdir()), [])

    def test_no_wine_blocks_after_pe_is_built(self):
        if not find_cc(64):
            self.skipTest("x86-64 MinGW not installed")
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "n wine.exe"
            src = self._source_or_skip()
            with mock.patch("tools.linux_to_windows.find_wine", return_value=None):
                rec = linux_to_windows(source=src, out=out, bits=64, run=True)
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(rec["code"], "NO_WINE_ENGINE")
            self.assertEqual(rec["pe"]["pe_type"], "PE32+")
            self.assertTrue(out.read_bytes().startswith(b"MZ"))

    def test_real_source_builds_pe32_and_pe64(self):
        if not find_cc(32) or not find_cc(64):
            self.skipTest("MinGW-w64 32/64 toolchains not installed")
        src = self._source_or_skip()
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            r32 = linux_to_windows(source=src, out=root / "a32.exe", bits=32,
                                   run=False)
            r64 = linux_to_windows(source=src, out=root / "b64.exe", bits=64,
                                   run=False)
            self.assertEqual(r32["status"], "OK")
            self.assertEqual(r64["status"], "OK")
            self.assertEqual(r32["pe"]["pe_type"], "PE32")
            self.assertEqual(r32["pe"]["machine"], "i386")
            self.assertEqual(r64["pe"]["pe_type"], "PE32+")
            self.assertEqual(r64["pe"]["machine"], "x86-64")
            for path in (root / "a32.exe", root / "b64.exe"):
                self.assertEqual(path.read_bytes()[:2], b"MZ")
            self.assertEqual(r32["source"]["sha256"], r64["source"]["sha256"])

    def test_real_pe_runs_under_highball_wine_with_stdout(self):
        if not find_cc(64) or not find_wine():
            self.skipTest("MinGW-w64 x86-64 or Highball Wine not installed")
        src = self._source_or_skip()
        with tempfile.TemporaryDirectory() as t:
            rec = linux_to_windows(source=src, out=Path(t) / "run64.exe",
                                   bits=64, wine_timeout=120,
                                   expected_stdout="is not equal")
            self.assertEqual(rec["status"], "OK", rec.get("reason", ""))
            self.assertEqual(rec["code"], "PE_RAN_UNDER_WINE")
            self.assertEqual(rec["wine_run"]["exit_code"], 0)
            self.assertIn("is not equal", rec["wine_run"]["stdout"])
            self.assertIn("Highball", rec["wine_run"]["wine"])
            self.assertEqual(rec["source"]["path"], str(Path(src).resolve()))


if __name__ == "__main__":
    unittest.main()
