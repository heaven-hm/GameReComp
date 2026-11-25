"""Deterministic tests for the Windows->Linux runner capability matrix.

Run with: python3 -m pytest tests/test_linux_runner_matrix.py -q

Every runner record is built in-memory or written under tmp_path; no test
probes the host, launches a command or reads a game/evidence path. The cases
prove the evidence gates, fail-closed tampering handling, integrity checking
and the absence of any fake Linux PASS.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

from tools import linux_runner_matrix as lrm

FIXED_TIME = "2026-11-25T00:00:00Z"
GOOD_SHA = "a" * 64
OTHER_SHA = "b" * 64
GOOD_TOKEN = "a1b2c3d4e5f60718"


def make_record(
    runner: str = "docker",
    *,
    claim: str = "USABLE",
    execution: dict | None = None,
    native_binary: dict | None = None,
    extra: dict | None = None,
    omit_execution: bool = False,
    omit_native_binary: bool = False,
) -> dict:
    rec: dict = {
        "runner": runner,
        "claimed_status": claim,
        "execution": {
            "executed": True,
            "argv": ["docker", "run", "linux-runner", "sh", "-c", "uname -a"],
            "exit_code": 0,
            "kernel": "Linux",
            "arch": "x86_64",
            "timed_out": False,
        },
        "native_binary": {
            "sha256": GOOD_SHA,
            "elf_magic": "7f454c46",
            "executed": True,
            "exit_code": 0,
            "sentinel_expected": GOOD_TOKEN,
            "sentinel_observed": GOOD_TOKEN,
        },
        "verified": True,
    }
    if omit_execution:
        rec.pop("execution")
    else:
        rec["execution"].update(execution or {})
    if omit_native_binary:
        rec.pop("native_binary")
    else:
        rec["native_binary"].update(native_binary or {})
    rec.update(extra or {})
    return rec


def row_of(matrix: dict, runner: str) -> dict:
    for row in matrix["runners"]:
        if row["runner"] == runner:
            return row
    raise AssertionError(f"no row for {runner!r} in {[r['runner'] for r in matrix['runners']]}")


def assert_no_fake_pass(node: object) -> None:
    """No value anywhere may be an unqualified PASS, at any nesting depth."""
    if isinstance(node, dict):
        for value in node.values():
            assert_no_fake_pass(value)
    elif isinstance(node, list):
        for value in node:
            assert_no_fake_pass(value)
    elif isinstance(node, str):
        assert node.strip().upper() != "PASS", f"fake PASS found: {node!r}"


def assert_honest(matrix: dict) -> None:
    assert matrix["linux_work_result"] == "NOT TESTED"
    assert matrix["not_a_linux_pass"] is True
    assert matrix["no_local_probing"] is True
    assert matrix["claimed_status_is_advisory"] is True
    assert matrix["direction"] == "Windows->Linux"
    for row in matrix["runners"]:
        assert row["status"] in lrm.STATUSES
    assert_no_fake_pass(matrix)


class TestNoLocalProbing:
    def test_tool_does_not_import_subprocess_or_sockets(self):
        assert not hasattr(lrm, "subprocess")
        assert not hasattr(lrm, "socket")
        assert not hasattr(lrm, "make_real_executor")

    def test_matrix_and_cli_never_launch_a_command(self, tmp_path: Path):
        guards = (
            mock.patch("subprocess.run", side_effect=AssertionError("command launched")),
            mock.patch("subprocess.Popen", side_effect=AssertionError("command launched")),
            mock.patch("os.system", side_effect=AssertionError("command launched")),
        )
        with guards[0], guards[1], guards[2]:
            matrix = lrm.build_matrix([make_record()])
            out = tmp_path / "matrix.json"
            rc = lrm.main([
                "--record", json.dumps([make_record("ci"), make_record("native")]),
                "--out", str(out),
            ])
        assert matrix["summary"]["USABLE"] == 1
        assert rc == 0
        assert out.exists()

    def test_injected_record_is_the_only_observation_source(self):
        matrix = lrm.build_matrix([make_record("lima")])
        row = row_of(matrix, "lima")
        assert row["evidence"]["argv"] == make_record("lima")["execution"]["argv"]
        assert row["evidence"]["kernel"] == "Linux"
        assert row["evidence"]["native_elf_sha256"] == GOOD_SHA


class TestStatusComputation:
    @pytest.mark.parametrize("runner", ["docker", "colima", "lima", "native", "ci"])
    def test_full_evidence_makes_each_runner_usable(self, runner):
        matrix = lrm.build_matrix([make_record(runner)], generated_at=FIXED_TIME)
        row = row_of(matrix, runner)
        assert row["status"] == "USABLE"
        assert all(row["checks"].values())
        assert row["reasons"] == []
        assert matrix["summary"]["USABLE"] == 1
        assert matrix["generated_at"] == FIXED_TIME
        assert_honest(matrix)

    def test_no_evidence_is_not_tested_with_explicit_reason(self):
        matrix = lrm.build_matrix([{"runner": "docker", "claimed_status": "USABLE"}])
        row = row_of(matrix, "docker")
        assert row["status"] == "NOT TESTED"
        assert any("no Linux runner evidence" in reason for reason in row["reasons"])
        assert matrix["summary"]["NOT TESTED"] == 1
        assert_honest(matrix)

    def test_elf_without_execution_is_blocked(self):
        matrix = lrm.build_matrix([
            make_record(omit_execution=True),
        ])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert row["checks"]["linux_command_executed"] is False
        assert any("no command was recorded" in r for r in row["reasons"])

    def test_execution_without_elf_hash_is_blocked(self):
        matrix = lrm.build_matrix([make_record(omit_native_binary=True)])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert row["checks"]["native_elf_hash_verified"] is False
        assert any("SHA-256" in r for r in row["reasons"])

    @pytest.mark.parametrize("kernel", ["Darwin", "MacOS", "Windows", "", "GNU/Linux-x"])
    def test_foreign_or_missing_kernel_never_becomes_usable(self, kernel):
        matrix = lrm.build_matrix([make_record(execution={"kernel": kernel})])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert row["checks"]["kernel_arch_recorded"] is False
        assert any("not recorded as Linux" in r for r in row["reasons"])
        assert any("foreign host" in r for r in row["reasons"])
        assert_honest(matrix)

    def test_blank_execution_kernel_is_not_bypassed_by_top_level_kernel(self):
        """A blank recorded kernel must not inherit a friendlier claim above it."""
        matrix = lrm.build_matrix([
            make_record(execution={"kernel": ""}, extra={"kernel": "Linux"}),
        ])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert row["checks"]["kernel_arch_recorded"] is False

    def test_missing_arch_is_blocked(self):
        matrix = lrm.build_matrix([make_record(execution={"arch": ""})])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert any("architecture was not recorded" in r for r in row["reasons"])

    @pytest.mark.parametrize("exit_code", [1, 127, None, "error"])
    def test_nonzero_command_exit_is_blocked(self, exit_code):
        matrix = lrm.build_matrix([make_record(execution={"exit_code": exit_code})])
        assert row_of(matrix, "docker")["status"] == "BLOCKED"

    def test_timed_out_command_is_blocked(self):
        matrix = lrm.build_matrix([make_record(execution={"timed_out": True})])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert any("timed out" in r for r in row["reasons"])

    def test_missing_argv_is_blocked(self):
        matrix = lrm.build_matrix([make_record(execution={"argv": []})])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert any("argv" in r for r in row["reasons"])

    @pytest.mark.parametrize("bad_hash", ["deadbeef", "z" * 64, GOOD_SHA + "00", 123, ""])
    def test_forged_or_malformed_elf_hash_is_blocked(self, bad_hash):
        matrix = lrm.build_matrix([
            make_record(native_binary={"sha256": bad_hash}),
        ])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert row["checks"]["native_elf_hash_verified"] is False
        assert any("SHA-256" in r for r in row["reasons"])

    def test_wrong_elf_magic_is_blocked_as_tampering(self):
        matrix = lrm.build_matrix([
            make_record(native_binary={"elf_magic": "89504e47"}),
        ])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert any("forged/tampered" in r for r in row["reasons"])

    def test_elf_not_recorded_as_executed_is_blocked(self):
        matrix = lrm.build_matrix([
            make_record(native_binary={"executed": False}),
        ])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert row["checks"]["native_elf_executed"] is False

    def test_sentinel_mismatch_is_blocked_as_tampering(self):
        matrix = lrm.build_matrix([
            make_record(native_binary={"sentinel_observed": "ffffffffffffffff"}),
        ])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert any("sentinel mismatch" in r for r in row["reasons"])

    def test_declared_verification_and_claim_never_grant_usable(self):
        record = {
            "runner": "docker",
            "status": "USABLE",
            "verified": True,
            "linux_pipeline_result": "PASS",
            "summary": "PASS",
        }
        matrix = lrm.build_matrix([record])
        row = row_of(matrix, "docker")
        assert row["status"] == "NOT TESTED"
        assert row["claimed_statuses"] == ["USABLE"]
        assert_honest(matrix)

    def test_claim_is_recorded_but_ignored_when_gates_fail(self):
        matrix = lrm.build_matrix([make_record(execution={"kernel": "Darwin"})])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert any("claimed status" in r and "ignored" in r for r in row["reasons"])

    def test_supported_runner_aliases_map_onto_the_matrix(self):
        matrix = lrm.build_matrix([
            make_record("github-actions"), make_record("linux-native"),
        ])
        assert {row["runner"] for row in matrix["runners"]} == {"ci", "native"}
        assert matrix["summary"]["USABLE"] == 2


class TestAggregationAndTampering:
    def test_conflicting_records_fail_closed(self):
        matrix = lrm.build_matrix([
            make_record("docker"),
            make_record("docker", execution={"kernel": "Darwin"}),
        ])
        row = row_of(matrix, "docker")
        assert row["status"] == "BLOCKED"
        assert row["records_seen"] == 2
        assert any("conflicting records" in r for r in row["reasons"])
        assert_honest(matrix)

    def test_agreeing_records_stay_usable(self):
        matrix = lrm.build_matrix([make_record("docker"), make_record("docker")])
        row = row_of(matrix, "docker")
        assert row["status"] == "USABLE"
        assert row["records_seen"] == 2
        assert row["reasons"] == []

    def test_unknown_runner_kind_is_not_tested_with_reason(self):
        matrix = lrm.build_matrix([make_record("wine")])
        row = matrix["runners"][0]
        assert row["runner"] == "unknown:wine"
        assert row["status"] == "NOT TESTED"
        assert any("unrecognised runner kind" in r for r in row["reasons"])

    def test_integrity_digest_is_stable_for_fixed_input(self):
        first = lrm.build_matrix([make_record()], generated_at=FIXED_TIME)
        second = lrm.build_matrix([make_record()], generated_at=FIXED_TIME)
        assert first == second
        assert len(first["integrity"]["sha256"]) == 64
        assert first["integrity"]["sha256"] == second["integrity"]["sha256"]


class TestCLI:
    def test_inline_and_file_records_are_deterministic(self, tmp_path: Path):
        source = tmp_path / "runners.json"
        source.write_text(json.dumps({
            "runners": [make_record("colima"), make_record("ci")],
        }), encoding="utf-8")
        out1 = tmp_path / "one.json"
        out2 = tmp_path / "two.json"
        md = tmp_path / "matrix.md"
        args = [
            "--records", str(source),
            "--record", json.dumps(make_record("lima")),
            "--generated-at", FIXED_TIME,
        ]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            assert lrm.main(args + ["--out", str(out1), "--markdown", str(md)]) == 0
            assert lrm.main(args + ["--out", str(out2)]) == 0
        assert out1.read_bytes() == out2.read_bytes()
        matrix = json.loads(out1.read_text(encoding="utf-8"))
        assert {row["runner"] for row in matrix["runners"]} == {"ci", "colima", "lima"}
        assert "| runner | status |" in md.read_text(encoding="utf-8")
        assert_honest(matrix)

    def test_verify_accepts_clean_and_rejects_edited_report(self, tmp_path: Path):
        out = tmp_path / "matrix.json"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            assert lrm.main([
                "--record", json.dumps(make_record("native")),
                "--generated-at", FIXED_TIME, "--out", str(out),
            ]) == 0
            assert lrm.main(["--verify", str(out)]) == 0
        doc = json.loads(out.read_text(encoding="utf-8"))
        doc["runners"][0]["status"] = "USABLE" if doc["runners"][0]["status"] != "USABLE" else "BLOCKED"
        out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with redirect_stderr(io.StringIO()) as err:
            assert lrm.main(["--verify", str(out)]) == 1
        assert "integrity mismatch" in err.getvalue()

    def test_verify_rejects_invariant_tamper_even_with_recomputed_digest(self, tmp_path: Path):
        """A tamperer who recomputes the digest still cannot fake a Linux pass."""
        out = tmp_path / "tampered.json"
        doc = lrm.build_matrix([make_record()], generated_at=FIXED_TIME)
        doc["linux_work_result"] = "PASS"
        bare = {k: v for k, v in doc.items() if k != "integrity"}
        doc["integrity"]["sha256"] = lrm.sha256_bytes(lrm.canonical_bytes(bare))
        out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        with redirect_stderr(io.StringIO()) as err:
            assert lrm.main(["--verify", str(out)]) == 1
        assert "honesty invariant" in err.getvalue()
        assert_no_fake_pass(doc["runners"])

    @pytest.mark.parametrize("args", [
        ["--record", "{not json}"],
        ["--records", "does-not-exist.json"],
        ["--record", json.dumps(make_record()), "--records", "missing.json"],
    ])
    def test_invalid_input_exits_nonzero(self, args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            assert lrm.main(args) == 1

    def test_no_records_is_rejected(self):
        with redirect_stderr(io.StringIO()) as err:
            assert lrm.main([]) == 1
        assert "no runner records" in err.getvalue()

    def test_all_blocked_still_exits_zero_but_never_claims_a_pass(self, tmp_path: Path):
        out = tmp_path / "matrix.json"
        buf = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(buf):
            rc = lrm.main([
                "--record", json.dumps(make_record(execution={"kernel": "Darwin"})),
                "--record", json.dumps({"runner": "ci"}),
                "--out", str(out),
            ])
        assert rc == 0
        assert "linux_work_result: NOT TESTED" in buf.getvalue()
        assert "PASS" not in buf.getvalue()
        assert_honest(json.loads(out.read_text(encoding="utf-8")))

    def test_cli_invocation_via_subprocess_is_not_used_by_the_tool(self):
        # Guard the module surface: the CLI is driven in-process everywhere else.
        assert "subprocess" not in vars(lrm)
        assert "Popen" not in dir(lrm)
