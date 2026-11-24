/*
 * slist_selftest.c - the four APIs added to kernel32_crt.c for the coverage
 * audit, driven through the real stdcall bridge (hle_call_guest) with guest
 * addresses and guest memory only:
 *
 *   - InterlockedPushEntrySList builds an x86 SLIST in the guest's memory using
 *     the real guest structure layout (SLIST_ENTRY = one Next word;
 *     SLIST_HEADER = 8 bytes, Next at +0, Depth:16/Sequence:16 at +4). It
 *     checks the returned previous head, the linked chain, the packed Depth and
 *     Sequence, the 8-byte footprint, and LIFO order.
 *   - InterlockedFlushSList detaches the whole chain in one step, returns its
 *     old head, clears Depth, preserves Sequence, and leaves an empty header
 *     untouched. A push after the flush reuses the list.
 *   - RtlPcToFileHeader reports the loader's real module base for a PC in a
 *     mapped PE32 image, and NULL/zero for an unmapped PC; a NULL out pointer
 *     is tolerated.
 *   - RtlUnwindEx performs NO unwind: it reports ERROR_CALL_NOT_IMPLEMENTED,
 *     leaves the guest's registers and stack alone, and returns void. Nothing
 *     is fabricated to look like a completed unwind.
 *
 * Each shim is also called with a hand-built stdcall frame to confirm it pops
 * exactly its own arguments (the shim table carries no arity).
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#define NOT_IMPL 120u                       /* ERROR_CALL_NOT_IMPLEMENTED */
#define BAD_PARAM 87u                       /* ERROR_INVALID_PARAMETER    */

/* x86 guest layout constants (see kernel32_crt.c). */
#define SLIST_NEXT_OFF   0u
#define SLIST_COUNT_OFF  4u
#define SLIST_HEADER_SZ  8u
#define SLIST_ENTRY_SZ   4u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
/* RECOMP_ICALL publishes the arithmetic flags through these; generated bodies
 * declare them themselves, this driver is not one, so they live here. */
static uint32_t _flag_k = FK_NONE, _flag_a, _flag_b, _cf;

static uint32_t call(const char *name, int argc, const uint32_t *a) {
    uint32_t va = hle_resolve(name);
    if (!va) hle_fatal("selftest: no shim named %s", name);
    return hle_call_guest(va, argc, a);
}
/* A guest-side stdcall call leaves the esp change visible (hle_call_guest
 * restores it on return), so this checks each shim's argument count. */
static uint32_t scall(const char *name, int argc, const uint32_t *a) {
    uint32_t va = hle_resolve(name);
    if (!va) hle_fatal("selftest: no shim named %s", name);
    for (int i = argc - 1; i >= 0; i--) PUSH32(esp, a[i]);
    RECOMP_ICALL(va);
    return eax;                             /* the shim popped ret + its args */
}
/* An 8-byte guest header with a canary word on either side. */
static uint32_t header_va;
static void header_reset(void) {
    uint32_t raw = header_va - 4u;
    memset(hle_ptr(raw), 0xCC, 16u);
    MEM32(header_va + SLIST_NEXT_OFF) = 0;
    MEM32(header_va + SLIST_COUNT_OFF) = 0;
}
static int header_canaries_ok(void) {
    return MEM32(header_va - 4u) == 0xCCCCCCCCu &&
           MEM32(header_va + SLIST_HEADER_SZ) == 0xCCCCCCCCu;
}

int main(void) {
    win32hle_register(win32hle_kernel32_crt);
    mach_enter();

    ok("four shims registered",
       hle_resolve("InterlockedPushEntrySList") != 0 &&
       hle_resolve("InterlockedFlushSList") != 0 &&
       hle_resolve("RtlPcToFileHeader") != 0 &&
       hle_resolve("RtlUnwindEx") != 0);
    if (!hle_resolve("InterlockedPushEntrySList") ||
        !hle_resolve("InterlockedFlushSList") ||
        !hle_resolve("RtlPcToFileHeader") ||
        !hle_resolve("RtlUnwindEx")) return 1;

    /* --- guest storage ---------------------------------------------------- */
    header_va = hle_va(hle_alloc(16u)) + 4u;
    ok("header allocated", header_va != 4u);
    if (header_va == 4u) return 1;
    header_reset();

    /* Three entries, each with a trailing canary word. */
    uint32_t e[3];
    for (int i = 0; i < 3; i++) {
        e[i] = hle_va(hle_alloc(8u));
        ok("entry allocated", e[i] != 0);
        memset(hle_ptr(e[i]), 0, 8u);
    }

    /* --- InterlockedPushEntrySList: LIFO push, real header layout --------- */
    ok("first push returns NULL",
       call("InterlockedPushEntrySList", 2, (uint32_t[]){ header_va, e[0] }) == 0u);
    ok("head is entry 0", MEM32(header_va + SLIST_NEXT_OFF) == e[0]);
    ok("entry 0 has no Next", MEM32(e[0] + SLIST_NEXT_OFF) == 0u);
    ok("depth=1, sequence=1", MEM32(header_va + SLIST_COUNT_OFF) == 0x00010001u);

    ok("second push returns old head",
       call("InterlockedPushEntrySList", 2, (uint32_t[]){ header_va, e[1] }) == e[0]);
    ok("head is entry 1", MEM32(header_va + SLIST_NEXT_OFF) == e[1]);
    ok("entry 1 links to entry 0", MEM32(e[1] + SLIST_NEXT_OFF) == e[0]);
    ok("depth=2, sequence=2", MEM32(header_va + SLIST_COUNT_OFF) == 0x00020002u);

    ok("third push returns old head",
       call("InterlockedPushEntrySList", 2, (uint32_t[]){ header_va, e[2] }) == e[1]);
    ok("head is entry 2", MEM32(header_va + SLIST_NEXT_OFF) == e[2]);
    ok("entry 2 links to entry 1", MEM32(e[2] + SLIST_NEXT_OFF) == e[1]);
    ok("depth=3, sequence=3", MEM32(header_va + SLIST_COUNT_OFF) == 0x00030003u);
    ok("push stayed inside 8-byte header + 4-byte entries",
       header_canaries_ok() && MEM32(e[0] + SLIST_ENTRY_SZ) == 0u &&
       MEM32(e[1] + SLIST_ENTRY_SZ) == 0u && MEM32(e[2] + SLIST_ENTRY_SZ) == 0u);

    /* --- InterlockedFlushSList: detach the chain in one step -------------- */
    ok("flush returns the whole list's head",
       call("InterlockedFlushSList", 1, (uint32_t[]){ header_va }) == e[2]);
    ok("flush empties head", MEM32(header_va + SLIST_NEXT_OFF) == 0u);
    ok("flush clears depth, keeps sequence",
       MEM32(header_va + SLIST_COUNT_OFF) == 0x00030000u);
    ok("flushed chain still links 2->1->0->NULL",
       MEM32(e[2]) == e[1] && MEM32(e[1]) == e[0] && MEM32(e[0]) == 0u);
    ok("flush stayed inside header", header_canaries_ok());

    /* Flushing an empty list is a no-op that returns NULL. */
    MEM32(header_va + SLIST_COUNT_OFF) = 0x000A0000u;     /* depth 0, sequence 10 */
    ok("flush of empty list returns NULL",
       call("InterlockedFlushSList", 1, (uint32_t[]){ header_va }) == 0u);
    ok("empty flush leaves header untouched",
       MEM32(header_va + SLIST_NEXT_OFF) == 0u &&
       MEM32(header_va + SLIST_COUNT_OFF) == 0x000A0000u && header_canaries_ok());

    /* A push after the flush reuses the header, advancing the sequence. */
    ok("push after flush returns NULL",
       call("InterlockedPushEntrySList", 2, (uint32_t[]){ header_va, e[0] }) == 0u);
    ok("push after flush depth=1 sequence=11",
       MEM32(header_va + SLIST_COUNT_OFF) == 0x000B0001u && header_canaries_ok());

    /* Null arguments fail explicitly rather than crashing the layer. */
    hle_set_last_error(0);
    ok("push with NULL head fails",
       call("InterlockedPushEntrySList", 2, (uint32_t[]){ 0u, e[0] }) == 0u &&
       hle_get_last_error() == BAD_PARAM);
    hle_set_last_error(0);
    ok("push with NULL entry fails",
       call("InterlockedPushEntrySList", 2, (uint32_t[]){ header_va, 0u }) == 0u &&
       hle_get_last_error() == BAD_PARAM);
    hle_set_last_error(0);
    ok("flush with NULL head fails",
       call("InterlockedFlushSList", 1, (uint32_t[]){ 0u }) == 0u &&
       hle_get_last_error() == BAD_PARAM);

    /* --- RtlPcToFileHeader: honest loader base only ----------------------- */
    uint32_t base = hle_va(hle_alloc(0x2000u));
    ok("module image allocated", base != 0);
    if (!base) return 1;
    memset(hle_ptr(base), 0, 0x2000u);
    MEM16(base) = 0x5A4Du;                       /* MZ */
    MEM32(base + 0x3Cu) = 0x80u;                 /* e_lfanew */
    uint32_t nt = base + 0x80u;
    MEM32(nt) = 0x00004550u;                     /* PE\0\0 */
    MEM16(nt + 24u) = 0x010Bu;                   /* PE32 */
    MEM32(nt + 24u + 0x38u) = 0x2000u;           /* SizeOfImage */
    hle_module_add("slist-test.exe", base, 0, 0, 1);

    uint32_t out = hle_va(hle_alloc(8u));
    uint32_t pc = base + 0x1234u;
    MEM32(out) = 0xDEADBEEFu;
    ok("mapped PC returns the module base",
       call("RtlPcToFileHeader", 2, (uint32_t[]){ pc, out }) == base);
    ok("BaseOfImage out parameter is the module base", MEM32(out) == base);

    MEM32(out) = 0xDEADBEEFu;
    ok("unmapped PC returns NULL",
       call("RtlPcToFileHeader", 2, (uint32_t[]){ 0x70000000u, out }) == 0u);
    ok("unmapped PC writes a zero BaseOfImage", MEM32(out) == 0u);
    ok("NULL out pointer tolerated",
       call("RtlPcToFileHeader", 2, (uint32_t[]){ pc, 0u }) == base);

    /* --- RtlUnwindEx: explicitly unsupported, no fake unwind -------------- */
    hle_set_last_error(0);
    uint32_t esp_before = g_esp;
    uint32_t unw = call("RtlUnwindEx", 6, (uint32_t[]){
        0u, 0u, 0u, 0u, header_va, 0u
    });
    ok("RtlUnwindEx returns void (eax 0)", unw == 0u);
    ok("RtlUnwindEx reports ERROR_CALL_NOT_IMPLEMENTED",
       hle_get_last_error() == NOT_IMPL);
    ok("RtlUnwindEx did not disturb the guest stack", g_esp == esp_before);
    ok("RtlUnwindEx did not touch the header", header_canaries_ok());

    /* --- stdcall arity: each callee pops ret + exactly its own arguments --- */
    uint32_t sp = g_esp;
    scall("InterlockedPushEntrySList", 2, (uint32_t[]){ header_va, e[0] });
    ok("InterlockedPushEntrySList stdcall arity", g_esp == sp);
    sp = g_esp;
    scall("InterlockedFlushSList", 1, (uint32_t[]){ header_va });
    ok("InterlockedFlushSList stdcall arity", g_esp == sp);
    sp = g_esp;
    scall("RtlPcToFileHeader", 2, (uint32_t[]){ pc, out });
    ok("RtlPcToFileHeader stdcall arity", g_esp == sp);
    sp = g_esp;
    scall("RtlUnwindEx", 6, (uint32_t[]){ 0u, 0u, 0u, 0u, header_va, 0u });
    ok("RtlUnwindEx stdcall arity", g_esp == sp);

    mach_leave();
    printf("slist_selftest: all checks passed\n");
    return fails != 0;
}
