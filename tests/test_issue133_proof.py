"""Guard issue #133 acceptance evidence."""
import json, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DOC=ROOT/"docs/ISSUE_133_PROOF.md"
CITED=("proof/issue-28/issue28-proof.json","docs/ISSUE_28_PROOF.md",
       "proof/ports/igi-native-port.json","docs/feasibility/X64_LIFTER_GATE.md",
       "tools/win64_gate_summary.py","tools/autorecomp/cli.py")

def test_acceptance_items_have_real_artifacts():
    text=DOC.read_text()
    for rel in CITED:
        assert rel in text, rel
        assert (ROOT/rel).is_file(), rel
    p=json.loads((ROOT/"proof/issue-28/issue28-proof.json").read_text())
    assert p["run"]["unresolved_imports"]==0
    assert p["run"]["frame_count"]==3==len(p["frames"])
    assert all(f["distinct_colors"]>1 and not f["blank"] for f in p["frames"])
    gate=(ROOT/"docs/feasibility/X64_LIFTER_GATE.md").read_text()
    assert "NO-GO / DEFERRED" in gate

def test_no_fake_success_claim():
    t=DOC.read_text().lower()
    assert "gameplay equivalence" in t
    assert "100% pass" not in t
    assert re.search(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?)\s+(?:issue|#)\s*133\b",DOC.read_text()) is None
