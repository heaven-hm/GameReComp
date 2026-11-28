"""Deterministic bounds tests for runtime/recomp32/image_loader.c (issue #330).

`recomp_load_image` is Windows-only, so each test compiles the real C source
against a tiny windows.h shim plus a driver that records every VirtualAlloc
request. When an AddressSanitizer-capable compiler is available, the same
binary also detects any read past the malloc'd file buffer (the exact bug the
unchecked PE offsets caused). Every case is a synthetic PE buffer written to a
temp file: no game assets, no Windows host, no network.

Run:  python3 -m pytest tests/test_image_loader_bounds.py -q
"""

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "runtime" / "recomp32" / "image_loader.c"

# Minimal stand-in for the Windows API the loader uses. The driver supplies
# VirtualAlloc; everything else is C runtime.
WINDOWS_SHIM = r"""#ifndef TEST_WINDOWS_H
#define TEST_WINDOWS_H
#include <stddef.h>
#include <stdint.h>
#define MEM_RESERVE 0x00002000u
#define MEM_COMMIT  0x00001000u
#define PAGE_EXECUTE_READWRITE 0x40u
typedef uint32_t DWORD;
typedef void* LPVOID;
LPVOID VirtualAlloc(LPVOID lpAddress, size_t dwSize, DWORD flAllocationType,
                    DWORD flProtect);
#endif
"""

DRIVER = r"""#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "image_loader.h"

static size_t g_sizes[8];
static int g_count;

/* Permissive allocator on purpose: a wrapped zero span must be rejected by
 * the loader itself, not rescued by a NULL from VirtualAlloc. */
void* VirtualAlloc(void* addr, size_t size, DWORD type, DWORD protect) {
    (void)addr; (void)type; (void)protect;
    if (g_count < 8) g_sizes[g_count] = size;   /* record the requested size */
    g_count++;
    if (size == 0) size = 1;
    void* p = malloc(size);
    if (p) memset(p, 0xA5, size);
    return p;
}

int main(int argc, char** argv) {
    if (argc < 3) return 2;
    uint32_t base = (uint32_t)strtoul(argv[2], NULL, 0);
    uint32_t span = recomp_load_image(argv[1], base);
    printf("span=0x%08X alloc_count=%d", span, g_count);
    for (int i = 0; i < g_count && i < 8; i++)
        printf(" alloc[%d]=0x%zX", i, g_sizes[i]);
    printf("\n");
    return 0;
}
"""

# Deliberately invalid and caught by the ASan smoke check below.
ASAN_SMOKE = r"""#include <stdlib.h>
int main(void) { volatile char* p = (char*)malloc(4); p[8] = 1; return p[8]; }
"""


def _run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True,
                          text=True, timeout=kw.pop("timeout", 60), **kw)


def _compiler_flags():
    """Return (cc, flags, has_asan). Skip Apple clang 16's broken ASan runtime
    by probing instead of assuming; Homebrew clang works with the CLT SDK."""
    flags = []
    if sys.platform == "darwin":
        sdk = _run(["xcrun", "--show-sdk-path"])
        if sdk.returncode == 0 and Path(sdk.stdout.strip()).is_dir():
            flags = ["-isysroot", sdk.stdout.strip()]

    for cc in (shutil.which("clang"), "/usr/bin/clang", shutil.which("gcc"),
               shutil.which("cc")):
        if not cc or not Path(cc).exists():
            continue
        with tempfile.TemporaryDirectory(prefix="grc-asan-probe-") as tmp:
            root = Path(tmp)
            src, exe = root / "probe.c", root / "probe"
            src.write_text(ASAN_SMOKE)
            probe = _run([cc, *flags, "-fsanitize=address", "-O0", src,
                          "-o", exe])
            if probe.returncode != 0:
                continue
            env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:abort_on_error=0")
            try:
                res = _run([exe], env=env)
            except subprocess.TimeoutExpired:
                continue
            if res.returncode != 0 and "AddressSanitizer" in res.stderr:
                return cc, [*flags, "-fsanitize=address"], True
        # A compiler without ASan is still useful for the fail-closed checks.
        return cc, list(flags), False
    raise unittest.SkipTest("no C compiler available for the loader harness")


def _pe(e_lfanew=0x40, opt_sz=96, nsec=1, size=0x400,
        sections=None, size_of_headers=0x200, signature=b"PE\0\0"):
    """Build a PE32 byte buffer. `sections` is a list of
    (VirtualSize, VirtualAddress, SizeOfRawData, PointerToRawData)."""
    sections = sections if sections is not None else [(0x2000, 0x1000, 0, 0)]
    sec_off = e_lfanew + 24 + opt_sz
    buf = bytearray(max(size, sec_off + 40 * nsec + 4))
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, e_lfanew)
    buf[e_lfanew:e_lfanew + 4] = signature
    struct.pack_into("<H", buf, e_lfanew + 6, nsec)         # NumberOfSections
    struct.pack_into("<H", buf, e_lfanew + 20, opt_sz)      # SizeOfOptionalHeader
    struct.pack_into("<I", buf, e_lfanew + 24 + 60, size_of_headers)
    for i, (vsize, vaddr, rsize, roff) in enumerate(sections[:nsec]):
        struct.pack_into("<8sIIIIIIHHI", buf, sec_off + 40 * i,
                         b".text\0\0\0", vsize, vaddr, rsize, roff, 0, 0, 0, 0,
                         0x60000020)
    if size < len(buf):
        del buf[size:]                                      # truncated file
    else:
        buf.extend(b"\0" * (size - len(buf)))
    return bytes(buf)


def _mz_with_lfanew(value, size=0x200):
    """Only the DOS header exists; e_lfanew points far outside the buffer."""
    buf = bytearray(size)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, value)
    return bytes(buf)


class ImageLoaderBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="grc-image-loader-")
        cls.root = Path(cls.tmp.name)
        cls.cc, cls.flags, cls.asan = _compiler_flags()
        (cls.root / "windows.h").write_text(WINDOWS_SHIM)
        (cls.root / "driver.c").write_text(DRIVER)
        cmd = [cls.cc, *cls.flags, "-std=c11", "-O1", "-g",
               "-I", str(cls.root), "-I", str(SRC.parent),
               str(cls.root / "driver.c"), str(SRC),
               "-o", str(cls.root / "driver")]
        build = _run(cmd)
        if build.returncode != 0:
            cls.tmp.cleanup()
            raise unittest.SkipTest(f"loader harness failed to build: {build.stderr}")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _load(self, blob, base=0):
        path = self.root / "case.bin"
        path.write_bytes(blob)
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:abort_on_error=0")
        proc = _run([self.root / "driver", path, hex(base)], env=env)
        line = proc.stdout.strip()
        head = re.search(r"span=0x([0-9A-Fa-f]+) alloc_count=(\d+)", line)
        return {
            "rc": proc.returncode,
            "span": int(head.group(1), 16) if head else None,
            "allocs": [int(x, 16) for x in
                       re.findall(r"alloc\[\d+\]=0x([0-9A-Fa-f]+)", line)],
            "stderr": proc.stderr,
            "stdout": line,
        }

    def assert_rejected(self, result, needle):
        # ASan aborts with a nonzero exit on an out-of-bounds header read.
        self.assertEqual(result["rc"], 0, result)
        self.assertNotIn("AddressSanitizer", result["stderr"], result)
        self.assertEqual(result["span"], 0, result)
        self.assertEqual(result["allocs"], [], result)      # no mapping attempted
        self.assertIn(needle, result["stderr"], result)
        self.assertIn("[loader]", result["stderr"], result)

    def test_huge_e_lfanew_is_rejected_without_any_read(self):
        result = self._load(_mz_with_lfanew(0xFFFFFFF0))
        self.assert_rejected(result, "e_lfanew")

    def test_e_lfanew_past_file_is_rejected(self):
        result = self._load(_mz_with_lfanew(0x10000))
        self.assert_rejected(result, "e_lfanew")

    def test_e_lfanew_inside_dos_header_is_rejected(self):
        result = self._load(_pe(e_lfanew=0x20))
        self.assert_rejected(result, "e_lfanew")

    def test_truncated_nt_header_is_rejected(self):
        # 0x40 + 24 + 64 bytes are required; only 0x50 are present.
        result = self._load(_mz_with_lfanew(0x40, size=0x50))
        self.assert_rejected(result, "e_lfanew")

    def test_truncated_optional_header_is_rejected(self):
        # File holds the mandatory 24 + 64 bytes, not the declared 0xE0.
        result = self._load(_pe(opt_sz=0xE0, size=0x40 + 24 + 64 + 8))
        self.assert_rejected(result, "optional header truncated")

    def test_too_small_optional_header_is_rejected(self):
        result = self._load(_pe(opt_sz=32, nsec=0, size=0x200))
        self.assert_rejected(result, "optional header size")

    def test_truncated_section_table_is_rejected(self):
        # Eight sections declared, room for two in the file.
        result = self._load(_pe(nsec=8, size=0x40 + 24 + 96 + 40 * 2))
        self.assert_rejected(result, "section table truncated")

    def test_wraparound_span_near_fffff000_is_rejected(self):
        result = self._load(_pe(sections=[(0x1000, 0xFFFFF000, 0, 0)]))
        # assert_rejected also proves no VirtualAlloc(0) request was made --
        # the old (max_end + 0xFFF) & ~0xFFF wrapped to exactly that.
        self.assert_rejected(result, "span overflow")

    def test_wraparound_span_with_raw_data_is_rejected(self):
        result = self._load(_pe(sections=[(0x2000, 0xFFFFF000, 0x100, 0x100)]))
        self.assert_rejected(result, "span overflow")

    def test_image_base_plus_span_past_4gb_is_rejected(self):
        result = self._load(_pe(sections=[(0x1000, 0x20000, 0, 0)]),
                            base=0xFFFF0000)
        self.assert_rejected(result, "span overflow")

    def test_no_sections_is_rejected(self):
        result = self._load(_pe(nsec=0))
        self.assert_rejected(result, "span overflow")

    def test_valid_pe_still_maps_with_64bit_math(self):
        result = self._load(_pe())
        self.assertEqual(result["rc"], 0, result)
        self.assertNotIn("AddressSanitizer", result["stderr"], result)
        self.assertEqual(result["span"], 0x3000, result)
        self.assertEqual(result["allocs"], [0x3000], result)


if __name__ == "__main__":
    unittest.main()
