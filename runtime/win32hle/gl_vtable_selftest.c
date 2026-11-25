/*
 * gl_vtable_selftest.c - deterministic guest-ABI evidence for issue #27.
 *
 * What was actually measured on the exact gr.exe (SHA-256
 * 6ea800ee918547ca1953a24bebdcd07c29cef4ada617d7321a7dc3701e825636):
 *
 *   ICALL: null target from 0x0040942C / 0x00409324 / 0x00419D38
 *   fatal signal 10 in lifted function 0x00419D38
 *
 * At 0x0040942C the guest loads GameManager+0x74 (its InGameState), its vptr
 * and vtable slot +0x58, then calls the result.  GameManager+0x74 is NULL
 * because prefManager.load()'s DMD OpenEx("gr.prf") was dispatched to the
 * catch funclet 0x0040DC96 but resumed in guest frame 0x00419CC0 (exact=0,
 * an inner checkpoint) instead of the frame that owns the catch.  The
 * funclet therefore returns to the wrong guest caller, skipping
 * gameManager.init(), which is the only code that assigns InGameState.
 * SDL_GL_SwapBuffers is never reached, so no frame is presented.
 *
 * This selftest does NOT run the game and does NOT claim a frame.  It proves
 * the guest-ABI chain and the fail-closed reason names deterministically, so
 * a future EH/lifter fix can be checked against the same four words:
 *
 *     object -> [object + 0x74] -> vptr -> [vptr + 0x58] -> method VA
 *
 * No shim in sdl12.c returns a null vtable: the null word is guest-owned.
 * The real fix belongs in the EH resume/lifter path (an EH checkpoint in the
 * tail-call-inlined catch owner), outside this shim's file scope.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

/* The measured gr.exe chain: GameManager.InGameState at +0x74, and
 * InGameState.loadReplay at vtable slot +0x58. */
enum { IFACE_OFF = 0x74, SLOT_OFF = 0x58 };
static const uint32_t METHOD_VA = 0x00419F58u;

static int fails;
static void ok(const char *what, int cond) {
    if (cond) printf("ok    %s\n", what);
    else { printf("FAIL  %s\n", what); fails++; }
}

int main(void) {
    /* One writable guest object, interface cell and vtable slot. */
    uint32_t obj = hle_va(hle_alloc(0x80));
    uint32_t iface = hle_va(hle_alloc(4));
    uint32_t vtable = hle_va(hle_alloc(0x60));
    uint32_t target = 0xDEADBEEFu;
    int rc;

    memset(hle_ptr(obj), 0, 0x80);
    memset(hle_ptr(vtable), 0, 0x60);
    MEM32(iface) = vtable;
    MEM32(vtable + SLOT_OFF) = METHOD_VA;
    MEM32(obj + IFACE_OFF) = iface;

    rc = hle_guest_vcall_target(obj, IFACE_OFF, SLOT_OFF, &target);
    ok("valid chain resolves the guest method", rc == HLE_VCHAIN_OK && target == METHOD_VA);

    /* The measured fault: the interface/subobject word is NULL. */
    MEM32(obj + IFACE_OFF) = 0;
    target = 0xDEADBEEFu;
    rc = hle_guest_vcall_target(obj, IFACE_OFF, SLOT_OFF, &target);
    ok("null interface is named, not a generic null target",
       rc == HLE_VCHAIN_NO_INTERFACE &&
       strcmp(hle_guest_vcall_reason(rc), "NULL guest interface/subobject") == 0);
    ok("failed validation never invents a target", target == 0);

    /* A constructed object with an uninitialised vptr: distinct reason. */
    MEM32(obj + IFACE_OFF) = iface;
    MEM32(iface) = 0;
    rc = hle_guest_vcall_target(obj, IFACE_OFF, SLOT_OFF, &target);
    ok("null vtable is a distinct named defect",
       rc == HLE_VCHAIN_NO_VTABLE &&
       strstr(hle_guest_vcall_reason(rc), "never constructed") != NULL);

    /* A vtable that ends before the slot the guest calls. */
    MEM32(iface) = vtable;
    MEM32(vtable + SLOT_OFF) = 0;
    rc = hle_guest_vcall_target(obj, IFACE_OFF, SLOT_OFF, &target);
    ok("null method slot is a distinct named defect",
       rc == HLE_VCHAIN_NO_TARGET && target == 0);

    rc = hle_guest_vcall_target(0, IFACE_OFF, SLOT_OFF, &target);
    ok("null object is refused before any read",
       rc == HLE_VCHAIN_NO_OBJECT && target == 0);
    ok("a NULL target pointer argument is tolerated",
       hle_guest_vcall_target(obj, IFACE_OFF, SLOT_OFF, NULL) == HLE_VCHAIN_NO_TARGET);

    /* Fail closed about the thing this issue actually asks for. */
    printf("present NOT_TESTED; no frame claimed\n");
    if (fails) return 1;
    printf("gl_vtable_selftest: all checks passed\n");
    return 0;
}
