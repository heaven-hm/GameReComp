"""Tests for the issue #27 evidence joiner.

Every record, log and frame here is synthetic and written under tmp_path. The
cases prove each verdict (RESOLVED / PARTIAL / BLOCKED) and every tamper path:
altered frame or log bytes, contradictory counts, cross-record hash mismatch,
claimed PASS, and an absent claimed log event. No game or fixture is executed.

Run with: python3 -m pytest tests/test_eh27_evidence.py -q
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tools import eh27_evidence

ROOT = Path(__file__).resolve().parents[1]


def make_bmp24(pixels: list[list[tuple[int, int, int]]]) -> bytes:
    """Minimal 24-bpp uncompressed BMP with the given RGB rows."""
    height, width = len(pixels), len(pixels[0])
    stride = ((width * 3 + 3) // 4) * 4
    data = bytearray(54 + stride * height)
    data[:2] = b'BM'
    struct.pack_into('<I', data, 10, 54)
    struct.pack_into('<I', data, 14, 40)
    struct.pack_into('<ii', data, 18, width, height)
    struct.pack_into('<H', data, 26, 1)
    struct.pack_into('<H', data, 28, 24)
    for file_row, image_row in enumerate(reversed(pixels)):
        off = 54 + file_row * stride
        for x, (red, green, blue) in enumerate(image_row):
            data[off + x * 3:off + x * 3 + 3] = bytes((blue, green, red))
    return bytes(data)


NONBLANK = [[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (255, 255, 255)]]
BLANK = [[(7, 7, 7), (7, 7, 7)], [(7, 7, 7), (7, 7, 7)]]
EXE_SHA = "ab" * 32


def eh_record(**overrides):
    rec = {
        "schema_version": 1,
        "events": [{"name": name, "status": "ok"}
                   for name in eh27_evidence.EH_REQUIRED],
        "counts": {"ok": len(eh27_evidence.EH_REQUIRED), "FAIL": 0,
                   "BUILD FAIL": 0, "skip": 0,
                   "total": len(eh27_evidence.EH_REQUIRED)},
        "game_evidence": {"verdict": "NOT TESTED", "promotion": False},
    }
    rec.update(overrides)
    return rec


def dmd_report(exe_sha: str = EXE_SHA, **overrides):
    rec = {
        "schema_version": 1,
        "tool": "synthetic-dmd-validator",
        "target": {"name": "gr.exe", "sha256": exe_sha},
        "exception_code": eh27_evidence.DMD_CODE,
        "checks": [{"id": name, "status": "PASS", "detail": "synthetic"}
                   for name in eh27_evidence.DMD_REQUIRED_CHECKS],
        "dispatch": {"caught": True, "resumed_guest": True,
                     "catch_va": "0x00405300",
                     "reason": "dispatched to guest catch"},
    }
    rec.update(overrides)
    return rec


def log_text(*, caught: bool = True, resumed: bool = True, negative: str = "") -> str:
    lines = ["gr.exe rebuilt run, exit=0"]
    if caught:
        lines.append(f"DMD_EXCEPTION_CAUGHT code={eh27_evidence.DMD_CODE} "
                     "guest=0x00405300")
    if resumed:
        lines.append("GUEST_EXECUTION_RESUMED catch=0x00405300")
    if negative:
        lines.append(negative)
    lines.append("captured 1 frame")
    return "\n".join(lines) + "\n"


def run_record(log: str, frame_name: str | None, frame_sha: str | None,
               exe_sha: str = EXE_SHA, **overrides):
    lines = log.split("\n")
    events = []
    for code in ("DMD_EXCEPTION_CAUGHT", "GUEST_EXECUTION_RESUMED"):
        for number, line in enumerate(lines, 1):
            if code in line:
                events.append({"code": code, "line": number})
                break
    rec = {
        "schema_version": 1,
        "game": "Gunroar",
        "exe": {"name": "gr.exe", "sha256": exe_sha},
        "log_sha256": hashlib.sha256(log.encode()).hexdigest(),
        "log_events": events,
        "frames": ([{"path": frame_name, "sha256": frame_sha}]
                   if frame_name and frame_sha else []),
    }
    rec.update(overrides)
    return rec


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def invoke(tmp_path: Path, *, eh=None, eh_log=None, dmd=None, run=None,
           run_log=None, frames=None, name: str = ""):
    """Supply evidence through the real CLI and return (exit code, verdict)."""
    args = ["--out", str(tmp_path / f"verdict{name}.json")]
    if eh is not None:
        path = tmp_path / f"eh{name}.data"
        path.write_text(eh if isinstance(eh, str) else json.dumps(eh),
                        encoding="utf-8")
        args += ["--eh-selftest", str(path)]
    if eh_log is not None:
        path = tmp_path / f"ehlog{name}.log"
        path.write_bytes(eh_log)
        args += ["--eh-selftest-log", str(path)]
    if dmd is not None:
        path = tmp_path / f"dmd{name}.json"
        write_json(path, dmd)
        args += ["--dmd-report", str(path)]
    if run is not None:
        path = tmp_path / f"run{name}.json"
        write_json(path, run)
        args += ["--run", str(path)]
    if run_log is not None:
        path = tmp_path / f"runlog{name}.log"
        path.write_bytes(run_log if isinstance(run_log, bytes)
                         else run_log.encode())
        args += ["--run-log", str(path)]
    for frame in frames or []:
        args += ["--frame", str(frame)]
    code = eh27_evidence.main(args)
    doc = json.loads((tmp_path / f"verdict{name}.json").read_text())
    return code, doc


def full_case(tmp_path: Path, name: str = ""):
    """A complete, consistent evidence set that must resolve."""
    folder = tmp_path if not name else tmp_path / f"case{name.lstrip('-')}"
    folder.mkdir(exist_ok=True)
    frame = folder / "frame.bmp"
    frame.write_bytes(make_bmp24(NONBLANK))
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    log = log_text()
    eh, dmd = eh_record(), dmd_report()
    run = run_record(log, frame.name, frame_sha)
    return invoke(tmp_path, eh=eh, dmd=dmd, run=run,
                  run_log=log.encode(), frames=[frame], name=name)


def codes(doc) -> set[str]:
    return {item["code"] for item in doc["reasons"]}


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------
def test_full_evidence_resolved(tmp_path):
    code, doc = full_case(tmp_path)
    assert code == 0
    assert doc["verdict"] == "RESOLVED"
    assert doc["reasons"] == []
    game = doc["evidence"]["game_run"]
    assert game["caught"] is True and game["resumed"] is True
    assert game["verified_nonblank"] == 1
    assert game["frames"][0]["distinct_colors"] == 4
    assert game["frames"][0]["sha256"] == hashlib.sha256(
        (tmp_path / "frame.bmp").read_bytes()).hexdigest()


def test_selftests_only_is_partial(tmp_path):
    code, doc = invoke(tmp_path, eh=eh_record())
    assert code == 1
    assert doc["verdict"] == "PARTIAL"
    assert {"SELFTEST_ONLY_NO_RUN", "NO_RUN_EVIDENCE"} <= codes(doc)
    assert doc["evidence"]["eh_selftest"]["clean"] is True
    assert doc["evidence"]["game_run"]["available"] is False


def test_collector_style_record_without_original_log_is_still_partial(tmp_path):
    """A source hash claim without the log is a limitation, not a failure."""
    record = eh_record()
    record["generator"] = "tools/selftest_evidence_collector.py"
    record["source"] = {"kind": "summary", "sha256": "55" * 32}
    code, doc = invoke(tmp_path, eh=record)
    assert code == 1
    assert doc["verdict"] == "PARTIAL"
    assert "EH_SELFTEST_LOG_MISSING" in codes(doc)
    assert doc["evidence"]["eh_selftest"]["clean"] is True


def test_no_inputs_is_blocked(tmp_path):
    code, doc = invoke(tmp_path)
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "NO_EVIDENCE" in codes(doc)


def test_dmd_report_without_run_is_blocked(tmp_path):
    code, doc = invoke(tmp_path, dmd=dmd_report())
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "NO_RUN_EVIDENCE" in codes(doc)


def test_run_without_catch_or_resume_is_blocked(tmp_path):
    frame = tmp_path / "frame.bmp"
    frame.write_bytes(make_bmp24(NONBLANK))
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    log = log_text(caught=False, resumed=False)
    code, doc = invoke(
        tmp_path, eh=eh_record(), dmd=dmd_report(),
        run=run_record(log, frame.name, frame_sha), run_log=log, frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert {"EXCEPTION_NOT_CAUGHT", "EXECUTION_NOT_RESUMED"} <= codes(doc)


def test_negative_run_marker_blocks_even_with_positive_markers(tmp_path):
    frame = tmp_path / "frame.bmp"
    frame.write_bytes(make_bmp24(NONBLANK))
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    log = log_text(negative="DMD throw NOT dispatched: no matching catch handler")
    code, doc = invoke(
        tmp_path, eh=eh_record(), dmd=dmd_report(),
        run=run_record(log, frame.name, frame_sha), run_log=log, frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "RUN_NEGATIVE_EVIDENCE" in codes(doc)


def test_blank_frame_is_not_render_evidence(tmp_path):
    frame = tmp_path / "frame.bmp"
    frame.write_bytes(make_bmp24(BLANK))
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    log = log_text()
    code, doc = invoke(
        tmp_path, eh=eh_record(), dmd=dmd_report(),
        run=run_record(log, frame.name, frame_sha), run_log=log, frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert {"FRAME_BLANK", "NO_NONBLANK_FRAME"} <= codes(doc)
    assert doc["evidence"]["game_run"]["verified_nonblank"] == 0


def test_run_without_frame_bytes_is_partial(tmp_path):
    log = log_text()
    run = run_record(log, "frame-0000.bmp", "cd" * 32)
    code, doc = invoke(tmp_path, eh=eh_record(), dmd=dmd_report(),
                       run=run, run_log=log)
    assert code == 1
    assert doc["verdict"] == "PARTIAL"
    assert "FRAME_PROOF_MISSING" in codes(doc)
    assert doc["evidence"]["game_run"]["caught"] is True


def test_raw_selftest_log_is_accepted_and_per_check_lines_ignored(tmp_path):
    frame = tmp_path / "frame.bmp"
    frame.write_bytes(make_bmp24(NONBLANK))
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    log = log_text()
    # The per-check "FAIL ..." line is a fixture detail, not a selftest outcome;
    # only the four summary lines count.
    eh_log = "\n".join(
        [f"ok    {name}" for name in eh27_evidence.EH_REQUIRED] +
        ["FAIL catch local was not bound  # per-check line, not a status"])
    code, doc = invoke(
        tmp_path, eh=eh_log, dmd=dmd_report(),
        run=run_record(log, frame.name, frame_sha), run_log=log, frames=[frame])
    assert code == 0
    assert doc["verdict"] == "RESOLVED"
    assert doc["evidence"]["eh_selftest"]["counts"]["FAIL"] == 0
    assert len(doc["evidence"]["eh_selftest"]["events"]) == 4


# ---------------------------------------------------------------------------
# tamper detection
# ---------------------------------------------------------------------------
def test_frame_byte_tamper_detected(tmp_path):
    frame = tmp_path / "frame.bmp"
    good = make_bmp24(NONBLANK)
    frame.write_bytes(good)
    log = log_text()
    run = run_record(log, frame.name, hashlib.sha256(good).hexdigest())
    frame.write_bytes(good[:-1] + bytes([good[-1] ^ 0xFF]))  # alter after claiming
    code, doc = invoke(tmp_path, eh=eh_record(), dmd=dmd_report(), run=run,
                       run_log=log, frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert {"FRAME_HASH_MISMATCH", "EVIDENCE_TAMPERED"} <= codes(doc)


def test_run_log_hash_tamper_detected(tmp_path):
    code, doc = full_case(tmp_path)
    assert code == 0
    frame = tmp_path / "frame.bmp"
    log = log_text()
    run = run_record(log, frame.name, hashlib.sha256(frame.read_bytes()).hexdigest())
    altered = log.replace("exit=0", "exit=3")
    code, doc = invoke(tmp_path, eh=eh_record(), dmd=dmd_report(), run=run,
                       run_log=altered, frames=[frame], name="-tampered")
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert {"RUN_LOG_HASH_MISMATCH", "EVIDENCE_TAMPERED"} <= codes(doc)


def test_cross_record_exe_hash_mismatch_detected(tmp_path):
    frame = tmp_path / "frame.bmp"
    frame.write_bytes(make_bmp24(NONBLANK))
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    log = log_text()
    code, doc = invoke(tmp_path, eh=eh_record(),
                       dmd=dmd_report(exe_sha="11" * 32),
                       run=run_record(log, frame.name, frame_sha),
                       run_log=log, frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "EXE_HASH_MISMATCH" in codes(doc)


@pytest.mark.parametrize("claim", [
    {"verdict": "RESOLVED"}, {"game_pass": True}, {"promotion": True},
])
def test_claimed_pass_is_refused(tmp_path, claim):
    frame = tmp_path / "frame.bmp"
    frame.write_bytes(make_bmp24(NONBLANK))
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    log = log_text()
    run = run_record(log, frame.name, frame_sha, **claim)
    code, doc = invoke(tmp_path, eh=eh_record(), dmd=dmd_report(), run=run,
                       run_log=log, frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert {"FAKE_PASS_CLAIM", "EVIDENCE_TAMPERED"} <= codes(doc)


def test_selftest_count_tamper_detected(tmp_path):
    counts = eh_record()["counts"]
    counts["ok"] = 99
    code, doc = invoke(tmp_path, eh=eh_record(counts=counts))
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "EH_COUNT_MISMATCH" in codes(doc)


def test_failing_selftest_blocks(tmp_path):
    events = eh_record()["events"]
    events[0]["status"] = "FAIL"
    code, doc = invoke(tmp_path, eh=eh_record(events=events))
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "EH_SELFTEST_FAILED" in codes(doc)


def test_failed_dmd_check_blocks(tmp_path):
    checks = dmd_report()["checks"]
    checks[0]["status"] = "FAIL"
    code, doc = invoke(tmp_path, eh=eh_record(), dmd=dmd_report(checks=checks))
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "DMD_CHECK_FAILED" in codes(doc)


def test_claimed_log_event_absent_from_log_is_tampering(tmp_path):
    frame = tmp_path / "frame.bmp"
    frame.write_bytes(make_bmp24(NONBLANK))
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    log = log_text()
    run = run_record(log, frame.name, frame_sha)
    run["log_events"].append({"code": "GAME_PASS_ACHIEVED", "line": 1})
    code, doc = invoke(tmp_path, eh=eh_record(), dmd=dmd_report(), run=run,
                       run_log=log, frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert {"LOG_EVENT_MISMATCH", "EVIDENCE_TAMPERED"} <= codes(doc)


def test_selftest_log_hash_mismatch_detected(tmp_path):
    frame = tmp_path / "frame.bmp"
    frame.write_bytes(make_bmp24(NONBLANK))
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    log = log_text()
    eh_log = "\n".join(f"ok    {name}" for name in eh27_evidence.EH_REQUIRED)
    record = eh_record()
    record["source"] = {"sha256": "00" * 32}
    code, doc = invoke(tmp_path, eh=record, eh_log=eh_log.encode(),
                       dmd=dmd_report(),
                       run=run_record(log, frame.name, frame_sha),
                       run_log=log, frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "EH_SELFTEST_LOG_HASH_MISMATCH" in codes(doc)


# ---------------------------------------------------------------------------
# determinism and CLI
# ---------------------------------------------------------------------------
def test_output_is_byte_identical_for_same_evidence(tmp_path):
    code_a, _ = full_case(tmp_path, "-a")
    code_b, _ = full_case(tmp_path, "-b")
    a = (tmp_path / "verdict-a.json").read_bytes()
    b = (tmp_path / "verdict-b.json").read_bytes()
    assert code_a == code_b == 0
    assert a == b


def test_module_cli_exit_status(tmp_path):
    frame = tmp_path / "frame.bmp"
    frame.write_bytes(make_bmp24(NONBLANK))
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    log = log_text()
    (tmp_path / "eh.json").write_text(json.dumps(eh_record()))
    (tmp_path / "dmd.json").write_text(json.dumps(dmd_report()))
    (tmp_path / "run.json").write_text(
        json.dumps(run_record(log, frame.name, frame_sha)))
    (tmp_path / "run.log").write_text(log)
    base = [sys.executable, "-m", "tools.eh27_evidence"]
    full = base + ["--eh-selftest", str(tmp_path / "eh.json"),
                   "--dmd-report", str(tmp_path / "dmd.json"),
                   "--run", str(tmp_path / "run.json"),
                   "--run-log", str(tmp_path / "run.log"),
                   "--frame", str(frame), "--out", str(tmp_path / "out.json")]
    done = subprocess.run(full, cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    blocked = subprocess.run(base + ["--out", str(tmp_path / "empty.json")],
                             cwd=ROOT, capture_output=True, text=True)
    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["verdict"] == "BLOCKED"
