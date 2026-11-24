#!/usr/bin/env python3
"""Fail-closed hybrid fidelity orchestrator: Frida + Unicorn + scripted replay.

This tool drives three *injectable* evidence sources through a single verdict
gate:

1. ``--frida-adapter`` -- a CLI-supplied Python module exposing
   ``collect(context)`` (or ``run(context)``), or a JSON evidence file. It must
   report a real, executed original-process oracle (never a dummy/headless one)
   and a non-empty capture.
2. ``--unicorn-adapter`` -- the same adapter contract, reporting CPU-level
   agreement between the original bytes and the native port.
3. ``--input-scenario`` -- a JSON scripted-input scenario whose ``replay``
   section carries frame and state evidence for the *same* events.

Adapters are the only way an engine result enters the report. An adapter module
is imported from the path given on the command line and called under a hard
wall-clock deadline (``--timeout``); a JSON adapter is read as recorded
evidence. No engine, game, corpus, Wine prefix, path or hash is built into this
module. Hashes are computed here with SHA-256 from the CLI-supplied files, or
accepted from adapter evidence only when they are real 64-hex digests that
agree with those computed values -- fake hashes can never satisfy a gate.

PASS is emitted only when *all* of these hold at once:

* a boolean executed-oracle flag naming a real original process,
* original, native and input SHA-256 values that match across all evidence,
* non-empty Frida capture evidence,
* Unicorn agreement backed by evidence,
* a replayed input scenario with matching events, frame hashes and state.

Anything else is capped at PARTIAL / BUILD ONLY / FAIL / BLOCKED / NOT TESTED
with the exact withheld-gate reasons. No universal OS or game support is
claimed: every OS/arch pair other than the single evidenced target pair is
reported as DEFERRED (BLOCKED for support claims).

Usage::

    python3 -m tools.hybrid_pipeline \\
        --original <original.exe> --native <native.bin> \\
        --input-scenario <scenario.json> --out <dir> \\
        --frida-adapter <module.py|evidence.json> \\
        --unicorn-adapter <module.py|evidence.json> \\
        --timeout 15 --json --markdown
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools import os_capability_matrix as ocm

SCHEMA_VERSION = 1
GENERATOR = "tools/hybrid_pipeline.py"
OSES = ("Windows", "macOS", "Linux")
ARCHES = ("x86", "x64", ".NET", "Win16")
VERDICTS = ("PASS", "PARTIAL", "BUILD ONLY", "FAIL", "BLOCKED", "NOT TESTED")
REAL_ORACLES = ocm.REAL_ORACLES
NO_ORACLES = ocm.NO_ORACLES
HASH_KEYS = ("original_sha256", "native_sha256", "input_sha256")

PASS_POLICY = (
    "No universal OS/game support is claimed. PASS is scoped to the single "
    "OS/arch pair with real executed-oracle, Frida, Unicorn and replay "
    "evidence; every other OS/arch pair is DEFERRED (BLOCKED for support "
    "claims). Written adapter statuses are advisory only and can never grant "
    "PASS on their own."
)

GATE_REASONS = {
    "real_executed_oracle_flag": "no real executed original-process oracle flag",
    "hashes_match_evidence": "original/native/input SHA-256 values do not match evidence",
    "frida_capture_evidence": "no Frida capture evidence",
    "unicorn_agreement": "Unicorn did not report evidenced agreement",
    "replayed_input_scenario": "input scenario was not replayed with frame/state evidence",
    "target_os_arch_evidenced": "target OS/architecture pair is not evidenced",
}

REPLAY_CHECKS = ("has_name", "target_evidenced", "scripted_events",
                 "replay_executed", "events_match", "frames_evidenced",
                 "state_evidenced", "replay_hash_matches")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_evidence_path(raw: Any, base_dir: Path) -> Path | None:
    """Resolve an evidence path; relative paths are relative to the declaring
    file. Absolute paths (CLI or evidence) are used verbatim."""
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else base_dir / path


def _with_timeout(fn: Callable[[], Any], timeout: int) -> Any:
    """Run an adapter under a SIGALRM deadline on POSIX hosts. The adapter is
    never trusted to police itself; on expiry a TimeoutError is raised and the
    loader records BLOCKED, never a partial engine result."""
    timeout = max(1, int(timeout))
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        return fn()

    def handler(signum, frame):  # pragma: no cover - exercised via timeout test
        raise TimeoutError(f"adapter exceeded --timeout {timeout}s")

    previous = signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, float(timeout))
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def sha256_of(path: Path | None) -> str | None:
    return ocm.sha256_file(path) if path else None


def load_json_document(path: Path, kind: str) -> tuple[Any, dict, str | None]:
    """Read a CLI-supplied JSON file, recording unreadable inputs fail-closed."""
    entry = {"kind": kind, "path": str(path), "sha256": sha256_of(path)}
    if not path.is_file():
        entry["status"] = "MISSING"
        return None, entry, f"{kind} file not found: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        entry["status"] = "INVALID"
        error = f"{type(exc).__name__}: {exc}"
        return None, entry, error
    entry["status"] = "OK"
    return data, entry, None


def _blocked_evidence(kind: str, reason: str, source: str = "",
                      method: str = "absent") -> dict:
    return {
        "kind": kind, "source": str(source), "method": method,
        "status": "BLOCKED", "reason": reason, "oracle": None,
        "capture": None, "hashes": {}, "invalid_hash_claims": [],
        "agreement": None, "evidence": "", "evidence_path": "",
        "evidence_sha256": "", "actual_evidence_sha256": None,
    }


def normalize_evidence(kind: str, raw: Any, source: Path,
                       base_dir: Path, method: str) -> dict:
    """Normalise one adapter's claims into a fixed, verifiable shape."""
    rec = _blocked_evidence(kind, "", source, method)
    if not isinstance(raw, dict):
        rec["reason"] = "adapter evidence is not a JSON object"
        return rec

    status = str(raw.get("status", "OK")).strip().upper()
    if status in ("OK", "BLOCKED", "FAIL"):
        rec["status"] = status
    else:
        rec["reason"] = f"unknown adapter status {status!r}"
        return rec
    rec["reason"] = str(raw.get("reason") or "").strip()

    hashes_raw = raw.get("hashes") if isinstance(raw.get("hashes"), dict) else {}
    claims: dict[str, set[str]] = {}
    for key in HASH_KEYS:
        value = hashes_raw.get(key, raw.get(key))
        if value is None:
            continue
        normalised = ocm.norm_sha(value)
        if normalised:
            claims.setdefault(key, set()).add(normalised)
        else:
            rec["invalid_hash_claims"].append(key)
    rec["hashes"] = {key: sorted(values) for key, values in claims.items()}

    oracle_value = raw.get("oracle")
    oracle_raw = oracle_value if isinstance(oracle_value, dict) else {}
    if isinstance(oracle_value, str):
        name_raw = oracle_value
    else:
        name_raw = (oracle_raw.get("name") or oracle_raw.get("oracle")
                    or oracle_raw.get("kind"))
    verified = oracle_raw.get("verified", raw.get("oracle_verified"))
    executed = oracle_raw.get("executed", raw.get("oracle_executed"))
    if name_raw is not None or verified is not None or executed is not None:
        rec["oracle"] = {
            "name": str(name_raw or "").strip(),
            "verified": verified is True,
            "executed": executed is True,
        }

    capture_value = raw.get("capture")
    if isinstance(capture_value, dict):
        cpath = resolve_evidence_path(capture_value.get("path"), base_dir)
        declared = capture_value.get("sha256")
        csha = ocm.norm_sha(declared)
        calls = capture_value.get("calls")
        if declared is not None and not csha:
            rec["invalid_hash_claims"].append("capture.sha256")
        rec["capture"] = {
            "evidence": str(capture_value.get("evidence") or "").strip(),
            "calls": calls if isinstance(calls, list) else [],
            "path": str(cpath) if cpath else "",
            "sha256": csha or "",
            "actual_sha256": sha256_of(cpath) or "",
        }

    agreement = raw.get("agreement", raw.get("match"))
    rec["agreement"] = agreement if isinstance(agreement, bool) else None
    rec["evidence"] = str(
        raw.get("evidence") or raw.get("signature") or "").strip()

    evidence_path = resolve_evidence_path(raw.get("evidence_path"), base_dir)
    declared_sha = raw.get("evidence_sha256")
    evidence_sha = ocm.norm_sha(declared_sha)
    if declared_sha is not None and not evidence_sha:
        rec["invalid_hash_claims"].append("evidence_sha256")
    rec["evidence_path"] = str(evidence_path) if evidence_path else ""
    rec["evidence_sha256"] = evidence_sha or ""
    rec["actual_evidence_sha256"] = sha256_of(evidence_path) or ""
    return rec


def load_adapter(raw_path: str, kind: str, context: dict,
                 timeout: int) -> tuple[dict, dict]:
    """Import a CLI-supplied module or read a JSON evidence file. Absent,
    unreadable, raising or timing-out adapters always become BLOCKED."""
    if not raw_path:
        return (_blocked_evidence(kind, f"no --{kind}-adapter supplied"),
                {"kind": kind, "path": "", "sha256": None, "status": "ABSENT"})

    path = Path(raw_path)
    entry = {"kind": kind, "path": str(path), "sha256": sha256_of(path)}
    if not path.is_file():
        entry["status"] = "MISSING"
        return (_blocked_evidence(kind, f"{kind} adapter not found: {path}",
                                  str(path)), entry)
    entry["status"] = "OK"
    adapter_context = dict(context)
    adapter_context["kind"] = kind
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get(kind, data) if isinstance(data, dict) else None
            return normalize_evidence(kind, raw, path, path.parent,
                                      "json-evidence"), entry

        spec = importlib.util.spec_from_file_location(f"_hybrid_{kind}_adapter", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load adapter module: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        collector = getattr(module, "collect", None) or getattr(module, "run", None)
        if not callable(collector):
            return (_blocked_evidence(
                kind, "adapter exposes no collect(context) or run(context)",
                str(path), "python-module"), entry)
        raw = _with_timeout(lambda: collector(adapter_context), timeout)
        return normalize_evidence(kind, raw, path, path.parent,
                                  "python-module"), entry
    except TimeoutError as exc:
        return _blocked_evidence(kind, str(exc), str(path),
                                 "python-module"), entry
    except (Exception, SystemExit) as exc:
        detail = str(exc)[:300]
        return _blocked_evidence(kind, f"{type(exc).__name__}: {detail}",
                                 str(path), "python-module"), entry


def validate_replay(scenario: Any, base_dir: Path) -> tuple[dict, dict]:
    """Check the scripted-input scenario and its frame/state replay evidence."""
    checks = {key: False for key in REPLAY_CHECKS}
    summary: dict[str, Any] = {
        "name": "", "os": None, "arch": None, "executed": False,
        "events_match": False, "frame_count": 0, "valid_frame_count": 0,
        "state_present": False, "evidence_path": "", "evidence_sha256": "",
        "actual_sha256": "", "invalid_hash_claim": False,
    }
    if not isinstance(scenario, dict):
        return checks, summary

    summary["name"] = str(
        scenario.get("name") or scenario.get("scenario") or "").strip()
    checks["has_name"] = bool(summary["name"])

    target = scenario.get("target") if isinstance(scenario.get("target"), dict) else {}
    os_name = ocm.norm_os(scenario.get("os") or scenario.get("operating_system")
                          or target.get("os"))
    arch_name = ocm.norm_arch(scenario.get("arch") or scenario.get("architecture")
                              or target.get("arch"))
    summary["os"], summary["arch"] = os_name, arch_name
    checks["target_evidenced"] = bool(os_name and arch_name)

    events = (scenario.get("events") or scenario.get("input_events")
              or scenario.get("inputs") or scenario.get("script"))
    if not isinstance(events, list):
        events = []

    replay = scenario.get("replay") if isinstance(scenario.get("replay"), dict) else {}
    executed = replay.get("executed", replay.get("replayed"))
    checks["replay_executed"] = executed is True
    summary["executed"] = checks["replay_executed"]
    checks["scripted_events"] = bool(events)
    checks["events_match"] = bool(events) and replay.get("events") == events
    summary["events_match"] = checks["events_match"]

    frames = replay.get("frames")
    summary["frame_count"] = len(frames) if isinstance(frames, list) else 0
    valid_frames = 0
    if isinstance(frames, list):
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            digest = ocm.norm_sha(frame.get("sha256") or frame.get("hash"))
            frame_path = resolve_evidence_path(frame.get("path"), base_dir)
            actual = sha256_of(frame_path) or ""
            # A frame counts only when a real file was captured and its
            # recomputed SHA-256 matches the declared value. Non-empty text or
            # a made-up digest never counts as frame evidence.
            if digest and actual and digest == actual:
                valid_frames += 1
            elif frame.get("sha256") is not None and not digest:
                summary["invalid_hash_claim"] = True
    summary["valid_frame_count"] = valid_frames
    checks["frames_evidenced"] = valid_frames > 0

    state = replay.get("state")
    if isinstance(state, (dict, list)):
        summary["state_present"] = bool(state)
    elif isinstance(state, str):
        summary["state_present"] = bool(state.strip())
    checks["state_evidenced"] = summary["state_present"]

    evidence_path = resolve_evidence_path(replay.get("evidence_path"), base_dir)
    declared_sha = replay.get("evidence_sha256")
    evidence_sha = ocm.norm_sha(declared_sha)
    actual_sha = sha256_of(evidence_path) or ""
    summary.update({
        "evidence_path": str(evidence_path) if evidence_path else "",
        "evidence_sha256": evidence_sha or "",
        "actual_sha256": actual_sha,
    })
    if declared_sha is not None and not evidence_sha:
        summary["invalid_hash_claim"] = True
    # Replay evidence must be a real digest whose file is recomputed here.
    checks["replay_hash_matches"] = bool(
        evidence_sha and evidence_path and actual_sha == evidence_sha)
    return checks, summary


def _evidence_hash_ok(rec: dict) -> bool:
    sha = rec.get("evidence_sha256")
    if not sha:
        return False
    path = rec.get("evidence_path")
    if path:
        return bool(rec.get("actual_evidence_sha256")) and (
            rec["actual_evidence_sha256"] == sha)
    return True  # a real digest was declared, but there is no file to recompute


def evaluate(*, inputs: list[dict], computed: dict, frida: dict,
             unicorn: dict, replay_checks: dict, replay_summary: dict,
             generated_at: str) -> dict:
    """Join all gates into one conservative verdict with exact reasons."""
    blockers: list[str] = []
    failures: list[str] = []

    for entry in inputs:
        if entry.get("status") in ("MISSING", "INVALID"):
            detail = entry.get("error") or f"status {entry['status']}"
            blockers.append(f"{entry['kind']}: {detail}")
        elif entry.get("status") == "ABSENT":
            blockers.append(f"{entry['kind']}: no adapter supplied")

    for rec in (frida, unicorn):
        reason = rec.get("reason") or "no reason recorded"
        if rec["status"] == "BLOCKED":
            blockers.append(f"{rec['kind']}: {reason}")
        elif rec["status"] == "FAIL":
            failures.append(f"{rec['kind']}: {reason}")
        elif rec["status"] != "OK":
            blockers.append(f"{rec['kind']}: unknown status {rec['status']!r}")
        for key in rec.get("invalid_hash_claims", []):
            failures.append(f"{rec['kind']}: {key} is not a real SHA-256 value")
    if replay_summary.get("invalid_hash_claim"):
        failures.append("input-scenario: replay.evidence_sha256 is not a real SHA-256 value")

    hash_sets: dict[str, set[str]] = {key: set() for key in HASH_KEYS}
    for rec in (frida, unicorn):
        for key, values in rec.get("hashes", {}).items():
            hash_sets[key].update(values)
    hash_gate_ok = True
    for key in HASH_KEYS:
        claims = hash_sets[key]
        if len(claims) > 1:
            failures.append(f"conflicting {key} claims across adapters")
            hash_gate_ok = False
        actual = computed.get(key)
        if not actual:
            blockers.append(f"could not compute a real {key} from the CLI input")
            hash_gate_ok = False
        elif claims != {actual}:
            # Absent claims withhold the gate; a *different* claim is a FAIL.
            hash_gate_ok = False
            if claims:
                failures.append(f"{key} does not match the CLI-supplied file")
    # Each engine must attest the same digests: one engine's correct claim may
    # not silently compensate for another engine's missing or fake claim.
    for rec in (frida, unicorn):
        for key in HASH_KEYS:
            expected = computed.get(key)
            if not expected or set(rec.get("hashes", {}).get(key, [])) != {expected}:
                hash_gate_ok = False
    if not all(computed.get(key) for key in HASH_KEYS):
        blockers.append("all original/native/input hashes must be real CLI-supplied digests")

    # A BLOCKED/FAIL adapter contributes claims but no *usable* evidence: a
    # blocked Frida run cannot supply the capture or the oracle gate.
    frida_ran = frida.get("status") == "OK"
    oracle = frida.get("oracle") or {}
    oracle_name = ocm.norm_oracle(oracle.get("name") or "")
    oracle_ok = (
        frida_ran
        and oracle_name in REAL_ORACLES and oracle_name not in NO_ORACLES
        and oracle.get("verified") is True and oracle.get("executed") is True
    )

    capture = frida.get("capture") or {}
    capture_ok = frida_ran and bool(capture.get("evidence") or capture.get("calls"))
    if capture.get("path"):
        capture_ok = capture_ok and bool(capture.get("actual_sha256")) and (
            (not capture.get("sha256")) or capture["actual_sha256"] == capture["sha256"])

    unicorn_ok = (unicorn.get("status") == "OK"
                  and unicorn.get("agreement") is True
                  and bool(unicorn.get("evidence"))
                  and _evidence_hash_ok(unicorn))
    frida_file_ok = (not frida.get("evidence_path")) or _evidence_hash_ok(frida)
    replay_ok = all(replay_checks.values())

    checks = {
        "real_executed_oracle_flag": oracle_ok,
        "hashes_match_evidence": hash_gate_ok,
        "frida_capture_evidence": capture_ok and frida_file_ok,
        "unicorn_agreement": unicorn_ok,
        "replayed_input_scenario": replay_ok,
        "target_os_arch_evidenced": replay_checks.get("target_evidenced", False),
    }
    missing = [key for key in GATE_REASONS if not checks[key]]
    # Surface the exact replay sub-gate that failed, not just the parent gate.
    missing.extend(key for key, ok in replay_checks.items() if not ok)
    missing = list(dict.fromkeys(missing))

    if failures:
        verdict = "FAIL"
        reasons = list(dict.fromkeys(failures + blockers))
        reasons.append("PASS withheld: concrete failure or non-real hash in evidence")
    elif blockers:
        verdict = "BLOCKED"
        reasons = list(dict.fromkeys(blockers))
        reasons.append("PASS withheld: required adapter or input evidence is unavailable")
    elif all(checks.values()):
        verdict = "PASS"
        reasons = [
            "all gates satisfied: executed oracle, matching original/native/input "
            "hashes, Frida capture, Unicorn agreement and replayed input scenario "
            "with frame/state evidence"
        ]
    else:
        positive = (oracle_ok or capture_ok or unicorn_ok
                    or replay_checks.get("replay_executed", False))
        if not positive and not replay_checks.get("target_evidenced", False):
            verdict = "NOT TESTED"
        elif not positive and computed.get("native_sha256"):
            verdict = "BUILD ONLY"
        elif positive:
            verdict = "PARTIAL"
        else:
            verdict = "NOT TESTED"
        reasons = [f"PASS withheld: {GATE_REASONS.get(key, 'replay check ' + key + ' failed')}"
                   for key in missing]

    target_os = replay_summary.get("os")
    target_arch = replay_summary.get("arch")
    scope = build_scope(target_os, target_arch, verdict, reasons)

    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at,
        "verdict": verdict,
        "status": verdict,
        "claim": PASS_POLICY,
        "reasons": reasons,
        "inputs": inputs,
        "target": {"os": target_os, "arch": target_arch},
        "checks": checks,
        "missing_checks": missing,
        "hashes": {
            "original_sha256": computed.get("original_sha256"),
            "native_sha256": computed.get("native_sha256"),
            "input_sha256": computed.get("input_sha256"),
            "adapter_claims": {key: sorted(values) for key, values in hash_sets.items()},
        },
        "engines": {"frida": frida, "unicorn": unicorn},
        "replay": replay_summary,
        "scope": scope,
    }


def build_scope(target_os: str | None, target_arch: str | None,
                verdict: str, reasons: list[str]) -> dict:
    """Exactly one cell can be evidenced; every other pair is DEFERRED."""
    cells = []
    for os_name in OSES:
        for arch_name in ARCHES:
            evidenced = os_name == target_os and arch_name == target_arch
            cells.append({
                "os": os_name,
                "arch": arch_name,
                "key": f"{os_name}|{arch_name}",
                "evidenced": evidenced,
                "status": verdict if evidenced else "DEFERRED",
                "reason": ("; ".join(reasons) if evidenced else
                           "no executed-oracle/Frida/Unicorn/replay evidence for this "
                           "OS/arch pair; DEFERRED and BLOCKED for support claims"),
            })
    by_status = {status: sum(1 for cell in cells if cell["status"] == status)
                 for status in (*VERDICTS, "DEFERRED")
                 if any(cell["status"] == status for cell in cells)}
    return {
        "policy": PASS_POLICY,
        "target_verdict": verdict,
        "cells": cells,
        "summary": {
            "total": len(cells),
            "by_status": by_status,
            "evidenced_pairs": [cell["key"] for cell in cells if cell["evidenced"]],
        },
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Hybrid pipeline verdict",
        "",
        f"Verdict: **{report['verdict']}**  ",
        f"Generated: {report['generated_at']}  ",
        f"Target: {report['target']['os'] or 'unknown'} / "
        f"{report['target']['arch'] or 'unknown'}",
        "",
        report["claim"],
        "",
        "## Reasons",
        "",
    ]
    lines.extend(f"- {reason}" for reason in report["reasons"])
    lines += ["", "## Gates", "", "| Gate | Result |", "| --- | --- |"]
    for key, ok in report["checks"].items():
        lines.append(f"| {key} | {'PASS' if ok else 'WITHHELD'} |")
    lines += ["", "## Hashes", ""]
    for key in HASH_KEYS:
        lines.append(f"- {key}: `{report['hashes'].get(key) or '-'}`")
    lines += ["", "## Inputs", "", "| Source | Path | Status | SHA-256 |",
              "| --- | --- | --- | --- |"]
    for item in report["inputs"]:
        lines.append(f"| {item['kind']} | `{item['path']}` | {item['status']} | "
                     f"`{item.get('sha256') or '-'}` |")
    lines += ["", "## OS/arch scope", "",
              "| OS | Arch | Status | Evidenced |", "| --- | --- | --- | --- |"]
    for cell in report["scope"]["cells"]:
        lines.append(f"| {cell['os']} | {cell['arch']} | {cell['status']} | "
                     f"{'yes' if cell['evidenced'] else 'no'} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--original", required=True, type=Path, help="original PE file")
    ap.add_argument("--native", required=True, type=Path, help="native binary")
    ap.add_argument("--input-scenario", required=True, type=Path,
                    help="scripted input scenario JSON")
    ap.add_argument("--out", required=True, type=Path, help="output directory")
    ap.add_argument("--frida-adapter", default="", metavar="PATH",
                    help="Frida adapter module (.py) or JSON evidence file")
    ap.add_argument("--unicorn-adapter", default="", metavar="PATH",
                    help="Unicorn adapter module (.py) or JSON evidence file")
    ap.add_argument("--timeout", type=int, default=15,
                    help="hard wall-clock deadline for each adapter (default: 15)")
    ap.add_argument("--json", nargs="?", const="report.json", default="report.json",
                    metavar="PATH", help="JSON report name in --out (always written)")
    ap.add_argument("--markdown", nargs="?", const="report.md", default=None,
                    metavar="PATH", help="also write a Markdown report in --out")
    ap.add_argument("--generated-at", default=None,
                    help="ISO UTC timestamp override for deterministic output")
    args = ap.parse_args(argv)

    if args.timeout < 1:
        ap.error("--timeout must be at least 1 second")

    inputs: list[dict] = []
    computed: dict[str, str | None] = {}
    for kind, path in (("original", args.original), ("native", args.native)):
        digest = sha256_of(path)
        empty = path.is_file() and path.stat().st_size == 0
        status = "OK" if digest and path.is_file() and not empty else "MISSING"
        entry = {"kind": kind, "path": str(path), "sha256": digest, "status": status}
        if empty:
            entry["error"] = f"{kind} file is empty; no build evidence"
        inputs.append(entry)
        computed[f"{kind}_sha256"] = None if empty else digest

    scenario, scenario_entry, scenario_error = load_json_document(
        args.input_scenario, "input-scenario")
    if scenario_error:
        scenario_entry["error"] = scenario_error
    inputs.append(scenario_entry)
    computed["input_sha256"] = (scenario_entry["sha256"]
                                if scenario_entry["status"] == "OK" else None)

    replay_checks, replay_summary = validate_replay(
        scenario, args.input_scenario.resolve().parent)
    context = {
        "original": str(args.original),
        "native": str(args.native),
        "scenario_path": str(args.input_scenario),
        "scenario": scenario,
        "events": (scenario.get("events") if isinstance(scenario, dict) else None),
        "hashes": dict(computed),
        "timeout": args.timeout,
        "out_dir": str(args.out),
        "target": {"os": replay_summary["os"], "arch": replay_summary["arch"]},
    }
    frida, frida_entry = load_adapter(
        args.frida_adapter, "frida", context, args.timeout)
    unicorn, unicorn_entry = load_adapter(
        args.unicorn_adapter, "unicorn", context, args.timeout)
    inputs.extend([frida_entry, unicorn_entry])

    report = evaluate(
        inputs=inputs, computed=computed, frida=frida, unicorn=unicorn,
        replay_checks=replay_checks, replay_summary=replay_summary,
        generated_at=args.generated_at or utc_now())

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = Path(args.json)
    json_path = json_path if json_path.is_absolute() else out_dir / json_path
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")

    markdown_path = None
    if args.markdown:
        markdown_path = Path(args.markdown)
        markdown_path = (markdown_path if markdown_path.is_absolute()
                         else out_dir / markdown_path)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown(report), encoding="utf-8")

    print(f"{report['verdict']}: "
          f"{report['reasons'][0] if report['reasons'] else 'no reason recorded'}")
    print(f"json: {json_path}")
    if markdown_path:
        print(f"markdown: {markdown_path}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
