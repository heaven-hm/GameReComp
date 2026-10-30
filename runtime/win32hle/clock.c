/*
 * clock.c - the clock the shims report, and a deterministic mode for replay.
 *
 * Why this exists. Comparing two runs of a rebuilt game is how a divergence is
 * found, and it only works if the two runs are comparable. They are not, by
 * default: measured on Doukutsu, two runs of the same binary diverge at call
 * 117,955 on a timeGetTime poll, resync locally, and then differ for good ~700
 * calls later because the clock difference changed which branch the game took.
 * Normalising the clock calls out of the trace does not fix it, so the clock
 * itself has to be controlled before a divergence can be bisected.
 *
 * Virtual time. With RECOMP_CLOCK=virtual the shims report a time that starts at
 * a fixed epoch and advances deterministically: STEP milliseconds on every read
 * (default 1) and at least STEP on every sleep. A spin-wait that polls the clock
 * therefore terminates in a fixed number of iterations instead of depending on
 * how fast the host happens to be, which is what makes two runs of the same
 * input comparable. It is not wall-clock accurate, and it is not meant to be:
 * it is the clock a replay needs.
 *
 *   RECOMP_CLOCK=virtual        virtual time, 1 ms per read
 *   RECOMP_CLOCK=virtual:16     virtual time, 16 ms per read (frame-ish)
 *   RECOMP_CLOCK_EPOCH=MS       start at MS (default 0)
 *   unset                       the real monotonic clock, exactly as before
 *
 * Off by default, so an ordinary run - and every recorded baseline - is
 * unaffected.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int g_virtual = -1;          /* -1 until the environment has been read */
static uint32_t g_step = 1;
static uint32_t g_now;
static uint64_t g_start_us;         /* the real clock's origin, for QPC */

static void clock_init(void) {
    if (g_virtual >= 0) return;
    const char *v = getenv("RECOMP_CLOCK");
    g_virtual = (v && strncmp(v, "virtual", 7) == 0);
    if (g_virtual) {
        const char *colon = strchr(v, ':');
        if (colon && atoi(colon + 1) > 0) g_step = (uint32_t)atoi(colon + 1);
        const char *epoch = getenv("RECOMP_CLOCK_EPOCH");
        g_now = epoch ? (uint32_t)strtoul(epoch, NULL, 0) : 0;
    }
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    g_start_us = (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}

int hle_clock_virtual(void) { clock_init(); return g_virtual; }

/* The time the shims report, in milliseconds. In virtual mode a read advances
 * the clock, so a poll loop makes progress deterministically. */
uint32_t hle_clock_ms(void) {
    clock_init();
    if (!g_virtual) {
        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return (uint32_t)((uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u);
    }
    uint32_t t = g_now;
    g_now += g_step;
    return t;
}

/* How long the host should really sleep for a guest Sleep(ms): ms as before, or
 * nothing at all in virtual mode, where the time has already passed. */
uint32_t hle_clock_sleep(uint32_t ms) {
    clock_init();
    if (!g_virtual) return ms;
    g_now += ms > g_step ? ms : g_step;
    return 0;
}

/* A monotonic microsecond counter for QueryPerformanceCounter. */
uint64_t hle_clock_us(void) {
    clock_init();
    if (!g_virtual) {
        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return ((uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u) - g_start_us;
    }
    return (uint64_t)hle_clock_ms() * 1000u;
}

/* Re-read the environment and start virtual time over. For probes and tests,
 * which need a known clock without a fresh process. */
void hle_clock_reset(void) { g_virtual = -1; g_step = 1; g_now = 0; }
