"""Deterministic tests for the Win64 feasibility gate summary CLI.

Every artifact is synthetic and created under ``tmp_path``: no game asset, no
host-specific path and no live build/run is involved. The tool must decide only
from the CLI-supplied evidence document and the SHA-256 values it recomputes
from the named files. In particular:

  * the current NO-GO/DEFERRED gate state must stay DEFERRED;
  * a flags-only doc (or a doc whose boxes are ticked without a real binary)
    must never open the gate;
  * tampered hashes, non-x64 binaries and unbound scenarios are BLOCKED;
  * only a bundle with all criteria checked, passing guardrail tests, a real
    x64 native binary and an oracle-backed executed scenario is CLOSABLE;
  * the tool never closes an issue and never claims x64 support.

Run with: python3 -m pytest tests/test_win64_gate_summary.py -q
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tools.win64_gate_summary import (
    BLOCKED, CLOSABLE, CRITERIA, DEFERRED, ITEMS, NOT_TESTED, PASS,
    REQUIRED_TESTS, _parse_gate_doc, evaluate, main,
)

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / 'tools' / 'win64_gate_summary.py'
GATE_DOC = REPO / 'docs' / 'feasibility' / 'X64_LIFTER_GATE.md'
TEST_FILE = 'tests/test_x64_gate.py'


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, doc) -> None:
    path.write_text(json.dumps(doc), encoding='utf-8')


def _gate_doc_text(state='NO-GO', checked=()) -> str:
    """Build a synthetic gate doc with the required sections and checkboxes."""
    checked = set(checked)
    lines = [
        '# X64 lifter feasibility gate (Issue #148)',
        '',
        f'Status: **{state} — reproducible feasibility procedure.**',
        '',
        '## 1. Scope and fail-closed refusal boundary',
        '',
        '## 2. Inspect local upstream checkouts (exact commands)',
        '',
        '## 3. Licence constraints',
        '',
        '## 4. Benchmark a representative x64 function through a candidate lifter',
        '',
        '## 5. Go/no-go checklist',
        '',
    ]
    for name in CRITERIA:
        mark = 'x' if name in checked else ' '
        lines.append(f'- [{mark}] **{name} — synthetic criterion.**')
    lines += [
        '',
        '## 6. No x64 support claim without a built and executed native binary',
        '',
        '## 7. Reproduce the refusal (exact commands and expected output)',
        '',
        '## 8. Limits of this gate',
        '',
    ]
    return '\n'.join(lines)


def _elf64() -> bytes:
    data = bytearray(64)
    data[:4] = b'\x7fELF'
    data[4] = 2
    data[5] = 1
    struct.pack_into('<H', data, 18, 62)  # EM_X86_64
    return bytes(data) + b'\x90\x90\x90\x90'


def _elf32() -> bytes:
    data = bytearray(64)
    data[:4] = b'\x7fELF'
    data[4] = 1
    struct.pack_into('<H', data, 18, 3)  # EM_386
    return bytes(data) + b'\x90\x90\x90\x90'


def _macho64() -> bytes:
    data = bytearray(32)
    data[:4] = b'\xcf\xfa\xed\xfe'
    struct.pack_into('<i', data, 4, 0x01000007)  # CPU_TYPE_X86_64
    return bytes(data) + b'\x90\x90\x90\x90'


def _pe32plus() -> bytes:
    data = bytearray(0x200)
    data[:2] = b'MZ'
    pe = 0x80
    struct.pack_into('<I', data, 0x3C, pe)
    data[pe:pe + 4] = b'PE\x00\x00'
    struct.pack_into('<H', data, pe + 4, 0x8664)
    struct.pack_into('<H', data, pe + 24, 0x20B)
    return bytes(data)


BINARIES = {
    'elf64': _elf64(),
    'macho64': _macho64(),
    'pe32plus': _pe32plus(),
    'elf32': _elf32(),
    'text': b'#! /bin/sh\necho "this is not a native x64 binary"\n',
}


def _bundle(root: Path, *, checked=(), declared_state='NO-GO', flags_only=False,
            omit_doc=False, omit_tests=False, omit_binary=False,
            omit_scenario=False, tamper_doc=False, tamper_tests=False,
            tamper_binary=False, tamper_scenario=False, test_overrides=None,
            scenario_overrides=None, binary_kind='elf64', extra=None) -> dict:
    """Create one complete (or deliberately broken) evidence bundle."""
    root.mkdir(parents=True, exist_ok=True)
    doc = root / 'gate.md'
    doc.write_text(_gate_doc_text(declared_state, checked), encoding='utf-8')
    tests = root / 'tests.json'
    tests_doc = {
        'test_file': TEST_FILE,
        'tests': {name: 'passed' for name in REQUIRED_TESTS},
        'failed': 0,
        'errors': 0,
    }
    if test_overrides:
        tests_doc.update(test_overrides)
    _json(tests, tests_doc)
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

    evidence = {'schema': 'gamerecomp.win64-gate-summary.v1', 'issue': 13}
    if flags_only:
        evidence.update({name: True for name in ITEMS})
    else:
        if not omit_doc:
            evidence['gate_doc'] = {
                'path': str(doc),
                'sha256': '0' * 64 if tamper_doc else _sha(doc),
            }
        if not omit_tests:
            evidence['gate_tests'] = {
                'path': str(tests),
                'sha256': '0' * 64 if tamper_tests else _sha(tests),
            }
        if not omit_binary:
            evidence['native_binary'] = {
                'path': str(binary),
                'sha256': '0' * 64 if tamper_binary else _sha(binary),
            }
        if not omit_scenario:
            evidence['executed_scenario'] = {
                'path': str(scenario),
                'sha256': '0' * 64 if tamper_scenario else _sha(scenario),
            }
    if extra:
        evidence.update(extra)
    evidence_path = root / 'evidence.json'
    _json(evidence_path, evidence)
    return {'evidence': evidence_path, 'json_out': root / 'decision.json',
            'doc': doc, 'tests': tests, 'binary': binary, 'scenario': scenario}


def _run(paths: dict, *, cli: bool = False):
    argv = ['--evidence', str(paths['evidence']), '--json', str(paths['json_out'])]
    if cli:
        done = subprocess.run([sys.executable, str(TOOL), *argv],
                              capture_output=True, text=True, check=False)
        return done.returncode, json.loads(done.stdout)
    return main(argv), json.loads(paths['json_out'].read_text(encoding='utf-8'))


def _assert_unopened(result: dict, needle: str) -> None:
    assert result['verdict'] != CLOSABLE
    assert result['gate_open'] is False
    assert result['issue_closed'] is False
    assert result['supported_claim'] is None
    assert any(needle in reason for reason in result['reasons']), result['reasons']


# --- current state is DEFERRED ----------------------------------------------

def test_empty_evidence_is_deferred(tmp_path):
    paths = _bundle(tmp_path / 'empty', omit_doc=True, omit_tests=True,
                    omit_binary=True, omit_scenario=True)
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['verdict'] == DEFERRED
    assert set(result['checks'].values()) == {DEFERRED}
    _assert_unopened(result, 'gate_doc: absent')
    assert all(result['criteria'][name] == 'missing' for name in CRITERIA)


def test_current_no_go_doc_keeps_gate_deferred(tmp_path):
    """The real gate state (NO-GO, R0/B1/G1-G6 unchecked) stays DEFERRED."""
    paths = _bundle(tmp_path / 'current', omit_binary=True, omit_scenario=True)
    code, result = _run(paths)
    assert code != 0
    assert result['verdict'] == DEFERRED
    assert result['declared_state'] == 'NO-GO'
    assert result['checks']['gate_doc'] == DEFERRED
    assert result['checks']['gate_tests'] == PASS
    for name in CRITERIA:
        assert result['criteria'][name] == 'unchecked'
    _assert_unopened(result, 'gate_doc: criterion R0 unchecked')
    for name in ('R0', 'B1', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6'):
        assert any(f'criterion {name} unchecked' in reason
                   for reason in result['reasons'])


def test_committed_gate_doc_parses_as_no_go(tmp_path):
    """The committed feasibility doc really is NO-GO with unmet criteria."""
    assert GATE_DOC.is_file(), f'missing gate document: {GATE_DOC}'
    declared, criteria, is_gate = _parse_gate_doc(
        GATE_DOC.read_text(encoding='utf-8'))
    assert is_gate is True
    assert declared == 'NO-GO'
    for name in CRITERIA:
        assert criteria[name] == 'unchecked', (name, criteria[name])

    # Running the tool against the real doc also stays DEFERRED.
    evidence = {
        'schema': 'gamerecomp.win64-gate-summary.v1',
        'issue': 13,
        'gate_doc': {'path': str(GATE_DOC), 'sha256': _sha(GATE_DOC)},
    }
    evidence_path = tmp_path / 'real-evidence.json'
    _json(evidence_path, evidence)
    paths = {'evidence': evidence_path, 'json_out': tmp_path / 'real.json'}
    code, result = _run(paths)
    assert code != 0
    assert result['verdict'] == DEFERRED
    _assert_unopened(result, 'gate_doc: declared state is NO-GO')


# --- flags and unchecked boxes never open the gate --------------------------

def test_flags_only_evidence_is_blocked(tmp_path):
    paths = _bundle(tmp_path / 'flags', flags_only=True)
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['verdict'] == BLOCKED
    assert set(result['checks'].values()) == {BLOCKED}
    assert sum('flags are not evidence' in reason
               for reason in result['reasons']) == len(ITEMS)
    _assert_unopened(result, 'flags are not evidence')


def test_true_support_flag_is_not_tested(tmp_path):
    """A bare `supported: true` flag cannot stand in for evidence."""
    paths = _bundle(tmp_path / 'support-flag', extra={'supported': True})
    code, result = _run(paths)
    assert code != 0
    assert result['verdict'] == NOT_TESTED
    _assert_unopened(result, "flag 'supported' is true")


def test_all_boxes_checked_without_real_binary_is_deferred(tmp_path):
    """A flags-only doc (all boxes ticked) cannot open the gate by itself."""
    paths = _bundle(tmp_path / 'ticked', checked=CRITERIA,
                    declared_state='GO', omit_binary=True, omit_scenario=True)
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['verdict'] == DEFERRED
    assert result['declared_state'] == 'GO'
    assert all(result['criteria'][name] == 'checked' for name in CRITERIA)
    assert result['checks']['gate_doc'] == PASS
    assert result['checks']['native_binary'] == DEFERRED
    assert result['checks']['executed_scenario'] == DEFERRED
    _assert_unopened(result, 'native_binary: absent')


def test_all_checked_but_still_no_go_doc_is_blocked(tmp_path):
    paths = _bundle(tmp_path / 'inconsistent', checked=CRITERIA,
                    declared_state='NO-GO')
    code, result = _run(paths)
    assert code != 0
    assert result['verdict'] == BLOCKED
    assert result['checks']['gate_doc'] == BLOCKED
    _assert_unopened(result, 'declared state is still NO-GO')


# --- tamper detection -------------------------------------------------------

@pytest.mark.parametrize('flag,needle', [
    ('tamper_doc', 'gate_doc: hash mismatch'),
    ('tamper_tests', 'gate_tests: hash mismatch'),
    ('tamper_binary', 'native_binary: hash mismatch'),
    ('tamper_scenario', 'executed_scenario: hash mismatch'),
])
def test_tampered_hashes_are_blocked(tmp_path, flag, needle):
    paths = _bundle(tmp_path / flag, checked=CRITERIA, declared_state='GO',
                    **{flag: True})
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['verdict'] == BLOCKED
    assert result['checks'][needle.split(':')[0]] == BLOCKED
    _assert_unopened(result, needle)


def test_scenario_bound_to_wrong_binary_is_blocked(tmp_path):
    paths = _bundle(tmp_path / 'bad-binding', checked=CRITERIA,
                    declared_state='GO',
                    scenario_overrides={'built_binary_sha256': 'f' * 64})
    code, result = _run(paths)
    assert code != 0
    assert result['verdict'] == BLOCKED
    assert result['checks']['executed_scenario'] == BLOCKED
    _assert_unopened(result, 'built_binary_sha256 does not match')


@pytest.mark.parametrize('kind,needle', [
    ('text', 'unrecognised format'),
    ('elf32', 'ELF is not 64-bit'),
])
def test_non_x64_binary_is_blocked(tmp_path, kind, needle):
    paths = _bundle(tmp_path / kind, checked=CRITERIA, declared_state='GO',
                    binary_kind=kind)
    code, result = _run(paths)
    assert code != 0
    assert result['verdict'] == BLOCKED
    assert result['checks']['native_binary'] == BLOCKED
    _assert_unopened(result, needle)


def test_invalid_scenario_record_is_blocked(tmp_path):
    for name, overrides in (
        ('no-oracle', {'oracle': 'self-reported'}),
        ('bad-exit', {'exit_code': 3}),
        ('no-host', {'host': 'none'}),
        ('no-verdict', {'verdict': 'COMPLIE_ONLY'}),
        ('no-binding', {'built_binary_sha256': 'not-a-hash'}),
    ):
        paths = _bundle(tmp_path / name, checked=CRITERIA,
                        declared_state='GO', scenario_overrides=overrides)
        code, result = _run(paths)
        assert code != 0, (name, result)
        assert result['verdict'] == BLOCKED, (name, result)
        assert result['checks']['executed_scenario'] == BLOCKED, (name, result)


@pytest.mark.parametrize('overrides', [
    {'tests': {**{name: 'passed' for name in REQUIRED_TESTS[:-1]},
               REQUIRED_TESTS[-1]: 'failed'}},
    {'tests': {name: 'passed' for name in REQUIRED_TESTS[:-1]},
     'failed': 0, 'errors': 0},
    {'tests': {name: 'passed' for name in REQUIRED_TESTS}, 'failed': 2},
    {'test_file': 'tests/test_other.py',
     'tests': {name: 'passed' for name in REQUIRED_TESTS}},
])
def test_failed_or_missing_gate_tests_are_blocked(tmp_path, overrides):
    paths = _bundle(tmp_path / 'bad-tests', checked=CRITERIA,
                    declared_state='GO', test_overrides=overrides)
    code, result = _run(paths)
    assert code != 0
    assert result['verdict'] == BLOCKED
    assert result['checks']['gate_tests'] == BLOCKED
    _assert_unopened(result, 'gate_tests:')


def test_not_the_gate_doc_is_blocked(tmp_path):
    paths = _bundle(tmp_path / 'wrong-doc', checked=CRITERIA,
                    declared_state='GO')
    paths['doc'].write_text('# unrelated notes\nStatus: **GO**\n',
                            encoding='utf-8')
    # reseal the doc hash so only the wrong-document check can fire.
    doc_row = json.loads(paths['evidence'].read_text(encoding='utf-8'))
    doc_row['gate_doc']['sha256'] = _sha(paths['doc'])
    _json(paths['evidence'], doc_row)
    code, result = _run(paths)
    assert code != 0
    assert result['verdict'] == BLOCKED
    _assert_unopened(result, 'not the X64 lifter feasibility gate document')


# --- only complete, real evidence opens the gate ----------------------------

def test_complete_evidence_is_closable(tmp_path):
    paths = _bundle(tmp_path / 'complete', checked=CRITERIA,
                    declared_state='GO')
    code, result = _run(paths, cli=True)
    assert code == 0
    assert result['verdict'] == CLOSABLE
    assert result['gate_open'] is True
    assert result['issue_closed'] is False
    assert result['supported_claim'] is None
    assert set(result['checks'].values()) == {PASS}
    assert result['reasons'] == []
    assert all(result['criteria'][name] == 'checked' for name in CRITERIA)


@pytest.mark.parametrize('kind', ['elf64', 'macho64', 'pe32plus'])
def test_all_real_x64_binary_formats_open_the_gate(tmp_path, kind):
    paths = _bundle(tmp_path / kind, checked=CRITERIA, declared_state='GO',
                    binary_kind=kind)
    code, result = _run(paths)
    assert code == 0
    assert result['verdict'] == CLOSABLE


# --- fail-closed CLI handling and no side effects ---------------------------

def test_malformed_cli_input_is_not_tested(tmp_path):
    paths = _bundle(tmp_path / 'malformed')
    paths['evidence'].write_text('{not json', encoding='utf-8')
    code, result = _run(paths, cli=True)
    assert code != 0
    assert result['verdict'] == NOT_TESTED
    assert all(value == NOT_TESTED for value in result['checks'].values())
    assert any('INVALID_JSON' in reason for reason in result['reasons'])

    missing = _bundle(tmp_path / 'missing')
    missing['evidence'].unlink()
    code, result = _run(missing)
    assert code != 0
    assert result['verdict'] == NOT_TESTED
    assert any('MISSING_INPUT' in reason for reason in result['reasons'])

    wrong_shape = _bundle(tmp_path / 'shape')
    _json(wrong_shape['evidence'], ['gate_doc', True])
    code, result = _run(wrong_shape)
    assert code != 0
    assert result['verdict'] == NOT_TESTED
    assert any('INVALID_STRUCTURE' in reason for reason in result['reasons'])


def test_wrong_schema_or_issue_is_not_tested(tmp_path):
    for name, override in (('schema', {'schema': 'wrong'}),
                           ('issue', {'issue': 999})):
        paths = _bundle(tmp_path / name, checked=CRITERIA,
                        declared_state='GO', extra=override)
        code, result = _run(paths)
        assert code != 0, (name, result)
        assert result['verdict'] == NOT_TESTED, (name, result)
        _assert_unopened(result, 'evidence:')


def test_evaluate_is_pure_over_the_supplied_document(tmp_path):
    paths = _bundle(tmp_path / 'pure', checked=CRITERIA, declared_state='GO')
    doc = json.loads(paths['evidence'].read_text(encoding='utf-8'))
    direct = evaluate(doc, paths['evidence'].parent)
    code, via_cli = _run(paths)
    assert code == 0
    assert direct == via_cli


def test_no_issue_closure_or_support_claim_or_closing_keywords(tmp_path):
    """Every verdict, including CLOSABLE, stays side-effect and claim free."""
    for name, kwargs in (
        ('deferred', {}),
        ('blocked', {'checked': CRITERIA, 'declared_state': 'GO',
                     'tamper_binary': True}),
        ('closable', {'checked': CRITERIA, 'declared_state': 'GO'}),
    ):
        paths = _bundle(tmp_path / name, **kwargs)
        _, result = _run(paths)
        assert result['issue_closed'] is False
        assert result['supported_claim'] is None
        text = json.dumps(result)
        assert '#13' not in text or 'close' not in text.lower()
        assert 'close #' not in text.lower()
        assert 'resolve #' not in text.lower()


if __name__ == '__main__':
    pytest.main([__file__])
