"""The high-level source bridge: typed C compiled into the same executable.

What is being pinned here, in order:

1. A module that claims a verified function without evidence is refused.
2. The generated adapter marshals the guest ABI correctly (argc, RET purge).
3. A replaced address's mechanical body is still emitted and still compiled,
   under its `__lowlevel` name -- the fallback stays visible.
4. The dispatch table calls the high-level entry point, not the mechanical body.
5. The categories in the report are counted from the replacements that were
   actually applied, not from what a module claims.
6. The generated project compiles: the typed function and its adapter go into
   the build, and the mechanical body is still there.
"""

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("pe", "disasm", "lift"):
    sys.path.insert(0, str(ROOT / "tools" / sub))
sys.path.insert(0, str(ROOT))

from tools.autorecomp import highlevel  # noqa: E402
import translator  # noqa: E402


# Fields that belong to the function entry rather than to the manifest.
FUNCTION_FIELDS = ("address", "symbol", "category", "convention", "args",
                   "returns", "provenance")

# A module is written against one binary and must say so. The default target is
# a fixed, valid-looking pair; tests that care override it.
DEFAULT_TARGET = {"sha256": "a" * 64, "entry_point": "0x00401000"}


def write_module(root, name="demo", **overrides):
    """A minimal valid module.

    A keyword that names a function field lands on the function entry; anything
    else lands on the manifest. That keeps the call sites readable --
    write_module(tmp, category="HIGH_LEVEL_VERIFIED") means the function's
    category, which is what a test about categories is asking about.
    """
    module_dir = Path(root) / name
    module_dir.mkdir(parents=True, exist_ok=True)
    fn = {
        "address": "0x00401000",
        "symbol": "player_apply_damage",
        "category": "HIGH_LEVEL_UNVERIFIED",
        "convention": "stdcall",
        "args": [{"name": "player", "type": "Player *"},
                 {"name": "amount", "type": "int"}],
        "returns": "int",
        "provenance": {"evidence": "callers pass a Player* in arg 0"},
    }
    fn.update(overrides.pop("fn", {}))
    manifest = {"schema": 1, "name": name, "provider": "hand-written",
                "target": dict(overrides.pop("target", DEFAULT_TARGET)),
                "functions": [fn]}
    for key in list(overrides):
        if key in FUNCTION_FIELDS:
            fn[key] = overrides.pop(key)
    manifest.update(overrides)
    (module_dir / "module.json").write_text(json.dumps(manifest, indent=2))
    (module_dir / "impl.c").write_text(textwrap.dedent("""\
        #include "highlevel_impl.h"

        int player_apply_damage(Player *player, int amount) {
            if (!player || amount <= 0) {
                return 0;
            }
            player->health -= amount;
            return player->health;
        }
        """))
    return module_dir


class ModuleValidationTest(unittest.TestCase):
    def test_verified_claim_without_evidence_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp, category="HIGH_LEVEL_VERIFIED")
            with self.assertRaises(highlevel.ModuleError) as caught:
                highlevel.load_module(Path(tmp) / "demo")
            self.assertIn("requires provenance.verified_by", str(caught.exception))

    def test_verified_claim_with_evidence_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp, category="HIGH_LEVEL_VERIFIED",
                         fn={"provenance": {"verified_by":
                                            "difftest 12/12 cases, scenario 3"}})
            module = highlevel.load_module(Path(tmp) / "demo")
            self.assertEqual(module["functions"][0]["category"],
                             "HIGH_LEVEL_VERIFIED")

    def test_duplicate_address_across_modules_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp, "a")
            write_module(tmp, "b")
            with self.assertRaises(highlevel.ModuleError):
                highlevel.load_modules(tmp)

    def test_bad_convention_and_address_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp, convention="pascal")
            with self.assertRaises(highlevel.ModuleError):
                highlevel.load_module(Path(tmp) / "demo")
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp, fn={"address": "401000"})
            with self.assertRaises(highlevel.ModuleError):
                highlevel.load_module(Path(tmp) / "demo")


class TargetTest(unittest.TestCase):
    """A module names original addresses, and addresses are not portable.

    The synthetic fixture's entry point is 0x00401000; Ikachan's 0x00401000 is a
    global initialiser that writes to 0x413BE0. Applying one binary's module to
    the other replaced the fixture's entry point with a function writing to
    memory the fixture does not have -- it crashed, which is how it was found.
    """

    def test_a_module_without_a_target_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_dir = Path(tmp) / "demo"
            module_dir.mkdir(parents=True)
            (module_dir / "module.json").write_text(json.dumps({
                "schema": 1, "functions": [{"address": "0x00401000",
                                            "symbol": "f",
                                            "category": "HIGH_LEVEL_UNVERIFIED"}]}))
            (module_dir / "impl.c").write_text("void f(void) {}\n")
            with self.assertRaises(highlevel.ModuleError) as caught:
                highlevel.load_module(module_dir)
            self.assertIn("'target' is required", str(caught.exception))

    def test_a_malformed_target_sha_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp, target={"sha256": "not-a-hash",
                                      "entry_point": "0x00401000"})
            with self.assertRaises(highlevel.ModuleError):
                highlevel.load_module(Path(tmp) / "demo")

    def test_a_module_for_another_binary_is_skipped_not_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp)          # target sha = "aaa..."
            applied, skipped = highlevel.select_modules(tmp, "b" * 64, 0x401000)
        self.assertEqual(applied, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("sha256 does not match", skipped[0]["reason"])

    def test_a_matching_target_is_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp)
            applied, skipped = highlevel.select_modules(
                tmp, "a" * 64, 0x00401000)
        self.assertEqual(len(applied), 1)
        self.assertEqual(skipped, [])

    def test_a_matching_sha_with_a_different_entry_point_is_skipped(self):
        """The same bytes loaded at a different base are a different target."""
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp)
            applied, skipped = highlevel.select_modules(
                tmp, "a" * 64, 0x00402000)
        self.assertEqual(applied, [])
        self.assertIn("entry_point does not match", skipped[0]["reason"])


class AdapterTest(unittest.TestCase):
    def test_stdcall_adapter_pops_its_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp)
            fn = highlevel.load_module(Path(tmp) / "demo")["functions"][0]
        src = highlevel.adapter_source(fn)
        self.assertIn("void sub_00401000(void) {", src)
        self.assertIn("Player * player = (Player *)hle_ptr(A32(0));", src)
        self.assertIn("int amount = (int)A32(1);", src)
        self.assertIn("RET(result, 2);", src)

    def test_cdecl_adapter_leaves_arguments_for_the_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp, convention="cdecl")
            fn = highlevel.load_module(Path(tmp) / "demo")["functions"][0]
        src = highlevel.adapter_source(fn)
        self.assertIn("RETC(result);", src)
        self.assertNotIn("RET(result,", src)

    def test_void_return_uses_the_void_purge(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp, fn={"returns": "void", "args": []})
            fn = highlevel.load_module(Path(tmp) / "demo")["functions"][0]
        src = highlevel.adapter_source(fn)
        self.assertIn("RETV(0);", src)


class _Func:
    def __init__(self, address):
        self.address = address
        self.name = f"sub_{address:08X}"


class _Lifter:
    def __init__(self, iat_map=None, func_names=None, lifted=None):
        pass

    def lift_function(self, func):
        return (f"void {func.name}(void) {{\n"
                f"  /* mechanical body of 0x{func.address:08X} */\n"
                f"}}\n")


class BridgeIntoBuildTest(unittest.TestCase):
    def setUp(self):
        self._original = translator.Lifter
        translator.Lifter = _Lifter
        self.addCleanup(setattr, translator, "Lifter", self._original)

    def _generate(self, highlevel_map):
        funcs = {0x401000: _Func(0x401000), 0x401100: _Func(0x401100)}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name)
        names = translator.generate_recomp_files(
            funcs, {}, str(out), highlevel=highlevel_map)
        return out, dict(names)

    def test_mechanical_body_survives_under_its_lowlevel_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp)
            fn = highlevel.load_module(Path(tmp) / "demo")["functions"][0]
            out, names = self._generate({0x401000: fn})
        unit = "".join(p.read_text() for p in out.glob("recomp_[0-9]*.c"))
        # The fallback is still emitted and still compiled...
        self.assertIn("sub_00401000__lowlevel", unit)
        self.assertIn("mechanical body of 0x00401000", unit)
        # ...and the plain name is not defined twice.
        self.assertNotIn("void sub_00401000(void) {", unit)

    def test_dispatch_table_calls_the_high_level_entry_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp)
            fn = highlevel.load_module(Path(tmp) / "demo")["functions"][0]
            out, names = self._generate({0x401000: fn})
        self.assertEqual(names[0x401000], "sub_00401000")   # the adapter
        dispatch = (out / "recomp_dispatch.c").read_text()
        self.assertIn("{ 0x00401000u, sub_00401000 },", dispatch)
        header = (out / "recomp_funcs.h").read_text()
        self.assertIn("void sub_00401000__lowlevel(void);", header)

    def test_unreplaced_functions_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp)
            fn = highlevel.load_module(Path(tmp) / "demo")["functions"][0]
            out, names = self._generate({0x401000: fn})
        self.assertEqual(names[0x401100], "sub_00401100")
        unit = "".join(p.read_text() for p in out.glob("recomp_[0-9]*.c"))
        self.assertIn("void sub_00401100(void) {", unit)

    def test_categories_count_only_applied_replacements(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp, category="HIGH_LEVEL_VERIFIED",
                         fn={"provenance": {"verified_by": "scenario 3"}})
            module = highlevel.load_module(Path(tmp) / "demo")
        by_address = {module["functions"][0]["address"]: module["functions"][0]}
        # The binary has 0x401000 but not the module's other claim.
        functions = {0x401000: _Func(0x401000), 0x401100: _Func(0x401100)}
        summary = highlevel.summary([module], by_address, functions)
        self.assertEqual(summary["source_categories"]["HIGH_LEVEL_VERIFIED"], 1)
        self.assertEqual(summary["source_categories"]["LOW_LEVEL_FALLBACK"], 1)
        self.assertEqual(summary["high_level_count"], 1)

    def test_generated_project_compiles_with_the_typed_unit(self):
        """The point of the bridge: it is compiled, not merely recorded."""
        cc = subprocess.run(["cc", "--version"], capture_output=True)
        if cc.returncode != 0:
            self.skipTest("no C compiler")
        with tempfile.TemporaryDirectory() as tmp:
            write_module(tmp)
            module = highlevel.load_module(Path(tmp) / "demo")
            out = Path(tmp) / "gen"
            out.mkdir()
            translator.generate_recomp_files(
                {0x401000: _Func(0x401000)}, {}, str(out),
                highlevel={0x401000: module["functions"][0]})
            hl = Path(tmp) / "highlevel"
            hl.mkdir()
            for source in module["sources"]:
                (hl / source.name).write_text(source.read_text())
            highlevel.emit_unit([module], hl)
            (hl / "highlevel_impl.h").write_text(highlevel.header_source([module]))

            # A typed Player, the generated adapter, and the mechanical body:
            # one translation unit each, compiled together.
            # A module's own header needs a guard like any other header: the
            # adapter unit and the implementation unit both include it.
            (hl / "player.h").write_text(
                "#pragma once\ntypedef struct { int health; } Player;\n")
            impl = (hl / "impl.c").read_text().replace(
                '#include "highlevel_impl.h"',
                '#include "highlevel_impl.h"\n#include "player.h"')
            (hl / "impl.c").write_text(impl)
            adapter = (hl / "recomp_highlevel_demo.c").read_text().replace(
                '#include "highlevel_impl.h"',
                '#include "highlevel_impl.h"\n#include "player.h"', 1)
            (hl / "recomp_highlevel_demo.c").write_text(adapter)
            # The declaration header the adapters include must see the types
            # the module's own sources use. The test's Player lives in
            # player.h, so the header includes it once.
            hdr = (hl / "highlevel_impl.h").read_text().replace(
                '#include "recomp_types.h"',
                '#include "recomp_types.h"\n#include "player.h"')
            (hl / "highlevel_impl.h").write_text(hdr)

            sources = [hl / "impl.c", hl / "recomp_highlevel_demo.c",
                       out / "recomp_dispatch.c"]
            sources += sorted(out.glob("recomp_[0-9]*.c"))
            proc = subprocess.run(
                ["cc", "-fsyntax-only", "-Wall", "-Wextra",
                 "-I", str(ROOT / "runtime" / "recomp32"),
                 "-I", str(ROOT / "runtime" / "win32hle"),
                 "-I", str(out), "-I", str(hl)] + [str(s) for s in sources],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0,
                             f"generated project does not compile:\n{proc.stderr}")


if __name__ == "__main__":
    unittest.main()
