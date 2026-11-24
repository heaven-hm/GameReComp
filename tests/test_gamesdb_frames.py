"""The matrix's frame-proof column must come only from real proof JSON."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import gamesdb  # noqa: E402


def _proof(root, name, frames):
    d = root / name
    d.mkdir(parents=True)
    (d / "frame-proof.json").write_text(json.dumps(
        {"filename": {"cavestory": "Doukutsu.exe", "ikachan": "dxIka.exe"}.get(name, ""),
         "frames": frames}))


class FrameProofColumnTests(unittest.TestCase):
    def test_counts_nonblank_frames_and_matches_filename(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _proof(root, "cavestory", [
                {"game": "cavestory-doukutsu", "path": "frame-0000.bmp", "blank": True},
                {"game": "cavestory-doukutsu", "path": "frame-0001.bmp", "blank": False},
                {"game": "cavestory-doukutsu", "path": "frame-0002.bmp", "blank": False},
            ])
            proofs = gamesdb.load_frame_proofs(root)
            self.assertEqual(len(proofs), 1)
            self.assertEqual(gamesdb.frame_proof_for(proofs, "Doukutsu.exe"),
                             "2/3 non-blank frames")

    def test_blank_only_is_reported_not_promoted(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _proof(root, "ikachan", [
                {"game": "ikachan", "path": "frame-0001.bmp", "blank": True},
            ])
            proofs = gamesdb.load_frame_proofs(root)
            self.assertEqual(gamesdb.frame_proof_for(proofs, "dxIka.exe"),
                             "0/1 non-blank (blank frames)")

    def test_no_proof_and_bad_json_are_dashes(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "broken").mkdir()
            (root / "broken" / "frame-proof.json").write_text("{not json")
            proofs = gamesdb.load_frame_proofs(root)
            self.assertEqual(proofs, [])
            self.assertEqual(gamesdb.frame_proof_for(proofs, "anything.exe"), "-")

    def test_matrix_table_has_column_for_every_row(self):
        rows = [{"game": "X", "os": "Windows", "arch": "i386 PE32",
                 "details": "d", "frames": "1/3 non-blank frames", "status": "PARTIAL"}]
        table = gamesdb._matrix_table(rows)
        self.assertIn("| Frame proof |", table)
        self.assertIn("1/3 non-blank frames", table)


if __name__ == "__main__":
    unittest.main()
