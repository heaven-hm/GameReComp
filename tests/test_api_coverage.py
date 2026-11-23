"""Tests for the machine-readable API coverage auditor."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.api_coverage import (  # noqa: E402
    RECENTLY_REGISTERED_SHIMS,
    SYSTEM_DLLS,
    build_report,
    dll_kind,
    import_name,
    pe_imports,
    recently_registered_names,
    registered_shims,
    render_markdown,
    shim_import_names,
    strip_comments,
)


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

    # ---- namespaced shims ------------------------------------------------

    def test_namespaced_literals_are_extracted(self):
        """Full namespaced literals in X-macro and table forms are real shims."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "d3d.c").write_text('''
                X("D3D9::Object::QueryInterface", d3d_qi, 3)
                X("D3D9::Device::BeginScene", dev_begin, 1)
                { "IStream::Read", sm_Read },
            ''')
            raw = registered_shims(d)
        self.assertIn("D3D9::Object::QueryInterface", raw)
        self.assertIn("D3D9::Device::BeginScene", raw)
        self.assertIn("IStream::Read", raw)

    def test_namespaced_shims_map_to_imported_function_name(self):
        """The leaf after '::' is the name a PE import table carries."""
        raw = {
            "D3D9::Object::QueryInterface",
            "IDirect3D8::CreateDevice",
            "IStream::QueryInterface",
            "ExitProcess",
        }
        names = shim_import_names(raw)
        self.assertEqual(import_name("D3D9::Object::QueryInterface"), "QueryInterface")
        self.assertEqual(import_name("IDirect3D8::CreateDevice"), "CreateDevice")
        self.assertEqual(import_name("IStream::QueryInterface"), "QueryInterface")
        self.assertEqual(import_name("ExitProcess"), "ExitProcess")
        self.assertIn("QueryInterface", names)
        self.assertIn("CreateDevice", names)
        self.assertIn("ExitProcess", names)
        # The full registered name is still present, not replaced by the leaf.
        self.assertIn("D3D9::Object::QueryInterface", names)
        self.assertIn("IDirect3D8::CreateDevice", names)

    def test_concatenated_namespaced_leaf_from_x_macro(self):
        """d3d8.c builds 'IDirect3D8::' name; the X list gives us the leaf."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "d3d8.c").write_text('''
                #define D3D8_D3D_METHODS(X) \\
                    X("QueryInterface", d3d8_d3d_qi, 3) \\
                    X("CreateDevice", d3d8_d3d_create_device, 7)
                #define D3D8_D3D_ENTRY(name, fn, argc) { "IDirect3D8::" name, fn },
                const struct win32hle_shim t[] = {
                    D3D8_D3D_METHODS(D3D8_D3D_ENTRY)
                    { 0, 0 }
                };
            ''')
            raw = registered_shims(d)
            names = shim_import_names(raw)
        self.assertIn("CreateDevice", raw)
        self.assertIn("QueryInterface", raw)
        self.assertIn("CreateDevice", names)

    # ---- aliases and ordinals -------------------------------------------

    def test_ordinal_aliases_register_both_name_and_dll_qualified_ordinal(self):
        """SHIM2/AUT register a 'dll#N' alias plus the plain name."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "wsock.c").write_text('''
                #define SHIM2(ord, name, fn) { "wsock32.dll#" #ord, fn }, { name, fn }
                #define AUT(ord, name, fn) { "oleaut32.dll#" #ord, fn }, { name, fn }
                SHIM2(12, "WSAStartup", wsa_startup),
                AUT(4, "SafeArrayCreate", sac),
            ''')
            raw = registered_shims(d)
            names = shim_import_names(raw)
        self.assertIn("wsock32.dll#12", raw)
        self.assertIn("WSAStartup", raw)
        self.assertIn("oleaut32.dll#4", raw)
        self.assertIn("SafeArrayCreate", raw)
        self.assertEqual(import_name("wsock32.dll#12"), "#12")
        self.assertEqual(import_name("oleaut32.dll#4"), "#4")
        self.assertIn("#12", names)
        self.assertIn("#4", names)

    def test_name_aliases_are_all_counted(self):
        """Two registered names for one function are two shim names."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "aliases.c").write_text('''
                { "GetVersion", get_version },
                { "GetVersionA", get_version },
                { "IStream::Read", sm_Read },
                { "IStorage::Read", sm_Read },
            ''')
            raw = registered_shims(d)
            names = shim_import_names(raw)
        self.assertTrue({"GetVersion", "GetVersionA", "IStream::Read", "IStorage::Read"}.issubset(raw))
        # Both interfaces alias the same leaf import name.
        self.assertIn("Read", names)

    def test_report_matches_ordinal_imports_by_dll_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "fixture.exe").write_bytes(b"MZ")
            imports = {
                "wsock32.dll": ["#12", "#99"],
                "bulletml.dll": ["#7"],
            }
            with mock.patch("tools.api_coverage.pe_imports", return_value=imports):
                report = build_report(d, {"wsock32.dll#12"})
        key = next(iter(report["inputs"]))
        self.assertEqual(report["inputs"][key]["ordinals"], {"wsock32.dll": ["#99"]})
        self.assertEqual(report["inputs"][key]["game_owned_ordinals"], {"bulletml.dll": ["#7"]})
        self.assertEqual(report["summary"]["ordinal_imports"], 1)
        self.assertEqual(report["summary"]["game_owned_ordinal_imports"], 1)

    # ---- comment handling ------------------------------------------------

    def test_comments_never_become_shims(self):
        text = '/* { "NotARealApi", fn }, */ // { "AlsoFake", fn },\n{ "RealApi", fn },'
        self.assertNotIn("NotARealApi", strip_comments(text))
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "x.c").write_text(text)
            raw = registered_shims(d)
        self.assertIn("RealApi", raw)
        self.assertNotIn("NotARealApi", raw)
        self.assertNotIn("AlsoFake", raw)

    # ---- system vs game-owned split --------------------------------------

    def test_dll_kind_allow_list(self):
        self.assertIn("kernel32.dll", SYSTEM_DLLS)
        self.assertEqual(dll_kind("KERNEL32.DLL"), "system")
        self.assertEqual(dll_kind("d3d9.dll"), "system")
        self.assertEqual(dll_kind("binkw32.dll"), "game-owned")
        self.assertEqual(dll_kind("bulletml.dll"), "game-owned")
        self.assertEqual(dll_kind("d3dx9_30.dll"), "game-owned")
        self.assertEqual(dll_kind("msvcp140.dll"), "game-owned")
        self.assertEqual(dll_kind("vcruntime140.dll"), "game-owned")

    def test_report_separates_system_and_game_owned_imports(self):
        """Game-owned DLLs never inflate the Win32 'missing API' headline."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "fixture.exe").write_bytes(b"MZ")
            imports = {
                "kernel32.dll": ["ExitProcess", "SomeSystemMissing"],
                "bulletml.dll": ["bulletml_init", "game_only_api"],
                "msvcp140.dll": ["?foo@@YAXXZ"],
            }
            shims = {"ExitProcess", "bulletml_init"}
            with mock.patch("tools.api_coverage.pe_imports", return_value=imports):
                report = build_report(d, shims)

        self.assertEqual(report["schema_version"], "1.1.0")
        self.assertEqual(report["summary"]["distinct_missing_api"], 1)
        self.assertEqual(report["summary"]["game_owned_missing_api"], 2)
        self.assertEqual(report["summary"]["all_missing_api"], 3)
        self.assertEqual(report["summary"]["system_dlls"], 1)
        self.assertEqual(report["summary"]["game_owned_dlls"], 2)
        self.assertEqual(report["dll_classification"]["system"], ["kernel32.dll"])
        self.assertEqual(
            report["dll_classification"]["game_owned"],
            ["bulletml.dll", "msvcp140.dll"],
        )
        self.assertEqual(
            [(x["dll"], x["name"]) for x in report["missing_by_api"]],
            [("kernel32.dll", "SomeSystemMissing")],
        )
        pairs = {(x["dll"], x["name"]) for x in report["game_owned_missing_by_api"]}
        self.assertEqual(
            pairs,
            {("bulletml.dll", "game_only_api"), ("msvcp140.dll", "?foo@@YAXXZ")},
        )
        self.assertIn("registration", report["is_not"].lower())

    def test_report_registered_counts_are_from_the_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "fixture.exe").write_bytes(b"MZ")
            with mock.patch("tools.api_coverage.pe_imports", return_value={}):
                report = build_report(d, {"FooA", "D3D9::Device::Present", "wsock32.dll#9"})
        self.assertEqual(report["registered_shim_count"], 3)
        # FooA, D3D9 full name, leaf 'Present', wsock full name, ordinal '#9'
        self.assertEqual(report["registered_import_name_count"], 5)

    def test_report_is_json_serialisable(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            shutil.copy(ROOT / "tests/fixtures/pe32-minimal.exe", d / "fixture.exe")
            report = build_report(d, {"ExitProcess"})
        json.dumps(report)

    # ---- generated markdown doc -----------------------------------------

    def _sample_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "fixture.exe").write_bytes(b"MZ")
            imports = {
                "kernel32.dll": ["ExitProcess", "MissingSysA", "MissingSysB"],
                "bulletml.dll": ["bullet_init", "game_only"],
            }
            with mock.patch("tools.api_coverage.pe_imports", return_value=imports):
                return build_report(d, {"ExitProcess", "bullet_init",
                                        "D3D9::Device::Present"})

    def test_render_markdown_uses_report_numbers(self):
        report = self._sample_report()
        doc = render_markdown(report, corpus="/fake/games",
                              out="/tmp/api-coverage.json",
                              generated_at="2026-01-01T00:00:00Z")
        s = report["summary"]
        self.assertIn(f"| PE32 inputs analysed | {s['inputs']} |", doc)
        self.assertIn(f"| Registered shim names (`registered_shim_count`) | "
                      f"{report['registered_shim_count']} |", doc)
        self.assertIn(f"Missing Win32 **system** APIs (`distinct_missing_api`) | "
                      f"{s['distinct_missing_api']} |", doc)
        self.assertIn("| `kernel32.dll` | `MissingSysA` | 1 |", doc)
        self.assertIn("| `bulletml.dll` | `game_only` | 1 |", doc)
        self.assertIn("`/fake/games`", doc)
        self.assertIn("`2026-01-01T00:00:00Z`", doc)

    def test_render_markdown_is_deterministic(self):
        report = self._sample_report()
        kw = dict(corpus="/fake/games", generated_at="2026-01-01T00:00:00Z")
        self.assertEqual(render_markdown(report, **kw),
                         render_markdown(report, **kw))

    def test_render_markdown_never_claims_completion(self):
        report = self._sample_report()
        doc = render_markdown(report, generated_at="2026-01-01T00:00:00Z")
        self.assertIn("Coverage is incomplete", doc)
        self.assertIn("registration audit", doc)
        # Negative: there is no positive completion claim.
        self.assertNotIn("coverage is complete", doc.lower())
        self.assertNotIn("100% coverage achieved", doc.lower())

    # ---- recently registered shims (additive section) -------------------

    def test_recently_registered_shims_match_last_checkpoint(self):
        """24 names, grouped by the checkpoint's agent work; no duplicates."""
        names = recently_registered_names()
        self.assertEqual(len(names), 24)
        self.assertEqual(len(set(names)), 24)
        self.assertEqual(len(RECENTLY_REGISTERED_SHIMS), 5)
        for group in ("A22", "A26", "A27", "A28", "A29"):
            self.assertTrue(any(g.startswith(group) for g in RECENTLY_REGISTERED_SHIMS))

    def test_recently_registered_shims_are_actually_registered(self):
        """The documented delta must be a subset of the real shim registry."""
        self.assertTrue(set(recently_registered_names()).issubset(registered_shims()))

    def test_render_markdown_lists_recently_registered_shims_with_caveat(self):
        doc = render_markdown(self._sample_report(),
                              generated_at="2026-01-01T00:00:00Z")
        self.assertIn("## Recently registered shims (24, last checkpoint)", doc)
        self.assertIn("`docs/ISSUE_EXECUTION_PLAN.md`", doc)
        for name in recently_registered_names():
            self.assertIn(f"`{name}`", doc)
        self.assertIn("Registration only proves a name is present", doc)
        self.assertIn("not extra coverage", doc)


if __name__ == "__main__":
    unittest.main()
