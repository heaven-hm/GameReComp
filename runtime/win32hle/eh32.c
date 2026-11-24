/*
 * Portable guest MSVC C++ exception checkpoints.
 *
 * The generated C already emits RECOMP_EH_ENTER for frame-based MSVC
 * functions with a recoverable catch continuation.  native32 had an
 * implementation for its Windows-only host; this file supplies the matching
 * mechanism for win32hle's portable, guest-address-based host.  It reads
 * FuncInfo and registration records from guest memory and calls only lifted
 * guest funclets.  It deliberately declines malformed or unsupported frames;
 * callers preserve the existing explicit failure instead of continuing with
 * a corrupted stack.
 */
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

#define EH_END_CHAIN 0xffffffffu
#define EH_MAGIC_VC6 0x19930520u
#define EH_MAGIC_VC7 0x19930521u
#define EH_MAGIC_VC8 0x19930522u
#define EH_MAX_CHAIN 1024u
#define EH_MAX_ITEMS 4096u

static _Thread_local recomp_ehframe_t *g_eh_frames;
static _Thread_local uint32_t g_throw_object, g_throw_info;

void recomp_eh_push(recomp_ehframe_t *frame) {
    frame->prev = g_eh_frames;
    frame->resume = 0;
    g_eh_frames = frame;
}

void recomp_eh_pop(recomp_ehframe_t *frame) {
    recomp_ehframe_t **link = &g_eh_frames;
    while (*link) {
        if (*link == frame) { *link = frame->prev; return; }
        link = &(*link)->prev;
    }
}

recomp_ehframe_t *recomp_eh_owner(uint32_t esp) {
    for (recomp_ehframe_t *f = g_eh_frames; f; f = f->prev)
        if (f->entry_esp > esp) return f;
    return NULL;
}

void recomp_eh_resume(recomp_ehframe_t *frame, uint32_t resume) {
    if (!frame) return;
    g_eh_frames = frame;
    frame->resume = resume;
    g_cur_func = frame->func;
    longjmp(frame->jb, 1);
}

static int plausible_stack_record(uint32_t record) {
    if (!g_fs_base || !record || (record & 3u)) return 0;
    uint32_t lo = MEM32(g_fs_base + 8), hi = MEM32(g_fs_base + 4);
    return lo < hi && record >= lo && record <= hi - 16u;
}

/* The FS:[0] registration chain is guest-controlled.  Both EH dispatchers
 * walked it with no bound in their inner unwind loops, and their outer walks
 * stopped at EH_MAX_CHAIN; a cyclic chain therefore either spun or was
 * mislabelled "no matching catch handler".  Walk with Floyd's tortoise/hare:
 * reach `stop` (EH_END_CHAIN for the outer walk, the catch-owning record for
 * the inner unwind), or report a malformed/cyclic chain with a named reason
 * before any guest state is committed. */
static int fs_chain_to(uint32_t head, uint32_t stop, const char **why) {
    uint32_t tortoise = head, hare = head;
    for (;;) {
        if (tortoise == stop) return 1;
        if (!plausible_stack_record(tortoise)) {
            if (why) *why = "malformed registration record in FS chain";
            return 0;
        }
        tortoise = MEM32(tortoise);

        if (hare == stop) return 1;
        if (!plausible_stack_record(hare)) {
            if (why) *why = "malformed registration record in FS chain";
            return 0;
        }
        hare = MEM32(hare);
        if (hare == stop) return 1;
        if (!plausible_stack_record(hare)) {
            if (why) *why = "malformed registration record in FS chain";
            return 0;
        }
        hare = MEM32(hare);

        if (tortoise == hare) {
            if (why) *why = "cyclic FS registration chain";
            return 0;
        }
    }
}

static uint32_t read_u32_unaligned(uint32_t va) {
    uint32_t value;
    memcpy(&value, hle_ptr(va), sizeof value);
    return value;
}

/* An MSVC C++ registration handler begins "mov eax, FuncInfo" then jumps to
 * __CxxFrameHandler.  Other SEH handlers are left for the generic SEH layer. */
static uint32_t cxx_funcinfo(uint32_t handler) {
    if (handler < 0x10000u) return 0;
    const uint8_t *p = hle_ptr(handler);
    if (!p || p[0] != 0xB8u || (p[5] != 0xE9u && !(p[5] == 0xFFu && p[6] == 0x25u)))
        return 0;
    uint32_t fi = read_u32_unaligned(handler + 1u);
    if (fi < 0x10000u) return 0;
    uint32_t magic = MEM32(fi);
    return magic == EH_MAGIC_VC6 || magic == EH_MAGIC_VC7 || magic == EH_MAGIC_VC8 ? fi : 0;
}

/* Execute a lifted funclet with the frame's EBP.  The normal guest-call
 * bridge restores the caller's registers; only EAX is a funclet result. */
static uint32_t call_funclet(uint32_t va, uint32_t frame_ebp) {
    if (!recomp_lookup(va)) return 0;
    uint32_t saved_ebp = g_ebp;
    g_ebp = frame_ebp;
    uint32_t result = hle_call_guest(va, 0, NULL);
    g_ebp = saved_ebp;
    return result;
}

static int unwind_to(uint32_t registration, uint32_t fi, int target_state) {
    int state = (int)MEM32(registration + 8u);
    int count = (int)MEM32(fi + 4u);
    uint32_t map = MEM32(fi + 8u);
    for (unsigned steps = 0; state != target_state && state >= 0 && state < count; steps++) {
        if (steps == EH_MAX_ITEMS) return 0;
        uint32_t entry = map + (uint32_t)state * 8u;
        int next = (int)MEM32(entry);
        uint32_t action = MEM32(entry + 4u);
        MEM32(registration + 8u) = (uint32_t)next;
        if (action && !call_funclet(action, registration + 12u)) return 0;
        state = next;
    }
    return state == target_state;
}

static uint32_t catchable_type(uint32_t info, uint32_t wanted_type) {
    uint32_t array = MEM32(info + 12u);
    int count = (int)MEM32(array);
    if (count < 0 || count > (int)EH_MAX_ITEMS) return 0;
    for (int i = 0; i < count; i++) {
        uint32_t ct = MEM32(array + 4u + (uint32_t)i * 4u);
        uint32_t type = MEM32(ct + 4u);
        if (type == wanted_type) return ct;
        if (type && wanted_type && !strcmp((const char *)hle_ptr(type + 8u),
                                           (const char *)hle_ptr(wanted_type + 8u))) return ct;
    }
    return 0;
}

static int bind_catch_object(uint32_t handler, uint32_t catchable, uint32_t object, uint32_t frame_ebp) {
    uint32_t adjectives = MEM32(handler), type = MEM32(handler + 4u), displacement = MEM32(handler + 8u);
    if (!type || !displacement || !catchable) return 1; /* catch(...) or no local */
    uint32_t destination = frame_ebp + displacement;
    uint32_t source = object + MEM32(catchable + 8u);
    uint32_t size = MEM32(catchable + 20u), copy_ctor = MEM32(catchable + 24u);
    if (adjectives & 8u) { MEM32(destination) = source; return 1; }
    /* Calling a copy constructor needs a thiscall guest invocation.  Do not
     * pretend a value catch is safe until that ABI path is implemented. */
    if (copy_ctor && !(MEM32(catchable) & 1u)) return 0;
    if (size > (1u << 20)) return 0;
    memcpy(hle_ptr(destination), hle_ptr(source), size);
    return 1;
}

static int dispatch_cxx(uint32_t object, uint32_t info, const char **why) {
    uint32_t top = MEM32(g_fs_base);
    *why = NULL;
    /* Reject a cyclic or malformed chain up front: without this the walk ran
     * to EH_MAX_CHAIN and then reported the misleading "no matching catch". */
    if (!fs_chain_to(top, EH_END_CHAIN, why)) return 0;
    uint32_t record = top;
    for (unsigned walked = 0; record != EH_END_CHAIN && walked < EH_MAX_CHAIN; walked++) {
        if (!plausible_stack_record(record)) return 0;
        uint32_t fi = cxx_funcinfo(MEM32(record + 4u));
        if (!fi) { record = MEM32(record); continue; }
        int state = (int)MEM32(record + 8u);
        int tries = (int)MEM32(fi + 12u);
        uint32_t try_map = MEM32(fi + 16u);
        if (tries < 0 || tries > (int)EH_MAX_ITEMS) return 0;
        for (int t = 0; t < tries; t++) {
            uint32_t entry = try_map + (uint32_t)t * 20u;
            int low = (int)MEM32(entry), high = (int)MEM32(entry + 4u);
            int catches = (int)MEM32(entry + 12u);
            uint32_t handlers = MEM32(entry + 16u);
            if (state < low || state > high || catches < 0 || catches > (int)EH_MAX_ITEMS) continue;
            for (int c = 0; c < catches; c++) {
                uint32_t handler = handlers + (uint32_t)c * 16u;
                uint32_t type = MEM32(handler + 4u);
                uint32_t ct = type ? catchable_type(info, type) : 0;
                if (type && !ct) continue;
                uint32_t ebp = record + 12u;
                /* Validate the catch frame's ESP hand-off before any guest
                 * write or unwind.  The catch is resumed with the ESP saved at
                 * [ebp-16]; an out-of-range value would run guest code with a
                 * corrupt stack pointer and bind locals from outside the guest
                 * stack (the issue #27 failure signature).  The DMD path already
                 * checks its DHandlerTable espoffset the same way. */
                uint32_t stack_lo = MEM32(g_fs_base + 8u);
                uint32_t stack_hi = MEM32(g_fs_base + 4u);
                if (stack_lo >= stack_hi || ebp - 16u < stack_lo || ebp > stack_hi) {
                    *why = "C++ catch frame EBP is outside the guest stack";
                    return 0;
                }
                uint32_t catch_esp = MEM32(ebp - 16u);
                if (catch_esp < stack_lo || catch_esp > stack_hi - 16u) {
                    *why = "C++ catch ESP is outside the guest stack (malformed catch frame)";
                    return 0;
                }
                if (!fs_chain_to(top, record, why)) return 0;
                /* Unwind C++ registrations above this handler. */
                for (uint32_t above = top; above != record; above = MEM32(above)) {
                    uint32_t above_fi = cxx_funcinfo(MEM32(above + 4u));
                    if (above_fi && !unwind_to(above, above_fi, -1)) return 0;
                }
                if (!unwind_to(record, fi, low)) return 0;
                if (!bind_catch_object(handler, ct, object, ebp)) return 0;
                MEM32(g_fs_base) = record;
                MEM32(record + 8u) = (uint32_t)(high + 1);
                uint32_t saved_object = g_throw_object, saved_info = g_throw_info;
                g_throw_object = object; g_throw_info = info;
                uint32_t resume = call_funclet(MEM32(handler + 12u), ebp);
                g_throw_object = saved_object; g_throw_info = saved_info;
                for (recomp_ehframe_t *frame = g_eh_frames; frame; frame = frame->prev) {
                    if (frame->entry_esp != ebp + 4u) continue;
                    g_ebp = ebp;
                    g_esp = catch_esp;
                    g_eax = resume;
                    recomp_eh_resume(frame, resume);
                }
                return 0;
            }
        }
        record = MEM32(record);
    }
    return 0;
}

int hle_eh_cxx_throw(uint32_t object, uint32_t throw_info) {
    if (!throw_info || !g_fs_base) return 0;
    const char *why = NULL;
    int dispatched = dispatch_cxx(object, throw_info, &why);
    if (!dispatched && why)
        fprintf(stderr, "[win32hle] C++ throw refused: %s\n", why);
    return dispatched;
}

/* Digital Mars D structured exception tables (DMD 1.x): handler stub is
 * `mov eax, DHandlerTable; jmp _d_framehandler`, and table layout is
 * { fptr, espoffset, retoffset } followed by DHandlerInfo[]:
 * { prev_index, cioffset, finally_code } and DCatchInfo { ncatches, DCatchBlock[] }
 * where DCatchBlock is { ClassInfo*, bpoffset, catch_code }. */
static int dmd_isbaseof(uint32_t cls, uint32_t base) {
    if (!cls || !base) return 0;
    for (unsigned depth = 0; cls && depth < 64u; depth++) {
        if (cls == base) return 1;
        cls = MEM32(cls + 0x28u);
    }
    return 0;
}

/* A DMD throw object is the guest's Throwable: obj[0] is its vptr and vptr[0]
 * is its ClassInfo.  gr.exe's own frame handler reads exactly that chain
 * before it can match a catch (0x41E048: mov ecx,[eax+0x14];
 * mov edx,[ecx]; mov ebx,[edx]) -- so does dmd_isbaseof() below.
 *
 * When a recompiled guest's class vtables were never constructed, that vptr
 * is NULL or the ClassInfo chain is broken.  Resuming a catch with such an
 * object can bind a null vtable into the catch local; the catch block's virtual
 * call then faults with the issue #27 signature (ICALL: null target from the
 * catch frame, then SIGBUS in its caller).  Validate the object before any
 * guest state is committed, and name the exact defect instead of reporting a
 * misleading "no matching catch handler". */
static uint32_t dmd_exc_classinfo(uint32_t exc_obj, const char **why) {
    uint32_t vtbl = exc_obj ? MEM32(exc_obj) : 0u;
    if (!vtbl) {
        *why = "DMD throw object has a NULL vtable (object was never constructed)";
        return 0;
    }
    uint32_t ci = MEM32(vtbl);
    if (!ci) {
        *why = "DMD throw object's vtable has a NULL ClassInfo";
        return 0;
    }
    if (ci < 0x10000u || !hle_ptr(ci)) {
        *why = "DMD throw object's ClassInfo is not readable guest memory";
        return 0;
    }
    /* A cyclic base chain is malformed metadata: dmd_isbaseof's 64-step cap
     * bounds the walk, but the failure must not look like a missing catch. */
    for (uint32_t p = ci, steps = 0; p && steps < 64u; steps++) {
        uint32_t next = MEM32(p + 0x28u);
        if (next == p || next == ci) {
            *why = "DMD throw object's ClassInfo has a cyclic base chain";
            return 0;
        }
        p = next;
    }
    return ci;
}

static uint32_t dmd_table(uint32_t handler) {
    if (handler < 0x10000u) return 0;
    const uint8_t *p = hle_ptr(handler);
    if (!p || p[0] != 0xB8u || (p[5] != 0xE9u && !(p[5] == 0xFFu && p[6] == 0x25u)))
        return 0;
    uint32_t tbl = read_u32_unaligned(handler + 1u);
    if (tbl < 0x10000u) return 0;
    uint32_t fptr = MEM32(tbl);
    if (fptr == EH_MAGIC_VC6 || fptr == EH_MAGIC_VC7 || fptr == EH_MAGIC_VC8) return 0;
    if (!recomp_lookup(fptr)) return 0;
    uint32_t espoff = MEM32(tbl + 4u), retoff = MEM32(tbl + 8u);
    if (espoff >= 0x100000u || retoff == 0u || retoff >= 0x100000u) return 0;
    return tbl;
}

static int dmd_local_unwind(uint32_t record, uint32_t tbl, int stop_index) {
    int idx = (int)MEM32(record + 8u);
    for (unsigned steps = 0; idx != -1 && idx != stop_index; steps++) {
        if (steps == EH_MAX_ITEMS || idx < 0 || idx >= 1024) return 0;
        uint32_t ent = tbl + 12u + (uint32_t)idx * 12u;
        int prev = (int)MEM32(ent);
        uint32_t fva = MEM32(ent + 8u);
        MEM32(record + 8u) = (uint32_t)prev;
        if (fva) {
            if (!recomp_lookup(fva)) return 0;
            call_funclet(fva, record + 12u);
        }
        idx = prev;
    }
    return idx == stop_index;
}

/* The catch record at [fs:0] belongs to the function whose EBP is record+12.
 * Normally that function has a RECOMP_EH_ENTER checkpoint at entry_esp == EBP+4,
 * and recomp_eh_resume longjmps straight into its landing pad.
 *
 * DMD also emits tail-call stubs such as gr.exe's
 *     0040DB9C: add eax, -0Ch ; jmp 0040DBC0
 * where the callee opens an SEH frame (mov fs:[0], esp) but its body is lifted
 * *into the stub's function*.  The stub has no EH prologue, so the lifter emits
 * no RECOMP_EH_ENTER for that EBP: the guest registration record is live, but
 * there is no host checkpoint whose entry_esp == EBP+4.  Exact matching then
 * fails with "no matching lifted guest frame" even though the catch table is
 * valid (measured on gr.exe: record 0x104FEE94 -> table 0x0044BAF8 ->
 * catch 0x0040DC96).
 *
 * Recover without touching the guest metadata: use the innermost *live*
 * checkpoint whose entry_esp is inside the catch-owning guest frame.  It is a
 * valid setjmp target between the throw site and that frame, so longjmp there
 * unwinds the host C frames; its landing pad's default arm tail-calls the
 * lifted catch VA with the guest EBP/ESP set for the catch owner.  The exact
 * owner is still preferred whenever it exists. */
static recomp_ehframe_t *dmd_resume_frame(uint32_t ebp, int *exact) {
    recomp_ehframe_t *inner = NULL;
    *exact = 0;
    for (recomp_ehframe_t *f = g_eh_frames; f; f = f->prev) {
        if (f->entry_esp == ebp + 4u) { *exact = 1; return f; }
        /* entry_esp is the function's entry stack pointer; a frame whose entry
         * is above this record's EBP was called inside the catch-owning frame. */
        if (f->entry_esp > ebp + 4u && (!inner || f->entry_esp < inner->entry_esp))
            inner = f;
    }
    return inner;
}

int hle_eh_d_throw(uint32_t code, uint32_t n_args, uint32_t args_va,
                   const char **reason) {
    if (reason) *reason = "unsupported throw";
    if (code != 0xE0440001u) { if (reason) *reason = "not a DMD throw"; return 0; }
    if (n_args < 1u || !args_va) { if (reason) *reason = "missing exception arguments"; return 0; }
    if (!g_fs_base) { if (reason) *reason = "no guest TIB/FS base"; return 0; }
    uint32_t exc_obj = MEM32(args_va);
    if (!exc_obj) { if (reason) *reason = "null exception object"; return 0; }
    /* Refuse a throw whose object/vtable/ClassInfo chain is malformed before
     * touching any guest state.  Otherwise we would bind a null vtable into a
     * catch local and resume code that faulted with issue #27's signature. */
    const char *obj_why = "malformed DMD throw object";
    uint32_t obj_ci = dmd_exc_classinfo(exc_obj, &obj_why);
    if (!obj_ci) { if (reason) *reason = obj_why; return 0; }
    uint32_t top = MEM32(g_fs_base), record = top;
    /* Refuse a cyclic or malformed chain with a named reason instead of
     * walking EH_MAX_CHAIN nodes and reporting "no matching catch handler". */
    if (!fs_chain_to(top, EH_END_CHAIN, reason)) return 0;
    for (unsigned walked = 0; record != EH_END_CHAIN && walked < EH_MAX_CHAIN; walked++) {
        if (!plausible_stack_record(record)) { if (reason) *reason = "malformed registration record"; return 0; }
        uint32_t tbl = dmd_table(MEM32(record + 4u));
        if (!tbl) { record = MEM32(record); continue; }
        int idx = (int)MEM32(record + 8u);
        for (unsigned s = 0; idx != -1 && s < EH_MAX_ITEMS; s++) {
            if (idx < 0 || idx >= 1024) break;
            uint32_t ent = tbl + 12u + (uint32_t)idx * 12u;
            int prev = (int)MEM32(ent);
            uint32_t cioff = MEM32(ent + 4u);
            if (cioff >= 24u && cioff < 0x10000u) {
                uint32_t ci = tbl + cioff;
                uint32_t ncatches = MEM32(ci);
                if (ncatches > 0u && ncatches <= 64u) {
                    for (uint32_t c = 0; c < ncatches; c++) {
                        uint32_t cb = ci + 4u + c * 12u;
                        uint32_t wanted = MEM32(cb), catch_va = MEM32(cb + 8u);
                        int32_t bpoff = (int32_t)MEM32(cb + 4u);
                        if (!catch_va || !dmd_isbaseof(obj_ci, wanted)) continue;
                        uint32_t ebp = record + 12u;
                        int exact = 0;
                        recomp_ehframe_t *target_frame = dmd_resume_frame(ebp, &exact);
                        if (!target_frame) {
                            if (reason) *reason = "no EH checkpoint for tail-call-inlined catch frame and no inner live checkpoint";
                            return 0;
                        }
                        /* The fallback enters the catch through the landing pad's
                         * RECOMP_ITAIL(catch_va), so the continuation must be lifted. */
                        if (!exact && !recomp_lookup(catch_va)) {
                            if (reason) *reason = "catch continuation is not a lifted guest function";
                            return 0;
                        }
                        if (!fs_chain_to(top, record, reason)) return 0;
                        for (uint32_t above = top; above != record; above = MEM32(above)) {
                            if (!plausible_stack_record(above)) { if (reason) *reason = "malformed outer registration record"; return 0; }
                            uint32_t atbl = dmd_table(MEM32(above + 4u));
                            if (atbl && !dmd_local_unwind(above, atbl, -1)) { if (reason) *reason = "outer local unwind failed"; return 0; }
                        }
                        if (!dmd_local_unwind(record, tbl, idx)) { if (reason) *reason = "local unwind failed"; return 0; }
                        /* The catch block receives EBP=record+12 and
                         * ESP=EBP-espoffset, exactly as gr.exe's frame handler
                         * sets them up (0x41E0BD..0x41E0D7).  A bogus
                         * espoffset would resume guest code with an ESP
                         * outside the guest stack; its locals (including
                         * object vptrs) would then read as zero.  Check the
                         * hand-off before mutating the registration chain. */
                        uint32_t catch_esp = ebp - MEM32(tbl + 4u);
                        uint32_t stack_lo = MEM32(g_fs_base + 8u);
                        uint32_t stack_hi = MEM32(g_fs_base + 4u);
                        if (!(stack_lo < stack_hi && catch_esp >= stack_lo &&
                              catch_esp <= stack_hi - 16u)) {
                            if (reason) *reason = "catch frame ESP is outside the guest stack (bad DHandlerTable espoffset)";
                            return 0;
                        }
                        MEM32(g_fs_base) = record;
                        MEM32(record + 8u) = (uint32_t)prev;
                        MEM32(ebp + (uint32_t)bpoff) = exc_obj;
                        g_ebp = ebp;
                        g_esp = catch_esp;
                        if (reason) *reason = exact ? "dispatched to guest catch"
                            : "dispatched to guest catch via inner checkpoint (catch frame tail-call-inlined: no EH landing pad)";
                        recomp_eh_resume(target_frame, catch_va);
                        return 0;
                    }
                }
            }
            idx = prev;
        }
        record = MEM32(record);
    }
    if (reason) *reason = "no matching catch handler";
    return 0;
}

void hle_eh_longjmp(uint32_t jump_buffer, uint32_t value) {
    if (!jump_buffer) return;
    uint32_t saved_esp = MEM32(jump_buffer + 16u);
    recomp_ehframe_t *frame = recomp_eh_owner(saved_esp);
    if (!frame) return;
    g_ebp = MEM32(jump_buffer);
    g_ebx = MEM32(jump_buffer + 4u);
    g_edi = MEM32(jump_buffer + 8u);
    g_esi = MEM32(jump_buffer + 12u);
    g_esp = saved_esp;
    g_eax = value ? value : 1u;
    recomp_eh_resume(frame, MEM32(jump_buffer + 20u));
}
