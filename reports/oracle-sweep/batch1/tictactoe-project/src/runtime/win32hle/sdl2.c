/* SDL 2.x imports implemented on the *existing* win32hle presentation path.
 *
 * SDL2's Windows exports are cdecl, like SDL 1.2's, so every shim here ends in
 * RETC/RETCV: it pops only the synthetic return address and leaves the argument
 * words for the lifted caller to remove.
 *
 * This module deliberately does NOT create a second window, renderer, texture
 * uploader or event pump.  A guest window is mapped onto present.c's shared
 * SDL2 window (hle_present_open); a guest renderer/texture is a guest-memory
 * object whose pixels are published on RenderPresent through
 * hle_present_surface(), which both records the frame in screen.c's sink and
 * blits it into that same shared window.  Guest handles are real win32hle
 * handles (hle_handle_alloc), and RWops are built with sdl12.c's guest ABI so
 * the already-registered SDL_RWread/seek/close shims operate on them.
 *
 * Anything with no honest mapping (a second display, a render-target readback
 * in a foreign pixel format, ...) returns SDL's documented failure value and
 * sets an SDL error via SDL_SetError, so the guest's existing SDL_GetError shim
 * reports it.  We never report success for work we did not do.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

/* present.c owns the shared frame sink; sdl12.c carries a weak fallback with
 * the same signature.  Declared here (rather than in the shared header) to keep
 * the header change to the new shim table only. */
extern void hle_present_surface(const uint8_t *px, int w, int h, int pitch, int bpp);

/* ---- the guest's SDL2 structures (32-bit Windows SDL2 layout) ----
 * These offsets are the ABI a lifted PE was compiled against.  guest structs
 * live in guest memory (hle_alloc), never on the host stack. */
enum { S2_SURFACE = 60, S2_SF_FLAGS = 0, S2_SF_FORMAT = 4, S2_SF_W = 8,
       S2_SF_H = 12, S2_SF_PITCH = 16, S2_SF_PIXELS = 20, S2_SF_CLIP = 36,
       S2_SF_REFCOUNT = 56 };
enum { S2_FORMAT = 44, S2_FMT_FORMAT = 0, S2_FMT_PALETTE = 4, S2_FMT_BPP = 8,
       S2_FMT_BYTESPP = 9, S2_FMT_RMASK = 12, S2_FMT_GMASK = 16,
       S2_FMT_BMASK = 20, S2_FMT_AMASK = 24, S2_FMT_RLOSS = 28,
       S2_FMT_GLOSS = 29, S2_FMT_BLOSS = 30, S2_FMT_ALOSS = 31,
       S2_FMT_RSHIFT = 32, S2_FMT_GSHIFT = 33, S2_FMT_BSHIFT = 34,
       S2_FMT_ASHIFT = 35, S2_FMT_REFCOUNT = 36 };
enum { S2_PALETTE = 16, S2_PAL_NCOLORS = 0, S2_PAL_COLORS = 4,
       S2_PAL_VERSION = 8, S2_PAL_REFCOUNT = 12 };
enum { S2_DISPLAYMODE = 20, S2_DM_FORMAT = 0, S2_DM_W = 4, S2_DM_H = 8,
       S2_DM_REFRESH = 12, S2_DM_DRIVERDATA = 16 };
/* Reuse sdl12.c's RWops guest layout: kind 1 -> HANDLE is a guest VA, SIZE is
 * its length, POS the cursor, OWN whether close frees it. */
enum { S2_RWOPS = 44, S2_RW_SEEK = 0, S2_RW_READ = 4, S2_RW_WRITE = 8,
       S2_RW_CLOSE = 12, S2_RW_TYPE = 16, S2_RW_KIND = 20,
       S2_RW_HANDLE = 24, S2_RW_SIZE = 28, S2_RW_POS = 32, S2_RW_OWN = 36 };
enum { S2_RWOPS_MEMORY_RO = 4, S2_RW_KIND_MEM = 1 };

/* Window flags we hand back that are true of the shared, shown window. */
#ifndef SDL_WINDOW_SHOWN
#define SDL_WINDOW_SHOWN 0x00000004u
#endif

/* ---- state ---------------------------------------------------------- */
enum { H2_WINDOW = 1, H2_RENDERER, H2_TEXTURE };
typedef struct {
    uint32_t h;                  /* our own handle, for hle_handle_close */
    int kind;
    uint32_t win;                /* owning window handle */
    int w, h_pixels;
    Uint32 format;               /* SDL_PixelFormatEnum */
    int access;
    uint32_t pixels;             /* guest VA of the pixel buffer */
    int pitch, bytespp;
    uint32_t palette;            /* guest VA of SDL_Palette, 0 when none */
    Uint32 rmask, gmask, bmask, amask;
    int rshift, gshift, bshift, ashift;
    int rloss, gloss, bloss, aloss;
    int logical_w, logical_h;
    uint32_t target;             /* 0 = the shared window's backbuffer */
    uint32_t last_tex;           /* texture last handed to SDL_RenderCopy */
    int sx, sy, sw, sh;          /* its (validated) source rectangle */
    int has_src;
    Uint32 flags;                /* SDL_WindowFlags as the guest asked */
    int id;                      /* synthetic SDL_WindowID */
    uint32_t surface_va;         /* SDL_GetWindowSurface's guest surface */
} sdl2_obj;

static sdl2_obj *g_window;       /* the shared window object */
static int g_present_opened;
static int g_main_ready;

/* Every error is both printed (an explicit diagnostic) and published through
 * SDL_SetError.  sdl12.c's SDL_GetError shim falls back to SDL_GetError() when
 * its own buffer is empty, so a lifted SDL2 program sees this message. */
static void sdl2_error(const char *fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    fprintf(stderr, "[sdl2] %s\n", buf);
    SDL_SetError("%s", buf);
}

/* A guest VA range must fit in the 32-bit address space; this bounds every
 * pointer the guest hands us before MEM* touches it. */
static int guest_ok(uint32_t va, uint64_t n) {
    if (n == 0) return 1;
    if (!va) return 0;
    return n <= (uint64_t)0x100000000ull - (uint64_t)va;
}

static uint32_t obj_new(int kind) {
    sdl2_obj *o = (sdl2_obj *)calloc(1, sizeof *o);
    if (!o) { sdl2_error("out of host memory"); return 0; }
    o->kind = kind;
    o->h = hle_handle_alloc(HLE_H_OTHER, o);
    if (!o->h) { free(o); sdl2_error("out of guest handles"); return 0; }
    return o->h;
}
static sdl2_obj *obj_of(uint32_t h, int kind) {
    sdl2_obj *o = (sdl2_obj *)hle_handle_obj(h, HLE_H_OTHER);
    return o && o->kind == kind ? o : NULL;
}
static void obj_del(uint32_t h) {
    sdl2_obj *o = (sdl2_obj *)hle_handle_obj(h, HLE_H_OTHER);
    if (!o) return;
    hle_handle_close(h);
    free(o);
}
static void need_obj(uint32_t h, int kind, const char *what) {
    if (!obj_of(h, kind)) sdl2_error("%s: invalid handle 0x%08X", what, h);
}

/* ---- pixel formats --------------------------------------------------- */
static void fmt_info(Uint32 f, int *bpp, Uint32 *rm, Uint32 *gm, Uint32 *bm,
                     Uint32 *am, int *rs, int *gs, int *bs, int *as,
                     int *rl, int *gl, int *bl, int *al) {
    if (bpp) *bpp = 0;
    if (rm) *rm = 0; if (gm) *gm = 0; if (bm) *bm = 0; if (am) *am = 0;
    if (rs) *rs = 0; if (gs) *gs = 0; if (bs) *bs = 0; if (as) *as = 0;
    if (rl) *rl = 0; if (gl) *gl = 0; if (bl) *bl = 0; if (al) *al = 0;
    SDL_PixelFormat *pf = SDL_AllocFormat(f);
    if (!pf) { sdl2_error("unsupported SDL pixel format 0x%08X", f); return; }
    if (bpp) *bpp = pf->BytesPerPixel;
    if (rm) *rm = pf->Rmask; if (gm) *gm = pf->Gmask;
    if (bm) *bm = pf->Bmask; if (am) *am = pf->Amask;
    if (rs) *rs = pf->Rshift; if (gs) *gs = pf->Gshift;
    if (bs) *bs = pf->Bshift; if (as) *as = pf->Ashift;
    if (rl) *rl = pf->Rloss; if (gl) *gl = pf->Gloss;
    if (bl) *bl = pf->Bloss; if (al) *al = pf->Aloss;
    SDL_FreeFormat(pf);
}
static uint32_t shift_mask(uint32_t v, uint32_t mask, int shift, int loss) {
    uint32_t x = (v & mask) >> shift;
    if (loss > 0 && loss < 8) x = x * 255u / ((1u << (8 - loss)) - 1u);
    return x & 0xFFu;
}

/* ---- guest SDL2 surface allocation (SDL_surface.h / SDL_pixels.h) ---- */
static uint32_t make_palette(int ncolors) {
    uint32_t pal = hle_va(hle_alloc(S2_PALETTE));
    uint32_t cols = hle_va(hle_alloc((uint32_t)ncolors * 4u));
    if (!pal || !cols) { sdl2_error("out of guest memory for palette"); return 0; }
    MEM32(pal + S2_PAL_NCOLORS) = (uint32_t)ncolors;
    MEM32(pal + S2_PAL_COLORS) = cols;
    MEM32(pal + S2_PAL_VERSION) = 0;
    MEM32(pal + S2_PAL_REFCOUNT) = 1;
    return pal;
}
/* A fresh guest SDL_Surface with its own pixel buffer.  Returns the surface's
 * guest VA, or 0.  ARGB8888/INDEX8 are what SDL2 programs ask for most. */
static uint32_t make_surface(int w, int h, Uint32 format) {
    int bpp, rs, gs, bs, as, rl, gl, bl, al;
    Uint32 rm = 0, gm = 0, bm = 0, am = 0;
    fmt_info(format, &bpp, &rm, &gm, &bm, &am, &rs, &gs, &bs, &as, &rl, &gl, &bl, &al);
    if (w <= 0 || h <= 0 || w > 16384 || h > 16384 || bpp < 1 || bpp > 4) {
        sdl2_error("invalid surface geometry %dx%d (bpp %d)", w, h, bpp);
        return 0;
    }
    uint64_t pitch64 = (uint64_t)w * (uint32_t)bpp;
    uint64_t bytes = pitch64 * (uint32_t)h;
    if (bytes > (64u << 20)) { sdl2_error("surface too large"); return 0; }
    uint32_t px = hle_va(hle_alloc((uint32_t)bytes));
    uint32_t s = hle_va(hle_alloc(S2_SURFACE));
    uint32_t f = hle_va(hle_alloc(S2_FORMAT));
    uint32_t pal = bpp == 1 ? make_palette(256) : 0;
    if (!px || !s || !f || (bpp == 1 && !pal)) { sdl2_error("out of guest memory for surface"); return 0; }

    MEM32(f + S2_FMT_FORMAT) = format;
    MEM32(f + S2_FMT_PALETTE) = pal;
    MEM8(f + S2_FMT_BPP) = (uint8_t)bpp;
    MEM8(f + S2_FMT_BYTESPP) = (uint8_t)bpp;
    MEM32(f + S2_FMT_RMASK) = rm; MEM32(f + S2_FMT_GMASK) = gm;
    MEM32(f + S2_FMT_BMASK) = bm; MEM32(f + S2_FMT_AMASK) = am;
    MEM8(f + S2_FMT_RLOSS) = (uint8_t)rl; MEM8(f + S2_FMT_GLOSS) = (uint8_t)gl;
    MEM8(f + S2_FMT_BLOSS) = (uint8_t)bl; MEM8(f + S2_FMT_ALOSS) = (uint8_t)al;
    MEM8(f + S2_FMT_RSHIFT) = (uint8_t)rs; MEM8(f + S2_FMT_GSHIFT) = (uint8_t)gs;
    MEM8(f + S2_FMT_BSHIFT) = (uint8_t)bs; MEM8(f + S2_FMT_ASHIFT) = (uint8_t)as;
    MEM32(f + S2_FMT_REFCOUNT) = 1;

    MEM32(s + S2_SF_FLAGS) = 0;              /* SDL_SWSURFACE */
    MEM32(s + S2_SF_FORMAT) = f;
    MEM32(s + S2_SF_W) = (uint32_t)w;
    MEM32(s + S2_SF_H) = (uint32_t)h;
    MEM32(s + S2_SF_PITCH) = (uint32_t)pitch64;
    MEM32(s + S2_SF_PIXELS) = px;
    MEM32(s + S2_SF_CLIP + 0) = 0; MEM32(s + S2_SF_CLIP + 4) = 0;
    MEM32(s + S2_SF_CLIP + 8) = (uint32_t)w; MEM32(s + S2_SF_CLIP + 12) = (uint32_t)h;
    MEM32(s + S2_SF_REFCOUNT) = 1;
    return s;
}

/* ---- present the guest texture on the shared window ------------------ */
/* Convert a (possibly sub-)rectangle of a guest texture into compact ARGB8888
 * in a host buffer.  The buffer is consumed synchronously by
 * hle_present_surface(), exactly as present.c's own conversion scratch is. */
static const uint32_t *to_argb(sdl2_obj *t, int x, int y, int w, int h, int *out_pitch) {
    static uint32_t *buf;
    static size_t cap;
    size_t need = (size_t)w * (size_t)h;
    if (need == 0 || need > (size_t)(64u << 20) / 4u) return NULL;
    if (cap < need) {
        uint32_t *nb = (uint32_t *)realloc(buf, need * 4u);
        if (!nb) return NULL;
        buf = nb; cap = need;
    }
    uint32_t colors = 0, ncolors = 0;
    if (t->bytespp == 1 && t->palette) {
        ncolors = MEM32(t->palette + S2_PAL_NCOLORS);
        colors = MEM32(t->palette + S2_PAL_COLORS);
        if (!colors) ncolors = 0;
    }
    for (int row = 0; row < h; row++) {
        uint32_t src = t->pixels + (uint32_t)((y + row) * t->pitch) + (uint32_t)(x * t->bytespp);
        for (int col = 0; col < w; col++, src += (uint32_t)t->bytespp) {
            uint32_t p;
            switch (t->bytespp) {
            case 1: p = MEM8(src); break;
            case 2: p = (uint32_t)MEM8(src) | ((uint32_t)MEM8(src + 1) << 8); break;
            case 3: p = (uint32_t)MEM8(src) | ((uint32_t)MEM8(src + 1) << 8) | ((uint32_t)MEM8(src + 2) << 16); break;
            default: p = MEM32(src); break;
            }
            uint32_t r, g, b, a;
            if (t->bytespp == 1 && colors) {
                uint32_t i = p < ncolors ? p : 0;
                uint8_t *c = hle_ptr(colors + i * 4u);
                r = c ? c[0] : 0; g = c ? c[1] : 0; b = c ? c[2] : 0; a = c ? c[3] : 255;
            } else {
                r = shift_mask(p, t->rmask, t->rshift, t->rloss);
                g = shift_mask(p, t->gmask, t->gshift, t->gloss);
                b = shift_mask(p, t->bmask, t->bshift, t->bloss);
                a = t->amask ? shift_mask(p, t->amask, t->ashift, t->aloss) : 255u;
            }
            buf[(size_t)row * (size_t)w + (size_t)col] =
                (a << 24) | (r << 16) | (g << 8) | b;
        }
    }
    *out_pitch = w * 4;
    return buf;
}

/* The texture currently painted on the shared window: the last RenderCopy's
 * texture, or the window surface when the guest never made a texture. */
static sdl2_obj *current_frame(sdl2_obj *r) {
    sdl2_obj *t = obj_of(r->last_tex, H2_TEXTURE);
    if (t) return t;
    if (r->win) {
        sdl2_obj *w = obj_of(r->win, H2_WINDOW);
        if (w && w->surface_va && guest_ok(w->surface_va, S2_SURFACE)) {
            uint32_t f = MEM32(w->surface_va + S2_SF_FORMAT);
            int bpp = f && guest_ok(f, S2_FORMAT) ? MEM8(f + S2_FMT_BPP) : 0;
            if (bpp >= 1 && bpp <= 4) {
                /* A lightweight view over the window surface, not an allocated
                 * object: only the fields the presenter reads are filled. */
                static sdl2_obj view;
                memset(&view, 0, sizeof view);
                view.kind = H2_TEXTURE;
                view.w = (int)MEM32(w->surface_va + S2_SF_W);
                view.h_pixels = (int)MEM32(w->surface_va + S2_SF_H);
                view.pixels = MEM32(w->surface_va + S2_SF_PIXELS);
                view.pitch = (int)MEM32(w->surface_va + S2_SF_PITCH);
                view.bytespp = bpp;
                view.format = f && guest_ok(f, S2_FORMAT) ? MEM32(f + S2_FMT_FORMAT) : 0;
                view.palette = f && bpp == 1 ? MEM32(f + S2_FMT_PALETTE) : 0;
                fmt_info(view.format, NULL, &view.rmask, &view.gmask, &view.bmask, &view.amask,
                         &view.rshift, &view.gshift, &view.bshift, &view.ashift,
                         &view.rloss, &view.gloss, &view.bloss, &view.aloss);
                if (view.w > 0 && view.h_pixels > 0 && view.pitch > 0 &&
                    guest_ok(view.pixels, (uint64_t)view.pitch * (uint32_t)view.h_pixels))
                    return &view;
            }
        }
    }
    return NULL;
}

static void publish(sdl2_obj *r) {
    sdl2_obj *t = current_frame(r);
    if (!t) return;
    int x = 0, y = 0, w = t->w, h = t->h_pixels;
    if (r->has_src) {
        x = r->sx; y = r->sy; w = r->sw; h = r->sh;
        if (x < 0) { w += x; x = 0; }
        if (y < 0) { h += y; y = 0; }
        if (x + w > t->w) w = t->w - x;
        if (y + h > t->h_pixels) h = t->h_pixels - y;
    }
    if (w <= 0 || h <= 0) return;
    if (!guest_ok(t->pixels, (uint64_t)t->pitch * (uint32_t)t->h_pixels)) return;
    if (t->bytespp == 4 && t->format == SDL_PIXELFORMAT_ARGB8888 &&
        t->pitch == t->w * 4 && x == 0 && y == 0 && w == t->w && h == t->h_pixels) {
        hle_present_surface((const uint8_t *)hle_ptr(t->pixels), w, h, t->pitch, 32);
    } else {
        int pitch = 0;
        const uint32_t *argb = to_argb(t, x, y, w, h, &pitch);
        if (argb) hle_present_surface((const uint8_t *)argb, w, h, pitch, 32);
    }
}

/* ---- shims (all cdecl) ------------------------------------------------ */
static void s2_SetMainReady(void) { g_main_ready = 1; RETCV(); }

static void s2_CreateWindow(void) {
    const char *title = ASTR(0);
    int w = (int)A32(3), h = (int)A32(4);
    Uint32 flags = A32(5);
    if (w <= 0 || h <= 0 || w > 16384 || h > 16384) {
        sdl2_error("SDL_CreateWindow: invalid size %dx%d", w, h); RETC(0);
    }
    if (!g_present_opened) {
        if (hle_present_open(title, w, h) != 0) {
            sdl2_error("SDL_CreateWindow: shared presentation window unavailable");
            RETC(0);
        }
        g_present_opened = 1;
    }
    uint32_t wh = obj_new(H2_WINDOW);
    if (!wh) RETC(0);
    sdl2_obj *win = obj_of(wh, H2_WINDOW);
    win->w = w; win->h_pixels = h; win->flags = flags;
    win->id = g_window ? g_window->id + 1 : 1;
    win->surface_va = 0;
    if (!g_window) g_window = win;
    hle_screen_set_native_size(w, h);      /* keep screen.c's mode in sync */
    RETC(wh);
}
static void s2_DestroyWindow(void) {
    uint32_t wh = A32(0);
    if (!obj_of(wh, H2_WINDOW)) { need_obj(wh, H2_WINDOW, "SDL_DestroyWindow"); RETCV(); }
    sdl2_obj *w = obj_of(wh, H2_WINDOW);
    /* The window surface is a guest struct in the guest heap, not a handle. */
    if (g_window == w) g_window = NULL;
    /* The shared host window is owned by present.c; do not tear it down here. */
    obj_del(wh);
    RETCV();
}
static void s2_GetWindowFlags(void) {
    uint32_t wh = A32(0);
    sdl2_obj *w = obj_of(wh, H2_WINDOW);
    if (!w) { need_obj(wh, H2_WINDOW, "SDL_GetWindowFlags"); RETC(0); }
    RETC(w->flags | SDL_WINDOW_SHOWN);
}
static void s2_GetWindowID(void) {
    sdl2_obj *w = obj_of(A32(0), H2_WINDOW);
    RETC(w ? (uint32_t)w->id : 0u);
}
static void s2_GetWindowSurface(void) {
    uint32_t wh = A32(0);
    sdl2_obj *w = obj_of(wh, H2_WINDOW);
    if (!w) { need_obj(wh, H2_WINDOW, "SDL_GetWindowSurface"); RETC(0); }
    if (!w->surface_va) {
        w->surface_va = make_surface(w->w, w->h_pixels, SDL_PIXELFORMAT_ARGB8888);
        if (!w->surface_va) RETC(0);
    }
    RETC(w->surface_va);
}
static void s2_SetWindowPosition(void) {
    uint32_t wh = A32(0);
    if (!obj_of(wh, H2_WINDOW)) { need_obj(wh, H2_WINDOW, "SDL_SetWindowPosition"); RETCV(); }
    /* Cosmetic: the shared window's placement is host/policy controlled. */
    RETCV();
}
static void s2_SetWindowIcon(void) {
    uint32_t wh = A32(0), icon = A32(1);
    if (!obj_of(wh, H2_WINDOW)) { need_obj(wh, H2_WINDOW, "SDL_SetWindowIcon"); RETCV(); }
    if (icon && !guest_ok(icon, S2_SURFACE)) {
        sdl2_error("SDL_SetWindowIcon: bad surface pointer 0x%08X", icon);
    }
    /* Cosmetic: the shared window has no guest-controlled icon. */
    RETCV();
}
static void s2_SetRelativeMouseMode(void) {
    SDL_bool on = A32(0) ? SDL_TRUE : SDL_FALSE;
    if (!SDL_WasInit(SDL_INIT_VIDEO) && SDL_InitSubSystem(SDL_INIT_VIDEO) != 0) {
        sdl2_error("SDL_SetRelativeMouseMode: video init: %s", SDL_GetError()); RETC(-1);
    }
    if (SDL_SetRelativeMouseMode(on) != 0) {
        sdl2_error("SDL_SetRelativeMouseMode: %s", SDL_GetError()); RETC(-1);
    }
    RETC(0);
}

static void s2_CreateRenderer(void) {
    uint32_t wh = A32(0);
    sdl2_obj *w = obj_of(wh, H2_WINDOW);
    if (!w) { need_obj(wh, H2_WINDOW, "SDL_CreateRenderer"); RETC(0); }
    uint32_t rh = obj_new(H2_RENDERER);
    if (!rh) RETC(0);
    sdl2_obj *r = obj_of(rh, H2_RENDERER);
    r->win = wh; r->logical_w = w->w; r->logical_h = w->h_pixels;
    /* No host renderer: present.c/screen.c already own window presentation. */
    RETC(rh);
}
static void s2_DestroyRenderer(void) {
    uint32_t rh = A32(0);
    if (!obj_of(rh, H2_RENDERER)) { need_obj(rh, H2_RENDERER, "SDL_DestroyRenderer"); RETCV(); }
    obj_del(rh);
    RETCV();
}
static void s2_RenderClear(void) {
    uint32_t rh = A32(0);
    sdl2_obj *r = obj_of(rh, H2_RENDERER);
    if (!r) { need_obj(rh, H2_RENDERER, "SDL_RenderClear"); RETC(-1); }
    /* The shared presenter draws the copied texture each frame; there is no
     * separate backbuffer to clear.  A new frame starts at RenderCopy. */
    r->last_tex = 0; r->has_src = 0;
    RETC(0);
}
static void s2_RenderCopy(void) {
    uint32_t rh = A32(0), th = A32(1), sr = A32(2);
    sdl2_obj *r = obj_of(rh, H2_RENDERER), *t = obj_of(th, H2_TEXTURE);
    if (!r) { need_obj(rh, H2_RENDERER, "SDL_RenderCopy"); RETC(-1); }
    if (!t) { need_obj(th, H2_TEXTURE, "SDL_RenderCopy"); RETC(-1); }
    int x = 0, y = 0, w = t->w, h = t->h_pixels;
    if (sr) {
        if (!guest_ok(sr, 16)) { sdl2_error("SDL_RenderCopy: bad srcrect"); RETC(-1); }
        x = (int)MEM32(sr); y = (int)MEM32(sr + 4);
        w = (int)MEM32(sr + 8); h = (int)MEM32(sr + 12);
        if (x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > t->w || y + h > t->h_pixels) {
            sdl2_error("SDL_RenderCopy: srcrect %d,%d %dx%d outside %dx%d texture",
                       x, y, w, h, t->w, t->h_pixels);
            RETC(-1);
        }
    }
    uint64_t need = (uint64_t)t->pitch * (uint32_t)t->h_pixels;
    if (!guest_ok(t->pixels, need)) { sdl2_error("SDL_RenderCopy: texture pixels out of range"); RETC(-1); }
    r->last_tex = th; r->sx = x; r->sy = y; r->sw = w; r->sh = h; r->has_src = sr != 0;
    /* dstrect is a scaling hint; hle_present_surface scales to the window. */
    RETC(0);
}
static void s2_RenderPresent(void) {
    uint32_t rh = A32(0);
    sdl2_obj *r = obj_of(rh, H2_RENDERER);
    if (!r) { need_obj(rh, H2_RENDERER, "SDL_RenderPresent"); RETC(-1); }
    publish(r);
    RETC(0);
}
static void s2_RenderReadPixels(void) {
    uint32_t rh = A32(0), rect = A32(1), format = A32(2), dst = A32(3);
    int pitch = (int)A32(4);
    sdl2_obj *r = obj_of(rh, H2_RENDERER);
    if (!r) { need_obj(rh, H2_RENDERER, "SDL_RenderReadPixels"); RETC(-1); }
    if (!dst || pitch <= 0) { sdl2_error("SDL_RenderReadPixels: bad destination"); RETC(-1); }
    sdl2_obj *t = current_frame(r);
    if (!t) { sdl2_error("SDL_RenderReadPixels: nothing has been rendered"); RETC(-1); }
    int x = 0, y = 0, w = t->w, h = t->h_pixels;
    if (rect) {
        if (!guest_ok(rect, 16)) { sdl2_error("SDL_RenderReadPixels: bad rect"); RETC(-1); }
        x = (int)MEM32(rect); y = (int)MEM32(rect + 4);
        w = (int)MEM32(rect + 8); h = (int)MEM32(rect + 12);
    }
    if (x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > t->w || y + h > t->h_pixels) {
        sdl2_error("SDL_RenderReadPixels: rect outside the render target"); RETC(-1);
    }
    if (format != t->format) {
        /* No second pixel-format converter: refuse honestly instead of
         * writing pixels in the wrong layout. */
        sdl2_error("SDL_RenderReadPixels: conversion 0x%08X -> 0x%08X unsupported",
                   t->format, format);
        RETC(-1);
    }
    if (pitch < w * t->bytespp || !guest_ok(dst, (uint64_t)pitch * (uint32_t)h)) {
        sdl2_error("SDL_RenderReadPixels: destination too small"); RETC(-1);
    }
    for (int row = 0; row < h; row++)
        memcpy(hle_ptr(dst + (uint32_t)row * (uint32_t)pitch),
               hle_ptr(t->pixels + (uint32_t)((y + row) * t->pitch) + (uint32_t)(x * t->bytespp)),
               (size_t)w * (size_t)t->bytespp);
    RETC(0);
}
static void s2_RenderSetLogicalSize(void) {
    uint32_t rh = A32(0);
    int w = (int)A32(1), h = (int)A32(2);
    sdl2_obj *r = obj_of(rh, H2_RENDERER);
    if (!r) { need_obj(rh, H2_RENDERER, "SDL_RenderSetLogicalSize"); RETC(-1); }
    if (w <= 0 || h <= 0 || w > 16384 || h > 16384) {
        sdl2_error("SDL_RenderSetLogicalSize: invalid size %dx%d", w, h); RETC(-1);
    }
    r->logical_w = w; r->logical_h = h;
    RETC(0);
}
static void s2_CreateTexture(void) {
    uint32_t rh = A32(0);
    Uint32 format = A32(1);
    int access = (int)A32(2), w = (int)A32(3), h = (int)A32(4);
    sdl2_obj *r = obj_of(rh, H2_RENDERER);
    if (!r) { need_obj(rh, H2_RENDERER, "SDL_CreateTexture"); RETC(0); }
    int bpp, rs, gs, bs, as, rl, gl, bl, al;
    Uint32 rm = 0, gm = 0, bm = 0, am = 0;
    fmt_info(format, &bpp, &rm, &gm, &bm, &am, &rs, &gs, &bs, &as, &rl, &gl, &bl, &al);
    if (w <= 0 || h <= 0 || w > 16384 || h > 16384 || bpp < 1 || bpp > 4) {
        sdl2_error("SDL_CreateTexture: unsupported format/geometry"); RETC(0);
    }
    uint64_t pitch = (uint64_t)w * (uint32_t)bpp, bytes = pitch * (uint32_t)h;
    if (bytes > (128u << 20)) { sdl2_error("SDL_CreateTexture: texture too large"); RETC(0); }
    uint32_t th = obj_new(H2_TEXTURE);
    if (!th) RETC(0);
    sdl2_obj *t = obj_of(th, H2_TEXTURE);
    t->pixels = hle_va(hle_alloc((uint32_t)bytes));
    if (!t->pixels) { obj_del(th); sdl2_error("out of guest memory for texture"); RETC(0); }
    t->win = rh; t->w = w; t->h_pixels = h; t->format = format; t->access = access;
    t->pitch = (int)pitch; t->bytespp = bpp;
    t->rmask = rm; t->gmask = gm; t->bmask = bm; t->amask = am;
    t->rshift = rs; t->gshift = gs; t->bshift = bs; t->ashift = as;
    t->rloss = rl; t->gloss = gl; t->bloss = bl; t->aloss = al;
    if (bpp == 1) t->palette = make_palette(256);
    RETC(th);
}
static void s2_CreateTextureFromSurface(void) {
    uint32_t rh = A32(0), s = A32(1);
    sdl2_obj *r = obj_of(rh, H2_RENDERER);
    if (!r) { need_obj(rh, H2_RENDERER, "SDL_CreateTextureFromSurface"); RETC(0); }
    if (!guest_ok(s, S2_SURFACE)) { sdl2_error("SDL_CreateTextureFromSurface: bad surface"); RETC(0); }
    uint32_t f = MEM32(s + S2_SF_FORMAT);
    int w = (int)MEM32(s + S2_SF_W), h = (int)MEM32(s + S2_SF_H);
    int srcpitch = (int)MEM32(s + S2_SF_PITCH);
    uint32_t srcpx = MEM32(s + S2_SF_PIXELS);
    if (!guest_ok(f, S2_FORMAT) || w <= 0 || h <= 0 || srcpitch <= 0 ||
        !guest_ok(srcpx, (uint64_t)srcpitch * (uint32_t)h) ||
        (uint64_t)srcpitch * (uint32_t)h > (128u << 20)) {
        sdl2_error("SDL_CreateTextureFromSurface: invalid surface geometry"); RETC(0);
    }
    Uint32 format = MEM32(f + S2_FMT_FORMAT);
    int bpp = MEM8(f + S2_FMT_BPP);
    if (bpp < 1 || bpp > 4) { sdl2_error("SDL_CreateTextureFromSurface: bad pixel format"); RETC(0); }
    uint64_t pitch = (uint64_t)w * (uint32_t)bpp, bytes = pitch * (uint32_t)h;
    uint32_t th = obj_new(H2_TEXTURE);
    if (!th) RETC(0);
    sdl2_obj *t = obj_of(th, H2_TEXTURE);
    t->pixels = hle_va(hle_alloc((uint32_t)bytes));
    if (!t->pixels) { obj_del(th); sdl2_error("out of guest memory for texture"); RETC(0); }
    for (int row = 0; row < h; row++)
        memcpy(hle_ptr(t->pixels + (uint32_t)row * (uint32_t)pitch),
               hle_ptr(srcpx + (uint32_t)row * (uint32_t)srcpitch), (size_t)pitch);
    t->win = rh; t->w = w; t->h_pixels = h; t->format = format;
    t->access = SDL_TEXTUREACCESS_STATIC; t->pitch = (int)pitch; t->bytespp = bpp;
    t->palette = bpp == 1 ? MEM32(f + S2_FMT_PALETTE) : 0;
    fmt_info(format, NULL, &t->rmask, &t->gmask, &t->bmask, &t->amask,
             &t->rshift, &t->gshift, &t->bshift, &t->ashift,
             &t->rloss, &t->gloss, &t->bloss, &t->aloss);
    RETC(th);
}
static void s2_DestroyTexture(void) {
    uint32_t th = A32(0);
    if (!obj_of(th, H2_TEXTURE)) { need_obj(th, H2_TEXTURE, "SDL_DestroyTexture"); RETCV(); }
    obj_del(th);
    RETCV();
}
static void s2_SetRenderTarget(void) {
    uint32_t rh = A32(0), th = A32(1);
    sdl2_obj *r = obj_of(rh, H2_RENDERER);
    if (!r) { need_obj(rh, H2_RENDERER, "SDL_SetRenderTarget"); RETC(-1); }
    if (th && !obj_of(th, H2_TEXTURE)) {
        need_obj(th, H2_TEXTURE, "SDL_SetRenderTarget"); RETC(-1);
    }
    r->target = th;
    RETC(0);
}
static void s2_SetPaletteColors(void) {
    uint32_t pal = A32(0), colors = A32(1);
    int first = (int)A32(2), n = (int)A32(3);
    if (!guest_ok(pal, S2_PALETTE) || !guest_ok(colors, (uint64_t)(n > 0 ? n : 0) * 4u) || n < 0) {
        sdl2_error("SDL_SetPaletteColors: bad palette/colors pointer"); RETC(-1);
    }
    uint32_t ncolors = MEM32(pal + S2_PAL_NCOLORS);
    uint32_t dst_colors = MEM32(pal + S2_PAL_COLORS);
    if (!dst_colors || first < 0 || n < 0 || (uint32_t)first + (uint32_t)n > ncolors ||
        !guest_ok(dst_colors, (uint64_t)ncolors * 4u)) {
        sdl2_error("SDL_SetPaletteColors: range %d+%d outside %u colors", first, n, ncolors);
        RETC(-1);
    }
    memcpy(hle_ptr(dst_colors + (uint32_t)first * 4u), hle_ptr(colors), (size_t)n * 4u);
    RETC(0);
}
static void s2_RWFromConstMem(void) {
    uint32_t mem = A32(0);
    int size = (int)A32(1);
    if (!guest_ok(mem, (uint64_t)(size > 0 ? size : 0)) || size <= 0) {
        sdl2_error("SDL_RWFromConstMem: bad buffer (%d bytes at 0x%08X)", size, mem);
        RETC(0);
    }
    /* sdl12.c's rw_* shims read these exact offsets.  Reusing that layout and
     * its registered method VAs means SDL_RWread/seek/tell/close just work. */
    uint32_t rw = hle_va(hle_alloc(S2_RWOPS));
    if (!rw) { sdl2_error("SDL_RWFromConstMem: out of guest memory"); RETC(0); }
    MEM32(rw + S2_RW_SEEK) = hle_resolve("SDL_RWseek");
    MEM32(rw + S2_RW_READ) = hle_resolve("SDL_RWread");
    MEM32(rw + S2_RW_WRITE) = hle_resolve("SDL_RWwrite");
    MEM32(rw + S2_RW_CLOSE) = hle_resolve("SDL_RWclose");
    MEM32(rw + S2_RW_TYPE) = S2_RWOPS_MEMORY_RO;
    MEM32(rw + S2_RW_KIND) = S2_RW_KIND_MEM;
    MEM32(rw + S2_RW_HANDLE) = mem;          /* const: never written, never freed */
    MEM32(rw + S2_RW_SIZE) = (uint32_t)size;
    MEM32(rw + S2_RW_POS) = 0;
    MEM32(rw + S2_RW_OWN) = 0;
    RETC(rw);
}

/* ---- display modes: the host's shared mode table, not a new display ---- */
static uint32_t put_mode(uint32_t dst, int w, int h) {
    if (!guest_ok(dst, S2_DISPLAYMODE)) { sdl2_error("SDL display mode: bad output pointer"); return 0; }
    MEM32(dst + S2_DM_FORMAT) = SDL_PIXELFORMAT_ARGB8888;
    MEM32(dst + S2_DM_W) = (uint32_t)w;
    MEM32(dst + S2_DM_H) = (uint32_t)h;
    MEM32(dst + S2_DM_REFRESH) = 60;
    MEM32(dst + S2_DM_DRIVERDATA) = 0;
    return 1;
}
static void s2_GetNumDisplayModes(void) {
    int display = (int)A32(0);
    int modes[HLE_DD_MAX_MODES][2];
    if (display != 0) {
        sdl2_error("SDL_GetNumDisplayModes: display %d not available", display); RETC(-1);
    }
    RETC((uint32_t)hle_dd_modes(0, 0, modes));
}
static void s2_GetDisplayMode(void) {
    int display = (int)A32(0), index = (int)A32(1);
    uint32_t dst = A32(2);
    int modes[HLE_DD_MAX_MODES][2];
    if (display != 0) { sdl2_error("SDL_GetDisplayMode: display %d not available", display); RETC(-1); }
    int n = hle_dd_modes(0, 0, modes);
    if (index < 0 || index >= n) { sdl2_error("SDL_GetDisplayMode: mode %d out of range", index); RETC(-1); }
    RETC(put_mode(dst, modes[index][0], modes[index][1]) ? 0 : (uint32_t)-1);
}
static void s2_GetCurrentDisplayMode(void) {
    int display = (int)A32(0);
    uint32_t dst = A32(1);
    int w = 0, h = 0;
    if (display != 0) { sdl2_error("SDL_GetCurrentDisplayMode: display %d not available", display); RETC(-1); }
    hle_screen_desktop(&w, &h);
    if (w <= 0 || h <= 0) {
        SDL_DisplayMode m;
        if (SDL_GetCurrentDisplayMode(0, &m) == 0) { w = m.w; h = m.h; }
    }
    if ((w <= 0 || h <= 0) && g_window) { w = g_window->w; h = g_window->h_pixels; }
    if (w <= 0 || h <= 0) { w = 640; h = 480; }
    RETC(put_mode(dst, w, h) ? 0 : (uint32_t)-1);
}
static void s2_GL_DeleteContext(void) {
    /* The GL context (when any) is owned by screen.c/sdl12.c and shared; a
     * guest cannot delete the host's context.  NULL is SDL2's no-op case. */
    RETCV();
}

const win32hle_shim win32hle_sdl2[] = {
    { "SDL_SetMainReady", s2_SetMainReady },
    { "SDL_CreateWindow", s2_CreateWindow }, { "SDL_DestroyWindow", s2_DestroyWindow },
    { "SDL_GetWindowFlags", s2_GetWindowFlags }, { "SDL_GetWindowID", s2_GetWindowID },
    { "SDL_GetWindowSurface", s2_GetWindowSurface },
    { "SDL_SetWindowPosition", s2_SetWindowPosition }, { "SDL_SetWindowIcon", s2_SetWindowIcon },
    { "SDL_SetRelativeMouseMode", s2_SetRelativeMouseMode },
    { "SDL_CreateRenderer", s2_CreateRenderer }, { "SDL_DestroyRenderer", s2_DestroyRenderer },
    { "SDL_RenderClear", s2_RenderClear }, { "SDL_RenderCopy", s2_RenderCopy },
    { "SDL_RenderPresent", s2_RenderPresent }, { "SDL_RenderReadPixels", s2_RenderReadPixels },
    { "SDL_RenderSetLogicalSize", s2_RenderSetLogicalSize },
    { "SDL_CreateTexture", s2_CreateTexture },
    { "SDL_CreateTextureFromSurface", s2_CreateTextureFromSurface },
    { "SDL_DestroyTexture", s2_DestroyTexture }, { "SDL_SetRenderTarget", s2_SetRenderTarget },
    { "SDL_SetPaletteColors", s2_SetPaletteColors },
    { "SDL_RWFromConstMem", s2_RWFromConstMem },
    { "SDL_GetCurrentDisplayMode", s2_GetCurrentDisplayMode },
    { "SDL_GetDisplayMode", s2_GetDisplayMode },
    { "SDL_GetNumDisplayModes", s2_GetNumDisplayModes },
    { "SDL_GL_DeleteContext", s2_GL_DeleteContext },
    { 0, 0 }
};
