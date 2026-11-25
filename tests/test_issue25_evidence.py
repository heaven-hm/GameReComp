"""Tests for tools.issue25_evidence: issue #25 acceptance-evidence auditing.

Every input is synthetic and CLI-supplied. No test runs a provider, a lifter,
a differential harness, gh, or the network. The suite proves each acceptance
criterion gate, that a linked report (not a boolean claim) drives each verdict,
that hashes are recomputed, that tampering is detected, and that the tool's
output is deterministic and free of GitHub auto-closing keywords.

Run with: python3 -m pytest tests/test_issue25_evidence.py -q
"""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import issue25_evidence as i25  # noqa: E402

TOOL = ROOT / "tools" / "issue25_evidence.py"
CLOSING_RE = i25.CLOSING_KEYWORD


def seal(data):
    """Return a copy of a JSON record with a valid self-seal."""
    result = dict(data)
    result["sha256"] = i25.canonical_digest(result)
    return result


def wjson(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def rewrite(path, obj, *, reseal=True):
    return wjson(path, seal(obj) if reseal else obj)


def lifter(name, status, *, diverged=0, total=4):
    return {"name": name, "status": status, "verdict": "agree" if status == "PASS" else "diverge",
            "diverged_cases": diverged, "total_cases": total, "reasons": []}


def selection(decision, chosen, lifters):
    return {"decision": decision, "chosen_lifter": chosen, "lifters": lifters,
            "reasons": ["synthetic selection"]}


def gate_report(*, checks=None, decisions=None, ok=True):
    names = [
        "named-memory-from-original-bytes", "reference-flags-nonvacuous",
        "flag-break-hidden-when-flags-excluded", "flag-break-detected-when-flags-compared",
        "memory-break-detected-at-named-address", "clean-body-agrees",
        "multilifter-promotes-only-clean-body",
        "pointer-break-detected", "stack-purge-break-detected", "callback-break-detected",
    ]
    return {
        "tool": "tests/diff/differential_gate.py",
        "ok": ok,
        "checks": [{"name": name, "ok": True, "detail": "synthetic check"}
                   for name in (names if checks is None else checks)],
        "decisions": ({"clean_body": "PROMOTE", "pointer_bug": "FAILED",
                       "stack_bug": "FAILED", "callback_bug": "FAILED"}
                      if decisions is None else decisions),
        "fixture": {"entry": "0x00401000", "cases": 8},
    }


def evidence(criterion, **fields):
    record = {"schema_version": 1, "criterion": criterion}
    record.update(fields)
    return seal(record)


def build_fixture(tmp):
    """Write a complete, self-consistent evidence set: this must be CLOSABLE."""
    tmp = Path(tmp)
    selections = {
        "revng_pass": selection("PROMOTE", "revng", [
            lifter("revng", "PASS"), lifter("pcrecomp", "PASS")]),
        "fallback": selection("PROMOTE", "pcrecomp", [
            lifter("revng", "DIVERGED", diverged=1), lifter("pcrecomp", "PASS")]),
        "both_fail": selection("FAILED", None, [
            lifter("revng", "DIVERGED", diverged=1),
            lifter("pcrecomp", "UNVERIFIED", diverged=0, total=0)]),
    }
    sel_paths = {name: wjson(tmp / f"{name}.json", seal(data))
                 for name, data in selections.items()}

    gate_data = gate_report()
    gate_path = wjson(tmp / "gate_main.json", seal(gate_data))
    gate_repeat_path = wjson(tmp / "gate_repeat.json", seal(gate_data))

    baseline = tmp / "baseline.json"
    default_run = tmp / "default_run.json"
    baseline.write_bytes(b'{"default_pipeline": "unchanged"}\n')
    default_run.write_bytes(baseline.read_bytes())
    digest = i25.sha256_file(baseline)

    records = [
        evidence("revng_selection", selection="revng_pass"),
        evidence("pcrecomp_fallback_after_revng_fail", selection="fallback",
                 fallback_reason="revng diverged on case 0"),
        evidence("both_fail_unresolved", selection="both_fail",
                 classification="UNRESOLVED", verified=False, label="unresolved"),
        evidence("negative_pointer", diff_gate="gate_main",
                 check="pointer-break-detected"),
        evidence("negative_stack", diff_gate="gate_main",
                 check="stack-purge-break-detected"),
        evidence("negative_callback", diff_gate="gate_main",
                 check="callback-break-detected"),
        evidence("determinism", runs=["gate_main", "gate_repeat"], repeat_count=2),
        evidence("opt_in_no_default_regression", flag="ISSUE25_MULTILIFTER",
                 flag_default=False, default_pipeline_unchanged=True,
                 baseline={"path": baseline.name, "sha256": digest},
                 default_run={"path": default_run.name, "sha256": digest}),
        evidence("source_behavior_separation", diff_gate="gate_main",
                 source_quality={"verdict": "PASS", "basis": "static review"},
                 behavior_quality={"verdict": "SCOPED_PASS",
                                   "basis": "differential_gate",
                                   "scope": "8 seeded cases at 0x00401000",
                                   "claims_full_game_equivalence": False}),
    ]
    ev_path = wjson(tmp / "issue25_evidence.json", records)
    return SimpleNamespace(
        tmp=tmp, selections=selections, sel_paths=sel_paths, gate_data=gate_data,
        gate_path=gate_path, gate_repeat_path=gate_repeat_path,
        baseline=baseline, default_run=default_run, digest=digest, records=records,
        ev_path=ev_path,
    )


def run_cli(fx, *, evidence_path=None, selections=None, gates=None, omit_evidence=False):
    out = fx.tmp / "audit.md"
    json_out = fx.tmp / "audit.json"
    for path in (out, json_out):
        if path.exists():
            path.unlink()
    cmd = [sys.executable, str(TOOL)]
    for name, path in (selections or fx.sel_paths).items():
        cmd += ["--selection", f"{name}={path}"]
    for name, path in (gates or {"gate_main": fx.gate_path,
                                 "gate_repeat": fx.gate_repeat_path}).items():
        cmd += ["--diff-gate", f"{name}={path}"]
    if not omit_evidence:
        cmd += ["--evidence", str(evidence_path or fx.ev_path)]
    cmd += ["--json", str(json_out), "--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(fx.tmp))
    report = json.loads(json_out.read_text(encoding="utf-8")) if json_out.is_file() else None
    return proc, report, out, json_out


def codes(report, criterion):
    row = next(r for r in report["criteria"] if r["criterion"] == criterion)
    return row["reason_codes"], row["verdict"]


def rewrite_evidence(fx, index, **changes):
    """Change a claim and reseal it: this tests the criterion, not tamper detection."""
    records = [dict(r) for r in fx.records]
    records[index].update(changes)
    records[index] = seal(records[index])
    return wjson(fx.tmp / "tampered_evidence.json", records)


class TestFullSuite:
    """A CLOSABLE fixture and the invariants attached to it."""

    def test_all_criteria_pass_with_consistent_synthetic_reports(self, tmp_path):
        fx = build_fixture(tmp_path)
        proc, report, out, json_out = run_cli(fx)
        assert proc.returncode == 0, proc.stderr
        assert report["overall"] == "CLOSABLE"
        assert report["counts"] == {"PASS": 9, "FAIL": 0}
        assert [r["criterion"] for r in report["criteria"]] == list(i25.CRITERIA)
        assert all(r["verdict"] == "PASS" for r in report["criteria"])
        assert out.is_file() and json_out.is_file()
        assert not CLOSING_RE.search(json_out.read_text(encoding="utf-8"))
        assert not CLOSING_RE.search(out.read_text(encoding="utf-8"))
        assert "no issue state" in proc.stdout

    def test_hashes_are_recomputed_and_recorded_for_every_input(self, tmp_path):
        fx = build_fixture(tmp_path)
        _, report, _, _ = run_cli(fx)
        for name, spec in report["inputs"]["selections"].items():
            assert spec["sha256"] == i25.sha256_file(fx.sel_paths[name])
        assert report["inputs"]["diff_gates"]["gate_main"]["sha256"] == i25.sha256_file(fx.gate_path)
        assert report["inputs"]["evidence"][0]["sha256"] == i25.sha256_file(fx.ev_path)
        observed = next(r for r in report["criteria"]
                        if r["criterion"] == "opt_in_no_default_regression")["observed"]
        assert observed["artifacts"]["baseline"] == fx.digest
        assert observed["artifacts"]["default_run"] == fx.digest

    def test_output_is_deterministic(self, tmp_path):
        fx = build_fixture(tmp_path)
        proc1, _, out1, json1 = run_cli(fx)
        proc2, _, out2, json2 = run_cli(fx)
        assert proc1.returncode == proc2.returncode == 0
        assert json1.read_bytes() == json2.read_bytes()
        assert out1.read_bytes() == out2.read_bytes()

    def test_command_line_reproduces_the_audit(self, tmp_path):
        fx = build_fixture(tmp_path)
        _, report, _, _ = run_cli(fx)
        assert report["commands"]["exact"].startswith(
            "python3 tools/issue25_evidence.py --selection ")
        assert "--evidence " in report["commands"]["exact"]

    def test_no_evidence_is_open_for_every_criterion(self, tmp_path):
        fx = build_fixture(tmp_path)
        proc, report, _, _ = run_cli(fx, omit_evidence=True)
        assert proc.returncode == 1
        assert report["overall"] == "OPEN"
        assert report["counts"] == {"PASS": 0, "FAIL": 9}
        assert all("NO_EVIDENCE" in codes(report, c)[0] for c in i25.CRITERIA)
        assert "no --evidence" in " ".join(report["input_errors"])

    def test_usage_error_has_no_report_and_exit_two(self, tmp_path):
        fx = build_fixture(tmp_path)
        proc = subprocess.run([sys.executable, str(TOOL), "--selection",
                               f"revng_pass={fx.sel_paths['revng_pass']}"],
                              capture_output=True, text=True, cwd=str(tmp_path))
        assert proc.returncode == 2
        assert "--json" in proc.stderr

    def test_tool_does_not_import_network_or_gh_clients(self):
        for name in ("subprocess", "socket", "requests", "urllib"):
            assert not hasattr(i25, name), f"auditor must not import {name}"
        assert "gh issue" not in Path(TOOL).read_text(encoding="utf-8")


class TestCriterionGate:
    """Each acceptance criterion must fail when its specific evidence lies."""

    def test_revng_must_be_the_selected_lifter(self, tmp_path):
        fx = build_fixture(tmp_path)
        data = dict(fx.selections["revng_pass"])
        data["chosen_lifter"] = "pcrecomp"  # revng no longer selected
        rewrite(fx.sel_paths["revng_pass"], data)
        _, report, _, _ = run_cli(fx)
        assert report["overall"] == "OPEN"
        code_list, verdict = codes(report, "revng_selection")
        assert verdict == "FAIL" and "REVNG_NOT_SELECTED" in code_list

    def test_revng_must_be_a_clean_pass_with_cases(self, tmp_path):
        fx = build_fixture(tmp_path)
        data = dict(fx.selections["revng_pass"])
        data["lifters"] = [
            lifter("revng", "PASS", diverged=1), lifter("pcrecomp", "PASS")]
        data["decision"] = "FAILED"
        data["chosen_lifter"] = None
        rewrite(fx.sel_paths["revng_pass"], data)
        _, report, _, _ = run_cli(fx)
        code_list, _ = codes(report, "revng_selection")
        assert "REVNG_DIVERGED_CASES" in code_list

    def test_pcrecomp_fallback_requires_revng_failure(self, tmp_path):
        fx = build_fixture(tmp_path)
        data = dict(fx.selections["fallback"])
        data["lifters"] = [lifter("revng", "PASS"), lifter("pcrecomp", "PASS")]
        rewrite(fx.sel_paths["fallback"], data)
        _, report, _, _ = run_cli(fx)
        code_list, _ = codes(report, "pcrecomp_fallback_after_revng_fail")
        assert "FALLBACK_WITHOUT_REVNG_FAILURE" in code_list

    def test_pcrecomp_fallback_requires_pcrecomp_to_pass(self, tmp_path):
        fx = build_fixture(tmp_path)
        data = dict(fx.selections["fallback"])
        data["decision"] = "FAILED"
        data["chosen_lifter"] = None
        data["lifters"] = [lifter("revng", "DIVERGED", diverged=1),
                           lifter("pcrecomp", "DIVERGED", diverged=1)]
        rewrite(fx.sel_paths["fallback"], data)
        _, report, _, _ = run_cli(fx)
        code_list, _ = codes(report, "pcrecomp_fallback_after_revng_fail")
        assert "PCRECOMP_NOT_SELECTED" in code_list
        assert "PCRECOMP_STATUS_NOT_PASS" in code_list

    def test_both_fail_is_unresolved_never_verified(self, tmp_path):
        fx = build_fixture(tmp_path)
        data = dict(fx.selections["both_fail"])
        data["decision"] = "PROMOTE"
        data["chosen_lifter"] = "revng"
        data["lifters"] = [lifter("revng", "PASS"), lifter("pcrecomp", "PASS")]
        rewrite(fx.sel_paths["both_fail"], data)
        _, report, _, _ = run_cli(fx)
        code_list, _ = codes(report, "both_fail_unresolved")
        assert "NOT_UNRESOLVED" in code_list
        assert "VERIFICATION_LABEL_PRESENT" in code_list

    def test_a_verified_boolean_claim_is_refused(self, tmp_path):
        fx = build_fixture(tmp_path)
        path = rewrite_evidence(fx, 2, verified=True)
        _, report, _, _ = run_cli(fx, evidence_path=path)
        code_list, _ = codes(report, "both_fail_unresolved")
        assert "VERIFICATION_LABEL_PRESENT" in code_list

    def test_wrong_pointer_stack_callback_break_must_be_detected(self, tmp_path):
        for criterion, check in (("negative_pointer", "pointer-break-detected"),
                                 ("negative_stack", "stack-purge-break-detected"),
                                 ("negative_callback", "callback-break-detected")):
            fx = build_fixture(tmp_path / criterion)
            data = json.loads(json.dumps(fx.gate_data))
            for row in data["checks"]:
                if row["name"] == check:
                    row["ok"] = False
            data["ok"] = False
            rewrite(fx.gate_path, data)
            _, report, _, _ = run_cli(fx)
            code_list, _ = codes(report, criterion)
            assert "NEGATIVE_CHECK_FAILED" in code_list, f"{criterion}: {code_list}"

    def test_negative_check_must_be_named_in_the_gate(self, tmp_path):
        fx = build_fixture(tmp_path)
        path = rewrite_evidence(fx, 3, check="clean-body-agrees")
        _, report, _, _ = run_cli(fx, evidence_path=path)
        code_list, _ = codes(report, "negative_pointer")
        assert "NEGATIVE_CHECK_WRONG" in code_list

    def test_negative_criterion_without_a_failed_decision_is_refused(self, tmp_path):
        fx = build_fixture(tmp_path)
        data = dict(fx.gate_data)
        data["decisions"] = {"clean_body": "PROMOTE"}
        rewrite(fx.gate_path, data)
        _, report, _, _ = run_cli(fx)
        for criterion in ("negative_pointer", "negative_stack", "negative_callback"):
            assert "NEGATIVE_CANDIDATE_NOT_REJECTED" in codes(report, criterion)[0]

    def test_determinism_needs_byte_identical_repeated_runs(self, tmp_path):
        fx = build_fixture(tmp_path)
        fx.gate_repeat_path.write_text(
            json.dumps(fx.gate_data, indent=4, sort_keys=True) + "\n", encoding="utf-8")
        _, report, _, _ = run_cli(fx)
        code_list, _ = codes(report, "determinism")
        assert "DETERMINISM_DIGEST_MISMATCH" in code_list

    def test_determinism_needs_two_distinct_runs(self, tmp_path):
        fx = build_fixture(tmp_path)
        path = rewrite_evidence(fx, 6, runs=["gate_main"], repeat_count=1)
        _, report, _, _ = run_cli(fx, evidence_path=path)
        code_list, _ = codes(report, "determinism")
        assert "DETERMINISM_TOO_FEW_RUNS" in code_list
        assert "DETERMINISM_COUNT_MISMATCH" in code_list

    def test_opt_in_flag_must_default_to_off(self, tmp_path):
        fx = build_fixture(tmp_path)
        path = rewrite_evidence(fx, 7, flag_default=True)
        _, report, _, _ = run_cli(fx, evidence_path=path)
        code_list, _ = codes(report, "opt_in_no_default_regression")
        assert "FEATURE_FLAG_DEFAULT_NOT_FALSE" in code_list

    def test_default_pipeline_must_match_baseline_bytes(self, tmp_path):
        fx = build_fixture(tmp_path)
        fx.default_run.write_bytes(b'{"default_pipeline": "changed"}\n')
        _, report, _, _ = run_cli(fx)
        code_list, _ = codes(report, "opt_in_no_default_regression")
        assert "ARTIFACT_SHA256_MISMATCH" in code_list
        assert "DEFAULT_PIPELINE_CHANGED" in code_list

    def test_source_and_behavior_quality_are_separate(self, tmp_path):
        fx = build_fixture(tmp_path)
        behavior = dict(fx.records[8]["behavior_quality"])
        behavior["basis"] = "static review"
        path = rewrite_evidence(fx, 8, behavior_quality=behavior)
        _, report, _, _ = run_cli(fx, evidence_path=path)
        code_list, _ = codes(report, "source_behavior_separation")
        assert "BEHAVIOR_NOT_GATE_BACKED" in code_list
        assert "QUALITY_NOT_SEPARATE" in code_list

    def test_behavior_quality_may_not_claim_full_game_equivalence(self, tmp_path):
        fx = build_fixture(tmp_path)
        behavior = dict(fx.records[8]["behavior_quality"])
        behavior["verdict"] = "PASS"
        behavior["claims_full_game_equivalence"] = True
        path = rewrite_evidence(fx, 8, behavior_quality=behavior)
        _, report, _, _ = run_cli(fx, evidence_path=path)
        code_list, _ = codes(report, "source_behavior_separation")
        assert "BEHAVIOR_QUALITY_BAD_VERDICT" in code_list
        assert "FULL_GAME_EQUIVALENCE_CLAIM" in code_list


class TestTamper:
    """Recomputed hashes must catch edits to any supplied artifact."""

    def test_evidence_field_edit_without_resealing_is_detected(self, tmp_path):
        fx = build_fixture(tmp_path)
        records = json.loads(json.dumps(fx.records))
        records[0]["selection"] = "both_fail"  # edited, seal left stale
        path = wjson(fx.tmp / "tampered.json", records)
        _, report, _, _ = run_cli(fx, evidence_path=path)
        code_list, verdict = codes(report, "revng_selection")
        assert verdict == "FAIL"
        assert "SHA256_SEAL_MISMATCH" in code_list
        assert report["overall"] == "OPEN"

    def test_selection_report_edit_without_resealing_is_detected(self, tmp_path):
        fx = build_fixture(tmp_path)
        data = json.loads(json.dumps(seal(fx.selections["revng_pass"])))
        data["decision"] = "FAILED"
        data["chosen_lifter"] = None
        rewrite(fx.sel_paths["revng_pass"], data, reseal=False)
        _, report, _, _ = run_cli(fx)
        code_list, _ = codes(report, "revng_selection")
        assert "SHA256_SEAL_MISMATCH" in code_list

    def test_gate_report_edit_without_resealing_is_detected(self, tmp_path):
        fx = build_fixture(tmp_path)
        data = json.loads(json.dumps(seal(fx.gate_data)))
        data["checks"][0]["ok"] = False
        data["ok"] = False
        rewrite(fx.gate_path, data, reseal=False)
        _, report, _, _ = run_cli(fx)
        code_list, _ = codes(report, "negative_pointer")
        assert "SHA256_SEAL_MISMATCH" in code_list

    def test_artifact_hash_claim_tamper_is_detected(self, tmp_path):
        fx = build_fixture(tmp_path)
        records = json.loads(json.dumps(fx.records))
        records[7]["baseline"]["sha256"] = "0" * 64
        path = wjson(fx.tmp / "tampered.json", records)
        _, report, _, _ = run_cli(fx, evidence_path=path)
        assert "SHA256_SEAL_MISMATCH" in codes(report, "opt_in_no_default_regression")[0]

    def test_missing_artifact_is_never_a_pass(self, tmp_path):
        fx = build_fixture(tmp_path)
        fx.baseline.unlink()
        _, report, _, _ = run_cli(fx)
        code_list, _ = codes(report, "opt_in_no_default_regression")
        assert "ARTIFACT_MISSING" in code_list

    def test_unsealed_report_cannot_fabricate_a_pass(self, tmp_path):
        fx = build_fixture(tmp_path)
        data = dict(fx.selections["revng_pass"])
        data["chosen_lifter"] = "revng"
        data["lifters"] = [{"name": "revng", "status": "FAIL", "diverged_cases": 0,
                            "total_cases": 0}]
        rewrite(fx.sel_paths["revng_pass"], data, reseal=False)  # no seal at all
        _, report, _, _ = run_cli(fx)
        code_list, _ = codes(report, "revng_selection")
        assert "LIFTER_BAD_STATUS" in code_list
        assert "SHA256_SEAL_MISMATCH" not in code_list  # absent seal is advisory

    def test_findings_carry_exact_reason_details(self, tmp_path):
        fx = build_fixture(tmp_path)
        data = dict(fx.selections["fallback"])
        data["lifters"] = [lifter("revng", "PASS"), lifter("pcrecomp", "PASS")]
        rewrite(fx.sel_paths["fallback"], data)
        _, report, _, _ = run_cli(fx)
        finding = next(f for f in report["findings"]
                       if f["criterion"] == "pcrecomp_fallback_after_revng_fail")
        assert "FALLBACK_WITHOUT_REVNG_FAILURE" in finding["reason_codes"]
        assert any("revng is PASS" in detail for detail in finding["details"])
