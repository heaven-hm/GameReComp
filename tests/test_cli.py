"""CLI surface: stage planning, target resolution and the progress renderer.

The pipeline itself is exercised elsewhere; these tests pin the contract the
CLI promises a user: an explicit stage plan, a target that is refused rather
than mislabeled, and progress output that works on a TTY and in a pipe.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.autorecomp import cli  # noqa: E402
from tools.autorecomp.pipeline import STAGES  # noqa: E402

FIXTURE_EXE = ROOT / "tests" / "fixtures" / "pe32-minimal.exe"


def _events():
    return [
        {"event": "stage-start", "stage": "inspect"},
        {"event": "stage-end", "stage": "inspect", "status": "ok",
         "seconds": 1.25, "message": "PE32, 97 imports"},
        {"event": "stage-start", "stage": "recover"},
        {"event": "stage-end", "stage": "recover", "status": "failed",
         "seconds": 2.5, "message": "no function boundaries"},
    ]


class StagePlanTests(unittest.TestCase):
    def test_out_mode_defaults_to_full_pipeline(self):
        args = cli.build_parser().parse_args([str(FIXTURE_EXE)])
        self.assertEqual(cli.OUT_MODE_STAGES["both"], list(STAGES))

    def test_source_mode_plan(self):
        args = cli.build_parser().parse_args(
            [str(FIXTURE_EXE), "--out-mode", "source"])
        plan = cli._resolve_stages(args, lambda msg: None)
        self.assertEqual(
            plan, ["inspect", "recover", "translate", "project"])

    def test_binary_mode_plan_includes_compile_not_run(self):
        args = cli.build_parser().parse_args(
            [str(FIXTURE_EXE), "--out-mode", "binary"])
        plan = cli._resolve_stages(args, lambda msg: None)
        self.assertIn("compile", plan)
        self.assertNotIn("run", plan)
        self.assertNotIn("verify", plan)

    def test_stages_flag_canonicalizes_order(self):
        args = cli.build_parser().parse_args(
            [str(FIXTURE_EXE), "--stages", "report,translate,inspect"])
        plan = cli._resolve_stages(args, lambda msg: None)
        self.assertEqual(plan, ["inspect", "translate", "report"])

    def test_unknown_stage_is_refused(self):
        args = cli.build_parser().parse_args(
            [str(FIXTURE_EXE), "--stages", "inspect,warp"])
        errors = []
        plan = cli._resolve_stages(args, errors.append)
        self.assertIsNone(plan)
        self.assertTrue(any("warp" in e for e in errors))

    def test_out_mode_and_stages_conflict(self):
        args = cli.build_parser().parse_args(
            [str(FIXTURE_EXE), "--out-mode", "source", "--stages", "inspect"])
        errors = []
        plan = cli._resolve_stages(args, errors.append)
        self.assertIsNone(plan)
        self.assertTrue(any("mutually exclusive" in e for e in errors))

    def test_skip_stages_removes_from_mode(self):
        args = cli.build_parser().parse_args(
            [str(FIXTURE_EXE), "--skip-stages", "verify"])
        plan = cli._resolve_stages(args, lambda msg: None)
        self.assertIn("compile", plan)
        self.assertNotIn("verify", plan)

    def test_report_cannot_be_skipped(self):
        args = cli.build_parser().parse_args(
            [str(FIXTURE_EXE), "--skip-stages", "report"])
        errors = []
        plan = cli._resolve_stages(args, errors.append)
        self.assertIsNone(plan)
        self.assertTrue(any("report" in e for e in errors))


class InputIdentityTests(unittest.TestCase):
    def test_auto_detects_the_fixture(self):
        identity, error = cli._resolve_input(FIXTURE_EXE, "auto", "auto")
        self.assertIsNone(error)
        self.assertEqual(identity, ("windows", "x86"))

    def test_explicit_match_is_accepted(self):
        identity, error = cli._resolve_input(FIXTURE_EXE, "windows", "x86")
        self.assertIsNone(error)
        self.assertEqual(identity, ("windows", "x86"))

    def test_wrong_declared_arch_is_refused(self):
        identity, error = cli._resolve_input(FIXTURE_EXE, "windows", "arm64")
        self.assertIsNone(identity)
        self.assertIn("header says x86", error)

    def test_wrong_declared_os_is_refused(self):
        identity, error = cli._resolve_input(FIXTURE_EXE, "linux", "auto")
        self.assertIsNone(identity)
        self.assertIn("header says windows", error)

    def test_not_a_pe_is_refused(self):
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"MZ" + b"\x00" * 64)            # MZ magic, e_lfanew = 0
            bad = f.name
        identity, error = cli._resolve_input(Path(bad), "auto", "auto")
        self.assertIsNone(identity)
        self.assertIn("PE signature", error)

    def test_out_mode_aliases_map_to_the_same_plan(self):
        for alias, canon in (("full", "both"), ("source-only", "source"),
                             ("binary-only", "binary")):
            self.assertEqual(cli.OUT_MODE_STAGES[alias],
                             cli.OUT_MODE_STAGES[canon],
                             f"{alias} != {canon}")


class TargetTests(unittest.TestCase):
    def test_host_resolves(self):
        with mock.patch.object(cli.platform, "system", return_value="Darwin"), \
                mock.patch.object(cli.platform, "machine",
                                  return_value="arm64"):
            target, error = cli._resolve_target("host", "host")
        self.assertIsNone(error)
        self.assertEqual(target, ("macos", "arm64"))

    def test_explicit_host_match_is_accepted(self):
        with mock.patch.object(cli.platform, "system", return_value="Linux"), \
                mock.patch.object(cli.platform, "machine",
                                  return_value="x86_64"):
            target, error = cli._resolve_target("linux", "x86_64")
        self.assertIsNone(error)
        self.assertEqual(target, ("linux", "x86_64"))

    def test_cross_target_is_refused(self):
        with mock.patch.object(cli.platform, "system", return_value="Linux"), \
                mock.patch.object(cli.platform, "machine",
                                  return_value="x86_64"):
            target, error = cli._resolve_target("windows", "x86_64")
        self.assertIsNone(target)
        self.assertIn("cross-target", error)

    def test_same_os_other_arch_is_refused(self):
        with mock.patch.object(cli.platform, "system", return_value="Linux"), \
                mock.patch.object(cli.platform, "machine",
                                  return_value="x86_64"):
            target, error = cli._resolve_target("linux", "arm64")
        self.assertIsNone(target)
        self.assertIn("cross-target", error)

    def test_unknown_host_is_refused(self):
        with mock.patch.object(cli.platform, "system", return_value="Plan9"), \
                mock.patch.object(cli.platform, "machine",
                                  return_value="mips"):
            target, error = cli._resolve_target("host", "host")
        self.assertIsNone(target)
        self.assertIn("unsupported build host", error)


class ProgressTests(unittest.TestCase):
    def test_plain_mode_matches_traditional_lines(self):
        stream = io.StringIO()
        progress = cli._StageProgress(STAGES, stream=stream, bar=False)
        for evt in _events():
            progress(evt)
        text = stream.getvalue()
        self.assertIn("==> inspect\n", text)
        self.assertIn("==> recover\n", text)
        self.assertIn("ok", text)
        self.assertIn("failed", text)
        self.assertTrue(text.startswith("==> "))

    def test_stage_end_record_keyed_by_name_not_stage(self):
        # run_pipeline emits stage-end as {"name": ...}; the renderer must
        # accept both key spellings or stage lines silently vanish (the old
        # CLI's KeyError was swallowed by _emit).
        stream = io.StringIO()
        progress = cli._StageProgress(STAGES, stream=stream, bar=False)
        progress({"event": "stage-start", "stage": "inspect"})
        progress({"event": "stage-end", "name": "inspect", "status": "ok",
                  "seconds": 1.0, "message": "fine"})
        self.assertIn("inspect: fine", stream.getvalue())

    def test_bar_mode_counts_weights_and_reports_failure(self):
        stream = io.StringIO()
        progress = cli._StageProgress(STAGES, stream=stream, bar=True,
                                      tick=False)
        for evt in _events():
            progress(evt)
        text = stream.getvalue()
        # inspect ok -> weight 10 of 100; recover failed -> another 10
        self.assertIn(" 20%", text)
        self.assertIn("FAIL", text)
        self.assertIn("recover", text)
        # every redraw is one line, closed by the final newline
        self.assertTrue(text.endswith("\n"))

    def test_bar_mode_plans_subset_only(self):
        stream = io.StringIO()
        stages = ["inspect", "recover", "translate", "project", "report"]
        progress = cli._StageProgress(stages, stream=stream, bar=True,
                                      tick=False)
        for evt in _events():
            progress(evt)
        self.assertIn(" 20%", stream.getvalue())

    def test_skipped_stage_neutral_percent(self):
        stream = io.StringIO()
        progress = cli._StageProgress(STAGES, stream=stream, bar=True,
                                      tick=False)
        progress({"event": "stage-start", "stage": "inspect"})
        progress({"event": "stage-end", "stage": "inspect", "status": "skipped",
                  "seconds": 0.0, "message": "skipped"})
        self.assertIn("skipped", stream.getvalue())
        self.assertIn("  0%", stream.getvalue())

    def test_ticker_stops_before_exit_returns(self):
        # A real thread is started only when a bar is drawn with tick=True;
        # stopping it must be prompt so main() can exit immediately.
        stream = io.StringIO()
        progress = cli._StageProgress(STAGES, stream=stream, bar=True, tick=True)
        progress({"event": "stage-start", "stage": "inspect"})
        self.assertIsNotNone(progress._ticker)
        progress({"event": "stage-end", "stage": "inspect", "status": "ok",
                  "seconds": 0.0, "message": ""})
        self.assertIsNone(progress._ticker)


class DryRunTests(unittest.TestCase):
    def test_dry_run_prints_plan_and_builds_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td) / "proj"
            rc = cli.main([
                str(FIXTURE_EXE), "--dry-run",
                "--project-dir", str(project_dir),
                "--out-mode", "source",
            ])
            self.assertEqual(rc, 0)
            self.assertFalse(project_dir.exists())
            self.assertFalse((ROOT / "projects" / FIXTURE_EXE.stem).exists())

    def test_dry_run_refuses_cross_target(self):
        with mock.patch.object(cli.platform, "system", return_value="Darwin"), \
                mock.patch.object(cli.platform, "machine",
                                  return_value="arm64"):
            rc = cli.main([str(FIXTURE_EXE), "--dry-run",
                           "--target-os", "windows"])
        self.assertEqual(rc, cli.EXIT_UNSUPPORTED)

    def test_list_stages(self):
        stream = io.StringIO()
        with mock.patch.object(sys, "stdout", stream):
            rc = cli.main(["--list-stages"])
        self.assertEqual(rc, 0)
        self.assertIn("inspect", stream.getvalue())
        self.assertIn("report", stream.getvalue())

    def test_missing_input_is_exit_stage_failed(self):
        rc = cli.main(["/nonexistent/game.exe", "--dry-run"])
        self.assertEqual(rc, cli.EXIT_STAGE_FAILED)


class IdentityDefaultTests(unittest.TestCase):
    """Omitted input/output identity is assumed, and said so.

    The four knobs are independently defaultable: input OS/arch from the PE
    header, output OS/arch from this host. That is what lets the common run
    be a single argument, but the result is an assumption, so it is warned
    about once, naming every knob the caller left out.
    """

    def test_omitted_identity_warns_and_defaults(self):
        args = cli.build_parser().parse_args([str(FIXTURE_EXE)])
        stream = io.StringIO()
        cli._identity_defaults(args, stream)
        out = stream.getvalue()
        self.assertIn("not fully specified", out)
        self.assertIn("--input-os", out)
        self.assertIn("--input-arch", out)
        self.assertIn("--target-os", out)
        self.assertIn("--target-arch", out)
        self.assertEqual(args.input_os, "auto")
        self.assertEqual(args.input_arch, "auto")
        self.assertEqual(args.target_os, "host")
        self.assertEqual(args.target_arch, "host")

    def test_explicit_identity_does_not_warn(self):
        args = cli.build_parser().parse_args([
            str(FIXTURE_EXE), "--input-os", "windows", "--input-arch", "x86",
            "--target-os", "macos", "--target-arch", "arm64"])
        stream = io.StringIO()
        cli._identity_defaults(args, stream)
        self.assertEqual(stream.getvalue(), "")
        self.assertEqual(args.input_os, "windows")
        self.assertEqual(args.target_arch, "arm64")

    def test_partial_identity_warns_about_the_rest(self):
        args = cli.build_parser().parse_args([
            str(FIXTURE_EXE), "--input-os", "windows"])
        stream = io.StringIO()
        cli._identity_defaults(args, stream)
        out = stream.getvalue()
        self.assertNotIn("omitted: --input-os", out)
        self.assertIn("omitted: --input-arch", out)
        self.assertIn("omitted: --target-os", out)
        self.assertIn("omitted: --target-arch", out)

    def test_identity_aliases_parse(self):
        args = cli.build_parser().parse_args([
            str(FIXTURE_EXE), "--in-os", "windows", "--in-arch", "x86",
            "--out-os", "macos", "--out-arch", "arm64"])
        self.assertEqual(args.input_os, "windows")
        self.assertEqual(args.input_arch, "x86")
        self.assertEqual(args.target_os, "macos")
        self.assertEqual(args.target_arch, "arm64")


if __name__ == "__main__":
    unittest.main()
