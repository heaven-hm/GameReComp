"""Forwarded-export auditing for PE32 images.

A game that resolves imports through LoadLibrary + GetProcAddress can land on
an export that is really a forwarder string ("KERNEL32.Sleep", "NTDLL.#8").
The runtime follows those strings; this pins the reader that lets an audit say
which images need it. The fixture is a hand-built PE32 with one .edata section
holding two ordinary exports and two forwarders.
"""

import contextlib
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "pe"))

import forwarders  # noqa: E402
from forwarders import parse_exports  # noqa: E402


def make_pe(plus=False, with_exports=True):
    """One .edata section at RVA 0x1000 / file offset 0x200."""
    b = bytearray(0x800)
    b[0:2] = b'MZ'
    struct.pack_into('<I', b, 0x3C, 0x80)
    b[0x80:0x84] = b'PE\x00\x00'
    struct.pack_into('<H', b, 0x84, 0x014C)
    struct.pack_into('<H', b, 0x86, 1)
    opt_size = 0xF0 if plus else 0xE0
    struct.pack_into('<H', b, 0x94, opt_size)
    opt = 0x98
    struct.pack_into('<H', b, opt, 0x20B if plus else 0x10B)
    struct.pack_into('<I', b, opt + (108 if plus else 92), 16)  # NumberOfRvaAndSizes
    dd = opt + (112 if plus else 96)
    if with_exports:
        struct.pack_into('<II', b, dd, 0x1000, 0x200)          # export dir RVA/size

    sec = opt + opt_size
    b[sec:sec + 8] = b'.edata\0\0'
    struct.pack_into('<IIII', b, sec + 8, 0x400, 0x1000, 0x400, 0x200)

    # IMAGE_EXPORT_DIRECTORY at file 0x200.
    struct.pack_into('<I', b, 0x200 + 12, 0x1180)             # Name RVA
    struct.pack_into('<II', b, 0x200 + 16, 1, 3)              # Base, NumberOfFunctions
    struct.pack_into('<I', b, 0x200 + 24, 3)                  # NumberOfNames
    struct.pack_into('<III', b, 0x200 + 28, 0x1080, 0x10A0, 0x10C0)

    # AddressOfFunctions: a real RVA, then two RVAs inside the export directory.
    struct.pack_into('<III', b, 0x280, 0x2000, 0x1100, 0x1120)
    struct.pack_into('<III', b, 0x2A0, 0x1140, 0x1150, 0x1160)
    struct.pack_into('<HHH', b, 0x2C0, 0, 1, 2)

    def put(rva, text):
        off = 0x200 + (rva - 0x1000)
        b[off:off + len(text)] = text

    put(0x1100, b'KERNEL32.Sleep\x00')
    put(0x1120, b'NTDLL.#8\x00')
    put(0x1140, b'RealFunc\x00')
    put(0x1150, b'ForwardByName\x00')
    put(0x1160, b'ForwardByOrdinal\x00')
    put(0x1180, b'fixture.dll\x00')
    return bytes(b)


class ForwarderAuditTest(unittest.TestCase):
    def test_name_and_ordinal_forwarders(self):
        exports = parse_exports(make_pe())
        self.assertEqual([e['ordinal'] for e in exports], [1, 2, 3])
        self.assertEqual(exports[0]['name'], 'RealFunc')
        self.assertEqual(exports[0]['rva'], 0x2000)
        self.assertFalse(exports[0]['forwarded'])

        by_name = exports[1]
        self.assertEqual(by_name['name'], 'ForwardByName')
        self.assertTrue(by_name['forwarded'])
        self.assertEqual(by_name['target_dll'], 'KERNEL32')
        self.assertEqual(by_name['target_name'], 'Sleep')
        self.assertIsNone(by_name['target_ordinal'])

        by_ord = exports[2]
        self.assertTrue(by_ord['forwarded'])
        self.assertEqual(by_ord['target_dll'], 'NTDLL')
        self.assertEqual(by_ord['target_ordinal'], 8)
        self.assertIsNone(by_ord['target_name'])

    def test_pe32_plus_data_directory_offset(self):
        exports = parse_exports(make_pe(plus=True))
        self.assertEqual(len(exports), 3)
        self.assertEqual(exports[1]['target_name'], 'Sleep')

    def test_no_export_directory(self):
        self.assertEqual(parse_exports(make_pe(with_exports=False)), [])

    def test_truncated_or_non_pe_input(self):
        self.assertEqual(parse_exports(b''), [])
        self.assertEqual(parse_exports(b'MZ'), [])
        self.assertEqual(parse_exports(make_pe()[:0x250]), [])

    def test_cli_reports_forwarders(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'fixture.dll'
            path.write_bytes(make_pe())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = forwarders.main(['forwarders.py', str(path)])
            self.assertEqual(rc, 0)
            text = out.getvalue()
            self.assertIn('2 forwarded', text)
            self.assertIn('-> KERNEL32.Sleep', text)
            self.assertIn('-> NTDLL.#8', text)


if __name__ == '__main__':
    unittest.main()
