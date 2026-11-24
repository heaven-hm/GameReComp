/*
 * ddraw_extra_selftest.c - DirectDrawCreateClipper, the one real ddraw.dll
 * export the HLE did not serve.  MSDN says dwFlags must be 0, pUnkOuter must
 * be NULL, and the object is not owned by any DirectDraw object; this drives
 * exactly that contract through hle_call_guest, the way a title reaching it
 * through GetProcAddress does.
 *
 * What is asserted, besides the HRESULTs: the returned object has the very
 * same IDirectDrawClipper vtable as IDirectDraw::CreateClipper (so the guest's
 * vtable slots are identical), the nine slots are in ddraw.h order, refcounts
 * balance, and creating a clipper touches neither the shared display mode nor
 * the presenter - there is still no primary surface and no second renderer.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

#define DD_OK               0u
#define DDERR_INVALIDPARAMS 0x80070057u

static int fails;
static void ok(const char *what, int cond) {
    printf("%s %s\n", cond ? "ok  " : "FAIL", what);
    if (!cond) fails++;
}

/* Everything the guest sees lives in one hle_alloc block, including the
 * argument vectors: hle_call_guest pushes values from them into the guest
 * stack, and on a based host a host stack address is not a guest VA. */
typedef struct {
    uint32_t vec[8][4];                /* four-word stdcall argument vectors */
    uint32_t out, dd, clip, clip2, hwnd;
    int w, h, bpp;
    const uint8_t *px;
    const uint32_t *pal;
    int fw, fh, fpitch, fbpp;
    unsigned seq;
} fixture_t;
static fixture_t *f;

/* Three-argument stdcall vector (every factory export here takes three). */
static uint32_t *av(int i, uint32_t a, uint32_t b, uint32_t c) {
    uint32_t *v = f->vec[i];
    v[0] = a; v[1] = b; v[2] = c; v[3] = 0;
    return v;
}
/* Invoke a method at a guest vtable slot; `this` is arg 0. */
static uint32_t vcall(uint32_t obj, int slot, int n, const uint32_t *args) {
    uint32_t va = MEM32(MEM32(obj) + 4u * (uint32_t)slot);
    if (!va) { fprintf(stderr, "FAIL empty vtable slot %d\n", slot); fails++; return 0xFFFFFFFFu; }
    uint32_t *v = f->vec[7];
    v[0] = obj;
    for (int i = 1; i < n; i++) v[i] = args[i - 1];
    return hle_call_guest(va, n, v);
}

int main(void) {
    recomp_host_init();
    mach_enter();
    f = (fixture_t *)hle_alloc(sizeof *f);

    uint32_t create_clipper = hle_resolve("DirectDrawCreateClipper");
    ok("DirectDrawCreateClipper resolves by name (GetProcAddress path)", create_clipper != 0);
    if (!create_clipper) { mach_leave(); return 1; }

    /* Baseline: a clipper must not open a display, create a primary surface
     * or start an OpenGL context. Capture that before any DirectDraw call. */
    hle_dd_mode(&f->w, &f->h, &f->bpp);
    int gl_before = hle_screen_gl_active();
    int frame_before = hle_dd_frame(&f->px, &f->fw, &f->fh, &f->fpitch, &f->fbpp, &f->pal, &f->seq);

    /* --- the happy path --- */
    f->out = 0xDEADBEEFu;
    uint32_t rc = hle_call_guest(create_clipper, 3, av(0, 0, hle_va(&f->out), 0));
    ok("flags=0, outer=NULL -> DD_OK", rc == DD_OK);
    ok("out receives a real object", f->out && f->out != 0xDEADBEEFu);
    if (!f->out) { mach_leave(); return 1; }
    f->clip = f->out;
    ok("new clipper refcount is 1", MEM32(f->clip + 4) == 1);
    ok("new clipper has no HWND", MEM32(f->clip + 8) == 0);

    /* Same interface as the DirectDraw-owned clipper: same vtable, same slots. */
    rc = hle_call_guest(hle_resolve("DirectDrawCreate"), 3, av(1, 0, hle_va(&f->dd), 0));
    ok("DirectDrawCreate -> DD_OK", rc == DD_OK && f->dd != 0);
    f->out = 0;
    /* IDirectDraw::CreateClipper(this, flags, &out, outer): the helper's
     * `this` is arg 0, so write the other three by hand. */
    {
        uint32_t *v = f->vec[2];
        v[0] = f->dd; v[1] = 0; v[2] = hle_va(&f->out); v[3] = 0;
        rc = hle_call_guest(MEM32(MEM32(f->dd) + 16u), 4, v);  /* slot 4: CreateClipper */
    }
    ok("IDirectDraw::CreateClipper -> DD_OK", rc == DD_OK && f->out != 0);
    f->clip2 = f->out;
    ok("standalone clipper has IDirectDraw's clipper vtable", f->clip2 && MEM32(f->clip) == MEM32(f->clip2));

    /* ddraw.h order, indices 0..8. */
    static const char *const clip_methods[9] = {
        "IDirectDrawClipper::QueryInterface", "IDirectDrawClipper::AddRef",
        "IDirectDrawClipper::Release", "IDirectDrawClipper::GetClipList",
        "IDirectDrawClipper::GetHWnd", "IDirectDrawClipper::Initialize",
        "IDirectDrawClipper::IsClipListChanged", "IDirectDrawClipper::SetClipList",
        "IDirectDrawClipper::SetHWnd"
    };
    uint32_t vt = MEM32(f->clip);
    for (int i = 0; i < 9; i++) {
        char label[64];
        snprintf(label, sizeof label, "vtable slot %d = %s", i, clip_methods[i] + strlen("IDirectDrawClipper::"));
        ok(label, MEM32(vt + 4u * (uint32_t)i) == hle_resolve(clip_methods[i]));
    }

    /* COM lifetime and a SetHWnd/GetHWnd round trip through those slots. */
    {
        uint32_t one[1] = { 0 };
        ok("AddRef -> 2", vcall(f->clip, 1, 1, one) == 2 && MEM32(f->clip + 4) == 2);
        ok("Release -> 1", vcall(f->clip, 2, 1, one) == 1 && MEM32(f->clip + 4) == 1);
    }
    f->hwnd = 0x1234u;
    {
        /* IDirectDrawClipper::SetHWnd(dwFlags, hWnd): hWnd is arg 2. */
        uint32_t a[2] = { 0, f->hwnd };
        ok("SetHWnd -> DD_OK", vcall(f->clip, 8, 3, a) == DD_OK);
    }
    f->out = 0;
    {
        uint32_t a[2] = { hle_va(&f->out), 0 };
        ok("GetHWnd -> DD_OK with the HWND we set", vcall(f->clip, 4, 2, a) == DD_OK && f->out == f->hwnd);
    }

    /* --- the failure paths are errors, not fabricated clippers --- */
    f->out = 0xDEADBEEFu;
    rc = hle_call_guest(create_clipper, 3, av(0, 2, hle_va(&f->out), 0));
    ok("nonzero flags -> DDERR_INVALIDPARAMS", rc == DDERR_INVALIDPARAMS);
    ok("failure clears the out pointer", f->out == 0);
    f->out = 0xDEADBEEFu;
    rc = hle_call_guest(create_clipper, 3, av(0, 0, hle_va(&f->out), 0x1234));
    ok("non-NULL aggregation outer -> DDERR_INVALIDPARAMS", rc == DDERR_INVALIDPARAMS && f->out == 0);
    rc = hle_call_guest(create_clipper, 3, av(0, 0, 0, 0));
    ok("NULL out -> DDERR_INVALIDPARAMS (no fault)", rc == DDERR_INVALIDPARAMS);

    /* --- no shared-display side effects --- */
    {
        int w, h, bpp;
        hle_dd_mode(&w, &h, &bpp);
        ok("display mode untouched", w == f->w && h == f->h && bpp == f->bpp);
        const uint8_t *px; int fw, fh, fp, fb; const uint32_t *pal; unsigned seq;
        ok("still no primary surface", hle_dd_frame(&px, &fw, &fh, &fp, &fb, &pal, &seq) == frame_before);
        ok("no second renderer / GL context opened", hle_screen_gl_active() == gl_before);
    }

    /* Balance the references we took. */
    {
        uint32_t one[1] = { 0 };
        ok("final Release of standalone clipper -> 0", vcall(f->clip, 2, 1, one) == 0);
        if (f->clip2) vcall(f->clip2, 2, 1, one);
        if (f->dd) vcall(f->dd, 2, 1, one);
    }

    mach_leave();
    printf("ddraw_extra_selftest: %s\n", fails ? "FAIL" : "all checks passed");
    return fails != 0;
}
