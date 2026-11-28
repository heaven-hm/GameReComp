"""Fail-closed CLI/.NET metadata detection.

The deterministic fixture proves the parser reads a COR20 header, the runtime
version pair, the BSJB metadata root and its streams. The malformed variants
prove that damaged structures are rejected rather than guessed at. Where the
real corpus is present on this host, its managed assemblies are parsed too --
but detection only ever yields UNSUPPORTED_NOT_TESTED, never a build claim.
"""

import json
import struct
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pe"))

from cli_metadata import (  # noqa: E402
    MANAGED, MALFORMED, NATIVE, NOT_PE, detect_bytes, detect_file,
)
from gen_cli_fixture import build_fixture, VARIANTS  # noqa: E402

FIXTURE_SHA256 = "5d829946730796c343c4c860ced3cdcde26eb67fefa7d33bb03f10ecb4e4cca4"
CORPUS = Path.home() / "Downloads" / "recomp-apps"
REAL_ASSEMBLIES = {
    "luhn/luhn.exe": "61069ebfe79053477e3859075d0a9c800d60d755a795a28ad069efc1d851b304",
    "luhn/luhn.dll": "e30a028e8db474b8a3b17f7088efb8054b818f08b1e30011a50112480d5be353",
    "tictactoe/TicTacToe.exe":
        "df4aec7299b1e002ca0e1008b20a889e0cfe5b482a19958399fab16ac6090402",
    "windows/x86/games/tictactoe-net.exe":
        "df4aec7299b1e002ca0e1008b20a889e0cfe5b482a19958399fab16ac6090402",
}


class FixtureTests(unittest.TestCase):
    def test_fixture_is_deterministic(self):
        a = build_fixture()
        self.assertEqual(a, build_fixture())
        import hashlib
        self.assertEqual(hashlib.sha256(a).hexdigest(), FIXTURE_SHA256)

    def test_parses_known_fixture(self):
        r = detect_bytes(build_fixture(), path="fixture.exe")
        self.assertEqual(r.status, MANAGED)
        self.assertTrue(r.is_pe)
        self.assertTrue(r.is_managed)
        self.assertEqual(r.pe_type, "PE32")
        self.assertFalse(r.recompile_attempted)
        self.assertIn("detection only", r.claim)
        cor = r.cor20
        self.assertIsNotNone(cor)
        self.assertEqual(cor.cb, 0x48)
        self.assertEqual((cor.major_runtime_version, cor.minor_runtime_version), (2, 5))
        self.assertEqual(cor.flag_names, ["ILONLY"])
        meta = cor.metadata
        self.assertEqual(meta.runtime_version, "v2.0.50727")
        self.assertEqual([s.name for s in meta.streams], ["#~", "#Strings", "#Blob"])
        self.assertEqual(meta.tables_stream, "#~")
        self.assertEqual(meta.missing_expected_streams, [])
        for stream in meta.streams:
            self.assertTrue(stream.known)
            self.assertGreater(stream.size, 0)
        # Stream offsets are relative to the root and stay inside it.
        self.assertTrue(all(s.offset + s.size <= meta.size for s in meta.streams))

    def test_json_round_trip_keeps_verdict(self):
        r = detect_bytes(build_fixture(), path="fixture.exe").as_json()
        self.assertEqual(json.loads(json.dumps(r))["status"], MANAGED)
        self.assertFalse(r["recompile_attempted"])

    def test_cli_writes_deterministic_report(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "fixture.exe"
            one, two = Path(td) / "one.json", Path(td) / "two.json"
            exe.write_bytes(build_fixture())
            cmd = [sys.executable, "-m", "tools.pe.cli_metadata", str(exe),
                   "--quiet"]
            for out in (one, two):
                subprocess.run(cmd + ["--json", str(out)], check=True, cwd=ROOT,
                               capture_output=True)
            self.assertEqual(one.read_bytes(), two.read_bytes())
            self.assertEqual(json.loads(one.read_text())["counts"], {MANAGED: 1})


class FailClosedTests(unittest.TestCase):
    def test_malformed_variants_are_rejected(self):
        expected = {
            "bad-signature": "bad metadata signature",
            "bad-stream-offset": "stream range outside metadata root",
            "truncated": "RVA range truncated by file",
            "missing-tables": "missing #~/#- metadata tables stream",
            "bad-cor-size": "COM descriptor directory too small",
            "bad-cor-cb": "invalid COR20 cb",
        }
        for variant, reason in expected.items():
            self.assertIn(variant, VARIANTS)
            with self.subTest(variant=variant):
                r = detect_bytes(build_fixture(variant), path=variant)
                self.assertEqual(r.status, MALFORMED)
                self.assertIn(reason, r.reason)
                self.assertFalse(r.is_managed)
                self.assertFalse(r.recompile_attempted)
                self.assertIsNone(r.cor20)

    def test_native_pe_and_non_pe_are_not_managed(self):
        native = detect_bytes(build_fixture("no-com-dir"), path="native.exe")
        self.assertEqual(native.status, NATIVE)
        self.assertFalse(native.is_managed)
        self.assertEqual(detect_bytes(b"not a PE at all").status, NOT_PE)
        # A PE signature with a nonsense e_lfanew must not be trusted.
        broken = bytearray(build_fixture())
        struct.pack_into("<I", broken, 0x3C, 0xFFFFFF00)
        self.assertEqual(detect_bytes(bytes(broken)).status, MALFORMED)

    def test_random_corruption_never_claims_a_build(self):
        good = build_fixture()
        for pos in (0x98, 0x168, 0x200, 0x204, 0x300, 0x304, 0x320):
            with self.subTest(pos=hex(pos)):
                bad = bytearray(good)
                bad[pos] ^= 0xFF
                r = detect_bytes(bytes(bad))
                self.assertFalse(r.recompile_attempted)
                self.assertNotIn("PASS", r.status)
                self.assertIn(r.status, {MANAGED, NATIVE, NOT_PE, MALFORMED})
                if r.is_managed:
                    self.assertEqual(r.status, MANAGED)

    def test_missing_file_is_reported_not_raised(self):
        r = detect_file(Path(tempfile.gettempdir()) / "does-not-exist-x1.exe")
        self.assertEqual(r.status, "REJECTED_IO")
        self.assertFalse(r.recompile_attempted)


class PipelineGateTests(unittest.TestCase):
    def test_inspect_refuses_managed_assembly(self):
        from tools.autorecomp.pipeline import Pipeline, StageFailure

        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "managed.exe"
            exe.write_bytes(build_fixture())
            project = Path(td) / "project"
            pipeline = Pipeline(exe, project_dir=project, build=False,
                                run_binary=False)
            with self.assertRaises(StageFailure) as caught:
                pipeline.stage_inspect()
            data = caught.exception.data
            self.assertEqual(data["status"], MANAGED)
            self.assertEqual(data["clr"], "2.5")
            self.assertEqual(data["streams"], ["#~", "#Strings", "#Blob"])
            self.assertIn("UNSUPPORTED_NOT_TESTED", str(caught.exception))
            report = json.loads((project / "reports" / "cli_detection.json").read_text())
            self.assertEqual(report["status"], MANAGED)
            self.assertFalse(report["recompile_attempted"])
            self.assertFalse((project / "reports" / "inspect.json").exists())


@unittest.skipUnless(CORPUS.is_dir(), "real corpus not present on this host")
class RealAssemblyTests(unittest.TestCase):
    def test_real_managed_assemblies(self):
        for rel, sha in REAL_ASSEMBLIES.items():
            path = CORPUS / rel
            if not path.is_file():
                continue
            with self.subTest(path=rel):
                r = detect_file(path)
                self.assertEqual(r.sha256, sha)
                self.assertEqual(r.status, MANAGED)
                self.assertTrue(r.is_managed)
                self.assertEqual(r.pe_type, "PE32")
                self.assertIsNotNone(r.cor20)
                self.assertEqual(r.cor20.major_runtime_version, 2)
                self.assertEqual(r.cor20.minor_runtime_version, 5)
                self.assertEqual(r.cor20.metadata.runtime_version, "v4.0.30319")
                self.assertIn("#~", [s.name for s in r.cor20.metadata.streams])
                self.assertIn("#Strings", [s.name for s in r.cor20.metadata.streams])
                self.assertIn("#Blob", [s.name for s in r.cor20.metadata.streams])
                self.assertFalse(r.recompile_attempted)


if __name__ == "__main__":
    unittest.main()
