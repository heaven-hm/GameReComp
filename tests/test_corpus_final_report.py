"""Tests for docs/GAME_CORPUS_FINAL_REPORT.md and its copied evidence."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "docs" / "GAME_CORPUS_FINAL_REPORT.md"
VERIFICATION = ROOT / "reports" / "corpus_verification.json"
EVIDENCE = ROOT / "evidence" / "corpus"
VALID = {"PASS", "PARTIAL", "BUILD-ONLY", "FAIL", "BLOCKED"}
PATH_RE = re.compile(r"`(evidence/corpus/[^`]+)`")
HEADERS = [
    "Name", "Source exe", "Architecture", "Build status",
    "Native binary SHA-256", "Launch exit", "Frames", "Non-blank",
    "Screenshots (relative)", "Logs (relative)", "Unicorn", "Frida",
    "Native replay", "macOS→Windows", "Linux→Windows", "Windows→Linux",
    "x64→macOS", "Classification", "Rationale",
]


def table_rows() -> list[list[str]]:
    text = REPORT.read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < len(HEADERS):
            continue
        if cells[0] in {"Name", "---"} or set(cells[0]) <= {"-"}:
            continue
        rows.append(cells)
    return rows


def verification() -> dict:
    return json.loads(VERIFICATION.read_text(encoding="utf-8"))


def test_report_exists_and_covers_whole_tree():
    assert REPORT.is_file(), "final corpus report is missing"
    header = next(line for line in REPORT.read_text().splitlines()
                  if line.startswith("| Name"))
    for col in HEADERS:
        assert col in header, f"missing report column {col!r}"
    rows = table_rows()
    assert len(rows) >= 19, f"expected at least 19 evidence-backed targets, got {len(rows)}"
    assert verification()["count"] == len(rows), "row count disagrees with verification snapshot"


def test_every_table_row_has_matching_evidence_file():
    rows = table_rows()
    assert rows
    required = ("verification.json", "engines.json", "macos-to-windows.json",
                "linux-to-windows.json")
    for row in rows:
        paths = PATH_RE.findall(" ".join(row))
        assert paths, f"row for {row[0]!r} has no evidence path"
        for rel in paths:
            p = ROOT / rel
            assert p.is_file(), f"missing evidence file {rel} for row {row[0]!r}"
            assert p.stat().st_size > 0 or p.suffix == ".json", f"empty evidence file {rel}"
            assert ".." not in Path(rel).parts, f"path escapes evidence tree: {rel}"
        present = " ".join(paths)
        for name in required:
            assert name in present, f"row {row[0]!r} lacks {name} evidence"
        assert "PR175_LINUX_RUNNER_EVIDENCE.md" in present
        assert "PR174_X64_CORPUS_EVIDENCE.md" in present


def test_fail_closed_classifications_and_no_fake_pass():
    rows = table_rows()
    data = {}
    for t in verification()["targets"]:
        data[t["source_exe"]] = t
    for row in rows:
        cls = re.sub(r"\*", "", row[17]).strip()
        assert cls in VALID, f"unexpected classification {cls!r} for {row[0]!r}"
        assert cls != "PASS", "PASS requires a Windows oracle and running differential engines"
        source_exe = row[1].strip("`")
        v = data[source_exe]
        assert v["classification"] == cls, f"classification drift for {row[0]!r}"
        if cls == "PARTIAL":
            assert v["nonblank_verified"] > 0, "PARTIAL needs independently proven non-blank frames"
        if cls == "BUILD-ONLY":
            assert v["native"]["valid"], "BUILD-ONLY still requires a real native binary"
        if cls == "PASS":
            assert "BLOCKED" not in row[10] and "BLOCKED" not in row[11], (
                "PASS requires Unicorn and Frida results from engines that actually ran")


def test_no_binary_vendored_and_tree_stays_small():
    assert EVIDENCE.is_dir()
    total = 0
    for p in EVIDENCE.rglob("*"):
        if not p.is_file():
            continue
        total += p.stat().st_size
        assert p.suffix.lower() not in {".exe", ".dll", ".dylib", ".so", ".o", ".a"}, (
            f"binary vendored into evidence: {p}"
        )
    assert total < 100 * 1024 * 1024, f"evidence tree too large: {total} bytes"
