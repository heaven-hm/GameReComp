"""Synthetic coverage for the size-tier evidence report.

The fixtures here are invented *records*, not invented screenshots: each frame
only gets a hash when a matching synthetic frame-proof row exists, and the
blank-frame case is checked explicitly.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from tools.size_tier_report import analyse, build_report, main, parse_size

SMALL_MAX = 1_000
MEDIUM_MAX = 2_000

SHA_SMALL = "a" * 64
SHA_BLANK = "b" * 64
SHA_GHOST_PROOF_ONLY = "c" * 64
SHA_STAT = "d" * 64
SHA_BINARY = "f" * 64

FRAME_SMALL = "/run/alpha-small/screenshots/frame-0000.bmp"
FRAME_BLANK = "/run/beta-medium/screenshots/frame-0000.bmp"
FRAME_GHOST = "/run/epsilon-ghost/screenshots/frame-0000.bmp"
FRAME_STAT = "/run/eta-stat/screenshots/frame-0000.bmp"
FRAME_GHOST_PROOF_ONLY = "/run/zeta-not-in-summary/screenshots/frame-0000.bmp"


def rec(name, *, size=None, binary=None, frames=(), exit_code=0,
        status=None, reason=None, evidence=None, binary_sha256=None):
    row = {
        "game": name,
        "binary": binary if binary is not None else f"/bin/{name}",
        "exit_code": exit_code,
        "frames": [{"path": p} for p in frames],
        "frame_count": len(frames),
        "evidence": evidence if evidence is not None else f"/logs/{name}",
    }
    if size is not None:
        row["binary_bytes"] = size
    if status:
        row["status"] = status
    if reason:
        row["reason"] = reason
    if binary_sha256:
        row["binary_sha256"] = binary_sha256
    return row


def proof_row(game, path, *, sha, colours, blank=False, error=None):
    row = {"game": game, "path": path, "sha256": sha,
           "distinct_colors": colours, "blank": blank}
    if error:
        row["error"] = error
    return row


def game_section(report, name):
    """Return the Markdown subsection for one game heading."""
    marker = f"### `{name}`"
    start = report.index(marker)
    end = report.find("\n### ", start + len(marker))
    if end == -1:
        end = report.find("\n## ", start + len(marker))
    return report[start:end if end != -1 else len(report)]


class SizeTierReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.stat_binary = self.tmp / "eta-stat-bin"
        self.stat_binary.write_bytes(b"x" * 700)
        self.summary = {"schema": 1, "results": [
            rec("alpha-small", size=500, frames=[FRAME_SMALL],
                exit_code="TIMEOUT", binary_sha256=SHA_BINARY),
            rec("beta-medium", size=1_500, frames=[FRAME_BLANK],
                exit_code="TIMEOUT"),
            rec("gamma-big", size=3_000, frames=[], exit_code=-10),
            rec("delta-unknown", binary=None, frames=[], exit_code="BLOCKED",
                status="BLOCKED", reason="no native binary"),
            rec("epsilon-ghost", size=600, frames=[FRAME_GHOST]),
            rec("eta-stat", binary=str(self.stat_binary), frames=[FRAME_STAT]),
        ]}
        self.proof = {"schema": 1, "frames": [
            proof_row("alpha-small", FRAME_SMALL, sha=SHA_SMALL, colours=7),
            proof_row("beta-medium", FRAME_BLANK, sha=SHA_BLANK, colours=1, blank=True),
            proof_row("eta-stat", FRAME_STAT, sha=SHA_STAT, colours=5),
            # Proof for a frame the runner never recorded: must be ignored.
            proof_row("zeta-not-in-summary", FRAME_GHOST_PROOF_ONLY,
                      sha=SHA_GHOST_PROOF_ONLY, colours=9),
        ]}
        self.report = self.make_report()

    def make_report(self, summary=None, proof=Ellipsis, small_max=SMALL_MAX,
                    medium_max=MEDIUM_MAX):
        use_proof = self.proof if proof is Ellipsis else proof
        return build_report(
            self.summary if summary is None else summary,
            use_proof,
            small_max=small_max, medium_max=medium_max,
            summary_label="synthetic-summary.json",
            proof_label="synthetic-frame-proof.json" if use_proof else None)

    def test_tiers_group_by_actual_binary_size(self):
        games = analyse(self.summary, self.proof,
                        small_max=SMALL_MAX, medium_max=MEDIUM_MAX)
        tiers = {g["game"]: g["tier"] for g in games}
        self.assertEqual(tiers, {
            "alpha-small": "small",
            "beta-medium": "medium",
            "gamma-big": "big",
            "delta-unknown": "unknown",
            "epsilon-ghost": "small",
            "eta-stat": "small",
        })
        for heading in ("## Tier: small", "## Tier: medium", "## Tier: big",
                        "## Tier: unknown size"):
            self.assertIn(heading, self.report)

    def test_exact_hashes_and_colour_counts_are_verbatim(self):
        alpha = game_section(self.report, "alpha-small")
        self.assertIn(SHA_SMALL, alpha)
        self.assertIn("7 distinct colours", alpha)
        self.assertIn("500 B", alpha)
        eta = game_section(self.report, "eta-stat")
        self.assertIn(SHA_STAT, eta)
        self.assertIn("5 distinct colours", eta)
        self.assertIn("stat of binary path", eta)

    def test_blank_frame_is_marked_and_its_game_fails(self):
        beta = game_section(self.report, "beta-medium")
        self.assertIn(SHA_BLANK, beta)
        self.assertIn("BLANK", beta)
        self.assertIn("single-colour (1)", beta)
        self.assertIn("**FAIL**", beta)
        self.assertIn("every captured frame is BLANK", beta)

    def test_no_frames_is_fail_and_invents_no_screenshot(self):
        gamma = game_section(self.report, "gamma-big")
        self.assertIn("**FAIL**", gamma)
        self.assertIn("no frames captured", gamma)
        self.assertIn("No frames recorded by the runner", gamma)
        self.assertNotIn("| Frame |", gamma)

    def test_missing_proof_is_unproven_and_never_gets_a_hash(self):
        ghost = game_section(self.report, "epsilon-ghost")
        self.assertIn("UNPROVEN", ghost)
        self.assertIn("no hash claimed", ghost)
        self.assertNotIn(SHA_GHOST_PROOF_ONLY, ghost)
        self.assertEqual(re.findall(r"\b[0-9a-f]{64}\b", ghost), [])

    def test_proof_only_frame_is_ignored(self):
        self.assertNotIn("zeta-not-in-summary", self.report)
        self.assertNotIn(SHA_GHOST_PROOF_ONLY, self.report)

    def test_blocked_game_has_unknown_size_and_fail_marker(self):
        blocked = game_section(self.report, "delta-unknown")
        self.assertIn("**FAIL**", blocked)
        self.assertIn("BLOCKED", blocked)
        self.assertIn("no native binary", blocked)
        self.assertIn("size unavailable", blocked)

    def test_logs_paths_are_explicit(self):
        alpha = game_section(self.report, "alpha-small")
        self.assertIn("`/logs/alpha-small/run.log`", alpha)
        self.assertIn("Frames directory: `/run/alpha-small/screenshots`", alpha)

    def test_thresholds_are_configurable(self):
        report = self.make_report(small_max=2_000, medium_max=4_000)
        self.assertIn("### `beta-medium`", game_section(report, "beta-medium"))
        games = analyse(self.summary, self.proof, small_max=2_000, medium_max=4_000)
        tiers = {g["game"]: g["tier"] for g in games}
        self.assertEqual(tiers["beta-medium"], "small")
        self.assertEqual(tiers["gamma-big"], "medium")

    def test_parse_size_units_and_bad_values(self):
        self.assertEqual(parse_size(4096), 4096)
        self.assertEqual(parse_size("4MiB"), 4 * 1024 ** 2)
        self.assertEqual(parse_size("4MB"), 4_000_000)
        self.assertEqual(parse_size("1KiB"), 1024)
        for bad in ("", "abc", "4ZB", -1, "4 MiB extra"):
            with self.assertRaises(ValueError):
                parse_size(bad)

    def test_cli_writes_report_and_rejects_bad_thresholds(self):
        summary_path = self.tmp / "summary.json"
        proof_path = self.tmp / "frame-proof.json"
        out_path = self.tmp / "report.md"
        summary_path.write_text(json.dumps(self.summary))
        proof_path.write_text(json.dumps(self.proof))
        self.assertEqual(main([
            "--summary", str(summary_path),
            "--frame-proof", str(proof_path),
            "--out", str(out_path),
            "--small-max", str(SMALL_MAX),
            "--medium-max", str(MEDIUM_MAX),
        ]), 0)
        text = out_path.read_text()
        self.assertIn(SHA_SMALL, text)
        self.assertIn("BLANK", text)
        self.assertEqual(main([
            "--summary", str(summary_path),
            "--out", str(self.tmp / "never.md"),
            "--small-max", "12MiB", "--medium-max", "4MiB",
        ]), 2)

    def test_proof_error_is_an_explicit_fail_marker(self):
        summary = {"schema": 1, "results": [
            rec("err-game", size=500, frames=[FRAME_SMALL])]}
        proof = {"schema": 1, "frames": [
            proof_row("err-game", FRAME_SMALL, sha=SHA_SMALL, colours=7,
                      error="frame file missing")]}
        report = self.make_report(summary=summary, proof=proof)
        self.assertIn("FAIL — proof error: frame file missing", report)
        self.assertIn("**FAIL**", game_section(report, "err-game"))

    def test_no_frame_proof_claims_no_hashes(self):
        report = self.make_report(proof=None)
        self.assertIn("Frame proof: **not supplied**", report)
        self.assertNotIn(SHA_SMALL, report)
        alpha = game_section(report, "alpha-small")
        self.assertIn("UNPROVEN", alpha)
        self.assertIn("**FAIL**", alpha)


if __name__ == "__main__":
    unittest.main()
