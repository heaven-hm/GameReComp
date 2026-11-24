#!/usr/bin/env python3
"""Drive the existing GUI's built-in selftest modes over every game/project.

The GUI binary exposes --selftest-pe <exe> (PE inspection, the same path the
file-picker uses) and --selftest <project> --repo <root> (project model, stages,
frames and build digest, the same data the home cards render). This driver
runs those modes and stores exact command/exit/output evidence. It does not
rebuild or replace the GUI.

Usage:
  python3 -m tools.gui_matrix --gui <binary> --repo <repo> \
      --exe <exe> [--exe ...] --project <dir> [--project ...] --out <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], timeout: int) -> dict:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=timeout, check=False)
        return {'command': cmd, 'exit_code': p.returncode, 'stdout': p.stdout[-6000:]}
    except subprocess.TimeoutExpired as exc:
        text = exc.stdout or ''
        if isinstance(text, bytes):
            text = text.decode('utf-8', 'replace')
        return {'command': cmd, 'exit_code': 'TIMEOUT', 'stdout': text[-6000:]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--gui', required=True, type=Path)
    ap.add_argument('--repo', required=True, type=Path)
    ap.add_argument('--exe', action='append', type=Path, default=[])
    ap.add_argument('--project', action='append', type=Path, default=[])
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--timeout', type=int, default=30)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for exe in args.exe:
        if not exe.is_file():
            continue
        rec = run([str(args.gui), '--selftest-pe', str(exe)], args.timeout)
        rec['kind'] = 'pe'
        rec['input'] = str(exe)
        rec['sha256'] = hashlib.sha256(exe.read_bytes()).hexdigest()
        results.append(rec)
        print(f"PE  {exe.name:32} rc={rec['exit_code']}", flush=True)
    for project in args.project:
        if not project.is_dir():
            continue
        rec = run([str(args.gui), '--selftest', str(project), '--repo', str(args.repo)],
                  args.timeout)
        rec['kind'] = 'project'
        rec['input'] = str(project)
        results.append(rec)
        print(f"PROJ {project.name:31} rc={rec['exit_code']}", flush=True)
    summary = args.out / 'gui-summary.json'
    summary.write_text(json.dumps({'schema': 1, 'results': results}, indent=2))
    print(f'summary: {summary}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
