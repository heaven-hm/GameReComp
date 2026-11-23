"""Tests for the native-binary ledger (no real build needed)."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.recompile_ledger import scan


class RecompileLedgerTest(unittest.TestCase):
    def test_header_only_is_not_recompiled(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'app'
            (p / 'reports').mkdir(parents=True)
            (p / 'reports' / 'report.json').write_text('{"score":70,"stages":[]}')
            rec = scan(p)
            self.assertEqual(rec['status'], 'NOT_RECOMPILED')
            self.assertIsNone(rec['binary'])

    def test_large_binary_is_detected_with_hash(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'app'
            (p / 'build').mkdir(parents=True)
            (p / 'build' / 'app').write_bytes(b'x' * 200000)
            (p / 'reports').mkdir()
            (p / 'reports' / 'report.json').write_text(
                '{"stages":[{"name":"compile","status":"ok"}]}')
            rec = scan(p)
            self.assertEqual(rec['status'], 'NATIVE_BINARY_PRESENT')
            self.assertEqual(rec['binary_size'], 200000)
            self.assertEqual(len(rec['binary_sha256']), 64)

    def test_partial_build_without_ok_compile_is_not_recompiled(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'app'
            (p / 'build').mkdir(parents=True)
            (p / 'build' / 'app').write_bytes(b'x' * 200000)
            (p / 'reports').mkdir()
            (p / 'reports' / 'report.json').write_text(
                '{"stages":[{"name":"compile","status":"failed"}]}')
            rec = scan(p)
            self.assertEqual(rec['status'], 'NOT_RECOMPILED')

    def test_small_files_are_not_counted_as_binaries(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'app'
            (p / 'build').mkdir(parents=True)
            (p / 'build' / 'Makefile').write_text('all:')
            rec = scan(p)
            self.assertEqual(rec['status'], 'NOT_RECOMPILED')


if __name__ == '__main__':
    unittest.main()
