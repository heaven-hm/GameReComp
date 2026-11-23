/* winmm_playsound_selftest.c - sndPlaySoundA/W through a real guest call.
 *
 * The WAV used for the playback checks can be supplied at run time (argv[1]
 * or RECOMP_PLAYSOUND_WAV); otherwise a small valid PCM WAVE is generated in
 * the working directory. A deliberately missing file and a non-PCM WAVE are
 * used to check that failures are reported, not silently reported as success.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

/* Win32 sndPlaySound returns BOOL: exactly 1 on success and 0 on failure.
 * The detailed MMSYSERR_* reason is kept host-side in the diagnostic log, not
 * in the guest-visible return value. */
enum { PS_TRUE = 1u, PS_FALSE = 0u };
/* Flags used below. */
enum {
    PS_SYNC = 0x0000u, PS_ASYNC = 0x0001u, PS_MEMORY = 0x0004u,
    PS_PURGE = 0x0040u, PS_FILENAME = 0x00020000u, PS_ALIAS = 0x00010000u
};

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
    else printf("ok   %s\n", what);
}
static uint32_t call(const char *name, uint32_t a0, uint32_t a1) {
    uint32_t args[2] = { a0, a1 };
    uint32_t fn = hle_resolve(name);
    if (!fn) { printf("FAIL %s did not resolve\n", name); fails++; return 0xFFFFFFFFu; }
    return hle_call_guest(fn, 2, args);
}
static uint32_t guest_str(const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 1u;
    uint32_t va = hle_va(hle_alloc(n));
    if (va) memcpy(hle_ptr(va), s, n);
    return va;
}
static uint32_t guest_wide(const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 1u;
    uint32_t va = hle_va(hle_alloc(n * 2u));
    if (va) {
        uint16_t *w = (uint16_t *)hle_ptr(va);
        for (uint32_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)s[i];
    }
    return va;
}
static void put16(uint8_t *p, unsigned v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *p, unsigned v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}
/* A 0.1 s, 8 kHz, 16-bit mono PCM WAVE; a non-PCM variant for the error test. */
enum { WAV_BYTES = 44 + 1600 };
static void make_wav(uint8_t *b, int non_pcm) {
    memset(b, 0, WAV_BYTES);
    memcpy(b, "RIFF", 4); put32(b + 4, WAV_BYTES - 8); memcpy(b + 8, "WAVE", 4);
    memcpy(b + 12, "fmt ", 4); put32(b + 16, 16);
    put16(b + 20, non_pcm ? 3u : 1u);                 /* PCM vs IEEE float */
    put16(b + 22, 1); put32(b + 24, 8000); put32(b + 28, 16000);
    put16(b + 32, 2); put16(b + 34, 16);
    memcpy(b + 36, "data", 4); put32(b + 40, 1600);
    for (int i = 0; i < 800; i++) put16(b + 44 + i * 2, (unsigned)((i * 37) % 2000) - 1000u);
}

int main(int argc, char **argv) {
    win32hle_register(win32hle_winmm);
    mach_enter();
    ok("sndPlaySoundA resolves", hle_resolve("sndPlaySoundA") != 0);
    ok("sndPlaySoundW resolves", hle_resolve("sndPlaySoundW") != 0);

    char host[1024], float_host[1200];
    const char *supplied = argc > 1 ? argv[1] : getenv("RECOMP_PLAYSOUND_WAV");
    int generated = 0;
    if (supplied && *supplied) {
        snprintf(host, sizeof host, "%s", supplied);
    } else {
        if (!getcwd(host, sizeof host)) { printf("FAIL getcwd\n"); return 1; }
        size_t n = strlen(host);
        snprintf(host + n, sizeof host - n, "/winmm_playsound_selftest.wav");
        uint8_t wav[WAV_BYTES];
        make_wav(wav, 0);
        FILE *f = fopen(host, "wb");
        ok("temp WAV written", f && fwrite(wav, 1, sizeof wav, f) == sizeof wav);
        if (f) fclose(f);
        generated = 1;
    }
    FILE *probe = fopen(host, "rb");
    ok("runtime WAV is readable", probe != NULL);
    if (probe) fclose(probe);

    /* The non-PCM fixture is always written locally, beside neither the
     * supplied path nor a path the caller may not be able to write to. */
    if (!getcwd(float_host, sizeof float_host)) { printf("FAIL getcwd (float)\n"); return 1; }
    size_t fn = strlen(float_host);
    snprintf(float_host + fn, sizeof float_host - fn, "/winmm_playsound_selftest_float.wav");

    hle_set_drive('Z', "/");
    char guest[1200], guest_bad[1200], guest_float[1200];
    snprintf(guest, sizeof guest, "Z:%s", host);
    snprintf(guest_bad, sizeof guest_bad, "Z:%s.does-not-exist.wav", host);
    snprintf(guest_float, sizeof guest_float, "Z:%s", float_host);
    uint32_t p_wav = guest_str(guest), p_bad = guest_str(guest_bad);
    uint32_t w_wav = guest_wide(guest);
    ok("guest strings allocated", p_wav && p_bad && w_wav);

    /* A missing file is a FALSE return, with the guest path and the MMSYSERR_*
     * reason in the host diagnostic. */
    uint32_t r = call("sndPlaySoundA", p_bad, PS_FILENAME | PS_SYNC);
    ok("missing file -> FALSE(0)", r == PS_FALSE);

    /* Unsupported flags are rejected, not quietly downgraded. */
    r = call("sndPlaySoundA", p_wav, PS_FILENAME | PS_ALIAS);
    ok("unsupported flags -> FALSE(0)", r == PS_FALSE);

    /* Real playback through SDL audio (the build runs with SDL_AUDIODRIVER=dummy). */
    r = call("sndPlaySoundA", p_wav, PS_FILENAME | PS_SYNC);
    ok("synchronous WAV path -> TRUE(1)", r == PS_TRUE);
    r = call("sndPlaySoundW", w_wav, PS_FILENAME | PS_SYNC);
    ok("wide WAV path -> TRUE(1)", r == PS_TRUE);

    /* SND_MEMORY: a WAVE image in guest memory, not a host path. */
    uint32_t mem = hle_va(hle_alloc(WAV_BYTES));
    if (mem) {
        uint8_t wav[WAV_BYTES];
        make_wav(wav, 0);
        memcpy(hle_ptr(mem), wav, sizeof wav);
        r = call("sndPlaySoundA", mem, PS_MEMORY | PS_SYNC);
        ok("SND_MEMORY WAV -> TRUE(1)", r == PS_TRUE);
    } else {
        printf("FAIL SND_MEMORY guest buffer\n");
        fails++;
    }

    /* A non-PCM WAVE is unsupported, not played as silence. */
    {
        uint8_t wav[WAV_BYTES];
        FILE *f = fopen(float_host, "wb");
        make_wav(wav, 1);
        ok("float WAV written", f && fwrite(wav, 1, sizeof wav, f) == sizeof wav);
        if (f) fclose(f);
        r = call("sndPlaySoundA", guest_str(guest_float), PS_FILENAME | PS_SYNC);
        ok("non-PCM WAV -> FALSE(0)", r == PS_FALSE);
        unlink(float_host);
    }

    /* Stop/purge paths, including an asynchronous voice. */
    r = call("sndPlaySoundA", p_wav, PS_FILENAME | PS_ASYNC);
    ok("async WAV path -> TRUE(1)", r == PS_TRUE);
    r = call("sndPlaySoundA", 0, PS_PURGE);
    ok("NULL name purge -> TRUE(1)", r == PS_TRUE);

    /* Explicit guest-ABI contract: Win32 sndPlaySound returns BOOL, so a
     * successful play is exactly 1 and a failed play is exactly 0. An
     * MMSYSERR-style 0-on-success regression is caught here for both forms. */
    r = call("sndPlaySoundA", p_wav, PS_FILENAME | PS_SYNC);
    ok("BOOL ABI: sndPlaySoundA success == 1", r == 1u);
    r = call("sndPlaySoundA", p_bad, PS_FILENAME | PS_SYNC);
    ok("BOOL ABI: sndPlaySoundA failure == 0", r == 0u);
    r = call("sndPlaySoundW", w_wav, PS_FILENAME | PS_SYNC);
    ok("BOOL ABI: sndPlaySoundW success == 1", r == 1u);
    r = call("sndPlaySoundW", guest_wide(guest_bad), PS_FILENAME | PS_SYNC);
    ok("BOOL ABI: sndPlaySoundW failure == 0", r == 0u);

    if (generated) unlink(host);
    mach_leave();
    printf("winmm_playsound_selftest: %s\n", fails ? "FAILURES" : "all checks passed");
    return fails != 0;
}
