#!/usr/bin/env python3
"""Verify the repository's proof artifacts and print an honest status summary.

The checker never upgrades a status: a screenshot proves pixels were captured,
a frame-proof JSON proves those pixels were non-blank, and a hybrid JSON records
what Frida/Unicorn actually managed. Absent or inconsistent artifacts are listed
as gaps, not silently treated as passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REQUIRED_TOOLS = ["tools/frida_probe.py", "tools/hybrid_diff.py",
                  "tools/input_replay_diff.py"]
SCREENSHOTS = ["evidence/screenshots/" + n for n in (
    "cavestory-native-01.png", "cavestory-native-02.png",
    "cavestory-binary-01.png", "tetris-oucc-new-01.png",
    "tetris-oucc-new-02.png")]
EVIDENCE = ["evidence/README.md", "reports/frames/cavestory/frame-proof.json",
            "reports/frames/tetris-new/frame-proof.json", "reports/hybrid-tetris.json",
            "reports/oracle/base64-oracle.json", "reports/oracle/arith-calc-oracle.json"]


def check(root: Path) -> dict:
    root = Path(root)
    rows = []
    for rel in REQUIRED_TOOLS + SCREENSHOTS + EVIDENCE:
        p = root / rel
        item = {"path": rel, "present": p.is_file()}
        if item["present"]:
            data = p.read_bytes()
            item["bytes"] = len(data)
            item["sha256"] = hashlib.sha256(data).hexdigest()
            if rel.endswith(".json"):
                try:
                    doc = json.loads(data)
                    item["json_ok"] = True
                    if rel == "reports/hybrid-tetris.json":
                        item["hybrid_status"] = doc.get("status")
                except ValueError:
                    item["json_ok"] = False
        rows.append(item)

    screenshots = [r for r in rows if r["path"].endswith(".png")]
    frame_proofs = [r for r in rows if "frame-proof" in r["path"]]
    hybrid = next((r for r in rows if r["path"] == "reports/hybrid-tetris.json"), None)
    gaps = [r["path"] for r in rows if not r["present"]]
    gaps += [r["path"] for r in frame_proofs if not r.get("json_ok", False)]
    return {
        "schema": 1,
        "counts": {"checked": len(rows), "present": sum(1 for r in rows if r["present"]),
                   "screenshots": len(screenshots)},
        "hybrid_status": (hybrid or {}).get("hybrid_status", "MISSING"),
        "gaps": gaps,
        "classification": "EVIDENCE PRESENT - not 100% compatibility; see evidence/README.md",
        "rows": rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    doc = check(args.root)
    text = json.dumps(doc, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(f"checked {doc['counts']['checked']} artifacts, "
          f"{doc['counts']['present']} present, {len(doc['gaps'])} gaps; "
          f"hybrid={doc['hybrid_status']}")
    for gap in doc["gaps"]:
        print(f"  GAP {gap}")
    return 0 if not doc["gaps"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
