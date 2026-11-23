#!/usr/bin/env python3
"""Typed, fail-closed refusal reporting for an autorecomp project.

An autorecomp project can look finished while still carrying three different
kinds of unresolved work:

  * a generated C unit with a ``/* ERROR lifting sub_XXXXXXXX: ... */`` stub;
  * an import the loader could not bind (``[pe] unresolved import NAME`` in a
    runtime log, or ``unresolved_imports`` in a JSON report);
  * no native binary at all (a build was skipped, failed or was cleaned).

None of those is a soft warning: each one means the project must not be
presented as accepted. This tool reads those *observed* artifacts and reduces
them to one JSON document with named causes and counts. It never runs the
game, rebuilds anything, or infers success from missing evidence.

Design pattern (not copied code): Stela's ``UnsupportedReason`` is a closed
set of typed reasons carrying guest source context, with no interpreter
fallback -- an unrepresentable operation is a compile error. Here the closed
set is :class:`RefusalCause`, each sighting is a :class:`Refusal` carrying a
project-relative source and line, and the aggregate verdict is fail-closed:
any cause (or any unreadable source) refuses the project. Stela is
GPL-2.0-or-later; this module is original Python and uses only the audit notes
in ``docs/stela-audit/``, never Stela source.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

SCHEMA = "gamerecomp.refusals.v1"
VERDICT_REFUSED = "REFUSED"
VERDICT_ACCEPTED = "ACCEPTED"
EXIT_ACCEPTED = 0
EXIT_REFUSED = 1

MANIFEST = "manifest.json"
FUNCTIONS_REPORT = "reports/functions.json"

# /* ERROR lifting sub_00401000: <exception text> */
_LIFT_RE = re.compile(
    r"/\*\s*ERROR\s+lifting\s+(?P<symbol>[A-Za-z_]\w*)\s*:\s*"
    r"(?P<message>.*?)\s*\*/", re.S)
_SUB_RE = re.compile(r"^sub_([0-9A-Fa-f]{1,16})$")
_ADDRESS_RE = re.compile(r"^(?:0x)?([0-9A-Fa-f]{1,16})$")

# [pe] unresolved import module!name
_IMPORT_RE = re.compile(r"\[pe\]\s+unresolved import\s+(?P<name>\S+)")
# [pe] bound 59 imports, 86 unresolved
_BOUND_RE = re.compile(
    r"\[pe\]\s+bound\s+(?P<bound>\d+)\s+imports?,\s+"
    r"(?P<unresolved>\d+)\s+unresolved")
# [host] game.exe booted: entry 0x00400000, 86 unresolved imports
_BOOTED_RE = re.compile(
    r"booted:\s+entry\s+(?P<entry>0x[0-9A-Fa-f]+),\s+"
    r"(?P<unresolved>\d+)\s+unresolved imports")

_COUNT_KEYS = ("unresolved_import_count", "unresolved_imports_count",
               "unresolved_iat_imports", "iat_unresolved")


class RefusalCause(str, Enum):
    """The closed set of reasons a project is refused.

    The members are stable wire names in the JSON report. Adding a cause is an
    API change; interpreting an unknown name must not degrade to acceptance.
    """

    LIFT_ERROR = "LIFT_ERROR"
    UNRESOLVED_IMPORT = "UNRESOLVED_IMPORT"
    MISSING_NATIVE_BINARY = "MISSING_NATIVE_BINARY"


@dataclass(frozen=True)
class Refusal:
    """One typed sighting, anchored to a source and (where possible) a line.

    ``key`` is the stable identity used for de-duplication: a guest address for
    lift errors, an import name for imports, ``native-binary`` for the build
    artifact. ``detail`` holds the human-readable evidence.
    """

    cause: RefusalCause
    key: str
    detail: dict
    source: str
    line: int | None = None

    def as_item(self) -> dict:
        item = {"key": self.key}
        item.update(self.detail)
        item["source"] = self.source
        if self.line is not None:
            item["line"] = self.line
        return item


def _line_no(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _norm_addr(value) -> str | None:
    """'0x00401000', '00401000' or int -> '0x00401000'; else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return f"0x{value:08X}" if value >= 0 else None
    match = _ADDRESS_RE.match(str(value).strip()) if value is not None else None
    return f"0x{int(match.group(1), 16):08X}" if match else None


def _rel(project: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project))
    except ValueError:
        return str(path)


# -- evidence parsers ---------------------------------------------------

def parse_lift_errors(text: str | None, source: str) -> list[Refusal]:
    """Refusals from generated C ``ERROR lifting`` markers in ``text``.

    Pure parser: it reports only markers actually present, with their line
    number so a reader can locate the emitting stub.
    """
    if not text:
        return []
    out = []
    for match in _LIFT_RE.finditer(text):
        symbol = match.group("symbol")
        address = _norm_addr(symbol[4:]) if symbol.startswith("sub_") else None
        message = " ".join(match.group("message").split())
        out.append(Refusal(
            RefusalCause.LIFT_ERROR, address or symbol,
            {"address": address, "symbol": symbol, "message": message},
            source, _line_no(text, match.start())))
    return out


def parse_unresolved_imports_text(text: str | None, source: str) -> tuple[list[Refusal], list[dict]]:
    """Named unresolved imports and aggregate loader counts from a log.

    Returns ``(refusals, aggregates)``. Aggregate numbers are kept separate so
    a log that names two imports out of 86 is not miscounted as three causes.
    """
    if not text:
        return [], []
    refusals, aggregates = [], []
    for match in _IMPORT_RE.finditer(text):
        name = match.group("name")
        refusals.append(Refusal(
            RefusalCause.UNRESOLVED_IMPORT, name,
            {"import": name, "evidence": match.group(0).strip()},
            source, _line_no(text, match.start())))
    for kind, regex in (("iat-bind", _BOUND_RE), ("boot", _BOOTED_RE)):
        for match in regex.finditer(text):
            count = int(match.group("unresolved"))
            if count:
                aggregates.append({"count": count, "kind": kind,
                                   "source": source,
                                   "line": _line_no(text, match.start())})
    return refusals, aggregates


def parse_unresolved_imports_report(data, source: str) -> tuple[list[Refusal], list[dict]]:
    """Same evidence as above, read from a decoded JSON report.

    Recognised keys: ``unresolved_imports`` (a list of names or of objects with
    ``import``/``name``) and the integer count keys in ``_COUNT_KEYS``. Nested
    objects are walked, so ``reports/run.json`` and a game-index report both
    work without a schema-specific branch.
    """
    refusals, aggregates = [], []

    def visit(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                here = path + (str(key),)
                if key == "unresolved_imports" and isinstance(value, list):
                    for item in value:
                        name = None
                        if isinstance(item, str):
                            name = item
                        elif isinstance(item, dict):
                            name = item.get("import") or item.get("name")
                        if isinstance(name, str) and name:
                            refusals.append(Refusal(
                                RefusalCause.UNRESOLVED_IMPORT, name,
                                {"import": name, "report_path": ".".join(here)},
                                source))
                    continue
                if key in _COUNT_KEYS and isinstance(value, int) and not isinstance(value, bool):
                    if value:
                        aggregates.append({"count": value, "kind": key,
                                           "source": source,
                                           "path": ".".join(here)})
                    continue
                visit(value, here)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, path + (str(index),))

    visit(data, ())
    return refusals, aggregates


def parse_missing_native(project: Path) -> tuple[list[Refusal], dict]:
    """Refuse when no recorded or conventional native binary exists.

    ``manifest.json`` is authoritative when it records a binary path. The
    conventional ``build/<name>`` spellings (native, .exe, .wasm) are also
    checked, so a stale manifest path does not hide a binary that is present.
    """
    project = Path(project)
    candidates: list[Path] = []
    names: set[str] = set()
    recorded = None
    manifest_path = project / MANIFEST
    manifest = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(errors="replace"))
        except (OSError, ValueError, TypeError):
            manifest = None
    if isinstance(manifest, dict):
        binary = manifest.get("binary")
        if isinstance(binary, dict) and isinstance(binary.get("path"), str) and binary["path"]:
            recorded = Path(binary["path"])
            if not recorded.is_absolute():
                recorded = project / recorded
            candidates.append(recorded)
        game = manifest.get("game")
        if isinstance(game, str) and game:
            names.add(Path(game).stem)

    project_json = project / "project.json"
    if project_json.is_file():
        try:
            meta = json.loads(project_json.read_text(errors="replace"))
            name = meta.get("name") if isinstance(meta, dict) else None
            if isinstance(name, str) and name:
                names.add(name)
        except (OSError, ValueError, TypeError):
            pass
    names.add(project.name)

    build = project / "build"
    for name in sorted(names):
        candidates.extend((build / name, build / (name + ".exe"),
                           build / (name + ".wasm")))
    checked = list(dict.fromkeys(candidates))
    existing = [path for path in checked if path.is_file()]
    info = {"manifest_recorded": str(recorded) if recorded else None,
            "checked": [str(path) for path in checked],
            "existing": [str(path) for path in existing]}
    if existing:
        return [], info
    refusal = Refusal(
        RefusalCause.MISSING_NATIVE_BINARY, "native-binary",
        {"reason": "no native binary exists for this project",
         "checked": info["checked"],
         "manifest_recorded": info["manifest_recorded"]},
        MANIFEST if manifest_path.is_file() else "build/")
    return [refusal], info


# -- aggregation --------------------------------------------------------

def _empty_cause(name: str) -> dict:
    return {"cause": name, "count": 0, "occurrences": 0,
            "items": [], "sources": []}


def _finalize(report: dict, refusals: list[Refusal], aggregates: list[dict]) -> None:
    order = {cause: index for index, cause in enumerate(RefusalCause)}
    grouped = {}
    for refusal in sorted(refusals,
                          key=lambda r: (order[r.cause], r.key, r.source, r.line or 0)):
        name = refusal.cause.value
        entry = grouped.setdefault(name, _empty_cause(name))
        item = next((it for it in entry["items"] if it["key"] == refusal.key), None)
        if item is None:
            entry["count"] += 1
            entry["items"].append(refusal.as_item())
        else:
            occurrences = item.get("occurrences", 1) + 1
            if refusal.line is not None and "line" not in item:
                # A later sighting with source context is more useful than an
                # earlier count-only one: keep the richer evidence, not just
                # the first line that happened to mention the same key.
                replacement = refusal.as_item()
                replacement["occurrences"] = occurrences
                entry["items"][entry["items"].index(item)] = replacement
            else:
                item["occurrences"] = occurrences
        entry["occurrences"] += 1
        if refusal.source not in entry["sources"]:
            entry["sources"].append(refusal.source)

    report["causes"] = {cause.value: grouped.get(cause.value,
                                                 _empty_cause(cause.value))
                        for cause in RefusalCause}
    report["aggregates"] = aggregates
    reported = [int(a["count"]) for a in aggregates
                if isinstance(a.get("count"), int) and not isinstance(a["count"], bool)]
    report["reported_unresolved"] = max(reported) if reported else 0

    unresolved = report["causes"][RefusalCause.UNRESOLVED_IMPORT.value]
    if unresolved["count"] == 0 and report["reported_unresolved"]:
        # The loader counted unresolved imports but did not name them. Refuse
        # on the number it printed rather than inventing a name.
        unresolved["count"] = report["reported_unresolved"]
        unresolved["items"].append({
            "key": f"reported:{report['reported_unresolved']}",
            "import": None, "count": report["reported_unresolved"],
            "source": "aggregate",
            "note": "count reported by loader/runtime without import names"})
        unresolved["occurrences"] += 1
        unresolved["sources"].append("aggregate")

    report["counts"] = {name: entry["count"] for name, entry in report["causes"].items()}
    report["total"] = sum(report["counts"].values())
    report["refused"] = bool(report["total"] or report["error"])
    report["verdict"] = VERDICT_REFUSED if report["refused"] else VERDICT_ACCEPTED


def collect_refusals(project) -> dict:
    """Read one autorecomp project and return its structured refusal report.

    Sources are optional: a source-only project legitimately has no run log.
    The project directory itself is not optional -- if it is missing the
    report is refused with an ``error``, never silently accepted.
    """
    project = Path(project)
    report = {
        "schema": SCHEMA,
        "project": str(project),
        "verdict": VERDICT_REFUSED,
        "fail_closed": True,
        "refused": True,
        "counts": {cause.value: 0 for cause in RefusalCause},
        "causes": {},
        "total": 0,
        "reported_unresolved": 0,
        "sources_scanned": [],
        "native_binary": {},
        "aggregates": [],
        "error": None,
    }
    if not project.is_dir():
        report["error"] = f"project directory does not exist: {project}"
        _finalize(report, [], [])
        return report

    refusals: list[Refusal] = []
    aggregates: list[dict] = []
    seen_sources = set()

    def record_source(path: Path, kind: str) -> None:
        rel = _rel(project, path)
        if (rel, kind) not in seen_sources:
            seen_sources.add((rel, kind))
            report["sources_scanned"].append({"path": rel, "kind": kind,
                                              "status": "read"})

    def fail_source(path: Path, kind: str, exc: Exception) -> None:
        # An artifact we found but could not read is a gap in the evidence:
        # fail closed instead of treating it as "no refusal found".
        rel = _rel(project, path)
        message = f"unreadable {kind} {rel}: {type(exc).__name__}: {exc}"
        report["error"] = (report["error"] + "; " + message) if report["error"] else message

    # 1. generated C: the authoritative lift-failure markers.
    gen_dir = project / "src" / "recomp" / "gen"
    c_files = sorted(gen_dir.glob("recomp_*.c")) if gen_dir.is_dir() else []
    for path in c_files:
        try:
            text = path.read_text(errors="replace")
        except OSError as exc:
            fail_source(path, "generated-c", exc)
            continue
        refusals.extend(parse_lift_errors(text, _rel(project, path)))
        record_source(path, "generated-c")

    # 2. the function inventory also marks lift failures by category and
    #    address; de-duplication below merges it with the C markers.
    functions = project / FUNCTIONS_REPORT
    if functions.is_file():
        try:
            data = json.loads(functions.read_text(errors="replace"))
            entries = data.get("functions", []) if isinstance(data, dict) else []
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict) or entry.get("category") != "UNRESOLVED":
                    continue
                address = _norm_addr(entry.get("address"))
                key = address or str(entry.get("name") or "unknown")
                refusals.append(Refusal(
                    RefusalCause.LIFT_ERROR, key,
                    {"address": address, "name": entry.get("name"),
                     "category": "UNRESOLVED"}, FUNCTIONS_REPORT))
            record_source(functions, "functions-report")
        except (OSError, ValueError, TypeError) as exc:
            fail_source(functions, "functions-report", exc)

    # 3. runtime/loader logs: unresolved imports and aggregate bind counts.
    log_dir = project / "logs"
    log_files = (sorted(path for path in log_dir.rglob("*.log") if path.is_file())
                 if log_dir.is_dir() else [])
    for path in log_files:
        try:
            text = path.read_text(errors="replace")
        except OSError as exc:
            fail_source(path, "log", exc)
            continue
        found, counted = parse_unresolved_imports_text(text, _rel(project, path))
        refusals.extend(found)
        aggregates.extend(counted)
        record_source(path, "log")

    # 4. JSON reports: run.json and any other report naming unresolved imports.
    reports_dir = project / "reports"
    report_files = (sorted(path for path in reports_dir.rglob("*.json") if path.is_file())
                    if reports_dir.is_dir() else [])
    for path in report_files:
        try:
            data = json.loads(path.read_text(errors="replace"))
        except (OSError, ValueError, TypeError) as exc:
            fail_source(path, "report", exc)
            continue
        found, counted = parse_unresolved_imports_report(data, _rel(project, path))
        refusals.extend(found)
        aggregates.extend(counted)
        record_source(path, "report")

    # 5. the build artifact itself.
    native_refusals, report["native_binary"] = parse_missing_native(project)
    refusals.extend(native_refusals)

    _finalize(report, refusals, aggregates)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="report typed, fail-closed refusals for an autorecomp project")
    parser.add_argument("project", type=Path, help="autorecomp project directory")
    parser.add_argument("--json", type=Path, metavar="PATH",
                        help="also write the JSON report to PATH")
    parser.add_argument("--quiet", action="store_true",
                        help="do not print the JSON report to stdout")
    args = parser.parse_args(argv)

    report = collect_refusals(args.project)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text)
    if not args.quiet:
        sys.stdout.write(text)
    return EXIT_REFUSED if report["refused"] else EXIT_ACCEPTED


if __name__ == "__main__":
    sys.exit(main())
