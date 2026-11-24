#!/usr/bin/env python3
"""Audit the Win32 API surface of PE32 and PE32+ modules.

This is a *port and adaptation* of three small, MIT-licensed utilities from
Sahilg93/arm64-recompiler (commit bc1ca0ecd6decdbf46cbd378a392a60863f81293):

  * ``recompiler/build_wine_db.py``   -- mine exported symbols out of PE DLLs
    and build the ``api_to_dll`` / ``dll_to_apis`` / ``core_dlls`` database;
  * ``recompiler/audit_pe.py``        -- the per-file audit report (sha256,
    entry RVA, import slots, writable sections, TLS directory/callbacks);
  * ``recompiler/scan_missing_imports.py`` -- rank imports that are not yet
    known/shimmed, per file and per DLL.

Those upstream scripts are thin wrappers around ``pefile`` and are tied to
that project's lifter. GameReComp's PE toolbox is deliberately dependency
free and its win32hle shim work needs *both* PE32 and PE32+ (the existing
``tools/pe/extract_imports.py`` returns nothing for PE32+). This module keeps
the upstream algorithms and report shape but replaces the ``pefile`` calls
with a small ``struct``-based reader, and adds PE32+ support and explicit
error reporting. See ``docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md``.

Upstream licence: MIT, Copyright (c) 2026 Sahilg93, preserved verbatim at
``LICENSES/arm64-recompiler-MIT.txt``.

Usage::

    python -m tools.pe.audit_api_surface module.exe other.dll   # per-file JSON
    python -m tools.pe.audit_api_surface --api-db lib/*.dll     # export DB
    python -m tools.pe.audit_api_surface --known known.txt *.exe --top 10
"""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

# The DLLs build_wine_db.py singled out as the "core" Windows surface.
CORE_DLLS = ("kernel32.dll", "kernelbase.dll", "ntdll.dll", "ucrtbase.dll",
             "advapi32.dll", "user32.dll")

# Upstream audit_pe.py capped the TLS callback walk at 256 entries.
TLS_CALLBACK_LIMIT = 256

_MACHINES = {0x014C: "I386", 0x8664: "AMD64", 0x01C0: "ARM",
             0xAA64: "ARM64", 0x0200: "IA64"}


class PEFormatError(ValueError):
    """Raised when a file is not a PE we can audit."""


class PEImage:
    """Minimal, dependency-free PE32/PE32+ header and directory reader."""

    def __init__(self, data):
        self.data = data
        if data[:2] != b"MZ" or len(data) < 0x40:
            raise PEFormatError("not an MZ image")
        lfanew = self._u32(0x3C)
        if lfanew + 24 > len(data) or data[lfanew:lfanew + 4] != b"PE\0\0":
            raise PEFormatError("missing PE signature")

        coff = lfanew + 4
        self.machine = self._u16(coff)
        self.number_of_sections = self._u16(coff + 2)
        opt_size = self._u16(coff + 16)
        opt = coff + 20
        magic = self._u16(opt)
        if magic == 0x10B:
            self.pe_format = "PE32"
            self.image_base = self._u32(opt + 28)
            self.entry_rva = self._u32(opt + 16)
            dd_count_off, dd_off = opt + 92, opt + 96
            self.pointer_size = 4
        elif magic == 0x20B:
            self.pe_format = "PE32+"
            self.image_base = self._u64(opt + 24)
            self.entry_rva = self._u32(opt + 16)
            dd_count_off, dd_off = opt + 108, opt + 112
            self.pointer_size = 8
        else:
            raise PEFormatError(f"unsupported optional-header magic {magic:#x}")

        self.directories = {}
        count = min(self._u32(dd_count_off), 16)
        for i in range(count):
            off = dd_off + i * 8
            if off + 8 <= len(data):
                self.directories[i] = (self._u32(off), self._u32(off + 4))

        self.sections = []
        sec_off = opt + opt_size
        for i in range(self.number_of_sections):
            off = sec_off + i * 40
            if off + 40 > len(data):
                break
            name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
            vsize, rva, rawsize, rawptr = struct.unpack_from("<IIII", data, off + 8)
            chars = self._u32(off + 36)
            self.sections.append((name, rva, vsize, rawptr, rawsize, chars))

    # ---- primitive readers -------------------------------------------------
    def _u16(self, off):
        return struct.unpack_from("<H", self.data, off)[0]

    def _u32(self, off):
        return struct.unpack_from("<I", self.data, off)[0]

    def _u64(self, off):
        return struct.unpack_from("<Q", self.data, off)[0]

    def _ptr(self, off):
        return self._u32(off) if self.pointer_size == 4 else self._u64(off)

    def rva_to_offset(self, rva):
        """Map an RVA to a file offset (headers map to themselves)."""
        for _name, s_rva, vsize, rawptr, rawsize, _chars in self.sections:
            if s_rva <= rva < s_rva + max(vsize, rawsize):
                return rva - s_rva + rawptr
        return rva

    def cstring(self, rva):
        off = self.rva_to_offset(rva)
        if not 0 <= off < len(self.data):
            return ""
        end = self.data.find(b"\0", off)
        if end < 0:
            end = len(self.data)
        return self.data[off:end].decode("ascii", "replace")

    # ---- data directories --------------------------------------------------
    def imports(self):
        """Return ``{dll_name: [imported symbol names]}`` for the image."""
        rva, _size = self.directories.get(1, (0, 0))
        if not rva:
            return {}
        result = {}
        off = self.rva_to_offset(rva)
        while off + 20 <= len(self.data):
            ilt_rva, _stamp, _fwd, name_rva, iat_rva = struct.unpack_from(
                "<IIIII", self.data, off)
            if not (ilt_rva or name_rva or iat_rva):
                break
            dll = self.cstring(name_rva)
            thunk_rva = ilt_rva or iat_rva
            names = []
            t_off = self.rva_to_offset(thunk_rva)
            step = self.pointer_size
            ordinal_flag = 1 << (self.pointer_size * 8 - 1)
            while 0 <= t_off < len(self.data) - step:
                entry = self._ptr(t_off)
                if entry == 0:
                    break
                if entry & ordinal_flag:
                    names.append(f"ordinal_{entry & 0xFFFF}")
                else:
                    # IMAGE_IMPORT_BY_NAME: 2-byte hint, then the name.
                    names.append(self.cstring(entry + 2))
                t_off += step
            result[dll] = names
            off += 20
        return result

    def exports(self):
        """Return the list of named exports of the image (build_wine_db style)."""
        rva, size = self.directories.get(0, (0, 0))
        if not rva:
            return []
        off = self.rva_to_offset(rva)
        if off + 40 > len(self.data):
            return []
        (_chars, _stamp, _maj, _min, _name_rva, _base, _nfuncs,
         nnames, _funcs_rva, names_rva, ords_rva) = struct.unpack_from(
            "<IIHHIIIIIII", self.data, off)
        result = []
        names_off = self.rva_to_offset(names_rva)
        ords_off = self.rva_to_offset(ords_rva)
        for i in range(nnames):
            ordinal = self._u16(ords_off + i * 2)
            name = self.cstring(self._u32(names_off + i * 4))
            if name:
                # A forwarder points inside the export directory; upstream
                # build_wine_db.py records the symbol name either way.
                result.append(name)
        return result

    def tls_callbacks(self):
        """Count TLS callbacks, mirroring audit_pe.py's 256-entry cap."""
        rva, _size = self.directories.get(9, (0, 0))
        if not rva:
            return 0
        off = self.rva_to_offset(rva)
        # IMAGE_TLS_DIRECTORY.AddressOfCallBacks: +12 (PE32) / +24 (PE32+).
        callbacks_rva = self._ptr(off + (12 if self.pointer_size == 4 else 24))
        if not callbacks_rva:
            return 0
        cb_off = self.rva_to_offset(callbacks_rva)
        count = 0
        while count < TLS_CALLBACK_LIMIT:
            address = self._ptr(cb_off + count * self.pointer_size)
            if address == 0:
                break
            count += 1
        return count

    def writable_sections(self):
        return [name for name, _rva, _vs, _rp, _rs, chars in self.sections
                if chars & 0x80000000]


def audit_pe(path):
    """Return the per-file audit report (upstream ``audit_pe.audit`` shape)."""
    path = Path(path)
    try:
        data = path.read_bytes()
        pe = PEImage(data)
        imports = pe.imports()
        exports = pe.exports()
        return {
            "file": path.name,
            "sha256": sha256(data).hexdigest(),
            "machine": _MACHINES.get(pe.machine, f"{pe.machine:#06x}"),
            "pe_format": pe.pe_format,
            "entry_rva": f"{pe.entry_rva:#x}",
            "import_slots": sum(len(names) for names in imports.values()),
            "export_slots": len(exports),
            "imports": imports,
            "exports": exports,
            "writable_sections": pe.writable_sections(),
            "tls_directory": 9 in pe.directories and pe.directories[9] != (0, 0),
            "tls_callbacks": pe.tls_callbacks(),
        }
    except (OSError, ValueError, struct.error) as error:
        return {"file": path.name, "audit_error": str(error)}


def build_api_db(paths):
    """Port of ``build_wine_db.main``: export database over a set of PE files.

    ``api_to_dll`` records the first DLL defining each symbol, ``dll_to_apis``
    keeps every symbol per DLL, and ``core_dlls`` is the upstream six-DLL
    subset used to seed win32hle shims.
    """
    api_to_dll = {}
    dll_to_apis = {}
    for path in paths:
        path = Path(path)
        try:
            pe = PEImage(path.read_bytes())
        except (OSError, ValueError, struct.error):
            continue
        funcs = pe.exports()
        if not funcs:
            continue
        dll = path.name.lower()
        dll_to_apis[dll] = funcs
        for func in funcs:
            api_to_dll.setdefault(func, dll)
    return {
        "total_symbols": len(api_to_dll),
        "dll_count": len(dll_to_apis),
        "api_to_dll": api_to_dll,
        "dll_to_apis": dll_to_apis,
        "core_dlls": {dll: dll_to_apis[dll] for dll in CORE_DLLS if dll in dll_to_apis},
    }


def rank_missing_imports(paths, known_imports, top_n=25):
    """Port of ``scan_missing_imports.scan_directory``: rank unknown APIs.

    ``known_imports`` is a set of already-shimmed symbol names. Returns the
    same three views the upstream script printed: per-file misses, a global
    frequency ranking and a per-DLL breakdown.
    """
    freq = Counter()
    by_file = {}
    by_dll = {}
    for path in paths:
        path = Path(path)
        try:
            pe = PEImage(path.read_bytes())
        except (OSError, ValueError, struct.error) as error:
            by_file[path.name] = {"error": str(error)}
            continue
        missing = []
        for dll, names in pe.imports().items():
            for name in names:
                if name.startswith("ordinal_"):
                    continue
                if name not in known_imports:
                    missing.append((dll.lower(), name))
                    freq[name] += 1
                    by_dll.setdefault(dll.lower(), Counter())[name] += 1
        by_file[path.name] = [f"{dll}!{name}" for dll, name in missing]
    return {
        "by_file": by_file,
        "top_missing": freq.most_common(top_n),
        "by_dll": {dll: dict(funcs) for dll, funcs in sorted(
            by_dll.items(), key=lambda item: sum(item[1].values()), reverse=True)},
    }


def _read_known(path):
    text = Path(path).read_text()
    try:
        spec = json.loads(text)
    except ValueError:
        return {line.strip() for line in text.splitlines() if line.strip()}
    if isinstance(spec, dict):
        known = set(spec.get("passthrough", []))
        for item in spec.get("stubs", []) + spec.get("adapters", []):
            if isinstance(item, dict) and "name" in item:
                known.add(item["name"])
        return known
    return set(spec)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", type=Path, nargs="+", help="PE files to audit")
    parser.add_argument("--api-db", action="store_true",
                        help="build the upstream-style exported-API database")
    parser.add_argument("--known", type=Path,
                        help="file of known/shimmed API names; rank imports against it")
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args(argv)

    if args.api_db:
        result = build_api_db(args.paths)
    elif args.known:
        result = rank_missing_imports(args.paths, _read_known(args.known), args.top)
    else:
        result = [audit_pe(path) for path in args.paths]
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
