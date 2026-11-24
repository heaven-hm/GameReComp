"""Fail-closed per-function multi-lifter selection from difftest verdicts (issue #25).

This is the *selector* half of issue #25. It does not run a lifter or a
differential harness: it consumes the JSON reports that ``tools/lift/difftest.py
--project --json ...`` already writes, one per candidate lifter, and decides
which (if any) body may be promoted for a single function.

The contract is deliberately small and deterministic (see docs/MULTILIFTER.md):

* Lifters are supplied in priority order. The first lifter whose report is a
  clean agreement with **zero diverged cases** is the only one promoted.
* ``original-faulted``, ``no-generated-body``, ``harness-failed``,
  ``nothing-compared``, malformed reports and unknown verdicts are never
  promoted. They are reasons to fall through to the next lifter.
* When no lifter passes, the decision is explicit: ``FAILED`` when at least one
  real comparison diverged, ``BLOCKED`` when every lifter was faulted,
  bodyless, unverified or absent. There is no silent default and no partial
  promotion.

An ``agree`` verdict from difftest already means "diverged == 0" for the driven
cases, so a report without a ``cases`` list is accepted on the strength of its
verdict. If a ``cases`` list *is* present it is cross-checked: any case whose
``agree`` flag is not ``True`` makes the report fail closed even if the verdict
string says otherwise.

Exit codes for the CLI: 0 = PROMOTE, 1 = FAILED, 2 = BLOCKED.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Per-lifter statuses.
STATUS_PASS = "PASS"
STATUS_DIVERGED = "DIVERGED"
STATUS_FAULTED = "FAULTED"
STATUS_NO_BODY = "NO_BODY"
STATUS_UNVERIFIED = "UNVERIFIED"
ALL_STATUSES = (STATUS_PASS, STATUS_DIVERGED, STATUS_FAULTED, STATUS_NO_BODY,
                STATUS_UNVERIFIED)

# Overall decisions.
DECISION_PROMOTE = "PROMOTE"
DECISION_FAILED = "FAILED"
DECISION_BLOCKED = "BLOCKED"
EXIT_CODES = {DECISION_PROMOTE: 0, DECISION_FAILED: 1, DECISION_BLOCKED: 2}

# Verdict families emitted by tools/lift/difftest.py. ``agree*`` is the scoped
# pass family difftest itself recognises (it checks ``startswith('agree')``).
# ``original-faulted`` and ``no-generated-body`` are refuse-to-promote verdicts,
# never a softer kind of pass.
FAULT_VERDICTS = frozenset({"original-faulted", "harness-faulted", "faulted"})
NO_BODY_VERDICTS = frozenset({"no-generated-body", "no-body", "nothing-generated"})

# Deterministic precedence when one lifter has several verdict rows (e.g. a
# multi-address report): a single blocker is enough to refuse the whole lifter.
_STATUS_PRECEDENCE = (STATUS_FAULTED, STATUS_NO_BODY, STATUS_UNVERIFIED, STATUS_DIVERGED)


@dataclass(frozen=True)
class _RowResult:
    status: str
    verdict: str | None
    address: str | None
    diverged_cases: int
    total_cases: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class LifterResult:
    """The evaluation of one lifter's report, in the selector's own terms."""

    name: str
    status: str
    verdict: str | None
    address: str | None
    diverged_cases: int
    total_cases: int
    reasons: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.status == STATUS_PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "verdict": self.verdict,
            "address": self.address,
            "diverged_cases": self.diverged_cases,
            "total_cases": self.total_cases,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class Decision:
    """The machine-readable outcome of a multi-lifter selection."""

    decision: str
    chosen_lifter: str | None
    lifters: tuple[LifterResult, ...]
    reasons: tuple[str, ...]

    @property
    def promoted(self) -> bool:
        return self.decision == DECISION_PROMOTE

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "chosen_lifter": self.chosen_lifter,
            "reasons": list(self.reasons),
            "lifters": [lifter.to_dict() for lifter in self.lifters],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _rows_from_json(data: Any) -> list[Any]:
    """Extract the verdict-row list from a difftest JSON report."""
    if isinstance(data, Mapping):
        addresses = data.get("addresses")
        if isinstance(addresses, list):
            rows = list(addresses)
            # A single-address report may carry the address only at the top.
            if len(rows) == 1 and isinstance(rows[0], Mapping) and "address" not in rows[0]:
                if "address" in data:
                    rows[0] = dict(rows[0], address=data["address"])
            return rows
        rows = data.get("rows")
        if isinstance(rows, list):
            return list(rows)
        if "verdict" in data:
            return [data]
        return [{"__error__": "report has neither a 'verdict' nor an 'addresses' list"}]
    if isinstance(data, list):
        return list(data)
    return [{"__error__": f"report is {type(data).__name__}, expected an object or list"}]


def load_report(report: Any) -> list[Any]:
    """Coerce a report input into a list of raw verdict-row objects.

    Accepts a difftest report dict, a list of rows, a JSON string, or a path to
    a JSON file. Parsing/IO failures become an unverified row rather than an
    exception, so a broken report fails closed like any other unverified body.
    """
    if isinstance(report, Path):
        try:
            return _rows_from_json(json.loads(report.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return [{"__error__": f"cannot read report {report}: {exc}"}]
    if isinstance(report, (str, bytes)):
        text = report.decode("utf-8", errors="replace") if isinstance(report, bytes) else report
        try:
            candidate = Path(text.strip())
            if candidate.is_file():
                return load_report(candidate)
        except (OSError, ValueError):
            pass  # not a path (or too long to be one); treat it as JSON text
        try:
            return _rows_from_json(json.loads(text))
        except json.JSONDecodeError as exc:
            return [{"__error__": f"cannot parse report: {exc}"}]
    return _rows_from_json(report)


def _case_counts(row: Mapping[str, Any]) -> tuple[int, int, bool, str | None]:
    """Return (diverged, total, cases_present, error) for a verdict row."""
    if "cases" not in row:
        return 0, 0, False, None
    cases = row.get("cases")
    if not isinstance(cases, list):
        return 0, 0, True, "'cases' is not a list"
    diverged = 0
    for case in cases:
        # Fail closed: a case must explicitly say agree=True to count as an
        # agreement. Anything else (including a malformed case) is divergent.
        if not isinstance(case, Mapping) or case.get("agree") is not True:
            diverged += 1
    return diverged, len(cases), True, None


def classify_row(row: Any) -> _RowResult:
    """Classify one difftest verdict row without ever promoting on doubt."""
    if not isinstance(row, Mapping):
        return _RowResult(STATUS_UNVERIFIED, None, None, 0, 0,
                          (f"verdict row is {type(row).__name__}, expected an object",))

    error = row.get("__error__")
    if error:
        return _RowResult(STATUS_UNVERIFIED, None, None, 0, 0, (str(error),))

    verdict_value = row.get("verdict")
    verdict = str(verdict_value).strip() if verdict_value is not None else ""
    address = str(row["address"]) if row.get("address") is not None else None
    diverged, total, cases_present, cases_error = _case_counts(row)
    detail = row.get("detail")

    def reason(msg: str) -> tuple[str, ...]:
        text = f"verdict '{verdict}': {msg}" if verdict else msg
        return (text,)

    if cases_error:
        return _RowResult(STATUS_UNVERIFIED, verdict or None, address, 0, 0,
                          reason(cases_error))

    if verdict.startswith("agree"):
        if diverged:
            return _RowResult(STATUS_DIVERGED, verdict, address, diverged, total,
                              reason(f"verdict claims agreement but {diverged}/{total} "
                                     "case(s) diverged"))
        if cases_present and total == 0:
            return _RowResult(STATUS_UNVERIFIED, verdict, address, 0, 0,
                              reason("no cases were recorded: nothing was compared"))
        suffix = f" over {total} case(s)" if cases_present else " (no case list; verdict authoritative)"
        return _RowResult(STATUS_PASS, verdict, address, 0, total,
                          reason(f"agrees with zero diverged cases{suffix}"))

    if verdict.startswith("diverge"):
        extra = f"; {diverged}/{total} case(s) diverged" if cases_present and total else ""
        return _RowResult(STATUS_DIVERGED, verdict, address, diverged, total,
                          reason(f"diverged{extra}"))

    if verdict in FAULT_VERDICTS:
        msg = "the original body faulted; no trustworthy comparison exists"
        if detail:
            msg += f" ({detail})"
        return _RowResult(STATUS_FAULTED, verdict, address, diverged, total, reason(msg))

    if verdict in NO_BODY_VERDICTS:
        msg = "no generated body exists to verify"
        if detail:
            msg += f" ({detail})"
        return _RowResult(STATUS_NO_BODY, verdict, address, diverged, total, reason(msg))

    if not verdict:
        return _RowResult(STATUS_UNVERIFIED, None, address, diverged, total,
                          ("missing verdict: nothing was verified",))
    return _RowResult(STATUS_UNVERIFIED, verdict, address, diverged, total,
                      reason("verdict is not a scoped agreement"))


def _evaluate(name: str, report: Any) -> LifterResult:
    rows = [classify_row(row) for row in load_report(report)]
    if not rows:
        return LifterResult(name, STATUS_UNVERIFIED, None, None, 0, 0,
                            ("report contains no verdict rows",))

    diverged = sum(row.diverged_cases for row in rows)
    total = sum(row.total_cases for row in rows)
    verdicts = tuple(dict.fromkeys(row.verdict for row in rows if row.verdict is not None))
    addresses = tuple(dict.fromkeys(row.address for row in rows if row.address is not None))
    verdict = ", ".join(verdicts) if verdicts else None
    address = ", ".join(addresses) if addresses else None

    if all(row.status == STATUS_PASS for row in rows):
        status = STATUS_PASS
    else:
        status = next(
            candidate for candidate in _STATUS_PRECEDENCE
            if any(row.status == candidate for row in rows)
        )

    prefix = f"[{address}] " if len(rows) > 1 and address else ""
    reasons: list[str] = []
    for row in rows:
        for message in row.reasons:
            rendered = f"{prefix}{message}"
            if rendered not in reasons:
                reasons.append(rendered)

    return LifterResult(name, status, verdict, address, diverged, total, tuple(reasons))


def select(lifters: Iterable[tuple[str, Any]]) -> Decision:
    """Choose the first passing lifter, or return an explicit FAILED/BLOCKED.

    ``lifters`` is an iterable of ``(name, report)`` pairs in descending
    priority. A report may be a difftest JSON dict, a list of verdict rows, a
    JSON string, or a path to a JSON file. Iteration is in the given order, so
    the outcome is deterministic and never depends on dict/set ordering.
    """
    evaluated = tuple(_evaluate(str(name), report) for name, report in lifters)
    passers = [lifter for lifter in evaluated if lifter.passed]
    if passers:
        chosen = passers[0]
        reasons: list[str] = []
        for lifter in evaluated:
            verb = "promoted" if lifter is chosen else "skipped"
            detail = "; ".join(lifter.reasons)
            reasons.append(f"{verb} '{lifter.name}' ({lifter.status}): {detail}")
        return Decision(DECISION_PROMOTE, chosen.name, evaluated, tuple(reasons))

    # No passer: divergence is a real failure, fault/no-body/absence is a
    # blocker. Record each lifter's status and reason in priority order.
    has_divergence = any(
        lifter.status == STATUS_DIVERGED or lifter.diverged_cases > 0
        for lifter in evaluated
    )
    decision = DECISION_FAILED if has_divergence else DECISION_BLOCKED
    if not evaluated:
        reasons = ["no lifters supplied: there is no body to promote"]
    else:
        reasons = [
            f"'{lifter.name}': {lifter.status}: {'; '.join(lifter.reasons)}"
            for lifter in evaluated
        ]
    return Decision(decision, None, evaluated, tuple(reasons))


def _cli_load(value: str) -> Any:
    """Load a CLI --lifter value; a bad value fails closed as an unverified row."""
    try:
        path = Path(value)
        if path.is_file():
            return path
    except (OSError, ValueError):
        pass
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        return {"__error__": f"cannot load lifter report {value!r}: {exc}"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="fail-closed per-function multi-lifter selector (issue #25)")
    parser.add_argument(
        "--lifter", action="append", default=[], metavar="NAME=REPORT",
        help="a candidate lifter in priority order; REPORT is a difftest JSON file "
             "or inline JSON (repeatable)")
    parser.add_argument("--json", dest="json_out", metavar="PATH",
                        help="also write the decision JSON to PATH")
    parser.add_argument("--quiet", action="store_true",
                        help="do not print the decision JSON to stdout")
    args = parser.parse_args(argv)

    if not args.lifter:
        parser.error("at least one --lifter NAME=REPORT is required")
    pairs: list[tuple[str, Any]] = []
    for spec in args.lifter:
        name, sep, value = spec.partition("=")
        if not sep or not name.strip():
            parser.error(f"invalid --lifter spec {spec!r}; expected NAME=REPORT")
        pairs.append((name.strip(), _cli_load(value)))

    decision = select(pairs)
    text = decision.to_json()
    if not args.quiet:
        print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return EXIT_CODES[decision.decision]


if __name__ == "__main__":
    sys.exit(main())
