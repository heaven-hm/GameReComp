"""Rebasing a 32-bit PE onto a new ImageBase.

A game DLL whose preferred base collides with the game image (parsec47,
torus_trooper and tumiki_fighters all ship BulletML at 0x400000) cannot be
lifted at that base: its functions would overlap the game's. The pipeline
rebases it the way the Windows loader would -- apply the .reloc HIGHLOW fixups
for the delta, rewrite the header -- and lifts the copy.

These tests pin the two things that make that safe: every HIGHLOW fixup moves
by exactly the delta, and a file without a usable .reloc is refused rather
than silently produced at the wrong base.
"""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pe"))

from pe_rebase import RebaseError, pick_base, rebase_pe  # noqa: E402


def _minimal_pe(base=0x400000, reloc=True):
    """A tiny PE32 with one section-at-0x1000 and a .reloc directory.

    Layout: headers (0x200), .text at RVA 0x1000 (one HIGHLOW fixup targets
    RVA 0x1000), .reloc at RVA 0x2000. Enough for rebase_pe to do its work.
    """
    size = 0x600
    b = bytearray(size)
    b[0:2] = b"MZ"
    struct.pack_into("<I", b, 0x3C, 0x80)          # e_lfanew
    b[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", b, 0x84, 0x014C)        # machine i386
    struct.pack_into("<H", b, 0x86, 2)             # 2 sections
    struct.pack_into("<H", b, 0x94, 0xE0)          # SizeOfOptionalHeader
    opt = 0x98
    struct.pack_into("<H", b, opt, 0x10B)          # PE32
    struct.pack_into("<I", b, opt + 28, base)      # ImageBase
    struct.pack_into("<I", b, opt + 32, 0x1000)    # SectionAlignment
    struct.pack_into("<I", b, opt + 36, 0x200)     # FileAlignment
    struct.pack_into("<I", b, opt + 56, 0x3000)    # SizeOfImage
    struct.pack_into("<I", b, opt + 60, 0x200)     # SizeOfHeaders
    struct.pack_into("<H", b, opt + 68, 1)         # NumberOfRvaAndSizes
    # DataDirectory[5] = BaseReloc
    dd = opt + 96 + 5 * 8
    struct.pack_into("<I", b, dd, 0x2000 if reloc else 0)
    struct.pack_into("<I", b, dd + 4, 0x100 if reloc else 0)
    sec = opt + 0xE0
    # .text at RVA 0x1000, raw at 0x200
    b[sec:sec + 8] = b".text\0\0\0"
    struct.pack_into("<IIII", b, sec + 8, 0x1000, 0x1000, 0x200, 0x200)
    # .reloc at RVA 0x2000, raw at 0x400
    sec2 = sec + 40
    b[sec2:sec2 + 8] = b".reloc\0\0"
    struct.pack_into("<IIII", b, sec2 + 8, 0x1000, 0x2000, 0x200, 0x400)
    # A pointer in .text at RVA 0x1000 that must move by the delta.
    struct.pack_into("<I", b, 0x200, base + 0x1234)
    # A reloc block at RVA 0x2000: page 0x1000, size 12, one HIGHLOW @ 0x000.
    struct.pack_into("<II", b, 0x400, 0x1000, 12)
    struct.pack_into("<H", b, 0x408, (3 << 12) | 0)
    struct.pack_into("<H", b, 0x40A, 0)             # ABSOLUTE padding entry
    return bytes(b)


class RebaseTests(unittest.TestCase):
    def test_fixup_moves_by_delta(self):
        src = _minimal_pe(base=0x400000)
        out = rebase_pe(src, 0x10000000)
        delta = 0x10000000 - 0x400000
        moved = struct.unpack_from("<I", out, 0x200)[0]
        self.assertEqual(moved, 0x400000 + 0x1234 + delta)

    def test_image_base_rewritten(self):
        out = rebase_pe(_minimal_pe(base=0x400000), 0x20000000)
        self.assertEqual(struct.unpack_from("<I", out, 0x98 + 28)[0], 0x20000000)

    def test_input_not_mutated(self):
        src = _minimal_pe()
        copy = bytes(src)
        rebase_pe(src, 0x10000000)
        self.assertEqual(src, copy, "rebase_pe must return a copy, not mutate")

    def test_no_reloc_dir_refused(self):
        with self.assertRaises(RebaseError):
            rebase_pe(_minimal_pe(reloc=False), 0x10000000)

    def test_same_base_is_a_noop(self):
        src = _minimal_pe(base=0x400000)
        self.assertEqual(rebase_pe(src, 0x400000), src)

    def test_pick_base_avoids_occupied(self):
        base = pick_base(0x400000, 0x80000, [(0x400000, 0x700000)])
        self.assertGreaterEqual(base, 0x700000)
        self.assertEqual(base % 0x10000, 0, "must be 64K aligned")


if __name__ == "__main__":
    unittest.main()
