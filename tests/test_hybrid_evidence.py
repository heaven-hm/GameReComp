"""Tests for the hybrid pipeline's machine-readable evidence schema.

Every test builds a real claim against real files: the checked-in PE32 x86
fixture, a small Mach-O header (so the native OS/arch is detected from bytes),
a scenario JSON and two frame artifacts. No engine is mocked and no engine
result is fabricated -- the validator treats the engine records as claims and
the files as facts, which is exactly what these tests exercise.

Covered refusals: tampered/fabricated hashes, missing evidence, wrong OS/arch,
legacy status strings, blanket percentages, vacuous Unicorn runs, unlabelled
or fallback runners and PE-fact mismatches.
"""
from __future__ import annotations

import copy
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import hybrid_evidence as he  # noqa: E402

FIXED_TIME = "2026-01-01T00:00:00Z"
ORIGINAL_FIXTURE = ROOT / "tests" / "fixtures" / "pe32-minimal.exe"
STDOUT_HASH = hashlib.sha256(b"fib(20)=6765\narith:done\n").hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def macho_arm64() -> bytes:
    """A minimal thin Mach-O arm64 header (enough for header detection)."""
    return b"\xcf\xfa\xed\xfe" + struct.pack("<I", 0x0100000C) + b"\x00" * 24


def elf_x64() -> bytes:
    """A minimal ELF64 x86-64 header (enough for header detection)."""
    data = bytearray(64)
    data[:4] = b"\x7fELF"
    data[4] = 2  # ELFCLASS64
    data[5] = 1  # ELFDATA2LSB
    struct.pack_into("<H", data, 18, 62)  # EM_X86_64
    return bytes(data)


class HybridEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.original = self.dir / "original.exe"
        self.original.write_bytes(ORIGINAL_FIXTURE.read_bytes())
        self.native = self.dir / "native"
        self.native.write_bytes(macho_arm64())
        self.scenario = self.dir / "scenario.json"
        self.scenario.write_text(json.dumps({"scenario": "boot-to-menu", "args": []}),
                                 encoding="utf-8")
        self.frames = [self.dir / "frame-01.bin", self.dir / "frame-02.bin"]
        for index, frame in enumerate(self.frames):
            frame.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([index]) * 32)

        self.claim = self.valid_claim()

    def valid_claim(self) -> dict:
        return {
            "schema": 1,
            "target": {"os": "macOS", "arch": "arm64"},
            "hashes": {
                "original": sha256_file(self.original),
                "native": sha256_file(self.native),
                "scenario": sha256_file(self.scenario),
                "frames": [sha256_file(path) for path in self.frames],
            },
            "pe_facts": {
                "machine": "i386",
                "pe_type": "PE32",
                "architecture": "x86",
                "image_base": "0x400000",
                "entry_point_rva": "0x1000",
                "sections": [".text", ".rdata"],
            },
            "unicorn": {
                "status": "PASS", "function": "0x401000", "section": ".text",
                "cases": 8, "seeds": ["0x1", "0x2"], "distinct_eax": 7,
                "nonvacuous": True,
            },
            "frida": {
                "status": "PASS", "script_sha256": "d981d7402c622f3c",
                "calls": ["KERNEL32.dll!WriteFile", "KERNEL32.dll!ExitProcess"],
                "reason": "captured 2 calls in the bounded attach",
            },
            "input_replay": {
                "status": "PASS",
                "original": {
                    "runner": "highball", "exit_code": 0,
                    "stdout_hash": STDOUT_HASH,
                    "command": ["highball", "run", str(self.original)],
                },
                "native": {"exit_code": 0, "stdout_hash": STDOUT_HASH},
            },
            "verdict": {
                "status": "PASS",
                "reasons": ["all declared gates were recomputed and validated"],
            },
        }

    def report(self, claim=None, **overrides) -> dict:
        options = {
            "original": self.original,
            "native": self.native,
            "scenario": self.scenario,
            "frames": self.frames,
            "generated_at": FIXED_TIME,
        }
        options.update(overrides)
        return he.validate(claim if claim is not None else self.claim, **options)

    def codes(self, report: dict) -> set[str]:
        return {reason["code"] for reason in report["verdict"]["reasons"]}

    # -----------------------------------------------------------------
    # Baseline + canonical writer + Markdown
    # -----------------------------------------------------------------
    def test_complete_evidence_yields_pass(self):
        report = self.report()
        self.assertEqual(report["verdict"]["status"], "PASS",
                         report["verdict"]["reasons"])
        self.assertEqual(report["native_target"], {"os": "macOS", "arch": "arm64"})
        self.assertTrue(report["pe_facts"]["match"])
        self.assertTrue(report["summary"]["accepted"])
        self.assertTrue(all(item["match"] for kind in ("original", "native", "scenario")
                            for item in [report["hashes"][kind]]))
        self.assertTrue(all(item["match"] for item in report["hashes"]["frames"]))

    def test_canonical_json_is_sorted_compact_and_deterministic(self):
        report = self.report()
        text = he.canonical_json(report)
        self.assertEqual(text, json.dumps(report, sort_keys=True,
                                          separators=(",", ":"),
                                          ensure_ascii=False, allow_nan=False) + "\n")
        self.assertEqual(text, he.canonical_json(json.loads(text)))
        out = he.write_canonical(report, self.dir / "nested" / "report.json")
        self.assertEqual(out.read_bytes(), text.encode("utf-8"))

    def test_markdown_renderer_reports_verdict_reasons_and_scope(self):
        report = self.report()
        md = he.render_markdown(report)
        self.assertIn("Verdict: **PASS**", md)
        self.assertIn("## Hashes (claimed vs recomputed)", md)
        self.assertIn("## Verdict reasons", md)
        self.assertIn("CPU/process-level hybrid evidence only", md)
        self.assertIn("not a 100% compatibility", md)
        self.assertIn("Recomputed SHA-256", md)

    # -----------------------------------------------------------------
    # Tampering / fabricated evidence
    # -----------------------------------------------------------------
    def test_tampered_claim_hash_is_failed(self):
        claim = copy.deepcopy(self.claim)
        claim["hashes"]["original"] = "0" * 64
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "FAIL")
        self.assertIn("HASH_MISMATCH", self.codes(report))
        self.assertIn("CLAIM_NOT_EVIDENCED", self.codes(report))

    def test_tampered_file_after_the_claim_is_failed(self):
        claim = copy.deepcopy(self.claim)
        self.original.write_bytes(self.original.read_bytes() + b"\n")
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "FAIL")
        self.assertIn("HASH_MISMATCH", self.codes(report))
        self.assertEqual(report["hashes"]["original"]["match"], False)

    def test_fabricated_or_malformed_hash_is_failed(self):
        for bad in ("not-a-hash", "1234", 1234, None):
            with self.subTest(bad=bad):
                claim = copy.deepcopy(self.claim)
                claim["hashes"]["native"] = bad
                report = self.report(claim)
                self.assertEqual(report["verdict"]["status"], "FAIL")
                self.assertIn("FABRICATED_HASH", self.codes(report))

    def test_tampered_pe_fact_is_failed(self):
        claim = copy.deepcopy(self.claim)
        claim["pe_facts"]["image_base"] = "0x999999"
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "FAIL")
        self.assertIn("PE_FACT_MISMATCH", self.codes(report))

    def test_hybrid_artifact_binding_mismatch_is_failed(self):
        claim = copy.deepcopy(self.claim)
        claim["artifacts"] = {"original": {"sha256": "f" * 64},
                              "native": {"sha256": sha256_file(self.native)}}
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "FAIL")
        self.assertIn("ARTIFACT_BINDING_MISMATCH", self.codes(report))

    # -----------------------------------------------------------------
    # Missing / incomplete evidence
    # -----------------------------------------------------------------
    def test_missing_artifact_paths_are_blocked(self):
        report = self.report(scenario=None, frames=None, native=None)
        self.assertEqual(report["verdict"]["status"], "BLOCKED")
        self.assertIn("MISSING_EVIDENCE", self.codes(report))
        self.assertFalse(report["summary"]["accepted"])

    def test_missing_engine_record_is_blocked(self):
        claim = copy.deepcopy(self.claim)
        del claim["input_replay"]
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "BLOCKED")
        self.assertEqual(report["records"]["input_replay"]["present"], False)
        self.assertIn("MISSING_EVIDENCE", self.codes(report))

    def test_no_claim_at_all_is_not_tested(self):
        report = he.validate(None, original=self.original, native=self.native,
                             scenario=self.scenario, frames=self.frames,
                             generated_at=FIXED_TIME)
        self.assertEqual(report["verdict"]["status"], "NOT TESTED")
        self.assertIn("INVALID_CLAIM", self.codes(report))

    def test_wrong_schema_version_is_not_tested(self):
        claim = copy.deepcopy(self.claim)
        claim["schema"] = 2
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "NOT TESTED")
        self.assertIn("INVALID_SCHEMA", self.codes(report))

    def test_written_pass_without_reasons_is_demoted(self):
        claim = copy.deepcopy(self.claim)
        claim["verdict"]["reasons"] = []
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "PARTIAL")
        self.assertIn("INCOMPLETE_EVIDENCE", self.codes(report))

    def test_frida_blocked_without_exact_reason_is_incomplete(self):
        claim = copy.deepcopy(self.claim)
        claim["frida"] = {"status": "BLOCKED"}
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "PARTIAL")
        self.assertIn("INCOMPLETE_EVIDENCE", self.codes(report))
        self.assertIn("ENGINE_BLOCKED", self.codes(report))

    # -----------------------------------------------------------------
    # OS/arch
    # -----------------------------------------------------------------
    def test_wrong_native_arch_is_blocked(self):
        claim = copy.deepcopy(self.claim)
        claim["target"]["arch"] = "x64"  # native header says arm64
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "BLOCKED")
        self.assertIn("OS_ARCH_MISMATCH", self.codes(report))

    def test_wrong_native_os_is_blocked(self):
        claim = copy.deepcopy(self.claim)
        claim["target"]["os"] = "Linux"  # Mach-O header says macOS
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "BLOCKED")
        self.assertIn("OS_ARCH_MISMATCH", self.codes(report))

    def test_unsupported_os_arch_pair_is_blocked(self):
        for os_name, arch in (("Windows", "arm64"), ("macOS", "Win16"),
                              ("unknown", "x86")):
            with self.subTest(os=os_name, arch=arch):
                claim = copy.deepcopy(self.claim)
                claim["target"] = {"os": os_name, "arch": arch}
                report = self.report(claim)
                self.assertEqual(report["verdict"]["status"], "BLOCKED")
                self.assertTrue({"UNSUPPORTED_TARGET", "INVALID_TARGET"}
                                & self.codes(report))

    def test_wrong_original_arch_claim_is_blocked(self):
        claim = copy.deepcopy(self.claim)
        claim["original_target"] = {"os": "Windows", "arch": "x64"}
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "BLOCKED")
        self.assertIn("OS_ARCH_MISMATCH", self.codes(report))

    def test_os_arch_is_detected_from_native_bytes_not_the_claim(self):
        self.native.write_bytes(elf_x64())
        claim = copy.deepcopy(self.claim)
        claim["hashes"]["native"] = sha256_file(self.native)
        claim["target"] = {"os": "Linux", "arch": "x64"}
        report = self.report(claim)
        self.assertEqual(report["native_target"], {"os": "Linux", "arch": "x64"})
        self.assertEqual(report["verdict"]["status"], "PASS")

    # -----------------------------------------------------------------
    # Status vocabulary and claim style
    # -----------------------------------------------------------------
    def test_old_verdict_statuses_are_demoted(self):
        for old in ("MATCH", "DIVERGED", "OK", "SUCCESS", "100%", "PASSED"):
            with self.subTest(old=old):
                claim = copy.deepcopy(self.claim)
                claim["verdict"]["status"] = old
                report = self.report(claim)
                self.assertEqual(report["verdict"]["status"], "NOT TESTED")
                self.assertIn("OLD_STATUS_STRING", self.codes(report))
                self.assertNotIn("PASS", report["verdict"]["status"])

    def test_legacy_engine_statuses_are_translated_but_demoted(self):
        claim = copy.deepcopy(self.claim)
        claim["unicorn"]["status"] = "OK"
        claim["input_replay"]["status"] = "MATCH"
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "PARTIAL")
        self.assertIn("LEGACY_ENGINE_STATUS", self.codes(report))
        self.assertEqual(report["records"]["unicorn"]["status"], "PASS")
        self.assertEqual(report["records"]["input_replay"]["status"], "PASS")

    def test_unknown_engine_status_is_blocked(self):
        claim = copy.deepcopy(self.claim)
        claim["frida"]["status"] = "GREEN"
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "BLOCKED")
        self.assertIn("INVALID_ENGINE_STATUS", self.codes(report))

    def test_blanket_percentage_is_demoted(self):
        for mutated in (
            lambda c: c["verdict"]["reasons"].append("100% compatible"),
            lambda c: c.__setitem__("score", 100),
            lambda c: c.__setitem__("compatibility", "100%"),
        ):
            with self.subTest(mutation=mutated):
                claim = copy.deepcopy(self.claim)
                mutated(claim)
                report = self.report(claim)
                self.assertEqual(report["verdict"]["status"], "PARTIAL")
                self.assertIn("BLANKET_PERCENTAGE", self.codes(report))

    def test_disclaimer_percentage_is_not_a_blanket_claim(self):
        claim = copy.deepcopy(self.claim)
        claim["verdict"]["reasons"].append("not a 100% compatibility claim")
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "PASS")
        self.assertNotIn("BLANKET_PERCENTAGE", self.codes(report))

    def test_operator_can_only_make_the_verdict_more_conservative(self):
        claim = copy.deepcopy(self.claim)
        claim["verdict"]["status"] = "DEFERRED"
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "DEFERRED")
        self.assertIn("CLAIM_DOWNGRADED", self.codes(report))

    # -----------------------------------------------------------------
    # Engine-specific gates
    # -----------------------------------------------------------------
    def test_vacuous_unicorn_is_blocked(self):
        claim = copy.deepcopy(self.claim)
        claim["unicorn"].update(nonvacuous=False, distinct_eax=1)
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "BLOCKED")
        self.assertIn("VACUOUS_UNICORN", self.codes(report))

    def test_incomplete_unicorn_is_demoted(self):
        claim = copy.deepcopy(self.claim)
        del claim["unicorn"]["distinct_eax"]
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "PARTIAL")
        self.assertIn("INCOMPLETE_EVIDENCE", self.codes(report))

    def test_frida_blocked_caps_at_partial_and_keeps_exact_reason(self):
        claim = copy.deepcopy(self.claim)
        claim["frida"] = {
            "status": "BLOCKED",
            "reason": "PermissionDeniedError: unable to access process with pid 86596",
            "blocked_class": "environment",
        }
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "PARTIAL")
        self.assertIn("ENGINE_BLOCKED", self.codes(report))
        self.assertIn("PermissionDeniedError", report["records"]["frida"]["reason"])

    def test_wine_fallback_runner_is_demoted(self):
        claim = copy.deepcopy(self.claim)
        claim["input_replay"]["original"]["runner"] = "wine-fallback"
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "PARTIAL")
        self.assertIn("FALLBACK_RUNNER", self.codes(report))

    def test_divergent_replay_is_failed(self):
        claim = copy.deepcopy(self.claim)
        claim["input_replay"]["native"]["stdout_hash"] = "a" * 64
        report = self.report(claim)
        self.assertEqual(report["verdict"]["status"], "FAIL")
        self.assertIn("REPLAY_DIVERGENCE", self.codes(report))

    def test_no_frame_evidence_is_partial(self):
        claim = copy.deepcopy(self.claim)
        claim["hashes"]["frames"] = []
        report = self.report(claim, frames=[])
        self.assertEqual(report["verdict"]["status"], "PARTIAL")
        self.assertIn("NO_FRAME_EVIDENCE", self.codes(report))

    # -----------------------------------------------------------------
    # CLI
    # -----------------------------------------------------------------
    def test_cli_writes_canonical_report_and_markdown(self):
        claim_path = self.dir / "claim.json"
        claim_path.write_text(json.dumps(self.claim), encoding="utf-8")
        out = self.dir / "reports" / "hybrid-evidence.json"
        md = self.dir / "reports" / "hybrid-evidence.md"
        result = subprocess.run(
            [sys.executable, "-m", "tools.hybrid_evidence",
             "--claim", str(claim_path), "--original", str(self.original),
             "--native", str(self.native), "--scenario", str(self.scenario),
             "--frames", str(self.frames[0]), "--frames", str(self.frames[1]),
             "--generated-at", FIXED_TIME, "--out", str(out), "--markdown", str(md),
             "--strict"],
            cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = out.read_text(encoding="utf-8")
        self.assertEqual(text, he.canonical_json(json.loads(text)))
        self.assertEqual(json.loads(text)["verdict"]["status"], "PASS")
        self.assertIn("Verdict: **PASS**", md.read_text(encoding="utf-8"))

    def test_cli_strict_refuses_a_blocked_report(self):
        claim = copy.deepcopy(self.claim)
        claim["target"]["arch"] = "x64"
        claim_path = self.dir / "claim.json"
        claim_path.write_text(json.dumps(claim), encoding="utf-8")
        out = self.dir / "blocked.json"
        result = subprocess.run(
            [sys.executable, "-m", "tools.hybrid_evidence",
             "--claim", str(claim_path), "--original", str(self.original),
             "--native", str(self.native), "--scenario", str(self.scenario),
             "--frames", str(self.frames[0]), "--frames", str(self.frames[1]),
             "--generated-at", FIXED_TIME, "--out", str(out), "--strict"],
            cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(out.read_text())["verdict"]["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
