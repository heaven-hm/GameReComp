#!/usr/bin/env python3
"""Drive the existing GUI with CUA's real mouse clicks and capture proof.

This does not rebuild the GUI. It launches the shipped binary, finds its
window through CuaDriver's accessibility tree, clicks each named control with
foreground CGEvent delivery, and records before/after screenshots with SHA-256
so a click that did nothing is a FAIL, not a PASS.

Usage:
  python3 -m tools.gui_cua_test --gui <binary> --project <dir> \
      --out <evidence-dir> [--driver <cua-driver>]
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


# Coordinates from the reference 1568x1033 CUA screenshot of this GUI.
REF_W, REF_H = 1568, 1033
STEPS = [
    {'name': 'functions_tab', 'kind': 'pixel', 'x': 500, 'y': 252},
    {'name': 'stages_tab', 'kind': 'pixel', 'x': 660, 'y': 252},
    {'name': 'agents_tab', 'kind': 'pixel', 'x': 780, 'y': 252},
    {'name': 'frames_tab', 'kind': 'pixel', 'x': 860, 'y': 252},
    {'name': 'settings_tab', 'kind': 'pixel', 'x': 952, 'y': 252},
    {'name': 'overview_tab', 'kind': 'pixel', 'x': 370, 'y': 252},
    {'name': 'run_options_button', 'kind': 'ax', 'label': 'Run Options',
     'escape': True},
    {'name': 'open_agents_button', 'kind': 'ax', 'label': 'Open Agents'},
    {'name': 'sidebar_settings', 'kind': 'pixel', 'x': 78, 'y': 849},
]


def scaled(x: int, y: int, w: int, h: int) -> tuple[int, int]:
    return round(x * w / REF_W), round(y * h / REF_H)


class Cua:
    def __init__(self, driver: Path):
        self.driver = str(driver)

    def call(self, tool: str, args: dict) -> dict:
        p = subprocess.run([self.driver, 'call', tool, json.dumps(args), '--json'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=60, check=False)
        try:
            # GPUI labels occasionally contain raw control characters; the
            # driver's own JSON is otherwise well formed.
            return json.loads(p.stdout, strict=False)
        except ValueError:
            return {'error': p.stdout[-2000:]}

    def find_window(self, app: str = 'game-recomp-gui') -> tuple[int, int] | None:
        tree = self.call('get_accessibility_tree', {})
        for w in tree.get('windows', []):
            if w.get('app_name') == app:
                return int(w['pid']), int(w['window_id'])
        return None

    def snapshot(self, pid: int, wid: int, png: Path | None = None) -> dict:
        rec = self.call('get_window_state', {'pid': pid, 'window_id': wid})
        blob = rec.get('screenshot_png_b64')
        if blob:
            raw = base64.b64decode(blob)
            rec['screenshot_sha256'] = hashlib.sha256(raw).hexdigest()
            if png:
                png.write_bytes(raw)
        return rec

    def click(self, pid: int, wid: int, x: int, y: int) -> dict:
        return self.call('click', {'pid': pid, 'window_id': wid, 'x': x, 'y': y,
                                   'delivery_mode': 'foreground'})

    def key(self, pid: int, wid: int, key: str) -> dict:
        return self.call('press_key', {'pid': pid, 'window_id': wid, 'key': key,
                                       'delivery_mode': 'foreground'})

    def click_label(self, pid: int, wid: int, label: str) -> dict:
        rec = self.snapshot(pid, wid)
        for el in rec.get('elements', []):
            if label.lower() in (el.get('label') or '').lower():
                token = el.get('element_token')
                if token:
                    return self.call('click', {'pid': pid, 'window_id': wid,
                                               'element_token': token})
        return {'effect': 'refused', 'reason': f'AX label not found: {label}'}


def run(gui: Path, project: Path, out: Path, driver: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    cua = Cua(driver)
    log = (out / 'gui.log').open('w')
    proc = subprocess.Popen([str(gui), str(project)], stdout=log, stderr=log)
    try:
        target = None
        for _ in range(20):
            target = cua.find_window()
            if target:
                break
            time.sleep(0.5)
        if not target:
            return {'status': 'BLOCKED', 'reason': 'GUI window not found in CUA tree'}
        pid, wid = target
        results = []
        for step in STEPS:
            name = step['name']
            before = cua.snapshot(pid, wid)
            bw, bh = int(before.get('screenshot_width', REF_W)), int(
                before.get('screenshot_height', REF_H))
            if step['kind'] == 'ax':
                click_at = None
                clicked = cua.click_label(pid, wid, step['label'])
            else:
                click_at = scaled(step['x'], step['y'], bw, bh)
                clicked = cua.click(pid, wid, *click_at)
            time.sleep(0.5)
            # Capture the state the click produced BEFORE any dismissal key:
            # a modal that is closed again would otherwise look like a no-op.
            after_click = cua.snapshot(pid, wid, out / f'{name}.png')
            changed = (before.get('screenshot_sha256') !=
                       after_click.get('screenshot_sha256'))
            if step.get('escape'):
                cua.key(pid, wid, 'escape')
                time.sleep(0.3)
            results.append({
                'step': name, 'kind': step['kind'], 'click': click_at,
                'label': step.get('label'),
                'before_sha256': before.get('screenshot_sha256'),
                'after_sha256': after_click.get('screenshot_sha256'),
                'screenshot_changed': changed,
                'click_effect': clicked.get('effect'),
                'ax_elements': after_click.get('element_count'),
                'status': 'PASS' if changed else 'FAIL',
            })
            print(f"{name:20} click={click_at or step.get('label')} "
                  f"changed={changed} effect={clicked.get('effect')}", flush=True)
        return {'status': 'DONE', 'pid': pid, 'window_id': wid, 'steps': results}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--gui', required=True, type=Path)
    ap.add_argument('--project', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--driver', type=Path, default=Path(
        os.environ.get('CUA_DRIVER',
                       '/Applications/CuaDriver.app/Contents/MacOS/cua-driver')))
    args = ap.parse_args(argv)
    if not args.driver.is_file():
        print(f'CUA driver unavailable: {args.driver}', file=sys.stderr)
        return 3
    report = run(args.gui, args.project, args.out, args.driver)
    (args.out / 'cua-results.json').write_text(json.dumps(
        {'schema': 1, **report}, indent=2))
    fails = [s for s in report.get('steps', []) if s['status'] == 'FAIL']
    print(f"{len(report.get('steps', [])) - len(fails)} passed, "
          f"{len(fails)} failed; evidence in {args.out}")
    return 1 if fails or report.get('status') != 'DONE' else 0


if __name__ == '__main__':
    raise SystemExit(main())
