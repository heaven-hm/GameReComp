"""Guard the issue #3 evidence-based no-additional-code decision."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/ISSUE3_DECISION.md"
PATHS = (
    "runtime/win32hle/dinput.c", "tools/real_input_pump_proof.py",
    "tools/issue25_evidence.py", "tools/graphics_defer_matrix.py",
    "docs/DECOMPILER_DECISION.md", "docs/DECOMP_REUSE_DECISION.md",
    "docs/ISSUE_28_PROOF.md", "docs/FRIDA_RESIGN_ATTACH_EVIDENCE.md",
)

def test_decision_cites_real_evidence():
    text = DOC.read_text()
    assert "NO_ADDITIONAL_CODE_JUSTIFIED" in text
    for rel in PATHS:
        assert rel in text, rel
        assert (ROOT / rel).is_file(), rel

def test_no_fake_claim_or_auto_close():
    text = DOC.read_text()
    low = text.lower()
    assert "gameplay equivalence" in low
    assert "no 100% pass" in low
    assert re.search(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?)\s+(?:issue|#)\s*3\b", text) is None
