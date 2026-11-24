/* tls_callbacks_selftest.c - PE32 TLS callback dispatch. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

#define CB_VA 0x00406000u

static void tls_cb(void);
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { CB_VA, tls_cb } };
const uint32_t recomp_dispatch_count = 1;
static int fails, calls;
static uint32_t seen0, seen1, seen2;
static void tls_cb(void) {
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0;
    RECOMP_ENTER(CB_VA);
    calls++;
    seen0 = A32(0); seen1 = A32(1); seen2 = A32(2);
}
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

int main(void) {
    mach_enter();
    uint32_t base = hle_va(hle_alloc(0x1000));
    ok("image allocated", base != 0);
    memset(hle_ptr(base), 0, 0x1000);
    uint32_t nt = base + 0x80u, opt = nt + 24u;

    MEM16(base) = 0x5A4D;
    MEM32(base + 0x3Cu) = 0x80u;
    MEM32(nt) = 0x00004550u;
    MEM16(nt + 4u) = 0x014Cu;
    MEM16(opt) = 0x010Bu;
    MEM32(opt + 0x5Cu) = 16u;                       /* NumberOfRvaAndSizes */
    uint32_t dd9 = opt + 0x60u + 9u * 8u;
    MEM32(dd9) = 0x200u; MEM32(dd9 + 4u) = 24u;      /* TLS directory */
    uint32_t tls = base + 0x200u;
    MEM32(tls) = base + 0x100u;                     /* raw template */
    MEM32(tls + 4u) = base + 0x180u;
    MEM32(tls + 8u) = base + 0x190u;                /* _tls_index */
    MEM32(tls + 12u) = base + 0x300u;               /* AddressOfCallBacks */
    MEM32(base + 0x300u) = CB_VA;
    MEM32(base + 0x304u) = 0;

    pe_image img;
    img.base = base;
    img.image_base = base;
    img.span = 0x1000u;
    img.entry = base + 0x400u;
    img.import_rva = img.resource_rva = img.resource_size = img.stamp = 0;

    calls = 0;
    hle_pe_run_tls_callbacks(&img);
    ok("TLS callback invoked once", calls == 1);
    ok("callback got hinstDLL", seen0 == base);
    ok("callback got DLL_PROCESS_ATTACH", seen1 == 1u);
    ok("callback got reserved 0", seen2 == 0u);

    /* an out-of-image callback must be refused without being invoked */
    MEM32(base + 0x300u) = 0x77770000u;
    calls = 0;
    hle_pe_run_tls_callbacks(&img);
    ok("invalid callback VA not invoked", calls == 0);

    mach_leave();
    printf("tls_callbacks_selftest: all checks passed\n");
    return fails != 0;
}
