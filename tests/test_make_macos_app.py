"""The macOS app packager is reproducible and fail-closed on missing input."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.make_macos_app import make_app, sha256  # noqa: E402


class MakeAppTests(unittest.TestCase):
    def test_bundle_layout_and_launcher(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            binary = root / "game-native"
            binary.write_bytes(b"#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
            assets = root / "game"
            assets.mkdir()
            (assets / "data").mkdir()
            (assets / "data" / "level.dat").write_bytes(b"level")
            (assets / "readme.txt").write_text("hello")
            app = make_app(binary, assets, root / "dist", "TestGame")
            contents = app / "Contents"
            self.assertTrue((contents / "MacOS" / "game-native").is_file())
            self.assertEqual(sha256(contents / "MacOS" / "game-native"), sha256(binary))
            self.assertTrue((contents / "Resources" / "data" / "level.dat").is_file())
            self.assertTrue((contents / "Resources" / "readme.txt").is_file())
            plist = contents / "Info.plist"
            self.assertIn("CFBundleExecutable", plist.read_text())
            self.assertIn("com.gamerecomp.testgame", plist.read_text())
            launcher = contents / "MacOS" / "TestGame"
            self.assertTrue(launcher.is_file())
            self.assertTrue(launcher.stat().st_mode & 0o111)
            self.assertIn('cd "$HERE/../Resources"', launcher.read_text())
            self.assertIn('exec "$HERE/game-native"', launcher.read_text())
            manifest = json.loads((contents / "manifest.json").read_text())
            self.assertEqual(manifest["native_sha256"], sha256(binary))

    def test_missing_inputs_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            binary = root / "missing"
            with self.assertRaises(ValueError):
                make_app(binary, root, root / "dist", "X")
            binary.write_bytes(b"x")
            with self.assertRaises(ValueError):
                make_app(binary, root / "no-assets", root / "dist", "X")


if __name__ == "__main__":
    unittest.main()
