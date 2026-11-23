"""The differential gate, and proof that the gate has teeth.

Passing the gate is necessary but not sufficient: a gate that cannot fail is
worthless. The last two tests inject the exact historical defect -- a reference
machine whose EFLAGS are hard coded to zero, and a comparator that ignores
memory -- and assert the gate reports a failure instead of a clean pass.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.diff import differential_gate as gate  # noqa: E402
from tools.lift import difftest  # noqa: E402


def _checks(report):
    return {c["name"]: c for c in report["checks"]}


def test_gate_fixture_is_deterministic():
    assert gate.build_gate_pe() == gate.build_gate_pe()


def test_gate_passes_on_the_unmodified_toolchain():
    report = gate.run_gate()
    assert report["ok"] is True
    assert len(report["checks"]) == 7


def test_every_check_reports_a_nonempty_detail():
    for check in gate.run_gate()["checks"]:
        assert check["detail"], check["name"]


def test_flag_break_is_only_visible_when_flags_are_compared():
    checks = _checks(gate.run_gate())
    assert checks["flag-break-hidden-when-flags-excluded"]["ok"]
    assert checks["flag-break-detected-when-flags-compared"]["ok"]


def test_multilifter_never_promotes_a_broken_body():
    decisions = gate.run_gate()["decisions"]
    assert decisions == {"clean_body": "PROMOTE",
                         "flag_bug": "FAILED", "memory_bug": "FAILED"}


def test_gate_catches_a_hardcoded_zero_eflags_regression(monkeypatch):
    """The documented defect: vacuous flags made every flag verdict an agree."""
    real_run = difftest.run_unicorn_function

    def zero_flags(*args, **kwargs):
        rows = real_run(*args, **kwargs)
        for row in rows:
            row["eflags"] = 0
        return rows

    monkeypatch.setattr(difftest, "run_unicorn_function", zero_flags)
    report = gate.run_gate()
    assert report["ok"] is False
    assert _checks(report)["reference-flags-nonvacuous"]["ok"] is False


def test_gate_catches_a_comparator_that_drops_memory(monkeypatch):
    """A comparator that forgets memory would promote the memory-broken body."""
    real_diff = difftest.diff

    def no_memory(case, lifted, real):
        return [f for f in real_diff(case, lifted, real) if not f[0].startswith("[")]

    monkeypatch.setattr(difftest, "diff", no_memory)
    report = gate.run_gate()
    assert report["ok"] is False
    assert _checks(report)["memory-break-detected-at-named-address"]["ok"] is False
    assert report["decisions"]["memory_bug"] == "PROMOTE"
