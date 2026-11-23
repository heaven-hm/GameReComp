#!/usr/bin/env python3
""": which downloaded Windows exes have native builds at all.

A header-inspected executable is NOT a recompiled executable. This tool walks
supplied autorecomp project directories and reports, per input, whether a
native binary and a report.json exist, with measured sizes and hashes. Anything
missing is explicitly `NOT_RECOMPILED`, never a pass.

Usage: python3 -m tools.recompile_ledger --project <dir> [--project ...] \
    --out <json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def scan(project: Path) -> dict:
    rec = {'project': str(project), 'name': project.name,
           'status': 'NOT_RECOMPILED', 'binary': None, 'report': None}
    report = project / 'reports' / 'report.json'
    if report.is_file():
        try:
            data = json.loads(report.read_text())
            rec['score'] = data.get('score')
            rec['stages'] = {s['name']: s['status'] for s in data.get('stages', [])}
        except (ValueError, KeyError, TypeError):
            rec['report'] = 'unreadable'
        else:
            rec['report'] = str(report)
    # A stale or partial build artifact is not a recompile: require a report
    # whose compile stage actually succeeded.
    stages = rec.get('stages') or {}
    if stages.get('compile') != 'ok':
        rec['status'] = 'NOT_RECOMPILED'
        return rec
    build = project / 'build'
    if build.is_dir():
        for p in sorted(build.iterdir()):
            if p.is_file() and p.stat().st_size > 100000 and p.suffix == '':
                raw = p.read_bytes()
                rec['binary'] = str(p)
                rec['binary_size'] = len(raw)
                rec['binary_sha256'] = hashlib.sha256(raw).hexdigest()
                rec['status'] = 'NATIVE_BINARY_PRESENT'
                break
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', action='append', type=Path, required=True)
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args(argv)
    rows = [scan(p) for p in args.project]
    args.out.write_text(json.dumps({
        'schema': 1,
        'what_it_proves': 'existence of native binaries, not execution or compatibility',
        'results': rows,
    }, indent=2))
    for r in rows:
        print(f"{r['name'][:28]:28} {r['status']:24} "
              f"{r.get('binary_size', 0):>12} bytes")
    print(f'wrote {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
