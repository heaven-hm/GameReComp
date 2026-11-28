#!/usr/bin/env python3
"""Fail-closed verifier for x64 native execution-evidence records.

The tool reads exactly one CLI-supplied JSON document (``--records FILE``;
``-`` reads stdin). Its ``records`` list holds execution evidence. Each record
must show all six fields:

  * ``pe32plus_input``   - {path, sha256} for a real PE32+ (AMD64) input image;
  * ``native_binary``    - {path, sha256} for a real 64-bit x86 native binary;
  * ``executed_command`` - the command run on the target host;
  * ``exit_code``        - integer 0 (a successful run);
  * ``output``           - {path, sha256} for captured, non-empty run output;
  * ``host_arch``        - the target host architecture, an x86-64 spelling.

Every digest is recomputed from file bytes with streaming SHA-256. The PE32+
input must really be PE32+/AMD64, the native binary must really be ELF64
x86-64, Mach-O 64-bit x86-64 or PE32+/AMD64, the command must name the proven
native binary, the exit code must be zero and the host architecture must be an
x86-64 spelling. Supplied verdict/PASS flags are never copied or trusted.

A SUPPORTED verdict is emitted only when every record passes every check. A
record with a compile-only marker or a compile-only command stays DEFERRED and
is never promoted to a support claim. Invalid, missing or tampered evidence is
BLOCKED; absent/malformed CLI input is NOT TESTED. No issue-state term is
emitted and no issue state is altered by this tool. Paths come only from the
CLI-supplied document (relative paths resolve against that document's directory);
there are no built-in file paths or host-specific defaults. The tool never
executes a command.

Usage::

    python3 tools/x64_run_evidence.py --records evidence.json
    python3 tools/x64_run_evidence.py --records evidence.json --json decision.json

Exit status: 0 = SUPPORTED, 3 = any other verdict, 2 = CLI usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

SUPPORTED = 'SUPPORTED'
DEFERRED = 'DEFERRED'
BLOCKED = 'BLOCKED'
NOT_TESTED = 'NOT TESTED'
PASS = 'PASS'

SCHEMA = 'gamerecomp.x64-run-evidence.v1'
FIELDS = ('pe32plus_input', 'native_binary', 'executed_command',
          'exit_code', 'output', 'host_arch')
RULE = (
    'SUPPORTED requires every record to show all six fields: a real PE32+/AMD64 '
    'input, a real 64-bit x86 native binary, an executed command naming that '
    'binary, exit_code 0, captured output and an x86-64 host_arch; every digest '
    'is recomputed from file bytes. Compile-only records stay DEFERRED, invalid '
    'or tampered evidence is BLOCKED, and absent CLI input is NOT TESTED.'
)

_HEX = frozenset('0123456789abcdefABCDEF')
_X64_ARCHES = frozenset({'x86-64', 'amd64', 'x64', 'x8664', 'intel64', 'em64t'})
_BUILD_TOOLS = frozenset({
    'cc', 'gcc', 'g++', 'clang', 'clang++', 'cl', 'cl.exe', 'msbuild', 'cargo',
    'rustc', 'javac', 'ld', 'lld', 'link', 'make', 'cmake',
})
_NOOP_TOKENS = frozenset({'true', 'echo', ':', 'no-op', 'noop'})
_COMPILE_ONLY_VALUES = frozenset({
    'compile-only', 'compile only', 'compile_only', 'build-only', 'build only',
    'build_only', 'compiled-only', 'compiled only', 'source-only', 'source only',
})
_COMPILE_ONLY_PHRASES = (
    'compile-only', 'compile only', 'compile_only', 'build-only', 'build only',
    'build_only', 'source-only', 'source only', 'no-run', 'not run',
    'not executed',
)
# Words that would change GitHub issue state if repeated in a published report.
# They are detected here so the verifier never emits one of them.
_STATE_TERMS_RE = re.compile(
    r'\b(?:close|closes|closed|closing|resolve|resolves|resolved|fix|fixes|fixed)\b',
    re.IGNORECASE,
)
_ARCH_NORM_RE = re.compile(r'[\s_-]+')
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
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _is_hash(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in _HEX for ch in value))


def _load(path_text: str | None) -> tuple[dict, Path | None]:
    """Parse one CLI-supplied JSON object; return ``(document, base_dir)``."""
    if not path_text:
        raise LoadError('MISSING_INPUT: no --records path supplied')
    if path_text == '-':
        try:
            raw = sys.stdin.read()
        except OSError as exc:
            raise LoadError('UNREADABLE_INPUT: cannot read stdin') from exc
        base = None
    else:
        path = Path(path_text)
        if not path.is_file():
            raise LoadError('MISSING_INPUT: evidence document is not a file')
        try:
            raw = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            raise LoadError('UNREADABLE_INPUT: evidence document') from exc
        base = path.resolve().parent
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise LoadError('INVALID_JSON: evidence document is not valid JSON') from exc
    if not isinstance(doc, dict):
        raise LoadError('INVALID_STRUCTURE: expected a JSON object')
    return doc, base


def _resolve(text, base: Path | None) -> Path | None:
    """Resolve one CLI-supplied path; relative paths need a CLI document base."""
    if not isinstance(text, str) or not text.strip():
        return None
    path = Path(text.strip())
    if not path.is_absolute():
        if base is None:
            return None
        path = base / path
    return path


def _pe32plus_info(data: bytes) -> tuple[str | None, str]:
    """Identify a PE32+/AMD64 image without executing it."""
    if len(data) < 0x40 or data[:2] != b'MZ':
        return None, 'not a DOS/PE image'
    pe_off = struct.unpack_from('<I', data, 0x3C)[0]
    if pe_off < 0x40 or pe_off + 24 > len(data):
        return None, 'invalid PE header offset'
    if data[pe_off:pe_off + 4] != b'PE\x00\x00':
        return None, 'PE signature missing'
    machine = struct.unpack_from('<H', data, pe_off + 4)[0]
    if machine != _MACHINE_AMD64:
        return None, 'PE machine is not AMD64'
    opt = pe_off + 24
    if opt + 2 > len(data):
        return None, 'PE optional header truncated'
    magic = struct.unpack_from('<H', data, opt)[0]
    if magic != _PE32PLUS_MAGIC:
        return None, 'PE optional magic is not PE32+'
    return 'pe32+-amd64', ''


def _x64_info(data: bytes) -> tuple[str | None, str]:
    """Identify a 64-bit x86 native binary without executing it."""
    if len(data) < 24:
        return None, 'binary too small for a native header'
    if data[:4] == b'\x7fELF':
        if data[4] != 2:
            return None, 'ELF is not 64-bit'
        endian = '<' if data[5] == 1 else '>'
        if len(data) < 20:
            return None, 'ELF header truncated'
        machine = struct.unpack_from(endian + 'H', data, 18)[0]
        if machine != _EM_X86_64:
            return None, 'ELF machine is not EM_X86_64'
        return 'elf64-x86-64', ''
    endian = _MACH_O_64.get(data[:4])
    if endian is not None:
        if len(data) < 8:
            return None, 'Mach-O header truncated'
        cputype = struct.unpack_from(endian + 'i', data, 4)[0]
        if cputype != _CPU_TYPE_X86_64:
            return None, 'Mach-O cputype is not x86-64'
        return 'macho64-x86-64', ''
    if data[:2] == b'MZ':
        return _pe32plus_info(data)
    return None, 'unrecognised native format'


def _artifact(row, label: str, base: Path | None, reasons: list[str],
              kind: str) -> tuple[str, str | None, str | None]:
    """Validate one artifact row; return ``(status, digest, format)``."""
    if row is None or (isinstance(row, (dict, list)) and not row):
        reasons.append(f'{label}: absent')
        return DEFERRED, None, None
    if not isinstance(row, dict):
        reasons.append(f'{label}: not a path/hash object')
        return BLOCKED, None, None
    path = _resolve(row.get('path'), base)
    if path is None:
        reasons.append(f'{label}: no usable artifact path')
        return BLOCKED, None, None
    declared = row.get('sha256')
    if not _is_hash(declared):
        reasons.append(f'{label}: invalid declared sha256')
        return BLOCKED, None, None
    if not path.is_file():
        reasons.append(f'{label}: artifact file is missing')
        return BLOCKED, None, None
    try:
        actual = sha256(path)
        data = path.read_bytes()
    except OSError:
        reasons.append(f'{label}: artifact file is unreadable')
        return BLOCKED, None, None
    if actual.lower() != declared.lower():
        reasons.append(f'{label}: hash mismatch')
        return BLOCKED, None, None
    if not data:
        reasons.append(f'{label}: artifact file is empty')
        return BLOCKED, None, None
    if kind == 'pe32plus':
        fmt, err = _pe32plus_info(data)
    elif kind == 'x64':
        fmt, err = _x64_info(data)
    else:
        fmt, err = 'output', ''
    if err:
        reasons.append(f'{label}: {err}')
        return BLOCKED, None, None
    return PASS, actual, fmt


def _compile_only_marker(record: dict) -> str | None:
    """Return the exact compile-only marker a record carries, if any."""
    for key in ('compile_only', 'build_only'):
        if record.get(key) is True:
            return f'{key} flag is true'
    for key in ('verdict', 'status', 'build_status', 'result'):
        value = record.get(key)
        if isinstance(value, str) and value.strip().lower() in _COMPILE_ONLY_VALUES:
            return f'{key}={value.strip()}'
    return None


def _compile_only_command(command: str) -> bool:
    """True for a build/no-op command, which never demonstrates a run.

    Only the executable's basename and the arguments after it are inspected;
    a path that merely contains a build word is not itself a compile marker.
    """
    tokens = re.findall(r'[^\s]+', command.lower())
    if not tokens:
        return True
    first = Path(tokens[0]).name
    if first in _BUILD_TOOLS or first in _NOOP_TOKENS:
        return True
    args = ' '.join(tokens[1:])
    if '-c' in tokens[1:]:
        return True
    return any(phrase in args for phrase in _COMPILE_ONLY_PHRASES)


def _command_check(record: dict, binary_name: str | None,
                   reasons: list[str]) -> tuple[str, str | None]:
    key = 'executed_command'
    if key not in record or record[key] is None:
        reasons.append(f'{key}: absent')
        return DEFERRED, None
    value = record[key]
    if isinstance(value, str) and not value.strip():
        reasons.append(f'{key}: absent')
        return DEFERRED, None
    if not isinstance(value, str):
        reasons.append(f'{key}: not a command string')
        return BLOCKED, None
    command = value.strip()
    if _STATE_TERMS_RE.search(command):
        reasons.append(f'{key}: carries a banned term')
        return BLOCKED, None
    if _compile_only_command(command):
        reasons.append(f'{key}: compile-only, not an execution')
        return DEFERRED, None
    if binary_name is None:
        # The binary was absent or unverified; that is a missing capability,
        # not an additional tampering finding.
        reasons.append(f'{key}: no proven native binary to bind to')
        return DEFERRED, None
    if binary_name.lower() not in command.lower():
        reasons.append(f'{key}: does not name the native binary')
        return BLOCKED, None
    return PASS, command


def _exit_check(record: dict, reasons: list[str]) -> tuple[str, int | None]:
    key = 'exit_code'
    if key not in record or record[key] is None:
        reasons.append(f'{key}: absent')
        return DEFERRED, None
    value = record[key]
    if isinstance(value, bool) or not isinstance(value, int):
        reasons.append(f'{key}: not an integer')
        return BLOCKED, None
    if value != 0:
        reasons.append(f'{key}: run did not exit successfully')
        return BLOCKED, None
    return PASS, value


def _arch_check(record: dict, reasons: list[str]) -> tuple[str, str | None]:
    key = 'host_arch'
    value = record.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        reasons.append(f'{key}: absent')
        return DEFERRED, None
    if not isinstance(value, str):
        reasons.append(f'{key}: not an architecture name')
        return BLOCKED, None
    arch = value.strip()
    if _ARCH_NORM_RE.sub('-', arch.lower()) not in _X64_ARCHES:
        reasons.append(f'{key}: not an x86-64 spelling')
        return BLOCKED, None
    return PASS, arch


def _check_record(record, base: Path | None, index: int):
    label = f'record[{index}]'
    if not isinstance(record, dict):
        reasons = [f'{label}: not an object']
        return BLOCKED, {name: BLOCKED for name in FIELDS}, reasons, None

    reasons: list[str] = []
    marker = _compile_only_marker(record)
    if marker:
        reasons.append(f'{label}: compile-only marker ({marker})')

    in_status, in_digest, _ = _artifact(
        record.get('pe32plus_input'), 'pe32plus_input', base, reasons, 'pe32plus')
    bin_status, bin_digest, bin_fmt = _artifact(
        record.get('native_binary'), 'native_binary', base, reasons, 'x64')

    bin_row = record.get('native_binary')
    bin_name = None
    if bin_digest is not None and isinstance(bin_row, dict):
        raw_path = bin_row.get('path')
        if isinstance(raw_path, str) and raw_path.strip():
            bin_name = Path(raw_path.strip()).name

    cmd_status, command = _command_check(record, bin_name, reasons)
    exit_status, exit_code = _exit_check(record, reasons)
    out_status, out_digest, _ = _artifact(
        record.get('output'), 'output', base, reasons, 'output')
    arch_status, arch = _arch_check(record, reasons)

    checks = {
        'pe32plus_input': in_status,
        'native_binary': bin_status,
        'executed_command': cmd_status,
        'exit_code': exit_status,
        'output': out_status,
        'host_arch': arch_status,
    }
    if BLOCKED in checks.values():
        status = BLOCKED
    elif marker or DEFERRED in checks.values():
        status = DEFERRED
    elif all(value == PASS for value in checks.values()):
        status = PASS
    else:
        status = NOT_TESTED

    claim = None
    if status == PASS:
        claim = {
            'scope': 'exact-record-only',
            'pe32plus_sha256': in_digest,
            'native_binary_sha256': bin_digest,
            'native_binary_format': bin_fmt,
            'executed_command': command,
            'exit_code': exit_code,
            'output_sha256': out_digest,
            'host_arch': arch,
            'note': ('Only this recomputed record is covered; no general x64 '
                     'support is implied. No issue state is changed.'),
        }
    return status, checks, reasons, claim


def _decision(status: str, checks: dict, reasons: list[str],
              claims: list | None = None) -> dict:
    return {
        'schema': SCHEMA,
        'status': status,
        'supported': status == SUPPORTED,
        'issue_action': 'none',
        'checks': checks,
        'reasons': reasons,
        'rule': RULE,
        'supported_claims': claims or [],
    }


def evaluate(doc: dict, base: Path | None = None) -> dict:
    """Pure decision function over the supplied document and its file paths."""
    reasons: list[str] = []
    if doc.get('schema', SCHEMA) != SCHEMA:
        reasons.append('schema mismatch')
        return _decision(NOT_TESTED, {}, reasons)

    records = doc.get('records')
    if records is None and isinstance(doc.get('record'), dict):
        records = [doc['record']]
    if not isinstance(records, list):
        reasons.append('records is not a list')
        return _decision(NOT_TESTED, {}, reasons)
    if not records:
        reasons.append('no records supplied')
        return _decision(NOT_TESTED, {}, reasons)

    results = [_check_record(record, base, index)
               for index, record in enumerate(records)]
    checks = {f'record[{index}]': result[1]
              for index, result in enumerate(results)}
    all_reasons = [reason for result in results for reason in result[2]]
    statuses = [result[0] for result in results]
    if BLOCKED in statuses:
        status = BLOCKED
    elif DEFERRED in statuses:
        status = DEFERRED
    elif all(value == PASS for value in statuses):
        status = SUPPORTED
    else:
        status = NOT_TESTED
    claims = [result[3] for result in results] if status == SUPPORTED else None
    return _decision(status, checks, all_reasons, claims)


def _emit(result: dict, out_path: str | None) -> None:
    text = json.dumps(result, indent=1, sort_keys=True) + '\n'
    if _STATE_TERMS_RE.search(text):
        result = _decision(BLOCKED, {},
                           ['INTERNAL_GUARD: banned term in decision text'])
        text = json.dumps(result, indent=1, sort_keys=True) + '\n'
    if out_path:
        Path(out_path).write_text(text, encoding='utf-8')
    print(text, end='')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--records', required=True,
                    help='path to the CLI-supplied evidence JSON object, or - for stdin')
    ap.add_argument('--json', dest='json_out',
                    help='write the decision JSON here')
    args = ap.parse_args(argv)
    try:
        doc, base = _load(args.records)
        result = evaluate(doc, base)
    except LoadError as exc:
        message = str(exc)
        print(message, file=sys.stderr)
        result = _decision(NOT_TESTED, {}, [message])
    try:
        _emit(result, args.json_out)
    except OSError as exc:
        print(f'CANNOT_WRITE_DECISION: {args.json_out}: {exc}', file=sys.stderr)
        return 3
    return 0 if result['status'] == SUPPORTED else 3


if __name__ == '__main__':
    sys.exit(main())
