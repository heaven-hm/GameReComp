#!/usr/bin/env python3
"""Deterministic, review-oriented probe for win32hle shim risk patterns.

This is an *assistive* static scan over first-party C sources in
``runtime/win32hle``.  It reads text only -- it does not compile, lift, inject,
or run the game -- and it reports suspicious patterns with a file and line so a
human reviewer can look at the exact code.  A clean report is NOT a proof of
correctness, and a finding is NOT a proof of a bug: every result is a prompt to
inspect, nothing more.

Rules (all heuristic, all deliberately conservative about false positives):

* ``unchecked-guest-pointer`` -- a value obtained from ``APTR``/``ASTR``/
  ``hle_ptr``/``A32`` is dereferenced without a nearby validity check.  In the
  based-memory build guest pointer 0 becomes host NULL, so a missing guard can
  fault the host instead of behaving like the Win32 API.
* ``ret-arity-too-small`` -- a stdcall shim pops fewer argument words with
  ``RET``/``RETP``/``RETV`` than the highest argument index it reads.  That
  leaves the guest stack misaligned on return.
* ``inconsistent-ret-arity`` -- different ``RET`` sites in one shim pop
  different numbers of words.
* ``missing-ret`` -- a function named in a shim table reads arguments but has
  no ``RET`` at all (a common state of a half-written new shim).
* ``duplicate-shim-name`` -- the same literal shim name is registered in more
  than one place.  Registration order decides which one wins, which is rarely
  what the author intended.

Usage::

    python3 -m tools.ai_assist_probe                    # JSON on stdout
    python3 -m tools.ai_assist_probe --out probe.json
    python3 -m tools.ai_assist_probe --source /path/to/synthetic/c

Output is deterministic: no timestamps, no machine-dependent paths, sorted
findings.  The same source tree always produces byte-identical JSON.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "runtime" / "win32hle"
SCHEMA_VERSION = 1
DISCLAIMER = (
    "Heuristic review aid only. Findings are leads, not proofs of defects, and "
    "an empty report is not proof of correctness. No game, PE, or lifted code is "
    "executed; verify behavior with the project's harnesses and live-game "
    "workflow before claiming a fix works."
)

# Only files under the scanned directory are considered first-party. Selftests
# deliberately contain mock tables and partial shims, so they are excluded by
# default; --include-selftests opts back in.
SELFTEST_SUFFIX = "_selftest.c"

# A C function that can be a shim: "static void k_Foo(void) {" and friends.
# ``mask_c`` blanks comments and string literals, so matches are code only.
FUNC_RE = re.compile(
    r"(?m)^[ \t]*(?:static[ \t]+)?(?:[A-Za-z_]\w*[ \t]+)*?void[ \t]+"
    r"([A-Za-z_]\w*)[ \t]*\([^;{}]*\)[ \t]*\{"
)
ARG_READ_RE = re.compile(r"\b(A32|APTR|ASTR|hle_a32)\s*\(\s*(\d+)\s*\)")
RET_TOKEN_RE = re.compile(r"\b(RET|RETP|RETV|RETC|RETCV)\b")
INT_RE = re.compile(r"\d+[uUlL]*\Z")

# Sources of guest addresses/pointers, and the variables that hold them.
GUEST_PTR_VAR_RE = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*(?:ASTR|APTR|hle_ptr)\s*\(")
GUEST_VA_VAR_RE = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*A32\s*\(")
DIRECT_GUEST_DEREF_RE = re.compile(
    r"\*\s*(?:APTR|ASTR|hle_ptr)\s*\("
    r"|(?:APTR|ASTR|hle_ptr)\s*\([^)]*\)\s*(?:->|\[)"
    r"|MEM(?:8|16|32|64)\s*\(\s*(?:APTR|hle_ptr)\s*\("
)
# Library calls that dereference a pointer argument. This list is intentionally
# short and well known; unknown calls are not guessed at.
DEREF_CALLS = frozenset({
    "strlen", "strcmp", "strcpy", "strcat", "memcpy", "memmove", "memset",
    "memcmp", "strncpy", "strncmp", "strnlen", "lstrlenA", "lstrlenW",
})

# Shim table entry forms, shared in spirit with tools/api_coverage.py. Only
# *literal* names are extracted; names assembled by token pasting are out of
# scope and are called out in docs/AI_ASSIST_TOOLING.md.
SHIM_NAME = r'"([A-Za-z0-9_:?#@.$]+)"'
SHIM_ENTRY_PATTERNS = [
    re.compile(r"\{\s*" + SHIM_NAME + r"\s*,\s*[A-Za-z_]\w*"),
    re.compile(r"\b(?:SHIM\d*|AUT)\s*\(\s*\d+\s*,\s*" + SHIM_NAME),
    re.compile(r"\bX\s*\(\s*" + SHIM_NAME + r"\s*,\s*[A-Za-z_]\w*\s*,\s*\d+\s*\)"),
]
SHIM_FN_RE = re.compile(r'\{\s*"[A-Za-z0-9_:?#@.$]+"\s*,\s*([A-Za-z_]\w*)')


@dataclass(frozen=True)
class Finding:
    """One review lead. ``reason`` is always human-readable."""

    file: str
    line: int
    rule: str
    reason: str

    def as_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "rule": self.rule, "reason": self.reason}


def mask_c(text: str) -> str:
    """Blank comments and string/char literals, preserving offsets and lines.

    Brace matching and identifier searches run on the masked copy so a ``{``
    inside a comment or a format string cannot confuse them.
    """
    out = list(text)
    n = len(text)
    i = 0

    def blank(start: int, end: int) -> None:
        for j in range(start, min(end, n)):
            if out[j] != "\n":
                out[j] = " "

    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            blank(i, n if j < 0 else j)
            i = n if j < 0 else j
        elif c in ('"', "'"):
            quote = c
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            blank(i, j)
            i = j
        else:
            i += 1
    return "".join(out)


def line_of(text: str, offset: int) -> int:
    """1-based source line for a character offset."""
    return text.count("\n", 0, offset) + 1


def _matching_brace(text: str, open_at: int) -> int | None:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def functions(masked: str):
    """Yield ``(name, body_start, body_end)`` offsets for void functions."""
    for m in FUNC_RE.finditer(masked):
        open_at = masked.index("{", m.start())
        close_at = _matching_brace(masked, open_at)
        if close_at is not None:
            yield m.group(1), open_at + 1, close_at


def _call_content(text: str, open_at: int) -> str | None:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    return None


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for i, c in enumerate(text):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return parts


def _ret_arities(body: str):
    """Yield ``(offset_in_body, macro, argc)`` for each constant-arity RET."""
    for m in RET_TOKEN_RE.finditer(body):
        macro = m.group(1)
        open_at = m.end()
        while open_at < len(body) and body[open_at].isspace():
            open_at += 1
        if open_at >= len(body) or body[open_at] != "(":
            continue
        content = _call_content(body, open_at)
        if content is None:
            continue
        parts = [p.strip() for p in content.split(",")] if macro == "RETV" else _split_top_level(content)
        last = parts[-1] if parts else ""
        if not INT_RE.fullmatch(last):
            continue  # computed arity: cannot judge it textually
        yield m.start(), macro, int(last.rstrip("uUlL"))


def check_ret_arity(
    name: str, body: str, body_start: int, text: str, display: str, shim_functions: set[str]
) -> list[Finding]:
    """Check a shim's argument reads against its stdcall return purges."""
    finds: list[Finding] = []
    reads = [(m.group(1), int(m.group(2)), m.start()) for m in ARG_READ_RE.finditer(body)]
    rets = list(_ret_arities(body))

    # cdecl shims (SDL 1.2 style) leave the arguments for the caller; RETC/CV
    # do not pop them, so the stdcall arity rules do not apply.
    if any(macro.startswith("RETC") for _, macro, _ in rets):
        return finds

    if reads and not rets and name in shim_functions:
        _, _, off = reads[0]
        return [Finding(
            display, line_of(text, body_start + off), "missing-ret",
            f"shim '{name}' reads guest arguments but has no RET/RETP/RETV; the guest stack "
            "would not be restored on return",
        )]

    if not reads or not rets:
        return finds

    highest = max(index for _, index, _ in reads)
    last_read = next(macro for macro, index, _ in reversed(reads) if index == highest)
    for off, macro, argc in rets:
        if argc < highest + 1:
            finds.append(Finding(
                display, line_of(text, body_start + off), "ret-arity-too-small",
                f"shim '{name}' reads argument {highest} via {last_read}() but {macro} purges "
                f"argc={argc}; at least {highest + 1} words are needed to restore the guest stack",
            ))

    arities = {argc for _, _, argc in rets}
    if len(arities) > 1:
        off = rets[0][0]
        values = ", ".join(str(a) for a in sorted(arities))
        finds.append(Finding(
            display, line_of(text, body_start + off), "inconsistent-ret-arity",
            f"shim '{name}' has RET sites with differing argc values ({values}); a stdcall "
            "shim must pop the same number of argument words on every path",
        ))
    return finds


def _has_guard(body: str, token: str) -> bool:
    """True when an ``if`` syntactically looks like a guard for ``token``."""
    word = re.escape(token)
    for m in re.finditer(r"\bif\s*\(([^)]*)\)", body):
        cond = m.group(1)
        if token not in cond:
            continue
        if re.search(r"!\s*" + word, cond) or re.search(word + r"\s*(?:&&|\|\||==|!=|\))", cond):
            return True
        if "NULL" in cond:
            return True
    return False


def check_guest_pointers(
    name: str, body: str, body_start: int, text: str, display: str
) -> list[Finding]:
    """Flag guest-pointer/VA dereferences with no syntactic validity check."""
    finds: list[Finding] = []
    lines = body.split("\n")

    # Direct uses, e.g. ``*APTR(0)`` or ``MEM32(APTR(1))``. A guard on the
    # very same line is accepted; anything else is a lead for review.
    for m in DIRECT_GUEST_DEREF_RE.finditer(body):
        line_text = lines[text.count("\n", body_start, body_start + m.start())]
        if "if (" in line_text or "if(" in line_text:
            continue
        finds.append(Finding(
            display, line_of(text, body_start + m.start()), "unchecked-guest-pointer",
            f"shim '{name}' dereferences a guest pointer directly with no local guard; "
            "guest VA 0 becomes host NULL in based-memory builds",
        ))

    # Variables assigned from guest pointers or guest VAs.
    for pattern, kind in ((GUEST_PTR_VAR_RE, "pointer"), (GUEST_VA_VAR_RE, "address")):
        for m in pattern.finditer(body):
            var = m.group(1)
            if _has_guard(body, var):
                continue
            word = re.escape(var)
            derefs = [
                re.compile(r"\*\s*" + word + r"\b"),
                re.compile(r"\b" + word + r"\s*(?:->|\[)"),
                re.compile(r"\bMEM(?:8|16|32|64)\s*\(\s*" + word + r"\b"),
            ]
            derefs += [
                re.compile(r"\b" + call + r"\s*\([^;]*?\b" + word + r"\b")
                for call in sorted(DEREF_CALLS)
            ]
            for deref in derefs:
                dm = deref.search(body, m.end())
                if dm is not None:
                    finds.append(Finding(
                        display, line_of(text, body_start + dm.start()), "unchecked-guest-pointer",
                        f"shim '{name}' uses {kind} '{var}' from a guest argument as a "
                        "dereferenced pointer without a NULL/validity guard",
                    ))
                    break  # one lead per variable is enough for review
    return finds


def shim_name_entries(raw: str) -> tuple[list[tuple[str, int]], set[str]]:
    """Return ``(literal name, line)`` entries and registered C function names."""
    entries: list[tuple[str, int]] = []
    functions_named: set[str] = set()
    for pattern in SHIM_ENTRY_PATTERNS:
        for m in pattern.finditer(raw):
            entries.append((m.group(1), line_of(raw, m.start(1))))
    for m in SHIM_FN_RE.finditer(raw):
        functions_named.add(m.group(1))
    return entries, functions_named


def duplicate_name_findings(entries: list[tuple[str, str, int]]) -> list[Finding]:
    """Report every file:line where a shim name is registered more than once."""
    by_name: dict[str, list[tuple[str, int]]] = {}
    for name, display, line in entries:
        by_name.setdefault(name, []).append((display, line))
    finds: list[Finding] = []
    for name, places in sorted(by_name.items()):
        if len(places) < 2:
            continue
        for file, line in places:
            others = ", ".join(f"{f}:{ln}" for f, ln in places if (f, ln) != (file, line))
            finds.append(Finding(
                file, line, "duplicate-shim-name",
                f"shim name '{name}' is registered more than once (also: {others}); "
                "the registration order, not the source, decides which shim answers",
            ))
    return finds


def iter_c_files(source: Path, include_selftests: bool = False):
    """Sorted first-party C files directly inside ``source``."""
    for path in sorted(source.glob("*.c")):
        if not include_selftests and path.name.endswith(SELFTEST_SUFFIX):
            continue
        yield path


def scan_path(source: Path, display_root: Path | None = None, include_selftests: bool = False) -> list[Finding]:
    """Scan every first-party C file in ``source`` and return sorted findings."""
    source = Path(source)
    display_root = Path(display_root) if display_root is not None else source
    all_entries: list[tuple[str, str, int]] = []
    all_shim_functions: set[str] = set()
    per_file: dict[Path, tuple[str, str]] = {}

    for path in iter_c_files(source, include_selftests):
        raw = path.read_text(encoding="utf-8", errors="replace")
        display = path.relative_to(display_root).as_posix() if display_root in path.parents else path.name
        per_file[path] = (raw, display)
        entries, shim_functions = shim_name_entries(raw)
        all_shim_functions |= shim_functions
        for name, line in entries:
            all_entries.append((name, display, line))

    finds: list[Finding] = []
    for path, (raw, display) in per_file.items():
        masked = mask_c(raw)
        for name, start, end in functions(masked):
            body = masked[start:end]
            finds.extend(check_ret_arity(name, body, start, masked, display, all_shim_functions))
            finds.extend(check_guest_pointers(name, body, start, masked, display))

    # Duplicate names are a whole-tree rule: a name may be defined in one file
    # and duplicated in another.
    finds.extend(duplicate_name_findings(all_entries))

    # Deterministic output: sort, then drop exact duplicates.
    unique = sorted({(f.file, f.line, f.rule, f.reason) for f in finds})
    return [Finding(file, line, rule, reason) for file, line, rule, reason in unique]


def build_report(source: Path = DEFAULT_SOURCE, display_root: Path | None = None,
                 include_selftests: bool = False) -> dict:
    """Build the JSON-serialisable report. No timestamps or host-specific paths."""
    source = Path(source)
    display_root = Path(display_root) if display_root is not None else source
    findings = scan_path(source, display_root, include_selftests)
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.rule] = counts.get(finding.rule, 0) + 1
    root_name = source.relative_to(display_root).as_posix() if display_root in source.parents else source.name
    return {
        "schema": SCHEMA_VERSION,
        "tool": "ai_assist_probe",
        "source": root_name,
        "files_scanned": len(list(iter_c_files(source, include_selftests))),
        "summary": {"findings": len(findings), "by_rule": dict(sorted(counts.items()))},
        "disclaimer": DISCLAIMER,
        "findings": [f.as_dict() for f in findings],
    }


def render(report: dict) -> str:
    """Stable JSON: sorted keys and fixed indentation, trailing newline."""
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="directory of first-party C sources (default: runtime/win32hle)")
    parser.add_argument("--repo-root", type=Path, default=ROOT,
                        help="repo root used to make reported paths relative (default: inferred)")
    parser.add_argument("--out", type=Path, default=None,
                        help="write JSON here instead of stdout")
    parser.add_argument("--include-selftests", action="store_true",
                        help="also scan *_selftest.c files")
    args = parser.parse_args(argv)

    report = build_report(args.source, args.repo_root, args.include_selftests)
    text = render(report)
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
