"""Deterministic tests for the Windows PE -> native Linux evidence verifier.

Every artifact is synthetic and created under ``tmp_path``: no repository game
asset, no host-specific path and no live Linux build or execution is involved.
The verifier must decide only from the CLI-supplied document and the hashes it
recomputes from those files. In particular:

  * a complete x86 + x64 bundle yields PASS for each architecture separately;
  * a macOS/Darwin host or dyld command is never substituted for Linux;
  * forged ELF headers, wrong PE classes, bad bindings and tampered hashes are
    BLOCKED;
  * missing fields, compile-only markers and absent records are DEFERRED or
    NOT TESTED, never a fabricated PASS.

Run with: python3 -m pytest tests/test_linux_native_evidence.py -q
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

from tools.linux_native_evidence import (
    ARCHES, BLOCKED, CHECKS, DEFERRED, NOT_TESTED, PASS, SCHEMA, evaluate, main,
)

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / 'tools' / 'linux_native_evidence.py'
_STATE_TERMS_RE = re.compile(
    r'\b(?:close|closes|closed|closing|resolve|resolves|resolved|fix|fixes|fixed)\b',
    re.IGNORECASE,
)


def _pe(bits: int) -> bytes:
    data = bytearray(0x200)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3C, 0x80)
    data[0x80:0x84] = b'PE\x00\x00'
    machine = 0x8664 if bits == 64 else 0x014C
    magic = 0x20B if bits == 64 else 0x10B
    struct.pack_into('<H', data, 0x84, machine)
    struct.pack_into('<H', data, 0x80 + 24, magic)
    return bytes(data)


def _elf(bits: int) -> bytes:
    """A structurally real, minimal native ELF with one in-file PT_LOAD."""
    if bits == 64:
        ehsize, phentsize = 64, 56
        data = bytearray(ehsize + phentsize + 32)
        data[:4] = b'\x7fELF'
        data[4], data[5], data[6] = 2, 1, 1
        struct.pack_into('<H', data, 16, 2)          # ET_EXEC
        struct.pack_into('<H', data, 18, 62)         # EM_X86_64
        struct.pack_into('<I', data, 20, 1)          # e_version
        struct.pack_into('<Q', data, 24, 0x401000)   # e_entry
        struct.pack_into('<Q', data, 32, ehsize)     # e_phoff
        struct.pack_into('<H', data, 52, ehsize)
        struct.pack_into('<H', data, 54, phentsize)
        struct.pack_into('<H', data, 56, 1)          # e_phnum
        off = ehsize
        struct.pack_into('<I', data, off, 1)         # PT_LOAD
        struct.pack_into('<Q', data, off + 8, ehsize + phentsize)
        struct.pack_into('<Q', data, off + 32, 32)   # p_filesz
    else:
        ehsize, phentsize = 52, 32
        data = bytearray(ehsize + phentsize + 32)
        data[:4] = b'\x7fELF'
        data[4], data[5], data[6] = 1, 1, 1
        struct.pack_into('<H', data, 16, 2)          # ET_EXEC
        struct.pack_into('<H', data, 18, 3)          # EM_386
        struct.pack_into('<I', data, 20, 1)
        struct.pack_into('<I', data, 24, 0x08048000)
        struct.pack_into('<I', data, 28, ehsize)
        struct.pack_into('<H', data, 40, ehsize)
        struct.pack_into('<H', data, 42, phentsize)
        struct.pack_into('<H', data, 44, 1)
        off = ehsize
        struct.pack_into('<I', data, off, 1)         # PT_LOAD
        struct.pack_into('<I', data, off + 4, ehsize + phentsize)
        struct.pack_into('<I', data, off + 16, 32)   # p_filesz
    return bytes(data)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, doc) -> None:
    path.write_text(json.dumps(doc), encoding='utf-8')


def _make_record(root: Path, arch: str) -> tuple[dict, dict]:
    """Write a complete, self-consistent record for one architecture."""
    root.mkdir(parents=True, exist_ok=True)
    bits = 32 if arch == 'x86' else 64
    pe = root / 'original.exe'
    elf = root / 'native-elf'
    log = root / 'stage.log'
    scenario = root / 'scenario.json'
    pe.write_bytes(_pe(bits))
    elf.write_bytes(_elf(bits))
    log.write_bytes(f'stage: built {arch}\nstage: scenario ran\n'.encode())
    obj = {
        'verdict': 'PASS',
        'scenario': f'selftest-{arch}',
        'host': 'Linux',
        'command': f'{elf} --selftest',
        'exit_code': 0,
        'original_sha256': _sha(pe),
        'native_elf_sha256': _sha(elf),
        'stage_logs': [{'path': str(log), 'sha256': _sha(log)}],
    }
    _json(scenario, obj)
    record = {
        'arch': arch,
        'host': {
            'os': 'Linux',
            'kernel': 'Linux 6.1.0-linux-host',
            'machine': 'i686' if arch == 'x86' else 'x86_64',
        },
        'original': {'path': str(pe), 'sha256': _sha(pe)},
        'native_elf': {'path': str(elf), 'sha256': _sha(elf)},
        'executed_scenario': {'path': str(scenario), 'sha256': _sha(scenario)},
    }
    return record, {
        'root': root, 'original': pe, 'native_elf': elf, 'log': log,
        'scenario': scenario, 'scenario_obj': obj,
    }


def _bundle(root: Path, arches=ARCHES) -> dict:
    records: dict[str, dict] = {}
    paths: dict[str, dict] = {}
    for arch in arches:
        rec, extra = _make_record(root / arch, arch)
        records[arch], paths[arch] = rec, {'record': rec, **extra}
    doc = {'schema': SCHEMA, 'records': [records[a] for a in arches]}
    evidence = root / 'evidence.json'
    _json(evidence, doc)
    return {'root': root, 'doc': doc, 'evidence': evidence,
            'json_out': root / 'decision.json', 'by_arch': paths}


def _rewrite(paths: dict, arch: str = 'x86', *, record=None,
             doc=None, drop=()) -> None:
    cur = dict(paths['by_arch'][arch]['record'])
    for key in drop:
        cur.pop(key, None)
    if record is not None:
        cur.update(record)
    paths['by_arch'][arch]['record'] = cur
    document = doc if doc is not None else {
        **paths['doc'],
        'records': [paths['by_arch'][a]['record'] for a in ARCHES
                    if a in paths['by_arch']],
    }
    paths['doc'] = document
    _json(paths['evidence'], document)


def _rewrite_scenario(paths: dict, arch: str = 'x86', **overrides) -> None:
    """Change scenario fields, re-hash the file and update the record."""
    p = paths['by_arch'][arch]
    obj = dict(p['scenario_obj'])
    obj.update(overrides)
    p['scenario_obj'] = obj
    _json(p['scenario'], obj)
    _rewrite(paths, arch, record={'executed_scenario': {
        'path': str(p['scenario']), 'sha256': _sha(p['scenario'])}})


def _rebind(paths: dict, arch: str = 'x86') -> None:
    """Re-hash every artifact after a file changed, keeping the bundle honest."""
    p = paths['by_arch'][arch]
    _rewrite_scenario(paths, arch,
                      original_sha256=_sha(p['original']),
                      native_elf_sha256=_sha(p['native_elf']))
    _rewrite(paths, arch, record={
        'original': {'path': str(p['original']), 'sha256': _sha(p['original'])},
        'native_elf': {'path': str(p['native_elf']), 'sha256': _sha(p['native_elf'])},
    })


def _run(paths: dict, *, cli: bool = False, stdin: bool = False):
    argv = ['--records', '-' if stdin else str(paths['evidence']),
            '--json', str(paths['json_out'])]
    if cli:
        done = subprocess.run(
            [sys.executable, str(TOOL), *argv],
            input=paths['evidence'].read_text(encoding='utf-8') if stdin else None,
            capture_output=True, text=True, check=False)
        return done.returncode, json.loads(done.stdout)
    return main(argv), json.loads(paths['json_out'].read_text(encoding='utf-8'))


def _assert_refused(result: dict, status: str, needle: str | None = None) -> None:
    assert result['status'] == status, result
    assert result['passed'] is False
    assert result['issue_action'] == 'none'
    # A scoped claim may survive for the other architecture, but never for a
    # record whose own verdict refused it.
    for arch, item in result['architectures'].items():
        if item['status'] != PASS:
            assert item['claim'] is None, (arch, item)
    proven = {arch for arch, item in result['architectures'].items()
              if item['status'] == PASS}
    assert {claim['arch'] for claim in result['supported_claims']} <= proven
    if needle is not None:
        assert any(needle in reason for reason in result['reasons']), result['reasons']


# --- complete evidence gives two separate PASS verdicts -------------------

def test_both_arches_pass_with_complete_records(tmp_path):
    paths = _bundle(tmp_path / 'complete')
    code, result = _run(paths, cli=True)
    assert code == 0
    assert result['status'] == PASS
    assert result['passed'] is True
    assert result['reasons'] == []
    assert set(result['architectures']) == set(ARCHES)
    for arch, expected_fmt in (('x86', 'pe32-i386'), ('x64', 'pe32+-amd64')):
        item = result['architectures'][arch]
        assert item['status'] == PASS
        assert set(item['checks'].values()) == {PASS}
        assert item['reasons'] == []
        assert item['claim']['scope'] == f'exact-{arch}-record-only'
        assert item['claim']['original_format'] == expected_fmt
        assert item['claim']['native_elf_format'] == (
            'elf32-i386' if arch == 'x86' else 'elf64-x86-64')
        assert item['claim']['original_sha256'] == _sha(paths['by_arch'][arch]['original'])
        assert item['claim']['native_elf_sha256'] == _sha(paths['by_arch'][arch]['native_elf'])
    assert len(result['supported_claims']) == 2


def test_self_reported_pass_flags_are_not_evidence(tmp_path):
    """A record-level PASS flag cannot survive one tampered artifact."""
    paths = _bundle(tmp_path / 'self-report')
    _rewrite(paths, 'x64', record={
        'verdict': 'PASS', 'status': 'PASS', 'passed': True,
        'original': {'path': str(paths['by_arch']['x64']['original']),
                     'sha256': '0' * 64},
    })
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'x64: original: hash mismatch')
    assert result['architectures']['x86']['status'] == PASS
    assert result['architectures']['x64']['status'] == BLOCKED


def test_architectures_are_recorded_separately(tmp_path):
    """A bad x86 ELF does not silently pass or hide the good x64 record."""
    paths = _bundle(tmp_path / 'separate')
    paths['by_arch']['x86']['native_elf'].write_bytes(_elf(64))
    _rebind(paths, 'x86')
    code, result = _run(paths)
    assert code != 0
    assert result['architectures']['x86']['status'] == BLOCKED
    assert result['architectures']['x64']['status'] == PASS
    assert any('x86: native_elf:' in reason for reason in result['reasons'])


# --- the original PE and native ELF must really match the architecture ----

@pytest.mark.parametrize('arch,bad_bits,needle', [
    ('x86', 64, 'not a real PE32/i386 image'),
    ('x64', 32, 'not a real PE32+/AMD64 image'),
])
def test_original_pe_must_match_arch(tmp_path, arch, bad_bits, needle):
    paths = _bundle(tmp_path / ('pe-' + arch))
    paths['by_arch'][arch]['original'].write_bytes(_pe(bad_bits))
    _rebind(paths, arch)
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, needle)


@pytest.mark.parametrize('arch', ARCHES)
def test_original_text_file_is_blocked(tmp_path, arch):
    paths = _bundle(tmp_path / ('pe-text-' + arch))
    paths['by_arch'][arch]['original'].write_bytes(b'not a PE image\n')
    _rebind(paths, arch)
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'not a DOS/PE image')


@pytest.mark.parametrize('arch,bad_bits', [('x86', 64), ('x64', 32)])
def test_native_elf_must_match_arch(tmp_path, arch, bad_bits):
    paths = _bundle(tmp_path / ('elf-arch-' + arch))
    paths['by_arch'][arch]['native_elf'].write_bytes(_elf(bad_bits))
    _rebind(paths, arch)
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'native_elf: not a real native ELF')


@pytest.mark.parametrize('payload,needle', [
    (b'\x7fELF' + b'\x00' * 200, 'EI_CLASS'),
    (b'#!/bin/sh\necho not native\n', 'missing ELF magic'),
    (b'MZ\x90\x00' + b'\x00' * 200, 'missing ELF magic'),
])
def test_non_elf_payloads_are_blocked(tmp_path, payload, needle):
    paths = _bundle(tmp_path / ('elf-fake-' + needle[:6]))
    paths['by_arch']['x86']['native_elf'].write_bytes(payload)
    _rebind(paths, 'x86')
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, needle)


@pytest.mark.parametrize('mutate,needle', [
    (lambda d: d.__setitem__(6, 0), 'EI_VERSION'),
    (lambda d: struct.pack_into('<H', d, 18, 183), 'ELF machine'),
    (lambda d: struct.pack_into('<H', d, 56, 0), 'program header table is absent'),
    (lambda d: struct.pack_into('<Q', d, 24, 0), 'entry point is zero'),
    (lambda d: struct.pack_into('<I', d, 64, 4), 'no in-file PT_LOAD'),
    (lambda d: struct.pack_into('<Q', d, 72, 0xffff), 'no in-file PT_LOAD'),
])
def test_fabricated_elf_headers_are_blocked(tmp_path, mutate, needle):
    """Magic plus a few bytes is not a native ELF; structural checks bite."""
    paths = _bundle(tmp_path / ('elf-mut-' + str(abs(hash(needle)) % 9999)))
    data = bytearray(_elf(64))
    mutate(data)
    paths['by_arch']['x64']['native_elf'].write_bytes(bytes(data))
    _rebind(paths, 'x64')
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, needle)


# --- no macOS run may stand in for Linux ----------------------------------

@pytest.mark.parametrize('host', [
    {'os': 'Darwin', 'kernel': 'Darwin Kernel Version 23.1.0', 'machine': 'x86_64'},
    {'os': 'macOS', 'kernel': 'Darwin 23.1.0', 'machine': 'arm64'},
    {'os': 'Linux', 'kernel': 'Darwin Kernel Version 23.1.0', 'machine': 'x86_64'},
    {'os': 'Windows NT', 'kernel': 'Windows NT 10.0', 'machine': 'AMD64'},
])
def test_non_linux_host_facts_are_blocked(tmp_path, host):
    paths = _bundle(tmp_path / ('host-' + host['os'].split()[0]))
    _rewrite(paths, 'x64', record={'host': host})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED)
    assert result['architectures']['x64']['status'] == BLOCKED


@pytest.mark.parametrize('scenario_host', ['macOS', 'Darwin', 'macOS arm64', 'Windows'])
def test_macos_scenario_host_is_blocked(tmp_path, scenario_host):
    paths = _bundle(tmp_path / ('scen-host-' + scenario_host.split()[0]))
    _rewrite_scenario(paths, 'x86', host=scenario_host)
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'no macOS/Darwin run may be substituted')


def test_dyld_command_is_not_a_linux_run(tmp_path):
    paths = _bundle(tmp_path / 'dyld')
    p = paths['by_arch']['x64']
    _rewrite_scenario(paths, 'x64',
                      command=f"DYLD_LIBRARY_PATH=/tmp {p['native_elf']} --selftest")
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'macOS runtime in command')


@pytest.mark.parametrize('arch,machine,needle', [
    ('x86', 'x86_64', 'not an x86 host'),
    ('x64', 'i686', 'not an x64 host'),
])
def test_host_machine_must_match_arch(tmp_path, arch, machine, needle):
    paths = _bundle(tmp_path / ('host-arch-' + arch))
    host = dict(paths['by_arch'][arch]['record']['host'])
    host['machine'] = machine
    _rewrite(paths, arch, record={'host': host})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, needle)


# --- tamper detection and scenario bindings -------------------------------

@pytest.mark.parametrize('field', ['original', 'native_elf', 'executed_scenario'])
def test_tampered_declared_hash_is_blocked(tmp_path, field):
    paths = _bundle(tmp_path / ('hash-' + field))
    row = dict(paths['by_arch']['x86']['record'][field])
    row['sha256'] = 'f' * 64
    _rewrite(paths, 'x86', record={field: row})
    code, result = _run(paths, cli=True)
    assert code != 0
    _assert_refused(result, BLOCKED, f'x86: {field}: hash mismatch')


@pytest.mark.parametrize('field,needle', [
    ('original_sha256', 'original_sha256 does not match the original image'),
    ('native_elf_sha256', 'native_elf_sha256 does not match the native ELF'),
])
def test_scenario_must_bind_both_digests(tmp_path, field, needle):
    paths = _bundle(tmp_path / ('bind-' + field))
    _rewrite_scenario(paths, 'x64', **{field: 'a' * 64})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, needle)


@pytest.mark.parametrize('verdict,exit_code,needle', [
    ('FAIL', 0, 'verdict is not PASS'),
    ('PASS', 1, 'exit_code is 1'),
    ('PASS', True, 'exit_code is not an integer'),
])
def test_scenario_verdict_and_exit_are_checked(tmp_path, verdict, exit_code, needle):
    paths = _bundle(tmp_path / ('verdict-' + str(exit_code)))
    _rewrite_scenario(paths, 'x86', verdict=verdict, exit_code=exit_code)
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, needle)


def test_scenario_command_must_name_the_native_elf(tmp_path):
    paths = _bundle(tmp_path / 'unbound')
    _rewrite_scenario(paths, 'x86', command='./unrelated-tool --selftest')
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'command does not name the native ELF')


def test_bad_stage_log_hash_is_blocked(tmp_path):
    paths = _bundle(tmp_path / 'bad-log')
    log = paths['by_arch']['x64']['log']
    log.write_bytes(b'altered log\n')
    _rewrite_scenario(paths, 'x64', stage_logs=[
        {'path': str(log), 'sha256': '0' * 64}])
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'stage_logs[0]: hash mismatch')


# --- missing / compile-only evidence is never a PASS ----------------------

@pytest.mark.parametrize('field', CHECKS)
def test_each_missing_field_is_deferred(tmp_path, field):
    paths = _bundle(tmp_path / ('missing-' + field))
    _rewrite(paths, 'x86', drop=(field,))
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, DEFERRED)
    assert result['architectures']['x86']['checks'][field] == DEFERRED


@pytest.mark.parametrize('overrides', [
    {'compile_only': True},
    {'build_only': True},
    {'verdict': 'BUILD-ONLY'},
    {'status': 'build only'},
])
def test_compile_only_scenario_is_deferred(tmp_path, overrides):
    paths = _bundle(tmp_path / ('compile-' + str(len(overrides))))
    _rewrite_scenario(paths, 'x86', **overrides)
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, DEFERRED, 'compile-only marker')


@pytest.mark.parametrize('command', ['gcc -c native.c', 'make native-elf', 'true'])
def test_compile_only_command_is_deferred(tmp_path, command):
    paths = _bundle(tmp_path / ('cmd-' + command.split()[0]))
    _rewrite_scenario(paths, 'x86', command=command)
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, DEFERRED, 'command is compile-only')


def test_no_records_and_missing_arch_are_not_tested(tmp_path):
    paths = _bundle(tmp_path / 'no-records')
    _rewrite(paths, doc={**paths['doc'], 'records': []})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, NOT_TESTED, 'no records supplied')

    paths = _bundle(tmp_path / 'one-arch')
    _rewrite(paths, doc={**paths['doc'], 'records': [paths['by_arch']['x86']['record']]})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, NOT_TESTED, 'x64: no record supplied')
    assert result['architectures']['x64']['status'] == NOT_TESTED


def test_duplicate_and_unknown_arch_records_are_blocked(tmp_path):
    paths = _bundle(tmp_path / 'dups')
    record = paths['by_arch']['x86']['record']
    _rewrite(paths, doc={**paths['doc'], 'records': [record, dict(record)]})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'duplicate records')

    paths = _bundle(tmp_path / 'unknown')
    other = dict(paths['by_arch']['x86']['record'], arch='arm64')
    _rewrite(paths, doc={**paths['doc'], 'records':
                         [paths['by_arch']['x86']['record'], other]})
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, BLOCKED, 'unsupported or missing arch')


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


@pytest.mark.parametrize('doc,needle', [
    (['not', 'an', 'object'], 'INVALID_STRUCTURE'),
    ({'schema': 'other', 'records': []}, 'schema mismatch'),
    ({'schema': SCHEMA, 'issue': 13, 'records': []}, 'issue mismatch'),
    ({'schema': SCHEMA, 'direction': 'other', 'records': []}, 'direction mismatch'),
])
def test_wrong_shape_and_metadata_are_not_tested(tmp_path, doc, needle):
    paths = _bundle(tmp_path / ('shape-' + needle.replace(' ', '-')))
    _json(paths['evidence'], doc)
    code, result = _run(paths)
    assert code != 0
    _assert_refused(result, NOT_TESTED, needle)


def test_relative_paths_resolve_against_the_document(tmp_path):
    paths = _bundle(tmp_path / 'relative')
    # Put the document in the x86 directory so its base directory is defined;
    # x86 artifacts are siblings and x64 artifacts are one directory up.
    evidence = paths['root'] / 'x86' / 'evidence.json'
    for arch in ARCHES:
        cur = dict(paths['by_arch'][arch]['record'])
        prefix = '' if arch == 'x86' else '../x64/'
        for key in ('original', 'native_elf', 'executed_scenario'):
            row = dict(cur[key])
            row['path'] = prefix + Path(row['path']).name
            cur[key] = row
        paths['by_arch'][arch]['record'] = cur
    paths['evidence'] = evidence
    _rewrite(paths)
    code, result = _run(paths)
    assert code == 0
    assert result['status'] == PASS


def test_stdin_document_is_accepted(tmp_path):
    paths = _bundle(tmp_path / 'stdin')
    code, result = _run(paths, cli=True, stdin=True)
    assert code == 0
    assert result['status'] == PASS


def test_evaluate_matches_cli_on_the_same_document(tmp_path):
    paths = _bundle(tmp_path / 'pure')
    direct = evaluate(json.loads(paths['evidence'].read_text(encoding='utf-8')),
                      paths['root'])
    code, via_cli = _run(paths)
    assert code == 0
    assert direct == via_cli


def test_tool_emits_no_issue_state_terms(tmp_path):
    cases = [
        _bundle(tmp_path / 'terms-ok'),
        _bundle(tmp_path / 'terms-blocked'),
        _bundle(tmp_path / 'terms-deferred'),
        _bundle(tmp_path / 'terms-not-tested'),
    ]
    _rewrite(cases[1], 'x64', record={'exit_code': 9})
    _rewrite(cases[2], 'x86', drop=('native_elf',))
    _rewrite(cases[3], doc={**cases[3]['doc'], 'records': []})
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
