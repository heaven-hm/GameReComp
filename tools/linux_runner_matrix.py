#!/usr/bin/env python3
"""Build an honest per-runner capability matrix for Windows->Linux work.

This tool consumes runner records supplied on the command line (inline JSON or
a JSON file) and reports what each runner was *measured* to support. It never
probes the local host, never launches a command, and never produces a Linux
work result: ``linux_work_result`` is always ``NOT TESTED``. Exit code 0 means
"a matrix was computed", never "Linux work passed".

Runner kinds
------------
``ci``, ``colima``, ``docker``, ``lima`` and ``native``. Records are grouped by
runner; conflicting records for one runner fail closed to BLOCKED.

Evidence gates (all must hold for USABLE)
-----------------------------------------
1. ``linux_command_executed`` -- a real command was recorded as executed in the
   runner, exited 0, did not time out, and has a non-empty argv;
2. ``kernel_arch_recorded`` -- the runner kernel was recorded as Linux and a
   non-empty architecture was recorded alongside it;
3. ``native_elf_hash_verified`` -- a real 64-hex SHA-256 was recorded for the
   native artifact (and, if an ELF magic/first-bytes field is present, it must
   equal ``7f454c46``);
4. ``native_elf_executed`` -- that artifact was recorded as actually executed
   with exit code 0 (and any recorded run sentinel must match).

A claimed ``status``/``claimed_status``/``verdict``, a bare ``verified: true``
or free-form prose is advisory only and can never grant USABLE. Records with no
evidence at all are NOT TESTED; records with evidence that fails a gate are
BLOCKED with one explicit reason per failed gate. Foreign kernels, malformed
hashes, wrong ELF magic, timeouts, non-zero exits and sentinel mismatches are
downgraded with a reason rather than silently accepted.

Integrity
---------
Each report carries a SHA-256 of its canonical bytes. ``--verify`` recomputes
that digest and also re-checks the honesty invariants, so post-hoc tampering
is detected without trusting the document's own status fields.

Usage::

    python3 -m tools.linux_runner_matrix \
        --record '{"runner":"docker","execution":{...},"native_binary":{...}}' \
        --records runners.json [--out matrix.json] [--markdown matrix.md] \
        [--generated-at 2026-11-25T00:00:00Z]

    python3 -m tools.linux_runner_matrix --verify matrix.json

Exit codes: 0 = matrix computed (or integrity verified), 1 = invalid input or
integrity/invariant failure, 2 = invalid command-line arguments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/linux_runner_matrix.py"
DIRECTION = "Windows->Linux"
LINUX_WORK_RESULT = "NOT TESTED"

USABLE = "USABLE"
BLOCKED = "BLOCKED"
NOT_TESTED = "NOT TESTED"
STATUSES = (USABLE, BLOCKED, NOT_TESTED)

RUNNERS = ("ci", "colima", "docker", "lima", "native")
RUNNER_ALIASES = {
    "ci": "ci", "github-actions": "ci", "github-ci": "ci", "gitlab-ci": "ci",
    "colima": "colima", "lima": "lima", "docker": "docker",
    "native": "native", "linux-native": "native",
}
CAPABILITIES = (
    "linux_command_executed",
    "kernel_arch_recorded",
    "native_elf_hash_verified",
    "native_elf_executed",
)

ELF_MAGIC_HEX = "7f454c46"
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")

RUNNER_KEYS = ("runner", "kind", "runner_kind", "name")
CLAIM_KEYS = ("claimed_status", "status", "verdict", "claimed")
EXEC_KEYS = ("execution", "command", "linux_command", "probe")
ELF_KEYS = ("native_binary", "native_elf", "elf")
ARGV_KEYS = ("argv", "command", "cmd", "args", "command_line")
EXIT_KEYS = ("exit_code", "rc", "returncode")
TIMEOUT_KEYS = ("timed_out", "timeout")
KERNEL_KEYS = ("kernel", "kernel_name", "system", "os")
ARCH_KEYS = ("arch", "architecture", "machine")
SHA_KEYS = ("sha256", "sha_256", "digest", "hash")
MAGIC_KEYS = ("elf_magic", "magic", "first_bytes", "elf_first_bytes")
ELF_EXEC_KEYS = ("executed", "ran", "launched")
EXECUTED_KEYS = ("executed", "ran", "completed")
SENTINEL_EXPECT_KEYS = ("sentinel_expected", "sentinel", "token_expected")
SENTINEL_OBSERVE_KEYS = ("sentinel_observed", "observed", "token_observed")
RECORD_LIST_KEYS = ("runners", "records", "rows")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_bytes(data: Any) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def with_integrity(matrix: dict) -> dict:
    """Attach a digest of the canonical document so --verify can spot edits."""
    out = dict(matrix)
    out["integrity"] = {
        "algorithm": "sha256",
        "sha256": sha256_bytes(canonical_bytes(out)),
    }
    return out


def norm_token(value: Any) -> str:
    text = " ".join(str(value or "").replace("_", "-").split()).lower()
    return re.sub(r"-+", "-", text).strip("-")


def norm_runner(value: Any) -> str | None:
    return RUNNER_ALIASES.get(norm_token(value))


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value.strip()))


def field(obj: Any, keys: tuple[str, ...]) -> Any:
    """First present, non-None value for ``keys`` in a mapping (no guessing)."""
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if key in obj and obj[key] is not None:
            return obj[key]
    return None


def nested(record: Any, keys: tuple[str, ...]) -> dict:
    for key in keys:
        if isinstance(record, dict) and isinstance(record.get(key), dict):
            return record[key]
    return {}


def recorded(obj: dict, fallback: dict, keys: tuple[str, ...]) -> Any:
    """Prefer a field present on the execution; a blank one cannot be bypassed
    by a friendlier top-level value."""
    for key in keys:
        if key in obj and obj[key] is not None:
            return obj[key]
    return field(fallback, keys)


def exit_ok(value: Any) -> bool:
    return value == 0 or value == "0"


def norm_hex(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace(":", "")


def has_argv(value: Any) -> bool:
    if isinstance(value, (list, tuple)):
        return len(value) > 0 and all(str(item).strip() for item in value)
    return isinstance(value, str) and bool(value.strip())


def evaluate(record: Any) -> dict:
    """Compute fail-closed gates for one supplied runner record.

    Returns a small dict with ``checks``, ``reasons``, ``status`` and ``evidence``
    plus ``claimed_status`` (recorded verbatim but never trusted). No command is
    executed here -- the record is the only source of observations.
    """
    if not isinstance(record, dict):
        return {
            "status": NOT_TESTED,
            "checks": {name: False for name in CAPABILITIES},
            "reasons": ["record is not a JSON object; nothing was verified"],
            "evidence": {},
            "claimed_status": "",
        }

    exec_rec = nested(record, EXEC_KEYS)
    elf_rec = nested(record, ELF_KEYS)
    reasons: list[str] = []

    claimed = field(record, CLAIM_KEYS)
    claimed_status = str(claimed).strip() if isinstance(claimed, (str, int)) else ""

    # Gate 1: a real Linux command was executed in the runner.
    command_ran = field(exec_rec, EXECUTED_KEYS) is True
    command_rc_ok = exit_ok(field(exec_rec, EXIT_KEYS))
    timed_out = field(exec_rec, TIMEOUT_KEYS) is True
    argv = field(exec_rec, ARGV_KEYS)
    argv_present = has_argv(argv)
    command_ok = command_ran and command_rc_ok and not timed_out and argv_present
    if not command_ran:
        reasons.append("no command was recorded as executed in the runner")
    if not command_rc_ok:
        reasons.append("recorded command exit code is missing or non-zero")
    if timed_out:
        reasons.append("recorded command timed out")
    if not argv_present:
        reasons.append("no command line/argv was recorded for the execution")

    # Gate 2: kernel and architecture were recorded with that execution.
    kernel = str(recorded(exec_rec, record, KERNEL_KEYS) or "").strip()
    arch = str(recorded(exec_rec, record, ARCH_KEYS) or "").strip()
    kernel_ok = norm_token(kernel) == "linux"
    arch_ok = bool(arch)
    if not kernel_ok:
        reasons.append(
            "runner kernel is not recorded as Linux "
            f"(got {kernel!r}); a foreign host cannot be relabelled as Linux"
        )
    if not arch_ok:
        reasons.append("runner architecture was not recorded")

    # Gate 3: a real SHA-256 of the native artifact was recorded.
    sha_raw = field(elf_rec, SHA_KEYS)
    sha = str(sha_raw).strip().lower() if isinstance(sha_raw, str) else ""
    hash_ok = is_sha256(sha)
    magic_raw = field(elf_rec, MAGIC_KEYS)
    magic = norm_hex(magic_raw)
    magic_ok = not magic or magic == ELF_MAGIC_HEX
    if not hash_ok:
        reasons.append("native artifact SHA-256 is missing or not 64 hex digits")
    if not magic_ok:
        reasons.append(
            f"artifact first bytes {magic!r} are not native ELF magic "
            f"({ELF_MAGIC_HEX}); forged/tampered evidence is rejected"
        )

    # Gate 4: that artifact was actually executed with a matching sentinel.
    elf_ran = field(elf_rec, ELF_EXEC_KEYS) is True
    elf_rc_ok = exit_ok(field(elf_rec, EXIT_KEYS))
    expected = field(elf_rec, SENTINEL_EXPECT_KEYS)
    observed = field(elf_rec, SENTINEL_OBSERVE_KEYS)
    sentinel_checked = bool(expected) and bool(observed)
    sentinel_ok = not sentinel_checked or expected == observed
    elf_ok = elf_ran and elf_rc_ok and sentinel_ok
    if not elf_ran:
        reasons.append("native artifact was not recorded as executed")
    if not elf_rc_ok:
        reasons.append("native artifact exit code is missing or non-zero")
    if sentinel_checked and not sentinel_ok:
        reasons.append(
            "execution sentinel mismatch: the recorded native binary did not "
            "run with the expected value"
        )

    checks = {
        "linux_command_executed": command_ok,
        "kernel_arch_recorded": kernel_ok and arch_ok,
        "native_elf_hash_verified": hash_ok and magic_ok,
        "native_elf_executed": elf_ok,
    }
    if claimed_status.upper() == USABLE and not all(checks.values()):
        reasons.append(
            f"claimed status {claimed_status!r} ignored: evidence gates failed"
        )
    if not exec_rec and not elf_rec:
        reasons.append("no Linux runner evidence was supplied; nothing was verified")

    evidence = {
        "argv": argv if isinstance(argv, (list, str)) else [],
        "command_exit_code": field(exec_rec, EXIT_KEYS),
        "command_timed_out": timed_out,
        "kernel": kernel,
        "arch": arch,
        "native_elf_sha256": sha,
        "elf_first_bytes": magic,
        "native_elf_executed": elf_ran,
        "native_elf_exit_code": field(elf_rec, EXIT_KEYS),
    }
    if not exec_rec and not elf_rec:
        status = NOT_TESTED
    elif all(checks.values()):
        status = USABLE
    else:
        status = BLOCKED
    return {
        "status": status,
        "checks": checks,
        "reasons": reasons,
        "evidence": evidence,
        "claimed_status": claimed_status,
    }


def group_key(record: Any) -> tuple[str, str | None]:
    """Return (matrix key, raw runner value). Unknown kinds keep a visible key."""
    raw = field(record, RUNNER_KEYS)
    runner = norm_runner(raw)
    if runner is not None:
        return runner, str(raw)
    token = norm_token(raw)
    return f"unknown:{token or 'missing'}", str(raw) if raw is not None else None


def merge_group(key: str, items: list[tuple[Any, dict]]) -> dict:
    """Merge records for one runner, fail-closed on conflicts."""
    results = [result for _, result in items]
    statuses = {result["status"] for result in results}
    raw_values = [raw for _, raw in items]
    raw = next(
        (value for value in raw_values if value),
        raw_values[0] if raw_values else None,
    )
    runner = key
    checks = {
        name: all(result["checks"][name] for result in results)
        for name in CAPABILITIES
    }
    reasons: list[str] = []
    for result in results:
        for reason in result["reasons"]:
            if reason not in reasons:
                reasons.append(reason)

    if key.startswith("unknown:"):
        status = NOT_TESTED
        reasons.insert(0, f"unrecognised runner kind {raw!r}: not mapped to {DIRECTION}")
    elif BLOCKED in statuses or (USABLE in statuses and NOT_TESTED in statuses):
        status = BLOCKED
        if len(results) > 1:
            reasons.append(
                f"conflicting records for runner {key!r} "
                f"({len(results)} supplied): fail closed"
            )
    elif statuses == {USABLE}:
        status = USABLE
        reasons = []
    else:
        status = NOT_TESTED

    reasons.sort()
    evidence = next(
        (result["evidence"] for result in results if result["status"] == USABLE),
        results[0]["evidence"] if results else {},
    )
    return {
        "runner": runner,
        "raw_runner": raw,
        "status": status,
        "reasons": reasons,
        "checks": checks,
        "evidence": evidence,
        "claimed_statuses": sorted({r["claimed_status"] for r in results if r["claimed_status"]}),
        "records_seen": len(results),
    }


def build_matrix(
    records: list[Any], *, generated_at: str | None = None
) -> dict:
    """Compute the capability matrix from supplied records only."""
    groups: dict[str, list[tuple[Any, dict]]] = {}
    for record in records:
        key, raw = group_key(record)
        groups.setdefault(key, []).append((raw, evaluate(record)))

    rows = [merge_group(key, groups[key]) for key in sorted(groups)]
    summary = {status: sum(1 for row in rows if row["status"] == status)
               for status in STATUSES}
    summary["runner_count"] = len(rows)
    matrix = {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at or utc_now(),
        "direction": DIRECTION,
        "capabilities": list(CAPABILITIES),
        "runners": rows,
        "summary": summary,
        "linux_work_result": LINUX_WORK_RESULT,
        "not_a_linux_pass": True,
        "no_local_probing": True,
        "claimed_status_is_advisory": True,
    }
    return with_integrity(matrix)


def load_records(inline: list[str], paths: list[str]) -> list[Any]:
    """Parse CLI-supplied JSON records; no file is read unless explicitly named."""
    records: list[Any] = []
    for text in inline:
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"--record is not valid JSON: {exc}") from exc
        if isinstance(parsed, list):
            records.extend(parsed)
        elif isinstance(parsed, dict):
            records.append(parsed)
        else:
            raise ValueError("--record must be a JSON object or list of objects")
    for name in paths:
        path = Path(name)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read --records {name!r}: {exc}") from exc
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"--records {name!r} is not valid JSON: {exc}") from exc
        if isinstance(parsed, dict):
            listed = None
            for key in RECORD_LIST_KEYS:
                if isinstance(parsed.get(key), list):
                    listed = parsed[key]
                    break
            records.extend(listed if listed is not None else [parsed])
        elif isinstance(parsed, list):
            records.extend(parsed)
        else:
            raise ValueError(f"--records {name!r} must contain objects")
    if not records:
        raise ValueError("no runner records supplied (use --record or --records)")
    return records


def verify_report(path: str) -> tuple[bool, str]:
    """Recompute integrity and re-check honesty invariants without trusting fields."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"cannot read report {path!r}: {exc}"
    if not isinstance(doc, dict):
        return False, "report is not a JSON object"
    integrity = doc.get("integrity")
    if not isinstance(integrity, dict) or not is_sha256(integrity.get("sha256")):
        return False, "report has no usable integrity digest"
    expected = str(integrity["sha256"]).strip().lower()
    bare = {key: value for key, value in doc.items() if key != "integrity"}
    actual = sha256_bytes(canonical_bytes(bare))
    if actual != expected:
        return False, f"integrity mismatch: computed {actual}, recorded {expected}"
    if doc.get("linux_work_result") != LINUX_WORK_RESULT:
        return False, "honesty invariant violated: linux_work_result was changed"
    if doc.get("not_a_linux_pass") is not True:
        return False, "honesty invariant violated: not_a_linux_pass was changed"
    if doc.get("no_local_probing") is not True:
        return False, "honesty invariant violated: no_local_probing was changed"
    for row in doc.get("runners", []):
        if not isinstance(row, dict) or row.get("status") not in STATUSES:
            return False, "honesty invariant violated: invalid runner status"
    return True, "integrity and honesty invariants verified"


def md_cell(value: Any) -> str:
    """Keep a cell from breaking the markdown table."""
    return " ".join(str(value or "").replace("|", "\\|").split())


def markdown_report(matrix: dict) -> str:
    lines = [
        f"# {DIRECTION} runner capability matrix",
        "",
        f"linux_work_result: {LINUX_WORK_RESULT} (no Linux work result is produced here)",
        "",
        "| runner | status | linux command | kernel/arch | native ELF hash | ELF executed | records | reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in matrix["runners"]:
        checks = row["checks"]
        lines.append(
            "| {runner} | {status} | {c0} | {c1} | {c2} | {c3} | {n} | {reason} |".format(
                runner=md_cell(row["runner"]), status=md_cell(row["status"]),
                c0="yes" if checks["linux_command_executed"] else "no",
                c1="yes" if checks["kernel_arch_recorded"] else "no",
                c2="yes" if checks["native_elf_hash_verified"] else "no",
                c3="yes" if checks["native_elf_executed"] else "no",
                n=row["records_seen"],
                reason=md_cell("; ".join(row["reasons"]) or "-"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", action="append", default=[], metavar="JSON",
                    help="inline runner record (object or list), repeatable")
    ap.add_argument("--records", action="append", default=[], metavar="PATH",
                    help="path to a JSON file of runner records, repeatable")
    ap.add_argument("--out", metavar="PATH", help="write the JSON matrix here")
    ap.add_argument("--markdown", metavar="PATH", help="write a markdown matrix here")
    ap.add_argument("--generated-at", metavar="UTC",
                    help="fixed UTC timestamp for deterministic output")
    ap.add_argument("--verify", metavar="PATH",
                    help="check integrity/honesty invariants of an existing report")
    args = ap.parse_args(argv)

    if args.verify:
        ok, message = verify_report(args.verify)
        print(message, file=sys.stderr)
        return 0 if ok else 1

    try:
        records = load_records(args.record, args.records)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    matrix = build_matrix(records, generated_at=args.generated_at)
    text = json.dumps(matrix, indent=2, sort_keys=True) + "\n"
    try:
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
        if args.markdown:
            md = Path(args.markdown)
            md.parent.mkdir(parents=True, exist_ok=True)
            md.write_text(markdown_report(matrix), encoding="utf-8")
    except OSError as exc:
        print(f"cannot write output: {exc}", file=sys.stderr)
        return 1
    print(text)
    print(
        f"direction: {DIRECTION} | linux_work_result: {LINUX_WORK_RESULT} | "
        + " | ".join(f"{status}: {matrix['summary'][status]}" for status in STATUSES),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
