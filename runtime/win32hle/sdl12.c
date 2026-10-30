/* SDL 1.2 imports implemented on SDL2 for lifted 32-bit games.
 *
 * SDL 1.2's Windows exports are cdecl, unlike the Win32 API.  Every shim in
 * this file therefore uses RETC: it pops only the synthetic return address and
 * leaves the caller's arguments for the lifted cdecl caller to remove.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <pthread.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

/* Guest-visible SDL 1.2 structures, as SDL-1.2.15's headers lay them out on a
 * 32-bit Windows build (the ABI the games were compiled against).  A lifted
 * game reads these fields itself, so the offsets are the ABI, not a private
 * choice:
 *
 *   SDL_Surface      flags 0, format 4, w 8 (int), h 12 (int), pitch 16
 *                    (Uint16), pixels 20, offset 24, hwdata 28, clip_rect 32
 *                    (4 x Sint16/Uint16), unused1 48, locked 52, map 56,
 *                    format_version 60, refcount 64           -- sizeof 68
 *   SDL_PixelFormat  palette 0, BitsPerPixel 4, BytesPerPixel 5, [RGBA]loss
 *                    6..9, [RGBA]shift 10..13, [RGBA]mask 16..28,
 *                    colorkey 32, alpha 36                    -- sizeof 40
 *   SDL_Palette      ncolors 0, colors 4                      -- sizeof 8
 *   SDL_Color        r 0, g 1, b 2, unused 3                  -- sizeof 4
 *   SDL_Rect         x 0, y 2, w 4, h 6 (Sint16 x/y, Uint16 w/h)
 *   SDL_keysym       scancode 0, sym 4, mod 8, unicode 12     -- sizeof 16
 *   SDL_KeyboardEvent type 0, which 1, state 2, keysym 4      -- sizeof 20
 *   SDL_MouseMotion  type 0, which 1, state 2, x 4, y 6, xrel 8, yrel 10
 *   SDL_MouseButton  type 0, which 1, button 2, state 3, x 4, y 6
 *   SDL_JoyAxisEvent type 0, which 1, axis 2, value 4 (Sint16)
 *   SDL_JoyBallEvent type 0, which 1, ball 2, xrel 4, yrel 6
 *   SDL_JoyHatEvent  type 0, which 1, hat 2, value 3
 *   SDL_JoyButton    type 0, which 1, button 2, state 3
 *   SDL_ResizeEvent  type 0, w 4, h 8
 *   SDL_UserEvent    type 0, code 4, data1 8, data2 12
 *   SDL_Event        a union of the above: 20 bytes
 *
 * (SDL 1.2's headers are the reference: include/SDL_video.h, SDL_events.h,
 * SDL_keyboard.h, SDL_audio.h, SDL_rwops.h in SDL-1.2.15.) */
enum { S12_SURFACE = 68, S12_FORMAT = 40, S12_PALETTE = 8, S12_COLOR = 4 };
enum { S12_SF_FLAGS = 0, S12_SF_FORMAT = 4, S12_SF_W = 8, S12_SF_H = 12,
       S12_SF_PITCH = 16, S12_SF_PIXELS = 20, S12_SF_CLIP = 32,
       S12_SF_UNUSED1 = 48, S12_SF_REFCOUNT = 64 };
enum { S12_FMT_PALETTE = 0, S12_FMT_BPP = 4, S12_FMT_BYTESPP = 5,
       S12_FMT_RLOSS = 6, S12_FMT_GLOSS = 7, S12_FMT_BLOSS = 8, S12_FMT_ALOSS = 9,
       S12_FMT_RSHIFT = 10, S12_FMT_GSHIFT = 11, S12_FMT_BSHIFT = 12, S12_FMT_ASHIFT = 13,
       S12_FMT_RMASK = 16, S12_FMT_GMASK = 20, S12_FMT_BMASK = 24,
       S12_FMT_AMASK = 28, S12_FMT_COLORKEY = 32, S12_FMT_ALPHA = 36 };
enum { S12_SWSURFACE = 0, S12_HWSURFACE = 1, S12_FULLSCREEN = 0x80000000u,
       S12_SRCCOLORKEY = 0x00001000, S12_SRCALPHA = 0x00010000,
       S12_OPENGL = 0x00000002 };
enum { S12_NOEVENT = 0, S12_ACTIVEEVENT = 1, S12_KEYDOWN = 2, S12_KEYUP = 3,
       S12_MOUSEMOTION = 4, S12_MOUSEBUTTONDOWN = 5, S12_MOUSEBUTTONUP = 6,
       S12_JOYAXISMOTION = 7, S12_JOYBALLMOTION = 8, S12_JOYHATMOTION = 9,
       S12_JOYBUTTONDOWN = 10, S12_JOYBUTTONUP = 11, S12_QUIT = 12,
       S12_SYSWMEVENT = 13, S12_VIDEORESIZE = 17, S12_VIDEOEXPOSE = 18,
       S12_USEREVENT = 24, S12_NUMEVENTS = 32 };
enum { S12_PRESSED = 1, S12_RELEASED = 0 };
enum { S12_EVENT_SIZE = 20 };
/* SDL_EventState/SDL_PeepEvents actions (SDL_events.h). */
enum { S12_QUERY = -1, S12_IGNORE = 0, S12_DISABLE = 0, S12_ENABLE = 1,
       S12_ADDEVENT = 0, S12_PEEKEVENT = 1, S12_GETEVENT = 2 };

static SDL_Window *g_window;
static SDL_Renderer *g_renderer;
static SDL_Texture *g_texture;
static SDL_GLContext g_glctx;
static uint32_t g_video;
static char g_error[256];

/* The GL attributes the guest set before SDL_SetVideoMode (SDL 1.2's SDL_GLattr
 * 0..15 are SDL2's own; 16 is SDL_GL_SWAP_CONTROL, which SDL 1.2 turns into a
 * swap-interval request rather than a context attribute). */
static int g_gl_attr[17];
static int g_gl_attr_set[17];
static int g_gl_swap_interval = -1;
static int g_gl_mode;

/* sdl12.c's OpenGL window and context, for gl32.c.  A host function pointer is
 * not callable from lifted code, so gl32.c resolves the gl* entry points
 * through SDL_GL_GetProcAddress (which this file answers with shim VAs) and
 * only needs the context to exist; these accessors let it check that. */
void *hle_sdl12_window(void) { return g_window; }
void *hle_sdl12_glcontext(void) { return g_glctx; }
int   hle_sdl12_gl_active(void) { return g_glctx != NULL; }
void  hle_sdl12_gl_swap(void) { if (g_glctx && g_window) SDL_GL_SwapWindow(g_window); }

static void error(const char *s) { snprintf(g_error, sizeof g_error, "%s", s ? s : "SDL error"); }
static void put16(uint32_t p, uint16_t v) { MEM8(p) = (uint8_t)v; MEM8(p + 1) = (uint8_t)(v >> 8); }
static uint16_t get16(uint32_t p) { return (uint16_t)(MEM8(p) | ((uint16_t)MEM8(p + 1) << 8)); }
static uint32_t sf_format(uint32_t s) { return MEM32(s + S12_SF_FORMAT); }
static int sf_w(uint32_t s) { return (int)MEM32(s + S12_SF_W); }
static int sf_h(uint32_t s) { return (int)MEM32(s + S12_SF_H); }
static int sf_pitch(uint32_t s) { return (int)get16(s + S12_SF_PITCH); }
static uint32_t sf_pixels(uint32_t s) { return MEM32(s + S12_SF_PIXELS); }
static int sf_bpp(uint32_t s) { return MEM8(sf_format(s) + S12_FMT_BPP); }
static int sf_bytespp(uint32_t s) { return MEM8(sf_format(s) + S12_FMT_BYTESPP); }
static uint32_t sf_flags(uint32_t s) { return MEM32(s + S12_SF_FLAGS); }

/* SDL_ReallocFormat's loss/shift derivation: a mask's shift is its lowest set
 * bit, its loss is 8 - popcount(mask).  SDL_MapRGB and SDL_GetRGB are defined
 * in terms of these, and a lifted game reads them too. */
static void set_mask_fields(uint32_t f, uint32_t rmask, uint32_t gmask, uint32_t bmask, uint32_t amask) {
    uint32_t masks[4] = { rmask, gmask, bmask, amask };
    const int loss_at[4] = { S12_FMT_RLOSS, S12_FMT_GLOSS, S12_FMT_BLOSS, S12_FMT_ALOSS };
    const int shift_at[4] = { S12_FMT_RSHIFT, S12_FMT_GSHIFT, S12_FMT_BSHIFT, S12_FMT_ASHIFT };
    for (int i = 0; i < 4; i++) {
        uint32_t m = masks[i];
        int shift = 0, bits = 0;
        if (m) { while (!(m & 1u)) { m >>= 1; shift++; } for (uint32_t b = m; b; b >>= 1) bits += (int)(b & 1u); }
        MEM8(f + loss_at[i]) = (uint8_t)(bits ? 8 - bits : 8);
        MEM8(f + shift_at[i]) = (uint8_t)shift;
    }
    MEM32(f + S12_FMT_RMASK) = rmask; MEM32(f + S12_FMT_GMASK) = gmask;
    MEM32(f + S12_FMT_BMASK) = bmask; MEM32(f + S12_FMT_AMASK) = amask;
}

static uint32_t new_surface(int w, int h, int bpp, uint32_t rmask, uint32_t gmask, uint32_t bmask, uint32_t amask, uint32_t pixels, int own_pixels) {
    if (w <= 0 || h <= 0 || (bpp != 8 && bpp != 16 && bpp != 24 && bpp != 32)) { error("unsupported SDL surface format"); return 0; }
    int pitch = (w * bpp + 7) / 8;
    if (!pixels) { pixels = hle_va(hle_alloc((uint32_t)(pitch * h))); own_pixels = 1; }
    uint32_t s = hle_va(hle_alloc(S12_SURFACE));
    uint32_t f = hle_va(hle_alloc(S12_FORMAT));
    if (!s || !f || !pixels) { error("out of guest memory"); return 0; }
    MEM32(s + S12_SF_FLAGS) = S12_SWSURFACE;
    MEM32(s + S12_SF_FORMAT) = f;
    MEM32(s + S12_SF_W) = (uint32_t)w;
    MEM32(s + S12_SF_H) = (uint32_t)h;
    put16(s + S12_SF_PITCH, (uint16_t)pitch);
    MEM32(s + S12_SF_PIXELS) = pixels;
    put16(s + S12_SF_CLIP, 0); put16(s + S12_SF_CLIP + 2, 0);
    put16(s + S12_SF_CLIP + 4, (uint16_t)w); put16(s + S12_SF_CLIP + 6, (uint16_t)h);
    MEM32(s + S12_SF_UNUSED1) = own_pixels ? 1u : 0u;   /* unused1: ownership for this shim */
    MEM32(s + S12_SF_REFCOUNT) = 1;
    MEM8(f + S12_FMT_BPP) = (uint8_t)bpp; MEM8(f + S12_FMT_BYTESPP) = (uint8_t)(bpp / 8);
    MEM8(f + S12_FMT_ALPHA) = 255;                       /* SDL_ALPHA_OPAQUE, as SDL_AllocFormat sets */
    if (bpp == 8) {
        /* "If the depth is 4 or 8 bits, an empty palette is allocated" (SDL_video.h). */
        uint32_t pal = hle_va(hle_alloc(S12_PALETTE));
        uint32_t colors = hle_va(hle_alloc(256 * S12_COLOR));
        if (!pal || !colors) { error("out of guest memory"); return 0; }
        MEM32(pal) = 256; MEM32(pal + 4) = colors;
        MEM32(f + S12_FMT_PALETTE) = pal;
        set_mask_fields(f, 0, 0, 0, 0);
    } else {
        set_mask_fields(f, rmask, gmask, bmask, amask);
    }
    return s;
}

static uint32_t pixel_read(uint32_t s, int x, int y) {
    uint32_t p = sf_pixels(s) + (uint32_t)(y * sf_pitch(s) + x * sf_bytespp(s));
    if (sf_bytespp(s) == 1) return MEM8(p);
    if (sf_bytespp(s) == 2) return get16(p);
    if (sf_bytespp(s) == 3) return MEM8(p) | ((uint32_t)MEM8(p + 1) << 8) | ((uint32_t)MEM8(p + 2) << 16);
    return MEM32(p);
}
static void pixel_write(uint32_t s, int x, int y, uint32_t v) {
    uint32_t p = sf_pixels(s) + (uint32_t)(y * sf_pitch(s) + x * sf_bytespp(s));
    if (sf_bytespp(s) == 1) MEM8(p) = (uint8_t)v;
    else if (sf_bytespp(s) == 2) put16(p, (uint16_t)v);
    else if (sf_bytespp(s) == 3) MEM8(p) = (uint8_t)v, MEM8(p + 1) = (uint8_t)(v >> 8), MEM8(p + 2) = (uint8_t)(v >> 16);
    else MEM32(p) = v;
}
/* SDL_Rect: Sint16 x, y; Uint16 w, h. */
static void rect(uint32_t r, int *x, int *y, int *w, int *h, uint32_t s) {
    *x = 0, *y = 0, *w = sf_w(s), *h = sf_h(s);
    if (r) { *x = (int)(int16_t)get16(r); *y = (int)(int16_t)get16(r + 2); *w = (int)get16(r + 4); *h = (int)get16(r + 6); }
    /* clip to the surface's clip_rect, then to the surface (SDL_SetClipRect) */
    int cx = (int)(int16_t)get16(s + S12_SF_CLIP), cy = (int)(int16_t)get16(s + S12_SF_CLIP + 2);
    int cw = (int)get16(s + S12_SF_CLIP + 4), ch = (int)get16(s + S12_SF_CLIP + 6);
    if (*x < cx) *w += *x - cx, *x = cx;
    if (*y < cy) *h += *y - cy, *y = cy;
    if (*x + *w > cx + cw) *w = cx + cw - *x;
    if (*y + *h > cy + ch) *h = cy + ch - *y;
    if (*w < 0) *w = 0; if (*h < 0) *h = 0;
}
/* Pixel-format helpers, defined with the converters below. */
static uint32_t map_rgba(uint32_t f, int r, int g, int b, int a);
static void get_rgba(uint32_t f, uint32_t pixel, int *r, int *g, int *b, int *a);
/* Keyboard translation, defined with the input shims below (put_event uses it). */
static int s12_key(SDL_Keycode k);
static uint16_t unicode_of(SDL_Keycode k, SDL_Keymod mod);
static void keystate_note(int sym, int down);
static int g_unicode;

static uint32_t *g_conv; static int g_conv_w, g_conv_h;
static void present(void) {
    if (!g_video || !g_renderer || !g_texture) return;
    int w = sf_w(g_video), h = sf_h(g_video);
    if (sf_bpp(g_video) == 8) {
        /* An 8-bit screen is shown through its palette: the texture is ARGB8888. */
        uint32_t pal = MEM32(sf_format(g_video) + S12_FMT_PALETTE);
        uint32_t colors = pal ? MEM32(pal + 4) : 0;
        if (!colors) return;
        if (!g_conv || g_conv_w != w || g_conv_h != h) { free(g_conv); g_conv = (uint32_t *)malloc((size_t)w * h * 4); g_conv_w = w; g_conv_h = h; }
        if (!g_conv) return;
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++) {
                uint32_t i = pixel_read(g_video, x, y) & 0xFFu;
                g_conv[y * w + x] = 0xFF000000u | ((uint32_t)MEM8(colors + i * 4) << 16)
                                  | ((uint32_t)MEM8(colors + i * 4 + 1) << 8) | MEM8(colors + i * 4 + 2);
            }
        if (SDL_UpdateTexture(g_texture, NULL, g_conv, w * 4) != 0) { error(SDL_GetError()); return; }
    } else if (SDL_UpdateTexture(g_texture, NULL, hle_ptr(sf_pixels(g_video)), sf_pitch(g_video)) != 0) {
        error(SDL_GetError()); return;
    }
    SDL_RenderClear(g_renderer); SDL_RenderCopy(g_renderer, g_texture, NULL, NULL); SDL_RenderPresent(g_renderer);
}

static void s_Init(void) { int rc = SDL_Init(A32(0)); if (rc) error(SDL_GetError()); RETC(rc); }
static void s_InitSubSystem(void) { int rc = SDL_InitSubSystem(A32(0)); if (rc) error(SDL_GetError()); RETC(rc); }
static void close_video(void);
static void s_Quit(void) { close_video(); SDL_Quit(); RETCV(); }
static void s_QuitSubSystem(void) { SDL_QuitSubSystem(A32(0)); RETCV(); }
static void s_WasInit(void) { RETC(SDL_WasInit(A32(0))); }
static void s_GetError(void) { RETC(hle_guest_strdup(g_error[0] ? g_error : SDL_GetError())); }
static void s_ClearError(void) { g_error[0] = 0; SDL_ClearError(); RETCV(); }
static void s_GetTicks(void) { RETC(hle_clock_ms()); }
/* SDL_Delay must let other guest threads run: the machine is given up around
 * the sleep (hle_block_begin/end), as kernel32's Sleep does. */
static void s_Delay(void) {
    uint32_t ms = A32(0);
    hle_block_begin();
    SDL_Delay(hle_clock_virtual() ? 0u : ms);   /* virtual time passes by itself */
    hle_block_end();
    RETCV();
}

/* Drop whatever the last mode created. */
static void close_video(void) {
    if (g_texture) SDL_DestroyTexture(g_texture), g_texture = NULL;
    if (g_renderer) SDL_DestroyRenderer(g_renderer), g_renderer = NULL;
    if (g_glctx) SDL_GL_DeleteContext(g_glctx), g_glctx = NULL;
    if (g_window) SDL_DestroyWindow(g_window), g_window = NULL;
    g_gl_mode = 0;
}
/* SDL_GL_SetAttribute's effect on the next SDL_SetVideoMode: 0..15 are context
 * attributes SDL2 also knows, 16 (SDL_GL_SWAP_CONTROL) is a swap-interval wish. */
static void apply_gl_attrs(void) {
    for (int a = 0; a <= 15; a++) if (g_gl_attr_set[a]) SDL_GL_SetAttribute((SDL_GLattr)a, g_gl_attr[a]);
}

static void s_SetVideoMode(void) {
    int w = (int)A32(0), h = (int)A32(1), bpp = (int)A32(2);
    uint32_t flags = A32(3);
    int want_gl = (flags & S12_OPENGL) != 0;
    if (!SDL_WasInit(SDL_INIT_VIDEO) && SDL_InitSubSystem(SDL_INIT_VIDEO) != 0) { error(SDL_GetError()); RETC(0); }
    close_video();
    if (want_gl) {
        /* SDL_OPENGL: the guest draws with gl*, so the window is an OpenGL one
         * and there is a context; no software renderer or texture is made. */
        apply_gl_attrs();
        g_window = SDL_CreateWindow("SDL application", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, w, h,
                                    SDL_WINDOW_OPENGL | ((flags & S12_FULLSCREEN) ? SDL_WINDOW_FULLSCREEN_DESKTOP : 0));
        if (!g_window) { error(SDL_GetError()); RETC(0); }
        g_glctx = SDL_GL_CreateContext(g_window);
        if (!g_glctx) { error(SDL_GetError()); close_video(); RETC(0); }
        if (g_gl_swap_interval >= 0) SDL_GL_SetSwapInterval(g_gl_swap_interval);
        g_gl_mode = 1;
    } else {
        g_window = SDL_CreateWindow("SDL application", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, w, h,
                                    (flags & S12_FULLSCREEN) ? SDL_WINDOW_FULLSCREEN_DESKTOP : 0);
        if (!g_window) { error(SDL_GetError()); RETC(0); }
        g_renderer = SDL_CreateRenderer(g_window, -1, SDL_RENDERER_SOFTWARE);
        if (!g_renderer) { error(SDL_GetError()); RETC(0); }
        uint32_t fmt = bpp == 16 ? SDL_PIXELFORMAT_RGB565 : SDL_PIXELFORMAT_ARGB8888;
        g_texture = SDL_CreateTexture(g_renderer, fmt, SDL_TEXTUREACCESS_STREAMING, w, h);
        if (!g_texture) { error(SDL_GetError()); RETC(0); }
    }
    /* SDL 1.2 returns a surface for the mode either way; a GL one carries the
     * SDL_OPENGL flag and its pixels are not what is on screen. */
    g_video = new_surface(w, h, bpp == 16 ? 16 : 32, bpp == 16 ? 0xF800u : 0x00FF0000u,
                          bpp == 16 ? 0x07E0u : 0x0000FF00u, bpp == 16 ? 0x001Fu : 0x000000FFu, bpp == 16 ? 0 : 0xFF000000u, 0, 1);
    if (g_video) MEM32(g_video + S12_SF_FLAGS) = S12_SWSURFACE | (flags & (S12_FULLSCREEN | S12_OPENGL));
    RETC(g_video);
}
/* In a GL mode SDL_Flip does nothing: the guest swaps with SDL_GL_SwapBuffers
 * (SDL 1.2 only blits for SDL_OPENGLBLIT, which we do not offer). */
static void s_Flip(void) { if (!g_gl_mode && A32(0) == g_video) present(); RETC(0); }
/* ---- timers, threads and synchronisation ----
 * SDL2's SDL_mutex/SDL_sem/SDL_cond/SDL_Thread/SDL_TimerID are host pointers, so
 * the guest gets handles in guest memory and one table maps them back.  The
 * objects themselves are SDL2's, so the locking and waiting really happen: a
 * guest SDL_CondWait blocks the thread that called it, and a guest thread runs
 * lifted code through hle_call_guest (which takes the machine lock). */
enum { HS_MUTEX = 1, HS_SEM, HS_COND, HS_THREAD, HS_TIMER, HS_MAX = 64 };
typedef struct { uint32_t va; void *obj; int kind; } hs_slot;
static hs_slot g_hs[HS_MAX];
static uint32_t hs_new(int kind, void *obj) {
    for (int i = 0; i < HS_MAX; i++)
        if (!g_hs[i].va) {
            uint32_t va = hle_va(hle_alloc(8));
            if (!va) return 0;
            MEM32(va) = (uint32_t)kind;
            g_hs[i].va = va; g_hs[i].obj = obj; g_hs[i].kind = kind;
            return va;
        }
    return 0;
}
static void *hs_of(uint32_t va, int kind) {
    if (!va) return NULL;
    for (int i = 0; i < HS_MAX; i++) if (g_hs[i].va == va && g_hs[i].kind == kind) return g_hs[i].obj;
    return NULL;
}
static void hs_del(uint32_t va, int kind) {
    for (int i = 0; i < HS_MAX; i++) if (g_hs[i].va == va && g_hs[i].kind == kind) { g_hs[i].va = 0; g_hs[i].obj = NULL; g_hs[i].kind = 0; }
}

/* ---- timers ---- */
struct timer_arg { uint32_t cb, param, which; };         /* which: 1 = SDL_SetTimer, 2 = SDL_AddTimer */
static struct timer_arg g_settimer;                     /* the one SDL_SetTimer installs */
static uint32_t g_settimer_cb_id;                       /* its SDL2 timer id, so a new call replaces it */
static Uint32 timer_tramp(Uint32 interval, void *p) {
    struct timer_arg *a = (struct timer_arg *)p;
    if (!a->cb) return 0;
    if (a->which == 1) {                                 /* SDL_TimerCallback(Uint32) -> next interval */
        uint32_t args[1] = { interval };
        return (Uint32)hle_call_guest(a->cb, 1, args);
    }
    uint32_t args[2] = { interval, a->param };           /* SDL_NewTimerCallback(Uint32, void *) */
    return (Uint32)hle_call_guest(a->cb, 2, args);
}
static void s_AddTimer(void) {                           /* (interval, callback, param) */
    uint32_t cb = A32(1);
    if (!cb) { error("SDL_AddTimer: NULL callback"); RETC(0); }
    struct timer_arg *a = (struct timer_arg *)calloc(1, sizeof *a);
    if (!a) RETC(0);
    a->cb = cb; a->param = A32(2); a->which = 2;
    SDL_TimerID id = SDL_AddTimer((Uint32)A32(0), timer_tramp, a);
    if (!id) { free(a); error(SDL_GetError()); RETC(0); }
    uint32_t h = hs_new(HS_TIMER, a);
    if (!h) { SDL_RemoveTimer(id); free(a); RETC(0); }
    a->which = 2;
    MEM32(h + 4) = (uint32_t)(uintptr_t)id;              /* the SDL2 timer id, for RemoveTimer */
    RETC(h);
}
static void s_RemoveTimer(void) {                        /* (SDL_TimerID) */
    uint32_t h = A32(0);
    struct timer_arg *a = (struct timer_arg *)hs_of(h, HS_TIMER);
    if (!a) RETC(0);                                     /* SDL_FALSE: not ours */
    SDL_TimerID id = (SDL_TimerID)(uintptr_t)MEM32(h + 4);
    SDL_RemoveTimer(id);
    hs_del(h, HS_TIMER);
    free(a);
    RETC(1);                                             /* SDL_TRUE */
}
static void s_SetTimer(void) {                           /* (interval, SDL_TimerCallback) */
    if (g_settimer.which == 1 && g_settimer.cb) {        /* replace the previous one */
        if (g_settimer_cb_id) SDL_RemoveTimer((SDL_TimerID)(uintptr_t)g_settimer_cb_id);
        g_settimer_cb_id = 0;
    }
    g_settimer.cb = 0;
    uint32_t interval = A32(0), cb = A32(1);
    if (!interval || !cb) RETC(0);                       /* interval 0 stops it: success */
    g_settimer.cb = cb; g_settimer.param = 0; g_settimer.which = 1;
    SDL_TimerID id = SDL_AddTimer(interval, timer_tramp, &g_settimer);
    if (!id) { error(SDL_GetError()); g_settimer.cb = 0; RETC(-1); }
    g_settimer_cb_id = (uint32_t)(uintptr_t)id;
    RETC(0);
}

/* ---- threads ----
 * SDL 1.2 on Win32 takes four arguments (SDL_thread.h's
 * SDL_PASSED_BEGINTHREAD_ENDTHREAD): the entry point, its data, and the CRT's
 * begin/end thread functions, which are not used here. */
struct thread_arg2 { uint32_t fn, data, tid; int done, status; };
static void *thread_tramp(void *p) {
    struct thread_arg2 *a = (struct thread_arg2 *)p;
    hle_thread_id_adopt(a->tid);
    uint32_t args[1] = { a->data };
    a->status = (int)hle_call_guest(a->fn, 1, args);
    a->done = 1;
    return NULL;
}
static void s_CreateThread(void) {                       /* (fn, data, pfnBegin, pfnEnd) */
    uint32_t fn = A32(0);
    if (!fn) { error("SDL_CreateThread: NULL function"); RETC(0); }
    struct thread_arg2 *a = (struct thread_arg2 *)calloc(1, sizeof *a);
    if (!a) RETC(0);
    a->fn = fn; a->data = A32(1); a->tid = hle_thread_id_new();
    pthread_t t;
    if (pthread_create(&t, NULL, thread_tramp, a) != 0) { free(a); error("SDL_CreateThread: cannot start a thread"); RETC(0); }
    uint32_t h = hs_new(HS_THREAD, a);
    if (!h) { pthread_detach(t); free(a); RETC(0); }
    pthread_detach(t);
    RETC(h);
}
static void s_WaitThread(void) {                         /* (thread, int *status) */
    uint32_t h = A32(0), out = A32(1);
    struct thread_arg2 *a = (struct thread_arg2 *)hs_of(h, HS_THREAD);
    if (!a) RETCV();
    /* The guest thread runs lifted code and needs the machine, so this wait
     * gives it up (hle_block_begin/end) - otherwise waiting for a thread that
     * can never run is a deadlock.  No guest state is touched until it is
     * taken back. */
    hle_block_begin();
    while (!a->done) SDL_Delay(1);
    hle_block_end();
    if (out) MEM32(out) = (uint32_t)a->status;
    hs_del(h, HS_THREAD);
    free(a);
    RETCV();
}
static void s_ThreadID(void) { RETC(hle_thread_id()); }
static void s_GetThreadID(void) {
    struct thread_arg2 *a = (struct thread_arg2 *)hs_of(A32(0), HS_THREAD);
    RETC(a ? a->tid : 0);
}

/* ---- mutexes, semaphores and condition variables ---- */
static void s_CreateMutex(void) {
    SDL_mutex *m = SDL_CreateMutex();
    if (!m) { error(SDL_GetError()); RETC(0); }
    uint32_t h = hs_new(HS_MUTEX, m);
    if (!h) { SDL_DestroyMutex(m); RETC(0); }
    RETC(h);
}
static void s_LockMutex(void) {
    SDL_mutex *m = (SDL_mutex *)hs_of(A32(0), HS_MUTEX);
    if (!m) RETC(-1);
    RETC(SDL_LockMutex(m));
}
static void s_UnlockMutex(void) {
    SDL_mutex *m = (SDL_mutex *)hs_of(A32(0), HS_MUTEX);
    if (!m) RETC(-1);
    RETC(SDL_UnlockMutex(m));
}
static void s_DestroyMutex(void) {
    uint32_t h = A32(0);
    SDL_mutex *m = (SDL_mutex *)hs_of(h, HS_MUTEX);
    if (!m) RETCV();
    SDL_DestroyMutex(m);
    hs_del(h, HS_MUTEX);
    RETCV();
}
static void s_CreateSemaphore(void) {
    SDL_sem *sem = SDL_CreateSemaphore((Uint32)A32(0));
    if (!sem) { error(SDL_GetError()); RETC(0); }
    uint32_t h = hs_new(HS_SEM, sem);
    if (!h) { SDL_DestroySemaphore(sem); RETC(0); }
    RETC(h);
}
static void s_DestroySemaphore(void) {
    uint32_t h = A32(0);
    SDL_sem *sem = (SDL_sem *)hs_of(h, HS_SEM);
    if (!sem) RETCV();
    SDL_DestroySemaphore(sem);
    hs_del(h, HS_SEM);
    RETCV();
}
static void s_SemWait(void) {
    SDL_sem *sem = (SDL_sem *)hs_of(A32(0), HS_SEM);
    if (!sem) RETC(-1);
    hle_block_begin();
    int rc = SDL_SemWait(sem);
    hle_block_end();
    RETC(rc);
}
static void s_SemTryWait(void)      { SDL_sem *s = (SDL_sem *)hs_of(A32(0), HS_SEM); RETC(s ? (uint32_t)SDL_SemTryWait(s) : (uint32_t)-1); }
static void s_SemWaitTimeout(void) {
    SDL_sem *sem = (SDL_sem *)hs_of(A32(0), HS_SEM);
    uint32_t ms = A32(1);
    if (!sem) RETC(-1);
    hle_block_begin();
    int rc = SDL_SemWaitTimeout(sem, ms);
    hle_block_end();
    RETC(rc);
}
static void s_SemPost(void)         { SDL_sem *s = (SDL_sem *)hs_of(A32(0), HS_SEM); RETC(s ? (uint32_t)SDL_SemPost(s) : (uint32_t)-1); }
static void s_SemValue(void)        { SDL_sem *s = (SDL_sem *)hs_of(A32(0), HS_SEM); RETC(s ? SDL_SemValue(s) : 0u); }
static void s_CreateCond(void) {
    SDL_cond *c = SDL_CreateCond();
    if (!c) { error(SDL_GetError()); RETC(0); }
    uint32_t h = hs_new(HS_COND, c);
    if (!h) { SDL_DestroyCond(c); RETC(0); }
    RETC(h);
}
static void s_DestroyCond(void) {
    uint32_t h = A32(0);
    SDL_cond *c = (SDL_cond *)hs_of(h, HS_COND);
    if (!c) RETCV();
    SDL_DestroyCond(c);
    hs_del(h, HS_COND);
    RETCV();
}
static void s_CondSignal(void)    { SDL_cond *c = (SDL_cond *)hs_of(A32(0), HS_COND); RETC(c ? (uint32_t)SDL_CondSignal(c) : (uint32_t)-1); }
static void s_CondBroadcast(void) { SDL_cond *c = (SDL_cond *)hs_of(A32(0), HS_COND); RETC(c ? (uint32_t)SDL_CondBroadcast(c) : (uint32_t)-1); }
static void s_CondWait(void) {
    SDL_cond *c = (SDL_cond *)hs_of(A32(0), HS_COND);
    SDL_mutex *m = (SDL_mutex *)hs_of(A32(1), HS_MUTEX);
    if (!c || !m) RETC(-1);
    hle_block_begin();                                   /* the signaller is another guest thread */
    int rc = SDL_CondWait(c, m);
    hle_block_end();
    RETC(rc);
}
static void s_CondWaitTimeout(void) {
    SDL_cond *c = (SDL_cond *)hs_of(A32(0), HS_COND);
    SDL_mutex *m = (SDL_mutex *)hs_of(A32(1), HS_MUTEX);
    uint32_t ms = A32(2);
    if (!c || !m) RETC(-1);
    hle_block_begin();
    int rc = SDL_CondWaitTimeout(c, m, ms);
    hle_block_end();
    RETC(rc);
}

/* ---- SDL 1.2's OpenGL API ----
 * SDL_GL_GetProcAddress is the one that needs care: a host function pointer is
 * not callable from lifted code, so it answers with the VA of our own gl* shim
 * when we have one (an indirect call to a shim VA goes through
 * recomp_lookup_import) and 0 - "not available" - when we do not, naming the
 * symbol on stderr once.  A guest that checks for NULL sees the truth. */
static void s_GL_SetAttribute(void) {                    /* (attr, value) */
    int attr = (int)A32(0), value = (int)A32(1);
    if (attr < 0 || attr > 16) { error("SDL_GL_SetAttribute: unknown attribute"); RETC(-1); }
    g_gl_attr[attr] = value;
    g_gl_attr_set[attr] = 1;
    if (attr == 16) {                                    /* SDL_GL_SWAP_CONTROL */
        g_gl_swap_interval = value;
        if (g_glctx && SDL_GL_SetSwapInterval(value) != 0) { error(SDL_GetError()); RETC(-1); }
    }
    RETC(0);
}
static void s_GL_GetAttribute(void) {                    /* (attr, int *value) */
    int attr = (int)A32(0);
    uint32_t out = A32(1);
    if (attr < 0 || attr > 16 || !out) { error("SDL_GL_GetAttribute: bad argument"); RETC(-1); }
    if (attr == 16) { MEM32(out) = (uint32_t)(g_gl_swap_interval < 0 ? 0 : g_gl_swap_interval); RETC(0); }
    if (!g_gl_attr_set[attr]) { error("SDL_GL_GetAttribute: no such attribute set"); RETC(-1); }
    int value = 0;
    if (g_glctx && SDL_GL_GetAttribute((SDL_GLattr)attr, &value) != 0) { error(SDL_GetError()); RETC(-1); }
    else if (!g_glctx) value = g_gl_attr[attr];          /* before a mode: what was asked for */
    MEM32(out) = (uint32_t)value;
    RETC(0);
}
static void s_GL_GetProcAddress(void) {                  /* (const char *proc) */
    const char *name = ASTR(0);
    if (!name || !*name) { error("SDL_GL_GetProcAddress: NULL name"); RETC(0); }
    uint32_t va = hle_resolve(name);
    if (va) RETC(va);
    fprintf(stderr, "[sdl] SDL_GL_GetProcAddress: no shim for %s (returning NULL)\n", name);
    RETC(0);
}
static void s_GL_SwapBuffers(void) { hle_sdl12_gl_swap(); RETCV(); }
static void s_GL_LoadLibrary(void) {                     /* (const char *path) */
    if (SDL_GL_LoadLibrary(A32(0) ? ASTR(0) : NULL) != 0) { error(SDL_GetError()); RETC(-1); }
    RETC(0);
}
static void s_GL_UnloadLibrary(void) { SDL_GL_UnloadLibrary(); RETCV(); }
static void s_GL_SetSwapInterval(void) {                 /* not SDL 1.2, but harmless */
    g_gl_swap_interval = (int)A32(0);
    RETC(g_glctx ? SDL_GL_SetSwapInterval((int)A32(0)) : 0);
}
static void s_GL_GetSwapInterval(void) {
    RETC(g_glctx ? SDL_GL_GetSwapInterval() : (g_gl_swap_interval < 0 ? 0 : g_gl_swap_interval));
}
static void s_GetVideoSurface(void) { RETC(g_video); }
static void s_UpdateRect(void) { if (A32(0) == g_video) present(); RETCV(); }
static void s_UpdateRects(void) { if (A32(0) == g_video) present(); RETCV(); }
static void s_CreateRGBSurface(void) { RETC(new_surface((int)A32(1), (int)A32(2), (int)A32(3), A32(4), A32(5), A32(6), A32(7), 0, 1)); }
static void s_CreateRGBSurfaceFrom(void) {
    uint32_t s = new_surface((int)A32(1), (int)A32(2), (int)A32(3), A32(5), A32(6), A32(7), A32(8), A32(0), 0);
    if (s) put16(s + 12, (uint16_t)A32(4));
    RETC(s);
}
static void s_FreeSurface(void) { uint32_t s = A32(0); if (s) { if (MEM32(s + S12_SF_UNUSED1)) hle_free(hle_ptr(sf_pixels(s))); hle_free(hle_ptr(sf_format(s))); hle_free(hle_ptr(s)); } RETCV(); }
static void s_LockSurface(void) { RETC(A32(0) ? 0 : -1); }
static void s_UnlockSurface(void) { RETCV(); }
static void s_FillRect(void) { int x,y,w,h; uint32_t s=A32(0); if (!s) RETC(-1); rect(A32(1),&x,&y,&w,&h,s); for(int j=0;j<h;j++) for(int i=0;i<w;i++) pixel_write(s,x+i,y+j,A32(2)); RETC(0); }
/* SDL_UpperBlit's rectangle rules: the source rectangle is clipped to the
 * source surface, the destination takes its SIZE from that source rectangle
 * (not from dstrect->w/h, which SDL treats as an output), and the pair is then
 * clipped to the destination's clip_rect, moving the source window with it. */
static void blit_rects(uint32_t srcrect, uint32_t dstrect, uint32_t src, uint32_t dst,
                       int *sx, int *sy, int *sw, int *sh, int *dx, int *dy) {
    rect(srcrect, sx, sy, sw, sh, src);
    *dx = dstrect ? (int)(int16_t)get16(dstrect) : 0;
    *dy = dstrect ? (int)(int16_t)get16(dstrect + 2) : 0;
    int cx = (int)(int16_t)get16(dst + S12_SF_CLIP), cy = (int)(int16_t)get16(dst + S12_SF_CLIP + 2);
    int cw = (int)get16(dst + S12_SF_CLIP + 4), ch = (int)get16(dst + S12_SF_CLIP + 6);
    if (*dx < cx) { *sx += cx - *dx; *sw -= cx - *dx; *dx = cx; }
    if (*dy < cy) { *sy += cy - *dy; *sh -= cy - *dy; *dy = cy; }
    if (*dx + *sw > cx + cw) *sw = cx + cw - *dx;
    if (*dy + *sh > cy + ch) *sh = cy + ch - *dy;
    if (*sw < 0) *sw = 0; if (*sh < 0) *sh = 0;
}

/* SDL_UpperBlit: clip both rects, then copy.  SDL 1.2's blit rules
 * (SDL_video.h): a source colour key skips matching pixels (comparing RGB,
 * ignoring alpha); SDL_SRCALPHA blends with the per-pixel alpha when the source
 * has an alpha channel, else with the per-surface alpha. */
static void s_BlitSurface(void) {
    int sx, sy, sw, sh, dx, dy;
    uint32_t src = A32(0), dst = A32(2);
    if (!src || !dst) RETC(-1);
    blit_rects(A32(1), A32(3), src, dst, &sx, &sy, &sw, &sh, &dx, &dy);
    uint32_t sf = sf_format(src), df = sf_format(dst);
    int ckey_on = (sf_flags(src) & S12_SRCCOLORKEY) != 0;
    uint32_t ckey = MEM32(sf + S12_FMT_COLORKEY);
    int alpha_on = (sf_flags(src) & S12_SRCALPHA) != 0;
    int per_pixel_alpha = MEM32(sf + S12_FMT_AMASK) != 0;
    for (int y = 0; y < sh; y++)
        for (int x = 0; x < sw; x++) {
            int r, g, b, a;
            get_rgba(sf, pixel_read(src, sx + x, sy + y), &r, &g, &b, &a);
            if (ckey_on) {
                int kr, kg, kb, ka;
                get_rgba(sf, ckey, &kr, &kg, &kb, &ka);
                if (r == kr && g == kg && b == kb) continue;
            }
            uint32_t dst_pix = pixel_read(dst, dx + x, dy + y);
            uint32_t out;
            if (alpha_on) {
                /* "SDL_SRCALPHA set: alpha-blend (using the source per-surface
                 * alpha value)" for an RGB source, "(using the source alpha
                 * channel)" for an RGBA one (SDL_video.h). */
                int sa = per_pixel_alpha ? a : MEM8(sf + S12_FMT_ALPHA);
                if (sa < 255) {
                    int dr, dg, db, da;
                    get_rgba(df, dst_pix, &dr, &dg, &db, &da);
                    r = (r * sa + dr * (255 - sa)) / 255;
                    g = (g * sa + dg * (255 - sa)) / 255;
                    b = (b * sa + db * (255 - sa)) / 255;
                }
                out = map_rgba(df, r, g, b, 255);
                if (MEM32(df + S12_FMT_AMASK) && per_pixel_alpha)
                    out = (out & ~MEM32(df + S12_FMT_AMASK)) | (dst_pix & MEM32(df + S12_FMT_AMASK));   /* dest alpha untouched */
            } else {
                /* No SDL_SRCALPHA: copy RGB; an RGB source onto an RGBA
                 * destination takes the source's per-surface alpha. */
                out = map_rgba(df, r, g, b, per_pixel_alpha ? a : MEM8(sf + S12_FMT_ALPHA));
            }
            pixel_write(dst, dx + x, dy + y, out);
        }
    /* "The final blit rectangles are saved in srcrect and dstrect" (SDL_video.h). */
    if (A32(1)) { put16(A32(1), (uint16_t)sx); put16(A32(1) + 2, (uint16_t)sy); put16(A32(1) + 4, (uint16_t)sw); put16(A32(1) + 6, (uint16_t)sh); }
    if (A32(3)) { put16(A32(3), (uint16_t)dx); put16(A32(3) + 2, (uint16_t)dy); put16(A32(3) + 4, (uint16_t)sw); put16(A32(3) + 6, (uint16_t)sh); }
    RETC(0);
}
static void s_UpperBlit(void) { s_BlitSurface(); }

/* ---- pixel formats: SDL_MapRGB/SDL_GetRGB and the surface converters ----
 * SDL 1.2 defines a pixel's channels by mask + shift + loss (SDL_video.c's
 * SDL_ReallocFormat); SDL_MapRGB packs with the loss/shift, SDL_GetRGB unpacks
 * with the loss table.  A palette surface goes through its SDL_Palette. */
static int expand_loss(int v, int loss) {
    int max = (1 << (8 - loss)) - 1;
    return max ? (v * 255) / max : v;
}
static int channel_of(uint32_t pixel, uint32_t f, int shift_at, int loss_at) {
    int shift = MEM8(f + shift_at), loss = MEM8(f + loss_at);
    uint32_t mask = (loss >= 8) ? 0u : (uint32_t)((1u << (8 - loss)) - 1u) << shift;
    return expand_loss((int)((pixel & mask) >> shift), loss);
}
static int find_color(uint32_t f, int r, int g, int b) {
    uint32_t pal = MEM32(f + S12_FMT_PALETTE);
    if (!pal) return 0;
    int n = (int)MEM32(pal);
    uint32_t colors = MEM32(pal + 4);
    if (!colors || n <= 0) return 0;
    int best = 0; long bestd = -1;
    for (int i = 0; i < n && i < 256; i++) {
        int cr = MEM8(colors + (uint32_t)(i * 4)), cg = MEM8(colors + (uint32_t)(i * 4 + 1)), cb = MEM8(colors + (uint32_t)(i * 4 + 2));
        long d = (long)(cr - r) * (cr - r) + (long)(cg - g) * (cg - g) + (long)(cb - b) * (cb - b);
        if (d == 0) return i;
        if (bestd < 0 || d < bestd) bestd = d, best = i;
    }
    return best;
}
static uint32_t map_rgba(uint32_t f, int r, int g, int b, int a) {
    if (MEM32(f + S12_FMT_PALETTE)) return (uint32_t)find_color(f, r, g, b);
    uint32_t px = ((uint32_t)(r >> MEM8(f + S12_FMT_RLOSS)) << MEM8(f + S12_FMT_RSHIFT))
                | ((uint32_t)(g >> MEM8(f + S12_FMT_GLOSS)) << MEM8(f + S12_FMT_GSHIFT))
                | ((uint32_t)(b >> MEM8(f + S12_FMT_BLOSS)) << MEM8(f + S12_FMT_BSHIFT));
    if (MEM32(f + S12_FMT_AMASK)) px |= ((uint32_t)(a >> MEM8(f + S12_FMT_ALOSS)) << MEM8(f + S12_FMT_ASHIFT));
    else px |= MEM32(f + S12_FMT_AMASK);
    return px;
}
static void get_rgba(uint32_t f, uint32_t pixel, int *r, int *g, int *b, int *a) {
    if (MEM32(f + S12_FMT_PALETTE)) {
        uint32_t pal = MEM32(f + S12_FMT_PALETTE);
        int n = (int)MEM32(pal);
        uint32_t colors = MEM32(pal + 4);
        if (colors && (int)pixel < n) {
            *r = MEM8(colors + pixel * 4); *g = MEM8(colors + pixel * 4 + 1);
            *b = MEM8(colors + pixel * 4 + 2); *a = 255;
        } else *r = *g = *b = 0, *a = 255;
        return;
    }
    *r = channel_of(pixel, f, S12_FMT_RSHIFT, S12_FMT_RLOSS);
    *g = channel_of(pixel, f, S12_FMT_GSHIFT, S12_FMT_GLOSS);
    *b = channel_of(pixel, f, S12_FMT_BSHIFT, S12_FMT_BLOSS);
    *a = MEM32(f + S12_FMT_AMASK) ? channel_of(pixel, f, S12_FMT_ASHIFT, S12_FMT_ALOSS) : 255;
}

/* A new surface with the same shape as src, in the given format (or src's). */
static uint32_t surface_like(uint32_t src, int bpp, uint32_t rmask, uint32_t gmask, uint32_t bmask, uint32_t amask) {
    uint32_t sf = sf_format(src);
    if (!bpp) {
        bpp = MEM8(sf + S12_FMT_BPP);
        rmask = MEM32(sf + S12_FMT_RMASK); gmask = MEM32(sf + S12_FMT_GMASK);
        bmask = MEM32(sf + S12_FMT_BMASK); amask = MEM32(sf + S12_FMT_AMASK);
    }
    uint32_t dst = new_surface(sf_w(src), sf_h(src), bpp, rmask, gmask, bmask, amask, 0, 1);
    if (!dst) return 0;
    if (bpp == 8 && MEM32(sf + S12_FMT_PALETTE)) {          /* carry the palette over */
        uint32_t sp = MEM32(sf + S12_FMT_PALETTE), dp = MEM32(sf_format(dst) + S12_FMT_PALETTE);
        if (sp && dp) memcpy(hle_ptr(MEM32(dp + 4)), hle_ptr(MEM32(sp + 4)), 256 * 4);
    }
    return dst;
}
/* SDL_ConvertSurface's pixel loop, via RGBA so any format pair works. */
static void convert_pixels(uint32_t src, uint32_t dst) {
    int w = sf_w(src) < sf_w(dst) ? sf_w(src) : sf_w(dst);
    int h = sf_h(src) < sf_h(dst) ? sf_h(src) : sf_h(dst);
    uint32_t df = sf_format(dst);
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            int r, g, b, a;
            get_rgba(sf_format(src), pixel_read(src, x, y), &r, &g, &b, &a);
            pixel_write(dst, x, y, map_rgba(df, r, g, b, a));
        }
}
/* SDL_ConvertSurface copies the source's transparency state onto the copy. */
static void copy_transparency(uint32_t src, uint32_t dst) {
    uint32_t sf = sf_format(src), df = sf_format(dst);
    if (sf_flags(src) & S12_SRCCOLORKEY) {
        MEM32(dst + S12_SF_FLAGS) |= S12_SRCCOLORKEY;
        MEM32(df + S12_FMT_COLORKEY) = MEM32(sf + S12_FMT_COLORKEY);
    }
    if (sf_flags(src) & S12_SRCALPHA) {
        MEM32(dst + S12_SF_FLAGS) |= S12_SRCALPHA;
        MEM8(df + S12_FMT_ALPHA) = MEM8(sf + S12_FMT_ALPHA);
    }
}
static uint32_t display_format(uint32_t src, int want_alpha) {
    uint32_t vf = g_video ? sf_format(g_video) : 0;
    int bpp = vf ? MEM8(vf + S12_FMT_BPP) : 0;
    uint32_t rmask = vf ? MEM32(vf + S12_FMT_RMASK) : 0, gmask = vf ? MEM32(vf + S12_FMT_GMASK) : 0;
    uint32_t bmask = vf ? MEM32(vf + S12_FMT_BMASK) : 0, amask = vf ? MEM32(vf + S12_FMT_AMASK) : 0;
    if (want_alpha) { bpp = 32; rmask = 0x00FF0000u; gmask = 0x0000FF00u; bmask = 0x000000FFu; amask = 0xFF000000u; }
    if (bpp == 8 || (bpp && MEM32(sf_format(src) + S12_FMT_PALETTE))) { bpp = 0; }   /* keep the source's depth */
    uint32_t dst = surface_like(src, bpp, rmask, gmask, bmask, amask);
    if (!dst) return 0;
    convert_pixels(src, dst);
    copy_transparency(src, dst);
    return dst;
}

/* ---- SDL_RWops ----
 * SDL 1.2's SDL_RWops is a guest structure whose four function pointers the
 * guest may call itself (SDL_RWread and friends are macros over them).  We
 * allocate it in guest memory and point those fields at our own shims, so both
 * the macros and SDL's own loaders work.  The private tail is ours:
 *   kind 0: HANDLE is a host file descriptor; kind 1: HANDLE is a guest VA of
 *   a memory buffer of SIZE bytes; POS is the cursor; OWN says whether the
 *   close shim must release the descriptor / buffer. */
enum { S12_RWOPS = 44, S12_RW_SEEK = 0, S12_RW_READ = 4, S12_RW_WRITE = 8, S12_RW_CLOSE = 12,
       S12_RW_TYPE = 16, S12_RW_KIND = 20, S12_RW_HANDLE = 24, S12_RW_SIZE = 28, S12_RW_POS = 32, S12_RW_OWN = 36 };
enum { S12_RWOPS_UNKNOWN = 0, S12_RWOPS_WINFILE = 1, S12_RWOPS_STDFILE = 2, S12_RWOPS_MEMORY = 3, S12_RWOPS_MEMORY_RO = 4 };
enum { S12_RW_KIND_FILE = 0, S12_RW_KIND_MEM = 1 };
enum { S12_SEEK_SET = 0, S12_SEEK_CUR = 1, S12_SEEK_END = 2 };

static uint32_t new_rwops(int kind, int handle, long size, int own) {
    uint32_t rw = hle_va(hle_alloc(S12_RWOPS));
    if (!rw) { error("out of guest memory"); return 0; }
    MEM32(rw + S12_RW_SEEK) = hle_resolve("SDL_RWseek");
    MEM32(rw + S12_RW_READ) = hle_resolve("SDL_RWread");
    MEM32(rw + S12_RW_WRITE) = hle_resolve("SDL_RWwrite");
    MEM32(rw + S12_RW_CLOSE) = hle_resolve("SDL_RWclose");
    MEM32(rw + S12_RW_TYPE) = kind == S12_RW_KIND_MEM ? S12_RWOPS_MEMORY : S12_RWOPS_STDFILE;
    MEM32(rw + S12_RW_KIND) = (uint32_t)kind;
    MEM32(rw + S12_RW_HANDLE) = (uint32_t)handle;
    MEM32(rw + S12_RW_SIZE) = (uint32_t)size;
    MEM32(rw + S12_RW_POS) = 0;
    MEM32(rw + S12_RW_OWN) = (uint32_t)own;
    return rw;
}
static int rw_seek(uint32_t rw, int offset, int whence) {
    long pos;
    if (MEM32(rw + S12_RW_KIND) == S12_RW_KIND_MEM) {
        long size = (long)MEM32(rw + S12_RW_SIZE);
        pos = whence == S12_SEEK_SET ? offset : whence == S12_SEEK_CUR ? (long)MEM32(rw + S12_RW_POS) + offset : size + offset;
        if (pos < 0 || pos > size) return -1;
    } else {
        int fd = (int)MEM32(rw + S12_RW_HANDLE);
        pos = lseek(fd, (off_t)offset, whence == S12_SEEK_SET ? SEEK_SET : whence == S12_SEEK_CUR ? SEEK_CUR : SEEK_END);
        if (pos < 0) return -1;
    }
    MEM32(rw + S12_RW_POS) = (uint32_t)pos;
    return (int)pos;
}
static int rw_read(uint32_t rw, uint32_t ptr, int size, int maxnum) {
    if (size <= 0 || maxnum <= 0) return 0;
    long want = (long)size * maxnum;
    if (MEM32(rw + S12_RW_KIND) == S12_RW_KIND_MEM) {
        long pos = (long)MEM32(rw + S12_RW_POS), avail = (long)MEM32(rw + S12_RW_SIZE) - pos;
        if (avail <= 0) return 0;
        if (want > avail) want = avail;
        memcpy(hle_ptr(ptr), hle_ptr(MEM32(rw + S12_RW_HANDLE) + (uint32_t)pos), (size_t)want);
        MEM32(rw + S12_RW_POS) = (uint32_t)(pos + want);
    } else {
        ssize_t n = read((int)MEM32(rw + S12_RW_HANDLE), hle_ptr(ptr), (size_t)want);
        if (n <= 0) return n < 0 ? -1 : 0;
        want = n;
        MEM32(rw + S12_RW_POS) += (uint32_t)want;
    }
    return (int)(want / size);
}
static int rw_write(uint32_t rw, uint32_t ptr, int size, int num) {
    if (size <= 0 || num <= 0) return 0;
    long want = (long)size * num;
    if (MEM32(rw + S12_RW_KIND) == S12_RW_KIND_MEM) {
        long pos = (long)MEM32(rw + S12_RW_POS), size_left = (long)MEM32(rw + S12_RW_SIZE) - pos;
        if (want > size_left) return -1;                 /* would overrun the buffer */
        memcpy(hle_ptr(MEM32(rw + S12_RW_HANDLE) + (uint32_t)pos), hle_ptr(ptr), (size_t)want);
        MEM32(rw + S12_RW_POS) = (uint32_t)(pos + want);
    } else {
        ssize_t n = write((int)MEM32(rw + S12_RW_HANDLE), hle_ptr(ptr), (size_t)want);
        if (n < 0) return -1;
        want = n;
        MEM32(rw + S12_RW_POS) += (uint32_t)want;
    }
    return (int)(want / size);
}
static int rw_close(uint32_t rw) {
    if (!rw) return -1;
    if (MEM32(rw + S12_RW_KIND) == S12_RW_KIND_MEM) {
        if (MEM32(rw + S12_RW_OWN) && MEM32(rw + S12_RW_HANDLE)) hle_free(hle_ptr(MEM32(rw + S12_RW_HANDLE)));
    } else if (MEM32(rw + S12_RW_OWN)) {
        close((int)MEM32(rw + S12_RW_HANDLE));
    }
    hle_free(hle_ptr(rw));
    return 0;
}
/* Translate one SDL2 event into the guest's SDL 1.2 SDL_Event (20 bytes). The
 * guest reads this with SDL 1.2's offsets, which is why they are spelled out. */
static void put16h(uint8_t *o, uint16_t v) { o[0] = (uint8_t)v; o[1] = (uint8_t)(v >> 8); }
static void event_from_sdl2(uint8_t *out, const SDL_Event *e) {
    memset(out, 0, S12_EVENT_SIZE);
    switch (e->type) {
    case SDL_QUIT:
        out[0] = S12_QUIT;
        break;
    case SDL_KEYDOWN:
    case SDL_KEYUP: {
        SDL_Keymod mod = SDL_GetModState();
        int sym = s12_key(e->key.keysym.sym);
        keystate_note(sym, e->type == SDL_KEYDOWN);
        out[0] = (uint8_t)(e->type == SDL_KEYDOWN ? S12_KEYDOWN : S12_KEYUP);
        out[1] = 0;                       /* which: the keyboard device */
        out[2] = e->key.state;            /* SDL_PRESSED / SDL_RELEASED */
        out[4] = (uint8_t)e->key.keysym.scancode;   /* SDL2's scancode: USB usage, not SDL 1.2's set 1 */
        put16h(out + 8, (uint16_t)sym);
        put16h(out + 12, (uint16_t)mod);
        put16h(out + 16, g_unicode ? unicode_of(e->key.keysym.sym, mod) : 0);
        break;
    }
    case SDL_MOUSEMOTION:
        out[0] = S12_MOUSEMOTION;
        out[1] = 0;
        out[2] = e->motion.state;
        put16h(out + 4, (uint16_t)e->motion.x); put16h(out + 6, (uint16_t)e->motion.y);
        put16h(out + 8, (uint16_t)e->motion.xrel); put16h(out + 10, (uint16_t)e->motion.yrel);
        break;
    case SDL_MOUSEBUTTONDOWN:
    case SDL_MOUSEBUTTONUP:
        out[0] = (uint8_t)(e->type == SDL_MOUSEBUTTONDOWN ? S12_MOUSEBUTTONDOWN : S12_MOUSEBUTTONUP);
        out[1] = 0;
        out[2] = e->button.button;
        out[3] = e->button.state;
        put16h(out + 4, (uint16_t)e->button.x); put16h(out + 6, (uint16_t)e->button.y);
        break;
    case SDL_JOYAXISMOTION:
        out[0] = S12_JOYAXISMOTION;
        out[1] = e->jaxis.which; out[2] = e->jaxis.axis;
        put16h(out + 4, (uint16_t)e->jaxis.value);
        break;
    case SDL_JOYBALLMOTION:
        out[0] = S12_JOYBALLMOTION;
        out[1] = e->jball.which; out[2] = e->jball.ball;
        put16h(out + 4, (uint16_t)e->jball.xrel); put16h(out + 6, (uint16_t)e->jball.yrel);
        break;
    case SDL_JOYHATMOTION:
        out[0] = S12_JOYHATMOTION;
        out[1] = e->jhat.which; out[2] = e->jhat.hat; out[3] = e->jhat.value;
        break;
    case SDL_JOYBUTTONDOWN:
    case SDL_JOYBUTTONUP:
        out[0] = (uint8_t)(e->type == SDL_JOYBUTTONDOWN ? S12_JOYBUTTONDOWN : S12_JOYBUTTONUP);
        out[1] = e->jbutton.which; out[2] = e->jbutton.button; out[3] = e->jbutton.state;
        break;
    default:
        out[0] = S12_NOEVENT;
        break;
    }
}

/* ---- SDL_RWops shims (SDL_rwops.h) ---- */
static void s_RWFromFile(void) {
    const char *file = ASTR(0), *mode = ASTR(1);
    char path[1024];
    if (!file || !mode) { error("SDL_RWFromFile: NULL argument"); RETC(0); }
    int plus = strchr(mode, '+') != NULL, wr = plus || strchr(mode, 'w') || strchr(mode, 'a');
    if (wr) hle_host_path_for_write(file, path, sizeof path, 0);
    else hle_host_path(file, path, sizeof path);
    int fl = plus ? O_RDWR : wr ? O_WRONLY : O_RDONLY;
    if (strchr(mode, 'w')) fl |= O_CREAT | O_TRUNC;
    if (strchr(mode, 'a')) fl |= O_CREAT | O_APPEND;
    int fd = open(path, fl, 0644);
    if (fd < 0) { error("SDL_RWFromFile: cannot open the file"); RETC(0); }
    struct stat st;
    long size = (fstat(fd, &st) == 0 && S_ISREG(st.st_mode)) ? (long)st.st_size : 0;
    uint32_t rw = new_rwops(S12_RW_KIND_FILE, fd, size, 1);
    if (!rw) { close(fd); RETC(0); }
    RETC(rw);
}
static void s_RWFromMem(void) {
    uint32_t mem = A32(0); int size = (int)A32(1);
    if (!mem || size < 0) { error("SDL_RWFromMem: bad buffer"); RETC(0); }
    RETC(new_rwops(S12_RW_KIND_MEM, (int)mem, size, 0));
}
static void s_RWseek(void) { uint32_t rw = A32(0); if (!rw) RETC(-1); RETC(rw_seek(rw, (int)A32(1), (int)A32(2))); }
static void s_RWread(void) { uint32_t rw = A32(0); if (!rw) RETC(0); RETC(rw_read(rw, A32(1), (int)A32(2), (int)A32(3))); }
static void s_RWwrite(void) { uint32_t rw = A32(0); if (!rw) RETC(0); RETC(rw_write(rw, A32(1), (int)A32(2), (int)A32(3))); }
static void s_RWclose(void) { uint32_t rw = A32(0); if (!rw) RETC(-1); RETC(rw_close(rw)); }
static void s_RWtell(void) { uint32_t rw = A32(0); if (!rw) RETC(-1); RETC(rw_seek(rw, 0, S12_SEEK_CUR)); }

/* ---- SDL_LoadBMP_RW / SDL_SaveBMP_RW (SDL_bmp.c) ----
 * Uncompressed BMP: 1/4/8-bit palettised (1 and 4 expanded to 8), 16-bit
 * 5-5-5 or BI_BITFIELDS, 24-bit BGR and 32-bit BGRA, rows padded to 4 bytes and
 * bottom-up unless the height is negative.  Saving writes 8-bit with a palette
 * for a palettised surface and 24-bit BGR for everything else, as SDL 1.2 does. */
enum { S12_BI_RGB = 0, S12_BI_BITFIELDS = 3 };
static uint32_t le32_at(uint32_t p) {
    return MEM8(p) | ((uint32_t)MEM8(p + 1) << 8) | ((uint32_t)MEM8(p + 2) << 16) | ((uint32_t)MEM8(p + 3) << 24);
}
static void put_le32(uint32_t p, uint32_t v) { MEM8(p) = (uint8_t)v; MEM8(p + 1) = (uint8_t)(v >> 8); MEM8(p + 2) = (uint8_t)(v >> 16); MEM8(p + 3) = (uint8_t)(v >> 24); }

static uint32_t load_bmp(uint32_t rw) {
    uint32_t mark = hle_scratch_mark();
    uint32_t h = hle_scratch(64);
    if (rw_seek(rw, 0, S12_SEEK_SET) < 0 || rw_read(rw, h, 1, 54) != 54) { hle_scratch_release(mark); error("SDL_LoadBMP_RW: truncated header"); return 0; }
    if (MEM8(h) != 'B' || MEM8(h + 1) != 'M') { hle_scratch_release(mark); error("SDL_LoadBMP_RW: not a Windows BMP file"); return 0; }
    uint32_t offbits = le32_at(h + 10), hdrsize = le32_at(h + 14);
    int w, hgt, bpp, topdown = 0;
    uint32_t compression, clrused;
    if (hdrsize == 12) {                      /* BITMAPCOREHEADER */
        w = (int)MEM8(h + 18) | ((int)MEM8(h + 19) << 8);
        hgt = (int)MEM8(h + 20) | ((int)MEM8(h + 21) << 8);
        bpp = (int)MEM8(h + 24) | ((int)MEM8(h + 25) << 8);
        compression = S12_BI_RGB; clrused = 0;
    } else {
        w = (int)le32_at(h + 18); hgt = (int)le32_at(h + 22);
        bpp = (int)MEM8(h + 28) | ((int)MEM8(h + 29) << 8);
        compression = le32_at(h + 30);
        clrused = le32_at(h + 46);
    }
    if (hgt < 0) { topdown = 1; hgt = -hgt; }
    if (w <= 0 || hgt <= 0) { hle_scratch_release(mark); error("SDL_LoadBMP_RW: bad dimensions"); return 0; }
    int expand = (bpp == 1 || bpp == 4) ? bpp : 0;
    int store_bpp = expand ? 8 : bpp;
    if (store_bpp != 8 && store_bpp != 16 && store_bpp != 24 && store_bpp != 32) { hle_scratch_release(mark); error("SDL_LoadBMP_RW: unsupported bit depth"); return 0; }
    uint32_t rmask = 0, gmask = 0, bmask = 0, amask = 0;
    if (compression == S12_BI_RGB) {
        if (offbits != 14 + hdrsize) compression = S12_BI_BITFIELDS;   /* masks follow the header */
        else if (store_bpp == 16) rmask = 0x7C00u, gmask = 0x03E0u, bmask = 0x001Fu;
        else if (store_bpp == 24) rmask = 0x00FF0000u, gmask = 0x0000FF00u, bmask = 0x000000FFu;
        else if (store_bpp == 32) rmask = 0x00FF0000u, gmask = 0x0000FF00u, bmask = 0x000000FFu;
    }
    if (compression == S12_BI_BITFIELDS && (store_bpp == 16 || store_bpp == 32)) {
        uint32_t m = hle_scratch(12);
        if (rw_seek(rw, 14 + hdrsize, S12_SEEK_SET) < 0 || rw_read(rw, m, 1, 12) != 12) { hle_scratch_release(mark); error("SDL_LoadBMP_RW: truncated bit masks"); return 0; }
        rmask = le32_at(m); gmask = le32_at(m + 4); bmask = le32_at(m + 8);
    } else if (compression != S12_BI_RGB) {
        hle_scratch_release(mark); error("SDL_LoadBMP_RW: compressed BMP files are not supported"); return 0;
    }
    uint32_t s = new_surface(w, hgt, store_bpp, rmask, gmask, bmask, amask, 0, 1);
    if (!s) { hle_scratch_release(mark); return 0; }
    if (store_bpp == 8) {
        uint32_t pal = MEM32(sf_format(s) + S12_FMT_PALETTE), colors = MEM32(pal + 4);
        int n = clrused ? (int)clrused : (expand == 1 ? 2 : expand == 4 ? 16 : 256);
        if (n > 256) n = 256;
        uint32_t ent = hle_scratch((uint32_t)(4 * n));
        int ent_size = hdrsize == 12 ? 3 : 4;
        if (rw_seek(rw, 14 + hdrsize, S12_SEEK_SET) < 0) { hle_scratch_release(mark); error("SDL_LoadBMP_RW: cannot seek to the palette"); return 0; }
        for (int i = 0; i < n; i++) {
            if (rw_read(rw, ent + (uint32_t)(i * ent_size), 1, ent_size) != ent_size) { hle_scratch_release(mark); error("SDL_LoadBMP_RW: truncated palette"); return 0; }
            MEM8(colors + (uint32_t)(i * 4) + 2) = MEM8(ent + (uint32_t)(i * ent_size));         /* BMP stores B, G, R */
            MEM8(colors + (uint32_t)(i * 4) + 1) = MEM8(ent + (uint32_t)(i * ent_size) + 1);
            MEM8(colors + (uint32_t)(i * 4)) = MEM8(ent + (uint32_t)(i * ent_size) + 2);
            MEM8(colors + (uint32_t)(i * 4) + 3) = 255;
        }
    }
    int src_bpp = expand ? expand : store_bpp;
    long row = ((long)w * src_bpp + 31) / 32 * 4;
    uint32_t rowbuf = hle_scratch((uint32_t)row);
    if (rw_seek(rw, (int)offbits, S12_SEEK_SET) < 0) { hle_scratch_release(mark); error("SDL_LoadBMP_RW: cannot seek to the pixels"); return 0; }
    for (int y = 0; y < hgt; y++) {
        int dsty = topdown ? y : hgt - 1 - y;
        if (rw_read(rw, rowbuf, 1, (int)row) != (int)row) { hle_scratch_release(mark); error("SDL_LoadBMP_RW: truncated pixel data"); return 0; }
        for (int x = 0; x < w; x++) {
            uint32_t v;
            if (expand == 1) v = (MEM8(rowbuf + (uint32_t)(x / 8)) >> (7 - (x % 8))) & 1u;
            else if (expand == 4) v = (MEM8(rowbuf + (uint32_t)(x / 2)) >> ((x % 2) ? 0 : 4)) & 0xFu;
            else if (store_bpp == 8) v = MEM8(rowbuf + (uint32_t)x);
            else if (store_bpp == 16) v = (uint32_t)MEM8(rowbuf + (uint32_t)(x * 2)) | ((uint32_t)MEM8(rowbuf + (uint32_t)(x * 2 + 1)) << 8);
            else if (store_bpp == 24) v = (uint32_t)MEM8(rowbuf + (uint32_t)(x * 3)) | ((uint32_t)MEM8(rowbuf + (uint32_t)(x * 3 + 1)) << 8) | ((uint32_t)MEM8(rowbuf + (uint32_t)(x * 3 + 2)) << 16);
            else v = le32_at(rowbuf + (uint32_t)(x * 4));
            pixel_write(s, x, dsty, v);
        }
    }
    hle_scratch_release(mark);
    return s;
}
static void s_LoadBMP_RW(void) {
    uint32_t rw = A32(0), s = 0;
    if (rw) s = load_bmp(rw);
    if (A32(1) && rw) rw_close(rw);
    RETC(s);
}
static void s_SaveBMP_RW(void) {
    uint32_t s = A32(0), rw = A32(1);
    if (!s || !rw) RETC(-1);
    uint32_t mark = hle_scratch_mark();
    int w = sf_w(s), h = sf_h(s), pal8 = MEM32(sf_format(s) + S12_FMT_PALETTE) != 0;
    int out_bpp = pal8 ? 8 : 24;
    long row = ((long)w * out_bpp + 31) / 32 * 4;
    uint32_t hdr = hle_scratch(54 + (pal8 ? 1024 : 0));
    uint32_t rowbuf = hle_scratch((uint32_t)row);
    memset(hle_ptr(hdr), 0, 54 + (pal8 ? 1024 : 0));
    uint32_t offbits = 54 + (pal8 ? 1024 : 0);
    MEM8(hdr) = 'B'; MEM8(hdr + 1) = 'M';
    put_le32(hdr + 2, (uint32_t)(offbits + row * h));
    put_le32(hdr + 10, offbits);
    put_le32(hdr + 14, 40);
    put_le32(hdr + 18, (uint32_t)w);
    put_le32(hdr + 22, (uint32_t)h);                       /* bottom-up */
    MEM8(hdr + 26) = 1;                                    /* biPlanes */
    MEM8(hdr + 28) = (uint8_t)out_bpp;
    put_le32(hdr + 34, (uint32_t)(row * h));               /* biSizeImage */
    if (pal8) {
        uint32_t colors = MEM32(MEM32(sf_format(s) + S12_FMT_PALETTE) + 4);
        for (int i = 0; i < 256; i++) {
            MEM8(hdr + 54 + (uint32_t)(i * 4)) = colors ? MEM8(colors + (uint32_t)(i * 4) + 2) : 0;
            MEM8(hdr + 54 + (uint32_t)(i * 4) + 1) = colors ? MEM8(colors + (uint32_t)(i * 4) + 1) : 0;
            MEM8(hdr + 54 + (uint32_t)(i * 4) + 2) = colors ? MEM8(colors + (uint32_t)(i * 4)) : 0;
        }
        put_le32(hdr + 46, 256);
    }
    int ok = rw_write(rw, hdr, 1, (int)(54 + (pal8 ? 1024 : 0))) == (int)(54 + (pal8 ? 1024 : 0));
    for (int y = h - 1; y >= 0 && ok; y--) {
        memset(hle_ptr(rowbuf), 0, (size_t)row);
        for (int x = 0; x < w; x++) {
            int r, g, b, a;
            get_rgba(sf_format(s), pixel_read(s, x, y), &r, &g, &b, &a);
            if (pal8) MEM8(rowbuf + (uint32_t)x) = (uint8_t)pixel_read(s, x, y);
            else {
                MEM8(rowbuf + (uint32_t)(x * 3)) = (uint8_t)b;
                MEM8(rowbuf + (uint32_t)(x * 3 + 1)) = (uint8_t)g;
                MEM8(rowbuf + (uint32_t)(x * 3 + 2)) = (uint8_t)r;
            }
        }
        ok = rw_write(rw, rowbuf, 1, (int)row) == (int)row;
    }
    hle_scratch_release(mark);
    if (A32(2)) rw_close(rw);
    if (!ok) error("SDL_SaveBMP_RW: write failed");
    RETC(ok ? 0 : -1);
}

/* ---- SDL_LoadWAV_RW / SDL_FreeWAV (SDL_wave.c) ----
 * RIFF/WAVE, PCM 8/16-bit (and the WAVE_FORMAT_EXTENSIBLE wrapper around it),
 * mono or stereo: spec gets freq/format/channels and SDL's 4096-sample default,
 * the samples come back in a guest buffer the guest frees with SDL_FreeWAV. */
static uint32_t load_wav(uint32_t rw, uint32_t spec, uint32_t buf_out, uint32_t len_out) {
    int total = rw_seek(rw, 0, S12_SEEK_END);
    if (total < 12 || rw_seek(rw, 0, S12_SEEK_SET) < 0) { error("SDL_LoadWAV_RW: not a seekable source"); return 0; }
    uint32_t mark = hle_scratch_mark();
    uint32_t f = hle_va(hle_alloc((uint32_t)total));
    if (!f || rw_read(rw, f, 1, total) != total) { hle_scratch_release(mark); error("SDL_LoadWAV_RW: short read"); return 0; }
    if (memcmp(hle_ptr(f), "RIFF", 4) != 0 || memcmp(hle_ptr(f + 8), "WAVE", 4) != 0) { hle_scratch_release(mark); hle_free(hle_ptr(f)); error("SDL_LoadWAV_RW: not a RIFF/WAVE file"); return 0; }
    int fmt_ok = 0, channels = 0, bits = 0, rate = 0, data_off = 0, data_len = 0;
    uint32_t pos = 12;
    while (pos + 8 <= (uint32_t)total) {
        uint32_t size = le32_at(f + pos + 4);
        uint32_t body = pos + 8;
        if (memcmp(hle_ptr(f + pos), "fmt ", 4) == 0 && body + 16 <= (uint32_t)total) {
            int tag = MEM8(f + body) | (MEM8(f + body + 1) << 8);
            channels = MEM8(f + body + 2) | (MEM8(f + body + 3) << 8);
            rate = (int)le32_at(f + body + 4);
            bits = MEM8(f + body + 14) | (MEM8(f + body + 15) << 8);
            if (tag == 0xFFFE && size >= 40 && body + 26 <= (uint32_t)total) tag = MEM8(f + body + 24) | (MEM8(f + body + 25) << 8);
            fmt_ok = (tag == 1 && (bits == 8 || bits == 16));
            if (!fmt_ok) { hle_scratch_release(mark); hle_free(hle_ptr(f)); error("SDL_LoadWAV_RW: unsupported WAVE data format"); return 0; }
        } else if (memcmp(hle_ptr(f + pos), "data", 4) == 0) {
            data_off = (int)body;
            data_len = (int)size;
            if ((uint32_t)(data_off + data_len) > (uint32_t)total) data_len = total - data_off;
            break;
        }
        pos = body + size + (size & 1);
    }
    if (!fmt_ok || !data_off || data_len <= 0) { hle_scratch_release(mark); hle_free(hle_ptr(f)); error("SDL_LoadWAV_RW: missing fmt or data chunk"); return 0; }
    int sample_size = (bits / 8) * channels;
    data_len &= ~(sample_size - 1);
    uint32_t buf = hle_va(hle_alloc((uint32_t)data_len));
    if (!buf) { hle_scratch_release(mark); hle_free(hle_ptr(f)); error("out of guest memory"); return 0; }
    memcpy(hle_ptr(buf), hle_ptr(f + (uint32_t)data_off), (size_t)data_len);
    hle_free(hle_ptr(f));
    hle_scratch_release(mark);
    MEM32(spec) = (uint32_t)rate;
    MEM16(spec + 4) = (uint16_t)(bits == 8 ? 0x0008 : 0x8010);   /* AUDIO_U8 / AUDIO_S16LSB */
    MEM8(spec + 6) = (uint8_t)channels;
    MEM8(spec + 7) = 0;                                          /* silence: SDL leaves it 0 */
    MEM16(spec + 8) = 4096;                                      /* SDL's default buffer */
    MEM16(spec + 10) = 0;
    MEM32(spec + 12) = 0;
    MEM32(spec + 16) = 0;
    MEM32(spec + 20) = 0;
    MEM32(buf_out) = buf;
    MEM32(len_out) = (uint32_t)data_len;
    return spec;
}
static void s_LoadWAV_RW(void) {
    uint32_t rw = A32(0), s = 0;
    if (rw) s = load_wav(rw, A32(2), A32(3), A32(4));
    if (A32(1) && rw) rw_close(rw);
    RETC(s);
}
static void s_FreeWAV(void) { uint32_t b = A32(0); if (b) hle_free(hle_ptr(b)); RETCV(); }

/* ---- pixel format API ---- */
static void s_MapRGB(void) { RETC(map_rgba(A32(0), (int)A32(1), (int)A32(2), (int)A32(3), 255)); }
static void s_MapRGBA(void) { RETC(map_rgba(A32(0), (int)A32(1), (int)A32(2), (int)A32(3), (int)A32(4))); }
static void s_GetRGB(void) {
    int r, g, b, a;
    get_rgba(A32(1), A32(0), &r, &g, &b, &a);
    if (A32(2)) MEM8(A32(2)) = (uint8_t)r;
    if (A32(3)) MEM8(A32(3)) = (uint8_t)g;
    if (A32(4)) MEM8(A32(4)) = (uint8_t)b;
    RETCV();
}
static void s_GetRGBA(void) {
    int r, g, b, a;
    get_rgba(A32(1), A32(0), &r, &g, &b, &a);
    if (A32(2)) MEM8(A32(2)) = (uint8_t)r;
    if (A32(3)) MEM8(A32(3)) = (uint8_t)g;
    if (A32(4)) MEM8(A32(4)) = (uint8_t)b;
    if (A32(5)) MEM8(A32(5)) = (uint8_t)a;
    RETCV();
}
static void s_DisplayFormat(void) { uint32_t src = A32(0); RETC(src ? display_format(src, 0) : 0); }
static void s_DisplayFormatAlpha(void) { uint32_t src = A32(0); RETC(src ? display_format(src, 1) : 0); }
static void s_ConvertSurface(void) {
    uint32_t src = A32(0), fmt = A32(1);
    if (!src || !fmt) RETC(0);
    uint32_t dst = new_surface(sf_w(src), sf_h(src), MEM8(fmt + S12_FMT_BPP),
                               MEM32(fmt + S12_FMT_RMASK), MEM32(fmt + S12_FMT_GMASK),
                               MEM32(fmt + S12_FMT_BMASK), MEM32(fmt + S12_FMT_AMASK), 0, 1);
    if (!dst) RETC(0);
    uint32_t sp = MEM32(fmt + S12_FMT_PALETTE), dp = MEM32(sf_format(dst) + S12_FMT_PALETTE);
    if (sp && dp) memcpy(hle_ptr(MEM32(dp + 4)), hle_ptr(MEM32(sp + 4)), 256 * 4);
    convert_pixels(src, dst);
    copy_transparency(src, dst);
    RETC(dst);
}
static void s_SetColorKey(void) {
    uint32_t s = A32(0), flag = A32(1);
    if (!s) RETC(-1);
    uint32_t f = sf_format(s);
    if (flag & S12_SRCCOLORKEY) { MEM32(f + S12_FMT_COLORKEY) = A32(2); MEM32(s + S12_SF_FLAGS) |= S12_SRCCOLORKEY; }
    else { MEM32(f + S12_FMT_COLORKEY) = 0; MEM32(s + S12_SF_FLAGS) &= ~S12_SRCCOLORKEY; }
    RETC(0);
}
static void s_SetAlpha(void) {
    uint32_t s = A32(0), flag = A32(1);
    if (!s) RETC(-1);
    uint32_t f = sf_format(s);
    if (flag & S12_SRCALPHA) { MEM8(f + S12_FMT_ALPHA) = (uint8_t)A32(2); MEM32(s + S12_SF_FLAGS) |= S12_SRCALPHA; }
    else { MEM8(f + S12_FMT_ALPHA) = 255; MEM32(s + S12_SF_FLAGS) &= ~S12_SRCALPHA; }
    RETC(0);
}
/* SDL_SetPalette / SDL_SetColors (which is SetPalette with LOGPAL|PHYSPAL).
 * Both copy a run of SDL_Color into an 8-bit surface's palette; a surface with
 * no palette "does nothing, returning 0" (SDL_video.h). */
static int set_palette_range(uint32_t s, uint32_t colors, int first, int n) {
    if (!s) return 0;
    uint32_t pal = MEM32(sf_format(s) + S12_FMT_PALETTE);
    if (!pal) return 0;
    int npal = (int)MEM32(pal);
    uint32_t dst = MEM32(pal + 4);
    if (!dst || first < 0 || first >= npal || n <= 0) return 0;
    if (first + n > npal) n = npal - first;
    if (colors) memcpy(hle_ptr(dst + (uint32_t)first * 4), hle_ptr(colors), (size_t)n * 4);
    return 1;
}
static void s_SetPalette(void) { RETC(set_palette_range(A32(0), A32(2), (int)A32(3), (int)A32(4))); }
static void s_SetColors(void) { RETC(set_palette_range(A32(0), A32(1), (int)A32(2), (int)A32(3))); }

/* ---- guest RWops: the shim table entries below ---- */
/* ---- the event queue ----
 * SDL 1.2's SDL_PushEvent and SDL_PeepEvents work on SDL's own queue, and
 * SDL_EventState(SDL_IGNORE) drops a type on the way out.  SDL2's event types
 * are numbered differently from SDL 1.2's, so the queue is ours: events the
 * guest pushes land in it, SDL2's are translated into it, and everything is
 * filtered by the per-type enable table on the way to the guest. */
enum { S12_QUEUE_MAX = 64 };
static uint8_t g_queue[S12_QUEUE_MAX][S12_EVENT_SIZE];
static int g_qhead, g_qcount;
static uint8_t g_event_enabled[S12_NUMEVENTS];
static int g_events_ready;
/* SDL 1.2 starts with every event type enabled (SDL_EventState's default). */
static void events_init(void) {
    if (g_events_ready) return;
    g_events_ready = 1;
    memset(g_event_enabled, 1, sizeof g_event_enabled);
}

static int queue_push(const uint8_t *ev) {
    if (g_qcount >= S12_QUEUE_MAX) return -1;
    memcpy(g_queue[(g_qhead + g_qcount) % S12_QUEUE_MAX], ev, S12_EVENT_SIZE);
    g_qcount++;
    return 0;
}
static int queue_pop(uint8_t *ev) {
    if (g_qcount <= 0) return 0;
    memcpy(ev, g_queue[g_qhead], S12_EVENT_SIZE);
    g_qhead = (g_qhead + 1) % S12_QUEUE_MAX;
    g_qcount--;
    return 1;
}
static int enabled(const uint8_t *ev) {
    int t = ev[0];
    return t > 0 && t < S12_NUMEVENTS ? g_event_enabled[t] != 0 : 1;
}
/* SDL 1.2 drops a disabled type where it would be delivered
 * (SDL_ProcessEvents[type] == SDL_IGNORE), so it is discarded, not held. */
static int queue_pop_enabled(uint8_t *ev) {
    while (queue_pop(ev)) if (enabled(ev)) return 1;
    return 0;
}
/* Pull one SDL2 event into the queue (translated), skipping disabled types. */
static int pump_one(int block_ms) {
    SDL_Event e;
    for (int i = 0; i < 64; i++) {
        int got = block_ms < 0 ? SDL_WaitEventTimeout(&e, 1000) : block_ms ? SDL_WaitEventTimeout(&e, block_ms) : SDL_PollEvent(&e);
        if (!got) return 0;
        uint8_t ev[S12_EVENT_SIZE];
        event_from_sdl2(ev, &e);
        if (ev[0] == S12_NOEVENT) continue;              /* not one SDL 1.2 has */
        if (!enabled(ev)) continue;
        return queue_push(ev) == 0;
    }
    return 0;
}
static void s_PollEvent(void) {
    events_init();
    uint8_t ev[S12_EVENT_SIZE];
    /* pump_one leaves what it translated in the queue, so it is popped here. */
    if (!queue_pop_enabled(ev) && !(pump_one(0) && queue_pop_enabled(ev))) RETC(0);
    if (A32(0)) memcpy(hle_ptr(A32(0)), ev, S12_EVENT_SIZE);
    RETC(1);
}
static void s_PumpEvents(void) { SDL_PumpEvents(); RETCV(); }
static void s_WaitEvent(void) {                          /* (SDL_Event *event) */
    events_init();
    uint8_t ev[S12_EVENT_SIZE];
    uint32_t out = A32(0);
    int got = queue_pop_enabled(ev);
    if (!got) {                                          /* waiting: let other threads run */
        hle_block_begin();
        got = pump_one(-1) && queue_pop_enabled(ev);
        hle_block_end();
    }
    if (!got) RETC(0);
    if (out) memcpy(hle_ptr(out), ev, S12_EVENT_SIZE);
    RETC(1);
}
static void s_PushEvent(void) {
    events_init();                          /* (SDL_Event *event) */
    uint8_t ev[S12_EVENT_SIZE];
    uint32_t p = A32(0);
    if (!p) RETC(-1);
    memcpy(ev, hle_ptr(p), S12_EVENT_SIZE);
    if (!ev[0]) RETC(-1);
    RETC(queue_push(ev) == 0 ? 0 : -1);                  /* SDL 1.2: 0 on success, -1 when the queue is full */
}
static void s_PeepEvents(void) {
    events_init();                         /* (events, numevents, action, mask) */
    uint32_t events = A32(0), mask = A32(3);
    int n = (int)A32(1), action = (int)A32(2), got = 0;
    if (n < 0 || !events) RETC(0);
    if (action == S12_ADDEVENT) {                        /* push the guest's events into the queue */
        for (int i = 0; i < n; i++)
            if (queue_push((const uint8_t *)hle_ptr(events + (uint32_t)i * S12_EVENT_SIZE)) != 0) break;
            else got++;
        RETC(got);
    }
    for (int i = 0; i < g_qcount && got < n; i++) {
        uint8_t *ev = g_queue[(g_qhead + i) % S12_QUEUE_MAX];
        if (!enabled(ev)) continue;
        if (mask != 0xFFFFFFFFu && ev[0] < 32 && !(mask & (1u << ev[0]))) continue;
        memcpy(hle_ptr(events + (uint32_t)got * S12_EVENT_SIZE), ev, S12_EVENT_SIZE);
        got++;
    }
    if (action == S12_GETEVENT) {                        /* ... and remove them */
        for (int i = 0; i < got; i++) { uint8_t drop[S12_EVENT_SIZE]; queue_pop(drop); }
    }
    RETC(got);
}
static void s_NumEvents(void) { RETC((uint32_t)g_qcount); }
static void s_EventState(void) {
    events_init();                         /* (type, state) */
    int type = (int)A32(0), state = (int)A32(1);
    if (type <= 0 || type >= S12_NUMEVENTS) RETC(S12_ENABLE);
    int was = g_event_enabled[type] ? S12_ENABLE : S12_IGNORE;
    if (state != S12_QUERY) g_event_enabled[type] = (uint8_t)(state != S12_IGNORE);
    RETC(was);
}
/* ---- the audio device (SDL_audio.h) ----
 * SDL_OpenAudio's callback is guest code, so the SDL2 device's callback runs it
 * through hle_call_guest with a guest buffer of its own; SDL2 silences the
 * stream before each callback, as SDL 1.2 does.  The format is opened exactly
 * as asked (no allowed changes), so the guest's callback always sees the format
 * it negotiated, and a device that cannot do it fails the open instead of
 * silently converting. */
static SDL_AudioDeviceID g_adev;
static uint32_t g_audio_cb, g_audio_userdata, g_audio_stream, g_audio_stream_len;
static uint16_t g_audio_format;
static uint8_t g_audio_channels, g_audio_silence;

static void audio_cb(void *userdata, Uint8 *stream, int len) {
    (void)userdata;
    if (!g_audio_cb) return;                             /* SDL2 already silenced it */
    if ((int)g_audio_stream_len < len) {
        if (g_audio_stream) hle_free(hle_ptr(g_audio_stream));
        g_audio_stream = hle_va(hle_alloc((uint32_t)len));
        g_audio_stream_len = g_audio_stream ? (uint32_t)len : 0;
        if (!g_audio_stream) return;
    }
    uint32_t args[3] = { g_audio_userdata, g_audio_stream, (uint32_t)len };
    hle_call_guest(g_audio_cb, 3, args);
    memcpy(stream, hle_ptr(g_audio_stream), (size_t)len);
}
static void s_OpenAudio(void) {                          /* (desired, obtained) */
    uint32_t want = A32(0), got = A32(1);
    if (!want) { error("SDL_OpenAudio: NULL spec"); RETC(-1); }
    SDL_AudioSpec desired;
    SDL_zero(desired);
    desired.freq = (int)MEM32(want);
    desired.format = (SDL_AudioFormat)MEM16(want + 4);
    desired.channels = MEM8(want + 6);
    desired.samples = MEM16(want + 8);
    desired.callback = audio_cb;
    desired.userdata = NULL;
    if (!desired.freq || !desired.channels || !desired.samples) { error("SDL_OpenAudio: bad spec"); RETC(-1); }
    if (g_adev) SDL_CloseAudioDevice(g_adev), g_adev = 0;
    SDL_AudioSpec have;
    SDL_zero(have);
    SDL_AudioDeviceID dev = SDL_OpenAudioDevice(NULL, 0, &desired, &have, 0);
    if (!dev) { error(SDL_GetError()); RETC(-1); }
    g_adev = dev;
    g_audio_cb = MEM32(want + 16);
    g_audio_userdata = MEM32(want + 20);
    g_audio_format = have.format;
    g_audio_channels = have.channels;
    g_audio_silence = have.silence;
    if (got) {                                           /* what the device really gave */
        MEM32(got) = (uint32_t)have.freq;
        MEM16(got + 4) = have.format;
        MEM8(got + 6) = have.channels;
        MEM8(got + 7) = have.silence;
        MEM16(got + 8) = have.samples;
        MEM16(got + 10) = 0;
        MEM32(got + 12) = (uint32_t)have.size;
        MEM32(got + 16) = MEM32(want + 16);
        MEM32(got + 20) = MEM32(want + 20);
    }
    RETC(0);
}
static void s_CloseAudio(void) {
    if (g_adev) SDL_CloseAudioDevice(g_adev), g_adev = 0;
    g_audio_cb = 0;
    RETCV();
}
static void s_PauseAudio(void) { if (g_adev) SDL_PauseAudioDevice(g_adev, (int)A32(0)); RETCV(); }
static void s_LockAudio(void) { if (g_adev) SDL_LockAudioDevice(g_adev); RETCV(); }
static void s_UnlockAudio(void) { if (g_adev) SDL_UnlockAudioDevice(g_adev); RETCV(); }
static void s_GetAudioStatus(void) {                     /* SDL_AUDIO_STOPPED/PLAYING/PAUSED */
    RETC(g_adev ? (uint32_t)SDL_GetAudioDeviceStatus(g_adev) : 0u);
}

/* SDL_BuildAudioCVT/SDL_ConvertAudio.  SDL 1.2 fills an SDL_AudioCVT with a
 * filter chain; this shim records the conversion the guest asked for (SDL
 * 1.2's return contract: 0 = nothing to do, 1 = convert, -1 = error) and
 * SDL_ConvertAudio does it in place in cvt->buf: any of U8/S8/U16/S16, mono or
 * stereo, and a linear resample.  SDL_AudioCVT's 32-bit layout is
 * needed 0, src_format 4, dst_format 6, rate_incr 8 (double), buf 16, len 20,
 * len_cvt 24, len_mult 28, len_ratio 32 (double), filters 40..79,
 * filter_index 80. */
enum { S12_CVT_NEEDED = 0, S12_CVT_SRCFMT = 4, S12_CVT_DSTFMT = 6, S12_CVT_RATEINCR = 8,
       S12_CVT_BUF = 16, S12_CVT_LEN = 20, S12_CVT_LENCVT = 24, S12_CVT_LENMULT = 28,
       S12_CVT_LENRATIO = 32, S12_CVT_FILTERS = 40, S12_CVT_FILTERINDEX = 80, S12_CVT_SIZE = 84 };
static int audio_bits(uint16_t fmt) { return (fmt & 0xFF) == 8 ? 8 : 16; }
static int audio_signed(uint16_t fmt) { return (fmt & 0x8000) != 0; }
/* The conversion's channel counts and rates are not fields of SDL_AudioCVT (SDL
 * 1.2 keeps them inside the filter chain SDL_BuildAudioCVT installed), so this
 * shim remembers what each guest SDL_AudioCVT was built for.  SDL_ConvertAudio
 * refuses a CVT it never built rather than guessing at one. */
typedef struct { uint32_t cvt; uint16_t sf, df; int sc, dc, sr, dr; } cvt_slot;
static cvt_slot g_cvt[8];
static cvt_slot *cvt_of(uint32_t va) {
    if (!va) return NULL;
    for (int i = 0; i < (int)(sizeof g_cvt / sizeof g_cvt[0]); i++) if (g_cvt[i].cvt == va) return &g_cvt[i];
    return NULL;
}
static void s_BuildAudioCVT(void) {                      /* (cvt, src_fmt, src_ch, src_rate, dst_fmt, dst_ch, dst_rate) */
    uint32_t cvt = A32(0);
    if (!cvt) RETC(-1);
    uint16_t sf = (uint16_t)A32(1), df = (uint16_t)A32(4);
    int sc = (int)A32(2), dc = (int)A32(5), sr = (int)A32(3), dr = (int)A32(6);
    if (sc <= 0 || dc <= 0 || sr <= 0 || dr <= 0 || audio_bits(sf) == 16 + 1 || audio_bits(df) == 16 + 1) { error("SDL_BuildAudioCVT: bad format"); RETC(-1); }
    int needed = (sf != df) || (sc != dc) || (sr != dr);
    int src_frame = (audio_bits(sf) / 8) * sc, dst_frame = (audio_bits(df) / 8) * dc;
    /* "buffer must be len*len_mult big" (SDL_audio.h): the rate change and the
     * frame-size change both count, e.g. mono u8 22050 -> stereo s16 44100 is
     * 8x. */
    double len_ratio = ((double)dst_frame * dr) / ((double)src_frame * sr);
    int len_mult = (int)(len_ratio + 0.999999);
    if (len_mult < 1) len_mult = 1;
    MEM32(cvt + S12_CVT_NEEDED) = (uint32_t)needed;
    MEM16(cvt + S12_CVT_SRCFMT) = sf;
    MEM16(cvt + S12_CVT_DSTFMT) = df;
    double incr = (double)dr / (double)sr, ratio = len_ratio;
    memcpy(hle_ptr(cvt + S12_CVT_RATEINCR), &incr, 8);
    memcpy(hle_ptr(cvt + S12_CVT_LENRATIO), &ratio, 8);
    MEM32(cvt + S12_CVT_BUF) = 0;
    MEM32(cvt + S12_CVT_LEN) = 0;
    MEM32(cvt + S12_CVT_LENCVT) = 0;
    MEM32(cvt + S12_CVT_LENMULT) = (uint32_t)len_mult;
    for (int i = 0; i < 10; i++) MEM32(cvt + S12_CVT_FILTERS + (uint32_t)i * 4) = 0;
    MEM32(cvt + S12_CVT_FILTERINDEX) = 0;
    cvt_slot *slot = cvt_of(cvt);                        /* remember what to convert from and to */
    if (!slot) for (int i = 0; i < (int)(sizeof g_cvt / sizeof g_cvt[0]); i++) if (!g_cvt[i].cvt) { slot = &g_cvt[i]; break; }
    if (!slot) { error("SDL_BuildAudioCVT: too many converter tables"); RETC(-1); }
    slot->cvt = cvt; slot->sf = sf; slot->df = df;
    slot->sc = sc; slot->dc = dc; slot->sr = sr; slot->dr = dr;
    RETC(needed ? 1 : 0);
}
/* One sample as a 32-bit value in the 16-bit range, so 8- and 16-bit sources
 * mix and convert without losing anything. */
static int audio_sample(uint16_t fmt, const uint8_t *p, int i) {
    if (audio_bits(fmt) == 8) return audio_signed(fmt) ? (int)(int8_t)p[i] * 256 : ((int)p[i] - 128) * 256;
    uint16_t raw = (uint16_t)(p[i * 2] | (p[i * 2 + 1] << 8));
    if (fmt & 0x1000) raw = (uint16_t)((raw >> 8) | (raw << 8));   /* MSB format */
    return (int)(int16_t)raw;
}
static void audio_put(uint16_t fmt, uint8_t *out, int i, int v) {
    if (v > 32767) v = 32767;
    if (v < -32768) v = -32768;
    if (audio_bits(fmt) == 8) {
        int b = v / 256;
        if (b > 127) b = 127;
        if (b < -128) b = -128;
        out[i] = (uint8_t)(audio_signed(fmt) ? (int8_t)b : b + 128);
    } else {
        uint16_t raw = (uint16_t)(int16_t)v;
        if (fmt & 0x1000) raw = (uint16_t)((raw >> 8) | (raw << 8));
        out[i * 2] = (uint8_t)raw;
        out[i * 2 + 1] = (uint8_t)(raw >> 8);
    }
}
static void s_ConvertAudio(void) {                       /* (SDL_AudioCVT *cvt) */
    uint32_t cvt = A32(0);
    cvt_slot *c = cvt_of(cvt);
    if (!cvt || !c) { error("SDL_ConvertAudio: this SDL_AudioCVT was not built by SDL_BuildAudioCVT"); RETC(-1); }
    uint32_t buf = MEM32(cvt + S12_CVT_BUF);
    int len = (int)MEM32(cvt + S12_CVT_LEN);
    if (!buf || len <= 0) { error("SDL_ConvertAudio: no buffer"); RETC(-1); }
    if (!MEM32(cvt + S12_CVT_NEEDED)) { MEM32(cvt + S12_CVT_LENCVT) = (uint32_t)len; RETC(0); }
    int sframe = (audio_bits(c->sf) / 8) * c->sc;
    if (len % sframe) len -= len % sframe;               /* whole source frames only */
    int frames = len / sframe;
    if (frames <= 0) { error("SDL_ConvertAudio: nothing to convert"); RETC(-1); }
    int out_frames = c->sr == c->dr ? frames : (int)((long)frames * c->dr / c->sr);
    if (out_frames <= 0) { error("SDL_ConvertAudio: conversion yields no frames"); RETC(-1); }
    int dframe = (audio_bits(c->df) / 8) * c->dc;
    uint32_t need = (uint32_t)(out_frames * dframe);
    if (need > MEM32(cvt + S12_CVT_LENMULT) * (uint32_t)len) { error("SDL_ConvertAudio: buffer too small"); RETC(-1); }
    int *tmp = (int *)malloc(sizeof(int) * (size_t)out_frames * (size_t)c->dc);
    if (!tmp) { error("out of memory"); RETC(-1); }
    const uint8_t *in = (const uint8_t *)hle_ptr(buf);
    /* channel conversion, then a linear resample */
    for (int f = 0; f < out_frames; f++) {
        double pos = c->sr == c->dr ? (double)f : (double)f * c->sr / c->dr;
        int i0 = (int)pos;
        if (i0 > frames - 1) i0 = frames - 1;
        int i1 = i0 + 1 < frames ? i0 + 1 : i0;
        double t = pos - i0;
        for (int ch = 0; ch < c->dc; ch++) {
            int src_ch = ch < c->sc ? ch : (c->sc == 1 ? 0 : c->sc - 1);
            int a = audio_sample(c->sf, in, i0 * c->sc + src_ch);
            int b = audio_sample(c->sf, in, i1 * c->sc + src_ch);
            int v = (int)(a + (b - a) * t);
            if (c->sc == 2 && c->dc == 1) {              /* stereo to mono: average both */
                int l = audio_sample(c->sf, in, i0 * 2), r = audio_sample(c->sf, in, i0 * 2 + 1);
                int l2 = audio_sample(c->sf, in, i1 * 2), r2 = audio_sample(c->sf, in, i1 * 2 + 1);
                v = (int)(((l + r) / 2) + (((l2 + r2) / 2) - ((l + r) / 2)) * t);
            }
            tmp[f * c->dc + ch] = v;
        }
    }
    for (int i = 0; i < out_frames * c->dc; i++) audio_put(c->df, (uint8_t *)hle_ptr(buf), i, tmp[i]);
    free(tmp);
    MEM32(cvt + S12_CVT_LENCVT) = need;
    RETC(0);
}
static void s_MixAudio(void) {                           /* (dst, src, len, volume) */
    uint32_t dst = A32(0), src = A32(1), len = A32(2);
    if (!g_adev || !dst || !src || !len) RETCV();         /* nothing open: SDL 1.2 mixes nothing either */
    SDL_MixAudioFormat(hle_ptr(dst), hle_ptr(src), g_audio_format, len, (int)A32(3));
    RETCV();
}

/* ---- video information and modes ----
 * SDL_GetVideoInfo's SDL_VideoInfo is a guest structure (SDL_video.h): a word
 * of capability bits, video_mem, the SDL_PixelFormat *, and the current mode.
 * The bits say what this layer really offers - software surfaces with colour
 * key, alpha and fill blits, in a window - so a game that checks them gets an
 * answer that matches what its blits will do. */
enum { S12_VIDEOINFO = 20 };
static uint32_t g_videoinfo;
static uint32_t video_format(void) {
    if (g_video) return sf_format(g_video);
    uint32_t f = hle_va(hle_alloc(S12_FORMAT));
    if (!f) return 0;
    MEM8(f + S12_FMT_BPP) = 32; MEM8(f + S12_FMT_BYTESPP) = 4;
    MEM8(f + S12_FMT_ALPHA) = 255;
    set_mask_fields(f, 0x00FF0000u, 0x0000FF00u, 0x000000FFu, 0xFF000000u);
    return f;
}
static void s_GetVideoInfo(void) {
    if (!g_videoinfo) {
        g_videoinfo = hle_va(hle_alloc(S12_VIDEOINFO));
        if (!g_videoinfo) RETC(0);
    }
    uint32_t vf = video_format();
    MEM32(g_videoinfo) = (1u << 1) | (1u << 12) | (1u << 13) | (1u << 14) | (1u << 15);   /* wm_available, blit_sw[_CC|_A], blit_fill */
    MEM32(g_videoinfo + 4) = 0;                          /* video_mem: none of it is video memory */
    MEM32(g_videoinfo + 8) = vf;
    MEM32(g_videoinfo + 12) = g_video ? (uint32_t)sf_w(g_video) : 640;
    MEM32(g_videoinfo + 16) = g_video ? (uint32_t)sf_h(g_video) : 480;
    RETC(g_videoinfo);
}
/* SDL_ListModes: the display's modes as SDL_Rects, largest first, NULL-ended;
 * (SDL_Rect **)-1 means "any size is fine".  SDL2's mode list is the display's,
 * which is what a window would be created in. */
#define S12_MAX_MODES 32
static uint32_t g_modelist;
static void s_ListModes(void) {
    /* "Returns (SDL_Rect **)-1 if any dimension is okay for the given format"
     * (SDL_video.h): in a window that is exactly the case. */
    if (!(A32(1) & S12_FULLSCREEN)) RETC(0xFFFFFFFFu);
    if (!SDL_WasInit(SDL_INIT_VIDEO) && SDL_InitSubSystem(SDL_INIT_VIDEO) != 0) { error(SDL_GetError()); RETC(0); }
    int display = 0, n = SDL_GetNumDisplayModes(display);
    if (n <= 0) RETC(0);
    if (!g_modelist) {
        g_modelist = hle_va(hle_alloc((S12_MAX_MODES + 1) * 8));
        if (!g_modelist) RETC(0);
    }
    int used = 0;
    for (int i = 0; i < n && used < S12_MAX_MODES; i++) {
        SDL_DisplayMode m;
        if (SDL_GetDisplayMode(display, i, &m) != 0) continue;
        uint32_t r = g_modelist + (uint32_t)used * 8;
        put16(r, 0); put16(r + 2, 0); put16(r + 4, (uint16_t)m.w); put16(r + 6, (uint16_t)m.h);
        used++;
    }
    MEM32(g_modelist + (uint32_t)used * 8) = 0;          /* the NULL terminator */
    RETC(used ? g_modelist : 0);
}
static void s_VideoModeOK(void) {                        /* (w, h, bpp, flags) */
    int bpp = (int)A32(2);
    if (!(A32(3) & S12_FULLSCREEN)) {
        /* In a window any size works and the surface really is the depth asked
         * for (SDL 1.2 answers the bpp it would give); 0 for a depth we cannot
         * make a surface in. */
        RETC(bpp == 0 ? 32u : (bpp == 8 || bpp == 16 || bpp == 24 || bpp == 32) ? (uint32_t)bpp : 0u);
    }
    if (!SDL_WasInit(SDL_INIT_VIDEO) && SDL_InitSubSystem(SDL_INIT_VIDEO) != 0) { error(SDL_GetError()); RETC(0); }
    SDL_DisplayMode want, got;
    SDL_zero(want);
    want.w = (int)A32(0); want.h = (int)A32(1);
    want.format = (bpp == 16) ? SDL_PIXELFORMAT_RGB565 : SDL_PIXELFORMAT_ARGB8888;
    if (SDL_GetClosestDisplayMode(0, &want, &got)) RETC((uint32_t)SDL_BITSPERPIXEL(got.format));
    RETC(0);                                             /* no mode at that size: SDL answers 0 too */
}

/* ---- the window manager ---- */
static uint32_t g_caption, g_icon_caption;
static void s_WM_SetCaption(void) {                      /* (title, icon) */
    if (A32(0)) g_caption = hle_guest_strdup(ASTR(0));
    if (A32(1)) g_icon_caption = hle_guest_strdup(ASTR(1));
    if (g_window) SDL_SetWindowTitle(g_window, A32(0) ? ASTR(0) : "");
    RETCV();
}
static void s_WM_GetCaption(void) {                      /* (char **title, char **icon) */
    if (A32(0)) MEM32(A32(0)) = g_caption;
    if (A32(1)) MEM32(A32(1)) = g_icon_caption;
    RETCV();
}
/* SDL_WM_SetIcon: the guest's surface becomes an SDL2 surface over the same
 * guest pixels and SDL_SetWindowIcon copies what it needs. */
static void s_WM_SetIcon(void) {                         /* (SDL_Surface *icon, Uint8 *mask) */
    uint32_t s = A32(0);
    if (!s || !g_window) RETCV();
    uint32_t f = sf_format(s);
    SDL_Surface *icon = SDL_CreateRGBSurfaceFrom(hle_ptr(sf_pixels(s)), sf_w(s), sf_h(s),
                                                 sf_bpp(s), sf_pitch(s),
                                                 MEM32(f + S12_FMT_RMASK), MEM32(f + S12_FMT_GMASK),
                                                 MEM32(f + S12_FMT_BMASK), MEM32(f + S12_FMT_AMASK));
    if (!icon) { error(SDL_GetError()); RETCV(); }
    SDL_SetWindowIcon(g_window, icon);
    SDL_FreeSurface(icon);
    RETCV();
}
static void s_WM_IconifyWindow(void) {
    if (!g_window) RETC(0);                              /* nothing to iconify */
    SDL_MinimizeWindow(g_window);
    RETC(1);
}
static void s_WM_ToggleFullScreen(void) {
    if (!g_window) RETC(0);
    uint32_t full = (SDL_GetWindowFlags(g_window) & SDL_WINDOW_FULLSCREEN_DESKTOP) ? 1u : 0u;
    if (SDL_SetWindowFullscreen(g_window, full ? 0 : SDL_WINDOW_FULLSCREEN_DESKTOP) != 0) { error(SDL_GetError()); RETC(0); }
    RETC(1);
}
static void s_WM_GrabInput(void) {                       /* (SDL_GrabMode mode) */
    int mode = (int)A32(0);
    if (mode != -1 && g_window) {                        /* SDL_GRAB_QUERY leaves it alone */
        SDL_SetWindowGrab(g_window, mode == 1 ? SDL_TRUE : SDL_FALSE);
        SDL_SetRelativeMouseMode(mode == 1 ? SDL_TRUE : SDL_FALSE);
    }
    if (mode == -1 && g_window) RETC((SDL_GetWindowGrab(g_window) == SDL_TRUE) ? 1u : 0u);
    RETC((uint32_t)(mode == -1 ? 0 : mode));
}
static void s_GetAppState(void) {
    if (!g_window) RETC(0);                              /* no window, no focus to report */
    uint32_t f = SDL_GetWindowFlags(g_window), st = 0;
    if (f & SDL_WINDOW_MOUSE_FOCUS) st |= 0x01u;         /* SDL_APPMOUSEFOCUS */
    if (f & SDL_WINDOW_INPUT_FOCUS) st |= 0x02u;         /* SDL_APPINPUTFOCUS */
    if (!(f & SDL_WINDOW_MINIMIZED)) st |= 0x04u;        /* SDL_APPACTIVE */
    RETC(st);
}
static void s_ShowCursor(void) { RETC(SDL_ShowCursor((int)A32(0))); }
/* ---- keyboard, mouse and joystick ----
 * SDL 1.2's Windows driver builds keysym.sym from the virtual key through
 * VK_keymap (src/video/windib/SDL_dibevents.c): letters are the LOWERCASE
 * SDLK_a..z, digits and punctuation are unshifted, and the function/navigation
 * keys take SDL 1.2's own values.  SDL2's keycodes are the same idea but number
 * the special keys differently, so they are translated here; the translated
 * character (with Shift applied) goes in keysym.unicode, which is what
 * SDL_EnableUNICODE controls in SDL 1.2. */
enum { S12_SDLK_LAST = 323 };                            /* SDL_keysym.h: SDLK_LAST */
static int g_repeat_delay, g_repeat_interval;
static uint32_t g_keystate;                              /* the guest's SDLK_LAST bytes */
static void *g_module_handle;                            /* SDL_SetModuleHandle */

static int s12_key(SDL_Keycode k) {
    if (k >= 32 && k <= 126) return (int)k;
    switch (k) {
    case SDLK_BACKSPACE: return 8;
    case SDLK_TAB: return 9;
    case SDLK_RETURN: return 13;
    case SDLK_ESCAPE: return 27;
    case SDLK_DELETE: return 127;
    case SDLK_UP: return 273; case SDLK_DOWN: return 274;
    case SDLK_RIGHT: return 275; case SDLK_LEFT: return 276;
    case SDLK_INSERT: return 277; case SDLK_HOME: return 278; case SDLK_END: return 279;
    case SDLK_PAGEUP: return 280; case SDLK_PAGEDOWN: return 281;
    case SDLK_F1: return 282; case SDLK_F2: return 283; case SDLK_F3: return 284;
    case SDLK_F4: return 285; case SDLK_F5: return 286; case SDLK_F6: return 287;
    case SDLK_F7: return 288; case SDLK_F8: return 289; case SDLK_F9: return 290;
    case SDLK_F10: return 291; case SDLK_F11: return 292; case SDLK_F12: return 293;
    case SDLK_F13: return 294; case SDLK_F14: return 295; case SDLK_F15: return 296;
    case SDLK_KP_0: return 256; case SDLK_KP_1: return 257; case SDLK_KP_2: return 258;
    case SDLK_KP_3: return 259; case SDLK_KP_4: return 260; case SDLK_KP_5: return 261;
    case SDLK_KP_6: return 262; case SDLK_KP_7: return 263; case SDLK_KP_8: return 264;
    case SDLK_KP_9: return 265; case SDLK_KP_PERIOD: return 266; case SDLK_KP_DIVIDE: return 267;
    case SDLK_KP_MULTIPLY: return 268; case SDLK_KP_MINUS: return 269; case SDLK_KP_PLUS: return 270;
    case SDLK_KP_ENTER: return 271; case SDLK_KP_EQUALS: return 272;
    case SDLK_NUMLOCKCLEAR: return 300; case SDLK_CAPSLOCK: return 301; case SDLK_SCROLLLOCK: return 302;
    case SDLK_RSHIFT: return 303; case SDLK_LSHIFT: return 304;
    case SDLK_RCTRL: return 305; case SDLK_LCTRL: return 306;
    case SDLK_RALT: return 307; case SDLK_LALT: return 308;
    case SDLK_LGUI: return 311; case SDLK_RGUI: return 312;
    case SDLK_MODE: return 313;
    case SDLK_HELP: return 315; case SDLK_PRINTSCREEN: return 316; case SDLK_SYSREQ: return 317;
    case SDLK_PAUSE: return 19;
    case SDLK_MENU: return 319; case SDLK_POWER: return 320; case SDLK_UNDO: return 322;
    default: return 0;                                   /* SDLK_UNKNOWN */
    }
}
/* The character a key produces with the modifiers held, for keysym.unicode
 * (SDL 1.2 asks the OS; this is the US layout the games assume). */
static int shift_us(int c) {
    switch (c) {
    case '1': return '!'; case '2': return '@'; case '3': return '#'; case '4': return '$';
    case '5': return '%'; case '6': return '^'; case '7': return '&'; case '8': return '*';
    case '9': return '('; case '0': return ')'; case '-': return '_'; case '=': return '+';
    case '[': return '{'; case ']': return '}'; case '\\': return '|'; case ';': return ':';
    case '\'': return '"'; case ',': return '<'; case '.': return '>'; case '/': return '?';
    case '`': return '~';
    default: return c;
    }
}
static uint16_t unicode_of(SDL_Keycode k, SDL_Keymod mod) {
    if (k < 32 || k > 126) return 0;
    int shift = (mod & KMOD_SHIFT) != 0;
    if (k >= 'a' && k <= 'z') return (uint16_t)(shift ? k - 32 : k);
    if (k >= 'A' && k <= 'Z') return (uint16_t)(shift ? k : k + 32);
    return (uint16_t)(shift ? shift_us((int)k) : (int)k);
}
/* Refresh the guest's key-state array.  Two sources, as SDL 1.2 has: SDL2's own
 * keyboard state (real input) and the key events this shim has delivered - SDL
 * 1.2's SDL_PrivateKeyboard sets SDL_KeyState as events are processed, so an
 * event the guest pushed through SDL_PushEvent counts too. */
static uint8_t g_evt_down[S12_SDLK_LAST];
static void keystate_note(int sym, int down) { if (sym > 0 && sym < S12_SDLK_LAST) g_evt_down[sym] = down ? 1 : 0; }
static void refresh_keystate(void) {
    if (!g_keystate) return;
    memset(hle_ptr(g_keystate), 0, S12_SDLK_LAST);
    int n = 0;
    const Uint8 *ks = SDL_GetKeyboardState(&n);
    for (int sc = 0; sc < n; sc++) {
        if (!ks[sc]) continue;
        int sym = s12_key(SDL_GetKeyFromScancode((SDL_Scancode)sc));
        if (sym > 0 && sym < S12_SDLK_LAST) MEM8(g_keystate + (uint32_t)sym) = 1;
    }
    for (int i = 0; i < S12_SDLK_LAST; i++) if (g_evt_down[i]) MEM8(g_keystate + (uint32_t)i) = 1;
}
static void s_GetKeyState(void) {
    if (!g_keystate) g_keystate = hle_va(hle_alloc(S12_SDLK_LAST));
    SDL_PumpEvents();
    refresh_keystate();
    if (A32(0)) MEM32(A32(0)) = S12_SDLK_LAST;
    RETC(g_keystate);
}
static void s_GetModState(void) { RETC((uint32_t)SDL_GetModState()); }
static void s_GetMouseState(void) {
    int x = 0, y = 0;
    uint32_t b = SDL_GetMouseState(&x, &y);
    if (A32(0)) MEM32(A32(0)) = (uint32_t)x;
    if (A32(1)) MEM32(A32(1)) = (uint32_t)y;
    RETC(b);
}
static void s_GetRelativeMouseState(void) {
    int x = 0, y = 0;
    uint32_t b = SDL_GetRelativeMouseState(&x, &y);
    if (A32(0)) MEM32(A32(0)) = (uint32_t)x;
    if (A32(1)) MEM32(A32(1)) = (uint32_t)y;
    RETC(b);
}
/* SDL_GetKeyName answers SDL 1.2's own names (SDL_keyboard.c's SDL_Keynames):
 * the character itself for the ASCII keys, lowercase words for the rest.  A key
 * with no SDL 1.2 name of its own falls back to SDL2's, which is still a real
 * name for it. */
static const char *s12_key_name(int sym) {
    static char one[2];
    if (sym >= 32 && sym <= 126) { one[0] = (char)sym; one[1] = 0; return one; }
    switch (sym) {
    case 8: return "backspace"; case 9: return "tab"; case 12: return "clear";
    case 13: return "return"; case 19: return "pause"; case 27: return "escape"; case 127: return "delete";
    case 273: return "up"; case 274: return "down"; case 275: return "right"; case 276: return "left";
    case 277: return "insert"; case 278: return "home"; case 279: return "end";
    case 280: return "page up"; case 281: return "page down";
    case 282: return "f1"; case 283: return "f2"; case 284: return "f3"; case 285: return "f4";
    case 286: return "f5"; case 287: return "f6"; case 288: return "f7"; case 289: return "f8";
    case 290: return "f9"; case 291: return "f10"; case 292: return "f11"; case 293: return "f12";
    case 294: return "f13"; case 295: return "f14"; case 296: return "f15";
    case 256: return "kp 0"; case 257: return "kp 1"; case 258: return "kp 2"; case 259: return "kp 3";
    case 260: return "kp 4"; case 261: return "kp 5"; case 262: return "kp 6"; case 263: return "kp 7";
    case 264: return "kp 8"; case 265: return "kp 9"; case 266: return "kp period"; case 267: return "kp divide";
    case 268: return "kp multiply"; case 269: return "kp minus"; case 270: return "kp plus";
    case 271: return "kp enter"; case 272: return "kp equals";
    case 300: return "num lock"; case 301: return "caps lock"; case 302: return "scroll lock";
    case 303: return "right shift"; case 304: return "left shift";
    case 305: return "right ctrl"; case 306: return "left ctrl";
    case 307: return "right alt"; case 308: return "left alt";
    case 309: return "right meta"; case 310: return "left meta";
    case 311: return "left super"; case 312: return "right super";
    case 313: return "mode"; case 314: return "compose";
    case 315: return "help"; case 316: return "print screen"; case 317: return "sys req";
    case 318: return "break"; case 319: return "menu"; case 320: return "power";
    case 321: return "euro"; case 322: return "undo";
    default: return NULL;
    }
}
static void s_GetKeyName(void) {
    int sym = (int)A32(0);
    const char *n = s12_key_name(sym);
    if (!n) {
        SDL_Keycode k = 0;                               /* SDL 1.2 value back to SDL2's for the fallback */
        for (int c = 0; c < 0x40000100 && !k; c++) if (s12_key((SDL_Keycode)c) == sym) k = (SDL_Keycode)c;
        n = k ? SDL_GetKeyName(k) : SDL_GetKeyName((SDL_Keycode)sym);
    }
    RETC(hle_guest_strdup(n ? n : "unknown"));
}
static void s_EnableUNICODE(void) { int was = g_unicode; g_unicode = (int)A32(0); RETC(was); }
/* SDL_EnableKeyRepeat(delay, interval): SDL 1.2 generates the repeats itself, so
 * these set how fast.  SDL2 has no such knob - the platform's key repeat
 * arrives as repeated SDL_KEYDOWN events - so the values are recorded (which is
 * what a later call reads back as the previous setting) and the repeats
 * themselves are whatever SDL2 delivers. */
static void s_EnableKeyRepeat(void) {
    int was = g_repeat_delay;
    g_repeat_delay = (int)A32(0);
    g_repeat_interval = (int)A32(1);
    RETC(was);
}
static void s_StartTextInput(void) { SDL_StartTextInput(); RETCV(); }
static void s_StopTextInput(void) { SDL_StopTextInput(); RETCV(); }

/* ---- joysticks ----
 * SDL2's SDL_Joystick * is a host pointer, so the guest gets a handle in guest
 * memory instead; the slot table maps it back. */
typedef struct { uint32_t va; SDL_Joystick *joy; int index; } joy_slot;
static joy_slot g_joy[8];
static void joy_init(void) { if (!SDL_WasInit(SDL_INIT_JOYSTICK)) SDL_InitSubSystem(SDL_INIT_JOYSTICK); }
static joy_slot *joy_of(uint32_t va) {
    if (!va) return NULL;
    for (int i = 0; i < (int)(sizeof g_joy / sizeof g_joy[0]); i++) if (g_joy[i].va == va && g_joy[i].joy) return &g_joy[i];
    return NULL;
}
static void s_NumJoysticks(void) { joy_init(); RETC((uint32_t)SDL_NumJoysticks()); }
static void s_JoystickName(void) {
    joy_init();
    const char *n = SDL_JoystickNameForIndex((int)A32(0));
    RETC(n ? hle_guest_strdup(n) : 0);
}
static void s_JoystickOpen(void) {
    joy_init();
    int index = (int)A32(0);
    SDL_Joystick *j = SDL_JoystickOpen(index);
    if (!j) { error(SDL_GetError()); RETC(0); }
    for (int i = 0; i < (int)(sizeof g_joy / sizeof g_joy[0]); i++)
        if (!g_joy[i].joy) {
            uint32_t va = hle_va(hle_alloc(8));
            if (!va) { SDL_JoystickClose(j); error("out of guest memory"); RETC(0); }
            MEM32(va) = (uint32_t)index; MEM32(va + 4) = 1;
            g_joy[i].va = va; g_joy[i].joy = j; g_joy[i].index = index;
            RETC(va);
        }
    SDL_JoystickClose(j);
    error("SDL_JoystickOpen: too many open joysticks");
    RETC(0);
}
static void s_JoystickOpened(void) {
    int index = (int)A32(0), open = 0;
    for (int i = 0; i < (int)(sizeof g_joy / sizeof g_joy[0]); i++) if (g_joy[i].joy && g_joy[i].index == index) open = 1;
    RETC((uint32_t)open);
}
static void s_JoystickIndex(void) { joy_slot *s = joy_of(A32(0)); RETC(s ? (uint32_t)s->index : (uint32_t)-1); }
static void s_JoystickNumAxes(void)    { joy_slot *s = joy_of(A32(0)); RETC(s ? (uint32_t)SDL_JoystickNumAxes(s->joy) : 0u); }
static void s_JoystickNumBalls(void)   { joy_slot *s = joy_of(A32(0)); RETC(s ? (uint32_t)SDL_JoystickNumBalls(s->joy) : 0u); }
static void s_JoystickNumHats(void)    { joy_slot *s = joy_of(A32(0)); RETC(s ? (uint32_t)SDL_JoystickNumHats(s->joy) : 0u); }
static void s_JoystickNumButtons(void) { joy_slot *s = joy_of(A32(0)); RETC(s ? (uint32_t)SDL_JoystickNumButtons(s->joy) : 0u); }
static void s_JoystickUpdate(void) { SDL_JoystickUpdate(); RETCV(); }
static void s_JoystickEventState(void) { RETC((uint32_t)SDL_JoystickEventState((int)A32(0))); }
static void s_JoystickGetAxis(void) { joy_slot *s = joy_of(A32(0)); RETC(s ? (uint32_t)(int32_t)SDL_JoystickGetAxis(s->joy, (int)A32(1)) : 0u); }
static void s_JoystickGetHat(void) { joy_slot *s = joy_of(A32(0)); RETC(s ? SDL_JoystickGetHat(s->joy, (int)A32(1)) : 0u); }
static void s_JoystickGetButton(void) { joy_slot *s = joy_of(A32(0)); RETC(s ? SDL_JoystickGetButton(s->joy, (int)A32(1)) : 0u); }
static void s_JoystickGetBall(void) {                     /* (joystick, ball, int *dx, int *dy) */
    joy_slot *s = joy_of(A32(0));
    if (!s) RETC(-1);
    int dx = 0, dy = 0;
    int rc = SDL_JoystickGetBall(s->joy, (int)A32(1), &dx, &dy);
    if (rc == 0) {
        if (A32(2)) MEM32(A32(2)) = (uint32_t)dx;
        if (A32(3)) MEM32(A32(3)) = (uint32_t)dy;
    }
    RETC((uint32_t)rc);
}
static void s_JoystickClose(void) {
    joy_slot *s = joy_of(A32(0));
    if (!s) RETCV();
    SDL_JoystickClose(s->joy);
    s->joy = NULL; s->va = 0; s->index = -1;
    RETCV();
}
/* SDL_main.h: "This should be called from your WinMain()".  SDL keeps the
 * instance handle to find its own resources; nothing in this layer loads SDL's
 * resources from the guest module, so recording it is the whole of its effect. */
static void s_SetModuleHandle(void) { g_module_handle = APTR(0); RETCV(); }
static void s_WarpMouse(void) { SDL_WarpMouseInWindow(g_window,(int)A32(0),(int)A32(1)); RETCV(); }

const win32hle_shim win32hle_sdl12[] = {
    { "SDL_Init", s_Init }, { "SDL_InitSubSystem", s_InitSubSystem }, { "SDL_Quit", s_Quit }, { "SDL_QuitSubSystem", s_QuitSubSystem }, { "SDL_WasInit", s_WasInit },
    { "SDL_GetError", s_GetError }, { "SDL_ClearError", s_ClearError }, { "SDL_GetTicks", s_GetTicks }, { "SDL_Delay", s_Delay },
    { "SDL_SetVideoMode", s_SetVideoMode }, { "SDL_GetVideoSurface", s_GetVideoSurface }, { "SDL_Flip", s_Flip }, { "SDL_UpdateRect", s_UpdateRect }, { "SDL_UpdateRects", s_UpdateRects },
    { "SDL_CreateRGBSurface", s_CreateRGBSurface }, { "SDL_CreateRGBSurfaceFrom", s_CreateRGBSurfaceFrom }, { "SDL_FreeSurface", s_FreeSurface }, { "SDL_LockSurface", s_LockSurface }, { "SDL_UnlockSurface", s_UnlockSurface }, { "SDL_FillRect", s_FillRect }, { "SDL_BlitSurface", s_BlitSurface }, { "SDL_UpperBlit", s_UpperBlit },
    { "SDL_PollEvent", s_PollEvent }, { "SDL_PumpEvents", s_PumpEvents }, { "SDL_ShowCursor", s_ShowCursor }, { "SDL_WarpMouse", s_WarpMouse },
    { "SDL_RWFromFile", s_RWFromFile }, { "SDL_RWFromMem", s_RWFromMem }, { "SDL_RWseek", s_RWseek }, { "SDL_RWread", s_RWread },
    { "SDL_RWwrite", s_RWwrite }, { "SDL_RWclose", s_RWclose }, { "SDL_RWtell", s_RWtell },
    { "SDL_LoadBMP_RW", s_LoadBMP_RW }, { "SDL_SaveBMP_RW", s_SaveBMP_RW }, { "SDL_LoadWAV_RW", s_LoadWAV_RW }, { "SDL_FreeWAV", s_FreeWAV },
    { "SDL_MapRGB", s_MapRGB }, { "SDL_MapRGBA", s_MapRGBA }, { "SDL_GetRGB", s_GetRGB }, { "SDL_GetRGBA", s_GetRGBA },
    { "SDL_DisplayFormat", s_DisplayFormat }, { "SDL_DisplayFormatAlpha", s_DisplayFormatAlpha }, { "SDL_ConvertSurface", s_ConvertSurface },
    { "SDL_SetColorKey", s_SetColorKey }, { "SDL_SetAlpha", s_SetAlpha }, { "SDL_SetPalette", s_SetPalette }, { "SDL_SetColors", s_SetColors },
    { "SDL_GL_SetAttribute", s_GL_SetAttribute }, { "SDL_GL_GetAttribute", s_GL_GetAttribute }, { "SDL_GL_GetProcAddress", s_GL_GetProcAddress },
    { "SDL_GL_SwapBuffers", s_GL_SwapBuffers }, { "SDL_GL_LoadLibrary", s_GL_LoadLibrary }, { "SDL_GL_UnloadLibrary", s_GL_UnloadLibrary },
    { "SDL_GL_SetSwapInterval", s_GL_SetSwapInterval }, { "SDL_GL_GetSwapInterval", s_GL_GetSwapInterval },
    { "SDL_GetKeyState", s_GetKeyState }, { "SDL_GetModState", s_GetModState }, { "SDL_GetMouseState", s_GetMouseState },
    { "SDL_GetRelativeMouseState", s_GetRelativeMouseState }, { "SDL_GetKeyName", s_GetKeyName },
    { "SDL_EnableUNICODE", s_EnableUNICODE }, { "SDL_EnableKeyRepeat", s_EnableKeyRepeat },
    { "SDL_StartTextInput", s_StartTextInput }, { "SDL_StopTextInput", s_StopTextInput },
    { "SDL_NumJoysticks", s_NumJoysticks }, { "SDL_JoystickName", s_JoystickName }, { "SDL_JoystickOpen", s_JoystickOpen },
    { "SDL_JoystickOpened", s_JoystickOpened }, { "SDL_JoystickIndex", s_JoystickIndex },
    { "SDL_JoystickNumAxes", s_JoystickNumAxes }, { "SDL_JoystickNumBalls", s_JoystickNumBalls },
    { "SDL_JoystickNumHats", s_JoystickNumHats }, { "SDL_JoystickNumButtons", s_JoystickNumButtons },
    { "SDL_JoystickUpdate", s_JoystickUpdate }, { "SDL_JoystickEventState", s_JoystickEventState },
    { "SDL_JoystickGetAxis", s_JoystickGetAxis }, { "SDL_JoystickGetHat", s_JoystickGetHat },
    { "SDL_JoystickGetButton", s_JoystickGetButton }, { "SDL_JoystickGetBall", s_JoystickGetBall },
    { "SDL_JoystickClose", s_JoystickClose }, { "SDL_SetModuleHandle", s_SetModuleHandle },
    { "SDL_PushEvent", s_PushEvent }, { "SDL_WaitEvent", s_WaitEvent }, { "SDL_PeepEvents", s_PeepEvents },
    { "SDL_EventState", s_EventState }, { "SDL_NumEvents", s_NumEvents }, { "SDL_GetAppState", s_GetAppState },
    { "SDL_GetVideoInfo", s_GetVideoInfo }, { "SDL_ListModes", s_ListModes }, { "SDL_VideoModeOK", s_VideoModeOK },
    { "SDL_WM_SetCaption", s_WM_SetCaption }, { "SDL_WM_GetCaption", s_WM_GetCaption }, { "SDL_WM_SetIcon", s_WM_SetIcon },
    { "SDL_WM_IconifyWindow", s_WM_IconifyWindow }, { "SDL_WM_ToggleFullScreen", s_WM_ToggleFullScreen },
    { "SDL_WM_GrabInput", s_WM_GrabInput },
    { "SDL_OpenAudio", s_OpenAudio }, { "SDL_CloseAudio", s_CloseAudio }, { "SDL_PauseAudio", s_PauseAudio },
    { "SDL_LockAudio", s_LockAudio }, { "SDL_UnlockAudio", s_UnlockAudio }, { "SDL_GetAudioStatus", s_GetAudioStatus },
    { "SDL_BuildAudioCVT", s_BuildAudioCVT }, { "SDL_ConvertAudio", s_ConvertAudio }, { "SDL_MixAudio", s_MixAudio },
    { "SDL_SetTimer", s_SetTimer }, { "SDL_AddTimer", s_AddTimer }, { "SDL_RemoveTimer", s_RemoveTimer },
    { "SDL_CreateThread", s_CreateThread }, { "SDL_WaitThread", s_WaitThread }, { "SDL_ThreadID", s_ThreadID },
    { "SDL_GetThreadID", s_GetThreadID },
    { "SDL_CreateMutex", s_CreateMutex }, { "SDL_LockMutex", s_LockMutex }, { "SDL_mutexP", s_LockMutex },
    { "SDL_UnlockMutex", s_UnlockMutex }, { "SDL_mutexV", s_UnlockMutex }, { "SDL_DestroyMutex", s_DestroyMutex },
    { "SDL_CreateSemaphore", s_CreateSemaphore }, { "SDL_DestroySemaphore", s_DestroySemaphore },
    { "SDL_SemWait", s_SemWait }, { "SDL_SemTryWait", s_SemTryWait }, { "SDL_SemWaitTimeout", s_SemWaitTimeout },
    { "SDL_SemPost", s_SemPost }, { "SDL_SemValue", s_SemValue },
    { "SDL_CreateCond", s_CreateCond }, { "SDL_DestroyCond", s_DestroyCond }, { "SDL_CondSignal", s_CondSignal },
    { "SDL_CondBroadcast", s_CondBroadcast }, { "SDL_CondWait", s_CondWait }, { "SDL_CondWaitTimeout", s_CondWaitTimeout },
    { 0, 0 }
};
