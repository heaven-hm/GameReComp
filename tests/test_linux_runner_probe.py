"""The Linux runner probe must be evidence-driven and never fabricate a result.

All probe tests inject a fake command executor; none touches the live host. The
cases cover a present runner, an absent runner, failing commands, tampered
evidence (forged ELF magic / stale sentinel / foreign kernel / forged PASS
text), bounded output and CLI exit codes. A green report still says that the
Linux *pipeline* is NOT TESTED -- there is no unqualified PASS anywhere.

Run with: python3 -m pytest tests/test_linux_runner_probe.py -q
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import linux_runner_probe as lrp
from tools.linux_runner_probe import (
    DEFAULT_IMAGE,
    ELF_MAGIC_HEX,
    STATUSES,
    CmdResult,
    probe_linux_runner,
)

GOOD_SHA = "0123456789abcdef" * 4
CC_TEXT = "cc (GCC) 13.2.1 20230801"
CMAKE_TEXT = "cmake version 3.28.1"
PYTHON_TEXT = "Python 3.11.8"


class FakeExecutor:
    """Deterministic, injectable stand-in for real command execution.

    A fake can be made to present a native Linux host, a Docker Linux runner on
    a macOS host, or any of the failure/tamper combinations below. It records
    every argv it was given so tests can check what was actually attempted.
    """

    def __init__(
        self,
        *,
        host_kernel: str = "Linux",
        host_arch: str = "x86_64",
        docker_ok: bool = True,
        image_ok: bool = True,
        runner_kernel: str = "Linux",
        runner_arch: str = "x86_64",
        runner_identity: str = "Linux runner 6.6",
        cc: str = CC_TEXT,
        cmake: str = CMAKE_TEXT,
        python3: str = PYTHON_TEXT,
        identity_rc: int = 0,
        identity_timed_out: bool = False,
        identity_stdout: str | None = None,
        probe_rc: int = 0,
        probe_timed_out: bool = False,
        probe_stderr: str = "",
        probe_stdout: str | None = None,
        probe_exc: BaseException | None = None,
        elf: str = ELF_MAGIC_HEX,
        sha: str = GOOD_SHA,
        run_rc: str = "0",
        run_token: str | None = None,
    ) -> None:
        self.host_kernel = host_kernel
        self.host_arch = host_arch
        self.docker_ok = docker_ok
        self.image_ok = image_ok
        self.runner_kernel = runner_kernel
        self.runner_arch = runner_arch
        self.runner_identity = runner_identity
        self.cc = cc
        self.cmake = cmake
        self.python3 = python3
        self.identity_rc = identity_rc
        self.identity_timed_out = identity_timed_out
        self.identity_stdout = identity_stdout
        self.probe_rc = probe_rc
        self.probe_timed_out = probe_timed_out
        self.probe_stderr = probe_stderr
        self.probe_stdout = probe_stdout
        self.probe_exc = probe_exc
        self.elf = elf
        self.sha = sha
        self.run_rc = run_rc
        self.run_token = run_token
        self.calls: list[list[str]] = []

    def __call__(self, argv) -> CmdResult:
        cmd = [str(a) for a in argv]
        self.calls.append(cmd)
        if cmd == ["uname", "-s"]:
            return CmdResult(cmd, 0, self.host_kernel + "\n")
        if cmd == ["uname", "-m"]:
            return CmdResult(cmd, 0, self.host_arch + "\n")
        if cmd[:2] == ["docker", "info"]:
            return CmdResult(cmd, 0 if self.docker_ok else 1, "daemon unavailable\n")
        if cmd[:2] == ["docker", "image"]:
            return CmdResult(cmd, 0 if self.image_ok else 1, "no such image\n")

        script = cmd[-1] if cmd else ""
        if "ELF=" in script:  # the compile-and-execute fixture
            if self.probe_exc is not None:
                raise self.probe_exc
            stdout = self.probe_stdout
            if stdout is None:
                token = self.run_token
                if token is None:
                    match = re.search(r'puts\("([0-9a-f]+)"\)', script)
                    token = match.group(1) if match else "SENTINEL_NOT_FOUND"
                stdout = (
                    f"ELF={self.elf}\nSHA256={self.sha}\n"
                    f"RUN={token}\nRC={self.run_rc}\n"
                )
            return CmdResult(cmd, self.probe_rc, stdout, self.probe_stderr,
                             self.probe_timed_out)

        stdout = self.identity_stdout
        if stdout is None:
            stdout = (
                f"KERNEL={self.runner_kernel}\nARCH={self.runner_arch}\n"
                f"UNAME={self.runner_identity}\nCC={self.cc}\n"
                f"CMAKE={self.cmake}\nPYTHON={self.python3}\n"
            )
        return CmdResult(cmd, self.identity_rc, stdout, "",
                         self.identity_timed_out)


def assert_no_pass(test: unittest.TestCase, rec: dict) -> None:
    """Universal guard: no report is -- or contains -- an unqualified PASS."""
    test.assertIn(rec["status"], STATUSES)
    test.assertNotEqual(rec["status"], "PASS")
    test.assertEqual(rec["linux_pipeline_result"], "NOT TESTED")
    test.assertTrue(rec["not_a_linux_pipeline_pass"])
    test.assertFalse(any(v == "PASS" for v in rec.values()))


class NoLiveProbingTests(unittest.TestCase):
    def test_probe_without_executor_runs_nothing_and_is_not_tested(self):
        with mock.patch.object(lrp.subprocess, "run",
                               side_effect=AssertionError("live probe attempted")):
            rec = probe_linux_runner()
        self.assertEqual(rec["status"], "NOT TESTED")
        self.assertEqual(rec["commands"], [])
        self.assertFalse(rec["native_binary"]["executed"])
        self.assertTrue(any("no command executor" in r for r in rec["reasons"]))
        assert_no_pass(self, rec)

    def test_cli_no_probe_flag_never_executes_a_command(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "report.json"
            with mock.patch.object(
                lrp.subprocess, "run",
                side_effect=AssertionError("live probe attempted"),
            ):
                rc = lrp.main(["--no-probe", "--out-json", str(out)])
            self.assertEqual(rc, 1)
            rec = json.loads(out.read_text())
        self.assertEqual(rec["status"], "NOT TESTED")
        assert_no_pass(self, rec)

    def test_invalid_runner_mode_fails_before_any_command(self):
        fake = FakeExecutor()
        rec = probe_linux_runner(fake, runner="wine")
        self.assertEqual(rec["status"], "FAIL")
        self.assertEqual(fake.calls, [])
        assert_no_pass(self, rec)


class PresentRunnerTests(unittest.TestCase):
    def test_native_linux_runner_is_usable_but_pipeline_not_tested(self):
        rec = probe_linux_runner(FakeExecutor())
        self.assertEqual(rec["status"], "USABLE")
        self.assertEqual(rec["host"], {"kernel": "Linux", "arch": "x86_64"})
        self.assertEqual(rec["runner"]["mode"], "native")
        self.assertEqual(rec["runner"]["kernel"], "Linux")
        self.assertEqual(rec["runner"]["arch"], "x86_64")
        self.assertTrue(rec["toolchain"]["cc"])
        self.assertTrue(rec["toolchain"]["cmake"])
        self.assertTrue(rec["toolchain"]["python3"])
        nb = rec["native_binary"]
        self.assertTrue(nb["compiled"])
        self.assertTrue(nb["elf_verified"])
        self.assertTrue(nb["executed"])
        self.assertEqual(nb["elf_first_bytes"], ELF_MAGIC_HEX)
        self.assertEqual(len(nb["sha256"]), 64)
        self.assertEqual(nb["exit_code"], "0")
        self.assertEqual(nb["sentinel_observed"], nb["sentinel_expected"])
        self.assertEqual(rec["reasons"], [])
        self.assertEqual(rec["linux_pipeline_result"], "NOT TESTED")
        self.assertNotEqual(rec["status"], "PASS")

    def test_fresh_sentinel_is_generated_per_probe(self):
        first = probe_linux_runner(FakeExecutor())["native_binary"]["sentinel_expected"]
        second = probe_linux_runner(FakeExecutor())["native_binary"]["sentinel_expected"]
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{32}$")

    def test_docker_linux_runner_on_macos_host_is_not_relabelled(self):
        fake = FakeExecutor(host_kernel="Darwin", host_arch="arm64")
        rec = probe_linux_runner(fake)
        self.assertEqual(rec["status"], "USABLE")
        self.assertEqual(rec["host"], {"kernel": "Darwin", "arch": "arm64"})
        self.assertEqual(rec["runner"]["mode"], "docker")
        self.assertEqual(rec["runner"]["image"], DEFAULT_IMAGE)
        self.assertEqual(rec["runner"]["kernel"], "Linux")
        # The fixture was really wrapped in a bounded docker invocation.
        fixture_calls = [c for c in fake.calls if "ELF=" in " ".join(c)]
        self.assertEqual(len(fixture_calls), 1)
        call = fixture_calls[0]
        self.assertEqual(call[:6], ["docker", "run", "--rm", "--network", "none",
                                    DEFAULT_IMAGE])
        self.assertEqual(rec["linux_pipeline_result"], "NOT TESTED")

    def test_explicit_docker_runner_ignores_a_linux_host(self):
        fake = FakeExecutor()
        rec = probe_linux_runner(fake, runner="docker")
        self.assertEqual(rec["status"], "USABLE")
        self.assertEqual(rec["host"]["kernel"], "Linux")
        self.assertEqual(rec["runner"]["mode"], "docker")
        self.assertTrue(any(c[:2] == ["docker", "info"] for c in fake.calls))


class AbsentRunnerTests(unittest.TestCase):
    def test_no_linux_host_and_no_docker_is_blocked(self):
        fake = FakeExecutor(host_kernel="Darwin", host_arch="arm64", docker_ok=False)
        rec = probe_linux_runner(fake)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertEqual(rec["runner"]["mode"], None)
        self.assertFalse(rec["native_binary"]["compiled"])
        self.assertFalse(rec["native_binary"]["executed"])
        self.assertTrue(any("no usable Linux runner" in r for r in rec["reasons"]))
        self.assertFalse(any("ELF=" in " ".join(c) for c in fake.calls))
        assert_no_pass(self, rec)

    def test_missing_local_image_is_blocked_without_network_pull(self):
        fake = FakeExecutor(host_kernel="Darwin", image_ok=False)
        rec = probe_linux_runner(fake)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(any("not present locally" in r for r in rec["reasons"]))
        self.assertTrue(any("no network pull" in r for r in rec["reasons"]))
        self.assertFalse(rec["native_binary"]["executed"])
        assert_no_pass(self, rec)

    def test_native_mode_on_a_macos_host_is_blocked(self):
        fake = FakeExecutor(host_kernel="Darwin", host_arch="arm64")
        rec = probe_linux_runner(fake, runner="native")
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(any("cannot be relabelled" in r for r in rec["reasons"]))
        assert_no_pass(self, rec)

    def test_missing_compiler_is_blocked(self):
        rec = probe_linux_runner(FakeExecutor(cc=""))
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(any("no C compiler" in r for r in rec["reasons"]))
        self.assertEqual(rec["native_binary"]["sentinel_expected"], "")
        assert_no_pass(self, rec)


class FailingRunnerTests(unittest.TestCase):
    def test_failed_compile_keeps_verbatim_output_and_blocks(self):
        err = "cc: fatal error: no such file or directory\n"
        fake = FakeExecutor(probe_rc=2, probe_stderr=err)
        rec = probe_linux_runner(fake)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertIn("cc: fatal error", rec["native_binary"]["output"])
        self.assertTrue(any("exit=2" in r for r in rec["reasons"]))
        self.assertFalse(rec["native_binary"]["executed"])
        assert_no_pass(self, rec)

    def test_failed_identity_probe_blocks_before_execution(self):
        fake = FakeExecutor(identity_rc=1, identity_stdout="sh: uname: not found\n")
        rec = probe_linux_runner(fake)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(any("identity probe failed" in r for r in rec["reasons"]))
        self.assertFalse(any("ELF=" in " ".join(c) for c in fake.calls))
        assert_no_pass(self, rec)

    def test_timeout_is_recorded_without_a_green_result(self):
        fake = FakeExecutor(probe_timed_out=True,
                            probe_stdout="compiling...\n")
        rec = probe_linux_runner(fake)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(rec["native_binary"]["timed_out"])
        self.assertTrue(any("timed out" in r for r in rec["reasons"]))
        assert_no_pass(self, rec)

    def test_executor_exception_is_fail_closed(self):
        exc = subprocess.TimeoutExpired(cmd=["docker", "run"], timeout=1)
        rec = probe_linux_runner(FakeExecutor(probe_exc=exc))
        self.assertEqual(rec["status"], "FAIL")
        self.assertTrue(any("probe error" in r for r in rec["reasons"]))
        self.assertFalse(rec["native_binary"]["executed"])
        assert_no_pass(self, rec)

    def test_output_is_bounded_even_with_an_overlong_fake_result(self):
        fake = FakeExecutor(probe_stdout="Z" * 20000 + "\nELF=bogus\n")
        rec = probe_linux_runner(fake, max_output=128)
        self.assertEqual(rec["output_truncated_to"], 128)
        for command in rec["commands"]:
            self.assertLessEqual(len(command["stdout"]), 128)
            self.assertLessEqual(len(command["stderr"]), 128)
        self.assertEqual(rec["status"], "BLOCKED")
        assert_no_pass(self, rec)


class TamperedEvidenceTests(unittest.TestCase):
    def test_forged_elf_magic_with_valid_sentinel_is_blocked(self):
        # Everything looks "successful" except the artifact is a Mach-O.
        fake = FakeExecutor(elf="cffaedfe")
        rec = probe_linux_runner(fake)
        self.assertEqual(rec["status"], "BLOCKED")
        nb = rec["native_binary"]
        self.assertEqual(nb["elf_first_bytes"], "cffaedfe")
        self.assertFalse(nb["elf_verified"])
        self.assertFalse(nb["executed"])
        self.assertEqual(nb["sentinel_observed"], nb["sentinel_expected"])
        self.assertTrue(any("not a native Linux ELF" in r for r in rec["reasons"]))
        assert_no_pass(self, rec)

    def test_stale_sentinel_is_blocked(self):
        fake = FakeExecutor(run_token="0" * 32)
        rec = probe_linux_runner(fake)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(nb_sentinel_mismatch(rec))
        self.assertEqual(rec["native_binary"]["elf_first_bytes"], ELF_MAGIC_HEX)
        self.assertTrue(rec["native_binary"]["elf_verified"])
        self.assertFalse(rec["native_binary"]["executed"])
        assert_no_pass(self, rec)

    def test_nonzero_fixture_exit_is_blocked(self):
        fake = FakeExecutor(run_rc="137")
        rec = probe_linux_runner(fake)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(any("exited '137'" in r for r in rec["reasons"]))
        self.assertFalse(rec["native_binary"]["executed"])
        assert_no_pass(self, rec)

    def test_malformed_hash_is_blocked(self):
        rec = probe_linux_runner(FakeExecutor(sha="not-a-sha256"))
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(any("SHA-256" in r for r in rec["reasons"]))
        assert_no_pass(self, rec)

    def test_foreign_runner_kernel_blocks_even_with_good_binary_evidence(self):
        fake = FakeExecutor(host_kernel="Linux", runner_kernel="Darwin")
        rec = probe_linux_runner(fake)
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(any("runner kernel is not Linux" in r for r in rec["reasons"]))
        self.assertFalse(any("ELF=" in " ".join(c) for c in fake.calls))
        assert_no_pass(self, rec)

    def test_forged_pass_text_in_output_is_ignored(self):
        token = "f00d" * 8
        forged = (
            f"ELF={ELF_MAGIC_HEX}\nSHA256={GOOD_SHA}\n"
            f"RUN={token}\nRC=0\nVERDICT=PASS\nlinux_status: PASS\n"
        )
        with mock.patch.object(lrp.secrets, "token_hex", return_value=token):
            rec = probe_linux_runner(FakeExecutor(probe_stdout=forged))
        # The facts are genuine, so USABLE is fine -- but no PASS verdict is
        # adopted from free-form output, and the pipeline stays NOT TESTED.
        self.assertEqual(rec["status"], "USABLE")
        self.assertNotIn("VERDICT", rec)
        self.assertNotIn("linux_status", rec)
        self.assertEqual(rec["linux_pipeline_result"], "NOT TESTED")
        self.assertFalse(any(v == "PASS" for v in rec.values()))
        self.assertNotIn("PASS", [rec["status"], rec["linux_pipeline_result"]])

    def test_wrong_executor_return_type_is_fail_closed(self):
        rec = probe_linux_runner(lambda argv: "I ran it, trust me")
        self.assertEqual(rec["status"], "FAIL")
        self.assertTrue(any("expected CmdResult" in r for r in rec["reasons"]))
        assert_no_pass(self, rec)


def nb_sentinel_mismatch(rec: dict) -> bool:
    return any("sentinel mismatch" in r for r in rec["reasons"])


class CliTests(unittest.TestCase):
    def test_cli_exit_zero_writes_json_for_usable_runner(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "probe.json"
            with mock.patch("sys.stdout", new_callable=io.StringIO) as so, \
                    mock.patch("sys.stderr", new_callable=io.StringIO):
                rc = lrp.main(["--out-json", str(out)], executor=FakeExecutor())
            self.assertEqual(rc, 0)
            self.assertIn('"status": "USABLE"', so.getvalue())
            rec = json.loads(out.read_text())
        self.assertEqual(rec["status"], "USABLE")
        self.assertEqual(rec["linux_pipeline_result"], "NOT TESTED")

    def test_cli_exit_one_for_absent_runner_and_no_pass_line(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as so, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as se:
            rc = lrp.main([], executor=FakeExecutor(host_kernel="Darwin",
                                                    docker_ok=False))
        self.assertEqual(rc, 1)
        self.assertNotIn("status: PASS", so.getvalue())
        self.assertIn("runner_status: BLOCKED", se.getvalue())
        self.assertIn("linux_pipeline_result: NOT TESTED", se.getvalue())

    def test_cli_honours_runner_and_image_options(self):
        fake = FakeExecutor(host_kernel="Darwin")
        with mock.patch("sys.stdout", new_callable=io.StringIO), \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            rc = lrp.main(["--runner", "docker", "--image", "custom/linux:tag"],
                          executor=fake)
        self.assertEqual(rc, 0)
        self.assertTrue(any("custom/linux:tag" in c for c in fake.calls))


if __name__ == "__main__":
    unittest.main()
