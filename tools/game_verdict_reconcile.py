#!/usr/bin/env python3
"""Reconcile game verdicts from independently supplied evidence (fail-closed).

This tool joins, by game name, three CLI-supplied evidence sources:

* ``--matrix-rows`` / ``--row`` / ``--game-matrix``: game-matrix verdicts. These
  are *document claims*: advisory only, never evidence that can raise a verdict.
* ``--corpus-acceptance``: JSON from :mod:`tools.corpus_acceptance` (the recorded
  build/launch/frame evidence).
* ``--frame-proof``: JSON from :mod:`tools.frame_proof` (per-frame SHA-256,
  dimensions, colour counts). Every claimed frame hash is recomputed here.

An optional ``--oracle-evidence`` JSON is the *only* input allowed to produce
PASS, and only when it passes the same strict validation used by
:mod:`tools.acceptance_matrix` (executed original-Windows oracle, real hashes,
named scenario, existing evidence file whose hash is recomputed here).

Every path is supplied on the command line. No game, host, repository or
evidence location is baked in. A matrix PASS against a corpus PARTIAL is a
CONFLICT, not an upgrade. A claimed hash that does not match the bytes on disk
is a CONFLICT. Nothing here builds, launches or edits a game or a document.

Usage:
    python3 -m tools.game_verdict_reconcile \
        --matrix-rows rows.json --corpus-acceptance corpus.json \
        --frame-proof frame-proof.json --oracle-evidence oracle.json \
        --out verdicts.json [--markdown verdicts.md] \
        [--generated-at 2026-01-01T00:00:00Z]

Matrix-rows JSON shape (a bare list is also accepted)::

    {"rows": [{"game": "Cave Story", "status": "PARTIAL",
               "sha256": "<original SHA-256>", "source": "docs/GAME_MATRIX.md"}]}

``--row NAME=STATUS`` adds one advisory doc claim without a hash.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools import acceptance_matrix as oracle_lib

SCHEMA_VERSION = 1
GENERATOR = "tools/game_verdict_reconcile.py"

PASS = "PASS"
PARTIAL = "PARTIAL"
BUILD_ONLY = "BUILD ONLY"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
NOT_TESTED = "NOT TESTED"
CONFLICT = "CONFLICT"
STATUSES = (PASS, PARTIAL, BUILD_ONLY, FAIL, BLOCKED, NOT_TESTED, CONFLICT)

_STATUS_PREFIX = re.compile(
    r"^(PASS|PARTIAL|BUILD\s+ONLY|FAIL|BLOCKED|NOT\s+TESTED|NOT\s+RUN|"
    r"NOT\s+APPLICABLE|UNTESTABLE)\b")
_SHA_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_sha(value: Any) -> str | None:
    """A real SHA-256 in lower case; anything else is None (never guessed)."""
    return oracle_lib.norm_sha(value)


def key_of(name: Any) -> str:
    """Stable case/whitespace-insensitive join key for a game name."""
    return oracle_lib.key_of(name)


def sha256_file(path: Path | None) -> str | None:
    """Recompute a SHA-256 from bytes on disk; None if unreadable."""
    return oracle_lib.sha256_file(path) if path is not None else None


def normalize_status(value: Any) -> str:
    """Map a claimed/recording status to the reconciliation vocabulary."""
    text = re.sub(r"\s+", " ", str(value or "").strip().upper())
    match = _STATUS_PREFIX.match(text)
    if not match:
        return NOT_TESTED
    token = re.sub(r"\s+", " ", match.group(1))
    if token in ("NOT RUN", "NOT APPLICABLE", "UNTESTABLE"):
        return NOT_TESTED
    return token if token in STATUSES else NOT_TESTED


def resolve_path(raw: Any, base_dir: Path) -> Path | None:
    """Resolve a CLI-supplied path; relative paths are relative to the JSON."""
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_absolute() else base_dir / path


def load_json_sources(paths: list[str], kind: str) -> tuple[list[dict], list[dict]]:
    """Read CLI-supplied JSON, hashing each input file for provenance."""
    loaded: list[dict] = []
    problems: list[dict] = []
    for raw in paths:
        path = Path(raw).expanduser()
        entry = {"kind": kind, "path": str(path), "sha256": sha256_file(path),
                 "status": "ok"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, (dict, list)):
                raise ValueError("expected a JSON object or list")
        except (OSError, ValueError, UnicodeError) as exc:
            entry.update(status="INVALID", error=f"{type(exc).__name__}: {exc}")
            problems.append(entry)
            continue
        loaded.append({"data": data, "source": entry})
    return loaded, problems


def _first(mapping: dict, *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_matrix_rows(data: dict | list, source: dict) -> list[dict]:
    """Normalise one matrix-rows document into advisory claim records."""
    if isinstance(data, list):
        raw_rows = data
    elif isinstance(data, dict):
        raw_rows = _first(data, "rows", "games", "matrix", "records") or []
    else:
        raw_rows = []
    if not isinstance(raw_rows, list):
        raw_rows = []
    out: list[dict] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        game = str(_first(raw, "game", "name", "title") or "").strip()
        if not game:
            continue
        row_text = " ".join(str(v) for v in raw.values() if isinstance(v, str))
        claim_path = _first(raw, "evidence_path", "evidence", "file")
        out.append({
            "game": game,
            "status": normalize_status(_first(raw, "status", "verdict",
                                              "working_status")),
            "sha256": norm_sha(_first(raw, "sha256", "sha", "input_sha256")),
            "evidence_path": str(claim_path) if claim_path else None,
            "evidence_sha256": norm_sha(_first(raw, "evidence_sha256")),
            "upstream_claim": bool(raw.get("upstream_claim") or raw.get("claim")
                                   or "†" in row_text or "claim" in str(
                                       raw.get("source", "")).lower()),
            "source_doc": str(_first(raw, "source", "source_doc") or source["path"]),
            "source": source,
        })
    return out


def parse_cli_rows(assignments: list[str], source_kind: str = "matrix-rows") -> list[dict]:
    """Parse ``NAME=STATUS`` assignments into advisory doc claims."""
    source = {"kind": source_kind, "path": "<cli --row>", "sha256": None,
              "status": "ok"}
    rows: list[dict] = []
    for text in assignments:
        if "=" not in text:
            raise ValueError(f"expected NAME=STATUS, got {text!r}")
        name, status = (part.strip() for part in text.split("=", 1))
        if not name or not status:
            raise ValueError(f"expected NAME=STATUS, got {text!r}")
        rows.append({
            "game": name, "status": normalize_status(status), "sha256": None,
            "evidence_path": None, "evidence_sha256": None,
            "upstream_claim": False, "source_doc": source["path"],
            "source": source,
        })
    return rows


def load_matrix_markdown(paths: list[str]) -> tuple[list[dict], list[dict]]:
    """Parse optional Markdown game-matrix docs into advisory claims."""
    rows: list[dict] = []
    problems: list[dict] = []
    for raw in paths:
        path = Path(raw).expanduser()
        source = {"kind": "game-matrix", "path": str(path),
                  "sha256": sha256_file(path), "status": "ok"}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            source.update(status="INVALID", error=f"{type(exc).__name__}: {exc}")
            problems.append(source)
            continue
        for row in oracle_lib.parse_game_matrix(text):
            game = str(row.get("game") or "").strip()
            if not game:
                continue
            rows.append({
                "game": game, "status": normalize_status(row.get("status")),
                "sha256": norm_sha(row.get("sha256")),
                "evidence_path": None, "evidence_sha256": None,
                "upstream_claim": bool(row.get("upstream_claim")),
                "source_doc": str(path), "source": source,
            })
    return rows, problems


def index_corpus(loaded: list[dict]) -> dict[str, list[dict]]:
    """Index corpus acceptance games by name; keep hashes and paths for rechecks."""
    out: dict[str, list[dict]] = {}
    for wrapped in loaded:
        data, source = wrapped["data"], wrapped["source"]
        games = data.get("games") if isinstance(data, dict) else data
        if not isinstance(games, list):
            games = []
        base_dir = Path(source["path"]).parent
        for game in games:
            if not isinstance(game, dict):
                continue
            name = str(_first(game, "game", "name") or "").strip()
            if not name:
                continue
            original = game.get("original") if isinstance(game.get("original"), dict) else {}
            native = game.get("native") if isinstance(game.get("native"), dict) else {}
            pipeline = game.get("pipeline") if isinstance(game.get("pipeline"), dict) else {}
            launch = game.get("launch") if isinstance(game.get("launch"), dict) else {}
            frames = game.get("frames") if isinstance(game.get("frames"), dict) else {}
            scenario = game.get("scenario") if isinstance(game.get("scenario"), dict) else {}
            example = frames.get("example") if isinstance(frames.get("example"), dict) else {}
            cli_evidence = (game.get("cli_evidence")
                            if isinstance(game.get("cli_evidence"), dict) else {})
            out.setdefault(key_of(name), []).append({
                "game": name,
                "status": normalize_status(game.get("status")),
                "input_sha256": norm_sha(original.get("sha256")),
                "input_path": resolve_path(original.get("path"), base_dir),
                "native_sha256": norm_sha(native.get("sha256")),
                "native_path": resolve_path(native.get("path"), base_dir),
                "launched": bool(launch.get("executed")),
                "exit_code": launch.get("exit_code"),
                "verification_status": pipeline.get("verification_status"),
                "stage_failed": pipeline.get("stage_failed"),
                "scenario_declared": bool(scenario.get("declared")),
                "scenario_complete": bool(scenario.get("complete")),
                "cli_reproduced": cli_evidence.get("reproduced"),
                "frame_example": {
                    "path": str(_first(example, "path", "file") or ""),
                    "sha256": norm_sha(example.get("sha256")),
                    "distinct_colors": example.get("distinct_colors"),
                } if example else None,
                "reasons": [str(r) for r in (game.get("reasons") or [])],
                "source": source,
            })
    return out


def index_frame_proofs(loaded: list[dict]) -> dict[str, list[dict]]:
    """Index frame-proof rows by name and recompute every claimed frame hash."""
    out: dict[str, list[dict]] = {}
    for wrapped in loaded:
        data, source = wrapped["data"], wrapped["source"]
        frames = data.get("frames") if isinstance(data, dict) else data
        if not isinstance(frames, list):
            frames = []
        base_dir = Path(source["path"]).parent
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            name = str(_first(frame, "game", "name") or "").strip()
            if not name:
                continue
            path = resolve_path(_first(frame, "path", "file"), base_dir)
            claimed = norm_sha(_first(frame, "sha256", "hash"))
            actual = sha256_file(path)
            colors = frame.get("distinct_colors", frame.get("colours"))
            blank = bool(frame.get("blank"))
            hash_ok = bool(claimed and actual and claimed == actual)
            out.setdefault(key_of(name), []).append({
                "game": name,
                "path": str(path) if path else None,
                "claimed_sha256": claimed,
                "actual_sha256": actual,
                "hash_recomputed": hash_ok,
                "hash_mismatch": bool(claimed and actual and claimed != actual),
                "blank": blank,
                "distinct_colors": colors if isinstance(colors, int) else None,
                "proven": bool(hash_ok and not blank
                               and isinstance(colors, int) and colors > 1),
                "source": source,
            })
    return out


def index_oracles(loaded: list[dict]) -> dict[str, list[dict]]:
    """Validate oracle claims once, using the shared strict validator."""
    out: dict[str, list[dict]] = {}
    for wrapped in loaded:
        data, source = wrapped["data"], wrapped["source"]
        records = (data.get("records", data.get("games", data.get("scenarios", [])))
                   if isinstance(data, dict) else data)
        if not isinstance(records, list):
            continue
        base_dir = Path(source["path"]).parent
        for raw in records:
            if not isinstance(raw, dict):
                continue
            checked = oracle_lib.validate_oracle_record(raw, base_dir=base_dir)
            checked["source"] = source
            if checked["game"]:
                out.setdefault(key_of(checked["game"]), []).append(checked)
    return out


def check_path_hash(path: Path | None, claimed: str | None, label: str,
                    reasons: list[str], conflicts: list[str]) -> None:
    """Recompute one claimed hash; a mismatch is a conflict, a missing file is
    only an unverifiable claim (fail closed, but not fabricated evidence)."""
    if not claimed or path is None:
        return
    actual = sha256_file(path)
    if actual and actual != claimed:
        conflicts.append(f"{label} SHA-256 {claimed[:16]}... does not match "
                         f"recomputed {actual[:16]}... from {path}")
    elif actual is None:
        reasons.append(f"{label} SHA-256 cannot be recomputed: {path} is missing")


def summarize_frames(frames: list[dict]) -> dict:
    return {
        "count": len(frames),
        "proven": sum(1 for f in frames if f["proven"]),
        "blank": sum(1 for f in frames if f["blank"]),
        "hash_mismatch": sum(1 for f in frames if f["hash_mismatch"]),
        "frames": [{k: f[k] for k in ("path", "claimed_sha256", "actual_sha256",
                                     "hash_recomputed", "blank", "distinct_colors",
                                     "proven")} for f in frames],
    }


def reconcile(game: str, *, matrix: list[dict], corpus: list[dict],
              frames: list[dict], oracles: list[dict]) -> dict:
    """Join one game's evidence into a verdict, checks and explicit reasons."""
    reasons: list[str] = []
    conflicts: list[str] = []

    if len(matrix) > 1:
        conflicts.append("game-matrix supplies more than one row for this game")
    if len(corpus) > 1:
        conflicts.append("corpus acceptance supplies more than one game record")
    m = matrix[0] if matrix else None
    c = corpus[0] if corpus else None

    if m and c and m["status"] != c["status"]:
        conflicts.append(f"verdict conflict: game-matrix claims {m['status']} "
                         f"while corpus acceptance records {c['status']}")
    if m and c and m["sha256"] and c["input_sha256"] and \
            m["sha256"] != c["input_sha256"]:
        conflicts.append("input SHA-256 differs between the game-matrix row and "
                         "the corpus record")

    if m:
        if m["upstream_claim"]:
            reasons.append("game-matrix row is an upstream doc claim; advisory only")
        if m["evidence_path"]:
            # ``evidence_sha256`` is the hash of the evidence file; a bare
            # ``sha256`` is the original input claim, cross-checked against the
            # corpus below (or against the evidence file when no corpus exists).
            claimed_evidence = m["evidence_sha256"] or (m["sha256"] if c is None else None)
            check_path_hash(
                resolve_path(m["evidence_path"], Path(m["source"]["path"]).parent),
                claimed_evidence, "game-matrix evidence", reasons, conflicts)
    if c:
        check_path_hash(c["input_path"], c["input_sha256"], "corpus original",
                        reasons, conflicts)
        check_path_hash(c["native_path"], c["native_sha256"], "corpus native",
                        reasons, conflicts)

    frame_summary = summarize_frames(frames)
    for frame in frames:
        if frame["hash_mismatch"]:
            conflicts.append(f"frame {frame['path']} claims SHA-256 "
                             f"{frame['claimed_sha256'][:16]}... but recomputed "
                             f"{frame['actual_sha256'][:16]}...")
    gui_mode = not (c and c["scenario_declared"] and c["scenario_complete"])
    if c and c["frame_example"] and frames and gui_mode and \
            not frame_summary["proven"] and c["status"] in (PASS, PARTIAL):
        conflicts.append("corpus claims frame-based evidence but frame-proof has "
                         "no proven, non-blank frame with a matching hash")
    if c and not c["launched"] and len(oracles) == 1 and oracles[0]["valid"]:
        conflicts.append("validated oracle claims PASS but corpus records no launch")

    oracle_rows = []
    for oracle in oracles:
        if oracle["input_sha256"] and c and c["input_sha256"] and \
                oracle["input_sha256"] != c["input_sha256"]:
            conflicts.append("oracle input SHA-256 does not match the corpus record")
        if oracle["native_sha256"] and c and c["native_sha256"] and \
                oracle["native_sha256"] != c["native_sha256"]:
            conflicts.append("oracle native SHA-256 does not match the corpus record")
        if oracle["actual_evidence_sha256"] and oracle["evidence_sha256"] and \
                oracle["actual_evidence_sha256"] != oracle["evidence_sha256"]:
            conflicts.append("oracle evidence SHA-256 does not match the recomputed hash")
        if oracle["valid"] and c and c["status"] in (FAIL, BLOCKED, BUILD_ONLY):
            conflicts.append(f"validated oracle claims PASS but corpus status is "
                             f"{c['status']}")
        if not oracle["valid"]:
            reasons.extend("PASS withheld: " + text for text in oracle["reasons"])
        oracle_rows.append({
            "oracle": oracle["oracle"], "valid": oracle["valid"],
            "verified": oracle["checks"]["has_real_oracle_flag"],
            "scenario": oracle["scenario"],
            "evidence_path": oracle["evidence_path"],
            "evidence_sha256": oracle["evidence_sha256"],
            "actual_evidence_sha256": oracle["actual_evidence_sha256"],
            "source": oracle["source"]["path"],
        })

    if len(oracles) > 1:
        conflicts.append("more than one oracle claim matches this game name")

    # The doc claim is never evidence: a bare matrix PASS cannot even establish
    # PARTIAL without a corpus record.
    if c is None:
        status = NOT_TESTED
        reasons.append("no corpus acceptance record; game-matrix claims are not evidence")
    elif c["status"] == PASS:
        status = PARTIAL
        reasons.append("corpus PASS is not credited without a validated oracle")
    else:
        status = c["status"]
    if c and c["stage_failed"]:
        reasons.append(f"pipeline stage {c['stage_failed']!s} failed")
    if m and m["status"] != NOT_TESTED:
        reasons.append(f"game-matrix says {m['status']} (advisory doc claim only)")
    if frames and not frame_summary["proven"]:
        reasons.append("no proven non-blank frame in frame-proof evidence")

    frame_requirement = frame_summary["proven"] > 0
    if not gui_mode:
        frame_requirement = bool(c and c["scenario_complete"]
                                 and c["cli_reproduced"] is not False)
    oracle = oracles[0] if len(oracles) == 1 else None
    hashes_match = bool(
        oracle and c and c["input_sha256"] and c["native_sha256"]
        and oracle["input_sha256"] == c["input_sha256"]
        and oracle["native_sha256"] == c["native_sha256"]
    )
    pass_eligible = bool(
        not conflicts and c and c["launched"] and c["status"] in (PASS, PARTIAL)
        and oracle and oracle["valid"] and hashes_match
        and frame_requirement
        and not (c["scenario_declared"] and not c["scenario_complete"])
    )
    if pass_eligible:
        status = PASS
        reasons = [
            f"validated original-Windows oracle {oracles[0]['oracle']!r} for "
            f"scenario {oracles[0]['scenario']!r}",
            f"oracle evidence {oracles[0]['evidence_path']} SHA-256 recomputed and matched",
            "matrix rows are advisory doc claims; PASS comes from oracle evidence only",
        ]
    if conflicts:
        status = CONFLICT
        reasons = conflicts + reasons

    if not reasons:
        reasons.append("no evidence supplied for this game")
    return {
        "game": game,
        "key": key_of(game),
        "verdict": status,
        "reasons": reasons,
        "pass_eligible": pass_eligible,
        "claims": {
            "matrix": m["status"] if m else None,
            "matrix_upstream_claim": bool(m and m["upstream_claim"]),
            "matrix_source": m["source_doc"] if m else None,
            "corpus": c["status"] if c else None,
            "frame_proof": "PROVEN" if frame_summary["proven"] else
                           ("NO PROVEN FRAME" if frames else None),
        },
        "hashes": {
            "matrix_input": m["sha256"] if m else None,
            "corpus_input": c["input_sha256"] if c else None,
            "corpus_native": c["native_sha256"] if c else None,
            "recomputed_and_agreed": not any("SHA-256" in r or "hash" in r
                                             for r in conflicts),
        },
        "frames": frame_summary,
        "launched": bool(c and c["launched"]),
        "scenario_declared": bool(c and c["scenario_declared"]),
        "oracles": oracle_rows,
        "sources": [s["source"] for s in (*matrix, *corpus)],
        "conflicts": conflicts,
    }


def build_report(matrix: list[dict], corpus: dict[str, list[dict]],
                 frames: dict[str, list[dict]], oracles: dict[str, list[dict]],
                 *, generated_at: str, source_paths: list[dict]) -> dict:
    """Join all supplied evidence into a deterministic, sorted report."""
    names = set(key_of(m["game"]) for m in matrix)
    names.update(corpus)
    names.update(frames)
    names.update(oracles)
    rows = []
    for key in names:
        label = (next((m["game"] for m in matrix if key_of(m["game"]) == key), None)
                 or next((r["game"] for r in corpus.get(key, [])), None)
                 or next((r["game"] for r in frames.get(key, [])), None)
                 or next((r["game"] for r in oracles.get(key, [])), None) or key)
        rows.append(reconcile(
            label,
            matrix=[m for m in matrix if key_of(m["game"]) == key],
            corpus=corpus.get(key, []), frames=frames.get(key, []),
            oracles=oracles.get(key, [])))
    rows.sort(key=lambda r: (r["key"], r["game"], r["verdict"]))
    counts = {status: sum(1 for row in rows if row["verdict"] == status)
              for status in STATUSES}
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at,
        "policy": {
            "matrix_rows_are_doc_claims": True,
            "doc_claims_never_upgrade_a_verdict": True,
            "pass_requires_validated_oracle": True,
            "hashes_recomputed_from_supplied_paths": True,
            "contradictions_are_conflicts": True,
        },
        "inputs": sorted(source_paths, key=lambda s: (s["kind"], s["path"])),
        "rows": rows,
        "summary": {"total": len(rows), "by_verdict": counts},
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Game verdict reconciliation",
        "",
        f"Generated: {report['generated_at']}  ",
        f"Schema: {report['schema']}  ",
        "Game-matrix rows are advisory doc claims. PASS requires validated "
        "oracle evidence; contradictory verdicts or hashes are CONFLICT.",
        "",
        "| Game | Verdict | Matrix | Corpus | Frame proof | Reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["rows"]:
        claims = row["claims"]
        reason = (row["reasons"][0] if row["reasons"] else "-").replace("|", "\\|")
        lines.append(
            f"| {(row['game'] or '-').replace('|', chr(92) + '|')} "
            f"| {row['verdict']} | {claims['matrix'] or '-'} "
            f"| {claims['corpus'] or '-'} | {claims['frame_proof'] or '-'} "
            f"| {reason} |")
    counts = report["summary"]["by_verdict"]
    lines += ["", "Summary: " + "; ".join(f"{k}: {v}" for k, v in counts.items()) + ".",
              "No game was launched, built or modified by this reconciler.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--matrix-rows", action="append", default=[], metavar="JSON",
                    help="JSON file of game-matrix rows (repeatable)")
    ap.add_argument("--row", action="append", default=[], metavar="NAME=STATUS",
                    help="add one advisory doc-claim row (repeatable)")
    ap.add_argument("--game-matrix", "--matrix-markdown", action="append", default=[],
                    metavar="MD", help="optional Markdown game-matrix doc (repeatable)")
    ap.add_argument("--corpus-acceptance", action="append", default=[], metavar="JSON",
                    help="tools.corpus_acceptance JSON report (repeatable)")
    ap.add_argument("--frame-proof", action="append", default=[], metavar="JSON",
                    help="tools.frame_proof JSON report (repeatable)")
    ap.add_argument("--oracle-evidence", action="append", default=[], metavar="JSON",
                    help="JSON with explicit PASS claims; the only PASS source (repeatable)")
    ap.add_argument("--out", required=True, type=Path, help="write the JSON report here")
    ap.add_argument("--markdown", type=Path, help="optionally write a Markdown report here")
    ap.add_argument("--generated-at", default=None,
                    help="ISO UTC timestamp override for byte-deterministic output")
    ap.add_argument("--fail-on-conflict", action="store_true",
                    help="exit 1 when any row is CONFLICT (default: always exit 0)")
    args = ap.parse_args(argv)

    if not any((args.matrix_rows, args.row, args.game_matrix,
                args.corpus_acceptance, args.frame_proof, args.oracle_evidence)):
        ap.error("supply at least one evidence input")

    matrix: list[dict] = []
    problems: list[dict] = []
    inputs: list[dict] = []
    loaded_rows, row_problems = load_json_sources(args.matrix_rows, "matrix-rows")
    problems.extend(row_problems)
    for wrapped in loaded_rows:
        matrix.extend(parse_matrix_rows(wrapped["data"], wrapped["source"]))
        inputs.append(wrapped["source"])
    try:
        matrix.extend(parse_cli_rows(args.row))
    except ValueError as exc:
        print(f"argument error: {exc}", file=sys.stderr)
        return 2
    doc_rows, doc_problems = load_matrix_markdown(args.game_matrix)
    matrix.extend(doc_rows)
    problems.extend(doc_problems)

    loaded_corpus, corpus_problems = load_json_sources(args.corpus_acceptance,
                                                       "corpus-acceptance")
    loaded_frames, frame_problems = load_json_sources(args.frame_proof, "frame-proof")
    loaded_oracles, oracle_problems = load_json_sources(args.oracle_evidence,
                                                        "oracle-evidence")
    problems.extend((*corpus_problems, *frame_problems, *oracle_problems))
    for source in (loaded_corpus, loaded_frames, loaded_oracles):
        inputs.extend(wrapped["source"] for wrapped in source)
    for raw in args.game_matrix:
        path = str(Path(raw).expanduser())
        if not any(entry["path"] == path for entry in (*inputs, *problems)):
            inputs.append({"kind": "game-matrix", "path": path,
                           "sha256": sha256_file(Path(raw).expanduser()),
                           "status": "ok"})

    report = build_report(
        matrix,
        index_corpus(loaded_corpus),
        index_frame_proofs(loaded_frames),
        index_oracles(loaded_oracles),
        generated_at=args.generated_at or utc_now(),
        source_paths=[*inputs, *problems],
    )
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report), encoding="utf-8")
    counts = report["summary"]["by_verdict"]
    print("verdicts: " + "; ".join(f"{k}: {v}" for k, v in counts.items()))
    print(f"report: {args.out}")
    if args.fail_on_conflict and counts[CONFLICT]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
