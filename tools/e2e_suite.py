#!/usr/bin/env python3
"""One-command end-to-end suite: CLI, GUI (CUA), AI readability, evidence.

Runs real binaries and real GUI clicks, then chains the machine-readable
outputs into one report. It never prints or stores credentials; the AI key is
only inherited from the caller's environment by the child processes.

Usage:
  python3 -m tools.e2e_suite --repo <dir> --cli <bin> --gui <bin> \
      --fixture <project> --ai-bin-dir <dir> --driver <cua-driver> \
      --game-summary <summary.json> --frame-proof <frame-proof.json> \
      --recompile-ledger <ledger.json> --out <dir>
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def run(cmd: list[str], timeout: int = 300, env: dict | None = None) -> dict:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors='replace', timeout=timeout,
                           check=False, env=env)
        return {'command': cmd, 'rc': p.returncode, 'output': p.stdout[-8000:]}
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ''
        if isinstance(out, bytes):
            out = out.decode('utf-8', 'replace')
        return {'command': cmd, 'rc': 'TIMEOUT', 'output': out[-8000:]}


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


class Cua:
    def __init__(self, driver: Path):
        self.driver = str(driver)

    def call(self, tool: str, args: dict) -> dict:
        p = self.call_raw(tool, args)
        try:
            return json.loads(p.stdout, strict=False)
        except ValueError:
            return {'error': p.stdout[-1000:]}

    def call_raw(self, tool: str, args: dict):
        return subprocess.run([self.driver, 'call', tool, json.dumps(args), '--json'],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=60, check=False)

    def window(self, app: str = 'game-recomp-gui') -> tuple[int, int] | None:
        tree = self.call('get_accessibility_tree', {})
        for w in tree.get('windows', []):
            if w.get('app_name') == app:
                return int(w['pid']), int(w['window_id'])
        return None

    def snapshot(self, pid: int, wid: int, shot: Path | None = None) -> dict:
        rec = self.call('get_window_state', {'pid': pid, 'window_id': wid})
        blob = rec.get('screenshot_png_b64')
        if blob:
            raw = base64.b64decode(blob)
            rec['sha256'] = hashlib.sha256(raw).hexdigest()
            if shot:
                shot.write_bytes(raw)
        return rec


def summarize_games(summary: dict | None, proof: dict | None) -> dict:
    frames = (proof or {}).get('frames', [])
    nonblank = sum(1 for f in frames if not f.get('blank'))
    games = (summary or {}).get('results', [])
    return {'games_executed': len(games),
            'games_with_frames': sum(1 for g in games if g.get('frame_count', 0) > 0),
            'frames_total': len(frames), 'frames_nonblank': nonblank,
            'frames_blank': len(frames) - nonblank}


def summarize_recompiles(ledger: dict | None) -> dict:
    rows = (ledger or {}).get('results', [])
    return {'native_binaries': sum(1 for r in rows
                                  if r['status'] == 'NATIVE_BINARY_PRESENT'),
            'header_only': sum(1 for r in rows
                               if r['status'] == 'NOT_RECOMPILED'),
            'total': len(rows)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', required=True, type=Path)
    ap.add_argument('--cli', required=True, type=Path)
    ap.add_argument('--gui', required=True, type=Path)
    ap.add_argument('--fixture', required=True, type=Path)
    ap.add_argument('--inspect-exe', type=Path,
                    help='real PE32 file for the CLI inspect smoke check')
    ap.add_argument('--ai-bin-dir', required=True, type=Path)
    ap.add_argument('--driver', required=True, type=Path)
    ap.add_argument('--game-summary', type=Path)
    ap.add_argument('--frame-proof', type=Path)
    ap.add_argument('--recompile-ledger', type=Path)
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    steps = []
    env = dict(os.environ)
    env['PATH'] = str(args.ai_bin_dir) + os.pathsep + env.get('PATH', '')
    env.setdefault('REVENG_AI_CMD', 'llm')
    env.setdefault('GAMERECOMP_AI_MAX_TOKENS', '1024')

    # 1. CLI smoke and AI readability, in one inherited environment.
    inspect_target = args.inspect_exe or args.fixture
    for name, extra in [('cli_version', ['--version']), ('cli_about', ['about']),
                        ('cli_inspect', ['inspect', str(inspect_target), '--json'])]:
        r = run([str(args.cli)] + extra, 120, env)
        steps.append({'step': name, 'rc': r['rc'], 'output': r['output'][:400]})
        print(f'{name:20} rc={r["rc"]}', flush=True)
    ai = run([str(args.cli), 'readability', str(args.fixture), '--providers', 'ai',
              '--function', '0x00402020', '--max-attempts', '3',
              '--max-seconds', '240', '--provider-timeout', '120'], 300, env)
    outcome = read_json(args.fixture / 'reports' / 'readability.json') or {}
    steps.append({'step': 'cli_ai_readability', 'rc': ai['rc'],
                  'outcome': outcome.get('outcome'),
                  'reason': outcome.get('reason', '')[:200]})
    print(f'cli_ai_readability rc={ai["rc"]} outcome={outcome.get("outcome")}',
          flush=True)

    # 2. GUI via CuaDriver: launch, find window, click AX label, verify.
    cua = Cua(args.driver)
    log = (args.out / 'gui.log').open('w')
    proc = subprocess.Popen([str(args.gui), str(args.fixture)], stdout=log,
                            stderr=log, env=env)
    try:
        target = None
        for _ in range(20):
            target = cua.window()
            if target:
                break
            time.sleep(0.5)
        if target:
            pid, wid = target
            before = cua.snapshot(pid, wid)
            # Open Agents is a custom-drawn control on the fixture view.
            cua.call('click', {'pid': pid, 'window_id': wid, 'x': 1460, 'y': 695,
                               'delivery_mode': 'foreground'})
            time.sleep(1)
            snap = cua.snapshot(pid, wid)
            token = None
            for el in snap.get('elements', []):
                if 'Improve C Readability' in (el.get('label') or ''):
                    token = el.get('element_token')
                    break
            if token:
                cua.call('click', {'pid': pid, 'window_id': wid,
                                   'element_token': token})
            time.sleep(12)
            after = cua.snapshot(pid, wid, args.out / 'gui-final.png')
            outcome2 = read_json(args.fixture / 'reports' / 'readability.json') or {}
            steps.append({'step': 'gui_ai_readability',
                          'window_found': True, 'ax_button_found': bool(token),
                          'screenshot_changed': before.get('sha256') != after.get('sha256'),
                          'outcome': outcome2.get('outcome'),
                          'reason': outcome2.get('reason', '')[:200]})
            print(f'gui_ai_readability button={bool(token)} '
                  f'outcome={outcome2.get("outcome")}', flush=True)
        else:
            steps.append({'step': 'gui_ai_readability', 'window_found': False})
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    games = summarize_games(read_json(args.game_summary) if args.game_summary else None,
                            read_json(args.frame_proof) if args.frame_proof else None)
    recomp = summarize_recompiles(
        read_json(args.recompile_ledger) if args.recompile_ledger else None)
    report = {'schema': 1, 'steps': steps, 'games': games, 'recompiles': recomp,
              'credentials_in_report': False}
    (args.out / 'e2e-report.json').write_text(json.dumps(report, indent=2))
    ok = (steps[0]['rc'] == 0 and steps[1]['rc'] == 0 and
          steps[2]['rc'] == 0 and steps[3].get('outcome') == 'complete' and
          steps[4].get('outcome') == 'complete')
    (args.out / 'e2e-report.md').write_text(
        '# End-to-end suite\n\n' +
        '\n'.join(f"- **{s['step']}**: " + str(s.get('outcome', s.get('rc'))) for s in steps) +
        f"\n\nGames: {games}\nRecompiles: {recomp}\n\n"
        f"Verdict: {'PASS' if ok else 'PARTIAL/FAIL'}. No secret values are stored.\n")
    print('verdict', 'PASS' if ok else 'PARTIAL/FAIL', flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
