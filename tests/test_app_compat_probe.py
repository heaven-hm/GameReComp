"""Tests for the fail-closed application compatibility classifier."""
import struct
import tempfile
import unittest
from pathlib import Path

from tools.app_compat_probe import classify


def pe(path: Path, machine: int, magic: int, clr: bool = False) -> None:
    data = bytearray(0x300)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3C, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HH', data, 0x84, machine, 1)
    struct.pack_into('<H', data, 0x84 + 16, 224)      # size of optional header
    opt = 0x84 + 20
    struct.pack_into('<H', data, opt, magic)
    struct.pack_into('<H', data, opt + 68, 3)         # CUI subsystem
    if clr:
        dd = opt + (96 if magic == 0x10B else 112)
        struct.pack_into('<II', data, dd + 14 * 8, 0x2000, 72)
    path.write_bytes(data)


class AppCompatProbeTest(unittest.TestCase):
    def test_pe32_native_is_candidate_not_a_pass(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'native.exe'
            pe(p, 0x14C, 0x10B)
            rec = classify(p)
            self.assertEqual(rec['verdict'], 'SUPPORTED_CANDIDATE')
            self.assertIn('structurally eligible only', rec['reason'])
            self.assertEqual(len(rec['sha256']), 64)

    def test_x64_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x64.exe'
            pe(p, 0x8664, 0x20B)
            rec = classify(p)
            self.assertEqual(rec['verdict'], 'UNSUPPORTED')
            self.assertEqual(rec['family'], 'PE32+/x64')

    def test_managed_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'managed.exe'
            pe(p, 0x14C, 0x10B, clr=True)
            rec = classify(p)
            self.assertEqual(rec['verdict'], 'UNSUPPORTED')
            self.assertEqual(rec['family'], 'PE32/.NET')

    def test_non_pe_and_ne_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'mz.exe'
            p.write_bytes(b'MZ' + b'\0' * 100)
            rec = classify(p)
            self.assertEqual(rec['verdict'], 'UNSUPPORTED')
            self.assertIn('without a PE header', rec['reason'])
            q = Path(d) / 'ne.exe'
            data = bytearray(b'MZ' + b'\0' * 100)
            struct.pack_into('<H', data, 0x18, 0x40)
            q.write_bytes(data)
            self.assertEqual(classify(q)['family'], 'Win16/NE')


if __name__ == '__main__':
    unittest.main()
