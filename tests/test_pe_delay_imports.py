"""Delay-loaded imports must be visible to the PE analyser and its IAT map.

A delay-load descriptor (data directory 13) names a real IAT slot even though
Windows leaves it pointing at __delayLoadHelper until the first call. Static
recompilation has no lazy-load helper: it binds every slot at boot, so an
omitted delay import becomes an unresolved ICALL -- the guest calls a shim (or
a companion DLL export) through a slot that still holds a never-lifted thunk,
and the call is silently dropped.

The fixture below is a hand-built PE32 with one ordinary import, two RVA-based
delay imports (one by name, one by ordinal) and one old-style VA-based delay
import. It is deterministic: no game binary, no network, no pefile required for
the struct parser path.
"""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pe"))

from pe_analyze import (PEInfo, _analyze_with_pefile,  # noqa: E402
                        _analyze_with_struct, analyze_pe, build_iat_map)

BASE = 0x400000
SEC_RVA = 0x1000
SEC_RAW = 0x200
FILE_SIZE = 0x800

# RVA layout inside the single section.
IMP_DESC, IMP_ILT, IMP_IAT = 0x1000, 0x1030, 0x1040
NAME_GETTICKCOUNT, NAME_KERNEL32 = 0x1050, 0x1060
DELAY_DESC, DELAY_ILT0, DELAY_IAT0 = 0x1080, 0x10E0, 0x10F0
DELAY_ILT1, DELAY_IAT1 = 0x1100, 0x1110
NAME_GAMDLL, NAME_OLDSTYLE = 0x1120, 0x1130
ORDINAL = 0x42


def _put32(b, rva, value):
    struct.pack_into('<I', b, SEC_RAW + rva - SEC_RVA, value)


def _putstr(b, rva, text):
    off = SEC_RAW + rva - SEC_RVA
    b[off:off + len(text) + 1] = text.encode('ascii') + b'\x00'


def _imp_descriptor(b, rva, name, iat, ilt):
    """IMAGE_IMPORT_DESCRIPTOR (5 dwords, 'Name' at +12)."""
    struct.pack_into('<5I', b, SEC_RAW + rva - SEC_RVA,
                     ilt, 0, 0, name, iat)


def _delay_descriptor(b, rva, attrs, name, iat, ilt, va_based=False):
    def conv(v):
        return BASE + v if va_based else v
    off = SEC_RAW + rva - SEC_RVA
    struct.pack_into('<8I', b, off, attrs, conv(name), 0, conv(iat), conv(ilt), 0, 0, 0)


def build_pe():
    b = bytearray(FILE_SIZE)
    b[0:2] = b'MZ'
    struct.pack_into('<I', b, 0x3C, 0x80)
    b[0x80:0x84] = b'PE\x00\x00'
    struct.pack_into('<HHIIIHH', b, 0x84, 0x14C, 1, 0, 0, 0, 0xE0, 0x0102)
    opt = 0x98
    struct.pack_into('<H', b, opt, 0x10B)             # PE32
    struct.pack_into('<I', b, opt + 16, SEC_RVA)      # AddressOfEntryPoint
    struct.pack_into('<I', b, opt + 28, BASE)         # ImageBase
    struct.pack_into('<I', b, opt + 32, 0x1000)       # SectionAlignment
    struct.pack_into('<I', b, opt + 36, 0x200)        # FileAlignment
    struct.pack_into('<I', b, opt + 56, 0x2000)       # SizeOfImage
    struct.pack_into('<I', b, opt + 60, 0x200)        # SizeOfHeaders
    struct.pack_into('<I', b, opt + 92, 16)           # NumberOfRvaAndSizes
    dd = opt + 96
    struct.pack_into('<II', b, dd + 8, IMP_DESC, 40)          # directory 1
    struct.pack_into('<II', b, dd + 13 * 8, DELAY_DESC, 96)   # directory 13
    sec = opt + 0xE0
    b[sec:sec + 8] = b'.idata\0'
    struct.pack_into('<IIII', b, sec + 8, 0x600, SEC_RVA, 0x600, SEC_RAW)
    struct.pack_into('<I', b, sec + 36, 0xC0000040)            # initialized, r/w

    # Ordinary import: KERNEL32!GetTickCount.
    _imp_descriptor(b, IMP_DESC, NAME_KERNEL32, IMP_IAT, IMP_ILT)
    _put32(b, IMP_ILT, NAME_GETTICKCOUNT)
    _put32(b, IMP_IAT, NAME_GETTICKCOUNT)

    # Delay import 0 (RVA-based): GAMDLL!GetTickCount and GAMDLL ordinal 42.
    _delay_descriptor(b, DELAY_DESC, 1, NAME_GAMDLL, DELAY_IAT0, DELAY_ILT0)
    _put32(b, DELAY_ILT0, NAME_GETTICKCOUNT)
    _put32(b, DELAY_ILT0 + 4, 0x80000000 | ORDINAL)

    # Delay import 1 (old-style: fields are VAs, not RVAs).
    _delay_descriptor(b, DELAY_DESC + 32, 0, NAME_OLDSTYLE, DELAY_IAT1,
                      DELAY_ILT1, va_based=True)
    _put32(b, DELAY_ILT1, BASE + NAME_GETTICKCOUNT)

    # hint + name and DLL names.
    struct.pack_into('<H', b, SEC_RAW + NAME_GETTICKCOUNT - SEC_RVA, 0)
    _putstr(b, NAME_GETTICKCOUNT + 2, 'GetTickCount')
    _putstr(b, NAME_KERNEL32, 'KERNEL32.dll')
    _putstr(b, NAME_GAMDLL, 'GAMDLL.dll')
    _putstr(b, NAME_OLDSTYLE, 'OLDSTYLE.dll')
    return bytes(b)


class DelayImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / 'delay.exe'
        cls.path.write_bytes(build_pe())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _assert_contents(self, info):
        delay = [i for i in info.imports if i.is_delayed]
        self.assertEqual(len(delay), 3)
        self.assertEqual([(i.dll, i.name, i.ordinal, i.iat_rva)
                          for i in delay], [
            ('GAMDLL.dll', 'GetTickCount', None, DELAY_IAT0),
            ('GAMDLL.dll', None, ORDINAL, DELAY_IAT0 + 4),
            ('OLDSTYLE.dll', 'GetTickCount', None, DELAY_IAT1),
        ])
        self.assertEqual(info.import_dlls,
                         ['KERNEL32.dll', 'GAMDLL.dll', 'OLDSTYLE.dll'])
        iat = build_iat_map(info)
        self.assertEqual(iat[BASE + IMP_IAT], ('KERNEL32.dll', 'GetTickCount'))
        self.assertEqual(iat[BASE + DELAY_IAT0], ('GAMDLL.dll', 'GetTickCount'))
        self.assertEqual(iat[BASE + DELAY_IAT0 + 4],
                         ('GAMDLL.dll', 'ordinal_66'))
        self.assertEqual(iat[BASE + DELAY_IAT1],
                         ('OLDSTYLE.dll', 'GetTickCount'))
        self.assertEqual(len(iat), 4)

    def test_struct_parser(self):
        """The pefile-free fallback sees the delay directory."""
        info = PEInfo(filename='delay.exe', filepath=str(self.path),
                      filesize=FILE_SIZE)
        _analyze_with_struct(str(self.path), info)
        self._assert_contents(info)

    def test_analyze_pe(self):
        """The public entry point folds delay imports into the import list."""
        info = analyze_pe(str(self.path))
        self.assertEqual(sum(1 for i in info.imports if i.is_delayed), 3)
        self._assert_contents(info)

    def test_pefile_parser(self):
        """pefile's richer parser agrees with the struct fallback."""
        try:
            import pefile  # noqa: F401
        except ImportError:
            self.skipTest('pefile not installed')
        info = PEInfo(filename='delay.exe', filepath=str(self.path),
                      filesize=FILE_SIZE)
        _analyze_with_pefile(str(self.path), info, pefile)
        self._assert_contents(info)

    def test_no_delay_directory(self):
        """A PE without directory 13 keeps working and has no delay entries."""
        b = bytearray(build_pe())
        struct.pack_into('<I', b, 0x98 + 96 + 13 * 8, 0)
        self.path.with_name('plain.exe').write_bytes(bytes(b))
        info = analyze_pe(str(self.path.with_name('plain.exe')))
        self.assertEqual(info.import_dlls, ['KERNEL32.dll'])
        self.assertEqual([i for i in info.imports if i.is_delayed], [])
        self.assertEqual(len(build_iat_map(info)), 1)


if __name__ == '__main__':
    unittest.main()
