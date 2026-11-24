/* companion_exports_selftest.c - ordinal and forwarded companion exports. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static const recomp_export_t ex_a[] = {
    { "Named",   0x10001000u, 7u },
    { NULL,      0x10002000u, 9u },   /* ordinal-only export */
    { "B.dll.Forwarded", 0xDEAD0000u, 10u }, /* forwarder: VA is a string */
    { "B.dll#11",        0xDEAD0001u, 11u }, /* forwarded by ordinal */
};
static const recomp_export_t ex_b[] = {
    { "Forwarded", 0x20001000u, 11u },
    { NULL,        0x20002000u, 12u },
};
static const recomp_companion_t companions[] = {
    { "A.dll", 0x10000000u, NULL, NULL, 0, ex_a, 4 },
    { "B.dll", 0x20000000u, NULL, NULL, 0, ex_b, 2 },
};
const recomp_companion_t *const recomp_companion_list = companions;
const uint32_t recomp_companion_count = 2;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

int main(void) {
    ok("named export", recomp_companion_resolve("A.dll", "Named") == 0x10001000u);
    ok("ordinal by name", recomp_companion_resolve("A.dll", "A.dll#7") == 0x10001000u);
    ok("ordinal-only export", recomp_companion_resolve("A.dll", "A.dll#9") == 0x10002000u);
    ok("case-insensitive dll", recomp_companion_resolve("a.DLL", "a.dll#9") == 0x10002000u);
    ok("wrong dll for ordinal", recomp_companion_resolve("B.dll", "A.dll#9") == 0);
    ok("unknown ordinal", recomp_companion_resolve("A.dll", "A.dll#99") == 0);
    ok("forwarded by name", recomp_companion_resolve("A.dll", "B.dll.Forwarded") == 0x20001000u);
    ok("forwarded by ordinal", recomp_companion_resolve("A.dll", "A.dll#11") == 0x20001000u);
    ok("direct B ordinal", recomp_companion_resolve("B.dll", "B.dll#12") == 0x20002000u);

    printf("companion_exports_selftest: all checks passed\n");
    return fails != 0;
}
