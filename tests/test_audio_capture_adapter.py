"""Fail-closed tests for the A8 real-capture adapter.

Every WAV here is a tiny byte fixture written under ``tmp_path``; no game, host
audio device, capture process or user path is involved.  The tests separate a
caller-supplied *real* capture (non-silent, byte-verified) from the repository's
generated synthetic tone, and prove that tampering, silence, dummy devices and
unspecified sources can never be reported as PASS.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import audio_acceptance as aa  # noqa: E402
from tools import audio_capture_adapter as aca  # noqa: E402

DEVICE = "Built-in Output"
RATE = 22050
FRAMES = 1024


def make_capture(frames: int = FRAMES, silence: bool = False,
                 constant: bool = False) -> bytes:
    """A small, non-synthetic PCM WAV that stands in for a device capture."""
    pcm = bytearray()
    for i in range(frames):
        if silence:
            value = 0
        elif constant:
            value = 4000
        else:
            # Two tones: deliberately not the repo's single-tone guest fixture.
            value = int(9000 * math.sin(2 * math.pi * 440 * i / RATE)
                        + 700 * math.sin(2 * math.pi * 97 * i / RATE))
        pcm += struct.pack("<h", value)
    return aa.build_wav(bytes(pcm), sample_rate=RATE, channels=1, sample_width=2)


def claim(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def capture(tmp_path):
    data = make_capture()
    path = tmp_path / "capture.wav"
    path.write_bytes(data)
    return {"path": path, "data": data, "sha256": claim(data)}


def evaluate(capture, **overrides):
    kwargs = {"device_name": DEVICE, "wav_path": capture["path"],
              "source": "live-device", "claimed_sha256": capture["sha256"]}
    kwargs.update(overrides)
    return aca.adapt(**kwargs)


def assert_not_pass(record, verdict):
    assert record["verdict"] == verdict
    assert record["verdict"] != aca.PASS
    assert record["verification"] == aca.UNVERIFIED
    assert record["reasons"]
    assert record["reason"]


# ---------------------------------------------------------------------------
# Real capture: PASS only with byte-level proof
# ---------------------------------------------------------------------------

def test_real_capture_passes_with_recomputed_hash_frames_and_duration(capture, tmp_path):
    record = evaluate(capture, expected_frames=FRAMES,
                      expected_duration_s=FRAMES / RATE)
    assert record["verdict"] == aca.PASS
    assert record["verification"] == aca.VERIFIED
    assert record["reasons"] == []
    assert record["device"]["state"] == aca.REAL
    cap = record["capture"]
    assert cap["sha256_computed"] == claim(capture["data"])
    assert cap["sha256_claimed"] == cap["sha256_computed"]
    assert cap["hash_matches"] is True
    assert cap["riff"] is True and cap["wave"] is True
    assert cap["format_tag"] == 1
    assert (cap["sample_rate"], cap["channels"], cap["sample_width"]) == (RATE, 1, 2)
    assert cap["bytes"] == FRAMES * 2
    assert cap["frames"] == FRAMES
    assert cap["frame_aligned"] is True
    assert cap["duration_s"] == pytest.approx(FRAMES / RATE, abs=1e-6)
    assert cap["non_silent"] is True
    assert cap["peak"] > 0
    assert cap["matches_synthetic_fixture"] is False

    out = aca.write_record(record, tmp_path / "evidence" / "a8.json")
    assert json.loads(out.read_text(encoding="utf-8")) == record
    assert aca.verify_record(out) == []


def test_sample_count_and_duration_claims_are_independently_checked(capture):
    bad_frames = evaluate(capture, expected_frames=FRAMES + 1)
    assert_not_pass(bad_frames, aca.BLOCKED)
    assert "FRAMES_MISMATCH" in bad_frames["reasons"]

    bad_duration = evaluate(capture, expected_duration_s=9.0)
    assert_not_pass(bad_duration, aca.BLOCKED)
    assert "DURATION_MISMATCH" in bad_duration["reasons"]


# ---------------------------------------------------------------------------
# Real vs synthetic: generated tones can never be PASS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spec", [
    aa.FIXTURE,
    aa.FixtureSpec(frames=1024),
    aa.FixtureSpec(frames=8192),
    aa.FixtureSpec(channels=2),
])
def test_generated_synthetic_fixture_never_passes(tmp_path, spec):
    data = aa.build_tone_wav(spec)
    path = tmp_path / "synthetic.wav"
    path.write_bytes(data)
    # Even with a real device name, a live-source attestation and a matching
    # hash, a known generated tone is not host evidence.
    record = aca.adapt(DEVICE, path, source="live-device", claimed_sha256=claim(data))
    assert_not_pass(record, aca.NOT_TESTED)
    assert "SYNTHETIC_FIXTURE" in record["reasons"]
    assert record["capture"]["hash_matches"] is True
    assert record["capture"]["matches_synthetic_fixture"] is True


def test_synthetic_source_attestation_never_passes(capture):
    record = evaluate(capture, source="synthetic")
    assert_not_pass(record, aca.NOT_TESTED)
    assert "SOURCE_NOT_LIVE" in record["reasons"]

    unspecified = aca.adapt(DEVICE, capture["path"],
                            claimed_sha256=capture["sha256"])
    assert_not_pass(unspecified, aca.NOT_TESTED)
    assert "SOURCE_NOT_LIVE" in unspecified["reasons"]


def test_adapter_cannot_generate_a_capture():
    # PASS is derived from supplied bytes: this module has no tone builder and
    # no write path for WAV files.
    assert not hasattr(aca, "build_tone_wav")
    assert not hasattr(aca, "build_wav")
    assert aca.SYNTHETIC_HASHES


# ---------------------------------------------------------------------------
# Dummy devices and missing provenance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"device_name": "dummy"},
    {"device_name": "SDL-DUMMY"},
    {"device_name": "Null Audio Device"},
    {"device_name": "Built-in Output", "driver": "dummy"},
    {"device_name": "Built-in Output", "driver": "headless"},
    {"device_name": "Built-in Output", "kind": "synthetic"},
    {"device_name": "Built-in Output", "kind": "virtual"},
    {"device_name": "Built-in Output", "kind": "something-else"},
])
def test_dummy_or_ambiguous_device_never_passes(capture, kwargs):
    record = evaluate(capture, **kwargs)
    assert_not_pass(record, aca.NOT_TESTED)
    assert "DUMMY_DEVICE" in record["reasons"]
    assert "dummy" in record["reason"].lower()
    assert record["device"]["state"] == aca.DUMMY


def test_missing_device_and_missing_wav_are_not_tested(capture):
    no_device = evaluate(capture, device_name=None)
    assert_not_pass(no_device, aca.NOT_TESTED)
    assert "NO_DEVICE" in no_device["reasons"]

    no_wav = aca.adapt(DEVICE, None, source="live-device",
                       claimed_sha256=capture["sha256"])
    assert_not_pass(no_wav, aca.NOT_TESTED)
    assert "NO_WAV" in no_wav["reasons"]


def test_missing_or_malformed_hash_claim_is_blocked(capture):
    missing = evaluate(capture, claimed_sha256=None)
    assert_not_pass(missing, aca.BLOCKED)
    assert "HASH_MISSING" in missing["reasons"]
    assert missing["capture"]["sha256_computed"] == capture["sha256"]

    malformed = evaluate(capture, claimed_sha256="not-a-hash")
    assert_not_pass(malformed, aca.BLOCKED)
    assert "HASH_MISSING" in malformed["reasons"]


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------

def test_tampered_wav_is_blocked_on_hash_mismatch(capture):
    data = bytearray(capture["data"])
    data[100] ^= 0x01
    capture["path"].write_bytes(bytes(data))
    record = evaluate(capture)
    assert_not_pass(record, aca.BLOCKED)
    assert "HASH_MISMATCH" in record["reasons"]
    assert record["capture"]["hash_matches"] is False
    assert record["capture"]["sha256_computed"] == claim(bytes(data))
    assert record["capture"]["sha256_claimed"] == capture["sha256"]


def test_swapping_wav_for_a_different_real_capture_is_blocked(capture):
    other = make_capture(frames=FRAMES + 8)
    capture["path"].write_bytes(other)
    record = evaluate(capture, expected_frames=FRAMES)
    assert_not_pass(record, aca.BLOCKED)
    assert "HASH_MISMATCH" in record["reasons"]


def test_tampered_file_after_pass_is_detected_by_verify(capture, tmp_path):
    record = evaluate(capture)
    assert record["verdict"] == aca.PASS
    out = aca.write_record(record, tmp_path / "a8.json")
    assert aca.main(["--verify", str(out)]) == aca.EXIT_PASS

    data = bytearray(capture["data"])
    data[60] ^= 0x02
    capture["path"].write_bytes(bytes(data))
    problems = aca.verify_record(out)
    assert problems and any("mismatch" in problem for problem in problems)
    assert aca.main(["--verify", str(out)]) == aca.EXIT_FAILED


def test_tampered_record_without_pass_evidence_is_refused(capture, tmp_path):
    record = evaluate(capture, source="synthetic")
    out = aca.write_record(record, tmp_path / "a8.json")
    assert aca.verify_record(out) == [
        "record verdict is 'NOT TESTED'; no PASS evidence to verify"]


# ---------------------------------------------------------------------------
# Silence and malformed WAV rejection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [{"silence": True}, {"constant": True}])
def test_silent_or_constant_samples_are_blocked(capture, kwargs):
    data = make_capture(**kwargs)
    capture["path"].write_bytes(data)
    record = aca.adapt(DEVICE, capture["path"], source="live-device",
                       claimed_sha256=claim(data))
    assert_not_pass(record, aca.BLOCKED)
    assert "SILENT" in record["reasons"]
    assert record["capture"]["non_silent"] is False


@pytest.mark.parametrize("mutation", [
    b"not a wav at all, just enough bytes to be suspicious",
    b"RIFX" + make_capture()[4:],             # wrong container magic
    b"RIFF" + make_capture()[4:8] + b"AVI " + make_capture()[12:],
    struct.pack("<I", 999) + make_capture()[4:],  # wrong RIFF size
    make_capture()[:-6],                      # truncated data chunk
])
def test_malformed_wave_files_are_blocked(capture, mutation):
    capture["path"].write_bytes(mutation)
    record = evaluate(capture)
    assert_not_pass(record, aca.BLOCKED)
    assert "INVALID_WAVE" in record["reasons"]


def test_non_pcm_format_tag_is_blocked(capture):
    data = bytearray(make_capture())
    struct.pack_into("<H", data, 20, 3)  # IEEE float, not PCM
    capture["path"].write_bytes(bytes(data))
    record = aca.adapt(DEVICE, capture["path"], source="live-device",
                       claimed_sha256=claim(bytes(data)))
    assert_not_pass(record, aca.BLOCKED)
    assert "INVALID_WAVE" in record["reasons"]


def test_missing_wav_file_is_blocked(capture, tmp_path):
    record = aca.adapt(DEVICE, tmp_path / "absent.wav", source="live-device",
                       claimed_sha256=capture["sha256"])
    assert_not_pass(record, aca.BLOCKED)
    assert "WAV_UNREADABLE" in record["reasons"]


# ---------------------------------------------------------------------------
# CLI behaviour: CLI-supplied paths only
# ---------------------------------------------------------------------------

def test_cli_pass_writes_record_at_cli_path(capture, tmp_path, capsys):
    out = tmp_path / "evidence" / "a8.json"
    rc = aca.main(["--device-name", DEVICE, "--source", "live-device",
                   "--wav", str(capture["path"]), "--sha256", capture["sha256"],
                   "--expected-frames", str(FRAMES), "--out", str(out)])
    assert rc == aca.EXIT_PASS
    assert "PASS" in capsys.readouterr().out
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["verdict"] == aca.PASS
    assert record["gate"] == "A8"
    assert record["capture"]["path"] == str(capture["path"])


def test_cli_dummy_device_exits_without_pass(capture, tmp_path, capsys):
    out = tmp_path / "a8.json"
    rc = aca.main(["--device-name", "SDL_DUMMY", "--source", "live-device",
                   "--wav", str(capture["path"]), "--sha256", capture["sha256"],
                   "--out", str(out)])
    assert rc == aca.EXIT_NOT_TESTED
    assert "NOT TESTED" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == aca.NOT_TESTED


def test_cli_bad_record_is_tampered(capture, tmp_path, capsys):
    out = tmp_path / "a8.json"
    aca.main(["--device-name", DEVICE, "--source", "live-device",
              "--wav", str(capture["path"]), "--sha256", capture["sha256"],
              "--out", str(out)])
    data = bytearray(capture["data"])
    data[200] ^= 0x04
    capture["path"].write_bytes(bytes(data))
    assert aca.main(["--verify", str(out)]) == aca.EXIT_FAILED
    assert "TAMPERED" in capsys.readouterr().err


def test_cli_requires_out_unless_verifying():
    with pytest.raises(SystemExit):
        aca.main(["--device-name", DEVICE, "--source", "live-device"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
