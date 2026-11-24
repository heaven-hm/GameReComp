#!/usr/bin/env python3
"""Build the deterministic PE32 / PE32+ fixtures used by the API-surface tests.

Each fixture is a small, valid PE image carrying all the directories the
auditor reads: an import table (two by name, one by ordinal), an export table
(two named symbols), a TLS directory with three callbacks, and a non-writable
.text plus a writable .data section. One fixture is PE32/i386, the other is
PE32+/x86-64 -- the latter is exactly what tools/pe/extract_imports.py cannot
read today.

    python3 tests/fixtures/make_pe_api_fixture.py            # rewrite fixtures
    python3 tests/fixtures/make_pe_api_fixture.py --check    # verify SHA-256s
"""

import argparse
import hashlib
from pathlib import Path
import struct
import sys

HERE = Path(__file__).resolve().parent
FIXTURES = {
    "pe32-api.dll": False,
    "pe64-api.dll": True,
}

BASE_RVA = 0x1000
RAW_PTR = 0x200
DATA_RVA = 0x2000
DATA_RAW = 0x800

IMPORTS = ("ExitProcess", "GetCurrentProcessId")
EXPORTS = ("CreateFileA", "GetTickCount")


def _cstr(value):
    return value.encode("ascii") + b"\0"


def build_pe(pe64=False):
    """Return the bytes of a deterministic PE32 or PE32+ test image."""
    ptr = 8 if pe64 else 4
    machine = 0x8664 if pe64 else 0x014C
    magic = 0x20B if pe64 else 0x10B
    opt_size = 0xF0 if pe64 else 0xE0
    characteristics = 0x2022 if pe64 else 0x0102

    image = bytearray(0x200)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    coff = 0x84
    struct.pack_into("<HHIIIHH", image, coff, machine, 2, 0x5F3759DF, 0, 0,
                     opt_size, characteristics)
    opt = coff + 20
    struct.pack_into("<H", image, opt, magic)
    image[opt + 2] = 6  # linker version
    struct.pack_into("<I", image, opt + 16, BASE_RVA)  # AddressOfEntryPoint
    if pe64:
        struct.pack_into("<Q", image, opt + 24, 0x140000000)  # ImageBase
        struct.pack_into("<I", image, opt + 32, 0x1000)       # SectionAlignment
        struct.pack_into("<I", image, opt + 36, 0x200)        # FileAlignment
        struct.pack_into("<I", image, opt + 56, 0x3000)       # SizeOfImage
        struct.pack_into("<I", image, opt + 60, 0x200)        # SizeOfHeaders
        struct.pack_into("<H", image, opt + 68, 3)            # CUI subsystem
        struct.pack_into("<I", image, opt + 108, 16)          # NumberOfRvaAndSizes
        dd = opt + 112
    else:
        struct.pack_into("<I", image, opt + 28, 0x00400000)   # ImageBase
        struct.pack_into("<I", image, opt + 32, 0x1000)
        struct.pack_into("<I", image, opt + 36, 0x200)
        struct.pack_into("<I", image, opt + 56, 0x3000)
        struct.pack_into("<I", image, opt + 60, 0x200)
        struct.pack_into("<H", image, opt + 68, 3)
        struct.pack_into("<I", image, opt + 92, 16)
        dd = opt + 96
    # Data directories: 0 = export, 1 = import, 9 = TLS.
    struct.pack_into("<II", image, dd + 0, BASE_RVA + 0x200, 0x100)
    struct.pack_into("<II", image, dd + 8, BASE_RVA + 0x100, 0x40)
    struct.pack_into("<II", image, dd + 72, BASE_RVA + 0x300, 0x40)

    sec = opt + opt_size
    image[sec:sec + 8] = b".text\0\0\0"
    struct.pack_into("<IIII", image, sec + 8, 0x400, BASE_RVA, 0x400, RAW_PTR)
    struct.pack_into("<I", image, sec + 36, 0x60000020)  # CODE|EXECUTE|READ
    sec2 = sec + 40
    image[sec2:sec2 + 8] = b".data\0\0\0"
    struct.pack_into("<IIII", image, sec2 + 8, 0x200, DATA_RVA, 0x200, DATA_RAW)
    struct.pack_into("<I", image, sec2 + 36, 0xC0000040)  # INITIALIZED_DATA|READ|WRITE

    body = bytearray(0x400)

    # ---- import directory at RVA +0x100 ------------------------------------
    body[0x00:0x0D] = _cstr("KERNEL32.dll")
    hint_names = {}
    cursor = 0x40
    for name in IMPORTS:
        hint_names[name] = cursor
        struct.pack_into("<H", body, cursor, 0)
        body[cursor + 2:cursor + 2 + len(name) + 1] = _cstr(name)
        cursor += 0x20
    # ILT and IAT: name, name, ordinal 42, terminator.
    for table in (0x80, 0xC0):
        for i, name in enumerate(IMPORTS):
            struct.pack_into("<Q" if pe64 else "<I", body, table + i * ptr,
                             BASE_RVA + hint_names[name])
        struct.pack_into("<Q" if pe64 else "<I", body, table + 2 * ptr,
                         (1 << (ptr * 8 - 1)) | 42)
        struct.pack_into("<Q" if pe64 else "<I", body, table + 3 * ptr, 0)
    # One IMAGE_IMPORT_DESCRIPTOR + null descriptor.
    struct.pack_into("<IIIII", body, 0x100, BASE_RVA + 0x80, 0, 0,
                     BASE_RVA + 0x00, BASE_RVA + 0xC0)
    struct.pack_into("<IIIII", body, 0x114, 0, 0, 0, 0, 0)

    # ---- export directory at RVA +0x200 ------------------------------------
    struct.pack_into("<IIHHIIIIIII", body, 0x200,
                     0, 0x5F3759DF, 1, 0, BASE_RVA + 0x2A0, 1,
                     len(EXPORTS), len(EXPORTS),
                     BASE_RVA + 0x240, BASE_RVA + 0x260, BASE_RVA + 0x280)
    for i in range(len(EXPORTS)):
        struct.pack_into("<I", body, 0x240 + i * 4, BASE_RVA + 0x400 + i * 4)
    # AddressOfNames entries point straight at NUL-terminated names (unlike
    # import-by-name entries, export names have no 2-byte hint).
    export_names = {}
    cursor = 0x2C0
    for name in EXPORTS:
        export_names[name] = cursor
        body[cursor:cursor + len(name) + 1] = _cstr(name)
        cursor += 0x20
    for i, name in enumerate(EXPORTS):
        struct.pack_into("<I", body, 0x260 + i * 4, BASE_RVA + export_names[name])
        struct.pack_into("<H", body, 0x280 + i * 2, i)
    body[0x2A0:0x2A0 + len("api32.dll") + 1] = _cstr("api32.dll")

    # ---- TLS at RVA +0x300 with three callbacks + terminator ----------------
    struct.pack_into("<" + ("Q" if pe64 else "I") * 2, body, 0x300,
                     BASE_RVA + 0x1000, BASE_RVA + 0x1000)
    callbacks_field = 24 if pe64 else 12
    struct.pack_into("<Q" if pe64 else "<I", body, 0x300 + callbacks_field,
                     BASE_RVA + 0x340)
    for i in range(3):
        struct.pack_into("<Q" if pe64 else "<I", body, 0x340 + i * ptr,
                         BASE_RVA + 0x100 + i)
    struct.pack_into("<Q" if pe64 else "<I", body, 0x340 + 3 * ptr, 0)

    image.extend(b"\0" * (RAW_PTR - len(image)))
    image.extend(body)
    image.extend(b"\0" * (DATA_RAW - len(image)))
    image.extend(b"writable\0")
    return bytes(image)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    failed = False
    for name, pe64 in FIXTURES.items():
        target = HERE / name
        image = build_pe(pe64)
        digest = hashlib.sha256(image).hexdigest()
        if args.check:
            ok = target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == digest
            print(f"{'ok' if ok else 'MISMATCH'} {name} sha256 {digest} ({len(image)} bytes)")
            failed |= not ok
        else:
            target.write_bytes(image)
            print(f"wrote {name} sha256 {digest} ({len(image)} bytes)")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
