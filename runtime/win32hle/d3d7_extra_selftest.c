/*
 * d3d7_extra_selftest.c - IDirect3DDevice7::PreLoad, driven through the same
 * guest ABI a title uses. There is no game binary and no private renderer:
 * the only rendering state touched is the SDL-owned OpenGL context the device
 * already created.
 *
 * PreLoad used to be one of the device's "not available" stubs even though
 * IGI calls it. The point of this file is that a successful PreLoad really
 * uploads the named surface's pixels into the shared GL texture (checked with
 * glGetTexImage, not inferred from the HRESULT), that it does not silently
 * succeed on a null or non-surface argument, and that it does not change the
 * texture currently bound to a stage.
 *
 * The device half needs a GL context; where SDL cannot give one the run exits
 * 77 (skipped) rather than claiming a pass.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

#define D3D_OK              0u
#define D3DERR_INVALIDCALL  0x8876086Cu

static const uint8_t IID_IDirectDraw7[16] = { 0xC0, 0x5E, 0xE6, 0x15, 0x1E, 0xA2, 0xD0, 0x11,
                                              0xB7, 0x44, 0x00, 0x60, 0x97, 0x97, 0xDB, 0xEA };
static const uint8_t IID_IDirect3D7[16]   = { 0x77, 0x9E, 0x04, 0xF5, 0x61, 0x48, 0xD2, 0x11,
                                              0xA4, 0x07, 0x00, 0xA0, 0xC9, 0x06, 0x29, 0xA8 };
static const uint8_t RGB_DEVICE_GUID[16]  = { 0x60, 0x5C, 0x66, 0xA4, 0x73, 0x26, 0xCF, 0x11,
                                              0xA3, 0x1A, 0x00, 0xAA, 0x00, 0xB9, 0x33, 0x56 };

/* GL constants used only by the selftest's verification, not by the shim. */
enum { GL_TEXTURE_2D = 0x0DE1, GL_TEXTURE_BINDING_2D = 0x8069,
       GL_RGBA = 0x1908, GL_UNSIGNED_BYTE = 0x1401 };
typedef void (*gl_get_int_fn)(unsigned, int *);
typedef void (*gl_get_tex_image_fn)(unsigned, int, unsigned, unsigned, void *);

static int fails;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }
static uint32_t call(const char *name, int n, const uint32_t *a) { return hle_call_guest(hle_resolve(name), n, a); }

/* Argument vectors must live in guest memory: hle_call_guest pushes them into
 * the guest, so a host stack array is fatal deep inside the runtime. */
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

/* A COM method call through the guest entry, the way a title's vtable does. */
static uint32_t vcall(uint32_t obj, const char *method, int n, const uint32_t *args) {
    uint32_t a[8] = { obj };
    for (int i = 0; i < n; i++) a[1 + i] = args[i];
    uint32_t va = hle_resolve(method);
    if (!va) { fprintf(stderr, "d3d7_extra_selftest: no shim named %s\n", method); fails++; return 0xFFFFFFFFu; }
    mach_enter();
    uint32_t stack = g_esp;
    uint32_t r = hle_call_guest(va, n + 1, a);
    mach_leave();
    if (g_esp != stack) {
        fprintf(stderr, "d3d7_extra_selftest: %s did not pop its arguments\n", method);
        fails++;
    }
    return r;
}
#define P(x) hle_va(x)

/* The DDSURFACEDESC2 fields this selftest fills, as 32-bit words. */
static void fill_desc(uint32_t *desc, int w, int h, int bpp, uint32_t caps, uint32_t pf_flags) {
    memset(desc, 0, 128 * sizeof(uint32_t));
    desc[1]  = 0x1u | 0x20u | 0x2000u | 0x2u | 0x1000u;   /* CAPS|WIDTH|PITCH|HEIGHT|PIXELFORMAT */
    desc[2]  = (uint32_t)h;
    desc[3]  = (uint32_t)w;
    desc[4]  = (uint32_t)w * (uint32_t)(bpp / 8);          /* lPitch */
    desc[26] = caps;                                        /* ddsCaps at byte 104 */
    desc[18] = 32;                                          /* ddPixelFormat size */
    desc[19] = pf_flags;                                    /* DDPF_RGB | DDPF_ALPHAPIXELS */
    desc[20] = 0;
    desc[21] = (uint32_t)bpp;
    desc[22] = 0x00FF0000u;
    desc[23] = 0x0000FF00u;
    desc[24] = 0x000000FFu;
    desc[25] = 0xFF000000u;
}

int main(void) {
    struct {
        uint8_t guid[16];
        uint8_t iid_dd7[16], iid_d3d7[16];
        uint32_t dd, d3d, device, primary, texture, tmp;
        uint32_t primary_desc[128], texture_desc[128];
        uint8_t dummy[16], texel[4];
    } *g = hle_alloc(sizeof *g);
    memcpy(g->iid_dd7, IID_IDirectDraw7, 16);
    memcpy(g->iid_d3d7, IID_IDirect3D7, 16);

    win32hle_register(win32hle_ddraw);
    mach_enter();
    g_vec = hle_alloc(sizeof *g_vec);

    ok("DirectDrawCreateEx(IDirectDraw7)",
       call("DirectDrawCreateEx", 4, A3(5, 0, P(&g->dd), P(g->iid_dd7))) == 0 && g->dd);
    ok("IDirectDraw7::QueryInterface(IID_IDirect3D7)",
       vcall(g->dd, "IDirectDraw7::QueryInterface", 2, A3(6, P(g->iid_d3d7), P(&g->d3d), 0)) == D3D_OK && g->d3d);

    /* A 4x4 32bpp texture surface with an alpha mask. D3D7 stores pixels as
     * B,G,R,A bytes for these masks, which GL uploads as GL_BGRA. */
    fill_desc(g->texture_desc, 4, 4, 32, 0x1000u, 0x40u | 0x01u);
    ok("CreateSurface(offscreen 4x4 32bpp texture)",
       vcall(g->dd, "IDirectDraw7::CreateSurface", 3, A3(4, P(g->texture_desc), P(&g->texture), 0)) == 0 && g->texture);
    if (!g->texture) {
        mach_leave();
        return fails != 0;
    }
    uint8_t *px = NULL; int pw = 0, ph = 0, pitch = 0, bpp = 0;
    ok("the texture surface exposes its pixels",
       hle_dd_surface_pixels(g->texture, &px, &pw, &ph, &pitch, &bpp) && px && pw == 4 && ph == 4 && bpp == 32);
    if (!px) {
        mach_leave();
        return fails != 0;
    }
    /* B=0, G=255, R=0, A=255 -> GL RGBA (0,255,0,255). */
    for (int y = 0; y < ph; y++)
        for (int x = 0; x < pw; x++)
            *(uint32_t *)(px + (size_t)y * (size_t)pitch + (size_t)x * 4u) = 0xFF00FF00u;

    memset(g->dummy, 0, sizeof g->dummy);

    /* A real GL context, as a title's device would have. The device's shim
     * table is built by CreateDevice, so without a context there is no device
     * entry to drive: this run is skipped, not passed. */
    if (!hle_screen_request_opengl() || hle_screen_open("d3d7 PreLoad selftest", 0, 0) != 0) {
        fprintf(stderr, "d3d7_extra_selftest: no OpenGL context; the device half needs one\n");
        mach_leave();
        return 77;
    }

    fill_desc(g->primary_desc, 16, 16, 32, 0x200u | 0x8u | 0x10u | 0x2000u, 0x40u);
    ok("CreateSurface(primary 16x16 32bpp, 3D device)",
       vcall(g->dd, "IDirectDraw7::CreateSurface", 3, A3(4, P(g->primary_desc), P(&g->primary), 0)) == 0 && g->primary);
    memcpy(g->guid, RGB_DEVICE_GUID, 16);
    ok("IDirect3D7::CreateDevice(RGB device)",
       vcall(g->d3d, "IDirect3D7::CreateDevice", 3, A3(7, P(g->guid), g->primary, P(&g->device))) == D3D_OK && g->device);
    if (!g->device) {
        mach_leave();
        printf("d3d7_extra_selftest: no device, so the upload checks did not run\n");
        return fails != 0;
    }

    /* Honest failures: no argument, and an object that is not a surface. */
    ok("PreLoad(NULL) is an invalid call, not a silent success",
       vcall(g->device, "IDirect3DDevice7::PreLoad", 1, A2(0, 0)) == D3DERR_INVALIDCALL);
    ok("PreLoad(non-surface object) is an invalid call",
       vcall(g->device, "IDirect3DDevice7::PreLoad", 1, A2(P(&g->dummy), 0)) == D3DERR_INVALIDCALL);

    /* PreLoad must not change which texture a stage has bound. */
    ok("GetTexture(stage 0) is empty before PreLoad",
       vcall(g->device, "IDirect3DDevice7::GetTexture", 2, A3(1, 0, P(&g->tmp), 0)) == D3D_OK && g->tmp == 0);

    ok("PreLoad(texture) succeeds",
       vcall(g->device, "IDirect3DDevice7::PreLoad", 1, A2(g->texture, 0)) == D3D_OK);
    ok("GetTexture(stage 0) is still empty after PreLoad",
       vcall(g->device, "IDirect3DDevice7::GetTexture", 2, A3(2, 0, P(&g->tmp), 0)) == D3D_OK && g->tmp == 0);

    /* The success is real: a GL texture is bound and holds the pixels that
     * were in the surface at the time of the call. */
    gl_get_int_fn gl_get_int = (gl_get_int_fn)SDL_GL_GetProcAddress("glGetIntegerv");
    gl_get_tex_image_fn gl_get_tex_image = (gl_get_tex_image_fn)SDL_GL_GetProcAddress("glGetTexImage");
    ok("glGetIntegerv is available for verification", gl_get_int != NULL);
    ok("glGetTexImage is available for verification", gl_get_tex_image != NULL);
    if (gl_get_int && gl_get_tex_image) {
        int binding = 0;
        gl_get_int(GL_TEXTURE_BINDING_2D, &binding);
        ok("PreLoad bound a real GL texture", binding != 0);
        memset(g->texel, 0, sizeof g->texel);
        gl_get_tex_image(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, g->texel);
        ok("PreLoad uploaded the surface's pixels into that texture",
           g->texel[0] == 0 && g->texel[1] == 255 && g->texel[2] == 0 && g->texel[3] == 255);
        if (g->texel[0] != 0 || g->texel[1] != 255 || g->texel[2] != 0 || g->texel[3] != 255)
            printf("  texel = %u,%u,%u,%u\n", g->texel[0], g->texel[1], g->texel[2], g->texel[3]);

        /* A modified surface must be uploaded again, not left stale. */
        for (int y = 0; y < ph; y++)
            for (int x = 0; x < pw; x++)
                *(uint32_t *)(px + (size_t)y * (size_t)pitch + (size_t)x * 4u) = 0xFFFF0000u;  /* B=0, G=0, R=255 -> red */
        ok("a second PreLoad after the surface changed succeeds",
           vcall(g->device, "IDirect3DDevice7::PreLoad", 1, A2(g->texture, 0)) == D3D_OK);
        memset(g->texel, 0, sizeof g->texel);
        gl_get_tex_image(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, g->texel);
        ok("the second PreLoad refreshed the GL texture",
           g->texel[0] == 255 && g->texel[1] == 0 && g->texel[2] == 0 && g->texel[3] == 255);
        if (g->texel[0] != 255 || g->texel[1] != 0 || g->texel[2] != 0 || g->texel[3] != 255)
            printf("  texel after second = %u,%u,%u,%u\n", g->texel[0], g->texel[1], g->texel[2], g->texel[3]);
    }

    ok("IDirect3DDevice7::Release", vcall(g->device, "IDirect3DDevice7::Release", 0, NULL) == 0);
    mach_leave();
    if (fails == 0) printf("d3d7_extra_selftest: all checks passed\n");
    return fails != 0;
}
