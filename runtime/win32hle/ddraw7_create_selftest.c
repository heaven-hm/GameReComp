/*
 * ddraw7_create_selftest.c - the DirectDraw7 gate AND the surface path behind
 * it, driven through the real ddraw.c shims with a synthetic lifted callback.
 *
 * wf.exe (Warning Forever) first enumerates DirectDraw7 devices. Its callback
 * (guest 0x405270) creates a DirectDraw7 object, calls GetCaps with a
 * 380-byte DDCAPS_DX7 and accepts the device only if DDCAPS.dwCaps bit 0 and
 * ddsCaps.dwCaps bit DDSCAPS_TEXTURE (byte 364) are set. If it rejects, the
 * game shows "ERROR: DirectDraw7 ..." and exits before creating a surface.
 * After the gate it creates a clipper and a DDSURFACEDESC2 primary surface;
 * the old CreateSurface read the DDSURFACEDESC (108-byte) field offsets from
 * that 128-byte structure, so it saw no DDSCAPS_PRIMARYSURFACE and produced
 * no primary for screen.c/present.c to show.
 *
 * This test exercises both stages, the two descriptor shapes, the documented
 * failure codes for bad arguments/unsupported IIDs, and the fact that the
 * resulting primary's pixels reach the existing software frame publisher.
 * No game assets, display or OpenGL context are needed (SDL dummy driver).
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

/* present.c's raw-surface publisher: the only frame path this test uses. */
extern void hle_present_surface(const uint8_t *px, int w, int h, int pitch, int bpp);

#define CB_VA          0x00401234u
#define DD_OK          0u
#define DDERR_INVALIDPARAMS 0x80070057u
#define E_NOINTERFACE  0x80004002u
#define DDCAPS_3D      0x00000001u
#define DDSCAPS_TEXTURE 0x00001000u
#define DDSCAPS_PRIMARYSURFACE 0x00000200u
#define DDSCAPS_COMPLEX 0x00000008u
#define DDSCAPS_FLIP    0x00000010u
#define DDSCAPS_BACKBUFFER 0x00000004u
#define DDSCAPS_SYSTEMMEMORY 0x00000800u
#define DDSD_CAPS      0x00000001u
#define DDSD_HEIGHT    0x00000002u
#define DDSD_WIDTH     0x00000004u
#define DDSD_PITCH     0x00000008u
#define DDSD_BACKBUFFERCOUNT 0x00000020u
#define DDSD_PIXELFORMAT 0x00001000u
#define DDPF_RGB       0x00000040u

static const uint8_t IID_IDirectDraw7[16] = {
    0xC0, 0x5E, 0xE6, 0x15, 0x1E, 0xA2, 0xD0, 0x11,
    0xB9, 0x2F, 0x00, 0x60, 0x97, 0x97, 0xEA, 0x5B
};
static const uint8_t IID_IDirectDraw[16] = {
    0x80, 0xDB, 0x14, 0x6C, 0x23, 0xAE, 0xCE, 0x11,
    0xA5, 0x21, 0x00, 0x20, 0xAF, 0x0B, 0xE5, 0x60
};

static int fails;
static void ok(const char *what, int cond) {
    printf("%s %s\n", cond ? "ok  " : "FAIL", what);
    if (!cond) fails++;
}

/* All guest-visible storage, including the argument vectors, is in the guest
 * heap: hle_call_guest pushes from guest addresses, never the host stack. */
typedef struct {
    uint32_t vec[8][4];
    uint32_t iid, iid_dd1, caps, out, ctx, calls, accepted;
    uint32_t rc_create, rc_caps, dwcaps, surfcaps, gate_rc;
    uint8_t iidbuf[16], iidbuf_dd1[16];
    uint8_t capsbuf[380];
    uint8_t bad_iid[16];
    uint32_t desc2[128], desc1[64], desc_bb[128], lock_desc[128];
    uint32_t dd7, clip, clip_via_dd, primary, back, owner;
    uint32_t rc_clip, rc_bad_clip, rc_setcoop, rc_create_surface, rc_attach;
    uint32_t rc_create_ex_bad, rc_directcreate, want_back;
} fixture_t;
static fixture_t *f;

#define GUEST_PROLOGUE(va) \
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0; RECOMP_ENTER(va)

/* BOOL CALLBACK(LPGUID, LPSTR, LPSTR, LPVOID) - wf.exe's device gate. */
static void dd7_enum_cb(void) {
    GUEST_PROLOGUE(CB_VA);
    uint32_t create = hle_resolve("DirectDrawCreateEx");
    (void)MEM32(esp + 4); (void)MEM32(esp + 8); (void)MEM32(esp + 12);
    f->calls++;
    f->ctx = MEM32(esp + 16);

    f->out = 0xDEADBEEFu;
    {
        uint32_t *a = f->vec[0];
        a[0] = 0; a[1] = hle_va(&f->out); a[2] = f->iid; a[3] = 0;
        f->rc_create = hle_call_guest(create, 4, a);
    }
    if (f->rc_create == DD_OK && f->out && f->out != 0xDEADBEEFu) {
        uint32_t dd7 = f->out, vt = MEM32(dd7);
        memset(f->capsbuf, 0, sizeof f->capsbuf);
        MEM32(f->caps) = 380;
        {
            uint32_t *a = f->vec[1];                     /* slot 11: GetCaps(this, &drv, &hel) */
            a[0] = dd7; a[1] = f->caps; a[2] = 0;
            f->rc_caps = hle_call_guest(MEM32(vt + 11u * 4u), 3, a);
        }
        f->dwcaps = MEM32(f->caps + 4);
        f->surfcaps = MEM32(f->caps + 364);
        if (f->rc_caps == DD_OK && (f->dwcaps & DDCAPS_3D) &&
            (f->surfcaps & DDSCAPS_TEXTURE)) f->accepted++;
        {
            uint32_t *a = f->vec[2];                     /* slot 2: Release */
            a[0] = dd7;
            hle_call_guest(MEM32(vt + 2u * 4u), 1, a);
        }
    }
    eax = 1u;                       /* DDENUMRET_OK */
    esp += 4 + 4u * 4u;             /* stdcall callee pops ret + 4 args */
}
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { CB_VA, dd7_enum_cb } };
const uint32_t recomp_dispatch_count = 1;

static uint32_t call(const char *n, int argc, const uint32_t *a) {
    return hle_call_guest(hle_resolve(n), argc, a);
}
/* Drive a COM method through the object's vtable (the guest's real path). */
static uint32_t vcall(uint32_t obj, int slot, int argc, uint32_t *a) {
    uint32_t va = MEM32(MEM32(obj) + 4u * (uint32_t)slot);
    if (!va) { fprintf(stderr, "FAIL empty vtable slot %d\n", slot); fails++; return 0xFFFFFFFFu; }
    uint32_t *v = f->vec[7];
    v[0] = obj;
    for (int i = 1; i < argc; i++) v[i] = a[i - 1];
    return hle_call_guest(va, argc, v);
}
/* Named shim entry, for surface methods whose slots are stable but verbose. */
static uint32_t mcall(const char *name, uint32_t obj, int argc, uint32_t *a) {
    uint32_t *v = f->vec[6];
    v[0] = obj;
    for (int i = 1; i < argc; i++) v[i] = a[i - 1];
    return hle_call_guest(hle_resolve(name), argc, v);
}

static int rec_n;
static uint32_t rec_first;
static void record_frame(const uint8_t *px, int w, int h, int pitch, int bpp) {
    (void)h; (void)bpp;
    rec_n++;
    if (px && pitch > 0) memcpy(&rec_first, px, sizeof rec_first < (size_t)pitch ? sizeof rec_first : (size_t)pitch);
}

/* DDSURFACEDESC2 as a 32-bit word array: pf at 76 (word 19), ddsCaps at 108
 * (word 27), dwSize 128. The old 108-byte layout put them at words 18/26. */
static void fill_desc2(uint32_t *p, int w, int h, int bpp, uint32_t caps, int backbuffers) {
    memset(p, 0, 128 * sizeof *p);
    p[0]  = 128;
    p[1]  = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT |
            (backbuffers ? DDSD_BACKBUFFERCOUNT : 0);
    p[2]  = (uint32_t)h; p[3] = (uint32_t)w; p[4] = (uint32_t)(w * bpp / 8);
    p[5]  = (uint32_t)backbuffers;
    p[19] = 32; p[20] = DDPF_RGB; p[21] = 0; p[22] = (uint32_t)bpp;
    p[23] = 0xF800u; p[24] = 0x07E0u; p[25] = 0x001Fu; p[26] = 0;
    p[27] = caps;
}

int main(void) {
    recomp_host_init();
    win32hle_register(win32hle_ddraw);
    ok("DirectDrawCreateEx resolves by name", hle_resolve("DirectDrawCreateEx") != 0);
    ok("DirectDrawEnumerateExA resolves by name", hle_resolve("DirectDrawEnumerateExA") != 0);
    mach_enter();

    f = (fixture_t *)hle_alloc(sizeof *f);
    memset(f, 0, sizeof *f);
    memcpy(f->iidbuf, IID_IDirectDraw7, 16);
    memcpy(f->iidbuf_dd1, IID_IDirectDraw, 16);
    f->iid = hle_va(f->iidbuf);
    f->iid_dd1 = hle_va(f->iidbuf_dd1);
    f->caps = hle_va(f->capsbuf);
    f->bad_iid[0] = 0x11;                                /* no such DirectDraw IID */

    /* ---- stage 1: the device gate that used to kill wf.exe ---- */
    {
        uint32_t ctx = 0x12345678u;
        f->gate_rc = call("DirectDrawEnumerateExA", 3, (uint32_t[]){ CB_VA, ctx, 0 });
        ok("DirectDrawEnumerateExA -> DD_OK", f->gate_rc == DD_OK);
        ok("the callback was visited once", f->calls == 1);
        ok("the context round-trips", f->ctx == ctx);
    }
    ok("gate's DirectDrawCreateEx(IID_IDirectDraw7) -> DD_OK", f->rc_create == DD_OK);
    ok("gate's GetCaps -> DD_OK", f->rc_caps == DD_OK);
    ok("DDCAPS.dwCaps advertises DDCAPS_3D", (f->dwcaps & DDCAPS_3D) != 0);
    ok("DDCAPS_DX7.ddsCaps.dwCaps advertises DDSCAPS_TEXTURE", (f->surfcaps & DDSCAPS_TEXTURE) != 0);
    ok("Warning Forever's DirectDraw7 gate accepts the device", f->accepted == 1);

    /* ---- stage 2: the factories and the post-gate creation sequence ---- */
    f->out = 0;
    ok("DirectDrawCreateEx(IID_IDirectDraw7) -> DD_OK",
       call("DirectDrawCreateEx", 4, (uint32_t[]){ 0, hle_va(&f->out), f->iid, 0 }) == DD_OK && f->out);
    f->dd7 = f->out;
    ok("DirectDrawCreateEx(IID_IDirectDraw) -> DD_OK",
       call("DirectDrawCreateEx", 4, (uint32_t[]){ 0, hle_va(&f->out), f->iid_dd1, 0 }) == DD_OK && f->out);
    {
        uint32_t *a = f->vec[0];
        a[0] = 0; a[1] = hle_va(&f->owner); a[2] = 0;
        f->rc_directcreate = call("DirectDrawCreate", 3, a);
        ok("DirectDrawCreate -> DD_OK", f->rc_directcreate == DD_OK && f->owner);
    }
    f->out = 0xDEADBEEFu;
    f->rc_create_ex_bad = call("DirectDrawCreateEx", 4,
        (uint32_t[]){ 0, hle_va(&f->out), hle_va(f->bad_iid), 0 });
    ok("unknown IID -> E_NOINTERFACE (not a wrong-shaped object)", f->rc_create_ex_bad == E_NOINTERFACE && f->out == 0);

    /* IDirectDraw7::SetCooperativeLevel is slot 20; DDSCL_NORMAL is fine. */
    {
        uint32_t a[2] = { 0x1234u, 1u };
        f->rc_setcoop = vcall(f->dd7, 20, 3, a);
    }
    ok("IDirectDraw7::SetCooperativeLevel -> DD_OK", f->rc_setcoop == DD_OK);

    /* CreateClipper (slot 4): valid args give a real clipper; invalid args
     * give the documented DDERR_INVALIDPARAMS and a cleared out pointer. */
    f->out = 0;
    {
        uint32_t a[3] = { 0, hle_va(&f->out), 0 };
        f->rc_clip = vcall(f->dd7, 4, 4, a);
    }
    ok("IDirectDraw7::CreateClipper -> DD_OK", f->rc_clip == DD_OK && f->out);
    f->clip = f->out;
    f->out = 0;
    {
        uint32_t a[3] = { 2, hle_va(&f->out), 0 };
        f->rc_bad_clip = vcall(f->dd7, 4, 4, a);
    }
    ok("CreateClipper(nonzero flags) -> DDERR_INVALIDPARAMS with out cleared",
       f->rc_bad_clip == DDERR_INVALIDPARAMS && f->out == 0);
    {
        uint32_t a[3] = { 0, hle_va(&f->clip_via_dd), 0 };
        ok("IDirectDraw::CreateClipper shares the clipper vtable",
           vcall(f->owner, 4, 4, a) == DD_OK &&
           MEM32(f->clip) && MEM32(f->clip) == MEM32(f->clip_via_dd));
    }
    {
        uint32_t a[2] = { 0, 0x1234u };                  /* SetHWnd: flags, hWnd */
        ok("IDirectDrawClipper::SetHWnd -> DD_OK", mcall("IDirectDrawClipper::SetHWnd", f->clip, 3, a) == DD_OK);
    }

    /* SetDisplayMode (slot 21) sets the geometry the primary must adopt. */
    {
        uint32_t a[3] = { 640, 480, 16 };
        ok("IDirectDraw7::SetDisplayMode -> DD_OK", vcall(f->dd7, 21, 4, a) == DD_OK);
    }

    /* A real DDSURFACEDESC2 primary with one back buffer. Before the fix the
     * 108-byte field offsets made caps==0, so no primary surface existed. */
    fill_desc2(f->desc2, 640, 480, 16,
               DDSCAPS_PRIMARYSURFACE | DDSCAPS_COMPLEX | DDSCAPS_FLIP, 1);
    f->out = 0;
    {
        uint32_t a[3] = { hle_va(f->desc2), hle_va(&f->out), 0 };
        f->rc_create_surface = vcall(f->dd7, 6, 4, a);
    }
    ok("IDirectDraw7::CreateSurface(DDSURFACEDESC2 primary) -> DD_OK",
       f->rc_create_surface == DD_OK && f->out != 0);
    f->primary = f->out;
    if (f->primary) {
        const uint8_t *px; int w, h, pitch, bpp; const uint32_t *pal; unsigned seq;
        ok("hle_dd_frame exposes the 640x480x16 primary",
           hle_dd_frame(&px, &w, &h, &pitch, &bpp, &pal, &seq) && w == 640 && h == 480 && bpp == 16);

        /* The back buffer is attached through DDSCAPS_BACKBUFFER. */
        f->want_back = DDSCAPS_BACKBUFFER;
        f->out = 0;
        {
            uint32_t a[2] = { hle_va(&f->want_back), hle_va(&f->out) };
            f->rc_attach = mcall("IDirectDrawSurface::GetAttachedSurface", f->primary, 3, a);
        }
        ok("primary has its back buffer", f->rc_attach == DD_OK && f->out != 0);
        f->back = f->out;

        /* Lock/Unlock the primary: the frame publisher sees those pixels. */
        uint32_t ld = hle_va(f->lock_desc);
        uint32_t lp[4] = { 0, ld, 0, 0 };                 /* rect, desc, flags, event */
        memset(f->lock_desc, 0, sizeof f->lock_desc);
        MEM32(ld) = 128;
        ok("IDirectDrawSurface::Lock(DDSURFACEDESC2) -> DD_OK",
           mcall("IDirectDrawSurface::Lock", f->primary, 5, lp) == DD_OK);
        ok("Lock wrote lpSurface at the DDSURFACEDESC2 offset +40", MEM32(ld + 40) != 0);
        ok("Lock wrote ddsCaps at the DDSURFACEDESC2 offset +108",
           (MEM32(ld + 108) & DDSCAPS_PRIMARYSURFACE) != 0);
        {
            uint32_t va = MEM32(ld + 40);
            uint16_t color = 0x07E0u;                    /* green in RGB565 */
            for (int y = 0; y < 8; y++) for (int x = 0; x < 8; x++)
                MEM16(va + (size_t)y * 1280u + (size_t)x * 2u) = color;
        }
        ok("IDirectDrawSurface::Unlock -> DD_OK",
           mcall("IDirectDrawSurface::Unlock", f->primary, 1, 0) == DD_OK);

        rec_n = 0; rec_first = 0;
        void (*previous)(const uint8_t *, int, int, int, int) = hle_screen_frame_hook;
        hle_screen_frame_hook = record_frame;
        ok("hle_dd_frame still exposes the written pixels", hle_dd_frame(&px, &w, &h, &pitch, &bpp, &pal, &seq));
        hle_present_surface(px, w, h, pitch, bpp);       /* present.c's software path */
        hle_screen_frame_hook = previous;
        ok("the shared presenter published one frame", rec_n == 1);
        ok("that frame carries the game's pixel", (rec_first & 0xFFFFu) == 0x07E0u);
    }

    /* IGI's back-buffer request sets only DDSD_CAPS: DirectDraw inherits the
     * display size instead of treating the unset width/height fields as 0x0.
     * Before the fix this returned DDERR_INVALIDPARAMS four times and the game
     * showed "Couldn't create device". */
    memset(f->desc2, 0, sizeof f->desc2);
    f->desc2[0] = 128;                          /* DDSURFACEDESC2.dwSize */
    f->desc2[1] = DDSD_CAPS;                    /* no DDSD_WIDTH/HEIGHT */
    f->desc2[27] = DDSCAPS_BACKBUFFER;
    f->out = 0;
    {
        uint32_t a[3] = { hle_va(f->desc2), hle_va(&f->out), 0 };
        f->rc_create_surface = vcall(f->dd7, 6, 4, a);
    }
    ok("CreateSurface with inherited size (DDSD_CAPS only) -> DD_OK",
       f->rc_create_surface == DD_OK && f->out != 0);
    if (f->out) {
        f->want_back = DDSCAPS_BACKBUFFER;
        f->out = 0;
        {
            uint32_t a[2] = { hle_va(&f->want_back), hle_va(&f->out) };
            f->rc_attach = mcall("IDirectDrawSurface::GetAttachedSurface", f->primary, 3, a);
        }
        ok("the separately created back buffer is attached to the primary",
           f->rc_attach == DD_OK && f->out != 0);
        uint32_t one[1] = { 0 };
        if (f->out) mcall("IDirectDrawSurface::Release", f->out, 1, one);
    }

    /* The legacy DDSURFACEDESC (108-byte) shape must still work. */
    memset(f->desc1, 0, sizeof f->desc1);
    f->desc1[0]  = 108;
    f->desc1[1]  = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT;
    f->desc1[2]  = 32; f->desc1[3] = 32; f->desc1[4] = 128;
    f->desc1[18] = 32; f->desc1[19] = DDPF_RGB; f->desc1[21] = 16;
    f->desc1[22] = 0xF800u; f->desc1[23] = 0x07E0u; f->desc1[24] = 0x001Fu;
    f->desc1[26] = DDSCAPS_SYSTEMMEMORY;
    f->out = 0;
    {
        uint32_t a[3] = { hle_va(f->desc1), hle_va(&f->out), 0 };
        ok("CreateSurface(DDSURFACEDESC legacy) -> DD_OK",
           vcall(f->dd7, 6, 4, a) == DD_OK && f->out != 0);
    }

    /* Release what we took; the global DD objects live on by design. */
    {
        uint32_t one[1] = { 0 };
        if (f->primary) mcall("IDirectDrawSurface::Release", f->primary, 1, one);
        if (f->back) mcall("IDirectDrawSurface::Release", f->back, 1, one);
        mcall("IDirectDrawClipper::Release", f->clip, 1, one);
        mcall("IDirectDrawClipper::Release", f->clip_via_dd, 1, one);
        vcall(f->dd7, 2, 1, one);
        vcall(f->owner, 2, 1, one);
    }

    mach_leave();
    printf("ddraw7_create_selftest: %s\n", fails ? "FAIL" : "all checks passed");
    return fails != 0;
}
