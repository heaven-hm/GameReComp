"""Deterministic bounds/ABI tests for the Win64 UCRT shims.

Covers four issues in runtime/recomp64_cpu/win64_shims.c:

* #331 - EMIT() passed snprintf()'s raw (possibly > buffer / negative) length
  to out_write(), over-reading the 1024-byte host stack buffer.
* #342 - vfmt() defaulted integer width to 8, feeding upper garbage from the
  Win64 vararg slot into plain %d/%u/%x.
* #341 - TlsAlloc() always returned slot 3 and g_tls[64] truncated 64-bit
  pointers to int32_t.
* #340 - vscan() had an unbounded %s/%[...] stack buffer, never detected '%*'
  suppression, and mismatched fscanf target types for %f/%lf and integers.

The tests compile the real win64_shims.c into a small AddressSanitizer harness
and drive the actual static functions, so a regression is a real ASan report or
a wrong byte stream - not a source-text assertion.

Run: python3 -m pytest tests/test_win64_emit_bounds.py -q
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "recomp64_cpu"

# A minimal stand-in for the per-PE generated recomp_dispatch.h. win64_shims.c
# only needs HLE_COUNT, hle_import_t, recomp_imports[] and dispatch() to link.
STUB_DISPATCH_H = r"""#pragma once
#include "cpu64.h"
#define HLE_COUNT 5
typedef struct { uint64_t slot; const char *dll; const char *name; } hle_import_t;
extern const hle_import_t recomp_imports[];
void dispatch(CPU *c, uint64_t va);
"""

# Include the .c rather than a copy: the tests must exercise the shipped code.
HARNESS_C = r"""#include "recomp_dispatch.h"
#include "win64_shims.c"

int64_t g_image_delta = 0;
void dispatch(CPU *c, uint64_t va) { (void)c; (void)va; }
const hle_import_t recomp_imports[] = {
    { 0, "msvcrt.dll", "__stdio_common_vfprintf" },
    { 1, "KERNEL32.dll", "TlsAlloc" },
    { 2, "KERNEL32.dll", "TlsSetValue" },
    { 3, "KERNEL32.dll", "TlsGetValue" },
    { 4, "KERNEL32.dll", "TlsFree" },
};
enum { IMP_FMT, IMP_TLSALLOC, IMP_TLSSET, IMP_TLSGET, IMP_TLSFREE };

static uint64_t g_va[16];
static uint64_t g_stack[16];
static int g_count;
static char g_sink[8192];

/* Win64 va_list is a pointer into the stack-argument area at rsp+40. */
static void setup(CPU *c, const char *fmt, FILE *stream) {
    memset(c, 0, sizeof *c);
    memset(g_va, 0, sizeof g_va);
    memset(g_stack, 0, sizeof g_stack);
    memset(g_sink, 0, sizeof g_sink);
    g_count = -1;
    g_stack[5] = (uint64_t)(uintptr_t)g_va;
    c->rsp = (uint64_t)(uintptr_t)g_stack;
    c->rcx = 0;                                   /* options */
    c->rdx = (uint64_t)(uintptr_t)&g_iob[1];       /* stream (unused by vfmt) */
    c->r8  = (uint64_t)(uintptr_t)fmt;             /* format */
    c->r9  = 0;                                   /* locale */
    (void)stream;
}

static void tls_call(CPU *c, int imp, uint64_t a0, uint64_t a1) {
    memset(c, 0, sizeof *c);
    c->rsp = (uint64_t)(uintptr_t)g_stack;
    c->rcx = a0; c->rdx = a1;
    win64_hle_invoke(c, (uint32_t)imp);
}

static int scan(CPU *c, const char *fmt, const char *input, size_t len,
                uint64_t a0, uint64_t a1) {
    FILE *in = fmemopen((void *)input, len, "r");
    if (!in) return -99;
    setup(c, fmt, in);
    g_va[0] = a0; g_va[1] = a1;
    int got = vscan(c, in, 0);
    fclose(in);
    return got;
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    const char *scenario = argv[1];
    FILE *out = fopen(argv[2], "wb");
    if (!out) return 2;
    static CPU c;
    int ret = 0;

    /* #331 - oversized / negative snprintf lengths */
    if (!strcmp(scenario, "s4096")) {
        static char big[4097];
        memset(big, 'Z', 4096); big[4096] = 0;
        setup(&c, "%s", out);
        g_va[0] = (uint64_t)(uintptr_t)big;
        ret = vfmt(&c, out, 0);
    } else if (!strcmp(scenario, "width2000")) {
        setup(&c, "%02000d", out);
        g_va[0] = 42;
        ret = vfmt(&c, out, 0);
    } else if (!strcmp(scenario, "exact1023")) {
        static char exact[1024];
        memset(exact, 'Q', 1023); exact[1023] = 0;
        setup(&c, "%s", out);
        g_va[0] = (uint64_t)(uintptr_t)exact;
        ret = vfmt(&c, out, 0);
    } else if (!strcmp(scenario, "pct_n")) {
        static char big[4097];
        memset(big, 'Z', 4096); big[4096] = 0;
        setup(&c, "%s%n", out);
        g_va[0] = (uint64_t)(uintptr_t)big;
        g_va[1] = (uint64_t)(uintptr_t)&g_count;
        ret = vfmt(&c, out, 0);
        fprintf(stderr, "COUNT=%d\n", g_count);
    } else if (!strcmp(scenario, "neg")) {
        setup(&c, "", out);
        ret = emit_bounded(out, "abc", 3, -1);
    } else if (!strcmp(scenario, "heap_clamp")) {
        char *heap = (char *)malloc(1024);
        if (!heap) return 2;
        memset(heap, 'H', 1024);
        setup(&c, "", out);
        ret = emit_bounded(out, heap, 1023, 4096);
        free(heap);

    /* #342 - plain %d/%u/%x must ignore upper garbage in the vararg slot */
    } else if (!strcmp(scenario, "int_garbage")) {
        uint64_t v = 0xDEADBEEF12345678ULL;
        setup(&c, "%d|%u|%x|%lld|%I64u|%ld|%hd|%hhd", out);
        for (int k = 0; k < 8; k++) g_va[k] = v;
        ret = vfmt(&c, out, 0);
    } else if (!strcmp(scenario, "int_negative")) {
        setup(&c, "%d|%u|%lld", out);
        for (int k = 0; k < 3; k++) g_va[k] = ~(uint64_t)0;   /* -1 */
        ret = vfmt(&c, out, 0);

    /* #341 - distinct TLS indices and full 64-bit values */
    } else if (!strcmp(scenario, "tls")) {
        uint64_t p1 = 0x1400123456789AABULL, p2 = 0x00007FF612345678ULL;
        uint32_t i1, i2, i3;
        tls_call(&c, IMP_TLSALLOC, 0, 0); i1 = (uint32_t)c.rax;
        tls_call(&c, IMP_TLSALLOC, 0, 0); i2 = (uint32_t)c.rax;
        fprintf(stderr, "ALLOC=%u,%u\n", i1, i2);
        tls_call(&c, IMP_TLSSET, i1, p1);
        tls_call(&c, IMP_TLSSET, i2, p2);
        tls_call(&c, IMP_TLSGET, i1, 0);
        fprintf(stderr, "GET1=%llx\n", (unsigned long long)c.rax);
        tls_call(&c, IMP_TLSGET, i2, 0);
        fprintf(stderr, "GET2=%llx\n", (unsigned long long)c.rax);
        tls_call(&c, IMP_TLSFREE, i1, 0);
        fprintf(stderr, "FREE=%u\n", (uint32_t)c.rax);
        tls_call(&c, IMP_TLSGET, i1, 0);
        fprintf(stderr, "AFTER=%llx\n", (unsigned long long)c.rax);
        tls_call(&c, IMP_TLSALLOC, 0, 0); i3 = (uint32_t)c.rax;
        fprintf(stderr, "REALLOC=%u\n", i3);
    } else if (!strcmp(scenario, "tls_exhaust")) {
        uint32_t bad_at = 0;
        for (uint32_t k = 0; k < 80; k++) {
            tls_call(&c, IMP_TLSALLOC, 0, 0);
            if ((uint32_t)c.rax == 0xFFFFFFFFu) { bad_at = k; break; }
        }
        fprintf(stderr, "EXHAUST=%u\n", bad_at);
        tls_call(&c, IMP_TLSSET, 0xFFFFFFFFu, 0x1234);
        fprintf(stderr, "BADSET=%u\n", (uint32_t)c.rax);
        tls_call(&c, IMP_TLSSET, 9999, 0x1234);
        fprintf(stderr, "OOBSET=%u\n", (uint32_t)c.rax);

    /* #340 - suppression, bounded strings, float types, integer widths */
    } else if (!strcmp(scenario, "scan_suppress")) {
        static int dst;
        ret = scan(&c, "%*d %d", "111 42", 6,
                   (uint64_t)(uintptr_t)&dst, 0);
        fprintf(stderr, "DST=%d\n", dst);
    } else if (!strcmp(scenario, "scan_suppress_s")) {
        ret = scan(&c, "%*s %s", "skip keep", 9,
                   (uint64_t)(uintptr_t)g_sink, 0);
        fprintf(stderr, "STR=%s\n", g_sink);
    } else if (!strcmp(scenario, "scan_string") || !strcmp(scenario, "scan_string_width")) {
        static char input[5001];
        memset(input, 'A', 5000); input[5000] = 0;
        ret = strcmp(scenario, "scan_string") == 0
            ? scan(&c, "%s", input, 5000, (uint64_t)(uintptr_t)g_sink, 0)
            : scan(&c, "%10000s", input, 5000, (uint64_t)(uintptr_t)g_sink, 0);
        fprintf(stderr, "LEN=%zu HEAD=%c TAIL=%c NUL=%d\n",
                strlen(g_sink), g_sink[0], g_sink[4094], g_sink[4095]);
    } else if (!strcmp(scenario, "scan_set")) {
        static char input[5001];
        memset(input, 'a', 5000); input[5000] = 0;
        ret = scan(&c, "%[a]", input, 5000, (uint64_t)(uintptr_t)g_sink, 0);
        fprintf(stderr, "LEN=%zu HEAD=%c\n", strlen(g_sink), g_sink[0]);
    } else if (!strcmp(scenario, "scan_float")) {
        static float f;
        static double d;
        setup(&c, "", out);
        g_va[0] = (uint64_t)(uintptr_t)&f;
        g_va[1] = (uint64_t)(uintptr_t)&d;
        {
            FILE *in = fmemopen("1.5 2.25", 8, "r");
            if (!in) return 2;
            c.r8 = (uint64_t)(uintptr_t)"%f %lf";
            ret = vscan(&c, in, 0);
            fclose(in);
        }
        fprintf(stderr, "F=%a D=%a\n", (double)f, d);
    } else if (!strcmp(scenario, "scan_int")) {
        static int i32;
        static long long i64;
        setup(&c, "", out);
        g_va[0] = (uint64_t)(uintptr_t)&i32;
        g_va[1] = (uint64_t)(uintptr_t)&i64;
        {
            FILE *in = fmemopen("-1 -1234567890123", 17, "r");
            if (!in) return 2;
            c.r8 = (uint64_t)(uintptr_t)"%d %lld";
            ret = vscan(&c, in, 0);
            fclose(in);
        }
        fprintf(stderr, "I32=%d I64=%lld\n", i32, i64);
    } else {
        return 2;
    }

    fprintf(stderr, "RET=%d\n", ret);
    fclose(out);
    return 0;
}
"""


def _sdk_path():
    try:
        r = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True,
                           text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _compiler_candidates():
    sdk = _sdk_path() if sys.platform == "darwin" else None
    sysroot = ["-isysroot", sdk] if sdk else []
    if sys.platform == "darwin":
        # Apple clang's ASan runtime is unreliable on current macOS; the
        # Homebrew toolchain is the one this repository uses for x64 builds.
        return [("/opt/homebrew/opt/llvm/bin/clang", sysroot),
                (shutil.which("clang") or "clang", sysroot),
                (shutil.which("gcc") or "gcc", [])]
    return [(shutil.which("clang") or "clang", []),
            (shutil.which("gcc") or "gcc", [])]


def _probe(compiler, extra):
    """Require a compiler whose ASan really detects a deliberate over-read."""
    with tempfile.TemporaryDirectory(prefix="emit-asan-probe-") as d:
        src = Path(d) / "probe.c"
        exe = Path(d) / "probe"
        src.write_text(
            "#include <string.h>\n"
            "int main(void) {\n"
            "    char b[4]; memset(b, 'A', 4);\n"
            "    volatile int s = 0; for (int i = 0; i < 8; i++) s += b[i];\n"
            "    return s == 0;\n"
            "}\n")
        cmd = [compiler, *extra, "-fsanitize=address", "-O0", "-w",
               str(src), "-o", str(exe)]
        try:
            if subprocess.run(cmd, capture_output=True, timeout=60).returncode:
                return False
            env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:abort_on_error=0")
            env.pop("X64_SHIM_TRACE", None)
            r = subprocess.run([str(exe)], capture_output=True, text=True,
                               env=env, timeout=30)
            return r.returncode != 0 and "AddressSanitizer" in r.stderr
        except (OSError, subprocess.SubprocessError):
            return False


def _asan_compiler():
    for compiler, extra in _compiler_candidates():
        if not (Path(compiler).is_file() or shutil.which(compiler)):
            continue
        if _probe(compiler, extra):
            return compiler, extra
    return None


def _compile_harness(tmp):
    compiler, extra = _asan_compiler()
    (tmp / "recomp_dispatch.h").write_text(STUB_DISPATCH_H)
    src = tmp / "harness.c"
    src.write_text(HARNESS_C)
    exe = tmp / "harness"
    cmd = [compiler, *extra, "-fsanitize=address", "-O0", "-w",
           "-I" + str(tmp), "-I" + str(RUNTIME), str(src), "-o", str(exe)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"harness compile failed:\n{r.stdout}\n{r.stderr}"
    return exe


def _run(exe, scenario, tmp):
    out = tmp / f"{scenario}.bin"
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:abort_on_error=0")
    env.pop("X64_SHIM_TRACE", None)   # keep the shim's debug trace off
    r = subprocess.run([str(exe), scenario, str(out)], capture_output=True,
                      text=True, env=env, timeout=60)
    assert "AddressSanitizer" not in r.stderr, (
        f"{scenario}: ASan reported a memory error:\n{r.stderr}")
    assert r.returncode == 0, f"{scenario}: exit {r.returncode}\n{r.stderr}"
    data = out.read_bytes()
    fields = {}
    for line in r.stderr.splitlines():
        for token in line.split():
            if "=" in token:
                k, v = token.split("=", 1)
                fields[k] = v
    return data, fields


class _Harness:
    @classmethod
    def setup_class(cls):
        import pytest
        if _asan_compiler() is None:
            pytest.skip("no C compiler with a working AddressSanitizer runtime")
        cls._tmp = tempfile.TemporaryDirectory(prefix="test-win64-shims-")
        cls.tmp = Path(cls._tmp.name)
        cls.exe = _compile_harness(cls.tmp)

    @classmethod
    def teardown_class(cls):
        cls._tmp.cleanup()

    def run(self, scenario):
        return _run(self.exe, scenario, self.tmp)


class TestEmitBounds(_Harness):
    """#331 - snprintf() length must be clamped before out_write()."""

    def test_oversized_string_is_clamped_without_overread(self):
        data, f = self.run("s4096")
        assert data == b"Z" * 1023
        assert f["RET"] == "1023"

    def test_oversized_width_is_clamped_without_overread(self):
        data, f = self.run("width2000")
        assert data == b"0" * 1023
        assert f["RET"] == "1023"

    def test_exact_capacity_is_not_truncated(self):
        data, f = self.run("exact1023")
        assert data == b"Q" * 1023
        assert f["RET"] == "1023"

    def test_percent_n_counts_only_emitted_bytes(self):
        data, f = self.run("pct_n")
        assert len(data) == 1023
        assert f["RET"] == f["COUNT"] == "1023"

    def test_negative_snprintf_result_writes_nothing(self):
        data, f = self.run("neg")
        assert data == b""
        assert f["RET"] == "0"

    def test_heap_buffer_is_never_read_past_capacity(self):
        data, f = self.run("heap_clamp")
        assert data == b"H" * 1023
        assert f["RET"] == "1023"


class TestIntegerWidth(_Harness):
    """#342 - plain conversions take only the low 32 bits of a vararg slot."""

    def test_upper_garbage_is_ignored_for_int_but_kept_for_ll(self):
        data, f = self.run("int_garbage")
        v = 0xDEADBEEF12345678
        s64 = v - (1 << 64) if v >= (1 << 63) else v
        expected = (f"{v & 0xFFFFFFFF}|{v & 0xFFFFFFFF}|{v & 0xFFFFFFFF:x}|"
                    f"{s64}|{v}|{v & 0xFFFFFFFF}|{(v & 0xFFFF)}|{(v & 0xFF)}")
        assert data.decode() == expected
        assert f["RET"] == str(len(data))

    def test_negative_values_keep_their_sign_at_each_width(self):
        data, f = self.run("int_negative")
        assert data.decode() == "-1|4294967295|-1"
        assert f["RET"] == str(len(data))


class TestTls(_Harness):
    """#341 - distinct indices, full 64-bit values and proper exhaustion."""

    def test_distinct_indices_and_full_width_values(self):
        _data, f = self.run("tls")
        i1, i2 = (int(x) for x in f["ALLOC"].split(","))
        assert i1 != i2
        assert i1 != 3 or i2 != 3                 # never a constant slot
        assert f["GET1"] == "1400123456789aab"
        assert f["GET2"] == "7ff612345678"
        assert f["FREE"] == "1"
        assert f["AFTER"] == "0"                  # freed slot is zeroed
        assert int(f["REALLOC"]) == i1            # slot is reusable

    def test_exhaustion_and_invalid_indices(self):
        _data, f = self.run("tls_exhaust")
        assert f["EXHAUST"] == "64"
        assert f["BADSET"] == "0"
        assert f["OOBSET"] == "0"


class TestScan(_Harness):
    """#340 - bounded fields, real suppression and exact fscanf types."""

    def test_suppressed_integer_does_not_consume_a_vararg(self):
        _data, f = self.run("scan_suppress")
        assert f["RET"] == "1"                    # only the assigned conversion
        assert f["DST"] == "42"

    def test_suppressed_string_does_not_consume_a_vararg(self):
        _data, f = self.run("scan_suppress_s")
        assert f["RET"] == "1"
        assert f["STR"] == "keep"

    def test_oversized_string_is_clamped(self):
        _data, f = self.run("scan_string")
        assert f["RET"] == "1"
        assert f["LEN"] == "4095"
        assert f["HEAD"] == f["TAIL"] == "A"
        assert f["NUL"] == "0"

    def test_explicit_oversized_width_is_clamped(self):
        _data, f = self.run("scan_string_width")
        assert f["RET"] == "1"
        assert f["LEN"] == "4095"

    def test_oversized_scan_set_is_clamped(self):
        _data, f = self.run("scan_set")
        assert f["RET"] == "1"
        assert f["LEN"] == "4095"
        assert f["HEAD"] == "a"

    def test_float_and_double_use_matching_fscanf_types(self):
        _data, f = self.run("scan_float")
        assert f["RET"] == "2"
        assert float.fromhex(f["F"]) == 1.5
        assert float.fromhex(f["D"]) == 2.25

    def test_negative_int_and_ll_keep_their_sign(self):
        _data, f = self.run("scan_int")
        assert f["RET"] == "2"
        assert f["I32"] == "-1"
        assert f["I64"] == "-1234567890123"
