"""Generator source-unit chunking: a function count is not a size bound.

GeneRally's first 1,000 functions produced a 273 MB translation unit because
individual lifted bodies reach 2.25 MB. These tests pin the byte cap and the
units manifest the pipeline uses for per-function provenance.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("pe", "disasm", "lift"):
    sys.path.insert(0, str(ROOT / "tools" / sub))

import translator  # noqa: E402


class _Func:
    def __init__(self, address):
        self.address = address
        self.name = f"sub_{address:08X}"


class _Lifter:
    def __init__(self, iat_map=None, func_names=None):
        pass

    def lift_function(self, func):
        return (f"void {func.name}(void) {{\n"
                f"  /* {'x' * 1200} */\n"
                f"}}\n")


class GeneratorChunkingTest(unittest.TestCase):
    def setUp(self):
        self._original = translator.Lifter
        translator.Lifter = _Lifter

    def tearDown(self):
        translator.Lifter = self._original

    def _generate(self, count, split_size, max_unit_bytes):
        funcs = {0x401000 + i * 0x10: _Func(0x401000 + i * 0x10)
                 for i in range(count)}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        names = translator.generate_recomp_files(
            funcs, {}, tmp.name, split_size=split_size,
            max_unit_bytes=max_unit_bytes)
        out = Path(tmp.name)
        return names, out, sorted(p for p in out.glob("recomp_[0-9]*.c"))

    def test_units_are_capped_by_bytes(self):
        names, out, units = self._generate(80, split_size=1000,
                                           max_unit_bytes=10_000)
        self.assertGreater(len(units), 1)
        self.assertEqual(len(names), 80)
        for unit in units:
            self.assertLessEqual(unit.stat().st_size, 10_000 + 2_000)
        text = "".join(unit.read_text() for unit in units)
        self.assertEqual(text.count("void sub_"), 80)

        dispatch = (out / "recomp_dispatch.c").read_text()
        self.assertIn("recomp_dispatch_count = 80", dispatch)

        manifest = json.loads((out / "recomp_units.json").read_text())
        self.assertEqual(len(manifest["units"]), len(units))
        self.assertEqual(sum(u["functions"] for u in manifest["units"]), 80)
        self.assertEqual(manifest["units"][0]["first_function_index"], 0)
        self.assertEqual(manifest["units"][-1]["last_function_index"], 79)

    def test_function_count_cap_still_applies(self):
        _names, _out, units = self._generate(12, split_size=5,
                                             max_unit_bytes=1 << 30)
        self.assertEqual(len(units), 3)


if __name__ == "__main__":
    unittest.main()
