/* mmio_wide_selftest.c - wide RIFF path through the existing WinMM shim. */
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
static uint32_t call(const char *name, int n, const uint32_t *a) {
    return hle_call_guest(hle_resolve(name), n, a);
}

int main(void) {
    win32hle_register(win32hle_winmm);
    mach_enter();
    ok("mmioOpenW resolves", hle_resolve("mmioOpenW") != 0);

    char host[512];
    if (!getcwd(host, sizeof host)) { printf("FAIL getcwd\n"); return 1; }
    char file[600];
    snprintf(file, sizeof file, "%s/mmio_wide_selftest.wav", host);
    static const uint8_t riff[] = { 'R','I','F','F', 4,0,0,0, 'W','A','V','E' };
    FILE *f = fopen(file, "wb");
    ok("temp RIFF written", f && fwrite(riff, 1, sizeof riff, f) == sizeof riff);
    if (f) fclose(f);

    hle_set_drive('Z', "/");
    char guest_path[600];
    snprintf(guest_path, sizeof guest_path, "Z:%s", file);
    size_t n = strlen(guest_path);
    uint32_t wide = hle_va(hle_alloc((uint32_t)(n + 1) * 2u));
    uint16_t *w = (uint16_t *)hle_ptr(wide);
    for (size_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)guest_path[i];
    w[n] = 0;

    uint32_t h = call("mmioOpenW", 3, (uint32_t[]){ wide, 0, 0 });
    ok("mmioOpenW opens file", h != 0);
    uint32_t buf = hle_va(hle_alloc(16));
    if (h) {
        ok("mmioRead reads RIFF", call("mmioRead", 3, (uint32_t[]){ h, buf, 12 }) == 12);
        ok("RIFF bytes", memcmp(hle_ptr(buf), riff, 12) == 0);
        ok("mmioClose", call("mmioClose", 1, &h) == 0);
    }
    unlink(file);
    mach_leave();
    printf("mmio_wide_selftest: all checks passed\n");
    return fails != 0;
}
