"""Tests for the fail-closed controller/raw-input capability gate (issue #11).

Run with: python3 -m pytest tests/test_controller_capability_audit.py -q

Every device, artifact and log here is synthetic and written under ``tmp_path``;
nothing probes the host or runs a game. The cases prove that a SUPPORTED verdict
needs a real (non-synthetic) device *and* a matching real-hardware selftest,
that absent/synthetic devices and registration-only claims never produce a
fake PASS, and that tampering with any hash or binding is detected.

The hand-written records are logic fixtures only. They are not evidence that
any physical controller or in-game behaviour exists.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from tools.controller_capability_audit import APIS, BLOCKED, DEFERRED, \
    NOT_TESTED, SUPPORTED, UNSUPPORTED, evaluate, main, parse_log_text

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'controller_capability_audit.py'
HOST = 'audit-host'


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, doc: object) -> None:
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n',
                    encoding='utf-8')


def _log_text(checks: list[dict]) -> str:
    lines = []
    for case in checks:
        if case['status'] == 'skip':
            lines.append(f"skip {case['name']} ({case['reason']})")
        else:
            lines.append(f"{case['status']} {case['name']}")
    return '\n'.join(lines) + '\n'


def _bundle(root: Path, *, apis=APIS, with_devices: bool = True,
            with_tests: bool = True, host: str = HOST) -> dict:
    """Create one complete (or deliberately incomplete) CLI evidence bundle."""
    root.mkdir(parents=True, exist_ok=True)
    inventory: dict = {'schema_version': 1, 'issue': 11, 'host': host,
                       'devices': []}
    selftests: dict = {'schema_version': 1, 'issue': 11, 'records': []}
    for api in apis:
        evidence = root / f'enumeration-{api}.txt'
        evidence.write_text(f'physical enumeration record for {api}\n',
                            encoding='utf-8')
        capabilities = (['force_feedback'] if api == 'force-feedback'
                        else ['generic-input'])
        if with_devices:
            inventory['devices'].append({
                'id': f'device-{api}', 'api': api, 'kind': 'gamepad',
                'real': True, 'enumeration': 'iohid',
                'capabilities': capabilities,
                'evidence': {'path': evidence.name, 'sha256': _sha(evidence)},
            })
        checks = [{'name': f'{api}-enumerate', 'status': 'ok'},
                  {'name': f'{api}-state', 'status': 'ok'}]
        log = root / f'selftest-{api}.log'
        log.write_text(_log_text(checks), encoding='utf-8')
        if with_tests:
            selftests['records'].append({
                'api': api, 'device_id': f'device-{api}', 'host': host,
                'adapter': 'real-hardware', 'verdict': 'PASS', 'exit_code': 0,
                'checks': checks,
                'log': {'path': log.name, 'sha256': _sha(log)},
            })
    inv_path, test_path = root / 'inventory.json', root / 'selftests.json'
    _json(inv_path, inventory)
    _json(test_path, selftests)
    return {'root': root, 'inventory': inv_path, 'selftests': test_path,
            'inv': inventory, 'tests': selftests, 'json': root / 'decision.json'}


def _write(bundle: dict) -> None:
    _json(bundle['inventory'], bundle['inv'])
    _json(bundle['selftests'], bundle['tests'])


def _run(bundle: dict, *, cli: bool = True):
    argv = ['--inventory', str(bundle['inventory']),
            '--selftests', str(bundle['selftests']),
            '--json', str(bundle['json'])]
    if cli:
        done = subprocess.run(
            [sys.executable, '-m', 'tools.controller_capability_audit', *argv],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=60)
        raw = (bundle['json'].read_text(encoding='utf-8')
               if bundle['json'].is_file() else done.stdout)
        return done.returncode, json.loads(raw), done
    code = main(argv)
    return code, json.loads(bundle['json'].read_text(encoding='utf-8')), None


def _verdict(result: dict, api: str) -> str:
    return result['per_api'][api]['status']


def _has_reason(result: dict, api: str, code: str) -> bool:
    return any(code in reason for reason in result['per_api'][api]['reasons'])


# --- complete, self-consistent evidence is the only path to SUPPORTED -------

def test_complete_bundle_supports_every_audited_api(tmp_path):
    bundle = _bundle(tmp_path / 'complete')
    code, result, done = _run(bundle, cli=True)
    assert code == 0, (done.stderr if done else '')
    assert result['status'] == SUPPORTED and result['supported'] is True
    assert result['supported_apis'] == list(APIS)
    assert result['issue_closed'] is False
    for api in APIS:
        row = result['per_api'][api]
        assert row['status'] == SUPPORTED
        assert row['device_status'] == row['selftest_status'] == 'PASS'
        assert row['test_device_id'] == f'device-{api}'
        assert row['log_sha256'] == _sha(bundle['root'] / f'selftest-{api}.log')
        assert row['check_counts'] == {'ok': 2, 'FAIL': 0, 'skip': 0}
        assert _has_reason(result, api, 'SUPPORTED: physical device')


def test_every_refusal_carries_an_exact_reason(tmp_path):
    """A partial inventory must still explain every non-supported API."""
    bundle = _bundle(tmp_path / 'partial', apis=('xinput',))
    code, result, _ = _run(bundle)
    assert code != 0
    assert result['supported_apis'] == ['xinput']
    assert result['status'] == DEFERRED
    for api in APIS:
        row = result['per_api'][api]
        if row['supported']:
            continue
        assert row['reasons']
        assert all(reason.startswith(f'{api}: ') for reason in row['reasons'])
        assert any(code in reason for reason in row['reasons']
                   for code in ('API_DEVICE_ABSENT', 'API_SELFTEST_ABSENT'))


def test_repeated_cli_runs_are_byte_identical(tmp_path):
    bundle = _bundle(tmp_path / 'repro')
    first = bundle['root'] / 'first.json'
    second = bundle['root'] / 'second.json'
    for target in (first, second):
        done = subprocess.run(
            [sys.executable, '-m', 'tools.controller_capability_audit',
             '--inventory', str(bundle['inventory']),
             '--selftests', str(bundle['selftests']), '--json', str(target)],
            cwd=ROOT, capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stderr
    assert first.read_bytes() == second.read_bytes()
    assert first.read_text() == json.dumps(json.loads(first.read_text()),
                                           indent=2, sort_keys=True,
                                           ensure_ascii=True) + '\n'


# --- absent devices / tests are DEFERRED, never a fabricated PASS ----------

def test_absent_devices_are_deferred(tmp_path):
    bundle = _bundle(tmp_path / 'no-devices', with_devices=False,
                     with_tests=False)
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert result['supported'] is False and result['supported_apis'] == []
    for api in APIS:
        assert _verdict(result, api) == DEFERRED
        assert _has_reason(result, api, 'API_DEVICE_ABSENT') or _has_reason(
            result, api, 'DEVICE_ID_MISMATCH')


def test_absent_selftests_are_deferred(tmp_path):
    bundle = _bundle(tmp_path / 'no-tests', with_tests=False)
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert result['status'] == DEFERRED
    for api in APIS:
        assert _verdict(result, api) == DEFERRED
        assert _has_reason(result, api, 'API_SELFTEST_ABSENT')


def test_registration_only_claims_never_support_an_api(tmp_path):
    bundle = _bundle(tmp_path / 'registration-only', apis=('dinput8',),
                     with_devices=False, with_tests=False)
    bundle['inv']['verdict'] = 'PASS'          # unbacked root claim
    bundle['inv']['devices'] = [{
        'api': 'dinput8', 'id': 'device-dinput8', 'registered': True}]
    bundle['tests']['records'] = [{
        'api': 'dinput8', 'verdict': 'PASS', 'registered': True}]
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert result['status'] == BLOCKED
    assert result['supported_apis'] == []
    assert _verdict(result, 'dinput8') != SUPPORTED
    assert _has_reason(result, 'dinput8', 'REGISTRATION_ONLY')
    assert any('UNBACKED_PASS_CLAIM_IGNORED' in reason
               for reason in result['reasons'])


# --- synthetic / non-physical devices are UNSUPPORTED ----------------------

def test_synthetic_devices_never_support_any_api(tmp_path):
    bundle = _bundle(tmp_path / 'synthetic', with_tests=False)
    for device in bundle['inv']['devices']:
        device['real'] = False
        device['synthetic'] = True
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert result['supported'] is False
    for api in APIS:
        assert _verdict(result, api) in (UNSUPPORTED, BLOCKED, DEFERRED)
        assert _verdict(result, api) != SUPPORTED
        assert _has_reason(result, api, 'SYNTHETIC_DEVICE_ONLY')


def test_virtual_enumeration_cannot_be_promoted_by_real_true(tmp_path):
    bundle = _bundle(tmp_path / 'virtual-spoof', apis=('xinput',))
    bundle['inv']['devices'][0]['real'] = True
    bundle['inv']['devices'][0]['enumeration'] = 'virtual-hid'
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'xinput') == UNSUPPORTED
    assert _has_reason(result, 'xinput', 'SYNTHETIC_DEVICE_ONLY')


def test_synthetic_test_adapter_is_blocked(tmp_path):
    bundle = _bundle(tmp_path / 'fake-adapter', apis=('hid',))
    bundle['tests']['records'][0]['adapter'] = 'synthetic'
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'hid') == BLOCKED
    assert _has_reason(result, 'hid', 'SYNTHETIC_SELFTEST_ADAPTER')


def test_missing_force_feedback_capability_is_unsupported(tmp_path):
    bundle = _bundle(tmp_path / 'no-ff', apis=('force-feedback',))
    bundle['inv']['devices'][0]['capabilities'] = ['generic-input']
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'force-feedback') == UNSUPPORTED
    assert _has_reason(result, 'force-feedback', 'NO_REAL_DEVICE')
    assert 'force_feedback' in ' '.join(
        result['per_api']['force-feedback']['reasons'])


def test_failed_real_selftest_reports_unsupported(tmp_path):
    bundle = _bundle(tmp_path / 'failed-test', apis=('raw-input',))
    record = bundle['tests']['records'][0]
    record['checks'] = [{'name': 'raw-input-enumerate', 'status': 'FAIL'}]
    record['verdict'] = 'FAIL'
    record['exit_code'] = 3
    log = bundle['root'] / 'selftest-raw-input.log'
    log.write_text(_log_text(record['checks']), encoding='utf-8')
    record['log']['sha256'] = _sha(log)
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'raw-input') == UNSUPPORTED
    assert _has_reason(result, 'raw-input', 'SELFTEST_FAILED')


def test_skipped_selftest_is_deferred(tmp_path):
    bundle = _bundle(tmp_path / 'skipped-test', apis=('dinput-joystick',))
    record = bundle['tests']['records'][0]
    record['checks'] = [{'name': 'dinput-joystick-haptics', 'status': 'skip',
                        'reason': 'no FF hardware on host'}]
    record['verdict'] = 'SKIP'
    record['exit_code'] = 0
    log = bundle['root'] / 'selftest-dinput-joystick.log'
    log.write_text(_log_text(record['checks']), encoding='utf-8')
    record['log']['sha256'] = _sha(log)
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'dinput-joystick') == DEFERRED
    assert _has_reason(result, 'dinput-joystick', 'SELFTEST_SKIPPED')


# --- tampering is detected -------------------------------------------------

def test_tampered_device_evidence_hash_is_blocked(tmp_path):
    bundle = _bundle(tmp_path / 'bad-device-hash', apis=('dinput8',))
    bundle['inv']['devices'][0]['evidence']['sha256'] = '0' * 64
    _write(bundle)
    code, result, _ = _run(bundle, cli=True)
    assert code != 0
    assert _verdict(result, 'dinput8') == BLOCKED
    assert _has_reason(result, 'dinput8', 'DEVICE_EVIDENCE_HASH_MISMATCH')


def test_device_evidence_changed_after_record_was_written_is_blocked(tmp_path):
    bundle = _bundle(tmp_path / 'changed-device', apis=('hid',))
    (bundle['root'] / 'enumeration-hid.txt').write_text('different device\n',
                                                        encoding='utf-8')
    code, result, _ = _run(bundle)
    assert code != 0
    assert _has_reason(result, 'hid', 'DEVICE_EVIDENCE_HASH_MISMATCH')


def test_tampered_log_hash_is_blocked(tmp_path):
    bundle = _bundle(tmp_path / 'bad-log-hash', apis=('xinput',))
    bundle['tests']['records'][0]['log']['sha256'] = 'f' * 64
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'xinput') == BLOCKED
    assert _has_reason(result, 'xinput', 'LOG_HASH_MISMATCH')


def test_extra_log_line_the_record_does_not_claim_is_blocked(tmp_path):
    bundle = _bundle(tmp_path / 'extra-log-line', apis=('xinput',))
    log = bundle['root'] / 'selftest-xinput.log'
    log.write_text(log.read_text() + 'ok xinput-extra-check\n', encoding='utf-8')
    bundle['tests']['records'][0]['log']['sha256'] = _sha(log)
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'xinput') == BLOCKED
    assert _has_reason(result, 'xinput', 'CHECK_LOG_MISMATCH')


def test_verdict_flipped_to_pass_over_failing_checks_is_blocked(tmp_path):
    bundle = _bundle(tmp_path / 'verdict-flip', apis=('raw-input',))
    record = bundle['tests']['records'][0]
    record['checks'] = [{'name': 'raw-input-enumerate', 'status': 'FAIL'}]
    log = bundle['root'] / 'selftest-raw-input.log'
    log.write_text(_log_text(record['checks']), encoding='utf-8')
    record['log']['sha256'] = _sha(log)
    # verdict stays PASS and exit_code stays 0: a forged PASS claim.
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'raw-input') == BLOCKED
    assert _has_reason(result, 'raw-input', 'VERDICT_CHECK_MISMATCH')


def test_selftest_bound_to_another_device_is_blocked(tmp_path):
    bundle = _bundle(tmp_path / 'wrong-device', apis=('dinput-joystick',))
    bundle['tests']['records'][0]['device_id'] = 'device-xinput'
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'dinput-joystick') == BLOCKED
    assert _has_reason(result, 'dinput-joystick', 'DEVICE_ID_MISMATCH')


def test_host_mismatch_is_blocked(tmp_path):
    bundle = _bundle(tmp_path / 'host-mismatch', apis=('hid',))
    bundle['tests']['records'][0]['host'] = 'another-host'
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'hid') == BLOCKED
    assert _has_reason(result, 'hid', 'HOST_MISMATCH')


def test_duplicate_selftest_record_is_blocked(tmp_path):
    bundle = _bundle(tmp_path / 'duplicate', apis=('dinput8',))
    bundle['tests']['records'].append(dict(bundle['tests']['records'][0]))
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'dinput8') == BLOCKED
    assert _has_reason(result, 'dinput8', 'DUPLICATE_SELFTEST_RECORD')


def test_generic_selftest_cannot_prove_an_api(tmp_path):
    bundle = _bundle(tmp_path / 'generic-check', apis=('xinput',))
    record = bundle['tests']['records'][0]
    record['checks'] = [{'name': 'generic-keyboard-poll', 'status': 'ok'}]
    log = bundle['root'] / 'selftest-xinput.log'
    log.write_text(_log_text(record['checks']), encoding='utf-8')
    record['log']['sha256'] = _sha(log)
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'xinput') == BLOCKED
    assert _has_reason(result, 'xinput', 'CHECK_NOT_API_SPECIFIC')


def test_malformed_log_line_is_blocked(tmp_path):
    bundle = _bundle(tmp_path / 'malformed-log', apis=('hid',))
    log = bundle['root'] / 'selftest-hid.log'
    log.write_text('ok hid-enumerate\nPASS hid-state\n', encoding='utf-8')
    bundle['tests']['records'][0]['log']['sha256'] = _sha(log)
    _write(bundle)
    code, result, _ = _run(bundle)
    assert code != 0
    assert _verdict(result, 'hid') == BLOCKED
    assert _has_reason(result, 'hid', 'LOG_LINE_INVALID')


# --- CLI input failures and pure-function use ------------------------------

def test_missing_cli_input_is_not_tested(tmp_path):
    done = subprocess.run(
        [sys.executable, '-m', 'tools.controller_capability_audit',
         '--inventory', str(tmp_path / 'missing.json'),
         '--selftests', str(tmp_path / 'missing.json')],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert done.returncode == 2
    result = json.loads(done.stdout)
    assert result['status'] == NOT_TESTED
    assert result['supported'] is False and result['issue_closed'] is False
    assert 'MISSING_INPUT' in done.stderr


def test_evaluate_is_pure_over_loaded_documents(tmp_path):
    bundle = _bundle(tmp_path / 'pure')
    result = evaluate(bundle['inv'], bundle['tests'],
                      inventory_base=bundle['root'],
                      selftests_base=bundle['root'])
    assert result['status'] == SUPPORTED
    assert result['issue_closed'] is False
    assert bundle['inv'].get('_base') is None  # documents are not mutated


def test_log_grammar_rejects_prose_and_needs_a_skip_reason():
    assert [event['name'] for event in parse_log_text(
        'ok a-one\nskip a-two (no hardware)\nFAIL a-three\n')] == [
            'a-one', 'a-two', 'a-three']
    for bad in ('ok a-one\nthis is prose\n', 'skip a-two\n',
                'ok a-one (unexpected)\n', 'PASS a-one\n'):
        try:
            parse_log_text(bad)
        except ValueError as exc:
            assert 'LOG_' in str(exc)
        else:
            raise AssertionError(f'accepted malformed log: {bad!r}')
