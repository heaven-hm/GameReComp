#!/usr/bin/env python3
"""Run the existing GameReComp CLI over every supplied PE32 file.

Records exact command, exit code and output for version/about/setup/tasks and
`inspect --json` on each executable. `inspect` is a header check, not execution
or compatibility proof; the report says so.

Usage: python3 -m tools.cli_matrix --cli <binary> --repo <dir> \
    --exe <file> [--exe ...] --project <dir> --out <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def run(cmd: list[str], timeout: int = 60) -> dict:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors='replace', timeout=timeout, check=False)
        return {'command': cmd, 'exit_code': p.returncode, 'stdout': p.stdout[-6000:]}
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ''
        if isinstance(out, bytes):
            out = out.decode('utf-8', 'replace')
        return {'command': cmd, 'exit_code': 'TIMEOUT', 'stdout': out[-6000:]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cli', required=True, type=Path)
    ap.add_argument('--repo', required=True, type=Path)
    ap.add_argument('--exe', action='append', type=Path, default=[])
    ap.add_argument('--project', action='append', type=Path, default=[])
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    cli = str(args.cli)
    results = []
    for name, extra in [('version', ['--version']), ('about', ['about']),
                        ('setup', ['setup']), ('tasks', ['tasks'])]:
        rec = run([cli] + extra + ['--repo', str(args.repo)])
        rec['kind'] = name
        results.append(rec)
        print(f'CLI {name:10} rc={rec["exit_code"]}', flush=True)
    for project in args.project:
        rec = run([cli, 'status', str(project), '--repo', str(args.repo)])
        rec['kind'] = 'status'
        rec['input'] = str(project)
        results.append(rec)
        print(f'CLI status    {project.name[:28]:28} rc={rec["exit_code"]}', flush=True)
    for exe in args.exe:
        if not exe.is_file():
            continue
        rec = run([cli, 'inspect', str(exe), '--json', '--repo', str(args.repo)])
        rec['kind'] = 'inspect'
        rec['input'] = str(exe)
        rec['sha256'] = hashlib.sha256(exe.read_bytes()).hexdigest()
        results.append(rec)
        print(f'CLI inspect   {exe.name[:28]:28} rc={rec["exit_code"]}', flush=True)
    summary = args.out / 'cli-summary.json'
    summary.write_text(json.dumps({
        'schema': 1,
        'what_it_proves': 'CLI subcommands run and fail-closed header checks; no execution or gameplay',
        'results': results,
    }, indent=2))
    print(f'summary: {summary}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
