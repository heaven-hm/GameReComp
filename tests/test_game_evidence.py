"""Validate that every one-at-a-time game run produced real evidence."""
import json
import unittest
from pathlib import Path


SUMMARY = Path(__file__).resolve().parents[1] / 'test-results/game-matrix/execution/summary.json'


class GameEvidenceTest(unittest.TestCase):
    def test_summary_rows_have_proof(self):
        if not SUMMARY.exists():
            self.skipTest('no execution summary generated yet; run tools.game_runner')
        data = json.loads(SUMMARY.read_text())
        self.assertEqual(data['schema'], 1)
        self.assertGreater(data['results'], [])
        for rec in data['results']:
            with self.subTest(game=rec.get('game')):
                self.assertIn('exit_code', rec)
                self.assertIn('command', rec)
                self.assertIn('binary_sha256', rec)
                self.assertEqual(len(rec['binary_sha256']), 64)
                self.assertIn('folder', rec)
                log = Path(rec['evidence']) / 'run.log'
                # A silent game legitimately produces an empty log; the file
                # itself is the evidence that the run happened.
                self.assertTrue(log.is_file(), log)
                # A frame is either absent (explicit failure) or a real file
                # with recorded dimensions; never an assumed PASS.
                for frame in rec['frames']:
                    self.assertIn('width', frame)
                    self.assertIn('height', frame)
                    self.assertGreater(frame['width'], 0)
                    self.assertGreater(frame['height'], 0)
                    self.assertTrue(Path(frame['path']).is_file(), frame['path'])

    def test_new_game_blockers_are_recorded(self):
        if not SUMMARY.exists():
            self.skipTest('no execution summary generated yet')
        data = json.loads(SUMMARY.read_text())
        by_name = {r['game']: r for r in data['results']}
        for name in ('tetris-oucc', 'chocolate-doom'):
            if name not in by_name:
                continue
            rec = by_name[name]
            # Both now bind all imports; Tetris also produced non-blank frames.
            self.assertIn('0 unresolved imports', rec['stdout_tail'])
            if name == 'tetris-oucc':
                self.assertGreater(rec['frame_count'], 0)
            else:
                self.assertEqual(rec['frame_count'], 0)
                self.assertIn('C:\\chocolate-doom.exe booted', rec['stdout_tail'])


if __name__ == '__main__':
    unittest.main()
