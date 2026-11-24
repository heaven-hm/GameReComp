#!/usr/bin/env python3
"""Deterministic input adapter for an injected native-binary runner.

A CLI-supplied JSON scenario holds timestamped key/mouse events. The adapter
drives an *injectable* native-binary runner one event at a time and records,
for every step, SHA-256 hashes of the runner's stdout and stderr, its exit
code, and the captured frame's hash plus distinct-colour count. It then
replays the whole scenario with a fresh runner and requires the two canonical
records to be byte-identical.

The harness is deliberately narrow and fail-closed:

* A missing scenario, or no injected runner, is BLOCKED/FAIL -- never PASS.
  A build, an emulator result or a stub runner cannot be reported as a
  live-game success by this tool.
* An event the runner does not explicitly report as consumed, or that is
  missing from ``events_delivered()``, fails the run. Nothing is assumed to
  have been delivered; there is no built-in "fake consumption".
* A blank or undecodable frame is a failure, not render evidence.
* Divergence between the two replay records is a failure.
* Joystick, force-feedback (FFB) and XInput are explicitly out of scope and
  are rejected by validation; silence is never support for those devices.
* No wall-clock sleeps: event ``t`` values only order the scenario. No binary,
  game or evidence path is hard-coded; all paths come from the CLI.

Runner protocol (see :class:`NativeRunner`): a factory returning a *fresh*
runner per replay, selected with ``--runner module.path:factory_name``.

Usage:
    python3 -m tools.real_input_adapter --scenario <scenario.json> \
        --out <evidence-dir> --runner <module.path:factory_name>
    python3 -m tools.real_input_adapter --out <evidence-dir> --verify
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
REPLAY_COUNT = 2

ALLOWED_EVENT_TYPES = frozenset({'key', 'mouse'})
# Explicitly rejected so nobody reads silence as support for these devices.
FORBIDDEN_EVENT_TYPES = frozenset({
    'joystick', 'gamepad', 'controller', 'ffb', 'force_feedback',
    'force-feedback', 'xinput', 'xinput_gamepad', 'rumble', 'haptic',
})
ALLOWED_KEY_ACTIONS = frozenset({'down', 'up'})
ALLOWED_MOUSE_ACTIONS = frozenset({'move', 'down', 'up', 'wheel'})


class ScenarioError(ValueError):
    """The scenario JSON is missing, malformed or out of scope."""


class EventNotConsumed(RuntimeError):
    """The runner did not deliver the whole scenario to the native binary."""


class FrameError(RuntimeError):
    """A captured frame cannot be accepted as evidence (blank/undecodable)."""


class DeterminismError(RuntimeError):
    """Two independent replays produced different records."""


class RecordError(RuntimeError):
    """Guest output or a record is not deterministic JSON evidence."""


class RunnerError(RuntimeError):
    """The injected runner violated the NativeRunner protocol."""


class TamperError(RecordError):
    """Recorded evidence no longer matches the hashes it claims."""


@dataclass(frozen=True)
class StepResult:
    """One step's observable output from the injected native runner.

    ``consumed`` must be ``True`` only when the runner actually delivered the
    event to the native binary. ``stdout``/``stderr`` are raw bytes (hashed by
    this adapter), ``exit_code`` is an ``int`` or ``None``, and ``screenshot``
    is one captured frame as 24-bpp BMP or 8-bit non-interlaced PNG bytes.
    """

    consumed: bool
    stdout: bytes
    stderr: bytes
    exit_code: int | None
    screenshot: bytes


class NativeRunner(Protocol):
    """Adapter around an injected native-binary runner.

    A factory returning a *fresh* instance is invoked once per replay, so
    state from an earlier replay cannot leak into the next one.

    ``start()`` prepares the runner before the first event. ``step(event)``
    receives one normalised event dict and returns a :class:`StepResult`.
    ``events_delivered()`` reports the events actually delivered to the native
    binary; it is compared against the scenario, so a runner that merely
    claims consumption cannot pass. ``exit_code()`` returns the final process
    exit code. ``close()`` releases any host resource held by the adapter.
    """

    def start(self) -> None: ...

    def step(self, event: dict) -> StepResult: ...

    def events_delivered(self) -> list[dict]: ...

    def exit_code(self) -> int | None: ...

    def close(self) -> None: ...


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _require_int(event: dict, field: str, index: int) -> None:
    if not _is_int(event.get(field)):
        raise ScenarioError(f'event {index}: {field!r} must be an integer')


def load_scenario(path: Path) -> dict:
    """Read and validate a scenario; return a normalised, ordered dict.

    Events are ordered by ``(t, input order)`` so the replay is deterministic
    regardless of how the file lists same-timestamp events. Unknown extra
    event fields are preserved for the runner, but device types outside
    keyboard/mouse are rejected outright.
    """
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScenarioError(f'cannot read scenario {path}: {exc}') from exc
    if not isinstance(raw, dict):
        raise ScenarioError('scenario root must be a JSON object')
    if 'schema' in raw and raw['schema'] != 1:
        raise ScenarioError(f'unsupported scenario schema {raw["schema"]!r}')
    name = raw.get('name', '')
    if not isinstance(name, str):
        raise ScenarioError('scenario "name" must be a string')
    events = raw.get('events')
    if not isinstance(events, list) or not events:
        raise ScenarioError('scenario "events" must be a non-empty list')

    normalised = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ScenarioError(f'event {index}: must be a JSON object')
        kind = event.get('type')
        if kind in FORBIDDEN_EVENT_TYPES:
            raise ScenarioError(
                f'event {index}: {kind!r} events are out of scope; this adapter '
                'supports keyboard and mouse only (no joystick/FFB/XInput claims)')
        if kind not in ALLOWED_EVENT_TYPES:
            raise ScenarioError(f'event {index}: unsupported type {kind!r}')
        t = event.get('t')
        if not _is_number(t) or t < 0:
            raise ScenarioError(
                f'event {index}: "t" must be a finite non-negative number')
        action = event.get('action')
        if kind == 'key':
            if action not in ALLOWED_KEY_ACTIONS:
                raise ScenarioError(f'event {index}: key action must be down/up')
            key = event.get('key')
            if not ((isinstance(key, str) and key) or (_is_int(key) and key >= 0)):
                raise ScenarioError(
                    f'event {index}: "key" must be a name or non-negative integer')
        else:
            if action not in ALLOWED_MOUSE_ACTIONS:
                raise ScenarioError(
                    f'event {index}: mouse action must be move/down/up/wheel')
            if action == 'move':
                _require_int(event, 'x', index)
                _require_int(event, 'y', index)
            elif action in ('down', 'up'):
                button = event.get('button')
                if not (isinstance(button, str) and button):
                    raise ScenarioError(f'event {index}: "button" must be a name')
            else:  # wheel
                if 'dx' not in event and 'dy' not in event:
                    raise ScenarioError(f'event {index}: wheel needs "dx" and/or "dy"')
                if 'dx' in event:
                    _require_int(event, 'dx', index)
                if 'dy' in event:
                    _require_int(event, 'dy', index)
        normalised.append((t, index, dict(event)))

    normalised.sort(key=lambda item: (item[0], item[1]))
    return {'schema': 1, 'name': name,
            'events': [event for _, _, event in normalised]}


def canonical_bytes(value: object) -> bytes:
    """Stable byte encoding used both for records and for the identity check."""
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise RecordError(f'value is not deterministic JSON: {exc}') from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _bmp_info(data: bytes) -> dict:
    if data[:2] != b'BM' or len(data) < 54:
        return {'error': 'not a BMP'}
    off = struct.unpack_from('<I', data, 10)[0]
    w, h = struct.unpack_from('<ii', data, 18)
    bpp = struct.unpack_from('<H', data, 28)[0]
    comp = struct.unpack_from('<I', data, 30)[0]
    if bpp != 24 or comp != 0 or w <= 0 or h <= 0:
        return {'error': 'unsupported BMP variant (need 24bpp, uncompressed)'}
    stride = ((w * 3 + 3) // 4) * 4
    if off + stride * h > len(data):
        return {'error': 'truncated BMP pixel data'}
    colours: set[bytes] = set()
    for y in range(h):
        row = off + (h - 1 - y) * stride
        for x in range(w):
            p = row + x * 3
            colours.add(data[p:p + 3])
            if len(colours) > 100000:
                break
        if len(colours) > 100000:
            break
    return {'width': w, 'height': h, 'distinct_colors': len(colours),
            'blank': len(colours) <= 1}


def _png_info(data: bytes) -> dict:
    """Decode an 8-bit non-interlaced PNG far enough to count distinct pixels."""
    if data[:8] != PNG_SIGNATURE:
        return {'error': 'not a PNG'}
    pos, chunks, idat = 8, {}, bytearray()
    while pos + 12 <= len(data):
        length = struct.unpack_from('>I', data, pos)[0]
        ctype = data[pos + 4:pos + 8]
        if pos + 12 + length > len(data):
            return {'error': 'truncated PNG chunk'}
        chunks[ctype] = data[pos + 8:pos + 8 + length]
        if ctype == b'IDAT':
            idat.extend(chunks[ctype])
        if ctype == b'IEND':
            break
        pos += 12 + length
    if b'IHDR' not in chunks or b'IEND' not in chunks:
        return {'error': 'incomplete PNG'}
    width, height, depth, color, comp, filt, interlace = struct.unpack(
        '>IIBBBBB', chunks[b'IHDR'])
    if depth != 8 or interlace != 0 or comp != 0 or filt != 0:
        return {'error': 'unsupported PNG variant (need 8-bit, non-interlaced)'}
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if channels is None:
        return {'error': 'unsupported PNG colour type'}
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        return {'error': f'PNG inflate failed: {exc}'}
    stride = width * channels
    if len(raw) != height * (stride + 1):
        return {'error': 'unexpected PNG scanline size'}

    previous = bytearray(stride)
    colours: set[bytes] = set()
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
                return {'error': f'unknown PNG filter {filter_type}'}
            row[i] = (row[i] + predictor) & 0xFF
        for x in range(width):
            colours.add(bytes(row[x * channels:(x + 1) * channels]))
            if len(colours) > 100000:
                break
        previous = row
    return {'width': width, 'height': height,
            'distinct_colors': len(colours), 'blank': len(colours) <= 1}


def frame_info(data: bytes) -> dict:
    """Hash and decode one captured frame; never infer content from a path."""
    info = {'sha256': _sha256(data), 'bytes': len(data)}
    if data[:2] == b'BM':
        info.update(_bmp_info(data))
        info['format'] = 'bmp'
    elif data[:8] == PNG_SIGNATURE:
        info.update(_png_info(data))
        info['format'] = 'png'
    else:
        info.update(error='unsupported screenshot format (need 24bpp BMP or 8-bit PNG)',
                    blank_check='unproven')
    return info


def _as_bytes(value: object, field: str, index: int) -> bytes:
    if not isinstance(value, (bytes, bytearray)):
        raise RunnerError(f'event {index}: {field} must be bytes, got '
                          f'{type(value).__name__}')
    return bytes(value)


def _safe_path(base: Path, relative: str) -> Path:
    """Resolve a recorded relative path, refusing to escape the replay dir."""
    target = (base / relative).resolve()
    root = base.resolve()
    if target != root and root not in target.parents:
        raise TamperError(f'recorded path escapes its replay directory: {relative!r}')
    return target


def run_once(scenario: dict, runner_factory, out_dir: Path) -> dict:
    """Run the scenario once with a fresh runner; fail closed on any bad step."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / 'frames'
    frames_dir.mkdir(parents=True, exist_ok=True)
    runner = runner_factory()
    record = {'schema': 1, 'scenario_sha256': _sha256(canonical_bytes(scenario)),
              'steps': []}
    try:
        try:
            runner.start()
        except Exception as exc:
            raise RunnerError(f'runner.start() failed: {exc}') from exc

        for index, event in enumerate(scenario['events']):
            try:
                step = runner.step(event)
            except Exception as exc:
                raise EventNotConsumed(
                    f'event {index} raised in runner.step: {exc}') from exc
            consumed = getattr(step, 'consumed', None)
            if consumed is not True:
                raise EventNotConsumed(
                    f'event {index} was not consumed by the runner: {event!r}')
            stdout = _as_bytes(getattr(step, 'stdout', None), 'stdout', index)
            stderr = _as_bytes(getattr(step, 'stderr', None), 'stderr', index)
            exit_code = getattr(step, 'exit_code', None)
            if exit_code is not None and not _is_int(exit_code):
                raise RunnerError(f'event {index}: exit_code must be int or None')
            shot = _as_bytes(getattr(step, 'screenshot', None), 'screenshot', index)

            stdout_name = f'step-{index:04d}.stdout'
            stderr_name = f'step-{index:04d}.stderr'
            (out_dir / stdout_name).write_bytes(stdout)
            (out_dir / stderr_name).write_bytes(stderr)
            suffix = '.png' if shot[:8] == PNG_SIGNATURE else '.bmp'
            frame_name = f'frame-{index:04d}{suffix}'
            (frames_dir / frame_name).write_bytes(shot)
            info = frame_info(shot)
            if info.get('error'):
                raise FrameError(f'event {index}: frame {frame_name}: {info["error"]}')
            if info.get('blank'):
                raise FrameError(
                    f'event {index}: frame {frame_name} is blank '
                    f'({info.get("distinct_colors")} colour(s))')
            info['path'] = f'frames/{frame_name}'
            record['steps'].append({
                'index': index, 'event': event, 'consumed': True,
                'exit_code': exit_code,
                'stdout': {'sha256': _sha256(stdout), 'bytes': len(stdout),
                           'path': stdout_name},
                'stderr': {'sha256': _sha256(stderr), 'bytes': len(stderr),
                           'path': stderr_name},
                'screenshot': info,
            })

        try:
            delivered = runner.events_delivered()
        except Exception as exc:
            raise RunnerError(f'runner.events_delivered() failed: {exc}') from exc
        if (not isinstance(delivered, list)
                or canonical_bytes(delivered) != canonical_bytes(scenario['events'])):
            raise EventNotConsumed(
                'runner did not deliver the complete scenario to the native binary')

        try:
            final_exit = runner.exit_code()
        except Exception as exc:
            raise RunnerError(f'runner.exit_code() failed: {exc}') from exc
        if not _is_int(final_exit):
            raise RunnerError('runner.exit_code() must return an int')
        record['final_exit_code'] = final_exit
        record['status'] = 'PASS'
        canonical_bytes(record)  # reject NaN / non-JSON output before claiming PASS
        return record
    finally:
        try:
            runner.close()
        except Exception:
            pass  # a teardown failure never turns a run into a PASS


def run_replay(scenario: dict, runner_factory, out: Path,
               replays: int = REPLAY_COUNT) -> dict:
    """Run the scenario ``replays`` times and require byte-identical records."""
    if replays < REPLAY_COUNT:
        raise ValueError(f'at least {REPLAY_COUNT} replays are required')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'scenario.json').write_bytes(canonical_bytes(scenario))
    blobs: list[bytes] = []
    for number in range(replays):
        run_dir = out / f'replay-{number}'
        record = run_once(scenario, runner_factory, run_dir)
        blob = canonical_bytes(record)
        (run_dir / 'record.json').write_bytes(blob)
        blobs.append(blob)
    if any(blob != blobs[0] for blob in blobs[1:]):
        raise DeterminismError('replay records differ between independent runs')

    result = {
        'schema': 1, 'status': 'PASS', 'replays': replays,
        'scenario_sha256': _sha256(canonical_bytes(scenario)),
        'record_sha256s': [_sha256(blob) for blob in blobs],
        'record_paths': [f'replay-{i}/record.json' for i in range(replays)],
        'what_it_proves': (
            'two fresh-runner replays of this scenario produced byte-identical '
            'records with every event delivered, every captured frame non-blank, '
            'and per-step stdout/stderr/exit/frame hashes recorded'),
        'not_claimed': (
            'no live-game success; no gameplay, audio, joystick, force-feedback '
            'or XInput claim'),
    }
    (out / 'result.json').write_bytes(canonical_bytes(result))
    return result


def verify_evidence(out: Path) -> dict:
    """Recompute every recorded hash; detect post-hoc tampering or drift.

    This never runs the game. It checks that the stored scenario and replay
    records still match the result's hashes, that every stdout/stderr/frame
    file still matches its step hash, that frame colour counts still hold, and
    that all replay records remain byte-identical.
    """
    result_path = out / 'result.json'
    if not result_path.is_file():
        raise RecordError(f'missing evidence result: {result_path}')
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if result.get('status') != 'PASS':
        raise RecordError('evidence is not a PASS run; nothing to verify')

    scenario_bytes = (out / 'scenario.json').read_bytes()
    if _sha256(scenario_bytes) != result.get('scenario_sha256'):
        raise TamperError('scenario.json no longer matches the recorded hash')

    blobs: list[bytes] = []
    expected_hashes = result.get('record_sha256s', [])
    if len(expected_hashes) < REPLAY_COUNT:
        raise RecordError(f'expected at least {REPLAY_COUNT} replay records')
    for number, expected in enumerate(expected_hashes):
        run_dir = out / f'replay-{number}'
        raw = (run_dir / 'record.json').read_bytes()
        if _sha256(raw) != expected:
            raise TamperError(f'replay-{number}: record.json hash mismatch')
        record = json.loads(raw)
        if canonical_bytes(record) != raw:
            raise TamperError(f'replay-{number}: record.json is not canonical')
        blobs.append(raw)
        for step in record.get('steps', []):
            index = step['index']
            for stream in ('stdout', 'stderr'):
                meta = step[stream]
                data = _safe_path(run_dir, meta['path']).read_bytes()
                if _sha256(data) != meta['sha256']:
                    raise TamperError(
                        f'replay-{number} step {index}: {stream} hash mismatch')
            shot = step['screenshot']
            data = _safe_path(run_dir, shot['path']).read_bytes()
            if _sha256(data) != shot['sha256']:
                raise TamperError(
                    f'replay-{number} step {index}: frame hash mismatch')
            info = frame_info(data)
            if (info.get('error') or info.get('blank')
                    or info.get('distinct_colors') != shot['distinct_colors']):
                raise TamperError(
                    f'replay-{number} step {index}: frame content no longer matches')
    if any(blob != blobs[0] for blob in blobs[1:]):
        raise DeterminismError('replay records differ between independent runs')
    return {'schema': 1, 'status': 'PASS', 'verified_replays': len(blobs),
            'what_it_proves': 'stored evidence still matches every recorded hash '
                              'and both replays remain byte-identical'}


def load_runner_factory(spec: str):
    """Resolve a ``module.path:factory_name`` supplied on the command line."""
    if ':' not in spec:
        raise ValueError('--runner must be "module.path:factory_name"')
    module_name, _, attribute = spec.partition(':')
    if not module_name or not attribute:
        raise ValueError('--runner must be "module.path:factory_name"')
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise ValueError(f'--runner target {spec!r} is not callable')
    return factory


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--scenario', type=Path,
                    help='JSON file of timestamped key/mouse events')
    ap.add_argument('--out', required=True, type=Path,
                    help='evidence directory for replay records and frames')
    ap.add_argument('--runner',
                    help='module.path:factory_name for a fresh NativeRunner per replay')
    ap.add_argument('--replays', type=int, default=REPLAY_COUNT,
                    help=f'number of independent runs (default: {REPLAY_COUNT})')
    ap.add_argument('--verify', action='store_true',
                    help='recompute hashes for existing --out evidence instead of running')
    args = ap.parse_args(argv)

    if args.verify:
        try:
            result = verify_evidence(args.out)
            code = 0
        except Exception as exc:
            result = {'schema': 1, 'status': 'FAIL',
                      'reason': f'{type(exc).__name__}: {exc}'}
            code = 1
        print(json.dumps(result, indent=2, sort_keys=True))
        return code

    result: dict = {'schema': 1, 'status': 'FAIL'}
    try:
        if args.scenario is None or not args.scenario.is_file():
            result.update(status='BLOCKED',
                          reason='missing --scenario file; refusing to invent input events')
            code = 2
        else:
            scenario = load_scenario(args.scenario)
            if not args.runner:
                result.update(status='BLOCKED',
                              reason='no --runner adapter supplied; refusing to claim '
                                     'native input determinism')
                code = 2
            else:
                result = run_replay(scenario, load_runner_factory(args.runner),
                                    args.out, args.replays)
                code = 0
    except Exception as exc:
        result.update(status='FAIL', reason=f'{type(exc).__name__}: {exc}')
        code = 1

    try:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / 'result.json').write_bytes(canonical_bytes(result))
    except OSError as exc:
        print(f'warning: could not write result.json: {exc}', file=sys.stderr)
    print(json.dumps(result, indent=2, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
