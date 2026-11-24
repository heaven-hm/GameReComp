"""Tests for the end-to-end report aggregators (no binaries needed)."""
import unittest

from tools.e2e_suite import summarize_games, summarize_recompiles


class E2eSuiteTest(unittest.TestCase):
    def test_game_summary_counts_blank_and_nonblank_frames(self):
        summary = {'results': [{'game': 'a', 'frame_count': 2},
                               {'game': 'b', 'frame_count': 0}]}
        proof = {'frames': [{'blank': False}, {'blank': True}, {'blank': False}]}
        r = summarize_games(summary, proof)
        self.assertEqual(r['games_executed'], 2)
        self.assertEqual(r['games_with_frames'], 1)
        self.assertEqual(r['frames_nonblank'], 2)
        self.assertEqual(r['frames_blank'], 1)

    def test_recompile_summary_distinguishes_header_only(self):
        ledger = {'results': [
            {'status': 'NATIVE_BINARY_PRESENT'},
            {'status': 'NOT_RECOMPILED'}]}
        r = summarize_recompiles(ledger)
        self.assertEqual(r['native_binaries'], 1)
        self.assertEqual(r['header_only'], 1)
        self.assertEqual(r['total'], 2)

    def test_missing_inputs_do_not_invent_passes(self):
        self.assertEqual(summarize_games(None, None)['games_executed'], 0)
        self.assertEqual(summarize_recompiles(None)['total'], 0)


if __name__ == '__main__':
    unittest.main()
