#!/usr/bin/env python3
"""Definitive, fail-closed CLI closure gate for GitHub issues.

This tool reads three CLI-supplied inputs and nothing else:

* ``--issues``        a JSON array of GitHub issue objects (the output shape of
                      ``gh issue list --json number,title,state``);
* ``--manifest``      one or more per-issue evidence manifest JSON files
                      (repeat the flag; ``--manifest-list`` can supply a JSON
                      array of paths instead);
* ``--policy``        a JSON policy describing the required assets, the
                      verification booleans and the critical API gaps.

It writes a deterministic machine-readable report (``--json``) and a
deterministic Markdown report (``--out``), and prints a one-line summary.
It is strictly read-only: it never calls ``gh``, never opens a network socket,
never edits an issue, and emits no GitHub auto-closing keywords.  Its only
verdicts are CLOSABLE and OPEN; the word CLOSABLE is a permission to close,
not an action, and the tool never performs a closure itself.

An issue is CLOSABLE only when ALL of the following hold:

  (a) the manifest carries a real executed-oracle record (``executed_oracle``
      with ``executed`` true, a command and at least one artifact), and every
      artifact file exists and its SHA-256 matches the recorded claim --
      recomputed from the bytes on disk by this tool;
  (b) every asset required by the policy exists and its SHA-256 matches the
      policy claim -- again recomputed from disk, not taken on trust;
  (c) every verification boolean required by the policy is present and is
      exactly the JSON boolean ``true``;
  (d) the policy records no unresolved critical API gap for the issue.

A manifest-level ``sha256`` claim is required and verified against a canonical
digest of the manifest with that claim field excluded, so editing any other
field without resealing the claim is a detected tamper.  Everything else is
OPEN with explicit reason codes and details.  A flag, a documentation page or
a bare ``acceptance_100=true`` can never produce CLOSABLE on its own.

Policy shape (``schema_version`` 1)::

    {
      "schema_version": 1,
      "assets_root": ".",                 # relative to the policy file's dir
      "expected_issues": [1, 2, 3],       # optional completeness set
      "required_verifications": ["acceptance_100", "linux_runner_verified"],
      "issues": {
        "1": {
          "required_assets": [
            {"path": "assets/issue-01.bin", "sha256": "<64 hex>"}
          ],
          "required_verifications": ["gameplay_equivalent"],
          "critical_api_gaps": [
            {"id": "GAP-A", "resolved": true}
          ]
        }
      },
      "critical_api_gaps": [              # optional global gap list
        {"issue": 2, "id": "GAP-B", "resolved": false}
      ]
    }

Manifest shape (one JSON object, or an array of them, per ``--manifest``)::

    {
      "issue": 1,
      "sha256": "<64 hex of this file's bytes>",
      "executed_oracle": {
        "executed": true,
        "command": "wine game.exe --selftest",
        "artifacts": [{"path": "oracle-out.txt", "sha256": "<64 hex>"}]
      },
      "acceptance_100": true,
      "linux_runner_verified": true,
      "gameplay_equivalent": true
    }

Oracle artifact paths resolve relative to the manifest file's own directory;
policy asset paths resolve relative to ``assets_root``.  Absolute paths are
accepted.  No timestamp, environment value, network result or directory walk
order enters the report: identical argv and identical bytes produce identical
output files.

Usage::

    python3 -m tools.issue_closure_gate \\
        --issues issues.json --policy policy.json \\
        --manifest evidence/issues/issue-01.json \\
        --manifest evidence/issues/issue-02.json \\
        --out gate.md --json gate.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/issue_closure_gate.py"
GATE = "CLOSABLE_ONLY_WITH_EXECUTED_ORACLE_AND_VERIFIED_ASSETS"
VERDICTS = ("CLOSABLE", "OPEN")

# GitHub's auto-closing keywords followed by an issue reference.  Nothing the
# tool writes may contain any of these; see assert_no_closing_keywords().
CLOSING_KEYWORD = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:#\d+|gh-\d+)"
)
_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_MISSING = object()

LIMITS = (
    "The tool only reports CLOSABLE or OPEN. It never closes, reopens, comments "
    "on or edits any issue, calls gh, or uses the network; it reads only the "
    "CLI-supplied files and writes only the two CLI-supplied report paths.",
    "CLOSABLE means the supplied evidence satisfies the gate, not that an issue "
    "was or will be closed by this tool. No GitHub auto-closing keyword is "
    "emitted in any report or on stdout/stderr.",
    "An executed-oracle record is accepted only when 'executed' is true, a "
    "non-empty command is present and at least one artifact exists with a "
    "matching SHA-256 recomputed from its bytes. Flags and documentation are "
    "never treated as an oracle.",
    "Every asset required by the policy must exist with a SHA-256 that matches "
    "the policy claim, recomputed from disk; every required verification must "
    "be present and exactly the JSON boolean true; and no critical API gap for "
    "the issue may be unresolved.",
    "Each manifest must carry a sha256 claim over its canonical JSON with the "
    "claim field excluded. Editing any other manifest field without resealing "
    "that claim is detected as MANIFEST_SHA256_MISMATCH.",
    "When policy.expected_issues is supplied, every expected issue must appear "
    "in --issues or it is reported OPEN with MISSING_EXPECTED_ISSUE; an issue "
    "outside that set is OPEN with UNEXPECTED_ISSUE.",
    "Reports are byte-stable: no clock, environment value or machine-dependent "
    "ordering is used. The same argv and the same input bytes yield the same "
    "two reports.",
)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def sha256_file(path: Path) -> str | None:
    """Exact SHA-256 of a file, or None when it cannot be read (never guessed)."""
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX.match(value))


def manifest_digest(data: dict) -> str:
    """Canonical digest of a manifest record, excluding its own ``sha256``.

    A file cannot contain the hash of its own bytes, so the claim is defined
    over the deterministic JSON form of the record with the claim removed.
    Changing any other field invalidates it until the claim is resealed.
    """
    payload = {k: v for k, v in data.items() if k != "sha256"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def issue_number(value: Any) -> int | None:
    """Coerce a GitHub issue number to a positive int; reject bools and junk."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def load_json(path: Path, what: str) -> Any:
    """Read and parse one UTF-8 JSON file, with a stable error message."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{what} {path}: cannot read JSON: {exc}") from exc


def load_issues(path: Path) -> list[dict]:
    data = load_json(path, "issues file")
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError(f"{path}: expected a JSON array of issue objects")
    return data


def load_policy(path: Path) -> dict:
    """Validate and normalise the policy file into a deterministic structure."""
    raw = load_json(path, "policy file")
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: policy must be a JSON object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}: unsupported schema_version "
                         f"{raw.get('schema_version')!r}; expected {SCHEMA_VERSION}")

    assets_root_text = raw.get("assets_root", ".")
    if not isinstance(assets_root_text, str) or not assets_root_text.strip():
        raise ValueError(f"{path}: assets_root must be a non-empty string")
    assets_root = Path(assets_root_text)
    if not assets_root.is_absolute():
        assets_root = path.parent / assets_root

    expected_raw = raw.get("expected_issues")
    expected: list[int] | None = None
    if expected_raw is not None:
        if not isinstance(expected_raw, list):
            raise ValueError(f"{path}: expected_issues must be an array")
        numbers = [issue_number(n) for n in expected_raw]
        if any(n is None for n in numbers) or len(set(numbers)) != len(numbers):
            raise ValueError(f"{path}: expected_issues must be unique positive ints")
        expected = sorted(numbers)

    def names(value: Any, field: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or not all(
                isinstance(n, str) and n.strip() for n in value):
            raise ValueError(f"{path}: {field} must be an array of non-empty strings")
        return tuple(dict.fromkeys(value))

    default_verifications = names(raw.get("required_verifications"),
                                  "required_verifications")

    issues_raw = raw.get("issues", {})
    if not isinstance(issues_raw, dict):
        raise ValueError(f"{path}: issues must be an object keyed by issue number")
    issues: dict[int, dict] = {}
    for key, value in issues_raw.items():
        number = issue_number(key)
        if number is None or not isinstance(value, dict):
            raise ValueError(f"{path}: issues key {key!r} must map to an object")
        assets = value.get("required_assets", [])
        gaps = value.get("critical_api_gaps", [])
        if not isinstance(assets, list) or not isinstance(gaps, list):
            raise ValueError(f"{path}: issue {number} assets/gaps must be arrays")
        issues[number] = {
            "assets": list(assets),
            "verifications": names(value.get("required_verifications"),
                                   f"issues[{number}].required_verifications"),
            "gaps": list(gaps),
        }

    global_gaps = raw.get("critical_api_gaps", [])
    if not isinstance(global_gaps, list):
        raise ValueError(f"{path}: critical_api_gaps must be an array")

    return {
        "path": str(path),
        "assets_root": assets_root,
        "expected_issues": expected,
        "default_verifications": default_verifications,
        "issues": issues,
        "global_gaps": list(global_gaps),
    }


def load_manifests(paths: list[Path]) -> tuple[dict[int, list[dict]], list[str]]:
    """Map issue number -> manifest records; collect unattributable files."""
    by_number: dict[int, list[dict]] = {}
    errors: list[str] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            errors.append(f"duplicate manifest path {key}")
            continue
        seen.add(key)
        try:
            data = load_json(path, "manifest file")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        records = data if isinstance(data, list) else [data]
        if not records or not all(isinstance(r, dict) for r in records):
            errors.append(f"{path}: manifest must be an object or non-empty array "
                          "of objects")
            continue
        for index, record in enumerate(records):
            number = issue_number(record.get("issue"))
            item = {
                "path": Path(path),
                "rel": key,
                "index": index,
                "data": record,
                "digest": manifest_digest(record),
            }
            if number is None:
                errors.append(f"{path}[{index}]: missing or invalid issue number")
                continue
            by_number.setdefault(number, []).append(item)
    return by_number, errors


def expand_manifest_list(path: Path) -> list[Path]:
    """Read a JSON array of manifest paths, relative to the list file."""
    data = load_json(path, "manifest list")
    if not isinstance(data, list) or not all(isinstance(p, str) and p.strip()
                                             for p in data):
        raise ValueError(f"{path}: expected an array of non-empty path strings")
    paths = [Path(p) for p in data]
    return [p if p.is_absolute() else path.parent / p for p in paths]


def resolve_path(value: str, base: Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else base / candidate


# --------------------------------------------------------------------------
# per-issue gating
# --------------------------------------------------------------------------
def check_issue(number: int | None, row: dict, policy: dict,
                manifests_by_number: dict[int, list[dict]]) -> dict:
    """Assign exactly one fail-closed verdict to one issue row."""
    reasons: list[dict] = []

    def add(code: str, detail: str) -> None:
        reasons.append({"code": code, "detail": detail})

    title = row.get("title") if isinstance(row.get("title"), str) else None
    github_state = (row.get("state").strip().upper()
                    if isinstance(row.get("state"), str) else None)

    result: dict[str, Any] = {
        "number": number,
        "title": title,
        "github_state": github_state,
        "verdict": "OPEN",
        "reasons": reasons,
        "reason_codes": [],
        "manifest": None,
        "manifest_sha256": None,
        "oracle_artifacts_checked": 0,
        "assets_checked": 0,
        "verifications_checked": 0,
        "gaps_checked": 0,
    }

    if number is None:
        add("MALFORMED_ISSUE_ROW", "issue row has a missing or invalid number")
        result["reason_codes"] = [r["code"] for r in reasons]
        return result

    candidates = manifests_by_number.get(number, [])
    if not candidates:
        add("NO_MANIFEST", f"no evidence manifest supplied for issue {number}")
    elif len(candidates) > 1:
        add("MULTIPLE_MANIFESTS",
            f"{len(candidates)} manifests claim issue {number}; expected exactly one")
    else:
        record = candidates[0]
        data = record["data"]
        result["manifest"] = record["rel"]
        result["manifest_sha256"] = record["digest"]

        # Manifest self-integrity: a sealed claim that disagrees with the
        # canonical digest means a field was edited after it was produced.
        actual = record["digest"]
        claim = data.get("sha256")
        if claim is None:
            add("MANIFEST_SHA256_MISSING",
                f"manifest {record['rel']} has no sha256 claim")
        elif not is_sha256(claim):
            add("MANIFEST_SHA256_MALFORMED",
                f"manifest {record['rel']} has a malformed sha256 claim")
        elif claim.lower() != actual:
            add("MANIFEST_SHA256_MISMATCH",
                f"manifest {record['rel']} claimed {claim} but recomputed {actual}")

        # (a) real executed-oracle record with recomputed artifact hashes.
        oracle = data.get("executed_oracle")
        if not isinstance(oracle, dict):
            add("NO_EXECUTED_ORACLE",
                f"issue {number} has no executed_oracle record; flags and docs "
                "are not an oracle")
        else:
            if oracle.get("executed") is not True:
                add("ORACLE_NOT_EXECUTED",
                    "executed_oracle.executed is not the boolean true")
            command = oracle.get("command")
            if not isinstance(command, str) or not command.strip():
                add("ORACLE_NO_COMMAND", "executed_oracle has no non-empty command")
            artifacts = oracle.get("artifacts")
            if not isinstance(artifacts, list) or not artifacts:
                add("ORACLE_NO_ARTIFACTS",
                    "executed_oracle has no non-empty artifact list")
            else:
                for index, artifact in enumerate(artifacts):
                    label = f"executed_oracle.artifacts[{index}]"
                    if not isinstance(artifact, dict) or not isinstance(
                            artifact.get("path"), str) or not artifact["path"].strip():
                        add("ORACLE_ARTIFACT_BAD_SPEC", f"{label} has no path")
                        continue
                    result["oracle_artifacts_checked"] += 1
                    target = resolve_path(artifact["path"], record["path"].parent)
                    if not target.is_file():
                        add("ORACLE_ARTIFACT_MISSING",
                            f"{label} cites missing file {artifact['path']!r}")
                        continue
                    claimed = artifact.get("sha256")
                    recomputed = sha256_file(target)
                    if not is_sha256(claimed):
                        add("ORACLE_ARTIFACT_SHA256_MALFORMED",
                            f"{label} has no valid sha256 claim")
                    elif recomputed is None:
                        add("ORACLE_ARTIFACT_UNREADABLE",
                            f"{label} file cannot be read: {artifact['path']!r}")
                    elif claimed.lower() != recomputed:
                        add("ORACLE_ARTIFACT_SHA256_MISMATCH",
                            f"{label} claimed {claimed} but recomputed {recomputed}")

        # (b) policy-required assets, hashes recomputed from disk.
        spec = policy["issues"].get(number, {})
        for asset in sorted(spec.get("assets", []),
                            key=lambda a: str(a.get("path"))
                            if isinstance(a, dict) else ""):
            if (not isinstance(asset, dict) or not isinstance(asset.get("path"), str)
                    or not asset["path"].strip() or not is_sha256(asset.get("sha256"))):
                add("ASSET_BAD_SPEC", "policy asset entry lacks a path/sha256")
                continue
            result["assets_checked"] += 1
            target = resolve_path(asset["path"], policy["assets_root"])
            if not target.is_file():
                add("ASSET_MISSING",
                    f"required asset {asset['path']!r} does not exist")
                continue
            recomputed = sha256_file(target)
            if recomputed is None:
                add("ASSET_UNREADABLE",
                    f"required asset {asset['path']!r} cannot be read")
            elif asset["sha256"].lower() != recomputed:
                add("ASSET_SHA256_MISMATCH",
                    f"asset {asset['path']!r} claimed {asset['sha256']} but "
                    f"recomputed {recomputed}")

        # (c) required verification booleans must be present and exactly true.
        names = sorted(set(policy["default_verifications"])
                       | set(spec.get("verifications", ())))
        nested = data.get("verifications")
        for name in names:
            if isinstance(nested, dict) and name in nested:
                value = nested[name]
            elif name in data:
                value = data[name]
            else:
                value = _MISSING
            if value is _MISSING:
                add("VERIFICATION_MISSING",
                    f"verification {name!r} is absent from the manifest")
            elif value is not True:
                add("VERIFICATION_NOT_TRUE",
                    f"verification {name!r} is {value!r}, expected true")
        result["verifications_checked"] = len(names)

        # (d) no unresolved critical API gap for this issue.
        all_gaps = [g for g in policy["global_gaps"]
                    if isinstance(g, dict) and issue_number(g.get("issue")) == number]
        all_gaps += spec.get("gaps", [])
        for gap in sorted(all_gaps,
                          key=lambda g: str(g.get("id")) if isinstance(g, dict) else ""):
            if (not isinstance(gap, dict) or not isinstance(gap.get("id"), str)
                    or not gap["id"].strip() or "resolved" not in gap):
                add("CRITICAL_API_GAP_BAD_SPEC", "critical API gap lacks id/resolved")
                continue
            result["gaps_checked"] += 1
            if gap["resolved"] is not True:
                add("CRITICAL_API_GAP_UNRESOLVED",
                    f"critical API gap {gap['id']!r} is unresolved")

    if not reasons:
        result["verdict"] = "CLOSABLE"
    elif github_state == "CLOSED":
        add("GITHUB_STATE_CLOSED_PREMATURE",
            "github_state=CLOSED while the closure gate verdict is OPEN")
    result["reason_codes"] = [r["code"] for r in reasons]
    return result


# --------------------------------------------------------------------------
# report assembly and deterministic rendering
# --------------------------------------------------------------------------
def build_report(issues: list[dict], policy: dict,
                 manifests_by_number: dict[int, list[dict]],
                 manifest_errors: list[str], *, inputs: dict[str, str],
                 command: str) -> dict:
    rows: list[dict] = []
    supplied: dict[int, dict] = {}
    for issue in issues:
        number = issue_number(issue.get("number"))
        if number is not None and number in supplied:
            duplicate = dict(issue)
            row = check_issue(None, duplicate, policy, manifests_by_number)
            row["reasons"].insert(0, {
                "code": "DUPLICATE_ISSUE_ROW",
                "detail": f"issue {number} appears more than once in --issues",
            })
            row["reason_codes"].insert(0, "DUPLICATE_ISSUE_ROW")
            row["verdict"] = "OPEN"
            rows.append(row)
            continue
        if number is not None:
            supplied[number] = issue
        rows.append(check_issue(number, issue, policy, manifests_by_number))

    expected = policy["expected_issues"]
    if expected is not None:
        for number in expected:
            if number not in supplied:
                row = check_issue(number, {"number": number}, policy,
                                  manifests_by_number)
                row["reasons"].insert(0, {
                    "code": "MISSING_EXPECTED_ISSUE",
                    "detail": f"issue {number} is required by policy but absent "
                              "from --issues",
                })
                row["reason_codes"].insert(0, "MISSING_EXPECTED_ISSUE")
                row["verdict"] = "OPEN"
                rows.append(row)
        for number, row in ((r["number"], r) for r in rows):
            if number is not None and number not in expected and (
                    "MISSING_EXPECTED_ISSUE" not in row["reason_codes"]):
                row["reasons"].append({
                    "code": "UNEXPECTED_ISSUE",
                    "detail": f"issue {number} is not in policy.expected_issues",
                })
                row["reason_codes"].append("UNEXPECTED_ISSUE")
                row["verdict"] = "OPEN"
        for number in sorted(set(manifests_by_number) - set(expected)):
            if number not in supplied:
                row = check_issue(number, {"number": number}, policy,
                                  manifests_by_number)
                row["reasons"].insert(0, {
                    "code": "UNEXPECTED_MANIFEST_ISSUE",
                    "detail": f"manifest for issue {number} is outside "
                              "policy.expected_issues",
                })
                row["reason_codes"].insert(0, "UNEXPECTED_MANIFEST_ISSUE")
                row["verdict"] = "OPEN"
                rows.append(row)

    rows.sort(key=lambda r: (r["number"] is None, r["number"] or 0))
    counts = {verdict: sum(1 for r in rows if r["verdict"] == verdict)
              for verdict in VERDICTS}
    findings = [
        {"issue": r["number"], "reason_codes": list(r["reason_codes"]),
         "details": [x["detail"] for x in r["reasons"]]}
        for r in rows if r["verdict"] == "OPEN"
    ]
    overall = ("CLOSABLE" if rows and not findings and not manifest_errors
               else "OPEN")
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "gate": GATE,
        "overall": overall,
        "counts": counts,
        "rows": rows,
        "findings": findings,
        "manifest_errors": list(manifest_errors),
        "expected_issue_count": len(expected) if expected is not None else None,
        "inputs": inputs,
        "commands": {"exact": command},
        "limits": list(LIMITS),
    }


def md_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\r", " ").replace("\n", " ")


def md_code(value: str) -> str:
    fence = "`" * (max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def render_markdown(report: dict) -> str:
    out: list[str] = [
        "# Issue closure gate",
        "",
        f"- Gate: {md_code(report['gate'])}",
        f"- Generator: {md_code(report['generator'])} "
        f"(schema {report['schema_version']})",
        f"- Issues: {md_code(report['inputs']['issues'])}",
        f"- Policy: {md_code(report['inputs']['policy'])}",
        f"- Manifests: {report['inputs']['manifests']}",
        "",
        f"**Overall: {report['overall']}** -- this tool reports CLOSABLE or OPEN "
        "only; it does not close issues.",
        "",
        "| Verdict | Count |",
        "|---|---:|",
    ]
    for verdict in VERDICTS:
        out.append(f"| {verdict} | {report['counts'][verdict]} |")
    out += ["", "## Findings", ""]
    if report["findings"] or report["manifest_errors"]:
        out.append("| Issue | Reason codes | Details |")
        out.append("|---|---|---|")
        for finding in report["findings"]:
            issue = f"#{finding['issue']}" if finding["issue"] is not None else "—"
            out.append("| {issue} | {codes} | {details} |".format(
                issue=md_cell(issue),
                codes=md_cell(", ".join(finding["reason_codes"])),
                details=md_cell("; ".join(finding["details"])),
            ))
        for error in report["manifest_errors"]:
            out.append(f"| — | MANIFEST_ERROR | {md_cell(error)} |")
    else:
        out.append("None. Every supplied issue satisfies the gate.")

    out += ["", "## Issues", "",
            "| # | Title | GitHub state | Verdict | Reason codes | "
            "Manifest | SHA-256 |", "|---|---|---|---|---|---|---|"]
    for row in report["rows"]:
        number = f"#{row['number']}" if row["number"] is not None else "—"
        out.append("| {number} | {title} | {state} | {verdict} | {codes} | "
                   "{manifest} | {sha} |".format(
                       number=md_cell(number),
                       title=md_cell(row["title"] or ""),
                       state=md_cell(row["github_state"] or "UNKNOWN"),
                       verdict=md_cell(row["verdict"]),
                       codes=md_cell(", ".join(row["reason_codes"])),
                       manifest=md_cell(md_code(row["manifest"])
                                        if row["manifest"] else "—"),
                       sha=md_cell(md_code(row["manifest_sha256"])
                                   if row["manifest_sha256"] else "—"),
                   ))

    out += ["", "## Reproduction command", "",
            md_code(report["commands"]["exact"]), "", "## Limits", ""]
    out.extend(f"- {limit}" for limit in report["limits"])
    out.append("")
    return "\n".join(out)


def assert_no_closing_keywords(*texts: str) -> None:
    """Fail closed if any report would contain a GitHub auto-closing keyword."""
    for text in texts:
        match = CLOSING_KEYWORD.search(text)
        if match:
            raise ValueError(f"refusing to emit auto-closing keyword: "
                             f"{match.group(0)!r}")


def make_command(argv: argparse.Namespace, manifest_args: list[str]) -> str:
    parts = ["python3", "-m", "tools.issue_closure_gate",
             "--issues", shlex.quote(str(argv.issues)),
             "--policy", shlex.quote(str(argv.policy))]
    for path in manifest_args:
        parts += ["--manifest", shlex.quote(path)]
    if argv.manifest_list is not None:
        parts += ["--manifest-list", shlex.quote(str(argv.manifest_list))]
    parts += ["--out", shlex.quote(str(argv.out)),
              "--json", shlex.quote(str(argv.json_out))]
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--issues", required=True, type=Path,
                        help="JSON array from `gh issue list --json`")
    parser.add_argument("--manifest", action="append", default=[], type=Path,
                        help="per-issue evidence manifest JSON (repeatable)")
    parser.add_argument("--manifest-list", type=Path,
                        help="JSON array of manifest paths (alternative to repeats)")
    parser.add_argument("--policy", required=True, type=Path,
                        help="JSON policy: assets, verifications, API gaps")
    parser.add_argument("--out", required=True, type=Path,
                        help="write the deterministic Markdown report here")
    parser.add_argument("--json", dest="json_out", required=True, type=Path,
                        help="write the machine-readable report here")
    args = parser.parse_args(argv)

    for label, path in (("issues", args.issues), ("policy", args.policy)):
        if not path.is_file():
            print(f"issue_closure_gate: not a file: {path} ({label})",
                  file=sys.stderr)
            return 2
    if not args.manifest and args.manifest_list is None:
        print("issue_closure_gate: supply at least one --manifest or "
              "--manifest-list", file=sys.stderr)
        return 2
    if args.manifest_list is not None and not args.manifest_list.is_file():
        print(f"issue_closure_gate: not a file: {args.manifest_list}",
              file=sys.stderr)
        return 2
    for path in args.manifest:
        if not path.is_file():
            print(f"issue_closure_gate: not a file: {path} (manifest)",
                  file=sys.stderr)
            return 2

    try:
        issues = load_issues(args.issues)
        policy = load_policy(args.policy)
        manifest_args = [str(p) for p in args.manifest]
        all_paths = list(args.manifest)
        if args.manifest_list is not None:
            all_paths.extend(expand_manifest_list(args.manifest_list))
        manifests, manifest_errors = load_manifests(all_paths)
    except ValueError as exc:
        print(f"issue_closure_gate: {exc}", file=sys.stderr)
        return 2
    if not issues:
        print("issue_closure_gate: empty issue array; refusing a vacuous pass",
              file=sys.stderr)
        return 2

    command = make_command(args, manifest_args)
    report = build_report(
        issues, policy, manifests, manifest_errors,
        inputs={
            "issues": str(args.issues),
            "policy": str(args.policy),
            "manifests": ", ".join(str(p) for p in all_paths),
        },
        command=command,
    )
    json_text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    markdown = render_markdown(report)
    try:
        assert_no_closing_keywords(json_text, markdown)
    except ValueError as exc:
        print(f"issue_closure_gate: {exc}", file=sys.stderr)
        return 2

    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        args.json_out.write_text(json_text, encoding="utf-8")
    except OSError as exc:
        print(f"issue_closure_gate: cannot write report: {exc}", file=sys.stderr)
        return 2

    counts = report["counts"]
    print(f"wrote {args.out} and {args.json_out} "
          f"({len(report['rows'])} issues; CLOSABLE={counts['CLOSABLE']}, "
          f"OPEN={counts['OPEN']}; overall={report['overall']}; "
          "no issues were closed by this tool)")
    return 0 if report["overall"] == "CLOSABLE" else 1


if __name__ == "__main__":
    sys.exit(main())
