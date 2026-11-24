#!/usr/bin/env python3
"""Per-target probe: dual-engine differential + all cross-OS build directions.

Fail-closed: every engine/build that cannot run records a status and exact
reason.  No placeholder PASS.  Invoked by tools/run_corpus_verification.py.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import hybrid_diff, linux_to_windows, win32_crossbuild, wine_oracle  # noqa: E402

B1 = Path("/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/b1-builds")


def trim(text: str, limit: int = 3000) -> str:
    return text[-limit:] if len(text) > limit else text


def probe(slug: str) -> dict:
    edir = ROOT / "evidence" / "corpus" / slug
    v = json.loads((edir / "verification.json").read_text())
    exe = Path(v["source_exe"])
    native = Path(v["native"]["path"]) if v["native"].get("path") else None
    out = {"slug": slug}

    # --- Unicorn + live Frida + native replay. Each engine is attempted
    # independently so one engine's hard failure cannot mask the others.
    from tools import input_replay_diff
    try:
        u = hybrid_diff.unicorn_signal(exe)
    except BaseException as exc:  # analyze_pe calls sys.exit on PE32+ inputs
        u = {"status": "BLOCKED",
             "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
    u["reason"] = trim(str(u.get("reason", "")))
    try:
        f = hybrid_diff.frida_signal(exe, 3)
        f["reason"] = trim(str(f.get("reason", "")))
    except BaseException as exc:
        f = {"status": "BLOCKED",
             "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
    try:
        if native is None:
            n = {"status": "BLOCKED", "reason": "no native binary supplied"}
        else:
            n = input_replay_diff.diff_one(exe, native, b"", [], exe.parent, 8)
            for k in ("original_lines", "native_lines"):
                if k in n:
                    n[k] = [str(x)[:300] for x in n[k][:10]]
    except BaseException as exc:
        n = {"status": "BLOCKED",
             "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
    engines = [u, f, n]
    blocked = [f"{k}: {e.get('reason', '')}" for k, e in
               zip(("unicorn", "frida", "native"), engines)
               if e.get("status") == "BLOCKED"]
    diverged = [e.get("reason", "") for e in engines
                if e.get("status") == "DIVERGED"]
    status = ("BLOCKED" if blocked else "DIVERGED" if diverged else "MATCH")
    out["engines"] = {"status": status,
                      "reason": "; ".join(blocked + diverged),
                      "unicorn": u, "frida": f, "native": n,
                      "note": "CPU/process-level evidence only; BLOCKED is never MATCH"}
    (edir / "engines.json").write_text(json.dumps(out["engines"], indent=2) + "\n")

    # --- macOS host -> Windows PE (source-level cross-build of the real project).
    project = B1 / slug / "project"
    cross = {"status": "BLOCKED", "reason": "no generated C project available"}
    if project.is_dir():
        bits = 64 if v["b1_arch"] == "pe32plus_x64" else 32
        with tempfile.TemporaryDirectory() as td:
            pe_out = Path(td) / (exe.stem + ".exe")
            try:
                cross = win32_crossbuild.cross_build(project, pe_out, bits, timeout=75)
            except BaseException as exc:
                cross = {"status": "BLOCKED",
                         "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
            if cross.get("compiler_output"):
                cross["compiler_output"] = trim(cross["compiler_output"])
            if cross.get("status") == "OK":
                try:
                    wine = wine_oracle.find_wine()
                    if not wine:
                        cross["wine_run"] = {"status": "BLOCKED",
                                             "reason": "no Wine/Highball engine found"}
                    else:
                        run = wine_oracle.run_original(
                            wine, wine_oracle.DEFAULT_BOTTLE, pe_out, [], 10,
                            stdin_data=b"")
                        cross["wine_run"] = {
                            "status": "OK" if run["exit_code"] not in ("TIMEOUT",) else "BLOCKED",
                            "exit_code": run["exit_code"],
                            "stdout": trim(run["stdout"]), "stderr": trim(run["stderr"]),
                        }
                except Exception as exc:
                    cross["wine_run"] = {"status": "BLOCKED",
                                         "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
    out["macos_to_windows"] = cross
    (edir / "macos-to-windows.json").write_text(json.dumps(cross, indent=2) + "\n")

    # --- Linux-host -> Windows PE: only real pre-existing C source may be used.
    with tempfile.TemporaryDirectory() as td:
        bits = 64 if v["b1_arch"] == "pe32plus_x64" else 32
        try:
            l2w = linux_to_windows.linux_to_windows(
                out=Path(td) / (exe.stem + "-l2w.exe"), bits=bits,
                run=False, roots=[exe.parent])
            l2w["reason"] = trim(str(l2w.get("reason", "")))
        except BaseException as exc:
            l2w = {"status": "BLOCKED",
                   "code": "PROBE_EXCEPTION",
                   "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
    out["linux_to_windows"] = l2w
    (edir / "linux-to-windows.json").write_text(json.dumps(l2w, indent=2) + "\n")
    return out


if __name__ == "__main__":
    rec = probe(sys.argv[1])
    print(json.dumps({"slug": rec["slug"],
                      "engines": rec["engines"].get("status"),
                      "macos_to_windows": rec["macos_to_windows"].get("status"),
                      "linux_to_windows": rec["linux_to_windows"].get("status")}))
