/*
 * screenshot.c - write the frames a game presents to PNG files.
 *
 * The evidence that a rebuilt game is running is not that its process stayed
 * alive: it is what it drew. screen.c already calls a hook with the primary
 * surface's pixels for every frame it shows, so this installs one and writes
 * the frames out.
 *
 * Enabled by the environment, off by default:
 *
 *   RECOMP_SCREENSHOT=DIR        write frames into DIR
 *   RECOMP_SCREENSHOT_EVERY=MS   minimum gap between saved frames (default 250)
 *   RECOMP_SCREENSHOT_MAX=N      stop after N frames (default 40)
 *
 * Frames are written as PNG through SDL_image-free code: a minimal,
 * self-contained PNG encoder is not worth carrying, so these are BMP files,
 * which every viewer and every image library reads. The name says what they
 * are.
 *
 * Nothing here runs unless the variable is set, so a normal run is unaffected.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <pthread.h>
#include <time.h>
#include "win32hle.h"

static char     g_dir[512];
static int      g_every_ms = 250;
static int      g_max = 40;
static int      g_written;
static uint32_t g_last_ms;
static int      g_installed;

/* A 24-bit BMP: bottom-up rows, padded to 4 bytes, which is what every reader
 * expects and what needs no library. */
static int write_bmp(const char *path, const uint8_t *px, int w, int h,
                     int pitch, int bpp) {
    if (w <= 0 || h <= 0 || (bpp != 8 && bpp != 16 && bpp != 24 && bpp != 32)) return -1;
    int row = (w * 3 + 3) & ~3;
    uint32_t pixels = (uint32_t)row * (uint32_t)h;
    uint32_t offset = 14 + 40;
    uint32_t size = offset + pixels;

    FILE *f = fopen(path, "wb");
    if (!f) return -1;
    uint8_t hdr[54];
    memset(hdr, 0, sizeof hdr);
    hdr[0] = 'B', hdr[1] = 'M';
    memcpy(hdr + 2, &size, 4);
    memcpy(hdr + 10, &offset, 4);
    uint32_t dib = 40;
    memcpy(hdr + 14, &dib, 4);
    memcpy(hdr + 18, &w, 4);
    memcpy(hdr + 22, &h, 4);
    uint16_t planes = 1, bits = 24;
    memcpy(hdr + 26, &planes, 2);
    memcpy(hdr + 28, &bits, 2);
    memcpy(hdr + 34, &pixels, 4);
    fwrite(hdr, 1, sizeof hdr, f);

    uint8_t *line = calloc(1, (size_t)row);
    if (!line) { fclose(f); return -1; }
    for (int y = h - 1; y >= 0; y--) {                   /* BMP rows run bottom-up */
        const uint8_t *src = px + (size_t)y * (size_t)pitch;
        for (int x = 0; x < w; x++) {
            uint8_t r, g, b;
            if (bpp == 32) {                             /* BGRA in memory */
                b = src[x * 4 + 0], g = src[x * 4 + 1], r = src[x * 4 + 2];
            } else if (bpp == 24) {
                b = src[x * 3 + 0], g = src[x * 3 + 1], r = src[x * 3 + 2];
            } else if (bpp == 16) {                      /* RGB565 */
                uint16_t v = (uint16_t)(src[x * 2] | (src[x * 2 + 1] << 8));
                r = (uint8_t)(((v >> 11) & 0x1F) * 255 / 31);
                g = (uint8_t)(((v >> 5) & 0x3F) * 255 / 63);
                b = (uint8_t)((v & 0x1F) * 255 / 31);
            } else {                                     /* 8-bit: the palette is not passed here */
                r = g = b = src[x];
            }
            line[x * 3 + 0] = b, line[x * 3 + 1] = g, line[x * 3 + 2] = r;
        }
        fwrite(line, 1, (size_t)row, f);
    }
    free(line);
    fclose(f);
    return 0;
}

static void on_frame(const uint8_t *px, int w, int h, int pitch, int bpp) {
    if (!g_dir[0] || !px || g_written >= g_max) return;
    uint32_t now = hle_ticks_ms();
    if (g_written && now - g_last_ms < (uint32_t)g_every_ms) return;
    g_last_ms = now;
    char path[600];
    snprintf(path, sizeof path, "%s/frame-%04d.bmp", g_dir, g_written);
    if (write_bmp(path, px, w, h, pitch, bpp) == 0) {
        fprintf(stderr, "[screenshot] %s (%dx%d %d bpp)\n", path, w, h, bpp);
        g_written++;
    }
}

/* The capture thread.
 *
 * screen.c's frame hook is the natural place for this, but it is only wired up
 * once a real window and renderer exist -- and the runs worth capturing are the
 * headless ones. So the primary surface is polled directly instead: ddraw.c
 * publishes it through hle_dd_frame() whether or not anything is displaying it,
 * and a screenshot of a headless run is exactly the evidence needed.
 *
 * The read races the guest's writes to the surface. For a screenshot that is
 * harmless -- a torn frame is still a frame -- and it keeps this out of the
 * machine lock, which must never be held while writing a file. */
static void *capture_thread(void *unused) {
    (void)unused;
    while (g_written < g_max) {
        const uint8_t *px = NULL;
        const uint32_t *pal = NULL;
        int w = 0, h = 0, pitch = 0, bpp = 0;
        unsigned seq = 0;
        if (hle_dd_frame(&px, &w, &h, &pitch, &bpp, &pal, &seq) && px)
            on_frame(px, w, h, pitch, bpp);
        struct timespec ts = { 0, 20 * 1000 * 1000 };   /* 20 ms */
        nanosleep(&ts, NULL);
    }
    return NULL;
}

__attribute__((constructor)) static void install(void) {
    const char *dir = getenv("RECOMP_SCREENSHOT");
    if (!dir || !*dir) return;
    snprintf(g_dir, sizeof g_dir, "%s", dir);
    mkdir(g_dir, 0755);
    const char *every = getenv("RECOMP_SCREENSHOT_EVERY");
    if (every && atoi(every) > 0) g_every_ms = atoi(every);
    const char *max = getenv("RECOMP_SCREENSHOT_MAX");
    if (max && atoi(max) > 0) g_max = atoi(max);
    /* Also take screen.c's hook when it is available, so a windowed run is
     * captured at the moment it presents rather than on the poll. */
    hle_screen_frame_hook = on_frame;
    g_installed = 1;
    pthread_t tid;
    if (pthread_create(&tid, NULL, capture_thread, NULL) == 0)
        pthread_detach(tid);
    fprintf(stderr, "[screenshot] writing up to %d frame(s) to %s, at most one "
                    "every %d ms\n", g_max, g_dir, g_every_ms);
}

/* Reported so a run's evidence can say whether capture was on. */
int hle_screenshot_active(void) { return g_installed; }
int hle_screenshot_count(void) { return g_written; }
