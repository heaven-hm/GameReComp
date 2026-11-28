/* storage_lockbytes_selftest.c - end-to-end guest-ABI check for structured
 * storage on an ILockBytes: create the byte array, create a storage on it,
 * write a stream, commit, then read the compound-file bytes back out of the
 * byte array and verify the CFB signature and the stream payload. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#define S_OKVAL              0u
#define STG_E_INVALIDPOINTER 0x80030009u
#define STGM_CREATE          0x1000u
#define STGM_READWRITE       0x2u
#define STGM_SHARE_EXCLUSIVE 0x10u

static uint32_t p_lb, p_stg, p_stm, p_n;
#define lb_out MEM32(p_lb)
#define stg    MEM32(p_stg)
#define stm    MEM32(p_stm)
#define n      MEM32(p_n)

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { fprintf(stderr, "FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int argc, const uint32_t *a) {
    return hle_call_guest(hle_resolve(name), argc, a);
}
static uint32_t vc(uint32_t obj, int slot, int nargs, const uint32_t *a) {
    uint32_t args[8] = { obj };
    for (int i = 0; i < nargs; i++) args[1 + i] = a[i];
    return hle_call_guest(MEM32(MEM32(obj) + 4u * (uint32_t)slot), nargs + 1, args);
}
static uint32_t guest_copy(const void *p, uint32_t len) {
    uint32_t va = hle_va(hle_alloc(len));
    memcpy(hle_ptr(va), p, len);
    return va;
}
static uint32_t guest_wstr(const wchar_t *s) {
    uint32_t len = 0;
    while (s[len]) len++;
    uint32_t va = hle_va(hle_alloc(2u * (len + 1u)));
    for (uint32_t i = 0; i <= len; i++) MEM16(va + 2u * i) = (uint16_t)s[i];
    return va;
}

int main(void) {
    win32hle_register(win32hle_ole32);
    win32hle_register(win32hle_storage);
    mach_enter();
    p_lb = hle_va(hle_alloc(4)); p_stg = hle_va(hle_alloc(4));
    p_stm = hle_va(hle_alloc(4)); p_n = hle_va(hle_alloc(4));

    ok("StgCreateDocfileOnILockBytes registered", hle_resolve("StgCreateDocfileOnILockBytes") != 0);
    ok("NULL lockbytes -> STG_E_INVALIDPOINTER",
       call("StgCreateDocfileOnILockBytes", 4, (uint32_t[]){ 0, 0, 0, p_stg }) == STG_E_INVALIDPOINTER);
    ok("NULL out -> STG_E_INVALIDPOINTER",
       call("StgCreateDocfileOnILockBytes", 4, (uint32_t[]){ 0x1234u, 0, 0, 0 }) == STG_E_INVALIDPOINTER);

    ok("CreateILockBytesOnHGlobal -> S_OK",
       call("CreateILockBytesOnHGlobal", 3, (uint32_t[]){ 0, 1, p_lb }) == S_OKVAL);
    uint32_t lb = lb_out;
    ok("byte array has 1 ref", lb != 0 && MEM32(lb + 4) == 1);

    uint32_t mode = STGM_CREATE | STGM_READWRITE | STGM_SHARE_EXCLUSIVE;
    ok("StgCreateDocfileOnILockBytes -> S_OK",
       call("StgCreateDocfileOnILockBytes", 4, (uint32_t[]){ lb, mode, 0, p_stg }) == S_OKVAL);
    ok("storage object returned", stg != 0 && MEM32(stg + 4) == 1);
    ok("storage holds its own byte-array ref", MEM32(lb + 4) == 2);

    /* IStorage::CreateStream(this, name, mode, reserved, reserved, &stm) */
    uint32_t name = guest_wstr(L"Alpha");
    ok("CreateStream -> S_OK",
       vc(stg, 3, 5, (uint32_t[]){ name, mode, 0, 0, p_stm }) == S_OKVAL);
    ok("stream object returned", stm != 0);

    uint32_t payload = guest_copy("PAYLOAD-12345", 13);
    /* IStream::Write(this, pv, cb, &written) */
    ok("IStream::Write -> S_OK", vc(stm, 4, 3, (uint32_t[]){ payload, 13, p_n }) == S_OKVAL && n == 13);
    /* IStorage::Commit */
    ok("IStorage::Commit -> S_OK", vc(stg, 9, 0, NULL) == S_OKVAL);

    /* Read the image back through ILockBytes::Stat / ReadAt. */
    uint32_t stat = hle_va(hle_alloc(80));
    ok("ILockBytes::Stat -> S_OK", vc(lb, 9, 2, (uint32_t[]){ stat, 0 }) == S_OKVAL);
    uint32_t bytes = MEM32(stat + 8);
    ok("byte array holds a compound file (>= 1536 bytes)", bytes >= 1536u);
    uint32_t image = hle_va(hle_alloc(bytes));
    ok("ILockBytes::ReadAt -> S_OK",
       vc(lb, 3, 5, (uint32_t[]){ 0, 0, image, bytes, p_n }) == S_OKVAL && n == bytes);
    static const uint8_t sig[8] = { 0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1 };
    ok("CFB signature", memcmp(hle_ptr(image), sig, 8) == 0);
    int found = 0;
    for (uint32_t i = 0; i + 13 <= bytes; i++)
        if (!memcmp((uint8_t *)hle_ptr(image) + i, "PAYLOAD-12345", 13)) { found = 1; break; }
    ok("stream payload present in the compound file", found);

    ok("IStream::Release -> 0", vc(stm, 2, 0, NULL) == 0);
    ok("final IStorage::Release", vc(stg, 2, 0, NULL) == 0);
    ok("byte-array ref returned to 1", MEM32(lb + 4) == 1);
    ok("final ILockBytes::Release", vc(lb, 2, 0, NULL) == 0);

    mach_leave();
    fprintf(stderr, "storage_lockbytes_selftest: %s\n", fails ? "FAILED" : "all checks passed");
    return fails != 0;
}
