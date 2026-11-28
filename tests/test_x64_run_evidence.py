"""Deterministic tests for the x64 native run-evidence verifier.

Every artifact is synthetic and created under ``tmp_path``: no repository game
asset, no host-specific path and no live build or execution is involved. The
verifier must decide solely from the CLI-supplied evidence document and the
hashes it recomputes from those synthetic files. In particular a complete,
self-consistent record yields a narrow scoped claim, a compile-only record is
never promoted, and a tampered hash is BLOCKED.

Run with: python3 -m pytest tests/test_x64_run_evidence.py -q
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tools.x64_run_evidence import (
    BLOCKED, DEFERRED, FIELDS, NOT_TESTED, PASS, SCHEMA, SUPPORTED, evaluate,
    main,
)

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / 'tools' / 'x64_run_evidence.py'
_STATE_TERMS_RE = re.compile(
    r'\b(?:close|closes|closed|closing|resolve|resolves|resolved|fix|fixes|fixed)\b',
    re.IGNORECASE,
)


def _pe32plus() -> bytes:
    data = bytearray(0x200)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3C, 0x80)
    data[0x80:0x84] = b'PE\x00\x00'
    struct.pack_into('<H', data, 0x84, 0x8664)          # AMD64
    struct.pack_into('<H', data, 0x80 + 24, 0x20B)      # PE32+ optional magic
    return bytes(data)


def _pe32() -> bytes:
    data = bytearray(_pe32plus())
    struct.pack_into('<H', data, 0x80 + 24, 0x10B)      # PE32, not PE32+
    return bytes(data)


def _elf64() -> bytes:
    data = bytearray(64)
    data[:4] = b'\x7fELF'
    data[4] = 2                                         # ELFCLASS64
    data[5] = 1                                         # little endian
    struct.pack_into('<H', data, 18, 62)                 # EM_X86_64
    return bytes(data) + b'\x90\x90\x90\x90'


def _elf32() -> bytes:
    data = bytearray(64)
    data[:4] = b'\x7fELF'
    data[4] = 1                                         # ELFCLASS32
    struct.pack_into('<H', data, 18, 3)                  # EM_386
    return bytes(data) + b'\x90\x90\x90\x90'


def _macho64() -> bytes:
    data = bytearray(32)
    data[:4] = b'\xcf\xfa\xed\xfe'                      # 64-bit little-endian
    struct.pack_into('<i', data, 4, 0x01000007)          # CPU_TYPE_X86_64
    return bytes(data) + b'\x90\x90\x90\x90'


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, doc) -> None:
    path.write_text(json.dumps(doc), encoding='utf-8')


def _bundle(root: Path, **overrides) -> dict:
    """Create one complete (or caller-modified) evidence bundle."""
    root.mkdir(parents=True, exist_ok=True)
    pe = root / 'input.exe'
    pe.write_bytes(_pe32plus())
    binary = root / 'native64'
    binary.write_bytes(_pe32plus())
    out = root / 'run.out'
    out.write_bytes(b'usage: native64 [--selftest]\nanswer=42\n')

    record = {
        'pe32plus_input': {'path': str(pe), 'sha256': _sha(pe)},
        'native_binary': {'path': str(binary), 'sha256': _sha(binary)},
        'executed_command': f'{binary} --selftest',
        'exit_code': 0,
        'output': {'path': str(out), 'sha256': _sha(out)},
        'host_arch': 'x86_64',
    }
    record.update(overrides)
    doc = {'schema': SCHEMA, 'records': [record]}
    evidence = root / 'evidence.json'
    _json(evidence, doc)
    return {
        'root': root,
        'evidence': evidence,
        'json_out': root / 'decision.json',
        'record': record,
        'doc': doc,
        'pe': pe,
        'binary': binary,
        'output': out,
    }


def _rewrite(paths: dict, *, record=None, doc=None, drop=()) -> None:
    current = dict(paths['record'])
    for key in drop:
        current.pop(key, None)
    if record is not None:
        current.update(record)
    document = doc if doc is not None else {
        'schema': SCHEMA, 'records': [current]}
    _json(paths['evidence'], document)


def _run(paths: dict, *, cli: bool = False, stdin: bool = False):
    argv = ['--records', '-' if stdin else str(paths['evidence']),
            '--json', str(paths['json_out'])]
    if cli:
        done = subprocess.run([sys.executable, str(TOOL), *argv],
                              input=paths['evidence'].read_text(encoding='utf-8')
                              if stdin else None,
                              capture_output=True, text=True, check=False)
        return done.returncode, json.loads(done.stdout)
    return main(argv), json.loads(paths['json_out'].read_text(encoding='utf-8'))


def _assert_refused(result: dict, status: str, needle: str | None = None) -> None:
    assert result['status'] == status, result
    assert result['supported'] is False
    assert result['issue_action'] == 'none'
    assert result['supported_claims'] == []
    if needle is not None:
        assert any(needle in reason for reason in result['reasons']), result['reasons']


# --- complete evidence gives a narrow scoped claim ------------------------

def test_complete_record_is_supported(tmp_path):
    paths = _bundle(tmp_path / 'complete')
    code, result = _run(paths, cli=True)
    assert code == 0
    assert result['status'] == SUPPORTED
    assert result['supported'] is True
    assert result['issue_action'] == 'none'
    assert result['reasons'] == []
    assert set(result['checks']['record[0]'].values()) == {PASS}
    claims = result['supported_claims']
    assert len(claims) == 1
    claim = claims[0]
    assert claim['scope'] == 'exact-record-only'
    assert claim['pe32plus_sha256'] == _sha(paths['pe'])
    assert claim['native_binary_sha256'] == _sha(paths['binary'])
    assert claim['native_binary_format'] == 'pe32+-amd64'
    assert claim['executed_command'] == f"{paths['binary']} --selftest"
    assert claim['exit_code'] == 0
    assert claim['output_sha256'] == _sha(paths['output'])
    assert claim['host_arch'] == 'x86_64'


@pytest.mark.parametrize('kind,fmt', [
    ('pe32plus', 'pe32+-amd64'),
    ('elf64', 'elf64-x86-64'),
    ('macho64', 'macho64-x86-64'),
])
def test_native_binary_formats_are_verified(tmp_path, kind, fmt):
    payload = {'pe32plus': _pe32plus, 'elf64': _elf64,
               'macho64': _macho64}[kind]()
    paths = _bundle(tmp_path / kind)
    paths['binary'].write_bytes(payload)
    _rewrite(paths, record={'native_binary': {
        'path': str(paths['binary']), 'sha256': _sha(paths['binary'])}})
    code, result = _run(paths)
    assert code == 0
    assert result['supported_claims'][0]['native_binary_format'] == fmt


def test_declared_verdict_is_not_trusted(tmp_path):
    """A self-reported SUPPORTED flag cannot override a recomputed failure."""
    paths = _bundle(tmp_path / 'self-report')
    _rewrite(paths, record={
        'verdict': 'SUPPORTED',
        'supported': True,
        'native_binary': {'path': str(paths['binary']), 'sha256': '0' * 64},
    })
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'native_binary: hash mismatch')


# --- missing fields are DEFERRED -----------------------------------------

@pytest.mark.parametrize('field', FIELDS)
def test_each_missing_field_is_deferred(tmp_path, field):
    paths = _bundle(tmp_path / ('missing-' + field))
    _rewrite(paths, drop=(field,))
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, DEFERRED)
    assert result['checks']['record[0]'][field] == DEFERRED


def test_absent_records_are_not_tested(tmp_path):
    paths = _bundle(tmp_path / 'no-records')
    _rewrite(paths, doc={'schema': SCHEMA, 'records': []})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, NOT_TESTED, 'no records supplied')


# --- tampered and invalid evidence is BLOCKED -----------------------------

@pytest.mark.parametrize('field,label', [
    ('pe32plus_input', 'pe32plus_input'),
    ('native_binary', 'native_binary'),
    ('output', 'output'),
])
def test_tampered_hash_is_blocked(tmp_path, field, label):
    paths = _bundle(tmp_path / ('hash-' + field))
    row = dict(paths['record'][field])
    row['sha256'] = 'f' * 64
    _rewrite(paths, record={field: row})
    code, result = _run(paths, cli=True)
    assert code != 0
    _assert_refused(result, BLOCKED, f'{label}: hash mismatch')


def test_pe32plus_input_must_really_be_pe32plus(tmp_path):
    paths = _bundle(tmp_path / 'wrong-pe')
    paths['pe'].write_bytes(_pe32())
    _rewrite(paths, record={'pe32plus_input': {
        'path': str(paths['pe']), 'sha256': _sha(paths['pe'])}})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'PE optional magic is not PE32+')


def test_non_x64_binary_is_blocked(tmp_path):
    paths = _bundle(tmp_path / 'elf32')
    paths['binary'].write_bytes(_elf32())
    _rewrite(paths, record={'native_binary': {
        'path': str(paths['binary']), 'sha256': _sha(paths['binary'])}})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'ELF is not 64-bit')


def test_text_binary_is_blocked(tmp_path):
    paths = _bundle(tmp_path / 'text')
    paths['binary'].write_bytes(b'#!/bin/sh\necho not native\n')
    _rewrite(paths, record={'native_binary': {
        'path': str(paths['binary']), 'sha256': _sha(paths['binary'])}})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'unrecognised native format')


@pytest.mark.parametrize('bad_exit', [1, 3, -1, True, '0', 0.0])
def test_bad_exit_code_is_blocked(tmp_path, bad_exit):
    paths = _bundle(tmp_path / ('exit-' + str(bad_exit)))
    _rewrite(paths, record={'exit_code': bad_exit})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'exit_code:')


@pytest.mark.parametrize('arch', ['arm64', 'aarch64', 'i386', 'x86', 'unknown'])
def test_non_x64_host_arch_is_blocked(tmp_path, arch):
    paths = _bundle(tmp_path / ('arch-' + arch))
    _rewrite(paths, record={'host_arch': arch})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'host_arch: not an x86-64 spelling')


@pytest.mark.parametrize('arch', ['x86_64', 'x86-64', 'amd64', 'x64', 'AMD64'])
def test_x64_host_arch_spellings_are_accepted(tmp_path, arch):
    paths = _bundle(tmp_path / ('ok-arch-' + arch))
    _rewrite(paths, record={'host_arch': arch})
    code, result = _run(paths)
    assert code == 0
    assert result['status'] == SUPPORTED


def test_command_not_naming_binary_is_blocked(tmp_path):
    paths = _bundle(tmp_path / 'unbound')
    _rewrite(paths, record={'executed_command': './unrelated-tool --selftest'})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'does not name the native binary')


def test_empty_output_artifact_is_blocked(tmp_path):
    paths = _bundle(tmp_path / 'empty-output')
    paths['output'].write_bytes(b'')
    _rewrite(paths, record={'output': {
        'path': str(paths['output']), 'sha256': _sha(paths['output'])}})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'output: artifact file is empty')


def test_flag_values_are_not_evidence(tmp_path):
    for field in ('pe32plus_input', 'native_binary', 'output'):
        paths = _bundle(tmp_path / ('flag-' + field))
        _rewrite(paths, record={field: True})
        code, result = _run(paths)
        assert code != 0
        _assert_refused(result, BLOCKED, f'{field}: not a path/hash object')


def test_issue_state_terms_in_command_are_refused(tmp_path):
    paths = _bundle(tmp_path / 'state-term')
    bad_command = f"{paths['binary']} --selftest; gh issue close 13"
    _rewrite(paths, record={'executed_command': bad_command})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'executed_command: carries a banned term')
    assert not _STATE_TERMS_RE.search(json.dumps(result)), result


# --- compile-only is DEFERRED ---------------------------------------------

@pytest.mark.parametrize('marker', [
    {'compile_only': True},
    {'build_only': True},
    {'build_status': 'COMPILE_ONLY'},
    {'verdict': 'compile-only'},
    {'status': 'build only'},
])
def test_compile_only_marker_is_deferred(tmp_path, marker):
    paths = _bundle(tmp_path / ('marker-' + str(len(marker))))
    _rewrite(paths, record=marker)
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, DEFERRED, 'compile-only marker')
    assert set(result['checks']['record[0]'].values()) == {PASS}


@pytest.mark.parametrize('command', [
    'clang -c input.c -o native64',
    'gcc -c native64.c',
    'make native64',
    'true',
])
def test_compile_only_command_is_deferred(tmp_path, command):
    paths = _bundle(tmp_path / ('cmd-' + command.split()[0]))
    _rewrite(paths, record={'executed_command': command})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, DEFERRED, 'executed_command: compile-only')
    assert result['checks']['record[0]']['executed_command'] == DEFERRED


def test_compiled_binary_without_run_fields_is_deferred(tmp_path):
    """A compiled artifact alone is never promoted to a run claim."""
    paths = _bundle(tmp_path / 'compile-only-bundle')
    _rewrite(paths, drop=('executed_command', 'exit_code', 'output', 'host_arch'))
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, DEFERRED)
    assert result['checks']['record[0]']['native_binary'] == PASS


# --- multiple records and aggregation -------------------------------------

def test_all_records_supported_gives_all_claims(tmp_path):
    paths = _bundle(tmp_path / 'two-ok')
    second = json.loads(json.dumps(paths['record']))
    doc = {'schema': SCHEMA, 'records': [paths['record'], second]}
    _rewrite(paths, doc=doc)
    code, result = _run(paths)
    assert code == 0
    assert result['status'] == SUPPORTED
    assert len(result['supported_claims']) == 2
    assert set(result['checks']) == {'record[0]', 'record[1]'}


def test_one_deferred_record_holds_back_the_verdict(tmp_path):
    paths = _bundle(tmp_path / 'mixed-deferred')
    second = json.loads(json.dumps(paths['record']))
    second.pop('output')
    _rewrite(paths, doc={'schema': SCHEMA,
                         'records': [paths['record'], second]})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, DEFERRED)
    assert result['checks']['record[1]']['output'] == DEFERRED


def test_one_blocked_record_blocks_the_verdict(tmp_path):
    paths = _bundle(tmp_path / 'mixed-blocked')
    second = json.loads(json.dumps(paths['record']))
    second['exit_code'] = 7
    _rewrite(paths, doc={'schema': SCHEMA,
                         'records': [paths['record'], second]})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED)
    assert result['checks']['record[0]']['exit_code'] == PASS


# --- fail-closed CLI input handling ---------------------------------------

def test_malformed_cli_input_is_not_tested(tmp_path):
    paths = _bundle(tmp_path / 'malformed')
    paths['evidence'].write_text('{not json', encoding='utf-8')
    code, result = _run(paths, cli=True)
    assert code != 0
    _assert_refused(result, NOT_TESTED, 'INVALID_JSON')

    paths['evidence'].unlink()
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, NOT_TESTED, 'MISSING_INPUT')


def test_wrong_shape_and_schema_are_not_tested(tmp_path):
    paths = _bundle(tmp_path / 'shape')
    _json(paths['evidence'], ['pe32plus_input', True])
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, NOT_TESTED, 'INVALID_STRUCTURE')

    _json(paths['evidence'], {'schema': 'other', 'records': [paths['record']]})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, NOT_TESTED, 'schema mismatch')


def test_relative_paths_resolve_against_the_document(tmp_path):
    paths = _bundle(tmp_path / 'relative')
    rec = dict(paths['record'])
    for key in ('pe32plus_input', 'native_binary', 'output'):
        row = dict(rec[key])
        row['path'] = Path(row['path']).name
        rec[key] = row
    _rewrite(paths, record=rec)
    code, result = _run(paths)
    assert code == 0
    assert result['status'] == SUPPORTED


def test_stdin_document_is_accepted(tmp_path):
    paths = _bundle(tmp_path / 'stdin')
    code, result = _run(paths, cli=True, stdin=True)
    assert code == 0
    assert result['status'] == SUPPORTED


def test_evaluate_matches_cli_on_the_same_document(tmp_path):
    paths = _bundle(tmp_path / 'pure')
    direct = evaluate(json.loads(paths['evidence'].read_text(encoding='utf-8')),
                      paths['root'])
    code, via_cli = _run(paths)
    assert code == 0
    assert direct == via_cli


def test_tool_emits_no_issue_state_terms(tmp_path):
    """Every emitted decision, including refusals, is free of state terms."""
    cases = [
        _bundle(tmp_path / 'terms-ok'),
        _bundle(tmp_path / 'terms-blocked'),
        _bundle(tmp_path / 'terms-deferred'),
    ]
    _rewrite(cases[1], record={'exit_code': 9})
    _rewrite(cases[2], drop=('host_arch',))
    for paths in cases:
        _, result = _run(paths)
        assert not _STATE_TERMS_RE.search(json.dumps(result)), result


def test_tool_has_no_embedded_paths_or_execution(tmp_path):
    source = TOOL.read_text(encoding='utf-8')
    assert '/Users/' not in source
    assert '/home/' not in source
    assert 'C:\\' not in source
    assert 'subprocess' not in source
    assert 'os.system' not in source


if __name__ == '__main__':
    pytest.main([__file__])
