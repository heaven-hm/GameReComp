/* registry_w_selftest.c - RegEnumKeyExW over the isolated registry store.
 *
 * Drives the guest ABI through hle_call_guest: wide subkey names, WCHAR buffer
 * capacities, the ERROR_MORE_DATA / ERROR_NO_MORE_ITEMS contracts, optional
 * buffers, and cleanup.  Child keys are created with RegCreateKeyExA (the only
 * create shim in this layer) and enumerated through the W form, so both forms
 * are checked against the same store. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

#define HKEY_CURRENT_USER 0x80000001u
#define KEY_ALL_ACCESS_VALUE 0xF003Fu
#define ERROR_SUCCESS_VALUE 0u
#define ERROR_INVALID_HANDLE_VALUE 6u
#define ERROR_INVALID_PARAMETER_VALUE 87u
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
/* ASCII guest UTF-16 buffer equals s, NUL-terminated, nothing more required. */
static int widecmp(const uint16_t *w, const char *s) {
    size_t i = 0;
    for (; s[i]; i++) if (w[i] != (uint16_t)(unsigned char)s[i]) return 0;
    return w[i] == 0;
}

int main(void) {
    win32hle_register(win32hle_advapi32);
    mach_enter();
    if (!hle_resolve("RegEnumKeyExW")) { printf("FAIL missing RegEnumKeyExW\n"); fails++; }

    uint32_t parent_path = cstr("Software\\GameReCompWEnumTest");
    uint32_t out = hle_va(hle_alloc(4)), disp = hle_va(hle_alloc(4));
    ok("create parent", call("RegCreateKeyExA", 9, (uint32_t[]){
        HKEY_CURRENT_USER, parent_path, 0, 0, 0, KEY_ALL_ACCESS_VALUE, 0, out, disp }) == ERROR_SUCCESS_VALUE);
    uint32_t parent = MEM32(out);
    ok("parent handle", parent != 0);

    /* subkey() prepends, so enumeration order is the reverse of creation:
     * index 0 = Caf\x80, index 1 = Beta, index 2 = Alpha. */
    static const char *child_names[] = { "Alpha", "Beta", "Caf\x80" };
    uint32_t created[3];
    for (unsigned i = 0; i < 3; i++) {
        ok("create child", call("RegCreateKeyExA", 9, (uint32_t[]){
            parent, cstr(child_names[i]), 0, 0, 0, KEY_ALL_ACCESS_VALUE, 0, out, disp }) == ERROR_SUCCESS_VALUE);
        created[i] = MEM32(out);
        ok("child handle", created[i] != 0);
    }

    uint32_t name = hle_va(hle_alloc(256)), name_len = hle_va(hle_alloc(4));
    uint32_t cls = hle_va(hle_alloc(8)), cls_len = hle_va(hle_alloc(4));
    uint32_t ft = hle_va(hle_alloc(8));
    uint16_t *w = (uint16_t *)hle_ptr(name);

    /* index 0: the CP1252 byte 0x80 must arrive as U+20AC (EURO SIGN), and
     * the class/last-write buffers (both untracked here) must be cleared. */
    memset(hle_ptr(name), 0xAA, 256);
    MEM32(name_len) = 128; MEM32(cls_len) = 4;
    ok("enum index 0", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 0, name, name_len, 0, cls, cls_len, ft }) == ERROR_SUCCESS_VALUE);
    ok("wide euro name", w[0] == 'C' && w[1] == 'a' && w[2] == 'f' && w[3] == 0x20AC && w[4] == 0);
    ok("wide length excludes NUL", MEM32(name_len) == 4);
    ok("empty class", MEM32(cls_len) == 0 && MEM16(cls) == 0);
    ok("last-write zeroed", MEM32(ft) == 0 && MEM32(ft + 4) == 0);
    ok("no write past name", ((uint8_t *)hle_ptr(name))[10] == 0xAA);

    /* index 1 and 2: plain ASCII names, with all optional outputs NULL. */
    MEM32(name_len) = 128;
    ok("enum index 1", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 1, name, name_len, 0, 0, 0, 0 }) == ERROR_SUCCESS_VALUE);
    ok("wide Beta", widecmp(w, "Beta"));
    ok("Beta length", MEM32(name_len) == 4);
    ok("Beta NUL", w[4] == 0);

    MEM32(name_len) = 128;
    ok("enum index 2", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 2, name, name_len, 0, 0, 0, 0 }) == ERROR_SUCCESS_VALUE);
    ok("wide Alpha", widecmp(w, "Alpha"));
    ok("Alpha length", MEM32(name_len) == 5);
    ok("Alpha NUL", w[5] == 0);

    /* Capacity counts the NUL: 5 chars need 6 WCHARs. Too small is an honest
     * ERROR_MORE_DATA that leaves both the buffer and the capacity untouched. */
    memset(hle_ptr(name), 0xAA, 256);
    MEM32(name_len) = 5;
    ok("more data at exact chars", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 2, name, name_len, 0, 0, 0, 0 }) == ERROR_MORE_DATA_VALUE);
    ok("more data keeps capacity", MEM32(name_len) == 5);
    ok("more data leaves buffer", ((uint8_t *)hle_ptr(name))[0] == 0xAA);
    MEM32(name_len) = 6;
    ok("retry with room for NUL", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 2, name, name_len, 0, 0, 0, 0 }) == ERROR_SUCCESS_VALUE);
    ok("retry name", widecmp(w, "Alpha"));
    ok("retry length", MEM32(name_len) == 5);

    /* Past the last subkey nothing is copied or resized. */
    memset(hle_ptr(name), 0xAA, 32);
    MEM32(name_len) = 128;
    ok("past end", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 3, name, name_len, 0, 0, 0, 0 }) == ERROR_NO_MORE_ITEMS_VALUE);
    ok("past end leaves buffer", ((uint8_t *)hle_ptr(name))[0] == 0xAA);
    ok("past end leaves capacity", MEM32(name_len) == 128);

    /* Required parameters, the reserved word, and the optional class buffer's
     * own capacity are all validated rather than guessed at. */
    ok("null name", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 0, 0, name_len, 0, 0, 0, 0 }) == ERROR_INVALID_PARAMETER_VALUE);
    ok("null name capacity", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 0, name, 0, 0, 0, 0, 0 }) == ERROR_INVALID_PARAMETER_VALUE);
    ok("reserved nonzero", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 0, name, name_len, 1, 0, 0, 0 }) == ERROR_INVALID_PARAMETER_VALUE);
    ok("class without capacity", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 0, name, name_len, 0, cls, 0, 0 }) == ERROR_INVALID_PARAMETER_VALUE);
    MEM32(cls_len) = 0;
    ok("class capacity zero", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 0, name, name_len, 0, cls, cls_len, 0 }) == ERROR_MORE_DATA_VALUE);
    ok("invalid handle", call("RegEnumKeyExW", 8, (uint32_t[]){
        0xDEADBEEFu, 0, name, name_len, 0, 0, 0, 0 }) == ERROR_INVALID_HANDLE_VALUE);

    /* Cleanup: the W form must enumerate what the A form can delete. */
    for (unsigned i = 0; i < 3; i++)
        ok("delete child", call("RegDeleteKeyA", 2, (uint32_t[]){ parent, cstr(child_names[i]) }) == ERROR_SUCCESS_VALUE);
    ok("empty enumeration after cleanup", call("RegEnumKeyExW", 8, (uint32_t[]){
        parent, 0, name, name_len, 0, 0, 0, 0 }) == ERROR_NO_MORE_ITEMS_VALUE);
    ok("delete parent", call("RegDeleteKeyA", 2, (uint32_t[]){ HKEY_CURRENT_USER, cstr("Software\\GameReCompWEnumTest") }) == ERROR_SUCCESS_VALUE);
    for (unsigned i = 0; i < 3; i++)
        ok("close child", call("RegCloseKey", 1, &created[i]) == ERROR_SUCCESS_VALUE);
    ok("close parent", call("RegCloseKey", 1, &parent) == ERROR_SUCCESS_VALUE);

    mach_leave();
    printf("registry_w_selftest: all checks passed\n");
    return fails != 0;
}
