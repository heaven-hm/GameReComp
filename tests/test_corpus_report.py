"""Tests for the fail-closed, CLI-driven corpus acceptance report generator.

Run with: python3 -m pytest tests/test_corpus_report.py -q
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import corpus_report as cr


def make_summary(tmp_path: Path, name: str = "sample", *, exit_code: int = 0,
                 frames: list[str] | None = None, status: str | None = None,
                 binary: Path | None = None) -> dict:
    evidence = tmp_path / "evidence" / name
    shots = evidence / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    frame_rows = []
    for frame in frames or []:
        p = shots / frame
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
        frame_rows.append({"path": str(p), "width": 320, "height": 240})
    (evidence / "run.log").write_text("launched\n")
    binary = binary or tmp_path / f"{name}.bin"
    binary.write_bytes(b"binary bytes for " + name.encode())
    rec = {
        "game": name,
        "binary": str(binary),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "folder": str(tmp_path),
        "command": [str(binary)],
        "exit_code": exit_code,
        "evidence": str(evidence),
        "frames": frame_rows,
        "frame_count": len(frame_rows),
    }
    if status:
        rec["status"] = status
        rec["reason"] = "no native binary at /missing/game.exe"
    return {"schema": 1, "results": [rec]}


def make_proof(game: str, frames: list[dict]) -> dict:
    return {"schema": 1, "frames": [{"game": game, **row} for row in frames]}


def test_sha256_file_is_exact_and_load_json_rejects_arrays(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"abc")
    assert cr.sha256_file(p) == hashlib.sha256(b"abc").hexdigest()
    assert cr.sha256_file(tmp_path / "absent") is None
    bad = tmp_path / "bad.json"
    bad.write_text("[1,2,3]")
    with pytest.raises(ValueError):
        cr.load_json(bad)


def test_proof_index_prefers_hash_and_path_only_match_is_unambiguous(tmp_path):
    p1, p2 = str(tmp_path / "a.bmp"), str(tmp_path / "b.bmp")
    proof = {"frames": [
        {"game": "g", "path": p1, "distinct_colors": 5},
        {"game": "g", "path": p1, "sha256": "a" * 64, "distinct_colors": 7,
         "blank": False},
        {"game": "g", "path": p2, "sha256": "b" * 64, "distinct_colors": 1,
         "blank": True},
    ]}
    by_game, by_path = cr.index_frame_proof(proof)
    assert by_game[("g", p1)]["sha256"] == "a" * 64
    assert len(by_path[p1]) == 2  # ambiguous -> no path-only fallback
    empty_by_game, empty_by_path = cr.index_frame_proof(None)
    assert empty_by_game == {} and empty_by_path == {}


def test_classify_frames_is_fail_closed_without_proof(tmp_path):
    f = str(tmp_path / "frame.bmp")
    Path(f).write_bytes(b"BM")
    rows = cr.classify_frames([{"game": "g", "path": f, "width": 8, "height": 8}],
                              {}, {})
    assert rows[0]["marker"].startswith("UNPROVEN")
    assert rows[0]["sha256"] is None
    assert rows[0]["distinct_colors"] is None


def test_classify_frames_marks_blank_and_ok(tmp_path):
    f = str(tmp_path / "frame.bmp")
    Path(f).write_bytes(b"BM")
    base = {"game": "g", "path": f}
    proof = {"frames": [
        {**base, "sha256": "c" * 64, "distinct_colors": 1, "blank": True},
        {**base, "sha256": "d" * 64, "distinct_colors": 42, "blank": False},
    ]}
    by_game, by_path = cr.index_frame_proof(proof)
    assert by_game[("g", f)]["sha256"] == "c" * 64
    ok = cr.classify_frames([{"game": "g", "path": f}], {("g", f): proof["frames"][1]}, {})
    assert ok[0]["marker"].startswith("OK")
    assert ok[0]["sha256"] == "d" * 64
    blank = cr.classify_frames([{"game": "g", "path": f}], {("g", f): proof["frames"][0]}, {})
    assert blank[0]["marker"].startswith("BLANK")
    # A proof row without a hash stays UNPROVEN rather than being assumed blank.
    no_hash = cr.classify_frames([{"game": "g", "path": f}],
                                 {("g", f): {"distinct_colors": 1, "blank": True}}, {})
    assert no_hash[0]["marker"].startswith("UNPROVEN")


@pytest.mark.parametrize("rec,scenario,expected", [
    ({"frames": [], "blocked": True, "status_text": "no native binary at /x",
      "ran": False, "run_status": None, "build_evidence": False,
      "binary_sha256": None, "input_sha256": None, "log_exists": False,
      "exit_code": None}, None, "BLOCKED"),
    ({"frames": [], "blocked": False, "status_text": "", "ran": False,
      "run_status": None, "build_evidence": True, "binary_sha256": "e" * 64,
      "input_sha256": None, "log_exists": False, "exit_code": None}, None,
     "BUILD ONLY"),
    ({"frames": [], "blocked": False, "status_text": "", "ran": False,
      "run_status": None, "build_evidence": False, "binary_sha256": None,
      "input_sha256": None, "log_exists": False, "exit_code": None}, None,
     "BLOCKED"),
    ({"frames": [], "blocked": False,
      "status_text": "rebuilt process aborted on unhandled exception",
      "ran": True, "run_status": "aborted", "build_evidence": True,
      "binary_sha256": "f" * 64, "input_sha256": None, "log_exists": True,
      "exit_code": 3}, None, "FAIL"),
    ({"frames": [], "blocked": False, "status_text": "stayed alive", "ran": True,
      "run_status": "exited", "build_evidence": True, "binary_sha256": "f" * 64,
      "input_sha256": None, "log_exists": True, "exit_code": "TIMEOUT"}, None,
     "PARTIAL"),
    ({"frames": [], "blocked": False, "status_text": "no oracle supplied",
      "ran": True, "run_status": "exited", "build_evidence": True,
      "binary_sha256": "f" * 64, "input_sha256": None, "log_exists": True,
      "exit_code": 0}, None, "PARTIAL"),
])
def test_assign_verdict_fail_closed(rec, scenario, expected):
    verdict, reason = cr.assign_verdict(rec, scenario)
    assert verdict == expected
    assert isinstance(reason, str) and reason


def test_scenario_pass_requires_oracle_evidence_file_and_matching_hash(tmp_path):
    evidence = tmp_path / "scenario.log"
    evidence.write_text("scenario executed with oracle")
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    good = {"game": "g", "verdict": "PASS", "scenario": "boot-to-menu",
            "oracle": "original Windows exe", "evidence": "recorded menu transition",
            "evidence_path": str(evidence), "evidence_sha256": digest}
    assert cr.validate_scenario(good)["evidence_sha256"] == digest
    for change in (
        {"oracle": "no oracle"},
        {"oracle": "dummy device"},
        {"evidence_sha256": "0" * 64},
        {"evidence_path": str(tmp_path / "missing")},
        {"scenario": ""},
        {"verdict": "PARTIAL"},
    ):
        assert cr.validate_scenario({**good, **change}) is None
    # No evidence path at all -> no PASS.
    assert cr.validate_scenario({k: v for k, v in good.items()
                                 if k != "evidence_path"}) is None


def test_pass_only_from_valid_scenario_and_downgrades_when_run_missing(tmp_path):
    ran = {"frames": [], "blocked": False, "status_text": "", "ran": True,
           "run_status": "exited", "build_evidence": True,
           "binary_sha256": "f" * 64, "input_sha256": None,
           "log_exists": True, "exit_code": 0}
    scenario = {"scenario": "s", "oracle": "o", "evidence": "e",
                "evidence_path": "/tmp/evidence", "evidence_sha256": "a" * 64}
    assert cr.assign_verdict(ran, scenario)[0] == "PASS"
    not_ran = dict(ran, ran=False, log_exists=False)
    verdict, reason = cr.assign_verdict(not_ran, scenario)
    assert verdict == "PARTIAL"
    assert "downgraded" in reason


def test_build_report_end_to_end_never_invents_pass(tmp_path):
    summary = make_summary(tmp_path, "tetris", frames=["frame-0000.bmp"])
    proof = make_proof("tetris", [
        {"path": summary["results"][0]["frames"][0]["path"],
         "sha256": "1" * 64, "distinct_colors": 3713, "blank": False,
         "width": 320, "height": 240},
    ])
    s_path = tmp_path / "summary.json"
    p_path = tmp_path / "proof.json"
    s_path.write_text(json.dumps(summary))
    p_path.write_text(json.dumps(proof))
    report = cr.build_report(summary, proof, None, None, label="smoke",
                             source_paths={"summary": str(s_path),
                                           "frame_proof": str(p_path)})
    assert report["schema_version"] == 1
    assert report["generator"] == "tools/corpus_report.py"
    game = report["games"][0]
    assert game["game"] == "tetris"
    assert game["verdict"] == "PARTIAL"
    assert game["checks"]["has_nonblank_frame"] is True
    assert game["evidence"]["frames"][0]["sha256"] == "1" * 64
    assert len(game["evidence"]["binary_sha256"]) == 64
    assert game["evidence"]["log"]["exists"] is True
    assert report["counts"]["PASS"] == 0
    assert sum(report["counts"].values()) == len(report["games"])
    assert "no original-windows execution oracle" in " ".join(report["limits"]).lower()


def test_build_report_reads_results_shape_without_frames(tmp_path):
    results = {"schema": 1, "runs": [
        {"game": "Noiz2sa", "input_sha256": "a" * 64, "verification": "partial",
         "status": "launched, no oracle", "run_status": "exited", "exit_code": 0,
         "port_details": "6615 fns recovered", "evidence": "projects/x/report.html"},
        {"game": "P47", "input_sha256": "b" * 64, "verification": "launch-failed",
         "status": "unhandled guest C++ exception", "run_status": "aborted",
         "exit_code": 3, "port_details": "3932 fns recovered"},
    ]}
    report = cr.build_report(None, None, results, None, label="results smoke")
    verdicts = {g["game"]: g["verdict"] for g in report["games"]}
    assert verdicts == {"Noiz2sa": "PARTIAL", "P47": "FAIL"}
    assert report["games"][0]["evidence"]["input_sha256"] == "a" * 64
    assert report["counts"]["PASS"] == 0


def test_main_writes_report_and_rejects_missing_evidence(tmp_path):
    summary = make_summary(tmp_path, "cli-game")
    s_path = tmp_path / "summary.json"
    s_path.write_text(json.dumps(summary))
    out = tmp_path / "out" / "report.json"
    assert cr.main(["--summary", str(s_path), "--out", str(out),
                    "--note", "cli smoke"]) == 0
    data = json.loads(out.read_text())
    assert data["label"] == "CLI-supplied corpus evidence"
    assert data["note"] == "cli smoke"
    assert data["sources"]["summary"] == str(s_path)
    assert cr.main(["--out", str(out)]) == 2
    assert cr.main(["--summary", str(tmp_path / "missing.json"),
                    "--out", str(out)]) == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
