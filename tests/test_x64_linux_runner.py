"""Regression tests for PE32+ -> native Linux execution (PR #175 follow-up).

Covers the two bounded blockers that made the x64 path fail to build:

  1. cpu64.h's GS helpers used x86-only inline asm on every non-MSVC compiler,
     so aarch64 Linux rejected `movq %gs:(x3),x3`. The default is now a
     modelled TEB base, with native GS available only as an explicit opt-in.
  2. There was no way to turn generate64.py output into a project that
     tools/linux_matrix.py could build; tools/x64_project.py does that now.

Run with: python3 -m pytest tests/test_x64_linux_runner.py -q
"""

import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import x64_project  # noqa: E402
from tools.disasm.disasm64 import PE64  # noqa: E402
from tools.lift import generate64  # noqa: E402

CPU64 = ROOT / 'runtime' / 'recomp64_cpu' / 'cpu64.h'
HOST64 = ROOT / 'runtime' / 'recomp64_cpu' / 'host64_linux.c'


def minimal_pe64():
    """A structurally valid PE32+ header, enough for read_pe() only."""
    data = bytearray(0x200)
    data[0:2] = b'MZ'
    pe = 0x80
    struct.pack_into('<I', data, 0x3c, pe)
    data[pe:pe + 4] = b'PE\0\0'
    struct.pack_into('<H', data, pe + 4, 0x8664)      # AMD64
    struct.pack_into('<H', data, pe + 6, 1)           # one section
    struct.pack_into('<H', data, pe + 20, 0xF0)       # SizeOfOptionalHeader
    opt = pe + 24
    struct.pack_into('<H', data, opt, 0x20B)          # PE32+ magic
    struct.pack_into('<I', data, opt + 16, 0x1234)    # AddressOfEntryPoint
    struct.pack_into('<Q', data, opt + 24, 0x140000000)
    struct.pack_into('<I', data, opt + 56, 0x4000)    # SizeOfImage
    struct.pack_into('<I', data, opt + 60, 0x400)     # SizeOfHeaders
    return bytes(data)


class Cpu64GsTests(unittest.TestCase):
    def test_default_gs_helpers_are_not_x86_inline_asm(self):
        text = CPU64.read_text()
        self.assertIn('RECOMP64_GS_NATIVE', text, 'opt-in native GS must be explicit')
        # The modelled TEB is exposed through cpu64_rd_gs64/cpu64_wr_gs64 in
        # cpu64.h (implemented in cpu64.c). g_gs_base itself lives in the
        # loader's host64_linux.c, so asserting it here was stale.
        self.assertIn('uint64_t cpu64_rd_gs64(uint64_t off)', text,
                      'modelled TEB read helper is required')
        self.assertIn('void cpu64_wr_gs64(uint64_t off, uint64_t v)', text,
                      'modelled TEB write helper is required')
        # The x86 asm template may exist only in the explicit opt-in branch.
        opt_in = text.split('#elif defined(RECOMP64_GS_NATIVE) && defined(__x86_64__)')
        self.assertEqual(len(opt_in), 2)
        self.assertNotIn('__asm__', opt_in[0], 'default branch still emits x86 asm')
        self.assertIn('__asm__', opt_in[1])

    def test_gs_helpers_compile_to_non_x86_assembly(self):
        """Compile a real call to all four helpers and inspect the asm text."""
        src = ('#include "cpu64.h"\n'
               'uint64_t t64(uint64_t o) { return rd_gs64(o); }\n'
               'uint32_t t32(uint64_t o) { return rd_gs32(o); }\n'
               'void s64(uint64_t o, uint64_t v) { wr_gs64(o, v); }\n'
               'void s32(uint64_t o, uint32_t v) { wr_gs32(o, v); }\n'
               'uint8_t *gs_base;\n')
        with tempfile.TemporaryDirectory() as t:
            c = Path(t) / 'gs.c'
            asm = Path(t) / 'gs.s'
            c.write_text(src)
            p = subprocess.run(['cc', '-w', '-O0', '-I', str(CPU64.parent),
                                '-S', str(c), '-o', str(asm)],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            text = asm.read_text()
            self.assertNotIn('%gs:', text, 'assembler still sees an x86 segment override')

    def test_host_supplies_gs_base_and_dispatch(self):
        text = HOST64.read_text()
        self.assertIn('uint8_t *g_gs_base;', text)
        self.assertIn('TEB.Self', text)
        self.assertIn('void dispatch(CPU *c, uint64_t target)', text)
        self.assertIn('void dispatch_jmp(CPU *c, uint64_t target)', text)
        for name in ('ExitProcess', 'GetStdHandle', 'WriteFile'):
            self.assertIn('"%s"' % name, text)


class TruncatedPdataTests(unittest.TestCase):
    """imgview64.exe's exception directory claims far more than the file holds.

    Both readers used to raise struct.error/IndexError and abort the catalog.
    The fixture is synthetic only in its header layout; it reproduces the
    exact measured truncation (directory size > bytes mapped by the section).
    """

    def _pe(self, path):
        data = bytearray(0x1000)
        data[0:2] = b'MZ'
        pe = 0x80
        struct.pack_into('<I', data, 0x3c, pe)
        data[pe:pe + 4] = b'PE\0\0'
        struct.pack_into('<H', data, pe + 4, 0x8664)
        struct.pack_into('<H', data, pe + 6, 1)            # one section
        struct.pack_into('<H', data, pe + 20, 0xF0)
        opt = pe + 24
        struct.pack_into('<H', data, opt, 0x20B)
        struct.pack_into('<I', data, opt + 16, 0x1000)     # entry RVA
        struct.pack_into('<Q', data, opt + 24, 0x140000000)
        struct.pack_into('<I', data, opt + 56, 0x3000)     # SizeOfImage
        struct.pack_into('<I', data, opt + 60, 0x200)      # SizeOfHeaders
        struct.pack_into('<I', data, opt + 108, 16)        # NumberOfRvaAndSizes
        # Data directory 3 (.pdata): 0x600 bytes = 128 entries, but the section
        # holds only 0x40 bytes on disk (three entries at most).
        struct.pack_into('<II', data, opt + 112 + 3 * 8, 0x1000, 0x600)
        sh = opt + 0xF0
        data[sh:sh + 6] = b'.code\0'
        struct.pack_into('<IIII', data, sh + 8, 0x1000, 0x1000, 0x40, 0x400)
        struct.pack_into('<I', data, sh + 36, 0x60000020)  # CODE|EXECUTE|READ
        struct.pack_into('<III', data, 0x400, 0x1000, 0x1010, 0x1100)
        data[0x500] = 0x01                                   # minimal unwind info
        path.write_bytes(data)
        return path

    def test_disasm64_clamps_truncated_pdata(self):
        with tempfile.TemporaryDirectory() as t:
            pe = self._pe(Path(t) / 'trunc.exe')
            parsed = PE64(pe.read_bytes())
            primary, chunks = parsed.pdata()
            self.assertEqual(primary, {0x140001000})
            self.assertEqual(chunks, set())
            # Directory claims 128 entries; only 0x40/12 = 5 fit in the file.
            self.assertEqual(len(list(parsed.pdata_entries())), 5)

    def test_generate64_finds_code_without_text_named_section(self):
        with tempfile.TemporaryDirectory() as t:
            pe = self._pe(Path(t) / 'code.exe')
            parsed = generate64.read_pe(str(pe))
            self.assertEqual(parsed[-1], [(0x1000, 0x1000, 0x40)])


class X64ProjectTests(unittest.TestCase):
    def _project(self, root: Path):
        exe = root / 'guest.exe'
        exe.write_bytes(minimal_pe64())
        gen = root / 'gen'
        gen.mkdir()
        (gen / 'recomp_dispatch.c').write_text('/* stub */\n')
        out = root / 'project'
        rc = x64_project.main(['--exe', str(exe), '--gen', str(gen), '--out', str(out)])
        return rc, out

    def test_generator_emits_buildable_shape(self):
        with tempfile.TemporaryDirectory() as t:
            rc, out = self._project(Path(t))
            self.assertEqual(rc, 0)
            self.assertTrue((out / 'CMakeLists.txt').is_file())
            for name in ('main.c', 'guest_image.c', 'cpu64.h', 'eh64.h',
                         'recomp_dispatch.c'):
                self.assertTrue((out / 'src' / name).is_file(), name)
            import json
            meta = json.loads((out / 'project_meta.json').read_text())
            self.assertEqual(meta['image_base'], 0x140000000)
            self.assertEqual(meta['entry_va'], 0x140001234)
            self.assertEqual(meta['size_of_image'], 0x4000)
            image_c = (out / 'src' / 'guest_image.c').read_text()
            self.assertIn('0x4d,', image_c)  # MZ
            self.assertIn('const unsigned long guest_image_len', image_c)

    def test_rejects_pe32(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            exe = root / 'guest32.exe'
            bad = bytearray(minimal_pe64())
            struct.pack_into('<H', bad, 0x80 + 24, 0x10B)   # PE32 magic
            exe.write_bytes(bad)
            gen = root / 'gen'
            gen.mkdir()
            (gen / 'recomp_dispatch.c').write_text('')
            with self.assertRaises(SystemExit) as e:
                x64_project.main(['--exe', str(exe), '--gen', str(gen),
                                  '--out', str(root / 'p')])
            self.assertIn('PE32+', str(e.exception))


if __name__ == '__main__':
    unittest.main()
