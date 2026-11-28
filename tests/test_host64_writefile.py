"""Deterministic regression tests for the recomp64 Linux host HLE shims (#345).

These tests exercise the REAL C source, not a Python model of it: a small C
harness includes runtime/recomp64_cpu/host64_linux.c, stubs the generated
headers and the loader's embedded image, and calls the file's static
`hle_call` / `hle_enter` helpers directly.

What is covered:

  * ``WriteFile`` truncates the Win64 DWORD count in R8 to 32 bits. A dirty
    upper half (the classic >4 GB value) used to be read as a full ``size_t``
    and made ``fwrite()`` walk gigabytes past a tiny guest buffer -> SIGSEGV.
  * ``WriteFile`` rejects ``lpBuffer == NULL`` for a non-zero count instead of
    handing NULL to ``fwrite()``; a zero-length write still succeeds.
  * ``lpNumberOfBytesWritten`` (R9) may be NULL; it must not be dereferenced.
  * ``hle_enter`` fails closed on misaligned / out-of-range HLE targets.

Nothing here loads a game, a fixture, a network resource or a real binary.
Run with: python3 -m pytest tests/test_host64_writefile.py -q
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "recomp64_cpu"
HOST64 = RUNTIME / "host64_linux.c"

HLE_BASE = 0x0000E5E30000
HLE_STRIDE = 8
HLE_COUNT = 4  # must match the stub recomp_imports[] below

# Stand-ins for headers emitted by tools/lift/generate64.py. host64_linux.c
# includes them from its own directory, so they are supplied via -I<tmp>/include.
RECOMP_FUNCS_H = "#pragma once\n"
RECOMP_DISPATCH_H = """#pragma once
#include "cpu64.h"
typedef struct { uint64_t va; void (*fn)(CPU *); } recomp_entry_t;
extern const recomp_entry_t recomp_dispatch_table[];
extern const uint32_t recomp_dispatch_count;
#define HLE_BASE   0x%Xull
#define HLE_STRIDE %d
#define HLE_COUNT  %d
#define HLE_SENTINEL(i) (HLE_BASE + (uint64_t)(i) * HLE_STRIDE)
typedef struct { uint64_t slot; const char *dll; const char *name; } hle_import_t;
extern const hle_import_t recomp_imports[];
""" % (HLE_BASE, HLE_STRIDE, HLE_COUNT)

# Rename main() so this translation unit can own one, then inject the statics.
HARNESS_C = r"""
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define main host64_unused_main
#include "host64_linux.c"
#undef main

/* Definitions the renamed loader main still refers to. */
const recomp_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;
const hle_import_t recomp_imports[] = {
    { 0, "KERNEL32", "WriteFile" },
    { 0, "KERNEL32", "GetStdHandle" },
    { 0, "KERNEL32", "ExitProcess" },
    { 0, "KERNEL32", "FlushFileBuffers" },
};
const unsigned char guest_image[1] = { 0 };
const unsigned long guest_image_len = 1;

static size_t hex_bytes(const char *s, unsigned char *out, size_t cap)
{
    size_t n = 0;
    while (*s && n < cap) {
        unsigned v = 0;
        if (sscanf(s, "%2x", &v) != 1) exit(3);
        out[n++] = (unsigned char)v;
        s += 2;
    }
    return n;
}

static uint64_t t_write(uint64_t handle, uint64_t buf, uint64_t len64,
                        uint64_t written_ptr)
{
    CPU c;
    memset(&c, 0, sizeof c);
    c.rcx = handle;                 /* 2 -> stderr, anything else -> stdout */
    c.rdx = buf;                    /* lpBuffer */
    c.r8 = len64;                   /* nNumberOfBytesToWrite (raw guest R8) */
    c.r9 = written_ptr;             /* lpNumberOfBytesWritten (may be NULL) */
    hle_call("WriteFile", &c);
    return c.rax;
}

int main(int argc, char **argv)
{
    unsigned char buf[64];

    if (argc < 2) return 2;

    /* wr <r8-hex> <buf-hex|-> : full result line "<BOOL> <bytes-written>".
     * WriteFile output goes to stderr; status goes to stdout. */
    if (!strcmp(argv[1], "wr") && argc == 4) {
        uint64_t len = strtoull(argv[2], NULL, 0);
        uint64_t bufp = 0;
        uint32_t written = 0xDEADBEEFu;
        uint64_t rax;
        if (strcmp(argv[3], "-")) {
            hex_bytes(argv[3], buf, sizeof buf);
            bufp = (uint64_t)(uintptr_t)buf;
        }
        rax = t_write(2, bufp, len, (uint64_t)(uintptr_t)&written);
        printf("%" PRIu64 " %" PRIu32 "\n", rax, written);
        return 0;
    }

    /* wrn <r8-hex> <buf-hex|-> : R9 == 0, must not be dereferenced. */
    if (!strcmp(argv[1], "wrn") && argc == 4) {
        uint64_t len = strtoull(argv[2], NULL, 0);
        uint64_t bufp = 0;
        uint64_t rax;
        if (strcmp(argv[3], "-")) {
            hex_bytes(argv[3], buf, sizeof buf);
            bufp = (uint64_t)(uintptr_t)buf;
        }
        rax = t_write(2, bufp, len, 0);
        printf("%" PRIu64 "\n", rax);
        return 0;
    }

    /* hle <target-hex> <pop> : valid targets print "<RAX> <RSP>"; invalid
     * targets make the fail-closed checks call die() -> exit(96). */
    if (!strcmp(argv[1], "hle") && argc == 4) {
        CPU c;
        uint64_t target = strtoull(argv[2], NULL, 0);
        int pop = atoi(argv[3]);
        memset(&c, 0, sizeof c);
        hle_enter(&c, target, pop);
        printf("%" PRIu64 " %" PRIu64 "\n", c.rax, c.rsp);
        return 0;
    }

    return 2;
}
"""


def _compiler(tmp_path: Path) -> str:
    probe = tmp_path / "probe.c"
    probe.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    for name in ("cc", "clang", "gcc"):
        exe = shutil.which(name)
        if not exe:
            continue
        out = tmp_path / f"probe-{name}"
        result = subprocess.run([exe, "-std=c11", str(probe), "-o", str(out)],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return exe
    pytest.skip("no C11 compiler is available")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("host64-writefile")
    cc = _compiler(tmp)
    inc = tmp / "include"
    inc.mkdir()
    (inc / "recomp_funcs.h").write_text(RECOMP_FUNCS_H, encoding="utf-8")
    (inc / "recomp_dispatch.h").write_text(RECOMP_DISPATCH_H, encoding="utf-8")
    source = tmp / "harness.c"
    source.write_text(HARNESS_C, encoding="utf-8")
    exe = tmp / "harness"
    result = subprocess.run(
        [cc, "-std=c11", "-O0", "-w", f"-I{inc}", f"-I{RUNTIME}",
         str(source), "-o", str(exe), "-lm"],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"C11 harness compile failed:\n{result.stderr}"
    return exe


def invoke(harness: Path, *args, expected_rc: int = 0):
    result = subprocess.run([str(harness), *(str(a) for a in args)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == expected_rc, (
        f"harness {args} exited {result.returncode} (expected {expected_rc}): "
        f"{result.stderr}")
    return result.stdout.strip(), result.stderr


# --------------------------------------------------------------------------- #
# WriteFile: DWORD truncation, NULL buffer, zero length, optional R9.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("len64", [
    0x0000000100000004,  # 4 GiB + 4: the canonical dirty-upper-bits case
    0xDEADBEEF00000004,  # arbitrary garbage in bits 63:32
    0xFFFFFFFF00000004,  # all high bits set; low DWORD still just 4
    0x0000000200000004,
])
def test_writefile_masks_r8_to_dword_and_writes_only_low_count(harness, len64):
    """A dirty upper half must not reach fwrite() as a size_t (issue #345)."""
    out, err = invoke(harness, "wr", hex(len64), "41424344")
    assert out == "1 4"       # TRUE, exactly four bytes accounted for
    assert err == "ABCD"      # only the declared four bytes are read


def test_writefile_normal_write_reports_success_and_byte_count(harness):
    out, err = invoke(harness, "wr", "5", "48454c4c4f")
    assert out == "1 5"
    assert err == "HELLO"


def test_writefile_rejects_null_buffer_for_nonzero_dword(harness):
    """NULL lpBuffer with a non-zero count must fail without calling fwrite."""
    out, err = invoke(harness, "wr", "0x0000000100000008", "-")
    assert out == "0 0"       # BOOL FALSE, zero bytes written
    assert err == ""


@pytest.mark.parametrize("len64,buf", [
    ("0", "41424344"),
    ("0", "-"),
    ("0x0000000100000000", "41424344"),
])
def test_writefile_zero_length_succeeds_without_touching_buffer(harness, len64, buf):
    out, err = invoke(harness, "wr", len64, buf)
    assert out == "1 0"
    assert err == ""


def test_writefile_tolerates_null_bytes_written_pointer(harness):
    """R9 is optional; a zero R9 must never be dereferenced."""
    out, err = invoke(harness, "wrn", "0xDEADBEEF00000004", "41424344")
    assert out == "1"
    assert err == "ABCD"


def test_source_uses_dword_length_and_no_unguarded_fwrite(harness):
    source = HOST64.read_text(encoding="utf-8")
    assert "uint32_t n = (uint32_t)c->r8" in source
    assert "(size_t)c->r8" not in source
    assert "if (!buf)" in source
    # Sanity: the harness still sees the other host shims the repo relies on.
    assert '"WriteFile"' in source
    assert '"GetStdHandle"' in source


# --------------------------------------------------------------------------- #
# hle_enter: fail closed on misaligned / out-of-range indirect jumps.
# --------------------------------------------------------------------------- #

def test_hle_enter_dispatches_an_exact_in_range_sentinel(harness):
    target = HLE_BASE + HLE_STRIDE  # slot 1 -> GetStdHandle, returns 1
    out, _ = invoke(harness, "hle", hex(target), "1")
    assert out == "1 8"             # RAX=1, call return word consumed (RSP + 8)


@pytest.mark.parametrize("target", [
    HLE_BASE + 4,                         # misaligned inside the HLE window
    HLE_BASE - 8,                         # below the window
    HLE_BASE + HLE_STRIDE * HLE_COUNT,    # past the last slot
])
def test_hle_enter_rejects_bad_targets(harness, target):
    out, err = invoke(harness, "hle", hex(target), "1", expected_rc=96)
    assert out == ""
    assert "host64:" in err
