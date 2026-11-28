#!/usr/bin/env python3
"""Generate a fail-closed, machine-readable acceptance matrix from CLI evidence.

This tool joins evidence that *other* tools already recorded; it never runs a
game, compiles a binary, edits a document or guesses a result. Every input path
is supplied on the command line -- no repository, game, corpus or evidence
location is built into this module.

Inputs (all optional individually, at least one required):

* ``--corpus-acceptance`` : JSON written by :mod:`tools.corpus_acceptance`
  (``games[].status``, ``original.sha256``, ``native.sha256``,
  ``pipeline.native_sha256``, ``launch.executed``, frames, scenario).
* ``--api-coverage`` : JSON written by :mod:`tools.api_coverage` (``inputs``
  keyed by original input SHA-256). Registration-only: it can withhold PASS,
  never create one.
* ``--game-matrix`` : Markdown game-matrix document(s). Markdown tables are
  parsed for measured statuses, SHA-256 values and the Agent 3 addendum. Rows
  marked ``†`` (upstream claims) are recorded as NOT TESTED, never as evidence.
* ``--oracle-evidence`` : JSON with explicit PASS claims. This is the only input
  that can produce PASS, and only when *every* gate below is met for one row.

A row becomes PASS only when all of these hold at once:

1. ``oracle_verified`` is the boolean ``true`` and ``oracle`` names a real
   original-Windows process oracle (a dummy device, headless SDL context,
   unexecuted "windows oracle" label or unknown string is rejected);
2. claimed original **and** native SHA-256 values are real 64-hex digests and
   match the corpus record (and each other across all supplied sources);
3. a named scenario plus non-empty scenario evidence are supplied;
4. an existing evidence file has the claimed SHA-256, recomputed here;
5. the corpus record shows an evidenced launch, not FAIL/BLOCKED;
6. no game-matrix row conflicts with the claim;
7. no matching API-coverage input reports unresolved system imports.

Everything else is capped at PARTIAL / BUILD ONLY / FAIL / BLOCKED / NOT TESTED
with an explicit reason. In particular, a corpus ``PASS`` status, a game-matrix
``PASS`` cell, a bare exit code, a non-blank frame and a fabricated hash are
*not* PASS on their own.

Usage:
    python3 -m tools.acceptance_matrix \
        --corpus-acceptance <corpus.json> \
        --api-coverage <coverage.json> \
        --game-matrix <GAME_MATRIX.md> \
        --oracle-evidence <oracle.json> \
        --out <acceptance-matrix.json> [--markdown <matrix.md>] \
        [--generated-at 2026-01-01T00:00:00Z]

Oracle evidence JSON shape (one record per row)::

    {"records": [{
       "game": "Cave Story",
       "sha256": "<original input SHA-256>",
       "native_sha256": "<rebuilt binary SHA-256>",
       "oracle": "original-windows",
       "oracle_verified": true,
       "scenario": "boot-to-menu",
       "scenario_evidence": "original and native menus match at frame 12",
       "evidence_path": "relative-to-this-json-or-absolute/file.json",
       "evidence_sha256": "<SHA-256 of that file>"
    }]}

``generated_at`` defaults to the current UTC time; supply it explicitly for
byte-for-byte deterministic output in tests and CI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/acceptance_matrix.py"
STATUSES = ("PASS", "PARTIAL", "BUILD ONLY", "FAIL", "BLOCKED", "NOT TESTED")

# Only an explicit, executed original-Windows process can satisfy the oracle
# gate. Unknown spellings are rejected: an empty allow-list entry is safer than
# accepting a vague "verified" string.
REAL_ORACLES = frozenset({
    "original-windows", "original-windows-process", "windows-oracle",
    "windows-original", "windows-process", "original-process",
    "original-vs-native", "original-game", "retail-windows",
    "retail-original",
})
NO_ORACLES = frozenset({
    "", "none", "null", "n/a", "na", "unknown", "unspecified", "not-supplied",
    "dummy", "dummy-device", "dummy-device-audio", "headless", "sdl-dummy",
    "stub", "fake", "fake-success", "emulator", "unit-test", "selftest",
    "windows-oracle-unexecuted", "no-oracle",
})

DOC_STATUS_RE = re.compile(
    r"^\s*(PASS|PARTIAL|BUILD\s+ONLY|FAIL|BLOCKED|NOT\s+TESTED|NOT\s+RUN|"
    r"NOT\s+APPLICABLE|UNTESTABLE)\b", re.IGNORECASE)
SHA_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_sha256(value: Any) -> bool:
    """True only for a 64-character hexadecimal digest string."""
    return isinstance(value, str) and bool(SHA_RE.fullmatch(value.strip()))


def norm_sha(value: Any) -> str | None:
    """Normalise a real SHA-256 to lower case; anything else becomes None."""
    return value.strip().lower() if is_sha256(value) else None


def find_sha(value: Any) -> str | None:
    """Return the first 64-hex digest inside a table row, or None."""
    match = SHA_RE.search(str(value or ""))
    return match.group(0).lower() if match else None


def key_of(name: Any) -> str:
    """Stable lookup key for a game name (no case/whitespace surprises)."""
    return " ".join(str(name or "").replace("†", "").split()).lower()


def sha256_file(path: Path) -> str | None:
    """Exact SHA-256 of a file, or None when unreadable (never guessed)."""
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def clean_cell(text: Any) -> str:
    """Strip Markdown decoration from a table cell without changing its claim."""
    value = str(text or "").strip()
    value = MD_LINK_RE.sub(r"\1", value)
    return value.replace("`", "").strip()


def load_sources(paths: list[str], kind: str) -> tuple[list[dict], list[dict]]:
    """Read CLI-supplied JSON objects, recording invalid inputs fail-closed."""
    loaded: list[dict] = []
    problems: list[dict] = []
    for raw in paths:
        path = Path(raw)
        entry = {"kind": kind, "path": str(path), "sha256": sha256_file(path),
                 "status": "ok"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
        except (OSError, ValueError, UnicodeError) as exc:
            entry.update(status="INVALID", error=f"{type(exc).__name__}: {exc}")
            problems.append(entry)
            continue
        loaded.append({"data": data, "source": entry})
    return loaded, problems


# ---------------------------------------------------------------------------
# Evidence indexing
# ---------------------------------------------------------------------------

def index_corpus(reports: list[dict]) -> list[dict]:
    """Flatten corpus acceptance reports into comparable, hash-keyed records."""
    rows: list[dict] = []
    for wrapped in reports:
        data, source = wrapped["data"], wrapped["source"]
        games = data.get("games")
        if not isinstance(games, list):
            continue
        for game in games:
            if not isinstance(game, dict):
                continue
            original = game.get("original") if isinstance(game.get("original"), dict) else {}
            native = game.get("native") if isinstance(game.get("native"), dict) else {}
            pipeline = game.get("pipeline") if isinstance(game.get("pipeline"), dict) else {}
            launch = game.get("launch") if isinstance(game.get("launch"), dict) else {}
            frames = game.get("frames") if isinstance(game.get("frames"), dict) else {}
            scenario = game.get("scenario") if isinstance(game.get("scenario"), dict) else {}
            input_sha = norm_sha(original.get("sha256"))
            native_sha = norm_sha(native.get("sha256"))
            pipeline_sha = norm_sha(pipeline.get("native_sha256"))
            status = str(game.get("status") or "NOT TESTED").strip().upper()
            if status not in STATUSES:
                status = "NOT TESTED"
            example = frames.get("example") if isinstance(frames.get("example"), dict) else {}
            rows.append({
                "game": str(game.get("game") or ""),
                "status": status,
                "reasons": [str(r) for r in (game.get("reasons") or [])],
                "input_sha256": input_sha,
                "native_sha256": native_sha,
                "pipeline_native_sha256": pipeline_sha,
                "native_hashes_agree": native_sha is None or pipeline_sha is None
                                       or native_sha == pipeline_sha,
                "launched": bool(launch.get("executed")),
                "exit_code": launch.get("exit_code"),
                "nonblank_frame": bool(example) and bool(norm_sha(example.get("sha256")))
                                  and isinstance(example.get("distinct_colors"), int)
                                  and example["distinct_colors"] > 1,
                # An input hash alone is identity, not a rebuilt binary.
                "build_evidence": bool(native_sha or pipeline_sha
                                       or original.get("pe_type")),
                "pipeline_status": str(pipeline.get("verification_status") or ""),
                "stage_failed": pipeline.get("stage_failed"),
                "scenario_declared": bool(scenario.get("declared")),
                "scenario_complete": bool(scenario.get("complete")),
                "cli_evidence": game.get("cli_evidence")
                                if isinstance(game.get("cli_evidence"), dict) else {},
                "source": source,
            })
    return rows


def index_coverage(reports: list[dict]) -> dict[str, dict]:
    """Index API coverage by input SHA-256. A hash with no entry has no coverage."""
    out: dict[str, dict] = {}
    for wrapped in reports:
        data, source = wrapped["data"], wrapped["source"]
        inputs = data.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, entry in inputs.items():
            digest = norm_sha(key)
            if digest is None and isinstance(entry, dict):
                digest = norm_sha(entry.get("input_sha256"))
            if digest is None:
                continue
            entry = entry if isinstance(entry, dict) else {}
            missing = entry.get("missing") if isinstance(entry.get("missing"), dict) else {}
            missing_names = sorted(
                f"{dll}:{name}"
                for dll, names in missing.items() for name in (names or [])
            )
            out[digest] = {
                "input_sha256": digest,
                "missing_system_imports": len(missing_names),
                "missing_import_names": missing_names,
                "source": source,
                "registration_only": True,
            }
    return out


def canonical_doc_status(text: str) -> str | None:
    """Map a Markdown status cell to the matrix vocabulary, fail-closed."""
    match = DOC_STATUS_RE.match(clean_cell(text))
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip().upper()
    if value in ("NOT RUN", "NOT APPLICABLE", "UNTESTABLE"):
        return "NOT TESTED"
    return value if value in STATUSES else "NOT TESTED"


def parse_game_matrix(text: str) -> list[dict]:
    """Parse Markdown tables from a game-matrix doc into advisory doc rows.

    The document is prose plus tables; only table cells are read. Section
    headings decide whether rows are measured or upstream claims. Doc rows are
    advisory evidence: they can withhold PASS but can never grant it.
    """
    rows: list[dict] = []
    section = ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("##"):
            section = stripped.lstrip("#").strip()
            continue
        if not stripped.startswith("|") or index + 1 >= len(lines):
            continue
        separator = lines[index + 1].strip()
        if not (separator.startswith("|") and re.fullmatch(r"[|:\-\s]+", separator)):
            continue
        headers = [clean_cell(cell).lower() for cell in stripped.strip("|").split("|")]
        is_claim_section = "claim" in section.lower()
        is_addendum = "addendum" in section.lower() or "native build" in headers
        name_col = next((i for i, h in enumerate(headers) if h in ("name", "game")), None)
        status_col = next((i for i, h in enumerate(headers)
                           if h in ("working status", "status")), None)
        sha_col = next((i for i, h in enumerate(headers)
                        if h.replace(" ", "-") in ("sha-256", "sha256")), None)
        cursor = index + 2
        while cursor < len(lines) and lines[cursor].strip().startswith("|"):
            cells = [clean_cell(cell) for cell in lines[cursor].strip().strip("|").split("|")]
            cursor += 1
            if not any(cells):
                continue
            raw_status = cells[status_col] if status_col is not None and status_col < len(cells) else ""
            name = cells[name_col] if name_col is not None and name_col < len(cells) else ""
            raw_sha = cells[sha_col] if sha_col is not None and sha_col < len(cells) else ""
            row_text = " | ".join(cells)
            sha = norm_sha(raw_sha) or find_sha(row_text)
            is_claim = is_claim_section or "†" in row_text
            status = canonical_doc_status(raw_status)
            if status is None and is_addendum:
                # Evidence/blocker prose can contain the word BLOCKED; it is not
                # an aspect verdict. Only acceptance columns count here.
                ignored = ("evidence", "blocker", "sha-256", "sha256", "executable",
                           "hash", "source", "commit", "arch", "os", "name", "game",
                           "status", "port details", "#")
                aspects = {
                    headers[i]: cells[i] for i in range(min(len(headers), len(cells)))
                    if i != name_col and not any(token in headers[i] for token in ignored)
                }
                values = [canonical_doc_status(v) or "NOT TESTED" for v in aspects.values()]
                if "FAIL" in values:
                    status = "FAIL"
                elif "BLOCKED" in values:
                    status = "BLOCKED"
                elif "PARTIAL" in values or "PASS" in values:
                    status = "PARTIAL"  # a partial addendum row is never doc PASS
                else:
                    status = "NOT TESTED"
            elif status is None:
                status = "NOT TESTED"
            rows.append({
                "game": name,
                "status": status,
                "section": section,
                "sha256": sha,
                "upstream_claim": is_claim,
                "raw": row_text,
                "evidence": cells[-1] if cells else "",
                "source": None,  # filled by load_docs
            })
    return rows


def load_docs(paths: list[str]) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    failures: list[dict] = []
    for raw in paths:
        path = Path(raw)
        source = {"kind": "game-matrix", "path": str(path),
                  "sha256": sha256_file(path), "status": "ok"}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            source.update(status="INVALID", error=f"{type(exc).__name__}: {exc}")
            failures.append(source)
            continue
        doc_rows = parse_game_matrix(text)
        for row in doc_rows:
            row["source"] = source
        rows.extend(doc_rows)
    return rows, failures


def normalize_oracle(value: Any) -> str:
    token = str(value or "").strip().lower()
    token = re.sub(r"[\s_]+", "-", token)
    return re.sub(r"-{2,}", "-", token).strip("-")


def validate_oracle_record(record: dict, *, base_dir: Path) -> dict:
    """Validate one PASS claim structurally, including the evidence file hash.

    This function never sees a game row: even a perfectly formed claim is only
    a *candidate* until its hashes and game identity match recorded evidence.
    """
    game = str(record.get("game") or "").strip()
    input_sha = norm_sha(record.get("input_sha256") or record.get("sha256"))
    native_sha = norm_sha(record.get("native_sha256"))
    oracle = normalize_oracle(record.get("oracle"))
    scenario = str(record.get("scenario") or "").strip()
    scenario_evidence = str(record.get("scenario_evidence") or "").strip()
    evidence_raw = str(record.get("evidence_path") or "").strip()
    claimed_sha = norm_sha(record.get("evidence_sha256"))
    evidence_path = (base_dir / evidence_raw) if evidence_raw and not Path(evidence_raw).is_absolute() else Path(evidence_raw)
    actual_sha = sha256_file(evidence_path) if evidence_raw else None

    checks = {
        "has_game_name": bool(game),
        "has_real_oracle_flag": record.get("oracle_verified") is True,
        "has_real_oracle_name": oracle in REAL_ORACLES and oracle not in NO_ORACLES,
        "has_input_sha256": input_sha is not None,
        "has_native_sha256": native_sha is not None,
        "has_scenario": bool(scenario),
        "has_scenario_evidence": bool(scenario_evidence),
        "has_evidence_file": bool(evidence_raw) and actual_sha is not None,
        "evidence_hash_matches": bool(claimed_sha and actual_sha and claimed_sha == actual_sha),
    }
    reasons = []
    if not checks["has_real_oracle_flag"]:
        reasons.append("oracle_verified is not the boolean true (no executed oracle flag)")
    if not checks["has_real_oracle_name"]:
        reasons.append(f"oracle {oracle!r} is not an accepted original-Windows oracle")
    if not checks["has_input_sha256"]:
        reasons.append("oracle claim has no valid original input SHA-256")
    if not checks["has_native_sha256"]:
        reasons.append("oracle claim has no valid native binary SHA-256")
    if not checks["has_scenario"]:
        reasons.append("oracle claim has no named scenario")
    if not checks["has_scenario_evidence"]:
        reasons.append("oracle claim has no scenario evidence text")
    if not checks["has_evidence_file"]:
        reasons.append(f"scenario evidence file is missing: {evidence_raw or '(none)'}")
    if not checks["evidence_hash_matches"]:
        reasons.append("scenario evidence SHA-256 is absent or does not match the file")
    if not checks["has_game_name"]:
        reasons.append("oracle claim has no game name")

    return {
        "valid": all(checks.values()),
        "reasons": reasons,
        "checks": checks,
        "game": game,
        "input_sha256": input_sha,
        "native_sha256": native_sha,
        "oracle": oracle,
        "scenario": scenario,
        "scenario_evidence": scenario_evidence,
        "evidence_path": str(evidence_path) if evidence_raw else "",
        "evidence_sha256": claimed_sha,
        "actual_evidence_sha256": actual_sha,
    }


def index_oracles(reports: list[dict]) -> tuple[list[dict], list[dict]]:
    out: list[dict] = []
    failures: list[dict] = []
    for wrapped in reports:
        data, source = wrapped["data"], wrapped["source"]
        records = data.get("records", data.get("games", data.get("scenarios", [])))
        if not isinstance(records, list):
            failures.append({"kind": "oracle-evidence", "path": source["path"],
                             "status": "INVALID", "error": "expected a records list"})
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            validated = validate_oracle_record(record, base_dir=Path(source["path"]).parent)
            validated["source"] = source
            out.append(validated)
    return out, failures


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def base_status(corpus: dict | None, doc: dict | None, *, hash_conflict: bool,
                ambiguous: bool) -> str:
    """Evidence-derived status before considering any PASS claim."""
    if hash_conflict:
        return "FAIL"
    if doc and doc["upstream_claim"] and corpus is None:
        return "NOT TESTED"  # quoted upstream prose is never this product's result
    statuses = [c["status"] for c in (corpus, doc) if c]
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if "FAIL" in statuses:
        return "FAIL"
    if ambiguous:
        return "PARTIAL"
    if (corpus and corpus["status"] == "BUILD ONLY") or (doc and doc["status"] == "BUILD ONLY"):
        return "BUILD ONLY"
    if "PARTIAL" in statuses:
        return "PARTIAL"
    if "PASS" in statuses:
        return "PARTIAL"  # a bare PASS claim without oracle validation
    if corpus and corpus["build_evidence"] and not corpus["launched"]:
        return "BUILD ONLY"
    if corpus and corpus["launched"]:
        return "PARTIAL"
    return "NOT TESTED"


def classify_row(corpus: dict | None, doc: dict | None, coverage: dict | None,
                 oracle: dict | None, *, ambiguous_doc: bool = False) -> dict:
    """Join one row's evidence into a status, checks and explicit reasons."""
    reasons: list[str] = []
    name = (corpus or doc or oracle or {}).get("game", "")
    corpus_input = corpus["input_sha256"] if corpus else None
    doc_input = doc["sha256"] if doc else None
    oracle_input = oracle["input_sha256"] if oracle else None
    coverage_input = coverage["input_sha256"] if coverage else None
    observed = {"corpus": corpus_input, "game-matrix": doc_input,
                "oracle": oracle_input, "api-coverage": coverage_input}
    present = {kind: sha for kind, sha in observed.items() if sha}
    hash_conflict = len(set(present.values())) > 1
    input_sha = corpus_input or doc_input or oracle_input or coverage_input

    corpus_native = corpus["native_sha256"] if corpus else None
    native_match = bool(corpus_native and oracle and corpus_native == oracle["native_sha256"])
    input_match = bool(corpus_input and oracle and corpus_input == oracle_input)
    native_hashes_agree = corpus["native_hashes_agree"] if corpus else True
    missing_imports = bool(coverage and coverage["missing_system_imports"])

    checks = {
        "has_corpus_record": corpus is not None,
        "has_matrix_record": doc is not None,
        "has_api_coverage_record": coverage is not None,
        "has_oracle_record": oracle is not None,
        "has_input_sha256": input_sha is not None,
        "hashes_agree_across_sources": not hash_conflict,
        "native_hashes_agree": native_hashes_agree,
        "launch_evidenced": bool(corpus and corpus["launched"]),
        "upstream_claim_only": bool(doc and doc["upstream_claim"] and corpus is None),
        "ambiguous_matrix_match": ambiguous_doc,
        "no_unresolved_system_imports": not missing_imports,
        "has_real_oracle_flag": bool(oracle and oracle["checks"]["has_real_oracle_flag"]),
        "has_real_oracle_name": bool(oracle and oracle["checks"]["has_real_oracle_name"]),
        "has_scenario_evidence": bool(oracle and oracle["checks"]["has_scenario_evidence"]
                                      and oracle["checks"]["has_evidence_file"]
                                      and oracle["checks"]["evidence_hash_matches"]),
        "oracle_input_matches_corpus": input_match,
        "oracle_native_matches_corpus": native_match,
    }
    if oracle:
        for key in ("has_game_name", "has_real_oracle_flag", "has_real_oracle_name",
                    "has_input_sha256", "has_native_sha256", "has_scenario",
                    "has_scenario_evidence", "has_evidence_file", "evidence_hash_matches"):
            checks[key] = oracle["checks"][key]
    else:
        checks.update({key: False for key in (
            "has_game_name", "has_input_sha256", "has_native_sha256", "has_scenario",
            "has_evidence_file", "evidence_hash_matches")})

    if hash_conflict:
        reasons.append("PASS withheld: input SHA-256 conflicts across sources: "
                       + "; ".join(f"{k}={v}" for k, v in sorted(present.items())))
    if not native_hashes_agree:
        reasons.append("PASS withheld: native SHA-256 differs between native and pipeline records")
    if ambiguous_doc:
        reasons.append("PASS withheld: game-matrix row matches more than one corpus input")
    if missing_imports:
        reasons.append(f"PASS withheld: {coverage['missing_system_imports']} unresolved "
                       "system imports in API coverage (registration-only audit)")
    if checks["upstream_claim_only"]:
        reasons.append("upstream claim (†) quoted from another project; not measured here")
    if not corpus:
        reasons.append("no corpus acceptance record for this game")
    if not oracle:
        reasons.append("no oracle evidence record supplied; PASS is not available")
    else:
        reasons.extend("PASS withheld: " + text for text in oracle["reasons"])
        if not input_match:
            reasons.append("PASS withheld: oracle input SHA-256 does not match the corpus input")
        if not native_match:
            reasons.append("PASS withheld: oracle native SHA-256 does not match the corpus binary")
        if oracle["game"] and key_of(oracle["game"]) != key_of(name):
            reasons.append("PASS withheld: oracle record names a different game")
    if corpus and corpus["status"] in ("FAIL", "BLOCKED", "BUILD ONLY", "NOT TESTED"):
        reasons.append(f"corpus acceptance status is {corpus['status']}; no evidenced launch to promote")
    if doc and doc["status"] in ("FAIL", "BLOCKED", "PARTIAL", "NOT TESTED"):
        reasons.append(f"game-matrix status is {doc['status']}; conflicting evidence")
    if corpus and corpus["scenario_declared"] and not corpus["scenario_complete"]:
        reasons.append("corpus scenario record is incomplete")

    status = base_status(corpus, doc, hash_conflict=hash_conflict, ambiguous=ambiguous_doc)
    if missing_imports and status == "PASS":
        status = "PARTIAL"
    if not reasons:
        reasons.append("no evidence supplied for this row")

    pass_gate = (
        oracle is not None and oracle["valid"] and input_match and native_match
        and not hash_conflict and native_hashes_agree and not ambiguous_doc
        and not missing_imports and not checks["upstream_claim_only"]
        and corpus is not None and corpus["launched"]
        and corpus["status"] in ("PASS", "PARTIAL")
        and not (corpus["scenario_declared"] and not corpus["scenario_complete"])
        and (doc is None or doc["status"] == "PASS")
        and all(checks[key] for key in (
            "has_real_oracle_flag", "has_real_oracle_name", "has_scenario",
            "has_scenario_evidence", "has_evidence_file", "evidence_hash_matches"))
        and key_of(oracle["game"]) == key_of(name)
    )
    if pass_gate:
        status = "PASS"
        reasons = [
            f"validated original-Windows oracle {oracle['oracle']!r} for scenario "
            f"{oracle['scenario']!r}",
            f"input SHA-256 {input_sha} and native SHA-256 {corpus_native} match the corpus record",
            f"scenario evidence {oracle['evidence_path']} SHA-256 {oracle['evidence_sha256']} recomputed and matched",
        ]
    checks["pass_eligible"] = status == "PASS"

    hashes = {
        "input_sha256": input_sha,
        "native_sha256": corpus_native,
        "evidence_sha256": oracle["evidence_sha256"] if oracle else None,
        "actual_evidence_sha256": oracle["actual_evidence_sha256"] if oracle else None,
        "by_source": {k: v for k, v in sorted(present.items())},
        "agree": not hash_conflict and (not oracle or (input_match and native_match)),
    }
    return {
        "game": name,
        "status": status,
        "reasons": reasons,
        "checks": checks,
        "hashes": hashes,
        "scenario": ({"name": oracle["scenario"], "evidence": oracle["scenario_evidence"]}
                     if oracle else None),
        "oracle": ({"name": oracle["oracle"], "verified": oracle["checks"]["has_real_oracle_flag"],
                    "evidence_path": oracle["evidence_path"], "valid": oracle["valid"],
                    "source": oracle["source"]["path"]} if oracle else None),
        "corpus_status": corpus["status"] if corpus else None,
        "matrix_status": doc["status"] if doc else None,
        "matrix_section": doc["section"] if doc else None,
        "api_coverage": ({"missing_system_imports": coverage["missing_system_imports"],
                          "missing_import_names": coverage["missing_import_names"],
                          "registration_only": True, "source": coverage["source"]["path"]}
                         if coverage else None),
        "sources": [c["source"] for c in (corpus, doc) if c]
                   + ([coverage["source"]] if coverage else [])
                   + ([oracle["source"]] if oracle else []),
    }


# ---------------------------------------------------------------------------
# Matrix assembly and CLI
# ---------------------------------------------------------------------------

def build_matrix(corpus: list[dict], docs: list[dict], coverage: dict[str, dict],
                 oracles: list[dict], *, generated_at: str,
                 source_paths: list[dict]) -> dict:
    """Join all supplied evidence into a deterministic machine-readable matrix."""
    doc_by_sha: dict[str, list[dict]] = {}
    doc_by_name: dict[str, list[dict]] = {}
    for doc_id, doc in enumerate(docs):
        doc["_id"] = doc_id
        if doc["sha256"]:
            doc_by_sha.setdefault(doc["sha256"], []).append(doc)
        doc_by_name.setdefault(key_of(doc["game"]), []).append(doc)

    used_docs: set[int] = set()
    used_oracles: set[int] = set()
    rows: list[dict] = []

    def add_row(corpus_row: dict | None, doc_row: dict | None,
                oracle_row: dict | None, ambiguous: bool) -> None:
        lookup_sha = (corpus_row["input_sha256"] if corpus_row else
                      (doc_row.get("sha256") if doc_row else None)) or ""
        cov = coverage.get(lookup_sha)
        row = classify_row(corpus_row, doc_row, cov, oracle_row, ambiguous_doc=ambiguous)
        key_parts = [key_of(row["game"]), row["hashes"]["input_sha256"] or ""]
        row["key"] = "|".join(key_parts)
        rows.append(row)
        if doc_row is not None:
            used_docs.add(doc_row["_id"])
        if oracle_row is not None:
            used_oracles.add(id(oracle_row))

    for corpus_row in sorted(corpus, key=lambda r: (key_of(r["game"]), r["game"],
                                                     r["input_sha256"] or "")):
        sha_docs = doc_by_sha.get(corpus_row["input_sha256"] or "", [])
        name_docs = [d for d in doc_by_name.get(key_of(corpus_row["game"]), [])
                     if d not in sha_docs]
        matching_docs = sha_docs + name_docs
        ambiguous = len(matching_docs) > 1
        selected_doc = matching_docs[0] if matching_docs else None

        # Match by game name only: a claim with a fabricated hash must stay on
        # the row so the hash mismatch is recorded, not silently dropped.
        candidates = [o for o in oracles
                      if key_of(o["game"]) == key_of(corpus_row["game"])]
        if len(candidates) > 1:
            candidates = []
        selected_oracle = candidates[0] if candidates else None
        add_row(corpus_row, selected_doc, selected_oracle, ambiguous)

    # Matrix rows with no corpus record still deserve an honest NOT TESTED row.
    seen_doc_rows: set[tuple[str, str, str, str]] = set()
    for doc in docs:
        if doc["_id"] in used_docs:
            continue
        identity = (key_of(doc["game"]), doc["section"], doc["status"], doc["raw"])
        if identity in seen_doc_rows:
            continue
        seen_doc_rows.add(identity)
        add_row(None, doc, None, False)

    # An oracle claim with no recorded evidence cannot establish anything.
    for oracle in oracles:
        if id(oracle) in used_oracles:
            continue
        add_row(None, None, oracle, False)

    rows.sort(key=lambda r: (key_of(r["game"]), r["key"], r["status"]))
    counts = {status: sum(1 for row in rows if row["status"] == status) for status in STATUSES}
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at,
        "policy": {
            "pass_requires": ["executed original-windows oracle flag",
                              "matching original and native SHA-256 values across sources",
                              "named scenario with evidenced file and matching hash"],
            "sources_are_evidence_only": ["corpus-acceptance", "api-coverage", "game-matrix"],
            "api_coverage_is_registration_only": True,
            "upstream_claims_never_count": True,
        },
        "inputs": sorted(source_paths, key=lambda s: (s["kind"], s["path"])),
        "rows": rows,
        "summary": {"total": len(rows), "by_status": counts},
    }


def render_markdown(matrix: dict) -> str:
    lines = [
        "# Acceptance matrix",
        "",
        f"Generated: {matrix['generated_at']}  ",
        f"Schema: {matrix['schema']}  ",
        "PASS requires an executed original-Windows oracle, matching hashes and "
        "scenario evidence. Registration-only coverage and quoted upstream rows "
        "can never establish PASS.",
        "",
        "| Game | Status | Input SHA-256 | Scenario | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in matrix["rows"]:
        scenario = row["scenario"]["name"] if row["scenario"] else "-"
        reason = row["reasons"][0].replace("|", "\\|") if row["reasons"] else "-"
        lines.append("| {game} | {status} | {sha} | {scenario} | {reason} |".format(
            game=str(row["game"]).replace("|", "\\|") or "-",
            status=row["status"], sha=row["hashes"]["input_sha256"] or "-",
            scenario=scenario.replace("|", "\\|"), reason=reason))
    counts = matrix["summary"]["by_status"]
    lines += ["", "Summary: " + "; ".join(f"{k}: {v}" for k, v in counts.items()) + ".", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus-acceptance", action="append", default=[], metavar="JSON",
                    help="corpus_acceptance JSON report (repeatable)")
    ap.add_argument("--api-coverage", action="append", default=[], metavar="JSON",
                    help="api_coverage JSON report (repeatable)")
    ap.add_argument("--game-matrix", action="append", default=[], metavar="MD",
                    help="game matrix Markdown document (repeatable)")
    ap.add_argument("--oracle-evidence", action="append", default=[], metavar="JSON",
                    help="JSON with explicit oracle PASS claims (repeatable)")
    ap.add_argument("--out", required=True, type=Path, help="write the JSON matrix here")
    ap.add_argument("--markdown", type=Path, help="optionally write a Markdown summary here")
    ap.add_argument("--generated-at", default=None,
                    help="ISO UTC timestamp override for deterministic output")
    args = ap.parse_args(argv)

    if not any((args.corpus_acceptance, args.api_coverage, args.game_matrix,
                args.oracle_evidence)):
        ap.error("supply at least one evidence input")

    inputs: list[dict] = []
    problems: list[dict] = []
    loaded: dict[str, list[dict]] = {}
    for kind, paths in (("corpus-acceptance", args.corpus_acceptance),
                        ("api-coverage", args.api_coverage),
                        ("oracle-evidence", args.oracle_evidence)):
        loaded[kind], failures = load_sources(paths, kind)
        problems.extend(failures)
        inputs.extend(entry["source"] for entry in loaded[kind])

    docs, doc_failures = load_docs(args.game_matrix)
    problems.extend(doc_failures)
    # One provenance entry per supplied document, not per parsed table row.
    # An unreadable document is already recorded as INVALID in problems.
    for raw in args.game_matrix:
        path = Path(raw)
        if any(p["path"] == str(path) for p in (*inputs, *problems)):
            continue
        inputs.append({"kind": "game-matrix", "path": str(path),
                       "sha256": sha256_file(path), "status": "ok"})

    corpus = index_corpus(loaded["corpus-acceptance"])
    coverage = index_coverage(loaded["api-coverage"])
    oracles, oracle_failures = index_oracles(loaded["oracle-evidence"])
    problems.extend(oracle_failures)

    matrix = build_matrix(
        corpus, docs, coverage, oracles,
        generated_at=args.generated_at or utc_now(),
        source_paths=inputs,
    )
    if problems:
        matrix["inputs"].extend(problems)
        matrix["inputs"].sort(key=lambda s: (s["kind"], s["path"]))

    payload = json.dumps(matrix, indent=2, sort_keys=True) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(matrix), encoding="utf-8")
    counts = matrix["summary"]["by_status"]
    print("summary: " + "; ".join(f"{k}: {v}" for k, v in counts.items()))
    print(f"matrix: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
