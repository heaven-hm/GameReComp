"""Closure-gate tests: the documented doc gate and the definitive CLI gate.

Part 1 enforces the project rule over the readouts in docs/ISSUE_WORK_LEDGER.md
and docs/ISSUE_STATUS.md: an issue is never marked complete without evidence.

Part 2 exercises tools.issue_closure_gate with synthetic CLI-supplied issue
JSON, evidence manifests and a policy file. It proves that the CLI gate cannot
be passed with flags or documentation alone, that tampering with hashes or
flags is detected, and that output is deterministic and free of GitHub
auto-closing keywords. No test touches the network or a real GitHub issue.

Run with: python3 -m pytest tests/test_issue_closure_gate.py -q
"""

import hashlib
import json
import re
import socket
import subprocess
from pathlib import Path

from tools import issue_closure_gate as icg

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "ISSUE_WORK_LEDGER.md"
STATUS = ROOT / "docs" / "ISSUE_STATUS.md"

BEGIN = "<!-- BEGIN CLOSURE GATE -->"
END = "<!-- END CLOSURE GATE -->"
EXPECTED_NUMBERS = set(range(1, 29))


def _ledger_text():
    assert LEDGER.is_file(), f"missing issue work ledger: {LEDGER}"
    return LEDGER.read_text(encoding="utf-8")


def _gate_block(text):
    assert BEGIN in text and END in text, "closure-gate markers missing from ledger"
    block = text.split(BEGIN, 1)[1].split(END, 1)[0]
    m = re.search(r"```json\s*(.*?)\s*```", block, re.DOTALL)
    assert m, "closure gate is not a ```json fenced block"
    return json.loads(m.group(1))


def _all_json_blocks(text):
    return [json.loads(m.group(1)) for m in
            re.finditer(r"```json\s*(.*?)\s*```", text, re.DOTALL)]


def _readout_block(text):
    blocks = [b for b in _all_json_blocks(text)
              if isinstance(b, dict) and b.get("readout_only") is True]
    assert len(blocks) == 1, "expected exactly one readout ledger JSON block"
    return blocks[0]


def _gate():
    return _gate_block(_ledger_text())


def _exists(rel):
    return isinstance(rel, str) and bool(rel.strip()) and (ROOT / rel).is_file()


def test_gate_block_is_valid_and_complete():
    gate = _gate()
    assert gate["schema_version"] == 1
    assert gate["document"] == "docs/ISSUE_WORK_LEDGER.md"
    assert gate["closure_gate"] is True
    assert "rule" in gate and gate["rule"].strip()
    assert gate["evidence_file_pattern"] == "evidence/issues/issue-NN.json"
    assert "summary" in gate and isinstance(gate["summary"], dict)


def test_all_28_issues_are_present_with_unique_evidence_files():
    gate = _gate()
    issues = gate["issues"]
    assert len(issues) == 28, f"expected 28 gate rows, found {len(issues)}"
    numbers = [i["number"] for i in issues]
    assert numbers == sorted(EXPECTED_NUMBERS), f"gate numbers are wrong: {numbers}"
    assert len(set(numbers)) == 28, "duplicate issue numbers in gate"

    paths = [i.get("evidence_file") for i in issues]
    assert len(set(paths)) == 28, "evidence_file paths must be unique per issue"
    for i in issues:
        n = i["number"]
        assert re.fullmatch(r"evidence/issues/issue-\d{2}\.json",
                            i["evidence_file"]), (
            f"#{n} evidence_file does not follow the documented pattern"
        )
        assert i["evidence_file"].endswith(f"{n:02d}.json"), (
            f"#{n} evidence_file does not match its issue number"
        )


def test_every_issue_has_criteria_with_explicit_met_flags():
    for issue in _gate()["issues"]:
        n = issue["number"]
        assert issue["github_state"] in ("OPEN", "CLOSED"), f"#{n} bad github_state"
        assert isinstance(issue["complete"], bool), f"#{n} complete is not boolean"
        criteria = issue.get("acceptance_criteria")
        assert isinstance(criteria, list) and criteria, f"#{n} has no criteria"
        ids = [c.get("id") for c in criteria]
        assert len(ids) == len(set(ids)), f"#{n} has duplicate criterion ids"
        for c in criteria:
            assert isinstance(c, dict), f"#{n} has a malformed criterion"
            for field in ("id", "description", "met", "evidence"):
                assert field in c, f"#{n} criterion missing {field}"
            assert isinstance(c["met"], bool), f"#{n}/{c['id']} met is not boolean"
            assert isinstance(c["description"], str) and c["description"].strip(), (
                f"#{n}/{c['id']} has no description"
            )


def test_no_issue_marked_complete_without_evidence_and_all_criteria_met():
    """Core closure gate: complete => evidence file exists AND all criteria met."""
    for issue in _gate()["issues"]:
        n = issue["number"]
        if not issue["complete"]:
            continue  # an OPEN issue may have arbitrary unmet criteria
        assert _exists(issue["evidence_file"]), (
            f"#{n} is marked complete but its evidence file "
            f"{issue['evidence_file']} does not exist"
        )
        unmet = [c["id"] for c in issue["acceptance_criteria"] if not c["met"]]
        assert not unmet, f"#{n} is marked complete with unmet criteria: {unmet}"
        for c in issue["acceptance_criteria"]:
            assert _exists(c["evidence"]), (
                f"#{n}/{c['id']} is met but cites missing evidence {c['evidence']!r}"
            )


def test_met_criterion_always_cites_existing_evidence():
    """A criterion cannot be flipped to met without an artifact to back it."""
    for issue in _gate()["issues"]:
        n = issue["number"]
        for c in issue["acceptance_criteria"]:
            if c["met"]:
                assert _exists(c["evidence"]), (
                    f"#{n}/{c['id']} is met without an existing evidence artifact"
                )


def test_gate_agrees_with_readout_ledger_and_summary():
    gate = _gate()
    readout = _readout_block(_ledger_text())
    readouts = {i["number"]: i for i in readout["issues"]}
    assert set(readouts) == EXPECTED_NUMBERS, "readout ledger is incomplete"

    complete_count = 0
    all_open = True
    for issue in gate["issues"]:
        n = issue["number"]
        assert issue["github_state"] == readouts[n]["github_state"], (
            f"#{n} github_state drift between gate and readout ledger"
        )
        assert issue["complete"] == readouts[n]["complete"], (
            f"#{n} complete flag drift between gate and readout ledger"
        )
        if issue["complete"]:
            complete_count += 1
        if issue["github_state"] != "OPEN":
            all_open = False

    summary = gate["summary"]
    assert summary["issues"] == 28
    assert summary["complete_count"] == complete_count, "complete_count drift"
    assert summary["all_open"] is all_open, "all_open drift"


def test_status_doc_points_at_the_gate():
    text = STATUS.read_text(encoding="utf-8")
    assert "docs/ISSUE_WORK_LEDGER.md" in text
    assert "tests/test_issue_closure_gate.py" in text
    assert "BEGIN CLOSURE GATE" in text
    for token in ("gameplay", "Linux runner", "11 CLOSED", "0e0e5de"):
        assert token in text, f"status doc is missing the gate caveat token: {token}"


# ---------------------------------------------------------------------------
# Part 2: the definitive CLI closure gate (synthetic inputs only)
# ---------------------------------------------------------------------------
CLOSING_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:#\d+|gh-\d+)"
)


def dg(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wjson(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def make_issue_row(number: int, **extra) -> dict:
    row = {"number": number, "title": f"Issue {number}", "state": "OPEN"}
    row.update(extra)
    return row


def make_asset(tmp_path: Path, rel: str, data: bytes = b"asset bytes\n") -> str:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return dg(data)


def make_policy(tmp_path: Path, *, expected: list[int],
                verifications: tuple[str, ...] = ("acceptance_100",),
                assets: dict[int, list[dict]] | None = None,
                gaps: dict[int, list[dict]] | None = None,
                global_gaps: list[dict] | None = None,
                schema_version: int = 1) -> Path:
    issues = {}
    for number in set(expected) | set(assets or {}) | set(gaps or {}):
        issues[str(number)] = {
            "required_assets": (assets or {}).get(number, []),
            "required_verifications": list(verifications),
            "critical_api_gaps": (gaps or {}).get(number, []),
        }
    policy = {
        "schema_version": schema_version,
        "assets_root": ".",
        "expected_issues": expected,
        "required_verifications": list(verifications),
        "issues": issues,
        "critical_api_gaps": global_gaps or [],
    }
    return wjson(tmp_path / "policy.json", policy)


def seal_manifest(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["sha256"] = icg.manifest_digest(data)
    wjson(path, data)


def make_manifest(tmp_path: Path, number: int, *, oracle: bool = True,
                  executed: bool = True, command: str = "wine app.exe --selftest",
                  artifacts: list[tuple[str, bytes]] | None = None,
                  flags: dict | None = None) -> Path:
    mdir = tmp_path / "manifests"
    mdir.mkdir(parents=True, exist_ok=True)
    if artifacts is None:
        artifacts = [(f"oracle-{number}.txt", f"oracle output {number}\n".encode())]
    art_records = []
    for rel, data in artifacts:
        target = mdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        art_records.append({"path": rel, "sha256": dg(data)})
    record = {"issue": number, "sha256": "pending"}
    if oracle:
        record["executed_oracle"] = {
            "executed": executed,
            "command": command,
            "artifacts": art_records,
        }
    record.update(flags or {})
    path = wjson(mdir / f"issue-{number}.json", record)
    seal_manifest(path)
    return path


def run_gate(tmp_path: Path, issues_path: Path, policy_path: Path,
             manifests: list[Path], *, out: str = "gate.md",
             jout: str = "gate.json", extra: list[str] | None = None):
    out_path = tmp_path / out
    json_path = tmp_path / jout
    argv = ["--issues", str(issues_path), "--policy", str(policy_path)]
    for manifest in manifests:
        argv += ["--manifest", str(manifest)]
    argv += ["--out", str(out_path), "--json", str(json_path)]
    argv += extra or []
    rc = icg.main(argv)
    markdown = out_path.read_text(encoding="utf-8") if out_path.is_file() else ""
    report = (json.loads(json_path.read_text(encoding="utf-8"))
              if json_path.is_file() else {})
    return rc, markdown, report, (out_path, json_path)


def only_row(report: dict) -> dict:
    assert len(report["rows"]) == 1, report["rows"]
    return report["rows"][0]


def codes(row: dict) -> set[str]:
    return set(row["reason_codes"])


def happy_case(tmp_path: Path):
    """Build a fully evidenced single-issue case and return its inputs."""
    asset_hash = make_asset(tmp_path, "assets/issue-1.bin")
    policy = make_policy(
        tmp_path, expected=[1],
        verifications=("acceptance_100", "linux_runner_verified"),
        assets={1: [{"path": "assets/issue-1.bin", "sha256": asset_hash}]},
        gaps={1: [{"id": "GAP-A", "resolved": True}]},
    )
    manifest = make_manifest(
        tmp_path, 1,
        flags={"acceptance_100": True, "linux_runner_verified": True})
    issues = wjson(tmp_path / "issues.json", [make_issue_row(1)])
    return policy, manifest, issues


def test_cli_happy_path_is_closable(tmp_path):
    policy, manifest, issues = happy_case(tmp_path)
    rc, markdown, report, _ = run_gate(tmp_path, issues, policy, [manifest])
    assert rc == 0
    assert report["overall"] == "CLOSABLE"
    assert report["counts"] == {"CLOSABLE": 1, "OPEN": 0}
    row = only_row(report)
    assert row["verdict"] == "CLOSABLE"
    assert row["reasons"] == []
    assert row["manifest"].endswith("issue-1.json")
    assert row["oracle_artifacts_checked"] == 1
    assert row["assets_checked"] == 1
    assert row["verifications_checked"] == 2
    assert row["gaps_checked"] == 1
    assert "**Overall: CLOSABLE**" in markdown


def test_flags_and_docs_alone_never_pass(tmp_path):
    asset_hash = make_asset(tmp_path, "assets/issue-1.bin")
    policy = make_policy(
        tmp_path, expected=[1], verifications=("acceptance_100",),
        assets={1: [{"path": "assets/issue-1.bin", "sha256": asset_hash}]},
    )
    (tmp_path / "docs-proof.md").write_text("# all criteria met\n",
                                            encoding="utf-8")
    manifest = make_manifest(tmp_path, 1, oracle=False,
                             flags={"acceptance_100": True})
    issues = wjson(tmp_path / "issues.json", [make_issue_row(1)])
    rc, markdown, report, _ = run_gate(tmp_path, issues, policy, [manifest])
    assert rc == 1
    assert report["overall"] == "OPEN"
    row = only_row(report)
    assert row["verdict"] == "OPEN"
    assert "NO_EXECUTED_ORACLE" in codes(row)
    issues_table = markdown.split("## Issues", 1)[1].split("## Reproduction", 1)[0]
    assert "| OPEN |" in issues_table


def test_executed_flag_without_artifacts_is_open(tmp_path):
    policy = make_policy(tmp_path, expected=[1])
    manifest = make_manifest(tmp_path, 1, artifacts=[],
                             flags={"acceptance_100": True})
    issues = wjson(tmp_path / "issues.json", [make_issue_row(1)])
    rc, _, report, _ = run_gate(tmp_path, issues, policy, [manifest])
    assert rc == 1
    assert codes(only_row(report)) == {"ORACLE_NO_ARTIFACTS"}


def test_tampered_oracle_artifact_is_detected(tmp_path):
    policy, manifest, issues = happy_case(tmp_path)
    rc, _, before, _ = run_gate(tmp_path, issues, policy, [manifest])
    assert rc == 0
    oracle_file = tmp_path / "manifests" / "oracle-1.txt"
    oracle_file.write_bytes(b"tampered oracle output\n")
    rc, _, after, _ = run_gate(tmp_path, issues, policy, [manifest],
                               out="after.md", jout="after.json")
    assert rc == 1
    row = only_row(after)
    assert "ORACLE_ARTIFACT_SHA256_MISMATCH" in codes(row)
    assert "recomputed" in row["reasons"][0]["detail"]


def test_tampered_manifest_field_is_detected(tmp_path):
    policy, manifest, issues = happy_case(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["acceptance_100"] = False
    wjson(manifest, data)  # edited without resealing the manifest hash
    rc, _, unsealed, _ = run_gate(tmp_path, issues, policy, [manifest],
                                  out="u.md", jout="u.json")
    assert rc == 1
    assert "MANIFEST_SHA256_MISMATCH" in codes(only_row(unsealed))

    seal_manifest(manifest)  # tamper now consistent with the file hash
    rc, _, resealed, _ = run_gate(tmp_path, issues, policy, [manifest],
                                  out="r.md", jout="r.json")
    assert rc == 1
    assert "VERIFICATION_NOT_TRUE" in codes(only_row(resealed))


def test_tampered_policy_asset_is_detected(tmp_path):
    policy, manifest, issues = happy_case(tmp_path)
    (tmp_path / "assets" / "issue-1.bin").write_bytes(b"replaced asset\n")
    rc, _, report, _ = run_gate(tmp_path, issues, policy, [manifest])
    assert rc == 1
    assert "ASSET_SHA256_MISMATCH" in codes(only_row(report))


def test_unresolved_critical_api_gap_blocks(tmp_path):
    asset_hash = make_asset(tmp_path, "assets/issue-1.bin")
    policy = make_policy(
        tmp_path, expected=[1],
        assets={1: [{"path": "assets/issue-1.bin", "sha256": asset_hash}]},
        gaps={1: [{"id": "GAP-A", "resolved": False}]},
    )
    manifest = make_manifest(tmp_path, 1)
    issues = wjson(tmp_path / "issues.json", [make_issue_row(1)])
    rc, _, report, _ = run_gate(tmp_path, issues, policy, [manifest])
    assert rc == 1
    assert "CRITICAL_API_GAP_UNRESOLVED" in codes(only_row(report))

    global_policy = make_policy(
        tmp_path, expected=[1],
        assets={1: [{"path": "assets/issue-1.bin", "sha256": asset_hash}]},
        global_gaps=[{"issue": 1, "id": "GAP-G", "resolved": False}],
    )
    rc, _, global_report, _ = run_gate(tmp_path, issues, global_policy,
                                       [manifest], out="g.md", jout="g.json")
    assert rc == 1
    assert "CRITICAL_API_GAP_UNRESOLVED" in codes(only_row(global_report))


def test_missing_and_unexpected_issues_are_reported(tmp_path):
    policy, manifest, issues = happy_case(tmp_path)
    data = json.loads(policy.read_text(encoding="utf-8"))
    data["expected_issues"] = [1, 2]
    wjson(policy, data)
    rc, _, report, _ = run_gate(tmp_path, issues, policy, [manifest])
    assert rc == 1
    rows = {r["number"]: r for r in report["rows"]}
    assert rows[1]["verdict"] == "CLOSABLE"
    assert rows[2]["verdict"] == "OPEN"
    assert "MISSING_EXPECTED_ISSUE" in codes(rows[2])

    extra_issues = wjson(
        tmp_path / "issues-extra.json",
        [make_issue_row(1), make_issue_row(3)],
    )
    rc, _, extra_report, _ = run_gate(tmp_path, extra_issues, policy, [manifest],
                                      out="e.md", jout="e.json")
    rows = {r["number"]: r for r in extra_report["rows"]}
    assert rc == 1
    assert "UNEXPECTED_ISSUE" in codes(rows[3])
    assert "NO_MANIFEST" in codes(rows[3])


def test_multiple_manifests_are_ambiguous_and_open(tmp_path):
    policy, manifest, issues = happy_case(tmp_path)
    second = tmp_path / "manifests" / "duplicate-1.json"
    second.write_bytes(manifest.read_bytes())
    rc, _, report, _ = run_gate(tmp_path, issues, policy, [manifest, second])
    assert rc == 1
    assert "MULTIPLE_MANIFESTS" in codes(only_row(report))


def test_github_closed_state_alone_does_not_pass(tmp_path):
    policy = make_policy(tmp_path, expected=[1])
    issues = wjson(tmp_path / "issues.json",
                   [make_issue_row(1, state="CLOSED")])
    rc, _, report, _ = run_gate(tmp_path, issues, policy, [])
    assert rc == 2  # no manifest supplied is a usage error
    # Supply an unreadable/invalid manifest path through a list instead.
    bad_list = wjson(tmp_path / "manifests.json", ["missing.json"])
    rc, _, report, _ = run_gate(tmp_path, issues, policy, [],
                                extra=["--manifest-list", str(bad_list)],
                                out="b.md", jout="b.json")
    assert rc == 1
    assert report["manifest_errors"]
    assert report["overall"] == "OPEN"
    row = only_row(report)
    assert {"NO_MANIFEST", "GITHUB_STATE_CLOSED_PREMATURE"} <= codes(row)


def test_no_closing_keywords_and_only_two_verdicts(tmp_path, capsys):
    policy, manifest, issues = happy_case(tmp_path)
    rc, markdown, report, files = run_gate(tmp_path, issues, policy, [manifest])
    captured = capsys.readouterr()
    assert rc == 0
    assert set(report["counts"]) == {"CLOSABLE", "OPEN"}
    assert all(r["verdict"] in ("CLOSABLE", "OPEN") for r in report["rows"])
    for text in (markdown, files[0].read_text(encoding="utf-8"),
                 files[1].read_text(encoding="utf-8"),
                 captured.out, captured.err):
        assert CLOSING_RE.search(text) is None, text


def test_output_is_byte_deterministic(tmp_path):
    policy, manifest, issues = happy_case(tmp_path)
    rc1, md1, _, paths = run_gate(tmp_path, issues, policy, [manifest],
                                  out="same.md", jout="same.json")
    first_md, first_json = paths[0].read_bytes(), paths[1].read_bytes()
    rc2, md2, _, _ = run_gate(tmp_path, issues, policy, [manifest],
                              out="same.md", jout="same.json")
    assert rc1 == rc2 == 0
    assert md1 == md2
    assert first_md == paths[0].read_bytes()
    assert first_json == paths[1].read_bytes()


def test_manifest_list_is_supported(tmp_path):
    policy, manifest, issues = happy_case(tmp_path)
    listing = wjson(tmp_path / "manifest-list.json", [str(manifest)])
    rc, _, report, _ = run_gate(tmp_path, issues, policy, [],
                                extra=["--manifest-list", str(listing)])
    assert rc == 0
    assert report["overall"] == "CLOSABLE"


def test_all_46_issues_scale(tmp_path):
    numbers = list(range(1, 47))
    assets = {}
    for number in numbers:
        assets[number] = [{
            "path": f"assets/issue-{number}.bin",
            "sha256": make_asset(tmp_path, f"assets/issue-{number}.bin",
                                 f"asset {number}\n".encode()),
        }]
    policy = make_policy(tmp_path, expected=numbers, assets=assets)
    issues = wjson(tmp_path / "issues.json",
                   [make_issue_row(n) for n in numbers])
    manifests = [make_manifest(tmp_path, n,
                               flags={"acceptance_100": True}) for n in numbers]
    rc, _, report, _ = run_gate(tmp_path, issues, policy, manifests)
    assert rc == 0
    assert len(report["rows"]) == 46
    assert report["counts"] == {"CLOSABLE": 46, "OPEN": 0}

    (tmp_path / "assets" / "issue-46.bin").write_bytes(b"tampered\n")
    rc, _, tampered, _ = run_gate(tmp_path, issues, policy, manifests,
                                  out="t.md", jout="t.json")
    assert rc == 1
    rows = {r["number"]: r for r in tampered["rows"]}
    assert "ASSET_SHA256_MISMATCH" in codes(rows[46])
    assert rows[1]["verdict"] == "CLOSABLE"


def test_gate_never_calls_gh_or_the_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("closure gate must not run external commands "
                             "or open network sockets")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    policy, manifest, issues = happy_case(tmp_path)
    rc, _, report, _ = run_gate(tmp_path, issues, policy, [manifest])
    assert rc == 0
    assert report["overall"] == "CLOSABLE"


def test_empty_issues_and_bad_policy_are_refused(tmp_path):
    policy, manifest, _ = happy_case(tmp_path)
    empty = wjson(tmp_path / "empty.json", [])
    rc, _, report, files = run_gate(tmp_path, empty, policy, [manifest])
    assert rc == 2
    assert not files[0].is_file() and not files[1].is_file()

    bad = make_policy(tmp_path, expected=[1], schema_version=99)
    issues = wjson(tmp_path / "issues.json", [make_issue_row(1)])
    rc, _, report, files = run_gate(tmp_path, issues, bad, [manifest],
                                    out="bad.md", jout="bad.json")
    assert rc == 2
    assert not files[0].is_file() and not files[1].is_file()
