/*
 * winmm.c - win32hle WINMM: multimedia timers, joystick, waveOut audio,
 * sndPlaySound, a waveaudio MCI command subset, and mmio RIFF access.
 *
 * Timing is on libc/the virtual clock. All PCM playback -- waveOut,
 * sndPlaySound and MCI "play" -- feeds dsound.c's single SDL2 audio mixer
 * stream. There is one device and one mixer, never a second audio backend.
 *   - waveOut completes a WAVEHDR only once the audio clock has consumed its
 *     frames; waveOutPause/Restart/GetPosition/BreakLoop are real state changes.
 *   - sndPlaySound decodes RIFF/WAVE and returns the Win32 BOOL.
 *   - mciSendStringA implements a documented subset of waveaudio commands
 *     (open/close/play/stop/pause/resume/status/seek/set volume) on that same
 *     stream; mciGetErrorStringA formats the MCI errors. The message form
 *     (mciSendCommandA) remains unsupported.
 * No mixer, no recording device and no MIDI device: those report zero devices
 * and documented errors rather than a pretend handle or fabricated
 * capabilities. Joystick stays absent (the game takes its keyboard/mouse path).
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "win32hle.h"

/* MMSYSERR_* and the wave constants from Wine-10.0 include/mmsystem.h. */
#define MMSYSERR_NOERROR    0u
#define MMSYSERR_ERROR      1u
#define MMSYSERR_BADDEVICEID 2u
#define MMSYSERR_ALLOCATED  4u
#define MMSYSERR_INVALHANDLE 5u
#define MMSYSERR_NODRIVER   6u
#define MMSYSERR_NOMEM      7u
#define MMSYSERR_NOTSUPPORTED 8u
#define MMSYSERR_INVALFLAG  10u
#define MMSYSERR_INVALPARAM 11u
#define JOYERR_UNPLUGGED  167u
#define MCIERR_UNSUPPORTED_FUNCTION 274u
#define WAVERR_STILLPLAYING (32u + 1u)
#define WAVERR_UNPREPARED   (32u + 2u)

/* --- multimedia timer --- */
/* Through the clock (clock.c), like every other time-returning shim: this is
 * the call that started the Doukutsu divergence, so it is the one that has to
 * report virtual time for a replay to be comparable. */
static void m_timeGetTime(void) { RET(hle_clock_ms(), 0); }
static void m_timeBeginPeriod(void) { RET(MMSYSERR_NOERROR, 1); }
static void m_timeGetDevCaps(void) {           /* (&TIMECAPS, size) */
    if (A32(0) && A32(1) >= 8) MEM32(A32(0)) = 1, MEM32(A32(0) + 4) = 1000000;
    RET(MMSYSERR_NOERROR, 2);
}

/* --- multimedia timers ---
 * Each timer is a host thread that calls the guest's callback through
 * hle_call_guest, so the callback runs once it gets the machine, as on
 * Windows it runs on winmm's thread. timeKillEvent returns only once a
 * callback already under way has finished (giving the machine up while it
 * waits): a game frees what its callback uses right after the kill
 * (Tiberian Sun's movie audio, its bring-up log 8). */
#define MAX_MMTIMERS 32
static struct { volatile int used, killed, inflight; uint32_t id, delay, proc, user, flags; pthread_t thread; } g_mmt[MAX_MMTIMERS];
static uint32_t g_mmt_next = 1;
static void *mmtimer(void *arg) {
    int i = (int)(intptr_t)arg;
    uint32_t due = hle_ticks_ms() + g_mmt[i].delay;
    for (;;) {
        int32_t wait = (int32_t)(due - hle_ticks_ms());
        if (wait > 0) usleep((useconds_t)wait * 1000u);
        if (g_mmt[i].killed) break;
        __sync_fetch_and_add(&g_mmt[i].inflight, 1);
        if (!g_mmt[i].killed) {
            if (g_mmt[i].flags & 0x30u) {                /* TIME_CALLBACK_EVENT_SET / _PULSE: proc is an event */
                uint32_t h = g_mmt[i].proc;
                mach_enter();
                hle_set_event(h);
                mach_leave();
            } else {
                uint32_t a[5] = { g_mmt[i].id, 0, g_mmt[i].user, 0, 0 };
                hle_call_guest(g_mmt[i].proc, 5, a);
            }
        }
        __sync_fetch_and_sub(&g_mmt[i].inflight, 1);
        if (!(g_mmt[i].flags & 1u)) break;               /* TIME_ONESHOT */
        due += g_mmt[i].delay ? g_mmt[i].delay : 1;
        if ((int32_t)(hle_ticks_ms() - due) > 1000) due = hle_ticks_ms();   /* fell far behind: no catch-up storm */
    }
    g_mmt[i].used = 0;
    return NULL;
}
static void m_timeSetEvent(void) {             /* (delay, resolution, proc, user, flags) */
    for (int i = 0; i < MAX_MMTIMERS; i++)
        if (!g_mmt[i].used) {
            g_mmt[i].used = 1, g_mmt[i].killed = 0, g_mmt[i].inflight = 0;
            g_mmt[i].id = g_mmt_next++, g_mmt[i].delay = A32(0), g_mmt[i].proc = A32(2), g_mmt[i].user = A32(3), g_mmt[i].flags = A32(4);
            if (pthread_create(&g_mmt[i].thread, NULL, mmtimer, (void *)(intptr_t)i) != 0) { g_mmt[i].used = 0; RET(0, 5); }
            pthread_detach(g_mmt[i].thread);
            RET(g_mmt[i].id, 5);
        }
    RET(0, 5);
}
static void m_timeKillEvent(void) {
    for (int i = 0; i < MAX_MMTIMERS; i++)
        if (g_mmt[i].used && g_mmt[i].id == A32(0) && !g_mmt[i].killed) {
            g_mmt[i].killed = 1;
            if (!pthread_equal(g_mmt[i].thread, pthread_self()))
                while (g_mmt[i].inflight) { hle_block_begin(); usleep(200); hle_block_end(); }
            RET(MMSYSERR_NOERROR, 1);
        }
    RET(97u /* MMSYSERR_INVALPARAM */, 1);
}
static void m_timeEndPeriod(void)   { RET(MMSYSERR_NOERROR, 1); }

/* --- joystick: none present, so the game takes its keyboard/mouse path --- */
static void m_joyGetNumDevs(void)   { RET(0, 0); }
static void m_joyGetDevCapsA(void)  { RET(JOYERR_UNPLUGGED, 3); }
static void m_joyGetPos(void)       { RET(JOYERR_UNPLUGGED, 2); }
static void m_joyGetPosEx(void)     { RET(JOYERR_UNPLUGGED, 2); }

/* --- waveOut: real PCM into the SDL2 mixer dsound.c already owns ---
 *
 * waveOutOpen takes a dsound.c host stream at the guest's PCM format;
 * waveOutWrite converts the WAVEHDR's samples to interleaved s16 and pushes
 * them into it. A host thread watches the stream's queue, marks a buffer DONE
 * and delivers its WOM_DONE only after the audio clock has consumed its
 * frames. No second SDL device and no duplicate mixer: the samples are the
 * same frames bink.c and sndPlaySound push through hle_audio_stream_*.
 *
 * `pushed` is cumulative frames sent to the stream, `played` cumulative frames
 * the mixer has consumed (pushed - queued). Each queued buffer records the
 * stream frame where its first pass began, so completion and GetPosition are
 * in audio time, not wall time. A buffer with WHDR_BEGINLOOP|ENDLOOP is re-pushed
 * while its dwLoops remain; waveOutBreakLoop stops that (Wine waveform.c's
 * loop_counter = 0), which is a real change in what is heard.
 */
#define WHDR_DONE       0x00000001u
#define WHDR_PREPARED   0x00000002u
#define WHDR_BEGINLOOP  0x00000004u
#define WHDR_ENDLOOP    0x00000008u
#define WHDR_INQUEUE    0x00000010u
#define CALLBACK_TYPEMASK 0x00070000u
#define CALLBACK_WINDOW 0x00010000u
#define CALLBACK_FUNCTION 0x00030000u
#define CALLBACK_EVENT  0x00050000u
/* Wine-10.0 mmsystem.h: WOM_DONE == MM_WOM_DONE == 0x03BD (not 0x3D5). */
#define WOM_DONE        0x000003BDu
#define MM_WOM_DONE     0x000003BDu
#define TIME_SAMPLES    0x0002u
#define TIME_BYTES      0x0004u
#define WAVE_FORMAT_PCM 1u
#define MMTIME_SIZE     12u              /* UINT wType + 8-byte union on x86 */
#define MAX_WAVEOUT 8
#define MAX_WO_BUFS 128
#define WO_MAX_FRAMES (1u << 16)         /* the ring is 2^17; leave headroom */

typedef struct {
    uint32_t hdr, data, frames, start;   /* WAVEHDR VA, PCM VA, frames/pass, first-pass offset */
    int passes, loops, looping;
} wo_buf_t;
typedef struct {
    int used, stream, rate, channels, bits, block;
    volatile int paused, closing, break_loop;
    uint32_t callback, instance, flags, volume;
    uint64_t pushed, played;             /* frames sent / consumed by the mixer */
    wo_buf_t bufs[MAX_WO_BUFS];
    int head, nbufs;
    pthread_t thread;
} waveout_t;
static waveout_t g_wo[MAX_WAVEOUT];
static pthread_mutex_t g_wo_lock = PTHREAD_MUTEX_INITIALIZER;

static uint32_t wo_handle(const waveout_t *w) {
    return 0x5A5E0000u | (uint32_t)(int)(w - g_wo) + 1u;
}
static waveout_t *wo_of(uint32_t h) {
    if ((h & 0xFFFF0000u) != 0x5A5E0000u) return NULL;
    uint32_t i = (h & 0xFFFFu) - 1u;
    return i < MAX_WAVEOUT && g_wo[i].used ? &g_wo[i] : NULL;
}
/* Windows keeps a stereo DWORD volume; the host stream has one scalar gain. */
static float wo_gain(const waveout_t *w) {
    if (w->volume == 0xFFFFFFFFu) return 1.0f;
    uint32_t l = w->volume & 0xFFFFu, r = w->volume >> 16;
    return (float)(((uint64_t)l + r) / 2u) / 65535.0f;
}
static void wo_fire(uint32_t h, uint32_t cb, uint32_t inst, uint32_t flags, uint32_t hdr) {
    if (!cb) return;
    if ((flags & CALLBACK_TYPEMASK) == CALLBACK_FUNCTION) {
        uint32_t a[5] = { h, WOM_DONE, inst, hdr, 0 };     /* WAVECALLBACK stdcall */
        hle_call_guest(cb, 5, a);                         /* takes the machine itself */
    } else if ((flags & CALLBACK_TYPEMASK) == CALLBACK_EVENT) {
        mach_enter(); hle_set_event(cb); mach_leave();
    } else if ((flags & CALLBACK_TYPEMASK) == CALLBACK_WINDOW) {
        mach_enter(); hle_post_message(cb, MM_WOM_DONE, h, hdr); mach_leave();
    }
}
/* Called with g_wo_lock held: the queue is in stream frames, as is `pushed`. */
static void wo_update_played(waveout_t *w) {
    int q = hle_audio_stream_queued(w->stream);
    if (q < 0) q = 0;
    /* pushed == queued means nothing has played yet, not everything has.
     * Getting this boundary wrong completes a buffer the instant it is written. */
    w->played = w->pushed >= (uint64_t)q ? w->pushed - (uint64_t)q : 0;
}
/* Convert one pass of guest PCM (8/16-bit, mono/stereo) to host s16 stereo. */
static int wo_push(waveout_t *w, uint32_t data, uint32_t frames) {
    if (!data || !frames || frames > WO_MAX_FRAMES) return -1;
    int16_t *pcm = (int16_t *)malloc((size_t)frames * 2u * sizeof(int16_t));
    if (!pcm) return -1;
    const uint8_t *src = (const uint8_t *)hle_ptr(data);
    for (uint32_t f = 0; f < frames; f++) {
        int16_t l, r;
        if (w->bits == 8) {
            int a = (int)src[(size_t)f * (size_t)w->block] - 128;
            int b = w->channels > 1 ? (int)src[(size_t)f * (size_t)w->block + 1] - 128 : a;
            l = (int16_t)(a << 8); r = (int16_t)(b << 8);
        } else {
            int16_t a, b;
            memcpy(&a, src + (size_t)f * (size_t)w->block, 2);
            memcpy(&b, src + (size_t)f * (size_t)w->block + (w->channels > 1 ? 2u : 0u), 2);
            l = a; r = b;
        }
        pcm[2 * (size_t)f] = l; pcm[2 * (size_t)f + 1] = r;
    }
    hle_audio_stream_push(w->stream, pcm, (int)frames);
    free(pcm);
    return 0;
}

/* Complete buffers the mixer has played. A loop is re-pushed at the tail of
 * the stream one pass at a time, so break_loop takes effect at the next pass
 * boundary rather than leaving pre-queued repeats audible. */
static void *wo_complete(void *arg) {
    waveout_t *w = (waveout_t *)arg;
    uint32_t h = wo_handle(w);
    while (!w->closing) {
        uint32_t hdrs[16], cb = 0, inst = 0, flags = 0;
        int n = 0;
        pthread_mutex_lock(&g_wo_lock);
        wo_update_played(w);
        uint64_t played = w->played;
        if (!w->paused) {
            while (w->head < w->nbufs && n < 16) {
                wo_buf_t *b = &w->bufs[w->head];
                uint64_t end = (uint64_t)b->start + (uint64_t)b->frames * (uint64_t)b->passes;
                if (played < end) break;
                if (b->looping && b->loops > 0 && !w->break_loop && w->head == w->nbufs - 1) {
                    if (wo_push(w, b->data, b->frames) == 0) {
                        w->pushed += b->frames;
                        b->passes++; b->loops--;
                        continue;
                    }
                    b->loops = 0;                         /* no stream room: stop looping */
                }
                MEM32(b->hdr + 16) = (MEM32(b->hdr + 16) | WHDR_DONE) & ~WHDR_INQUEUE;
                hdrs[n++] = b->hdr;
                w->head++;
            }
            if (w->head >= w->nbufs) w->head = w->nbufs = 0;
        }
        cb = w->callback; inst = w->instance; flags = w->flags;
        pthread_mutex_unlock(&g_wo_lock);
        for (int i = 0; i < n; i++) wo_fire(h, cb, inst, flags, hdrs[i]);
        usleep(1000);
    }
    return NULL;
}

/* Return every outstanding buffer. Used by Reset and by a Close that follows
 * one; the callbacks fire after the lock is dropped (they take the machine). */
static void wo_finish_all(waveout_t *w, int callbacks) {
    uint32_t hdrs[MAX_WO_BUFS], cb, inst, flags;
    int n = 0;
    pthread_mutex_lock(&g_wo_lock);
    for (int i = w->head; i < w->nbufs; i++) {
        MEM32(w->bufs[i].hdr + 16) = (MEM32(w->bufs[i].hdr + 16) | WHDR_DONE) & ~WHDR_INQUEUE;
        hdrs[n++] = w->bufs[i].hdr;
    }
    w->head = w->nbufs = 0;
    w->break_loop = 0;
    cb = w->callback; inst = w->instance; flags = w->flags;
    pthread_mutex_unlock(&g_wo_lock);
    if (callbacks)
        for (int i = 0; i < n; i++) wo_fire(wo_handle(w), cb, inst, flags, hdrs[i]);
}

static void m_waveOutOpen(void) {              /* (&hwo, devID, &fmt, cb, inst, flags) */
    uint32_t ph = A32(0), dev = A32(1), fmt = A32(2), cb = A32(3), inst = A32(4), flags = A32(5);
    if (ph) MEM32(ph) = 0;
    if (dev != 0 && dev != 0xFFFFFFFFu) RET(MMSYSERR_BADDEVICEID, 6);   /* WAVE_MAPPER */
    if (!fmt) RET(MMSYSERR_INVALPARAM, 6);
    uint32_t cbt = flags & CALLBACK_TYPEMASK;
    if (cbt && cbt != CALLBACK_FUNCTION && cbt != CALLBACK_EVENT && cbt != CALLBACK_WINDOW)
        RET(MMSYSERR_INVALFLAG, 6);
    int tag = MEM16(fmt), ch = MEM16(fmt + 2), rate = (int)MEM32(fmt + 4);
    int bits = MEM16(fmt + 14), align = MEM16(fmt + 12);
    if (tag != WAVE_FORMAT_PCM || (ch != 1 && ch != 2) || (bits != 8 && bits != 16) ||
        rate < 1000 || rate > 192000 || align != ch * bits / 8)
        RET(MMSYSERR_NOTSUPPORTED, 6);

    int stream = hle_audio_stream_open(rate, ch);
    if (stream < 0) RET(MMSYSERR_NODRIVER, 6);
    int slot = -1;
    pthread_mutex_lock(&g_wo_lock);
    for (int i = 0; i < MAX_WAVEOUT; i++) if (!g_wo[i].used) { slot = i; break; }
    pthread_mutex_unlock(&g_wo_lock);
    if (slot < 0) { hle_audio_stream_close(stream); RET(MMSYSERR_NODRIVER, 6); }
    waveout_t *w = &g_wo[slot];
    memset(w, 0, sizeof *w);                    /* no thread can see it yet */
    w->used = 1; w->stream = stream; w->rate = rate; w->channels = ch;
    w->bits = bits; w->block = align;
    w->callback = cb; w->instance = inst; w->flags = flags; w->volume = 0xFFFFFFFFu;
    if (pthread_create(&w->thread, NULL, wo_complete, w) != 0) {
        memset(w, 0, sizeof *w);
        hle_audio_stream_close(stream);
        RET(MMSYSERR_ERROR, 6);
    }
    if (ph) MEM32(ph) = wo_handle(w);
    RET(MMSYSERR_NOERROR, 6);
}

static void m_waveOutWrite(void) {             /* (hwo, lpWaveHdr, cbwh) */
    waveout_t *w = wo_of(A32(0));
    uint32_t wh = A32(1);
    if (!w || !wh) RET(MMSYSERR_INVALHANDLE, 3);
    uint32_t fl = MEM32(wh + 16);
    if (!MEM32(wh) || !(fl & WHDR_PREPARED)) RET(WAVERR_UNPREPARED, 3);
    if (fl & WHDR_INQUEUE) RET(WAVERR_STILLPLAYING, 3);
    uint32_t frames = MEM32(wh + 4) / (uint32_t)w->block;
    if (!frames || frames > WO_MAX_FRAMES) RET(MMSYSERR_INVALPARAM, 3);

    pthread_mutex_lock(&g_wo_lock);
    wo_update_played(w);
    if (w->nbufs >= MAX_WO_BUFS || w->pushed - w->played + frames > WO_MAX_FRAMES) {
        pthread_mutex_unlock(&g_wo_lock);
        RET(MMSYSERR_NODRIVER, 3);              /* never let the ring truncate a buffer */
    }
    wo_buf_t *b = &w->bufs[w->nbufs];
    memset(b, 0, sizeof *b);
    b->hdr = wh; b->data = MEM32(wh); b->frames = frames; b->start = (uint32_t)w->pushed;
    b->passes = 1;
    b->looping = (fl & (WHDR_BEGINLOOP | WHDR_ENDLOOP)) == (WHDR_BEGINLOOP | WHDR_ENDLOOP);
    b->loops = (int)MEM32(wh + 20);             /* dwLoops: repeats after the first pass */
    w->break_loop = 0;                          /* a fresh write may loop again */
    if (wo_push(w, b->data, frames) != 0) {
        pthread_mutex_unlock(&g_wo_lock);
        RET(MMSYSERR_NODRIVER, 3);
    }
    w->pushed += frames;
    w->nbufs++;
    MEM32(wh + 16) = (fl | WHDR_INQUEUE) & ~WHDR_DONE;
    pthread_mutex_unlock(&g_wo_lock);
    RET(MMSYSERR_NOERROR, 3);
}

static void m_waveOutPrepareHeader(void) {
    waveout_t *w = wo_of(A32(0));
    uint32_t wh = A32(1);
    if (!w) RET(MMSYSERR_INVALHANDLE, 3);
    if (!wh || A32(2) < 32) RET(MMSYSERR_INVALPARAM, 3);
    MEM32(wh + 16) = (MEM32(wh + 16) | WHDR_PREPARED) & ~(WHDR_DONE | WHDR_INQUEUE);
    RET(MMSYSERR_NOERROR, 3);
}
static void m_waveOutUnprepareHeader(void) {
    waveout_t *w = wo_of(A32(0));
    uint32_t wh = A32(1);
    if (!w) RET(MMSYSERR_INVALHANDLE, 3);
    if (!wh || A32(2) < 32) RET(MMSYSERR_INVALPARAM, 3);
    pthread_mutex_lock(&g_wo_lock);
    for (int i = 0; i < w->nbufs; i++)
        if (w->bufs[i].hdr == wh) { pthread_mutex_unlock(&g_wo_lock); RET(WAVERR_STILLPLAYING, 3); }
    MEM32(wh + 16) &= ~(WHDR_PREPARED | WHDR_DONE);
    pthread_mutex_unlock(&g_wo_lock);
    RET(MMSYSERR_NOERROR, 3);
}

/* Pause/Restart are real: pause mutes the stream and freezes WAVEHDR
 * completion; restart restores gain and lets the completion thread catch up.
 * Frames the mixer drains while paused are accounted for on restart, not
 * reported as a completion during the pause. */
static void m_waveOutPause(void) {
    waveout_t *w = wo_of(A32(0));
    if (!w) RET(MMSYSERR_INVALHANDLE, 1);
    if (!w->paused) { w->paused = 1; hle_audio_stream_gain(w->stream, 0.0f); }
    RET(MMSYSERR_NOERROR, 1);
}
static void m_waveOutRestart(void) {
    waveout_t *w = wo_of(A32(0));
    if (!w) RET(MMSYSERR_INVALHANDLE, 1);
    if (w->paused) { w->paused = 0; hle_audio_stream_gain(w->stream, wo_gain(w)); }
    RET(MMSYSERR_NOERROR, 1);
}
static void m_waveOutBreakLoop(void) {
    waveout_t *w = wo_of(A32(0));
    if (!w) RET(MMSYSERR_INVALHANDLE, 1);
    w->break_loop = 1;
    RET(MMSYSERR_NOERROR, 1);
}

static void m_waveOutReset(void) {
    waveout_t *w = wo_of(A32(0));
    if (!w) RET(MMSYSERR_INVALHANDLE, 1);
    wo_finish_all(w, 1);
    /* Reopen the stream so the ring contains no samples from the stopped run;
     * GetPosition then starts from zero, as a Windows reset does. */
    pthread_mutex_lock(&g_wo_lock);
    hle_audio_stream_close(w->stream);
    w->stream = hle_audio_stream_open(w->rate, w->channels);
    w->pushed = w->played = 0;
    w->paused = 0;
    if (w->stream >= 0) hle_audio_stream_gain(w->stream, wo_gain(w));
    pthread_mutex_unlock(&g_wo_lock);
    RET(w->stream >= 0 ? MMSYSERR_NOERROR : MMSYSERR_NODRIVER, 1);
}
static void m_waveOutClose(void) {
    waveout_t *w = wo_of(A32(0));
    if (!w) RET(MMSYSERR_INVALHANDLE, 1);
    pthread_mutex_lock(&g_wo_lock);
    int busy = w->nbufs > 0;
    pthread_mutex_unlock(&g_wo_lock);
    if (busy) RET(WAVERR_STILLPLAYING, 1);
    wo_finish_all(w, 0);
    w->closing = 1;
    if (!pthread_equal(w->thread, pthread_self())) {
        /* The completion thread may be inside a guest callback waiting for the
         * machine this shim holds. Give the machine up while joining. */
        hle_block_begin();
        pthread_join(w->thread, NULL);
        hle_block_end();
    }
    pthread_mutex_lock(&g_wo_lock);
    if (w->stream >= 0) hle_audio_stream_close(w->stream);
    memset(w, 0, sizeof *w);
    pthread_mutex_unlock(&g_wo_lock);
    RET(MMSYSERR_NOERROR, 1);
}
static void m_waveOutGetNumDevs(void)  { RET(1, 0); }          /* one device */
static void m_waveOutGetDevCapsA(void) {
    uint32_t c = A32(1);
    /* WAVEOUTCAPSA: wMid 0, wPid 2, vDriverVersion 4, szPname 8..39,
     * dwFormats 40, wChannels 44, wReserved1 46, dwSupport 48 (52 bytes). */
    if (c && A32(2) >= 52) {
        memset(hle_ptr(c), 0, 52);
        MEM16(c + 0) = 1;                                     /* MM_MICROSOFT */
        MEM16(c + 2) = 1;                                     /* wPid */
        MEM32(c + 4) = 0x00010000u;                           /* vDriverVersion 1.0 */
        snprintf((char *)hle_ptr(c + 8), 32, "SDL2 waveOut");
        MEM32(c + 40) = 0x00000F00u;                          /* 8/16-bit, 1/2 ch at 44.1 kHz */
        MEM16(c + 44) = 2;                                    /* wChannels */
        MEM32(c + 48) = 0x0004u;                              /* WAVECAPS_VOLUME */
    }
    RET(MMSYSERR_NOERROR, 3);
}

/* Position in audio time. Wine's WINMM_FramesToMMTime answers TIME_SAMPLES in
 * frames and coerces every other request to TIME_BYTES; MMTIME is 12 bytes on
 * x86 (UINT + 8-byte union). */
static void m_waveOutGetPosition(void) {
    waveout_t *w = wo_of(A32(0));
    uint32_t t = A32(1), size = A32(2);
    if (!w) RET(MMSYSERR_INVALHANDLE, 3);
    if (!t || !size) RET(MMSYSERR_INVALPARAM, 3);
    if (size < MMTIME_SIZE) RET(MMSYSERR_ERROR, 3);
    pthread_mutex_lock(&g_wo_lock);
    wo_update_played(w);
    uint64_t played = w->played;
    uint32_t bytes_per_sec = (uint32_t)(w->block * w->rate);
    pthread_mutex_unlock(&g_wo_lock);
    if (MEM32(t) == TIME_SAMPLES) {
        MEM32(t) = TIME_SAMPLES;
        MEM32(t + 4) = (uint32_t)played;
    } else {
        MEM32(t) = TIME_BYTES;
        MEM32(t + 4) = (uint32_t)(played * bytes_per_sec / (uint32_t)w->rate);
    }
    MEM32(t + 8) = 0;
    RET(MMSYSERR_NOERROR, 3);
}
static void m_waveOutSetVolume(void) {
    waveout_t *w = wo_of(A32(0));
    if (!w) RET(MMSYSERR_INVALHANDLE, 2);
    w->volume = A32(1);
    if (!w->paused) hle_audio_stream_gain(w->stream, wo_gain(w));
    RET(MMSYSERR_NOERROR, 2);
}
static void m_waveOutGetVolume(void) {
    waveout_t *w = wo_of(A32(0));
    if (!w) RET(MMSYSERR_INVALHANDLE, 2);
    if (A32(1)) MEM32(A32(1)) = w->volume;
    RET(MMSYSERR_NOERROR, 2);
}
static void m_waveOutGetID(void) {
    waveout_t *w = wo_of(A32(0));
    if (!w) RET(MMSYSERR_INVALHANDLE, 2);
    if (A32(1)) MEM32(A32(1)) = (uint32_t)(int)(w - g_wo);
    RET(MMSYSERR_NOERROR, 2);
}

/* --- no mixer, no recording device ---
 * What Windows answers on a PC without either: no devices, an open of device
 * 0 refused as a bad id, and anything given a handle told it is not one. A
 * program that sets its card's levels through the mixer, or enumerates
 * inputs for a record page, carries on without them. */
static void m_none0(void)       { RET(0, 0); }                        /* mixerGetNumDevs, waveInGetNumDevs */
static void m_mixerOpen(void)   { if (A32(0)) MEM32(A32(0)) = 0; RET(MMSYSERR_BADDEVICEID, 5); }
static void m_waveInOpen(void)  { if (A32(0)) MEM32(A32(0)) = 0; RET(MMSYSERR_BADDEVICEID, 6); }
static void m_badhandle1(void)  { RET(MMSYSERR_INVALHANDLE, 1); }     /* (h) */
static void m_badhandle3(void)  { RET(MMSYSERR_INVALHANDLE, 3); }     /* (h, struct, size or flags) */

/* --- mixer caps: no device, so no capabilities to report --- */
static void m_mixerGetDevCapsA(void)   { RET(MMSYSERR_BADDEVICEID, 3); }

/* --- MIDI mapper: no devices, and no pretend device either ---
 * On a machine with no MIDI hardware the mapper reports zero devices. Every
 * call that would need one fails with the documented MMSYSERR_BADDEVICEID,
 * including the handle-taking calls (with no device there can be no valid
 * handle). A caps query never fabricates capabilities: it zeroes at most the
 * real ANSI caps structure, bounded by the caller's cb, and returns the error.
 * The error-text helpers are the exception: they only format a code, so they
 * work without a device and return MMSYSERR_NOERROR.
 *
 * Exactly one diagnostic is printed, on the first failing MIDI call, naming
 * the API that got there first. A game that checks GetNumDevs and carries on
 * stays quiet; a game that assumes a device exists gets an explicit reason on
 * stderr rather than a fake success that would strand it later.
 *
 * No MIDI synthesis is attempted. Games that only want sound keep using the
 * waveOut/sndPlaySound paths above (dsound.c's SDL mixer); this subset opens
 * no SDL stream of its own and introduces no second audio backend.
 */
#define WINMM_MIDI_OUT_CAPS_A 52u   /* sizeof(MIDIOUTCAPSA) on x86 */
#define WINMM_MIDI_IN_CAPS_A  44u   /* sizeof(MIDIINCAPSA)  on x86 */
static int g_midi_warned;
static void midi_no_device(const char *api) {
    if (g_midi_warned) return;
    g_midi_warned = 1;
    fprintf(stderr, "[winmm] MIDI: no MIDI devices present; %s -> MMSYSERR_BADDEVICEID "
            "(MIDI is not emulated; PCM audio still works via waveOut/sndPlaySound)\n", api);
}
static void midi_zero_caps(void *caps, uint32_t cb, uint32_t real_size) {
    uint32_t n;
    if (!caps) return;
    n = cb < real_size ? cb : real_size;
    if (n) memset(caps, 0, n);
}
static const char *midi_err_text(uint32_t e) {
    switch (e) {
    case MMSYSERR_NOERROR:      return "No error";
    case MMSYSERR_ERROR:        return "Unspecified error";
    case MMSYSERR_BADDEVICEID:  return "Bad device ID";
    case MMSYSERR_INVALHANDLE:  return "Invalid handle";
    case MMSYSERR_NOTSUPPORTED: return "Function not supported";
    default:                    return "Unknown MIDI error";
    }
}
static uint32_t midi_get_error_text(void) {      /* (MMRESULT, LPSTR, UINT) */
    const char *s = midi_err_text(A32(0));
    uint32_t dst = A32(1), cb = A32(2);
    size_t n = strlen(s);
    char *out;
    if (!dst || !cb) return MMSYSERR_NOERROR;
    if (n >= cb) n = (size_t)cb - 1;
    out = (char *)APTR(1);
    memcpy(out, s, n);
    out[n] = 0;
    return MMSYSERR_NOERROR;
}

static void m_midiOutGetNumDevs(void) { RET(0, 0); }
static void m_midiInGetNumDevs(void)  { RET(0, 0); }
static void m_midiOutGetDevCapsA(void) {          /* (uDeviceID, LPMIDIOUTCAPSA, cb) */
    midi_no_device("midiOutGetDevCapsA");
    midi_zero_caps(APTR(1), A32(2), WINMM_MIDI_OUT_CAPS_A);
    RET(MMSYSERR_BADDEVICEID, 3);
}
static void m_midiInGetDevCapsA(void) {           /* (uDeviceID, LPMIDIINCAPSA, cb) */
    midi_no_device("midiInGetDevCapsA");
    midi_zero_caps(APTR(1), A32(2), WINMM_MIDI_IN_CAPS_A);
    RET(MMSYSERR_BADDEVICEID, 3);
}
static void m_midiOutOpen(void) {                 /* (lphmo, dev, callback, instance, flags) */
    midi_no_device("midiOutOpen");
    if (A32(0)) MEM32(A32(0)) = 0;                /* no handle on failure */
    RET(MMSYSERR_BADDEVICEID, 5);
}
static void m_midiInOpen(void) {                  /* (lphmi, dev, callback, instance, flags) */
    midi_no_device("midiInOpen");
    if (A32(0)) MEM32(A32(0)) = 0;
    RET(MMSYSERR_BADDEVICEID, 5);
}
static void m_midiOutClose(void)            { midi_no_device("midiOutClose"); RET(MMSYSERR_BADDEVICEID, 1); }
static void m_midiInClose(void)             { midi_no_device("midiInClose"); RET(MMSYSERR_BADDEVICEID, 1); }
static void m_midiOutShortMsg(void)         { midi_no_device("midiOutShortMsg"); RET(MMSYSERR_BADDEVICEID, 2); }
static void m_midiOutLongMsg(void)          { midi_no_device("midiOutLongMsg"); RET(MMSYSERR_BADDEVICEID, 3); }
static void m_midiOutReset(void)            { midi_no_device("midiOutReset"); RET(MMSYSERR_BADDEVICEID, 1); }
static void m_midiOutGetVolume(void)        { midi_no_device("midiOutGetVolume"); RET(MMSYSERR_BADDEVICEID, 2); }
static void m_midiOutSetVolume(void)        { midi_no_device("midiOutSetVolume"); RET(MMSYSERR_BADDEVICEID, 2); }
static void m_midiOutPrepareHeader(void)    { midi_no_device("midiOutPrepareHeader"); RET(MMSYSERR_BADDEVICEID, 3); }
static void m_midiOutUnprepareHeader(void)  { midi_no_device("midiOutUnprepareHeader"); RET(MMSYSERR_BADDEVICEID, 3); }
static void m_midiInPrepareHeader(void)     { midi_no_device("midiInPrepareHeader"); RET(MMSYSERR_BADDEVICEID, 3); }
static void m_midiInUnprepareHeader(void)   { midi_no_device("midiInUnprepareHeader"); RET(MMSYSERR_BADDEVICEID, 3); }
static void m_midiInAddBuffer(void)         { midi_no_device("midiInAddBuffer"); RET(MMSYSERR_BADDEVICEID, 3); }
static void m_midiInStart(void)             { midi_no_device("midiInStart"); RET(MMSYSERR_BADDEVICEID, 1); }
static void m_midiInStop(void)              { midi_no_device("midiInStop"); RET(MMSYSERR_BADDEVICEID, 1); }
static void m_midiInReset(void)             { midi_no_device("midiInReset"); RET(MMSYSERR_BADDEVICEID, 1); }
static void m_midiOutGetID(void)            { midi_no_device("midiOutGetID"); RET(MMSYSERR_BADDEVICEID, 2); }
static void m_midiInGetID(void)             { midi_no_device("midiInGetID"); RET(MMSYSERR_BADDEVICEID, 2); }
static void m_midiOutGetErrorTextA(void)    { RET(midi_get_error_text(), 3); }
static void m_midiInGetErrorTextA(void)     { RET(midi_get_error_text(), 3); }

/* --- mmio: RIFF files (WAVs) for reading ---
 * A file is read whole into memory at mmioOpenA, or a caller's buffer is
 * used as it stands (FOURCC_MEM). The direct-buffer calls (mmioGetInfo,
 * mmioAdvance, mmioSetInfo) then hand out that one buffer, which is the
 * whole file, so an advance never has to refill it. Writing is not done:
 * opening for write fails, as with a read-only disk. */
#define MMIOERR_CANNOTOPEN    257u
#define MMIOERR_CHUNKNOTFOUND 265u
#define FOURCC_MEM  0x204D454Du
#define FOURCC_RIFF 0x46464952u
#define FOURCC_LIST 0x5453494Cu
typedef struct { uint8_t *data; uint32_t len, pos; int owns; } hmmio;
static hmmio *MMIO(uint32_t h) { return (hmmio *)hle_handle_obj(h, HLE_H_MMIO); }
static void mmio_info(const hmmio *m, uint32_t info, uint32_t h) {   /* MMIOINFO over the whole file */
    MEM32(info + 20) = m->len, MEM32(info + 24) = hle_va(m->data);
    MEM32(info + 28) = hle_va(m->data) + m->pos, MEM32(info + 32) = hle_va(m->data) + m->len;
    MEM32(info + 36) = MEM32(info + 32), MEM32(info + 40) = 0, MEM32(info + 44) = m->len, MEM32(info + 68) = h;
}
static uint32_t mmio_open_named(const char *name, uint32_t info, uint32_t flags) {
    hmmio *m = (hmmio *)calloc(1, sizeof *m);
    if (info && MEM32(info + 4) == FOURCC_MEM && MEM32(info + 24)) {
        m->data = (uint8_t *)hle_ptr(MEM32(info + 24)), m->len = MEM32(info + 20);
    } else {
        char host[1024];
        FILE *f = !(flags & 0x1003u) && name && hle_host_path(name, host, sizeof host) ? fopen(host, "rb") : NULL;   /* not WRITE, READWRITE, CREATE */
        long n = -1;
        if (f && fseek(f, 0, SEEK_END) == 0 && (n = ftell(f)) >= 0) rewind(f);
        m->data = f && n >= 0 ? (uint8_t *)hle_alloc((uint32_t)n + 1) : NULL, m->len = (uint32_t)n, m->owns = 1;
        if (!m->data || fread(m->data, 1, (size_t)n, f) != (size_t)n) {
            if (f) fclose(f);
            hle_free(m->data), free(m);
            if (info) MEM32(info + 12) = MMIOERR_CANNOTOPEN;
            return 0;
        }
        fclose(f);
    }
    uint32_t h = hle_handle_alloc(HLE_H_MMIO, m);
    if (info) MEM32(info + 12) = 0;
    return h;
}
/* mmioOpenW: the wide-name form used by TrackMania. Convert through the
 * existing Win32 codepage helper; the file path handling is shared. */
static void m_mmioOpenW(void) {                          /* (nameW, MMIOINFO*, flags) */
    const uint16_t *w = (const uint16_t *)APTR(0);
    char name[1024];
    if (!w || !hle_wide_to_ansi(w, name, sizeof name)) {
        if (A32(1)) MEM32(A32(1) + 12) = MMIOERR_CANNOTOPEN;
        RET(0, 3);
    }
    uint32_t host_name = hle_va(hle_alloc((uint32_t)strlen(name) + 1));
    if (!host_name) { RET(0, 3); }
    memcpy(hle_ptr(host_name), name, strlen(name) + 1);
    RET(mmio_open_named((const char *)hle_ptr(host_name), A32(1), A32(2)), 3);
}
static void m_mmioOpenA(void) {
    RET(mmio_open_named(ASTR(0), A32(1), A32(2)), 3);
}
static void m_mmioClose(void) {
    hmmio *m = MMIO(A32(0));
    if (!m) RET(MMSYSERR_ERROR, 2);
    if (m->owns) hle_free(m->data);
    free(m);
    hle_handle_close(A32(0));
    RET(0, 2);
}
static void m_mmioRead(void) {                           /* (h, buf, n) */
    hmmio *m = MMIO(A32(0));
    if (!m) RET(0xFFFFFFFFu, 3);
    uint32_t n = A32(2) < m->len - m->pos ? A32(2) : m->len - m->pos;
    memcpy(APTR(1), m->data + m->pos, n);
    m->pos += n;
    RET(n, 3);
}
static void m_mmioWrite(void) { RET(0xFFFFFFFFu, 3); }
static void m_mmioSeek(void) {                           /* (h, offset, origin) */
    hmmio *m = MMIO(A32(0));
    int64_t to = (int32_t)A32(1) + (int64_t)(A32(2) == 1 ? m ? m->pos : 0 : A32(2) == 2 ? m ? m->len : 0 : 0);
    if (!m || to < 0 || to > m->len) RET(0xFFFFFFFFu, 3);
    m->pos = (uint32_t)to;
    RET(m->pos, 3);
}
static void m_mmioDescend(void) {                        /* (h, MMCKINFO*, parent, flags) */
    hmmio *m = MMIO(A32(0));
    uint32_t ck = A32(1), parent = A32(2), flags = A32(3);
    uint32_t end = parent ? MEM32(parent + 12) + MEM32(parent + 4) : m ? m->len : 0;
    if (!m) RET(MMSYSERR_ERROR, 4);
    if (end > m->len) end = m->len;
    for (;;) {
        if (m->pos + 8 > end) RET(MMIOERR_CHUNKNOTFOUND, 4);
        uint32_t id, size, type = 0;
        memcpy(&id, m->data + m->pos, 4), memcpy(&size, m->data + m->pos + 4, 4);
        uint32_t data = m->pos + 8;
        int list = id == FOURCC_RIFF || id == FOURCC_LIST;
        if (list && data + 4 <= end) memcpy(&type, m->data + data, 4);
        int found = flags & 0x10 ? id == MEM32(ck)                                     /* FINDCHUNK */
                  : flags & 0x20 ? id == FOURCC_RIFF && type == MEM32(ck + 8)          /* FINDRIFF */
                  : flags & 0x40 ? id == FOURCC_LIST && type == MEM32(ck + 8) : 1;     /* FINDLIST */
        if (found) {
            MEM32(ck) = id, MEM32(ck + 4) = size, MEM32(ck + 8) = type, MEM32(ck + 12) = data, MEM32(ck + 16) = 0;
            m->pos = data + (list ? 4 : 0);
            RET(0, 4);
        }
        m->pos = data + size + (size & 1);
    }
}
static void m_mmioAscend(void) {                         /* (h, MMCKINFO*, flags) */
    hmmio *m = MMIO(A32(0));
    uint32_t ck = A32(1), to = MEM32(ck + 12) + MEM32(ck + 4);
    if (!m) RET(MMSYSERR_ERROR, 3);
    to += to & 1;
    m->pos = to < m->len ? to : m->len;
    RET(0, 3);
}
static void m_mmioGetInfo(void) {                        /* (h, MMIOINFO*, flags) */
    hmmio *m = MMIO(A32(0));
    if (!m) RET(MMSYSERR_ERROR, 3);
    memset(APTR(1), 0, 72);
    mmio_info(m, A32(1), A32(0));
    RET(0, 3);
}
static void m_mmioSetInfo(void) {                        /* (h, MMIOINFO*, flags): where pchNext got to */
    hmmio *m = MMIO(A32(0));
    if (!m) RET(MMSYSERR_ERROR, 3);
    uint32_t next = MEM32(A32(1) + 28) - hle_va(m->data);
    m->pos = next < m->len ? next : m->len;
    RET(0, 3);
}
static void m_mmioAdvance(void) {                        /* (h, MMIOINFO*, flags) */
    hmmio *m = MMIO(A32(0));
    if (!m) RET(MMSYSERR_ERROR, 3);
    if (A32(1)) { uint32_t next = MEM32(A32(1) + 28) - hle_va(m->data); m->pos = next < m->len ? next : m->len; mmio_info(m, A32(1), A32(0)); }
    RET(0, 3);
}
static void m_mmioSetBuffer(void) { RET(0, 4); }        /* the buffer is the whole file already */

/* mmioStringToFOURCCA(str, flags): the first four characters, padded with
 * spaces; MMIO_TOUPPER (0x10) upper-cases them. */
static void m_mmioStringToFOURCCA(void) {
    const char *s = ASTR(0);
    uint32_t fcc = 0;
    for (int i = 0, end = 0; i < 4; i++) {
        char c = !end && s && s[i] ? s[i] : (end = 1, ' ');
        if ((A32(1) & 0x10) && c >= 'a' && c <= 'z') c = (char)(c - 32);
        fcc |= (uint32_t)(uint8_t)c << (8 * i);
    }
    RET(fcc, 2);
}

/* --- sndPlaySoundA/W: real RIFF/WAVE playback on the SDL audio mixer ---
 *
 * Guest ABI: Win32 sndPlaySound returns BOOL, so these shims return exactly
 * TRUE (1) when the sound really was started (or stopped, for a NULL name) and
 * FALSE (0) when it was not. A guest that tests truthiness gets the Windows
 * answer; a missing file, a non-PCM WAV, an unsupported flag or a missing audio
 * device is never reported as success.
 *
 * Internally ps_play() keeps the precise MMSYSERR_* reason, which is logged at
 * the BOOL boundary: no failure detail is lost, it is just not leaked into a
 * BOOL return. (The other winmm APIs below really do return MMSYSERR_*.)
 *
 * Playback uses dsound.c's hle_audio_stream_* (the SDL2 audio device and its
 * software mixer), the same path bink.c uses for movie sound. Decoding covers
 * the RIFF/WAVE formats sndPlaySound handles itself (PCM 8/16-bit, mono or
 * stereo); anything else (SND_ALIAS, SND_RESOURCE, compressed WAV, ...) is
 * reported as unsupported rather than played as silence. No system default
 * sound is fabricated when a name cannot be resolved, with or without
 * SND_NODEFAULT.
 */
#define SND_SYNC        0x00000000u
#define SND_ASYNC       0x00000001u
#define SND_NODEFAULT   0x00000002u
#define SND_MEMORY      0x00000004u
#define SND_LOOP        0x00000008u
#define SND_NOSTOP      0x00000010u
#define SND_PURGE       0x00000040u
#define SND_NOWAIT      0x00002000u
#define SND_FILENAME    0x00020000u
/* Flags this shim implements; all others (notably SND_ALIAS, SND_ALIAS_ID,
 * SND_RESOURCE, SND_SYSTEM, SND_APPLICATION) are honest errors. */
#define PS_OK_FLAGS (SND_ASYNC | SND_NODEFAULT | SND_MEMORY | SND_LOOP | \
                     SND_NOSTOP | SND_NOWAIT | SND_PURGE | SND_FILENAME)

/* MMSYSERR_* used by ps_play are defined with the rest at the top of this file. */

enum { PS_MAX_VOICES = 4, PS_CHUNK = 2048, PS_HIGH_WATER = 32768, PS_SPIN_MAX = 30000,
       PS_MAX_WAV = 64 * 1024 * 1024 };

typedef struct {
    int used;
    int stream;                 /* hle_audio_stream_open id */
    int16_t *pcm;               /* host memory, interleaved frames */
    uint32_t frames;
    int channels;
    int loop;
    volatile int stop;
    pthread_t thread;
} ps_voice;
static pthread_mutex_t g_ps_mtx = PTHREAD_MUTEX_INITIALIZER;
static ps_voice g_ps_voice[PS_MAX_VOICES];

static uint32_t ps_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* Feed the stream in chunks, waiting for room; a loop replays until stopped.
 * Called on the guest thread for SND_SYNC and on a voice thread for ASYNC. */
static void ps_feed(ps_voice *v) {
    int stall = 0;
    for (;;) {
        uint32_t off = 0;
        while (off < v->frames && !v->stop) {
            if (hle_audio_stream_queued(v->stream) > PS_HIGH_WATER) {
                if (++stall > PS_SPIN_MAX) return;           /* stuck device: stop instead of hanging */
                usleep(2000);
                continue;
            }
            stall = 0;
            uint32_t n = v->frames - off;
            if (n > PS_CHUNK) n = PS_CHUNK;
            hle_audio_stream_push(v->stream, v->pcm + (size_t)off * (size_t)v->channels, (int)n);
            off += n;
        }
        if (v->stop) return;
        /* Let the last chunk drain: a non-looping sound ends here. */
        int spins = 0;
        while (hle_audio_stream_queued(v->stream) > 0 && !v->stop && spins++ < PS_SPIN_MAX) usleep(1000);
        if (!v->loop || v->stop) return;
    }
}

static void *ps_voice_thread(void *arg) {
    ps_voice *v = (ps_voice *)arg;
    ps_feed(v);
    hle_audio_stream_close(v->stream);
    free(v->pcm);
    pthread_mutex_lock(&g_ps_mtx);
    v->used = 0;
    v->stream = -1;
    v->pcm = NULL;
    pthread_mutex_unlock(&g_ps_mtx);
    return NULL;
}

/* Stop every asynchronous sound. The voice threads close their own streams,
 * so joining here leaves no stream or PCM behind. */
static void ps_stop_all(void) {
    pthread_t join[PS_MAX_VOICES];
    int n = 0;
    pthread_mutex_lock(&g_ps_mtx);
    for (int i = 0; i < PS_MAX_VOICES; i++)
        if (g_ps_voice[i].used) {
            g_ps_voice[i].stop = 1;
            if (!pthread_equal(g_ps_voice[i].thread, pthread_self())) join[n++] = g_ps_voice[i].thread;
        }
    pthread_mutex_unlock(&g_ps_mtx);
    for (int i = 0; i < n; i++) pthread_join(join[i], NULL);
}

/* Decode a RIFF/WAVE image into int16 frames for the mixer. */
static uint32_t ps_decode(const uint8_t *data, size_t len, int16_t **out_pcm,
                          uint32_t *out_frames, int *out_channels, int *out_rate) {
    if (len < 12 || memcmp(data, "RIFF", 4) || memcmp(data + 8, "WAVE", 4)) {
        fprintf(stderr, "[winmm] sndPlaySound: not a RIFF/WAVE image\n");
        return MMSYSERR_ERROR;
    }
    const uint8_t *fmt = NULL, *pcm = NULL;
    size_t fmt_len = 0, pcm_len = 0;
    for (size_t p = 12; p + 8 <= len; ) {
        uint32_t sz = ps_le32(data + p + 4);
        if (sz > len - p - 8) sz = (uint32_t)(len - p - 8);
        if (!memcmp(data + p, "fmt ", 4)) { fmt = data + p + 8; fmt_len = sz; }
        else if (!memcmp(data + p, "data", 4)) { pcm = data + p + 8; pcm_len = sz; }
        p += 8 + sz + (sz & 1);
    }
    if (!fmt || fmt_len < 16 || !pcm) {
        fprintf(stderr, "[winmm] sndPlaySound: WAVE has no fmt/data chunk\n");
        return MMSYSERR_ERROR;
    }
    unsigned tag = (unsigned)(fmt[0] | (fmt[1] << 8));
    int ch = fmt[2] | (fmt[3] << 8);
    int rate = (int)ps_le32(fmt + 4);
    int bits = fmt[14] | (fmt[15] << 8);
    if (tag != 1 || (bits != 8 && bits != 16) || (ch != 1 && ch != 2) || rate <= 0) {
        fprintf(stderr, "[winmm] sndPlaySound: unsupported WAVE (tag %u, %d-bit, %d ch, %d Hz)\n",
                tag, bits, ch, rate);
        return MMSYSERR_NOTSUPPORTED;
    }
    size_t frame_bytes = (size_t)ch * (size_t)(bits / 8);
    size_t frames = pcm_len / frame_bytes;
    if (!frames) { fprintf(stderr, "[winmm] sndPlaySound: WAVE has no samples\n"); return MMSYSERR_ERROR; }
    int16_t *out = (int16_t *)malloc(frames * (size_t)ch * sizeof(int16_t));
    if (!out) return MMSYSERR_NOMEM;
    for (size_t i = 0; i < frames * (size_t)ch; i++)
        out[i] = bits == 16 ? (int16_t)(pcm[i * 2] | (pcm[i * 2 + 1] << 8))
                            : (int16_t)(((int)pcm[i] - 128) * 256);
    *out_pcm = out;
    *out_frames = (uint32_t)frames;
    *out_channels = ch;
    *out_rate = rate;
    return MMSYSERR_NOERROR;
}

static uint32_t ps_play(uint32_t name_va, uint32_t flags) {
    if (flags & ~(uint32_t)PS_OK_FLAGS) {
        fprintf(stderr, "[winmm] sndPlaySound: unsupported flags 0x%08x\n", flags);
        return MMSYSERR_INVALFLAG;
    }
    if (!name_va || (flags & SND_PURGE)) { ps_stop_all(); return MMSYSERR_NOERROR; }
    if (flags & SND_NOSTOP) {
        pthread_mutex_lock(&g_ps_mtx);
        int busy = 0;
        for (int i = 0; i < PS_MAX_VOICES; i++) if (g_ps_voice[i].used && !g_ps_voice[i].stop) busy = 1;
        pthread_mutex_unlock(&g_ps_mtx);
        if (busy) { fprintf(stderr, "[winmm] sndPlaySound: sound already playing (SND_NOSTOP)\n"); return MMSYSERR_ALLOCATED; }
    }

    uint8_t *file_buf = NULL;
    const uint8_t *data;
    size_t len;
    if (flags & SND_MEMORY) {
        data = (const uint8_t *)hle_ptr(name_va);
        if (!data || memcmp(data, "RIFF", 4)) {
            fprintf(stderr, "[winmm] sndPlaySound: SND_MEMORY image is not RIFF\n");
            return MMSYSERR_ERROR;
        }
        len = 8u + (size_t)ps_le32(data + 4);
        if (len < 12 || len > PS_MAX_WAV) {
            fprintf(stderr, "[winmm] sndPlaySound: bad in-memory WAVE size %zu\n", len);
            return MMSYSERR_ERROR;
        }
    } else {
        const char *guest = (const char *)hle_ptr(name_va);
        char host[1024];
        if (!hle_host_path(guest, host, sizeof host) || !*host) {
            fprintf(stderr, "[winmm] sndPlaySound: file not found: %s\n", guest ? guest : "(null)");
            return MMSYSERR_ERROR;
        }
        FILE *f = fopen(host, "rb");
        if (!f) {
            fprintf(stderr, "[winmm] sndPlaySound: cannot open %s (%s)\n", guest, host);
            return MMSYSERR_ERROR;
        }
        if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return MMSYSERR_ERROR; }
        long n = ftell(f);
        if (n < 12 || n > PS_MAX_WAV) { fclose(f); return MMSYSERR_ERROR; }
        rewind(f);
        file_buf = (uint8_t *)malloc((size_t)n);
        if (!file_buf || fread(file_buf, 1, (size_t)n, f) != (size_t)n) {
            free(file_buf); fclose(f); return MMSYSERR_ERROR;
        }
        fclose(f);
        fprintf(stderr, "[winmm] sndPlaySound: %s -> %s\n", guest, host);
        data = file_buf;
        len = (size_t)n;
    }

    int16_t *pcm = NULL;
    uint32_t frames = 0;
    int ch = 0, rate = 0;
    uint32_t err = ps_decode(data, len, &pcm, &frames, &ch, &rate);
    free(file_buf);
    if (err != MMSYSERR_NOERROR) return err;

    int stream = hle_audio_stream_open(rate, ch);
    if (stream < 0) {
        fprintf(stderr, "[winmm] sndPlaySound: no audio stream for %d Hz, %d ch\n", rate, ch);
        free(pcm);
        return MMSYSERR_NODRIVER;
    }

    if (!(flags & SND_ASYNC)) {
        ps_voice v;
        memset(&v, 0, sizeof v);
        v.stream = stream; v.pcm = pcm; v.frames = frames; v.channels = ch;
        ps_feed(&v);
        hle_audio_stream_close(stream);
        free(pcm);
        return MMSYSERR_NOERROR;
    }

    pthread_mutex_lock(&g_ps_mtx);
    int slot = -1;
    for (int i = 0; i < PS_MAX_VOICES; i++) if (!g_ps_voice[i].used) { slot = i; break; }
    if (slot < 0) {
        pthread_mutex_unlock(&g_ps_mtx);
        hle_audio_stream_close(stream);
        free(pcm);
        return MMSYSERR_ALLOCATED;
    }
    ps_voice *v = &g_ps_voice[slot];
    v->used = 1; v->stream = stream; v->pcm = pcm; v->frames = frames;
    v->channels = ch; v->loop = (flags & SND_LOOP) != 0; v->stop = 0;
    if (pthread_create(&v->thread, NULL, ps_voice_thread, v) != 0) {
        memset(v, 0, sizeof *v);
        pthread_mutex_unlock(&g_ps_mtx);
        hle_audio_stream_close(stream);
        free(pcm);
        return MMSYSERR_ERROR;
    }
    pthread_mutex_unlock(&g_ps_mtx);
    return MMSYSERR_NOERROR;
}

/* Win32's BOOL, not an MMSYSERR code, is what the guest gets. The internal
 * classification from ps_play() is logged here so every failure keeps its
 * reason; only the success/failure bit crosses the ABI. */
static const char *ps_err_name(uint32_t err) {
    switch (err) {
    case MMSYSERR_NOERROR:    return "NOERROR";
    case MMSYSERR_ERROR:      return "ERROR";
    case MMSYSERR_ALLOCATED:  return "ALLOCATED";
    case MMSYSERR_NODRIVER:   return "NODRIVER";
    case MMSYSERR_NOMEM:      return "NOMEM";
    case MMSYSERR_NOTSUPPORTED: return "NOTSUPPORTED";
    case MMSYSERR_INVALFLAG:  return "INVALFLAG";
    case MMSYSERR_INVALPARAM: return "INVALPARAM";
    default:                  return "UNKNOWN";
    }
}
static uint32_t ps_bool(uint32_t err) {
    if (err == MMSYSERR_NOERROR) return 1u;          /* TRUE */
    fprintf(stderr, "[winmm] sndPlaySound: FAILED (MMSYSERR_%s = %u) -> FALSE\n",
            ps_err_name(err), err);
    return 0u;                                       /* FALSE */
}

static void m_sndPlaySoundA(void) { RET(ps_bool(ps_play(A32(0), A32(1))), 2); }
static void m_sndPlaySoundW(void) {
    uint32_t flags = A32(1);
    if (flags & SND_MEMORY) { RET(ps_bool(ps_play(A32(0), flags)), 2); }  /* the bytes are not a string */
    const uint16_t *w = (const uint16_t *)APTR(0);
    char name[1024];
    if (!w || !hle_wide_to_ansi(w, name, sizeof name)) {
        fprintf(stderr, "[winmm] sndPlaySoundW: bad wide name\n");
        RET(ps_bool(MMSYSERR_INVALPARAM), 2);
    }
    uint32_t mark = hle_scratch_mark();
    uint32_t dst = hle_scratch_copy(name, (uint32_t)strlen(name) + 1);
    uint32_t err = dst ? ps_play(dst, flags) : MMSYSERR_NOMEM;
    hle_scratch_release(mark);
    RET(ps_bool(err), 2);
}

/* --- MCI: a small, real waveaudio command-string subset ---
 *
 * Games drive music with mciSendString("open song.wav type waveaudio alias
 * music") followed by "play" / "stop" / "status". Answering every command with
 * MCIERR_UNSUPPORTED_FUNCTION strands those games even though this shim already
 * owns real PCM playback: sndPlaySound and waveOut push frames into dsound.c's
 * one SDL mixer stream. This subset loads a RIFF/WAVE with the same ps_decode,
 * keeps the decoded frames in host memory, and plays them through a dedicated
 * hle_audio_stream_* stream owned by the device -- the same audio device, not a
 * second backend. Commands outside the subset get their documented MCI error;
 * nothing is pretended.
 *
 *   open <file> [type waveaudio] [alias <name>]   (returns the device id)
 *   close|play|stop|pause|resume <device>
 *   status <device> mode|length|position|volume
 *   seek <device> to <milliseconds>
 *   set <device> volume <0..1000>
 *
 * A device is named by its alias or decimal id; a file opened without an alias
 * is named by its path, as on Windows. Guest strings and the return buffer are
 * bounded: a command longer than MCI_CMD_MAX is MCIERR_PARAM_OVERFLOW, and the
 * reply is NUL-terminated within cchReturn. mciGetErrorStringA formats MCI
 * error codes without needing a device; the message form (mciSendCommandA) is
 * still unsupported.
 */
#define MCIERR_NO_ERROR             0u
#define MCIERR_INVALID_DEVICE_ID    (256u + 1u)
#define MCIERR_UNRECOGNIZED_KEYWORD (256u + 3u)
#define MCIERR_UNRECOGNIZED_COMMAND (256u + 5u)
#define MCIERR_HARDWARE             (256u + 6u)
#define MCIERR_OUT_OF_MEMORY        (256u + 8u)
#define MCIERR_PARAM_OVERFLOW       (256u + 12u)
#define MCIERR_MISSING_STRING_ARG   (256u + 13u)
#define MCIERR_BAD_INTEGER          (256u + 14u)
/* MCIERR_UNSUPPORTED_FUNCTION (256u + 18u) is defined with the codes at top. */
#define MCIERR_FILE_NOT_FOUND       (256u + 19u)
#define MCIERR_OUTOFRANGE           (256u + 26u)
#define MCIERR_DUPLICATE_ALIAS      (256u + 33u)
#define MCIERR_INVALID_FILE         (256u + 40u)
#define MCI_CMD_MAX     1024u
#define MCI_DEV_MAX     8
#define MCI_ALIAS_MAX   32u
enum { MCI_ST_STOPPED = 0, MCI_ST_PLAYING, MCI_ST_PAUSED };

typedef struct {
    int used, thread_started, stream;
    uint32_t id;
    char alias[MCI_ALIAS_MAX];
    int16_t *pcm;                       /* host memory, interleaved s16 */
    uint32_t frames, length_ms, pushed, pos_frames, start_frame;
    int channels, rate, state;
    volatile int stop, pause;
    float volume;
    pthread_t thread;
} mci_dev_t;
static mci_dev_t g_mci[MCI_DEV_MAX];
static pthread_mutex_t g_mci_lock = PTHREAD_MUTEX_INITIALIZER;

/* Copy a guest string into a bounded host buffer. Returns 0 if it is not NUL
 * terminated inside cap (the caller reports MCIERR_PARAM_OVERFLOW). */
static int mci_copy_cmd(const char *guest, char *out, size_t cap) {
    if (!guest || !cap) return 0;
    for (size_t i = 0; i + 1 < cap; i++) {
        char c = guest[i];
        out[i] = c;
        if (!c) return 1;
    }
    out[cap - 1] = 0;
    return 0;
}
static const char *mci_skip(const char *p) {
    while (*p == ' ' || *p == '\t') p++;
    return p;
}
/* One whitespace- or quote-delimited token, bounded by cap. */
static const char *mci_word(const char *p, char *out, size_t cap) {
    size_t n = 0;
    p = mci_skip(p);
    if (*p == '"') {
        for (p++; *p && *p != '"'; p++) if (n + 1 < cap) out[n++] = *p;
        if (*p == '"') p++;
    } else {
        for (; *p && *p != ' ' && *p != '\t'; p++) if (n + 1 < cap) out[n++] = *p;
    }
    out[n] = 0;
    return p;
}
/* Bounded, always NUL-terminated reply in the guest's return buffer. */
static void mci_put(uint32_t dst, uint32_t cb, const char *s) {
    char *out;
    size_t n;
    if (!dst || !cb || !s) return;
    out = (char *)hle_ptr(dst);
    if (!out) return;
    n = strlen(s);
    if (n >= cb) n = cb - 1;
    memcpy(out, s, n);
    out[n] = 0;
}
static void mci_put_num(uint32_t dst, uint32_t cb, uint32_t v) {
    char text[24];
    snprintf(text, sizeof text, "%u", v);
    mci_put(dst, cb, text);
}
static mci_dev_t *mci_find(const char *name) {
    char *end;
    unsigned long id;
    if (!name || !*name) return NULL;
    id = strtoul(name, &end, 10);
    if (!*end && id >= 1 && id <= MCI_DEV_MAX)
        return g_mci[id - 1].used ? &g_mci[id - 1] : NULL;
    for (int i = 0; i < MCI_DEV_MAX; i++)
        if (g_mci[i].used && !strcasecmp(g_mci[i].alias, name)) return &g_mci[i];
    return NULL;
}
/* Decode a guest-named WAV through the sndPlaySound decoder and size bounds. */
static uint32_t mci_load(const char *guest, int16_t **pcm, uint32_t *frames,
                         int *channels, int *rate) {
    char host[1024];
    FILE *f;
    long n;
    uint8_t *buf;
    uint32_t err;
    if (!hle_host_path(guest, host, sizeof host) || !(f = fopen(host, "rb"))) {
        fprintf(stderr, "[winmm] MCI: file not found: %s\n", guest ? guest : "(null)");
        return MCIERR_FILE_NOT_FOUND;
    }
    if (fseek(f, 0, SEEK_END) != 0 || (n = ftell(f)) < 12 || n > PS_MAX_WAV) {
        fclose(f);
        return MCIERR_INVALID_FILE;
    }
    rewind(f);
    buf = (uint8_t *)malloc((size_t)n);
    if (!buf || fread(buf, 1, (size_t)n, f) != (size_t)n) {
        free(buf); fclose(f);
        return MCIERR_OUT_OF_MEMORY;
    }
    fclose(f);
    err = ps_decode(buf, (size_t)n, pcm, frames, channels, rate);
    free(buf);
    if (err != MMSYSERR_NOERROR) return MCIERR_INVALID_FILE;
    return MCIERR_NO_ERROR;
}

/* Frames the mixer has consumed. A stopped device keeps the value captured at
 * its stop; a live one derives it from frames submitted minus queue depth. */
static uint32_t mci_position(const mci_dev_t *d) {
    uint32_t played;
    int q;
    if (d->state == MCI_ST_STOPPED) return d->pos_frames;
    q = d->stream >= 0 ? hle_audio_stream_queued(d->stream) : 0;
    if (q < 0) q = 0;
    played = d->pushed >= (uint32_t)q ? d->pushed - (uint32_t)q : 0;
    return played < d->frames ? played : d->frames;
}
/* Stop a device without taking the machine: the feed thread only touches the
 * audio stream, never lifted code. Join before closing so it cannot write to a
 * freed stream. */
static void mci_halt(mci_dev_t *d) {
    uint32_t q = d->stream >= 0 ? hle_audio_stream_queued(d->stream) : 0;
    uint32_t played;
    if (q < 0) q = 0;
    played = d->pushed >= (uint32_t)q ? d->pushed - (uint32_t)q : 0;
    d->pos_frames = played < d->frames ? played : d->frames;
    if (d->stream >= 0) hle_audio_stream_gain(d->stream, 0.0f);
    d->stop = 1;
    d->pause = 0;
    if (d->thread_started && !pthread_equal(d->thread, pthread_self()))
        pthread_join(d->thread, NULL);
    if (d->stream >= 0) { hle_audio_stream_close(d->stream); d->stream = -1; }
    d->thread_started = 0;
    d->state = MCI_ST_STOPPED;
}

/* The device's feed thread: same chunking and high-water wait as sndPlaySound,
 * with pause holding the feed and a stop flag for close/seek/stop. */
static void *mci_thread(void *arg) {
    mci_dev_t *d = (mci_dev_t *)arg;
    uint32_t off = d->start_frame;
    int stall = 0;
    while (!d->stop && off < d->frames) {
        uint32_t n;
        if (d->pause) { usleep(2000); continue; }
        if (hle_audio_stream_queued(d->stream) > PS_HIGH_WATER) {
            if (++stall > PS_SPIN_MAX) break;            /* stuck device: stop, never hang */
            usleep(2000);
            continue;
        }
        stall = 0;
        n = d->frames - off;
        if (n > PS_CHUNK) n = PS_CHUNK;
        hle_audio_stream_push(d->stream, d->pcm + (size_t)off * (size_t)d->channels, (int)n);
        off += n;
        d->pushed = off;
    }
    for (int spins = 0; !d->stop && hle_audio_stream_queued(d->stream) > 0 &&
                        spins < PS_SPIN_MAX; spins++) usleep(1000);
    if (!d->stop) {
        pthread_mutex_lock(&g_mci_lock);
        if (d->used) {
            d->state = MCI_ST_STOPPED;
            d->pos_frames = d->frames;
            /* Release the stream at end of media, or repeated plays would
             * exhaust dsound.c's four host streams. */
            if (d->stream >= 0) { hle_audio_stream_close(d->stream); d->stream = -1; }
        }
        pthread_mutex_unlock(&g_mci_lock);
    }
    return NULL;
}
static uint32_t mci_start(mci_dev_t *d, uint32_t from) {
    int stream;
    if (d->thread_started) mci_halt(d);
    stream = hle_audio_stream_open(d->rate, d->channels);
    if (stream < 0) return MCIERR_HARDWARE;
    pthread_mutex_lock(&g_mci_lock);
    d->stream = stream;
    d->start_frame = from; d->pushed = from; d->pos_frames = from;
    d->stop = 0; d->pause = 0; d->state = MCI_ST_PLAYING;
    pthread_mutex_unlock(&g_mci_lock);
    hle_audio_stream_gain(stream, d->volume);
    if (pthread_create(&d->thread, NULL, mci_thread, d) != 0) {
        hle_audio_stream_close(stream);
        pthread_mutex_lock(&g_mci_lock);
        d->stream = -1; d->state = MCI_ST_STOPPED;
        pthread_mutex_unlock(&g_mci_lock);
        return MCIERR_HARDWARE;
    }
    d->thread_started = 1;
    return MCIERR_NO_ERROR;
}

static uint32_t mci_send(uint32_t cmd_va, uint32_t ret_va, uint32_t cb, uint32_t hwnd) {
    char cmd[MCI_CMD_MAX], verb[32], name[512], arg[32];
    const char *p;
    mci_dev_t *d;
    (void)hwnd;                          /* MM_MCINOTIFY needs no window here */
    if (!mci_copy_cmd((const char *)hle_ptr(cmd_va), cmd, sizeof cmd))
        return MCIERR_PARAM_OVERFLOW;
    p = mci_word(cmd, verb, sizeof verb);
    if (!*verb) return MCIERR_MISSING_STRING_ARG;
    p = mci_word(p, name, sizeof name);
    /* An unknown verb is an unknown command even if the device name after it
     * is not open yet: report the command error, not a misleading device id. */
    {
        static const char *const verbs[] = {
            "open", "close", "play", "stop", "pause", "resume", "status", "seek", "set"
        };
        int known = 0;
        for (unsigned i = 0; i < sizeof verbs / sizeof verbs[0]; i++)
            if (!strcasecmp(verb, verbs[i])) { known = 1; break; }
        if (!known) return MCIERR_UNRECOGNIZED_COMMAND;
    }

    if (!strcasecmp(verb, "open")) {
        char type[32] = "waveaudio", alias[512] = "";
        int16_t *pcm = NULL;
        uint32_t frames = 0;
        int ch = 0, rate = 0, slot = -1, have_alias = 0, err;
        if (!*name) return MCIERR_FILE_NOT_FOUND;
        for (;;) {
            p = mci_word(p, arg, sizeof arg);
            if (!*arg) break;
            if (!strcasecmp(arg, "type")) {
                p = mci_word(p, type, sizeof type);
                if (!*type) return MCIERR_MISSING_STRING_ARG;
            } else if (!strcasecmp(arg, "alias")) {
                p = mci_word(p, alias, sizeof alias);
                if (!*alias) return MCIERR_MISSING_STRING_ARG;
                have_alias = 1;
            } else return MCIERR_UNRECOGNIZED_KEYWORD;
        }
        if (strcasecmp(type, "waveaudio")) return MCIERR_UNSUPPORTED_FUNCTION;
        if (strlen(alias) >= MCI_ALIAS_MAX) return MCIERR_PARAM_OVERFLOW;
        if (have_alias && mci_find(alias)) return MCIERR_DUPLICATE_ALIAS;
        err = mci_load(name, &pcm, &frames, &ch, &rate);
        if (err) return err;
        pthread_mutex_lock(&g_mci_lock);
        for (int i = 0; i < MCI_DEV_MAX; i++) if (!g_mci[i].used) { slot = i; break; }
        if (slot >= 0) {
            d = &g_mci[slot];
            memset(d, 0, sizeof *d);
            d->used = 1; d->id = (uint32_t)slot + 1; d->stream = -1;
            d->pcm = pcm; d->frames = frames; d->channels = ch; d->rate = rate;
            d->length_ms = (uint32_t)((uint64_t)frames * 1000u / (uint32_t)rate);
            d->state = MCI_ST_STOPPED; d->volume = 1.0f;
            snprintf(d->alias, sizeof d->alias, "%s", have_alias ? alias : name);
        }
        pthread_mutex_unlock(&g_mci_lock);
        if (slot < 0) { free(pcm); return MCIERR_HARDWARE; }
        mci_put_num(ret_va, cb, g_mci[slot].id);        /* Windows returns the id */
        return MCIERR_NO_ERROR;
    }

    d = mci_find(name);
    if (!d) return MCIERR_INVALID_DEVICE_ID;

    if (!strcasecmp(verb, "close")) {
        if (p && *mci_skip(p)) return MCIERR_UNRECOGNIZED_KEYWORD;
        if (d->thread_started) mci_halt(d);
        pthread_mutex_lock(&g_mci_lock);
        free(d->pcm);
        memset(d, 0, sizeof *d);
        pthread_mutex_unlock(&g_mci_lock);
        return MCIERR_NO_ERROR;
    }
    if (!strcasecmp(verb, "play")) {
        if (p && *mci_skip(p)) return MCIERR_UNRECOGNIZED_KEYWORD;
        return mci_start(d, 0);
    }
    if (!strcasecmp(verb, "stop")) {
        if (p && *mci_skip(p)) return MCIERR_UNRECOGNIZED_KEYWORD;
        if (d->thread_started) mci_halt(d);
        return MCIERR_NO_ERROR;
    }
    if (!strcasecmp(verb, "pause")) {
        if (p && *mci_skip(p)) return MCIERR_UNRECOGNIZED_KEYWORD;
        d->pause = 1;
        if (d->stream >= 0) hle_audio_stream_gain(d->stream, 0.0f);
        if (d->state == MCI_ST_PLAYING) d->state = MCI_ST_PAUSED;
        return MCIERR_NO_ERROR;
    }
    if (!strcasecmp(verb, "resume")) {
        if (p && *mci_skip(p)) return MCIERR_UNRECOGNIZED_KEYWORD;
        d->pause = 0;
        if (d->stream >= 0 && d->state == MCI_ST_PAUSED)
            hle_audio_stream_gain(d->stream, d->volume);
        if (d->state == MCI_ST_PAUSED) d->state = MCI_ST_PLAYING;
        return MCIERR_NO_ERROR;
    }
    if (!strcasecmp(verb, "status")) {
        p = mci_word(p, arg, sizeof arg);
        if (!*arg) return MCIERR_MISSING_STRING_ARG;
        if (!strcasecmp(arg, "mode")) {
            mci_put(ret_va, cb, d->state == MCI_ST_PLAYING ? "playing" :
                               d->state == MCI_ST_PAUSED ? "paused" : "stopped");
            return MCIERR_NO_ERROR;
        }
        if (!strcasecmp(arg, "length")) {
            mci_put_num(ret_va, cb, d->length_ms);
            return MCIERR_NO_ERROR;
        }
        if (!strcasecmp(arg, "position")) {
            mci_put_num(ret_va, cb, (uint32_t)((uint64_t)mci_position(d) * 1000u / (uint32_t)d->rate));
            return MCIERR_NO_ERROR;
        }
        if (!strcasecmp(arg, "volume")) {
            mci_put_num(ret_va, cb, (uint32_t)(d->volume * 1000.0f));
            return MCIERR_NO_ERROR;
        }
        return MCIERR_UNRECOGNIZED_KEYWORD;
    }
    if (!strcasecmp(verb, "seek")) {
        char value[32];
        char *end;
        unsigned long ms;
        uint32_t frame;
        int was_playing = d->state == MCI_ST_PLAYING || d->state == MCI_ST_PAUSED;
        p = mci_word(p, arg, sizeof arg);
        if (strcasecmp(arg, "to")) return MCIERR_UNRECOGNIZED_KEYWORD;
        p = mci_word(p, value, sizeof value);
        ms = strtoul(value, &end, 10);
        if (!*value || *end) return MCIERR_BAD_INTEGER;
        frame = (uint32_t)((uint64_t)ms * (uint32_t)d->rate / 1000u);
        if (frame > d->frames) frame = d->frames;
        if (d->thread_started) mci_halt(d);
        d->start_frame = frame; d->pushed = frame; d->pos_frames = frame;
        return was_playing ? mci_start(d, frame) : MCIERR_NO_ERROR;
    }
    if (!strcasecmp(verb, "set")) {
        char value[32];
        char *end;
        unsigned long v;
        p = mci_word(p, arg, sizeof arg);
        if (strcasecmp(arg, "volume")) return MCIERR_UNRECOGNIZED_KEYWORD;
        p = mci_word(p, value, sizeof value);
        v = strtoul(value, &end, 10);
        if (!*value || *end) return MCIERR_BAD_INTEGER;
        if (v > 1000) return MCIERR_OUTOFRANGE;
        d->volume = (float)v / 1000.0f;
        if (d->stream >= 0 && d->state != MCI_ST_PAUSED)
            hle_audio_stream_gain(d->stream, d->volume);
        return MCIERR_NO_ERROR;
    }
    return MCIERR_UNRECOGNIZED_COMMAND;
}

static const char *mci_err_text(uint32_t e) {
    switch (e) {
    case MCIERR_NO_ERROR:              return "No error";
    case MCIERR_INVALID_DEVICE_ID:     return "Invalid device ID";
    case MCIERR_UNRECOGNIZED_KEYWORD:  return "Unrecognized keyword";
    case MCIERR_UNRECOGNIZED_COMMAND:  return "Unrecognized command";
    case MCIERR_HARDWARE:              return "Hardware failure";
    case MCIERR_OUT_OF_MEMORY:         return "Out of memory";
    case MCIERR_PARAM_OVERFLOW:        return "Parameter overflow";
    case MCIERR_MISSING_STRING_ARG:    return "Missing string argument";
    case MCIERR_BAD_INTEGER:           return "Bad integer";
    case MCIERR_UNSUPPORTED_FUNCTION:  return "Unsupported function";
    case MCIERR_FILE_NOT_FOUND:        return "File not found";
    case MCIERR_OUTOFRANGE:            return "Value out of range";
    case MCIERR_DUPLICATE_ALIAS:       return "Duplicate alias";
    case MCIERR_INVALID_FILE:          return "Invalid file";
    default:                           return "Unknown MCI error";
    }
}
static void m_mciSendStringA(void) {
    RET(mci_send(A32(0), A32(1), A32(2), A32(3)), 4);
}
static void m_mciGetErrorStringA(void) {             /* (MCIERROR, LPSTR, UINT) -> BOOL */
    uint32_t dst = A32(1), cb = A32(2);
    mci_put(dst, cb, mci_err_text(A32(0)));
    RET(dst && cb ? 1u : 0u, 3);
}
static void m_mciSendCommandA(void) { RET(MCIERR_UNSUPPORTED_FUNCTION, 4); }

const win32hle_shim win32hle_winmm[] = {
    { "sndPlaySoundA", m_sndPlaySoundA }, { "sndPlaySoundW", m_sndPlaySoundW },
    { "mmioStringToFOURCCA",    m_mmioStringToFOURCCA },
    { "mmioOpenA", m_mmioOpenA }, { "mmioOpenW", m_mmioOpenW }, { "mmioClose", m_mmioClose }, { "mmioRead", m_mmioRead }, { "mmioWrite", m_mmioWrite },
    { "mmioSeek", m_mmioSeek }, { "mmioDescend", m_mmioDescend }, { "mmioAscend", m_mmioAscend },
    { "mmioGetInfo", m_mmioGetInfo }, { "mmioSetInfo", m_mmioSetInfo }, { "mmioAdvance", m_mmioAdvance },
    { "mmioSetBuffer", m_mmioSetBuffer },
    { "timeGetTime",            m_timeGetTime },
    { "timeBeginPeriod",        m_timeBeginPeriod },
    { "timeEndPeriod",          m_timeEndPeriod },
    { "timeGetDevCaps",         m_timeGetDevCaps },
    { "timeSetEvent",           m_timeSetEvent },
    { "timeKillEvent",          m_timeKillEvent },
    { "joyGetNumDevs",          m_joyGetNumDevs },
    { "joyGetDevCapsA",         m_joyGetDevCapsA },
    { "joyGetPos",              m_joyGetPos },
    { "joyGetPosEx",            m_joyGetPosEx },
    { "waveOutOpen",            m_waveOutOpen },
    { "waveOutWrite",           m_waveOutWrite },
    { "waveOutPrepareHeader",   m_waveOutPrepareHeader },
    { "waveOutUnprepareHeader", m_waveOutUnprepareHeader },
    { "waveOutPause",           m_waveOutPause },
    { "waveOutRestart",         m_waveOutRestart },
    { "waveOutBreakLoop",       m_waveOutBreakLoop },
    { "waveOutGetPosition",     m_waveOutGetPosition },
    { "waveOutReset",           m_waveOutReset },
    { "waveOutClose",           m_waveOutClose },
    { "waveOutGetNumDevs",      m_waveOutGetNumDevs },
    { "waveOutGetDevCapsA",     m_waveOutGetDevCapsA },
    { "waveOutSetVolume",       m_waveOutSetVolume },
    { "waveOutGetVolume",       m_waveOutGetVolume },
    { "waveOutGetID",           m_waveOutGetID },
    { "mixerGetNumDevs",        m_none0 },
    { "mixerOpen",              m_mixerOpen },
    { "mixerClose",             m_badhandle1 },
    { "mixerGetLineInfoA",      m_badhandle3 },
    { "mixerGetLineControlsA",  m_badhandle3 },
    { "mixerGetControlDetailsA", m_badhandle3 },
    { "mixerSetControlDetails", m_badhandle3 },
    { "waveInGetNumDevs",       m_none0 },
    { "waveInOpen",             m_waveInOpen },
    { "waveInClose",            m_badhandle1 },
    { "waveInReset",            m_badhandle1 },
    { "waveInStart",            m_badhandle1 },
    { "waveInStop",             m_badhandle1 },
    { "waveInPrepareHeader",    m_badhandle3 },
    { "waveInUnprepareHeader",  m_badhandle3 },
    { "waveInAddBuffer",        m_badhandle3 },
    { "waveInGetPosition",      m_badhandle3 },
    { "midiOutGetNumDevs",      m_midiOutGetNumDevs },
    { "midiInGetNumDevs",       m_midiInGetNumDevs },
    { "midiOutGetDevCapsA",     m_midiOutGetDevCapsA },
    { "midiInGetDevCapsA",      m_midiInGetDevCapsA },
    { "midiOutOpen",            m_midiOutOpen },
    { "midiInOpen",             m_midiInOpen },
    { "midiOutClose",           m_midiOutClose },
    { "midiInClose",            m_midiInClose },
    { "midiOutShortMsg",        m_midiOutShortMsg },
    { "midiOutLongMsg",         m_midiOutLongMsg },
    { "midiOutReset",           m_midiOutReset },
    { "midiOutGetVolume",       m_midiOutGetVolume },
    { "midiOutSetVolume",       m_midiOutSetVolume },
    { "midiOutPrepareHeader",   m_midiOutPrepareHeader },
    { "midiOutUnprepareHeader", m_midiOutUnprepareHeader },
    { "midiInPrepareHeader",    m_midiInPrepareHeader },
    { "midiInUnprepareHeader",  m_midiInUnprepareHeader },
    { "midiInAddBuffer",        m_midiInAddBuffer },
    { "midiInStart",            m_midiInStart },
    { "midiInStop",             m_midiInStop },
    { "midiInReset",            m_midiInReset },
    { "midiOutGetID",           m_midiOutGetID },
    { "midiInGetID",            m_midiInGetID },
    { "midiOutGetErrorTextA",   m_midiOutGetErrorTextA },
    { "midiInGetErrorTextA",    m_midiInGetErrorTextA },
    { "mciSendStringA",         m_mciSendStringA },
    { "mciGetErrorStringA",     m_mciGetErrorStringA },
    { "mciSendCommandA",        m_mciSendCommandA },
    { "mixerGetDevCapsA",       m_mixerGetDevCapsA },
    { 0, 0 }
};
