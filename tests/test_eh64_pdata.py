"""Deterministic regression tests for the recomp64 EH runtime (issue #336).

The three cases here exercise the REAL runtime source, not a Python copy of
it: a small C harness includes runtime/recomp64_cpu/eh64.c, builds synthetic PE
images in a temporary directory, and calls the file's static helpers directly.

  * TLS: the harness compiles as strict C11 (no -fms-extensions / -fdeclspec)
    and proves that push/pop state is per host thread.
  * .pdata: empty, zero-RVA/non-zero-size and truncated exception directories
    must leave pdata unset and es3_eh_throw() must decline the throw.
  * binary search: the unsigned midpoint helper is checked at the extremes of
    the 32-bit RVA range, and pdata_find() is checked on a synthetic table
    including the low-boundary case that used to underflow.

Nothing here loads a game, a fixture, a network resource or a real binary.
Run with: python3 -m pytest tests/test_eh64_pdata.py -q
"""
from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "recomp64_cpu"
EH64 = RUNTIME / "eh64.c"

# Stubs for the three symbols the runtime expects from the host, wrappers that
# expose the file's statics, and a tiny text protocol used by the tests below.
HARNESS_C = r"""
#include <inttypes.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "eh64.c"

void dispatch(CPU *c, uint64_t target) { (void)c; (void)target; }
int es3_cs_depth_get(void) { return 0; }
void es3_cs_depth_set(int d) { (void)d; }

static unsigned char *image;

static void t_load(const char *path)
{
    FILE *f = fopen(path, "rb");
    long n;
    if (!f) exit(2);
    if (fseek(f, 0, SEEK_END)) exit(2);
    n = ftell(f);
    if (n < 0x300 || fseek(f, 0, SEEK_SET)) exit(2);
    image = (unsigned char *)malloc((size_t)n + 16);
    if (!image || fread(image, 1, (size_t)n, f) != (size_t)n) exit(2);
    fclose(f);
    es3_eh_image_base = (uint64_t)(uintptr_t)image;
    pdata = 0;
    pdata_n = 0;
}

static uintptr_t t_mid(uintptr_t lo, uintptr_t hi) { return pdata_mid(lo, hi); }
static int t_has_pdata(void) { return pdata != 0; }
static uint32_t t_count(void) { return pdata_n; }

static long long t_find(uint32_t rva)
{
    rfunc_t *rf = pdata_find(rva);
    return rf ? (long long)(uintptr_t)(rf - pdata) : -1;
}

static int t_throw(void)
{
    CPU c;
    memset(&c, 0, sizeof c);
    return es3_eh_throw(&c, es3_eh_image_base + 0x210,
                        0x1000, es3_eh_image_base + 0x300);
}

static es3_ehframe_t t_main_frame, t_worker_frame;
static int worker_sees_none, worker_sees_own;

static void *t_worker(void *arg)
{
    (void)arg;
    worker_sees_none = (eh_top == 0);
    es3_eh_push(&t_worker_frame, 0x2000, 0);
    worker_sees_own = (eh_top == &t_worker_frame);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "mid") && argc == 4) {
        printf("%" PRIuPTR "\n",
               t_mid(strtoull(argv[2], 0, 0), strtoull(argv[3], 0, 0)));
        return 0;
    }
    if (!strcmp(argv[1], "init") && argc == 3) {
        t_load(argv[2]);
        eh_init();
        printf("%d %" PRIu32 "\n", t_has_pdata(), t_count());
        return 0;
    }
    if (!strcmp(argv[1], "find") && argc == 4) {
        t_load(argv[2]);
        eh_init();
        printf("%lld\n", t_find((uint32_t)strtoul(argv[3], 0, 0)));
        return 0;
    }
    if (!strcmp(argv[1], "throw") && argc == 3) {
        t_load(argv[2]);
        eh_init();
        printf("%d %d %d\n", t_has_pdata(), t_throw(), t_has_pdata());
        return 0;
    }
    if (!strcmp(argv[1], "tls") && argc == 2) {
        pthread_t tid;
        es3_eh_push(&t_main_frame, 0x1000, 0);
        if (pthread_create(&tid, 0, t_worker, 0)) return 3;
        pthread_join(tid, 0);
        printf("%d %d %d\n", (eh_top == &t_main_frame),
               worker_sees_none, worker_sees_own);
        return 0;
    }
    return 2;
}
"""

# dd = OptionalHeader (pe+0x18) + data directories (0x70) + entry 3 (3*8).
EXCEPTION_DD = 0x80 + 0x18 + 0x70 + 3 * 8
PDATA_RVA = 0x200


def synthetic_image(rva: int, size: int, entries=()) -> bytes:
    """A minimal PE32+ image with a chosen exception data directory."""
    image = bytearray(0x400)
    struct.pack_into("<I", image, 0x3c, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<II", image, EXCEPTION_DD, rva, size)
    for index, (begin, end, unwind) in enumerate(entries):
        # Never write over the headers when the RVA is bogus.
        assert rva >= 0x100
        struct.pack_into("<III", image, rva + 12 * index, begin, end, unwind)
    return bytes(image)


def _compiler(tmp_path: Path) -> str:
    probe = tmp_path / "probe.c"
    probe.write_text("#include <pthread.h>\nint main(void) { return 0; }\n",
                     encoding="utf-8")
    for name in ("cc", "clang", "gcc"):
        exe = shutil.which(name)
        if not exe:
            continue
        out = tmp_path / f"probe-{name}"
        result = subprocess.run([exe, "-std=c11", "-pthread", str(probe),
                                 "-o", str(out)], capture_output=True, text=True)
        if result.returncode == 0:
            return exe
    pytest.skip("no C11 compiler with pthread support is available")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eh64-pdata")
    cc = _compiler(tmp)
    source = tmp / "harness.c"
    source.write_text(HARNESS_C, encoding="utf-8")
    exe = tmp / "harness"
    # No -fms-extensions / -fdeclspec: the old __declspec(thread) fails here.
    result = subprocess.run([cc, "-std=c11", "-O0", "-w", "-pthread",
                             f"-I{RUNTIME}", str(source), "-o", str(exe)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"C11 compile failed:\n{result.stderr}"
    return exe


def invoke(harness: Path, *args) -> str:
    result = subprocess.run([str(harness), *(str(a) for a in args)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        f"harness {args} exited {result.returncode}: {result.stderr}")
    return result.stdout.strip()


def write_image(tmp_path: Path, name: str, rva: int, size: int, entries=()) -> Path:
    path = tmp_path / name
    path.write_bytes(synthetic_image(rva, size, entries))
    return path


def test_tls_qualifier_is_portable_and_per_thread(harness):
    source = EH64.read_text(encoding="utf-8")
    assert "_Thread_local" in source
    assert "static __declspec(thread)" not in source
    assert "static ES3_EH_TLS es3_ehframe_t *eh_top" in source
    # The harness itself compiles as strict C11; this also proves that no MS
    # extension is needed to build the runtime on a non-MSVC host.
    # main frame intact, worker starts empty, worker sees only its own frame.
    assert invoke(harness, "tls") == "1 1 1"


@pytest.mark.parametrize("rva,size", [(0, 0), (0, 12), (0, 4096), (0x200, 4)])
def test_eh_init_fails_closed_without_a_usable_exception_directory(
        harness, tmp_path, rva, size):
    image = write_image(tmp_path, f"empty-{rva}-{size}.bin", rva, size)
    # has_pdata, pdata_n: the DOS header must never be mistaken for .pdata.
    assert invoke(harness, "init", image) == "0 0"
    # No .pdata means the throw is declined without touching guest memory.
    assert invoke(harness, "throw", image) == "0 0 0"


def test_eh_init_still_reads_a_valid_exception_directory(harness, tmp_path):
    image = write_image(tmp_path, "valid.bin", PDATA_RVA, 24,
                        [(0x1000, 0x1100, 0x300), (0x1100, 0x1200, 0x310)])
    assert invoke(harness, "init", image) == "1 2"


@pytest.mark.parametrize("lo,hi,expected", [
    (0, 0xffffffff, 0x7fffffff),
    (0x7ffffffe, 0xffffffff, 0xbffffffe),  # signed (lo+hi) overflows here
    (0x80000000, 0x80000002, 0x80000001),
    (0x120, 0x120, 0x120),
])
def test_pdata_midpoint_is_overflow_safe(harness, lo, hi, expected):
    source = EH64.read_text(encoding="utf-8")
    assert "lo + (hi - lo) / 2" in source
    assert "uintptr_t" in source
    assert invoke(harness, "mid", hex(lo), hex(hi)) == str(expected)


def test_pdata_find_searches_a_synthetic_table_without_underflow(harness, tmp_path):
    image = write_image(tmp_path, "find.bin", PDATA_RVA, 36,
                        [(0x1000, 0x1100, 0x300),
                         (0x1100, 0x1200, 0x310),
                         (0x1200, 0x1300, 0x320)])
    # In-range hits map to table indices; below the first entry and past the
    # last entry miss. The first miss is the lo == 0 boundary.
    for rva, expected in [(0x1000, 0), (0x10ff, 0), (0x1100, 1),
                          (0x12ff, 2), (0x0fff, -1), (0x1300, -1),
                          (0xffffffff, -1)]:
        assert invoke(harness, "find", image, hex(rva)) == str(expected)
