#!/usr/bin/env python3
"""Build and run generated GameReComp projects inside native Linux.

The macOS host cannot execute ELF binaries and GameReComp's autorecomp refuses
cross-OS targets, so Linux validation builds the *generated project* in a Linux
container (Colima/Docker) and runs the resulting ELF there. This exercises the
Linux host code paths of win32hle directly; no Wine and no emulator is used.

Usage:
  python3 -m tools.linux_matrix --project <generated-project-dir> \
      [--project <dir> ...] --out <results-dir> [--image alpine:3.20] \
      [--scratch <existing-path>] [--min-free-gb 4]

Disk guard (issue #26)
  Building a generated project needs several GB of scratch space for the C
  tree and its CMake build. Before *each* build this driver calls the shared
  ``shutil.disk_usage`` guard from tools.game_matrix on ``--scratch`` (default
  ``--out``) and refuses to start below ``--min-free-gb`` (default 4 GiB).
  Refusals are explicit: stderr, the row as ``skipped="disk_guard"`` and
  summary.json all record the measured free bytes and threshold; no partial
  build is reported as a Linux result. Rows also carry the measured
  ``resources`` section when the generated project still has pipeline.py's
  report.json. Exit codes: 0 completed, 2 bad arguments, 3 docker unavailable
  or preflight guard blocked all builds, 4 some builds were refused mid-run.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:  # module invocation (python3 -m tools.linux_matrix)
    from tools.game_matrix import GIB, MIN_FREE_DISK_BYTES, check_free_disk
except ImportError:  # direct script invocation (python3 tools/linux_matrix.py)
    from game_matrix import GIB, MIN_FREE_DISK_BYTES, check_free_disk


def project_name(project: Path) -> str:
    text = (project / 'CMakeLists.txt').read_text()
    m = re.search(r'project\(\s*([A-Za-z0-9_.-]+)', text)
    return m.group(1) if m else project.name


def docker_ready() -> bool:
    return subprocess.run(['docker', 'info'], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode == 0


def resources_from_project(project: Path) -> dict | None:
    """pipeline.py's measured resources for this project, when still available.

    game_matrix keeps the generated project only with --no-clean, so a missing
    report.json yields None (never an estimate).
    """
    report = project / 'reports' / 'report.json'
    try:
        data = json.loads(report.read_text())
    except (OSError, ValueError):
        return None
    resources = data.get('resources')
    return resources if isinstance(resources, dict) else None


def build_one(project: Path, out: Path, image: str, jobs: int,
              run_timeout: int, build_timeout: int,
              min_free_bytes: int = MIN_FREE_DISK_BYTES,
              scratch: Path | None = None) -> dict:
    name = project_name(project)
    record = {
        'project': str(project), 'name': name, 'image': image,
        'resources': resources_from_project(project),
    }

    # Refuse before staging/copying any source or invoking docker: a blocked
    # build must never appear as a partial Linux matrix row.
    guard = check_free_disk(scratch or out, min_free_bytes)
    if not guard['ok']:
        diagnostic = f'{guard["diagnostic"]} project={project}'
        print(diagnostic, file=sys.stderr, flush=True)
        record.update({
            'skipped': 'disk_guard',
            'build_started': False,
            'disk_guard': guard,
            'diagnostic': diagnostic,
            'build_rc': 'NOT_RUN',
            'run_rc': 'NOT_RUN',
            'seconds': 0.0,
        })
        return record
    record['build_started'] = True

    staging = out / 'staging' / name
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for item in ('CMakeLists.txt', 'src'):
        src = project / item
        if src.exists():
            shutil.copytree(src, staging / item) if src.is_dir() else shutil.copy2(src, staging / item)

    setup = ('apk add --no-cache build-base cmake pkgconf sdl2-dev sdl2_ttf-dev '
             '> /dev/null && '
             f'cmake -S /work -B /work/build -DCMAKE_BUILD_TYPE=Release > /dev/null && '
             f'cmake --build /work/build -j {jobs}')
    cmd = ['docker', 'run', '--rm', '-v', f'{staging}:/work', '-w', '/work',
           image, 'sh', '-c', setup]
    started = time.time()
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=build_timeout, check=False)
        record['build_rc'] = p.returncode
        record['build_log'] = p.stdout[-4000:]
    except subprocess.TimeoutExpired as exc:
        record['build_rc'] = 'TIMEOUT'
        record['build_log'] = str(exc.stdout or '')[-4000:]

    if record['build_rc'] == 0:
        run = (f'cd /work && SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy '
               f'timeout {run_timeout} ./build/{name}')
        p = subprocess.run(['docker', 'run', '--rm', '-v', f'{staging}:/work',
                            '-w', '/work', image, 'sh', '-c', run],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=run_timeout + 120, check=False)
        record['run_rc'] = p.returncode
        record['run_log'] = p.stdout[-4000:]
    else:
        record['run_rc'] = 'NOT_RUN'
    record['seconds'] = round(time.time() - started, 1)
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', action='append', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--image', default='alpine:3.20')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--run-timeout', type=int, default=8)
    ap.add_argument('--build-timeout', type=int, default=1800)
    ap.add_argument('--parallel', type=int, default=2)
    ap.add_argument('--scratch', type=Path,
                    help='existing path on the volume holding build staging trees '
                         '(default: --out); the disk guard measures free space here')
    ap.add_argument('--min-free-gb', type=float, default=MIN_FREE_DISK_BYTES / GIB,
                    help=f'minimum free disk space required before a build '
                         f'(default: {MIN_FREE_DISK_BYTES / GIB:g} GiB)')
    args = ap.parse_args(argv)

    if not docker_ready():
        print('docker daemon unavailable: Linux validation cannot run here', file=sys.stderr)
        return 3
    min_free_bytes = int(args.min_free_gb * GIB)
    if min_free_bytes <= 0:
        print('--min-free-gb must be positive', file=sys.stderr)
        return 2
    # Docker bind mounts require absolute host paths: a relative --out made
    # every build fail with "includes invalid characters for a local volume
    # name" before cmake was even invoked.
    args.out = args.out.resolve()
    args.project = [p.resolve() for p in args.project]
    args.out.mkdir(parents=True, exist_ok=True)

    scratch = (args.scratch or args.out).resolve()
    guard = check_free_disk(scratch, min_free_bytes)
    if not guard['ok']:
        # Fail before scheduling any work: no Linux build has started here.
        print(guard['diagnostic'], file=sys.stderr, flush=True)
        summary = args.out / 'linux-summary.json'
        summary.write_text(json.dumps({
            'schema': 1,
            'results': [],
            'requested_inputs': len(args.project),
            'disk_guard': guard,
        }, indent=2))
        print(f'no builds started; summary: {summary}', file=sys.stderr)
        return 3

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(build_one, p, args.out, args.image, args.jobs,
                               args.run_timeout, args.build_timeout,
                               min_free_bytes, scratch): p for p in args.project}
        for f in concurrent.futures.as_completed(futures):
            rec = f.result()
            results.append(rec)
            print(f"{rec['name']:24} build={rec['build_rc']!s:>7} "
                  f"run={rec['run_rc']!s:>7} {rec['seconds']}s", flush=True)
    results.sort(key=lambda r: r['name'])
    blocked = [r['project'] for r in results if r.get('skipped') == 'disk_guard']
    summary = args.out / 'linux-summary.json'
    summary.write_text(json.dumps({
        'schema': 1,
        'results': results,
        'disk_guard': {
            'scratch': str(scratch),
            'min_free_bytes': min_free_bytes,
            'blocked_inputs': blocked,
        },
    }, indent=2))
    print(f'summary: {summary}')
    return 4 if blocked else 0


if __name__ == '__main__':
    sys.exit(main())
