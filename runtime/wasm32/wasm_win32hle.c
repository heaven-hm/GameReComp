#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>
#include <ctype.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <errno.h>
#include "win32hle.h"

__attribute__((import_module("env"), import_name("wasm_host_exit")))
void wasm_host_exit(int code);

/* ============================================================
 * Global x86 State Required by Lifted C (recomp_types.h)
 * ============================================================ */
uint32_t g_eax, g_ecx, g_edx, g_esp;
uint32_t g_ebx, g_esi, g_edi, g_ebp;
double   g_st[8];
int      g_fp_top;
uint16_t g_fpu_cw = 0x037Fu;
uint64_t g_mm[8];
xmm_t    g_xmm[8];
uint32_t g_mxcsr = 0x1F80u;
uint16_t g_seg_cs = 0x1B, g_seg_ds = 0x23, g_seg_es = 0x23, g_seg_fs = 0x3B, g_seg_gs = 0x23, g_seg_ss = 0x23;
uint32_t g_fs_base, g_gs_base, g_es_base;
ptrdiff_t g_mem_base = 0;

uint32_t g_cur_func;
uint32_t g_icall_trace[ICALL_TRACE_SIZE];
uint32_t g_icall_from[ICALL_TRACE_SIZE];
uint32_t g_icall_trace_idx;
uint32_t g_icall_count;

/* Weak defaults overridden when companion DLLs exist */
__attribute__((weak)) const uint32_t recomp_companion_count = 0;
__attribute__((weak)) const recomp_companion_t *const recomp_companion_list = NULL;
__attribute__((weak)) recomp_func_t recomp_lookup_manual(uint32_t va) { (void)va; return NULL; }
__attribute__((weak)) void recomp_eh_push(recomp_ehframe_t *f) { (void)f; }
__attribute__((weak)) void recomp_eh_pop(recomp_ehframe_t *f) { (void)f; }
__attribute__((weak)) recomp_ehframe_t *recomp_eh_owner(uint32_t esp) { (void)esp; return NULL; }
__attribute__((weak)) void recomp_eh_resume(recomp_ehframe_t *f, uint32_t resume) { (void)f; (void)resume; }

void recomp_int(int n) { (void)n; }
uint8_t  recomp_in8(uint32_t p)  { (void)p; return 0; }
uint16_t recomp_in16(uint32_t p) { (void)p; return 0; }
uint32_t recomp_in32(uint32_t p) { (void)p; return 0; }
void recomp_out8(uint32_t p, uint8_t v)   { (void)p; (void)v; }
void recomp_out16(uint32_t p, uint16_t v) { (void)p; (void)v; }
void recomp_out32(uint32_t p, uint32_t v) { (void)p; (void)v; }
void recomp_ins(uint32_t p, uint32_t va, int sz, uint32_t cnt, int dir) { (void)p; (void)va; (void)sz; (void)cnt; (void)dir; }
void recomp_outs(uint32_t p, uint32_t va, int sz, uint32_t cnt, int dir) { (void)p; (void)va; (void)sz; (void)cnt; (void)dir; }
void recomp_cli(void) {}
void recomp_sti(void) {}
void recomp_hlt(void) {}
void recomp_set_seg(int sreg, uint32_t sel) { (void)sreg; (void)sel; }
void recomp_dump_trace(const char *why) { (void)why; }

/* ============================================================
 * Guest VA Pointer / Bump Helpers
 * ============================================================ */
#define GUEST_HEAP_BASE  0x20000000u
#define GUEST_STACK_BASE 0x02400000u
#define GUEST_STACK_SIZE 0x00400000u
#define GUEST_TEB_BASE   0x00280000u
#define GUEST_CRT_BASE   0x002A0000u
#define THUNK_BASE       0x7E000000u
#define MAX_THUNKS       2048

static uint32_t g_guest_heap_ptr = GUEST_HEAP_BASE;
static uint32_t g_crt_bump = GUEST_CRT_BASE;
static int      g_exited = 0, g_exit_code = 0;

void *hle_ptr(uint32_t va) {
    if (!va) return NULL;
    return (void *)ADDR(va);
}

uint32_t hle_guest_alloc(size_t size) {
    uint32_t sz = (uint32_t)((size + 15u) & ~15u);
    if (!sz) sz = 16u;
    uint32_t va = g_guest_heap_ptr;
    wasm_map_guest_region(va, sz);
    g_guest_heap_ptr = va + sz;
    return va;
}

static uint32_t hle_crt_alloc(size_t size) {
    uint32_t sz = (uint32_t)((size + 15u) & ~15u);
    if (!sz) sz = 16u;
    uint32_t va = g_crt_bump;
    wasm_map_guest_region(va, sz);
    g_crt_bump = va + sz;
    return va;
}

uint32_t hle_guest_strdup(const char *s) {
    if (!s) return 0;
    size_t n = strlen(s) + 1;
    uint32_t va = hle_guest_alloc(n);
    memcpy(hle_ptr(va), s, n);
    return va;
}

int hle_has_exited(void) { return g_exited; }
int hle_get_exit_code(void) { return g_exit_code; }

int hle_call_guest(uint32_t va, int argc, const uint32_t *argv) {
    recomp_func_t fn = recomp_lookup(va);
    if (!fn) fn = recomp_lookup_import(va);
    if (!fn) return 0;
    for (int i = argc - 1; i >= 0; i--) PUSH32(g_esp, argv[i]);
    PUSH32(g_esp, RECOMP_RETADDR);
    uint32_t saved = g_cur_func;
    fn();
    g_cur_func = saved;
    return (int)g_eax;
}

/* ============================================================
 * IAT Thunk Registry & PE32 Loader
 * ============================================================ */
typedef struct {
    char          dll[64];
    char          name[128];
    recomp_func_t fn;
    int           resolved;
} thunk_slot_t;

static thunk_slot_t g_thunks[MAX_THUNKS];
static int          g_thunk_n;
static uint32_t     g_last_error = 0;

static void w_unresolved_stub(void) {
    hle_logf(2, "[pe] called unresolved import from 0x%08X\n", g_cur_func);
    RETC(0);
}

static recomp_func_t lookup_shim(const char *dll, const char *name);

static uint32_t register_import(const char *dll, const char *name, int *out_resolved) {
    for (int i = 0; i < g_thunk_n; i++) {
        if (!strcasecmp(g_thunks[i].dll, dll) && !strcmp(g_thunks[i].name, name)) {
            if (out_resolved) *out_resolved = g_thunks[i].resolved;
            return THUNK_BASE + (uint32_t)i * 4u;
        }
    }
    if (g_thunk_n >= MAX_THUNKS) return 0;
    int idx = g_thunk_n++;
    snprintf(g_thunks[idx].dll, sizeof g_thunks[idx].dll, "%s", dll ? dll : "");
    snprintf(g_thunks[idx].name, sizeof g_thunks[idx].name, "%s", name ? name : "");
    recomp_func_t fn = lookup_shim(dll, name);
    g_thunks[idx].fn = fn ? fn : w_unresolved_stub;
    g_thunks[idx].resolved = fn != NULL;
    if (out_resolved) *out_resolved = g_thunks[idx].resolved;
    return THUNK_BASE + (uint32_t)idx * 4u;
}

recomp_func_t recomp_lookup_import(uint32_t va) {
    if (va < THUNK_BASE) return NULL;
    uint32_t idx = (va - THUNK_BASE) >> 2;
    if (idx >= (uint32_t)g_thunk_n) return NULL;
    return g_thunks[idx].fn;
}

static uint16_t rd16(const uint8_t *p, uint32_t o) {
    return (uint16_t)p[o] | ((uint16_t)p[o + 1] << 8);
}
static uint32_t rd32(const uint8_t *p, uint32_t o) {
    return (uint32_t)p[o] | ((uint32_t)p[o + 1] << 8) | ((uint32_t)p[o + 2] << 16) | ((uint32_t)p[o + 3] << 24);
}

static uint32_t find_companion_export(const char *dll, const char *name) {
    for (uint32_t i = 0; i < recomp_companion_count; i++) {
        const recomp_companion_t *c = &recomp_companion_list[i];
        const char *bn = strrchr(c->name, '\\');
        bn = bn ? bn + 1 : c->name;
        if (!strcasecmp(bn, dll) || !strcasecmp(c->name, dll)) {
            for (uint32_t j = 0; j < c->export_count; j++) {
                if (!strcmp(c->exports[j].name, name)) return c->exports[j].va;
            }
        }
    }
    return 0;
}

typedef struct {
    uint32_t base;
    uint32_t span;
    uint32_t entry;
    int      bound;
    int      unresolved;
} pe_load_info_t;

static int map_pe_bytes(const uint8_t *raw, uint32_t raw_size, uint32_t target_base,
                        pe_load_info_t *out, int is_main) {
    if (!raw || raw_size < 0x40 || rd16(raw, 0) != 0x5A4Du) return 0;
    uint32_t lfanew = rd32(raw, 0x3Cu);
    if (lfanew + 24 > raw_size || rd32(raw, lfanew) != 0x00004550u) return 0;
    uint16_t nsec = rd16(raw, lfanew + 6);
    uint16_t opt_sz = rd16(raw, lfanew + 20);
    uint32_t opt_off = lfanew + 24;
    if (opt_off + 96 > raw_size || rd16(raw, opt_off) != 0x010Bu) return 0;
    uint32_t entry_rva = rd32(raw, opt_off + 16);
    uint32_t pref_base = rd32(raw, opt_off + 28);
    uint32_t img_size = rd32(raw, opt_off + 56);
    uint32_t hdr_size = rd32(raw, opt_off + 60);
    uint32_t base = target_base ? target_base : pref_base;

    wasm_map_guest_region(base, img_size);
    uint32_t copy_hdr = hdr_size < raw_size ? hdr_size : raw_size;
    memcpy(hle_ptr(base), raw, copy_hdr);

    uint32_t sec_off = opt_off + opt_sz;
    for (uint16_t i = 0; i < nsec; i++) {
        uint32_t so = sec_off + (uint32_t)i * 40u;
        if (so + 40 > raw_size) break;
        uint32_t va = rd32(raw, so + 12);
        uint32_t rsz = rd32(raw, so + 16);
        uint32_t rof = rd32(raw, so + 20);
        if (rsz && rof + rsz <= raw_size && va + rsz <= img_size) {
            memcpy(hle_ptr(base + va), raw + rof, rsz);
        }
    }

    /* Base relocations if mapped away from preferred ImageBase */
    uint32_t ndir = rd32(raw, opt_off + 92);
    if (base != pref_base && ndir > 5) {
        uint32_t rel_rva = rd32(raw, opt_off + 96 + 5 * 8);
        uint32_t rel_sz  = rd32(raw, opt_off + 96 + 5 * 8 + 4);
        int32_t delta = (int32_t)(base - pref_base);
        uint32_t cur = 0;
        while (cur + 8 <= rel_sz && rel_rva + cur + 8 <= img_size) {
            uint32_t blk_rva = MEM32(base + rel_rva + cur);
            uint32_t blk_sz  = MEM32(base + rel_rva + cur + 4);
            if (blk_sz < 8) break;
            uint32_t cnt = (blk_sz - 8) / 2;
            for (uint32_t k = 0; k < cnt; k++) {
                uint16_t e = MEM16(base + rel_rva + cur + 8 + k * 2);
                if ((e >> 12) == 3 /* IMAGE_REL_BASED_HIGHLOW */) {
                    uint32_t addr = base + blk_rva + (e & 0xFFFu);
                    MEM32(addr) = (uint32_t)((int32_t)MEM32(addr) + delta);
                }
            }
            cur += blk_sz;
        }
    }

    /* TLS index init */
    if (is_main && ndir > 9) {
        uint32_t tls_rva = rd32(raw, opt_off + 96 + 9 * 8);
        uint32_t tls_sz  = rd32(raw, opt_off + 96 + 9 * 8 + 4);
        if (tls_rva && tls_sz >= 16) {
            uint32_t idx_va = MEM32(base + tls_rva + 8);
            if (idx_va) MEM32(idx_va) = 0;
        }
    }

    /* Import Address Table binding */
    int bound = 0, unres = 0;
    if (ndir > 1) {
        uint32_t imp_rva = rd32(raw, opt_off + 96 + 8);
        uint32_t imp_sz  = rd32(raw, opt_off + 96 + 12);
        if (imp_rva && imp_sz) {
            for (uint32_t d = 0;; d += 20) {
                uint32_t desc = base + imp_rva + d;
                uint32_t oft = MEM32(desc);
                uint32_t name_rva = MEM32(desc + 12);
                uint32_t ft = MEM32(desc + 16);
                if (!name_rva || !ft) break;
                const char *dll = (const char *)hle_ptr(base + name_rva);
                uint32_t ilt = oft ? oft : ft;
                for (uint32_t slot = 0;; slot += 4) {
                    uint32_t th = MEM32(base + ilt + slot);
                    if (!th) break;
                    char symbuf[64];
                    const char *sym = symbuf;
                    if (th & 0x80000000u) snprintf(symbuf, sizeof symbuf, "#%u", th & 0xFFFFu);
                    else sym = (const char *)hle_ptr(base + th + 2);
                    uint32_t comp_va = find_companion_export(dll, sym);
                    if (comp_va) {
                        MEM32(base + ft + slot) = comp_va;
                        bound++;
                    } else {
                        int ok = 0;
                        uint32_t tv = register_import(dll, sym, &ok);
                        MEM32(base + ft + slot) = tv;
                        bound++;
                        if (!ok) {
                            unres++;
                            hle_logf(2, "[pe] unresolved import %s!%s\n", dll, sym);
                        }
                    }
                }
            }
        }
    }

    out->base = base;
    out->span = img_size;
    out->entry = base + entry_rva;
    out->bound = bound;
    out->unresolved = unres;
    hle_logf(2, "[pe] <%s> mapped at 0x%08X, span 0x%X (%d bound, %d unresolved)\n",
             is_main ? recomp_original_name : "companion", base, img_size, bound, unres);
    return 1;
}

/* ============================================================
 * Guest CRT Objects, Environment, VFS & Exit Chain
 * ============================================================ */
static uint32_t g_iob_va, g_pctype_va, g_ctype_va, g_errno_va, g_fmode_va, g_commode_va;
static uint32_t g_argc_va, g_argv_cell_va, g_environ_va, g_cmdline_va, g_wcmdline_va;
static uint32_t g_atexit_fns[128];
static int      g_atexit_n;
static uint32_t g_main_base = 0x00400000u;

static void init_guest_crt(void) {
    if (g_iob_va) return;
    g_iob_va = hle_crt_alloc(3 * 32);
    for (int i = 0; i < 3; i++) MEM32(g_iob_va + (uint32_t)i * 32u + 16u) = (uint32_t)i;
    g_ctype_va = hle_crt_alloc(257 * 2);
    uint16_t *ct = (uint16_t *)hle_ptr(g_ctype_va);
    for (int c = 0; c < 256; c++) {
        uint16_t f = 0;
        if (isupper(c)) f |= 0x0001u;
        if (islower(c)) f |= 0x0002u;
        if (isdigit(c)) f |= 0x0004u;
        if (isspace(c)) f |= 0x0008u;
        if (ispunct(c)) f |= 0x0010u;
        if (iscntrl(c)) f |= 0x0020u;
        if (c == ' ' || c == '\t') f |= 0x0040u;
        if (isxdigit(c)) f |= 0x0080u;
        if (isalpha(c)) f |= 0x0100u;
        ct[c + 1] = f;
    }
    g_pctype_va = g_ctype_va + 2;
    g_errno_va = hle_crt_alloc(4);
    g_fmode_va = hle_crt_alloc(4);
    g_commode_va = hle_crt_alloc(4);
    char cl[256];
    snprintf(cl, sizeof cl, "C:\\%s", recomp_original_name);
    g_cmdline_va = hle_guest_strdup(cl);
    g_wcmdline_va = hle_crt_alloc((strlen(cl) + 1) * 2);
    for (size_t i = 0; i <= strlen(cl); i++) MEM16(g_wcmdline_va + (uint32_t)i * 2u) = (uint8_t)cl[i];
    uint32_t argv_arr = hle_crt_alloc(8);
    MEM32(argv_arr) = g_cmdline_va;
    g_argc_va = hle_crt_alloc(4); MEM32(g_argc_va) = 1;
    g_argv_cell_va = hle_crt_alloc(4); MEM32(g_argv_cell_va) = argv_arr;
    uint32_t env_arr = hle_crt_alloc(4);
    g_environ_va = hle_crt_alloc(4); MEM32(g_environ_va) = env_arr;
}

void hle_exit(int code) {
    if (!g_exited) {
        g_exited = 1;
        g_exit_code = code;
        while (g_atexit_n > 0) {
            uint32_t fn = g_atexit_fns[--g_atexit_n];
            if (fn) hle_call_guest(fn, 0, NULL);
        }
        hle_logf(2, "[host] process exited %d\n", code);
    }
    wasm_host_exit(code);
    __builtin_unreachable();
}

/* ============================================================
 * Kernel32 + MSVCRT/UCRT Shims
 * ============================================================ */
static uint32_t g_tls[64];
static int      g_tls_used[64];

static void w_HeapAlloc(void) {
    uint32_t flags = A32(1), sz = A32(2);
    uint32_t va = hle_guest_alloc(sz ? sz : 1);
    if (va && (flags & 8u)) memset(hle_ptr(va), 0, sz ? sz : 1);
    RETS(3, va);
}
static void w_HeapReAlloc(void) {
    uint32_t ptr = A32(2), sz = A32(3);
    if (!ptr) { RETS(4, hle_guest_alloc(sz ? sz : 1)); }
    uint32_t np = hle_guest_alloc(sz ? sz : 1);
    if (np) memcpy(hle_ptr(np), hle_ptr(ptr), sz);
    RETS(4, np);
}
static void w_HeapFree(void) { RETS(3, 1); }
static void w_HeapCreate(void) { RETS(3, 0xABCD0001u); }
static void w_HeapDestroy(void) { RETS(1, 1); }
static void w_GetProcessHeap(void) { RETS(0, 0xABCD0001u); }
static void w_VirtualAlloc(void) {
    uint32_t addr = A32(0), sz = A32(1);
    if (addr) { wasm_map_guest_region(addr, sz); RETS(4, addr); }
    RETS(4, hle_guest_alloc(sz ? sz : 4096));
}
static void w_VirtualFree(void) { RETS(3, 1); }
static void w_VirtualProtect(void) { if (A32(3)) MEM32(A32(3)) = 0x40; RETS(4, 1); }
static void w_VirtualQuery(void) {
    uint32_t a = A32(0), mbi = A32(1);
    if (!mbi) RETS(3, 0);
    MEM32(mbi + 0) = a & ~0xFFFu;
    MEM32(mbi + 4) = (a >= g_main_base && a < g_main_base + 0x100000u) ? g_main_base : (a & ~0xFFFFu);
    MEM32(mbi + 8) = 0x40;
    MEM32(mbi + 12) = 0x10000u;
    MEM32(mbi + 16) = 0x1000u;
    MEM32(mbi + 20) = 0x40;
    MEM32(mbi + 24) = (a >= g_main_base && a < g_main_base + 0x100000u) ? 0x1000000u : 0x20000u;
    RETS(3, 28);
}
static void w_GlobalAlloc(void) { RETS(2, hle_guest_alloc(A32(1) ? A32(1) : 16)); }
static void w_GlobalLock(void) { RETS(1, A32(0)); }
static void w_GlobalUnlock(void) { RETS(1, 1); }
static void w_GlobalFree(void) { RETS(1, 0); }
static void w_LocalAlloc(void) { RETS(2, hle_guest_alloc(A32(1) ? A32(1) : 16)); }
static void w_LocalFree(void) { RETS(1, 0); }
static void w_GetModuleHandleA(void) { RETS(1, A32(0) ? 0 : g_main_base); }
static void w_GetModuleHandleW(void) { RETS(1, A32(0) ? 0 : g_main_base); }
static void w_GetModuleFileNameA(void) {
    char *dst = (char *)APTR(1); uint32_t cap = A32(2);
    char tmp[128]; snprintf(tmp, sizeof tmp, "C:\\%s", recomp_original_name);
    if (dst && cap) { strncpy(dst, tmp, cap - 1); dst[cap - 1] = 0; }
    RETS(3, (uint32_t)strlen(tmp));
}
static void w_GetModuleFileNameW(void) {
    uint32_t dst = A32(1), cap = A32(2);
    char tmp[128]; snprintf(tmp, sizeof tmp, "C:\\%s", recomp_original_name);
    uint32_t n = (uint32_t)strlen(tmp);
    if (dst && cap) {
        uint32_t m = n < cap - 1 ? n : cap - 1;
        for (uint32_t i = 0; i <= m; i++) MEM16(dst + i * 2u) = (i == m) ? 0 : (uint8_t)tmp[i];
    }
    RETS(3, n);
}
static void w_LoadLibraryA(void) { RETS(1, 0x7D000000u); }
static void w_FreeLibrary(void) { RETS(1, 1); }
static void w_GetProcAddress(void) {
    const char *nm = ASTR(1);
    int ok = 0;
    uint32_t tv = register_import("kernel32.dll", nm ? nm : "", &ok);
    RETS(2, ok ? tv : 0u);
}
static void w_GetCommandLineA(void) { init_guest_crt(); RETS(0, g_cmdline_va); }
static void w_GetCommandLineW(void) { init_guest_crt(); RETS(0, g_wcmdline_va); }
static void w_GetEnvironmentStrings(void) { RETS(0, hle_guest_alloc(4)); }
static void w_GetEnvironmentVariableA(void) { g_last_error = 203; RETS(3, 0); }
static void w_GetStartupInfoA(void) { if (A32(0)) { memset(APTR(0), 0, 68); MEM32(A32(0)) = 68; } RETSV(1); }
static void w_GetStartupInfoW(void) { if (A32(0)) { memset(APTR(0), 0, 68); MEM32(A32(0)) = 68; } RETSV(1); }
static void w_GetStdHandle(void) {
    uint32_t h = A32(0);
    if (h == (uint32_t)-10) RETS(1, 0x10u);
    if (h == (uint32_t)-11) RETS(1, 0x11u);
    if (h == (uint32_t)-12) RETS(1, 0x12u);
    RETS(1, 0xFFFFFFFFu);
}
static void w_GetFileType(void) { uint32_t h = A32(0); RETS(1, (h >= 0x10u && h <= 0x12u) ? 2u : 1u); }
static void w_GetLastError(void) { RETS(0, g_last_error); }
static void w_SetLastError(void) { g_last_error = A32(0); RETSV(1); }
static void w_GetTickCount(void) { RETS(0, (uint32_t)wasm_host_now_ms()); }
static void w_GetLocalTime(void) {
    uint32_t st = A32(0);
    if (st) { memset(hle_ptr(st), 0, 16); MEM16(st) = 2026; MEM16(st + 2) = 11; MEM16(st + 6) = 19; }
    RETSV(1);
}
static void w_SystemTimeToFileTime(void) { if (A32(1)) memset(APTR(1), 0, 8); RETS(2, 1); }
static void w_CompareFileTime(void) { RETS(2, 0); }
static void w_GetVersion(void) { RETS(0, 0x0A280105u); }
static void w_GetVersionExA(void) {
    uint32_t p = A32(0);
    if (p) { memset(hle_ptr(p), 0, 148); MEM32(p) = 148; MEM32(p + 4) = 5; MEM32(p + 8) = 1; MEM32(p + 12) = 2600; MEM32(p + 16) = 2; }
    RETS(1, 1);
}
static void w_GetCurrentProcess(void) { RETS(0, 0xFFFFFFFFu); }
static void w_GetCurrentProcessId(void) { RETS(0, 1001); }
static void w_GetCurrentThreadId(void) { RETS(0, 1); }
static void w_ExitProcess(void) { hle_exit((int)A32(0)); }
static void w_TerminateProcess(void) { hle_exit((int)A32(1)); }
static void w_Sleep(void) { RETSV(1); }
static void w_TlsAlloc(void) {
    for (int i = 0; i < 64; i++) if (!g_tls_used[i]) { g_tls_used[i] = 1; g_tls[i] = 0; RETS(0, (uint32_t)i); }
    RETS(0, 0xFFFFFFFFu);
}
static void w_TlsFree(void) { uint32_t i = A32(0); if (i < 64) g_tls_used[i] = 0; RETS(1, 1); }
static void w_TlsGetValue(void) { uint32_t i = A32(0); g_last_error = 0; RETS(1, i < 64 ? g_tls[i] : 0u); }
static void w_TlsSetValue(void) { uint32_t i = A32(0); if (i < 64) g_tls[i] = A32(1); RETS(2, 1); }
static void w_InterlockedIncrement(void) { uint32_t p = A32(0); uint32_t v = p ? (MEM32(p) + 1u) : 1u; if (p) MEM32(p) = v; RETS(1, v); }
static void w_InterlockedDecrement(void) { uint32_t p = A32(0); uint32_t v = p ? (MEM32(p) - 1u) : 0u; if (p) MEM32(p) = v; RETS(1, v); }
static void w_MulDiv(void) { int64_t r = ((int64_t)(int32_t)A32(0) * (int32_t)A32(1)); int32_t d = (int32_t)A32(2); RETS(3, d ? (uint32_t)(r / d) : 0xFFFFFFFFu); }
static void w_MultiByteToWideChar(void) {
    const char *src = ASTR(2); int srcl = (int32_t)A32(3); uint32_t dst = A32(4); int dstl = (int32_t)A32(5);
    if (!src) RETS(6, 0);
    int n = (srcl < 0) ? ((int)strlen(src) + 1) : srcl;
    if (dst && dstl > 0) {
        int m = n < dstl ? n : dstl;
        for (int i = 0; i < m; i++) MEM16(dst + (uint32_t)i * 2u) = (uint8_t)src[i];
    }
    RETS(6, (uint32_t)n);
}
static void w_WideCharToMultiByte(void) {
    uint32_t src = A32(2); int srcl = (int32_t)A32(3); char *dst = (char *)APTR(4); int dstl = (int32_t)A32(5);
    if (!src) RETS(8, 0);
    int n = 0;
    if (srcl < 0) { while (MEM16(src + (uint32_t)n * 2u)) n++; n++; } else n = srcl;
    if (dst && dstl > 0) {
        int m = n < dstl ? n : dstl;
        for (int i = 0; i < m; i++) dst[i] = (char)(MEM16(src + (uint32_t)i * 2u) & 0xFFu);
    }
    RETS(8, (uint32_t)n);
}
static void w_GetACP(void) { RETS(0, 1252); }
static void w_GetCPInfo(void) {
    uint32_t p = A32(1);
    if (p) { memset(hle_ptr(p), 0, 20); MEM32(p) = 1; MEM8(p + 4) = '?'; }
    RETS(2, 1);
}
static void w_GetStringTypeW(void) {
    int n = (int32_t)A32(3); uint32_t dst = A32(4);
    for (int i = 0; i < n; i++) MEM16(dst + (uint32_t)i * 2u) = 0x0100;
    RETS(5, 1);
}
static void w_CreateFileA(void) { g_last_error = 2; RETS(7, 0xFFFFFFFFu); }
static void w_WriteFile(void) {
    uint32_t h = A32(0), len = A32(2), wr = A32(3);
    const char *buf = (const char *)APTR(1);
    if ((h == 0x11u || h == 0x12u) && buf) wasm_host_log(h == 0x12u ? 2 : 1, buf, (int)len);
    if (wr) MEM32(wr) = len;
    RETS(5, 1);
}
static void w_ReadFile(void) { if (A32(3)) MEM32(A32(3)) = 0; RETS(5, 1); }
static void w_FindFirstFileA(void) { g_last_error = 2; RETS(2, 0xFFFFFFFFu); }
static void w_RaiseException(void) { hle_exit(3); }
static void w_OutputDebugStringA(void) { const char *s = ASTR(0); if (s) hle_logf(2, "%s", s); RETSV(1); }

/* Stdcall no-op/simple return helpers */
static void k_ret0_0(void) { RETS(0, 0); }
static void k_ret0_1(void) { RETS(1, 0); }
static void k_ret1_1(void) { RETS(1, 1); }
static void k_ret0_2(void) { RETS(2, 0); }
static void k_ret1_2(void) { RETS(2, 1); }
static void k_ret1_3(void) { RETS(3, 1); }
static void k_ret0_4(void) { RETS(4, 0); }
static void k_ret1_4(void) { RETS(4, 1); }
static void k_ret0_5(void) { RETS(5, 0); }
static void k_ret1_5(void) { RETS(5, 1); }
static void k_ret0_6(void) { RETS(6, 0); }
static void k_ret1_6(void) { RETS(6, 1); }
static void k_retv_1(void) { RETSV(1); }
static void k_retv_4(void) { RETSV(4); }

/* MSVCRT / UCRT shims (cdecl) */
static void c_initterm(void) {
    uint32_t p = A32(0), e = A32(1);
    for (; p < e; p += 4) {
        uint32_t fn = MEM32(p);
        if (fn) hle_call_guest(fn, 0, NULL);
    }
    RETCV();
}
static void c_initterm_e(void) {
    uint32_t p = A32(0), e = A32(1);
    for (; p < e; p += 4) {
        uint32_t fn = MEM32(p);
        if (fn) { int r = hle_call_guest(fn, 0, NULL); if (r) RETC((uint32_t)r); }
    }
    RETC(0);
}
static void c_p_argc(void)   { init_guest_crt(); RETC(g_argc_va); }
static void c_p_argv(void)   { init_guest_crt(); RETC(g_argv_cell_va); }
static void c_p_acmdln(void) { init_guest_crt(); static uint32_t cell; if (!cell) { cell = hle_crt_alloc(4); MEM32(cell) = g_cmdline_va; } RETC(cell); }
static void c_get_narrow_winmain_command_line(void) { init_guest_crt(); RETC(g_cmdline_va); }
static void c_p_environ(void){ init_guest_crt(); RETC(g_environ_va); }
static void c_p_commode(void){ init_guest_crt(); RETC(g_commode_va); }
static void c_p_fmode(void)  { init_guest_crt(); RETC(g_fmode_va); }
static void c_errno(void)    { init_guest_crt(); RETC(g_errno_va); }
static void c_acrt_iob_func(void) { init_guest_crt(); uint32_t i = A32(0); RETC(i < 3 ? (g_iob_va + i * 32u) : 0u); }
static void c_p_iob(void)    { init_guest_crt(); RETC(g_iob_va); }
static void c_p_pctype(void) { init_guest_crt(); RETC(g_pctype_va); }
static void c_initialize_onexit_table(void) { uint32_t t = A32(0); if (t) memset(hle_ptr(t), 0, 12); RETC(0); }
static void c_register_onexit_function(void) { if (A32(1) && g_atexit_n < 128) g_atexit_fns[g_atexit_n++] = A32(1); RETC(0); }
static void c_crt_atexit(void) { uint32_t fn = A32(0); if (fn && g_atexit_n < 128) g_atexit_fns[g_atexit_n++] = fn; RETC(0); }
static void c_exit(void) { hle_exit((int)A32(0)); }
static void c_cexit(void) { while (g_atexit_n > 0) { uint32_t fn = g_atexit_fns[--g_atexit_n]; if (fn) hle_call_guest(fn, 0, NULL); } RETCV(); }
static void c_malloc(void)  { RETC(hle_guest_alloc(A32(0) ? A32(0) : 1)); }
static void c_calloc(void)  { size_t n = (size_t)A32(0) * (size_t)A32(1); RETC(hle_guest_alloc(n ? n : 1)); }
static void c_realloc(void) { uint32_t p = A32(0), sz = A32(1); if (!p) RETC(hle_guest_alloc(sz ? sz : 1)); uint32_t np = hle_guest_alloc(sz ? sz : 1); if (np) memcpy(hle_ptr(np), hle_ptr(p), sz); RETC(np); }
static void c_free(void)    { RETCV(); }
static void c_memcpy(void)  { memcpy(APTR(0), APTR(1), A32(2)); RETC(A32(0)); }
static void c_memmove(void) { memmove(APTR(0), APTR(1), A32(2)); RETC(A32(0)); }
static void c_memset(void)  { memset(APTR(0), (int)A32(1), A32(2)); RETC(A32(0)); }
static void c_memcmp(void)  { RETC((uint32_t)memcmp(APTR(0), APTR(1), A32(2))); }
static void c_strlen(void)  { RETC((uint32_t)strlen(ASTR(0))); }
static void c_strcpy(void)  { strcpy((char *)APTR(0), ASTR(1)); RETC(A32(0)); }
static void c_strncpy(void) { strncpy((char *)APTR(0), ASTR(1), A32(2)); RETC(A32(0)); }
static void c_strcat(void)  { strcat((char *)APTR(0), ASTR(1)); RETC(A32(0)); }
static void c_strcmp(void)  { RETC((uint32_t)strcmp(ASTR(0), ASTR(1))); }
static void c_strncmp(void) { RETC((uint32_t)strncmp(ASTR(0), ASTR(1), A32(2))); }
static void c_strchr(void)  { char *p = strchr(ASTR(0), (int)A32(1)); RETC(p ? (A32(0) + (uint32_t)(p - ASTR(0))) : 0u); }
static void c_strrchr(void) { char *p = strrchr(ASTR(0), (int)A32(1)); RETC(p ? (A32(0) + (uint32_t)(p - ASTR(0))) : 0u); }
static void c_strcspn(void) { RETC((uint32_t)strcspn(ASTR(0), ASTR(1))); }
static void c_strspn(void)  { RETC((uint32_t)strspn(ASTR(0), ASTR(1))); }
static void c_wcslen(void)  { uint32_t w = A32(0), n = 0; while (w && MEM16(w + n * 2u)) n++; RETC(n); }
static void c_atoi(void)    { RETC((uint32_t)atoi(ASTR(0))); }
static void c_atof(void)    { double d = atof(ASTR(0)); g_st[++g_fp_top & 7] = d; RETCV(); }
static void c_isspace(void) { RETC((uint32_t)isspace((int)A32(0))); }
static void c_tolower(void) { RETC((uint32_t)tolower((int)A32(0))); }
static void c_toupper(void) { RETC((uint32_t)toupper((int)A32(0))); }
static void c_sin(void)     { double d; memcpy(&d, hle_ptr(g_esp + 4u), 8); g_st[++g_fp_top & 7] = sin(d); RETCV(); }
static void c_cos(void)     { double d; memcpy(&d, hle_ptr(g_esp + 4u), 8); g_st[++g_fp_top & 7] = cos(d); RETCV(); }
static void c_atan2(void)   { double y, x; memcpy(&y, hle_ptr(g_esp + 4u), 8); memcpy(&x, hle_ptr(g_esp + 12u), 8); g_st[++g_fp_top & 7] = atan2(y, x); RETCV(); }

static int guest_format(char *out, size_t cap, const char *fmt, uint32_t va_args) {
    size_t pos = 0;
    uint32_t ap = va_args;
    for (const char *p = fmt; p && *p; p++) {
        if (*p != '%') { if (pos + 1 < cap) out[pos] = *p; pos++; continue; }
        p++;
        int zero = 0, width = 0, prec = -1;
        while (*p == '-' || *p == '0' || *p == '+' || *p == ' ') { if (*p == '0') zero = 1; p++; }
        while (isdigit((unsigned char)*p)) { width = width * 10 + (*p - '0'); p++; }
        if (*p == '.') { p++; prec = 0; while (isdigit((unsigned char)*p)) { prec = prec * 10 + (*p - '0'); p++; } }
        while (*p == 'l') p++;
        char conv = *p; if (!conv) break;
        char tmp[128]; tmp[0] = 0;
        if (conv == '%') strcpy(tmp, "%");
        else if (conv == 'c') { tmp[0] = (char)MEM32(ap); tmp[1] = 0; ap += 4; }
        else if (conv == 's') { uint32_t s = MEM32(ap); ap += 4; const char *cs = s ? (const char *)hle_ptr(s) : "(null)"; int n = snprintf(out + (pos < cap ? pos : cap - 1), pos < cap ? cap - pos : 0, "%s", cs); pos += (size_t)n; continue; }
        else if (conv == 'd' || conv == 'i') { snprintf(tmp, sizeof tmp, "%*d", width, (int32_t)MEM32(ap)); ap += 4; (void)zero; }
        else if (conv == 'u') { snprintf(tmp, sizeof tmp, "%*u", width, MEM32(ap)); ap += 4; }
        else if (conv == 'x' || conv == 'X' || conv == 'p') { snprintf(tmp, sizeof tmp, zero ? "%0*x" : "%*x", width, MEM32(ap)); ap += 4; }
        else if (conv == 'f' || conv == 'g') { double d; memcpy(&d, hle_ptr(ap), 8); ap += 8; snprintf(tmp, sizeof tmp, prec >= 0 ? "%.3f" : "%.2f", d); }
        size_t tl = strlen(tmp);
        for (size_t i = 0; i < tl; i++) { if (pos + 1 < cap) out[pos] = tmp[i]; pos++; }
    }
    if (cap) out[pos < cap ? pos : cap - 1] = 0;
    return (int)pos;
}

static void u_stdio_common_vsprintf(void) {
    uint32_t buf = A32(2), bsz = A32(3), args = A32(6);
    const char *fmt = ASTR(4);
    char tmp[2048];
    int n = guest_format(tmp, sizeof tmp, fmt ? fmt : "", args);
    if (buf) memcpy(hle_ptr(buf), tmp, (bsz && (uint32_t)n >= bsz) ? bsz : (size_t)n + 1);
    RETC((uint32_t)n);
}
static void u_stdio_common_vfprintf(void) {
    uint32_t st = A32(2), args = A32(5); const char *fmt = ASTR(3);
    char tmp[2048];
    int n = guest_format(tmp, sizeof tmp, fmt ? fmt : "", args);
    init_guest_crt();
    int fd = (st >= g_iob_va && st < g_iob_va + 96) ? (int)MEM32(st + 16) : 1;
    wasm_host_log(fd == 2 ? 2 : 1, tmp, n < (int)sizeof tmp ? n : (int)sizeof tmp - 1);
    RETC((uint32_t)n);
}
static void c_printf(void)  { char tmp[2048]; int n = guest_format(tmp, sizeof tmp, ASTR(0) ? ASTR(0) : "", g_esp + 8); wasm_host_log(1, tmp, n); RETC((uint32_t)n); }
static void c_sprintf(void) { char *dst = (char *)APTR(0); char tmp[2048]; int n = guest_format(tmp, sizeof tmp, ASTR(1) ? ASTR(1) : "", g_esp + 12); if (dst) memcpy(dst, tmp, (size_t)n + 1); RETC((uint32_t)n); }
static void u_wvsprintfA(void) { char *dst = (char *)APTR(0); char tmp[2048]; int n = guest_format(tmp, sizeof tmp, ASTR(1) ? ASTR(1) : "", A32(2)); if (dst) memcpy(dst, tmp, (size_t)n + 1); RETS(3, (uint32_t)n); }
static void c_puts(void) { const char *s = ASTR(0); if (s) puts(s); RETC(0); }
static void c_putchar(void) { putchar((int)A32(0)); RETC(A32(0)); }
static void c_ret0(void) { RETC(0); }
static void c_ret_neg1(void) { RETC(0xFFFFFFFFu); }

static const struct { const char *name; recomp_func_t fn; } g_core_syms[] = {
    /* Kernel32 */
    { "CloseHandle",                  k_ret1_1 },
    { "CompareFileTime",              w_CompareFileTime },
    { "CreateDirectoryA",             k_ret1_2 },
    { "CreateFileA",                  w_CreateFileA },
    { "CreateProcessA",               k_ret0_6 },
    { "DeleteCriticalSection",        k_retv_1 },
    { "DeleteFileA",                  k_ret1_1 },
    { "EnterCriticalSection",         k_retv_1 },
    { "ExitProcess",                  w_ExitProcess },
    { "FindClose",                    k_ret1_1 },
    { "FindFirstFileA",               w_FindFirstFileA },
    { "FindNextFileA",                k_ret0_2 },
    { "FlushFileBuffers",             k_ret1_1 },
    { "FormatMessageA",               k_ret0_6 },
    { "FreeEnvironmentStringsA",      k_ret1_1 },
    { "FreeEnvironmentStringsW",      k_ret1_1 },
    { "FreeLibrary",                  w_FreeLibrary },
    { "GetACP",                       w_GetACP },
    { "GetOEMCP",                     w_GetACP },
    { "GetCPInfo",                    w_GetCPInfo },
    { "GetCommandLineA",              w_GetCommandLineA },
    { "GetCommandLineW",              w_GetCommandLineW },
    { "GetCurrentProcess",            w_GetCurrentProcess },
    { "GetCurrentProcessId",          w_GetCurrentProcessId },
    { "GetCurrentThreadId",           w_GetCurrentThreadId },
    { "GetEnvironmentStrings",        w_GetEnvironmentStrings },
    { "GetEnvironmentStringsW",       w_GetEnvironmentStrings },
    { "GetEnvironmentVariableA",      w_GetEnvironmentVariableA },
    { "GetFileTime",                  k_ret0_4 },
    { "GetFileType",                  w_GetFileType },
    { "GetLastError",                 w_GetLastError },
    { "GetLocalTime",                 w_GetLocalTime },
    { "GetLocaleInfoA",               k_ret0_4 },
    { "GetLocaleInfoW",               k_ret0_4 },
    { "GetModuleFileNameA",           w_GetModuleFileNameA },
    { "GetModuleFileNameW",           w_GetModuleFileNameW },
    { "GetModuleHandleA",             w_GetModuleHandleA },
    { "GetModuleHandleW",             w_GetModuleHandleW },
    { "GetProcAddress",               w_GetProcAddress },
    { "GetProcessHeap",               w_GetProcessHeap },
    { "GetStartupInfoA",              w_GetStartupInfoA },
    { "GetStartupInfoW",              w_GetStartupInfoW },
    { "GetStdHandle",                 w_GetStdHandle },
    { "GetStringTypeA",               k_ret1_5 },
    { "GetStringTypeW",               w_GetStringTypeW },
    { "GetSystemTime",                w_GetLocalTime },
    { "GetTickCount",                 w_GetTickCount },
    { "GetVersion",                   w_GetVersion },
    { "GetVersionExA",                w_GetVersionExA },
    { "GlobalAlloc",                  w_GlobalAlloc },
    { "GlobalFree",                   w_GlobalFree },
    { "GlobalLock",                   w_GlobalLock },
    { "GlobalUnlock",                 w_GlobalUnlock },
    { "HeapAlloc",                    w_HeapAlloc },
    { "HeapCreate",                   w_HeapCreate },
    { "HeapDestroy",                  w_HeapDestroy },
    { "HeapFree",                     w_HeapFree },
    { "HeapReAlloc",                  w_HeapReAlloc },
    { "InitializeCriticalSection",    k_retv_1 },
    { "InterlockedDecrement",         w_InterlockedDecrement },
    { "InterlockedIncrement",         w_InterlockedIncrement },
    { "IsBadWritePtr",                k_ret0_2 },
    { "IsDBCSLeadByte",               k_ret0_1 },
    { "LCMapStringA",                 k_ret0_6 },
    { "LCMapStringW",                 k_ret0_6 },
    { "LeaveCriticalSection",         k_retv_1 },
    { "LoadLibraryA",                 w_LoadLibraryA },
    { "LocalAlloc",                   w_LocalAlloc },
    { "LocalFree",                    w_LocalFree },
    { "MoveFileExA",                  k_ret1_3 },
    { "MulDiv",                       w_MulDiv },
    { "MultiByteToWideChar",          w_MultiByteToWideChar },
    { "OutputDebugStringA",           w_OutputDebugStringA },
    { "RaiseException",               w_RaiseException },
    { "ReadFile",                     w_ReadFile },
    { "RemoveDirectoryA",             k_ret1_1 },
    { "RtlUnwind",                    k_retv_4 },
    { "SearchPathA",                  k_ret0_6 },
    { "SetConsoleCtrlHandler",        k_ret1_2 },
    { "SetEndOfFile",                 k_ret1_1 },
    { "SetFilePointer",               k_ret0_4 },
    { "SetHandleCount",               k_ret0_1 },
    { "SetLastError",                 w_SetLastError },
    { "SetStdHandle",                 k_ret1_2 },
    { "SetUnhandledExceptionFilter",  k_ret0_1 },
    { "Sleep",                        w_Sleep },
    { "SystemTimeToFileTime",         w_SystemTimeToFileTime },
    { "TerminateProcess",             w_TerminateProcess },
    { "TlsAlloc",                     w_TlsAlloc },
    { "TlsFree",                      w_TlsFree },
    { "TlsGetValue",                  w_TlsGetValue },
    { "TlsSetValue",                  w_TlsSetValue },
    { "UnhandledExceptionFilter",     k_ret0_1 },
    { "VirtualAlloc",                 w_VirtualAlloc },
    { "VirtualFree",                  w_VirtualFree },
    { "VirtualProtect",               w_VirtualProtect },
    { "VirtualQuery",                 w_VirtualQuery },
    { "WaitForSingleObject",          k_ret0_2 },
    { "WideCharToMultiByte",          w_WideCharToMultiByte },
    { "WriteFile",                    w_WriteFile },
    /* Advapi32 / Shell32 / WinInet */
    { "RegOpenKeyExA",                k_ret1_5 },
    { "RegQueryValueExA",             k_ret1_6 },
    { "RegCloseKey",                  k_ret0_1 },
    { "DragQueryFileA",               k_ret0_4 },
    { "InternetOpenA",                k_ret1_5 },
    { "InternetOpenUrlA",             k_ret0_6 },
    { "InternetReadFile",             k_ret0_4 },
    { "InternetSetFilePointer",       k_ret0_5 },
    { "InternetCloseHandle",          k_ret1_1 },
    { "wvsprintfA",                   u_wvsprintfA },
    /* MSVCRT / UCRT */
    { "_initterm",                    c_initterm },
    { "_initterm_e",                  c_initterm_e },
    { "__p___argc",                   c_p_argc },
    { "__p___argv",                   c_p_argv },
    { "__p__acmdln",                  c_p_acmdln },
    { "_get_narrow_winmain_command_line", c_get_narrow_winmain_command_line },
    { "__p__environ",                 c_p_environ },
    { "__p__commode",                 c_p_commode },
    { "__p__fmode",                   c_p_fmode },
    { "__p__pctype",                  c_p_pctype },
    { "__p__iob",                     c_p_iob },
    { "__iob_func",                   c_p_iob },
    { "__acrt_iob_func",              c_acrt_iob_func },
    { "_errno",                       c_errno },
    { "_initialize_onexit_table",     c_initialize_onexit_table },
    { "_register_onexit_function",    c_register_onexit_function },
    { "_register_thread_local_exe_atexit_callback", c_ret0 },
    { "_configure_narrow_argv",       c_ret0 },
    { "_initialize_narrow_environment", c_ret0 },
    { "_set_app_type",                c_ret0 },
    { "__set_app_type",               c_ret0 },
    { "_set_new_mode",                c_ret0 },
    { "_configthreadlocale",          c_ret0 },
    { "__setusermatherr",             c_ret0 },
    { "_set_invalid_parameter_handler", c_ret0 },
    { "_seh_filter_exe",              c_ret0 },
    { "_crt_atexit",                  c_crt_atexit },
    { "atexit",                       c_crt_atexit },
    { "_onexit",                      c_crt_atexit },
    { "exit",                         c_exit },
    { "_exit",                        c_exit },
    { "_cexit",                       c_cexit },
    { "_c_exit",                      c_cexit },
    { "abort",                        w_RaiseException },
    { "_assert",                      w_RaiseException },
    { "signal",                       c_ret0 },
    { "getenv",                       c_ret0 },
    { "malloc",                       c_malloc },
    { "calloc",                       c_calloc },
    { "realloc",                      c_realloc },
    { "free",                         c_free },
    { "memcpy",                       c_memcpy },
    { "memmove",                      c_memmove },
    { "memset",                       c_memset },
    { "memcmp",                       c_memcmp },
    { "strlen",                       c_strlen },
    { "strcpy",                       c_strcpy },
    { "strncpy",                      c_strncpy },
    { "strcat",                       c_strcat },
    { "strcmp",                       c_strcmp },
    { "strncmp",                      c_strncmp },
    { "strchr",                       c_strchr },
    { "strrchr",                      c_strrchr },
    { "strcspn",                      c_strcspn },
    { "strspn",                       c_strspn },
    { "wcslen",                       c_wcslen },
    { "atoi",                         c_atoi },
    { "atof",                         c_atof },
    { "isspace",                      c_isspace },
    { "tolower",                      c_tolower },
    { "toupper",                      c_toupper },
    { "sin",                          c_sin },
    { "cos",                          c_cos },
    { "atan2",                        c_atan2 },
    { "_fdopen",                      c_ret0 },
    { "_fileno",                      c_ret_neg1 },
    { "_open_osfhandle",              c_ret_neg1 },
    { "_setmode",                     c_ret0 },
    { "fopen",                        c_ret0 },
    { "fclose",                       c_ret0 },
    { "fflush",                       c_ret0 },
    { "fgets",                        c_ret0 },
    { "fread",                        c_ret0 },
    { "fwrite",                       c_ret0 },
    { "setvbuf",                      c_ret0 },
    { "__stdio_common_vsprintf",      u_stdio_common_vsprintf },
    { "__stdio_common_vfprintf",      u_stdio_common_vfprintf },
    { "__stdio_common_vsscanf",       c_ret_neg1 },
    { "__stdio_common_vfscanf",       c_ret_neg1 },
    { "printf",                       c_printf },
    { "sprintf",                      c_sprintf },
    { "puts",                         c_puts },
    { "putchar",                      c_putchar },
    { "getchar",                      c_ret_neg1 },
    { NULL, NULL }
};

/* Keep extra arity helpers for table completeness */
static recomp_func_t lookup_shim(const char *dll, const char *name) {
    if (!name) return NULL;
    recomp_func_t ui = wasm_user32_gdi_lookup(dll, name);
    if (ui) return ui;
    for (int i = 0; g_core_syms[i].name; i++) {
        if (!strcmp(g_core_syms[i].name, name)) return g_core_syms[i].fn;
    }
    return NULL;
}

/* Note: ensure arity helpers used inside g_core_syms exist */
__attribute__((unused)) static void _arity_check(void) {}

/* ============================================================
 * Entry Point & WebAssembly Lifecycle
 * ============================================================ */
static pe_load_info_t g_main_pe;

int wasm_init(void) {
    wasm_ui_init(recomp_original_name);
    wasm_map_guest_region(GUEST_TEB_BASE, 0x10000u);
    g_fs_base = GUEST_TEB_BASE;
    MEM32(GUEST_TEB_BASE + 0x04u) = GUEST_STACK_BASE + GUEST_STACK_SIZE;
    MEM32(GUEST_TEB_BASE + 0x08u) = GUEST_STACK_BASE;
    MEM32(GUEST_TEB_BASE + 0x18u) = GUEST_TEB_BASE;
    MEM32(GUEST_TEB_BASE + 0x2Cu) = hle_crt_alloc(128);

    wasm_map_guest_region(GUEST_STACK_BASE, GUEST_STACK_SIZE);
    g_esp = GUEST_STACK_BASE + GUEST_STACK_SIZE - 0x1000u;
    init_guest_crt();

    /* Map any companion DLLs first so main image IAT binds cleanly */
    for (uint32_t i = 0; i < recomp_companion_count; i++) {
        const recomp_companion_t *c = &recomp_companion_list[i];
        pe_load_info_t ci = {0};
        map_pe_bytes(c->image_data, c->image_size, c->image_base, &ci, 0);
        if (ci.entry) {
            uint32_t args[3] = { ci.base, 1u /* DLL_PROCESS_ATTACH */, 0u };
            hle_call_guest(ci.entry, 3, args);
        }
    }

    if (!map_pe_bytes(recomp_embedded_image, (uint32_t)recomp_original_image_size, 0, &g_main_pe, 1)) {
        hle_logf(2, "[pe] failed to map embedded image\n");
        return 2;
    }
    g_main_base = g_main_pe.base;

    int total_bound = g_main_pe.bound;
    int total_unres = g_main_pe.unresolved;
    hle_logf(2, "[pe] bound %d imports, %d unresolved\n", total_bound, total_unres);
    if (total_unres > 0) return 2;

    hle_logf(2, "[host] %s booted: entry 0x%08X, %d unresolved imports\n",
             recomp_original_name, g_main_pe.entry, total_unres);

    hle_call_guest(g_main_pe.entry, 0, NULL);
    return g_exited ? g_exit_code : 0;
}

int wasm_frame(double now_ms) {
    (void)now_ms;
    wasm_ui_present();
    return g_exited ? 0 : 1;
}
