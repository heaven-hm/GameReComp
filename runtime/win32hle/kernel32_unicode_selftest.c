/*
 * kernel32_unicode_selftest.c - drives the five newly registered Unicode
 * KERNEL32 imports (lstrcpyW, lstrcatW, GetSystemDirectoryW, MoveFileExW and
 * FormatMessageW) from synthetic guest memory. Headless, no game, no Windows
 * oracle: every expectation is derived from Win32 semantics documented in the
 * shim itself, and the checks are about bounded UTF-16 writes, NUL
 * termination, last-error values and fail-closed behaviour.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/stat.h>
#include "win32hle.h"

static int fails;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }

/* A guest WCHAR string from an ASCII C string, and back. These shims only
 * produce/consume ASCII here; the point is the UTF-16 layout and bounds. */
static uint32_t wstr(const char *s) {
    size_t n = strlen(s) + 1u, i;
    uint16_t *p = (uint16_t *)hle_alloc((uint32_t)(n * 2u));
    for (i = 0; i < n; i++) p[i] = (uint16_t)(unsigned char)s[i];
    return hle_va(p);
}
static void wget(uint32_t va, char *out, size_t cap) {
    size_t i;
    for (i = 0; i + 1u < cap; i++) {
        uint16_t c = MEM16(va + (uint32_t)i * 2u);
        if (!c) break;
        out[i] = (char)c;
    }
    out[i] = 0;
}

#define FM_FROM_STRING     0x00000400u
#define FM_IGNORE_INSERTS  0x00000200u
#define FM_ALLOCATE_BUFFER 0x00000100u
#define FM_ARGUMENT_ARRAY  0x00002000u
#define FM_FROM_SYSTEM     0x00001000u

/* host_lite.c's recomp_lookup wants a dispatch table; this selftest calls the
 * shims directly with hle_call_guest and never lifts guest code, so one empty
 * entry is enough to satisfy the contract. */
static void guest(void) { }
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0x00401000u, guest } };
const uint32_t recomp_dispatch_count = 1;

int main(void) {
    uint32_t V_cpy, V_cat, V_gsd, V_move, V_fmt;
    char buf[128];
    uint32_t r, dst, outbuf;
    static char tmp[256];

    hle_mem_init();
    win32hle_register(win32hle_kernel32);
    win32hle_register(win32hle_kernel32_ext);
    V_cpy  = hle_resolve("lstrcpyW");
    V_cat  = hle_resolve("lstrcatW");
    V_gsd  = hle_resolve("GetSystemDirectoryW");
    V_move = hle_resolve("MoveFileExW");
    V_fmt  = hle_resolve("FormatMessageW");
    ok("all five Unicode KERNEL32 shims resolve", V_cpy && V_cat && V_gsd && V_move && V_fmt);

    /* ---- lstrcpyW / lstrcatW: return dst, terminate, respect a canary ---- */
    dst = hle_va(hle_alloc(64));
    MEM16(dst + 16u) = 0x5A5Au;                 /* canary beyond the result */
    r = hle_call_guest(V_cpy, 2, (uint32_t[]){ dst, wstr("World") });
    wget(dst, buf, sizeof buf);
    ok("lstrcpyW returns dst, copies and NUL-terminates",
       r == dst && !strcmp(buf, "World") && MEM16(dst + 5u * 2u) == 0);
    r = hle_call_guest(V_cat, 2, (uint32_t[]){ dst, wstr("!") });
    wget(dst, buf, sizeof buf);
    ok("lstrcatW appends at the NUL and returns dst",
       r == dst && !strcmp(buf, "World!") && MEM16(dst + 6u * 2u) == 0);
    ok("lstrcpyW/lstrcatW never touch the canary", MEM16(dst + 16u) == 0x5A5Au);
    hle_set_last_error(0);
    r = hle_call_guest(V_cpy, 2, (uint32_t[]){ dst, 0u });
    ok("lstrcpyW fails closed on a NULL source pointer", r == 0 && hle_get_last_error() == 87u);
    hle_set_last_error(0);
    r = hle_call_guest(V_cat, 2, (uint32_t[]){ 0u, wstr("x") });
    ok("lstrcatW fails closed on a NULL destination", r == 0 && hle_get_last_error() == 87u);

    /* ---- GetSystemDirectoryW: real Windows-style path, correct sizes ---- */
    outbuf = hle_va(hle_alloc(128));
    hle_set_last_error(0);
    r = hle_call_guest(V_gsd, 2, (uint32_t[]){ 0u, 0u });
    ok("GetSystemDirectoryW size probe returns 20 WCHARs", r == 20u);
    r = hle_call_guest(V_gsd, 2, (uint32_t[]){ outbuf, 32u });
    wget(outbuf, buf, sizeof buf);
    ok("GetSystemDirectoryW writes C:\\Windows\\System32 as UTF-16",
       r == 19u && !strcmp(buf, "C:\\Windows\\System32") && MEM16(outbuf + 19u * 2u) == 0);
    memset(hle_ptr(outbuf), 0xCC, 64);
    hle_set_last_error(0);
    r = hle_call_guest(V_gsd, 2, (uint32_t[]){ outbuf, 5u });
    ok("GetSystemDirectoryW small buffer: required size + ERROR_INSUFFICIENT_BUFFER",
       r == 20u && hle_get_last_error() == 122u && MEM16(outbuf) == 0xCCCCu);

    /* ---- MoveFileExW: ANSI form's body, driven with UTF-16 paths ---- */
    snprintf(tmp, sizeof tmp, "/tmp/k32uni_%ld", (long)getpid());
    mkdir(tmp, 0700);
    {
        char a[512], b[512];
        FILE *f;
        snprintf(a, sizeof a, "%s/src.txt", tmp);
        snprintf(b, sizeof b, "%s/dst.txt", tmp);
        f = fopen(a, "w"); if (f) { fputs("move", f); fclose(f); }
        remove(b);
        hle_set_drive('C', tmp);
        r = hle_call_guest(V_move, 3, (uint32_t[]){ wstr("C:\\src.txt"), wstr("C:\\dst.txt"), 0u });
        ok("MoveFileExW moves the guest file", r == 1u && access(b, F_OK) == 0 && access(a, F_OK) != 0);
        f = fopen(a, "w"); if (f) { fputs("again", f); fclose(f); }
        hle_set_last_error(0);
        r = hle_call_guest(V_move, 3, (uint32_t[]){ wstr("C:\\src.txt"), wstr("C:\\dst.txt"), 4u });
        ok("MoveFileExW refuses MOVEFILE_DELAY_UNTIL_REBOOT",
           r == 0u && hle_get_last_error() == 120u && access(a, F_OK) == 0);
    }

    /* ---- FormatMessageW ---- */
    {
        uint32_t fmt = wstr("Hi %1, you have %2.");
        uint32_t s1 = wstr("Bob"), s2 = wstr("3");
        uint32_t vals = hle_va(hle_alloc(16));
        uint32_t vlist = hle_va(hle_alloc(4));
        MEM32(vals) = s1; MEM32(vals + 4u) = s2;
        MEM32(vlist) = vals;                    /* x86 va_list*: first word is the varargs */
        outbuf = hle_va(hle_alloc(256));

        hle_set_last_error(0);
        r = hle_call_guest(V_fmt, 7, (uint32_t[]){ FM_FROM_STRING, fmt, 0u, 0u, outbuf, 64u, vlist });
        wget(outbuf, buf, sizeof buf);
        ok("FormatMessageW FROM_STRING + va_list insertions",
           r == 19u && !strcmp(buf, "Hi Bob, you have 3.") && hle_get_last_error() == 0u);

        hle_set_last_error(0);
        r = hle_call_guest(V_fmt, 7, (uint32_t[]){ FM_FROM_STRING | FM_ARGUMENT_ARRAY,
                                                  fmt, 0u, 0u, outbuf, 64u, vals });
        wget(outbuf, buf, sizeof buf);
        ok("FormatMessageW ARGUMENT_ARRAY insertions", r == 19u && !strcmp(buf, "Hi Bob, you have 3."));

        {
            uint32_t fi = wstr("count=%1!d!"), arr = hle_va(hle_alloc(4));
            MEM32(arr) = 42u;
            r = hle_call_guest(V_fmt, 7, (uint32_t[]){ FM_FROM_STRING | FM_ARGUMENT_ARRAY,
                                                      fi, 0u, 0u, outbuf, 64u, arr });
            wget(outbuf, buf, sizeof buf);
            ok("FormatMessageW integer insert !d!", r == 8u && !strcmp(buf, "count=42"));
        }
        {
            uint32_t esc = wstr("A%%B%tC%rD");
            r = hle_call_guest(V_fmt, 7, (uint32_t[]){ FM_FROM_STRING, esc, 0u, 0u, outbuf, 64u, 0u });
            wget(outbuf, buf, sizeof buf);
            ok("FormatMessageW escapes %%, %t, %r", r == 7u && !strcmp(buf, "A%B\tC\rD"));
        }
        hle_set_last_error(0);
        r = hle_call_guest(V_fmt, 7, (uint32_t[]){ FM_FROM_STRING | FM_IGNORE_INSERTS,
                                                  fmt, 0u, 0u, outbuf, 64u, 0u });
        wget(outbuf, buf, sizeof buf);
        ok("FormatMessageW IGNORE_INSERTS passes %n through",
           r == 19u && !strcmp(buf, "Hi %1, you have %2."));

        hle_set_last_error(0);
        r = hle_call_guest(V_fmt, 7, (uint32_t[]){ FM_FROM_STRING | FM_FROM_SYSTEM,
                                                  fmt, 5u, 0u, outbuf, 64u, vlist });
        ok("FormatMessageW refuses FROM_SYSTEM with ERROR_INVALID_FLAGS (diagnostic on stderr)",
           r == 0u && hle_get_last_error() == 1004u);
        hle_set_last_error(0);
        r = hle_call_guest(V_fmt, 7, (uint32_t[]){ FM_FROM_STRING, fmt, 0u, 0u, outbuf, 3u, vlist });
        ok("FormatMessageW small buffer: 0 + ERROR_INSUFFICIENT_BUFFER",
           r == 0u && hle_get_last_error() == 122u);
        {
            uint32_t pbuf = hle_va(hle_alloc(4));
            MEM32(pbuf) = 0u;
            r = hle_call_guest(V_fmt, 7, (uint32_t[]){ FM_FROM_STRING | FM_ALLOCATE_BUFFER,
                                                      fmt, 0u, 0u, pbuf, 8u, vlist });
            wget(MEM32(pbuf), buf, sizeof buf);
            ok("FormatMessageW ALLOCATE_BUFFER writes the pointer and the message",
               r == 19u && MEM32(pbuf) != 0u && !strcmp(buf, "Hi Bob, you have 3."));
        }
        {
            size_t z = 70000u, q;
            uint16_t *big = (uint16_t *)hle_alloc((uint32_t)((z + 1u) * 2u));
            for (q = 0; q < z; q++) big[q] = '%';  /* %% -> one '%'; 35000 output WCHARs */
            big[z] = 0;
            hle_set_last_error(0);
            r = hle_call_guest(V_fmt, 7, (uint32_t[]){ FM_FROM_STRING, hle_va(big),
                                                      0u, 0u, outbuf, 64u, 0u });
            ok("FormatMessageW oversized message: 0 + ERROR_MORE_DATA",
               r == 0u && hle_get_last_error() == 234u);
        }
        MEM32(vals) = 0u;                       /* a NULL insertion must fail closed */
        hle_set_last_error(0);
        r = hle_call_guest(V_fmt, 7, (uint32_t[]){ FM_FROM_STRING, fmt, 0u, 0u, outbuf, 64u, vlist });
        ok("FormatMessageW refuses a NULL insertion string",
           r == 0u && hle_get_last_error() == 87u);
    }

    if (fails == 0) printf("kernel32_unicode_selftest: all checks passed\n");
    return fails != 0;
}
