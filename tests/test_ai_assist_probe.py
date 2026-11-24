"""Tests for the deterministic, review-only win32hle probe.

Every fixture here is synthetic: the tests exercise the heuristics themselves,
not the correctness of the shipped runtime. A finding is a lead for a human,
so the tests check that the lead is emitted with a file, a line, and a reason --
and that clean/guarded code and cdecl shims are not reported.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.ai_assist_probe import (  # noqa: E402
    build_report,
    main,
    mask_c,
    render,
    scan_path,
    shim_name_entries,
)


class AiAssistProbeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def findings(self, include_selftests=False):
        return scan_path(self.dir, self.dir, include_selftests=include_selftests)

    def rules(self, findings=None):
        return {f.rule for f in (self.findings() if findings is None else findings)}

    def test_mask_c_blanks_comments_and_strings_without_shifting_lines(self):
        text = '// "fake { name"\nchar *s = "}"; /* { } */\nvoid real(void) {}\n'
        masked = mask_c(text)
        self.assertEqual(masked.count("\n"), text.count("\n"))
        self.assertNotIn('"', masked)
        self.assertIn("void real(void) {}", masked)
        # The brace in the comment/string cannot survive masking.
        self.assertNotIn("{ name", masked)

    def test_clean_guarded_shim_is_not_reported(self):
        self.write("clean.c", """
#include "win32hle.h"
static void k_Clean(void) {
    char *s = ASTR(0);
    if (!s) RETV(1);
    RET((uint32_t)strlen(s), 1);
}
const win32hle_shim win32hle_clean[] = {
    { "Clean", k_Clean },
    { 0, 0 }
};
""")
        self.assertEqual(self.findings(), [])

    def test_ret_arity_too_small_points_at_the_ret_line(self):
        text = """
static void k_BadArity(void) {
    uint32_t a = A32(0);
    uint32_t b = A32(1);
    RET(a + b, 1);
}
"""
        self.write("arity.c", text)
        finds = self.findings()
        self.assertIn("ret-arity-too-small", self.rules(finds))
        lead = next(f for f in finds if f.rule == "ret-arity-too-small")
        expected_line = next(
            i for i, line in enumerate(text.splitlines(), 1) if "RET(a + b, 1)" in line
        )
        self.assertEqual(lead.line, expected_line)
        self.assertEqual(lead.file, "arity.c")
        self.assertIn("k_BadArity", lead.reason)
        self.assertIn("argc=1", lead.reason)

    def test_inconsistent_ret_arity_is_reported(self):
        self.write("arity.c", """
static void k_Inconsistent(void) {
    if (A32(0)) RET(1, 1);
    RET(0, 2);
}
""")
        self.assertIn("inconsistent-ret-arity", self.rules())

    def test_cdecl_shim_is_exempt_from_stdcall_arity_rules(self):
        self.write("cdecl.c", """
static void sdl_Cdecl(void) {
    A32(0);
    A32(1);
    RETC(7);
}
""")
        self.assertNotIn("ret-arity-too-small", self.rules())
        self.assertNotIn("inconsistent-ret-arity", self.rules())

    def test_missing_ret_on_a_registered_shim_is_reported(self):
        self.write("half.c", """
static void k_Half(void) {
    uint32_t x = A32(0);
    g_eax = x;
}
const win32hle_shim win32hle_half[] = {
    { "Half", k_Half },
    { 0, 0 }
};
""")
        finds = self.findings()
        self.assertIn("missing-ret", self.rules(finds))
        self.assertIn("k_Half", next(f.reason for f in finds if f.rule == "missing-ret"))

    def test_unchecked_guest_pointer_is_reported_and_guard_suppresses_it(self):
        self.write("ptr.c", """
static void k_Unsafe(void) {
    char *d = ASTR(0);
    char *s = ASTR(1);
    strcpy(d, s);
    RETP(d, 2);
}
static void k_Safe(void) {
    char *d = ASTR(0);
    char *s = ASTR(1);
    if (!d || !s) RETV(2);
    strcpy(d, s);
    RETV(2);
}
""")
        finds = self.findings()
        self.assertIn("unchecked-guest-pointer", self.rules(finds))
        for lead in finds:
            if lead.rule != "unchecked-guest-pointer":
                continue
            self.assertEqual(lead.file, "ptr.c")
            self.assertGreater(lead.line, 0)
            self.assertIn("k_Unsafe", lead.reason)
            self.assertNotIn("k_Safe", lead.reason)

    def test_direct_guest_pointer_dereference_is_reported(self):
        self.write("direct.c", """
static void k_Direct(void) {
    RET(MEM32(APTR(0)), 1);
}
""")
        self.assertIn("unchecked-guest-pointer", self.rules())

    def test_duplicate_shim_name_across_files_is_reported_at_both_sites(self):
        self.write("a.c", """
static void k_A(void) { RETV(1); }
const win32hle_shim a[] = { { "CreateWidget", k_A }, { 0, 0 } };
""")
        self.write("b.c", """
static void k_B(void) { RETV(1); }
const win32hle_shim b[] = { { "CreateWidget", k_B }, { 0, 0 } };
""")
        dupes = [f for f in self.findings() if f.rule == "duplicate-shim-name"]
        self.assertEqual({f.file for f in dupes}, {"a.c", "b.c"})
        self.assertTrue(all("CreateWidget" in f.reason for f in dupes))
        self.assertTrue(all("also:" in f.reason for f in dupes))

    def test_literal_shim_name_extraction_ignores_non_table_strings(self):
        entries, fns = shim_name_entries(
            'fprintf(stderr, "not a shim");\n'
            'const win32hle_shim t[] = { { "RealA", k_RealA }, { 0, 0 } };\n'
        )
        self.assertEqual([name for name, _ in entries], ["RealA"])
        self.assertEqual(entries[0][1], 2)
        self.assertEqual(fns, {"k_RealA"})

    def test_selftests_are_excluded_by_default(self):
        self.write("probe_selftest.c", """
static void k_X(void) { RETV(1); }
const win32hle_shim x[] = { { "SelftestOnly", k_X }, { 0, 0 } };
""")
        self.write("y_selftest.c", """
static void k_Y(void) { RETV(1); }
const win32hle_shim y[] = { { "SelftestOnly", k_Y }, { 0, 0 } };
""")
        self.assertEqual(self.findings(), [])
        self.assertIn("duplicate-shim-name", self.rules(self.findings(True)))

    def test_report_is_deterministic_and_serialisable(self):
        self.write("x.c", """
static void k_X(void) { RET(A32(0), 0); }
const win32hle_shim x[] = { { "X", k_X }, { 0, 0 } };
""")
        first = render(build_report(self.dir, self.dir))
        second = render(build_report(self.dir, self.dir))
        self.assertEqual(first, second)
        report = json.loads(first)
        self.assertEqual(report["schema"], 1)
        self.assertEqual(report["tool"], "ai_assist_probe")
        self.assertEqual(report["files_scanned"], 1)
        self.assertEqual(report["summary"]["findings"], len(report["findings"]))
        self.assertIn("not proof", report["disclaimer"])
        self.assertIn("ret-arity-too-small", report["summary"]["by_rule"])
        for finding in report["findings"]:
            self.assertEqual(set(finding), {"file", "line", "rule", "reason"})
            self.assertEqual(finding["file"], "x.c")
            self.assertIsInstance(finding["line"], int)
            self.assertTrue(finding["reason"])
        self.assertNotIn(str(self.dir), first)

    def test_cli_writes_json_to_out(self):
        self.write("x.c", """
static void k_X(void) { RET(A32(0), 0); }
const win32hle_shim x[] = { { "X", k_X }, { 0, 0 } };
""")
        out = self.dir / "probe.json"
        self.assertEqual(
            main(["--source", str(self.dir), "--repo-root", str(self.dir), "--out", str(out)]), 0
        )
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertGreater(report["summary"]["findings"], 0)
        self.assertEqual(out.read_text(encoding="utf-8"), render(report))


if __name__ == "__main__":
    unittest.main()
