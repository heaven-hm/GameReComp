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

static int dispatch_cxx(uint32_t object, uint32_t info) {
    uint32_t top = MEM32(g_fs_base);
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
                /* Unwind C++ registrations above this handler. */
                for (uint32_t above = top; above != record; above = MEM32(above)) {
                    if (!plausible_stack_record(above)) return 0;
                    uint32_t above_fi = cxx_funcinfo(MEM32(above + 4u));
                    if (above_fi && !unwind_to(above, above_fi, -1)) return 0;
                }
                if (!unwind_to(record, fi, low)) return 0;
                MEM32(g_fs_base) = record;
                MEM32(record + 8u) = (uint32_t)(high + 1);
                uint32_t ebp = record + 12u;
                if (!bind_catch_object(handler, ct, object, ebp)) return 0;
                uint32_t saved_object = g_throw_object, saved_info = g_throw_info;
                g_throw_object = object; g_throw_info = info;
                uint32_t resume = call_funclet(MEM32(handler + 12u), ebp);
                g_throw_object = saved_object; g_throw_info = saved_info;
                for (recomp_ehframe_t *frame = g_eh_frames; frame; frame = frame->prev) {
                    if (frame->entry_esp != ebp + 4u) continue;
                    g_ebp = ebp;
                    g_esp = MEM32(ebp - 16u);
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
    return dispatch_cxx(object, throw_info);
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

int hle_eh_d_throw(uint32_t code, uint32_t n_args, uint32_t args_va) {
    if (code != 0xE0440001u || n_args < 1u || !args_va || !g_fs_base) return 0;
    uint32_t exc_obj = MEM32(args_va);
    if (!exc_obj) return 0;
    uint32_t vtbl = MEM32(exc_obj);
    uint32_t obj_ci = vtbl ? MEM32(vtbl) : 0u;
    uint32_t top = MEM32(g_fs_base), record = top;
    for (unsigned walked = 0; record != EH_END_CHAIN && walked < EH_MAX_CHAIN; walked++) {
        if (!plausible_stack_record(record)) return 0;
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
                        recomp_ehframe_t *target_frame = NULL;
                        for (recomp_ehframe_t *frame = g_eh_frames; frame; frame = frame->prev) {
                            if (frame->entry_esp == ebp + 4u) { target_frame = frame; break; }
                        }
                        if (!target_frame) return 0;
                        for (uint32_t above = top; above != record; above = MEM32(above)) {
                            if (!plausible_stack_record(above)) return 0;
                            uint32_t atbl = dmd_table(MEM32(above + 4u));
                            if (atbl && !dmd_local_unwind(above, atbl, -1)) return 0;
                        }
                        if (!dmd_local_unwind(record, tbl, idx)) return 0;
                        MEM32(g_fs_base) = record;
                        MEM32(record + 8u) = (uint32_t)prev;
                        MEM32(ebp + (uint32_t)bpoff) = exc_obj;
                        g_ebp = ebp;
                        g_esp = ebp - MEM32(tbl + 4u);
                        recomp_eh_resume(target_frame, catch_va);
                        return 0;
                    }
                }
            }
            idx = prev;
        }
        record = MEM32(record);
    }
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
