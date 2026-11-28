/* Regression for the SDL 1.2 exports added beyond the original sdl12_selftest:
 * SDL_SetClipRect/SDL_GetClipRect, SDL_LowerBlit and SDL_FreeCursor.  As with
 * that test, every structure lives in guest memory at SDL-1.2.15 offsets and
 * every call goes through the cdecl shim dispatcher (the arguments must still
 * be on the guest stack when the shim returns). */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

#define ENTRY_VA 0x00402000u
enum { SF_FLAGS = 0, SF_FORMAT = 4, SF_W = 8, SF_H = 12, SF_PITCH = 16, SF_PIXELS = 20, SF_CLIP = 32 };
enum { FMT_COLORKEY = 32 };
enum { SRCCOLORKEY = 0x00001000 };
enum { SCREEN_W = 8, SCREEN_H = 8 };

static int fails;
static uint32_t va_setclip, va_flip, g_screen;
static int cdecl_ok;
/* screen.c's recorder hook sees every frame published by the present sink. */
static int frames;
static const uint8_t *frame_px;
static int frame_w, frame_h, frame_pitch, frame_bpp;
static void frame_hook(const uint8_t *px, int w, int h, int pitch, int bpp) {
    frames++; frame_px = px; frame_w = w; frame_h = h; frame_pitch = pitch; frame_bpp = bpp;
}
static void ok(const char *what, int cond) { if (!cond) { printf("FAIL %s\n", what); fails++; } }
static uint16_t u16(uint32_t p) { return (uint16_t)(MEM8(p) | ((uint16_t)MEM8(p + 1) << 8)); }
static void w16(uint32_t p, uint16_t v) { MEM8(p) = (uint8_t)v; MEM8(p + 1) = (uint8_t)(v >> 8); }
static uint32_t pixel(uint32_t s, int x, int y) {
    return MEM32(MEM32(s + SF_PIXELS) + (uint32_t)(y * u16(s + SF_PITCH) + x * 4));
}
static uint32_t fmt_va(uint32_t s) { return MEM32(s + SF_FORMAT); }
static uint32_t rect_va(int x, int y, int w, int h) {
    uint32_t r = hle_va(hle_alloc(8));
    w16(r, (uint16_t)x); w16(r + 2, (uint16_t)y);
    w16(r + 4, (uint16_t)w); w16(r + 6, (uint16_t)h);
    return r;
}
#define GUEST_PROLOGUE(va) \
    uint32_t _cf=0; uint32_t _flag_k=FK_NONE; uint32_t _flag_a=0,_flag_b=0; RECOMP_ENTER(va)

/* Push two arguments for SDL_SetClipRect(NULL, NULL): both must survive the
 * shim's RETC, proving the new exports keep SDL 1.2's cdecl ABI.  The same
 * guest call then flips the video surface, which is the path RRootage/Noiz2sa
 * use to present a frame. */
static void guest_cdecl(void) {
    GUEST_PROLOGUE(ENTRY_VA);
    uint32_t before = esp;
    PUSH32(esp, 0); PUSH32(esp, 0); RECOMP_ICALL(va_setclip);
    cdecl_ok = esp == before - 8;
    esp += 8;
    PUSH32(esp, g_screen); RECOMP_ICALL(va_flip);
    esp += 4;                              /* SDL_Flip is cdecl too */
}
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { ENTRY_VA, guest_cdecl } };
const uint32_t recomp_dispatch_count = 1;
static uint32_t call(const char *name, int n, const uint32_t *a) { return hle_call_guest(hle_resolve(name), n, a); }

int main(void) {
    setenv("SDL_VIDEODRIVER", "dummy", 1);
    win32hle_register(win32hle_sdl12);
    va_setclip = hle_resolve("SDL_SetClipRect");
    va_flip = hle_resolve("SDL_Flip");
    ok("new SDL exports resolve", va_setclip && hle_resolve("SDL_GetClipRect") &&
       hle_resolve("SDL_LowerBlit") && hle_resolve("SDL_FreeCursor") && va_flip);
    hle_call_guest(ENTRY_VA, 0, NULL);
    ok("SDL_SetClipRect is cdecl (caller keeps args)", cdecl_ok);
    ok("SDL_Init", call("SDL_Init", 1, (uint32_t[]){ SDL_INIT_VIDEO }) == 0);
    uint32_t screen = call("SDL_SetVideoMode", 4, (uint32_t[]){ SCREEN_W, SCREEN_H, 32, 0 });
    ok("SDL_SetVideoMode", screen != 0);

    /* One SDL_Flip (the SDL 1.2 present path) must publish exactly one frame,
     * with the guest framebuffer and the geometry of the video surface. */
    g_screen = screen;
    hle_screen_frame_hook = frame_hook;
    frames = 0;
    call("SDL_FillRect", 3, (uint32_t[]){ screen, 0, 0xFFA0B0C0u });
    hle_call_guest(ENTRY_VA, 0, NULL);
    hle_screen_frame_hook = NULL;
    ok("one SDL flip records one frame", frames == 1);
    ok("recorded frame is the guest framebuffer", frame_px == hle_ptr(MEM32(screen + SF_PIXELS)));
    ok("recorded frame geometry", frame_w == SCREEN_W && frame_h == SCREEN_H &&
       frame_pitch == SCREEN_W * 4 && frame_bpp == 32);
    ok("recorded frame has the drawn pixels", frame_px && MEM32(hle_va(frame_px)) == 0xFFA0B0C0u);

    /* --- clip rect: write the guest field, read it back, clamp it --- */
    uint32_t clip = rect_va(2, 2, 3, 3);
    uint32_t got = rect_va(-1, -1, -1, -1);
    call("SDL_FillRect", 3, (uint32_t[]){ screen, 0, 0xFF101010u });   /* whole surface first */
    ok("SDL_SetClipRect returns SDL_TRUE", call("SDL_SetClipRect", 2, (uint32_t[]){ screen, clip }) == 1);
    call("SDL_GetClipRect", 2, (uint32_t[]){ screen, got });
    ok("SDL_GetClipRect round-trip", u16(got) == 2 && u16(got + 2) == 2 && u16(got + 4) == 3 && u16(got + 6) == 3);

    call("SDL_FillRect", 3, (uint32_t[]){ screen, 0, 0xFFA0B0C0u });
    ok("SDL_FillRect honours clip_rect (inside)", pixel(screen, 2, 2) == 0xFFA0B0C0u && pixel(screen, 4, 4) == 0xFFA0B0C0u);
    ok("SDL_FillRect honours clip_rect (outside)", pixel(screen, 1, 1) == 0xFF101010u && pixel(screen, 5, 5) == 0xFF101010u);
    ok("SDL_SetClipRect clamps negative origin", call("SDL_SetClipRect", 2, (uint32_t[]){ screen, rect_va(-2, -2, 4, 4) }) == 1);
    call("SDL_GetClipRect", 2, (uint32_t[]){ screen, got });
    ok("SDL_GetClipRect sees clamped rect", u16(got) == 0 && u16(got + 2) == 0 && u16(got + 4) == 2 && u16(got + 6) == 2);
    ok("SDL_SetClipRect clamps past right edge", call("SDL_SetClipRect", 2, (uint32_t[]){ screen, rect_va(6, 6, 10, 10) }) == 1);
    call("SDL_GetClipRect", 2, (uint32_t[]){ screen, got });
    ok("SDL_GetClipRect sees edge clamp", u16(got) == 6 && u16(got + 2) == 6 && u16(got + 4) == 2 && u16(got + 6) == 2);
    ok("SDL_SetClipRect(NULL rect) resets", call("SDL_SetClipRect", 2, (uint32_t[]){ screen, 0 }) == 1);
    call("SDL_GetClipRect", 2, (uint32_t[]){ screen, got });
    ok("SDL_GetClipRect sees full surface", u16(got) == 0 && u16(got + 2) == 0 && u16(got + 4) == SCREEN_W && u16(got + 6) == SCREEN_H);
    ok("SDL_SetClipRect(NULL surface) fails", call("SDL_SetClipRect", 2, (uint32_t[]){ 0, clip }) == 0);
    call("SDL_GetClipRect", 2, (uint32_t[]){ 0, got });   /* must not fault */
    ok("SDL_FillRect(NULL surface) fails", call("SDL_FillRect", 3, (uint32_t[]){ 0, 0, 0 }) == (uint32_t)-1);

    /* --- LowerBlit: the raw half of the blit pair --- */
    uint32_t src = call("SDL_CreateRGBSurface", 8, (uint32_t[]){ 0, 2, 2, 32, 0x00FF0000u, 0x0000FF00u, 0x000000FFu, 0xFF000000u });
    uint32_t dst = call("SDL_CreateRGBSurface", 8, (uint32_t[]){ 0, 4, 4, 32, 0x00FF0000u, 0x0000FF00u, 0x000000FFu, 0xFF000000u });
    ok("LowerBlit surfaces", src != 0 && dst != 0);
    call("SDL_FillRect", 3, (uint32_t[]){ src, 0, 0xFF334455u });
    call("SDL_FillRect", 3, (uint32_t[]){ dst, 0, 0xFF000000u });
    uint32_t dr = rect_va(1, 1, 9, 9);
    ok("SDL_LowerBlit", call("SDL_LowerBlit", 4, (uint32_t[]){ src, 0, dst, dr }) == 0);
    ok("SDL_LowerBlit copied pixels", pixel(dst, 1, 1) == 0xFF334455u && pixel(dst, 2, 2) == 0xFF334455u);
    ok("SDL_LowerBlit untouched outside dstrect", pixel(dst, 0, 0) == 0xFF000000u && pixel(dst, 3, 3) == 0xFF000000u);
    ok("SDL_LowerBlit output size", u16(dr + 4) == 2 && u16(dr + 6) == 2);
    ok("SDL_LowerBlit clamps to dst bounds", call("SDL_LowerBlit", 4, (uint32_t[]){ src, 0, dst, rect_va(3, 3, 9, 9) }) == 0);
    uint32_t dr2 = rect_va(3, 3, 9, 9);
    ok("SDL_LowerBlit clamped size", call("SDL_LowerBlit", 4, (uint32_t[]){ src, 0, dst, dr2 }) == 0 && u16(dr2 + 4) == 1 && u16(dr2 + 6) == 1);
    ok("SDL_LowerBlit(NULL surface) fails", call("SDL_LowerBlit", 4, (uint32_t[]){ 0, 0, dst, dr }) == (uint32_t)-1);

    /* colour key is shared with UpperBlit; it must survive the refactor. */
    MEM32(fmt_va(src) + FMT_COLORKEY) = 0xFF334455u;
    MEM32(src + SF_FLAGS) |= SRCCOLORKEY;
    call("SDL_FillRect", 3, (uint32_t[]){ dst, 0, 0xFF000000u });
    call("SDL_LowerBlit", 4, (uint32_t[]){ src, 0, dst, rect_va(1, 1, 2, 2) });
    ok("SDL_LowerBlit honours colour key", pixel(dst, 1, 1) == 0xFF000000u);
    MEM32(src + SF_FLAGS) &= ~SRCCOLORKEY;
    uint32_t upper = rect_va(0, 0, 2, 2), lower = rect_va(1, 1, 9, 9);
    call("SDL_FillRect", 3, (uint32_t[]){ dst, 0, 0xFF000000u });
    ok("SDL_UpperBlit after refactor", call("SDL_UpperBlit", 4, (uint32_t[]){ src, upper, dst, lower }) == 0);
    ok("SDL_UpperBlit copied pixels", pixel(dst, 1, 1) == 0xFF334455u && pixel(dst, 2, 2) == 0xFF334455u);

    /* --- cursor lifetime --- */
    uint8_t *bits = hle_alloc(8);
    memset(bits, 0xFF, 8);
    uint32_t cursor = call("SDL_CreateCursor", 6, (uint32_t[]){ hle_va(bits), hle_va(bits), 8, 8, 0, 0 });
    ok("SDL_CreateCursor", cursor != 0);
    call("SDL_SetCursor", 1, &cursor);
    uint32_t current_va = hle_va(hle_alloc(4));     /* guest output slot */
    call("SDL_GetCursor", 1, &current_va);
    ok("SDL_GetCursor before free", MEM32(current_va) == cursor);
    call("SDL_FreeCursor", 1, &cursor);
    MEM32(current_va) = 1;
    call("SDL_GetCursor", 1, &current_va);
    ok("SDL_FreeCursor clears current cursor", MEM32(current_va) == 0);
    call("SDL_SetCursor", 1, &cursor);              /* stale handle: no-op */
    MEM32(current_va) = 1;
    call("SDL_GetCursor", 1, &current_va);
    ok("SDL_SetCursor rejects freed handle", MEM32(current_va) == 0);
    call("SDL_FreeCursor", 1, &cursor);             /* double free: no-op */
    call("SDL_FreeCursor", 1, (uint32_t[]){ 0 });   /* NULL: no-op */

    call("SDL_FreeSurface", 1, &src);
    call("SDL_FreeSurface", 1, &dst);
    call("SDL_Quit", 0, NULL);
    if (!fails) printf("sdl_more_selftest: all checks passed\n");
    return fails != 0;
}
