"""Differential engine must be non-vacuous and fail closed."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.differential import compare_traces, form_key, normalize_guest


class FormTests(unittest.TestCase):
    def test_form_key_is_stable(self):
        from capstone import Cs, CS_ARCH_X86, CS_MODE_32
        md = Cs(CS_ARCH_X86, CS_MODE_32)
        md.detail = True
        insn = next(md.disasm(b"\x55\x8b\xec", 0x401000))
        self.assertEqual(form_key(insn)[0], "push")
        self.assertEqual(form_key(insn), form_key(insn))

    def test_forms_without_inventory_are_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            pe, proj, out = Path(t) / "a.exe", Path(t) / "proj", Path(t) / "out.json"
            pe.write_bytes(b"MZ")
            proj.mkdir()
            cmd = [sys.executable, "-m", "tools.differential", "forms", "--pe", str(pe),
                   "--project", str(proj), "--out", str(out)]
            p = subprocess.run(cmd, capture_output=True, cwd=Path(__file__).resolve().parents[1])
            rec = json.loads(out.read_text())
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertEqual(p.returncode, 1)


class DifferentialTests(unittest.TestCase):
    def test_normalize_strips_launcher_only(self):
        raw = "msync: up\nLaunching x.exe in bottle 'Games'...\nhello\n0024:err:module:bad\nexit=0 after 1s\n"
        self.assertEqual(normalize_guest(raw), "hello\n")

    def test_trace_delta_and_exact(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = Path(t) / "a.txt", Path(t) / "b.txt"
            a.write_text("0.0 n 1.000 2\n0.5 n 1.0005 2\n")
            b.write_text("0.0 n 1.000 2\n0.5 n 1.0005 3\n")
            r = compare_traces(a, b, 0.001)
            self.assertEqual(r["status"], "FAIL")
            self.assertFalse(r["numeric_ok"])
            self.assertEqual(round(r["max_delta_per_column"][1], 4), 1.0)

    def _run(self, tmp, out_name, code_a, code_b, extra=()):
        orig, reb = Path(tmp) / "a.exe", Path(tmp) / "b.exe"
        orig.write_bytes(b"MZ original")
        reb.write_bytes(b"\x7fELF rebuilt")
        out = Path(tmp) / out_name
        cmd = [sys.executable, "-m", "tools.differential", "process",
               "--original", str(orig), "--rebuilt", str(reb),
               "--original-cmd", "%s -c %s" % (sys.executable, repr(code_a)),
               "--rebuilt-cmd", "%s -c %s" % (sys.executable, repr(code_b)),
               "--timeout", "15", "--out", str(out), *extra]
        p = subprocess.run(cmd, capture_output=True, cwd=Path(__file__).resolve().parents[1])
        return p, json.loads(out.read_text())

    def test_identical_runs_pass(self):
        with tempfile.TemporaryDirectory() as t:
            p, rec = self._run(t, "pass.json", "print('hello')", "print('hello')")
            self.assertEqual(rec["status"], "PASS", rec)
            self.assertTrue(rec["checks"]["stdout_exact"])
            self.assertEqual(p.returncode, 0)

    def test_different_output_fails_with_line(self):
        with tempfile.TemporaryDirectory() as t:
            p, rec = self._run(t, "fail.json", "print('hello')", "print('world')")
            self.assertEqual(rec["status"], "FAIL")
            self.assertEqual(rec["failed_checks"], ["stdout_exact"])
            self.assertEqual(rec["differences"]["stdout"]["line"], 1)
            self.assertEqual(p.returncode, 1)

    def test_unstartable_original_is_blocked_not_pass(self):
        with tempfile.TemporaryDirectory() as t:
            p, rec = self._run(t, "blocked.json", "print('hello')",
                               "print('hello')")
            # Override the original command to a non-existent binary: the
            # driver must report BLOCKED, never PASS/FAIL on missing evidence.
            out = Path(t) / "blocked2.json"
            cmd = [sys.executable, "-m", "tools.differential", "process",
                   "--original", str(Path(t) / "a.exe"),
                   "--rebuilt", str(Path(t) / "b.exe"),
                   "--original-cmd", "/definitely/not/a/runner {exe}",
                   "--rebuilt-cmd", "%s -c 'print(1)'" % sys.executable,
                   "--out", str(out)]
            subprocess.run(cmd, capture_output=True, cwd=Path(__file__).resolve().parents[1])
            rec2 = json.loads(out.read_text())
            self.assertEqual(rec2["status"], "BLOCKED")

    def test_runner_infra_failure_is_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            orig, reb = Path(t) / "a.exe", Path(t) / "b.exe"
            orig.write_bytes(b"MZ"); reb.write_bytes(b"MZ")
            out = Path(t) / "infra.json"
            cmd = [sys.executable, "-m", "tools.differential", "process",
                   "--original", str(orig), "--rebuilt", str(reb),
                   "--original-cmd", "%s -c 'print(1)'" % sys.executable,
                   "--rebuilt-cmd", "%s -c 'import sys;sys.stderr.write(\"Cannot connect to the Docker daemon\")'" % sys.executable,
                   "--out", str(out)]
            subprocess.run(cmd, capture_output=True, cwd=Path(__file__).resolve().parents[1])
            rec = json.loads(out.read_text())
            self.assertEqual(rec["status"], "BLOCKED")
            self.assertIn("Docker", rec["reason"])

    def test_frame_sets_compared(self):
        with tempfile.TemporaryDirectory() as t:
            fa, fb = Path(t) / "fa", Path(t) / "fb"
            fa.mkdir(); fb.mkdir()
            (fa / "f0.bmp").write_bytes(b"AAA")
            (fb / "f0.bmp").write_bytes(b"BBB")
            p, rec = self._run(t, "frames.json", "print('x')", "print('x')",
                               ("--frames", str(fa), str(fb)))
            self.assertEqual(rec["status"], "FAIL")
            self.assertTrue(rec["failed_checks"][0].startswith("frames"))


if __name__ == "__main__":
    unittest.main()
