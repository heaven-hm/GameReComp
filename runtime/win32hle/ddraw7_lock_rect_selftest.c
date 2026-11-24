/*
 * ddraw7_lock_rect_selftest.c - IDirectDrawSurface::Lock with a non-NULL
 * lpDestRect, driven through the real ddraw.c guest ABI.
 *
 * ddraw.c's put_desc() knows the two descriptor shapes: for DDSURFACEDESC2
 * (dwSize >= 124) lpSurface lives at +40, while the legacy DDSURFACEDESC puts
 * it at +36. sf_Lock() then adjusted lpSurface to the locked rectangle, but
 * always wrote that adjustment to +36. A DirectDraw7 caller that locks a
 * sub-rectangle therefore read back the *whole surface* pointer from +40 and
 * drew through it at the wrong address; its +36 colour-key slot was clobbered
 * with a pointer at the same time. Legacy callers were unaffected, which is
 * why the existing whole-surface Lock test never saw it.
 *
 * This test locks a sub-rect of a primary surface with a 124-byte descriptor
 * and of an offscreen surface with a 108-byte descriptor, checks the exact
 * pointer returned in each shape, writes a pixel through it and reads it back
 * through the existing shared frame publisher. No new renderer or display is
 * involved: the dummy driver supplies the screen the HLE already uses.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

/* No lifted callbacks are needed: every call goes through hle_call_guest into
 * the registered shims, the same path a game's compiled code takes. */
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

#define DD_OK                   0u
#define DDERR_INVALIDPARAMS     0x80070057u
#define DDSCAPS_PRIMARYSURFACE  0x00000200u
#define DDSCAPS_SYSTEMMEMORY    0x00000800u
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

/* All guest-visible storage, argument vectors included, lives in the guest
 * heap: hle_call_guest pushes from guest addresses, never the host stack. */
typedef struct {
    uint32_t vec[8][4];
    uint8_t iid[16];
    uint32_t iid_va, out, dd7, primary, offscreen;
    uint32_t desc2[128], desc1[64];
    uint32_t lock2[128], lock1[64];
    uint32_t rect2[4], rect1[4];
    uint32_t rc_create, rc_mode, rc_create_primary, rc_create_offscreen;
    uint32_t rc_lock2, rc_unlock2, rc_lock1, rc_unlock1;
    uint32_t base, expected2, got2, got1, expected1;
    int pitch;
    const uint8_t *px;
} fixture_t;
static fixture_t *f;

static uint32_t call(const char *name, int argc, const uint32_t *a) {
    return hle_call_guest(hle_resolve(name), argc, a);
}
/* Invoke a COM method by its vtable slot (`this` is argument 0). */
static uint32_t vcall(uint32_t obj, int slot, int argc, uint32_t *a) {
    uint32_t va = MEM32(MEM32(obj) + 4u * (uint32_t)slot);
    if (!va) { fprintf(stderr, "FAIL empty vtable slot %d\n", slot); fails++; return 0xFFFFFFFFu; }
    uint32_t *v = f->vec[7];
    v[0] = obj;
    for (int i = 1; i < argc; i++) v[i] = a[i - 1];
    return hle_call_guest(va, argc, v);
}
/* Invoke a named surface shim; the vtable slots are stable but verbose. */
static uint32_t mcall(const char *name, uint32_t obj, int argc, uint32_t *a) {
    uint32_t *v = f->vec[6];
    v[0] = obj;
    for (int i = 1; i < argc; i++) v[i] = a[i - 1];
    return hle_call_guest(hle_resolve(name), argc, v);
}

/* DDSURFACEDESC2 word layout used throughout ddraw.c: pf at 76 (word 19),
 * ddsCaps at 108 (word 27), lpSurface at 40 (word 10). */
static void fill_desc2(uint32_t *p, int w, int h, int bpp, uint32_t caps) {
    memset(p, 0, 128 * sizeof *p);
    p[0] = 124;
    p[1] = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT;
    p[2] = (uint32_t)h; p[3] = (uint32_t)w; p[4] = (uint32_t)(w * bpp / 8);
    p[19] = 32; p[20] = DDPF_RGB; p[22] = (uint32_t)bpp;
    p[23] = 0xF800u; p[24] = 0x07E0u; p[25] = 0x001Fu;
    p[27] = caps;
}
/* The 108-byte DDSURFACEDESC: pf at 72 (word 18), caps at 104 (word 26),
 * lpSurface at 36 (word 9). */
static void fill_desc1(uint32_t *p, int w, int h, int bpp, uint32_t caps) {
    memset(p, 0, 64 * sizeof *p);
    p[0] = 108;
    p[1] = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT;
    p[2] = (uint32_t)h; p[3] = (uint32_t)w; p[4] = (uint32_t)(w * bpp / 8);
    p[18] = 32; p[19] = DDPF_RGB; p[21] = (uint32_t)bpp;
    p[22] = 0xF800u; p[23] = 0x07E0u; p[24] = 0x001Fu;
    p[26] = caps;
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

    /* ---- the DirectDraw7 object and a mode for the primary ---- */
    f->out = 0;
    {
        uint32_t a[4] = { 0, hle_va(&f->out), f->iid_va, 0 };
        f->rc_create = call("DirectDrawCreateEx", 4, a);
    }
    ok("DirectDrawCreateEx(IID_IDirectDraw7) -> DD_OK", f->rc_create == DD_OK && f->out);
    if (!f->out) { mach_leave(); return 1; }
    f->dd7 = f->out;
    /* COM method shims register when the DirectDraw vtables are first built. */
    ok("IDirectDrawSurface::Lock resolves by name", hle_resolve("IDirectDrawSurface::Lock") != 0);
    {
        uint32_t a[3] = { 320, 200, 16 };
        f->rc_mode = vcall(f->dd7, 21, 4, a);
    }
    ok("IDirectDraw7::SetDisplayMode(320x200x16) -> DD_OK", f->rc_mode == DD_OK);

    /* ---- a DDSURFACEDESC2 primary, locked on a sub-rectangle ---- */
    fill_desc2(f->desc2, 320, 200, 16, DDSCAPS_PRIMARYSURFACE);
    f->out = 0;
    {
        uint32_t a[3] = { hle_va(f->desc2), hle_va(&f->out), 0 };
        f->rc_create_primary = vcall(f->dd7, 6, 4, a);
    }
    ok("CreateSurface(DDSURFACEDESC2 primary) -> DD_OK", f->rc_create_primary == DD_OK && f->out);
    f->primary = f->out;
    if (!f->primary) { mach_leave(); return 1; }

    {
        const uint32_t *pal; unsigned seq; int w, h, bpp;
        ok("hle_dd_frame exposes the primary", hle_dd_frame(&f->px, &w, &h, &f->pitch, &bpp, &pal, &seq) &&
           w == 320 && h == 200 && bpp == 16);
    }
    f->base = hle_va(f->px);
    MEM16(f->base) = 0x001Fu;                            /* sentinel at (0,0) */
    f->rect2[0] = 10; f->rect2[1] = 20; f->rect2[2] = 18; f->rect2[3] = 26;
    f->expected2 = hle_va(f->px + (size_t)20 * (size_t)f->pitch + (size_t)10 * 2u);
    memset(f->lock2, 0, sizeof f->lock2);
    MEM32(hle_va(f->lock2)) = 124;
    {
        uint32_t a[4] = { hle_va(f->rect2), hle_va(f->lock2), 0, 0 };
        f->rc_lock2 = mcall("IDirectDrawSurface::Lock", f->primary, 5, a);
    }
    ok("Lock(rect, DDSURFACEDESC2) -> DD_OK", f->rc_lock2 == DD_OK);
    f->got2 = MEM32(hle_va(f->lock2) + 40);
    ok("DDSURFACEDESC2 lpSurface(+40) is the sub-rectangle address", f->got2 == f->expected2);
    ok("... not the whole-surface address", f->got2 != f->base);
    ok("... descriptor size and pitch survive the Lock",
       MEM32(hle_va(f->lock2)) == 124 && MEM32(hle_va(f->lock2) + 16) == (uint32_t)f->pitch);
    ok("... the +36 colour-key slot is not clobbered by a pointer",
       MEM32(hle_va(f->lock2) + 36) == 0);
    if (f->got2) MEM16(f->got2) = 0xF800u;               /* red, through the returned pointer */
    f->rc_unlock2 = mcall("IDirectDrawSurface::Unlock", f->primary, 1, 0);
    ok("Unlock -> DD_OK", f->rc_unlock2 == DD_OK);
    ok("the pixel landed at (10,20) in the shared frame", MEM16(f->expected2) == 0xF800u);
    ok("the pixel at (0,0) was not moved by the sub-rect Lock", MEM16(f->base) == 0x001Fu);

    /* ---- the legacy 108-byte shape must keep working at +36 ---- */
    fill_desc1(f->desc1, 32, 32, 16, DDSCAPS_SYSTEMMEMORY);
    f->out = 0;
    {
        uint32_t a[3] = { hle_va(f->desc1), hle_va(&f->out), 0 };
        f->rc_create_offscreen = vcall(f->dd7, 6, 4, a);
    }
    ok("CreateSurface(DDSURFACEDESC offscreen) -> DD_OK", f->rc_create_offscreen == DD_OK && f->out);
    f->offscreen = f->out;
    if (f->offscreen) {
        uint8_t *opx; int ow, oh, obpp;
        ok("hle_dd_surface_pixels exposes the offscreen surface",
           hle_dd_surface_pixels(f->offscreen, &opx, &ow, &oh, &f->pitch, &obpp) &&
           ow == 32 && oh == 32 && obpp == 16);
        f->rect1[0] = 4; f->rect1[1] = 6; f->rect1[2] = 8; f->rect1[3] = 10;
        f->expected1 = hle_va(opx + (size_t)6 * (size_t)f->pitch + (size_t)4 * 2u);
        memset(f->lock1, 0, sizeof f->lock1);
        MEM32(hle_va(f->lock1)) = 108;
        {
            uint32_t a[4] = { hle_va(f->rect1), hle_va(f->lock1), 0, 0 };
            f->rc_lock1 = mcall("IDirectDrawSurface::Lock", f->offscreen, 5, a);
        }
        ok("Lock(rect, DDSURFACEDESC) -> DD_OK", f->rc_lock1 == DD_OK);
        f->got1 = MEM32(hle_va(f->lock1) + 36);
        ok("legacy lpSurface(+36) is still the sub-rectangle address", f->got1 == f->expected1);
        f->rc_unlock1 = mcall("IDirectDrawSurface::Unlock", f->offscreen, 1, 0);
        ok("legacy Unlock -> DD_OK", f->rc_unlock1 == DD_OK);
    }

    /* Balance the references we took; the global DD7 object lives on. */
    {
        uint32_t one[1] = { 0 };
        if (f->offscreen) mcall("IDirectDrawSurface::Release", f->offscreen, 1, one);
        mcall("IDirectDrawSurface::Release", f->primary, 1, one);
        vcall(f->dd7, 2, 1, one);
    }

    mach_leave();
    printf("ddraw7_lock_rect_selftest: %s\n", fails ? "FAIL" : "all checks passed");
    return fails != 0;
}
