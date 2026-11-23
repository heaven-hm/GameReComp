"""Tests for the fail-closed per-function multi-lifter selector (issue #25).

The selector's whole job is refusal, so these tests pin both the pass path and
every way a body must *not* be promoted: divergence, an original fault, a
missing generated body, an unverified/malformed report, and a verdict that
claims agreement while its own case list disagrees. They also pin the fallback
order -- the first passing lifter wins, deterministically -- and the CLI's
explicit PROMOTE/FAILED/BLOCKED exit codes.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.lift import multilifter as ml  # noqa: E402


def row(verdict, **extra):
    result = {"address": "0x00401000", "verdict": verdict}
    result.update(extra)
    return result


def report(verdict, **extra):
    """A difftest --json report shape for one function."""
    return {"tool": "tools/lift/difftest.py --project", "addresses": [row(verdict, **extra)]}


def agree(verdict="agree", cases=3):
    return report(verdict, cases=[{"case": i, "agree": True} for i in range(cases)])


def diverged(cases=3):
    case_rows = [{"case": i, "agree": True} for i in range(cases)]
    case_rows[0] = {"case": 0, "agree": False,
                    "fields": [{"field": "eax", "project": 1, "original": 2}]}
    return report("diverge", cases=case_rows)


class SingleLifterTest(unittest.TestCase):
    def test_agree_with_zero_diverged_cases_promotes(self):
        decision = ml.select([("primary", agree())])
        self.assertEqual(decision.decision, ml.DECISION_PROMOTE)
        self.assertEqual(decision.chosen_lifter, "primary")
        self.assertTrue(decision.promoted)
        lifter = decision.lifters[0]
        self.assertEqual(lifter.status, ml.STATUS_PASS)
        self.assertEqual(lifter.diverged_cases, 0)
        self.assertEqual(lifter.total_cases, 3)
        self.assertTrue(any("zero diverged cases" in r for r in lifter.reasons))

    def test_scoped_agree_family_verdicts_all_promote(self):
        for verdict in ("agree", "agree-frame", "agree-return", "agree-memory"):
            with self.subTest(verdict=verdict):
                decision = ml.select([("primary", agree(verdict=verdict))])
                self.assertEqual(decision.decision, ml.DECISION_PROMOTE)
                self.assertEqual(decision.chosen_lifter, "primary")

    def test_agree_without_a_case_list_is_accepted_on_verdict_authority(self):
        decision = ml.select([("primary", report("agree"))])
        self.assertEqual(decision.decision, ml.DECISION_PROMOTE)
        self.assertTrue(any("verdict authoritative" in r for r in decision.reasons[0:1] + decision.lifters[0].reasons))

    def test_diverge_is_failed_and_never_promoted(self):
        decision = ml.select([("primary", diverged())])
        self.assertEqual(decision.decision, ml.DECISION_FAILED)
        self.assertIsNone(decision.chosen_lifter)
        self.assertFalse(decision.promoted)
        self.assertEqual(decision.lifters[0].status, ml.STATUS_DIVERGED)
        self.assertEqual(decision.lifters[0].diverged_cases, 1)
        self.assertTrue(all("primary" in r for r in decision.reasons))

    def test_agree_verdict_with_a_diverged_case_fails_closed(self):
        """A lying verdict must not slip a divergent body through."""
        fake_agree = report("agree", cases=[
            {"case": 0, "agree": True},
            {"case": 1, "agree": False},
        ])
        decision = ml.select([("primary", fake_agree)])
        self.assertEqual(decision.decision, ml.DECISION_FAILED)
        self.assertIsNone(decision.chosen_lifter)
        self.assertEqual(decision.lifters[0].status, ml.STATUS_DIVERGED)
        self.assertTrue(any("case(s) diverged" in r for r in decision.lifters[0].reasons))

    def test_original_faulted_is_blocked_and_never_promoted(self):
        decision = ml.select([("primary", report(
            "original-faulted", detail="reference machine faulted on case 2"))])
        self.assertEqual(decision.decision, ml.DECISION_BLOCKED)
        self.assertIsNone(decision.chosen_lifter)
        self.assertEqual(decision.lifters[0].status, ml.STATUS_FAULTED)
        self.assertTrue(any("faulted" in r for r in decision.lifters[0].reasons))

    def test_no_generated_body_is_blocked_and_never_promoted(self):
        decision = ml.select([("primary", report("no-generated-body"))])
        self.assertEqual(decision.decision, ml.DECISION_BLOCKED)
        self.assertIsNone(decision.chosen_lifter)
        self.assertEqual(decision.lifters[0].status, ml.STATUS_NO_BODY)
        self.assertTrue(any("no generated body" in r for r in decision.lifters[0].reasons))

    def test_unverified_verdicts_are_blocked(self):
        for verdict in ("harness-failed", "nothing-compared", "mystery-result", ""):
            with self.subTest(verdict=verdict):
                decision = ml.select([("primary", report(verdict))])
                self.assertEqual(decision.decision, ml.DECISION_BLOCKED)
                self.assertEqual(decision.lifters[0].status, ml.STATUS_UNVERIFIED)

    def test_agree_with_no_cases_is_unverified_not_a_pass(self):
        decision = ml.select([("primary", report("agree", cases=[]))])
        self.assertEqual(decision.decision, ml.DECISION_BLOCKED)
        self.assertEqual(decision.lifters[0].status, ml.STATUS_UNVERIFIED)
        self.assertTrue(any("no cases" in r for r in decision.lifters[0].reasons))

    def test_empty_lifter_list_is_blocked(self):
        decision = ml.select([])
        self.assertEqual(decision.decision, ml.DECISION_BLOCKED)
        self.assertIsNone(decision.chosen_lifter)
        self.assertTrue(any("no lifters supplied" in r for r in decision.reasons))


class MultipleLifterTest(unittest.TestCase):
    def test_first_passing_lifter_wins_over_later_passers(self):
        decision = ml.select([("first", agree()), ("second", agree(verdict="agree-frame"))])
        self.assertEqual(decision.decision, ml.DECISION_PROMOTE)
        self.assertEqual(decision.chosen_lifter, "first")
        self.assertEqual([lifter.status for lifter in decision.lifters],
                         [ml.STATUS_PASS, ml.STATUS_PASS])
        self.assertTrue(any("skipped 'second'" in r for r in decision.reasons))

    def test_falls_back_past_a_diverged_lifter(self):
        decision = ml.select([("bad", diverged()), ("good", agree())])
        self.assertEqual(decision.decision, ml.DECISION_PROMOTE)
        self.assertEqual(decision.chosen_lifter, "good")
        self.assertEqual(decision.lifters[0].status, ml.STATUS_DIVERGED)
        self.assertTrue(any("skipped 'bad' (DIVERGED)" in r for r in decision.reasons))

    def test_falls_back_past_faulted_and_bodyless_lifters(self):
        decision = ml.select([
            ("faulted", report("original-faulted")),
            ("bodyless", report("no-generated-body")),
            ("good", agree()),
        ])
        self.assertEqual(decision.decision, ml.DECISION_PROMOTE)
        self.assertEqual(decision.chosen_lifter, "good")
        self.assertEqual([lifter.status for lifter in decision.lifters],
                         [ml.STATUS_FAULTED, ml.STATUS_NO_BODY, ml.STATUS_PASS])
        self.assertTrue(any("skipped 'faulted' (FAULTED)" in r for r in decision.reasons))
        self.assertTrue(any("skipped 'bodyless' (NO_BODY)" in r for r in decision.reasons))

    def test_no_passer_with_any_divergence_is_failed(self):
        decision = ml.select([
            ("bad", diverged()),
            ("faulted", report("original-faulted")),
            ("bodyless", report("no-generated-body")),
        ])
        self.assertEqual(decision.decision, ml.DECISION_FAILED)
        self.assertIsNone(decision.chosen_lifter)
        self.assertTrue(all(
            any(name in reason for reason in decision.reasons)
            for name in ("bad", "faulted", "bodyless")
        ))

    def test_no_passer_without_divergence_is_blocked(self):
        decision = ml.select([
            ("faulted", report("original-faulted")),
            ("bodyless", report("no-generated-body")),
            ("unverified", report("harness-failed")),
        ])
        self.assertEqual(decision.decision, ml.DECISION_BLOCKED)
        self.assertIsNone(decision.chosen_lifter)

    def test_multi_address_report_with_one_diverge_is_not_promoted(self):
        multi = {"addresses": [row("agree", cases=[{"case": 0, "agree": True}]),
                               row("diverge", cases=[{"case": 0, "agree": False}])]}
        decision = ml.select([("multi", multi), ("fallback", agree())])
        self.assertEqual(decision.lifters[0].status, ml.STATUS_DIVERGED)
        self.assertEqual(decision.chosen_lifter, "fallback")
        self.assertEqual(decision.decision, ml.DECISION_PROMOTE)

    def test_multi_address_all_agree_promotes_with_counts_summed(self):
        multi = {"addresses": [
            row("agree", cases=[{"case": 0, "agree": True}, {"case": 1, "agree": True}]),
            row("agree", cases=[{"case": 0, "agree": True}]),
        ]}
        decision = ml.select([("multi", multi)])
        self.assertEqual(decision.decision, ml.DECISION_PROMOTE)
        self.assertEqual(decision.lifters[0].total_cases, 3)
        self.assertEqual(decision.lifters[0].diverged_cases, 0)

    def test_selection_is_deterministic_and_order_sensitive(self):
        first, second = agree(), agree(verdict="agree-frame")
        forward = ml.select([("alpha", first), ("beta", second)])
        again = ml.select([("alpha", first), ("beta", second)])
        reversed_order = ml.select([("beta", second), ("alpha", first)])
        self.assertEqual(forward.to_dict(), again.to_dict())
        self.assertEqual(forward.chosen_lifter, "alpha")
        self.assertEqual(reversed_order.chosen_lifter, "beta")


class InputShapeTest(unittest.TestCase):
    def test_dict_list_json_string_and_path_are_all_accepted(self):
        payload = report("agree", cases=[{"case": 0, "agree": True}])
        variants = [payload, payload["addresses"], json.dumps(payload)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "difftest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            variants.append(path)
            for variant in variants:
                with self.subTest(variant=type(variant).__name__):
                    decision = ml.select([("primary", variant)])
                    self.assertEqual(decision.decision, ml.DECISION_PROMOTE)
                    self.assertEqual(decision.chosen_lifter, "primary")

    def test_malformed_reports_fail_closed(self):
        for payload in (
            {"tool": "difftest", "no_verdict_here": True},
            {"addresses": [42]},
            "not json at all",
            Path("/this/path/does/not/exist.json"),
        ):
            with self.subTest(payload=str(payload)[:40]):
                decision = ml.select([("primary", payload)])
                self.assertEqual(decision.decision, ml.DECISION_BLOCKED)
                self.assertEqual(decision.lifters[0].status, ml.STATUS_UNVERIFIED)

    def test_decision_serialises_to_stable_json(self):
        decision = ml.select([("bad", diverged()), ("good", agree())])
        encoded = decision.to_json()
        decoded = json.loads(encoded)
        self.assertEqual(decoded["decision"], ml.DECISION_PROMOTE)
        self.assertEqual(decoded["chosen_lifter"], "good")
        self.assertEqual(len(decoded["lifters"]), 2)
        self.assertEqual(decoded, json.loads(decision.to_json()))


class CliTest(unittest.TestCase):
    def _run(self, *specs, extra=()):
        argv = []
        for spec in specs:
            argv += ["--lifter", spec]
        argv += list(extra)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ml.main(argv)
        return code, out.getvalue()

    def test_cli_exit_codes(self):
        code, text = self._run(f"good={json.dumps(agree())}")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["chosen_lifter"], "good")

        code, text = self._run(f"bad={json.dumps(diverged())}")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)["decision"], ml.DECISION_FAILED)

        code, text = self._run(f"fault={json.dumps(report('original-faulted'))}")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(text)["decision"], ml.DECISION_BLOCKED)

    def test_cli_accepts_a_file_and_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            report_path.write_text(json.dumps(agree()), encoding="utf-8")
            out_path = Path(tmp) / "decision.json"
            code, _ = self._run(f"primary={report_path}", extra=("--json", str(out_path)))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out_path.read_text())["chosen_lifter"], "primary")

    def test_cli_requires_at_least_one_lifter(self):
        with self.assertRaises(SystemExit):
            ml.main([])


if __name__ == "__main__":
    unittest.main()
