"""Guard against audit finding: a correct COM frontend left dormant in the runtime."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RuntimeRegistrationTest(unittest.TestCase):
    def test_d3d8_frontend_is_registered(self):
        host = (ROOT / 'runtime/win32hle/host.c').read_text()
        header = (ROOT / 'runtime/win32hle/win32hle.h').read_text()
        frontend = (ROOT / 'runtime/win32hle/d3d8.c').read_text()
        self.assertIn('win32hle_register(win32hle_d3d8)', host)
        self.assertIn('extern const win32hle_shim win32hle_d3d8[]', header)
        self.assertIn('{ "Direct3DCreate8", d3d8_Direct3DCreate8 }', frontend)


if __name__ == '__main__':
    unittest.main()
