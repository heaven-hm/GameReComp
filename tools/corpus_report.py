#!/usr/bin/env python3
"""Generate a machine-readable corpus acceptance report from CLI-supplied evidence.

This tool audits the evidence produced by the corpus tooling -- it never runs a
game, never compiles anything, and never fabricates a result. Every path it
reads is supplied on the command line; there are no built-in corpus locations.

Inputs (all optional except --out, but at least one evidence source is required):

* ``--summary``  : ``summary.json`` written by :mod:`tools.game_runner`
  (binary path/hash, command, exit code, evidence dir, frame paths).
* ``--frame-proof`` : ``frame-proof.json`` written by :mod:`tools.frame_proof`
  (per-frame SHA-256, dimensions, distinct-colour count, blank flag).
* ``--results`` : a results JSON such as ``reports/games/results.json``
  (input SHA-256, build/port details, run status, exit code).
* ``--scenarios`` : an *optional* scenario/assertion JSON. A ``PASS`` verdict can
  only be produced from a record in this file that names a scenario, an oracle
  and an existing evidence file (with an optional matching SHA-256). Without it
  the highest verdict this tool can emit is ``PARTIAL``.

Verdict vocabulary: ``PASS`` / ``PARTIAL`` / ``BUILD ONLY`` / ``FAIL`` / ``BLOCKED``.
A build, a process launch and a non-blank frame are each recorded as facts, but
none of them alone is a PASS. ``PASS`` is emitted only for a game whose scenario
record passes strict validation *and* whose run evidence exists. Everything
else is fail-closed with an explicit human-readable reason.

Usage:
    python3 -m tools.corpus_report \\
        --summary <summary.json> --frame-proof <frame-proof.json> \\
        --results <results.json> --scenarios <scenarios.json> \\
        --out <report.json> [--label <label>] [--note <note>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/corpus_report.py"
VERDICTS = ("PASS", "PARTIAL", "BUILD ONLY", "FAIL", "BLOCKED")

# Oracle strings that do not establish an independent acceptance oracle.
_NO_ORACLE = {
    "", "none", "no oracle", "no-oracle", "null", "n/a", "na", "n.a.",
    "unspecified", "unknown", "dummy", "dummy device", "not supplied",
    "windows oracle",  # an unexecuted oracle is not an observation
}

# Run statuses that are an explicit failure rather than a clean/partial launch.
_FAILURE_RUN_STATUSES = {"aborted", "crashed", "boot-failed", "boot_failed", "failed"}
_FAILURE_TEXT = ("crashed", "aborted", "boot-failed", "cannot boot", "unhandled")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str | None:
    """Exact SHA-256 of a file, or None when it cannot be read (never guessed)."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def norm(path: str | None) -> str:
    return os.path.normpath(path) if path else ""


def load_json(path: Path) -> dict:
    """Load a JSON object from a CLI-supplied path; fail closed on bad input."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def index_frame_proof(proof: dict | None) -> tuple[dict, dict]:
    """Index proof rows by (game, path) and by path alone.

    A row carrying a real hash is preferred over one that does not. The
    path-only index is used later only when a path was proven once and is
    therefore unambiguous.
    """
    by_game_path: dict[tuple[str, str], dict] = {}
    by_path: dict[str, list[dict]] = {}
    for row in (proof or {}).get("frames", []):
        if not isinstance(row, dict):
            continue
        game = str(row.get("game", ""))
        path = norm(str(row.get("path", "")))
        key = (game, path)
        if key not in by_game_path or (
            "sha256" in row and "sha256" not in by_game_path[key]
        ):
            by_game_path[key] = row
        by_path.setdefault(path, []).append(row)
    return by_game_path, by_path


def classify_frames(frames: list[dict], by_game_path: dict,
                    by_path: dict) -> list[dict]:
    """Attach proof to each recorded frame without inventing any field.

    A frame with no matching proof row keeps ``sha256``/``distinct_colors`` as
    null and is marked ``UNPROVEN``. A proof error is marked ``FAIL``. Only a
    decoded, non-blank frame is marked ``OK``.
    """
    game = str(frames[0].get("game", "")) if frames and "game" in frames[0] else ""
    resolved: list[dict] = []
    for frame in frames:
        path = str(frame.get("path", ""))
        key = (game, norm(path))
        proof = by_game_path.get(key)
        if proof is None:
            candidates = by_path.get(norm(path), [])
            if len(candidates) == 1:
                proof = candidates[0]
        row: dict[str, Any] = {
            "path": path,
            "exists": bool(path) and Path(path).is_file(),
            "sha256": None,
            "distinct_colors": None,
            "width": frame.get("width"),
            "height": frame.get("height"),
            "blank": None,
            "marker": "UNPROVEN — no matching frame-proof entry",
        }
        if proof is not None:
            if isinstance(frame.get("width"), int):
                row["width"] = frame["width"]
            elif isinstance(proof.get("width"), int):
                row["width"] = proof["width"]
            if isinstance(frame.get("height"), int):
                row["height"] = frame["height"]
            elif isinstance(proof.get("height"), int):
                row["height"] = proof["height"]
            if proof.get("error"):
                row["marker"] = f"FAIL — proof error: {proof['error']}"
            else:
                digest = proof.get("sha256")
                colours = proof.get("distinct_colors")
                row["sha256"] = digest if isinstance(digest, str) else None
                row["distinct_colors"] = colours if isinstance(colours, int) else None
                row["blank"] = bool(proof.get("blank")) or colours == 1
                if not row["sha256"] or not isinstance(colours, int):
                    row["marker"] = "UNPROVEN — proof has no hash or colour count"
                elif row["blank"]:
                    row["marker"] = "BLANK — single-colour frame; not graphics evidence"
                else:
                    row["marker"] = "OK — non-blank render evidence only, not gameplay"
        resolved.append(row)
    return resolved


def _log_path(evidence: Any) -> str | None:
    if not evidence:
        return None
    return str(Path(str(evidence)) / "run.log")


def records_from_summary(summary: dict, by_game_path: dict,
                         by_path: dict) -> list[dict]:
    records: list[dict] = []
    for rec in summary.get("results", []):
        if not isinstance(rec, dict):
            continue
        game = str(rec.get("game", "?"))
        raw_frames = [f for f in rec.get("frames", []) if isinstance(f, dict)]
        for f in raw_frames:
            f.setdefault("game", game)
        frames = classify_frames(raw_frames, by_game_path, by_path)
        log = _log_path(rec.get("evidence"))
        blocked = str(rec.get("status", "")).upper() == "BLOCKED"
        records.append({
            "game": game,
            "sources": ["summary"],
            "binary": str(rec.get("binary") or "") or None,
            "binary_sha256": rec.get("binary_sha256"),
            "input_sha256": None,
            "log": log,
            "log_exists": bool(log) and Path(log).is_file(),
            "frames": frames,
            "exit_code": rec.get("exit_code"),
            "run_status": "blocked" if blocked else None,
            "status_text": str(rec.get("reason") or ""),
            "ran": "exit_code" in rec and not blocked,
            "blocked": blocked,
            "build_evidence": bool(rec.get("binary") or rec.get("binary_sha256")),
        })
    return records


def records_from_results(results: dict) -> list[dict]:
    records: list[dict] = []
    rows = results.get("runs", results.get("results", []))
    for row in rows:
        if not isinstance(row, dict):
            continue
        game = str(row.get("game") or row.get("relative_path")
                   or row.get("filename") or "?")
        run_status = str(row.get("run_status") or row.get("verification") or "").lower()
        status_text = str(row.get("status") or "")
        blocked = run_status == "blocked" or "BLOCKED" in status_text.upper()
        log = row.get("log") or row.get("evidence")
        records.append({
            "game": game,
            "sources": ["results"],
            "binary": str(row.get("binary") or "") or None,
            "binary_sha256": row.get("binary_sha256"),
            "input_sha256": row.get("input_sha256"),
            "log": str(log) if log else None,
            "log_exists": bool(log) and Path(str(log)).is_file(),
            "frames": [],
            "exit_code": row.get("exit_code"),
            "run_status": run_status or None,
            "status_text": status_text,
            "ran": ("exit_code" in row) or run_status in {
                "exited", "aborted", "crashed", "timeout", "partial", "launch-failed"},
            "blocked": blocked,
            "build_evidence": bool(row.get("port_details") or row.get("project_dir")
                                   or row.get("input_sha256") or row.get("binary")),
        })
    return records


def validate_scenario(record: Any) -> dict | None:
    """Return a validated PASS scenario, or None (never a pass) on any gap."""
    if not isinstance(record, dict) or str(record.get("verdict", "")).upper() != "PASS":
        return None
    oracle = str(record.get("oracle") or "").strip()
    scenario = str(record.get("scenario") or "").strip()
    evidence = str(record.get("evidence") or "").strip()
    if oracle.lower() in _NO_ORACLE or not scenario or not evidence:
        return None
    evidence_path = record.get("evidence_path")
    if not evidence_path:
        return None
    path = Path(str(evidence_path))
    if not path.is_file():
        return None
    actual = sha256_file(path)
    if actual is None:
        return None
    claimed = record.get("evidence_sha256")
    if claimed and str(claimed).lower() != actual:
        return None
    return {
        "scenario": scenario,
        "oracle": oracle,
        "evidence": evidence,
        "evidence_path": str(evidence_path),
        "evidence_sha256": actual,
    }


def index_scenarios(scenarios: dict | None) -> dict[str, dict | None]:
    """Map a lower-cased game name to a validated PASS scenario (or rejection)."""
    out: dict[str, dict | None] = {}
    for record in (scenarios or {}).get("records", []):
        game = str(record.get("game", "")) if isinstance(record, dict) else ""
        if not game:
            continue
        key = game.lower()
        validated = validate_scenario(record)
        # A rejected PASS claim must not be silently replaced by a later valid
        # one for the same game: keep the explicit rejection so PASS is refused.
        if key in out and out[key] is None:
            continue
        out[key] = validated
    return out


def assign_verdict(rec: dict, scenario: dict | None) -> tuple[str, str]:
    """Assign exactly one verdict with a reason, fail-closed.

    Ordering matters: a validated scenario is the only source of PASS, and it
    is downgraded when the run evidence it depends on is absent.
    """
    frames = rec["frames"]
    nonblank = any(f["marker"].startswith("OK") for f in frames)
    all_blank = bool(frames) and all(f["marker"].startswith("BLANK") for f in frames)

    if scenario is not None:
        if rec["ran"] and (rec["log_exists"] or nonblank):
            return ("PASS",
                    f"validated scenario '{scenario['scenario']}' with oracle "
                    f"'{scenario['oracle']}' and evidenced "
                    f"{scenario['evidence_path']}; {scenario['evidence']}")
        return ("PARTIAL",
                "scenario PASS claim present but run/log/frame evidence is "
                "missing; downgraded, PASS not invented")

    if rec["blocked"]:
        return ("BLOCKED", rec["status_text"] or "no native binary / input available")

    status = (rec.get("run_status") or "").lower()
    if status in _FAILURE_RUN_STATUSES or any(
        token in rec["status_text"].lower() for token in _FAILURE_TEXT
    ):
        return ("FAIL", rec["status_text"] or f"explicit failure status: {status}")

    if not rec["ran"]:
        if rec["build_evidence"]:
            return ("BUILD ONLY",
                    "build/input evidence recorded but no run record; "
                    "no process launch claimed")
        return ("BLOCKED", "no build, input or run evidence supplied")

    if all_blank:
        return ("FAIL", "every captured frame is single-colour BLANK; not graphics evidence")

    if rec["exit_code"] == "TIMEOUT":
        return ("PARTIAL", "process stayed alive until timeout; no independent scenario or oracle")

    if nonblank:
        return ("PARTIAL", "non-blank frame proof only; no independent scenario or oracle")

    return ("PARTIAL", "process launched; no independent scenario or oracle")


def build_report(summary: dict | None, frame_proof: dict | None,
                 results: dict | None, scenarios: dict | None, *,
                 label: str, note: str | None = None,
                 source_paths: dict | None = None) -> dict:
    """Combine the supplied evidence dicts into the machine-readable report."""
    by_game_path, by_path = index_frame_proof(frame_proof)
    collected = records_from_summary(summary or {}, by_game_path, by_path)
    seen = {(r["game"].lower(), s) for r in collected for s in r["sources"]}
    for rec in records_from_results(results or {}):
        key = (rec["game"].lower(), "results")
        if key in seen:
            continue
        collected.append(rec)
        seen.add(key)

    scenario_index = index_scenarios(scenarios)
    games: list[dict] = []
    counts = {verdict: 0 for verdict in VERDICTS}
    for rec in sorted(collected, key=lambda r: (r["game"].lower(), r["game"])):
        scenario = scenario_index.get(rec["game"].lower())
        verdict, reason = assign_verdict(rec, scenario)
        counts[verdict] += 1
        checks = {
            "has_binary_identity": bool(rec["binary_sha256"] or rec["input_sha256"]),
            "has_log_file": rec["log_exists"],
            "has_run_record": rec["ran"],
            "has_frame_proof": any(f["sha256"] for f in rec["frames"]),
            "has_nonblank_frame": any(f["marker"].startswith("OK") for f in rec["frames"]),
            "has_valid_pass_scenario": scenario is not None,
        }
        games.append({
            "game": rec["game"],
            "verdict": verdict,
            "reason": reason,
            "checks": checks,
            "evidence": {
                "binary": rec["binary"],
                "binary_sha256": rec["binary_sha256"],
                "input_sha256": rec["input_sha256"],
                "log": {"path": rec["log"], "exists": rec["log_exists"]},
                "exit_code": rec["exit_code"],
                "run_status": rec["run_status"],
                "frames": rec["frames"],
                "sources": rec["sources"],
                "scenario": scenario,
            },
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at_utc": utc_now(),
        "label": label,
        "note": note,
        "sources": source_paths or {},
        "verdict_vocabulary": list(VERDICTS),
        "limits": [
            "PASS is only emitted from a validated CLI-supplied scenario with "
            "an oracle and an existing evidence file; it is never inferred "
            "from a build, launch, log or frame.",
            "No original-Windows execution oracle exists here; no Windows "
            "frame or gameplay comparison is claimed.",
            "Non-blank frames prove rendered pixel content only, not correct "
            "gameplay, input, audio or save/load.",
            "Logs, hashes and frames are recorded as supplied paths/values; "
            "no missing file or colour count is fabricated.",
        ],
        "counts": counts,
        "games": games,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--summary", type=Path, help="game_runner summary.json")
    ap.add_argument("--frame-proof", dest="frame_proof", type=Path,
                    help="frame_proof.py frame-proof.json")
    ap.add_argument("--results", type=Path,
                    help="a results JSON such as reports/games/results.json")
    ap.add_argument("--scenarios", type=Path,
                    help="optional scenario assertions; sole source of PASS")
    ap.add_argument("--out", required=True, type=Path, help="write report JSON here")
    ap.add_argument("--label", default="CLI-supplied corpus evidence",
                    help="human-readable label for this run")
    ap.add_argument("--note", help="optional provenance note")
    args = ap.parse_args(argv)

    if not any((args.summary, args.results)):
        print("corpus_report: supply at least one of --summary / --results",
              file=sys.stderr)
        return 2

    source_paths: dict[str, str] = {}
    try:
        loaded: dict[str, dict | None] = {}
        for name, path in (("summary", args.summary),
                           ("frame_proof", args.frame_proof),
                           ("results", args.results),
                           ("scenarios", args.scenarios)):
            if path is None:
                loaded[name] = None
                continue
            loaded[name] = load_json(path)
            source_paths[name] = str(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"corpus_report: {exc}", file=sys.stderr)
        return 2

    report = build_report(
        loaded["summary"], loaded["frame_proof"],
        loaded["results"], loaded["scenarios"],
        label=args.label, note=args.note, source_paths=source_paths)
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    except OSError as exc:
        print(f"corpus_report: cannot write {args.out}: {exc}", file=sys.stderr)
        return 2

    counts = ", ".join(f"{k}={v}" for k, v in report["counts"].items())
    print(f"wrote {args.out} ({len(report['games'])} games; {counts})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
