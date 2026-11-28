#!/usr/bin/env python3
"""Independently verify a frame-proof JSON against the frame files on disk.

The input document holds *claims*: paths, SHA-256 digests and (optionally)
colour counts. This tool trusts none of them. For every claimed frame it

* locates the actual BMP/PNG file (CLI ``--frame`` paths, or the document's
  own path resolved under ``--root``),
* recomputes SHA-256 from the file bytes and compares it with every hash the
  document claims for that frame, and
* decodes 24-bpp uncompressed BMP or 8-bit non-interlaced PNG (colour types
  0, 2, 4 and 6) to count distinct RGB colours. A frame with at most one
  colour is reported BLANK.

Fail closed is the rule: a missing path, a hash mismatch, an unsupported or
undecodable format produces status FAIL with a concrete reason and
``distinct_colors``/``blank`` left as ``null``. No colour count is ever copied
from the claim and no pixel is inferred from a file extension. The verdict is
deterministic JSON (no timestamps, fixed frame order, sorted keys).

This tool reads bytes; it never runs a game, a native binary, Frida or
Unicorn, and it says nothing about a Windows oracle.

Usage::

    python3 -m tools.frame_evidence_check --proof <frame-proof.json> \
        [--root <dir>] [--frame <image> ...] [--out <verdict.json>]

Exit status is 0 only for a PASS verdict; any FAIL returns 1 after the report
has been written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
PIXEL_LIMIT = 8_000_000
COLOUR_LIMIT = 1_000_000
PATH_KEYS = ('path', 'committed_png', 'committed_screenshot', 'image')
HASH_KEYS = ('sha256', 'committed_screenshot_sha256',
             'committed_png_sha256', 'source_bmp_sha256')
HEX_DIGITS = frozenset('0123456789abcdefABCDEF')


class ProofError(ValueError):
    """The CLI-supplied proof JSON cannot be used as a claim set."""


def _reason(code: str, detail: str) -> dict:
    return {'code': code, 'detail': detail}


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_bmp24(data: bytes) -> dict:
    """Decode an uncompressed 24-bpp BMP far enough to count colours."""
    if len(data) < 54 or data[:2] != b'BM':
        return {'error': 'not a 24-bpp BMP'}
    offset = struct.unpack_from('<I', data, 10)[0]
    dib_size = struct.unpack_from('<I', data, 14)[0]
    width, signed_height = struct.unpack_from('<ii', data, 18)
    planes = struct.unpack_from('<H', data, 26)[0]
    bpp = struct.unpack_from('<H', data, 28)[0]
    compression = struct.unpack_from('<I', data, 30)[0]
    if (dib_size < 40 or offset < 54 or planes != 1 or bpp != 24
            or compression != 0 or width <= 0 or signed_height == 0):
        return {'error': 'unsupported BMP variant (need uncompressed 24-bpp)'}
    height = abs(signed_height)
    if width * height > PIXEL_LIMIT:
        return {'error': 'BMP too large to verify'}
    stride = ((width * 3 + 3) // 4) * 4
    if offset + stride * height > len(data):
        return {'error': 'truncated BMP pixel data'}
    colours: set[tuple[int, int, int]] = set()
    top_down = signed_height < 0
    for y in range(height):
        row = offset + (y if top_down else height - 1 - y) * stride
        for x in range(width):
            p = row + x * 3
            colours.add((data[p + 2], data[p + 1], data[p]))  # BGR -> RGB
            if len(colours) > COLOUR_LIMIT:
                return {'error': 'distinct colour count exceeds verification cap'}
    return {'format': 'bmp24', 'width': width, 'height': height,
            'distinct_colors': len(colours), 'blank': len(colours) <= 1}


def decode_png8(data: bytes) -> dict:
    """Decode an 8-bit non-interlaced PNG far enough to count colours."""
    if not data.startswith(PNG_SIGNATURE):
        return {'error': 'not a PNG'}
    pos, ihdr, idat, seen_iend = 8, None, bytearray(), False
    while pos + 12 <= len(data):
        length = struct.unpack_from('>I', data, pos)[0]
        kind = data[pos + 4:pos + 8]
        if pos + 12 + length > len(data):
            return {'error': 'truncated PNG chunk'}
        payload = data[pos + 8:pos + 8 + length]
        crc = struct.unpack_from('>I', data, pos + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            return {'error': 'bad CRC for PNG chunk'}
        if ihdr is None:
            if kind != b'IHDR' or length != 13:
                return {'error': 'PNG IHDR must be the first chunk (13 bytes)'}
            ihdr = payload
        elif kind == b'IDAT':
            idat.extend(payload)
        elif kind == b'IEND':
            if length != 0:
                return {'error': 'bad PNG IEND length'}
            seen_iend = True
            break
        pos += 12 + length
    if ihdr is None or not seen_iend or not idat:
        return {'error': 'incomplete PNG'}
    width, height, depth, colour_type, comp, filt, interlace = struct.unpack(
        '>IIBBBBB', ihdr)
    if (depth != 8 or interlace != 0 or comp != 0 or filt != 0
            or width == 0 or height == 0):
        return {'error': 'unsupported PNG variant (need 8-bit, non-interlaced)'}
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colour_type)
    if channels is None:
        return {'error': 'unsupported PNG colour type (need 0, 2, 4 or 6)'}
    if width * height > PIXEL_LIMIT:
        return {'error': 'PNG too large to verify'}
    stride = width * channels
    expected = height * (stride + 1)
    stream = zlib.decompressobj()
    raw = stream.decompress(bytes(idat), expected + 1)
    if len(raw) != expected or not stream.eof:
        return {'error': 'unexpected PNG scanline size'}

    colours: set[tuple[int, int, int]] = set()
    previous = bytearray(stride)
    pos = 0
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        row = bytearray(raw[pos:pos + stride])
        pos += stride
        for i in range(stride):
            left = row[i - channels] if i >= channels else 0
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
            elif filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            else:
                return {'error': f'unknown PNG filter {filter_type}'}
            row[i] = (row[i] + predictor) & 0xFF
        for x in range(width):
            if channels == 1:
                grey = row[x]
                colour = (grey, grey, grey)
            elif channels == 2:
                grey = row[x * 2]
                colour = (grey, grey, grey)
            elif channels == 3:
                colour = tuple(row[x * 3:x * 3 + 3])
            else:  # RGBA: alpha is transparency, not a colour
                colour = tuple(row[x * 4:x * 4 + 3])
            colours.add(colour)
            if len(colours) > COLOUR_LIMIT:
                return {'error': 'distinct colour count exceeds verification cap'}
        previous = row
    return {'format': 'png8', 'width': width, 'height': height,
            'distinct_colors': len(colours), 'blank': len(colours) <= 1}


def frame_info(data: bytes) -> dict:
    """Hash and decode raw frame bytes; unsupported data yields an error."""
    if data[:2] == b'BM':
        return decode_bmp24(data)
    if data.startswith(PNG_SIGNATURE):
        return decode_png8(data)
    return {'error': 'unsupported frame format (need 24-bpp BMP or 8-bit PNG)'}


def load_proof(path: Path) -> dict:
    """Read the claim document, failing closed on malformed input."""
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProofError(f'cannot read proof {path}: {exc}') from exc
    if not isinstance(doc, dict):
        raise ProofError('proof root must be a JSON object')
    if 'schema' in doc and doc['schema'] != 1:
        raise ProofError(f'unsupported proof schema {doc["schema"]!r}')
    frames = doc.get('frames')
    if not isinstance(frames, list) or any(not isinstance(f, dict) for f in frames):
        raise ProofError('proof "frames" must be a list of JSON objects')
    return doc


def _claim_path(claim: dict) -> str | None:
    for key in PATH_KEYS:
        value = claim.get(key)
        if isinstance(value, str) and value.strip() and value != '-':
            return value
    return None


def _claim_hashes(claim: dict) -> list[dict]:
    hashes = []
    for key in HASH_KEYS:
        value = claim.get(key)
        if (isinstance(value, str) and len(value) == 64
                and all(ch in HEX_DIGITS for ch in value)):
            hashes.append({'key': key, 'sha256': value.lower()})
    return hashes


def _new_row(index: int, claim: dict) -> dict:
    path = _claim_path(claim)
    return {
        'index': index,
        'claim_path': path,
        'actual_path': None,
        'claimed_sha256': _claim_hashes(claim),
        'actual_sha256': None,
        'hash_match': False,
        'format': None,
        'width': None,
        'height': None,
        'distinct_colors': None,
        'blank': None,
        'status': 'FAIL',
        'reasons': [],
    }


def verify(proof: dict, root: Path, cli_frames: list[Path] | None = None) -> dict:
    """Verify every claimed frame; all facts come from bytes on disk."""
    root = Path(root)
    supplied = {}
    global_reasons = []
    for frame in cli_frames or []:
        name = Path(frame).name
        if name in supplied:
            global_reasons.append(_reason(
                'AMBIGUOUS_CLI_FRAME', f'duplicate CLI frame name {name!r}'))
        else:
            supplied[name] = Path(frame)

    rows = []
    for index, claim in enumerate(proof.get('frames', [])):
        row = _new_row(index, claim)
        reasons = row['reasons']
        claim_path = row['claim_path']
        if claim_path is None:
            reasons.append(_reason('MISSING_PATH', 'frame has no claim path'))
            rows.append(row)
            continue
        if supplied:
            actual = supplied.get(Path(claim_path).name)
            if actual is None:
                reasons.append(_reason(
                    'MISSING_PATH', f'no CLI --frame for claim {claim_path!r}'))
                rows.append(row)
                continue
        else:
            actual = Path(claim_path)
            if not actual.is_absolute():
                actual = root / actual
        row['actual_path'] = str(actual)
        if not actual.is_file():
            reasons.append(_reason(
                'MISSING_PATH', f'frame file is not present: {actual}'))
            rows.append(row)
            continue
        try:
            data = actual.read_bytes()
        except OSError as exc:
            reasons.append(_reason('UNREADABLE_FRAME', f'{actual}: {exc}'))
            rows.append(row)
            continue
        digest = hashlib.sha256(data).hexdigest()
        row['actual_sha256'] = digest
        hashes = row['claimed_sha256']
        if not hashes:
            reasons.append(_reason(
                'NO_HASH_CLAIM', 'frame has no usable 64-hex SHA-256 claim'))
            rows.append(row)
            continue
        matched = [h['key'] for h in hashes if h['sha256'] == digest]
        if not matched:
            reasons.append(_reason(
                'HASH_MISMATCH',
                f'actual sha256 {digest} matches none of '
                f'{", ".join(h["key"] for h in hashes)}'))
            rows.append(row)
            continue
        row['hash_match'] = True
        reasons.append(_reason('HASH_MATCH', f'matched claim key(s): {", ".join(matched)}'))
        info = frame_info(data)
        if 'error' in info:
            reasons.append(_reason('UNDECODABLE', info['error']))
            rows.append(row)
            continue
        row.update({key: info[key] for key in
                    ('format', 'width', 'height', 'distinct_colors', 'blank')})
        reasons.append(_reason(
            'BLANK' if row['blank'] else 'NONBLANK',
            f'{row["distinct_colors"]} distinct RGB colour(s)'))
        row['status'] = 'PASS'
        rows.append(row)

    if supplied:
        claimed_names = {Path(r['claim_path']).name for r in rows
                         if r['claim_path'] is not None}
        for name in sorted(set(supplied) - claimed_names):
            global_reasons.append(_reason(
                'UNMATCHED_CLI_FRAME', f'CLI frame {name!r} is not claimed by the proof'))

    passed = sum(1 for r in rows if r['status'] == 'PASS')
    status = 'PASS' if rows and passed == len(rows) and not global_reasons else 'FAIL'
    if not rows:
        global_reasons.append(_reason('NO_FRAMES', 'proof claims no frames'))
    return {
        'schema': 1,
        'tool': 'frame_evidence_check',
        'what_it_proves': ('claimed frames are present, byte-identical and decodable; '
                           'blank is a measured classification, not a Windows oracle'),
        'status': status,
        'summary': {
            'frames': len(rows),
            'pass': passed,
            'fail': len(rows) - passed,
            'blank': sum(1 for r in rows if r['blank'] is True),
            'nonblank': sum(1 for r in rows if r['blank'] is False),
        },
        'global_reasons': global_reasons,
        'frames': rows,
    }


def error_verdict(reason: dict) -> dict:
    """A deterministic FAIL verdict for an unreadable claim document."""
    return {
        'schema': 1,
        'tool': 'frame_evidence_check',
        'what_it_proves': 'nothing; the claim document could not be verified',
        'status': 'FAIL',
        'summary': {'frames': 0, 'pass': 0, 'fail': 0, 'blank': 0, 'nonblank': 0},
        'global_reasons': [reason],
        'frames': [],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--proof', required=True, type=Path,
                    help='frame-proof JSON holding the claims')
    ap.add_argument('--root', type=Path,
                    help='base directory for relative claim paths '
                         '(default: the proof file directory)')
    ap.add_argument('--frame', action='append', type=Path, default=[],
                    help='actual BMP/PNG frame path (repeatable, matched by '
                         'filename to a claim); overrides claim paths')
    ap.add_argument('--out', type=Path, help='write the JSON verdict here')
    args = ap.parse_args(argv)
    root = args.root if args.root is not None else args.proof.parent
    try:
        proof = load_proof(args.proof)
        verdict = verify(proof, root, args.frame)
    except ProofError as exc:
        verdict = error_verdict(_reason('INVALID_PROOF', str(exc)))
    text = json.dumps(verdict, indent=2, sort_keys=True,
                      ensure_ascii=True, allow_nan=False) + '\n'
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding='utf-8')
    print(text, end='')
    return 0 if verdict['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
