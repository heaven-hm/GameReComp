"""Tests for the deterministic scripted-input harness and its fake pump.

The fake pump is an in-memory implementation of the InputPump adapter contract:
it turns injected key/mouse events into guest-visible state and captures
synthetic BMP/PNG frames. It proves replay determinism, unconsumed-event and
blank-frame failure, and that no game/joystick/FFB/XInput claim is made.
"""
import hashlib
import json
import struct
import zlib

import pytest

from tools.input_scenario import (
    DeterminismError,
    EventNotConsumed,
    FrameError,
    ScenarioError,
    load_scenario,
    main,
    run_replay,
)


def make_bmp24(pixels: list[list[tuple[int, int, int]]]) -> bytes:
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


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack('>I', len(payload)) + kind + payload +
            struct.pack('>I', zlib.crc32(kind + payload) & 0xFFFFFFFF))


def make_png(pixels: list[list[tuple[int, int, int]]]) -> bytes:
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


class FakePump:
    """Deterministic in-memory pump following the InputPump adapter contract."""

    _serial = 0

    def __init__(self, *, drop_at=None, blank=False, fmt='bmp', drift=False,
                 bad_bytes=False):
        FakePump._serial += 1
        self._serial_value = FakePump._serial
        self.drop_at = drop_at
        self.blank = blank
        self.fmt = fmt
        self.drift = drift
        self.bad_bytes = bad_bytes
        self.keys: dict[str, bool] = {}
        self.mouse = {'x': 0, 'y': 0, 'buttons': {}, 'wheel': (0, 0)}
        self.focus = 1
        self.count = 0
        self.closed = False

    def inject(self, event: dict) -> bool:
        if self.drop_at is not None and self.count == self.drop_at:
            return False
        if event['type'] == 'key':
            self.keys[str(event['key'])] = event['action'] == 'down'
        else:
            action = event['action']
            if action == 'move':
                self.mouse['x'], self.mouse['y'] = event['x'], event['y']
            elif action in ('down', 'up'):
                self.mouse['buttons'][event['button']] = action == 'down'
                if action == 'down':
                    self.focus += 1
            elif action == 'wheel':
                self.mouse['wheel'] = (event.get('dx', 0), event.get('dy', 0))
        self.count += 1
        return True

    def state(self) -> dict:
        snapshot = {
            'keys': dict(self.keys),
            'mouse': {'x': self.mouse['x'], 'y': self.mouse['y'],
                      'buttons': dict(self.mouse['buttons']),
                      'wheel': self.mouse['wheel']},
            'focus': self.focus,
        }
        if self.drift:
            snapshot['pump_serial'] = self._serial_value
        return snapshot

    def screenshot(self) -> bytes:
        if self.bad_bytes:
            return b'not-an-image'
        n = self.count % 251
        if self.blank:
            return make_bmp24([[(1, 2, 3), (1, 2, 3)],
                               [(1, 2, 3), (1, 2, 3)]])
        pixels = [[(n, 0, 0), (0, n, 0)], [(0, 0, n), (255, 255, 255)]]
        return make_png(pixels) if self.fmt == 'png' else make_bmp24(pixels)

    def close(self) -> None:
        self.closed = True


def cli_fake_factory():
    """Importable adapter used by the CLI end-to-end test (no hard-coded path)."""
    return FakePump()


PUMP_SPEC = f'{__name__}:cli_fake_factory'


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

    result = run_replay(scenario, lambda: FakePump(), out)

    assert result['status'] == 'PASS'
    assert result['replays'] == 2
    assert result['record_sha256s'][0] == result['record_sha256s'][1]
    r0 = (out / 'replay-0' / 'record.json').read_bytes()
    r1 = (out / 'replay-1' / 'record.json').read_bytes()
    assert r0 == r1
    record = json.loads(r0)
    assert len(record['steps']) == len(scenario['events'])
    for step in record['steps']:
        assert step['consumed'] is True
        shot = step['screenshot']
        assert shot['blank'] is False
        assert shot['distinct_colors'] >= 2
        frame = out / 'replay-0' / shot['path']
        assert hashlib.sha256(frame.read_bytes()).hexdigest() == shot['sha256']
    keys = record['steps'][-1]['state']['keys']
    assert keys['A'] is False  # the final key-up is reflected in guest state


def test_unconsumed_event_fails_closed(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')
    out = tmp_path / 'evidence'

    with pytest.raises(EventNotConsumed, match='event 1'):
        run_replay(scenario, lambda: FakePump(drop_at=1), out)
    assert not (out / 'result.json').exists()


def test_blank_frame_fails_closed(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')

    with pytest.raises(FrameError, match='blank'):
        run_replay(scenario, lambda: FakePump(blank=True), tmp_path / 'evidence')


def test_undecodable_frame_fails_closed(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')

    with pytest.raises(FrameError, match='unsupported screenshot format'):
        run_replay(scenario, lambda: FakePump(bad_bytes=True), tmp_path / 'evidence')


def test_png_frames_are_accepted(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')

    result = run_replay(scenario, lambda: FakePump(fmt='png'), tmp_path / 'evidence')
    assert result['status'] == 'PASS'
    record = json.loads((tmp_path / 'evidence' / 'replay-0' / 'record.json').read_text())
    assert record['steps'][0]['screenshot']['format'] == 'png'


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


def test_nondeterministic_pump_is_detected(tmp_path):
    write_scenario(tmp_path / 'scenario.json')
    scenario = load_scenario(tmp_path / 'scenario.json')

    with pytest.raises(DeterminismError):
        run_replay(scenario, lambda: FakePump(drift=True), tmp_path / 'evidence')


def test_cli_without_pump_is_blocked(tmp_path):
    scenario_path = tmp_path / 'scenario.json'
    write_scenario(scenario_path)
    out = tmp_path / 'evidence'

    assert main(['--scenario', str(scenario_path), '--out', str(out)]) == 2
    result = json.loads((out / 'result.json').read_text())
    assert result['status'] == 'BLOCKED'
    assert 'no --pump' in result['reason']


def test_cli_with_fake_pump_reports_pass(tmp_path):
    scenario_path = tmp_path / 'scenario.json'
    write_scenario(scenario_path)
    out = tmp_path / 'evidence'

    assert main(['--scenario', str(scenario_path), '--out', str(out),
                 '--pump', PUMP_SPEC]) == 0
    result = json.loads((out / 'result.json').read_text())
    assert result['status'] == 'PASS'
    assert len(result['record_sha256s']) == 2
    assert 'not_claimed' in result and 'XInput' in result['not_claimed']
