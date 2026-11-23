"""Tests for the reusable game-matrix drivers (no game assets required)."""
import json
import os
import unittest
from pathlib import Path

from tools.game_matrix import pe32_x86, discover, sha256
from tools.linux_matrix import project_name, docker_ready


CORPUS = os.environ.get('RECOMP_GAME_CORPUS')


class GameMatrixToolTest(unittest.TestCase):
    def test_pe32_detection(self):
        igi = Path('/Users/haseeb-mir/Downloads/IGI/igi.exe')
        if igi.exists():
            self.assertTrue(pe32_x86(igi))
        x64 = Path('/Users/haseeb-mir/Downloads/recomp-apps/games/opentyrian/opentyrian.exe')
        if x64.exists():
            self.assertFalse(pe32_x86(x64))
        self.assertFalse(pe32_x86(Path(__file__)))          # not a PE at all

    def test_discover_filters_x64(self):
        if not CORPUS:
            self.skipTest('RECOMP_GAME_CORPUS not set')
        found = discover(Path(CORPUS))
        self.assertGreater(len(found), 0)
        for p in found:
            self.assertTrue(pe32_x86(p))
        self.assertFalse(any('opentyrian' in str(p) for p in found))
        self.assertFalse(any('cannonball' in str(p) for p in found))

    def test_sha256_stable(self):
        p = Path(__file__)
        self.assertEqual(sha256(p), sha256(p))
        self.assertEqual(len(sha256(p)), 64)

    def test_project_name(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            (project / 'CMakeLists.txt').write_text(
                'cmake_minimum_required(VERSION 3.16)\nproject(dxIka C)\n')
            self.assertEqual(project_name(project), 'dxIka')

    def test_docker_capability_is_reported_not_assumed(self):
        # The value depends on the host; the contract is that it is a bool and
        # that the matrix driver refuses to fake Linux results when it is false.
        self.assertIsInstance(docker_ready(), bool)

    def test_summary_schema_when_present(self):
        summary = Path(__file__).parent.parent / 'test-results/game-matrix/macos/summary.json'
        if not summary.exists():
            self.skipTest('no matrix summary generated yet')
        data = json.loads(summary.read_text())
        self.assertEqual(data['schema'], 1)
        self.assertGreater(data['inputs'], 0)
        for rec in data['results']:
            self.assertIn('sha256', rec)
            self.assertEqual(len(rec['sha256']), 64)
            self.assertIn('stages', rec)


if __name__ == '__main__':
    unittest.main()
