"""The diagnostic sweep must report what the runtime printed, not a guess.

These tests pin the two things a fake result would hide: the parser only fires
on the runtime's real fatal line, and the generated entry point keeps strict
binding as the default while honouring RECOMP_PERMISSIVE=1.
"""

import json
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


class SignalTest(unittest.TestCase):
    def test_names_the_signal_a_process_died_from(self):
        self.assertEqual(diagnose.signal_name(-6), "SIGABRT")
        self.assertEqual(diagnose.signal_name(-10), "SIGBUS")
        self.assertIsNone(diagnose.signal_name(0))
        self.assertIsNone(diagnose.signal_name(2))
        self.assertIsNone(diagnose.signal_name(None))


class StrictOutputTest(unittest.TestCase):
    """The strict launch is the only one that reports real unresolved imports."""

    # Verbatim stderr from a strict run of the rebuilt tumiki-fighters binary.
    STRICT_STDERR = """\
state
[pe] unresolved import bulletml.dll!_BulletMLRunner_new_parser
[pe] unresolved import KERNEL32.DLL!SuspendThread
[pe] bound 59 imports, 86 unresolved
[host] tf.exe booted: entry 0x004187D0, 86 unresolved imports
"""

    def test_unresolved_names_and_counts_come_from_the_loader_lines(self):
        names = diagnose.UNRESOLVED_RE.findall(self.STRICT_STDERR)
        self.assertEqual(names, ["bulletml.dll!_BulletMLRunner_new_parser",
                                 "KERNEL32.DLL!SuspendThread"])
        self.assertEqual(diagnose.parse_bound_line(self.STRICT_STDERR),
                         {"iat_bound": 59, "iat_unresolved": 86})
        self.assertEqual(
            diagnose.parse_boot_line(self.STRICT_STDERR),
            {"entry": "0x004187D0", "unresolved_iat_imports": 86})

    def test_permissive_line_must_not_be_read_as_a_strict_count(self):
        # Under permissive boot the same binary prints 0 unresolved, because
        # every unresolved name got a stub. The parser reports what it sees;
        # the tool's job is to keep the two launches apart.
        permissive = ("[pe] bound 145 imports, 0 unresolved\n"
                      "[host] tf.exe booted: entry 0x004187D0, "
                      "0 unresolved imports\n")
        self.assertEqual(
            diagnose.parse_boot_line(permissive)["unresolved_iat_imports"], 0)
        self.assertEqual(diagnose.UNRESOLVED_RE.findall(permissive), [])


class TraceTest(unittest.TestCase):
    """RECOMP_TRACE lines are what names the shims a crashed guest reached."""

    TRACE_STDERR = """\
[pe] tf.exe mapped at 0x00400000, span 0x3D000
[host] tf.exe booted: entry 0x004187D0, 0 unresolved imports
[hle] VirtualAlloc
[hle] GetVersion
[hle] EnterCriticalSection
[hle] VirtualAlloc
[hle] LeaveCriticalSection
"""

    def test_collects_every_dispatch_line_not_just_the_first(self):
        names = diagnose.TRACE_RE.findall(self.TRACE_STDERR)
        self.assertEqual(names, ["VirtualAlloc", "GetVersion",
                                 "EnterCriticalSection", "VirtualAlloc",
                                 "LeaveCriticalSection"])

    def test_a_boot_with_no_dispatch_yields_nothing(self):
        self.assertEqual(
            diagnose.TRACE_RE.findall("[pe] mapped\n[host] booted\n"), [])


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


class RuntimeSyncTest(unittest.TestCase):
    """Every row must be measurable against one named runtime."""

    def test_copies_the_runtime_and_skips_selftests(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_win = root / "head" / "win32hle"
            src_rec = root / "head" / "recomp32"
            src_win.mkdir(parents=True)
            src_rec.mkdir(parents=True)
            (src_win / "host.c").write_text("/* head host */\n")
            (src_win / "sdl12.c").write_text("/* head sdl12 */\n")
            (src_win / "storage_selftest.c").write_text("/* not shipped */\n")
            (src_rec / "recomp_types.h").write_text("/* head types */\n")

            project = root / "projects" / "game"
            (project / "src" / "runtime" / "win32hle").mkdir(parents=True)
            (project / "src" / "runtime" / "recomp32").mkdir(parents=True)
            (project / "src" / "runtime" / "win32hle" / "host.c").write_text(
                "/* stale host */\n")

            info = diagnose.sync_runtime(project, root / "head")
            self.assertIsNotNone(info)
            self.assertEqual(info["files_copied"], 3)
            self.assertEqual(
                (project / "src" / "runtime" / "win32hle" / "host.c").read_text(),
                "/* head host */\n")
            self.assertFalse(
                (project / "src" / "runtime" / "win32hle"
                 / "storage_selftest.c").exists())
            self.assertTrue(info["host_c_sha256"])

    def test_missing_source_is_reported_not_guessed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "projects" / "game"
            (project / "src" / "runtime" / "win32hle").mkdir(parents=True)
            self.assertIsNone(
                diagnose.sync_runtime(project, root / "does-not-exist"))


class RunStatusTest(unittest.TestCase):
    """run_status is the pipeline-comparable classification of a row."""

    def test_timeout_means_it_looped(self):
        self.assertEqual(diagnose.run_status_of("timeout"), "timeout")

    def test_returning_on_its_own_is_exited_whatever_the_code(self):
        self.assertEqual(diagnose.run_status_of("exited-clean"), "exited")
        self.assertEqual(diagnose.run_status_of("exited-error"), "exited")

    def test_signal_is_a_crash(self):
        self.assertEqual(diagnose.run_status_of("crashed"), "crashed")

    def test_aborting_at_an_unimplemented_import_is_boot_failed(self):
        # Under strict binding the same binary fails the boot, so this is the
        # comparable status; permissive_outcome keeps the precise value.
        self.assertEqual(
            diagnose.run_status_of("reached-unimplemented-import"),
            "boot-failed")

    def test_unmeasured_rows_are_not_run(self):
        for outcome in ("no-binary", "launch-failed", "build-failed"):
            self.assertEqual(diagnose.run_status_of(outcome), "not-run")

    def test_an_unknown_outcome_is_not_silently_mapped(self):
        self.assertEqual(diagnose.run_status_of("something-new"), "unknown")


class TraceProfileTest(unittest.TestCase):
    """A dispatch profile is what separates a game loop from a spin."""

    TRACE = """\
[pe] mapped
[host] booted
[hle] GetTickCount
[hle] PeekMessageA
[hle] IDirectInputDevice::GetDeviceState
[hle] GetTickCount
[hle] PeekMessageA
[hle] Sleep
[hle] PeekMessageA
[hle] GetTickCount
"""

    def test_counts_each_import_in_order_of_calls(self):
        from collections import Counter
        counts = Counter(diagnose.TRACE_RE.findall(self.TRACE))
        self.assertEqual(dict(counts.most_common(2)),
                         {"PeekMessageA": 3, "GetTickCount": 3})
        self.assertEqual(counts["Sleep"], 1)
        self.assertEqual(sum(counts.values()), 8)

    def test_a_loop_profile_is_not_one_call_repeating(self):
        # The distinction the column exists for: a spin has one distinct
        # import, a frame loop has several.
        from collections import Counter
        counts = Counter(diagnose.TRACE_RE.findall(self.TRACE))
        self.assertGreater(len(counts), 3)
        spin = Counter(["Sleep"] * 50)
        self.assertEqual(len(spin), 1)


class BaselineCompareTest(unittest.TestCase):
    """Before/after is keyed by PE hash, not by project directory name."""

    BASELINE = {"source": "run.json", "captured_at": "2026-10-29T22:47:03Z",
                "by_pe_sha256": {"aa": {"project_dir": "generally",
                                        "unresolved_import_count": 179,
                                        "recorded_at": "2026-10-29T22:47:03Z"}}}

    def _row(self, name, sha, now):
        return {"project": name, "pe": f"/games/{name}.exe", "pe_sha256": sha,
                "outcome": "reached-unimplemented-import",
                "strict": {"unresolved_iat_imports": now}}

    def test_delta_is_computed_for_a_matching_executable(self):
        rows = [self._row("GeneRally", "aa", 7)]
        out = diagnose.compare_baseline(rows, self.BASELINE)
        self.assertEqual(out[0]["baseline_unresolved"], 179)
        self.assertEqual(out[0]["now_unresolved"], 7)
        self.assertEqual(out[0]["delta"], -172)

    def test_a_renamed_directory_still_matches_on_the_pe_hash(self):
        # The measured tree is named differently from the baseline's tree.
        rows = [self._row("generally", "aa", 0)]
        out = diagnose.compare_baseline(rows, self.BASELINE)
        self.assertEqual(out[0]["baseline_unresolved"], 179)

    def test_an_unknown_executable_reports_no_baseline_not_a_zero(self):
        rows = [self._row("other", "zz", 5)]
        out = diagnose.compare_baseline(rows, self.BASELINE)
        self.assertIsNone(out[0]["baseline_unresolved"])
        self.assertIsNone(out[0]["delta"])

    def test_no_baseline_yields_no_comparison(self):
        self.assertEqual(diagnose.compare_baseline([self._row("x", "aa", 1)], None), [])
        self.assertIsNone(diagnose.load_baseline(None))

    def test_zero_unresolved_is_marked_for_the_next_slice_decision(self):
        rows = [self._row("orgview", "aa", 0), self._row("other", "zz", 3)]
        out = {e["project"]: e for e in
               diagnose.compare_baseline(rows, self.BASELINE)}
        self.assertTrue(out["orgview"]["at_zero_unresolved"])
        self.assertFalse(out["other"]["at_zero_unresolved"])
        # A missing strict measurement is not a zero.
        rows = [{"project": "p", "pe": "/p.exe", "pe_sha256": "aa",
                 "outcome": "no-binary", "strict": None}]
        self.assertFalse(
            diagnose.compare_baseline(rows, self.BASELINE)[0]["at_zero_unresolved"])


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


class DiscoveryTest(unittest.TestCase):
    """One row per game: several project dirs can hold the same executable."""

    def _make(self, root, dirname, pe_path, sha, name, pe_type="PE32"):
        d = root / dirname
        d.mkdir(parents=True)
        (d / "project.json").write_text(json.dumps({
            "schema": 1, "name": name, "pe": {
                "path": pe_path, "pe_type": pe_type, "sha256": sha}}))
        return d

    def test_collapses_duplicate_dirs_to_the_corpus_recorded_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make(root, "wf", "/games/warning_forever/wf.exe", "aa", "wf")
            self._make(root, "warning-forever",
                       "/games/warning_forever/wf.exe", "aa", "wf")
            found, duplicates = diagnose.discover_projects(
                root, canonical={"aa": "warning-forever"})
            self.assertEqual([d.name for d in found], ["warning-forever"])
            self.assertEqual(len(duplicates), 1)
            self.assertEqual(duplicates[0]["also_present"], ["wf"])

    def test_falls_back_to_the_descriptive_dir_without_a_record(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make(root, "wf", "/games/warning_forever/wf.exe", "aa", "wf")
            self._make(root, "warning-forever",
                       "/games/warning_forever/wf.exe", "aa", "wf")
            found, _dup = diagnose.discover_projects(root, canonical={})
            self.assertEqual([d.name for d in found], ["warning-forever"])

    def test_prefers_a_sibling_that_is_actually_built(self):
        # The corpus record names one tree, but if that tree has no binary and
        # a duplicate does, the game is still measurable.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make(root, "wf", "/games/wf/wf.exe", "aa", "wf")
            self._make(root, "warning-forever", "/games/wf/wf.exe", "aa", "wf")
            build = root / "warning-forever" / "build"
            build.mkdir()
            (build / "wf").write_text("#!/bin/sh\n")
            found, duplicates = diagnose.discover_projects(
                root, canonical={"aa": "warning-forever"})
            self.assertEqual([d.name for d in found], ["warning-forever"])
            self.assertEqual(duplicates[0]["chosen_because"], "corpus-record")

            # Now the other way round: the recorded tree is the unbuilt one.
            (build / "wf").unlink()
            (root / "wf" / "build").mkdir()
            (root / "wf" / "build" / "wf").write_text("#!/bin/sh\n")
            found, duplicates = diagnose.discover_projects(
                root, canonical={"aa": "warning-forever"})
            self.assertEqual([d.name for d in found], ["wf"])
            self.assertIn("no binary", duplicates[0]["chosen_because"])

    def test_keeps_distinct_games_and_skips_non_pe32(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make(root, "wf", "/games/wf/wf.exe", "aa", "wf")
            self._make(root, "tf", "/games/tf/tf.exe", "bb", "tf")
            self._make(root, "cannonball", "/games/cb/cb.exe", "cc", "cb",
                       pe_type="PE32+")
            found, duplicates = diagnose.discover_projects(root, canonical={})
            self.assertEqual([d.name for d in found], ["tf", "wf"])
            self.assertEqual(duplicates, [])

    def test_explicit_only_bypasses_deduplication(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make(root, "wf", "/games/wf/wf.exe", "aa", "wf")
            self._make(root, "warning-forever", "/games/wf/wf.exe", "aa", "wf")
            found, duplicates = diagnose.discover_projects(
                root, only=["wf"], canonical={"aa": "warning-forever"})
            self.assertEqual([d.name for d in found], ["wf"])
            self.assertEqual(duplicates, [])

    def test_canonical_dirs_come_from_the_corpus_record(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.json"
            results.write_text(json.dumps({"schema": 1, "runs": [
                {"input_sha256": "aa",
                 "evidence": "projects/warning-forever/reports/report.html"},
                {"input_sha256": "bb", "evidence": "not-a-project-path"},
            ]}))
            self.assertEqual(diagnose.canonical_project_dirs(results),
                             {"aa": "warning-forever"})


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
                ["cc", "-fsyntax-only",
                 "-I", str(ROOT / "runtime" / "win32hle"),
                 "-I", str(ROOT / "runtime" / "recomp32"), str(c_path)],
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
