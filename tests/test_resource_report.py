"""Issue #26: standalone, evidence-only resource reporter (synthetic inputs).

Every fixture here is an invented *record*, never an invented measurement: the
reporter must only transcribe values present in the supplied JSON. Null/absent
readings stay ``NOT TESTED`` and contradictory or malformed values are flagged
as tampering instead of being presented as clean measurements.

Run with: python3 -m pytest tests/test_resource_report.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.autorecomp import resource_report as rr


PK = "generated_bytes"
BS = "build_seconds"
RSS = "peak_rss_bytes"
DF = "disk_free_bytes"


def make_report() -> dict:
    """A synthetic pipeline report.json with all four readings present."""
    return {
        "schema": 1,
        "tool": "GameReComp autorecomp",
        "version": "0.0.1",
        "resources": {
            "generated_source_bytes": 4096,
            "binary_bytes": 2048,
            "project_disk_bytes": 8192,
            "peak_rss_bytes": 144_000_000,
            "peak_rss_bytes_self": 12_000_000,
            "peak_rss_bytes_children": 144_000_000,
            "stage_seconds": {"translate": 2.5, "compile": 7.25},
            "stage_seconds_total": 9.75,
            "build_seconds": 7.25,
        },
        "disk_guard": {
            "free_bytes": 987_654_321,
            "total_bytes": 1_000_000_000,
            "ok": True,
        },
    }


def make_measured() -> dict:
    return {
        "schema": 1,
        "generated_bytes": 4096,
        "build_seconds": 7.25,
        "peak_rss_bytes": 144_000_000,
        "disk_free_bytes": 987_654_321,
    }


def by_key(summary: dict) -> dict:
    return {m["key"]: m for m in summary["metrics"]}


class TestMissingMeasurements:
    """Null/absent readings must stay NOT TESTED -- never zero or a guess."""

    def test_all_present_readings_are_measured(self):
        summary = rr.analyse(make_report())
        assert summary["verdict"] == "OK"
        assert summary["metrics_tested"] == 4
        metrics = by_key(summary)
        assert metrics[PK]["value"] == 4096
        assert metrics[BS]["value"] == 7.25
        assert metrics[RSS]["value"] == 144_000_000
        assert metrics[DF]["value"] == 987_654_321
        assert all(m["status"] == rr.MEASURED for m in metrics.values())

    def test_completely_empty_report_is_all_not_tested(self):
        summary = rr.analyse({})
        assert summary["verdict"] == "OK"  # an honest skip is not an error
        assert summary["metrics_tested"] == 0
        assert all(m["status"] == rr.NOT_TESTED for m in summary["metrics"])
        assert all(m["value"] is None for m in summary["metrics"])
        md = rr.render_markdown(summary)
        # explicit in both the Value and Status columns of every metric row
        assert md.count(rr.NOT_TESTED) == 4 * 2

    def test_explicit_nulls_stay_not_tested(self):
        report = make_report()
        report["resources"]["peak_rss_bytes"] = None
        report["resources"]["generated_source_bytes"] = None
        report["disk_guard"]["free_bytes"] = None
        summary = rr.analyse(report)
        metrics = by_key(summary)
        for key in (PK, RSS, DF):
            assert metrics[key]["status"] == rr.NOT_TESTED
            assert metrics[key]["value"] is None
        assert metrics[BS]["status"] == rr.MEASURED
        md = rr.render_markdown(summary)
        assert md.count(rr.NOT_TESTED) == 3 * 2

    def test_missing_sections_stay_not_tested(self):
        report = make_report()
        report.pop("disk_guard")
        report["resources"].pop("build_seconds")
        summary = rr.analyse(report)
        metrics = by_key(summary)
        assert metrics[DF]["status"] == rr.NOT_TESTED
        assert metrics[BS]["status"] == rr.NOT_TESTED
        assert summary["metrics_tested"] == 2

    def test_no_measured_file_keeps_null_report_value_not_tested(self):
        report = make_report()
        report["resources"]["peak_rss_bytes"] = None
        summary = rr.analyse(report, None)
        assert by_key(summary)[RSS]["status"] == rr.NOT_TESTED
        assert by_key(summary)[RSS]["value"] is None


class TestTamperedValues:
    """Contradictory or malformed readings must be detected, not believed."""

    def test_independent_mismatch_is_detected(self):
        measured = make_measured()
        measured["peak_rss_bytes"] = 999_999_999  # contradict the report
        summary = rr.analyse(make_report(), measured)
        metric = by_key(summary)[RSS]
        assert metric["status"] == rr.MISMATCH
        assert metric["value"] is None
        assert summary["verdict"] == "TAMPERED"
        assert any("peak rss" in f.lower() for f in summary["findings"])
        assert "999999999" in " ".join(summary["findings"])
        assert rr.MISMATCH in rr.render_markdown(summary)

    def test_string_where_bytes_expected_is_invalid(self):
        report = make_report()
        report["resources"]["generated_source_bytes"] = "4096"
        summary = rr.analyse(report)
        metric = by_key(summary)[PK]
        assert metric["status"] == rr.INVALID
        assert metric["value"] is None
        assert summary["verdict"] == "TAMPERED"
        assert any("4096" in f for f in summary["findings"])

    def test_negative_disk_free_is_invalid(self):
        report = make_report()
        report["disk_guard"]["free_bytes"] = -1
        summary = rr.analyse(report)
        assert by_key(summary)[DF]["status"] == rr.INVALID
        assert summary["verdict"] == "TAMPERED"

    def test_boolean_and_fractional_bytes_are_not_measurements(self):
        report = make_report()
        report["resources"]["peak_rss_bytes"] = True
        report["resources"]["generated_source_bytes"] = 4096.5
        summary = rr.analyse(report)
        assert by_key(summary)[RSS]["status"] == rr.INVALID
        assert by_key(summary)[PK]["status"] == rr.INVALID
        assert summary["verdict"] == "TAMPERED"

    def test_invalid_independent_reading_is_detected(self):
        measured = make_measured()
        measured["disk_free_bytes"] = "lots"
        summary = rr.analyse(make_report(), measured)
        assert by_key(summary)[DF]["status"] == rr.INVALID
        assert summary["verdict"] == "TAMPERED"
        assert "independent measurement" in " ".join(summary["findings"])

    def test_bogus_independent_reading_cannot_fabricate_a_null_report_value(self):
        report = make_report()
        report["resources"]["peak_rss_bytes"] = None
        measured = make_measured()
        measured["peak_rss_bytes"] = "fabricated"
        summary = rr.analyse(report, measured)
        assert by_key(summary)[RSS]["status"] == rr.INVALID
        assert by_key(summary)[RSS]["value"] is None
        assert summary["verdict"] == "TAMPERED"

    def test_strict_cli_exits_nonzero_on_tamper(self, tmp_path: Path):
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(make_report()))
        measured = make_measured()
        measured["peak_rss_bytes"] = 1
        measured_path = tmp_path / "measured.json"
        measured_path.write_text(json.dumps(measured))
        base = ["--report", str(report_path), "--measured", str(measured_path)]
        assert rr.main(base + ["--out-md", str(tmp_path / "a.md")]) == 0
        assert rr.main(base + ["--strict", "--out-md", str(tmp_path / "b.md")]) == 1


class TestIndependentEvidence:
    def test_matching_readings_are_confirmed(self):
        summary = rr.analyse(make_report(), make_measured())
        assert summary["verdict"] == "OK"
        assert summary["metrics_tested"] == 4
        assert all(m["status"] == rr.MEASURED for m in summary["metrics"])
        assert all(m["source"] == "report+measured" for m in summary["metrics"])
        assert summary["findings"] == []

    def test_measured_fills_a_null_report_value_legitimately(self):
        report = make_report()
        report["resources"]["peak_rss_bytes"] = None
        measured = make_measured()
        summary = rr.analyse(report, measured)
        metric = by_key(summary)[RSS]
        assert metric["status"] == rr.MEASURED
        assert metric["value"] == 144_000_000
        assert metric["source"] == "measured"
        assert summary["verdict"] == "OK"

    def test_nested_measurement_containers_are_accepted(self):
        measured = {
            "schema": 1,
            "resources": {"peak_rss_bytes": 144_000_000},
            "measurements": {"disk_free_bytes": 987_654_321},
        }
        summary = rr.analyse(make_report(), measured)
        assert by_key(summary)[RSS]["source"] == "report+measured"
        assert by_key(summary)[DF]["source"] == "report+measured"

    def test_seconds_agree_within_centisecond(self):
        measured = make_measured()
        measured["build_seconds"] = 7.2505  # rounding slack, not tampering
        summary = rr.analyse(make_report(), measured)
        assert by_key(summary)[BS]["status"] == rr.MEASURED
        assert summary["verdict"] == "OK"
        measured["build_seconds"] = 7.5  # a real disagreement
        summary = rr.analyse(make_report(), measured)
        assert by_key(summary)[BS]["status"] == rr.MISMATCH


class TestDeterminismAndCli:
    def test_json_is_canonical_and_input_order_independent(self):
        first = rr.analyse(make_report(), make_measured())
        second = rr.analyse(make_report(), make_measured())
        assert rr.dumps_json(first) == rr.dumps_json(second)
        assert rr.dumps_json(first).endswith("\n")
        assert rr.dumps_json(first) == json.dumps(first, indent=2, sort_keys=True) + "\n"

    def test_markdown_has_no_timestamps_and_fixed_order(self):
        md = rr.render_markdown(rr.analyse(make_report()))
        assert md.startswith("# Autorecomp resource evidence")
        assert md == rr.render_markdown(rr.analyse(make_report()))
        assert [m["key"] for m in rr.analyse(make_report())["metrics"]] == [PK, BS, RSS, DF]

    def test_cli_writes_markdown_and_json_from_supplied_paths(self, tmp_path: Path):
        report = make_report()
        report["resources"]["peak_rss_bytes"] = None   # filled from measured only
        report["resources"]["generated_source_bytes"] = None  # no reading anywhere
        report_path = tmp_path / "in" / "report.json"
        report_path.parent.mkdir()
        report_path.write_text(json.dumps(report))
        measured = make_measured()
        measured.pop("generated_bytes")  # leave generated bytes truly untested
        measured_path = tmp_path / "in" / "measured.json"
        measured_path.write_text(json.dumps(measured))
        out_md = tmp_path / "out" / "summary.md"
        out_json = tmp_path / "out" / "summary.json"

        assert rr.main([
            "--report", str(report_path), "--measured", str(measured_path),
            "--out-md", str(out_md), "--out-json", str(out_json),
        ]) == 0
        text = out_md.read_text()
        assert rr.NOT_TESTED in text  # generated bytes: no reading in either file
        assert "144000000 B" in text  # peak RSS: legitimately filled from measured
        data = json.loads(out_json.read_text())
        assert data["generator"] == rr.GENERATOR
        assert data["report"] == str(report_path)
        assert data["measured"] == str(measured_path)
        assert by_key(data)[PK]["status"] == rr.NOT_TESTED  # no reading in either file
        assert by_key(data)[RSS]["status"] == rr.MEASURED  # filled from measured only

    def test_cli_rejects_non_object_json(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("[1, 2, 3]")
        out = tmp_path / "never.md"
        assert rr.main(["--report", str(bad), "--out-md", str(out)]) == 2
        assert not out.exists()


if __name__ == "__main__":
    pytest.main([__file__])
