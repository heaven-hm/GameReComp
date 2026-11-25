#!/usr/bin/env python3
"""Standalone, evidence-only duplicate-C-body reporter for autorecomp (#26).

Reads ONLY C files named on the command line, parses function bodies with a
conservative byte-level scanner, and emits a deterministic report of how many
bytes are occupied by byte-identical copies of the same body. It never rewrites
source, never runs a build, and never claims a build improvement: the number it
reports is the *exact* count of redundant body bytes present in the supplied
files (an upper bound on what an explicit, reviewed rewrite could remove), not a
measured wall-time or binary-size gain. ``build_improvement_measured`` is always
false and ``code_rewritten`` is always false in every report.

Parsing is deliberately fail-closed. Strings, character literals, comments and
preprocessor directives are skipped with their own scanners; any unterminated
literal/comment, unmatched brace, unbalanced directive, or top-level ``{`` that
cannot be classified confidently as a function body or a plain declaration is
an error (exit 2) rather than a guessed function list. Supplying the same file
twice is also refused, so an input cannot be double-counted as a duplicate.

A prior report can be supplied with ``--verify``. The tool then recomputes every
fact from the current files and compares it with the prior facts; any change
(including a one-byte edit to a duplicate body) yields verdict ``TAMPERED`` and
exit 1. There are no built-in paths, no timestamps, no fabricated readings, and
no hidden state: the same files always produce byte-identical JSON.

Usage::

    python3 -m tools.autorecomp.source_dedup FILE.c [FILE.c ...] \
        [--verify prior.json] [--out-json dedup.json] [--out-md dedup.md] [--quiet]

Issue #26 context (verified, not changed by this tool): the host pipeline in
``tools/autorecomp/pipeline.py`` already publishes measured
``resources.generated_source_bytes``, ``resources.build_seconds``, both
``resources.peak_rss_bytes*`` domains and per-stage timing into
``reports/report.json`` (``Autorecomp.stage_report``); the opt-in disk guard
(``RECOMP_MIN_FREE_DISK_BYTES``) and ``effective_build_jobs`` budget parallelism
from measured free disk and available RAM, and ``tool.check_build_disk_space``
fails closed on a failed measurement or insufficient space. This tool adds the
source-volume analysis only; no full-corpus rebuild has been demonstrated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

SCHEMA_VERSION = 1
GENERATOR = "tools/autorecomp/source_dedup.py"

_LBRACE = 0x7B
_RBRACE = 0x7D
_SEMI = 0x3B
_QUOTE = 0x22
_APOS = 0x27
_HASH = 0x23
_BACKSLASH = 0x5C
_NEWLINE = 0x0A

_IDENT_RE = re.compile(rb"[A-Za-z_][A-Za-z_0-9]*")
_DECL_RE = re.compile(rb"(?:typedef|struct|union|enum)\b")


class AmbiguousParse(ValueError):
    """A supplied C file could not be parsed with confidence; fail closed."""


class Function(NamedTuple):
    name: str
    signature: str
    body_start: int
    body_end: int
    body_bytes: int
    sha256: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "signature": self.signature,
            "body_start": self.body_start,
            "body_end": self.body_end,
            "body_bytes": self.body_bytes,
            "sha256": self.sha256,
        }


def _skip_quoted(data: bytes, i: int, where: str) -> int:
    """Index just past a string/char literal, or raise AmbiguousParse."""
    quote = data[i]
    j = i + 1
    n = len(data)
    while j < n:
        c = data[j]
        if c == _BACKSLASH:
            if j + 1 >= n:
                raise AmbiguousParse(f"{where}: dangling escape in literal")
            j += 2
            continue
        if c == quote:
            return j + 1
        if c == _NEWLINE:
            raise AmbiguousParse(f"{where}: literal crosses a raw newline")
        j += 1
    raise AmbiguousParse(f"{where}: unterminated literal")


def _skip_block_comment(data: bytes, i: int, where: str) -> int:
    end = data.find(b"*/", i + 2)
    if end < 0:
        raise AmbiguousParse(f"{where}: unterminated block comment")
    return end + 2


def _skip_line_comment(data: bytes, i: int) -> int:
    end = data.find(b"\n", i)
    return len(data) if end < 0 else end


def _at_line_start(data: bytes, i: int) -> bool:
    return i == 0 or data[i - 1] == _NEWLINE


def _skip_directive(data: bytes, i: int, where: str) -> int:
    """Skip one preprocessor directive (with continuations), fail closed on
    structural braces outside literals/comments. A ``#define`` containing a
    balanced ``do { ... } while(0)`` is fine; an unbalanced brace is not.
    """
    j = i
    n = len(data)
    braces = 0
    while j < n:
        c = data[j]
        if c == _BACKSLASH and j + 1 < n and data[j + 1] == _NEWLINE:
            j += 2
            continue
        if c == _NEWLINE:
            if braces:
                raise AmbiguousParse(
                    f"{where}: unbalanced braces in preprocessor directive")
            return j + 1
        if c in (_QUOTE, _APOS):
            j = _skip_quoted(data, j, where)
            continue
        if data[j:j + 2] == b"//":
            j = _skip_line_comment(data, j)
            continue
        if data[j:j + 2] == b"/*":
            j = _skip_block_comment(data, j, where)
            continue
        if c == _LBRACE:
            braces += 1
        elif c == _RBRACE:
            braces -= 1
        j += 1
    if braces:
        raise AmbiguousParse(f"{where}: unbalanced braces in preprocessor directive")
    return j


def _classify_head(head: bytes):
    """Return ("function", name, signature), ("skip",), or None=ambiguous."""
    if not head:
        return None
    if head.startswith(b"typedef") or _DECL_RE.match(head):
        # A struct/union/enum/typedef declaration, never a function definition.
        # (A function returning such a type still has '(' after the keyword.)
        if b"(" not in head:
            return ("skip",)
    if head[-1:] != b")":
        return None
    if head.count(b"(") == 0 or head.count(b"(") != head.count(b")"):
        return None
    before = head[:head.find(b"(")]
    ident = None
    for match in _IDENT_RE.finditer(before):
        ident = match
    if ident is None:
        return None
    name = ident.group().decode("ascii")
    signature = " ".join(head.decode("utf-8", "replace").split())
    return ("function", name, signature)


def parse_functions(data: bytes, label: str) -> list[Function]:
    """Parse byte-exact function bodies from one generated C file."""
    where = str(label)
    n = len(data)
    i = 0
    depth = 0
    prefix = bytearray()
    current = None  # (name, signature, body_start)
    functions: list[Function] = []

    while i < n:
        c = data[i]
        if c == _HASH and _at_line_start(data, i):
            i = _skip_directive(data, i, where)
            continue
        if data[i:i + 2] == b"//":
            i = _skip_line_comment(data, i)
            continue
        if data[i:i + 2] == b"/*":
            i = _skip_block_comment(data, i, where)
            if depth == 0:
                prefix.append(0x20)
            continue
        if c in (_QUOTE, _APOS):
            i = _skip_quoted(data, i, where)
            if depth == 0:
                prefix.append(0x20)
            continue
        if c == _LBRACE:
            if depth == 0:
                head = bytes(prefix).strip()
                if b"=" in head:
                    # An aggregate initialiser, not a function body. Its braces
                    # are balanced by the ordinary depth logic below.
                    depth = 1
                    prefix.clear()
                    i += 1
                    continue
                kind = _classify_head(head)
                if kind is None:
                    raise AmbiguousParse(
                        f"{where}: offset {i}: cannot classify top-level '{{' "
                        f"after {head[:80]!r}")
                if kind[0] == "function":
                    current = (kind[1], kind[2], i + 1)
                depth = 1
                prefix.clear()
                i += 1
                continue
            depth += 1
            i += 1
            continue
        if c == _RBRACE:
            depth -= 1
            if depth < 0:
                raise AmbiguousParse(f"{where}: unmatched '}}' at offset {i}")
            if depth == 0:
                if current is not None:
                    name, signature, start = current
                    body = data[start:i]
                    functions.append(Function(
                        name, signature, start, i, len(body),
                        hashlib.sha256(body).hexdigest()))
                    current = None
                prefix.clear()
            i += 1
            continue
        if c == _SEMI and depth == 0:
            prefix.clear()
            i += 1
            continue
        if depth == 0:
            prefix.append(c)
        i += 1

    if depth != 0 or current is not None:
        raise AmbiguousParse(f"{where}: unbalanced braces at end of file")
    return functions


def _file_facts(path: Path, data: bytes) -> dict:
    functions = parse_functions(data, path)
    return {
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "functions": [fn.as_dict() for fn in functions],
    }


def _group_duplicates(inputs: list[dict]) -> list[dict]:
    by_digest: dict[str, list[tuple[str, dict]]] = {}
    for info in inputs:
        for fn in info["functions"]:
            by_digest.setdefault(fn["sha256"], []).append((info["path"], fn))

    groups = []
    for digest in sorted(by_digest):
        members = sorted(by_digest[digest],
                         key=lambda m: (m[0], m[1]["body_start"]))
        if len(members) < 2:
            continue
        body_bytes = members[0][1]["body_bytes"]
        redundant = len(members) - 1
        groups.append({
            "digest": digest,
            "body_bytes": body_bytes,
            "copies": len(members),
            "redundant_copies": redundant,
            "redundant_bytes": body_bytes * redundant,
            "signatures_identical": len({m[1]["signature"] for m in members}) == 1,
            "members": [
                {"path": path, "name": fn["name"], "signature": fn["signature"],
                 "body_start": fn["body_start"], "body_end": fn["body_end"]}
                for path, fn in members
            ],
        })
    groups.sort(key=lambda g: (-g["redundant_bytes"], g["digest"]))
    return groups


def analyse(paths: list[Path]) -> dict:
    """Read the supplied files and build a deterministic dedup summary."""
    inputs = []
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise AmbiguousParse(f"{path}: cannot read file: {exc}") from exc
        inputs.append(_file_facts(path, data))
    inputs.sort(key=lambda info: info["path"])

    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            raise AmbiguousParse(f"duplicate input path supplied twice: {path}")
        seen.add(resolved)

    groups = _group_duplicates(inputs)
    facts = {
        "input_bytes_total": sum(info["bytes"] for info in inputs),
        "function_count": sum(len(info["functions"]) for info in inputs),
        "duplicate_group_count": len(groups),
        "exact_duplicate_byte_savings": sum(g["redundant_bytes"] for g in groups),
        "inputs": inputs,
        "duplicate_groups": groups,
    }
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "verdict": "OK",
        "facts": facts,
        "build_improvement_measured": False,
        "code_rewritten": False,
        "limitations": [
            "No source was rewritten and no build was run.",
            "Exact savings count byte-identical body copies beyond the first; "
            "the bytes of any replacement call are not measured.",
            "No full-corpus rebuild before/after measurement was demonstrated.",
        ],
        "findings": [],
    }


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _fingerprint(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:16]


def verify(summary: dict, expected: dict) -> list[str]:
    """Compare recomputed facts with a prior report; flag every difference."""
    findings: list[str] = []
    if not isinstance(expected, dict):
        return ["prior report is not a JSON object"]
    if expected.get("generator") != GENERATOR:
        findings.append("prior report generator mismatch")
    prior = expected.get("facts")
    if not isinstance(prior, dict):
        return findings + ["prior report has no facts object"]
    current = summary["facts"]
    for key in sorted(set(prior) | set(current)):
        if prior.get(key) != current.get(key):
            findings.append(
                f"facts.{key}: prior={_fingerprint(prior.get(key))} "
                f"observed={_fingerprint(current.get(key))}")
    return findings


def dumps_json(summary: dict) -> str:
    """Canonical JSON: sorted keys, fixed indentation, trailing newline."""
    return json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def render_markdown(summary: dict) -> str:
    facts = summary["facts"]
    lines = [
        "# Source dedup evidence (issue #26)",
        "",
        f"Inputs: {len(facts['inputs'])} file(s), {facts['input_bytes_total']} "
        f"measured bytes; {facts['function_count']} function body(ies) parsed.",
        f"Exact duplicate-body bytes: {facts['exact_duplicate_byte_savings']} "
        f"across {facts['duplicate_group_count']} group(s).",
        "Build improvement measured: **NO**. Code rewritten: **NO**.",
        "",
        "| Group | Body bytes | Copies | Redundant bytes | Same signature |",
        "|---|---|---|---|---|",
    ]
    for group in facts["duplicate_groups"]:
        lines.append(
            f"| {group['digest'][:12]} | {group['body_bytes']} | "
            f"{group['copies']} | {group['redundant_bytes']} | "
            f"{'yes' if group['signatures_identical'] else 'no'} |")
    if not facts["duplicate_groups"]:
        lines.append("| (none) | 0 | 0 | 0 | — |")
    lines += ["", "## Limitations", ""]
    lines += [f"- {note}" for note in summary["limitations"]]
    lines += ["", "## Findings", ""]
    lines += [f"- {finding}" for finding in summary["findings"]] or ["- none"]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, help="generated C file(s) to read")
    ap.add_argument("--file", dest="extra_files", action="append", type=Path,
                    help="additional generated C file (repeatable)")
    ap.add_argument("--verify", type=Path,
                    help="prior dedup report to compare against (tamper detection)")
    ap.add_argument("--out-json", type=Path, help="write the JSON summary here")
    ap.add_argument("--out-md", type=Path, help="write the Markdown summary here")
    ap.add_argument("--quiet", action="store_true", help="do not print Markdown")
    args = ap.parse_args(argv)

    paths = list(args.files) + list(args.extra_files or [])
    if not paths:
        ap.error("at least one generated C file is required")
    paths.sort(key=str)

    try:
        summary = analyse(paths)
        expected = None
        if args.verify:
            expected = json.loads(args.verify.read_text(encoding="utf-8"))
    except AmbiguousParse as exc:
        print(f"source_dedup: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"source_dedup: cannot read prior report: {exc}", file=sys.stderr)
        return 2

    if expected is not None:
        summary["findings"] = verify(summary, expected)
        if summary["findings"]:
            summary["verdict"] = "TAMPERED"

    markdown = render_markdown(summary)
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(dumps_json(summary), encoding="utf-8")
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(markdown, encoding="utf-8")
    if not args.quiet:
        print(markdown)

    return 1 if summary["verdict"] == "TAMPERED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
