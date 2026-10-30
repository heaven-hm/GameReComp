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

enum { S12_SURFACE = 56, S12_FORMAT = 40 };
enum { S12_SWSURFACE = 0, S12_HWSURFACE = 1, S12_FULLSCREEN = 0x80000000u };
enum { S12_QUIT = 12, S12_KEYDOWN = 2, S12_KEYUP = 3, S12_MOUSEMOTION = 4,
       S12_MOUSEBUTTONDOWN = 5, S12_MOUSEBUTTONUP = 6 };

static SDL_Window *g_window;
static SDL_Renderer *g_renderer;
static SDL_Texture *g_texture;
static uint32_t g_video;
static char g_error[256];

static void error(const char *s) { snprintf(g_error, sizeof g_error, "%s", s ? s : "SDL error"); }
static void put16(uint32_t p, uint16_t v) { MEM8(p) = (uint8_t)v; MEM8(p + 1) = (uint8_t)(v >> 8); }
static uint16_t get16(uint32_t p) { return (uint16_t)(MEM8(p) | ((uint16_t)MEM8(p + 1) << 8)); }
static uint32_t sf_format(uint32_t s) { return MEM32(s + 4); }
static int sf_w(uint32_t s) { return (int)(int16_t)get16(s + 8); }
static int sf_h(uint32_t s) { return (int)(int16_t)get16(s + 10); }
static int sf_pitch(uint32_t s) { return (int)get16(s + 12); }
static int sf_bpp(uint32_t s) { return MEM8(sf_format(s) + 5); }

static uint32_t new_surface(int w, int h, int bpp, uint32_t rmask, uint32_t gmask, uint32_t bmask, uint32_t amask, uint32_t pixels, int own_pixels) {
    if (w <= 0 || h <= 0 || (bpp != 16 && bpp != 24 && bpp != 32)) { error("unsupported SDL surface format"); return 0; }
    int pitch = (w * bpp + 7) / 8;
    if (!pixels) { pixels = hle_va(hle_alloc((uint32_t)(pitch * h))); own_pixels = 1; }
    uint32_t s = hle_va(hle_alloc(S12_SURFACE));
    uint32_t f = hle_va(hle_alloc(S12_FORMAT));
    if (!s || !f || !pixels) { error("out of guest memory"); return 0; }
    MEM32(s) = S12_SWSURFACE;
    MEM32(s + 4) = f;
    put16(s + 8, (uint16_t)w); put16(s + 10, (uint16_t)h); put16(s + 12, (uint16_t)pitch);
    MEM32(s + 16) = pixels;
    MEM32(s + 36) = own_pixels ? 1u : 0u;              /* unused1: ownership for this shim */
    MEM32(s + 52) = 1;
    MEM8(f + 4) = (uint8_t)bpp; MEM8(f + 5) = (uint8_t)(bpp / 8);
    /* SDL_PixelFormat's masks are 32-bit aligned after the ten byte fields. */
    MEM32(f + 16) = rmask; MEM32(f + 20) = gmask; MEM32(f + 24) = bmask; MEM32(f + 28) = amask;
    return s;
}

static uint32_t pixel_read(uint32_t s, int x, int y) {
    uint32_t p = MEM32(s + 16) + (uint32_t)(y * sf_pitch(s) + x * sf_bpp(s));
    if (sf_bpp(s) == 2) return get16(p);
    if (sf_bpp(s) == 3) return MEM8(p) | ((uint32_t)MEM8(p + 1) << 8) | ((uint32_t)MEM8(p + 2) << 16);
    return MEM32(p);
}
static void pixel_write(uint32_t s, int x, int y, uint32_t v) {
    uint32_t p = MEM32(s + 16) + (uint32_t)(y * sf_pitch(s) + x * sf_bpp(s));
    if (sf_bpp(s) == 2) put16(p, (uint16_t)v);
    else if (sf_bpp(s) == 3) MEM8(p) = (uint8_t)v, MEM8(p + 1) = (uint8_t)(v >> 8), MEM8(p + 2) = (uint8_t)(v >> 16);
    else MEM32(p) = v;
}
static void rect(uint32_t r, int *x, int *y, int *w, int *h, uint32_t s) {
    *x = 0, *y = 0, *w = sf_w(s), *h = sf_h(s);
    if (r) { *x = (int)(int16_t)get16(r); *y = (int)(int16_t)get16(r + 2); *w = (int)(int16_t)get16(r + 4); *h = (int)(int16_t)get16(r + 6); }
    if (*x < 0) *w += *x, *x = 0;
    if (*y < 0) *h += *y, *y = 0;
    if (*x + *w > sf_w(s)) *w = sf_w(s) - *x;
    if (*y + *h > sf_h(s)) *h = sf_h(s) - *y;
    if (*w < 0) *w = 0; if (*h < 0) *h = 0;
}
static void present(void) {
    if (!g_video || !g_renderer || !g_texture) return;
    if (SDL_UpdateTexture(g_texture, NULL, hle_ptr(MEM32(g_video + 16)), sf_pitch(g_video)) != 0) { error(SDL_GetError()); return; }
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
static void s_FreeSurface(void) { uint32_t s = A32(0); if (s) { if (MEM32(s + 36)) hle_free(hle_ptr(MEM32(s + 16))); hle_free(hle_ptr(sf_format(s))); hle_free(hle_ptr(s)); } RETCV(); }
static void s_LockSurface(void) { RETC(A32(0) ? 0 : -1); }
static void s_UnlockSurface(void) { RETCV(); }
static void s_FillRect(void) { int x,y,w,h; uint32_t s=A32(0); if (!s) RETC(-1); rect(A32(1),&x,&y,&w,&h,s); for(int j=0;j<h;j++) for(int i=0;i<w;i++) pixel_write(s,x+i,y+j,A32(2)); RETC(0); }
static void s_BlitSurface(void) { int sx,sy,sw,sh,dx,dy,dw,dh; uint32_t src=A32(0), dst=A32(2); if(!src||!dst||sf_bpp(src)!=sf_bpp(dst)) RETC(-1); rect(A32(1),&sx,&sy,&sw,&sh,src); rect(A32(3),&dx,&dy,&dw,&dh,dst); if(sw>dw) sw=dw; if(sh>dh) sh=dh; for(int y=0;y<sh;y++) for(int x=0;x<sw;x++) pixel_write(dst,dx+x,dy+y,pixel_read(src,sx+x,sy+y)); if(A32(3)) { put16(A32(3),(uint16_t)dx); put16(A32(3)+2,(uint16_t)dy); } RETC(0); }
static void s_UpperBlit(void) { s_BlitSurface(); }

static void s_PollEvent(void) {
    SDL_Event e; if (!SDL_PollEvent(&e)) RETC(0); uint32_t out=A32(0); if (!out) RETC(1); MEM32(out)=0;
    if(e.type==SDL_QUIT) MEM32(out)=S12_QUIT;
    else if(e.type==SDL_KEYDOWN || e.type==SDL_KEYUP) { MEM32(out)=e.type==SDL_KEYDOWN?S12_KEYDOWN:S12_KEYUP; MEM8(out+8)=e.key.state; put16(out+12,(uint16_t)e.key.keysym.sym); put16(out+14,(uint16_t)SDL_GetModState()); }
    else if(e.type==SDL_MOUSEMOTION) { MEM32(out)=S12_MOUSEMOTION; MEM8(out+8)=e.motion.state; put16(out+12,(uint16_t)e.motion.x); put16(out+14,(uint16_t)e.motion.y); put16(out+16,(uint16_t)e.motion.xrel); put16(out+18,(uint16_t)e.motion.yrel); }
    else if(e.type==SDL_MOUSEBUTTONDOWN || e.type==SDL_MOUSEBUTTONUP) { MEM32(out)=e.type==SDL_MOUSEBUTTONDOWN?S12_MOUSEBUTTONDOWN:S12_MOUSEBUTTONUP; MEM8(out+8)=e.button.button; MEM8(out+9)=e.button.state; put16(out+12,(uint16_t)e.button.x); put16(out+14,(uint16_t)e.button.y); }
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
