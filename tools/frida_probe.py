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


# Frida's local attach on macOS goes through taskgated (task_for_pid). On a
# non-root, SIP-enabled host it fails with PermissionDeniedError instead of
# hanging, but on Wine it has also been observed to hang while the process is
# being resumed. Never let that hang into a caller: the probe runs in a child
# process group with a wall-clock deadline and the group is SIGKILLed on expiry.
# The worker writes its result to a file *before* the parent is allowed to read
# it, so a missing file is itself an honest BLOCKED reason, never a fake OK.
_FRIDA_WORKER = r'''
import json, os, shutil, sys, time
from pathlib import Path
cfg_path, result_path, pid_path = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.loads(Path(cfg_path).read_text())
result = {"status": "BLOCKED", "reason": "worker exited without a result",
          "script_sha256": cfg.get("script_sha256", "")}
pid = None
try:
    import frida
    from tools import wine_oracle
    wine = cfg.get("wine")
    if not wine:
        result["reason"] = "no Wine/Highball engine found (RECOMP_WINE unset)"
    else:
        exe = Path(cfg["exe"])
        target_dir = Path(cfg["bottle"]) / "drive_c" / "recomp-oracle"
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exe, target_dir / exe.name)
        child_env = dict(os.environ)
        child_env.update({"WINEPREFIX": str(target_dir.parents[1]),
                          "WINEDEBUG": "-all",
                          "WINEDLLOVERRIDES": "winemenubuilder.exe=d"})
        pid = frida.spawn([wine, "C:\\recomp-oracle\\" + exe.name], env=child_env)
        Path(pid_path).write_text(str(pid))  # let the parent kill it on timeout
        session = frida.attach(pid)
        script = session.create_script(cfg["source"])
        script.load()
        frida.resume(pid)
        time.sleep(cfg["window"])
        calls = script.exports_sync.calls()
        session.detach()
        result = {"status": "OK", "calls": calls,
                  "counts": {h: calls.count(h) for h in cfg["hooks"]},
                  "script_sha256": cfg.get("script_sha256", ""), "pid": pid}
except BaseException as exc:
    result["reason"] = f"{type(exc).__name__}: {str(exc)[:400]}"
Path(result_path).write_text(json.dumps(result))
'''


def bounded_probe(source: str, hooks: list[str], exe: Path, timeout: int = 5,
                  *, root: Path | None = None, script_sha256: str = "",
                  window: float | None = None) -> dict:
    """Run a Frida capture in a killable child; always return a real status.

    The child is spawned in its own process group. If it exceeds ``timeout``
    seconds the whole group is SIGKILLed and the returned BLOCKED reason says so.
    ``status`` is never inferred from a silent process.
    """
    import hashlib
    import os
    import subprocess
    import tempfile

    root = Path(root) if root else Path(__file__).resolve().parents[1]
    hooks = list(hooks)
    window = max(0.5, min(3.0, float(timeout) - 2)) if window is None else float(window)
    from tools import wine_oracle

    sha = script_sha256 or hashlib.sha256(source.encode()).hexdigest()[:16]
    cfg = {"source": source, "hooks": hooks, "exe": str(exe),
           "wine": wine_oracle.find_wine(),
           "bottle": str(wine_oracle.find_bottle()), "window": window,
           "script_sha256": sha}
    with tempfile.TemporaryDirectory(prefix="frida-bounded-") as td:
        cfg_path, out_path, pid_path = (Path(td) / n for n in
                                        ("config.json", "result.json", "pid"))
        cfg_path.write_text(json.dumps(cfg))
        proc = subprocess.Popen([sys.executable, "-c", _FRIDA_WORKER,
                                 str(cfg_path), str(out_path), str(pid_path)],
                                cwd=str(root), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        killed = False
        try:
            _, err = proc.communicate(timeout=max(1, int(timeout)))
        except subprocess.TimeoutExpired:
            killed = True
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                proc.kill()
            _, err = proc.communicate(timeout=10)
        # Best effort: Frida's spawn can detach Wine from the worker's group.
        if killed and pid_path.is_file():
            try:
                os.kill(int(pid_path.read_text().strip()), 9)
            except (OSError, ValueError):
                pass
        if not out_path.is_file():
            return {"status": "BLOCKED",
                    "reason": f"worker produced no result (rc={proc.returncode}, "
                              f"killed_on_timeout={killed}, stderr={err.decode('utf-8', 'replace')[:200]})",
                    "script_sha256": sha, "timeout_s": timeout,
                    "killed_on_timeout": killed}
        rec = json.loads(out_path.read_text())
        rec["script_sha256"] = sha
        rec["timeout_s"] = timeout
        rec["killed_on_timeout"] = killed
        return rec


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
