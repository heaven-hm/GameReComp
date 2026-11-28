"""Tests for the fail-closed corpus acceptance harness.

The harness orchestrates other tools; these tests exercise its own glue and
status ladder, plus one real launch of a tiny native stand-in that writes a
BMP frame through the same RECOMP_SCREENSHOT path a rebuilt game uses.
"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from tools import corpus_acceptance as ca

FIXTURE = Path(__file__).parent / "fixtures" / "pe32-minimal.exe"


def assert_no_leaked_home_paths(text, allowed_input):
    """No home-directory paths except the input the test explicitly supplied.

    These tests pass a fixture from the checkout as `--original`, so the
    checkout's own path is expected in the report. What must not leak is any
    *other* path under the home directory (the old assertion forbade even the
    supplied fixture and failed whenever the checkout lived under $HOME).
    """
    allowed = str(Path(allowed_input).resolve())
    residual = text.replace(allowed, "")
    home = str(Path.home())
    assert home not in residual, f"unexpected {home} path in report"

FAKE_NATIVE = r'''#!/usr/bin/env python3
import os, struct
from pathlib import Path
d = Path(os.environ["RECOMP_SCREENSHOT"])
d.mkdir(parents=True, exist_ok=True)
w = h = 2
stride = ((w * 3 + 3) // 4) * 4
data = bytearray(54 + stride * h)
data[:2] = b"BM"
struct.pack_into("<I", data, 10, 54)
struct.pack_into("<I", data, 14, 40)
struct.pack_into("<ii", data, 18, w, h)
struct.pack_into("<H", data, 26, 1)
struct.pack_into("<H", data, 28, 24)
pixels = [b"\x01\x02\x03", b"\x04\x05\x06", b"\x07\x08\x09", b"\x04\x05\x06"]
for y in range(h):
    for x in range(w):
        off = 54 + y * stride + x * 3
        data[off:off + 3] = pixels[y * w + x]
(d / "frame-0000.bmp").write_bytes(data)
'''

BLANK_NATIVE = FAKE_NATIVE.replace(
    'pixels = [b"\\x01\\x02\\x03", b"\\x04\\x05\\x06", '
    'b"\\x07\\x08\\x09", b"\\x04\\x05\\x06"]',
    'pixels = [b"\\x01\\x02\\x03"] * 4')


def ok_original():
    return {"status": "ok", "sha256": "a" * 64, "architecture": "i386",
            "path": "/tmp/Game.EXE", "pe_type": "PE32"}


def ok_native(sha="b" * 64):
    return {"path": "/tmp/native", "sha256": sha, "size": 1234,
            "source": "cli", "reason": ""}


def ok_pipeline(native_sha="b" * 64, original_sha="a" * 64,
                verification="scenario-pass", stage_failed=None):
    return {
        "status": "ok" if not stage_failed else "STAGE_FAILED",
        "stage_failed": stage_failed,
        "claims_verified": (
            verification in ca.PASS_VERIFICATION_STATUSES and not stage_failed
            and native_sha == "b" * 64 and original_sha == "a" * 64),
        "verification_status": verification,
        "native_sha256": native_sha,
        "input_sha256": original_sha,
        "reasons": [f"pipeline verification {verification!r}"],
        "report": "/tmp/report.json", "score": 100,
    }


def ok_launch(exit_code=0):
    return {"executed": True, "exit_code": exit_code, "seconds": 1.2,
            "reason": "" if exit_code in ca.NONFAILING_EXITS else "crashed"}


def ok_frames():
    return {
        "runner_frame_count": 1, "joined_frame_count": 1,
        "proven_frame_count": 1, "blank_frame_count": 0,
        "unproven_frame_count": 0,
        "example": {"path": "/tmp/frame-0000.bmp", "sha256": "c" * 64,
                    "distinct_colors": 3, "marker": "OK — 3 distinct colours",
                    "proven": True},
        "frames": [],
    }


def no_frames():
    return {"runner_frame_count": 0, "joined_frame_count": 0,
            "example": None, "frames": []}


def ok_cli_launch(tmp, output=b"cli-ok-v1\n", exit_code=0, digest=None):
    """A recorded process-level launch with a real on-disk log and hash."""
    log = Path(tmp) / "run.log"
    log.write_bytes(output)
    return {
        "executed": True, "exit_code": exit_code, "seconds": 0.1,
        "evidence": str(Path(tmp)), "log": str(log),
        "command": ["/tmp/native", "--version"],
        "output_sha256": (hashlib.sha256(output).hexdigest()
                          if digest is None else digest),
        "stdout_tail": output.decode("utf-8", "replace"),
        "mode": "cli", "reason": "",
    }


def ok_cli_scenario(output=b"cli-ok-v1\n", exit_code=0, args=None):
    """A complete declared CLI scenario whose expected hash is computed, not invented."""
    return {
        "declared": True, "complete": True, "source": "/tmp/scenario.json",
        "args": ["--version"] if args is None else args,
        "expected_exit": exit_code,
        "expected_output_sha256": hashlib.sha256(output).hexdigest(),
        "reasons": [],
    }


# A stand-in for a headless CLI app: deterministic output, no screenshot frame.
CLI_NATIVE = '''#!/usr/bin/env python3
import sys
print("cli-ok-v1")
print("arg=" + ",".join(sys.argv[1:]))
'''
CLI_OUTPUT = b"cli-ok-v1\narg=--version\n"


class HelpersTest(unittest.TestCase):
    def test_parse_assignment_and_bare_path(self):
        self.assertEqual(ca.parse_assignment("Game=/tmp/a.exe"), ("Game", "/tmp/a.exe"))
        self.assertEqual(ca.parse_assignment("/tmp/a.exe"), ("a", "/tmp/a.exe"))
        with self.assertRaises(ValueError):
            ca.parse_assignment("=bad")

    def test_discover_roots_uses_supplied_root_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a").mkdir()
            (root / "a" / "Game.EXE").write_bytes(b"MZfake")
            (root / "readme.txt").write_text("no")
            found = ca.discover_roots([str(root)])
            self.assertEqual(found, {"Game": str(root / "a" / "Game.EXE")})

    def test_original_record_hashes_and_parses_pe(self):
        record = ca.original_record(str(FIXTURE))
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["sha256"],
                         hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        self.assertIn("architecture", record)

    def test_missing_and_invalid_originals_are_not_ok(self):
        self.assertEqual(ca.original_record(None)["status"], "MISSING_INPUT")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.exe"
            p.write_bytes(b"MZnot-a-pe")
            self.assertNotEqual(ca.original_record(str(p))["status"], "ok")

    def test_frame_summary_condenses_without_inventing(self):
        summary = ca.frame_summary(None)
        self.assertEqual(summary["joined_frame_count"], 0)
        self.assertIsNone(summary["example"])


class ClassifyTest(unittest.TestCase):
    def test_missing_original_or_native_is_blocked(self):
        status, reasons = ca.classify(
            ca.original_record(None), ok_pipeline(), ok_native(),
            ok_launch(), ok_frames(), None)
        self.assertEqual(status, ca.BLOCKED)
        self.assertTrue(any("original PE" in r for r in reasons))
        status, reasons = ca.classify(
            ok_original(), ok_pipeline(),
            {"path": None, "sha256": None, "reason": "no native binary"},
            ok_launch(), ok_frames(), None)
        self.assertEqual(status, ca.BLOCKED)
        self.assertTrue(any("native binary" in r for r in reasons))

    def test_build_without_launch_is_build_only(self):
        status, reasons = ca.classify(
            ok_original(), ok_pipeline(), ok_native(),
            {"executed": False, "exit_code": None, "reason": "not requested"},
            ok_frames(), None)
        self.assertEqual(status, ca.BUILD_ONLY)
        self.assertTrue(any("no launch evidence" in r for r in reasons))

    def test_no_frames_or_blank_frames_is_fail(self):
        for frames in ({"runner_frame_count": 0, "joined_frame_count": 0,
                        "example": None, "frames": []},
                       {"runner_frame_count": 1, "joined_frame_count": 1,
                        "example": None, "blank_frame_count": 1,
                        "frames": []}):
            status, reasons = ca.classify(ok_original(), ok_pipeline(),
                                          ok_native(), ok_launch(), frames, None)
            self.assertEqual(status, ca.FAIL)
            self.assertTrue(any("render evidence" in r for r in reasons))

    def test_render_evidence_without_verification_is_partial(self):
        status, reasons = ca.classify(
            ok_original(), ok_pipeline(verification="partial"), ok_native(),
            ok_launch(), ok_frames(), None)
        self.assertEqual(status, ca.PARTIAL)
        self.assertTrue(any(r.startswith("PASS withheld") for r in reasons))
        self.assertTrue(any("no Windows oracle" in r for r in reasons))

    def test_native_without_pipeline_report_cannot_reach_pass(self):
        status, reasons = ca.classify(
            ok_original(), ca.pipeline_record(None, ok_original()), ok_native(),
            ok_launch(), ok_frames(), None)
        self.assertEqual(status, ca.PARTIAL)
        self.assertTrue(any("no pipeline report" in r for r in reasons))

    def test_hash_mismatch_cannot_reach_pass(self):
        status, reasons = ca.classify(
            ok_original(), ok_pipeline(native_sha="d" * 64), ok_native(),
            ok_launch(), ok_frames(), None)
        self.assertEqual(status, ca.PARTIAL)
        self.assertTrue(any("native hash differs" in r for r in reasons))

    def test_failed_stage_or_bad_exit_cannot_reach_pass(self):
        for pipeline, launch in (
            (ok_pipeline(stage_failed="compile"), ok_launch()),
            (ok_pipeline(), ok_launch(exit_code=-11)),
        ):
            status, _ = ca.classify(ok_original(), pipeline, ok_native(),
                                    launch, ok_frames(), None)
            self.assertEqual(status, ca.PARTIAL)

    def test_pass_requires_all_explicit_conditions(self):
        status, reasons = ca.classify(ok_original(), ok_pipeline(), ok_native(),
                                      ok_launch(), ok_frames(),
                                      {"run_marker": "OK (exit 0)"})
        self.assertEqual(status, ca.PASS)
        self.assertTrue(any("verification 'scenario-pass' matched" in r
                            for r in reasons))


class PipelineRecordTest(unittest.TestCase):
    def _write_report(self, tmp, *, verification, input_sha, native_sha,
                      stage="ok"):
        report = {
            "input": "/tmp/Game.EXE",
            "manifest": {"input_sha256": input_sha,
                         "binary": {"path": "/tmp/native", "sha256": native_sha,
                                    "size": 10}},
            "outcome": {"status": verification, "reason": "test oracle"},
            "stages": [{"name": "compile", "status": stage}],
            "score": 42,
        }
        path = Path(tmp) / "report.json"
        path.write_text(json.dumps(report))
        return str(path)

    def test_matching_oracle_is_credited(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_report(d, verification="scenario-pass",
                                      input_sha="a" * 64, native_sha="b" * 64)
            rec = ca.pipeline_record(path, ok_original())
            self.assertTrue(rec["claims_verified"])
            self.assertEqual(rec["verification_status"], "scenario-pass")

    def test_mismatched_input_is_not_credited(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_report(d, verification="scenario-pass",
                                      input_sha="f" * 64, native_sha="b" * 64)
            rec = ca.pipeline_record(path, ok_original())
            self.assertFalse(rec["claims_verified"])
            self.assertTrue(any("does not match original" in r
                                for r in rec["reasons"]))

    def test_manifest_string_reference_is_resolved(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            (project / "reports").mkdir()
            (project / "manifest.json").write_text(json.dumps({
                "input_sha256": "a" * 64,
                "binary": {"path": "/tmp/native", "sha256": "b" * 64,
                           "size": 10},
            }))
            (project / "reports" / "report.json").write_text(json.dumps({
                "input": "/tmp/Game.EXE",
                "manifest": "manifest.json",
                "outcome": {"status": "scenario-pass", "reason": "test"},
                "stages": [{"name": "compile", "status": "ok"}],
            }))
            rec = ca.pipeline_record(str(project), ok_original())
            self.assertTrue(rec["claims_verified"])
            self.assertEqual(rec["native_sha256"], "b" * 64)
            self.assertEqual(rec["native_path"], "/tmp/native")

    def test_missing_report_is_explicit(self):
        rec = ca.pipeline_record("/does/not/exist/report.json", ok_original())
        self.assertEqual(rec["status"], "MISSING_INPUT")
        self.assertFalse(rec["claims_verified"])

    def test_native_record_detects_manifest_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            binary = Path(d) / "native"
            binary.write_bytes(b"native bytes")
            rec = ca.native_record(str(binary), {"native_sha256": "0" * 64})
            self.assertEqual(rec["sha256"],
                             hashlib.sha256(b"native bytes").hexdigest())
            self.assertTrue(rec["reason"])


class IntegrationTest(unittest.TestCase):
    def test_real_launch_produces_partial_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            native = root / "fake-native"
            native.write_text(FAKE_NATIVE)
            os.chmod(native, 0o755)
            game = {
                "name": "fake", "original": str(FIXTURE),
                "native": str(native), "assets": str(root),
                "pipeline_report": None,
            }
            report = ca.build_acceptance([game], root / "evidence", timeout=3)
            rec = report["games"][0]
            self.assertEqual(rec["status"], ca.PARTIAL, rec["reasons"])
            self.assertEqual(rec["launch"]["exit_code"], 0)
            self.assertEqual(rec["frames"]["runner_frame_count"], 1)
            self.assertEqual(rec["frames"]["proven_frame_count"], 1)
            self.assertGreater(rec["frames"]["example"]["distinct_colors"], 1)
            self.assertEqual(len(rec["frames"]["example"]["sha256"]), 64)
            self.assertTrue((root / "evidence" / "fake" / "frame-proof.json").is_file())

    def test_blank_frame_never_passes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            native = root / "blank-native"
            native.write_text(BLANK_NATIVE)
            os.chmod(native, 0o755)
            report = ca.build_acceptance([{
                "name": "blank", "original": str(FIXTURE),
                "native": str(native), "assets": str(root),
                "pipeline_report": None,
            }], root / "evidence", timeout=3)
            rec = report["games"][0]
            self.assertEqual(rec["status"], ca.FAIL, rec["reasons"])
            self.assertEqual(rec["frames"]["blank_frame_count"], 1)

    def test_no_run_reports_build_only_for_a_supplied_binary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            native = root / "native"
            native.write_bytes(b"binary")
            report = ca.build_acceptance([{
                "name": "built", "original": str(FIXTURE),
                "native": str(native), "assets": str(root),
            }], root / "evidence", execute=False)
            rec = report["games"][0]
            self.assertEqual(rec["status"], ca.BUILD_ONLY, rec["reasons"])
            self.assertFalse(rec["launch"]["executed"])
            self.assertEqual(rec["native"]["sha256"],
                             hashlib.sha256(b"binary").hexdigest())
            self.assertIn("BUILD ONLY", ca.render_markdown(report))

    def test_cli_writes_json_and_markdown_from_supplied_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            native = root / "native"
            native.write_bytes(b"binary")
            out_json = root / "out" / "acceptance.json"
            out_md = root / "out" / "acceptance.md"
            rc = ca.main([
                "--out", str(root / "evidence"), "--no-run",
                "--original", f"built={FIXTURE}",
                "--native", f"built={native}",
                "--json", str(out_json), "--markdown", str(out_md),
            ])
            self.assertEqual(rc, 0)
            data = json.loads(out_json.read_text())
            self.assertEqual(data["games"][0]["status"], ca.BUILD_ONLY)
            self.assertIn("Corpus acceptance report", out_md.read_text())
            assert_no_leaked_home_paths(out_json.read_text(), FIXTURE)


class CliHelpersTest(unittest.TestCase):
    def test_kind_defaults_to_gui_and_only_explicit_headless_opt_in(self):
        self.assertEqual(ca.normalize_kind(None), ca.KIND_GUI)
        self.assertEqual(ca.normalize_kind("gui"), ca.KIND_GUI)
        self.assertEqual(ca.normalize_kind("cli"), ca.KIND_CLI)
        self.assertEqual(ca.normalize_kind("HEADLESS"), ca.KIND_CLI)
        self.assertEqual(ca.normalize_kind("typo"), ca.KIND_GUI)

    def test_scenario_record_requires_all_three_declared_fields(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            good = root / "good.json"
            good.write_text(json.dumps({
                "args": ["--version"], "expected_exit": 0,
                "expected_output_sha256": "a" * 64}))
            rec = ca.scenario_record(str(good))
            self.assertTrue(rec["declared"])
            self.assertTrue(rec["complete"])
            self.assertEqual(rec["args"], ["--version"])
            self.assertEqual(rec["expected_exit"], 0)
            self.assertEqual(rec["expected_output_sha256"], "a" * 64)

            for name, payload in (
                ("no-hash.json", {"args": ["--version"], "expected_exit": 0}),
                ("bad-args.json", {"args": "--version", "expected_exit": 0,
                                   "expected_output_sha256": "a" * 64}),
                ("bad-exit.json", {"args": [], "expected_exit": "0",
                                   "expected_output_sha256": "a" * 64}),
            ):
                path = root / name
                path.write_text(json.dumps(payload))
                bad = ca.scenario_record(str(path))
                self.assertTrue(bad["declared"])
                self.assertFalse(bad["complete"], name)
                self.assertTrue(bad["reasons"], name)

            broken = root / "broken.json"
            broken.write_text("{not json")
            self.assertFalse(ca.scenario_record(str(broken))["complete"])
            self.assertFalse(ca.scenario_record(str(root / "absent.json"))["complete"])
            self.assertFalse(ca.scenario_record(None)["declared"])

    def test_uses_cli_evidence_requires_opt_in_and_declaration(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "scenario.json"
            path.write_text(json.dumps({
                "args": [], "expected_exit": 0,
                "expected_output_sha256": "a" * 64}))
            scenario = ca.scenario_record(str(path))
            self.assertTrue(ca.uses_cli_evidence("cli", scenario))
            self.assertTrue(ca.uses_cli_evidence("headless", scenario))
            self.assertFalse(ca.uses_cli_evidence("gui", scenario))
            self.assertFalse(ca.uses_cli_evidence("cli", ca.scenario_record(None)))

    def test_cli_evidence_compares_recorded_hash_and_log(self):
        output = b"deterministic output\n"
        scenario = ok_cli_scenario(output=output)
        with tempfile.TemporaryDirectory() as d:
            launch = ok_cli_launch(d, output=output)
            ev = ca.cli_evidence(scenario, launch)
            self.assertTrue(ev["evidence_present"])
            self.assertTrue(ev["exit_matches"])
            self.assertTrue(ev["output_matches"])
            self.assertTrue(ev["reproduced"])

            mismatch = ca.cli_evidence(scenario,
                                       ok_cli_launch(d, output=b"other output\n"))
            self.assertFalse(mismatch["output_matches"])
            self.assertFalse(mismatch["reproduced"])

            launch["log"] = str(Path(d) / "missing.log")
            self.assertFalse(ca.cli_evidence(scenario, launch)["reproduced"])


class CliClassifyTest(unittest.TestCase):
    def test_declared_cli_scenario_reaches_partial_without_oracle(self):
        output = b"cli-ok-v1\n"
        with tempfile.TemporaryDirectory() as d:
            status, reasons = ca.classify(
                ok_original(), ok_pipeline(verification="not-run"), ok_native(),
                ok_cli_launch(d, output=output), no_frames(), None,
                kind="cli", scenario=ok_cli_scenario(output=output))
            self.assertEqual(status, ca.PARTIAL, reasons)
            self.assertTrue(any("render-free process-level" in r for r in reasons))
            self.assertTrue(any("no Windows oracle" in r for r in reasons))
            self.assertTrue(any(r.startswith("PASS withheld") for r in reasons))

    def test_accepted_oracle_and_matching_output_can_reach_pass(self):
        output = b"cli-ok-v1\n"
        with tempfile.TemporaryDirectory() as d:
            status, reasons = ca.classify(
                ok_original(), ok_pipeline(), ok_native(),
                ok_cli_launch(d, output=output), no_frames(), None,
                kind="cli", scenario=ok_cli_scenario(output=output))
            self.assertEqual(status, ca.PASS, reasons)
            self.assertTrue(any("verification 'scenario-pass' matched" in r
                                for r in reasons))

    def test_bare_exit_code_without_scenario_cannot_reach_pass(self):
        # Meta-test: even a perfect pipeline oracle cannot promote a bare exit
        # code when no scenario was declared. Frame rules still apply.
        with tempfile.TemporaryDirectory() as d:
            for launch in (ok_launch(exit_code=0), ok_cli_launch(d)):
                status, reasons = ca.classify(
                    ok_original(), ok_pipeline(), ok_native(), launch,
                    no_frames(), None, kind="cli", scenario=ca.scenario_record(None))
                self.assertNotEqual(status, ca.PASS)
                self.assertEqual(status, ca.FAIL, reasons)
                self.assertTrue(any("render evidence" in r for r in reasons))

    def test_scenario_with_no_output_hash_stays_fail(self):
        # A scenario claiming only an exit code is not stronger than a bare
        # exit code, so it cannot pass even with an accepted oracle.
        scenario = {
            "declared": True, "complete": False, "source": "/tmp/s.json",
            "args": ["--version"], "expected_exit": 0,
            "expected_output_sha256": None,
            "reasons": ["'expected_output_sha256' must be a 64-character hex digest"],
        }
        with tempfile.TemporaryDirectory() as d:
            status, reasons = ca.classify(
                ok_original(), ok_pipeline(), ok_native(),
                ok_cli_launch(d), no_frames(), None,
                kind="cli", scenario=scenario)
            self.assertEqual(status, ca.FAIL, reasons)
            self.assertTrue(any("incomplete" in r for r in reasons))

    def test_divergent_exit_or_output_is_fail(self):
        with tempfile.TemporaryDirectory() as d:
            status, reasons = ca.classify(
                ok_original(), ok_pipeline(), ok_native(),
                ok_cli_launch(d, exit_code=2), no_frames(), None,
                kind="cli", scenario=ok_cli_scenario())
            self.assertEqual(status, ca.FAIL, reasons)
            self.assertTrue(any("did not reproduce" in r for r in reasons))
        with tempfile.TemporaryDirectory() as d:
            status, reasons = ca.classify(
                ok_original(), ok_pipeline(), ok_native(),
                ok_cli_launch(d, output=b"unexpected\n"), no_frames(), None,
                kind="cli", scenario=ok_cli_scenario())
            self.assertEqual(status, ca.FAIL, reasons)
            self.assertTrue(any("did not reproduce" in r for r in reasons))

    def test_missing_process_evidence_is_fail(self):
        with tempfile.TemporaryDirectory() as d:
            launch = ok_cli_launch(d)
            launch["output_sha256"] = None
            launch["log"] = str(Path(d) / "absent.log")
            status, reasons = ca.classify(
                ok_original(), ok_pipeline(), ok_native(), launch, no_frames(),
                None, kind="cli", scenario=ok_cli_scenario())
            self.assertEqual(status, ca.FAIL, reasons)
            self.assertTrue(any("no process-level evidence" in r for r in reasons))

    def test_cli_scenario_without_launch_is_build_only(self):
        status, reasons = ca.classify(
            ok_original(), ok_pipeline(), ok_native(),
            {"executed": False, "exit_code": None, "reason": "not requested"},
            no_frames(), None, kind="cli", scenario=ok_cli_scenario())
        self.assertEqual(status, ca.BUILD_ONLY, reasons)


class CliIntegrationTest(unittest.TestCase):
    def _scenario_file(self, root, *, output=CLI_OUTPUT):
        path = root / "scenario.json"
        path.write_text(json.dumps({
            "args": ["--version"], "expected_exit": 0,
            "expected_output_sha256": hashlib.sha256(output).hexdigest()}))
        return str(path)

    def test_declared_cli_scenario_reaches_partial_with_recorded_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            native = root / "cli-native"
            native.write_text(CLI_NATIVE)
            os.chmod(native, 0o755)
            scenario_path = self._scenario_file(root)
            report = ca.build_acceptance([{
                "name": "cliapp", "original": str(FIXTURE), "native": str(native),
                "assets": str(root), "kind": "cli", "scenario": scenario_path,
                "pipeline_report": None,
            }], root / "evidence", timeout=3)
            rec = report["games"][0]
            self.assertEqual(rec["status"], ca.PARTIAL, rec["reasons"])
            self.assertEqual(rec["kind"], "cli")
            self.assertEqual(rec["launch"]["exit_code"], 0)
            self.assertEqual(rec["launch"]["command"], [str(native), "--version"])
            self.assertEqual(rec["launch"]["output_sha256"],
                             hashlib.sha256(CLI_OUTPUT).hexdigest())
            self.assertEqual(Path(rec["launch"]["log"]).read_bytes(), CLI_OUTPUT)
            self.assertEqual(rec["frames"]["joined_frame_count"], 0)
            self.assertIsNone(rec["frames"]["example"])
            self.assertTrue(rec["cli_evidence"]["reproduced"])
            self.assertTrue(any("render-free process-level" in r
                                for r in rec["reasons"]))
            markdown = ca.render_markdown(report)
            self.assertIn("CLI scenario", markdown)
            self.assertIn("render-free process-level evidence", markdown)

    def test_divergent_cli_scenario_is_fail_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            native = root / "cli-native"
            native.write_text(CLI_NATIVE)
            os.chmod(native, 0o755)
            scenario_path = self._scenario_file(root, output=b"wrong output\n")
            report = ca.build_acceptance([{
                "name": "cliapp", "original": str(FIXTURE), "native": str(native),
                "assets": str(root), "kind": "headless", "scenario": scenario_path,
            }], root / "evidence", timeout=3)
            rec = report["games"][0]
            self.assertEqual(rec["status"], ca.FAIL, rec["reasons"])
            self.assertFalse(rec["cli_evidence"]["reproduced"])
            self.assertTrue(any("did not reproduce" in r for r in rec["reasons"]))

    def test_cli_game_without_scenario_keeps_existing_frame_rules(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            renderer = root / "renderer"
            renderer.write_text(FAKE_NATIVE)
            os.chmod(renderer, 0o755)
            # kind=cli with no declared scenario: frame rules still apply.
            renderer_report = ca.build_acceptance([{
                "name": "render", "original": str(FIXTURE), "native": str(renderer),
                "assets": str(root), "kind": "cli",
            }], root / "evidence-render", timeout=3)
            rec = renderer_report["games"][0]
            self.assertEqual(rec["status"], ca.PARTIAL, rec["reasons"])
            self.assertEqual(rec["frames"]["proven_frame_count"], 1)

            silent = root / "silent"
            silent.write_text(CLI_NATIVE)
            os.chmod(silent, 0o755)
            silent_report = ca.build_acceptance([{
                "name": "silent", "original": str(FIXTURE), "native": str(silent),
                "assets": str(root), "kind": "cli",
            }], root / "evidence-silent", timeout=3)
            rec = silent_report["games"][0]
            self.assertEqual(rec["status"], ca.FAIL, rec["reasons"])
            self.assertTrue(any("render evidence" in r for r in rec["reasons"]))

    def test_cli_no_run_is_build_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            native = root / "cli-native"
            native.write_bytes(b"binary")
            scenario_path = self._scenario_file(root)
            report = ca.build_acceptance([{
                "name": "cliapp", "original": str(FIXTURE), "native": str(native),
                "assets": str(root), "kind": "cli", "scenario": scenario_path,
            }], root / "evidence", execute=False)
            rec = report["games"][0]
            self.assertEqual(rec["status"], ca.BUILD_ONLY, rec["reasons"])
            self.assertFalse(rec["launch"]["executed"])

    def test_cli_kind_and_scenario_are_supplied_as_cli_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            native = root / "cli-native"
            native.write_text(CLI_NATIVE)
            os.chmod(native, 0o755)
            scenario_path = self._scenario_file(root)
            out_json = root / "out" / "acceptance.json"
            rc = ca.main([
                "--out", str(root / "evidence"),
                "--original", f"cliapp={FIXTURE}",
                "--native", f"cliapp={native}",
                "--assets", f"cliapp={root}",
                "--kind", "cliapp=cli",
                "--scenario", f"cliapp={scenario_path}",
                "--json", str(out_json),
            ])
            self.assertEqual(rc, 0)
            data = json.loads(out_json.read_text())
            rec = data["games"][0]
            self.assertEqual(rec["status"], ca.PARTIAL, rec["reasons"])
            self.assertEqual(rec["kind"], "cli")
            assert_no_leaked_home_paths(out_json.read_text(), FIXTURE)


if __name__ == "__main__":
    unittest.main()
