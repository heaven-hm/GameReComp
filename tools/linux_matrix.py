#!/usr/bin/env python3
"""Build and run generated GameReComp projects inside native Linux.

The macOS host cannot execute ELF binaries and GameReComp's autorecomp refuses
cross-OS targets, so Linux validation builds the *generated project* in a Linux
container (Colima/Docker) and runs the resulting ELF there. This exercises the
Linux host code paths of win32hle directly; no Wine and no emulator is used.

Usage:
  python3 -m tools.linux_matrix --project <generated-project-dir> \
      [--project <dir> ...] --out <results-dir> [--image alpine:3.20]
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


def project_name(project: Path) -> str:
    text = (project / 'CMakeLists.txt').read_text()
    m = re.search(r'project\(\s*([A-Za-z0-9_.-]+)', text)
    return m.group(1) if m else project.name


def docker_ready() -> bool:
    return subprocess.run(['docker', 'info'], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode == 0


def build_one(project: Path, out: Path, image: str, jobs: int,
              run_timeout: int, build_timeout: int) -> dict:
    name = project_name(project)
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
    record = {'project': str(project), 'name': name, 'image': image}
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
    args = ap.parse_args(argv)

    if not docker_ready():
        print('docker daemon unavailable: Linux validation cannot run here', file=sys.stderr)
        return 3
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(build_one, p, args.out, args.image, args.jobs,
                               args.run_timeout, args.build_timeout): p for p in args.project}
        for f in concurrent.futures.as_completed(futures):
            rec = f.result()
            results.append(rec)
            print(f"{rec['name']:24} build={rec['build_rc']!s:>7} "
                  f"run={rec['run_rc']!s:>7} {rec['seconds']}s", flush=True)
    results.sort(key=lambda r: r['name'])
    summary = args.out / 'linux-summary.json'
    summary.write_text(json.dumps({'schema': 1, 'results': results}, indent=2))
    print(f'summary: {summary}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
