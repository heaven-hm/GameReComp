#!/usr/bin/env python3
"""Deterministic compiler for CLI-supplied evidence docs and JSON reports.

This tool reads only files named on the command line (``--doc`` Markdown
evidence documents and ``--report`` JSON reports) and writes one verification
index. It never opens a repository file by its own initiative, never runs a
binary, and never invents an artifact. Every artifact identity in the index is
re-derived by hashing the bytes at the path supplied by the input document.

Recognised claim syntax
-----------------------

Markdown documents may declare a claim in either of two forms:

  * a directive line ``CLAIM <path> <64-hex-sha256> [note]`` (an optional
    ``- ``, ``* ``, ``+ `` or ``> `` list/quote marker is allowed); or
  * a GFM table whose header has a *path* column (``Path``, ``Artifact``,
    ``File``, ``Binary``, ``Source``, ``Frame``, ``Evidence``) and a *hash*
    column (``SHA-256``, ``SHA256``, ``Hash``, ``Digest``). Cells may be wrapped
    in backticks.

A 64-hex value found in a document but not bound to a path in the same line is
reported as NOT TESTED rather than silently ignored.

JSON reports are walked recursively. Any object containing a path key
(``path``, ``artifact_path``, ``evidence_path``, ``binary_path``,
``source_path``, ``frame_path``, ``output_path``, ``report_path``) and/or a hash
key (``sha256``, ``sha-256``, ``hash``, ``digest``, ``evidence_sha256``,
``binary_sha256``) becomes one claim. JSON Lines reports (one object per line)
are accepted as well.

Verdicts
--------

``MATCH`` re-derives the supplied digest and is recorded as RE-DERIVED. A
claimed digest that disagrees with the bytes is TAMPERED with both digests in
the reason. A missing, unreadable or unpaired claim is NOT TESTED with a
reason. The index itself is TAMPERED if any row is TAMPERED, else NOT TESTED if
any row is NOT TESTED, else PARTIAL when at least one row was re-derived, else
NOT TESTED when no claims were supplied. PASS is a permanent zero count: the
compiler does not promote a source-declared verdict into a pass.

Relative artifact paths are interpreted against the directory of the *supplying*
document, never against the process working directory or a repository path.
Output contains no clock, host or environment values, so identical arguments
and identical bytes always produce a byte-identical index.

Usage:
    python3 -m tools.proof_doc_compile \
        --doc evidence.md [--doc more.md] \
        --report report.json [--report more.json] \
        --out index.md [--json index.json] [--label LABEL] [--strict]

Exit codes: 0 = index written; 1 = --strict with TAMPERED rows;
2 = CLI usage error (no inputs, unwritable output).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/proof_doc_compile.py"

TAMPERED = "TAMPERED"
NOT_TESTED = "NOT TESTED"
PARTIAL = "PARTIAL"
PASS = "PASS"
RE_DERIVED = "RE-DERIVED"
# PASS is a documented, permanently empty slot: it is never assigned.
VERDICTS = (TAMPERED, NOT_TESTED, PARTIAL, PASS)

MATCH = "MATCH"
MISMATCH = "MISMATCH"
MISSING = "MISSING"
UNREADABLE = "UNREADABLE"
NO_PATH = "NO_PATH"
NO_HASH = "NO_HASH"
INVALID_HASH = "INVALID_HASH"

_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_HEX_IN_TEXT = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
_DIRECTIVE = re.compile(
    r"^\s*(?:[-*+]\s+|>\s+)*CLAIM\s+(?P<path>\S+)\s+(?P<hash>\S+)"
    r"(?:\s+(?P<note>.*))?$")
_SEPARATOR = re.compile(r"^:?-{3,}:?$")

_PATH_HEADERS = {
    "path", "artifact", "artifact path", "file", "file path", "binary",
    "binary path", "source", "source path", "frame", "frame path",
    "evidence", "evidence path",
}
_HASH_HEADERS = {
    "sha256", "sha-256", "sha 256", "hash", "digest", "sha256 hash",
    "sha-256 hash", "sha 256 hash",
}
_PATH_HEADER_PRIORITY = (
    "path", "artifact path", "file path", "binary path", "source path",
    "frame path", "evidence path", "artifact", "file", "binary", "source",
    "frame", "evidence",
)
_HASH_HEADER_PRIORITY = (
    "sha256", "sha-256", "sha 256", "sha256 hash", "sha-256 hash",
    "sha 256 hash", "hash", "digest",
)
_PATH_KEYS = (
    "path", "artifact_path", "evidence_path", "binary_path", "source_path",
    "frame_path", "output_path", "report_path",
)
_HASH_KEYS = (
    "sha256", "sha-256", "sha_256", "hash", "digest", "evidence_sha256",
    "binary_sha256",
)

LIMITS = (
    "PASS is never assigned; its row is a permanent zero documenting the refusal.",
    "Only a supplied path paired with a 64-hex SHA-256 claim is re-derived; no artifact is invented.",
    "Digests are recomputed from the bytes at the supplied path; a claimed digest is never trusted.",
    "A hash disagreement is TAMPERED; a missing, unreadable or unpaired claim is NOT TESTED with a reason.",
    "A re-derived digest establishes identity only: no build, runtime or equivalence result is implied.",
    "Relative artifact paths are interpreted against the directory of the declaring document.",
    "Source-declared verdicts, coverage text and flags are claims and are never promoted.",
    "No timestamps, host values or repository paths are read; identical inputs give a byte-identical index.",
)

TEMPLATE_COMMAND = (
    "python3 -m tools.proof_doc_compile "
    "--doc <DOC.md> [--doc ...] --report <REPORT.json> [--report ...] "
    "--out <INDEX.md> [--json <INDEX.json>] [--label <LABEL>] [--strict]"
)


# --------------------------------------------------------------------------
# hashing / small helpers
# --------------------------------------------------------------------------
def sha256_file(path: Path) -> str | None:
    """Exact SHA-256 of a file's bytes, or None when it cannot be read."""
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


def strip_md(value: str) -> str:
    """Remove Markdown decoration around a table/directive token."""
    text = value.strip()
    for _ in range(3):
        changed = text
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "`*_":
            text = text[1:-1].strip()
        if text != changed:
            continue
        break
    return text.strip("\"'")


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", strip_md(value).strip().rstrip(":").lower())


def safe_text(value: str) -> str:
    """Neutralise a caller-supplied coverage percentage before rendering."""
    return re.sub(r"(?i)(\d+)\s*%", r"\1 pct (not asserted)", value)


def table_cells(line: str) -> list[str] | None:
    text = line.strip()
    if not text.startswith("|"):
        return None
    return [cell.strip() for cell in text.strip("|").split("|")]


def make_row(source: str, location: str, label: str,
             path: str | None, claimed: str | None) -> dict:
    return {
        "source": source,
        "location": location,
        "label": label,
        "artifact_path": path if isinstance(path, str) and path else None,
        "checked_path": None,
        "claimed_sha256": claimed,
        "actual_sha256": None,
        "bytes": None,
        "status": NO_PATH if not isinstance(path, str) or not path else NO_HASH,
        "verdict": NOT_TESTED,
        "reasons": [],
    }


def check_row(row: dict, base: Path) -> dict:
    """Recompute the artifact digest and assign one fail-safe row verdict."""
    path_text = row["artifact_path"]
    claimed = row["claimed_sha256"]
    if not isinstance(path_text, str) or not path_text.strip():
        row["status"] = NO_PATH
        row["verdict"] = NOT_TESTED
        row["reasons"] = ["no artifact path supplied"]
        return row
    if not is_sha256(claimed):
        row["status"] = INVALID_HASH if isinstance(claimed, str) and claimed.strip() else NO_HASH
        row["verdict"] = NOT_TESTED
        row["reasons"] = ["claimed SHA-256 is missing or not 64 hex characters"]
        return row

    path = Path(path_text)
    target = path if path.is_absolute() else base / path
    row["checked_path"] = Path(os.path.normpath(str(target))).as_posix()
    if not target.is_file():
        row["status"] = MISSING
        row["verdict"] = NOT_TESTED
        row["reasons"] = [f"artifact missing: {path_text}"]
        return row
    actual = sha256_file(target)
    if actual is None:
        row["status"] = UNREADABLE
        row["verdict"] = NOT_TESTED
        row["reasons"] = [f"artifact unreadable: {path_text}"]
        return row
    row["actual_sha256"] = actual
    try:
        row["bytes"] = target.stat().st_size
    except OSError:
        row["bytes"] = None
    if actual.lower() != claimed.lower():
        row["status"] = MISMATCH
        row["verdict"] = TAMPERED
        row["reasons"] = [
            f"hash mismatch for {path_text} (claimed {claimed}, recomputed {actual})"
        ]
    else:
        row["status"] = MATCH
        row["verdict"] = RE_DERIVED
        row["reasons"] = ["SHA-256 re-derived from the supplied artifact bytes"]
    return row


# --------------------------------------------------------------------------
# Markdown extraction
# --------------------------------------------------------------------------
def scan_markdown(text: str, source: str) -> list[dict]:
    """Extract claims from one Markdown document in source line order."""
    rows: list[dict] = []
    columns: dict[str, int] = {}
    for number, line in enumerate(text.splitlines(), 1):
        cells = table_cells(line)
        if cells is not None:
            headers = {normalize_header(cell) for cell in cells}
            if headers & _HASH_HEADERS and headers & _PATH_HEADERS:
                names = [normalize_header(cell) for cell in cells]
                columns = {}
                for name in _PATH_HEADER_PRIORITY:
                    if name in names:
                        columns["path"] = names.index(name)
                        break
                for name in _HASH_HEADER_PRIORITY:
                    if name in names:
                        columns["hash"] = names.index(name)
                        break
                continue
            names = [normalize_header(cell) for cell in cells]
            if (not all(_SEPARATOR.match(cell) for cell in cells)
                    and all(name in _PATH_HEADERS or name in _HASH_HEADERS
                            for name in names)):
                columns = {}
            if all(_SEPARATOR.match(cell) for cell in cells):
                continue
            if columns and len(cells) > max(columns.values()):
                path = strip_md(cells[columns["path"]])
                claimed = strip_md(cells[columns["hash"]])
                label = cells[0] if 0 not in columns.values() else "table claim"
                rows.append(make_row(source, f"line {number}", safe_text(label),
                                     path, claimed))
                continue
        else:
            columns = {}

        directive = _DIRECTIVE.match(line)
        if directive:
            rows.append(make_row(
                source, f"line {number}",
                safe_text(directive.group("note") or "CLAIM directive"),
                strip_md(directive.group("path")),
                strip_md(directive.group("hash"))))
            continue
        if re.match(r"^\s*(?:[-*+]\s+|>\s+)*CLAIM\b", line):
            rows.append(make_row(source, f"line {number}",
                                 "malformed CLAIM directive", None, None))
            continue

        for found in _HEX_IN_TEXT.findall(line):
            rows.append(make_row(source, f"line {number}",
                                 "hash without an artifact path", None, found))
    return rows


# --------------------------------------------------------------------------
# JSON extraction
# --------------------------------------------------------------------------
def _walk_json(node: Any, location: str, rows: list[dict], source: str) -> None:
    if isinstance(node, dict):
        path_value = next((node[key] for key in _PATH_KEYS
                           if isinstance(node.get(key), str) and node[key].strip()), None)
        hash_value = next((node[key] for key in _HASH_KEYS
                           if isinstance(node.get(key), str) and node[key].strip()), None)
        if path_value is not None or hash_value is not None:
            rows.append(make_row(source, location, "JSON claim",
                                 path_value, hash_value))
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                _walk_json(value, f"{location}.{key}", rows, source)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, (dict, list)):
                _walk_json(value, f"{location}[{index}]", rows, source)


def load_report(text: str) -> tuple[list[Any], str | None]:
    """Load one JSON object/array, or a JSON Lines stream of objects."""
    try:
        doc = json.loads(text)
    except ValueError:
        objects: list[Any] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                objects.append(json.loads(line))
            except ValueError as exc:
                return [], f"invalid JSON report: {exc}"
        if not objects:
            return [], "empty JSON report"
        return objects, None
    if isinstance(doc, (dict, list)):
        return [doc], None
    return [], "JSON report is not an object or array"


def scan_report(text: str, source: str) -> tuple[list[dict], str | None]:
    docs, error = load_report(text)
    rows: list[dict] = []
    if error is None:
        for index, doc in enumerate(docs):
            _walk_json(doc, f"document[{index}]" if len(docs) > 1 else "$",
                       rows, source)
    return rows, error


# --------------------------------------------------------------------------
# index construction / rendering
# --------------------------------------------------------------------------
def _source_path(value: str) -> Path:
    return Path(value)


def collect_claims(doc_paths: list[str], report_paths: list[str]) -> tuple[list[dict], list[dict]]:
    """Read every CLI-supplied input; unreadable inputs become NOT TESTED rows."""
    rows: list[dict] = []
    inputs: list[dict] = []
    for kind, paths in (("doc", doc_paths), ("report", report_paths)):
        for raw in paths:
            source = f"{kind}:{raw}"
            inputs.append({"kind": kind, "path": raw})
            path = _source_path(raw)
            if not path.is_file():
                row = make_row(source, "input", f"{kind} missing", raw, None)
                row["reasons"] = [f"{kind} source missing: {raw}"]
                rows.append(row)
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                row = make_row(source, "input", f"{kind} unreadable", raw, None)
                row["reasons"] = [f"{kind} source unreadable: {exc}"]
                rows.append(row)
                continue
            base = path.parent
            if kind == "doc":
                found = scan_markdown(text, source)
            else:
                found, error = scan_report(text, source)
                if error:
                    row = make_row(source, "input", "invalid report", None, None)
                    row["reasons"] = [error]
                    found = [row]
            for row in found:
                rows.append(check_row(row, base))
    inputs.sort(key=lambda item: (item["kind"], item["path"]))
    rows.sort(key=lambda row: (row["source"], row["location"],
                               row["artifact_path"] or ""))
    return rows, inputs


def exact_command(doc_paths: list[str], report_paths: list[str], out: str,
                  json_out: str | None, label: str, strict: bool) -> str:
    parts = ["python3", "-m", "tools.proof_doc_compile"]
    for value in sorted(doc_paths):
        parts += ["--doc", shlex.quote(value)]
    for value in sorted(report_paths):
        parts += ["--report", shlex.quote(value)]
    parts += ["--out", shlex.quote(out)]
    if json_out is not None:
        parts += ["--json", shlex.quote(json_out)]
    if label:
        parts += ["--label", shlex.quote(label)]
    if strict:
        parts.append("--strict")
    return " ".join(parts)


def build_index(rows: list[dict], inputs: list[dict], *, label: str,
                command: str) -> dict:
    counts = {verdict: 0 for verdict in VERDICTS}
    for row in rows:
        bucket = PARTIAL if row["verdict"] == RE_DERIVED else row["verdict"]
        counts[bucket] += 1
    reasons: list[str] = []
    if counts[TAMPERED]:
        status = TAMPERED
        reasons.append(f"{counts[TAMPERED]} claim(s) disagree with artifact bytes; evidence not trusted")
    elif counts[NOT_TESTED]:
        status = NOT_TESTED
        reasons.append(f"{counts[NOT_TESTED]} claim(s) could not be tested; no verdict promoted")
    elif rows:
        status = PARTIAL
        reasons.append("all supplied digests re-derived; identity facts only, no broader claim")
    else:
        status = NOT_TESTED
        reasons.append("no claims found in the supplied inputs")
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "label": safe_text(label),
        "inputs": inputs,
        "counts": counts,
        "claims": rows,
        "verdict": {"status": status, "reasons": reasons},
        "commands": {"exact": command, "template": TEMPLATE_COMMAND},
        "limits": list(LIMITS),
    }


def _md_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _md_code(value: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(1, longest + 1)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def _md_block(value: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}sh\n{value}\n{fence}"


def render_markdown(index: dict) -> str:
    out = [
        "# Evidence document verification index",
        "",
        f"- Label: {_md_cell(index['label'])} (caller-supplied, not evidence)",
        f"- Generator: {_md_code(index['generator'])} (schema {index['schema_version']})",
        f"- Index verdict: {_md_cell(index['verdict']['status'])}",
        "",
    ]
    out.extend(f"- {reason}" for reason in index["verdict"]["reasons"])
    out += ["", "Every digest below was recomputed from artifact bytes at the "
            "supplied path. Source-declared verdicts are never promoted.", "",
            "## Verdict counts", "", "| Verdict | Count |", "|---|---:|"]
    for verdict in VERDICTS:
        out.append(f"| {verdict} | {index['counts'][verdict]} |")
    out += [f"| **Total** | **{len(index['claims'])}** |", "",
            "## Claims", "",
            "| Source | Location | Artifact | Claimed SHA-256 | Recomputed SHA-256 | Verdict | Reason |",
            "|---|---|---|---|---|---|---|"]
    for row in index["claims"]:
        out.append("| {source} | {location} | {artifact} | {claimed} | {actual} | "
                   "{verdict} | {reason} |".format(
                       source=_md_cell(_md_code(row["source"])),
                       location=_md_cell(row["location"]),
                       artifact=_md_cell(_md_code(row["artifact_path"] or "-")),
                       claimed=_md_cell(_md_code(row["claimed_sha256"] or "-")),
                       actual=_md_cell(_md_code(row["actual_sha256"] or "-")),
                       verdict=_md_cell(row["verdict"]),
                       reason=_md_cell("; ".join(row["reasons"])),
                   ))
    out += ["", "## Inputs", ""]
    for item in index["inputs"]:
        out.append(f"- {item['kind']}: {_md_code(item['path'])}")
    if not index["inputs"]:
        out.append("- none supplied")
    out += ["", "## Reproduction commands", "",
            "Exact invocation used for this index:", "",
            _md_block(index["commands"]["exact"]), "",
            "Template for a fresh set of inputs:", "",
            _md_block(index["commands"]["template"]), "", "## Limits", ""]
    out.extend(f"- {limit}" for limit in index["limits"])
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--doc", action="append", default=[],
                        help="Markdown evidence document (repeatable)")
    parser.add_argument("--report", action="append", default=[],
                        help="JSON report, object/array or JSON Lines (repeatable)")
    parser.add_argument("--out", required=True, type=Path,
                        help="write the deterministic Markdown index here")
    parser.add_argument("--json", dest="json_out", type=Path,
                        help="also write the machine-readable index JSON here")
    parser.add_argument("--label", default="CLI-supplied evidence bundle",
                        help="caller-supplied label recorded as metadata only")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any claim is TAMPERED")
    args = parser.parse_args(argv)

    if not args.doc and not args.report:
        print("proof_doc_compile: supply at least one --doc or --report",
              file=sys.stderr)
        return 2

    command = exact_command(args.doc, args.report, str(args.out),
                            str(args.json_out) if args.json_out else None,
                            args.label, args.strict)
    rows, inputs = collect_claims(args.doc, args.report)
    index = build_index(rows, inputs, label=args.label, command=command)
    markdown = render_markdown(index)
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        if args.json_out is not None:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8")
    except OSError as exc:
        print(f"proof_doc_compile: cannot write index: {exc}", file=sys.stderr)
        return 2

    counts = ", ".join(f"{key}={value}" for key, value in index["counts"].items())
    print(f"wrote {args.out}: {len(rows)} claim(s); {counts}; "
          f"verdict={index['verdict']['status']}")
    if args.strict and index["counts"][TAMPERED]:
        print("proof_doc_compile: TAMPERED claims found (--strict)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
