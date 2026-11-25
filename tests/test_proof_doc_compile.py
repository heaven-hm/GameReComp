"""Deterministic tests for tools.proof_doc_compile.

Every input is synthetic and lives under ``tmp_path``: no repository artifact,
no host-specific path and no live build or run is involved. The compiler must
decide only from the CLI-supplied documents and the bytes at the paths those
documents name. In particular a stale digest is TAMPERED, a missing artifact is
NOT TESTED with a reason, and a source-declared PASS or full-coverage string is
never promoted into the index.

Run with: python3 -m pytest tests/test_proof_doc_compile.py -q
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from tools import proof_doc_compile as pdc

ROOT = Path(__file__).resolve().parents[1]
CLOSE_WORDS = re.compile(r"(?i)\b(close|closed|closes|closing|fix|fixed|fixes|"
                         r"resolve|resolved|resolves)\b")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact(root: Path, name: str = "art.bin",
             data: bytes = b"synthetic artifact bytes\n") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def doc_table(path: Path, artifact_path: str, claimed: str,
              *, label: str = "binary") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "| Artifact | Path | SHA-256 |\n"
        "|---|---|---|\n"
        f"| {label} | {artifact_path} | `{claimed}` |\n",
        encoding="utf-8")
    return path


def report(path: Path, payload: dict | list | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    return path


def run(tmp_path: Path, *, docs: list[Path] | None = None,
        reports: list[Path] | None = None, label: str = "unit test",
        strict: bool = False, tag: str = "run") -> tuple[subprocess.CompletedProcess,
                                                        dict | None, str | None]:
    out = tmp_path / f"{tag}.md"
    json_out = tmp_path / f"{tag}.json"
    argv = [sys.executable, "-m", "tools.proof_doc_compile", "--out", str(out),
            "--json", str(json_out), "--label", label]
    for path in docs or []:
        argv += ["--doc", str(path)]
    for path in reports or []:
        argv += ["--report", str(path)]
    if strict:
        argv.append("--strict")
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    index = json.loads(json_out.read_text(encoding="utf-8")) if json_out.is_file() else None
    markdown = out.read_text(encoding="utf-8") if out.is_file() else None
    return proc, index, markdown


def one_claim(index: dict) -> dict:
    assert len(index["claims"]) == 1, index["claims"]
    return index["claims"][0]


# ---------------------------------------------------------------------------
# honest evidence: re-derived, PARTIAL, never a pass
# ---------------------------------------------------------------------------
def test_honest_bundle_is_partial_and_never_pass(tmp_path):
    art = artifact(tmp_path / "ev")
    correct = digest(art.read_bytes())
    d = doc_table(tmp_path / "ev" / "evidence.md", "art.bin", correct)
    r = report(tmp_path / "ev" / "report.json", {
        "verdict": "PASS",
        "coverage": "100% complete",
        "issue_closed": True,
        "artifacts": [{"path": str(art), "sha256": correct}],
    })
    proc, index, markdown = run(tmp_path, docs=[d], reports=[r])

    assert proc.returncode == 0, proc.stderr
    assert index["verdict"]["status"] == pdc.PARTIAL
    assert index["counts"] == {"TAMPERED": 0, "NOT TESTED": 0,
                               "PARTIAL": 2, "PASS": 0}
    assert all(row["verdict"] == pdc.RE_DERIVED for row in index["claims"])
    assert all(row["status"] == "MATCH" for row in index["claims"])
    assert "| PASS | 0 |" in markdown
    assert "100%" not in markdown
    assert "issue_closed" not in markdown
    assert CLOSE_WORDS.search(markdown) is None


def test_no_fake_promotion_even_when_source_claims_pass(tmp_path):
    art = artifact(tmp_path)
    d = doc_table(tmp_path / "claim.md", "art.bin", "0" * 64)
    r = report(tmp_path / "claim.json", {
        "verdict": "PASS", "coverage": "100% verified", "status": "PASS",
        "artifact": {"path": str(art), "sha256": "f" * 64},
    })
    proc, index, markdown = run(tmp_path, docs=[d], reports=[r])

    assert proc.returncode == 0
    assert index["verdict"]["status"] == pdc.TAMPERED
    assert index["counts"]["PASS"] == 0
    assert all(row["verdict"] != "PASS" for row in index["claims"])
    assert "100%" not in markdown
    assert "100% verified" not in markdown
    assert CLOSE_WORDS.search(markdown) is None


# ---------------------------------------------------------------------------
# stale hashes -> TAMPERED with both digests in the reason
# ---------------------------------------------------------------------------
def test_stale_doc_hash_is_tampered_with_both_digests(tmp_path):
    art = artifact(tmp_path / "ev")
    actual = digest(art.read_bytes())
    d = doc_table(tmp_path / "ev" / "claim.md", "art.bin", "0" * 64)
    proc, index, markdown = run(tmp_path, docs=[d])

    assert proc.returncode == 0
    assert index["verdict"]["status"] == pdc.TAMPERED
    row = one_claim(index)
    assert row["verdict"] == pdc.TAMPERED
    assert row["status"] == "MISMATCH"
    assert row["actual_sha256"] == actual
    reason = row["reasons"][0]
    assert "0" * 64 in reason and actual in reason and "art.bin" in reason
    assert pdc.TAMPERED in markdown


def test_stale_report_hash_is_tampered(tmp_path):
    art = artifact(tmp_path)
    r = report(tmp_path / "report.json", {
        "artifacts": [{"path": str(art), "sha256": "1" * 64}],
    })
    proc, index, _ = run(tmp_path, reports=[r])

    assert proc.returncode == 0
    assert index["verdict"]["status"] == pdc.TAMPERED
    row = one_claim(index)
    assert row["verdict"] == pdc.TAMPERED
    assert row["actual_sha256"] == digest(art.read_bytes())
    assert "1" * 64 in row["reasons"][0]


# ---------------------------------------------------------------------------
# missing / unpaired claims -> NOT TESTED with reasons
# ---------------------------------------------------------------------------
def test_missing_doc_artifact_is_not_tested(tmp_path):
    d = doc_table(tmp_path / "claim.md", "absent.bin", "2" * 64)
    proc, index, markdown = run(tmp_path, docs=[d])

    assert proc.returncode == 0
    assert index["verdict"]["status"] == pdc.NOT_TESTED
    row = one_claim(index)
    assert row["verdict"] == pdc.NOT_TESTED
    assert row["status"] == "MISSING"
    assert "absent.bin" in row["reasons"][0]
    assert row["actual_sha256"] is None
    assert pdc.NOT_TESTED in markdown
    assert index["counts"]["PASS"] == 0


def test_missing_report_artifact_is_not_tested(tmp_path):
    r = report(tmp_path / "report.json", {
        "frames": [{"path": "no-such-frame.bmp", "sha256": "3" * 64}],
    })
    proc, index, _ = run(tmp_path, reports=[r])

    assert proc.returncode == 0
    assert index["verdict"]["status"] == pdc.NOT_TESTED
    row = one_claim(index)
    assert row["verdict"] == pdc.NOT_TESTED
    assert "no-such-frame.bmp" in row["reasons"][0]


def test_unpaired_markdown_hash_is_not_tested(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text(f"- Binary SHA-256: `{chr(97) * 64}`\n", encoding="utf-8")
    proc, index, _ = run(tmp_path, docs=[path])

    assert proc.returncode == 0
    row = one_claim(index)
    assert row["verdict"] == pdc.NOT_TESTED
    assert row["artifact_path"] is None
    assert "hash without an artifact path" == row["label"]


def test_invalid_hash_claim_is_not_tested(tmp_path):
    art = artifact(tmp_path)
    r = report(tmp_path / "report.json", {
        "artifact": {"path": str(art), "sha256": "short"},
    })
    proc, index, _ = run(tmp_path, reports=[r])

    assert proc.returncode == 0
    row = one_claim(index)
    assert row["verdict"] == pdc.NOT_TESTED
    assert row["status"] == pdc.INVALID_HASH
    assert "64 hex" in row["reasons"][0]


def test_missing_input_source_is_reported_not_silently_skipped(tmp_path):
    missing = tmp_path / "no-document.md"
    proc, index, markdown = run(tmp_path, docs=[missing])

    assert proc.returncode == 0
    row = one_claim(index)
    assert row["verdict"] == pdc.NOT_TESTED
    assert row["source"] == f"doc:{missing}"
    assert "missing" in row["label"]
    assert index["verdict"]["status"] == pdc.NOT_TESTED
    assert str(missing) in markdown


# ---------------------------------------------------------------------------
# path handling, JSON Lines, determinism, strict mode
# ---------------------------------------------------------------------------
def test_relative_path_uses_supplying_document_directory(tmp_path):
    art = artifact(tmp_path / "ev")
    d = doc_table(tmp_path / "ev" / "nested" / "claim.md", "../art.bin",
                  digest(art.read_bytes()))
    proc, index, _ = run(tmp_path, docs=[d])

    assert proc.returncode == 0
    row = one_claim(index)
    assert row["verdict"] == pdc.RE_DERIVED
    assert row["checked_path"] == (tmp_path / "ev" / "art.bin").as_posix()


def test_json_lines_report_is_supported(tmp_path):
    a = artifact(tmp_path, "a.bin", b"first\n")
    b = artifact(tmp_path, "b.bin", b"second\n")
    r = report(tmp_path / "lines.json", "\n".join([
        json.dumps({"path": str(a), "sha256": digest(a.read_bytes())}),
        json.dumps({"path": str(b), "sha256": digest(b.read_bytes())}),
    ]))
    proc, index, _ = run(tmp_path, reports=[r])

    assert proc.returncode == 0
    assert len(index["claims"]) == 2
    assert all(row["verdict"] == pdc.RE_DERIVED for row in index["claims"])
    assert index["verdict"]["status"] == pdc.PARTIAL


def test_output_is_byte_deterministic_and_order_independent(tmp_path):
    a = artifact(tmp_path, "a.bin", b"first\n")
    b = artifact(tmp_path, "b.bin", b"second\n")
    da = doc_table(tmp_path / "a.md", "a.bin", digest(a.read_bytes()))
    db = doc_table(tmp_path / "b.md", "b.bin", digest(b.read_bytes()))
    r1 = report(tmp_path / "r.json", {
        "path": str(b), "sha256": digest(b.read_bytes())})

    _, index_a, md_a = run(tmp_path, docs=[da, db], reports=[r1], tag="same")
    _, index_b, md_b = run(tmp_path, docs=[db, da], reports=[r1], tag="same")

    assert md_a == md_b
    assert json.dumps(index_a, sort_keys=True) == json.dumps(index_b, sort_keys=True)
    assert "commands" in index_a and "<DOC.md>" in index_a["commands"]["template"]


def test_no_hard_coded_repository_paths_in_output(tmp_path):
    art = artifact(tmp_path)
    d = doc_table(tmp_path / "claim.md", "art.bin", digest(art.read_bytes()))
    _, index, markdown = run(tmp_path, docs=[d])

    assert str(ROOT) not in markdown
    assert str(ROOT) not in json.dumps(index)
    assert all(str(ROOT) not in row["checked_path"] for row in index["claims"])


def test_strict_exits_nonzero_only_for_tampered(tmp_path):
    art = artifact(tmp_path)
    good = doc_table(tmp_path / "good.md", "art.bin", digest(art.read_bytes()))
    stale = doc_table(tmp_path / "stale.md", "art.bin", "4" * 64)
    missing = doc_table(tmp_path / "missing.md", "gone.bin", "5" * 64)

    ok, _, _ = run(tmp_path, docs=[good], strict=True, tag="ok")
    bad, _, _ = run(tmp_path, docs=[stale], strict=True, tag="bad")
    untested, _, _ = run(tmp_path, docs=[missing], strict=True, tag="untested")

    assert ok.returncode == 0
    assert bad.returncode == 1
    assert untested.returncode == 0


def test_no_inputs_is_usage_error(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "tools.proof_doc_compile",
         "--out", str(tmp_path / "index.md")],
        cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 2
    assert not (tmp_path / "index.md").exists()


def test_claim_directive_and_table_are_both_indexed(tmp_path):
    art = artifact(tmp_path)
    correct = digest(art.read_bytes())
    path = tmp_path / "mixed.md"
    path.write_text(
        "| Artifact | Path | SHA-256 |\n|---|---|---|\n"
        f"| one | art.bin | `{correct}` |\n\n"
        f"CLAIM art.bin {correct} second-claim\n",
        encoding="utf-8")
    _, index, _ = run(tmp_path, docs=[path])

    assert len(index["claims"]) == 2
    assert sorted(row["label"] for row in index["claims"]) == [
        "one", "second-claim"]
    assert all(row["verdict"] == pdc.RE_DERIVED for row in index["claims"])
