"""ARM32 target detection must be bounded and fail closed.

The decisive test is ``test_compiler_success_with_host_arch_artifact_is_blocked``:
on this host ``xcrun -sdk iphoneos clang`` was observed to print an SDK error,
exit 0, and emit a *host arm64* object. Exit status alone must never be counted
as ARM32 support.
"""
from __future__ import annotations

import platform
import struct
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

from tools.arm32_target_probe import (
    ARM32_TRIPLE,
    CPU_TYPE_ARM,
    CPU_TYPE_ARM64,
    STATUSES,
    macho_cputypes,
    probe_arm32_target,
)


def _macho(cputype: int, *, bits: int = 32, big: bool = False) -> bytes:
    """Minimal but valid thin Mach-O header for test artifacts."""
    magic = 0xFEEDFACF if bits == 64 else 0xFEEDFACE
    if big:
        return struct.pack(">II", magic, cputype) + b"\x00" * 24
    # A little-endian Mach-O file stores the magic swapped on disk: packing
    # 0xFEEDFACF with '<' produces bytes cf fa ed fe (MH_CIGAM_64).
    return struct.pack("<II", magic, cputype) + b"\x00" * 24


def _fat(cputypes: list[int]) -> bytes:
    out = struct.pack(">II", 0xCAFEBABE, len(cputypes))
    for cputype in cputypes:
        out += struct.pack(">IIIII", cputype, 0, 0, 0, 0)
    return out


ARM32_OBJ = _macho(CPU_TYPE_ARM)
ARM32_EXE = _macho(CPU_TYPE_ARM)
HOST_OBJ = _macho(CPU_TYPE_ARM64, bits=64)


def _runner(*, compile_bytes=ARM32_OBJ, compile_rc=0, compile_stdout="",
            link_bytes=ARM32_EXE, link_rc=0, link_stdout="",
            exec_rc=1, exec_stdout="", exec_exc=None):
    """Build a fake subprocess.run that writes the requested artifacts."""

    def fake_run(cmd, **kwargs):
        c = [str(x) for x in cmd]
        if "--print-targets" in c:
            return CompletedProcess(c, 0, "Registered Targets:\n"
                                    "    arm         - ARM\n"
                                    "    arm64_32    - ARM64 (little endian ILP32)\n", "")
        if any("xcrun" in x for x in c):
            return CompletedProcess(c, 1, 'xcrun: error: SDK "iphoneos" '
                                    'cannot be located\n', "")
        if "-c" in c:
            out = Path(c[c.index("-o") + 1])
            if compile_bytes is not None and compile_rc == 0:
                out.write_bytes(compile_bytes)
            return CompletedProcess(c, compile_rc, compile_stdout, "")
        if "-nostdlib" in c:
            out = Path(c[c.index("-o") + 1])
            if link_bytes is not None and link_rc == 0:
                out.write_bytes(link_bytes)
            return CompletedProcess(c, link_rc, link_stdout, "")
        if exec_exc is not None:
            raise exec_exc
        return CompletedProcess(c, exec_rc, exec_stdout, "")

    return fake_run


class MachOHeaderTests(unittest.TestCase):
    def test_thin_arm32_little_and_big_endian(self):
        with __import__("tempfile").TemporaryDirectory() as td:
            p = Path(td) / "o"
            p.write_bytes(ARM32_OBJ)
            self.assertEqual(macho_cputypes(p), [CPU_TYPE_ARM])
            p.write_bytes(_macho(CPU_TYPE_ARM, big=True))
            self.assertEqual(macho_cputypes(p), [CPU_TYPE_ARM])

    def test_fat_header_lists_all_cputypes(self):
        with __import__("tempfile").TemporaryDirectory() as td:
            p = Path(td) / "fat"
            p.write_bytes(_fat([CPU_TYPE_ARM, CPU_TYPE_ARM64]))
            self.assertEqual(macho_cputypes(p), [CPU_TYPE_ARM, CPU_TYPE_ARM64])

    def test_non_macho_and_missing_file_are_rejected(self):
        with __import__("tempfile").TemporaryDirectory() as td:
            p = Path(td) / "elf"
            p.write_bytes(b"\x7fELF" + b"\x00" * 60)
            self.assertIsNone(macho_cputypes(p))
            self.assertIsNone(macho_cputypes(Path(td) / "missing"))


class FailClosedProbeTests(unittest.TestCase):
    def test_compiler_success_with_host_arch_artifact_is_blocked(self):
        """The observed `xcrun -sdk iphoneos clang` trap must not go green."""
        with mock.patch("tools.arm32_target_probe.subprocess.run",
                        new=_runner(compile_bytes=HOST_OBJ, compile_rc=0)):
            rec = probe_arm32_target()
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertEqual(rec["triple"], ARM32_TRIPLE)
        self.assertFalse(rec["compile"]["verified_arm32"])
        self.assertEqual(rec["compile"]["cputypes"], [CPU_TYPE_ARM64])
        self.assertTrue(any("verified ARM32" in r for r in rec["reasons"]))
        self.assertEqual(rec["link"], {})

    def test_link_failure_is_cross_build_only_with_verbatim_output(self):
        err = ("ld: warning: Csu support file -lcrt1.3.1.o not found, changing "
               "to target iOS 7.0 where it is not needed\n"
               "ld: library not found for -lSystem\n"
               "clang: error: linker command failed with exit code 1\n")
        with mock.patch("tools.arm32_target_probe.subprocess.run",
                        new=_runner(link_rc=1, link_stdout=err)):
            rec = probe_arm32_target()
        self.assertEqual(rec["status"], "CROSS_BUILD_ONLY")
        self.assertTrue(rec["compile"]["verified_arm32"])
        self.assertFalse(rec["link"]["verified_arm32"])
        self.assertIn("-lSystem", rec["link"]["output"])

    def test_execution_failure_is_cross_build_only(self):
        exc = OSError(86, "Bad CPU type in executable")
        with mock.patch("tools.arm32_target_probe.subprocess.run",
                        new=_runner(exec_exc=exc)):
            rec = probe_arm32_target()
        self.assertEqual(rec["status"], "CROSS_BUILD_ONLY")
        self.assertTrue(rec["link"]["verified_arm32"])
        self.assertFalse(rec["execution"]["succeeded"])
        self.assertIn("Bad CPU type", rec["execution"]["output"])

    def test_available_requires_a_real_successful_execution(self):
        with mock.patch("tools.arm32_target_probe.subprocess.run",
                        new=_runner(exec_rc=0, exec_stdout="ok\n")):
            rec = probe_arm32_target()
        self.assertEqual(rec["status"], "AVAILABLE")
        self.assertTrue(rec["execution"]["succeeded"])
        self.assertEqual(rec["reasons"], [])

    def test_missing_clang_is_blocked(self):
        with mock.patch("tools.arm32_target_probe.shutil.which", return_value=None):
            rec = probe_arm32_target()
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertIn("clang not found", " ".join(rec["reasons"]))

    def test_missing_explicit_source_is_blocked(self):
        rec = probe_arm32_target(source="/definitely/not/a/real/generated_unit.c")
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(any("source not found" in r for r in rec["reasons"]))

    def test_skip_exec_never_claims_available(self):
        with mock.patch("tools.arm32_target_probe.subprocess.run", new=_runner()):
            rec = probe_arm32_target(skip_exec=True)
        self.assertEqual(rec["status"], "CROSS_BUILD_ONLY")
        self.assertEqual(rec["execution"], {})


class RealHostProbeTests(unittest.TestCase):
    def test_host_probe_is_bounded_and_never_false_available_on_apple_silicon(self):
        rec = probe_arm32_target(timeout=20)
        self.assertIn(rec["status"], STATUSES)
        self.assertIn("reasons", rec)
        if rec["status"] != "AVAILABLE":
            self.assertTrue(rec["reasons"])
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            # macOS on Apple Silicon has no ARM32 execution ABI. A green result
            # here would mean the detector was fooled, not that macOS changed.
            self.assertNotEqual(rec["status"], "AVAILABLE")
        if rec["status"] == "AVAILABLE":
            self.assertTrue(rec["execution"].get("succeeded"))


if __name__ == "__main__":
    unittest.main()
