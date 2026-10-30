/*
 * d3d7_selftest.c - the Direct3D 7 COM facade, driven through the same guest
 * ABI a title uses: DirectDrawCreateEx(IDirectDraw7), QueryInterface(
 * IID_IDirect3D7), CreateDevice, and then the device's own vtable. No game
 * binary, no window of its own.
 *
 * The point is the contract, not the pixels: which calls succeed, which are
 * refused with which HRESULT, whether a refused call changes anything, and
 * whether COM reference counts balance. A call that returns
 * D3DERR_NOTAVAILABLE must leave GetRenderState/GetTextureStageState
 * reporting the previous value - a "success" that quietly ignores the request
 * is the failure mode this file exists to catch.
 *
 * The facade half needs no OpenGL and runs by default. The device half needs a
 * real GL context, so it is opt-in (D3D7_SELFTEST_DEVICE=1) on a GL-capable
 * SDL video driver; see runtime/win32hle/README.md.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

#define D3D_OK              0u
#define E_POINTER           0x80004003u
#define D3DERR_NOTAVAILABLE 0x8876086Au
#define D3DERR_INVALIDCALL  0x8876086Cu

/* DirectDraw 7 / Direct3D 7 interface identities. */
static const uint8_t IID_IDirectDraw7[16] = { 0xC0, 0x5E, 0xE6, 0x15, 0x1E, 0xA2, 0xD0, 0x11,
                                              0xB7, 0x44, 0x00, 0x60, 0x97, 0x97, 0xDB, 0xEA };
static const uint8_t IID_IDirect3D7[16]   = { 0x78, 0x9E, 0x04, 0xF5, 0x61, 0x48, 0xD2, 0x11,
                                              0xA4, 0x07, 0x00, 0xA0, 0xC9, 0x06, 0x29, 0xA8 };
static const uint8_t IID_IUnknown[16]     = { 0, 0, 0, 0, 0, 0, 0, 0,
                                              0xC0, 0, 0, 0, 0, 0, 0, 0x46 };

/* The RGB device GUID the backend advertises in D3DDEVICEDESC7.deviceGUID. */
static const uint8_t RGB_DEVICE_GUID[16]  = { 0x60, 0x5C, 0x66, 0xA4, 0x73, 0x26, 0xCF, 0x11,
                                              0xA3, 0x1A, 0x00, 0xAA, 0x00, 0xB9, 0x33, 0x56 };

static int fails;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }

static uint32_t call(const char *name, int n, const uint32_t *a) { return hle_call_guest(hle_resolve(name), n, a); }

/* Argument vectors live in GUEST memory (g_vec), never on the host stack:
 * hle_call_guest pushes them into the guest, so a host array is fatal. Both
 * are allocated after mach_enter. A2 fills a 2-argument vector, A3 a
 * 3-argument one; each slot is reused by call site, so they are zeroed. */
static struct { uint32_t args[8][4]; } *g_vec;
static uint32_t *A2(uint32_t a, uint32_t b) {
    memset(g_vec->args[0], 0, sizeof g_vec->args[0]);
    g_vec->args[0][0] = a; g_vec->args[0][1] = b;
    return g_vec->args[0];
}
static uint32_t *A3(int i, uint32_t a, uint32_t b, uint32_t c) {
    memset(g_vec->args[i], 0, sizeof g_vec->args[i]);
    g_vec->args[i][0] = a; g_vec->args[i][1] = b; g_vec->args[i][2] = c;
    return g_vec->args[i];
}

/* A COM method call. In a running game the object's vtable slot holds the
 * shim's guest VA, which hle_call_guest takes directly. A selftest links the
 * shim table with C functions instead, so resolve the name and drive the
 * shim through the guest entry the same way, rather than a host pointer. */
static uint32_t vcall(uint32_t obj, const char *method, int n, const uint32_t *args) {
    /* The caller's argument vector must be guest memory: hle_call_guest pushes
     * it into the guest, and a host stack array is a fatal error deep inside
     * the runtime. Catch it here, where the method name is known. */
#ifdef HLE_FLAT
    (void)0;                                     /* flat: every pointer is a VA */
#else
    if (args && ((uintptr_t)args - (uintptr_t)g_mem_base) >> 32) {   /* NULL = no arguments */
        fprintf(stderr, "d3d7_selftest: %s was handed a host-stack argument vector\n", method);
        fails++;
        return 0xFFFFFFFFu;
    }
#endif
    uint32_t a[8] = { obj };
    for (int i = 0; i < n; i++) a[1 + i] = args[i];
    uint32_t va = hle_resolve(method);
    if (!va) { fprintf(stderr, "d3d7_selftest: no shim named %s\n", method); fails++; return 0xFFFFFFFFu; }
    mach_enter();
    uint32_t stack = g_esp;
    uint32_t r = hle_call_guest(va, n + 1, a);
    mach_leave();
    if (g_esp != stack) {
        fprintf(stderr, "d3d7_selftest: %s did not pop its return address and %d argument(s)\n", method, n + 1);
        fails++;
    }
    return r;
}
static uint32_t refs(uint32_t obj) { return MEM32(obj + 4u); }

/* Render states and texture-stage states used below (d3d7types.h values). */
enum { RS_SRCBLEND = 19, RS_CULLMODE = 22, RS_FOGTABLEMODE = 35,
       RS_DIFFUSEMATERIALSOURCE = 145, RS_SPECULARMATERIALSOURCE = 146 };
enum { TSS_COLOROP = 1, TSS_COLORARG1 = 2, TSS_COLORARG2 = 3 };
enum { D3DCULL_CW = 2, D3DCULL_CCW = 3 };
enum { D3DTA_TEXTURE = 2, D3DTA_COMPLEMENT = 0x10, D3DTA_ALPHAREPLICATE = 0x20 };

/* Argument vectors live in guest memory: see A2/A3 above. */
#define P(x) hle_va(x)                                /* everything handed over is guest memory */

int main(void) {
    /* First: guest memory. On a 64-bit host every pointer the guest sees is
     * an offset into the block, so a stack struct is allocated here and only
     * ever addressed through P(). */
    struct {
        uint8_t guid[16];
        uint32_t dd, d3d, d3d2, device, other, tmp, count, passes, primary;
        uint8_t desc[236];
        float m[16], back[16];
        uint32_t viewport[6];
        float plane[4];
        uint8_t light[104];
        uint32_t iid_d3d7[4], iid_unknown[4], iid_dd7[4];
    } *g = hle_alloc(sizeof *g);
    memcpy(g->iid_d3d7, IID_IDirect3D7, 16);
    memcpy(g->iid_unknown, IID_IUnknown, 16);
    memcpy(g->iid_dd7, IID_IDirectDraw7, 16);

    win32hle_register(win32hle_ddraw);
    mach_enter();
    g_vec = hle_alloc(sizeof *g_vec);                /* argument vectors (needs the machine) */

    /* The device half needs the window's GL context, as a title's would. It
     * runs by default; D3D7_SELFTEST_FACADE_ONLY=1 stops after the facade
     * contract, and where SDL cannot give a GL context the run is skipped
     * (exit 77) rather than failed. */
    int device_half = getenv("D3D7_SELFTEST_FACADE_ONLY") == NULL;
    if (device_half && (!hle_screen_request_opengl() || hle_screen_open("d3d7 selftest", 0, 0) != 0)) {
        fprintf(stderr, "d3d7_selftest: no OpenGL context; the device half needs one\n");
        mach_leave();
        return 77;                                   /* skipped, not passed */
    }

    /* ---- IDirectDraw7 -> IDirect3D7: one object, thanks to a reference ---- */
    uint32_t r0 = call("DirectDrawCreateEx", 4, A3(5, 0, P(&g->dd), P(g->iid_dd7)));
    ok("DirectDrawCreateEx(IDirectDraw7)", r0 == 0 && g->dd && refs(g->dd) == 1);

    uint32_t qi_ret = vcall(g->dd, "IDirectDraw7::QueryInterface", 2, A3(6, P(g->iid_d3d7), P(&g->d3d), 0));
    ok("IDirectDraw7::QueryInterface(IID_IDirect3D7)",
       qi_ret == 0 && g->d3d && refs(g->d3d) >= 1);

    uint32_t before2 = refs(g->d3d);
    uint32_t r2 = vcall(g->dd, "IDirectDraw7::QueryInterface", 2, A3(6, P(g->iid_d3d7), P(&g->d3d2), 0));
    ok("a second QueryInterface(IID_IDirect3D7) returns the same object and a new reference",
       r2 == 0 && g->d3d2 == g->d3d && refs(g->d3d) == before2 + 1);
    uint32_t before3 = refs(g->d3d);
    uint32_t r3 = vcall(g->d3d, "IDirect3D7::QueryInterface", 2, A3(6, P(g->iid_unknown), P(&g->other), 0));
    ok("IDirect3D7::QueryInterface(IID_IUnknown) is the same interface, plus one reference",
       r3 == D3D_OK && g->other == g->d3d && refs(g->d3d) == before3 + 1);
    uint32_t before4 = refs(g->d3d);
    uint32_t r4 = vcall(g->d3d, "IDirect3D7::AddRef", 0, NULL);
    ok("IDirect3D7::AddRef", r4 == before4 + 1 && refs(g->d3d) == before4 + 1);
    uint32_t r5 = vcall(g->d3d, "IDirect3D7::Release", 0, NULL);
    ok("IDirect3D7::Release", r5 == before4 && refs(g->d3d) == before4);

    /* ---- CreateDevice input validation ---- */
    memcpy(g->guid, RGB_DEVICE_GUID, 16);
    ok("CreateDevice(NULL target surface) is refused",
       vcall(g->d3d, "IDirect3D7::CreateDevice", 3, A3(7, P(g->guid), 0, P(&g->device))) == D3DERR_INVALIDCALL &&
       g->device == 0);
    ok("CreateDevice(NULL device GUID) is a pointer error",
       vcall(g->d3d, "IDirect3D7::CreateDevice", 3, A3(7, 0, 0, P(&g->device))) == E_POINTER);
    memset(g->guid, 0, 16); g->guid[0] = 0x11;
    ok("CreateDevice(GUID nobody advertised) is refused",
       vcall(g->d3d, "IDirect3D7::CreateDevice", 3, A3(7, P(g->guid), 0, P(&g->device))) == D3DERR_NOTAVAILABLE &&
       g->device == 0);

    /* ---- A real device over the window's GL context ---- */
    if (!device_half) {
        mach_leave();
        if (fails == 0) printf("d3d7_selftest: facade checks passed (D3D7_SELFTEST_FACADE_ONLY)\n");
        return fails != 0;
    }

    /* A primary surface is what CreateDevice renders into. */
    uint32_t *desc = hle_alloc(128 * 4u);             /* DDSURFACEDESC2, guest memory (the heap's header sits before it) */
    desc[1]  = 0x1u | 0x20u | 0x2000u | 0x2u | 0x1000u;  /* CAPS|WIDTH|PITCH|HEIGHT|PIXELFORMAT */
    desc[2]  = 8; desc[3] = 8;
    desc[26] = 0x200u | 0x8u | 0x10u | 0x2000u;   /* PRIMARYSURFACE | COMPLEX | FLIP | 3DDEVICE, at byte 104 */
    static const uint32_t pf_dd[8] = { 32, 0x40u /*DDPF_RGB*/, 0, 24, 0xFF0000u, 0xFF00u, 0xFFu, 0 };
    memcpy(&desc[18], pf_dd, sizeof pf_dd);       /* ddPixelFormat, at byte 72 */
    uint32_t rc = vcall(g->dd, "IDirectDraw7::CreateSurface", 3, A3(4, P(desc), P(&g->primary), 0));
    ok("IDirectDraw7::CreateSurface(primary 8x8 32bpp)", rc == 0 && g->primary);

    memcpy(g->guid, RGB_DEVICE_GUID, 16);
    uint32_t rmk = vcall(g->d3d, "IDirect3D7::CreateDevice", 3, A3(7, P(g->guid), g->primary, P(&g->device)));
    ok("IDirect3D7::CreateDevice(RGB device) over the primary surface", rmk == D3D_OK && g->device);
    if (!g->device) {
        mach_leave();
        printf("d3d7_selftest: facade checks passed; %d failed; no device, so the device half did not run\n", fails);
        return fails != 0;
    }

    /* ---- The device's contract: accepted, refused, and never faked ---- */
    g->viewport[0] = 0; g->viewport[1] = 0;
    g->viewport[2] = 8; g->viewport[3] = 8;           /* the surface is 8x8 (a primary takes the display size) */
    g->viewport[4] = 0; g->viewport[5] = 0x3F800000u; /* minZ 0, maxZ 1 */
    ok("SetViewport", vcall(g->device, "IDirect3DDevice7::SetViewport", 1, A2(P(g->viewport), 0)) == D3D_OK);
    for (int i = 0; i < 16; i++) g->m[i] = (float)(i + 1) / 16.f;
    ok("SetTransform(WORLD)",
       vcall(g->device, "IDirect3DDevice7::SetTransform", 3, A3(0, 2u /* D3DTS_WORLD */, P(g->m), 1)) == D3D_OK);
    ok("GetTransform(WORLD) round-trips the matrix",
       vcall(g->device, "IDirect3DDevice7::GetTransform", 3, A3(1, 2u, P(g->back), 1)) == D3D_OK &&
       !memcmp(g->back, g->m, sizeof g->m));

    /* Render state: what is accepted reads back, what is refused changes nothing. */
    ok("GetRenderState(CULLMODE) default is D3DCULL_CCW",
       vcall(g->device, "IDirect3DDevice7::GetRenderState", 2, A2(RS_CULLMODE, P(&g->tmp))) == D3D_OK &&
       g->tmp == D3DCULL_CCW);
    ok("SetRenderState(CULLMODE, CW)",
       vcall(g->device, "IDirect3DDevice7::SetRenderState", 2, A2(RS_CULLMODE, D3DCULL_CW)) == D3D_OK);
    ok("SetRenderState(CULLMODE, undefined value) is an invalid call",
       vcall(g->device, "IDirect3DDevice7::SetRenderState", 2, A2(RS_CULLMODE, 0x70u)) == D3DERR_INVALIDCALL);
    ok("a refused render state leaves the previous value",
       vcall(g->device, "IDirect3DDevice7::GetRenderState", 2, A2(RS_CULLMODE, P(&g->tmp))) == D3D_OK &&
       g->tmp == D3DCULL_CW);
    ok("SetRenderState(unknown state) is refused, not silently ignored",
       vcall(g->device, "IDirect3DDevice7::SetRenderState", 2, A2(210u, 1u)) == D3DERR_NOTAVAILABLE);
    ok("GetRenderState(unknown state) is refused",
       vcall(g->device, "IDirect3DDevice7::GetRenderState", 2, A2(210u, P(&g->tmp))) == D3DERR_NOTAVAILABLE);
    ok("SetRenderState(FOGTABLEMODE, undefined mode) is refused",
       vcall(g->device, "IDirect3DDevice7::SetRenderState", 2, A2(RS_FOGTABLEMODE, 9u)) == D3DERR_INVALIDCALL);
    ok("SetRenderState(SRCBLEND, undefined factor) is refused",
       vcall(g->device, "IDirect3DDevice7::SetRenderState", 2, A2(RS_SRCBLEND, 0x5Du)) == D3DERR_INVALIDCALL);

    /* A material source the fixed-function GL path cannot represent is
     * refused, and the state it would have changed is untouched. */
    g->tmp = 0xFFFFFFFFu;
    vcall(g->device, "IDirect3DDevice7::GetRenderState", 2, A2(RS_SPECULARMATERIALSOURCE, P(&g->tmp)));
    uint32_t before_spec = g->tmp;
    uint32_t x = vcall(g->device, "IDirect3DDevice7::SetRenderState", 2,
                       A2(RS_SPECULARMATERIALSOURCE, 0u /* D3DMCS_MATERIAL */));
    g->tmp = 0xFFFFFFFEu;
    vcall(g->device, "IDirect3DDevice7::GetRenderState", 2, A2(RS_SPECULARMATERIALSOURCE, P(&g->tmp)));
    ok("SetRenderState(SPECULARMATERIALSOURCE, material) is refused, not faked",
       x == D3DERR_NOTAVAILABLE && g->tmp == before_spec);
    /* Diffuse from the material is the same task as from colour 1 here. */
    ok("SetRenderState(DIFFUSEMATERIALSOURCE, material)",
       vcall(g->device, "IDirect3DDevice7::SetRenderState", 2, A2(RS_DIFFUSEMATERIALSOURCE, 0u)) == D3D_OK);

    /* One texture stage is advertised: stage 1 is refused, not ignored. */
    ok("GetTextureStageState(0, COLOROP) default is D3DTOP_MODULATE",
       vcall(g->device, "IDirect3DDevice7::GetTextureStageState", 3, A3(0, 0, TSS_COLOROP, P(&g->tmp))) == D3D_OK &&
       g->tmp == 4);
    ok("SetTextureStageState(stage 1) is refused",
       vcall(g->device, "IDirect3DDevice7::SetTextureStageState", 3, A3(1, 1, TSS_COLOROP, 1u)) == D3DERR_NOTAVAILABLE);
    ok("SetTextureStageState(0, COLOROP, SELECTARG1)",
       vcall(g->device, "IDirect3DDevice7::SetTextureStageState", 3, A3(2, 0, TSS_COLOROP, 2u)) == D3D_OK);
    ok("SetTextureStageState(0, COLOROP, ADDSIGNED) is refused",
       vcall(g->device, "IDirect3DDevice7::SetTextureStageState", 3, A3(2, 0, TSS_COLOROP, 5u)) == D3DERR_NOTAVAILABLE);
    ok("SetTextureStageState(0, COLORARG1, undefined argument) is refused",
       vcall(g->device, "IDirect3DDevice7::SetTextureStageState", 3, A3(3, 0, TSS_COLORARG1, 9u)) == D3DERR_NOTAVAILABLE);
    const uint32_t mod = D3DTA_TEXTURE | D3DTA_COMPLEMENT | D3DTA_ALPHAREPLICATE;
    ok("SetTextureStageState(0, COLORARG2, TEXTURE|COMPLEMENT|ALPHAREPLICATE)",
       vcall(g->device, "IDirect3DDevice7::SetTextureStageState", 3, A3(4, 0, TSS_COLORARG2, mod)) == D3D_OK);
    ok("an accepted texture stage state reads back",
       vcall(g->device, "IDirect3DDevice7::GetTextureStageState", 3, A3(5, 0, TSS_COLORARG2, P(&g->tmp))) == D3D_OK &&
       g->tmp == mod);

    /* ValidateDevice must agree with what the setters accepted. */
    g->count = 0xFFFFFFFFu;
    ok("ValidateDevice passes the states it accepted",
       vcall(g->device, "IDirect3DDevice7::ValidateDevice", 1, A2(P(&g->count), 0)) == D3D_OK && g->count != 0);

    /* Scene pairing. */
    ok("EndScene without BeginScene is refused",
       vcall(g->device, "IDirect3DDevice7::EndScene", 0, NULL) == D3DERR_INVALIDCALL);
    ok("BeginScene", vcall(g->device, "IDirect3DDevice7::BeginScene", 0, NULL) == D3D_OK);
    ok("BeginScene twice is refused",
       vcall(g->device, "IDirect3DDevice7::BeginScene", 0, NULL) == D3DERR_INVALIDCALL);
    ok("EndScene", vcall(g->device, "IDirect3DDevice7::EndScene", 0, NULL) == D3D_OK);

    /* Pointer errors and bounds. */
    ok("GetMaterial(NULL) is a pointer error",
       vcall(g->device, "IDirect3DDevice7::GetMaterial", 1, A2(0, 0)) == E_POINTER);
    ok("SetLight(8) is refused",
       vcall(g->device, "IDirect3DDevice7::SetLight", 2, A2(8, P(g->light))) == D3DERR_INVALIDCALL);
    ok("SetClipPlane(6) is refused",
       vcall(g->device, "IDirect3DDevice7::SetClipPlane", 2, A2(6, P(g->plane))) == D3DERR_INVALIDCALL);

    /* A method this backend does not implement says so, rather than claiming success. */
    ok("CreateStateBlock is refused, not silently successful",
       vcall(g->device, "IDirect3DDevice7::CreateStateBlock", 2, A2(1u, P(&g->tmp))) == D3DERR_NOTAVAILABLE);

    /* GetCaps describes this backend: one software device, no hardware claims. */
    ok("GetCaps", vcall(g->device, "IDirect3DDevice7::GetCaps", 1, A2(P(g->desc), 0)) == D3D_OK);
    /* dwFlags: the Direct3D-7 "this is a software device" family. */
    ok("caps: describes a software device", (MEM32(P(g->desc)) & 0x400u) != 0 || (MEM32(P(g->desc)) & 0x800u) != 0);
    ok("caps: deviceGUID is the advertised RGB device",
       !memcmp(hle_ptr(P(&g->desc[196])), RGB_DEVICE_GUID, 16));

    /* ---- Release ---- */
    ok("IDirect3DDevice7::Release", vcall(g->device, "IDirect3DDevice7::Release", 0, NULL) == 0);
    mach_leave();
    if (fails == 0) printf("d3d7_selftest: all checks passed\n");
    return fails != 0;
}


