#!/usr/bin/env python3
"""Build the synthetic PE32 fixture used by the test suite and CI.

The fixture is a real, minimal Windows PE32 image: one code section, a small
import table against a DLL the win32hle runtime answers (KERNEL32), and a
program that calls one of them. It exists so the pipeline can be exercised
end-to-end -- inspect, recover, translate, project, compile, run, verify --
without shipping a proprietary game or depending on a corpus being present.

    python3 tests/fixtures/make_pe32_fixture.py            # rewrite the fixture
    python3 tests/fixtures/make_pe32_fixture.py --check    # fail if it drifted

The output is deterministic: same bytes for the same input every run, so the
checked-in file and its SHA-256 are reproducible.
"""

import argparse
import hashlib
import struct
import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "pe32-minimal.exe"

IMAGE_BASE = 0x00400000
SECTION_ALIGN = 0x1000
FILE_ALIGN = 0x200

# The program: a WinMain-shaped function that calls ExitProcess(0) and returns.
# 6A 00             push 0
# FF 15 <iat>       call dword ptr [ExitProcess]
# 33 C0             xor eax, eax
# C2 10 00          ret 0x10
CODE = bytes.fromhex("6A00FF15") + b"\x00\x00\x00\x00" + bytes.fromhex("33C0C21000")


def build_imports(code_rva, data_rva):
    """Return (import_dir_bytes, iat_rva, patch_offset) for KERNEL32!ExitProcess.

    The import descriptor, the import lookup table, the IAT and the two name
    strings all live in .rdata; the code's `call [mem]` is patched with the IAT
    entry's RVA once the layout is known.
    """
    name_rva = data_rva                     # "KERNEL32.dll"
    func_rva = data_rva + 0x20              # IMAGE_IMPORT_BY_NAME: hint 0 + "ExitProcess"
    ilt_rva = data_rva + 0x40               # import lookup table (one entry + 0)
    iat_rva = data_rva + 0x50               # import address table
    desc_rva = data_rva + 0x60              # one IMAGE_IMPORT_DESCRIPTOR + null

    data = bytearray(0x100)
    data[0x00:0x00 + 13] = b"KERNEL32.dll\x00"

    # Hint/name: hint 0, then the name, then padding to even.
    data[0x20:0x22] = b"\x00\x00"
    data[0x22:0x22 + 12] = b"ExitProcess\x00"

    # ILT: one hint/name entry, then the null terminator.
    struct.pack_into("<I", data, 0x40, func_rva)
    struct.pack_into("<I", data, 0x44, 0)
    # IAT: the loader writes the resolved VA here; the file value mirrors the ILT.
    struct.pack_into("<I", data, 0x50, func_rva)
    struct.pack_into("<I", data, 0x54, 0)

    # IMAGE_IMPORT_DESCRIPTOR: OriginalFirstThunk, TimeDateStamp, ForwarderChain,
    # Name, FirstThunk. A second all-zero descriptor terminates the array.
    struct.pack_into("<IIIII", data, 0x60, ilt_rva, 0, 0, name_rva, iat_rva)
    struct.pack_into("<IIIII", data, 0x74, 0, 0, 0, 0, 0)

    assert desc_rva == data_rva + 0x60
    return bytes(data), desc_rva, iat_rva


def build_pe():
    headers_size = 0x200
    text_rva = SECTION_ALIGN
    rdata_rva = text_rva + SECTION_ALIGN

    code = bytearray(CODE)
    rdata, import_desc_rva, iat_rva = build_imports(text_rva, rdata_rva)
    # Patch the `call dword ptr [iat]` displacement with the IAT entry's VA.
    struct.pack_into("<I", code, 4, IMAGE_BASE + iat_rva)
    code = bytes(code)

    text_raw = FILE_ALIGN
    rdata_raw = text_raw + FILE_ALIGN
    size_of_image = rdata_rva + SECTION_ALIGN
    entry = IMAGE_BASE + text_rva

    out = bytearray(headers_size)

    # ---- DOS header + stub ----
    out[0:2] = b"MZ"
    struct.pack_into("<H", out, 2, 0x90)          # bytes on last page
    struct.pack_into("<H", out, 4, 3)             # pages
    struct.pack_into("<H", out, 8, 4)             # header paragraphs
    struct.pack_into("<H", out, 0x18, 0x40)       # e_lfarlc
    struct.pack_into("<I", out, 0x3C, 0x80)       # e_lfanew
    out[0x40:0x40 + 4] = b"\x0E\x1F\xBA\x0E"      # push cs; pop ds; mov dx, ...
    out[0x40 + 4:0x40 + 6] = b"\x00\xB4"
    out[0x40 + 6:0x40 + 8] = b"\x09\xCD"
    out[0x40 + 8:0x40 + 10] = b"\x21\xB8"
    out[0x40 + 10:0x40 + 12] = b"\x01\x4C"
    out[0x40 + 12:0x40 + 14] = b"\xCD\x21"
    out[0x40 + 14:0x40 + 16] = b"This program cannot be run in DOS mode.\r\r\n$"

    # ---- PE signature + COFF header ----
    pe_off = 0x80
    out[pe_off:pe_off + 4] = b"PE\x00\x00"
    coff = pe_off + 4
    struct.pack_into("<H", out, coff + 0, 0x014C)   # IMAGE_FILE_MACHINE_I386
    struct.pack_into("<H", out, coff + 2, 2)        # NumberOfSections
    struct.pack_into("<I", out, coff + 4, 0x5F3759DF)  # fixed timestamp
    struct.pack_into("<H", out, coff + 16, 0x00E0)  # SizeOfOptionalHeader
    struct.pack_into("<H", out, coff + 18, 0x0102)  # EXECUTABLE_IMAGE | 32BIT

    # ---- Optional header (PE32) ----
    opt = coff + 20
    struct.pack_into("<H", out, opt + 0, 0x010B)    # PE32
    out[opt + 2] = 6                                # linker 6.0, as a 1998 toolchain
    out[opt + 3] = 0
    struct.pack_into("<I", out, opt + 4, len(code))         # SizeOfCode
    struct.pack_into("<I", out, opt + 8, len(rdata))        # SizeOfInitializedData
    struct.pack_into("<I", out, opt + 16, text_rva)         # AddressOfEntryPoint
    struct.pack_into("<I", out, opt + 20, text_rva)         # BaseOfCode
    struct.pack_into("<I", out, opt + 24, rdata_rva)        # BaseOfData
    struct.pack_into("<I", out, opt + 28, IMAGE_BASE)       # ImageBase
    struct.pack_into("<I", out, opt + 32, SECTION_ALIGN)    # SectionAlignment
    struct.pack_into("<I", out, opt + 36, FILE_ALIGN)       # FileAlignment
    struct.pack_into("<H", out, opt + 40, 4)                # MajorOperatingSystemVersion
    struct.pack_into("<H", out, opt + 48, 4)                # MajorSubsystemVersion
    struct.pack_into("<I", out, opt + 56, size_of_image)    # SizeOfImage
    struct.pack_into("<I", out, opt + 60, headers_size)     # SizeOfHeaders
    struct.pack_into("<H", out, opt + 68, 2)                # IMAGE_SUBSYSTEM_WINDOWS_GUI
    struct.pack_into("<I", out, opt + 72, 0x00100000)       # SizeOfStackReserve
    struct.pack_into("<I", out, opt + 76, 0x00001000)       # SizeOfStackCommit
    struct.pack_into("<I", out, opt + 80, 0x00100000)       # SizeOfHeapReserve
    struct.pack_into("<I", out, opt + 84, 0x00001000)       # SizeOfHeapCommit
    struct.pack_into("<I", out, opt + 92, 16)               # NumberOfRvaAndSizes
    struct.pack_into("<II", out, opt + 96 + 8,
                     import_desc_rva, 0x60)                  # import dir

    # ---- Section table ----
    sec = opt + 0x00E0
    struct.pack_into("<8sIIIIIIHHI", out, sec,
                     b".text", len(code), text_rva, FILE_ALIGN, text_raw,
                     0, 0, 0, 0, 0x60000020)      # CODE | EXECUTE | READ
    struct.pack_into("<8sIIIIIIHHI", out, sec + 40,
                     b".rdata", len(rdata), rdata_rva, FILE_ALIGN, rdata_raw,
                     0, 0, 0, 0, 0x40000040)     # INITIALIZED_DATA | READ

    # ---- Section bodies ----
    out.extend(b"\x00" * (text_raw - len(out)))
    out[text_raw:text_raw + len(code)] = code
    out.extend(b"\x00" * (rdata_raw - len(out)))
    out[rdata_raw:rdata_raw + len(rdata)] = rdata
    return bytes(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify the checked-in fixture matches; do not write")
    parser.add_argument("--out", default=str(FIXTURE))
    args = parser.parse_args(argv)

    image = build_pe()
    digest = hashlib.sha256(image).hexdigest()
    target = Path(args.out)

    if args.check:
        if not target.exists():
            print(f"missing fixture: {target}", file=sys.stderr)
            return 1
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != digest:
            print(f"fixture drifted: {target}\n  on disk {actual}\n  expected {digest}",
                  file=sys.stderr)
            return 1
        print(f"ok {target} sha256 {digest} ({len(image)} bytes)")
        return 0

    target.write_bytes(image)
    print(f"wrote {target} sha256 {digest} ({len(image)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
