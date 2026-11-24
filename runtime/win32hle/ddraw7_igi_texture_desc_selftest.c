/*
 * ddraw7_igi_texture_desc_selftest.c - regression for the 124-byte
 * DDSURFACEDESC2 pixel-format offset that made retail IGI abort with
 * "Invalid texture format".
 *
 * IGI's texture-upload routine (guest 0x0050B4F0) calls
 * IDirectDrawSurface::GetSurfaceDesc with a 124-byte descriptor and then
 * reads the pixel format at +72: dwFlags (+76) must have DDPF_RGB (0x40)
 * and dwRGBBitCount (+84) must be 16 or 32 (checks at guest 0x50B6E8 and
 * 0x50B6FE; the error branch at 0x50BB62 pushes the string at 0x54EAD8).
 * DDSURFACEDESC and DDSURFACEDESC2 share those offsets (mingw-w64 ddraw.h).
 *
 * A previous desc_shape() treated every dwSize >= 124 as a four-byte-shifted
 * variant and wrote the pixel format at +76, so IGI read dwSize (32) as
 * dwFlags and 0 as the bit count. This test drives the real ddraw.c shims:
 * create a 16bpp surface through a standard 124-byte descriptor, read its
 * desc back, and check the exact words IGI checks. It also checks that the
 * nonstandard 128-byte shape (wf.exe) keeps working.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

#define DD_OK                   0u
#define DDSCAPS_OFFSCREENPLAIN  0x00000040u
#define DDSD_CAPS               0x00000001u
#define DDSD_HEIGHT             0x00000002u
#define DDSD_WIDTH              0x00000004u
#define DDSD_PITCH              0x00000008u
#define DDSD_PIXELFORMAT        0x00001000u
#define DDPF_RGB                0x00000040u

static const uint8_t IID_IDirectDraw7[16] = {
    0xC0, 0x5E, 0xE6, 0x15, 0x1E, 0xA2, 0xD0, 0x11,
    0xB9, 0x2F, 0x00, 0x60, 0x97, 0x97, 0xEA, 0x5B
};

static int fails;
static void ok(const char *what, int cond) {
    printf("%s %s\n", cond ? "ok  " : "FAIL", what);
    if (!cond) fails++;
}

typedef struct {
    uint32_t vec[8][4];
    uint8_t iid[16];
    uint32_t iid_va, out, dd7, surf, surf128;
    uint32_t desc124[124 / 4], got124[124 / 4];
    uint32_t desc128[128 / 4];
    uint32_t rc_create, rc_mode, rc_surf, rc_desc, rc_surf128;
    uint8_t *px; int w, h, pitch, bpp;
} fixture_t;
static fixture_t *f;

static uint32_t call(const char *name, int argc, const uint32_t *a) {
    return hle_call_guest(hle_resolve(name), argc, a);
}
static uint32_t vcall(uint32_t obj, int slot, int argc, uint32_t *a) {
    uint32_t va = MEM32(MEM32(obj) + 4u * (uint32_t)slot);
    if (!va) { fprintf(stderr, "FAIL empty vtable slot %d\n", slot); fails++; return 0xFFFFFFFFu; }
    uint32_t *v = f->vec[7];
    v[0] = obj;
    for (int i = 1; i < argc; i++) v[i] = a[i - 1];
    return hle_call_guest(va, argc, v);
}
static uint32_t mcall(const char *name, uint32_t obj, int argc, uint32_t *a) {
    uint32_t *v = f->vec[6];
    v[0] = obj;
    for (int i = 1; i < argc; i++) v[i] = a[i - 1];
    return hle_call_guest(hle_resolve(name), argc, v);
}

/* Standard 124-byte DDSURFACEDESC2: pixel format at byte 72 (word 18). */
static void fill_desc124(uint32_t *p, int w, int h, int bpp) {
    memset(p, 0, 124);
    p[0] = 124;
    p[1] = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT;
    p[2] = (uint32_t)h; p[3] = (uint32_t)w; p[4] = (uint32_t)(w * bpp / 8);
    p[18] = 32; p[19] = DDPF_RGB; p[20] = 0; p[21] = (uint32_t)bpp;
    p[22] = 0xF800u; p[23] = 0x07E0u; p[24] = 0x001Fu; p[25] = 0;
    p[26] = DDSCAPS_OFFSCREENPLAIN;
}

int main(void) {
    recomp_host_init();
    win32hle_register(win32hle_ddraw);
    ok("DirectDrawCreateEx resolves by name", hle_resolve("DirectDrawCreateEx") != 0);
    mach_enter();

    f = (fixture_t *)hle_alloc(sizeof *f);
    memset(f, 0, sizeof *f);
    memcpy(f->iid, IID_IDirectDraw7, 16);
    f->iid_va = hle_va(f->iid);

    f->out = 0;
    {
        uint32_t a[4] = { 0, hle_va(&f->out), f->iid_va, 0 };
        f->rc_create = call("DirectDrawCreateEx", 4, a);
    }
    ok("DirectDrawCreateEx(IID_IDirectDraw7) -> DD_OK", f->rc_create == DD_OK && f->out);
    if (!f->out) { mach_leave(); return 1; }
    f->dd7 = f->out;
    {
        uint32_t a[3] = { 640, 480, 16 };
        f->rc_mode = vcall(f->dd7, 21, 4, a);
    }
    ok("SetDisplayMode(640x480x16) -> DD_OK", f->rc_mode == DD_OK);
    ok("IDirectDrawSurface::GetSurfaceDesc resolves by name",
       hle_resolve("IDirectDrawSurface::GetSurfaceDesc") != 0);

    /* ---- CreateSurface must read the bit count from +84, not +88 ---- */
    fill_desc124(f->desc124, 64, 32, 16);
    f->out = 0;
    {
        uint32_t a[3] = { hle_va(f->desc124), hle_va(&f->out), 0 };
        f->rc_surf = vcall(f->dd7, 6, 4, a);
    }
    ok("CreateSurface(124-byte DDSURFACEDESC2, 16bpp) -> DD_OK", f->rc_surf == DD_OK && f->out);
    f->surf = f->out;
    if (!f->surf) { mach_leave(); return 1; }
    ok("the surface was created with bpp 16 (not the +88 red mask)",
       hle_dd_surface_pixels(f->surf, &f->px, &f->w, &f->h, &f->pitch, &f->bpp) &&
       f->bpp == 16 && f->w == 64 && f->h == 32);

    /* ---- GetSurfaceDesc must write the exact words IGI checks ---- */
    memset(f->got124, 0, sizeof f->got124);
    MEM32(hle_va(f->got124)) = 124;             /* IGI passes dwSize 0x7C */
    {
        uint32_t a[2] = { hle_va(f->got124), 0 };
        f->rc_desc = mcall("IDirectDrawSurface::GetSurfaceDesc", f->surf, 3, a);
    }
    ok("GetSurfaceDesc(124-byte desc) -> DD_OK", f->rc_desc == DD_OK);
    ok("descriptor size is still 124", MEM32(hle_va(f->got124)) == 124);
    ok("ddpfPixelFormat.dwSize at +72 is 32", MEM32(hle_va(f->got124) + 72) == 32);
    ok("ddpfPixelFormat.dwFlags at +76 has DDPF_RGB (0x40) - IGI 0x50B6E8",
       (MEM32(hle_va(f->got124) + 76) & DDPF_RGB) != 0);
    ok("dwRGBBitCount at +84 is 16 - IGI 0x50B6FE",
       MEM32(hle_va(f->got124) + 84) == 16);
    ok("red mask at +88 is RGB565's 0xF800", MEM32(hle_va(f->got124) + 88) == 0xF800u);
    ok("the shifted slot at +80 is not the DDPF word",
       MEM32(hle_va(f->got124) + 80) != 32);

    /* ---- the nonstandard 128-byte shape (wf.exe) must keep working ---- */
    memset(f->desc128, 0, sizeof f->desc128);
    f->desc128[0] = 128;
    f->desc128[1] = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT;
    f->desc128[2] = 8; f->desc128[3] = 8;
    f->desc128[19] = 32; f->desc128[20] = DDPF_RGB; f->desc128[22] = 16;
    f->desc128[27] = DDSCAPS_OFFSCREENPLAIN;
    f->out = 0;
    {
        uint32_t a[3] = { hle_va(f->desc128), hle_va(&f->out), 0 };
        f->rc_surf128 = vcall(f->dd7, 6, 4, a);
    }
    ok("CreateSurface(nonstandard 128-byte desc) -> DD_OK", f->rc_surf128 == DD_OK && f->out);
    f->surf128 = f->out;
    if (f->surf128) {
        uint8_t *p; int w, h, pitch, bpp;
        ok("the 128-byte shape still yields a 16bpp surface",
           hle_dd_surface_pixels(f->surf128, &p, &w, &h, &pitch, &bpp) && bpp == 16);
    }

    /* Balance the references we took; the global DD7 object lives on. */
    {
        uint32_t one[1] = { 0 };
        if (f->surf128) mcall("IDirectDrawSurface::Release", f->surf128, 1, one);
        mcall("IDirectDrawSurface::Release", f->surf, 1, one);
        vcall(f->dd7, 2, 1, one);
    }

    mach_leave();
    printf("ddraw7_igi_texture_desc_selftest: %s\n", fails ? "FAIL" : "all checks passed");
    return fails != 0;
}
