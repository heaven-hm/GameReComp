"""Fail-closed tests for the Windows-oracle capture harness (issue #145).

A real local HTTP server acts as the fake Windows host/VM endpoint; no real
game, Windows binary, screenshot or network service is used. Every path is
supplied through tmp_path / CLI arguments. Covers BLOCKED, successful capture,
the injectable runner, acceptance-matrix compatibility and tamper detection.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import acceptance_matrix as am  # noqa: E402
from tools import windows_oracle as wo  # noqa: E402

GAME = "igi-oracle-test"
SCENARIO = "boot-to-menu"
SHOT_1 = b"\x89PNG\r\n fake screenshot one"
SHOT_2 = b"\x89PNG\r\n fake screenshot two"
AUDIO_1 = b"RIFF fake audio capture"
LOG_1 = b"game started\nmenu reached\n"
PROCESS = {"pid": 4242, "name": "game.exe", "command": "game.exe -windowed"}


# ---------------------------------------------------------------------------
# Fake endpoint and fixtures
# ---------------------------------------------------------------------------

def capture_response(*, exit_code: int = 0) -> dict:
    def entry(name: str, data: bytes) -> dict:
        return {"name": name, "data_b64": base64.b64encode(data).decode("ascii")}
    return {
        "process": dict(PROCESS),
        "exit_code": exit_code,
        "screenshots": [entry("menu.png", SHOT_1), entry("level.png", SHOT_2)],
        "audio": [entry("theme.wav", AUDIO_1)],
        "logs": [entry("stdout.log", LOG_1)],
    }


class _EndpointHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # keep test output clean
        pass

    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/health"):
            if getattr(self.server, "healthy", True):
                self._json(200, {"status": "ok"})
            else:
                self._json(503, {"status": "unavailable"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/capture"):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.received.append(body)
        if getattr(self.server, "fail_capture", False):
            self._json(500, {"error": "capture failed"})
        else:
            self._json(200, getattr(self.server, "response", capture_response()))


@contextmanager
def fake_endpoint(*, healthy: bool = True, fail_capture: bool = False,
                  response: dict | None = None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EndpointHandler)
    server.healthy = healthy
    server.fail_capture = fail_capture
    server.response = response if response is not None else capture_response()
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def closed_endpoint():
    """A URL whose TCP port was bound and then released (connection refused)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    yield f"http://127.0.0.1:{port}"


@pytest.fixture
def paths(tmp_path):
    original = tmp_path / "original.exe"
    native = tmp_path / "native.bin"
    script = tmp_path / "input-script.json"
    original.write_bytes(b"MZ fake original windows executable")
    native.write_bytes(b"\x7fELF fake rebuilt native binary")
    script.write_bytes(b"{'steps': ['wait', 'press enter', 'wait']}")
    return {
        "original": original, "native": native, "script": script,
        "out": tmp_path / "scenario.json",
    }


def capture_args(paths, endpoint: str | None, **overrides):
    args = []
    if endpoint is not None:
        args += ["--endpoint", endpoint]
    args += [
        "--game-id", GAME, "--input-script", str(paths["script"]),
        "--original", str(paths["original"]), "--native", str(paths["native"]),
        "--out", str(paths["out"]), "--timeout", "5",
        "--scenario", SCENARIO,
    ]
    args += [item for key, value in overrides.items()
             for item in (f"--{key.replace('_', '-')}", str(value))]
    return args


def read_record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class FakeRunner:
    """Injectable runner used via --runner module:attribute."""

    def __init__(self):
        self.request = None
        self.probed = None

    def probe(self, endpoint, timeout):
        self.probed = (endpoint, timeout)
        return endpoint != "fake://unhealthy"

    def capture(self, request):
        self.request = request
        return wo.CaptureResult(
            process={"pid": 999, "name": "fake.exe"}, exit_code=7,
            screenshots=[{"name": "menu.png", "data": SHOT_1}],
            audio=[{"name": "audio.wav", "data": AUDIO_1}],
            logs=[{"name": "stdout.log", "data": LOG_1}],
        )


class EmptyRunner(FakeRunner):
    """A runner that returns no screenshots: must fail closed."""

    def capture(self, request):
        self.request = request
        return wo.CaptureResult(process={"pid": 1}, exit_code=0)


# ---------------------------------------------------------------------------
# BLOCKED paths
# ---------------------------------------------------------------------------

def test_no_endpoint_is_blocked_without_fabricating_artifacts(paths, capsys):
    code = wo.main(capture_args(paths, None))
    stderr = capsys.readouterr().err
    assert code == wo.EXIT_BLOCKED
    assert "BLOCKED" in stderr and "NOT TESTED" in stderr

    record = read_record(paths["out"])
    assert record["status"] == "BLOCKED"
    assert record["verification"] == "NOT TESTED"
    assert record["records"] == []
    for key in ("original_sha256", "native_sha256", "input_script_sha256",
                "artifacts_dir"):
        assert record[key] is None
    for key in ("screenshots", "audio", "logs"):
        assert record[key] == []
    assert not (paths["out"].parent / f"{paths['out'].stem}-artifacts").exists()
    assert not (paths["out"].parent / f"{paths['out'].stem}-capture.json").exists()


def test_unreachable_endpoint_is_blocked(paths, capsys):
    with closed_endpoint() as endpoint:
        code = wo.main(capture_args(paths, endpoint))
    stderr = capsys.readouterr().err
    assert code == wo.EXIT_BLOCKED
    assert "BLOCKED" in stderr and "NOT TESTED" in stderr
    record = read_record(paths["out"])
    assert record["status"] == "BLOCKED"
    assert record["records"] == []
    assert record["original_sha256"] is None


def test_unhealthy_endpoint_is_blocked(paths):
    with fake_endpoint(healthy=False) as (_server, endpoint):
        code = wo.main(capture_args(paths, endpoint))
    assert code == wo.EXIT_BLOCKED
    assert read_record(paths["out"])["status"] == "BLOCKED"


def test_no_screenshots_fails_closed(paths, capsys):
    spec = f"{__name__}:EmptyRunner"
    code = wo.main(capture_args(paths, "fake://runner", runner=spec))
    assert code == wo.EXIT_FAILED
    assert "FAILED" in capsys.readouterr().err
    record = read_record(paths["out"])
    assert record["status"] == "FAILED"
    assert record["records"] == []
    assert record["screenshots"] == []


# ---------------------------------------------------------------------------
# Successful capture
# ---------------------------------------------------------------------------

def test_successful_capture_records_process_exit_and_hashes(paths, capsys):
    with fake_endpoint() as (server, endpoint):
        code = wo.main(capture_args(paths, endpoint))

    assert code == wo.EXIT_OK, capsys.readouterr().err
    record = read_record(paths["out"])
    assert record["status"] == "CAPTURED"
    assert record["verification"] == "CAPTURED"
    assert len(record["records"]) == 1
    entry = record["records"][0]

    original_sha = hashlib.sha256(paths["original"].read_bytes()).hexdigest()
    native_sha = hashlib.sha256(paths["native"].read_bytes()).hexdigest()
    script_sha = hashlib.sha256(paths["script"].read_bytes()).hexdigest()
    assert entry["oracle"] == "windows-oracle"
    assert entry["oracle_verified"] is True
    assert entry["game"] == GAME
    assert entry["scenario"] == SCENARIO
    assert entry["sha256"] == original_sha == record["original_sha256"]
    assert entry["native_sha256"] == native_sha == record["native_sha256"]
    assert entry["input_script_sha256"] == script_sha == record["input_script_sha256"]

    # Artifacts were written as real files whose bytes hash to the recorded
    # digests -- nothing was invented by the harness.
    base = Path(record["artifacts_dir"])
    for entry_list, expected in ((record["screenshots"], [SHOT_1, SHOT_2]),
                                 (record["audio"], [AUDIO_1]),
                                 (record["logs"], [LOG_1])):
        assert len(entry_list) == len(expected)
        for artifact, raw in zip(entry_list, expected):
            blob = (base / artifact["path"]).read_bytes()
            assert blob == raw
            assert artifact["sha256"] == hashlib.sha256(raw).hexdigest()
            assert artifact["bytes"] == len(raw)

    detail = json.loads(Path(entry["evidence_path"]).read_text(encoding="utf-8"))
    assert detail["process"] == PROCESS
    assert detail["exit_code"] == 0
    assert detail["game_id"] == GAME
    assert detail["scenario"] == SCENARIO
    assert Path(entry["evidence_path"]).name.endswith("-capture.json")
    assert entry["evidence_sha256"] == hashlib.sha256(
        Path(entry["evidence_path"]).read_bytes()).hexdigest()

    # The endpoint received the deterministic script and its digest.
    assert len(server.received) == 1
    sent = server.received[0]
    assert sent["game_id"] == GAME
    assert sent["scenario"] == SCENARIO
    assert base64.b64decode(sent["input_script"]) == paths["script"].read_bytes()
    assert sent["input_script_sha256"] == script_sha
    assert sent["original_sha256"] == original_sha
    assert sent["native_sha256"] == native_sha

    assert wo.verify_record(paths["out"]) == []
    assert wo.main(["--verify", str(paths["out"])]) == wo.EXIT_OK


def test_capture_result_rejects_unsafe_names_and_fake_digests():
    bad = capture_response()
    bad["screenshots"][0]["name"] = "../escape.png"
    with pytest.raises(wo.CaptureError):
        wo.parse_capture_response(bad)

    bad = capture_response()
    del bad["exit_code"]
    with pytest.raises(wo.CaptureError):
        wo.parse_capture_response(bad)

    # Digests returned by the endpoint are ignored: the harness hashes bytes.
    bad = capture_response()
    bad["screenshots"][0]["sha256"] = "f" * 64
    result = wo.parse_capture_response(bad)
    assert "sha256" not in result.screenshots[0]


# ---------------------------------------------------------------------------
# Injectable runner
# ---------------------------------------------------------------------------

def test_injectable_runner_is_used(paths):
    spec = f"{__name__}:FakeRunner"
    code = wo.main(capture_args(paths, "fake://runner", runner=spec))
    assert code == wo.EXIT_OK
    record = read_record(paths["out"])
    assert record["status"] == "CAPTURED"
    detail = json.loads(Path(record["records"][0]["evidence_path"]).read_text())
    assert detail["process"]["pid"] == 999
    assert detail["exit_code"] == 7
    assert record["screenshots"][0]["sha256"] == hashlib.sha256(SHOT_1).hexdigest()


def test_injectable_runner_probe_failure_is_blocked(paths):
    code = wo.main(capture_args(paths, "fake://unhealthy",
                                runner=f"{__name__}:FakeRunner"))
    assert code == wo.EXIT_BLOCKED
    assert read_record(paths["out"])["status"] == "BLOCKED"


def test_bad_runner_spec_is_blocked(paths):
    code = wo.main(capture_args(paths, "http://ignored", runner="not-a-spec"))
    assert code == wo.EXIT_BLOCKED
    assert "runner error" in read_record(paths["out"])["reason"]


# ---------------------------------------------------------------------------
# Acceptance-matrix compatibility and tamper detection
# ---------------------------------------------------------------------------

def _corpus_record(original_sha: str, native_sha: str, shot_sha: str) -> dict:
    return {"games": [{
        "game": GAME, "status": "PARTIAL", "reasons": ["evidence recorded"],
        "original": {"sha256": original_sha, "pe_type": "PE32"},
        "native": {"sha256": native_sha},
        "pipeline": {"verification_status": "scenario-pass",
                     "native_sha256": native_sha},
        "launch": {"executed": True, "exit_code": 0},
        "frames": {"example": {"sha256": shot_sha, "distinct_colors": 100}},
        "scenario": {"declared": True, "complete": True},
    }]}


def test_scenario_record_promotes_matrix_row_to_pass(tmp_path, paths):
    with fake_endpoint() as (_server, endpoint):
        assert wo.main(capture_args(paths, endpoint)) == wo.EXIT_OK
    record = read_record(paths["out"])
    entry = record["records"][0]

    # Structurally valid as --oracle-evidence, including the evidence hash.
    validated = am.validate_oracle_record(entry, base_dir=paths["out"].parent)
    assert validated["valid"] is True, validated["reasons"]
    assert validated["oracle"] in am.REAL_ORACLES

    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(_corpus_record(
        entry["sha256"], entry["native_sha256"],
        record["screenshots"][0]["sha256"])), encoding="utf-8")
    out = tmp_path / "matrix.json"
    assert am.main(["--corpus-acceptance", str(corpus),
                    "--oracle-evidence", str(paths["out"]),
                    "--generated-at", "2026-11-24T00:00:00Z",
                    "--out", str(out)]) == 0
    matrix = json.loads(out.read_text(encoding="utf-8"))
    assert matrix["summary"]["by_status"]["PASS"] == 1
    assert matrix["rows"][0]["status"] == "PASS"


def test_blocked_record_never_promotes_matrix_row(tmp_path, paths):
    assert wo.main(capture_args(paths, None)) == wo.EXIT_BLOCKED
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(_corpus_record("a" * 64, "b" * 64, "c" * 64)),
                      encoding="utf-8")
    out = tmp_path / "matrix.json"
    assert am.main(["--corpus-acceptance", str(corpus),
                    "--oracle-evidence", str(paths["out"]),
                    "--generated-at", "2026-11-24T00:00:00Z",
                    "--out", str(out)]) == 0
    matrix = json.loads(out.read_text(encoding="utf-8"))
    assert matrix["summary"]["by_status"].get("PASS", 0) == 0


def test_tampered_screenshot_is_detected(paths):
    with fake_endpoint() as (_server, endpoint):
        assert wo.main(capture_args(paths, endpoint)) == wo.EXIT_OK
    record = read_record(paths["out"])
    shot = Path(record["artifacts_dir"]) / record["screenshots"][0]["path"]
    shot.write_bytes(shot.read_bytes() + b"tampered")

    problems = wo.verify_record(paths["out"])
    assert problems and any("hash mismatch" in p for p in problems)
    assert wo.main(["--verify", str(paths["out"])]) == wo.EXIT_FAILED


def test_tampered_evidence_file_is_detected(paths):
    with fake_endpoint() as (_server, endpoint):
        assert wo.main(capture_args(paths, endpoint)) == wo.EXIT_OK
    record = read_record(paths["out"])
    evidence = Path(record["records"][0]["evidence_path"])
    evidence.write_bytes(evidence.read_bytes() + b"tampered")

    problems = wo.verify_record(paths["out"])
    assert problems and any("evidence hash mismatch" in p for p in problems)
    assert wo.main(["--verify", str(paths["out"])]) == wo.EXIT_FAILED


def test_tampered_claim_is_rejected_by_acceptance_matrix(paths):
    with fake_endpoint() as (_server, endpoint):
        assert wo.main(capture_args(paths, endpoint)) == wo.EXIT_OK
    record = read_record(paths["out"])
    record["records"][0]["evidence_sha256"] = "0" * 64
    paths["out"].write_text(json.dumps(record), encoding="utf-8")

    problems = wo.verify_record(paths["out"])
    assert problems and any("evidence hash mismatch" in p for p in problems)
    validated = am.validate_oracle_record(
        record["records"][0], base_dir=paths["out"].parent)
    assert validated["valid"] is False
    assert validated["checks"]["evidence_hash_matches"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
