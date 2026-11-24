#!/usr/bin/env python3
"""Deterministic byte-level game-audio acceptance helper.

This tool does NOT run a game and does NOT claim audible or correct in-game
audio.  What it proves is narrower and checkable: a known guest PCM tone is
built programmatically (fixed tone, sample count and RIFF/WAVE header), pushed
through an *injectable* host SDL-audio-style stream recorder in
SDL-callback-sized chunks, and the captured bytes are compared with the
guest-provided PCM by SHA-256, sample/frame count, duration and byte count.

The host-device gate is fail-closed.  A dummy/headless SDL audio driver yields
``NOT TESTED``; an unavailable device or missing real recorder yields
``BLOCKED``.  Neither can become ``PASS`` even when the injected recorder's
bytes match.  The CLI never injects a real device recorder, so a CLI run can
only report NOT TESTED / BLOCKED; ``PASS`` is reserved for a caller that
supplies a genuine host capture through ``accept(recorder=...)``.

All file paths come from CLI arguments (``--report`` and the optional
``--fixture``); nothing is read from a game installation or a user home.  Exit
status is 0 for PASS / NOT TESTED / BLOCKED (an honest skip is not an error)
and 1 for FAIL (byte mismatch).

Usage:
  python3 -m tools.audio_acceptance --report <report.json> [--fixture <tone.wav>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol


# ---- verdict / device vocabulary ------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
NOT_TESTED = "NOT TESTED"
BLOCKED = "BLOCKED"

AVAILABLE = "AVAILABLE"       # a real device plus a real injected recorder
DUMMY = "DUMMY"               # SDL dummy/headless driver: no audio happened
UNAVAILABLE = "UNAVAILABLE"   # no device / no real recorder available

DUMMY_DRIVERS = frozenset({
    "dummy", "sdl-dummy", "dummy-device", "dummy-device-audio", "headless",
    "none", "null", "stub", "fake",
})

NOTE = ("Byte-level PCM stream evidence only. No live-game execution, no audible "
        "device output and no in-game audio correctness is claimed.")


# ---- deterministic guest fixture ------------------------------------------------
@dataclass(frozen=True)
class FixtureSpec:
    """Fixed, reproducible guest PCM tone (22,050 Hz matches the IGI-1 audio rate)."""
    sample_rate: int = 22050
    channels: int = 1
    sample_width: int = 2          # signed 16-bit little-endian
    frames: int = 4096             # ~0.186 s: small enough for a fast gate
    frequency: int = 440           # A4 sine
    amplitude: int = 8000          # leaves headroom below int16 max


FIXTURE = FixtureSpec()


def build_wav(pcm: bytes, *, sample_rate: int, channels: int,
              sample_width: int) -> bytes:
    """Wrap raw PCM in a canonical RIFF/WAVE header (PCM format tag 1)."""
    if channels not in (1, 2) or sample_width not in (1, 2, 3, 4) or sample_rate <= 0:
        raise ValueError("unsupported PCM format")
    block_align = channels * sample_width
    byte_rate = sample_rate * block_align
    # RIFF chunks are word-aligned; the parser uses the declared data size, so
    # the pad byte (when present) is never mistaken for a sample.
    pad = b"\0" if len(pcm) % 2 else b""
    header = (
        b"RIFF" + struct.pack("<I", 36 + len(pcm) + len(pad)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                                byte_rate, block_align, sample_width * 8)
        + b"data" + struct.pack("<I", len(pcm))
    )
    return header + pcm + pad


def build_tone_wav(spec: FixtureSpec = FIXTURE) -> bytes:
    """Generate the guest tone fixture; identical inputs always produce identical bytes."""
    pcm = bytearray()
    for i in range(spec.frames):
        value = int(round(spec.amplitude
                          * math.sin(2.0 * math.pi * spec.frequency * i / spec.sample_rate)))
        pcm += struct.pack("<h", value)
    return build_wav(bytes(pcm), sample_rate=spec.sample_rate,
                     channels=spec.channels, sample_width=spec.sample_width)


@dataclass(frozen=True)
class GuestPcm:
    pcm: bytes
    sample_rate: int
    channels: int
    sample_width: int
    frames: int


def parse_wav(data: bytes) -> GuestPcm:
    """Parse a RIFF/WAVE PCM fixture into raw samples plus its format metadata."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE fixture")
    fmt: bytes | None = None
    pcm: bytes | None = None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if chunk_id == b"fmt ":
            fmt = body
        elif chunk_id == b"data":
            pcm = body
        pos += 8 + size + (size & 1)
    if fmt is None or len(fmt) < 16 or pcm is None:
        raise ValueError("WAVE fixture is missing fmt/data chunks")
    tag, channels, rate, _byte_rate, align, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if tag != 1 or channels not in (1, 2) or bits not in (8, 16, 24, 32):
        raise ValueError("only uncompressed PCM (8/16/24/32-bit, mono/stereo) is accepted")
    width = bits // 8
    if align != channels * width:
        raise ValueError("inconsistent WAVE block alignment")
    return GuestPcm(pcm=pcm, sample_rate=rate, channels=channels,
                    sample_width=width, frames=len(pcm) // align)


# ---- injectable host stream recorder ---------------------------------------------
class StreamRecorder(Protocol):
    """Seam standing in for the host SDL audio stream (a real capture injects here)."""

    def write(self, chunk: bytes) -> None:
        """Consume one SDL-audio-callback-sized chunk of guest PCM."""
        ...

    def captured(self) -> bytes:
        """Return the PCM the host stream actually consumed."""
        ...


class InMemoryRecorder:
    """Default test/simulation recorder: keeps every chunk verbatim."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, chunk: bytes) -> None:
        self._chunks.append(bytes(chunk))

    def captured(self) -> bytes:
        return b"".join(self._chunks)


class UnavailableRecorder:
    """Fail-closed stand-in used when no real host device capture exists."""

    def write(self, chunk: bytes) -> None:
        raise RuntimeError("no host SDL audio stream recorder available")

    def captured(self) -> bytes:
        return b""


# ---- measurement and comparison --------------------------------------------------
def pcm_metrics(pcm: bytes, *, channels: int, sample_width: int,
                sample_rate: int) -> dict:
    """SHA-256, byte count, sample-frame count and duration of a PCM buffer."""
    align = channels * sample_width
    return {
        "sha256": hashlib.sha256(pcm).hexdigest(),
        "bytes": len(pcm),
        "frames": len(pcm) // align,
        "frame_aligned": len(pcm) % align == 0,
        "sample_rate": sample_rate,
        "duration_s": round((len(pcm) // align) / sample_rate, 6),
    }


def compare_pcm(guest: GuestPcm, captured: bytes) -> dict:
    """Compare host-captured PCM with the guest-provided PCM field by field."""
    expected = pcm_metrics(guest.pcm, channels=guest.channels,
                           sample_width=guest.sample_width,
                           sample_rate=guest.sample_rate)
    got = pcm_metrics(captured, channels=guest.channels,
                      sample_width=guest.sample_width,
                      sample_rate=guest.sample_rate)
    fields = ("sha256", "bytes", "frames", "duration_s", "frame_aligned")
    differences = [field for field in fields if expected[field] != got[field]]
    return {"match": not differences, "guest": expected, "captured": got,
            "differences": differences}


def detect_host_audio_device(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Gate: a dummy driver is NOT TESTED; without a real recorder it is BLOCKED."""
    env = os.environ if env is None else env
    driver = str(env.get("SDL_AUDIODRIVER", "")).strip().lower()
    if driver in DUMMY_DRIVERS:
        return DUMMY, f"SDL_AUDIODRIVER={driver!r} is a dummy/headless audio device"
    return UNAVAILABLE, "no real SDL audio device / stream recorder supplied to the CLI"


def accept(guest_wav: bytes, recorder: StreamRecorder | None = None, *,
           device_state: str | None = None, chunk_frames: int = 512) -> dict:
    """Verify host stream consumption of a guest PCM fixture, fail-closed.

    ``device_state`` is only for callers/tests that genuinely own a real device;
    the CLI leaves it as None and therefore never reaches PASS.
    """
    guest = parse_wav(guest_wav)
    if device_state is None:
        state, reason = detect_host_audio_device()
    else:
        state, reason = device_state, f"caller-supplied device state: {device_state}"
    if state not in (AVAILABLE, DUMMY, UNAVAILABLE):
        raise ValueError(f"unknown device state: {state!r}")

    report = {
        "schema": 1,
        "verdict": BLOCKED,
        "reason": reason,
        "device_state": state,
        "fixture": {
            "wav_sha256": hashlib.sha256(guest_wav).hexdigest(),
            "wav_bytes": len(guest_wav),
            "sample_rate": guest.sample_rate,
            "channels": guest.channels,
            "sample_width": guest.sample_width,
            "pcm": pcm_metrics(guest.pcm, channels=guest.channels,
                               sample_width=guest.sample_width,
                               sample_rate=guest.sample_rate),
        },
        "capture": None,
        "note": NOTE,
    }
    # Device gate first: even a perfect byte match with an injected recorder
    # must not be reported as PASS when no real audio device was involved.
    if state == DUMMY:
        report["verdict"] = NOT_TESTED
        return report
    if state == UNAVAILABLE or recorder is None:
        if state == AVAILABLE and recorder is None:
            report["reason"] = "device reported available but no real recorder was injected"
        return report

    step = max(1, chunk_frames) * guest.channels * guest.sample_width
    for offset in range(0, len(guest.pcm), step):
        recorder.write(guest.pcm[offset:offset + step])
    comparison = compare_pcm(guest, recorder.captured())
    report["capture"] = comparison
    report["verdict"] = PASS if comparison["match"] else FAIL
    report["reason"] = (
        "host SDL audio stream consumed byte-identical guest PCM"
        if comparison["match"]
        else "captured PCM differs in: " + ", ".join(comparison["differences"])
    )
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", required=True, type=Path,
                    help="CLI path for the JSON acceptance report")
    ap.add_argument("--fixture", type=Path,
                    help="optional CLI path to write the generated guest WAV")
    ap.add_argument("--chunk-frames", type=int, default=512,
                    help="SDL-callback-sized chunk in sample frames (default 512)")
    args = ap.parse_args(argv)

    wav = build_tone_wav()
    if args.fixture is not None:
        args.fixture.parent.mkdir(parents=True, exist_ok=True)
        args.fixture.write_bytes(wav)
    report = accept(wav, recorder=None, chunk_frames=args.chunk_frames)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['verdict']}: {report['reason']}")
    print(f"report: {args.report}")
    return 1 if report["verdict"] == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
