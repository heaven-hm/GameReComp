#!/usr/bin/env python3
"""Fail-closed cost/compatibility matrix for issue #7 D3D9Ex/D3D10/11.

Reads only CLI-supplied evidence JSON and emits DEFER unless a real licensed
game scenario plus a versioned host renderer fixture are supplied. Never
emits a rendered-frame or gameplay PASS from registration or imports alone.
"""
import argparse, hashlib, json, sys
from pathlib import Path

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).is_file() else None

def decide(ev):
    if not isinstance(ev, dict):
        return {"verdict": "BLOCKED", "reasons": ["evidence is not an object"]}
    reasons = []
    fixtures = ev.get("renderer_fixtures") or []
    game = ev.get("licensed_game_scenario") or {}
    if not fixtures:
        reasons.append("NO_RENDERER_FIXTURES")
    if not game.get("path") or not game.get("sha256"):
        reasons.append("NO_LICENSED_GAME_SCENARIO")
    if game.get("sha256") and sha(game["path"]) != game["sha256"]:
        reasons.append("GAME_HASH_MISMATCH")
    if ev.get("importmentions_only") and not fixtures:
        reasons.append("IMPORT_MENTIONS_ARE_NOT_DEMAND")
    return {"verdict": "DEFER" if reasons else "REVIEW",
            "reasons": reasons, "dual_engine_match": None,
            "limits": "No universal D3D10/11 support or rendering PASS is claimed."}

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evidence", required=True, type=Path)
    ap.add_argument("--json-out", required=True, type=Path)
    a = ap.parse_args(argv)
    try:
        ev = json.loads(a.evidence.read_text())
    except Exception as e:
        print(json.dumps({"verdict": "BLOCKED", "reasons": [str(e)]})); return 2
    r = decide(ev)
    a.json_out.write_text(json.dumps(r, indent=2, sort_keys=True) + "\n")
    print(json.dumps(r, indent=2, sort_keys=True))
    return 0 if r["verdict"] == "DEFER" else 1

if __name__ == "__main__":
    sys.exit(main())
