"""Deterministic tests for native32.c::make_tib() allocation failure (issue #347).

`native32.c` is MSVC x86 only (inline asm), so the whole file cannot be built on
a host. Each test here compiles the *real* make_tib() source, extracted verbatim
from runtime/native32/native32.c, against a tiny C driver with a VirtualAlloc
stub that can fail on demand. With the stub returning NULL, the old code wrote
t[0] through the NULL pointer and died with SIGSEGV; the fixed code returns a
named diagnostic and 0 instead. A second run with a working stub proves the
success path still lays out the TIB fields the guest ABI expects.

No game assets, no Windows host, no network.

Run:  python3 -m pytest tests/test_native32_tib_null.py -q
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "runtime" / "native32" / "native32.c"

# Compiled as plain C: no windows.h needed, just the types the real function
# uses. The stub is deliberately the only allocator make_tib can reach.
DRIVER = r"""#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t DWORD;
#define MEM_RESERVE 0x00002000u
#define MEM_COMMIT  0x00001000u
#define PAGE_READWRITE 0x40u

static int       g_fail;
static int       g_calls;
static DWORD     g_last_error;
static DWORD     g_tid = 0x1234u;
static uint32_t  g_peb = 0x7FFDF000u;
static uint32_t* g_alloc;

void* VirtualAlloc(void* addr, size_t size, DWORD type, DWORD protect) {
    (void)addr; (void)type; (void)protect;
    g_calls++;
    if (g_fail) return NULL;          /* forced failure: no memory at all */
    void* p = malloc(size ? size : 1u);
    if (p) {
        memset(p, 0, size);
        g_alloc = (uint32_t*)p;       /* keep the full host pointer for checks */
    }
    return p;
}
DWORD GetCurrentThreadId(void) { return g_tid; }
DWORD GetLastError(void) { return g_last_error; }
uint32_t __readfsdword(uint32_t off) { (void)off; return g_peb; }

/* ----- verbatim from runtime/native32/native32.c ----- */
__MAKE_TIB__
/* ----------------------------------------------------- */

int main(int argc, char** argv) {
    if (argc < 2) return 2;
    g_fail = strcmp(argv[1], "fail") == 0;
    g_last_error = g_fail ? 8u : 0u;   /* ERROR_NOT_ENOUGH_MEMORY on failure */
    uint32_t tib = make_tib(0x00401000u, 0x00801000u);
    printf("tib=0x%08X calls=%d\n", tib, g_calls);
    if (!g_fail && g_alloc) {
        /* make_tib truncates the pointer to uint32_t for the guest ABI; on
         * this 64-bit test host, inspect through the stub's full pointer. */
        const uint32_t* t = g_alloc;
        printf("addr=0x%08X exc=0x%08X base=0x%08X limit=0x%08X self=0x%08X tid=0x%08X peb=0x%08X\n",
               (uint32_t)(uintptr_t)g_alloc, t[0], t[1], t[2],
               t[0x18 / 4], t[0x24 / 4], t[0x30 / 4]);
    }
    return 0;
}
"""


def _extract_function(source, signature):
    """Return a whole C function, verbatim, from its signature to its last
    brace. Skips comments and string/char literals so braces inside them do
    not end the function early."""
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"function not found in {SRC.name}: {signature}")
    i = source.find("{", start)
    if i < 0:
        raise AssertionError(f"no body for {signature}")
    depth = 0
    state = "code"
    j = i
    while j < len(source):
        c = source[j]
        nxt = source[j + 1] if j + 1 < len(source) else ""
        if state == "code":
            if c == "/" and nxt == "*":
                state, j = "block", j + 1
            elif c == "/" and nxt == "/":
                state, j = "line", j + 1
            elif c == '"':
                state = "str"
            elif c == "'":
                state = "char"
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return source[start:j + 1]
        elif state == "block":
            if c == "*" and nxt == "/":
                state, j = "code", j + 1
        elif state == "line":
            if c == "\n":
                state = "code"
        elif state == "str":
            if c == "\\":
                j += 1
            elif c == '"':
                state = "code"
        elif state == "char":
            if c == "\\":
                j += 1
            elif c == "'":
                state = "code"
        j += 1
    raise AssertionError(f"unterminated function: {signature}")


def _run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True,
                          text=True, timeout=kw.pop("timeout", 60), **kw)


class Native32TibHarnessTests(unittest.TestCase):
    """Run the real make_tib with a VirtualAlloc stub that fails on demand."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="grc-native32-tib-")
        cls.root = Path(cls.tmp.name)
        source = SRC.read_text()
        body = _extract_function(source, "static uint32_t make_tib(uint32_t lo, uint32_t hi)")
        (cls.root / "driver.c").write_text(DRIVER.replace("__MAKE_TIB__", body))
        cc = (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
        if not cc:
            cls.tmp.cleanup()
            raise unittest.SkipTest("no C compiler available for the make_tib harness")
        flags = []
        sdk = _run(["xcrun", "--show-sdk-path"])
        if sdk.returncode == 0 and Path(sdk.stdout.strip()).is_dir():
            flags = ["-isysroot", sdk.stdout.strip()]
        # -O0 keeps the failing stub's NULL write real (and so deterministic).
        build = _run([cc, *flags, "-std=c11", "-O0", "-g", cls.root / "driver.c",
                      "-o", cls.root / "driver"])
        if build.returncode != 0:
            cls.tmp.cleanup()
            raise AssertionError(f"make_tib harness failed to build: {build.stderr}")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run_case(self, mode):
        return _run([self.root / "driver", mode])

    def test_failing_virtualalloc_returns_zero_without_dereferencing_null(self):
        result = self._run_case("fail")
        # A write through the NULL pointer would kill the process with SIGSEGV;
        # rc == 0 is the proof no such write happened.
        self.assertEqual(result.returncode, 0, result)
        self.assertNotIn("AddressSanitizer", result.stderr, result)
        self.assertRegex(result.stdout, r"tib=0x00000000 calls=1", result)
        self.assertIn("[native32] make_tib:", result.stderr, result)
        self.assertIn("VirtualAlloc(0x1000) for guest TIB failed", result.stderr, result)
        self.assertIn("error 8", result.stderr, result)  # GetLastError was read

    def test_success_control_populates_guest_tib_fields(self):
        result = self._run_case("ok")
        self.assertEqual(result.returncode, 0, result)
        self.assertRegex(result.stdout, r"tib=0x[0-9A-Fa-f]{8} calls=1", result)
        self.assertRegex(result.stdout, r"exc=0xFFFFFFFF", result)
        self.assertRegex(result.stdout, r"base=0x00801000", result)
        self.assertRegex(result.stdout, r"limit=0x00401000", result)
        self.assertRegex(result.stdout, r"tid=0x00001234", result)
        self.assertRegex(result.stdout, r"peb=0x7FFDF000", result)
        tib = re.search(r"tib=(0x[0-9A-Fa-f]{8})", result.stdout).group(1)
        addr = re.search(r"addr=(0x[0-9A-Fa-f]{8})", result.stdout).group(1)
        self.assertEqual(tib.lower(), addr.lower())  # guest-ABI TIB is the allocation
        self.assertRegex(result.stdout, rf"self={re.escape(addr)}", result)
        self.assertNotIn("[native32] make_tib:", result.stderr, result)


class Native32TibSourceTests(unittest.TestCase):
    """Structural checks on the source, including the fail-closed call site."""

    @classmethod
    def setUpClass(cls):
        cls.source = SRC.read_text()
        cls.body = _extract_function(cls.source,
                                     "static uint32_t make_tib(uint32_t lo, uint32_t hi)")
        cls.mach = _extract_function(cls.source, "void mach_enter(void)")
        cls.init = _extract_function(cls.source, "void native32_init(void)")

    def _failure_branch(self):
        m = re.search(r"if\s*\(\s*!\s*t\s*\)\s*\{(?P<body>.*?)\n\s*\}",
                      self.body, re.S)
        self.assertIsNotNone(m, "no immediate NULL check of VirtualAlloc's result")
        return m.group("body")

    def test_null_check_is_before_every_tib_write(self):
        alloc = self.body.index("VirtualAlloc(")
        check = self.body.index("if (!t)")
        first_write = self.body.index("t[0]")
        self.assertLess(alloc, check)
        self.assertLess(check, first_write)

    def test_failure_branch_has_named_diagnostic_and_returns_zero(self):
        branch = self._failure_branch()
        self.assertIn("fprintf", branch)
        self.assertIn("[native32] make_tib:", branch)
        self.assertIn("VirtualAlloc", branch)
        self.assertIn("return 0", branch)

    def test_guest_last_error_path_is_not_read_on_success(self):
        # native_bridge() carries the guest's last error across mach_enter();
        # only the failure branch may call GetLastError, which also proves the
        # guest ABI error path is preserved.
        self.assertEqual(len(re.findall(r"GetLastError\s*\(", self.body)), 1)
        self.assertIn("GetLastError", self._failure_branch())

    def test_mach_enter_fails_closed_before_publishing_a_zero_fs_base(self):
        call = self.mach.index("make_tib(")
        guard = re.search(r"if\s*\(\s*!\s*m->r\.fs\s*\)", self.mach[call:])
        self.assertIsNotNone(guard, "make_tib()'s failure is ignored by mach_enter")
        guard_at = call + guard.start()
        tls_at = self.mach.index("TlsSetValue")
        self.assertLess(call, guard_at)
        self.assertLess(guard_at, tls_at)  # no lifted code runs with fs base 0
        self.assertIn("ExitProcess", self.mach[guard_at:tls_at])

    def test_nearby_virtualalloc_calls_are_checked(self):
        # The issue asks for an audit of adjacent VirtualAlloc calls.
        self.assertIn("if (!lo)", self.mach)             # guest stack
        self.assertIn("for guest stack failed", self.mach)
        self.assertIn("if (!g_shim_page)", self.init)    # shim page
        self.assertIn("for shim page failed", self.init)

    def test_native_bridge_guest_error_round_trip_is_preserved(self):
        self.assertIn("DWORD guest_error = GetLastError();", self.source)
        self.assertIn("SetLastError(guest_error);", self.source)
        self.assertIn("DWORD last_error = GetLastError();", self.source)
        self.assertIn("SetLastError(last_error);", self.source)


if __name__ == "__main__":
    unittest.main()
