"""Tests for deterministic audio acceptance and its fail-closed device gate."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import audio_acceptance as aa
from tools.audio_acceptance import FIXTURE


class FakeRecorder:
    """Fake host SDL audio stream recorder; an optional tamper hook mutates the capture."""

    def __init__(self, tamper=None):
        self.chunks = []
        self.tamper = tamper

    def write(self, chunk: bytes) -> None:
        self.chunks.append(bytes(chunk))

    def captured(self) -> bytes:
        data = b"".join(self.chunks)
        return self.tamper(data) if self.tamper else data


def flip_middle(data: bytes) -> bytes:
    out = bytearray(data)
    out[len(out) // 2] ^= 0x01
    return bytes(out)


def truncate_half(data: bytes) -> bytes:
    return data[:len(data) // 2]


class AudioAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.wav = aa.build_tone_wav()

    def test_fixture_is_deterministic_and_well_formed(self):
        self.assertEqual(self.wav, aa.build_tone_wav())
        self.assertEqual(self.wav[:4], b"RIFF")
        self.assertEqual(self.wav[8:12], b"WAVE")
        guest = aa.parse_wav(self.wav)
        self.assertEqual(guest.frames, FIXTURE.frames)
        self.assertEqual(guest.sample_rate, FIXTURE.sample_rate)
        self.assertEqual((guest.channels, guest.sample_width), (1, 2))
        self.assertEqual(aa.parse_wav(self.wav).pcm, guest.pcm)
        self.assertEqual(len(guest.pcm), FIXTURE.frames * 2)
        self.assertNotEqual(guest.pcm, b"\0" * len(guest.pcm))  # a real tone, not silence

    def test_chunked_stream_consumes_every_byte(self):
        guest = aa.parse_wav(self.wav)
        rec = FakeRecorder()
        aa.accept(self.wav, rec, device_state=aa.AVAILABLE, chunk_frames=100)
        self.assertEqual(len(rec.chunks), (FIXTURE.frames + 99) // 100)
        self.assertTrue(all(len(chunk) <= 200 for chunk in rec.chunks))
        self.assertEqual(rec.captured(), guest.pcm)

    def test_fake_recorder_match_is_pass_with_sha_frames_and_duration(self):
        rec = FakeRecorder()
        report = aa.accept(self.wav, rec, device_state=aa.AVAILABLE)
        self.assertEqual(report["verdict"], aa.PASS)
        self.assertTrue(report["capture"]["match"])
        self.assertEqual(report["capture"]["differences"], [])
        guest = aa.parse_wav(self.wav)
        expected = aa.pcm_metrics(guest.pcm, channels=1, sample_width=2,
                                  sample_rate=FIXTURE.sample_rate)
        self.assertEqual(report["capture"]["captured"]["sha256"], expected["sha256"])
        self.assertEqual(report["capture"]["captured"]["sha256"],
                         hashlib.sha256(guest.pcm).hexdigest())
        self.assertEqual(report["capture"]["captured"]["frames"], FIXTURE.frames)
        self.assertAlmostEqual(report["capture"]["captured"]["duration_s"],
                               FIXTURE.frames / FIXTURE.sample_rate, places=6)
        self.assertEqual(report["fixture"]["pcm"]["frames"], FIXTURE.frames)

    def test_tampered_sample_is_fail_even_with_equal_length(self):
        rec = FakeRecorder(tamper=flip_middle)
        report = aa.accept(self.wav, rec, device_state=aa.AVAILABLE)
        self.assertEqual(report["verdict"], aa.FAIL)
        self.assertFalse(report["capture"]["match"])
        self.assertIn("sha256", report["capture"]["differences"])
        self.assertEqual(report["capture"]["guest"]["frames"],
                         report["capture"]["captured"]["frames"])
        self.assertEqual(report["capture"]["guest"]["duration_s"],
                         report["capture"]["captured"]["duration_s"])
        self.assertIn("sha256", report["reason"])

    def test_truncated_stream_is_fail_on_sample_count_and_duration(self):
        rec = FakeRecorder(tamper=truncate_half)
        report = aa.accept(self.wav, rec, device_state=aa.AVAILABLE)
        self.assertEqual(report["verdict"], aa.FAIL)
        for field in ("sha256", "bytes", "frames", "duration_s"):
            self.assertIn(field, report["capture"]["differences"])
        self.assertEqual(report["capture"]["captured"]["frames"], FIXTURE.frames // 2)

    def test_dummy_device_never_passes_even_with_matching_recorder(self):
        rec = FakeRecorder()  # byte-perfect capture
        report = aa.accept(self.wav, rec, device_state=aa.DUMMY)
        self.assertEqual(report["verdict"], aa.NOT_TESTED)
        self.assertNotEqual(report["verdict"], aa.PASS)
        self.assertIsNone(report["capture"])
        self.assertIn("dummy", report["reason"].lower())
        self.assertIn("no live-game", report["note"].lower())

    def test_unavailable_device_is_blocked_not_pass(self):
        report = aa.accept(self.wav, FakeRecorder(), device_state=aa.UNAVAILABLE)
        self.assertEqual(report["verdict"], aa.BLOCKED)
        self.assertIsNone(report["capture"])

    def test_available_device_without_real_recorder_is_blocked(self):
        report = aa.accept(self.wav, None, device_state=aa.AVAILABLE)
        self.assertEqual(report["verdict"], aa.BLOCKED)
        self.assertIn("no real recorder", report["reason"])

    def test_device_detection_rejects_dummy_and_headless_env(self):
        for driver in ("dummy", "SDL-DUMMY", "dummy-device", "headless"):
            with self.subTest(driver=driver):
                state, reason = aa.detect_host_audio_device({"SDL_AUDIODRIVER": driver})
                self.assertEqual(state, aa.DUMMY)
                self.assertIn("dummy", reason.lower())
        state, _ = aa.detect_host_audio_device({})
        self.assertEqual(state, aa.UNAVAILABLE)

    def test_cli_writes_report_and_fixture_only_at_cli_supplied_paths(self):
        with tempfile.TemporaryDirectory() as d:
            report_path = Path(d) / "evidence" / "audio.json"
            fixture_path = Path(d) / "evidence" / "tone.wav"
            env = {"SDL_AUDIODRIVER": "dummy"}
            with mock.patch.dict(os.environ, env):
                rc = aa.main(["--report", str(report_path),
                              "--fixture", str(fixture_path)])
            self.assertEqual(rc, 0)
            self.assertEqual(fixture_path.read_bytes(), self.wav)
            report = json.loads(report_path.read_text())
            self.assertEqual(report["verdict"], aa.NOT_TESTED)
            self.assertIsNone(report["capture"])
            self.assertEqual(report["fixture"]["wav_sha256"],
                             hashlib.sha256(self.wav).hexdigest())
        with tempfile.TemporaryDirectory() as d:
            report_path = Path(d) / "blocked.json"
            with mock.patch.dict(os.environ, {"SDL_AUDIODRIVER": ""}):
                rc = aa.main(["--report", str(report_path)])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(report_path.read_text())["verdict"], aa.BLOCKED)
            self.assertFalse((Path(d) / "tone.wav").exists())

    def test_bad_device_state_is_rejected(self):
        with self.assertRaises(ValueError):
            aa.accept(self.wav, FakeRecorder(), device_state="REAL-TRUST-ME")


if __name__ == "__main__":
    unittest.main()
