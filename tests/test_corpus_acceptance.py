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
            self.assertNotIn("/Users/haseeb-mir", out_json.read_text())


if __name__ == "__main__":
    unittest.main()
