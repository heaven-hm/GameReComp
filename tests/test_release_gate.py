"""Deterministic tests for the fail-closed release gate.

Every case is built from synthetic files under tmp_path: no repository game
asset, no hard-coded host path and no live game is involved. The gate must
decide only from the three JSON documents supplied on the CLI.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from tools.release_gate import BLOCKED, NOT_TESTED, READY, main, sha256

TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'release_gate.py'
GAME = 'demo-game'
ORACLE = 'retail-windows'


def _json(path: Path, doc) -> None:
    path.write_text(json.dumps(doc), encoding='utf-8')


def _png(rows: list[list[tuple[int, int, int]]]) -> bytes:
    """Build a minimal 8-bit RGB PNG with filter type 0 (no dependencies)."""
    width, height = len(rows[0]), len(rows)
    raw = b''.join(b'\x00' + b''.join(bytes(px) for px in row) for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw))
            + chunk(b'IEND', b''))


def _case(tmp_path: Path, *, game_verdict: str = 'PASS', proof_verdict: str | None = None,
          oracle: str | None = ORACLE, proof_oracle: str | None = ORACLE,
          blank: bool = False, declare_blank: bool | None = None,
          with_oracle_proof: bool = True, declare_proof_hash: bool = True,
          tamper_proof: bool = False, missing_binary: bool = False,
          binary_hash: str | None = None, screenshot_hash: str | None = None,
          roadmap: dict | None = None, verification: dict | None = None,
          evidence_extra: dict | None = None) -> dict:
    """Create one complete (or deliberately broken) CLI evidence bundle."""
    root = tmp_path / 'case'
    root.mkdir(parents=True)

    binary = root / 'native.bin'
    binary.write_bytes(b'native recompiled binary payload\n')
    shot = root / 'frame.png'
    shot.write_bytes(_png([[(0, 0, 0), (255, 0, 0)]] if not blank
                          else [[(0, 0, 0), (0, 0, 0)]]))

    proof = root / 'oracle-proof.json'
    proof_doc = {
        'game': GAME,
        'status': proof_verdict if proof_verdict is not None else game_verdict,
        'oracle': proof_oracle,
        'evidence': 'recorded retail-windows scenario',
    }
    _json(proof, proof_doc)
    if tamper_proof:
        proof.write_text(proof.read_text(encoding='utf-8').replace(
            '"status": "' + proof_doc['status'] + '"', '"status": "PARTIAL"'),
            encoding='utf-8')

    shot_row = {'path': str(shot), 'sha256': screenshot_hash or sha256(shot)}
    if declare_blank is not None:
        shot_row['blank'] = declare_blank
    game_row = {
        'verdict': game_verdict,
        'oracle': oracle,
        'proof': str(proof) if with_oracle_proof else '',
    }
    if declare_proof_hash:
        # The hash is declared from the original file; tampering invalidates it.
        game_row['sha256'] = sha256(proof)
    evidence = {
        'binaries': [{'path': str(binary),
                      'sha256': binary_hash or sha256(binary)}],
        'screenshots': [shot_row],
        'games': {GAME: game_row},
    }
    if evidence_extra:
        evidence.update(evidence_extra)

    releases = {'version': '0.0.1', 'roadmap': {
        'A6-scenario': True, 'A7-oracle': True, 'A10-acceptance': True}
        if roadmap is None else roadmap}
    verification_doc = ({key: True for key in
                         ('pytest', 'selftests', 'difftest', 'differential_gate')}
                        if verification is None else verification)

    paths = {}
    for name, doc in (('evidence', evidence), ('releases', releases),
                      ('verification', verification_doc)):
        p = root / f'{name}.json'
        _json(p, doc)
        paths[name] = p
    paths['json_out'] = root / 'decision.json'
    paths['proof'] = proof
    if missing_binary:
        binary.unlink()
    return paths


def _argv(paths: dict) -> list[str]:
    return ['--evidence', str(paths['evidence']),
            '--releases', str(paths['releases']),
            '--verification', str(paths['verification']),
            '--json', str(paths['json_out'])]


def _run(paths: dict, *, cli: bool = False):
    if cli:
        done = subprocess.run([sys.executable, str(TOOL), *_argv(paths)],
                              capture_output=True, text=True, check=False)
        return done.returncode, json.loads(done.stdout)
    code = main(_argv(paths))
    return code, json.loads(paths['json_out'].read_text(encoding='utf-8'))


def _blocked_reason(result: dict, needle: str) -> None:
    assert result['status'] == BLOCKED
    assert result['release_allowed'] is False
    assert any(needle in reason for reason in result['reasons']), result['reasons']


def test_ready_release(tmp_path):
    paths = _case(tmp_path)
    code, result = _run(paths, cli=True)
    assert code == 0
    assert result['status'] == READY
    assert result['release_allowed'] is True
    assert set(result['checks'].values()) == {'PASS'}
    assert result['reasons'] == []
    assert 'oracle-backed' in result['rule']


def test_missing_binary_blocks(tmp_path):
    paths = _case(tmp_path, missing_binary=True)
    code, result = _run(paths)
    assert code != 0
    _blocked_reason(result, 'native.bin: missing')
    assert result['checks']['binaries'] == BLOCKED
    assert result['checks']['games'] == 'PASS'


def test_hash_mismatch_blocks(tmp_path):
    paths = _case(tmp_path, binary_hash='0' * 64)
    code, result = _run(paths)
    assert code != 0
    _blocked_reason(result, 'native.bin: hash mismatch')
    assert result['checks']['binaries'] == BLOCKED


def test_blank_screenshot_blocks_even_when_declared_nonblank(tmp_path):
    paths = _case(tmp_path, blank=True, declare_blank=False)
    code, result = _run(paths)
    assert code != 0
    _blocked_reason(result, 'frame.png: blank frame')
    assert result['checks']['screenshots'] == BLOCKED
    assert result['checks']['binaries'] == 'PASS'


def test_partial_game_never_qualifies(tmp_path):
    paths = _case(tmp_path, game_verdict='PARTIAL')
    code, result = _run(paths)
    assert code != 0
    _blocked_reason(result, 'PARTIAL is not an oracle-backed PASS')
    assert result['checks']['games'] == BLOCKED


def test_incomplete_roadmap_blocks(tmp_path):
    paths = _case(tmp_path, roadmap={'A6-scenario': True, 'A7-oracle': False})
    code, result = _run(paths)
    assert code != 0
    _blocked_reason(result, 'roadmap gates incomplete: A7-oracle')
    assert result['checks']['roadmap'] == BLOCKED


def test_failed_or_missing_verification_blocks(tmp_path):
    paths = _case(tmp_path, verification={
        'pytest': True, 'selftests': True, 'difftest': True,
        'differential_gate': False})
    code, result = _run(paths)
    assert code != 0
    _blocked_reason(result, 'verification failed or absent: differential_gate')
    assert result['checks']['verification'] == BLOCKED

    empty = _case(tmp_path / 'empty', verification={})
    code, result = _run(empty)
    assert code != 0
    _blocked_reason(result, 'verification failed or absent: pytest, selftests, '
                    'difftest, differential_gate')


def test_tampered_evidence_blocks(tmp_path):
    # 1. The evidence claims PASS but the oracle proof was edited to PARTIAL.
    edited = _case(tmp_path / 'verdict', tamper_proof=True,
                   declare_proof_hash=False)
    code, result = _run(edited)
    assert code != 0
    _blocked_reason(result, 'proof says PARTIAL, not PASS')
    assert result['checks']['games'] == BLOCKED

    # 2. The proof file no longer matches the hash declared in the evidence.
    tampered = _case(tmp_path / 'hash')
    tampered['proof'].write_text(
        json.dumps({'status': 'PASS', 'oracle': ORACLE,
                    'evidence': 'hand-edited after hashing'}), encoding='utf-8')
    code, result = _run(tampered)
    assert code != 0
    _blocked_reason(result, 'oracle proof hash mismatch (tampered)')

    # 3. A self-reported PASS with no oracle or no proof never qualifies.
    unproven = _case(tmp_path / 'unproven', oracle=None, proof_oracle=None)
    code, result = _run(unproven)
    assert code != 0
    _blocked_reason(result, 'PASS without an oracle')

    no_proof = _case(tmp_path / 'noproof', with_oracle_proof=False)
    code, result = _run(no_proof)
    assert code != 0
    _blocked_reason(result, 'PASS without an oracle proof')


def test_malformed_inputs_are_not_tested_not_a_crash(tmp_path):
    paths = _case(tmp_path / 'bad')
    paths['evidence'].write_text('{not json', encoding='utf-8')
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['status'] == NOT_TESTED
    assert result['release_allowed'] is False
    assert any('INVALID_JSON' in reason for reason in result['reasons'])
    assert set(result['checks'].values()) == {NOT_TESTED}

    missing = _case(tmp_path / 'missing')
    missing['releases'].unlink()
    code, result = _run(missing)
    assert code != 0
    assert result['status'] == NOT_TESTED
    assert any('MISSING_INPUT' in reason for reason in result['reasons'])

    wrong_shape = _case(tmp_path / 'shape')
    _json(wrong_shape['verification'], ['pytest'])
    code, result = _run(wrong_shape)
    assert code != 0
    assert result['status'] == NOT_TESTED
    assert any('INVALID_STRUCTURE' in reason for reason in result['reasons'])


def test_evidence_error_key_is_not_a_load_failure(tmp_path):
    # A top-level "error" key is ordinary evidence metadata, not a loader error.
    paths = _case(tmp_path, evidence_extra={'error': 'nothing to see here'})
    code, result = _run(paths)
    assert code == 0
    assert result['status'] == READY


def test_undecodable_screenshot_is_not_proven(tmp_path):
    paths = _case(tmp_path)
    shot_row = json.loads(paths['evidence'].read_text(encoding='utf-8'))['screenshots'][0]
    fake = paths['evidence'].parent / 'frame.png'
    fake.write_bytes(b'not an image')
    shot_row['sha256'] = sha256(fake)
    doc = json.loads(paths['evidence'].read_text(encoding='utf-8'))
    doc['screenshots'] = [shot_row]
    _json(paths['evidence'], doc)
    code, result = _run(paths)
    assert code != 0
    _blocked_reason(result, 'undecodable screenshot')


@pytest.mark.parametrize('verdict', ['PASS', 'PARTIAL', 'FAIL', 'BLOCKED', 'NOT TESTED'])
def test_only_pass_verdict_can_be_accepted(tmp_path, verdict):
    paths = _case(tmp_path / verdict.replace(' ', '_'), game_verdict=verdict)
    code, result = _run(paths)
    assert (code == 0) is (verdict == 'PASS')
