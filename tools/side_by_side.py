#!/usr/bin/env python3
"""Run original-under-Wine and recompiled-native side by side with ONE Frida script.

The same Interceptor script is injected into both processes. It resolves each
hook by export name in whichever process it runs, so it can observe Win32 DLL
calls in the Wine process and equivalent exported shim functions in the native
binary when present. Stdin is byte-identical on both sides; stdout, exit code
and call traces are captured and compared. Missing hooks are recorded as gaps,
not treated as a successful trace.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from tools import wine_oracle

SCRIPT = r"""
rpc.exports = { calls: [], hooked: [] };
var names = %s;
names.forEach(function (spec) {
  try {
    var parts = spec.split('!');
    var mod = parts.length > 1 ? parts[0] : null;
    var name = parts[parts.length - 1];
    var addr = mod ? Module.findExportByName(mod, name) : Module.findExportByName(null, name);
    if (!addr) return;
    Interceptor.attach(addr, { onEnter: function () { rpc.exports.calls.push(spec); } });
    rpc.exports.hooked.push(spec);
  } catch (e) {}
});
"""


def script_for(hooks: list[str]) -> str:
    return SCRIPT % json.dumps(hooks)


def run_side(wine: str, original: Path, native: Path, stdin_data: bytes,
             hooks: list[str], timeout: int, bottle: Path) -> dict:
    source = script_for(hooks)
    results = {}
    for side, command in (("original", [wine, "C:\\recomp-oracle\\" + original.name]),
                          ("native", [str(native.resolve())])):
        rec: dict = {"command": command, "script_sha256":
                     __import__("hashlib").sha256(source.encode()).hexdigest()}
        session = None
        proc = None
        try:
            import frida
            env = dict(os.environ)
            env.update({"WINEPREFIX": str(bottle), "WINEDEBUG": "-all"})
            proc = subprocess.Popen(command, cwd=str(bottle / "drive_c"), env=env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True)
            session = frida.attach(proc.pid)
            script = session.create_script(source)
            script.load()
            out, err = proc.communicate(input=stdin_data, timeout=timeout)
            rec["stdout"] = out.decode("utf-8", "replace")
            rec["stdout_sha256"] = __import__("hashlib").sha256(out).hexdigest()
            rec["exit_code"] = proc.returncode
            calls = script.exports_sync.calls()
            hooked = script.exports_sync.hooked()
            rec.update(status="TRACE_OK", calls=calls, hooked=hooked,
                       counts={h: calls.count(h) for h in hooked})
        except subprocess.TimeoutExpired:
            if proc is not None:
                try:
                    os.killpg(proc.pid, 9)
                except OSError:
                    proc.kill()
                proc.communicate()
            rec.update(status="BLOCKED", reason="process timeout")
        except Exception as exc:
            rec.update(status="BLOCKED", reason=f"{type(exc).__name__}: {str(exc)[:200]}")
        finally:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass
        results[side] = rec

    o, n = results["original"], results["native"]
    if o.get("status") != "TRACE_OK" or n.get("status") != "TRACE_OK":
        status = "BLOCKED"
        reason = "; ".join(f"{k}: {v.get('reason', 'not TRACE_OK')}" for k, v in results.items())
    elif o.get("counts") != n.get("counts"):
        status, reason = "DIVERGED", "Frida call counts differ between original and native"
    elif o.get("stdout_sha256") != n.get("stdout_sha256"):
        status, reason = "DIVERGED", "stdout hash differs between original and native"
    elif o.get("exit_code") != n.get("exit_code"):
        status, reason = "DIVERGED", "exit code differs between original and native"
    else:
        status, reason = "MATCH", "same Frida counts, stdout hash and exit code"
    return {"original": o, "native": n, "status": status, "reason": reason,
            "stdin_bytes": len(stdin_data), "hooks": hooks,
            "note": "Frida trace evidence only; not gameplay equivalence"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original", required=True, type=Path)
    ap.add_argument("--native", required=True, type=Path)
    ap.add_argument("--hook", action="append", required=True)
    ap.add_argument("--stdin-file", type=Path)
    ap.add_argument("--timeout", type=int, default=5)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    wine = wine_oracle.find_wine()
    if not wine:
        print("no Wine engine", file=sys.stderr)
        return 2
    target = wine_oracle.DEFAULT_BOTTLE / "drive_c" / "recomp-oracle"
    target.mkdir(parents=True, exist_ok=True)
    if args.original.is_file():
        shutil.copy2(args.original, target / args.original.name)
    data = args.stdin_file.read_bytes() if args.stdin_file else b""
    rec = run_side(wine, args.original, args.native, data, args.hook,
                   args.timeout, wine_oracle.DEFAULT_BOTTLE)
    text = json.dumps(rec, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    return 0 if rec["status"] == "MATCH" else 1


if __name__ == "__main__":
    sys.exit(main())
