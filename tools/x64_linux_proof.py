#!/usr/bin/env python3
"""Reproducible PE32+ -> native Linux proof for one real Windows executable.

Runs the entire chain with bounded timeouts and writes one evidence JSON:

    disasm64 -> generate64 -> x64_project -> linux_matrix (Docker) -> [Highball]

Every stage records its exact command, exit code and (truncated) output. The
Linux row comes from tools/linux_matrix.py's own linux-summary.json, so the ELF
header/hash and container stdout are measured, not inferred. A Highball run of
the original is optional (--highball) and is compared byte-for-byte against the
lifted ELF's stdout, with exit codes recorded separately.

The --work directory must be a host path shared with the Docker VM (e.g. under
$HOME); tools/linux_matrix.py probes this before building and fails closed.

Usage:
    python3 -m tools.x64_linux_proof --exe ~/Downloads/.../arith-calc.exe \
        --work ~/l64-proof --out ~/l64-proof/evidence.json [--highball]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd, timeout, cwd=None):
    started = time.time()
    try:
        p = subprocess.run([str(x) for x in cmd], cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
        return {'cmd': [str(x) for x in cmd], 'rc': p.returncode,
                'stdout': p.stdout[-8000:], 'stderr': p.stderr[-8000:],
                'seconds': round(time.time() - started, 2)}
    except subprocess.TimeoutExpired as e:
        return {'cmd': [str(x) for x in cmd], 'rc': 'TIMEOUT',
                'stdout': str(e.stdout or '')[-8000:],
                'stderr': str(e.stderr or '')[-8000:],
                'seconds': round(time.time() - started, 2)}
    except OSError as e:
        return {'cmd': [str(x) for x in cmd], 'rc': 'OS_ERROR',
                'stdout': '', 'stderr': '%s: %s' % (type(e).__name__, e),
                'seconds': round(time.time() - started, 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--exe', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True,
                    help='host path shared with Docker VM')
    ap.add_argument('--out', type=Path, help='evidence JSON path')
    ap.add_argument('--highball', action='store_true',
                    help='also run the original under Highball and compare')
    ap.add_argument('--highball-bin', default='highball')
    args = ap.parse_args(argv)

    exe = args.exe.resolve()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    stem = exe.stem
    gen = work / (stem + '-gen')
    project = work / ('proj-' + stem)
    results = work / ('results-' + stem)
    gen.mkdir(exist_ok=True)

    ev = {'schema': 1, 'target': str(exe), 'work': str(work),
          'pipeline': [], 'verdict': 'BLOCKED'}

    ev['pipeline'].append({'stage': 'disasm64', **run(
        [sys.executable, '-m', 'tools.disasm.disasm64', exe,
         '-o', work / (stem + '.json'),
         '--funcs-txt', work / (stem + '.funcs.txt')], 300, ROOT)})
    if ev['pipeline'][-1]['rc'] != 0:
        ev['blocker'] = 'disasm64 failed'
        (args.out or work / 'evidence.json').write_text(json.dumps(ev, indent=2))
        return 2

    ev['pipeline'].append({'stage': 'generate64', **run(
        [sys.executable, '-m', 'tools.lift.generate64', exe,
         work / (stem + '.funcs.txt'), gen], 300, ROOT)})
    if ev['pipeline'][-1]['rc'] != 0:
        ev['blocker'] = 'generate64 failed'
        (args.out or work / 'evidence.json').write_text(json.dumps(ev, indent=2))
        return 2

    ev['pipeline'].append({'stage': 'x64_project', **run(
        [sys.executable, '-m', 'tools.x64_project', '--exe', exe,
         '--gen', gen, '--out', project], 120, ROOT)})
    if ev['pipeline'][-1]['rc'] != 0:
        ev['blocker'] = 'project generation failed'
        (args.out or work / 'evidence.json').write_text(json.dumps(ev, indent=2))
        return 2

    ev['pipeline'].append({'stage': 'linux_matrix', **run(
        [sys.executable, '-m', 'tools.linux_matrix', '--project', project,
         '--out', results, '--original', exe, '--provision', 'prebuilt'],
        1800, ROOT)})
    summary_path = results / 'linux-summary.json'
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text())
        ev['linux_summary'] = summary
        rows = summary.get('results', [])
        ev['linux_row'] = rows[0] if rows else None
        if rows and rows[0].get('build_rc') == 0 and rows[0].get('run_rc') == 0 \
                and rows[0].get('elf', {}).get('status') == 'OK':
            ev['verdict'] = 'BUILT_AND_RAN'
        else:
            ev['blocker'] = 'linux_matrix row did not build and run'
    else:
        ev['blocker'] = 'linux_matrix produced no summary'

    if args.highball:
        # Highball's Wine bottle is occasionally busy right after another run
        # (`exit=1 after 0s` with no guest output). Retry a bounded number of
        # times and keep EVERY attempt verbatim; never pick a successful retry
        # while hiding a failed one.
        attempts = []
        for attempt in range(1, 4):
            rec = {'stage': 'highball_original', 'attempt': attempt, **run(
                [args.highball_bin, 'run', exe], 300, ROOT)}
            attempts.append(rec)
            ev['pipeline'].append(rec)
            if rec['rc'] == 0 and 'Launching ' in rec['stdout']:
                break
            if attempt < 3:
                time.sleep(3)
        ev['highball_attempts'] = [
            {'attempt': a['attempt'], 'rc': a['rc'], 'stdout': a['stdout']}
            for a in attempts
        ]
        hb = next((a for a in attempts
                   if a['rc'] == 0 and 'Launching ' in a['stdout']), attempts[-1])
        raw = hb['stdout'].strip()
        # `highball run` prints its own launcher/exit summary on stdout after
        # the guest's output. Strip only those two known lines; nothing else is
        # dropped, so a divergence in guest output still fails the comparison.
        guest = '\n'.join(
            line for line in raw.splitlines()
            if not line.startswith('Launching ')
            and not re.match(r'^exit=\d+ after ', line)
        ).strip()
        ev['highball'] = {'rc': hb['rc'], 'stdout': raw, 'guest_stdout': guest}
        if ev.get('linux_row'):
            lifted = ev['linux_row'].get('run_log', '').strip()
            ev['comparison'] = {
                'stdout_exact_match': lifted == guest,
                'exit_code_match': ev['linux_row'].get('run_rc') == hb['rc'],
                'normalisation': 'drop Highball launcher lines: "Launching ..." and "exit=N after ..."',
                'lifted_stdout': lifted,
                'lifted_exit_code': ev['linux_row'].get('run_rc'),
                'original_stdout': guest,
                'original_raw_stdout': raw,
                'original_exit_code': hb['rc'],
            }

    out = (args.out or work / 'evidence.json').resolve()
    out.write_text(json.dumps(ev, indent=2) + '\n')
    print('evidence: %s (verdict=%s)' % (out, ev['verdict']))
    return 0 if ev['verdict'] == 'BUILT_AND_RAN' else 1


if __name__ == '__main__':
    sys.exit(main())
