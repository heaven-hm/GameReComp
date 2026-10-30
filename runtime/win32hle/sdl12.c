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
static uint32_t g_video;
static char g_error[256];

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
static void s_Quit(void) { if (g_texture) SDL_DestroyTexture(g_texture), g_texture = NULL; if (g_renderer) SDL_DestroyRenderer(g_renderer), g_renderer = NULL; if (g_window) SDL_DestroyWindow(g_window), g_window = NULL; SDL_Quit(); RETCV(); }
static void s_QuitSubSystem(void) { SDL_QuitSubSystem(A32(0)); RETCV(); }
static void s_WasInit(void) { RETC(SDL_WasInit(A32(0))); }
static void s_GetError(void) { RETC(hle_guest_strdup(g_error[0] ? g_error : SDL_GetError())); }
static void s_ClearError(void) { g_error[0] = 0; SDL_ClearError(); RETCV(); }
static void s_GetTicks(void) { RETC(SDL_GetTicks()); }
static void s_Delay(void) { SDL_Delay(A32(0)); RETCV(); }

static void s_SetVideoMode(void) {
    int w = (int)A32(0), h = (int)A32(1), bpp = (int)A32(2);
    if (!SDL_WasInit(SDL_INIT_VIDEO) && SDL_InitSubSystem(SDL_INIT_VIDEO) != 0) { error(SDL_GetError()); RETC(0); }
    if (g_texture) SDL_DestroyTexture(g_texture), g_texture = NULL;
    if (g_renderer) SDL_DestroyRenderer(g_renderer), g_renderer = NULL;
    if (g_window) SDL_DestroyWindow(g_window), g_window = NULL;
    g_window = SDL_CreateWindow("SDL application", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, w, h,
                                (A32(3) & S12_FULLSCREEN) ? SDL_WINDOW_FULLSCREEN_DESKTOP : 0);
    if (!g_window) { error(SDL_GetError()); RETC(0); }
    g_renderer = SDL_CreateRenderer(g_window, -1, SDL_RENDERER_SOFTWARE);
    if (!g_renderer) { error(SDL_GetError()); RETC(0); }
    uint32_t fmt = bpp == 16 ? SDL_PIXELFORMAT_RGB565 : SDL_PIXELFORMAT_ARGB8888;
    g_texture = SDL_CreateTexture(g_renderer, fmt, SDL_TEXTUREACCESS_STREAMING, w, h);
    if (!g_texture) { error(SDL_GetError()); RETC(0); }
    g_video = new_surface(w, h, bpp == 16 ? 16 : 32, bpp == 16 ? 0xF800u : 0x00FF0000u,
                          bpp == 16 ? 0x07E0u : 0x0000FF00u, bpp == 16 ? 0x001Fu : 0x000000FFu, bpp == 16 ? 0 : 0xFF000000u, 0, 1);
    RETC(g_video);
}
static void s_Flip(void) { if (A32(0) == g_video) present(); RETC(0); }
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
    case SDL_KEYUP:
        MEM8(out) = (uint8_t)(e->type == SDL_KEYDOWN ? S12_KEYDOWN : S12_KEYUP);
        MEM8(out + 1) = 0;                       /* which: the keyboard device */
        MEM8(out + 2) = e->key.state;            /* SDL_PRESSED / SDL_RELEASED */
        MEM8(out + 4) = (uint8_t)e->key.keysym.scancode;
        put16(out + 8, (uint16_t)e->key.keysym.sym);
        put16(out + 12, (uint16_t)SDL_GetModState());
        put16(out + 16, e->key.keysym.sym < 128 ? (uint16_t)e->key.keysym.sym : 0);
        break;
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
    { 0, 0 }
};
