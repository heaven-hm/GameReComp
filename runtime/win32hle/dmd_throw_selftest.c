/*
 * dmd_throw_selftest.c - deterministic guest DMD (0xE0440001) throw dispatch.
 *
 * Uses the same global-register "synthetic guest" style as win32hle_selftest.c:
 * a lifted function with a RECOMP_EH_ENTER checkpoint installs a Digital Mars
 * SEH record, builds an exception object with a ClassInfo base chain, and calls
 * hle_eh_d_throw.  Two dispatch layouts and the explicit failure path are
 * covered:
 *
 *   1. the catch belongs to the frame that installed the record - the common
 *      case, resumed through the exact RECOMP_EH_ENTER checkpoint;
 *   2. the record belongs to a guest frame with no checkpoint of its own (the
 *      gr.exe tail-call-inlined case: 0x0040DB9C -> 0x0040DBC0), resumed
 *      through the innermost live checkpoint inside that guest frame;
 *   3. no live checkpoint at all - the reason string must name the unsupported
 *      structure instead of reporting a missing catch.
 *
 * Both dispatch layouts must reach a lifted catch with the guest EBP/ESP the
 * DMD frame handler would have installed.  No game binary, display or injected
 * DLL is involved.
 */
#define RECOMP_GENERATED_CODE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

#define VA_THROW   0x00405100u  /* guest function with an EH checkpoint */
#define VA_CATCH   0x00405200u  /* standalone catch funclet (RECOMP_ITAIL) */
#define VA_INLINE  0x00405300u  /* inline catch label inside VA_THROW */
#define ESP_OFFSET 0x30u        /* DHandlerTable.espoffset */
#define BPOFF      0xFFFFFFF0u  /* catch variable at [ebp-0x10] */

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

#define GUEST_PROLOGUE(va)              \
    uint32_t _cf = 0;                   \
    uint32_t _flag_k = FK_NONE;         \
    uint32_t _flag_a = 0, _flag_b = 0;  \
    RECOMP_ENTER(va)

static uint32_t g_stub_va, g_tbl_va, g_exc_obj, g_args_va;
static int g_catch_calls, g_inline_catch;
static uint32_t g_seen_ebp, g_seen_esp, g_expect_ebp, g_expect_esp;
static const char *g_reason;
static int g_dispatched;
static int g_use_inline_catch;  /* 1: exact frame, 0: tail-call-inlined frame */

/* The catch continuation the generated landing pad enters via RECOMP_ITAIL.
 * It reads its locals through the global EBP, exactly like a lifted DMD catch
 * block does; EBP/ESP must be those of the catch-owning guest frame. */
static void dmd_catch_funclet(void) {
    GUEST_PROLOGUE(VA_CATCH);
    g_catch_calls++;
    g_seen_ebp = ebp;
    g_seen_esp = esp;
    ok("catch funclet sees the ClassInfo-bound object",
       MEM32(g_expect_ebp + (uint32_t)(int32_t)BPOFF) == g_exc_obj);
}

/* Synthetic lifted function with a landing pad.  It installs a DMD SEH record
 * that names a catch, then throws.  For layout 2 the record's EBP is moved
 * below this frame, which is what the lifter produces when the catch-owning
 * body was inlined into a tail-call stub with no RECOMP_EH_ENTER. */
static void guest_dmd_throw(void) {
    GUEST_PROLOGUE(VA_THROW);
    uint32_t _itail_tgt = 0;
    uint32_t saved_fs = MEM32(FS_BASE);
    RECOMP_EH_ENTER();
    PUSH32(esp, ebp);
    ebp = esp;

    /* DMD SEH record: [0]=prev, [4]=handler stub, [8]=active scope index,
     * [12]=EBP of the guarded function. */
    PUSH32(esp, 0u);                /* idx = scope 0 */
    PUSH32(esp, g_stub_va);
    PUSH32(esp, saved_fs);
    uint32_t record = esp;
    uint32_t owner_ebp = ebp;
    if (!g_use_inline_catch) {
        owner_ebp = ebp - 0x40u;    /* the inlined frame's EBP is lower */
        record = owner_ebp - 12u;
        MEM32(record) = saved_fs;
        MEM32(record + 4u) = g_stub_va;
        MEM32(record + 8u) = 0u;
    }
    MEM32(FS_BASE) = record;
    g_expect_ebp = owner_ebp;
    g_expect_esp = owner_ebp - ESP_OFFSET;

    MEM32(g_args_va) = g_exc_obj;    /* RaiseException args[0] = exception object */
    g_reason = "no call";
    g_dispatched = hle_eh_d_throw(0xE0440001u, 1u, g_args_va, &g_reason);
    ok("hle_eh_d_throw must not return on a caught throw", 0);
    MEM32(FS_BASE) = saved_fs;
    RECOMP_EH_LEAVE();
    return;

  _ljump:
    switch (_itail_tgt) {
      case VA_INLINE: goto L_CATCH_INLINE;
      default: RECOMP_EH_LEAVE(); RECOMP_ITAIL(_itail_tgt); return;
    }
  L_CATCH_INLINE:
    g_inline_catch++;
    g_seen_ebp = ebp;
    g_seen_esp = esp;
    ok("inline catch sees the ClassInfo-bound object",
       MEM32(g_expect_ebp + (uint32_t)(int32_t)BPOFF) == g_exc_obj);
    MEM32(FS_BASE) = saved_fs;
    RECOMP_EH_LEAVE();
}

/* Dispatch table the generated lookups use (also proves the catch VA is lifted
 * when the fallback path tail-calls it). */
const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { VA_THROW, guest_dmd_throw },
    { VA_CATCH, dmd_catch_funclet },
};
const uint32_t recomp_dispatch_count = 2;

/* Build the DMD metadata in guest memory: exception object -> vtbl -> ClassInfo
 * whose base is the catch type, a `mov eax,tbl; jmp` handler stub, and a table
 * with one DHandlerInfo entry pointing at one DCatchBlock. */
static void build_dmd_metadata(uint32_t catch_va) {
    uint32_t base_ci = hle_va(hle_alloc(0x40));
    uint32_t obj_ci  = hle_va(hle_alloc(0x40));
    uint32_t vtbl    = hle_va(hle_alloc(8));
    uint8_t *stub    = (uint8_t *)hle_alloc(16);

    g_tbl_va   = hle_va(hle_alloc(64));
    g_exc_obj  = hle_va(hle_alloc(16));
    g_args_va  = hle_va(hle_alloc(8));
    g_stub_va  = hle_va(stub);

    MEM32(obj_ci + 0x28u) = base_ci;         /* ClassInfo.base */
    MEM32(vtbl) = obj_ci;                    /* vtbl[0] = ClassInfo */
    MEM32(g_exc_obj) = vtbl;                 /* obj->vptr = vtbl */
    MEM32(g_args_va) = g_exc_obj;            /* args[0] = obj */

    stub[0] = 0xB8u;
    memcpy(stub + 1, &g_tbl_va, 4);          /* mov eax, DHandlerTable */
    stub[5] = 0xE9u;
    memset(stub + 6, 0, 4);                  /* jmp +0 (never executed) */

    MEM32(g_tbl_va + 0u) = VA_THROW;         /* fptr: start of guarded function */
    MEM32(g_tbl_va + 4u) = ESP_OFFSET;       /* espoffset */
    MEM32(g_tbl_va + 8u) = 0x50u;            /* retoffset */
    /* DHandlerInfo[0]: prev = -1, no finally, DCatchInfo at +24 */
    MEM32(g_tbl_va + 12u) = 0xFFFFFFFFu;
    MEM32(g_tbl_va + 16u) = 24u;
    MEM32(g_tbl_va + 20u) = 0u;
    /* DCatchInfo: 1 catch; DCatchBlock = { ClassInfo*, bpoff, catch_code } */
    MEM32(g_tbl_va + 24u) = 1u;
    MEM32(g_tbl_va + 28u) = base_ci;
    MEM32(g_tbl_va + 32u) = BPOFF;
    MEM32(g_tbl_va + 36u) = catch_va;
}

int main(void) {
    mach_enter();   /* guest stack + simulated TIB, like the other selftests */

    /* Case 1: the catch belongs to the live frame (exact checkpoint). */
    build_dmd_metadata(VA_INLINE);
    g_use_inline_catch = 1;
    g_inline_catch = 0;
    g_catch_calls = 0;
    guest_dmd_throw();
    ok("exact frame: resumed at the inline catch", g_inline_catch == 1);
    /* A successful dispatch longjmps out of hle_eh_d_throw, so g_dispatched is
     * never assigned; the reason string is written just before the jump. */
    ok("exact frame: dispatch reported",
       g_reason && strstr(g_reason, "dispatched to guest catch") != NULL);
    ok("exact frame: guest EBP/ESP restored to the catch frame",
       g_seen_ebp == g_expect_ebp && g_seen_esp == g_expect_esp);

    /* Case 2: catch frame was tail-call-inlined (no RECOMP_EH_ENTER).  The
     * record's EBP is below the live checkpoint, so dispatch must use it. */
    build_dmd_metadata(VA_CATCH);
    g_use_inline_catch = 0;
    g_inline_catch = 0;
    g_catch_calls = 0;
    guest_dmd_throw();
    ok("inlined frame: resumed at the lifted catch funclet", g_catch_calls == 1);
    ok("inlined frame: dispatch reported the inner-checkpoint path",
       g_reason && strstr(g_reason, "inner checkpoint") != NULL);
    ok("inlined frame: guest EBP/ESP restored to the catch frame",
       g_seen_ebp == g_expect_ebp && g_seen_esp == g_expect_esp);

    /* Case 3: no live checkpoint at all - the failure must be explicit and
     * name the exact unsupported structure, never a silent "no catch". */
    {
        uint32_t saved_fs = MEM32(FS_BASE);
        uint32_t record = esp - 64u;   /* still inside the guest stack */
        MEM32(record) = saved_fs;
        MEM32(record + 4u) = g_stub_va;
        MEM32(record + 8u) = 0u;
        MEM32(record + 12u) = record + 12u;
        MEM32(FS_BASE) = record;
        g_reason = NULL;
        g_dispatched = hle_eh_d_throw(0xE0440001u, 1u, g_args_va, &g_reason);
        ok("no checkpoint: explicit failure with a precise reason",
           g_dispatched == 0 && g_reason &&
           strstr(g_reason, "no EH checkpoint") != NULL);
        MEM32(FS_BASE) = saved_fs;
    }

    mach_leave();

    if (fails == 0) printf("dmd_throw_selftest: all checks passed\n");
    return fails != 0;
}
