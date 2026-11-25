"""Guard the fail-closed #145/#146 capture-evidence join.

No game, capture device or GUI is involved: every record and byte file is
built in tmp_path. The tests prove that a console-only or silent capture can
never be promoted, that claims without bytes cannot fake PASS, and that a
tampered record or report is rejected by --verify.
"""
from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path

from tools import capture_blocker_evidence as cbe

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / "proof" / "capture-blockers"
DOC = ROOT / "docs" / "CAPTURE_BLOCKER_EVIDENCE.md"


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def make_png(colors) -> bytes:
    """A minimal 2x2 RGB PNG with the requested pixel colours."""
    raw = (b"\x00" + bytes(colors[0]) + bytes(colors[1])
           + b"\x00" + bytes(colors[2]) + bytes(colors[3]))
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))


def make_wav(samples) -> bytes:
    """A minimal mono 16-bit PCM WAV."""
    pcm = b"".join(struct.pack("<h", s) for s in samples)
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 8000 * 2, 2, 16)
    size = 4 + 8 + len(fmt) + 8 + len(pcm)
    return (b"RIFF" + struct.pack("<I", size) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def endpoint_record(capture_id="session-1", kind="retail-windows",
                    verified=True, scope="gui-frame"):
    return {
        "schema": 1, "capability": "windows-endpoint", "capture_id": capture_id,
        "endpoint": {"kind": kind, "label": kind, "verified": verified,
                     "scope": scope, "guest_output_sha256": "b6" * 32,
                     "native_sha256": None},
        "source": "test",
    }


def frames_record(name, sha, capture_id="session-1", scope="gui-frame",
                  completed=1, expected=1):
    return {
        "schema": 1, "capability": "gui-frames", "capture_id": capture_id,
        "scope": scope, "source": "test",
        "frames": [{"name": name, "sha256": sha}],
        "clicks": {"completed": completed, "expected": expected},
    }


def audio_record(name, sha, capture_id="session-1", non_silent=True, frames=4):
    return {
        "schema": 1, "capability": "audio", "capture_id": capture_id,
        "source": "test", "artifact": {"name": name, "sha256": sha},
        "audio": {"frames": frames, "non_silent": non_silent},
    }


def native_record(sha, capture_id="session-1"):
    return {"schema": 1, "capability": "native-hash", "capture_id": capture_id,
            "native": {"sha256": sha}, "source": "test"}


def full_evidence(tmp_path):
    """One genuine, byte-backed session where every capability can pass."""
    png = tmp_path / "frame.png"
    png.write_bytes(make_png([(255, 0, 0), (0, 255, 0),
                              (0, 0, 255), (255, 255, 255)]))
    wav = tmp_path / "capture.wav"
    wav.write_bytes(make_wav([1000, -1000] * 2))
    native_sha = "a" * 64
    records = [
        write_json(tmp_path / "endpoint.json", endpoint_record()),
        write_json(tmp_path / "frames.json",
                   frames_record(png.name, cbe.sha256_file(png))),
        write_json(tmp_path / "audio.json",
                   audio_record(wav.name, cbe.sha256_file(wav), frames=4)),
        write_json(tmp_path / "native.json", native_record(native_sha)),
    ]
    return records, [wav], [png], native_sha


def test_console_only_and_silent_captures_are_never_promoted(tmp_path):
    records = [
        write_json(tmp_path / "endpoint.json", endpoint_record(
            kind="highball-wine", scope="console-only")),
        write_json(tmp_path / "frames.json", frames_record(
            "x.png", "c" * 64, scope="console-only")),
        write_json(tmp_path / "audio.json", audio_record(
            "x.wav", "d" * 64, non_silent=False)),
        write_json(tmp_path / "native.json", native_record(None)),
    ]
    report = cbe.join(records)
    assert report["verdict"] == cbe.BLOCKED
    caps = report["capabilities"]
    assert "EMULATED_ENDPOINT" in caps["windows-endpoint"]["codes"]
    assert "ENDPOINT_CONSOLE_SCOPE" in caps["windows-endpoint"]["codes"]
    assert "CONSOLE_ONLY" in caps["gui-frames"]["codes"]
    assert "AUDIO_SILENT" in caps["audio"]["codes"]
    assert "NATIVE_HASH_NULL" in caps["native-hash"]["codes"]
    assert all(blocker.split(":", 1)[1] in cbe.REASON_TEXT
               for blocker in report["blockers"])


def test_all_zero_wav_bytes_are_silent_even_with_a_matching_hash(tmp_path):
    wav = tmp_path / "silent.wav"
    wav.write_bytes(make_wav([0] * 8))
    record = write_json(tmp_path / "audio.json", audio_record(
        wav.name, cbe.sha256_file(wav), non_silent=True, frames=8))
    result = cbe.join([record], [wav])["capabilities"]["audio"]
    assert result["verdict"] != cbe.PASS
    assert "AUDIO_SILENT" in result["codes"]
    assert result["detail"]["peak"] == 0
    assert result["detail"]["non_silent"] is False


def test_claims_without_supplied_bytes_cannot_fake_pass(tmp_path):
    record = write_json(tmp_path / "frames.json", frames_record("missing.png", "e" * 64))
    audio = write_json(tmp_path / "audio.json", audio_record("missing.wav", "f" * 64))
    report = cbe.join([record, audio])
    assert report["verdict"] != cbe.PASS
    assert "FRAME_NO_BYTES" in report["capabilities"]["gui-frames"]["codes"]
    assert "AUDIO_NO_BYTES" in report["capabilities"]["audio"]["codes"]


def test_hash_mismatch_on_supplied_bytes_blocks_audio(tmp_path):
    silent = tmp_path / "silent.wav"
    silent.write_bytes(make_wav([0] * 8))
    record = write_json(tmp_path / "audio.json", audio_record(
        silent.name, "1" * 64, non_silent=True, frames=8))
    result = cbe.join([record], [silent])["capabilities"]["audio"]
    assert result["verdict"] == cbe.BLOCKED
    assert "AUDIO_HASH_MISMATCH" in result["codes"]


def test_byte_backed_evidence_can_pass_all_capabilities(tmp_path):
    records, wavs, frames, native_sha = full_evidence(tmp_path)
    report = cbe.join(records, wavs, frames, native_sha)
    assert report["verdict"] == cbe.PASS
    assert report["session_codes"] == []
    assert all(c["verdict"] == cbe.PASS for c in report["capabilities"].values())


def test_session_mismatch_prevents_overall_pass(tmp_path):
    records, wavs, frames, native_sha = full_evidence(tmp_path)
    records[-1] = write_json(records[-1].parent / "native.json",
                             native_record(native_sha, capture_id="session-2"))
    report = cbe.join(records, wavs, frames, native_sha)
    assert all(c["verdict"] == cbe.PASS for c in report["capabilities"].values())
    assert "SESSION_MISMATCH" in report["session_codes"]
    assert report["verdict"] != cbe.PASS


def test_native_hash_mismatch_blocks(tmp_path):
    record = write_json(tmp_path / "native.json", native_record("b" * 64))
    result = cbe.join([record], expected_native="a" * 64)
    assert result["verdict"] == cbe.BLOCKED
    assert "NATIVE_HASH_MISMATCH" in result["capabilities"]["native-hash"]["codes"]


def test_verify_detects_tampered_record(tmp_path):
    records, wavs, frames, native_sha = full_evidence(tmp_path)
    report_path = tmp_path / "report.json"
    report = cbe.join(records, wavs, frames, native_sha)
    write_json(report_path, report)
    assert cbe.verify_report(report_path) == []

    # Downgrade the verified retail-Windows endpoint to unverified.
    endpoint = json.loads(records[0].read_text())
    endpoint["endpoint"]["verified"] = False
    write_json(records[0], endpoint)
    assert cbe.verify_report(report_path)
    assert cbe.main(["--verify", str(report_path)]) == cbe.EXIT_TAMPERED


def test_verify_detects_tampered_report(tmp_path):
    records, wavs, frames, native_sha = full_evidence(tmp_path)
    report_path = tmp_path / "report.json"
    report = cbe.join(records, wavs, frames, native_sha)
    report["verdict"] = cbe.BLOCKED
    write_json(report_path, report)
    assert cbe.verify_report(report_path)


def test_cli_exit_codes(tmp_path):
    records, wavs, frames, native_sha = full_evidence(tmp_path)
    assert cbe.main(["--record", str(records[0]), "--record", str(records[1]),
                     "--record", str(records[2]), "--record", str(records[3]),
                     "--wav", str(wavs[0]), "--frame", str(frames[0]),
                     "--native-sha256", native_sha,
                     "--out", str(tmp_path / "out.json")]) == cbe.EXIT_PASS
    assert cbe.main(["--record", str(records[0]),
                     "--out", str(tmp_path / "out2.json")]) == cbe.EXIT_PARTIAL


def test_committed_proof_and_doc_report_blockers_without_closing_issues(monkeypatch):
    assert DOC.is_file()
    doc = DOC.read_text(encoding="utf-8")
    for fact in ("254872", "304064", "4 of 9",
                 "b6674fd6da06bc9dd36a49a5697efdeb8af7bd354f00b9c6d77359533b572d8f"):
        assert fact in doc, fact
    assert "remain OPEN" in doc
    assert re.search(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?)\s+(?:issue|#)\s*14[56]\b",
                     doc) is None
    for name in ("windows-console-captured.json", "audio-descript-loopback.json",
                 "audio-screencapturekit.json", "gui-cua-clicks.json",
                 "native-unavailable.json", "joined-report.json"):
        assert (PROOF / name).is_file(), name
    report = json.loads((PROOF / "joined-report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == cbe.PARTIAL
    assert "EMULATED_ENDPOINT" in report["capabilities"]["windows-endpoint"]["codes"]
    assert "AUDIO_SILENT" in report["capabilities"]["audio"]["codes"]
    monkeypatch.chdir(ROOT)
    assert cbe.verify_report(PROOF / "joined-report.json") == []
