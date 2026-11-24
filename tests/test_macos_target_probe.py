"""macOS target probe: fail-closed architecture reporting and x86_64 cross-build.

Three things are deliberately kept separate here, because conflating them is
the exact over-claim this tool exists to prevent:

  * a Mach-O header proves what architecture was BUILT;
  * sysctl.proc_translated == 0 proves NATIVE execution;
  * a translated run (proc_translated == 1, Rosetta 2) is NOT native execution.

The integration tests build a real generated unit with the project's own x86-64
lifter, cross-compile it to x86_64-apple-macos11, and read the Mach-O header
back. They never claim the unit ran natively on an arm64 host.
"""

import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import macos_target_probe as probe  # noqa: E402

MH_EXECUTE = 2
CPU_X86_64 = probe.CPU_TYPE_X86_64
CPU_ARM64 = probe.CPU_TYPE_ARM64


def thin_macho(cputype, is64=True, endian="<", filetype=MH_EXECUTE):
    b = bytearray(32 if is64 else 28)
    magic = (0xFEEDFACF if is64 else 0xFEEDFACE)
    struct.pack_into(endian + "I", b, 0, magic)
    struct.pack_into(endian + "3I", b, 4, cputype, 3, filetype)
    return bytes(b)


def fake_report(host="arm64", x86_run=None, arm_run=None):
    x86_run = x86_run or {"status": "translated", "proc_translated": 1,
                          "native": False, "translated": True, "error": None}
    arm_run = arm_run or {"status": "native", "proc_translated": 0,
                          "native": True, "translated": False, "error": None}
    return {
        "schema": probe.SCHEMA,
        "host": {"machine": host, "native_arch": host},
        "architectures": {
            "x86_64": {"build": {"proven": True, "kind": "cross",
                                 "cputype": CPU_X86_64, "macho": "x86_64",
                                 "reason": None},
                       "run": x86_run},
            "arm64": {"build": {"proven": True, "kind": "native",
                                "cputype": CPU_ARM64, "macho": "arm64",
                                "reason": None},
                      "run": arm_run},
        },
    }


class MachOParserTests(unittest.TestCase):
    def test_thin_x86_64(self):
        h = probe.parse_macho(thin_macho(CPU_X86_64))
        self.assertEqual(h["cputype"], CPU_X86_64)
        self.assertEqual(h["arch"], "x86_64")
        self.assertTrue(h["is64"])
        self.assertFalse(h["fat"])
        self.assertEqual(h["filetype"], MH_EXECUTE)

    def test_thin_arm64_big_endian(self):
        h = probe.parse_macho(thin_macho(CPU_ARM64, endian=">"))
        self.assertEqual(h["arch"], "arm64")
        self.assertTrue(h["is64"])

    def test_thin_i386_32bit(self):
        h = probe.parse_macho(thin_macho(probe.CPU_TYPE_I386, is64=False))
        self.assertEqual(h["arch"], "i386")
        self.assertFalse(h["is64"])

    def test_fat_with_both_slices(self):
        b = bytearray(8 + 2 * 20)
        struct.pack_into(">2I", b, 0, 0xCAFEBABE, 2)
        struct.pack_into(">5I", b, 8, CPU_X86_64, 3, 4096, 100, 12)
        struct.pack_into(">5I", b, 28, CPU_ARM64, 0, 8192, 100, 12)
        h = probe.parse_macho(bytes(b))
        self.assertTrue(h["fat"])
        self.assertEqual(h["cputypes"], [CPU_X86_64, CPU_ARM64])
        self.assertEqual(h["arch"], "x86_64")

    def test_bad_magic_fails_closed(self):
        with self.assertRaises(probe.MachOError):
            probe.parse_macho(b"MZ\x90\x00" + b"\x00" * 40)

    def test_truncated_fails_closed(self):
        with self.assertRaises(probe.MachOError):
            probe.parse_macho(b"\xcf\xfa\xed\xfe\x07")
        with self.assertRaises(probe.MachOError):
            probe.parse_macho(thin_macho(CPU_X86_64)[:20])

    def test_bad_fat_count_fails_closed(self):
        b = bytearray(8)
        struct.pack_into(">2I", b, 0, 0xCAFEBABE, 100000)
        with self.assertRaises(probe.MachOError):
            probe.parse_macho(bytes(b))


class ClaimTests(unittest.TestCase):
    def test_x86_64_translated_is_not_native(self):
        r = fake_report(host="arm64")
        r["claims"] = probe.claims_from(r)
        self.assertFalse(r["claims"]["x86_64_native_execution"])
        self.assertTrue(r["claims"]["x86_64_run_via_rosetta"])
        self.assertTrue(r["claims"]["x86_64_cross_compile"])
        self.assertTrue(r["claims"]["arm64_native_execution"])

    def test_fail_closed_native_gate(self):
        r = fake_report(host="arm64")
        r["claims"] = probe.claims_from(r)
        self.assertEqual(
            probe.unmet_requirements(r, require_native=["x86_64"]),
            ["native execution of x86_64 is not proven"])
        self.assertEqual(probe.unmet_requirements(r, require_build=["x86_64"]), [])

    def test_untranslated_foreign_arch_is_inconsistent_not_native(self):
        # proc_translated=0 for x86_64 on an arm64 host cannot be trusted; a
        # fail-closed tool must not turn it into a native-execution claim.
        run = {"status": "inconsistent_untranslated", "proc_translated": 0,
               "native": False, "translated": False, "error": "inconsistent"}
        r = fake_report(host="arm64", x86_run=run)
        r["claims"] = probe.claims_from(r)
        self.assertFalse(r["claims"]["x86_64_native_execution"])
        self.assertFalse(r["claims"]["x86_64_run_via_rosetta"])

    def test_native_claim_requires_matching_host(self):
        native = {"status": "native", "proc_translated": 0, "native": True,
                  "translated": False, "error": None}
        r = fake_report(host="x86_64", x86_run=native, arm_run=native)
        r["claims"] = probe.claims_from(r)
        self.assertTrue(r["claims"]["x86_64_native_execution"])
        self.assertFalse(r["claims"]["x86_64_run_via_rosetta"])

    def test_unbuilt_arch_is_not_proven(self):
        r = fake_report(host="arm64")
        r["architectures"]["x86_64"]["build"]["proven"] = False
        r["architectures"]["x86_64"]["build"]["reason"] = "compile failed"
        r["claims"] = probe.claims_from(r)
        self.assertFalse(r["claims"]["x86_64_build_proven"])
        self.assertTrue(probe.unmet_requirements(r, require_build=["x86_64"]))


@unittest.skipUnless(sys.platform == "darwin", "macOS-only toolchain check")
class ProbeIntegrationTests(unittest.TestCase):
    def test_report_is_accurate_and_fail_closed(self):
        if not shutil.which("clang") or shutil.which("xcrun") is None:
            self.skipTest("clang/xcrun not available")
        p = subprocess.run([sys.executable, str(ROOT / "tools" /
                                               "macos_target_probe.py"), "--json"],
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["schema"], probe.SCHEMA)
        self.assertEqual(r["host"]["native_arch"], "arm64" if
                         platform.machine() in ("arm64", "aarch64") else "x86_64")
        self.assertTrue(r["claims"]["arm64_build_proven"])
        self.assertTrue(r["claims"]["x86_64_build_proven"])
        # Exact commands are part of the contract: a reader must be able to
        # reproduce the verdict without trusting the summary.
        cmds = [" ".join(c["argv"]) for c in r["commands"]]
        self.assertTrue(any("-target x86_64-apple-macos11" in c for c in cmds))
        self.assertTrue(any("-target arm64-apple-macos11" in c for c in cmds))
        self.assertTrue(any("xcrun --show-sdk-path" in c for c in cmds))
        self.assertTrue(any("--print-targets" in c for c in cmds))
        if r["host"]["native_arch"] == "arm64":
            self.assertTrue(r["claims"]["arm64_native_execution"])
            self.assertFalse(r["claims"]["x86_64_native_execution"])
            self.assertTrue(r["claims"]["x86_64_cross_compile"])
            if r["rosetta"]["present"]:
                self.assertTrue(r["claims"]["x86_64_run_via_rosetta"])
        else:
            self.assertTrue(r["claims"]["x86_64_native_execution"])

    def test_require_native_x86_64_fails_on_arm64(self):
        if platform.machine() not in ("arm64", "aarch64"):
            self.skipTest("host is not arm64")
        p = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "macos_target_probe.py"),
             "--require-native", "x86_64"],
            capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 2)
        self.assertIn("native execution of x86_64 is not proven", p.stderr)


@unittest.skipUnless(sys.platform == "darwin", "requires macOS SDK")
class GeneratedUnitCrossBuildTests(unittest.TestCase):
    def test_lifted_unit_builds_as_x86_64_macho(self):
        sdk = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True,
                             text=True)
        if sdk.returncode != 0 or not shutil.which("clang"):
            self.skipTest("no Command Line Tools SDK or clang")
        try:
            sys.path.insert(0, str(ROOT / "tools" / "lift"))
            from lift64_cpu import Lifter  # noqa: E402
        except ImportError as e:
            self.skipTest("x86-64 lifter dependency unavailable: %s" % e)

        # mov eax, 1 ; add rax, 2 ; ret - lifted by the project's own lifter.
        code = bytes([0xB8, 0x01, 0x00, 0x00, 0x00,
                      0x48, 0x83, 0xC0, 0x02, 0xC3])
        lifted = Lifter(0x1000, image_base=0x140000000).lift_function(
            code, 0x140000000, name="lifted_probe")
        self.assertIn("SET32(c->rax, 0x1u)", lifted)

        with tempfile.TemporaryDirectory(prefix="i64-generated-unit-") as wd:
            src = os.path.join(wd, "generated.c")
            exe = os.path.join(wd, "lifted_probe_x64")
            with open(src, "w", encoding="utf-8") as f:
                f.write("/* GENERATED by tools/lift/lift64_cpu.py - do not edit */\n")
                f.write('#include "cpu64.h"\n#include <stdio.h>\n')
                f.write("int64_t g_image_delta = 0;\n")
                f.write("void dispatch(CPU *c, uint64_t t){(void)c;(void)t;}\n")
                f.write("void dispatch_jmp(CPU *c, uint64_t t){(void)c;(void)t;}\n")
                f.write(lifted + "\n")
                f.write("int main(void){ CPU c; memset(&c,0,sizeof(c)); "
                        "lifted_probe(&c); printf(\"lifted_rax=%llu\\n\","
                        "(unsigned long long)c.rax); return c.rax==3?0:1; }\n")
            cmd = [shutil.which("clang"), "-target", "x86_64-apple-macos11",
                   "-isysroot", sdk.stdout.strip(), "-I",
                   str(ROOT / "runtime" / "recomp64_cpu"), src, "-o", exe]
            b = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            self.assertEqual(b.returncode, 0, b.stderr)
            h = probe.read_macho(exe)
            self.assertEqual(h["cputype"], CPU_X86_64)
            self.assertEqual(h["arch"], "x86_64")
            self.assertEqual(h["filetype"], MH_EXECUTE)

            if shutil.which("lipo"):
                lipo = subprocess.run(["lipo", "-info", exe],
                                      capture_output=True, text=True)
                self.assertIn("x86_64", lipo.stdout)

            if platform.machine() in ("arm64", "aarch64"):
                # This run, IF it succeeds, is Rosetta translation (the probe
                # proves proc_translated=1 for x86_64 on this host), NOT native
                # x86_64 execution. With Rosetta absent the kernel refuses it.
                forced = subprocess.run(["arch", "-arm64", exe],
                                        capture_output=True, text=True)
                if forced.returncode != 0:
                    self.assertIn("bad cpu type in executable",
                                  forced.stderr.lower())


if __name__ == "__main__":
    unittest.main()
