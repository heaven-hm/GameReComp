/*
 * ddraw7_init_selftest.c - the DirectDraw7 device gate that makes or breaks
 * Warning Forever (wf.exe) before it creates a single surface.
 *
 * wf.exe enumerates devices with DirectDrawEnumerateExA. Its callback (guest
 * 0x405270) creates a DirectDraw7 object, calls IDirectDraw7::GetCaps with a
 * 380-byte DDCAPS_DX7 (dwSize = 0x17c) and accepts the device only when
 *
 *     caps.dwCaps & DDCAPS_3D                       (offset   4)
 *     caps.ddsCaps.dwCaps & DDSCAPS_TEXTURE         (offset 364 = 0x16c)
 *
 * are both set. If either is zero the device is never recorded, word [ctx+8]
 * stays 0 and the game puts up "ERROR: DirectDraw7 ..." and exits 0. The HLE
 * used to write the surface caps at byte 104 (a DDSURFACEDESC2 offset, inside
 * DDCAPS.dwRops) so the DX7 field was always zero and the device was refused.
 *
 * This drives the real shims through hle_call_guest with a synthetic lifted
 * callback that performs exactly that gate, then checks both the DX7 and the
 * legacy DDCAPS field shapes. No game assets, display or OpenGL needed.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

#define CB_VA          0x00401000u
#define DD_OK          0u
#define DDCAPS_3D      0x00000001u
#define DDSCAPS_TEXTURE 0x00001000u
#define DDSCAPS_3DDEVICE 0x00002000u

static const uint8_t IID_IDirectDraw7[16] = {
    0xC0, 0x5E, 0xE6, 0x15, 0x1E, 0xA2, 0xD0, 0x11,
    0xB9, 0x2F, 0x00, 0x60, 0x97, 0x97, 0xEA, 0x5B
};

static int fails;
static void ok(const char *what, int cond) {
    printf("%s %s\n", cond ? "ok  " : "FAIL", what);
    if (!cond) fails++;
}

/* All guest-visible storage is in the guest heap: hle_call_guest pushes the
 * argument vectors from guest addresses, not host stack addresses. */
typedef struct {
    uint32_t vec[4][4];              /* stdcall argument vectors */
    uint32_t iid, caps;              /* guest VAs into the buffers below */
    uint32_t dd7, dd7b, out, ctx, calls, accepted;
    uint32_t rc_create, rc_caps, dwcaps, surfcaps, legacy;
    uint8_t iidbuf[16];
    uint8_t capsbuf[380];
    uint8_t smallcaps[316];
} fixture_t;
static fixture_t *f;

/* Lifted-function prologue, as in the other callback selftests. */
#define GUEST_PROLOGUE(va) \
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0; RECOMP_ENTER(va)

/* BOOL CALLBACK(LPGUID, LPSTR, LPSTR, LPVOID) - wf.exe's device gate. */
static void dd7_enum_cb(void) {
    GUEST_PROLOGUE(CB_VA);
    uint32_t guid = MEM32(esp + 4), desc = MEM32(esp + 8);
    uint32_t name = MEM32(esp + 12), ctx = MEM32(esp + 16);
    uint32_t create = hle_resolve("DirectDrawCreateEx");
    (void)guid; (void)desc; (void)name;
    f->calls++;
    f->ctx = ctx;

    /* DirectDrawCreateEx(NULL, &out, IID_IDirectDraw7, NULL) */
    f->out = 0xDEADBEEFu;
    {
        uint32_t *a = f->vec[0];
        a[0] = 0; a[1] = hle_va(&f->out); a[2] = f->iid; a[3] = 0;
        f->rc_create = hle_call_guest(create, 4, a);
    }
    if (f->rc_create == DD_OK && f->out && f->out != 0xDEADBEEFu) {
        uint32_t vt;
        f->dd7 = f->out;
        vt = MEM32(f->dd7);

        /* DDCAPS_DX7, dwSize = 380 (0x17c), exactly what the game passes. */
        memset(f->capsbuf, 0, sizeof f->capsbuf);
        MEM32(f->caps) = 380;
        {
            uint32_t *a = f->vec[1];                     /* slot 11: GetCaps(this, &drv, &hel) */
            a[0] = f->dd7; a[1] = f->caps; a[2] = 0;
            f->rc_caps = hle_call_guest(MEM32(vt + 11u * 4u), 3, a);
        }
        f->dwcaps = MEM32(f->caps + 4);
        f->surfcaps = MEM32(f->caps + 364);
        if (f->rc_caps == DD_OK && (f->dwcaps & DDCAPS_3D) &&
            (f->surfcaps & (DDSCAPS_TEXTURE | DDSCAPS_3DDEVICE))) f->accepted++;

        {
            uint32_t *a = f->vec[2];                     /* slot 2: Release */
            a[0] = f->dd7;
            hle_call_guest(MEM32(vt + 2u * 4u), 1, a);
        }
    }
    eax = 1u;                       /* DDENUMRET_OK: keep enumerating */
    esp += 4 + 4u * 4u;             /* stdcall callee pops ret + 4 args */
}

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { CB_VA, dd7_enum_cb } };
const uint32_t recomp_dispatch_count = 1;

static uint32_t call(const char *n, int argc, const uint32_t *a) {
    return hle_call_guest(hle_resolve(n), argc, a);
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
    f->iid = hle_va(f->iidbuf);
    f->caps = hle_va(f->capsbuf);

    /* The game's startup path: enumerate, then look at the recorded count. */
    {
        uint32_t ctx = 0x12345678u;
        uint32_t r = call("DirectDrawEnumerateExA", 3, (uint32_t[]){ CB_VA, ctx, 1u });
        ok("DirectDrawEnumerateExA -> DD_OK", r == DD_OK);
        ok("the callback was visited once", f->calls == 1);
        ok("the context round-trips to the callback", f->ctx == ctx);
    }
    ok("DirectDrawCreateEx(IID_IDirectDraw7) -> DD_OK",
       f->rc_create == DD_OK && f->dd7 != 0 && f->dd7 != 0xDEADBEEFu);
    ok("IDirectDraw7::GetCaps -> DD_OK", f->rc_caps == DD_OK);
    ok("DDCAPS.dwCaps advertises DDCAPS_3D", (f->dwcaps & DDCAPS_3D) != 0);
    ok("DDCAPS_DX7.ddsCaps.dwCaps advertises DDSCAPS_TEXTURE",
       (f->surfcaps & DDSCAPS_TEXTURE) != 0);
    ok("Warning Forever's gate accepts the DirectDraw7 device", f->accepted == 1);

    /* A DX3/DX5-size DDCAPS must still get the legacy DDSCAPS at byte 132,
     * and nothing must be written past the end of the smaller structure. */
    {
        uint32_t *a = f->vec[3];
        a[0] = 0; a[1] = hle_va(&f->dd7b); a[2] = f->iid; a[3] = 0;
        ok("second DirectDrawCreateEx -> DD_OK", call("DirectDrawCreateEx", 4, a) == DD_OK && f->dd7b != 0);
        if (f->dd7b) {
            uint32_t vt = MEM32(f->dd7b), sc = hle_va(f->smallcaps);
            uint32_t *b = f->vec[0];
            memset(f->smallcaps, 0, sizeof f->smallcaps);
            MEM32(sc) = 316;
            b[0] = f->dd7b; b[1] = sc; b[2] = 0;
            ok("GetCaps(DX3-size DDCAPS) -> DD_OK",
               hle_call_guest(MEM32(vt + 11u * 4u), 3, b) == DD_OK);
            f->legacy = MEM32(sc + 132);
            ok("legacy DDSCAPS at byte 132 advertises DDSCAPS_TEXTURE",
               (f->legacy & DDSCAPS_TEXTURE) != 0);
            ok("DX7 field is not written past a 316-byte caller", MEM32(sc + 364) == 0);
            b[0] = f->dd7b;
            hle_call_guest(MEM32(vt + 2u * 4u), 1, b);
        }
    }

    mach_leave();
    printf("ddraw7_init_selftest: %s\n", fails ? "FAIL" : "all checks passed");
    return fails != 0;
}
