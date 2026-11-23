#!/usr/bin/env python3
"""Run an already-built native game one at a time, with proof.

No compilation here: each input is an existing native binary plus the game
folder it must run from. For each binary the tool records the exact command,
exit code, stdout/stderr, screenshot frames and frame colour statistics. A
game with no native binary is reported BLOCKED, never PASS.

Usage:
  python3 -m tools.game_runner --game <name> --binary <path> --folder <dir> \
      [--game ...] --out <dir> [--timeout 12]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path


def image_info(path: Path) -> dict:
    """Return format/dimensions/size for the runtime's PNG or BMP frames."""
    try:
        data = path.read_bytes()
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            w, h = struct.unpack_from('>II', data, 16)
            fmt = 'png'
        elif data[:2] == b'BM':
            w, h = struct.unpack_from('<ii', data, 18)
            fmt = 'bmp'
        else:
            return {'error': 'not a PNG or BMP frame', 'bytes': len(data)}
        return {'format': fmt, 'width': abs(w), 'height': abs(h), 'bytes': len(data)}
    except Exception as exc:
        return {'error': str(exc)}


def run_game(name: str, binary: Path, folder: Path, out: Path, timeout: int) -> dict:
    evidence = out / name
    shots = evidence / 'screenshots'
    if evidence.exists():
        shutil.rmtree(evidence)
    shots.mkdir(parents=True)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    env = dict(os.environ)
    env['SDL_VIDEODRIVER'] = 'cocoa' if sys.platform == 'darwin' else 'x11'
    env['SDL_AUDIODRIVER'] = 'dummy'
    env['RECOMP_SCREENSHOT'] = str(shots)
    env['RECOMP_SCREENSHOT_MAX'] = '3'
    env['RECOMP_SCREENSHOT_EVERY'] = '500'
    cmd = [str(binary)]
    started = time.time()
    rec = {
        'game': name,
        'binary': str(binary),
        'binary_sha256': digest,
        'folder': str(folder),
        'command': cmd,
        'env': {'SDL_VIDEODRIVER': env['SDL_VIDEODRIVER'],
                'SDL_AUDIODRIVER': env['SDL_AUDIODRIVER'],
                'RECOMP_SCREENSHOT': str(shots)},
    }
    try:
        p = subprocess.run(cmd, cwd=str(folder), env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=timeout,
                           check=False)
        rec['exit_code'] = p.returncode
        (evidence / 'run.log').write_text(p.stdout)
        rec['stdout_tail'] = p.stdout[-3000:]
    except subprocess.TimeoutExpired as exc:
        rec['exit_code'] = 'TIMEOUT'
        text = exc.stdout or ''
        if isinstance(text, bytes):
            text = text.decode('utf-8', 'replace')
        (evidence / 'run.log').write_text(text)
        rec['stdout_tail'] = text[-3000:]
    rec['seconds'] = round(time.time() - started, 1)
    frames = sorted(list(shots.glob('frame-*.png')) + list(shots.glob('frame-*.bmp')))
    rec['frames'] = [{'path': str(f), **image_info(f)} for f in frames]
    rec['frame_count'] = len(frames)
    rec['evidence'] = str(evidence)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--game', action='append', required=True)
    ap.add_argument('--binary', action='append', required=True, type=Path)
    ap.add_argument('--folder', action='append', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--timeout', type=int, default=12)
    args = ap.parse_args(argv)
    if not (len(args.game) == len(args.binary) == len(args.folder)):
        print('--game/--binary/--folder counts must match', file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for name, binary, folder in zip(args.game, args.binary, args.folder):
        if not binary.is_file():
            results.append({'game': name, 'status': 'BLOCKED',
                            'reason': f'no native binary at {binary}'})
            print(f'{name:20} BLOCKED (no native binary)', flush=True)
            continue
        rec = run_game(name, binary, folder, args.out, args.timeout)
        results.append(rec)
        print(f"{name:20} exit={rec['exit_code']!s:>7} frames={rec['frame_count']} "
              f"{rec['seconds']}s", flush=True)
    summary = args.out / 'summary.json'
    summary.write_text(json.dumps({'schema': 1, 'results': results}, indent=2))
    print(f'summary: {summary}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
