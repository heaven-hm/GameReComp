"""Fail-closed evidence checks for the issue #27 EH frame fix.

Every log and frame here is synthetic and lives only in tmp_path.  The parser
mirrors the real status grammar of ``runtime/win32hle/dmd_throw_selftest.c``
(``ok <name>`` / ``FAIL <name>``) and never trusts a claimed PASS, a claimed
frame, or a self-reported count.  No game is executed and no frame is claimed.

The measured gr.exe identity is recorded as a label only; it is not an oracle.

Run with: python3 -m pytest tests/test_eh27_frame_proof.py -q
"""
from __future__ import annotations

import hashlib
import json
import struct
import re

import pytest

from tools.frame_proof import bmp24

EXE_SHA = "6ea800ee918547ca1953a24bebdcd07c29cef4ada617d7321a7dc3701e825636"
CATCH_VA = "0x0040DC96"
CATCH_OWNER_TABLE = "0x0044BAF8"
WRONG_FRAME = "0x00419CC0"

SUMMARY = "dmd_throw_selftest: all checks passed"
NO_FRAME = "present NOT_TESTED; no frame claimed"

# Exact check names printed by the extended selftest.  A clean log must carry
# every one of them; only the shared object-binding checks may repeat (one per
# successful catch path).
REQUIRED_CHECKS = (
    "inline catch sees the ClassInfo-bound object",
    "exact frame: resumed at the inline catch",
    "exact frame: dispatch reported",
    "exact frame: guest EBP/ESP restored to the catch frame",
    "catch funclet sees the ClassInfo-bound object",
    "catch-owning frame: resumed through the owner's landing pad",
    "catch-owning frame: reached the lifted catch funclet",
    "catch-owning frame: dispatch reported the owner path",
    "catch-owning frame: guest EBP/ESP restored to the catch frame",
    "inlined frame: resumed at the lifted catch funclet",
    "inlined frame: dispatch reported the inner-checkpoint path",
    "inlined frame: guest EBP/ESP restored to the catch frame",
    "no checkpoint: explicit failure with a precise reason",
)
REPEATABLE = {"catch funclet sees the ClassInfo-bound object"}
# This ok() is deliberately false: it prints only when a caught throw wrongly
# returns to hle_eh_d_throw's caller.  Its presence is a failure.
NEGATIVE_CHECK = "hle_eh_d_throw must not return on a caught throw"

_FRAME_CLAIM = re.compile(
    r"\b(present(ed|s)?\s+(PASS|OK)|captured\s+\d+\s+frame|frame\s+PASS|"
    r"SDL_GL_SwapBuffers\s+called)\b", re.IGNORECASE)
_STATUS = re.compile(r"^(ok|FAIL|BUILD FAIL|skip)[ \t]+(\S.*)$")


def clean_log() -> str:
    lines = []
    for name in REQUIRED_CHECKS:
        # The shared catch-funclet check runs on both inlined paths.
        times = 2 if name in REPEATABLE else 1
        lines.extend([f"ok    {name}"] * times)
    lines += [NO_FRAME, SUMMARY]
    return "\n".join(lines) + "\n"


def parse_log(text: str) -> dict:
    """Re-derive events, counts and claims from raw selftest output."""
    events, frame_claims = [], []
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
    reasons = []
    names = [e["name"] for e in parsed["events"]]
    for name in REQUIRED_CHECKS:
        need = 2 if name in REPEATABLE else 1
        if names.count(name) < need:
            reasons.append("MISSING_REQUIRED_CHECK")
            break
    if NEGATIVE_CHECK in names:
        reasons.append("CAUGHT_THROW_RETURNED")
    if parsed["counts"]["FAIL"] or parsed["counts"]["BUILD FAIL"]:
        reasons.append("CHECK_FAILED")
    for name in REPEATABLE:
        if names.count(name) > 2:
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
        "catch_va": CATCH_VA,
        "catch_owner_table": CATCH_OWNER_TABLE,
        "wrong_frame_in_unfixed_build": WRONG_FRAME,
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


def make_bmp24(pixels: list[list[tuple[int, int, int]]]) -> bytes:
    height, width = len(pixels), len(pixels[0])
    stride = ((width * 3 + 3) // 4) * 4
    data = bytearray(54 + stride * height)
    data[:2] = b"BM"
    struct.pack_into("<I", data, 10, 54)
    struct.pack_into("<I", data, 14, 40)
    struct.pack_into("<ii", data, 18, width, height)
    struct.pack_into("<H", data, 26, 1)
    struct.pack_into("<H", data, 28, 24)
    for file_row, image_row in enumerate(reversed(pixels)):
        off = 54 + file_row * stride
        for x, (red, green, blue) in enumerate(image_row):
            data[off + x * 3:off + x * 3 + 3] = bytes((blue, green, red))
    return bytes(data)


NONBLANK = [[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (255, 255, 255)]]


class TestEH27FrameEvidence:
    def test_clean_log_is_clean_but_never_a_game_pass(self):
        record = make_record(clean_log())
        assert record["verdict"] == "CLEAN"
        assert record["reasons"] == []
        assert record["game_verdict"] == "NOT TESTED"
        assert record["counts"] == {"ok": len(REQUIRED_CHECKS) + 1,
                                    "FAIL": 0, "BUILD FAIL": 0, "skip": 0}

    def test_missing_any_case_blocks(self):
        for name in REQUIRED_CHECKS:
            text = clean_log().replace(f"ok    {name}\n", "", 1)
            record = make_record(text)
            assert record["verdict"] == "BLOCKED", name
            assert "MISSING_REQUIRED_CHECK" in record["reasons"]

    def test_failed_check_blocks(self):
        text = clean_log().replace(
            "ok    catch-owning frame: reached the lifted catch funclet",
            "FAIL  catch-owning frame: reached the lifted catch funclet")
        record = make_record(text)
        assert record["verdict"] == "BLOCKED"
        assert "CHECK_FAILED" in record["reasons"]

    def test_caught_throw_returning_is_a_failure_even_with_a_clean_summary(self):
        text = clean_log() + f"FAIL  {NEGATIVE_CHECK}\n"
        record = make_record(text)
        assert record["verdict"] == "BLOCKED"
        assert "CAUGHT_THROW_RETURNED" in record["reasons"]

    def test_summary_and_no_frame_declaration_are_required(self):
        for line in (SUMMARY, NO_FRAME):
            record = make_record(clean_log().replace(line + "\n", ""))
            assert record["verdict"] == "BLOCKED"
        assert "SUMMARY_MISSING" in make_record(
            clean_log().replace(SUMMARY + "\n", ""))["reasons"]
        assert "NO_FRAME_DECLARATION_MISSING" in make_record(
            clean_log().replace(NO_FRAME + "\n", ""))["reasons"]

    @pytest.mark.parametrize("claim", [
        "present PASS", "captured 1 frame", "frame PASS", "SDL_GL_SwapBuffers called",
    ])
    def test_frame_claim_is_refused(self, claim):
        record = make_record(clean_log() + claim + "\n")
        assert record["verdict"] == "BLOCKED"
        assert "FRAME_CLAIM_REFUSED" in record["reasons"]
        assert record["game_verdict"] == "NOT TESTED"

    def test_output_is_byte_identical_for_the_same_log(self):
        assert encode(make_record(clean_log())) == encode(make_record(clean_log()))

    def test_log_bytes_are_hashed_not_trusted(self):
        record = make_record(clean_log())
        assert record["source"]["sha256"] == hashlib.sha256(
            clean_log().encode()).hexdigest()
        tampered = clean_log().replace("exact frame", "exact frane")
        assert make_record(tampered)["verdict"] == "BLOCKED"

    def test_frame_proof_refuses_blank_and_accepts_real_pixels(self, tmp_path):
        blank = tmp_path / "blank.bmp"
        blank.write_bytes(make_bmp24([[(7, 7, 7)] * 2 for _ in range(2)]))
        real = tmp_path / "real.bmp"
        real.write_bytes(make_bmp24(NONBLANK))
        assert bmp24(blank)["blank"] is True
        info = bmp24(real)
        assert info["blank"] is False and info["distinct_colors"] == 4
        assert len(info["sha256"]) == 64
