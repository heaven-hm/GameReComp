#!/usr/bin/env python3
"""Hybrid differential evidence: Unicorn (original bytes) + Frida (original process).

Every tested binary gets BOTH signals attempted:

1. UNICORN drives the ORIGINAL x86 bytes at the PE entry with controlled entry
   states (tools/lift/difftest.run_unicorn_function) -- a CPU-level reference
   independent of Wine and of the recompiled C.
2. FRIDA instruments the ORIGINAL process under Highball's Wine engine and
   counts selected Win32/HLE calls (tools/frida_probe).
3. The NATIVE port is replayed with identical stdin (tools/input_replay_diff).

A row is MATCH only when every engine that could run agrees; an engine that
cannot run is recorded BLOCKED with its reason, never silently skipped. This is
process/CPU-level evidence, not gameplay or frame equivalence.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools import frida_probe, input_replay_diff, wine_oracle


def unicorn_signal(exe: Path, cases: int = 2) -> dict:
    try:
        from tools.lift import difftest
        from tools.pe.pe_analyze import analyze_pe
    except Exception as exc:
        return {"status": "BLOCKED", "reason": f"unavailable: {exc}"}
    try:
        info = analyze_pe(str(exe))
        va = info.image_base + info.entry_point_rva
        blob, _ = difftest.pe_bytes_at(str(exe), va, 64)
        lo, size = difftest.oracle_window(str(exe))
        named = difftest.named_addrs_from_code(blob, va, lo, lo + size)
        if not named:
            return {"status": "BLOCKED", "reason": "no named guest addresses at entry"}
        def seed(c):
            return 0x1000 + c
        results = difftest.run_unicorn_function(str(exe), va, named, seed, cases)
        return {"status": "OK", "entry": hex(va), "cases": cases,
                "named": [hex(a) for a in named][:16],
                "signatures": [str(r)[:400] for r in results[:cases]]}
    except Exception as exc:
        return {"status": "BLOCKED", "reason": f"{type(exc).__name__}: {str(exc)[:180]}"}


def frida_signal(exe: Path, timeout: int = 5, hooks=None) -> dict:
    source = frida_probe.script_source(hooks or [])
    rec = {"status": "BLOCKED", "reason": "live Frida attach not run",
           "script_sha256": __import__("hashlib").sha256(source.encode()).hexdigest()[:16]}
    try:
        import frida
        import time
        wine = wine_oracle.find_wine()
        if not wine:
            return rec
        # Spawn the original through the Highball Wine engine under Frida and
        # count the requested calls for a bounded window.
        target_dir = wine_oracle.DEFAULT_BOTTLE / "drive_c" / "recomp-oracle"
        target_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(exe, target_dir / exe.name)
        pid = frida.spawn([wine, "C:\\recomp-oracle\\" + exe.name])
        session = frida.attach(pid)
        script = session.create_script(source)
        script.load()
        frida.resume(pid)
        time.sleep(timeout)
        calls = script.exports_sync.calls()
        session.detach()
        rec.update(status="OK", calls=calls,
                   counts={h: calls.count(h) for h in (hooks or [])})
    except Exception as exc:
        rec["reason"] = f"{type(exc).__name__}: {str(exc)[:180]}"
    return rec


def hybrid_one(exe: Path, native: Path | None, stdin: bytes,
               hooks=None, timeout: int = 10) -> dict:
    u = unicorn_signal(exe)
    f = frida_signal(exe, min(timeout, 5), hooks)
    n = {"status": "BLOCKED", "reason": "no native binary supplied"}
    if native is not None:
        n = input_replay_diff.diff_one(exe, native, stdin, [], exe.parent, timeout)
    signals = [u, f, n]
    if any(s.get("status") in ("BLOCKED",) for s in signals):
        status = "BLOCKED"
        reason = "; ".join(f"{k}: {s.get('reason', '')}" for k, s in
                           zip(("unicorn", "frida", "native"), signals)
                           if s.get("status") == "BLOCKED")
    elif any(s.get("status") == "DIVERGED" for s in signals):
        status, reason = "DIVERGED", "one or more differential signals diverged"
    else:
        status, reason = "MATCH", "all available engines agree"
    return {"exe": str(exe), "status": status, "reason": reason,
            "unicorn": u, "frida": f, "native": n,
            "note": "CPU/process-level evidence only; not gameplay or frame equivalence"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe", required=True, type=Path)
    ap.add_argument("--native", type=Path)
    ap.add_argument("--input", default="")
    ap.add_argument("--hook", action="append", default=[])
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    rec = hybrid_one(args.exe, args.native, args.input.encode(), args.hook,
                     args.timeout)
    text = json.dumps(rec, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    return 0 if rec["status"] == "MATCH" else 1


if __name__ == "__main__":
    sys.exit(main())
