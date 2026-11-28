"""Regression for issue #343: ns_libc.c::gformat() / sh_printf() memory safety.

The real static functions are extracted from runtime/nextstep/ns_libc.c and
compiled into a tiny POSIX harness.  Two host buffers that the original code
over-ran are placed immediately before PROT_NONE guard pages:

  * g_fmtbuf[65536] -- an unclamped ``fwrite(g_fmtbuf, 1, n, ...)`` on a
    truncated (>64 KiB) format walks into the guard page.
  * spec[32]       -- a crafted format with several ``*`` width/precision
    arguments overflows the stack spec buffer in the original code.

The harness replaces the caller's fwrite with a counter that touches exactly
the requested number of bytes, and replaces the stack ``spec`` array with a
guarded pointer (the bounds logic is otherwise compiled verbatim).  No
compiler sanitizer is required; a bad length faults deterministically.

The tests also assert the source-level bounds checks so a future "fix" that
only changes the harness cannot pass.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NS_LIBC_C = ROOT / 'runtime' / 'nextstep' / 'ns_libc.c'

GFORMAT_SIG = 'static int gformat(char *out, size_t cap, const char *fmt, uint32_t ap) {'
FMT_WRITE_LEN_SIG = 'static size_t fmt_write_len(int n, size_t cap) {'
SH_PRINTF_SIG = 'static void sh_printf(void) {'
SH_SPRINTF_SIG = 'static void sh_sprintf(void) {'

HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

/* The extracted gformat uses `sizeof spec`; the harness swaps the stack array
 * for a guarded pointer, so capture the intended capacity as a macro. */
#define SPEC_CAP 32
/* Separate RW islands, each ending at a guard page:
 *   0x0000..0x14000   stack / %n target / g_fmtbuf[65536]
 *   0x18000..0x19000  spec[32] (placed at the very end)
 *   0x20000..0x40000  format string
 *   0x60000..0x80000  sprintf destination
 * The format must not overlap g_fmtbuf, or gformat would read back the bytes
 * it just wrote and the would-be length would grow without bound. */
#define GUEST_MAP_SIZE 0x80000u
#define FMTBUF_VA 0x4000u
#define FMTBUF_CAP 0x10000u
#define FMT_VA 0x20000u
#define DEST_VA 0x60000u
#define NTARGET_VA 0x3000u
#define ESP_VA 0x0800u

static uintptr_t g_mem_base;
static char *g_harness_fmtbuf;
static char *g_harness_spec;
static size_t g_fmtbuf_cap = FMTBUF_CAP;
static uint32_t g_esp, g_eax;

/* Guest VAs are 32-bit offsets from g_mem_base (RECOMP_FLAT_MEMORY semantics). */
#define ADDR(va) ((uintptr_t)(uint32_t)(va) + g_mem_base)
#define MEM32(addr) (*(volatile uint32_t *)ADDR(addr))
#define MEM64(addr) (*(volatile uint64_t *)ADDR(addr))
#define MEMD(addr)  (*(volatile double   *)ADDR(addr))
#define GSTR(va) ((char *)ADDR(va))
#define ARG(n) MEM32(g_esp + 4 + 4 * (n))
#define NS_RET(v) do { g_eax = (uint32_t)(v); g_esp += 4; } while (0)
#define g_fmtbuf g_harness_fmtbuf

static size_t g_fwrite_calls, g_fwrite_max;
#define fwrite harness_fwrite
static size_t harness_fwrite(const void *p, size_t sz, size_t n, FILE *fp) {
    volatile unsigned char sink = 0;
    const unsigned char *q = (const unsigned char *)p;
    size_t i;
    (void)sz; (void)fp;
    g_fwrite_calls++;
    if (n > g_fwrite_max) g_fwrite_max = n;
    /* Touch every byte the caller asked for; an unclamped n faults. */
    for (i = 0; i < n; ++i) sink = q[i];
    (void)sink;
    return n;
}

__GFORMAT__
__FMT_WRITE_LEN__
__SH_PRINTF__
__SH_SPRINTF__

int main(int argc, char **argv) {
    long ps = sysconf(_SC_PAGESIZE);
    size_t page;
    void *map;
    const char *mode;
    if (argc < 2) return 90;
    mode = argv[1];
    if (ps <= 0) return 91;
    page = (size_t)ps;
    map = mmap(NULL, GUEST_MAP_SIZE, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (map == MAP_FAILED) return 92;
    /* RW up to 0x14000: g_fmtbuf ends exactly at a guard page. */
    if (mprotect(map, 0x14000, PROT_READ | PROT_WRITE) != 0) return 93;
    /* RW one page at 0x18000; spec ends exactly at the following guard page. */
    if (mprotect((char *)map + 0x18000, page, PROT_READ | PROT_WRITE) != 0) return 94;
    /* Format string and sprintf destination live in their own islands. */
    if (mprotect((char *)map + FMT_VA, 0x20000, PROT_READ | PROT_WRITE) != 0) return 95;
    if (mprotect((char *)map + DEST_VA, 0x20000, PROT_READ | PROT_WRITE) != 0) return 96;
    g_mem_base = (uintptr_t)map;
    g_harness_fmtbuf = (char *)ADDR(FMTBUF_VA);
    g_harness_spec = (char *)ADDR(0x18000u + page - SPEC_CAP);
    g_esp = ESP_VA;

    if (!strcmp(mode, "long")) {
        long want = strtol(argv[2], NULL, 10);
        long i;
        char *f = GSTR(FMT_VA);
        if (want < 0 || want > 400000) return 101;
        for (i = 0; i < want; ++i) f[i] = 'A';
        f[want] = 0;
        MEM32(g_esp + 4) = FMT_VA;                  /* ARG(0) = format */
        sh_printf();
        printf("ret=%d calls=%zu max=%zu nul=%d\n", (int)g_eax, g_fwrite_calls,
               g_fwrite_max, (int)(unsigned char)g_harness_fmtbuf[g_fwrite_max]);
        return 0;
    }
    if (!strcmp(mode, "star")) {
        int reps = atoi(argv[2]);
        int i, p = 0;
        char *f = GSTR(FMT_VA);
        if (reps < 1 || reps > 12) return 102;
        f[p++] = '%';
        for (i = 0; i < reps; ++i) {
            if (i) f[p++] = '.';
            f[p++] = '*';
        }
        f[p++] = 'd';
        f[p] = 0;
        /* INT_MIN renders as an 11-char width: three of them overflow spec[32]. */
        for (i = 0; i < 64; ++i) MEM32(g_esp + 8 + 4 * i) = 0x80000000u;
        MEM32(g_esp + 4) = FMT_VA;
        sh_printf();
        printf("ret=%d calls=%zu max=%zu\n", (int)g_eax, g_fwrite_calls, g_fwrite_max);
        return 0;
    }
    if (!strcmp(mode, "starok")) {
        char *f = GSTR(FMT_VA);
        strcpy(f, "%*d");
        MEM32(g_esp + 8) = 5;                       /* width */
        MEM32(g_esp + 12) = 42;                     /* value */
        MEM32(g_esp + 4) = FMT_VA;
        sh_printf();
        printf("ret=%d calls=%zu max=%zu buf=%02x%02x%02x%02x%02x\n",
               (int)g_eax, g_fwrite_calls, g_fwrite_max,
               (unsigned char)g_harness_fmtbuf[0], (unsigned char)g_harness_fmtbuf[1],
               (unsigned char)g_harness_fmtbuf[2], (unsigned char)g_harness_fmtbuf[3],
               (unsigned char)g_harness_fmtbuf[4]);
        return 0;
    }
    if (!strcmp(mode, "nul")) {
        strcpy(GSTR(FMT_VA), "abc%n");
        MEM32(g_esp + 8) = 0;                       /* null %n destination */
        MEM32(g_esp + 4) = FMT_VA;
        sh_printf();
        printf("ret=%d calls=%zu max=%zu\n", (int)g_eax, g_fwrite_calls, g_fwrite_max);
        return 0;
    }
    if (!strcmp(mode, "nvalid")) {
        strcpy(GSTR(FMT_VA), "abc%n");
        MEM32(g_esp + 8) = NTARGET_VA;
        MEM32(g_esp + 4) = FMT_VA;
        sh_printf();
        printf("ret=%d calls=%zu max=%zu target=%u\n", (int)g_eax, g_fwrite_calls,
               g_fwrite_max, (unsigned)MEM32(NTARGET_VA));
        return 0;
    }
    if (!strcmp(mode, "sprintf-long")) {
        long want = strtol(argv[2], NULL, 10);
        long i;
        char *f = GSTR(FMT_VA);
        char *dst = GSTR(DEST_VA);                  /* own RW island, guard after */
        if (want < 0 || want > 400000) return 103;
        for (i = 0; i < want; ++i) f[i] = 'B';
        f[want] = 0;
        MEM32(g_esp + 4) = DEST_VA;                 /* ARG(0) = dest */
        MEM32(g_esp + 8) = FMT_VA;                  /* ARG(1) = format */
        sh_sprintf();
        printf("ret=%d first=%c last=%c nul=%d\n", (int)g_eax, dst[0],
               dst[g_fmtbuf_cap - 2], (int)(unsigned char)dst[g_fmtbuf_cap - 1]);
        return 0;
    }
    if (!strcmp(mode, "clamp")) {
        int values[] = { -1, 0, 10, 65535, 65536, 70000, 2147483647 };
        size_t i;
        for (i = 0; i < sizeof values / sizeof values[0]; ++i)
            printf("%d=%zu\n", values[i], fmt_write_len(values[i], g_fmtbuf_cap));
        return 0;
    }
    return 98;
}
'''


def _extract(source, signature):
    """Return one whole static function, brace-matched from its signature."""
    start = source.index(signature)
    depth = 0
    for i in range(source.index('{', start), len(source)):
        if source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError('unbalanced braces at ' + signature)


def _compiler(tmp):
    probe = tmp / 'probe.c'
    probe.write_text('int main(void) { return 0; }\n')
    for candidate in ('/usr/bin/cc', 'cc', 'clang', 'gcc'):
        exe = shutil.which(candidate) or candidate
        if not Path(exe).exists():
            continue
        out = tmp / 'probe'
        r = subprocess.run([exe, str(probe), '-o', str(out)],
                           capture_output=True, text=True, check=False)
        if r.returncode == 0:
            return exe
    return None


class NsLibcFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ns-libc-format-')
        cls.root = Path(cls.tmp.name)
        cc = _compiler(cls.root)
        if cc is None:
            raise unittest.SkipTest('no working C compiler')
        src = NS_LIBC_C.read_text()
        gformat = _extract(src, GFORMAT_SIG)
        # The harness substitutes a guarded pointer for the stack spec array;
        # keep sizeof(spec) semantics via SPEC_CAP and put spec at a page end.
        gformat = gformat.replace('char spec[32], tmp[512];',
                                  'char *spec = g_harness_spec; char tmp[512];')
        gformat = gformat.replace('sizeof spec', 'SPEC_CAP')
        callers = ''
        for sig in (FMT_WRITE_LEN_SIG, SH_PRINTF_SIG, SH_SPRINTF_SIG):
            callers += _extract(src, sig) + '\n'
        callers = callers.replace('sizeof g_fmtbuf', 'g_fmtbuf_cap')
        code = (HARNESS.replace('__GFORMAT__', gformat)
                       .replace('__FMT_WRITE_LEN__\n__SH_PRINTF__\n__SH_SPRINTF__',
                                callers))
        cls.src = cls.root / 'harness.c'
        cls.src.write_text(code)
        cls.exe = cls.root / 'harness'
        r = subprocess.run([cc, '-std=c11', '-O1', '-Wall', '-Wextra',
                            str(cls.src), '-o', str(cls.exe)],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            raise unittest.SkipTest('harness did not compile: ' + r.stderr[-1000:])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run(self, *args):
        r = subprocess.run([str(self.exe), *[str(a) for a in args]],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0,
                         f'harness {args} faulted (rc={r.returncode}):\n{r.stderr[-1000:]}')
        self.assertNotIn('Segmentation fault', r.stderr)
        return dict(token.split('=', 1) for token in r.stdout.split())

    # --- runtime behaviour -------------------------------------------------
    def test_long_format_does_not_overread_fmtbuf(self):
        # snprintf semantics: the would-be length is 70000, but fwrite must
        # never be handed more than the 65536 bytes that exist in g_fmtbuf.
        out = self._run('long', 70000)
        self.assertEqual(int(out['ret']), 70000)
        self.assertEqual(int(out['calls']), 1)
        self.assertEqual(int(out['max']), 65535)
        self.assertEqual(int(out['nul']), 0)          # NUL at the clamp point

    def test_truncation_at_exact_buffer_boundary_is_safe(self):
        out = self._run('long', 65536)
        self.assertEqual(int(out['ret']), 65536)
        self.assertEqual(int(out['max']), 65535)
        self.assertEqual(int(out['nul']), 0)
        out = self._run('long', 65535)
        self.assertEqual(int(out['max']), 65535)
        self.assertEqual(int(out['nul']), 0)

    def test_sprintf_truncation_is_clamped(self):
        # sh_sprintf is a sibling caller of gformat; it must copy at most
        # cap-1 bytes and terminate the guest destination itself.
        out = self._run('sprintf-long', 70000)
        self.assertEqual(int(out['ret']), 70000)
        self.assertEqual(out['first'], 'B')
        self.assertEqual(out['last'], 'B')
        self.assertEqual(int(out['nul']), 0)

    def test_star_width_overflow_fails_closed(self):
        # Three INT_MIN '*' widths cannot fit in spec[32]. The original code
        # advanced k to 34 and wrote spec[34] past the guarded spec buffer.
        for reps in (3, 4, 6, 12):
            out = self._run('star', reps)
            self.assertEqual(int(out['ret']), -1, f'reps={reps}')
            self.assertEqual(int(out['calls']), 1)
            self.assertEqual(int(out['max']), 0)

    def test_normal_star_width_still_formats(self):
        out = self._run('starok')
        self.assertEqual(int(out['ret']), 5)
        self.assertEqual(int(out['max']), 5)
        self.assertEqual(int(out['buf'], 16), 0x2020203432)   # "   42"

    def test_null_write_target_is_rejected(self):
        out = self._run('nul')
        self.assertEqual(int(out['ret']), -1)
        self.assertEqual(int(out['max']), 0)

    def test_valid_write_target_receives_count(self):
        out = self._run('nvalid')
        self.assertEqual(int(out['ret']), 3)
        self.assertEqual(int(out['target']), 3)
        self.assertEqual(int(out['max']), 3)

    def test_fmt_write_len_clamps_all_shapes(self):
        out = self._run('clamp')
        expected = {'-1': 0, '0': 0, '10': 10, '65535': 65535, '65536': 65535,
                    '70000': 65535, '2147483647': 65535}
        for key, value in expected.items():
            self.assertEqual(int(out[key]), value)

    # --- source guards -----------------------------------------------------
    def test_source_bounds_all_buffer_writes(self):
        text = NS_LIBC_C.read_text()
        gformat = _extract(text, GFORMAT_SIG)
        self.assertIn('if (n < cap) {', gformat)
        self.assertIn('size_t _avail = cap - n - 1;', gformat)
        self.assertIn('if (_full > SIZE_MAX - n) return -1;', gformat)
        self.assertIn('out[n < cap ? n : cap - 1] = 0;', gformat)

    def test_source_bounds_spec_and_rejects_truncation(self):
        gformat = _extract(NS_LIBC_C.read_text(), GFORMAT_SIG)
        self.assertIn('#define SPEC_PUT(ch)', gformat)
        self.assertIn('if (k >= (int)sizeof spec - 1) return -1;', gformat)
        self.assertIn('r < 0 || (size_t)r >= sizeof spec - (size_t)k', gformat)
        # The only `spec[k++]` is the one inside the bounds-checking macro.
        self.assertEqual(gformat.count('spec[k++]'), 1)

    def test_source_validates_write_target(self):
        gformat = _extract(NS_LIBC_C.read_text(), GFORMAT_SIG)
        self.assertIn('uint32_t addr = MEM32(ap); ap += 4;', gformat)
        self.assertIn('if (addr == 0) return -1;', gformat)
        self.assertIn('MEM32(addr) = (uint32_t)n;', gformat)

    def test_source_clamps_every_caller(self):
        text = NS_LIBC_C.read_text()
        self.assertIn('return (size_t)n < cap ? (size_t)n : cap - 1;',
                      _extract(text, FMT_WRITE_LEN_SIG))
        for sig in (SH_PRINTF_SIG, 'static void sh_fprintf(void) {',
                    SH_SPRINTF_SIG, 'static void sh_vsprintf(void) {'):
            body = _extract(text, sig)
            self.assertIn('fmt_write_len(n, sizeof g_fmtbuf)', body, sig)
            self.assertNotIn(', n, stdout', body)
            self.assertNotIn(', n + 1)', body)


if __name__ == '__main__':
    unittest.main()
