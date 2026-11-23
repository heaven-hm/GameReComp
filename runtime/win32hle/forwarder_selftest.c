/*
 * forwarder_selftest.c - GetProcAddress on mapped images and forwarded exports.
 *
 * Two synthetic PE32 images are built in guest memory with real export tables.
 * The first exports a plain function, a forwarder to a system shim by name and
 * by ordinal, a forwarder into the second image (which forwards again), an
 * api-ms-win-style forwarder with no pseudo-module, and a forwarder cycle.
 * No game binary or display is needed.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

/* The helper is defined in module.c; declaring it here avoids changing the
 * public header for a function only this selftest calls directly. */
extern uint32_t hle_get_proc_address(uint32_t hmod, uint32_t name_or_ord);

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

static void shim_sleep(void) { }
static void shim_ordinal(void) { }
static void shim_api(void) { }
static const win32hle_shim test_shims[] = {
    { "Sleep", shim_sleep }, { "kernel32.dll#13", shim_ordinal },
    { "api_target_func", shim_api }, { 0, 0 }
};

/* Header plus an export directory at RVA 0x1000. Forwarder strings live below
 * 0x1600, inside the directory's 0x600-byte span; plain code sits at 0x1F00. */
static uint32_t make_image(const char *name, int nfunc, int main_image) {
    uint32_t base = hle_va(hle_alloc(0x2000u));
    ok(name, base != 0);
    memset(hle_ptr(base), 0, 0x2000u);
    MEM16(base) = 0x5A4D;                                  /* MZ */
    MEM32(base + 0x3Cu) = 0x80u;
    uint32_t nt = base + 0x80u;
    MEM32(nt) = 0x00004550u;
    MEM16(nt + 4) = 0x014Cu;
    MEM16(nt + 24) = 0x010Bu;                              /* PE32 */
    MEM32(nt + 24 + 92u) = 16u;                            /* NumberOfRvaAndSizes */
    MEM32(nt + 24 + 96u) = 0x1000u;                        /* export dir RVA */
    MEM32(nt + 24 + 100u) = 0x800u;                        /* export dir size */

    uint32_t d = base + 0x1000u;
    MEM32(d + 16u) = 1u;                                   /* ordinal Base */
    MEM32(d + 20u) = (uint32_t)nfunc;
    MEM32(d + 24u) = (uint32_t)nfunc;
    MEM32(d + 28u) = 0x1100u;                              /* AddressOfFunctions */
    MEM32(d + 32u) = 0x1200u;                              /* AddressOfNames */
    MEM32(d + 36u) = 0x1300u;                              /* AddressOfNameOrdinals */
    ok(name, hle_module_add(name, base, 0, 0, main_image) >= 0);
    return base;
}

static void put_export(uint32_t base, int idx, const char *name, const char *forwarder) {
    uint32_t frva = forwarder ? 0x1400u + 0x40u * (uint32_t)idx : 0x1F00u;
    uint32_t nrva = 0x1800u + 0x20u * (uint32_t)idx;
    MEM32(base + 0x1100u + 4u * (uint32_t)idx) = frva;
    MEM32(base + 0x1200u + 4u * (uint32_t)idx) = nrva;
    MEM16(base + 0x1300u + 2u * (uint32_t)idx) = (uint16_t)idx;
    memcpy(hle_ptr(base + nrva), name, strlen(name) + 1);
    if (forwarder) memcpy(hle_ptr(base + frva), forwarder, strlen(forwarder) + 1);
}

static uint32_t guest_str(const char *s) {
    uint32_t va = hle_va(hle_alloc((uint32_t)strlen(s) + 1));
    memcpy(hle_ptr(va), s, strlen(s) + 1);
    return va;
}

int main(void) {
    win32hle_register(test_shims);
    mach_enter();

    uint32_t a = make_image("A.dll", 7, 1);
    put_export(a, 0, "Direct", NULL);
    put_export(a, 1, "ToShim", "KERNEL32.Sleep");
    put_export(a, 2, "ToOrdinal", "KERNEL32.#13");
    put_export(a, 3, "ToB", "B.Indirect");
    put_export(a, 4, "Loop", "B.Loop");
    put_export(a, 5, "ToApi", "api-ms-win-crt-stdio-l1-1-0.api_target_func");
    put_export(a, 6, "Unknown", "NOPE.NoSuchFunction");

    uint32_t b = make_image("B.dll", 2, 0);
    put_export(b, 0, "Indirect", "KERNEL32.Sleep");
    put_export(b, 1, "Loop", "A.Loop");

    ok("direct export by name", hle_get_proc_address(a, guest_str("Direct")) == a + 0x1F00u);
    ok("direct export by ordinal", hle_get_proc_address(a, 1) == a + 0x1F00u);
    ok("forwarder to system shim by name",
       hle_get_proc_address(a, guest_str("ToShim")) == hle_resolve("Sleep"));
    ok("forwarder to system shim by ordinal",
       hle_get_proc_address(a, guest_str("ToOrdinal")) == hle_resolve("kernel32.dll#13"));
    ok("forwarder through a second image",
       hle_get_proc_address(a, guest_str("ToB")) == hle_resolve("Sleep"));
    ok("api-ms-win forwarder falls back to the global shim",
       hle_get_proc_address(a, guest_str("ToApi")) == hle_resolve("api_target_func"));
    ok("forwarder cycle is refused", hle_get_proc_address(a, guest_str("Loop")) == 0);
    ok("unanswered forwarder is refused", hle_get_proc_address(a, guest_str("Unknown")) == 0);
    ok("missing export is refused", hle_get_proc_address(a, guest_str("Missing")) == 0);
    ok("image with no export directory is refused", hle_get_proc_address(0x1234u, 1) == 0);

    if (fails) return 1;
    printf("ok    forwarder_selftest\n");
    return 0;
}
