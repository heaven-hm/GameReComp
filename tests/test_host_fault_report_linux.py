"""Regression: the fault-report PC extraction must build on Linux aarch64 too.

The aarch64 Linux matrix build failed on an x86_64-only
``uc_mcontext.gregs[REG_RIP]`` expression. Keep the architecture guards, and
syntax-check the real source in the prebuilt Linux toolchain image when that
image is available (skipped otherwise).
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HOST_C = ROOT / 'runtime' / 'win32hle' / 'host.c'
WSOCK_C = ROOT / 'runtime' / 'win32hle' / 'wsock32.c'
IMAGE = 'gamerecomp/linux-runner:local'


def _toolchain_available() -> bool:
    try:
        if subprocess.run(['docker', 'image', 'inspect', IMAGE],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False).returncode != 0:
            return False
        from tools import linux_matrix
        ok, _ = linux_matrix.verify_bind_mount(ROOT, IMAGE)
        return ok
    except Exception:
        return False


class FaultReportSourceTest(unittest.TestCase):
    def test_arch_guards_present(self):
        text = HOST_C.read_text()
        for token in ('defined(__x86_64__)', 'REG_RIP',
                      'defined(__i386__)', 'REG_EIP',
                      'defined(__aarch64__)', 'uc_mcontext.pc',
                      'defined(__arm__)', 'arm_pc'):
            self.assertIn(token, text, token)

    def test_no_unconditional_x86_registers(self):
        text = HOST_C.read_text()
        self.assertNotIn('((ucontext_t *)ctx)->uc_mcontext.gregs[REG_RIP]', text)

    def test_bsd_only_sockaddr_len_fields_are_guarded(self):
        lines = WSOCK_C.read_text().splitlines()
        for i, line in enumerate(lines):
            if 'sin_len =' in line or 'sin6_len =' in line:
                guards = [g for g in lines[max(0, i - 3):i] if '#ifdef __APPLE__' in g]
                self.assertTrue(guards, f'unguarded BSD sockaddr field at line {i + 1}: {line}')


@unittest.skipUnless(_toolchain_available(), 'prebuilt Linux image unavailable')
class FaultReportLinuxCompileTest(unittest.TestCase):
    def _syntax_check(self, source):
        cmd = ['docker', 'run', '--rm', '-v', f'{ROOT}:/work', '-w', '/work',
               '--network', 'none', IMAGE, 'cc', '-fsyntax-only',
               '-I', 'runtime/win32hle', '-I', 'runtime/recomp32', source]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=120, check=False)
        self.assertEqual(p.returncode, 0, p.stdout[-2000:])

    def test_host_c_compiles_in_linux_container(self):
        self._syntax_check('runtime/win32hle/host.c')

    def test_wsock32_c_compiles_in_linux_container(self):
        self._syntax_check('runtime/win32hle/wsock32.c')


if __name__ == '__main__':
    unittest.main()
