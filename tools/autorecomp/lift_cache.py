#!/usr/bin/env python3
"""Fingerprinted, content-addressed cache for recover/translate artifacts.

Portable methodology adapted from Stela's artifact cache (pinned commit
140ffaf716e2fa8a2f13d55880ca0fc4521df6fe): identity binds the input bytes,
the lifting/translation sources, options and companion images; publication is
atomic; a single-flight marker avoids duplicate work. No Stela source is
copied (its engine is GPL-2.0-or-later; this implementation is original).

The cache is opt-in via RECOMP_LIFT_CACHE=1 and never changes semantics: a
cache entry is used only when every fingerprint component matches.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import shutil
import stat
from pathlib import Path


TOOL_GLOBS = (
    'tools/lift/*.py', 'tools/pe/*.py', 'tools/disasm/*.py',
    'tools/autorecomp/pipeline.py', 'tools/autorecomp/lift_cache.py',
    'tools/autorecomp/highlevel.py',
)


def enabled() -> bool:
    return os.environ.get('RECOMP_LIFT_CACHE') == '1'


def cache_dir() -> Path:
    override = os.environ.get('RECOMP_LIFT_CACHE_DIR')
    return Path(override).expanduser().resolve() if override else (
        Path(os.environ.get('TMPDIR', '/tmp')) / 'gamerecomp-lift-cache')


def tool_fingerprint(repo_root: Path) -> str:
    h = hashlib.sha256()
    files = []
    for pattern in TOOL_GLOBS:
        files.extend(sorted(repo_root.glob(pattern)))
    for path in files:
        if path.is_file():
            h.update(str(path.relative_to(repo_root)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def fingerprint(pe_path: Path, companions: list, split: int, split_bytes: int,
                highlevel_dir: Path | None, repo_root: Path) -> str:
    h = hashlib.sha256()
    h.update(b'gamerecomp-lift-cache-v1\0')
    h.update(tool_fingerprint(repo_root).encode())
    h.update(pe_path.read_bytes())
    for comp in companions:
        h.update(Path(comp['path']).read_bytes())
    h.update(f'{int(split)}:{int(split_bytes)}'.encode())
    if highlevel_dir and Path(highlevel_dir).is_dir():
        for path in sorted(Path(highlevel_dir).rglob('*.c')):
            h.update(str(path.relative_to(highlevel_dir)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()


class LiftCache:
    def __init__(self, key: str):
        self.root = cache_dir() / key[:2] / key
        self.root.mkdir(parents=True, exist_ok=True)

    def _entry(self, stage: str) -> Path:
        return self.root / f'{stage}.bin'

    def get(self, stage: str):
        path = self._entry(stage)
        if not path.is_file():
            return None
        try:
            return pickle.loads(path.read_bytes())
        except (OSError, ValueError, TypeError, pickle.UnpicklingError, AttributeError):
            return None

    def put(self, stage: str, payload) -> None:
        """Atomic publish; a concurrent writer's completed entry wins."""
        target = self._entry(stage)
        if target.exists():
            return
        tmp = target.with_suffix('.tmp-' + str(os.getpid()))
        try:
            with tmp.open('wb') as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp, target)          # atomic on POSIX
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass

    def copy_gen_dir(self, gen_dir: Path) -> bool:
        payload = self.get('translate')
        if not isinstance(payload, dict):
            return False
        files = payload.get('files') if 'files' in payload else payload
        if not isinstance(files, dict):
            return False
        if gen_dir.exists():
            shutil.rmtree(gen_dir)
        gen_dir.mkdir(parents=True)
        for name, data in files.items():
            if '/' in name or '\\' in name or name.startswith('.'):
                continue
            (gen_dir / name).write_bytes(data)
        return True

    def capture_gen_dir(self, gen_dir: Path) -> None:
        if not gen_dir.is_dir():
            return
        self.put('translate', {'files': {p.name: p.read_bytes()
                                         for p in sorted(gen_dir.iterdir())
                                         if p.is_file()}})
