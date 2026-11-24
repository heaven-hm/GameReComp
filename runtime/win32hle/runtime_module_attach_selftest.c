/*
 * runtime_module_attach_selftest.c - the loader half of LoadLibrary.
 *
 * Mapping a DLL only gives the guest data. This proves the missing runtime
 * attach path: bind the DLL's own IAT (lifted companion export preferred over
 * a shim), run its TLS callbacks, then call its lifted DllMain with
 * (hinstDLL, DLL_PROCESS_ATTACH, 0) -- in that order. A DllMain that returns
 * FALSE must make the load fail and leave no module registered.
 *
 * Two independent fixtures:
 *   1. a synthetic PE in guest memory, attached directly through the new
 *      entry point with fixed dispatch VAs (ordering and failure cases);
 *   2. a tiny real PE32 file in TMPDIR, loaded through LoadLibraryA via
 *      hle_call_guest, then queried with GetProcAddress by name and forwarder.
 * No game asset, original DLL or display is needed.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "win32hle.h"
#include "pe_format.h"

/* Only this selftest calls the attach helper; keep it out of the public header. */
extern int hle_module_attach(const pe_image *img, const char *guest_path, int owned);
extern uint32_t hle_get_proc_address(uint32_t hmod, uint32_t name_or_ord);
extern uint32_t hle_get_last_error(void);

#define TLS_CB_VA 0x00406000u
#define MAIN_VA   0x00406100u
#define FAIL_VA   0x00406200u
#define COMP_VA   0x00406300u
#define DLL_INIT_FAILED 1114u

static int fails, tls_calls, main_calls, fail_calls, nseq;
static int seq[8];
static uint32_t tls_a0, tls_a1, tls_a2, main_a0, main_a1, main_a2;

static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

/* Lifted bodies: same prologue as every generated function, so A32 reads the
 * stdcall frame hle_call_guest pushes for DllMain and the TLS callbacks. */
static void tls_cb(void) {
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0;
    RECOMP_ENTER(TLS_CB_VA);
    tls_calls++;
    tls_a0 = A32(0); tls_a1 = A32(1); tls_a2 = A32(2);
    if (nseq < 8) seq[nseq++] = 1;
}
static void dll_main(void) {
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0;
    RECOMP_ENTER(MAIN_VA);
    main_calls++;
    main_a0 = A32(0); main_a1 = A32(1); main_a2 = A32(2);
    if (nseq < 8) seq[nseq++] = 2;
    g_eax = 1;
}
static void fail_main(void) {
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0;
    RECOMP_ENTER(FAIL_VA);
    fail_calls++;
    if (nseq < 8) seq[nseq++] = 3;
    g_eax = 0;                                          /* DllMain returns FALSE */
}
/* The dispatch table is binary-searched, so keep the VAs ascending. */
const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { TLS_CB_VA, tls_cb }, { MAIN_VA, dll_main }, { FAIL_VA, fail_main }
};
const uint32_t recomp_dispatch_count = 3;

static void shim_sleep(void) { }
static const win32hle_shim test_shims[] = { { "Sleep", shim_sleep }, { 0, 0 } };

/* The DLL's import of "ShimFunc" must go to the lifted companion body, not
 * to a shim of the same name; the binder asks the companion first. */
static const recomp_export_t exports[] = { { "ShimFunc", COMP_VA, 1u } };
static const recomp_companion_t companions[] = {
    { "TEST.dll", 0, NULL, NULL, 0, exports, 1 }
};
const recomp_companion_t *const recomp_companion_list = companions;
const uint32_t recomp_companion_count = 1;

static uint32_t guest_str(const char *s) {
    uint32_t va = hle_va(hle_alloc((uint32_t)strlen(s) + 1));
    memcpy(hle_ptr(va), s, strlen(s) + 1);
    return va;
}

/* A 0x2000-byte image with a real import directory (one by-name thunk) and a
 * TLS directory whose callback array holds TLS_CB_VA. The entry VA is supplied
 * by the caller: it need not be a VA inside the image, since the generated
 * dispatch table is keyed by absolute VA. */
static uint32_t make_image(const char *what, uint32_t entry_va) {
    uint32_t base = hle_va(hle_alloc(0x2000u));
    ok(what, base != 0);
    memset(hle_ptr(base), 0, 0x2000u);
    MEM16(base) = 0x5A4D;                                  /* MZ */
    MEM32(base + 0x3Cu) = 0x80u;
    uint32_t nt = base + 0x80u, opt = nt + 24u;
    MEM32(nt) = 0x00004550u;                               /* PE\0\0 */
    MEM16(nt + 4u) = 0x014Cu;                              /* i386 */
    MEM16(opt) = 0x010Bu;                                  /* PE32 */
    MEM32(opt + 92u) = 16u;                                /* NumberOfRvaAndSizes */
    MEM32(opt + 96u + 1u * 8u) = 0x400u;                   /* import dir RVA */
    MEM32(opt + 96u + 1u * 8u + 4u) = 0x40u;
    MEM32(opt + 96u + 9u * 8u) = 0x600u;                   /* TLS dir RVA */
    MEM32(opt + 96u + 9u * 8u + 4u) = 24u;

    uint32_t d = base + 0x400u;
    MEM32(d) = 0x440u;                                     /* OriginalFirstThunk */
    MEM32(d + 12u) = 0x460u;                               /* Name */
    MEM32(d + 16u) = 0x480u;                               /* FirstThunk (IAT) */
    MEM32(base + 0x440u) = 0x4A0u;
    MEM32(base + 0x480u) = 0x4A0u;
    memcpy(hle_ptr(base + 0x460u), "TEST.dll\0", 9);
    MEM16(base + 0x4A0u) = 0;                              /* hint */
    memcpy(hle_ptr(base + 0x4A2u), "ShimFunc\0", 9);

    MEM32(base + 0x600u + 12u) = base + 0x700u;            /* AddressOfCallBacks */
    MEM32(base + 0x700u) = TLS_CB_VA;
    MEM32(base + 0x704u) = 0;
    (void)entry_va;
    return base;
}

static pe_image image_at(uint32_t base, uint32_t entry_va) {
    pe_image img;
    memset(&img, 0, sizeof img);
    img.base = base;
    img.image_base = base;
    img.span = 0x2000u;
    img.entry = entry_va;
    img.import_rva = 0x400u;
    return img;
}

/* A minimal on-disk PE32 DLL: one section, one plain export and one forwarder
 * to KERNEL32.Sleep. Its entry point has no lifted body, so attaching it must
 * stay data-only rather than execute original PE bytes. */
static void put16(uint8_t *b, uint32_t off, uint16_t v) { memcpy(b + off, &v, 2); }
static void put32(uint8_t *b, uint32_t off, uint32_t v) { memcpy(b + off, &v, 4); }

static void write_test_pe(const char *path) {
    uint8_t b[0x1000];
    memset(b, 0, sizeof b);
    put16(b, 0, 0x5A4D);
    put32(b, 0x3Cu, 0x80u);
    put32(b, 0x80u, 0x00004550u);                          /* PE signature */
    put16(b, 0x84u, 0x014Cu);                              /* i386 */
    put16(b, 0x86u, 1u);                                   /* one section */
    uint32_t opt = 0x98u;                                  /* 0x80 + 4 + 20 */
    put16(b, 0x94u, 0xE0u);                                /* SizeOfOptionalHeader */
    put16(b, opt, 0x010Bu);                                /* PE32 */
    put32(b, opt + 16u, 0x1000u);                          /* AddressOfEntryPoint */
    put32(b, opt + 28u, 0x10000000u);                      /* ImageBase */
    put32(b, opt + 32u, 0x1000u);                          /* SectionAlignment */
    put32(b, opt + 36u, 0x200u);                           /* FileAlignment */
    put32(b, opt + 56u, 0x3000u);                          /* SizeOfImage */
    put32(b, opt + 60u, 0x200u);                           /* SizeOfHeaders */
    put32(b, opt + 92u, 16u);                              /* NumberOfRvaAndSizes */
    put32(b, opt + 96u, 0x1400u);                          /* export dir RVA */
    put32(b, opt + 100u, 0x200u);                          /* export dir size */

    uint32_t sec = opt + 0xE0u;
    memcpy(b + sec, ".text\0\0\0", 8);
    put32(b, sec + 8u, 0x800u);                            /* VirtualSize */
    put32(b, sec + 12u, 0x1000u);                          /* VirtualAddress */
    put32(b, sec + 16u, 0x800u);                           /* SizeOfRawData */
    put32(b, sec + 20u, 0x200u);                           /* PointerToRawData */
    put32(b, sec + 36u, 0x60000020u);                      /* code|exec|read */

    /* .text raw starts at file 0x200 == RVA 0x1000, so RVA = offset + 0xE00. */
    put32(b, 0x600u + 16u, 1u);                            /* ordinal Base */
    put32(b, 0x600u + 20u, 2u);                            /* NumberOfFunctions */
    put32(b, 0x600u + 24u, 2u);                            /* NumberOfNames */
    put32(b, 0x600u + 28u, 0x1480u);                       /* AddressOfFunctions */
    put32(b, 0x600u + 32u, 0x1490u);                       /* AddressOfNames */
    put32(b, 0x600u + 36u, 0x14A0u);                       /* NameOrdinals */
    put32(b, 0x680u, 0x1100u);                             /* Direct -> code RVA */
    put32(b, 0x684u, 0x1500u);                             /* ViaShim -> forwarder */
    put32(b, 0x690u, 0x14C0u);
    put32(b, 0x694u, 0x14C7u);
    put16(b, 0x6A0u, 0u);
    put16(b, 0x6A2u, 1u);
    memcpy(b + 0x6C0u, "Direct\0", 7);
    memcpy(b + 0x6C7u, "ViaShim\0", 8);
    memcpy(b + 0x700u, "KERNEL32.Sleep\0", 15);

    FILE *f = fopen(path, "wb");
    ok("test PE created", f != NULL);
    if (f) { fwrite(b, 1, sizeof b, f); fclose(f); }
}

int main(void) {
    win32hle_register(test_shims);
    win32hle_register(win32hle_module);
    mach_enter();

    /* ---- 1. direct attach: ordering, arguments, IAT, registration ---- */
    uint32_t base = make_image("attach image", MAIN_VA);
    pe_image img = image_at(base, MAIN_VA);
    ok("attach succeeds", hle_module_attach(&img, "ATTACH.dll", 0) == 0);
    ok("IAT bound to lifted companion export", MEM32(base + 0x480u) == COMP_VA);
    ok("TLS callback ran once", tls_calls == 1);
    ok("DllMain ran once", main_calls == 1);
    ok("TLS before DllMain", nseq == 2 && seq[0] == 1 && seq[1] == 2);
    ok("TLS got hinstDLL", tls_a0 == base);
    ok("TLS got PROCESS_ATTACH and reserved 0", tls_a1 == 1u && tls_a2 == 0u);
    ok("DllMain got hinstDLL", main_a0 == base);
    ok("DllMain got PROCESS_ATTACH and reserved 0", main_a1 == 1u && main_a2 == 0u);
    ok("attached module is findable by name",
        hle_call_guest(hle_resolve("GetModuleHandleA"), 1,
                       (uint32_t[]){ guest_str("ATTACH.dll") }) == base);

    /* ---- 2. negative: DllMain(FALSE) rejects the load ---- */
    tls_calls = main_calls = fail_calls = nseq = 0;
    uint32_t bad = make_image("fail image", FAIL_VA);
    pe_image badimg = image_at(bad, FAIL_VA);
    ok("failed DllMain rejects attach", hle_module_attach(&badimg, "FAIL.dll", 0) == -1);
    ok("failing DllMain ran", fail_calls == 1);
    ok("TLS still precedes a failing DllMain", nseq == 2 && seq[0] == 1 && seq[1] == 3);
    ok("ERROR_DLL_INIT_FAILED set", hle_get_last_error() == DLL_INIT_FAILED);
    ok("failed module not registered",
        hle_call_guest(hle_resolve("GetModuleHandleA"), 1,
                       (uint32_t[]){ guest_str("FAIL.dll") }) == 0);

    /* ---- 3. negative: no lifted body means no fabricated call ---- */
    tls_calls = main_calls = fail_calls = nseq = 0;
    uint32_t data_base = make_image("data image", 0);
    MEM32(data_base + 0x700u) = 0;                         /* no TLS callbacks */
    pe_image dataimg = image_at(data_base, data_base + 0x1000u);
    ok("data-only DLL attaches", hle_module_attach(&dataimg, "DATA.dll", 0) == 0);
    ok("unlifted entry is not called", main_calls == 0 && fail_calls == 0 && tls_calls == 0);
    ok("data-only DLL's IAT still bound", MEM32(data_base + 0x480u) == COMP_VA);

    /* ---- 4. the real LoadLibraryA/GetProcAddress guest-ABI path ---- */
    const char *tmp = getenv("TMPDIR");
    if (!tmp || !*tmp) tmp = "/tmp";
    char path[1024];
    snprintf(path, sizeof path, "%s/attach_selftest_%d.dll", tmp, (int)getpid());
    write_test_pe(path);
    uint32_t loaded = hle_call_guest(hle_resolve("LoadLibraryA"), 1,
                                     (uint32_t[]){ guest_str(path) });
    ok("LoadLibraryA mapped the DLL", loaded != 0);
    if (loaded) {
        ok("loaded module registered",
            hle_call_guest(hle_resolve("GetModuleHandleA"), 1,
                           (uint32_t[]){ guest_str(path) }) == loaded);
        ok("plain export answered", hle_get_proc_address(loaded, guest_str("Direct")) == loaded + 0x1100u);
        ok("forwarded export answered", hle_get_proc_address(loaded, guest_str("ViaShim")) == hle_resolve("Sleep"));
    }
    uint32_t absent = hle_call_guest(hle_resolve("LoadLibraryA"), 1,
                                     (uint32_t[]){ guest_str("C:\\NO_SUCH_MODULE.dll") });
    ok("absent DLL refused with MOD_NOT_FOUND", absent == 0 && hle_get_last_error() == 126u);
    unlink(path);

    mach_leave();
    if (fails) return 1;
    printf("runtime_module_attach_selftest: all checks passed\n");
    return 0;
}
