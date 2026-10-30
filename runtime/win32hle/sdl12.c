/* SDL 1.2 imports implemented on SDL2 for lifted 32-bit games.
 *
 * SDL 1.2's Windows exports are cdecl, unlike the Win32 API.  Every shim in
 * this file therefore uses RETC: it pops only the synthetic return address and
 * leaves the caller's arguments for the lifted cdecl caller to remove.
 */
#include <stdint.h>
#include <string.h>
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
    /* SDL_PixelFormat's masks are 32-bit aligned after the ten byte fields. */
    MEM32(f + S12_FMT_RMASK) = rmask; MEM32(f + S12_FMT_GMASK) = gmask;
    MEM32(f + S12_FMT_BMASK) = bmask; MEM32(f + S12_FMT_AMASK) = amask;
    if (bpp == 8) {
        /* "If the depth is 4 or 8 bits, an empty palette is allocated" (SDL_video.h). */
        uint32_t pal = hle_va(hle_alloc(S12_PALETTE));
        uint32_t colors = hle_va(hle_alloc(256 * S12_COLOR));
        if (!pal || !colors) { error("out of guest memory"); return 0; }
        MEM32(pal) = 256; MEM32(pal + 4) = colors;
        MEM32(f + S12_FMT_PALETTE) = pal;
        MEM32(f + S12_FMT_RMASK) = 0; MEM32(f + S12_FMT_GMASK) = 0;
        MEM32(f + S12_FMT_BMASK) = 0; MEM32(f + S12_FMT_AMASK) = 0;
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
static void present(void) {
    if (!g_video || !g_renderer || !g_texture) return;
    if (SDL_UpdateTexture(g_texture, NULL, hle_ptr(sf_pixels(g_video)), sf_pitch(g_video)) != 0) { error(SDL_GetError()); return; }
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
static void s_BlitSurface(void) { int sx,sy,sw,sh,dx,dy,dw,dh; uint32_t src=A32(0), dst=A32(2); if(!src||!dst||sf_bpp(src)!=sf_bpp(dst)) RETC(-1); rect(A32(1),&sx,&sy,&sw,&sh,src); rect(A32(3),&dx,&dy,&dw,&dh,dst); if(sw>dw) sw=dw; if(sh>dh) sh=dh; for(int y=0;y<sh;y++) for(int x=0;x<sw;x++) pixel_write(dst,dx+x,dy+y,pixel_read(src,sx+x,sy+y)); if(A32(3)) { put16(A32(3),(uint16_t)dx); put16(A32(3)+2,(uint16_t)dy); } RETC(0); }
static void s_UpperBlit(void) { s_BlitSurface(); }

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
    { "SDL_PollEvent", s_PollEvent }, { "SDL_PumpEvents", s_PumpEvents }, { "SDL_ShowCursor", s_ShowCursor }, { "SDL_WarpMouse", s_WarpMouse }, { 0, 0 }
};
