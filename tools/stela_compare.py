#!/usr/bin/env python3
"""Compare two stela_bench JSON results with percentages from measurements."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def pct(before: float, after: float) -> float:
    return round((after - before) * 100.0 / before, 2) if before else 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--before', required=True, type=Path)
    ap.add_argument('--after', required=True, type=Path)
    ap.add_argument('--out', type=Path)
    a = ap.parse_args(argv)
    b = json.loads(a.before.read_text())
    n = json.loads(a.after.read_text())
    keys = ('wall_seconds', 'generated_source_bytes', 'binary_bytes',
            'compile_seconds')
    rows = []
    for k in keys:
        x, y = b.get(k), n.get(k)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            rows.append({'metric': k, 'before': x, 'after': y,
                         'delta_percent': pct(float(x), float(y))})
    before_stages = b.get('stages_seconds', {})
    after_stages = n.get('stages_seconds', {})
    for stage, before_secs in before_stages.items():
        after_secs = after_stages.get(stage)
        if isinstance(before_secs, (int, float)) and isinstance(after_secs, (int, float)):
            rows.append({'metric': f'stage:{stage}', 'before': before_secs,
                         'after': after_secs,
                         'delta_percent': pct(float(before_secs), float(after_secs))})
    result = {'before': str(a.before), 'after': str(a.after),
              'same_input': b.get('input_sha256') == n.get('input_sha256'),
              'rows': rows}
    text = json.dumps(result, indent=2)
    if a.out:
        a.out.write_text(text)
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
