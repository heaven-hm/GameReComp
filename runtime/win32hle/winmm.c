/*
 * winmm.c - win32hle WINMM: multimedia timers, joystick, waveOut audio,
 * sndPlaySound, MCI; no mixer and no recording device.
 * On libc for timing; waveOut is "accept and retire" (a silent device that
 * completes every buffer immediately, so a game's double-buffered mixer thread
 * keeps cycling without a real sound card); no joystick; MCI stubbed. Real SDL
 * audio can replace the waveOut path later without touching callers.
 * sndPlaySoundA/W, unlike waveOut, already play for real: they decode RIFF/WAVE
 * and push PCM through dsound.c's SDL audio mixer stream path.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "win32hle.h"

#define MMSYSERR_NOERROR  0u
#define MMSYSERR_ERROR    1u
#define JOYERR_UNPLUGGED  167u
#define WHDR_DONE         0x00000001u
#define WHDR_INQUEUE      0x00000010u
#define MCIERR_UNSUPPORTED_FUNCTION 274u

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

/* --- waveOut: a silent device that retires every buffer at once ---
 * waveOutOpen succeeds with a fake handle; waveOutWrite marks the WAVEHDR done
 * and not-in-queue, so a mixer that waits for buffers to drain keeps going. */
static void m_waveOutOpen(void) {              /* (&hwo, devID, &fmt, cb, inst, flags) */
    uint32_t ph = A32(0);
    if (ph) MEM32(ph) = 0x5A5E0001u;           /* a nonzero HWAVEOUT */
    RET(MMSYSERR_NOERROR, 6);
}
static void m_waveOutWrite(void) {             /* (hwo, lpWaveHdr, cbwh) */
    uint32_t wh = A32(1);
    if (wh) { MEM32(wh + 16) = (MEM32(wh + 16) | WHDR_DONE) & ~WHDR_INQUEUE; }
    RET(MMSYSERR_NOERROR, 3);
}
static void m_waveOutPrepareHeader(void)   { uint32_t wh=A32(1); if(wh) MEM32(wh+16)|=0x00000002u/*WHDR_PREPARED*/; RET(MMSYSERR_NOERROR, 3); }
static void m_waveOutUnprepareHeader(void) { uint32_t wh=A32(1); if(wh) MEM32(wh+16)&=~0x00000002u; RET(MMSYSERR_NOERROR, 3); }
static void m_waveOutReset(void)       { RET(MMSYSERR_NOERROR, 1); }
static void m_waveOutClose(void)       { RET(MMSYSERR_NOERROR, 1); }
static void m_waveOutGetNumDevs(void)  { RET(1, 0); }          /* one (silent) device */
static void m_waveOutGetDevCapsA(void) { RET(MMSYSERR_NOERROR, 3); }

/* The device's own volume, the id behind a handle: the one device, at full
 * volume (a caller's level is its own business: dsound.c mixes at the level
 * a buffer asks for). */
static void m_waveOutSetVolume(void)  { RET(MMSYSERR_NOERROR, 2); }
static void m_waveOutGetVolume(void)  { if (A32(1)) MEM32(A32(1)) = 0xFFFFFFFFu; RET(MMSYSERR_NOERROR, 2); }
static void m_waveOutGetID(void)      { if (A32(1)) MEM32(A32(1)) = 0; RET(MMSYSERR_NOERROR, 2); }

/* --- no mixer, no recording device ---
 * What Windows answers on a PC without either: no devices, an open of device
 * 0 refused as a bad id, and anything given a handle told it is not one. A
 * program that sets its card's levels through the mixer, or enumerates
 * inputs for a record page, carries on without them. */
#define MMSYSERR_BADDEVICEID  2u
#define MMSYSERR_INVALHANDLE  5u
static void m_none0(void)       { RET(0, 0); }                        /* mixerGetNumDevs, waveInGetNumDevs */
static void m_mixerOpen(void)   { if (A32(0)) MEM32(A32(0)) = 0; RET(MMSYSERR_BADDEVICEID, 5); }
static void m_waveInOpen(void)  { if (A32(0)) MEM32(A32(0)) = 0; RET(MMSYSERR_BADDEVICEID, 6); }
static void m_badhandle1(void)  { RET(MMSYSERR_INVALHANDLE, 1); }     /* (h) */
static void m_badhandle3(void)  { RET(MMSYSERR_INVALHANDLE, 3); }     /* (h, struct, size or flags) */

/* --- MCI (CD audio / command strings): not available --- */
static void m_mciSendStringA(void)     { RET(MMSYSERR_ERROR, 4); }
static void m_mciGetErrorStringA(void) { RET(0, 3); }          /* FALSE: no string */
static void m_mciSendCommandA(void)    { RET(MCIERR_UNSUPPORTED_FUNCTION, 4); }
static void m_mixerGetDevCapsA(void)   { RET(MMSYSERR_BADDEVICEID, 3); }

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

#define MMSYSERR_ALLOCATED     4u
#define MMSYSERR_NODRIVER      6u
#define MMSYSERR_NOMEM         7u
#define MMSYSERR_NOTSUPPORTED  8u
#define MMSYSERR_INVALFLAG    10u
#define MMSYSERR_INVALPARAM   11u

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
    { "mciSendStringA",         m_mciSendStringA },
    { "mciGetErrorStringA",     m_mciGetErrorStringA },
    { "mciSendCommandA",        m_mciSendCommandA },
    { "mixerGetDevCapsA",       m_mixerGetDevCapsA },
    { 0, 0 }
};
