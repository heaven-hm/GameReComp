/* Direct3D 8 guest COM frontend.
 *
 * This file is deliberately a FRONTEND, not a renderer. It answers the D3D8
 * DLL export and serves correctly shaped IDirect3D8 / IDirect3DDevice8
 * vtables, with real IUnknown reference counting and honest HRESULTs. Calls
 * that need a rasteriser (device state, resources, presentation, shaders)
 * return D3DERR_NOTAVAILABLE instead of pretending to have succeeded.
 *
 * Presentation is NOT duplicated here: d3d7.c/ddraw.c already own the shared
 * SDL OpenGL context and the screen's frame pump. The only host facts this
 * layer uses are hle_dd_mode() (the shared display mode) and the HLE COM
 * helpers in ole32.c. No WebGPU, browser DOM, WASM memory layout, or game
 * engine code is present. See docs/UPSTREAM_REUSE_ISSUE3.md for provenance.
 */
#include "win32hle.h"

#define D3D_OK                 0u
#define E_NOINTERFACE          0x80004002u
#define E_POINTER              0x80004003u
#define D3DERR_INVALIDCALL     0x8876086Cu
#define D3DERR_NOTAVAILABLE    0x8876086Au
#define D3D_SDK_VERSION        220u
#define D3DFMT_R5G6B5          23u
#define D3DFMT_X8R8G8B8        22u

/* GUIDs in the on-disk order used by D3D's REFIIDs (first 3 fields little
 * endian, last 8 bytes verbatim), as in d3d7.c's IID tables. */
static const uint8_t d3d8_iid_unknown[16] = {
    0, 0, 0, 0, 0, 0, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46
};
static const uint8_t d3d8_iid_d3d8[16] = { /* {1DD9E8DA-1C77-4D40-B0CF-98FEFDFF9512} */
    0xDA, 0xE8, 0xD9, 0x1D, 0x77, 0x1C, 0x40, 0x4D,
    0xB0, 0xCF, 0x98, 0xFE, 0xFD, 0xFF, 0x95, 0x12
};
static const uint8_t d3d8_iid_device8[16] = { /* {7385E2DF-8FE8-41D5-86B3-C07E4D06B6DD} */
    0xDF, 0xE2, 0x85, 0x73, 0xE8, 0xFE, 0xD5, 0x41,
    0x86, 0xB3, 0xC0, 0x7E, 0x4D, 0x06, 0xB6, 0xDD
};

static int d3d8_trace(void) {
    static int on = -1;
    if (on < 0) on = getenv("RECOMP_D3D8_DIAG") != NULL;
    return on;
}

/* Every unsupported method has a distinct argc so its stdcall purge is still
 * correct: a wrong pop corrupts the guest stack even when the HRESULT is
 * honest. The list below carries the real argument count from d3d8.h. */
#define D3D8_NA(n) static void d3d8_na##n(void) { RET(D3DERR_NOTAVAILABLE, n); }
D3D8_NA(0) D3D8_NA(1) D3D8_NA(2) D3D8_NA(3)
D3D8_NA(4) D3D8_NA(5) D3D8_NA(6) D3D8_NA(7) D3D8_NA(8) D3D8_NA(9)
#undef D3D8_NA

/* QueryInterface: IUnknown and this interface only. A bogus REFIID yields
 * E_NOINTERFACE with *out cleared; a missing iid/out is E_POINTER. */
static int d3d8_qi(uint32_t self, uint32_t iid, uint32_t out,
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

static void d3d8_d3d_qi(void) {
    static const uint8_t *const ids[] = { d3d8_iid_unknown, d3d8_iid_d3d8 };
    uint32_t iid = A32(1), out = A32(2);
    uint32_t hr = d3d8_qi(A32(0), iid, out, ids, 2) ? D3D_OK : E_NOINTERFACE;
    if (!iid || !out) hr = E_POINTER;
    RET(hr, 3);
}
static void d3d8_d3d_addref(void) { RET(++MEM32(A32(0) + 4), 1); }
static void d3d8_d3d_release(void) {
    uint32_t self = A32(0), r = MEM32(self + 4);
    if (r) r--;
    MEM32(self + 4) = r;
    if (!r) hle_com_free(self);
    RET(r, 1);
}
static void d3d8_dev_qi(void) {
    static const uint8_t *const ids[] = { d3d8_iid_unknown, d3d8_iid_device8 };
    uint32_t iid = A32(1), out = A32(2);
    uint32_t hr = d3d8_qi(A32(0), iid, out, ids, 2) ? D3D_OK : E_NOINTERFACE;
    if (!iid || !out) hr = E_POINTER;
    RET(hr, 3);
}
static void d3d8_dev_addref(void) { RET(++MEM32(A32(0) + 4), 1); }
static void d3d8_dev_release(void) {
    uint32_t self = A32(0), r = MEM32(self + 4);
    if (r) r--;
    MEM32(self + 4) = r;
    if (!r) hle_com_free(self);
    RET(r, 1);
}

/* One virtual adapter. Its geometry comes from the same hle_dd_mode() the
 * DirectDraw and D3D7 presentation layers use, never a host GL call. */
static void d3d8_write_mode(uint32_t p) {
    int w = 640, h = 480, bpp = 32;
    hle_dd_mode(&w, &h, &bpp);
    MEM32(p)      = w > 0 ? (uint32_t)w : 640u;
    MEM32(p + 4)  = h > 0 ? (uint32_t)h : 480u;
    MEM32(p + 8)  = 60u;                            /* refresh rate in Hz */
    MEM32(p + 12) = bpp == 16 ? D3DFMT_R5G6B5 : D3DFMT_X8R8G8B8;
}
static void d3d8_d3d_get_adapter_count(void) { RET(1u, 1); }
static void d3d8_d3d_get_mode_count(void) {
    RET(A32(1) == 0 ? 1u : 0u, 2);
}
static void d3d8_d3d_enum_modes(void) {
    uint32_t mode = A32(3);
    if (!mode) RET(E_POINTER, 4);
    if (A32(1) != 0 || A32(2) != 0) RET(D3DERR_INVALIDCALL, 4);
    d3d8_write_mode(mode);
    RET(D3D_OK, 4);
}
static void d3d8_d3d_get_display_mode(void) {
    uint32_t mode = A32(2);
    if (!mode) RET(E_POINTER, 3);
    if (A32(1) != 0) RET(D3DERR_INVALIDCALL, 3);
    d3d8_write_mode(mode);
    RET(D3D_OK, 3);
}

/* A device object exists so the guest can inspect IDirect3DDevice8's vtable,
 * but it owns no rasteriser. Only IUnknown is implemented there; every draw,
 * state and resource entry point answers D3DERR_NOTAVAILABLE. */
static uint32_t d3d8_device_vt(void);
static void d3d8_d3d_create_device(void) {
    uint32_t out = A32(6);
    if (!out) RET(E_POINTER, 7);
    MEM32(out) = 0;
    if (A32(1) != 0 || !A32(5)) RET(D3DERR_INVALIDCALL, 7);
    uint32_t dev = hle_com_new(d3d8_device_vt(), 8);
    if (!dev) RET(D3DERR_NOTAVAILABLE, 7);
    if (d3d8_trace()) fprintf(stderr, "[d3d8] CreateDevice -> %08x (no renderer)\n", dev);
    MEM32(out) = dev;
    RET(D3D_OK, 7);
}

/* ---- vtables -----------------------------------------------------------
 * X-macro order is the public DirectX 8 ABI. It matches the order the IGI-WEB
 * host observes for these interfaces, but no host code is copied: these are
 * the method names and argument counts from d3d8.h. */
#define D3D8_D3D_METHODS(X) \
    X("QueryInterface", d3d8_d3d_qi, 3) \
    X("AddRef", d3d8_d3d_addref, 1) \
    X("Release", d3d8_d3d_release, 1) \
    X("RegisterSoftwareDevice", d3d8_na2, 2) \
    X("GetAdapterCount", d3d8_d3d_get_adapter_count, 1) \
    X("GetAdapterIdentifier", d3d8_na4, 4) \
    X("GetAdapterModeCount", d3d8_d3d_get_mode_count, 2) \
    X("EnumAdapterModes", d3d8_d3d_enum_modes, 4) \
    X("GetAdapterDisplayMode", d3d8_d3d_get_display_mode, 3) \
    X("CheckDeviceType", d3d8_na6, 6) \
    X("CheckDeviceFormat", d3d8_na7, 7) \
    X("CheckDeviceMultiSampleType", d3d8_na6, 6) \
    X("CheckDepthStencilMatch", d3d8_na6, 6) \
    X("GetDeviceCaps", d3d8_na4, 4) \
    X("GetAdapterMonitor", d3d8_na2, 2) \
    X("CreateDevice", d3d8_d3d_create_device, 7)

#define D3D8_DEVICE_METHODS(X) \
    X("QueryInterface", d3d8_dev_qi, 3) \
    X("AddRef", d3d8_dev_addref, 1) \
    X("Release", d3d8_dev_release, 1) \
    X("TestCooperativeLevel", d3d8_na1, 1) \
    X("GetAvailableTextureMem", d3d8_na1, 1) \
    X("ResourceManagerDiscardBytes", d3d8_na2, 2) \
    X("GetDirect3D", d3d8_na2, 2) \
    X("GetDeviceCaps", d3d8_na2, 2) \
    X("GetDisplayMode", d3d8_na2, 2) \
    X("GetCreationParameters", d3d8_na2, 2) \
    X("SetCursorProperties", d3d8_na4, 4) \
    X("SetCursorPosition", d3d8_na4, 4) \
    X("ShowCursor", d3d8_na2, 2) \
    X("CreateAdditionalSwapChain", d3d8_na3, 3) \
    X("Reset", d3d8_na2, 2) \
    X("Present", d3d8_na5, 5) \
    X("GetBackBuffer", d3d8_na4, 4) \
    X("GetRasterStatus", d3d8_na2, 2) \
    X("SetGammaRamp", d3d8_na3, 3) \
    X("GetGammaRamp", d3d8_na2, 2) \
    X("CreateTexture", d3d8_na8, 8) \
    X("CreateVolumeTexture", d3d8_na9, 9) \
    X("CreateCubeTexture", d3d8_na7, 7) \
    X("CreateVertexBuffer", d3d8_na6, 6) \
    X("CreateIndexBuffer", d3d8_na6, 6) \
    X("CreateRenderTarget", d3d8_na7, 7) \
    X("CreateDepthStencilSurface", d3d8_na6, 6) \
    X("CreateImageSurface", d3d8_na5, 5) \
    X("CopyRects", d3d8_na6, 6) \
    X("UpdateTexture", d3d8_na3, 3) \
    X("GetFrontBuffer", d3d8_na2, 2) \
    X("SetRenderTarget", d3d8_na3, 3) \
    X("GetRenderTarget", d3d8_na2, 2) \
    X("GetDepthStencilSurface", d3d8_na2, 2) \
    X("BeginScene", d3d8_na1, 1) \
    X("EndScene", d3d8_na1, 1) \
    X("Clear", d3d8_na7, 7) \
    X("SetTransform", d3d8_na3, 3) \
    X("GetTransform", d3d8_na3, 3) \
    X("MultiplyTransform", d3d8_na3, 3) \
    X("SetViewport", d3d8_na2, 2) \
    X("GetViewport", d3d8_na2, 2) \
    X("SetMaterial", d3d8_na2, 2) \
    X("GetMaterial", d3d8_na2, 2) \
    X("SetLight", d3d8_na3, 3) \
    X("GetLight", d3d8_na3, 3) \
    X("LightEnable", d3d8_na3, 3) \
    X("GetLightEnable", d3d8_na3, 3) \
    X("SetClipPlane", d3d8_na3, 3) \
    X("GetClipPlane", d3d8_na3, 3) \
    X("SetRenderState", d3d8_na3, 3) \
    X("GetRenderState", d3d8_na3, 3) \
    X("BeginStateBlock", d3d8_na1, 1) \
    X("EndStateBlock", d3d8_na2, 2) \
    X("ApplyStateBlock", d3d8_na2, 2) \
    X("CaptureStateBlock", d3d8_na2, 2) \
    X("DeleteStateBlock", d3d8_na2, 2) \
    X("CreateStateBlock", d3d8_na3, 3) \
    X("SetClipStatus", d3d8_na2, 2) \
    X("GetClipStatus", d3d8_na2, 2) \
    X("GetTexture", d3d8_na3, 3) \
    X("SetTexture", d3d8_na3, 3) \
    X("GetTextureStageState", d3d8_na4, 4) \
    X("SetTextureStageState", d3d8_na4, 4) \
    X("ValidateDevice", d3d8_na2, 2) \
    X("GetInfo", d3d8_na4, 4) \
    X("SetPaletteEntries", d3d8_na3, 3) \
    X("GetPaletteEntries", d3d8_na3, 3) \
    X("SetCurrentTexturePalette", d3d8_na2, 2) \
    X("GetCurrentTexturePalette", d3d8_na2, 2) \
    X("DrawPrimitive", d3d8_na4, 4) \
    X("DrawIndexedPrimitive", d3d8_na6, 6) \
    X("DrawPrimitiveUP", d3d8_na5, 5) \
    X("DrawIndexedPrimitiveUP", d3d8_na9, 9) \
    X("ProcessVertices", d3d8_na6, 6) \
    X("CreateVertexShader", d3d8_na5, 5) \
    X("SetVertexShader", d3d8_na2, 2) \
    X("GetVertexShader", d3d8_na2, 2) \
    X("DeleteVertexShader", d3d8_na2, 2) \
    X("SetVertexShaderConstant", d3d8_na4, 4) \
    X("GetVertexShaderConstant", d3d8_na4, 4) \
    X("GetVertexShaderDeclaration", d3d8_na4, 4) \
    X("GetVertexShaderFunction", d3d8_na4, 4) \
    X("SetStreamSource", d3d8_na4, 4) \
    X("GetStreamSource", d3d8_na4, 4) \
    X("SetIndices", d3d8_na3, 3) \
    X("GetIndices", d3d8_na3, 3) \
    X("CreatePixelShader", d3d8_na3, 3) \
    X("SetPixelShader", d3d8_na2, 2) \
    X("GetPixelShader", d3d8_na2, 2) \
    X("DeletePixelShader", d3d8_na2, 2) \
    X("SetPixelShaderConstant", d3d8_na4, 4) \
    X("GetPixelShaderConstant", d3d8_na4, 4) \
    X("GetPixelShaderFunction", d3d8_na4, 4) \
    X("DrawRectPatch", d3d8_na4, 4) \
    X("DrawTriPatch", d3d8_na4, 4) \
    X("DeletePatch", d3d8_na2, 2)

#define D3D8_D3D_ENTRY(name, fn, argc) { "IDirect3D8::" name, fn },
#define D3D8_DEV_ENTRY(name, fn, argc) { "IDirect3DDevice8::" name, fn },

const win32hle_shim d3d8_methods[] = {
    D3D8_D3D_METHODS(D3D8_D3D_ENTRY)
    { 0, 0 }
};
const win32hle_shim d3d8_device_methods[] = {
    D3D8_DEVICE_METHODS(D3D8_DEV_ENTRY)
    { 0, 0 }
};

static uint32_t d3d8_d3d_vt(void) {
    static uint32_t vt;
    if (!vt) vt = hle_com_vtable(d3d8_methods);
    return vt;
}
static uint32_t d3d8_device_vt(void) {
    static uint32_t vt;
    if (!vt) vt = hle_com_vtable(d3d8_device_methods);
    return vt;
}

/* Direct3DCreate8(UINT SDKVersion) -> IDirect3D8*. A wrong SDK version gets a
 * NULL pointer, as the real DLL does; a correct one gets a fresh object with
 * one reference, which the guest must Release. */
static void d3d8_Direct3DCreate8(void) {
    if (A32(0) != D3D_SDK_VERSION) RET(0u, 1);
    uint32_t obj = hle_com_new(d3d8_d3d_vt(), 8);
    if (d3d8_trace()) fprintf(stderr, "[d3d8] Direct3DCreate8(%u) -> %08x\n", A32(0), obj);
    RET(obj, 1);
}

/* Module export table: register with win32hle_register(win32hle_d3d8). */
const win32hle_shim win32hle_d3d8[] = {
    { "Direct3DCreate8", d3d8_Direct3DCreate8 },
    { 0, 0 }
};
