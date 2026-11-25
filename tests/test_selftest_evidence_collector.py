"""Tests for tools.selftest_evidence_collector: counts, refusals, no promotion.

Run with: python3 -m pytest tests/test_selftest_evidence_collector.py -q

Every log here is synthetic and written under tmp_path. The cases cover the
real build_selftests.sh status grammar, the misleading per-check ``ok`` lines
inside a failing selftest's output, fail-closed handling of empty/malformed
input, summary-vs-log tamper detection, and the invariant that a clean build
or unit test never becomes a game PASS.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools import selftest_evidence_collector as sec

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_LOG = (
    "ok    alpha_selftest.c\n"
    "skip  beta_selftest.c (no OpenGL context)\n"
    "FAIL  gamma_selftest.c\n"
    "ok   DirectDrawCreate -> DD_OK\n"          # per-check line, not a result
    "ok   the surface was created with bpp 16\n"  # likewise
    "BUILD FAIL  delta_selftest.c\n"
    "skip  zeta_selftest.c (SDL2_ttf missing)\n"
    "ok    eta_selftest.c\n"
)


def write_log(tmp_path: Path, text: str, name: str = "selftests.log") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def write_summary(tmp_path: Path, data: object, name: str = "summary.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_summary(events: list[dict], *, counts: object = None,
                 **claims: object) -> dict:
    data: dict = {"schema_version": 1,
                  "events": [dict(event) for event in events]}
    if counts is not None:
        data["counts"] = counts
    data.update(claims)
    return data


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tools.selftest_evidence_collector", *args],
        cwd=ROOT, capture_output=True, text=True, check=False)


def sample_events() -> list[dict]:
    return sec.parse_log_text(SAMPLE_LOG)


class TestCountsFromLogs:
    def test_counts_are_deterministic_and_rederived(self, tmp_path: Path):
        log = write_log(tmp_path, SAMPLE_LOG)
        result = run_cli("--log", str(log), "--out", str(tmp_path / "r.json"))
        assert result.returncode == 0, result.stderr
        record = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))

        assert record["counts"] == {
            "ok": 2, "FAIL": 1, "BUILD FAIL": 1, "skip": 2, "total": 6}
        assert record["outcome"] == "FAILED"
        assert [event["name"] for event in record["events"]] == sorted(
            event["name"] for event in record["events"])
        assert [e["status"] for e in record["events"]].count("BUILD FAIL") == 1
        assert record["source"]["sha256"] == hashlib.sha256(
            log.read_bytes()).hexdigest()
        assert record["source"]["kind"] == "log"
        assert sec.validate_record(record) == []

    def test_per_check_ok_lines_never_inflate_the_ok_count(self, tmp_path: Path):
        # A failing selftest dumps its own output; those 'ok' lines are checks,
        # not selftest results, and must not turn FAIL into a pass count.
        log = write_log(tmp_path, SAMPLE_LOG)
        data = json.loads(run_cli("--log", str(log)).stdout)
        assert data["counts"]["ok"] == 2  # alpha and eta, not the four 'ok   ' lines
        assert data["counts"]["FAIL"] == 1
        assert all(not e["name"].endswith("DD_OK")
                   for e in data["events"])

    def test_crlf_does_not_change_parsed_events(self):
        crlf = SAMPLE_LOG.replace("\n", "\r\n")
        assert sec.parse_log_text(crlf) == sample_events()

    def test_repeated_cli_runs_are_byte_identical(self, tmp_path: Path):
        log = write_log(tmp_path, SAMPLE_LOG)
        a, b = tmp_path / "a.json", tmp_path / "b.json"
        assert run_cli("--log", str(log), "--out", str(a)).returncode == 0
        assert run_cli("--log", str(log), "--out", str(b)).returncode == 0
        assert a.read_bytes() == b.read_bytes()
        assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}",
                             a.read_text(encoding="utf-8"))
        text = a.read_text(encoding="utf-8")
        parsed = json.loads(text)
        assert text == json.dumps(parsed, indent=2, sort_keys=True,
                                  ensure_ascii=True) + "\n"

    def test_cli_prints_json_when_no_out_is_given(self, tmp_path: Path):
        log = write_log(tmp_path, SAMPLE_LOG)
        result = run_cli("--log", str(log))
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["counts"]["total"] == 6
        assert result.stderr == ""


class TestSkipReasons:
    def test_every_skip_carries_its_verbatim_reason(self, tmp_path: Path):
        log = write_log(tmp_path, SAMPLE_LOG)
        data = json.loads(run_cli("--log", str(log)).stdout)
        assert data["skips"] == [
            {"name": "beta_selftest.c", "reason": "no OpenGL context"},
            {"name": "zeta_selftest.c", "reason": "SDL2_ttf missing"},
        ]
        by_name = {e["name"]: e for e in data["events"]}
        assert by_name["alpha_selftest.c"]["reason"] is None
        assert by_name["beta_selftest.c"]["status"] == "skip"

    @pytest.mark.parametrize("line", [
        "skip  beta_selftest.c\n",
        "skip  beta_selftest.c ()\n",
    ])
    def test_skip_without_a_reason_is_refused(self, tmp_path: Path, line: str):
        log = write_log(tmp_path, "ok    alpha_selftest.c\n" + line)
        result = run_cli("--log", str(log), "--out",
                         str(tmp_path / "must-not-exist.json"))
        assert result.returncode == 2
        assert "no reason" in result.stderr
        assert not (tmp_path / "must-not-exist.json").exists()


class TestFailClosed:
    @pytest.mark.parametrize("text", [
        "",
        "   \n\n",
        "=== ddraw_selftest.c ===\nRC=0\nok   a check passed\n",
    ])
    def test_empty_or_summary_free_log_is_refused(self, tmp_path: Path,
                                                   text: str):
        log = write_log(tmp_path, text)
        result = run_cli("--log", str(log), "--out",
                         str(tmp_path / "no.json"))
        assert result.returncode == 2
        assert ("empty log" in result.stderr
                or "no ok/FAIL/BUILD FAIL/skip summary lines" in result.stderr)
        assert not (tmp_path / "no.json").exists()

    def test_non_utf8_or_nul_bytes_are_refused(self, tmp_path: Path):
        binary = tmp_path / "binary.log"
        binary.write_bytes(b"ok    alpha_selftest.c\n\xff\xfe\x00")
        result = run_cli("--log", str(binary))
        assert result.returncode == 2
        assert "log is not UTF-8" in result.stderr or "control character" in result.stderr

        nul = tmp_path / "nul.log"
        nul.write_bytes(b"ok    alpha_selftest.c\x00\n")
        assert run_cli("--log", str(nul)).returncode == 2

    @pytest.mark.parametrize("line", [
        "ok    alpha_selftest.c(reason)\n",   # near-miss summary line
        "PASS  alpha_selftest.c\n",            # unrecognised status token
        "skip  alpha_selftest.c (outer (inner))\n",
    ])
    def test_malformed_status_line_is_refused(self, tmp_path: Path, line: str):
        log = write_log(tmp_path, line)
        result = run_cli("--log", str(log))
        assert result.returncode == 2
        assert result.stderr

    def test_game_pass_claim_in_log_is_refused(self, tmp_path: Path):
        log = write_log(tmp_path, "ok    alpha_selftest.c\nverdict: PASS\n")
        result = run_cli("--log", str(log), "--out", str(tmp_path / "no.json"))
        assert result.returncode == 2
        assert "no false" in result.stderr or "cannot promote" in result.stderr
        assert not (tmp_path / "no.json").exists()

    def test_cli_requires_an_input(self):
        result = run_cli()
        assert result.returncode == 2
        assert "--log" in result.stderr


class TestTamperDetection:
    def test_conflicting_duplicate_test_is_refused(self, tmp_path: Path):
        log = write_log(tmp_path, "ok    alpha_selftest.c\n"
                                  "FAIL  alpha_selftest.c\n")
        result = run_cli("--log", str(log))
        assert result.returncode == 2
        assert "duplicate" in result.stderr and "contradictory" in result.stderr

    def test_forged_ok_for_a_failing_test_is_refused(self, tmp_path: Path):
        # build_selftests.sh prints the failing test's tail after FAIL; a
        # planted summary-shaped 'ok' there must not rescue the test.
        log = write_log(tmp_path, "FAIL  alpha_selftest.c\n"
                                  "ok   a passing internal check\n"
                                  "ok    alpha_selftest.c\n")
        result = run_cli("--log", str(log))
        assert result.returncode == 2
        assert "duplicate" in result.stderr

    def test_summary_counts_are_recomputed_not_trusted(self, tmp_path: Path):
        events = sec.parse_log_text("ok    alpha_selftest.c\n"
                                    "skip  beta_selftest.c (no display)\n")
        summary = write_summary(tmp_path, make_summary(
            events, counts={"ok": 99, "FAIL": 0, "BUILD FAIL": 0,
                            "skip": 0, "total": 99}))
        result = run_cli("--summary", str(summary))
        assert result.returncode == 2
        assert "counts['ok']=99 disagrees" in result.stderr

    @pytest.mark.parametrize("claim", [
        {"verdict": "PASS"},
        {"game_verdict": "PASS"},
        {"game_pass": True},
        {"promotion": "PASS"},
        {"promoted": True},
        {"counts": {"ok": 1, "PASS": 1}},
    ])
    def test_summary_pass_claims_are_refused(self, tmp_path: Path,
                                             claim: dict):
        events = sec.parse_log_text("ok    alpha_selftest.c\n")
        summary = write_summary(tmp_path, make_summary(events, **claim))
        result = run_cli("--summary", str(summary))
        assert result.returncode == 2
        assert "no false game promotion" in result.stderr or "unknown key" in result.stderr

    def test_stale_summary_is_checked_against_log_bytes(self, tmp_path: Path):
        log = write_log(tmp_path, SAMPLE_LOG)
        stale = sec.parse_log_text("ok    alpha_selftest.c\n")
        summary = write_summary(tmp_path, make_summary(stale))
        result = run_cli("--log", str(log), "--summary", str(summary))
        assert result.returncode == 2
        assert "summary events disagree with log events" in result.stderr

    def test_bad_log_hash_claim_is_refused(self, tmp_path: Path):
        log = write_log(tmp_path, SAMPLE_LOG)
        summary = write_summary(tmp_path, make_summary(
            sample_events(), log_sha256="0" * 64))
        result = run_cli("--log", str(log), "--summary", str(summary))
        assert result.returncode == 2
        assert "does not match the log bytes" in result.stderr

    def test_expect_sha256_mismatch_is_refused(self, tmp_path: Path):
        log = write_log(tmp_path, SAMPLE_LOG)
        result = run_cli("--log", str(log), "--expect-sha256", "f" * 64)
        assert result.returncode == 2
        assert "tampered" in result.stderr
        good = hashlib.sha256(log.read_bytes()).hexdigest()
        assert run_cli("--log", str(log), "--expect-sha256", good).returncode == 0

    def test_expect_sha256_without_log_is_refused(self, tmp_path: Path):
        summary = write_summary(tmp_path, make_summary(sample_events()))
        result = run_cli("--summary", str(summary), "--expect-sha256", "0" * 64)
        assert result.returncode == 2
        assert "requires --log" in result.stderr

    def test_verify_detects_tampered_record_and_sha(self, tmp_path: Path):
        log = write_log(tmp_path, SAMPLE_LOG)
        record_path = tmp_path / "record.json"
        assert run_cli("--log", str(log), "--out", str(record_path)).returncode == 0
        assert run_cli("--verify", str(record_path)).returncode == 0

        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["counts"]["ok"] += 1
        record["game_evidence"]["promotion"] = True
        tampered = tmp_path / "tampered.json"
        tampered.write_text(json.dumps(record), encoding="utf-8")
        result = run_cli("--verify", str(tampered))
        assert result.returncode == 1
        assert "disagrees with events" in result.stderr
        assert "promotion must be false" in result.stderr

        record["counts"]["ok"] -= 1
        record["source"]["sha256"] = "0" * 64
        tampered.write_text(json.dumps(record), encoding="utf-8")
        result = run_cli("--verify", str(tampered), "--log", str(log))
        assert result.returncode == 1
        assert "sha256" in result.stderr


class TestNoFalsePromotion:
    @pytest.mark.parametrize("via", ["log", "summary"])
    def test_all_ok_evidence_still_has_no_game_pass(self, tmp_path: Path,
                                                     via: str):
        text = "ok    alpha_selftest.c\nok    beta_selftest.c\n"
        if via == "log":
            args = ["--log", str(write_log(tmp_path, text))]
        else:
            events = sec.parse_log_text(text)
            args = ["--summary", str(write_summary(
                tmp_path, make_summary(events, counts={
                    "ok": 2, "FAIL": 0, "BUILD FAIL": 0, "skip": 0,
                    "total": 2})))]
        result = run_cli(*args)
        assert result.returncode == 0, result.stderr
        record = json.loads(result.stdout)
        assert record["counts"]["ok"] == 2
        assert record["outcome"] == "CLEAN"
        assert record["game_evidence"]["verdict"] == "NOT TESTED"
        assert record["game_evidence"]["promotion"] is False
        assert record["counts"].get("PASS", 0) == 0

        def values(node: object):
            if isinstance(node, dict):
                for value in node.values():
                    yield from values(value)
            elif isinstance(node, list):
                for value in node:
                    yield from values(value)
            else:
                yield node

        assert not any(isinstance(v, str) and v.upper() == "PASS"
                       for v in values(record))

    def test_strict_only_reports_selftest_failures(self, tmp_path: Path):
        failing = write_log(tmp_path, SAMPLE_LOG)
        result = run_cli("--log", str(failing), "--strict")
        assert result.returncode == 1
        assert "FAIL" in result.stderr
        record = json.loads(result.stdout)
        assert record["game_evidence"]["verdict"] == "NOT TESTED"

        clean = write_log(tmp_path, "ok    alpha_selftest.c\n", "clean.log")
        assert run_cli("--log", str(clean), "--strict").returncode == 0


def test_module_entry_point_writes_record(tmp_path: Path):
    log = write_log(tmp_path, SAMPLE_LOG)
    out = tmp_path / "nested" / "record.json"
    result = subprocess.run(
        [sys.executable, "-m", "tools.selftest_evidence_collector",
         "--log", str(log), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["counts"]["total"] == 6
