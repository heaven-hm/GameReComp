/*
 * host64_linux.c - minimal native Linux host for lifted PE32+ guests.
 *
 * The generated x64 code (tools/lift/generate64.py) is CPU-only: it declares
 * dispatch()/dispatch_jmp() and expects the loader to map the original image at
 * its ImageBase, fill the IAT with HLE sentinels and supply a TEB base. This
 * file is that loader. It is deliberately small -- it implements only the Win64
 * imports needed by console PE32+ smoke targets and fails loudly for anything
 * else, rather than inventing behaviour a real Windows host would have.
 *
 * Build it as `src/main.c` of a project emitted by tools/x64_project.py, next to
 * the generated recomp_*.c files and cpu64.h/eh64.h. No Wine, no emulator.
 */
#include "cpu64.h"
#include "recomp_funcs.h"
#include "recomp_dispatch.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

/* Supplied by src/guest_image.c (generated from the original PE). */
extern const unsigned char guest_image[];
extern const unsigned long guest_image_len;

/* cpu64.h's portable GS model reads/writes through this pointer. */
uint8_t *g_gs_base;
static uint8_t gs_teb[0x2000];
int64_t g_image_delta = 0;   /* live_base - PE ImageBase; 0 when fixed mapping works */

/* Return address pushed for the initial dispatch. A guest that returns from
 * its entry point is treated as a clean exit rather than a bad dispatch. */
#define GUEST_RETURN_SENTINEL UINT64_MAX

static void die(const char *what, uint64_t v) {
    fprintf(stderr, "host64: %s (%#llx errno=%d: %s)\n", what,
            (unsigned long long)v, errno, strerror(errno));
    exit(96);
}

static uint16_t u16le(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t u32le(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint64_t u64le(const uint8_t *p) {
    return (uint64_t)u32le(p) | ((uint64_t)u32le(p + 4) << 32);
}

/* ---- Win64 import shims ---------------------------------------------------
 * Arguments arrive in RCX/RDX/R8/R9 (the lifted caller reserves shadow space),
 * results go back in RAX. The guest stack is untouched: dispatch() already
 * accounts for the call's return address. */
static void hle_call(const char *name, CPU *c) {
    if (!strcmp(name, "ExitProcess")) {
        fflush(stdout); fflush(stderr);
        exit((int)(uint32_t)c->rcx);
    }
    if (!strcmp(name, "GetStdHandle")) {
        /* STD_INPUT_HANDLE -10, STD_OUTPUT_HANDLE -11, STD_ERROR_HANDLE -12.
         * Any non-invalid pseudo-handle works for our two writers. */
        int64_t which = (int64_t)c->rcx;
        SET64(c->rax, which == -12 ? 2u : (which == -10 ? 3u : 1u));
        return;
    }
    if (!strcmp(name, "WriteFile")) {
        FILE *out = c->rcx == 2 ? stderr : stdout;
        const void *buf = (const void *)(uintptr_t)c->rdx;
        /* Win64: nNumberOfBytesToWrite is a 32-bit DWORD carried in R8. The
         * upper 32 bits are caller-undefined, so reading the full 64-bit
         * register as size_t let garbage high bits make fwrite() walk gigabytes
         * past buf (issue #345). Truncate exactly as the ABI requires. */
        uint32_t n = (uint32_t)c->r8;
        size_t wrote = 0;
        if (n == 0) {
            /* Zero-length writes succeed without touching lpBuffer. */
            if (c->r9) wr32(c->r9, 0);
            SET64(c->rax, 1u);   /* BOOL TRUE */
            return;
        }
        if (!buf) {
            /* lpBuffer is mandatory for a non-zero count. Fail closed instead
             * of handing a NULL pointer to fwrite(). */
            if (c->r9) wr32(c->r9, 0);
            SET64(c->rax, 0u);   /* BOOL FALSE */
            return;
        }
        wrote = fwrite(buf, 1, n, out);
        if (c->r9) wr32(c->r9, (uint32_t)wrote);
        SET64(c->rax, wrote == n ? 1u : 0u);
        return;
    }
    if (!strcmp(name, "FlushFileBuffers")) {
        fflush(c->rcx == 2 ? stderr : stdout);
        SET64(c->rax, 1u);
        return;
    }
    fprintf(stderr, "host64: unimplemented import %s (rcx=%#llx rdx=%#llx)\n",
            name, (unsigned long long)c->rcx, (unsigned long long)c->rdx);
    exit(97);
}

/* ---- generated control transfer ------------------------------------------
 * A lifted guest `call` pushes its return address on the GUEST stack and the
 * callee consumes it (`c->rsp += 8; return;` at the end of every L_ function).
 * An HLE import is a callee too: it MUST consume that word, or rsp drifts by
 * 8 per import and the next `pop` in a prologue restores a stale value into a
 * callee-saved register (measured: rsi became 0x100000000 after four calls).
 * A `jmp` through dispatch_jmp has NO pushed return address and must not pop.
 */
static int is_hle(uint64_t target) {
    return target >= HLE_BASE && target < HLE_BASE + (uint64_t)HLE_STRIDE * HLE_COUNT;
}

static void hle_enter(CPU *c, uint64_t target, int pop_return) {
    uint64_t off;
    uint32_t i;
    /* Fail closed on a misaligned or out-of-range indirect jump into the HLE
     * window: an unchecked index could read past recomp_imports[] (issue #345).
     * is_hle() bounds the normal path, but keep the check here so no future
     * caller can bypass it. */
    if (target < HLE_BASE) die("HLE target below base", target);
    off = target - HLE_BASE;
    if (off % HLE_STRIDE) die("misaligned HLE target", target);
    i = (uint32_t)(off / HLE_STRIDE);
    if (i >= (uint32_t)HLE_COUNT) die("HLE slot out of range", target);
    if (pop_return) c->rsp += 8;
    hle_call(recomp_imports[i].name, c);
}

static void dispatch_fn(CPU *c, uint64_t target) {
    uint32_t i;
    for (i = 0; i < recomp_dispatch_count; i++) {
        if (recomp_dispatch_table[i].va == target) {
            recomp_dispatch_table[i].fn(c);
            return;
        }
    }
    fprintf(stderr, "host64: dispatch to uncatalogued guest address %#llx (rip=%#llx)\n",
            (unsigned long long)target, (unsigned long long)c->rip);
    exit(98);
}

void dispatch(CPU *c, uint64_t target) {
    if (target == GUEST_RETURN_SENTINEL) return;
    if (is_hle(target)) { hle_enter(c, target, 1); return; }
    dispatch_fn(c, target);
}

void dispatch_jmp(CPU *c, uint64_t target) {
    if (target == GUEST_RETURN_SENTINEL) return;
    if (is_hle(target)) { hle_enter(c, target, 0); return; }
    dispatch_fn(c, target);
}

/* ---- PE32+ loader --------------------------------------------------------- */
int main(int argc, char **argv) {
    const uint8_t *img = guest_image;
    size_t len = guest_image_len;
    uint32_t pe_off, nsec, optsz, size_of_image, size_of_headers, entry_rva;
    uint64_t image_base;
    const uint8_t *opt, *sh;
    void *map;
    uint64_t map_va;
    uint32_t k;
    void *stack;
    size_t stack_size = 1u << 20;
    uint64_t stack_top;
    CPU cpu;

    (void)argc; (void)argv;   /* the PE is embedded; argv is kept for CLI parity */

    if (len < 0x40 || u16le(img) != 0x5A4D) die("embedded image is not MZ", 0);
    pe_off = u32le(img + 0x3c);
    if ((size_t)pe_off + 24 > len || memcmp(img + pe_off, "PE\0\0", 4)) die("bad PE signature", pe_off);
    if (u16le(img + pe_off + 4) != 0x8664) die("not AMD64", u16le(img + pe_off + 4));
    nsec = u16le(img + pe_off + 6);
    optsz = u16le(img + pe_off + 20);
    opt = img + pe_off + 24;
    if (u16le(opt) != 0x20b) die("not PE32+", u16le(opt));
    entry_rva = u32le(opt + 16);
    image_base = u64le(opt + 24);
    size_of_image = u32le(opt + 56);
    size_of_headers = u32le(opt + 60);
    sh = opt + optsz;
    if (!size_of_image || size_of_image > (1u << 30)) die("bad SizeOfImage", size_of_image);

    /* Prefer the PE's fixed ImageBase so GVA() is identity. MAP_FIXED_NOREPLACE
     * avoids clobbering anything the loader put there; if the address is taken,
     * fall back to an arbitrary mapping and let GVA() apply the delta. */
    map = MAP_FAILED;
#ifdef MAP_FIXED_NOREPLACE
    map = mmap((void *)(uintptr_t)image_base, size_of_image,
               PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
#endif
    if (map == MAP_FAILED) {
        map = mmap(NULL, size_of_image, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (map == MAP_FAILED) die("mmap image", 0);
    }
    map_va = (uint64_t)(uintptr_t)map;
    g_image_delta = (int64_t)(map_va - image_base);
    memset(map, 0, size_of_image);
    memcpy(map, img, size_of_headers < len ? size_of_headers : len);
    for (k = 0; k < nsec; k++) {
        const uint8_t *s = sh + (size_t)k * 40;
        uint32_t vsize = u32le(s + 8), va = u32le(s + 12);
        uint32_t rawsz = u32le(s + 16), rawptr = u32le(s + 20);
        uint32_t n;
        if (!vsize) vsize = rawsz;
        if ((uint64_t)va + vsize > size_of_image) die("section outside image", va);
        n = rawsz < vsize ? rawsz : vsize;
        if ((size_t)rawptr + n > len) die("section outside file", rawptr);
        memcpy((uint8_t *)map + va, img + rawptr, n);
        /* bytes between rawsz and VirtualSize are already zero from memset */
    }
    /* The generated code reaches imports through IAT slots. Write the sentinel
     * for each slot; dispatch() maps it back to recomp_imports[i].name. */
    for (k = 0; k < HLE_COUNT; k++) wr64(GVA(recomp_imports[k].slot), HLE_SENTINEL(k));

    stack = mmap(NULL, stack_size, PROT_READ | PROT_WRITE,
                 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (stack == MAP_FAILED) die("mmap guest stack", 0);
    stack_top = (uint64_t)(uintptr_t)stack + stack_size;

    memset(gs_teb, 0, sizeof(gs_teb));
    g_gs_base = gs_teb;
    wr64((uint64_t)(uintptr_t)(gs_teb + 0x30), (uint64_t)(uintptr_t)gs_teb); /* TEB.Self */
    wr64((uint64_t)(uintptr_t)(gs_teb + 0x08), stack_top);                  /* TEB.StackBase */
    wr64((uint64_t)(uintptr_t)(gs_teb + 0x10), (uint64_t)(uintptr_t)stack);  /* TEB.StackLimit */

    memset(&cpu, 0, sizeof(cpu));
    cpu.rsp = (stack_top - 0x1000) & ~(uint64_t)0xF;
    cpu.rip = GVA(image_base + entry_rva);
    push64(&cpu, GUEST_RETURN_SENTINEL);
    dispatch(&cpu, cpu.rip);
    fflush(stdout);
    return 0;
}
