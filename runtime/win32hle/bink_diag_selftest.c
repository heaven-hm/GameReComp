/*
 * bink_diag_selftest.c - honest diagnostics for Bink files, with no movie
 * fixture and no FFmpeg. The selftest build compiles bink.c's stub path, so
 * these checks exercise (a) the shared header probe on synthetic buffers and
 * (b) the stub BinkOpen/BinkGetError path on synthetic files. Nothing here
 * claims that a frame was decoded: BinkOpen must keep failing, with a specific
 * reason, on this host.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "win32hle.h"

extern const win32hle_shim win32hle_bink[];
static void no_guest(void) { }                          /* no lifted code: the shims are host C */
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0x1000u, no_guest } };
const uint32_t recomp_dispatch_count = 1;
/* bink.c deliberately does not put these in the shared header: they are local
 * to the runtime. A pointer is a pointer at the ABI, so `void *` is enough. */
extern int hle_bink_probe(const uint8_t *hdr, uint32_t avail, uint64_t file_size, void *info);
extern const char *hle_bink_probe_error(int status);

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int n, const uint32_t *a) {
    uint32_t va = hle_resolve(name);
    if (!va) hle_fatal("selftest: no shim named %s", name);
    return hle_call_guest(va, n, a);
}

static void put32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

/* A plausible 36-byte Bink 1 header. `declared` is the file size the header
 * claims; callers can corrupt one field at a time. */
static void header(uint8_t *b, const char *sig, uint32_t declared, uint32_t frames,
                   uint32_t largest, uint32_t w, uint32_t h, uint32_t fn, uint32_t fd) {
    memset(b, 0, 36);
    memcpy(b, sig, 4);
    put32(b + 4, declared - 8);
    put32(b + 8, frames);
    put32(b + 12, largest);
    put32(b + 20, w);
    put32(b + 24, h);
    put32(b + 28, fn);
    put32(b + 32, fd);
}

/* Probe a buffer and require the diagnostic to name the right fault. */
static void probe_case(const char *what, const uint8_t *buf, uint32_t n, uint64_t file_size,
                       int want_ok, const char *keyword) {
    int st = hle_bink_probe(buf, n, file_size, NULL);
    const char *msg = hle_bink_probe_error(st);
    int good = want_ok ? st == 0 : st != 0 && keyword && strstr(msg, keyword) != NULL;
    if (!good) printf("FAIL %s: status %d, message \"%s\"\n", what, st, msg);
    if (!good) fails++;
}

static void probe_tests(void) {
    uint8_t b[512 + 36 + 8];
    header(b, "BIKb", 640, 25, 512, 320, 240, 15, 1);
    probe_case("a plausible BIK header is accepted", b, 36, 640, 1, NULL);

    header(b, "BIKb", 640, 25, 512, 320, 240, 15, 1);
    probe_case("a 20-byte header is reported truncated", b, 20, 640, 0, "fewer than 36");

    header(b, "XXXX", 640, 25, 512, 320, 240, 15, 1);
    probe_case("a non-Bink buffer is rejected", b, 36, 640, 0, "not a Bink movie");

    header(b, "KB2a", 640, 25, 512, 320, 240, 15, 1);
    probe_case("Bink 2 is reported unsupported, not accepted", b, 36, 640, 0, "Bink 2");

    header(b, "BIKb", 16, 25, 512, 320, 240, 15, 1);
    probe_case("a declared size smaller than the header is rejected", b, 36, 16, 0, "smaller than the header");

    header(b, "BIKb", 1000, 25, 512, 320, 240, 15, 1);
    probe_case("a short file is reported truncated", b, 36, 500, 0, "shorter than the size");

    header(b, "BIKb", 640, 0, 512, 320, 240, 15, 1);
    probe_case("a zero frame count is rejected", b, 36, 640, 0, "frame count");

    header(b, "BIKb", 640, 1000001, 512, 320, 240, 15, 1);
    probe_case("an impossible frame count is rejected", b, 36, 640, 0, "frame count");

    header(b, "BIKb", 640, 25, 99999, 320, 240, 15, 1);
    probe_case("an oversized largest-frame value is rejected", b, 36, 640, 0, "largest frame");

    header(b, "BIKb", 640, 25, 512, 0, 240, 15, 1);
    probe_case("a zero width is rejected", b, 36, 640, 0, "dimensions");

    header(b, "BIKb", 640, 25, 512, 320, 240, 15, 0);
    probe_case("a zero fps denominator is rejected", b, 36, 640, 0, "frame rate");

    /* A Smush-wrapped movie must not be mislabelled "not a Bink": FFmpeg looks
     * for the BIK signature in 512-byte blocks, and so does the probe. */
    memset(b, 0, sizeof b);
    memcpy(b, "SMUS", 4);
    header(b + 512, "BIKb", 512 + 36, 25, 512, 320, 240, 15, 1);
    probe_case("a BIK inside a Smush block is found", b, 512 + 36, 512 + 36, 1, NULL);
}

/* ---- whole-shim path: synthetic files, no decoder, explicit errors ---- */
static uint32_t write_movie(const char *dir, const char *file, const uint8_t *bytes, uint32_t n) {
    char host[1024];
    snprintf(host, sizeof host, "%s/%s", dir, file);
    FILE *fp = fopen(host, "wb");
    if (!fp) hle_fatal("selftest: cannot create %s", host);
    fwrite(bytes, 1, n, fp);
    fclose(fp);
    char guest[1100];
    snprintf(guest, sizeof guest, "T:\\%s", file);
    return hle_guest_strdup(guest);
}

static const char *last_error(void) {
    uint32_t va = call("_BinkGetError@0", 0, NULL);
    return va ? (const char *)hle_ptr(va) : "";
}

static void open_case(const char *what, uint32_t path, const char *keyword) {
    uint32_t a[2] = { path, 0 };
    uint32_t h = call("_BinkOpen@8", 2, a);
    const char *e = last_error();
    if (h != 0 || !strstr(e, keyword))
        printf("FAIL %s: handle 0x%x, BinkGetError \"%s\" (wanted \"%s\")\n", what, h, e, keyword), fails++;
}

static void shim_tests(void) {
    win32hle_register(win32hle_bink);
    int n = 0;
    for (const win32hle_shim *s = win32hle_bink; s->name; s++) {
        n++;
        if (!hle_resolve(s->name)) { printf("FAIL unresolved %s\n", s->name); fails++; }
    }
    ok("both-style export table is complete and resolvable", n >= 22);
    ok("the new BinkSetSoundOnOff export resolves", hle_resolve("_BinkSetSoundOnOff@8") != 0);

    char tmpl[] = "/tmp/bink-diag-XXXXXX";
    char *dir = mkdtemp(tmpl);
    if (!dir) hle_fatal("selftest: mkdtemp failed");
    hle_set_drive('T', dir);

    uint8_t b[64];
    header(b, "BIKb", sizeof b, 25, 32, 320, 240, 15, 1);
    open_case("a valid header still fails honestly with no decoder",
              write_movie(dir, "valid.bik", b, sizeof b), "no movie decoder on this host");

    header(b, "BIKb", sizeof b, 25, 32, 320, 240, 15, 1);
    open_case("a truncated BinkOpen file names the fault",
              write_movie(dir, "short.bik", b, 20), "truncated Bink header");

    header(b, "NOTBIK", sizeof b, 25, 32, 320, 240, 15, 1);
    open_case("a non-Bink BinkOpen file names the fault",
              write_movie(dir, "wrong.bik", b, sizeof b), "not a Bink movie");

    header(b, "KB2a", sizeof b, 25, 32, 320, 240, 15, 1);
    open_case("a Bink 2 BinkOpen file names the fault",
              write_movie(dir, "kb2.bik", b, sizeof b), "unsupported Bink 2");

    header(b, "BIKb", 4096, 25, 4096, 320, 240, 15, 1);
    open_case("a short movie with a large declared size names the fault",
              write_movie(dir, "body.bik", b, sizeof b), "shorter than the size");

    open_case("a missing movie names the fault", hle_guest_strdup("T:\\absent.bik"), "file not found");

    /* No fake readiness or sound: with no movie, Wait says "wait" and the new
     * API reports the previous (off) state rather than a made-up success. */
    uint32_t h[2] = { 0x1234u, 1 };
    ok("stub BinkWait never says a frame is ready", call("_BinkWait@4", 1, h) == 1);
    ok("stub BinkSetSoundOnOff reports sound off", call("_BinkSetSoundOnOff@8", 2, h) == 0);

    const char *f[] = { "valid.bik", "short.bik", "wrong.bik", "kb2.bik", "body.bik" };
    for (int i = 0; i < 5; i++) {
        char host[1024];
        snprintf(host, sizeof host, "%s/%s", dir, f[i]);
        unlink(host);
    }
    rmdir(dir);
}

int main(void) {
    mach_enter();
    probe_tests();
    shim_tests();
    mach_leave();
    if (!fails) printf("bink_diag_selftest: all checks passed\n");
    return fails != 0;
}
