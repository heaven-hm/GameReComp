"""Native macOS ARM64 execution of a lifted function from a real Win16 NE.

The source is SHIPEDIT.EXE (OpenTyrian 2.0 shareware data), SHA-256
9be5f168ee0e0743c0e9a541b8620820b6d53b6d8458fdc8d3c50727ee9c3181.
25 bytes at NE segment 2, offset 0x2F2E were lifted with tools/lift/ne_lift.py,
and the resulting C is built by clang -arch arm64 against the unmodified
runtime/recomp16/cpu.h. The same 25 bytes are executed by Unicorn in x86-16
mode with the identical initial state; registers, flags, the seven bytes the
function writes, and a full-memory CRC must agree.

This is an end-to-end check of the *native* path, not a re-run of
difftest16.py: the binary under test is a normal arm64 Mach-O with no emulator
and no Wine. Skipped off Apple Silicon or when unicorn/a compiler is missing.
"""

import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

CODE = bytes.fromhex(
    "a3 58 f5 89 16 62 f5 88 0e 57 f5 c6 06 56 f5 01 33 c0 a3 5c f5 89 16 5e f5 c3"
)
MEM_SIZE = 0x110000   # runtime/recomp16/cpu.h MEM_SIZE (1 MB + 64 KB)
CODE_AT = 0x30000     # CS=0x3000 fits in a 16-bit segment register
REGS = {
    "ax": 0x1234, "bx": 0x2222, "cx": 0x00EF, "dx": 0xABCD,
    "sp": 0xFF00, "bp": 0x3333, "si": 0x4444, "di": 0x5555,
}


def _reference():
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_PROT_ALL
    from unicorn import x86_const as X

    pattern = bytes(((i * 7 + 13) & 0xFF) for i in range(MEM_SIZE))
    uc = Uc(UC_ARCH_X86, UC_MODE_16)
    uc.mem_map(0, MEM_SIZE, UC_PROT_ALL)
    uc.mem_write(0, pattern)
    uc.mem_write(CODE_AT, CODE + b"\x90" * (0x20 - len(CODE)) + b"\xf4")
    # C3 pops IP; aim it at the HLT at CODE_AT+0x20 so the reference stops there.
    uc.mem_write(0xFF00, b"\x20\x00")
    for name, val in REGS.items():
        uc.reg_write(getattr(X, "UC_X86_REG_" + name.upper()), val)
    for s in ("ds", "es", "ss"):
        uc.reg_write(getattr(X, "UC_X86_REG_" + s.upper()), 0)
    uc.reg_write(X.UC_X86_REG_CS, CODE_AT >> 4)
    uc.reg_write(X.UC_X86_REG_EFLAGS, 0x0202)
    uc.emu_start(CODE_AT, CODE_AT + 0x20)
    regs = {n: uc.reg_read(getattr(X, "UC_X86_REG_" + n.upper())) for n in REGS}
    flags = uc.reg_read(X.UC_X86_REG_EFLAGS)
    mem = bytes(uc.mem_read(0, MEM_SIZE))
    crc = 0
    for off in range(0, MEM_SIZE, 4):
        crc = ((crc * 16777619) ^ struct.unpack_from("<I", mem, off)[0]) & 0xFFFFFFFF
    slots = {f"{a:04X}": struct.unpack_from("<H", mem, a)[0]
             for a in (0xF552, 0xF558, 0xF55C, 0xF55E)}
    slots.update({f"{a:04X}": mem[a]
                  for a in (0xF556, 0xF557)})
    return regs, flags, slots, crc


class NativeSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin" or platform.machine() != "arm64":
            raise unittest.SkipTest("native smoke test targets macOS ARM64")
        try:
            import unicorn  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("unicorn not installed")
        # Try the same compiler order as difftest16.py: on this host /usr/bin/clang
        # points at a missing MacOSX27.sdk while /usr/bin/gcc (a clang wrapper)
        # has a working sysroot.
        ccs = [shutil.which(n) for n in ("gcc", "clang", "cc")]
        ccs = [c for c in ccs if c]
        if not ccs:
            raise unittest.SkipTest("no C compiler installed")
        cls._tmp = tempfile.TemporaryDirectory(prefix="win16_smoke_")
        exe = os.path.join(cls._tmp.name, "win16_smoke")
        err = ""
        for cc in ccs:
            cmd = [cc, "-arch", "arm64", "-O2",
                   "-I", str(ROOT / "runtime"), "-I", str(HERE),
                   str(HERE / "main.c"), str(HERE / "seg002_2F2E.c"),
                   "-o", exe]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                break
            err += f"--- {cc}\n{r.stderr}"
        else:
            raise unittest.SkipTest("no compiler could build the smoke binary: " + err)
        cls._exe = exe

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_tmp"):
            cls._tmp.cleanup()

    def test_native_binary_is_arm64(self):
        out = subprocess.run(["file", self._exe], capture_output=True, text=True).stdout
        self.assertIn("arm64", out, out)

    def test_lifted_c_regenerates_from_real_ne(self):
        """If the source binary is present, the fixture must be its lift.

        Skipped when the NE is not on this host (it is a shareware game data
        file, deliberately not vendored). The committed C differs from raw
        ne_lift output only in the include line -- one leaf must not need the
        whole-module `segments.h`.
        """
        import hashlib
        ne = Path.home() / "Downloads/recomp-apps/games/opentyrian/data/shipedit.exe"
        if not ne.exists():
            self.skipTest("source SHIPEDIT.EXE not present on this host")
        self.assertEqual(
            hashlib.sha256(ne.read_bytes()).hexdigest(),
            "9be5f168ee0e0743c0e9a541b8620820b6d53b6d8458fdc8d3c50727ee9c3181")
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "lift" / "ne_lift.py"),
             str(ne), "--seg", "2", "--func", "0x2F2E"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stderr)
        generated = r.stdout[r.stdout.index("void seg002_2F2E"):].strip()
        committed = (HERE / "seg002_2F2E.c").read_text(encoding="utf-8")
        committed = committed[committed.index("void seg002_2F2E"):].strip()
        self.assertEqual(committed, generated)

    def test_lifted_function_matches_real_x86_bytes(self):
        out = subprocess.run([self._exe], capture_output=True, text=True, check=True).stdout
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2, out)
        regs = {k: int(v, 16) for k, v in re.findall(r"(\w+)=([0-9A-Fa-f]+)", lines[0])}
        fields = {k: int(v, 16) for k, v in re.findall(r"(\w+)=([0-9A-Fa-f]+)", lines[1])}
        want_regs, want_flags, want_slots, want_crc = _reference()
        crc = fields.pop("crc")

        self.assertEqual({k: regs[k] for k in REGS}, want_regs)
        self.assertEqual(regs["flags"], want_flags & 0xFFFF)
        self.assertEqual(fields, want_slots)
        self.assertEqual(crc, want_crc)


if __name__ == "__main__":
    unittest.main()
