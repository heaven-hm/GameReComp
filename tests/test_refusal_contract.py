"""The refusal-contract checker must fail closed on synthetic records.

Everything here is generated in-process: no game binary, no live CLI run and no
checked-in artifact. The records model what a caller observed when feeding an
unsupported input class to the CLI; the checker is tested on how it judges
those records, not on any claim about a real game or platform.
"""

import contextlib
import copy
import io
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import refusal_contract as rc  # noqa: E402


def rec(input_class, diagnostic, exit_code, label=None):
    item = {"input_class": input_class, "diagnostic": diagnostic,
            "exit_code": exit_code}
    if label is not None:
        item["label"] = label
    return item


def good_records():
    """One explicit, named, non-zero refusal for each required class."""
    return [
        rec("x64", "unsupported input architecture: x86_64", 4, "x64-case"),
        rec(".NET", ".NET/CLR assembly supplied", 2, "dotnet-case"),
        rec("Win16", "Win16 NE executable supplied", 3, "win16-case"),
        rec("ELF", "ELF 64 executable supplied", 1, "elf-case"),
        rec("Mach-O", "Mach-O fat/universal binary supplied", 255, "macho-case"),
    ]


def text(report):
    return json.dumps(report, indent=2, sort_keys=True)


# --- accept only complete, explicit evidence -------------------------------

def test_complete_named_refusals_pass():
    report = rc.evaluate(good_records())
    assert report["schema"] == rc.SCHEMA
    assert report["verdict"] == rc.VERDICT_PASS
    assert report["passed"] is True
    assert report["fail_closed"] is True
    assert report["counts"] == {"pass": 5, "fail": 0}
    assert report["reasons"] == []
    assert report["records_seen"] == 5
    assert tuple(report["classes"]) == rc.INPUT_CLASSES
    assert all(entry["status"] == rc.VERDICT_PASS
               for entry in report["classes"].values())


def test_missing_record_is_a_fail_not_a_pass_by_absence():
    empty = rc.evaluate([])
    assert empty["verdict"] == rc.VERDICT_FAIL
    assert empty["counts"] == {"pass": 0, "fail": 5}
    assert all("missing_record" in reason for reason in empty["reasons"])
    assert len(empty["reasons"]) == 5

    partial = good_records()
    del partial[3]  # no ELF record at all
    report = rc.evaluate(partial)
    assert report["verdict"] == rc.VERDICT_FAIL
    assert report["counts"] == {"pass": 4, "fail": 1}
    assert [r for r in report["reasons"] if r.startswith("ELF:")] == [
        "ELF: missing_record: no refusal record supplied for ELF"]


def test_missing_or_malformed_input_never_yields_pass():
    records, sources, errors = rc.load_records()
    assert records == [] and sources == []
    assert any("no refusal records supplied" in e for e in errors)
    assert rc.evaluate(records, errors=errors)["verdict"] == rc.VERDICT_FAIL

    assert rc.evaluate([], errors=["bad input"])["verdict"] == rc.VERDICT_FAIL
    assert rc.evaluate(["not an object"])["verdict"] == rc.VERDICT_FAIL


# --- silent success and vague diagnostics ----------------------------------

@pytest.mark.parametrize("diagnostic,expected", [
    ("x86_64 was accepted", "silent_success"),
    ("", "silent_success"),
])
def test_exit_zero_is_silent_success_even_with_a_message(diagnostic, expected):
    records = good_records()
    records[0] = rec("x64", diagnostic, 0, "x64-case")
    report = rc.evaluate(records)
    assert report["verdict"] == rc.VERDICT_FAIL
    assert report["classes"]["x64"]["status"] == rc.VERDICT_FAIL
    assert any(expected in reason for reason in report["reasons"])
    if not diagnostic:
        assert any("missing_diagnostic" in reason for reason in report["reasons"])


def test_numeric_string_exit_zero_is_also_silent_success():
    records = good_records()
    records[0] = rec("x64", "x86_64 supplied", "0", "x64-case")
    report = rc.evaluate(records)
    assert report["verdict"] == rc.VERDICT_FAIL
    assert any("silent_success" in r for r in report["reasons"])


def test_invalid_exit_code_is_not_a_pass():
    records = good_records()
    records[1] = rec(".NET", ".NET assembly supplied", None, "dotnet-case")
    report = rc.evaluate(records)
    assert report["verdict"] == rc.VERDICT_FAIL
    assert any("invalid_exit_code" in r for r in report["reasons"])


@pytest.mark.parametrize("vague", [
    "unsupported input",
    "unsupported input type",
    "error",
    "invalid format",
    "failed",
    "not supported",
    "rejected",
])
def test_vague_diagnostic_is_detected(vague):
    records = good_records()
    records[0] = rec("x64", vague, 4, "x64-case")
    report = rc.evaluate(records)
    assert report["verdict"] == rc.VERDICT_FAIL
    assert report["classes"]["x64"]["reasons"] == [
        f"vague_or_unnamed_diagnostic: x64-case does not name x64"]


def test_diagnostic_naming_the_wrong_class_is_detected():
    records = good_records()
    records[0] = rec("x64", "ELF 64 executable supplied", 4, "x64-case")
    report = rc.evaluate(records)
    assert report["verdict"] == rc.VERDICT_FAIL
    assert any(r.startswith("x64: wrong_class_named:") and "ELF" in r
               for r in report["reasons"])


# --- ambiguity and aliases -------------------------------------------------

def test_conflicting_records_for_one_class_are_ambiguous():
    records = good_records()
    records.append(rec("x64", "x86_64 refused", 9, "x64-second"))
    report = rc.evaluate(records)
    assert report["verdict"] == rc.VERDICT_FAIL
    assert any("ambiguous_conflict" in r for r in report["reasons"])


def test_identical_duplicate_records_are_not_ambiguous():
    records = good_records()
    records.append(copy.deepcopy(records[0]))
    report = rc.evaluate(records)
    assert report["verdict"] == rc.VERDICT_PASS
    assert not any("ambiguous" in r for r in report["reasons"])


@pytest.mark.parametrize("cls,diagnostic,index", [
    ("x86_64", "x64 binary supplied", 0),
    ("amd64", "AMD64 image supplied", 0),
    ("pe32+", "PE32+ image supplied", 0),
    ("dotnet", "dotnet assembly supplied", 1),
    ("CLR", "managed CLR image supplied", 1),
    ("win16", "NE executable supplied", 2),
    ("16-bit", "16-bit executable supplied", 2),
    ("macho", "macho binary supplied", 4),
    ("Mach-O", "fat Mach-O binary supplied", 4),
    ("elf64", "ELF64 binary supplied", 3),
])
def test_aliases_resolve_to_exactly_one_required_class(cls, diagnostic, index):
    records = good_records()
    records[index] = rec(cls, diagnostic, 4, "aliased")
    report = rc.evaluate(records)
    assert report["verdict"] == rc.VERDICT_PASS, report["reasons"]


@pytest.mark.parametrize("cls", ["arm64", "unknown", "", None])
def test_unrecognized_class_is_ambiguous_and_fails(cls):
    records = good_records()
    records[0] = rec(cls, "x86_64 supplied", 4, "aliased")
    report = rc.evaluate(records)
    assert report["verdict"] == rc.VERDICT_FAIL
    assert any("unrecognized input_class" in r for r in report["reasons"])
    assert any("missing_record" in r for r in report["reasons"])


# --- deterministic verdicts -----------------------------------------------

def test_report_is_byte_identical_across_runs_and_input_order():
    baseline = text(rc.evaluate(good_records()))
    assert text(rc.evaluate(good_records())) == baseline
    shuffled = good_records()
    random.Random(7).shuffle(shuffled)
    assert text(rc.evaluate(shuffled)) == baseline

    bad = good_records()
    bad[0] = rec("x64", "unsupported input", 0, "x64-case")
    bad[2] = rec("Win16", "ELF supplied", 4, "win16-case")
    bad_baseline = text(rc.evaluate(bad))
    random.Random(11).shuffle(bad)
    assert text(rc.evaluate(bad)) == bad_baseline
    assert bad_baseline != baseline


# --- CLI -------------------------------------------------------------------

def test_cli_passes_on_a_records_file_and_writes_json(tmp_path):
    path = tmp_path / "records.json"
    path.write_text(json.dumps({"records": good_records()}))
    out = tmp_path / "report.json"
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = rc.main(["--records", str(path), "--json", str(out), "--quiet"])
    assert code == rc.EXIT_PASS
    assert stdout.getvalue() == ""
    doc = json.loads(out.read_text())
    assert doc["verdict"] == rc.VERDICT_PASS
    assert doc["counts"] == {"pass": 5, "fail": 0}


def test_cli_fails_with_named_reasons_and_no_silent_pass(tmp_path):
    records = good_records()
    records[4] = rec("Mach-O", "unhandled", 0, "macho-case")
    path = tmp_path / "records.json"
    path.write_text(json.dumps(records))
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = rc.main(["--records", str(path)])
    assert code == rc.EXIT_FAIL
    printed = stdout.getvalue()
    assert "refusal-contract: FAIL (4/5 classes pass, 2 reasons)" in printed
    assert "silent_success" in printed
    assert "vague_or_unnamed_diagnostic" in printed


def test_cli_inline_records_and_stdin(tmp_path):
    records = good_records()
    inline = ["--record", json.dumps(records[0])]
    for item in records[1:]:
        inline += ["--record", json.dumps(item)]
    assert rc.main(inline + ["--quiet"]) == rc.EXIT_PASS

    original = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"records": records}))
    try:
        assert rc.main(["--records", "-", "--quiet"]) == rc.EXIT_PASS
    finally:
        sys.stdin = original


@pytest.mark.parametrize("file_body,expected", [
    ("{not json", "invalid JSON"),
    ('{"summary": "no records"}', "neither 'records' nor a refusal record"),
    ('{"records": "nope"}', "'records' is not a list"),
])
def test_cli_malformed_input_fails_closed(tmp_path, file_body, expected):
    path = tmp_path / "records.json"
    path.write_text(file_body)
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = rc.main(["--records", str(path)])
    assert code == rc.EXIT_FAIL
    assert expected in stdout.getvalue()
    assert "5/5 classes pass" not in stdout.getvalue()


def test_cli_with_no_records_fails(tmp_path):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = rc.main(["--quiet"])
    assert code == rc.EXIT_FAIL
    assert "no refusal records supplied" not in stdout.getvalue()  # --quiet
    with contextlib.redirect_stdout(stdout):
        code = rc.main([])
    assert code == rc.EXIT_FAIL
    assert "no refusal records supplied" in stdout.getvalue()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
