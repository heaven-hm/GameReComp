#!/usr/bin/env python3
"""Run the per-target corpus probes in parallel and summarise exact counts."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_one(slug: str) -> tuple[str, dict | str]:
    try:
        p = subprocess.run([sys.executable, "-m", "tools.corpus_probe", slug],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=150)
        if p.returncode != 0:
            return slug, {"status": "PROBE_ERROR", "stderr": p.stderr[-500:]}
        return slug, json.loads(p.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return slug, "PROBE_TIMEOUT"
    except Exception as exc:
        return slug, f"{type(exc).__name__}: {exc}"


def main() -> int:
    data = json.loads((ROOT / "reports" / "corpus_verification.json").read_text())
    force = "--force" in sys.argv
    slugs = [t["slug"] for t in data["targets"]
             if t["native"]["valid"] and
             (force or not (ROOT / "evidence" / "corpus" / t["slug"] / "engines.json").is_file())]
    print(f"probing {len(slugs)} targets with verified native binaries", flush=True)
    results: dict[str, dict | str] = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(run_one, s): s for s in slugs}
        for i, fut in enumerate(as_completed(futs), 1):
            slug, rec = fut.result()
            results[slug] = rec
            if i % 10 == 0 or i == len(slugs):
                print(f"[{i}/{len(slugs)}] {time.time() - t0:.0f}s", flush=True)
    (ROOT / "reports" / "engine_crossos_results.json").write_text(
        json.dumps({"schema": 1, "count": len(results), "results": results},
                   indent=2, sort_keys=True) + "\n")
    for key in ("engines", "macos_to_windows", "linux_to_windows"):
        counts = Counter(str(r.get(key)) if isinstance(r, dict) else str(r)
                         for r in results.values())
        print(key, dict(counts), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
