/* Direct3D 9 guest COM frontend.
 *
 * A FRONTEND, not a renderer: it answers the d3d9.dll export with correctly
 * shaped IDirect3D9 / IDirect3DDevice9 vtables (method order and stdcall purge
 * counts from the official DirectX 9 ABI), real IUnknown reference counting
 * and honest HRESULTs. Adapter/display queries read the shared SDL display
 * mode via hle_dd_mode(); anything needing a rasteriser returns
 * D3DERR_NOTAVAILABLE. Presentation stays in d3d7.c/ddraw.c.
 *
 * IDirect3DVertexBuffer9 / IDirect3DIndexBuffer9 objects are frontend-only
 * resources too: they own a descriptor and a device reference, expose the
 * correct 14-slot public vtables and real refcounting, and describe themselves
 * through GetDevice/GetPriority/GetType/GetDesc. Lock and every other
 * rasteriser-dependent method stay NOTAVAILABLE; no guest data is rasterised.
 *
 * Vtable order and argument counts were derived from wine-mirror/wine
 * include/d3d9.h and include/d3d9types.h at commit
 * 97027f879ba9a36dbe594720d4710818536e5433 (IDirect3D9, IDirect3DDevice9,
 * IDirect3DVertexBuffer9, IDirect3DIndexBuffer9). No Wine code was copied: only
 * the public ABI layout is encoded below.
 */
#include "win32hle.h"

#define D3D_OK                 0u
#define E_NOINTERFACE          0x80004002u
#define E_POINTER              0x80004003u
#define D3DERR_INVALIDCALL     0x8876086Cu
#define D3DERR_NOTAVAILABLE    0x8876086Au
#define D3D_SDK_VERSION        32u
#define D3DENUM_WHQL_LEVEL     0x2u
#define D3DFMT_R5G6B5          23u
#define D3DFMT_X8R8G8B8        22u
#define D3DFMT_VERTEXDATA      100u
#define D3DFMT_INDEX16         101u
#define D3DFMT_INDEX32         102u
#define D3DPOOL_SCRATCH        3u
#define D3DRTYPE_VERTEXBUFFER  6u
#define D3DRTYPE_INDEXBUFFER   7u

/* D3DADAPTER_IDENTIFIER9 is 512+512+32 bytes of fixed char arrays, a
 * LARGE_INTEGER, four DWORDs, a 16-byte GUID and a final WHQL DWORD = 1100
 * bytes. All offsets are fixed by the public ABI, so a frontend can fill it
 * without a rasteriser. */
#define D3D9_ADAPTER_ID_SIZE   1100u
#define D3D9_ADAPTER_DRIVER    0u
#define D3D9_ADAPTER_DESC      512u
#define D3D9_ADAPTER_NAME      1024u

/* GUID bytes in REFIID on-disk order (first three fields little endian). */
static const uint8_t d3d9_iid_unknown[16] = {
    0, 0, 0, 0, 0, 0, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46
};
static const uint8_t d3d9_iid_d3d9[16] = {
    0xCA, 0xCB, 0xBD, 0x81, 0xD4, 0x64, 0x6D, 0x42,
    0xAE, 0x8D, 0xAD, 0x01, 0x47, 0xF4, 0x27, 0x5C
};
static const uint8_t d3d9_iid_device9[16] = {
    0x96, 0x3B, 0x22, 0xD0, 0x7A, 0xBF, 0xFD, 0x43,
    0x92, 0xBD, 0xA4, 0x3B, 0x0D, 0x82, 0xB9, 0xEB
};
static const uint8_t d3d9_iid_resource9[16] = {
    0x5D, 0xC0, 0xEE, 0x05, 0x7D, 0x8F, 0x62, 0x43,
    0xB9, 0x99, 0xD1, 0xBA, 0xF3, 0x57, 0xC7, 0x04
};
static const uint8_t d3d9_iid_vertexbuffer9[16] = {
    0xB5, 0xB1, 0x4B, 0xB6, 0x70, 0xFD, 0xF6, 0x4D,
    0xBF, 0x91, 0x19, 0xD0, 0xA1, 0x24, 0x55, 0xE3
};
static const uint8_t d3d9_iid_indexbuffer9[16] = {
    0x5E, 0xD6, 0x9D, 0x7C, 0xF7, 0xD3, 0x29, 0x45,
    0xAC, 0xEE, 0x78, 0x58, 0x30, 0xAC, 0xDE, 0x35
};

/* Frontend state of an IDirect3DVertexBuffer9 / IDirect3DIndexBuffer9 object:
 * vtable pointer, refcount, owning device (with one reference held by the
 * resource), type,
 * priority, byte length, usage, format-or-FVF, pool. hle_com_new zeroes it. */
#define D3D9_RES_DEVICE    8u
#define D3D9_RES_TYPE      12u
#define D3D9_RES_PRIORITY  16u
#define D3D9_RES_LENGTH    20u
#define D3D9_RES_USAGE     24u
#define D3D9_RES_FMT_FVF   28u
#define D3D9_RES_POOL      32u
#define D3D9_RES_SIZE      40u

static int d3d9_trace(void) {
    static int on = -1;
    if (on < 0) on = getenv("RECOMP_D3D9_DIAG") != NULL;
    return on;
}

/* Each unsupported method keeps its real stdcall purge count: a wrong pop
 * corrupts the guest stack even though the HRESULT is honest. */
#define D3D9_NA(n) static void d3d9_na##n(void) { RET(D3DERR_NOTAVAILABLE, n); }
D3D9_NA(1) D3D9_NA(2) D3D9_NA(3) D3D9_NA(4) D3D9_NA(5)
D3D9_NA(6) D3D9_NA(7) D3D9_NA(8) D3D9_NA(9) D3D9_NA(10)
#undef D3D9_NA

static int d3d9_qi(uint32_t self, uint32_t iid, uint32_t out,
                   const uint8_t *const *ids, unsigned count) {
    if (!iid || !out) return 0;
    const uint8_t *want = hle_ptr(iid);
    unsigned k;
    for (k = 0; k < count; k++)
        if (!memcmp(want, ids[k], 16)) break;
    if (k == count) { MEM32(out) = 0; return 0; }
    MEM32(out) = self;
    MEM32(self + 4)++;
    return 1;
}
static void d3d9_d3d_qi(void) {
    static const uint8_t *const ids[] = { d3d9_iid_unknown, d3d9_iid_d3d9 };
    uint32_t iid = A32(1), out = A32(2);
    uint32_t hr = d3d9_qi(A32(0), iid, out, ids, 2) ? D3D_OK : E_NOINTERFACE;
    if (!iid || !out) hr = E_POINTER;
    RET(hr, 3);
}
static void d3d9_d3d_addref(void) { RET(++MEM32(A32(0) + 4), 1); }
/* One decrement-and-destroy path for every frontend object, so a resource's
 * final Release can also drop the device reference it owns. */
static uint32_t d3d9_drop_ref(uint32_t self) {
    uint32_t r = MEM32(self + 4u);
    if (r) r--;
    MEM32(self + 4u) = r;
    if (!r) hle_com_free(self);
    return r;
}
static void d3d9_d3d_release(void) { RET(d3d9_drop_ref(A32(0)), 1); }
static void d3d9_dev_qi(void) {
    static const uint8_t *const ids[] = { d3d9_iid_unknown, d3d9_iid_device9 };
    uint32_t iid = A32(1), out = A32(2);
    uint32_t hr = d3d9_qi(A32(0), iid, out, ids, 2) ? D3D_OK : E_NOINTERFACE;
    if (!iid || !out) hr = E_POINTER;
    RET(hr, 3);
}
static void d3d9_dev_addref(void) { RET(++MEM32(A32(0) + 4), 1); }
static void d3d9_dev_release(void) { RET(d3d9_drop_ref(A32(0)), 1); }

/* ---- frontend resource objects -----------------------------------------
 * A vertex/index buffer is a descriptor in guest memory, not a GPU allocation:
 * there is no rasteriser and no host memory backing. Everything a guest can
 * legitimately ask about a descriptor without one (IUnknown identity, owning
 * device, priority, resource type and the buffer DESC) is answered directly.
 * Lock/Unlock/private data keep their real stdcall purges and return
 * D3DERR_NOTAVAILABLE rather than pretending to store pixels or vertices. */
static void d3d9_vb_qi(void) {
    static const uint8_t *const ids[] = {
        d3d9_iid_unknown, d3d9_iid_resource9, d3d9_iid_vertexbuffer9
    };
    uint32_t iid = A32(1), out = A32(2);
    uint32_t hr = d3d9_qi(A32(0), iid, out, ids, 3) ? D3D_OK : E_NOINTERFACE;
    if (!iid || !out) hr = E_POINTER;
    RET(hr, 3);
}
static void d3d9_ib_qi(void) {
    static const uint8_t *const ids[] = {
        d3d9_iid_unknown, d3d9_iid_resource9, d3d9_iid_indexbuffer9
    };
    uint32_t iid = A32(1), out = A32(2);
    uint32_t hr = d3d9_qi(A32(0), iid, out, ids, 3) ? D3D_OK : E_NOINTERFACE;
    if (!iid || !out) hr = E_POINTER;
    RET(hr, 3);
}
static void d3d9_res_addref(void) { RET(++MEM32(A32(0) + 4u), 1); }
static void d3d9_res_release(void) {
    uint32_t self = A32(0), dev = MEM32(self + D3D9_RES_DEVICE);
    uint32_t r = d3d9_drop_ref(self);
    /* The resource owned one device reference from the moment it was created;
     * releasing the last resource reference gives it back. */
    if (!r && dev) (void)d3d9_drop_ref(dev);
    RET(r, 1);
}
static void d3d9_res_get_device(void) {
    uint32_t out = A32(1);
    if (!out) RET(E_POINTER, 2);
    uint32_t dev = MEM32(A32(0) + D3D9_RES_DEVICE);
    MEM32(out) = dev;
    if (dev) MEM32(dev + 4u)++;
    RET(dev ? D3D_OK : D3DERR_NOTAVAILABLE, 2);
}
static void d3d9_res_set_priority(void) {
    uint32_t self = A32(0), old = MEM32(self + D3D9_RES_PRIORITY);
    MEM32(self + D3D9_RES_PRIORITY) = A32(1);
    RET(old, 2);                                /* returns the previous priority */
}
static void d3d9_res_get_priority(void) { RET(MEM32(A32(0) + D3D9_RES_PRIORITY), 1); }
static void d3d9_res_preload(void) { RETV(1); }
static void d3d9_res_get_type(void) { RET(MEM32(A32(0) + D3D9_RES_TYPE), 1); }

static void d3d9_vb_get_desc(void) {
    uint32_t out = A32(1);
    if (!out) RET(E_POINTER, 2);
    uint32_t self = A32(0);
    /* D3DVERTEXBUFFER_DESC: Format, Type, Usage, Pool, Size, FVF = 24 bytes. */
    for (unsigned i = 0; i < 24u; i += 4u) MEM32(out + i) = 0u;
    MEM32(out)       = D3DFMT_VERTEXDATA;
    MEM32(out + 4u)  = MEM32(self + D3D9_RES_TYPE);
    MEM32(out + 8u)  = MEM32(self + D3D9_RES_USAGE);
    MEM32(out + 12u) = MEM32(self + D3D9_RES_POOL);
    MEM32(out + 16u) = MEM32(self + D3D9_RES_LENGTH);
    MEM32(out + 20u) = MEM32(self + D3D9_RES_FMT_FVF);
    RET(D3D_OK, 2);
}
static void d3d9_ib_get_desc(void) {
    uint32_t out = A32(1);
    if (!out) RET(E_POINTER, 2);
    uint32_t self = A32(0);
    /* D3DINDEXBUFFER_DESC: Format, Type, Usage, Pool, Size = 20 bytes. */
    for (unsigned i = 0; i < 20u; i += 4u) MEM32(out + i) = 0u;
    MEM32(out)       = MEM32(self + D3D9_RES_FMT_FVF);
    MEM32(out + 4u)  = MEM32(self + D3D9_RES_TYPE);
    MEM32(out + 8u)  = MEM32(self + D3D9_RES_USAGE);
    MEM32(out + 12u) = MEM32(self + D3D9_RES_POOL);
    MEM32(out + 16u) = MEM32(self + D3D9_RES_LENGTH);
    RET(D3D_OK, 2);
}

/* The 14 entries are IUnknown (3) + IDirect3DResource9 (8) + the buffer's own
 * Lock/Unlock/GetDesc (3), in the order declared in Wine's public d3d9.h. */
#define D3D9_RESOURCE_METHODS(X, P, QI, GD) \
    X(P "::QueryInterface", QI, 3) \
    X(P "::AddRef", d3d9_res_addref, 1) \
    X(P "::Release", d3d9_res_release, 1) \
    X(P "::GetDevice", d3d9_res_get_device, 2) \
    X(P "::SetPrivateData", d3d9_na5, 5) \
    X(P "::GetPrivateData", d3d9_na4, 4) \
    X(P "::FreePrivateData", d3d9_na2, 2) \
    X(P "::SetPriority", d3d9_res_set_priority, 2) \
    X(P "::GetPriority", d3d9_res_get_priority, 1) \
    X(P "::PreLoad", d3d9_res_preload, 1) \
    X(P "::GetType", d3d9_res_get_type, 1) \
    X(P "::Lock", d3d9_na5, 5) \
    X(P "::Unlock", d3d9_na1, 1) \
    X(P "::GetDesc", GD, 2)

#define D3D9_RESOURCE_ENTRY(name, fn, argc) { name, fn },
static const win32hle_shim d3d9_vb_methods[] = {
    D3D9_RESOURCE_METHODS(D3D9_RESOURCE_ENTRY, "D3D9::VertexBuffer",
                          d3d9_vb_qi, d3d9_vb_get_desc)
    { 0, 0 }
};
static const win32hle_shim d3d9_ib_methods[] = {
    D3D9_RESOURCE_METHODS(D3D9_RESOURCE_ENTRY, "D3D9::IndexBuffer",
                          d3d9_ib_qi, d3d9_ib_get_desc)
    { 0, 0 }
};
static uint32_t d3d9_vb_vt(void) {
    static uint32_t vt;
    if (!vt) vt = hle_com_vtable(d3d9_vb_methods);
    return vt;
}
static uint32_t d3d9_ib_vt(void) {
    static uint32_t vt;
    if (!vt) vt = hle_com_vtable(d3d9_ib_methods);
    return vt;
}

static uint32_t d3d9_new_buffer(uint32_t dev, uint32_t vt, uint32_t type,
                                uint32_t length, uint32_t usage, uint32_t fmt,
                                uint32_t pool, uint32_t out) {
    uint32_t obj = hle_com_new(vt, D3D9_RES_SIZE);
    if (!obj) return D3DERR_NOTAVAILABLE;
    MEM32(obj + D3D9_RES_DEVICE) = dev;
    MEM32(obj + D3D9_RES_TYPE) = type;
    MEM32(obj + D3D9_RES_LENGTH) = length;
    MEM32(obj + D3D9_RES_USAGE) = usage;
    MEM32(obj + D3D9_RES_FMT_FVF) = fmt;
    MEM32(obj + D3D9_RES_POOL) = pool;
    /* Device outlives its resources: the resource owns one reference. */
    MEM32(dev + 4u)++;
    MEM32(out) = obj;
    if (d3d9_trace()) fprintf(stderr, "[d3d9] buffer -> %08x (frontend, no rasteriser)\n", obj);
    return D3D_OK;
}

/* CreateVertexBuffer(Length, Usage, FVF, Pool, ppVertexBuffer, pSharedHandle)
 * and CreateIndexBuffer(Length, Usage, Format, Pool, ppIndexBuffer,
 * pSharedHandle) both take six arguments after `this`: ret + this + 6 = 7
 * words. Shared resources need a rasteriser this frontend does not have. */
static void d3d9_dev_create_vb(void) {
    uint32_t out = A32(5);
    if (!out) RET(E_POINTER, 7);
    MEM32(out) = 0;
    if (!A32(1) || A32(4) > D3DPOOL_SCRATCH || A32(6)) RET(D3DERR_INVALIDCALL, 7);
    RET(d3d9_new_buffer(A32(0), d3d9_vb_vt(), D3DRTYPE_VERTEXBUFFER,
                        A32(1), A32(2), A32(3), A32(4), out), 7);
}
static void d3d9_dev_create_ib(void) {
    uint32_t out = A32(5);
    if (!out) RET(E_POINTER, 7);
    MEM32(out) = 0;
    if (!A32(1) || A32(4) > D3DPOOL_SCRATCH || A32(6) ||
        (A32(3) != D3DFMT_INDEX16 && A32(3) != D3DFMT_INDEX32))
        RET(D3DERR_INVALIDCALL, 7);
    RET(d3d9_new_buffer(A32(0), d3d9_ib_vt(), D3DRTYPE_INDEXBUFFER,
                        A32(1), A32(2), A32(3), A32(4), out), 7);
}

/* One virtual adapter; geometry is the shared display mode, never a host GL
 * call, so these queries agree with ddraw/d3d7. */
static uint32_t d3d9_display_format(void) {
    int w = 640, h = 480, bpp = 32;
    hle_dd_mode(&w, &h, &bpp);
    return bpp == 16 ? D3DFMT_R5G6B5 : D3DFMT_X8R8G8B8;
}
static void d3d9_write_mode(uint32_t p) {
    int w = 640, h = 480, bpp = 32;
    hle_dd_mode(&w, &h, &bpp);
    MEM32(p)      = w > 0 ? (uint32_t)w : 640u;
    MEM32(p + 4)  = h > 0 ? (uint32_t)h : 480u;
    MEM32(p + 8)  = 60u;                            /* refresh rate in Hz */
    MEM32(p + 12) = d3d9_display_format();
}
static void d3d9_d3d_get_adapter_count(void) { RET(1u, 1); }
/* Wine's d3d9.h declares GetAdapterModeCount(UINT Adapter, D3DFORMAT Format):
 * two arguments after `this`, so a callee must pop three words including the
 * synthetic return address. The D3D8 version has only Adapter and pops two;
 * copying that here left one word on the guest stack per call, which drifts
 * ESP through a game's whole mode-enumeration loop. */
static void d3d9_d3d_get_mode_count(void) {
    RET(A32(1) == 0 && (!A32(2) || A32(2) == d3d9_display_format()) ? 1u : 0u, 3);
}
static void d3d9_d3d_enum_modes(void) {
    uint32_t mode = A32(4);
    if (!mode) RET(E_POINTER, 5);
    /* EnumAdapterModes(Adapter, Format, Mode, pMode) in d3d9.h adds a Format
     * slot D3D8 does not have. Demanding Format == 0 rejected the real
     * D3DFMT_X8R8G8B8 / D3DFMT_R5G6B5 every DX9 title passes here, and
     * testing A32(2) as the mode index accepted out-of-range indices. */
    if (A32(1) != 0 || A32(3) != 0) RET(D3DERR_INVALIDCALL, 5);
    if (A32(2) && A32(2) != d3d9_display_format()) RET(D3DERR_INVALIDCALL, 5);
    d3d9_write_mode(mode);
    RET(D3D_OK, 5);
}
static void d3d9_d3d_get_display_mode(void) {
    uint32_t mode = A32(2);
    if (!mode) RET(E_POINTER, 3);
    if (A32(1) != 0) RET(D3DERR_INVALIDCALL, 3);
    d3d9_write_mode(mode);
    RET(D3D_OK, 3);
}

/* GetAdapterIdentifier(UINT Adapter, DWORD Flags, D3DADAPTER_IDENTIFIER9 *p)
 * is a pure descriptive query: unlike caps or resource creation it needs no
 * rasteriser, so answering D3DERR_NOTAVAILABLE (the old d3d9_na4 slot) made
 * real DX9 titles abort during startup before they ever reached CreateDevice.
 * Fill the identifier from the frontend's own shared-display identity: the
 * physical-device and WHQL fields stay zero/honest. Flags may be 0 or
 * D3DENUM_WHQL_LEVEL; the D3D9 purge is ret + this + three args = 5 words. */
static void d3d9_d3d_get_adapter_identifier(void) {
    static const char driver[] = "win32hle";
    static const char description[] = "GameReComp D3D9 frontend (shared presenter, no rasteriser)";
    static const char device_name[] = "d3d9";
    uint32_t out = A32(3);
    if (!out) RET(E_POINTER, 4);
    if (A32(1) != 0 || (A32(2) & ~D3DENUM_WHQL_LEVEL)) RET(D3DERR_INVALIDCALL, 4);
    uint8_t *p = hle_ptr(out);
    if (!p) RET(E_POINTER, 4);
    memset(p, 0, D3D9_ADAPTER_ID_SIZE);
    memcpy(p + D3D9_ADAPTER_DRIVER, driver, sizeof driver);
    memcpy(p + D3D9_ADAPTER_DESC, description, sizeof description);
    memcpy(p + D3D9_ADAPTER_NAME, device_name, sizeof device_name);
    RET(D3D_OK, 4);
}

/* A device object exists so guests can inspect IDirect3DDevice9's vtable and
 * reference counting, but it owns no rasteriser: apart from the two frontend
 * buffer factories, all non-IUnknown entries answer D3DERR_NOTAVAILABLE with
 * their real stdcall purge count. */
static uint32_t d3d9_device_vt(void);
static void d3d9_d3d_create_device(void) {
    uint32_t out = A32(6);
    if (!out) RET(E_POINTER, 7);
    MEM32(out) = 0;
    if (A32(1) != 0 || !A32(5)) RET(D3DERR_INVALIDCALL, 7);
    uint32_t dev = hle_com_new(d3d9_device_vt(), 8);
    if (!dev) RET(D3DERR_NOTAVAILABLE, 7);
    if (d3d9_trace()) fprintf(stderr, "[d3d9] CreateDevice -> %08x (no renderer)\n", dev);
    MEM32(out) = dev;
    RET(D3D_OK, 7);
}

/* ---- vtables -----------------------------------------------------------
 * X-macro order is the public DirectX 9 ABI as declared in wine's d3d9.h.
 * The device vtable has 119 entries; no host code is copied. */
#define D3D9_D3D_METHODS(X) \
    X("D3D9::Object::QueryInterface", d3d9_d3d_qi, 3) \
    X("D3D9::Object::AddRef", d3d9_d3d_addref, 1) \
    X("D3D9::Object::Release", d3d9_d3d_release, 1) \
    X("D3D9::Object::RegisterSoftwareDevice", d3d9_na2, 2) \
    X("D3D9::Object::GetAdapterCount", d3d9_d3d_get_adapter_count, 1) \
    X("D3D9::Object::GetAdapterIdentifier", d3d9_d3d_get_adapter_identifier, 4) \
    X("D3D9::Object::GetAdapterModeCount", d3d9_d3d_get_mode_count, 3) \
    X("D3D9::Object::EnumAdapterModes", d3d9_d3d_enum_modes, 5) \
    X("D3D9::Object::GetAdapterDisplayMode", d3d9_d3d_get_display_mode, 3) \
    X("D3D9::Object::CheckDeviceType", d3d9_na6, 6) \
    X("D3D9::Object::CheckDeviceFormat", d3d9_na7, 7) \
    X("D3D9::Object::CheckDeviceMultiSampleType", d3d9_na7, 7) \
    X("D3D9::Object::CheckDepthStencilMatch", d3d9_na6, 6) \
    X("D3D9::Object::CheckDeviceFormatConversion", d3d9_na5, 5) \
    X("D3D9::Object::GetDeviceCaps", d3d9_na4, 4) \
    X("D3D9::Object::GetAdapterMonitor", d3d9_na2, 2) \
    X("D3D9::Object::CreateDevice", d3d9_d3d_create_device, 7)

#define D3D9_DEVICE_METHODS(X) \
    X("D3D9::Device::QueryInterface", d3d9_dev_qi, 3) \
    X("D3D9::Device::AddRef", d3d9_dev_addref, 1) \
    X("D3D9::Device::Release", d3d9_dev_release, 1) \
    X("D3D9::Device::TestCooperativeLevel", d3d9_na1, 1) \
    X("D3D9::Device::GetAvailableTextureMem", d3d9_na1, 1) \
    X("D3D9::Device::EvictManagedResources", d3d9_na1, 1) \
    X("D3D9::Device::GetDirect3D", d3d9_na2, 2) \
    X("D3D9::Device::GetDeviceCaps", d3d9_na2, 2) \
    X("D3D9::Device::GetDisplayMode", d3d9_na3, 3) \
    X("D3D9::Device::GetCreationParameters", d3d9_na2, 2) \
    X("D3D9::Device::SetCursorProperties", d3d9_na4, 4) \
    X("D3D9::Device::SetCursorPosition", d3d9_na4, 4) \
    X("D3D9::Device::ShowCursor", d3d9_na2, 2) \
    X("D3D9::Device::CreateAdditionalSwapChain", d3d9_na3, 3) \
    X("D3D9::Device::GetSwapChain", d3d9_na3, 3) \
    X("D3D9::Device::GetNumberOfSwapChains", d3d9_na1, 1) \
    X("D3D9::Device::Reset", d3d9_na2, 2) \
    X("D3D9::Device::Present", d3d9_na5, 5) \
    X("D3D9::Device::GetBackBuffer", d3d9_na5, 5) \
    X("D3D9::Device::GetRasterStatus", d3d9_na3, 3) \
    X("D3D9::Device::SetDialogBoxMode", d3d9_na2, 2) \
    X("D3D9::Device::SetGammaRamp", d3d9_na4, 4) \
    X("D3D9::Device::GetGammaRamp", d3d9_na3, 3) \
    X("D3D9::Device::CreateTexture", d3d9_na9, 9) \
    X("D3D9::Device::CreateVolumeTexture", d3d9_na10, 10) \
    X("D3D9::Device::CreateCubeTexture", d3d9_na8, 8) \
    X("D3D9::Device::CreateVertexBuffer", d3d9_dev_create_vb, 7) \
    X("D3D9::Device::CreateIndexBuffer", d3d9_dev_create_ib, 7) \
    X("D3D9::Device::CreateRenderTarget", d3d9_na9, 9) \
    X("D3D9::Device::CreateDepthStencilSurface", d3d9_na9, 9) \
    X("D3D9::Device::UpdateSurface", d3d9_na5, 5) \
    X("D3D9::Device::UpdateTexture", d3d9_na3, 3) \
    X("D3D9::Device::GetRenderTargetData", d3d9_na3, 3) \
    X("D3D9::Device::GetFrontBufferData", d3d9_na3, 3) \
    X("D3D9::Device::StretchRect", d3d9_na6, 6) \
    X("D3D9::Device::ColorFill", d3d9_na4, 4) \
    X("D3D9::Device::CreateOffscreenPlainSurface", d3d9_na7, 7) \
    X("D3D9::Device::SetRenderTarget", d3d9_na3, 3) \
    X("D3D9::Device::GetRenderTarget", d3d9_na3, 3) \
    X("D3D9::Device::SetDepthStencilSurface", d3d9_na2, 2) \
    X("D3D9::Device::GetDepthStencilSurface", d3d9_na2, 2) \
    X("D3D9::Device::BeginScene", d3d9_na1, 1) \
    X("D3D9::Device::EndScene", d3d9_na1, 1) \
    X("D3D9::Device::Clear", d3d9_na7, 7) \
    X("D3D9::Device::SetTransform", d3d9_na3, 3) \
    X("D3D9::Device::GetTransform", d3d9_na3, 3) \
    X("D3D9::Device::MultiplyTransform", d3d9_na3, 3) \
    X("D3D9::Device::SetViewport", d3d9_na2, 2) \
    X("D3D9::Device::GetViewport", d3d9_na2, 2) \
    X("D3D9::Device::SetMaterial", d3d9_na2, 2) \
    X("D3D9::Device::GetMaterial", d3d9_na2, 2) \
    X("D3D9::Device::SetLight", d3d9_na3, 3) \
    X("D3D9::Device::GetLight", d3d9_na3, 3) \
    X("D3D9::Device::LightEnable", d3d9_na3, 3) \
    X("D3D9::Device::GetLightEnable", d3d9_na3, 3) \
    X("D3D9::Device::SetClipPlane", d3d9_na3, 3) \
    X("D3D9::Device::GetClipPlane", d3d9_na3, 3) \
    X("D3D9::Device::SetRenderState", d3d9_na3, 3) \
    X("D3D9::Device::GetRenderState", d3d9_na3, 3) \
    X("D3D9::Device::CreateStateBlock", d3d9_na3, 3) \
    X("D3D9::Device::BeginStateBlock", d3d9_na1, 1) \
    X("D3D9::Device::EndStateBlock", d3d9_na2, 2) \
    X("D3D9::Device::SetClipStatus", d3d9_na2, 2) \
    X("D3D9::Device::GetClipStatus", d3d9_na2, 2) \
    X("D3D9::Device::GetTexture", d3d9_na3, 3) \
    X("D3D9::Device::SetTexture", d3d9_na3, 3) \
    X("D3D9::Device::GetTextureStageState", d3d9_na4, 4) \
    X("D3D9::Device::SetTextureStageState", d3d9_na4, 4) \
    X("D3D9::Device::GetSamplerState", d3d9_na4, 4) \
    X("D3D9::Device::SetSamplerState", d3d9_na4, 4) \
    X("D3D9::Device::ValidateDevice", d3d9_na2, 2) \
    X("D3D9::Device::SetPaletteEntries", d3d9_na3, 3) \
    X("D3D9::Device::GetPaletteEntries", d3d9_na3, 3) \
    X("D3D9::Device::SetCurrentTexturePalette", d3d9_na2, 2) \
    X("D3D9::Device::GetCurrentTexturePalette", d3d9_na2, 2) \
    X("D3D9::Device::SetScissorRect", d3d9_na2, 2) \
    X("D3D9::Device::GetScissorRect", d3d9_na2, 2) \
    X("D3D9::Device::SetSoftwareVertexProcessing", d3d9_na2, 2) \
    X("D3D9::Device::GetSoftwareVertexProcessing", d3d9_na1, 1) \
    X("D3D9::Device::SetNPatchMode", d3d9_na2, 2) \
    X("D3D9::Device::GetNPatchMode", d3d9_na1, 1) \
    X("D3D9::Device::DrawPrimitive", d3d9_na4, 4) \
    X("D3D9::Device::DrawIndexedPrimitive", d3d9_na7, 7) \
    X("D3D9::Device::DrawPrimitiveUP", d3d9_na5, 5) \
    X("D3D9::Device::DrawIndexedPrimitiveUP", d3d9_na9, 9) \
    X("D3D9::Device::ProcessVertices", d3d9_na7, 7) \
    X("D3D9::Device::CreateVertexDeclaration", d3d9_na3, 3) \
    X("D3D9::Device::SetVertexDeclaration", d3d9_na2, 2) \
    X("D3D9::Device::GetVertexDeclaration", d3d9_na2, 2) \
    X("D3D9::Device::SetFVF", d3d9_na2, 2) \
    X("D3D9::Device::GetFVF", d3d9_na2, 2) \
    X("D3D9::Device::CreateVertexShader", d3d9_na3, 3) \
    X("D3D9::Device::SetVertexShader", d3d9_na2, 2) \
    X("D3D9::Device::GetVertexShader", d3d9_na2, 2) \
    X("D3D9::Device::SetVertexShaderConstantF", d3d9_na4, 4) \
    X("D3D9::Device::GetVertexShaderConstantF", d3d9_na4, 4) \
    X("D3D9::Device::SetVertexShaderConstantI", d3d9_na4, 4) \
    X("D3D9::Device::GetVertexShaderConstantI", d3d9_na4, 4) \
    X("D3D9::Device::SetVertexShaderConstantB", d3d9_na4, 4) \
    X("D3D9::Device::GetVertexShaderConstantB", d3d9_na4, 4) \
    X("D3D9::Device::SetStreamSource", d3d9_na5, 5) \
    X("D3D9::Device::GetStreamSource", d3d9_na5, 5) \
    X("D3D9::Device::SetStreamSourceFreq", d3d9_na3, 3) \
    X("D3D9::Device::GetStreamSourceFreq", d3d9_na3, 3) \
    X("D3D9::Device::SetIndices", d3d9_na2, 2) \
    X("D3D9::Device::GetIndices", d3d9_na2, 2) \
    X("D3D9::Device::CreatePixelShader", d3d9_na3, 3) \
    X("D3D9::Device::SetPixelShader", d3d9_na2, 2) \
    X("D3D9::Device::GetPixelShader", d3d9_na2, 2) \
    X("D3D9::Device::SetPixelShaderConstantF", d3d9_na4, 4) \
    X("D3D9::Device::GetPixelShaderConstantF", d3d9_na4, 4) \
    X("D3D9::Device::SetPixelShaderConstantI", d3d9_na4, 4) \
    X("D3D9::Device::GetPixelShaderConstantI", d3d9_na4, 4) \
    X("D3D9::Device::SetPixelShaderConstantB", d3d9_na4, 4) \
    X("D3D9::Device::GetPixelShaderConstantB", d3d9_na4, 4) \
    X("D3D9::Device::DrawRectPatch", d3d9_na4, 4) \
    X("D3D9::Device::DrawTriPatch", d3d9_na4, 4) \
    X("D3D9::Device::DeletePatch", d3d9_na2, 2) \
    X("D3D9::Device::CreateQuery", d3d9_na3, 3)

#define D3D9_D3D_ENTRY(name, fn, argc)    { name, fn },
#define D3D9_DEVICE_ENTRY(name, fn, argc) { name, fn },

static const win32hle_shim d3d9_d3d_methods[] = {
    D3D9_D3D_METHODS(D3D9_D3D_ENTRY)
    { 0, 0 }
};
static const win32hle_shim d3d9_device_methods[] = {
    D3D9_DEVICE_METHODS(D3D9_DEVICE_ENTRY)
    { 0, 0 }
};
static uint32_t d3d9_d3d_vt(void) {
    static uint32_t vt;
    if (!vt) vt = hle_com_vtable(d3d9_d3d_methods);
    return vt;
}
static uint32_t d3d9_device_vt(void) {
    static uint32_t vt;
    if (!vt) vt = hle_com_vtable(d3d9_device_methods);
    return vt;
}

/* Direct3DCreate9(UINT SDKVersion) -> IDirect3D9*. A wrong SDK version gets
 * NULL; a correct one gets a fresh object with one reference, which the guest
 * must Release. */
static void d3d9_Direct3DCreate9(void) {
    if (A32(0) != D3D_SDK_VERSION) RET(0u, 1);
    uint32_t obj = hle_com_new(d3d9_d3d_vt(), 8);
    if (d3d9_trace()) fprintf(stderr, "[d3d9] Direct3DCreate9(%u) -> %08x\n", A32(0), obj);
    RET(obj, 1);
}

const win32hle_shim win32hle_d3d9[] = {
    { "Direct3DCreate9", d3d9_Direct3DCreate9 },
    { 0, 0 }
};
