"""Regression tests for issue #346: GS segment bounds and per-thread TEB.

Two independent layers:

  1. Source guards. The wrap-prone `off + width <= sizeof g_teb` check must be
     gone, and the simulated TEB must be thread-local with a portable TLS macro
     (C11 / MSVC / GNU) while the PEB remains process-wide.

  2. A real native build. `cpu64.c` is compiled with a tiny driver that stubs
     the generated dispatch table, then exercised from two host threads.

The runtime checks are deterministic, not probabilistic. A guest GS offset near
UINT64_MAX makes the OLD code pass `off + width <= 0x1000` and index
`g_teb - n`; for width 4/8 that 4/8-byte window always still covers
`g_teb[0..]`. Seeding offset 0x00 with an all-nonzero qword therefore makes a
bad read return a nonzero value and a bad zero-write clobber the seed -- both
fail loudly on the old code. A second, barrier-synchronised pair of threads
proves the TEB is per-thread: with a shared buffer at most one thread can still
see its own gs:[0x48] value after both have written.

Run with: python3 -m pytest tests/test_cpu64_gs_bounds.py -q
"""

import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CPU64_C = ROOT / "runtime" / "recomp64_cpu" / "cpu64.c"

# cpu64.c includes a dispatch header that generate64.py normally emits. This is
# the smallest shape that satisfies the include and lets the file link alone.
STUB_DISPATCH_HEADER = r"""
#pragma once
#include "cpu64.h"
typedef struct { uint64_t va; void (*fn)(CPU *); } recomp_entry_t;
extern const recomp_entry_t recomp_dispatch_table[];
extern const uint32_t recomp_dispatch_count;
#define HLE_BASE   0xE5E30000ull
#define HLE_STRIDE 8
#define HLE_COUNT  1
#define HLE_SENTINEL(i) (HLE_BASE + (uint64_t)(i) * HLE_STRIDE)
typedef struct { uint64_t slot; const char *dll; const char *name; } hle_import_t;
extern const hle_import_t recomp_imports[];
"""

DRIVER = r"""
#include "cpu64.h"
#include "recomp_dispatch.h"

#include <pthread.h>
#include <stdio.h>
#include <stdint.h>

/* Minimal generated-boundary symbols so cpu64.c links without a full lift. */
const recomp_entry_t recomp_dispatch_table[1] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;
const hle_import_t recomp_imports[1] = { { 0, "KERNEL32.dll", "ExitProcess" } };

static int g_fails;
#define FAIL() __atomic_fetch_add(&g_fails, 1, __ATOMIC_RELAXED)
#define CHECK(c) do { if (!(c)) { \
    fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); FAIL(); } } while (0)

#define SEED  UINT64_C(0x1122334455667788)  /* every byte nonzero */
#define SEED2 UINT64_C(0x8877665544332211)
#define ID_A  UINT64_C(0xAAAAAAAAAAAAAAAA)
#define ID_B  UINT64_C(0xBBBBBBBBBBBBBBBB)

/* #346: off + width wraps near UINT64_MAX. Reads must fail closed and writes
 * must be no-ops, without ever indexing before the TEB. */
static void test_bounds(void)
{
    cpu64_wr_gs64(0x00, SEED);
    CHECK(cpu64_rd_gs64(0x00) == SEED);

    /* Old check: off + 8 == 7, off + 8 == 4, off + 4 == 3 -- all <= 0x1000. */
    CHECK(cpu64_rd_gs64(UINT64_MAX) == 0);
    CHECK(cpu64_rd_gs64(UINT64_MAX - 4) == 0);
    CHECK(cpu64_rd_gs32(UINT64_MAX) == 0);

    /* Old zero-writes land at g_teb-1 / g_teb-3 and clobber g_teb[0..6] /
     * g_teb[0..2]; the seed at offset 0 must survive both. */
    cpu64_wr_gs64(UINT64_MAX, 0);
    CHECK(cpu64_rd_gs64(0x00) == SEED);
    cpu64_wr_gs32(UINT64_MAX, 0);
    CHECK(cpu64_rd_gs64(0x00) == SEED);

    /* 0x1000-4 has room for a dword but not a qword: rejected, no-op. */
    CHECK(cpu64_rd_gs64(0x1000 - 4) == 0);
    cpu64_wr_gs64(0x1000 - 4, SEED2);
    CHECK(cpu64_rd_gs64(0x00) == SEED);

    /* The real top-of-TEB offsets still round-trip: no over-rejection. */
    cpu64_wr_gs64(0x1000 - 8, SEED2);
    CHECK(cpu64_rd_gs64(0x1000 - 8) == SEED2);
    cpu64_wr_gs32(0x1000 - 4, 0xCAFEBABEu);
    CHECK(cpu64_rd_gs32(0x1000 - 4) == 0xCAFEBABEu);
}

typedef struct {
    pthread_mutex_t *mu;
    pthread_cond_t  *cv;
    int             *ready;
    uint64_t         id;
    uint64_t         zero_seen;
    uint64_t         peb_seen;
} targ;

static void *thread_body(void *p)
{
    targ *t = (targ *)p;
    t->zero_seen = cpu64_rd_gs64(0x00);  /* fresh TEB: no main-thread seed */
    t->peb_seen  = cpu64_rd_gs64(0x30);  /* the shared PEB, same for all */
    cpu64_wr_gs64(0x48, t->id);          /* per-thread ThreadID slot */

    pthread_mutex_lock(t->mu);
    (*t->ready)++;
    pthread_cond_broadcast(t->cv);
    while (*t->ready < 2) pthread_cond_wait(t->cv, t->mu);
    pthread_mutex_unlock(t->mu);

    /* Both writes happened. A shared TEB would let one clobber the other. */
    CHECK(cpu64_rd_gs64(0x48) == t->id);
    return 0;
}

static void test_teb_isolation(void)
{
    pthread_mutex_t mu;
    pthread_cond_t cv;
    int ready = 0;
    pthread_t ta, tb;
    targ a, b;

    cpu64_wr_gs64(0x00, SEED);
    uint64_t main_peb = cpu64_rd_gs64(0x30);

    pthread_mutex_init(&mu, NULL);
    pthread_cond_init(&cv, NULL);
    a.mu = &mu; a.cv = &cv; a.ready = &ready; a.id = ID_A;
    b.mu = &mu; b.cv = &cv; b.ready = &ready; b.id = ID_B;

    pthread_create(&ta, NULL, thread_body, &a);
    pthread_create(&tb, NULL, thread_body, &b);
    pthread_join(ta, NULL);
    pthread_join(tb, NULL);

    CHECK(a.zero_seen == 0);
    CHECK(b.zero_seen == 0);
    CHECK(a.peb_seen != 0 && a.peb_seen == main_peb);
    CHECK(b.peb_seen != 0 && b.peb_seen == main_peb);
    /* Main's TEB was untouched by either child. */
    CHECK(cpu64_rd_gs64(0x00) == SEED);
    CHECK(cpu64_rd_gs64(0x48) == 0);

    pthread_cond_destroy(&cv);
    pthread_mutex_destroy(&mu);
}

int main(void)
{
    test_bounds();
    test_teb_isolation();
    if (g_fails) {
        fprintf(stderr, "%d check(s) failed\n", g_fails);
        return 1;
    }
    puts("cpu64 GS bounds + TLS: OK");
    return 0;
}
"""


class Cpu64GsSourceTests(unittest.TestCase):
    def test_no_wrapping_bounds_checks(self):
        text = CPU64_C.read_text()
        self.assertNotIn("off + 8 <=", text, "wrap-prone qword bounds check remains")
        self.assertNotIn("off + 4 <=", text, "wrap-prone dword bounds check remains")
        self.assertIn("cpu64_gs_ok(off, 8)", text)
        self.assertIn("cpu64_gs_ok(off, 4)", text)
        self.assertIn("sizeof(g_teb) - width", text,
                      "bounds must compare against the last valid offset")

    def test_teb_thread_local_peb_process_wide(self):
        text = CPU64_C.read_text()
        self.assertIn("#define CPU64_TLS", text)
        for guard in ("_Thread_local", "__declspec(thread)", "__thread"):
            self.assertIn(guard, text, f"missing portable TLS guard: {guard}")
        self.assertIn("static CPU64_TLS uint8_t g_teb[0x1000]", text)
        self.assertIn("static CPU64_TLS int g_teb_ready", text,
                      "the init guard must be thread-local too")
        self.assertIn("static uint8_t g_peb[0x400]", text,
                      "the PEB stays process-wide")
        self.assertNotIn("CPU64_TLS uint8_t g_peb", text,
                         "PEB must not be made thread-local")


class Cpu64GsRuntimeTests(unittest.TestCase):
    def test_gs_bounds_and_teb_isolation(self):
        with tempfile.TemporaryDirectory(prefix="cpu64-gs-") as tmp:
            d = Path(tmp)
            (d / "recomp_dispatch.h").write_text(STUB_DISPATCH_HEADER)
            (d / "driver.c").write_text(textwrap.dedent(DRIVER))
            exe = d / "gs_test"
            cmd = ["cc", "-w", "-O0", "-std=c11", f"-I{d}",
                   f"-I{CPU64_C.parent}", str(CPU64_C), str(d / "driver.c"),
                   "-o", str(exe), "-lpthread"]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            self.assertEqual(p.returncode, 0,
                             f"compile failed:\n{p.stdout}\n{p.stderr}")
            r = subprocess.run([str(exe)], capture_output=True, text=True,
                               timeout=30)
            self.assertEqual(r.returncode, 0,
                             f"runtime checks failed:\n{r.stdout}\n{r.stderr}")
            self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
