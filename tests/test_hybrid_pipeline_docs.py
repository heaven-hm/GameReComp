"""Deterministic guardrails for docs/HYBRID_TEST_PIPELINE.md.

The document is checked as plain text: no network, no binaries, no fixtures and
no wall-clock behaviour. It must contain the required sections, the exact
commands for each adapter and the orchestrator, the evidence fields, the
fail-closed verdict rules and an honest BLOCKED example. It must not make an
unqualified 100% / all-games / all-OS claim, claim an issue was closed, or embed
a screenshot.

Run with: python3 -m pytest tests/test_hybrid_pipeline_docs.py -q
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "HYBRID_TEST_PIPELINE.md"

REQUIRED_SECTIONS = (
    "# Hybrid test pipeline",
    "## Purpose",
    "## Supported scope",
    "## Deferred and unsupported classes",
    "## Commands",
    "### Unicorn adapter",
    "### Frida adapter",
    "### Input adapter",
    "### Orchestrator",
    "## Evidence schema",
    "## Fail-closed verdict rules",
    "## Blocked run example",
    "## Limits",
    "## Verification",
)

# Exact substrings of the documented commands. These are the flags the checked-in
# CLIs actually accept; changing a command here must mean changing the doc too.
REQUIRED_COMMANDS = (
    "python3 -m tools.lift.difftest",
    "--project <project-dir> --pe <original.exe>",
    "--address 0x4010A0 --cases 8",
    "--json <evidence-dir>/difftest-per-address.json",
    "python3 -m tools.frida_probe --dry-run --hook ExitProcess --hook WriteFile",
    "python3 -m tools.frida_probe",
    "--spawn /path/to/highball run /path/to/Original.exe",
    "--hook KERNEL32.dll!ExitProcess --hook KERNEL32.dll!WriteFile",
    "--out <evidence-dir>/frida-calls.json",
    "python3 -m tools.input_scenario",
    "--scenario <scenario.json> --out <evidence-dir>/input-replay",
    "--pump mypackage.pump:make_pump --replays 2",
    "python3 -m tools.hybrid_diff",
    "--exe <original.exe> --native <native-binary> --input \"\"",
    "--function 0x4010A0 --seeds 1,2,3,4,5,6,7,8",
    "--live-frida --timeout 20",
    "--out <evidence-dir>/hybrid.json",
    "--process-level-match",
    "python3 -m tools.auto_verify",
    "--exe <original.exe> --native <name>=<native-binary>",
    "--stdin-file <real-input.bin> --hook WriteFile --hook ExitProcess",
    "--out <evidence-dir>/auto-verify",
    "--live-frida --capture-frames",
)

REQUIRED_SCOPE_TOKENS = (
    "PE32 x86 original -> host-native binary",
    "SUPPORTED_CANDIDATE",
    "DEFERRED (issue #13 gate not open)",
    "DEFERRED (issue #8 gate not open)",
    "UNSUPPORTED",
    "NOT TESTED",
    "no Linux runner",
    "no original-Windows oracle",
)

REQUIRED_SCHEMA_FIELDS = (
    "`exe`", "`status`", "`reason`", "`original_runner`",
    "`matched_engines`", "`blocked_engines`", "`diverged_engines`",
    "`claim`", "`note`", "`process_level_only`", "`artifacts`", "`sha256`",
    "`unicorn.status`", "`blocked_class`", "`function`", "`entry`",
    "`from_entry`", "`section`", "`cases`", "`seeds`", "`named`",
    "`distinct_eax`", "`nonvacuous`", "`signatures`",
    "`frida.status`", "`calls`", "`counts`", "`script_sha256`", "`runner`",
    "`timeout_s`", "`killed_on_timeout`", "`native.status`", "`original`",
    "`exit_code`", "`stdout_hash`", "`command`", "`stdin_bytes`", "`args`",
    "`hybrid_status`", "`frame_capture`", "`hooks`", "`timestamp`",
    "`schema`", "`count`", "`counts`", "`results`",
    "`scenario_sha256`", "`record_sha256s`", "`record_paths`",
    "`what_it_proves`", "`not_claimed`", "`consumed`", "`state`",
    "`screenshot`", "`distinct_colors`", "`blank`",
)

REQUIRED_VERDICT_TOKENS = (
    "`DIVERGED`", "`BLOCKED`", "`MATCH`", "`PASS`", "`fault`", "`environment`",
    "`nonvacuous: false`", "`blocked_class: fault`", "`blocked_class: environment`",
    "`--process-level-match`", "Divergence is never upgraded",
    "timed out", "Any engine", "unconsumed event", "no pump",
)

REQUIRED_BLOCKED_TOKENS = (
    "reports/hybrid-tetris.json",
    "tetris-win-32-en.exe",
    "0x004013D0",
    "UC_ERR_READ_UNMAPPED",
    "live attach disabled in bounded run (--live-frida available)",
    "bounded replay timed out",
    "No compatibility claim.",
)

# Unqualified perfection / universal-scope claims are forbidden. The same
# phrases are allowed when the sentence explicitly negates or refutes them.
UNIVERSAL_CLAIM = re.compile(
    r"100\s*%|\ball[- ]games?\b|\ball[- ]OS\b|\bevery game\b|\bevery OS\b|"
    r"\bcomplete compatibility\b",
    re.IGNORECASE,
)
NEGATION = re.compile(
    r"(?:\b(?:not|no|never|none|zero|without|cannot|n't|under|below|less|"
    r"rather|avoid|exclud\w*|refus\w*|deni\w*|non|counterexampl\w*|"
    r"refut\w*|disprov\w*|debunk\w*|impossible|unverifi\w*)\b)|<|!=",
    re.IGNORECASE,
)
ISSUE_CLOSED = re.compile(
    r"(?:\b(?:issue|issues|ticket|tickets)\b[^.\n]{0,50}"
    r"\b(?:closed|resolved|fixed|done|fully\s+complete)\b"
    r"|\b(?:closes?|fixes?|resolves?)\s+#?\d+\b"
    r"|\b#\d+\b[^.\n]{0,30}\b(?:is|are|was|were|has\s+been|have\s+been)\s+"
    r"(?:now\s+)?(?:closed|resolved|fixed|done)\b"
    r"|\bissues?[- ]closed\b)",
    re.IGNORECASE,
)
EMBEDDED_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)|<img\b", re.IGNORECASE)


def _read():
    assert DOC.is_file(), f"missing {DOC.relative_to(ROOT)}"
    return DOC.read_text(encoding="utf-8")


def _sentence(text, start, end):
    """Widen a match to its sentence so wrapped negation is visible."""
    left = start
    while left > 0:
        char = text[left - 1]
        if char in ".!?" or (char == "\n" and left > 1 and text[left - 2] == "\n"):
            break
        left -= 1
    right = end
    while right < len(text):
        char = text[right]
        if char in ".!?" or (char == "\n" and right + 1 < len(text)
                             and text[right + 1] == "\n"):
            break
        right += 1
    return text[left:right]


def _offending_lines(text, pattern):
    """Return lines whose match has no negation in the surrounding sentence."""
    found = []
    for match in pattern.finditer(text):
        probe = _sentence(text, match.start(), match.end())
        if NEGATION.search(probe):
            continue
        number = text.count("\n", 0, match.start()) + 1
        found.append((number, text.splitlines()[number - 1].strip()[:180]))
    return found


def test_doc_has_every_required_section():
    text = _read()
    missing = [section for section in REQUIRED_SECTIONS if section not in text]
    assert not missing, f"missing required sections: {missing}"


def test_doc_has_exact_adapter_and_orchestrator_commands():
    text = _read()
    missing = [command for command in REQUIRED_COMMANDS if command not in text]
    assert not missing, f"missing required command strings: {missing}"


def test_doc_states_supported_and_deferred_scope():
    text = _read()
    missing = [token for token in REQUIRED_SCOPE_TOKENS if token not in text]
    assert not missing, f"missing scope statements: {missing}"


def test_doc_documents_evidence_schema_fields():
    text = _read()
    missing = [field for field in REQUIRED_SCHEMA_FIELDS if field not in text]
    assert not missing, f"missing evidence schema fields: {missing}"


def test_doc_documents_fail_closed_verdict_rules():
    text = _read()
    missing = [token for token in REQUIRED_VERDICT_TOKENS if token not in text]
    assert not missing, f"missing verdict rule tokens: {missing}"


def test_doc_contains_honest_blocked_example():
    text = _read()
    missing = [token for token in REQUIRED_BLOCKED_TOKENS if token not in text]
    assert not missing, f"missing BLOCKED example tokens: {missing}"
    section = text.split("## Blocked run example", 1)[1].split("## Limits", 1)[0]
    assert '"status": "BLOCKED"' in section
    assert '"status": "MATCH"' not in section


def test_doc_has_no_unqualified_perfect_or_universal_claim():
    text = _read()
    bad = _offending_lines(text, UNIVERSAL_CLAIM)
    assert not bad, f"unqualified 100%/all-games/all-OS claim: {bad}"


def test_doc_does_not_claim_issue_closure():
    text = _read()
    bad = _offending_lines(text, ISSUE_CLOSED)
    assert not bad, f"issue-closure claim: {bad}"


def test_doc_embeds_no_screenshot():
    text = _read()
    assert not EMBEDDED_IMAGE.search(text), "document embeds an image/screenshot"
    assert "embeds no screenshots" in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
