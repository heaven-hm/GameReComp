"""Tests for the GUI selftest driver (no GUI window required)."""
import json
import unittest
from pathlib import Path


SUMMARY = Path(__file__).resolve().parents[1] / 'test-results/gui-matrix/gui-summary.json'


class GuiMatrixTest(unittest.TestCase):
    def test_summary_records_exact_commands_and_results(self):
        if not SUMMARY.exists():
            self.skipTest('no GUI matrix summary generated yet')
        data = json.loads(SUMMARY.read_text())
        self.assertEqual(data['schema'], 1)
        self.assertGreater(data['results'], [])
        for rec in data['results']:
            with self.subTest(input=rec['input']):
                self.assertIn('kind', rec)
                self.assertIn('command', rec)
                self.assertIn('exit_code', rec)
                self.assertIn('stdout', rec)
                if rec['kind'] == 'pe':
                    self.assertEqual(len(rec['sha256']), 64)
                    self.assertIn('--selftest-pe', rec['command'])
                else:
                    self.assertIn('--selftest', rec['command'])


if __name__ == '__main__':
    unittest.main()
