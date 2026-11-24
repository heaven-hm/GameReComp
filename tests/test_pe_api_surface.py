"""Tests for the arm64-recompiler API-surface audit port.

The audited code is ``tools/pe/audit_api_surface.py``, adapted from three
MIT-licensed scripts in Sahilg93/arm64-recompiler at upstream commit
bc1ca0ecd6decdbf46cbd378a392a60863f81293 (see
``docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md``). The fixtures are built by
``tests/fixtures/make_pe_api_fixture.py`` and cross-checked here against the
independent ``pefile`` parser when it happens to be installed -- the tool and
these tests otherwise have no third-party dependency.
"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pe"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

from audit_api_surface import (  # noqa: E402
    PEImage, audit_pe, build_api_db, rank_missing_imports)
from make_pe_api_fixture import build_pe  # noqa: E402

FIX32 = ROOT / "tests" / "fixtures" / "pe32-api.dll"
FIX64 = ROOT / "tests" / "fixtures" / "pe64-api.dll"
FIX_MINIMAL = ROOT / "tests" / "fixtures" / "pe32-minimal.exe"

EXPECTED_IMPORTS = {"KERNEL32.dll": ["ExitProcess", "GetCurrentProcessId", "ordinal_42"]}
EXPECTED_EXPORTS = ["CreateFileA", "GetTickCount"]


class FixtureTests(unittest.TestCase):
    def test_checked_in_fixtures_match_generator(self):
        for path, pe64 in ((FIX32, False), (FIX64, True)):
            with self.subTest(path=path.name):
                expected = hashlib.sha256(build_pe(pe64)).hexdigest()
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)

    @unittest.skipUnless(__import__("importlib").util.find_spec("pefile"), "pefile not installed")
    def test_fixtures_are_valid_pes_per_pefile(self):
        """Independent parser agrees with our reader (no hard dependency)."""
        import pefile  # type: ignore

        for path, magic in ((FIX32, 0x10B), (FIX64, 0x20B)):
            with self.subTest(path=path.name):
                other = pefile.PE(str(path))
                ours = PEImage(path.read_bytes())
                self.assertEqual(other.OPTIONAL_HEADER.Magic, magic)
                self.assertEqual(other.OPTIONAL_HEADER.ImageBase, ours.image_base)
                self.assertEqual([s.name.decode() for s in other.DIRECTORY_ENTRY_EXPORT.symbols],
                                 ours.exports())
                self.assertEqual(
                    [[i.name.decode() if i.name else f"ordinal_{i.ordinal}"
                      for i in dll.imports] for dll in other.DIRECTORY_ENTRY_IMPORT],
                    [v for v in ours.imports().values()])


class AuditTests(unittest.TestCase):
    def test_pe32_report(self):
        report = audit_pe(FIX32)
        self.assertEqual(report["file"], "pe32-api.dll")
        self.assertEqual(report["pe_format"], "PE32")
        self.assertEqual(report["machine"], "I386")
        self.assertEqual(report["entry_rva"], "0x1000")
        self.assertEqual(report["sha256"], hashlib.sha256(FIX32.read_bytes()).hexdigest())
        self.assertEqual(report["imports"], EXPECTED_IMPORTS)
        self.assertEqual(report["import_slots"], 3)
        self.assertEqual(report["exports"], EXPECTED_EXPORTS)
        self.assertEqual(report["export_slots"], 2)
        self.assertEqual(report["writable_sections"], [".data"])
        self.assertTrue(report["tls_directory"])
        self.assertEqual(report["tls_callbacks"], 3)
        self.assertNotIn("audit_error", report)

    def test_pe64_report(self):
        """PE32+ uses 8-byte thunks and a different optional-header layout."""
        report = audit_pe(FIX64)
        self.assertEqual(report["pe_format"], "PE32+")
        self.assertEqual(report["machine"], "AMD64")
        self.assertEqual(report["imports"], EXPECTED_IMPORTS)
        self.assertEqual(report["exports"], EXPECTED_EXPORTS)
        self.assertEqual(report["tls_callbacks"], 3)
        self.assertEqual(report["writable_sections"], [".data"])
        self.assertNotIn("audit_error", report)

    def test_existing_pe32_minimal_fixture(self):
        report = audit_pe(FIX_MINIMAL)
        self.assertEqual(report["pe_format"], "PE32")
        self.assertEqual(report["imports"], {"KERNEL32.dll": ["ExitProcess"]})
        self.assertEqual(report["sha256"],
                         "583759c15211f04bd7fd69d96b140ed8a7b1ed8306c5c54fd09163cd6693cfe3")

    def test_non_pe_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not-a-pe.dll"
            path.write_bytes(b"just some text, not MZ\n")
            report = audit_pe(path)
        self.assertIn("audit_error", report)
        self.assertIn("not an MZ image", report["audit_error"])

    def test_truncated_pe_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "truncated.exe"
            path.write_bytes(FIX32.read_bytes()[:0x90])
            report = audit_pe(path)
        self.assertIn("audit_error", report)

    def test_tls_callback_limit(self):
        """Upstream audit_pe.py refuses tables larger than 256 entries."""
        pe = PEImage(build_pe(False))
        pe._ptr = lambda _off: 1  # every callback slot is occupied
        self.assertEqual(pe.tls_callbacks(), 256)


class ApiDbTests(unittest.TestCase):
    def test_build_api_db(self):
        db = build_api_db([FIX32, FIX64])
        self.assertEqual(db["dll_count"], 2)
        self.assertEqual(db["total_symbols"], 2)
        self.assertEqual(db["api_to_dll"],
                         {"CreateFileA": "pe32-api.dll", "GetTickCount": "pe32-api.dll"})
        self.assertEqual(db["dll_to_apis"]["pe64-api.dll"], EXPECTED_EXPORTS)
        self.assertEqual(db["core_dlls"], {})

    def test_core_dlls_subset(self):
        """Renaming a fixture to a core DLL populates upstream's core_dlls map."""
        with tempfile.TemporaryDirectory() as tmp:
            kernel32 = Path(tmp) / "KERNEL32.DLL"
            kernel32.write_bytes(FIX64.read_bytes())
            db = build_api_db([kernel32])
        self.assertEqual(db["core_dlls"], {"kernel32.dll": EXPECTED_EXPORTS})
        self.assertEqual(db["api_to_dll"]["CreateFileA"], "kernel32.dll")

    def test_rank_missing_imports(self):
        result = rank_missing_imports([FIX32, FIX64], {"ExitProcess"}, top_n=5)
        self.assertEqual(result["by_file"], {
            "pe32-api.dll": ["kernel32.dll!GetCurrentProcessId"],
            "pe64-api.dll": ["kernel32.dll!GetCurrentProcessId"],
        })
        self.assertEqual(result["top_missing"], [("GetCurrentProcessId", 2)])
        self.assertEqual(result["by_dll"], {"kernel32.dll": {"GetCurrentProcessId": 2}})

    def test_rank_empty_known(self):
        result = rank_missing_imports([FIX32], set())
        self.assertEqual(result["top_missing"],
                         [("ExitProcess", 1), ("GetCurrentProcessId", 1)])


class CliTests(unittest.TestCase):
    def test_cli_audit_json(self):
        proc = subprocess.run(
            [sys.executable, "-m", "tools.pe.audit_api_surface", str(FIX32), str(FIX64)],
            cwd=ROOT, text=True, capture_output=True, check=True)
        reports = json.loads(proc.stdout)
        self.assertEqual([r["pe_format"] for r in reports], ["PE32", "PE32+"])

    def test_cli_api_db(self):
        proc = subprocess.run(
            [sys.executable, "-m", "tools.pe.audit_api_surface", "--api-db",
             str(FIX32), str(FIX64)],
            cwd=ROOT, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(proc.stdout)["api_to_dll"],
                         {"CreateFileA": "pe32-api.dll", "GetTickCount": "pe32-api.dll"})

    def test_cli_rank(self):
        with tempfile.TemporaryDirectory() as tmp:
            known = Path(tmp) / "known.txt"
            known.write_text("ExitProcess\nCreateFileA\n")
            proc = subprocess.run(
                [sys.executable, "-m", "tools.pe.audit_api_surface",
                 "--known", str(known), str(FIX32)],
                cwd=ROOT, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(proc.stdout)["top_missing"],
                         [["GetCurrentProcessId", 1]])


if __name__ == "__main__":
    unittest.main()
