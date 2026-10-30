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
 * Per thread, because a game has more than one. A single shared counter would
 * make each thread's readings depend on how often the OTHER thread read the
 * clock, which is the same nondeterminism in a new place; so each guest thread
 * advances its own counter, keyed by the guest thread id (hle_thread_id() - the
 * same id the trace is tagged with, so the clock and the trace agree about what
 * a thread is). The counters all start at the epoch, so a thread's own sequence
 * is reproducible even though the threads interleave differently. The trade-off
 * is real and deliberate: two threads do not share one timeline, so a game that
 * compares a time taken on one thread with a time taken on another sees the two
 * counters, not one clock. That is acceptable for a replay facility that is off
 * by default, and it is the price of each thread being comparable on its own.
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
#include <pthread.h>
#include "win32hle.h"

#define CLOCK_THREADS 64

static int g_virtual = -1;          /* -1 until the environment has been read */
static uint32_t g_step = 1;
static uint32_t g_epoch;
static uint64_t g_start_us;         /* the real clock's origin, for QPC */
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;

/* One counter per guest thread. A thread's slot is made on its first read; more
 * threads than slots fall back to slot 0, which is a degradation (they would
 * share a counter) but not a failure. */
static struct { uint32_t tid; uint32_t now; int used; } g_th[CLOCK_THREADS];

static int slot_for(uint32_t tid) {          /* called with g_lock held */
    int free_slot = -1;
    for (int i = 0; i < CLOCK_THREADS; i++) {
        if (g_th[i].used && g_th[i].tid == tid) return i;
        if (!g_th[i].used && free_slot < 0) free_slot = i;
    }
    if (free_slot < 0) return 0;
    g_th[free_slot].used = 1;
    g_th[free_slot].tid = tid;
    g_th[free_slot].now = g_epoch;
    return free_slot;
}

static void clock_init(void) {
    if (g_virtual >= 0) return;
    const char *v = getenv("RECOMP_CLOCK");
    g_virtual = (v && strncmp(v, "virtual", 7) == 0);
    if (g_virtual) {
        const char *colon = strchr(v, ':');
        if (colon && atoi(colon + 1) > 0) g_step = (uint32_t)atoi(colon + 1);
        const char *epoch = getenv("RECOMP_CLOCK_EPOCH");
        g_epoch = epoch ? (uint32_t)strtoul(epoch, NULL, 0) : 0;
    }
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    g_start_us = (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}

int hle_clock_virtual(void) { clock_init(); return g_virtual; }

/* The time the shims report, in milliseconds. In virtual mode a read advances
 * the calling thread's clock, so a poll loop makes progress deterministically. */
uint32_t hle_clock_ms(void) {
    clock_init();
    if (!g_virtual) {
        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return (uint32_t)((uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u);
    }
    uint32_t tid = hle_thread_id();
    pthread_mutex_lock(&g_lock);
    int i = slot_for(tid);
    uint32_t t = g_th[i].now;
    g_th[i].now += g_step;
    pthread_mutex_unlock(&g_lock);
    return t;
}

/* How long the host should really sleep for a guest Sleep(ms): ms as before, or
 * nothing at all in virtual mode, where the calling thread's time has already
 * moved by the sleep. A sleep that did not move it would leave a guest waiting
 * for time to pass spinning forever. */
uint32_t hle_clock_sleep(uint32_t ms) {
    clock_init();
    if (!g_virtual) return ms;
    uint32_t tid = hle_thread_id();
    pthread_mutex_lock(&g_lock);
    int i = slot_for(tid);
    g_th[i].now += ms > g_step ? ms : g_step;
    pthread_mutex_unlock(&g_lock);
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

/* Re-read the environment and start virtual time over, for EVERY thread: a
 * probe that resets between runs would otherwise find one thread at zero and
 * the rest where they were. */
void hle_clock_reset(void) {
    pthread_mutex_lock(&g_lock);
    memset(g_th, 0, sizeof g_th);
    pthread_mutex_unlock(&g_lock);
    g_virtual = -1;
    g_step = 1;
    g_epoch = 0;
}
