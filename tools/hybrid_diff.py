#!/usr/bin/env python3
"""Hybrid differential evidence: Unicorn (original bytes) + Frida (original process).

Every tested binary gets BOTH signals attempted:

1. UNICORN drives the ORIGINAL x86 bytes with controlled entry states
   (tools/lift/difftest.run_unicorn_function) -- a CPU-level reference
   independent of Wine and of the recompiled C. ``--function`` selects a real
   function inside the PE instead of the PE entry: a console PE entry normally
   calls unresolved IAT imports, which is why the tetris/arith entry faults with
   UC_ERR_READ_UNMAPPED. A leaf/internal function that touches only registers and
   its own stack (e.g. arith-calc.exe's fib at 0x4010A0) runs fault-free and its
   output varies with the entry seed, so the comparison is non-vacuous.
2. FRIDA instruments the ORIGINAL process under Highball's Wine engine
   (tools/frida_probe) and counts selected Win32/HLE calls. Live mode is opt-in
   and ALWAYS bounded: the attach runs in a child process group with a hard
   wall-clock deadline; on expiry the group (and the spawned Wine PID) is
   SIGKILLed and the exact reason is recorded.
3. The NATIVE port is replayed with identical stdin (tools/input_replay_diff).
   The ORIGINAL side of that comparison always runs via ``highball run <exe>``
   when the CLI is available; a raw Wine invocation is a labelled fallback only.

A row is MATCH only when every engine that could run agrees. The default stays
fail-closed: any BLOCKED engine makes the row BLOCKED. For hosts where Frida
cannot attach at all (macOS taskgated, no root, no Frida, no Wine), an operator
may pass ``--process-level-match``: Unicorn and native replay must still both
succeed, every blocked engine's exact reason is carried in the report, and the
claim is explicitly downgraded to process-level (never 100%).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from tools import frida_probe, input_replay_diff


ROOT = Path(__file__).resolve().parents[1]


def _blocked(reason: str, cls: str, **extra) -> dict:
    return {"status": "BLOCKED", "reason": reason, "blocked_class": cls, **extra}


def _as_va(value) -> int:
    return int(str(value), 0) if isinstance(value, str) else int(value)


def unicorn_signal(exe: Path, cases: int = 2, va=None, seeds=None) -> dict:
    """Drive the ORIGINAL bytes at ``va`` (default: PE entry) through Unicorn.

    ``va`` is an explicit real function address in a code section; it makes the
    'no named addresses' case legal because register/stack-only functions name
    no absolute guest memory. ``seeds`` are concrete entry values so a caller can
    prove the signature varies (a constant signature would be a vacuous pass).
    """
    try:
        from tools.lift import difftest
        from tools.pe.pe_analyze import analyze_pe
    except Exception as exc:
        return _blocked(f"unavailable: {exc}", "environment")
    try:
        info = analyze_pe(str(exe))
        entry = info.image_base + info.entry_point_rva
        explicit = va is not None
        fn = _as_va(va) if explicit else entry
        blob, section = difftest.pe_bytes_at(str(exe), fn, 64)
        lo, size = difftest.oracle_window(str(exe))
        named = difftest.named_addrs_from_code(blob, fn, lo, lo + size)
        if not named and not explicit:
            return _blocked(
                "no named guest addresses at entry; pass --function with a real "
                "internal/leaf function for register-only comparison", "fault")
        values = ([_as_va(s) for s in seeds] if seeds is not None
                  else [0x1000 + c for c in range(max(1, cases))])
        results = difftest.run_unicorn_function(
            str(exe), fn, named, lambda c: values[c], len(values))
        distinct = len({r["regs"]["eax"] for r in results})
        return {"status": "OK", "blocked_class": None,
                "function": hex(fn), "entry": hex(entry),
                "from_entry": not explicit, "section": section,
                "cases": len(values), "seeds": [hex(v) for v in values],
                "named": [hex(a) for a in named],
                "distinct_eax": distinct, "nonvacuous": distinct > 1,
                "signatures": [str(r)[:400] for r in results]}
    except (Exception, SystemExit) as exc:
        text = f"{type(exc).__name__}: {str(exc)[:220]}"
        cls = "fault" if any(w in str(exc).lower()
                             for w in ("fault", "unmapped", "oraclefault")) else "environment"
        return _blocked(text, cls, function=hex(_as_va(va)) if va is not None else None)


def frida_signal(exe: Path, timeout: int = 5, hooks=None,
                 live: bool = True) -> dict:
    """Attempt a real, hard-bounded Frida capture; never fabricate a call trace."""
    hooks = list(hooks or [])
    source = frida_probe.script_source(hooks)
    sha = hashlib.sha256(source.encode()).hexdigest()[:16]
    if not live:
        return _blocked(
            "live Frida attach disabled (pass --live-frida to run it)",
            "environment", script_sha256=sha)
    rec = frida_probe.bounded_probe(source, hooks, exe, timeout,
                                    root=ROOT, script_sha256=sha)
    rec.setdefault("script_sha256", sha)
    # A Frida failure is an environment/observability limit, not a guest fault:
    # the exact exception (e.g. PermissionDeniedError from taskgated) is kept.
    if rec.get("status") == "BLOCKED":
        rec["blocked_class"] = "environment"
    else:
        rec["blocked_class"] = None
    return rec


def hybrid_one(exe: Path, native: Path | None, stdin: bytes,
               hooks=None, timeout: int = 10, live_frida: bool = False,
               function_va=None, seeds=None,
               process_level_match: bool = False) -> dict:
    u = unicorn_signal(exe, va=function_va, seeds=seeds)
    f = frida_signal(exe, max(1, min(timeout, 5)), hooks, live=live_frida)
    n = {"status": "BLOCKED", "reason": "no native binary supplied",
         "blocked_class": "environment"}
    if native is not None:
        n = input_replay_diff.diff_one(exe, native, stdin, [], exe.parent, timeout)
        # A native timeout is a real failure to run, not an environment skip.
        if n.get("status") == "BLOCKED":
            n.setdefault("blocked_class", "fault")

    signals = {"unicorn": u, "frida": f, "native": n}
    diverged = [k for k, s in signals.items() if s.get("status") == "DIVERGED"]
    faulted = [k for k, s in signals.items()
               if s.get("status") == "BLOCKED" and s.get("blocked_class") == "fault"]
    env_blocked = [k for k, s in signals.items()
                   if s.get("status") == "BLOCKED" and s.get("blocked_class") == "environment"]
    vacuous = (u.get("status") == "OK" and function_va is not None
               and u.get("nonvacuous") is False)

    if diverged:
        status = "DIVERGED"
    elif faulted or vacuous:
        status = "BLOCKED"
    elif env_blocked:
        # Only the process-level (native replay + Unicorn) MATCH is allowed;
        # Frida must not be the engine being skipped to obtain it.
        allowed = (process_level_match
                   and set(env_blocked).issubset({"frida"})
                   and u.get("status") == "OK"
                   and n.get("status") == "MATCH")
        status = "MATCH" if allowed else "BLOCKED"
    else:
        status = "MATCH"

    if status == "MATCH" and env_blocked:
        reason = ("process-level MATCH: Unicorn and native replay agree; "
                  + "; ".join(f"{k}: {signals[k]['reason']}" for k in env_blocked)
                  + " -- NOT a 100% compatibility claim")
    elif status == "BLOCKED" and vacuous and not faulted:
        reason = "unicorn: non-vacuous signature required (all cases identical)"
    elif status == "BLOCKED":
        reason = "; ".join(
            f"{k}: {signals[k].get('reason', '')}"
            for k, s in signals.items() if s.get("status") == "BLOCKED")
    else:
        reason = "all available engines agree"

    claim = ("CPU/process-level differential evidence only; not gameplay or frame "
             "equivalence and not a 100% compatibility claim")
    artifacts = {}
    for label, path in (("original", exe), ("native", native)):
        if path is not None and Path(path).is_file():
            artifacts[label] = {"path": str(path),
                                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    return {"exe": str(exe), "status": status, "reason": reason,
            "original_runner": n.get("original", {}).get("runner", "not-run"),
            "matched_engines": [k for k, s in signals.items()
                                if s.get("status") in ("OK", "MATCH")],
            "blocked_engines": env_blocked + faulted,
            "diverged_engines": diverged,
            "claim": claim, "process_level_only": bool(env_blocked),
            "artifacts": artifacts,
            "unicorn": u, "frida": f, "native": n, "note": claim}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe", required=True, type=Path)
    ap.add_argument("--native", type=Path)
    ap.add_argument("--input", default="")
    ap.add_argument("--hook", action="append", default=[])
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--live-frida", action="store_true",
                    help="attempt a hard-bounded live Frida attach to the Wine process")
    ap.add_argument("--function", help="real function VA inside .text (default: PE entry)")
    ap.add_argument("--seeds", help="comma-separated entry seeds, e.g. 1,2,3,4,5")
    ap.add_argument("--process-level-match", action="store_true",
                    help="allow MATCH when only Frida is environment-BLOCKED; "
                         "records the exact reason and downgrades the claim")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    seeds = [int(s, 0) for s in args.seeds.split(",")] if args.seeds else None
    rec = hybrid_one(args.exe, args.native, args.input.encode(), args.hook,
                     args.timeout, args.live_frida, args.function, seeds,
                     args.process_level_match)
    text = json.dumps(rec, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    return 0 if rec["status"] == "MATCH" else 1


if __name__ == "__main__":
    sys.exit(main())
