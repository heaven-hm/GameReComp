/*
 * locale_ex_selftest.c - drive LCMapStringEx, GetLocaleInfoEx and
 * CompareStringEx through the HLE's stdcall import path, entirely headless.
 * All strings live in guest memory as UTF-16; reserved parameters are checked,
 * buffer probes and last errors are checked, and unsupported locales/flags
 * must fail rather than return invented NLS data.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

#define LCMAP_LOWERCASE         0x00000100u
#define LCMAP_UPPERCASE         0x00000200u
#define LCMAP_SORTKEY           0x00000400u
#define LCMAP_BYTEREV           0x00000800u
#define LCMAP_HIRAGANA          0x00100000u
#define NORM_IGNORECASE         0x00000001u
#define NORM_IGNORENONSPACE     0x00000002u
#define NORM_IGNORESYMBOLS      0x00000004u
#define SORT_STRINGSORT         0x00001000u
#define LOCALE_RETURN_NUMBER    0x20000000u
#define ERROR_INVALID_PARAMETER 87u
#define ERROR_INSUFFICIENT_BUFFER 122u
#define ERROR_INVALID_FLAGS     1004u

#define LOCALE_SDECIMAL         0x0000000Eu
#define LOCALE_ILANGUAGE        0x00000001u
#define LOCALE_SNAME            0x0000005Cu
#define LOCALE_UNKNOWN          0x00007FFFu

static int fails;
static void ok(const char *what, int cond) { if (!cond) { printf("FAIL %s\n", what); fails++; } }

/* hle_call_guest needs a dispatch table to resolve guest code VAs; this test
 * only calls registered import shims, so the table itself is never entered. */
static void unused_guest(void) {}
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0x00401000u, unused_guest } };
const uint32_t recomp_dispatch_count = 1;

static uint32_t V_map, V_info, V_cmp;

/* UTF-16 in guest memory (not a host pointer). */
static uint32_t put_units(const uint16_t *u, int n) {
    uint32_t va = hle_va(hle_alloc((uint32_t)n * 2u + 2u));
    int i;
    for (i = 0; i < n; i++) MEM16(va + (uint32_t)i * 2u) = u[i];
    MEM16(va + (uint32_t)n * 2u) = 0;
    return va;
}
static uint32_t put_wide(const char *s) {
    uint16_t buf[128];
    int n = (int)strlen(s), i;
    if (n >= (int)(sizeof buf / sizeof buf[0])) return 0;
    for (i = 0; i < n; i++) buf[i] = (uint8_t)s[i];
    return put_units(buf, n);
}
static int wide_eq(uint32_t va, const uint16_t *want, int n) {
    int i;
    for (i = 0; i < n; i++) if (MEM16(va + (uint32_t)i * 2u) != want[i]) return 0;
    return 1;
}
static int wide_str_eq(uint32_t va, const char *s) {
    uint16_t buf[128];
    int n = (int)strlen(s), i;
    if (n >= (int)(sizeof buf / sizeof buf[0])) return 0;
    for (i = 0; i < n; i++) buf[i] = (uint8_t)s[i];
    return wide_eq(va, buf, n) && MEM16(va + (uint32_t)n * 2u) == 0;
}
static int byte_eq(uint32_t va, const uint8_t *want, int n) {
    int i;
    for (i = 0; i < n; i++) if (MEM8(va + (uint32_t)i) != want[i]) return 0;
    return 1;
}

static uint32_t map(uint32_t loc, uint32_t flags, uint32_t src, int cs, uint32_t dst, int cd, uint32_t *err) {
    hle_set_last_error(0);
    uint32_t r = hle_call_guest(V_map, 9, (uint32_t[]){ loc, flags, src, (uint32_t)cs, dst,
                                                       (uint32_t)cd, 0, 0, 0 });
    *err = hle_get_last_error();
    return r;
}
static uint32_t info(uint32_t loc, uint32_t type, uint32_t dst, int cd, uint32_t *err) {
    hle_set_last_error(0);
    uint32_t r = hle_call_guest(V_info, 4, (uint32_t[]){ loc, type, dst, (uint32_t)cd });
    *err = hle_get_last_error();
    return r;
}
static uint32_t cmp(uint32_t loc, uint32_t flags, uint32_t a, int ca, uint32_t b, int cb, uint32_t *err) {
    hle_set_last_error(0);
    uint32_t r = hle_call_guest(V_cmp, 9, (uint32_t[]){ loc, flags, a, (uint32_t)ca, b, (uint32_t)cb,
                                                       0, 0, 0 });
    *err = hle_get_last_error();
    return r;
}

int main(void) {
    uint32_t err, r;
    uint32_t en = put_wide("en-US"), invariant = put_wide(""), fr = put_wide("fr-FR");
    uint32_t dst = hle_va(hle_alloc(128));
    win32hle_register(win32hle_kernel32_ext);
    V_map  = hle_resolve("LCMapStringEx");
    V_info = hle_resolve("GetLocaleInfoEx");
    V_cmp  = hle_resolve("CompareStringEx");
    ok("all three Ex locale shims resolve", V_map && V_info && V_cmp);
    if (!V_map || !V_info || !V_cmp) return 1;

    /* LCMapStringEx: UTF-16 source, NUL-terminated (-1) and explicit lengths. */
    {
        uint16_t src[5] = { 'A', 'b', 'c', 0x00E9, 0 };
        uint16_t want[5] = { 'A', 'B', 'C', 0x00C9, 0 };
        uint32_t s = put_units(src, 5);
        r = map(en, LCMAP_UPPERCASE, s, -1, dst, 16, &err);
        ok("LCMapStringEx uppercases UTF-16 (ASCII + Latin-1) and NUL-terminates",
           r == 5 && err == 0 && wide_eq(dst, want, 5));
        r = map(en, LCMAP_UPPERCASE, s, -1, 0, 0, &err);
        ok("LCMapStringEx cchDest==0 is a size probe", r == 5 && err == 0);
        r = map(en, LCMAP_UPPERCASE, s, -1, dst, 3, &err);
        ok("LCMapStringEx small buffer fails with ERROR_INSUFFICIENT_BUFFER", r == 0 && err == ERROR_INSUFFICIENT_BUFFER);
    }
    {
        uint16_t src[6] = { 'H','E','L','L','O',0 };
        uint16_t want[5] = { 'h','e','l','l','o' };
        uint32_t s = put_units(src, 6);
        r = map(0, LCMAP_LOWERCASE, s, 5, dst, 5, &err);  /* NULL = user default */
        ok("LCMapStringEx honours an explicit cchSrc without adding a NUL",
           r == 5 && err == 0 && wide_eq(dst, want, 5) && MEM16(dst + 10) == 0);
        MEM16(dst + 10) = 0x1234;
        r = map(0, LCMAP_LOWERCASE, s, 5, dst, 5, &err);
        ok("LCMapStringEx does not overwrite past cchDest", MEM16(dst + 10) == 0x1234);
    }
    {
        uint16_t src[3] = { 'a','b',0 }, want[2] = { 0x4100, 0x4200 };
        uint32_t s = put_units(src, 3);
        r = map(en, LCMAP_UPPERCASE | LCMAP_BYTEREV, s, 2, dst, 2, &err);
        ok("LCMapStringEx LCMAP_BYTEREV swaps each UTF-16 code unit",
           r == 2 && err == 0 && wide_eq(dst, want, 2));
    }
    {
        uint16_t src[3] = { 'a','B',0 };
        uint8_t want[5] = { 0, 'a', 0, 'B', 0 };
        uint32_t s = put_units(src, 3);
        r = map(en, LCMAP_SORTKEY, s, 2, dst, 32, &err);
        ok("LCMapStringEx sort key is a byte stream with a terminator",
           r == 5 && err == 0 && byte_eq(dst, want, 5));
        r = map(en, LCMAP_SORTKEY, s, 2, 0, 0, &err);
        ok("LCMapStringEx sort-key size probe", r == 5 && err == 0);
    }
    r = map(en, LCMAP_HIRAGANA, put_wide("a"), -1, dst, 16, &err);
    ok("LCMapStringEx rejects flags it cannot implement", r == 0 && err == ERROR_INVALID_FLAGS);
    r = map(fr, LCMAP_UPPERCASE, put_wide("a"), -1, dst, 16, &err);
    ok("LCMapStringEx rejects an unsupported locale", r == 0 && err == ERROR_INVALID_PARAMETER);
    {
        uint32_t s = put_wide("a");
        hle_set_last_error(0);
        r = hle_call_guest(V_map, 9, (uint32_t[]){ en, LCMAP_UPPERCASE, s, (uint32_t)-1, dst, 16u, 0x1234u, 0, 0 });
        ok("LCMapStringEx rejects a version structure it cannot honour", r == 0 && hle_get_last_error() == ERROR_INVALID_PARAMETER);
    }

    /* GetLocaleInfoEx: UTF-16 text, size probing, RETURN_NUMBER and LCTypes. */
    r = info(0, LOCALE_SDECIMAL, dst, 8, &err);
    ok("GetLocaleInfoEx(LOCALE_SDECIMAL) is '.' + NUL", r == 2 && err == 0 && wide_str_eq(dst, "."));
    r = info(en, LOCALE_SDECIMAL, 0, 0, &err);
    ok("GetLocaleInfoEx cchData==0 is a size probe", r == 2 && err == 0);
    r = info(en, LOCALE_SNAME, dst, 8, &err);
    ok("GetLocaleInfoEx knows its own locale name", r == 6 && err == 0 && wide_str_eq(dst, "en-US"));
    r = info(en, LOCALE_SNAME, dst, 2, &err);
    ok("GetLocaleInfoEx small buffer fails with ERROR_INSUFFICIENT_BUFFER", r == 0 && err == ERROR_INSUFFICIENT_BUFFER);
    r = info(0, LOCALE_ILANGUAGE | LOCALE_RETURN_NUMBER, dst, 2, &err);
    ok("GetLocaleInfoEx RETURN_NUMBER writes a DWORD and returns 2",
       r == 2 && err == 0 && MEM32(dst) == 0x0409u);
    r = info(0, LOCALE_ILANGUAGE, dst, 8, &err);
    ok("GetLocaleInfoEx LOCALE_ILANGUAGE is a hex string", r == 5 && err == 0 && wide_str_eq(dst, "0409"));
    r = info(0, LOCALE_UNKNOWN, dst, 8, &err);
    ok("GetLocaleInfoEx rejects an unknown LCType", r == 0 && err == ERROR_INVALID_PARAMETER);
    r = info(invariant, LOCALE_SDECIMAL, dst, 8, &err);
    ok("GetLocaleInfoEx has no invariant data and says so", r == 0 && err == ERROR_INVALID_PARAMETER);
    r = info(fr, LOCALE_SDECIMAL, dst, 8, &err);
    ok("GetLocaleInfoEx rejects an unsupported locale", r == 0 && err == ERROR_INVALID_PARAMETER);

    /* CompareStringEx: CSTR_* results, lengths, ignore flags and failures. */
    {
        uint32_t abc = put_wide("abc"), ABC = put_wide("ABC"), ab = put_wide("ab"), abcd = put_wide("abcd");
        r = cmp(0, 0, abc, -1, ABC, -1, &err);
        ok("CompareStringEx default ordering is case-sensitive", r == 3 && err == 0);
        r = cmp(en, NORM_IGNORECASE, abc, -1, ABC, -1, &err);
        ok("CompareStringEx NORM_IGNORECASE", r == 2 && err == 0);
        r = cmp(en, 0, ab, -1, abc, -1, &err);
        ok("CompareStringEx shorter prefix sorts first", r == 1 && err == 0);
        r = cmp(en, 0, ABC, -1, abc, -1, &err);
        ok("CompareStringEx less", r == 1 && err == 0);
        r = cmp(en, 0, abc, -1, ab, -1, &err);
        ok("CompareStringEx greater", r == 3 && err == 0);
        r = cmp(en, 0, abcd, 2, ab, -1, &err);
        ok("CompareStringEx honours explicit cchCount", r == 2 && err == 0);
        r = cmp(en, NORM_IGNORESYMBOLS, put_wide("a,b"), -1, ab, -1, &err);
        ok("CompareStringEx NORM_IGNORESYMBOLS ignores ASCII punctuation", r == 2 && err == 0);
        {
            uint16_t eacute[3] = { 'e', 0x0301, 0 };
            r = cmp(en, NORM_IGNORENONSPACE, put_units(eacute, 3), -1, put_wide("e"), -1, &err);
            ok("CompareStringEx NORM_IGNORENONSPACE skips combining marks", r == 2 && err == 0);
        }
        r = cmp(invariant, NORM_IGNORECASE, abc, -1, ABC, -1, &err);
        ok("CompareStringEx supports the invariant locale for casing", r == 2 && err == 0);
        r = cmp(en, SORT_STRINGSORT, abc, -1, ABC, -1, &err);
        ok("CompareStringEx rejects flags needing real collation data", r == 0 && err == ERROR_INVALID_FLAGS);
        r = cmp(fr, 0, abc, -1, ABC, -1, &err);
        ok("CompareStringEx rejects an unsupported locale", r == 0 && err == ERROR_INVALID_PARAMETER);
        hle_set_last_error(0);
        r = hle_call_guest(V_cmp, 9, (uint32_t[]){ en, 0u, abc, (uint32_t)-1, ABC, (uint32_t)-1, 0, 0, 1 });
        ok("CompareStringEx rejects a nonzero sort handle", r == 0 && hle_get_last_error() == ERROR_INVALID_PARAMETER);
    }

    if (!fails) printf("locale_ex_selftest: all checks passed\n");
    return fails != 0;
}
