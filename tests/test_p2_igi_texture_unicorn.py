"""Regression: the real guest's DDPF/bitcount check over retail igi.exe bytes.

Skipped (not failed) when the retail executable or Unicorn is unavailable.
The guest code is never reimplemented here: every instruction is executed
from the PE image by tools/p2_igi_tex_unicorn.py.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from tools.p2_igi_tex_unicorn import IGI, image_bytes, fresh_uc, game_formats, run_callback, run_check
except Exception as exc:  # pragma: no cover - optional deps
    IGI = None
    IMPORT_ERROR = exc


@unittest.skipUnless(IGI is not None and IGI.exists(),
                     "retail igi.exe / unicorn / pefile not available")
class IgiTextureCheckUnicornTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.img, cls.base = image_bytes()
        cls.formats = game_formats()

    def test_parse_game_enumeration(self):
        self.assertGreaterEqual(len(self.formats), 6)
        self.assertEqual(self.formats[0][0], 32)       # dwSize
        self.assertEqual(self.formats[0][1], 0x40)     # DDPF_RGB
        self.assertEqual(self.formats[0][3], 16)       # dwRGBBitCount

    def test_guest_accepts_rgb565_and_rejects_32bpp(self):
        accepted, before, after, _ = run_callback(fresh_uc(self.img), self.formats[0])
        self.assertTrue(accepted)
        self.assertEqual((before, after), (0, 1))
        b32 = next(f for f in self.formats if f[3] == 32)
        accepted, before, after, _ = run_callback(fresh_uc(self.img), b32)
        self.assertFalse(accepted)
        self.assertEqual((before, after), (0, 0))

    def test_guest_check_passes_rgb565_and_fails_empty_format(self):
        self.assertEqual(run_check(fresh_uc(self.img), self.formats[0]), 'PASS')
        self.assertEqual(run_check(fresh_uc(self.img), [0] * 8), 'FAIL')


if __name__ == '__main__':
    unittest.main()
