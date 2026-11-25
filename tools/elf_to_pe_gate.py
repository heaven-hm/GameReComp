#!/usr/bin/env python3
"""Fail-closed feasibility gate: Linux ELF -> Windows PE (issue #137).

This is a *decision tool*, not an implementation and not a claim of support.
It reads exactly one CLI-supplied JSON evidence document and decides whether
the five components required by issue #137 are proven:

  * ``elf_reader``       - a hash-checked ELF reader implementation + audit
  * ``pe_writer``        - a hash-checked PE writer implementation + audit
  * ``win32_runtime``    - a hash-checked Win32 runtime implementation + audit
  * ``built_pe``         - a real, hash-checked PE produced from that pipeline
  * ``executed_scenario`` - a hash-checked proof that a scenario really ran

Every artifact and every nested audit/scenario proof must be a real file whose
declared SHA-256 matches its bytes. Booleans and self-reported verdicts are
*never* evidence: ``{"elf_reader": true}`` is BLOCKED, not PASS. The PE is
structurally parsed (MZ, PE signature, known machine, valid optional header
and section table); a text file that merely claims to be a PE is BLOCKED. The
scenario proof must bind to the *actual* digest of the built PE, carry a named
oracle and report a successful exit. Missing capabilities are DEFERRED (the
roadmap explicitly forbids duplicating a loader/runtime); supplied but invalid
or unproven capabilities are BLOCKED; malformed/missing CLI inputs are NOT
TESTED. FAIL/BLOCKED/NOT TESTED/DEFERRED stay distinct.

A PASS verdict means only "the supplied evidence is internally consistent".
It does not implement, integrate or close anything: ``issue_closed`` is always
false. Semantic correctness of a reader/writer/runtime cannot be proven by a
hash and remains a human/real-scenario review item.

Input schema (all paths are resolved only from this CLI-supplied document):

  {
    "issue": 137,                            # optional, checked when present
    "direction": "linux-elf-to-windows-pe",  # optional, checked when present
    "elf_reader":    <artifact>,
    "pe_writer":     <artifact>,
    "win32_runtime": <artifact>,
    "built_pe":      <artifact>,
    "executed_scenario": <artifact>
  }

  <artifact> = {"path": "<file>", "sha256": "<64 hex>"}

  The three capability rows additionally carry a nested ``proof`` object:
    "proof": {"path": "<audit json>", "sha256": "<64 hex>"}
  whose JSON body must contain:
    {"kind": "<same component name>", "verdict": "PASS",
     "oracle": "<named audit oracle>", "license": "<non-empty licence>",
     "artifact_sha256": "<digest of the implementation file>"}

  ``executed_scenario``'s file must be JSON containing:
    {"verdict": "PASS", "oracle": "<named oracle>", "scenario": "<name>",
     "source_os": "linux", "target_os": "windows", "exit_code": 0,
     "built_pe_sha256": "<digest of the built PE file>"}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

PASS = 'PASS'
DEFERRED = 'DEFERRED'
BLOCKED = 'BLOCKED'
NOT_TESTED = 'NOT TESTED'

CAPABILITIES = ('elf_reader', 'pe_writer', 'win32_runtime')
ITEMS = CAPABILITIES + ('built_pe', 'executed_scenario')
RULE = ('PASS requires five hash-checked components, a structurally real PE and '
        'an oracle-backed scenario bound to that PE; flags alone never qualify')
_HEX = set('0123456789abcdefABCDEF')
_NO_ORACLE = {'', 'none', 'missing', 'n/a', 'na', 'unproven', 'partial',
              'not tested', 'self-reported', NOT_TESTED.lower()}
_MACHINES = {0x014C: 'i386', 0x8664: 'x86-64', 0x01C0: 'arm',
             0xAA64: 'arm64', 0x0200: 'ia64'}


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
    return isinstance(value, str) and value.strip().lower() not in _NO_ORACLE


def _checked_file(row, label: str, reasons: list[str]):
    """Validate one artifact row and return ``(status, digest, bytes)``.

    A missing row is DEFERRED (roadmap-only, do-not-integrate); a present row
    that is not hash-checked evidence is BLOCKED. No fallback path is ever
    invented and no digest is computed in place of a missing declaration.
    """
    if row is None or row == {} or row == []:
        reasons.append(f'{label}: absent (no implementation claimed)')
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
    if not data.strip():
        reasons.append(f'{label}: artifact is empty: {path.name}')
        return BLOCKED, None, None
    return PASS, actual, data


def _load_proof(row: dict, label: str, reasons: list[str]) -> dict | None:
    """Hash-check and parse a nested proof document referenced by a row."""
    proof = row.get('proof')
    if not isinstance(proof, dict):
        reasons.append(f'{label}: no audit proof (unproven capability)')
        return None
    text = proof.get('path')
    if not isinstance(text, str) or not text.strip():
        reasons.append(f'{label}: audit proof has no path')
        return None
    declared = proof.get('sha256')
    if not _is_hash(declared):
        reasons.append(f'{label}: invalid audit proof sha256')
        return None
    path = Path(text)
    if not path.is_file():
        reasons.append(f'{label}: audit proof missing: {path.name}')
        return None
    try:
        if sha256(path).lower() != declared.lower():
            reasons.append(f'{label}: audit proof hash mismatch (tampered)')
            return None
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        reasons.append(f'{label}: audit proof unreadable/invalid JSON')
        return None
    if not isinstance(doc, dict):
        reasons.append(f'{label}: audit proof is not an object')
        return None
    return doc


def _check_capability(label: str, row, reasons: list[str], checks: dict) -> None:
    status, digest, _ = _checked_file(row, label, reasons)
    if status != PASS:
        checks[label] = status
        return
    doc = _load_proof(row, label, reasons)
    if doc is None:
        checks[label] = BLOCKED
        return
    bad: list[str] = []
    if doc.get('kind') != label:
        bad.append(f'{label}: audit proof is for {doc.get("kind")!r}')
    if doc.get('verdict') != PASS:
        bad.append(f'{label}: audit verdict is not PASS')
    if not _named(doc.get('oracle')):
        bad.append(f'{label}: audit has no named oracle')
    if not _named(doc.get('license')):
        bad.append(f'{label}: licence audit missing (GATE 1)')
    if not _is_hash(doc.get('artifact_sha256')) \
            or doc['artifact_sha256'].lower() != digest.lower():
        bad.append(f'{label}: audit proof does not bind to the artifact hash')
    if bad:
        reasons.extend(bad)
        checks[label] = BLOCKED
    else:
        checks[label] = PASS


def _pe_info(data: bytes) -> tuple[dict | None, str]:
    """Structurally validate a PE image without executing it."""
    if len(data) < 64:
        return None, 'PE too small for a DOS header'
    if data[:2] != b'MZ':
        return None, 'missing MZ magic (not a PE)'
    pe_off = struct.unpack_from('<I', data, 0x3C)[0]
    if pe_off < 64 or pe_off + 24 > len(data):
        return None, f'invalid e_lfanew ({pe_off})'
    if data[pe_off:pe_off + 4] != b'PE\x00\x00':
        return None, 'missing PE signature'
    coff = pe_off + 4
    machine, sections, _, _, _, opt_size, _ = struct.unpack_from(
        '<HHIIIHH', data, coff)
    if machine not in _MACHINES:
        return None, f'unknown PE machine 0x{machine:04x}'
    if sections == 0:
        return None, 'PE declares no sections'
    if opt_size < 2:
        return None, 'PE optional header too small'
    opt = coff + 20
    if opt + opt_size > len(data):
        return None, 'PE optional header truncated'
    magic = struct.unpack_from('<H', data, opt)[0]
    if magic == 0x10B:
        pe_type = 'PE32'
    elif magic == 0x20B:
        pe_type = 'PE32+'
    else:
        return None, f'unknown optional header magic 0x{magic:04x}'
    section_table = opt + opt_size
    if section_table + sections * 40 > len(data):
        return None, 'PE section table truncated'
    return {'machine': _MACHINES[machine], 'pe_type': pe_type,
            'sections': sections, 'bytes': len(data)}, ''


def _check_built_pe(row, reasons: list[str], checks: dict) -> str | None:
    """Validate the built PE and return its real digest when proven."""
    status, digest, data = _checked_file(row, 'built_pe', reasons)
    if status != PASS:
        checks['built_pe'] = status
        return None
    info, err = _pe_info(data)
    if info is None:
        reasons.append(f'built_pe: {err}')
        checks['built_pe'] = BLOCKED
        return None
    checks['built_pe'] = PASS
    return digest


def _check_scenario(row, pe_digest: str | None, reasons: list[str],
                    checks: dict) -> None:
    status, digest, data = _checked_file(row, 'executed_scenario', reasons)
    if status != PASS:
        checks['executed_scenario'] = status
        return
    try:
        doc = json.loads(data.decode('utf-8'))
    except (UnicodeError, ValueError):
        reasons.append('executed_scenario: proof is not valid JSON')
        checks['executed_scenario'] = BLOCKED
        return
    if not isinstance(doc, dict):
        reasons.append('executed_scenario: proof is not an object')
        checks['executed_scenario'] = BLOCKED
        return
    bad: list[str] = []
    if doc.get('verdict') != PASS:
        bad.append('executed_scenario: verdict is not PASS')
    if not _named(doc.get('oracle')):
        bad.append('executed_scenario: no named oracle')
    if not isinstance(doc.get('scenario'), str) or not doc['scenario'].strip():
        bad.append('executed_scenario: no scenario name')
    if doc.get('source_os') != 'linux' or doc.get('target_os') != 'windows':
        bad.append('executed_scenario: both sides (linux/windows) not recorded')
    if doc.get('exit_code') != 0:
        bad.append('executed_scenario: scenario did not exit successfully')
    bound = doc.get('built_pe_sha256')
    if not _is_hash(bound):
        bad.append('executed_scenario: no built_pe_sha256 binding')
    elif pe_digest is None:
        bad.append('executed_scenario: no proven PE to bind the scenario to')
    elif bound.lower() != pe_digest.lower():
        bad.append('executed_scenario: built_pe_sha256 does not match the PE')
    if bad:
        reasons.extend(bad)
        checks['executed_scenario'] = BLOCKED
    else:
        checks['executed_scenario'] = PASS


def _decision(status: str, checks: dict, reasons: list[str]) -> dict:
    return {
        'status': status,
        'feasible': status == PASS,
        'issue_closed': False,
        'checks': {name: checks.get(name, NOT_TESTED) for name in ITEMS},
        'reasons': reasons,
        'rule': RULE,
    }


def evaluate(evidence: dict) -> dict:
    """Pure decision function; reads only paths supplied in the evidence doc."""
    reasons: list[str] = []
    checks: dict[str, str] = {}
    if evidence.get('issue', 137) != 137:
        reasons.append('evidence: issue is not #137')
    if evidence.get('direction', 'linux-elf-to-windows-pe') \
            != 'linux-elf-to-windows-pe':
        reasons.append('evidence: direction is not linux-elf-to-windows-pe')
    if reasons:
        return _decision(NOT_TESTED, checks, reasons)

    for label in CAPABILITIES:
        _check_capability(label, evidence.get(label), reasons, checks)
    pe_digest = _check_built_pe(evidence.get('built_pe'), reasons, checks)
    _check_scenario(evidence.get('executed_scenario'), pe_digest, reasons, checks)

    if BLOCKED in checks.values():
        status = BLOCKED
    elif DEFERRED in checks.values():
        status = DEFERRED
    elif all(checks.get(name) == PASS for name in ITEMS):
        status = PASS
    else:
        status = NOT_TESTED
    return _decision(status, checks, reasons)


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
    return 0 if result['status'] == PASS else 3


if __name__ == '__main__':
    sys.exit(main())
