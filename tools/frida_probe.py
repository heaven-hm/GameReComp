#!/usr/bin/env python3
"""Frida-based call probe for differential runs.

Used to observe *which* Win32/HLE calls an original Windows binary makes under
Highball's Wine engine, so a recompiled native run can be compared against real
behaviour rather than only exit codes. Frida needs a running PID or a spawn
command; --dry-run prints the exact script without attaching (useful in CI).

This is an observation tool, not a compatibility claim: a matching call trace
is process-level evidence only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import frida
except ImportError:  # optional dependency
    frida = None

DEFAULT_SCRIPT = r"""
rpc.exports = { calls: [] };
var names = %s;
names.forEach(function (spec) {
  try {
    var parts = spec.split('!');
    var mod = parts.length > 1 ? parts[0] : null;
    var name = parts[parts.length - 1];
    var addr = mod ? Module.findExportByName(mod, name)
                   : Module.findExportByName(null, name);
    if (!addr) return;
    Interceptor.attach(addr, {
      onEnter: function () { rpc.exports.calls.push(spec); }
    });
  } catch (e) {}
});
"""


def script_source(names: list[str]) -> str:
    return DEFAULT_SCRIPT % json.dumps(names)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pid", type=int)
    ap.add_argument("--spawn", nargs="+", help="program and arguments to spawn")
    ap.add_argument("--hook", action="append", default=[],
                    help="function to count, optionally module!function")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    names = args.hook or ["ExitProcess", "MessageBoxW", "CreateFileW",
                          "GetTickCount", "RegOpenKeyExW"]
    source = script_source(names)
    if args.dry_run:
        print(source)
        return 0
    if frida is None:
        print("frida is not installed", file=sys.stderr)
        return 2
    if args.pid is None and not args.spawn:
        print("need --pid or --spawn (or --dry-run)", file=sys.stderr)
        return 2
    try:
        if args.spawn:
            pid = frida.spawn(args.spawn)
            session = frida.attach(pid)
            frida.resume(pid)
        else:
            session = frida.attach(args.pid)
        script = session.create_script(source)
        script.load()
        import time
        time.sleep(args.timeout)
        calls = script.exports_sync.calls()
        session.detach()
    except Exception as exc:
        print(f"frida probe failed: {exc}", file=sys.stderr)
        return 3
    doc = {"hooks": names, "calls": calls,
           "counts": {n: calls.count(n) for n in names}}
    text = json.dumps(doc, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
