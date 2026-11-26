/*
 * cpu64.c - the small host-side runtime for generated x86-64 C.
 *
 * This file supplies exactly the externals generate64.py's output leaves open:
 *
 *   g_image_delta   - live_base - PE ImageBase (normally 0)
 *   dispatch()      - guest `call`: enter a recompiled function / HLE import
 *   dispatch_jmp()  - guest `jmp`: tail-enter one, no return-address push
 *   recomp64_load_image() - a tiny PE32+ loader that maps the image, makes a
 *                           guest stack, and fills the IAT with HLE sentinels
 *
 * It is deliberately NOT a recompiler and NOT an emulator. It does not know how
 * to translate an instruction; it only routes a guest VA to the C function the
 * lifter already emitted for it. Keeping it this small is the point: the LLVM
 * project's x64->ARM64 recompiler is a different architecture (IR + optimizer +
 * hosted Wine layer), and copying any of it here would replace our lifter
 * rather than advance it.
 *
 * Build it together with a generate64.py tree:
 *
 *   clang -target arm64-apple-macos11 -O2 -I<recomp tree> -I<this dir> \
 *         cpu64.c recomp_funcs_*.c recomp_dispatch.c recomp_imports.c runner.c \
 *         -o arith64
 *
 * HLE imports: the generated `recomp_imports[]` names the IAT slots. The loader
 * writes HLE_SENTINEL(i) into slot i; a `call`/`jmp` through that slot arrives
 * here as a dispatch target in [HLE_BASE, HLE_BASE + HLE_COUNT*HLE_STRIDE).
 * hle_invoke() is weak by default: a runner that implements the imports
 * overrides it. A call-import has a pushed return address, which the HLE path
 * pops to emulate the callee's `ret`; a jmp-import is a tail call and the
 * handler must emulate its own return (ExitProcess simply never returns).
 */
#include "cpu64.h"
#include "recomp_dispatch.h"

#include <stdio.h>
#include <string.h>
#include <errno.h>

#if !defined(_MSC_VER)
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#endif

/* ---- the one global the generated GVA() macro needs --------------------- */
int64_t g_image_delta = 0;   /* live_base - PE ImageBase; 0 when MAP_FIXED works */

/* ---- portable thread-local storage -------------------------------------
 * A Win64 process has ONE PEB but ONE TEB PER THREAD. The simulated TEB below
 * is therefore host-thread-local so concurrent guest threads cannot corrupt
 * each other's SEH chain (gs:[0x00]), stack bounds (gs:[0x08]/[0x10]), thread
 * id (gs:[0x48]) or TLS slots. Prefer standard C11 _Thread_local; keep MSVC's
 * __declspec(thread) and pre-C11 GNU's __thread so every supported host builds.
 * This mirrors the ES3_EH_TLS guard in eh64.c. */
#if defined(_MSC_VER)
#define CPU64_TLS __declspec(thread)
#elif defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
#define CPU64_TLS _Thread_local
#elif defined(__GNUC__)
#define CPU64_TLS __thread
#else
#define CPU64_TLS
#endif

/* ---- simulated TEB/PEB on hosts without a GS segment (Apple Silicon) ----
 * Win64 CRT startup reads gs:[0x30] for the PEB and PEB+0x10 for the image
 * base. x86 hosts use the real GS segment; here each host thread gets its own
 * zeroed TEB that points at the ONE process-wide PEB. */
#if !defined(__x86_64__) && !defined(_MSC_VER)
static CPU64_TLS uint8_t g_teb[0x1000] __attribute__((aligned(16)));
static CPU64_TLS int g_teb_ready;               /* per-thread init guard */
static uint8_t g_peb[0x400] __attribute__((aligned(16)));

/* Guest GS offsets are full 64-bit values. The old `off + width <= sizeof` check
 * WRAPS for off near UINT64_MAX (off + 8 becomes [0,7], off + 4 becomes [0,3]),
 * passing the check and then indexing g_teb - n out of bounds. Compare against
 * the LAST VALID OFFSET instead: sizeof(g_teb) - width is a positive constant,
 * so it cannot underflow and the single unsigned comparison rejects every
 * wrapped offset without ever computing off + width. */
static int cpu64_gs_ok(uint64_t off, uint64_t width) {
    return off <= (uint64_t)sizeof(g_teb) - width;
}

static void cpu64_teb_init(void) {
    if (g_teb_ready) return;
    g_teb_ready = 1;
    memset(g_teb, 0, sizeof g_teb);
    /* g_peb is zero-initialized once by .bss. Do NOT clear it here: unlike the
     * TEB it is shared by all threads, and cpu64_set_peb_image_base() may have
     * already written the process-wide image base at PEB+0x10. */
    *(uint64_t *)(void *)(g_teb + 0x30) = (uint64_t)(uintptr_t)g_peb; /* PEB */
}
uint64_t cpu64_rd_gs64(uint64_t off) {
    cpu64_teb_init();
    return cpu64_gs_ok(off, 8) ? rd64((uint64_t)(uintptr_t)g_teb + off) : 0;
}
uint32_t cpu64_rd_gs32(uint64_t off) {
    cpu64_teb_init();
    return cpu64_gs_ok(off, 4) ? rd32((uint64_t)(uintptr_t)g_teb + off) : 0;
}
void cpu64_wr_gs64(uint64_t off, uint64_t v) {
    cpu64_teb_init();
    if (cpu64_gs_ok(off, 8)) wr64((uint64_t)(uintptr_t)g_teb + off, v);
}
void cpu64_wr_gs32(uint64_t off, uint32_t v) {
    cpu64_teb_init();
    if (cpu64_gs_ok(off, 4)) wr32((uint64_t)(uintptr_t)g_teb + off, v);
}
void cpu64_set_peb_image_base(uint64_t va) {
    cpu64_teb_init();
    wr64((uint64_t)(uintptr_t)g_peb + 0x10, va);
}
#endif

/* ---- default unmodelled-op handler: override with a strong symbol ------- */
__attribute__((weak))
void x64_todo(uint64_t va, const char *text) {
    fprintf(stderr, "cpu64: RECOMP_TODO at %#llx: %s\n",
            (unsigned long long)va, text);
    abort();
}

/* ---- default HLE handler: override with a strong symbol in the runner ---- */
__attribute__((weak))
int hle_invoke(CPU *c, uint32_t idx) {
    (void)c;
    if (idx < (uint32_t)HLE_COUNT)
        fprintf(stderr, "cpu64: unresolved import %s!%s (HLE slot %u)\n",
                recomp_imports[idx].dll, recomp_imports[idx].name, idx);
    else
        fprintf(stderr, "cpu64: HLE slot %u out of range (HLE_COUNT %d)\n",
                idx, HLE_COUNT);
    return 0;
}

/* ---- dispatch table lookup --------------------------------------------- */
/* The table is emitted in ascending VA order by generate64.py. A binary search
 * keeps this O(log n) for the multi-thousand-function games the lifter targets. */
static const recomp_entry_t *find_entry(uint64_t va) {
    uint32_t lo = 0, hi = recomp_dispatch_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        uint64_t m = recomp_dispatch_table[mid].va;
        if (m == va) return &recomp_dispatch_table[mid];
        if (m < va) lo = mid + 1; else hi = mid;
    }
    return NULL;
}

/* Is `t` one of the IAT sentinel addresses the loader wrote? */
static int hle_index(uint64_t t, uint32_t *idx_out) {
    uint64_t base = (uint64_t)HLE_BASE;
    uint64_t end  = base + (uint64_t)HLE_COUNT * HLE_STRIDE;
    if (t >= base && t < end) {
        *idx_out = (uint32_t)((t - base) / HLE_STRIDE);
        return 1;
    }
    return 0;
}

static void unmodelled_import(uint32_t idx, const char *kind) {
    fprintf(stderr, "cpu64: %s-import unmodelled: %s!%s (HLE slot %u)\n",
            kind, recomp_imports[idx].dll, recomp_imports[idx].name, idx);
    fflush(stderr);
    abort();
}

static void bad_target(CPU *c, uint64_t target, const char *kind) {
    fprintf(stderr, "cpu64: %s to unmapped guest VA %#llx (guest RIP %#llx)\n",
            kind, (unsigned long long)target, (unsigned long long)c->rip);
    fflush(stderr);
    abort();
}

/* ---- the two externals the generated bodies call ----------------------- */
void dispatch(CPU *c, uint64_t target) {
    uint32_t idx;
    if (hle_index(target, &idx)) {
        if (!hle_invoke(c, idx))
            unmodelled_import(idx, "call");
        /* The generated `call` already pushed the return address. A real
         * callee would pop it on `ret`; so do we, closing the guest stack. */
        c->rsp += 8;
        return;
    }
    const recomp_entry_t *e = find_entry(target);
    if (!e) bad_target(c, target, "call");
    e->fn(c);
}

void dispatch_jmp(CPU *c, uint64_t target) {
    uint32_t idx;
    if (hle_index(target, &idx)) {
        /* A guest `jmp [__imp_X]` is usually MSVC's one-line import thunk:
         * some caller did `call thunk`, so the thunk's return address is on
         * the guest stack. The real import ends in `ret` and consumes it.
         * Emulate that ret here for every handler that returns; ExitProcess
         * never does, so it is unaffected. Not popping used to shift the
         * guest stack by 8 on each import and silently trash callee-saved
         * registers on the next call. */
        if (!hle_invoke(c, idx))
            unmodelled_import(idx, "jmp");
        c->rsp += 8;
        return;
    }
    const recomp_entry_t *e = find_entry(target);
    if (!e) bad_target(c, target, "jmp");
    e->fn(c);
}

/* ---- a tiny PE32+ loader ------------------------------------------------ */
/* Reads a real Windows x86-64 PE, maps it at (or near) its ImageBase, and
 * leaves `c` ready for dispatch(c, *entry_out). Only the pieces a lifted
 * translation unit actually touches are implemented: sections, the IAT, and a
 * stack. There is no loader heap, TLS, or Win32 subsystem here - that is what a
 * full HLE layer would add, and it is intentionally out of scope. */
int recomp64_load_image(const char *path, CPU *c, uint64_t *entry_out) {
#if defined(_MSC_VER)
    (void)path; (void)c; (void)entry_out;
    fprintf(stderr, "cpu64: PE loader is POSIX-only (mmap)\n");
    return -1;
#else
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        fprintf(stderr, "cpu64: open(%s): %s\n", path, strerror(errno));
        return -1;
    }
    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size < 0x100) {
        fprintf(stderr, "cpu64: stat(%s) failed or file too small\n", path);
        close(fd);
        return -1;
    }
    size_t fsz = (size_t)st.st_size;
    uint8_t *data = (uint8_t *)malloc(fsz);
    if (!data) { close(fd); return -1; }
    ssize_t got = read(fd, data, fsz);
    close(fd);
    if (got != (ssize_t)fsz) { free(data); return -1; }

    if (memcmp(data, "MZ", 2) != 0) {
        fprintf(stderr, "cpu64: %s is not a PE (no MZ)\n", path);
        free(data); return -1;
    }
    uint32_t peoff = *(uint32_t *)(data + 0x3c);
    if (peoff + 24 > fsz || memcmp(data + peoff, "PE\0\0", 4) != 0) {
        fprintf(stderr, "cpu64: %s has no PE signature\n", path);
        free(data); return -1;
    }
    uint8_t *coff = data + peoff + 4;
    uint16_t nsec = *(uint16_t *)(coff + 2);
    uint16_t optsz = *(uint16_t *)(coff + 16);
    uint8_t *opt = coff + 20;
    if (*(uint16_t *)opt != 0x20b) {
        fprintf(stderr, "cpu64: %s is not PE32+ (magic %#x)\n", path,
                *(uint16_t *)opt);
        free(data); return -1;
    }
    uint32_t entry_rva = *(uint32_t *)(opt + 16);
    uint64_t image_base = *(uint64_t *)(opt + 24);
    uint32_t size_of_image = *(uint32_t *)(opt + 56);
    uint8_t *sech = opt + optsz;
    if (size_of_image == 0) size_of_image = 0x100000;

    /* Map the image. Try the preferred base first so g_image_delta is 0 and
     * every statically-resolved address in the generated C is already right.
     * If something else owns that range, map anywhere and let GVA() shift.
     *
     * RW only, deliberately: on Apple Silicon a PROT_EXEC anonymous mapping
     * needs MAP_JIT + pthread_jit_write_protect_np, and none of it is needed
     * here. The lifted instructions were compiled into this process's ARM64
     * text segment; the PE bytes we map are guest *data* that generated C reads
     * and writes through rd64/wr64. Nothing ever jumps into this mapping. */
    void *map = mmap((void *)(uintptr_t)image_base, size_of_image,
                     PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    if (map == MAP_FAILED) {
        map = mmap(NULL, size_of_image, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (map == MAP_FAILED) {
            fprintf(stderr, "cpu64: mmap image: %s\n", strerror(errno));
            free(data); return -1;
        }
        g_image_delta = (int64_t)((uintptr_t)map - (uintptr_t)image_base);
    } else {
        g_image_delta = 0;
    }

    /* Copy raw section data. Virtual-size-only tail is already zero from mmap. */
    for (uint16_t i = 0; i < nsec; i++) {
        uint8_t *s = sech + (size_t)i * 40;
        uint32_t vsize = *(uint32_t *)(s + 8);
        uint32_t vaddr = *(uint32_t *)(s + 12);
        uint32_t rsize = *(uint32_t *)(s + 16);
        uint32_t raddr = *(uint32_t *)(s + 20);
        uint32_t n = rsize < vsize ? rsize : vsize;
        if (n > 0 && (uint64_t)raddr + n <= fsz &&
            (uint64_t)vaddr + n <= size_of_image) {
            memcpy((void *)(uintptr_t)GVA(image_base + vaddr), data + raddr, n);
        }
    }

    /* Guest stack. On Win64 a function entered by `call` sees RSP % 16 == 8
     * (the call pushed eight bytes). Lay down a fake return address at such a
     * slot so the entry function is entered exactly that way. */
    size_t stack_sz = 1u << 20;
    void *stack = mmap(NULL, stack_sz, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (stack == MAP_FAILED) {
        fprintf(stderr, "cpu64: mmap stack: %s\n", strerror(errno));
        free(data); return -1;
    }
    uint64_t top = (uint64_t)(uintptr_t)stack + stack_sz;
    uint64_t ret_slot = (top & ~(uint64_t)15) - 8;   /* == 8 mod 16 */
    wr64(ret_slot, 0);                               /* sentinel return address */

    memset(c, 0, sizeof(*c));
    c->rsp = ret_slot;
    c->rip = GVA(image_base + entry_rva);
#if !defined(__x86_64__) && !defined(_MSC_VER)
    cpu64_set_peb_image_base(GVA(image_base));  /* simulated PEB, see above */
#endif

    /* The generated code reads IAT slots through GVA(); write the sentinels to
     * the same addresses, so an indirect call through any of them lands in the
     * HLE range dispatch() recognises. */
    for (int i = 0; i < HLE_COUNT; i++)
        wr64(GVA(recomp_imports[i].slot), HLE_SENTINEL((uint32_t)i));

    if (entry_out) *entry_out = image_base + entry_rva;  /* original VA: dispatch key */
    free(data);
    return 0;
#endif
}
