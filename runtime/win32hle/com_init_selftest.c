/* com_init_selftest.c - COM initialisation state and apartment-model checks. */
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
    win32hle_register(win32hle_ole32);
    mach_enter();
    ok("CoInitializeEx registered", hle_resolve("CoInitializeEx") != 0);

    ok("first CoInitialize -> S_OK", call("CoInitialize", 1, (uint32_t[]){ 0 }) == 0u);
    ok("second CoInitialize -> S_FALSE", call("CoInitialize", 1, (uint32_t[]){ 0 }) == 1u);
    ok("changed model refused", call("CoInitializeEx", 2,
       (uint32_t[]){ 0, 0u }) == 0x80010106u);
    ok("same model -> S_FALSE", call("CoInitializeEx", 2,
       (uint32_t[]){ 0, 0x2u }) == 1u);
    call("CoUninitialize", 0, NULL);
    call("CoUninitialize", 0, NULL);
    call("CoUninitialize", 0, NULL);

    ok("reinit after unwind", call("CoInitializeEx", 2,
       (uint32_t[]){ 0, 0u }) == 0u);
    ok("reserved must be NULL (Ex)", call("CoInitializeEx", 2,
       (uint32_t[]){ 0x1234u, 0u }) == 0x80070057u);
    ok("reserved must be NULL (A)", call("CoInitialize", 1,
       (uint32_t[]){ 0x1234u }) == 0x80070057u);
    call("CoUninitialize", 0, NULL);

    mach_leave();
    printf("com_init_selftest: all checks passed\n");
    return fails != 0;
}
