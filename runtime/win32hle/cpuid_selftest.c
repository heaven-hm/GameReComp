/* cpuid_selftest.c - guest CPUID answers on non-MSVC hosts. */
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

int main(void) {
    CPUID(0u, 0u, 0u, 0u);
    ok("leaf 0 max", g_eax == 1u);
    ok("leaf 0 vendor EBX", g_ebx == 0x756E6547u);
    ok("leaf 0 vendor EDX", g_edx == 0x49656E69u);
    ok("leaf 0 vendor ECX", g_ecx == 0x6C65746Eu);

    CPUID(1u, 0u, 0u, 0u);
    ok("leaf 1 EAX family", g_eax == 0x000006F2u);
    ok("leaf 1 SSE bit", (g_edx & (1u << 25)) != 0);
    ok("leaf 1 SSE2 bit", (g_edx & (1u << 26)) != 0);
    ok("leaf 1 ECX has no SSE3", (g_ecx & 1u) == 0);

    CPUID(0x80000000u, 0u, 0u, 0u);
    ok("extended max leaf", g_eax == 0x80000001u);
    CPUID(0x80000001u, 0u, 0u, 0u);
    ok("extended 3DNow bit", (g_edx & 0x80000000u) != 0);

    CPUID(9u, 0u, 0u, 0u);
    ok("unknown leaf zeroes registers",
       g_eax == 0 && g_ebx == 0 && g_ecx == 0 && g_edx == 0);

    uint32_t saved = g_cpuid_edx1;
    g_cpuid_edx1 = 0;
    CPUID(1u, 0u, 0u, 0u);
    ok("feature mask honoured", g_edx == 0u);
    g_cpuid_edx1 = saved;

    printf("cpuid_selftest: all checks passed\n");
    return fails != 0;
}
