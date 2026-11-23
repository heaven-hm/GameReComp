#!/usr/bin/env python3
"""Run the GameReComp pipeline over a corpus of PE32 x86 games.

Generic driver: no game name, path or hotkey is hard-coded. Inputs are supplied
as explicit .exe paths (or discovered under a directory); every result is
recorded by input SHA-256 so a matrix row always names the exact file tested.

Usage:
  python3 -m tools.game_matrix --exe <path> [--exe <path> ...] \
      --out <dir> [--jobs 4] [--timeout 1200] [--run-timeout 8]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path


def pe32_x86(path: Path) -> bool:
    try:
        h = path.open('rb').read(4096)
        if h[:2] != b'MZ':
            return False
        off = struct.unpack_from('<I', h, 0x3c)[0]
        if off + 24 > len(h) or h[off:off + 4] != b'PE\0\0':
            return False
        return struct.unpack_from('<H', h, off + 4)[0] == 0x14c
    except (OSError, struct.error):
        return False


def discover(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob('*.exe') if pe32_x86(p))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def run_one(exe: Path, out: Path, jobs: int, timeout: int, run_timeout: int,
            reuse_build: bool, headless: bool) -> dict:
    name = exe.stem.replace(' ', '_')
    log = out / f'{name}.log'
    project = out / 'projects' / name
    frames = out / 'frames' / name
    frames.mkdir(parents=True, exist_ok=True)
    if not headless:
        # A real video driver is required: SDL's "dummy" driver has no OpenGL,
        # so every SDL_OPENGL game exits 1 before drawing a frame. On macOS
        # --interactive selects Cocoa; screenshots provide graphics evidence.
        cmd_env = dict(os.environ)
        cmd_env['RECOMP_SCREENSHOT'] = str(frames)
        cmd_env['RECOMP_SCREENSHOT_MAX'] = '3'
    else:
        cmd_env = None
    cmd = [
        sys.executable, '-m', 'tools.autorecomp', str(exe),
        '--project-dir', str(project),
        '--assets-dir', str(exe.parent),
        '--timeout', str(run_timeout), '--no-table', '--no-progress',
        '--input-os', 'windows', '--input-arch', 'x86',
        '--target-os', 'host', '--target-arch', 'host',
        '--jobs', str(jobs),
    ]
    if not headless:
        cmd.append('--interactive')
    if reuse_build:
        cmd.append('--reuse-build')
    started = time.time()
    record = {
        'input': str(exe),
        'sha256': sha256(exe),
        'size': exe.stat().st_size,
        'command': cmd,
        'log': str(log),
    }
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              env=cmd_env, timeout=timeout, text=True, check=False)
        log.write_text(proc.stdout)
        record['returncode'] = proc.returncode
    except subprocess.TimeoutExpired as exc:
        log.write_text((exc.stdout or b'') if isinstance(exc.stdout, bytes) else (exc.stdout or ''))
        record['returncode'] = 'TIMEOUT'
    record['seconds'] = round(time.time() - started, 1)
    record['frames'] = sorted(str(p) for p in frames.glob('*.png'))

    report = project / 'reports' / 'report.json'
    if report.exists():
        try:
            data = json.loads(report.read_text())
            record['score'] = data.get('score')
            record['stages'] = [
                {'name': s['name'], 'status': s['status'], 'message': s.get('message', '')}
                for s in data.get('stages', [])
            ]
            outcome = data.get('outcome') or data.get('verification') or {}
            record['outcome_status'] = outcome.get('status')
            record['outcome_reason'] = outcome.get('reason')
            run = outcome.get('run') or {}
            record['run_exit_code'] = run.get('exit_code')
            record['unresolved_imports'] = run.get('unresolved_imports')
            record['stdout_tail'] = (run.get('stdout') or '')[-1500:]
            record['stderr_tail'] = (run.get('stderr') or '')[-1500:]
            record['report'] = str(report)
        except (ValueError, KeyError, TypeError) as exc:
            record['report_error'] = str(exc)
    else:
        record['report_error'] = 'no report.json produced'
    return record


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument('--exe', action='append', type=Path, help='PE32 x86 executable (repeatable)')
    src.add_argument('--corpus', type=Path, help='directory to scan for PE32 x86 .exe files')
    p.add_argument('--exclude', action='append', default=[], help='substring to exclude from discovery')
    p.add_argument('--out', required=True, type=Path, help='results directory')
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--timeout', type=int, default=1200, help='per-game pipeline deadline (seconds)')
    p.add_argument('--run-timeout', type=int, default=8)
    p.add_argument('--reuse-build', action='store_true')
    p.add_argument('--headless', action='store_true',
                   help='use SDL dummy drivers (no OpenGL; games will exit 1 before drawing)')
    p.add_argument('--parallel', type=int, default=4)
    args = p.parse_args(argv)

    exes = list(args.exe or [])
    if args.corpus:
        exes = [e for e in discover(args.corpus)
                if not any(x in str(e) for x in args.exclude)]
    if not exes:
        print('no PE32 x86 inputs found', file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(run_one, e, args.out, args.jobs, args.timeout,
                               args.run_timeout, args.reuse_build, args.headless): e for e in exes}
        for future in concurrent.futures.as_completed(futures):
            rec = future.result()
            results.append(rec)
            stages = ','.join(f"{s['name']}={s['status']}" for s in rec.get('stages', []))
            print(f"{rec['input'].split('/')[-1]:28} rc={rec.get('returncode')!s:>7} "
                  f"score={rec.get('score')!s:>5} outcome={rec.get('outcome_status')!s:>8} "
                  f"exit={rec.get('run_exit_code')!s:>5} frames={len(rec.get('frames', []))} "
                  f"{rec['seconds']}s  {stages}", flush=True)

    results.sort(key=lambda r: r['input'])
    summary = args.out / 'summary.json'
    summary.write_text(json.dumps({
        'schema': 1,
        'inputs': len(results),
        'host': {'platform': sys.platform},
        'results': results,
    }, indent=2))
    print(f'\nsummary: {summary}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
