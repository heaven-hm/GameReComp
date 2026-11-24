/*
 * locale_more_selftest.c - drive the locale-name bridge (user/system default
 * locale name, LCIDToLocaleName, LocaleNameToLCID) through the HLE stdcall
 * import path, entirely headless. Output strings live in guest memory as
 * UTF-16; size probes, NUL termination, exact code units, too-small buffers,
 * unsupported locales, neutral-name flags and last errors are all checked.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

#define ERROR_INVALID_PARAMETER  87u
#define ERROR_INSUFFICIENT_BUFFER 122u

#define LOCALE_ALLOW_NEUTRAL_NAMES 0x08000000u
#define LOCALE_NEUTRAL_EN        0x0009u
#define LOCALE_INVARIANT         0x007Fu
#define LOCALE_USER_DEFAULT      0x0400u
#define LOCALE_SYSTEM_DEFAULT    0x0800u
#define LOCALE_NEUTRAL           0x0000u
#define LOCALE_EN_US             0x0409u
#define LOCALE_DE                0x0407u   /* a locale this layer cannot back */

static int fails;
static void ok(const char *what, int cond) { if (!cond) { printf("FAIL %s\n", what); fails++; } }

/* hle_call_guest needs a dispatch table to resolve guest code VAs; this test
 * only calls registered import shims, so the table itself is never entered. */
static void unused_guest(void) {}
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0x00401000u, unused_guest } };
const uint32_t recomp_dispatch_count = 1;

static uint32_t V_user, V_system, V_lcid_to_name, V_name_to_lcid;

/* UTF-16 in guest memory (not a host pointer). */
static uint32_t put_units(const uint16_t *u, int n) {
    uint32_t va = hle_va(hle_alloc((uint32_t)n * 2u + 2u));
    int i;
    for (i = 0; i < n; i++) MEM16(va + (uint32_t)i * 2u) = u[i];
    MEM16(va + (uint32_t)n * 2u) = 0;
    return va;
}
static uint32_t put_wide(const char *s) {
    uint16_t buf[64];
    int n = (int)strlen(s), i;
    if (n >= (int)(sizeof buf / sizeof buf[0])) return 0;
    for (i = 0; i < n; i++) buf[i] = (uint8_t)s[i];
    return put_units(buf, n);
}
static void fill_units(uint32_t va, uint16_t v, int n) {
    int i;
    for (i = 0; i < n; i++) MEM16(va + (uint32_t)i * 2u) = v;
}
static int wide_eq(uint32_t va, const uint16_t *want, int n) {
    int i;
    for (i = 0; i < n; i++) if (MEM16(va + (uint32_t)i * 2u) != want[i]) return 0;
    return 1;
}
static int wide_str_eq(uint32_t va, const char *s) {
    uint16_t buf[64];
    int n = (int)strlen(s), i;
    if (n >= (int)(sizeof buf / sizeof buf[0])) return 0;
    for (i = 0; i < n; i++) buf[i] = (uint8_t)s[i];
    return wide_eq(va, buf, n) && MEM16(va + (uint32_t)n * 2u) == 0;
}

static uint32_t call2(uint32_t v, uint32_t a0, uint32_t a1, uint32_t *err) {
    hle_set_last_error(0);
    uint32_t r = hle_call_guest(v, 2, (uint32_t[]){ a0, a1 });
    *err = hle_get_last_error();
    return r;
}
static uint32_t call4(uint32_t v, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3, uint32_t *err) {
    hle_set_last_error(0);
    uint32_t r = hle_call_guest(v, 4, (uint32_t[]){ a0, a1, a2, a3 });
    *err = hle_get_last_error();
    return r;
}

int main(void) {
    uint32_t err, r;
    uint32_t en_us = put_wide("en-US"), en = put_wide("en"), invariant = put_wide("");
    uint32_t sysname = put_wide("!x-sys-default-locale"), de = put_wide("de-DE");
    uint32_t dst = hle_va(hle_alloc(128));
    static const uint16_t want_en_us[6] = { 'e', 'n', '-', 'U', 'S', 0 };

    win32hle_register(win32hle_kernel32_ext);
    V_user        = hle_resolve("GetUserDefaultLocaleName");
    V_system      = hle_resolve("GetSystemDefaultLocaleName");
    V_lcid_to_name = hle_resolve("LCIDToLocaleName");
    V_name_to_lcid = hle_resolve("LocaleNameToLCID");
    ok("all four locale-name shims resolve", V_user && V_system && V_lcid_to_name && V_name_to_lcid);
    if (!V_user || !V_system || !V_lcid_to_name || !V_name_to_lcid) return 1;

    /* GetUserDefaultLocaleName / GetSystemDefaultLocaleName. */
    r = call2(V_user, 0, 0, &err);
    ok("GetUserDefaultLocaleName(0,0) is a size probe returning 6", r == 6 && err == 0);
    r = call2(V_system, 0, 0, &err);
    ok("GetSystemDefaultLocaleName(0,0) is a size probe returning 6", r == 6 && err == 0);
    r = call2(V_user, dst, 8, &err);
    ok("GetUserDefaultLocaleName writes UTF-16 'en-US' plus NUL",
       r == 6 && err == 0 && wide_eq(dst, want_en_us, 6));
    r = call2(V_system, dst, 6, &err);
    ok("GetSystemDefaultLocaleName agrees with the user default and fits exactly",
       r == 6 && err == 0 && wide_eq(dst, want_en_us, 6));
    fill_units(dst, 0xA55Au, 8);
    r = call2(V_user, dst, 5, &err);
    ok("GetUserDefaultLocaleName with a short buffer fails and leaves it untouched",
       r == 0 && err == ERROR_INSUFFICIENT_BUFFER);
    {
        int i, untouched = 1;
        for (i = 0; i < 8; i++) if (MEM16(dst + (uint32_t)i * 2u) != 0xA55Au) untouched = 0;
        ok("short-buffer failure wrote no UTF-16 code units", untouched);
    }
    r = call2(V_user, 0, 8, &err);
    ok("GetUserDefaultLocaleName with NULL buffer and cch>0 fails", r == 0 && err == ERROR_INSUFFICIENT_BUFFER);
    r = call2(V_user, dst, (uint32_t)-1, &err);
    ok("GetUserDefaultLocaleName rejects a negative cch", r == 0 && err == ERROR_INVALID_PARAMETER);

    /* LCIDToLocaleName. */
    r = call4(V_lcid_to_name, LOCALE_EN_US, dst, 8, 0, &err);
    ok("LCIDToLocaleName(0x0409) is 'en-US'", r == 6 && err == 0 && wide_str_eq(dst, "en-US"));
    r = call4(V_lcid_to_name, LOCALE_USER_DEFAULT, dst, 8, 0, &err);
    ok("LCIDToLocaleName(LOCALE_USER_DEFAULT) is 'en-US'", r == 6 && err == 0 && wide_str_eq(dst, "en-US"));
    r = call4(V_lcid_to_name, LOCALE_SYSTEM_DEFAULT, dst, 8, 0, &err);
    ok("LCIDToLocaleName(LOCALE_SYSTEM_DEFAULT) is 'en-US'", r == 6 && err == 0 && wide_str_eq(dst, "en-US"));
    r = call4(V_lcid_to_name, LOCALE_NEUTRAL, dst, 8, 0, &err);
    ok("LCIDToLocaleName(LOCALE_NEUTRAL) uses the user locale", r == 6 && err == 0 && wide_str_eq(dst, "en-US"));
    r = call4(V_lcid_to_name, LOCALE_INVARIANT, dst, 8, 0, &err);
    ok("LCIDToLocaleName(LOCALE_INVARIANT) is the empty name",
       r == 1 && err == 0 && wide_str_eq(dst, ""));
    r = call4(V_lcid_to_name, LOCALE_NEUTRAL_EN, dst, 8, 0, &err);
    ok("LCIDToLocaleName(neutral en) falls back to 'en-US' without the flag",
       r == 6 && err == 0 && wide_str_eq(dst, "en-US"));
    r = call4(V_lcid_to_name, LOCALE_NEUTRAL_EN, dst, 8, LOCALE_ALLOW_NEUTRAL_NAMES, &err);
    ok("LCIDToLocaleName(neutral en) honours LOCALE_ALLOW_NEUTRAL_NAMES",
       r == 3 && err == 0 && wide_str_eq(dst, "en"));
    r = call4(V_lcid_to_name, LOCALE_EN_US, 0, 0, 0, &err);
    ok("LCIDToLocaleName cchName==0 is a size probe", r == 6 && err == 0);
    r = call4(V_lcid_to_name, LOCALE_EN_US, dst, 3, 0, &err);
    ok("LCIDToLocaleName with a short buffer fails with ERROR_INSUFFICIENT_BUFFER",
       r == 0 && err == ERROR_INSUFFICIENT_BUFFER);
    r = call4(V_lcid_to_name, LOCALE_EN_US, 0, 8, 0, &err);
    ok("LCIDToLocaleName with NULL buffer and cch>0 fails", r == 0 && err == ERROR_INSUFFICIENT_BUFFER);
    r = call4(V_lcid_to_name, LOCALE_DE, dst, 8, 0, &err);
    ok("LCIDToLocaleName refuses a locale the layer cannot back", r == 0 && err == ERROR_INVALID_PARAMETER);

    /* LocaleNameToLCID. */
    r = call2(V_name_to_lcid, 0, 0, &err);
    ok("LocaleNameToLCID(NULL) is the user default LCID", r == LOCALE_EN_US && err == 0);
    r = call2(V_name_to_lcid, invariant, 0, &err);
    ok("LocaleNameToLCID(L\"\") is LOCALE_INVARIANT", r == LOCALE_INVARIANT && err == 0);
    r = call2(V_name_to_lcid, sysname, 0, &err);
    ok("LocaleNameToLCID(LOCALE_NAME_SYSTEM_DEFAULT) is the system LCID", r == LOCALE_EN_US && err == 0);
    r = call2(V_name_to_lcid, en_us, 0, &err);
    ok("LocaleNameToLCID(\"en-US\") is 0x0409", r == LOCALE_EN_US && err == 0);
    r = call2(V_name_to_lcid, en, 0, &err);
    ok("LocaleNameToLCID(\"en\") uses the default language without the flag",
       r == LOCALE_EN_US && err == 0);
    r = call2(V_name_to_lcid, en, LOCALE_ALLOW_NEUTRAL_NAMES, &err);
    ok("LocaleNameToLCID(\"en\") honours LOCALE_ALLOW_NEUTRAL_NAMES",
       r == LOCALE_NEUTRAL_EN && err == 0);
    r = call2(V_name_to_lcid, en_us, 0xdeadbeefu, &err);
    ok("LocaleNameToLCID ignores reserved flag bits like Windows", r == LOCALE_EN_US && err == 0);
    r = call2(V_name_to_lcid, de, 0, &err);
    ok("LocaleNameToLCID refuses an unsupported locale name", r == 0 && err == ERROR_INVALID_PARAMETER);
    {
        uint16_t nonascii[3] = { 'e', 0x00E9, 0 };
        r = call2(V_name_to_lcid, put_units(nonascii, 3), 0, &err);
        ok("LocaleNameToLCID refuses a non-ASCII name", r == 0 && err == ERROR_INVALID_PARAMETER);
    }
    {
        /* 64 non-NUL units: the name reader's cap must reject, not run on. */
        uint32_t unterminated = put_wide("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        fill_units(unterminated, 'a', 64);
        r = call2(V_name_to_lcid, unterminated, 0, &err);
        ok("LocaleNameToLCID refuses an unterminated name", r == 0 && err == ERROR_INVALID_PARAMETER);
    }

    if (!fails) printf("locale_more_selftest: all checks passed\n");
    return fails != 0;
}
