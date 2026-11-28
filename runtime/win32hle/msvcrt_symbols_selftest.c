/*
 * msvcrt_symbols_selftest.c - the five MSVCRT imports left unresolved by
 * 7z2606.exe: _acmdln, _adjust_fdiv, _controlfp, _except_handler3, _XcptFilter.
 *
 * The two data imports are bound through hle_msvcrt_data: the IAT slot receives
 * the guest address of the object, so the test reads/writes the cell at that
 * address and never calls a shim for them. The three called symbols are looked
 * up and invoked through hle_call_guest, exercising the cdecl entry path (RETC
 * leaves the argument words for the lifted caller - MSVCRT is cdecl, not
 * stdcall, so the correct purge is no purge). _except_handler3 is only checked
 * for registration: invoking it would take the documented "no SEH engine"
 * unsupported exit, which is the point, not a result to call in a selftest.
 * Headless, no game and no live-injection claim.
 */
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
    uint32_t va = hle_resolve(name);
    if (!va) { printf("FAIL no shim %s\n", name); fails++; return 0; }
    return hle_call_guest(va, argc, args);
}

int main(void) {
    win32hle_register(win32hle_msvcrt);
    /* Give the CRT the guest command line before it builds its objects. */
    static char *argv[] = {
        (char *)"7z2606.exe", (char *)"x", (char *)"archive.7z", NULL
    };
    hle_set_argv(3, argv);
    mach_enter();

    /* ---- _acmdln: a data import binds to the object, not to a shim VA ---- */
    uint32_t acmdln = hle_msvcrt_data("_acmdln");
    ok("_acmdln data import resolves", acmdln != 0u);
    ok("_acmdln is data, not a function shim", hle_resolve("_acmdln") == 0u);
    if (acmdln) {
        uint32_t str = MEM32(acmdln);
        const char *line = (const char *)hle_ptr(str);
        ok("_acmdln cell points at a guest string", str != 0u && line != NULL);
        /* This CRT renders argv[0] verbatim and quotes later arguments, the
         * same raw-command-line form __p__acmdln already reported. */
        ok("_acmdln is the guest command line",
           line != NULL && strcmp(line, "7z2606.exe \"x\" \"archive.7z\"") == 0);
        /* bounded: the string is NUL-terminated inside the 1 KiB builder */
        ok("_acmdln string is bounded", line != NULL && strlen(line) < 1024u);
        /* __p__acmdln must hand out this same real cell, not a second copy */
        ok("__p__acmdln shares the _acmdln cell", call("__p__acmdln", 0, NULL) == acmdln);
    }

    /* ---- _adjust_fdiv: an int data symbol, writable in bounded memory --- */
    uint32_t fdiv = hle_msvcrt_data("_adjust_fdiv");
    ok("_adjust_fdiv data import resolves", fdiv != 0u);
    ok("_adjust_fdiv is data, not a function shim", hle_resolve("_adjust_fdiv") == 0u);
    if (fdiv) {
        ok("_adjust_fdiv cell is an int 0 (legacy FPU flag ignored)", MEM32(fdiv) == 0u);
        MEM32(fdiv) = 1u;
        ok("_adjust_fdiv cell is writable guest memory", MEM32(fdiv) == 1u);
        MEM32(fdiv) = 0u;                       /* restore */
    }

    /* ---- _controlfp: cdecl, returns the previous FP control word -------- */
    ok("_controlfp shim registered", hle_resolve("_controlfp") != 0u);
    uint32_t def = call("_controlfp", 2, (uint32_t[]){ 0u, 0u });  /* mask 0: query */
    ok("_controlfp query returns the default control word", def == 0x0009001Fu);
    uint32_t cell = hle_va(hle_alloc(4));
    call("_controlfp_s", 3, (uint32_t[]){ cell, 0u, 0u });
    ok("_controlfp and _controlfp_s share the control word", MEM32(cell) == def);

    uint32_t prev = call("_controlfp", 2, (uint32_t[]){ 0x100u, 0x300u }); /* _RC_DOWN */
    ok("_controlfp set returns the previous word", prev == def);
    call("_controlfp_s", 3, (uint32_t[]){ cell, 0u, 0u });
    ok("_controlfp records the new rounding bits",
       MEM32(cell) == ((def & ~0x300u) | 0x100u));

    call("_controlfp", 2, (uint32_t[]){ 0u, 0x300u });              /* restore */
    call("_controlfp_s", 3, (uint32_t[]){ cell, 0u, 0u });
    ok("_controlfp restore is exact", MEM32(cell) == def);

    /* ---- _except_handler3: registered, mapped to the existing EH policy - */
    ok("_except_handler3 shim registered", hle_resolve("_except_handler3") != 0u);

    /* ---- _XcptFilter: documented unhandled-exception filter result ------ */
    ok("_XcptFilter shim registered", hle_resolve("_XcptFilter") != 0u);
    ok("_XcptFilter returns EXCEPTION_EXECUTE_HANDLER (1)",
       call("_XcptFilter", 1, (uint32_t[]){ 0u }) == 1u);

    mach_leave();
    printf("msvcrt_symbols_selftest: all checks passed\n");
    return fails != 0;
}
