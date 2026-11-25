#!/usr/bin/env python3
"""Generic differential engine: same input -> original vs rebuilt -> evidence.

Method follows the IGI differential projects (igi-recomp's instruction-form
sweeps and A/B trace comparison; IGI-RE's same-state compare of return, stack,
memory and call order), reduced to what can be measured generically on any
GameReComp target:

  process  exit code + stdout + stderr, byte for byte, from identical args and
           stdin; optional frame-set and watched-value trace comparison.
  function reuse tools/lift/difftest.py reports (Unicorn vs generated C).

Anything that cannot be measured is BLOCKED with the verbatim reason. A missing
oracle is never a PASS, and a partial match is never a full PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = 1

# Highball's own launcher/Wine chatter is not guest output. Keep the raw text
# in the evidence file; strip only these known prefixes for the comparison.
EXE_PATH = re.compile(r"(?:[A-Za-z]:\\[^\s]*|/[^\s]*)(?:\.exe)", re.I)
LAUNCHER_PREFIXES = (
    "Launching ", "exit=", "msync:", "wine: failed to open",
    "[pe] ", "[host] ", "[module] ", "[win32hle] ", "[screen] ", "[screenshot] ",
)
ERR_PREFIX = re.compile(r"^[0-9a-f]{4}:(?:err|warn|fixme):")

# Runner-infrastructure failures are missing evidence, not a guest mismatch.
BLOCK_PATTERNS = re.compile(
    r"Cannot connect to the Docker daemon|docker daemon running|"
    r"command not found|No such file or directory|Permission denied", re.I)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_guest(text: str) -> str:
    """Strip launcher/Wine lines so only the guest process output remains."""
    out = []
    for line in text.splitlines():
        if line.startswith(LAUNCHER_PREFIXES) or ERR_PREFIX.match(line):
            continue
        out.append(line)
    return EXE_PATH.sub("<exe>", "\n".join(out).replace("C:\\original", "<exe>").replace("/original", "<exe>")) + ("\n" if out else "")


def first_difference(a: str, b: str) -> dict | None:
    al, bl = a.splitlines(), b.splitlines()
    for i in range(max(len(al), len(bl))):
        x = al[i] if i < len(al) else "<missing>"
        y = bl[i] if i < len(bl) else "<missing>"
        if x != y:
            return {"line": i + 1, "original": x[:300], "rebuilt": y[:300]}
    return None


def frame_hashes(path: Path) -> dict:
    if not path.is_dir():
        return {}
    out = {}
    for f in sorted(path.iterdir()):
        if f.is_file() and f.suffix.lower() in (".bmp", ".png"):
            out[f.name] = sha256(f.read_bytes())
    return out


def compare_traces(path_a: Path, path_b: Path, tolerance: float) -> dict:
    """A/B watched-value traces; each row is: time name value ...

    Numeric columns are compared with an absolute tolerance; non-numeric
    columns must match exactly. This is igi-recomp's tick_compare idea with
    no game-specific addresses or object layouts.
    """
    def load(p):
        rows = {}
        for line in p.read_text(errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            rows[round(float(parts[0]), 4)] = parts[2:]
        return rows

    a, b = load(path_a), load(path_b)
    common = sorted(set(a) & set(b))
    fields = max((len(v) for v in a.values()), default=0)
    worst = [0.0] * fields
    exact_fail = []
    for t in common:
        for i, (x, y) in enumerate(zip(a[t], b[t])):
            try:
                delta = abs(float(x) - float(y))
                worst[i] = max(worst[i], delta)
            except ValueError:
                if x != y:
                    exact_fail.append({"time": t, "column": i, "original": x, "rebuilt": y})
    numeric_ok = all(v <= tolerance for v in worst)
    return {"times_compared": len(common), "max_delta_per_column": worst,
            "tolerance": tolerance, "numeric_ok": numeric_ok,
            "exact_mismatches": exact_fail[:20],
            "status": "PASS" if numeric_ok and not exact_fail and common else "FAIL"}


def run_cmd(template: str, timeout: float, stdin: bytes | None = None) -> dict:
    """Run a command template; {exe} is substituted by the caller."""
    t0 = time.time()
    try:
        p = subprocess.run(shlex.split(template), input=stdin, capture_output=True, timeout=timeout)
        return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr,
                "seconds": round(time.time() - t0, 2), "error": None}
    except subprocess.TimeoutExpired as e:
        return {"rc": 124, "stdout": e.stdout or b"", "stderr": e.stderr or b"",
                "seconds": round(time.time() - t0, 2), "error": "timeout"}
    except (OSError, ValueError) as e:
        return {"rc": None, "stdout": b"", "stderr": str(e).encode(),
                "seconds": round(time.time() - t0, 2), "error": str(e)}


def process_diff(args) -> dict:
    rec = {"schema": SCHEMA, "kind": "process", "target": str(args.original),
           "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    original, rebuilt = Path(args.original), Path(args.rebuilt)
    if not original.is_file():
        rec.update(status="BLOCKED", reason="original executable not found: %s" % original)
        return rec
    if not rebuilt.is_file() and not args.rebuilt_cmd:
        rec.update(status="BLOCKED", reason="rebuilt executable not found: %s" % rebuilt)
        return rec
    rec["original_sha256"] = sha256(original.read_bytes())
    if rebuilt.is_file():
        rec["rebuilt_sha256"] = sha256(rebuilt.read_bytes())
    stdin = Path(args.stdin_file).read_bytes() if args.stdin_file else (args.stdin or "").encode()
    a = run_cmd(args.original_cmd.format(exe=shlex.quote(str(original))), args.timeout, stdin)
    b = run_cmd((args.rebuilt_cmd or "{exe}").format(exe=shlex.quote(str(rebuilt))), args.timeout, stdin)
    rec["commands"] = {"original": args.original_cmd.format(exe=str(original)),
                       "rebuilt": (args.rebuilt_cmd or "{exe}").format(exe=str(rebuilt))}
    rec["raw"] = {k: {kk: (vv.decode("utf-8", errors="replace") if isinstance(vv, bytes) else vv)
                      for kk, vv in v.items()} for k, v in (("original", a), ("rebuilt", b))}
    if a["error"] or a["rc"] is None:
        rec.update(status="BLOCKED", reason="original run failed: %s" % a["error"])
        return rec
    if b["error"] or b["rc"] is None:
        rec.update(status="BLOCKED", reason="rebuilt run failed: %s" % b["error"])
        return rec
    infra = BLOCK_PATTERNS.search((b["stderr"] + b["stdout"]).decode("utf-8", errors="replace"))
    if infra:
        rec.update(status="BLOCKED", reason="rebuilt runner unavailable: %s" % infra.group(0))
        return rec
    # A Wine/DLL startup failure is not a guest result; never treat it as FAIL/PASS.
    if a["rc"] == 53 and b"failed to open" in (a["stdout"] + a["stderr"]):
        rec.update(status="BLOCKED", reason="original could not be opened by the runner (exit 53)")
        return rec
    ga = normalize_guest((a["stdout"] + a["stderr"]).decode("utf-8", errors="replace"))
    gb = normalize_guest((b["stdout"] + b["stderr"]).decode("utf-8", errors="replace"))
    rec["guest_stdout"] = {"original_sha256": sha256(ga.encode()),
                           "rebuilt_sha256": sha256(gb.encode())}
    checks = {"exit_code": a["rc"] == b["rc"], "stdout_exact": ga == gb}
    diffs = {}
    if not checks["stdout_exact"]:
        diffs["stdout"] = first_difference(ga, gb)
    if args.frames:
        ha, hb = frame_hashes(Path(args.frames[0])), frame_hashes(Path(args.frames[1]))
        checks["frames_exact"] = bool(ha) and ha == hb
        if not checks["frames_exact"]:
            diffs["frames"] = {"original": ha, "rebuilt": hb}
    if args.trace:
        tr = compare_traces(Path(args.trace[0]), Path(args.trace[1]), args.tolerance)
        rec["trace"] = tr
        checks["trace"] = tr["status"] == "PASS"
    if args.functions:
        for f in args.functions:
            d = json.load(open(f))
            bad = [r for r in d.get("results", []) if r.get("status") not in ("agree-return", "PASS", "ok")]
            checks["functions:" + Path(f).name] = not bad
            if bad:
                diffs["functions"] = bad[:5]
    rec["checks"] = checks
    rec["differences"] = diffs
    failed = [k for k, v in checks.items() if not v]
    rec["status"] = "BLOCKED" if not checks else ("PASS" if not failed else "FAIL")
    rec["failed_checks"] = failed
    rec["limits"] = ("Process-level differential only. No gameplay, audio or frame-timing "
                     "oracle is implied unless explicitly supplied and compared.")
    return rec


def form_key(insn) -> tuple:
    """Stable instruction form: mnemonic + operand kinds + leading opcode byte."""
    return (insn.mnemonic, tuple(op.type for op in insn.operands), insn.bytes[:1].hex())


def collect_forms(pe_path: Path, project: Path, per_form: int, max_forms: int) -> list:
    """Pick representative lifted functions covering distinct instruction forms.

    igi-recomp isolates every instruction form and drives it from random states;
    GameReComp's generic path can only drive whole lifted functions, so this
    selects one function per form and lets difftest.py compare those functions
    against the original bytes. That is a coverage proxy, not isolated-form
    equivalence, and is labelled as such in the evidence.
    """
    import pefile
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32

    report = json.loads((project / "reports" / "functions.json").read_text())
    pe = pefile.PE(str(pe_path), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    data = pe.__data__
    forms = {}
    for fn in report.get("functions", []):
        va = int(fn["address"], 0)
        rva = va - base
        sec = next((s for s in pe.sections
                    if s.VirtualAddress <= rva < s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData)), None)
        if sec is None:
            continue
        off = sec.PointerToRawData + (rva - sec.VirtualAddress)
        chunk = data[off:off + min(int(fn.get("size", 128)), 256)]
        for insn in md.disasm(chunk, va):
            key = form_key(insn)
            bucket = forms.setdefault(key, [])
            if len(bucket) < per_form:
                bucket.append(va)
            if len(forms) >= max_forms and all(len(v) >= per_form for v in forms.values()):
                break
        if len(forms) >= max_forms and all(len(v) >= per_form for v in forms.values()):
            break
    return [{"form": " ".join([k[0]] + ["op%d" % t for t in k[1]] + [k[2]]),
             "addresses": ["0x%08X" % v for v in vals]} for k, vals in forms.items()][:max_forms]


def forms_diff(args) -> dict:
    import tempfile
    rec = {"schema": SCHEMA, "kind": "forms", "pe": str(args.pe), "project": str(args.project),
           "scope": "representative lifted functions per instruction form (coverage proxy)"}
    pe, project = Path(args.pe), Path(args.project)
    if not pe.is_file() or not (project / "reports" / "functions.json").is_file():
        rec.update(status="BLOCKED", reason="original PE or project function inventory not found")
        return rec
    forms = collect_forms(pe, project, args.per_form, args.max_forms)
    rec["forms"] = forms
    addrs = sorted({a for f in forms for a in f["addresses"]})
    if not addrs:
        rec.update(status="BLOCKED", reason="no instruction forms mapped to lifted functions")
        return rec
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "difftest.json"
        cmd = [sys.executable, "tools/lift/difftest.py", "--project", str(project),
               "--pe", str(pe)]
        for a in addrs:
            cmd += ["--address", a]
        cmd += ["--cases", str(args.cases), "--flags", "--json", str(out)]
        rec["command"] = cmd
        r = subprocess.run(cmd, cwd=args.repo, capture_output=True, timeout=args.timeout)
        rec["difftest_rc"] = r.returncode
        # difftest exits 1 when a compared function disagrees: that is a FAIL,
        # not a BLOCKED. Only a missing/unparseable report is missing evidence.
        if not out.is_file():
            rec.update(status="BLOCKED",
                       reason="difftest produced no report: " + (r.stderr or r.stdout).decode("utf-8", errors="replace")[-600:])
            return rec
        try:
            report = json.loads(out.read_text())
        except (ValueError, OSError) as e:
            rec.update(status="BLOCKED", reason="difftest report unreadable: %s" % e)
            return rec
    results = report.get("addresses", []) or report.get("results", [])
    ok = {"agree", "agree-return", "PASS", "ok", "agree-flags"}
    oracle_fault = {"original-faulted", "unmapped", "no-original", "error", "oracle-faulted"}
    def verdict(x):
        return x.get("verdict", x.get("status", ""))
    bad = [x for x in results if verdict(x) not in ok | oracle_fault]
    faults = [x for x in results if verdict(x) in oracle_fault]
    rec["cases_total"] = len(results)
    rec["cases_bad"] = bad[:10]
    rec["oracle_faults"] = faults[:10]
    if not results:
        rec["status"] = "BLOCKED"
    elif bad:
        rec["status"] = "FAIL"
    elif faults:
        rec["status"] = "BLOCKED"
        rec["reason"] = "%d/%d addresses faulted in the original oracle (not counted as PASS)" % (len(faults), len(results))
    else:
        rec["status"] = "PASS"
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("process", help="run original vs rebuilt with identical input")
    p.add_argument("--original", required=True)
    p.add_argument("--rebuilt", required=True)
    p.add_argument("--original-cmd", default="highball run {exe}",
                   help="command template; {exe} is the original (Highball primary)")
    p.add_argument("--rebuilt-cmd", default=None, help="command template for the rebuilt target")
    p.add_argument("--args", default="", help="extra guest arguments (appended to both commands)")
    p.add_argument("--stdin", default="")
    p.add_argument("--stdin-file")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--frames", nargs=2, metavar=("ORIG_DIR", "REBUILT_DIR"))
    p.add_argument("--trace", nargs=2, metavar=("ORIG_TXT", "REBUILT_TXT"))
    p.add_argument("--tolerance", type=float, default=0.001)
    p.add_argument("--functions", action="append", default=[], help="difftest.py --json report")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--stdout", action="store_true")
    t = sub.add_parser("trace", help="compare two watched-value traces")
    t.add_argument("a", type=Path)
    t.add_argument("b", type=Path)
    t.add_argument("--tolerance", type=float, default=0.001)
    t.add_argument("--out", type=Path)
    f = sub.add_parser("forms", help="instruction-form coverage sweep via difftest.py")
    f.add_argument("--pe", required=True, type=Path)
    f.add_argument("--project", required=True, type=Path)
    f.add_argument("--repo", default="/Users/haseeb-mir/Documents/Code/GameReComp",
                   help="checkout containing tools/lift/difftest.py")
    f.add_argument("--per-form", type=int, default=1)
    f.add_argument("--max-forms", type=int, default=20)
    f.add_argument("--cases", type=int, default=8)
    f.add_argument("--timeout", type=float, default=600)
    f.add_argument("--out", required=True, type=Path)
    f.add_argument("--stdout", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "trace":
        rec = {"schema": SCHEMA, "kind": "trace", **compare_traces(a.a, a.b, a.tolerance)}
    elif a.cmd == "forms":
        rec = forms_diff(a)
    else:
        if a.args:
            a.original_cmd += " " + a.args
            a.rebuilt_cmd = (a.rebuilt_cmd or "{exe}") + " " + a.args
        rec = process_diff(a)
    text = json.dumps(rec, indent=2) + "\n"
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text)
    if getattr(a, "stdout", False) or a.cmd == "trace":
        print(text)
    return 0 if rec.get("status") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
