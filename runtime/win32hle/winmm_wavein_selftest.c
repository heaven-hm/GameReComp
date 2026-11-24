/* winmm_wavein_selftest.c - the no-device waveIn (record) subset, driven as a
 * lifted guest drives it: every call goes through its registered stdcall shim.
 *
 * Focused build (small, for a shared disk): only winmm.c and the four spine
 * files it needs are linked; WINMM_WAVEIN_SELFTEST_MINIMAL supplies the few
 * symbols that live in dsound.c/kernel32.c in the full build. The same file
 * also links cleanly with the whole layer under build_selftests.sh (no macro):
 * the stubs are compiled only in the focused build.
 *
 *   cd runtime/win32hle
 *   TMPDIR=/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode \
 *   cc -O1 -Wall -Wno-unused-function -Wno-unused-but-set-variable \
 *      -DWINMM_WAVEIN_SELFTEST_MINIMAL -I../recomp32 -I. \
 *      winmm.c host_lite.c guest_mem.c clock.c hle_path.c \
 *      winmm_wavein_selftest.c -lpthread -lm -o $TMPDIR/winmm_wavein_selftest
 *   SDL_AUDIODRIVER=dummy $TMPDIR/winmm_wavein_selftest
 *
 * Proves: zero device enumeration; the two added caps names (A/W) answer the
 * documented MMSYSERR_BADDEVICEID and only zero the real, bounded structure;
 * WAVE_MAPPER open reports MMSYSERR_NODRIVER and every failed open clears the
 * output handle; handle-taking calls report MMSYSERR_INVALHANDLE and never
 * touch a WAVEHDR or MMTIME (no fabricated captured audio); exact stdcall
 * purge (1 return word + argc argument words) for every one of the 12 shims.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#ifdef WINMM_WAVEIN_SELFTEST_MINIMAL
/* Symbols winmm.c references for paths this selftest never exercises. They
 * are the real definitions in dsound.c / kernel32.c in a whole-layer build;
 * here they keep the focused link small without overriding those definitions. */
void hle_exit(int code) { (void)code; fprintf(stderr, "hle_exit\n"); abort(); }
uint32_t hle_thread_id(void) { return 1; }
uint32_t hle_ticks_ms(void) { return hle_clock_ms(); }
void hle_set_event(uint32_t h) { (void)h; }
void hle_post_message(uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp) {
    (void)hwnd; (void)msg; (void)wp; (void)lp;   /* waveOut callback path, not taken here */
}
int hle_wide_to_ansi(const uint16_t *w, char *out, size_t n) {
    size_t i;
    if (!w || !n) return 0;
    for (i = 0; i + 1 < n && w[i]; i++) out[i] = (char)w[i];
    out[i] = 0;
    return 1;
}
int hle_audio_stream_open(int rate, int channels) { (void)rate; (void)channels; return -1; }
void hle_audio_stream_push(int id, const int16_t *pcm, int frames) { (void)id; (void)pcm; (void)frames; }
int hle_audio_stream_queued(int id) { (void)id; return 0; }
void hle_audio_stream_gain(int id, float gain) { (void)id; (void)gain; }
void hle_audio_stream_close(int id) { (void)id; }
#endif

enum { MMSYS_NOERROR = 0u, MMSYS_BADDEVICEID = 2u, MMSYS_INVALHANDLE = 5u, MMSYS_NODRIVER = 6u };
enum { WAVE_MAPPER = 0xFFFFFFFFu };
enum { WAVEIN_CAPS_A = 48, WAVEIN_CAPS_W = 80, WAVEHDR_SIZE = 32, MMTIME_SIZE = 12 };

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
    else printf("ok   %s\n", what);
}
/* Call the shim the way lifted code does: stdcall args pushed by hle_call_guest. */
static uint32_t call(const char *name, int argc, const uint32_t *args) {
    uint32_t fn = hle_resolve(name);
    if (!fn) { printf("FAIL %s did not resolve\n", name); fails++; return 0xFFFFFFFFu; }
    return hle_call_guest(fn, argc, args);
}
/* hle_call_guest restores the register file, so it cannot see a wrong stdcall
 * purge. Drive the registered shim directly with a synthetic return address
 * and count the words it popped (1 + argc for a correct stdcall). */
static uint32_t shim_pops(const char *name, int argc, const uint32_t *args, uint32_t *out_eax) {
    uint32_t va = hle_resolve(name);
    recomp_func_t fn = va ? recomp_lookup_import(va) : 0;
    if (!fn) return 0xFFFFFFFFu;
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t scratch = hle_va(hle_alloc(64));
    int i;
    g_esp = scratch + 32;
    for (i = argc - 1; i >= 0; --i) { g_esp -= 4; MEM32(g_esp) = args[i]; }
    g_esp -= 4; MEM32(g_esp) = RECOMP_RETADDR;
    uint32_t esp_in = g_esp;
    fn();
    uint32_t words = (g_esp - esp_in) / 4u;
    if (out_eax) *out_eax = g_eax;
    g_esp = saved_esp; g_eax = saved_eax;
    return words;
}
static int all_bytes(const void *p, size_t n, unsigned char value) {
    const unsigned char *b = (const unsigned char *)p;
    for (size_t i = 0; i < n; i++) if (b[i] != value) return 0;
    return 1;
}

/* Every registered name and its documented stdcall argument count. */
static const struct { const char *name; int argc; } kApi[] = {
    { "waveInGetNumDevs",       0 },
    { "waveInGetDevCapsA",      3 },
    { "waveInGetDevCapsW",      3 },
    { "waveInOpen",             6 },
    { "waveInClose",            1 },
    { "waveInPrepareHeader",    3 },
    { "waveInUnprepareHeader",  3 },
    { "waveInAddBuffer",        3 },
    { "waveInStart",            1 },
    { "waveInStop",             1 },
    { "waveInReset",            1 },
    { "waveInGetPosition",      3 },
};
#define NAPI ((int)(sizeof kApi / sizeof kApi[0]))

int main(void) {
    uint32_t zero[6] = { 0, 0, 0, 0, 0, 0 };
    int i;

    win32hle_register(win32hle_winmm);
    mach_enter();

    for (i = 0; i < NAPI; i++) ok(kApi[i].name, hle_resolve(kApi[i].name) != 0);

    /* Enumeration is honest: no capture device is reported. */
    ok("waveInGetNumDevs -> 0", call("waveInGetNumDevs", 0, zero) == 0);

    /* Caps: error, bounded zeroing of the real ANSI / Unicode structs only. */
    {
        uint32_t caps_va = hle_va(hle_alloc(256));
        unsigned char *caps = (unsigned char *)hle_ptr(caps_va);
        uint32_t a[3];
        ok("caps guest buffer", caps_va != 0);
        if (!caps_va) return 1;

        memset(caps, 0xAA, 256);
        a[0] = 0; a[1] = caps_va; a[2] = WAVEIN_CAPS_A;
        ok("waveInGetDevCapsA -> BADDEVICEID", call("waveInGetDevCapsA", 3, a) == MMSYS_BADDEVICEID);
        ok("A caps zeroed within cb", all_bytes(caps, WAVEIN_CAPS_A, 0));
        ok("A caps write bounded by struct size", caps[WAVEIN_CAPS_A] == 0xAA && caps[255] == 0xAA);

        memset(caps, 0xAA, 256);
        a[0] = 999; a[1] = caps_va; a[2] = 4;
        ok("unknown device id -> BADDEVICEID", call("waveInGetDevCapsA", 3, a) == MMSYS_BADDEVICEID);
        ok("short cb zeroes only cb bytes", all_bytes(caps, 4, 0) && caps[4] == 0xAA);
        a[0] = 0; a[1] = 0; a[2] = WAVEIN_CAPS_A;
        ok("NULL A caps -> BADDEVICEID (no crash)", call("waveInGetDevCapsA", 3, a) == MMSYS_BADDEVICEID);

        memset(caps, 0xAA, 256);
        a[0] = 0; a[1] = caps_va; a[2] = WAVEIN_CAPS_W;
        ok("waveInGetDevCapsW -> BADDEVICEID", call("waveInGetDevCapsW", 3, a) == MMSYS_BADDEVICEID);
        ok("W caps zeroed within cb", all_bytes(caps, WAVEIN_CAPS_W, 0));
        ok("W caps write bounded by struct size", caps[WAVEIN_CAPS_W] == 0xAA && caps[255] == 0xAA);
        a[0] = 7; a[1] = caps_va; a[2] = 256;
        memset(caps, 0xAA, 256);
        ok("oversized cb cannot invent W caps", call("waveInGetDevCapsW", 3, a) == MMSYS_BADDEVICEID);
        ok("oversized cb still bounded by struct size", all_bytes(caps, WAVEIN_CAPS_W, 0) && caps[WAVEIN_CAPS_W] == 0xAA);
        a[0] = 0; a[1] = 0; a[2] = WAVEIN_CAPS_W;
        ok("NULL W caps -> BADDEVICEID (no crash)", call("waveInGetDevCapsW", 3, a) == MMSYS_BADDEVICEID);
    }

    /* No fake handle is left behind by a failed open. */
    {
        uint32_t hv = hle_va(hle_alloc(4)), a[6];
        ok("handle guest buffer", hv != 0);
        if (!hv) return 1;
        MEM32(hv) = 0xDEADBEEFu;
        a[0] = hv; a[1] = 0; a[2] = 0; a[3] = 0; a[4] = 0; a[5] = 0;
        ok("waveInOpen(device 0) -> BADDEVICEID", call("waveInOpen", 6, a) == MMSYS_BADDEVICEID);
        ok("failed open leaves no handle", MEM32(hv) == 0);
        MEM32(hv) = 0xDEADBEEFu;
        a[0] = hv; a[1] = WAVE_MAPPER;
        ok("waveInOpen(WAVE_MAPPER) -> NODRIVER", call("waveInOpen", 6, a) == MMSYS_NODRIVER);
        ok("failed mapper open leaves no handle", MEM32(hv) == 0);
        a[0] = 0; a[1] = 0;
        ok("waveInOpen(NULL out) -> BADDEVICEID (no crash)", call("waveInOpen", 6, a) == MMSYS_BADDEVICEID);
    }

    /* Every handle call fails as it must with no device, and writes nothing:
     * a WAVEHDR (dwBytesRecorded included) and an MMTIME stay byte-for-byte
     * as the guest left them. That is the no-fake-audio boundary. */
    {
        uint32_t wh = hle_va(hle_alloc(64)), mt = hle_va(hle_alloc(32));
        uint32_t a[3];
        ok("guest WAVEHDR/MMTIME buffers", wh != 0 && mt != 0);
        if (!wh || !mt) return 1;
        memset(hle_ptr(wh), 0x5A, 64);
        memset(hle_ptr(mt), 0x5A, 32);
        a[0] = 0xDEADBEEFu; a[1] = wh; a[2] = WAVEHDR_SIZE;
        ok("waveInPrepareHeader -> INVALHANDLE", call("waveInPrepareHeader", 3, a) == MMSYS_INVALHANDLE);
        ok("waveInUnprepareHeader -> INVALHANDLE", call("waveInUnprepareHeader", 3, a) == MMSYS_INVALHANDLE);
        ok("waveInAddBuffer -> INVALHANDLE", call("waveInAddBuffer", 3, a) == MMSYS_INVALHANDLE);
        a[1] = mt; a[2] = MMTIME_SIZE;
        ok("waveInGetPosition -> INVALHANDLE", call("waveInGetPosition", 3, a) == MMSYS_INVALHANDLE);
        ok("WAVEHDR untouched (no captured audio)", all_bytes(hle_ptr(wh), 64, 0x5A));
        ok("WAVEHDR.dwBytesRecorded untouched", all_bytes(hle_ptr(wh + 8), 4, 0x5A));
        ok("MMTIME untouched (no fake position)", all_bytes(hle_ptr(mt), 32, 0x5A));

        zero[0] = 0xDEADBEEFu;
        ok("waveInClose -> INVALHANDLE", call("waveInClose", 1, zero) == MMSYS_INVALHANDLE);
        ok("waveInStart -> INVALHANDLE", call("waveInStart", 1, zero) == MMSYS_INVALHANDLE);
        ok("waveInStop -> INVALHANDLE", call("waveInStop", 1, zero) == MMSYS_INVALHANDLE);
        ok("waveInReset -> INVALHANDLE", call("waveInReset", 1, zero) == MMSYS_INVALHANDLE);
    }

    /* Exact stdcall purge: 1 return word + argc argument words for each shim. */
    {
        uint32_t a[6] = { 0, 0, 0, 0, 0, 0 }, eax;
        for (i = 0; i < NAPI; i++) {
            char what[96];
            snprintf(what, sizeof what, "%s pops %d words", kApi[i].name, 1 + kApi[i].argc);
            ok(what, shim_pops(kApi[i].name, kApi[i].argc, a, &eax) == (uint32_t)(1 + kApi[i].argc));
        }
    }

    mach_leave();
    printf("winmm_wavein_selftest: %s\n", fails ? "FAILURES" : "all checks passed");
    return fails != 0;
}
