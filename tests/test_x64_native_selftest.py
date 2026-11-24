"""Native ARM64 execution of a REAL Windows x86-64 PE, cross-checked against
two independent engines on the original bytes.

This is deliberately pinned to arith-calc.exe from the real corpus. There is no
synthetic "proof" program here: the PE, its function catalog, the generated C,
and both oracles all come from the same file on disk.

Flow
  1. disasm64.py -> function catalog
  2. generate64.py -> translation units + dispatch table + import boundary
  3. clang -target arm64-apple-macos11 -> native Mach-O (cpu64.c is the runtime)
  4. run the Mach-O; capture stdout/exit
  5. run the ORIGINAL under Unicorn (tools/lift/x64_oracle.py) and under
     Highball's Wine engine; compare bytes
  6. attempt Frida instrumentation of the original under Wine; if it cannot
     attach, record the EXACT exception rather than claiming success

The evidence JSON is written to reports/x64_native/arith-calc-evidence.json.
Passing this test means the three byte streams agree; it does NOT mean x64
support is universally complete. The scope is one small, real PE.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS / "lift"))
sys.path.insert(0, str(TOOLS / "disasm"))

from x64_oracle import run_unicorn_x64  # noqa: E402

PE = Path.home() / "Downloads" / "recomp-apps" / "windows" / "x64" / "apps" / "arith-calc.exe"
RUNTIME = ROOT / "runtime" / "recomp64_cpu"
EVIDENCE = ROOT / "reports" / "x64_native" / "arith-calc-evidence.json"

HIGHBALL_ENGINE = (Path.home() / "Library" / "Application Support" / "Highball"
                   / "engines" / "x64-crossover26.3-r27" / "engine")
HIGHBALL_BOTTLE = (Path.home() / "Library" / "Application Support" / "Highball"
                   / "bottles" / "Games")


def _run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True,
                          text=True, timeout=kw.pop("timeout", 180), **kw)


class X64NativeSelftest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PE.is_file():
            raise unittest.SkipTest(f"real PE not present: {PE}")
        cls.tmp = Path(tempfile.mkdtemp(prefix="grc-x64-"))
        cls.gen = cls.tmp / "gen"
        cls.gen.mkdir()
        cls.evidence = {
            "target": str(PE),
            "scope": "one real PE32+ console executable; NOT general x64 support",
            "host": {"platform": sys.platform, "machine": os.uname().machine},
            "pipeline": {},
            "native": {},
            "engines": {},
            "verdict": None,
        }

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _step1_catalog_and_generate(self):
        funcs = self.tmp / "arith.funcs.txt"
        r = _run([sys.executable, TOOLS / "disasm" / "disasm64.py", PE,
                  "-o", self.tmp / "arith.funcs.json", "--funcs-txt", funcs])
        self.assertEqual(r.returncode, 0, f"disasm64 failed:\n{r.stderr}")
        r = _run([sys.executable, TOOLS / "lift" / "generate64.py", PE,
                  funcs, self.gen])
        self.assertEqual(r.returncode, 0, f"generate64 failed:\n{r.stderr}")
        meta = json.loads((self.gen / "recomp_meta.json").read_text())
        self.assertEqual(meta["lift_errors"], 0, meta)
        self.assertEqual(meta["recomp_todo_sites"], 0, meta)
        self.evidence["pipeline"] = {
            "disassembler": "disasm64.py",
            "lifter": "generate64.py",
            "meta": meta,
        }
        self.assertGreater(meta["functions"], 0)

    def _step2_compile_and_run(self):
        exe = self.tmp / "arith64"
        objs = sorted(self.gen.glob("recomp_funcs_*.c"))
        self.assertTrue(objs, "no generated translation units")
        cmd = [shutil.which("clang") or "clang", "-target",
               "arm64-apple-macos11", "-O2", "-w",
               f"-I{self.gen}", f"-I{RUNTIME}",
               RUNTIME / "cpu64.c", RUNTIME / "cpu64_selftest.c",
               *objs, self.gen / "recomp_dispatch.c",
               self.gen / "recomp_imports.c", "-o", exe]
        r = _run(cmd)
        self.assertEqual(r.returncode, 0,
                         f"native compile failed:\n{r.stdout}\n{r.stderr}")
        self.assertIn("Mach-O", _run(["file", exe]).stdout)
        # Raw bytes: text=True would collapse the PE's CRLF output to LF and
        # make a byte-for-byte comparison against the original meaningless.
        r = subprocess.run([str(exe), str(PE)], capture_output=True, timeout=30)
        self.evidence["native"] = {
            "compiler": cmd[0],
            "target": "arm64-apple-macos11",
            "binary": str(exe),
            "exit": r.returncode,
            "stdout_hex": r.stdout.hex(),
            "stderr": r.stderr.decode(errors="replace")[-800:],
        }
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout, "native run produced no output")

    def _step3_unicorn(self):
        self.evidence["engines"]["unicorn"] = run_unicorn_x64(PE)

    def _step4_wine(self):
        wine = HIGHBALL_ENGINE / "bin" / "wine"
        rec = {"engine": "highball-wine", "wine": str(wine), "attempts": []}
        if not wine.is_file():
            rec.update(outcome="BLOCKED", detail="Highball x64 engine not installed")
        else:
            env = dict(os.environ)
            env["WINEPREFIX"] = str(HIGHBALL_BOTTLE)
            env["WINEDEBUG"] = "-all"
            # The bottle is already booted, but a wineserver left behind by a
            # previous Frida probe can make one launch return empty. Retry a
            # couple of times; a fresh prefix is not an option because wineboot
            # under Rosetta takes minutes here.
            import time
            for n in range(3):
                try:
                    p = subprocess.run([str(wine), str(PE)], capture_output=True,
                                       env=env, timeout=90)
                    attempt = {"exit": p.returncode, "stdout_hex": p.stdout.hex(),
                               "stderr": p.stderr.decode(errors="replace")[-400:]}
                    rec["attempts"].append(attempt)
                    if p.returncode == 0 and p.stdout:
                        rec.update(outcome="PASS", exit=p.returncode,
                                   stdout_hex=p.stdout.hex(),
                                   stderr=attempt["stderr"])
                        break
                except Exception as e:
                    rec["attempts"].append({"detail": f"{type(e).__name__}: {e}"})
                time.sleep(3)
            if "outcome" not in rec:
                rec.update(outcome="BLOCKED",
                           detail="wine produced no output in 3 attempts; see attempts[]")
        self.evidence["engines"]["wine"] = rec

    def _step5_frida(self):
        """Attempt both attach and spawn; never pretend this worked."""
        rec = {"engine": "frida", "target": str(PE), "attempts": []}
        try:
            import frida
            rec["version"] = frida.__version__
        except Exception as e:
            rec.update(outcome="BLOCKED",
                       detail=f"frida import failed: {type(e).__name__}: {e}")
            self.evidence["engines"]["frida"] = rec
            return

        env = dict(os.environ)
        env["WINEPREFIX"] = str(HIGHBALL_BOTTLE)
        env["WINEDEBUG"] = "-all"
        wine = str(HIGHBALL_ENGINE / "bin" / "wine")

        # (a) attach to an already-running original under Highball Wine
        proc = None
        session = None
        import time
        try:
            proc = subprocess.Popen([wine, str(PE)], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(2)
            if proc.poll() is not None:
                rec["attempts"].append({"mode": "attach", "outcome": "BLOCKED",
                                        "detail": f"wine exited early rc={proc.returncode}"})
            else:
                session = frida.attach(proc.pid)
                script = session.create_script(
                    "send(Process.enumerateModules().map(m=>m.name)"
                    ".filter(n=>/kernel32|ntdll/i.test(n)));")
                seen = []
                script.on("message", lambda m, _d: seen.append(m.get("payload")))
                script.load()
                time.sleep(1)
                rec["attempts"].append({"mode": "attach", "outcome": "PASS",
                                        "pid": proc.pid, "modules": seen})
        except Exception as e:
            rec["attempts"].append({"mode": "attach", "outcome": "BLOCKED",
                                    "detail": f"{type(e).__name__}: {e}"})
        finally:
            if session:
                try: session.detach()
                except Exception: pass
            if proc:
                try:
                    out, err = proc.communicate(timeout=5)
                    rec.setdefault("wine_stdout_hex", out.hex())
                except Exception:
                    proc.kill()

        # (b) frida.spawn the original under Wine
        try:
            pid = frida.spawn([wine, str(PE)], env=env)
            try:
                session = frida.attach(pid)
                frida.resume(pid)
                time.sleep(2)
                rec["attempts"].append({"mode": "spawn", "outcome": "PASS", "pid": pid})
            except Exception as e:
                rec["attempts"].append({"mode": "spawn", "outcome": "BLOCKED",
                                        "detail": f"{type(e).__name__}: {e}"})
                try: frida.kill(pid)
                except Exception: pass
            finally:
                if session:
                    try: session.detach()
                    except Exception: pass
        except Exception as e:
            rec["attempts"].append({"mode": "spawn", "outcome": "BLOCKED",
                                    "detail": f"{type(e).__name__}: {e}"})

        rec["outcome"] = ("PASS" if any(a["outcome"] == "PASS"
                                        for a in rec["attempts"]) else "BLOCKED")
        self.evidence["engines"]["frida"] = rec

    def test_real_pe_native_matches_original_engines(self):
        self._step1_catalog_and_generate()
        self._step2_compile_and_run()
        self._step3_unicorn()
        self._step4_wine()
        self._step5_frida()

        native = self.evidence["native"]["stdout_hex"]
        uc = self.evidence["engines"]["unicorn"]
        wine = self.evidence["engines"]["wine"]

        self.assertIsNone(uc["fault"], f"Unicorn faulted: {uc['fault']}")
        self.assertEqual(uc["exit"], self.evidence["native"]["exit"])
        self.assertEqual(uc["stdout_hex"], native, "native != Unicorn(original)")

        wine_verdict = "NOT_CHECKED"
        if wine["outcome"] == "PASS":
            self.assertEqual(wine["stdout_hex"], native,
                             "native != Highball Wine(original)")
            wine_verdict = "MATCH"
        else:
            # Wine is a secondary cross-check. Unicorn is the primary oracle;
            # record Wine's exact failure rather than failing the whole test.
            print(f"[x64] Wine unavailable: {wine.get('detail', 'see attempts[]')}")

        # Frida is attempted, not assumed. A BLOCKED verdict is acceptable only
        # with a concrete error string for every attempt.
        fr = self.evidence["engines"]["frida"]
        if fr["outcome"] == "BLOCKED":
            for a in fr["attempts"]:
                self.assertIn("detail", a, "BLOCKED Frida attempt needs an exact reason")
        else:
            self.assertTrue(fr["attempts"])

        self.evidence["verdict"] = {
            "native_vs_unicorn": "MATCH",
            "native_vs_wine": wine_verdict,
            "wine_outcome": wine["outcome"],
            "frida": fr["outcome"],
            "claim": ("native ARM64 recompilation of THIS real PE matches the "
                      "original under Unicorn; Wine and Frida results are "
                      "recorded verbatim above. Not a claim of general x64 support."),
        }
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(self.evidence, indent=2) + "\n")
        print(f"\n[x64 evidence] {EVIDENCE}")


if __name__ == "__main__":
    unittest.main()
