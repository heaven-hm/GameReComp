/* imagelist_replace_selftest.c - guest-ABI checks for ImageList_Replace, the
 * last unregistered import of HeroCore gm7. ReactOS semantics: FALSE for an
 * invalid list handle, a negative/out-of-range index, or a bad image bitmap;
 * TRUE for a valid replacement. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { fprintf(stderr, "FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int argc, const uint32_t *a) {
    return hle_call_guest(hle_resolve(name), argc, a);
}

int main(void) {
    win32hle_register(win32hle_advapi32);
    mach_enter();
    ok("ImageList_Replace registered", hle_resolve("ImageList_Replace") != 0);

    uint32_t himl = call("ImageList_Create", 5, (uint32_t[]){ 16, 16, 0, 0, 4 });
    ok("ImageList_Create -> handle", himl != 0);
    ok("Add image 0", call("ImageList_Add", 3, (uint32_t[]){ himl, 0x100u, 0x200u }) == 0);
    ok("Add image 1", call("ImageList_Add", 3, (uint32_t[]){ himl, 0x100u, 0x200u }) == 1);
    ok("GetImageCount = 2", call("ImageList_GetImageCount", 1, (uint32_t[]){ himl }) == 2);

    ok("Replace(0) -> TRUE", call("ImageList_Replace", 4, (uint32_t[]){ himl, 0, 0x300u, 0 }) == 1);
    ok("Replace(1) -> TRUE", call("ImageList_Replace", 4, (uint32_t[]){ himl, 1, 0x300u, 0x400u }) == 1);
    ok("Replace(out of range) -> FALSE", call("ImageList_Replace", 4, (uint32_t[]){ himl, 2, 0x300u, 0 }) == 0);
    ok("Replace(-1) -> FALSE", call("ImageList_Replace", 4, (uint32_t[]){ himl, 0xFFFFFFFFu, 0x300u, 0 }) == 0);
    ok("Replace(NULL bitmap) -> FALSE", call("ImageList_Replace", 4, (uint32_t[]){ himl, 0, 0, 0 }) == 0);
    ok("Replace(bad handle) -> FALSE", call("ImageList_Replace", 4, (uint32_t[]){ 0, 0, 0x300u, 0 }) == 0);
    ok("count unchanged", call("ImageList_GetImageCount", 1, (uint32_t[]){ himl }) == 2);
    ok("ImageList_Destroy", call("ImageList_Destroy", 1, (uint32_t[]){ himl }) == 1);

    mach_leave();
    fprintf(stderr, "imagelist_replace_selftest: %s\n", fails ? "FAILED" : "all checks passed");
    return fails != 0;
}
