#!/usr/bin/env python3
"""Fail-closed A8 adapter for a *real* host audio capture.

``tools/audio_acceptance.py`` can only report NOT TESTED / BLOCKED from the CLI:
it builds a synthetic guest tone and compares it with an injected recorder.  It
never sees a host device.  This adapter is the complementary, deliberately
narrow step: it takes a WAV file that a caller says was captured from a real
output device and the device information reported by that caller's capture
tool, then checks bytes only.

What it proves
--------------
* the caller-supplied SHA-256 matches the SHA-256 recomputed from the WAV's
  actual bytes (so a swapped or tampered fixture is caught);
* the file is a well-formed RIFF/WAVE uncompressed-PCM capture, with a
  consistent header and an integer number of sample frames;
* its sample count and duration match the caller's optional expectations;
* the samples are non-silent; and
* the bytes are not one of the repository's generated synthetic tones.

PASS is issued only when all of those hold **and** the caller explicitly
attests ``--source live-device`` with a non-dummy device name.  A dummy device,
an unspecified/synthetic source, a missing file, a bad header, a hash mismatch,
silence or a known synthetic fixture always yields NOT TESTED or BLOCKED with
an explicit reason code.  The adapter never generates, synthesises or infers a
capture: no flag or code path here can manufacture PASS from generated audio.

This tool reads bytes from CLI-supplied paths only.  It does not run a game, a
capture process, SDL, Wine or a native binary, and it claims no audible output
or in-game audio correctness.  A PASS means "the supplied bytes survive these
checks", not "a human heard the game".

Usage::

    python3 -m tools.audio_capture_adapter \
        --device-name 'Built-in Output' --source live-device \
        --wav /cli/supplied/capture.wav --sha256 <hash-from-capture-tool> \
        --out /cli/supplied/a8-record.json
    python3 -m tools.audio_capture_adapter --verify /cli/supplied/a8-record.json

Exit status is 0 for PASS, 2 for NOT TESTED / BLOCKED (an honest skip is not an
error) and 1 only for a failed ``--verify`` integrity check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any

try:  # allow both `python3 -m tools.audio_capture_adapter` and direct execution
    from tools import audio_acceptance as aa
except ImportError:  # pragma: no cover - exercised only by direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import audio_acceptance as aa

SCHEMA_VERSION = 1
GENERATOR = "tools/audio_capture_adapter.py"
GATE = "A8"

PASS = "PASS"
BLOCKED = "BLOCKED"
NOT_TESTED = "NOT TESTED"
VERIFIED = "CAPTURED"
UNVERIFIED = "NOT TESTED"

EXIT_PASS = 0
EXIT_FAILED = 1
EXIT_NOT_TESTED = 2

REAL = "real"
DUMMY = "dummy"
MISSING = "missing"

NOTE = ("Byte-level PCM capture evidence only. The caller attests a live-device "
        "capture; no game execution, audible output or in-game audio correctness "
        "is claimed.")

# Whole-word matching avoids rejecting a genuine device such as "Realtek".
DUMMY_WORDS = frozenset({
    *aa.DUMMY_DRIVERS,
    "virtual", "synthetic", "generated", "generated-tone", "loopback",
    "null-sink", "alsa-null", "pulse-null", "mock", "test", "unavailable",
    "unknown", "no-device", "not-a-device",
})
DUMMY_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in sorted(
    DUMMY_WORDS, key=lambda w: (-len(w), w))) + r")\b", re.IGNORECASE)
REAL_KINDS = frozenset({"real", "hardware", "physical", "live"})
DUMMY_KINDS = frozenset({"dummy", "virtual", "synthetic", "mock", "test",
                         "generated", "headless", "unavailable", "unknown"})
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class CaptureError(ValueError):
    """A supplied WAV is not a usable RIFF/WAVE PCM capture."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Exact SHA-256 of the file on disk (no caching, no claims copied)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _synthetic_hashes() -> frozenset[str]:
    """Hashes of tones this repository can generate; none may ever be a PASS."""
    specs = (
        aa.FIXTURE,
        aa.FixtureSpec(frames=1024),
        aa.FixtureSpec(frames=8192),
        aa.FixtureSpec(channels=2),
    )
    hashes: set[str] = set()
    for spec in specs:
        wav = aa.build_tone_wav(spec)
        hashes.add(_sha256(wav))
        hashes.add(_sha256(aa.parse_wav(wav).pcm))
    return frozenset(hashes)


SYNTHETIC_HASHES = _synthetic_hashes()


def _device_text(value: Any) -> str:
    """Normalise separators so SDL_DUMMY and dummy-device match the same words."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def classify_device(name: Any, driver: Any = None, kind: Any = None) -> tuple[str, list[str]]:
    """Classify CLI-supplied device info; fail closed on anything ambiguous."""
    dev_name, dev_driver, dev_kind = (_device_text(value)
                                      for value in (name, driver, kind))
    if not dev_name:
        return MISSING, ["NO_DEVICE"]
    kind_norm = dev_kind.lower()
    if kind_norm and kind_norm not in REAL_KINDS:
        # An unrecognised kind is never silently treated as a real device.
        return DUMMY, ["DUMMY_DEVICE"]
    if any(DUMMY_RE.search(value) for value in (dev_name, dev_driver, kind_norm)):
        return DUMMY, ["DUMMY_DEVICE"]
    return REAL, []


def parse_capture(data: bytes) -> dict:
    """Validate a RIFF/WAVE PCM file and return measurements from its bytes."""
    if len(data) < 44:
        raise CaptureError("capture is shorter than a RIFF/WAVE header")
    if data[:4] != b"RIFF":
        raise CaptureError("missing RIFF magic")
    if data[8:12] != b"WAVE":
        raise CaptureError("missing WAVE magic")
    declared = struct.unpack_from("<I", data, 4)[0]
    if declared != len(data) - 8:
        raise CaptureError(f"RIFF size mismatch: declared={declared}, actual={len(data) - 8}")

    fmt: bytes | None = None
    pcm: bytes | None = None
    pcm_offset = 0
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        end = pos + 8 + size
        if end > len(data):
            label = chunk_id.decode("ascii", "replace")
            raise CaptureError(f"truncated {label!r} chunk: declared={size}, available={len(data) - pos - 8}")
        if chunk_id == b"fmt ":
            fmt = data[pos + 8:end]
        elif chunk_id == b"data":
            pcm = data[pos + 8:end]
            pcm_offset = pos + 8
        pos = end + (size & 1)
    if pos < len(data) and not (pos == len(data) - 1 and data[pos] == 0):
        raise CaptureError("unexpected trailing bytes after WAVE chunks")
    if fmt is None or len(fmt) < 16:
        raise CaptureError("missing or incomplete fmt chunk")
    if pcm is None:
        raise CaptureError("missing data chunk")

    tag, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if tag != 1:
        raise CaptureError(f"unsupported WAVE format tag {tag} (only PCM=1 is accepted)")
    if channels not in (1, 2):
        raise CaptureError(f"unsupported channel count {channels} (need mono or stereo)")
    if bits not in (8, 16, 24, 32):
        raise CaptureError(f"unsupported sample width {bits}-bit")
    width = bits // 8
    if rate <= 0:
        raise CaptureError(f"invalid sample rate {rate}")
    if align != channels * width:
        raise CaptureError(f"inconsistent WAVE block alignment {align}")
    if byte_rate != rate * align:
        raise CaptureError(f"inconsistent WAVE byte rate {byte_rate}")
    if not pcm:
        raise CaptureError("data chunk is empty")
    if len(pcm) % align:
        raise CaptureError("PCM data is not frame aligned")
    frames = len(pcm) // align
    return {
        "riff": True, "wave": True, "format_tag": tag,
        "sample_rate": rate, "channels": channels, "sample_width": width,
        "bytes": len(pcm), "frames": frames, "frame_aligned": True,
        "duration_s": round(frames / rate, 6),
        "pcm_offset": pcm_offset, "pcm_sha256": _sha256(pcm),
    }


def _sample_values(pcm: bytes, width: int):
    if width == 1:  # 8-bit WAVE samples are unsigned, centred on 128
        for byte in pcm:
            yield byte - 128
    elif width == 2:
        for (value,) in struct.iter_unpack("<h", pcm):
            yield value
    elif width == 3:
        for offset in range(0, len(pcm), 3):
            yield int.from_bytes(pcm[offset:offset + 3], "little", signed=True)
    else:
        for (value,) in struct.iter_unpack("<i", pcm):
            yield value


def sample_stats(pcm: bytes, width: int) -> dict:
    """Peak and distinct-sample checks; a DC constant or silence is rejected."""
    full_scale = 1 << (8 * width - 1)
    threshold = max(1, full_scale // 100)  # at least 1% of full scale
    peak = 0
    distinct: set[int] = set()
    for value in _sample_values(pcm, width):
        magnitude = abs(value)
        if magnitude > peak:
            peak = magnitude
        if len(distinct) <= 8:
            distinct.add(value)
    return {
        "peak": peak,
        "distinct_samples": len(distinct),
        "non_silent": peak >= threshold and len(distinct) > 1,
    }


def _reason_text(code: str, *, name: str | None, source: str, driver: str | None,
                 claimed: str | None, computed: str | None,
                 expected_frames: int | None, frames: int | None,
                 expected_duration: float | None, duration: float | None,
                 file_error: str | None, wave_error: str | None) -> str:
    messages = {
        "NO_DEVICE": "no real host audio device name was supplied",
        "DUMMY_DEVICE": (f"host audio device is dummy/headless or unknown: "
                         f"name={name!r}, driver={driver!r}"),
        "SOURCE_NOT_LIVE": ("capture source is not an explicit live-device capture: "
                            f"{source or 'unspecified'}"),
        "NO_WAV": "no captured PCM WAV was supplied",
        "WAV_UNREADABLE": (f"captured PCM WAV could not be read: {file_error or 'I/O error'}"),
        "INVALID_WAVE": ("captured PCM WAV is not a well-formed RIFF/WAVE PCM file: "
                         f"{wave_error or 'invalid header'}"),
        "SYNTHETIC_FIXTURE": "captured bytes match a generated synthetic fixture",
        "HASH_MISSING": "no valid 64-hex --sha256 claim was supplied",
        "HASH_MISMATCH": (f"SHA-256 mismatch: claimed={claimed!r}, "
                          f"computed={computed!r}"),
        "SILENT": ("captured samples are silent/near-silent or constant "
                   "(non-silent samples are required)"),
        "FRAMES_MISMATCH": (f"sample frame count mismatch: expected={expected_frames}, "
                            f"actual={frames}"),
        "DURATION_MISMATCH": (f"duration mismatch: expected={expected_duration}, "
                              f"actual={duration}"),
    }
    return messages[code]


def adapt(device_name: Any = None, wav_path: Any = None, *, driver: Any = None,
          kind: Any = None, source: Any = "unspecified",
          claimed_sha256: Any = None, expected_frames: Any = None,
          expected_duration_s: Any = None) -> dict:
    """Evaluate one caller-supplied capture and return a fail-closed A8 record."""
    codes: list[str] = []

    device_state, device_codes = classify_device(device_name, driver, kind)
    codes.extend(device_codes)

    source_norm = str(source).strip().lower() if isinstance(source, str) else ""
    if source_norm != "live-device":
        codes.append("SOURCE_NOT_LIVE")

    claim = str(claimed_sha256).strip().lower() if isinstance(claimed_sha256, str) else ""
    if not HASH_RE.fullmatch(claim):
        codes.append("HASH_MISSING")
        claim = None  # type: ignore[assignment]

    file_error: str | None = None
    wave_error: str | None = None
    capture: dict[str, Any] = {
        "path": str(wav_path) if wav_path is not None else None,
        "source": source_norm or "unspecified",
        "sha256_claimed": claim,
        "sha256_computed": None,
        "hash_matches": None,
        "riff": None, "wave": None, "format_tag": None,
        "sample_rate": None, "channels": None, "sample_width": None,
        "bytes": None, "frames": None, "frame_aligned": None,
        "duration_s": None, "expected_frames": expected_frames,
        "expected_duration_s": expected_duration_s,
        "peak": None, "distinct_samples": None, "non_silent": None,
        "matches_synthetic_fixture": None,
        "file_error": None, "wave_error": None,
    }

    data: bytes | None = None
    if wav_path is None:
        codes.append("NO_WAV")
    else:
        path = Path(wav_path)
        capture["path"] = str(path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            codes.append("WAV_UNREADABLE")
            file_error = f"{type(exc).__name__}: {exc}"
            capture["file_error"] = file_error
        else:
            computed = _sha256(data)
            capture["sha256_computed"] = computed
            capture["hash_matches"] = computed == claim if claim else None
            if claim and computed != claim:
                codes.append("HASH_MISMATCH")
            if computed in SYNTHETIC_HASHES:
                codes.append("SYNTHETIC_FIXTURE")
                capture["matches_synthetic_fixture"] = True
            try:
                metrics = parse_capture(data)
            except CaptureError as exc:
                codes.append("INVALID_WAVE")
                wave_error = str(exc)
                capture["wave_error"] = wave_error
            else:
                capture.update({key: value for key, value in metrics.items()
                                if key not in ("pcm_sha256", "pcm_offset")})
                capture["matches_synthetic_fixture"] = (
                    capture["matches_synthetic_fixture"] is True
                    or metrics["pcm_sha256"] in SYNTHETIC_HASHES)
                if metrics["pcm_sha256"] in SYNTHETIC_HASHES:
                    codes.append("SYNTHETIC_FIXTURE")
                stats = sample_stats(
                    data[metrics["pcm_offset"]:metrics["pcm_offset"] + metrics["bytes"]],
                    metrics["sample_width"])
                capture.update(stats)
                if not stats["non_silent"]:
                    codes.append("SILENT")
                if expected_frames is not None and expected_frames != metrics["frames"]:
                    codes.append("FRAMES_MISMATCH")
                if expected_duration_s is not None:
                    tolerance = max(1e-3, 1.0 / metrics["sample_rate"])
                    if abs(float(expected_duration_s) - metrics["duration_s"]) > tolerance:
                        codes.append("DURATION_MISMATCH")

    # Device/source gates decide NOT TESTED; anything else that failed is
    # BLOCKED.  A synthetic fixture is NOT TESTED: no real capture happened.
    skip_codes = {"NO_DEVICE", "DUMMY_DEVICE", "SOURCE_NOT_LIVE", "NO_WAV",
                  "SYNTHETIC_FIXTURE"}
    if any(code in skip_codes for code in codes):
        verdict = NOT_TESTED
    elif codes:
        verdict = BLOCKED
    else:
        verdict = PASS

    # Stable order, deduplicated: the first failure is the most specific one.
    ordered_codes: list[str] = []
    for code in codes:
        if code not in ordered_codes:
            ordered_codes.append(code)
    reasons = [_reason_text(
        code, name=device_name if isinstance(device_name, str) else None,
        source=source_norm, driver=driver if isinstance(driver, str) else None,
        claimed=claim, computed=capture["sha256_computed"],
        expected_frames=expected_frames, frames=capture["frames"],
        expected_duration=expected_duration_s,
        duration=capture["duration_s"], file_error=file_error,
        wave_error=wave_error) for code in ordered_codes]

    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "gate": GATE,
        "verdict": verdict,
        "verification": VERIFIED if verdict == PASS else UNVERIFIED,
        "reason": "; ".join(reasons) or "live-device PCM capture passed all byte-level checks",
        "reasons": ordered_codes,
        "device": {
            "name": device_name if isinstance(device_name, str) else None,
            "driver": driver if isinstance(driver, str) else None,
            "kind": kind if isinstance(kind, str) else None,
            "state": device_state,
            "source": "cli",
        },
        "capture": capture,
        "note": NOTE,
    }


def write_record(record: dict, out: Any) -> Path:
    """Write deterministic JSON to a CLI-supplied path only."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    return out


def verify_record(path: Any) -> list[str]:
    """Recompute every PASS claim against the bytes still on disk."""
    problems: list[str] = []
    path = Path(path)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return [f"cannot read record: {type(exc).__name__}: {exc}"]
    if not isinstance(record, dict):
        return ["record is not a JSON object"]
    if record.get("verdict") != PASS:
        return [f"record verdict is {record.get('verdict')!r}; no PASS evidence to verify"]
    capture = record.get("capture")
    if not isinstance(capture, dict):
        return ["record has no capture object"]
    wav = capture.get("path")
    if not isinstance(wav, str) or not wav:
        return ["record has no capture path"]
    try:
        data = Path(wav).read_bytes()
    except OSError as exc:
        return [f"capture file is unreadable: {type(exc).__name__}: {exc}"]
    computed = _sha256(data)
    for label in ("sha256_computed", "sha256_claimed"):
        expected = capture.get(label)
        if expected != computed:
            problems.append(f"{label} mismatch: recorded={expected!r}, actual={computed!r}")
    try:
        metrics = parse_capture(data)
    except CaptureError as exc:
        problems.append(f"capture is not valid RIFF/WAVE PCM: {exc}")
        return problems
    for key in ("riff", "wave", "format_tag", "sample_rate", "channels",
                "sample_width", "bytes", "frames", "frame_aligned", "duration_s"):
        if capture.get(key) != metrics[key]:
            problems.append(f"{key} mismatch: recorded={capture.get(key)!r}, "
                            f"actual={metrics[key]!r}")
    pcm = data[metrics["pcm_offset"]:metrics["pcm_offset"] + metrics["bytes"]]
    stats = sample_stats(pcm, metrics["sample_width"])
    if capture.get("non_silent") is not stats["non_silent"]:
        problems.append("non_silent mismatch: samples no longer pass the silence gate")
    if computed in SYNTHETIC_HASHES or metrics["pcm_sha256"] in SYNTHETIC_HASHES:
        problems.append("capture bytes are a generated synthetic fixture")
    if record.get("reasons"):
        problems.append("PASS record unexpectedly carries failure reasons")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify", type=Path, metavar="RECORD",
                    help="recompute a PASS record's hashes against the bytes on disk")
    ap.add_argument("--device-name", help="real host output device name supplied by the capture tool")
    ap.add_argument("--device-driver", help="optional host audio driver (dummy drivers are rejected)")
    ap.add_argument("--device-kind", default="real",
                    help="'real' (default) or an explicit non-real kind (never PASS)")
    ap.add_argument("--source", default="unspecified",
                    choices=("live-device", "synthetic", "unspecified"),
                    help="caller attestation of capture provenance; PASS needs live-device")
    ap.add_argument("--wav", type=Path, help="CLI path to the real captured PCM WAV")
    ap.add_argument("--sha256", dest="claimed_sha256",
                    help="SHA-256 claimed by the capture tool for the WAV bytes")
    ap.add_argument("--expected-frames", type=int,
                    help="optional sample-frame count claimed by the capture tool")
    ap.add_argument("--expected-duration", type=float,
                    help="optional duration in seconds claimed by the capture tool")
    ap.add_argument("--out", type=Path, help="CLI path for the fail-closed A8 record")
    args = ap.parse_args(argv)

    if args.verify is not None:
        problems = verify_record(args.verify)
        if problems:
            for problem in problems:
                print(f"TAMPERED: {problem}", file=sys.stderr)
            return EXIT_FAILED
        print(f"OK: {args.verify}")
        return EXIT_PASS

    if args.out is None:
        ap.error("--out is required unless --verify is used")

    record = adapt(args.device_name, args.wav, driver=args.device_driver,
                   kind=args.device_kind, source=args.source,
                   claimed_sha256=args.claimed_sha256,
                   expected_frames=args.expected_frames,
                   expected_duration_s=args.expected_duration)
    write_record(record, args.out)
    print(f"{record['verdict']}: {record['reason']}")
    print(f"record: {args.out}")
    return EXIT_PASS if record["verdict"] == PASS else EXIT_NOT_TESTED


if __name__ == "__main__":
    raise SystemExit(main())
