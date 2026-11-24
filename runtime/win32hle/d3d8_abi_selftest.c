/* d3d8_abi_selftest.c - D3D8 guest COM vtable ABI and HRESULT fixture.
 *
 * Complements tests/test_d3d8.py: that harness compiles d3d8.c against a fake
 * HLE header and checks stdcall pops exhaustively. This selftest links the
 * real win32hle layer, walks the *built guest vtables* slot by slot against
 * wine's d3d8.h method order, and drives each implemented entry point the way
 * a lifted guest does (through hle_call_guest), checking the HRESULT/out
 * pointer/reference-count contract. It also pins the IID_IDirect3DDevice8
 * fix: QI with the GUID from d3d8.h must answer S_OK, not E_NOINTERFACE.
 *
 * No rasteriser is pretended: every device method without a host-side state
 * answer must still return D3DERR_NOTAVAILABLE.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

/* win32hle.h is shared and deliberately does not declare this table yet
 * (see docs/UPSTREAM_REUSE_ISSUE3.md); declare it locally instead of editing
 * a header this change does not own. */
extern const win32hle_shim win32hle_d3d8[];

#define D3D_OK_VALUE                0u
#define E_NOINTERFACE_VALUE         0x80004002u
#define E_POINTER_VALUE             0x80004003u
#define D3DERR_INVALIDCALL_VALUE    0x8876086Cu
#define D3DERR_NOTAVAILABLE_VALUE   0x8876086Au
#define D3D_SDK_VERSION_VALUE       220u

/* REFIID bytes in on-disk order: first three fields little endian. */
static const uint8_t IID_UNKNOWN[16] = {
    0, 0, 0, 0, 0, 0, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46
};
static const uint8_t IID_D3D8[16] = { /* {1DD9E8DA-1C77-4D40-B0CF-98FEFDFF9512} */
    0xDA, 0xE8, 0xD9, 0x1D, 0x77, 0x1C, 0x40, 0x4D,
    0xB0, 0xCF, 0x98, 0xFE, 0xFD, 0xFF, 0x95, 0x12
};
/* IID_IDirect3DDevice8 from wine's include/d3d8.h (and the retail SDK). */
static const uint8_t IID_DEVICE8[16] = { /* {7385E5DF-8FE8-41D5-86B6-D7B48547B6CF} */
    0xDF, 0xE5, 0x85, 0x73, 0xE5, 0x8F, 0xD5, 0x41,
    0x86, 0xB6, 0xD7, 0xB4, 0x85, 0x47, 0xB6, 0xCF
};
/* The old, non-standard GUID still accepted as a fixture compatibility alias. */
static const uint8_t IID_DEVICE8_LEGACY[16] = {
    0xDF, 0xE2, 0x85, 0x73, 0xE8, 0xFE, 0xD5, 0x41,
    0x86, 0xB3, 0xC0, 0x7E, 0x4D, 0x06, 0xB6, 0xDD
};
static const uint8_t IID_BOGUS[16] = {
    0x11, 0x22, 0x33, 0x44, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x99
};

/* The lift's generated dispatch table is empty for this fixture. */
const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t vtcall(uint32_t vt, unsigned slot, int argc, const uint32_t *args) {
    return hle_call_guest(MEM32(vt + 4u * slot), argc, args);
}
static uint32_t copy_iid(const uint8_t iid[16]) {
    uint32_t va = hle_va(hle_alloc(16));
    memcpy(hle_ptr(va), iid, 16);
    return va;
}

int main(void) {
    recomp_host_init();
    win32hle_register(win32hle_d3d8);
    mach_enter();

    /* ---- export and factory ---- */
    ok("Direct3DCreate8 registered", hle_resolve("Direct3DCreate8") != 0);
    ok("wrong SDK version returns NULL", hle_call_guest(
        hle_resolve("Direct3DCreate8"), 1, (uint32_t[]){ D3D_SDK_VERSION_VALUE - 1u }) == 0u);

    uint32_t d3d = hle_call_guest(hle_resolve("Direct3DCreate8"), 1,
                                  (uint32_t[]){ D3D_SDK_VERSION_VALUE });
    ok("factory returns an object", d3d != 0 && d3d != 0xFFFFFFFFu);
    uint32_t vt = MEM32(d3d);
    ok("object vtable set", vt != 0);
    ok("object refcount starts at 1", MEM32(d3d + 4u) == 1u);

    /* ---- IDirect3D8: 16 slots in wine d3d8.h order ---- */
    static const char *const d3d_names[16] = {
        "QueryInterface", "AddRef", "Release", "RegisterSoftwareDevice",
        "GetAdapterCount", "GetAdapterIdentifier", "GetAdapterModeCount",
        "EnumAdapterModes", "GetAdapterDisplayMode", "CheckDeviceType",
        "CheckDeviceFormat", "CheckDeviceMultiSampleType", "CheckDepthStencilMatch",
        "GetDeviceCaps", "GetAdapterMonitor", "CreateDevice"
    };
    for (unsigned i = 0; i < 16; i++) {
        char name[80];
        snprintf(name, sizeof name, "IDirect3D8::%s", d3d_names[i]);
        ok("IDirect3D8 vtable slot", MEM32(vt + 4u * i) == hle_resolve(name));
    }

    /* ---- IUnknown HRESULTs on the D3D object ---- */
    uint32_t out = hle_va(hle_alloc(4));
    MEM32(out) = 0xDEADBEEFu;
    ok("QI(IID_IUnknown) is S_OK", vtcall(vt, 0, 3,
        (uint32_t[]){ d3d, copy_iid(IID_UNKNOWN), out }) == D3D_OK_VALUE);
    ok("QI(IID_IUnknown) identity", MEM32(out) == d3d);
    ok("QI(IID_IUnknown) AddRef", MEM32(d3d + 4u) == 2u);
    vtcall(vt, 2, 1, &d3d); /* undo the QI reference */
    ok("QI(IID_IDirect3D8) is S_OK", vtcall(vt, 0, 3,
        (uint32_t[]){ d3d, copy_iid(IID_D3D8), out }) == D3D_OK_VALUE);
    vtcall(vt, 2, 1, &d3d);
    MEM32(out) = 0xDEADBEEFu;
    ok("QI(bogus IID) is E_NOINTERFACE", vtcall(vt, 0, 3,
        (uint32_t[]){ d3d, copy_iid(IID_BOGUS), out }) == E_NOINTERFACE_VALUE);
    ok("QI(bogus IID) clears output", MEM32(out) == 0u);
    ok("QI(NULL iid) is E_POINTER", vtcall(vt, 0, 3,
        (uint32_t[]){ d3d, 0u, out }) == E_POINTER_VALUE);
    ok("QI(NULL out) is E_POINTER", vtcall(vt, 0, 3,
        (uint32_t[]){ d3d, copy_iid(IID_D3D8), 0u }) == E_POINTER_VALUE);

    /* ---- adapter adapter methods backed by the shared display mode ---- */
    ok("GetAdapterCount", vtcall(vt, 4, 1, &d3d) == 1u);
    ok("GetAdapterModeCount(adapter 0)", vtcall(vt, 6, 2,
        (uint32_t[]){ d3d, 0u }) == 1u);
    ok("GetAdapterModeCount(bad adapter)", vtcall(vt, 6, 2,
        (uint32_t[]){ d3d, 1u }) == 0u);

    uint32_t mode = hle_va(hle_alloc(16));
    memset(hle_ptr(mode), 0, 16);
    ok("GetAdapterDisplayMode is S_OK", vtcall(vt, 8, 3,
        (uint32_t[]){ d3d, 0u, mode }) == D3D_OK_VALUE);
    ok("display mode width is sane", MEM32(mode) > 0u);
    ok("display mode height is sane", MEM32(mode + 4u) > 0u);
    ok("display mode format is D3DFMT_R5G6B5 or X8R8G8B8",
        MEM32(mode + 12u) == 23u || MEM32(mode + 12u) == 22u);
    ok("GetAdapterDisplayMode(bad adapter) is INVALIDCALL", vtcall(vt, 8, 3,
        (uint32_t[]){ d3d, 1u, mode }) == D3DERR_INVALIDCALL_VALUE);
    ok("GetAdapterDisplayMode(NULL) is E_POINTER", vtcall(vt, 8, 3,
        (uint32_t[]){ d3d, 0u, 0u }) == E_POINTER_VALUE);

    memset(hle_ptr(mode), 0, 16);
    ok("EnumAdapterModes is S_OK", vtcall(vt, 7, 4,
        (uint32_t[]){ d3d, 0u, 0u, mode }) == D3D_OK_VALUE);
    ok("enumerated mode width is sane", MEM32(mode) > 0u);
    ok("enumerated mode height is sane", MEM32(mode + 4u) > 0u);
    ok("enumerated mode format is valid",
        MEM32(mode + 12u) == 23u || MEM32(mode + 12u) == 22u);
    ok("EnumAdapterModes(bad index) is INVALIDCALL", vtcall(vt, 7, 4,
        (uint32_t[]){ d3d, 0u, 1u, mode }) == D3DERR_INVALIDCALL_VALUE);
    ok("EnumAdapterModes(NULL) is E_POINTER", vtcall(vt, 7, 4,
        (uint32_t[]){ d3d, 0u, 0u, 0u }) == E_POINTER_VALUE);

    /* A method with no host-side state must not fake success. */
    ok("CheckDeviceType is honestly NOTAVAILABLE", vtcall(vt, 9, 6,
        (uint32_t[]){ d3d, 0u, 0u, 22u, 22u, 1u }) == D3DERR_NOTAVAILABLE_VALUE);

    /* ---- CreateDevice and the device vtable ---- */
    uint32_t dev_out = hle_va(hle_alloc(4));
    MEM32(dev_out) = 0xDEADBEEFu;
    ok("CreateDevice is S_OK", vtcall(vt, 15, 7,
        (uint32_t[]){ d3d, 0u, 0u, 0u, 1u, 1u, dev_out }) == D3D_OK_VALUE);
    uint32_t dev = MEM32(dev_out);
    ok("device object created", dev != 0 && dev != 0xDEADBEEFu);
    uint32_t devvt = MEM32(dev);
    ok("device vtable distinct from D3D vtable", devvt != 0 && devvt != vt);
    ok("device refcount starts at 1", MEM32(dev + 4u) == 1u);

    /* IDirect3DDevice8: 97 slots in wine d3d8.h order. */
    static const char *const dev_names[97] = {
        "QueryInterface", "AddRef", "Release", "TestCooperativeLevel",
        "GetAvailableTextureMem", "ResourceManagerDiscardBytes", "GetDirect3D",
        "GetDeviceCaps", "GetDisplayMode", "GetCreationParameters",
        "SetCursorProperties", "SetCursorPosition", "ShowCursor",
        "CreateAdditionalSwapChain", "Reset", "Present", "GetBackBuffer",
        "GetRasterStatus", "SetGammaRamp", "GetGammaRamp", "CreateTexture",
        "CreateVolumeTexture", "CreateCubeTexture", "CreateVertexBuffer",
        "CreateIndexBuffer", "CreateRenderTarget", "CreateDepthStencilSurface",
        "CreateImageSurface", "CopyRects", "UpdateTexture", "GetFrontBuffer",
        "SetRenderTarget", "GetRenderTarget", "GetDepthStencilSurface",
        "BeginScene", "EndScene", "Clear", "SetTransform", "GetTransform",
        "MultiplyTransform", "SetViewport", "GetViewport", "SetMaterial",
        "GetMaterial", "SetLight", "GetLight", "LightEnable", "GetLightEnable",
        "SetClipPlane", "GetClipPlane", "SetRenderState", "GetRenderState",
        "BeginStateBlock", "EndStateBlock", "ApplyStateBlock",
        "CaptureStateBlock", "DeleteStateBlock", "CreateStateBlock",
        "SetClipStatus", "GetClipStatus", "GetTexture", "SetTexture",
        "GetTextureStageState", "SetTextureStageState", "ValidateDevice",
        "GetInfo", "SetPaletteEntries", "GetPaletteEntries",
        "SetCurrentTexturePalette", "GetCurrentTexturePalette", "DrawPrimitive",
        "DrawIndexedPrimitive", "DrawPrimitiveUP", "DrawIndexedPrimitiveUP",
        "ProcessVertices", "CreateVertexShader", "SetVertexShader",
        "GetVertexShader", "DeleteVertexShader", "SetVertexShaderConstant",
        "GetVertexShaderConstant", "GetVertexShaderDeclaration",
        "GetVertexShaderFunction", "SetStreamSource", "GetStreamSource",
        "SetIndices", "GetIndices", "CreatePixelShader", "SetPixelShader",
        "GetPixelShader", "DeletePixelShader", "SetPixelShaderConstant",
        "GetPixelShaderConstant", "GetPixelShaderFunction", "DrawRectPatch",
        "DrawTriPatch", "DeletePatch"
    };
    for (unsigned i = 0; i < 97; i++) {
        char name[96];
        snprintf(name, sizeof name, "IDirect3DDevice8::%s", dev_names[i]);
        ok("IDirect3DDevice8 vtable slot", MEM32(devvt + 4u * i) == hle_resolve(name));
    }

    /* ---- device IID HRESULTs, including the corrected GUID regression ---- */
    MEM32(out) = 0xDEADBEEFu;
    ok("device QI(IID_IDirect3DDevice8 from d3d8.h) is S_OK", vtcall(devvt, 0, 3,
        (uint32_t[]){ dev, copy_iid(IID_DEVICE8), out }) == D3D_OK_VALUE);
    ok("device QI(correct IID) identity", MEM32(out) == dev);
    ok("device QI(correct IID) AddRef", MEM32(dev + 4u) == 2u);
    vtcall(devvt, 2, 1, &dev); /* undo */
    ok("device QI(legacy alias) is S_OK", vtcall(devvt, 0, 3,
        (uint32_t[]){ dev, copy_iid(IID_DEVICE8_LEGACY), out }) == D3D_OK_VALUE);
    vtcall(devvt, 2, 1, &dev);
    ok("device QI(IID_IUnknown) is S_OK", vtcall(devvt, 0, 3,
        (uint32_t[]){ dev, copy_iid(IID_UNKNOWN), out }) == D3D_OK_VALUE);
    vtcall(devvt, 2, 1, &dev);
    MEM32(out) = 0xDEADBEEFu;
    ok("device QI(bogus) is E_NOINTERFACE", vtcall(devvt, 0, 3,
        (uint32_t[]){ dev, copy_iid(IID_BOGUS), out }) == E_NOINTERFACE_VALUE);
    ok("device QI(bogus) clears output", MEM32(out) == 0u);
    ok("device QI(NULL iid) is E_POINTER", vtcall(devvt, 0, 3,
        (uint32_t[]){ dev, 0u, out }) == E_POINTER_VALUE);
    ok("device QI(NULL out) is E_POINTER", vtcall(devvt, 0, 3,
        (uint32_t[]){ dev, copy_iid(IID_DEVICE8), 0u }) == E_POINTER_VALUE);

    /* No rasteriser behind any of these: honest D3DERR_NOTAVAILABLE. */
    ok("Present is NOTAVAILABLE", vtcall(devvt, 15, 5,
        (uint32_t[]){ dev, 0u, 0u, 0u, 0u }) == D3DERR_NOTAVAILABLE_VALUE);
    ok("CreateTexture is NOTAVAILABLE", vtcall(devvt, 20, 8,
        (uint32_t[]){ dev, 0u, 0u, 0u, 0u, 0u, 0u, 0u }) == D3DERR_NOTAVAILABLE_VALUE);
    ok("Clear is NOTAVAILABLE", vtcall(devvt, 36, 7,
        (uint32_t[]){ dev, 0u, 0u, 0u, 0u, 0u, 0u }) == D3DERR_NOTAVAILABLE_VALUE);
    ok("DrawPrimitive is NOTAVAILABLE", vtcall(devvt, 70, 4,
        (uint32_t[]){ dev, 0u, 0u, 0u }) == D3DERR_NOTAVAILABLE_VALUE);
    ok("DeletePatch is NOTAVAILABLE", vtcall(devvt, 96, 2,
        (uint32_t[]){ dev, 0u }) == D3DERR_NOTAVAILABLE_VALUE);

    /* Reference counting: final Release destroys each object. */
    ok("device final Release destroys", vtcall(devvt, 2, 1, &dev) == 0u);
    ok("D3D final Release destroys", vtcall(vt, 2, 1, &d3d) == 0u);

    mach_leave();
    if (!fails) printf("d3d8_abi_selftest: all checks passed\n");
    else printf("d3d8_abi_selftest: %d check(s) failed\n", fails);
    return fails != 0;
}
