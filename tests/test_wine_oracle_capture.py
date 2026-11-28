"""Deterministic Highball oracle capture, proven with a FAKE runner only.

No test in this module starts Wine or the real ``highball`` binary.  The process
runner is injected as a callable returning a canned ``RunOutcome`` (or raising
a canned error), so BLOCKED / success / tamper / timeout behaviour is tested
with zero external dependencies.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import wine_oracle_capture as woc  # noqa: E402


class FakeRunner:
    """Injectable stand-in for ``run_highball``; records every invocation."""

    def __init__(self, outcome=None, exc=None):
        self.outcome = outcome
        self.exc = exc
        self.calls = []

    def __call__(self, command, *, stdin, timeout, cwd):
        self.calls.append({"command": list(command), "stdin": stdin,
                           "timeout": timeout, "cwd": cwd})
        if self.exc is not None:
            raise self.exc
        return self.outcome


BANNER = ("Launching arith-calc.exe in bottle 'Games' "
          "[engine: x64-crossover26.3-r27, renderer: dxmt]...\n").encode()
EXIT = "exit=0 after 2s — log: /tmp/hb.log\n".encode()
GUEST = ("\x1b[36marith:start\x1b[0m\nfib(20)=6765\narith:done\n").encode()
STDERR = ("freetype: cannot load font\n"
          "msync: up and running\n"
          "real guest warning\n").encode()
EXPECTED_GUEST = "arith:start\nfib(20)=6765\narith:done\nreal guest warning\n"


def _exe(tmp_path: Path, name="arith-calc.exe", data=b"MZfake-pe-bytes") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_success_records_exact_command_hashes_and_normalized_output(tmp_path):
    exe = _exe(tmp_path)
    stdin = b"1\nhello\n3\n"
    runner = FakeRunner(woc.RunOutcome(0, BANNER + GUEST + EXIT, STDERR, False))
    out = tmp_path / "capture"

    rec = woc.capture(exe, args=["--flag", "value"], stdin=stdin, out_dir=out,
                      timeout=7, bottle="Games", highball="/fake/highball",
                      runner=runner)

    assert rec["status"] == "OK"
    assert rec["blocked_class"] is None and rec["reason"] is None
    assert rec["command"] == ["/fake/highball", "run", "-b", "Games",
                              str(exe), "--flag", "value"]
    assert rec["command_display"] == " ".join([
        "/fake/highball", "run", "-b", "Games", str(exe), "--flag", "value"])
    assert rec["exit_code"] == 0 and rec["timed_out"] is False
    assert rec["exe_sha256"] == hashlib.sha256(b"MZfake-pe-bytes").hexdigest()
    assert rec["limits"].startswith("console scenarios only")

    # The fake runner saw exactly the command/stdin/timeout/cwd we advertise.
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["command"] == rec["command"]
    assert call["stdin"] == stdin and call["timeout"] == 7
    assert call["cwd"] == str(exe.parent)

    # Raw bytes are persisted and hashed from the real file contents.
    assert (out / woc.STDOUT_RAW).read_bytes() == BANNER + GUEST + EXIT
    assert (out / woc.STDERR_RAW).read_bytes() == STDERR
    assert (out / woc.STDIN_RAW).read_bytes() == stdin
    for key, path in (("stdout", woc.STDOUT_RAW), ("stderr", woc.STDERR_RAW),
                      ("stdin", woc.STDIN_RAW)):
        meta = rec["raw"][key]
        assert meta["sha256"] == hashlib.sha256((out / path).read_bytes()).hexdigest()
        assert meta["bytes"] == len((out / path).read_bytes())

    assert rec["guest_output"] == EXPECTED_GUEST
    assert (out / woc.GUEST_OUTPUT).read_text(encoding="utf-8") == EXPECTED_GUEST
    assert rec["guest_output_sha256"] == hashlib.sha256(
        EXPECTED_GUEST.encode()).hexdigest()
    assert woc.verify_capture(out) == {
        "ok": True, "problems": [], "status": "OK"}


def test_blocked_when_highball_missing_never_calls_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(woc, "find_highball", lambda: None)
    runner = FakeRunner(woc.RunOutcome(0, GUEST, b"", False))

    rec = woc.capture(_exe(tmp_path), out_dir=tmp_path / "out", highball=None,
                      runner=runner)

    assert rec["status"] == "BLOCKED"
    assert rec["blocked_class"] == "highball-missing"
    assert "RECOMP_HIGHBALL" in rec["reason"]
    assert rec["command"] is None
    assert runner.calls == []  # fail closed before any process run


def test_blocked_on_timeout_keeps_partial_output_unclaimed(tmp_path):
    runner = FakeRunner(woc.RunOutcome(None, b"partial guest line\n", b"", True))
    out = tmp_path / "out"

    rec = woc.capture(_exe(tmp_path), out_dir=out, timeout=3,
                      highball="/fake/highball", runner=runner)

    assert rec["status"] == "BLOCKED"
    assert rec["blocked_class"] == "timeout"
    assert rec["timed_out"] is True and rec["exit_code"] is None
    assert "within 3s" in rec["reason"]
    assert (out / woc.STDOUT_RAW).read_bytes() == b"partial guest line\n"
    assert rec["raw"]["stdout"]["sha256"] == hashlib.sha256(
        b"partial guest line\n").hexdigest()
    assert woc.verify_capture(out)["ok"] is True


@pytest.mark.parametrize("outcome,expected_class", [
    (woc.RunOutcome(1, b"error: no bottles found in /tmp/bottles\n", b"", False),
     "highball-unavailable"),
])
def test_blocked_when_highball_reports_unavailable(tmp_path, outcome, expected_class):
    runner = FakeRunner(outcome)
    rec = woc.capture(_exe(tmp_path), out_dir=tmp_path / "out",
                      highball="/fake/highball", runner=runner)
    assert rec["status"] == "BLOCKED"
    assert rec["blocked_class"] == expected_class
    assert "could not run" in rec["reason"]


def test_blocked_when_runner_cannot_start_highball(tmp_path):
    runner = FakeRunner(exc=woc.HighballUnavailable("cannot start highball: boom"))
    rec = woc.capture(_exe(tmp_path), out_dir=tmp_path / "out",
                      highball="/fake/highball", runner=runner)
    assert rec["status"] == "BLOCKED"
    assert rec["blocked_class"] == "highball-unavailable"
    assert "boom" in rec["reason"]


def test_hash_tamper_on_raw_file_is_detected(tmp_path):
    runner = FakeRunner(woc.RunOutcome(0, GUEST, b"", False))
    out = tmp_path / "out"
    woc.capture(_exe(tmp_path), out_dir=out, highball="/fake/highball",
                runner=runner)
    assert woc.verify_capture(out)["ok"] is True

    # Append a byte to the captured stdout: manifest hash no longer matches.
    with (out / woc.STDOUT_RAW).open("ab") as fh:
        fh.write(b"X")
    result = woc.verify_capture(out)
    assert result["ok"] is False
    assert "stdout sha256 mismatch" in result["problems"]
    assert "stdout byte-count mismatch" in result["problems"]


def test_hash_tamper_on_manifest_is_detected(tmp_path):
    runner = FakeRunner(woc.RunOutcome(0, GUEST, b"", False))
    out = tmp_path / "out"
    woc.capture(_exe(tmp_path), out_dir=out, stdin=b"abc\n",
                highball="/fake/highball", runner=runner)
    manifest = out / woc.CAPTURE_JSON
    rec = json.loads(manifest.read_text(encoding="utf-8"))
    rec["raw"]["stdin"]["sha256"] = "0" * 64  # forged hash, raw bytes untouched
    manifest.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")

    result = woc.verify_capture(out)
    assert result["ok"] is False
    assert "stdin sha256 mismatch" in result["problems"]


def test_cli_capture_and_verify_use_only_the_fake_runner(tmp_path, monkeypatch):
    """Even the CLI entry point can be driven without touching real Wine."""
    exe = _exe(tmp_path)
    stdin_file = tmp_path / "input.txt"
    stdin_file.write_bytes(b"1\nhello world\n3\n")
    out = tmp_path / "out"
    runner = FakeRunner(woc.RunOutcome(0, BANNER + GUEST + EXIT, STDERR, False))
    monkeypatch.setattr(woc, "run_highball", runner)
    monkeypatch.setattr(woc, "find_highball", lambda: "/fake/highball")

    rc = woc.main(["--exe", str(exe), "--out", str(out),
                   "--stdin", str(stdin_file), "--highball", "/fake/highball"])
    assert rc == 0
    assert (out / woc.STDIN_RAW).read_bytes() == b"1\nhello world\n3\n"
    assert woc.verify_capture(out)["ok"] is True
    assert woc.main(["--verify", str(out)]) == 0

    # A BLOCKED capture exits non-zero so CI cannot mistake it for a run.
    monkeypatch.setattr(woc, "find_highball", lambda: None)
    blocked_out = tmp_path / "blocked"
    rc = woc.main(["--exe", str(exe), "--out", str(blocked_out)])
    assert rc == 1
    assert json.loads((blocked_out / woc.CAPTURE_JSON).read_text())["status"] == "BLOCKED"
