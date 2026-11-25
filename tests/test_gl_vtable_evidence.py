"""Deterministic evidence checks for runtime/win32hle/gl_vtable_selftest.c.

Every log here is synthetic and lives only in tmp_path.  Nothing runs the game,
claims a presented frame, or treats a selftest as game acceptance.  The parser
mirrors the real selftest's status grammar (``ok    name`` / ``FAIL  name``)
and is deliberately fail-closed: a missing check, a summary that is not the
selftest's own, or any frame/PASS claim blocks the verdict.

Run with: python3 -m pytest tests/test_gl_vtable_evidence.py -q
"""
from __future__ import annotations

import hashlib
import json
import re

# Exact checks printed by gl_vtable_selftest.c.  The names are the ABI
# contract: object -> [object+0x74] -> vptr -> [vptr+0x58] -> guest method.
REQUIRED_CHECKS = (
    "valid chain resolves the guest method",
    "null interface is named, not a generic null target",
    "failed validation never invents a target",
    "null vtable is a distinct named defect",
    "null method slot is a distinct named defect",
    "null object is refused before any read",
    "a NULL target pointer argument is tolerated",
)
SUMMARY = "gl_vtable_selftest: all checks passed"
NO_FRAME = "present NOT_TESTED; no frame claimed"

# The measured gr.exe identity and the exact null-interface call site.  These
# are labels for the evidence, not a claim that a frame was presented.
EXE_SHA = "6ea800ee918547ca1953a24bebdcd07c29cef4ada617d7321a7dc3701e825636"
NULL_CALL_SITES = (0x0040942C, 0x00409324, 0x00419D38)
BLOCKER = (
    "DMD OpenEx(gr.prf) catch resumed in guest frame 0x00419CC0 instead of "
    "the catch-owning frame; gameManager.init() was skipped, so "
    "GameManager+0x74 (InGameState) stayed NULL"
)

_STATUS = re.compile(r"^(ok|FAIL|BUILD FAIL|skip)\s{2,}(\S.*)$")
_FRAME_CLAIM = re.compile(
    r"\b(present(ed|s)?\s+(PASS|OK)|captured\s+\d+\s+frame|frame\s+PASS|"
    r"SDL_GL_SwapBuffers\s+called)\b", re.IGNORECASE)


def clean_log() -> str:
    lines = [f"ok    {name}" for name in REQUIRED_CHECKS]
    lines += [NO_FRAME, SUMMARY]
    return "\n".join(lines) + "\n"


def parse_log(text: str) -> dict:
    """Re-derive events and counts from raw selftest output; never trust a claim."""
    events: list[dict] = []
    frame_claims: list[str] = []
    summary = False
    for number, line in enumerate(text.splitlines(), 1):
        if _FRAME_CLAIM.search(line):
            frame_claims.append({"line": number, "text": line})
        if line == SUMMARY:
            summary = True
        match = _STATUS.match(line)
        if match:
            events.append({"line": number, "status": match.group(1),
                           "name": match.group(2).strip()})
    counts = {status: sum(e["status"] == status for e in events)
              for status in ("ok", "FAIL", "BUILD FAIL", "skip")}
    return {"events": events, "counts": counts, "summary": summary,
            "frame_claims": frame_claims}


def make_record(text: str) -> dict:
    """Turn a selftest log into a verdict with a fail-closed reason list."""
    parsed = parse_log(text)
    reasons: list[str] = []
    names = [e["name"] for e in parsed["events"]]
    missing = [name for name in REQUIRED_CHECKS if name not in names]
    if missing:
        reasons.append("MISSING_REQUIRED_CHECK")
    if parsed["counts"]["FAIL"] or parsed["counts"]["BUILD FAIL"]:
        reasons.append("CHECK_FAILED")
    if len(names) != len(REQUIRED_CHECKS):
        reasons.append("EXTRA_OR_DUPLICATE_CHECK")
    if not parsed["summary"]:
        reasons.append("SUMMARY_MISSING")
    if parsed["frame_claims"]:
        reasons.append("FRAME_CLAIM_REFUSED")
    if NO_FRAME not in text.splitlines():
        reasons.append("NO_FRAME_DECLARATION_MISSING")
    verdict = "CLEAN" if not reasons else "BLOCKED"
    return {
        "schema_version": 1,
        "source": {"kind": "synthetic-selftest-log",
                   "sha256": hashlib.sha256(text.encode()).hexdigest()},
        "exe_sha256": EXE_SHA,
        "null_call_sites": [f"0x{va:08X}" for va in NULL_CALL_SITES],
        "blocker": BLOCKER,
        "counts": parsed["counts"],
        "events": parsed["events"],
        "frame_claims": parsed["frame_claims"],
        "reasons": sorted(set(reasons)),
        "verdict": verdict,
        "game_verdict": "NOT TESTED",
    }


def encode(record: dict) -> str:
    """Stable bytes: sorted keys, no timestamps, one trailing newline."""
    return json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


class TestSelftestEvidence:
    def test_clean_log_is_clean_but_never_a_game_pass(self):
        record = make_record(clean_log())
        assert record["verdict"] == "CLEAN"
        assert record["reasons"] == []
        assert record["game_verdict"] == "NOT TESTED"
        assert record["counts"] == {"ok": len(REQUIRED_CHECKS), "FAIL": 0,
                                    "BUILD FAIL": 0, "skip": 0}
        assert record["frame_claims"] == []
        assert record["exe_sha256"] == EXE_SHA
        assert "0x00419D38" in record["null_call_sites"]

    def test_output_is_byte_identical_for_the_same_log(self):
        a, b = clean_log(), clean_log()
        assert encode(make_record(a)) == encode(make_record(b))
        assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}",
                             encode(make_record(a)))

    def test_crlf_does_not_change_the_verdict_or_events(self):
        unix = make_record(clean_log())
        windows = make_record(clean_log().replace("\n", "\r\n"))
        assert windows["verdict"] == "CLEAN"
        assert [(e["name"], e["status"]) for e in unix["events"]] == [
            (e["name"], e["status"]) for e in windows["events"]]
        assert windows["source"]["sha256"] != unix["source"]["sha256"]

    def test_one_failed_check_blocks(self):
        lines = clean_log().splitlines()
        lines[2] = lines[2].replace("ok    ", "FAIL  ", 1)
        record = make_record("\n".join(lines) + "\n")
        assert record["verdict"] == "BLOCKED"
        assert "CHECK_FAILED" in record["reasons"]

    def test_missing_required_check_blocks(self):
        lines = [line for line in clean_log().splitlines()
                 if "null vtable is a distinct named defect" not in line]
        record = make_record("\n".join(lines) + "\n")
        assert record["verdict"] == "BLOCKED"
        assert "MISSING_REQUIRED_CHECK" in record["reasons"]

    def test_duplicate_check_is_not_a_pass(self):
        lines = clean_log().splitlines()
        lines.insert(1, lines[0])
        record = make_record("\n".join(lines) + "\n")
        assert record["verdict"] == "BLOCKED"
        assert "EXTRA_OR_DUPLICATE_CHECK" in record["reasons"]

    def test_missing_summary_blocks(self):
        lines = [line for line in clean_log().splitlines() if line != SUMMARY]
        record = make_record("\n".join(lines) + "\n")
        assert record["verdict"] == "BLOCKED"
        assert "SUMMARY_MISSING" in record["reasons"]

    def test_missing_no_frame_declaration_blocks(self):
        lines = [line for line in clean_log().splitlines() if line != NO_FRAME]
        record = make_record("\n".join(lines) + "\n")
        assert record["verdict"] == "BLOCKED"
        assert "NO_FRAME_DECLARATION_MISSING" in record["reasons"]

    def test_frame_or_pass_claim_is_refused(self):
        for claim in ("present PASS", "presented OK", "captured 1 frame",
                      "frame PASS", "SDL_GL_SwapBuffers called"):
            record = make_record(clean_log() + claim + "\n")
            assert record["verdict"] == "BLOCKED", claim
            assert "FRAME_CLAIM_REFUSED" in record["reasons"], claim
            assert record["game_verdict"] == "NOT TESTED"

    def test_summary_claim_without_checks_is_not_accepted(self):
        record = make_record(SUMMARY + "\n" + NO_FRAME + "\n")
        assert record["verdict"] == "BLOCKED"
        assert {"MISSING_REQUIRED_CHECK", "EXTRA_OR_DUPLICATE_CHECK"} <= set(
            record["reasons"])

    def test_log_hash_is_rederived_not_believed(self):
        record = make_record(clean_log())
        assert record["source"]["sha256"] == hashlib.sha256(
            clean_log().encode()).hexdigest()
        tampered = dict(record)
        tampered["source"] = {"kind": "synthetic-selftest-log",
                              "sha256": "00" * 32}
        assert tampered["source"]["sha256"] != make_record(clean_log())[
            "source"]["sha256"]

    def test_blocker_is_carried_verbatim(self):
        record = make_record("")
        assert record["blocker"] == BLOCKER
        assert "catch resumed in guest frame 0x00419CC0" in record["blocker"]
        assert "InGameState" in record["blocker"]
