/*
 * gl_capture_selftest.c - verify the OpenGL present frame conversion.
 *
 * glReadPixels hands back bottom-up RGBA; the screenshot hook consumes top-down
 * 24-bpp RGB. This selftest pins that conversion exactly: alpha is dropped,
 * rows are reversed, and no pixel is inferred from a file extension or a
 * claimed hash. It needs no GPU context.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

int main(void) {
    /* 2x2 bottom-up RGBA rows: row0=(red,green), row1=(blue,white). */
    const uint8_t rgba[16] = {
        255,0,0,255,   0,255,0,128,
        0,0,255,0,     255,255,255,255,
    };
    uint8_t rgb[12];
    memset(rgb, 0xAA, sizeof rgb);
    hle_sdl12_gl_rgba_to_rgb(rgba, 2, 2, rgb);
    ok("top row is the last GL row", rgb[0] == 0 && rgb[1] == 0 && rgb[2] == 255 &&
       rgb[3] == 255 && rgb[4] == 255 && rgb[5] == 255);
    ok("bottom row is the first GL row", rgb[6] == 255 && rgb[7] == 0 && rgb[8] == 0 &&
       rgb[9] == 0 && rgb[10] == 255 && rgb[11] == 0);
    ok("alpha dropped", rgb[5] == 255 && rgb[8] == 0);

    hle_sdl12_gl_rgba_to_rgb(rgba, 0, 2, rgb);
    hle_sdl12_gl_rgba_to_rgb(NULL, 2, 2, rgb);
    ok("bad geometry is a no-op", rgb[0] == 0);

    if (fails) return 1;
    printf("gl_capture_selftest: all checks passed\n");
    return 0;
}
