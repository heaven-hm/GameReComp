/*
 * shell_fileop_selftest.c - SHFileOperationA on a synthetic lifted guest.
 *
 * A per-process scratch directory is mapped as the C: drive, so every copy,
 * move and delete the shim performs lands there. The guest hands it x86
 * SHFILEOPSTRUCTA blocks with Windows-style double-NUL backslash path lists;
 * the host checks the real files that result and the DE_* error codes.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

#define FO_MOVE        0x0001u
#define FO_COPY        0x0002u
#define FO_DELETE      0x0003u
#define FOF_MULTIDESTFILES    0x0001u
#define FOF_RENAMEONCOLLISION 0x0008u
#define DE_INVALIDFILES    0x79u
#define DE_MANYDEST        0x76u
#define DE_DESTSAMETREE    0x7Cu
#define DE_ERROR_MAX       0x100u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

static char g_tmp[256];
static const char *tmpdir(void) {
    if (!g_tmp[0]) {
        snprintf(g_tmp, sizeof g_tmp, "%s/win32hle_fileop_%ld", "/tmp", (long)getpid());
        mkdir(g_tmp, 0700);
    }
    return g_tmp;
}
static void mkdir_p(const char *path) {
    char tmp[1024];
    snprintf(tmp, sizeof tmp, "%s", path);
    for (char *s = tmp + 1; *s; s++) if (*s == '/') {
        char c = *s; *s = 0;
        if (mkdir(tmp, 0755) != 0 && errno != EEXIST) { printf("FAIL mkdir %s\n", tmp); fails++; }
        *s = c;
    }
    if (mkdir(tmp, 0755) != 0 && errno != EEXIST) { printf("FAIL mkdir %s\n", tmp); fails++; }
}
static void put(const char *host, const char *content) {
    char parent[1024];
    snprintf(parent, sizeof parent, "%s", host);
    char *slash = strrchr(parent, '/');
    if (slash) { *slash = 0; mkdir_p(parent); }
    FILE *f = fopen(host, "wb");
    if (f) { fputs(content, f); fclose(f); }
    else { printf("FAIL put %s\n", host); fails++; }
}
static int exists(const char *host) { return access(host, F_OK) == 0; }
static int content(const char *host, const char *want) {
    FILE *f = fopen(host, "rb");
    if (!f) return 0;
    char buf[256] = { 0 };
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = 0;
    return !strcmp(buf, want);
}
static void host_path(const char *rel, char *out, size_t cap) {
    snprintf(out, cap, "%s/%s", tmpdir(), rel);
}

/* A guest buffer holding "s0\0s1\0...\0\0" - the Windows list form. */
static uint32_t guest_list(const char *const *items, int n) {
    size_t len = 1;
    for (int i = 0; i < n; i++) len += strlen(items[i]) + 1;
    uint32_t va = hle_va(hle_alloc((uint32_t)len));
    char *p = (char *)hle_ptr(va);
    for (int i = 0; i < n; i++) {
        size_t l = strlen(items[i]);
        memcpy(p, items[i], l);
        p += l, *p++ = 0;
    }
    *p = 0;
    return va;
}
/* x86 SHFILEOPSTRUCTA: hwnd, wFunc, pFrom, pTo, fFlags(W), aborted, mapping, title. */
static uint32_t guest_op(uint32_t wFunc, uint32_t from, uint32_t to, uint16_t flags) {
    uint32_t va = hle_va(hle_alloc(32));
    MEM32(va + 0) = 0;
    MEM32(va + 4) = wFunc;
    MEM32(va + 8) = from;
    MEM32(va + 12) = to;
    MEM16(va + 16) = flags;
    MEM32(va + 20) = 1;                 /* pre-set: the shim must clear it on success */
    MEM32(va + 24) = 0xDEADBEEFu;        /* ditto hNameMappings */
    MEM32(va + 28) = 0;
    return va;
}

int main(void) {
    win32hle_register(win32hle_advapi32);
    hle_set_drive('C', tmpdir());       /* the runtime supplies the sandbox */
    mach_enter();
    uint32_t fn = hle_resolve("SHFileOperationA");
    ok("SHFileOperationA registered", fn != 0);

    /* NULL struct and an unknown wFunc must report explicit errors. */
    ok("NULL struct -> DE_INVALIDFILES", hle_call_guest(fn, 1, (uint32_t[]){ 0 }) == DE_INVALIDFILES);
    {
        uint32_t from = guest_list((const char *[]){ "C:\\src\\hello.txt" }, 1);
        uint32_t op = guest_op(0x9999, from, 0, 0);
        ok("unknown wFunc -> DE_ERROR_MAX", hle_call_guest(fn, 1, &op) == DE_ERROR_MAX);
    }

    /* FO_COPY of one file: backslashes, double-NUL, real bytes copied. */
    char a[1024], b[1024];
    host_path("src/hello.txt", a, sizeof a);
    host_path("dst/copy.txt", b, sizeof b);
    put(a, "hello");
    {
        uint32_t from = guest_list((const char *[]){ "C:\\src\\hello.txt" }, 1);
        uint32_t to = guest_list((const char *[]){ "C:\\dst\\copy.txt" }, 1);
        uint32_t op = guest_op(FO_COPY, from, to, 0);
        uint32_t rc = hle_call_guest(fn, 1, &op);
        ok("FO_COPY single file -> success", rc == 0);
        ok("FO_COPY copied the real bytes", content(b, "hello"));
        ok("FO_COPY kept the source", content(a, "hello"));
        ok("FO_COPY cleared fAnyOperationsAborted/hNameMappings",
           MEM32(op + 20) == 0 && MEM32(op + 24) == 0);
    }

    /* FO_COPY: two sources into one destination directory. */
    char c[1024], d[1024], e[1024];
    host_path("src/a.txt", c, sizeof c);
    host_path("src/b.txt", d, sizeof d);
    host_path("dst2", e, sizeof e);
    put(c, "aaa"), put(d, "bbb");
    mkdir_p(e);
    {
        uint32_t from = guest_list((const char *[]){ "C:\\src\\a.txt", "C:\\src\\b.txt" }, 2);
        uint32_t to = guest_list((const char *[]){ "C:\\dst2" }, 1);
        uint32_t rc = hle_call_guest(fn, 1, (uint32_t[]){ guest_op(FO_COPY, from, to, 0) });
        char x[1024], y[1024];
        host_path("dst2/a.txt", x, sizeof x);
        host_path("dst2/b.txt", y, sizeof y);
        ok("FO_COPY many sources into a folder", rc == 0 && content(x, "aaa") && content(y, "bbb"));
    }

    /* FOF_RENAMEONCOLLISION picks an unused name instead of overwriting. */
    {
        uint32_t from = guest_list((const char *[]){ "C:\\src\\b.txt" }, 1);
        uint32_t to = guest_list((const char *[]){ "C:\\dst2\\a.txt" }, 1);
        uint32_t rc = hle_call_guest(fn, 1, (uint32_t[]){ guest_op(FO_COPY, from, to, FOF_RENAMEONCOLLISION) });
        char x[1024], y[1024];
        host_path("dst2/a.txt", x, sizeof x);
        host_path("dst2/a (2).txt", y, sizeof y);
        ok("FOF_RENAMEONCOLLISION copies to a new name",
           rc == 0 && content(x, "aaa") && content(y, "bbb"));
    }

    /* FO_MOVE: the source disappears, the destination appears. */
    {
        uint32_t from = guest_list((const char *[]){ "C:\\dst\\copy.txt" }, 1);
        uint32_t to = guest_list((const char *[]){ "C:\\dst\\moved.txt" }, 1);
        uint32_t rc = hle_call_guest(fn, 1, (uint32_t[]){ guest_op(FO_MOVE, from, to, 0) });
        char x[1024], y[1024];
        host_path("dst/copy.txt", x, sizeof x);
        host_path("dst/moved.txt", y, sizeof y);
        ok("FO_MOVE relocates the file", rc == 0 && !exists(x) && content(y, "hello"));
    }

    /* FO_DELETE: a file, and a directory tree recursively. */
    {
        uint32_t from = guest_list((const char *[]){ "C:\\dst2\\a.txt" }, 1);
        uint32_t rc = hle_call_guest(fn, 1, (uint32_t[]){ guest_op(FO_DELETE, from, 0, 0) });
        char x[1024];
        host_path("dst2/a.txt", x, sizeof x);
        ok("FO_DELETE removes a file", rc == 0 && !exists(x));
    }
    host_path("tree/sub/deep.txt", a, sizeof a);
    put(a, "deep");
    {
        uint32_t from = guest_list((const char *[]){ "C:\\tree" }, 1);
        uint32_t rc = hle_call_guest(fn, 1, (uint32_t[]){ guest_op(FO_DELETE, from, 0, 0) });
        char x[1024];
        host_path("tree", x, sizeof x);
        ok("FO_DELETE removes a directory tree", rc == 0 && !exists(x));
    }

    /* Error paths: missing source, missing destination, mismatched list
     * lengths and a destination inside the source tree. */
    {
        uint32_t from = guest_list((const char *[]){ "C:\\missing.txt" }, 1);
        ok("FO_DELETE missing source -> DE_INVALIDFILES",
           hle_call_guest(fn, 1, (uint32_t[]){ guest_op(FO_DELETE, from, 0, 0) }) == DE_INVALIDFILES);
    }
    {
        uint32_t from = guest_list((const char *[]){ "C:\\src\\hello.txt" }, 1);
        ok("FO_COPY without pTo -> DE_INVALIDFILES",
           hle_call_guest(fn, 1, (uint32_t[]){ guest_op(FO_COPY, from, 0, 0) }) == DE_INVALIDFILES);
    }
    {
        uint32_t from = guest_list((const char *[]){ "C:\\src\\a.txt", "C:\\src\\b.txt" }, 2);
        uint32_t to = guest_list((const char *[]){ "C:\\dst2" }, 1);
        ok("FOF_MULTIDESTFILES with one destination -> DE_MANYDEST",
           hle_call_guest(fn, 1, (uint32_t[]){ guest_op(FO_COPY, from, to, FOF_MULTIDESTFILES) }) == DE_MANYDEST);
    }
    host_path("self/inside", a, sizeof a);
    mkdir_p(a);
    {
        uint32_t from = guest_list((const char *[]){ "C:\\self" }, 1);
        uint32_t to = guest_list((const char *[]){ "C:\\self\\inside" }, 1);
        ok("destination inside source -> DE_DESTSAMETREE",
           hle_call_guest(fn, 1, (uint32_t[]){ guest_op(FO_COPY, from, to, 0) }) == DE_DESTSAMETREE);
    }

    mach_leave();
    if (fails == 0) printf("shell_fileop_selftest: all checks passed\n");
    return fails != 0;
}
