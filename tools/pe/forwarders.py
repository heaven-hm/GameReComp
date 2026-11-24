#!/usr/bin/env python3
"""
forwarders.py - report a PE's exported functions and its forwarded exports.

An export table normally holds a code RVA. When that RVA falls inside the
export data directory it is instead the address of a NUL-terminated string,
"DLL.Function" or "DLL.#Ordinal": the Windows loader resolves the name in the
named DLL. MSVC CRT DLLs and the api-ms-win-* layer forward almost everything
this way, so a loader that ignores forwarders answers GetProcAddress with 0
for a function it could otherwise shim.

Pure-struct parser: the export directory is data directory entry 0, and the
file offset of its RVAs comes from the section headers. No pefile dependency
and no hardcoded file offsets.

Usage:
    python forwarders.py <file.exe>
"""
import struct
import sys

EXPORT_DIR = 0


def _r16(data, off):
    if off is None or off < 0 or off + 2 > len(data):
        return 0
    return struct.unpack_from('<H', data, off)[0]


def _r32(data, off):
    if off is None or off < 0 or off + 4 > len(data):
        return 0
    return struct.unpack_from('<I', data, off)[0]


def _sections(data, start, count):
    out = []
    for i in range(min(count, 96)):
        o = start + i * 40
        if o + 40 > len(data):
            break
        name = data[o:o + 8].rstrip(b'\x00').decode('ascii', errors='replace')
        vsize, va, raw_size, raw_off = struct.unpack_from('<IIII', data, o + 8)
        out.append({'name': name, 'va': va, 'vsize': vsize,
                    'raw_size': raw_size, 'raw_off': raw_off})
    return out


def _rva_to_off(rva, sections):
    for s in sections:
        if s['va'] <= rva < s['va'] + max(s['vsize'], s['raw_size']):
            return s['raw_off'] + (rva - s['va'])
    return None


def _read_cstr(data, off, maxlen=512):
    if off is None or off < 0 or off >= len(data):
        return ''
    end = data.find(b'\x00', off, min(off + maxlen, len(data)))
    if end == -1:
        end = min(off + maxlen, len(data))
    return data[off:end].decode('utf-8', errors='replace')


def parse_exports(data):
    """Parse the PE32/PE32+ export directory.

    Returns a list of dicts sorted by ordinal:
      ordinal, name (str or None), rva, forwarded (bool),
      target_dll / target_name / target_ordinal for a forwarder.
    An empty list means no usable export directory, never a guessed one.
    """
    try:
        if data[0:2] != b'MZ':
            return []
        pe = _r32(data, 0x3C)
        if data[pe:pe + 4] != b'PE\x00\x00':
            return []
        nsections = _r16(data, pe + 6)
        opt_size = _r16(data, pe + 20)
        opt = pe + 24
        magic = _r16(data, opt)
        plus = magic == 0x20B
        if magic not in (0x10B, 0x20B):
            return []
        nrva_off = opt + (108 if plus else 92)
        dd = opt + (112 if plus else 96)
        if _r32(data, nrva_off) <= EXPORT_DIR:
            return []
        exp_rva, exp_size = _r32(data, dd), _r32(data, dd + 4)
        if not exp_rva:
            return []
        sections = _sections(data, opt + opt_size, nsections)
        off = _rva_to_off(exp_rva, sections)
        if off is None:
            return []

        first = _r32(data, off + 16)
        nfunc, nnames = _r32(data, off + 20), _r32(data, off + 24)
        aof, aon, aords = _r32(data, off + 28), _r32(data, off + 32), _r32(data, off + 36)
        if not nfunc or not aof:
            return []

        names = {}
        for i in range(min(nnames, 0x10000)):
            name_off = _rva_to_off(_r32(data, _rva_to_off(aon + 4 * i, sections)), sections)
            idx = _r16(data, _rva_to_off(aords + 2 * i, sections))
            names[idx] = _read_cstr(data, name_off)

        out = []
        for idx in range(min(nfunc, 0x10000)):
            frva = _r32(data, _rva_to_off(aof + 4 * idx, sections))
            if not frva:
                continue
            row = {'ordinal': first + idx, 'name': names.get(idx), 'rva': frva,
                   'forwarded': False, 'target_dll': None,
                   'target_name': None, 'target_ordinal': None}
            if exp_rva <= frva < exp_rva + exp_size:
                text = _read_cstr(data, _rva_to_off(frva, sections))
                dot = text.rfind('.')
                if dot > 0:
                    dll, symbol = text[:dot], text[dot + 1:]
                    row['forwarded'] = True
                    row['target_dll'] = dll
                    if symbol.startswith('#') and symbol[1:].isdigit():
                        row['target_ordinal'] = int(symbol[1:])
                    else:
                        row['target_name'] = symbol
            out.append(row)
        return out
    except (struct.error, IndexError, ValueError, TypeError):
        return []


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) < 2:
        print(f"Usage: {argv[0]} <file.exe>")
        return 1
    with open(argv[1], 'rb') as f:
        exports = parse_exports(f.read())
    if not exports:
        print("No usable export directory in this PE.")
        return 0
    fwd = sum(1 for e in exports if e['forwarded'])
    print(f"Exports: {len(exports)} ({fwd} forwarded)")
    for e in exports:
        label = f"[{e['ordinal']:5d}] {e['name'] or '(unnamed)':32s}"
        if e['forwarded']:
            target = f"#{e['target_ordinal']}" if e['target_ordinal'] is not None else e['target_name']
            print(f"{label} -> {e['target_dll']}.{target}")
        else:
            print(f"{label} @ RVA 0x{e['rva']:08X}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
