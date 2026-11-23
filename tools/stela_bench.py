#!/usr/bin/env python3
"""Reproducible GameReComp benchmark used for the Stela port evaluation.

Measures one PE32 input with the real pipeline and records per-stage seconds,
generated source bytes, binary bytes and wall time. Inputs are CLI-supplied;
no game path is hard-coded. A result is a measurement, never a speed claim
until compared with a baseline under the same settings.

Usage:
  python3 -m tools.stela_bench --pe <exe> --project <dir> --out <json> \
      [--mode source|binary] [--jobs 1]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


def bench(pe: Path, project: Path, mode: str, jobs: int, timeout: int) -> dict:
    cmd = [sys.executable, '-m', 'tools.autorecomp', str(pe),
           '--project-dir', str(project), '--assets-dir', str(pe.parent),
           '--no-table', '--no-progress', '--input-os', 'windows',
           '--input-arch', 'x86', '--target-os', 'host', '--target-arch', 'host',
           '--jobs', str(jobs),
           '--out-mode', 'source-only' if mode == 'source' else 'binary-only',
           '--no-run']
    t0 = time.time()
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors='replace', timeout=timeout, check=False)
        rc, output = p.returncode, p.stdout
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ''
        if isinstance(out, bytes):
            out = out.decode('utf-8', 'replace')
        rc, output = 'TIMEOUT', out
    wall = round(time.time() - t0, 2)
    report = project / 'reports' / 'report.json'
    data = json.loads(report.read_text()) if report.is_file() else {}
    stages = {s['name']: s.get('seconds') for s in data.get('stages', [])}
    tr = next((s.get('data') or {} for s in data.get('stages', [])
               if s['name'] == 'translate'), {})
    compiler = next((s.get('data') or {} for s in data.get('stages', [])
                     if s['name'] == 'compile'), {})
    return {
        'input': str(pe),
        'input_sha256': hashlib.sha256(pe.read_bytes()).hexdigest(),
        'mode': mode, 'jobs': jobs, 'rc': rc, 'wall_seconds': wall,
        'stages_seconds': stages,
        'generated_source_bytes': tr.get('source_bytes', 0),
        'generated_units': tr.get('source_units', 0),
        'binary_bytes': (project / 'build' / project.name).stat().st_size
                        if (project / 'build' / project.name).is_file() else 0,
        'compile_seconds': compiler.get('seconds'),
        'output_tail': output[-2000:],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pe', required=True, type=Path)
    ap.add_argument('--project', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--mode', choices=('source', 'binary'), default='source')
    ap.add_argument('--jobs', type=int, default=1)
    ap.add_argument('--timeout', type=int, default=1800)
    a = ap.parse_args(argv)
    rec = bench(a.pe, a.project, a.mode, a.jobs, a.timeout)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rec, indent=2))
    print(f"{a.pe.name}: rc={rec['rc']} wall={rec['wall_seconds']}s "
          f"source={rec['generated_source_bytes']}B "
          f"stages={rec['stages_seconds']}")
    return 0 if rec['rc'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
