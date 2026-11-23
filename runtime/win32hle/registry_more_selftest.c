/* registry_more_selftest.c - RegQueryInfoKeyW and RegDeleteTreeA/W.
 *
 * Drives the guest ABI through hle_call_guest against the isolated registry
 * store, the same way registry_w_selftest.c does.  Checks the W metadata
 * contract (WCHAR name counts, byte data counts, class capacity, optional
 * outputs, honest errors) and recursive deletion through both the ANSI and
 * wide entry points, including that a NULL subkey clears a key without
 * closing it and that a named missing subkey reports ERROR_FILE_NOT_FOUND. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

#define HKEY_CURRENT_USER 0x80000001u
#define KEY_ALL_ACCESS_VALUE 0xF003Fu
#define REG_SZ_VALUE 1u
#define REG_DWORD_VALUE 4u
#define ERROR_SUCCESS_VALUE 0u
#define ERROR_FILE_NOT_FOUND_VALUE 2u
#define ERROR_INVALID_HANDLE_VALUE 6u
#define ERROR_INVALID_PARAMETER_VALUE 87u

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static void oku(const char *what, uint32_t got, uint32_t want) {
    if (got != want) { printf("FAIL %s (got %u, want %u)\n", what, got, want); fails++; }
}
static uint32_t call(const char *n, int argc, const uint32_t *a) {
    return hle_call_guest(hle_resolve(n), argc, a);
}
static uint32_t u32(void) { return hle_va(hle_alloc(4)); }
static uint32_t cstr(const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 1, va = hle_va(hle_alloc(n));
    memcpy(hle_ptr(va), s, n);
    return va;
}
/* ASCII guest UTF-16 buffer. */
static uint32_t wstr(const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 1, va = hle_va(hle_alloc(n * 2u));
    uint16_t *w = (uint16_t *)hle_ptr(va);
    for (uint32_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)s[i];
    return va;
}
/* CP1252 -> UTF-16 for the one high byte this test uses (0x80 -> U+20AC),
 * matching hle_ansi_to_wide so the W path round-trips the stored name. */
static uint32_t wstr_cp(const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 1, va = hle_va(hle_alloc(n * 2u));
    uint16_t *w = (uint16_t *)hle_ptr(va);
    for (uint32_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        w[i] = c == 0x80 ? 0x20ACu : (uint16_t)c;
    }
    return va;
}
static uint32_t create(uint32_t parent, const char *path) {
    uint32_t out = u32(), disp = u32();
    ok("create key", call("RegCreateKeyExA", 9, (uint32_t[]){
        parent, cstr(path), 0, 0, 0, KEY_ALL_ACCESS_VALUE, 0, out, disp }) == ERROR_SUCCESS_VALUE);
    return MEM32(out);
}
/* Open and close, to ask whether a path exists without leaking a handle. */
static int has(uint32_t parent, const char *path) {
    uint32_t out = u32();
    if (call("RegOpenKeyExA", 5, (uint32_t[]){ parent, cstr(path), 0, 0, out }) != ERROR_SUCCESS_VALUE)
        return 0;
    uint32_t h = MEM32(out);
    ok("close probe", call("RegCloseKey", 1, &h) == ERROR_SUCCESS_VALUE);
    return h != 0;
}
typedef struct {
    uint32_t status, nsub, maxsub, maxclass, nval, maxname, maxdata, sec, ftlo, fthi;
    uint32_t class_len;
    uint16_t class0;
} info_t;
/* Read all twelve outputs of RegQueryInfoKeyW into one place for assertions. */
static info_t query(uint32_t key, uint32_t cls, uint32_t cls_len) {
    info_t r;
    uint32_t nsub = u32(), maxsub = u32(), maxclass = u32(), nval = u32();
    uint32_t maxname = u32(), maxdata = u32(), sec = u32(), ft = hle_va(hle_alloc(8));
    memset(&r, 0, sizeof r);
    r.status = call("RegQueryInfoKeyW", 12, (uint32_t[]){
        key, cls, cls_len, 0, nsub, maxsub, maxclass, nval, maxname, maxdata, sec, ft });
    r.nsub = MEM32(nsub);     r.maxsub = MEM32(maxsub);
    r.maxclass = MEM32(maxclass); r.nval = MEM32(nval);
    r.maxname = MEM32(maxname); r.maxdata = MEM32(maxdata);
    r.sec = MEM32(sec);       r.ftlo = MEM32(ft); r.fthi = MEM32(ft + 4);
    if (cls_len) r.class_len = MEM32(cls_len);
    if (cls) r.class0 = MEM16(cls);
    return r;
}

int main(void) {
    win32hle_register(win32hle_advapi32);
    mach_enter();
    static const char *names[] = { "RegQueryInfoKeyW", "RegDeleteTreeA", "RegDeleteTreeW" };
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; i++)
        if (!hle_resolve(names[i])) { printf("FAIL missing %s\n", names[i]); fails++; }

    uint32_t handles[24]; unsigned nh = 0;
#define TRACK(h) do { if (nh < sizeof handles / sizeof handles[0]) handles[nh++] = (h); } while (0)

    /* ---- RegQueryInfoKeyW -------------------------------------------- */
    uint32_t parent = create(HKEY_CURRENT_USER, "Software\\GameReCompMoreTest");
    TRACK(parent);
    ok("parent handle", parent != 0);
    static const char *subs[] = { "A", "LongSubkeyName", "Caf\x80" };
    uint32_t subhandles[3];
    for (unsigned i = 0; i < 3; i++) {
        subhandles[i] = create(parent, subs[i]);
        TRACK(subhandles[i]);
        ok("child handle", subhandles[i] != 0);
    }
    uint32_t dword = u32();
    MEM32(dword) = 0x1234u;
    ok("set dword", call("RegSetValueExA", 6, (uint32_t[]){
        parent, cstr("X"), 0, REG_DWORD_VALUE, dword, 4 }) == ERROR_SUCCESS_VALUE);
    ok("set sz", call("RegSetValueExA", 6, (uint32_t[]){
        parent, cstr("LongValueName"), 0, REG_SZ_VALUE, cstr("hello"), 6 }) == ERROR_SUCCESS_VALUE);

    uint32_t cls = hle_va(hle_alloc(16)), cls_len = u32();
    memset(hle_ptr(cls), 0xAA, 16);
    MEM32(cls_len) = 8;                                  /* capacity in WCHARs */
    info_t i = query(parent, cls, cls_len);
    oku("info status", i.status, ERROR_SUCCESS_VALUE);
    oku("info nsub", i.nsub, 3u);
    oku("info maxsub", i.maxsub, 14u);                   /* WCHARs, not bytes */
    oku("info maxclass", i.maxclass, 0u);
    oku("info nval", i.nval, 2u);
    oku("info maxname", i.maxname, 13u);
    oku("info maxdata", i.maxdata, 6u);                  /* value data is bytes */
    oku("info security", i.sec, 0u);
    oku("info ft low", i.ftlo, 0u);
    oku("info ft high", i.fthi, 0u);
    oku("info class len", i.class_len, 0u);
    oku("info empty class", i.class0, 0u);               /* NUL was written */

    /* A zero class capacity writes nothing but is otherwise a normal query. */
    memset(hle_ptr(cls), 0xAA, 16);
    MEM32(cls_len) = 0;
    info_t z = query(parent, cls, cls_len);
    oku("zero-cap status", z.status, ERROR_SUCCESS_VALUE);
    oku("zero-cap len", z.class_len, 0u);
    oku("zero-cap untouched", ((uint8_t *)hle_ptr(cls))[0], 0xAAu);

    /* lpClass with no lpcchClass, and a bad key, are reported, not guessed. */
    oku("class without capacity", call("RegQueryInfoKeyW", 12, (uint32_t[]){
        parent, cls, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 }), ERROR_INVALID_PARAMETER_VALUE);
    oku("info bad handle", call("RegQueryInfoKeyW", 12, (uint32_t[]){
        0xDEADBEEFu, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 }), ERROR_INVALID_HANDLE_VALUE);
    /* Every optional output pointer may be NULL. */
    oku("info all optional NULL", call("RegQueryInfoKeyW", 12, (uint32_t[]){
        parent, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 }), ERROR_SUCCESS_VALUE);

    /* A CP1252 byte is one UTF-16 code unit in the store, so maxsub counts it
     * as one WCHAR even though it encodes as a two-byte code point. */
    uint32_t wide = create(HKEY_CURRENT_USER, "Software\\GameReCompWideCount");
    TRACK(wide);
    TRACK(create(wide, "C\x80\x80"));                    /* 3 store bytes */
    info_t w = query(wide, 0, 0);
    oku("cp1252 status", w.status, ERROR_SUCCESS_VALUE);
    oku("cp1252 nsub", w.nsub, 1u);
    oku("cp1252 maxsub is WCHARs", w.maxsub, 3u);

    /* ---- RegDeleteTreeA ---------------------------------------------- */
    TRACK(create(subhandles[0], "A1"));                  /* a grandchild */
    oku("delete tree A", call("RegDeleteTreeA", 2, (uint32_t[]){ parent, cstr("A") }), ERROR_SUCCESS_VALUE);
    oku("A gone", has(parent, "A"), 0);
    oku("A1 gone", has(parent, "A\\A1"), 0);
    oku("sibling survives", has(parent, "LongSubkeyName"), 1);
    info_t after_a = query(parent, 0, 0);
    oku("after A nsub", after_a.nsub, 2u);
    oku("after A nval", after_a.nval, 2u);               /* parent values untouched */
    oku("missing subkey", call("RegDeleteTreeA", 2, (uint32_t[]){ parent, cstr("Missing") }), ERROR_FILE_NOT_FOUND_VALUE);
    oku("tree A bad handle", call("RegDeleteTreeA", 2, (uint32_t[]){ 0xDEADBEEFu, 0 }), ERROR_INVALID_HANDLE_VALUE);
    /* NULL subkey clears the key's contents without closing the key itself. */
    oku("clear parent", call("RegDeleteTreeA", 2, (uint32_t[]){ parent, 0 }), ERROR_SUCCESS_VALUE);
    info_t cleared = query(parent, 0, 0);
    oku("cleared nsub", cleared.nsub, 0u);
    oku("cleared nval", cleared.nval, 0u);
    oku("cleared maxname", cleared.maxname, 0u);
    oku("parent still open", has(HKEY_CURRENT_USER, "Software\\GameReCompMoreTest"), 1);

    /* ---- RegDeleteTreeW ---------------------------------------------- */
    uint32_t parentw = create(HKEY_CURRENT_USER, "Software\\GameReCompTreeW");
    TRACK(parentw);
    TRACK(create(parentw, "Caf\x80"));                   /* target of the W call */
    TRACK(create(parentw, "Plain"));                     /* must survive */
    TRACK(create(parentw, "Plain\\Inner"));
    oku("delete tree W", call("RegDeleteTreeW", 2, (uint32_t[]){ parentw, wstr_cp("Caf\x80") }), ERROR_SUCCESS_VALUE);
    oku("wide target gone", has(parentw, "Caf\x80"), 0);
    oku("wide sibling survives", has(parentw, "Plain"), 1);
    oku("wide missing", call("RegDeleteTreeW", 2, (uint32_t[]){ parentw, wstr("Missing") }), ERROR_FILE_NOT_FOUND_VALUE);
    oku("tree W bad handle", call("RegDeleteTreeW", 2, (uint32_t[]){ 0xDEADBEEFu, 0 }), ERROR_INVALID_HANDLE_VALUE);
    oku("clear parent W", call("RegDeleteTreeW", 2, (uint32_t[]){ parentw, 0 }), ERROR_SUCCESS_VALUE);
    info_t clearedw = query(parentw, 0, 0);
    oku("cleared W subkeys", clearedw.nsub, 0u);
    oku("cleared W values", clearedw.nval, 0u);

    /* ---- cleanup ------------------------------------------------------ */
    for (unsigned i = 0; i < nh; i++) {
        uint32_t h = handles[i];
        ok("close handle", call("RegCloseKey", 1, &h) == ERROR_SUCCESS_VALUE);
    }
    ok("delete parent", call("RegDeleteKeyA", 2, (uint32_t[]){
        HKEY_CURRENT_USER, cstr("Software\\GameReCompMoreTest") }) == ERROR_SUCCESS_VALUE);
    /* a named RegDeleteTree removes the named subkey itself, so this drops the
     * WCHAR-count probe key together with its child */
    ok("delete wide count key", call("RegDeleteTreeA", 2, (uint32_t[]){
        HKEY_CURRENT_USER, cstr("Software\\GameReCompWideCount") }) == ERROR_SUCCESS_VALUE);
    ok("delete W parent", call("RegDeleteKeyA", 2, (uint32_t[]){
        HKEY_CURRENT_USER, cstr("Software\\GameReCompTreeW") }) == ERROR_SUCCESS_VALUE);

    mach_leave();
    printf("registry_more_selftest: %s\n", fails ? "FAILED" : "all checks passed");
    return fails != 0;
}
