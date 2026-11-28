#!/usr/bin/env python3
"""Deterministic scripted-input harness for the host's existing input pump.

A CLI-supplied JSON scenario holds timestamped key/mouse events. The harness
injects them through an *injectable* pump adapter, and after every event records
the guest-visible state plus a screenshot (path + SHA-256 + blank/colour check).
It then replays the whole scenario a second time with a fresh pump and requires
the two canonical JSON records to be byte-identical.

The harness is deliberately narrow and fail-closed:

* It never sleeps on wall-clock time. Event ``t`` values order the scenario;
  they are data given to the pump, not a real-time schedule.
* An event the pump does not report as consumed is a failure, not a warning.
* A blank or undecodable screenshot is a failure, not render evidence.
* No pump adapter supplied -> BLOCKED. A build, an emulator result or a fake
  pump can never be reported as a live-game success by this tool.
* Only keyboard and mouse events are accepted. Joystick, force-feedback (FFB)
  and XInput are explicitly out of scope and are rejected by validation.

Pump adapter contract (see :class:`InputPump`): a factory callable returning a
fresh pump per replay, selected with ``--pump module.path:factory_name``.

Usage:
    python3 -m tools.input_scenario --scenario <scenario.json> \
        --out <evidence-dir> --pump <module.path:factory_name>
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
from pathlib import Path
from typing import Protocol

from tools.frame_proof import bmp24


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
    """A pump refused an event, so the guest never saw the whole scenario."""


class FrameError(RuntimeError):
    """A captured frame cannot be accepted as evidence (blank or undecodable)."""


class DeterminismError(RuntimeError):
    """Two independent replays produced different records."""


class RecordError(RuntimeError):
    """Guest-visible state or a record is not deterministic JSON data."""


class InputPump(Protocol):
    """Adapter for the host's existing input pump, injected by the caller.

    A factory returning a *fresh* instance is invoked once per replay, so any
    state accumulated by a previous replay cannot leak into the next one.

    ``inject(event)`` receives one normalised event dict (including its ``t``,
    ``type``, ``action`` and device fields). It must return ``True`` only when
    the pump actually delivered that event into guest state; anything else
    fails the run closed.

    ``state()`` returns a JSON-serialisable snapshot of guest-visible state
    (keys held, mouse position/buttons, focus window, ...). It must be a pure
    read: two pumps fed the same scenario must report identical snapshots.

    ``screenshot()`` returns the raw bytes of one captured frame, in a format
    this harness can decode: 24-bpp uncompressed BMP (the runtime's native
    capture) or 8-bit non-interlaced PNG (colour types 0, 2, 4 or 6).

    ``close()`` releases any host resource held by the adapter.
    """

    def inject(self, event: dict) -> bool: ...

    def state(self) -> dict: ...

    def screenshot(self) -> bytes: ...

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
    regardless of how the file happens to list same-timestamp events. Unknown
    extra event fields are preserved for the pump adapter, but device types
    outside keyboard/mouse are rejected outright.
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
                f'event {index}: {kind!r} events are out of scope; this harness '
                'supports keyboard and mouse only (no joystick/FFB/XInput claims)')
        if kind not in ALLOWED_EVENT_TYPES:
            raise ScenarioError(f'event {index}: unsupported type {kind!r}')
        t = event.get('t')
        if not _is_number(t) or t < 0:
            raise ScenarioError(f'event {index}: "t" must be a finite non-negative number')
        action = event.get('action')
        if kind == 'key':
            if action not in ALLOWED_KEY_ACTIONS:
                raise ScenarioError(f'event {index}: key action must be down/up')
            key = event.get('key')
            if not ((isinstance(key, str) and key) or (_is_int(key) and key >= 0)):
                raise ScenarioError(f'event {index}: "key" must be a name or non-negative integer')
        else:
            if action not in ALLOWED_MOUSE_ACTIONS:
                raise ScenarioError(f'event {index}: mouse action must be move/down/up/wheel')
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


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def png_info(data: bytes) -> dict:
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


def frame_info(path: Path) -> dict:
    """Hash and decode one captured frame; never infer a pixel from a path."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if data[:2] == b'BM':
        info = bmp24(path)
        info['format'] = 'bmp'
        info['sha256'] = digest
        return info
    if data[:8] == PNG_SIGNATURE:
        info = png_info(data)
        info.update(format='png', sha256=digest, bytes=len(data))
        return info
    return {'error': 'unsupported screenshot format (need 24bpp BMP or 8-bit PNG)',
            'blank_check': 'unproven', 'sha256': digest, 'bytes': len(data)}


def run_once(scenario: dict, pump_factory, out_dir: Path) -> dict:
    """Run the scenario once with a fresh pump; fail closed on any bad step."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / 'frames'
    frames_dir.mkdir(parents=True, exist_ok=True)
    pump = pump_factory()
    record = {'schema': 1, 'scenario': scenario, 'steps': []}
    try:
        try:
            record['initial_state'] = pump.state()
        except Exception as exc:
            raise RecordError(f'pump.state() before first event failed: {exc}') from exc

        for index, event in enumerate(scenario['events']):
            try:
                consumed = pump.inject(event)
            except Exception as exc:
                raise EventNotConsumed(
                    f'event {index} raised in pump.inject: {exc}') from exc
            if consumed is not True:
                raise EventNotConsumed(
                    f'event {index} was not consumed by the pump: {event!r}')
            try:
                state = pump.state()
            except Exception as exc:
                raise RecordError(f'event {index}: pump.state() failed: {exc}') from exc

            shot = pump.screenshot()
            if not isinstance(shot, (bytes, bytearray)):
                raise FrameError(f'event {index}: pump.screenshot() did not return bytes')
            suffix = '.png' if bytes(shot[:8]) == PNG_SIGNATURE else '.bmp'
            name = f'frame-{index:04d}{suffix}'
            (frames_dir / name).write_bytes(shot)
            info = frame_info(frames_dir / name)
            if info.get('error'):
                raise FrameError(f'event {index}: frame {name}: {info["error"]}')
            if info.get('blank'):
                raise FrameError(f'event {index}: frame {name} is blank '
                                 f'({info.get("distinct_colors")} colour(s))')
            info['path'] = f'frames/{name}'
            record['steps'].append({'index': index, 'event': event,
                                    'consumed': True, 'state': state,
                                    'screenshot': info})
        record['status'] = 'PASS'
        canonical_bytes(record)  # reject NaN / non-JSON state before claiming PASS
        return record
    finally:
        try:
            pump.close()
        except Exception:
            pass  # a teardown failure never turns a run into a PASS


def run_replay(scenario: dict, pump_factory, out: Path,
               replays: int = REPLAY_COUNT) -> dict:
    """Run the scenario ``replays`` times and require byte-identical records."""
    if replays < REPLAY_COUNT:
        raise ValueError(f'at least {REPLAY_COUNT} replays are required')
    out.mkdir(parents=True, exist_ok=True)
    blobs: list[bytes] = []
    for number in range(replays):
        run_dir = out / f'replay-{number}'
        record = run_once(scenario, pump_factory, run_dir)
        blob = canonical_bytes(record)
        (run_dir / 'record.json').write_bytes(blob)
        blobs.append(blob)
    if any(blob != blobs[0] for blob in blobs[1:]):
        raise DeterminismError('replay records differ between independent runs')

    result = {
        'schema': 1,
        'status': 'PASS',
        'replays': replays,
        'scenario_sha256': hashlib.sha256(canonical_bytes(scenario)).hexdigest(),
        'record_sha256s': [hashlib.sha256(blob).hexdigest() for blob in blobs],
        'record_paths': [f'replay-{i}/record.json' for i in range(replays)],
        'what_it_proves': ('two fresh-pump replays of this scenario produced '
                           'byte-identical records with every event consumed '
                           'and every captured frame non-blank'),
        'not_claimed': ('no live-game success; no gameplay, audio, joystick, '
                        'force-feedback or XInput claim'),
    }
    (out / 'result.json').write_bytes(canonical_bytes(result))
    return result


def load_pump_factory(spec: str):
    """Resolve a ``module.path:factory_name`` supplied on the command line."""
    if ':' not in spec:
        raise ValueError('--pump must be "module.path:factory_name"')
    module_name, _, attribute = spec.partition(':')
    if not module_name or not attribute:
        raise ValueError('--pump must be "module.path:factory_name"')
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise ValueError(f'--pump target {spec!r} is not callable')
    return factory


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--scenario', required=True, type=Path,
                    help='JSON file of timestamped key/mouse events')
    ap.add_argument('--out', required=True, type=Path,
                    help='evidence directory for replay records and frames')
    ap.add_argument('--pump', help='module.path:factory_name for a fresh InputPump per replay')
    ap.add_argument('--replays', type=int, default=REPLAY_COUNT,
                    help=f'number of independent runs (default: {REPLAY_COUNT})')
    args = ap.parse_args(argv)

    result: dict = {'schema': 1, 'status': 'FAIL'}
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        scenario = load_scenario(args.scenario)
        if not args.pump:
            result.update(status='BLOCKED',
                          reason='no --pump adapter supplied; refusing to claim input determinism')
            code = 2
        else:
            result = run_replay(scenario, load_pump_factory(args.pump), args.out,
                                args.replays)
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
