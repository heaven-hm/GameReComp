#!/usr/bin/env python3
"""Prove captured frames are real rendered images, not blank buffers.

Reads the frames named by game_runner's summary.json, decodes the runtime's
uncompressed 24-bpp BMPs, and records per-frame SHA-256, dimensions and the
number of distinct RGB colours. A single-colour frame is flagged blank and is
never counted as graphics evidence. The HTML gallery shows the native macOS
frames side by side; it deliberately contains no "Windows original" column,
because no original-Windows execution oracle exists in this environment.

Usage: python3 -m tools.frame_proof --summary <summary.json> --out <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import struct
from pathlib import Path


def bmp24(path: Path) -> dict:
    data = path.read_bytes()
    if data[:2] != b'BM':
        return {'error': 'not a BMP'}
    off = struct.unpack_from('<I', data, 10)[0]
    w, h = struct.unpack_from('<ii', data, 18)
    bpp = struct.unpack_from('<H', data, 28)[0]
    comp = struct.unpack_from('<I', data, 30)[0]
    if bpp != 24 or comp != 0 or w <= 0 or h <= 0:
        return {'width': w, 'height': h, 'bpp': bpp, 'error': 'unsupported BMP variant'}
    stride = ((w * 3 + 3) // 4) * 4
    colors = set()
    for y in range(h):
        row = off + (h - 1 - y) * stride
        for x in range(w):
            p = row + x * 3
            colors.add(data[p:p + 3])
        if len(colors) > 100000:
            break
    digest = hashlib.sha256(data).hexdigest()
    return {'width': w, 'height': h, 'bpp': bpp, 'sha256': digest,
            'distinct_colors': len(colors), 'blank': len(colors) == 1,
            'bytes': len(data)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--summary', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    data = json.loads(args.summary.read_text())
    rows = []
    for rec in data['results']:
        for frame in rec.get('frames', []):
            p = Path(frame['path'])
            info = bmp24(p) if p.exists() else {'error': 'frame file missing'}
            info.update({'game': rec['game'], 'path': str(p)})
            rows.append(info)
            print(f"{rec['game']:18} {p.name:16} "
                  f"{info.get('width', '?')}x{info.get('height', '?')} "
                  f"colors={info.get('distinct_colors', '?')} "
                  f"blank={info.get('blank', '?')}")
    summary = args.out / 'frame-proof.json'
    summary.write_text(json.dumps({
        'schema': 1,
        'what_it_proves': 'captured native frames have real pixel content (distinct colours + hashes); not a Windows oracle',
        'frames': rows,
    }, indent=2))
    cards = []
    for r in rows:
        if 'error' in r:
            continue
        cards.append(
            '<figure><img src="file://' + html.escape(r['path']) + '" width="320">'
            '<figcaption>' + html.escape(r['game']) + ' ' +
            html.escape(Path(r['path']).name) + ' — ' +
            str(r['width']) + 'x' + str(r['height']) + ', ' +
            str(r['distinct_colors']) + ' colours, sha256 ' + r['sha256'][:16] + '…'
            '</figcaption></figure>')
    gallery = args.out / 'native-frames.html'
    gallery.write_text(
        '<!doctype html><meta charset="utf-8"><title>Native macOS frames</title>'
        '<h1>Native macOS game frames — proof gallery</h1>'
        '<p>These are frames captured from GameReComp native binaries. '
        'No original-Windows screenshot column is present: no Windows execution '
        'oracle is available in this environment, and Wine is not used as the '
        'final runtime. Per-frame hashes and colour counts are in '
        '<code>frame-proof.json</code>.</p>' + ''.join(cards))
    print(f'wrote {summary} and {gallery}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
