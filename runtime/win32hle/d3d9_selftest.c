/* d3d9_selftest.c - Direct3D 9 guest COM frontend and vtable ABI fixture. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

#define D3D_OK_VALUE 0u
#define E_NOINTERFACE_VALUE 0x80004002u
#define E_POINTER_VALUE 0x80004003u
#define D3DERR_INVALIDCALL_VALUE 0x8876086Cu
#define D3DERR_NOTAVAILABLE_VALUE 0x8876086Au
#define D3D_SDK_VERSION_VALUE 32u

static const uint8_t IID_UNKNOWN[16] = {
    0, 0, 0, 0, 0, 0, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46
};
static const uint8_t IID_D3D9[16] = {
    0xCA, 0xCB, 0xBD, 0x81, 0xD4, 0x64, 0x6D, 0x42,
    0xAE, 0x8D, 0xAD, 0x01, 0x47, 0xF4, 0x27, 0x5C
};
static const uint8_t IID_DEVICE9[16] = {
    0x96, 0x3B, 0x22, 0xD0, 0x7A, 0xBF, 0xFD, 0x43,
    0x92, 0xBD, 0xA4, 0x3B, 0x0D, 0x82, 0xB9, 0xEB
};
static const uint8_t IID_BOGUS[16] = {
    0x11, 0x22, 0x33, 0x44, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x99
};

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int argc, const uint32_t *args) {
    uint32_t fn = hle_resolve(name);
    return fn ? hle_call_guest(fn, argc, args) : 0xFFFFFFFFu;
}
static uint32_t vtcall(uint32_t vt, unsigned slot, int argc, const uint32_t *args) {
    return hle_call_guest(MEM32(vt + 4u * slot), argc, args);
}
/* hle_call_guest restores the register file after a shim returns, so it cannot
 * see a wrong stdcall purge. Drive the registered shim the way lifted code
 * does (synthetic return address + args on a guest stack), call it directly,
 * and report how many words it popped. That makes a missing argument slot in
 * RET() a deterministic failure instead of an invisible guest-stack leak. */
static uint32_t shim_pops(const char *name, int argc, const uint32_t *args,
                          uint32_t *out_eax) {
    uint32_t va = hle_resolve(name);
    recomp_func_t fn = va ? recomp_lookup_import(va) : 0;
    if (!fn) return 0xFFFFFFFFu;
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t scratch = hle_va(hle_alloc(64));       /* ret + up to 7 args */
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

int main(void) {
    recomp_host_init();
    win32hle_register(win32hle_d3d9);
    mach_enter();

    ok("Direct3DCreate9 registered", hle_resolve("Direct3DCreate9") != 0);
    ok("wrong SDK version returns NULL", call("Direct3DCreate9", 1,
       (uint32_t[]){ 0u }) == 0u);

    /* The factory returns the object directly in EAX. */
    uint32_t d3d = hle_call_guest(hle_resolve("Direct3DCreate9"), 1,
                                  (uint32_t[]){ D3D_SDK_VERSION_VALUE });
    ok("factory returns object", d3d != 0 && d3d != 0xFFFFFFFFu);
    uint32_t vt = MEM32(d3d);
    ok("object vtable set", vt != 0);
    ok("object refcount starts at 1", MEM32(d3d + 4u) == 1u);

    /* IDirect3D9 vtable: 17 entries in official order. */
    static const char *const d3d_names[17] = {
        "QueryInterface", "AddRef", "Release", "RegisterSoftwareDevice",
        "GetAdapterCount", "GetAdapterIdentifier", "GetAdapterModeCount",
        "EnumAdapterModes", "GetAdapterDisplayMode", "CheckDeviceType",
        "CheckDeviceFormat", "CheckDeviceMultiSampleType", "CheckDepthStencilMatch",
        "CheckDeviceFormatConversion", "GetDeviceCaps", "GetAdapterMonitor",
        "CreateDevice"
    };
    for (unsigned i = 0; i < 17; i++) {
        char name[64];
        snprintf(name, sizeof name, "D3D9::Object::%s", d3d_names[i]);
        ok("D3D9 vtable slot", MEM32(vt + 4u * i) == hle_resolve(name));
    }

    /* IUnknown identity from vtable slot 0. */
    uint32_t out = hle_va(hle_alloc(4));
    MEM32(out) = 0xDEADBEEFu;
    ok("QI(IID_IDirect3D9)", vtcall(vt, 0, 3,
       (uint32_t[]){ d3d, copy_iid(IID_D3D9), out }) == D3D_OK_VALUE);
    ok("D3D9 QI identity", MEM32(out) == d3d);
    ok("D3D9 QI AddRef", MEM32(d3d + 4u) == 2u);
    vtcall(vt, 2, 1, &d3d);                       /* undo QI reference */

    ok("QI(IID_IUnknown)",
       vtcall(vt, 0, 3, (uint32_t[]){ d3d, copy_iid(IID_UNKNOWN), out }) == D3D_OK_VALUE);
    vtcall(vt, 2, 1, &d3d);
    MEM32(out) = 0xDEADBEEFu;
    ok("QI(unsupported IID)", vtcall(vt, 0, 3,
       (uint32_t[]){ d3d, copy_iid(IID_BOGUS), out }) == E_NOINTERFACE_VALUE);
    ok("QI clears output on failure", MEM32(out) == 0u);
    ok("QI(NULL iid) is E_POINTER", vtcall(vt, 0, 3,
       (uint32_t[]){ d3d, 0u, out }) == E_POINTER_VALUE);
    ok("QI(NULL out) is E_POINTER", vtcall(vt, 0, 3,
       (uint32_t[]){ d3d, copy_iid(IID_D3D9), 0u }) == E_POINTER_VALUE);

    /* Adapter queries backed by the shared display mode. Wine's d3d9.h gives
     * GetAdapterModeCount(Adapter, Format) and EnumAdapterModes(Adapter,
     * Format, Mode, pMode); D3D8's versions have no Format slot. */
    ok("GetAdapterCount", vtcall(vt, 4, 1, &d3d) == 1u);
    uint32_t mode = hle_va(hle_alloc(16));
    memset(hle_ptr(mode), 0, 16);
    ok("GetAdapterDisplayMode", vtcall(vt, 8, 3,
       (uint32_t[]){ d3d, 0u, mode }) == D3D_OK_VALUE);
    ok("display mode width", MEM32(mode) == 640u);
    ok("display mode height", MEM32(mode + 4u) == 480u);
    /* The shared display mode decides the format: 16-bit -> R5G6B5, else X8R8G8B8. */
    ok("display mode format", MEM32(mode + 12u) == 22u || MEM32(mode + 12u) == 23u);
    uint32_t fmt = MEM32(mode + 12u);

    ok("GetAdapterModeCount(real format)", vtcall(vt, 6, 3,
       (uint32_t[]){ d3d, 0u, fmt }) == 1u);
    ok("GetAdapterModeCount(D3DFMT_UNKNOWN)", vtcall(vt, 6, 3,
       (uint32_t[]){ d3d, 0u, 0u }) == 1u);
    ok("GetAdapterModeCount(bad adapter)", vtcall(vt, 6, 3,
       (uint32_t[]){ d3d, 1u, fmt }) == 0u);
    uint32_t hr = 0u;
    ok("GetAdapterModeCount pops ret + 3 args", shim_pops(
       "D3D9::Object::GetAdapterModeCount", 3,
       (uint32_t[]){ d3d, 0u, fmt }, &hr) == 4u);

    memset(hle_ptr(mode), 0, 16);
    ok("EnumAdapterModes(real format)", vtcall(vt, 7, 5,
       (uint32_t[]){ d3d, 0u, fmt, 0u, mode }) == D3D_OK_VALUE);
    ok("enumerated mode format", MEM32(mode + 12u) == fmt);
    ok("EnumAdapterModes(bad index) is INVALIDCALL", vtcall(vt, 7, 5,
       (uint32_t[]){ d3d, 0u, fmt, 1u, mode }) == D3DERR_INVALIDCALL_VALUE);
    ok("EnumAdapterModes(bad format) is INVALIDCALL", vtcall(vt, 7, 5,
       (uint32_t[]){ d3d, 0u, fmt == 22u ? 23u : 22u, 0u, mode }) == D3DERR_INVALIDCALL_VALUE);
    ok("EnumAdapterModes(NULL out) is E_POINTER", vtcall(vt, 7, 5,
       (uint32_t[]){ d3d, 0u, fmt, 0u, 0u }) == E_POINTER_VALUE);
    ok("EnumAdapterModes pops ret + 5 args on success", shim_pops(
       "D3D9::Object::EnumAdapterModes", 5,
       (uint32_t[]){ d3d, 0u, fmt, 0u, mode }, &hr) == 6u);
    ok("EnumAdapterModes pops ret + 5 args on failure", shim_pops(
       "D3D9::Object::EnumAdapterModes", 5,
       (uint32_t[]){ d3d, 0u, fmt, 1u, mode }, &hr) == 6u);
    ok("CheckDeviceType is honest NA", vtcall(vt, 9, 6,
       (uint32_t[]){ d3d, 0u, 0u, 22u, 22u, 1u }) == D3DERR_NOTAVAILABLE_VALUE);

    /* CreateDevice returns a device object owned by no rasteriser. */
    uint32_t dev_out = hle_va(hle_alloc(4));
    MEM32(dev_out) = 0xDEADBEEFu;
    ok("CreateDevice succeeds as a COM object", vtcall(vt, 16, 7,
       (uint32_t[]){ d3d, 0u, 0u, 0u, 1u, 1u, dev_out }) == D3D_OK_VALUE);
    uint32_t dev = MEM32(dev_out);
    ok("device object created", dev != 0 && dev != 0xDEADBEEFu);
    uint32_t devvt = MEM32(dev);
    ok("device vtable distinct", devvt != 0 && devvt != vt);
    ok("device refcount 1", MEM32(dev + 4u) == 1u);

    /* IDirect3DDevice9 vtable: 119 entries, official order. A wrong slot here
     * would give the guest a method with the wrong stdcall purge count. */
    static const char *const dev_names[119] = {
        "QueryInterface", "AddRef", "Release", "TestCooperativeLevel",
        "GetAvailableTextureMem", "EvictManagedResources", "GetDirect3D",
        "GetDeviceCaps", "GetDisplayMode", "GetCreationParameters",
        "SetCursorProperties", "SetCursorPosition", "ShowCursor",
        "CreateAdditionalSwapChain", "GetSwapChain", "GetNumberOfSwapChains",
        "Reset", "Present", "GetBackBuffer", "GetRasterStatus",
        "SetDialogBoxMode", "SetGammaRamp", "GetGammaRamp", "CreateTexture",
        "CreateVolumeTexture", "CreateCubeTexture", "CreateVertexBuffer",
        "CreateIndexBuffer", "CreateRenderTarget", "CreateDepthStencilSurface",
        "UpdateSurface", "UpdateTexture", "GetRenderTargetData",
        "GetFrontBufferData", "StretchRect", "ColorFill",
        "CreateOffscreenPlainSurface", "SetRenderTarget", "GetRenderTarget",
        "SetDepthStencilSurface", "GetDepthStencilSurface", "BeginScene",
        "EndScene", "Clear", "SetTransform", "GetTransform", "MultiplyTransform",
        "SetViewport", "GetViewport", "SetMaterial", "GetMaterial", "SetLight",
        "GetLight", "LightEnable", "GetLightEnable", "SetClipPlane",
        "GetClipPlane", "SetRenderState", "GetRenderState", "CreateStateBlock",
        "BeginStateBlock", "EndStateBlock", "SetClipStatus", "GetClipStatus",
        "GetTexture", "SetTexture", "GetTextureStageState",
        "SetTextureStageState", "GetSamplerState", "SetSamplerState",
        "ValidateDevice", "SetPaletteEntries", "GetPaletteEntries",
        "SetCurrentTexturePalette", "GetCurrentTexturePalette", "SetScissorRect",
        "GetScissorRect", "SetSoftwareVertexProcessing",
        "GetSoftwareVertexProcessing", "SetNPatchMode", "GetNPatchMode",
        "DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitiveUP",
        "DrawIndexedPrimitiveUP", "ProcessVertices", "CreateVertexDeclaration",
        "SetVertexDeclaration", "GetVertexDeclaration", "SetFVF", "GetFVF",
        "CreateVertexShader", "SetVertexShader", "GetVertexShader",
        "SetVertexShaderConstantF", "GetVertexShaderConstantF",
        "SetVertexShaderConstantI", "GetVertexShaderConstantI",
        "SetVertexShaderConstantB", "GetVertexShaderConstantB", "SetStreamSource",
        "GetStreamSource", "SetStreamSourceFreq", "GetStreamSourceFreq",
        "SetIndices", "GetIndices", "CreatePixelShader", "SetPixelShader",
        "GetPixelShader", "SetPixelShaderConstantF", "GetPixelShaderConstantF",
        "SetPixelShaderConstantI", "GetPixelShaderConstantI",
        "SetPixelShaderConstantB", "GetPixelShaderConstantB", "DrawRectPatch",
        "DrawTriPatch", "DeletePatch", "CreateQuery"
    };
    for (unsigned i = 0; i < 119; i++) {
        char name[80];
        snprintf(name, sizeof name, "D3D9::Device::%s", dev_names[i]);
        ok("device vtable slot", MEM32(devvt + 4u * i) == hle_resolve(name));
    }

    MEM32(out) = 0xDEADBEEFu;
    ok("device QI(IDirect3DDevice9)", vtcall(devvt, 0, 3,
       (uint32_t[]){ dev, copy_iid(IID_DEVICE9), out }) == D3D_OK_VALUE);
    ok("device QI identity", MEM32(out) == dev);
    vtcall(devvt, 2, 1, &dev);
    ok("CreateTexture is honest NA", vtcall(devvt, 23, 9,
       (uint32_t[]){ dev, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u }) == D3DERR_NOTAVAILABLE_VALUE);
    ok("BeginScene is honest NA", vtcall(devvt, 41, 1, &dev) == D3DERR_NOTAVAILABLE_VALUE);

    ok("device final Release destroys", vtcall(devvt, 2, 1, &dev) == 0u);
    ok("D3D9 final Release destroys", vtcall(vt, 2, 1, &d3d) == 0u);

    mach_leave();
    if (!fails) printf("d3d9_selftest: all checks passed\n");
    else printf("d3d9_selftest: %d check(s) failed\n", fails);
    return fails != 0;
}
