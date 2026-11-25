#!/usr/bin/env python3
"""Fail-closed original-vs-native scenario oracle for game-target issues.

This CLI drives **one original** Windows game under a caller-supplied
Highball/Wine endpoint and **one native recompiled binary**, captures real
screenshot frames for each side, recomputes every SHA-256 from the bytes on
disk, and emits a verdict.

Verdict contract
----------------
* ``PASS`` requires: a real executed original run, a real executed native run,
  real non-blank frame artifacts on **both** sides independently confirmed to
  belong to the game window, the *same deterministic input bytes delivered to
  both sides* (their hashes must match each other and the declared
  ``--input-file``), a coarse frame-layout similarity above the caller's
  declared threshold, and a clean independent integrity re-check.  Anything
  less is never promoted to PASS.
* ``PARTIAL`` means at least one side produced honest evidence but the pair
  cannot be compared (e.g. no native recompilation exists yet, a side did not
  execute, input hashes differ, or frame similarity is below threshold).
* ``BLOCKED`` means there is no trustworthy original-side evidence at all
  (missing/invalid CLI paths, no original frames, harness failure, tampering).

A verdict of PASS is **only** a pass of this declared scenario comparison.  It
is not a claim that a game target issue is closed, that gameplay/graphics/
audio/input are equivalent, or that any other title, edition or scenario
works.  The record always carries ``not_claimed`` and ``limits`` strings and
``matrix_promotion`` is always false, so the generic acceptance matrix cannot
turn a capture into a game-support PASS.

The injected harness contract (``--harness module:attribute``) is::

    probe(bundle: BundleRequest) -> (bool, reason_string)
    run(request: SideRequest) -> RunOutcome

The default :class:`LocalHarness` uses the real ``highball`` CLI, CUA
``get_desktop_state`` or ``screencapture`` for frames, and ``RECOMP_SCREENSHOT``
for runtime-generated native frames.  Tests inject a fake harness so no game,
Wine process, CUA driver or screen recorder is started.

Usage::

    python3 -m tools.game_target_oracle_run \
        --game-id igi --issue 15 \
        --original /path/to/igi.exe --native /path/to/igi-native \
        --input-file /path/to/replay.bin --replay-input \
        --original-capture cua --native-capture recomp-screenshot \
        --original-window-title 'Project I.G.I.' \
        --highball /path/to/highball --bottle Games \
        --out proof/game-target-oracle/igi-attempt.json
    python3 -m tools.game_target_oracle_run --verify <record.json>
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

SCHEMA = 1
GENERATOR = "tools/game_target_oracle_run.py"

VERDICT_PASS = "PASS"
VERDICT_PARTIAL = "PARTIAL"
VERDICT_BLOCKED = "BLOCKED"
VERDICTS = (VERDICT_PASS, VERDICT_PARTIAL, VERDICT_BLOCKED)

EXIT_PASS = 0
EXIT_PARTIAL = 1
EXIT_BLOCKED = 2
EXIT_TAMPERED = 3

ORACLE_LABEL = "game-target-oracle-dual-run"
CAPTURE_CUA = "cua"
CAPTURE_SCREENCAPTURE = "screencapture"
CAPTURE_RECOMP = "recomp-screenshot"
CAPTURE_BACKENDS = (CAPTURE_CUA, CAPTURE_SCREENCAPTURE, CAPTURE_RECOMP)

DEFAULT_SCREENCAPTURE = "/usr/sbin/screencapture"
DEFAULT_CUA_DRIVER = "/Applications/CuaDriver.app/Contents/MacOS/cua-driver"

GRID = 8                 # coarse 8x8 layout signature, not pixel equality
MAX_SAMPLE_PIXELS = 200_000
MAX_COLORS = 1_000_000
MAX_FRAME_PIXELS = 8_000_000
DEFAULT_MIN_SIMILARITY = 0.85

NOT_CLAIMED = (
    "PASS means only that this one declared original-vs-native scenario "
    "comparison passed: both sides executed, real non-blank frames were "
    "captured from independently confirmed game windows, identical input "
    "bytes were delivered, and a coarse 8x8 frame layout similarity exceeded "
    "the declared threshold. No claim of gameplay, graphics, audio or input "
    "equivalence, issue closure, other editions, other scenarios or other "
    "titles is made."
)
LIMITS = (
    "Frame similarity is a coarse global colour-layout metric, not a pixel, "
    "perceptual or gameplay-state diff; checkpoint state, AI state, timing and "
    "audio are not compared here. Frames come from a Highball/Wine emulation "
    "of the original and from one native process on this host."
)


class HarnessError(Exception):
    """The configured harness cannot provide trustworthy run evidence."""


class ProbeError(HarnessError):
    """A CLI-supplied tool or binary failed its health probe."""


class CaptureError(HarnessError):
    """A side run failed or returned an incomplete/unsafe capture."""


class EndpointError(HarnessError):
    """The injected harness (module:attribute) could not be loaded."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Exact SHA-256 of a file; raise OSError when unreadable (never guess)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(name: str) -> bool:
    candidate = Path(name)
    return (name not in ("", ".", "..") and candidate.name == name)


# ---------------------------------------------------------------------------
# Frame analysis: deterministic, self-contained PNG(8-bit)/BMP(24-bpp) decoder
# ---------------------------------------------------------------------------

def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _png_rows(data: bytes) -> dict:
    """Return unpacked RGB rows for an 8-bit non-interlaced PNG."""
    pos, ihdr, idat, seen_iend = 8, None, bytearray(), False
    while pos + 12 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        if pos + 12 + length > len(data):
            return {"error": "truncated PNG chunk"}
        payload = data[pos + 8:pos + 8 + length]
        crc = struct.unpack_from(">I", data, pos + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            return {"error": f"bad CRC for PNG chunk {kind!r}"}
        if ihdr is None:
            if kind != b"IHDR" or length != 13:
                return {"error": "PNG IHDR must be the first chunk (13 bytes)"}
            ihdr = payload
        elif kind == b"IDAT":
            idat.extend(payload)
        elif kind == b"IEND":
            if length != 0:
                return {"error": "bad PNG IEND length"}
            seen_iend = True
            break
        pos += 12 + length
    if ihdr is None or not seen_iend or not idat:
        return {"error": "incomplete PNG"}
    width, height, depth, colour, comp, filt, interlace = struct.unpack(
        ">IIBBBBB", ihdr)
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colour)
    if (depth != 8 or interlace != 0 or comp != 0 or filt != 0
            or width == 0 or height == 0 or channels is None):
        return {"error": "unsupported PNG variant (need 8-bit, non-interlaced)"}
    if width * height > MAX_FRAME_PIXELS:
        return {"error": "PNG too large to verify"}
    stride = width * channels
    expected = height * (stride + 1)
    stream = zlib.decompressobj()
    raw = stream.decompress(bytes(idat), expected + 1)
    if len(raw) != expected or not stream.eof:
        return {"error": "unexpected PNG scanline size"}

    rows: list[bytes] = []
    previous = bytearray(stride)
    pos = 0
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        row = bytearray(raw[pos:pos + stride])
        pos += stride
        for i in range(stride):
            left = row[i - channels] if i >= channels else 0
            above = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) >> 1
            elif filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            else:
                return {"error": f"unknown PNG filter {filter_type}"}
            row[i] = (row[i] + predictor) & 0xFF
        rgb = bytearray(width * 3)
        for x in range(width):
            if channels == 1:
                grey = row[x]
                rgb[x * 3:x * 3 + 3] = bytes((grey, grey, grey))
            elif channels == 2:
                grey = row[x * channels]
                rgb[x * 3:x * 3 + 3] = bytes((grey, grey, grey))
            else:
                rgb[x * 3:x * 3 + 3] = row[x * channels:x * channels + 3]
        rows.append(bytes(rgb))
        previous = row
    return {"format": "png8", "width": width, "height": height, "rows": rows}


def _bmp_rows(data: bytes) -> dict:
    if len(data) < 54 or data[:2] != b"BM":
        return {"error": "not a 24-bpp BMP"}
    offset = struct.unpack_from("<I", data, 10)[0]
    dib = struct.unpack_from("<I", data, 14)[0]
    width, signed_height = struct.unpack_from("<ii", data, 18)
    planes = struct.unpack_from("<H", data, 26)[0]
    bpp = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    if (dib < 40 or offset < 54 or planes != 1 or bpp != 24 or compression != 0
            or width <= 0 or signed_height == 0):
        return {"error": "unsupported BMP variant (need uncompressed 24-bpp)"}
    height = abs(signed_height)
    if width * height > MAX_FRAME_PIXELS:
        return {"error": "BMP too large to verify"}
    stride = ((width * 3 + 3) // 4) * 4
    if offset + stride * height > len(data):
        return {"error": "truncated BMP pixel data"}
    rows: list[bytes] = []
    top_down = signed_height < 0
    for y in range(height):
        row_offset = offset + (y if top_down else height - 1 - y) * stride
        rgb = bytearray(width * 3)
        for x in range(width):
            p = row_offset + x * 3
            rgb[x * 3:x * 3 + 3] = bytes((data[p + 2], data[p + 1], data[p]))
        rows.append(bytes(rgb))
    return {"format": "bmp24", "width": width, "height": height, "rows": rows}


def analyze_frame(data: bytes, grid: int = GRID) -> dict:
    """Hash/decode raw frame bytes into a coarse, tamper-detectable signature.

    A blank, undecodable or oversized frame yields an ``error`` and no usable
    signature, so it can never take part in a PASS comparison.
    """
    info = _png_rows(data) if data[:8] == b"\x89PNG\r\n\x1a\n" else (
        _bmp_rows(data) if data[:2] == b"BM" else
        {"error": "unsupported frame format (need 8-bit PNG or 24-bpp BMP)"})
    if "error" in info:
        return {"error": info["error"]}
    rows = info["rows"]
    width, height = info["width"], info["height"]
    total = width * height
    step = max(1, total // MAX_SAMPLE_PIXELS)
    colours: set[tuple[int, int, int]] = set()
    sums = [[0, 0, 0] for _ in range(grid * grid)]
    counts = [0] * (grid * grid)
    sampled = 0
    for y in range(0, height, step):
        row = rows[y]
        gy = min(grid - 1, y * grid // height)
        for x in range(0, width, step):
            p = x * 3
            colour = (row[p], row[p + 1], row[p + 2])
            colours.add(colour)
            if len(colours) > MAX_COLORS:
                return {"error": "distinct colour count exceeds verification cap"}
            gx = min(grid - 1, x * grid // width)
            cell = sums[gy * grid + gx]
            cell[0] += colour[0]
            cell[1] += colour[1]
            cell[2] += colour[2]
            counts[gy * grid + gx] += 1
            sampled += 1
    signature = [
        [round(cell[0] / counts[i]) if counts[i] else 0,
         round(cell[1] / counts[i]) if counts[i] else 0,
         round(cell[2] / counts[i]) if counts[i] else 0]
        for i, cell in enumerate(sums)
    ]
    return {
        "format": info["format"], "width": width, "height": height,
        "distinct_colors": len(colours), "blank": len(colours) <= 1,
        "sampled_pixels": sampled, "sample_step": step, "grid": grid,
        "signature": signature,
    }


def frame_similarity(a: dict, b: dict) -> float | None:
    """Coarse 1 - mean-normalised-RGB-difference over the 8x8 signatures."""
    sig_a, sig_b = a.get("signature"), b.get("signature")
    if not isinstance(sig_a, list) or not isinstance(sig_b, list) \
            or len(sig_a) != len(sig_b) or not sig_a:
        return None
    total = 0.0
    for ca, cb in zip(sig_a, sig_b):
        if not isinstance(ca, list) or not isinstance(cb, list):
            return None
        for va, vb in zip(ca[:3], cb[:3]):
            if not isinstance(va, int) or not isinstance(vb, int):
                return None
            total += abs(va - vb)
    return round(1.0 - total / (len(sig_a) * 3 * 255), 6)


# ---------------------------------------------------------------------------
# Request/outcome data model
# ---------------------------------------------------------------------------

@dataclass
class SideRequest:
    role: str                         # "original" | "native"
    binary: Path
    cwd: Path
    args: list[str] = field(default_factory=list)
    capture_backend: str = CAPTURE_CUA
    input_bytes: bytes = b""
    replay_input: bool = False
    frames: int = 2
    settle_sec: float = 3.0
    frame_interval_sec: float = 1.0
    run_seconds: float = 10.0
    timeout: int = 60
    highball: str = ""
    bottle: str = "Games"
    engine: str | None = None
    renderer: str | None = None
    cua_driver: str = DEFAULT_CUA_DRIVER
    screencapture: str = DEFAULT_SCREENCAPTURE
    window_title: str = ""
    artifacts_dir: Path = Path(".")


@dataclass
class BundleRequest:
    game_id: str
    scenario: str
    input_sha256: str
    original: SideRequest | None = None
    native: SideRequest | None = None


@dataclass
class FrameArtifact:
    path: Path
    source: str


@dataclass
class RunOutcome:
    command: list[str]
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    executed: bool = True
    frames: list[FrameArtifact] = field(default_factory=list)
    input_delivered_sha256: str | None = None
    witness: dict = field(default_factory=dict)


class Harness(Protocol):
    def probe(self, bundle: BundleRequest) -> tuple[bool, str]:
        ...

    def run(self, request: SideRequest) -> RunOutcome:
        ...


# ---------------------------------------------------------------------------
# Default real harness
# ---------------------------------------------------------------------------

class Cua:
    """Minimal CUA driver client: window witness + desktop PNG capture."""

    def __init__(self, driver: str, timeout: int = 60):
        self.driver = driver
        self.timeout = timeout
        self.last_error = ""

    def call(self, tool: str, args: dict) -> dict:
        proc = subprocess.run(
            [self.driver, "call", tool, json.dumps(args), "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=self.timeout, check=False)
        try:
            return json.loads(proc.stdout, strict=False)
        except ValueError:
            return {"error": (proc.stdout or "")[-2000:]}

    def window_titles(self) -> list[str]:
        tree = self.call("get_accessibility_tree", {})
        titles = []
        for window in tree.get("windows", []):
            title = window.get("title") or window.get("name") or ""
            if title:
                titles.append(str(title))
        return titles

    def focus_title(self, needle: str, attempts: int = 4) -> dict | None:
        """Bring a window whose title matches ``needle`` to the foreground.

        GUI windows appear asynchronously; a short retry avoids mistaking a
        still-starting game for a process with no window.  No match is still
        reported as ``None`` (never a guessed confirmation).
        """
        for attempt in range(max(1, attempts)):
            tree = self.call("get_accessibility_tree", {})
            for window in tree.get("windows", []):
                title = str(window.get("title") or window.get("name") or "")
                if needle.lower() not in title.lower():
                    continue
                try:
                    pid, wid = int(window["pid"]), int(window["window_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                result = self.call("bring_to_front", {"pid": pid, "window_id": wid})
                return {"pid": pid, "window_id": wid, "title": title,
                        "effect": result.get("effect"), "attempt": attempt + 1}
            if attempt + 1 < max(1, attempts):
                time.sleep(1.0)
        return None

    def capture(self, target: Path) -> bool:
        # The CUA daemon has its own working directory: always pass an
        # absolute path or the write is refused for a non-existent ancestor.
        target = Path(target).resolve()
        rec = self.call("get_desktop_state", {"screenshot_out_file": str(target)})
        if rec.get("error") or rec.get("refusal") or rec.get("status") == "refused":
            self.last_error = json.dumps(
                rec.get("refusal") or rec.get("error") or rec)[:300]
            return False
        return target.is_file() and target.stat().st_size > 0


class LocalHarness:
    """Real Highball/Wine + CUA/screencapture/RECOMP_SCREENSHOT harness."""

    def probe(self, bundle: BundleRequest) -> tuple[bool, str]:
        for side in (bundle.original, bundle.native):
            if side is None:
                continue
            if not side.binary.is_file():
                return False, f"{side.role} binary not found: {side.binary}"
            if not os.access(side.binary, os.R_OK):
                return False, f"{side.role} binary not readable: {side.binary}"
            if side.role == "original":
                if not side.highball or not Path(side.highball).is_file():
                    return False, "highball CLI not found (--highball)"
                if not os.access(side.highball, os.X_OK):
                    return False, "highball CLI not executable"
                if not side.bottle:
                    return False, "highball bottle (--bottle) is required"
            if side.capture_backend == CAPTURE_CUA:
                if not Path(side.cua_driver).is_file():
                    return False, "CUA driver not found (--cua-driver)"
            elif side.capture_backend == CAPTURE_SCREENCAPTURE:
                if not Path(side.screencapture).is_file():
                    return False, "screencapture not found (--screencapture)"
            elif side.capture_backend != CAPTURE_RECOMP:
                return False, f"unknown capture backend: {side.capture_backend}"
        return True, "binaries, highball and capture backends present"

    @staticmethod
    def _build_command(request: SideRequest) -> list[str]:
        if request.role == "original":
            command = [request.highball, "run", "-b", request.bottle]
            if request.engine:
                command += ["-e", request.engine]
            if request.renderer:
                command += ["-r", request.renderer]
            command += ["-d", str(request.cwd), "--", str(request.binary),
                        *request.args]
            return command
        return [str(request.binary), *request.args]

    def run(self, request: SideRequest) -> RunOutcome:
        """Launch one side, deliver input, capture frames, then collect bytes.

        Games do not exit on command, so after the declared scenario window the
        whole process group is stopped.  Frames and output bytes are captured
        *before* that stop; an honest timeout is recorded when the process did
        not exit within ``--timeout``.
        """
        command = self._build_command(request)
        request.artifacts_dir.mkdir(parents=True, exist_ok=True)
        use_recomp = request.capture_backend == CAPTURE_RECOMP
        shot_dir = request.artifacts_dir / "runtime-frames" if use_recomp else None
        cua = Cua(request.cua_driver, request.timeout) \
            if request.capture_backend == CAPTURE_CUA else None
        env: dict[str, str] | None = None
        if shot_dir is not None:
            shot_dir.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ)
            env["RECOMP_SCREENSHOT"] = str(shot_dir.resolve())
            env["RECOMP_SCREENSHOT_MAX"] = str(max(1, request.frames))
            env["RECOMP_SCREENSHOT_EVERY"] = "500"
        witness: dict = {"capture_backend": request.capture_backend,
                         "command": shlex.join(command)}
        if shot_dir is not None:
            witness["recomp_screenshot"] = str(shot_dir.resolve())
        try:
            proc = subprocess.Popen(
                command, cwd=str(request.cwd), env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True)
        except OSError as exc:
            raise CaptureError(f"cannot start {request.role}: {exc}") from exc

        delivered: str | None = None
        frames: list[FrameArtifact] = []
        timed_out = False
        out, err = b"", b""
        started = time.monotonic()
        try:
            if request.replay_input and request.input_bytes:
                if request.role == "original" and cua is not None:
                    result = cua.call("type_text", {
                        "pid": proc.pid,
                        "text": request.input_bytes.decode("utf-8", "replace"),
                        "delivery_mode": "foreground"})
                    if result.get("effect") not in (None, "error", "refused"):
                        delivered = sha(request.input_bytes)
                else:
                    try:
                        proc.stdin.write(request.input_bytes)
                        proc.stdin.flush()
                        delivered = sha(request.input_bytes)
                    except (BrokenPipeError, OSError):
                        delivered = None

            if shot_dir is None:
                time.sleep(max(0.0, request.settle_sec))
                if cua is not None:
                    if request.window_title:
                        witness["focused_window"] = cua.focus_title(request.window_title)
                    witness["window_titles"] = cua.window_titles()
                    witness["window_title_confirmed"] = (
                        not request.window_title
                        or witness.get("focused_window") is not None
                        or any(request.window_title.lower() in t.lower()
                               for t in witness["window_titles"]))
                for index in range(max(0, request.frames)):
                    target = (request.artifacts_dir /
                              f"{request.role}-screen-{index:03d}.png").resolve()
                    if cua is not None:
                        ok = cua.capture(target)
                    elif request.capture_backend == CAPTURE_SCREENCAPTURE:
                        ok = subprocess.run(
                            [request.screencapture, "-x", "-t", "png", str(target)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=request.timeout, check=False).returncode == 0 \
                            and target.is_file()
                    else:
                        ok = False
                    if ok:
                        frames.append(FrameArtifact(
                            path=target, source=request.capture_backend))
                    elif cua is not None and cua.last_error:
                        witness.setdefault("capture_errors", []).append(cua.last_error)
                    if index + 1 < max(0, request.frames):
                        time.sleep(max(0.0, request.frame_interval_sec))

            scenario_deadline = (started + request.settle_sec
                                 + max(0, request.frames - 1)
                                 * request.frame_interval_sec
                                 + max(0.0, request.run_seconds))
            remaining = max(1, min(request.timeout,
                                   int(scenario_deadline - time.monotonic() + 1)))
            try:
                out, err = proc.communicate(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                out, err = exc.output or b"", exc.stderr or b""
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    proc.kill()
                try:
                    out2, err2 = proc.communicate(timeout=5)
                    out, err = out or out2, err or err2
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except OSError:
                        proc.kill()
                    try:
                        out2, err2 = proc.communicate(timeout=5)
                        out, err = out or out2, err or err2
                    except subprocess.TimeoutExpired:
                        pass  # bounded partial output; never claimed as a result
            try:
                proc.stdin.close()
            except OSError:
                pass

        if shot_dir is not None:
            frames = [FrameArtifact(path=p.resolve(), source=CAPTURE_RECOMP)
                      for p in sorted(shot_dir.glob("frame-*.png"))
                      + sorted(shot_dir.glob("frame-*.bmp"))]
        return RunOutcome(
            command=list(command), exit_code=proc.returncode,
            stdout=out or b"", stderr=err or b"", timed_out=timed_out,
            frames=frames, input_delivered_sha256=delivered, witness=witness)


def load_harness(spec: str) -> Harness:
    if spec == "builtin":
        return LocalHarness()
    if ":" not in spec:
        raise EndpointError("--harness must be 'builtin' or 'module:attribute'")
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise EndpointError("--harness must be 'builtin' or 'module:attribute'")
    try:
        obj = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise EndpointError("cannot load harness: " + type(exc).__name__) from None
    harness = obj() if isinstance(obj, type) else obj
    if not callable(getattr(harness, "probe", None)) or not callable(
            getattr(harness, "run", None)):
        raise EndpointError("harness must provide probe() and run()")
    return harness


# ---------------------------------------------------------------------------
# Verdict: pure, fail-closed decision from two independently judged sides
# ---------------------------------------------------------------------------

def usable_analyses(side: dict | None) -> list[dict]:
    """Only frames whose decoded bytes prove real, non-blank pixels count."""
    if not isinstance(side, dict):
        return []
    frames = side.get("frames")
    if not isinstance(frames, list):
        return []
    result = []
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        analysis = frame.get("analysis")
        if isinstance(analysis, dict) and "error" not in analysis \
                and analysis.get("blank") is False:
            result.append(analysis)
    return result


def side_evidence(side: dict | None) -> dict:
    """Normalise one side's stored evidence for the verdict function."""
    if not isinstance(side, dict):
        return {"role": None, "executed": False, "exit_code": None,
                "timed_out": None, "frame_count": 0, "usable_frames": 0,
                "input_delivered_sha256": None, "window_title_confirmed": None,
                "declared_window_title": False, "similarity": None}
    witness = side.get("witness")
    witness = witness if isinstance(witness, dict) else {}
    frames = side.get("frames")
    frames = frames if isinstance(frames, list) else []
    return {
        "role": side.get("role"),
        "executed": side.get("executed") is True,
        "exit_code": side.get("exit_code"),
        "timed_out": side.get("timed_out"),
        "frame_count": len(frames),
        "usable_frames": len(usable_analyses(side)),
        "input_delivered_sha256": side.get("input_delivered_sha256"),
        "window_title_confirmed": witness.get("window_title_confirmed"),
        "declared_window_title": bool(witness.get("declared_window_title", "")),
        "similarity": side.get("similarity"),
    }


def decide_verdict(original: dict | None, native: dict | None,
                   declared_input_sha: str | None,
                   min_similarity: float) -> tuple[str, list[str], dict]:
    """Return ``(verdict, reasons, comparison)`` from normalised side evidence.

    No argument is trusted as a claim: callers pass evidence already rebuilt
    from bytes, and the integrity verifier independently re-derives these
    fields before a recorded PASS is accepted.
    """
    blockers: list[str] = []
    partials: list[str] = []
    comparison: dict = {
        "declared_input_sha256": declared_input_sha,
        "original_input_sha256": None,
        "native_input_sha256": None,
        "input_hashes_match": False,
        "similarity": None,
        "min_similarity": min_similarity,
        "similarity_met": False,
    }
    org = side_evidence(original) if original else None
    nat = side_evidence(native) if native else None

    if org is None:
        blockers.append("NO_ORIGINAL_RUN: no original-side evidence")
        return VERDICT_BLOCKED, blockers, comparison
    if not org["executed"]:
        blockers.append("ORIGINAL_NOT_EXECUTED: original process did not start")
    if org["usable_frames"] < 1:
        blockers.append("NO_ORIGINAL_FRAMES: no real non-blank original frame")
    if org["window_title_confirmed"] is not True:
        partials.append(
            "ORIGINAL_WINDOW_UNCONFIRMED: no independently confirmed game "
            "window; a running process or desktop pixels alone are not evidence")

    if nat is None:
        partials.append(
            "MISSING_NATIVE_INPUT: no native recompiled binary/run supplied; "
            "cannot compare an original against a native side")
    else:
        if not nat["executed"]:
            partials.append("NATIVE_NOT_EXECUTED: native process did not start")
        if nat["usable_frames"] < 1:
            partials.append("NO_NATIVE_FRAMES: no real non-blank native frame")
        if nat["window_title_confirmed"] is not True:
            partials.append(
                "NATIVE_WINDOW_UNCONFIRMED: no independently confirmed native "
                "game window; process exit alone is not evidence")

    comparison["original_input_sha256"] = org["input_delivered_sha256"]
    comparison["native_input_sha256"] = nat["input_delivered_sha256"] if nat else None
    hashes_match = (declared_input_sha is not None and org["input_delivered_sha256"]
                    == declared_input_sha and nat is not None
                    and nat["input_delivered_sha256"] == declared_input_sha)
    comparison["input_hashes_match"] = hashes_match
    if not declared_input_sha:
        partials.append("NO_DECLARED_INPUT: --input-file is required for PASS")
    elif not hashes_match:
        partials.append("INPUT_HASH_MISMATCH: original/native/declared input hashes differ")

    if org["usable_frames"] >= 1 and nat is not None and nat["usable_frames"] >= 1:
        analyses = usable_analyses(original)
        nat_analyses = usable_analyses(native)
        similarity = frame_similarity(analyses[0], nat_analyses[0]) \
            if analyses and nat_analyses else None
        comparison["similarity"] = similarity
        comparison["similarity_met"] = (similarity is not None
                                        and similarity >= min_similarity)
        if not comparison["similarity_met"]:
            partials.append(
                f"SIMILARITY_BELOW_THRESHOLD: {similarity} < {min_similarity}")

    if blockers:
        return VERDICT_BLOCKED, blockers + partials, comparison
    if partials:
        return VERDICT_PARTIAL, partials, comparison
    return VERDICT_PASS, ["SCENARIO_COMPARISON_PASS: " + NOT_CLAIMED], comparison


# ---------------------------------------------------------------------------
# Record building and independent verification
# ---------------------------------------------------------------------------

def _write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _blocked_record(reason: str) -> dict:
    return {
        "schema": SCHEMA, "generator": GENERATOR, "verdict": VERDICT_BLOCKED,
        "status": VERDICT_BLOCKED,
        "oracle": ORACLE_LABEL, "matrix_promotion": False, "reason": reason,
        "reasons": [reason], "game_id": None, "scenario": None,
        "declared_input_sha256": None, "comparison": None,
        "original": None, "native": None, "records": [], "not_claimed": NOT_CLAIMED,
        "limits": LIMITS, "evidence_path": None, "evidence_sha256": None,
    }


def build_side(request: SideRequest, outcome: RunOutcome, artifacts_dir: Path,
               min_similarity: float) -> dict:
    """Persist a side's real bytes and build claims recomputed from them."""
    artifacts_dir = Path(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    side_dir = artifacts_dir / request.role
    side_dir.mkdir(parents=True, exist_ok=True)

    raw_refs: dict[str, dict] = {}
    for key, blob in (("stdout", outcome.stdout), ("stderr", outcome.stderr)):
        target = side_dir / f"{key}.raw"
        target.write_bytes(blob)
        actual = sha(blob)
        if sha256_file(target) != actual:
            raise CaptureError(f"artifact write verification failed: {key}")
        raw_refs[key] = {"file": f"{request.role}/{key}.raw", "bytes": len(blob),
                         "sha256": actual}

    binary_sha = sha256_file(request.binary)
    frame_refs: list[dict] = []
    for index, frame in enumerate(outcome.frames):
        if not frame.path.is_file():
            raise CaptureError(f"captured frame is missing: {frame.path}")
        data = frame.path.read_bytes()
        analysis = analyze_frame(data)
        name = f"{request.role}-frame-{index:03d}{frame.path.suffix or '.png'}"
        target = side_dir / name
        target.write_bytes(data)
        actual = sha(data)
        if sha256_file(target) != actual:
            raise CaptureError(f"frame write verification failed: {name}")
        frame_refs.append({
            "name": frame.path.name, "file": f"{request.role}/{name}",
            "source": frame.source, "bytes": len(data), "sha256": actual,
            "analysis": analysis,
        })

    witness = dict(outcome.witness)
    witness["declared_window_title"] = request.window_title
    return {
        "role": request.role,
        "binary": str(request.binary), "binary_sha256": binary_sha,
        "command": list(outcome.command), "command_display": shlex.join(outcome.command),
        "exit_code": outcome.exit_code, "timed_out": outcome.timed_out,
        "executed": True, "capture_backend": request.capture_backend,
        "input_delivered_sha256": outcome.input_delivered_sha256,
        "raw": raw_refs, "frames": frame_refs, "witness": witness,
    }


def build_record(*, bundle: BundleRequest, harness: Harness,
                 original_outcome: RunOutcome, native_outcome: RunOutcome | None,
                 min_similarity: float, out: Path, issues: list[int]) -> dict:
    artifacts_dir = (out.parent / f"{out.stem}-artifacts").resolve()
    detail_path = (out.parent / f"{out.stem}-evidence.json").resolve()
    original = build_side(bundle.original, original_outcome, artifacts_dir,
                          min_similarity) if bundle.original else None
    native = (build_side(bundle.native, native_outcome, artifacts_dir, min_similarity)
              if bundle.native and native_outcome else None)

    verdict, reasons, comparison = decide_verdict(
        original, native, bundle.input_sha256 or None, min_similarity)

    detail = {
        "schema": SCHEMA, "generator": GENERATOR, "game_id": bundle.game_id,
        "scenario": bundle.scenario, "issues": issues,
        "declared_input_sha256": bundle.input_sha256,
        "original": original, "native": native,
        "verdict": verdict, "reasons": reasons, "comparison": comparison,
        "oracle": ORACLE_LABEL, "matrix_promotion": False,
        "not_claimed": NOT_CLAIMED, "limits": LIMITS,
    }
    _write_json(detail_path, detail)
    detail_sha = sha256_file(detail_path)

    entry = {
        "game_id": bundle.game_id, "scenario": bundle.scenario,
        "oracle": ORACLE_LABEL, "oracle_verified": True,
        "verdict": verdict, "matrix_promotion": False,
        "original_sha256": (original or {}).get("binary_sha256"),
        "native_sha256": (native or {}).get("binary_sha256"),
        "declared_input_sha256": bundle.input_sha256,
        "original_input_sha256": comparison["original_input_sha256"],
        "native_input_sha256": comparison["native_input_sha256"],
        "similarity": comparison["similarity"],
        "evidence_path": str(detail_path), "evidence_sha256": detail_sha,
        "not_claimed": NOT_CLAIMED,
    }
    record = dict(detail)
    record.update({
        "status": verdict, "reason": reasons[0] if reasons else None,
        "verification": "INDEPENDENTLY VERIFIED" if verdict == VERDICT_PASS
                        else "NOT VERIFIED",
        "artifacts_dir": str(artifacts_dir), "evidence_path": str(detail_path),
        "evidence_sha256": detail_sha, "records": [entry],
    })
    _write_json(out, record)
    return record


def verify_record(record_path: Path) -> list[str]:
    """Recompute every hash, frame analysis and verdict from bytes on disk."""
    record_path = Path(record_path)
    problems: list[str] = []
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return [f"cannot read record: {type(exc).__name__}: {exc}"]
    if not isinstance(record, dict):
        return ["record is not a JSON object"]
    if record.get("oracle") != ORACLE_LABEL or record.get("matrix_promotion") is not False:
        problems.append("oracle label or matrix_promotion flag was altered")
    records = record.get("records")
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        return problems + ["record has no single oracle-evidence entry"]
    entry = records[0]
    if record.get("status") not in VERDICTS:
        problems.append("unknown verdict/status")
    if entry.get("verdict") != record.get("status"):
        problems.append("entry verdict disagrees with record verdict")
    if entry.get("oracle") != ORACLE_LABEL or entry.get("matrix_promotion") is not False:
        problems.append("entry oracle label or matrix_promotion was altered")

    evidence_raw = str(entry.get("evidence_path", ""))
    if not evidence_raw:
        return problems + ["no evidence_path"]
    evidence_path = Path(evidence_raw)
    try:
        detail = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return problems + ["evidence file is missing or invalid JSON"]
    if not isinstance(detail, dict):
        return problems + ["evidence file is not a JSON object"]
    actual_evidence_sha = sha256_file(evidence_path)
    if actual_evidence_sha != entry.get("evidence_sha256"):
        problems.append(f"evidence hash mismatch: recorded={entry.get('evidence_sha256')} "
                        f"actual={actual_evidence_sha}")

    artifacts_raw = record.get("artifacts_dir")
    if not artifacts_raw:
        return problems + ["no artifacts_dir"]
    artifacts_dir = Path(artifacts_raw)

    def check_file(label: str, path: Path, claimed: Any) -> bytes | None:
        try:
            data = path.read_bytes()
        except OSError:
            problems.append(f"{label} file is missing: {path}")
            return None
        actual = sha(data)
        if claimed != actual:
            problems.append(f"{label} hash mismatch: recorded={claimed} actual={actual}")
        return data

    def artifact_path(label: str, stored_name: Any, expected_role: str) -> Path | None:
        """Accept only ``<role>/<safe-file>`` under the artifacts directory."""
        name = str(stored_name)
        parts = name.split("/")
        if (len(parts) != 2 or parts[0] != expected_role
                or not safe_name(parts[1])):
            problems.append(f"unsafe {label} artifact name: {name!r}")
            return None
        return artifacts_dir / parts[0] / parts[1]

    sides: dict[str, dict] = {}
    for role in ("original", "native"):
        stored = detail.get(role)
        if role == "native" and stored is None:
            continue
        if not isinstance(stored, dict):
            problems.append(f"{role} evidence is not an object")
            continue
        sides[role] = stored
        check_file(f"{role} binary", Path(stored.get("binary", "")),
                   stored.get("binary_sha256"))
        raw_meta = stored.get("raw", {})
        if not isinstance(raw_meta, dict):
            problems.append(f"{role} raw metadata is not an object")
            raw_meta = {}
        for key in ("stdout", "stderr"):
            meta = raw_meta.get(key, {})
            if not isinstance(meta, dict):
                problems.append(f"{role} {key} metadata invalid")
                continue
            raw_path = artifact_path(f"{role} {key}", meta.get("file"), role)
            if raw_path is None:
                continue
            blob = check_file(f"{role} {key}", raw_path, meta.get("sha256"))
            if blob is not None and meta.get("bytes") != len(blob):
                problems.append(f"{role} {key} byte-count mismatch")
        frame_refs = stored.get("frames", [])
        if not isinstance(frame_refs, list):
            problems.append(f"{role} frames is not a list")
            frame_refs = []
        for index, frame in enumerate(frame_refs):
            if not isinstance(frame, dict):
                problems.append(f"{role} frame {index} is not an object")
                continue
            frame_path = artifact_path(f"{role} frame {index}", frame.get("file"), role)
            if frame_path is None:
                continue
            data = check_file(f"{role} frame {index}", frame_path,
                              frame.get("sha256"))
            if data is None:
                continue
            if frame.get("bytes") != len(data):
                problems.append(f"{role} frame {index} byte-count mismatch")
            observed = analyze_frame(data)
            claimed_analysis = frame.get("analysis")
            if not isinstance(claimed_analysis, dict):
                problems.append(f"{role} frame {index} has no analysis")
                continue
            for key in ("format", "width", "height", "blank", "sampled_pixels",
                        "sample_step", "grid", "signature"):
                if observed.get(key) != claimed_analysis.get(key):
                    problems.append(f"{role} frame {index} {key} disagrees with bytes")
                    break
            # Blank/undecodable frames are not an integrity failure by
            # themselves: decide_verdict() refuses to count them as evidence.
            # If someone *claims* them as PASS, the re-derived verdict below
            # disagrees and the record is rejected as TAMPERED.

    # Re-derive the verdict independently; a forged PASS is always rejected.
    declared = detail.get("declared_input_sha256")
    min_sim = (detail.get("comparison") or {}).get("min_similarity")
    if not isinstance(min_sim, (int, float)):
        min_sim = DEFAULT_MIN_SIMILARITY
    recomputed, _reasons, recomputed_comparison = decide_verdict(
        sides.get("original"), sides.get("native"), declared or None,
        float(min_sim))
    if recomputed != record.get("status"):
        problems.append(
            f"verdict inconsistency: recorded={record.get('status')} "
            f"recomputed={recomputed}")
    if recomputed_comparison != detail.get("comparison"):
        problems.append("comparison fields disagree with independently recomputed values")
    if record.get("status") == VERDICT_PASS:
        if "original" not in sides or "native" not in sides:
            problems.append("PASS requires both original and native sides")
        if record.get("verification") != "INDEPENDENTLY VERIFIED":
            problems.append("PASS requires independent verification marker")
        if not isinstance(declared, str) or not declared:
            problems.append("PASS requires a declared input hash")
        if recomputed_comparison.get("similarity_met") is not True:
            problems.append("PASS requires frame similarity at or above threshold")
        if recomputed_comparison.get("input_hashes_match") is not True:
            problems.append("PASS requires matching input hashes on both sides")
    entry_original = entry.get("original_sha256")
    entry_native = entry.get("native_sha256")
    if entry_original != (sides.get("original") or {}).get("binary_sha256"):
        problems.append("entry original hash disagrees with evidence")
    if entry_native != (sides.get("native") or {}).get("binary_sha256"):
        problems.append("entry native hash disagrees with evidence")
    if entry.get("declared_input_sha256") != declared:
        problems.append("entry input hash disagrees with evidence")
    if entry.get("similarity") != recomputed_comparison.get("similarity"):
        problems.append("entry similarity disagrees with recomputed similarity")
    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _emit(out: Path | None, record: dict, message: str, code: int) -> int:
    print(message, file=sys.stderr)
    if out is not None:
        try:
            _write_json(out, record)
        except OSError as exc:
            print(f"error: cannot write record: {type(exc).__name__}", file=sys.stderr)
    return code


def _side_request(role: str, args: argparse.Namespace, out: Path,
                  input_bytes: bytes, artifacts: Path) -> SideRequest:
    native_side = role == "native"
    capture = args.native_capture if native_side else args.original_capture
    default_cwd = (args.native.parent if native_side else args.original.parent)
    return SideRequest(
        role=role,
        binary=args.native if native_side else args.original,
        cwd=(args.native_cwd or default_cwd).resolve(),
        args=list(args.native_arg if native_side else args.arg),
        capture_backend=capture, input_bytes=input_bytes,
        replay_input=bool(args.replay_input),
        frames=args.frames, settle_sec=args.settle_sec,
        frame_interval_sec=args.frame_interval_sec, run_seconds=args.run_seconds,
        timeout=args.timeout,
        highball=args.highball or "", bottle=args.bottle, engine=args.engine,
        renderer=args.renderer, cua_driver=str(args.cua_driver),
        screencapture=str(args.screencapture),
        window_title=(args.native_window_title if native_side
                      else args.original_window_title) or "",
        artifacts_dir=artifacts,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", help="game identifier (e.g. igi, tmn-eswc)")
    ap.add_argument("--issue", type=int, action="append", default=[],
                    help="issue number this attempt relates to (repeatable)")
    ap.add_argument("--original", type=Path, help="original Windows executable")
    ap.add_argument("--native", type=Path, help="native recompiled binary (optional)")
    ap.add_argument("--out", type=Path, help="verdict record JSON to write")
    ap.add_argument("--scenario", help="scenario name (default: game id)")
    ap.add_argument("--input-file", type=Path,
                    help="deterministic replay/seed bytes delivered to both sides")
    ap.add_argument("--replay-input", action="store_true",
                    help="actually deliver --input-file bytes (CUA text/stdin)")
    ap.add_argument("--arg", action="append", default=[], help="original guest argument")
    ap.add_argument("--native-arg", action="append", default=[],
                    help="native argument")
    ap.add_argument("--native-cwd", type=Path, help="native working directory")
    ap.add_argument("--highball", help="path to Highball CLI (env/PATH if omitted)")
    ap.add_argument("--bottle", default="Games", help="Highball bottle")
    ap.add_argument("--engine", help="Highball engine id")
    ap.add_argument("--renderer", help="Highball renderer")
    ap.add_argument("--original-capture", choices=CAPTURE_BACKENDS,
                    default=CAPTURE_CUA, help="original frame capture backend")
    ap.add_argument("--native-capture", choices=CAPTURE_BACKENDS,
                    default=CAPTURE_RECOMP, help="native frame capture backend")
    ap.add_argument("--cua-driver", type=Path,
                    default=Path(os.environ.get("CUA_DRIVER", DEFAULT_CUA_DRIVER)))
    ap.add_argument("--screencapture", type=Path, default=Path(DEFAULT_SCREENCAPTURE))
    ap.add_argument("--original-window-title", help="required CUA window title substring")
    ap.add_argument("--native-window-title", help="required CUA window title substring")
    ap.add_argument("--frames", type=int, default=2, help="frames per side")
    ap.add_argument("--settle-sec", type=float, default=3.0)
    ap.add_argument("--frame-interval-sec", type=float, default=1.0)
    ap.add_argument("--run-seconds", type=float, default=10.0)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--min-similarity", type=float, default=DEFAULT_MIN_SIMILARITY)
    ap.add_argument("--harness", default="builtin",
                    help="'builtin' or 'module:attribute' injectable harness")
    ap.add_argument("--verify", type=Path, metavar="RECORD",
                    help="independently verify an existing record and exit")
    args = ap.parse_args(argv)

    if args.verify is not None:
        problems = verify_record(args.verify)
        if problems:
            for problem in problems:
                print(f"TAMPERED: {problem}", file=sys.stderr)
            return EXIT_TAMPERED
        verdict = json.loads(args.verify.read_text(encoding="utf-8")).get("status")
        print(f"OK: {args.verify} verdict={verdict}")
        return {VERDICT_PASS: EXIT_PASS, VERDICT_PARTIAL: EXIT_PARTIAL,
                VERDICT_BLOCKED: EXIT_BLOCKED}[verdict]

    if not args.game_id or args.original is None or args.out is None:
        ap.error("--game-id, --original and --out are required unless --verify is given")
    if args.frames < 1 or args.timeout < 1:
        ap.error("--frames and --timeout must be positive integers")
    if not 0.0 <= args.min_similarity <= 1.0:
        ap.error("--min-similarity must be between 0 and 1")

    out = args.out
    scenario = args.scenario or args.game_id
    input_bytes = args.input_file.read_bytes() if args.input_file else b""
    declared_sha = sha(input_bytes) if args.input_file else ""
    artifacts = out.parent / f"{out.stem}-artifacts"

    # Fail closed, in priority order, before any process is started.
    if not args.original.is_file():
        return _emit(out, _blocked_record(f"original not found: {args.original}"),
                     f"BLOCKED: original not found: {args.original}; NOT TESTED",
                     EXIT_BLOCKED)
    if args.native is not None and not args.native.is_file():
        return _emit(out, _blocked_record(f"native binary not found: {args.native}"),
                     f"BLOCKED: native binary not found: {args.native}; NOT TESTED",
                     EXIT_BLOCKED)
    if args.input_file is not None and not args.input_file.is_file():
        return _emit(out, _blocked_record(f"input file not found: {args.input_file}"),
                     "BLOCKED: input file not found; NOT TESTED", EXIT_BLOCKED)

    highball = args.highball or shutil.which("highball")
    original_req = _side_request("original", args, out, input_bytes, artifacts)
    original_req.highball = highball or ""
    native_req = _side_request("native", args, out, input_bytes, artifacts) \
        if args.native is not None else None
    bundle = BundleRequest(game_id=args.game_id, scenario=scenario,
                           input_sha256=declared_sha, original=original_req,
                           native=native_req)

    try:
        harness = load_harness(args.harness)
    except EndpointError as exc:
        return _emit(out, _blocked_record(f"harness error: {exc}"),
                     f"BLOCKED: harness error: {exc}; NOT TESTED", EXIT_BLOCKED)
    try:
        healthy, probe_reason = harness.probe(bundle)
    except Exception as exc:
        healthy, probe_reason = False, "harness probe error: " + type(exc).__name__
    if not healthy:
        reason = f"endpoint unhealthy: {probe_reason}"
        return _emit(out, _blocked_record(reason), f"BLOCKED: {reason}; NOT TESTED",
                     EXIT_BLOCKED)

    try:
        original_outcome = harness.run(original_req)
        if not isinstance(original_outcome, RunOutcome):
            raise CaptureError("harness returned an invalid original outcome")
        native_outcome = harness.run(native_req) if native_req is not None else None
        if native_req is not None and not isinstance(native_outcome, RunOutcome):
            raise CaptureError("harness returned an invalid native outcome")
        record = build_record(
            bundle=bundle, harness=harness, original_outcome=original_outcome,
            native_outcome=native_outcome, min_similarity=args.min_similarity,
            out=out, issues=args.issue)
    except (HarnessError, OSError, ValueError) as exc:
        message = str(exc) or type(exc).__name__
        return _emit(out, _blocked_record(f"capture failed: {message}"),
                     f"BLOCKED: capture failed: {message}; NOT TESTED", EXIT_BLOCKED)

    problems = verify_record(out)
    if problems:
        return _emit(out, _blocked_record("post-write integrity check failed: "
                                         + "; ".join(problems)),
                     "TAMPERED: post-write integrity check failed: "
                     + "; ".join(problems), EXIT_TAMPERED)

    verdict = record["status"]
    print(f"{verdict}: {args.game_id} scenario={scenario} "
          f"issues={args.issue} probe={probe_reason}")
    for reason in record["reasons"]:
        print(f"  reason: {reason}")
    if record["comparison"]:
        print(f"  input_match={record['comparison']['input_hashes_match']} "
              f"similarity={record['comparison']['similarity']} "
              f"threshold={args.min_similarity}")
    print(f"record: {out}")
    return {VERDICT_PASS: EXIT_PASS, VERDICT_PARTIAL: EXIT_PARTIAL,
            VERDICT_BLOCKED: EXIT_BLOCKED}[verdict]


if __name__ == "__main__":
    sys.exit(main())
