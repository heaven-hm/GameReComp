#!/usr/bin/env python3
"""Fail-closed DEFERRED gate for x64 build/run support claims (issue #13).

This is a *decision tool*, not an implementation and not an x64 backend. It
reads exactly one CLI-supplied JSON evidence document and decides whether a
narrow, scoped SUPPORTED claim is justified by the evidence in that document:

  * ``built_native_binary`` - a hash-checked, *structurally* real x64 native
    binary (ELF64 x86-64, Mach-O 64-bit x86-64 or PE32+ AMD64);
  * ``executed_scenario``  - a hash-checked JSON proof that a named scenario
    really ran on a named host, exited successfully and names a trusted oracle.

The scenario proof must bind to the *actual* digest of the built binary via
``built_binary_sha256``. Booleans, self-reported verdicts and a compile-only
result are never evidence: a built binary with no executed scenario is
DEFERRED, not SUPPORTED, and an explicit compile-only marker is DEFERRED even
if both artifacts are present. A correct hash of a non-x64 or non-binary file
is BLOCKED, as is any declared digest that does not match the file bytes.

Missing capabilities are DEFERRED (roadmap work not supplied); supplied but
invalid or tampered capabilities are BLOCKED; malformed/missing CLI inputs are
NOT TESTED. FAIL/BLOCKED/NOT TESTED/DEFERRED/SUPPORTED stay distinct.

A SUPPORTED verdict means only "the supplied binary hash ran in the supplied
scenario on the supplied host". The tool never closes an issue
(``issue_closed`` is always False) and never generalises to broad x64 support.

Input schema (all paths are resolved only from this CLI-supplied document):

  {
    "issue": 13,                           # optional, checked when present
    "direction": "x64-native-build-run",   # optional, checked when present
    "built_native_binary": {"path": "<file>", "sha256": "<64 hex>"},
    "executed_scenario":   {"path": "<file>", "sha256": "<64 hex>"}
  }

  ``built_native_binary`` may be absent (a compile-only result). When present
  its file must be a real x64 native binary and its declared sha256 must match.

  ``executed_scenario``'s file must be JSON containing:
    {"verdict": "PASS", "oracle": "<named oracle>", "scenario": "<name>",
     "host": "<named host>", "exit_code": 0,
     "built_binary_sha256": "<digest of the built native binary>"}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

SUPPORTED = 'SUPPORTED'
DEFERRED = 'DEFERRED'
BLOCKED = 'BLOCKED'
NOT_TESTED = 'NOT TESTED'
# A passing artifact check is not itself the public SUPPORTED verdict.
PASS = 'PASS'

ITEMS = ('built_native_binary', 'executed_scenario')
ISSUE = 13
DIRECTION = 'x64-native-build-run'
RULE = ('SUPPORTED requires a hash-checked real x64 native binary and an '
        'oracle-backed scenario executed on a named host whose binary hash '
        'binds to that exact binary; compile-only results are DEFERRED, never '
        'a support claim')

_HEX = set('0123456789abcdefABCDEF')
_NO_NAME = {'', 'none', 'missing', 'n/a', 'na', 'unproven', 'partial',
            'not tested', 'self-reported', 'compile-only', 'compile only',
            'build-only', 'build only', NOT_TESTED.lower()}
_COMPILE_ONLY = {'compile_only', 'compile-only', 'compile only', 'build_only',
                 'build-only', 'build only', 'compiled-only', 'compiled only',
                 'source-only', 'source only'}
_COMPILE_ONLY_KEYS = ('verdict', 'status', 'build_status', 'result')
_MACH_O_64 = {b'\xcf\xfa\xed\xfe': '<', b'\xfe\xed\xfa\xcf': '>'}
_CPU_TYPE_X86_64 = 0x01000007
_EM_X86_64 = 62
_MACHINE_AMD64 = 0x8664


class LoadError(Exception):
    """A CLI input is absent, unreadable or not a JSON object."""


def sha256(path: Path) -> str:
    """Streaming SHA-256 of a file; raises OSError when it cannot be read."""
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path_text: str | None) -> dict:
    """Parse one CLI-supplied JSON object; raise LoadError instead of guessing."""
    if not path_text:
        raise LoadError('MISSING_INPUT: no --evidence path supplied')
    path = Path(path_text)
    if not path.is_file():
        raise LoadError(f'MISSING_INPUT: {path}')
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise LoadError(f'INVALID_JSON: {path}: {exc}') from exc
    if not isinstance(doc, dict):
        raise LoadError(f'INVALID_STRUCTURE: {path}: expected a JSON object')
    return doc


def _is_hash(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in _HEX for ch in value))


def _named(value) -> bool:
    return isinstance(value, str) and value.strip().lower() not in _NO_NAME


def _compile_only_marker(evidence: dict) -> str | None:
    """Return the exact compile-only marker carried by the evidence, if any."""
    if evidence.get('compile_only') is True or evidence.get('build_only') is True:
        return 'compile_only flag is true'
    for key in _COMPILE_ONLY_KEYS:
        value = evidence.get(key)
        if isinstance(value, str) and value.strip().lower() in _COMPILE_ONLY:
            return f'{key}={value}'
    return None


def _checked_file(row, label: str, reasons: list[str], absent_note: str):
    """Validate one artifact row and return ``(status, digest, bytes)``.

    A missing row is DEFERRED (roadmap-only, do-not-claim); a present row that
    is not hash-checked evidence is BLOCKED. No fallback path is invented and
    no digest is computed in place of a missing declaration.
    """
    if row is None or row == {} or row == []:
        reasons.append(f'{label}: absent ({absent_note})')
        return DEFERRED, None, None
    if not isinstance(row, dict):
        reasons.append(f'{label}: flag/value is not hash-checked evidence')
        return BLOCKED, None, None
    text = row.get('path')
    if not isinstance(text, str) or not text.strip():
        reasons.append(f'{label}: no artifact path declared')
        return BLOCKED, None, None
    path = Path(text)
    declared = row.get('sha256')
    if not _is_hash(declared):
        reasons.append(f'{label}: invalid declared sha256')
        return BLOCKED, None, None
    if not path.is_file():
        reasons.append(f'{label}: artifact missing: {path.name}')
        return BLOCKED, None, None
    try:
        actual = sha256(path)
        data = path.read_bytes()
    except OSError:
        reasons.append(f'{label}: artifact unreadable: {path.name}')
        return BLOCKED, None, None
    if actual.lower() != declared.lower():
        reasons.append(f'{label}: hash mismatch (declared {declared[:12]}..., '
                       f'actual {actual[:12]}...): {path.name}')
        return BLOCKED, None, None
    if not data:
        reasons.append(f'{label}: artifact is empty: {path.name}')
        return BLOCKED, None, None
    return PASS, actual, data


def _binary_info(data: bytes) -> tuple[str | None, str]:
    """Identify a *64-bit x86* native binary without executing it."""
    if len(data) < 24:
        return None, 'binary too small for a native header'
    if data[:4] == b'\x7fELF':
        if data[4] != 2:
            return None, f'ELF is not 64-bit (EI_CLASS={data[4]})'
        endian = '<' if data[5] == 1 else '>'
        if len(data) < 20:
            return None, 'ELF header truncated'
        machine = struct.unpack_from(endian + 'H', data, 18)[0]
        if machine != _EM_X86_64:
            return None, f'ELF machine {machine} is not EM_X86_64 ({_EM_X86_64})'
        return 'elf64-x86-64', ''
    endian = _MACH_O_64.get(data[:4])
    if endian is not None:
        if len(data) < 8:
            return None, 'Mach-O header truncated'
        cputype = struct.unpack_from(endian + 'i', data, 4)[0]
        if cputype != _CPU_TYPE_X86_64:
            return None, (f'Mach-O cputype {cputype:#x} is not '
                          f'CPU_TYPE_X86_64 ({_CPU_TYPE_X86_64:#x})')
        return 'macho64-x86-64', ''
    if data[:2] == b'MZ':
        pe_off = struct.unpack_from('<I', data, 0x3C)[0]
        if pe_off < 64 or pe_off + 24 > len(data):
            return None, f'invalid PE e_lfanew ({pe_off})'
        if data[pe_off:pe_off + 4] != b'PE\x00\x00':
            return None, 'PE signature missing'
        machine = struct.unpack_from('<H', data, pe_off + 4)[0]
        if machine != _MACHINE_AMD64:
            return None, f'PE machine 0x{machine:04x} is not AMD64 (0x8664)'
        opt = pe_off + 24
        if opt + 2 > len(data):
            return None, 'PE optional header truncated'
        magic = struct.unpack_from('<H', data, opt)[0]
        if magic != 0x20B:
            return None, f'PE optional magic 0x{magic:04x} is not PE32+ (0x20b)'
        return 'pe32+-amd64', ''
    return None, 'unrecognised format (not ELF64, Mach-O 64-bit or PE32+)'


def _check_binary(row, reasons: list[str], checks: dict) -> tuple[str | None, str | None]:
    """Validate the built native binary; return ``(digest, format)`` when proven."""
    status, digest, data = _checked_file(
        row, 'built_native_binary', reasons, 'no native binary supplied')
    if status != PASS:
        checks['built_native_binary'] = status
        return None, None
    fmt, err = _binary_info(data)
    if fmt is None:
        reasons.append(f'built_native_binary: not a real x64 native binary: {err}')
        checks['built_native_binary'] = BLOCKED
        return None, None
    checks['built_native_binary'] = PASS
    return digest, fmt


def _check_scenario(row, binary_digest: str | None, reasons: list[str],
                    checks: dict) -> dict | None:
    """Validate the executed-scenario proof; return its scoped fields if proven."""
    status, _, data = _checked_file(
        row, 'executed_scenario', reasons,
        'no executed scenario; compile-only result cannot support a claim')
    if status != PASS:
        checks['executed_scenario'] = status
        return None
    try:
        doc = json.loads(data.decode('utf-8'))
    except (UnicodeError, ValueError):
        reasons.append('executed_scenario: proof is not valid JSON')
        checks['executed_scenario'] = BLOCKED
        return None
    if not isinstance(doc, dict):
        reasons.append('executed_scenario: proof is not an object')
        checks['executed_scenario'] = BLOCKED
        return None
    bad: list[str] = []
    if doc.get('verdict') != 'PASS':
        bad.append('executed_scenario: verdict is not PASS')
    if not _named(doc.get('oracle')):
        bad.append('executed_scenario: no named oracle')
    if not _named(doc.get('scenario')):
        bad.append('executed_scenario: no named scenario')
    if not _named(doc.get('host')):
        bad.append('executed_scenario: no named host (scenario not shown to run)')
    exit_code = doc.get('exit_code')
    if (not isinstance(exit_code, int) or isinstance(exit_code, bool)
            or exit_code != 0):
        bad.append('executed_scenario: scenario did not exit successfully')
    bound = doc.get('built_binary_sha256')
    if not _is_hash(bound):
        bad.append('executed_scenario: no built_binary_sha256 binding')
    elif binary_digest is None:
        bad.append('executed_scenario: no proven binary to bind the scenario to')
    elif bound.lower() != binary_digest.lower():
        bad.append('executed_scenario: built_binary_sha256 does not match the binary')
    if bad:
        reasons.extend(bad)
        checks['executed_scenario'] = BLOCKED
        return None
    checks['executed_scenario'] = PASS
    return {'scenario': doc['scenario'], 'oracle': doc['oracle'], 'host': doc['host']}


def _decision(status: str, checks: dict, reasons: list[str],
              claim: dict | None = None) -> dict:
    return {
        'status': status,
        'supported': status == SUPPORTED,
        'issue_closed': False,
        'checks': {name: checks.get(name, NOT_TESTED) for name in ITEMS},
        'reasons': reasons,
        'rule': RULE,
        'supported_claim': claim,
    }


def evaluate(evidence: dict) -> dict:
    """Pure decision function; reads only paths supplied in the evidence doc."""
    reasons: list[str] = []
    checks: dict[str, str] = {}
    if evidence.get('issue', ISSUE) != ISSUE:
        reasons.append(f'evidence: issue is not #{ISSUE}')
    if evidence.get('direction', DIRECTION) != DIRECTION:
        reasons.append(f'evidence: direction is not {DIRECTION}')
    if reasons:
        return _decision(NOT_TESTED, checks, reasons)

    marker = _compile_only_marker(evidence)
    if marker:
        reasons.append(f'evidence: compile-only result ({marker}) cannot support a claim')

    digest, fmt = _check_binary(evidence.get('built_native_binary'), reasons, checks)
    scoped = _check_scenario(evidence.get('executed_scenario'), digest, reasons, checks)

    if BLOCKED in checks.values():
        status = BLOCKED
    elif marker or DEFERRED in checks.values():
        status = DEFERRED
    elif all(checks.get(name) == PASS for name in ITEMS):
        status = SUPPORTED
    else:
        status = NOT_TESTED

    claim = None
    if status == SUPPORTED and scoped is not None and digest is not None:
        claim = {
            'scope': 'exact-binary-and-scenario-only',
            'binary_sha256': digest,
            'binary_format': fmt,
            'scenario': scoped['scenario'],
            'oracle': scoped['oracle'],
            'host': scoped['host'],
            'note': ('Only the supplied binary hash under the named scenario '
                     'on the declared host is supported; no general x64 '
                     'support is implied.'),
        }
    return _decision(status, checks, reasons, claim)


def _emit(result: dict, out_path: str | None) -> None:
    text = json.dumps(result, indent=1, sort_keys=True) + '\n'
    if out_path:
        Path(out_path).write_text(text, encoding='utf-8')
    print(text, end='')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--evidence', required=True,
                    help='path to the CLI-supplied evidence JSON object')
    ap.add_argument('--json', dest='json_out',
                    help='write the decision JSON here')
    args = ap.parse_args(argv)
    try:
        result = evaluate(_load(args.evidence))
    except LoadError as exc:
        print(str(exc), file=sys.stderr)
        result = _decision(NOT_TESTED, {}, [str(exc)])
    try:
        _emit(result, args.json_out)
    except OSError as exc:
        print(f'CANNOT_WRITE_DECISION: {args.json_out}: {exc}', file=sys.stderr)
        return 3
    return 0 if result['status'] == SUPPORTED else 3


if __name__ == '__main__':
    sys.exit(main())
