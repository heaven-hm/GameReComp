/* resource_selftest.c - synthetic PE32 resource tree via the real module API. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

#define IMG_SIZE 0x2000u
#define RSRC_RVA 0x0800u
#define TYPE_SUB 0x0100u
#define NAME_SUB 0x0200u
#define DATA_ENT 0x0300u
#define DATA_RVA 0x0C00u
#define RT_VERSION_ID 16u
#define RES_ID 1u
#define LANG_ID 1033u

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static void put_dir(uint32_t p, int ids) {
    MEM32(p) = 0; MEM32(p + 4) = 0; MEM32(p + 8) = 0;
    MEM16(p + 12) = 0; MEM16(p + 14) = (uint16_t)ids;
}
static void put_entry(uint32_t p, uint32_t id, uint32_t off) { MEM32(p) = id; MEM32(p + 4) = off; }

int main(void) {
    win32hle_register(win32hle_module);
    mach_enter();
    uint32_t base = hle_va(hle_alloc(IMG_SIZE));
    ok("image allocated", base != 0);
    memset(hle_ptr(base), 0, IMG_SIZE);
    MEM16(base) = 0x5A4D;
    MEM32(base + 0x3Cu) = 0x80u;
    uint32_t nt = base + 0x80u;
    MEM32(nt) = 0x00004550u;
    MEM16(nt + 4) = 0x014Cu;
    MEM16(nt + 24) = 0x010Bu;                       /* PE32 */
    MEM32(nt + 24 + 0x38u) = IMG_SIZE;              /* SizeOfImage */
    MEM32(nt + 24 + 0x70u) = RSRC_RVA;              /* DataDirectory[2] */
    MEM32(nt + 24 + 0x74u) = 0x400u;

    uint32_t root = base + RSRC_RVA;
    put_dir(root, 1);
    put_entry(root + 16u, RT_VERSION_ID, 0x80000000u | TYPE_SUB);
    uint32_t type = base + RSRC_RVA + TYPE_SUB;
    put_dir(type, 1);
    put_entry(type + 16u, RES_ID, 0x80000000u | NAME_SUB);
    uint32_t name = base + RSRC_RVA + NAME_SUB;
    put_dir(name, 1);
    put_entry(name + 16u, LANG_ID, DATA_ENT);
    uint32_t de = base + RSRC_RVA + DATA_ENT;
    static const char payload[] = "VS_VERSION_INFO fixture";
    MEM32(de) = DATA_RVA;
    MEM32(de + 4u) = (uint32_t)sizeof payload;
    MEM32(de + 8u) = 0; MEM32(de + 12u) = 0;
    memcpy(hle_ptr(base + DATA_RVA), payload, sizeof payload);

    int m = hle_module_add("resource-test.exe", base, RSRC_RVA, 0x400u, 1);
    ok("module registered", m >= 0);

    uint32_t hrsrc = hle_find_resource(base, RES_ID, RT_VERSION_ID);
    ok("FindResource finds version resource", hrsrc != 0);
    uint32_t size = 0;
    uint32_t data = hle_resource_data(base, hrsrc, &size);
    ok("resource data resolved", data != 0 && size == sizeof payload);
    ok("resource bytes match", memcmp(hle_ptr(data), payload, sizeof payload) == 0);

    ok("unknown type refused", hle_find_resource(base, RES_ID, 3u) == 0);
    ok("unknown id refused", hle_find_resource(base, 99u, RT_VERSION_ID) == 0);

    /* SizeofResource/LockResource through the registered shim ABI */
    uint32_t so = hle_resolve("SizeofResource"), lo = hle_resolve("LockResource");
    ok("resource shims resolve", so && lo);
    ok("SizeofResource", hle_call_guest(so, 2, (uint32_t[]){ base, hrsrc }) == sizeof payload);
    ok("LockResource returns data VA", hle_call_guest(lo, 1, &data) == data);

    /* malformed directory counts must be rejected, not followed out of bounds */
    MEM16(root + 12) = 0xFFFFu; MEM16(root + 14) = 0xFFFFu;
    ok("malformed resource directory refused",
       hle_find_resource(base, RES_ID, RT_VERSION_ID) == 0);
    MEM16(root + 12) = 0; MEM16(root + 14) = 1;

    /* data entry outside the image must be refused */
    MEM32(de) = IMG_SIZE + 0x100u;
    ok("out-of-image resource RVA refused", hle_resource_data(base, hrsrc, &size) == 0);
    MEM32(de) = DATA_RVA;
    MEM32(de + 4u) = IMG_SIZE;                 /* size exceeds remaining image */
    ok("oversized resource refused", hle_resource_data(base, hrsrc, &size) == 0);

    mach_leave();
    printf("resource_selftest: all checks passed\n");
    return fails != 0;
}
