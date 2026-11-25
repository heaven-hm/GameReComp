"""Deterministic tests for the x64 DEFERRED support-claim auditor.

Every artifact is synthetic and created under ``tmp_path``: no repository game
asset, no host-specific path and no live build/run is involved. The auditor must
decide solely from the CLI-supplied evidence document and its hashes. In
particular a compile-only result is DEFERRED, a tampered hash is BLOCKED and a
complete, self-consistent bundle yields only a narrow scoped claim.

Run with: python3 -m pytest tests/test_x64_claim_audit.py -q
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tools.x64_claim_audit import (
    BLOCKED, DEFERRED, ITEMS, NOT_TESTED, SUPPORTED, evaluate, main,
)

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / 'tools' / 'x64_claim_audit.py'


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, doc) -> None:
    path.write_text(json.dumps(doc), encoding='utf-8')


def _elf64() -> bytes:
    data = bytearray(64)
    data[:4] = b'\x7fELF'
    data[4] = 2                                  # EI_CLASS = ELFCLASS64
    data[5] = 1                                  # EI_DATA = ELFDATA2LSB
    struct.pack_into('<H', data, 18, 62)          # e_machine = EM_X86_64
    return bytes(data) + b'\x90\x90\x90\x90'


def _elf32() -> bytes:
    data = bytearray(64)
    data[:4] = b'\x7fELF'
    data[4] = 1                                  # EI_CLASS = ELFCLASS32
    struct.pack_into('<H', data, 18, 3)           # e_machine = EM_386
    return bytes(data) + b'\x90\x90\x90\x90'


def _macho64() -> bytes:
    data = bytearray(32)
    data[:4] = b'\xcf\xfa\xed\xfe'                # 64-bit little-endian Mach-O
    struct.pack_into('<i', data, 4, 0x01000007)   # CPU_TYPE_X86_64
    return bytes(data) + b'\x90\x90\x90\x90'


def _pe32plus() -> bytes:
    data = bytearray(0x200)
    data[:2] = b'MZ'
    pe = 0x80
    struct.pack_into('<I', data, 0x3C, pe)
    data[pe:pe + 4] = b'PE\x00\x00'
    struct.pack_into('<H', data, pe + 4, 0x8664)  # IMAGE_FILE_MACHINE_AMD64
    struct.pack_into('<H', data, pe + 24, 0x20B)  # PE32+ optional magic
    return bytes(data)


BINARIES = {
    'elf64': _elf64(),
    'macho64': _macho64(),
    'pe32plus': _pe32plus(),
    'elf32': _elf32(),
    'text': b'#! /bin/sh\necho "not a native x64 binary"\n',
}


def _bundle(root: Path, *, binary_kind: str = 'elf64', flags_only: bool = False,
            omit_binary: bool = False, omit_scenario: bool = False,
            tamper_binary_hash: bool = False, tamper_scenario_hash: bool = False,
            scenario_overrides: dict | None = None) -> dict:
    """Create one complete (or deliberately broken) evidence bundle."""
    root.mkdir(parents=True, exist_ok=True)
    binary = root / 'native.bin'
    binary.write_bytes(BINARIES[binary_kind])
    scenario = root / 'scenario.json'
    scenario_doc = {
        'verdict': 'PASS',
        'oracle': 'host-execution-oracle',
        'scenario': 'entry-smoke',
        'host': 'darwin',
        'exit_code': 0,
        'built_binary_sha256': _sha(binary),
    }
    if scenario_overrides:
        scenario_doc.update(scenario_overrides)
    _json(scenario, scenario_doc)

    evidence: dict = {'issue': 13, 'direction': 'x64-native-build-run'}
    if flags_only:
        evidence = {name: True for name in ITEMS}
    else:
        if not omit_binary:
            evidence['built_native_binary'] = {
                'path': str(binary),
                'sha256': '0' * 64 if tamper_binary_hash else _sha(binary),
            }
        if not omit_scenario:
            evidence['executed_scenario'] = {
                'path': str(scenario),
                'sha256': 'f' * 64 if tamper_scenario_hash else _sha(scenario),
            }
    _json(root / 'evidence.json', evidence)
    return {'evidence': root / 'evidence.json', 'json_out': root / 'decision.json',
            'binary': binary, 'scenario': scenario}


def _run(paths: dict, *, cli: bool = False):
    argv = ['--evidence', str(paths['evidence']), '--json', str(paths['json_out'])]
    if cli:
        done = subprocess.run([sys.executable, str(TOOL), *argv],
                              capture_output=True, text=True, check=False)
        return done.returncode, json.loads(done.stdout)
    return main(argv), json.loads(paths['json_out'].read_text(encoding='utf-8'))


def _assert_false(result: dict, needle: str) -> None:
    assert result['supported'] is False
    assert result['issue_closed'] is False
    assert result['supported_claim'] is None
    assert any(needle in reason for reason in result['reasons']), result['reasons']


# --- compile-only is DEFERRED ---------------------------------------------

def test_compile_only_binary_without_scenario_is_deferred(tmp_path):
    """A built binary alone is a compile-only result, never a support claim."""
    paths = _bundle(tmp_path / 'compile-only', omit_scenario=True)
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['status'] == DEFERRED
    assert result['checks']['built_native_binary'] == 'PASS'
    assert result['checks']['executed_scenario'] == DEFERRED
    _assert_false(result, 'executed_scenario: absent')
    assert 'compile-only' in ' '.join(result['reasons'])
    assert 'compile-only results are DEFERRED' in result['rule']


def test_empty_evidence_is_deferred(tmp_path):
    paths = _bundle(tmp_path / 'empty')
    _json(paths['evidence'], {'issue': 13, 'direction': 'x64-native-build-run'})
    code, result = _run(paths)
    assert code != 0
    assert result['status'] == DEFERRED
    assert set(result['checks'].values()) == {DEFERRED}
    _assert_false(result, 'built_native_binary: absent')


def test_explicit_compile_only_marker_is_deferred(tmp_path):
    """An explicit compile-only marker blocks a claim even with both artifacts."""
    paths = _bundle(tmp_path / 'marker')
    doc = json.loads(paths['evidence'].read_text(encoding='utf-8'))
    doc['build_status'] = 'COMPILE_ONLY'
    _json(paths['evidence'], doc)
    code, result = _run(paths)
    assert code != 0
    assert result['status'] == DEFERRED
    assert set(result['checks'].values()) == {'PASS'}
    _assert_false(result, 'compile-only result')


# --- tampered / invalid hashes are rejected -------------------------------

def test_tampered_binary_hash_is_rejected(tmp_path):
    paths = _bundle(tmp_path / 'bad-binary-hash', tamper_binary_hash=True)
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['status'] == BLOCKED
    assert result['checks']['built_native_binary'] == BLOCKED
    _assert_false(result, 'built_native_binary: hash mismatch')


def test_tampered_scenario_hash_is_rejected(tmp_path):
    paths = _bundle(tmp_path / 'bad-scenario-hash', tamper_scenario_hash=True)
    code, result = _run(paths)
    assert code != 0
    assert result['status'] == BLOCKED
    assert result['checks']['executed_scenario'] == BLOCKED
    _assert_false(result, 'executed_scenario: hash mismatch')


def test_scenario_bound_to_wrong_hash_is_rejected(tmp_path):
    paths = _bundle(tmp_path / 'bad-binding',
                    scenario_overrides={'built_binary_sha256': 'f' * 64})
    code, result = _run(paths)
    assert code != 0
    assert result['status'] == BLOCKED
    _assert_false(result, 'built_binary_sha256 does not match the binary')


def test_hash_of_non_binary_is_blocked(tmp_path):
    paths = _bundle(tmp_path / 'text', binary_kind='text')
    code, result = _run(paths)
    assert code != 0
    assert result['status'] == BLOCKED
    assert result['checks']['built_native_binary'] == BLOCKED
    _assert_false(result, 'not a real x64 native binary')


def test_32bit_elf_is_blocked(tmp_path):
    """A 32-bit x86 binary is not an x64 native binary."""
    paths = _bundle(tmp_path / 'elf32', binary_kind='elf32')
    code, result = _run(paths)
    assert code != 0
    assert result['status'] == BLOCKED
    _assert_false(result, 'ELF is not 64-bit')


def test_flags_are_not_evidence(tmp_path):
    paths = _bundle(tmp_path / 'flags', flags_only=True)
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['status'] == BLOCKED
    assert set(result['checks'].values()) == {BLOCKED}
    assert sum('flag' in reason for reason in result['reasons']) == len(ITEMS)


def test_scenario_without_host_or_bad_exit_is_blocked(tmp_path):
    for name, override in (('no-host', {'host': 'none'}),
                           ('bad-exit', {'exit_code': 3}),
                           ('bool-exit', {'exit_code': False}),
                           ('no-oracle', {'oracle': 'self-reported'})):
        paths = _bundle(tmp_path / name, scenario_overrides=override)
        code, result = _run(paths)
        assert code != 0
        assert result['status'] == BLOCKED, (name, result)
        assert result['checks']['executed_scenario'] == BLOCKED


# --- complete evidence gives a scoped claim --------------------------------

def test_complete_evidence_gives_scoped_supported_claim(tmp_path):
    paths = _bundle(tmp_path / 'complete')
    code, result = _run(paths, cli=True)
    assert code == 0
    assert result['status'] == SUPPORTED
    assert result['supported'] is True
    assert result['issue_closed'] is False, 'a gate must never close the issue'
    assert set(result['checks'].values()) == {'PASS'}
    assert result['reasons'] == []
    claim = result['supported_claim']
    assert claim is not None
    assert claim['scope'] == 'exact-binary-and-scenario-only'
    assert claim['binary_sha256'] == _sha(paths['binary'])
    assert claim['binary_format'] == 'elf64-x86-64'
    assert claim['scenario'] == 'entry-smoke'
    assert claim['oracle'] == 'host-execution-oracle'
    assert claim['host'] == 'darwin'


@pytest.mark.parametrize('kind,expected', [
    ('elf64', 'elf64-x86-64'),
    ('macho64', 'macho64-x86-64'),
    ('pe32plus', 'pe32+-amd64'),
])
def test_all_x64_native_formats_are_supported(tmp_path, kind, expected):
    paths = _bundle(tmp_path / kind, binary_kind=kind)
    code, result = _run(paths)
    assert code == 0
    assert result['status'] == SUPPORTED
    assert result['supported_claim']['binary_format'] == expected


# --- fail-closed CLI input handling ----------------------------------------

def test_malformed_cli_input_is_not_tested(tmp_path):
    paths = _bundle(tmp_path / 'malformed')
    paths['evidence'].write_text('{not json', encoding='utf-8')
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['status'] == NOT_TESTED
    assert set(result['checks'].values()) == {NOT_TESTED}
    assert any('INVALID_JSON' in reason for reason in result['reasons'])

    missing = _bundle(tmp_path / 'missing')
    missing['evidence'].unlink()
    code, result = _run(missing)
    assert code != 0
    assert result['status'] == NOT_TESTED
    assert any('MISSING_INPUT' in reason for reason in result['reasons'])

    wrong_shape = _bundle(tmp_path / 'shape')
    _json(wrong_shape['evidence'], ['built_native_binary', True])
    code, result = _run(wrong_shape)
    assert code != 0
    assert result['status'] == NOT_TESTED
    assert any('INVALID_STRUCTURE' in reason for reason in result['reasons'])


def test_wrong_issue_or_direction_is_not_tested(tmp_path):
    for name, override in (('issue', {'issue': 999}),
                           ('direction', {'direction': 'x86-build-run'})):
        paths = _bundle(tmp_path / name)
        doc = json.loads(paths['evidence'].read_text(encoding='utf-8'))
        doc.update(override)
        _json(paths['evidence'], doc)
        code, result = _run(paths)
        assert code != 0
        assert result['status'] == NOT_TESTED, (name, result)


def test_evaluate_is_pure_over_the_supplied_document(tmp_path):
    """The pure decision function agrees with the CLI on the same evidence."""
    paths = _bundle(tmp_path / 'pure')
    direct = evaluate(json.loads(paths['evidence'].read_text(encoding='utf-8')))
    code, via_cli = _run(paths)
    assert code == 0
    assert direct == via_cli


if __name__ == '__main__':
    pytest.main([__file__])
