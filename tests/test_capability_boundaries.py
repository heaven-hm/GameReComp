"""Fail-closed capability boundaries: the tool must refuse what it cannot do.

This test proves the opposite of 'all x86/x64 software converts 100%': it
verifies the pipeline explicitly refuses x64 inputs and cross-OS Linux targets
instead of emitting fake native binaries. Paths are supplied by the caller;
nothing is hard-coded and no game binary is committed.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
X64 = os.environ.get('RECOMP_PROBE_X64_EXE')
X86 = os.environ.get('RECOMP_PROBE_X86_EXE')


def run(exe, extra):
    cmd = [sys.executable, '-m', 'tools.autorecomp', str(exe),
           '--out-mode', 'source-only', '--no-table', '--no-progress'] + extra
    p = subprocess.run(cmd, cwd=REPO, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, timeout=60, check=False)
    return p.returncode, (p.stdout or '')


@unittest.skipUnless(X64 and Path(X64).is_file(), 'RECOMP_PROBE_X64_EXE not supplied')
class CapabilityBoundaryTest(unittest.TestCase):
    def test_x64_input_is_refused(self):
        rc, out = run(X64, ['--input-os', 'windows', '--input-arch', 'auto',
                            '--target-os', 'host', '--target-arch', 'host'])
        self.assertNotEqual(rc, 0)
        self.assertIn('unsupported input architecture: x86_64', out)

    def test_forcing_x86_on_x64_is_refused(self):
        rc, out = run(X64, ['--input-os', 'windows', '--input-arch', 'x86',
                            '--target-os', 'host', '--target-arch', 'host'])
        self.assertNotEqual(rc, 0)
        self.assertIn('refusing to reconstruct on a guess', out)


@unittest.skipUnless(X86 and Path(X86).is_file(), 'RECOMP_PROBE_X86_EXE not supplied')
class CrossTargetTest(unittest.TestCase):
    def test_linux_cross_target_is_refused_on_macos(self):
        if sys.platform != 'darwin':
            self.skipTest('only meaningful on a macOS host')
        rc, out = run(X86, ['--input-os', 'windows', '--input-arch', 'x86',
                            '--target-os', 'linux', '--target-arch', 'x86_64'])
        self.assertNotEqual(rc, 0)
        self.assertIn('cross-target build requested', out)


if __name__ == '__main__':
    unittest.main()
