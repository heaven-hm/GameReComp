"""The Unicorn diff adapter must reduce the tool's report honestly and fail closed.

All tests inject a fake difftest runner: it writes a report shaped exactly like
``tools/lift/difftest.py --project`` emits, so agreement, divergence, a faulted
original, a missing tool and a tampered report can all be proven without a real
PE, a C compiler, or Unicorn actually executing anything.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import unicorn_diff_adapter as adapter  # noqa: E402

ADDRS = ["0x00401000", "0x00401120"]
EXPECTED_TOOL = "tools/lift/difftest.py --project"


def make_paths(tmp_path: Path):
    project = tmp_path / "project"
    (project / "src" / "highlevel").mkdir(parents=True)
    pe = tmp_path / "app.exe"
    pe.write_bytes(b"MZ" + b"\x00" * 62)
    return project, pe, tmp_path / "report.json"


def write_report(path: Path, verdicts, tool: str = EXPECTED_TOOL) -> bytes:
    """Write a difftest-shaped report and return the exact bytes written."""
    doc = {
        "tool": tool,
        "what_it_proves": "the bodies and the original's bytes agree on the "
                          "compared fields over the driven entry states",
        "what_it_does_not_prove": "anything about other entry states or the game",
        "addresses": [
            {"address": addr, "verdict": verdict, "cases": [],
             "diverged_cases": 0 if verdict.startswith("agree") else 1}
            for addr, verdict in verdicts
        ],
    }
    payload = (json.dumps(doc, indent=1) + "\n").encode()
    path.write_bytes(payload)
    return payload


def fake_runner(verdicts, *, tamper=None, returncode=None, tool=EXPECTED_TOOL,
                stdout="per-function oracle\n"):
    """Build a runner that writes a report, optionally tampering with it on disk.

    The runner returns the bytes it originally wrote; if ``tamper`` changed the
    file on disk, the adapter must notice the mismatch and fail closed.
    """
    def runner(command, report_path):
        written = write_report(Path(report_path), zip(ADDRS, verdicts), tool=tool)
        if tamper is not None:
            tamper(Path(report_path))
        rc = returncode
        if rc is None:
            rc = 0 if all(v.startswith("agree") for v in verdicts) else 1
        return adapter.DiffOutcome(rc, stdout, "", written)
    return runner


def flip_verdict(path: Path) -> None:
    """Tamper hook: rewrite the report so the first verdict says 'agree'."""
    doc = json.loads(path.read_text())
    doc["addresses"][0]["verdict"] = "agree"
    path.write_text(json.dumps(doc, indent=1) + "\n")


class UnicornDiffAdapterTests(unittest.TestCase):
    def test_agreement_reported_with_exact_command_and_report_hash(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            rec = adapter.run_unicorn_diff(
                project, pe, ADDRS, cases=8, report_path=report,
                runner=fake_runner(["agree", "agree-frame"]))

            self.assertEqual(rec["status"], "OK")
            self.assertEqual(rec["blocked_class"], None)
            self.assertEqual(rec["agreement"], "full")
            self.assertEqual(rec["counts"], {"addresses_tested": 2, "agree": 2,
                                              "diverge": 0, "original_faulted": 0,
                                              "other": 0})
            self.assertEqual(rec["addresses_tested"], ADDRS)
            self.assertEqual(rec["tool"], EXPECTED_TOOL)
            self.assertEqual(rec["tool_exit_code"], 0)
            self.assertEqual(rec["report_sha256"],
                             hashlib.sha256(report.read_bytes()).hexdigest())
            command = rec["command"]
            self.assertEqual(command[1], str(adapter.DIFTEST))
            self.assertIn("--project", command)
            self.assertEqual(command[command.index("--pe") + 1], str(pe))
            self.assertEqual([command[i + 1] for i, a in enumerate(command)
                              if a == "--address"], ADDRS)
            self.assertEqual(command[command.index("--cases") + 1], "8")
            self.assertEqual(command[command.index("--json") + 1], str(report))
            self.assertNotIn("--flags", command)
            self.assertTrue(rec["command_string"])
            self.assertEqual(len(rec["verdicts"]), 2)

    def test_flags_comparison_is_forwarded(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            rec = adapter.run_unicorn_diff(
                project, pe, ADDRS[:1], cases=3, flags=True,
                report_path=report, runner=fake_runner(["agree"]))
            self.assertEqual(rec["status"], "OK")
            self.assertIn("--flags", rec["command"])
            self.assertEqual(rec["command"][rec["command"].index("--cases") + 1], "3")
            self.assertTrue(rec["flags"])

    def test_divergence_is_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            rec = adapter.run_unicorn_diff(
                project, pe, ADDRS, report_path=report,
                runner=fake_runner(["agree", "diverge"], returncode=1))

            self.assertEqual(rec["status"], "OK")
            self.assertEqual(rec["agreement"], "partial")
            self.assertEqual(rec["counts"]["agree"], 1)
            self.assertEqual(rec["counts"]["diverge"], 1)
            self.assertEqual(rec["counts"]["original_faulted"], 0)
            self.assertEqual(rec["tool_exit_code"], 1)  # expected exit for divergence

    def test_original_faulted_is_a_reported_result_not_an_agreement(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            rec = adapter.run_unicorn_diff(
                project, pe, ADDRS[:1], report_path=report,
                runner=fake_runner(["original-faulted"], returncode=1))

            self.assertEqual(rec["status"], "OK")
            self.assertEqual(rec["agreement"], "none")
            self.assertEqual(rec["counts"]["original_faulted"], 1)
            self.assertEqual(rec["counts"]["agree"], 0)
            self.assertEqual(rec["verdicts"][0]["verdict"], "original-faulted")

    def test_missing_project_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            rec = adapter.run_unicorn_diff(
                project / "absent", pe, ADDRS, report_path=report,
                runner=fake_runner(["agree", "agree"]))
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(rec["blocked_class"], "input")
            self.assertIsNone(rec["agreement"])
            self.assertEqual(rec["counts"]["agree"], 0)
            self.assertEqual(rec["addresses_tested"], [])

    def test_missing_pe_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            rec = adapter.run_unicorn_diff(
                project, pe.with_name("gone.exe"), ADDRS, report_path=report,
                runner=fake_runner(["agree", "agree"]))
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(rec["blocked_class"], "input")
            self.assertIsNone(rec["agreement"])

    def test_missing_unicorn_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            with mock.patch.object(adapter, "_unicorn_available", return_value=False):
                rec = adapter.run_unicorn_diff(
                    project, pe, ADDRS, report_path=report,
                    runner=fake_runner(["agree", "agree"]))
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(rec["blocked_class"], "environment")
            self.assertIn("unicorn", rec["reason"])
            self.assertIsNone(rec["agreement"])

    def test_missing_tool_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            absent = Path(d) / "no_difftest.py"
            with mock.patch.object(adapter, "DIFTEST", absent):
                rec = adapter.run_unicorn_diff(project, pe, ADDRS, report_path=report)
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(rec["blocked_class"], "tool")
            self.assertIn("tool", rec["reason"].lower())
            self.assertIsNone(rec["agreement"])
            self.assertEqual(rec["counts"]["agree"], 0)

    def test_tampered_report_never_claims_agreement(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            rec = adapter.run_unicorn_diff(
                project, pe, ADDRS, report_path=report,
                runner=fake_runner(["diverge", "diverge"], tamper=flip_verdict,
                                   returncode=1))
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(rec["blocked_class"], "report")
            self.assertIn("tampered", rec["reason"].lower())
            self.assertIsNone(rec["agreement"])
            self.assertEqual(rec["counts"]["agree"], 0)
            self.assertEqual(rec["counts"]["diverge"], 0)
            self.assertEqual(rec["addresses_tested"], [])
            # the hash of the tampered bytes is still recorded as evidence
            self.assertEqual(rec["report_sha256"],
                             hashlib.sha256(report.read_bytes()).hexdigest())

    def test_no_report_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))

            def no_write(command, report_path):
                return adapter.DiffOutcome(0, "nothing written", "")

            rec = adapter.run_unicorn_diff(project, pe, ADDRS, report_path=report,
                                           runner=no_write)
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(rec["blocked_class"], "report")
            self.assertIn("no report", rec["reason"].lower())

    def test_wrong_tool_tag_or_addresses_are_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            bad_tool = adapter.run_unicorn_diff(
                project, pe, ADDRS, report_path=report,
                runner=fake_runner(["agree", "agree"], tool="some/other/tool.py"))
            self.assertEqual(bad_tool["status"], "BLOCKED")
            self.assertEqual(bad_tool["blocked_class"], "report")

            other = ["0x00409999", "0x00408888"]
            def wrong_addresses(command, report_path):
                written = write_report(Path(report_path), zip(other, ["agree"] * 2))
                return adapter.DiffOutcome(0, "", "", written)

            bad_addrs = adapter.run_unicorn_diff(
                project, pe, ADDRS, report_path=report, runner=wrong_addresses)
            self.assertEqual(bad_addrs["status"], "BLOCKED")
            self.assertEqual(bad_addrs["blocked_class"], "report")

    def test_unexpected_exit_code_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            rec = adapter.run_unicorn_diff(
                project, pe, ADDRS, report_path=report,
                runner=fake_runner(["agree", "agree"], returncode=-11))
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(rec["blocked_class"], "tool")

    def test_cli_missing_project_emits_blocked_record_and_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            pe = Path(d) / "app.exe"
            pe.write_bytes(b"MZ" + b"\x00" * 62)
            proc = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "unicorn_diff_adapter.py"),
                 "--project", str(Path(d) / "absent"), "--pe", str(pe),
                 "--address", ADDRS[0], "--report", str(Path(d) / "report.json")],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            record = json.loads(proc.stdout)
            self.assertEqual(record["status"], "BLOCKED")
            self.assertIsNone(record["agreement"])

    def test_cli_agreement_via_fake_report(self):
        # Drive the CLI with a stub difftest.py on a temporary sys.path: the
        # adapter builds the command from its own DIFTEST path, so we patch it
        # by running through main() with a patched module instead.
        with tempfile.TemporaryDirectory() as d:
            project, pe, report = make_paths(Path(d))
            runner = fake_runner(["agree", "agree"])
            with mock.patch.object(adapter, "subprocess_runner", runner):
                rc = adapter.main(["--project", str(project), "--pe", str(pe),
                                   "--address", ADDRS[0], "--address", ADDRS[1],
                                   "--report", str(report),
                                   "--output", str(Path(d) / "record.json")])
            self.assertEqual(rc, 0)
            record = json.loads((Path(d) / "record.json").read_text())
            self.assertEqual(record["status"], "OK")
            self.assertEqual(record["agreement"], "full")
            self.assertEqual(record["counts"]["agree"], 2)


if __name__ == "__main__":
    unittest.main()
