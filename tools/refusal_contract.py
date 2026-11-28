#!/usr/bin/env python3
"""Fail-closed checker for CLI refusal records of unsupported input classes.

The GameReComp CLI is expected to *name* the guest input it refuses and to
exit non-zero when that input is outside the supported set (native PE32/x86).
This tool never runs a binary, lifts code or inspects game assets: it validates
the refusal records a caller supplies through its CLI (``--records FILE`` and
or repeated ``--record JSON``). It reports what those records demonstrate and
nothing more.

Five input classes are required, in this fixed order: x64, .NET, Win16, ELF and
Mach-O. For each class a record must

  * exist (a missing record is a FAIL, never a silent PASS by absence);
  * carry an explicit, named diagnostic for that class -- "unsupported input",
    "error", "invalid format" and "failed" are vague and do not count;
  * carry a non-zero exit code (exit 0 is a silent success, even if a message
    was printed).

Conflicting records for one class are ambiguous and also FAIL. The verdict is
deterministic: classes, records and reasons are always emitted in the same
order, with no timestamps or environment-dependent values. A malformed or
absent input produces a FAIL report, not an empty PASS.

Usage:
    python3 tools/refusal_contract.py --records records.json
    python3 tools/refusal_contract.py --record '{"input_class":"x64",...}' ...
    echo '{"records":[...]}' | python3 tools/refusal_contract.py --records -

Exit codes: 0 = every class has an explicit, named, non-zero refusal;
1 = at least one class failed; 2 = CLI usage error (argparse).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA = "gamerecomp.refusal-contract.v1"
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
EXIT_PASS = 0
EXIT_FAIL = 1

# Canonical wire names and the only order in which they are ever reported.
INPUT_CLASSES = ("x64", ".NET", "Win16", "ELF", "Mach-O")

# Accepted spellings after _norm(); the canonical name maps to itself.
_ALIASES = {
    "x64": ("x64", "x86 64", "amd64", "pe32+", "pe32plus", "pe32 plus",
            "x64 pe", "x86 64 pe", "pe32+ image", "64 bit pe"),
    ".NET": (".net", "dotnet", "clr", "managed", "managed pe",
             "clr assembly", ".net assembly", ".net clr"),
    "Win16": ("win16", "win 16", "16 bit", "16bit", "ne", "ne exe",
              "ne image", "new executable", "win16 ne", "win 3.1"),
    "ELF": ("elf", "elf32", "elf64", "elf executable", "elf binary"),
    "Mach-O": ("mach o", "macho", "fat mach o", "macho fat",
               "universal binary"),
}

# A diagnostic only counts when it contains a named marker for its class.
# Substring/word-boundary patterns keep "unsupported input", "self" and an
# empty string from ever being mistaken for evidence.
_TOKENS = {
    "x64": (r"\bx64\b", r"\bx86 64\b", r"\bamd64\b", r"pe32\+",
            r"\bpe32plus\b", r"\bpe32 plus\b"),
    ".NET": (r"\.net\b", r"\bdotnet\b", r"\bclr\b", r"\bmanaged\b"),
    "Win16": (r"\bwin ?16\b", r"\b16 ?bit\b", r"\b16bit\b", r"\bne exe\b",
              r"\bne executable\b", r"\bnew executable\b"),
    "ELF": (r"\belf",),
    "Mach-O": (r"\bmach-?o\b", r"\bmach o\b", r"\bmacho\b"),
}
_COMPILED = {name: tuple(re.compile(p) for p in patterns)
             for name, patterns in _TOKENS.items()}
_CLASS_BY_ALIAS = {alias: canonical
                   for canonical, aliases in _ALIASES.items()
                   for alias in aliases}

_RECORD_KEYS = ("input_class", "diagnostic", "exit_code")


def _norm(value: str) -> str:
    """Lower-case, collapse whitespace/underscore/hyphen; keep dots and plus."""
    return re.sub(r"[\s_-]+", " ", str(value).strip().lower())


def _label(record: dict, index: int) -> str:
    for key in ("label", "case", "id", "name"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"record[{index}]"


def _parse_exit(value) -> tuple[int | None, str | None]:
    """Return (exit_code, error). bool is rejected; numeric strings accepted."""
    if isinstance(value, bool) or value is None:
        return None, "missing or non-integer exit_code"
    if isinstance(value, int):
        return value, None
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value.strip()), None
    return None, "missing or non-integer exit_code"


def _named_classes(diagnostic: str) -> list[str]:
    """Required classes explicitly named by a diagnostic, in canonical order."""
    text = _norm(diagnostic)
    return [name for name in INPUT_CLASSES
            if any(pattern.search(text) for pattern in _COMPILED[name])]


def _signature(record: dict) -> tuple:
    """Stable identity used to detect conflicting records for one class."""
    return (record["exit_code"], _norm(record["diagnostic"]))


def evaluate(records, errors=(), sources=()) -> dict:
    """Reduce supplied records to a deterministic, fail-closed verdict.

    ``records`` is a sequence of JSON objects. ``errors`` holds top-level
    input problems (missing file, bad JSON, non-object record) that must also
    force a FAIL. ``sources`` is only descriptive metadata.
    """
    by_class: dict[str, list[dict]] = {name: [] for name in INPUT_CLASSES}
    reasons: list[str] = []
    for error in errors:
        reasons.append(f"input: {error}")

    seen = 0
    for index, raw in enumerate(records):
        seen += 1
        if not isinstance(raw, dict):
            reasons.append(f"input: record[{index}]: not a JSON object")
            continue
        label = _label(raw, index)
        cls_raw = raw.get("input_class")
        cls = _CLASS_BY_ALIAS.get(_norm(cls_raw)) if isinstance(cls_raw, str) else None
        if cls is None:
            detail = repr(cls_raw) if cls_raw not in (None, "") else "empty"
            reasons.append(f"input: {label}: unrecognized input_class {detail}")
            continue

        exit_code, exit_error = _parse_exit(raw.get("exit_code"))
        diagnostic = raw.get("diagnostic")
        if not isinstance(diagnostic, str) or not diagnostic.strip():
            diagnostic = ""
        by_class[cls].append({
            "label": label,
            "input_class": cls_raw,
            "diagnostic": diagnostic,
            "exit_code": exit_code,
            "_exit_error": exit_error,
            "_diagnostic_present": bool(diagnostic.strip()),
        })

    classes: dict[str, dict] = {}
    passed = 0
    for cls in INPUT_CLASSES:
        group = by_class[cls]
        class_reasons: list[str] = []
        if not group:
            class_reasons.append(
                f"missing_record: no refusal record supplied for {cls}")
        else:
            signatures = {_signature(rec) for rec in group}
            if len(signatures) > 1:
                detail = ", ".join(
                    f"exit={sig[0]!r}/diagnostic={sig[1]!r}"
                    for sig in sorted(signatures, key=lambda s: (str(s[0]), s[1])))
                class_reasons.append(
                    f"ambiguous_conflict: {cls} has conflicting records ({detail})")
            for rec in group:
                label = rec["label"]
                if rec["_exit_error"]:
                    class_reasons.append(f"invalid_exit_code: {label}: {rec['_exit_error']}")
                elif rec["exit_code"] == 0:
                    class_reasons.append(
                        f"silent_success: {label} exited 0 for unsupported input {cls}")
                if not rec["_diagnostic_present"]:
                    class_reasons.append(
                        f"missing_diagnostic: {label} has no diagnostic for {cls}")
                    continue
                named = _named_classes(rec["diagnostic"])
                if cls not in named:
                    others = [name for name in named if name != cls]
                    if others:
                        class_reasons.append(
                            f"wrong_class_named: {label} names {', '.join(others)} "
                            f"instead of {cls}")
                    else:
                        class_reasons.append(
                            f"vague_or_unnamed_diagnostic: {label} does not name {cls}")
        # De-duplicate, then sort for a stable, readable order.
        class_reasons = sorted(dict.fromkeys(class_reasons))
        if not class_reasons:
            passed += 1
        reasons.extend(f"{cls}: {reason}" for reason in class_reasons)
        classes[cls] = {
            "status": VERDICT_FAIL if class_reasons else VERDICT_PASS,
            "records": [{
                "label": rec["label"],
                "input_class": rec["input_class"],
                "diagnostic": rec["diagnostic"],
                "exit_code": rec["exit_code"],
            } for rec in sorted(group, key=lambda r: r["label"])],
            "reasons": class_reasons,
        }

    ok = not reasons
    return {
        "schema": SCHEMA,
        "verdict": VERDICT_PASS if ok else VERDICT_FAIL,
        "fail_closed": True,
        "passed": ok,
        "counts": {"pass": passed, "fail": len(INPUT_CLASSES) - passed},
        "input_classes": list(INPUT_CLASSES),
        "records_seen": seen,
        "sources": list(sources),
        "classes": classes,
        "reasons": reasons,
        "error": None,
    }


def _flatten(doc, source: str, errors: list[str]) -> list:
    """Accept a list of records, an object with 'records', or one record."""
    if isinstance(doc, dict):
        if "records" in doc:
            value = doc["records"]
            if isinstance(value, list):
                return list(value)
            errors.append(f"{source}: 'records' is not a list")
            return []
        if any(key in doc for key in _RECORD_KEYS):
            return [doc]
        errors.append(f"{source}: object has neither 'records' nor a refusal record")
        return []
    if isinstance(doc, list):
        return list(doc)
    errors.append(f"{source}: expected a JSON object or list")
    return []


def load_records(records_path=None, inline=()) -> tuple[list, list[str], list[str]]:
    """Read caller-supplied records. Fail closed on any input problem."""
    records: list = []
    sources: list[str] = []
    errors: list[str] = []
    if records_path is not None:
        source = "<stdin>" if str(records_path) == "-" else str(records_path)
        try:
            text = (sys.stdin.read() if str(records_path) == "-"
                    else Path(records_path).read_text(encoding="utf-8"))
        except OSError as exc:
            errors.append(f"{source}: unreadable records ({type(exc).__name__}: {exc})")
            text = None
        if text is not None:
            try:
                doc = json.loads(text)
            except (ValueError, TypeError) as exc:
                errors.append(f"{source}: invalid JSON ({exc})")
            else:
                records.extend(_flatten(doc, source, errors))
                sources.append(source)
    for index, raw in enumerate(inline, 1):
        source = f"--record[{index}]"
        try:
            doc = json.loads(raw)
        except (ValueError, TypeError) as exc:
            errors.append(f"{source}: invalid JSON ({exc})")
            continue
        records.extend(_flatten(doc, source, errors))
        sources.append(source)
    if records_path is None and not inline:
        errors.append("no refusal records supplied")
    return records, sources, errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", type=Path, metavar="PATH",
                    help="JSON file of refusal records ('-' reads stdin)")
    ap.add_argument("--record", action="append", default=[], metavar="JSON",
                    help="one inline JSON refusal record (repeatable)")
    ap.add_argument("--json", type=Path, metavar="PATH",
                    help="also write the JSON report to PATH")
    ap.add_argument("--quiet", action="store_true",
                    help="do not print the report or summary to stdout")
    args = ap.parse_args(argv)

    records, sources, errors = load_records(args.records, args.record)
    report = evaluate(records, errors=errors, sources=sources)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text)
    if not args.quiet:
        print(f"refusal-contract: {report['verdict']} "
              f"({report['counts']['pass']}/{len(INPUT_CLASSES)} classes pass, "
              f"{len(report['reasons'])} reasons)")
        for reason in report["reasons"]:
            print(f"  FAIL {reason}")
    return EXIT_PASS if report["passed"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
