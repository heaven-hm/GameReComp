"""Proof validator must reject incomplete or inconsistent artifacts."""
import json
from pathlib import Path
from tools.validate_proof import validate

BASE = {
    "schema": 2, "kind": "matrix", "generated_at": "2026-11-25T00:00:00Z",
    "input": {"sha256": "a"}, "target": {"sha256": "b"},
    "commands": {"original": ["highball", "run", "x.exe"], "rebuilt": ["x"]},
    "scope": "one scripted scenario", "aggregate": "pass", "limits": "process only",
    "scenarios": [{"raw_original": "x", "raw_rebuilt": "x", "guest_normalized": "x\n",
                   "expected": {"exit_code": 0, "guest_stdout_sha256": "h"},
                   "actual": {"exit_code": 0, "guest_stdout_sha256": "h"}, "verdict": "pass"}],
}


def test_valid_artifact(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps(BASE))
    assert validate(p) == []


def test_missing_raw_capture_rejected(tmp_path):
    p = tmp_path / "p.json"
    d = json.loads(json.dumps(BASE))
    d["scenarios"][0].pop("raw_rebuilt")
    p.write_text(json.dumps(d))
    assert any("raw captures" in x for x in validate(p))
