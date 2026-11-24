/*
 * fs_extra_selftest.c - SetFileAttributesW through the guest ABI.
 *
 * The temp directory is supplied at runtime (argv[1], then
 * HLE_FS_SELFTEST_DIR, then TMPDIR); a unique child is made with mkdtemp so
 * concurrent runs cannot collide. No user path is baked in. Drive C: is
 * mapped to that directory, so the guest names below exercise the W shim's
 * UTF-16 conversion, drive letter and backslash/forward-slash handling, and
 * case-insensitive component lookup through hle_host_path.
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

#define FILE_ATTRIBUTE_READONLY  0x01u
#define FILE_ATTRIBUTE_ARCHIVE   0x20u
#define FILE_ATTRIBUTE_NORMAL    0x80u
#define ERROR_FILE_NOT_FOUND     2u
#define ERROR_PATH_NOT_FOUND     3u
#define ERROR_INVALID_PARAMETER 87u
#define ERROR_INVALID_NAME     123u

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

/* A guest UTF-16 string, in guest memory. */
static uint32_t wstr(const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 1u;
    uint32_t va = hle_va(hle_alloc(n * 2u));
    uint16_t *w = (uint16_t *)hle_ptr(va);
    for (uint32_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)s[i];
    return va;
}
static uint32_t astr(const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 1u;
    uint32_t va = hle_va(hle_alloc(n));
    memcpy(hle_ptr(va), s, n);
    return va;
}

static uint32_t V_SetW, V_SetA, V_GetW;

static uint32_t set_w(const char *name, uint32_t attrs) {
    return hle_call_guest(V_SetW, 2, (uint32_t[]){ wstr(name), attrs });
}
static uint32_t set_a(const char *name, uint32_t attrs) {
    return hle_call_guest(V_SetA, 2, (uint32_t[]){ astr(name), attrs });
}
static uint32_t get_w(const char *name) {
    return hle_call_guest(V_GetW, 1, (uint32_t[]){ wstr(name) });
}
static int host_mode_has(const char *path, mode_t bits) {
    struct stat st;
    return stat(path, &st) == 0 && (st.st_mode & bits) != 0;
}
static int host_mode_lacks(const char *path, mode_t bits) {
    struct stat st;
    return stat(path, &st) == 0 && (st.st_mode & bits) == 0;
}

int main(int argc, char **argv) {
    const char *base = argc > 1 && argv[1] && *argv[1] ? argv[1] : getenv("HLE_FS_SELFTEST_DIR");
    if (!base || !*base) base = getenv("TMPDIR");
    if (!base || !*base) base = "/tmp";
    char dir[1024];
    snprintf(dir, sizeof dir, "%s/fs_extra_XXXXXX", base);
    if (!mkdtemp(dir)) { printf("FAIL could not make temp dir under %s\n", base); return 1; }

    char top[1200], sub[1200], inner[1200];
    snprintf(top, sizeof top, "%s/ReadOnly.TXT", dir);
    snprintf(sub, sizeof sub, "%s/SubDir", dir);
    snprintf(inner, sizeof inner, "%s/SubDir/Inner.bin", dir);
    FILE *f = fopen(top, "wb");
    int made = f != NULL;
    if (f) { fputs("x", f); fclose(f); }
    made = made && mkdir(sub, 0755) == 0;
    f = made ? fopen(inner, "wb") : NULL;
    if (f) { fputs("y", f); fclose(f); }
    made = made && f != NULL;
    if (!made) { printf("FAIL could not create fixtures under %s\n", dir); return 1; }

    win32hle_register(win32hle_kernel32_ext);
    mach_enter();
    V_SetW = hle_resolve("SetFileAttributesW");
    V_SetA = hle_resolve("SetFileAttributesA");
    V_GetW = hle_resolve("GetFileAttributesW");
    ok("filesystem shims resolve", V_SetW && V_SetA && V_GetW);
    hle_set_drive('C', dir);

    /* READONLY on a mixed-case name with a drive letter and a backslash. */
    ok("SetFileAttributesW READONLY succeeds", set_w("C:\\ReadOnly.TXT", FILE_ATTRIBUTE_READONLY) == 1);
    ok("SetFileAttributesW clears the host write bits",
       host_mode_lacks(top, S_IWUSR | S_IWGRP | S_IWOTH));
    ok("GetFileAttributesW sees READONLY", (get_w("C:\\ReadOnly.TXT") & FILE_ATTRIBUTE_READONLY) != 0);

    /* NORMAL on an all-lowercase spelling: the component is found without case. */
    ok("SetFileAttributesW NORMAL succeeds", set_w("c:\\readonly.txt", FILE_ATTRIBUTE_NORMAL) == 1);
    ok("SetFileAttributesW restores the write bit", host_mode_has(top, S_IWUSR));
    ok("GetFileAttributesW no longer sees READONLY", (get_w("C:\\ReadOnly.TXT") & FILE_ATTRIBUTE_READONLY) == 0);

    /* A nested directory, once with backslashes and once with forward slashes. */
    ok("SetFileAttributesW nested backslash path", set_w("C:\\SubDir\\Inner.bin", FILE_ATTRIBUTE_READONLY) == 1);
    ok("nested file is readonly on the host", host_mode_lacks(inner, S_IWUSR));
    ok("SetFileAttributesW nested forward-slash path", set_w("C:/SubDir/Inner.bin", FILE_ATTRIBUTE_ARCHIVE) == 1);
    ok("forward-slash path clears READONLY", host_mode_has(inner, S_IWUSR));

    /* The non-POSIX settable bits are accepted without disturbing READONLY. */
    ok("SetFileAttributesW HIDDEN|SYSTEM|ARCHIVE succeeds",
       set_w("C:\\SubDir\\Inner.bin", 0x02u | 0x04u | FILE_ATTRIBUTE_ARCHIVE) == 1);
    ok("archived file stays writable", host_mode_has(inner, S_IWUSR));

    /* Honest errors: missing file, an invalid combination, an unsupported bit,
     * a wide character the ANSI codepage cannot carry, and an empty name. */
    ok("missing file fails", set_w("C:\\NoSuchFile.txt", FILE_ATTRIBUTE_NORMAL) == 0);
    ok("missing file sets ERROR_FILE_NOT_FOUND", hle_get_last_error() == ERROR_FILE_NOT_FOUND);
    ok("invalid attr combination fails", set_w("C:\\ReadOnly.TXT", FILE_ATTRIBUTE_NORMAL | FILE_ATTRIBUTE_READONLY) == 0);
    ok("invalid combination sets ERROR_INVALID_PARAMETER", hle_get_last_error() == ERROR_INVALID_PARAMETER);
    ok("unsupported DIRECTORY bit fails", set_w("C:\\ReadOnly.TXT", 0x10u) == 0);
    ok("unsupported bit sets ERROR_INVALID_PARAMETER", hle_get_last_error() == ERROR_INVALID_PARAMETER);
    {
        uint32_t bad = wstr("C:\\X.txt");
        ((uint16_t *)hle_ptr(bad))[3] = 0x100u;       /* U+0100 is not in codepage 1252 */
        ok("unrepresentable wide name fails", hle_call_guest(V_SetW, 2, (uint32_t[]){ bad, FILE_ATTRIBUTE_NORMAL }) == 0);
        ok("unrepresentable name sets ERROR_INVALID_NAME", hle_get_last_error() == ERROR_INVALID_NAME);
    }
    ok("empty name fails", set_w("", FILE_ATTRIBUTE_NORMAL) == 0);
    ok("empty name sets ERROR_PATH_NOT_FOUND", hle_get_last_error() == ERROR_PATH_NOT_FOUND);

    /* The A form shares the implementation, so it has the same real effect. */
    ok("SetFileAttributesA READONLY succeeds", set_a("C:\\ReadOnly.TXT", FILE_ATTRIBUTE_READONLY) == 1);
    ok("A form makes the file unwritable", host_mode_lacks(top, S_IWUSR));
    ok("SetFileAttributesA NORMAL succeeds", set_a("C:\\ReadOnly.TXT", FILE_ATTRIBUTE_NORMAL) == 1);
    ok("A form restores the write bit", host_mode_has(top, S_IWUSR));

    mach_leave();

    unlink(top);
    unlink(inner);
    rmdir(sub);
    rmdir(dir);
    if (fails == 0) printf("fs_extra_selftest: all checks passed\n");
    return fails != 0;
}
