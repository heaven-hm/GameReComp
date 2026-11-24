#!/usr/bin/env python3
"""Cross-build a generated *console* project to a Windows PE with MinGW-w64.

This is source recompilation of the generated C, not binary translation.  The
win32hle runtime is POSIX-based, so a plain `i686-w64-mingw32-gcc *.c` fails:
no sys/mman.h, dlfcn.h, poll.h, ucontext, and the GUI modules need SDL2 headers
that MinGW-w64 does not ship here.  This tool builds the small console subset
against runtime/win32hle/win32_port.{h,c} (VirtualAlloc/LoadLibraryA/WSAPoll
implementations) and the redirected POSIX headers in win32_compat/.

SCOPE: console apps only.  arith-calc.exe imports just
ExitProcess/GetStdHandle/WriteFile, so the kernel32 console shims are enough.
GUI/SDL/DDraw/DSound/DInput/OpenGL/Bink/MSS modules are NOT linked; their
shim tables are empty stubs in win32_port.c.  A project whose guest imports
those names will fail to boot with "unimplemented import <NAME>" -- that is
an honest failure, not a silent downgrade.  See docs/WIN32_CONSOLE_PORT.md.

Run with no Wine engine required.  Every failure returns a STATUS != OK with
the verbatim compiler output; no fake MZ file or success status is produced.
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

CC32 = "i686-w64-mingw32-gcc"
CC64 = "x86_64-w64-mingw32-gcc"

# The console subset: PE loading, guest memory, the HLE core, the two kernel32
# console shims, and the Win32 port layer.  Everything else in win32hle is
# GUI/audio/network/filesystem-heavy POSIX code and is deliberately omitted.
CONSOLE_RUNTIME = (
    "guest_mem.c", "hle_path.c", "host.c", "host_lite.c",
    "kernel32.c", "module.c", "pe_loader.c", "win32_port.c",
)


def find_cc(bits: int) -> str | None:
    return shutil.which(CC32 if bits == 32 else CC64)


def _pe_kind(path: Path) -> str | None:
    """Read the real PE optional-header magic; do not trust the file name."""
    data = path.read_bytes()[:1024]
    if data[:2] != b"MZ":
        return None
    if len(data) < 0x40:
        return None
    lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if lfanew + 26 > len(data) or data[lfanew:lfanew + 4] != b"PE\0\0":
        return None
    magic = struct.unpack_from("<H", data, lfanew + 24)[0]
    return {0x10B: "PE32", 0x20B: "PE32+"}.get(magic)


def build_console(project: Path, out: Path, bits: int = 32,
                  runtime: Path | None = None, timeout: int = 600) -> dict:
    """Cross-build a generated project.  Returns a JSON-serialisable record."""
    cc = find_cc(bits)
    if not cc:
        return {"status": "BLOCKED",
                "reason": f"{CC32 if bits == 32 else CC64} not found"}
    project = Path(project)
    if not project.is_dir():
        return {"status": "BLOCKED", "reason": f"project not found: {project}"}
    rt = Path(runtime) if runtime else project / "src" / "runtime" / "win32hle"
    if not (rt / "win32_port.c").is_file() or not (rt / "win32_compat").is_dir():
        return {"status": "BLOCKED",
                "reason": f"Win32 port layer missing in {rt}; regenerate the project"}
    gen = project / "src" / "recomp" / "gen"
    image_asm = gen / "recomp_image.S"
    image_bin = gen / "recomp_image.bin"
    if not image_asm.is_file() or not image_bin.is_file():
        return {"status": "BLOCKED",
                "reason": "project has no embedded image (recomp_image.S/.bin); "
                          "regenerate with --embed-image"}
    if not (project / "src" / "main.c").is_file():
        return {"status": "BLOCKED", "reason": "project has no src/main.c"}

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    obj = out.parent / (out.stem + "-objs")
    obj.mkdir(parents=True, exist_ok=True)

    sources = [project / "src" / "main.c"]
    sources += sorted(gen.glob("recomp_*.c"))
    sources += [rt / name for name in CONSOLE_RUNTIME]
    missing = [str(s) for s in sources if not s.is_file()]
    if missing:
        return {"status": "BLOCKED", "reason": f"missing sources: {missing}"}

    includes = [f"-I{rt / 'win32_compat'}", f"-I{rt}",
                f"-I{project / 'src' / 'runtime' / 'recomp32'}", f"-I{gen}"]
    defines = ["-DRECOMP_CONSOLE_ONLY", "-D_WIN32_WINNT=0x0601",
               "-DWIN32_LEAN_AND_MEAN=1"]
    if bits == 32:
        # A flat 32-bit guest would reserve host VA 0x400000, which Highball's
        # Wine already uses for its own low mappings ("map @ 0x00400000 failed
        # (the range is in use)").  Force the based-memory model so guest VAs
        # are offsets into a 1 GB reservation; that fits a 32-bit PE and keeps
        # every lifted MEM32(0x00xxxxxx) landing correctly.  GUI apps whose
        # guest VAs reach above 0x40000000 are out of scope for this port.
        defines += ["-DRECOMP_BASED_MEMORY=1", "-DRECOMP_GUEST_SPAN=0x40000000ull"]
    # -include makes the port visible to runtime sources that use POSIX names
    # (lstat, realpath, mkdir, fork, timegm, ...) without editing every file.
    common = [cc, "-std=gnu11", "-O2", "-w", *defines, *includes,
              "-include", "win32_port.h"]
    # i386 PE/COFF C symbols carry a leading underscore, x86-64 PE/COFF do not;
    # recomp_image.S handles both.  Assemble it separately: -include is for C.
    image_obj = obj / "recomp_image.o"
    asm_cmd = [cc, "-c", str(image_asm), "-o", str(image_obj)]
    link_cmd = common + [str(s) for s in sources] + [str(image_obj), "-o", str(out),
                 "-static",
                 f"-Wl,--image-base={'0x60000000' if bits == 32 else '0x140000000'}",
                 "-lws2_32", "-luser32", "-lgdi32", "-ladvapi32",
                 "-lshell32", "-lole32"]
    commands = [asm_cmd, link_cmd]
    try:
        for cmd in commands:
            p = subprocess.run(cmd, cwd=str(obj), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True,
                               timeout=timeout, check=False)
            if p.returncode != 0:
                return {"status": "FAIL", "reason": "cross-compilation failed",
                        "compiler_output": p.stdout[-6000:], "command": cmd,
                        "command_index": commands.index(cmd)}
    except subprocess.TimeoutExpired as exc:
        return {"status": "BLOCKED", "reason": "compiler timeout",
                "timeout": timeout, "command": getattr(exc, "cmd", None)}

    kind = _pe_kind(out) if out.is_file() else None
    if kind != ("PE32" if bits == 32 else "PE32+"):
        return {"status": "FAIL", "reason": "compiler succeeded but output is not the expected PE",
                "expected": "PE32" if bits == 32 else "PE32+", "actual": kind,
                "compiler_output": "", "command": link_cmd}
    return {"status": "OK", "binary": str(out), "bytes": out.stat().st_size,
            "pe": kind, "bits": bits,
            "sources": [str(s) for s in sources] + [str(image_asm)],
            "commands": commands,
            "note": ("console subset only; GUI/SDL shims are empty. "
                     "PE32 uses based guest memory (RECOMP_BASED_MEMORY, 1 GB span) "
                     "because Highball Wine occupies low host VAs.")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--bits", type=int, choices=(32, 64), default=32)
    ap.add_argument("--runtime", type=Path,
                    help="override runtime dir (defaults to the project's copy)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--out-json", type=Path)
    args = ap.parse_args(argv)
    rec = build_console(args.project, args.out, args.bits, args.runtime, args.timeout)
    text = json.dumps(rec, indent=2) + "\n"
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(text)
    print(text)
    return 0 if rec["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
