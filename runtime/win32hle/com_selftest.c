/* com_selftest.c - shared IUnknown / COM identity infrastructure fixture. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

#define S_OK_VALUE 0u
#define E_NOINTERFACE 0x80004002u
#define E_POINTER_VALUE 0x80004003u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

static const uint8_t IID_A[16] = { 0x11,0x22,0x33,0x44,0,0,0,0,0,0,0,0,0,0,0,0xAA };
static const uint8_t IID_B[16] = { 0xBB,0x22,0x33,0x44,0,0,0,0,0,0,0,0,0,0,0,0xBB };
static const uint8_t IID_X[16] = { 0xFF,0xFF,0xFF,0xFF,0,0,0,0,0,0,0,0,0,0,0,0xFF };

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

int main(void) {
    recomp_host_init();
    mach_enter();

    static const win32hle_shim methods[] = {
        { "ComTest::QueryInterface", hle_com_QueryInterface },
        { "ComTest::AddRef",         hle_com_AddRef },
        { "ComTest::Release",        hle_com_Release },
        { 0, 0 }
    };
    uint32_t vt = hle_com_vtable(methods);
    ok("vtable is a 32-bit guest VA", vt != 0 && vt <= 0xFFFFFFFFu);
    ok("vtable slot 0 = QueryInterface", MEM32(vt) == hle_resolve("ComTest::QueryInterface"));
    ok("vtable slot 1 = AddRef", MEM32(vt + 4u) == hle_resolve("ComTest::AddRef"));
    ok("vtable slot 2 = Release", MEM32(vt + 8u) == hle_resolve("ComTest::Release"));

    uint32_t objA = hle_com_new(vt, 16);
    uint32_t objB = hle_com_new(vt, 16);
    ok("object vtab/refs", MEM32(objA) == vt && MEM32(objA + 4u) == 1);
    hle_com_bind_iid(objA, IID_A, objA);
    hle_com_bind_iid(objA, IID_B, objB);   /* second interface on the same object */

    uint32_t qi = hle_resolve("ComTest::QueryInterface");
    uint32_t addref = hle_resolve("ComTest::AddRef");
    uint32_t release = hle_resolve("ComTest::Release");
    ok("IUnknown shims resolve", qi && addref && release);

    uint32_t iid_va = hle_va(hle_alloc(16));
    uint32_t out_va = hle_va(hle_alloc(4));

    memcpy(hle_ptr(iid_va), IID_A, 16);
    MEM32(out_va) = 0;
    ok("QI(IID_A) succeeds", hle_call_guest(qi, 3, (uint32_t[]){ objA, iid_va, out_va }) == S_OK_VALUE);
    ok("QI(IID_A) interface identity", MEM32(out_va) == objA);
    ok("QI(IID_A) AddRef", MEM32(objA + 4u) == 2);
    hle_call_guest(release, 1, &objA);     /* undo the QI reference */

    memcpy(hle_ptr(iid_va), IID_B, 16);
    MEM32(out_va) = 0;
    ok("QI(IID_B) succeeds", hle_call_guest(qi, 3, (uint32_t[]){ objA, iid_va, out_va }) == S_OK_VALUE);
    ok("QI(IID_B) returns second interface", MEM32(out_va) == objB);
    ok("QI(IID_B) AddRef on that interface", MEM32(objB + 4u) == 2);
    hle_call_guest(release, 1, &objB);

    memcpy(hle_ptr(iid_va), IID_X, 16);
    MEM32(out_va) = 0xDEADBEEFu;
    ok("unsupported IID fails", hle_call_guest(qi, 3, (uint32_t[]){ objA, iid_va, out_va }) == E_NOINTERFACE);
    ok("unsupported IID NULLs output", MEM32(out_va) == 0);

    ok("QI(NULL iid) is E_POINTER",
       hle_call_guest(qi, 3, (uint32_t[]){ objA, 0, out_va }) == E_POINTER_VALUE);
    ok("QI(NULL out) is E_POINTER",
       hle_call_guest(qi, 3, (uint32_t[]){ objA, iid_va, 0 }) == E_POINTER_VALUE);

    ok("AddRef increments", hle_call_guest(addref, 1, &objA) == 2);
    ok("Release decrements", hle_call_guest(release, 1, &objA) == 1);
    ok("final Release destroys object", hle_call_guest(release, 1, &objA) == 0);
    ok("final Release unbinds IIDs",
       hle_call_guest(qi, 3, (uint32_t[]){ objB, iid_va, out_va }) == S_OK_VALUE);
    hle_call_guest(release, 1, &objB);

    /* Integration with an existing DirectX interface: a DirectSound object
     * created through the registered COM path must answer its real IID and
     * refuse an unsupported one, using the same shared QI shim. */
    uint32_t ds_create_va = hle_resolve("DirectSoundCreate");
    if (ds_create_va) {
        uint32_t ds_out = hle_va(hle_alloc(4));
        uint32_t rc = hle_call_guest(ds_create_va, 3, (uint32_t[]){ 0, ds_out, 0 });
        if (rc == 0 && MEM32(ds_out)) {
            uint32_t ds = MEM32(ds_out);
            static const uint8_t IID_DS[16] = {
                0x83,0xFA,0x9A,0x27,0x8E,0x49,0xD0,0x11,
                0x98,0x28,0x00,0xC0,0x4F,0xC2,0x97,0x21
            };
            memcpy(hle_ptr(iid_va), IID_DS, 16);
            MEM32(out_va) = 0;
            ok("DirectSound QI(IID_IDirectSound)",
               hle_call_guest(qi, 3, (uint32_t[]){ ds, iid_va, out_va }) == S_OK_VALUE &&
               MEM32(out_va) == ds);
            memcpy(hle_ptr(iid_va), IID_X, 16);
            ok("DirectSound QI(unsupported)",
               hle_call_guest(qi, 3, (uint32_t[]){ ds, iid_va, out_va }) == E_NOINTERFACE);
            hle_call_guest(release, 1, &ds);
        } else {
            printf("SKIP DirectSound COM integration: no audio device in this environment\n");
        }
    }

    mach_leave();
    printf("com_selftest: all checks passed\n");
    return fails != 0;
}
