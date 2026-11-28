"""Tests for the CLI-supplied, evidence-led API gap resolver.

All fixtures are synthetic and written under pytest's ``tmp_path``: no repo
paths, corpus, game assets or live registries are read.  The name list below
mirrors the categories from issue #28 only as *input data* -- the resolver has
no built-in names and must not resolve any of them without supplied evidence.

Run with: python3 -m pytest tests/test_api_gap_resolver.py -q
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import api_gap_resolver as resolver

# Synthetic reproduction of the issue's name set (Windows, CRT, SDL, audio,
# winsock).  These are CLI input values, never resolver constants.
ISSUE_STYLE_NAMES = [
    "CreatePatternBrush", "LoadLibraryW", "CreateIconIndirect", "DrawTextW",
    "LoadCursorW", "MessageBoxW", "__initenv",
    "SDL_SetCursor", "SDL_GetCursor", "SDL_FreeCursor", "SDL_GetError",
    "SDL_strdup", "SDL_strlen", "SDL_GetClipboardText",
    "Mix_SetPostMix", "WSACancelBlockingCall",
    "fopen", "fprintf", "fwrite",
]


# --------------------------------------------------------------------------- #
# Synthetic fixture helpers
# --------------------------------------------------------------------------- #

def write_json(path: Path, doc: object) -> Path:
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def missing_doc(*names: str, hints: bool = False) -> dict:
    apis = []
    for name in names:
        record: dict[str, object] = {"name": name}
        if hints:
            record.update(status="RESOLVED", resolved=True, implemented=True)
        apis.append(record)
    return {"schema_version": "1.0.0", "apis": apis}


def shims_doc(*names: str) -> dict:
    return {"schema_version": "1.0.0", "shims": list(names)}


def evidence_doc(*records: dict) -> dict:
    return {"schema_version": "1.0.0", "records": list(records)}


def behavior_evidence(api: str) -> dict:
    return {"api": api,
            "behavior": {"ref": f"tests/test_{api}.py::test_behavior",
                         "result": "pass"}}


def add_evidence(tmp_path: Path, api: str, *, filename: str | None = None,
                 content: dict | None = None) -> tuple[dict, Path]:
    """Write an evidence file and return its (index record, path)."""
    filename = filename or f"evidence-{api}.json"
    path = tmp_path / filename
    write_json(path, content if content is not None else behavior_evidence(api))
    record = {"api": api, "path": str(path),
              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return record, path


def build(missing: dict, shims: dict, evidence: dict | None = None) -> dict:
    return resolver.build_status(missing, shims, evidence)


def row_for(report: dict, name: str) -> dict:
    return next(row for row in report["apis"] if row["name"] == name)


# --------------------------------------------------------------------------- #
# Resolution + gaps
# --------------------------------------------------------------------------- #

def test_resolved_requires_shim_and_hash_verified_behavior_file(tmp_path):
    record, _ = add_evidence(tmp_path, "MessageBoxW")
    report = build(missing_doc("MessageBoxW"), shims_doc("MessageBoxW"),
                   evidence_doc(record))
    row = row_for(report, "MessageBoxW")
    assert row["status"] == "RESOLVED"
    assert row["reason"] == "verified_behavior_evidence"
    assert row["registered"] is True and row["evidence_supplied"] is True
    assert row["sha256_recomputed"] == record["sha256"] == row["sha256_expected"]
    assert row["behavior_ref"] == "tests/test_MessageBoxW.py::test_behavior"
    assert report["counts"] == {"apis": 1, "RESOLVED": 1,
                                "UNPROVEN": 0, "MISSING": 0}


def test_evidence_alone_cannot_resolve_an_unregistered_name(tmp_path):
    record, _ = add_evidence(tmp_path, "MessageBoxW")
    report = build(missing_doc("MessageBoxW"), shims_doc(), evidence_doc(record))
    row = row_for(report, "MessageBoxW")
    assert row["status"] == "MISSING"
    assert row["reason"] == "no_registered_shim"
    assert row["registered"] is False and row["evidence_supplied"] is True
    assert row["sha256_recomputed"] is None  # no shim -> evidence is not consulted
    assert report["counts"]["RESOLVED"] == 0


def test_no_close_keyword_prefix_or_case_folded_matching(tmp_path):
    names = ("MessageBoxW", "SDL_GetError")
    shims = ("MessageBoxA", "messageboxw", "SDL_SetCursor", "SDL_GetErrorA")
    records = [add_evidence(tmp_path, name)[0] for name in names]
    report = build(missing_doc(*names), shims_doc(*shims), evidence_doc(*records))
    for name in names:
        row = row_for(report, name)
        assert row["status"] == "MISSING", name
        assert row["reason"] == "no_registered_shim"
    assert report["counts"]["RESOLVED"] == 0


def test_registered_shim_without_evidence_record_is_unproven():
    report = build(missing_doc("LoadLibraryW"), shims_doc("LoadLibraryW"))
    row = row_for(report, "LoadLibraryW")
    assert row["status"] == "UNPROVEN"
    assert row["reason"] == "no_evidence_record"
    assert row["registered"] is True and row["evidence_supplied"] is False


def test_empty_evidence_index_is_the_same_gap():
    report = build(missing_doc("LoadLibraryW"), shims_doc("LoadLibraryW"),
                   evidence_doc())
    assert row_for(report, "LoadLibraryW")["reason"] == "no_evidence_record"


def test_unreadable_evidence_file_is_unproven(tmp_path):
    record = {"api": "DrawTextW", "path": str(tmp_path / "absent.json"),
              "sha256": "0" * 64}
    report = build(missing_doc("DrawTextW"), shims_doc("DrawTextW"),
                   evidence_doc(record))
    row = row_for(report, "DrawTextW")
    assert row["status"] == "UNPROVEN"
    assert row["reason"] == "evidence_file_unreadable"
    assert row["sha256_recomputed"] is None


# --------------------------------------------------------------------------- #
# Tamper and fabricated success
# --------------------------------------------------------------------------- #

def test_tampered_evidence_file_is_unproven_with_recomputed_hash(tmp_path):
    record, path = add_evidence(tmp_path, "DrawTextW")
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    report = build(missing_doc("DrawTextW"), shims_doc("DrawTextW"),
                   evidence_doc(record))
    row = row_for(report, "DrawTextW")
    assert row["status"] == "UNPROVEN"
    assert row["reason"] == "evidence_hash_mismatch"
    assert row["sha256_expected"] == record["sha256"]
    assert row["sha256_recomputed"] != record["sha256"]
    assert row["sha256_recomputed"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_success_flags_without_behavior_reference_are_unproven(tmp_path):
    fake = {"api": "CreatePatternBrush", "status": "RESOLVED", "resolved": True,
            "implemented": True, "behavior": {"result": "pass"}}
    record, _ = add_evidence(tmp_path, "CreatePatternBrush", content=fake)
    report = build(missing_doc("CreatePatternBrush", hints=True),
                   shims_doc("CreatePatternBrush"), evidence_doc(record))
    row = row_for(report, "CreatePatternBrush")
    assert row["status"] == "UNPROVEN"
    assert row["reason"] == "evidence_not_behavior_proof"


@pytest.mark.parametrize("content", [
    {"api": "LoadCursorW", "behavior": {"ref": "r", "result": "fail"}},
    {"api": "LoadCursorW", "behavior": {"ref": "r"}, "status": "skipped"},
    {"api": "LoadCursorW", "behavior": {"ref": "r"}, "outcome": "error"},
])
def test_failed_or_skipped_result_evidence_is_unproven(tmp_path, content):
    record, _ = add_evidence(tmp_path, "LoadCursorW", content=content)
    report = build(missing_doc("LoadCursorW"), shims_doc("LoadCursorW"),
                   evidence_doc(record))
    row = row_for(report, "LoadCursorW")
    assert row["status"] == "UNPROVEN"
    assert row["reason"] == "evidence_failed_result"


def test_evidence_for_a_different_api_does_not_resolve_its_neighbour(tmp_path):
    record, _ = add_evidence(
        tmp_path, "LoadCursorW",
        content=behavior_evidence("CreateIconIndirect"))
    report = build(missing_doc("LoadCursorW", "CreateIconIndirect"),
                   shims_doc("LoadCursorW", "CreateIconIndirect"),
                   evidence_doc(record))
    assert row_for(report, "LoadCursorW")["reason"] == "evidence_api_mismatch"
    assert row_for(report, "CreateIconIndirect")["reason"] == "no_evidence_record"
    assert report["counts"]["RESOLVED"] == 0


def test_invalid_json_evidence_with_matching_hash_is_unproven(tmp_path):
    path = tmp_path / "garbage.json"
    path.write_text("not json at all", encoding="utf-8")
    record = {"api": "__initenv", "path": str(path),
              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    report = build(missing_doc("__initenv"), shims_doc("__initenv"),
                   evidence_doc(record))
    row = row_for(report, "__initenv")
    assert row["status"] == "UNPROVEN"
    assert row["reason"] == "evidence_invalid_json"


def test_no_built_in_names_and_no_hard_coded_paths():
    source = Path(resolver.__file__).read_text(encoding="utf-8")
    for name in ("CreatePatternBrush", "Mix_SetPostMix",
                 "WSACancelBlockingCall", "__initenv", "MessageBoxW"):
        assert name not in source, f"resolver hard-codes API name {name}"
    for marker in ("/Users/", "runtime/win32hle", "ROOT =", "Path(__file__)"):
        assert marker not in source, f"resolver hard-codes path marker {marker}"


# --------------------------------------------------------------------------- #
# Issue-shaped set, determinism, fail-closed validation
# --------------------------------------------------------------------------- #

def test_all_issue_style_names_are_gaps_without_supplied_proof():
    report = build(missing_doc(*ISSUE_STYLE_NAMES), shims_doc())
    assert len(ISSUE_STYLE_NAMES) == 19
    assert [row["name"] for row in report["apis"]] == sorted(ISSUE_STYLE_NAMES)
    assert {row["status"] for row in report["apis"]} == {"MISSING"}
    assert report["counts"]["MISSING"] == 19
    assert report["counts"]["RESOLVED"] == 0
    assert "%" not in resolver.render_json(report)
    assert "completion claim" in report["verdict"]


def test_report_is_deterministic_and_sorted_regardless_of_input_order(tmp_path):
    record, _ = add_evidence(tmp_path, "DrawTextW")
    names = ("MessageBoxW", "DrawTextW", "CreatePatternBrush")
    shims = ("CreatePatternBrush", "DrawTextW")
    first = build(missing_doc(*names), shims_doc(*shims), evidence_doc(record))
    second = build(missing_doc(*reversed(names)), shims_doc(*reversed(shims)),
                   evidence_doc(record))
    assert resolver.render_json(first) == resolver.render_json(second)
    assert resolver.render_markdown(first) == resolver.render_markdown(second)
    assert [row["name"] for row in first["apis"]] == sorted(names)


def test_malformed_inputs_fail_closed():
    with pytest.raises(resolver.InputError):
        build({"schema_version": "1.0.0", "apis": []}, shims_doc())
    with pytest.raises(resolver.InputError):
        build(missing_doc("A", "A"), shims_doc())
    with pytest.raises(resolver.InputError):
        build(missing_doc("A"), {"schema_version": "1.0.0", "shims": None})
    with pytest.raises(resolver.InputError):
        build(missing_doc("A"), shims_doc(),
              evidence_doc({"api": "A", "path": "x", "sha256": "abcd"}))
    with pytest.raises(resolver.InputError):
        build(missing_doc("A"), shims_doc(), evidence_doc(
            {"api": "A", "path": "x", "sha256": "0" * 64},
            {"api": "A", "path": "y", "sha256": "1" * 64}))


# --------------------------------------------------------------------------- #
# CLI behaviour
# --------------------------------------------------------------------------- #

def cli_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    record, _ = add_evidence(tmp_path, "MessageBoxW")
    missing = write_json(tmp_path / "missing.json", missing_doc("MessageBoxW"))
    shims = write_json(tmp_path / "shims.json", shims_doc("MessageBoxW"))
    evidence = write_json(tmp_path / "evidence.json", evidence_doc(record))
    return missing, shims, evidence


def test_cli_writes_deterministic_outputs_and_no_completion_claim(tmp_path, capsys):
    missing, shims, evidence = cli_inputs(tmp_path)
    out_a = tmp_path / "status-a.json"
    md_a = tmp_path / "status-a.md"
    args = ["--missing", str(missing), "--shims", str(shims),
            "--evidence", str(evidence), "--out", str(out_a),
            "--markdown-out", str(md_a)]
    assert resolver.main(args) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not a completion claim" in captured.err
    first = out_a.read_bytes()
    assert json.loads(first)["counts"]["RESOLVED"] == 1
    assert "| `MessageBoxW` | RESOLVED |" in md_a.read_text(encoding="utf-8")

    out_b = tmp_path / "status-b.json"
    args[args.index(str(out_a))] = str(out_b)
    assert resolver.main(args) == 0
    assert out_b.read_bytes() == first  # byte-for-byte deterministic

    report = json.loads(first)
    assert report["input_sha256"]["missing"] == hashlib.sha256(
        missing.read_bytes()).hexdigest()
    assert report["input_sha256"]["shims"] == hashlib.sha256(
        shims.read_bytes()).hexdigest()
    assert report["input_sha256"]["evidence"] == hashlib.sha256(
        evidence.read_bytes()).hexdigest()


def test_cli_prints_canonical_json_to_stdout_when_out_omitted(tmp_path, capsys):
    missing, shims, evidence = cli_inputs(tmp_path)
    assert resolver.main(["--missing", str(missing), "--shims", str(shims),
                          "--evidence", str(evidence)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["counts"]["RESOLVED"] == 1
    assert "not a completion claim" in captured.err


def test_cli_bad_input_exits_two_without_writing_output(tmp_path, capsys):
    missing = write_json(tmp_path / "missing.json",
                         {"schema_version": "1.0.0", "apis": []})
    shims = write_json(tmp_path / "shims.json", shims_doc("A"))
    out = tmp_path / "never.json"
    assert resolver.main(["--missing", str(missing), "--shims", str(shims),
                          "--out", str(out)]) == 2
    assert not out.exists()
    assert "api_gap_resolver" in capsys.readouterr().err
