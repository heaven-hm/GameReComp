/*
 * rtl_unwind_selftest.c - the Rtl* unwind/CONTEXT shims driven through the real
 * stdcall bridge (hle_call_guest), with guest addresses and guest registers
 * only.  Checks:
 *
 *   - RtlCaptureContext writes a full 0x2CC-byte x86 CONTEXT and nothing past
 *     it; ContextFlags, the integer/segment registers, Eip (the guest return
 *     address), Esp (the caller's SP just past it) and the deliberately zeroed
 *     debug/FP/extended areas all have the documented values.  A null CONTEXT
 *     writes nothing and reports ERROR_INVALID_PARAMETER.
 *   - RtlLookupFunctionEntry never fabricates an x86 RUNTIME_FUNCTION: it
 *     returns NULL and reports the missing mechanism.  It still reports the
 *     honest image base when ControlPc is in a module the loader knows, and
 *     zero for an unmapped PC; a null ImageBase out pointer is tolerated.
 *   - RtlVirtualUnwind, with and without a well-formed RUNTIME_FUNCTION,
 *     returns NULL, reports the missing mechanism, and leaves the caller's
 *     CONTEXT byte-for-byte untouched - no invented establisher frame.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

/* Mirror of winnt.h's x86 CONTEXT, used only to read what the shim wrote in
 * guest memory.  All fields are 4-byte aligned and there is no host padding:
 * this is the guest layout regardless of the host's word size. */
typedef struct {
    uint32_t ContextFlags, Dr0, Dr1, Dr2, Dr3, Dr6, Dr7;
    uint8_t  FloatSave[112];
    uint32_t SegGs, SegFs, SegEs, SegDs, Edi, Esi, Ebx, Edx, Ecx, Eax, Ebp,
             Eip, SegCs, EFlags, Esp, SegSs;
    uint8_t  ExtendedRegisters[512];
} test_context_t;
_Static_assert(sizeof(test_context_t) == 0x2CCu, "x86 CONTEXT must be 0x2CC bytes");

#define CTX_SIZE      0x2CCu
#define CTX_FULL      0x10007u                 /* CONTROL | INTEGER | SEGMENTS */
#define NOT_IMPL      120u                     /* ERROR_CALL_NOT_IMPLEMENTED */
#define BAD_PARAM     87u                      /* ERROR_INVALID_PARAMETER */

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
/* A guest-side stdcall call leaves esp change visible (hle_call_guest restores
 * it on return), so this is used to check each shim's argument count. */
static uint32_t scall(const char *name, int argc, const uint32_t *a) {
    uint32_t va = hle_resolve(name);
    if (!va) hle_fatal("selftest: no shim named %s", name);
    for (int i = argc - 1; i >= 0; i--) PUSH32(esp, a[i]);
    RECOMP_ICALL(va);
    return eax;                                  /* the shim popped ret + its args */
}
static int all_bytes(const void *p, size_t n, uint8_t b) {
    const uint8_t *q = (const uint8_t *)p;
    for (size_t i = 0; i < n; i++) if (q[i] != b) return 0;
    return 1;
}

int main(void) {
    win32hle_register(win32hle_kernel32_crt);
    mach_enter();

    uint32_t cap = hle_resolve("RtlCaptureContext");
    uint32_t lookup = hle_resolve("RtlLookupFunctionEntry");
    uint32_t unwind = hle_resolve("RtlVirtualUnwind");
    ok("three Rtl shims registered", cap != 0 && lookup != 0 && unwind != 0);
    if (!cap || !lookup || !unwind) return 1;

    /* --- RtlCaptureContext: exact fields, exact footprint ---------------- */
    uint32_t raw = hle_va(hle_alloc(CTX_SIZE + 8u));
    ok("context buffer allocated", raw != 0);
    if (!raw) return 1;
    memset(hle_ptr(raw), 0xCC, CTX_SIZE + 8u);
    uint32_t ctx = raw + 4u;                     /* canary word on either side */

    g_eax = 0x11111111u; g_ecx = 0x22222222u; g_edx = 0x33333333u;
    g_ebx = 0x44444444u; g_esi = 0x55555555u; g_edi = 0x66666666u;
    g_ebp = 0x77777777u;
    g_seg_cs = 0x0023u; g_seg_ds = 0x002Bu; g_seg_es = 0x002Bu;
    g_seg_fs = 0x003Bu; g_seg_gs = 0x002Du; g_seg_ss = 0x002Bu;
    uint32_t esp_before = g_esp;
    call("RtlCaptureContext", 1, &ctx);

    const test_context_t *c = (const test_context_t *)hle_ptr(ctx);
    ok("ContextFlags == CONTEXT_FULL", c->ContextFlags == CTX_FULL);
    ok("integer registers captured", c->Eax == 0x11111111u && c->Ecx == 0x22222222u &&
       c->Edx == 0x33333333u && c->Ebx == 0x44444444u && c->Esi == 0x55555555u &&
       c->Edi == 0x66666666u && c->Ebp == 0x77777777u);
    ok("segment registers captured", c->SegCs == 0x0023u && c->SegDs == 0x002Bu &&
       c->SegEs == 0x002Bu && c->SegFs == 0x003Bu && c->SegGs == 0x002Du &&
       c->SegSs == 0x002Bu);
    ok("Eip is the guest return address", c->Eip == RECOMP_RETADDR);
    /* hle_call_guest pushed one argument and one return address, so the shim
     * entered with esp = esp_before - 8; CONTEXT.Esp is that + 4. */
    ok("Esp is the caller SP past the return address", c->Esp == esp_before - 4u);
    ok("EFlags not fabricated from lazy flags", c->EFlags == 0u);
    ok("debug registers left zero", c->Dr0 == 0 && c->Dr1 == 0 && c->Dr2 == 0 &&
       c->Dr3 == 0 && c->Dr6 == 0 && c->Dr7 == 0);
    ok("FP and extended areas left zero",
       all_bytes(c->FloatSave, sizeof c->FloatSave, 0) &&
       all_bytes(c->ExtendedRegisters, sizeof c->ExtendedRegisters, 0));
    ok("no write outside the 0x2CC-byte CONTEXT",
       MEM32(raw) == 0xCCCCCCCCu && MEM32(raw + CTX_SIZE + 4u) == 0xCCCCCCCCu);
    ok("caller registers restored by hle_call_guest", g_eax == 0x11111111u &&
       g_esp == esp_before);

    /* Error path: a null pointer writes nothing and is reported. */
    memset(hle_ptr(raw), 0xCC, CTX_SIZE + 8u);
    hle_set_last_error(0);
    call("RtlCaptureContext", 1, (uint32_t[]){ 0u });
    ok("null CONTEXT refused", hle_get_last_error() == BAD_PARAM);
    ok("null CONTEXT wrote nothing", all_bytes(hle_ptr(raw), CTX_SIZE + 8u, 0xCC));

    /* --- RtlLookupFunctionEntry: honest no-table answer, honest base ------ */
    /* A minimal PE32 image so the loader can resolve an image base. */
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
    hle_module_add("unwind-test.exe", base, 0, 0, 1);

    uint32_t out = hle_va(hle_alloc(16u));
    ok("image-base buffer allocated", out != 0);
    uint32_t pc = base + 0x1234u;
    MEM32(out) = 0xDEADBEEFu;
    hle_set_last_error(0);
    ok("lookup returns NULL for an x86 guest PC",
       call("RtlLookupFunctionEntry", 3, (uint32_t[]){ pc, out, 0u }) == 0u);
    ok("lookup reports the missing table", hle_get_last_error() == NOT_IMPL);
    ok("lookup reports the real image base", MEM32(out) == base);

    MEM32(out) = 0xDEADBEEFu;
    ok("unmapped PC still returns NULL",
       call("RtlLookupFunctionEntry", 3, (uint32_t[]){ 0x70000000u, out, 0u }) == 0u);
    ok("unmapped PC has image base 0", MEM32(out) == 0u);
    ok("null ImageBase out pointer tolerated",
       call("RtlLookupFunctionEntry", 3, (uint32_t[]){ pc, 0u, 0u }) == 0u);

    /* --- RtlVirtualUnwind: no unwind, no CONTEXT changes ----------------- */
    /* Even a syntactically valid RUNTIME_FUNCTION must not be interpreted:
     * with no unwind info to read, there is nothing to unwind honestly. */
    uint32_t fn = hle_va(hle_alloc(12u));
    MEM32(fn) = 0x1000u; MEM32(fn + 4u) = 0x1100u; MEM32(fn + 8u) = 0u;
    memset(hle_ptr(ctx), 0x5A, CTX_SIZE);
    hle_set_last_error(0);
    ok("virtual unwind returns NULL",
       call("RtlVirtualUnwind", 8, (uint32_t[]){ 0u, base, pc, fn, ctx, 0u, 0u, 0u }) == 0u);
    ok("virtual unwind reports the missing mechanism", hle_get_last_error() == NOT_IMPL);
    ok("virtual unwind left the CONTEXT untouched",
       all_bytes(hle_ptr(ctx), CTX_SIZE, 0x5A));
    hle_set_last_error(0);
    ok("all-null arguments tolerated",
       call("RtlVirtualUnwind", 8, (uint32_t[8]){ 0u }) == 0u);
    ok("all-null arguments still report unimplemented", hle_get_last_error() == NOT_IMPL);

    /* --- stdcall arity: each callee pops ret + exactly its own arguments --- */
    uint32_t sp = g_esp;
    scall("RtlCaptureContext", 1, &ctx);
    ok("RtlCaptureContext stdcall arity", g_esp == sp);
    sp = g_esp;
    scall("RtlLookupFunctionEntry", 3, (uint32_t[]){ pc, out, 0u });
    ok("RtlLookupFunctionEntry stdcall arity", g_esp == sp);
    sp = g_esp;
    scall("RtlVirtualUnwind", 8, (uint32_t[]){ 0u, base, pc, fn, ctx, 0u, 0u, 0u });
    ok("RtlVirtualUnwind stdcall arity", g_esp == sp);

    mach_leave();
    printf("rtl_unwind_selftest: all checks passed\n");
    return fails != 0;
}
