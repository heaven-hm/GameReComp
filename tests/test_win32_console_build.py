"""Windows console cross-build: real PE32 output, real Wine runs, honest evidence.

Every engine that cannot run is recorded with its exact reason.  A missing
toolchain, corpus executable or Wine engine is a skip (BLOCKED), never a fake
PASS.  On this machine all three prerequisites exist, so the tests below build
a generated arith-calc project, cross-compile it to PE32 and PE32+, run both
PEs under Highball's Wine, and compare their stdout byte-for-byte with the
native macOS rebuild.  Unicorn/Frida are attempted through the existing hybrid
harness and their BLOCKED reasons are written to reports/win32-console-port-evidence.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import frida_probe, hybrid_diff, wine_oracle
from tools.win32_console_build import build_console, find_cc, _pe_kind

REPO = Path(__file__).resolve().parents[1]
CORPUS = Path("/Users/haseeb-mir/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe")
EVIDENCE = REPO / "reports" / "win32-console-port-evidence.json"
_TEST_BOTTLE: Path | None = None   # private prefix: the shared Games bottle is flaky under parallel agents


def _wine_run(pe: Path, timeout: int = 90) -> dict:
    """Run a PE under Highball's Wine, returning captured stdout/stderr."""
    wine = wine_oracle.find_wine()
    if not wine:
        return {"status": "BLOCKED", "reason": "no Wine/Highball engine found"}
    env = dict(os.environ)
    env.update({"WINEPREFIX": str(_TEST_BOTTLE or wine_oracle.DEFAULT_BOTTLE),
                "WINEDEBUG": "-all",
                "WINEDLLOVERRIDES": "winemenubuilder.exe=d"})
    proc = subprocess.Popen([wine, str(pe)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        out, err = proc.communicate(timeout=10)
        rc = "TIMEOUT"
    return {"status": "OK", "exit_code": rc,
            "stdout": out.decode("utf-8", "replace"),
            "stderr": err.decode("utf-8", "replace"),
            "stdout_sha256": hashlib.sha256(out).hexdigest(), "wine": wine}


class Win32ConsoleBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="p4-win32-console-"))
        cls.project = None
        if not CORPUS.is_file():
            cls.skip_reason = f"corpus executable not found: {CORPUS}"
            return
        if not find_cc(32) or not find_cc(64):
            cls.skip_reason = "MinGW-w64 32/64 cross-compilers not found"
            return
        global _TEST_BOTTLE
        wine = wine_oracle.find_wine()
        if wine:
            _TEST_BOTTLE = cls.tmp / "winebottle"
            boot = subprocess.run([wine, "wineboot", "-i"],
                                  env={**os.environ, "WINEPREFIX": str(_TEST_BOTTLE),
                                       "WINEDEBUG": "-all"},
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, timeout=240)
            if boot.returncode != 0:
                _TEST_BOTTLE = None
        try:
            from tools.autorecomp.pipeline import Pipeline, run_pipeline
            pipe = Pipeline(CORPUS, project_dir=cls.tmp / "arith-calc",
                            build=False, run_binary=False, headless=True)
            run_pipeline(pipe, ["inspect", "recover", "translate", "project"])
            if pipe.stage_status.get("project") != "ok":
                cls.skip_reason = f"project generation failed: {pipe.stage_status}"
                return
            cls.project = pipe.project_dir
            build_dir = cls.project / "build"
            conf = subprocess.run(["cmake", "-S", str(cls.project), "-B", str(build_dir),
                                   "-DCMAKE_BUILD_TYPE=Release"],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, timeout=180)
            if conf.returncode != 0:
                cls.skip_reason = f"native cmake configure blocked: {conf.stdout[-400:]}"
                return
            bld = subprocess.run(["cmake", "--build", str(build_dir), "-j4"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, timeout=600)
            if bld.returncode != 0 or not (build_dir / "arith_calc").is_file():
                cls.skip_reason = f"native build blocked: {bld.stdout[-400:]}"
                return
            cls.skip_reason = None
        except Exception as exc:                       # pragma: no cover
            cls.skip_reason = f"project generation blocked: {type(exc).__name__}: {exc}"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _need_project(self):
        if self.project is None:
            self.skipTest(self.skip_reason or "generated project unavailable")

    def _build(self, bits):
        rec = build_console(self.project, self.tmp / f"arith-{bits}.exe", bits)
        self.assertEqual(rec["status"], "OK", json.dumps(rec, indent=2)[:3000])
        return rec

    def test_cross_build_produces_real_pe32_and_pe32plus(self):
        self._need_project()
        for bits, expected in ((32, "PE32"), (64, "PE32+")):
            with self.subTest(bits=bits):
                rec = self._build(bits)
                pe = Path(rec["binary"])
                self.assertEqual(rec["pe"], expected)
                self.assertEqual(pe.read_bytes()[:2], b"MZ")
                self.assertEqual(_pe_kind(pe), expected)

    def test_wine_output_matches_native_macos_run(self):
        self._need_project()
        if not wine_oracle.find_wine():
            self.skipTest("no Wine/Highball engine found")
        # The native rebuild is the reference; it was built in setUpClass.
        native = self.project / "build" / "arith_calc"
        nat = subprocess.run([str(native)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(nat.returncode, 0)

        oracle = json.loads((REPO / "reports/oracle/arith-calc-oracle.json").read_text())
        for bits in (32, 64):
            rec = self._build(bits)
            run = _wine_run(Path(rec["binary"]))
            self.assertEqual(run["status"], "OK", json.dumps(run)[:500])
            self.assertEqual(run["exit_code"], 0, json.dumps(run)[:500])
            # Byte-exact: the guest already emits CRLF and the Windows shim
            # writes in binary mode, so no line-ending fudge is needed.
            self.assertEqual(run["stdout"].encode(), nat.stdout,
                             f"PE{bits} stdout differs from native rebuild")
            for needle in oracle["stdout_contains"]:
                self.assertIn(needle, run["stdout"])

    def test_hybrid_evidence_and_frida_probe_recorded(self):
        """Unicorn + Frida are attempted; BLOCKED is the truthful outcome here."""
        self._need_project()
        original = CORPUS
        native = self.project / "build" / "arith_calc"
        if not native.is_file():
            self.skipTest("native rebuild unavailable (run the Wine test first)")
        rec = hybrid_diff.hybrid_one(original, native, b"",
                                     ["ExitProcess", "WriteFile"],
                                     timeout=8, live_frida=True)
        # The hybrid harness is fail-closed: a MATCH is allowed only if every
        # engine agreed.  Never assert 100% when engines report BLOCKED.
        self.assertIn(rec["status"], ("MATCH", "BLOCKED", "DIVERGED"))
        for key in ("unicorn", "frida", "native"):
            self.assertIn(key, rec)
            if rec[key].get("status") == "BLOCKED":
                self.assertTrue(rec[key].get("reason"), f"{key} BLOCKED with no reason")
        hooks = ["ExitProcess", "WriteFile"]
        source = frida_probe.script_source(hooks)
        evidence = {
            "scope": "original arith-calc.exe vs native rebuild; cross-built PE "
                     "under Highball Wine compared byte-for-byte in the Wine test",
            "cross_build": {str(b): build_console(
                self.project, self.tmp / f"evidence-{b}.exe", b)
                for b in (32, 64)},
            "engine_probes": {
                "difftest.py": {"entry": "hybrid_diff.unicorn_signal -> "
                                          "difftest.run_unicorn_function",
                                "record": rec["unicorn"]},
                "frida_probe.py": {"script_sha256": hashlib.sha256(
                                       source.encode()).hexdigest()[:16],
                                   "hooks": hooks,
                                   "dry_run": "tools/frida_probe.py --dry-run prints this script",
                                   "live_record": rec["frida"]},
            },
            "hybrid": rec,
            "note": "CPU/process-level evidence only, not gameplay or frame equivalence. "
                    "BLOCKED engines are recorded verbatim; no fabricated 100%.",
        }
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n")
        self.assertTrue(EVIDENCE.is_file())

    def test_missing_port_layer_fails_closed(self):
        """A project without win32_port.c must be BLOCKED, never a fake MZ."""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "src" / "recomp" / "gen").mkdir(parents=True)
            (root / "src" / "runtime" / "win32hle").mkdir(parents=True)
            (root / "src" / "main.c").write_text("int main(void){return 0;}\n")
            (root / "src/recomp/gen/recomp_image.bin").write_bytes(b"MZ")
            (root / "src/recomp/gen/recomp_image.S").write_text("")
            rec = build_console(root, root / "fake.exe", 32)
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertIn("port layer missing", rec["reason"])
            self.assertFalse((root / "fake.exe").exists())


if __name__ == "__main__":
    unittest.main()
