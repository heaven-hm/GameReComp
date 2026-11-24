/*
 * ddraw_palette_selftest.c - IDirectDrawPalette through the real guest ABI.
 *
 * The capability under test is the one a title crosses when it builds a
 * palettised primary: IDirectDraw::CreatePalette hands the host a guest
 * PALETTEENTRY array whose size comes from dwFlags, GetCaps/GetEntries/SetEntries
 * walk that array, and the shared presenter (screen.c via hle_dd_frame) colours
 * an 8-bpp primary with the current entries. The old host copied a fixed 256
 * entries regardless of flags and checked `start + count > 256` with an
 * overflowing sum, so a 4-bit palette over-read its 16-entry array and an
 * out-of-range range could wrap into a valid one.
 *
 * Everything here is driven as lifted code would drive it: stdcall argument
 * vectors in guest memory, calls through the object's vtable via
 * hle_call_guest, and the frame hle_dd_frame publishes to the existing
 * presenter. No renderer, display mode change beyond SetDisplayMode, or game
 * asset is involved. Evidence lines record the entries and the frame before
 * and after a SetEntries call.
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

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

#define DD_OK                   0u
#define DDERR_INVALIDPARAMS     0x80070057u
#define DDERR_INVALIDCAPS       0x88760064u
#define DDSD_CAPS               0x00000001u
#define DDSD_HEIGHT             0x00000002u
#define DDSD_WIDTH              0x00000004u
#define DDSD_PITCH              0x00000008u
#define DDSD_PIXELFORMAT        0x00001000u
#define DDPF_RGB                0x00000040u
#define DDPF_PALETTEINDEXED8    0x00000020u
#define DDSCAPS_PRIMARYSURFACE  0x00000200u
#define DDPCAPS_4BIT            0x00000001u
#define DDPCAPS_8BIT            0x00000004u
#define DDPCAPS_ALLOW256        0x00000040u
#define DDPCAPS_1BIT            0x00000100u
#define DDPCAPS_2BIT            0x00000200u

/* Guest vtable slots, from ddraw.h. */
#define DD_CREATEPALETTE        5
#define DD_GETCAPS              11
#define DD_SETDISPLAYMODE       21
#define SF_GETPALETTE           20
#define SF_LOCK                 25
#define SF_SETPALETTE           31
#define SF_UNLOCK               32
#define PAL_GETCAPS             3
#define PAL_GETENTRIES          4
#define PAL_INITIALIZE          5
#define PAL_SETENTRIES          6

static int fails;
static void ok(const char *what, int cond) {
    printf("%s %s\n", cond ? "ok  " : "FAIL", what);
    if (!cond) fails++;
}

/* All guest-visible storage, including argument vectors, lives in the guest
 * heap: hle_call_guest pushes values from guest addresses, never the host
 * stack (which is not a guest VA on a based host). */
typedef struct {
    uint32_t vec[8][5];
    uint32_t out, dd, primary, pal, pal4, pal_from_surface;
    uint32_t caps, lock_desc, entries_va, entries4_va, fetched_va, change_va;
    uint32_t rc, seq_before, seq_after, hook_before, hook_after;
    uint32_t desc[128], lock[128];
    uint32_t entries[256], entries4[16], fetched[256], change[2];
    uint8_t capsbuf[380];
    int fw, fh, fpitch, fbpp;
    const uint8_t *px;
    const uint32_t *palette;
    unsigned seq;
} fixture_t;
static fixture_t *f;

static uint32_t hook_calls;
static void count_hook(void) { hook_calls++; }

static uint32_t *av(int i, int n, const uint32_t *a) {
    uint32_t *v = f->vec[i];
    for (int k = 0; k < n; k++) v[k] = a[k];
    for (int k = n; k < 5; k++) v[k] = 0;
    return v;
}

/* Invoke a method at a guest vtable slot; `this` is arg 0. */
static uint32_t vcall(uint32_t obj, int slot, int argc, const uint32_t *args) {
    uint32_t va = MEM32(MEM32(obj) + 4u * (uint32_t)slot);
    if (!va) { fprintf(stderr, "FAIL empty vtable slot %d\n", slot); fails++; return 0xFFFFFFFFu; }
    uint32_t *v = f->vec[7];
    v[0] = obj;
    for (int i = 1; i < argc; i++) v[i] = args[i - 1];
    return hle_call_guest(va, argc, v);
}
static uint32_t mcall(const char *name, uint32_t obj, int argc, const uint32_t *args) {
    uint32_t *v = f->vec[6];
    v[0] = obj;
    for (int i = 1; i < argc; i++) v[i] = args[i - 1];
    return hle_call_guest(hle_resolve(name), argc, v);
}

/* The exact 8-bpp palette lookup screen.c performs for the shared presenter. */
static uint32_t screen_argb(const uint32_t *pal, unsigned index) {
    uint32_t e = pal ? pal[index] : 0;
    return 0xFF000000u | ((e & 0xFFu) << 16) | (e & 0xFF00u) | ((e >> 16) & 0xFFu);
}
static uint32_t pe(unsigned r, unsigned g, unsigned b) { return r | (g << 8) | (b << 16); }

static int rec_n;
static uint32_t rec_first, rec_bpp, rec_pitch;
static void record_frame(const uint8_t *px, int w, int h, int pitch, int bpp) {
    (void)w; (void)h;
    rec_n++;
    rec_pitch = (uint32_t)pitch;
    rec_bpp = (uint32_t)bpp;
    if (px && pitch > 0) memcpy(&rec_first, px, sizeof rec_first < (size_t)pitch ? sizeof rec_first : (size_t)pitch);
}

int main(void) {
    recomp_host_init();
    win32hle_register(win32hle_ddraw);
    mach_enter();
    f = (fixture_t *)hle_alloc(sizeof *f);
    memset(f, 0, sizeof *f);
    f->caps = hle_va(f->capsbuf);
    f->lock_desc = hle_va(f->lock);
    f->entries_va = hle_va(f->entries);
    f->entries4_va = hle_va(f->entries4);
    f->fetched_va = hle_va(f->fetched);
    f->change_va = hle_va(f->change);

    uint32_t create = hle_resolve("DirectDrawCreate");
    ok("DirectDrawCreate resolves", create != 0);
    if (!create) { mach_leave(); return 1; }

    /* ---- baseline: no primary, no palette ---- */
    int frame_before = hle_dd_frame(&f->px, &f->fw, &f->fh, &f->fpitch, &f->fbpp, &f->palette, &f->seq);
    ok("no primary surface before the test", frame_before == 0);
    printf("[evidence] baseline: hle_dd_frame=%d, frame_seq=%u\n", frame_before, f->seq);

    f->out = 0;
    f->rc = hle_call_guest(create, 3, av(0, 3, (uint32_t[]){ 0, hle_va(&f->out), 0 }));
    ok("DirectDrawCreate -> DD_OK", f->rc == DD_OK && f->out != 0);
    if (!f->out) { mach_leave(); return 1; }
    f->dd = f->out;

    /* DDCAPS.dwPalCaps (byte 24) must advertise the depths CreatePalette serves. */
    memset(f->capsbuf, 0, sizeof f->capsbuf);
    vcall(f->dd, DD_GETCAPS, 3, (uint32_t[]){ f->caps, 0 });
    uint32_t pal_caps = MEM32(f->caps + 24);
    ok("DDCAPS.dwPalCaps advertises 1/2/4/8-bit palettes",
       (pal_caps & (DDPCAPS_1BIT | DDPCAPS_2BIT | DDPCAPS_4BIT | DDPCAPS_8BIT | DDPCAPS_ALLOW256)) ==
       (DDPCAPS_1BIT | DDPCAPS_2BIT | DDPCAPS_4BIT | DDPCAPS_8BIT | DDPCAPS_ALLOW256));
    printf("[evidence] DDCAPS.dwPalCaps=0x%08X\n", pal_caps);

    /* ---- CreatePalette: 8-bit, 256 bounded PALETTEENTRYs ---- */
    for (unsigned i = 0; i < 256; i++) f->entries[i] = pe(i & 0xFF, (i >> 3) & 0xFF, (i >> 5) & 0xFF);
    f->out = 0xDEADBEEFu;
    f->rc = vcall(f->dd, DD_CREATEPALETTE, 5,
                  (uint32_t[]){ DDPCAPS_8BIT | DDPCAPS_ALLOW256, f->entries_va, hle_va(&f->out), 0 });
    ok("CreatePalette(DDPCAPS_8BIT|ALLOW256) -> DD_OK", f->rc == DD_OK);
    ok("CreatePalette writes a real object and clears nothing after success", f->out != 0 && f->out != 0xDEADBEEFu);
    if (!f->out) { mach_leave(); return 1; }
    f->pal = f->out;
    ok("new palette refcount is 1", MEM32(f->pal + 4) == 1);
    ok("new palette object has a guest-readable vtable", MEM32(f->pal) != 0);

    /* The seven methods must sit in their ddraw.h slots under their ddraw.h
     * names: that registered table is the COM/stdcall ABI the guest binds. */
    static const char *const pal_methods[7] = {
        "IDirectDrawPalette::QueryInterface", "IDirectDrawPalette::AddRef",
        "IDirectDrawPalette::Release", "IDirectDrawPalette::GetCaps",
        "IDirectDrawPalette::GetEntries", "IDirectDrawPalette::Initialize",
        "IDirectDrawPalette::SetEntries"
    };
    for (int i = 0; i < 7; i++) {
        char label[80];
        snprintf(label, sizeof label, "palette vtable slot %d = %s", i, pal_methods[i] + strlen("IDirectDrawPalette::"));
        ok(label, hle_resolve(pal_methods[i]) != 0 && MEM32(MEM32(f->pal) + 4u * (uint32_t)i) == hle_resolve(pal_methods[i]));
    }

    /* The 4-bit factory must read 16 entries, not 1 KB. Fill only 16 words
     * and put a guard immediately after: a fixed-size copy would read it. */
    for (unsigned i = 0; i < 16; i++) f->entries4[i] = pe(0x80 + i, 0x40 + i, 0x20 + i);
    f->out = 0;
    f->rc = vcall(f->dd, DD_CREATEPALETTE, 5,
                  (uint32_t[]){ DDPCAPS_4BIT, f->entries4_va, hle_va(&f->out), 0 });
    ok("CreatePalette(DDPCAPS_4BIT, 16-entry array) -> DD_OK", f->rc == DD_OK && f->out != 0);
    f->pal4 = f->out;
    if (f->pal4) {
        f->rc = vcall(f->pal4, PAL_GETCAPS, 2, (uint32_t[]){ hle_va(&f->out) });
        ok("GetCaps reports the flags the guest created with (4-bit)", f->rc == DD_OK && f->out == DDPCAPS_4BIT);
        memset(f->fetched, 0xEE, sizeof f->fetched);
        f->rc = vcall(f->pal4, PAL_GETENTRIES, 5, (uint32_t[]){ 0, 0, 16, f->fetched_va });
        ok("GetEntries(0,16) copies exactly the 16 declared entries",
           f->rc == DD_OK && memcmp(f->fetched, f->entries4, 16 * 4) == 0);
        ok("entry 16 is untouched by that bounded copy", f->fetched[16] == 0xEEEEEEEEu);
        ok("GetEntries(16,1) is past a 4-bit palette's end -> DDERR_INVALIDPARAMS",
           vcall(f->pal4, PAL_GETENTRIES, 5, (uint32_t[]){ 0, 16, 1, f->fetched_va }) == DDERR_INVALIDPARAMS);
    }

    /* GetCaps of the 8-bit palette, and a full round trip of all 256 entries. */
    f->out = 0;
    f->rc = vcall(f->pal, PAL_GETCAPS, 2, (uint32_t[]){ hle_va(&f->out) });
    ok("GetCaps reports DDPCAPS_8BIT|ALLOW256", f->rc == DD_OK && f->out == (DDPCAPS_8BIT | DDPCAPS_ALLOW256));
    memset(f->fetched, 0, sizeof f->fetched);
    f->rc = vcall(f->pal, PAL_GETENTRIES, 5, (uint32_t[]){ 0, 0, 256, f->fetched_va });
    ok("GetEntries(0,256) round-trips the whole guest array", f->rc == DD_OK && memcmp(f->fetched, f->entries, sizeof f->entries) == 0);
    memset(f->fetched, 0, sizeof f->fetched);
    f->rc = vcall(f->pal, PAL_GETENTRIES, 5, (uint32_t[]){ 0, 7, 2, f->fetched_va });
    ok("GetEntries(7,2) returns exactly that slice", f->rc == DD_OK && f->fetched[0] == f->entries[7] && f->fetched[1] == f->entries[8]);

    /* ---- fail closed: bad flags, bounds, pointers and handles ---- */
    f->out = 0xDEADBEEFu;
    ok("CreatePalette(NULL out) -> DDERR_INVALIDPARAMS",
       vcall(f->dd, DD_CREATEPALETTE, 5, (uint32_t[]){ DDPCAPS_8BIT, f->entries_va, 0, 0 }) == DDERR_INVALIDPARAMS);
    f->out = 0xDEADBEEFu;
    ok("CreatePalette(non-NULL outer) -> DDERR_INVALIDPARAMS, out cleared",
       vcall(f->dd, DD_CREATEPALETTE, 5, (uint32_t[]){ DDPCAPS_8BIT, f->entries_va, hle_va(&f->out), 0x1234 }) == DDERR_INVALIDPARAMS && f->out == 0);
    f->out = 0xDEADBEEFu;
    ok("CreatePalette(unknown flag bit) -> DDERR_INVALIDCAPS, out cleared",
       vcall(f->dd, DD_CREATEPALETTE, 5, (uint32_t[]){ DDPCAPS_8BIT | 0x80000000u, f->entries_va, hle_va(&f->out), 0 }) == DDERR_INVALIDCAPS && f->out == 0);
    f->out = 0xDEADBEEFu;
    ok("CreatePalette(no depth bit) -> DDERR_INVALIDPARAMS, out cleared",
       vcall(f->dd, DD_CREATEPALETTE, 5, (uint32_t[]){ DDPCAPS_ALLOW256, f->entries_va, hle_va(&f->out), 0 }) == DDERR_INVALIDPARAMS && f->out == 0);
    ok("GetEntries(DDPCAPS_8BITENTRIES) -> DDERR_INVALIDPARAMS (no byte array here)",
       vcall(f->pal, PAL_GETENTRIES, 5, (uint32_t[]){ 0x00000002u, 0, 1, f->fetched_va }) == DDERR_INVALIDPARAMS);
    ok("GetEntries(count=0) -> DDERR_INVALIDPARAMS",
       vcall(f->pal, PAL_GETENTRIES, 5, (uint32_t[]){ 0, 0, 0, f->fetched_va }) == DDERR_INVALIDPARAMS);
    ok("GetEntries(start=256,count=1) -> DDERR_INVALIDPARAMS",
       vcall(f->pal, PAL_GETENTRIES, 5, (uint32_t[]){ 0, 256, 1, f->fetched_va }) == DDERR_INVALIDPARAMS);
    ok("GetEntries(NULL entries) -> DDERR_INVALIDPARAMS",
       vcall(f->pal, PAL_GETENTRIES, 5, (uint32_t[]){ 0, 0, 1, 0 }) == DDERR_INVALIDPARAMS);
    ok("GetEntries(start=0xFFFFFFFF,count=2) cannot wrap into range",
       vcall(f->pal, PAL_GETENTRIES, 5, (uint32_t[]){ 0, 0xFFFFFFFFu, 2, f->fetched_va }) == DDERR_INVALIDPARAMS);
    ok("SetEntries(start=0xFFFFFFFF,count=2) cannot wrap into range",
       vcall(f->pal, PAL_SETENTRIES, 5, (uint32_t[]){ 0, 0xFFFFFFFFu, 2, f->change_va }) == DDERR_INVALIDPARAMS);
    ok("SetEntries(start=255,count=2) -> DDERR_INVALIDPARAMS",
       vcall(f->pal, PAL_SETENTRIES, 5, (uint32_t[]){ 0, 255, 2, f->change_va }) == DDERR_INVALIDPARAMS);
    /* Call the named shim directly for these: a wrong-type `this` has no
     * palette slot to look up, and an unmapped handle must not be dereferenced
     * even to find a vtable. */
    ok("GetEntries with a DirectDraw object as `this` -> DDERR_INVALIDPARAMS",
       mcall("IDirectDrawPalette::GetEntries", f->dd, 5, (uint32_t[]){ 0, 0, 1, f->fetched_va }) == DDERR_INVALIDPARAMS);
    ok("SetEntries with an unmapped handle as `this` -> DDERR_INVALIDPARAMS",
       mcall("IDirectDrawPalette::SetEntries", 0x1234u, 5, (uint32_t[]){ 0, 0, 1, f->change_va }) == DDERR_INVALIDPARAMS);
    ok("Release with an unmapped handle -> DDERR_INVALIDPARAMS",
       mcall("IDirectDrawPalette::Release", 0x1234u, 1, 0) == DDERR_INVALIDPARAMS);
    ok("the real palette is still alive after those handle errors", MEM32(f->pal + 4) == 1);
    memset(f->fetched, 0, sizeof f->fetched);
    vcall(f->pal, PAL_GETENTRIES, 5, (uint32_t[]){ 0, 0, 256, f->fetched_va });
    ok("no failed call modified any entry", memcmp(f->fetched, f->entries, sizeof f->entries) == 0);

    /* ---- shared presenter: an 8-bpp primary, a pixel, and a palette change ---- */
    f->rc = vcall(f->dd, DD_SETDISPLAYMODE, 4, (uint32_t[]){ 64, 48, 8 });
    ok("SetDisplayMode(64x48x8) -> DD_OK", f->rc == DD_OK);

    memset(f->desc, 0, sizeof f->desc);
    f->desc[0] = 108;                                    /* legacy DDSURFACEDESC */
    f->desc[1] = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT;
    f->desc[2] = 48; f->desc[3] = 64; f->desc[4] = 64;
    f->desc[18] = 32; f->desc[19] = DDPF_RGB | DDPF_PALETTEINDEXED8; f->desc[21] = 8;
    f->desc[26] = DDSCAPS_PRIMARYSURFACE;
    f->out = 0;
    f->rc = vcall(f->dd, 6, 4, (uint32_t[]){ hle_va(f->desc), hle_va(&f->out), 0 });
    ok("CreateSurface(8-bpp primary) -> DD_OK", f->rc == DD_OK && f->out != 0);
    if (!f->out) { mach_leave(); return 1; }
    f->primary = f->out;
    ok("hle_dd_frame exposes the 8-bpp primary",
       hle_dd_frame(&f->px, &f->fw, &f->fh, &f->fpitch, &f->fbpp, &f->palette, &f->seq) &&
       f->fw == 64 && f->fh == 48 && f->fbpp == 8);
    ok("the primary has no palette yet", f->palette == NULL);

    ok("SetPalette(palette) -> DD_OK",
       vcall(f->primary, SF_SETPALETTE, 2, (uint32_t[]){ f->pal }) == DD_OK);
    ok("SetPalette took its own reference", MEM32(f->pal + 4) == 2);
    f->out = 0;
    ok("GetPalette returns the same object and takes a reference",
       vcall(f->primary, SF_GETPALETTE, 2, (uint32_t[]){ hle_va(&f->out) }) == DD_OK && f->out == f->pal && MEM32(f->pal + 4) == 3);
    f->pal_from_surface = f->out;
    hle_dd_frame(&f->px, &f->fw, &f->fh, &f->fpitch, &f->fbpp, &f->palette, &f->seq);
    ok("hle_dd_frame now exposes the attached palette", f->palette != NULL && f->palette[7] == f->entries[7]);

    memset(f->lock, 0, sizeof f->lock);
    MEM32(f->lock_desc) = 128;                           /* DDSURFACEDESC2 shape */
    ok("Lock(primary) -> DD_OK",
       vcall(f->primary, SF_LOCK, 5, (uint32_t[]){ 0, f->lock_desc, 0, 0 }) == DD_OK);
    uint32_t surface_va = MEM32(f->lock_desc + 40);
    ok("Lock returned a usable lpSurface", surface_va != 0);
    MEM8(surface_va) = 7;                                /* index 7: the entry we will change */
    MEM8(surface_va + 1) = 9;                            /* index 9: a control entry */
    ok("Unlock(primary) -> DD_OK", vcall(f->primary, SF_UNLOCK, 1, 0) == DD_OK);

    hle_dd_frame(&f->px, &f->fw, &f->fh, &f->fpitch, &f->fbpp, &f->palette, &f->seq);
    f->seq_before = f->seq;
    uint32_t argb7_before = screen_argb(f->palette, 7), argb9_before = screen_argb(f->palette, 9);
    printf("[evidence] before: px[0]=%u px[1]=%u entry7=%08X entry9=%08X presenter7=%08X seq=%u\n",
           f->px[0], f->px[1], f->palette[7], f->palette[9], argb7_before, f->seq_before);
    ok("the primary holds the game's palette indices", f->px[0] == 7 && f->px[1] == 9);

    void (*old_hook)(void) = hle_dd_primary_hook;
    hle_dd_primary_hook = count_hook;
    hook_calls = 0;
    f->change[0] = pe(0x12, 0x34, 0x56);                 /* palette entry 7 */
    f->change[1] = pe(0xAA, 0xBB, 0xCC);                 /* palette entry 8 */
    f->change_va = hle_va(f->change);
    f->rc = vcall(f->pal, PAL_SETENTRIES, 5, (uint32_t[]){ 0, 7, 2, f->change_va });
    ok("SetEntries(7,2) -> DD_OK", f->rc == DD_OK);
    f->hook_after = hook_calls;
    hle_dd_primary_hook = old_hook;

    hle_dd_frame(&f->px, &f->fw, &f->fh, &f->fpitch, &f->fbpp, &f->palette, &f->seq);
    f->seq_after = f->seq;
    uint32_t argb7_after = screen_argb(f->palette, 7), argb9_after = screen_argb(f->palette, 9);
    printf("[evidence] after:  px[0]=%u px[1]=%u entry7=%08X entry9=%08X presenter7=%08X seq=%u hook=%u\n",
           f->px[0], f->px[1], f->palette[7], f->palette[9], argb7_after, f->seq_after, f->hook_after);
    ok("the two addressed entries changed", f->palette[7] == f->change[0] && f->palette[8] == f->change[1]);
    ok("entry 9 and the pixel indices did not change", f->palette[9] == f->entries[9] && f->px[0] == 7 && f->px[1] == 9);
    ok("the presenter's palette lookup changed colour", argb7_after != argb7_before && argb9_after == argb9_before);
    ok("SetEntries advanced the frame sequence", f->seq_after > f->seq_before);
    ok("SetEntries notified the shared presenter hook", f->hook_after == 1);

    /* Publish the primary through present.c's shared-surface path: the hook
     * sees the very bytes hle_dd_frame handed the presenter, and screen.c
     * combines that index with the palette we just changed. */
    rec_n = 0; rec_first = rec_pitch = rec_bpp = 0;
    void (*old_frame_hook)(const uint8_t *, int, int, int, int) = hle_screen_frame_hook;
    hle_screen_frame_hook = record_frame;
    hle_present_surface(f->px, f->fw, f->fh, f->fpitch, f->fbpp);
    hle_screen_frame_hook = old_frame_hook;
    ok("the shared presenter published one frame", rec_n == 1 && rec_bpp == 8 && rec_pitch == (uint32_t)f->fpitch);
    ok("that frame carries the palette index the game wrote", (rec_first & 0xFFu) == 7);
    printf("[evidence] presenter frame: first_index=%u raw=%08X palette_colour=%08X\n",
           rec_first & 0xFFu, rec_first, argb7_after);

    /* ---- COM lifetime: the surface held one reference via SetPalette ---- */
    {
        uint32_t one[1] = { 0 };
        ok("Release the GetPalette reference -> 2", vcall(f->pal, 2, 1, one) == 2 && MEM32(f->pal + 4) == 2);
        ok("AddRef -> 3", vcall(f->pal, 1, 1, one) == 3 && MEM32(f->pal + 4) == 3);
        ok("Release -> 2", vcall(f->pal, 2, 1, one) == 2 && MEM32(f->pal + 4) == 2);
        if (f->pal4) vcall(f->pal4, 2, 1, one);
        vcall(f->primary, 2, 1, one);                  /* freeing the surface drops its palette reference */
        ok("the surface's release leaves one palette reference", MEM32(f->pal + 4) == 1);
        vcall(f->pal, 2, 1, one);                      /* last release frees the object */
        vcall(f->dd, 2, 1, one);
    }
    ok("released palette handle is rejected, not dereferenced",
       mcall("IDirectDrawPalette::GetCaps", f->pal, 2, (uint32_t[]){ hle_va(&f->out) }) == DDERR_INVALIDPARAMS);

    mach_leave();
    printf("ddraw_palette_selftest: %s\n", fails ? "FAIL" : "all checks passed");
    return fails != 0;
}
