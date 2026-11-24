"""Frida probe's generated script and dry-run must be deterministic."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.frida_probe import script_source  # noqa: E402


class FridaProbeTests(unittest.TestCase):
    def test_script_contains_each_hook(self):
        src = script_source(["kernel32!ExitProcess", "MessageBoxW"])
        self.assertIn("kernel32!ExitProcess", src)
        self.assertIn("MessageBoxW", src)
        self.assertIn("Interceptor.attach", src)

    def test_dry_run_needs_no_frida_session(self):
        p = subprocess.run([sys.executable, "-m", "tools.frida_probe",
                            "--dry-run", "--hook", "ExitProcess"],
                           cwd=ROOT, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=30,
                           check=False)
        self.assertEqual(p.returncode, 0)
        self.assertIn("Interceptor.attach", p.stdout)


if __name__ == "__main__":
    unittest.main()
