#!/usr/bin/env python3
"""Fail-closed summary of the PE32+ / Win64 feasibility gate (issue #13).

This is a *decision tool*, not an implementation, not a x64 backend and not an
issue-closure action. It reads exactly one CLI-supplied JSON evidence document
and joins three independent evidence sources:

  1. ``gate_doc``   - the ``docs/feasibility/X64_LIFTER_GATE.md`` markdown. Its
     Status line, required sections and the eleven §5 go/no-go checkboxes are
     parsed from the file bytes (R0, U1, U2, B1, G1-G6, N1).
  2. ``gate_tests`` - the recorded results of ``tests/test_x64_gate.py``. Every
     named guardrail test must be present with outcome ``passed``.
  3. ``native_binary`` + ``executed_scenario`` - a hash-checked, structurally
     real 64-bit x86 native binary and a hash-checked JSON record of a scenario
     that actually ran against that binary (named oracle/host, exit code 0).

Every digest is recomputed from the bytes on disk; no supplied verdict, PASS
flag or checkbox is copied as evidence. The only positive verdict is CLOSABLE,
which means "the supplied evidence satisfies the feasibility gate" - it is
permission, not an action. This tool never closes an issue, never claims x64
game support (``supported_claim`` is always null, ``issue_closed`` always
false) and emits no GitHub auto-closing keyword.

The repository's current gate state is NO-GO / DEFERRED: R0, B1 and G1-G6 are
unchecked, so a doc that only asserts support (or ticks boxes without real
built-and-executed x64 evidence) can never reach CLOSABLE here.

Input schema (all paths are resolved from this CLI-supplied document; relative
paths resolve against the document's own directory)::

    {
      "schema": "gamerecomp.win64-gate-summary.v1",   # optional, checked
      "issue": 13,                                   # optional, checked
      "gate_doc":           {"path": "<md>",   "sha256": "<64 hex>"},
      "gate_tests":         {"path": "<json>", "sha256": "<64 hex>"},
      "native_binary":      {"path": "<file>", "sha256": "<64 hex>"},
      "executed_scenario":  {"path": "<json>", "sha256": "<64 hex>"}
    }

An artifact row may be omitted (roadmap work not supplied => DEFERRED). A row
that is a boolean/flag or any value other than a path/hash object is BLOCKED:
flags are never evidence.

``gate_tests`` file body::

    {"test_file": "tests/test_x64_gate.py",
     "tests": {"test_gate_doc_exists_with_required_sections": "passed", ...},
     "failed": 0, "errors": 0}

``executed_scenario`` file body::

    {"verdict": "PASS", "oracle": "<named oracle>", "scenario": "<name>",
     "host": "<named host>", "exit_code": 0,
     "built_binary_sha256": "<digest of the native binary>"}

Usage::

    python3 tools/win64_gate_summary.py --evidence evidence.json
    python3 tools/win64_gate_summary.py --evidence evidence.json --json verdict.json

Exit status: 0 = CLOSABLE, 3 = DEFERRED / BLOCKED / NOT TESTED, 2 = CLI error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any

SCHEMA = 'gamerecomp.win64-gate-summary.v1'
GENERATOR = 'tools/win64_gate_summary.py'
GATE = 'WIN64_FEASIBILITY_GATE_ONLY_AFTER_REAL_X64_EVIDENCE'
ISSUE = 13

DEFERRED = 'DEFERRED'
BLOCKED = 'BLOCKED'
NOT_TESTED = 'NOT TESTED'
CLOSABLE = 'CLOSABLE'
# A passing per-source check is not itself a public verdict.
PASS = 'PASS'

ITEMS = ('gate_doc', 'gate_tests', 'native_binary', 'executed_scenario')
CRITERIA = ('R0', 'U1', 'U2', 'B1', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'N1')
REQUIRED_SECTIONS = (
    '# X64 lifter feasibility gate (Issue #148)',
    '## 1. Scope and fail-closed refusal boundary',
    '## 2. Inspect local upstream checkouts (exact commands)',
    '## 3. Licence constraints',
    '## 4. Benchmark a representative x64 function through a candidate lifter',
    '## 5. Go/no-go checklist',
    '## 6. No x64 support claim without a built and executed native binary',
    '## 7. Reproduce the refusal (exact commands and expected output)',
    '## 8. Limits of this gate',
)
REQUIRED_TESTS = (
    'test_gate_doc_exists_with_required_sections',
    'test_gate_doc_contains_exact_refusal_strings',
    'test_gate_doc_contains_no_claim_without_binary_rule',
    'test_x64_auto_arch_is_refused_named_and_nonzero',
    'test_forcing_x86_on_x64_is_refused',
    'test_binaryless_x64_project_is_refused_by_auditor',
    'test_x64_input_never_yields_a_native_binary',
)

RULE = (
    'CLOSABLE requires all eleven §5 criteria checked in the gate doc, passing '
    'test_x64_gate.py results, and a hash-checked real x64 native binary with '
    'an oracle-backed executed scenario bound to that binary; the current '
    'NO-GO state, flags, unchecked criteria and compile-only artifacts never '
    'open the gate'
)
LIMITS = (
    'The tool reads only the CLI-supplied evidence document and the files it '
    'names. It recomputes every SHA-256 from those bytes and copies no supplied '
    'verdict or PASS flag.',
    'CLOSABLE means the supplied evidence satisfies the feasibility gate. It is '
    'permission, not an action: no issue is closed by this tool and no GitHub '
    'auto-closing keyword is emitted.',
    'No general x64 support is claimed. Even CLOSABLE covers only the exact '
    'supplied binary and scenario; supported_claim is always null.',
    'A SHA-256 proves byte identity, not that a §5 criterion artifact is '
    'actually committed, nor semantic correctness of a lifter, ABI or runtime.',
    'When the gate doc declares NO-GO / DEFERRED the verdict is DEFERRED even '
    'if other sources are complete; a doc cannot open its own gate.',
)

_HEX = frozenset('0123456789abcdefABCDEF')
_NO_NAME = frozenset({'', 'none', 'missing', 'n/a', 'na', 'unproven', 'partial',
                      'not tested', 'self-reported', 'compile-only',
                      'compile only', NOT_TESTED.lower()})
_FALSE_FLAGS = ('supported', 'x64_supported', 'gate_open', 'closable',
                'issue_closed', 'supported_flag')
_CRITERION_RE = re.compile(r'^\s*[-*]\s*\[([ xX])\]\s*\*\*\s*([A-Z][0-9])\b')
_STATUS_RE = re.compile(r'^\s*Status:\s*(?:\*\*)?\s*([^\n*]+)', re.MULTILINE)
# Closing verbs followed by an issue reference must never be emitted.
_CLOSING_RE = re.compile(
    r'\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:#\d+|gh-\d+)',
    re.IGNORECASE,
)
_MACH_O_64 = {b'\xcf\xfa\xed\xfe': '<', b'\xfe\xed\xfa\xcf': '>'}
_EM_X86_64 = 62
_CPU_TYPE_X86_64 = 0x01000007
_MACHINE_AMD64 = 0x8664
_PE32PLUS_MAGIC = 0x20B


class LoadError(Exception):
    """A CLI input is absent, unreadable or not a JSON object."""


def sha256(path: Path) -> str:
    """Streaming SHA-256 of a file; raises OSError when it cannot be read."""
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path_text: str | None) -> tuple[dict, Path | None]:
    """Parse the one CLI-supplied JSON object; return ``(doc, base_dir)``."""
    if not path_text:
        raise LoadError('MISSING_INPUT: no --evidence path supplied')
    path = Path(path_text)
    if not path.is_file():
        raise LoadError(f'MISSING_INPUT: {path}')
    try:
        raw = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        raise LoadError(f'UNREADABLE_INPUT: {path}: {exc}') from exc
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise LoadError(f'INVALID_JSON: {path}: {exc}') from exc
    if not isinstance(doc, dict):
        raise LoadError(f'INVALID_STRUCTURE: {path}: expected a JSON object')
    return doc, path.resolve().parent


def _resolve(text: Any, base: Path | None) -> Path | None:
    """Resolve one supplied path; relative paths need the document's base dir."""
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = Path(text.strip())
    if not candidate.is_absolute():
        if base is None:
            return None
        candidate = base / candidate
    return candidate


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in _HEX for char in value)


def _named(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in _NO_NAME


def _checked_file(row: Any, label: str, base: Path | None,
                  reasons: list[str], absent_note: str):
    """Hash-check one artifact row; return ``(status, digest, bytes)``.

    An absent row is DEFERRED (roadmap work not supplied); a present row that
    is not a real, hash-matching file is BLOCKED. No fallback path is invented
    and no digest is computed in place of a missing declaration.
    """
    if row is None or (isinstance(row, (dict, list)) and not row):
        reasons.append(f'{label}: absent ({absent_note})')
        return DEFERRED, None, None
    if not isinstance(row, dict):
        reasons.append(f'{label}: flag/value is not a path/hash object '
                       '(flags are not evidence)')
        return BLOCKED, None, None
    path = _resolve(row.get('path'), base)
    if path is None:
        reasons.append(f'{label}: no artifact path declared')
        return BLOCKED, None, None
    declared = row.get('sha256')
    if not _is_hash(declared):
        reasons.append(f'{label}: invalid declared sha256')
        return BLOCKED, None, None
    if not path.is_file():
        reasons.append(f'{label}: artifact file is missing: {path.name}')
        return BLOCKED, None, None
    try:
        actual = sha256(path)
        data = path.read_bytes()
    except OSError:
        reasons.append(f'{label}: artifact file is unreadable: {path.name}')
        return BLOCKED, None, None
    if actual.lower() != declared.lower():
        reasons.append(f'{label}: hash mismatch (declared {declared[:12]}..., '
                       f'recomputed {actual[:12]}...): {path.name}')
        return BLOCKED, None, None
    if not data:
        reasons.append(f'{label}: artifact file is empty: {path.name}')
        return BLOCKED, None, None
    return PASS, actual, data


def _parse_gate_doc(text: str) -> tuple[str | None, dict[str, str | None], bool]:
    """Extract the declared state, §5 checkbox states and document identity."""
    criteria: dict[str, str | None] = {name: None for name in CRITERIA}
    for line in text.splitlines():
        match = _CRITERION_RE.match(line)
        if match:
            name = match.group(2).upper()
            if name in criteria:
                criteria[name] = 'checked' if match.group(1).lower() == 'x' \
                    else 'unchecked'
    declared: str | None = None
    match = _STATUS_RE.search(text)
    if match:
        state = match.group(1).strip().upper()
        if 'NO-GO' in state or 'DEFERRED' in state:
            declared = 'NO-GO'
        elif 'GO' in state:
            declared = 'GO'
        else:
            declared = state
    is_gate = all(section in text for section in REQUIRED_SECTIONS)
    return declared, criteria, is_gate


def _check_gate_doc(row: Any, base: Path | None, reasons: list[str]):
    """Parse the feasibility gate markdown and report its go/no-go state."""
    status, _, data = _checked_file(
        row, 'gate_doc', base, reasons, 'no feasibility gate document supplied')
    if status != PASS:
        return status, None, None
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        reasons.append('gate_doc: document is not UTF-8 text')
        return BLOCKED, None, None
    declared, criteria, is_gate = _parse_gate_doc(text)
    if not is_gate:
        reasons.append('gate_doc: not the X64 lifter feasibility gate document '
                       '(missing required sections)')
        return BLOCKED, declared, criteria
    unmet = [name for name in CRITERIA if criteria.get(name) != 'checked']
    if declared is None:
        reasons.append('gate_doc: no Status line declaring GO or NO-GO')
        return BLOCKED, declared, criteria
    if unmet:
        reasons.append(f'gate_doc: declared state is {declared} / DEFERRED '
                       '(gate open and unmet)')
        for name in unmet:
            reasons.append(f'gate_doc: criterion {name} unchecked '
                           '(feasibility gate unmet)')
        return DEFERRED, declared, criteria
    if declared == 'NO-GO':
        reasons.append('gate_doc: all criteria checked but declared state is '
                       'still NO-GO / DEFERRED (inconsistent evidence)')
        return BLOCKED, declared, criteria
    if declared != 'GO':
        reasons.append(f'gate_doc: declared state {declared!r} is not GO')
        return BLOCKED, declared, criteria
    return PASS, declared, criteria


def _normalise_test_results(doc: Any) -> dict[str, str]:
    """Return ``{test_name: outcome}`` from a supported results body."""
    tests = doc.get('tests')
    if isinstance(tests, dict):
        return {str(name): str(outcome).lower()
                for name, outcome in tests.items()}
    if isinstance(tests, list):
        out: dict[str, str] = {}
        for item in tests:
            if not isinstance(item, dict):
                continue
            name = item.get('name', item.get('nodeid', item.get('test')))
            if not isinstance(name, str) or not name:
                continue
            if '::' in name:
                name = name.rsplit('::', 1)[-1]
            outcome = item.get('outcome', item.get('result', item.get('status')))
            out[name] = str(outcome).lower() if outcome is not None else 'missing'
        return out
    return {}


def _check_gate_tests(row: Any, base: Path | None, reasons: list[str]):
    """Validate the recorded test_x64_gate.py results."""
    status, _, data = _checked_file(
        row, 'gate_tests', base, reasons, 'no test_x64_gate.py results supplied')
    if status != PASS:
        return status
    try:
        doc = json.loads(data.decode('utf-8'))
    except (UnicodeError, ValueError):
        reasons.append('gate_tests: results are not valid JSON')
        return BLOCKED
    if not isinstance(doc, dict):
        reasons.append('gate_tests: results are not a JSON object')
        return BLOCKED
    test_file = doc.get('test_file', doc.get('test_path'))
    if not isinstance(test_file, str) or not test_file.strip().endswith(
            'test_x64_gate.py'):
        reasons.append('gate_tests: test_file is not test_x64_gate.py')
        return BLOCKED
    outcomes = _normalise_test_results(doc)
    bad = False
    for name in REQUIRED_TESTS:
        if name not in outcomes:
            reasons.append(f'gate_tests: required test {name} is missing')
            bad = True
        elif outcomes[name] != 'passed':
            reasons.append(f'gate_tests: test {name} outcome is '
                           f'{outcomes[name]!r}, expected passed')
            bad = True
    for key in ('failed', 'errors'):
        value = doc.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            reasons.append(f'gate_tests: {key} count is {value}, expected 0')
            bad = True
    return BLOCKED if bad else PASS


def _x64_info(data: bytes) -> tuple[str | None, str]:
    """Identify a 64-bit x86 native binary without executing it."""
    if len(data) < 24:
        return None, 'binary too small for a native header'
    if data[:4] == b'\x7fELF':
        if data[4] != 2:
            return None, f'ELF is not 64-bit (EI_CLASS={data[4]})'
        endian = '<' if data[5] == 1 else '>'
        machine = struct.unpack_from(endian + 'H', data, 18)[0]
        if machine != _EM_X86_64:
            return None, f'ELF machine {machine} is not EM_X86_64'
        return 'elf64-x86-64', ''
    endian = _MACH_O_64.get(data[:4])
    if endian is not None:
        cputype = struct.unpack_from(endian + 'i', data, 4)[0]
        if cputype != _CPU_TYPE_X86_64:
            return None, f'Mach-O cputype {cputype:#x} is not x86-64'
        return 'macho64-x86-64', ''
    if data[:2] == b'MZ':
        pe_off = struct.unpack_from('<I', data, 0x3C)[0]
        if pe_off < 0x40 or pe_off + 24 > len(data):
            return None, f'invalid PE e_lfanew ({pe_off})'
        if data[pe_off:pe_off + 4] != b'PE\x00\x00':
            return None, 'PE signature missing'
        machine = struct.unpack_from('<H', data, pe_off + 4)[0]
        if machine != _MACHINE_AMD64:
            return None, f'PE machine 0x{machine:04x} is not AMD64'
        magic = struct.unpack_from('<H', data, pe_off + 24)[0]
        if magic != _PE32PLUS_MAGIC:
            return None, f'PE optional magic 0x{magic:04x} is not PE32+'
        return 'pe32+-amd64', ''
    return None, 'unrecognised format (not ELF64, Mach-O 64-bit or PE32+)'


def _check_native_binary(row: Any, base: Path | None, reasons: list[str]):
    """Hash-check and structurally validate the built x64 binary."""
    status, digest, data = _checked_file(
        row, 'native_binary', base, reasons, 'no built x64 binary supplied')
    if status != PASS:
        return status, None, None
    fmt, err = _x64_info(data)
    if err:
        reasons.append(f'native_binary: not a real x64 native binary: {err}')
        return BLOCKED, None, None
    return PASS, digest, fmt


def _check_scenario(row: Any, base: Path | None, binary_digest: str | None,
                    reasons: list[str]):
    """Hash-check the executed-scenario record and bind it to the binary."""
    status, _, data = _checked_file(
        row, 'executed_scenario', base, reasons,
        'no executed x64 scenario supplied')
    if status != PASS:
        return status
    try:
        doc = json.loads(data.decode('utf-8'))
    except (UnicodeError, ValueError):
        reasons.append('executed_scenario: record is not valid JSON')
        return BLOCKED
    if not isinstance(doc, dict):
        reasons.append('executed_scenario: record is not a JSON object')
        return BLOCKED
    bad = False
    if doc.get('verdict') != PASS:
        reasons.append('executed_scenario: verdict is not PASS')
        bad = True
    for key in ('oracle', 'scenario', 'host'):
        if not _named(doc.get(key)):
            reasons.append(f'executed_scenario: no named {key} '
                           '(scenario not shown to run)')
            bad = True
    exit_code = doc.get('exit_code')
    if (not isinstance(exit_code, int) or isinstance(exit_code, bool)
            or exit_code != 0):
        reasons.append('executed_scenario: scenario did not exit successfully')
        bad = True
    bound = doc.get('built_binary_sha256')
    if not _is_hash(bound):
        reasons.append('executed_scenario: no built_binary_sha256 binding')
        bad = True
    elif binary_digest is None:
        reasons.append('executed_scenario: no proven native binary to bind to')
        bad = True
    elif bound.lower() != binary_digest.lower():
        reasons.append('executed_scenario: built_binary_sha256 does not match '
                       'the native binary')
        bad = True
    return BLOCKED if bad else PASS


def _decision(status: str, checks: dict, reasons: list[str],
              declared: str | None = None,
              criteria: dict | None = None) -> dict:
    result = {
        'schema': SCHEMA,
        'generator': GENERATOR,
        'gate': GATE,
        'verdict': status,
        'gate_open': status == CLOSABLE,
        'issue_closed': False,
        'supported_claim': None,
        'checks': {name: checks.get(name, NOT_TESTED) for name in ITEMS},
        'declared_state': declared,
        'criteria': {name: (criteria or {}).get(name, 'missing')
                     for name in CRITERIA},
        'reasons': list(reasons),
        'reason_codes': list(dict.fromkeys(
            reason.split(':', 1)[0] for reason in reasons)),
        'rule': RULE,
        'limits': list(LIMITS),
    }
    text = json.dumps(result, sort_keys=True)
    if _CLOSING_RE.search(text):
        result['verdict'] = BLOCKED
        result['gate_open'] = False
        result['reasons'] = ['INTERNAL_GUARD: banned closing keyword in report']
        result['reason_codes'] = ['INTERNAL_GUARD']
    return result


def evaluate(evidence: dict, base: Path | None = None) -> dict:
    """Pure decision function over the supplied document and named files."""
    reasons: list[str] = []
    if evidence.get('schema', SCHEMA) != SCHEMA:
        reasons.append('evidence: schema mismatch')
    if evidence.get('issue', ISSUE) != ISSUE:
        reasons.append(f'evidence: issue is not #{ISSUE}')
    for flag in _FALSE_FLAGS:
        if evidence.get(flag) is True:
            reasons.append(f'evidence: flag {flag!r} is true; flags are not '
                           'evidence and cannot open the gate')
    if reasons:
        return _decision(NOT_TESTED, {}, reasons)

    checks: dict[str, str] = {}
    doc_status, declared, criteria = _check_gate_doc(
        evidence.get('gate_doc'), base, reasons)
    checks['gate_doc'] = doc_status
    checks['gate_tests'] = _check_gate_tests(
        evidence.get('gate_tests'), base, reasons)
    bin_status, digest, _ = _check_native_binary(
        evidence.get('native_binary'), base, reasons)
    checks['native_binary'] = bin_status
    checks['executed_scenario'] = _check_scenario(
        evidence.get('executed_scenario'), base, digest, reasons)

    if BLOCKED in checks.values():
        status = BLOCKED
    elif DEFERRED in checks.values():
        status = DEFERRED
    elif all(checks.get(name) == PASS for name in ITEMS):
        status = CLOSABLE
    else:
        status = NOT_TESTED
    return _decision(status, checks, reasons, declared, criteria)


def _emit(result: dict, out_path: str | None) -> None:
    text = json.dumps(result, indent=1, sort_keys=True) + '\n'
    if out_path:
        Path(out_path).write_text(text, encoding='utf-8')
    print(text, end='')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--evidence', required=True,
                        help='path to the CLI-supplied evidence JSON object')
    parser.add_argument('--json', dest='json_out',
                        help='write the decision JSON here')
    args = parser.parse_args(argv)
    try:
        doc, base = _load(args.evidence)
        result = evaluate(doc, base)
    except LoadError as exc:
        message = str(exc)
        print(message, file=sys.stderr)
        result = _decision(NOT_TESTED, {}, [message])
    try:
        _emit(result, args.json_out)
    except OSError as exc:
        print(f'CANNOT_WRITE_DECISION: {args.json_out}: {exc}', file=sys.stderr)
        return 2
    return 0 if result['verdict'] == CLOSABLE else 3


if __name__ == '__main__':
    sys.exit(main())
