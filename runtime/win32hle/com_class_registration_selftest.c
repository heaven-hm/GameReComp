/* com_class_registration_selftest.c - deterministic guest COM class-factory
 * fixture for ole32.c.
 *
 * A lifted game that serves its own COM classes (Yuri's Revenge's saved
 * classes/locomotors) calls CoRegisterClassObject with an IUnknown* and later
 * expects CoCreateInstance to route through that class object's IClassFactory:
 *
 *   class object (guest VA):  +0 vtable VA, +4 refcount, +8 state ...
 *   vtable (guest VA):        slot0 QueryInterface, 1 AddRef, 2 Release,
 *                             3 CreateInstance, 4 LockServer
 *
 * Each vtable slot holds the guest VA of a lifted function (here a registered
 * shim). Calling slot N therefore requires TWO guest-memory reads:
 * MEM32(MEM32(obj) + 4*N), not MEM32(obj + 4*N).
 *
 * This fixture builds a real-shaped guest IClassFactory and drives the full
 * register -> CoCreateInstance -> revoke path. It records every guest callback
 * so a wrong vtable dereference (or a wrong stdcall argument order) is visible
 * without a live game.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

#define S_OK_VALUE            0u
#define E_NOINTERFACE_VALUE   0x80004002u
#define E_POINTER_VALUE       0x80004003u
#define CLSCTX_INPROC_SERVER  1u
#define REGCLS_MULTIPLEUSE    1u

/* IID_IClassFactory {00000001-0000-0000-C000-000000000046}, REFIID byte order. */
static const uint8_t IID_IClassFactory[16] = {
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46
};
/* Arbitrary, fixed GUIDs for the fixture; only equality/identity matters. */
static const uint8_t TEST_CLSID[16] = {
    0xD4, 0xC3, 0xB2, 0xA1, 0x34, 0x12, 0x78, 0x56,
    0x9A, 0xBC, 0xDE, 0xF0, 0x12, 0x34, 0x56, 0x78
};
static const uint8_t TEST_IID[16] = {
    0x44, 0x33, 0x22, 0x11, 0x66, 0x55, 0x88, 0x77,
    0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00
};
static const uint8_t BOGUS_IID[16] = {
    0x11, 0x22, 0x33, 0x44, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x99
};

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

/* Observed guest callbacks and the arguments CreateInstance received. */
static int qi_calls, addref_calls, release_calls, create_calls, lock_calls;
static int wrong_riid, wrong_outer;
static uint32_t created_object;      /* guest VA the factory hands back */
static uint32_t iface_iid;           /* guest VA of IID_IClassFactory */

/* --- the guest's IClassFactory, implemented as named HLE shims so that
 * hle_call_guest can resolve their synthetic VAs exactly as it would a lifted
 * function. --- */
static void tf_QueryInterface(void) {                /* (this, iid, &out) */
    uint32_t self = A32(0), iid = A32(1), out = A32(2);
    qi_calls++;
    if (!iid || !out) { if (out) MEM32(out) = 0; RET(E_POINTER_VALUE, 3); }
    const uint8_t *want = (const uint8_t *)hle_ptr(iid);
    if (!iface_iid || memcmp(want, hle_ptr(iface_iid), 16) != 0) {
        wrong_riid++;
        MEM32(out) = 0;
        RET(E_NOINTERFACE_VALUE, 3);
    }
    MEM32(out) = self;
    MEM32(self + 4)++;
    RET(S_OK_VALUE, 3);
}
static void tf_AddRef(void) {                        /* (this) */
    addref_calls++;
    RET(++MEM32(A32(0) + 4), 1);
}
static void tf_Release(void) {                       /* (this) */
    release_calls++;
    RET(--MEM32(A32(0) + 4), 1);
}
static void tf_CreateInstance(void) {                /* (this, outer, riid, &out) */
    create_calls++;
    if (A32(1) != 0u) wrong_outer++;
    const uint8_t *want = A32(2) ? (const uint8_t *)hle_ptr(A32(2)) : NULL;
    if (!want || memcmp(want, TEST_IID, 16) != 0) {
        wrong_riid++;
        if (A32(3)) MEM32(A32(3)) = 0;
        RET(E_NOINTERFACE_VALUE, 4);
    }
    if (A32(3)) MEM32(A32(3)) = created_object;
    RET(S_OK_VALUE, 4);
}
static void tf_LockServer(void) {                    /* (this, fLock) */
    lock_calls++;
    RET(S_OK_VALUE, 2);
}

static const win32hle_shim tf_shims[] = {
    { "GR4TF::QueryInterface", tf_QueryInterface },
    { "GR4TF::AddRef",         tf_AddRef },
    { "GR4TF::Release",        tf_Release },
    { "GR4TF::CreateInstance", tf_CreateInstance },
    { "GR4TF::LockServer",     tf_LockServer },
    { 0, 0 }
};

static uint32_t guest_copy(const void *src, uint32_t n) {
    uint32_t va = hle_va(hle_alloc(n));
    memcpy(hle_ptr(va), src, n);
    return va;
}
static uint32_t call(const char *name, int argc, const uint32_t *args) {
    uint32_t fn = hle_resolve(name);
    if (!fn) { printf("FAIL %s not registered\n", name); fails++; return 0xFFFFFFFFu; }
    return hle_call_guest(fn, argc, args);
}

int main(void) {
    recomp_host_init();
    win32hle_register(tf_shims);
    mach_enter();

    ok("CoRegisterClassObject registered", hle_resolve("CoRegisterClassObject") != 0);
    ok("CoRevokeClassObject registered", hle_resolve("CoRevokeClassObject") != 0);
    ok("CoCreateInstance registered", hle_resolve("CoCreateInstance") != 0);

    /* A COM object: [0] = vtable VA, [4] = refcount, [8] = host state. */
    uint32_t vt = hle_va(hle_alloc(5u * 4u));
    for (unsigned i = 0; i < 5; i++) MEM32(vt + 4u * i) = hle_resolve(tf_shims[i].name);
    ok("all IClassFactory shims resolved",
       MEM32(vt) && MEM32(vt + 4) && MEM32(vt + 8) && MEM32(vt + 12) && MEM32(vt + 16));

    uint32_t factory = hle_va(hle_alloc(12u));
    MEM32(factory) = vt;                 /* one dereference to reach the vtable */
    MEM32(factory + 4) = 1u;             /* initial reference */
    MEM32(factory + 8) = 0u;

    created_object = hle_va(hle_alloc(8u));
    MEM32(created_object) = 0x20161123u;

    uint32_t clsid = guest_copy(TEST_CLSID, 16);
    uint32_t iid = guest_copy(TEST_IID, 16);
    iface_iid = guest_copy(IID_IClassFactory, 16);
    uint32_t bad_iid = guest_copy(BOGUS_IID, 16);
    uint32_t cookie_va = hle_va(hle_alloc(4));
    MEM32(cookie_va) = 0;

    /* Register: COM AddRefs the class object and hands back a cookie. */
    uint32_t hr = call("CoRegisterClassObject", 5, (uint32_t[]){
        clsid, factory, CLSCTX_INPROC_SERVER, REGCLS_MULTIPLEUSE, cookie_va
    });
    ok("CoRegisterClassObject returns S_OK", hr == S_OK_VALUE);
    ok("registration issued a cookie", MEM32(cookie_va) != 0u);
    ok("registration called AddRef once", addref_calls == 1);
    ok("factory refcount is 2 after registration", MEM32(factory + 4) == 2u);

    /* The core regression: the guest class must be reachable by CLSID. */
    uint32_t out = hle_va(hle_alloc(4));
    MEM32(out) = 0xDEADBEEFu;
    hr = call("CoCreateInstance", 5, (uint32_t[]){
        clsid, 0u, CLSCTX_INPROC_SERVER, iid, out
    });
    ok("CoCreateInstance returns S_OK", hr == S_OK_VALUE);
    ok("class factory QueryInterface called once", qi_calls == 1);
    ok("class factory CreateInstance called once", create_calls == 1);
    ok("CreateInstance got pUnkOuter == NULL", wrong_outer == 0);
    ok("CreateInstance got the requested IID", wrong_riid == 0);
    ok("created object returned to the guest", MEM32(out) == created_object);
    ok("class factory Release called once", release_calls == 1);
    /* Registration's AddRef stays held: QI's matching AddRef/Release cancel. */
    ok("factory refcount is 2 after CoCreateInstance", MEM32(factory + 4) == 2u);

    /* Unsupported IID: the factory's QI still succeeds (it is the class
     * factory itself), but CreateInstance refuses the IID and the HRESULT is
     * propagated with a cleared out pointer. */
    MEM32(out) = 0xDEADBEEFu;
    hr = call("CoCreateInstance", 5, (uint32_t[]){
        clsid, 0u, CLSCTX_INPROC_SERVER, bad_iid, out
    });
    ok("unsupported IID returns E_NOINTERFACE", hr == E_NOINTERFACE_VALUE);
    ok("unsupported IID clears the output pointer", MEM32(out) == 0u);
    ok("failed CreateInstance still released the factory", release_calls == 2);
    ok("failed CoCreateInstance kept the registration ref", MEM32(factory + 4) == 2u);

    /* An unregistered CLSID keeps the REGDB_E_CLASSNOTREG contract. */
    uint32_t missing = guest_copy(BOGUS_IID, 16);
    MEM32(out) = 0xDEADBEEFu;
    hr = call("CoCreateInstance", 5, (uint32_t[]){
        missing, 0u, CLSCTX_INPROC_SERVER, iid, out
    });
    ok("unregistered CLSID is REGDB_E_CLASSNOTREG", hr == 0x80040154u);
    ok("unregistered CLSID clears the output pointer", MEM32(out) == 0u);

    /* Revoke releases only registration's reference; the fixture's own
     * initial reference stays with the guest. */
    uint32_t cookie = MEM32(cookie_va);
    hr = call("CoRevokeClassObject", 1, (uint32_t[]){ cookie });
    ok("CoRevokeClassObject returns S_OK", hr == S_OK_VALUE);
    ok("revoke called Release once more", release_calls == 3);
    ok("factory refcount reaches its initial 1", MEM32(factory + 4) == 1u);
    ok("a second revoke fails", call("CoRevokeClassObject", 1,
        (uint32_t[]){ cookie }) == 0x80040011u);
    ok("a revoked CLSID is no longer creatable", call("CoCreateInstance", 5,
        (uint32_t[]){ clsid, 0u, CLSCTX_INPROC_SERVER, iid, out }) == 0x80040154u);
    ok("LockServer was never needed", lock_calls == 0);

    mach_leave();
    printf("com_class_registration_selftest: %s (%d failures)\n",
           fails ? "FAILED" : "all checks passed", fails);
    return fails != 0;
}
