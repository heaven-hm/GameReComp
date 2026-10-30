"""The diagnostic sweep must report what the runtime printed, not a guess.

These tests pin the two things a fake result would hide: the parser only fires
on the runtime's real fatal line, and the generated entry point keeps strict
binding as the default while honouring RECOMP_PERMISSIVE=1.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.autorecomp import diagnose, projectgen  # noqa: E402

# Verbatim stderr from a permissive run of projects/warning-forever (relinked
# from the committed build objects), 2026-10-30.
REAL_STDERR = """\
[pe] wf.exe mapped at 0x00400000, span 0x6C000
[pe] bound 147 imports, 0 unresolved
[host] wf.exe booted: entry 0x00433569, 0 unresolved imports
[module] GetProcAddress(kernel32.dll, InitializeCriticalSectionAndSpinCount): not answered
[win32hle] FATAL: unimplemented import DirectDrawEnumerateExA (0x7F002BC0) called from guest 0x004219D2
"""


class ParseFirstImportTest(unittest.TestCase):
    def test_parses_the_runtime_fatal_line(self):
        hit = diagnose.parse_first_import(REAL_STDERR)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["import"], "DirectDrawEnumerateExA")
        self.assertEqual(hit["stub_va"], "0x7F002BC0")
        self.assertEqual(hit["caller"], "0x004219D2")

    def test_no_import_is_not_invented_from_other_output(self):
        # A successful boot with no stub call must yield nothing, however
        # suggestive the rest of the log is.
        text = ("[pe] dxIka.exe mapped at 0x00400000\n"
                "[pe] bound 97 imports, 0 unresolved\n"
                "[host] dxIka.exe booted: entry 0x0040BA76, 0 unresolved imports\n"
                "[win32hle] ExitProcess(1)\n")
        self.assertIsNone(diagnose.parse_first_import(text))
        self.assertIsNone(diagnose.parse_first_import(""))
        self.assertIsNone(diagnose.parse_first_import(None))

    def test_takes_the_first_import_when_several_appear(self):
        text = (REAL_STDERR +
                "[win32hle] FATAL: unimplemented import Later (0x1) "
                "called from guest 0x2\n")
        self.assertEqual(diagnose.parse_first_import(text)["import"],
                         "DirectDrawEnumerateExA")

    def test_parses_boot_and_bind_lines(self):
        self.assertEqual(diagnose.parse_boot_line(REAL_STDERR),
                         {"entry": "0x00433569", "unresolved_iat_imports": 0})
        self.assertEqual(diagnose.parse_bound_line(REAL_STDERR),
                         {"iat_bound": 147, "iat_unresolved": 0})
        self.assertIsNone(diagnose.parse_boot_line("nothing here"))


class ClassifyTest(unittest.TestCase):
    def test_timeout_wins_over_an_earlier_import(self):
        # The process was still running at the deadline: that is the outcome,
        # even if an import was named earlier.
        self.assertEqual(
            diagnose.classify(None, True, {"import": "X"}), "timeout")

    def test_reached_import(self):
        self.assertEqual(
            diagnose.classify(-6, False, {"import": "X"}),
            "reached-unimplemented-import")

    def test_clean_exit_without_import(self):
        self.assertEqual(diagnose.classify(1, False, None), "exited-error")
        self.assertEqual(diagnose.classify(0, False, None), "exited-clean")

    def test_signal_is_a_crash(self):
        self.assertEqual(diagnose.classify(-11, False, None), "crashed")


class RankImportsTest(unittest.TestCase):
    def test_ranks_by_games_blocked_then_name(self):
        rows = [
            {"project": "a", "first_import": "Zed"},
            {"project": "b", "first_import": "Alpha"},
            {"project": "c", "first_import": "Zed"},
            {"project": "d", "first_import": None},
        ]
        ranked = diagnose.rank_imports(rows)
        self.assertEqual(ranked[0]["import"], "Zed")
        self.assertEqual(ranked[0]["games_blocked"], 2)
        self.assertEqual(ranked[0]["games"], ["a", "c"])
        self.assertEqual(ranked[1]["import"], "Alpha")
        self.assertEqual(len(ranked), 2)  # a game without an import adds no row


class MarkdownTest(unittest.TestCase):
    def test_reports_unlaunchable_games_with_their_raw_error(self):
        rows = [{
            "project": "broken", "name": None, "pe": None, "binary": None,
            "binary_exists": False, "outcome": "no-binary", "first_import": None,
            "stub_va": None, "caller": None, "exit_code": None,
            "timed_out": False, "duration_seconds": None, "boot_line": None,
            "iat_bind": None, "raw_error": "rebuilt binary not found at /x",
            "stderr_tail": "",
        }]
        report = diagnose.build_report(rows, "2026-10-30T00:00:00Z",
                                       "diagnose.py --timeout 15", 15.0)
        md = diagnose.render_markdown(report)
        self.assertIn("rebuilt binary not found at /x", md)
        self.assertIn("No project reached an unimplemented import", md)


class MainTemplateTest(unittest.TestCase):
    """The generated entry point must keep strict boot the default."""

    def _compile(self, source):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            c_path = Path(tmp) / "main.c"
            c_path.write_text(source)
            return subprocess.run(
                ["cc", "-fsyntax-only", "-I",
                 str(ROOT / "runtime" / "win32hle"), str(c_path)],
                capture_output=True, text=True).returncode

    def test_template_calls_permissive_only_behind_the_env_var(self):
        self.assertIn("recomp_host_main(argc, argv)", projectgen.MAIN_C)
        self.assertIn("recomp_host_boot_permissive(argv[1])", projectgen.MAIN_C)
        self.assertIn("RECOMP_PERMISSIVE", projectgen.MAIN_C)
        # The strict call must be the one on the no-env-var path.
        strict_at = projectgen.MAIN_C.index("if (!permissive_requested())")
        permissive_at = projectgen.MAIN_C.index(
            "recomp_host_boot_permissive(argv[1])")
        self.assertLess(strict_at, permissive_at)

    def test_template_compiles(self):
        self.assertEqual(self._compile(projectgen.MAIN_C), 0,
                         "MAIN_C template does not compile against host.h")


if __name__ == "__main__":
    unittest.main()
