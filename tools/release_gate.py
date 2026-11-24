#!/usr/bin/env python3
"""Hard gate for cutting a GameReComp release.

A release is allowed only when CLI-supplied evidence proves:
  1. native binaries exist and hash to the declared values,
  2. screenshots exist, hash to the declared values and are non-blank,
  3. every named game has an oracle-backed PASS verdict,
  4. the roadmap gates for the target version are complete,
  5. full verification commands passed (supplied as a JSON record).

Fail closed: a missing, malformed, undecodable or self-contradictory input is
BLOCKED or NOT TESTED and the exit status is non-zero. The tool never upgrades
a status, never invents a hash and never reads a path that was not supplied
through the CLI (a game proof path inside the CLI-supplied evidence file is
still CLI-supplied).

Input schema (all three files are required):

  --evidence  {"binaries": [{"path": ..., "sha256": ...}, ...],
               "screenshots": [{"path": ..., "sha256": ...}, ...],
               "games": {"name": {"verdict": "PASS",
                                  "oracle": "retail-windows",
                                  "proof": "<path to proof JSON>",
                                  "sha256": "<optional proof hash>"}, ...}}
  --releases  {"version": "0.0.1", "roadmap": {"GATE_1": true, ...}}
  --verification {"pytest": true, "selftests": true,
                  "difftest": true, "differential_gate": true}

A bare string verdict is *not* enough for PASS: the proof JSON must itself
record PASS plus an oracle. PARTIAL never qualifies, whatever the proof says.
Screenshot blankness is recomputed from the image bytes; a self-reported
``"blank": false`` cannot turn a single-colour image into evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

BLOCKED = 'BLOCKED'
NOT_TESTED = 'NOT TESTED'
READY = 'READY'
PASS = 'PASS'
ACCEPTED = {PASS}
CHECKS = ('binaries', 'screenshots', 'games', 'roadmap', 'verification')
VERIFICATION_KEYS = ('pytest', 'selftests', 'difftest', 'differential_gate')
RULE = 'PASS requires oracle-backed game verdicts; PARTIAL never qualifies'
_HEX = set('0123456789abcdefABCDEF')
_NO_ORACLE = {'', 'none', 'missing', 'n/a', 'na', 'unproven', 'partial', 'not tested',
              NOT_TESTED.lower()}


class LoadError(Exception):
    """A CLI input is absent, unreadable or not a JSON object."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _load(path_text: str | None) -> dict:
    """Parse one CLI-supplied JSON object; raise LoadError instead of guessing."""
    if not path_text:
        raise LoadError('MISSING_INPUT: no input path supplied')
    p = Path(path_text)
    if not p.is_file():
        raise LoadError(f'MISSING_INPUT: {p}')
    try:
        doc = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise LoadError(f'INVALID_JSON: {p}: {exc}') from exc
    if not isinstance(doc, dict):
        raise LoadError(f'INVALID_STRUCTURE: {p}: expected a JSON object')
    return doc


def _is_hash(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in _HEX for ch in value))


def _path_of(row: dict, label: str, bad: list[str]) -> Path | None:
    text = row.get('path')
    if not isinstance(text, str) or not text.strip():
        bad.append(f'{label}: no path declared')
        return None
    return Path(text)


def _file_hash(p: Path, declared, bad: list[str]) -> str | None:
    """Check a declared hash; return the actual digest when it matched."""
    if not _is_hash(declared):
        bad.append(f'{p.name}: invalid declared sha256')
        return None
    if not p.is_file():
        bad.append(f'{p.name}: missing')
        return None
    try:
        actual = sha256(p)
    except OSError:
        bad.append(f'{p.name}: unreadable')
        return None
    if actual.lower() != declared.lower():
        bad.append(f'{p.name}: hash mismatch')
        return None
    return actual


# --- screenshot blankness, computed from the file itself (stdlib only) ------

def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _png_colors(data: bytes) -> int | None:
    """Distinct pixel values (capped at 2) for 8-bit non-interlaced PNG."""
    pos = 8
    header = None
    palette = b''
    compressed = []
    while pos + 8 <= len(data):
        size = struct.unpack_from('>I', data, pos)[0]
        tag = data[pos + 4:pos + 8]
        end = pos + 12 + size
        if end > len(data):
            return None
        chunk = data[pos + 8:pos + 8 + size]
        if tag == b'IHDR':
            header = chunk
        elif tag == b'PLTE':
            palette = chunk
        elif tag == b'IDAT':
            compressed.append(chunk)
        elif tag == b'IEND':
            break
        pos = end
    if header is None or len(header) != 13 or not compressed:
        return None
    width, height, depth, ctype, compression, filtering, interlace = \
        struct.unpack('>IIBBBBB', header)
    if (depth != 8 or interlace != 0 or compression != 0 or filtering != 0
            or ctype not in (0, 2, 3, 4, 6) or width == 0 or height == 0):
        return None
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    try:
        raw = zlib.decompress(b''.join(compressed))
    except zlib.error:
        return None
    stride = width * channels
    if len(raw) < height * (stride + 1):
        return None
    colors: set[bytes] = set()
    prev = bytearray(stride)
    off = 0
    for _ in range(height):
        filter_type = raw[off]
        line = bytearray(raw[off + 1:off + 1 + stride])
        off += stride + 1
        if filter_type not in (0, 1, 2, 3, 4):
            return None
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if filter_type == 1:
                line[i] = (line[i] + a) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + b) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif filter_type == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        for x in range(width):
            px = line[x * channels:(x + 1) * channels]
            if ctype == 3:
                index = px[0]
                if (index + 1) * 3 > len(palette):
                    return None
                colors.add(palette[index * 3:index * 3 + 3])
            elif ctype in (0, 4):
                colors.add(bytes(px[:1]))
            else:
                colors.add(bytes(px[:3]))
            if len(colors) > 1:
                return 2
        prev = line
    return len(colors)


def _bmp_colors(data: bytes) -> int | None:
    """Distinct pixel values (capped at 2) for uncompressed 24-bpp BMP."""
    if len(data) < 54 or data[:2] != b'BM':
        return None
    off = struct.unpack_from('<I', data, 10)[0]
    width, height = struct.unpack_from('<ii', data, 18)
    bpp = struct.unpack_from('<H', data, 28)[0]
    compression = struct.unpack_from('<I', data, 30)[0]
    if bpp != 24 or compression != 0 or width <= 0 or height <= 0:
        return None
    stride = ((width * 3 + 3) // 4) * 4
    if off + stride * height > len(data):
        return None
    colors: set[bytes] = set()
    for y in range(height):
        row = off + (height - 1 - y) * stride
        for x in range(width):
            p = row + x * 3
            colors.add(data[p:p + 3])
            if len(colors) > 1:
                return 2
    return len(colors)


def _distinct_colors(p: Path) -> int | None:
    data = p.read_bytes()
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return _png_colors(data)
    if data[:2] == b'BM':
        return _bmp_colors(data)
    return None


# --- checks -----------------------------------------------------------------

def _check_binaries(evidence: dict, reasons: list[str], checks: dict) -> None:
    rows = evidence.get('binaries')
    if not isinstance(rows, list) or not rows:
        reasons.append('no native binaries declared')
        checks['binaries'] = BLOCKED
        return
    bad: list[str] = []
    for index, row in enumerate(rows):
        label = f'binaries[{index}]'
        if not isinstance(row, dict):
            bad.append(f'{label}: not an object')
            continue
        p = _path_of(row, label, bad)
        if p is not None:
            _file_hash(p, row.get('sha256'), bad)
    checks['binaries'] = PASS if not bad else BLOCKED
    if bad:
        reasons.append('binaries: ' + ', '.join(bad[:5]))


def _check_screenshots(evidence: dict, reasons: list[str], checks: dict) -> None:
    rows = evidence.get('screenshots')
    if not isinstance(rows, list) or not rows:
        reasons.append('no screenshots declared')
        checks['screenshots'] = BLOCKED
        return
    bad: list[str] = []
    for index, row in enumerate(rows):
        label = f'screenshots[{index}]'
        if not isinstance(row, dict):
            bad.append(f'{label}: not an object')
            continue
        p = _path_of(row, label, bad)
        if p is None:
            continue
        if _file_hash(p, row.get('sha256'), bad) is None:
            continue
        # A declared "blank": false is a claim, not proof: decode the pixels.
        if row.get('blank') is True:
            bad.append(f'{p.name}: blank frame')
            continue
        try:
            colors = _distinct_colors(p)
        except (OSError, ValueError, struct.error, zlib.error):
            colors = None
        if colors is None:
            bad.append(f'{p.name}: undecodable screenshot (blank unproven)')
        elif colors <= 1:
            bad.append(f'{p.name}: blank frame')
    checks['screenshots'] = PASS if not bad else BLOCKED
    if bad:
        reasons.append('screenshots: ' + ', '.join(bad[:5]))


def _game_fields(row):
    if isinstance(row, str):
        return row, '', '', ''
    if not isinstance(row, dict):
        return '', '', '', ''
    verdict = row.get('verdict', row.get('status'))
    oracle = row.get('oracle')
    if isinstance(oracle, dict):
        oracle = oracle.get('type') or oracle.get('name') or oracle.get('status')
    proof = row.get('proof', row.get('proof_path'))
    return verdict, oracle, proof, row.get('sha256')


def _oracle_named(value) -> bool:
    return (isinstance(value, str) and value.strip().lower() not in _NO_ORACLE)


def _check_games(evidence: dict, reasons: list[str], checks: dict) -> None:
    rows = evidence.get('games')
    if not isinstance(rows, dict) or not rows:
        reasons.append('no game verdicts declared')
        checks['games'] = BLOCKED
        return
    bad: list[str] = []
    for name, row in rows.items():
        verdict, oracle, proof, proof_hash = _game_fields(row)
        norm = verdict.strip().upper() if isinstance(verdict, str) else ''
        if norm not in ACCEPTED:
            bad.append(f'{name}: {verdict or "no verdict"} is not an oracle-backed PASS')
            continue
        if not _oracle_named(oracle):
            bad.append(f'{name}: PASS without an oracle')
            continue
        if not isinstance(proof, str) or not proof.strip():
            bad.append(f'{name}: PASS without an oracle proof')
            continue
        p = Path(proof)
        if not p.is_file():
            bad.append(f'{name}: oracle proof missing: {p.name}')
            continue
        try:
            doc = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, ValueError):
            bad.append(f'{name}: oracle proof invalid JSON: {p.name}')
            continue
        if not isinstance(doc, dict):
            bad.append(f'{name}: oracle proof is not an object: {p.name}')
            continue
        if proof_hash is not None:
            try:
                digest = sha256(p)
            except OSError:
                bad.append(f'{name}: oracle proof unreadable: {p.name}')
                continue
            if not _is_hash(proof_hash) or digest.lower() != proof_hash.lower():
                bad.append(f'{name}: oracle proof hash mismatch (tampered): {p.name}')
                continue
        recorded = doc.get('verdict', doc.get('status'))
        recorded_norm = recorded.strip().upper() if isinstance(recorded, str) else ''
        if recorded_norm != PASS:
            bad.append(f'{name}: proof says {recorded or "no verdict"}, not PASS')
        elif not _oracle_named(doc.get('oracle')):
            bad.append(f'{name}: proof has no oracle')
    checks['games'] = PASS if not bad else BLOCKED
    if bad:
        reasons.append('games not fully tested: ' + ', '.join(bad[:8]))


def _gate_done(value) -> bool:
    if value is True:
        return True
    return isinstance(value, dict) and value.get('complete') is True


def _check_roadmap(releases: dict, reasons: list[str], checks: dict) -> None:
    roads = releases.get('roadmap')
    if not isinstance(roads, dict) or not roads:
        reasons.append('no roadmap gates declared')
        checks['roadmap'] = BLOCKED
        return
    bad = [name for name, done in roads.items() if not _gate_done(done)]
    checks['roadmap'] = PASS if not bad else BLOCKED
    if bad:
        reasons.append('roadmap gates incomplete: ' + ', '.join(sorted(bad)[:8]))


def _check_verification(verification: dict, reasons: list[str], checks: dict) -> None:
    bad = [name for name in VERIFICATION_KEYS if verification.get(name) is not True]
    checks['verification'] = PASS if not bad else BLOCKED
    if bad:
        reasons.append('verification failed or absent: ' + ', '.join(bad))


def _blocked(reasons: list[str], status: str = BLOCKED) -> dict:
    return {
        'status': status,
        'release_allowed': False,
        'checks': {name: NOT_TESTED for name in CHECKS},
        'reasons': reasons,
        'rule': RULE,
    }


def evaluate(evidence: dict, releases: dict, verification: dict) -> dict:
    """Pure decision function; reads only paths supplied in the input documents."""
    reasons: list[str] = []
    checks: dict[str, str] = {name: NOT_TESTED for name in CHECKS}
    for name, value in (('evidence', evidence), ('releases', releases),
                        ('verification', verification)):
        if not isinstance(value, dict):
            reasons.append(f'{name}: expected a JSON object')
    if reasons:
        return _blocked(reasons, NOT_TESTED)
    _check_binaries(evidence, reasons, checks)
    _check_screenshots(evidence, reasons, checks)
    _check_games(evidence, reasons, checks)
    _check_roadmap(releases, reasons, checks)
    _check_verification(verification, reasons, checks)
    status = READY if not reasons else BLOCKED
    return {
        'status': status,
        'release_allowed': status == READY,
        'checks': checks,
        'reasons': reasons,
        'rule': RULE,
    }


def _emit(result: dict, out_path: str | None) -> None:
    text = json.dumps(result, indent=1, sort_keys=True) + '\n'
    if out_path:
        Path(out_path).write_text(text, encoding='utf-8')
    print(text, end='')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--evidence', required=True, help='JSON: binaries, screenshots, games')
    ap.add_argument('--releases', required=True, help='JSON: roadmap gate map')
    ap.add_argument('--verification', required=True, help='JSON: boolean verification results')
    ap.add_argument('--json', help='write the decision JSON here')
    args = ap.parse_args(argv)
    try:
        evidence = _load(args.evidence)
        releases = _load(args.releases)
        verification = _load(args.verification)
    except LoadError as exc:
        print(str(exc), file=sys.stderr)
        result = _blocked([str(exc)], NOT_TESTED)
    else:
        result = evaluate(evidence, releases, verification)
    try:
        _emit(result, args.json)
    except OSError as exc:
        print(f'CANNOT_WRITE_DECISION: {args.json}: {exc}', file=sys.stderr)
        return 3
    return 0 if result['release_allowed'] else 3


if __name__ == '__main__':
    sys.exit(main())
