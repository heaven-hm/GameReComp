"""Regression tests for evidence-backed game-matrix rows.

A row must never claim PASS/PARTIAL unless its ``evidence`` field names a
proof file that is checked into this repository. The previous generator
counted git-ignored ``projects/**/reports/report.html`` paths as evidence and
printed a "93 / 120 run natively" headline built from those same unverified
rows.
"""

import json
from pathlib import Path

from tools import gamesdb

ROOT = Path(gamesdb.__file__).resolve().parents[1]
RESULTS_PATH = ROOT / "reports" / "games" / "results.json"


def _runs(path=RESULTS_PATH):
    return json.loads(Path(path).read_text())["runs"]


def _tracked():
    return gamesdb._tracked_files(ROOT)


def test_pass_and_partial_rows_have_committed_evidence():
    tracked = _tracked()
    offenders = []
    for row in _runs():
        if (row.get("verification") or "not-run") not in \
                gamesdb.EVIDENCE_PASS_STATUSES:
            continue
        rel = gamesdb.evidence_path(row, ROOT)
        if (not rel or rel not in tracked or not (ROOT / rel).is_file()):
            offenders.append(f"{row.get('game')} ({row.get('filename')}): "
                             f"{row.get('evidence')!r}")
    assert offenders == [], "PASS/PARTIAL rows without committed proof:\n" + \
        "\n".join(offenders)


def test_audit_helper_agrees_with_the_data():
    assert gamesdb.audit_evidence(_runs(), ROOT) == []


def test_frame_proof_column_comes_only_from_committed_frame_proofs():
    proofs = gamesdb.load_frame_proofs()
    assert len(proofs) == 2
    cells = {name: gamesdb.frame_proof_for(proofs, name) for name in (
        "Doukutsu.exe", "tetris-win-32-en.exe", "chocolate-doom.exe",
        "igi.exe", "gr.exe")}
    assert cells["Doukutsu.exe"] == "2/3 non-blank frames"
    assert cells["tetris-win-32-en.exe"] == "2/2 non-blank frames"
    # Chocolate Doom has an evidence.json (imports/no WAD), not a frame proof.
    assert cells["chocolate-doom.exe"] == "-"
    # A committed splash screenshot is not a frame-proof report.
    assert cells["igi.exe"] == "-"


def test_generator_demotes_a_claim_without_committed_evidence(tmp_path):
    bad = [{
        "game": " Fake Game", "filename": "fake.exe",
        "verification": "scenario-pass", "status": "claimed PASS",
        "evidence": "projects/fake/reports/report.html",
        "detail": "no proof",
    }]
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"schema": 1, "runs": bad}))
    text = gamesdb.render_matrix(results_path=results)
    assert "NOT RUN - no committed evidence" in text
    assert "PASS - scenario verified" not in text


def test_matrix_headline_no_longer_counts_unverified_local_runs():
    text = gamesdb.render_matrix()
    assert "93 / 120" not in text
    assert "**Evidence-backed results:**" in text


def test_known_misclassifications_stay_demoted_or_failed():
    text = gamesdb.render_matrix()
    igi = [line for line in text.splitlines() if line.startswith("| IGI |")]
    assert len(igi) == 1 and igi[0].startswith("| IGI | Windows |")
    assert "NOT RUN" in igi[0] and "PARTIAL" not in igi[0]
    for row in ("Gunroar (gr.exe)", "Parsec47 (p47.exe)",
                "Torus Trooper (tt.exe)", "Tumiki Fighters (tf.exe)"):
        line = next(line for line in text.splitlines()
                    if line.startswith(f"| {row} |"))
        assert "FAIL - rebuilt process cannot boot" in line
        assert "PARTIAL" not in line
