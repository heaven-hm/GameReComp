"""cmpxchg8b: the 64-bit compare-and-swap behind 32-bit lock-free code.

F0 0F C7 /r (LOCK CMPXCHG8B m64) used to lift to a no-op. The compare that
follows it still passed in the common case, so a lock-free stack believed
every push and pop had happened: Rockall's heap in Rise of Legends drifted
until a share-lock claim spun forever. Only ZF changes, so the flag word is
frozen and just bit 0x40 is rewritten (the same trick rotates use for CF).

This is a fast structural regression guard: it checks the emitted C names
the 64-bit access, both register pairs, and the ZF-only flag freeze. The
semantic check against hardware lives in difftest.py's
`lock.cmpxchg8b.equal` / `lock.cmpxchg8b.differ` cases.

Run: python3 tools/lift/test_cmpxchg8b.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import capstone

from lift32 import Lifter


LOCK_CMPXCHG8B = bytes.fromhex('f00fc70e')   # lock cmpxchg8b qword ptr [esi]


def emit(encoding):
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    insn = next(iter(md.disasm(encoding, 0x1000)))
    return " ".join(Lifter().lift_instruction(insn))


def main():
    c = emit(LOCK_CMPXCHG8B)

    # mnemonic was recognised, not dropped to the no-op trailing statement.
    assert 'UNIMPLEMENTED' not in c, c
    assert 'cmpxchg8b' in c, c

    # One 64-bit load from the memory operand, and a 64-bit store on success.
    assert 'uint64_t _q = MEM64(_a)' in c, c
    assert 'MEM64(_a) = ((uint64_t)ecx << 32) | ebx' in c, c

    # The comparison is against edx:eax, the mismatch reload is into edx:eax.
    assert '(((uint64_t)edx << 32) | eax)' in c, c
    assert 'eax = (uint32_t)_q' in c, c
    assert 'edx = (uint32_t)(_q >> 32)' in c, c

    # ZF is the only flag written: freeze the previous word, flip 0x40, and
    # make the lazy flag model read the frozen value back.
    assert '_zf = 0x40u' in c and '_zf = 0' in c, c
    assert '& ~0x40u) | _zf' in c, c
    assert '_flag_k = FK_EFLAGS' in c, c

    print('cmpxchg8b: ok')


if __name__ == '__main__':
    main()
