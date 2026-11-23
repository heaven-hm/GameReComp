#!/usr/bin/env python3
"""Opt-in, content-addressed object-file cache for generated C sources.

This module caches the object produced by ``cc -c <generated.c> -o <obj>``.
An entry is keyed by three independent inputs:

  1. the *bytes* of the C source (not its path or mtime);
  2. the compiler identity (resolved path, ``--version`` text and, when it is
     a small regular file, the compiler file's own bytes);
  3. the exact compile flags, in the order supplied.

A change to any of them changes the key, so a previously compiled object can
never be served for a different source, compiler or flag set. Publication is
atomic (fsynced temporary file + ``os.replace``) and every retrieval verifies
the object against a checksum manifest. There is no stale-object reuse path:
a corrupt, partial or mismatched entry is a miss, not a hit.

The methodology follows the same idea as Stela's artifact cache (pinned commit
140ffaf716e2fa8a2f13d55880ca0fc4521df6fe): content-derived identity, atomic
publish, fail-closed validation. Stela is GPL-2.0-or-later; **no Stela source
is copied here** -- this implementation is original and self-contained.

Caching is strictly opt-in: ``RECOMP_BUILD_CACHE=1``. With the variable unset
(or any value other than ``1``) :func:`compile_cached` compiles exactly as the
caller would without this module, and writes no cache entries.

Standalone by design: this file does not import the pipeline and the pipeline
does not import it. A caller wires it in explicitly; see docs/BUILD_CACHE.md.

    python3 -m tools.autorecomp.build_cache key --source gen.c --compiler cc \
        --flag=-O2 --flag=-w
    python3 -m tools.autorecomp.build_cache dir
    python3 -m tools.autorecomp.build_cache clear --key <64-hex>
    python3 -m tools.autorecomp.build_cache sources /path/to/project
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import uuid
from pathlib import Path
from typing import Callable, Sequence

# Bumped whenever the identity rules change, so older entries are never read
# by a newer implementation that interprets their key differently.
CACHE_SCHEMA = 'gamerecomp-build-cache-v1'
ENABLE_ENV = 'RECOMP_BUILD_CACHE'
DIR_ENV = 'RECOMP_BUILD_CACHE_DIR'
OBJECT_NAME = 'object.o'
MANIFEST_NAME = 'manifest.json'

# Guards for the compiler-identity probe: a wrapper script's --version can be
# unchanged while its behaviour is not, so its bytes are bound too, but only
# when reading them is cheap.
MAX_COMPILER_BYTES = 64 * 1024 * 1024
VERSION_PROBE_BYTES = 8192
VERSION_TIMEOUT_SECONDS = 10

_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR
_OBJ_MODE = 0o644
_HEX = frozenset('0123456789abcdef')


class CacheIneligible(ValueError):
    """Raised when a caller supplies a key this implementation cannot trust."""


def enabled() -> bool:
    """True only for the explicit opt-in value ``1``."""
    return os.environ.get(ENABLE_ENV) == '1'


def cache_dir() -> Path:
    """Resolve the cache root from the environment, never from a fixed path."""
    override = os.environ.get(DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path(os.environ.get('TMPDIR', '/tmp')) / 'gamerecomp-build-cache'


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_parts(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        b = part.encode('utf-8')
        h.update(len(b).to_bytes(8, 'big'))  # unambiguous even with NUL text
        h.update(b)
    return h.hexdigest()


def source_digest(source: Path | bytes) -> str:
    """SHA-256 of the source *bytes*, independent of path and mtime."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return _sha256(bytes(source))
    return _sha256(Path(source).read_bytes())


def flags_digest(flags: Sequence[str]) -> str:
    """SHA-256 of the ordered flag list.

    Order is significant (``-I a -I b`` is not ``-I b -I a``), and each flag
    is length-prefixed so ``['ab', 'c']`` and ``['a', 'bc']`` differ. Do not
    pass ``-c``, ``-o`` or the source path here; :func:`compile_cached`
    supplies those itself and they are not identity inputs.
    """
    return _digest_parts(CACHE_SCHEMA, *(str(f) for f in flags))


def _resolve_compiler(compiler: str | Path) -> str:
    path = Path(compiler)
    if path.exists():
        return str(path.resolve())
    return shutil.which(str(compiler)) or str(compiler)


def compiler_identity(compiler: str | Path,
                      version_output: str | bytes | None = None) -> str:
    """Stable identity digest for a compiler invocation.

    ``version_output`` can be supplied by callers (tests, or a caller that has
    already probed the compiler); otherwise ``<compiler> --version`` is run.
    A failed probe is recorded as an empty version rather than raising: the
    resolved path and the compiler file bytes still bind the identity, and a
    compiler that cannot describe itself will fail the real compile anyway.
    """
    spec = str(compiler)
    resolved = _resolve_compiler(compiler)
    if version_output is None:
        try:
            probe = subprocess.run(
                [resolved, '--version'], capture_output=True, timeout=VERSION_TIMEOUT_SECONDS)
            raw = (probe.stdout or b'') + b'\n' + (probe.stderr or b'')
        except (OSError, subprocess.SubprocessError):
            raw = b''
        version_output = raw[:VERSION_PROBE_BYTES]
    if isinstance(version_output, str):
        version_bytes = version_output.encode('utf-8')[:VERSION_PROBE_BYTES]
    else:
        version_bytes = bytes(version_output)[:VERSION_PROBE_BYTES]

    h = hashlib.sha256()
    for part in (CACHE_SCHEMA, spec, resolved):
        h.update(len(part.encode('utf-8')).to_bytes(8, 'big'))
        h.update(part.encode('utf-8'))
    h.update(len(version_bytes).to_bytes(8, 'big'))
    h.update(version_bytes)
    try:
        path = Path(resolved)
        if path.is_file() and path.stat().st_size <= MAX_COMPILER_BYTES:
            h.update(path.read_bytes())
        else:
            h.update(b'<compiler-bytes-unavailable>')
    except OSError:
        h.update(b'<compiler-bytes-unavailable>')
    return h.hexdigest()


def object_key(source: Path | bytes, compiler: str | Path,
               flags: Sequence[str], *, version_output: str | bytes | None = None,
               extra_identity: str = '') -> str:
    """Content address for one object: source bytes + compiler + flags.

    ``extra_identity`` is an optional caller-supplied discriminator for inputs
    that are not visible in the source bytes or flags (for example a build
    path embedded by ``-g``). It defaults to empty so the normal key is exactly
    the three documented components.
    """
    parts = (
        CACHE_SCHEMA,
        source_digest(source),
        compiler_identity(compiler, version_output),
        flags_digest(flags),
        extra_identity,
    )
    return _digest_parts(*parts)


def _valid_key(key: str) -> bool:
    return (isinstance(key, str) and len(key) == 64
            and all(ch in _HEX for ch in key))


def generated_sources(project_dir: Path, *, include_highlevel: bool = True) -> list[Path]:
    """List generated C sources in a project, in deterministic order.

    This is a convenience for callers caching a whole generated project; it
    does not modify the project and does not import the pipeline.
    """
    root = Path(project_dir)
    out: list[Path] = []
    gen = root / 'src' / 'recomp' / 'gen'
    if gen.is_dir():
        out.extend(sorted(gen.glob('recomp_*.c')))
    if include_highlevel:
        hl = root / 'src' / 'highlevel'
        if hl.is_dir():
            out.extend(sorted(hl.glob('*.c')))
    return out


class BuildObjectCache:
    """One content-addressed entry: an object file plus its checksum manifest.

    The object is published before the manifest, so the manifest is the commit
    marker. A reader that sees an object without a manifest (a crash mid-write)
    treats it as a miss. The manifest records the key and the object's SHA-256
    and length; every read re-verifies them, so a mismatched or corrupt entry
    is never used.
    """

    def __init__(self, key: str):
        if not _valid_key(key):
            raise CacheIneligible('key must be a 64-character lowercase SHA-256 hex digest')
        self.key = key
        self.root = cache_dir() / key[:2] / key
        self.object_path = self.root / OBJECT_NAME
        self.manifest_path = self.root / MANIFEST_NAME

    @staticmethod
    def _tmp_path(target: Path) -> Path:
        return target.with_name(f'{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}')

    @staticmethod
    def _write_atomic(target: Path, data: bytes, mode: int) -> None:
        tmp = BuildObjectCache._tmp_path(target)
        try:
            with tmp.open('wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, target)  # atomic on POSIX; same-volume on Windows
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass  # some filesystems do not permit directory fsync

    def get_bytes(self) -> bytes | None:
        """Return the cached object only when the whole entry validates."""
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
            obj = self.object_path.read_bytes()
        except (OSError, ValueError, TypeError, UnicodeDecodeError):
            return None
        if not isinstance(manifest, dict):
            return None
        if manifest.get('schema') != CACHE_SCHEMA or manifest.get('key') != self.key:
            return None
        if not obj:
            return None
        if manifest.get('object_sha256') != _sha256(obj):
            return None
        if manifest.get('object_bytes') != len(obj):
            return None
        return obj

    def put_bytes(self, data: bytes) -> bool:
        """Atomically publish an object; False means nothing was published.

        A valid entry is left untouched (a concurrent writer's completed entry
        wins). Empty objects are never cached.
        """
        if not data:
            return False
        if self.get_bytes() is not None:
            return False
        manifest = {
            'schema': CACHE_SCHEMA,
            'key': self.key,
            'object_sha256': _sha256(data),
            'object_bytes': len(data),
        }
        payload = (json.dumps(manifest, sort_keys=True) + '\n').encode('utf-8')
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_atomic(self.object_path, data, _FILE_MODE)
        self._write_atomic(self.manifest_path, payload, _FILE_MODE)
        self._fsync_dir(self.root)
        return True

    def capture_object(self, obj: Path) -> bool:
        """Capture a freshly compiled object file into the cache."""
        try:
            data = Path(obj).read_bytes()
        except OSError:
            return False
        return self.put_bytes(data)

    def get_object(self, dst: Path) -> bool:
        """Atomically install a valid cached object at ``dst``; False on miss."""
        data = self.get_bytes()
        if data is None:
            return False
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomic(dst, data, _OBJ_MODE)
        return True

    def clear(self) -> int:
        """Remove this entry and any abandoned temporary files. Return count."""
        removed = 0
        if self.root.exists():
            for path in self.root.iterdir():
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
            try:
                self.root.rmdir()
                parent = self.root.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
        return removed


RunFn = Callable[..., subprocess.CompletedProcess]


def compile_cached(source: Path, compiler: str | Path, flags: Sequence[str],
                   output: Path, *, cache: BuildObjectCache | None = None,
                   key: str | None = None, version_output: str | bytes | None = None,
                   extra_identity: str = '', env: dict | None = None,
                   run: RunFn | None = None) -> dict:
    """Compile one C source, reusing a validated cached object when enabled.

    The compile command is ``[compiler, *flags, '-c', source, '-o', tmp]``;
    the caller never supplies ``-c`` or ``-o`` in ``flags``. On a cache miss
    the object is compiled to a temporary file, captured only on success, and
    then installed atomically at ``output``. A failed compile leaves neither a
    cache entry nor a changed ``output``.

    ``run`` is injectable for tests; it defaults to ``subprocess.run`` and is
    called with ``capture_output=True, text=True``.

    Returns a JSON-serialisable dict with ``hit``, ``key``, ``cached``,
    ``compiled``, ``returncode``, ``stderr`` and ``output``.
    """
    src = Path(source)
    out = Path(output)
    runner: RunFn = run or subprocess.run
    active = enabled()
    if active and key is None:
        key = object_key(src, compiler, flags, version_output=version_output,
                         extra_identity=extra_identity)
    if active and cache is None and key is not None:
        cache = BuildObjectCache(key)

    result = {
        'hit': False,
        'key': key,
        'cached': False,
        'compiled': False,
        'returncode': None,
        'stderr': '',
        'output': str(out),
    }
    if active and cache is not None and cache.get_object(out):
        result.update(hit=True, cached=True)
        return result

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_obj = BuildObjectCache._tmp_path(out)
    cmd = [str(compiler), *(str(f) for f in flags), '-c', str(src), '-o', str(tmp_obj)]
    try:
        proc = runner(cmd, env=env, capture_output=True, text=True)
        returncode = proc.returncode
        stderr = (proc.stderr or proc.stdout or '')
    except OSError as exc:  # compiler missing/unusable: a hard failure, no cache use
        returncode, stderr = 127, str(exc)
    finally:
        if tmp_obj.exists():
            try:
                data = tmp_obj.read_bytes()
            except OSError:
                data = b''
            try:
                tmp_obj.unlink()
            except OSError:
                pass
        else:
            data = b''

    result['returncode'] = returncode
    result['stderr'] = stderr
    if returncode != 0 or not data:
        return result

    if active and cache is not None:
        cache.put_bytes(data)
    BuildObjectCache._write_atomic(out, data, _OBJ_MODE)
    result['compiled'] = True
    return result


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--version', action='version', version='build-cache 1.0')
    sub = ap.add_subparsers(dest='command', required=True)

    k = sub.add_parser('key', help='print the object key for one compile')
    k.add_argument('--source', required=True, type=Path)
    k.add_argument('--compiler', required=True)
    k.add_argument('--flag', action='append', default=[])
    k.add_argument('--extra-identity', default='')
    k.add_argument('--verbose', action='store_true', help='print all components')

    sub.add_parser('dir', help='print the resolved cache directory')
    c = sub.add_parser('clear', help='remove one cache entry')
    c.add_argument('--key', required=True)
    s = sub.add_parser('sources', help='list generated C sources in a project')
    s.add_argument('project', type=Path)

    args = ap.parse_args(argv)
    if args.command == 'key':
        parts = {
            'source_sha256': source_digest(args.source),
            'compiler_identity': compiler_identity(args.compiler),
            'flags_sha256': flags_digest(args.flag),
            'key': object_key(args.source, args.compiler, args.flag,
                              extra_identity=args.extra_identity),
        }
        print(json.dumps(parts if args.verbose else {'key': parts['key']}, indent=2))
    elif args.command == 'dir':
        print(cache_dir())
    elif args.command == 'clear':
        print(json.dumps({'removed': BuildObjectCache(args.key).clear()}))
    elif args.command == 'sources':
        for path in generated_sources(args.project):
            print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
