/*
 * host_lite.c - the portable spine of a win32hle host (see win32hle.h).
 *
 * Owns the recomp32 register file, the machine lock, each thread's simulated
 * TIB (g_fs_base), the dispatch lookups, the shim registry, and the explicit
 * native -> guest call. No inline asm and no OS exception handling: every
 * import is a C shim, so the guest -> native and native -> guest paths are both
 * ordinary C calls. Builds under gcc/clang -m32 and MSVC x86 alike.
 *
 * "lite" because it is the spine without a PE loader: enough to drive lifted
 * code and the shim layer (the selftest does exactly that). A full game host
 * adds image mapping and IAT binding on top; those are separate.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <pthread.h>
#include <sched.h>
#include "win32hle.h"

/* ------------------------------------------------------------ register file
 * recomp_types.h declares these extern; exactly one translation unit defines
 * them, and for a win32hle host that is here. (Mirrors native32.c.) */
uint32_t  g_eax, g_ecx, g_edx, g_esp, g_ebx, g_esi, g_edi, g_ebp;
double    g_st[8];
int       g_fp_top;
uint16_t  g_fpu_cw = 0x027F;
uint16_t  g_seg_cs, g_seg_ds, g_seg_es, g_seg_fs, g_seg_gs, g_seg_ss;
uint64_t  g_mm[8];
xmm_t     g_xmm[8];
uint32_t  g_mxcsr = 0x1F80;
uint32_t  g_fs_base, g_gs_base;
ptrdiff_t g_mem_base = 0;   /* flat: 0; based: set by hle_mem_init (guest_mem.c) */
uint32_t  g_cur_func;
const char *g_cur_import;
uint32_t  g_icall_trace[ICALL_TRACE_SIZE], g_icall_from[ICALL_TRACE_SIZE];
uint32_t  g_icall_trace_idx, g_icall_count;

int win32hle_trace;

/* ---------------------------------------------------------- call cap
 *
 * A run that stops at a known point, which is what bisecting a replay needs:
 * run the same input to checkpoint N, then N/2, and so on. Off unless
 * RECOMP_CALL_CAP asks for it.
 *
 * The clock lives in clock.c and is the coder's, not this file's. An earlier
 * version of this file carried a second, incompatible clock -- including an int
 * named `hle_clock_virtual` where clock.c has a function of that name -- and
 * the linker bound the two together, so the flag read as the function's address
 * and was always true. Two implementations of one idea is how that happens.
 */
uint32_t hle_call_count;             /* shim dispatches since start */
uint32_t hle_call_cap;               /* 0 = no cap */
static int g_cap_read;

/* ------------------------------------------------------- state fingerprint
 *
 * A call count is not a state. Bisecting on call sequences proves the two runs
 * made different calls; bisecting on state proves they are in different states,
 * which is the stronger claim and the one a divergence report should rest on.
 *
 * FNV-1a over the eight general-purpose registers, the lazy flag state, and the
 * guest memory ranges named by RECOMP_STATE_RANGE -- a comma-separated list of
 * base:length pairs in any C integer notation:
 *
 *   RECOMP_STATE_RANGE=0x00400000:0x40000,0x10500000:0x100000
 *
 * With no range named it hashes the registers and flags only, and says so in
 * the cap line's `ranges 0`. A registers-only fingerprint that looked like a
 * state comparison and was not one would be worse than no fingerprint.
 *
 * This cannot enumerate committed memory -- the runtime has no region
 * accessor -- so a range has to be named. What to name is the caller's
 * knowledge, and naming it explicitly is honest about the fact that the
 * fingerprint covers what it was told to cover and nothing else.
 */
enum { HLE_STATE_REGS = 13 };          /* 8 general purpose + 4 flag words + eip */
static struct { uint32_t base, len; } g_state_range[16];
static int g_n_state_range = -1;

static void state_ranges_init(void) {
    const char *spec = getenv("RECOMP_STATE_RANGE");
    g_n_state_range = 0;
    if (!spec || !*spec) return;
    const char *p = spec;
    while (*p && g_n_state_range < (int)(sizeof g_state_range / sizeof *g_state_range)) {
        char *end;
        uint32_t base = (uint32_t)strtoul(p, &end, 0);
        if (end == p) break;
        p = end;
        if (*p != ':') break;
        p++;
        uint32_t len = (uint32_t)strtoul(p, &end, 0);
        if (end == p) break;
        p = end;
        if (*p == ',') p++;
        if (len) {
            g_state_range[g_n_state_range].base = base;
            g_state_range[g_n_state_range].len = len;
            g_n_state_range++;
        }
    }
}

static uint64_t fnv1a(uint64_t h, const void *data, size_t n) {
    const uint8_t *p = data;
    for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 1099511628211ull; }
    return h;
}

int hle_state_ranges(void) {
    if (g_n_state_range < 0) state_ranges_init();
    return g_n_state_range;
}

uint64_t hle_state_fingerprint(uint32_t *regs_out) {
    uint32_t regs[HLE_STATE_REGS] = {
        g_eax, g_ebx, g_ecx, g_edx, g_esi, g_edi, g_ebp, g_esp,
        g_flag_k, g_flag_a, g_flag_b, g_flag_cf, g_cur_func,
    };
    if (regs_out) memcpy(regs_out, regs, sizeof regs);
    uint64_t h = 1469598103934665603ull;
    h = fnv1a(h, regs, sizeof regs);
    if (hle_state_ranges() > 0)
        for (int i = 0; i < g_n_state_range; i++)
            h = fnv1a(h, hle_ptr(g_state_range[i].base), g_state_range[i].len);
    return h;
}

static void cap_init(void) {
    const char *cap = getenv("RECOMP_CALL_CAP");
    if (cap) hle_call_cap = (uint32_t)strtoul(cap, NULL, 0);
    if (hle_call_cap)
        fprintf(stderr, "[determinism] call cap %u\n", hle_call_cap);
    g_cap_read = 1;
}

void hle_fatal(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    fprintf(stderr, "[win32hle] FATAL: ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
    abort();
}

/* --------------------------------------------------------------- the lock
 * One mutex serialises the (global) register file. A thread holds it while it
 * runs lifted code; mach_enter/mach_leave nest per thread (a shim that calls
 * back into the guest nests the claim) and only the outermost pair takes and
 * gives the mutex, saving and loading the thread's registers as it does.
 *
 * A thread gives the machine to others mid-run only through hle_yield and the
 * hle_block_begin/end pair, which save its registers first. (Unlocking from a
 * nested depth without that is how another thread's registers end up in this
 * one's lifted code.) Every import is a yield point when another thread is
 * waiting (shim_entry), as every native call is under native32, and so is
 * every RECOMP_YIELD_EVERY-th loop back-edge (recomp_yield_hook). */
static pthread_mutex_t g_mach = PTHREAD_MUTEX_INITIALIZER;
static volatile int      g_waiters;     /* threads blocked in mach_take */
static volatile unsigned g_owner_gen;   /* bumped on every take */

static void mach_take(void) {
    __sync_fetch_and_add(&g_waiters, 1);
    pthread_mutex_lock(&g_mach);
    __sync_fetch_and_sub(&g_waiters, 1);
    g_owner_gen++;
}

typedef struct {
    uint32_t eax, ecx, edx, esp, ebx, esi, edi, ebp, fs, cur;
    double   st[8];
    int      fp_top;
    uint16_t fpu_cw;
    uint64_t mm[8];
} regs_t;

static void regs_save(regs_t *r) {
    r->eax=g_eax; r->ecx=g_ecx; r->edx=g_edx; r->esp=g_esp;
    r->ebx=g_ebx; r->esi=g_esi; r->edi=g_edi; r->ebp=g_ebp;
    r->fs=g_fs_base; r->cur=g_cur_func;
    memcpy(r->st, g_st, sizeof g_st); r->fp_top=g_fp_top; r->fpu_cw=g_fpu_cw;
    memcpy(r->mm, g_mm, sizeof g_mm);
}
static void regs_load(const regs_t *r) {
    g_eax=r->eax; g_ecx=r->ecx; g_edx=r->edx; g_esp=r->esp;
    g_ebx=r->ebx; g_esi=r->esi; g_edi=r->edi; g_ebp=r->ebp;
    g_fs_base=r->fs; g_cur_func=r->cur;
    memcpy(g_st, r->st, sizeof g_st); g_fp_top=r->fp_top; g_fpu_cw=r->fpu_cw;
    memcpy(g_mm, r->mm, sizeof g_mm);
}

/* A guest thread's stack and simulated TIB. Lifted code reads fs:[n] as
 * MEM32(g_fs_base + n); the host's own TLS stays separate, so a guest SEH frame
 * never lands on a chain the host walks. */
#define GUEST_STACK (4u << 20)

static uint32_t make_tib(uint32_t lo, uint32_t hi) {
    uint32_t *t = (uint32_t *)hle_alloc(0x1000);
    if (!t) hle_fatal("no guest memory for a thread block");
    t[0]        = 0xFFFFFFFFu;                 /* ExceptionList: end of chain */
    t[1]        = hi;                          /* StackBase                   */
    t[2]        = lo;                          /* StackLimit                  */
    t[0x18/4]   = hle_va(t);                   /* Self (fs:[0x18])            */
    return hle_va(t);
}

typedef struct { regs_t r; int depth, inited; } mstate;
static __thread mstate g_ms;

void mach_enter(void) {
    if (g_ms.depth++ == 0) {
        if (!g_ms.inited) {
            uint32_t lo = hle_vm_reserve(0, GUEST_STACK);
            if (!lo) hle_fatal("no guest memory for a thread's stack");
            g_ms.r.esp    = lo + GUEST_STACK - 64;
            g_ms.r.fs     = make_tib(lo, lo + GUEST_STACK);
            g_ms.r.fpu_cw = 0x027F;
            g_ms.inited   = 1;
        }
        mach_take();
        regs_load(&g_ms.r);
    }
}
void mach_leave(void) {
    if (--g_ms.depth == 0) {
        regs_save(&g_ms.r);
        pthread_mutex_unlock(&g_mach);
    }
}

/* Give the machine up around a wait (a Sleep, an event, a contended critical
 * section). The registers go with it; nothing between begin and end may touch
 * the register file or guest-shared host state. */
static __thread unsigned g_block_gen;
void hle_block_begin(void) {
    if (!g_ms.depth) return;
    regs_save(&g_ms.r);
    g_block_gen = g_owner_gen;
    pthread_mutex_unlock(&g_mach);
}
/* The mutex is not fair: a thread that gives the machine up for a moment
 * (a wait with no timeout, polled in a loop) took it straight back, and a
 * timer callback waiting for the machine while holding a game mutex never
 * ran, so the loop polling that mutex never ended. Coming back, a thread
 * first lets a waiter have its turn. */
void hle_block_end(void) {
    if (!g_ms.depth) return;
    for (int spins = 0; g_waiters && g_owner_gen == g_block_gen && spins < 1000; spins++) sched_yield();
    mach_take();
    regs_load(&g_ms.r);
}

/* Let a waiting thread have the machine, if there is one. The mutex is not
 * fair, so this waits until someone else has actually taken it. */
void hle_yield(void) {
    if (!g_waiters || !g_ms.depth) return;
    unsigned gen = g_owner_gen;
    hle_block_begin();
    for (int spins = 0; g_waiters && g_owner_gen == gen && spins < 1000; spins++) sched_yield();
    hle_block_end();
}

/* ------------------------------------------------------------- dispatch
 * recomp_lookup searches the generated dispatch table (declared extern in
 * recomp_types.h; defined by the lifted program, or the selftest). */
recomp_func_t recomp_lookup(uint32_t va) {
    uint32_t lo = 0, hi = recomp_dispatch_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2, m = recomp_dispatch_table[mid].address;
        if (m == va) return recomp_dispatch_table[mid].func;
        if (m < va) lo = mid + 1; else hi = mid;
    }
    return NULL;
}
recomp_func_t recomp_lookup_manual(uint32_t va) { (void)va; return NULL; }

/* --------------------------------------------------------- shim registry
 * Each shim gets a synthetic VA in a reserved range that no real image uses. */
#define SHIM_BASE  0x7F000000u
#define SHIM_STEP  16u
#define MAX_SHIMS  2048
static struct { const char *name; recomp_func_t fn; uint32_t va; } g_shim[MAX_SHIMS];
static int g_shim_n;

int win32hle_register(const win32hle_shim *shims) {
    int added = 0;
    for (; shims->name; shims++) {
        if (g_shim_n >= MAX_SHIMS) hle_fatal("too many shims");
        g_shim[g_shim_n].name = shims->name;
        g_shim[g_shim_n].fn   = shims->fn;
        g_shim[g_shim_n].va   = SHIM_BASE + SHIM_STEP * (uint32_t)g_shim_n;
        g_shim_n++; added++;
    }
    return added;
}
uint32_t hle_resolve(const char *name) {
    for (int i = 0; i < g_shim_n; i++)
        if (!strcmp(g_shim[i].name, name)) return g_shim[i].va;
    return 0;
}
const char *hle_name(uint32_t va) {
    for (int i = 0; i < g_shim_n; i++) if (g_shim[i].va == va) return g_shim[i].name;
    return NULL;
}

/* The shim VA last dispatched, so the unimplemented-import stub can name itself
 * (its fn is shared, so it learns which import it is from here). */
static uint32_t g_last_import_va;

/* An import VA that is one of our synthetic shim VAs resolves to its shim,
 * entered through shim_entry: the lookup and the call are back to back on the
 * thread that holds the machine, so the index passes through a global. */
static volatile uint32_t g_pending_shim;
static void shim_entry(void) {
    uint32_t i = g_pending_shim;
    if (g_waiters) hle_yield();
    g_last_import_va = g_shim[i].va;                     /* again: the thread that ran meanwhile set its own */
    g_cur_import = g_shim[i].name;
    /* The call count is what the virtual clock advances on and what a capped
     * run stops at, so it counts every dispatch whether or not tracing is on.
     * cap_init is called here, not from the clock: the cap has to work even
     * when no shim has read the clock yet, and a cap that silently does nothing
     * is the kind of thing that makes a bisector produce confident nonsense. */
    if (!g_cap_read) cap_init();
    hle_call_count++;
    if (hle_call_cap && hle_call_count > hle_call_cap) {
        /* The cap is a checkpoint: report the STATE, not just the count, so a
         * bisector can compare states rather than call sequences. */
        uint32_t regs[HLE_STATE_REGS];
        uint64_t fp = hle_state_fingerprint(regs);
        fprintf(stderr, "[determinism] state %016llx cap %u ranges %d\n",
                (unsigned long long)fp, hle_call_cap, hle_state_ranges());
        hle_exit(70);
    }
    /* A shim must return with esp ABOVE where it entered and on a word
     * boundary: it always pops at least the synthetic return address, plus its
     * own arguments for stdcall. esp must never come back below the entry
     * value (a shim that pushed without popping has corrupted the guest
     * stack), and a delta that is not a multiple of 4 means the stack pointer
     * itself is wrong.
     *
     * What this cannot check is whether the argc was RIGHT: the shim table is
     * { name, fn } with no declared arity, so the expected delta is unknown
     * here. A first version of this check compared against entry+4 (correct
     * only for a cdecl shim) and therefore reported every stdcall shim as
     * drifting by exactly its argument count -- GetModuleHandleA by 4 and
     * VirtualAlloc by 16, both of which are correct. Do not "fix" a report
     * from this check by changing the shim: add the arity to the table first,
     * then this can be exact. */
    uint32_t esp_in = win32hle_trace ? g_esp : 0;
    g_shim[i].fn();
    if (win32hle_trace) {
        if (g_esp <= esp_in || ((g_esp - esp_in) & 3u)) {
            fprintf(stderr, "[hle] BAD STACK after %s: esp 0x%08X -> 0x%08X "
                            "(a shim must pop its return address and stay on a "
                            "word boundary)\n", g_shim[i].name, esp_in, g_esp);
        } else if (win32hle_trace > 1) {
            fprintf(stderr, "[hle] %s popped %u word(s) (ret + argc)\n",
                    g_shim[i].name, (g_esp - esp_in) / 4u);
        }
    }
}
recomp_func_t recomp_lookup_import(uint32_t va) {
    if (va < SHIM_BASE || va >= SHIM_BASE + SHIM_STEP * (uint32_t)g_shim_n) return NULL;
    uint32_t i = (va - SHIM_BASE) / SHIM_STEP;
    if (i < (uint32_t)g_shim_n && g_shim[i].va == va) {
        g_last_import_va = va;
        g_cur_import = g_shim[i].name;
        /* The trace can be tagged with the guest thread id.
         *
         * The log is one global sequence across every thread, and a frame-loop
         * game's threads interleave differently from run to run even with a
         * virtual clock: measured on Doukutsu, the audio thread's DirectSound
         * calls land at a different point relative to the main thread's frame
         * loop, and the bisector correctly refuses to name a call because the
         * divergence is intermittent. Tagging each line with the thread that
         * made it lets each thread's own sequence be compared on its own, which
         * is the only comparison that can be stable when two threads genuinely
         * run concurrently.
         *
         * RECOMP_TRACE_TID=1 turns the tag on. Off by default, so the trace
         * format every existing tool reads is unchanged. */
        if (win32hle_trace) {
            static int tid_tag = -1;
            if (tid_tag < 0) {
                const char *v = getenv("RECOMP_TRACE_TID");
                tid_tag = v && *v && *v != '0';
            }
            if (tid_tag) fprintf(stderr, "[hle] %u %s\n", hle_thread_id(), g_shim[i].name);
            else fprintf(stderr, "[hle] %s\n", g_shim[i].name);
        }
        g_pending_shim = i;
        return shim_entry;
    }
    return NULL;
}

/* Permissive bind: unresolved imports bind here instead of failing. A stdcall
 * import can't be a silent no-op (it would not pop its args and the stack would
 * drift), so this names itself from the last-dispatched VA and stops — giving
 * the exact import the running program reached and who called it. Implement
 * that import, rerun, get the next. */
static void hle_unimplemented(void) {
    const char *nm = hle_name(g_last_import_va);
    hle_fatal("unimplemented import %s (0x%08X) called from guest 0x%08X",
              nm ? nm : "?", g_last_import_va, g_cur_func);
}

/* Resolve a name to a shim VA, or register a self-naming stub for it and return
 * that. Used as the resolver for a permissive bind. */
uint32_t hle_resolve_or_stub(const char *name) {
    uint32_t va = hle_resolve(name);
    if (va) return va;
    if (g_shim_n >= MAX_SHIMS) hle_fatal("too many shims");
    /* names from the IAT outlive the bind; the loader's image is mapped for the
     * whole run, so keeping the pointer is safe. */
    g_shim[g_shim_n].name = name;
    g_shim[g_shim_n].fn   = hle_unimplemented;
    g_shim[g_shim_n].va   = SHIM_BASE + SHIM_STEP * (uint32_t)g_shim_n;
    return g_shim[g_shim_n++].va;
}

/* ------------------------------------------------------- native -> guest */
uint32_t hle_call_guest(uint32_t va, int nargs, const uint32_t *args) {
    mach_enter();
    recomp_func_t f = recomp_lookup(va);       /* under the machine: shim_entry's index is shared */
    if (!f) f = recomp_lookup_import(va);
    if (!f) hle_fatal("no lifted function at 0x%08X", va);
    regs_t saved; regs_save(&saved);
    for (int i = nargs - 1; i >= 0; i--) PUSH32(g_esp, args[i]);
    PUSH32(g_esp, RECOMP_RETADDR);
    f();
    uint32_t eax = g_eax;
    regs_load(&saved);
    mach_leave();
    return eax;
}

/* Loops in lifted code hand the machine over too (RECOMP_BACKEDGE). */
static void yield_hook(void) { hle_yield(); }
__attribute__((constructor)) static void install_yield_hook(void) { recomp_yield_hook = yield_hook; }

/* ---------------------------------------------------------- handles
 * Kernel objects the guest holds by HANDLE: files, find searches, events,
 * mutexes... A handle is 0x100 + 4*slot (never 0, never INVALID_HANDLE_VALUE),
 * typed so a file call given an event's handle fails as on Windows. Each type
 * may register a closer for CloseHandle. Guest code calls these under the
 * machine; a host thread that touches the table must hold it too. */
#define MAX_HANDLES 4096
static struct { int type; void *obj; } g_handle[MAX_HANDLES];
static void (*g_closer[32])(void *);

uint32_t hle_handle_alloc(int type, void *obj) {
    for (int i = 1; i < MAX_HANDLES; i++)
        if (!g_handle[i].type) { g_handle[i].type = type; g_handle[i].obj = obj; return 0x100u + 4u * (uint32_t)i; }
    return 0;
}
static int handle_slot(uint32_t h) {
    if (h < 0x100u || (h & 3u)) return -1;
    uint32_t i = (h - 0x100u) / 4u;
    return i < MAX_HANDLES && g_handle[i].type ? (int)i : -1;
}
void *hle_handle_obj(uint32_t h, int type) {
    int i = handle_slot(h);
    return i >= 0 && (type == 0 || g_handle[i].type == type) ? g_handle[i].obj : NULL;
}
int hle_handle_type(uint32_t h) { int i = handle_slot(h); return i >= 0 ? g_handle[i].type : 0; }
void hle_handle_set_closer(int type, void (*fn)(void *)) { if (type > 0 && type < 32) g_closer[type] = fn; }
int hle_handle_close(uint32_t h) {
    int i = handle_slot(h);
    if (i < 0) return 0;
    int type = g_handle[i].type;
    void *obj = g_handle[i].obj;
    g_handle[i].type = 0, g_handle[i].obj = NULL;
    if (type > 0 && type < 32 && g_closer[type]) g_closer[type](obj);
    return 1;
}
