"""Issue #26: measurable, semantics-preserving generated-source size control.

The cap is soft: a single lifted body that exceeds it is emitted whole and
reported, never truncated. Every byte figure is observed from the files the
generator actually wrote (os.path.getsize), never estimated from instruction
counts or function counts.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("pe", "disasm", "lift"):
    sys.path.insert(0, str(ROOT / "tools" / sub))

import translator  # noqa: E402
from tools.autorecomp import projectgen  # noqa: E402


class _Func:
    def __init__(self, address):
        self.address = address
        self.name = f"sub_{address:08X}"


def _fixed_lifter(body_bytes):
    """A Lifter stand-in whose bodies have a known, constant byte length."""
    def build(**_kwargs):
        class Lifter:
            def lift_function(self, func, **_kwargs):
                return (f"void {func.name}(void) {{\n"
                        f"  /* {'x' * body_bytes} */\n"
                        f"}}\n")
        return Lifter()
    return build


class SourceBudgetTest(unittest.TestCase):
    def setUp(self):
        self._orig_lifter = translator.Lifter
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        translator.Lifter = self._orig_lifter

    def _generate(self, count, body_bytes, cap, subdir="gen"):
        translator.Lifter = _fixed_lifter(body_bytes)
        funcs = {0x401000 + i * 0x10: _Func(0x401000 + i * 0x10)
                 for i in range(count)}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            names = translator.generate_recomp_files(
                funcs, {}, str(self.root / subdir),
                split_size=1000, max_unit_bytes=cap)
        return names, buf.getvalue()

    def test_budget_is_measured_from_emitted_bytes(self):
        budget = translator.build_source_budget([
            {"file": "recomp_0000.c", "bytes": 100, "functions": 2},
            {"file": "recomp_0001.c", "bytes": 250, "functions": 1},
        ], 200)
        self.assertEqual(budget["unit_count"], 2)
        self.assertEqual(budget["cap_bytes"], 200)
        self.assertEqual(budget["total_bytes"], 350)
        self.assertEqual(budget["largest_unit_bytes"], 250)
        self.assertEqual(budget["largest_unit_file"], "recomp_0001.c")
        self.assertEqual(budget["over_cap_count"], 1)
        over = budget["over_cap_units"][0]
        self.assertEqual(over["file"], "recomp_0001.c")
        self.assertEqual(over["bytes"], 250)
        self.assertEqual(over["over_by_bytes"], 50)
        self.assertTrue(over["single_oversized_function"])
        self.assertEqual(budget["measured_from"], "os.path.getsize(recomp_*.c)")

    def test_manifest_and_report_carry_per_unit_bytes(self):
        self._generate(20, 1200, 10_000)
        gen = self.root / "gen"
        manifest = json.loads((gen / "recomp_units.json").read_text())
        units = manifest["units"]
        budget = manifest["budget"]
        total_on_disk = sum(u["bytes"] for u in units)
        self.assertEqual(budget["total_bytes"], total_on_disk)
        self.assertEqual(budget["unit_count"], len(units))
        for unit in units:
            self.assertEqual(unit["bytes"],
                             (gen / unit["file"]).stat().st_size)
        report = (gen / "source_budget.txt").read_text()
        for unit in units:
            self.assertIn(unit["file"], report)
            self.assertIn(str(unit["bytes"]), report)
        self.assertIn("soft cap", report)

    def test_over_cap_emitted_whole_with_explicit_diagnostic(self):
        # One 50 KB body against a 4 KB cap: the cap cannot be honoured at a
        # function boundary, so the function is emitted in full and reported.
        names, log = self._generate(1, 50_000, 4_000)
        self.assertEqual(len(names), 1)
        unit = (self.root / "gen" / "recomp_0000.c").read_text()
        self.assertIn("x" * 50_000, unit)
        self.assertIn("void sub_00401000(void)", unit)

        self.assertIn("Source budget:", log)
        self.assertIn("[!] source budget: recomp_0000.c", log)
        self.assertIn("no truncation", log)
        manifest = json.loads((self.root / "gen" / "recomp_units.json").read_text())
        self.assertEqual(manifest["budget"]["over_cap_count"], 1)
        self.assertTrue(manifest["budget"]["over_cap_units"][0]
                        ["single_oversized_function"])

    def test_cap_accounts_for_repeated_prologue(self):
        prologue = translator.unit_prologue(0, 0, 0x401000, 0x401000)
        one_function = (f"void sub_00401000(void) {{\n"
                        f"  /* {'x' * 100} */\n"
                        f"}}\n")
        # Just below one body plus the prologue: a cap measured on bodies
        # alone would let two bodies in and produce half as many units.
        cap = translator._utf8_bytes(prologue) + translator._utf8_bytes(one_function) + 2 - 1
        self._generate(6, 100, cap)
        units = sorted((self.root / "gen").glob("recomp_[0-9]*.c"))
        self.assertEqual(len(units), 6)
        manifest = json.loads((self.root / "gen" / "recomp_units.json").read_text())
        for unit in manifest["units"]:
            self.assertEqual(unit["functions"], 1)
        # Soft cap: the single function that crosses it is emitted whole.
        for unit in units:
            self.assertLessEqual(unit.stat().st_size,
                                 cap + translator._utf8_bytes(one_function) + 4)

    def test_env_cap_is_configurable_and_invalid_values_ignored(self):
        env = translator.SOURCE_UNIT_BYTES_ENV
        os.environ[env] = "600"
        try:
            self._generate(10, 200, None, subdir="envgen")
        finally:
            os.environ.pop(env, None)
        env_units = sorted((self.root / "envgen").glob("recomp_[0-9]*.c"))
        self.assertGreater(len(env_units), 1)

        os.environ[env] = "not-a-number"
        try:
            _names, bad_log = self._generate(10, 200, None, subdir="badgen")
        finally:
            os.environ.pop(env, None)
        self.assertIn(f"ignoring invalid {env}", bad_log)
        bad_units = sorted((self.root / "badgen").glob("recomp_[0-9]*.c"))
        self.assertEqual(len(bad_units), 1)  # fell back to the 8 MiB default

    def test_projectgen_publishes_measured_budget(self):
        proj = self.root / "project"
        gen = proj / "src" / "recomp" / "gen"
        gen.mkdir(parents=True)
        units = [{
            "file": "recomp_0000.c", "bytes": 4096, "functions": 3,
            "first_function_index": 0, "last_function_index": 2,
            "first_address": "0x00401000", "last_address": "0x00401020",
        }]
        (gen / "recomp_units.json").write_text(json.dumps({
            "schema": 1, "units": units,
            "budget": translator.build_source_budget(units, 8192),
        }))
        record = projectgen.write_project(proj, "game", "GAME.EXE", {})
        self.assertEqual(record["source_budget"]["total_bytes"], 4096)
        self.assertEqual(record["source_budget"]["cap_bytes"], 8192)
        on_disk = json.loads((proj / "project.json").read_text())
        self.assertEqual(on_disk["source_budget"]["total_bytes"], 4096)
        report = (proj / "reports" / "source_budget.txt").read_text()
        self.assertIn("recomp_0000.c", report)
        self.assertIn("4096", report)

    def test_projectgen_without_manifest_is_safe(self):
        proj = self.root / "bare"
        record = projectgen.write_project(proj, "bare", "BARE.EXE", {})
        self.assertNotIn("source_budget", record)
        self.assertFalse((proj / "reports" / "source_budget.txt").exists())


if __name__ == "__main__":
    unittest.main()
