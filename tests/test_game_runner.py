"""Tests for the one-game-at-a-time evidence runner."""
import struct
import tempfile
import unittest
from pathlib import Path

from tools.game_runner import image_info


class GameRunnerTest(unittest.TestCase):
    def test_bmp_dimensions(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'frame-0000.bmp'
            data = bytearray(64)
            data[:2] = b'BM'
            struct.pack_into('<ii', data, 18, 640, 480)
            p.write_bytes(data)
            info = image_info(p)
            self.assertEqual(info['format'], 'bmp')
            self.assertEqual((info['width'], info['height']), (640, 480))

    def test_png_dimensions(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'frame-0000.png'
            data = bytearray(32)
            data[:8] = b'\x89PNG\r\n\x1a\n'
            struct.pack_into('>II', data, 16, 351, 238)
            p.write_bytes(data)
            info = image_info(p)
            self.assertEqual(info['format'], 'png')
            self.assertEqual((info['width'], info['height']), (351, 238))

    def test_invalid_frame_is_reported_not_assumed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'frame.bmp'
            p.write_bytes(b'not an image')
            self.assertIn('error', image_info(p))


if __name__ == '__main__':
    unittest.main()
