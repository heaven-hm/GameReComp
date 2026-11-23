/*
 * crt_more_selftest.c - the extra MSVCRT wide-string exports added to
 * msvcrt.c: _wcsdup, wcscat, wcsncat and wcscmp.
 *
 * Everything goes through hle_call_guest on the shim looked up by its PE
 * import name, so the cdecl entry path (args pushed by the caller, RETC in the
 * shim popping only the synthetic return address) is exercised, not assumed.
 * Guest wchar_t is 2 bytes: every fixture is a uint16_t cell array in guest
 * memory, and every expectation is read back from guest memory - the host's
 * 4-byte wchar_t is never used as the oracle. Headless, no game required.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int argc, const uint32_t *args) {
    uint32_t va = hle_resolve(name);
    if (!va) hle_fatal("crt_more_selftest: no shim named %s", name);
    return hle_call_guest(va, argc, args);
}

/* A guest wide string with room for `cap` uint16_t cells. hle_alloc zeroes
 * the block, so anything past the explicit NUL is a clean canary region. */
static uint32_t wide_buf(const char *s, size_t cap) {
    size_t n = strlen(s) + 1;
    if (n > cap) cap = n;
    uint32_t va = hle_va(hle_alloc((uint32_t)(cap * 2u)));
    uint16_t *w = (uint16_t *)hle_ptr(va);
    for (size_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)s[i];
    return va;
}
static uint32_t wide(const char *s) { return wide_buf(s, strlen(s) + 1); }
static int wide_eq(uint32_t va, const char *s) {
    const uint16_t *w = (const uint16_t *)hle_ptr(va);
    size_t i = 0;
    for (; s[i]; i++) if (w[i] != (uint16_t)(unsigned char)s[i]) return 0;
    return w[i] == 0;
}

int main(void) {
    win32hle_register(win32hle_msvcrt);
    mach_enter();

    /* The four names are real registered shims, not stubs. */
    static const char *const names[] = { "_wcsdup", "wcscat", "wcsncat", "wcscmp" };
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; i++)
        ok("wide CRT shim registered", hle_resolve(names[i]) != 0);

    /* ---- _wcsdup: a real copy on the CRT heap, reclaimable by free ---- */
    uint32_t src = wide("Crystal Dynamics");
    uint32_t dup = call("_wcsdup", 1, &src);
    ok("_wcsdup returns a pointer", dup != 0);
    ok("_wcsdup makes a distinct buffer", dup != src);
    ok("_wcsdup copies every code unit", wide_eq(dup, "Crystal Dynamics"));
    ok("_wcsdup terminates copy", ((const uint16_t *)hle_ptr(dup))[16] == 0);
    ok("_wcsdup uses the guest CRT heap", call("_msize", 1, &dup) >= 34u);
    call("free", 1, &dup);                       /* no leak, and proves free ownership */

    uint32_t empty = wide("");
    uint32_t edup = call("_wcsdup", 1, &empty);
    ok("_wcsdup of empty string succeeds", edup != 0);
    ok("_wcsdup of empty string is just NUL", edup && ((const uint16_t *)hle_ptr(edup))[0] == 0);
    call("free", 1, &edup);

    ok("_wcsdup of NULL fails", call("_wcsdup", 1, (uint32_t[]){ 0u }) == 0u);
    uint32_t errno_va = call("_errno", 0, NULL);
    ok("_wcsdup NULL sets EINVAL", errno_va && MEM32(errno_va) == 22u);

    /* ---- wcscat: append in place, return dst ---- */
    uint32_t d1 = wide_buf("Tomb ", 32);
    uint32_t s1 = wide("Raider");
    uint32_t r1 = call("wcscat", 2, (uint32_t[]){ d1, s1 });
    ok("wcscat returns dst", r1 == d1);
    ok("wcscat appends the whole source", wide_eq(d1, "Tomb Raider"));
    uint32_t e1 = wide_buf("", 32);
    ok("wcscat onto empty is a copy", call("wcscat", 2, (uint32_t[]){ e1, s1 }) == e1);
    ok("wcscat onto empty result", wide_eq(e1, "Raider"));
    ok("wcscat of NULL dst fails honestly", call("wcscat", 2, (uint32_t[]){ 0u, s1 }) == 0u);
    ok("wcscat of NULL src fails honestly", call("wcscat", 2, (uint32_t[]){ d1, 0u }) == 0u);

    /* ---- wcsncat: bounded append, always NUL terminated ---- */
    uint32_t d2 = wide_buf("ab", 32);
    uint32_t s2 = wide("cdefgh");
    ok("wcsncat returns dst", call("wcsncat", 3, (uint32_t[]){ d2, s2, 3u }) == d2);
    ok("wcsncat appends at most n", wide_eq(d2, "abcde"));
    ok("wcsncat n=0 leaves dst alone", call("wcsncat", 3, (uint32_t[]){ d2, s2, 0u }) == d2);
    ok("wcsncat n=0 result", wide_eq(d2, "abcde"));
    uint32_t d3 = wide_buf("x", 32);
    call("wcsncat", 3, (uint32_t[]){ d3, s2, 100u });
    ok("wcsncat stops at source NUL", wide_eq(d3, "xcdefgh"));
    ok("wcsncat of NULL dst fails honestly", call("wcsncat", 3, (uint32_t[]){ 0u, s2, 3u }) == 0u);
    ok("wcsncat of NULL src fails honestly", call("wcsncat", 3, (uint32_t[]){ d3, 0u, 3u }) == 0u);

    /* ---- wcscmp: signed result from unsigned 16-bit code units ---- */
    uint32_t a = wide("abc"), ac = wide("abc");
    ok("wcscmp equal", call("wcscmp", 2, (uint32_t[]){ a, ac }) == 0u);
    ok("wcscmp less", call("wcscmp", 2, (uint32_t[]){ wide("abc"), wide("abd") }) == 0xFFFFFFFFu);
    ok("wcscmp greater", call("wcscmp", 2, (uint32_t[]){ wide("abd"), wide("abc") }) == 1u);
    ok("wcscmp shorter prefix is less",
       call("wcscmp", 2, (uint32_t[]){ wide("ab"), wide("abc") }) == 0xFFFFFFFFu);
    ok("wcscmp empty vs empty", call("wcscmp", 2, (uint32_t[]){ empty, wide("") }) == 0u);
    ok("wcscmp NULL vs NULL", call("wcscmp", 2, (uint32_t[]){ 0u, 0u }) == 0u);
    ok("wcscmp NULL is less", call("wcscmp", 2, (uint32_t[]){ 0u, a }) == 0xFFFFFFFFu);
    ok("wcscmp against NULL is greater", call("wcscmp", 2, (uint32_t[]){ a, 0u }) == 1u);
    /* 0x00E9 < 0x00FF as unsigned code units (a host wchar_t compare agrees,
     * but the point is that no sign extension of 16-bit units happens). */
    uint32_t u1 = wide_buf("", 2), u2 = wide_buf("", 2);
    ((uint16_t *)hle_ptr(u1))[0] = 0x00E9u;
    ((uint16_t *)hle_ptr(u2))[0] = 0x00FFu;
    ok("wcscmp compares unsigned code units",
       call("wcscmp", 2, (uint32_t[]){ u1, u2 }) == 0xFFFFFFFFu);

    mach_leave();
    printf("crt_more_selftest: all checks passed\n");
    return fails != 0;
}
