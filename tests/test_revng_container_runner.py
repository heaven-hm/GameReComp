"""The rev.ng container runner must fail closed and never manufacture a PASS.

These tests inject a fake command runner, so no Docker daemon, network or image
is needed. They pin the safety property that matters for issue #25: a blocked
attempt is reported with the exact reason, and even a successful container run
is only an ATTEMPT -- a differential verdict has to come from difftest.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import revng_container_runner as runner  # noqa: E402
from tools.revng_acquire_guide import PINNED_IMAGE  # noqa: E402


class FakeRunner:
    """Record every argv and return canned outcomes; no process is spawned."""

    def __init__(self, *, inspect_rc=1, inspect_digests=(), disk_ok=True,
                 command_rc=0):
        self.calls: list[tuple[list[str], int]] = []
        self.inspect_rc = inspect_rc
        self.inspect_digests = list(inspect_digests)
        self.disk_ok = disk_ok
        self.command_rc = command_rc

    def __call__(self, argv, timeout=60):
        self.calls.append((list(argv), timeout))
        argv = list(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            if self.inspect_rc == 0:
                payload = json.dumps(self.inspect_digests)
                return runner.CommandResult(argv, 0, f"abc123|{payload}|1000\n", "")
            return runner.CommandResult(argv, self.inspect_rc, "",
                                        "Error response from daemon: No such image\n")
        if "pull" in argv:
            return runner.CommandResult(
                argv, self.command_rc, "", "network stall" if self.command_rc else "")
        return runner.CommandResult(argv, self.command_rc, "revng output\n", "")


def _set_free(monkeypatch, gib):
    """Pin the disk reading instead of touching the real host filesystem."""
    class Usage:
        free = int(gib * 1024 ** 3)
        total = 40 * 1024 ** 3
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _p: Usage())


def test_pin_is_the_acquisition_guide_pin():
    assert runner.PINNED_IMAGE == PINNED_IMAGE
    assert PINNED_IMAGE.startswith("revng/revng@sha256:")


def test_disk_guard_refuses_below_floor(monkeypatch):
    _set_free(monkeypatch, 3)
    disk = runner.disk_state(ROOT)
    allowed, reason = runner.disk_guard(disk)
    assert allowed is False
    assert "refusing to start a pull" in reason
    assert "3.0 GiB" in reason

    _set_free(monkeypatch, 8)
    assert runner.disk_guard(runner.disk_state(ROOT)) == (True, None)


def test_default_mode_never_runs_pull_or_container(monkeypatch):
    _set_free(monkeypatch, 3)
    fake = FakeRunner()
    report = runner.attempt(runner=fake)
    assert report["verdict"] == "NO_REVNG_PASS"
    assert report["decision"] == "BLOCKED"
    assert "DISK_GUARD_BLOCKED" in report["reason_codes"]
    assert "PULL_NOT_AUTHORISED" in report["reason_codes"]
    assert fake.calls, "host version/inspection commands should still run"
    assert not any("pull" in argv or "run" in argv for argv, _ in fake.calls)


def test_missing_pinned_image_is_recorded_not_raised(monkeypatch):
    _set_free(monkeypatch, 8)
    fake = FakeRunner(inspect_rc=1)
    report = runner.attempt(runner=fake)
    assert "PINNED_IMAGE_NOT_USABLE" in report["reason_codes"]
    assert report["host"]["image"]["usable"] is False
    assert "No such image" in report["host"]["image"]["inspect_stderr_tail"]


def test_allow_pull_is_still_refused_when_disk_is_low(monkeypatch):
    _set_free(monkeypatch, 3)
    fake = FakeRunner()
    report = runner.attempt(allow_pull=True, runner=fake)
    assert report["pull"] is None
    assert not any("pull" in argv for argv, _ in fake.calls)


def test_successful_container_run_is_not_a_pass(monkeypatch):
    _set_free(monkeypatch, 8)
    digest = PINNED_IMAGE.split("@", 1)[1]
    fake = FakeRunner(inspect_rc=0, inspect_digests=[f"revng/revng@{digest}"])
    report = runner.attempt(allow_pull=True, runner=fake)
    assert report["decision"] == "ATTEMPTED"
    assert report["verdict"] == "NO_REVNG_PASS"
    assert report["fixture"]["pe_sha256"]
    assert any("pull" in argv for argv, _ in fake.calls)
    assert not report["reason_codes"]


def test_cli_writes_json_without_closing_keywords(monkeypatch, tmp_path):
    _set_free(monkeypatch, 3)
    monkeypatch.setattr(runner, "run_command", FakeRunner())
    out = tmp_path / "attempt.json"
    assert runner.main(["--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["schema_version"] == runner.SCHEMA_VERSION
    assert data["verdict"] == "NO_REVNG_PASS"
    assert data["decision"] == "BLOCKED"
    assert not runner.CLOSING_KEYWORD.search(out.read_text())
