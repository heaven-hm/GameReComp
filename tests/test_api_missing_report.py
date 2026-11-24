"""Tests for the deterministic, evidence-led missing-API report (issue #14).

Run with: python3 -m pytest tests/test_api_missing_report.py -q
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools import api_missing_report as amr

PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")


# --------------------------------------------------------------------------- #
# Synthetic fixtures (no repo paths, no game assets)
# --------------------------------------------------------------------------- #

def doc(*families, version="1.2.0"):
    return {"schema_version": version, "families": list(families)}


def family(name="kernel32.dll", kind="system", *, apis=None, gaps=None, **extra):
    out = {"family": name, "kind": kind}
    if apis is not None:
        out["apis"] = apis
    if gaps is not None:
        out["not_implemented"] = gaps
    out.update(extra)
    return out


def api(name, **extra):
    return {"name": name, **extra}


def evidence(ref="tests/test_win32hle.py::test_exit_process"):
    return {"selftest": ref}


def gap(name, files=1):
    return {"name": name, "files": files}


def write_input(tmp_path: Path, document, name="coverage.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def rows(report, family_name):
    fam = next(f for f in report["families"] if f["family"] == family_name)
    return fam


# --------------------------------------------------------------------------- #
# Core reporting behaviour
# --------------------------------------------------------------------------- #

def test_report_is_deterministic_and_has_no_blanket_percentage(tmp_path):
    document = doc(
        family("user32.dll", apis=[api("MessageBoxA", evidence=evidence())]),
        family("kernel32.dll", apis=[api("ExitProcess", evidence=evidence())],
               gaps=[gap("BrandNewApi", 3)]),
        family("sdl2.dll", "game-owned", gaps=[gap("SDL_CreateWindow")]),
    )
    path = write_input(tmp_path, document)
    first = amr.build_report(json.loads(path.read_text()), input_path=path,
                             input_sha256="0" * 64)
    second = amr.build_report(json.loads(path.read_text()), input_path=path,
                              input_sha256="0" * 64)
    json_a = amr.render_json(first)
    json_b = amr.render_json(second)
    md_a = amr.render_markdown(first)
    md_b = amr.render_markdown(second)
    assert json_a == json_b
    assert md_a == md_b
    assert PERCENT_RE.search(json_a) is None
    assert PERCENT_RE.search(md_a) is None
    assert "completion_claim" in json_a and '"completion_claim": false' in json_a
    assert "No aggregate percentage" in md_a or "no aggregate percentage" in md_a


def test_explicit_statuses_not_implemented_deferred_implemented(tmp_path):
    document = doc(
        family("kernel32.dll",
               apis=[api("ExitProcess", evidence=evidence()),
                     api("BrandNewApi", registered=True, status="implemented")]),
        family("user32.dll", apis=[api("MessageBoxA", evidence=evidence())]),
        family("sdl2.dll", "game-owned",
               apis=[api("SDL_Quit", evidence=evidence())],
               gaps=[gap("SDL_CreateWindow")]),
    )
    report = amr.build_report(document)
    statuses = {f["family"]: f["status"] for f in report["families"]}
    assert statuses == {
        "kernel32.dll": "not_implemented",
        "user32.dll": "implemented",
        "sdl2.dll": "deferred",
    }
    kernel = rows(report, "kernel32.dll")
    assert kernel["status_source"] == "recomputed_from_evidence"
    assert kernel["critical"] is True
    assert [r["name"] for r in kernel["implemented"]] == ["ExitProcess"]
    assert [(r["name"], r["reason"]) for r in kernel["not_implemented"]] == [
        ("BrandNewApi", "no_behavior_evidence")]
    user = rows(report, "user32.dll")
    assert user["not_implemented"] == []
    assert user["implemented"][0]["evidence"] == evidence()
    sdl = rows(report, "sdl2.dll")
    assert sdl["critical"] is False
    assert [r["name"] for r in sdl["not_implemented"]] == ["SDL_CreateWindow"]
    assert report["deferred_families"] == ["sdl2.dll"]


def test_registration_only_coverage_json_never_becomes_implemented():
    """A raw api_coverage-style report must never be read as a PASS.

    That format has registration counts and ``status: implemented`` hints but
    no behavior evidence.  Every system family must therefore be reported as
    not_implemented and game-owned families as deferred.
    """
    document = doc(
        family("kernel32.dll", critical=True, status="implemented",
               implemented_count=1, not_implemented_count=1,
               apis=[api("ExitProcess", registered=True, implemented=True)],
               gaps=[gap("MissingFromShims", 2)]),
        family("d3d8.dll", "game-owned", critical=False, status="implemented",
               implemented_count=1, not_implemented_count=0,
               apis=[api("Direct3DCreate8", registered=True, implemented=True)]),
    )
    report = amr.build_report(document)
    assert all(f["status"] != "implemented" for f in report["families"])
    assert rows(report, "kernel32.dll")["status"] == "not_implemented"
    assert rows(report, "d3d8.dll")["status"] == "deferred"
    assert report["counts"]["implemented"] == 0
    assert "INCOMPLETE" in report["verdict"]


# --------------------------------------------------------------------------- #
# Meta-tests: fabrication / missing evidence cannot manufacture implemented
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fabrication", [
    {"implemented": True, "registered": True},                        # bare flag
    {"status": "implemented", "registered": True},                    # status hint
    {"evidence": {"registered": True, "implemented": True}},          # flags as evidence
    {"evidence": {"test": "PASS"}},                                   # trivial token
    {"evidence": {"selftest": "pass"}},                               # trivial token
    {"evidence": {"selftest": ""}},                                   # empty reference
    {"evidence": {"selftest": ["", None]}},                           # invalid list
    {"evidence": {"selftest": "tests/test_x.py::test_ok",
                  "result": "fail"}},                                 # failed test
])
def test_fabricated_implementation_flag_cannot_yield_implemented_status(fabrication):
    document = doc(family("kernel32.dll", apis=[api("ExitProcess", **fabrication)]))
    report = amr.build_report(document)
    fam = rows(report, "kernel32.dll")
    assert fam["status"] != "implemented"
    assert fam["implemented"] == []
    assert fam["implemented_count"] == 0
    assert fam["not_implemented"][0]["reason"] == "no_behavior_evidence"
    assert report["counts"]["implemented"] == 0
    text = amr.render_markdown(report) + amr.render_json(report)
    assert "| `kernel32.dll` | system | implemented |" not in text


def test_missing_evidence_cannot_yield_implemented_status():
    """The explicit missing-evidence case: no evidence field, no implemented."""
    document = doc(family("kernel32.dll", apis=[api("ExitProcess")]))
    report = amr.build_report(document)
    fam = rows(report, "kernel32.dll")
    assert fam["status"] == "not_implemented"
    assert fam["not_implemented"] == [
        {"name": "ExitProcess", "reason": "no_behavior_evidence", "files": None}]
    assert "evidence" not in fam["not_implemented"][0]


def test_explicit_gap_wins_over_evidenced_flag():
    document = doc(family("kernel32.dll",
                          apis=[api("ExitProcess", evidence=evidence())],
                          gaps=[gap("ExitProcess")]))
    fam = rows(amr.build_report(document), "kernel32.dll")
    assert fam["status"] == "not_implemented"
    assert fam["implemented"] == []
    assert fam["not_implemented"][0]["reason"] == "explicit_gap"


def test_evidence_supports_implementation_unit_cases():
    assert amr.evidence_supports_implementation({"selftest": "tests/x.py::test_y"})
    assert amr.evidence_supports_implementation({"frame_proof": "reports/f.json"})
    assert amr.evidence_supports_implementation({"witness": ["a", "b"]})
    assert not amr.evidence_supports_implementation(None)
    assert not amr.evidence_supports_implementation("tests/x.py::test_y")
    assert not amr.evidence_supports_implementation({"registered": True})
    assert not amr.evidence_supports_implementation({"selftest": "PASS"})
    assert not amr.evidence_supports_implementation(
        {"selftest": "tests/x.py::test_y", "status": "skipped"})


# --------------------------------------------------------------------------- #
# Fail-closed validation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad", [
    "this is not json",
    "[]",                                                   # top level not object
    '{}',                                                   # missing schema_version
    '{"schema_version": "1", "families": []}',             # empty families
    '{"schema_version": "1", "families": {}}',             # families not list
    '{"schema_version": "1", "families": [1]}',            # family not object
    json.dumps(doc(family(kind="unknown"))),                # unknown kind
    json.dumps(doc(family(apis=[{"evidence": {}}]))),       # API without name
    json.dumps(doc(family(apis=[api("A"), api("A")]))),     # duplicate API
    json.dumps(doc(family("x"), family("x"))),              # duplicate family
    json.dumps(doc(family(apis=[api("A", evidence="x")]))),  # evidence not object
    json.dumps(doc(family(apis=[api("A", files=-1)]))),     # negative files
    json.dumps(doc(family())),                              # no APIs and no gaps
])
def test_malformed_input_fails_closed_and_writes_nothing(tmp_path, bad):
    path = tmp_path / "input.json"
    path.write_text(bad, encoding="utf-8")
    json_out = tmp_path / "out" / "report.json"
    md_out = tmp_path / "out" / "report.md"
    rc = amr.main(["--input", str(path), "--json-out", str(json_out),
                   "--markdown-out", str(md_out)])
    assert rc == 2
    assert not json_out.exists()
    assert not md_out.exists()


def test_missing_input_file_fails_closed(tmp_path):
    rc = amr.main(["--input", str(tmp_path / "absent.json")])
    assert rc == 2


def test_no_input_argument_fails_closed():
    assert amr.main([]) == 2


# --------------------------------------------------------------------------- #
# CLI behavior / no hard-coded paths
# --------------------------------------------------------------------------- #

def test_cli_writes_supplied_paths_and_reports_honest_summary(tmp_path, capsys):
    document = doc(
        family("kernel32.dll",
               apis=[api("ExitProcess", evidence=evidence())],
               gaps=[gap("BrandNewApi")]),
        family("sdl2.dll", "game-owned", gaps=[gap("SDL_CreateWindow")]),
    )
    path = write_input(tmp_path, document)
    json_out = tmp_path / "nested" / "report.json"
    md_out = tmp_path / "nested" / "report.md"
    rc = amr.main(["--input", str(path), "--json-out", str(json_out),
                   "--markdown-out", str(md_out)])
    assert rc == 0
    assert "not a completion claim" in capsys.readouterr().out
    written = json.loads(json_out.read_text(encoding="utf-8"))
    markdown = md_out.read_text(encoding="utf-8")
    assert written["input"] == str(path)
    assert written["policy"]["completion_claim"] is False
    assert "| `kernel32.dll` | system | not_implemented |" in markdown
    assert "| `sdl2.dll` | game-owned | deferred |" in markdown
    assert "`BrandNewApi`" in markdown
    assert PERCENT_RE.search(markdown) is None


def test_cli_default_outputs_are_derived_from_input(tmp_path):
    path = write_input(tmp_path, doc(family("kernel32.dll",
                                            apis=[api("ExitProcess",
                                                      evidence=evidence())])))
    assert amr.main([str(path)]) == 0  # positional input also accepted
    assert (tmp_path / "coverage.json.missing.json").is_file()
    assert (tmp_path / "coverage.json.missing.md").is_file()


def test_tool_has_no_hard_coded_machine_paths():
    source = (Path(amr.__file__).resolve()).read_text(encoding="utf-8")
    for marker in ("/Users/", "/private/var/", "C:\\\\", "reports/api-coverage.json"):
        assert marker not in source


def test_json_and_markdown_are_machine_and_human_readable(tmp_path):
    document = doc(family("kernel32.dll",
                          apis=[api("ExitProcess", evidence=evidence())],
                          gaps=[gap("BrandNewApi", 7)]))
    report = amr.build_report(document, input_path="coverage.json",
                              input_sha256="a" * 64)
    assert set(report) >= {"schema_version", "tool", "input", "input_sha256",
                           "generated_at", "policy", "counts", "families",
                           "deferred_families", "verdict"}
    assert report["counts"] == {
        "families": 1, "apis": 2, "implemented": 1, "not_implemented": 1,
        "deferred_families": 0, "critical_families": 1}
    # Canonical JSON round-trips and is stable under key ordering.
    text = amr.render_json(report)
    assert json.loads(text) == report
    assert text.endswith("\n")
