"""Behavioural regression for the D3D8 guest COM frontend (issue #3).

The vtable order below is the public DirectX 8 ABI, cross-checked against the
IGI-WEB host's ComMethod tables at commit 0a747264 (MIT). It is the *contract*
this shim must expose, not copied host code.

The C harness compiles runtime/win32hle/d3d8.c against a tiny fake HLE header
and drives each shim the way a guest does: arguments are written into a fake
guest stack, the shim is called through its vtable slot, and the returned
HRESULT, stack purge, reference count and vtable order are checked. This
catches a mis-ordered slot or a wrong stdcall pop that a source-text check
would miss.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runtime" / "win32hle" / "d3d8.c"

# (name, arguments after `this`) from the public D3D8 interface / IGI-WEB's
# observed method tables. The harness adds one for `this` when calling.
D3D_METHODS = [
    ("QueryInterface", 2), ("AddRef", 0), ("Release", 0),
    ("RegisterSoftwareDevice", 1), ("GetAdapterCount", 0),
    ("GetAdapterIdentifier", 3), ("GetAdapterModeCount", 1),
    ("EnumAdapterModes", 3), ("GetAdapterDisplayMode", 2),
    ("CheckDeviceType", 5), ("CheckDeviceFormat", 6),
    ("CheckDeviceMultiSampleType", 5), ("CheckDepthStencilMatch", 5),
    ("GetDeviceCaps", 3), ("GetAdapterMonitor", 1), ("CreateDevice", 6),
]

DEVICE_METHODS = [
    ("QueryInterface", 2), ("AddRef", 0), ("Release", 0),
    ("TestCooperativeLevel", 0), ("GetAvailableTextureMem", 0),
    ("ResourceManagerDiscardBytes", 1), ("GetDirect3D", 1),
    ("GetDeviceCaps", 1), ("GetDisplayMode", 1),
    ("GetCreationParameters", 1), ("SetCursorProperties", 3),
    ("SetCursorPosition", 3), ("ShowCursor", 1),
    ("CreateAdditionalSwapChain", 2), ("Reset", 1), ("Present", 4),
    ("GetBackBuffer", 3), ("GetRasterStatus", 1), ("SetGammaRamp", 2),
    ("GetGammaRamp", 1), ("CreateTexture", 7), ("CreateVolumeTexture", 8),
    ("CreateCubeTexture", 6), ("CreateVertexBuffer", 5),
    ("CreateIndexBuffer", 5), ("CreateRenderTarget", 6),
    ("CreateDepthStencilSurface", 5), ("CreateImageSurface", 4),
    ("CopyRects", 5), ("UpdateTexture", 2), ("GetFrontBuffer", 1),
    ("SetRenderTarget", 2), ("GetRenderTarget", 1),
    ("GetDepthStencilSurface", 1), ("BeginScene", 0), ("EndScene", 0),
    ("Clear", 6), ("SetTransform", 2), ("GetTransform", 2),
    ("MultiplyTransform", 2), ("SetViewport", 1), ("GetViewport", 1),
    ("SetMaterial", 1), ("GetMaterial", 1), ("SetLight", 2),
    ("GetLight", 2), ("LightEnable", 2), ("GetLightEnable", 2),
    ("SetClipPlane", 2), ("GetClipPlane", 2), ("SetRenderState", 2),
    ("GetRenderState", 2), ("BeginStateBlock", 0), ("EndStateBlock", 1),
    ("ApplyStateBlock", 1), ("CaptureStateBlock", 1),
    ("DeleteStateBlock", 1), ("CreateStateBlock", 2),
    ("SetClipStatus", 1), ("GetClipStatus", 1), ("GetTexture", 2),
    ("SetTexture", 2), ("GetTextureStageState", 3),
    ("SetTextureStageState", 3), ("ValidateDevice", 1), ("GetInfo", 3),
    ("SetPaletteEntries", 2), ("GetPaletteEntries", 2),
    ("SetCurrentTexturePalette", 1), ("GetCurrentTexturePalette", 1),
    ("DrawPrimitive", 3), ("DrawIndexedPrimitive", 5),
    ("DrawPrimitiveUP", 4), ("DrawIndexedPrimitiveUP", 8),
    ("ProcessVertices", 5), ("CreateVertexShader", 4),
    ("SetVertexShader", 1), ("GetVertexShader", 1),
    ("DeleteVertexShader", 1), ("SetVertexShaderConstant", 3),
    ("GetVertexShaderConstant", 3), ("GetVertexShaderDeclaration", 3),
    ("GetVertexShaderFunction", 3), ("SetStreamSource", 3),
    ("GetStreamSource", 3), ("SetIndices", 2), ("GetIndices", 2),
    ("CreatePixelShader", 2), ("SetPixelShader", 1),
    ("GetPixelShader", 1), ("DeletePixelShader", 1),
    ("SetPixelShaderConstant", 3), ("GetPixelShaderConstant", 3),
    ("GetPixelShaderFunction", 3), ("DrawRectPatch", 3),
    ("DrawTriPatch", 3), ("DeletePatch", 1),
]

FAKE_HEADER = r"""
#ifndef WIN32HLE_H
#define WIN32HLE_H
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef void (*recomp_func_t)(void);
typedef struct { const char *name; recomp_func_t fn; } win32hle_shim;
extern unsigned char g_guest_mem[];
extern uint32_t g_eax, g_esp;
#define MEM32(a) (*(volatile uint32_t *)(void *)((uintptr_t)g_guest_mem + (uint32_t)(a)))
static inline uint32_t hle_a32(int i) { return MEM32(g_esp + 4u + 4u * (uint32_t)i); }
#define A32(i) hle_a32(i)
static inline void *hle_ptr(uint32_t va) { return va ? (void *)((uintptr_t)g_guest_mem + va) : NULL; }
static inline uint32_t hle_va(const void *p) { return p ? (uint32_t)((uintptr_t)p - (uintptr_t)g_guest_mem) : 0; }
#define RET(eax, argc) do { g_eax = (uint32_t)(eax); g_esp += 4u + 4u * (uint32_t)(argc); return; } while (0)
uint32_t hle_com_vtable(const win32hle_shim *methods);
uint32_t hle_com_new(uint32_t vtable, uint32_t size);
void hle_com_free(uint32_t obj);
void hle_dd_mode(int *w, int *h, int *bpp);
#endif
"""


def c_table(name, methods):
    names = ",\n    ".join('"%s"' % n for n, _ in methods)
    counts = ", ".join(str(a + 1) for _, a in methods)
    return ("static const char *const %s_names[%d] = {\n    %s\n};\n"
            "static const int %s_counts[%d] = { %s };\n"
            "static const int %s_count = %d;\n"
            % (name, len(methods), names, name, len(methods), counts,
               name, len(methods)))


HARNESS = r"""
#include "win32hle.h"

unsigned char g_guest_mem[1u << 20];
uint32_t g_eax, g_esp;

extern const win32hle_shim win32hle_d3d8[];
extern const win32hle_shim d3d8_methods[];
extern const win32hle_shim d3d8_device_methods[];

__TABLES__

static uint32_t bump = 0x10000u;
static int fails, frees, nvts;
static uint32_t alloc(uint32_t n) {
    uint32_t r;
    bump = (bump + 15u) & ~15u;
    r = bump;
    bump += n;
    if (bump > sizeof g_guest_mem) abort();
    memset(hle_ptr(r), 0, n);
    return r;
}
static void check(int c, const char *what) {
    if (c) printf("ok %s\n", what);
    else { printf("FAIL %s\n", what); fails++; }
}
uint32_t hle_com_vtable(const win32hle_shim *m) {
    int n = 0, i;
    uint32_t vt, base;
    while (m[n].name) n++;
    vt = alloc(4u * (uint32_t)n);
    base = 0xB0000000u + (uint32_t)nvts * 0x1000u;
    if (nvts < 2) {
        for (i = 0; i < n; i++)
            printf("%s %s\n", nvts == 0 ? "D3D_VT" : "DEV_VT", m[i].name);
    }
    for (i = 0; i < n; i++) MEM32(vt + 4u * (uint32_t)i) = base + (uint32_t)i;
    nvts++;
    return vt;
}
uint32_t hle_com_new(uint32_t vt, uint32_t size) {
    uint32_t o = alloc(size < 8 ? 8 : size);
    MEM32(o) = vt;
    MEM32(o + 4) = 1;
    return o;
}
void hle_com_free(uint32_t obj) { (void)obj; frees++; }
void hle_dd_mode(int *w, int *h, int *bpp) { *w = 800; *h = 600; *bpp = 32; }

static uint32_t call(recomp_func_t fn, int nargs, const uint32_t *args, const char *what) {
    uint32_t before;
    char msg[200];
    int i;
    g_esp = 0x1000u;
    MEM32(g_esp) = 0xDEADBEEFu;
    for (i = 0; i < nargs; i++) MEM32(g_esp + 4u + 4u * (uint32_t)i) = args[i];
    before = g_esp;
    fn();
    snprintf(msg, sizeof msg, "%s/stdcall-pop", what);
    check(g_esp == before + 4u + 4u * (uint32_t)nargs, msg);
    return g_eax;
}
static int implemented(const char *name, const char *const *list, int n) {
    int i;
    for (i = 0; i < n; i++) if (!strcmp(name, list[i])) return 1;
    return 0;
}

int main(void) {
    static const uint8_t iid_unknown[16] = {0,0,0,0,0,0,0,0,0xC0,0,0,0,0,0,0,0x46};
    static const uint8_t iid_d3d8[16] = {0xDA,0xE8,0xD9,0x1D,0x77,0x1C,0x40,0x4D,
                                         0xB0,0xCF,0x98,0xFE,0xFD,0xFF,0x95,0x12};
    static const uint8_t iid_device8[16] = {0xDF,0xE2,0x85,0x73,0xE8,0xFE,0xD5,0x41,
                                            0x86,0xB3,0xC0,0x7E,0x4D,0x06,0xB6,0xDD};
    static const uint8_t iid_bogus[16] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
    static const char *const d3d_impl[] = {"QueryInterface","AddRef","Release",
        "GetAdapterCount","GetAdapterModeCount","EnumAdapterModes",
        "GetAdapterDisplayMode","CreateDevice"};
    static const char *const dev_impl[] = {"QueryInterface","AddRef","Release"};
    uint32_t a[12] = {0}, d3d, dev, out, iid, bogus, mode, pp, devout;
    uint32_t refs;
    int i;

    check(!strcmp(win32hle_d3d8[0].name, "Direct3DCreate8") && win32hle_d3d8[1].name == NULL,
          "export/Direct3DCreate8-shape");
    a[0] = 219;
    check(call(win32hle_d3d8[0].fn, 1, a, "create/wrong-sdk") == 0, "create/wrong-sdk-NULL");
    a[0] = 220;
    d3d = call(win32hle_d3d8[0].fn, 1, a, "create/valid");
    check(d3d != 0, "create/returns-interface");
    refs = MEM32(d3d + 4);
    check(refs == 1, "create/initial-refcount-1");
    check(MEM32(MEM32(d3d)) == 0xB0000000u, "create/vtable-is-IDirect3D8");
    check(nvts == 1, "create/vtable-built-once");

    out = alloc(4); iid = alloc(16); bogus = alloc(16); mode = alloc(16);
    pp = alloc(64); devout = alloc(4);
    memcpy(hle_ptr(iid), iid_unknown, 16);
    memcpy(hle_ptr(bogus), iid_bogus, 16);
    a[0] = d3d; a[1] = iid; a[2] = out;
    check(call(d3d8_methods[0].fn, 3, a, "d3d/QI-IUnknown") == 0, "d3d/QI-IUnknown-S_OK");
    check(MEM32(out) == d3d && MEM32(d3d + 4) == refs + 1, "d3d/QI-adds-ref");
    refs = MEM32(d3d + 4);
    a[1] = bogus; MEM32(out) = 0x12345678u;
    check(call(d3d8_methods[0].fn, 3, a, "d3d/QI-bogus") == 0x80004002u, "d3d/QI-E_NOINTERFACE");
    check(MEM32(out) == 0 && MEM32(d3d + 4) == refs, "d3d/QI-bogus-clears-out");
    a[2] = 0;
    check(call(d3d8_methods[0].fn, 3, a, "d3d/QI-null-out") == 0x80004003u, "d3d/QI-E_POINTER");
    a[1] = 0; a[2] = out;
    check(call(d3d8_methods[0].fn, 3, a, "d3d/QI-null-iid") == 0x80004003u, "d3d/QI-null-iid-E_POINTER");
    memcpy(hle_ptr(iid), iid_d3d8, 16); a[1] = iid;
    check(call(d3d8_methods[0].fn, 3, a, "d3d/QI-IDirect3D8") == 0, "d3d/QI-IDirect3D8-S_OK");
    check(MEM32(d3d + 4) == refs + 1, "d3d/QI-IDirect3D8-adds-ref");
    a[0] = d3d;
    check(call(d3d8_methods[1].fn, 1, a, "d3d/AddRef") == MEM32(d3d + 4), "d3d/AddRef-returns-new-count");
    check(call(d3d8_methods[2].fn, 1, a, "d3d/Release") == MEM32(d3d + 4), "d3d/Release-returns-new-count");
    check(MEM32(d3d + 4) >= 2, "d3d/still-referenced");

    a[0] = d3d;
    check(call(d3d8_methods[4].fn, 1, a, "d3d/GetAdapterCount") == 1, "d3d/one-adapter");
    a[1] = 0;
    check(call(d3d8_methods[6].fn, 2, a, "d3d/GetAdapterModeCount") == 1, "d3d/one-mode");
    a[1] = 1;
    check(call(d3d8_methods[6].fn, 2, a, "d3d/bad-adapter") == 0, "d3d/bad-adapter-no-mode");
    a[1] = 0; a[2] = 0; a[3] = mode;
    check(call(d3d8_methods[7].fn, 4, a, "d3d/EnumAdapterModes") == 0, "d3d/EnumAdapterModes-S_OK");
    check(MEM32(mode) == 800 && MEM32(mode + 4) == 600 && MEM32(mode + 12) == 22,
          "d3d/mode-from-shared-display-path");
    a[3] = 0;
    check(call(d3d8_methods[7].fn, 4, a, "d3d/Enum-null-mode") == 0x80004003u, "d3d/Enum-E_POINTER");
    a[2] = 1; a[3] = mode;
    check(call(d3d8_methods[7].fn, 4, a, "d3d/Enum-bad-mode-index") == 0x8876086Cu,
          "d3d/Enum-INVALIDCALL");
    a[1] = 0; a[2] = mode;
    check(call(d3d8_methods[8].fn, 3, a, "d3d/GetAdapterDisplayMode") == 0, "d3d/GetAdapterDisplayMode-S_OK");
    check(MEM32(mode) == 800 && MEM32(mode + 4) == 600, "d3d/display-mode-shared");

    a[1] = 0; a[2] = 1; a[3] = 0; a[4] = 0; a[5] = pp; a[6] = devout;
    check(call(d3d8_methods[15].fn, 7, a, "d3d/CreateDevice") == 0, "d3d/CreateDevice-S_OK");
    dev = MEM32(devout);
    check(dev != 0 && MEM32(dev + 4) == 1, "device/initial-refcount-1");
    check(MEM32(MEM32(dev)) == 0xB0001000u, "device/vtable-is-IDirect3DDevice8");
    check(nvts == 2, "device/vtable-built-once");
    memcpy(hle_ptr(iid), iid_device8, 16);
    a[0] = dev; a[1] = iid; a[2] = out;
    check(call(d3d8_device_methods[0].fn, 3, a, "dev/QI-IDirect3DDevice8") == 0, "dev/QI-S_OK");
    check(MEM32(out) == dev && MEM32(dev + 4) == 2, "dev/QI-adds-ref");
    memcpy(hle_ptr(iid), iid_bogus, 16);
    check(call(d3d8_device_methods[0].fn, 3, a, "dev/QI-bogus") == 0x80004002u, "dev/QI-E_NOINTERFACE");
    check(MEM32(out) == 0, "dev/QI-bogus-clears-out");
    a[0] = dev;
    check(call(d3d8_device_methods[1].fn, 1, a, "dev/AddRef") == MEM32(dev + 4), "dev/AddRef-returns-new-count");
    check(call(d3d8_device_methods[2].fn, 1, a, "dev/Release") == MEM32(dev + 4), "dev/Release-returns-new-count");

    /* Every method not explicitly implemented must fail honestly with the
     * right stdcall pop, and must not change either object's refcount. */
    for (i = 0; i < d3d_methods_count; i++) {
        char msg[200];
        uint32_t before = MEM32(d3d + 4);
        if (implemented(d3d_methods_names[i], d3d_impl, 8)) continue;
        memset(a, 0, sizeof a); a[0] = d3d;
        snprintf(msg, sizeof msg, "d3d/unsupported/%s", d3d_methods_names[i]);
        check(call(d3d8_methods[i].fn, d3d_methods_counts[i], a, msg) == 0x8876086Au, msg);
        check(MEM32(d3d + 4) == before, "d3d/unsupported-refcount-unchanged");
    }
    for (i = 0; i < device_methods_count; i++) {
        char msg[200];
        uint32_t before = MEM32(dev + 4);
        if (implemented(device_methods_names[i], dev_impl, 3)) continue;
        memset(a, 0, sizeof a); a[0] = dev;
        snprintf(msg, sizeof msg, "dev/unsupported/%s", device_methods_names[i]);
        check(call(d3d8_device_methods[i].fn, device_methods_counts[i], a, msg) == 0x8876086Au, msg);
        check(MEM32(dev + 4) == before, "dev/unsupported-refcount-unchanged");
    }

    while (MEM32(dev + 4) > 0) call(d3d8_device_methods[2].fn, 1, a, "dev/final-release");
    check(frees == 1, "device/freed-at-zero-refs");
    a[0] = d3d;
    while (MEM32(d3d + 4) > 0) call(d3d8_methods[2].fn, 1, a, "d3d/final-release");
    check(frees == 2, "d3d/freed-at-zero-refs");
    printf(fails ? "RESULT FAIL %d\n" : "RESULT OK %d\n", fails);
    return fails != 0;
}
"""


class D3D8FrontendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SOURCE.read_text(encoding="utf-8")

    def test_source_is_a_frontend_only(self):
        # The issue forbids transplanting the browser/WebGPU runtime or game
        # engine code. None of those namespaces belongs in a guest shim.
        import re

        code = re.sub(r"/\*.*?\*/", "", self.text, flags=re.S)  # comments may name rejected designs
        text = code.upper()
        for forbidden in ("WEBGPU", "WGSL", "EMSCRIPTEN", "DOCUMENT.", "WINDOW.",
                          "WASM", "GAME_ENGINE", "IGI2DEC", "WGPU"):
            self.assertNotIn(forbidden, text, "foreign runtime reference: " + forbidden)
        self.assertIn("Direct3DCreate8", self.text)
        self.assertIn("win32hle_d3d8", self.text)
        self.assertIn("D3DERR_NOTAVAILABLE", self.text)

    def test_behaviour(self):
        compiler = os.environ.get("CC") or shutil.which("cc")
        if not compiler:
            self.skipTest("no C compiler available")
        with tempfile.TemporaryDirectory(prefix="d3d8-test-") as tmp:
            work = Path(tmp)
            shutil.copyfile(SOURCE, work / "d3d8.c")
            (work / "win32hle.h").write_text(FAKE_HEADER, encoding="utf-8")
            harness = HARNESS.replace("__TABLES__",
                                      c_table("d3d_methods", D3D_METHODS) +
                                      c_table("device_methods", DEVICE_METHODS))
            (work / "harness.c").write_text(harness, encoding="utf-8")
            exe = work / "harness"
            build = subprocess.run(
                [compiler, "-std=c11", "-O1", "-Wall", "-Wextra", "-Wno-unused-parameter",
                 "-I", str(work), str(work / "harness.c"), str(work / "d3d8.c"),
                 "-o", str(exe)],
                capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, "harness build failed:\n" + build.stdout + build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            output = run.stdout + run.stderr
            self.assertEqual(run.returncode, 0, "harness failed:\n" + output)
            self.assertIn("RESULT OK", output)

            def vt_lines(prefix):
                lines = [line.split(" ", 1)[1] for line in output.splitlines()
                         if line.startswith(prefix + " ")]
                # The harness records the fully qualified shim names.
                return [line.split("::", 1)[-1] for line in lines]

            self.assertEqual(vt_lines("D3D_VT"), [name for name, _ in D3D_METHODS])
            self.assertEqual(vt_lines("DEV_VT"), [name for name, _ in DEVICE_METHODS])
            for name, _ in D3D_METHODS[3:]:
                if name not in ("GetAdapterCount", "GetAdapterModeCount",
                                "EnumAdapterModes", "GetAdapterDisplayMode", "CreateDevice"):
                    self.assertIn("d3d/unsupported/%s" % name, output)
            for name, _ in DEVICE_METHODS[3:]:
                self.assertIn("dev/unsupported/%s" % name, output)


if __name__ == "__main__":
    unittest.main()
