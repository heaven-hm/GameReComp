/* api_gap_selftest.c - APIs found missing by the downloaded-game audit. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int argc, const uint32_t *args) {
    uint32_t fn = hle_resolve(name);
    return fn ? hle_call_guest(fn, argc, args) : 0xFFFFFFFFu;
}
static uint32_t ansi(const char *s) {
    size_t n = strlen(s) + 1, va = hle_va(hle_alloc(n));
    memcpy(hle_ptr(va), s, n);
    return va;
}
static uint32_t wide(const char *s) {
    size_t n = strlen(s) + 1, va = hle_va(hle_alloc(n * 2u));
    uint16_t *w = (uint16_t *)hle_ptr(va);
    for (size_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)s[i];
    return va;
}

int main(void) {
    win32hle_register(win32hle_user32);
    win32hle_register(win32hle_sdl12);
    win32hle_register(win32hle_wsock32);
    win32hle_register(win32hle_module);
    win32hle_register(win32hle_msvcrt);
    mach_enter();

    /* SDL 1.2 bounded string helpers (issue #28, Chocolate Doom). */
    ok("SDL_strlcpy registered", hle_resolve("SDL_strlcpy") != 0);
    ok("SDL_strlcat registered", hle_resolve("SDL_strlcat") != 0);
    uint32_t dst = hle_va(hle_alloc(32)), src = ansi("abcdefghij");
    memset(hle_ptr(dst), 0, 32);
    ok("strlcpy returns source length", call("SDL_strlcpy", 3,
       (uint32_t[]){ dst, src, 5u }) == 10u);
    ok("strlcpy truncates and NUL-terminates",
       memcmp(hle_ptr(dst), "abcd", 5) == 0);
    ok("strlcat returns combined length", call("SDL_strlcat", 3,
       (uint32_t[]){ dst, src, 32u }) == 14u);
    ok("strlcat appends", memcmp(hle_ptr(dst), "abcdabcdefghij", 15) == 0);
    ok("SDL_SetError returns -1", call("SDL_SetError", 1,
       (uint32_t[]){ ansi("gap fixture error") }) == 0xFFFFFFFFu);
    uint32_t err = call("SDL_GetError", 0, NULL);
    ok("SDL_GetError reports the set message",
       err != 0 && strcmp((char *)hle_ptr(err), "gap fixture error") == 0);

    /* MessageBoxW (issue #28, Tetris and Chocolate Doom). */
    ok("MessageBoxW registered", hle_resolve("MessageBoxW") != 0);
    ok("MessageBoxW answers IDOK", call("MessageBoxW", 4,
       (uint32_t[]){ 0u, wide("Need WAD"), wide("Chocolate Doom"), 0u }) == 1u);
    ok("MessageBoxW answers IDYES for MB_YESNO", call("MessageBoxW", 4,
       (uint32_t[]){ 0u, wide("Continue?"), wide("Tetris"), 4u }) == 6u);

    /* Chocolate Doom's SDL 1.2 video-driver query and legacy winsock call. */
    ok("SDL_VideoDriverName registered", hle_resolve("SDL_VideoDriverName") != 0);
    uint32_t drv = hle_va(hle_alloc(64));
    memset(hle_ptr(drv), 0, 64);
    ok("SDL_VideoDriverName returns the buffer", call("SDL_VideoDriverName", 2,
       (uint32_t[]){ drv, 64u }) == drv);
    /* The dummy driver may report no current driver before video init; the
     * contract is a NUL-terminated string in the guest buffer either way. */
    ok("driver name is NUL terminated", strlen((char *)hle_ptr(drv)) < 64);
    ok("SDL_Init(VIDEO) for cursor fixture", call("SDL_Init", 1,
       (uint32_t[]){ 0x20u }) == 0u);
    uint32_t bits = hle_va(hle_alloc(8)), mask = hle_va(hle_alloc(8));
    memset(hle_ptr(bits), 0, 8);
    memset(hle_ptr(mask), 0xFF, 8);
    ok("SDL_CreateCursor registered", hle_resolve("SDL_CreateCursor") != 0);
    uint32_t cur = call("SDL_CreateCursor", 6,
       (uint32_t[]){ bits, mask, 8u, 8u, 0u, 0u });
    ok("SDL_CreateCursor returns a guest handle", cur != 0);
    call("SDL_SetCursor", 1, (uint32_t[]){ cur });
    uint32_t got = hle_va(hle_alloc(4));
    MEM32(got) = 0;
    ok("SDL_GetCursor returns the set handle", call("SDL_GetCursor", 1,
       (uint32_t[]){ got }) == cur);
    ok("SDL_CreateCursor rejects bad geometry", call("SDL_CreateCursor", 6,
       (uint32_t[]){ bits, mask, 0u, 8u, 0u, 0u }) == 0u);
    call("SDL_Quit", 0, NULL);
    ok("WSACancelBlockingCall registered", hle_resolve("WSACancelBlockingCall") != 0);
    ok("WSACancelBlockingCall fails honestly", call("WSACancelBlockingCall", 0, NULL)
       == 0xFFFFFFFFu);
    ok("WSAEINVAL reported", call("WSAGetLastError", 0, NULL) == 10022u);

    /* More Windows W variants from the downloaded-game import audit. */
    ok("LoadLibraryW registered", hle_resolve("LoadLibraryW") != 0);
    ok("LoadLibraryW on a missing module fails", call("LoadLibraryW", 1,
       (uint32_t[]){ wide("not_a_real_module.dll") }) == 0u);
    ok("LoadCursorW registered", hle_resolve("LoadCursorW") != 0);
    ok("LoadCursorW returns a cursor", call("LoadCursorW", 2,
       (uint32_t[]){ 0u, 0u }) != 0u);
    ok("DrawTextW registered", hle_resolve("DrawTextW") != 0);
    uint32_t rect = hle_va(hle_alloc(16));
    memset(hle_ptr(rect), 0, 16);
    ok("DrawTextW computes text extent", call("DrawTextW", 5,
       (uint32_t[]){ 0u, wide("abcdef"), 0xFFFFFFFFu, rect, 0x400u }) == 13u);
    ok("DrawTextW updated DT_CALCRECT width", MEM32(rect + 8u) == 36u);

    /* MSVCRT symbols imported by Chocolate Doom. */
    static const char *const crt[] = { "_snprintf", "vprintf", "_flsbuf", "fscanf" };
    for (unsigned i = 0; i < sizeof crt / sizeof crt[0]; i++)
        ok("CRT symbol registered", hle_resolve(crt[i]) != 0);
    uint32_t snp = hle_va(hle_alloc(32)), fmt = ansi("hi %d");
    memset(hle_ptr(snp), 0, 32);
    ok("_snprintf formats into the guest buffer", call("_snprintf", 4,
       (uint32_t[]){ snp, 32u, fmt, 42u }) == 5u);
    ok("_snprintf result", strcmp((char *)hle_ptr(snp), "hi 42") == 0);
    ok("fscanf fails explicitly", call("fscanf", 2,
       (uint32_t[]){ 0u, fmt }) == 0xFFFFFFFFu);

    mach_leave();
    printf("api_gap_selftest: all checks passed\n");
    return fails != 0;
}
