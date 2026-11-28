"""Deterministic tests for the issue #137 ELF -> PE feasibility gate.

Every artifact is synthetic and created under ``tmp_path``: no repository game
asset, no hard-coded host path and no live conversion is involved. The gate
must decide solely from the CLI-supplied evidence document and its hashes.
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tools.elf_to_pe_gate import (
    BLOCKED, DEFERRED, NOT_TESTED, PASS, evaluate, main,
)

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / 'tools' / 'elf_to_pe_gate.py'
CAPABILITIES = ('elf_reader', 'pe_writer', 'win32_runtime')
ITEMS = CAPABILITIES + ('built_pe', 'executed_scenario')


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, doc) -> None:
    path.write_text(json.dumps(doc), encoding='utf-8')


def _minimal_pe() -> bytes:
    """Small but structurally valid PE32 image (one .text section)."""
    dos = bytearray(64)
    dos[:2] = b'MZ'
    struct.pack_into('<I', dos, 0x3C, 64)  # e_lfanew
    coff = struct.pack('<HHIIIHH', 0x014C, 1, 0, 0, 0, 224, 0x0102)
    optional = struct.pack('<H', 0x010B) + bytes(222)  # PE32 magic
    section = (b'.text\x00\x00\x00'
               + struct.pack('<IIIIIIHHI', 0x1000, 0x1000, 0x200, 0x200,
                             0, 0, 0, 0, 0x60000020))
    return bytes(dos) + b'PE\x00\x00' + coff + optional + section


def _bundle(root: Path, *, flags_only: bool = False,
            fake_build_hash: bool = False, fake_pe: bool = False,
            absent: tuple[str, ...] = (), tamper_proof: str | None = None,
            bad_scenario_binding: bool = False,
            header_mismatch: bool = False) -> dict:
    """Create one complete (or deliberately broken) evidence bundle."""
    root.mkdir(parents=True, exist_ok=True)
    paths: dict = {}

    if flags_only:
        evidence = {name: True for name in ITEMS}
        if header_mismatch:
            evidence['issue'] = 999
        _json(root / 'evidence.json', evidence)
        paths['evidence'] = root / 'evidence.json'
        paths['json_out'] = root / 'decision.json'
        return paths

    impl = {
        'elf_reader': b'ELF_MAGIC = b"\\x7fELF"\n# parses e_ident and program headers\n',
        'pe_writer': b'PE_SIG = b"PE\\x00\\x00"\n# emits DOS/COFF/optional headers\n',
        'win32_runtime': b'def win32_runtime():\n    return "CreateProcessA"\n',
    }
    evidence: dict = {'issue': 137, 'direction': 'linux-elf-to-windows-pe'}
    for label in CAPABILITIES:
        path = root / f'{label}.py'
        path.write_bytes(impl[label])
        proof = root / f'{label}-audit.json'
        _json(proof, {
            'kind': label,
            'verdict': PASS,
            'oracle': 'license-and-upstream-audit',
            'license': 'MIT',
            'artifact_sha256': _sha(path),
            'note': 'synthetic audit record',
        })
        declared_proof_hash = _sha(proof)
        if tamper_proof == label:
            _json(proof, {'kind': label, 'verdict': 'PARTIAL'})
        row = {'path': str(path), 'sha256': _sha(path),
               'proof': {'path': str(proof), 'sha256': declared_proof_hash}}
        if label not in absent:
            evidence[label] = row

    pe = root / 'built.exe'
    if fake_pe:
        fake = bytearray(128)
        fake[:2] = b'MZ'
        struct.pack_into('<I', fake, 0x3C, 64)
        fake[64:68] = b'NOPE'  # no PE signature
        pe.write_bytes(bytes(fake))
    else:
        pe.write_bytes(_minimal_pe())
    if 'built_pe' not in absent:
        evidence['built_pe'] = {
            'path': str(pe),
            'sha256': '0' * 64 if fake_build_hash else _sha(pe),
        }

    scenario = root / 'scenario.json'
    _json(scenario, {
        'verdict': PASS,
        'oracle': 'windows-native-scenario',
        'scenario': 'print-argv',
        'source_os': 'linux',
        'target_os': 'windows',
        'exit_code': 0,
        'built_pe_sha256': ('f' * 64 if bad_scenario_binding else _sha(pe)),
    })
    if 'executed_scenario' not in absent:
        evidence['executed_scenario'] = {
            'path': str(scenario), 'sha256': _sha(scenario),
        }
    if header_mismatch:
        evidence['direction'] = 'windows-pe-to-linux-elf'

    _json(root / 'evidence.json', evidence)
    paths.update(evidence=root / 'evidence.json', json_out=root / 'decision.json',
                 pe=pe, scenario=scenario)
    return paths


def _run(paths: dict, *, cli: bool = False):
    argv = ['--evidence', str(paths['evidence']), '--json', str(paths['json_out'])]
    if cli:
        done = subprocess.run([sys.executable, str(TOOL), *argv],
                              capture_output=True, text=True, check=False)
        return done.returncode, json.loads(done.stdout)
    return main(argv), json.loads(paths['json_out'].read_text(encoding='utf-8'))


def _assert_blocked(result: dict, needle: str) -> None:
    assert result['feasible'] is False
    assert result['issue_closed'] is False
    assert result['status'] == BLOCKED
    assert any(needle in reason for reason in result['reasons']), result['reasons']


def test_flags_alone_never_pass(tmp_path):
    """Booleans are not evidence: no amount of flags can produce PASS."""
    paths = _bundle(tmp_path / 'flags', flags_only=True)
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['status'] == BLOCKED
    assert result['feasible'] is False
    assert result['issue_closed'] is False
    assert set(result['checks'].values()) == {BLOCKED}
    assert sum('flag' in reason for reason in result['reasons']) == len(ITEMS)
    assert 'flags alone never qualify' in result['rule']


def test_fabricated_build_hash_rejected(tmp_path):
    """A declared build digest that does not match the file bytes is BLOCKED."""
    paths = _bundle(tmp_path / 'fabricated', fake_build_hash=True)
    code, result = _run(paths, cli=True)
    assert code != 0
    _assert_blocked(result, 'built_pe: hash mismatch')
    assert result['checks']['built_pe'] == BLOCKED
    assert result['checks']['elf_reader'] == PASS, result['reasons']
    # With no proven PE digest the scenario cannot be bound at all.
    joined = ' '.join(result['reasons'])
    assert ('no proven PE to bind' in joined
            or 'built_pe_sha256 does not match' in joined), joined


def test_absent_components_are_deferred_not_pass(tmp_path):
    paths = _bundle(tmp_path / 'absent', absent=ITEMS)
    code, result = _run(paths)
    assert code != 0
    assert result['status'] == DEFERRED
    assert result['feasible'] is False
    assert result['issue_closed'] is False
    assert set(result['checks'].values()) == {DEFERRED}


def test_complete_hash_checked_evidence_can_pass(tmp_path):
    """PASS is reachable only when all five components are proven and bound."""
    paths = _bundle(tmp_path / 'complete')
    code, result = _run(paths, cli=True)
    assert code == 0
    assert result['status'] == PASS
    assert result['feasible'] is True
    assert result['issue_closed'] is False, 'a gate must never close the issue'
    assert set(result['checks'].values()) == {PASS}
    assert result['reasons'] == []


def test_fake_pe_payload_is_rejected(tmp_path):
    """A correct hash of a non-PE file is not proof of a built PE."""
    paths = _bundle(tmp_path / 'fakepe', fake_pe=True)
    code, result = _run(paths)
    assert code != 0
    _assert_blocked(result, 'built_pe: missing PE signature')
    assert result['checks']['built_pe'] == BLOCKED


def test_scenario_not_bound_to_built_pe_is_rejected(tmp_path):
    paths = _bundle(tmp_path / 'unbound', bad_scenario_binding=True)
    code, result = _run(paths)
    assert code != 0
    _assert_blocked(result, 'built_pe_sha256 does not match')
    assert result['checks']['executed_scenario'] == BLOCKED
    assert result['checks']['built_pe'] == PASS


def test_tampered_capability_audit_is_rejected(tmp_path):
    paths = _bundle(tmp_path / 'tampered', tamper_proof='win32_runtime')
    code, result = _run(paths)
    assert code != 0
    _assert_blocked(result, 'win32_runtime: audit proof hash mismatch')
    assert result['checks']['win32_runtime'] == BLOCKED


def test_malformed_or_missing_cli_input_is_not_tested(tmp_path):
    paths = _bundle(tmp_path / 'malformed')
    paths['evidence'].write_text('{not json', encoding='utf-8')
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['status'] == NOT_TESTED
    assert result['feasible'] is False
    assert set(result['checks'].values()) == {NOT_TESTED}
    assert any('INVALID_JSON' in reason for reason in result['reasons'])

    missing = _bundle(tmp_path / 'missing')
    missing['evidence'].unlink()
    code, result = _run(missing)
    assert code != 0
    assert result['status'] == NOT_TESTED
    assert any('MISSING_INPUT' in reason for reason in result['reasons'])

    wrong_shape = _bundle(tmp_path / 'shape')
    _json(wrong_shape['evidence'], ['elf_reader', True])
    code, result = _run(wrong_shape)
    assert code != 0
    assert result['status'] == NOT_TESTED
    assert any('INVALID_STRUCTURE' in reason for reason in result['reasons'])


def test_wrong_issue_or_direction_is_not_tested(tmp_path):
    for name, kwargs in (('issue', {'flags_only': True, 'header_mismatch': True}),
                         ('direction', {'header_mismatch': True})):
        paths = _bundle(tmp_path / name, **kwargs)
        code, result = _run(paths)
        assert code != 0
        assert result['status'] == NOT_TESTED


if __name__ == '__main__':
    pytest.main([__file__])
