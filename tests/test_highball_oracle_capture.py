"""Fail-closed tests for the Highball/Wine Windows-side capture adapter (issue #145).

No Wine process is started: a fake endpoint is injected via ``--endpoint
module:attribute``.  Tests cover BLOCKED/FAILED paths, a genuine CAPTURED
console record, scope labelling (console-only never becomes GUI/frame),
acceptance-matrix rejection (no fake PASS), and hash-tamper detection.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import acceptance_matrix as am  # noqa: E402
from tools import highball_oracle_capture as ho  # noqa: E402

ENDPOINT_SPEC = f"{__name__}:FakeEndpoint"
STDOUT = b"Launching game in bottle Games [engine: x64-crossover26.3-r27 renderer: dxvk]...\n" \
         b"arith:start\nprimes<200=46\nfib(20)=6765\narith:done\n" \
         b"exit=0 after 1s . log: /tmp/game.log\n"
STDERR = b""
STDIN = b"hello world\n"
FRAME = b"\x89PNG\r\n\x1a\nfake-frame-bytes"


class FakeEndpoint:
    """Injectable endpoint whose behaviour is configured via class attributes."""

    healthy = True
    exit_code = 0
    timed_out = False
    stdout = STDOUT
    stderr = STDERR
    command = None
    requests: list = []

    def probe(self, request):
        FakeEndpoint.requests.append(("probe", request))
        return self.healthy, ("ok" if self.healthy else "fake health failure")

    def capture(self, request):
        FakeEndpoint.requests.append(("capture", request))
        command = self.command or [request.highball, "run", "-b", request.bottle,
                                   str(request.exe)]
        return ho.RunOutcome(command=list(command), exit_code=self.exit_code,
                             stdout=self.stdout, stderr=self.stderr,
                             timed_out=self.timed_out)


class UnhealthyEndpoint(FakeEndpoint):
    healthy = False


class TimeoutEndpoint(FakeEndpoint):
    timed_out = True
    exit_code = None


@pytest.fixture(autouse=True)
def reset_endpoint():
    FakeEndpoint.healthy = True
    FakeEndpoint.exit_code = 0
    FakeEndpoint.timed_out = False
    FakeEndpoint.stdout = STDOUT
    FakeEndpoint.stderr = STDERR
    FakeEndpoint.command = None
    FakeEndpoint.requests = []
    yield


@pytest.fixture
def paths(tmp_path):
    exe = tmp_path / "arith-calc.exe"
    native = tmp_path / "arith-calc-native"
    stdin = tmp_path / "input.txt"
    highball = tmp_path / "highball"
    exe.write_bytes(b"MZ fake windows executable")
    native.write_bytes(b"\x7fELF fake native")
    stdin.write_bytes(STDIN)
    highball.write_bytes(b"#!/bin/sh\nexit 0\n")
    return {
        "exe": exe, "native": native, "stdin": stdin, "highball": highball,
        "out": tmp_path / "capture.json",
        "frame": tmp_path / "frame.png",
    }


def args(paths, **overrides):
    base = {
        "exe": str(paths["exe"]), "out": str(paths["out"]),
        "native": str(paths["native"]),
        "highball": str(paths["highball"]), "bottle": "Games",
        "endpoint": ENDPOINT_SPEC, "stdin_file": str(paths["stdin"]),
        "game_id": "arith-calc", "scenario": "console-run", "timeout": 5,
    }
    base.update(overrides)
    argv = []
    for key, value in base.items():
        if value is None:
            continue
        argv += [f"--{key.replace('_', '-')}", str(value)]
    return argv


def invoke(paths, **overrides):
    return ho.main(args(paths, **overrides))


def read_record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Fail closed: no fake CAPTURED, no fabricated artifacts
# ---------------------------------------------------------------------------

def test_no_captured_record_without_healthy_endpoint(paths, capsys):
    code = ho.main(args(paths, endpoint=f"{__name__}:UnhealthyEndpoint"))
    assert code == ho.EXIT_BLOCKED
    assert "BLOCKED" in capsys.readouterr().err
    record = read_record(paths["out"])
    assert record["status"] == "BLOCKED"
    assert record["verification"] == "NOT TESTED"
    assert record["records"] == []
    for key in ("command", "exit_code", "exe", "native", "evidence_sha256"):
        assert record[key] is None
    assert record["raw"] == {}
    assert not (paths["out"].parent / f"{paths['out'].stem}-artifacts").exists()
    assert not (paths["out"].parent / f"{paths['out'].stem}-capture.json").exists()


def test_missing_executable_is_blocked_without_running_guest(paths):
    code = invoke(paths, exe=str(paths["exe"].parent / "missing.exe"))
    assert code == ho.EXIT_BLOCKED
    record = read_record(paths["out"])
    assert record["status"] == "BLOCKED"
    assert record["records"] == []
    assert FakeEndpoint.requests == []


def test_gui_frame_without_real_frames_is_blocked(paths):
    code = invoke(paths, scope="gui-frame")
    assert code == ho.EXIT_BLOCKED
    record = read_record(paths["out"])
    assert record["status"] == "BLOCKED"
    assert record["scope"] == "gui-frame"
    assert record["gui_frame_evidence"] is False
    assert record["records"] == []
    assert "frame" in record["reason"]


def test_missing_frame_file_is_blocked(paths):
    code = invoke(paths, scope="gui-frame",
                  frame=str(paths["frame"].parent / "no-frame.png"))
    assert code == ho.EXIT_BLOCKED
    assert read_record(paths["out"])["records"] == []


def test_missing_highball_is_blocked(paths):
    code = invoke(paths, highball=str(paths["exe"].parent / "no-highball"))
    # CLI-supplied highball does not exist: the endpoint probe rejects it.
    assert code == ho.EXIT_BLOCKED
    assert read_record(paths["out"])["status"] == "BLOCKED"


def test_timeout_is_failed_not_captured(paths, capsys):
    code = ho.main(args(paths, endpoint=f"{__name__}:TimeoutEndpoint"))
    assert code == ho.EXIT_FAILED
    assert "FAILED" in capsys.readouterr().err
    record = read_record(paths["out"])
    assert record["status"] == "FAILED"
    assert record["records"] == []
    assert record["exit_code"] is None


def test_bad_endpoint_spec_is_blocked(paths):
    code = invoke(paths, endpoint="not-a-spec")
    assert code == ho.EXIT_BLOCKED
    assert "endpoint error" in read_record(paths["out"])["reason"]


def test_verify_blocked_record_is_not_success(paths, capsys):
    assert invoke(paths, endpoint=f"{__name__}:UnhealthyEndpoint") == ho.EXIT_BLOCKED
    assert ho.main(["--verify", str(paths["out"])]) == ho.EXIT_FAILED
    assert "TAMPERED" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Genuine CAPTURED console record
# ---------------------------------------------------------------------------

def test_console_capture_records_exact_command_exit_and_hashes(paths, capsys):
    command = [str(paths["highball"]), "run", "-b", "Games", str(paths["exe"])]
    FakeEndpoint.command = command
    code = invoke(paths)
    assert code == ho.EXIT_OK, capsys.readouterr().out + capsys.readouterr().err

    record = read_record(paths["out"])
    assert record["status"] == "CAPTURED"
    assert record["verification"] == "CAPTURED"
    assert record["scope"] == "console-only"
    assert record["gui_frame_evidence"] is False
    assert record["oracle"] == ho.ORACLE_CONSOLE
    assert record["exit_code"] == 0
    assert record["command"] == command
    assert "gameplay" in record["not_claimed"]

    # Digests are recomputed from the bytes that actually arrived.
    assert record["raw"]["stdout"]["sha256"] == sha(STDOUT)
    assert record["raw"]["stdout"]["bytes"] == len(STDOUT)
    assert record["raw"]["stderr"]["sha256"] == sha(STDERR)
    assert record["stdin"]["sha256"] == sha(STDIN)
    assert record["guest_output_sha256"] == sha(record["guest_output"].encode())
    assert "arith:done" in record["guest_output"]
    assert "Launching" not in record["guest_output"]

    base = Path(record["artifacts_dir"])
    assert (base / "stdout.raw").read_bytes() == STDOUT
    assert (base / "stderr.raw").read_bytes() == STDERR
    assert (base / "stdin.raw").read_bytes() == STDIN
    assert record["frames"] == []

    entry = record["records"][0]
    assert entry["oracle_verified"] is True
    assert entry["scope"] == "console-only"
    assert entry["gui_frame_evidence"] is False
    assert entry["sha256"] == sha(paths["exe"].read_bytes())
    assert entry["native_sha256"] == sha(paths["native"].read_bytes())
    assert entry["input_sha256"] == sha(STDIN)
    assert Path(entry["evidence_path"]).name.endswith("-capture.json")
    assert entry["evidence_sha256"] == sha(Path(entry["evidence_path"]).read_bytes())

    assert ho.verify_record(paths["out"]) == []
    assert ho.main(["--verify", str(paths["out"])]) == ho.EXIT_OK


def test_console_capture_rejects_matrix_pass_promotion(paths):
    """A Wine console capture must never be an accepted original-Windows oracle."""
    assert invoke(paths) == ho.EXIT_OK
    entry = read_record(paths["out"])["records"][0]
    assert ho.ORACLE_CONSOLE not in am.REAL_ORACLES
    validated = am.validate_oracle_record(entry, base_dir=paths["out"].parent)
    assert validated["valid"] is False
    assert validated["checks"]["has_real_oracle_name"] is False


def test_manifest_is_byte_stable(paths):
    """Same inputs and out path produce an identical manifest (no timestamps)."""
    assert invoke(paths) == ho.EXIT_OK
    first = paths["out"].read_bytes()
    detail = Path(read_record(paths["out"])["records"][0]["evidence_path"]).read_bytes()
    assert invoke(paths) == ho.EXIT_OK
    assert paths["out"].read_bytes() == first
    second_detail = Path(read_record(paths["out"])["records"][0]["evidence_path"]).read_bytes()
    assert second_detail == detail


# ---------------------------------------------------------------------------
# GUI/frame scope is only claimed with real frame bytes
# ---------------------------------------------------------------------------

def test_gui_frame_capture_uses_real_frame_bytes(paths):
    paths["frame"].write_bytes(FRAME)
    code = invoke(paths, scope="gui-frame", frame=str(paths["frame"]))
    assert code == ho.EXIT_OK
    record = read_record(paths["out"])
    assert record["scope"] == "gui-frame"
    assert record["gui_frame_evidence"] is True
    assert record["oracle"] == ho.ORACLE_FRAME
    assert len(record["frames"]) == 1
    assert record["frames"][0]["sha256"] == sha(FRAME)
    assert record["frames"][0]["bytes"] == len(FRAME)
    base = Path(record["artifacts_dir"])
    assert (base / record["frames"][0]["file"]).read_bytes() == FRAME
    assert "Wine emulation" in record["not_claimed"]
    assert ho.verify_record(paths["out"]) == []


def test_console_scope_never_inherits_frames(paths):
    paths["frame"].write_bytes(FRAME)
    code = invoke(paths, frame=str(paths["frame"]))  # default console scope
    assert code == ho.EXIT_OK
    record = read_record(paths["out"])
    assert record["scope"] == "console-only"
    assert record["frames"] == []
    assert record["gui_frame_evidence"] is False
    assert ho.verify_record(paths["out"]) == []


def test_gui_frame_oracle_cannot_promote_pass(paths):
    paths["frame"].write_bytes(FRAME)
    assert invoke(paths, scope="gui-frame", frame=str(paths["frame"])) == ho.EXIT_OK
    entry = read_record(paths["out"])["records"][0]
    assert ho.ORACLE_FRAME not in am.REAL_ORACLES
    assert am.validate_oracle_record(entry, base_dir=paths["out"].parent)["valid"] is False


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------

def test_tampered_stdout_is_detected(paths):
    assert invoke(paths) == ho.EXIT_OK
    record = read_record(paths["out"])
    target = Path(record["artifacts_dir"]) / record["raw"]["stdout"]["file"]
    target.write_bytes(target.read_bytes() + b"tampered")
    problems = ho.verify_record(paths["out"])
    assert any("stdout hash mismatch" in p for p in problems)
    assert ho.main(["--verify", str(paths["out"])]) == ho.EXIT_FAILED


def test_tampered_stderr_is_detected(paths):
    FakeEndpoint.stderr = b"warning line\n"
    assert invoke(paths) == ho.EXIT_OK
    record = read_record(paths["out"])
    target = Path(record["artifacts_dir"]) / record["raw"]["stderr"]["file"]
    target.write_bytes(b"different")
    problems = ho.verify_record(paths["out"])
    assert any("stderr hash mismatch" in p for p in problems)


def test_tampered_frame_is_detected(paths):
    paths["frame"].write_bytes(FRAME)
    assert invoke(paths, scope="gui-frame", frame=str(paths["frame"])) == ho.EXIT_OK
    record = read_record(paths["out"])
    target = Path(record["artifacts_dir"]) / record["frames"][0]["file"]
    target.write_bytes(FRAME + b"tampered")
    problems = ho.verify_record(paths["out"])
    assert any("frame 0 hash mismatch" in p for p in problems)
    assert ho.main(["--verify", str(paths["out"])]) == ho.EXIT_FAILED


def test_tampered_evidence_file_is_detected(paths):
    assert invoke(paths) == ho.EXIT_OK
    evidence = Path(read_record(paths["out"])["records"][0]["evidence_path"])
    evidence.write_bytes(evidence.read_bytes() + b"tampered")
    problems = ho.verify_record(paths["out"])
    assert any("evidence hash mismatch" in p for p in problems)
    assert ho.main(["--verify", str(paths["out"])]) == ho.EXIT_FAILED


def test_tampered_executable_is_detected(paths):
    assert invoke(paths) == ho.EXIT_OK
    problems = ho.verify_record(paths["out"])
    assert problems == []
    paths["exe"].write_bytes(b"MZ tampered executable")
    problems = ho.verify_record(paths["out"])
    assert any("exe hash mismatch" in p for p in problems)


def test_forged_claim_is_rejected(paths):
    assert invoke(paths) == ho.EXIT_OK
    record = read_record(paths["out"])
    record["records"][0]["evidence_sha256"] = "0" * 64
    record["records"][0]["gui_frame_evidence"] = True
    paths["out"].write_text(json.dumps(record), encoding="utf-8")
    problems = ho.verify_record(paths["out"])
    assert any("evidence hash mismatch" in p for p in problems)
    assert any("gui_frame_evidence" in p for p in problems)
    validated = am.validate_oracle_record(
        record["records"][0], base_dir=paths["out"].parent)
    assert validated["checks"]["evidence_hash_matches"] is False


def test_scope_invariants_reject_console_as_frame():
    base = {"status": "CAPTURED", "scope": "console-only",
            "gui_frame_evidence": True, "frames": [{"sha256": "x"}],
            "oracle": ho.ORACLE_CONSOLE}
    problems = ho.check_scope_invariants(base)
    assert any("gui_frame_evidence=false" in p for p in problems)
    assert any("must not contain frame" in p for p in problems)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
