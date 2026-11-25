#!/usr/bin/env python3
"""Honest, deterministic corpus-capacity accounting for issue #130.

This tool reads ONLY the report paths supplied on the command line. It has no
built-in corpus locations, never runs a game, and never fabricates a PASS.
Every number is derived from the bytes of the supplied files, and the exact
SHA-256 of each source is recorded so a later edit is detectable.

A row counts as a distinct game candidate only when it has a well-formed
SHA-256, is not a synthetic fixture, and is not a DLL-only binary. A PASS is
counted as verified only when the row carries an explicit ``verified`` flag,
a 64-hex ``evidence_sha256`` and a non-empty ``oracle``. A bare ``PASS``
status is refused (it is recorded in ``refused_pass_rows``), so build-only or
static-only evidence can never be promoted to a verified game.

Usage (from the repository root)::

    python3 -m tools.corpus_capacity_final \
        --inventory reports/local-games-inventory.json \
        --static reports/pe32-1000-static.json \
        --results reports/b1_results_snapshot.jsonl \
        --target 10000 --out proof/corpus-capacity/audit.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SCHEMA = 1
GENERATOR = "tools/corpus_capacity_final.py"
MISSING_SHA = "<missing-sha256>"
SYNTHETIC_PATH_MARKERS = ("tests/fixtures",)
NO_ORACLE = {
    "", "none", "no oracle", "no-oracle", "null", "n/a", "na", "unknown",
    "unspecified", "dummy", "not supplied", "windows oracle",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def display_path(path: Path) -> str:
    """Record a repo-relative path when possible; never a machine-specific one."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


def read_rows(path: Path, fmt: str) -> tuple[bytes, list[dict[str, Any]]]:
    """Read a CLI-supplied report; fail closed on unreadable or malformed data."""
    raw = path.read_bytes()
    if fmt == "jsonl":
        rows = []
        for lineno, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{lineno}: expected a JSON object")
            rows.append(row)
        return raw, rows
    doc = json.loads(raw.decode("utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        raise ValueError(f"{path}: expected an object with a rows[] list")
    rows = []
    for row in doc["rows"]:
        if not isinstance(row, dict):
            raise ValueError(f"{path}: every rows[] element must be an object")
        rows.append(row)
    return raw, rows


def norm_status(row: dict[str, Any]) -> str:
    return str(row.get("status") or "").strip().upper() or "UNSPECIFIED"


def dedup_key(row: dict[str, Any]) -> str:
    value = row.get("sha256")
    if isinstance(value, str) and HEX64.match(value.strip().lower()):
        return value.strip().lower()
    return MISSING_SHA


def is_synthetic(row: dict[str, Any]) -> bool:
    """Synthetic fixtures are never counted as real game candidates."""
    for field in ("synthetic", "is_synthetic", "fixture", "is_fixture"):
        if row.get(field) is True:
            return True
    path = str(row.get("path") or "").replace("\\", "/").lower()
    return any(marker in path for marker in SYNTHETIC_PATH_MARKERS)


def is_dll(row: dict[str, Any]) -> bool:
    return str(row.get("kind") or "").strip().lower() == "dll"


def is_verified_pass(row: dict[str, Any]) -> bool:
    """Fail-closed PASS gate: a status string alone is never evidence."""
    if norm_status(row) != "PASS":
        return False
    if row.get("verified") is not True:
        return False
    digest = row.get("evidence_sha256")
    if not isinstance(digest, str) or not HEX64.match(digest.strip().lower()):
        return False
    oracle = row.get("oracle")
    if not isinstance(oracle, str) or oracle.strip().lower() in NO_ORACLE:
        return False
    return True


def source_stats(role: str, supplied_path: str, raw: bytes,
                 rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = [dedup_key(row) for row in rows]
    verified = {key for row, key in zip(rows, keys) if is_verified_pass(row)}
    return {
        "role": role,
        "path": supplied_path,
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
        "rows": len(rows),
        "unique_dedup_keys": len(set(keys)),
        "unique_sha256": len(set(keys) - {MISSING_SHA}),
        "rows_missing_sha256": keys.count(MISSING_SHA),
        "in_scope_rows": sum(1 for row in rows if row.get("in_scope") is True),
        "synthetic_fixture_rows": sum(1 for row in rows if is_synthetic(row)),
        "kind_counts": dict(sorted(Counter(
            str(row.get("kind") or "unspecified").strip().lower()
            for row in rows).items())),
        "status_counts": dict(sorted(Counter(norm_status(row) for row in rows).items())),
        "pass_rows": sum(1 for row in rows if norm_status(row) == "PASS"),
        "verified_pass_rows": sum(1 for row in rows if is_verified_pass(row)),
        "refused_pass_rows": sum(
            1 for row in rows
            if norm_status(row) == "PASS" and not is_verified_pass(row)),
        "verified_pass_keys": sorted(verified),
    }


def build_audit(sources: list[tuple[str, str, Path, str]],
                target: int) -> dict[str, Any]:
    """Build the audit document from (role, supplied path, path, format) tuples."""
    seen: set[tuple[str, str]] = set()
    for role, supplied, _path, _fmt in sources:
        if (role, supplied) in seen:
            raise ValueError(f"duplicate source path: {supplied}")
        seen.add((role, supplied))

    stats: list[dict[str, Any]] = []
    tagged: list[tuple[dict[str, Any], str]] = []
    for role, supplied, path, fmt in sources:
        raw, rows = read_rows(path, fmt)
        stats.append(source_stats(role, display_path(path), raw, rows))
        tagged.extend((row, role) for row in rows)

    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row, _role in tagged:
        by_key[dedup_key(row)].append(row)

    union_keys = set(by_key)
    actual_keys = union_keys - {MISSING_SHA}
    # Conservative: a binary labelled DLL anywhere is not a standalone game.
    dll_labeled_keys = {
        key for key, rows in by_key.items()
        if key != MISSING_SHA
        and any(is_dll(row) for row in rows if not is_synthetic(row))
    }
    eligible_keys = {
        key for key, rows in by_key.items()
        if key != MISSING_SHA
        and any(not is_synthetic(row) for row in rows)
        and key not in dll_labeled_keys
    }
    verified_keys = {
        dedup_key(row) for row, _role in tagged if is_verified_pass(row)
    }

    status_counts = dict(sorted(Counter(
        norm_status(row) for row, _role in tagged).items()))
    upper_bound = len(eligible_keys)
    verified = len(verified_keys & actual_keys)
    target = int(target)
    if target < 0:
        raise ValueError("target must be >= 0")
    capacity_ok = upper_bound >= target and verified >= target
    shortfall = max(0, target - upper_bound)
    verified_shortfall = max(0, target - verified)

    dedup = {
        "dedup_key": "lowercased 64-hex sha256, or <missing-sha256> when absent",
        "total_rows_supplied": len(tagged),
        "union_dedup_keys": len(union_keys),
        "union_actual_sha256": len(actual_keys),
        "duplicate_rows_eliminated": len(tagged) - len(union_keys),
        "rows_with_missing_sha256": sum(1 for row, _r in tagged
                                        if dedup_key(row) == MISSING_SHA),
        "synthetic_fixture_rows_excluded": sum(1 for row, _r in tagged
                                                if is_synthetic(row)),
        "dll_labeled_sha256_excluded": len(dll_labeled_keys),
        "executable_game_candidate_sha256": upper_bound,
        "note": ("Candidate keys exclude synthetic fixtures, rows without a "
                 "SHA-256, and any binary labelled DLL. STATIC_OK is a PE "
                 "parse result, not a build or a launch."),
    }
    verification = {
        "status_counts": status_counts,
        "pass_rows": sum(1 for row, _r in tagged if norm_status(row) == "PASS"),
        "verified_pass_distinct_sha256": verified,
        "refused_pass_rows": sum(
            1 for row, _r in tagged
            if norm_status(row) == "PASS" and not is_verified_pass(row)),
        "no_fake_pass": True,
        "pass_requires": ["verified is true", "64-hex evidence_sha256",
                          "non-empty oracle"],
        "note": ("BUILD-ONLY is a compile result, BLOCKED means no build, and "
                 "STATIC_OK is a static PE parse. None establishes a running, "
                 "verified game."),
    }
    capacity = {
        "target_distinct_real_windows_games": target,
        "status": "CAPACITY_SUFFICIENT" if capacity_ok else "CAPACITY_LIMIT",
        "upper_bound_distinct_real_windows_games": upper_bound,
        "shortfall_to_upper_bound": shortfall,
        "verified_distinct_real_windows_games": verified,
        "shortfall_to_verified_target": verified_shortfall,
        "blocker": None if capacity_ok else "LEGAL_CORPUS_ABSENT",
        "reason": (
            "A legal corpus of 10,000 distinct real Windows games is not "
            "present; the supplied reports cap the claim at "
            f"{upper_bound} executable candidates with {verified} verified "
            "games. Issue #130 stays OPEN until such a corpus with "
            "fail-closed per-game evidence is supplied."
            if not capacity_ok else
            "Supplied reports meet the requested target."
        ),
    }
    inputs_digest = sha256_bytes("\n".join(
        f"{item['role']}:{item['path']}:{item['sha256']}" for item in stats
    ).encode("utf-8"))
    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "target_distinct_real_windows_games": target,
        "inputs_digest": inputs_digest,
        "sources": stats,
        "dedup": dedup,
        "verification": verification,
        "capacity": capacity,
        "issue_130": "OPEN",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inventory", action="append", default=[], type=Path,
                    metavar="PATH", help="local-games-inventory JSON (repeatable)")
    ap.add_argument("--static", action="append", default=[], type=Path,
                    metavar="PATH", help="PE32 static sweep JSON (repeatable)")
    ap.add_argument("--results", action="append", default=[], type=Path,
                    metavar="PATH", help="build/results JSONL (repeatable)")
    ap.add_argument("--target", type=int, default=10000,
                    help="required distinct real Windows games (default: 10000)")
    ap.add_argument("--out", required=True, type=Path, help="output JSON path")
    args = ap.parse_args(argv)

    if not (args.inventory or args.static or args.results):
        ap.error("supply at least one of --inventory, --static, --results")

    sources: list[tuple[str, str, Path, str]] = []
    for role, paths, fmt in (
        ("local-games-inventory", args.inventory, "json"),
        ("pe32-1000-static", args.static, "json"),
        ("b1-results-snapshot", args.results, "jsonl"),
    ):
        for path in paths:
            sources.append((role, str(path), path, fmt))

    try:
        doc = build_audit(sources, args.target)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    cap = doc["capacity"]
    print(json.dumps({
        "out": str(out),
        "status": cap["status"],
        "upper_bound": cap["upper_bound_distinct_real_windows_games"],
        "verified": cap["verified_distinct_real_windows_games"],
        "shortfall": cap["shortfall_to_upper_bound"],
        "issue_130": doc["issue_130"],
    }), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
