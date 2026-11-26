"""Tests for the issue #14 compatibility inventory and regression gate."""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import compatibility as compat  # noqa: E402


def baseline_and_schema():
    schema = json.loads((ROOT / "docs/compatibility/coverage.schema.json").read_text())
    baseline = json.loads((ROOT / "docs/compatibility/baseline.json").read_text())
    return baseline, schema


class CompatibilityInventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline, cls.schema = baseline_and_schema()
        cls.doc = compat.build_document(ROOT, cls.baseline, cls.schema, "test", "HEAD")

    def test_schema_is_valid_draft_2020_12(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema not installed")
        Draft202012Validator.check_schema(self.schema)

    def test_generated_document_validates_and_gate_passes(self):
        self.assertEqual(compat.validate(self.doc, self.schema), [])
        self.assertEqual(self.doc["gate"]["verdict"], compat.PASS)
        observed = {r["fixture_id"] for r in self.doc["inputs"].values() if r["fixture_id"]}
        self.assertTrue(set(self.baseline["fixture_ids"]).issubset(observed))

    def test_every_input_is_keyed_by_sha256_and_missing_never_passes(self):
        for key, rec in self.doc["inputs"].items():
            self.assertRegex(key, r"^[0-9a-f]{64}$")
            self.assertEqual(key, rec["input_sha256"])
            if not rec["present"]:
                self.assertEqual(rec["status"], compat.MISSING_INPUT)
                self.assertNotEqual(rec["observed_result"], compat.PASS)
            else:
                self.assertEqual(key, hashlib.sha256(
                    (ROOT / rec["path"]).read_bytes()).hexdigest()
                    if (ROOT / rec["path"]).is_file() else rec["bytes_sha256"])

    def test_synthetic_fixtures_are_deterministic_and_distinct(self):
        mod = compat._load_module(ROOT / "tests/fixtures/make_compat_fixture.py",
                                  "grc_test_fixture")
        supported = mod.build_pe32_import("KERNEL32.dll", "ExitProcess")
        unsupported = mod.build_pe32_import("DINPUT8.dll", "DirectInput8Create")
        self.assertEqual(supported, mod.build_pe32_import("KERNEL32.dll", "ExitProcess"))
        self.assertNotEqual(compat.sha256_bytes(supported),
                            compat.sha256_bytes(unsupported))
        self.assertNotEqual(supported[:2], b"NE")
        ne = mod.build_ne("WIN16", "USER32.EXE", "GetMessage")
        self.assertEqual(ne[0x40:0x42], b"NE")
        plus = mod.build_pe32plus_import("KERNEL32.dll", "GetProcAddress")
        self.assertEqual(plus[0x98:0x9A], b"\x0b\x02")

    def test_missing_inputs_are_not_run_never_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp)
            doc = compat.build_document(empty, self.baseline, self.schema, "empty", "HEAD")
            probe = next(r for r in doc["inputs"].values()
                         if r["fixture_id"] == "fx-missing-input-probe")
            self.assertEqual(probe["observed_result"], compat.MISSING_INPUT)
            self.assertNotIn(compat.PASS,
                             {r["observed_result"] for r in doc["inputs"].values()
                              if not r["present"]})
            self.assertEqual(doc["gate"]["checks"]["missing_never_pass"], [])
            self.assertNotEqual(doc["gate"]["verdict"], compat.PASS)

    def test_unsupported_rows_are_never_pass(self):
        for family in self.doc["families"]:
            if family["expected_implemented_status"] == "not-present":
                self.assertEqual(family["implemented_status"], "not-present")
                self.assertNotEqual(family["fixture_result"], compat.PASS)
        self.assertEqual(self.doc["gate"]["checks"]["unsupported_never_pass"], [])

    def test_deleted_fixture_fails_gate(self):
        doc = copy.deepcopy(self.doc)
        doc["inputs"] = {k: v for k, v in doc["inputs"].items()
                         if v["fixture_id"] != "fx-directinput-8"}
        gate = compat.make_gate(doc, self.baseline, ROOT, [])
        self.assertEqual(gate["verdict"], compat.FAIL)
        self.assertIn("fx-directinput-8", gate["checks"]["deleted_tests"])

    def test_new_critical_import_fails_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            spy = Path(tmp)
            (spy / "reports/games").mkdir(parents=True)
            (spy / "reports/games/diagnostic-sweep.json").write_text(json.dumps({
                "ranked_first_imports": [{"import": "D3D9.dll!NewCriticalPresent"}],
            }))
            gate = compat.make_gate(self.doc, self.baseline, spy, [])
            self.assertIn("D3D9.dll!NewCriticalPresent",
                          gate["checks"]["new_critical_imports"])
            self.assertEqual(gate["verdict"], compat.FAIL)

    def test_generated_reports_are_concise_and_honest(self):
        matrix = compat.render_api_matrix(self.doc)
        games = compat.render_games(self.doc, ROOT)
        self.assertIn("MISSING_INPUT", matrix)
        self.assertIn("NOT_RUN", matrix)
        self.assertIn("none is claimed here", matrix)
        self.assertIn("HOST_NOT_TESTED", games)
        self.assertLess(len(matrix.splitlines()), 60)
        self.assertLess(len(games.splitlines()), 60)

    def test_reuses_existing_synthetic_pe32_fixture(self):
        legacy = compat._load_module(ROOT / "tests/fixtures/make_pe32_fixture.py",
                                     "grc_test_pe32")
        digest = compat.sha256_bytes(legacy.build_pe())
        rec = next(r for r in self.doc["inputs"].values()
                   if r["fixture_id"] == "fx-existing-pe32-minimal")
        self.assertEqual(rec["input_sha256"], digest)

    def test_all_subsystem_owners_are_enumerated(self):
        owners = {f["owner_issue"] for f in self.baseline["families"]}
        self.assertTrue({1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13}.issubset(owners))
        families = {f["family_id"] for f in self.baseline["families"]}
        self.assertTrue({"directinput-3-5-7", "directinput-8", "winsock-2-directplay",
                         "pe32plus", "direct3d-9", "direct3d-8",
                         "direct3d-9ex-10-11-dxgi"}.issubset(families))


if __name__ == "__main__":
    unittest.main()
