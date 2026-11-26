#!/usr/bin/env python3
"""Deterministic synthetic PE32 / NE / PE32+ fixtures for the compatibility gate.

These are *metadata fixtures*, not runnable games and not commercial binaries.
They encode one guest DLL/import (and, for COM families, a method slot label) so
the inventory can be generated and re-checked on any host without a corpus. Every
builder is pure and deterministic: the same inputs always produce the same bytes
and therefore the same SHA-256. Supported and unsupported families use exactly
the same generator; only the import label differs, so a missing HLE handler can
never be reported as PASS by accident.

The PE32 layout deliberately mirrors tests/fixtures/make_pe32_fixture.py so the
existing checked-in fixture remains the canonical supported case.
"""

import hashlib
import struct

IMAGE_BASE = 0x00400000
SECTION_ALIGN = 0x1000
FILE_ALIGN = 0x200


def _fit(text, size):
    raw = text.encode("ascii", "replace")
    return raw[:size - 1] + b"\x00"


def build_pe32_import(dll, func):
    """Return a minimal PE32 image importing dll!func."""
    code = bytearray(bytes.fromhex("6A00FF15") + b"\x00\x00\x00\x00" +
                     bytes.fromhex("33C0C21000"))
    text_rva = SECTION_ALIGN
    rdata_rva = text_rva + SECTION_ALIGN
    iat_rva = rdata_rva + 0x50
    desc_rva = rdata_rva + 0x60
    func_rva = rdata_rva + 0x20

    rdata = bytearray(0x100)
    rdata[0x00:0x10] = _fit(dll, 16)
    rdata[0x20:0x22] = b"\x00\x00"
    rdata[0x22:0x30] = _fit(func, 14)
    struct.pack_into("<II", rdata, 0x40, func_rva, 0)   # ILT
    struct.pack_into("<II", rdata, 0x50, func_rva, 0)   # IAT
    struct.pack_into("<IIIII", rdata, 0x60, rdata_rva + 0x40, 0, 0,
                     rdata_rva, iat_rva)               # import descriptor
    struct.pack_into("<IIIII", rdata, 0x74, 0, 0, 0, 0, 0)
    struct.pack_into("<I", code, 4, IMAGE_BASE + iat_rva)

    out = bytearray(0x200)
    out[0:2] = b"MZ"
    struct.pack_into("<H", out, 2, 0x90)
    struct.pack_into("<H", out, 4, 3)
    struct.pack_into("<H", out, 8, 4)
    struct.pack_into("<H", out, 0x18, 0x40)
    struct.pack_into("<I", out, 0x3C, 0x80)
    out[0x80:0x84] = b"PE\x00\x00"
    coff = 0x84
    struct.pack_into("<HHIIIHH", out, coff, 0x014C, 2, 0x5F3759DF, 0, 0,
                     0x00E0, 0x0102)
    opt = coff + 20
    struct.pack_into("<H", out, opt, 0x010B)
    out[opt + 2] = 6
    struct.pack_into("<I", out, opt + 4, len(code))
    struct.pack_into("<I", out, opt + 8, len(rdata))
    struct.pack_into("<I", out, opt + 16, text_rva)
    struct.pack_into("<I", out, opt + 20, text_rva)
    struct.pack_into("<I", out, opt + 24, rdata_rva)
    struct.pack_into("<I", out, opt + 28, IMAGE_BASE)
    struct.pack_into("<I", out, opt + 32, SECTION_ALIGN)
    struct.pack_into("<I", out, opt + 36, FILE_ALIGN)
    struct.pack_into("<H", out, opt + 40, 4)
    struct.pack_into("<H", out, opt + 48, 4)
    struct.pack_into("<I", out, opt + 56, rdata_rva + SECTION_ALIGN)
    struct.pack_into("<I", out, opt + 60, 0x200)
    struct.pack_into("<H", out, opt + 68, 2)
    struct.pack_into("<I", out, opt + 92, 16)
    struct.pack_into("<II", out, opt + 96 + 8, desc_rva, 0x60)
    sec = opt + 0x00E0
    struct.pack_into("<8sIIIIIIHHI", out, sec, b".text", len(code), text_rva,
                     FILE_ALIGN, FILE_ALIGN, 0, 0, 0, 0, 0x60000020)
    struct.pack_into("<8sIIIIIIHHI", out, sec + 40, b".rdata", len(rdata),
                     rdata_rva, FILE_ALIGN, 2 * FILE_ALIGN, 0, 0, 0, 0,
                     0x40000040)
    out.extend(b"\x00" * (FILE_ALIGN - len(out)))
    out.extend(code)
    out.extend(b"\x00" * (2 * FILE_ALIGN - len(out)))
    out.extend(rdata)
    return bytes(out)


def build_ne(module, dll, func):
    """Return a minimal NE image whose module/import names are self-describing."""
    out = bytearray(0x200)
    out[0:2] = b"MZ"
    struct.pack_into("<H", out, 2, 0x90)
    struct.pack_into("<H", out, 4, 3)
    struct.pack_into("<H", out, 8, 4)
    struct.pack_into("<I", out, 0x3C, 0x40)
    out[0x40:0x42] = b"NE"
    struct.pack_into("<HH", out, 0x42, 2, 0)      # version 2.0
    struct.pack_into("<HH", out, 0x44, 0x60, 0x10)  # entry table offset/length
    struct.pack_into("<H", out, 0x4C, 3)           # flags: protected mode
    struct.pack_into("<HH", out, 0x54, 0, 0)       # auto data seg / heap size
    struct.pack_into("<H", out, 0x58, 0x0100)      # initial stack
    struct.pack_into("<H", out, 0x5A, 0x0100)      # minimum stack
    out[0x80:0x90] = _fit(module, 16)
    out[0xA0:0xB0] = _fit(dll, 16)
    out[0xC0:0xD0] = _fit(func, 16)
    return bytes(out)


def build_pe32plus_import(dll, func):
    """Return a minimal PE32+ header carrying one import label."""
    rdata_rva = 0x2000
    iat_rva = rdata_rva + 0x50
    desc_rva = rdata_rva + 0x60
    func_rva = rdata_rva + 0x20
    rdata = bytearray(0x100)
    rdata[0x00:0x10] = _fit(dll, 16)
    rdata[0x20:0x22] = b"\x00\x00"
    rdata[0x22:0x30] = _fit(func, 14)
    struct.pack_into("<QQ", rdata, 0x40, func_rva, 0)
    struct.pack_into("<QQ", rdata, 0x50, func_rva, 0)
    struct.pack_into("<II", rdata, 0x60, rdata_rva + 0x40, 0)
    struct.pack_into("<II", rdata, 0x68, 0, rdata_rva)
    struct.pack_into("<II", rdata, 0x6C, iat_rva, 0)

    out = bytearray(0x200)
    out[0:2] = b"MZ"
    struct.pack_into("<I", out, 0x3C, 0x80)
    out[0x80:0x84] = b"PE\x00\x00"
    coff = 0x84
    struct.pack_into("<HHIIIHH", out, coff, 0x8664, 1, 0x5F3759DF, 0, 0,
                     0x00F0, 0x0022)
    opt = coff + 20
    struct.pack_into("<H", out, opt, 0x020B)
    out[opt + 2] = 6
    struct.pack_into("<I", out, opt + 16, 0x1000)     # AddressOfEntryPoint
    struct.pack_into("<I", out, opt + 20, 0x1000)     # BaseOfCode
    struct.pack_into("<Q", out, opt + 24, 0x140000000)  # ImageBase
    struct.pack_into("<I", out, opt + 32, SECTION_ALIGN)
    struct.pack_into("<I", out, opt + 36, FILE_ALIGN)
    struct.pack_into("<H", out, opt + 40, 4)
    struct.pack_into("<H", out, opt + 48, 4)
    struct.pack_into("<I", out, opt + 56, rdata_rva + SECTION_ALIGN)
    struct.pack_into("<I", out, opt + 60, 0x200)
    struct.pack_into("<H", out, opt + 68, 3)          # Windows CUI
    struct.pack_into("<I", out, opt + 108, 16)
    struct.pack_into("<II", out, opt + 112 + 8, desc_rva, 0x70)
    sec = opt + 0x00F0
    struct.pack_into("<8sIIIIIIHHI", out, sec, b".text", 0x10, 0x1000,
                     FILE_ALIGN, FILE_ALIGN, 0, 0, 0, 0, 0x60000020)
    struct.pack_into("<8sIIIIIIHHI", out, sec + 40, b".rdata", len(rdata),
                     rdata_rva, FILE_ALIGN, 2 * FILE_ALIGN, 0, 0, 0, 0,
                     0x40000040)
    out.extend(b"\x00" * (FILE_ALIGN - len(out)))
    out.extend(b"\xC3")
    out.extend(b"\x00" * (2 * FILE_ALIGN - len(out)))
    out.extend(rdata)
    return bytes(out)


def build_fixture(kind, dll, func, module=None):
    """Build fixture bytes for a kind from the compatibility baseline."""
    if kind == "fixture-pe32":
        return build_pe32_import(dll, func)
    if kind == "fixture-ne":
        return build_ne(module or dll, dll, func)
    if kind == "fixture-pe32plus":
        return build_pe32plus_import(dll, func)
    raise ValueError("unsupported fixture kind: " + str(kind))


def sha256(data):
    return hashlib.sha256(data).hexdigest()
