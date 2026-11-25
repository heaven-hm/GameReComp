"""Deterministic synthetic-data tests for the game verdict reconciler.

Run with: python3 -m pytest tests/test_game_verdict_reconcile.py -q

Every fixture is created under tmp_path: no host corpus, game, evidence path or
repository document is read. All hashes are recomputed from those fixtures.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import game_verdict_reconcile as gvr

FIXED_TIME = "2026-11-25T00:00:00Z"
ORIGINAL_BYTES = b"synthetic original PE bytes"
NATIVE_BYTES = b"synthetic native binary bytes"
FRAME_BYTES = b"synthetic frame bytes"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


INPUT_SHA = digest(ORIGINAL_BYTES)
NATIVE_SHA = digest(NATIVE_BYTES)
FRAME_SHA = digest(FRAME_BYTES)


def write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def write_original(tmp_path: Path) -> Path:
    path = tmp_path / "Cave-Story.exe"
    path.write_bytes(ORIGINAL_BYTES)
    return path


def write_native(tmp_path: Path) -> Path:
    path = tmp_path / "cave-story-native"
    path.write_bytes(NATIVE_BYTES)
    return path


def write_frame(tmp_path: Path) -> Path:
    path = tmp_path / "frame.bmp"
    path.write_bytes(FRAME_BYTES)
    return path


def corpus_game(tmp_path: Path, *, name: str = "Cave Story",
                status: str = "PARTIAL", launched: bool = True,
                input_sha: str | None = INPUT_SHA,
                input_path: Path | None = None,
                native_sha: str | None = NATIVE_SHA,
                native_path: Path | None = None,
                frame_path: Path | None = None,
                frame_sha: str | None = FRAME_SHA,
                pipeline_status: str = "scenario-pass",
                scenario: bool = False) -> dict:
    original = write_original(tmp_path) if input_path is None else input_path
    native = write_native(tmp_path) if native_path is None else native_path
    frame = write_frame(tmp_path) if frame_path is None else frame_path
    return {
        "game": name,
        "status": status,
        "reasons": ["synthetic evidence recorded"],
        "original": {"path": str(original), "sha256": input_sha,
                     "architecture": "i386"},
        "native": {"path": str(native), "sha256": native_sha},
        "pipeline": {"status": "ok", "verification_status": pipeline_status,
                     "native_sha256": native_sha, "claims_verified": True},
        "launch": {"executed": launched, "exit_code": 0},
        "frames": {"example": {"path": str(frame), "sha256": frame_sha,
                               "distinct_colors": 3713},
                   "proven_frame_count": 1},
        "scenario": {"declared": scenario, "complete": scenario},
    }


def frame_proof(tmp_path: Path, *, name: str = "Cave Story",
                sha: str | None = FRAME_SHA, blank: bool = False,
                colors: int | None = 3713,
                path: Path | None = None) -> dict:
    frame = write_frame(tmp_path) if path is None else path
    return {
        "schema": 1,
        "what_it_proves": "synthetic frame has real pixel content",
        "frames": [{"game": name, "path": str(frame), "sha256": sha,
                    "width": 320, "height": 240,
                    "distinct_colors": colors, "blank": blank}],
    }


def oracle_record(tmp_path: Path, *, game: str = "Cave Story",
                  input_sha: str | None = INPUT_SHA,
                  native_sha: str | None = NATIVE_SHA,
                  oracle: str = "original-windows", verified: object = True,
                  scenario: str = "boot-to-menu",
                  evidence_text: str = "menus match at frame 12",
                  evidence_sha: str | None = None,
                  evidence_bytes: bytes | None = None,
                  evidence_path: Path | None = None) -> dict:
    evidence = evidence_path or (tmp_path / "scenario-evidence.json")
    payload = evidence_bytes if evidence_bytes is not None else \
        evidence_text.encode("utf-8")
    if not evidence.exists():
        evidence.write_bytes(payload)
    record = {
        "game": game,
        "sha256": input_sha,
        "native_sha256": native_sha,
        "oracle": oracle,
        "oracle_verified": verified,
        "scenario": scenario,
        "scenario_evidence": evidence_text,
        "evidence_path": str(evidence),
    }
    record["evidence_sha256"] = digest(payload) if evidence_sha is None else evidence_sha
    return record


def matrix_row(*, name: str = "Cave Story", status: str = "PARTIAL",
               sha: str | None = INPUT_SHA, **extra: object) -> dict:
    row = {"game": name, "status": status, "sha256": sha,
           "source": "docs/GAME_MATRIX.md"}
    row.update(extra)
    return row


def run_reconcile(tmp_path: Path, *, matrix: list[dict] | None = None,
                  corpus: dict | None = None, frames: dict | None = None,
                  oracle: dict | None = None, cli_rows: list[str] | None = None,
                  markdown: bool = False, fail_on_conflict: bool = False,
                  generated_at: str = FIXED_TIME) -> tuple[dict, bytes, str, int]:
    args: list[str] = []
    if matrix is not None:
        args += ["--matrix-rows", str(write_json(tmp_path / "matrix.json",
                                                  {"rows": matrix}))]
    if corpus is not None:
        args += ["--corpus-acceptance", str(write_json(tmp_path / "corpus.json", corpus))]
    if frames is not None:
        args += ["--frame-proof", str(write_json(tmp_path / "frame-proof.json", frames))]
    if oracle is not None:
        args += ["--oracle-evidence", str(write_json(tmp_path / "oracle.json",
                                                     {"records": [oracle]}))]
    for row in cli_rows or []:
        args += ["--row", row]
    out = tmp_path / "verdicts.json"
    args += ["--generated-at", generated_at, "--out", str(out)]
    md_path = tmp_path / "verdicts.md"
    if markdown:
        args += ["--markdown", str(md_path)]
    if fail_on_conflict:
        args += ["--fail-on-conflict"]
    rc = gvr.main(args)
    payload = out.read_bytes()
    text = md_path.read_text(encoding="utf-8") if markdown else ""
    return json.loads(payload), payload, text, rc


def row_of(report: dict, game: str = "Cave Story") -> dict:
    rows = [r for r in report["rows"] if r["game"] == game]
    assert len(rows) == 1, report["rows"]
    return rows[0]


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def test_matrix_pass_while_corpus_partial_is_conflict(tmp_path):
    report, _, text, rc = run_reconcile(
        tmp_path,
        matrix=[matrix_row(status="PASS")],
        corpus={"games": [corpus_game(tmp_path, status="PARTIAL")]},
        frames=frame_proof(tmp_path), markdown=True,
    )
    row = row_of(report)
    assert row["verdict"] == "CONFLICT"
    assert row["claims"]["matrix"] == "PASS"
    assert row["claims"]["corpus"] == "PARTIAL"
    assert row["pass_eligible"] is False
    assert any("verdict conflict" in reason for reason in row["reasons"])
    assert report["summary"]["by_verdict"]["PASS"] == 0
    assert "| Cave Story | CONFLICT | PASS | PARTIAL |" in text
    assert rc == 0


def test_fail_on_conflict_flag_changes_exit_code(tmp_path):
    matrix = [matrix_row(status="PASS")]
    corpus = {"games": [corpus_game(tmp_path, status="PARTIAL")]}
    _, _, _, rc = run_reconcile(tmp_path, matrix=matrix, corpus=corpus,
                                fail_on_conflict=True)
    assert rc == 1


def test_frame_hash_tampering_is_detected_and_blocks_pass(tmp_path):
    frame = write_frame(tmp_path)
    report, _, _, _ = run_reconcile(
        tmp_path,
        matrix=[matrix_row(status="PARTIAL")],
        corpus={"games": [corpus_game(tmp_path, status="PARTIAL", frame_path=frame)]},
        frames=frame_proof(tmp_path, sha="f" * 64, path=frame),
        oracle=oracle_record(tmp_path),
    )
    row = row_of(report)
    assert row["verdict"] == "CONFLICT"
    assert row["pass_eligible"] is False
    assert row["frames"]["hash_mismatch"] == 1
    assert row["frames"]["proven"] == 0
    assert row["frames"]["frames"][0]["actual_sha256"] == FRAME_SHA
    assert row["hashes"]["recomputed_and_agreed"] is False
    assert any("but recomputed" in r for r in row["reasons"])


def test_corpus_original_hash_tampering_is_conflict(tmp_path):
    original = write_original(tmp_path)
    report, _, _, _ = run_reconcile(
        tmp_path,
        matrix=[matrix_row(status="PARTIAL", sha=INPUT_SHA)],
        corpus={"games": [corpus_game(tmp_path, status="PARTIAL",
                                      input_path=original, input_sha="e" * 64)]},
    )
    row = row_of(report)
    assert row["verdict"] == "CONFLICT"
    assert any("corpus original SHA-256" in r for r in row["reasons"])


def test_matrix_evidence_hash_tampering_is_conflict(tmp_path):
    evidence = tmp_path / "matrix-evidence.json"
    evidence.write_bytes(b"real matrix evidence")
    report, _, _, _ = run_reconcile(
        tmp_path,
        matrix=[matrix_row(status="PARTIAL", sha=None,
                           evidence_path=str(evidence),
                           evidence_sha256="a" * 64)],
    )
    row = row_of(report)
    assert row["verdict"] == "CONFLICT"
    assert any("game-matrix evidence SHA-256" in r for r in row["reasons"])


def test_tampered_frame_is_not_treated_as_proven_evidence(tmp_path):
    frame = write_frame(tmp_path)
    report, _, _, _ = run_reconcile(
        tmp_path,
        corpus={"games": [corpus_game(tmp_path, status="PARTIAL", frame_path=frame)]},
        frames=frame_proof(tmp_path, sha="1" * 64, path=frame),
    )
    assert row_of(report)["frames"]["proven"] == 0
    assert row_of(report)["claims"]["frame_proof"] == "NO PROVEN FRAME"


# ---------------------------------------------------------------------------
# No fake promotion
# ---------------------------------------------------------------------------

def test_matrix_claim_without_corpus_is_not_tested(tmp_path):
    report, _, _, _ = run_reconcile(tmp_path, matrix=[matrix_row(status="PASS")])
    row = row_of(report)
    assert row["verdict"] == "NOT TESTED"
    assert row["pass_eligible"] is False
    assert any("not evidence" in reason for reason in row["reasons"])


def test_corpus_pass_without_validated_oracle_is_capped_at_partial(tmp_path):
    report, _, _, _ = run_reconcile(
        tmp_path,
        matrix=[matrix_row(status="PASS")],
        corpus={"games": [corpus_game(tmp_path, status="PASS")]},
        frames=frame_proof(tmp_path),
    )
    # Matrix PASS and corpus PASS agree, so there is no conflict -- but a bare
    # corpus PASS is still not credited without oracle evidence.
    row = row_of(report)
    assert row["verdict"] == "PARTIAL"
    assert row["pass_eligible"] is False
    assert any("validated oracle" in reason for reason in row["reasons"])


@pytest.mark.parametrize("mutate, expected_reason", [
    ({"verified": False}, "oracle_verified is not the boolean true"),
    ({"verified": "true"}, "oracle_verified is not the boolean true"),
    ({"oracle": "dummy-device"}, "not an accepted original-Windows oracle"),
    ({"oracle": "windows-oracle-unexecuted"}, "not an accepted original-Windows oracle"),
    ({"scenario": ""}, "no named scenario"),
    ({"evidence_sha": "2" * 64}, "does not match"),
    ({"input_sha": "3" * 64}, "oracle input SHA-256 does not match"),
    ({"native_sha": "4" * 64}, "oracle native SHA-256 does not match"),
])
def test_fake_or_weak_oracle_never_promotes(tmp_path, mutate, expected_reason):
    report, _, _, _ = run_reconcile(
        tmp_path,
        matrix=[matrix_row(status="PARTIAL")],
        corpus={"games": [corpus_game(tmp_path, status="PARTIAL")]},
        frames=frame_proof(tmp_path),
        oracle=oracle_record(tmp_path, **mutate),
    )
    row = row_of(report)
    assert row["verdict"] != "PASS"
    assert row["pass_eligible"] is False
    assert report["summary"]["by_verdict"]["PASS"] == 0
    assert any(expected_reason in reason for reason in row["reasons"])


def test_unvalidated_oracle_claim_with_corpus_fail_is_not_pass(tmp_path):
    report, _, _, _ = run_reconcile(
        tmp_path,
        corpus={"games": [corpus_game(tmp_path, status="FAIL")]},
        oracle=oracle_record(tmp_path, verified=False),
    )
    assert row_of(report)["verdict"] == "FAIL"
    assert row_of(report)["pass_eligible"] is False


def test_validated_oracle_without_proven_frame_does_not_pass(tmp_path):
    report, _, _, _ = run_reconcile(
        tmp_path,
        corpus={"games": [corpus_game(tmp_path, status="PARTIAL")]},
        # Claimed hash matches but the frame is blank: no render evidence.
        frames=frame_proof(tmp_path, blank=True),
        oracle=oracle_record(tmp_path),
    )
    row = row_of(report)
    assert row["verdict"] != "PASS"
    assert row["pass_eligible"] is False
    assert row["frames"]["proven"] == 0


def test_validated_oracle_can_promote_to_pass(tmp_path):
    report, _, text, _ = run_reconcile(
        tmp_path,
        matrix=[matrix_row(status="PARTIAL")],
        corpus={"games": [corpus_game(tmp_path, status="PARTIAL")]},
        frames=frame_proof(tmp_path),
        oracle=oracle_record(tmp_path), markdown=True,
    )
    row = row_of(report)
    assert row["verdict"] == "PASS"
    assert row["pass_eligible"] is True
    assert row["conflicts"] == []
    assert row["oracles"][0]["valid"] is True
    assert row["frames"]["proven"] == 1
    assert report["summary"]["by_verdict"]["PASS"] == 1
    assert any("validated original-Windows oracle" in r for r in row["reasons"])
    assert "| Cave Story | PASS | PARTIAL | PARTIAL | PROVEN |" in text


def test_corpus_fail_with_valid_oracle_is_conflict(tmp_path):
    report, _, _, _ = run_reconcile(
        tmp_path,
        corpus={"games": [corpus_game(tmp_path, status="FAIL")]},
        frames=frame_proof(tmp_path),
        oracle=oracle_record(tmp_path),
    )
    row = row_of(report)
    assert row["verdict"] == "CONFLICT"
    assert row["pass_eligible"] is False
    assert any("corpus status is FAIL" in r for r in row["reasons"])


# ---------------------------------------------------------------------------
# Joining, determinism and fail-closed inputs
# ---------------------------------------------------------------------------

def test_join_by_game_name_is_case_and_whitespace_insensitive(tmp_path):
    report, _, _, _ = run_reconcile(
        tmp_path,
        matrix=[matrix_row(name="cave   story", status="PARTIAL")],
        cli_rows=["CAVE STORY=PARTIAL"],
        corpus={"games": [corpus_game(tmp_path, name="Cave Story",
                                      status="PARTIAL")]},
        frames=frame_proof(tmp_path, name="CAVE STORY"),
    )
    # Two matrix rows for one game are themselves a contradiction: the join
    # happened, otherwise there would be separate rows instead of one CONFLICT.
    assert len(report["rows"]) == 1
    joined = report["rows"][0]
    assert joined["key"] == "cave story"
    assert joined["verdict"] == "CONFLICT"
    assert any("more than one row" in r for r in joined["reasons"])


def test_cli_row_alone_is_advisory(tmp_path):
    report, _, _, _ = run_reconcile(tmp_path, cli_rows=["Cave Story=PASS"])
    row = row_of(report)
    assert row["verdict"] == "NOT TESTED"
    assert row["claims"]["matrix"] == "PASS"
    assert row["pass_eligible"] is False


def test_json_and_markdown_are_byte_deterministic(tmp_path):
    kwargs = {
        "matrix": [matrix_row(status="PARTIAL")],
        "corpus": {"games": [corpus_game(tmp_path, status="PARTIAL")]},
        "frames": frame_proof(tmp_path),
        "oracle": oracle_record(tmp_path),
        "markdown": True,
    }
    run_reconcile(tmp_path, **kwargs)
    first_bytes = (tmp_path / "verdicts.json").read_bytes()
    first_md = (tmp_path / "verdicts.md").read_text(encoding="utf-8")
    run_reconcile(tmp_path, **kwargs)
    second_bytes = (tmp_path / "verdicts.json").read_bytes()
    second_md = (tmp_path / "verdicts.md").read_text(encoding="utf-8")
    assert second_bytes == first_bytes
    assert second_md == first_md
    assert json.loads(first_bytes)["summary"]["by_verdict"]["PASS"] == 1
    assert b'"generated_at": "2026-11-25T00:00:00Z"' in first_bytes


def test_invalid_json_input_is_recorded_and_cannot_pass(tmp_path):
    bad = tmp_path / "broken-corpus.json"
    bad.write_text("{ broken json ]", encoding="utf-8")
    out = tmp_path / "verdicts.json"
    rc = gvr.main(["--corpus-acceptance", str(bad), "--generated-at", FIXED_TIME,
                   "--out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0
    invalid = [entry for entry in report["inputs"] if entry["status"] == "INVALID"]
    assert len(invalid) == 1
    assert invalid[0]["kind"] == "corpus-acceptance"
    assert report["summary"]["by_verdict"]["PASS"] == 0


def test_missing_frame_file_is_unverifiable_not_a_fake_pass(tmp_path):
    frame = tmp_path / "deleted-frame.bmp"
    report, _, _, _ = run_reconcile(
        tmp_path,
        corpus={"games": [corpus_game(tmp_path, status="PARTIAL")]},
        frames=frame_proof(tmp_path, path=frame),
    )
    row = row_of(report)
    assert row["verdict"] != "PASS"
    assert row["frames"]["proven"] == 0
    assert row["frames"]["frames"][0]["actual_sha256"] is None
