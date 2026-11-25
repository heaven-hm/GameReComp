"""Issue #26: deterministic build-envelope verdicts from synthetic readings.

Every fixture here is a *supplied* measurement or a declared limit, never a
measurement invented by the tool. The tests prove the WITHIN/OVER/NOT TESTED
verdicts, that null readings stay null (never zero), that malformed/negative/
boolean/tampered inputs are rejected without a report, and that JSON/Markdown
are deterministic and free of derived performance claims.

Run with: python3 -m pytest tests/test_build_envelope.py -q
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from tools import build_envelope as be

KEYS = [spec.key for spec in be.METRICS]


def envelope(**limit_overrides) -> dict:
    """A synthetic declared envelope; overrides replace whole limit objects."""
    out = {
        "schema": 1,
        "limits": {
            "generated_bytes": {"max": 1000},
            "peak_rss_bytes": {"max": 100_000_000},
            "wall_seconds": {"max": 60.0},
            "disk_free_bytes": {"min": 1_000_000},
        },
    }
    out["limits"].update(limit_overrides)
    return out


def measured(**overrides) -> dict:
    """Synthetic readings that are all comfortably WITHIN the envelope."""
    out = {
        "generated_bytes": 500,
        "peak_rss_bytes": 50_000_000,
        "wall_seconds": 30.0,
        "disk_free_bytes": 2_000_000,
    }
    out.update(overrides)
    return out


def by_key(summary: dict) -> dict:
    return {metric["key"]: metric for metric in summary["metrics"]}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def cli_args(tmp_path: Path, *, env=None, meas=None, flags=None) -> list[str]:
    env_path = tmp_path / "in" / "envelope.json"
    write_json(env_path, envelope() if env is None else env)
    args = ["--envelope", str(env_path)]
    if meas is not None:
        meas_path = tmp_path / "in" / "measured.json"
        write_json(meas_path, meas)
        args += ["--measurements", str(meas_path)]
    for name, value in (flags or {}).items():
        args.append(f"--{name.replace('_', '-')}={value}")
    return args


class TestVerdicts:
    def test_all_within_is_pass(self):
        summary = be.analyse(measured(), envelope())
        assert summary["verdict"] == "PASS"
        assert summary["metrics_tested"] == 4
        assert summary["metrics_over"] == 0
        assert summary["findings"] == []
        assert [m["key"] for m in summary["metrics"]] == KEYS
        assert all(m["verdict"] == be.WITHIN for m in summary["metrics"])
        assert by_key(summary)["generated_bytes"]["value"] == 500
        assert by_key(summary)["disk_free_bytes"]["value"] == 2_000_000

    def test_inclusive_boundaries_are_within(self):
        summary = be.analyse(
            measured(generated_bytes=1000, peak_rss_bytes=100_000_000,
                     wall_seconds=60.0, disk_free_bytes=1_000_000),
            envelope())
        assert summary["verdict"] == "PASS"
        assert all(m["verdict"] == be.WITHIN for m in summary["metrics"])

    @pytest.mark.parametrize("key,value", [
        ("generated_bytes", 1001),
        ("peak_rss_bytes", 100_000_001),
        ("wall_seconds", 60.5),
        ("disk_free_bytes", 999_999),
    ])
    def test_each_breach_is_over_and_fails(self, key, value):
        summary = be.analyse(measured(**{key: value}), envelope())
        metric = by_key(summary)[key]
        assert metric["verdict"] == be.OVER
        assert metric["value"] == value  # the supplied reading is preserved
        assert summary["verdict"] == "FAIL"
        assert summary["metrics_over"] == 1
        assert summary["metrics_tested"] == 4
        assert be.OVER in be.render_markdown(summary)

    def test_all_over_is_fail(self):
        summary = be.analyse(
            measured(generated_bytes=5000, peak_rss_bytes=500_000_000,
                     wall_seconds=600.0, disk_free_bytes=1),
            envelope())
        assert summary["verdict"] == "FAIL"
        assert summary["metrics_over"] == 4
        assert all(m["verdict"] == be.OVER for m in summary["metrics"])

    def test_wall_seconds_integer_reading_is_accepted(self):
        summary = be.analyse(measured(wall_seconds=30), envelope())
        assert by_key(summary)["wall_seconds"]["verdict"] == be.WITHIN

    def test_zero_is_a_real_reading_not_a_missing_one(self):
        # A measured zero is data: it is WITHIN for a max limit and OVER for
        # the disk-free floor. It is never turned into NOT TESTED.
        summary = be.analyse(
            measured(generated_bytes=0, peak_rss_bytes=0, wall_seconds=0.0,
                     disk_free_bytes=0), envelope())
        metrics = by_key(summary)
        for key in ("generated_bytes", "peak_rss_bytes", "wall_seconds"):
            assert metrics[key]["verdict"] == be.WITHIN
            assert metrics[key]["value"] == 0
        assert metrics["disk_free_bytes"]["verdict"] == be.OVER
        assert metrics["disk_free_bytes"]["value"] == 0
        assert summary["metrics_tested"] == 4


class TestNotTested:
    def test_explicit_nulls_stay_not_tested_and_never_zero(self):
        summary = be.analyse(
            measured(generated_bytes=None, peak_rss_bytes=None,
                     wall_seconds=None, disk_free_bytes=None), envelope())
        assert summary["verdict"] == be.NOT_TESTED
        assert summary["metrics_tested"] == 0
        assert all(m["verdict"] == be.NOT_TESTED for m in summary["metrics"])
        assert all(m["value"] is None for m in summary["metrics"])
        text = be.dumps_json(summary)
        assert '"value": 0' not in text
        # Explicit in both the Value and the Verdict column of every metric row.
        rows = [line for line in be.render_markdown(summary).splitlines()
                if line.startswith("|") and be.NOT_TESTED in line]
        assert len(rows) == 4
        assert all(row.count(be.NOT_TESTED) == 2 for row in rows)

    def test_missing_fields_stay_not_tested(self):
        summary = be.analyse({}, envelope())
        assert summary["verdict"] == be.NOT_TESTED
        assert by_key(summary)["generated_bytes"]["value"] is None

    def test_undeclared_limit_is_not_tested(self):
        env = envelope()
        env["limits"].pop("peak_rss_bytes")
        summary = be.analyse(measured(), env)
        metric = by_key(summary)["peak_rss_bytes"]
        assert metric["verdict"] == be.NOT_TESTED
        assert metric["value"] is None
        assert metric["limit"] is None
        assert summary["verdict"] == "PARTIAL"

    def test_empty_envelope_declares_no_verdict(self):
        summary = be.analyse(measured(), {"schema": 1, "limits": {}})
        assert summary["verdict"] == be.NOT_TESTED
        assert summary["metrics_tested"] == 0

    def test_partial_when_some_metrics_untested(self):
        summary = be.analyse(measured(wall_seconds=None), envelope())
        assert summary["verdict"] == "PARTIAL"
        assert summary["metrics_tested"] == 3
        assert by_key(summary)["wall_seconds"]["value"] is None
        assert any("wall_seconds" in f for f in summary["findings"])

    def test_nested_measurement_containers_are_accepted(self):
        readings = {
            "schema": 1,
            "measurements": {"generated_bytes": 500, "wall_seconds": 30.0},
            "resources": {"peak_rss_bytes": 50_000_000},
            "disk_free_bytes": 2_000_000,
        }
        assert be.analyse(readings, envelope())["verdict"] == "PASS"


class TestTamperRejected:
    @pytest.mark.parametrize("key,value", [
        ("generated_bytes", -1),
        ("generated_bytes", True),
        ("generated_bytes", False),
        ("generated_bytes", 4096.5),
        ("generated_bytes", "4096"),
        ("generated_bytes", float("nan")),
        ("generated_bytes", float("inf")),
        ("peak_rss_bytes", -5),
        ("peak_rss_bytes", "12 MB"),
        ("wall_seconds", -0.1),
        ("wall_seconds", True),
        ("wall_seconds", "fast"),
        ("wall_seconds", "7s"),
        ("wall_seconds", float("nan")),
        ("wall_seconds", float("inf")),
        ("disk_free_bytes", -1),
        ("disk_free_bytes", True),
        ("disk_free_bytes", 2.5),
    ])
    def test_bad_reading_is_rejected_without_a_report(self, key, value):
        with pytest.raises(ValueError, match=key):
            be.analyse(measured(**{key: value}), envelope())

    @pytest.mark.parametrize("env", [
        envelope(generated_bytes={"max": -1}),
        envelope(generated_bytes={"max": True}),
        envelope(generated_bytes={"max": float("nan")}),
        envelope(generated_bytes={"max": float("inf")}),
        envelope(generated_bytes={"max": "4096"}),
        envelope(generated_bytes={"max": 4096.5}),
        envelope(peak_rss_bytes={"min": 1}),          # wrong direction
        envelope(disk_free_bytes={"max": 1}),         # wrong direction
        envelope(wall_seconds={"max": 60, "min": 0}),  # two directions
        envelope(wall_seconds={}),
        envelope(wall_seconds=60),
        envelope(bogus_metric={"max": 1}),
        {"schema": 1},                                # no limits object
        {"schema": 1, "limits": []},
        [1, 2, 3],
    ])
    def test_bad_envelope_is_rejected(self, env):
        with pytest.raises(ValueError, match="envelope"):
            be.analyse(measured(), env)

    def test_tampered_inputs_are_not_mutated(self):
        readings = measured(generated_bytes=-1)
        env = envelope()
        before = (copy.deepcopy(readings), copy.deepcopy(env))
        with pytest.raises(ValueError):
            be.analyse(readings, env)
        assert (readings, env) == before

    @pytest.mark.parametrize("flag,value", [
        ("generated_bytes", "-1"),
        ("generated_bytes", "true"),
        ("generated_bytes", "1.5"),
        ("generated_bytes", "1_000"),
        ("generated_bytes", "0x10"),
        ("peak_rss_bytes", "-1"),
        ("wall_seconds", "-0.1"),
        ("wall_seconds", "nan"),
        ("wall_seconds", "inf"),
        ("wall_seconds", "fast"),
        ("disk_free_bytes", "false"),
        ("disk_free_bytes", "-5"),
    ])
    def test_cli_rejects_bad_flags_without_writing_a_report(
            self, tmp_path: Path, capsys, flag, value):
        out_json = tmp_path / "out" / "summary.json"
        out_md = tmp_path / "out" / "summary.md"
        args = cli_args(tmp_path, flags={flag: value})
        assert be.main(args + ["--out-json", str(out_json),
                               "--out-md", str(out_md)]) == 2
        assert not out_json.exists() and not out_md.exists()
        assert "build_envelope:" in capsys.readouterr().err

    def test_cli_rejects_conflicting_file_and_flag(self, tmp_path: Path, capsys):
        args = cli_args(tmp_path, meas=measured(),
                        flags={"peak_rss_bytes": "123"})
        out = tmp_path / "summary.md"
        assert be.main(args + ["--out-md", str(out)]) == 2
        assert not out.exists()
        assert "conflicts" in capsys.readouterr().err

    @pytest.mark.parametrize("field", ["envelope", "measurements"])
    def test_cli_rejects_non_object_json(self, tmp_path: Path, capsys, field):
        bad = tmp_path / "bad.json"
        write_json(bad, [1, 2, 3])
        args = cli_args(tmp_path, meas={} if field == "measurements" else None)
        idx = args.index("--" + field)
        args[idx + 1] = str(bad)
        out = tmp_path / "summary.md"
        assert be.main(args + ["--out-md", str(out)]) == 2
        assert not out.exists()
        assert "expected a JSON object" in capsys.readouterr().err

    def test_cli_rejects_tampered_file_without_writing_a_report(
            self, tmp_path: Path, capsys):
        readings = measured()
        readings["disk_free_bytes"] = True
        out = tmp_path / "summary.md"
        assert be.main(cli_args(tmp_path, meas=readings) + ["--out-md", str(out)]) == 2
        assert not out.exists()
        assert "disk_free_bytes" in capsys.readouterr().err

    def test_cli_rejects_missing_input_file(self, tmp_path: Path, capsys):
        args = ["--envelope", str(tmp_path / "nope.json")]
        assert be.main(args) == 2
        assert "build_envelope:" in capsys.readouterr().err


class TestCliAndDeterminism:
    def test_cli_writes_json_and_md_from_supplied_paths(self, tmp_path: Path):
        out_json = tmp_path / "out" / "nested" / "summary.json"
        out_md = tmp_path / "out" / "nested" / "summary.md"
        args = cli_args(tmp_path, flags={
            "generated_bytes": "500", "peak_rss_bytes": "50000000",
            "wall_seconds": "30.0", "disk_free_bytes": "2000000",
        })
        assert be.main(args + ["--out-json", str(out_json),
                               "--out-md", str(out_md)]) == 0
        data = json.loads(out_json.read_text(encoding="utf-8"))
        assert data["generator"] == be.GENERATOR
        assert data["verdict"] == "PASS"
        assert data["envelope"] == args[1]
        assert "CLI flags" in data["measurements"]
        assert by_key(data)["wall_seconds"]["value"] == 30.0
        md = out_md.read_text(encoding="utf-8")
        assert md.startswith("# Build resource envelope")
        assert md.count(be.WITHIN) == 4

    def test_cli_prints_markdown_by_default(self, tmp_path: Path, capsys):
        assert be.main(cli_args(tmp_path, flags={"generated_bytes": "500"})) == 0
        out = capsys.readouterr().out
        assert out.startswith("# Build resource envelope")
        assert be.NOT_TESTED in out

    @pytest.mark.parametrize("env,meas,flags,expected", [
        (None, None, {"generated_bytes": "500", "peak_rss_bytes": "50000000",
                      "wall_seconds": "30.0", "disk_free_bytes": "2000000"}, 0),
        (None, None, {"generated_bytes": "5000", "peak_rss_bytes": "50000000",
                      "wall_seconds": "30.0", "disk_free_bytes": "2000000"}, 1),
        (None, None, {"generated_bytes": "500"}, 1),
        (None, None, {}, 1),
    ])
    def test_strict_exit_codes(self, tmp_path: Path, env, meas, flags, expected):
        assert be.main(cli_args(tmp_path, env=env, meas=meas, flags=flags)
                       + ["--strict", "--out-md", str(tmp_path / "s.md")]) == expected

    def test_file_null_filled_by_cli_flag(self, tmp_path: Path):
        readings = measured(peak_rss_bytes=None)
        args = cli_args(tmp_path, meas=readings,
                        flags={"peak_rss_bytes": "50000000"})
        out = tmp_path / "summary.json"
        assert be.main(args + ["--out-json", str(out)]) == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["verdict"] == "PASS"
        assert by_key(data)["peak_rss_bytes"]["value"] == 50_000_000

    def test_json_is_canonical_and_input_order_independent(self):
        first = be.analyse(measured(), envelope())
        reversed_readings = dict(reversed(list(measured().items())))
        second = be.analyse(reversed_readings, envelope())
        assert be.dumps_json(first) == be.dumps_json(second)
        assert be.dumps_json(first) == json.dumps(
            first, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        assert be.dumps_json(first) == be.dumps_json(
            be.analyse(measured(), envelope()))

    def test_markdown_fixed_order_and_no_timestamps_or_derived_claims(self):
        summary = be.analyse(measured(), envelope())
        md = be.render_markdown(summary)
        assert md == be.render_markdown(be.analyse(measured(), envelope()))
        assert [md.index(label) for label in
                ("Generated bytes", "Peak RSS", "Wall seconds", "Disk free")] == sorted(
                    md.index(label) for label in
                    ("Generated bytes", "Peak RSS", "Wall seconds", "Disk free"))
        assert re.search(r"\d{4}-\d{2}-\d{2}", md) is None
        assert "speed" not in md.lower()
        assert "ratio" not in md.lower()
        assert "derived performance claims" in md

    def test_fixed_key_set_and_no_derived_fields(self):
        summary = be.analyse(measured(), envelope())
        assert set(summary) == {
            "schema", "generator", "method", "verdict", "metrics_tested",
            "metrics_over", "metrics", "findings",
        }
        for metric in summary["metrics"]:
            assert set(metric) == {"key", "label", "unit", "value", "limit", "verdict"}
        text = be.dumps_json(summary).lower()
        assert "speedup" not in text and "baseline" not in text


if __name__ == "__main__":
    pytest.main([__file__])
