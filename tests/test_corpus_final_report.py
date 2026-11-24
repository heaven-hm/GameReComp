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


def table_rows() -> list[list[str]]:
    text = REPORT.read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 12:
            continue
        if cells[0] in {"Name", "---"} or set(cells[0]) <= {"-"}:
            continue
        rows.append(cells)
    return rows


def verification() -> dict:
    return json.loads(VERIFICATION.read_text(encoding="utf-8"))


def test_report_exists_and_covers_targets():
    assert REPORT.is_file(), "final corpus report is missing"
    rows = table_rows()
    assert len(rows) >= 19, f"expected at least 19 evidence-backed targets, got {len(rows)}"
    data = verification()
    assert data["count"] == len(rows), "report row count disagrees with verification snapshot"
    assert {t["slug"] for t in data["targets"]}


def test_every_table_row_has_matching_evidence_file():
    rows = table_rows()
    assert rows
    for row in rows:
        paths = PATH_RE.findall(" ".join(row))
        assert paths, f"row for {row[0]!r} has no evidence path"
        for rel in paths:
            p = ROOT / rel
            assert p.is_file(), f"missing evidence file {rel} for row {row[0]!r}"
            assert p.stat().st_size > 0 or p.suffix == ".json", f"empty evidence file {rel}"
            assert ".." not in Path(rel).parts, f"path escapes evidence tree: {rel}"
        # one of the evidence paths must be that row's per-target verification record
        assert any(p.endswith("/verification.json") for p in paths), (
            f"row for {row[0]!r} lacks a per-target verification.json reference"
        )


def test_no_fake_pass_or_binary_vendoring():
    rows = table_rows()
    data = {t["slug"]: t for t in verification()["targets"]}
    for row in rows:
        cls = re.sub(r"\*", "", row[10]).strip()
        assert cls in VALID, f"unexpected classification {cls!r} for {row[0]!r}"
        assert cls != "PASS", "PASS requires an original-Windows oracle; none was captured"
        source_exe = row[1].strip("`")
        slug = [s for s in data if data[s]["source_exe"] == source_exe]
        assert slug, f"no verification record for row {row[0]!r} ({source_exe})"
        v = data[slug[0]]
        assert v["classification"] == cls, f"classification drift for {row[0]!r}"
        if cls == "PARTIAL":
            assert v["nonblank_verified"] > 0, "PARTIAL needs independently proven non-blank frames"
        if cls == "BUILD-ONLY":
            assert v["native"]["valid"], "BUILD-ONLY still requires a real native binary"
    for p in EVIDENCE.rglob("*"):
        if p.is_file():
            assert p.suffix.lower() not in {".exe", ".dll", ".dylib", ".so", ".o", ".a"}, (
                f"binary vendored into evidence: {p}"
            )


def test_evidence_tree_is_small():
    assert EVIDENCE.is_dir()
    total = sum(p.stat().st_size for p in EVIDENCE.rglob("*") if p.is_file())
    assert total < 100 * 1024 * 1024, f"evidence tree too large: {total} bytes"
