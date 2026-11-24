#!/usr/bin/env python3
"""Fail-closed Windows-application compatibility probe.

This tool does NOT claim that any Windows application can be converted. It
classifies a binary from its own headers and reports the exact class the
GameReComp PE32 pipeline accepts, and the exact class it refuses. A verdict of
SUPPORTED_CANDIDATE means only "PE32 x86 native, structurally eligible for the
pipeline" -- never "works", "renders" or "100% compatible".

Usage: python3 -m tools.app_compat_probe <file> [<file> ...] [--json OUT]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


MACHINE_X86 = 0x14C
MACHINE_X64 = 0x8664
PE32_MAGIC = 0x10B
PE32PLUS_MAGIC = 0x20B
COM_DESCRIPTOR_RVA = 14                  # optional-header data directory index


def classify(path: Path) -> dict:
    result = {
        'path': str(path),
        'name': path.name,
        'size': path.stat().st_size,
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'verdict': 'UNSUPPORTED',
        'reason': '',
    }
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        result['reason'] = 'not a DOS/PE executable (no MZ magic)'
        return result
    e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
    if e_lfanew + 24 > len(data):
        result['reason'] = 'DOS header points past the file'
        return result
    result['pe_signature'] = data[e_lfanew:e_lfanew + 4].decode('latin-1')
    if data[e_lfanew:e_lfanew + 4] != b'PE\0\0':
        ne = struct.unpack_from('<H', data, 0x18)[0] if len(data) > 0x1A else 0
        result['family'] = 'Win16/NE' if ne else 'non-PE MZ'
        result['reason'] = ('Win16/NE is roadmap-DEFERRED (issue #8), not supported'
                            if ne else 'MZ executable without a PE header')
        return result
    coff = e_lfanew + 4
    machine = struct.unpack_from('<H', data, coff)[0]
    n_sections = struct.unpack_from('<H', data, coff + 2)[0]
    opt_size = struct.unpack_from('<H', data, coff + 16)[0]
    opt = coff + 20
    if opt_size < 96 or opt + opt_size > len(data):
        result['reason'] = 'truncated PE optional header'
        return result
    magic = struct.unpack_from('<H', data, opt)[0]
    subsystem = struct.unpack_from('<H', data, opt + 68)[0] if opt_size >= 70 else 0
    result.update({'machine': f'0x{machine:04X}', 'pe_magic': f'0x{magic:04X}',
                   'sections': n_sections, 'subsystem': subsystem})

    # Managed binaries are not native x86 code: the IAT contains no Win32
    # imports to bind, and this pipeline has no CLR runtime.
    dd_off = opt + (96 if magic == PE32_MAGIC else 112)
    managed = False
    if dd_off + (COM_DESCRIPTOR_RVA + 1) * 8 <= len(data):
        clr_rva, clr_size = struct.unpack_from('<II', data,
                                              dd_off + COM_DESCRIPTOR_RVA * 8)
        managed = bool(clr_rva and clr_size)
    result['managed_clr'] = managed

    if machine == MACHINE_X64 or magic == PE32PLUS_MAGIC:
        result['family'] = 'PE32+/x64'
        result['reason'] = ('x64 is explicitly refused by the pipeline '
                            '(issue #13 DEFERRED); no x64 loader/lifter/ABI')
    elif managed:
        result['family'] = 'PE32/.NET'
        result['reason'] = 'managed CLR assembly: no native Win32 import table to reconstruct'
    elif machine != MACHINE_X86 or magic != PE32_MAGIC:
        result['family'] = 'other PE machine'
        result['reason'] = f'unsupported machine 0x{machine:04X} / magic 0x{magic:04X}'
    else:
        result['family'] = 'PE32/x86 native'
        result['verdict'] = 'SUPPORTED_CANDIDATE'
        result['reason'] = ('structurally eligible only: import binding, lift accuracy, '
                            'graphics/audio and gameplay still require per-app evidence')
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('files', nargs='+', type=Path)
    ap.add_argument('--json', type=Path)
    args = ap.parse_args(argv)
    rows = []
    for path in args.files:
        if not path.is_file():
            rows.append({'path': str(path), 'verdict': 'BLOCKED', 'reason': 'file missing'})
            continue
        rec = classify(path)
        rows.append(rec)
        print(f"{rec['name'][:34]:34} {rec['verdict']:20} "
              f"{rec.get('family', '-'):18} {rec['reason'][:80]}")
    if args.json:
        args.json.write_text(json.dumps({'schema': 1,
            'not_a_compatibility_claim': True,
            'results': rows}, indent=2))
        print(f'wrote {args.json}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
