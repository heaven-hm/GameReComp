"""Unit tests for the CUA GUI click harness (no GUI or driver needed)."""
import unittest

from tools.gui_cua_test import STEPS, scaled


class GuiCuaTest(unittest.TestCase):
    def test_scaling_matches_reference(self):
        self.assertEqual(scaled(500, 252, 1568, 1033), (500, 252))

    def test_scaling_to_smaller_window(self):
        x, y = scaled(1568, 1033, 784, 516)
        self.assertAlmostEqual(x, 784, delta=1)
        self.assertAlmostEqual(y, 516, delta=1)

    def test_manifest_has_safe_actions_only(self):
        names = [s['name'] for s in STEPS]
        self.assertEqual(len(names), len(set(names)))
        for s in STEPS:
            self.assertIn(s['kind'], ('pixel', 'ax'))
            if s['kind'] == 'pixel':
                self.assertGreaterEqual(s['x'], 0)
                self.assertGreaterEqual(s['y'], 0)
            else:
                self.assertTrue(s['label'])
        # Destructive controls must never be part of the automated manifest.
        for banned in ('run_pipeline', 'launch', 'create_issue'):
            self.assertNotIn(banned, names)


if __name__ == '__main__':
    unittest.main()
