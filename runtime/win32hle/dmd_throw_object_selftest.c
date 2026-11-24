/*
 * dmd_throw_object_selftest.c - issue #27: refuse a DMD throw whose object
 * would resume guest code through a NULL vtable.
 *
 * gr.exe's own DMD frame handler reads the thrown object's vptr and ClassInfo
 * before it can match a catch (0x41E048: mov ecx,[eax+0x14]; mov edx,[ecx];
 * mov ebx,[edx]).  hle_eh_d_throw used to read the same chain without
 * validating it: a recompiled guest whose class vtable was never constructed
 * then got its malformed object bound into the catch local, and the catch's
 * virtual call faulted with the issue #27 signature -- "ICALL: null target"
 * from the catch frame followed by SIGBUS in its caller.
 *
 * This fixture builds the real DMD metadata in guest memory (object -> vptr ->
 * ClassInfo -> base ClassInfo, a `mov eax,tbl; jmp` handler stub, a
 * DHandlerTable, one DHandlerInfo and one DCatchBlock) and drives the same
 * guest-ABI entry point with a lifted RECOMP_EH_ENTER checkpoint.  It checks:
 *
 *   1. a well-formed throw still dispatches to the guest catch (no regression);
 *   2. a NULL object vptr is refused with a precise reason, no guest write;
 *   3. a vptr with NULL ClassInfo is refused with a precise reason;
 *   4. an unreadable ClassInfo pointer is refused;
 *   5. a cyclic ClassInfo base chain is refused;
 *   6. a DHandlerTable espoffset that puts the catch ESP outside the guest
 *      stack is refused before the registration chain or registers change.
 *
 * No game binary, display or injected DLL is involved.
 */
#define RECOMP_GENERATED_CODE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

#define VA_THROW    0x00405100u   /* lifted guest frame with a checkpiont */
#define VA_INLINE   0x00405300u   /* inline catch label in that frame */
#define ESP_OFFSET  0x30u         /* valid DHandlerTable.espoffset */
#define BPOFF       0xFFFFFFF0u   /* catch variable at [ebp-0x10] */
#define SENTINEL    0x5EEDFACEu   /* initial value of the catch local */

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
static uint32_t g_vtbl, g_obj_ci, g_base_ci;
static uint32_t g_catch_local, g_catch_value, g_seen_ebp, g_seen_esp;
static int g_catch_calls, g_returned, g_dispatched;
static const char *g_reason;

/* Snapshot taken inside the guest frame, just before the throw, so a refusal
 * can be checked for not having touched guest state. */
static uint32_t g_fs_before, g_idx_before, g_ebp_before, g_esp_before, g_saved_fs;
static void snapshot_guest(void) {
    g_fs_before = MEM32(FS_BASE);
    g_idx_before = MEM32(g_fs_before + 8u);
    g_ebp_before = g_ebp;
    g_esp_before = g_esp;
}

/* Synthetic lifted function with a DMD SEH record and a landing pad.  A
 * successful dispatch longjmps into L_CATCH; a refusal returns through the
 * `g_returned` path with guest state we can inspect. */
static void guest_attempt(void) {
    GUEST_PROLOGUE(VA_THROW);
    uint32_t _itail_tgt = 0;
    uint32_t saved_fs = MEM32(FS_BASE);
    g_saved_fs = saved_fs;
    RECOMP_EH_ENTER();
    PUSH32(esp, ebp);
    ebp = esp;

    /* DMD SEH record: [0]=prev, [4]=handler stub, [8]=active scope index,
     * [12]=EBP of the guarded function.  record+12 == ebp here. */
    PUSH32(esp, 0u);
    PUSH32(esp, g_stub_va);
    PUSH32(esp, saved_fs);
    uint32_t record = esp;
    MEM32(FS_BASE) = record;

    g_catch_local = ebp + (uint32_t)(int32_t)BPOFF;
    MEM32(g_catch_local) = SENTINEL;
    g_reason = "no call";
    g_catch_calls = 0;
    g_returned = 0;
    snapshot_guest();
    g_dispatched = hle_eh_d_throw(0xE0440001u, 1u, g_args_va, &g_reason);
    /* Only reached when dispatch refused the throw. */
    g_returned = 1;
    MEM32(FS_BASE) = saved_fs;
    RECOMP_EH_LEAVE();
    return;

  _ljump:
    switch (_itail_tgt) {
      case VA_INLINE: goto L_CATCH;
      default: MEM32(FS_BASE) = saved_fs; RECOMP_EH_LEAVE(); return;
    }
  L_CATCH:
    g_catch_calls++;
    g_seen_ebp = ebp;
    g_seen_esp = esp;
    g_catch_value = MEM32(g_catch_local);
    MEM32(FS_BASE) = saved_fs;
    RECOMP_EH_LEAVE();
}

const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { VA_THROW, guest_attempt },
};
const uint32_t recomp_dispatch_count = 1;

/* Build the DMD metadata in guest memory.  ClassInfo.base lives at +0x28 in
 * DMD 1.x (verified against gr.exe: Exception's ClassInfo at 0x004B2168 has
 * base 0x004B19E8 = Object's ClassInfo at that offset). */
static void build_metadata(uint32_t catch_va, uint32_t esp_offset) {
    g_base_ci = hle_va(hle_alloc(0x40));
    g_obj_ci  = hle_va(hle_alloc(0x40));
    g_vtbl    = hle_va(hle_alloc(8));
    uint8_t *stub = (uint8_t *)hle_alloc(16);

    g_tbl_va  = hle_va(hle_alloc(64));
    g_exc_obj = hle_va(hle_alloc(16));
    g_args_va = hle_va(hle_alloc(8));
    g_stub_va = hle_va(stub);

    memset(hle_ptr(g_base_ci), 0, 0x40);
    memset(hle_ptr(g_obj_ci), 0, 0x40);
    MEM32(g_obj_ci + 0x28u) = g_base_ci;     /* ClassInfo.base */
    MEM32(g_vtbl) = g_obj_ci;                /* vtbl[0] = ClassInfo */
    MEM32(g_exc_obj) = g_vtbl;               /* obj->vptr = vtbl */
    MEM32(g_args_va) = g_exc_obj;            /* args[0] = thrown object */

    stub[0] = 0xB8u;
    memcpy(stub + 1, &g_tbl_va, 4);          /* mov eax, DHandlerTable */
    stub[5] = 0xE9u;
    memset(stub + 6, 0, 4);                  /* jmp +0 (never executed) */

    MEM32(g_tbl_va + 0u) = VA_THROW;         /* fptr */
    MEM32(g_tbl_va + 4u) = esp_offset;       /* espoffset */
    MEM32(g_tbl_va + 8u) = 0x50u;            /* retoffset */
    MEM32(g_tbl_va + 12u) = 0xFFFFFFFFu;     /* DHandlerInfo[0].prev = -1 */
    MEM32(g_tbl_va + 16u) = 24u;             /* cioffset */
    MEM32(g_tbl_va + 20u) = 0u;              /* finally_code */
    MEM32(g_tbl_va + 24u) = 1u;              /* ncatches */
    MEM32(g_tbl_va + 28u) = g_base_ci;       /* catch type */
    MEM32(g_tbl_va + 32u) = BPOFF;           /* catch local offset */
    MEM32(g_tbl_va + 36u) = catch_va;        /* catch continuation */
}

static int has(const char *needle) {
    return g_reason && strstr(g_reason, needle) != NULL;
}

/* A refusal must leave the guest registration chain, the catch local and the
 * guest register file exactly as they were before the throw. */
static void check_untouched(const char *label) {
    ok(label, MEM32(FS_BASE) == g_saved_fs &&
              MEM32(g_fs_before + 8u) == g_idx_before &&
              g_ebp == g_ebp_before && g_esp == g_esp_before &&
              MEM32(g_catch_local) == SENTINEL &&
              g_catch_calls == 0 && g_returned == 1 && g_dispatched == 0);
}

int main(void) {
    mach_enter();   /* guest stack + simulated TIB, like the other selftests */

    /* Case 1: a well-formed throw still dispatches into the guest catch. */
    build_metadata(VA_INLINE, ESP_OFFSET);
    guest_attempt();
    ok("valid throw: reached the inline catch", g_catch_calls == 1);
    ok("valid throw: dispatch reported", has("dispatched to guest catch"));
    ok("valid throw: catch local received the exception object",
       g_catch_value == g_exc_obj);
    ok("valid throw: guest EBP/ESP are the catch frame's",
       g_seen_ebp + (uint32_t)(int32_t)BPOFF == g_catch_local &&
       g_seen_esp == g_seen_ebp - ESP_OFFSET);

    /* Case 2: the object's vptr is NULL -- the unconstructed-object shape that
     * produced issue #27.  No guest write, no jump, explicit reason. */
    build_metadata(VA_INLINE, ESP_OFFSET);
    MEM32(g_exc_obj) = 0u;
    guest_attempt();
    ok("NULL object vptr: explicit reason", has("NULL vtable"));
    check_untouched("NULL object vptr: no guest state changed");

    /* Case 3: vptr present but ClassInfo missing. */
    build_metadata(VA_INLINE, ESP_OFFSET);
    MEM32(g_vtbl) = 0u;
    guest_attempt();
    ok("NULL ClassInfo: explicit reason", has("NULL ClassInfo"));
    check_untouched("NULL ClassInfo: no guest state changed");

    /* Case 4: ClassInfo pointer is not a readable guest address. */
    build_metadata(VA_INLINE, ESP_OFFSET);
    MEM32(g_vtbl) = 0x100u;
    guest_attempt();
    ok("unreadable ClassInfo: explicit reason", has("not readable guest memory"));
    check_untouched("unreadable ClassInfo: no guest state changed");

    /* Case 5: self-referential base chain. */
    build_metadata(VA_INLINE, ESP_OFFSET);
    MEM32(g_obj_ci + 0x28u) = g_obj_ci;
    guest_attempt();
    ok("cyclic ClassInfo: explicit reason", has("cyclic base chain"));
    check_untouched("cyclic ClassInfo: no guest state changed");

    /* Case 6: valid object and catch, but the catch ESP falls outside the
     * guest stack.  Narrow the TIB's stack window around the SEH record the
     * guest is about to install: the record itself stays plausible, while
     * EBP-espoffset lands below StackLimit.  The refusal must happen before
     * the registration chain, registers or catch local change. */
    build_metadata(VA_INLINE, ESP_OFFSET);
    {
        uint32_t tib = FS_BASE;
        uint32_t old_lo = MEM32(tib + 8u), old_hi = MEM32(tib + 4u);
        uint32_t record = g_esp - 16u;   /* guest_attempt pushes ebp+3 words */
        MEM32(tib + 8u) = record;        /* StackLimit */
        MEM32(tib + 4u) = record + 16u;  /* StackBase */
        guest_attempt();
        MEM32(tib + 8u) = old_lo;
        MEM32(tib + 4u) = old_hi;
    }
    ok("bad espoffset: explicit reason", has("outside the guest stack"));
    check_untouched("bad espoffset: no guest state changed");

    mach_leave();

    if (fails == 0) printf("dmd_throw_object_selftest: all checks passed\n");
    return fails != 0;
}
