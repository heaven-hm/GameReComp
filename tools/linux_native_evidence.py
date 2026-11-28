#!/usr/bin/env python3
"""Fail-closed per-architecture verifier for Windows PE -> native Linux evidence.

This is a *decision tool* for issue #134, not a recompiler, a Linux runner or an
ELF writer. It reads exactly one CLI-supplied JSON document (``--records FILE``;
``-`` reads stdin) holding Linux-host pipeline records. It recomputes every
digest from the supplied file bytes and emits a separate status for x86 and x64.

An architecture is PASS only when the same record shows all four things:

  1. ``host``       -- real Linux host facts (Linux OS, Linux kernel/uname and
                       an architecture spelling matching that record);
  2. ``original``   -- a hash-checked real PE32/i386 (x86) or PE32+/AMD64
                       (x64) input image;
  3. ``native_elf`` -- a hash-checked, structurally real 32-bit i386 or
                       64-bit x86-64 ELF with a loadable segment;
  4. ``executed_scenario`` -- a hash-checked JSON proof that a named scenario
                       ran that exact ELF on a Linux host, exited 0, and binds
                       both the original and native ELF digests.

Self-reported PASS flags, booleans and compile-only markers are never evidence.
A macOS/Darwin host is explicitly refused for this direction: no macOS run,
kernel or dyld-based scenario may stand in for a Linux run. Missing artifacts
are DEFERRED (no claim), supplied but wrong/tampered artifacts are BLOCKED, and
absent or malformed CLI input is NOT TESTED. The overall verdict is PASS only
when *both* architectures are PASS; every other outcome stays distinct.

No path, host, key or executable is built in; all paths come from the supplied
document (relative paths resolve against that document's directory). The tool
never executes a command, writes an ELF, changes issue state or generalises a
PASS beyond the exact digests in the supplied record.

Usage::

    python3 tools/linux_native_evidence.py --records evidence.json
    python3 tools/linux_native_evidence.py --records evidence.json --json decision.json

Exit status: 0 = both architectures PASS, 3 = any other verdict, 2 = CLI usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

SCHEMA = 'gamerecomp.linux-native-evidence.v1'
ARCHES = ('x86', 'x64')
CHECKS = ('host', 'original', 'native_elf', 'executed_scenario')
PASS, NOT_TESTED, BLOCKED, DEFERRED = 'PASS', 'NOT TESTED', 'BLOCKED', 'DEFERRED'

ISSUE = 134
DIRECTION = 'windows-pe-to-native-linux'
RULE = (
    'PASS requires, for BOTH x86 and x64, real Linux host facts, a hash-checked '
    'real PE input, a hash-checked structurally real native ELF, and a '
    'hash-checked executed-scenario proof that binds both digests and exited 0. '
    'macOS/Darwin evidence is never substituted for Linux; missing artifacts '
    'are DEFERRED, tampered or invalid artifacts are BLOCKED, and absent CLI '
    'input is NOT TESTED. Only the exact supplied digests are covered.'
)

_HEX = frozenset('0123456789abcdefABCDEF')
_NO_NAME = frozenset({
    '', 'none', 'missing', 'n/a', 'na', 'unproven', 'partial', 'unknown',
    'self-reported', 'compile-only', 'compile only', 'build-only', 'build only',
    NOT_TESTED.lower(),
})
_COMPILE_ONLY_VALUES = frozenset({
    'compile-only', 'compile only', 'compile_only', 'build-only', 'build only',
    'build_only', 'compiled-only', 'compiled only', 'source-only', 'source only',
    'no-run', 'not run', 'not executed',
})
_BUILD_TOOLS = frozenset({
    'cc', 'gcc', 'g++', 'clang', 'clang++', 'cl', 'cl.exe', 'msbuild', 'cargo',
    'rustc', 'javac', 'ld', 'lld', 'link', 'make', 'cmake',
})
_NOOP_TOKENS = frozenset({'true', 'echo', ':', 'no-op', 'noop'})

# Words that would change GitHub issue state if repeated in a published report.
# They are detected so the tool never emits one of them.
_STATE_TERMS_RE = re.compile(
    r'\b(?:close|closes|closed|closing|resolve|resolves|resolved|fix|fixes|fixed)\b',
    re.IGNORECASE,
)
_NON_LINUX_RE = re.compile(r'\b(?:darwin|macos|mac os|osx|os x|apple|windows|nt)\b',
                           re.IGNORECASE)
_LINUX_RE = re.compile(r'\blinux\b', re.IGNORECASE)
_MACOS_RUNTIME_RE = re.compile(r'\b(?:dyld|cocoa|appkit|mach-o|macho)\b',
                               re.IGNORECASE)
_ARCH_NORM_RE = re.compile(r'[\s_]+')
_ARCH_ALIASES = {
    'x86': frozenset({'x86', 'i386', 'i486', 'i586', 'i686', 'ia32', 'x86-32',
                      '32-bit', '32'}),
    'x64': frozenset({'x64', 'amd64', 'x86-64', 'x8664', 'intel64', 'em64t',
                      'x86-64', '64-bit', '64'}),
}
_HOST_ARCHES = {
    'x86': frozenset({'x86', 'i386', 'i486', 'i586', 'i686', 'ia32'}),
    'x64': frozenset({'x64', 'amd64', 'x86-64', 'x8664', 'intel64'}),
}

_ELF_MAGIC = b'\x7fELF'
_EM_386, _EM_X86_64 = 3, 62
_PT_LOAD = 1
_ET_EXEC, _ET_DYN = 2, 3
_MACHINE_I386, _MACHINE_AMD64 = 0x14C, 0x8664
_PE32_MAGIC, _PE32PLUS_MAGIC = 0x10B, 0x20B


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


def _named(value) -> bool:
    return isinstance(value, str) and value.strip().lower() not in _NO_NAME


def _norm_arch(value) -> str:
    return _ARCH_NORM_RE.sub('-', str(value).strip().lower())


def _arch_of(value) -> str | None:
    """Canonical x86/x64 architecture name, or None when unrecognised."""
    if not isinstance(value, str):
        return None
    token = _norm_arch(value)
    for arch in ARCHES:
        if token in _ARCH_ALIASES[arch]:
            return arch
    return None


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


def _checked_file(row, label: str, base: Path | None, reasons: list[str],
                  absent_note: str) -> tuple[str, str | None, bytes | None]:
    """Validate one path/hash artifact row; return ``(status, digest, data)``."""
    if row is None or (isinstance(row, (dict, list)) and not row):
        reasons.append(f'{label}: absent ({absent_note})')
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
    return PASS, actual, data


def _pe_info(data: bytes, arch: str) -> tuple[str | None, str]:
    """Identify a real PE32/i386 or PE32+/AMD64 image without executing it."""
    if len(data) < 0x40 or data[:2] != b'MZ':
        return None, 'not a DOS/PE image'
    pe_off = struct.unpack_from('<I', data, 0x3C)[0]
    if pe_off < 0x40 or pe_off + 24 > len(data):
        return None, 'invalid PE header offset'
    if data[pe_off:pe_off + 4] != b'PE\x00\x00':
        return None, 'PE signature missing'
    machine = struct.unpack_from('<H', data, pe_off + 4)[0]
    opt = pe_off + 24
    if opt + 2 > len(data):
        return None, 'PE optional header truncated'
    magic = struct.unpack_from('<H', data, opt)[0]
    if arch == 'x86':
        if machine != _MACHINE_I386:
            return None, f'PE machine 0x{machine:04x} is not i386 (0x014c)'
        if magic != _PE32_MAGIC:
            return None, f'PE optional magic 0x{magic:03x} is not PE32 (0x10b)'
        return 'pe32-i386', ''
    if machine != _MACHINE_AMD64:
        return None, f'PE machine 0x{machine:04x} is not AMD64 (0x8664)'
    if magic != _PE32PLUS_MAGIC:
        return None, f'PE optional magic 0x{magic:03x} is not PE32+ (0x20b)'
    return 'pe32+-amd64', ''


def _elf_info(data: bytes, arch: str) -> tuple[str | None, str]:
    """Identify a structurally real native ELF for ``arch`` without running it.

    Magic alone is not enough: a real image needs a coherent ELF header, the
    right class/machine, and at least one in-file PT_LOAD segment with bytes.
    That keeps text stubs and magic-only forgeries from being called native ELF.
    """
    if len(data) < 20 or data[:4] != _ELF_MAGIC:
        return None, 'not an ELF image (missing ELF magic)'
    elf_class = 1 if arch == 'x86' else 2
    ehsize = 52 if arch == 'x86' else 64
    phentsize = 32 if arch == 'x86' else 56
    machine = _EM_386 if arch == 'x86' else _EM_X86_64
    want = 'EM_386' if arch == 'x86' else 'EM_X86_64'
    if data[4] != elf_class:
        return None, f'ELF is not {32 if arch == "x86" else 64}-bit (EI_CLASS={data[4]})'
    if data[5] not in (1, 2):
        return None, f'invalid ELF data encoding (EI_DATA={data[5]})'
    if data[6] != 1:
        return None, f'invalid ELF ident version (EI_VERSION={data[6]})'
    if len(data) < ehsize:
        return None, 'ELF header truncated'
    endian = '<' if data[5] == 1 else '>'
    if struct.unpack_from(endian + 'I', data, 20)[0] != 1:
        return None, 'invalid ELF version (e_version)'
    actual_machine = struct.unpack_from(endian + 'H', data, 18)[0]
    if actual_machine != machine:
        return None, f'ELF machine {actual_machine} is not {want} ({machine})'
    e_type = struct.unpack_from(endian + 'H', data, 16)[0]
    if e_type not in (_ET_EXEC, _ET_DYN):
        return None, 'ELF type is not executable or shared object'
    if struct.unpack_from(endian + 'H', data, 40 if arch == 'x86' else 52)[0] != ehsize:
        return None, f'invalid ELF header size (expected {ehsize})'
    phoff = struct.unpack_from(endian + 'I', data, 28 if arch == 'x86' else 32)[0]
    phentsize_actual = struct.unpack_from(endian + 'H', data,
                                          42 if arch == 'x86' else 54)[0]
    phnum = struct.unpack_from(endian + 'H', data,
                               44 if arch == 'x86' else 56)[0]
    entry = struct.unpack_from(endian + ('I' if arch == 'x86' else 'Q'), data,
                               24)[0]
    if phnum < 1 or phoff < ehsize or phentsize_actual < phentsize:
        return None, 'ELF program header table is absent or fabricated'
    table_end = phoff + phnum * phentsize_actual
    if table_end > len(data):
        return None, 'ELF program header table is truncated'
    if entry == 0:
        return None, 'ELF entry point is zero (not a runnable image)'
    loadable = False
    for index in range(phnum):
        off = phoff + index * phentsize_actual
        p_type = struct.unpack_from(endian + 'I', data, off)[0]
        if arch == 'x86':
            p_offset = struct.unpack_from(endian + 'I', data, off + 4)[0]
            p_filesz = struct.unpack_from(endian + 'I', data, off + 16)[0]
        else:
            p_offset = struct.unpack_from(endian + 'Q', data, off + 8)[0]
            p_filesz = struct.unpack_from(endian + 'Q', data, off + 32)[0]
        if p_type == _PT_LOAD and p_filesz > 0 and p_offset + p_filesz <= len(data):
            loadable = True
            break
    if not loadable:
        return None, 'ELF has no in-file PT_LOAD segment (fabricated image)'
    return ('elf32-i386' if arch == 'x86' else 'elf64-x86-64'), ''


def _check_host(record: dict, arch: str, reasons: list[str]) -> tuple[str, dict]:
    """Validate real Linux host facts for this architecture."""
    row = record.get('host')
    if row is None or (isinstance(row, (dict, list)) and not row):
        reasons.append('host: absent (no Linux host facts supplied)')
        return DEFERRED, {}
    if not isinstance(row, dict):
        reasons.append('host: not a host-facts object')
        return BLOCKED, {}
    values = {key: value for key, value in row.items() if isinstance(value, str)}
    facts = ' '.join(values.values())
    if _NON_LINUX_RE.search(facts) and not _LINUX_RE.search(facts):
        reasons.append('host: macOS/Darwin/Windows facts are not Linux host facts; '
                       'no substitution for this direction')
        return BLOCKED, {}
    os_name = None
    for key in ('os', 'system', 'platform'):
        if isinstance(row.get(key), str) and row[key].strip():
            os_name = row[key].strip()
            break
    if os_name is None:
        reasons.append('host.os: absent (no host OS fact)')
        return BLOCKED, {}
    if _NON_LINUX_RE.search(os_name) or not _LINUX_RE.search(os_name):
        reasons.append(f'host.os: not Linux ({os_name})')
        return BLOCKED, {}
    kernel = None
    for key in ('kernel', 'uname', 'release'):
        if isinstance(row.get(key), str) and row[key].strip():
            kernel = row[key].strip()
            break
    if kernel is None:
        reasons.append('host.kernel: absent (no Linux kernel/uname fact)')
        return DEFERRED, {}
    if _NON_LINUX_RE.search(kernel) or not _LINUX_RE.search(kernel):
        reasons.append(f'host.kernel: not a Linux kernel ({kernel})')
        return BLOCKED, {}
    machine = None
    for key in ('machine', 'arch', 'architecture'):
        if isinstance(row.get(key), str) and row[key].strip():
            machine = row[key].strip()
            break
    if machine is None:
        reasons.append('host.machine: absent (no host architecture fact)')
        return DEFERRED, {}
    if _norm_arch(machine) not in _HOST_ARCHES[arch]:
        reasons.append(f'host.machine: not an {arch} host ({machine})')
        return BLOCKED, {}
    return PASS, {'os': os_name, 'kernel': kernel, 'machine': machine}


def _check_original(record: dict, arch: str, base: Path | None,
                    reasons: list[str]) -> tuple[str, str | None, str | None]:
    """Validate the declared original PE image and recompute its digest."""
    status, digest, data = _checked_file(
        record.get('original'), 'original', base, reasons,
        'no original PE image supplied')
    if status != PASS or data is None:
        return status, None, None
    fmt, err = _pe_info(data, arch)
    if fmt is None:
        reasons.append(f'original: not a real PE32/i386 image for {arch}: {err}'
                       if arch == 'x86' else
                       f'original: not a real PE32+/AMD64 image for {arch}: {err}')
        return BLOCKED, None, None
    return PASS, digest, fmt


def _check_elf(record: dict, arch: str, base: Path | None,
               reasons: list[str]) -> tuple[str, str | None, str | None]:
    """Validate the declared native Linux ELF and recompute its digest."""
    status, digest, data = _checked_file(
        record.get('native_elf'), 'native_elf', base, reasons,
        'no native Linux ELF supplied')
    if status != PASS or data is None:
        return status, None, None
    fmt, err = _elf_info(data, arch)
    if fmt is None:
        reasons.append(f'native_elf: not a real native ELF for {arch}: {err}')
        return BLOCKED, None, None
    return PASS, digest, fmt


def _compile_only(doc: dict) -> str | None:
    """Return an exact compile-only marker carried by a scenario, if any."""
    for key in ('compile_only', 'build_only'):
        if doc.get(key) is True:
            return f'{key} flag is true'
    for key in ('verdict', 'status', 'build_status', 'result'):
        value = doc.get(key)
        if isinstance(value, str) and value.strip().lower() in _COMPILE_ONLY_VALUES:
            return f'{key}={value.strip()}'
    return None


def _compile_only_command(command: str) -> bool:
    """True for a build/no-op command, which never demonstrates a run."""
    tokens = re.findall(r'[^\s]+', command.lower())
    if not tokens:
        return True
    if Path(tokens[0]).name in _BUILD_TOOLS or Path(tokens[0]).name in _NOOP_TOKENS:
        return True
    args = ' '.join(tokens[1:])
    if '-c' in tokens[1:]:
        return True
    return any(word in args for word in _COMPILE_ONLY_VALUES)


def _check_scenario(record: dict, arch: str, base: Path | None,
                    original_digest: str | None, elf_digest: str | None,
                    elf_name: str | None, reasons: list[str]) -> tuple[str, dict]:
    """Validate the executed-scenario proof and its bindings; return its claim."""
    status, _, data = _checked_file(
        record.get('executed_scenario'), 'executed_scenario', base, reasons,
        'no executed scenario; a build cannot support a Linux run claim')
    if status != PASS or data is None:
        return status, {}
    try:
        doc = json.loads(data.decode('utf-8'))
    except (UnicodeError, ValueError):
        reasons.append('executed_scenario: proof is not valid JSON')
        return BLOCKED, {}
    if not isinstance(doc, dict):
        reasons.append('executed_scenario: proof is not a JSON object')
        return BLOCKED, {}

    marker = _compile_only(doc)
    if marker:
        reasons.append(f'executed_scenario: compile-only marker ({marker})')

    bad: list[str] = []
    deferred: list[str] = []
    verdict = doc.get('verdict')
    if verdict is None or not isinstance(verdict, str) or not verdict.strip():
        deferred.append('executed_scenario: verdict: absent')
    elif (verdict.strip().upper() != PASS
          and verdict.strip().lower() not in _COMPILE_ONLY_VALUES):
        bad.append(f'executed_scenario: verdict is not PASS ({verdict})')
    if not _named(doc.get('scenario')):
        deferred.append('executed_scenario: no named scenario')
    if not _named(doc.get('host')):
        deferred.append('executed_scenario: no named host')
    else:
        host = doc['host'].strip()
        if _NON_LINUX_RE.search(host) or not _LINUX_RE.search(host):
            bad.append(f'executed_scenario: host is not Linux ({host}); '
                       'no macOS/Darwin run may be substituted')
    command = doc.get('command')
    if command is None or (isinstance(command, str) and not command.strip()):
        deferred.append('executed_scenario: command: absent')
    elif not isinstance(command, str):
        bad.append('executed_scenario: command is not a string')
    else:
        command = command.strip()
        if _STATE_TERMS_RE.search(command):
            bad.append('executed_scenario: command carries a banned term')
        elif _MACOS_RUNTIME_RE.search(command):
            bad.append('executed_scenario: macOS runtime in command; '
                       'no macOS run may be substituted for Linux')
        elif _compile_only_command(command):
            deferred.append('executed_scenario: command is compile-only, not an execution')
        elif elf_name is None:
            deferred.append('executed_scenario: no proven native ELF to bind to')
        elif elf_name.lower() not in command.lower():
            bad.append('executed_scenario: command does not name the native ELF')
    exit_code = doc.get('exit_code')
    if exit_code is None:
        deferred.append('executed_scenario: exit_code: absent')
    elif isinstance(exit_code, bool) or not isinstance(exit_code, int):
        bad.append('executed_scenario: exit_code is not an integer')
    elif exit_code != 0:
        bad.append(f'executed_scenario: exit_code is {exit_code}, not 0')
    bound_original = doc.get('original_sha256')
    if bound_original is None or bound_original == '':
        deferred.append('executed_scenario: original_sha256: absent')
    elif not _is_hash(bound_original):
        bad.append('executed_scenario: invalid original_sha256 binding')
    elif original_digest is None:
        deferred.append('executed_scenario: no proven original image to bind to')
    elif bound_original.lower() != original_digest.lower():
        bad.append('executed_scenario: original_sha256 does not match the original image')
    bound_elf = doc.get('native_elf_sha256')
    if bound_elf is None or bound_elf == '':
        deferred.append('executed_scenario: native_elf_sha256: absent')
    elif not _is_hash(bound_elf):
        bad.append('executed_scenario: invalid native_elf_sha256 binding')
    elif elf_digest is None:
        deferred.append('executed_scenario: no proven native ELF to bind to')
    elif bound_elf.lower() != elf_digest.lower():
        bad.append('executed_scenario: native_elf_sha256 does not match the native ELF')
    logs = doc.get('stage_logs', [])
    if logs is not None:
        if not isinstance(logs, list):
            bad.append('executed_scenario: stage_logs is not a list')
        else:
            for index, row in enumerate(logs):
                _s, _d, _ = _checked_file(
                    row, f'executed_scenario: stage_logs[{index}]', base, bad, '')
                if _s != PASS:
                    break

    reasons.extend(bad)
    reasons.extend(deferred)
    if bad or _STATE_TERMS_RE.search(command or ''):
        return BLOCKED, {}
    if marker or deferred:
        return DEFERRED, {}
    return PASS, {'scenario': doc['scenario'].strip(), 'host': doc['host'].strip(),
                  'command': command, 'exit_code': exit_code}


def _arch_decision(checks: dict, reasons: list[str], claim: dict | None) -> dict:
    if BLOCKED in checks.values():
        status = BLOCKED
    elif DEFERRED in checks.values():
        status = DEFERRED
    elif all(checks.get(name) == PASS for name in CHECKS):
        status = PASS
    else:
        status = NOT_TESTED
    return {
        'status': status,
        'checks': {name: checks.get(name, NOT_TESTED) for name in CHECKS},
        'reasons': reasons,
        'claim': claim if status == PASS else None,
    }


def _check_record(record, base: Path | None, arch: str) -> dict:
    if not isinstance(record, dict):
        return _arch_decision({}, [f'{arch}: record is not an object'], None)
    reasons: list[str] = []
    host_status, host_facts = _check_host(record, arch, reasons)
    original_status, original_digest, original_fmt = _check_original(
        record, arch, base, reasons)
    elf_status, elf_digest, elf_fmt = _check_elf(record, arch, base, reasons)
    elf_row = record.get('native_elf')
    elf_name = None
    if isinstance(elf_row, dict) and isinstance(elf_row.get('path'), str):
        elf_name = Path(elf_row['path'].strip()).name or None
    scenario_status, scoped = _check_scenario(
        record, arch, base, original_digest, elf_digest, elf_name, reasons)
    checks = {
        'host': host_status, 'original': original_status,
        'native_elf': elf_status, 'executed_scenario': scenario_status,
    }
    claim = None
    if all(value == PASS for value in checks.values()) and scoped:
        claim = {
            'scope': f'exact-{arch}-record-only',
            'arch': arch,
            'original_format': original_fmt,
            'original_sha256': original_digest,
            'native_elf_format': elf_fmt,
            'native_elf_sha256': elf_digest,
            'scenario': scoped['scenario'],
            'executed_command': scoped['command'],
            'host': host_facts,
            'note': ('Only the exact digests in this supplied record are covered; '
                     'no general bridge support is implied.'),
        }
    return _arch_decision(checks, reasons, claim)


def _decision(status: str, architectures: dict, reasons: list[str]) -> dict:
    claims = [item['claim'] for item in architectures.values() if item['claim']]
    return {
        'schema': SCHEMA,
        'status': status,
        'passed': status == PASS,
        'issue': ISSUE,
        'issue_action': 'none',
        'direction': DIRECTION,
        'architectures': architectures,
        'reasons': reasons,
        'rule': RULE,
        'supported_claims': claims,
    }


def evaluate(doc: dict, base: Path | None = None) -> dict:
    """Pure decision function over the CLI-supplied document and its paths."""
    reasons: list[str] = []
    if doc.get('schema', SCHEMA) != SCHEMA:
        reasons.append(f'schema mismatch: {doc.get("schema")!r}')
        return _decision(NOT_TESTED, {}, reasons)
    if doc.get('issue', ISSUE) != ISSUE:
        reasons.append(f'issue mismatch: {doc.get("issue")!r}')
        return _decision(NOT_TESTED, {}, reasons)
    if doc.get('direction', DIRECTION) != DIRECTION:
        reasons.append(f'direction mismatch: {doc.get("direction")!r}')
        return _decision(NOT_TESTED, {}, reasons)
    records = doc.get('records')
    if not isinstance(records, list):
        reasons.append('records is not a list')
        return _decision(NOT_TESTED, {}, reasons)
    if not records:
        reasons.append('no records supplied')
        return _decision(NOT_TESTED, {}, reasons)

    by_arch: dict[str, list] = {arch: [] for arch in ARCHES}
    input_blocked = False
    for index, record in enumerate(records):
        arch = _arch_of(record.get('arch')) if isinstance(record, dict) else None
        if arch is None:
            input_blocked = True
            detail = repr(record.get('arch')) if isinstance(record, dict) else repr(record)
            reasons.append(f'record[{index}]: unsupported or missing arch {detail}')
        else:
            by_arch[arch].append((index, record))

    architectures: dict[str, dict] = {}
    for arch in ARCHES:
        group = by_arch[arch]
        if not group:
            item = _arch_decision(
                {name: NOT_TESTED for name in CHECKS},
                [f'{arch}: no record supplied'], None)
        elif len(group) > 1:
            item = _arch_decision(
                {name: BLOCKED for name in CHECKS},
                [f'{arch}: duplicate records '
                 f'({", ".join(f"record[{i}]" for i, _ in group)}); '
                 'refusing to choose one'], None)
        else:
            item = _check_record(group[0][1], base, arch)
        architectures[arch] = item
        reasons.extend(f'{arch}: {reason}' for reason in item['reasons'])

    # Keep the final list deterministic and de-duplicated.
    reasons = list(dict.fromkeys(reasons))
    statuses = [architectures[arch]['status'] for arch in ARCHES]
    if input_blocked or BLOCKED in statuses:
        status = BLOCKED
    elif DEFERRED in statuses:
        status = DEFERRED
    elif NOT_TESTED in statuses:
        status = NOT_TESTED
    else:
        status = PASS
    return _decision(status, architectures, reasons)


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
    return 0 if result['status'] == PASS else 3


if __name__ == '__main__':
    sys.exit(main())
