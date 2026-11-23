#!/usr/bin/env python3
"""Portable machine-readable source map for generated recompilation C.

Methodology is adapted from Stela's ``NativeBlock.source_map`` (pinned commit
140ffaf716e2fa8a2f13d55880ca0fc4521df6fe): a native block carries a list of
``(guest_address, offset)`` pairs that symbolise faults and is geometry-checked
before use.  GameReComp emits C instead of machine bytes, so the portable
analogue is a list of ``(guest_va, c_file, c_line)`` rows recovered by *reading*
an already-generated project directory.  No Stela source is copied -- its
engine is GPL-2.0-or-later, this implementation is original and AGPL-3.0.

Design rules:

* Input is a CLI-supplied generated project directory (or the ``gen`` dir
  itself).  No pipeline changes, no runtime changes, no emitted-C changes.
* Output is JSON whose file paths are *relative to the project directory* with
  POSIX separators.  Absolute paths, user names and machine details are never
  written, so the artefact is portable across machines and checkouts.
* This is a generated report, never source: a readability pass or a human edit
  cannot silently invalidate it, and it is regenerated from the files on disk.
* Validation returns *named violations*, never a bare bool, so callers can
  fail closed with a concrete reason (the Stela ``validate_source_map_geometry``
  idea applied to function-level C geometry).

Scope: function-level.  Each ``void sub_XXXXXXXX(void)`` definition and each
``{ 0xXXXXXXXXu, sub_XXXXXXXX }`` dispatch entry is mapped to file/line.  A
per-instruction (guest opcode -> C statement) map would require changing the
lifter and is deliberately out of scope here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SCHEMA_VERSION = 1
GENERATOR = "GameReComp/source_map.py"

# A generated unit defines `void sub_XXXXXXXX(void) {` (optionally the
# `sub_XXXXXXXX__lowlevel` mechanical fallback kept beside a high-level entry).
DEF_RE = re.compile(
    r'(?m)^[ \t]*void[ \t]+(sub_([0-9A-Fa-f]{8})(?:__lowlevel)?)'
    r'[ \t]*\([ \t]*void[ \t]*\)[ \t]*\{')

# The dispatch table: `{ 0xXXXXXXXXu, sub_XXXXXXXX },`
DISPATCH_RE = re.compile(
    r'(?m)^[ \t]*\{[ \t]*0x([0-9A-Fa-f]+)u?[ \t]*,[ \t]*'
    r'([A-Za-z_][A-Za-z0-9_]*)[ \t]*\}[ \t]*,[ \t]*\r?$')

# Forward declarations carry the authoritative VA as a trailing comment.
HEADER_RE = re.compile(
    r'(?m)^[ \t]*void[ \t]+([A-Za-z_][A-Za-z0-9_]*)'
    r'[ \t]*\([ \t]*void[ \t]*\)[ \t]*;[ \t]*/\*[ \t]*'
    r'0x([0-9A-Fa-f]+)[ \t]*\*/')

COUNT_RE = re.compile(
    r'(?m)^[ \t]*const[ \t]+uint32_t[ \t]+recomp_dispatch_count'
    r'[ \t]*=[ \t]*(\d+)[ \t]*;')

# Address encoded in an address-named symbol, when the symbol has one.
SYMBOL_ADDR_RE = re.compile(r'^sub_([0-9A-Fa-f]{8})(?:__lowlevel)?$')


# ---------------------------------------------------------------------------
# Discovery and low-level helpers
# ---------------------------------------------------------------------------

def find_gen_dir(project_dir: Path | str) -> Path:
    """Locate the directory holding the generated C inside a project tree.

    Accepts a full generated project (``<project>/src/recomp/gen``), a bare
    ``gen`` directory, or any tree containing ``recomp_dispatch.c``.  Raises
    ``ValueError`` with a readable message when there is nothing to map.
    """
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project directory does not exist: {root}")
    canonical = root / "src" / "recomp" / "gen"
    if canonical.is_dir() and any(canonical.glob("recomp_*.c")):
        return canonical
    if any(root.glob("recomp_*.c")) or (root / "recomp_dispatch.c").is_file():
        return root
    for dispatch in sorted(root.rglob("recomp_dispatch.c")):
        return dispatch.parent
    raise ValueError(f"no generated C (recomp_*.c) found under {root}")


def _body_end_line(text: str, brace_index: int) -> int | None:
    """Return the 1-based line of the brace closing the body at *brace_index*.

    Skips comments and string/char literals so a ``/* } */`` or a brace in a
    format string cannot end the body early.  ``None`` means unbalanced.
    """
    i = brace_index
    depth = 0
    state = "code"
    n = len(text)
    while i < n:
        c = text[i]
        if state == "code":
            if c == "/" and i + 1 < n and text[i + 1] == "/":
                state, i = "line_comment", i + 2
                continue
            if c == "/" and i + 1 < n and text[i + 1] == "*":
                state, i = "block_comment", i + 2
                continue
            if c == '"':
                state, i = "string", i + 1
                continue
            if c == "'":
                state, i = "char", i + 1
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text.count("\n", 0, i) + 1
        elif state == "line_comment":
            if c == "\n":
                state = "code"
        elif state == "block_comment":
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                state, i = "code", i + 2
                continue
        elif state in ("string", "char"):
            quote = '"' if state == "string" else "'"
            if c == "\\":
                i += 2
                continue
            if c == quote:
                state = "code"
        i += 1
    return None


def _line_at(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


# ---------------------------------------------------------------------------
# Map construction
# ---------------------------------------------------------------------------

def build_source_map(project_dir: Path | str) -> dict:
    """Read a generated project and return its portable source-map document."""
    root = Path(project_dir).expanduser().resolve()
    gen_dir = find_gen_dir(root)

    def rel(path: Path) -> str:
        return path.relative_to(root).as_posix()

    files_info: dict[str, dict] = {}
    defs_by_addr: dict[int, list[dict]] = defaultdict(list)
    dispatch_by_addr: dict[int, list[dict]] = defaultdict(list)
    header_by_addr: dict[int, list[dict]] = defaultdict(list)
    dispatch_order: list[tuple[int, int]] = []
    declared_count: int | None = None

    units = sorted(p for p in gen_dir.glob("recomp_*.c")
                   if p.name != "recomp_dispatch.c" and p.is_file())
    for path in units:
        data = path.read_bytes()
        text = data.decode("utf-8", "replace")
        r = rel(path)
        files_info[r] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "lines": len(text.splitlines()),
        }
        for m in DEF_RE.finditer(text):
            address = int(m.group(2), 16)
            symbol = m.group(1)
            end = _body_end_line(text, m.end() - 1)
            defs_by_addr[address].append({
                "address": f"0x{address:08X}",
                "symbol": symbol,
                "kind": "fallback" if symbol.endswith("__lowlevel") else "entry",
                "file": r,
                "line": _line_at(text, m.start()),
                "end_line": end,
            })

    dispatch_path = gen_dir / "recomp_dispatch.c"
    dispatch_text = ""
    if dispatch_path.is_file():
        data = dispatch_path.read_bytes()
        dispatch_text = data.decode("utf-8", "replace")
        files_info[rel(dispatch_path)] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "lines": len(dispatch_text.splitlines()),
        }
        for m in DISPATCH_RE.finditer(dispatch_text):
            address = int(m.group(1), 16)
            line = _line_at(dispatch_text, m.start())
            dispatch_by_addr[address].append({
                "symbol": m.group(2), "file": rel(dispatch_path), "line": line})
            dispatch_order.append((address, line))
        count_match = COUNT_RE.search(dispatch_text)
        if count_match:
            declared_count = int(count_match.group(1))

    header_path = gen_dir / "recomp_funcs.h"
    if header_path.is_file():
        data = header_path.read_bytes()
        header_text = data.decode("utf-8", "replace")
        files_info[rel(header_path)] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "lines": len(header_text.splitlines()),
        }
        for m in HEADER_RE.finditer(header_text):
            address = int(m.group(2), 16)
            header_by_addr[address].append({
                "symbol": m.group(1), "file": rel(header_path),
                "line": _line_at(header_text, m.start())})

    addresses = sorted(set(defs_by_addr) | set(dispatch_by_addr))
    functions: list[dict] = []
    source_map: list[dict] = []
    for address in addresses:
        defs = sorted(defs_by_addr.get(address, []),
                      key=lambda d: (d["kind"] == "fallback", d["line"]))
        disps = dispatch_by_addr.get(address, [])
        primary_symbol = (disps[0]["symbol"] if disps else
                          (defs[0]["symbol"] if defs else None))
        primary = next((d for d in defs if d["symbol"] == primary_symbol),
                       defs[0] if defs else None)
        fallback = next((d for d in defs if d["kind"] == "fallback"), None)
        if fallback is primary:
            fallback = None
        header = header_by_addr.get(address, [])
        functions.append({
            "address": f"0x{address:08X}",
            "address_int": address,
            "symbol": primary_symbol,
            "definition": _definition_row(primary),
            "fallback_definition": _definition_row(fallback),
            "dispatch": (
                {"file": disps[0]["file"], "line": disps[0]["line"],
                 "symbol": disps[0]["symbol"]} if disps else None),
            "header": (
                {"file": header[0]["file"], "line": header[0]["line"],
                 "symbol": header[0]["symbol"]} if header else None),
            "definition_count": len(defs),
            "dispatch_count": len(disps),
        })
        if primary is not None:
            source_map.append({
                "guest_va": f"0x{address:08X}",
                "file": primary["file"],
                "line": primary["line"],
                "symbol": primary["symbol"],
            })

    mapped = sorted(set(defs_by_addr) & set(dispatch_by_addr))
    dropped = sorted(set(dispatch_by_addr) - set(defs_by_addr))
    unmatched_defs = sorted(set(defs_by_addr) - set(dispatch_by_addr))
    expected = len(dispatch_by_addr)
    coverage = {
        "mapped": len(mapped),
        "expected": expected,
        "definition_count": sum(len(v) for v in defs_by_addr.values()),
        "dispatch_entry_count": sum(len(v) for v in dispatch_by_addr.values()),
        "declared_dispatch_count": declared_count,
        "percent": round(100.0 * len(mapped) / expected, 2) if expected else 100.0,
        "dropped_addresses": [f"0x{a:08X}" for a in dropped],
        "unmatched_definition_addresses": [f"0x{a:08X}" for a in unmatched_defs],
    }

    material = [f"{path}\0{info['sha256']}" for path, info in sorted(files_info.items())]
    doc = {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "gen_dir": rel(gen_dir),
        "artifact_sha256": hashlib.sha256("\0".join(material).encode()).hexdigest(),
        "files": files_info,
        "functions": functions,
        "source_map": source_map,
        "coverage": coverage,
    }
    doc["violations"] = validate_source_map(doc, root)
    return doc


def _definition_row(defn: dict | None) -> dict | None:
    if defn is None:
        return None
    return {key: defn[key]
            for key in ("address", "symbol", "kind", "file", "line", "end_line")}


# ---------------------------------------------------------------------------
# Validation (named violations, never a bool)
# ---------------------------------------------------------------------------

def validate_source_map(document: dict, project_dir: Path | str) -> list[str]:
    """Return named geometry/completeness violations for a source-map document.

    Adapted from Stela's ``validate_source_map_geometry``: every referenced file
    must exist, every recorded line must be in range, each dispatch VA must
    have a definition and vice versa, a definition body must be balanced and
    non-empty, symbols must agree with their dispatch address, and the dispatch
    table must be strictly increasing in both address and line.  An empty list
    means the map is internally consistent; it is not a proof of semantic
    correctness of the generated C.
    """
    root = Path(project_dir).expanduser().resolve()
    violations: list[str] = []

    for path, info in sorted(document.get("files", {}).items()):
        target = root / path
        if not target.is_file():
            violations.append(f"file_missing: {path}")
            continue
        actual_lines = len(target.read_bytes().decode(
            "utf-8", "replace").splitlines())
        if int(info.get("lines", -1)) != actual_lines:
            violations.append(
                f"file_line_count_mismatch: {path} recorded {info.get('lines')}, "
                f"found {actual_lines}")

    for fn in document.get("functions", []):
        address = fn.get("address", "?")
        definition = fn.get("definition")
        dispatch = fn.get("dispatch")
        if dispatch and not definition:
            violations.append(
                f"missing_definition: {address} is dispatched but has no "
                f"void sub_XXXXXXXX definition")
        if definition and not dispatch:
            violations.append(
                f"missing_dispatch: {address} is defined but absent from the "
                f"dispatch table")
        if definition:
            start, end = definition.get("line"), definition.get("end_line")
            if end is None:
                violations.append(f"unbalanced_body: {address} in {definition.get('file')}")
            elif not isinstance(start, int) or end < start:
                violations.append(
                    f"invalid_body_span: {address} lines {start}-{end}")
        # One entry body plus its `sub_XXXXXXXX__lowlevel` fallback is the
        # expected high-level shape; two *entry* bodies are not.
        definition_count = int(fn.get("definition_count", 0))
        has_fallback = fn.get("fallback_definition") is not None
        if definition_count > 2 or (definition_count > 1 and not has_fallback):
            violations.append(
                f"duplicate_entry_definition: {address} has "
                f"{definition_count} definitions")
        if int(fn.get("dispatch_count", 0)) > 1:
            violations.append(
                f"duplicate_dispatch: {address} has "
                f"{fn['dispatch_count']} dispatch entries")
        if dispatch:
            sym_match = SYMBOL_ADDR_RE.match(str(dispatch.get("symbol", "")))
            if sym_match:
                encoded = int(sym_match.group(1), 16)
                try:
                    key = int(str(address), 16)
                except ValueError:
                    key = None
                if key is not None and encoded != key:
                    violations.append(
                        f"symbol_address_mismatch: {address} dispatches to "
                        f"{dispatch['symbol']} (encodes 0x{encoded:08X})")
        for row in (definition, dispatch, fn.get("header"),
                    fn.get("fallback_definition")):
            if not row:
                continue
            path, line = row.get("file"), row.get("line")
            info = document.get("files", {}).get(path)
            if info is None:
                violations.append(f"unregistered_file: {address} -> {path}")
            elif not isinstance(line, int) or not 1 <= line <= int(info["lines"]):
                violations.append(
                    f"line_out_of_range: {address} -> {path}:{line}")

    dispatch_text = (root / document.get("gen_dir", "") / "recomp_dispatch.c")
    order: list[tuple[int, int]] = []
    if dispatch_text.is_file():
        text = dispatch_text.read_bytes().decode("utf-8", "replace")
        for m in DISPATCH_RE.finditer(text):
            order.append((int(m.group(1), 16), _line_at(text, m.start())))
    for (addr_a, line_a), (addr_b, line_b) in zip(order, order[1:]):
        if addr_b <= addr_a:
            violations.append(
                f"non_monotonic_dispatch_address: 0x{addr_a:08X} then 0x{addr_b:08X}")
        if line_b <= line_a:
            violations.append(
                f"non_monotonic_dispatch_line: {line_a} then {line_b}")

    coverage = document.get("coverage", {})
    declared = coverage.get("declared_dispatch_count")
    if declared is not None and int(declared) != len(order):
        violations.append(
            f"dispatch_count_mismatch: declared {declared}, found {len(order)}")
    return violations


def coverage(document: dict) -> dict:
    """Return a compact coverage summary already embedded in the document."""
    return dict(document.get("coverage", {}))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Map generated recompilation C back to guest x86 addresses.")
    parser.add_argument(
        "--project-dir", required=True,
        help="generated project directory (or its src/recomp/gen directory)")
    parser.add_argument(
        "--output", help="write the JSON map here (default: stdout)")
    parser.add_argument("--indent", type=int, default=2,
                        help="JSON indent (default: 2; use 0 for compact)")
    parser.add_argument(
        "--fail-on-violation", action="store_true",
        help="exit 1 when validation reports any named violation")
    args = parser.parse_args(argv)

    try:
        document = build_source_map(args.project_dir)
    except (OSError, ValueError) as exc:
        print(f"source_map: {exc}", file=sys.stderr)
        return 2

    text = json.dumps(document, indent=args.indent or None,
                      sort_keys=True) + "\n"
    if args.output:
        out = Path(args.output).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"[source_map] {len(document['functions'])} functions, "
              f"{len(document['violations'])} violations -> {out}",
              file=sys.stderr)
    else:
        sys.stdout.write(text)
    if args.fail_on_violation and document["violations"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
