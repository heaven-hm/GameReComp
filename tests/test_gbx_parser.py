"""GBX header parser tests, including optional real TrackMania fixtures."""
import os
import struct
import unittest
from pathlib import Path

from tools.gbx.parser import GbxError, parse_header, parse_file


def synthetic(version=6, mode=b"R", user_data=b"", nodes=1):
    data = bytearray(b"GBX")
    data += struct.pack("<H", version)
    data += b"B" + b"U" + b"C"
    if version >= 4:
        data += mode
    data += struct.pack("<I", 0x3F008000)
    if version >= 6:
        data += struct.pack("<i", len(user_data)) + user_data
    data += struct.pack("<i", nodes)
    return bytes(data)


class GbxParserTest(unittest.TestCase):
    def test_synthetic_v6(self):
        info = parse_header(synthetic())
        self.assertEqual(info["version"], 6)
        self.assertEqual(info["mode"], "release")
        self.assertEqual(info["class_id"], "0x3F008000")
        self.assertEqual(info["node_count"], 1)
        self.assertEqual(info["body_compression"], "compressed")
        self.assertEqual(len(info["input_sha256"]), 64)

    def test_synthetic_v3_has_no_mode_byte(self):
        info = parse_header(synthetic(version=3))
        self.assertEqual(info["version"], 3)
        self.assertEqual(info["mode"], "editor")

    def test_malformed_inputs(self):
        bad = [b"", b"not a gbx", synthetic()[:6], synthetic(mode=b"X"),
               synthetic(user_data=b"", nodes=-1)]
        for data in bad:
            with self.subTest(data=data[:12]):
                with self.assertRaises(GbxError):
                    parse_header(data)
        # negative user data length
        d = bytearray(synthetic())
        struct.pack_into("<i", d, 12, -5)
        with self.assertRaises(GbxError):
            parse_header(bytes(d))

    def test_real_fixtures_when_supplied(self):
        root = os.environ.get("RECOMP_GBX_FIXTURES")
        if not root:
            self.skipTest("RECOMP_GBX_FIXTURES not set; no original game assets used")
        files = sorted(Path(root).rglob("*.Gbx"))[:5]
        if not files:
            self.skipTest("no GBX fixtures under RECOMP_GBX_FIXTURES")
        for path in files:
            with self.subTest(path=str(path)):
                info = parse_file(path)
                self.assertIn(info["version"], range(1, 10))
                self.assertGreaterEqual(info["node_count"], 0)
                self.assertLess(info["body_offset"], info["input_size"])
                self.assertEqual(len(info["input_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
