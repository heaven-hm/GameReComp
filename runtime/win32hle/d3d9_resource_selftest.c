/* d3d9_resource_selftest.c - deterministic frontend-only evidence for the
 * D3D9 CreateVertexBuffer / CreateIndexBuffer ABI.
 *
 * BEFORE this change both slots were plain D3DERR_NOTAVAILABLE stubs: a guest
 * got no object, could not call QueryInterface/GetDevice/GetDesc and could not
 * observe a resource lifetime. AFTER the change both slots return real guest
 * COM objects with the public 14-entry IDirect3DVertexBuffer9 /
 * IDirect3DIndexBuffer9 vtables, reference counting and a device reference
 * held for the resource's lifetime. Nothing here rasterises: Lock/Unlock and
 * every rasteriser-dependent method remain honest D3DERR_NOTAVAILABLE.
 *
 * The fixture drives the registered shims the way lifted code does (return
 * address + args on a guest stack) in addition to hle_call_guest, because
 * hle_call_guest restores the register file and would hide a wrong stdcall
 * pop. Sources of the public ABI: wine-mirror/wine include/d3d9.h and
 * include/d3d9types.h at 97027f879ba9a36dbe594720d4710818536e5433. No Wine
 * code is copied; only names, order, argument counts and constant values are
 * encoded here. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

#define D3D_OK_VALUE                  0u
#define E_POINTER_VALUE               0x80004003u
#define E_NOINTERFACE_VALUE           0x80004002u
#define D3DERR_INVALIDCALL_VALUE      0x8876086Cu
#define D3DERR_NOTAVAILABLE_VALUE     0x8876086Au
#define D3D_SDK_VERSION_VALUE         32u
#define D3DRTYPE_VERTEXBUFFER_VALUE   6u
#define D3DRTYPE_INDEXBUFFER_VALUE    7u
#define D3DFMT_VERTEXDATA_VALUE       100u
#define D3DFMT_INDEX16_VALUE          101u
#define D3DFMT_INDEX32_VALUE          102u
#define D3DPOOL_SCRATCH_VALUE         3u

static const uint8_t IID_UNKNOWN[16] = {
    0, 0, 0, 0, 0, 0, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46
};
static const uint8_t IID_RESOURCE9[16] = {
    0x5D, 0xC0, 0xEE, 0x05, 0x7D, 0x8F, 0x62, 0x43,
    0xB9, 0x99, 0xD1, 0xBA, 0xF3, 0x57, 0xC7, 0x04
};
static const uint8_t IID_VB9[16] = {
    0xB5, 0xB1, 0x4B, 0xB6, 0x70, 0xFD, 0xF6, 0x4D,
    0xBF, 0x91, 0x19, 0xD0, 0xA1, 0x24, 0x55, 0xE3
};
static const uint8_t IID_IB9[16] = {
    0x5E, 0xD6, 0x9D, 0x7C, 0xF7, 0xD3, 0x29, 0x45,
    0xAC, 0xEE, 0x78, 0x58, 0x30, 0xAC, 0xDE, 0x35
};
static const uint8_t IID_DEVICE9[16] = {
    0x96, 0x3B, 0x22, 0xD0, 0x7A, 0xBF, 0xFD, 0x43,
    0x92, 0xBD, 0xA4, 0x3B, 0x0D, 0x82, 0xB9, 0xEB
};
static const uint8_t IID_BOGUS[16] = {
    0x11, 0x22, 0x33, 0x44, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x99
};

/* Standalone host: no lifted dispatch table is linked into this build. */
const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t vtcall(uint32_t vt, unsigned slot, int argc, const uint32_t *args) {
    return hle_call_guest(MEM32(vt + 4u * slot), argc, args);
}
/* Direct shim call with a synthetic guest stack: measure exact stdcall pops. */
static uint32_t shim_pops(const char *name, int argc, const uint32_t *args,
                          uint32_t *out_eax) {
    uint32_t va = hle_resolve(name);
    recomp_func_t fn = va ? recomp_lookup_import(va) : 0;
    if (!fn) return 0xFFFFFFFFu;
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t scratch = hle_va(hle_alloc(64));
    g_esp = scratch + 32;
    for (int i = argc - 1; i >= 0; --i) { g_esp -= 4; MEM32(g_esp) = args[i]; }
    g_esp -= 4; MEM32(g_esp) = RECOMP_RETADDR;
    uint32_t esp_in = g_esp;
    fn();
    uint32_t words = (g_esp - esp_in) / 4u;
    if (out_eax) *out_eax = g_eax;
    g_esp = saved_esp; g_eax = saved_eax;
    return words;
}
static uint32_t copy_iid(const uint8_t iid[16]) {
    uint32_t va = hle_va(hle_alloc(16));
    memcpy(hle_ptr(va), iid, 16);
    return va;
}
static void zero32(uint32_t va, unsigned words) {
    for (unsigned i = 0; i < words; i++) MEM32(va + 4u * i) = 0u;
}
static uint32_t alloc_u32(uint32_t poison) {
    uint32_t va = hle_va(hle_alloc(4));
    MEM32(va) = poison;
    return va;
}

static void check_resource_qi(const char *label, uint32_t vt, uint32_t self,
                              const uint8_t *iid, uint32_t expect, uint32_t *out) {
    uint32_t hr = vtcall(vt, 0, 3,
        (uint32_t[]){ self, copy_iid(iid), *out });
    ok(label, hr == expect);
    if (expect == D3D_OK_VALUE) {
        ok("QI returns resource identity", MEM32(*out) == self);
        vtcall(vt, 2, 1, &self);                       /* undo the QI reference */
    } else {
        ok("QI clears output on failure", MEM32(*out) == 0u);
    }
}

int main(void) {
    recomp_host_init();
    win32hle_register(win32hle_d3d9);
    mach_enter();

    printf("d3d9_resource_selftest: frontend resource ABI (no rasteriser)\n");

    uint32_t d3d = hle_call_guest(hle_resolve("Direct3DCreate9"), 1,
                                  (uint32_t[]){ D3D_SDK_VERSION_VALUE });
    ok("factory returns object", d3d != 0 && d3d != 0xFFFFFFFFu);
    uint32_t vt = MEM32(d3d);
    uint32_t dev_out = alloc_u32(0xDEADBEEFu);
    ok("CreateDevice", vtcall(vt, 16, 7,
        (uint32_t[]){ d3d, 0u, 0u, 0u, 1u, 1u, dev_out }) == D3D_OK_VALUE);
    uint32_t dev = MEM32(dev_out);
    ok("device object created", dev != 0 && dev != 0xDEADBEEFu);
    uint32_t devvt = MEM32(dev);
    ok("device refcount 1", MEM32(dev + 4u) == 1u);

    /* Device slots 26 and 27 must be the real creation shims, not another
     * d3d9_na7 slot. Behaviour below confirms the slot actually creates. */
    ok("CreateVertexBuffer slot named",
       MEM32(devvt + 4u * 26u) == hle_resolve("D3D9::Device::CreateVertexBuffer"));
    ok("CreateIndexBuffer slot named",
       MEM32(devvt + 4u * 27u) == hle_resolve("D3D9::Device::CreateIndexBuffer"));
    ok("CreateTexture stays NOTAVAILABLE (no rasteriser)", vtcall(devvt, 23, 9,
        (uint32_t[]){ dev, 64u, 64u, 1u, 0u, 22u, 0u, dev_out, 0u })
        == D3DERR_NOTAVAILABLE_VALUE);
    ok("CreateTexture still pops ret + this + 8 args", shim_pops(
        "D3D9::Device::CreateTexture", 9,
        (uint32_t[]){ dev, 64u, 64u, 1u, 0u, 22u, 0u, dev_out, 0u }, 0) == 10u);

    static const char *const vb_names[14] = {
        "QueryInterface", "AddRef", "Release", "GetDevice", "SetPrivateData",
        "GetPrivateData", "FreePrivateData", "SetPriority", "GetPriority",
        "PreLoad", "GetType", "Lock", "Unlock", "GetDesc"
    };

    /* ---- BEFORE: a valid CreateVertexBuffer call answered NOTAVAILABLE and
     * left the output pointer untouched. AFTER: D3D_OK + a frontend object. */
    uint32_t vb_out = alloc_u32(0xDEADBEEFu);
    const uint32_t vb_args[7] = { dev, 256u, 8u, 0x204u, 0u, vb_out, 0u };
    uint32_t hr = vtcall(devvt, 26, 7, vb_args);
    if (hr != D3D_OK_VALUE || MEM32(vb_out) == 0u || MEM32(vb_out) == 0xDEADBEEFu) {
        printf("before evidence: CreateVertexBuffer valid call = 0x%08X (object ptr 0x%08X)\n",
               hr, MEM32(vb_out));
        ok("CreateVertexBuffer returns D3D_OK frontend object", 0);
    } else {
        ok("CreateVertexBuffer returns D3D_OK frontend object", 1);
        uint32_t vb = MEM32(vb_out);
        uint32_t vbvt = MEM32(vb);
        ok("vertex buffer vtable set", vbvt != 0);
        ok("vertex buffer refcount 1", MEM32(vb + 4u) == 1u);
        /* A resource holds a device reference: creation raises it to 2. */
        ok("resource holds device reference", MEM32(dev + 4u) == 2u);
        for (unsigned i = 0; i < 14; i++) {
            char name[80];
            snprintf(name, sizeof name, "D3D9::VertexBuffer::%s", vb_names[i]);
            ok("vertex buffer vtable slot", MEM32(vbvt + 4u * i) == hle_resolve(name));
        }
        uint32_t out = alloc_u32(0xDEADBEEFu);
        check_resource_qi("VB QI(IUnknown)", vbvt, vb, IID_UNKNOWN, D3D_OK_VALUE, &out);
        out = alloc_u32(0xDEADBEEFu);
        check_resource_qi("VB QI(IDirect3DResource9)", vbvt, vb, IID_RESOURCE9, D3D_OK_VALUE, &out);
        out = alloc_u32(0xDEADBEEFu);
        check_resource_qi("VB QI(IDirect3DVertexBuffer9)", vbvt, vb, IID_VB9, D3D_OK_VALUE, &out);
        out = alloc_u32(0xDEADBEEFu);
        check_resource_qi("VB QI(IDirect3DIndexBuffer9)", vbvt, vb, IID_IB9, E_NOINTERFACE_VALUE, &out);
        out = alloc_u32(0xDEADBEEFu);
        check_resource_qi("VB QI(IDirect3DDevice9)", vbvt, vb, IID_DEVICE9, E_NOINTERFACE_VALUE, &out);
        out = alloc_u32(0xDEADBEEFu);
        check_resource_qi("VB QI(bogus)", vbvt, vb, IID_BOGUS, E_NOINTERFACE_VALUE, &out);
        out = alloc_u32(0xDEADBEEFu);
        ok("VB QI(NULL iid) is E_POINTER", vtcall(vbvt, 0, 3,
            (uint32_t[]){ vb, 0u, out }) == E_POINTER_VALUE);
        ok("VB QI(NULL out) is E_POINTER", vtcall(vbvt, 0, 3,
            (uint32_t[]){ vb, copy_iid(IID_VB9), 0u }) == E_POINTER_VALUE);

        /* GetDevice AddRefs the owner, as D3D9 requires. */
        out = alloc_u32(0xDEADBEEFu);
        ok("VB GetDevice", vtcall(vbvt, 3, 2,
            (uint32_t[]){ vb, out }) == D3D_OK_VALUE);
        ok("VB GetDevice returns owner", MEM32(out) == dev);
        ok("VB GetDevice AddRefs owner", MEM32(dev + 4u) == 3u);
        ok("undo GetDevice reference", vtcall(devvt, 2, 1, &dev) == 2u);
        ok("owner refcount restored", MEM32(dev + 4u) == 2u);
        ok("GetDevice pops ret + this + 1 arg", shim_pops(
            "D3D9::VertexBuffer::GetDevice", 2,
            (uint32_t[]){ vb, out }, 0) == 3u);
        vtcall(devvt, 2, 1, &dev);                     /* undo the shim_pops AddRef */

        /* Descriptive resource state needs no rasteriser. */
        ok("VB priority starts 0", vtcall(vbvt, 8, 1, &vb) == 0u);
        ok("VB SetPriority returns old", vtcall(vbvt, 7, 2,
            (uint32_t[]){ vb, 7u }) == 0u);
        ok("VB GetPriority returns new", vtcall(vbvt, 8, 1, &vb) == 7u);
        ok("SetPriority pops ret + this + 1 arg", shim_pops(
            "D3D9::VertexBuffer::SetPriority", 2,
            (uint32_t[]){ vb, 0u }, 0) == 3u);
        ok("VB GetType", vtcall(vbvt, 10, 1, &vb) == D3DRTYPE_VERTEXBUFFER_VALUE);
        ok("VB PreLoad is void", vtcall(vbvt, 9, 1, &vb) == 0u);

        uint32_t desc = hle_va(hle_alloc(28));
        memset(hle_ptr(desc), 0xCD, 28);
        ok("VB GetDesc", vtcall(vbvt, 13, 2,
            (uint32_t[]){ vb, desc }) == D3D_OK_VALUE);
        ok("VB desc Format=D3DFMT_VERTEXDATA", MEM32(desc) == D3DFMT_VERTEXDATA_VALUE);
        ok("VB desc Type=D3DRTYPE_VERTEXBUFFER", MEM32(desc + 4u) == D3DRTYPE_VERTEXBUFFER_VALUE);
        ok("VB desc Usage", MEM32(desc + 8u) == 8u);
        ok("VB desc Pool", MEM32(desc + 12u) == 0u);
        ok("VB desc Size", MEM32(desc + 16u) == 256u);
        ok("VB desc FVF", MEM32(desc + 20u) == 0x204u);
        ok("VB desc does not overrun", MEM32(desc + 24u) == 0xCDCDCDCDu);
        ok("GetDesc(NULL) is E_POINTER", vtcall(vbvt, 13, 2,
            (uint32_t[]){ vb, 0u }) == E_POINTER_VALUE);

        /* Rasteriser-dependent methods stay honest with exact pops. */
        ok("VB Lock is NOTAVAILABLE", vtcall(vbvt, 11, 5,
            (uint32_t[]){ vb, 0u, 0u, out, 0u }) == D3DERR_NOTAVAILABLE_VALUE);
        ok("Lock pops ret + this + 4 args", shim_pops(
            "D3D9::VertexBuffer::Lock", 5,
            (uint32_t[]){ vb, 0u, 0u, out, 0u }, 0) == 6u);
        ok("Unlock pops ret + this", shim_pops(
            "D3D9::VertexBuffer::Unlock", 1, &vb, 0) == 2u);
        ok("SetPrivateData pops ret + this + 4 args", shim_pops(
            "D3D9::VertexBuffer::SetPrivateData", 5,
            (uint32_t[]){ vb, 0u, 0u, 0u, 0u }, 0) == 6u);
        ok("GetPrivateData pops ret + this + 3 args", shim_pops(
            "D3D9::VertexBuffer::GetPrivateData", 4,
            (uint32_t[]){ vb, 0u, 0u, 0u }, 0) == 5u);
        ok("FreePrivateData pops ret + this + 1 arg", shim_pops(
            "D3D9::VertexBuffer::FreePrivateData", 2,
            (uint32_t[]){ vb, 0u }, 0) == 3u);

        /* Refcount and resource lifetime: each AddRef must be matched, and
         * the final Release must drop the device reference as well. */
        ok("VB AddRef returns 2", vtcall(vbvt, 1, 1, &vb) == 2u);
        ok("VB Release returns 1", vtcall(vbvt, 2, 1, &vb) == 1u);
        ok("VB final Release returns 0", vtcall(vbvt, 2, 1, &vb) == 0u);
        ok("resource lifetime released device ref", MEM32(dev + 4u) == 1u);
    }

    /* Error paths never create an object and never leak a device reference. */
    uint32_t bad_out = alloc_u32(0xDEADBEEFu);
    uint32_t hrc = 0;
    ok("VB NULL out is E_POINTER", shim_pops("D3D9::Device::CreateVertexBuffer", 7,
        (uint32_t[]){ dev, 256u, 8u, 0x204u, 0u, 0u, 0u }, &hrc) == 8u);
    ok("VB NULL out returns E_POINTER", hrc == E_POINTER_VALUE);
    ok("VB zero length is INVALIDCALL", shim_pops("D3D9::Device::CreateVertexBuffer", 7,
        (uint32_t[]){ dev, 0u, 8u, 0x204u, 0u, bad_out, 0u }, &hrc) == 8u);
    ok("VB zero length returns INVALIDCALL", hrc == D3DERR_INVALIDCALL_VALUE);
    ok("VB bad pool is INVALIDCALL", shim_pops("D3D9::Device::CreateVertexBuffer", 7,
        (uint32_t[]){ dev, 256u, 8u, 0x204u, D3DPOOL_SCRATCH_VALUE + 1u, bad_out, 0u }, &hrc) == 8u);
    ok("VB bad pool returns INVALIDCALL", hrc == D3DERR_INVALIDCALL_VALUE);
    ok("VB shared handle is INVALIDCALL", shim_pops("D3D9::Device::CreateVertexBuffer", 7,
        (uint32_t[]){ dev, 256u, 8u, 0x204u, 0u, bad_out, dev_out }, &hrc) == 8u);
    ok("VB shared handle returns INVALIDCALL", hrc == D3DERR_INVALIDCALL_VALUE);
    ok("no object on VB failure", MEM32(bad_out) == 0u);
    ok("failed calls did not leak a device ref", MEM32(dev + 4u) == 1u);

    /* ---- BEFORE: CreateIndexBuffer answered NOTAVAILABLE. AFTER: object. */
    uint32_t ib_out = alloc_u32(0xDEADBEEFu);
    const uint32_t ib_args[7] = { dev, 512u, 0u, D3DFMT_INDEX16_VALUE, 0u, ib_out, 0u };
    hr = vtcall(devvt, 27, 7, ib_args);
    if (hr != D3D_OK_VALUE || MEM32(ib_out) == 0u || MEM32(ib_out) == 0xDEADBEEFu) {
        printf("before evidence: CreateIndexBuffer valid call = 0x%08X (object ptr 0x%08X)\n",
               hr, MEM32(ib_out));
        ok("CreateIndexBuffer returns D3D_OK frontend object", 0);
    } else {
        ok("CreateIndexBuffer returns D3D_OK frontend object", 1);
        uint32_t ib = MEM32(ib_out);
        uint32_t ibvt = MEM32(ib);
        ok("index buffer vtable set", ibvt != 0);
        ok("index buffer refcount 1", MEM32(ib + 4u) == 1u);
        ok("IB resource holds device reference", MEM32(dev + 4u) == 2u);
        for (unsigned i = 0; i < 14; i++) {
            char name[80];
            snprintf(name, sizeof name, "D3D9::IndexBuffer::%s", vb_names[i]);
            ok("index buffer vtable slot", MEM32(ibvt + 4u * i) == hle_resolve(name));
        }
        uint32_t out = alloc_u32(0xDEADBEEFu);
        check_resource_qi("IB QI(IUnknown)", ibvt, ib, IID_UNKNOWN, D3D_OK_VALUE, &out);
        out = alloc_u32(0xDEADBEEFu);
        check_resource_qi("IB QI(IDirect3DResource9)", ibvt, ib, IID_RESOURCE9, D3D_OK_VALUE, &out);
        out = alloc_u32(0xDEADBEEFu);
        check_resource_qi("IB QI(IDirect3DIndexBuffer9)", ibvt, ib, IID_IB9, D3D_OK_VALUE, &out);
        out = alloc_u32(0xDEADBEEFu);
        check_resource_qi("IB QI(IDirect3DVertexBuffer9)", ibvt, ib, IID_VB9, E_NOINTERFACE_VALUE, &out);
        ok("IB GetType", vtcall(ibvt, 10, 1, &ib) == D3DRTYPE_INDEXBUFFER_VALUE);

        uint32_t desc = hle_va(hle_alloc(24));
        memset(hle_ptr(desc), 0xCD, 24);
        ok("IB GetDesc", vtcall(ibvt, 13, 2,
            (uint32_t[]){ ib, desc }) == D3D_OK_VALUE);
        ok("IB desc Format=D3DFMT_INDEX16", MEM32(desc) == D3DFMT_INDEX16_VALUE);
        ok("IB desc Type=D3DRTYPE_INDEXBUFFER", MEM32(desc + 4u) == D3DRTYPE_INDEXBUFFER_VALUE);
        ok("IB desc Size", MEM32(desc + 16u) == 512u);
        ok("IB desc does not overrun", MEM32(desc + 20u) == 0xCDCDCDCDu);
        ok("IB Lock is NOTAVAILABLE", vtcall(ibvt, 11, 5,
            (uint32_t[]){ ib, 0u, 0u, 0u, 0u }) == D3DERR_NOTAVAILABLE_VALUE);

        ok("IB final Release returns 0", vtcall(ibvt, 2, 1, &ib) == 0u);
        ok("IB lifetime released device ref", MEM32(dev + 4u) == 1u);
    }

    uint32_t ib_bad = alloc_u32(0xDEADBEEFu);
    ok("IB NULL out is E_POINTER", shim_pops("D3D9::Device::CreateIndexBuffer", 7,
        (uint32_t[]){ dev, 512u, 0u, D3DFMT_INDEX16_VALUE, 0u, 0u, 0u }, &hrc) == 8u);
    ok("IB NULL out returns E_POINTER", hrc == E_POINTER_VALUE);
    ok("IB bad format is INVALIDCALL", shim_pops("D3D9::Device::CreateIndexBuffer", 7,
        (uint32_t[]){ dev, 512u, 0u, 22u, 0u, ib_bad, 0u }, &hrc) == 8u);
    ok("IB bad format returns INVALIDCALL", hrc == D3DERR_INVALIDCALL_VALUE);
    ok("no object on IB failure", MEM32(ib_bad) == 0u);
    ok("IB failures did not leak a device ref", MEM32(dev + 4u) == 1u);

    /* Other rasteriser-dependent slots keep their real stdcall purges. */
    ok("Present still NOTAVAILABLE", vtcall(devvt, 17, 5,
        (uint32_t[]){ dev, 0u, 0u, 0u, 0u }) == D3DERR_NOTAVAILABLE_VALUE);
    ok("Present pops ret + this + 4 args", shim_pops(
        "D3D9::Device::Present", 5,
        (uint32_t[]){ dev, 0u, 0u, 0u, 0u }, 0) == 6u);

    ok("device final Release destroys", vtcall(devvt, 2, 1, &dev) == 0u);
    ok("D3D9 final Release destroys", vtcall(vt, 2, 1, &d3d) == 0u);

    mach_leave();
    if (!fails) printf("d3d9_resource_selftest: all checks passed\n");
    else printf("d3d9_resource_selftest: %d check(s) failed\n", fails);
    return fails != 0;
}
