/* registry_apis_selftest.c - Unicode open + value enumeration in the registry. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

#define HKEY_CURRENT_USER 0x80000001u
#define REG_SZ 1u
#define REG_DWORD 4u
#define ERROR_SUCCESS_VALUE 0u
#define ERROR_MORE_DATA_VALUE 234u
#define ERROR_NO_MORE_ITEMS_VALUE 259u

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *n, int argc, const uint32_t *a) {
    return hle_call_guest(hle_resolve(n), argc, a);
}
static uint32_t cstr(const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 1, va = hle_va(hle_alloc(n));
    memcpy(hle_ptr(va), s, n);
    return va;
}
static uint32_t wstr(const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 1, va = hle_va(hle_alloc(n * 2u));
    uint16_t *w = (uint16_t *)hle_ptr(va);
    for (uint32_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)s[i];
    return va;
}

int main(void) {
    win32hle_register(win32hle_advapi32);
    mach_enter();
    static const char *names[] = { "RegCreateKeyExA", "RegSetValueExA",
            "RegOpenKeyExW", "RegEnumValueA", "RegEnumValueW", "RegDeleteValueA",
            "RegDeleteKeyA", "RegCloseKey" };
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; i++)
        if (!hle_resolve(names[i])) { printf("FAIL missing %s\n", names[i]); fails++; }
    uint32_t sub = cstr("Software\\GameReCompTest");
    uint32_t key_va = hle_va(hle_alloc(4)), disp = hle_va(hle_alloc(4));
    ok("create key", call("RegCreateKeyExA", 9, (uint32_t[]){
        HKEY_CURRENT_USER, sub, 0, 0, 0, 0xF003Fu, 0, key_va, disp }) == ERROR_SUCCESS_VALUE);
    uint32_t key = MEM32(key_va);
    ok("key handle", key != 0);

    uint32_t val_va = hle_va(hle_alloc(4));
    MEM32(val_va) = 0x12345678u;
    ok("set DWORD", call("RegSetValueExA", 6, (uint32_t[]){
        key, cstr("Alpha"), 0, REG_DWORD, val_va, 4 }) == ERROR_SUCCESS_VALUE);
    ok("set SZ", call("RegSetValueExA", 6, (uint32_t[]){
        key, cstr("Beta"), 0, REG_SZ, cstr("hello"), 6 }) == ERROR_SUCCESS_VALUE);

    uint32_t name = hle_va(hle_alloc(128)), name_len = hle_va(hle_alloc(4));
    uint32_t type = hle_va(hle_alloc(4)), data = hle_va(hle_alloc(64)), data_len = hle_va(hle_alloc(4));
    int saw_alpha = 0, saw_beta = 0;
    for (uint32_t i = 0; i < 2; i++) {
        MEM32(name_len) = 128; MEM32(data_len) = 64;
        ok("enum value", call("RegEnumValueA", 8, (uint32_t[]){
            key, i, name, name_len, 0, type, data, data_len }) == ERROR_SUCCESS_VALUE);
        if (!strcmp((char *)hle_ptr(name), "Alpha")) {
            saw_alpha = 1;
            ok("type REG_DWORD", MEM32(type) == REG_DWORD);
            ok("data dword", MEM32(data) == 0x12345678u);
        } else if (!strcmp((char *)hle_ptr(name), "Beta")) {
            saw_beta = 1;
            ok("type REG_SZ", MEM32(type) == REG_SZ);
            ok("data string", memcmp(hle_ptr(data), "hello", 6) == 0);
        } else ok("known value name", 0);
    }
    ok("all values enumerated", saw_alpha && saw_beta);
    /* The W form returns the same data with a UTF-16 name buffer. */
    int saw_beta_w = 0;
    for (uint32_t i = 0; i < 2; i++) {
        MEM32(name_len) = 128; MEM32(data_len) = 64;
        if (call("RegEnumValueW", 8, (uint32_t[]){
                key, i, name, name_len, 0, type, data, data_len }) != ERROR_SUCCESS_VALUE) continue;
        uint16_t *wname = (uint16_t *)hle_ptr(name);
        if (wname[0] == 'B' && wname[1] == 'e' && wname[2] == 't' && wname[3] == 'a' && wname[4] == 0) {
            saw_beta_w = 1;
            ok("wide type REG_SZ", MEM32(type) == REG_SZ);
            ok("wide data string", memcmp(hle_ptr(data), "hello", 6) == 0);
        }
    }
    ok("wide name Beta", saw_beta_w);
    ok("enum past end", call("RegEnumValueA", 8, (uint32_t[]){
        key, 99, name, name_len, 0, type, data, data_len }) == ERROR_NO_MORE_ITEMS_VALUE);
    uint32_t key2 = hle_va(hle_alloc(4));
    ok("open key by wide path", call("RegOpenKeyExW", 5, (uint32_t[]){
        HKEY_CURRENT_USER, wstr("Software\\GameReCompTest"), 0, 0, key2 }) == ERROR_SUCCESS_VALUE);
    ok("same store", MEM32(key2) != 0);

    ok("delete value", call("RegDeleteValueA", 2,
        (uint32_t[]){ key, cstr("Alpha") }) == ERROR_SUCCESS_VALUE);
    ok("delete key", call("RegDeleteKeyA", 2, (uint32_t[]){ key, 0 }) == ERROR_SUCCESS_VALUE);
    ok("close first handle", call("RegCloseKey", 1, &key) == ERROR_SUCCESS_VALUE);
    uint32_t key2copy = hle_va(hle_alloc(4));
    MEM32(key2copy) = MEM32(key2);
    ok("close second handle", call("RegCloseKey", 1, &key2copy) == ERROR_SUCCESS_VALUE);

    mach_leave();
    printf("registry_apis_selftest: all checks passed\n");
    return fails != 0;
}
