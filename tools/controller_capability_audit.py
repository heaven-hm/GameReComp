#!/usr/bin/env python3
"""Fail-closed per-API controller/raw-input capability verdicts (issue #11).

Issue #11 permits a truthful ``DIERR_UNSUPPORTED`` when the host has no real
force-feedback/joystick/XInput/Raw Input hardware. This tool turns that rule
into a decision gate. It reads **only** two CLI-supplied JSON documents and
CLI-supplied artifact files; it never probes the host, never runs a game,
never registers a DLL and never promotes registration/import mentions to a
support claim.

    python3 -m tools.controller_capability_audit \
        --inventory <inventory.json> --selftests <selftests.json> \
        [--json <decision.json>]

Input documents (all paths are CLI-supplied; artifact paths inside a document
are resolved relative to that document's directory)::

  inventory.json
    {"schema_version": 1, "issue": 11, "host": "<named host>",
     "devices": [
       {"id": "<stable device identity>", "api": "<api>",
        "kind": "<physical kind>", "real": true,
        "enumeration": "<physical enumeration source>",
        "capabilities": ["<capability>", ...],
        "evidence": {"path": "<enumeration dump>", "sha256": "<64 hex>"}}
     ]}

  selftests.json
    {"schema_version": 1, "issue": 11,
     "records": [
       {"api": "<api>", "device_id": "<id from inventory>",
        "host": "<named host>", "adapter": "real-hardware",
        "verdict": "PASS", "exit_code": 0,
        "checks": [{"name": "<api>-<case>", "status": "ok"}, ...],
        "log": {"path": "<selftest summary log>", "sha256": "<64 hex>"}}
     ]}

The log uses one result per line: ``ok <name>``, ``FAIL <name>`` or
``skip <name> (<reason>)``. Counts are re-derived from the log and compared
with the record's ``checks``; nothing declared in the record is trusted on its
own. Both artifact digests are recomputed from the supplied bytes.

APIs: dinput-joystick, force-feedback, dinput8, xinput, raw-input, hid.

Verdict contract (per API, with exact reason codes):

* ``SUPPORTED`` -- a hash-checked inventory row declares a *physical* device
  for that API and a separate hash-checked selftest record passed for that
  exact device id on the named host, through a real-hardware adapter, with
  API-specific checks that agree with the log. Nothing less is enough.
* ``DEFERRED`` -- the device or the selftest record was not supplied, a test
  was skipped, or only registration/import claims were supplied. Roadmap
  work, not a support claim.
* ``UNSUPPORTED`` -- the inventory declares only synthetic/virtual devices,
  the device lacks the required capability, or the real-hardware selftest
  failed. This is the honest basis for ``DIERR_UNSUPPORTED``.
* ``BLOCKED`` -- evidence is present but invalid or tampered: bad/mismatched
  hashes, a synthetic row claiming ``real: true``, a synthetic test adapter,
  a device-id/host mismatch, contradictory checks/verdict, duplicate records
  or an unbacked PASS claim.
* ``NOT TESTED`` -- a CLI document could not be read at all.

Overall status is ``BLOCKED`` if any API is blocked, ``SUPPORTED`` only if
every API is supported, otherwise ``DEFERRED``/``UNSUPPORTED``. ``issue_closed``
is always false: this tool never closes issue #11.

Exit codes: 0 = decision emitted with overall ``SUPPORTED``; 3 = decision
emitted with any other status (still printed in full); 2 = CLI input cannot be
read. Failures go to stderr only for code 2; a decision is always canonical
JSON so repeated runs on the same inputs are byte-identical.

Limit: a CLI-supplied document cannot cryptographically prove that a physical
device exists on a machine. ``SUPPORTED`` means exactly that the supplied
hash-checked inventory declared a physical device and the supplied
hash-checked, API-specific selftest passed for that same device id on the
named host; it is not a claim of universal or in-game controller support.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = 1
ISSUE = 11
APIS = ('dinput-joystick', 'force-feedback', 'dinput8', 'xinput', 'raw-input',
        'hid')

SUPPORTED = 'SUPPORTED'
DEFERRED = 'DEFERRED'
UNSUPPORTED = 'UNSUPPORTED'
BLOCKED = 'BLOCKED'
NOT_TESTED = 'NOT TESTED'
PASS = 'PASS'
FAIL = 'FAIL'
SKIP = 'SKIP'
REAL_ADAPTER = 'real-hardware'
HEX64 = re.compile(r'^[0-9a-fA-F]{64}$')
NAME = re.compile(r'^[A-Za-z0-9_.-]+$')
SUMMARY_LINE = re.compile(
    r'^(ok|FAIL|skip)[ \t]+([A-Za-z0-9_.-]+)'
    r'(?:[ \t]+\(([^()\r\n]*)\))?[ \t]*$')

# A device/test that declares any of these is not physical hardware. Matching
# is on whole lower-cased tokens, so "iohid" and "sdl2-hid" stay valid while
# "virtual-hid" or "sdl-emulation" do not.
SYNTHETIC_TOKENS = frozenset({
    'synthetic', 'fake', 'stub', 'mock', 'virtual', 'emulated', 'emulation',
    'simulated', 'test', 'fixture', 'inmemory', 'in', 'memory', 'null',
    'none', 'n/a', 'na',
})
# Fields that are claims, never evidence. An unbacked PASS in a document root
# is detected and ignored rather than counted.
CLAIM_KEYS = ('verdict', 'supported', 'pass', 'game_pass', 'promotion',
              'promoted')
REGISTRATION_KEYS = ('registered', 'registration', 'registered_only',
                     'dll_registered', 'import_mentions', 'import_only',
                     'selftest_registered', 'build_only', 'compile_only')
STATUSES = ('ok', 'FAIL', 'skip')
REQUIRED_CAPABILITIES = {'force-feedback': 'force_feedback'}

RULE = (
    'SUPPORTED requires a hash-checked physical device row AND a separate '
    'hash-checked real-hardware selftest record bound to that exact device '
    'id and host, with API-specific checks that agree with the log; '
    'registration, imports, synthetic devices and declared verdicts alone '
    'never yield SUPPORTED'
)
LIMITS = (
    'No host probe, game run, Windows-original comparison or DLL registration '
    'is performed; paths and records come only from the CLI.',
    'A CLI-supplied inventory cannot cryptographically prove a physical '
    'device exists; SUPPORTED covers only the supplied hash-checked rows and '
    'selftest records, never universal or in-game controller support.',
    'Truthful DIERR_UNSUPPORTED is allowed: synthetic-only devices, missing '
    'hardware capability or a failed real-hardware selftest yield '
    'UNSUPPORTED, never a fabricated PASS.',
    'The same CLI inputs always yield byte-identical JSON: no clock, no '
    'environment value and no machine path is added.',
)


class LoadError(Exception):
    """A CLI document is absent, unreadable or not a JSON object."""


class EvidenceError(ValueError):
    """A supplied row/artifact is malformed; carries an exact reason code."""


def _fail(code: str, detail: str) -> None:
    raise EvidenceError(f'{code}: {detail}')


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path_text: str | None, what: str) -> tuple[dict, Path]:
    if not path_text:
        raise LoadError(f'MISSING_INPUT: no --{what} path supplied')
    path = Path(path_text)
    if not path.is_file():
        raise LoadError(f'MISSING_INPUT: {what} file not found: {path.name}')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise LoadError(f'INVALID_JSON: {what}: {exc}') from exc
    if not isinstance(data, dict):
        raise LoadError(f'INVALID_STRUCTURE: {what} root is not a JSON object')
    return data, path.resolve().parent


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _named(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and NAME.fullmatch(
        value.strip()) is not None


def _host(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _synthetic(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    tokens = re.split(r'[^A-Za-z0-9]+', value.lower())
    return any(token in SYNTHETIC_TOKENS for token in tokens if token)


def _registration_only(row: dict) -> bool:
    """True when a row is only a claim of registration, not test evidence."""
    if 'checks' in row or 'log' in row:
        return False
    return any(row.get(key) is True for key in REGISTRATION_KEYS)


def parse_log_text(text: str) -> list[dict]:
    """Re-derive selftest outcomes; refuse every non-summary line."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if not text.strip():
        _fail('LOG_EMPTY', 'selftest log has no summary lines')
    events: list[dict] = []
    for lineno, line in enumerate(text.split('\n'), 1):
        if not line.strip():
            continue
        match = SUMMARY_LINE.match(line)
        if match is None:
            _fail('LOG_LINE_INVALID',
                  f'line {lineno}: {line.strip()!r} is not an ok/FAIL/skip '
                  'summary line')
        status, name, reason = match.groups()
        if status == 'skip' and (not reason or not reason.strip()):
            _fail('LOG_LINE_INVALID', f'line {lineno}: skip has no reason')
        if status != 'skip' and reason is not None:
            _fail('LOG_LINE_INVALID',
                  f'line {lineno}: {status} carries an unexpected reason')
        events.append({'status': status, 'name': name, 'reason': reason})
    return events


def _artifact(row: Any, base: Path, label: str) -> tuple[bytes, str]:
    """Hash-check one CLI-supplied artifact; never trust a declared digest."""
    if not isinstance(row, dict):
        _fail(f'{label}_INVALID', 'artifact row is not an object')
    text = row.get('path')
    declared = row.get('sha256')
    if not isinstance(text, str) or not text.strip():
        _fail(f'{label}_MISSING', 'artifact has no path')
    if not _is_hash(declared):
        _fail(f'{label}_INVALID', 'artifact has no 64-hex sha256')
    path = Path(text)
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        _fail(f'{label}_MISSING', f'artifact not found: {path.name}')
    try:
        data = path.read_bytes()
    except OSError as exc:
        _fail(f'{label}_MISSING', f'artifact unreadable: {exc}')
    actual = hashlib.sha256(data).hexdigest()
    if actual.lower() != declared.lower():
        _fail(f'{label}_HASH_MISMATCH',
              f'declared {str(declared)[:12]}..., actual {actual[:12]}...')
    if not data:
        _fail(f'{label}_EMPTY', f'artifact is empty: {path.name}')
    return data, actual


def _check_claims(doc: dict, prefix: str, reasons: list[str]) -> None:
    """Detect unbacked self-reported PASS claims in a document root."""
    for key in CLAIM_KEYS:
        value = doc.get(key)
        if value is True or (isinstance(value, str)
                             and value.strip().upper() in {'PASS', 'TRUE', 'YES'}):
            reasons.append(
                f'{prefix}: UNBACKED_PASS_CLAIM_IGNORED: {key}={value!r} in '
                'a document root is a claim, not evidence')


def _device_state(devices: Any, base: Path, reasons: list[str],
                  checks: dict[str, dict]) -> dict[str, dict]:
    """Validate inventory rows; return eligible physical devices by API."""
    eligible: dict[str, list[dict]] = {api: [] for api in APIS}
    synthetic: set[str] = set()
    if devices is None or devices in ([], {}):
        for api in APIS:
            reasons.append(
                f'{api}: API_DEVICE_ABSENT: no physical device row supplied')
        return {}
    if not isinstance(devices, list):
        for api in APIS:
            reasons.append(f'{api}: DEVICE_ROW_INVALID: devices is not a list')
        return {}
    seen: set[str] = set()
    for index, row in enumerate(devices):
        where = f'devices[{index}]'
        api = None
        try:
            if not isinstance(row, dict):
                _fail('DEVICE_ROW_INVALID', 'device row is not an object')
            api = row.get('api')
            if api not in APIS:
                _fail('UNKNOWN_API', f'api {api!r} is not in the audited set')
            ident = row.get('id')
            if not _named(ident):
                _fail('DEVICE_ROW_INVALID', 'id is missing or malformed')
            ident = ident.strip()
            if ident in seen:
                _fail('DEVICE_ID_CONFLICT', f'duplicate device id {ident}')
            seen.add(ident)
            enumeration = row.get('enumeration')
            if not _host(enumeration):
                _fail('DEVICE_ROW_INVALID', 'enumeration source is missing')
            if (_synthetic(enumeration) or _synthetic(row.get('kind'))
                    or _synthetic(row.get('adapter'))
                    or row.get('synthetic') is True
                    or row.get('real') is False):
                # A row cannot make a virtual device physical by claiming it is.
                _fail('SYNTHETIC_DEVICE_ONLY',
                      f'{where} is virtual/synthetic (enumeration='
                      f'{enumeration!r}, real={row.get("real")!r})')
            if row.get('real') is not True:
                _fail('DEVICE_ROW_INVALID',
                      'real must be true for a physical device row')
            if not _host(row.get('kind')):
                _fail('DEVICE_ROW_INVALID', 'physical kind is missing')
            capabilities = row.get('capabilities')
            if (not isinstance(capabilities, list) or not capabilities
                    or not all(_named(cap) for cap in capabilities)):
                _fail('DEVICE_ROW_INVALID',
                      'capabilities must be a non-empty list of named strings')
            _, digest = _artifact(row.get('evidence'), base,
                                  'DEVICE_EVIDENCE')
            required = REQUIRED_CAPABILITIES.get(api)
            if required is not None and required not in {
                    cap.strip() for cap in capabilities if isinstance(cap, str)}:
                _fail('NO_REAL_DEVICE',
                      f'{api} requires capability {required!r}')
        except EvidenceError as exc:
            findings = str(exc)
            reasons.append(f'{api if api in APIS else "inventory"}: {findings}')
            code = findings.split(':', 1)[0]
            apis = (api,) if api in APIS else APIS
            for name in apis:
                current = checks[name]['device']
                if code == 'SYNTHETIC_DEVICE_ONLY':
                    synthetic.add(name)
                    continue
                if code == 'NO_REAL_DEVICE':
                    if checks[name]['device'] != BLOCKED:
                        checks[name]['device'] = UNSUPPORTED
                    continue
                checks[name]['device'] = BLOCKED
            continue
        eligible[api].append({'id': ident, 'kind': row['kind'].strip(),
                              'enumeration': enumeration.strip(),
                              'evidence_sha256': digest})

    for api in APIS:
        if eligible[api]:
            checks[api]['device'] = PASS
            checks[api]['device_ids'] = sorted(
                device['id'] for device in eligible[api])
        elif checks[api]['device'] not in (BLOCKED, UNSUPPORTED):
            if api in synthetic:
                checks[api]['device'] = UNSUPPORTED
            else:
                checks[api]['device'] = DEFERRED
        if (checks[api]['device'] == DEFERRED
                and not any(reason.startswith(f'{api}: ') for reason in reasons)):
            reasons.append(
                f'{api}: API_DEVICE_ABSENT: no physical device row for this API')
    return {api: {device['id']: device for device in eligible[api]}
            for api in APIS}


def _declared_ids(inventory: dict) -> dict[str, set[str]]:
    """All ids declared by the inventory, eligible or not (for refusals)."""
    declared: dict[str, set[str]] = {api: set() for api in APIS}
    rows = inventory.get('devices')
    if isinstance(rows, list):
        for row in rows:
            if (isinstance(row, dict) and row.get('api') in APIS
                    and isinstance(row.get('id'), str)):
                declared[row['api']].add(row['id'].strip())
    return declared


def _check_selftests(doc: dict, base: Path, host: Any,
                     devices: dict[str, dict[str, dict]],
                     declared: dict[str, set[str]],
                     reasons: list[str], checks: dict[str, dict]) -> None:
    """Validate selftest records against the eligible physical devices."""
    records = doc.get('records')
    if records is None or records in ([], {}):
        for api in APIS:
            reasons.append(
                f'{api}: API_SELFTEST_ABSENT: no selftest record supplied')
        return
    if not isinstance(records, list):
        for api in APIS:
            checks[api]['test_supplied'] = False
            reasons.append(f'{api}: SELFTEST_ROW_INVALID: records is not a list')
        return
    seen: set[str] = set()
    for index, row in enumerate(records):
        api = row.get('api') if isinstance(row, dict) else None
        if api not in APIS:
            api = 'unknown'
        try:
            if not isinstance(row, dict):
                _fail('SELFTEST_ROW_INVALID', 'record is not an object')
            if api not in APIS:
                _fail('UNKNOWN_API', f'api {api!r} is not in the audited set')
            if _registration_only(row):
                _fail('REGISTRATION_ONLY',
                      'only registration/import claims; no selftest, device '
                      'binding or log was supplied')
            ident = row.get('device_id')
            if not _named(ident):
                _fail('SELFTEST_ROW_INVALID', 'device_id is missing or malformed')
            ident = ident.strip()
            if ident in seen:
                _fail('DUPLICATE_SELFTEST_RECORD',
                      f'duplicate selftest device_id {ident}')
            seen.add(ident)
            if ident not in devices[api]:
                if ident in declared[api]:
                    _fail('DEVICE_NOT_ELIGIBLE',
                          f'{ident} is declared but is not a verified physical '
                          f'device for {api}')
                _fail('DEVICE_ID_MISMATCH',
                      f'{ident} is not a declared device for {api}')
            if not _host(row.get('host')) or row['host'].strip() != host.strip():
                _fail('HOST_MISMATCH',
                      f'record host {row.get("host")!r} != inventory host '
                      f'{host!r}')
            adapter = row.get('adapter')
            if (not isinstance(adapter, str)
                    or adapter.strip().lower().replace('_', '-') != REAL_ADAPTER
                    or _synthetic(adapter)):
                _fail('SYNTHETIC_SELFTEST_ADAPTER',
                      f'adapter {adapter!r} is not {REAL_ADAPTER!r}')
            verdict = row.get('verdict')
            if verdict not in (PASS, FAIL, SKIP):
                _fail('SELFTEST_ROW_INVALID',
                      f'verdict {verdict!r} is not PASS/FAIL/SKIP')
            exit_code = row.get('exit_code')
            if not _int(exit_code) or (verdict == PASS and exit_code != 0):
                _fail('SELFTEST_ROW_INVALID',
                      f'exit_code {exit_code!r} does not match {verdict}')
            cases = row.get('checks')
            if (not isinstance(cases, list) or not cases
                    or not all(isinstance(case, dict) for case in cases)):
                _fail('CHECKS_INVALID', 'checks must be a non-empty list of objects')
            normalised: list[dict] = []
            for case in cases:
                name, status = case.get('name'), case.get('status')
                if not _named(name) or status not in STATUSES:
                    _fail('CHECK_STATUS_INVALID',
                          f'check {name!r}/{status!r} is malformed')
                reason = case.get('reason')
                if status == 'skip' and (not isinstance(reason, str)
                                         or not reason.strip()):
                    _fail('CHECK_STATUS_INVALID',
                          f'skip check {name!r} has no reason')
                if status != 'skip' and reason is not None:
                    _fail('CHECK_STATUS_INVALID',
                          f'{status} check {name!r} carries a reason')
                normalised.append({'name': name.strip(),
                                   'status': status, 'reason': reason})
            if not any(case['name'].startswith(api + '-')
                       for case in normalised):
                _fail('CHECK_NOT_API_SPECIFIC',
                      f'no check is named "{api}-..."; a generic selftest '
                      'cannot prove this API')
            data, digest = _artifact(row.get('log'), base, 'LOG')
            log_events = parse_log_text(data.decode('utf-8'))
            supplied = sorted((case['status'], case['name'], case['reason'])
                              for case in normalised)
            derived = sorted((event['status'], event['name'], event['reason'])
                             for event in log_events)
            if supplied != derived:
                _fail('CHECK_LOG_MISMATCH',
                      'record checks disagree with the re-derived log lines')
            derived_status = ('FAIL' if any(e['status'] == 'FAIL' for e in log_events)
                              else 'SKIP' if any(e['status'] == 'skip' for e in log_events)
                              else PASS)
            if derived_status != verdict:
                _fail('VERDICT_CHECK_MISMATCH',
                      f'declared verdict {verdict} disagrees with log-derived '
                      f'outcome {derived_status}')
        except EvidenceError as exc:
            if api not in APIS:
                reasons.append(f'inventory: {exc}')
                continue
            reasons.append(f'{api}: {exc}')
            code = str(exc).split(':', 1)[0]
            if code == 'REGISTRATION_ONLY':
                if checks[api]['test'] != BLOCKED:
                    checks[api]['test'] = DEFERRED
            elif code == 'DEVICE_NOT_ELIGIBLE':
                if checks[api]['test'] != BLOCKED:
                    checks[api]['test'] = UNSUPPORTED
            else:
                checks[api]['test'] = BLOCKED
            continue

        if verdict == FAIL:
            reasons.append(
                f'{api}: SELFTEST_FAILED: real-hardware selftest reported FAIL '
                f'for {ident}; DIERR_UNSUPPORTED is the truthful result')
            checks[api]['test'] = UNSUPPORTED
        elif verdict == SKIP:
            reasons.append(
                f'{api}: SELFTEST_SKIPPED: real-hardware selftest skipped; '
                'no support claim is justified')
            checks[api]['test'] = DEFERRED
        else:
            checks[api]['test'] = PASS
            checks[api]['test_device_id'] = ident
            checks[api]['log_sha256'] = digest
            checks[api]['check_counts'] = {
                status: sum(1 for case in normalised if case['status'] == status)
                for status in STATUSES}
    for api in APIS:
        if (checks[api]['test'] == DEFERRED
                and not any(reason.startswith(f'{api}: ') for reason in reasons)):
            reasons.append(
                f'{api}: API_SELFTEST_ABSENT: no selftest record for this API')


def _per_api(checks: dict[str, dict], reasons: list[str]) -> dict[str, dict]:
    """Combine device + test states; BLOCKED outranks every other state."""
    result: dict[str, dict] = {}
    for api in APIS:
        state = checks[api]
        statuses = (state['device'], state['test'])
        if BLOCKED in statuses:
            verdict = BLOCKED
        elif UNSUPPORTED in statuses:
            verdict = UNSUPPORTED
        elif DEFERRED in statuses:
            verdict = DEFERRED
        elif statuses == (PASS, PASS):
            verdict = SUPPORTED
        else:
            verdict = NOT_TESTED
        api_reasons = [reason for reason in reasons
                       if reason.startswith(f'{api}: ')]

        def need(code: str) -> bool:
            return not any(code in reason for reason in api_reasons)

        if verdict == SUPPORTED:
            api_reasons.append(
                f'{api}: SUPPORTED: physical device '
                f'{state["test_device_id"]} and log {state["log_sha256"][:12]}... '
                'agree; scoped to the supplied records only')
        else:
            # Every refusal carries an exact, self-contained reason code even
            # when only some of the six APIs were named in the input files.
            if state['device'] == DEFERRED and need('API_DEVICE_ABSENT'):
                api_reasons.append(
                    f'{api}: API_DEVICE_ABSENT: no physical device row supplied')
            if state['test'] == DEFERRED and need('API_SELFTEST_ABSENT'):
                api_reasons.append(
                    f'{api}: API_SELFTEST_ABSENT: no selftest record supplied')
            if state['device'] == UNSUPPORTED and need('NO_REAL_DEVICE'):
                api_reasons.append(
                    f'{api}: NO_REAL_DEVICE: no eligible physical device for '
                    'this API')
            if state['test'] == UNSUPPORTED and need('UNSUPPORTED_SELFTEST'):
                api_reasons.append(
                    f'{api}: UNSUPPORTED_SELFTEST: no accepted real-hardware '
                    'selftest for this API')
            if verdict == BLOCKED and need('EVIDENCE_INVALID'):
                api_reasons.append(
                    f'{api}: EVIDENCE_INVALID: device={state["device"]}, '
                    f'selftest={state["test"]}; refusing to claim support')
        result[api] = {
            'status': verdict,
            'supported': verdict == SUPPORTED,
            'device_status': state['device'],
            'selftest_status': state['test'],
            'device_ids': state.get('device_ids', []),
            'test_device_id': state.get('test_device_id'),
            'log_sha256': state.get('log_sha256'),
            'check_counts': state.get('check_counts'),
            'reasons': api_reasons,
        }
    return result


def evaluate(inventory: dict, selftests: dict, *, inventory_base: Path | None = None,
             selftests_base: Path | None = None) -> dict:
    """Pure decision function over already-loaded CLI-supplied documents."""
    reasons: list[str] = []
    _check_claims(inventory, 'inventory', reasons)
    _check_claims(selftests, 'selftests', reasons)
    raw_host = inventory.get('host')
    checks: dict[str, dict] = {
        api: {'device': DEFERRED, 'test': DEFERRED} for api in APIS}
    devices = _device_state(inventory.get('devices'),
                            inventory_base or Path('.'), reasons, checks)
    if not _host(raw_host):
        for api in APIS:
            reasons.append(f'{api}: HOST_MISMATCH: inventory has no named host')
        for api in APIS:
            if checks[api]['test'] == PASS:
                checks[api]['test'] = BLOCKED
    else:
        _check_selftests(selftests, selftests_base or Path('.'),
                         raw_host, devices, _declared_ids(inventory),
                         reasons, checks)
    per_api = _per_api(checks, reasons)
    statuses = [row['status'] for row in per_api.values()]
    if BLOCKED in statuses:
        overall = BLOCKED
    elif all(status == SUPPORTED for status in statuses):
        overall = SUPPORTED
    elif DEFERRED in statuses:
        overall = DEFERRED
    elif UNSUPPORTED in statuses:
        overall = UNSUPPORTED
    else:
        overall = NOT_TESTED
    if any('UNBACKED_PASS_CLAIM_IGNORED' in reason for reason in reasons):
        overall = BLOCKED
    return {
        'schema_version': SCHEMA,
        'generator': 'tools/controller_capability_audit.py',
        'issue': ISSUE,
        'status': overall,
        'supported': overall == SUPPORTED,
        'issue_closed': False,
        'supported_apis': [api for api, row in per_api.items()
                           if row['supported']],
        'per_api': per_api,
        'reasons': reasons,
        'rule': RULE,
        'limits': list(LIMITS),
    }


def _not_tested(reason: str) -> dict:
    checks = {api: {'device': NOT_TESTED, 'test': NOT_TESTED} for api in APIS}
    per_api = {api: {'status': NOT_TESTED, 'supported': False,
                     'device_status': NOT_TESTED, 'selftest_status': NOT_TESTED,
                     'device_ids': [], 'test_device_id': None,
                     'log_sha256': None, 'check_counts': None,
                     'reasons': [reason]} for api in APIS}
    return {'schema_version': SCHEMA,
            'generator': 'tools/controller_capability_audit.py',
            'issue': ISSUE, 'status': NOT_TESTED, 'supported': False,
            'issue_closed': False, 'supported_apis': [], 'per_api': per_api,
            'reasons': [reason], 'rule': RULE, 'limits': list(LIMITS)}


def _emit(result: dict, out_path: str | None) -> None:
    text = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + '\n'
    if out_path:
        Path(out_path).write_text(text, encoding='utf-8')
    print(text, end='')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--inventory', required=True,
                        help='CLI-supplied host input-device inventory JSON')
    parser.add_argument('--selftests', required=True,
                        help='CLI-supplied selftest records JSON')
    parser.add_argument('--json', dest='json_out',
                        help='also write the decision JSON to this path')
    args = parser.parse_args(argv)
    try:
        inventory, inv_base = _load(args.inventory, 'inventory')
        selftests, test_base = _load(args.selftests, 'selftests')
    except LoadError as exc:
        result = _not_tested(str(exc))
        print(str(exc), file=sys.stderr)
        try:
            _emit(result, args.json_out)
        except OSError:
            pass
        return 2
    result = evaluate(inventory, selftests, inventory_base=inv_base,
                      selftests_base=test_base)
    try:
        _emit(result, args.json_out)
    except OSError as exc:
        print(f'CANNOT_WRITE_DECISION: {args.json_out}: {exc}', file=sys.stderr)
        return 2
    return 0 if result['status'] == SUPPORTED else 3


if __name__ == '__main__':
    sys.exit(main())
