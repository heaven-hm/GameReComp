"""Tests for the fail-closed frame-proof verifier.

Every frame is generated in-memory here: synthetic 24-bpp BMPs and 8-bit
non-interlaced PNGs. The tests cover a real non-blank frame, a blank frame, a
tampered hash, a missing path and an unsupported format, plus CLI exit status
and byte-for-byte deterministic output.
"""
import hashlib
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from tools.frame_evidence_check import decode_png8, verify

ROOT = Path(__file__).resolve().parents[1]
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


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
        for x, (red, green, blue) in enumerate(image_row):
            data[off + x * 3:off + x * 3 + 3] = bytes((blue, green, red))
    return bytes(data)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack('>I', len(payload)) + kind + payload +
            struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def make_png(pixels: list[list[tuple[int, int, int]]],
             colour_type: int = 2, filter_type: int = 0) -> bytes:
    """Encode a tiny PNG with a properly applied per-row PNG filter."""
    height, width = len(pixels), len(pixels[0])
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour_type]
    raw = bytearray()
    previous = bytearray(width * channels)
    for row in pixels:
        current = bytearray()
        for colour in row:
            if colour_type in (0, 4):
                current.append(colour[0])  # grayscale
            elif colour_type == 2:
                current.extend(colour)
            else:  # RGBA
                current.extend((*colour, 255))
        raw.append(filter_type)
        for i, value in enumerate(current):
            left = current[i - channels] if i >= channels else 0
            above = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) >> 1
            else:
                predictor = _paeth(left, above, upper_left)
            raw.append((value - predictor) & 0xFF)
        previous = current
    return (PNG_SIGNATURE +
            png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8,
                                           colour_type, 0, 0, 0)) +
            png_chunk(b'IDAT', zlib.compress(bytes(raw))) +
            png_chunk(b'IEND', b''))


NONBLANK = [[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (255, 255, 255)]]
BLANK = [[(1, 2, 3), (1, 2, 3)], [(1, 2, 3), (1, 2, 3)]]


def claim(path: str, data: bytes, **extra) -> dict:
    row = {'path': path, 'sha256': hashlib.sha256(data).hexdigest()}
    row.update(extra)
    return row


def codes(row: dict) -> set[str]:
    return {reason['code'] for reason in row['reasons']}


def run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, '-m', 'tools.frame_evidence_check', *map(str, args)],
        cwd=ROOT, capture_output=True, text=True)


def test_nonblank_bmp_and_png_frames_are_verified(tmp_path):
    bmp = make_bmp24(NONBLANK)
    png = make_png(NONBLANK)
    (tmp_path / 'real.bmp').write_bytes(bmp)
    (tmp_path / 'real.png').write_bytes(png)
    verdict = verify({'frames': [claim('real.bmp', bmp),
                                 claim('real.png', png)]}, tmp_path)
    assert verdict['status'] == 'PASS'
    assert verdict['summary'] == {
        'frames': 2, 'pass': 2, 'fail': 0, 'blank': 0, 'nonblank': 2}
    for row in verdict['frames']:
        assert row['status'] == 'PASS'
        assert row['hash_match'] is True
        assert row['distinct_colors'] == 4
        assert row['blank'] is False
        assert codes(row) == {'HASH_MATCH', 'NONBLANK'}
    assert [row['format'] for row in verdict['frames']] == ['bmp24', 'png8']


def test_blank_frame_is_marked_blank_without_inventing_colours(tmp_path):
    bmp = make_bmp24(BLANK)
    png = make_png(BLANK, colour_type=6, filter_type=4)
    (tmp_path / 'blank.bmp').write_bytes(bmp)
    (tmp_path / 'blank.png').write_bytes(png)
    verdict = verify({'frames': [claim('blank.bmp', bmp, blank=False,
                                       distinct_colors=9),
                                 claim('blank.png', png, blank=False)]}, tmp_path)
    assert verdict['status'] == 'PASS'
    assert verdict['summary']['blank'] == 2
    for row in verdict['frames']:
        assert row['distinct_colors'] == 1  # never copied from the claim
        assert row['blank'] is True
        assert 'BLANK' in codes(row)


def test_tampered_hash_fails_closed(tmp_path):
    data = make_bmp24(NONBLANK)
    (tmp_path / 'real.bmp').write_bytes(data)
    forged = claim('real.bmp', b'not-the-frame-bytes', distinct_colors=4)
    verdict = verify({'frames': [forged]}, tmp_path)
    assert verdict['status'] == 'FAIL'
    row = verdict['frames'][0]
    assert 'HASH_MISMATCH' in codes(row)
    assert row['actual_sha256'] == hashlib.sha256(data).hexdigest()
    assert row['distinct_colors'] is None
    assert row['blank'] is None
    assert row['format'] is None


def test_missing_path_fails_closed(tmp_path):
    verdict = verify({'frames': [claim('absent.bmp', make_bmp24(NONBLANK))]},
                     tmp_path)
    assert verdict['status'] == 'FAIL'
    row = verdict['frames'][0]
    assert 'MISSING_PATH' in codes(row)
    assert row['actual_sha256'] is None
    assert row['distinct_colors'] is None


def test_unsupported_and_undecodable_formats_fail_closed(tmp_path):
    data = make_bmp24(NONBLANK)
    gif = b'GIF89a' + b'\0' * 48
    eight_bit = data[:28] + struct.pack('<H', 8) + data[30:]
    bad_crc = make_png(NONBLANK)[:-4] + b'\0\0\0\0'
    (tmp_path / 'not-an-image.bmp').write_bytes(gif)
    (tmp_path / 'eight-bit.bmp').write_bytes(eight_bit)
    (tmp_path / 'bad-crc.png').write_bytes(bad_crc)
    verdict = verify({'frames': [
        claim('not-an-image.bmp', gif),
        claim('eight-bit.bmp', eight_bit),
        claim('bad-crc.png', bad_crc),
    ]}, tmp_path)
    assert verdict['status'] == 'FAIL'
    assert all('UNDECODABLE' in codes(row) for row in verdict['frames'])
    assert all(row['distinct_colors'] is None for row in verdict['frames'])
    assert all(row['blank'] is None for row in verdict['frames'])
    assert 'unsupported frame format' in verdict['frames'][0]['reasons'][1]['detail']
    assert 'unsupported BMP variant' in verdict['frames'][1]['reasons'][1]['detail']
    assert 'bad CRC' in verdict['frames'][2]['reasons'][1]['detail']


def test_png_filters_zero_through_four_decode_identically(tmp_path):
    for filter_type in range(5):
        png = make_png(NONBLANK, filter_type=filter_type)
        info = decode_png8(png)
        assert info['distinct_colors'] == 4, filter_type
        assert info['blank'] is False


def test_cli_writes_deterministic_verdict_and_exit_status(tmp_path):
    png = make_png(NONBLANK)
    (tmp_path / 'frame.png').write_bytes(png)
    proof = tmp_path / 'proof.json'
    proof.write_text(json.dumps({'schema': 1, 'frames': [
        claim('frame.png', png, distinct_colors=1, blank=True)]}))
    first = run_cli('--proof', proof, '--out', tmp_path / 'verdict.json')
    assert first.returncode == 0, first.stderr
    written = (tmp_path / 'verdict.json').read_text()
    assert written == first.stdout
    verdict = json.loads(written)
    assert verdict['status'] == 'PASS'
    assert verdict['frames'][0]['distinct_colors'] == 4
    assert verdict['frames'][0]['blank'] is False
    second = run_cli('--proof', proof, '--out', tmp_path / 'verdict2.json')
    assert second.returncode == 0
    assert second.stdout == first.stdout
    assert (tmp_path / 'verdict2.json').read_text() == written


def test_cli_supplied_frame_paths_are_used(tmp_path):
    bmp = make_bmp24(NONBLANK)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (elsewhere / 'frame.bmp').write_bytes(bmp)
    proof = tmp_path / 'proof.json'
    proof.write_text(json.dumps({'frames': [claim('old/frame.bmp', bmp)]}))
    result = run_cli('--proof', proof, '--frame', elsewhere / 'frame.bmp')
    assert result.returncode == 0, result.stderr
    verdict = json.loads(result.stdout)
    assert verdict['frames'][0]['actual_path'] == str(elsewhere / 'frame.bmp')
    assert verdict['frames'][0]['status'] == 'PASS'


def test_empty_proof_and_invalid_proof_fail_closed(tmp_path):
    empty = tmp_path / 'empty.json'
    empty.write_text(json.dumps({'frames': []}))
    result = run_cli('--proof', empty)
    assert result.returncode == 1
    assert json.loads(result.stdout)['global_reasons'][0]['code'] == 'NO_FRAMES'
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json')
    result = run_cli('--proof', bad)
    assert result.returncode == 1
    assert json.loads(result.stdout)['global_reasons'][0]['code'] == 'INVALID_PROOF'


def test_missing_hash_claim_fails_closed(tmp_path):
    data = make_png(NONBLANK)
    (tmp_path / 'f.png').write_bytes(data)
    verdict = verify({'frames': [{'path': 'f.png'}]}, tmp_path)
    assert verdict['status'] == 'FAIL'
    assert 'NO_HASH_CLAIM' in codes(verdict['frames'][0])
    assert verdict['frames'][0]['actual_sha256'] is not None
    assert verdict['frames'][0]['distinct_colors'] is None


if __name__ == '__main__':
    pytest.main([__file__])
