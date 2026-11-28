/* fullpath_w_selftest.c - GetFullPathNameW guest ABI. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t wide_guest(const char *s) {
    size_t n = strlen(s) + 1;
    uint32_t va = hle_va(hle_alloc((uint32_t)n * 2u));
    uint16_t *w = (uint16_t *)hle_ptr(va);
    for (size_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)s[i];
    return va;
}
static void narrow(const uint16_t *w, char *out, size_t cap) {
    size_t i = 0;
    for (; w[i] && i + 1 < cap; i++) out[i] = (char)w[i];
    out[i] = 0;
}

int main(void) {
    win32hle_register(win32hle_kernel32_ext);
    mach_enter();
    uint32_t fn = hle_resolve("GetFullPathNameW");
    ok("GetFullPathNameW registered", fn != 0);
    uint32_t name = wide_guest("sub\\file.txt");
    uint32_t out = hle_va(hle_alloc(2048));
    uint32_t part = hle_va(hle_alloc(4));
    uint32_t n = hle_call_guest(fn, 4, (uint32_t[]){ name, 1024, out, part });
    char result[1200], filename[64];
    narrow((const uint16_t *)hle_ptr(out), result, sizeof result);
    char cwd[768];
    ok("nonzero length", n > 0);
    ok("length matches output", n == strlen(result));
    ok("prefix is current directory", getcwd(cwd, sizeof cwd) && strncmp(result, cwd, strlen(cwd)) == 0);
    ok("suffix is input", strstr(result, "sub/file.txt") || strstr(result, "sub\\file.txt"));
    narrow((const uint16_t *)hle_ptr(MEM32(part)), filename, sizeof filename);
    ok("file part found", strcmp(filename, "file.txt") == 0);

    /* NULL output: required-size contract, no write through NULL. */
    uint32_t need = hle_call_guest(fn, 4, (uint32_t[]){ name, 0, 0, 0 });
    ok("required size is length+1", need == n + 1);

    mach_leave();
    printf("fullpath_w_selftest: all checks passed\n");
    return fails != 0;
}
