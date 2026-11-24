#!/usr/bin/env python3
"""Agent P2 evidence: Unicorn harness over the REAL retail igi.exe bytes.

Two targeted emulations, both using code loaded from ~/Downloads/IGI/igi.exe
(no reimplementation of the guest):

  1. The guest's EnumTextureFormats callback at 0x50BC00, fed the six
     DDPIXELFORMATs advertised by GameReComp's runtime/win32hle/d3d7.c
     (parsed from that source, not hard-coded here).  This shows exactly which
     of GameReComp's formats the real guest accepts into its texture-format
     table at 0xa7c140 / count 0xa7cebc.

  2. The guest's DDPF_RGB / bitcount check at 0x50B6E5..0x50B70A, fed the
     selected format copied into a DDSURFACEDESC.  This shows whether the real
     bytes take the success path (0x50B6FB) or the 'Invalid texture format'
     path (0x50B6EC / 0x50BB62).

Emits JSON evidence to stdout (and --out path).  No synthetic success: if the
emulation cannot be set up, the record says so.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EIP

IGI = Path.home() / 'Downloads' / 'IGI' / 'igi.exe'
D3D7 = Path(__file__).resolve().parent.parent / 'runtime' / 'win32hle' / 'd3d7.c'

BASE = 0x400000
CB = 0x50BC00
CB_RET = 0x50BD62          # NOP pad just past both `ret 8` sites
CHECK = 0x50B6E5
CHECK_OK = 0x50B6FB
CHECK_ERR = 0x50B6EC
PF_TABLE = 0xA7C140
PF_COUNT = 0xA7CEBC
STACK = 0x30000000
STACK_TOP = 0x30080000


def image_bytes():
    pe = pefile.PE(str(IGI), fast_load=True)
    return pe.get_memory_mapped_image(), pe.OPTIONAL_HEADER.ImageBase


def read32(uc, a):
    return int.from_bytes(uc.mem_read(a, 4), 'little')


def write32(uc, a, v):
    uc.mem_write(a, int(v & 0xffffffff).to_bytes(4, 'little'))


def game_formats():
    """Parse the six rows of `static const uint32_t formats[6][8]` from d3d7.c."""
    txt = D3D7.read_text()
    m = re.search(r'formats\[\s*\d*\s*\]\[8\]\s*=\s*\{(.*?)\n\s*\};', txt, re.S)
    if not m:
        raise RuntimeError('cannot find dev_enum_textures formats[] in d3d7.c')
    rows = re.findall(r'\{([^{}]*)\}', m.group(1))
    out = []
    for r in rows:
        vals = [int(x, 0) for x in re.findall(r'0x[0-9A-Fa-f]+|\d+', r)]
        if len(vals) != 8:
            raise RuntimeError(f'bad format row: {r!r} -> {vals!r}')
        out.append(vals)
    if len(out) < 6:
        raise RuntimeError(f'expected at least 6 formats, parsed {len(out)}')
    return out


def fresh_uc(img):
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(BASE, (len(img) + 0xfff) & ~0xfff)
    uc.mem_write(BASE, img[:len(img)])
    uc.mem_map(STACK, 0x100000)
    write32(uc, PF_COUNT, 0)
    uc.mem_write(PF_TABLE, b'\0' * (16 * 0x44))
    return uc


def run_callback(uc, pf_values):
    """Emulate 0x50BC00(pf, ctx). Return (accepted, count_before, count_after, record)."""
    pfp = STACK_TOP - 0x200
    uc.mem_write(pfp, b''.join(int(v).to_bytes(4, 'little') for v in pf_values))
    uc.reg_write(UC_X86_REG_ESP, STACK_TOP - 0x100)
    write32(uc, STACK_TOP - 0x100 + 0, CB_RET)   # fake return address
    write32(uc, STACK_TOP - 0x100 + 4, pfp)      # arg1: DDPIXELFORMAT*
    write32(uc, STACK_TOP - 0x100 + 8, 0)        # arg2: ctx
    before = read32(uc, PF_COUNT)
    uc.emu_start(CB, CB_RET + 1, count=20000)
    after = read32(uc, PF_COUNT)
    rec = bytes(uc.mem_read(PF_TABLE + (before % 16) * 0x44, 0x44)).hex()
    return after > before, before, after, rec


def run_check(uc, pf_values):
    """Emulate the DDPF_RGB/bitcount check with a DDSURFACEDESC at ebp-0xb0."""
    ebp = STACK_TOP - 0x400
    desc = ebp - 0xb0
    uc.mem_write(desc, b'\0' * 0x7c)
    write32(uc, desc + 0, 0x7c)
    write32(uc, desc + 72 + 0, pf_values[0])
    for i, v in enumerate(pf_values):
        write32(uc, desc + 72 + i * 4, v)
    outcome = {'reached': False}
    def hook(_uc, addr, _size, _ud):
        if addr in (CHECK_OK, CHECK_ERR):
            outcome['addr'] = addr
            outcome['reached'] = True
            _uc.emu_stop()
    h = uc.hook_add(UC_HOOK_CODE, hook)
    uc.reg_write(UC_X86_REG_EBP, ebp)
    # 0x50B6E5 loads al from [ebp-0x64]; start at 0x50B6E5.
    uc.emu_start(CHECK, 0x50BD62, count=5000)
    uc.hook_del(h)
    return 'PASS' if outcome.get('addr') == CHECK_OK else (
        'FAIL' if outcome.get('addr') == CHECK_ERR else 'UNKNOWN')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    img, base = image_bytes()
    if base != BASE:
        print(json.dumps({'status': 'BLOCKED', 'reason': f'unexpected image base {base:#x}'}))
        return 2
    formats = game_formats()
    report = {
        'engine': 'unicorn',
        'unicorn_bytes_source': str(IGI),
        'image_base': hex(BASE),
        'callbacks': {
            'enum_texture_formats_callback': hex(CB),
            'check_region': f'{CHECK:#x}-{CHECK_OK:#x}',
            'error_string': 'Invalid texture format (0x54EAD8, pushed at 0x50B6EC / 0x50BB62)',
        },
        'game_formats_from': str(D3D7),
        'results': [],
    }
    for i, pf in enumerate(formats):
        uc = fresh_uc(img)
        try:
            accepted, before, after, rec = run_callback(uc, pf)
            uc2 = fresh_uc(img)
            check = run_check(uc2, pf)
            res = {
                'index': i,
                'ddpf': {
                    'dwSize': pf[0], 'dwFlags': hex(pf[1]), 'dwFourCC': pf[2],
                    'dwRGBBitCount': pf[3], 'dwRBitMask': hex(pf[4]),
                    'dwGBitMask': hex(pf[5]), 'dwBBitMask': hex(pf[6]),
                    'dwRGBAlphaBitMask': hex(pf[7]),
                },
                'guest_callback_accepted': accepted,
                'format_table_count_before': before,
                'format_table_count_after': after,
                'format_table_record_hex': rec,
                'guest_ddpf_check': check,
            }
        except Exception as e:
            res = {'index': i, 'status': 'BLOCKED', 'reason': f'{type(e).__name__}: {e}'}
        report['results'].append(res)
    txt = json.dumps(report, indent=2)
    print(txt)
    if args.out:
        Path(args.out).write_text(txt + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
