#!/usr/bin/env python3
"""Deterministic report of proven console scenarios versus untested games.

This CLI reads per-game oracle records from a JSON file supplied on the command
line and emits a stable, machine-readable report. It never launches a game,
contacts a host, edits a record or touches an issue tracker: it only reads bytes
and recomputes SHA-256 digests. There are no built-in record or evidence
locations -- every path comes from the CLI (``--records``, ``--evidence-root``).

A record is placed in ``proven_console_scenarios`` only when *all* of the
following hold:

* it claims ``PROVEN`` and its ``kind`` is ``console``,
* it names a real, executed Windows-oracle (an allow-listed name),
* it explicitly asserts ``matched: true``,
* its evidence file exists and its claimed SHA-256 matches the digest
  recomputed from the file's bytes, and
* an optional ``claimed_stdout_sha256`` (when present) matches that same
  recomputed digest.

Anything else is deny-by-default: a bad digest, a missing file, a fabricated oracle,
a contradictory flag or an unknown claim produces a ``rejected_claims`` entry
with a reason and never a ``PROVEN`` verdict. Records that claim ``PROVEN`` but
describe a GUI or gameplay scenario are reported under ``out_of_scope`` -- they
are not console proofs. Records that claim ``UNTESTED`` are listed without a
verdict; no verdict is inferred from a build, a launch or a captured frame.

The report always carries explicit ``limits`` stating that the proven console
scenarios do not generalize to GUI titles or gameplay. Output is byte-stable
for identical inputs (sorted keys and sorted lists).

Usage::

    python3 -m tools.oracle_corpus_report \\
        --records <records.json> [--evidence-root <dir>] \\
        [--label <label>] [--out <report.json>]

Exit codes: 0 = clean report, 1 = fabricated/tampered claim (FAIL),
2 = required input unavailable (BLOCKED). FAIL outranks BLOCKED.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/oracle_corpus_report.py"

# Allow-lists mirror tools.oracle_scenario_check / tools.acceptance_matrix. Inlined
# so this reporter stays a pure JSON/hash reader with no runner imports.
REAL_ORACLES = frozenset({
    "original-windows", "original-windows-process", "windows-oracle",
    "windows-original", "windows-process", "original-process",
    "original-vs-native", "original-game", "retail-windows", "retail-original",
})
FAKE_ORACLES = frozenset({
    "", "none", "null", "n/a", "na", "unknown", "unspecified", "not-supplied",
    "dummy", "dummy-device", "dummy-device-audio", "headless", "sdl-dummy",
    "stub", "fake", "fake-success", "emulator", "unit-test", "selftest",
    "windows-oracle-unexecuted", "no-oracle",
})

KINDS = ("console", "gui", "gameplay")
CLAIMS = ("PROVEN", "UNTESTED")
SEVERITY_RANK = {"OK": 0, "INFO": 0, "BLOCKED": 1, "FAIL": 2}
EXIT_BY_STATUS = {"OK": 0, "FAIL": 1, "BLOCKED": 2}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

LIMITS = (
    "Only console scenarios whose evidence bytes were recomputed and matched "
    "are marked PROVEN; every other game is UNTESTED or explicitly rejected.",
    "The proven console scenarios do not generalize to GUI games or gameplay: "
    "no graphics, frame, input or audio equivalence is claimed here.",
    "A build, a process launch or a captured frame alone never yields a PROVEN "
    "verdict without a matching, recomputed evidence hash.",
    "This tool reads bytes and computes hashes only; it runs no game, contacts "
    "no host and makes no tracker mutations.",
)

CATEGORIES = ("proven_console_scenarios", "untested_games", "rejected_claims",
              "out_of_scope")


def sha256_file(path: Path) -> str:
    """Exact SHA-256 of a real file; raise OSError when unreadable (never guess)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm_sha(value: Any) -> str | None:
    """Canonical lowercase 64-hex digest, or None when the value is not one."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if SHA256_RE.fullmatch(text) else None


def norm_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def load_records(path: Path) -> tuple[dict | None, str | None]:
    """Load the CLI-supplied records document; deny by default on bad input."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return None, "not a JSON object"
    return data, None


def evidence_path(root: Path, raw: Any) -> Path | None:
    """Map a CLI-supplied evidence path to a filesystem path (no built-ins).

    Relative paths are read under ``--evidence-root`` when supplied, otherwise
    under the records file's own directory. ``..`` traversal is rejected.
    """
    text = norm_text(raw)
    if not text:
        return None
    candidate = Path(text)
    if ".." in candidate.parts:
        return None
    return candidate if candidate.is_absolute() else root / candidate


def _reason(code: str, severity: str, game: str, message: str) -> dict:
    return {"code": code, "severity": severity, "game": game, "message": message}


def _worst(reasons: list[dict]) -> str:
    worst = "OK"
    for reason in reasons:
        if SEVERITY_RANK[reason["severity"]] > SEVERITY_RANK[worst]:
            worst = reason["severity"]
    return worst


def _rejected(game: str, scenario: str, claim: str, reasons: list[dict]) -> dict:
    return {"game": game, "scenario": scenario, "claim": claim,
            "verdict": "REJECTED", "severity": _worst(reasons),
            "reasons": [r["code"] for r in reasons]}


def classify_record(raw: Any, *, index: int, root: Path,
                    seen: set[str]) -> tuple[str, dict | None, list[dict]]:
    """Classify one record. Returns (category, entry, reasons).

    ``category`` is one of :data:`CATEGORIES`. A rejected record never carries
    a PROVEN verdict, regardless of what the record itself asserts.
    """
    reasons: list[dict] = []

    def add(code: str, severity: str, message: str) -> None:
        reasons.append(_reason(code, severity, label, message))

    if not isinstance(raw, dict):
        label = f"record[{index}]"
        add("INVALID_RECORD", "FAIL", "record is not a JSON object")
        return "rejected_claims", _rejected(label, "", "", reasons), reasons

    game = norm_text(raw.get("game"))
    label = game or f"record[{index}]"
    scenario = norm_text(raw.get("scenario"))
    claim = norm_text(raw.get("claim")).upper()
    kind = norm_text(raw.get("kind")).lower()

    if not game:
        add("GAME_MISSING", "FAIL", "record has no game name")
    elif game in seen:
        add("DUPLICATE_GAME", "FAIL", f"game {game!r} appears more than once")
    else:
        seen.add(game)

    if not scenario:
        add("SCENARIO_MISSING", "BLOCKED", f"{label}: no scenario name")
    if claim not in CLAIMS:
        add("CLAIM_INVALID", "FAIL",
            f"{label}: claim {claim or '<missing>'!r} is not PROVEN or UNTESTED")
        return "rejected_claims", _rejected(label, scenario, claim, reasons), reasons

    if claim == "UNTESTED":
        if raw.get("matched") is True:
            add("CONTRADICTORY_UNTESTED", "FAIL",
                f"{label}: UNTESTED record asserts matched=true")
            return "rejected_claims", _rejected(label, scenario, claim, reasons), reasons
        if kind and kind not in KINDS:
            add("KIND_UNKNOWN", "BLOCKED", f"{label}: unknown kind {kind!r}")
        entry = {"game": game, "scenario": scenario, "kind": kind or None,
                 "verdict": "UNTESTED", "reason": "no matching evidence supplied"}
        return "untested_games", entry, reasons

    # ---- claim == PROVEN ---------------------------------------------------
    if kind not in KINDS:
        add("KIND_UNKNOWN", "FAIL",
            f"{label}: PROVEN claim has unknown kind {kind or '<missing>'!r}")
        return "rejected_claims", _rejected(label, scenario, claim, reasons), reasons

    if kind != "console":
        # Scope limiting: a GUI/gameplay scenario is never a console proof.
        entry = {"game": game, "scenario": scenario, "kind": kind,
                 "claim": "PROVEN", "verdict": "OUT_OF_SCOPE",
                 "reason": "not a console scenario; no GUI/gameplay verdict issued"}
        add("OUT_OF_SCOPE_KIND", "INFO",
            f"{label}: {kind} scenario excluded from console proofs")
        return "out_of_scope", entry, reasons

    oracle = norm_text(raw.get("oracle")).lower()
    if not oracle:
        add("ORACLE_UNNAMED", "BLOCKED", f"{label}: no named oracle")
    elif oracle in FAKE_ORACLES or oracle not in REAL_ORACLES:
        add("ORACLE_NOT_REAL", "FAIL",
            f"{label}: oracle {oracle!r} is not a real executed oracle")

    matched = raw.get("matched")
    if matched is None:
        add("MATCH_NOT_ASSERTED", "BLOCKED", f"{label}: no matched flag")
    elif matched is not True:
        add("MATCH_FALSE", "FAIL", f"{label}: matched is {matched!r}, not true")

    evidence = raw.get("evidence")
    if not isinstance(evidence, dict):
        add("EVIDENCE_MISSING", "BLOCKED", f"{label}: no evidence object")
        return "rejected_claims", _rejected(label, scenario, claim, reasons), reasons

    claimed_sha_raw = evidence.get("sha256")
    claimed_sha = norm_sha(claimed_sha_raw)
    if claimed_sha_raw is None:
        add("EVIDENCE_HASH_MISSING", "BLOCKED", f"{label}: no evidence SHA-256")
    elif claimed_sha is None:
        add("FABRICATED_HASH", "FAIL",
            f"{label}: evidence SHA-256 is not 64 lowercase hex characters")

    target = evidence_path(root, evidence.get("path"))
    if target is None:
        add("EVIDENCE_PATH_UNSAFE", "FAIL",
            f"{label}: evidence path is missing or traverses upward")
    actual_sha: str | None = None
    try:
        if target is not None:
            actual_sha = sha256_file(target)
    except OSError:
        add("EVIDENCE_FILE_MISSING", "BLOCKED",
            f"{label}: evidence file is not readable: {target}")

    if actual_sha is not None and claimed_sha is not None and actual_sha != claimed_sha:
        add("HASH_MISMATCH", "FAIL",
            f"{label}: claimed {claimed_sha} != recomputed {actual_sha}")

    stdout_raw = raw.get("claimed_stdout_sha256")
    if stdout_raw is not None:
        stdout_sha = norm_sha(stdout_raw)
        if stdout_sha is None:
            add("FABRICATED_STDOUT_HASH", "FAIL",
                f"{label}: claimed_stdout_sha256 is not 64 hex characters")
        elif actual_sha is not None and stdout_sha != actual_sha:
            add("STDOUT_HASH_MISMATCH", "FAIL",
                f"{label}: claimed_stdout_sha256 {stdout_sha} != "
                f"recomputed {actual_sha}")

    if _worst(reasons) != "OK":
        return "rejected_claims", _rejected(label, scenario, claim, reasons), reasons

    entry = {
        "game": game,
        "scenario": scenario,
        "kind": kind,
        "oracle": oracle,
        "claim": "PROVEN",
        "verdict": "PROVEN (console scenario only)",
        "matched": True,
        "evidence_path": str(target),
        "claimed_sha256": claimed_sha,
        "recomputed_sha256": actual_sha,
        "claimed_stdout_sha256": norm_sha(stdout_raw) if stdout_raw is not None else None,
        "scope": "console scenario only; no GUI or gameplay equivalence",
    }
    return "proven_console_scenarios", entry, reasons


def build_report(records_path: Path, *, root: Path | None = None,
                 label: str | None = None) -> dict:
    """Build the deterministic report from a CLI-supplied records document."""
    records_path = Path(records_path)
    evidence_root = Path(root) if root is not None else records_path.parent
    doc, error = load_records(records_path)
    report: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "source": str(records_path),
        "evidence_root": str(evidence_root),
        "status": "OK",
        "counts": {key: 0 for key in CATEGORIES},
        "proven_console_scenarios": [],
        "untested_games": [],
        "rejected_claims": [],
        "out_of_scope": [],
        "limits": list(LIMITS),
        "no_issue_actions": True,
        "read_only": True,
    }
    if label is not None:
        report["label"] = label

    all_reasons: list[dict] = []
    if error is not None:
        all_reasons.append(_reason("RECORDS_UNREADABLE", "BLOCKED", "",
                                   f"records file: {error}"))
        records: list[Any] = []
    else:
        raw_records = doc.get("records")
        if not isinstance(raw_records, list):
            all_reasons.append(_reason("NO_RECORDS", "BLOCKED", "",
                                       "records file has no 'records' list"))
            records = []
        else:
            records = raw_records

    # A game may not appear twice with different claims; deny by default for all of
    # its records so no contradictory PROVEN entry survives.
    gameplay_names: dict[str, int] = {}
    for row in records:
        if isinstance(row, dict):
            name = norm_text(row.get("game"))
            if name:
                gameplay_names[name] = gameplay_names.get(name, 0) + 1
    duplicates = {name for name, count in gameplay_names.items() if count > 1}

    seen: set[str] = set()
    for index, row in enumerate(records):
        game = norm_text(row.get("game")) if isinstance(row, dict) else ""
        if game in duplicates:
            reason = _reason("DUPLICATE_GAME", "FAIL", game,
                             f"{game!r} has conflicting records; all rejected")
            all_reasons.append(reason)
            raw = row
            claim = norm_text(raw.get("claim")).upper() if isinstance(raw, dict) else ""
            report["rejected_claims"].append(
                _rejected(game or f"record[{index}]",
                          norm_text(raw.get("scenario")) if isinstance(raw, dict) else "",
                          claim, [reason]))
            continue
        category, entry, reasons = classify_record(
            row, index=index, root=evidence_root, seen=seen)
        all_reasons.extend(reasons)
        if entry is not None:
            report[category].append(entry)

    for category in CATEGORIES:
        report[category].sort(key=lambda item: (str(item.get("game", "")),
                                                str(item.get("scenario", ""))))
        report["counts"][category] = len(report[category])

    status = "OK"
    for reason in all_reasons:
        if SEVERITY_RANK[reason["severity"]] > SEVERITY_RANK[status]:
            status = reason["severity"]
    report["status"] = status if status != "INFO" else "OK"
    report["exit_code"] = EXIT_BY_STATUS[report["status"]]
    report["reasons"] = all_reasons
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", required=True, type=Path,
                    help="CLI-supplied JSON file of per-game oracle records")
    ap.add_argument("--evidence-root", type=Path, default=None,
                    help="base directory for relative evidence paths "
                         "(default: the records file's directory)")
    ap.add_argument("--label", default=None, help="optional provenance label")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the machine-readable report JSON here")
    args = ap.parse_args(argv)

    report = build_report(args.records, root=args.evidence_root, label=args.label)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    for reason in report["reasons"]:
        if reason["severity"] != "INFO":
            print(f"{reason['severity']}: {reason['message']}", file=sys.stderr)
    print(f"{report['status']}: proven console scenarios="
          f"{report['counts']['proven_console_scenarios']}, untested games="
          f"{report['counts']['untested_games']}, rejected="
          f"{report['counts']['rejected_claims']}, out_of_scope="
          f"{report['counts']['out_of_scope']}")
    for limit in report["limits"]:
        print(f"limit: {limit}")
    if args.out is not None:
        print(f"report: {args.out}")
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
