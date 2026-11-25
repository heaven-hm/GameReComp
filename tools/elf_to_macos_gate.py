#!/usr/bin/env python3
"""Fail-closed feasibility gate: Linux ELF -> macOS Mach-O/arm64 (Issue #138).

This module does **not** lift an ELF, emit a Mach-O, map a system call, compile
a native binary or execute a game. It joins evidence that another tool (or a
human) supplies on the command line and refuses to open the gate unless every
required capability is backed by a real, hash-verified artifact. It is a
decision function, not an implementation of the bridge.

The five mandatory requirements, mirroring ``docs/roadmaps/OS_BRIDGE_138.md``:

1. ``elf_lift``       -- a lift path from a Linux ELF exists (report + hash);
2. ``macho_writer``   -- a Mach-O writer exists (report + hash);
3. ``abi_syscall_map``-- an ABI/system-call mapping exists (report + hash);
4. ``binary``         -- a native arm64 Mach-O executable was built (hash +
                         header check on the bytes behind that hash);
5. ``scenario``       -- that binary was executed in a named scenario on a real
                         macOS/arm64 oracle (report + hash + binary hash match).

Every artifact path is CLI-supplied: it is declared in the manifest passed to
``--evidence`` and resolved relative to that manifest's directory (absolute
paths are used verbatim). No repository path, game asset, hotkey, toolchain or
default evidence location is built in. A boolean flag in the manifest is
advisory only -- it never contributes to PASS. A declared SHA-256 that does not
match the bytes on disk blocks the gate; nothing is ever trusted on a claim.

Statuses are kept distinct: ``GO`` (all five proven), ``DEFERRED`` (a
requirement was simply not supplied), ``BLOCKED`` (evidence was supplied but is
absent, malformed, hash-tampered, self-contradictory or unproven), ``FAIL`` (an
executed scenario reported FAIL) and ``NOT TESTED`` (the CLI manifest itself is
missing, unreadable or not a JSON object, so nothing could be evaluated).
Only ``GO`` opens the gate; all other statuses exit non-zero.

Manifest schema (one JSON object)::

    {
      "source_os": "linux", "target_os": "macos", "target_arch": "arm64",
      "elf_lift":        {"artifacts": [{"path": "lift.json", "sha256": "<64hex>"}]},
      "macho_writer":    {"artifacts": [{"path": "writer.json", "sha256": "<64hex>"}]},
      "abi_syscall_map": {"artifacts": [{"path": "abi.json", "sha256": "<64hex>"}]},
      "binary":          {"artifacts": [{"path": "hello", "sha256": "<64hex>"}]},
      "scenario":        {"artifacts": [{"path": "run.json", "sha256": "<64hex>"}]}
    }

The three report artifacts must be JSON objects with ``status``/``verdict``
``PASS`` and a non-empty ``tool``/``method``. ``binary`` must really be a
64-bit little-endian Mach-O executable with ``CPU_TYPE_ARM64``. ``scenario``
must be a JSON object with ``verdict`` ``PASS``, ``executed: true``, a real
(native, non-dummy) ``oracle`` and ``binary_sha256`` equal to the digest of the
verified binary artifact.

Usage::

    python3 -m tools.elf_to_macos_gate --evidence manifest.json \
        [--json decision.json]

Exit codes: 0 = GO, 3 = every other status (including bad CLI input).

Limits: this is evidence bookkeeping. Hashes prove integrity, not truthfulness;
a caller can fabricate a consistent set of files. The gate only certifies that
the named artifacts exist, agree with their hashes and meet the shape checks
above. It is a prerequisite for opening implementation work on #138, not a
claim that ELF -> macOS translation works.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = 'tools/elf_to_macos_gate.py'
RULE = ('GO requires CLI-supplied, hash-verified evidence for all five '
        'requirements; flags alone never PASS')

GO = 'GO'
PASS = 'PASS'
FAIL = 'FAIL'
DEFERRED = 'DEFERRED'
BLOCKED = 'BLOCKED'
NOT_TESTED = 'NOT TESTED'

REPORT_REQUIREMENTS = ('elf_lift', 'macho_writer', 'abi_syscall_map')
REQUIREMENTS = REPORT_REQUIREMENTS + ('binary', 'scenario')

# Manifest-level flags are advisory. They are deliberately ignored by the
# decision function; their only effect is to distinguish a flag-only claim
# (BLOCKED) from an entirely absent requirement (DEFERRED).
FLAG_KEYS = ('proven', 'enabled', 'supported', 'pass', 'ok', 'flag',
             'executed', 'complete', 'verified', 'done')

# mach/machine.h and loader.h values; see tools/macos_target_probe.py.
MH_MAGIC_64_LE = b'\xcf\xfa\xed\xfe'  # MH_MAGIC_64 (0xfeedfacf), little-endian
CPU_TYPE_ARM64 = 0x0100000C           # CPU_TYPE_ARM64 | CPU_ARCH_ABI64
MH_EXECUTE = 0x2                      # a built executable, not a dylib/object

# Only an explicitly executed native macOS/arm64 process counts as an oracle.
REAL_ORACLES = frozenset({
    'macos-arm64-native-process', 'native-macos-arm64-process',
    'macos-arm64-process', 'arm64-native-process', 'native-arm64-process',
    'macos-native-process', 'native-process', 'native-execution',
    'executed-native-binary', 'macos-arm64-oracle',
})
NO_ORACLES = frozenset({
    '', 'none', 'null', 'n/a', 'na', 'unknown', 'unspecified', 'not-supplied',
    'dummy', 'dummy-device', 'headless', 'stub', 'fake', 'fake-success',
    'emulator', 'qemu', 'wine', 'unit-test', 'selftest', 'unexecuted',
    'not-executed', 'not-tested', 'build-only', 'no-oracle',
})
LINUX_TOKENS = frozenset({'linux', 'gnu-linux', 'linux-gnu'})
MACOS_TOKENS = frozenset({'macos', 'mac-os', 'macosx', 'osx', 'darwin', 'apple'})
ARM64_TOKENS = frozenset({'arm64', 'aarch64', 'arm-64'})

_HEX = frozenset('0123456789abcdefABCDEF')
_PRECEDENCE = (BLOCKED, FAIL, DEFERRED, NOT_TESTED)


class LoadError(Exception):
    """The CLI-supplied manifest is missing, unreadable or not an object."""


def is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in _HEX for ch in value))


def norm_token(value: Any) -> str:
    text = str(value or '').strip().lower().replace('_', '-').replace(' ', '-')
    while '--' in text:
        text = text.replace('--', '-')
    return text.strip('-')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(raw: Any, base: Path) -> Path | None:
    """Resolve a declared path; relative paths are relative to the manifest."""
    text = str(raw or '').strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else base / path


def load_manifest(path_text: str | None) -> tuple[dict, Path]:
    """Parse the one CLI-supplied manifest; raise :class:`LoadError` otherwise."""
    if not path_text or not str(path_text).strip():
        raise LoadError('MISSING_INPUT: no --evidence manifest was supplied')
    path = Path(str(path_text)).expanduser()
    if not path.is_file():
        raise LoadError(f'MISSING_INPUT: {path}')
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise LoadError(f'INVALID_JSON: {path}: {exc}') from exc
    if not isinstance(doc, dict):
        raise LoadError(f'INVALID_STRUCTURE: {path}: expected a JSON object')
    return doc, path.resolve().parent


def _flag_claim(section: Any) -> bool:
    return isinstance(section, dict) and any(
        section.get(key) is True for key in FLAG_KEYS)


def _result(status: str, code: str, reasons: list[str]) -> dict:
    return {'status': status, 'code': code, 'reasons': list(reasons)}


def verify_section(name: str, section: Any, base: Path) -> dict:
    """Verify the declared artifact hashes for one requirement."""
    out = _result(NOT_TESTED, '', [])
    out.update({'artifacts': [], 'digests': []})
    if section is None:
        out.update(_result(DEFERRED, 'NO_EVIDENCE',
                           [f'{name}: no evidence supplied']))
        return out
    if not isinstance(section, dict):
        out.update(_result(BLOCKED, 'BAD_SCHEMA',
                           [f'{name}: requirement must be a JSON object']))
        return out

    rows = section.get('artifacts')
    if rows is None:
        if _flag_claim(section):
            out.update(_result(BLOCKED, 'FLAG_ONLY_CLAIM',
                               [f'{name}: flags without a hashed artifact']))
        else:
            out.update(_result(DEFERRED, 'NO_EVIDENCE',
                               [f'{name}: no evidence supplied']))
        return out
    if not isinstance(rows, list) or not rows:
        out.update(_result(BLOCKED, 'NO_ARTIFACTS',
                           [f'{name}: artifacts must be a non-empty list']))
        return out

    bad: list[str] = []
    good: list[dict] = []
    for index, row in enumerate(rows):
        label = f'{name}.artifacts[{index}]'
        if not isinstance(row, dict):
            bad.append(f'{label}: not an object')
            continue
        path = resolve_path(row.get('path'), base)
        if path is None:
            bad.append(f'{label}: no path declared')
            continue
        declared = row.get('sha256')
        if not is_sha256(declared):
            bad.append(f'{label}: invalid declared sha256')
            continue
        if not path.is_file():
            bad.append(f'{label}: artifact missing: {path.name}')
            continue
        try:
            digest = sha256_file(path)
            size = path.stat().st_size
        except OSError:
            bad.append(f'{label}: artifact unreadable: {path.name}')
            continue
        if digest.lower() != declared.lower():
            bad.append(f'{label}: sha256 mismatch (tampered): {path.name}')
            continue
        good.append({'path': str(path.resolve()), 'sha256': digest,
                     'bytes': size})
    out['artifacts'] = good
    out['digests'] = [item['sha256'] for item in good]
    if bad:
        out.update(_result(BLOCKED, 'ARTIFACT_INVALID', bad))
    else:
        out.update(_result(PASS, 'HASH_VERIFIED', []))
    return out


def _read_artifact_json(check: dict, code: str) -> dict | None:
    """Read the first hash-verified artifact as a JSON object, fail closed."""
    if check['status'] != PASS:
        return None
    path = Path(check['artifacts'][0]['path'])
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        check.update(_result(BLOCKED, code,
                             [f'{path.name}: unreadable/invalid JSON: {exc}']))
        return None
    if not isinstance(doc, dict):
        check.update(_result(BLOCKED, code,
                             [f'{path.name}: expected a JSON object']))
        return None
    return doc


def _check_report(check: dict, name: str) -> None:
    if check['status'] != PASS:
        return
    doc = _read_artifact_json(check, 'BAD_REPORT')
    if doc is None:
        return
    verdict = doc.get('verdict', doc.get('status'))
    token = norm_token(verdict).upper()
    if token != PASS:
        status = FAIL if token == FAIL else BLOCKED
        code = 'REPORT_FAILED' if status == FAIL else 'UNPROVEN_REPORT'
        check.update(_result(status, code,
                             [f'{name}: report verdict is '
                              f'{verdict or "absent"}, not PASS']))
        return
    method = doc.get('tool', doc.get('method'))
    if not isinstance(method, str) or not method.strip():
        check.update(_result(BLOCKED, 'NO_METHOD',
                             [f'{name}: report names no tool/method']))


def _check_binary(check: dict) -> None:
    if check['status'] != PASS:
        return
    path = Path(check['artifacts'][0]['path'])
    try:
        head = path.read_bytes()[:32]
    except OSError as exc:
        check.update(_result(BLOCKED, 'BAD_MACHO_HEADER',
                             [f'{path.name}: unreadable: {exc}']))
        return
    if len(head) < 32 or head[:4] != MH_MAGIC_64_LE:
        check.update(_result(BLOCKED, 'BAD_MACHO_HEADER',
                             [f'{path.name}: not a 64-bit little-endian Mach-O']))
        return
    cputype, _cpusubtype, filetype = struct.unpack_from('<III', head, 4)
    if cputype != CPU_TYPE_ARM64:
        check.update(_result(BLOCKED, 'NOT_ARM64',
                             [f'{path.name}: cputype 0x{cputype:x} is not '
                              f'CPU_TYPE_ARM64 (0x{CPU_TYPE_ARM64:x})']))
        return
    if filetype != MH_EXECUTE:
        check.update(_result(BLOCKED, 'NOT_EXECUTABLE',
                             [f'{path.name}: Mach-O filetype {filetype} is not '
                              f'MH_EXECUTE ({MH_EXECUTE})']))


def _check_scenario(check: dict, binary_digest: str | None,
                    section: Any) -> None:
    if check['status'] != PASS:
        return
    doc = _read_artifact_json(check, 'BAD_SCENARIO')
    if doc is None:
        return
    verdict = doc.get('verdict', doc.get('status'))
    token = norm_token(verdict).upper()
    if token != PASS:
        status = FAIL if token == FAIL else BLOCKED
        code = 'SCENARIO_FAILED' if status == FAIL else 'UNPROVEN_SCENARIO'
        check.update(_result(status, code,
                             [f'scenario verdict is {verdict or "absent"}, '
                              'not PASS']))
        return
    if doc.get('executed') is not True:
        check.update(_result(BLOCKED, 'SCENARIO_NOT_EXECUTED',
                             ['scenario does not record executed: true']))
        return
    oracle = norm_token(doc.get('oracle'))
    if oracle in NO_ORACLES or oracle not in REAL_ORACLES:
        check.update(_result(BLOCKED, 'BAD_ORACLE',
                             [f'scenario oracle {doc.get("oracle")!r} is not '
                              'an executed native macOS/arm64 process']))
        return
    reported = doc.get('binary_sha256')
    if not is_sha256(reported) or binary_digest is None \
            or reported.lower() != binary_digest.lower():
        check.update(_result(BLOCKED, 'SCENARIO_HASH_MISMATCH',
                             ['scenario binary_sha256 does not match the '
                              'hash-verified built binary']))
        return
    if isinstance(section, dict) and 'binary_sha256' in section:
        declared = section['binary_sha256']
        if not is_sha256(declared) or declared.lower() != binary_digest.lower():
            check.update(_result(BLOCKED, 'SCENARIO_HASH_MISMATCH',
                                 ['manifest scenario binary_sha256 conflicts '
                                  'with the hash-verified built binary']))


def _check_target(manifest: dict, checks: dict) -> None:
    """Optional advisory target fields become hard checks when supplied."""
    def reject(name: str, value: Any, allowed: frozenset[str], code: str) -> None:
        if value is None:
            return
        token = norm_token(value)
        if token and token not in allowed:
            checks[name].update(_result(BLOCKED, code,
                                        [f'{name}: unexpected {value!r}']))

    source = manifest.get('source_os', manifest.get('input_os'))
    target_os = manifest.get('target_os')
    target_arch = manifest.get('target_arch')
    reject('elf_lift', source, LINUX_TOKENS, 'WRONG_SOURCE_OS')
    reject('scenario', target_os, MACOS_TOKENS, 'WRONG_TARGET_OS')
    reject('scenario', target_arch, ARM64_TOKENS, 'WRONG_TARGET_ARCH')


def _blocked_all(reasons: list[str], status: str = NOT_TESTED) -> dict:
    return {
        'schema_version': SCHEMA_VERSION,
        'generator': GENERATOR,
        'status': status,
        'gate_allowed': False,
        'rule': RULE,
        'checks': {name: _result(status, 'NO_EVALUATION', [])
                   for name in REQUIREMENTS},
        'reasons': list(reasons),
    }


def evaluate(manifest: dict, base: Path) -> dict:
    """Pure decision function over one parsed manifest and its evidence dir."""
    checks = {name: verify_section(name, manifest.get(name), base)
              for name in REQUIREMENTS}
    for name in REPORT_REQUIREMENTS:
        _check_report(checks[name], name)
    _check_binary(checks['binary'])
    binary_digest = (checks['binary']['digests'][0]
                     if checks['binary']['status'] == PASS else None)
    _check_scenario(checks['scenario'], binary_digest,
                    manifest.get('scenario'))
    _check_target(manifest, checks)

    reasons = [f'{name}: {reason}'
               for name in REQUIREMENTS
               for reason in checks[name]['reasons']]
    current = [check['status'] for check in checks.values()]
    status = GO if all(item == PASS for item in current) else next(
        (item for item in _PRECEDENCE if item in current), BLOCKED)
    return {
        'schema_version': SCHEMA_VERSION,
        'generator': GENERATOR,
        'status': status,
        'gate_allowed': status == GO,
        'rule': RULE,
        'evidence_dir': str(base),
        'checks': checks,
        'reasons': reasons,
        'hashes': {name: checks[name]['digests']
                   for name in REQUIREMENTS},
    }


def _emit(result: dict, out_text: str | None) -> None:
    text = json.dumps(result, indent=1, sort_keys=True) + '\n'
    if out_text:
        out = Path(out_text).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding='utf-8')
    print(text, end='')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True,
                        help='CLI-supplied JSON manifest (all artifact paths '
                             'are relative to this file)')
    parser.add_argument('--json', dest='out_json', default=None,
                        help='write the decision JSON to this path')
    args = parser.parse_args(argv)
    try:
        manifest, base = load_manifest(args.evidence)
    except LoadError as exc:
        result = _blocked_all([str(exc)])
    else:
        result = evaluate(manifest, base)
    try:
        _emit(result, args.out_json)
    except OSError as exc:
        print(f'CANNOT_WRITE_DECISION: {args.out_json}: {exc}', file=sys.stderr)
        return 3
    return 0 if result['gate_allowed'] else 3


if __name__ == '__main__':
    sys.exit(main())
