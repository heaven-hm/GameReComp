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
static void s_GetTicks(void) { RETC(SDL_GetTicks()); }
static void s_Delay(void) { SDL_Delay(A32(0)); RETCV(); }

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
static void s_WM_SetCaption(void) { if (g_window && ASTR(0)) SDL_SetWindowTitle(g_window, ASTR(0)); RETCV(); }
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
static void put_event(uint32_t out, const SDL_Event *e) {
    if (!out) return;
    for (int i = 0; i < S12_EVENT_SIZE; i++) MEM8(out + (uint32_t)i) = 0;
    switch (e->type) {
    case SDL_QUIT:
        MEM8(out) = S12_QUIT;
        break;
    case SDL_KEYDOWN:
    case SDL_KEYUP: {
        SDL_Keymod mod = SDL_GetModState();
        int sym = s12_key(e->key.keysym.sym);
        keystate_note(sym, e->type == SDL_KEYDOWN);
        MEM8(out) = (uint8_t)(e->type == SDL_KEYDOWN ? S12_KEYDOWN : S12_KEYUP);
        MEM8(out + 1) = 0;                       /* which: the keyboard device */
        MEM8(out + 2) = e->key.state;            /* SDL_PRESSED / SDL_RELEASED */
        MEM8(out + 4) = (uint8_t)e->key.keysym.scancode;   /* SDL2's scancode: USB usage, not SDL 1.2's set 1 */
        put16(out + 8, (uint16_t)sym);
        put16(out + 12, (uint16_t)mod);
        put16(out + 16, g_unicode ? unicode_of(e->key.keysym.sym, mod) : 0);
        break;
    }
    case SDL_MOUSEMOTION:
        MEM8(out) = S12_MOUSEMOTION;
        MEM8(out + 1) = 0;
        MEM8(out + 2) = e->motion.state;
        put16(out + 4, (uint16_t)e->motion.x); put16(out + 6, (uint16_t)e->motion.y);
        put16(out + 8, (uint16_t)e->motion.xrel); put16(out + 10, (uint16_t)e->motion.yrel);
        break;
    case SDL_MOUSEBUTTONDOWN:
    case SDL_MOUSEBUTTONUP:
        MEM8(out) = (uint8_t)(e->type == SDL_MOUSEBUTTONDOWN ? S12_MOUSEBUTTONDOWN : S12_MOUSEBUTTONUP);
        MEM8(out + 1) = 0;
        MEM8(out + 2) = e->button.button;
        MEM8(out + 3) = e->button.state;
        put16(out + 4, (uint16_t)e->button.x); put16(out + 6, (uint16_t)e->button.y);
        break;
    case SDL_JOYAXISMOTION:
        MEM8(out) = S12_JOYAXISMOTION;
        MEM8(out + 1) = e->jaxis.which; MEM8(out + 2) = e->jaxis.axis;
        put16(out + 4, (uint16_t)e->jaxis.value);
        break;
    case SDL_JOYBALLMOTION:
        MEM8(out) = S12_JOYBALLMOTION;
        MEM8(out + 1) = e->jball.which; MEM8(out + 2) = e->jball.ball;
        put16(out + 4, (uint16_t)e->jball.xrel); put16(out + 6, (uint16_t)e->jball.yrel);
        break;
    case SDL_JOYHATMOTION:
        MEM8(out) = S12_JOYHATMOTION;
        MEM8(out + 1) = e->jhat.which; MEM8(out + 2) = e->jhat.hat; MEM8(out + 3) = e->jhat.value;
        break;
    case SDL_JOYBUTTONDOWN:
    case SDL_JOYBUTTONUP:
        MEM8(out) = (uint8_t)(e->type == SDL_JOYBUTTONDOWN ? S12_JOYBUTTONDOWN : S12_JOYBUTTONUP);
        MEM8(out + 1) = e->jbutton.which; MEM8(out + 2) = e->jbutton.button; MEM8(out + 3) = e->jbutton.state;
        break;
    default:
        MEM8(out) = S12_NOEVENT;
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
static void s_PollEvent(void) {
    SDL_Event e;
    if (!SDL_PollEvent(&e)) RETC(0);
    put_event(A32(0), &e);
    RETC(1);
}
static void s_PumpEvents(void) { SDL_PumpEvents(); RETCV(); }
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
    { "SDL_SetVideoMode", s_SetVideoMode }, { "SDL_GetVideoSurface", s_GetVideoSurface }, { "SDL_WM_SetCaption", s_WM_SetCaption }, { "SDL_Flip", s_Flip }, { "SDL_UpdateRect", s_UpdateRect }, { "SDL_UpdateRects", s_UpdateRects },
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
    { 0, 0 }
};
