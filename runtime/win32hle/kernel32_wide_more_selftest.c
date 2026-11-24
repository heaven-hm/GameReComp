/* kernel32_wide_more_selftest.c - GetWindowsDirectoryW guest-ABI selftest.
 *
 * Of the four KERNEL32 wide-char APIs named around issue #28,
 * GetCurrentDirectoryW and SetCurrentDirectoryW already live in module.c and
 * GetTempPathW in kernel32_crt.c; only GetWindowsDirectoryW was genuinely
 * absent from the registration table, so this shim is added in kernel32_ext.c
 * and the other three are deliberately not duplicated.
 *
 * Focused selftest: only the win32hle minimal core plus kernel32_ext are
 * linked. Every call goes through hle_call_guest, the UTF-16 result is read
 * with a bounded reader (a cap in code units, stopping at the NUL), and the
 * stdcall purge is measured directly by driving the shim with a synthetic
 * guest stack. No live game, Windows oracle or coverage claim is implied. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

/* The focused minimal-core build omits host.c and msvcrt.c, which own these
 * three services module.c now calls. Supply inert defaults only in that build
 * (-DK32W2_STANDALONE=1); the full-layer build leaves them to the real modules. */
#ifdef K32W2_STANDALONE
uint32_t hle_msvcrt_data(const char *name) { (void)name; return 0; }
void hle_pe_run_tls_callbacks(const pe_image *img) { (void)img; }
uint32_t recomp_companion_resolve(const char *dll, const char *name) {
    (void)dll; (void)name; return 0;
}
#endif

#define ERROR_INVALID_PARAMETER   87u
#define ERROR_INSUFFICIENT_BUFFER 122u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

/* hle_call_guest restores the register file, so it hides a wrong stdcall
 * purge. Drive the shim directly with a synthetic guest stack and count the
 * words it pops: ret + argc for a correct stdcall callee. */
static uint32_t shim_pops(const char *name, int argc, const uint32_t *args,
                          uint32_t *out_eax) {
    uint32_t va = hle_resolve(name);
    recomp_func_t fn = va ? recomp_lookup_import(va) : 0;
    if (!fn) return 0xFFFFFFFFu;
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t scratch = hle_va(hle_alloc(64));        /* ret + up to 3 args */
    g_esp = scratch + 32;
    for (int i = argc - 1; i >= 0; --i) { g_esp -= 4; MEM32(g_esp) = args[i]; }
    g_esp -= 4; MEM32(g_esp) = RECOMP_RETADDR;
    uint32_t esp_in = g_esp;
    fn();
    uint32_t words = (g_esp - esp_in) / 4u;
    if (out_eax) *out_eax = g_eax;
    g_esp = saved_esp; g_eax = saved_eax;
    return words;
}

/* Bounded UTF-16 read: never more than `cap` code units, and stop at the
 * first NUL. Returns the number of units read INCLUDING the NUL, or 0 when
 * the input is NULL or has no terminator in the window. */
static size_t wide_read(uint32_t va, uint16_t *out, size_t cap) {
    size_t i;
    if (!va) return 0;
    for (i = 0; i < cap; i++) {
        uint16_t c = MEM16(va + (uint32_t)i * 2u);
        out[i] = c;
        if (!c) return i + 1u;
    }
    return 0;
}

static int wide_eq(uint32_t va, const char *s, size_t cap) {
    uint16_t w[64];
    size_t n = wide_read(va, w, cap), len = strlen(s), i;
    if (n != len + 1u) return 0;                     /* NUL must be in the window */
    for (i = 0; i < len; i++) if (w[i] != (uint16_t)(unsigned char)s[i]) return 0;
    return w[len] == 0;
}

static int canary(uint32_t va, size_t from, size_t to) {
    const uint8_t *p = (const uint8_t *)hle_ptr(va);
    for (size_t i = from; i < to; i++) if (p[i] != 0xAA) return 0;
    return 1;
}

int main(void) {
    win32hle_register(win32hle_kernel32_ext);
    mach_enter();

    uint32_t fn = hle_resolve("GetWindowsDirectoryW");
    ok("GetWindowsDirectoryW registered", fn != 0);

    /* uSize == 0 is a pure size probe: required size including the NUL. */
    hle_set_last_error(0xDEADBEEFu);
    uint32_t probe = hle_call_guest(fn, 2, (uint32_t[]){ 0, 0 });
    ok("size probe returns 11 WCHARs", probe == 11u);

    /* ---- success: content, length, NUL, bounded write, cleared error ---- */
    uint32_t out = hle_va(hle_alloc(64));
    memset(hle_ptr(out), 0xAA, 64);
    hle_set_last_error(0xDEADBEEFu);
    uint32_t n = hle_call_guest(fn, 2, (uint32_t[]){ out, 32 });
    ok("success returns 10 (NUL excluded)", n == 10u);
    ok("writes C:\\WINDOWS as bounded UTF-16", wide_eq(out, "C:\\WINDOWS", 32));
    ok("success clears last error", hle_get_last_error() == 0);
    ok("write stops after 11 WCHARs (22 bytes)", canary(out, 22, 64));

    /* ---- NULL buffer with a real capacity: required size + honest error ---- */
    hle_set_last_error(0);
    uint32_t r = hle_call_guest(fn, 2, (uint32_t[]){ 0, 32 });
    ok("NULL buffer returns required size", r == 11u);
    ok("NULL buffer -> ERROR_INSUFFICIENT_BUFFER",
       hle_get_last_error() == ERROR_INSUFFICIENT_BUFFER);

    /* ---- short buffer: required size + error, buffer untouched ---- */
    uint32_t small = hle_va(hle_alloc(64));
    memset(hle_ptr(small), 0xAA, 64);
    hle_set_last_error(0);
    r = hle_call_guest(fn, 2, (uint32_t[]){ small, 10 });
    ok("short buffer returns required size", r == 11u);
    ok("short buffer -> ERROR_INSUFFICIENT_BUFFER",
       hle_get_last_error() == ERROR_INSUFFICIENT_BUFFER);
    ok("short buffer left untouched", canary(small, 0, 64));

    /* ---- exact fit: 11 WCHARs, no byte past them touched ---- */
    uint32_t exact = hle_va(hle_alloc(64));
    memset(hle_ptr(exact), 0xAA, 64);
    hle_set_last_error(0);
    r = hle_call_guest(fn, 2, (uint32_t[]){ exact, 11 });
    ok("exact-fit returns 10", r == 10u);
    ok("exact-fit content is C:\\WINDOWS", wide_eq(exact, "C:\\WINDOWS", 11));
    ok("exact-fit write is bounded", canary(exact, 22, 64));

    /* ---- address-space bound: a destination whose 22-byte write would wrap
     * past the guest's 4 GB fails closed instead of wrapping into low memory. */
    hle_set_last_error(0);
    r = hle_call_guest(fn, 2, (uint32_t[]){ 0xFFFFFFF0u, 32 });
    ok("near-top destination fails", r == 0);
    ok("near-top destination -> ERROR_INVALID_PARAMETER",
       hle_get_last_error() == ERROR_INVALID_PARAMETER);

    /* ---- direct stdcall purge: ret + 2 args popped, eax preserved ---- */
    uint32_t eax, words = shim_pops("GetWindowsDirectoryW", 2,
                                    (uint32_t[]){ out, 32 }, &eax);
    ok("pops ret + 2 args", words == 3u);
    ok("purge keeps eax", eax == 10u);
    words = shim_pops("GetWindowsDirectoryW", 2, (uint32_t[]){ 0, 0 }, &eax);
    ok("probe path pops ret + 2 args", words == 3u);
    ok("probe path purge keeps eax", eax == 11u);

    mach_leave();
    if (fails == 0) printf("kernel32_wide_more_selftest: all checks passed\n");
    return fails != 0;
}
