"""Guard the #131/#134 Linux-host acceptance evidence."""
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DOC=ROOT/"docs/ISSUE_131_134_PROOF.md"
CITED=(".github/workflows/linux-evidence.yml","docs/feasibility/X64_LIFTER_GATE.md",
       "tools/win64_gate_summary.py","tests/test_linux_evidence_workflow.py")

def test_evidence_is_cited_and_present():
    t=DOC.read_text()
    for rel in CITED:
        assert rel in t, rel
        assert (ROOT/rel).exists(), rel
    assert "45677290863" in t
    assert "conclusion: success" in t
    assert "build_rc: 0" in t and "run_rc: 3" in t
    assert "NO-GO / DEFERRED" in (ROOT/"docs/feasibility/X64_LIFTER_GATE.md").read_text()

def test_no_fake_claim():
    t=DOC.read_text()
    assert "gameplay equivalence" in t.lower()
    assert "100% pass" not in t.lower()
    assert re.search(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?)\s+(?:issue|#)\s*13[14]\b",t) is None
