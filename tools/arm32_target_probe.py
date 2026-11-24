#!/usr/bin/env python3
"""Fail-closed detector for ARM32 Apple target availability on this host.

The probe answers four separate questions, and never blends them into a single
"supported" claim:

  1. Does clang accept an ARM32 Apple triple (``armv7-apple-ios``) and emit a
     Mach-O object whose ``cputype`` is ``CPU_TYPE_ARM`` (12)?
  2. Can it link a Mach-O ARM32 executable (freestanding, ``-nostdlib``)?
  3. Is there an iPhoneOS SDK/sysroot, and a user-mode ``qemu-arm``?
  4. Can the produced ARM32 Mach-O actually be *executed* here?

Statuses are ``AVAILABLE``, ``CROSS_BUILD_ONLY``, ``BLOCKED`` and ``FAIL``.
Only ``AVAILABLE`` exits 0.

Why this is fail-closed
-----------------------
A compiler exit code of 0 is never trusted by itself. On this host a probe of
``xcrun -sdk iphoneos clang ...`` printed ``SDK "iphoneos" cannot be located``,
still exited 0, and silently produced a **host arm64** object. Every artifact is
therefore parsed and its Mach-O cputype verified before it is counted. Missing
SDKs, missing emulators, timeouts, unexpected exceptions and non-ARM artifacts
all resolve to a non-green status with the verbatim tool output attached.

The embedded C is a small *capability fixture*, not evidence that a game build
works. Pass ``--source`` to probe a real pre-existing generated C unit instead.
Everything is bounded: small fixed input, per-command timeout, truncated output,
no network, and all artifacts live in a ``TemporaryDirectory``.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

# Mach-O cputype constants (from <mach/machine.h>).
CPU_TYPE_ARM = 12
CPU_TYPE_ARM64 = 0x0100000C
CPU_TYPE_ARM64_32 = 0x0200000C

ARM32_TRIPLE = "armv7-apple-ios"
STATUSES = ("AVAILABLE", "CROSS_BUILD_ONLY", "BLOCKED", "FAIL")

# Self-contained capability fixture. It is deliberately tiny and has no
# includes, so a failure cannot be blamed on a missing sysroot header.
PROBE_SOURCE = """/* ARM32 capability fixture -- not a generated game project. */
typedef unsigned int u32;
int probe_entry(void);
int probe_entry(void) {
    u32 x = 0x00401000u * 0x000343FDu + 0x00269EC3u;
    return (int)((x >> 16) & 0x7FFFu);
}
"""


def macho_cputypes(path: Path, max_fat: int = 16) -> list[int] | None:
    """Return the Mach-O cputype(s) in *path*, or None if it is not Mach-O.

    Handles thin 32/64-bit files in either byte order and fat files. Only the
    header is read, so this stays bounded regardless of artifact size.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read(4096)
    except OSError:
        return None
    if len(data) < 8:
        return None

    magic = struct.unpack_from(">I", data, 0)[0]
    if magic in (0xCAFEBABE, 0xCAFEBABF):  # FAT_MAGIC / FAT_MAGIC_64
        nfat = struct.unpack_from(">I", data, 4)[0]
        if nfat < 1 or nfat > max_fat:
            return None
        entry_size = 32 if magic == 0xCAFEBABF else 20
        out: list[int] = []
        for i in range(nfat):
            off = 8 + i * entry_size
            if off + entry_size > len(data):
                return None
            out.append(struct.unpack_from(">I", data, off)[0])
        return out

    if magic == 0xFEEDFACE:  # MH_MAGIC (big-endian file)
        endian = ">"
    elif magic == 0xCEFAEDFE:  # MH_CIGAM (little-endian file)
        endian = "<"
    elif magic == 0xFEEDFACF:  # MH_MAGIC_64
        endian = ">"
    elif magic == 0xCFFAEDFE:  # MH_CIGAM_64
        endian = "<"
    else:
        return None
    if len(data) < 8:
        return None
    return [struct.unpack_from(endian + "I", data, 4)[0]]


def _is_arm32(artifact: Path) -> bool:
    types = macho_cputypes(artifact)
    return bool(types and CPU_TYPE_ARM in types)


def _run(cmd: list[str], timeout: int, max_output: int) -> dict:
    rec: dict = {"command": [str(c) for c in cmd], "exit_code": None,
                 "output": "", "timed_out": False}
    try:
        p = subprocess.run([str(c) for c in cmd], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=timeout,
                           check=False)
        rec["exit_code"] = p.returncode
        rec["output"] = p.stdout[-max_output:]
    except subprocess.TimeoutExpired as exc:
        rec["timed_out"] = True
        raw = exc.stdout or ""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        rec["output"] = ("TIMEOUT\n" + raw)[-max_output:]
    except OSError as exc:
        rec["output"] = f"OSError: {exc!r}"[-max_output:]
    return rec


def probe_arm32_target(clang: str | None = None, timeout: int = 30,
                       max_output: int = 4000, source: str | Path | None = None,
                       extra_args: list[str] | None = None,
                       skip_exec: bool = False) -> dict:
    """Probe ARM32 Apple cross-build/execution capability, fail-closed.

    ``extra_args`` are passed to every clang invocation (e.g. ``-include
    shim.h`` for a real generated unit that needs type definitions).
    """
    extra = [str(a) for a in (extra_args or [])]
    rec: dict = {
        "status": "BLOCKED",
        "host_arch": platform.machine(),
        "host_system": platform.system(),
        "triple": ARM32_TRIPLE,
        "compiler": clang or shutil.which("clang"),
        "backend_arm_registered": None,
        "backend_arm64_32_registered": None,
        "sdk": {},
        "compile": {},
        "link": {},
        "execution": {},
        "emulator": {},
        "source": str(source) if source else "embedded capability fixture",
        "output_truncated_to": max_output,
        "reasons": [],
    }
    reasons: list[str] = rec["reasons"]

    if not rec["compiler"]:
        reasons.append("clang not found on PATH")
        return rec
    if source is not None and not Path(source).is_file():
        reasons.append(f"source not found: {source}")
        return rec

    try:
        targets = _run([rec["compiler"], "--print-targets"], timeout, max_output)
        rec["backend_arm_registered"] = bool(
            re.search(r"(?m)^\s*(arm|thumb)\s+-", targets["output"]))
        rec["backend_arm64_32_registered"] = bool(
            re.search(r"(?m)^\s*arm64_32\s+-", targets["output"]))
        rec["targets_probe"] = targets

        if shutil.which("xcrun"):
            sdk = _run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"],
                       timeout, max_output)
            sdk_path = sdk["output"].strip().splitlines()[-1] if sdk["output"].strip() else ""
            sdk["available"] = bool(
                sdk["exit_code"] == 0 and sdk_path and Path(sdk_path).is_dir())
            sdk["resolved_path"] = sdk_path if sdk["available"] else ""
            rec["sdk"] = sdk
        else:
            rec["sdk"] = {"available": False, "reason": "xcrun not found"}

        qemu_user = shutil.which("qemu-arm") or shutil.which("qemu-arm-static")
        rec["emulator"] = {
            "qemu_user": qemu_user,
            "qemu_system_arm": shutil.which("qemu-system-arm"),
            "note": ("qemu-system-arm is a full-system emulator; it cannot run a "
                     "bare Mach-O executable. qemu-user emulates ELF, not Mach-O."),
        }

        with tempfile.TemporaryDirectory(prefix="a32-arm32-") as td:
            tmp = Path(td)
            if source is not None:
                src = Path(source)
            else:
                src = tmp / "arm32_probe_fixture.c"
                src.write_text(PROBE_SOURCE)
            obj = tmp / "probe_armv7.o"
            exe = tmp / "probe_armv7"

            comp = _run([rec["compiler"], *extra, "-target", ARM32_TRIPLE, "-O2",
                         "-ffreestanding", "-nostdinc", "-c", str(src),
                         "-o", str(obj)], timeout, max_output)
            comp["cputypes"] = macho_cputypes(obj)
            comp["verified_arm32"] = _is_arm32(obj)
            rec["compile"] = comp
            if not comp["verified_arm32"]:
                reasons.append(
                    "clang did not emit a verified ARM32 Mach-O object "
                    f"(cputypes={comp['cputypes']}, exit={comp['exit_code']})")
                return rec

            link = _run([rec["compiler"], *extra, "-target", ARM32_TRIPLE,
                         "-nostdlib", "-Wl,-e,_probe_entry", "-o", str(exe),
                         str(src)], timeout, max_output)
            link["cputypes"] = macho_cputypes(exe)
            link["verified_arm32"] = _is_arm32(exe)
            rec["link"] = link
            if not link["verified_arm32"]:
                reasons.append("no verified ARM32 Mach-O executable was linked")
                rec["status"] = "CROSS_BUILD_ONLY"
                return rec

            ran_ok = False
            if not skip_exec:
                execution = _run([str(exe)], timeout, max_output)
                ran_ok = execution["exit_code"] == 0
                execution["succeeded"] = ran_ok
                rec["execution"] = execution
                if not ran_ok:
                    reasons.append("ARM32 Mach-O could not be executed on this host")

            emu_ok = False
            if qemu_user and not skip_exec:
                emu = _run([qemu_user, str(exe)], timeout, max_output)
                emu_ok = emu["exit_code"] == 0
                emu["succeeded"] = emu_ok
                rec["emulator"]["user_run"] = emu
                if not emu_ok:
                    reasons.append("user-mode qemu-arm could not execute the Mach-O")

            if ran_ok or emu_ok:
                rec["status"] = "AVAILABLE"
            else:
                rec["status"] = "CROSS_BUILD_ONLY"
                if not reasons:
                    reasons.append("execution disabled (--skip-exec)")
            return rec
    except Exception as exc:  # fail closed on anything unexpected
        reasons.append(f"probe error: {exc!r}")
        rec["status"] = "FAIL"
        return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clang")
    ap.add_argument("--source", type=Path,
                    help="pre-existing C source to probe instead of the fixture")
    ap.add_argument("--extra-arg", action="append", default=[],
                    help="extra clang argument (repeatable), e.g. -include shim.h")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--max-output", type=int, default=4000)
    ap.add_argument("--skip-exec", action="store_true")
    ap.add_argument("--out-json", type=Path)
    args = ap.parse_args(argv)

    rec = probe_arm32_target(clang=args.clang, timeout=args.timeout,
                             max_output=args.max_output, source=args.source,
                             extra_args=args.extra_arg, skip_exec=args.skip_exec)
    text = json.dumps(rec, indent=2) + "\n"
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(text)
    print(text)
    return 0 if rec["status"] == "AVAILABLE" else 1


if __name__ == "__main__":
    sys.exit(main())
