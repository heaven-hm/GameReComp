"""Deterministic tests for the fail-closed cross-OS gate matrix runner.

Run with: python3 -m pytest tests/test_os_matrix_runner.py -q

Every gate is a fake Python module written into tmp_path. No game corpus,
repository path, host tool or pre-recorded evidence file is read by these
tests. The meta-tests prove that a missing gate, a tampered hash, a fabricated
PASS and a changed artifact can never produce a PASS cell.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import os_matrix_runner as osm

FIXED_TIME = "2026-11-25T00:00:00Z"
FRAME = b"frame 12 from an executed scenario"
FRAME_SHA = hashlib.sha256(FRAME).hexdigest()

GATE_TEMPLATE = '''import argparse, json, sys, time
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
a = ap.parse_args()
payload = json.loads(__PAYLOAD__)
__ACTION__
'''


def write_gate(tmp_path: Path, name: str, cells: object = None, *, mode: str = "file",
               rc: int = 0) -> str:
    """Write a fake gate that emits a JSON verdict exactly as instructed."""
    script = tmp_path / f"{name}.py"
    actions = {
        "file": "Path(a.out).write_text(json.dumps(payload))",
        "stdout": "print(json.dumps(payload))",
        "none": "pass",
        "bad-json": "Path(a.out).write_text('{not json')",
        "exit": (f"Path(a.out).write_text(json.dumps(payload)); "
                 f"raise SystemExit({rc})"),
        "sleep": "time.sleep(5); Path(a.out).write_text(json.dumps(payload))",
    }
    body = (GATE_TEMPLATE.replace("__PAYLOAD__", repr(json.dumps(cells or {"cells": []})))
            .replace("__ACTION__", actions[mode]))
    script.write_text(body, encoding="utf-8")
    return str(script)


def evidence(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(FRAME)
    return str(path)


def good_cell(os_name: str, arch: str, evidence_path: str,
              *, status: str = "PASS", declared: str | None = None) -> dict:
    return {
        "os": os_name, "arch": arch, "status": status,
        "reason": "executed original-windows oracle",
        "evidence_path": evidence_path,
        "evidence_sha256": FRAME_SHA if declared is None else declared,
    }


def run_runner(tmp_path: Path, gates: list[str], *, out: Path | None = None,
               generated_at: str = FIXED_TIME, extra: list[str] | None = None
               ) -> tuple[dict, Path]:
    out = out or tmp_path / "matrix.json"
    argv = []
    for gate in gates:
        argv += ["--gate", gate]
    argv += ["--out", str(out), "--generated-at", generated_at]
    argv += extra or []
    assert osm.main(argv) == 0
    return json.loads(out.read_text(encoding="utf-8")), out


def cell_of(matrix: dict, os_name: str, arch: str) -> dict:
    found = [c for c in matrix["cells"] if c["os"] == os_name and c["arch"] == arch]
    assert len(found) == 1
    return found[0]


def spec(name: str, target: str, coverage: list[str] | None = None) -> str:
    data = {"name": name}
    data["path" if target.endswith(".py") else "module"] = target
    if coverage is not None:
        data["coverage"] = coverage
    return json.dumps(data)


# ---------------------------------------------------------------------------
# Merge: per-cell verdicts, unanimity and evidence verification
# ---------------------------------------------------------------------------

def test_merge_of_two_gates_is_fail_closed_per_cell(tmp_path):
    ev = evidence(tmp_path, "frame.txt")
    a = write_gate(tmp_path, "alpha", {"cells": [
        good_cell("Windows", "x86", ev),
        {"os": "Windows", "arch": "x64", "status": "PASS",
         "reason": "bare written PASS"},
    ]})
    b = write_gate(tmp_path, "bravo", {"results": [
        {"os": "Windows", "arch": "x86", "verdict": "PARTIAL",
         "reason": "partial corroboration"},
        {"os": "macOS", "arch": "x64", "verdict": "FAIL",
         "reason": "concrete failure"},
    ]}, mode="stdout")

    matrix, _ = run_runner(tmp_path, [a, b])

    win_x86 = cell_of(matrix, "Windows", "x86")
    assert win_x86["status"] == "PARTIAL"
    assert win_x86["gates"] == {"alpha": "PASS", "bravo": "PARTIAL"}
    assert "PASS withheld" in win_x86["reason"]
    assert "partial corroboration" in win_x86["reason"]
    assert win_x86["evidence"][0]["verified"] is True
    assert cell_of(matrix, "Windows", "x64")["status"] == "PARTIAL", (
        "a bare PASS without a verified evidence hash is only PARTIAL")
    assert cell_of(matrix, "macOS", "x64")["status"] == "FAIL"
    assert matrix["summary"]["by_status"]["NOT TESTED"] == 9
    assert {gate["name"] for gate in matrix["gates"]} == {"alpha", "bravo"}


def test_unanimous_pass_requires_every_covering_gate(tmp_path):
    ev = evidence(tmp_path, "frame.txt")
    a = write_gate(tmp_path, "alpha", {"cells": [good_cell("Windows", "x86", ev)]})
    b = write_gate(tmp_path, "bravo", {"cells": [good_cell("Windows", "x86", ev)]})
    matrix, _ = run_runner(tmp_path, [a, b])
    passed = cell_of(matrix, "Windows", "x86")
    assert passed["status"] == "PASS"
    assert passed["gates"] == {"alpha": "PASS", "bravo": "PASS"}


# ---------------------------------------------------------------------------
# Missing / broken gates: NOT TESTED per covered cell, never PASS
# ---------------------------------------------------------------------------

def test_missing_module_and_path_are_not_tested_per_covered_cell(tmp_path):
    ev = evidence(tmp_path, "frame.txt")
    good = spec("good", write_gate(tmp_path, "good", {
        "cells": [good_cell("macOS", "x64", ev)]}), ["macOS|x64"])
    missing_module = spec("missingmod", "tools.no_such_gate_module_149",
                          ["Windows|x86"])
    missing_path = spec("missingpath", str(tmp_path / "absent.py"), ["Linux|x64"])

    matrix, _ = run_runner(tmp_path, [good, missing_module, missing_path])

    assert cell_of(matrix, "macOS", "x64")["status"] == "PASS"
    win = cell_of(matrix, "Windows", "x86")
    assert win["status"] == "NOT TESTED"
    assert "module not importable" in win["reason"]
    lin = cell_of(matrix, "Linux", "x64")
    assert lin["status"] == "NOT TESTED"
    assert "gate path not found" in lin["reason"]
    # Uncovered cells are simply untested, not blamed on the missing gates.
    assert "unavailable" not in cell_of(matrix, "Windows", "x64")["reason"]
    assert {g["name"]: g["status"] for g in matrix["gates"]} == {
        "good": "OK", "missingmod": "MISSING", "missingpath": "MISSING"}


def test_missing_gate_with_default_coverage_makes_every_cell_not_tested(tmp_path):
    matrix, _ = run_runner(tmp_path, ["tools.no_such_gate_module_149"])
    assert matrix["summary"]["by_status"]["NOT TESTED"] == 12
    assert all(c["status"] == "NOT TESTED" for c in matrix["cells"])
    assert all("module not importable" in c["reason"] for c in matrix["cells"])


@pytest.mark.parametrize("mode,expected", [
    ("exit", "gate exited"),
    ("bad-json", "not JSON"),
    ("none", "no JSON verdict"),
])
def test_broken_gates_are_not_tested_not_pass(tmp_path, mode, expected):
    bad = spec("broken", write_gate(tmp_path, "broken", {
        "cells": [good_cell("Windows", "x86", evidence(tmp_path, "frame.txt"))]},
        mode=mode, rc=3), ["Windows|x86"])
    matrix, _ = run_runner(tmp_path, [bad])
    cell = cell_of(matrix, "Windows", "x86")
    assert cell["status"] == "NOT TESTED"
    assert expected in cell["reason"]
    assert matrix["summary"]["by_status"]["PASS"] == 0


def test_timeout_is_not_tested(tmp_path):
    gate = spec("slow", write_gate(tmp_path, "slow", {
        "cells": [good_cell("Windows", "x86", evidence(tmp_path, "frame.txt"))]},
        mode="sleep"), ["Windows|x86"])
    matrix, _ = run_runner(tmp_path, [gate], extra=["--timeout", "1"])
    cell = cell_of(matrix, "Windows", "x86")
    assert cell["status"] == "NOT TESTED"
    assert "timed out" in cell["reason"]


# ---------------------------------------------------------------------------
# Tamper detection: a fabricated or changed hash cannot create PASS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("declared,expected_status", [
    ("f" * 64, "FAIL"),            # fabricated hash for an existing file
    (None, "PARTIAL"),             # bare PASS: no hash to verify
])
def test_fabricated_or_missing_hash_never_passes(tmp_path, declared, expected_status):
    ev = evidence(tmp_path, "frame.txt")
    if declared is None:
        claim = {"os": "Windows", "arch": "x86", "status": "PASS",
                 "reason": "bare claim"}
    else:
        claim = good_cell("Windows", "x86", ev, declared=declared)
    gate = write_gate(tmp_path, "fake", {"cells": [claim]})
    matrix, _ = run_runner(tmp_path, [gate])
    cell = cell_of(matrix, "Windows", "x86")
    assert cell["status"] == expected_status
    assert matrix["summary"]["by_status"]["PASS"] == 0


def test_missing_evidence_file_fails_closed(tmp_path):
    claim = good_cell("Windows", "x86", str(tmp_path / "gone.txt"))
    gate = write_gate(tmp_path, "fake", {"cells": [claim]})
    matrix, _ = run_runner(tmp_path, [gate])
    cell = cell_of(matrix, "Windows", "x86")
    assert cell["status"] == "FAIL"
    assert "tampered or unverifiable evidence" in cell["reason"]


def test_verify_detects_changed_matrix_artifact_and_evidence(tmp_path, capsys):
    ev = evidence(tmp_path, "frame.txt")
    gate = spec("alpha", write_gate(tmp_path, "alpha", {
        "cells": [good_cell("Windows", "x86", ev)]}), ["Windows|x86"])
    matrix, out = run_runner(tmp_path, [gate])

    capsys.readouterr()
    assert osm.main(["--verify", str(out)]) == 0
    assert "verdict: OK" in capsys.readouterr().out

    # A hand-written PASS in the matrix is detected by the document hash.
    tampered = json.loads(out.read_text(encoding="utf-8"))
    cell_of(tampered, "Linux", "x64")["status"] = "PASS"
    out.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n")
    capsys.readouterr()
    assert osm.main(["--verify", str(out)]) == 1
    assert "matrix document SHA-256 mismatch" in capsys.readouterr().out

    # Restored matrix, then a changed gate verdict file.
    restored, out = run_runner(tmp_path, [gate], out=out)
    verdict_path = Path(restored["gates"][0]["verdict_path"])
    verdict_path.write_text(verdict_path.read_text() + " ")
    capsys.readouterr()
    assert osm.main(["--verify", str(out)]) == 1
    assert "verdict SHA-256 mismatch" in capsys.readouterr().out

    # Restored again, then a byte flipped in the recorded evidence file.
    restored, out = run_runner(tmp_path, [gate], out=out)
    evidence_path = Path(cell_of(restored, "Windows", "x86")["evidence"][0]["path"])
    evidence_path.write_bytes(evidence_path.read_bytes() + b"tampered")
    capsys.readouterr()
    assert osm.main(["--verify", str(out)]) == 1
    assert "evidence changed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Determinism: gate order and cell order are canonical
# ---------------------------------------------------------------------------

def test_output_and_summary_are_order_independent(tmp_path, capsys):
    ev = evidence(tmp_path, "frame.txt")
    gates = [
        spec("zulu", write_gate(tmp_path, "zulu", {
            "cells": [good_cell("Linux", "x86", ev)]}), ["Linux|x86"]),
        spec("alpha", write_gate(tmp_path, "alpha", {
            "cells": [good_cell("Windows", "x86", ev)]}), ["Windows|x86"]),
        spec("mike", write_gate(tmp_path, "mike", {
            "cells": [good_cell("macOS", "x64", ev)]}), ["macOS|x64"]),
    ]
    out = tmp_path / "matrix.json"
    first, path_a = run_runner(tmp_path, gates, out=out)
    printed_a = capsys.readouterr().out
    second, path_b = run_runner(tmp_path, list(reversed(gates)), out=out)
    printed_b = capsys.readouterr().out

    assert path_a.read_bytes() == path_b.read_bytes()
    assert printed_a == printed_b
    assert [g["name"] for g in first["gates"]] == ["alpha", "mike", "zulu"]
    assert [(c["os"], c["arch"]) for c in first["cells"]] == [
        (o, a) for o in osm.OSES for a in osm.ARCHES]
    summary_line = next(line for line in printed_a.splitlines()
                        if line.startswith("summary: "))
    assert [part.split(":")[0] for part in summary_line[9:].split("; ")] == list(
        osm.STATUSES)


def test_markdown_is_rendered_and_deterministic(tmp_path):
    gate = write_gate(tmp_path, "alpha", {"cells": []})
    _, out = run_runner(tmp_path, [gate], extra=["--markdown", str(tmp_path / "m.md")])
    md = (tmp_path / "m.md").read_text(encoding="utf-8")
    assert md.count("NOT TESTED") >= 12
    assert "alpha" in md
    assert out.is_file()


# ---------------------------------------------------------------------------
# CLI parsing and policy meta-tests
# ---------------------------------------------------------------------------

def test_gate_specs_normalize_status_and_coverage():
    used: set[str] = set()
    plain = osm.parse_gate("tools.fake_gate", used)
    assert (plain["name"], plain["kind"], plain["target"]) == (
        "fake_gate", "module", "tools.fake_gate")
    assert len(plain["coverage"]) == 12

    aliased = osm.parse_gate(json.dumps({
        "name": "chip", "module": "tools.chip_gate",
        "coverage": ["Linux", "x86"], "args": ["--json"],
    }), used)
    assert aliased["kind"] == "module"
    assert aliased["args"] == ["--json"]
    assert len(aliased["coverage"]) == 6  # 4 Linux cells + 3 other x86 cells
    assert osm.parse_gate("dup=tools.fake_gate", used)["name"] == "dup"
    assert osm.parse_gate("tools.fake_gate", used)["name"] == "fake_gate_2"
    assert osm.coverage_of(["*"]) == osm.ALL_CELLS
    assert osm.coverage_of(["not-an-os"]) == osm.ALL_CELLS
    assert osm.norm_status("SCENARIO-PASS") == "PASS"
    assert osm.norm_status("build-only evidence") == "BUILD ONLY"
    assert osm.norm_status("not tested per target") == "NOT TESTED"
    assert osm.norm_status("nonsense") is None


def test_runner_contains_no_hard_coded_gate_or_host_paths():
    source = (Path(osm.__file__).resolve()).read_text(encoding="utf-8")
    for forbidden in ("/Users/", "/Downloads/", "evidence/corpus/", "tools/windows_oracle"):
        assert forbidden not in source
    assert "No hard-coded" in source or "Nothing about a gate" in source
