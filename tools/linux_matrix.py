#!/usr/bin/env python3
"""Build and run generated GameReComp projects inside native Linux.

The macOS host cannot execute ELF binaries and GameReComp's autorecomp refuses
cross-OS targets, so Linux validation builds the *generated project* in a Linux
container (Colima/Docker) and runs the resulting ELF there. This exercises the
Linux host code paths of win32hle directly; no Wine and no emulator is used.

The container must have a compiler, cmake, pkg-config and the SDL2/SDL2_ttf
development packages. Rather than downloading them on every build (which proved
unreliable on this host for apk), the runner uses a prebuilt image by default:

    sh tools/linux/build-image.sh     # docker build -t gamerecomp/linux-runner:local ...

and refuses to start if that image lacks the toolchain. ``--provision apk|apt``
is still available for images without one; it installs packages over the
network and records that fact in the summary. Prebuilt builds use
``--network none`` so evidence cannot depend on a package mirror.

Usage:
  python3 -m tools.linux_matrix --project <generated-project-dir> \
      [--project <dir> ...] --out <results-dir> \
      [--image gamerecomp/linux-runner:local] \
      [--provision auto|prebuilt|apk|apt] [--original <original-windows-pe>] \
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
  report.json. Every row records the exact docker command, the toolchain seen
  in the image, and (after a successful build) the ELF header, size and
  SHA-256 of the produced binary. Exit codes: 0 completed, 2 bad arguments,
  3 docker/image/provision unavailable or preflight guard blocked all builds,
  4 some builds were refused mid-run.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
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


DEFAULT_IMAGE = 'gamerecomp/linux-runner:local'
APK_PACKAGES = 'build-base cmake pkgconf sdl2-dev sdl2_ttf-dev'
APT_PACKAGES = ('build-essential cmake pkg-config libsdl2-dev '
                'libsdl2-ttf-dev ca-certificates')


def project_name(project: Path) -> str:
    text = (project / 'CMakeLists.txt').read_text()
    m = re.search(r'project\(\s*([A-Za-z0-9_.-]+)', text)
    return m.group(1) if m else project.name


def docker_ready() -> bool:
    return subprocess.run(['docker', 'info'], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode == 0


def image_available(image: str) -> bool:
    """Fail closed when the requested image is not present locally.

    ``docker run`` would silently try a registry pull, which is exactly the
    network path that stalled earlier. Requiring a local image keeps the run
    bounded and makes the evidence reproducible.
    """
    return subprocess.run(['docker', 'image', 'inspect', image],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False).returncode == 0


def verify_bind_mount(path: Path, image: str, timeout: int = 60) -> tuple[bool, str]:
    """Check that Docker actually sees the host staging directory.

    Colima/virtiofs silently mounts an EMPTY directory for host paths outside
    its shared roots (e.g. /private/var/folders on this host), which otherwise
    appears as a confusing "source directory does not contain CMakeLists.txt".
    A marker file makes that failure explicit before any build starts.
    """
    marker = path / f'.linux-mount-probe-{os.getpid()}'
    try:
        marker.write_text('ok')
        try:
            p = subprocess.run(
                ['docker', 'run', '--rm', '--network', 'none', '-v',
                 f'{path}:/work', '-w', '/work', image, 'sh', '-c',
                 f'cat {marker.name}'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                timeout=timeout, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, f'mount probe failed: {type(exc).__name__}: {exc}'
        if p.returncode != 0 or p.stdout.strip() != 'ok':
            return False, (f'Docker sees no files at {path} (rc={p.returncode}, '
                           f'output={p.stdout.strip()[:200]!r}); put --out/--scratch '
                           'under a host path shared with the Docker VM (e.g. $HOME)')
        return True, ''
    except OSError as exc:
        return False, f'cannot write mount probe marker: {exc}'
    finally:
        try:
            marker.unlink()
        except OSError:
            pass


def probe_image(image: str, timeout: int = 90) -> dict:
    """Inspect the image once: toolchain versions and package manager.

    One bounded ``docker run``; no packages are installed here. The result is
    written verbatim into the summary so a later reader can tell exactly what
    compiled the project.
    """
    script = (
        'printf "CMAKE=%s\\n" "$(cmake --version 2>/dev/null | head -1)"; '
        'printf "CC=%s\\n" "$(cc --version 2>/dev/null | head -1)"; '
        'printf "PKG=%s\\n" "$(pkg-config --version 2>/dev/null)"; '
        'printf "SDL=%s\\n" "$(pkg-config --modversion sdl2 SDL2_ttf 2>/dev/null | tr \'\\n\' \' \')"; '
        'printf "APK=%s\\n" "$(command -v apk)"; '
        'printf "APT=%s\\n" "$(command -v apt-get)"; '
        'printf "UNAME=%s\\n" "$(uname -sr)"'
    )
    rec = {'image': image, 'status': 'BLOCKED'}
    try:
        p = subprocess.run(['docker', 'run', '--rm', '--network', 'none', image,
                            'sh', '-c', script], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True,
                           timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        rec['reason'] = f'{type(exc).__name__}: {exc}'
        return rec
    rec['rc'] = p.returncode
    rec['raw'] = p.stdout[-4000:]
    fields = {}
    for line in p.stdout.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            fields[k] = v.strip()
    rec.update(fields)
    rec['toolchain_ok'] = all(fields.get(k) for k in ('CMAKE', 'CC', 'PKG', 'SDL'))
    rec['status'] = 'OK' if p.returncode == 0 and fields else 'BLOCKED'
    if rec['status'] == 'BLOCKED' and 'reason' not in rec:
        rec['reason'] = f'probe rc={p.returncode}: {rec["raw"][-300:]}'
    return rec


def provision_plan(probe: dict | None, provision: str) -> tuple[str | None, str | None]:
    """Return (mode, error). Network-free 'prebuilt' is the default choice."""
    probe = probe or {}
    ok = bool(probe.get('toolchain_ok'))
    if provision == 'auto':
        if ok:
            provision = 'prebuilt'
        elif probe.get('APK'):
            provision = 'apk'
        elif probe.get('APT'):
            provision = 'apt'
        else:
            return 'auto', ('image has no cmake/cc/pkg-config/SDL2 toolchain and '
                            'no apk/apt package manager to install one')
    if provision == 'prebuilt':
        if not ok:
            return provision, ('image lacks a complete toolchain '
                               '(cmake, cc, pkg-config, sdl2 and SDL2_ttf); '
                               'rebuild it with tools/linux/build-image.sh')
        return provision, None
    if provision == 'apk':
        return provision, None
    if provision == 'apt':
        return provision, None
    return provision, f'unknown --provision {provision!r}'


def setup_prefix(provision: str) -> str:
    """Shell prefix run before cmake. Empty for a prebuilt toolchain image."""
    if provision == 'apk':
        return f'apk add --no-cache {APK_PACKAGES} > /dev/null && '
    if provision == 'apt':
        return ('apt-get update -qq && '
                f'DEBIAN_FRONTEND=noninteractive apt-get install -y -qq '
                f'--no-install-recommends {APT_PACKAGES} > /dev/null && ')
    return ''


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


def rewrite_incbin_paths(staging: Path) -> list[str]:
    """Point generated ``.incbin`` directives at the staged copy, not the origin.

    pipeline.py embeds the original PE as ``.incbin "/abs/origin/.../x.bin"``.
    That path does not exist inside the container (and would leak the build
    machine's layout), so rewrite it to the bind-mounted ``/work`` copy. Returns
    the list of rewritten directives; a missing staged binary is a blocker, not
    a silent no-op.
    """
    rewritten = []
    for asm in sorted(staging.rglob('*.S')):
        text = asm.read_text()
        relative_dir = asm.relative_to(staging).parent.as_posix()

        def replace(match):
            source = match.group(1)
            name = Path(source).name
            target = staging / relative_dir / name
            if not target.is_file():
                raise FileNotFoundError(f'{asm}: .incbin source not staged: {source}')
            new = f'/work/{relative_dir}/{name}'.replace('//', '/')
            rewritten.append(f'{asm.name}: {source} -> {new}')
            return f'.incbin "{new}"'

        updated = re.sub(r'\.incbin\s+"([^"]+)"', replace, text)
        if updated != text:
            asm.write_text(updated)
    return rewritten


def elf_evidence(path: Path) -> dict:
    """Read the produced binary itself; never infer ELF from a build exit code."""
    try:
        head = path.read_bytes()[:20]
    except OSError as exc:
        return {'status': 'MISSING', 'reason': f'{type(exc).__name__}: {exc}',
                'path': str(path)}
    if head[:4] != b'\x7fELF':
        return {'status': 'NOT_ELF', 'magic': head[:4].hex(), 'path': str(path)}
    cls = {1: 'ELF32', 2: 'ELF64'}.get(head[4], f'unknown({head[4]})')
    data = {1: 'LSB', 2: 'MSB'}.get(head[5], f'unknown({head[5]})')
    machine = int.from_bytes(head[18:20], 'little' if head[5] == 1 else 'big')
    machine_name = {3: 'i386', 62: 'x86-64', 183: 'aarch64'}.get(machine, 'unknown')
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return {'status': 'OK', 'path': str(path), 'class': cls, 'data': data,
            'machine': hex(machine), 'machine_name': machine_name,
            'size': path.stat().st_size, 'sha256': digest.hexdigest()}


def build_one(project: Path, out: Path, image: str, jobs: int,
              run_timeout: int, build_timeout: int,
              min_free_bytes: int = MIN_FREE_DISK_BYTES,
              scratch: Path | None = None, provision: str = 'auto',
              probe: dict | None = None,
              original: Path | None = None) -> dict:
    name = project_name(project)
    record = {
        'project': str(project), 'name': name, 'image': image,
        'original': str(original) if original else None,
        'resources': resources_from_project(project),
        'toolchain': {k: probe.get(k, '') for k in ('CMAKE', 'CC', 'PKG', 'SDL', 'UNAME')}
                     if probe else {},
    }
    mode, error = provision_plan(probe, provision)
    record['provision'] = mode
    record['network'] = 'none' if mode == 'prebuilt' else 'default'
    if error:
        diagnostic = f'image provisioning blocked: {error} project={project}'
        print(diagnostic, file=sys.stderr, flush=True)
        record.update({'skipped': 'image_provision', 'build_started': False,
                       'diagnostic': diagnostic, 'build_rc': 'NOT_RUN',
                       'run_rc': 'NOT_RUN', 'seconds': 0.0})
        return record

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
    try:
        record['incbin_rewritten'] = rewrite_incbin_paths(staging)
    except OSError as exc:
        diagnostic = f'staging blocked: {exc}'
        print(diagnostic, file=sys.stderr, flush=True)
        record.update({'skipped': 'staging', 'build_started': False,
                       'diagnostic': diagnostic, 'build_rc': 'NOT_RUN',
                       'run_rc': 'NOT_RUN', 'seconds': 0.0})
        return record

    docker_args = ['docker', 'run', '--rm', '-v', f'{staging}:/work', '-w', '/work']
    if mode == 'prebuilt':
        docker_args += ['--network', 'none']
    setup = (setup_prefix(mode) +
             f'cmake -S /work -B /work/build -DCMAKE_BUILD_TYPE=Release > /dev/null && '
             f'cmake --build /work/build -j {jobs}')
    cmd = docker_args + [image, 'sh', '-c', setup]
    record['build_cmd'] = cmd
    started = time.time()
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=build_timeout, check=False)
        record['build_rc'] = p.returncode
        record['build_log'] = p.stdout[-4000:]
    except subprocess.TimeoutExpired as exc:
        record['build_rc'] = 'TIMEOUT'
        record['build_log'] = str(exc.stdout or '')[-4000:]
    except OSError as exc:
        record['build_rc'] = 'DOCKER_ERROR'
        record['diagnostic'] = f'{type(exc).__name__}: {exc}'
        record['build_log'] = str(exc)

    binary = staging / 'build' / name
    if record['build_rc'] == 0:
        record['elf'] = elf_evidence(binary)
        if record['elf']['status'] != 'OK':
            record['run_rc'] = 'NO_ELF'
            record['run_log'] = json.dumps(record['elf'])
        else:
            run_docker = list(docker_args)
            game_arg = ''
            if original is not None:
                run_docker += ['-v', f'{original}:/original:ro']
                game_arg = ' /original'
            run = (f'cd /work && SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy '
                   f'timeout {run_timeout} ./build/{name}{game_arg}')
            run_cmd = run_docker + [image, 'sh', '-c', run]
            record['run_cmd'] = run_cmd
            try:
                p = subprocess.run(run_cmd, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   timeout=run_timeout + 120, check=False)
                record['run_rc'] = p.returncode
                record['run_log'] = p.stdout[-4000:]
            except subprocess.TimeoutExpired as exc:
                record['run_rc'] = 'TIMEOUT'
                record['run_log'] = str(exc.stdout or '')[-4000:]
            except OSError as exc:
                record['run_rc'] = 'DOCKER_ERROR'
                record['run_log'] = f'{type(exc).__name__}: {exc}'
    else:
        record['run_rc'] = 'NOT_RUN'
        record['elf'] = {'status': 'NOT_RUN'}
    record['seconds'] = round(time.time() - started, 1)
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', action='append', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--original', type=Path,
                    help='original Windows PE, bind-mounted read-only as '
                         '/original and passed as argv[1] to the rebuilt ELF')
    ap.add_argument('--image', default=DEFAULT_IMAGE)
    ap.add_argument('--provision', choices=('auto', 'prebuilt', 'apk', 'apt'),
                    default='auto',
                    help='how to obtain the toolchain (default auto: use the '
                         'image toolchain if present, else apk/apt)')
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
    args.original = args.original.resolve() if args.original else None
    if args.original and not args.original.is_file():
        print(f'--original is not a file: {args.original}', file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    probe = {'image': args.image, 'status': 'BLOCKED',
             'reason': 'image not present locally; build it with tools/linux/build-image.sh'}
    if image_available(args.image):
        probe = probe_image(args.image)
    summary_path = args.out / 'linux-summary.json'
    if probe['status'] != 'OK':
        print(f'image probe blocked: {probe.get("reason", probe)}', file=sys.stderr)
        summary_path.write_text(json.dumps({
            'schema': 1, 'results': [], 'image_probe': probe,
            'requested_inputs': len(args.project),
        }, indent=2))
        return 3
    mode, error = provision_plan(probe, args.provision)
    if error:
        print(f'provisioning blocked: {error}', file=sys.stderr)
        summary_path.write_text(json.dumps({
            'schema': 1, 'results': [], 'image_probe': probe,
            'provision': mode, 'provision_error': error,
            'requested_inputs': len(args.project),
        }, indent=2))
        return 3

    scratch = (args.scratch or args.out).resolve()
    guard = check_free_disk(scratch, min_free_bytes)
    if not guard['ok']:
        # Fail before scheduling any work: no Linux build has started here.
        print(guard['diagnostic'], file=sys.stderr, flush=True)
        summary_path.write_text(json.dumps({
            'schema': 1,
            'results': [],
            'image_probe': probe,
            'provision': mode,
            'requested_inputs': len(args.project),
            'disk_guard': guard,
        }, indent=2))
        print(f'no builds started; summary: {summary_path}', file=sys.stderr)
        return 3

    mount_ok, mount_error = verify_bind_mount(args.out, args.image)
    if not mount_ok:
        print(f'bind mount blocked: {mount_error}', file=sys.stderr)
        summary_path.write_text(json.dumps({
            'schema': 1,
            'results': [],
            'image_probe': probe,
            'provision': mode,
            'bind_mount': {'ok': False, 'path': str(args.out), 'reason': mount_error},
            'requested_inputs': len(args.project),
        }, indent=2))
        return 3

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(build_one, p, args.out, args.image, args.jobs,
                               args.run_timeout, args.build_timeout,
                               min_free_bytes, scratch, mode, probe,
                               args.original): p
                   for p in args.project}
        for f in concurrent.futures.as_completed(futures):
            rec = f.result()
            results.append(rec)
            print(f"{rec['name']:24} build={rec['build_rc']!s:>7} "
                  f"run={rec['run_rc']!s:>7} "
                  f"elf={rec.get('elf', {}).get('status', 'NOT_RUN')} "
                  f"{rec['seconds']}s", flush=True)
    results.sort(key=lambda r: r['name'])
    blocked = [r['project'] for r in results
               if r.get('skipped') in ('disk_guard', 'image_provision')]
    summary_path.write_text(json.dumps({
        'schema': 1,
        'results': results,
        'image_probe': probe,
        'provision': mode,
        'disk_guard': {
            'scratch': str(scratch),
            'min_free_bytes': min_free_bytes,
            'blocked_inputs': blocked,
        },
    }, indent=2))
    print(f'summary: {summary_path}')
    return 4 if blocked else 0


if __name__ == '__main__':
    sys.exit(main())
