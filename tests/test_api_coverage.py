"""Tests for the machine-readable API coverage auditor."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.api_coverage import registered_shims, pe_imports, build_report  # noqa: E402


class ApiCoverageTest(unittest.TestCase):
    def test_registered_shims_from_all_table_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.c").write_text('''
                { "FooA", foo },
                SHIM2(12, "Bar", bar),
                AUT(4, "Baz", baz),
            ''')
            names = registered_shims(d)
        self.assertTrue({"FooA", "Bar", "Baz"}.issubset(names))

    def test_pe_imports_of_shipped_fixture(self):
        imports = pe_imports(ROOT / "tests/fixtures/pe32-minimal.exe")
        self.assertIn("kernel32.dll", imports)
        self.assertIn("ExitProcess", imports["kernel32.dll"])

    def test_report_keyed_by_input_sha_and_reports_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            import shutil
            shutil.copy(ROOT / "tests/fixtures/pe32-minimal.exe", d / "fixture.exe")
            report = build_report(d, {"SomeOtherApi"})
        self.assertEqual(report["summary"]["inputs"], 1)
        key = next(iter(report["inputs"]))
        self.assertEqual(len(key), 64)
        self.assertEqual(report["inputs"][key]["input_sha256"], key)
        pairs = {(x["dll"], x["name"]) for x in report["missing_by_api"]}
        self.assertIn(("kernel32.dll", "ExitProcess"), pairs)

    def test_registered_runtime_has_shims(self):
        self.assertGreater(len(registered_shims()), 100)


if __name__ == "__main__":
    unittest.main()
