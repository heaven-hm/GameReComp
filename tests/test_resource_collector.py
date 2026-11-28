"""Issue #26: real child-process resource collection, no fabricated numbers.

The positive tests run a tiny Python child through the CLI and assert sane
values. The negative tests inject absent or tampered ``resource`` readings and
assert that the collector reports ``NOT TESTED`` / ``INVALID`` rather than a
plausible-looking measurement. No game path, executable name or output path is
hard-coded: commands and paths come from the test/tmp_path or sys.executable.

Run with: python3 -m pytest tests/test_resource_collector.py -q
"""
from __future__ import annotations

import json
import platform
import sys
import types
from pathlib import Path

import pytest

from tools import resource_collector as rc

CHILD = [sys.executable, "-c", "sum(range(1000))"]
SCALE = rc._rss_scale()


class FakeResource:
    """Injectable `resource` stand-in with scripted ru_maxrss readings."""

    RUSAGE_CHILDREN = 1

    def __init__(self, readings, *, exc=None, missing=False, exc_after=False):
        self.readings = list(readings)
        self.exc = exc
        self.missing = missing
        self.exc_after = exc_after  # fail only the second (after) snapshot
        self.calls = 0

    def getrusage(self, who):
        assert who == self.RUSAGE_CHILDREN
        late = self.calls >= 1
        if self.exc is not None and (late or not self.exc_after):
            raise self.exc
        raw = self.readings[min(self.calls, len(self.readings) - 1)]
        self.calls += 1
        if self.missing and (late or not self.exc_after):
            return types.SimpleNamespace()
        return types.SimpleNamespace(ru_maxrss=raw)


class TestRealChildMeasurement:
    def test_cli_measures_tiny_python_child(self, tmp_path: Path):
        out = tmp_path / "nested" / "result.json"
        assert rc.main(["--out", str(out), "--", *CHILD]) == 0
        text = out.read_text(encoding="utf-8")
        data = json.loads(text)

        assert data["generator"] == rc.GENERATOR
        assert data["schema"] == rc.SCHEMA_VERSION
        assert data["status"] == rc.MEASURED
        assert data["command"] == CHILD
        assert data["returncode"] == 0
        assert data["platform"] == sys.platform
        assert data["python"] == platform.python_version()
        assert data["findings"] == []
        assert isinstance(data["wall_seconds"], float)
        assert 0 <= data["wall_seconds"] < 60
        assert isinstance(data["peak_rss_bytes"], int)
        assert 0 <= data["peak_rss_bytes"] < 4 * 1024**3
        assert isinstance(data["rusage_before_bytes"], int)
        assert isinstance(data["rusage_after_bytes"], int)
        assert data["rusage_after_bytes"] >= data["rusage_before_bytes"]
        assert data["peak_rss_bytes"] == (data["rusage_after_bytes"]
                                          - data["rusage_before_bytes"])
        # Canonical, timestamp-free serialisation.
        assert text == json.dumps(data, indent=2, sort_keys=True,
                                  ensure_ascii=True) + "\n"

    def test_collect_directly_returns_sane_values(self):
        rec = rc.collect(CHILD)
        assert rec["status"] == rc.MEASURED
        assert rec["wall_seconds"] > 0
        assert rec["peak_rss_bytes"] >= 0
        assert "high-water variance" in rec["method"]

    def test_nonzero_child_exit_is_still_an_honest_measurement(self, tmp_path: Path):
        out = tmp_path / "result.json"
        failing = [sys.executable, "-c", "raise SystemExit(7)"]
        assert rc.main(["--out", str(out), "--", *failing]) == 0
        data = json.loads(out.read_text())
        assert data["returncode"] == 7
        assert data["status"] == rc.MEASURED

    def test_cli_prints_json_without_out(self, capsys):
        assert rc.main(["--", *CHILD]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == rc.MEASURED

    def test_injected_readings_use_before_after_variance(self, monkeypatch):
        fake = FakeResource([100, 250])
        monkeypatch.setattr(rc, "_resource", fake)
        rec = rc.collect(CHILD)
        assert rec["status"] == rc.MEASURED
        assert rec["rusage_before_bytes"] == 100 * SCALE
        assert rec["rusage_after_bytes"] == 250 * SCALE
        assert rec["peak_rss_bytes"] == 150 * SCALE
        assert fake.calls == 2

    def test_high_water_unchanged_is_a_measured_zero(self, monkeypatch):
        # An earlier child already set the high-water mark: variance is zero,
        # which is a lower bound, not a fabricated number.
        monkeypatch.setattr(rc, "_resource", FakeResource([400, 400]))
        rec = rc.collect(CHILD)
        assert rec["status"] == rc.MEASURED
        assert rec["peak_rss_bytes"] == 0


class TestMissingMeasurements:
    def test_unsupported_platform_is_not_tested(self, monkeypatch):
        monkeypatch.setattr(rc, "_resource", None)
        rec = rc.collect(CHILD)
        assert rec["status"] == rc.NOT_TESTED
        assert rec["wall_seconds"] is None
        assert rec["peak_rss_bytes"] is None
        assert rec["rusage_before_bytes"] is None
        assert rec["rusage_after_bytes"] is None
        assert any("unavailable" in f for f in rec["findings"])

        monkeypatch.setattr(rc, "_resource", object())  # no RUSAGE_CHILDREN
        assert rc.collect(CHILD)["status"] == rc.NOT_TESTED

    @pytest.mark.parametrize("fake", [
        FakeResource([0, 0], exc=OSError("no rusage")),
        FakeResource([0, 0], missing=True),
    ])
    def test_absent_before_snapshot_is_not_tested(self, monkeypatch, fake):
        monkeypatch.setattr(rc, "_resource", fake)
        rec = rc.collect(CHILD)
        assert rec["status"] == rc.NOT_TESTED
        assert rec["peak_rss_bytes"] is None
        assert rec["rusage_before_bytes"] is None
        # No before snapshot: the tool refuses to run an unmeasurable command.
        assert rec["returncode"] == rc._base_record([])["returncode"]
        assert any("before" in f for f in rec["findings"])

    @pytest.mark.parametrize("fake", [
        FakeResource([100, 200], exc=OSError("no rusage"), exc_after=True),
        FakeResource([100, 200], missing=True, exc_after=True),
    ])
    def test_absent_after_snapshot_is_not_tested_but_command_ran(self, monkeypatch, fake):
        monkeypatch.setattr(rc, "_resource", fake)
        rec = rc.collect(CHILD)
        assert rec["status"] == rc.NOT_TESTED
        assert rec["returncode"] == 0
        assert rec["rusage_before_bytes"] == 100 * SCALE
        assert rec["rusage_after_bytes"] is None
        assert rec["peak_rss_bytes"] is None
        assert any("after" in f for f in rec["findings"])

    def test_no_command_is_not_tested(self):
        rec = rc.collect([])
        assert rec["status"] == rc.NOT_TESTED
        assert rec["returncode"] == rc._base_record([])["returncode"]
        assert rec["peak_rss_bytes"] is None
        assert any("no command" in f for f in rec["findings"])

    def test_cli_rejects_missing_command_and_bad_timeout(self):
        assert rc.main([]) == 2
        assert rc.main(["--timeout", "0", "--", *CHILD]) == 2

    def test_not_tested_reason_is_not_upgraded_by_validation(self, monkeypatch):
        monkeypatch.setattr(rc, "_resource", None)
        rec = rc.collect(CHILD)
        checked = rc.validate_record(rec)
        assert checked["status"] == rc.NOT_TESTED
        assert checked["findings"] == rec["findings"]


class TestTamperedValues:
    @pytest.mark.parametrize("raw", [-1, True, False, float("nan"),
                                     float("inf"), "4096"])
    def test_invalid_rusage_reading_is_rejected(self, monkeypatch, raw):
        monkeypatch.setattr(rc, "_resource", FakeResource([raw, raw]))
        rec = rc.collect(CHILD)
        assert rec["status"] == rc.INVALID
        assert rec["peak_rss_bytes"] is None
        assert rec["rusage_before_bytes"] is None
        assert rec["rusage_after_bytes"] is None
        assert any("ru_maxrss" in f for f in rec["findings"])

    def test_after_below_before_is_rejected(self, monkeypatch):
        monkeypatch.setattr(rc, "_resource", FakeResource([500, 100]))
        rec = rc.collect(CHILD)
        assert rec["status"] == rc.INVALID
        assert rec["peak_rss_bytes"] is None
        assert any("negative" in f for f in rec["findings"])

    @pytest.mark.parametrize("key,value", [
        ("peak_rss_bytes", -1),
        ("peak_rss_bytes", True),
        ("peak_rss_bytes", float("nan")),
        ("wall_seconds", -0.1),
        ("wall_seconds", float("nan")),
        ("rusage_before_bytes", -5),
        ("rusage_after_bytes", 1.5),
    ])
    def test_validate_record_rejects_tampered_numbers_and_does_not_mutate(
            self, key, value):
        clean = rc.collect(CHILD)
        original = clean[key]
        tampered = dict(clean)
        tampered[key] = value
        checked = rc.validate_record(tampered)
        assert checked is not tampered
        assert checked["status"] == rc.INVALID
        assert checked[key] is None
        assert any(key in f for f in checked["findings"])
        # Neither the caller's tampered dict nor the clean record is mutated.
        assert tampered[key] is value
        assert clean[key] == original

    def test_measured_status_with_missing_metric_is_invalid(self):
        clean = rc.collect(CHILD)
        tampered = dict(clean)
        tampered["peak_rss_bytes"] = None
        checked = rc.validate_record(tampered)
        assert checked["status"] == rc.INVALID
        assert any("metric is missing" in f for f in checked["findings"])

    def test_verify_cli_flags_tampered_json(self, tmp_path: Path):
        clean = rc.collect(CHILD)
        clean["peak_rss_bytes"] = -123
        src = tmp_path / "tampered.json"
        src.write_text(json.dumps(clean), encoding="utf-8")
        out = tmp_path / "checked.json"
        assert rc.main(["--verify", str(src), "--strict", "--out", str(out)]) == 1
        data = json.loads(out.read_text())
        assert data["status"] == rc.INVALID
        assert data["peak_rss_bytes"] is None

    def test_verify_cli_rejects_missing_and_non_object_json(self, tmp_path: Path):
        missing = tmp_path / "does-not-exist.json"
        assert rc.main(["--verify", str(missing)]) == 2
        bad = tmp_path / "array.json"
        bad.write_text("[1, 2, 3]", encoding="utf-8")
        assert rc.main(["--verify", str(bad)]) == 2


class TestDeterminism:
    def test_json_is_canonical_and_idempotent(self):
        rec = rc.collect(CHILD)
        first = rc.dumps_json(rec)
        assert first == rc.dumps_json(rec)
        assert first == json.dumps(rec, indent=2, sort_keys=True,
                                   ensure_ascii=True) + "\n"
        assert rc.dumps_json(rc.validate_record(rec)) == first

    def test_fixed_key_set_and_no_timestamps(self):
        rec = rc.collect(CHILD)
        assert set(rec) == {
            "schema", "generator", "status", "method", "platform", "python",
            "command", "returncode", "wall_seconds", "peak_rss_bytes",
            "rusage_before_bytes", "rusage_after_bytes", "findings",
        }
        assert not any("time" in k.lower() and k != "wall_seconds" for k in rec)


if __name__ == "__main__":
    pytest.main([__file__])
