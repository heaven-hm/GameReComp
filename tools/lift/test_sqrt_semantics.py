"""SQRT must be the x86 instruction, not the host libm.

A `sqrtf(-2.25f)` in generated C produces a POSITIVE QNaN (7FC00000). x86's
sqrtss/sqrtps produce the NEGATIVE indefinite QNaN (FFC00000), pass a quiet
NaN through with its sign and payload intact, and quiet a signalling NaN by
setting the top mantissa bit. The differential suite found the first byte of
that in sse.sqrtps; this test pins the emitted C and the helper itself so the
other three forms cannot regress while only one case is being watched.

Run: python3 tools/lift/test_sqrt_semantics.py
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

import capstone
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

from tools.lift.lift32 import Lifter

# (mnemonic, encoding, helper the generated C must call)
FORMS = (
    ('sqrtss', 'f30f51c1', 'sse_sqrtf'),
    ('sqrtps', '0f51c1', 'sse_sqrtf'),
    ('sqrtsd', 'f20f51c1', 'sse_sqrtd'),
    ('sqrtpd', '660f51c1', 'sse_sqrtd'),
)

# Bit patterns measured on Unicorn 2.1.4 (x86 SQRT is the reference).
F32 = (
    (0xC0100000, 0xFFC00000),   # -2.25  -> negative indefinite
    (0xFF800000, 0xFFC00000),   # -inf   -> negative indefinite
    (0x7FC00001, 0x7FC00001),   # qNaN   -> unchanged
    (0xFFC00007, 0xFFC00007),   # qNaN-  -> unchanged
    (0x7F800001, 0x7FC00001),   # sNaN   -> quieted
    (0xFF800003, 0xFFC00003),   # sNaN-  -> quieted, sign kept
    (0x80000000, 0x80000000),   # -0.0   -> -0.0
    (0x00000000, 0x00000000),   # +0.0   -> +0.0
    (0x7F800000, 0x7F800000),   # +inf   -> +inf
)
F64 = (
    (0xC010000000000000, 0xFFF8000000000000),
    (0x7FF0000000000001, 0x7FF8000000000001),
    (0x7FF8000000000001, 0x7FF8000000000001),
    (0x8000000000000000, 0x8000000000000000),
)


def emitted(mnemonic, encoding):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    lifter = Lifter()
    lifter._labels = set()
    lifter._jump_targets = set()
    lifter._flag_state = None
    insn = next(iter(md.disasm(bytes.fromhex(encoding), 0x1000)))
    assert insn.mnemonic == mnemonic, (mnemonic, insn.mnemonic)
    return '\n'.join(lifter.lift_instruction(insn))


def check_emitted():
    for mnemonic, encoding, helper in FORMS:
        c = emitted(mnemonic, encoding)
        assert helper in c, (mnemonic, c)
        # No bare libm call: allow sse_sqrtf/sse_sqrtd, reject sqrtf(/sqrt(.
        bare = re.findall(r'(?<![A-Za-z0-9_])sqrt(?:f)?\s*\(', c)
        assert not bare, (mnemonic, c, bare)


def check_helper():
    lines = [
        '#include <stdint.h>',
        '#include <stdio.h>',
        '#include "recomp_types.h"',
        'int main(void) {',
    ]
    for src, _ in F32:
        lines.append(f'printf("%08X\\n", sse_u32(sse_sqrtf(sse_f32(0x{src:08X}u))));')
    for src, _ in F64:
        lines.append(f'printf("%016llX\\n", (unsigned long long)sse_u64('
                     f'sse_sqrtd(sse_f64(0x{src:016X}ull))));')
    lines.append('return 0; }')

    import shutil
    candidates = ([os.environ['RECOMP_CC']] if os.environ.get('RECOMP_CC')
                  else [shutil.which(n) for n in ('gcc', 'clang', 'cc')])
    candidates = [c for c in candidates if c]
    assert candidates, 'no C compiler found'
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / 'sqrt_helper.c'
        exe = Path(td) / 'sqrt_helper'
        src.write_text('\n'.join(lines) + '\n')
        inc = ROOT / 'runtime' / 'recomp32'
        # As in difftest.py, an installed compiler is not necessarily a working
        # one (broken SDK sysroots are common), so use the first that builds.
        for cc in candidates:
            r = subprocess.run([cc, '-O0', '-w', '-I', str(inc), str(src), '-o', str(exe)],
                               capture_output=True, text=True)
            if r.returncode == 0:
                break
        else:
            raise SystemExit('no compiler could build the sqrt helper')
        got = subprocess.check_output([str(exe)], text=True).split()

    expected = ['%08X' % dst for _, dst in F32] + ['%016X' % dst for _, dst in F64]
    assert got == expected, '\n'.join(
        '%s: got %s want %s' % (i, g, e)
        for i, (g, e) in enumerate(zip(got, expected)) if g != e)


def main():
    check_emitted()
    check_helper()
    print('sqrt semantics: ok (4 forms emitted through helpers, %d bit patterns match hardware)'
          % (len(F32) + len(F64)))


if __name__ == '__main__':
    main()
