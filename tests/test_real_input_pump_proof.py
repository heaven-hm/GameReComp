"""Tests for the fail-closed real input-pump evidence verifier.

The records built here are *unit fixtures for the verifier's logic*, not
evidence: the "binary" is a Mach-O-shaped byte string in tmp_path and the
frames are hand-written. They exercise hash recomputation, determinism and
refusal paths. No test claims a real game ran; the genuine native-process
capture is a separate CLI run documented in docs/INPUT_PUMP_REAL_EVIDENCE.md.
"""
from __future__ import annotations

import hashlib
import itertools
import json

import pytest

from tools.real_input_pump_proof import (
    DeterminismError,
    EventNotConsumed,
    FrameError,
    NotTested,
    REAL_ADAPTER,
    TamperError,
    main,
    text_frame_info,
    verify_evidence,
)
from tools.real_input_adapter import canonical_bytes

_EVIDENCE_SEQ = itertools.count()

EVENTS = [
    {'t': 0, 'type': 'key', 'action': 'down', 'key': '0'},
    {'t': 1, 'type': 'key', 'action': 'down', 'key': '9'},
    {'t': 2, 'type': 'key', 'action': 'down', 'key': 'Q'},
]
FRAME = (b'=== Minimalist Tetris (Score: 0) ===\n'
         b'############\n#[]       []#\n############\n'
         b'Controls: Choose column (0-9) to drop block, Q to quit: \n')


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rewrite(root, manifest, records):
    """Re-canonicalise records and the manifest after a fixture mutation."""
    hashes = []
    for index, record in enumerate(records):
        raw = canonical_bytes(record)
        (root / f'replay-{index}' / 'record.json').write_bytes(raw)
        hashes.append(_sha(raw))
    manifest['record_sha256s'] = hashes
    (root / 'manifest.json').write_bytes(canonical_bytes(manifest))
    return manifest


def make_evidence(tmp_path, *, adapter_kind=REAL_ADAPTER, synthetic=False,
                  fake_binary=False, blank=False, unconsumed=False,
                  divergent=False, traversal=False):
    """Write a self-consistent fake record set (verifier logic fixture only)."""
    root = tmp_path / f'evidence-{next(_EVIDENCE_SEQ)}'
    root.mkdir(parents=True)
    binary = root / 'native-fixture'
    binary.write_bytes(b'#!not-native\n' if fake_binary
                       else b'\xcf\xfa\xed\xfe' + b'fixture' + b'\x00' * 32)
    data = binary.read_bytes()
    scenario = {'schema': 1, 'name': 'fixture', 'events': EVENTS}
    prov = {
        'adapter_kind': adapter_kind,
        'synthetic': synthetic,
        'capture_method': 'stdin_script',
        'prompt': 'Controls:',
        'platform': 'darwin', 'machine': 'arm64',
        'binary_name': binary.name,
        'binary_sha256': _sha(data), 'binary_size': len(data),
    }
    digest = _sha(canonical_bytes(scenario))
    records, manifests = [], None
    for replay in range(2):
        run = root / f'replay-{replay}'
        (run / 'frames').mkdir(parents=True)
        payload = b'   \n' if blank else FRAME
        frame_path = 'frames/frame-0000.txt'
        (run / frame_path).write_bytes(payload)
        frame = {'path': '../escape.txt' if traversal else frame_path,
                 'kind': 'text', 'sha256': _sha(payload), 'bytes': len(payload),
                 'blank': False,
                 'distinct_symbols': text_frame_info(payload)['distinct_symbols']}
        record = {
            'schema': 1, 'provenance': prov, 'scenario_sha256': digest,
            'events': EVENTS, 'status': 'PASS',
            'unconsumed_events': 1 if unconsumed else 0,
            'total_consumed_events': len(EVENTS) - 1 if unconsumed else len(EVENTS),
            'events_delivered': EVENTS[:-1] if unconsumed else EVENTS,
            'exit_code': 99 if divergent and replay else 0,
            'steps': [
                {'index': i, 'event': event, 'consumed': True,
                 'consumed_event_count': i + 1, 'frame': frame}
                for i, event in enumerate(EVENTS)
            ],
        }
        if traversal:
            (root / 'escape.txt').write_bytes(payload)
        records.append(record)
    manifest = {
        'schema': 1, 'kind': 'real_input_pump_proof',
        'provenance': prov, 'scenario': scenario,
        'scenario_sha256': digest, 'replays': 2,
        'record_paths': ['replay-0', 'replay-1'],
        'record_sha256s': [],
    }
    _rewrite(root, manifest, records)
    return root, binary


def _verify(root, binary, tmp_path):
    out = tmp_path / 'result'
    code = main(['verify', '--manifest', str(root / 'manifest.json'),
                 '--binary', str(binary), '--out', str(out)])
    return code, json.loads((out / 'result.json').read_text())


@pytest.mark.parametrize('kind', [
    'fake', 'synthetic', 'stub', 'mock', 'in_memory', 'FakeRunner',
    'simulated_adapter', 'fake_pump',
])
def test_synthetic_adapters_are_never_pass(tmp_path, kind):
    root, binary = make_evidence(tmp_path, adapter_kind=kind)
    code, result = _verify(root, binary, tmp_path)
    assert code == 2
    assert result['status'] == 'NOT_TESTED'
    assert 'not a real native process' in result['reason']
    assert result['not_claimed']


def test_synthetic_flag_is_never_pass(tmp_path):
    root, binary = make_evidence(tmp_path, synthetic=True)
    code, result = _verify(root, binary, tmp_path)
    assert code == 2 and result['status'] == 'NOT_TESTED'
    assert 'synthetic' in result['reason']


def test_missing_binary_is_not_tested(tmp_path):
    root, binary = make_evidence(tmp_path)
    binary.unlink()
    code, result = _verify(root, binary, tmp_path)
    assert code == 2 and result['status'] == 'NOT_TESTED'
    assert 'no real native binary' in result['reason']


def test_script_binary_is_not_tested(tmp_path):
    root, binary = make_evidence(tmp_path, fake_binary=True)
    code, result = _verify(root, binary, tmp_path)
    assert code == 2 and result['status'] == 'NOT_TESTED'
    assert 'script' in result['reason']


def test_real_marked_evidence_verifies(tmp_path):
    root, binary = make_evidence(tmp_path)
    code, result = _verify(root, binary, tmp_path)
    assert code == 0 and result['status'] == 'PASS'
    assert result['replays'] == 2
    assert len(set(result['record_sha256s'])) == 1
    assert 'gameplay-equivalence' in result['not_claimed']
    assert 'A7' in result['not_claimed']


def test_tampered_frame_fails(tmp_path):
    root, binary = make_evidence(tmp_path)
    target = root / 'replay-0' / 'frames' / 'frame-0000.txt'
    target.write_bytes(target.read_bytes() + b'TAMPER')
    code, result = _verify(root, binary, tmp_path)
    assert code == 1 and result['status'] == 'FAIL'
    assert 'frame hash mismatch' in result['reason']


def test_tampered_record_fails(tmp_path):
    root, binary = make_evidence(tmp_path)
    path = root / 'replay-1' / 'record.json'
    record = json.loads(path.read_text())
    record['exit_code'] = 1234
    path.write_bytes(canonical_bytes(record))
    code, result = _verify(root, binary, tmp_path)
    assert code == 1 and result['status'] == 'FAIL'
    assert 'record.json hash mismatch' in result['reason']


def test_tampered_scenario_fails(tmp_path):
    root, binary = make_evidence(tmp_path)
    path = root / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['scenario']['events'][0]['key'] = 'X'
    path.write_bytes(canonical_bytes(manifest))
    code, result = _verify(root, binary, tmp_path)
    assert code == 1 and result['status'] == 'FAIL'
    assert 'scenario.json hash mismatch' in result['reason']


def test_divergent_replays_fail(tmp_path):
    root, binary = make_evidence(tmp_path, divergent=True)
    code, result = _verify(root, binary, tmp_path)
    assert code == 1 and result['status'] == 'FAIL'
    assert 'replay records differ' in result['reason']


@pytest.mark.parametrize('kind', ['unconsumed', 'blank', 'traversal'])
def test_fail_closed_on_bad_evidence(tmp_path, kind):
    root, binary = make_evidence(tmp_path, **{kind: True})
    code, result = _verify(root, binary, tmp_path)
    assert code == 1 and result['status'] == 'FAIL'
    if kind == 'unconsumed':
        assert 'unconsumed' in result['reason'].lower()
    elif kind == 'blank':
        assert 'blank' in result['reason']
    else:
        assert 'escapes' in result['reason']


def test_direct_verify_raises_typed_errors(tmp_path):
    root, binary = make_evidence(tmp_path, divergent=True)
    with pytest.raises(DeterminismError):
        verify_evidence(root / 'manifest.json', binary)
    root, binary = make_evidence(tmp_path, blank=True)
    with pytest.raises(FrameError):
        verify_evidence(root / 'manifest.json', binary)
    root, binary = make_evidence(tmp_path, unconsumed=True)
    with pytest.raises(EventNotConsumed):
        verify_evidence(root / 'manifest.json', binary)
    root, binary = make_evidence(tmp_path)
    (root / 'replay-0' / 'frames' / 'frame-0000.txt').write_bytes(b'TAMPER')
    with pytest.raises(TamperError):
        verify_evidence(root / 'manifest.json', binary)
    root, binary = make_evidence(tmp_path, adapter_kind='fake')
    with pytest.raises(NotTested):
        verify_evidence(root / 'manifest.json', binary)


def test_no_fake_pass_claim_for_synthetic_adapter(tmp_path):
    root, binary = make_evidence(tmp_path, adapter_kind='in_memory')
    code, result = _verify(root, binary, tmp_path)
    assert code == 2
    assert json.dumps(result) != json.dumps({'status': 'PASS'})
    assert result['status'] == 'NOT_TESTED'
