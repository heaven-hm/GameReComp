#!/usr/bin/env python3
"""Fail-closed evidence for a REAL native input-pump replay.

This tool never accepts an injected pump, a fake runner or an in-memory
adapter. It has two modes:

* ``capture`` runs a CLI-supplied native executable, feeds a CLI-supplied
  key-event scenario to its stdin, and records the frames the real process
  prints after each consumed event. One replay is one fresh process.
* ``verify`` reads only CLI-supplied records. It emits PASS only when the
  provenance declares a real native process, the supplied binary still hashes
  to the recorded native executable, every replay record is byte-identical to
  its peers, every scenario event was consumed, and every frame's hash and
  non-blank check still hold when recomputed from the stored bytes.

A synthetic/fake adapter, a script masquerading as a binary, missing
provenance or a missing binary yields NOT TESTED (exit 2), never PASS.
A tampered or internally inconsistent record yields FAIL (exit 1).

Scope: console input pumps whose consumed events are observable as a prompt
in the process output. There is no Windows-original claim, no gameplay or
audio claim, and no assertion that a hand-authored record cannot carry a
forged provenance header -- the CLI records and re-checks what a real process
actually emitted, and says exactly that.

Usage:
    python3 -m tools.real_input_pump_proof capture \
        --binary PATH --scenario FILE --out DIR --prompt TEXT
    python3 -m tools.real_input_pump_proof verify \
        --manifest FILE --binary PATH --out DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

from tools.frame_proof import bmp24
from tools.real_input_adapter import ScenarioError, canonical_bytes, load_scenario

SCHEMA = 1
REAL_ADAPTER = 'real_native_process'
CAPTURE_METHOD = 'stdin_script'
MIN_REPLAYS = 2

# A record naming any of these is an adapter, not a native game process.
FORBIDDEN_ADAPTERS = frozenset({
    'fake', 'synthetic', 'stub', 'mock', 'in_memory', 'in-memory',
    'simulated', 'fixture', 'test', 'pump', 'runner', 'emulator',
    'unicorn', 'wine', 'replay_only',
})

NOT_CLAIMED = (
    'no Windows-original, IGI/A7 or gameplay-equivalence claim; no audio, '
    'joystick, force-feedback or XInput claim; a hand-authored record could '
    'carry a forged provenance header, so this verdict covers the supplied '
    'records and the recomputed hashes only'
)


class ProofError(RuntimeError):
    """Base class for a fail-closed verdict."""


class NotTested(ProofError):
    """No real native process is in evidence; the tool refuses to judge."""


class TamperError(ProofError):
    """Stored evidence no longer matches its own hashes or declarations."""


class DeterminismError(ProofError):
    """Replays of the same scenario produced different records."""


class EventNotConsumed(ProofError):
    """The real process did not consume the whole scenario."""


class FrameError(ProofError):
    """A stored frame is blank, undecodable or no longer matches its hash."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path, what: str) -> dict:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TamperError(f'cannot read {what} {path}: {exc}') from exc
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TamperError(f'{what} is not valid JSON: {exc}') from exc
    if not isinstance(value, dict):
        raise TamperError(f'{what} root must be a JSON object')
    return value


def _safe_path(base: Path, relative: str) -> Path:
    """Resolve a recorded relative path, refusing to escape the replay dir."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise TamperError(f'invalid recorded path: {relative!r}')
    target = (base / relative).resolve()
    root = base.resolve()
    if target != root and root not in target.parents:
        raise TamperError(f'recorded path escapes its evidence directory: {relative!r}')
    return target


def _native_magic(data: bytes) -> bool:
    """Only a native executable may stand in for a real game process."""
    if data[:4] == b'\x7fELF' or data[:2] == b'MZ':
        return True
    return data[:4] in (b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe',
                        b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
                        b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca')


def _provenance(binary: Path, prompt: str) -> dict:
    data = binary.read_bytes()
    return {
        'adapter_kind': REAL_ADAPTER,
        'synthetic': False,
        'capture_method': CAPTURE_METHOD,
        'prompt': prompt,
        'platform': platform.system().lower(),
        'machine': platform.machine().lower(),
        'binary_name': binary.name,
        'binary_sha256': _sha(data),
        'binary_size': len(data),
    }


def _check_real_provenance(value: object) -> dict:
    """Refuse anything that is not an explicit real native process record."""
    if not isinstance(value, dict):
        raise NotTested('missing provenance; refusing to claim a real native run')
    kind = value.get('adapter_kind')
    if not isinstance(kind, str):
        raise NotTested('provenance has no adapter_kind; adapter is unknown')
    normalised = kind.strip().lower().replace('-', '_')
    if normalised != REAL_ADAPTER:
        raise NotTested(
            f'adapter_kind {kind!r} is not a real native process; '
            'synthetic/fake adapters are NOT TESTED')
    if value.get('synthetic') is True or value.get('fake') is True:
        raise NotTested('record declares a synthetic/fake adapter; NOT TESTED')
    if value.get('capture_method') != CAPTURE_METHOD:
        raise NotTested(
            f'capture_method {value.get("capture_method")!r} is not {CAPTURE_METHOD}; '
            'only a real process capture is in scope')
    for key in ('platform', 'machine', 'binary_name'):
        if not isinstance(value.get(key), str) or not value[key]:
            raise NotTested(f'provenance.{key} is missing; NOT TESTED')
    digest = value.get('binary_sha256')
    if not isinstance(digest, str) or len(digest) != 64:
        raise NotTested('provenance.binary_sha256 is missing; NOT TESTED')
    if not _is_int(value.get('binary_size')) or value['binary_size'] <= 0:
        raise NotTested('provenance.binary_size is missing; NOT TESTED')
    return value


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def text_frame_info(data: bytes) -> dict:
    """Hash one captured console frame and check it is not a blank buffer."""
    text = data.decode('utf-8', errors='replace')
    symbols = {ch for ch in text if not ch.isspace()}
    return {
        'kind': 'text', 'sha256': _sha(data), 'bytes': len(data),
        'blank': len(symbols) < 2, 'distinct_symbols': len(symbols),
    }


def _check_frame(run_dir: Path, meta: object, step_index: int) -> None:
    if not isinstance(meta, dict):
        raise FrameError(f'step {step_index}: frame metadata is not an object')
    path = _safe_path(run_dir, meta.get('path'))
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise FrameError(f'step {step_index}: cannot read frame: {exc}') from exc
    if _sha(data) != meta.get('sha256'):
        raise TamperError(f'step {step_index}: frame hash mismatch')
    if meta.get('bytes') != len(data):
        raise TamperError(f'step {step_index}: frame byte count mismatch')
    kind = meta.get('kind')
    if kind == 'text':
        info = text_frame_info(data)
        field = 'distinct_symbols'
    elif kind == 'bmp':
        info = bmp24(path)
        info['kind'] = 'bmp'
        field = 'distinct_colors'
    else:
        raise FrameError(f'step {step_index}: unsupported frame kind {kind!r}')
    if info.get('error'):
        raise FrameError(f'step {step_index}: frame decode failed: {info["error"]}')
    if info.get('blank'):
        raise FrameError(f'step {step_index}: frame is blank')
    if meta.get('blank') is not False or meta.get(field) != info.get(field):
        raise TamperError(f'step {step_index}: frame content no longer matches its record')


def _check_record(record: dict, manifest_prov: dict, scenario: dict,
                  scenario_digest: str, replay: int) -> None:
    if record.get('schema') != SCHEMA:
        raise TamperError(f'replay-{replay}: unsupported record schema')
    if _check_real_provenance(record.get('provenance')) != manifest_prov:
        raise TamperError(f'replay-{replay}: provenance differs from the manifest')
    if record.get('scenario_sha256') != scenario_digest:
        raise TamperError(f'replay-{replay}: scenario hash mismatch')
    if record.get('status') != 'PASS':
        raise TamperError(f'replay-{replay}: record is not a PASS capture')
    if canonical_bytes(record.get('events')) != canonical_bytes(scenario['events']):
        raise TamperError(f'replay-{replay}: event stream differs from the scenario')
    if record.get('unconsumed_events') != 0:
        raise EventNotConsumed(f'replay-{replay}: unconsumed events were recorded')
    if record.get('total_consumed_events') != len(scenario['events']):
        raise EventNotConsumed(f'replay-{replay}: consumed-event count does not match the scenario')
    if canonical_bytes(record.get('events_delivered')) != canonical_bytes(scenario['events']):
        raise EventNotConsumed(f'replay-{replay}: delivered events differ from the scenario')
    if not _is_int(record.get('exit_code')):
        raise TamperError(f'replay-{replay}: exit_code must be an int')
    steps = record.get('steps')
    if not isinstance(steps, list) or len(steps) != len(scenario['events']):
        raise TamperError(f'replay-{replay}: step count does not match the scenario')


def _check_steps(run_dir: Path, record: dict, scenario: dict, replay: int) -> None:
    steps = record['steps']
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise TamperError(f'replay-{replay} step {index}: step is not an object')
        if step.get('index') != index:
            raise TamperError(f'replay-{replay} step {index}: wrong index')
        if canonical_bytes(step.get('event')) != canonical_bytes(scenario['events'][index]):
            raise TamperError(f'replay-{replay} step {index}: event differs from the scenario')
        if step.get('consumed') is not True:
            raise EventNotConsumed(f'replay-{replay} step {index}: event was not consumed')
        if step.get('consumed_event_count') != index + 1:
            raise EventNotConsumed(
                f'replay-{replay} step {index}: consumed-event count is not cumulative')
        _check_frame(run_dir, step.get('frame'), index)


def verify_evidence(manifest_path: Path, binary: Path) -> dict:
    """Recompute every hash and every non-blank check; never runs a game."""
    manifest = _read_json(manifest_path, 'manifest')
    if manifest.get('schema') != SCHEMA:
        raise TamperError('unsupported manifest schema')
    prov = _check_real_provenance(manifest.get('provenance'))

    if not binary.is_file():
        raise NotTested(f'no real native binary at {binary}; refusing to claim a native run')
    data = binary.read_bytes()
    if not _native_magic(data):
        raise NotTested(f'{binary.name} is a script, not a native executable; NOT TESTED')
    if binary.name != prov['binary_name']:
        raise TamperError('binary name differs from the recorded native executable')
    if _sha(data) != prov['binary_sha256'] or len(data) != prov['binary_size']:
        raise TamperError('binary hash/size no longer matches the recorded native executable')

    scenario = manifest.get('scenario')
    if not isinstance(scenario, dict) or not isinstance(scenario.get('events'), list):
        raise TamperError('manifest scenario is missing or malformed')
    if not scenario['events']:
        raise TamperError('manifest scenario has no events; nothing was compared')
    scenario_digest = _sha(canonical_bytes(scenario))
    if scenario_digest != manifest.get('scenario_sha256'):
        raise TamperError('scenario.json hash mismatch')

    expected = manifest.get('record_sha256s')
    paths = manifest.get('record_paths')
    replays = manifest.get('replays')
    if not _is_int(replays) or replays < MIN_REPLAYS:
        raise TamperError(f'at least {MIN_REPLAYS} replays are required')
    if (not isinstance(expected, list) or not isinstance(paths, list)
            or len(expected) != replays or len(paths) != replays):
        raise TamperError('replay hash/path lists are missing or inconsistent')

    blobs: list[bytes] = []
    for index in range(replays):
        if not isinstance(expected[index], str):
            raise TamperError(f'replay-{index}: recorded hash is not a string')
        run_dir = _safe_path(manifest_path.parent, paths[index])
        raw = (run_dir / 'record.json').read_bytes()
        if _sha(raw) != expected[index]:
            raise TamperError(f'replay-{index}: record.json hash mismatch')
        record = _read_json(run_dir / 'record.json', f'replay-{index} record')
        if canonical_bytes(record) != raw:
            raise TamperError(f'replay-{index}: record.json is not canonical JSON')
        _check_record(record, prov, scenario, scenario_digest, index)
        _check_steps(run_dir, record, scenario, index)
        blobs.append(raw)

    if any(blob != blobs[0] for blob in blobs[1:]):
        raise DeterminismError('replay records differ between independent native runs')

    return {
        'schema': SCHEMA, 'status': 'PASS', 'replays': len(blobs),
        'binary_sha256': prov['binary_sha256'],
        'scenario_sha256': scenario_digest,
        'record_sha256s': [_sha(blob) for blob in blobs],
        'what_it_proves': (
            'CLI-supplied records from a declared real native process, checked '
            'against the CLI-supplied executable, are byte-identical across '
            'independent runs; every scenario event was consumed and every '
            'stored frame still hashes to a non-blank frame'),
        'not_claimed': NOT_CLAIMED,
    }


def capture_evidence(binary: Path, scenario_path: Path, out: Path,
                     prompt: str, replays: int) -> dict:
    """Run a real native process per replay and write verifiable records."""
    if replays < MIN_REPLAYS:
        raise ValueError(f'at least {MIN_REPLAYS} replays are required')
    if not prompt:
        raise ValueError('--prompt must name the consumed-event marker in process output')
    if not binary.is_file():
        raise NotTested(f'no real native binary at {binary}; refusing to invent evidence')
    binary_bytes = binary.read_bytes()
    if not _native_magic(binary_bytes):
        raise NotTested(f'{binary.name} is a script, not a native executable; NOT TESTED')

    scenario = load_scenario(scenario_path)
    for index, event in enumerate(scenario['events']):
        if event.get('type') != 'key' or not isinstance(event.get('key'), str):
            raise ScenarioError(
                f'event {index}: stdin capture supports named key events only')
    stdin = ''.join(event['key'] + '\n' for event in scenario['events']).encode('utf-8')
    marker = prompt.encode('utf-8')
    prov = _provenance(binary, prompt)
    scenario_digest = _sha(canonical_bytes(scenario))

    out.mkdir(parents=True, exist_ok=True)
    (out / 'scenario.json').write_bytes(canonical_bytes(scenario))
    blobs: list[bytes] = []
    for replay in range(replays):
        run_dir = out / f'replay-{replay}'
        (run_dir / 'frames').mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [str(binary.resolve())], input=stdin,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=30, check=False, cwd=str(out.resolve()))
        except (OSError, subprocess.SubprocessError) as exc:
            raise NotTested(f'replay-{replay}: real process could not run: {exc}') from exc
        output = completed.stdout
        offsets: list[int] = []
        pos = 0
        while True:
            found = output.find(marker, pos)
            if found < 0:
                break
            offsets.append(found + len(marker))
            pos = found + len(marker)
        if len(offsets) != len(scenario['events']):
            raise EventNotConsumed(
                f'replay-{replay}: process consumed {len(offsets)} of '
                f'{len(scenario["events"])} events (prompt {prompt!r}); refusing to claim '
                'that the whole scenario reached the real input pump')

        record = {
            'schema': SCHEMA, 'provenance': prov,
            'scenario_sha256': scenario_digest,
            'events': scenario['events'], 'status': 'PASS',
            'unconsumed_events': 0,
            'total_consumed_events': len(offsets),
            'events_delivered': scenario['events'],
            'exit_code': completed.returncode, 'steps': [],
        }
        start = 0
        for index, end in enumerate(offsets):
            frame_bytes = output[start:end]
            start = end
            name = f'frames/frame-{index:04d}.txt'
            (run_dir / name).write_bytes(frame_bytes)
            meta = text_frame_info(frame_bytes)
            meta['path'] = name
            if meta['blank']:
                raise FrameError(f'replay-{replay} step {index}: captured frame is blank')
            record['steps'].append({
                'index': index, 'event': scenario['events'][index],
                'consumed': True, 'consumed_event_count': index + 1,
                'frame': meta,
            })
        blob = canonical_bytes(record)
        (run_dir / 'record.json').write_bytes(blob)
        blobs.append(blob)

    manifest = {
        'schema': SCHEMA, 'kind': 'real_input_pump_proof',
        'provenance': prov, 'scenario': scenario,
        'scenario_sha256': scenario_digest,
        'replays': replays,
        'record_sha256s': [_sha(blob) for blob in blobs],
        'record_paths': [f'replay-{i}' for i in range(replays)],
        'not_claimed': NOT_CLAIMED,
    }
    (out / 'manifest.json').write_bytes(canonical_bytes(manifest))
    return verify_evidence(out / 'manifest.json', binary)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    cap = sub.add_parser('capture', help='run a real native process and capture records')
    cap.add_argument('--binary', required=True, type=Path,
                     help='path to the native executable that will actually run')
    cap.add_argument('--scenario', required=True, type=Path,
                     help='JSON file of timestamped key events')
    cap.add_argument('--out', required=True, type=Path, help='evidence directory')
    cap.add_argument('--prompt', required=True,
                     help='literal marker printed once per consumed event')
    cap.add_argument('--replays', type=int, default=MIN_REPLAYS)
    ver = sub.add_parser('verify', help='recompute hashes for CLI-supplied records')
    ver.add_argument('--manifest', required=True, type=Path)
    ver.add_argument('--binary', required=True, type=Path,
                     help='the real native executable named by the records')
    ver.add_argument('--out', required=True, type=Path, help='directory for result.json')
    args = ap.parse_args(argv)

    result: dict = {'schema': SCHEMA, 'status': 'FAIL'}
    code = 1
    try:
        if args.command == 'capture':
            result = capture_evidence(args.binary, args.scenario, args.out,
                                      args.prompt, args.replays)
        else:
            result = verify_evidence(args.manifest, args.binary)
        code = 0
    except NotTested as exc:
        result.update(status='NOT_TESTED', reason=f'{type(exc).__name__}: {exc}',
                      not_claimed=NOT_CLAIMED)
        code = 2
    except ProofError as exc:
        result.update(status='FAIL', reason=f'{type(exc).__name__}: {exc}',
                      not_claimed=NOT_CLAIMED)
    except Exception as exc:
        result.update(status='FAIL', reason=f'{type(exc).__name__}: {exc}',
                      not_claimed=NOT_CLAIMED)

    if args.command == 'verify':
        try:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / 'result.json').write_bytes(canonical_bytes(result))
        except OSError as exc:
            print(f'warning: could not write result.json: {exc}', file=sys.stderr)
    print(json.dumps(result, indent=2, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
