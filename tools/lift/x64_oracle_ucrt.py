#!/usr/bin/env python3
"""Unicorn x64 oracle for the small MSVC console PE32+ programs in the corpus.

x64_oracle.py covers the three-import arith-calc.exe. This module covers the
next ring: the standard UCRT/KERNEL32 startup + stdio surface that base64.exe,
tictactoe.exe and tetris.exe actually import. It runs the ORIGINAL x86-64
bytes, never the generated C, and is therefore an independent oracle.

The shims mirror runtime/recomp64_cpu/win64_shims.c one-for-one: same va_list
convention, same CRLF text-mode translation, same deterministic answers for
the CRT data cells. Only imports the programs ask for are modelled; anything
else stops the run with an explicit ``fault`` string (fail-closed).
"""

import os
import struct
import sys

from unicorn import (Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE,
                     UC_HOOK_MEM_INVALID)
from unicorn.x86_const import (
    UC_X86_REG_RAX, UC_X86_REG_RCX, UC_X86_REG_RDX,
    UC_X86_REG_R8, UC_X86_REG_R9, UC_X86_REG_RSP, UC_X86_REG_RIP,
    UC_X86_REG_GS_BASE,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from generate64 import read_pe, parse_imports  # noqa: E402

THUNK = 0x000000030000000     # IAT thunks (one page)
HEAP = 0x000000031000000      # simulated CRT data cells + bump allocator
HEAP_SZ = 1 << 20
STACK = 0x000000020000000
STACK_SZ = 1 << 20

R = lambda mu, r: mu.reg_read(r)                     # noqa: E731
W = lambda mu, r, v: mu.reg_write(r, v)               # noqa: E731


def _rd(mu, a, n):
    return bytes(mu.mem_read(a, n))


def _str(mu, a, cap=4096):
    b = bytearray()
    for i in range(cap):
        ch = _rd(mu, a + i, 1)[0]
        if not ch:
            break
        b.append(ch)
    return bytes(b)


def _cstr(s):
    return s + b'\0'


def _crlf(b):
    out = bytearray()
    for ch in b:
        if ch == 10 and (not out or out[-1] != 13):
            out.append(13)
        out.append(ch)
    return bytes(out)


def _format_into(mu, fmtva, vptr, out, slot=None):
    """Mini printf: enough of the C99 specifiers for these console programs."""
    fmt = _str(mu, fmtva)
    vargs = [struct.unpack('<Q', _rd(mu, vptr + 8 * i, 8))[0] for i in range(24)]
    def app(b):
        for c in b:
            if c == 10 and (not out or out[-1] != 13):
                out.append(13)
            out.append(c)

    ai = 0
    i = 0
    while i < len(fmt):
        ch = fmt[i]
        if ch != 0x25:                     # '%'
            app(bytes([ch]))
            i += 1
            continue
        if i + 1 < len(fmt) and fmt[i + 1] == 0x25:
            app(b'%')
            i += 2
            continue
        j = i + 1
        while j < len(fmt) and fmt[j] not in b'diouxXceEfFgGaApn%':
            j += 1
        if j >= len(fmt):
            app(b'%')
            i += 1
            continue
        conv = chr(fmt[j])
        spec = fmt[i:j + 1].decode('ascii', 'replace')
        host = spec.replace('I64', 'll')
        if 'l' in host[:-1] and 'll' not in host[:-1]:
            host = host.replace('l', '')   # guest long == int on Win64
        v = vargs[ai] if ai < len(vargs) else 0
        ai += 1
        if conv == 's':
            val = _str(mu, v)
        elif conv == 'c':
            val = v & 0xff
        elif conv == 'p':
            val = v
            host = '%llX'
        elif conv == 'n':
            mu.mem_write(v, struct.pack('<I', len(out)))
            i = j + 1
            continue
        elif conv in 'diouxX':
            if conv in 'uoxX':
                val = v & ((1 << 64) - 1)
            else:
                val = v & 0xffffffffffffffff
                if 'hh' in host[:-1]:
                    val = (val & 0xff)
                    if val & 0x80: val -= 0x100
                elif 'h' in host[:-1]:
                    val = val & 0xffff
                    if val & 0x8000: val -= 0x10000
                elif 'll' not in host[:-1]:
                    val &= 0xffffffff
                    if val & 0x80000000: val -= 0x100000000
        else:
            val = v
        try:
            piece = (host % val).encode('ascii', 'replace')
        except (TypeError, ValueError):
            piece = (spec % val).encode('ascii', 'replace')
        out.extend(_crlf(piece))
        i = j + 1
    return len(out)


def run_unicorn_x64(pe_path, stdin=b'', limit=20_000_000):
    """Execute the original PE under Unicorn with UCRT shims.

    Returns exit/stdout_hex/fault/imports exactly like x64_oracle.py, plus
    ``engine_version`` and ``stdin_hex`` so the evidence is self-describing.
    """
    import unicorn

    data, base, soi, entry_rva, secs, imp_rva, _, _ = read_pe(pe_path)
    iat = parse_imports(data, base, secs, imp_rva)
    if not iat:
        raise RuntimeError('the PE has no imports; this oracle needs IAT slots')

    mu = Uc(UC_ARCH_X86, UC_MODE_64)
    mu.mem_map(base, (soi + 0x3000) & ~0xfff)
    mu.mem_map(STACK - 0x1000, STACK_SZ + 0x2000)
    mu.mem_map(THUNK, 0x1000)
    mu.mem_map(HEAP, HEAP_SZ)
    for (_nm, vsa, _vs, ra, rs) in secs:
        if rs:
            mu.mem_write(base + vsa, data[ra:ra + rs])

    # Simulated CRT data cells (see win64_shims.c data_init()).
    PROG = HEAP + 0x30
    ARGV = HEAP + 0x10
    CELL = HEAP + 0x20
    ENVP = HEAP + 0x50
    IOB = HEAP + 0x60
    COMMODE = HEAP + 0x120
    FMODE = HEAP + 0x124
    BUMP = [HEAP + 0x1000]
    mu.mem_write(PROG, _cstr(b'recomp64.exe'))
    mu.mem_write(ARGV, struct.pack('<QQ', PROG, 0))
    mu.mem_write(CELL, struct.pack('<Q', ARGV))
    mu.mem_write(ENVP, struct.pack('<Q', 0))
    mu.mem_write(COMMODE, struct.pack('<I', 0))
    mu.mem_write(FMODE, struct.pack('<I', 0x40))
    # Win64 gs:[0x30] is the PEB; Unicorn's GS base is zero by default.
    TEB, PEB = HEAP + 0x800, HEAP + 0xA00
    mu.reg_write(UC_X86_REG_GS_BASE, TEB)
    mu.mem_write(TEB + 0x30, struct.pack('<Q', PEB))
    mu.mem_write(PEB + 0x10, struct.pack('<Q', base))
    for i in range(3):
        b = bytearray(48)
        struct.pack_into('<i', b, 16, i)       # _file
        struct.pack_into('<i', b, 12, 1 if i == 0 else 2)  # _flag
        mu.mem_write(IOB + 48 * i, bytes(b))

    names = sorted(iat.items())
    for i, (slot, _pair) in enumerate(names):
        mu.mem_write(THUNK + i * 0x10, b'\xC3')
        mu.mem_write(slot, struct.pack('<Q', THUNK + i * 0x10))

    out = bytearray()
    inp = bytearray(stdin)
    ipos = [0]
    state = {'exit': None, 'fault': None}
    tls = {}

    def rd32(a):
        return struct.unpack('<I', _rd(mu, a, 4))[0] if a else 0

    def rd64(a):
        return struct.unpack('<Q', _rd(mu, a, 8))[0] if a else 0

    def wr32(a, v):
        if a:
            mu.mem_write(a, struct.pack('<I', v & 0xffffffff))

    def wr64(a, v):
        if a:
            mu.mem_write(a, struct.pack('<Q', v & 0xffffffffffffffff))

    def emit(b):
        out.extend(_crlf(b))

    def finish(retaddr):
        mu.reg_write(UC_X86_REG_RIP, retaddr)
        mu.reg_write(UC_X86_REG_RSP, R(mu, UC_X86_REG_RSP) + 8)

    def hook(_mu, addr, _size, _ud):
        if not (THUNK <= addr < THUNK + 0x1000):
            return
        idx = (addr - THUNK) // 0x10
        _dll, name = names[idx][1]
        rcx, rdx, r8, r9 = (R(mu, r) for r in
                            (UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8, UC_X86_REG_R9))
        rsp = R(mu, UC_X86_REG_RSP)
        ret = rd64(rsp)
        va = rd64(rsp + 40) if rsp + 48 < STACK + STACK_SZ else 0
        nop = lambda: finish(ret)

        if name in ('ExitProcess', 'exit', '_exit'):
            state['exit'] = rcx & 0xffffffff
            mu.emu_stop()
            return
        if name == 'abort':
            state['fault'] = 'guest abort()'
            mu.emu_stop()
            return
        if name in ('DeleteCriticalSection', 'EnterCriticalSection',
                    'LeaveCriticalSection', 'InitializeCriticalSection', 'Sleep',
                    '_configthreadlocale', '__setusermatherr',
                    '_set_invalid_parameter_handler', '_set_app_type',
                    '_configure_narrow_argv', '_initialize_narrow_environment',
                    'setvbuf', '__C_specific_handler', 'TlsFree',
                    'SetLastError', '_cexit', 'fflush'):
            W(mu, UC_X86_REG_RAX, 1)
            nop(); return
        if name == 'GetLastError':
            W(mu, UC_X86_REG_RAX, 0); nop(); return
        if name == 'SetUnhandledExceptionFilter' or name == 'signal':
            W(mu, UC_X86_REG_RAX, 0); nop(); return
        if name == 'TlsGetValue':
            W(mu, UC_X86_REG_RAX, tls.get(rcx & 0x3f, 0)); nop(); return
        if name == 'TlsSetValue':
            tls[rcx & 0x3f] = rdx; W(mu, UC_X86_REG_RAX, 1); nop(); return
        if name == 'TlsAlloc':
            W(mu, UC_X86_REG_RAX, 3); nop(); return
        if name == 'VirtualProtect':
            if r9: wr32(r9, r8)
            W(mu, UC_X86_REG_RAX, 1); nop(); return
        if name == 'VirtualQuery':
            W(mu, UC_X86_REG_RAX, 0x30); nop(); return
        if name == '_set_new_mode':
            W(mu, UC_X86_REG_RAX, 0); nop(); return
        if name == '__p___argc':
            W(mu, UC_X86_REG_RAX, HEAP); nop(); return
        if name == '__p___argv':
            W(mu, UC_X86_REG_RAX, CELL); nop(); return
        if name == '__p__environ':
            W(mu, UC_X86_REG_RAX, ENVP); nop(); return
        if name == '__p__commode':
            W(mu, UC_X86_REG_RAX, COMMODE); nop(); return
        if name == '__p__fmode':
            W(mu, UC_X86_REG_RAX, FMODE); nop(); return
        if name == '__acrt_iob_func':
            W(mu, UC_X86_REG_RAX, IOB + 48 * (rcx % 3)); nop(); return
        if name in ('_initterm', '_initterm_e'):
            W(mu, UC_X86_REG_RAX, 0); nop(); return
        if name == '_crt_atexit':
            W(mu, UC_X86_REG_RAX, 0); nop(); return
        if name in ('malloc', 'calloc', 'realloc'):
            n = rcx if name == 'malloc' else rcx * rdx
            n = (n + 15) & ~15
            p = BUMP[0]
            BUMP[0] += max(n, 16)
            if BUMP[0] >= HEAP + HEAP_SZ:
                state['fault'] = 'oracle heap exhausted'
                mu.emu_stop(); return
            W(mu, UC_X86_REG_RAX, p); nop(); return
        if name == 'free':
            nop(); return
        if name == 'strlen':
            W(mu, UC_X86_REG_RAX, len(_str(mu, rcx))); nop(); return
        if name == 'strncmp':
            a, b = _str(mu, rcx, 256), _str(mu, rdx, 256)
            d = (a[:r8] > b[:r8]) - (a[:r8] < b[:r8])
            W(mu, UC_X86_REG_RAX, d & 0xffffffffffffffff); nop(); return
        if name == 'strcmp':
            a, b = _str(mu, rcx, 256), _str(mu, rdx, 256)
            d = (a > b) - (a < b)
            W(mu, UC_X86_REG_RAX, d & 0xffffffffffffffff); nop(); return
        if name in ('memcpy', 'memmove'):
            mu.mem_write(rcx, _rd(mu, rdx, r8)); W(mu, UC_X86_REG_RAX, rcx); nop(); return
        if name == 'memset':
            mu.mem_write(rcx, bytes([rdx & 0xff]) * r8); W(mu, UC_X86_REG_RAX, rcx); nop(); return
        if name in ('strchr', 'strrchr'):
            s = _str(mu, rcx, 256)
            pos = s.find(rdx & 0xff) if name == 'strchr' else s.rfind(rdx & 0xff)
            W(mu, UC_X86_REG_RAX, rcx + pos if pos >= 0 else 0); nop(); return
        if name == 'toupper' or name == 'tolower':
            ch = chr(rcx & 0xff)
            v = ord(ch.upper() if name == 'toupper' else ch.lower())
            W(mu, UC_X86_REG_RAX, v); nop(); return
        if name == 'atoi':
            s = _str(mu, rcx, 64).lstrip()
            neg = s.startswith(b'-')
            digits = s.lstrip(b'+-')
            v = 0
            for c in digits:
                if not (48 <= c <= 57): break
                v = v * 10 + c - 48
            W(mu, UC_X86_REG_RAX, (-v if neg else v) & 0xffffffff); nop(); return
        if name == 'rand':
            W(mu, UC_X86_REG_RAX, 1804289383); nop(); return
        if name == 'srand':
            nop(); return
        if name == '_time64':
            W(mu, UC_X86_REG_RAX, 0); nop(); return
        if name == '__stdio_common_vfprintf':
            before = len(out)
            _format_into(mu, r8, va, out)
            W(mu, UC_X86_REG_RAX, len(out) - before); nop(); return
        if name == 'puts':
            emit(_str(mu, rcx) + b'\n'); W(mu, UC_X86_REG_RAX, 0); nop(); return
        if name == 'putchar':
            emit(bytes([rcx & 0xff])); W(mu, UC_X86_REG_RAX, rcx & 0xff); nop(); return
        if name == 'getchar':
            ch = inp[ipos[0]] if ipos[0] < len(inp) else -1
            if ipos[0] < len(inp): ipos[0] += 1
            W(mu, UC_X86_REG_RAX, ch & 0xffffffffffffffff); nop(); return
        if name == 'fgets':
            n = rdx & 0xffffffff
            b = bytearray()
            while len(b) < max(n - 1, 0) and ipos[0] < len(inp):
                c = inp[ipos[0]]; ipos[0] += 1
                b.append(c)
                if c == 10: break
            if b:
                mu.mem_write(rcx, bytes(b) + b'\0')
                W(mu, UC_X86_REG_RAX, rcx)
            else:
                W(mu, UC_X86_REG_RAX, 0)
            nop(); return
        if name == '__stdio_common_vfscanf':
            fmt = _str(mu, r8)
            got = 0
            ai = 0
            i = 0
            while i < len(fmt):
                c = chr(fmt[i])
                if c.isspace():
                    while ipos[0] < len(inp) and chr(inp[ipos[0]]).isspace(): ipos[0] += 1
                    i += 1; continue
                if c != '%':
                    if ipos[0] >= len(inp) or inp[ipos[0]] != fmt[i]: break
                    ipos[0] += 1; i += 1; continue
                if i + 1 < len(fmt) and fmt[i + 1] == 0x25:
                    i += 2; continue
                j = i + 1
                while j < len(fmt) and chr(fmt[j]) not in 'diouxXcfFeEgGaAs[': j += 1
                if j >= len(fmt): break
                conv = chr(fmt[j]); spec = fmt[i:j + 1].decode()
                suppress = '*' in spec
                dst = struct.unpack('<Q', _rd(mu, va + 8 * ai, 8))[0]
                ai += 1
                if ipos[0] < len(inp) and chr(inp[ipos[0]]).isspace():
                    while ipos[0] < len(inp) and chr(inp[ipos[0]]).isspace(): ipos[0] += 1
                if conv in 'diu':
                    st = ipos[0]
                    if st < len(inp) and inp[st] in (43, 45): st += 1
                    en = st
                    while en < len(inp) and 48 <= inp[en] <= 57: en += 1
                    if en == st: break
                    v = int(inp[ipos[0]:en])
                    ipos[0] = en
                    if not suppress:
                        if 'hh' in spec: wr8 = lambda a, x: mu.mem_write(a, bytes([x & 0xff]))
                        if 'hh' in spec: wr8(dst, v)
                        elif 'h' in spec: wr32(dst, v & 0xffff)
                        elif 'll' in spec: wr64(dst, v)
                        else: wr32(dst, v & 0xffffffff)
                    got += 1
                elif conv == 'c':
                    if ipos[0] >= len(inp): break
                    ch = inp[ipos[0]]; ipos[0] += 1
                    if not suppress: mu.mem_write(dst, bytes([ch]))
                    got += 1
                elif conv == 's':
                    st = ipos[0]
                    while ipos[0] < len(inp) and not chr(inp[ipos[0]]).isspace(): ipos[0] += 1
                    if ipos[0] == st: break
                    if not suppress: mu.mem_write(dst, bytes(inp[st:ipos[0]]) + b'\0')
                    got += 1
                else:
                    state['fault'] = 'scanf spec %' + conv + ' unmodelled'
                    mu.emu_stop(); return
                i = j + 1
            W(mu, UC_X86_REG_RAX, got); nop(); return
        if name == 'fwrite':
            n = (rdx & 0xffffffff) * (r8 & 0xffffffff)
            emit(_rd(mu, rcx, n)); W(mu, UC_X86_REG_RAX, r8); nop(); return

        state['fault'] = 'unmodelled import %s!%s' % (_dll, name)
        mu.emu_stop()

    def invalid(_mu, access, addr, size, _value, _ud):
        if state['fault'] is None:
            state['fault'] = ('invalid %s at %#x size=%d rip=%#x'
                              % (access, addr, size,
                                 _mu.reg_read(UC_X86_REG_RIP)))
        return False

    mu.hook_add(UC_HOOK_MEM_INVALID, invalid)
    mu.hook_add(UC_HOOK_CODE, hook)
    sp = ((STACK + STACK_SZ // 2) & ~0xf) - 8
    mu.mem_write(sp, struct.pack('<Q', 0xDEADBEEF))
    mu.reg_write(UC_X86_REG_RSP, sp)
    mu.reg_write(UC_X86_REG_RAX, 1)          # g_argc cell at HEAP
    mu.reg_write(UC_X86_REG_RIP, base + entry_rva)
    try:
        mu.emu_start(base + entry_rva, 0xDEADBEEF, count=limit)
    except Exception as e:
        if state['fault'] is None:
            state['fault'] = '%s: %s' % (type(e).__name__, e)

    return {
        'engine': 'unicorn-ucrt',
        'version': unicorn.__version__,
        'pe': os.path.abspath(pe_path),
        'stdin_hex': bytes(stdin).hex(),
        'stdout_hex': bytes(out).hex(),
        'exit': state['exit'],
        'fault': state['fault'],
        'imports': ['%s!%s' % (d, n) for _s, (d, n) in names],
    }


if __name__ == '__main__':
    import json
    print(json.dumps(run_unicorn_x64(sys.argv[1],
          stdin=sys.argv[2].encode().decode('unicode_escape').encode()
          if len(sys.argv) > 2 else b''), indent=2))
