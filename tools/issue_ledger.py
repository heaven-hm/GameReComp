#!/usr/bin/env python3
"""Fail-closed closure ledger over CLI-supplied GitHub issue JSON.

This tool reads the JSON array produced by ``gh issue list --json`` from a
CLI-supplied path and cross-checks every CLOSED issue against a recorded
100%-acceptance evidence file found in a CLI-supplied evidence directory.
It never calls GitHub, never runs ``gh`` itself, and never closes, reopens or
edits an issue: it only reads the supplied files and writes two reports.

A CLOSED issue is a ``PASS`` only when a readable evidence file that claims
that issue number:

* explicitly asserts ``acceptance_100`` is true;
* lists a non-empty set of uniquely identified acceptance criteria, all with
  ``met`` true; and
* cites, for every met criterion, an artifact that exists on disk.

Anything short of that -- a missing file, a stale/mislabelled file, an
unreadable file, an absent 100% claim, an unmet criterion, a missing cited
artifact, a hash claim that disagrees with the bytes, or two files claiming the
same issue -- is a ``PREMATURE_CLOSE`` finding.  A PASS is never inferred from
the word "closed" alone; PASS gating is honest and structural.

Both the JSON and Markdown reports are byte-stable: no clock, environment,
network, or directory-walk-order dependence.  The same argv and the same bytes
always produce the same two files.

Usage:
    python3 -m tools.issue_ledger \
        --issues <issues.json> --evidence-dir <dir> \
        --out <ledger.md> --json <ledger.json> [--label <label>]
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
GENERATOR = "tools/issue_ledger.py"

# PASS is reachable, but only through the full evidence chain above.  Every
# other verdict is fail-closed and is never upgraded silently.
VERDICTS = ("PASS", "PREMATURE_CLOSE", "OPEN", "BLOCKED")
FINDING_TYPES = ("PREMATURE_CLOSE", "BLOCKED")

PRODUCER_COMMAND = (
    "gh issue list --state all --limit 100 "
    "--json number,title,state,stateReason,closedAt"
)
TEMPLATE_COMMAND = (
    "python3 -m tools.issue_ledger --issues <ISSUES.json> "
    "--evidence-dir <EVIDENCE_DIR> --out <LEDGER.md> "
    "--json <LEDGER.json> [--label <LABEL>]"
)

LIMITS = (
    "Only CLOSED issues are gated. OPEN issues are recorded with verdict OPEN "
    "and are never required to carry acceptance evidence. An all-OPEN input "
    "therefore yields overall PASS with zero PASS rows: no closure claim was "
    "made, so none was proven.",

    "stateReason is recorded but does not exempt a closure: every CLOSED issue "
    "must have a 100%-acceptance evidence file, including NOT_PLANNED and "
    "DUPLICATE closures.",
    "PASS is emitted only when the evidence file asserts acceptance_100=true, "
    "lists non-empty uniquely-identified criteria that are all met, and every "
    "cited artifact exists on disk. An empty or partial criterion list cannot "
    "produce a PASS.",
    "Evidence files are discovered by the 'issue' field inside each JSON file "
    "under the CLI-supplied evidence directory (sorted by relative path), not "
    "by a hard-coded filename. Two files claiming one issue are ambiguous and "
    "fail closed.",
    "Artifact paths inside an evidence file resolve relative to that file's own "
    "directory; absolute paths are allowed. The SHA-256 of each evidence file "
    "is re-derived from its bytes, and a 'sha256' claim that disagrees is a "
    "finding. No file or claim is fetched from the network.",
    "No timestamps, environment values or machine-dependent ordering appear in "
    "the output: identical argv and identical bytes yield byte-identical "
    "reports.",
    "This tool performs read-only analysis. It does not close or reopen issues, "
    "post comments, or modify the supplied JSON or evidence files.",
)

_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def sha256_file(path: Path) -> str | None:
    """Exact SHA-256 of a file, or None when it cannot be read (never guessed)."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX.match(value))


def issue_number(value: Any) -> int | None:
    """Coerce a GitHub issue number to int; reject bools and junk."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def normalize_state(value: Any) -> str | None:
    """GitHub returns OPEN/CLOSED (occasionally lower-case); canonicalise."""
    if not isinstance(value, str):
        return None
    state = value.strip().upper()
    return state if state in ("OPEN", "CLOSED") else None


def load_issues(path: Path) -> list[dict]:
    """Load the CLI-supplied ``gh issue list --json`` array of objects."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array of issues")
    if not all(isinstance(row, dict) for row in data):
        raise ValueError(f"{path}: every issue row must be a JSON object")
    return data


# --------------------------------------------------------------------------
# evidence discovery / validation
# --------------------------------------------------------------------------
def discover_evidence(evidence_dir: Path) -> dict[int, list[dict]]:
    """Map issue number -> evidence records found under the CLI-supplied dir.

    An evidence file is any readable JSON object with a positive integer
    'issue' field. Files that cannot be parsed or that carry no issue number
    cannot be attributed to a closed issue and therefore do not count as
    evidence (fail closed). Records are ordered by path relative to the root.
    """
    found: dict[int, list[dict]] = {}
    if not evidence_dir.is_dir():
        return found
    paths = sorted(
        (p for p in evidence_dir.rglob("*.json") if p.is_file()),
        key=lambda p: p.relative_to(evidence_dir).as_posix(),
    )
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        number = issue_number(data.get("issue"))
        if number is None:
            continue
        record = {
            "path": str(path),
            "rel": path.relative_to(evidence_dir).as_posix(),
            "data": data,
            "sha256": sha256_file(path),
        }
        found.setdefault(number, []).append(record)
    return found


def resolve_artifact(path_text: str, evidence_path: Path) -> Path | None:
    """Resolve a CLI-supplied artifact path; relative paths use the file's dir."""
    candidate = Path(path_text)
    return candidate if candidate.is_absolute() else evidence_path.parent / candidate


def validate_evidence(record: dict) -> list[str]:
    """Return every reason this evidence fails to certify issue ``number``.

    An empty list means the evidence is a genuine 100%-acceptance record.
    """
    reasons: list[str] = []
    data = record["data"]

    actual = record.get("sha256")
    if actual is None:
        reasons.append("evidence file is unreadable")
    claimed_hash = data.get("sha256")
    if claimed_hash is not None:
        if not is_sha256(claimed_hash):
            reasons.append("malformed sha256 claim in evidence file")
        elif actual is not None and claimed_hash.lower() != actual:
            reasons.append(
                f"evidence file hash mismatch (claimed {claimed_hash}, "
                f"recomputed {actual})")

    if data.get("acceptance_100") is not True:
        reasons.append("no explicit acceptance_100=true claim")

    criteria = data.get("acceptance_criteria", data.get("criteria"))
    if not isinstance(criteria, list) or not criteria:
        reasons.append("no non-empty acceptance_criteria list")
        return reasons

    seen_ids: set[str] = set()
    for index, criterion in enumerate(criteria):
        if not isinstance(criterion, dict):
            reasons.append(f"criterion[{index}] is not an object")
            continue
        cid = criterion.get("id")
        if not isinstance(cid, str) or not cid.strip():
            reasons.append(f"criterion[{index}] has no id")
        elif cid in seen_ids:
            reasons.append(f"duplicate criterion id {cid!r}")
        else:
            seen_ids.add(cid)
        if criterion.get("met") is not True:
            reasons.append(f"criterion {cid or index} is not met")
        artifact = criterion.get("evidence")
        if not isinstance(artifact, str) or not artifact.strip():
            reasons.append(f"criterion {cid or index} cites no artifact path")
            continue
        resolved = resolve_artifact(artifact, Path(record["path"]))
        if resolved is None or not resolved.is_file():
            reasons.append(
                f"criterion {cid or index} cites missing artifact {artifact!r}")
    return reasons


# --------------------------------------------------------------------------
# per-issue verdicts
# --------------------------------------------------------------------------
def check_issue(issue: dict, evidence_map: dict[int, list[dict]]) -> dict:
    """Assign exactly one fail-closed verdict to one issue row."""
    number = issue_number(issue.get("number"))
    title = issue.get("title") if isinstance(issue.get("title"), str) else None
    state = normalize_state(issue.get("state"))
    state_reason = (issue.get("stateReason")
                    if isinstance(issue.get("stateReason"), str) else None)
    closed_at = (issue.get("closedAt")
                 if isinstance(issue.get("closedAt"), str) else None)

    row: dict[str, Any] = {
        "number": number,
        "title": title,
        "state": state,
        "state_reason": state_reason,
        "closed_at": closed_at,
        "verdict": "BLOCKED",
        "reason": "",
        "evidence_file": None,
        "evidence_sha256": None,
    }

    if number is None:
        row["reason"] = "malformed issue row: missing or invalid number"
        return row
    if state is None:
        row["reason"] = f"#{number} has an unrecognised state {issue.get('state')!r}"
        return row
    if state == "OPEN":
        row["verdict"] = "OPEN"
        row["reason"] = f"#{number} is open; no closure claim to gate"
        return row

    candidates = evidence_map.get(number, [])
    if not candidates:
        row["verdict"] = "PREMATURE_CLOSE"
        row["reason"] = (
            f"#{number} is CLOSED but no 100%-acceptance evidence file was "
            "supplied")
        return row
    if len(candidates) > 1:
        row["verdict"] = "PREMATURE_CLOSE"
        row["reason"] = (
            f"#{number} is CLOSED but {len(candidates)} evidence files claim it "
            "(ambiguous); expected exactly one")
        return row

    record = candidates[0]
    row["evidence_file"] = record["rel"]
    row["evidence_sha256"] = record["sha256"]
    reasons = validate_evidence(record)
    if reasons:
        row["verdict"] = "PREMATURE_CLOSE"
        row["reason"] = f"#{number} evidence is not a valid 100% record: " + "; ".join(reasons)
        return row

    row["verdict"] = "PASS"
    row["reason"] = (
        f"#{number} is CLOSED with a valid 100%-acceptance evidence file "
        f"({record['rel']}); all criteria met and artifacts present")
    return row


# --------------------------------------------------------------------------
# report assembly
# --------------------------------------------------------------------------
def build_report(issues: list[dict], evidence_map: dict[int, list[dict]], *,
                 label: str, issues_source: str, evidence_dir: str,
                 command: str) -> dict:
    """Build the machine-readable ledger; no clock, environment or network."""
    rows = [check_issue(issue, evidence_map) for issue in issues]
    rows.sort(key=lambda r: (r["number"] is None, r["number"] or 0,
                             r["title"] or ""))

    findings = [
        {"type": row["verdict"], "issue": row["number"], "detail": row["reason"]}
        for row in rows if row["verdict"] in FINDING_TYPES
    ]
    counts = {verdict: 0 for verdict in VERDICTS}
    for row in rows:
        counts[row["verdict"]] += 1

    if not rows:
        overall = "BLOCKED"
    elif findings:
        overall = "FAIL"
    else:
        overall = "PASS"

    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "label": label,
        "gate": "CLOSED_ISSUES_REQUIRE_100PCT_ACCEPTANCE_EVIDENCE",
        "overall": overall,
        "counts": counts,
        "premature_close_count": counts["PREMATURE_CLOSE"],
        "rows": rows,
        "findings": findings,
        "inputs": {
            "issues_source": issues_source,
            "evidence_dir": evidence_dir,
        },
        "commands": {
            "exact": command,
            "template": TEMPLATE_COMMAND,
            "producer": PRODUCER_COMMAND,
        },
        "limits": list(LIMITS),
    }


# --------------------------------------------------------------------------
# deterministic Markdown rendering
# --------------------------------------------------------------------------
def md_cell(value: Any) -> str:
    """Escape a value for a GFM table cell."""
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\r", " ").replace("\n", " ")


def _fence(value: str) -> str:
    return "`" * (max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)


def md_code(value: str) -> str:
    fence = _fence(value)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def md_block(value: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}sh\n{value}\n{fence}"


def render_markdown(report: dict) -> str:
    """Render a byte-stable Markdown ledger from an already-built report."""
    out: list[str] = [
        "# Issue closure ledger",
        "",
        f"- Label: {md_cell(report['label'])}",
        f"- Gate: {md_code(report['gate'])}",
        f"- Issues source: {md_code(report['inputs']['issues_source'])}",
        f"- Evidence directory: {md_code(report['inputs']['evidence_dir'])}",
        f"- Generator: {md_code(report['generator'])} "
        f"(schema {report['schema_version']})",
        "",
        f"**Overall: {report['overall']}** -- a CLOSED issue is a PASS only "
        "with a recorded 100%-acceptance evidence file. Every finding is "
        "explicit; nothing is downgraded into a pass.",
        "",
        "## Verdict counts",
        "",
        "| Verdict | Count |",
        "|---|---:|",
    ]
    for verdict in VERDICTS:
        out.append(f"| {verdict} | {report['counts'][verdict]} |")
    out += [
        f"| **Total issues** | **{len(report['rows'])}** |",
        f"| **Premature closes** | **{report['premature_close_count']}** |",
        "",
        "## Findings",
        "",
    ]
    if report["findings"]:
        out += [
            "| Type | Issue | Detail |",
            "|---|---:|---|",
        ]
        for finding in report["findings"]:
            issue_text = (f"#{finding['issue']}"
                          if finding["issue"] is not None else "—")
            out.append("| {kind} | {issue} | {detail} |".format(
                kind=md_cell(finding["type"]),
                issue=md_cell(issue_text),
                detail=md_cell(finding["detail"]),
            ))
    else:
        out.append("None. No CLOSED issue lacks a valid 100%-acceptance record.")

    out += [
        "",
        "## Issues",
        "",
        "| # | Title | State | Reason | Verdict | Evidence file | "
        "Evidence SHA-256 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        number = f"#{row['number']}" if row["number"] is not None else "—"
        out.append("| {number} | {title} | {state} | {reason} | {verdict} | "
                   "{evidence} | {sha} |".format(
                       number=md_cell(number),
                       title=md_cell(row["title"] or ""),
                       state=md_cell(row["state"] or "UNKNOWN"),
                       reason=md_cell(row["reason"]),
                       verdict=md_cell(row["verdict"]),
                       evidence=md_cell(md_code(row["evidence_file"])
                                        if row["evidence_file"] else "—"),
                       sha=md_cell(md_code(row["evidence_sha256"])
                                   if row["evidence_sha256"] else "—"),
                   ))
    out += [
        "",
        "## Reproduction commands",
        "",
        "Exact invocation used for this ledger (run from the repository root):",
        "",
        md_block(report["commands"]["exact"]),
        "",
        "Template with placeholders for fresh inputs:",
        "",
        md_block(report["commands"]["template"]),
        "",
        "Input producer; this ledger does not run it and never fabricates "
        "an issue or evidence file:",
        "",
        md_block(report["commands"]["producer"]),
        "",
        "## Limits",
        "",
    ]
    out.extend(f"- {limit}" for limit in report["limits"])
    out.append("")
    return "\n".join(out)


def make_command(argv: list[str]) -> str:
    """Rebuild the exact ledger invocation from parsed CLI values."""
    parts = ["python3", "-m", "tools.issue_ledger",
             "--issues", shlex.quote(str(argv[0])),
             "--evidence-dir", shlex.quote(str(argv[1])),
             "--out", shlex.quote(str(argv[2])),
             "--json", shlex.quote(str(argv[3]))]
    if argv[4] is not None:
        parts += ["--label", shlex.quote(str(argv[4]))]
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--issues", required=True, type=Path,
                        help="JSON array from `gh issue list --json`")
    parser.add_argument("--evidence-dir", required=True, type=Path,
                        help="directory holding 100%%-acceptance evidence JSON")
    parser.add_argument("--out", required=True, type=Path,
                        help="write the deterministic Markdown ledger here")
    parser.add_argument("--json", dest="json_out", required=True, type=Path,
                        help="write the machine-readable ledger JSON here")
    parser.add_argument("--label", default="CLI-supplied issue ledger",
                        help="human-readable label recorded in the ledger")
    args = parser.parse_args(argv)

    if not args.issues.is_file():
        print(f"issue_ledger: not a file: {args.issues}", file=sys.stderr)
        return 2
    if not args.evidence_dir.is_dir():
        print(f"issue_ledger: not a directory: {args.evidence_dir}", file=sys.stderr)
        return 2
    try:
        issues = load_issues(args.issues)
    except (OSError, ValueError) as exc:
        print(f"issue_ledger: cannot read issues JSON: {exc}", file=sys.stderr)
        return 2
    if not issues:
        print("issue_ledger: empty issue array; refusing to emit a vacuous PASS",
              file=sys.stderr)
        return 2

    evidence_map = discover_evidence(args.evidence_dir)
    command = make_command([args.issues, args.evidence_dir, args.out,
                            args.json_out, args.label])
    report = build_report(
        issues, evidence_map, label=args.label,
        issues_source=str(args.issues), evidence_dir=str(args.evidence_dir),
        command=command)
    markdown = render_markdown(report)
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        args.json_out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    except OSError as exc:
        print(f"issue_ledger: cannot write ledger: {exc}", file=sys.stderr)
        return 2

    counts = report["counts"]
    print(f"wrote {args.out} and {args.json_out} "
          f"({len(report['rows'])} issues; PASS={counts['PASS']}, "
          f"PREMATURE_CLOSE={counts['PREMATURE_CLOSE']}, "
          f"OPEN={counts['OPEN']}, BLOCKED={counts['BLOCKED']}; "
          f"overall={report['overall']})")
    if report["findings"]:
        print(f"issue_ledger: {len(report['findings'])} finding(s); "
              "closure gate FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
