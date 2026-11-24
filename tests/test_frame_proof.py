"""Tests for frame-proof decoding and its fail-closed blank flag."""
import struct
import tempfile
import unittest
from pathlib import Path

from tools.frame_proof import bmp24


def make_bmp(path: Path, w: int, h: int, pixels: list[bytes]) -> None:
    stride = ((w * 3 + 3) // 4) * 4
    data = bytearray(54 + stride * h)
    data[:2] = b'BM'
    struct.pack_into('<I', data, 10, 54)
    struct.pack_into('<I', data, 14, 40)
    struct.pack_into('<ii', data, 18, w, h)
    struct.pack_into('<H', data, 26, 1)
    struct.pack_into('<H', data, 28, 24)
    for y, row in enumerate(pixels):
        off = 54 + y * stride
        for x in range(w):
            data[off + x * 3:off + x * 3 + 3] = row[x]
    path.write_bytes(data)


class FrameProofTest(unittest.TestCase):
    def test_blank_frame_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'blank.bmp'
            make_bmp(p, 2, 2, [[b'\x01\x02\x03'] * 2 for _ in range(2)])
            info = bmp24(p)
            self.assertEqual((info['width'], info['height']), (2, 2))
            self.assertEqual(info['distinct_colors'], 1)
            self.assertTrue(info['blank'])

    def test_nonblank_frame_counts_distinct_colors(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'real.bmp'
            make_bmp(p, 2, 2, [[b'\x01\x02\x03', b'\x04\x05\x06'],
                               [b'\x07\x08\x09', b'\x04\x05\x06']])
            info = bmp24(p)
            self.assertEqual(info['distinct_colors'], 3)
            self.assertFalse(info['blank'])
            self.assertEqual(len(info['sha256']), 64)

    def test_unsupported_bmp_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'bad.bmp'
            make_bmp(p, 2, 2, [[b'\0' * 3] * 2 for _ in range(2)])
            raw = bytearray(p.read_bytes())
            raw[:2] = b'XX'
            p.write_bytes(raw)
            self.assertIn('error', bmp24(p))


if __name__ == '__main__':
    unittest.main()
