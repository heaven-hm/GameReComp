#!/usr/bin/env python3
"""GBX header reader for TrackMania assets (PE32 guest data, host-side tool).

Adapted from GBX.NET 2 `Src/GBX.NET/Components/GbxHeaderBasic.cs` and
`Src/GBX.NET/Serialization/GbxHeaderReader.cs` at commit
`8c839df5c5d22c61eee36272ed3997445de6a88c` (MIT licence for Src/GBX.NET).
Only the uncompressed header is parsed; the LZO-compressed body remains a
separate concern and is not claimed here. See docs/GBX_FORMAT_REUSE.md.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

MAGIC = b"GBX"
MAX_USER_DATA = 0x1000000        # 16 MiB, same limit as GBX.NET
EXPECTED_FORMATS = {b"B": "binary", b"T": "text"}
EXPECTED_COMPRESSION = {b"U": "uncompressed", b"C": "compressed"}
EXPECTED_MODES = {b"E": "editor", b"R": "release"}


class GbxError(ValueError):
    """Malformed or unsupported GBX header."""


def parse_header(data: bytes) -> dict:
    """Parse a GBX header; return a dict with offsets and validated fields."""
    if len(data) < 8 or data[:3] != MAGIC:
        raise GbxError("not a GBX file (magic)")
    version = struct.unpack_from("<H", data, 3)[0]
    fmt = data[5:6]
    if fmt not in EXPECTED_FORMATS:
        raise GbxError(f"unknown GBX format byte {fmt!r}")
    ref_comp = data[6:7]
    body_comp = data[7:8]
    if ref_comp not in EXPECTED_COMPRESSION or body_comp not in EXPECTED_COMPRESSION:
        raise GbxError("unknown GBX compression byte")

    off = 8
    mode = "editor"                      # versions before 4 omit the mode byte
    if version >= 4:
        if len(data) < off + 1:
            raise GbxError("truncated GBX mode byte")
        raw_mode = data[off:off + 1]
        if raw_mode not in EXPECTED_MODES:
            raise GbxError(f"invalid GBX serialization mode {raw_mode!r}")
        mode = EXPECTED_MODES[raw_mode]
        off += 1
    if len(data) < off + 4:
        raise GbxError("truncated GBX class id")
    class_id = struct.unpack_from("<I", data, off)[0]
    off += 4

    user_data_len = 0
    if version >= 6:
        if len(data) < off + 4:
            raise GbxError("truncated GBX user data length")
        user_data_len = struct.unpack_from("<i", data, off)[0]
        off += 4
        if user_data_len < 0:
            raise GbxError("negative GBX user data length")
        if user_data_len > MAX_USER_DATA:
            raise GbxError("GBX user data exceeds 16 MiB limit")
        if len(data) < off + user_data_len + 4:
            raise GbxError("truncated GBX user data / node count")
        off += user_data_len
    num_nodes = struct.unpack_from("<i", data, off)[0]
    off += 4
    if num_nodes < 0:
        raise GbxError("negative GBX node count")

    return {
        "magic": MAGIC.decode(),
        "version": version,
        "format": EXPECTED_FORMATS[fmt],
        "reference_table_compression": EXPECTED_COMPRESSION[ref_comp],
        "body_compression": EXPECTED_COMPRESSION[body_comp],
        "mode": mode,
        "class_id": f"0x{class_id:08X}",
        "user_data_bytes": user_data_len,
        "node_count": num_nodes,
        "body_offset": off,
        "input_sha256": hashlib.sha256(data).hexdigest(),
        "input_size": len(data),
    }


def parse_file(path: Path) -> dict:
    return parse_header(Path(path).read_bytes())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("files", nargs="+", type=Path)
    args = p.parse_args(argv)
    for path in args.files:
        try:
            info = parse_file(path)
        except (OSError, GbxError) as exc:
            print(f"{path}: ERROR: {exc}")
            return 1
        print(f"{path}: GBX v{info['version']} {info['format']} "
              f"class={info['class_id']} nodes={info['node_count']} "
              f"sha256={info['input_sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
