#!/usr/bin/env python3
"""Run the GameReComp pipeline over a corpus of PE32 x86 games.

Generic driver: no game name, path or hotkey is hard-coded. Inputs are supplied
as explicit .exe paths (or discovered under a directory); every result is
recorded by input SHA-256 so a matrix row always names the exact file tested.

Usage:
  python3 -m tools.game_matrix --exe <path> [--exe <path> ...] \
      --out <dir> [--jobs 4] [--timeout 1200] [--run-timeout 8] \
      [--scratch <existing-path>] [--min-free-gb 4]

Disk guard (issue #26)
  Every row records the measured ``resources`` section that pipeline.py writes
  into report.json, so disk/CPU/memory costs are evidence, not guesses. Before
  *each* build the driver queries ``shutil.disk_usage`` on the scratch volume
  (``--scratch``, default ``--out``) and refuses to start if free space is
  below ``--min-free-gb`` (default 4 GiB, see MIN_FREE_DISK_BYTES). A generated
  C tree can be several GB, so running on an almost-full volume otherwise
  yields truncated projects and misleading matrix rows. Refusals are explicit:
  a diagnostic is printed to stderr, written to the game's log, stored in the
  row as ``skipped="disk_guard"`` and in summary.json, and no build is started.
  Exit codes: 0 completed, 2 bad/no inputs, 3 preflight guard blocked all
  builds, 4 some builds were refused mid-run by the guard.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
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


GIB = 1 << 30
# One generated C tree can be several GB (see the cleanup note in run_one), and
# the compile stage needs room for it plus its build products. 4 GiB is a
# deliberately conservative default for a single concurrent build; raise it
# for tighter CI disks or lower it (--min-free-gb) only for known-small inputs.
MIN_FREE_DISK_BYTES = 4 * GIB


def format_bytes(n: int) -> str:
    """Human-readable byte count used only in disk-guard diagnostics."""
    if abs(n) >= GIB:
        return f'{n / GIB:.2f} GiB'
    if abs(n) >= (1 << 20):
        return f'{n / (1 << 20):.1f} MiB'
    return f'{n} B'


def check_free_disk(scratch: Path, min_free_bytes: int = MIN_FREE_DISK_BYTES) -> dict:
    """Measure free space on the volume holding *scratch* and enforce the guard.

    This is a real ``shutil.disk_usage`` query against the scratch path, never a
    number inferred from report.json. The returned dict always contains an
    explicit ``diagnostic`` string; any measurement error fails closed so a
    build is never started without a known-good disk measurement.
    """
    required = int(min_free_bytes)
    base = {
        'scratch': str(scratch),
        'total_bytes': None,
        'free_bytes': None,
        'min_free_bytes': required,
        'ok': False,
    }
    try:
        usage = shutil.disk_usage(str(scratch))
    except OSError as exc:
        result = dict(base, error=f'{type(exc).__name__}: {exc}')
        result['diagnostic'] = (
            f'DISK GUARD: cannot measure free space on {scratch} '
            f'({result["error"]}); refusing to start a build.'
        )
        return result
    free = int(usage.free)
    ok = free >= required
    result = dict(base, total_bytes=int(usage.total), free_bytes=free, ok=ok)
    result['diagnostic'] = (
        f'DISK GUARD: {"only " if not ok else ""}{format_bytes(free)} free on '
        f'{scratch} (minimum {format_bytes(required)}); '
        + ('refusing to start a build.' if not ok else 'free-space check passed.')
    )
    return result


def run_one(exe: Path, out: Path, jobs: int, timeout: int, run_timeout: int,
            reuse_build: bool, headless: bool, clean: bool,
            min_free_bytes: int = MIN_FREE_DISK_BYTES,
            scratch: Path | None = None) -> dict:
    name = exe.stem.replace(' ', '_')
    log = out / f'{name}.log'
    project = out / 'projects' / name
    frames = out / 'frames' / name

    # Refuse to start the build when the scratch volume is too full. This runs
    # before any project/frames directory is created or subprocess is spawned,
    # so a refusal can never look like a partially executed game row.
    guard = check_free_disk(scratch or out, min_free_bytes)
    if not guard['ok']:
        diagnostic = f'{guard["diagnostic"]} input={exe}'
        print(diagnostic, file=sys.stderr, flush=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(diagnostic + '\n')
        return {
            'input': str(exe),
            'sha256': sha256(exe),
            'size': exe.stat().st_size,
            'log': str(log),
            'skipped': 'disk_guard',
            'build_started': False,
            'disk_guard': guard,
            'diagnostic': diagnostic,
            'seconds': 0.0,
            'frames': [],
        }

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
        'build_started': True,
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
            # Issue #26: copy the measured resource footprint verbatim. None
            # means the report predates measurement or omitted the section;
            # we never substitute an estimate for a missing measurement.
            resources = data.get('resources')
            record['resources'] = resources if isinstance(resources, dict) else None
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
            # Games read their own data relative to the working directory.
            # The runner supplies --assets-dir <exe's folder>; verify the
            # pipeline actually launched the binary there, do not assume it.
            run_cwd = run.get('cwd')
            record['run_cwd'] = run_cwd
            record['cwd_ok'] = (run_cwd is not None and
                                Path(run_cwd).resolve() == exe.parent.resolve())
            record['stdout_tail'] = (run.get('stdout') or '')[-1500:]
            record['stderr_tail'] = (run.get('stderr') or '')[-1500:]
            record['report'] = str(report)
        except (ValueError, KeyError, TypeError) as exc:
            record['report_error'] = str(exc)
    else:
        record['report_error'] = 'no report.json produced'

    # Generated C can be several GB per game. Keep the durable reports and
    # release the build tree so the whole corpus can be tested on a small disk.
    durable = out / 'reports' / name
    durable.mkdir(parents=True, exist_ok=True)
    if report.exists():
        shutil.copy2(report, durable / 'report.json')
    verification = project / 'reports' / 'verification.json'
    if verification.exists():
        shutil.copy2(verification, durable / 'verification.json')
    record['durable_report'] = str(durable)
    if clean and project.exists():
        shutil.rmtree(project, ignore_errors=True)
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
    p.add_argument('--no-clean', action='store_true',
                   help='keep generated C and build trees after recording the report')
    p.add_argument('--scratch', type=Path,
                   help='existing path on the volume holding generated build trees '
                        '(default: --out); the disk guard measures free space here')
    p.add_argument('--min-free-gb', type=float, default=MIN_FREE_DISK_BYTES / GIB,
                   help=f'minimum free disk space required before a build '
                        f'(default: {MIN_FREE_DISK_BYTES / GIB:g} GiB)')
    args = p.parse_args(argv)

    exes = list(args.exe or [])
    if args.corpus:
        exes = [e for e in discover(args.corpus)
                if not any(x in str(e) for x in args.exclude)]
    if not exes:
        print('no PE32 x86 inputs found', file=sys.stderr)
        return 2
    min_free_bytes = int(args.min_free_gb * GIB)
    if min_free_bytes <= 0:
        print('--min-free-gb must be positive', file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    scratch = args.scratch or args.out
    guard = check_free_disk(scratch, min_free_bytes)
    if not guard['ok']:
        # Fail before scheduling any work: no build has started at this point.
        print(guard['diagnostic'], file=sys.stderr, flush=True)
        summary = args.out / 'summary.json'
        summary.write_text(json.dumps({
            'schema': 1,
            'inputs': 0,
            'requested_inputs': len(exes),
            'host': {'platform': sys.platform},
            'disk_guard': guard,
            'results': [],
        }, indent=2))
        print(f'no builds started; summary: {summary}', file=sys.stderr)
        return 3

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(run_one, e, args.out, args.jobs, args.timeout,
                               args.run_timeout, args.reuse_build, args.headless,
                               not args.no_clean, min_free_bytes, scratch): e for e in exes}
        for future in concurrent.futures.as_completed(futures):
            rec = future.result()
            results.append(rec)
            stages = ','.join(f"{s['name']}={s['status']}" for s in rec.get('stages', []))
            res = rec.get('resources') or {}
            res_txt = (f"gen={res.get('generated_source_bytes', '?')} "
                       f"disk={res.get('project_disk_bytes', '?')} "
                       f"rss={res.get('peak_rss_bytes', '?')}")
            print(f"{rec['input'].split('/')[-1]:28} rc={rec.get('returncode')!s:>7} "
                  f"score={rec.get('score')!s:>5} outcome={rec.get('outcome_status')!s:>8} "
                  f"exit={rec.get('run_exit_code')!s:>5} frames={len(rec.get('frames', []))} "
                  f"{rec['seconds']}s  {stages}  {res_txt}", flush=True)

    results.sort(key=lambda r: r['input'])
    blocked = [r['input'] for r in results if r.get('skipped') == 'disk_guard']
    summary = args.out / 'summary.json'
    summary.write_text(json.dumps({
        'schema': 1,
        'inputs': len(results),
        'requested_inputs': len(exes),
        'host': {'platform': sys.platform},
        'disk_guard': {
            'scratch': str(scratch),
            'min_free_bytes': min_free_bytes,
            'blocked_inputs': blocked,
        },
        'results': results,
    }, indent=2))
    print(f'\nsummary: {summary}')
    return 4 if blocked else 0


if __name__ == '__main__':
    sys.exit(main())
