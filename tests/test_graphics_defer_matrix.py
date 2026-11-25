import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from tools.graphics_defer_matrix import decide

def test_defer_without_real_fixtures():
    r=decide({"importmentions_only":True})
    assert r["verdict"]=="DEFER" and "NO_RENDERER_FIXTURES" in r["reasons"]

def test_no_fake_render_pass():
    r=decide({})
    assert r["verdict"]=="DEFER" and r["dual_engine_match"] is None

def test_real_local_evidence_is_present():
    p=ROOT/"proof/graphics/d3d10-defer-evidence.json"
    d=json.loads(p.read_text())
    assert d["verdict"]=="DEFER"
    assert "NO_RENDERER_FIXTURES" in d["reasons"]
    assert "100% pass" not in p.read_text().lower()
