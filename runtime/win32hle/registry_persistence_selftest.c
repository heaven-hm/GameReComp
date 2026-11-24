/* registry_persistence_selftest.c - registry persistence APIs added in GR3.
 *
 * Drives the guest ABI through hle_call_guest against the isolated registry
 * store, the same way registry_apis_selftest.c does.  Covers the five missing
 * game-relevant APIs: RegCreateKeyExW, RegSetValueExW, RegQueryValueA/W and
 * RegSetValueA, plus their edge cases (default/unnamed value, legacy subkey
 * redirect, byte-counted buffers, ERROR_MORE_DATA, cross ANSI/UTF-16 store,
 * and persistence-file round-trip). */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "win32hle.h"

#define HKEY_CURRENT_USER 0x80000001u
#define REG_SZ            1u
#define REG_DWORD         4u
#define KEY_ALL_ACCESS    0xF003Fu
#define ERROR_SUCCESS_VALUE             0u
#define ERROR_FILE_NOT_FOUND_VALUE      2u
#define ERROR_INVALID_HANDLE_VALUE      6u
#define ERROR_INVALID_PARAMETER_VALUE  87u
#define ERROR_MORE_DATA_VALUE         234u
#define REG_CREATED_NEW_KEY             1u
#define REG_OPENED_EXISTING_KEY         2u

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
static int wstr_eq(uint32_t va, const char *s) {
    const uint16_t *w = (const uint16_t *)hle_ptr(va);
    for (const char *p = s; *p; p++, w++) if (*w != (uint16_t)(unsigned char)*p) return 0;
    return *w == 0;
}

int main(void) {
    win32hle_register(win32hle_advapi32);
    mach_enter();

    static const char *added[] = { "RegCreateKeyExW", "RegSetValueExW",
            "RegQueryValueA", "RegQueryValueW", "RegSetValueA" };
    for (unsigned i = 0; i < sizeof added / sizeof added[0]; i++)
        if (!hle_resolve(added[i])) { printf("FAIL missing %s\n", added[i]); fails++; }

    /* Give persistence a private file: hle_reg_load accepts a not-yet-existing
     * path and records it, so the next RegSetValue* writes the store to it. */
    char path[512];
    snprintf(path, sizeof path, "%s/gr3_reg_persist_%ld.txt",
             getenv("TMPDIR") ? getenv("TMPDIR") : "/tmp", (long)getpid());
    unlink(path);
    hle_reg_load(path);

    uint32_t sub = wstr("Software\\GR3Persistence");
    uint32_t key_va = u32(), disp = u32();
    oku("RegCreateKeyExW created",
        call("RegCreateKeyExW", 9, (uint32_t[]){
            HKEY_CURRENT_USER, sub, 0, 0, 0, KEY_ALL_ACCESS, 0, key_va, disp }),
        ERROR_SUCCESS_VALUE);
    uint32_t key = MEM32(key_va);
    ok("RegCreateKeyExW handle", key != 0);
    oku("disposition created new", MEM32(disp), REG_CREATED_NEW_KEY);
    oku("RegCreateKeyExW existing",
        call("RegCreateKeyExW", 9, (uint32_t[]){
            HKEY_CURRENT_USER, sub, 0, 0, 0, KEY_ALL_ACCESS, 0, key_va, disp }),
        ERROR_SUCCESS_VALUE);
    oku("disposition opened existing", MEM32(disp), REG_OPENED_EXISTING_KEY);

    /* RegSetValueExW: a UTF-16 name with string data and raw DWORD data. */
    oku("RegSetValueExW string",
        call("RegSetValueExW", 6, (uint32_t[]){
            key, wstr("WideName"), 0, REG_SZ, wstr("wide-value"),
            (uint32_t)((strlen("wide-value") + 1) * 2) }), ERROR_SUCCESS_VALUE);
    uint32_t dw = u32();
    MEM32(dw) = 0xDEADBEEFu;
    oku("RegSetValueExW dword",
        call("RegSetValueExW", 6, (uint32_t[]){
            key, wstr("WideDword"), 0, REG_DWORD, dw, 4 }), ERROR_SUCCESS_VALUE);

    /* The store is shared: the A and W value-name forms reach the same value. */
    uint32_t type = u32(), data = hle_va(hle_alloc(64)), len = u32();
    MEM32(type) = 0; MEM32(len) = 64;
    oku("query wide-written value via ANSI name",
        call("RegQueryValueExA", 6, (uint32_t[]){
            key, cstr("WideName"), 0, type, data, len }), ERROR_SUCCESS_VALUE);
    ok("wide string stored as CP1252", memcmp(hle_ptr(data), "wide-value", 11) == 0);
    ok("wide string type REG_SZ", MEM32(type) == REG_SZ);
    MEM32(len) = 64;
    oku("query wide dword via ANSI name",
        call("RegQueryValueExA", 6, (uint32_t[]){
            key, cstr("WideDword"), 0, type, data, len }), ERROR_SUCCESS_VALUE);
    ok("wide dword bytes preserved", MEM32(data) == 0xDEADBEEFu && MEM32(type) == REG_DWORD);

    /* Reverse direction: the pre-existing W query sees a wide-named write. */
    MEM32(len) = 64;
    oku("query wide name via RegQueryValueExW",
        call("RegQueryValueExW", 6, (uint32_t[]){
            key, wstr("WideName"), 0, type, data, len }), ERROR_SUCCESS_VALUE);

    /* RegSetValueA writes the default value; RegQueryValueA reads it back. */
    oku("RegSetValueA default",
        call("RegSetValueA", 5, (uint32_t[]){
            key, 0, REG_SZ, cstr("default-ansi"), 0xFFFFFFFFu }), ERROR_SUCCESS_VALUE);
    MEM32(len) = 64;
    oku("RegQueryValueA default",
        call("RegQueryValueA", 4, (uint32_t[]){ key, 0, data, len }), ERROR_SUCCESS_VALUE);
    ok("default string", memcmp(hle_ptr(data), "default-ansi", 13) == 0);
    oku("default length in bytes", MEM32(len), 13u);

    /* lpcbData is required; a NULL lpData asks only for the size; a short
     * buffer returns ERROR_MORE_DATA with the required size written back. */
    oku("RegQueryValueA needs lpcbData",
        call("RegQueryValueA", 4, (uint32_t[]){ key, 0, data, 0 }),
        ERROR_INVALID_PARAMETER_VALUE);
    MEM32(len) = 4;
    oku("RegQueryValueA short buffer",
        call("RegQueryValueA", 4, (uint32_t[]){ key, 0, data, len }),
        ERROR_MORE_DATA_VALUE);
    oku("short buffer writes required size", MEM32(len), 13u);

    /* Legacy subkey redirect: a value name that names a subkey selects that
     * subkey's default value. */
    uint32_t nested = u32();
    oku("create nested key",
        call("RegCreateKeyExW", 9, (uint32_t[]){
            key, wstr("Nested"), 0, 0, 0, KEY_ALL_ACCESS, 0, nested, disp }),
        ERROR_SUCCESS_VALUE);
    oku("set nested default",
        call("RegSetValueA", 5, (uint32_t[]){
            key, cstr("Nested"), REG_SZ, cstr("nested-default"), 0xFFFFFFFFu }),
        ERROR_SUCCESS_VALUE);
    MEM32(len) = 64;
    oku("RegQueryValueA subkey redirect",
        call("RegQueryValueA", 4, (uint32_t[]){ key, cstr("Nested"), data, len }),
        ERROR_SUCCESS_VALUE);
    ok("redirected default string", memcmp(hle_ptr(data), "nested-default", 15) == 0);

    /* RegQueryValueW returns the same default as UTF-16 and counts bytes. */
    uint32_t widebuf = hle_va(hle_alloc(64));
    MEM32(len) = 64;
    oku("RegQueryValueW default",
        call("RegQueryValueW", 4, (uint32_t[]){ key, 0, widebuf, len }),
        ERROR_SUCCESS_VALUE);
    ok("RegQueryValueW UTF-16 text", wstr_eq(widebuf, "default-ansi"));
    oku("RegQueryValueW byte count", MEM32(len), 26u);   /* 13 WCHARs incl. NUL */
    MEM32(len) = 4;
    oku("RegQueryValueW short buffer",
        call("RegQueryValueW", 4, (uint32_t[]){ key, 0, widebuf, len }),
        ERROR_MORE_DATA_VALUE);
    oku("RegQueryValueW writes required size", MEM32(len), 26u);

    /* Error paths: missing default value and invalid key handle. */
    uint32_t empty = u32();
    oku("create key without default",
        call("RegCreateKeyExW", 9, (uint32_t[]){
            key, wstr("Empty"), 0, 0, 0, KEY_ALL_ACCESS, 0, empty, disp }),
        ERROR_SUCCESS_VALUE);
    MEM32(len) = 64;
    oku("missing default reported",
        call("RegQueryValueA", 4, (uint32_t[]){ MEM32(empty), 0, data, len }),
        ERROR_FILE_NOT_FOUND_VALUE);
    oku("missing default zeroes length", MEM32(len), 0u);
    oku("invalid handle reported",
        call("RegQueryValueW", 4, (uint32_t[]){ 0x1234u, 0, widebuf, len }),
        ERROR_INVALID_HANDLE_VALUE);

    /* Persistence edge case: the writes above were saved to the private file;
     * verify the line format survived a round-trip through reg_save(). */
    FILE *f = fopen(path, "r");
    ok("persistence file created", f != NULL);
    if (f) {
        char line[2048], found_sz = 0, found_dword = 0, found_default = 0;
        while (fgets(line, sizeof line, f)) {
            if (strstr(line, "WideName=sz:wide-value")) found_sz = 1;
            if (strstr(line, "WideDword=dword:0xDEADBEEF")) found_dword = 1;
            if (strstr(line, "\\=") && strstr(line, "sz:default-ansi")) found_default = 1;
        }
        fclose(f);
        ok("wide string persisted", found_sz);
        ok("wide dword persisted", found_dword);
        ok("default value persisted", found_default);
        unlink(path);
    }

    /* Close the handles opened above. */
    ok("close key", call("RegCloseKey", 1, &key) == ERROR_SUCCESS_VALUE);
    uint32_t h = u32(); MEM32(h) = MEM32(nested);
    ok("close nested", call("RegCloseKey", 1, &h) == ERROR_SUCCESS_VALUE);
    h = u32(); MEM32(h) = MEM32(empty);
    ok("close empty", call("RegCloseKey", 1, &h) == ERROR_SUCCESS_VALUE);

    mach_leave();
    printf("registry_persistence_selftest: %s\n", fails ? "FAILED" : "all checks passed");
    return fails != 0;
}
