"""Fail-closed tests for the Issue #138 ELF -> macOS/arm64 feasibility gate.

All artifacts here are synthetic and exist only under ``tmp_path``. They prove
the *decision mechanics*, not that Linux ELF -> macOS Mach-O translation works:

  * flags alone never open the gate (hashed artifacts are mandatory);
  * a tampered or missing artifact blocks it;
  * a fake/non-arm64 Mach-O, an unexecuted/dummy oracle and a mismatched
    scenario binary hash are all refused;
  * a complete, internally consistent synthetic evidence set does open it.

Run with: python3 -m pytest tests/test_elf_to_macos_gate.py -q
"""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tools.elf_to_macos_gate import (
    BLOCKED,
    DEFERRED,
    FAIL,
    GO,
    NOT_TESTED,
    PASS,
    REQUIREMENTS,
    evaluate,
    load_manifest,
    main,
)

ROOT = Path(__file__).resolve().parents[1]
FLAG_ONLY_KEYS = ('proven', 'supported', 'executed', 'complete', 'verified')


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mach_header(cputype: int = 0x0100000C, filetype: int = 2) -> bytes:
    """A minimal 64-bit little-endian Mach-O header (not a runnable program)."""
    data = bytearray(64)
    data[0:4] = b'\xcf\xfa\xed\xfe'  # MH_MAGIC_64, little-endian
    struct.pack_into('<III', data, 4, cputype, 3, filetype)
    return bytes(data)


def _evidence(tmp_path, *, binary: bytes | None = None,
              scenario: dict | None = None,
              report_patch: dict | None = None):
    """Write a complete, self-consistent synthetic evidence set."""
    base = Path(tmp_path)
    art = base / 'art'
    art.mkdir(parents=True, exist_ok=True)
    binary = _mach_header() if binary is None else binary
    bin_path = art / 'hello-arm64'
    bin_path.write_bytes(binary)
    bin_sha = _sha(binary)

    scenario_obj = {
        'verdict': PASS,
        'executed': True,
        'oracle': 'macos-arm64-native-process',
        'binary_sha256': bin_sha,
        'scenario': 'hello-arm64-stdout',
    }
    if scenario is not None:
        scenario_obj.update(scenario)
    scenario_path = art / 'scenario.json'
    scenario_path.write_text(json.dumps(scenario_obj), encoding='utf-8')

    reports = {
        'elf_lift': {'status': PASS, 'tool': 'synthetic-elf-lift'},
        'macho_writer': {'status': PASS, 'tool': 'synthetic-macho-writer'},
        'abi_syscall_map': {'status': PASS, 'tool': 'synthetic-abi-mapper'},
    }
    for name, patch in (report_patch or {}).items():
        reports[name].update(patch)

    manifest: dict = {
        'source_os': 'linux',
        'target_os': 'macos',
        'target_arch': 'arm64',
    }
    for name, doc in reports.items():
        path = art / f'{name}.json'
        path.write_text(json.dumps(doc), encoding='utf-8')
        manifest[name] = {'artifacts': [
            {'path': f'art/{path.name}', 'sha256': _sha(path.read_bytes())}]}
    manifest['binary'] = {'artifacts': [
        {'path': f'art/{bin_path.name}', 'sha256': bin_sha}]}
    manifest['scenario'] = {'artifacts': [
        {'path': f'art/{scenario_path.name}',
         'sha256': _sha(scenario_path.read_bytes())}]}

    manifest_path = base / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    return manifest_path, {
        'manifest_path': manifest_path,
        'manifest': manifest,
        'art': art,
        'binary': bin_path,
        'binary_sha': bin_sha,
        'scenario': scenario_path,
        'scenario_sha': _sha(scenario_path.read_bytes()),
    }


def _decide(manifest_path: Path) -> dict:
    manifest, base = load_manifest(str(manifest_path))
    return evaluate(manifest, base)


def _run_cli(capsys, argv) -> tuple[int, dict]:
    rc = main(argv)
    out = capsys.readouterr().out
    return rc, json.loads(out)


# --- absent evidence --------------------------------------------------------

def test_missing_manifest_is_not_tested(tmp_path, capsys):
    rc, result = _run_cli(capsys, ['--evidence', str(tmp_path / 'absent.json')])
    assert rc != 0
    assert result['status'] == NOT_TESTED
    assert result['gate_allowed'] is False
    assert result['reasons'][0].startswith('MISSING_INPUT:')
    assert set(result['checks']) == set(REQUIREMENTS)
    assert all(check['status'] == NOT_TESTED
               for check in result['checks'].values())


def test_manifest_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert 'required' in capsys.readouterr().err


def test_empty_manifest_is_deferred(tmp_path):
    path = tmp_path / 'empty.json'
    path.write_text('{}', encoding='utf-8')
    result = _decide(path)
    assert result['status'] == DEFERRED
    assert result['gate_allowed'] is False
    assert all(result['checks'][name]['code'] == 'NO_EVIDENCE'
               for name in REQUIREMENTS)


# --- flags are not evidence -------------------------------------------------

def test_flags_alone_cannot_pass(tmp_path, capsys):
    manifest = {name: {key: True for key in FLAG_ONLY_KEYS}
                for name in REQUIREMENTS}
    path = tmp_path / 'flags.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')

    result = _decide(path)
    assert result['status'] != PASS
    assert result['status'] != GO
    assert result['gate_allowed'] is False
    assert all(result['checks'][name]['code'] == 'FLAG_ONLY_CLAIM'
               for name in REQUIREMENTS)
    assert all(result['checks'][name]['artifacts'] == []
               for name in REQUIREMENTS)

    rc, cli_result = _run_cli(capsys, ['--evidence', str(path)])
    assert rc == 3
    assert cli_result['status'] == BLOCKED


def test_flags_alongside_evidence_do_not_mask_bad_binary(tmp_path):
    path, _ = _evidence(tmp_path,
                        binary=b'this is not a Mach-O at all' + b'\0' * 64)
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['binary']['proven'] = True
    doc['scenario']['verified'] = True
    path.write_text(json.dumps(doc), encoding='utf-8')

    result = _decide(path)
    assert result['gate_allowed'] is False
    assert result['checks']['binary']['code'] == 'BAD_MACHO_HEADER'


# --- hash integrity ---------------------------------------------------------

def test_tampered_binary_hash_is_refused(tmp_path):
    path, info = _evidence(tmp_path)
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['binary']['artifacts'][0]['sha256'] = '0' * 64
    path.write_text(json.dumps(doc), encoding='utf-8')

    result = _decide(path)
    assert result['status'] == BLOCKED
    assert result['gate_allowed'] is False
    check = result['checks']['binary']
    assert check['status'] == BLOCKED
    assert check['code'] == 'ARTIFACT_INVALID'
    assert any('sha256 mismatch (tampered)' in reason
               for reason in check['reasons'])
    assert check['digests'] == [], 'a tampered artifact must not be counted'


def test_tampered_scenario_hash_is_refused(tmp_path):
    path, info = _evidence(tmp_path)
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['scenario']['artifacts'][0]['sha256'] = 'f' * 64
    path.write_text(json.dumps(doc), encoding='utf-8')

    result = _decide(path)
    assert result['status'] == BLOCKED
    assert result['checks']['scenario']['code'] == 'ARTIFACT_INVALID'


def test_missing_artifact_is_blocked(tmp_path):
    path, info = _evidence(tmp_path)
    info['binary'].unlink()
    result = _decide(path)
    assert result['status'] == BLOCKED
    assert any('artifact missing' in reason
               for reason in result['checks']['binary']['reasons'])


def test_invalid_declared_hash_is_blocked(tmp_path):
    path, _ = _evidence(tmp_path)
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['elf_lift']['artifacts'][0]['sha256'] = 'not-a-sha256'
    path.write_text(json.dumps(doc), encoding='utf-8')
    result = _decide(path)
    assert result['status'] == BLOCKED
    assert 'invalid declared sha256' in result['checks']['elf_lift']['reasons'][0]


# --- forged binary / ABI / scenario claims ----------------------------------

def test_fake_binary_is_refused(tmp_path):
    path, _ = _evidence(tmp_path, binary=b'\x7fELF' + b'\0' * 60)
    result = _decide(path)
    assert result['status'] == BLOCKED
    assert result['checks']['binary']['code'] == 'BAD_MACHO_HEADER'


def test_wrong_arch_macho_is_refused(tmp_path):
    # x86_64 Mach-O (cputype 0x01000007), not arm64.
    path, _ = _evidence(tmp_path,
                        binary=_mach_header(cputype=0x01000007))
    result = _decide(path)
    assert result['checks']['binary']['code'] == 'NOT_ARM64'
    assert result['gate_allowed'] is False


def test_dylib_is_not_a_built_executable(tmp_path):
    path, _ = _evidence(tmp_path, binary=_mach_header(filetype=6))
    result = _decide(path)
    assert result['checks']['binary']['code'] == 'NOT_EXECUTABLE'


@pytest.mark.parametrize('oracle', ['dummy', 'headless', 'build-only',
                                    'not-executed', ''])
def test_unexecuted_or_dummy_oracle_is_refused(tmp_path, oracle):
    path, _ = _evidence(tmp_path, scenario={'oracle': oracle})
    result = _decide(path)
    assert result['status'] == BLOCKED
    assert result['checks']['scenario']['code'] == 'BAD_ORACLE'


def test_scenario_without_execution_is_refused(tmp_path):
    path, _ = _evidence(tmp_path, scenario={'executed': False})
    result = _decide(path)
    assert result['checks']['scenario']['code'] == 'SCENARIO_NOT_EXECUTED'


def test_scenario_binary_hash_mismatch_is_refused(tmp_path):
    path, _ = _evidence(tmp_path, scenario={'binary_sha256': 'a' * 64})
    result = _decide(path)
    assert result['status'] == BLOCKED
    assert result['checks']['scenario']['code'] == 'SCENARIO_HASH_MISMATCH'


def test_failed_scenario_is_distinct_from_blocked(tmp_path):
    path, _ = _evidence(tmp_path, scenario={'verdict': FAIL})
    result = _decide(path)
    assert result['status'] == FAIL
    assert result['checks']['scenario']['code'] == 'SCENARIO_FAILED'
    assert result['gate_allowed'] is False


def test_unproven_report_is_refused(tmp_path):
    path, _ = _evidence(tmp_path, report_patch={
        'elf_lift': {'status': 'BUILD ONLY', 'tool': ''}})
    result = _decide(path)
    assert result['status'] == BLOCKED
    assert result['checks']['elf_lift']['code'] == 'UNPROVEN_REPORT'


def test_wrong_target_is_refused(tmp_path):
    path, info = _evidence(tmp_path)
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['target_arch'] = 'x86_64'
    path.write_text(json.dumps(doc), encoding='utf-8')
    result = _decide(path)
    assert result['status'] == BLOCKED
    assert result['checks']['scenario']['code'] == 'WRONG_TARGET_ARCH'


# --- complete synthetic evidence -------------------------------------------

def test_complete_synthetic_evidence_opens_gate(tmp_path):
    path, info = _evidence(tmp_path)
    result = _decide(path)
    assert result['status'] == GO
    assert result['gate_allowed'] is True
    assert all(result['checks'][name]['status'] == PASS
               for name in REQUIREMENTS)
    assert result['hashes']['binary'] == [info['binary_sha']]
    assert result['hashes']['scenario'] == [info['scenario_sha']]
    assert result['reasons'] == []


def test_cli_exit_codes_and_decision_file(tmp_path, capsys):
    good, _ = _evidence(tmp_path / 'good')
    decision = tmp_path / 'decision.json'
    rc, result = _run_cli(capsys, ['--evidence', str(good),
                                   '--json', str(decision)])
    assert rc == 0
    assert result['gate_allowed'] is True
    assert json.loads(decision.read_text(encoding='utf-8')) == result

    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps({name: {key: True for key in FLAG_ONLY_KEYS}
                               for name in REQUIREMENTS}), encoding='utf-8')
    rc, result = _run_cli(capsys, ['--evidence', str(bad)])
    assert rc == 3
    assert result['gate_allowed'] is False


def test_subprocess_module_entrypoint(tmp_path):
    """The documented ``python3 -m`` invocation uses the same exit codes."""
    good, _ = _evidence(tmp_path / 'good')
    proc = subprocess.run(
        [sys.executable, '-m', 'tools.elf_to_macos_gate',
         '--evidence', str(good)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stdout
    assert json.loads(proc.stdout)['status'] == GO
