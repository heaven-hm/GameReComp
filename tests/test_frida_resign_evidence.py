"""Guard for the Frida re-sign attach evidence."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.frida_resign_evidence import verify  # noqa: E402


def _record(**overrides):
    rec = {
        "pid": 33355, "version": "17.24.3", "attach": "OK",
        "payload": [{"op": "modules", "names": ["choc-copy", "Cocoa"],
                     "arch": "arm64", "platform": "darwin"}],
    }
    rec.update(overrides)
    return rec


def test_real_record_is_accepted(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(_record()))
    b = tmp_path / "bin"
    b.write_bytes(b"real binary bytes")
    result = verify(p, b)
    assert result["verdict"] == "ATTACH_PROVEN"
    assert len(result["target_sha256"]) == 64
    assert result["dual_engine_match"] is None


def test_synthetic_and_tampered_records_are_refused(tmp_path):
    b = tmp_path / "bin"
    b.write_bytes(b"x")
    cases = [
        _record(attach="FAIL"),
        _record(version=""),
        _record(payload=[]),
        _record(payload=[{"op": "modules", "names": [], "arch": "arm64", "platform": "darwin"}]),
        _record(payload=[{"op": "modules", "names": ["x"], "arch": "x64", "platform": "darwin"}]),
    ]
    for i, rec in enumerate(cases):
        p = tmp_path / f"r{i}.json"
        p.write_text(json.dumps(rec))
        assert verify(p, b)["verdict"] == "BLOCKED"


def test_documented_real_run_is_present():
    from pathlib import Path as P
    p = P(ROOT / "proof/frida/attach-success.json")
    d = json.loads(p.read_text())
    assert d["attach"] == "OK"
    assert d["version"] == "17.24.3"
    assert len(d["payload"][0]["names"]) >= 5
    doc = (ROOT / "docs/FRIDA_RESIGN_ATTACH_EVIDENCE.md").read_text().lower()
    assert "get-task-allow" in doc
    assert "security authorizationdb" in doc
    assert "dual-engine" in doc
    assert "100% pass" not in doc


def test_cli_exit_codes(tmp_path):
    b = tmp_path / "bin"
    b.write_bytes(b"x")
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_record()))
    out = tmp_path / "out.json"
    r = subprocess.run([sys.executable, str(ROOT / "tools/frida_resign_evidence.py"),
                        "--record", str(good), "--binary", str(b),
                        "--json-out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0
    assert json.loads(out.read_text())["verdict"] == "ATTACH_PROVEN"
