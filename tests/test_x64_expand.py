"""Expand the real-PE x86-64 -> native ARM64 proof beyond arith-calc.exe.

Each case below is a REAL PE32+ from ~/Downloads/recomp-apps/windows/x64.
The pipeline is always the same, with no synthetic binary and no baked-in
expected output:

  disasm64.py -> generate64.py -> clang -target arm64-apple-macos11
  -> native run -> the ORIGINAL bytes under Unicorn (x64_oracle_ucrt.py)
  -> the ORIGINAL under Highball Wine

MATCH means the three byte streams agree. A binary that cannot be recompiled
is recorded with its EXACT error and verdict BLOCKED; it is never reported as
a pass. Evidence is written to reports/x64_native/<tag>-evidence.json and a
summary to reports/x64_native/expand-summary.json.

Run:  python3 -m unittest tests.test_x64_expand -v
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

from x64_oracle_ucrt import run_unicorn_x64  # noqa: E402

CORPUS = Path.home() / "Downloads" / "recomp-apps" / "windows" / "x64"
RUNTIME = ROOT / "runtime" / "recomp64_cpu"
EVIDENCE_DIR = ROOT / "reports" / "x64_native"
CLANG = shutil.which("clang") or "/opt/homebrew/opt/llvm/bin/clang"

HIGHBALL_ENGINE = (Path.home() / "Library" / "Application Support" / "Highball"
                   / "engines" / "x64-crossover26.3-r27" / "engine")
HIGHBALL_BOTTLE = (Path.home() / "Library" / "Application Support" / "Highball"
                   / "bottles" / "Games")

# (tag, PE path relative to the corpus, stdin bytes, verdict expected)
CASES = [
    ("base64", "apps/base64.exe", b"3\n", "MATCH"),
    ("tictactoe", "games/tictactoe.exe", b"1\n2\n3\n4\n5\n6\n7\n8\n9\n", "MATCH"),
    ("tetris", "games/tetris.exe", b"q\n", "MATCH"),
    ("flood", "games/flood.exe", b"", "BLOCKED"),
    ("busybox", "apps/busybox.exe", b"", "BLOCKED"),
]

# Exact, recorded blockers. These are not guessed or paraphrased: they are the
# errors recomp64_cpu/cpu64.c emits when a running PE reaches an import the
# bounded shim layer does not implement.
BLOCKERS = {
    "flood": "call-import unmodelled: KERNEL32.dll!GetSystemTimeAsFileTime",
    "busybox": "jmp-import unmodelled: msvcrt.dll!__set_app_type",
}


def _run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True,
                          text=True, timeout=kw.pop("timeout", 300), **kw)


class X64Expand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.missing = [rel for _t, rel, _s, _v in CASES
                       if not (CORPUS / rel).is_file()]
        if len(cls.missing) == len(CASES):
            raise unittest.SkipTest(f"no real PE32+ corpus at {CORPUS}")
        cls.tmp = Path(tempfile.mkdtemp(prefix="grc-x64-expand-"))
        cls.results = {}

    @classmethod
    def tearDownClass(cls):
        if not cls.missing:
            EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
            (EVIDENCE_DIR / "expand-summary.json").write_text(
                json.dumps(cls.results, indent=2) + "\n")
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _pipeline(self, tag, rel, stdin):
        pe = CORPUS / rel
        work = self.tmp / tag
        work.mkdir()
        gen = work / "gen"
        gen.mkdir()
        rec = {
            "target": str(pe),
            "stdin_hex": stdin.hex(),
            "scope": ("real PE32+ recompiled to native ARM64; BLOCKED means the "
                      "bounded import shim cannot model this binary"),
            "pipeline": {},
            "native": {},
            "engines": {},
            "verdict": None,
        }

        # 1. disasm64 -> function catalog
        funcs = work / f"{tag}.funcs.txt"
        r = _run([sys.executable, TOOLS / "disasm" / "disasm64.py", pe,
                  "-o", work / f"{tag}.funcs.json", "--funcs-txt", funcs])
        self.assertEqual(r.returncode, 0, f"disasm64({tag}):\n{r.stderr}")
        # 2. generate64 -> translation units
        r = _run([sys.executable, TOOLS / "lift" / "generate64.py", pe,
                  funcs, gen])
        self.assertEqual(r.returncode, 0, f"generate64({tag}):\n{r.stderr}")
        meta = json.loads((gen / "recomp_meta.json").read_text())
        self.assertEqual(meta["lift_errors"], 0, meta)
        rec["pipeline"] = {"disassembler": "disasm64.py",
                           "lifter": "generate64.py", "meta": meta}
        self.assertGreater(meta["functions"], 0)

        # 3. clang arm64 -> native Mach-O, 4. run with the same stdin
        objs = sorted(gen.glob("recomp_funcs_*.c"))
        self.assertTrue(objs, f"{tag}: no generated translation units")
        cmd = [CLANG, "-target", "arm64-apple-macos11", "-O2", "-w",
               f"-I{gen}", f"-I{RUNTIME}",
               RUNTIME / "cpu64.c", RUNTIME / "win64_shims.c",
               RUNTIME / "win64_runner.c", *objs,
               gen / "recomp_dispatch.c", gen / "recomp_imports.c",
               "-o", work / "native"]
        r = _run(cmd)
        self.assertEqual(r.returncode, 0,
                         f"native compile({tag}):\n{r.stdout}\n{r.stderr}")
        self.assertIn("Mach-O", _run(["file", work / "native"]).stdout)
        p = subprocess.run([str(work / "native"), str(pe)], input=stdin,
                           capture_output=True, timeout=90)
        rec["native"] = {
            "compiler": CLANG,
            "target": "arm64-apple-macos11",
            "binary": str(work / "native"),
            "exit": p.returncode,
            "stdout_hex": p.stdout.hex(),
            "stderr": p.stderr.decode(errors="replace")[-1500:],
        }
        return rec

    def _wine(self, pe, stdin):
        wine = HIGHBALL_ENGINE / "bin" / "wine"
        rec = {"engine": "highball-wine", "wine": str(wine), "attempts": []}
        if not wine.is_file():
            rec.update(outcome="BLOCKED", detail="Highball x64 engine not installed")
            return rec
        env = dict(os.environ)
        env.update(WINEPREFIX=str(HIGHBALL_BOTTLE), WINEDEBUG="-all")
        for _ in range(3):
            try:
                p = subprocess.run([str(wine), str(pe)], input=stdin,
                                   capture_output=True, env=env, timeout=90)
                attempt = {"exit": p.returncode, "stdout_hex": p.stdout.hex(),
                           "stderr": p.stderr.decode(errors="replace")[-400:]}
                rec["attempts"].append(attempt)
                if p.returncode == 0 and p.stdout:
                    rec.update(outcome="PASS", exit=p.returncode,
                               stdout_hex=p.stdout.hex(), stderr=attempt["stderr"])
                    return rec
            except Exception as e:
                rec["attempts"].append({"detail": f"{type(e).__name__}: {e}"})
        rec.update(outcome="BLOCKED",
                   detail="wine produced no output in 3 attempts; see attempts[]")
        return rec

    def test_real_pe32_plus_binaries(self):
        for tag, rel, stdin, expected in CASES:
            if rel in self.missing:
                print(f"[x64-expand] SKIP {tag}: missing {CORPUS / rel}")
                continue
            with self.subTest(binary=tag):
                rec = self._pipeline(tag, rel, stdin)
                native = rec["native"]

                if expected == "BLOCKED":
                    # Fail-closed: the failure must be the exact expected one.
                    self.assertNotEqual(native["exit"], 0, native)
                    self.assertIn(BLOCKERS[tag], native["stderr"], native)
                    rec["engines"]["unicorn"] = {
                        "outcome": "NOT_RUN",
                        "detail": "native recompilation blocked before oracle comparison",
                    }
                    rec["verdict"] = {
                        "native": "BLOCKED",
                        "blocker": BLOCKERS[tag],
                        "claim": ("disasm64 + generate64 + clang succeeded, but "
                                  "native run stopped at the recorded unmodelled "
                                  "import; no PASS is claimed."),
                    }
                else:
                    rec["engines"]["unicorn"] = run_unicorn_x64(str(CORPUS / rel),
                                                                 stdin=stdin)
                    uc = rec["engines"]["unicorn"]
                    self.assertIsNone(uc["fault"], f"{tag}: Unicorn fault: {uc['fault']}")
                    self.assertEqual(native["exit"], uc["exit"], tag)
                    self.assertEqual(native["stdout_hex"], uc["stdout_hex"],
                                     f"{tag}: native != Unicorn(original)")
                    rec["engines"]["wine"] = self._wine(CORPUS / rel, stdin)
                    wine = rec["engines"]["wine"]
                    self.assertEqual(wine["outcome"], "PASS", f"{tag}: {wine}")
                    self.assertEqual(wine["stdout_hex"], native["stdout_hex"],
                                     f"{tag}: native != Highball Wine(original)")
                    rec["verdict"] = {
                        "native": "PASS",
                        "native_vs_unicorn": "MATCH",
                        "native_vs_wine": "MATCH",
                        "claim": ("native ARM64 recompilation of THIS real PE "
                                  "matches the original bytes under both "
                                  "Unicorn and Highball Wine."),
                    }

                EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
                (EVIDENCE_DIR / f"{tag}-evidence.json").write_text(
                    json.dumps(rec, indent=2) + "\n")
                self.results[tag] = {
                    "target": rec["target"],
                    "functions": rec["pipeline"]["meta"]["functions"],
                    "imports": rec["pipeline"]["meta"]["imports"],
                    "native_exit": native["exit"],
                    "native_stdout_bytes": len(native["stdout_hex"]) // 2,
                    "verdict": rec["verdict"],
                }
                print(f"[x64-expand] {tag}: {rec['verdict']['native']} "
                      f"({len(native['stdout_hex']) // 2} stdout bytes)")


if __name__ == "__main__":
    unittest.main()
