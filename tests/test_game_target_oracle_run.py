"""Fail-closed tests for the game-target original-vs-native oracle CLI.

No game, Wine process, CUA driver or screen recorder is started: an injectable
fake harness supplies run outcomes.  The tests prove that blank/fabricated
captures cannot PASS, that only real non-blank frames with matching delivered
input hashes can PASS, and that byte tampering is detected by the independent
verifier.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import acceptance_matrix as am  # noqa: E402
from tools import game_target_oracle_run as gto  # noqa: E402

HARNESS_SPEC = f"{__name__}:FakeHarness"
INPUT = b"scenario: mission-1, seed=42\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_png(width: int, height: int, colour) -> bytes:
    """Build a valid 8-bit RGB PNG from a ``colour(x, y)`` function."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        for x in range(width):
            raw.extend(colour(x, y))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


CHECKER_A = make_png(64, 64, lambda x, y: (255, 0, 0) if (x // 8 + y // 8) % 2 == 0
                     else (0, 0, 255))
CHECKER_B = make_png(64, 64, lambda x, y: (0, 255, 0) if (x // 8 + y // 8) % 2 == 0
                     else (255, 255, 0))
BLANK = make_png(64, 64, lambda x, y: (0, 0, 0))


class FakeHarness:
    """Configurable fake; class attributes are reset before every test."""

    healthy = True
    mode = "genuine"
    requests: list = []

    def probe(self, bundle):
        FakeHarness.requests.append(("probe", bundle))
        return self.healthy, "ok" if self.healthy else "fake health failure"

    def _frames(self, request):
        native_side = request.role == "native"
        if self.mode == "blank":
            blobs = [BLANK]
        elif self.mode == "native_blank" and native_side:
            blobs = [BLANK]
        elif self.mode == "low_similarity" and native_side:
            blobs = [CHECKER_B]
        else:
            # A genuine pair delivers the *same* declared scenario frames.
            blobs = [CHECKER_A]
        paths = []
        for index, blob in enumerate(blobs):
            path = request.artifacts_dir / f"fake-{request.role}-{index}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
            if self.mode == "missing_frame":
                path = request.artifacts_dir / f"absent-{request.role}-{index}.png"
            paths.append(gto.FrameArtifact(path=path, source="fake"))
        return paths

    def run(self, request):
        FakeHarness.requests.append(("run", request))
        delivered = sha(request.input_bytes) if self.mode in ("genuine", "low_similarity") \
            else None
        if self.mode == "input_mismatch" and request.role == "native":
            delivered = sha(b"different input")
        return gto.RunOutcome(
            command=[str(request.binary)], exit_code=0, stdout=b"run ok\n",
            stderr=b"", frames=self._frames(request),
            input_delivered_sha256=delivered,
            witness={"capture_backend": "fake", "window_title_confirmed": True,
                     "declared_window_title": "fake-game"})


@pytest.fixture(autouse=True)
def reset_harness():
    FakeHarness.healthy = True
    FakeHarness.mode = "genuine"
    FakeHarness.requests = []
    yield


@pytest.fixture
def paths(tmp_path):
    original = tmp_path / "igi.exe"
    native = tmp_path / "igi-native"
    highball = tmp_path / "highball"
    input_file = tmp_path / "replay.bin"
    original.write_bytes(b"MZ original windows game")
    native.write_bytes(b"\x7fELF native recompilation")
    highball.write_bytes(b"#!/bin/sh\nexit 0\n")
    input_file.write_bytes(INPUT)
    return {"original": original, "native": native, "highball": highball,
            "input": input_file, "out": tmp_path / "oracle.json"}


def invoke(paths, **overrides):
    base = {
        "game_id": "igi", "issue": [15], "original": str(paths["original"]),
        "native": str(paths["native"]), "out": str(paths["out"]),
        "input_file": str(paths["input"]), "replay_input": True,
        "highball": str(paths["highball"]), "harness": HARNESS_SPEC,
        "min_similarity": 0.85,
    }
    base.update(overrides)
    argv = []
    for key, value in base.items():
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                argv += [f"--{key.replace('_', '-')}", str(item)]
        elif value is True:
            argv.append(f"--{key.replace('_', '-')}")
        else:
            argv += [f"--{key.replace('_', '-')}", str(value)]
    return gto.main(argv)


def record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def detail_for(rec: dict) -> dict:
    return json.loads(Path(rec["evidence_path"]).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Byte-level frame analysis: no claim is trusted
# ---------------------------------------------------------------------------

def test_real_png_is_decoded_and_not_blank():
    info = gto.analyze_frame(CHECKER_A)
    assert info["format"] == "png8"
    assert (info["width"], info["height"]) == (64, 64)
    assert info["blank"] is False
    assert info["distinct_colors"] >= 2
    assert len(info["signature"]) == 64


def test_blank_png_is_reported_blank():
    assert gto.analyze_frame(BLANK)["blank"] is True


def test_fake_bytes_and_undecodable_frames_have_no_signature():
    assert "error" in gto.analyze_frame(b"not-a-frame")
    assert "error" in gto.analyze_frame(b"\x89PNG\r\n\x1a\ntruncated")


def test_similarity_is_computed_from_decoded_pixels():
    assert gto.frame_similarity(gto.analyze_frame(CHECKER_A),
                                gto.analyze_frame(CHECKER_A)) == 1.0
    low = gto.frame_similarity(gto.analyze_frame(CHECKER_A),
                               gto.analyze_frame(CHECKER_B))
    assert low is not None and low < 0.85


# ---------------------------------------------------------------------------
# Verdict function: fail closed
# ---------------------------------------------------------------------------

def _side(frames, delivered=sha(INPUT), analysis=None):
    return {"role": "original", "executed": True, "exit_code": 0,
            "timed_out": False, "input_delivered_sha256": delivered,
            "witness": {"window_title_confirmed": True,
                        "declared_window_title": "fake-game"},
            "frames": [{"analysis": analysis or gto.analyze_frame(f),
                        "sha256": sha(f)} for f in frames]}


def test_no_native_is_partial_never_pass():
    verdict, reasons, comparison = gto.decide_verdict(
        _side([CHECKER_A]), None, sha(INPUT), 0.85)
    assert verdict == gto.VERDICT_PARTIAL
    assert any("MISSING_NATIVE_INPUT" in r for r in reasons)
    assert comparison["input_hashes_match"] is False


def test_no_original_frames_is_blocked():
    verdict, reasons, _ = gto.decide_verdict(
        _side([BLANK]), _side([CHECKER_A]), sha(INPUT), 0.85)
    assert verdict == gto.VERDICT_BLOCKED
    assert any("NO_ORIGINAL_FRAMES" in r for r in reasons)


def test_mismatched_input_hashes_cannot_pass():
    verdict, reasons, _ = gto.decide_verdict(
        _side([CHECKER_A]), _side([CHECKER_B], delivered=sha(b"other")),
        sha(INPUT), 0.85)
    assert verdict == gto.VERDICT_PARTIAL
    assert any("INPUT_HASH_MISMATCH" in r for r in reasons)


def test_unconfirmed_window_cannot_pass():
    original = _side([CHECKER_A])
    original["witness"] = {"window_title_confirmed": False}
    native = _side([CHECKER_A])
    native["witness"] = {"window_title_confirmed": False}
    verdict, reasons, _ = gto.decide_verdict(
        original, native, sha(INPUT), 0.85)
    assert verdict == gto.VERDICT_PARTIAL
    assert any("WINDOW_UNCONFIRMED" in r for r in reasons)


def test_missing_declared_input_cannot_pass():
    verdict, reasons, _ = gto.decide_verdict(
        _side([CHECKER_A]), _side([CHECKER_A]), None, 0.85)
    assert verdict == gto.VERDICT_PARTIAL
    assert any("NO_DECLARED_INPUT" in r for r in reasons)


def test_low_frame_similarity_cannot_pass():
    verdict, reasons, comparison = gto.decide_verdict(
        _side([CHECKER_A]), _side([CHECKER_B]), sha(INPUT), 0.85)
    assert verdict == gto.VERDICT_PARTIAL
    assert comparison["similarity_met"] is False
    assert any("SIMILARITY_BELOW_THRESHOLD" in r for r in reasons)


def test_genuine_matching_pair_passes():
    verdict, _reasons, comparison = gto.decide_verdict(
        _side([CHECKER_A]), _side([CHECKER_A]), sha(INPUT), 0.85)
    assert verdict == gto.VERDICT_PASS
    assert comparison["input_hashes_match"] is True
    assert comparison["similarity_met"] is True


# ---------------------------------------------------------------------------
# CLI: genuine run, fake captures, missing native
# ---------------------------------------------------------------------------

def test_genuine_fake_harness_pair_passes_and_verifies(paths, capsys):
    assert invoke(paths) == gto.EXIT_PASS
    out = capsys.readouterr().out
    assert "PASS:" in out
    rec = record(paths["out"])
    assert rec["status"] == gto.VERDICT_PASS
    assert rec["verification"] == "INDEPENDENTLY VERIFIED"
    assert rec["comparison"]["input_hashes_match"] is True
    assert rec["comparison"]["similarity"] == 1.0
    assert gto.verify_record(paths["out"]) == []
    assert gto.main(["--verify", str(paths["out"])]) == gto.EXIT_PASS
    assert rec["matrix_promotion"] is False
    assert "gameplay" in rec["not_claimed"]


def test_pass_record_cannot_promote_acceptance_matrix(paths):
    assert invoke(paths) == gto.EXIT_PASS
    entry = record(paths["out"])["records"][0]
    assert gto.ORACLE_LABEL not in am.REAL_ORACLES
    validated = am.validate_oracle_record(entry, base_dir=paths["out"].parent)
    assert validated["valid"] is False


def test_no_native_binary_is_partial_with_exact_reason(paths):
    assert invoke(paths, native=None) == gto.EXIT_PARTIAL
    rec = record(paths["out"])
    assert rec["status"] == gto.VERDICT_PARTIAL
    assert rec["native"] is None
    assert any("MISSING_NATIVE_INPUT" in r for r in rec["reasons"])
    assert gto.main(["--verify", str(paths["out"])]) == gto.EXIT_PARTIAL


def test_blank_captures_cannot_pass(paths):
    FakeHarness.mode = "blank"
    assert invoke(paths) == gto.EXIT_BLOCKED
    rec = record(paths["out"])
    assert rec["status"] == gto.VERDICT_BLOCKED
    assert any("NO_ORIGINAL_FRAMES" in r for r in rec["reasons"])
    assert rec["status"] == gto.VERDICT_BLOCKED
    assert gto.main(["--verify", str(paths["out"])]) == gto.EXIT_BLOCKED


def test_one_blank_side_cannot_pass(paths):
    FakeHarness.mode = "native_blank"
    assert invoke(paths) == gto.EXIT_PARTIAL
    rec = record(paths["out"])
    assert rec["status"] != gto.VERDICT_PASS
    assert any("NO_NATIVE_FRAMES" in r for r in rec["reasons"])


def test_fake_capture_pointing_to_absent_file_is_blocked(paths):
    FakeHarness.mode = "missing_frame"
    assert invoke(paths) == gto.EXIT_BLOCKED
    rec = record(paths["out"])
    assert rec["status"] == gto.VERDICT_BLOCKED
    assert "capture failed" in rec["reason"]
    assert rec["records"] == []


def test_input_mismatch_cannot_pass(paths):
    FakeHarness.mode = "input_mismatch"
    assert invoke(paths) == gto.EXIT_PARTIAL
    rec = record(paths["out"])
    assert any("INPUT_HASH_MISMATCH" in r for r in rec["reasons"])


def test_low_similarity_cannot_pass(paths):
    FakeHarness.mode = "low_similarity"
    assert invoke(paths) == gto.EXIT_PARTIAL
    rec = record(paths["out"])
    assert any("SIMILARITY_BELOW_THRESHOLD" in r for r in rec["reasons"])


def test_missing_original_is_blocked_before_any_run(paths):
    code = invoke(paths, original=str(paths["original"].parent / "missing.exe"))
    assert code == gto.EXIT_BLOCKED
    assert FakeHarness.requests == []
    assert record(paths["out"])["records"] == []


def test_unhealthy_harness_is_blocked(paths):
    FakeHarness.healthy = False
    assert invoke(paths) == gto.EXIT_BLOCKED
    assert record(paths["out"])["status"] == gto.VERDICT_BLOCKED


def test_bad_harness_spec_is_blocked(paths):
    assert invoke(paths, harness="not-a-spec") == gto.EXIT_BLOCKED
    assert "harness error" in record(paths["out"])["reason"]


def test_manifest_is_byte_stable(paths):
    assert invoke(paths) == gto.EXIT_PASS
    first = paths["out"].read_bytes()
    detail_bytes = Path(record(paths["out"])["evidence_path"]).read_bytes()
    assert invoke(paths) == gto.EXIT_PASS
    assert paths["out"].read_bytes() == first
    assert Path(record(paths["out"])["evidence_path"]).read_bytes() == detail_bytes


# ---------------------------------------------------------------------------
# Independent verification: tampering is detected
# ---------------------------------------------------------------------------

def test_tampered_original_frame_is_detected(paths):
    assert invoke(paths) == gto.EXIT_PASS
    rec = record(paths["out"])
    frame = Path(rec["artifacts_dir"]) / detail_for(rec)["original"]["frames"][0]["file"]
    frame.write_bytes(CHECKER_B)  # valid PNG, different real pixels
    problems = gto.verify_record(paths["out"])
    assert any("original frame 0 hash mismatch" in p for p in problems)
    assert any("signature disagrees" in p for p in problems)
    assert gto.main(["--verify", str(paths["out"])]) == gto.EXIT_TAMPERED


def test_tampered_native_frame_is_detected(paths):
    assert invoke(paths) == gto.EXIT_PASS
    rec = record(paths["out"])
    frame = Path(rec["artifacts_dir"]) / detail_for(rec)["native"]["frames"][0]["file"]
    frame.write_bytes(frame.read_bytes() + b"tampered")
    assert any("native frame 0 hash mismatch" in p
               for p in gto.verify_record(paths["out"]))


def test_tampered_stdout_and_binary_are_detected(paths):
    assert invoke(paths) == gto.EXIT_PASS
    rec = record(paths["out"])
    raw = Path(rec["artifacts_dir"]) / detail_for(rec)["original"]["raw"]["stdout"]["file"]
    raw.write_bytes(b"different output")
    problems = gto.verify_record(paths["out"])
    assert any("original stdout hash mismatch" in p for p in problems)
    paths["native"].write_bytes(b"tampered native")
    assert any("native binary hash mismatch" in p
               for p in gto.verify_record(paths["out"]))


def test_tampered_evidence_file_is_detected(paths):
    assert invoke(paths) == gto.EXIT_PASS
    evidence = Path(record(paths["out"])["evidence_path"])
    forged = json.loads(evidence.read_text(encoding="utf-8"))
    forged["game_id"] = "forged"
    evidence.write_text(json.dumps(forged, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    assert any("evidence hash mismatch" in p for p in gto.verify_record(paths["out"]))
    assert gto.main(["--verify", str(paths["out"])]) == gto.EXIT_TAMPERED


def test_forged_pass_claim_is_rejected(paths):
    """Editing the verdict and analysis cannot turn partial evidence into PASS."""
    assert invoke(paths, native=None) == gto.EXIT_PARTIAL
    rec = record(paths["out"])
    rec["status"] = gto.VERDICT_PASS
    rec["verification"] = "INDEPENDENTLY VERIFIED"
    rec["records"][0]["verdict"] = gto.VERDICT_PASS
    paths["out"].write_text(json.dumps(rec), encoding="utf-8")
    problems = gto.verify_record(paths["out"])
    assert any("verdict inconsistency" in p for p in problems)
    assert any("PASS requires both original and native sides" in p for p in problems)
    assert gto.main(["--verify", str(paths["out"])]) == gto.EXIT_TAMPERED


def test_forged_frame_analysis_cannot_pass(paths):
    """Claiming blank bytes are real frames in the detail file is detected."""
    FakeHarness.mode = "blank"
    assert invoke(paths) == gto.EXIT_BLOCKED
    rec = record(paths["out"])
    assert rec["status"] == gto.VERDICT_BLOCKED
    evidence = Path(rec["evidence_path"])
    forged = json.loads(evidence.read_text(encoding="utf-8"))
    fake_signature = gto.analyze_frame(CHECKER_A)["signature"]
    for role in ("original", "native"):
        for frame in forged[role]["frames"]:
            frame["analysis"]["blank"] = False
            frame["analysis"]["signature"] = fake_signature
    evidence.write_text(json.dumps(forged, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    new_sha = sha(evidence.read_bytes())
    rec["evidence_sha256"] = new_sha
    rec["records"][0]["evidence_sha256"] = new_sha
    paths["out"].write_text(json.dumps(rec), encoding="utf-8")
    problems = gto.verify_record(paths["out"])
    assert any("blank disagrees with bytes" in p for p in problems)
    assert any("verdict inconsistency" in p for p in problems)
    assert gto.main(["--verify", str(paths["out"])]) == gto.EXIT_TAMPERED


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
