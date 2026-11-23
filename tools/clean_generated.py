#!/usr/bin/env python3
"""Reclaim disk after autorecomp runs without losing evidence.

Generated C trees and build directories are the large, reproducible products
of a run; reports/*.json and *.html are the small, irreplaceable evidence.
This tool copies reports to a durable directory, then removes only `src/` and
`build/` from the named project directories.

Fail-closed rules:
- dry-run is the DEFAULT; `--apply` is required to delete anything;
- a project without a verified reports copy is never touched;
- paths must be outside the repository (no source-tree deletion);
- symlinks and paths failing verification are skipped with a reason.

Usage:
  python3 -m tools.clean_generated --project <dir> --durable <dir> [--apply]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


REPORT_NAMES = ('report.json', 'verification.json', 'report.html')


def report_files(project: Path) -> list[Path]:
    reports = project / 'reports'
    if not reports.is_dir():
        return []
    return [p for p in sorted(reports.iterdir())
            if p.is_file() and p.name in REPORT_NAMES]


def clean_one(project: Path, durable: Path, apply: bool, repo_root: Path) -> dict:
    rec = {'project': str(project), 'removed': [], 'kept': [], 'reason': None}
    try:
        project = project.resolve()
        durable = durable.resolve()
        repo_root = repo_root.resolve()
    except OSError as exc:
        rec['reason'] = f'cannot resolve path: {exc}'
        return rec
    if project == repo_root or repo_root in project.parents:
        rec['reason'] = 'refusing to delete inside the repository'
        return rec
    if not project.is_dir():
        rec['reason'] = 'project directory does not exist'
        return rec

    files = report_files(project)
    if not files:
        rec['reason'] = 'no reports to preserve; refusing to delete'
        return rec

    target = durable / project.name
    if apply:
        target.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, target / f.name)
    copied = [f.name for f in files]
    # Verify the durable copy before deleting anything.
    for name in copied:
        if not (target / name).is_file() and apply:
            rec['reason'] = f'durable copy missing: {name}'
            return rec
    rec['preserved'] = copied

    for name in ('src', 'build'):
        victim = project / name
        if victim.is_symlink():
            rec['kept'].append(f'{name} (symlink)')
            continue
        if victim.is_dir():
            if apply:
                shutil.rmtree(victim, ignore_errors=False)
            rec['removed'].append(str(victim))
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', action='append', type=Path, required=True,
                    help='autorecomp project directory (repeatable)')
    ap.add_argument('--durable', required=True, type=Path,
                    help='directory where reports are preserved')
    ap.add_argument('--repo-root', type=Path,
                    default=Path(__file__).resolve().parents[1])
    ap.add_argument('--apply', action='store_true',
                    help='actually delete; default is a dry run')
    args = ap.parse_args(argv)

    failures = 0
    for project in args.project:
        rec = clean_one(project, args.durable, args.apply, args.repo_root)
        status = rec['reason'] or ','.join(rec['removed']) or 'nothing to remove'
        print(f"{rec['project']}: {status}")
        if rec['reason']:
            failures += 1
    if not args.apply:
        print('dry run: nothing deleted (pass --apply to delete)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
