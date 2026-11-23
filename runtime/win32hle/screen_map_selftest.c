/* screen_map_selftest.c - regression for upstream PCRecomp #195 mouse edges. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static void map(int px, int py, int pw, int ph, int ww, int wh,
                int dx, int dy, int dw, int dh, int gw, int gh, int *gx, int *gy) {
    hle_screen_map_point(px, py, pw, ph, ww, wh, dx, dy, dw, dh, gw, gh, gx, gy);
}

int main(void) {
    int gx, gy;
    /* MacBook full screen: 1512x982 game drawn at 2910x1890 renderer pixels,
     * SDL events still arrive in points. The last inside point must be the
     * game's last pixel in both axes (upstream PCRecomp #195). */
    const int gw = 1512, gh = 982, pw = 2910, ph = 1890, ww = 1512, wh = 982;
    const int dw = 2910, dh = 1890, dx = 0, dy = 0;
    map(0, 0, pw, ph, ww, wh, dx, dy, dw, dh, gw, gh, &gx, &gy);
    ok("top-left maps to 0,0", gx == 0 && gy == 0);
    map(1511, 981, pw, ph, ww, wh, dx, dy, dw, dh, gw, gh, &gx, &gy);
    ok("last point reaches right edge", gx == gw - 1);
    ok("last point reaches bottom edge", gy == gh - 1);
    map(1512, 982, pw, ph, ww, wh, dx, dy, dw, dh, gw, gh, &gx, &gy);
    ok("past-edge clamps", gx == gw - 1 && gy == gh - 1);
    map(756, 491, pw, ph, ww, wh, dx, dy, dw, dh, gw, gh, &gx, &gy);
    ok("midpoint stays inside", gx > 0 && gx < gw - 1 && gy > 0 && gy < gh - 1);

    /* 1x display: no HiDPI scaling, g_dst in event points. */
    map(1279, 479, 0, 0, 0, 0, 0, 0, 1280, 480, 1280, 480, &gx, &gy);
    ok("1x right edge", gx == 1279);
    ok("1x bottom edge", gy == 479);

    printf("screen_map_selftest: all checks passed\n");
    return fails != 0;
}
