/*
 * eh_validation_selftest.c - deterministic guest-ABI evidence for the two EH
 * chain validations that were missing from eh32.c:
 *
 *   1. FS:[0] registration chain cycles.  Both dispatchers walked the
 *      guest-controlled chain without a bound in their inner unwind loops and
 *      stopped their outer walk at EH_MAX_CHAIN.  A cyclic chain therefore
 *      either spun or was mislabelled "no matching catch handler".  A
 *      self-referential record could even dispatch a catch off a malformed
 *      chain.  The shared fs_chain_to() check now names the defect.
 *
 *   2. An out-of-range C++ catch ESP.  dispatch_cxx() resumed the catch with
 *      the ESP saved at [ebp-16] but never checked it (the DMD path checks its
 *      DHandlerTable espoffset).  A bogus value resumed guest code with a
 *      corrupt stack pointer and made the catch read locals outside the guest
 *      stack -- the issue #27 failure signature.  The hand-off is now validated
 *      before any guest write or unwind, with an explicit diagnostic.
 *
 * Every refusal is checked for before/after guest-state evidence: the FS
 * chain, the registration's state word, the guest register file and the catch
 * local are byte-for-byte unchanged, and no guest catch runs.  A well-formed
 * DMD chain and a valid C++ catch ESP still dispatch (no regression).  No game
 * binary, display or injected DLL is involved.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "win32hle.h"

#define VA_THROW    0x00405100u   /* lifted guest frame with an EH checkpoint */
#define VA_CATCH    0x00405200u   /* standalone lifted C++ catch funclet */
#define VA_INLINE   0x00405300u   /* inline DMD catch label in the guest frame */
#define ESP_OFFSET  0x30u         /* DHandlerTable.espoffset */
#define BPOFF       0xFFFFFFF0u   /* catch variable at [ebp-0x10] */
#define EH_MAGIC    0x19930520u   /* MSVC 6 FuncInfo magic */
#define SENTINEL    0x5EEDFACEu
#define BAD_ESP     0xDEADBEEFu

enum { DMD_VALID = 0, DMD_SELF_LOOP, DMD_TWO_NODE_CYCLE };

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

#define GUEST_PROLOGUE(va)              \
    uint32_t _cf = 0;                   \
    uint32_t _flag_k = FK_NONE;         \
    uint32_t _flag_a = 0, _flag_b = 0;  \
    RECOMP_ENTER(va)

/* DMD fixture state. */
static uint32_t g_dmd_stub_va, g_dmd_tbl_va, g_exc_obj, g_args_va;
static uint32_t g_base_ci, g_obj_ci, g_vtbl;
static uint32_t g_catch_local, g_catch_value, g_seen_ebp, g_seen_esp;
static int g_dmd_chain_mode, g_dmd_catch_calls, g_dmd_returned, g_dmd_dispatched;
static const char *g_dmd_reason;

/* C++ fixture state. */
static uint32_t g_cxx_stub_va, g_cxx_obj, g_cxx_info;
static int g_cxx_funclet_calls, g_cxx_caught, g_cxx_returned, g_cxx_dispatched;
static uint32_t g_catch_esp, g_cxx_seen_ebp, g_cxx_seen_esp;

/* Snapshot shared by both fixtures, taken immediately before the throw. */
static uint32_t g_fs_before, g_record_va, g_prev_before, g_handler_before,
                g_idx_before, g_ebp_before, g_esp_before;
static int g_chain_unchanged, g_regs_unchanged;

static void snapshot(uint32_t record) {
    g_record_va = record;
    g_fs_before = MEM32(FS_BASE);
    g_prev_before = MEM32(record);
    g_handler_before = MEM32(record + 4u);
    g_idx_before = MEM32(record + 8u);
    g_ebp_before = g_ebp;
    g_esp_before = g_esp;
}

/* Check the throw did not mutate the registration chain or the register file
 * before refusing.  Called inside the guest frame, before its own epilogue
 * restores FS:[0]. */
static void check_after(void) {
    g_chain_unchanged = MEM32(FS_BASE) == g_fs_before &&
        MEM32(g_record_va) == g_prev_before &&
        MEM32(g_record_va + 4u) == g_handler_before &&
        MEM32(g_record_va + 8u) == g_idx_before;
    g_regs_unchanged = g_ebp == g_ebp_before && g_esp == g_esp_before;
}

/* Synthetic lifted frame: installs a DMD SEH record whose `prev` is either the
 * real chain end, itself, or a two-node cycle, then throws. */
static void guest_dmd_attempt(void) {
    GUEST_PROLOGUE(VA_THROW);
    uint32_t _itail_tgt = 0;
    uint32_t saved_fs = MEM32(FS_BASE);
    RECOMP_EH_ENTER();
    PUSH32(esp, ebp);
    ebp = esp;

    PUSH32(esp, 0u);                       /* [8] = active scope index */
    PUSH32(esp, g_dmd_stub_va);            /* [4] = handler stub */
    uint32_t record = esp - 4u;
    uint32_t prev = saved_fs;
    if (g_dmd_chain_mode == DMD_SELF_LOOP) {
        prev = record;                     /* record.prev == record */
    } else if (g_dmd_chain_mode == DMD_TWO_NODE_CYCLE) {
        uint32_t other = record - 32u;     /* plausible stack record */
        MEM32(other) = record;             /* other.prev  -> record */
        MEM32(other + 4u) = 0u;            /* no DMD table there */
        MEM32(other + 8u) = 0u;
        prev = other;                      /* record.prev -> other */
    }
    PUSH32(esp, prev);                     /* [0] = prev; esp becomes record */

    MEM32(FS_BASE) = record;
    g_catch_local = ebp + (uint32_t)(int32_t)BPOFF;
    MEM32(g_catch_local) = SENTINEL;
    snapshot(record);
    g_dmd_reason = "no call";
    g_dmd_catch_calls = 0;
    g_dmd_returned = 0;
    g_dmd_dispatched = hle_eh_d_throw(0xE0440001u, 1u, g_args_va, &g_dmd_reason);
    g_dmd_returned = 1;
    check_after();
    MEM32(FS_BASE) = saved_fs;
    RECOMP_EH_LEAVE();
    return;

  _ljump:
    switch (_itail_tgt) {
      case VA_INLINE: goto L_DMD_CATCH;
      default: MEM32(FS_BASE) = saved_fs; RECOMP_EH_LEAVE(); return;
    }
  L_DMD_CATCH:
    g_dmd_catch_calls++;
    g_seen_ebp = ebp;
    g_seen_esp = esp;
    g_catch_value = MEM32(g_catch_local);
    MEM32(FS_BASE) = saved_fs;
    RECOMP_EH_LEAVE();
}

/* Synthetic lifted frame: installs an MSVC C++ registration record and throws
 * an MSVC exception whose ThrowInfo names a catch(...) handler. */
static void guest_cxx_attempt(void) {
    GUEST_PROLOGUE(VA_THROW);
    uint32_t _itail_tgt = 0;
    uint32_t saved_fs = MEM32(FS_BASE);
    RECOMP_EH_ENTER();
    PUSH32(esp, ebp);
    ebp = esp;

    PUSH32(esp, 0u);                       /* [8] = state inside try block */
    PUSH32(esp, g_cxx_stub_va);            /* [4] = MSVC C++ handler stub */
    uint32_t record = esp - 4u;
    PUSH32(esp, saved_fs);                 /* [0] = prev; esp becomes record */

    MEM32(FS_BASE) = record;
    MEM32(ebp - 16u) = g_catch_esp;        /* the hand-off slot under test */
    snapshot(record);
    g_cxx_funclet_calls = 0;
    g_cxx_caught = 0;
    g_cxx_returned = 0;
    g_cxx_dispatched = hle_eh_cxx_throw(g_cxx_obj, g_cxx_info);
    g_cxx_returned = 1;
    check_after();
    MEM32(FS_BASE) = saved_fs;
    RECOMP_EH_LEAVE();
    return;

  _ljump:
    /* A caught throw longjmps here with the guest ESP/EBP set for the catch. */
    g_seen_ebp = ebp;
    g_cxx_seen_ebp = ebp;
    g_cxx_seen_esp = esp;
    g_cxx_caught = 1;
    MEM32(FS_BASE) = saved_fs;
    RECOMP_EH_LEAVE();
}

/* The catch continuation entered by call_funclet() on a matching C++ throw. */
static void cxx_catch_funclet(void) {
    GUEST_PROLOGUE(VA_CATCH);
    g_cxx_funclet_calls++;
}

const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { VA_THROW, guest_dmd_attempt },     /* both fixtures share the VA */
    { VA_CATCH, cxx_catch_funclet },
};
const uint32_t recomp_dispatch_count = 2;

static void build_dmd_metadata(void) {
    g_base_ci = hle_va(hle_alloc(0x40));
    g_obj_ci  = hle_va(hle_alloc(0x40));
    g_vtbl    = hle_va(hle_alloc(8));
    uint8_t *stub = (uint8_t *)hle_alloc(16);
    g_dmd_tbl_va = hle_va(hle_alloc(64));
    g_exc_obj = hle_va(hle_alloc(16));
    g_args_va = hle_va(hle_alloc(8));
    g_dmd_stub_va = hle_va(stub);

    memset(hle_ptr(g_base_ci), 0, 0x40);
    memset(hle_ptr(g_obj_ci), 0, 0x40);
    MEM32(g_obj_ci + 0x28u) = g_base_ci;
    MEM32(g_vtbl) = g_obj_ci;
    MEM32(g_exc_obj) = g_vtbl;
    MEM32(g_args_va) = g_exc_obj;

    stub[0] = 0xB8u;
    memcpy(stub + 1, &g_dmd_tbl_va, 4);    /* mov eax, DHandlerTable */
    stub[5] = 0xE9u;
    memset(stub + 6, 0, 4);

    MEM32(g_dmd_tbl_va + 0u) = VA_THROW;
    MEM32(g_dmd_tbl_va + 4u) = ESP_OFFSET;
    MEM32(g_dmd_tbl_va + 8u) = 0x50u;
    MEM32(g_dmd_tbl_va + 12u) = 0xFFFFFFFFu;
    MEM32(g_dmd_tbl_va + 16u) = 24u;
    MEM32(g_dmd_tbl_va + 20u) = 0u;
    MEM32(g_dmd_tbl_va + 24u) = 1u;
    MEM32(g_dmd_tbl_va + 28u) = g_base_ci;
    MEM32(g_dmd_tbl_va + 32u) = BPOFF;
    MEM32(g_dmd_tbl_va + 36u) = VA_INLINE;
}

/* FuncInfo + unwind map + one try block + one catch(...) handler, and a
 * `mov eax, FuncInfo; jmp __CxxFrameHandler` handler stub. */
static void build_cxx_metadata(void) {
    g_cxx_obj  = hle_va(hle_alloc(16));
    g_cxx_info = hle_va(hle_alloc(16));    /* only needs to be a nonzero VA */
    uint32_t fi      = hle_va(hle_alloc(64));
    uint32_t unwind  = hle_va(hle_alloc(8));
    uint32_t tm      = hle_va(hle_alloc(20));
    uint32_t handler = hle_va(hle_alloc(16));
    uint8_t *stub    = (uint8_t *)hle_alloc(16);

    MEM32(unwind) = 0xFFFFFFFFu;           /* state 0: no unwind action */
    MEM32(unwind + 4u) = 0u;
    MEM32(fi) = EH_MAGIC;
    MEM32(fi + 4u) = 1u;                   /* maxState */
    MEM32(fi + 8u) = unwind;               /* pUnwindMap */
    MEM32(fi + 12u) = 1u;                  /* nTryBlocks */
    MEM32(fi + 16u) = tm;                  /* pTryBlockMap */
    MEM32(tm) = 0u;                        /* low state */
    MEM32(tm + 4u) = 0u;                   /* high state */
    MEM32(tm + 8u) = 0u;
    MEM32(tm + 12u) = 1u;                  /* nCatches */
    MEM32(tm + 16u) = handler;
    MEM32(handler) = 0u;                   /* adjectives */
    MEM32(handler + 4u) = 0u;              /* type 0 -> catch(...) */
    MEM32(handler + 8u) = 0u;              /* no catch local */
    MEM32(handler + 12u) = VA_CATCH;       /* lifted catch funclet */

    stub[0] = 0xB8u;
    memcpy(stub + 1, &fi, 4);
    stub[5] = 0xE9u;
    memset(stub + 6, 0, 4);
    g_cxx_stub_va = hle_va(stub);
}

static int has(const char *needle) {
    return g_dmd_reason && strstr(g_dmd_reason, needle) != NULL;
}

/* Capture diagnostics across the whole run; checked at the end. */
static char g_diag[8192];
static size_t g_diag_len;
static int g_saved_stderr, g_diag_fds[2];

static void diag_begin(void) {
    fflush(stderr);
    if (pipe(g_diag_fds) != 0) { ok("pipe() for diagnostics", 0); return; }
    g_saved_stderr = dup(2);
    dup2(g_diag_fds[1], 2);
    close(g_diag_fds[1]);
}
static void diag_end(void) {
    fflush(stderr);
    dup2(g_saved_stderr, 2);
    close(g_saved_stderr);
    ssize_t n;
    while (g_diag_len + 1 < sizeof g_diag &&
           (n = read(g_diag_fds[0], g_diag + g_diag_len,
                     sizeof g_diag - 1 - g_diag_len)) > 0)
        g_diag_len += (size_t)n;
    g_diag[g_diag_len] = '\0';
    close(g_diag_fds[0]);
}

int main(void) {
    diag_begin();
    mach_enter();                          /* guest stack + simulated TIB */

    build_dmd_metadata();

    /* Case 1: a well-formed DMD chain still dispatches to the guest catch. */
    g_dmd_chain_mode = DMD_VALID;
    guest_dmd_attempt();
    ok("valid DMD chain: catch was invoked", g_dmd_catch_calls == 1);
    /* The valid inline catch longjmps past the assignment to g_dmd_dispatched;
     * the reason is written just before the jump. */
    ok("valid DMD chain: catch reason reported",
       has("dispatched to guest catch"));
    ok("valid DMD chain: catch local received the exception",
       g_catch_value == g_exc_obj);

    /* Case 2: self-referential registration record.  Before the guard this
     * dispatched a catch off a malformed chain; now it must refuse, name the
     * cycle and leave all guest state untouched. */
    g_dmd_chain_mode = DMD_SELF_LOOP;
    guest_dmd_attempt();
    ok("self-referential FS record: explicit cyclic-chain reason",
       g_dmd_dispatched == 0 && has("cyclic FS registration chain"));
    ok("self-referential FS record: no guest catch ran",
       g_dmd_catch_calls == 0);
    ok("self-referential FS record: chain and registers unchanged",
       g_chain_unchanged && g_regs_unchanged && g_dmd_returned == 1);
    ok("self-referential FS record: catch local untouched",
       MEM32(g_catch_local) == SENTINEL);

    /* Case 3: a two-node cycle in the chain above the registration record. */
    g_dmd_chain_mode = DMD_TWO_NODE_CYCLE;
    guest_dmd_attempt();
    ok("two-node FS cycle: explicit cyclic-chain reason",
       g_dmd_dispatched == 0 && has("cyclic FS registration chain"));
    ok("two-node FS cycle: no guest catch ran", g_dmd_catch_calls == 0);
    ok("two-node FS cycle: chain and registers unchanged",
       g_chain_unchanged && g_regs_unchanged && g_dmd_returned == 1);
    ok("two-node FS cycle: catch local untouched",
       MEM32(g_catch_local) == SENTINEL);

    build_cxx_metadata();

    /* Case 4: a valid C++ catch ESP still resumes the catch with guest
     * EBP/ESP set from the catch frame (no regression). */
    {
        uint32_t tib = FS_BASE;
        g_catch_esp = MEM32(tib + 4u) - 0x80u;   /* inside the guest stack */
        guest_cxx_attempt();
        ok("valid C++ catch ESP: lifted catch funclet was called",
           g_cxx_funclet_calls == 1);
        ok("valid C++ catch ESP: guest frame resumed", g_cxx_caught == 1);
        ok("valid C++ catch ESP: catch EBP/ESP handed off",
           g_cxx_seen_ebp == g_record_va + 12u &&
           g_cxx_seen_esp == g_catch_esp);
    }

    /* Case 5: an out-of-range catch ESP must be refused before any guest
     * write or unwind, with an explicit diagnostic -- not a fake catch. */
    g_catch_esp = BAD_ESP;
    guest_cxx_attempt();
    ok("bad C++ catch ESP: throw was declined",
       g_cxx_dispatched == 0 && g_cxx_returned == 1);
    ok("bad C++ catch ESP: no guest catch ran",
       g_cxx_funclet_calls == 0 && g_cxx_caught == 0);
    ok("bad C++ catch ESP: chain and registers unchanged",
       g_chain_unchanged && g_regs_unchanged);

    mach_leave();
    diag_end();
    ok("bad C++ catch ESP: explicit diagnostic",
       strstr(g_diag, "C++ catch ESP is outside the guest stack") != NULL);

    if (fails == 0) printf("eh_validation_selftest: all checks passed\n");
    return fails != 0;
}
