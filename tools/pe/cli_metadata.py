"""Fail-closed detection and description of CLI/.NET PE images (ECMA-335 II.25).

This module **detects** managed assemblies: the COR20 (COM descriptor) data
directory, the CLI header, the metadata root (``BSJB``) and its streams
(``#~``/``#-``, ``#Strings``, ``#Blob``, ...). It does **not** decode IL, and it
does not translate, compile, or claim to recompile anything. A managed input is
reported as ``UNSUPPORTED_NOT_TESTED``; a malformed one is rejected with a
reason. Nothing here turns an untested format into a success.

Layout references (all bounds-checked, no native parsing libraries):

* PE/COFF: DOS ``e_lfanew`` -> ``PE\\0\\0`` -> COFF header -> optional header
  -> data directory 14 (``IMAGE_DIRECTORY_ENTRY_COM_DESCRIPTOR``).
* CLI header (``IMAGE_COR20_HEADER``), 0x48 bytes: cb, MajorRuntimeVersion,
  MinorRuntimeVersion, MetaData (RVA/size), Flags, EntryPointToken/RVA and six
  further RVA/size pairs.
* Metadata root: ``BSJB`` (0x424A5342), major/minor, reserved, version length,
  version string, flags, stream count, then (offset, size, NUL-terminated
  name padded to 4) stream headers. Stream offsets are relative to the root.

Usage::

    python -m tools.pe.cli_metadata <file-or-dir> [...] [--json report.json]

The JSON report is deterministic (sorted paths, no timestamps) so it can be
committed as evidence and diffed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

SCHEMA = 1

# Verdicts. These are deliberately narrow: detection is not a build result.
MANAGED = "UNSUPPORTED_NOT_TESTED"
NATIVE = "NOT_CLI"
NOT_PE = "NOT_PE"
MALFORMED = "REJECTED_MALFORMED"
IO_ERROR = "REJECTED_IO"
REJECTED = (MALFORMED, IO_ERROR)

COM_DESCRIPTOR_DIR = 14
COR20_MIN_SIZE = 0x48
METADATA_SIGNATURE = 0x424A5342  # "BSJB"
KNOWN_TABLES_STREAMS = ("#~", "#-")
KNOWN_STREAMS = ("#~", "#-", "#Strings", "#Blob", "#GUID", "#US")

COMIMAGE_FLAGS = {
    0x00000001: "ILONLY",
    0x00000002: "32BITREQUIRED",
    0x00000004: "IL_LIBRARY",
    0x00000008: "STRONGNAMESIGNED",
    0x00000010: "NATIVE_ENTRYPOINT",
    0x00010000: "TRACKDEBUGDATA",
    0x00020000: "32BITPREFERRED",
}


class ParseError(Exception):
    """A structural violation; the image must not be trusted or translated."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _u16(data: bytes, off: int, what: str) -> int:
    if off < 0 or off + 2 > len(data):
        raise ParseError("truncated structure", what)
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int, what: str) -> int:
    if off < 0 or off + 4 > len(data):
        raise ParseError("truncated structure", what)
    return struct.unpack_from("<I", data, off)[0]


@dataclass
class CliStream:
    name: str
    offset: int          # relative to metadata root
    size: int
    file_offset: int
    known: bool = True


@dataclass
class MetadataRoot:
    file_offset: int
    size: int
    version_major: int
    version_minor: int
    runtime_version: str
    flags: int
    streams: List[CliStream] = field(default_factory=list)
    missing_expected_streams: List[str] = field(default_factory=list)

    @property
    def tables_stream(self) -> Optional[str]:
        for s in self.streams:
            if s.name in KNOWN_TABLES_STREAMS:
                return s.name
        return None


@dataclass
class Cor20Header:
    file_offset: int
    cb: int
    major_runtime_version: int
    minor_runtime_version: int
    flags: int
    flag_names: List[str]
    entrypoint_token: int
    metadata: MetadataRoot


@dataclass
class CliReport:
    """Structured result. ``status`` is a verdict, never a recompilation."""

    schema: int = SCHEMA
    path: str = ""
    sha256: str = ""
    file_size: int = 0
    is_pe: bool = False
    is_managed: bool = False
    pe_type: str = ""
    machine: int = 0
    status: str = NATIVE
    reason: str = ""
    cor20: Optional[Cor20Header] = None
    # An honest, constant claim: this tool only inspects.
    claim: str = "detection only; no IL-to-C or IL-to-native translation implemented"
    recompile_attempted: bool = False

    def as_json(self) -> dict:
        return asdict(self)


def _parse_pe(data: bytes) -> dict:
    """Return the PE facts needed to find and validate the COR20 directory."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ParseError("not a PE image", "missing MZ signature")
    pe_off = _u32(data, 0x3C, "e_lfanew")
    if pe_off > len(data) - 24:
        raise ParseError("invalid PE header offset", hex(pe_off))
    if data[pe_off:pe_off + 4] != b"PE\0\0":
        raise ParseError("not a PE image", "missing PE signature")
    coff = pe_off + 4
    machine = _u16(data, coff, "COFF Machine")
    nsec = _u16(data, coff + 2, "COFF NumberOfSections")
    opt_size = _u16(data, coff + 16, "COFF SizeOfOptionalHeader")
    opt = coff + 20
    magic = _u16(data, opt, "optional header Magic")
    if magic == 0x10B:
        pe_type, dd_base, nrva_off = "PE32", opt + 96, opt + 92
    elif magic == 0x20B:
        pe_type, dd_base, nrva_off = "PE32+", opt + 112, opt + 108
    else:
        raise ParseError("unsupported optional header magic", hex(magic))
    nrva = _u32(data, nrva_off, "NumberOfRvaAndSizes")
    if nrva <= COM_DESCRIPTOR_DIR:
        return {"pe_type": pe_type, "machine": machine,
                "cor_rva": 0, "cor_size": 0, "sections": []}
    if dd_base + (COM_DESCRIPTOR_DIR + 1) * 8 > len(data):
        raise ParseError("truncated data directory")
    dd = dd_base + COM_DESCRIPTOR_DIR * 8
    cor_rva = _u32(data, dd, "COM descriptor RVA")
    cor_size = _u32(data, dd + 4, "COM descriptor size")

    sec_base = opt + opt_size
    if nsec == 0 or nsec > 96 or sec_base + nsec * 40 > len(data):
        raise ParseError("invalid section table", f"{nsec} sections")
    sections = []
    for i in range(nsec):
        s = sec_base + i * 40
        name = data[s:s + 8].split(b"\0", 1)[0].decode("ascii", "replace")
        vsize, va, rawsize, rawoff = struct.unpack_from("<IIII", data, s + 8)
        sections.append({"name": name, "vsize": vsize, "va": va,
                         "rawsize": rawsize, "rawoff": rawoff})
    return {"pe_type": pe_type, "machine": machine, "cor_rva": cor_rva,
            "cor_size": cor_size, "sections": sections}


def _rva_to_offset(sections: list, rva: int, size: int, file_len: int) -> int:
    """Map an RVA range to a file offset, refusing overlaps and truncation."""
    if rva == 0:
        raise ParseError("null RVA")
    for s in sections:
        span = max(s["vsize"], s["rawsize"])
        if s["va"] <= rva < s["va"] + span:
            delta = rva - s["va"]
            off = s["rawoff"] + delta
            if delta + size > span:
                raise ParseError("RVA range exceeds section", hex(rva))
            if off < 0 or off + size > file_len:
                raise ParseError("RVA range truncated by file", hex(rva))
            return off
    raise ParseError("RVA not in any section", hex(rva))


def _parse_metadata(data: bytes, off: int, size: int) -> MetadataRoot:
    """Parse a metadata root. Every read is inside [off, off+size)."""
    end = off + size
    if size < 20 or end > len(data):
        raise ParseError("metadata root truncated or out of bounds")
    sig = _u32(data, off, "metadata signature")
    if sig != METADATA_SIGNATURE:
        raise ParseError("bad metadata signature", hex(sig))
    vmaj = _u16(data, off + 4, "metadata major version")
    vmin = _u16(data, off + 6, "metadata minor version")
    reserved = _u32(data, off + 8, "metadata reserved")
    if reserved != 0:
        raise ParseError("nonzero metadata reserved field", hex(reserved))
    ver_len = _u32(data, off + 12, "metadata version length")
    if ver_len == 0 or ver_len > size - 16 or (ver_len % 4) != 0:
        raise ParseError("invalid metadata version length", str(ver_len))
    ver_raw = data[off + 16:off + 16 + ver_len]
    runtime_version = ver_raw.split(b"\0", 1)[0].decode("ascii", "replace")
    p = off + 16 + ver_len
    flags = _u16(data, p, "metadata flags")
    nstreams = _u16(data, p + 2, "metadata stream count")
    p += 4
    if nstreams == 0 or nstreams > 64:
        raise ParseError("invalid metadata stream count", str(nstreams))

    streams: List[CliStream] = []
    names = set()
    for _ in range(nstreams):
        s_off = _u32(data, p, "stream offset")
        s_size = _u32(data, p + 4, "stream size")
        p += 8
        name_bytes = bytearray()
        while True:
            if p >= end or p >= len(data) or len(name_bytes) > 32:
                raise ParseError("unterminated stream name")
            ch = data[p]
            p += 1
            if ch == 0:
                break
            name_bytes.append(ch)
        # Names are padded with NULs to a 4-byte boundary relative to the root.
        pad = (4 - ((p - off) % 4)) % 4
        if p + pad > end:
            raise ParseError("stream header truncated by padding")
        p += pad
        name = name_bytes.decode("ascii", "replace")
        if not name or name in names:
            raise ParseError("empty or duplicate stream name", name)
        names.add(name)
        if s_off > size or s_size > size - s_off:
            raise ParseError("stream range outside metadata root", name)
        abs_off = off + s_off
        if abs_off + s_size > end or abs_off + s_size > len(data):
            raise ParseError("stream range truncated by file", name)
        streams.append(CliStream(name=name, offset=s_off, size=s_size,
                                 file_offset=abs_off, known=name in KNOWN_STREAMS))

    root = MetadataRoot(file_offset=off, size=size, version_major=vmaj,
                        version_minor=vmin, runtime_version=runtime_version,
                        flags=flags, streams=streams)
    if root.tables_stream is None:
        raise ParseError("missing #~/#- metadata tables stream")
    root.missing_expected_streams = [
        n for n in ("#Strings", "#Blob") if n not in names]
    return root


def _parse_cor20(data: bytes, off: int, dir_size: int, sections: list) -> Cor20Header:
    if dir_size < COR20_MIN_SIZE:
        raise ParseError("COM descriptor directory too small", str(dir_size))
    cb = _u32(data, off, "COR20 cb")
    if cb < COR20_MIN_SIZE or cb > dir_size:
        raise ParseError("invalid COR20 cb", f"{cb} vs directory {dir_size}")
    if off + cb > len(data):
        raise ParseError("COR20 header truncated by file")
    major = _u16(data, off + 4, "MajorRuntimeVersion")
    minor = _u16(data, off + 6, "MinorRuntimeVersion")
    md_rva = _u32(data, off + 8, "MetaData RVA")
    md_size = _u32(data, off + 12, "MetaData size")
    flags = _u32(data, off + 16, "COR20 Flags")
    entry = _u32(data, off + 20, "EntryPointToken")
    md_off = _rva_to_offset(sections, md_rva, md_size, len(data))
    metadata = _parse_metadata(data, md_off, md_size)
    flag_names = [name for bit, name in COMIMAGE_FLAGS.items() if flags & bit]
    return Cor20Header(file_offset=off, cb=cb, major_runtime_version=major,
                       minor_runtime_version=minor, flags=flags,
                       flag_names=flag_names, entrypoint_token=entry,
                       metadata=metadata)


def detect_bytes(data: bytes, *, path: str = "<memory>") -> CliReport:
    """Inspect PE bytes. Never raises; structural faults become a REJECTED verdict."""
    report = CliReport(path=path, sha256=hashlib.sha256(data).hexdigest(),
                       file_size=len(data))
    try:
        pe = _parse_pe(data)
    except ParseError as exc:
        report.status = NOT_PE if exc.reason.startswith("not a PE image") else MALFORMED
        report.reason = str(exc)
        return report
    report.is_pe = True
    report.pe_type = pe["pe_type"]
    report.machine = pe["machine"]
    if pe["cor_rva"] == 0 and pe["cor_size"] == 0:
        report.reason = "no COM descriptor data directory (native PE)"
        return report
    if pe["cor_rva"] == 0 or pe["cor_size"] == 0:
        report.status = MALFORMED
        report.reason = "inconsistent COM descriptor directory (one field is null)"
        return report
    try:
        cor_off = _rva_to_offset(pe["sections"], pe["cor_rva"],
                                 COR20_MIN_SIZE, len(data))
        report.cor20 = _parse_cor20(data, cor_off, pe["cor_size"], pe["sections"])
    except ParseError as exc:
        report.status = MALFORMED
        report.reason = str(exc)
        return report
    report.is_managed = True
    report.status = MANAGED
    report.reason = ("managed CLI assembly detected; no IL recompiler is "
                     "implemented here, so nothing was built or run")
    return report


def detect_file(path) -> CliReport:
    """Inspect a file. I/O faults are reported, never raised."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        return CliReport(path=str(p), status=IO_ERROR,
                         reason=f"cannot read file: {exc.strerror or exc}")
    return detect_bytes(data, path=str(p))


EXTS = (".exe", ".dll", ".netmodule", ".obj")


def scan(paths, *, recursive: bool = True) -> List[CliReport]:
    """Scan files and/or directories; directories are filtered to PE-like names."""
    found = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            children = p.rglob("*") if recursive else p.glob("*")
            files = sorted(f for f in children
                           if f.is_file() and f.suffix.lower() in EXTS)
        elif p.is_file():
            files = [p]
        else:
            files = []
            found.append(CliReport(path=str(p), status=IO_ERROR,
                                    reason="path does not exist"))
        found.extend(detect_file(f) for f in files)
    found.sort(key=lambda r: r.path)
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help="PE files or directories to inspect")
    ap.add_argument("--json", dest="json_path", help="write a deterministic JSON report")
    ap.add_argument("--no-recurse", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="suppress per-file summary")
    ap.add_argument("--only-detections", action="store_true",
                    help="include only managed/rejected files in the JSON report "
                         "(counts still cover every file scanned)")
    args = ap.parse_args(argv)
    reports = scan(args.paths, recursive=not args.no_recurse)
    counts = {}
    for r in reports:
        counts[r.status] = counts.get(r.status, 0) + 1
        if not args.quiet:
            detail = f" cli={r.cor20.major_runtime_version}.{r.cor20.minor_runtime_version}" \
                     f" streams={','.join(s.name for s in r.cor20.metadata.streams)}" \
                if r.cor20 else ""
            print(f"{r.status:24s} {r.path} sha256={r.sha256[:12]}{detail}")
    if args.json_path:
        listed = reports
        if args.only_detections:
            listed = [r for r in reports
                      if r.status == MANAGED or r.status in REJECTED]
        out = {"schema": SCHEMA, "tool": "tools/pe/cli_metadata.py",
               "claim": "detection only; no IL-to-C or IL-to-native translation implemented",
               "counts": counts, "files": [r.as_json() for r in listed]}
        Path(args.json_path).write_text(json.dumps(out, indent=2) + "\n")
        if not args.quiet:
            print(f"wrote {args.json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
