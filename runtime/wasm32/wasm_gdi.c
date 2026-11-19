#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include "win32hle.h"

/* Built-in 5x7 ASCII font (0x20..0x7E) for canvas text & menu rendering */
static const uint8_t k_font5x7[95][5] = {
    {0x00,0x00,0x00,0x00,0x00}, {0x00,0x00,0x5F,0x00,0x00}, {0x00,0x07,0x00,0x07,0x00},
    {0x14,0x7F,0x14,0x7F,0x14}, {0x24,0x2A,0x7F,0x2A,0x12}, {0x23,0x13,0x08,0x64,0x62},
    {0x36,0x49,0x55,0x22,0x50}, {0x00,0x05,0x03,0x00,0x00}, {0x00,0x1C,0x22,0x41,0x00},
    {0x00,0x41,0x22,0x1C,0x00}, {0x14,0x08,0x3E,0x08,0x14}, {0x08,0x08,0x3E,0x08,0x08},
    {0x00,0x50,0x30,0x00,0x00}, {0x08,0x08,0x08,0x08,0x08}, {0x00,0x60,0x60,0x00,0x00},
    {0x20,0x10,0x08,0x04,0x02}, {0x3E,0x51,0x49,0x45,0x3E}, {0x00,0x42,0x7F,0x40,0x00},
    {0x42,0x61,0x51,0x49,0x46}, {0x21,0x41,0x45,0x4B,0x31}, {0x18,0x14,0x12,0x7F,0x10},
    {0x27,0x45,0x45,0x45,0x39}, {0x3C,0x4A,0x49,0x49,0x30}, {0x01,0x71,0x09,0x05,0x03},
    {0x36,0x49,0x49,0x49,0x36}, {0x06,0x49,0x49,0x29,0x1E}, {0x00,0x36,0x36,0x00,0x00},
    {0x00,0x56,0x36,0x00,0x00}, {0x08,0x14,0x22,0x41,0x00}, {0x14,0x14,0x14,0x14,0x14},
    {0x00,0x41,0x22,0x14,0x08}, {0x02,0x01,0x51,0x09,0x06}, {0x32,0x49,0x79,0x41,0x3E},
    {0x7E,0x11,0x11,0x11,0x7E}, {0x7F,0x49,0x49,0x49,0x36}, {0x3E,0x41,0x41,0x41,0x22},
    {0x7F,0x41,0x41,0x22,0x1C}, {0x7F,0x49,0x49,0x49,0x41}, {0x7F,0x09,0x09,0x09,0x01},
    {0x3E,0x41,0x49,0x49,0x7A}, {0x7F,0x08,0x08,0x08,0x7F}, {0x00,0x41,0x7F,0x41,0x00},
    {0x20,0x40,0x41,0x3F,0x01}, {0x7F,0x08,0x14,0x22,0x41}, {0x7F,0x40,0x40,0x40,0x40},
    {0x7F,0x02,0x0C,0x02,0x7F}, {0x7F,0x04,0x08,0x10,0x7F}, {0x3E,0x41,0x41,0x41,0x3E},
    {0x7F,0x09,0x09,0x09,0x06}, {0x3E,0x41,0x51,0x21,0x5E}, {0x7F,0x09,0x19,0x29,0x46},
    {0x46,0x49,0x49,0x49,0x31}, {0x01,0x01,0x7F,0x01,0x01}, {0x3F,0x40,0x40,0x40,0x3F},
    {0x1F,0x20,0x40,0x20,0x1F}, {0x3F,0x40,0x38,0x40,0x3F}, {0x63,0x14,0x08,0x14,0x63},
    {0x07,0x08,0x70,0x08,0x07}, {0x61,0x51,0x49,0x45,0x43}, {0x00,0x7F,0x41,0x41,0x00},
    {0x02,0x04,0x08,0x10,0x20}, {0x00,0x41,0x41,0x7F,0x00}, {0x04,0x02,0x01,0x02,0x04},
    {0x40,0x40,0x40,0x40,0x40}, {0x00,0x01,0x02,0x04,0x00}, {0x20,0x54,0x54,0x54,0x78},
    {0x7F,0x48,0x44,0x44,0x38}, {0x38,0x44,0x44,0x44,0x20}, {0x38,0x44,0x44,0x48,0x7F},
    {0x38,0x54,0x54,0x54,0x18}, {0x08,0x7E,0x09,0x01,0x02}, {0x0C,0x52,0x52,0x52,0x3E},
    {0x7F,0x08,0x04,0x04,0x78}, {0x00,0x44,0x7D,0x40,0x00}, {0x20,0x40,0x44,0x3D,0x00},
    {0x7F,0x10,0x28,0x44,0x00}, {0x00,0x41,0x7F,0x40,0x00}, {0x7C,0x04,0x18,0x04,0x78},
    {0x7C,0x08,0x04,0x04,0x78}, {0x38,0x44,0x44,0x44,0x38}, {0x7C,0x14,0x14,0x14,0x08},
    {0x08,0x14,0x14,0x18,0x7C}, {0x7C,0x08,0x04,0x04,0x08}, {0x48,0x54,0x54,0x54,0x20},
    {0x04,0x3F,0x44,0x40,0x20}, {0x3C,0x40,0x40,0x20,0x7C}, {0x1C,0x20,0x40,0x20,0x1C},
    {0x3C,0x40,0x30,0x40,0x3C}, {0x44,0x28,0x10,0x28,0x44}, {0x0C,0x50,0x50,0x50,0x3C},
    {0x44,0x64,0x54,0x4C,0x44}, {0x00,0x08,0x36,0x41,0x00}, {0x00,0x00,0x7F,0x00,0x00},
    {0x00,0x41,0x36,0x08,0x00}, {0x08,0x04,0x08,0x10,0x08}
};

#define MENU_BAR_H 22
#define MAX_FB_W 1280
#define MAX_FB_H 900

typedef struct {
    char     cls[64];
    uint32_t wndproc;
    uint32_t brush;
    int      extra;
} wasm_wndcls_t;

typedef struct {
    int      used;
    uint32_t hwnd;
    uint32_t parent;
    uint32_t wndproc;
    uint32_t hmenu;
    uint32_t style;
    uint32_t exstyle;
    uint32_t user_data;
    int      x, y, w, h;
    int      visible;
    int      dirty;
    char     title[128];
} wasm_wnd_t;

typedef struct {
    int      used;
    int      is_popup;
    uint32_t id;
    uint32_t submenu;
    uint32_t flags;
    char     label[64];
} wasm_menuitem_t;

typedef struct {
    int             used;
    uint32_t        handle;
    wasm_menuitem_t items[32];
    int             count;
} wasm_menu_t;

enum { GDI_NONE = 0, GDI_PEN, GDI_BRUSH, GDI_FONT, GDI_BITMAP };

typedef struct {
    int      used;
    int      kind;
    uint32_t handle;
    uint32_t color;
    int      width;
    int      style;
    int      font_h;
    /* bitmap payload */
    int      bm_w, bm_h;
    uint32_t *bm_px;
} wasm_gdiobj_t;

typedef struct {
    int      used;
    uint32_t hdc;
    uint32_t hwnd;
    uint32_t cur_pen, cur_brush, cur_font, cur_bm;
    uint32_t text_color, bk_color;
    int      bk_mode;
    int      pen_x, pen_y;
    int      clip_set;
    int      cx0, cy0, cx1, cy1;
} wasm_dc_t;

typedef struct {
    int      active;
    uint32_t hwnd;
    uint32_t id;
    double   interval_ms;
    double   next_ms;
} wasm_timer_t;

typedef struct {
    uint32_t hwnd, msg, wp, lp;
} wasm_msg_t;

static wasm_wndcls_t  g_classes[32];
static int            g_class_n;
static wasm_wnd_t     g_wnds[32];
static wasm_menu_t    g_menus[32];
static wasm_gdiobj_t  g_objs[256];
static wasm_dc_t      g_dcs[64];
static wasm_timer_t   g_timers[16];
static wasm_msg_t     g_msgq[128];
static int            g_msg_r, g_msg_w;

static uint32_t *g_screen_fb;
static int       g_fb_w = 640, g_fb_h = 480;
static uint32_t  g_main_hwnd;
static char      g_title[128] = "GameReComp WebApp";
static int       g_quit_posted, g_quit_code;
static int       g_open_menu_idx = -1;
static uint32_t  g_capture_hwnd;

static wasm_wnd_t *find_wnd(uint32_t hwnd) {
    for (int i = 0; i < 32; i++) if (g_wnds[i].used && g_wnds[i].hwnd == hwnd) return &g_wnds[i];
    return NULL;
}
static wasm_menu_t *find_menu(uint32_t hmenu) {
    for (int i = 0; i < 32; i++) if (g_menus[i].used && g_menus[i].handle == hmenu) return &g_menus[i];
    return NULL;
}
static wasm_gdiobj_t *find_obj(uint32_t h) {
    for (int i = 0; i < 256; i++) if (g_objs[i].used && g_objs[i].handle == h) return &g_objs[i];
    return NULL;
}
static wasm_dc_t *find_dc(uint32_t hdc) {
    for (int i = 0; i < 64; i++) if (g_dcs[i].used && g_dcs[i].hdc == hdc) return &g_dcs[i];
    return NULL;
}

static uint32_t alloc_obj(int kind) {
    for (int i = 1; i < 256; i++) {
        if (!g_objs[i].used) {
            memset(&g_objs[i], 0, sizeof g_objs[i]);
            g_objs[i].used = 1;
            g_objs[i].kind = kind;
            g_objs[i].handle = 0x2000u + (uint32_t)i;
            return g_objs[i].handle;
        }
    }
    return 0;
}

static void init_stock_objects(void) {
    static int inited = 0;
    if (inited) return;
    inited = 1;
    struct { uint32_t h; int kind; uint32_t col; } stocks[] = {
        { 0x80000000u, GDI_BRUSH, 0x00FFFFFFu }, /* WHITE_BRUSH */
        { 0x80000001u, GDI_BRUSH, 0x00C0C0C0u }, /* LTGRAY_BRUSH */
        { 0x80000002u, GDI_BRUSH, 0x00808080u }, /* GRAY_BRUSH */
        { 0x80000003u, GDI_BRUSH, 0x00404040u }, /* DKGRAY_BRUSH */
        { 0x80000004u, GDI_BRUSH, 0x00000000u }, /* BLACK_BRUSH */
        { 0x80000005u, GDI_BRUSH, 0xFFFFFFFFu }, /* NULL_BRUSH */
        { 0x80000006u, GDI_PEN,   0x00FFFFFFu }, /* WHITE_PEN */
        { 0x80000007u, GDI_PEN,   0x00000000u }, /* BLACK_PEN */
        { 0x80000008u, GDI_PEN,   0xFFFFFFFFu }, /* NULL_PEN */
        { 0x8000000Du, GDI_FONT,  0x00000000u }, /* SYSTEM_FONT */
        { 0x80000011u, GDI_FONT,  0x00000000u }, /* DEFAULT_GUI_FONT */
    };
    for (size_t i = 0; i < sizeof stocks / sizeof stocks[0]; i++) {
        g_objs[i + 200].used = 1;
        g_objs[i + 200].kind = stocks[i].kind;
        g_objs[i + 200].handle = stocks[i].h;
        g_objs[i + 200].color = stocks[i].col;
        g_objs[i + 200].width = 1;
        g_objs[i + 200].font_h = 12;
    }
}

static void ensure_screen_fb(int w, int h) {
    if (w < 64) w = 64;
    if (h < 64) h = 64;
    if (w > MAX_FB_W) w = MAX_FB_W;
    if (h > MAX_FB_H) h = MAX_FB_H;
    if (!g_screen_fb || g_fb_w != w || g_fb_h != h) {
        free(g_screen_fb);
        g_fb_w = w;
        g_fb_h = h;
        g_screen_fb = (uint32_t *)calloc((size_t)w * (size_t)h, 4);
        for (int i = 0; i < w * h; i++) g_screen_fb[i] = 0xFFD8D8DCu;
    }
}

void wasm_ui_init(const char *title) {
    init_stock_objects();
    if (title && *title) snprintf(g_title, sizeof g_title, "%s", title);
    ensure_screen_fb(640, 480);
}

uint32_t wasm_fb_ptr(void) {
    ensure_screen_fb(g_fb_w, g_fb_h);
    return (uint32_t)(uintptr_t)g_screen_fb;
}
int wasm_fb_width(void)  { return g_fb_w; }
int wasm_fb_height(void) { return g_fb_h; }

static int dc_target(wasm_dc_t *dc, uint32_t **out_px, int *out_w, int *out_h, int *out_yoff) {
    if (dc->cur_bm) {
        wasm_gdiobj_t *bm = find_obj(dc->cur_bm);
        if (bm && bm->kind == GDI_BITMAP && bm->bm_px) {
            *out_px = bm->bm_px; *out_w = bm->bm_w; *out_h = bm->bm_h; *out_yoff = 0;
            return 1;
        }
    }
    ensure_screen_fb(g_fb_w, g_fb_h);
    wasm_wnd_t *w = find_wnd(dc->hwnd ? dc->hwnd : g_main_hwnd);
    *out_px = g_screen_fb;
    *out_w = g_fb_w;
    *out_h = g_fb_h;
    *out_yoff = (w && w->hmenu) ? MENU_BAR_H : 0;
    return 1;
}

static void put_px(uint32_t *px, int w, int h, wasm_dc_t *dc, int x, int y, uint32_t rgba) {
    if (x < 0 || y < 0 || x >= w || y >= h) return;
    if (dc && dc->clip_set) {
        if (x < dc->cx0 || x >= dc->cx1 || y < dc->cy0 || y >= dc->cy1) return;
    }
    px[y * w + x] = rgba;
}

static void draw_rect_solid(uint32_t *px, int w, int h, wasm_dc_t *dc,
                            int x0, int y0, int x1, int y1, uint32_t rgba) {
    if (x0 > x1) { int t = x0; x0 = x1; x1 = t; }
    if (y0 > y1) { int t = y0; y0 = y1; y1 = t; }
    for (int y = y0; y < y1; y++) {
        for (int x = x0; x < x1; x++) put_px(px, w, h, dc, x, y, rgba);
    }
}

static void draw_line_raw(uint32_t *px, int w, int h, wasm_dc_t *dc,
                          int x0, int y0, int x1, int y1, uint32_t rgba, int thick) {
    int dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
    int dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;
    int r = thick > 1 ? thick / 2 : 0;
    for (;;) {
        if (r == 0) put_px(px, w, h, dc, x0, y0, rgba);
        else draw_rect_solid(px, w, h, dc, x0 - r, y0 - r, x0 + r + 1, y0 + r + 1, rgba);
        if (x0 == x1 && y0 == y1) break;
        int e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
}

static void draw_glyph(uint32_t *px, int w, int h, wasm_dc_t *dc,
                       int x, int y, char c, uint32_t rgba, int scale) {
    if (c < 0x20 || c > 0x7E) c = '?';
    const uint8_t *col = k_font5x7[c - 0x20];
    for (int cx = 0; cx < 5; cx++) {
        uint8_t bits = col[cx];
        for (int cy = 0; cy < 7; cy++) {
            if (bits & (1u << cy)) {
                draw_rect_solid(px, w, h, dc,
                                x + cx * scale, y + cy * scale,
                                x + (cx + 1) * scale, y + (cy + 1) * scale, rgba);
            }
        }
    }
}

static void draw_text_raw(uint32_t *px, int w, int h, wasm_dc_t *dc,
                          int x, int y, const char *s, int len, uint32_t rgba, int scale) {
    int adv = 6 * scale;
    for (int i = 0; i < len && s[i]; i++) {
        if (s[i] == '&') continue; /* strip Win32 mnemonic '&' */
        draw_glyph(px, w, h, dc, x, y, s[i], rgba, scale);
        x += adv;
    }
}

static int clean_label(const char *src, char *dst, size_t cap) {
    size_t k = 0;
    for (size_t i = 0; src && src[i] && k + 1 < cap; i++) {
        if (src[i] == '&') continue;
        if (src[i] == '\t') { dst[k++] = ' '; continue; }
        dst[k++] = src[i];
    }
    dst[k] = 0;
    return (int)k;
}

static void draw_menu_overlay(void) {
    wasm_wnd_t *w = find_wnd(g_main_hwnd);
    if (!w || !w->hmenu) return;
    wasm_menu_t *m = find_menu(w->hmenu);
    if (!m) return;
    ensure_screen_fb(g_fb_w, g_fb_h);
    draw_rect_solid(g_screen_fb, g_fb_w, g_fb_h, NULL, 0, 0, g_fb_w, MENU_BAR_H, 0xFFECEAE6u);
    draw_rect_solid(g_screen_fb, g_fb_w, g_fb_h, NULL, 0, MENU_BAR_H - 1, g_fb_w, MENU_BAR_H, 0xFFAAA8A4u);
    int x = 8;
    for (int i = 0; i < m->count; i++) {
        char lbl[64];
        int n = clean_label(m->items[i].label, lbl, sizeof lbl);
        int item_w = n * 6 + 14;
        if (i == g_open_menu_idx) {
            draw_rect_solid(g_screen_fb, g_fb_w, g_fb_h, NULL, x - 3, 2, x + item_w - 3, MENU_BAR_H - 2, 0xFF4A7AC9u);
            draw_text_raw(g_screen_fb, g_fb_w, g_fb_h, NULL, x + 2, 7, lbl, n, 0xFFFFFFFFu, 1);
        } else {
            draw_text_raw(g_screen_fb, g_fb_w, g_fb_h, NULL, x + 2, 7, lbl, n, 0xFF181818u, 1);
        }
        if (i == g_open_menu_idx && m->items[i].submenu) {
            wasm_menu_t *sub = find_menu(m->items[i].submenu);
            if (sub && sub->count > 0) {
                int drop_w = 170;
                int drop_h = sub->count * 18 + 6;
                draw_rect_solid(g_screen_fb, g_fb_w, g_fb_h, NULL, x - 2, MENU_BAR_H, x + drop_w, MENU_BAR_H + drop_h, 0xFFF4F4F6u);
                draw_line_raw(g_screen_fb, g_fb_w, g_fb_h, NULL, x - 2, MENU_BAR_H, x + drop_w, MENU_BAR_H, 0xFF707070u, 1);
                draw_line_raw(g_screen_fb, g_fb_w, g_fb_h, NULL, x + drop_w, MENU_BAR_H, x + drop_w, MENU_BAR_H + drop_h, 0xFF707070u, 1);
                draw_line_raw(g_screen_fb, g_fb_w, g_fb_h, NULL, x - 2, MENU_BAR_H + drop_h, x + drop_w, MENU_BAR_H + drop_h, 0xFF707070u, 1);
                draw_line_raw(g_screen_fb, g_fb_w, g_fb_h, NULL, x - 2, MENU_BAR_H, x - 2, MENU_BAR_H + drop_h, 0xFF707070u, 1);
                for (int s = 0; s < sub->count; s++) {
                    int sy = MENU_BAR_H + 3 + s * 18;
                    if (sub->items[s].flags & 0x0800u) {
                        draw_rect_solid(g_screen_fb, g_fb_w, g_fb_h, NULL, x + 4, sy + 8, x + drop_w - 6, sy + 9, 0xFFB8B8BCu);
                    } else {
                        char slbl[64];
                        int sn = clean_label(sub->items[s].label, slbl, sizeof slbl);
                        if (sub->items[s].flags & 0x0008u) {
                            draw_text_raw(g_screen_fb, g_fb_w, g_fb_h, NULL, x + 4, sy + 5, "*", 1, 0xFF106010u, 1);
                        }
                        draw_text_raw(g_screen_fb, g_fb_w, g_fb_h, NULL, x + 14, sy + 5, slbl, sn, 0xFF181818u, 1);
                    }
                }
            }
        }
        x += item_w;
    }
}

void wasm_ui_present(void) {
    draw_menu_overlay();
    wasm_host_present_fb(g_screen_fb, g_fb_w, g_fb_h, g_title);
}

int wasm_ui_has_window(void) { return g_main_hwnd != 0; }

static void enqueue_msg(uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp) {
    int nxt = (g_msg_w + 1) & 127;
    if (nxt == g_msg_r) return;
    g_msgq[g_msg_w] = (wasm_msg_t){ hwnd, msg, wp, lp };
    g_msg_w = nxt;
}

void wasm_ui_enqueue_command(uint32_t cmd_id) {
    if (g_main_hwnd) enqueue_msg(g_main_hwnd, 0x0111u /* WM_COMMAND */, cmd_id & 0xFFFFu, 0);
}

static uint32_t send_to_wnd(uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp) {
    wasm_wnd_t *w = find_wnd(hwnd);
    if (!w || !w->wndproc) return 0;
    uint32_t a[4] = { hwnd, msg, wp, lp };
    return (uint32_t)hle_call_guest(w->wndproc, 4, a);
}

void wasm_mouse_event(int type, int x, int y, int btn) {
    wasm_wnd_t *w = find_wnd(g_main_hwnd);
    if (!w) return;
    int y_off = w->hmenu ? MENU_BAR_H : 0;
    if (type == 2 /* down */ && w->hmenu && y < MENU_BAR_H) {
        wasm_menu_t *m = find_menu(w->hmenu);
        int mx = 8;
        int hit = -1;
        for (int i = 0; m && i < m->count; i++) {
            char lbl[64];
            int n = clean_label(m->items[i].label, lbl, sizeof lbl);
            int iw = n * 6 + 14;
            if (x >= mx - 3 && x < mx + iw - 3) { hit = i; break; }
            mx += iw;
        }
        if (hit >= 0 && !m->items[hit].is_popup && m->items[hit].id) {
            enqueue_msg(w->hwnd, 0x0111u, m->items[hit].id, 0);
            g_open_menu_idx = -1;
        } else {
            g_open_menu_idx = (g_open_menu_idx == hit) ? -1 : hit;
        }
        w->dirty = 1;
        wasm_ui_present();
        return;
    }
    if (type == 2 && g_open_menu_idx >= 0 && w->hmenu) {
        wasm_menu_t *m = find_menu(w->hmenu);
        int mx = 8;
        for (int i = 0; m && i < g_open_menu_idx; i++) {
            char lbl[64];
            int n = clean_label(m->items[i].label, lbl, sizeof lbl);
            mx += n * 6 + 14;
        }
        wasm_menu_t *sub = (m && m->items[g_open_menu_idx].submenu) ? find_menu(m->items[g_open_menu_idx].submenu) : NULL;
        if (sub && x >= mx - 2 && x <= mx + 170 && y >= MENU_BAR_H && y < MENU_BAR_H + sub->count * 18 + 6) {
            int idx = (y - MENU_BAR_H - 3) / 18;
            if (idx >= 0 && idx < sub->count && !(sub->items[idx].flags & 0x0800u)) {
                enqueue_msg(w->hwnd, 0x0111u, sub->items[idx].id, 0);
            }
            g_open_menu_idx = -1;
            w->dirty = 1;
            wasm_ui_present();
            return;
        }
        g_open_menu_idx = -1;
        w->dirty = 1;
    }
    int cy = y - y_off;
    if (cy < 0) cy = 0;
    uint32_t lp = ((uint32_t)(uint16_t)cy << 16) | (uint16_t)x;
    uint32_t msg = 0x0200u; /* WM_MOUSEMOVE */
    uint32_t wp = 0;
    if (type == 2) msg = (btn == 2) ? 0x0204u : 0x0201u; /* WM_RBUTTONDOWN / WM_LBUTTONDOWN */
    else if (type == 3) msg = (btn == 2) ? 0x0205u : 0x0202u; /* WM_RBUTTONUP / WM_LBUTTONUP */
    if (btn == 0 && (type == 1 || type == 2)) wp = 1;
    enqueue_msg(g_capture_hwnd ? g_capture_hwnd : g_main_hwnd, msg, wp, lp);
}

void wasm_key_event(int down, int vk, int ch) {
    if (!g_main_hwnd) return;
    enqueue_msg(g_main_hwnd, down ? 0x0100u : 0x0101u, (uint32_t)vk, 1u);
    if (down && ch >= 1) enqueue_msg(g_main_hwnd, 0x0102u, (uint32_t)ch, 1u);
}

void wasm_menu_action(int cmd_id) {
    wasm_ui_enqueue_command((uint32_t)cmd_id);
}

static void pump_host_events(int wait_ms) {
    double now = wasm_host_now_ms();
    for (int i = 0; i < 16; i++) {
        if (g_timers[i].active && now >= g_timers[i].next_ms) {
            g_timers[i].next_ms = now + (g_timers[i].interval_ms > 1.0 ? g_timers[i].interval_ms : 16.0);
            enqueue_msg(g_timers[i].hwnd, 0x0113u /* WM_TIMER */, g_timers[i].id, 0);
        }
    }
    int ev[5] = {0, 0, 0, 0, 0};
    int rc = wasm_host_wait_event(wait_ms, ev);
    if (rc < 0 || ev[0] == -1) {
        g_quit_posted = 1;
        enqueue_msg(g_main_hwnd, 0x0012u /* WM_QUIT */, 0, 0);
        return;
    }
    if (ev[0] == 1 || ev[0] == 2 || ev[0] == 3) wasm_mouse_event(ev[0], ev[1], ev[2], ev[3]);
    else if (ev[0] == 4 || ev[0] == 5) wasm_key_event(ev[0] == 4, ev[3], ev[4]);
    else if (ev[0] == 6) wasm_ui_enqueue_command((uint32_t)ev[3]);
}

/* ============================================================
 * GDI32 / USER32 Shims
 * ============================================================ */
static void w_RegisterClassA(void) {
    uint32_t wc = A32(0);
    if (!wc || g_class_n >= 32) RETS(1, 0);
    wasm_wndcls_t *c = &g_classes[g_class_n++];
    c->wndproc = MEM32(wc + 4);
    c->brush   = MEM32(wc + 24);
    c->extra   = (int)MEM32(wc + 12);
    const char *nm = MEM32(wc + 36) ? (const char *)hle_ptr(MEM32(wc + 36)) : "cls";
    snprintf(c->cls, sizeof c->cls, "%s", nm);
    RETS(1, 0x100u + (uint32_t)g_class_n);
}

static void w_RegisterClassExA(void) {
    uint32_t wc = A32(0);
    if (!wc || g_class_n >= 32) RETS(1, 0);
    wasm_wndcls_t *c = &g_classes[g_class_n++];
    c->wndproc = MEM32(wc + 8);
    c->brush   = MEM32(wc + 28);
    c->extra   = (int)MEM32(wc + 16);
    const char *nm = MEM32(wc + 40) ? (const char *)hle_ptr(MEM32(wc + 40)) : "cls";
    snprintf(c->cls, sizeof c->cls, "%s", nm);
    RETS(1, 0x100u + (uint32_t)g_class_n);
}

static void w_CreateWindowExA(void) {
    const char *cls = (A32(1) > 0xFFFFu) ? ASTR(1) : "cls";
    const char *title = ASTR(2);
    uint32_t style = A32(3);
    int x = (int32_t)A32(4), y = (int32_t)A32(5);
    int w = (int32_t)A32(6), h = (int32_t)A32(7);
    uint32_t parent = A32(8), hmenu = A32(9), lp = A32(11);
    if (w <= 0 || (uint32_t)w == 0x80000000u) w = 640;
    if (h <= 0 || (uint32_t)h == 0x80000000u) h = 480;
    if (x == (int)0x80000000u) x = 0;
    if (y == (int)0x80000000u) y = 0;
    uint32_t wndproc = 0;
    for (int i = 0; i < g_class_n; i++) {
        if (cls && !strcasecmp(g_classes[i].cls, cls)) { wndproc = g_classes[i].wndproc; break; }
    }
    if (!wndproc && g_class_n > 0) wndproc = g_classes[0].wndproc;
    wasm_wnd_t *slot = NULL;
    uint32_t hwnd = 0;
    for (int i = 1; i < 32; i++) {
        if (!g_wnds[i].used) { slot = &g_wnds[i]; hwnd = 0x10000u + (uint32_t)i; break; }
    }
    if (!slot) RETS(12, 0);
    memset(slot, 0, sizeof *slot);
    slot->used = 1; slot->hwnd = hwnd; slot->parent = parent; slot->wndproc = wndproc;
    slot->hmenu = (!parent && hmenu) ? hmenu : 0; slot->style = style; slot->exstyle = A32(0);
    slot->x = x; slot->y = y; slot->w = w; slot->h = h; slot->dirty = 1;
    if (title) snprintf(slot->title, sizeof slot->title, "%s", title);
    if (!g_main_hwnd && !parent) {
        g_main_hwnd = hwnd;
        if (title && *title) snprintf(g_title, sizeof g_title, "%s", title);
        ensure_screen_fb(w, h);
    }
    if (wndproc) {
        uint32_t cs = hle_guest_alloc(48);
        MEM32(cs + 0) = lp; MEM32(cs + 16) = (uint32_t)h; MEM32(cs + 20) = (uint32_t)w;
        send_to_wnd(hwnd, 0x0001u /* WM_CREATE */, 0, cs);
    }
    RETS(12, hwnd);
}

static void w_DestroyWindow(void) {
    uint32_t hwnd = A32(0);
    wasm_wnd_t *w = find_wnd(hwnd);
    if (w) {
        send_to_wnd(hwnd, 0x0002u /* WM_DESTROY */, 0, 0);
        w->used = 0;
        if (hwnd == g_main_hwnd) g_main_hwnd = 0;
    }
    RETS(1, 1);
}

static void w_ShowWindow(void) {
    wasm_wnd_t *w = find_wnd(A32(0));
    if (w) {
        w->visible = A32(1) != 0;
        w->dirty = 1;
        enqueue_msg(w->hwnd, 0x000Fu /* WM_PAINT */, 0, 0);
    }
    RETS(2, 1);
}

static void w_UpdateWindow(void) {
    wasm_wnd_t *w = find_wnd(A32(0));
    if (w && w->dirty) {
        w->dirty = 0;
        send_to_wnd(w->hwnd, 0x000Fu /* WM_PAINT */, 0, 0);
        wasm_ui_present();
    }
    RETS(1, 1);
}

static void w_InvalidateRect(void) {
    wasm_wnd_t *w = find_wnd(A32(0) ? A32(0) : g_main_hwnd);
    if (w) {
        w->dirty = 1;
        enqueue_msg(w->hwnd, 0x000Fu /* WM_PAINT */, 0, 0);
    }
    RETS(3, 1);
}

static void w_GetClientRect(void) {
    wasm_wnd_t *w = find_wnd(A32(0));
    uint32_t rc = A32(1);
    int cw = w ? w->w : g_fb_w;
    int ch = w ? (w->h - (w->hmenu ? MENU_BAR_H : 0)) : g_fb_h;
    if (ch < 1) ch = 1;
    if (rc) { MEM32(rc) = 0; MEM32(rc + 4) = 0; MEM32(rc + 8) = (uint32_t)cw; MEM32(rc + 12) = (uint32_t)ch; }
    RETS(2, 1);
}

static void w_GetWindowRect(void) {
    wasm_wnd_t *w = find_wnd(A32(0));
    uint32_t rc = A32(1);
    if (rc) {
        MEM32(rc) = (uint32_t)(w ? w->x : 0); MEM32(rc + 4) = (uint32_t)(w ? w->y : 0);
        MEM32(rc + 8) = (uint32_t)((w ? w->x : 0) + (w ? w->w : g_fb_w));
        MEM32(rc + 12) = (uint32_t)((w ? w->y : 0) + (w ? w->h : g_fb_h));
    }
    RETS(2, 1);
}

static void w_AdjustWindowRectEx(void) {
    uint32_t rc = A32(0);
    int has_menu = A32(2) != 0;
    if (rc && has_menu) {
        int32_t bottom = (int32_t)MEM32(rc + 12);
        MEM32(rc + 12) = (uint32_t)(bottom + MENU_BAR_H);
    }
    RETS(4, 1);
}

static void w_SetWindowPos(void) {
    wasm_wnd_t *w = find_wnd(A32(0));
    int nw = (int32_t)A32(4), nh = (int32_t)A32(5);
    uint32_t flags = A32(6);
    if (w && !(flags & 1u /* SWP_NOSIZE */) && nw > 0 && nh > 0) {
        w->w = nw; w->h = nh;
        if (w->hwnd == g_main_hwnd) ensure_screen_fb(nw, nh);
        uint32_t ch = (uint32_t)(nh - (w->hmenu ? MENU_BAR_H : 0));
        send_to_wnd(w->hwnd, 0x0005u /* WM_SIZE */, 0, (ch << 16) | ((uint32_t)nw & 0xFFFFu));
        w->dirty = 1;
    }
    RETS(7, 1);
}

static void w_SetWindowTextA(void) {
    wasm_wnd_t *w = find_wnd(A32(0));
    const char *s = ASTR(1);
    if (w && s) {
        snprintf(w->title, sizeof w->title, "%s", s);
        if (w->hwnd == g_main_hwnd) snprintf(g_title, sizeof g_title, "%s", s);
    }
    RETS(2, 1);
}

static void w_GetWindowLongA(void) {
    wasm_wnd_t *w = find_wnd(A32(0));
    int idx = (int32_t)A32(1);
    if (!w) RETS(2, 0);
    if (idx == -4) RETS(2, w->wndproc);
    if (idx == -16) RETS(2, w->style);
    if (idx == -20) RETS(2, w->exstyle);
    if (idx == -21) RETS(2, w->user_data);
    RETS(2, 0);
}

static void w_SetWindowLongA(void) {
    wasm_wnd_t *w = find_wnd(A32(0));
    int idx = (int32_t)A32(1); uint32_t v = A32(2);
    if (!w) RETS(3, 0);
    uint32_t old = 0;
    if (idx == -4) { old = w->wndproc; w->wndproc = v; }
    else if (idx == -16) { old = w->style; w->style = v; }
    else if (idx == -20) { old = w->exstyle; w->exstyle = v; }
    else if (idx == -21) { old = w->user_data; w->user_data = v; }
    RETS(3, old);
}

static void w_DefWindowProcA(void) {
    uint32_t msg = A32(1);
    if (msg == 0x0010u /* WM_CLOSE */) {
        uint32_t hwnd = A32(0);
        wasm_wnd_t *w = find_wnd(hwnd);
        if (w) send_to_wnd(hwnd, 0x0002u, 0, 0);
    }
    RETS(4, 0);
}
static void w_DefDlgProcA(void) { RETS(4, 0); }

static void w_CreateMenu(void) {
    for (int i = 1; i < 32; i++) {
        if (!g_menus[i].used) {
            memset(&g_menus[i], 0, sizeof g_menus[i]);
            g_menus[i].used = 1;
            g_menus[i].handle = 0x30000u + (uint32_t)i;
            RETS(0, g_menus[i].handle);
        }
    }
    RETS(0, 0);
}
static void w_DestroyMenu(void) {
    wasm_menu_t *m = find_menu(A32(0));
    if (m) m->used = 0;
    RETS(1, 1);
}
static void w_AppendMenuA(void) {
    wasm_menu_t *m = find_menu(A32(0));
    uint32_t flags = A32(1), id = A32(2);
    const char *lbl = (flags & 0x0004u /* MF_BITMAP */) ? "" : ASTR(3);
    if (!m || m->count >= 32) RETS(4, 0);
    wasm_menuitem_t *it = &m->items[m->count++];
    it->used = 1;
    it->flags = flags;
    it->is_popup = (flags & 0x0010u) != 0;
    it->submenu = it->is_popup ? id : 0;
    it->id = it->is_popup ? 0 : id;
    snprintf(it->label, sizeof it->label, "%s", lbl ? lbl : "");
    RETS(4, 1);
}
static void w_InsertMenuA(void) {
    wasm_menu_t *m = find_menu(A32(0));
    uint32_t flags = A32(2), id = A32(3);
    const char *lbl = (flags & 0x0004u) ? "" : ASTR(4);
    if (!m || m->count >= 32) RETS(5, 0);
    wasm_menuitem_t *it = &m->items[m->count++];
    it->used = 1; it->flags = flags; it->is_popup = (flags & 0x0010u) != 0;
    it->submenu = it->is_popup ? id : 0; it->id = it->is_popup ? 0 : id;
    snprintf(it->label, sizeof it->label, "%s", lbl ? lbl : "");
    RETS(5, 1);
}
static void w_ModifyMenuA(void) {
    wasm_menu_t *m = find_menu(A32(0));
    uint32_t pos = A32(1), flags = A32(2), id = A32(3);
    const char *lbl = (flags & 0x0004u) ? "" : ASTR(4);
    if (!m) RETS(5, 0);
    int idx = (flags & 0x0400u /* MF_BYPOSITION */) ? (int)pos : -1;
    if (idx < 0) for (int i = 0; i < m->count; i++) if (m->items[i].id == pos) { idx = i; break; }
    if (idx >= 0 && idx < m->count) {
        m->items[idx].flags = flags;
        m->items[idx].is_popup = (flags & 0x0010u) != 0;
        if (m->items[idx].is_popup) m->items[idx].submenu = id; else m->items[idx].id = id;
        if (lbl) snprintf(m->items[idx].label, sizeof m->items[idx].label, "%s", lbl);
    }
    RETS(5, 1);
}
static void w_GetMenu(void) {
    wasm_wnd_t *w = find_wnd(A32(0));
    RETS(1, w ? w->hmenu : 0);
}
static void w_SetMenu(void) {
    wasm_wnd_t *w = find_wnd(A32(0));
    if (w) { w->hmenu = A32(1); w->dirty = 1; }
    RETS(2, 1);
}
static void w_DrawMenuBar(void) { draw_menu_overlay(); RETS(1, 1); }
static void w_CheckMenuItem(void) {
    wasm_menu_t *m = find_menu(A32(0));
    uint32_t id = A32(1), chk = A32(2);
    if (m) {
        for (int i = 0; i < m->count; i++) {
            if (m->items[i].id == id) {
                if (chk & 0x0008u) m->items[i].flags |= 0x0008u; else m->items[i].flags &= ~0x0008u;
            }
        }
    }
    RETS(3, 0);
}
static void w_EnableMenuItem(void) { RETS(3, 0); }
static void w_GetMenuItemCount(void) {
    wasm_menu_t *m = find_menu(A32(0));
    RETS(1, m ? (uint32_t)m->count : 0u);
}
static void w_GetMenuItemRect(void) {
    uint32_t rc = A32(3);
    int pos = (int)A32(2);
    if (rc) {
        MEM32(rc) = (uint32_t)(pos * 50); MEM32(rc + 4) = 0;
        MEM32(rc + 8) = (uint32_t)(pos * 50 + 50); MEM32(rc + 12) = MENU_BAR_H;
    }
    RETS(4, 1);
}

static uint32_t alloc_dc(uint32_t hwnd) {
    init_stock_objects();
    for (int i = 1; i < 64; i++) {
        if (!g_dcs[i].used) {
            memset(&g_dcs[i], 0, sizeof g_dcs[i]);
            g_dcs[i].used = 1;
            g_dcs[i].hdc = 0x40000u + (uint32_t)i;
            g_dcs[i].hwnd = hwnd;
            g_dcs[i].cur_pen = 0x80000007u;
            g_dcs[i].cur_brush = 0x80000000u;
            g_dcs[i].text_color = 0x00000000u;
            g_dcs[i].bk_color = 0x00FFFFFFu;
            g_dcs[i].bk_mode = 2;
            return g_dcs[i].hdc;
        }
    }
    return 0;
}

static void w_BeginPaint(void) {
    uint32_t hwnd = A32(0), ps = A32(1);
    uint32_t hdc = alloc_dc(hwnd);
    wasm_wnd_t *w = find_wnd(hwnd);
    int cw = w ? w->w : g_fb_w;
    int ch = w ? (w->h - (w->hmenu ? MENU_BAR_H : 0)) : g_fb_h;
    if (ps) {
        memset(hle_ptr(ps), 0, 64);
        MEM32(ps + 0) = hdc; MEM32(ps + 4) = 1;
        MEM32(ps + 8) = 0; MEM32(ps + 12) = 0; MEM32(ps + 16) = (uint32_t)cw; MEM32(ps + 20) = (uint32_t)ch;
    }
    RETS(2, hdc);
}
static void w_EndPaint(void) {
    uint32_t ps = A32(1);
    if (ps) {
        wasm_dc_t *dc = find_dc(MEM32(ps));
        if (dc) dc->used = 0;
    }
    wasm_ui_present();
    RETS(2, 1);
}
static void w_GetDC(void) { RETS(1, alloc_dc(A32(0) ? A32(0) : g_main_hwnd)); }
static void w_ReleaseDC(void) {
    wasm_dc_t *dc = find_dc(A32(1));
    if (dc) dc->used = 0;
    RETS(2, 1);
}
static void w_CreateCompatibleDC(void) { RETS(1, alloc_dc(0)); }
static void w_DeleteDC(void) {
    wasm_dc_t *dc = find_dc(A32(0));
    if (dc) dc->used = 0;
    RETS(1, 1);
}
static void w_CreateCompatibleBitmap(void) {
    int w = (int32_t)A32(1), h = (int32_t)A32(2);
    if (w < 1) w = 1;
    if (h < 1) h = 1;
    uint32_t obj = alloc_obj(GDI_BITMAP);
    if (obj) {
        wasm_gdiobj_t *o = find_obj(obj);
        o->bm_w = w; o->bm_h = h;
        o->bm_px = (uint32_t *)calloc((size_t)w * (size_t)h, 4);
    }
    RETS(3, obj);
}
static void w_SelectObject(void) {
    wasm_dc_t *dc = find_dc(A32(0));
    wasm_gdiobj_t *o = find_obj(A32(1));
    if (!dc || !o) RETS(2, 0);
    uint32_t old = 0;
    if (o->kind == GDI_PEN) { old = dc->cur_pen; dc->cur_pen = o->handle; }
    else if (o->kind == GDI_BRUSH) { old = dc->cur_brush; dc->cur_brush = o->handle; }
    else if (o->kind == GDI_FONT) { old = dc->cur_font; dc->cur_font = o->handle; }
    else if (o->kind == GDI_BITMAP) { old = dc->cur_bm; dc->cur_bm = o->handle; }
    RETS(2, old);
}
static void w_DeleteObject(void) {
    uint32_t h = A32(0);
    if (h >= 0x80000000u) RETS(1, 1);
    wasm_gdiobj_t *o = find_obj(h);
    if (o) {
        if (o->kind == GDI_BITMAP) free(o->bm_px);
        o->used = 0;
    }
    RETS(1, 1);
}
static void w_GetStockObject(void) { init_stock_objects(); RETS(1, 0x80000000u + A32(0)); }
static void w_CreatePen(void) {
    uint32_t obj = alloc_obj(GDI_PEN);
    wasm_gdiobj_t *o = find_obj(obj);
    if (o) { o->style = (int)A32(0); o->width = (int)A32(1) ? (int)A32(1) : 1; o->color = (o->style == 5) ? 0xFFFFFFFFu : A32(2); }
    RETS(3, obj);
}
static void w_CreateSolidBrush(void) {
    uint32_t obj = alloc_obj(GDI_BRUSH);
    wasm_gdiobj_t *o = find_obj(obj);
    if (o) o->color = A32(0);
    RETS(1, obj);
}
static void w_CreateHatchBrush(void) {
    uint32_t obj = alloc_obj(GDI_BRUSH);
    wasm_gdiobj_t *o = find_obj(obj);
    if (o) o->color = A32(1);
    RETS(2, obj);
}
static void w_CreateFontA(void) {
    uint32_t obj = alloc_obj(GDI_FONT);
    wasm_gdiobj_t *o = find_obj(obj);
    if (o) { int h = abs((int32_t)A32(0)); o->font_h = h ? h : 12; }
    RETS(14, obj);
}
static void w_CreateFontIndirectA(void) {
    uint32_t lf = A32(0);
    uint32_t obj = alloc_obj(GDI_FONT);
    wasm_gdiobj_t *o = find_obj(obj);
    if (o && lf) { int h = abs((int32_t)MEM32(lf)); o->font_h = h ? h : 12; }
    RETS(1, obj);
}
static void w_GetDeviceCaps(void) {
    int cap = (int32_t)A32(1);
    if (cap == 88 || cap == 90) RETS(2, 96); /* LOGPIXELSX/Y */
    if (cap == 12) RETS(2, 32);              /* BITSPIXEL */
    if (cap == 8)  RETS(2, (uint32_t)g_fb_w);
    if (cap == 10) RETS(2, (uint32_t)g_fb_h);
    RETS(2, 0);
}
static void w_GetTextMetricsA(void) {
    uint32_t tm = A32(1);
    if (tm) {
        memset(hle_ptr(tm), 0, 56);
        MEM32(tm + 0) = 12; MEM32(tm + 4) = 10; MEM32(tm + 8) = 2;
        MEM32(tm + 20) = 6; MEM32(tm + 24) = 8;
    }
    RETS(2, 1);
}
static void w_GetTextExtentPoint32A(void) {
    int n = (int)A32(2); uint32_t sz = A32(3);
    if (sz) { MEM32(sz) = (uint32_t)(n * 6); MEM32(sz + 4) = 12; }
    RETS(4, 1);
}
static void w_GetTextExtentPoint32W(void) { w_GetTextExtentPoint32A(); }
static void w_SetBkMode(void) { wasm_dc_t *dc = find_dc(A32(0)); int old = dc ? dc->bk_mode : 2; if (dc) dc->bk_mode = (int)A32(1); RETS(2, (uint32_t)old); }
static void w_SetBkColor(void) { wasm_dc_t *dc = find_dc(A32(0)); uint32_t old = dc ? dc->bk_color : 0; if (dc) dc->bk_color = A32(1); RETS(2, old); }
static void w_SetTextColor(void) { wasm_dc_t *dc = find_dc(A32(0)); uint32_t old = dc ? dc->text_color : 0; if (dc) dc->text_color = A32(1); RETS(2, old); }
static void w_SetMapMode(void) { RETS(2, 1); }
static void w_SelectClipRgn(void) { wasm_dc_t *dc = find_dc(A32(0)); if (dc && !A32(1)) dc->clip_set = 0; RETS(2, 1); }
static void w_IntersectClipRect(void) {
    wasm_dc_t *dc = find_dc(A32(0));
    if (dc) {
        uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
        dc->clip_set = 1; dc->cx0 = (int32_t)A32(1); dc->cy0 = (int32_t)A32(2) + yoff;
        dc->cx1 = (int32_t)A32(3); dc->cy1 = (int32_t)A32(4) + yoff;
    }
    RETS(5, 2);
}

static void w_SetPixel(void) {
    wasm_dc_t *dc = find_dc(A32(0));
    if (dc) {
        uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
        put_px(px, w, h, dc, (int32_t)A32(1), (int32_t)A32(2) + yoff, 0xFF000000u | (A32(3) & 0xFFFFFFu));
    }
    RETS(4, A32(3));
}
static void w_FillRect(void) {
    wasm_dc_t *dc = find_dc(A32(0)); uint32_t rc = A32(1), hbr = A32(2);
    if (dc && rc) {
        wasm_gdiobj_t *br = find_obj(hbr);
        uint32_t col = br ? br->color : 0x00FFFFFFu;
        if (col != 0xFFFFFFFFu) {
            uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
            draw_rect_solid(px, w, h, dc, (int32_t)MEM32(rc), (int32_t)MEM32(rc + 4) + yoff,
                            (int32_t)MEM32(rc + 8), (int32_t)MEM32(rc + 12) + yoff, 0xFF000000u | (col & 0xFFFFFFu));
        }
    }
    RETS(3, 1);
}
static void w_Rectangle(void) {
    wasm_dc_t *dc = find_dc(A32(0));
    if (dc) {
        uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
        int x0 = (int32_t)A32(1), y0 = (int32_t)A32(2) + yoff, x1 = (int32_t)A32(3), y1 = (int32_t)A32(4) + yoff;
        wasm_gdiobj_t *br = find_obj(dc->cur_brush), *pen = find_obj(dc->cur_pen);
        if (br && br->color != 0xFFFFFFFFu) draw_rect_solid(px, w, h, dc, x0, y0, x1, y1, 0xFF000000u | (br->color & 0xFFFFFFu));
        if (pen && pen->color != 0xFFFFFFFFu) {
            uint32_t pc = 0xFF000000u | (pen->color & 0xFFFFFFu);
            draw_line_raw(px, w, h, dc, x0, y0, x1 - 1, y0, pc, pen->width);
            draw_line_raw(px, w, h, dc, x1 - 1, y0, x1 - 1, y1 - 1, pc, pen->width);
            draw_line_raw(px, w, h, dc, x1 - 1, y1 - 1, x0, y1 - 1, pc, pen->width);
            draw_line_raw(px, w, h, dc, x0, y1 - 1, x0, y0, pc, pen->width);
        }
    }
    RETS(5, 1);
}
static void w_Ellipse(void) {
    wasm_dc_t *dc = find_dc(A32(0));
    if (dc) {
        uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
        int x0 = (int32_t)A32(1), y0 = (int32_t)A32(2) + yoff, x1 = (int32_t)A32(3), y1 = (int32_t)A32(4) + yoff;
        double cx = 0.5 * (x0 + x1), cy = 0.5 * (y0 + y1), rx = 0.5 * abs(x1 - x0), ry = 0.5 * abs(y1 - y0);
        wasm_gdiobj_t *br = find_obj(dc->cur_brush), *pen = find_obj(dc->cur_pen);
        if (rx >= 0.5 && ry >= 0.5) {
            for (int yy = y0; yy <= y1; yy++) {
                double ny = (yy - cy) / ry;
                for (int xx = x0; xx <= x1; xx++) {
                    double nx = (xx - cx) / rx;
                    double d = nx * nx + ny * ny;
                    if (d <= 1.0) {
                        if (d >= 0.85 && pen && pen->color != 0xFFFFFFFFu)
                            put_px(px, w, h, dc, xx, yy, 0xFF000000u | (pen->color & 0xFFFFFFu));
                        else if (br && br->color != 0xFFFFFFFFu)
                            put_px(px, w, h, dc, xx, yy, 0xFF000000u | (br->color & 0xFFFFFFu));
                    }
                }
            }
        }
    }
    RETS(5, 1);
}
static void w_Polygon(void) {
    wasm_dc_t *dc = find_dc(A32(0)); uint32_t pts = A32(1); int n = (int32_t)A32(2);
    if (dc && pts && n >= 2) {
        uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
        wasm_gdiobj_t *br = find_obj(dc->cur_brush), *pen = find_obj(dc->cur_pen);
        if (br && br->color != 0xFFFFFFFFu && n >= 3 && n <= 64) {
            int miny = 100000, maxy = -100000;
            for (int i = 0; i < n; i++) {
                int py = (int32_t)MEM32(pts + (uint32_t)i * 8u + 4u) + yoff;
                if (py < miny) miny = py;
                if (py > maxy) maxy = py;
            }
            if (miny < 0) miny = 0;
            if (maxy >= h) maxy = h - 1;
            uint32_t fc = 0xFF000000u | (br->color & 0xFFFFFFu);
            for (int y = miny; y <= maxy; y++) {
                int xs[64], xn = 0;
                for (int i = 0; i < n; i++) {
                    int j = (i + 1) % n;
                    int x1 = (int32_t)MEM32(pts + (uint32_t)i * 8u), y1 = (int32_t)MEM32(pts + (uint32_t)i * 8u + 4u) + yoff;
                    int x2 = (int32_t)MEM32(pts + (uint32_t)j * 8u), y2 = (int32_t)MEM32(pts + (uint32_t)j * 8u + 4u) + yoff;
                    if ((y1 <= y && y2 > y) || (y2 <= y && y1 > y)) {
                        xs[xn++] = x1 + (y - y1) * (x2 - x1) / (y2 - y1);
                    }
                }
                for (int a = 0; a < xn; a++) for (int b = a + 1; b < xn; b++) if (xs[b] < xs[a]) { int t = xs[a]; xs[a] = xs[b]; xs[b] = t; }
                for (int a = 0; a + 1 < xn; a += 2) draw_rect_solid(px, w, h, dc, xs[a], y, xs[a + 1] + 1, y + 1, fc);
            }
        }
        if (pen && pen->color != 0xFFFFFFFFu) {
            uint32_t pc = 0xFF000000u | (pen->color & 0xFFFFFFu);
            for (int i = 0; i < n; i++) {
                int j = (i + 1) % n;
                draw_line_raw(px, w, h, dc,
                              (int32_t)MEM32(pts + (uint32_t)i * 8u), (int32_t)MEM32(pts + (uint32_t)i * 8u + 4u) + yoff,
                              (int32_t)MEM32(pts + (uint32_t)j * 8u), (int32_t)MEM32(pts + (uint32_t)j * 8u + 4u) + yoff,
                              pc, pen->width);
            }
        }
    }
    RETS(3, 1);
}
static void w_Polyline(void) {
    wasm_dc_t *dc = find_dc(A32(0)); uint32_t pts = A32(1); int n = (int32_t)A32(2);
    if (dc && pts && n >= 2) {
        uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
        wasm_gdiobj_t *pen = find_obj(dc->cur_pen);
        if (pen && pen->color != 0xFFFFFFFFu) {
            uint32_t pc = 0xFF000000u | (pen->color & 0xFFFFFFu);
            for (int i = 0; i + 1 < n; i++) {
                draw_line_raw(px, w, h, dc,
                              (int32_t)MEM32(pts + (uint32_t)i * 8u), (int32_t)MEM32(pts + (uint32_t)i * 8u + 4u) + yoff,
                              (int32_t)MEM32(pts + (uint32_t)(i + 1) * 8u), (int32_t)MEM32(pts + (uint32_t)(i + 1) * 8u + 4u) + yoff,
                              pc, pen->width);
            }
        }
    }
    RETS(3, 1);
}
static void w_MoveToEx(void) {
    wasm_dc_t *dc = find_dc(A32(0)); uint32_t oldp = A32(3);
    if (dc) { if (oldp) { MEM32(oldp) = (uint32_t)dc->pen_x; MEM32(oldp + 4) = (uint32_t)dc->pen_y; } dc->pen_x = (int32_t)A32(1); dc->pen_y = (int32_t)A32(2); }
    RETS(4, 1);
}
static void w_LineTo(void) {
    wasm_dc_t *dc = find_dc(A32(0));
    if (dc) {
        uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
        wasm_gdiobj_t *pen = find_obj(dc->cur_pen);
        int nx = (int32_t)A32(1), ny = (int32_t)A32(2);
        if (pen && pen->color != 0xFFFFFFFFu) {
            draw_line_raw(px, w, h, dc, dc->pen_x, dc->pen_y + yoff, nx, ny + yoff,
                          0xFF000000u | (pen->color & 0xFFFFFFu), pen->width);
        }
        dc->pen_x = nx; dc->pen_y = ny;
    }
    RETS(3, 1);
}
static void w_ExtTextOutW(void) {
    wasm_dc_t *dc = find_dc(A32(0)); int x = (int32_t)A32(1), y = (int32_t)A32(2);
    uint32_t opts = A32(3), rc = A32(4), str = A32(5); int n = (int32_t)A32(6);
    if (dc) {
        uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
        if ((opts & 2u /* ETO_OPAQUE */) && rc) {
            draw_rect_solid(px, w, h, dc, (int32_t)MEM32(rc), (int32_t)MEM32(rc + 4) + yoff,
                            (int32_t)MEM32(rc + 8), (int32_t)MEM32(rc + 12) + yoff, 0xFF000000u | (dc->bk_color & 0xFFFFFFu));
        }
        if (str && n > 0) {
            char tmp[256]; int m = n < 255 ? n : 255;
            for (int i = 0; i < m; i++) tmp[i] = (char)(MEM16(str + (uint32_t)i * 2u) & 0x7Fu);
            tmp[m] = 0;
            draw_text_raw(px, w, h, dc, x, y + yoff, tmp, m, 0xFF000000u | (dc->text_color & 0xFFFFFFu), 1);
        }
    }
    RETS(8, 1);
}
static void w_TextOutA(void) {
    wasm_dc_t *dc = find_dc(A32(0)); const char *s = ASTR(3); int n = (int32_t)A32(4);
    if (dc && s && n > 0) {
        uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
        draw_text_raw(px, w, h, dc, (int32_t)A32(1), (int32_t)A32(2) + yoff, s, n,
                      0xFF000000u | (dc->text_color & 0xFFFFFFu), 1);
    }
    RETS(5, 1);
}
static void w_DrawTextA(void) {
    wasm_dc_t *dc = find_dc(A32(0)); const char *s = ASTR(1); int n = (int32_t)A32(2); uint32_t rc = A32(3);
    if (dc && s && rc) {
        if (n < 0) n = (int)strlen(s);
        uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
        draw_text_raw(px, w, h, dc, (int32_t)MEM32(rc), (int32_t)MEM32(rc + 4) + yoff, s, n,
                      0xFF000000u | (dc->text_color & 0xFFFFFFu), 1);
    }
    RETS(5, 12);
}
static void w_BitBlt(void) {
    wasm_dc_t *dst = find_dc(A32(0)), *src = find_dc(A32(5));
    int dx = (int32_t)A32(1), dy = (int32_t)A32(2), bw = (int32_t)A32(3), bh = (int32_t)A32(4);
    int sx = (int32_t)A32(6), sy = (int32_t)A32(7); uint32_t rop = A32(8);
    if (!dst) RETS(9, 0);
    uint32_t *dpx; int dw, dh, dyoff; dc_target(dst, &dpx, &dw, &dh, &dyoff);
    dy += dyoff;
    if (rop == 0x00FF0062u /* WHITENESS */) { draw_rect_solid(dpx, dw, dh, dst, dx, dy, dx + bw, dy + bh, 0xFFFFFFFFu); RETS(9, 1); }
    if (rop == 0x00000042u /* BLACKNESS */) { draw_rect_solid(dpx, dw, dh, dst, dx, dy, dx + bw, dy + bh, 0xFF000000u); RETS(9, 1); }
    if (src) {
        uint32_t *spx; int sw, sh, syoff; dc_target(src, &spx, &sw, &sh, &syoff);
        sy += syoff;
        for (int yy = 0; yy < bh; yy++) {
            for (int xx = 0; xx < bw; xx++) {
                int rx = sx + xx, ry = sy + yy;
                if (rx >= 0 && rx < sw && ry >= 0 && ry < sh) put_px(dpx, dw, dh, dst, dx + xx, dy + yy, spx[ry * sw + rx]);
            }
        }
    }
    RETS(9, 1);
}
static void w_PatBlt(void) {
    wasm_dc_t *dc = find_dc(A32(0));
    if (dc) {
        uint32_t *px; int w, h, yoff; dc_target(dc, &px, &w, &h, &yoff);
        wasm_gdiobj_t *br = find_obj(dc->cur_brush);
        uint32_t col = br ? br->color : 0x00FFFFFFu;
        if (col != 0xFFFFFFFFu) {
            draw_rect_solid(px, w, h, dc, (int32_t)A32(1), (int32_t)A32(2) + yoff,
                            (int32_t)A32(1) + (int32_t)A32(3), (int32_t)A32(2) + yoff + (int32_t)A32(4),
                            0xFF000000u | (col & 0xFFFFFFu));
        }
    }
    RETS(6, 1);
}

static void w_GetMessageA(void) {
    uint32_t msg_va = A32(0);
    for (;;) {
        if (g_msg_r != g_msg_w) {
            wasm_msg_t m = g_msgq[g_msg_r];
            g_msg_r = (g_msg_r + 1) & 127;
            if (msg_va) {
                MEM32(msg_va + 0) = m.hwnd; MEM32(msg_va + 4) = m.msg;
                MEM32(msg_va + 8) = m.wp; MEM32(msg_va + 12) = m.lp;
                MEM32(msg_va + 16) = (uint32_t)wasm_host_now_ms();
                MEM32(msg_va + 20) = 0; MEM32(msg_va + 24) = 0;
            }
            RETS(4, (m.msg == 0x0012u /* WM_QUIT */) ? 0u : 1u);
        }
        if (g_quit_posted) {
            if (msg_va) { MEM32(msg_va + 4) = 0x0012u; MEM32(msg_va + 8) = (uint32_t)g_quit_code; }
            RETS(4, 0);
        }
        wasm_ui_present();
        pump_host_events(16);
    }
}

static void w_PeekMessageA(void) {
    uint32_t msg_va = A32(0), rm = A32(4);
    if (g_msg_r == g_msg_w && !g_quit_posted) pump_host_events(0);
    if (g_msg_r != g_msg_w) {
        wasm_msg_t m = g_msgq[g_msg_r];
        if (rm & 1u) g_msg_r = (g_msg_r + 1) & 127;
        if (msg_va) {
            MEM32(msg_va + 0) = m.hwnd; MEM32(msg_va + 4) = m.msg;
            MEM32(msg_va + 8) = m.wp; MEM32(msg_va + 12) = m.lp;
        }
        RETS(5, 1);
    }
    RETS(5, 0);
}
static void w_TranslateMessage(void) { RETS(1, 1); }
static void w_DispatchMessageA(void) {
    uint32_t msg_va = A32(0);
    uint32_t r = 0;
    if (msg_va) {
        uint32_t hwnd = MEM32(msg_va + 0), msg = MEM32(msg_va + 4), wp = MEM32(msg_va + 8), lp = MEM32(msg_va + 12);
        if (msg == 0x000Fu /* WM_PAINT */) {
            wasm_wnd_t *w = find_wnd(hwnd);
            if (w) w->dirty = 0;
        }
        r = send_to_wnd(hwnd, msg, wp, lp);
        if (msg == 0x000Fu) wasm_ui_present();
    }
    RETS(1, r);
}
static void w_PostQuitMessage(void) { g_quit_code = (int)A32(0); g_quit_posted = 1; RETSV(1); }
static void w_SendMessageA(void) { RETS(4, send_to_wnd(A32(0), A32(1), A32(2), A32(3))); }
static void w_SendDlgItemMessageA(void) { RETS(5, 0); }
static void w_SetTimer(void) {
    uint32_t hwnd = A32(0), id = A32(1), elapse = A32(2);
    for (int i = 0; i < 16; i++) {
        if (!g_timers[i].active || (g_timers[i].hwnd == hwnd && g_timers[i].id == id)) {
            g_timers[i].active = 1; g_timers[i].hwnd = hwnd; g_timers[i].id = id;
            g_timers[i].interval_ms = elapse ? (double)elapse : 16.0;
            g_timers[i].next_ms = wasm_host_now_ms() + g_timers[i].interval_ms;
            RETS(4, id ? id : 1u);
        }
    }
    RETS(4, 0);
}
static void w_KillTimer(void) {
    uint32_t hwnd = A32(0), id = A32(1);
    for (int i = 0; i < 16; i++) if (g_timers[i].active && g_timers[i].hwnd == hwnd && g_timers[i].id == id) g_timers[i].active = 0;
    RETS(2, 1);
}
static void w_SetCapture(void) { uint32_t old = g_capture_hwnd; g_capture_hwnd = A32(0); RETS(1, old); }
static void w_ReleaseCapture(void) { g_capture_hwnd = 0; RETS(0, 1); }
static void w_GetKeyboardState(void) { if (A32(0)) memset(hle_ptr(A32(0)), 0, 256); RETS(1, 1); }
static void w_MessageBoxA(void) {
    hle_logf(2, "[MessageBoxA] %s: %s\n", ASTR(2) ? ASTR(2) : "", ASTR(1) ? ASTR(1) : "");
    RETS(4, 1);
}
static void w_SetRect(void) {
    uint32_t rc = A32(0);
    if (rc) { MEM32(rc) = A32(1); MEM32(rc + 4) = A32(2); MEM32(rc + 8) = A32(3); MEM32(rc + 12) = A32(4); }
    RETS(5, 1);
}
static void w_OffsetRect(void) {
    uint32_t rc = A32(0); int dx = (int32_t)A32(1), dy = (int32_t)A32(2);
    if (rc) {
        MEM32(rc) = (uint32_t)((int32_t)MEM32(rc) + dx); MEM32(rc + 8) = (uint32_t)((int32_t)MEM32(rc + 8) + dx);
        MEM32(rc + 4) = (uint32_t)((int32_t)MEM32(rc + 4) + dy); MEM32(rc + 12) = (uint32_t)((int32_t)MEM32(rc + 12) + dy);
    }
    RETS(3, 1);
}
static void w_IntersectRect(void) {
    uint32_t d = A32(0), s1 = A32(1), s2 = A32(2);
    if (!d || !s1 || !s2) RETS(3, 0);
    int l = (int32_t)MEM32(s1) > (int32_t)MEM32(s2) ? (int32_t)MEM32(s1) : (int32_t)MEM32(s2);
    int t = (int32_t)MEM32(s1 + 4) > (int32_t)MEM32(s2 + 4) ? (int32_t)MEM32(s1 + 4) : (int32_t)MEM32(s2 + 4);
    int r = (int32_t)MEM32(s1 + 8) < (int32_t)MEM32(s2 + 8) ? (int32_t)MEM32(s1 + 8) : (int32_t)MEM32(s2 + 8);
    int b = (int32_t)MEM32(s1 + 12) < (int32_t)MEM32(s2 + 12) ? (int32_t)MEM32(s1 + 12) : (int32_t)MEM32(s2 + 12);
    if (l >= r || t >= b) { memset(hle_ptr(d), 0, 16); RETS(3, 0); }
    MEM32(d) = (uint32_t)l; MEM32(d + 4) = (uint32_t)t; MEM32(d + 8) = (uint32_t)r; MEM32(d + 12) = (uint32_t)b;
    RETS(3, 1);
}
static void w_UnionRect(void) {
    uint32_t d = A32(0), s1 = A32(1), s2 = A32(2);
    if (!d || !s1 || !s2) RETS(3, 0);
    int l = (int32_t)MEM32(s1) < (int32_t)MEM32(s2) ? (int32_t)MEM32(s1) : (int32_t)MEM32(s2);
    int t = (int32_t)MEM32(s1 + 4) < (int32_t)MEM32(s2 + 4) ? (int32_t)MEM32(s1 + 4) : (int32_t)MEM32(s2 + 4);
    int r = (int32_t)MEM32(s1 + 8) > (int32_t)MEM32(s2 + 8) ? (int32_t)MEM32(s1 + 8) : (int32_t)MEM32(s2 + 8);
    int b = (int32_t)MEM32(s1 + 12) > (int32_t)MEM32(s2 + 12) ? (int32_t)MEM32(s1 + 12) : (int32_t)MEM32(s2 + 12);
    MEM32(d) = (uint32_t)l; MEM32(d + 4) = (uint32_t)t; MEM32(d + 8) = (uint32_t)r; MEM32(d + 12) = (uint32_t)b;
    RETS(3, 1);
}
static void w_GetSysColor(void) {
    int id = (int32_t)A32(0);
    if (id == 15) RETS(1, 0x00DCE0E4u); /* COLOR_BTNFACE */
    if (id == 5)  RETS(1, 0x00FFFFFFu); /* COLOR_WINDOW */
    RETS(1, 0x00000000u);
}

/* Simple 0..4 arg stdcall helpers */
static void w_ret1_0(void) { RETS(0, 1); }
static void w_ret0_1(void) { RETS(1, 0); }
static void w_ret1_1(void) { RETS(1, 1); }
static void w_ret0_2(void) { RETS(2, 0); }
static void w_ret1_2(void) { RETS(2, 1); }
static void w_ret0_3(void) { RETS(3, 0); }
static void w_ret1_3(void) { RETS(3, 1); }
static void w_ret0_4(void) { RETS(4, 0); }
static void w_ret1_4(void) { RETS(4, 1); }
static void w_ret0_5(void) { RETS(5, 0); }
static void w_ret1_5(void) { RETS(5, 1); }

static void w_LoadIconOrCursorA(void) { RETS(2, 0x1001u); }
static void w_LoadStringA(void) { if (A32(2) && A32(3)) MEM8(A32(2)) = 0; RETS(4, 0); }

static const struct { const char *name; recomp_func_t fn; } g_ui_syms[] = {
    { "RegisterClassA",        w_RegisterClassA },
    { "RegisterClassExA",      w_RegisterClassExA },
    { "CreateWindowExA",       w_CreateWindowExA },
    { "DestroyWindow",         w_DestroyWindow },
    { "ShowWindow",            w_ShowWindow },
    { "UpdateWindow",          w_UpdateWindow },
    { "EnableWindow",          w_ret1_2 },
    { "SetWindowTextA",        w_SetWindowTextA },
    { "SetWindowPos",          w_SetWindowPos },
    { "GetClientRect",         w_GetClientRect },
    { "GetWindowRect",         w_GetWindowRect },
    { "AdjustWindowRectEx",    w_AdjustWindowRectEx },
    { "GetWindowLongA",        w_GetWindowLongA },
    { "SetWindowLongA",        w_SetWindowLongA },
    { "DefWindowProcA",        w_DefWindowProcA },
    { "DefDlgProcA",           w_DefDlgProcA },
    { "SendMessageA",          w_SendMessageA },
    { "SendDlgItemMessageA",   w_SendDlgItemMessageA },
    { "GetDlgItemTextA",       w_ret0_4 },
    { "CheckDlgButton",        w_ret1_3 },
    { "IsDlgButtonChecked",    w_ret0_2 },
    { "DialogBoxParamA",       w_ret0_5 },
    { "EndDialog",             w_ret1_2 },
    { "IsDialogMessageA",      w_ret0_2 },
    { "CreateMenu",            w_CreateMenu },
    { "CreatePopupMenu",       w_CreateMenu },
    { "DestroyMenu",           w_DestroyMenu },
    { "AppendMenuA",           w_AppendMenuA },
    { "InsertMenuA",           w_InsertMenuA },
    { "ModifyMenuA",           w_ModifyMenuA },
    { "GetMenu",               w_GetMenu },
    { "SetMenu",               w_SetMenu },
    { "DrawMenuBar",           w_DrawMenuBar },
    { "CheckMenuItem",         w_CheckMenuItem },
    { "EnableMenuItem",        w_EnableMenuItem },
    { "GetMenuItemCount",      w_GetMenuItemCount },
    { "GetMenuItemRect",       w_GetMenuItemRect },
    { "GetMessageA",           w_GetMessageA },
    { "PeekMessageA",          w_PeekMessageA },
    { "TranslateMessage",      w_TranslateMessage },
    { "DispatchMessageA",      w_DispatchMessageA },
    { "PostQuitMessage",       w_PostQuitMessage },
    { "SetTimer",              w_SetTimer },
    { "KillTimer",             w_KillTimer },
    { "SetCapture",            w_SetCapture },
    { "ReleaseCapture",        w_ReleaseCapture },
    { "GetKeyboardState",      w_GetKeyboardState },
    { "LoadCursorA",           w_LoadIconOrCursorA },
    { "LoadIconA",             w_LoadIconOrCursorA },
    { "LoadAcceleratorsA",     w_LoadIconOrCursorA },
    { "LoadStringA",           w_LoadStringA },
    { "TranslateAcceleratorA", w_ret0_3 },
    { "ShowCursor",            w_ret1_1 },
    { "OpenClipboard",         w_ret1_1 },
    { "EmptyClipboard",        w_ret1_0 },
    { "SetClipboardData",      w_ret0_2 },
    { "CloseClipboard",        w_ret1_0 },
    { "MessageBoxA",           w_MessageBoxA },
    { "MessageBeep",           w_ret1_1 },
    { "SetForegroundWindow",   w_ret1_1 },
    { "SystemParametersInfoA", w_ret0_4 },
    { "GetSysColor",           w_GetSysColor },
    { "WinHelpA",              w_ret1_4 },
    { "WaitForInputIdle",      w_ret0_2 },
    { "SetRect",               w_SetRect },
    { "OffsetRect",            w_OffsetRect },
    { "IntersectRect",         w_IntersectRect },
    { "UnionRect",             w_UnionRect },
    { "InvalidateRect",        w_InvalidateRect },
    /* GDI32 */
    { "BeginPaint",            w_BeginPaint },
    { "EndPaint",              w_EndPaint },
    { "GetDC",                 w_GetDC },
    { "ReleaseDC",             w_ReleaseDC },
    { "CreateCompatibleDC",    w_CreateCompatibleDC },
    { "DeleteDC",              w_DeleteDC },
    { "CreateCompatibleBitmap",w_CreateCompatibleBitmap },
    { "SelectObject",          w_SelectObject },
    { "DeleteObject",          w_DeleteObject },
    { "GetStockObject",        w_GetStockObject },
    { "CreatePen",             w_CreatePen },
    { "CreateSolidBrush",      w_CreateSolidBrush },
    { "CreateHatchBrush",      w_CreateHatchBrush },
    { "CreateFontA",           w_CreateFontA },
    { "CreateFontIndirectA",   w_CreateFontIndirectA },
    { "GetDeviceCaps",         w_GetDeviceCaps },
    { "GetTextMetricsA",       w_GetTextMetricsA },
    { "GetTextExtentPoint32A", w_GetTextExtentPoint32A },
    { "GetTextExtentPoint32W", w_GetTextExtentPoint32W },
    { "SetBkMode",             w_SetBkMode },
    { "SetBkColor",            w_SetBkColor },
    { "SetTextColor",          w_SetTextColor },
    { "SetMapMode",            w_SetMapMode },
    { "SelectClipRgn",         w_SelectClipRgn },
    { "IntersectClipRect",     w_IntersectClipRect },
    { "SetPixel",              w_SetPixel },
    { "FillRect",              w_FillRect },
    { "Rectangle",             w_Rectangle },
    { "Ellipse",               w_Ellipse },
    { "Polygon",               w_Polygon },
    { "Polyline",              w_Polyline },
    { "MoveToEx",              w_MoveToEx },
    { "LineTo",                w_LineTo },
    { "ExtTextOutW",           w_ExtTextOutW },
    { "TextOutA",              w_TextOutA },
    { "DrawTextA",             w_DrawTextA },
    { "BitBlt",                w_BitBlt },
    { "PatBlt",                w_PatBlt },
    { "StartDocA",             w_ret0_2 },
    { "EndDoc",                w_ret1_1 },
    { "StartPage",             w_ret1_1 },
    { "EndPage",               w_ret1_1 },
    /* COMCTL32 / COMDLG32 */
    { "InitCommonControls",    w_ret1_0 },
    { "GetOpenFileNameA",      w_ret0_1 },
    { "GetSaveFileNameA",      w_ret0_1 },
    { "PrintDlgA",             w_ret0_1 },
    { NULL, NULL }
};

recomp_func_t wasm_user32_gdi_lookup(const char *dll, const char *name) {
    (void)dll;
    if (!name) return NULL;
    for (int i = 0; g_ui_syms[i].name; i++) {
        if (!strcmp(g_ui_syms[i].name, name)) return g_ui_syms[i].fn;
    }
    return NULL;
}
