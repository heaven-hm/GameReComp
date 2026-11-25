"""Synthetic-manifest tests for the fail-closed game asset gate (#15-#19).

Every asset, manifest and evidence file is created under ``tmp_path``. No host
game install, user path or network is involved. The tests prove that PASS is
only reachable with real declared bytes on disk plus a scenario evidence file,
and that missing, mismatched and extra files can never manufacture a PASS.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.game_asset_gate import (
    BLOCKED, NOT_TESTED, PASS, TARGET_ISSUES, evaluate, main, norm_sha,
)

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "game_asset_gate.py"
GAMES = {
    15: "Project I.G.I.",
    16: "Hitman: Codename 47",
    17: "Max Payne",
    18: "Max Payne 2",
    19: "Hitman 2: Silent Assassin",
}
REQUIRED_NAMES = ("DATA/LEVEL1.WAD", "DATA/LEVEL2.WAD")
EXE_NAME = "IGI.EXE"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path, doc: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def _bundle(root: Path, *, issue: int = 15, exe: bool = True,
            omit_required: tuple[str, ...] = (), tamper: tuple[str, ...] = (),
            omit_hash: tuple[str, ...] = (), bad_hash: tuple[str, ...] = (),
            evidence: bool = True, manifest_evidence: bool = True,
            evidence_digest: str = "valid", extra: tuple[str, ...] = (),
            platform: str = "windows") -> dict:
    """Create one complete (or deliberately broken) synthetic asset bundle."""
    base = root  # manifest lives here, so relative paths need no assets_root
    base.mkdir(parents=True, exist_ok=True)
    exe_path = base / EXE_NAME
    exe_bytes = b"MZ synthetic original executable"
    if exe:
        exe_path.write_bytes(exe_bytes)

    declared: dict = {"executable": {
        "path": EXE_NAME,
        "sha256": _sha(exe_bytes) if exe else _sha(b"never written"),
    }}
    for label in omit_hash:
        if label == "executable":
            declared["executable"].pop("sha256", None)

    rows = []
    for name in REQUIRED_NAMES:
        content = f"synthetic original payload for {name}".encode()
        rows.append({"path": name, "sha256": _sha(content)})
        if name not in omit_required:
            target = base / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"TAMPERED payload" if name in tamper else content)
    for label in omit_hash:
        for row in rows:
            if row["path"] == label:
                row.pop("sha256", None)
    for label in bad_hash:
        if label == "executable":
            declared["executable"]["sha256"] = "not-a-sha256"
        for row in rows:
            if row["path"] == label:
                row["sha256"] = "0" * 64
    declared["required_files"] = rows

    evidence_path = root / "scenario-evidence.json"
    if evidence:
        _json(evidence_path, {"scenario": "mission-1", "issue": issue,
                              "note": "synthetic evidence record"})
    if manifest_evidence:
        declared["scenario_evidence"] = str(evidence_path)
    if evidence_digest == "valid":
        declared["scenario_evidence_sha256"] = _sha(evidence_path.read_bytes()) \
            if evidence else "f" * 64
    elif evidence_digest == "bad":
        declared["scenario_evidence_sha256"] = "a" * 64
    elif evidence_digest == "malformed":
        declared["scenario_evidence_sha256"] = "short"

    declared.update({"schema": 1, "issue": issue, "game": GAMES[issue],
                     "platform": platform})
    manifest = _json(root / "manifest.json", declared)
    for name in extra:
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"undeclared extra file")
    return {"manifest": manifest, "base": base, "evidence": evidence_path,
            "exe": exe_path, "doc": declared}


def _reason_codes(report: dict) -> set[str]:
    return {r["code"] for r in report["reasons"]}


# --- positive path -----------------------------------------------------------

@pytest.mark.parametrize("issue", TARGET_ISSUES)
def test_complete_bundle_passes_for_every_in_scope_issue(tmp_path, issue):
    bundle = _bundle(tmp_path, issue=issue)
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == PASS
    assert report["exit_code"] == 0
    assert report["issues"] == [issue]
    assert report["missing"] == []
    assert report["mismatched"] == []
    assert report["scenario_evidence"]["present"] is True
    assert report["assets"]["executable"]["computed_sha256"] == _sha(
        bundle["exe"].read_bytes())
    assert norm_sha(report["assets"]["required"][0]["computed_sha256"])
    assert report["issue_closed"] is False


def test_relative_paths_resolve_against_assets_root(tmp_path):
    bundle = _bundle(tmp_path)
    # Move the manifest away from the assets; only --assets-root keeps it valid.
    manifest = bundle["manifest"]
    relocated = tmp_path / "elsewhere" / "manifest.json"
    _json(relocated, bundle["doc"])
    assert evaluate(relocated)["verdict"] == BLOCKED
    passed = evaluate(relocated, assets_root=bundle["base"],
                      scenario_evidence=bundle["evidence"])
    assert passed["verdict"] == PASS


# --- missing files and declarations -> BLOCKED -------------------------------

def test_missing_executable_is_blocked_with_exact_reason(tmp_path):
    bundle = _bundle(tmp_path, exe=False)
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == BLOCKED
    assert report["exit_code"] == 2
    assert "ASSET_FILE_MISSING" in _reason_codes(report)
    assert report["missing"] == [{
        "kind": "executable", "label": "executable",
        "path": str(bundle["exe"]),
        "reason": f"missing executable: {bundle['exe']}",
    }]
    assert report["mismatched"] == []


def test_missing_required_file_is_blocked_with_exact_reason(tmp_path):
    missing = REQUIRED_NAMES[0]
    bundle = _bundle(tmp_path, omit_required=(missing,))
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == BLOCKED
    entry = next(m for m in report["missing"] if m["kind"] == "required")
    assert entry["path"] == str(bundle["base"] / missing)
    assert entry["reason"] == f"missing required_files[0]: {bundle['base'] / missing}"


def test_no_required_files_declaration_is_blocked(tmp_path):
    bundle = _bundle(tmp_path)
    doc = dict(bundle["doc"], required_files=[])
    report = evaluate(_json(tmp_path / "empty.json", doc))
    assert report["verdict"] == BLOCKED
    assert "REQUIRED_FILES_MISSING" in _reason_codes(report)


@pytest.mark.parametrize("label", ["executable", REQUIRED_NAMES[1]])
def test_missing_hash_declaration_is_blocked(tmp_path, label):
    bundle = _bundle(tmp_path, omit_hash=(label,))
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == BLOCKED
    assert "SHA256_MISSING" in _reason_codes(report)
    assert norm_sha(report["assets"]["executable"]["computed_sha256"])


@pytest.mark.parametrize("label", ["executable", REQUIRED_NAMES[1]])
def test_mismatched_hash_is_not_tested(tmp_path, label):
    bundle = _bundle(tmp_path, tamper=(label,) if label != "executable" else (),
                     bad_hash=(label,))
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == NOT_TESTED
    assert report["exit_code"] == 1
    assert "SHA256_MISMATCH" in _reason_codes(report) or \
        "SHA256_MALFORMED" in _reason_codes(report)
    assert report["missing"] == []
    assert report["issue_closed"] is False


def test_tampered_file_with_stale_declared_hash_is_not_tested(tmp_path):
    bundle = _bundle(tmp_path, tamper=(REQUIRED_NAMES[0],))
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == NOT_TESTED
    bad = report["mismatched"][0]
    assert bad["path"] == str(bundle["base"] / REQUIRED_NAMES[0])
    assert bad["declared_sha256"] != bad["computed_sha256"]


# --- evidence handling -------------------------------------------------------

def test_no_scenario_evidence_supplied_is_blocked(tmp_path):
    bundle = _bundle(tmp_path, manifest_evidence=False)
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == BLOCKED
    assert "SCENARIO_EVIDENCE_MISSING" in _reason_codes(report)
    assert any("no evidence file was supplied" in r["message"]
               for r in report["reasons"])


def test_missing_evidence_file_is_blocked(tmp_path):
    bundle = _bundle(tmp_path, evidence=False)
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == BLOCKED
    assert "SCENARIO_EVIDENCE_FILE_MISSING" in _reason_codes(report)
    assert report["scenario_evidence"]["present"] is False


def test_empty_evidence_file_is_blocked(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["evidence"].write_bytes(b"")
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == BLOCKED
    assert "SCENARIO_EVIDENCE_EMPTY" in _reason_codes(report)


def test_cli_evidence_override_supplies_evidence(tmp_path):
    bundle = _bundle(tmp_path, manifest_evidence=False, evidence_digest="absent")
    report = evaluate(bundle["manifest"], scenario_evidence=bundle["evidence"])
    assert report["verdict"] == PASS
    assert report["scenario_evidence"]["source"] == "cli"


def test_wrong_declared_evidence_digest_is_not_tested(tmp_path):
    bundle = _bundle(tmp_path, evidence_digest="bad")
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == NOT_TESTED
    assert "EVIDENCE_SHA256_MISMATCH" in _reason_codes(report)


# --- extra files -------------------------------------------------------------

def test_extra_file_never_substitutes_for_a_missing_required_file(tmp_path):
    # An undeclared file with the exact expected bytes sits beside the bundle.
    bundle = _bundle(tmp_path, omit_required=(REQUIRED_NAMES[0],),
                     extra=(REQUIRED_NAMES[0] + ".spare",))
    spare = bundle["base"] / (REQUIRED_NAMES[0] + ".spare")
    expected_bytes = f"synthetic original payload for {REQUIRED_NAMES[0]}".encode()
    spare.write_bytes(expected_bytes)
    assert _sha(spare.read_bytes()) == _sha(expected_bytes)  # hash, not path
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == BLOCKED
    assert "ASSET_FILE_MISSING" in _reason_codes(report)
    assert REQUIRED_NAMES[0] + ".spare" in report["extra_files"]["paths"]


def test_extra_files_are_informational_and_do_not_change_pass(tmp_path):
    bundle = _bundle(tmp_path, extra=("README.txt", "BONUS.BIN"))
    report = evaluate(bundle["manifest"])
    assert report["verdict"] == PASS
    assert report["extra_files"]["count"] == 2
    assert sorted(report["extra_files"]["paths"]) == ["BONUS.BIN", "README.txt"]


# --- fail-closed parsing and scope -------------------------------------------

def test_unreadable_manifest_is_blocked(tmp_path):
    report = evaluate(tmp_path / "does-not-exist.json")
    assert report["verdict"] == BLOCKED
    assert "MANIFEST_UNREADABLE" in _reason_codes(report)
    assert report["issue_closed"] is False


def test_malformed_manifest_json_is_blocked(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    report = evaluate(bad)
    assert report["verdict"] == BLOCKED
    assert "MANIFEST_UNREADABLE" in _reason_codes(report)


@pytest.mark.parametrize("issue", [14, 20, 999, "abc", None])
def test_out_of_scope_issue_is_blocked(tmp_path, issue):
    bundle = _bundle(tmp_path)
    doc = dict(bundle["doc"])
    if issue is None:
        doc.pop("issue")
    else:
        doc["issue"] = issue
    report = evaluate(_json(tmp_path / "scope.json", doc))
    assert report["verdict"] == BLOCKED
    assert "ISSUE_UNDECLARED" in _reason_codes(report)


def test_missing_platform_is_blocked(tmp_path):
    bundle = _bundle(tmp_path)
    doc = dict(bundle["doc"], platform="")
    report = evaluate(_json(tmp_path / "no-platform.json", doc))
    assert report["verdict"] == BLOCKED
    assert "PLATFORM_MISSING" in _reason_codes(report)


def test_duplicate_declared_path_is_not_tested(tmp_path):
    bundle = _bundle(tmp_path)
    doc = dict(bundle["doc"], required_files=[
        dict(bundle["doc"]["required_files"][0]),
        dict(bundle["doc"]["required_files"][0]),
    ])
    report = evaluate(_json(tmp_path / "dup.json", doc))
    assert report["verdict"] == NOT_TESTED
    assert "DUPLICATE_ASSET_DECLARATION" in _reason_codes(report)


# --- no fabricated PASS, no closure keywords ---------------------------------

FORBIDDEN_CLAIMS = ("issue closed", "issues closed", "resolved", "fixed",
                    "100%", "fully verified", "complete coverage")


def _all_text(report: dict) -> str:
    return json.dumps(report, sort_keys=True).lower()


@pytest.mark.parametrize("kwargs", [
    {"exe": False},
    {"tamper": (REQUIRED_NAMES[0],)},
    {"omit_required": (REQUIRED_NAMES[1],)},
    {"evidence": False},
    {"manifest_evidence": False},
    {"bad_hash": ("executable",)},
])
def test_no_fake_pass_for_broken_bundles(tmp_path, kwargs):
    report = evaluate(_bundle(tmp_path, **kwargs)["manifest"])
    assert report["verdict"] in (BLOCKED, NOT_TESTED)
    assert report["verdict"] != PASS
    assert report["issue_closed"] is False
    assert not any(token in _all_text(report) for token in FORBIDDEN_CLAIMS)


def test_report_never_emits_closure_keywords(tmp_path):
    passing = evaluate(_bundle(tmp_path)["manifest"])
    assert passing["issue_closed"] is False
    for report in (passing, evaluate(tmp_path / "missing.json")):
        assert report["verdict"] in (PASS, BLOCKED, NOT_TESTED)
        text = _all_text(report)
        assert not any(token in text for token in FORBIDDEN_CLAIMS)
        assert "closed" not in text.replace("issue_closed", "").replace(
            "false", "")


def test_source_has_no_hard_coded_user_paths():
    text = TOOL.read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert "C:\\games" not in text
    assert "--manifest" in text and "--assets-root" in text
    assert "--scenario-evidence" in text


# --- CLI ---------------------------------------------------------------------

def test_main_writes_report_and_returns_exit_codes(tmp_path):
    bundle = _bundle(tmp_path)
    out = tmp_path / "verdict.json"
    assert main(["--manifest", str(bundle["manifest"]), "--out", str(out)]) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["verdict"] == PASS
    assert saved["issue_closed"] is False

    broken = _bundle(tmp_path / "broken", exe=False, evidence=False)
    out2 = tmp_path / "broken.json"
    assert main(["--manifest", str(broken["manifest"]), "--out", str(out2)]) == 2
    assert json.loads(out2.read_text(encoding="utf-8"))["verdict"] == BLOCKED


def test_cli_exit_code_is_observable_from_subprocess(tmp_path):
    bundle = _bundle(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--manifest", str(bundle["manifest"])],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout.startswith("PASS:")
    assert proc.stderr == ""


def test_report_is_deterministic(tmp_path):
    bundle = _bundle(tmp_path)
    first = evaluate(bundle["manifest"])
    second = evaluate(bundle["manifest"])
    assert first == second
