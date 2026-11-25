"""Guard the issue #6 evidence-based DEFERRED decision."""
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DOC=ROOT/"docs/LEGACY_D3D_DEFER.md"
PATHS=("reports/games/game-target-issues-15-24.json","docs/GRAPHICS_DEFER_MATRIX.md",
       "runtime/win32hle/ddraw.c","runtime/win32hle/d3d7.c","evidence/corpus")
def test_decision_cites_real_evidence():
    text=DOC.read_text()
    assert "DEFERRED" in text
    for rel in PATHS:
        assert rel in text, rel
        assert (ROOT/rel).exists(), rel
def test_no_fake_claim():
    text=DOC.read_text()
    assert "not a supported claim" in text
    assert "100% pass" not in text.lower()
    assert re.search(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?)\s+(?:issue|#)\s*6\b",text) is None
