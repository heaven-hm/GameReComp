/*
 * present_selftest.c - exercises the SDL2 present/input layer headless, under
 * the SDL "dummy" video driver (no screen needed, works over RDP / in CI). It
 * opens a window, blits known surfaces, and checks that **every blit produces
 * a frame** at screen.c's recorder hook -- the regression that let SDL-based
 * titles stay alive for 12 seconds while writing zero screenshot frames. It
 * also pushes SDL input events, pumps them, and checks each turned into the
 * right WM_* message in the user32 queue.
 *
 * Forces SDL_VIDEODRIVER=dummy itself so the
 * check is deterministic without a display.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

/* host_lite's recomp_lookup wants the dispatch table the lifted program would
 * define; no guest runs here, so an empty one satisfies the link. */
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

/* present.c / screen.c entry points added to fix the frame path; the shared
 * header is deliberately left untouched. */
extern void hle_present_surface(const uint8_t *px, int w, int h, int pitch, int bpp);
extern void hle_screen_frame_publish(const uint8_t *px, int w, int h, int pitch, int bpp);

#define WM_QUIT      0x0012u
#define WM_KEYDOWN   0x0100u
#define WM_MOUSEMOVE 0x0200u

static int fails;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }

/* A recorder standing in for screenshot.c: count the frames and remember the
 * geometry and the leading pixel bytes of the last one. */
static int      rec_n;
static int      rec_w, rec_h, rec_pitch, rec_bpp;
static uint8_t  rec_first[8];
static void (*previous_hook)(const uint8_t *, int, int, int, int);

static void record(const uint8_t *px, int w, int h, int pitch, int bpp) {
    rec_n++;
    rec_w = w; rec_h = h; rec_pitch = pitch; rec_bpp = bpp;
    memset(rec_first, 0, sizeof rec_first);
    if (px) memcpy(rec_first, px, sizeof rec_first);
}
static void reset(void) { rec_n = 0; rec_w = rec_h = rec_pitch = rec_bpp = 0; memset(rec_first, 0, sizeof rec_first); }

int main(void) {
    setenv("SDL_VIDEODRIVER", "dummy", 1);     /* headless: no screen required */

    ok("present opens", hle_present_open("win32hle test", 8, 8) == 0);

    previous_hook = hle_screen_frame_hook;
    hle_screen_frame_hook = record;

    /* A surface blit must produce a frame, before any window-surface work. */
    static uint32_t fb[8 * 8];
    for (int i = 0; i < 64; i++) fb[i] = 0xFF000000u | (uint32_t)i;
    reset();
    hle_present_frame(fb);
    ok("ARGB blit -> one frame", rec_n == 1);
    ok("ARGB frame geometry", rec_w == 8 && rec_h == 8 && rec_pitch == 32 && rec_bpp == 32);
    ok("ARGB frame carries the blitted pixels",
       memcmp(rec_first, fb, sizeof rec_first) == 0);

    /* A raw 16-bpp surface blit is published verbatim (real pitch and bpp),
     * not converted away -- the recorder wants the game's frame. */
    static uint8_t px16[4 * 8];
    for (int i = 0; i < (int)sizeof px16; i++) px16[i] = (uint8_t)(i + 1);
    reset();
    hle_present_surface(px16, 4, 4, 8, 16);
    ok("16bpp surface blit -> one frame", rec_n == 1);
    ok("16bpp frame geometry", rec_w == 4 && rec_h == 4 && rec_pitch == 8 && rec_bpp == 16);
    ok("16bpp frame carries the blitted pixels",
       memcmp(rec_first, px16, sizeof rec_first) == 0);

    /* Invalid blits must not fabricate frames. */
    reset();
    hle_present_surface(NULL, 4, 4, 8, 16);
    hle_present_surface(px16, 4, 4, 4, 16);       /* pitch smaller than a row */
    hle_present_frame(NULL);
    ok("invalid blits produce no frame", rec_n == 0);

    /* A framebuffer whose size differs from the window is still a frame;
     * the old exact-size check silently dropped these. */
    reset();
    hle_gdi_set_target(fb, 4, 4);                 /* window is 8x8 */
    hle_present_step();
    hle_gdi_set_target(NULL, 0, 0);
    ok("mismatched-size framebuffer -> one frame", rec_n == 1);
    ok("mismatched-size geometry", rec_w == 4 && rec_h == 4 && rec_bpp == 32);

    /* The common sink records directly, with no window or primary required. */
    reset();
    hle_screen_frame_publish(px16, 3, 2, 6, 16);
    ok("generic publish -> one frame", rec_n == 1 && rec_w == 3 && rec_h == 2 && rec_bpp == 16);

    hle_screen_frame_hook = previous_hook;

    /* Push input and pump: a key, a mouse move, then quit */
    SDL_Event e;
    SDL_zero(e); e.type = SDL_KEYDOWN; e.key.keysym.sym = SDLK_a;              SDL_PushEvent(&e);
    SDL_zero(e); e.type = SDL_MOUSEMOTION; e.motion.x = 3; e.motion.y = 5;     SDL_PushEvent(&e);
    SDL_zero(e); e.type = SDL_QUIT;                                           SDL_PushEvent(&e);
    hle_present_pump();

    uint32_t msg, wp, lp;
    ok("key -> WM_KEYDOWN(a)", hle_msg_pop(&msg, &wp, &lp) && msg == WM_KEYDOWN && wp == (uint32_t)SDLK_a);
    ok("motion -> WM_MOUSEMOVE(3,5)", hle_msg_pop(&msg, &wp, &lp) && msg == WM_MOUSEMOVE && lp == ((5u << 16) | 3u));
    ok("quit -> WM_QUIT", hle_msg_pop(&msg, &wp, &lp) && msg == WM_QUIT);
    ok("queue now empty", hle_msg_pop(&msg, &wp, &lp) == 0);

    hle_present_close();
    if (fails == 0) printf("present_selftest: all checks passed\n");
    return fails != 0;
}
