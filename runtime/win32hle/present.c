/*
 * present.c - the optional SDL2 present/input layer for win32hle. It shows the
 * gdi32 framebuffer in a window and turns SDL input into WM_* messages posted
 * to the user32 queue. This is the ONE piece of win32hle that needs a display;
 * everything else is headless, which is why it lives on its own and user32 does
 * not depend on SDL. Link it when you want a visible window; leave it out for
 * headless/CI (--headless uses the SDL "dummy" video driver, which this still
 * drives without a screen).
 *
 * The framebuffer is drawn with the window surface (not a GL/renderer texture)
 * so the exact same path works under the dummy driver as on a real display.
 *
 * Every blit that reaches this layer is published to screen.c's frame sink
 * before it is drawn. That ordering matters: publishing does not depend on a
 * window surface being available (the dummy driver and headless runs still
 * record frames), and it is what turns a surface blit into observable frame
 * evidence for the screenshot recorder. Previously a blit only went to the
 * window surface, so a run could stay alive and write zero frames.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

/* A subset of the Win32 message ids SDL events map to. */
#define WM_QUIT         0x0012u
#define WM_KEYDOWN      0x0100u
#define WM_KEYUP        0x0101u
#define WM_MOUSEMOVE    0x0200u
#define WM_LBUTTONDOWN  0x0201u
#define WM_LBUTTONUP    0x0202u
#define WM_RBUTTONDOWN  0x0204u
#define WM_RBUTTONUP    0x0205u

/* screen.c owns the recorder hook; the shared header is deliberately left
 * untouched for this fix. */
extern void hle_screen_frame_publish(const uint8_t *px, int w, int h, int pitch, int bpp);

static SDL_Window  *g_win;
static SDL_Surface *g_winsurf;
static int          g_w, g_h;

int hle_present_open(const char *title, int w, int h) {
    SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");      /* SIGINT and SIGTERM end the process */
    if (SDL_Init(SDL_INIT_VIDEO) != 0) { fprintf(stderr, "[present] SDL_Init: %s\n", SDL_GetError()); return -1; }
    g_win = SDL_CreateWindow(title ? title : "win32hle",
                             SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                             w, h, SDL_WINDOW_SHOWN);
    if (!g_win) { fprintf(stderr, "[present] CreateWindow: %s\n", SDL_GetError()); return -1; }
    g_winsurf = SDL_GetWindowSurface(g_win);   /* NULL under some drivers; frame copes */
    g_w = w; g_h = h;
    return 0;
}

/* Blit a w*h ARGB8888 framebuffer to the window, scaled to the window size.
 * Separate from publication so an available window surface is never a
 * precondition for recording a frame. */
static void blit_argb(const uint32_t *fb, int w, int h) {
    if (!g_win || !fb || w <= 0 || h <= 0) return;
    if (!g_winsurf) g_winsurf = SDL_GetWindowSurface(g_win);
    if (!g_winsurf) return;
    SDL_Surface *src = SDL_CreateRGBSurfaceWithFormatFrom((void *)fb, w, h, 32,
                                                          (size_t)w * 4,
                                                          SDL_PIXELFORMAT_ARGB8888);
    if (src) { SDL_BlitScaled(src, NULL, g_winsurf, NULL); SDL_FreeSurface(src); }
    SDL_UpdateWindowSurface(g_win);
}

void hle_present_frame(const uint32_t *fb) {
    if (!fb) return;
    /* Record first: the framebuffer is g_w*g_h ARGB8888 (4 bytes/pixel). */
    hle_screen_frame_publish((const uint8_t *)fb, g_w, g_h, g_w * 4, 32);
    blit_argb(fb, g_w, g_h);
}

/* Present a raw guest surface blit of any common pixel format. The pixels are
 * published verbatim (so a recorder sees the game's real frame and pitch), and
 * an ARGB copy is drawn in the window. SDL 1.2's presenter and any other
 * surface-based path that needs a frame recorded should use this entry point. */
void hle_present_surface(const uint8_t *px, int w, int h, int pitch, int bpp) {
    if (!px || w <= 0 || h <= 0 || pitch <= 0) return;
    int bytes = bpp / 8;
    if (bytes < 1 || pitch < w * bytes) return;
    hle_screen_frame_publish(px, w, h, pitch, bpp);
    if (bpp == 32) { blit_argb((const uint32_t *)px, w, h); return; }

    static uint32_t *conv;
    static size_t conv_n;
    if (conv_n < (size_t)w * h) {
        free(conv);
        conv_n = (size_t)w * h;
        conv = (uint32_t *)malloc(conv_n * 4);
    }
    if (!conv) return;
    for (int y = 0; y < h; y++) {
        const uint8_t *src = px + (size_t)y * (size_t)pitch;
        uint32_t *dst = conv + (size_t)y * (size_t)w;
        for (int x = 0; x < w; x++) {
            uint32_t r = 0, g = 0, b = 0;
            if (bpp == 16) {
                uint16_t v = (uint16_t)(src[x * 2] | (src[x * 2 + 1] << 8));
                r = (uint32_t)((v >> 11) & 0x1F) * 255 / 31;
                g = (uint32_t)((v >> 5) & 0x3F) * 255 / 63;
                b = (uint32_t)(v & 0x1F) * 255 / 31;
            } else if (bpp == 24) {
                b = src[x * 3]; g = src[x * 3 + 1]; r = src[x * 3 + 2];
            } else {                                    /* 8 bpp: grayscale without a palette */
                r = g = b = src[x];
            }
            dst[x] = 0xFF000000u | (r << 16) | (g << 8) | b;
        }
    }
    blit_argb(conv, w, h);
}

static uint32_t mouse_lparam(int x, int y) { return ((uint32_t)(y & 0xFFFF) << 16) | (uint32_t)(x & 0xFFFF); }

/* Drain SDL's event queue, posting each as a WM_* message to the focused
 * window (the first one, for now). Call once per frame / message-loop turn. */
void hle_present_pump(void) {
    uint32_t hwnd = hle_first_hwnd();
    SDL_Event e;
    while (SDL_PollEvent(&e)) {
        switch (e.type) {
        case SDL_QUIT:
            hle_post_message(0, WM_QUIT, 0, 0);
            break;
        case SDL_KEYDOWN:
            hle_post_message(hwnd, WM_KEYDOWN, (uint32_t)e.key.keysym.sym, 0);
            break;
        case SDL_KEYUP:
            hle_post_message(hwnd, WM_KEYUP, (uint32_t)e.key.keysym.sym, 0);
            break;
        case SDL_MOUSEMOTION:
            hle_post_message(hwnd, WM_MOUSEMOVE, 0, mouse_lparam(e.motion.x, e.motion.y));
            break;
        case SDL_MOUSEBUTTONDOWN:
        case SDL_MOUSEBUTTONUP: {
            int down = e.type == SDL_MOUSEBUTTONDOWN;
            uint32_t msg = e.button.button == SDL_BUTTON_RIGHT
                         ? (down ? WM_RBUTTONDOWN : WM_RBUTTONUP)
                         : (down ? WM_LBUTTONDOWN : WM_LBUTTONUP);
            hle_post_message(hwnd, msg, 0, mouse_lparam(e.button.x, e.button.y));
            break;
        }
        default: break;
        }
    }
}

/* One turn of a guest message loop: collect input, show the current gdi32
 * frame, and yield briefly so an idle loop does not spin a core. Wired as
 * user32's pump hook by hle_present_enable, so a guest's own GetMessage/
 * PeekMessage loop drives the window with no change to the guest. */
void hle_present_step(void) {
    hle_present_pump();
    int w, h;
    const uint32_t *fb = hle_gdi_framebuffer(&w, &h);
    if (fb && w > 0 && h > 0) {
        /* No exact-size gate: whatever the guest drew is a frame, even when it
         * differs from the window size (previously such frames were dropped). */
        hle_screen_frame_publish((const uint8_t *)fb, w, h, w * 4, 32);
        blit_argb(fb, w, h);
    }
    SDL_Delay(1);
}
void hle_present_enable(void) { hle_set_pump_hook(hle_present_step); }

void hle_present_close(void) {
    hle_set_pump_hook(0);
    if (g_win) { SDL_DestroyWindow(g_win); g_win = NULL; g_winsurf = NULL; }
    SDL_Quit();
}
