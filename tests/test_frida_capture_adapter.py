"""Fail-closed tests for the orchestrator-facing Frida capture adapter.

The fake Frida transport below is an importable module attribute (loaded by the
real worker through ``--frida module:attribute``), not a monkeypatch of the
adapter. It proves that BLOCKED results carry no fabricated hook counts or
screenshots, that a CAPTURED result contains only bytes Frida really returned
with locally recomputed SHA-256 digests, that tampering is detected, and that a
stalled attach is killed at the wall-clock deadline.

No real game, Windows executable, Frida installation, Wine prefix or host path
is used. Every path comes from tmp_path / CLI arguments.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import frida_capture_adapter as fa  # noqa: E402

SCENARIO = {"name": "boot-to-menu",
            "hooks": ["kernel32!ExitProcess", "user32!MessageBoxW"],
            "args": []}
HITS = ["kernel32!ExitProcess", "user32!MessageBoxW", "kernel32!ExitProcess"]
SHOT_BYTES = b"fake screenshot bytes returned by the frida transport"
SHOT = {"name": "menu.png", "data_b64": base64.b64encode(SHOT_BYTES).decode("ascii")}
FAKE_SPEC = "tests.test_frida_capture_adapter:FakeFrida"
SLEEPY_SPEC = "tests.test_frida_capture_adapter:SleepyFrida"


# ---------------------------------------------------------------------------
# Injectable fake Frida module (imported by the bounded worker process)
# ---------------------------------------------------------------------------

class FakeExports:
    def __init__(self, hits: list[str], screenshots: list[dict]):
        self._hits = list(hits)
        self._screenshots = list(screenshots)

    def calls(self) -> list[str]:
        return list(self._hits)

    def screenshots(self) -> list[dict]:
        return [dict(item) for item in self._screenshots]


class FakeScript:
    def __init__(self, source: str, hits: list[str], screenshots: list[dict]):
        self.source = source
        self.loaded = False
        self.exports_sync = FakeExports(hits, screenshots)

    def load(self) -> None:
        self.loaded = True


class FakeSession:
    def __init__(self, hits: list[str], screenshots: list[dict]):
        self.script = None
        self._hits = hits
        self._screenshots = screenshots
        self.detached = False

    def create_script(self, source: str) -> FakeScript:
        self.script = FakeScript(source, self._hits, self._screenshots)
        return self.script

    def detach(self) -> None:
        self.detached = True


class FakeFrida:
    """A deterministic in-process-free Frida transport for the child worker."""

    hits = HITS
    screenshots = [SHOT]

    def spawn(self, command: list[str], env: dict | None = None) -> int:
        # The worker pid is safe to kill on timeout; no unrelated process is
        # spawned. The command itself is still recorded by the adapter.
        return os.getpid()

    def attach(self, pid: int) -> FakeSession:
        return FakeSession(self.hits, self.screenshots)

    def resume(self, pid: int) -> None:
        return None


class SleepyFrida(FakeFrida):
    """A transport that never reaches attach; only the deadline can stop it."""

    def spawn(self, command: list[str], env: dict | None = None) -> int:
        time.sleep(30)
        return os.getpid()


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _write_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def paths(tmp_path):
    exe = tmp_path / "original.exe"
    exe.write_bytes(b"MZ fake original windows executable")
    highball = _write_executable(tmp_path / "highball")
    return {"exe": exe, "highball": highball, "out": tmp_path / "frida.json",
            "scenario": dict(SCENARIO)}


def read_record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_no_evidence(record: dict, out: Path) -> None:
    assert record["status"] in (fa.STATUS_BLOCKED, fa.STATUS_FAILED)
    assert record["verification"] == fa.VERIFICATION_NOT_TESTED
    assert record["reason"]
    assert record["records"] == []
    assert record["hook_hits"] == []
    assert record["call_counts"] == {}
    assert record["screenshots"] == []
    assert record["artifacts"] == []
    for key in ("exe_sha256", "scenario_sha256", "artifacts_dir",
                "evidence_path", "evidence_sha256"):
        assert record[key] is None, key
    assert not (out.parent / f"{out.stem}-artifacts").exists()
    assert not (out.parent / f"{out.stem}-capture.json").exists()


# ---------------------------------------------------------------------------
# BLOCKED paths
# ---------------------------------------------------------------------------

def test_missing_frida_is_blocked_without_fabricated_evidence(paths):
    record = fa.capture(paths["exe"], paths["scenario"], 3, paths["out"],
                        frida="no_such_frida_module:Frida",
                        highball=str(paths["highball"]))
    assert "frida" in record["reason"].lower()
    assert_no_evidence(record, paths["out"])
    assert read_record(paths["out"]) == record


def test_missing_windows_process_host_is_blocked(paths):
    record = fa.capture(paths["exe"], paths["scenario"], 3, paths["out"],
                        frida=FAKE_SPEC)
    assert "process host" in record["reason"]
    assert "NOT TESTED" in record["reason"]
    assert_no_evidence(record, paths["out"])


def test_unreachable_host_endpoint_is_blocked(paths):
    # A port that was bound and released: connection refused, not a fake OK.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    record = fa.capture(paths["exe"], paths["scenario"], 3, paths["out"],
                        frida=FAKE_SPEC, highball=str(paths["highball"]),
                        endpoint=f"http://127.0.0.1:{port}")
    assert "unreachable" in record["reason"]
    assert_no_evidence(record, paths["out"])


def test_no_hooks_is_blocked_not_a_vacuous_capture(paths):
    record = fa.capture(paths["exe"], {"name": "no-hooks"}, 3, paths["out"],
                        frida=FAKE_SPEC, highball=str(paths["highball"]))
    assert "no hooks" in record["reason"]
    assert_no_evidence(record, paths["out"])


# ---------------------------------------------------------------------------
# Successful capture
# ---------------------------------------------------------------------------

def test_successful_capture_records_hits_and_recomputed_hashes(paths):
    record = fa.capture(paths["exe"], paths["scenario"], 4, paths["out"],
                        frida=FAKE_SPEC, highball=str(paths["highball"]))
    assert record["status"] == fa.STATUS_CAPTURED
    assert record["verification"] == fa.VERIFICATION_CAPTURED
    assert record["reason"] is None
    assert len(record["records"]) == 1
    entry = record["records"][0]

    assert record["exe_sha256"] == hashlib.sha256(paths["exe"].read_bytes()).hexdigest()
    assert record["scenario_sha256"] == fa.sha256_value(paths["scenario"])
    assert record["hook_hits"] == HITS
    assert record["call_counts"] == {"kernel32!ExitProcess": 2,
                                     "user32!MessageBoxW": 1}
    assert entry["call_counts"] == record["call_counts"]

    base = Path(record["artifacts_dir"])
    hits_file = base / "hook-hits.json"
    assert json.loads(hits_file.read_text(encoding="utf-8")) == HITS
    assert fa.sha256_file(hits_file) == fa.sha256_bytes(
        fa.canonical_bytes(HITS))

    # The screenshot exists as real bytes from the fake Frida module, and the
    # recorded digest was recomputed from those written bytes.
    assert len(record["screenshots"]) == 1
    shot = record["screenshots"][0]
    shot_path = base / shot["path"]
    assert shot_path.read_bytes() == SHOT_BYTES
    assert shot["sha256"] == hashlib.sha256(SHOT_BYTES).hexdigest()
    assert shot["bytes"] == len(SHOT_BYTES)

    evidence = Path(record["evidence_path"])
    assert evidence.name.endswith("-capture.json")
    assert record["evidence_sha256"] == fa.sha256_file(evidence)
    assert entry["evidence_sha256"] == record["evidence_sha256"]
    detail = json.loads(evidence.read_text(encoding="utf-8"))
    assert detail["hook_hits"] == HITS
    assert detail["runner"] == "highball"
    assert detail["command"][:2] == [str(paths["highball"]), "run"]
    assert "NOT a" not in entry["scenario_evidence"].lower()
    assert "Process-level observation only" in entry["scenario_evidence"]

    assert fa.verify_record(paths["out"]) == []
    assert read_record(paths["out"]) == record


def test_cli_captures_and_verifies_with_supplied_paths(paths, tmp_path, capsys):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(json.dumps(SCENARIO), encoding="utf-8")
    args = ["--exe", str(paths["exe"]), "--scenario", str(scenario_file),
            "--out", str(paths["out"]), "--timeout", "4",
            "--frida", FAKE_SPEC, "--highball", str(paths["highball"])]
    assert fa.main(args) == fa.EXIT_OK
    record = read_record(paths["out"])
    assert record["status"] == fa.STATUS_CAPTURED
    assert record["scenario"] == SCENARIO
    assert capsys.readouterr().out

    assert fa.main(["--verify", str(paths["out"])]) == fa.EXIT_OK


def test_cli_blocked_exit_code(paths, capsys):
    code = fa.main(["--exe", str(paths["exe"]), "--out", str(paths["out"]),
                    "--frida", FAKE_SPEC, "--timeout", "2"])
    assert code == fa.EXIT_BLOCKED
    assert "BLOCKED" in capsys.readouterr().out
    assert_no_evidence(read_record(paths["out"]), paths["out"])


def test_wine_fallback_is_labelled(paths):
    wine = _write_executable(paths["exe"].parent / "wine")
    record = fa.capture(paths["exe"], paths["scenario"], 4, paths["out"],
                        frida=FAKE_SPEC, wine=str(wine))
    assert record["status"] == fa.STATUS_CAPTURED
    assert record["host"]["runner"] == "wine-fallback"
    detail = json.loads(Path(record["evidence_path"]).read_text(encoding="utf-8"))
    assert detail["runner"] == "wine-fallback"
    assert detail["command"][0] == str(wine)


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------

def test_tampered_screenshot_is_detected(paths):
    record = fa.capture(paths["exe"], paths["scenario"], 4, paths["out"],
                        frida=FAKE_SPEC, highball=str(paths["highball"]))
    assert record["status"] == fa.STATUS_CAPTURED
    shot = Path(record["artifacts_dir"]) / record["screenshots"][0]["path"]
    shot.write_bytes(shot.read_bytes() + b"tampered")
    problems = fa.verify_record(paths["out"])
    assert problems and any("hash mismatch" in p for p in problems)
    assert fa.main(["--verify", str(paths["out"])]) == fa.EXIT_FAILED


def test_tampered_evidence_file_is_detected(paths):
    fa.capture(paths["exe"], paths["scenario"], 4, paths["out"],
               frida=FAKE_SPEC, highball=str(paths["highball"]))
    evidence = Path(read_record(paths["out"])["evidence_path"])
    evidence.write_bytes(evidence.read_bytes() + b"tampered")
    problems = fa.verify_record(paths["out"])
    assert problems and any("evidence hash mismatch" in p for p in problems)


def test_tampered_call_count_is_detected(paths):
    record = fa.capture(paths["exe"], paths["scenario"], 4, paths["out"],
                        frida=FAKE_SPEC, highball=str(paths["highball"]))
    assert record["status"] == fa.STATUS_CAPTURED
    record["call_counts"]["kernel32!ExitProcess"] = 999
    paths["out"].write_text(json.dumps(record), encoding="utf-8")
    problems = fa.verify_record(paths["out"])
    assert problems and any("call counts" in p for p in problems)


# ---------------------------------------------------------------------------
# Bounded execution
# ---------------------------------------------------------------------------

def test_stalled_frida_attach_is_killed_at_the_deadline(paths):
    started = time.monotonic()
    record = fa.capture(paths["exe"], paths["scenario"], 1, paths["out"],
                        frida=SLEEPY_SPEC, highball=str(paths["highball"]))
    elapsed = time.monotonic() - started
    assert record["status"] == fa.STATUS_BLOCKED
    assert record["killed_on_timeout"] is True
    assert "exceeded" in record["reason"]
    assert elapsed < 5, f"deadline was not bounded: {elapsed:.2f}s"
    assert_no_evidence(record, paths["out"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
