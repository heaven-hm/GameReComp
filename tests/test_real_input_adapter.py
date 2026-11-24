"""Tests for the injected native-runner input adapter.

The fake runner is an in-memory implementation of the NativeRunner protocol:
it turns each injected key/mouse event into deterministic stdout/stderr, exit
code and synthetic BMP/PNG frames, and reports the events actually delivered.
It proves replay determinism, every failure path (unconsumed event, blank and
undecodable frames, drift/divergence, missing scenario/runner) and tamper
detection of stored evidence. No real native binary or game path is used.
"""
import hashlib
import json
import struct
import zlib

import pytest

from tools.real_input_adapter import (
    DeterminismError,
    EventNotConsumed,
    FrameError,
    RunnerError,
    ScenarioError,
    StepResult,
    TamperError,
    load_scenario,
    main,
    run_replay,
    verify_evidence,
)


def make_bmp24(pixels):
    height, width = len(pixels), len(pixels[0])
    stride = ((width * 3 + 3) // 4) * 4
    data = bytearray(54 + stride * height)
    data[:2] = b'BM'
    struct.pack_into('<I', data, 10, 54)
    struct.pack_into('<I', data, 14, 40)
    struct.pack_into('<ii', data, 18, width, height)
    struct.pack_into('<H', data, 26, 1)
    struct.pack_into('<H', data, 28, 24)
    for file_row, image_row in enumerate(reversed(pixels)):
        off = 54 + file_row * stride
        for x, colour in enumerate(image_row):
            data[off + x * 3:off + x * 3 + 3] = bytes(colour)
    return bytes(data)


def _png_chunk(kind, payload):
    return (struct.pack('>I', len(payload)) + kind + payload +
            struct.pack('>I', zlib.crc32(kind + payload) & 0xFFFFFFFF))


def make_png(pixels):
    height, width = len(pixels), len(pixels[0])
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # PNG filter type None
        for colour in row:
            raw.extend(colour)
    return (b'\x89PNG\r\n\x1a\n' +
            _png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            _png_chunk(b'IDAT', zlib.compress(bytes(raw))) +
            _png_chunk(b'IEND', b''))


class FakeRunner:
    """Deterministic in-memory NativeRunner following the adapter protocol."""

    _serial = 0

    def __init__(self, *, drop_at=None, omit_delivery=None, blank=False, fmt='bmp',
                 bad_bytes=False, drift=False, lie_consumed=False, exit_code=7,
                 stdout_prefix=b'out'):
        FakeRunner._serial += 1
        self._serial_value = FakeRunner._serial
        self.drop_at = drop_at
        self.omit_delivery = omit_delivery
        self.blank = blank
        self.fmt = fmt
        self.bad_bytes = bad_bytes
        self.drift = drift
        self.lie_consumed = lie_consumed
        self._exit_code = exit_code
        self.stdout_prefix = stdout_prefix
        self.delivered = []
        self.count = 0
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def step(self, event):
        if self.drop_at is not None and self.count == self.drop_at:
            return StepResult(False, b'', b'', self._exit_code, self._frame())
        if self.omit_delivery is not None and self.count == self.omit_delivery:
            self.count += 1  # consumed, but never actually delivered
            return StepResult(True, b'x', b'', self._exit_code, self._frame())
        self.delivered.append(dict(event))
        consumed = False if self.lie_consumed else True
        self.count += 1
        if isinstance(self.stdout_prefix, bytes):
            out = self.stdout_prefix + b':' + str(self.count).encode() + b'\n'
        else:
            out = self.stdout_prefix  # deliberately wrong type for protocol test
        err = b'err:' + str(self.count).encode() + b'\n'
        return StepResult(consumed, out, err, self._exit_code, self._frame())

    def _frame(self):
        if self.bad_bytes:
            return b'not-an-image'
        n = self.count % 251
        if self.blank:
            return make_bmp24([[(1, 2, 3), (1, 2, 3)],
                               [(1, 2, 3), (1, 2, 3)]])
        pixels = [[(n, 0, 0), (0, n, 0)], [(0, 0, n), (255, 255, 255)]]
        return make_png(pixels) if self.fmt == 'png' else make_bmp24(pixels)

    def events_delivered(self):
        return list(self.delivered)

    def exit_code(self):
        return self._serial_value if self.drift else self._exit_code

    def close(self):
        self.closed = True


def cli_fake_factory():
    """Importable adapter used by the CLI end-to-end test (no hard-coded path)."""
    return FakeRunner()


RUNNER_SPEC = f'{__name__}:cli_fake_factory'


def write_scenario(path, events=None, **extra):
    scenario = {'schema': 1, 'name': 'test-scenario',
                'events': events if events is not None else [
                    {'t': 0, 'type': 'key', 'action': 'down', 'key': 'A'},
                    {'t': 16, 'type': 'mouse', 'action': 'move', 'x': 12, 'y': 34},
                    {'t': 32, 'type': 'mouse', 'action': 'down', 'button': 'left'},
                    {'t': 48, 'type': 'key', 'action': 'up', 'key': 'A'},
                ]}
    scenario.update(extra)
    path.write_text(json.dumps(scenario), encoding='utf-8')
    return scenario


def test_replay_is_byte_identical(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')
    out = tmp_path / 'evidence'

    result = run_replay(scenario, FakeRunner, out)

    assert result['status'] == 'PASS'
    assert result['replays'] == 2
    assert result['record_sha256s'][0] == result['record_sha256s'][1]
    r0 = (out / 'replay-0' / 'record.json').read_bytes()
    r1 = (out / 'replay-1' / 'record.json').read_bytes()
    assert r0 == r1
    record = json.loads(r0)
    assert len(record['steps']) == len(scenario['events'])
    assert record['final_exit_code'] == 7
    for step in record['steps']:
        assert step['consumed'] is True
        assert step['exit_code'] == 7
        for stream in ('stdout', 'stderr'):
            data = (out / 'replay-0' / step[stream]['path']).read_bytes()
            assert hashlib.sha256(data).hexdigest() == step[stream]['sha256']
        shot = step['screenshot']
        assert shot['blank'] is False
        assert shot['distinct_colors'] >= 2
        frame = out / 'replay-0' / shot['path']
        assert hashlib.sha256(frame.read_bytes()).hexdigest() == shot['sha256']


def test_unconsumed_event_fails_closed(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')
    out = tmp_path / 'evidence'

    with pytest.raises(EventNotConsumed, match='event 1'):
        run_replay(scenario, lambda: FakeRunner(drop_at=1), out)
    assert not (out / 'result.json').exists()


def test_undelivered_event_fails_closed(tmp_path):
    """A runner that claims consumption but never delivered fails closed."""
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')

    with pytest.raises(EventNotConsumed, match='complete scenario'):
        run_replay(scenario, lambda: FakeRunner(omit_delivery=1), tmp_path / 'evidence')


def test_false_consumption_fails_closed(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')

    with pytest.raises(EventNotConsumed, match='not consumed'):
        run_replay(scenario, lambda: FakeRunner(lie_consumed=True), tmp_path / 'evidence')


def test_blank_frame_fails_closed(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')

    with pytest.raises(FrameError, match='blank'):
        run_replay(scenario, lambda: FakeRunner(blank=True), tmp_path / 'evidence')


def test_undecodable_frame_fails_closed(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')

    with pytest.raises(FrameError, match='unsupported screenshot format'):
        run_replay(scenario, lambda: FakeRunner(bad_bytes=True), tmp_path / 'evidence')


def test_png_frames_are_accepted(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')

    result = run_replay(scenario, lambda: FakeRunner(fmt='png'), tmp_path / 'evidence')
    assert result['status'] == 'PASS'
    record = json.loads((tmp_path / 'evidence' / 'replay-0' / 'record.json').read_text())
    assert record['steps'][0]['screenshot']['format'] == 'png'
    assert record['steps'][0]['screenshot']['distinct_colors'] >= 2


def test_divergent_runner_is_detected(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')

    with pytest.raises(DeterminismError):
        run_replay(scenario, lambda: FakeRunner(drift=True), tmp_path / 'evidence')


def test_bad_protocol_output_fails_closed(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')

    with pytest.raises(RunnerError, match='stdout must be bytes'):
        run_replay(scenario, lambda: FakeRunner(stdout_prefix='text'), tmp_path / 'evidence')


@pytest.mark.parametrize('device', ['joystick', 'ffb', 'xinput', 'gamepad'])
def test_game_devices_are_rejected(tmp_path, device):
    write_scenario(tmp_path / 'scenario.json', events=[
        {'t': 0, 'type': device, 'action': 'down', 'button': 'a'}])
    with pytest.raises(ScenarioError, match='out of scope'):
        load_scenario(tmp_path / 'scenario.json')


def test_events_ordered_by_timestamp(tmp_path):
    write_scenario(tmp_path / 'scenario.json', events=[
        {'t': 32, 'type': 'key', 'action': 'up', 'key': 'A'},
        {'t': 0, 'type': 'key', 'action': 'down', 'key': 'A'},
    ])
    scenario = load_scenario(tmp_path / 'scenario.json')
    assert [event['t'] for event in scenario['events']] == [0, 32]


def test_cli_missing_scenario_is_blocked(tmp_path):
    out = tmp_path / 'evidence'
    assert main(['--out', str(out)]) == 2
    result = json.loads((out / 'result.json').read_text())
    assert result['status'] == 'BLOCKED'
    assert 'missing --scenario' in result['reason']


def test_cli_without_runner_is_blocked(tmp_path):
    scenario_path = tmp_path / 'scenario.json'
    write_scenario(scenario_path)
    out = tmp_path / 'evidence'

    assert main(['--scenario', str(scenario_path), '--out', str(out)]) == 2
    result = json.loads((out / 'result.json').read_text())
    assert result['status'] == 'BLOCKED'
    assert 'no --runner' in result['reason']


def test_cli_with_fake_runner_reports_pass(tmp_path):
    scenario_path = tmp_path / 'scenario.json'
    write_scenario(scenario_path)
    out = tmp_path / 'evidence'

    assert main(['--scenario', str(scenario_path), '--out', str(out),
                 '--runner', RUNNER_SPEC]) == 0
    result = json.loads((out / 'result.json').read_text())
    assert result['status'] == 'PASS'
    assert len(result['record_sha256s']) == 2
    assert 'not_claimed' in result and 'XInput' in result['not_claimed']


def test_verify_evidence_passes_on_clean_run(tmp_path):
    scenario_path = tmp_path / 'scenario.json'
    write_scenario(scenario_path)
    out = tmp_path / 'evidence'
    assert main(['--scenario', str(scenario_path), '--out', str(out),
                 '--runner', RUNNER_SPEC]) == 0

    assert main(['--out', str(out), '--verify']) == 0


@pytest.mark.parametrize('kind', ['stdout', 'stderr', 'frame'])
def test_verify_detects_tampered_evidence(tmp_path, kind):
    scenario_path = tmp_path / 'scenario.json'
    write_scenario(scenario_path)
    out = tmp_path / 'evidence'
    assert main(['--scenario', str(scenario_path), '--out', str(out),
                 '--runner', RUNNER_SPEC]) == 0

    record_path = out / 'replay-0' / 'record.json'
    record = json.loads(record_path.read_text())
    step = record['steps'][1]
    if kind == 'frame':
        target = out / 'replay-0' / step['screenshot']['path']
    else:
        target = out / 'replay-0' / step[kind]['path']
    original = target.read_bytes()
    target.write_bytes(original + b'TAMPER')

    with pytest.raises(TamperError, match=f'{kind} hash mismatch'):
        verify_evidence(out)


def test_verify_detects_tampered_record(tmp_path):
    scenario_path = tmp_path / 'scenario.json'
    write_scenario(scenario_path)
    out = tmp_path / 'evidence'
    assert main(['--scenario', str(scenario_path), '--out', str(out),
                 '--runner', RUNNER_SPEC]) == 0

    record_path = out / 'replay-0' / 'record.json'
    record = json.loads(record_path.read_text())
    record['final_exit_code'] = 999
    record_path.write_text(json.dumps(record))

    with pytest.raises(TamperError, match='record.json hash mismatch'):
        verify_evidence(out)


def test_verify_detects_tampered_scenario(tmp_path):
    scenario_path = tmp_path / 'scenario.json'
    write_scenario(scenario_path)
    out = tmp_path / 'evidence'
    assert main(['--scenario', str(scenario_path), '--out', str(out),
                 '--runner', RUNNER_SPEC]) == 0

    (out / 'scenario.json').write_text('{"schema":1,"name":"hacked","events":[]}')

    with pytest.raises(TamperError, match='scenario.json no longer matches'):
        verify_evidence(out)


def test_verify_refuses_path_traversal(tmp_path):
    scenario_path = tmp_path / 'scenario.json'
    write_scenario(scenario_path)
    out = tmp_path / 'evidence'
    assert main(['--scenario', str(scenario_path), '--out', str(out),
                 '--runner', RUNNER_SPEC]) == 0

    record_path = out / 'replay-0' / 'record.json'
    raw = record_path.read_bytes()
    record = json.loads(raw)
    record['steps'][0]['stdout']['path'] = '../../outside.stdout'
    # Keep the record hash consistent so the traversal check is what fires.
    from tools.real_input_adapter import canonical_bytes, _sha256
    import json as _json
    new_raw = canonical_bytes(record)
    record_path.write_bytes(new_raw)
    result = json.loads((out / 'result.json').read_text())
    result['record_sha256s'][0] = _sha256(new_raw)
    (out / 'result.json').write_bytes(canonical_bytes(result))

    with pytest.raises(TamperError, match='escapes its replay directory'):
        verify_evidence(out)
