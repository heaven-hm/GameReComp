/* Focused regression for the SDL2 window/render shims (sdl2.c).
 *
 * Verifies, headlessly (SDL dummy video driver):
 *   - all 26 SDL2 imports resolve and are cdecl (the caller keeps its args);
 *   - a guest window maps onto the shared present path and its query APIs
 *     answer with real, validated handles;
 *   - renderer/texture/palette/readback round-trip through the shared frame
 *     sink (an explicit hle_screen_frame_hook records the published pixels);
 *   - SDL_RWFromConstMem builds a RWops the already-registered SDL_RWread
 *     shim can consume;
 *   - unsupported operations return documented SDL failures and set an error
 *     the guest's SDL_GetError shim reports.
 *
 * This is frontend coverage only: no claim about prboom-plus gameplay. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

#define ENTRY_VA 0x00401000u
static int fails;
static uint32_t va_cdecl;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
#define GUEST_PROLOGUE(va) \
    uint32_t _cf=0; uint32_t _flag_k=FK_NONE; uint32_t _flag_a=0,_flag_b=0; RECOMP_ENTER(va)

/* A lifted cdecl caller: push one arg, call SDL_SetRelativeMouseMode, then
 * check the callee left the argument for the caller (cdecl purge). */
static int cdecl_ok;
static void guest_cdecl(void) {
    GUEST_PROLOGUE(ENTRY_VA);
    uint32_t before = esp;
    PUSH32(esp, 0);
    RECOMP_ICALL(va_cdecl);
    cdecl_ok = esp == before - 4;             /* no argument word popped */
    esp += 4;                                 /* the lifted caller's add esp, 4 */
}
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { ENTRY_VA, guest_cdecl } };
const uint32_t recomp_dispatch_count = 1;

static uint32_t call(const char *name, int n, const uint32_t *a) {
    uint32_t fn = hle_resolve(name);
    return fn ? hle_call_guest(fn, n, a) : 0xFFFFFFFFu;
}
static uint32_t ansi(const char *s) {
    size_t n = strlen(s) + 1, va = hle_va(hle_alloc((uint32_t)n));
    memcpy(hle_ptr(va), s, n);
    return va;
}
static const char *guest_error(void) {
    uint32_t e = call("SDL_GetError", 0, NULL);
    return e ? (const char *)hle_ptr(e) : "";
}

/* ---- captured frame --------------------------------------------------- */
static int frame_n;
static uint32_t frame_first;
static int frame_w, frame_h, frame_bpp;
static void frame_hook(const uint8_t *px, int w, int h, int pitch, int bpp) {
    frame_n++;
    frame_w = w; frame_h = h; frame_bpp = bpp;
    if (bpp == 32 && pitch >= w * 4) frame_first = ((const uint32_t *)px)[0];
}

/* guest SDL2 layout (32-bit) */
enum { SF_FMT = 4, SF_W = 8, SF_H = 12, SF_PITCH = 16, SF_PIXELS = 20 };
enum { FMT_FORMAT = 0, FMT_BPP = 8, FMT_PALETTE = 4 };
enum { DM_W = 4, DM_H = 8 };

int main(void) {
    setenv("SDL_VIDEODRIVER", "dummy", 1);
    /* sdl12 first: it owns SDL_GetError and the SDL_RWread/seek/close shims
     * that the sdl2 RWops object is built for. */
    win32hle_register(win32hle_sdl12);
    win32hle_register(win32hle_sdl2);
    mach_enter();

    static const char *names[] = {
        "SDL_SetMainReady", "SDL_CreateWindow", "SDL_DestroyWindow",
        "SDL_GetWindowFlags", "SDL_GetWindowID", "SDL_GetWindowSurface",
        "SDL_SetWindowPosition", "SDL_SetWindowIcon", "SDL_SetRelativeMouseMode",
        "SDL_CreateRenderer", "SDL_DestroyRenderer", "SDL_RenderClear",
        "SDL_RenderCopy", "SDL_RenderPresent", "SDL_RenderReadPixels",
        "SDL_RenderSetLogicalSize", "SDL_CreateTexture", "SDL_CreateTextureFromSurface",
        "SDL_DestroyTexture", "SDL_SetRenderTarget", "SDL_SetPaletteColors",
        "SDL_RWFromConstMem", "SDL_GetCurrentDisplayMode", "SDL_GetDisplayMode",
        "SDL_GetNumDisplayModes", "SDL_GL_DeleteContext"
    };
    int resolved = 0;
    for (unsigned i = 0; i < sizeof names / sizeof *names; i++)
        if (hle_resolve(names[i])) resolved++;
    ok("all 26 SDL2 imports resolve", resolved == 26);

    /* cdecl stack contract, through generated-code macros. */
    va_cdecl = hle_resolve("SDL_SetRelativeMouseMode");
    hle_call_guest(ENTRY_VA, 0, NULL);
    ok("SDL2 shims are cdecl (caller keeps args)", cdecl_ok);

    /* ---- window ------------------------------------------------------- */
    hle_screen_frame_hook = frame_hook;
    uint32_t title = ansi("sdl2 selftest");
    uint32_t win = call("SDL_CreateWindow", 6, (uint32_t[]){ title, 0, 0, 8, 8, 0x4u /* SHOWN */ });
    ok("SDL_CreateWindow returns a handle", win != 0);
    ok("SDL_GetWindowFlags", (call("SDL_GetWindowFlags", 1, &win) & 0x4u) != 0);
    ok("SDL_GetWindowID", call("SDL_GetWindowID", 1, &win) == 1);
    uint32_t surf = call("SDL_GetWindowSurface", 1, &win);
    ok("SDL_GetWindowSurface returns a guest SDL_Surface", surf != 0 &&
       MEM32(surf + SF_W) == 8 && MEM32(surf + SF_PIXELS) != 0);
    call("SDL_SetWindowPosition", 3, (uint32_t[]){ win, 10, 20 });
    call("SDL_SetWindowIcon", 2, (uint32_t[]){ win, surf });
    ok("SDL_SetRelativeMouseMode", call("SDL_SetRelativeMouseMode", 1, (uint32_t[]){ 0 }) == 0);

    /* ---- renderer + texture ------------------------------------------ */
    uint32_t ren = call("SDL_CreateRenderer", 3, (uint32_t[]){ win, 0, 0 });
    ok("SDL_CreateRenderer returns a handle", ren != 0);
    ok("SDL_RenderSetLogicalSize", call("SDL_RenderSetLogicalSize", 3, (uint32_t[]){ ren, 8, 8 }) == 0);
    ok("SDL_RenderSetLogicalSize rejects bad size",
       call("SDL_RenderSetLogicalSize", 3, (uint32_t[]){ ren, 0, 8 }) == (uint32_t)-1);
    ok("error is visible via SDL_GetError", strstr(guest_error(), "invalid size") != NULL);
    call("SDL_ClearError", 0, NULL);

    uint32_t tex = call("SDL_CreateTexture", 5,
                        (uint32_t[]){ ren, SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_STREAMING, 8, 8 });
    ok("SDL_CreateTexture returns a handle", tex != 0);
    /* The window surface is a guest SDL_Surface; fill one pixel and clone it
     * into a texture, which exercises CreateTextureFromSurface and gives us a
     * known ARGB value to assert at presentation/readback. */
    uint32_t src = surf;
    uint32_t srcpx = MEM32(src + SF_PIXELS);
    MEM32(srcpx) = 0xFF102030u;
    uint32_t tex2 = call("SDL_CreateTextureFromSurface", 2, (uint32_t[]){ ren, src });
    ok("SDL_CreateTextureFromSurface", tex2 != 0);

    ok("SDL_SetRenderTarget default", call("SDL_SetRenderTarget", 2, (uint32_t[]){ ren, 0 }) == 0);
    ok("SDL_SetRenderTarget texture", call("SDL_SetRenderTarget", 2, (uint32_t[]){ ren, tex2 }) == 0);
    ok("SDL_SetRenderTarget rejects bad handle",
       call("SDL_SetRenderTarget", 2, (uint32_t[]){ ren, 0x999u }) == (uint32_t)-1);
    call("SDL_SetRenderTarget", 2, (uint32_t[]){ ren, 0 });

    /* A 1x1 copy of tex2 -> publish the known pixel to the frame sink. */
    uint32_t rect = hle_va(hle_alloc(16));
    MEM32(rect) = 0; MEM32(rect + 4) = 0; MEM32(rect + 8) = 1; MEM32(rect + 12) = 1;
    ok("SDL_RenderClear", call("SDL_RenderClear", 1, &ren) == 0);
    ok("SDL_RenderCopy", call("SDL_RenderCopy", 4, (uint32_t[]){ ren, tex2, rect, 0 }) == 0);
    uint32_t badrect = hle_va(hle_alloc(16));
    MEM32(badrect) = 0; MEM32(badrect + 4) = 0; MEM32(badrect + 8) = 99; MEM32(badrect + 12) = 99;
    ok("SDL_RenderCopy rejects out-of-range srcrect",
       call("SDL_RenderCopy", 4, (uint32_t[]){ ren, tex2, badrect, 0 }) == (uint32_t)-1);
    /* Restore a valid copy after the rejected one. */
    call("SDL_RenderCopy", 4, (uint32_t[]){ ren, tex2, rect, 0 });
    frame_n = 0;
    ok("SDL_RenderPresent", call("SDL_RenderPresent", 1, &ren) == 0);
    ok("RenderPresent published a frame to the shared sink", frame_n >= 1);
    ok("published frame geometry", frame_w == 1 && frame_h == 1 && frame_bpp == 32);
    ok("published pixel matches the texture", frame_first == 0xFF102030u);

    /* --- readback: same format works, foreign format fails honestly ---- */
    uint32_t out = hle_va(hle_alloc(8));
    ok("SDL_RenderReadPixels same-format",
       call("SDL_RenderReadPixels", 5, (uint32_t[]){ ren, rect, SDL_PIXELFORMAT_ARGB8888, out, 4 }) == 0);
    ok("readback returned the painted pixel", MEM32(out) == 0xFF102030u);
    ok("SDL_RenderReadPixels foreign-format fails",
       call("SDL_RenderReadPixels", 5, (uint32_t[]){ ren, rect, SDL_PIXELFORMAT_RGB565, out, 4 }) == (uint32_t)-1);
    ok("conversion error is visible", strstr(guest_error(), "unsupported") != NULL);
    call("SDL_ClearError", 0, NULL);

    /* --- paletted texture + SDL_SetPaletteColors ------------------------ */
    uint32_t paltex = call("SDL_CreateTexture", 5,
                          (uint32_t[]){ ren, SDL_PIXELFORMAT_INDEX8, SDL_TEXTUREACCESS_STREAMING, 4, 1 });
    ok("paletted texture created", paltex != 0);
    /* Palette lives inside the surface's pixel format struct; fetch it from a
     * fresh INDEX8 surface built by the shim's window-surface path is ARGB, so
     * use a real palette we allocate and point the texture at via SetPalette?
     * SDL 2.x has no direct "set texture palette" export; verify the API's
     * validation path against a guest SDL_Palette instead. */
    uint32_t pal = hle_va(hle_alloc(16));
    uint32_t colors = hle_va(hle_alloc(256 * 4));
    MEM32(pal + 0) = 2; MEM32(pal + 4) = colors; MEM32(pal + 8) = 0; MEM32(pal + 12) = 1;
    MEM32(colors + 0) = 0xFF112233u; MEM32(colors + 4) = 0xFF445566u;
    ok("SDL_SetPaletteColors", call("SDL_SetPaletteColors", 4,
       (uint32_t[]){ pal, colors, 0, 2 }) == 0);
    ok("SDL_SetPaletteColors rejects out-of-range",
       call("SDL_SetPaletteColors", 4, (uint32_t[]){ pal, colors, 1, 2 }) == (uint32_t)-1);

    /* --- RWops --------------------------------------------------------- */
    uint32_t blob = ansi("RWops-DATA");
    uint32_t rw = call("SDL_RWFromConstMem", 2, (uint32_t[]){ blob, 10 });
    ok("SDL_RWFromConstMem returns a guest RWops", rw != 0);
    uint32_t buf = hle_va(hle_alloc(16));
    memset(hle_ptr(buf), 0, 16);
    ok("existing SDL_RWread consumes the SDL2 RWops",
       call("SDL_RWread", 4, (uint32_t[]){ rw, buf, 1, 10 }) == 10);
    ok("RWops read the const data", memcmp(hle_ptr(buf), "RWops-DATA", 10) == 0);
    call("SDL_RWclose", 1, &rw);

    /* --- display modes ------------------------------------------------- */
    int nm = (int)call("SDL_GetNumDisplayModes", 1, (uint32_t[]){ 0 });
    ok("SDL_GetNumDisplayModes > 0", nm > 0);
    uint32_t mode = hle_va(hle_alloc(20));
    ok("SDL_GetDisplayMode(0)", call("SDL_GetDisplayMode", 3, (uint32_t[]){ 0, 0, mode }) == 0);
    ok("mode has dimensions", MEM32(mode + DM_W) > 0 && MEM32(mode + DM_H) > 0);
    ok("SDL_GetDisplayMode rejects bad index",
       call("SDL_GetDisplayMode", 3, (uint32_t[]){ 0, 9999, mode }) == (uint32_t)-1);
    ok("SDL_GetCurrentDisplayMode",
       call("SDL_GetCurrentDisplayMode", 2, (uint32_t[]){ 0, mode }) == 0);
    ok("SDL_GetNumDisplayModes rejects other displays",
       call("SDL_GetNumDisplayModes", 1, (uint32_t[]){ 1 }) == (uint32_t)-1);

    /* --- cleanup ------------------------------------------------------- */
    call("SDL_GL_DeleteContext", 1, (uint32_t[]){ 0 });   /* documented no-op */
    call("SDL_DestroyTexture", 1, &paltex);
    call("SDL_DestroyTexture", 1, &tex2);
    call("SDL_DestroyTexture", 1, &tex);
    call("SDL_DestroyRenderer", 1, &ren);
    call("SDL_DestroyWindow", 1, &win);
    ok("destroyed handle is no longer valid",
       call("SDL_GetWindowFlags", 1, &win) == 0);
    hle_screen_frame_hook = NULL;

    if (!fails) printf("sdl2_selftest: all checks passed\n");
    return fails != 0;
}
