#!/usr/bin/env python3
"""x64_oracle.py - run the ORIGINAL bytes of a real PE32+ under Unicorn.

The 32-bit path has difftest.py's run_unicorn_function(); this is its 64-bit
counterpart for whole-program runs. It exists so the native recompilation
selftest can compare against the real x86-64 executable rather than against an
expected string baked into a test. No synthetic binary is involved: the caller
hands it a real Windows x64 PE.

Only the three-import surface arith-calc.exe needs is emulated, through tiny
`ret` thunks whose addresses are written into the PE's IAT slots. A code hook
intercepts each thunk, performs the Win64 integer-register contract for that
import, emulates the `ret`, and continues. This is an oracle, not an HLE layer.
"""

import os
import struct
import sys

from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE, UcError
from unicorn.x86_const import (
    UC_X86_REG_RSP, UC_X86_REG_RIP, UC_X86_REG_RAX,
    UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8, UC_X86_REG_R9,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from generate64 import read_pe, parse_imports  # noqa: E402

IMAGE = 0x140000000          # arith-calc.exe's preferred ImageBase
STACK = 0x000000020000000
STACK_SZ = 1 << 20
THUNK = 0x000000030000000    # scratch page for the import thunks


def run_unicorn_x64(pe_path, limit=5_000_000):
    """Execute the original PE and return its WriteFile bytes + exit status."""
    data, base, soi, entry_rva, secs, imp_rva, _, _ = read_pe(pe_path)
    iat = parse_imports(data, base, secs, imp_rva)
    if not iat:
        raise RuntimeError("the PE has no imports; this oracle needs IAT slots")

    mu = Uc(UC_ARCH_X86, UC_MODE_64)
    mu.mem_map(IMAGE, (soi + 0x1000 + 0xfff) & ~0xfff)
    mu.mem_map(STACK - 0x1000, STACK_SZ + 0x2000)
    mu.mem_map(THUNK, 0x1000)
    for (_nm, vsa, _vs, ra, rs) in secs:
        if rs:
            mu.mem_write(IMAGE + vsa, data[ra:ra + rs])

    names = sorted(iat.items())       # deterministic slot -> index mapping
    for i, (slot, _pair) in enumerate(names):
        mu.mem_write(THUNK + i * 0x10, b'\xC3')       # ret
        mu.mem_write(slot, struct.pack('<Q', THUNK + i * 0x10))

    out = bytearray()
    state = {'exit': None, 'fault': None}

    def thunk_hook(mu_, addr, _size, _ud):
        if not (THUNK <= addr < THUNK + 0x1000):
            return
        idx = (addr - THUNK) // 0x10
        _dll, name = names[idx][1]
        rcx = mu_.reg_read(UC_X86_REG_RCX)
        if name == 'GetStdHandle':
            mu_.reg_write(UC_X86_REG_RAX, rcx)
        elif name == 'WriteFile':
            buf = mu_.reg_read(UC_X86_REG_RDX)
            n = mu_.reg_read(UC_X86_REG_R8) & 0xffffffff
            written = mu_.reg_read(UC_X86_REG_R9)
            if n:
                out.extend(bytes(mu_.mem_read(buf, n)))
            if written:
                mu_.mem_write(written, struct.pack('<I', n))
            mu_.reg_write(UC_X86_REG_RAX, 1)
        elif name == 'ExitProcess':
            state['exit'] = rcx & 0xffffffff
            mu_.emu_stop()
            return
        else:
            state['fault'] = f'unmodelled import: {name}'
            mu_.emu_stop()
            return
        rsp = mu_.reg_read(UC_X86_REG_RSP)
        mu_.reg_write(UC_X86_REG_RIP,
                      struct.unpack('<Q', bytes(mu_.mem_read(rsp, 8)))[0])
        mu_.reg_write(UC_X86_REG_RSP, rsp + 8)

    mu.hook_add(UC_HOOK_CODE, thunk_hook)
    sp = ((STACK + STACK_SZ // 2) & ~0xf) - 8
    mu.mem_write(sp, struct.pack('<Q', 0xDEADBEEF))   # fake return address
    mu.reg_write(UC_X86_REG_RSP, sp)
    mu.reg_write(UC_X86_REG_RIP, base + entry_rva)
    try:
        mu.emu_start(base + entry_rva, 0xDEADBEEF, count=limit)
    except UcError as e:
        state['fault'] = str(e)

    return {
        'engine': 'unicorn',
        'version': __import__('unicorn').__version__,
        'pe': os.path.abspath(pe_path),
        'stdout_hex': bytes(out).hex(),
        'exit': state['exit'],
        'fault': state['fault'],
        'imports': [f'{d}!{n}' for _s, (d, n) in names],
    }


if __name__ == '__main__':
    import json
    print(json.dumps(run_unicorn_x64(sys.argv[1]), indent=2))
