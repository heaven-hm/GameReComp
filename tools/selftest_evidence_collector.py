#!/usr/bin/env python3
"""Fail-closed collector for CLI-supplied ``build_selftests.sh`` evidence.

The tool reads a log (or a JSON summary) supplied on the command line and
counts the four status lines that ``runtime/win32hle/build_selftests.sh``
emits::

    ok    alpha_selftest.c
    skip  beta_selftest.c (no OpenGL context)
    FAIL  gamma_selftest.c
    BUILD FAIL  delta_selftest.c

Only those summary lines are counted. The per-check ``ok ...`` lines a
selftest prints into its own output (shown by the script after a ``FAIL``) are
not selftest outcomes and are ignored; they can never inflate the ``ok``
count. Every recognised test appears exactly once in a log: a duplicate, a
summary line with junk after the test name, a ``skip`` without a reason, a
claim that a game passed, or any unreadable/empty input is refused with exit
code 2 and no record is written.

A JSON summary may be supplied instead of a log. Its ``events`` list is
re-counted from scratch; a contradictory ``counts`` block, an illegal status,
or a claimed PASS/game promotion is refused. Supplying ``--log`` and
``--summary`` together additionally re-derives the log SHA-256 and compares
every summary event with the log bytes, so stale/tampered summaries are
detected rather than trusted.

Nothing here runs a game. A build or unit selftest is build/unit evidence
only: the record always carries ``game_evidence.verdict = NOT TESTED`` and
``promotion = false``. There is no code path that promotes anything to a game
PASS, however clean the selftest counts are.

Usage::

    python3 -m tools.selftest_evidence_collector --log <build-selftests.log> \
        [--out <record.json>] [--label <label>] [--expect-sha256 <hex>] [--strict]
    python3 -m tools.selftest_evidence_collector --summary <summary.json> \
        [--out <record.json>] [--strict]
    python3 -m tools.selftest_evidence_collector --log <log> --summary <json> \
        [--out <record.json>]
    python3 -m tools.selftest_evidence_collector --verify <record.json> \
        [--log <original.log>]

Exit codes: 0 = record collected (or record verified), 1 = selftest failures
under ``--strict`` or a failed ``--verify``, 2 = unparsable/empty/tampered
input or an I/O error. Failures are printed to stderr; no partial record is
ever emitted.
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
GENERATOR = "tools/selftest_evidence_collector.py"

# Verbatim tokens emitted by build_selftests.sh. Nothing is normalised away:
# "BUILD FAIL" is a build outcome, never a generic unit-test failure.
STATUSES = ("ok", "FAIL", "BUILD FAIL", "skip")
COUNT_KEYS = STATUSES + ("total",)
GAME_VERDICT = "NOT TESTED"

# A status line names exactly one generated selftest source file. The strict
# grammar keeps prose such as "ok   DirectDrawCreate -> DD_OK" out of the counts.
SUMMARY_LINE = re.compile(
    r"^(ok|FAIL|BUILD FAIL|skip)[ \t]+"
    r"([A-Za-z0-9_.-]+_selftest\.c)"
    r"(?:[ \t]+\(([^()\r\n]*)\))?[ \t]*$")
STATUS_WORD = re.compile(r"^(?:ok|FAIL|BUILD FAIL|skip|pass)[ \t]+", re.IGNORECASE)
NEAR_MISS = re.compile(
    r"^(?:[A-Za-z0-9_.-]+_selftest\.c)(?:[ \t]+\([^()]*\))?[ \t]*$")
PROMOTION_CLAIM = re.compile(
    r"^\s*(?:game[ _-]*)?(?:verdict|promotion)\s*[:=]\s*pass\b"
    r"|^\s*game[ _-]*pass\b"
    r"|^\s*pass\s+(?:game|for\s+game)\b",
    re.IGNORECASE)
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+_selftest\.c$")
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

LIMITS = (
    "Only build_selftests.sh summary lines are counted; per-check 'ok' lines "
    "printed by an individual selftest are not selftest outcomes.",
    "Skip reasons are the verbatim text in parentheses on the skip line; a "
    "skip without a reason is refused.",
    "No game, engine or execution oracle is run. Build and unit selftests "
    "never promote a game to PASS; game_evidence is always NOT TESTED.",
    "Counts are re-derived from the supplied events; a contradictory counts "
    "block, duplicate test, PASS claim or hash mismatch is refused.",
    "No clock, environment, machine path or network value enters the record; "
    "the same supplied bytes and arguments yield byte-identical JSON.",
)


class EvidenceError(ValueError):
    """Unparsable, empty or tampered input; carries every finding."""

    def __init__(self, *findings: str):
        self.findings = list(findings) or ["unparsable selftest evidence"]
        super().__init__("; ".join(self.findings))


def _fail(*findings: str) -> None:
    raise EvidenceError(*findings)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(record: dict) -> str:
    """Canonical, timestamp-free JSON: sorted keys, fixed indent, one newline."""
    return json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _valid_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def count_events(events: list[dict]) -> dict[str, int]:
    """Re-derive counts from events; no supplied number is ever trusted."""
    counts = {status: 0 for status in STATUSES}
    for event in events:
        counts[event["status"]] += 1
    counts["total"] = len(events)
    return counts


def _event_tuple(event: dict) -> tuple[str, str, str | None]:
    return event["name"], event["status"], event["reason"]


def sorted_event_tuples(events: list[dict]) -> list[tuple[str, str, str | None]]:
    return sorted(_event_tuple(event) for event in events)


# ---------------------------------------------------------------------------
# log parsing
# ---------------------------------------------------------------------------
def parse_log_text(text: str) -> list[dict]:
    """Parse summary lines from a build_selftests.sh log, fail closed.

    Returns one event per selftest with its 1-based line number. Prose lines
    (compiler warnings, failing selftests' internal ``ok`` checks) are ignored;
    a malformed status line, a duplicate test, a reason-less skip or a game
    PASS claim raises ``EvidenceError``.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        _fail("empty log: no text to parse")
    bad = CONTROL.search(text)
    if bad is not None:
        _fail(f"line 1+: control character U+{ord(bad.group()):04X} in log "
              "(bytes are not a parsable text log)")

    events: list[dict] = []
    seen: dict[str, dict] = {}
    for lineno, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        match = SUMMARY_LINE.match(line)
        if match is not None:
            status, name, reason = match.groups()
            if status == "skip":
                if reason is None or not reason.strip():
                    _fail(f"line {lineno}: skip for {name} has no reason")
                reason = reason.strip()
            elif reason is not None:
                _fail(f"line {lineno}: {status} for {name} has unexpected "
                      "parenthesised text")
            if name in seen:
                previous = seen[name]
                _fail(f"line {lineno}: duplicate {name}: {previous['status']} "
                      f"at line {previous['line']} and {status} at line "
                      f"{lineno}; log is contradictory")
            event = {"name": name, "status": status, "reason": reason,
                     "line": lineno}
            events.append(event)
            seen[name] = event
            continue
        if PROMOTION_CLAIM.search(line):
            _fail(f"line {lineno}: log claims game PASS; a build/unit selftest "
                  "cannot promote a game")
        word = STATUS_WORD.match(line)
        if word is not None and NEAR_MISS.fullmatch(line[word.end():].strip()):
            _fail(f"line {lineno}: malformed status line {line.strip()!r}; "
                  "expected one of ok/FAIL/BUILD FAIL/skip then a selftest name")
    if not events:
        _fail("no ok/FAIL/BUILD FAIL/skip summary lines in log; refusing to "
              "emit counts")
    return events


def parse_log_bytes(data: bytes) -> list[dict]:
    """Decode a caller-supplied log as strict UTF-8, then parse it."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        _fail(f"log is not UTF-8 text: {exc}")
    return parse_log_text(text)


# ---------------------------------------------------------------------------
# JSON summary parsing
# ---------------------------------------------------------------------------
def _parse_summary_event(raw: Any, index: int) -> dict:
    where = f"events[{index}]"
    if not isinstance(raw, dict):
        _fail(f"{where}: expected an object")
    name, status = raw.get("name"), raw.get("status")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        _fail(f"{where}: name {name!r} is not a *_selftest.c filename")
    if status not in STATUSES:
        _fail(f"{where}: status {status!r} is not one of {', '.join(STATUSES)}")
    reason = raw.get("reason")
    if status == "skip":
        if not isinstance(reason, str) or not reason.strip():
            _fail(f"{where}: skip for {name} has no reason")
        reason = reason.strip()
    elif reason is not None:
        _fail(f"{where}: {status} for {name} must not carry a reason")
    line = raw.get("line")
    if line is not None and (not _valid_count(line) or line < 1):
        _fail(f"{where}: line must be a positive integer or null")
    return {"name": name, "status": status, "reason": reason, "line": line}


def parse_summary_object(obj: Any) -> list[dict]:
    """Re-derive a summary's events and cross-check its claims, fail closed."""
    if not isinstance(obj, dict):
        _fail("summary must be a JSON object")
    version = obj.get("schema_version")
    if version is not None and (not _valid_count(version) or version != 1):
        _fail(f"summary schema_version {version!r} is not supported")

    for key in ("verdict", "game_verdict"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip().upper() == "PASS":
            _fail(f"summary claims {key}=PASS; no false game promotion")
    for key in ("game_pass", "promoted", "promotion"):
        value = obj.get(key)
        if (value is True
                or (isinstance(value, str)
                    and value.strip().upper() in {"PASS", "TRUE", "YES"})
                or (_valid_count(value) and value == 1)):
            _fail(f"summary claims {key}={value!r}; no false game promotion")

    raw_events = obj.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        _fail("summary has no events; refusing to emit empty counts")
    events: list[dict] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_events):
        event = _parse_summary_event(raw, index)
        if event["name"] in seen:
            _fail(f"events[{index}]: duplicate {event['name']}; summary is "
                  "contradictory")
        seen.add(event["name"])
        events.append(event)

    counts = obj.get("counts")
    if counts is not None:
        if not isinstance(counts, dict):
            _fail("summary counts must be an object")
        for key, value in counts.items():
            if key not in COUNT_KEYS:
                _fail(f"summary counts has unknown key {key!r}; no PASS claim "
                      "may enter the record")
            if not _valid_count(value):
                _fail(f"summary counts[{key!r}] is not a non-negative integer")
        actual = count_events(events)
        for key in COUNT_KEYS:
            if key in counts and counts[key] != actual[key]:
                _fail(f"summary counts[{key!r}]={counts[key]} disagrees with "
                      f"the {len(events)} supplied events ({actual[key]})")
    claim = obj.get("log_sha256")
    if claim is not None and (not isinstance(claim, str)
                              or not HEX64.fullmatch(claim)):
        _fail("summary log_sha256 is not a 64-character hex digest")
    return events


# ---------------------------------------------------------------------------
# record construction and verification
# ---------------------------------------------------------------------------
def outcome_for(counts: dict[str, int]) -> str:
    """Selftest-level outcome only; never a game verdict."""
    if counts["FAIL"] or counts["BUILD FAIL"]:
        return "FAILED"
    if counts["skip"]:
        return "PARTIAL"
    return "CLEAN"


def build_record(events: list[dict], *, log_path: Path | None,
                 summary_path: Path | None, log_bytes: bytes | None,
                 label: str) -> dict:
    """Build a canonical record from already-validated events."""
    if not events:
        _fail("no selftest events; refusing to emit a record")
    ordered = [dict(event) for event in sorted(events, key=lambda e: e["name"])]
    counts = count_events(ordered)
    skips = [{"name": e["name"], "reason": e["reason"]}
             for e in ordered if e["status"] == "skip"]
    if log_path is not None and summary_path is not None:
        kind = "log+summary"
    else:
        kind = "log" if log_path is not None else "summary"
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "label": label,
        "source": {
            "kind": kind,
            "log_path": str(log_path) if log_path is not None else None,
            "summary_path": str(summary_path) if summary_path is not None else None,
            "sha256": sha256_bytes(log_bytes) if log_bytes is not None else None,
        },
        "counts": counts,
        "events": ordered,
        "skips": skips,
        "outcome": outcome_for(counts),
        "game_evidence": {
            "verdict": GAME_VERDICT,
            "promotion": False,
            "reason": ("build and unit selftests are not game execution; "
                       "no game or oracle was run, so no game PASS is claimed"),
        },
        "findings": [],
        "limits": list(LIMITS),
    }


def validate_record(record: Any) -> list[str]:
    """Re-check a record structurally; return findings (empty means valid)."""
    if not isinstance(record, dict):
        return ["record must be a JSON object"]
    findings: list[str] = []
    if record.get("schema_version") != SCHEMA_VERSION:
        findings.append("schema_version is not 1")
    if record.get("generator") != GENERATOR:
        findings.append("generator does not match the collector")

    events = record.get("events")
    if not isinstance(events, list) or not events:
        findings.append("record has no events")
        events = []
    seen: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            findings.append(f"events[{index}] is not an object")
            continue
        name, status = event.get("name"), event.get("status")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            findings.append(f"events[{index}] has an invalid name {name!r}")
            continue
        if name in seen:
            findings.append(f"events[{index}] repeats {name}")
        seen.add(name)
        if status not in STATUSES:
            findings.append(f"events[{index}] has invalid status {status!r}")
        reason = event.get("reason")
        if status == "skip" and (not isinstance(reason, str) or not reason.strip()):
            findings.append(f"events[{index}]: skip has no reason")
        if status != "skip" and reason is not None:
            findings.append(f"events[{index}]: non-skip event carries a reason")

    actual = count_events([e for e in events if isinstance(e, dict)
                           and e.get("status") in STATUSES]) if events else {}
    supplied_counts = record.get("counts")
    if not isinstance(supplied_counts, dict):
        findings.append("record counts must be an object")
    else:
        if set(supplied_counts) != set(COUNT_KEYS):
            findings.append("record counts keys are not exactly ok/FAIL/"
                            "BUILD FAIL/skip/total")
        for key in COUNT_KEYS:
            value = supplied_counts.get(key)
            if not _valid_count(value):
                findings.append(f"counts[{key!r}] is not a non-negative integer")
            elif events and value != actual.get(key):
                findings.append(f"counts[{key!r}]={value} disagrees with events "
                                f"({actual.get(key)})")

    if events:
        expected_skips = [{"name": e["name"], "reason": e["reason"]}
                          for e in events if isinstance(e, dict)
                          and e.get("status") == "skip"]
        expected_skips.sort(key=lambda item: str(item["name"]))
        if record.get("skips") != expected_skips:
            findings.append("skips do not match the skip events and reasons")
        if record.get("outcome") != outcome_for(actual):
            findings.append("outcome disagrees with counts")

    game = record.get("game_evidence")
    if not isinstance(game, dict) or game.get("verdict") != GAME_VERDICT:
        findings.append("game_evidence verdict is not NOT TESTED (no false promotion)")
    elif game.get("promotion") is not False:
        findings.append("game_evidence promotion must be false")

    source = record.get("source")
    if not isinstance(source, dict):
        findings.append("record source must be an object")
    else:
        digest = source.get("sha256")
        if digest is not None and (not isinstance(digest, str)
                                   or not HEX64.fullmatch(digest)):
            findings.append("source.sha256 is not a 64-character hex digest")
        if source.get("kind") not in {"log", "summary", "log+summary"}:
            findings.append("source.kind is not a supported input kind")
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", type=Path, help="build_selftests.sh output log")
    parser.add_argument("--summary", type=Path, help="JSON summary of selftest outcomes")
    parser.add_argument("--out", type=Path, help="write the JSON record here")
    parser.add_argument("--verify", type=Path, help="re-check an existing record")
    parser.add_argument("--expect-sha256",
                        help="refuse a log whose SHA-256 is not this digest")
    parser.add_argument("--label", default="CLI-supplied selftest evidence",
                        help="label recorded in the output")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 when a selftest FAIL or BUILD FAIL was counted")
    args = parser.parse_args(argv)

    prefix = "selftest_evidence_collector:"
    if args.verify is not None:
        if args.summary is not None or args.out is not None:
            parser.error("--verify cannot be combined with --summary or --out")
        try:
            raw = json.loads(args.verify.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"{prefix} {exc}", file=sys.stderr)
            return 2
        findings = validate_record(raw)
        if args.log is not None:
            try:
                original = args.log.read_bytes()
            except OSError as exc:
                print(f"{prefix} {exc}", file=sys.stderr)
                return 2
            source = raw.get("source") if isinstance(raw, dict) else None
            claimed = source.get("sha256") if isinstance(source, dict) else None
            if claimed is not None and claimed != sha256_bytes(original):
                findings.append("source.sha256 does not match --log bytes "
                                "(tampered)")
        if findings:
            for finding in findings:
                print(f"{prefix} verify: {finding}", file=sys.stderr)
            return 1
        print(f"verified {args.verify}")
        return 0

    if args.log is None and args.summary is None:
        parser.error("supply --log and/or --summary (or --verify)")
    if (args.expect_sha256 is not None
            and not HEX64.fullmatch(args.expect_sha256)):
        print(f"{prefix} --expect-sha256 must be a 64-character hex digest",
              file=sys.stderr)
        return 2

    try:
        log_bytes: bytes | None = None
        log_events: list[dict] | None = None
        summary_obj: dict | None = None
        summary_events: list[dict] | None = None
        if args.log is not None:
            log_bytes = args.log.read_bytes()
            log_events = parse_log_bytes(log_bytes)
        if args.summary is not None:
            summary_obj = json.loads(
                args.summary.read_text(encoding="utf-8"))
            summary_events = parse_summary_object(summary_obj)

        if log_events is not None and summary_events is not None:
            assert summary_obj is not None and log_bytes is not None
            if (sorted_event_tuples(log_events)
                    != sorted_event_tuples(summary_events)):
                _fail("summary events disagree with log events; stale or "
                      "tampered summary")
            claim = summary_obj.get("log_sha256")
            if claim is not None and claim.lower() != sha256_bytes(log_bytes):
                _fail("summary log_sha256 does not match the log bytes "
                      "(tampered)")
            events = log_events
        else:
            events = log_events if log_events is not None else summary_events
            assert events is not None

        if args.expect_sha256 is not None:
            if log_bytes is None:
                _fail("--expect-sha256 requires --log so the digest can be "
                      "re-derived")
            if args.expect_sha256.lower() != sha256_bytes(log_bytes):
                _fail("--expect-sha256 disagrees with the log bytes (tampered)")

        record = build_record(events, log_path=args.log,
                              summary_path=args.summary, log_bytes=log_bytes,
                              label=args.label)
        problems = validate_record(record)
        if problems:
            _fail(*problems)
    except EvidenceError as exc:
        for finding in exc.findings:
            print(f"{prefix} {finding}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:  # unreadable input / bad JSON
        print(f"{prefix} {exc}", file=sys.stderr)
        return 2

    text = canonical_json(record)
    if args.out is not None:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"{prefix} cannot write record: {exc}", file=sys.stderr)
            return 2
    else:
        print(text, end="")

    counts = record["counts"]
    if args.strict and (counts["FAIL"] or counts["BUILD FAIL"]):
        print(f"{prefix} selftest failures counted (--strict): "
              f"FAIL={counts['FAIL']}, BUILD FAIL={counts['BUILD FAIL']}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
