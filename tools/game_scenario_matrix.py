#!/usr/bin/env python3
"""Build a deterministic, fail-closed scenario matrix for game issues #20-#24.

This tool only *joins* JSON records that are supplied on the command line. It
never launches a game, compiles a binary, runs a Windows oracle endpoint, edits
a document, or closes an issue. Every path comes from ``--scenario-records`` (or
is contained in those JSON files); no repository, game, evidence or host path is
built into this module.

The default issue scope is 20,21,22,23,24 (Hitman Contracts/Blood Money,
TrackMania Nations Forever/ESWC and TrackMania United Forever). Scope is a CLI
option (``--issues``), not a hidden constant: a missing record still produces a
NOT TESTED row so the matrix never silently drops an in-scope issue.

A row reaches PASS only when a single oracle-bearing scenario record supplies
all of these at once:

1. ``oracle_verified`` is the boolean ``true`` and ``oracle`` is a real,
   executed original-Windows oracle (the shared acceptance-matrix allow-list;
   dummy/headless/unexecuted labels are rejected);
2. real original and native SHA-256 values that agree across *every* supplied
   record for that issue, and agree with the bytes on disk when
   ``original_path`` / ``native_path`` are supplied;
3. a recorded, non-empty scenario id plus non-empty scenario evidence text;
4. an existing evidence file whose SHA-256 is recomputed here and matches the
   claim.

Anything else is capped at PARTIAL, BLOCKED (recorded blocker) or NOT TESTED
(no record), with explicit reasons. Document/status claims are advisory only:
they can withhold PASS but can never establish or upgrade it. Consequently no
output of this tool closes or advances an issue.

Usage::

    python3 -m tools.game_scenario_matrix \
        --scenario-records records.json [--scenario-records more.json] \
        --out matrix.json [--markdown matrix.md] \
        [--issues 20,21,22,23,24] [--generated-at 2026-11-25T00:00:00Z]

Scenario-record JSON shape (``records``/``scenarios``/``games`` or a bare list)::

    {"records": [{
      "issue": 23,
      "game": "TrackMania Nations Forever",
      "sha256": "<original input SHA-256>",
      "native_sha256": "<native binary SHA-256>",
      "oracle": "original-windows",
      "oracle_verified": true,
      "executed": true,
      "scenario": "race-to-finish",
      "scenario_evidence": "original and native finish frames match",
      "evidence_path": "relative-to-this-json-or-absolute/evidence.json",
      "evidence_sha256": "<SHA-256 of that file>",
      "status": "PARTIAL",          <- advisory doc claim, never evidence
      "source": "docs/GAME_MATRIX.md"
    }]}

Accepted aliases: ``issue_number``, ``original_sha256``/``input_sha256``,
``scenario_id``, ``evidence_text``, ``oracle_name``. Relative evidence paths are
resolved relative to the JSON file that supplied them.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from tools import acceptance_matrix as oracle_lib

SCHEMA_VERSION = 1
GENERATOR = "tools/game_scenario_matrix.py"

PASS = "PASS"
PARTIAL = "PARTIAL"
BLOCKED = "BLOCKED"
NOT_TESTED = "NOT TESTED"
STATUSES = (PASS, PARTIAL, BLOCKED, NOT_TESTED)
DEFAULT_ISSUES = (20, 21, 22, 23, 24)

POLICY = {
    "pass_requires": [
        "executed original-Windows oracle flag (boolean true, real oracle name)",
        "real original and native SHA-256 values that agree across supplied records",
        "recorded scenario id with non-empty scenario evidence",
        "existing evidence file whose SHA-256 is recomputed and matched here",
    ],
    "doc_and_status_claims_are_advisory_only": True,
    "doc_claims_never_upgrade_a_verdict": True,
    "all_supplied_hashes_recomputed_locally": True,
    "closes_or_advances_issues": False,
    "runs_games_or_edits_documents": False,
}

LIMITATIONS = [
    "Proves local record integrity and recomputed SHA-256 consistency only.",
    "Cannot attest that an endpoint was genuinely retail Windows or that a "
    "process actually ran there; endpoint provenance remains the operator's job.",
    "Cannot establish gameplay, frame, audio or input equivalence by itself.",
    "Absent original_path/native_path values are not treated as evidence; hash "
    "agreement is then checked only across the supplied claims.",
    "No GitHub issue is closed, reopened or edited by this tool.",
]

CHECK_NAMES = (
    "has_record",
    "no_blocker",
    "single_oracle_candidate",
    "has_real_oracle_flag",
    "executed_flag",
    "has_real_oracle_name",
    "has_input_sha256",
    "has_native_sha256",
    "hashes_agree_across_sources",
    "original_hash_verified",
    "native_hash_verified",
    "has_scenario_id",
    "has_scenario_evidence",
    "has_evidence_file",
    "evidence_hash_recomputed_matches",
    "oracle_game_matches",
    "doc_claims_are_advisory",
    "pass_eligible",
)

REQUIRED_PASS_CHECKS = (
    "has_real_oracle_flag", "executed_flag", "has_real_oracle_name",
    "has_input_sha256", "has_native_sha256", "hashes_agree_across_sources",
    "original_hash_verified", "native_hash_verified", "has_scenario_id",
    "has_scenario_evidence", "has_evidence_file",
    "evidence_hash_recomputed_matches", "oracle_game_matches",
)

_ISSUE_RE = re.compile(r"(\d+)")
_STATUS_RE = re.compile(
    r"^\s*(PASS|PARTIAL|BUILD\s+ONLY|FAIL|BLOCKED|NOT\s+TESTED|NOT\s+RUN)\b",
    re.IGNORECASE,
)
_CANDIDATE_FIELDS = (
    "oracle", "oracle_verified", "scenario", "scenario_id",
    "evidence_path", "evidence_sha256", "native_sha256",
)


def new_checks() -> dict[str, bool]:
    return {name: False for name in CHECK_NAMES}


def parse_issue(value: Any) -> int | None:
    """Parse an issue number from an int or text such as '23' or '#23'."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        match = _ISSUE_RE.search(value)
        return int(match.group(1)) if match else None
    return None


def parse_issues(text: str) -> list[int]:
    """Parse the ``--issues`` comma-separated list (empty means default)."""
    if not str(text or "").strip():
        return list(DEFAULT_ISSUES)
    out: list[int] = []
    for part in str(text).split(","):
        issue = parse_issue(part.strip())
        if issue is None:
            raise ValueError(f"invalid issue number: {part!r}")
        if issue not in out:
            out.append(issue)
    return out


def resolve_path(raw: Any, base_dir: Path) -> Path | None:
    """Resolve a CLI/JSON-supplied path; relative paths are relative to the JSON."""
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_absolute() else base_dir / path


def normalize_record(raw: dict) -> dict:
    """Apply documented field aliases without changing the supplied claims."""
    out = dict(raw)
    aliases = (
        ("scenario", ("scenario_id", "scenario_name")),
        ("scenario_evidence", ("evidence_text", "evidence_detail")),
        ("sha256", ("original_sha256", "input_sha256")),
        ("oracle", ("oracle_name", "oracle_type")),
    )
    for target, names in aliases:
        if out.get(target) in (None, ""):
            for name in names:
                if out.get(name) not in (None, ""):
                    out[target] = out[name]
                    break
    return out


def advisory_status(value: Any) -> str | None:
    """Normalise a doc/status claim to the matrix vocabulary (advisory only)."""
    match = _STATUS_RE.match(str(value or ""))
    if not match:
        return None
    token = re.sub(r"\s+", " ", match.group(1)).strip().upper()
    if token == "NOT RUN":
        return NOT_TESTED
    if token in ("BUILD ONLY", "FAIL"):
        return PARTIAL
    return token if token in STATUSES else None


def blocker_text(raw: dict) -> str | None:
    """Return a recorded blocker, or None. Blocker text is never evidence."""
    if raw.get("blocked") is True:
        return "record sets blocked=true"
    if raw.get("original_available") is False:
        return "original Windows binary recorded as unavailable"
    if advisory_status(raw.get("status")) == BLOCKED:
        return "recorded status is BLOCKED"
    for key in ("blocker", "blocking_reason", "blocker_reason"):
        text = str(raw.get(key) or "").strip()
        if text:
            return f"recorded blocker: {text}"
    return None


def is_candidate(raw: dict) -> bool:
    """True when a record carries oracle/scenario evidence fields at all."""
    return any(key in raw and raw[key] is not None and raw[key] != ""
               for key in _CANDIDATE_FIELDS)


def load_records(paths: list[str]) -> tuple[list[dict], list[dict]]:
    """Read CLI-supplied JSON records; unreadable inputs are recorded fail-closed."""
    records: list[dict] = []
    problems: list[dict] = []
    for raw in paths:
        path = Path(raw).expanduser()
        source = {"kind": "scenario-records", "path": str(path),
                  "sha256": oracle_lib.sha256_file(path), "status": "ok"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            source.update(status="INVALID", error=f"{type(exc).__name__}: {exc}")
            problems.append(source)
            continue
        if isinstance(data, dict):
            entries = data.get("records", data.get("scenarios",
                                                   data.get("games", [])))
        elif isinstance(data, list):
            entries = data
        else:
            entries = []
        if not isinstance(entries, list):
            source.update(status="INVALID", error="expected a records list")
            problems.append(source)
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                problems.append(dict(source, status="INVALID",
                                     error=f"record #{index} is not an object"))
                continue
            record = normalize_record(entry)
            issue = parse_issue(record.get("issue", record.get("issue_number")))
            if issue is None:
                problems.append(dict(source, status="INVALID",
                                     error=f"record #{index} has no issue number"))
                continue
            record["_issue"] = issue
            record["_source"] = source
            records.append(record)
    return records, problems


def classify_issue(issue: int, records: list[dict]) -> dict:
    """Join every supplied record for one issue into a fail-closed row."""
    reasons: list[str] = []
    conflicts: list[str] = []
    checks = new_checks()
    checks["doc_claims_are_advisory"] = True  # policy, never a source of PASS
    checks["has_record"] = bool(records)

    name = next((str(r["game"]).strip() for r in records
                 if str(r.get("game") or "").strip()), f"issue #{issue}")
    key = f"#{issue}|{oracle_lib.key_of(name)}"
    hashes: dict[str, Any] = {
        "input_sha256": None,
        "native_sha256": None,
        "evidence_sha256": None,
        "actual_evidence_sha256": None,
        "original_path_sha256": None,
        "native_path_sha256": None,
        "by_source": {"input_sha256": {}, "native_sha256": {}},
    }
    scenario: dict[str, str] | None = None
    oracle: dict[str, Any] | None = None
    doc_claims: list[dict] = []
    for record in records:
        claim = (advisory_status(record.get("status"))
                 or advisory_status(record.get("claimed_status"))
                 or advisory_status(record.get("reported_status")))
        if claim:
            doc_claims.append({
                "status": claim,
                "source": record["_source"]["path"],
                "upstream_claim": bool(record.get("upstream_claim")
                                       or record.get("claim")),
            })

    blockers = [(r, text) for r in records if (text := blocker_text(r))]
    checks["no_blocker"] = not blockers

    if not records:
        status = NOT_TESTED
        reasons.append(f"no scenario record supplied for issue #{issue}")
    elif blockers:
        status = BLOCKED
        for record, text in blockers:
            reasons.append(f"PASS withheld: {text} ({record['_source']['path']})")
        reasons.append("recorded blockers are authoritative; no PASS claim may override them")
        for claim in doc_claims:
            reasons.append(f"doc claim {claim['status']} is advisory; not evidence")
    else:
        candidates = [r for r in records if is_candidate(r)]
        checks["single_oracle_candidate"] = len(candidates) == 1
        for claim in doc_claims:
            label = "upstream doc claim" if claim["upstream_claim"] else "doc claim"
            reasons.append(f"{label} {claim['status']} from {claim['source']} is "
                           "advisory; cannot establish or upgrade PASS")
        if not candidates:
            status = NOT_TESTED
            reasons.append("no oracle-bearing scenario record; doc claims alone "
                           "are not evidence")
        elif len(candidates) > 1:
            status = PARTIAL
            reasons.append(f"more than one oracle-bearing record for issue #{issue}; "
                           "refusing to choose among them")
        else:
            candidate = candidates[0]
            base_dir = Path(candidate["_source"]["path"]).parent
            checked = oracle_lib.validate_oracle_record(candidate, base_dir=base_dir)
            check_map = (
                ("has_real_oracle_flag", "has_real_oracle_flag"),
                ("has_real_oracle_name", "has_real_oracle_name"),
                ("has_scenario", "has_scenario_id"),
                ("has_scenario_evidence", "has_scenario_evidence"),
                ("has_evidence_file", "has_evidence_file"),
                ("evidence_hash_matches", "evidence_hash_recomputed_matches"),
                ("has_input_sha256", "has_input_sha256"),
                ("has_native_sha256", "has_native_sha256"),
            )
            for source_check, target_check in check_map:
                checks[target_check] = checked["checks"][source_check]
            reasons.extend("PASS withheld: " + text for text in checked["reasons"])

            # An explicit executed flag must be the boolean True. When absent,
            # the validated oracle_verified flag is the executed-oracle signal.
            executed = candidate.get("executed", candidate.get("oracle_executed"))
            if executed is not None:
                checks["executed_flag"] = executed is True
                if not checks["executed_flag"]:
                    reasons.append("PASS withheld: executed flag is not boolean true")
            else:
                checks["executed_flag"] = checks["has_real_oracle_flag"]

            # Every supplied hash for this issue must agree; a conflict withholds
            # PASS instead of letting one record silently override another.
            observed_input: dict[str, list[str]] = {}
            observed_native: dict[str, list[str]] = {}
            for record in records:
                source_path = record["_source"]["path"]
                input_sha = oracle_lib.norm_sha(
                    record.get("sha256") or record.get("input_sha256")
                    or record.get("original_sha256"))
                native_sha = oracle_lib.norm_sha(record.get("native_sha256"))
                if input_sha:
                    observed_input.setdefault(input_sha, []).append(source_path)
                if native_sha:
                    observed_native.setdefault(native_sha, []).append(source_path)
            hashes["by_source"]["input_sha256"] = {
                sha: sorted(sources) for sha, sources in sorted(observed_input.items())}
            hashes["by_source"]["native_sha256"] = {
                sha: sorted(sources) for sha, sources in sorted(observed_native.items())}
            if len(observed_input) > 1:
                conflicts.append("input SHA-256 differs across supplied records: "
                                 + "; ".join(f"{sha} ({', '.join(sorted(src))})"
                                             for sha, src in sorted(observed_input.items())))
            if len(observed_native) > 1:
                conflicts.append("native SHA-256 differs across supplied records: "
                                 + "; ".join(f"{sha} ({', '.join(sorted(src))})"
                                             for sha, src in sorted(observed_native.items())))
            reasons.extend("PASS withheld: " + text for text in conflicts)
            checks["hashes_agree_across_sources"] = not conflicts

            # Recompute optional original/native file hashes when paths are given.
            for label, path_key, claimed, out_key, check_key in (
                ("original", "original_path", checked["input_sha256"],
                 "original_path_sha256", "original_hash_verified"),
                ("native", "native_path", checked["native_sha256"],
                 "native_path_sha256", "native_hash_verified"),
            ):
                raw_path = candidate.get(path_key)
                if not raw_path:
                    checks[check_key] = True  # nothing on disk to contradict
                    continue
                path = resolve_path(raw_path, base_dir)
                actual = oracle_lib.sha256_file(path) if path is not None else None
                hashes[out_key] = actual
                if actual is None:
                    checks[check_key] = False
                    reasons.append(f"PASS withheld: {label} file {path} is missing "
                                   "or unreadable")
                elif claimed is None:
                    checks[check_key] = False
                    reasons.append(f"PASS withheld: no claimed {label} SHA-256 to "
                                   f"compare with {path}")
                elif actual != claimed:
                    checks[check_key] = False
                    reasons.append(f"PASS withheld: {label} SHA-256 {claimed[:16]}... "
                                   f"does not match recomputed {actual[:16]}... "
                                   f"from {path}")
                else:
                    checks[check_key] = True

            checks["oracle_game_matches"] = (
                not checked["game"] or oracle_lib.key_of(checked["game"])
                == oracle_lib.key_of(name))
            if not checks["oracle_game_matches"]:
                reasons.append("PASS withheld: oracle record names a different game")

            hashes.update(
                input_sha256=checked["input_sha256"],
                native_sha256=checked["native_sha256"],
                evidence_sha256=checked["evidence_sha256"],
                actual_evidence_sha256=checked["actual_evidence_sha256"],
            )
            scenario = {"id": checked["scenario"],
                        "evidence": checked["scenario_evidence"]}
            oracle = {
                "name": checked["oracle"],
                "verified": checked["checks"]["has_real_oracle_flag"],
                "executed": executed if executed is not None else None,
                "valid": checked["valid"],
                "evidence_path": checked["evidence_path"],
                "source": candidate["_source"]["path"],
            }

            status = PARTIAL
            pass_gate = (
                checked["valid"] and checks["single_oracle_candidate"]
                and all(checks[item] for item in REQUIRED_PASS_CHECKS)
                and not conflicts
            )
            checks["pass_eligible"] = pass_gate
            if pass_gate:
                status = PASS
                reasons = [
                    f"executed original-Windows oracle {checked['oracle']!r} validated "
                    f"for scenario {checked['scenario']!r}",
                    f"input SHA-256 {checked['input_sha256']} and native SHA-256 "
                    f"{checked['native_sha256']} agree across supplied records",
                    f"scenario evidence {checked['evidence_path']} SHA-256 "
                    f"{checked['evidence_sha256']} recomputed and matched",
                ]
            else:
                reasons.append("PASS withheld: not every oracle gate is satisfied")

    if not reasons:
        reasons.append("no evidence supplied for this issue")
    return {
        "issue": issue,
        "game": name,
        "key": key,
        "status": status,
        "reasons": reasons,
        "checks": checks,
        "hashes": hashes,
        "scenario": scenario,
        "oracle": oracle,
        "advisory_claims": doc_claims,
        "conflicts": conflicts,
        "sources": sorted({r["_source"]["path"] for r in records}),
        "pass_eligible": checks["pass_eligible"],
    }


def build_matrix(records: list[dict], *, issues: list[int], generated_at: str,
                 source_paths: list[dict], problems: list[dict]) -> dict:
    """Join all supplied records into a deterministic, sorted matrix."""
    by_issue: dict[int, list[dict]] = {}
    for record in records:
        by_issue.setdefault(record["_issue"], []).append(record)
    rows = [classify_issue(issue, by_issue.get(issue, []))
            for issue in sorted(issues)]
    counts = {status: sum(1 for row in rows if row["status"] == status)
              for status in STATUSES}
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at,
        "issues": sorted(issues),
        "policy": POLICY,
        "limitations": LIMITATIONS,
        "inputs": sorted([*source_paths, *problems],
                         key=lambda entry: (entry["kind"], entry["path"],
                                            entry.get("status", ""))),
        "rows": rows,
        "summary": {"total": len(rows), "by_status": counts},
        "issues_closed": 0,
    }


def render_markdown(matrix: dict) -> str:
    """Render a compact, human-readable summary of the matrix."""
    lines = [
        "# Game scenario matrix (issues #20-#24)",
        "",
        f"Generated: {matrix['generated_at']}  ",
        f"Schema: {matrix['schema']}  ",
        "PASS requires an executed original-Windows oracle, agreeing original "
        "and native hashes, a named scenario and an evidence file whose hash is "
        "recomputed here. Doc claims are advisory and no issue is closed.",
        "",
        "| Issue | Game | Status | Scenario | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    pipe = "\\|"
    for row in matrix["rows"]:
        scenario = row["scenario"]["id"] if row["scenario"] else "-"
        reason = row["reasons"][0].replace("|", pipe) if row["reasons"] else "-"
        game = str(row["game"]).replace("|", pipe)
        lines.append(f"| #{row['issue']} | {game} | {row['status']} | "
                     f"{scenario.replace('|', pipe)} | {reason} |")
    counts = matrix["summary"]["by_status"]
    lines += ["", "Summary: " + "; ".join(f"{key}: {value}"
                                          for key, value in counts.items()) + ".", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario-records", "--records", action="append", default=[],
                    metavar="JSON", help="JSON file of per-game scenario records "
                    "(repeatable; all paths are CLI-supplied)")
    ap.add_argument("--issues", default=",".join(str(i) for i in DEFAULT_ISSUES),
                    metavar="LIST", help="comma-separated issue numbers to report "
                    f"(default: {','.join(str(i) for i in DEFAULT_ISSUES)})")
    ap.add_argument("--out", required=True, type=Path,
                    help="write the machine-readable matrix here")
    ap.add_argument("--markdown", type=Path, default=None,
                    help="optionally write a Markdown summary here")
    ap.add_argument("--generated-at", default=None,
                    help="ISO UTC timestamp override for byte-deterministic output")
    args = ap.parse_args(argv)

    if not args.scenario_records:
        ap.error("supply at least one --scenario-records JSON file")
    try:
        issues = parse_issues(args.issues)
    except ValueError as exc:
        ap.error(str(exc))
    if not issues:
        ap.error("--issues must contain at least one issue number")

    records, problems = load_records(args.scenario_records)
    source_paths: list[dict] = []
    for raw in args.scenario_records:
        path = Path(raw).expanduser()
        if any(entry["path"] == str(path) for entry in (*source_paths, *problems)):
            continue
        source_paths.append({"kind": "scenario-records", "path": str(path),
                             "sha256": oracle_lib.sha256_file(path),
                             "status": "ok"})

    matrix = build_matrix(
        records, issues=issues,
        generated_at=args.generated_at or oracle_lib.utc_now(),
        source_paths=source_paths, problems=problems,
    )
    payload = json.dumps(matrix, indent=2, sort_keys=True) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(matrix), encoding="utf-8")

    counts = matrix["summary"]["by_status"]
    print("summary: " + "; ".join(f"{key}: {value}" for key, value in counts.items()))
    print(f"matrix: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
