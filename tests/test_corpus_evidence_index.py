"""Tests for tools.corpus_evidence_index: fail-closed, deterministic CLI index.

Run with: python3 -m pytest tests/test_corpus_evidence_index.py -q

Every case builds real files under tmp_path, writes honest corpus-shaped JSON,
then mutates either the bytes or the claim to prove the indexer re-derives
hashes instead of trusting the JSON.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools import corpus_evidence_index as cei

ROOT = Path(__file__).resolve().parent.parent


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_game(root: Path, slug: str, *, frames: int = 2,
              classification: str = "BUILD-ONLY") -> dict:
    """Create a synthetic per-game evidence dir whose claims are all honest."""
    edir = root / slug
    edir.mkdir(parents=True, exist_ok=True)
    original = edir / f"{slug}.exe"
    original.write_bytes(b"MZ\x90\x00" + slug.encode("ascii"))
    native = edir / f"{slug}-native"
    native.write_bytes(b"\xcf\xfa\xed\xfe" + slug.encode("ascii"))
    frame_paths = []
    for i in range(frames):
        frame = edir / f"frame-{i}.bmp"
        frame.write_bytes(b"BM" + bytes([i]) + slug.encode("ascii"))
        frame_paths.append(frame)
    verification = {
        "slug": slug,
        "classification": classification,
        "b1_status": "BUILD-ONLY",
        "source_exe": str(original),
        "source": {"exists": True, "valid": True,
                   "sha256": digest(original.read_bytes())},
        "native": {"exists": True, "valid": True, "path": str(native),
                   "sha256": digest(native.read_bytes())},
        "frames": [],
        "exit": 0,
    }
    save_verification(edir, verification)
    proof = {
        "schema": 1,
        "frames": [
            {"game": slug, "path": str(path), "sha256": digest(path.read_bytes()),
             "blank": i == 0, "distinct_colors": 1 if i == 0 else 7}
            for i, path in enumerate(frame_paths)
        ],
    }
    save_proof(edir, proof)
    return {"dir": edir, "verification": verification, "proof": proof,
            "original": original, "native": native, "frames": frame_paths}


def save_verification(edir: Path, verification: dict) -> None:
    (edir / "verification.json").write_text(
        json.dumps(verification, indent=2), encoding="utf-8")


def save_proof(edir: Path, proof: dict) -> None:
    (edir / "frame-proof.json").write_text(
        json.dumps(proof, indent=2), encoding="utf-8")


def run_index(root: Path, out_dir: Path, *, label: str = "unit test",
              strict: bool = False) -> tuple[int, str, dict]:
    out = out_dir / "index.md"
    json_out = out_dir / "index.json"
    argv = ["--evidence-root", str(root), "--out", str(out),
            "--json", str(json_out), "--label", label]
    if strict:
        argv.append("--strict")
    rc = cei.main(argv)
    markdown = out.read_text(encoding="utf-8") if out.is_file() else ""
    data = json.loads(json_out.read_text(encoding="utf-8")) if json_out.is_file() else {}
    return rc, markdown, data


def only_game(data: dict) -> dict:
    assert len(data["games"]) == 1
    return data["games"][0]


# --------------------------------------------------------------------------
# hashing primitives
# --------------------------------------------------------------------------
def test_sha256_file_is_exact_and_check_claim_statuses(tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"abc")
    assert cei.sha256_file(blob) == digest(b"abc")
    assert cei.sha256_file(tmp_path / "absent") is None
    assert cei.is_sha256(digest(b"abc"))
    assert not cei.is_sha256("short") and not cei.is_sha256(None)

    match = cei.check_claim(str(blob), digest(b"abc"), tmp_path)
    assert match["status"] == "MATCH" and match["actual"] == digest(b"abc")
    assert cei.check_claim(str(blob), "0" * 64, tmp_path)["status"] == "MISMATCH"
    assert cei.check_claim(str(tmp_path / "absent"), digest(b"abc"),
                           tmp_path)["status"] == "MISSING"
    assert cei.check_claim(None, digest(b"abc"), tmp_path)["status"] == "NO_CLAIM"
    assert cei.check_claim(str(blob), "not-a-hash", tmp_path)["status"] == "INVALID_CLAIM"
    # Relative supplied paths resolve against the JSON's directory.
    assert cei.check_claim("blob.bin", digest(b"abc"), tmp_path)["status"] == "MATCH"


def test_load_object_rejects_non_objects(tmp_path):
    bad = tmp_path / "array.json"
    bad.write_text("[1, 2, 3]")
    with pytest.raises(ValueError):
        cei.load_object(bad)


# --------------------------------------------------------------------------
# honest evidence: PARTIAL, never PASS
# --------------------------------------------------------------------------
def test_honest_case_is_partial_and_never_pass(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "honest")
    rc, markdown, data = run_index(root, tmp_path / "out")
    assert rc == 0
    game = only_game(data)
    assert game["verdict"] == "PARTIAL"
    assert game["nonblank_verified"] == 1
    assert game["original"]["status"] == game["native"]["status"] == "MATCH"
    assert all(c["status"] == "MATCH" for c in game["frames"])
    assert data["counts"]["PASS"] == 0
    assert "| PASS | 0 |" in markdown
    assert all(g["verdict"] != "PASS" for g in data["games"])
    # The forged classification in JSON is recorded but never used as a verdict.
    assert game["claimed_classification"] == "BUILD-ONLY"
    assert case["dir"].is_dir()


def test_forged_pass_classification_never_becomes_a_pass(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "forged", classification="PASS")
    verification = case["verification"]
    verification["engines"] = {"status": "MATCH", "reason": ""}
    save_verification(case["dir"], verification)
    _, markdown, data = run_index(root, tmp_path / "out")
    game = only_game(data)
    assert game["claimed_classification"] == "PASS"
    assert game["verdict"] == "PARTIAL"
    assert data["counts"]["PASS"] == 0
    assert "| PASS | 0 |" in markdown


def test_all_blank_frames_are_fail_not_partial(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "blank", frames=2)
    proof = case["proof"]
    for row in proof["frames"]:
        row["blank"] = True
        row["distinct_colors"] = 1
    save_proof(case["dir"], proof)
    _, _, data = run_index(root, tmp_path / "out")
    assert only_game(data)["verdict"] == "FAIL"


def test_no_frames_is_build_only(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "build", frames=0)
    assert case["proof"]["frames"] == []
    _, _, data = run_index(root, tmp_path / "out")
    game = only_game(data)
    assert game["verdict"] == "BUILD ONLY"
    assert data["counts"]["BUILD ONLY"] == 1


# --------------------------------------------------------------------------
# tampering detection: bytes mutated or claims fabricated
# --------------------------------------------------------------------------
def test_original_file_tampering_is_detected(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "orig")
    case["original"].write_bytes(b"MZ-tampered-original")
    _, markdown, data = run_index(root, tmp_path / "out")
    game = only_game(data)
    assert game["verdict"] == "TAMPERED"
    assert game["original"]["status"] == "MISMATCH"
    assert "original hash mismatch" in game["reason"]
    assert data["counts"]["TAMPERED"] == 1
    assert data["counts"]["PARTIAL"] == 0
    assert "TAMPERED" in markdown


def test_fabricated_original_claim_in_json_is_detected(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "claim")
    verification = case["verification"]
    verification["source"]["sha256"] = "0" * 64
    save_verification(case["dir"], verification)
    _, _, data = run_index(root, tmp_path / "out")
    game = only_game(data)
    assert game["verdict"] == "TAMPERED"
    assert "original hash mismatch" in game["reason"]
    assert case["original"].read_bytes().startswith(b"MZ")  # bytes are honest


def test_native_and_frame_file_tampering_is_detected(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "native")
    case["native"].write_bytes(b"tampered-native")
    case["frames"][1].write_bytes(b"tampered-frame")
    _, _, data = run_index(root, tmp_path / "out")
    game = only_game(data)
    assert game["verdict"] == "TAMPERED"
    assert "native hash mismatch" in game["reason"]
    assert "frame[1] hash mismatch" in game["reason"]
    assert game["native"]["status"] == "MISMATCH"
    assert game["frames"][1]["status"] == "MISMATCH"
    assert game["frames"][0]["status"] == "MATCH"


def test_fabricated_frame_claim_is_detected(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "frame")
    proof = case["proof"]
    proof["frames"][0]["sha256"] = "f" * 64
    save_proof(case["dir"], proof)
    _, _, data = run_index(root, tmp_path / "out")
    game = only_game(data)
    assert game["verdict"] == "TAMPERED"
    assert "frame[0] hash mismatch" in game["reason"]


def test_malformed_hash_claim_fails_closed(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "malformed")
    verification = case["verification"]
    verification["native"]["sha256"] = None
    save_verification(case["dir"], verification)
    _, _, data = run_index(root, tmp_path / "out")
    game = only_game(data)
    assert game["native"]["status"] == "INVALID_CLAIM"
    assert game["verdict"] == "TAMPERED"


# --------------------------------------------------------------------------
# missing evidence fails closed
# --------------------------------------------------------------------------
def test_missing_original_file_is_blocked(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "gone")
    case["original"].unlink()
    _, _, data = run_index(root, tmp_path / "out")
    game = only_game(data)
    assert game["original"]["status"] == "MISSING"
    assert game["verdict"] == "BLOCKED"
    assert "file missing" in game["reason"]


def test_missing_frame_file_is_blocked(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "goneframe")
    case["frames"][0].unlink()
    _, _, data = run_index(root, tmp_path / "out")
    game = only_game(data)
    assert game["frames"][0]["status"] == "MISSING"
    assert game["verdict"] == "BLOCKED"
    assert data["counts"]["PASS"] == 0


def test_missing_or_malformed_verification_json_is_blocked(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "noverification")
    make_game(root, "good")  # keep discovery non-empty; bad game stays visible
    (case["dir"] / "verification.json").unlink()
    _, _, data = run_index(root, tmp_path / "out")
    bad = next(g for g in data["games"] if g["slug"] == "noverification")
    good = next(g for g in data["games"] if g["slug"] == "good")
    assert bad["verdict"] == "BLOCKED"
    assert "verification.json missing" in bad["reason"]
    assert good["verdict"] == "PARTIAL"

    (case["dir"] / "verification.json").write_text("{not json")
    _, _, data = run_index(root, tmp_path / "out")
    bad = next(g for g in data["games"] if g["slug"] == "noverification")
    assert bad["verdict"] == "BLOCKED"
    assert "unreadable" in bad["reason"]


def test_missing_frame_proof_is_blocked(tmp_path):
    root = tmp_path / "root"
    case = make_game(root, "noproof")
    (case["dir"] / "frame-proof.json").unlink()
    _, _, data = run_index(root, tmp_path / "out")
    assert only_game(data)["verdict"] == "BLOCKED"


def test_empty_or_absent_evidence_root_is_refused(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    out = tmp_path / "index.md"
    assert cei.main(["--evidence-root", str(empty), "--out", str(out)]) == 2
    assert not out.exists()
    assert cei.main(["--evidence-root", str(tmp_path / "absent"),
                     "--out", str(out)]) == 2


def test_strict_mode_fails_on_tamper_only(tmp_path):
    clean_root = tmp_path / "clean"
    make_game(clean_root, "clean")
    assert run_index(clean_root, tmp_path / "cleanout", strict=True)[0] == 0

    bad_root = tmp_path / "bad"
    case = make_game(bad_root, "bad")
    case["original"].write_bytes(b"tampered")
    rc, markdown, data = run_index(bad_root, tmp_path / "badout", strict=True)
    assert rc == 1
    assert data["counts"]["TAMPERED"] == 1
    assert "TAMPERED evidence found" not in markdown  # stderr, not the index


# --------------------------------------------------------------------------
# deterministic output and exact commands
# --------------------------------------------------------------------------
def test_markdown_has_verdict_counts_and_exact_commands(tmp_path):
    root = tmp_path / "root"
    make_game(root, "one")
    out_dir = tmp_path / "out"
    _, markdown, data = run_index(root, out_dir, label="unit label")
    assert "- Label: unit label" in markdown
    assert "## Verdict counts" in markdown
    for verdict in cei.VERDICTS:
        assert f"| {verdict} | {data['counts'][verdict]} |" in markdown
    assert "## Reproduction commands" in markdown
    assert "python3 -m tools.corpus_evidence_index --evidence-root" in markdown
    assert "<EVIDENCE_ROOT>" in markdown
    assert "python3 -m tools.verify_corpus" in markdown
    assert "python3 -m tools.frame_proof" in markdown
    assert "## Limits" in markdown
    assert "PASS is never emitted" in markdown
    assert sum(data["counts"].values()) == len(data["games"])


def test_output_is_byte_stable_and_games_are_sorted(tmp_path):
    root = tmp_path / "root"
    for slug in ("zeta", "alpha", "middle"):
        make_game(root, slug)
    out_dir = tmp_path / "out"
    rc1, markdown1, data1 = run_index(root, out_dir)
    rc2, markdown2, data2 = run_index(root, out_dir)  # identical arguments
    assert rc1 == rc2 == 0
    assert markdown1 == markdown2
    assert data1 == data2
    assert (out_dir / "index.md").read_bytes() == markdown1.encode("utf-8")
    slugs = [game["slug"] for game in data1["games"]]
    assert slugs == sorted(slugs) == ["alpha", "middle", "zeta"]
    # No clock or other timestamp leaks into the index.
    assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", markdown1)
    assert sum(data1["counts"].values()) == 3


def test_cli_module_entry_point_writes_the_index(tmp_path):
    root = tmp_path / "root"
    make_game(root, "cli")
    out = tmp_path / "cli-index.md"
    result = subprocess.run(
        [sys.executable, "-m", "tools.corpus_evidence_index",
         "--evidence-root", str(root), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    markdown = out.read_text(encoding="utf-8")
    assert "## Verdict counts" in markdown
    assert "| PARTIAL | 1 |" in markdown
    assert "python3 -m tools.corpus_evidence_index" in markdown
