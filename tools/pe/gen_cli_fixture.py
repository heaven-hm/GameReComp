"""Deterministic PE32 + COR20 + CLI-metadata fixture generator.

No timestamps, no randomness, no compiler and no .NET SDK: the same invocation
always produces the same bytes. The fixture is just large enough to exercise
the structural checks in :mod:`tools.pe.cli_metadata` -- a native PE32 stub
whose COM descriptor points at a ``BSJB`` metadata root containing ``#~``,
``#Strings`` and ``#Blob`` streams. ``--variant`` produces malformed copies for
fail-closed tests.

Usage::

    python -m tools.pe.gen_cli_fixture --out /tmp/good.exe
    python -m tools.pe.gen_cli_fixture --variant bad-signature --out /tmp/bad.exe

Layout (fixed)::

    0x000 DOS header / e_lfanew = 0x80
    0x080 PE signature + COFF header
    0x098 PE32 optional header (0xE0 bytes), data dir 14 at 0x168
    0x178 one .text section header (VA 0x2000, raw 0x200)
    0x200 IMAGE_COR20_HEADER (0x48 bytes)
    0x300 metadata root; streams follow the headers inside the root
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

PE_OFF = 0x80
OPT_OFF = 0x98
OPT_SIZE = 0xE0
DD14_OFF = OPT_OFF + 96 + 14 * 8      # 0x168
SEC_OFF = OPT_OFF + OPT_SIZE          # 0x178
COR20_OFF = 0x200
COR20_RVA = 0x2000
MD_OFF = 0x300
MD_RVA = 0x2100
FILE_SIZE = 0x600
VERSION = b"v2.0.50727\x00"           # padded to 12 bytes below
VARIANTS = ("good", "bad-signature", "bad-stream-offset", "truncated",
            "missing-tables", "bad-cor-size", "bad-cor-cb", "no-com-dir")


def _padded(data: bytes, pad_to: int = 4) -> bytes:
    return data + b"\0" * ((-len(data)) % pad_to)


def build_metadata() -> bytes:
    """Build a minimal, structurally valid metadata root (BSJB)."""
    version = _padded(VERSION)
    tables = (struct.pack("<IBBBBQQI", 0, 2, 0, 0, 1, 0, 0, 0))
    strings = b"\0Class1\0Main\0\0"
    blob = b"\0\x02ab\0"
    streams = (("#~", tables), ("#Strings", strings), ("#Blob", blob))

    header_size = 16 + len(version) + 4
    headers = b""
    for name, _payload in streams:
        headers += struct.pack("<II", 0, 0) + _padded(name.encode("ascii") + b"\0")
    data_area = header_size + len(headers)
    offset = data_area
    body = b""
    stream_headers = b""
    for name, payload in streams:
        stream_headers += struct.pack("<II", offset, len(payload)) + \
            _padded(name.encode("ascii") + b"\0")
        body += payload
        offset += len(payload)
    return (struct.pack("<IHHII", 0x424A5342, 1, 1, 0, len(version)) + version +
            struct.pack("<HH", 0, len(streams)) + stream_headers + body)


def build_fixture(variant: str = "good") -> bytes:
    """Return deterministic fixture bytes; ``variant`` injects one fault."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; choose from {VARIANTS}")
    metadata = build_metadata()
    b = bytearray(FILE_SIZE)

    b[0:2] = b"MZ"
    struct.pack_into("<I", b, 0x3C, PE_OFF)
    b[PE_OFF:PE_OFF + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", b, PE_OFF + 4,
                     0x014C, 1, 0, 0, 0, OPT_SIZE, 0x0102)  # i386, executable
    struct.pack_into("<H", b, OPT_OFF, 0x10B)               # PE32
    struct.pack_into("<I", b, OPT_OFF + 28, 0x400000)       # ImageBase
    struct.pack_into("<I", b, OPT_OFF + 32, 0x2000)         # SectionAlignment
    struct.pack_into("<I", b, OPT_OFF + 36, 0x200)          # FileAlignment
    struct.pack_into("<I", b, OPT_OFF + 56, 0x5000)         # SizeOfImage
    struct.pack_into("<I", b, OPT_OFF + 60, 0x200)          # SizeOfHeaders
    struct.pack_into("<H", b, OPT_OFF + 68, 3)              # Subsystem: console
    struct.pack_into("<I", b, OPT_OFF + 92, 16)             # NumberOfRvaAndSizes
    struct.pack_into("<II", b, DD14_OFF, COR20_RVA, 0x48)   # COM descriptor

    b[SEC_OFF:SEC_OFF + 8] = b".text\0\0\0"
    struct.pack_into("<IIII", b, SEC_OFF + 8, 0x400, 0x2000, 0x400, 0x200)
    struct.pack_into("<I", b, SEC_OFF + 36, 0x60000020)      # CODE|EXEC|READ

    struct.pack_into("<IHHIIII", b, COR20_OFF,
                     0x48, 2, 5, MD_RVA, len(metadata), 1, 0x06000001)
    b[MD_OFF:MD_OFF + len(metadata)] = metadata

    if variant == "bad-signature":
        b[MD_OFF:MD_OFF + 4] = b"XXXX"
    elif variant == "bad-stream-offset":
        struct.pack_into("<I", b, MD_OFF + 32, 0xFFFFFFF0)
    elif variant == "truncated":
        return bytes(b[:MD_OFF + 20])
    elif variant == "missing-tables":
        b[MD_OFF + 40:MD_OFF + 42] = b"#?"
    elif variant == "bad-cor-size":
        struct.pack_into("<I", b, DD14_OFF + 4, 0x10)
    elif variant == "bad-cor-cb":
        struct.pack_into("<I", b, COR20_OFF, 0x20)
    elif variant == "no-com-dir":
        struct.pack_into("<II", b, DD14_OFF, 0, 0)
    return bytes(b)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="output path")
    ap.add_argument("--variant", choices=VARIANTS, default="good")
    args = ap.parse_args(argv)
    Path(args.out).write_bytes(build_fixture(args.variant))
    print(f"wrote {args.out} ({args.variant})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
