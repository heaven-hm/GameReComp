"""Tests for fail-safe generated-artifact cleanup (issue #26)."""
import tempfile
import unittest
from pathlib import Path

from tools.clean_generated import clean_one


class CleanGeneratedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.project = self.root / 'projects' / 'game'
        (self.project / 'src').mkdir(parents=True)
        (self.project / 'build').mkdir()
        (self.project / 'src' / 'recomp_a.c').write_text('int a;')
        (self.project / 'build' / 'game').write_bytes(b'binary')
        (self.project / 'reports').mkdir()
        (self.project / 'reports' / 'report.json').write_text('{"schema":1}')

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_deletes_nothing(self):
        durable = self.root / 'durable'
        rec = clean_one(self.project, durable, False, self.repo)
        self.assertEqual(len(rec['removed']), 2)
        self.assertTrue((self.project / 'src').is_dir())
        self.assertTrue((self.project / 'build').is_dir())
        self.assertFalse(durable.exists())

    def test_apply_preserves_reports_and_deletes_generated(self):
        durable = self.root / 'durable'
        rec = clean_one(self.project, durable, True, self.repo)
        self.assertEqual(rec['reason'], None)
        self.assertEqual(rec['preserved'], ['report.json'])
        self.assertFalse((self.project / 'src').exists())
        self.assertFalse((self.project / 'build').exists())
        self.assertEqual((durable / 'game' / 'report.json').read_text(), '{"schema":1}')
        self.assertTrue((self.project / 'reports' / 'report.json').exists())

    def test_refuses_project_without_reports(self):
        bare = self.root / 'projects' / 'bare'
        (bare / 'src').mkdir(parents=True)
        rec = clean_one(bare, self.root / 'durable', True, self.repo)
        self.assertIsNotNone(rec['reason'])
        self.assertTrue((bare / 'src').exists())

    def test_refuses_paths_inside_repository(self):
        inside = self.repo / 'projects' / 'game'
        (inside / 'reports').mkdir(parents=True)
        (inside / 'reports' / 'report.json').write_text('{}')
        rec = clean_one(inside, self.root / 'durable', True, self.repo)
        self.assertIn('repository', rec['reason'])
        self.assertTrue(inside.exists())


if __name__ == '__main__':
    unittest.main()
