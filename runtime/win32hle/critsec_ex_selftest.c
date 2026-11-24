/* critsec_ex_selftest.c - InitializeCriticalSectionEx guest ABI. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *n, int argc, const uint32_t *a) {
    return hle_call_guest(hle_resolve(n), argc, a);
}

int main(void) {
    win32hle_register(win32hle_kernel32);
    mach_enter();
    uint32_t cs = hle_va(hle_alloc(64));
    memset(hle_ptr(cs), 0xCC, 64);

    ok("init Ex succeeds", call("InitializeCriticalSectionEx", 3,
       (uint32_t[]){ cs, 123, 0 }) == 1);
    ok("LockCount initialized", MEM32(cs + 4u) == 0xFFFFFFFFu);
    ok("spin count stored", MEM32(cs + 20u) == 123u);
    ok("enter/leave works", call("EnterCriticalSection", 1, &cs) == 0 &&
                            call("LeaveCriticalSection", 1, &cs) == 0);

    ok("re-init with NO_DEBUG_INFO", call("InitializeCriticalSectionEx", 3,
       (uint32_t[]){ cs, 7, 0x01000000u }) == 1);
    ok("re-init resets spin", MEM32(cs + 20u) == 7u);

    ok("null CS fails", call("InitializeCriticalSectionEx", 3,
       (uint32_t[]){ 0, 0, 0 }) == 0);
    ok("invalid flag fails", call("InitializeCriticalSectionEx", 3,
       (uint32_t[]){ cs, 0, 0x4u }) == 0);
    ok("invalid parameter error", call("GetLastError", 0, NULL) == 87u);

    mach_leave();
    printf("critsec_ex_selftest: all checks passed\n");
    return fails != 0;
}
