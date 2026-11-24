/*
 * winmm_waveout_abi_selftest.c - the WINMM waveOut device driven as a lifted
 * guest drives it: stdcall calls through a guest shim driver, a guest
 * WAVECALLBACK for WOM_DONE, and guest-visible WAVEHDR / MMTIME structures.
 *
 * Proves the four newly answered imports (waveOutPause, waveOutRestart,
 * waveOutGetPosition, waveOutBreakLoop) are real state changes on a device
 * that feeds PCM into dsound.c's SDL2 mixer, not no-ops over a silent handle:
 *   - open validates the format, write rejects un/queued headers;
 *   - the WAVEHDR is INQUEUE after write and DONE only after the audio clock
 *     has consumed its frames, with a timed WOM_DONE (0x03BD) callback whose
 *     handle / instance / header arguments round-trip;
 *   - GetPosition reports consumed frames (TIME_SAMPLES) and coerces other
 *     requests to TIME_BYTES, rejecting a short MMTIME;
 *   - pause holds completion, restart releases it, break loop ends a looping
 *     buffer at the next pass instead of repeating dwLoops times forever.
 *
 * Headless: SDL_AUDIODRIVER=dummy still runs the mixer in real time.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "win32hle.h"

#define CB_VA   0x00401500u                 /* guest waveOutProc */
#define DRV_VA  0x00401600u                 /* guest code calling the shims */

#define GUEST_PROLOGUE(va) \
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0; RECOMP_ENTER(va)

#define MMSYSERR_NOERROR  0u
#define MMSYSERR_ERROR    1u
#define MMSYSERR_BADDEVICEID 2u
#define MMSYSERR_INVALHANDLE 5u
#define MMSYSERR_NODRIVER 6u
#define MMSYSERR_NOTSUPPORTED 8u
#define MMSYSERR_INVALFLAG 10u
#define MMSYSERR_INVALPARAM 11u
#define WAVERR_STILLPLAYING 33u
#define WAVERR_UNPREPARED  34u
#define WHDR_DONE       0x00000001u
#define WHDR_PREPARED   0x00000002u
#define WHDR_BEGINLOOP  0x00000004u
#define WHDR_ENDLOOP    0x00000008u
#define WHDR_INQUEUE    0x00000010u
#define CALLBACK_FUNCTION 0x00030000u
#define WOM_DONE        0x000003BDu
#define TIME_SAMPLES    0x00000002u
#define TIME_BYTES      0x00000004u
#define TEST_FRAMES     4410u               /* 100 ms at 44.1 kHz */
#define WAVEHDR_SIZE    32u                 /* sizeof(WAVEHDR) on x86, 8 dwords */
#define OUT_RATE        44100u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
    else printf("ok   %s\n", what);
}
static uint64_t now_ns(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}
static void wait_us(uint64_t us) { usleep((useconds_t)us); }

/* Guest WAVECALLBACK: void CALLBACK(HWAVEOUT, UINT, DWORD, DWORD, DWORD),
 * stdcall. The message is 0x03BD (Wine-10.0 mmsystem.h), not a made-up value. */
static volatile int g_done_calls;
static volatile uint32_t g_done_hwo, g_done_msg, g_done_inst, g_done_hdr;
static volatile uint64_t g_done_ns;
static void guest_waveout_proc(void) {
    GUEST_PROLOGUE(CB_VA);
    uint32_t hwo = MEM32(esp + 4), msg = MEM32(esp + 8);
    uint32_t inst = MEM32(esp + 12), hdr = MEM32(esp + 16);
    if (msg == WOM_DONE) {
        if (!g_done_calls) g_done_ns = now_ns();
        g_done_hwo = hwo; g_done_msg = msg; g_done_inst = inst; g_done_hdr = hdr;
        g_done_calls++;
    }
    eax = 0;
    esp += 4u + 5u * 4u;                     /* stdcall: pop ret + 5 args */
}

/* One lifted driver for every shim: push the stdcall args in reverse, do an
 * indirect call through the IAT-resolved VA, save eax, restore the stack. */
static uint32_t g_target, g_nargs, g_args[8], g_rc;
static void guest_call_shim(void) {
    GUEST_PROLOGUE(DRV_VA);
    for (int i = (int)g_nargs - 1; i >= 0; i--) PUSH32(esp, g_args[i]);
    RECOMP_ICALL(g_target);
    g_rc = eax;
    esp += 4u * g_nargs;                     /* RECOMP_ICALL's callee popped its own frame */
}

const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { CB_VA, guest_waveout_proc },
    { DRV_VA, guest_call_shim }
};
const uint32_t recomp_dispatch_count = 2;

static uint32_t call(const char *name, uint32_t n, const uint32_t *args) {
    uint32_t fn = hle_resolve(name);
    if (!fn) { printf("FAIL %s did not resolve\n", name); fails++; return 0xFFFFFFFFu; }
    g_target = fn; g_nargs = n;
    for (uint32_t i = 0; i < n; i++) g_args[i] = args[i];
    hle_call_guest(DRV_VA, 0, NULL);
    return g_rc;
}
static uint32_t call1(const char *name, uint32_t a) { return call(name, 1, (uint32_t[]){ a }); }
static uint32_t call3(const char *name, uint32_t a, uint32_t b, uint32_t c) {
    return call(name, 3, (uint32_t[]){ a, b, c });
}
static uint32_t rd32(uint32_t va) {
    uint32_t v;
    mach_enter(); v = MEM32(va); mach_leave();
    return v;
}
static void wr32(uint32_t va, uint32_t v) {
    mach_enter(); MEM32(va) = v; mach_leave();
}
static int wait_done(uint64_t limit_ms) {
    uint64_t t0 = now_ns();
    while (!g_done_calls && (now_ns() - t0) / 1000000ull < limit_ms) wait_us(1000);
    return g_done_calls != 0;
}

int main(void) {
    setenv("SDL_AUDIODRIVER", "dummy", 1);
    setenv("SDL_VIDEODRIVER", "dummy", 1);
    win32hle_register(win32hle_winmm);

    static const char *names[] = {
        "waveOutOpen", "waveOutWrite", "waveOutPrepareHeader", "waveOutUnprepareHeader",
        "waveOutPause", "waveOutRestart", "waveOutBreakLoop", "waveOutGetPosition",
        "waveOutReset", "waveOutClose", "waveOutGetNumDevs"
    };
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; i++)
        ok(names[i], hle_resolve(names[i]) != 0);

    /* Guest WAVEFORMATEX, WAVEHDR and MMTIME, all in guest memory. */
    uint32_t fmt = hle_va(hle_alloc(40));
    uint32_t hwo_va = hle_va(hle_alloc(4));
    uint32_t inst_va = hle_va(hle_alloc(4));
    uint32_t data_va = hle_va(hle_alloc(TEST_FRAMES * 4u));
    uint32_t wh_va = hle_va(hle_alloc(WAVEHDR_SIZE));
    uint32_t mmt_va = hle_va(hle_alloc(16));
    ok("guest buffers allocated", fmt && hwo_va && inst_va && data_va && wh_va && mmt_va);
    if (!(fmt && hwo_va && inst_va && data_va && wh_va && mmt_va)) return 1;

    mach_enter();
    MEM16(fmt + 0) = 1;                      /* WAVE_FORMAT_PCM */
    MEM16(fmt + 2) = 2;                      /* stereo */
    MEM32(fmt + 4) = OUT_RATE;
    MEM32(fmt + 8) = OUT_RATE * 4u;          /* bytes/sec */
    MEM16(fmt + 12) = 4;                     /* block align */
    MEM16(fmt + 14) = 16;                    /* bits/sample */
    MEM16(fmt + 16) = 0;                     /* cbSize */
    MEM32(inst_va) = 0xC0FFEEu;
    uint8_t *pcm = (uint8_t *)hle_ptr(data_va);
    for (uint32_t i = 0; i < TEST_FRAMES; i++) {
        int16_t l = ((i / 64) & 1u) ? 12000 : -12000;   /* 64-frame square wave */
        int16_t r = (int16_t)-l;
        memcpy(pcm + 4u * i, &l, 2);
        memcpy(pcm + 4u * i + 2u, &r, 2);
    }
    MEM32(wh_va + 0) = data_va;
    MEM32(wh_va + 4) = TEST_FRAMES * 4u;     /* dwBufferLength */
    MEM32(wh_va + 8) = 0;                    /* dwBytesRecorded */
    MEM32(wh_va + 12) = 0;                   /* dwUser */
    MEM32(wh_va + 16) = 0;                   /* dwFlags */
    MEM32(wh_va + 20) = 0;                   /* dwLoops */
    mach_leave();

    /* The four new imports must reject an invalid handle, not return success. */
    ok("waveOutPause rejects a bad handle", call1("waveOutPause", 0xDEADBEEFu) == MMSYSERR_INVALHANDLE);
    ok("waveOutRestart rejects a bad handle", call1("waveOutRestart", 0xDEADBEEFu) == MMSYSERR_INVALHANDLE);
    ok("waveOutGetPosition rejects a bad handle",
       call3("waveOutGetPosition", 0xDEADBEEFu, mmt_va, 12) == MMSYSERR_INVALHANDLE);
    ok("waveOutBreakLoop rejects a bad handle", call1("waveOutBreakLoop", 0xDEADBEEFu) == MMSYSERR_INVALHANDLE);

    /* Bad format and bad device are errors, not a silent fake device. */
    wr32(fmt, 3u | (2u << 16));              /* wFormatTag=IEEE float, nChannels=2 */
    ok("open rejects a non-PCM format",
       call("waveOutOpen", 6, (uint32_t[]){ hwo_va, 0, fmt, 0, 0, 0 }) == MMSYSERR_NOTSUPPORTED);
    wr32(fmt, 1u | (2u << 16));              /* restore PCM stereo */
    ok("open rejects a bad device id",
       call("waveOutOpen", 6, (uint32_t[]){ hwo_va, 7, fmt, 0, 0, 0 }) == MMSYSERR_BADDEVICEID);
    ok("open rejects an unknown callback type",
       call("waveOutOpen", 6, (uint32_t[]){ hwo_va, 0, fmt, 0, 0, 0x00060000u }) == MMSYSERR_INVALFLAG);

    /* Real open with a guest WAVECALLBACK, then prepare + write one buffer. */
    /* dwCallback / dwInstance are values, not pointers: pass the value the
     * guest stored at inst_va, as a real WAVECALLBACK registration would. */
    uint32_t r = call("waveOutOpen", 6,
                      (uint32_t[]){ hwo_va, 0, fmt, CB_VA, rd32(inst_va), CALLBACK_FUNCTION });
    ok("waveOutOpen succeeded", r == MMSYSERR_NOERROR);
    uint32_t hwo = rd32(hwo_va);
    ok("waveOutOpen returned a handle", hwo != 0);
    if (r || !hwo) return 1;

    ok("GetPosition rejects a short MMTIME",
       call3("waveOutGetPosition", hwo, mmt_va, 8) == MMSYSERR_ERROR);
    ok("GetPosition rejects a NULL MMTIME",
       call3("waveOutGetPosition", hwo, 0, 12) == MMSYSERR_INVALPARAM);
    ok("write before prepare is WAVERR_UNPREPARED",
       call3("waveOutWrite", hwo, wh_va, WAVEHDR_SIZE) == WAVERR_UNPREPARED);
    ok("waveOutPrepareHeader succeeded",
       call3("waveOutPrepareHeader", hwo, wh_va, WAVEHDR_SIZE) == MMSYSERR_NOERROR);

    int q_before = hle_audio_stream_queued(0);
    ok("waveOutWrite succeeded", call3("waveOutWrite", hwo, wh_va, WAVEHDR_SIZE) == MMSYSERR_NOERROR);
    int q_after = hle_audio_stream_queued(0);
    ok("PCM reached the SDL2 mixer stream", q_after > q_before);
    ok("a second write of a queued header is WAVERR_STILLPLAYING",
       call3("waveOutWrite", hwo, wh_va, WAVEHDR_SIZE) == WAVERR_STILLPLAYING);
    uint32_t flags = rd32(wh_va + 16);
    ok("header is INQUEUE, not DONE, right after write",
       (flags & WHDR_INQUEUE) && !(flags & WHDR_DONE));
    ok("WOM_DONE is not delivered synchronously by waveOutWrite", g_done_calls == 0);

    uint64_t t0 = now_ns();
    ok("WOM_DONE arrived", wait_done(3000));
    uint64_t delay_ms = g_done_calls ? (g_done_ns - t0) / 1000000ull : 0;
    ok("WOM_DONE message is 0x03BD", g_done_msg == WOM_DONE);
    ok("WOM_DONE carried the device handle", g_done_hwo == hwo);
    ok("WOM_DONE carried dwInstance", g_done_inst == 0xC0FFEEu);
    ok("WOM_DONE carried the WAVEHDR", g_done_hdr == wh_va);
    ok("completion was timed by the audio clock (>= 50 ms)", delay_ms >= 50);
    ok("completion was not stalled (< 3000 ms)", delay_ms < 3000);
    flags = rd32(wh_va + 16);
    ok("header is DONE and no longer INQUEUE after playback",
       (flags & WHDR_DONE) && !(flags & WHDR_INQUEUE));
    ok("unprepare after completion succeeds",
       call3("waveOutUnprepareHeader", hwo, wh_va, WAVEHDR_SIZE) == MMSYSERR_NOERROR);

    /* Position is frames consumed by the mixer, not wall-clock time. */
    wr32(mmt_va, TIME_SAMPLES);
    ok("GetPosition(TIME_SAMPLES) succeeded",
       call3("waveOutGetPosition", hwo, mmt_va, 12) == MMSYSERR_NOERROR);
    uint32_t type = rd32(mmt_va), samples = rd32(mmt_va + 4);
    ok("TIME_SAMPLES is preserved", type == TIME_SAMPLES);
    ok("reported samples equal the frames written", samples == TEST_FRAMES);
    wr32(mmt_va, 1);                         /* TIME_MS: Wine coerces to TIME_BYTES */
    ok("GetPosition(TIME_MS) succeeded",
       call3("waveOutGetPosition", hwo, mmt_va, 12) == MMSYSERR_NOERROR);
    type = rd32(mmt_va);
    uint32_t bytes = rd32(mmt_va + 4);
    ok("a non-sample request is coerced to TIME_BYTES", type == TIME_BYTES);
    ok("reported bytes equal frames * block align", bytes == TEST_FRAMES * 4u);

    /* Pause before writing: the buffer queues but cannot complete until the
     * device is restarted. This is the API TrackMania imports. */
    ok("waveOutPause succeeded", call1("waveOutPause", hwo) == MMSYSERR_NOERROR);
    wr32(wh_va + 16, WHDR_PREPARED);         /* a fresh, prepared header */
    g_done_calls = 0;
    ok("write while paused succeeded", call3("waveOutWrite", hwo, wh_va, WAVEHDR_SIZE) == MMSYSERR_NOERROR);
    wait_us(180000);                         /* longer than one 100 ms buffer */
    flags = rd32(wh_va + 16);
    ok("paused device does not complete the header", g_done_calls == 0 && (flags & WHDR_INQUEUE));
    ok("waveOutRestart succeeded", call1("waveOutRestart", hwo) == MMSYSERR_NOERROR);
    ok("restarted device completes the header", wait_done(3000));
    flags = rd32(wh_va + 16);
    ok("header is DONE after restart", (flags & WHDR_DONE) && !(flags & WHDR_INQUEUE));

    /* A looping buffer is re-pushed pass by pass; break_loop stops it. */
    wr32(wh_va + 16, WHDR_PREPARED | WHDR_BEGINLOOP | WHDR_ENDLOOP);
    wr32(wh_va + 20, 100);                   /* would play for 10 s without a break */
    g_done_calls = 0;
    uint64_t loop_start = now_ns();
    ok("write looping buffer succeeded", call3("waveOutWrite", hwo, wh_va, WAVEHDR_SIZE) == MMSYSERR_NOERROR);
    /* dwLoops=100 would play for ~10 s. If looping is real the buffer is still
     * queued well after its one-pass (100 ms) duration; the break must then
     * end it at the next pass boundary. */
    wait_us(200000);
    ok("looping buffer is still queued after one pass", g_done_calls == 0);
    ok("waveOutBreakLoop succeeded", call1("waveOutBreakLoop", hwo) == MMSYSERR_NOERROR);
    ok("looping buffer completed after the break", wait_done(3000));
    uint64_t loop_ms = (now_ns() - loop_start) / 1000000ull;
    ok("loop covered more than one pass (>= 150 ms)", loop_ms >= 150);
    ok("break ended the loop promptly (< 2000 ms)", loop_ms < 2000);

    ok("waveOutReset succeeded", call1("waveOutReset", hwo) == MMSYSERR_NOERROR);
    wr32(mmt_va, TIME_SAMPLES);
    call3("waveOutGetPosition", hwo, mmt_va, 12);
    ok("position restarts at zero after reset", rd32(mmt_va + 4) == 0);
    ok("waveOutClose succeeded", call1("waveOutClose", hwo) == MMSYSERR_NOERROR);
    ok("closed handle is invalid", call1("waveOutPause", hwo) == MMSYSERR_INVALHANDLE);

    if (fails == 0) printf("winmm_waveout_abi_selftest: all checks passed\n");
    return fails != 0;
}
