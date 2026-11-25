"""Synthetic tests for the evidence-led API family status tracker.

Run with: python3 -m pytest tests/test_api_family_tracker.py -q

Every fixture below is hand-written JSON: no repository scan, no corpus, no
game assets and no live tests are involved.  The two regression properties this
module pins are (1) flag-only claims can never produce ``implemented`` and
(2) tampered evidence is rejected fail-closed.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from tools import api_family_tracker as ft

PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
NO_EVIDENCE = object()


# --------------------------------------------------------------------------- #
# Synthetic fixture builders (all data invented for these tests)
# --------------------------------------------------------------------------- #

def digest(value) -> str:
    """Independent canonical digest, not the tool's helper."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def evidence(ref="tests/test_win32hle.py::test_exit_process"):
    return {"selftest": ref}


def cov_api(name, *, evidence_value=NO_EVIDENCE, family=None, **flags):
    record = {"name": name}
    if family is not None:
        record["family"] = family
    if evidence_value is not NO_EVIDENCE:
        record["evidence"] = evidence_value
    record.update(flags)  # untrusted hints; the tool must ignore them
    return record


def coverage(*apis, families=None, version="1.2.0"):
    doc = {"schema_version": version, "apis": list(apis)}
    if families is not None:
        doc["families"] = families
    return doc


def fam(name, apis, kind="system", **extra):
    declared = [{"name": a} if isinstance(a, str) else a for a in apis]
    return {"family": name, "kind": kind, "apis": declared, **extra}


def definition(*families, coverage_sha256=None, version="1.0.0"):
    doc = {"schema_version": version, "families": list(families)}
    if coverage_sha256 is not None:
        doc["coverage_sha256"] = coverage_sha256
    return doc


def write(path: Path, doc) -> Path:
    text = doc if isinstance(doc, str) else json.dumps(doc, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_cli(tmp_path: Path, cov_doc, defn_doc, *, cov_name="coverage.json",
            defn_name="families.json", extra_args=()):
    cov_path = write(tmp_path / cov_name, cov_doc)
    defn_path = write(tmp_path / defn_name, defn_doc)
    json_out = tmp_path / "out" / "status.json"
    md_out = tmp_path / "out" / "status.md"
    rc = ft.main(["--coverage", str(cov_path), "--families", str(defn_path),
                  "--json-out", str(json_out), "--markdown-out", str(md_out),
                  *extra_args])
    return rc, json_out, md_out


def family_row(report, name):
    return next(f for f in report["families"] if f["family"] == name)


# --------------------------------------------------------------------------- #
# Core status rules
# --------------------------------------------------------------------------- #

def test_four_explicit_statuses_and_missing_lists():
    cov = coverage(
        cov_api("ExitProcess", evidence_value=evidence(), family="kernel32.dll"),
        cov_api("CreateFileA",
                evidence_value=evidence("tests/test_win32hle.py::test_create_file"),
                family="kernel32.dll"),
        cov_api("MessageBoxA",
                evidence_value=good_ref("tests/test_win32hle.py::test_messagebox"),
                family="user32.dll"),
        cov_api("SDL_Quit",
                evidence_value=evidence("tests/test_sdl.py::test_quit"),
                family="sdl2.dll"),
    )
    defn = definition(
        fam("kernel32.dll", ["ExitProcess", "CreateFileA", "GetTickCount"]),
        fam("user32.dll", ["MessageBoxA"]),
        fam("gdi32.dll", ["BitBlt", "LineTo"]),
        fam("sdl2.dll", ["SDL_Quit", "SDL_CreateWindow"], kind="game-owned"),
    )
    report = ft.build_report(defn, cov)
    statuses = {f["family"]: f["status"] for f in report["families"]}
    assert statuses == {
        "kernel32.dll": "partial",
        "user32.dll": "implemented",
        "gdi32.dll": "not_implemented",
        "sdl2.dll": "deferred",
    }
    assert family_row(report, "kernel32.dll")["missing_apis"] == [
        {"name": "GetTickCount", "reason": "no_behavior_evidence"}]
    assert [r["name"] for r in family_row(report, "gdi32.dll")["missing_apis"]] == [
        "BitBlt", "LineTo"]
    assert family_row(report, "user32.dll")["missing_apis"] == []
    assert family_row(report, "sdl2.dll")["missing_apis"] == [
        {"name": "SDL_CreateWindow", "reason": "no_behavior_evidence"}]
    assert report["counts"] == {
        "families": 4, "apis": 8, "implemented": 1, "partial": 1,
        "not_implemented": 1, "deferred": 1, "missing_apis": 4}
    assert report["deferred_families"] == ["sdl2.dll"]
    assert all(f["status_source"] == "recomputed_from_evidence"
               for f in report["families"])


def good_ref(ref):
    """Behavior evidence via a different proof key than the default helper."""
    return {"test_id": ref}


def test_deferred_family_is_never_implemented_even_with_full_evidence():
    cov = coverage(
        cov_api("SDL_Quit", evidence_value=evidence()),
        cov_api("SDL_CreateWindow", evidence_value=evidence("tests/x.py::test_cw")),
    )
    defn = definition(fam("sdl2.dll", ["SDL_Quit", "SDL_CreateWindow"],
                          kind="game-owned", status="implemented"))
    report = ft.build_report(defn, cov)
    sdl = family_row(report, "sdl2.dll")
    assert sdl["status"] == "deferred"
    assert sdl["critical"] is False
    assert sdl["missing_apis"] == []
    assert report["deferred_families"] == ["sdl2.dll"]


def test_explicit_gap_from_coverage_json_is_labelled_and_stays_missing():
    cov = coverage(families=[{
        "family": "kernel32.dll",
        "apis": [cov_api("ExitProcess", evidence_value=evidence())],
        "not_implemented": [{"name": "BrandNewApi", "files": 2}],
    }])
    defn = definition(fam("kernel32.dll", ["ExitProcess", "BrandNewApi"]))
    kern = family_row(ft.build_report(defn, cov), "kernel32.dll")
    assert kern["status"] == "partial"
    assert kern["missing_apis"] == [
        {"name": "BrandNewApi", "reason": "explicit_gap"}]


def test_empty_coverage_is_honest_not_implemented_not_a_pass():
    report = ft.build_report(definition(fam("kernel32.dll", ["ExitProcess"])),
                             coverage())
    kern = family_row(report, "kernel32.dll")
    assert kern["status"] == "not_implemented"
    assert kern["implemented"] == []
    assert report["counts"]["implemented"] == 0
    assert "not a completion claim" in report["verdict"]


# --------------------------------------------------------------------------- #
# Flags cannot fabricate status
# --------------------------------------------------------------------------- #

def test_flag_only_claims_never_produce_implemented():
    cov = coverage(cov_api("ExitProcess", implemented=True, registered=True,
                           status="implemented", implemented_count=1, files=1))
    defn = definition(fam("kernel32.dll", ["ExitProcess"], status="implemented",
                          critical=False, implemented_count=1))
    report = ft.build_report(defn, cov)
    kern = family_row(report, "kernel32.dll")
    assert kern["status"] == "not_implemented"
    assert kern["implemented"] == []
    assert kern["implemented_count"] == 0
    assert kern["missing_apis"] == [
        {"name": "ExitProcess", "reason": "no_behavior_evidence"}]
    assert report["counts"]["implemented"] == 0
    rendered = ft.render_json(report) + ft.render_markdown(report)
    assert "| `kernel32.dll` | system | implemented |" not in rendered


def test_flags_cannot_upgrade_partial_to_implemented():
    cov = coverage(
        cov_api("ExitProcess", evidence_value=evidence()),
        cov_api("GetTickCount", implemented=True, registered=True,
                status="implemented"),
    )
    defn = definition(fam("kernel32.dll", ["ExitProcess", "GetTickCount"],
                          status="implemented"))
    kern = family_row(ft.build_report(defn, cov), "kernel32.dll")
    assert kern["status"] == "partial"
    assert [r["name"] for r in kern["implemented"]] == ["ExitProcess"]
    assert [r["name"] for r in kern["missing_apis"]] == ["GetTickCount"]


@pytest.mark.parametrize("fabrication, malformed", [
    ({"implemented": True, "registered": True}, False),
    ({"status": "implemented"}, False),
    ({"evidence": {"implemented": True, "registered": True}}, True),
    ({"evidence": {"test": "PASS"}}, True),
    ({"evidence": {"selftest": ""}}, True),
    ({"evidence": {"selftest": ["", None]}}, True),
    ({"evidence": {"selftest": "tests/x.py::test_y", "verdict": "skipped"}}, True),
    ({"evidence": "tests/x.py::test_y"}, True),
    ({"evidence": {}}, True),
])
def test_flag_only_and_malformed_evidence_never_implement(tmp_path, fabrication,
                                                          malformed):
    cov = coverage(cov_api("ExitProcess", **fabrication))
    defn = definition(fam("kernel32.dll", ["ExitProcess"]))
    if malformed:
        with pytest.raises(ft.InputError):
            ft.build_report(defn, cov)
        rc, json_out, md_out = run_cli(tmp_path, cov, defn)
        assert rc == 2
        assert not json_out.exists() and not md_out.exists()
        return
    report = ft.build_report(defn, cov)
    kern = family_row(report, "kernel32.dll")
    assert kern["status"] != "implemented"
    assert kern["implemented"] == []
    assert report["counts"]["implemented"] == 0


def test_evidence_supports_implementation_unit_cases():
    assert ft.evidence_supports_implementation({"selftest": "tests/x.py::test_y"})
    assert ft.evidence_supports_implementation({"frame_proof": "reports/f.json"})
    assert ft.evidence_supports_implementation({"witness": ["a", "b"]})
    assert not ft.evidence_supports_implementation(None)
    assert not ft.evidence_supports_implementation("tests/x.py::test_y")
    assert not ft.evidence_supports_implementation({"registered": True})
    assert not ft.evidence_supports_implementation({"selftest": "PASS"})
    assert not ft.evidence_supports_implementation(
        {"selftest": "tests/x.py::test_y", "status": "skipped"})


# --------------------------------------------------------------------------- #
# Tamper detection / fail-closed pins
# --------------------------------------------------------------------------- #

def test_tampered_evidence_is_rejected(tmp_path, capsys):
    original = evidence("tests/test_win32hle.py::test_exit_process")
    cov = coverage(cov_api("ExitProcess", evidence_value=original,
                           family="kernel32.dll"))
    defn = definition(fam("kernel32.dll", [
        {"name": "ExitProcess", "evidence_sha256": digest(original)}]))
    assert family_row(ft.build_report(defn, cov), "kernel32.dll")["status"] == "implemented"

    # Someone edits the evidence reference to a different (possibly fake) test.
    cov["apis"][0]["evidence"] = evidence("tests/test_win32hle.py::test_fake")
    with pytest.raises(ft.InputError, match="tampered evidence"):
        ft.build_report(defn, cov)
    rc, json_out, md_out = run_cli(tmp_path, cov, defn)
    assert rc == 2
    assert not json_out.exists() and not md_out.exists()
    assert "tampered evidence" in capsys.readouterr().err


def test_missing_pinned_evidence_fails_closed(tmp_path):
    defn = definition(fam("kernel32.dll", [
        {"name": "ExitProcess", "evidence_sha256": digest(evidence())}]))
    cov = coverage(cov_api("ExitProcess", implemented=True, status="implemented"))
    with pytest.raises(ft.InputError, match="pinned evidence missing"):
        ft.build_report(defn, cov)
    rc, json_out, md_out = run_cli(tmp_path, cov, defn)
    assert rc == 2
    assert not json_out.exists() and not md_out.exists()


def test_coverage_sha256_pin_rejects_modified_coverage(tmp_path, capsys):
    cov = coverage(cov_api("ExitProcess", evidence_value=evidence()))
    cov_path = write(tmp_path / "coverage.json", cov)
    pin = sha256_file(cov_path)
    defn = definition(fam("kernel32.dll", ["ExitProcess"]),
                      coverage_sha256=pin)
    assert family_row(ft.build_report(defn, cov, coverage_sha256=pin),
                      "kernel32.dll")["status"] == "implemented"

    # Modify the coverage document after the pin was recorded.  The pin is
    # verified against the raw bytes the CLI reads, not a caller-supplied
    # digest, so a caller cannot vouch for a document it has just edited.
    cov["apis"][0]["evidence"]["selftest"] = "tests/x.py::test_fake"
    write(cov_path, cov)  # tampered bytes on disk; pin still records the original
    out = tmp_path / "out" / "status.json"
    rc = ft.main(["--coverage", str(cov_path),
                  "--families", str(write(tmp_path / "families.json", defn)),
                  "--json-out", str(out)])
    assert rc == 2
    assert not out.exists()
    assert "coverage_sha256 pin mismatch" in capsys.readouterr().err


def test_pinned_coverage_requires_a_cli_supplied_file():
    defn = definition(fam("kernel32.dll", ["ExitProcess"]),
                      coverage_sha256="0" * 64)
    with pytest.raises(ft.InputError, match="needs a CLI-supplied coverage file"):
        ft.build_report(defn, coverage(cov_api("ExitProcess")))


def test_evidence_digest_is_canonical_and_independent():
    a = {"selftest": "tests/x.py::test_y", "oracle": "reports/f.json"}
    b = {"oracle": "reports/f.json", "selftest": "tests/x.py::test_y"}
    assert ft.evidence_digest(a) == ft.evidence_digest(b) == digest(a)


# --------------------------------------------------------------------------- #
# Malformed input fails closed
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad_cov, bad_defn", [
    ("this is not json", definition(fam("kernel32.dll", ["ExitProcess"]))),
    ("[]", definition(fam("kernel32.dll", ["ExitProcess"]))),
    ("{}", definition(fam("kernel32.dll", ["ExitProcess"]))),
    (coverage(cov_api("ExitProcess")), {"schema_version": "1.0.0", "families": []}),
    (coverage(cov_api("ExitProcess")), definition(fam("kernel32.dll", []))),
    (coverage(cov_api("ExitProcess")),
     definition({"family": "kernel32.dll", "kind": "system", "apis": None})),
    (coverage(cov_api("ExitProcess")), definition(fam("kernel32.dll", "ExitProcess", kind="unknown"))),
    (coverage(cov_api("ExitProcess")), definition(fam("kernel32.dll", ["ExitProcess"]),
                                                  fam("kernel32.dll", ["ExitProcess"]))),
    (coverage(cov_api("ExitProcess")), definition(fam("kernel32.dll", ["ExitProcess", "ExitProcess"]))),
    (coverage(cov_api("ExitProcess")), definition(fam("kernel32.dll", [
        {"name": "ExitProcess", "evidence_sha256": "not-a-digest"}]))),
    (coverage({"status": "implemented"}), definition(fam("kernel32.dll", ["ExitProcess"]))),
    (coverage(cov_api("ExitProcess", evidence_value=evidence()),
              cov_api("ExitProcess", evidence_value=evidence("tests/fake.py::test_x"))),
     definition(fam("kernel32.dll", ["ExitProcess"]))),
    (coverage(cov_api("NotInDefinition", evidence_value=evidence())),
     definition(fam("kernel32.dll", ["ExitProcess"]))),
    (coverage(cov_api("ExitProcess", evidence_value=evidence(), family="user32.dll")),
     definition(fam("kernel32.dll", ["ExitProcess"]))),
    (coverage(cov_api("ExitProcess")), definition(fam("kernel32.dll", ["ExitProcess"]),
                                                  coverage_sha256="xyz")),
])
def test_malformed_input_fails_closed_and_writes_nothing(tmp_path, bad_cov, bad_defn):
    rc, json_out, md_out = run_cli(tmp_path, bad_cov, bad_defn)
    assert rc == 2
    assert not json_out.exists() and not md_out.exists()


def test_missing_input_files_and_no_arguments_fail_closed(tmp_path):
    assert ft.main([]) == 2
    assert ft.main(["--coverage", str(tmp_path / "absent.json"),
                    "--families", str(tmp_path / "absent.json")]) == 2
    cov_path = write(tmp_path / "coverage.json",
                     coverage(cov_api("ExitProcess", evidence_value=evidence())))
    assert ft.main(["--coverage", str(cov_path),
                    "--families", str(tmp_path / "absent.json")]) == 2


def test_percent_guard_blocks_an_accidental_percentage(tmp_path, monkeypatch):
    monkeypatch.setattr(ft, "render_markdown", lambda report: "coverage: 95%")
    rc, json_out, md_out = run_cli(
        tmp_path,
        coverage(cov_api("ExitProcess", evidence_value=evidence())),
        definition(fam("kernel32.dll", ["ExitProcess"])))
    assert rc == 2
    assert not json_out.exists() and not md_out.exists()


# --------------------------------------------------------------------------- #
# Deterministic artifacts / CLI behavior
# --------------------------------------------------------------------------- #

def sample_inputs():
    cov = coverage(
        cov_api("ExitProcess", evidence_value=evidence(), family="kernel32.dll"),
        cov_api("GetTickCount", family="kernel32.dll"),
        cov_api("SDL_Quit", evidence_value=evidence("tests/x.py::test_quit"),
                family="sdl2.dll"),
    )
    defn = definition(
        fam("kernel32.dll", ["ExitProcess", "GetTickCount"]),
        fam("sdl2.dll", ["SDL_Quit", "SDL_CreateWindow"], kind="game-owned"),
    )
    return cov, defn


def test_json_and_markdown_are_deterministic_without_percentages():
    cov, defn = sample_inputs()
    first = ft.build_report(defn, cov)
    second = ft.build_report(defn, cov)
    json_a = ft.render_json(first)
    json_b = ft.render_json(second)
    md_a = ft.render_markdown(first)
    md_b = ft.render_markdown(second)
    assert json_a == json_b and md_a == md_b
    assert json.loads(json_a) == first
    assert json_a.endswith("\n")
    assert PERCENT_RE.search(json_a) is None
    assert PERCENT_RE.search(md_a) is None
    assert '"completion_claim": false' in json_a
    assert '"no_percentage": true' in json_a
    assert "aggregate percentage" in md_a
    assert "not a completion claim" in md_a
    assert "| `kernel32.dll` | system | partial |" in md_a
    assert "| `sdl2.dll` | game-owned | deferred |" in md_a
    assert "`GetTickCount`" in md_a and "`SDL_CreateWindow`" in md_a


def test_cli_writes_supplied_paths_and_honest_summary(tmp_path, capsys):
    cov, defn = sample_inputs()
    rc, json_out, md_out = run_cli(tmp_path, cov, defn)
    assert rc == 0
    assert "not a completion claim" in capsys.readouterr().out
    report = json.loads(json_out.read_text(encoding="utf-8"))
    markdown = md_out.read_text(encoding="utf-8")
    assert report["coverage"].endswith("coverage.json")
    assert len(report["coverage_sha256"]) == 64
    assert len(report["family_definition_sha256"]) == 64
    assert report["policy"]["completion_claim"] is False
    assert report["counts"]["missing_apis"] == 2
    assert "Missing APIs (explicit)" in markdown
    assert PERCENT_RE.search(markdown) is None


def test_cli_default_outputs_are_derived_from_coverage_path(tmp_path):
    cov, defn = sample_inputs()
    cov_path = write(tmp_path / "coverage.json", cov)
    defn_path = write(tmp_path / "families.json", defn)
    assert ft.main([str(cov_path), str(defn_path)]) == 0
    assert (tmp_path / "coverage.json.family-tracker.json").is_file()
    assert (tmp_path / "coverage.json.family-tracker.md").is_file()


def test_tool_has_no_hard_coded_paths_or_repo_scanning():
    source = Path(ft.__file__).resolve().read_text(encoding="utf-8")
    for marker in ("/Users/", "/private/var/", "C:\\\\", "RECOMP_CORPUS",
                   "rglob(", ".glob("):
        assert marker not in source
