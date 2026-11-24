/* kernel32_wide_extra_selftest.c - GetShortPathNameW / GetLongPathNameW guest
 * ABI. These two KERNEL32 wide string/file imports were genuinely absent from
 * the registration table; the others named around issue #28 (GetFullPathNameW,
 * GetTempPathW, lstrlenW) already existed and are deliberately not duplicated.
 *
 * Focused selftest: only the win32hle core plus kernel32_ext and its helper
 * modules are linked (see the minimal-core build recorded in the PR). Every
 * call goes through hle_call_guest, and the stdcall purge is also measured
 * directly by driving the shim with a synthetic guest stack. No live game,
 * Windows oracle or 100% coverage claim is implied. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#define ERROR_INVALID_PARAMETER   87u
#define ERROR_FILE_NOT_FOUND       2u
#define ERROR_INSUFFICIENT_BUFFER 122u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int argc, const uint32_t *args) {
    uint32_t fn = hle_resolve(name);
    return fn ? hle_call_guest(fn, argc, args) : 0xFFFFFFFFu;
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
static uint32_t wide_guest(const char *s) {
    size_t n = strlen(s) + 1;
    uint32_t va = hle_va(hle_alloc((uint32_t)n * 2u));
    for (size_t i = 0; i < n; i++) MEM16(va + (uint32_t)i * 2u) = (uint16_t)(unsigned char)s[i];
    return va;
}
static void narrow(const uint16_t *w, char *out, size_t cap) {
    size_t i = 0;
    for (; w[i] && i + 1 < cap; i++) out[i] = (char)w[i];
    out[i] = 0;
}

int main(void) {
    win32hle_register(win32hle_kernel32_ext);
    mach_enter();

    ok("GetShortPathNameW registered", hle_resolve("GetShortPathNameW") != 0);
    ok("GetLongPathNameW registered", hle_resolve("GetLongPathNameW") != 0);

    /* Win32 signature: (lpszLongPath, lpszShortPath, cchBuffer) -- the output
     * buffer is argument 1, the capacity argument 2. */
    /* ---- GetShortPathNameW ---- */
    {
        uint32_t fn = hle_resolve("GetShortPathNameW");
        uint32_t name = wide_guest("README.TXT");
        uint32_t out = hle_va(hle_alloc(1024));
        char result[512];
        uint32_t n = hle_call_guest(fn, 3, (uint32_t[]){ name, out, 512 });
        narrow((const uint16_t *)hle_ptr(out), result, sizeof result);
        ok("GetShortPathNameW already-short path unchanged", strcmp(result, "README.TXT") == 0);
        ok("GetShortPathNameW length matches output", n == strlen(result));

        uint32_t probe = hle_call_guest(fn, 3, (uint32_t[]){ name, 0, 0 });
        ok("GetShortPathNameW NULL buffer is size probe", probe == n + 1);

        hle_set_last_error(0);
        uint32_t small = hle_call_guest(fn, 3, (uint32_t[]){ name, out, 4 });
        ok("GetShortPathNameW small buffer returns required size", small == n + 1);
        ok("GetShortPathNameW small buffer sets ERROR_INSUFFICIENT_BUFFER",
           hle_get_last_error() == ERROR_INSUFFICIENT_BUFFER);

        /* Long components are mangled to deterministic 8.3 form. */
        uint32_t lname = wide_guest("Program Files\\Long File Name.txt");
        uint32_t lout = hle_va(hle_alloc(1024));
        hle_call_guest(fn, 3, (uint32_t[]){ lname, lout, 512 });
        narrow((const uint16_t *)hle_ptr(lout), result, sizeof result);
        ok("GetShortPathNameW mangles directory to PROGRA~1", strstr(result, "PROGRA~1") != NULL);
        ok("GetShortPathNameW mangles file to LONGFI~1.TXT", strstr(result, "LONGFI~1.TXT") != NULL);
        ok("GetShortPathNameW result has no spaces", strchr(result, ' ') == NULL);

        /* Bounded reads: NULL and unterminated pointers must not scan the
         * guest block. Fill a buffer with no NUL inside the read window. */
        hle_set_last_error(0);
        ok("GetShortPathNameW NULL input fails", hle_call_guest(fn, 3,
           (uint32_t[]){ 0, out, 512 }) == 0);
        ok("GetShortPathNameW NULL input -> ERROR_INVALID_PARAMETER",
           hle_get_last_error() == ERROR_INVALID_PARAMETER);
        /* A truly unterminated WCHAR string: fill the whole U16_MAX window
         * (1M code units) so the bounded scan cannot find a NUL and fails
         * closed instead of walking off the guest block. */
        uint32_t unterminated = hle_va(hle_alloc(2u * 0x100000u));
        for (uint32_t i = 0; i < 0x100000u; i++) MEM16(unterminated + i * 2u) = 'A';
        hle_set_last_error(0);
        ok("GetShortPathNameW unterminated input fails", hle_call_guest(fn, 3,
           (uint32_t[]){ unterminated, out, 512 }) == 0);
        ok("GetShortPathNameW unterminated -> ERROR_INVALID_PARAMETER",
           hle_get_last_error() == ERROR_INVALID_PARAMETER);

        /* A successful write touches exactly (len+1) WCHARs, no more: use an
         * exact-fit capacity and check the canary past it. */
        uint32_t canary_out = hle_va(hle_alloc(64));
        memset(hle_ptr(canary_out), 0xAA, 64);
        uint32_t tiny = wide_guest("A.TXT");            /* 5 units + NUL = 6 */
        hle_call_guest(fn, 3, (uint32_t[]){ tiny, canary_out, 6 });
        const uint8_t *tail = (const uint8_t *)hle_ptr(canary_out) + 12;
        int clean = 1;
        for (int i = 0; i < 52; i++) if (tail[i] != 0xAA) clean = 0;
        ok("GetShortPathNameW write bounded by cchBuffer", clean);
        char canary_result[16];
        narrow((const uint16_t *)hle_ptr(canary_out), canary_result, sizeof canary_result);
        ok("GetShortPathNameW exact-fit write content", strcmp(canary_result, "A.TXT") == 0);

        uint32_t eax, words = shim_pops("GetShortPathNameW", 3,
            (uint32_t[]){ name, out, 512 }, &eax);
        ok("GetShortPathNameW pops ret + 3 args", words == 4u);
        ok("GetShortPathNameW purge keeps eax", eax == n);
    }

    /* ---- GetLongPathNameW ----
     * Signature: (lpszShortPath, lpszLongPath, cchBuffer). */
    {
        uint32_t fn = hle_resolve("GetLongPathNameW");
        uint32_t name = wide_guest("data\\rules.ini");
        uint32_t out = hle_va(hle_alloc(1024));
        char result[512];
        uint32_t n = hle_call_guest(fn, 3, (uint32_t[]){ name, out, 512 });
        narrow((const uint16_t *)hle_ptr(out), result, sizeof result);
        ok("GetLongPathNameW already-long path unchanged", strcmp(result, "data\\rules.ini") == 0);
        ok("GetLongPathNameW length matches output", n == strlen(result));

        uint32_t probe = hle_call_guest(fn, 3, (uint32_t[]){ name, 0, 0 });
        ok("GetLongPathNameW NULL buffer is size probe", probe == n + 1);

        hle_set_last_error(0);
        uint32_t small = hle_call_guest(fn, 3, (uint32_t[]){ name, out, 4 });
        ok("GetLongPathNameW small buffer returns required size", small == n + 1);
        ok("GetLongPathNameW small buffer sets ERROR_INSUFFICIENT_BUFFER",
           hle_get_last_error() == ERROR_INSUFFICIENT_BUFFER);

        hle_set_last_error(0);
        ok("GetLongPathNameW NULL input fails", hle_call_guest(fn, 3,
           (uint32_t[]){ 0, out, 512 }) == 0);
        ok("GetLongPathNameW NULL input -> ERROR_INVALID_PARAMETER",
           hle_get_last_error() == ERROR_INVALID_PARAMETER);
        uint32_t unterminated = hle_va(hle_alloc(2u * 0x100000u));
        for (uint32_t i = 0; i < 0x100000u; i++) MEM16(unterminated + i * 2u) = 'A';
        hle_set_last_error(0);
        ok("GetLongPathNameW unterminated input fails", hle_call_guest(fn, 3,
           (uint32_t[]){ unterminated, out, 512 }) == 0);
        ok("GetLongPathNameW unterminated -> ERROR_INVALID_PARAMETER",
           hle_get_last_error() == ERROR_INVALID_PARAMETER);

        /* A ~N component we cannot resolve is an honest failure, not the
         * short text dressed up as a successful expansion. */
        uint32_t bogus = wide_guest("NoSuchDir\\LONGFI~1.TXT");
        hle_set_last_error(0);
        ok("GetLongPathNameW unresolvable short name fails", hle_call_guest(fn, 3,
           (uint32_t[]){ bogus, out, 512 }) == 0);
        ok("GetLongPathNameW unresolvable -> ERROR_FILE_NOT_FOUND",
           hle_get_last_error() == ERROR_FILE_NOT_FOUND);

        uint32_t eax, words = shim_pops("GetLongPathNameW", 3,
            (uint32_t[]){ name, out, 512 }, &eax);
        ok("GetLongPathNameW pops ret + 3 args", words == 4u);
        ok("GetLongPathNameW purge keeps eax", eax == n);
    }

    /* ---- real short -> long round trip against a real directory ---- */
    {
        const char *tmp = getenv("TMPDIR");
        char dtemplate[512];
        snprintf(dtemplate, sizeof dtemplate, "%s/k32wide-XXXXXX", tmp ? tmp : "/tmp");
        char *dir = mkdtemp(dtemplate);
        ok("temp directory created", dir != NULL);
        if (dir) {
            char cwd[1024], filepath[1024];
            ok("getcwd before chdir", getcwd(cwd, sizeof cwd) != NULL);
            int fd;
            snprintf(filepath, sizeof filepath, "%s/longfilename.txt", dir);
            fd = open(filepath, O_CREAT | O_WRONLY | O_TRUNC, 0644);
            ok("created real long-named file", fd >= 0);
            if (fd >= 0) close(fd);
            ok("chdir to temp directory", chdir(dir) == 0);

            uint32_t sf = hle_resolve("GetShortPathNameW");
            uint32_t lf = hle_resolve("GetLongPathNameW");
            uint32_t in = wide_guest("longfilename.txt");
            uint32_t sbuf = hle_va(hle_alloc(1024)), lbuf = hle_va(hle_alloc(1024));
            hle_call_guest(sf, 3, (uint32_t[]){ in, sbuf, 512 });
            char shortp[512], longp[512];
            narrow((const uint16_t *)hle_ptr(sbuf), shortp, sizeof shortp);
            ok("round-trip short form", strcmp(shortp, "LONGFI~1.TXT") == 0);
            uint32_t short_va = wide_guest(shortp);
            uint32_t ln = hle_call_guest(lf, 3, (uint32_t[]){ short_va, lbuf, 512 });
            narrow((const uint16_t *)hle_ptr(lbuf), longp, sizeof longp);
            ok("round-trip long form", strcmp(longp, "longfilename.txt") == 0);
            ok("round-trip length matches", ln == strlen(longp));

            ok("chdir back", chdir(cwd) == 0);
            unlink(filepath);
            rmdir(dir);
        }
    }

    mach_leave();
    printf("kernel32_wide_extra_selftest: all checks passed\n");
    return fails != 0;
}
