/*
 * dsound.c - DirectSound on SDL2 audio: IDirectSound and IDirectSoundBuffer,
 * with a software mixer, and driver enumeration over SDL's device list.
 *
 * A secondary buffer is guest memory the game Locks and writes, as it would
 * be on Windows; the mixer (SDL's audio thread) reads it at the buffer's
 * frequency, volume and pan and moves its play cursor in real time, so a
 * game that streams into a looping buffer by watching GetCurrentPosition
 * keeps doing so. The primary buffer is only its format: everything is mixed
 * at the device's rate. One mutex covers the buffers' state, between the
 * mixer and the shims.
 *
 * With no audio device (SDL_AUDIODRIVER=dummy, a headless box) SDL's dummy
 * driver still runs the callback in real time, so the cursors still move.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <pthread.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

#define DS_OK               0u
#define DSERR_INVALIDPARAM  0x80070057u
#define DSERR_NODRIVER      0x88780078u
#define DSERR_INVALIDCALL   0x88780032u
#define DSERR_BADFORMAT     0x88780064u
#define DSERR_PRIOLEVELNEEDED 0x88780046u
#define DSERR_BUFFERLOST    0x88780096u
#define DSBCAPS_PRIMARYBUFFER 0x00000001u
#define DSBPLAY_LOOPING     0x00000001u
#define DSBSTATUS_PLAYING   0x00000001u
#define DSBSTATUS_LOOPING   0x00000004u
#define DSCBSTATUS_CAPTURING 0x00000001u
#define DSCBSTATUS_LOOPING   0x00000002u
#define DSCBLOCK_ENTIREBUFFER 0x00000001u
#define DSCBSTART_LOOPING    0x00000001u
#define DSERR_UNSUPPORTED   0x80004001u  /* E_NOTIMPL */
#define DSERR_OBJECTNOTFOUND 0x88781161u /* Wine dsound.h: MAKE_DSHRESULT(4449) */

#define OUT_RATE 44100

/* Public IIDs, in the byte order a guest passes them (wine-10.0 dsound.h).
 * The legacy DX7 IIDs already used below differ in Data2/Data3 and are kept
 * for the IDirectSound v1 facade; DS8 callers use these. */
static const uint8_t IID_DirectSound8[16] = {
    0x93, 0x7E, 0x0A, 0xC5, 0x95, 0xF3, 0x34, 0x48,
    0x9E, 0xF6, 0x7F, 0xA9, 0x9D, 0xE5, 0x09, 0x66 };
static const uint8_t IID_DSBuf8[16] = {
    0x49, 0xA4, 0x25, 0x68, 0x24, 0x75, 0x82, 0x4D,
    0x92, 0x0F, 0x50, 0xE3, 0x6A, 0xB3, 0xAB, 0x1E };
static const uint8_t IID_DSCapture[16] = {
    0x81, 0x07, 0x21, 0xB0, 0xCD, 0x89, 0xCD, 0x11,
    0xAF, 0x08, 0x00, 0xA0, 0xC9, 0x25, 0xCD, 0x16 };
static const uint8_t IID_DSCaptureBuf[16] = {
    0x82, 0x07, 0x21, 0xB0, 0xCD, 0x89, 0xCD, 0x11,
    0xAF, 0x08, 0x00, 0xA0, 0xC9, 0x25, 0xCD, 0x16 };
static const uint8_t IID_DSCaptureBuf8[16] = {
    0xF4, 0x0D, 0x99, 0x00, 0xDB, 0x0B, 0x72, 0x48,
    0x83, 0x3E, 0x6D, 0x30, 0x3E, 0x80, 0xAE, 0xB6 };

typedef struct {
    uint8_t *data;              /* the samples, guest-visible */
    int *data_refs;             /* shared with duplicates */
    uint32_t bytes, flags;
    int channels, bits, rate, block;
    int playing, looping, primary, ds8;
    double pos;                 /* the play cursor, in frames */
    int volume, pan;            /* hundredths of a dB; -10000..10000 */
    float gl, gr;
} dsbuf;

static pthread_mutex_t g_mix = PTHREAD_MUTEX_INITIALIZER;
#define MAX_BUFS 256
static dsbuf *g_buf[MAX_BUFS];
static SDL_AudioDeviceID g_dev;
static int g_audio_ok = -1;
static float g_master = 1.0f;
void hle_dsound_set_master(float gain) { g_master = gain; }

static void gains(dsbuf *b) {
    float v = b->volume <= -10000 ? 0.0f : powf(10.0f, (float)b->volume / 2000.0f);
    float l = 1.0f, r = 1.0f;
    if (b->pan < 0) r = b->pan <= -10000 ? 0.0f : powf(10.0f, (float)b->pan / 2000.0f);
    if (b->pan > 0) l = b->pan >= 10000 ? 0.0f : powf(10.0f, (float)-b->pan / 2000.0f);
    b->gl = v * l, b->gr = v * r;
}

static inline float sample(const dsbuf *b, uint32_t frame, int ch) {
    const uint8_t *p = b->data + (size_t)frame * (uint32_t)b->block + (b->channels > 1 ? (size_t)ch * (uint32_t)(b->bits / 8) : 0);
    return b->bits == 16 ? (float)*(const int16_t *)p / 32768.0f : ((float)*p - 128.0f) / 128.0f;
}

static int audio_open(void);
static int audio_open_once(void) { return audio_open(); }

/* Host streams: PCM a host pushes (a movie's sound), mixed with the
 * buffers. A ring of interleaved s16 at the stream's rate, played at that
 * rate. */
#define MAX_STREAMS 4
#define STREAM_FRAMES (1 << 17)
static struct { int used, channels, rate; float gain; int16_t *ring; uint32_t rd, wr; double pos; } g_stream[MAX_STREAMS];
int hle_audio_stream_open(int rate, int channels) {
    if (!audio_open_once()) return -1;
    pthread_mutex_lock(&g_mix);
    int id = -1;
    for (int i = 0; i < MAX_STREAMS && id < 0; i++)
        if (!g_stream[i].used) {
            id = i;
            g_stream[i].ring = (int16_t *)calloc(STREAM_FRAMES * 2, 2);
            g_stream[i].used = 1, g_stream[i].channels = channels, g_stream[i].rate = rate, g_stream[i].gain = 1.0f;
            g_stream[i].rd = g_stream[i].wr = 0, g_stream[i].pos = 0;
        }
    pthread_mutex_unlock(&g_mix);
    return id;
}
void hle_audio_stream_push(int id, const int16_t *pcm, int frames) {
    if (id < 0 || id >= MAX_STREAMS) return;
    pthread_mutex_lock(&g_mix);
    for (int f = 0; f < frames && g_stream[id].wr - g_stream[id].rd < STREAM_FRAMES - 1; f++, g_stream[id].wr++) {
        uint32_t k = g_stream[id].wr % STREAM_FRAMES;
        g_stream[id].ring[2 * k] = pcm[f * g_stream[id].channels];
        g_stream[id].ring[2 * k + 1] = pcm[f * g_stream[id].channels + (g_stream[id].channels > 1)];
    }
    pthread_mutex_unlock(&g_mix);
}
int hle_audio_stream_queued(int id) {                    /* frames not yet played */
    if (id < 0 || id >= MAX_STREAMS) return 0;
    pthread_mutex_lock(&g_mix);
    int n = (int)(g_stream[id].wr - g_stream[id].rd);
    pthread_mutex_unlock(&g_mix);
    return n;
}
void hle_audio_stream_gain(int id, float gain) { if (id >= 0 && id < MAX_STREAMS) g_stream[id].gain = gain; }
void hle_audio_stream_close(int id) {
    if (id < 0 || id >= MAX_STREAMS) return;
    pthread_mutex_lock(&g_mix);
    free(g_stream[id].ring);
    memset(&g_stream[id], 0, sizeof g_stream[id]);
    pthread_mutex_unlock(&g_mix);
}

static void mix(void *ud, Uint8 *stream, int len) {
    (void)ud;
    int frames = len / 4;
    static float acc[2 * 8192];
    if (frames > 8192) frames = 8192;
    memset(acc, 0, sizeof(float) * 2 * (size_t)frames);
    pthread_mutex_lock(&g_mix);
    for (int i = 0; i < MAX_BUFS; i++) {
        dsbuf *b = g_buf[i];
        if (!b || !b->playing || b->primary || !b->block) continue;
        uint32_t nframes = b->bytes / (uint32_t)b->block;
        double step = (double)b->rate / OUT_RATE;
        for (int f = 0; f < frames && b->playing; f++) {
            uint32_t at = (uint32_t)b->pos;
            if (at >= nframes) {
                if (b->looping) { b->pos -= nframes; at = (uint32_t)b->pos; if (at >= nframes) b->pos = 0, at = 0; }
                else { b->playing = 0; b->pos = 0; break; }
            }
            float l = sample(b, at, 0), r = b->channels > 1 ? sample(b, at, 1) : l;
            acc[2 * f] += l * b->gl;
            acc[2 * f + 1] += r * b->gr;
            b->pos += step;
        }
    }
    for (int i = 0; i < MAX_STREAMS; i++) {
        if (!g_stream[i].used) continue;
        double step = (double)g_stream[i].rate / OUT_RATE;
        for (int f = 0; f < frames && g_stream[i].rd < g_stream[i].wr; f++) {
            uint32_t k = g_stream[i].rd % STREAM_FRAMES;
            acc[2 * f] += g_stream[i].ring[2 * k] / 32768.0f * g_stream[i].gain;
            acc[2 * f + 1] += g_stream[i].ring[2 * k + 1] / 32768.0f * g_stream[i].gain;
            g_stream[i].pos += step;
            while (g_stream[i].pos >= 1.0 && g_stream[i].rd < g_stream[i].wr) g_stream[i].pos -= 1.0, g_stream[i].rd++;
        }
    }
    pthread_mutex_unlock(&g_mix);
    int16_t *out = (int16_t *)stream;
    for (int k = 0; k < 2 * frames; k++) {
        float v = acc[k] * g_master * 32767.0f;
        out[k] = (int16_t)(v > 32767.0f ? 32767 : v < -32768.0f ? -32768 : v);
    }
}

static int audio_open(void) {
    if (g_audio_ok >= 0) return g_audio_ok;
    g_audio_ok = 0;
    SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");      /* SIGINT and SIGTERM end the process, not WM_CLOSE */
    SDL_AudioSpec want, have;
    SDL_zero(want);
    want.freq = OUT_RATE, want.format = AUDIO_S16SYS, want.channels = 2, want.samples = 1024, want.callback = mix;
    if (SDL_InitSubSystem(SDL_INIT_AUDIO) == 0) g_dev = SDL_OpenAudioDevice(NULL, 0, &want, &have, 0);
    if (!g_dev) {
        /* No sound card (a server, a container): a silent device whose
         * cursors still move, rather than DirectSound failing. A game whose
         * sound DLL takes the failure for granted (Civilization III's
         * sound.dll) would fault on it. */
        fprintf(stderr, "[dsound] no audio device (%s): silent\n", SDL_GetError());
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        SDL_setenv("SDL_AUDIODRIVER", "dummy", 1);
        if (SDL_InitSubSystem(SDL_INIT_AUDIO) == 0) g_dev = SDL_OpenAudioDevice(NULL, 0, &want, &have, 0);
        if (!g_dev) { fprintf(stderr, "[dsound] no audio: %s\n", SDL_GetError()); return 0; }
    }
    SDL_PauseAudioDevice(g_dev, 0);
    fprintf(stderr, "[dsound] %s, %d Hz\n", SDL_GetCurrentAudioDriver(), have.freq);
    return g_audio_ok = 1;
}

/* the object: vtable, refs, then the buffer slot */
#define SLOT(self) MEM32((self) + 8)
static dsbuf *buf_of(uint32_t self) { uint32_t i = SLOT(self); return i < MAX_BUFS ? g_buf[i] : NULL; }

static uint32_t g_vt_ds, g_vt_buf, g_vt_ds8, g_vt_buf8, g_vt_cap8, g_vt_capbuf8;
static uint32_t new_buffer(dsbuf *b) {
    pthread_mutex_lock(&g_mix);
    int slot = -1;
    for (int i = 0; i < MAX_BUFS && slot < 0; i++) if (!g_buf[i]) slot = i;
    if (slot >= 0) g_buf[slot] = b;
    pthread_mutex_unlock(&g_mix);
    if (slot < 0) return 0;
    uint32_t o = hle_com_new(b->ds8 ? g_vt_buf8 : g_vt_buf, 12);
    /* Public IID_IDirectSoundBuffer (Microsoft DirectX headers). */
    static const uint8_t IID_DSBuf[16] = {
        0x85, 0xFA, 0x9A, 0x27, 0x8E, 0x49, 0xD0, 0x11,
        0x98, 0x28, 0x00, 0xC0, 0x4F, 0xC2, 0x97, 0x21
    };
    hle_com_bind_iid(o, IID_DSBuf, o);
    if (b->ds8) hle_com_bind_iid(o, IID_DSBuf8, o);
    SLOT(o) = (uint32_t)slot;
    return o;
}

/* ---- IDirectSoundBuffer ---- */
static void b_Release(void) {
    uint32_t self = A32(0), n = --MEM32(self + 4);
    if (!n) {
        pthread_mutex_lock(&g_mix);
        dsbuf *b = buf_of(self);
        g_buf[SLOT(self)] = NULL;
        pthread_mutex_unlock(&g_mix);
        if (b) {
            if (--*b->data_refs == 0) { hle_free(b->data); free(b->data_refs); }
            free(b);
        }
        hle_com_unbind_iids(self);
        hle_com_free(self);
    }
    RET(n, 1);
}
static void b_GetCaps(void) {                            /* (this, &DSBCAPS) */
    dsbuf *b = buf_of(A32(0));
    uint32_t c = A32(1);
    if (!b || !c) RET(DSERR_INVALIDPARAM, 2);
    MEM32(c + 4) = b->flags | 0x8u /* LOCSOFTWARE */, MEM32(c + 8) = b->bytes, MEM32(c + 12) = 0, MEM32(c + 16) = 0;
    RET(DS_OK, 2);
}
static void b_GetCurrentPosition(void) {                 /* (this, &play, &write) */
    dsbuf *b = buf_of(A32(0));
    if (!b) RET(DSERR_INVALIDPARAM, 3);
    pthread_mutex_lock(&g_mix);
    uint32_t play = b->block ? (uint32_t)b->pos * (uint32_t)b->block : 0;
    uint32_t lead = b->block ? (uint32_t)(b->rate / 100) * (uint32_t)b->block : 0;   /* 10 ms ahead */
    pthread_mutex_unlock(&g_mix);
    if (b->bytes) play %= b->bytes;
    if (A32(1)) MEM32(A32(1)) = play;
    if (A32(2)) MEM32(A32(2)) = b->bytes ? (play + (b->playing ? lead : 0)) % b->bytes : 0;
    RET(DS_OK, 3);
}
static void b_GetFormat(void) {                          /* (this, &wfx, size, &written) */
    dsbuf *b = buf_of(A32(0));
    uint32_t w = A32(1);
    if (!b) RET(DSERR_INVALIDPARAM, 4);
    if (w && A32(2) >= 16) {
        MEM16(w) = 1, MEM16(w + 2) = (uint16_t)b->channels, MEM32(w + 4) = (uint32_t)b->rate;
        MEM32(w + 8) = (uint32_t)(b->rate * b->block), MEM16(w + 12) = (uint16_t)b->block, MEM16(w + 14) = (uint16_t)b->bits;
        if (A32(2) >= 18) MEM16(w + 16) = 0;
    }
    if (A32(3)) MEM32(A32(3)) = 18;
    RET(DS_OK, 4);
}
static void b_GetVolume(void)    { dsbuf *b = buf_of(A32(0)); if (A32(1) && b) MEM32(A32(1)) = (uint32_t)b->volume; RET(b ? DS_OK : DSERR_INVALIDPARAM, 2); }
static void b_GetPan(void)       { dsbuf *b = buf_of(A32(0)); if (A32(1) && b) MEM32(A32(1)) = (uint32_t)b->pan; RET(b ? DS_OK : DSERR_INVALIDPARAM, 2); }
static void b_GetFrequency(void) { dsbuf *b = buf_of(A32(0)); if (A32(1) && b) MEM32(A32(1)) = (uint32_t)b->rate; RET(b ? DS_OK : DSERR_INVALIDPARAM, 2); }
static void b_GetStatus(void) {
    dsbuf *b = buf_of(A32(0));
    if (A32(1) && b) MEM32(A32(1)) = (b->playing ? DSBSTATUS_PLAYING : 0u) | (b->playing && b->looping ? DSBSTATUS_LOOPING : 0u);
    RET(b ? DS_OK : DSERR_INVALIDPARAM, 2);
}
static void b_Initialize(void) { RET(DSERR_INVALIDCALL, 3); }   /* already initialized */
static void b_Lock(void) {                               /* (this, offset, bytes, &p1, &n1, &p2, &n2, flags) */
    dsbuf *b = buf_of(A32(0));
    if (!b || b->primary) RET(b ? DSERR_PRIOLEVELNEEDED : DSERR_INVALIDPARAM, 8);
    uint32_t off = A32(1), n = A32(2), fl = A32(7);
    if (fl & 2u) off = 0, n = b->bytes;                  /* DSBLOCK_ENTIREBUFFER */
    else if (fl & 1u) {                                  /* DSBLOCK_FROMWRITECURSOR */
        pthread_mutex_lock(&g_mix);
        off = b->block ? ((uint32_t)b->pos * (uint32_t)b->block + (uint32_t)(b->rate / 100) * (uint32_t)b->block) % b->bytes : 0;
        pthread_mutex_unlock(&g_mix);
    }
    if (off >= b->bytes || n > b->bytes) RET(DSERR_INVALIDPARAM, 8);
    uint32_t n1 = off + n <= b->bytes ? n : b->bytes - off;
    if (A32(3)) MEM32(A32(3)) = hle_va(b->data + off);
    if (A32(4)) MEM32(A32(4)) = n1;
    if (A32(5)) MEM32(A32(5)) = n1 < n ? hle_va(b->data) : 0;
    if (A32(6)) MEM32(A32(6)) = n - n1;
    RET(DS_OK, 8);
}
static void b_Unlock(void) { RET(DS_OK, 5); }            /* (this, p1, n1, p2, n2): the memory is the buffer */
static void b_Play(void) {                               /* (this, reserved, priority, flags) */
    dsbuf *b = buf_of(A32(0));
    if (!b) RET(DSERR_INVALIDPARAM, 4);
    pthread_mutex_lock(&g_mix);
    b->playing = 1, b->looping = (A32(3) & DSBPLAY_LOOPING) != 0;
    pthread_mutex_unlock(&g_mix);
    RET(DS_OK, 4);
}
static void b_SetCurrentPosition(void) {
    dsbuf *b = buf_of(A32(0));
    if (!b || !b->block) RET(DSERR_INVALIDPARAM, 2);
    pthread_mutex_lock(&g_mix);
    b->pos = (double)((A32(1) % (b->bytes ? b->bytes : 1)) / (uint32_t)b->block);
    pthread_mutex_unlock(&g_mix);
    RET(DS_OK, 2);
}
static void b_SetFormat(void) {                          /* the primary's: the mix is at the device rate anyway */
    dsbuf *b = buf_of(A32(0));
    uint32_t w = A32(1);
    if (b && w) b->channels = MEM16(w + 2), b->rate = (int)MEM32(w + 4), b->bits = MEM16(w + 14), b->block = MEM16(w + 12);
    RET(DS_OK, 2);
}
static void b_SetVolume(void) {
    dsbuf *b = buf_of(A32(0));
    if (!b) RET(DSERR_INVALIDPARAM, 2);
    pthread_mutex_lock(&g_mix);
    b->volume = (int)A32(1);
    gains(b);
    pthread_mutex_unlock(&g_mix);
    RET(DS_OK, 2);
}
static void b_SetPan(void) {
    dsbuf *b = buf_of(A32(0));
    if (!b) RET(DSERR_INVALIDPARAM, 2);
    pthread_mutex_lock(&g_mix);
    b->pan = (int)A32(1);
    gains(b);
    pthread_mutex_unlock(&g_mix);
    RET(DS_OK, 2);
}
static void b_SetFrequency(void) {
    dsbuf *b = buf_of(A32(0));
    if (!b) RET(DSERR_INVALIDPARAM, 2);
    pthread_mutex_lock(&g_mix);
    if (A32(1)) b->rate = (int)A32(1);
    pthread_mutex_unlock(&g_mix);
    RET(DS_OK, 2);
}
static void b_Stop(void) {
    dsbuf *b = buf_of(A32(0));
    if (!b) RET(DSERR_INVALIDPARAM, 1);
    pthread_mutex_lock(&g_mix);
    b->playing = 0;
    pthread_mutex_unlock(&g_mix);
    RET(DS_OK, 1);
}
static void b_Restore(void) { RET(DS_OK, 1); }
/* IDirectSoundBuffer8's three extra slots. No effect processing exists here,
 * so a request for effects is answered as unsupported rather than accepted
 * and then silently ignored. */
static void b8_SetFX(void)            { RET(DSERR_UNSUPPORTED, 4); }
static void b8_AcquireResources(void) { RET(A32(2) ? DSERR_UNSUPPORTED : DS_OK, 4); }  /* dwEffectsCount */
static void b8_GetObjectInPath(void)  { RET(DSERR_OBJECTNOTFOUND, 5); }

/* ---- IDirectSound ---- */
static void d_Release(void) {
    uint32_t self = A32(0), n = --MEM32(self + 4);
    if (!n) { hle_com_unbind_iids(self); hle_com_free(self); }
    RET(n, 1);
}
static uint32_t create_buffer_impl(uint32_t d, uint32_t out, int ds8) {
    if (!d || !out) return DSERR_INVALIDPARAM;
    uint32_t flags = MEM32(d + 4), bytes = MEM32(d + 8), wfx = MEM32(d + 16);
    dsbuf *b = (dsbuf *)calloc(1, sizeof *b);
    b->flags = flags, b->primary = (flags & DSBCAPS_PRIMARYBUFFER) != 0, b->ds8 = ds8;
    b->channels = 2, b->bits = 16, b->rate = 22050, b->block = 4;
    if (wfx) {
        b->channels = MEM16(wfx + 2), b->rate = (int)MEM32(wfx + 4), b->block = MEM16(wfx + 12), b->bits = MEM16(wfx + 14);
        if (MEM16(wfx) != 1 || (b->bits != 8 && b->bits != 16) || b->channels < 1 || b->channels > 2) {
            fprintf(stderr, "[dsound] buffer format tag %u, %d bits, %d channels: not PCM this mixer plays\n", MEM16(wfx), b->bits, b->channels);
            free(b);
            return DSERR_INVALIDPARAM;
        }
    }
    if (b->primary) bytes = 4096 * 4;
    b->bytes = bytes;
    b->data = (uint8_t *)hle_alloc(bytes ? bytes : 4);
    if (b->bits == 8) memset(b->data, 128, bytes);
    b->data_refs = (int *)malloc(sizeof(int));
    *b->data_refs = 1;
    gains(b);
    uint32_t o = new_buffer(b);
    if (!o) { hle_free(b->data); free(b->data_refs); free(b); return DSERR_INVALIDPARAM; }
    MEM32(out) = o;
    return DS_OK;
}
static void d_CreateSoundBuffer(void) {                  /* (this, &DSBUFFERDESC, &out, outer) */
    RET(create_buffer_impl(A32(1), A32(2), 0), 4);
}
static void d_CreateSoundBuffer8(void) {                  /* IDirectSound8::CreateSoundBuffer */
    RET(create_buffer_impl(A32(1), A32(2), 1), 4);
}
static void d_GetCaps(void) {                            /* (this, &DSCAPS) */
    uint32_t c = A32(1);
    if (c) {
        uint32_t size = MEM32(c);
        memset(hle_ptr(c + 4), 0, size > 4 ? size - 4 : 0);
        MEM32(c + 4) = 0x0F5Fu;                          /* the primary/secondary 8/16-bit mono/stereo, continuous rate */
        MEM32(c + 8) = 100, MEM32(c + 12) = 100000;      /* min/max secondary rate */
        MEM32(c + 16) = 1;                               /* primary buffers */
        MEM32(c + 20) = 64, MEM32(c + 24) = 64, MEM32(c + 28) = 64;   /* max hw mixing: all, static, streaming */
        MEM32(c + 32) = 64, MEM32(c + 36) = 64, MEM32(c + 40) = 64;   /* free hw */
    }
    RET(DS_OK, 2);
}
static void d_DuplicateSoundBuffer(void) {               /* (this, original, &out) */
    dsbuf *src = buf_of(A32(1));
    if (!src || !A32(2)) RET(DSERR_INVALIDPARAM, 3);
    dsbuf *b = (dsbuf *)malloc(sizeof *b);
    pthread_mutex_lock(&g_mix);
    *b = *src;
    (*b->data_refs)++;
    pthread_mutex_unlock(&g_mix);
    b->playing = 0, b->pos = 0;
    uint32_t o = new_buffer(b);
    MEM32(A32(2)) = o;
    RET(o ? DS_OK : DSERR_INVALIDPARAM, 3);
}
static void d_SetCooperativeLevel(void) { RET(DS_OK, 3); }
static void d_Compact(void) { RET(DS_OK, 1); }
static void d_GetSpeakerConfig(void) { if (A32(1)) MEM32(A32(1)) = 4; RET(DS_OK, 2); }   /* DSSPEAKER_STEREO */
static void d_SetSpeakerConfig(void) { RET(DS_OK, 2); }
static void d_Initialize(void) { RET(DS_OK, 2); }
static void d_VerifyCertification(void) {                /* IDirectSound8 slot 11 */
    if (A32(1)) MEM32(A32(1)) = 0;                        /* DS_CERTIFIED */
    RET(DS_OK, 2);
}

/* The capture vtable builders are defined later in this file. */
static void cb_Release(void), cb_GetCaps(void), cb_GetCurrentPosition(void), cb_GetFormat(void);
static void cb_GetStatus(void), cb_Initialize(void), cb_Lock(void), cb_Start(void), cb_Stop(void), cb_Unlock(void);
static void cb8_GetObjectInPath(void), cb8_GetFXStatus(void);
static void c_CreateCaptureBuffer(void), c_GetCaps(void), c_Initialize(void);

static void vtables(void) {
    if (g_vt_ds) return;
    static const win32hle_shim ds[] = {
        { "IDirectSound::QueryInterface", hle_com_QueryInterface }, { "IDirectSound::AddRef", hle_com_AddRef },
        { "IDirectSound::Release", d_Release }, { "IDirectSound::CreateSoundBuffer", d_CreateSoundBuffer },
        { "IDirectSound::GetCaps", d_GetCaps }, { "IDirectSound::DuplicateSoundBuffer", d_DuplicateSoundBuffer },
        { "IDirectSound::SetCooperativeLevel", d_SetCooperativeLevel }, { "IDirectSound::Compact", d_Compact },
        { "IDirectSound::GetSpeakerConfig", d_GetSpeakerConfig }, { "IDirectSound::SetSpeakerConfig", d_SetSpeakerConfig },
        { "IDirectSound::Initialize", d_Initialize }, { 0, 0 } };
    static const win32hle_shim buf[] = {
        { "IDirectSoundBuffer::QueryInterface", hle_com_QueryInterface }, { "IDirectSoundBuffer::AddRef", hle_com_AddRef },
        { "IDirectSoundBuffer::Release", b_Release }, { "IDirectSoundBuffer::GetCaps", b_GetCaps },
        { "IDirectSoundBuffer::GetCurrentPosition", b_GetCurrentPosition }, { "IDirectSoundBuffer::GetFormat", b_GetFormat },
        { "IDirectSoundBuffer::GetVolume", b_GetVolume }, { "IDirectSoundBuffer::GetPan", b_GetPan },
        { "IDirectSoundBuffer::GetFrequency", b_GetFrequency }, { "IDirectSoundBuffer::GetStatus", b_GetStatus },
        { "IDirectSoundBuffer::Initialize", b_Initialize }, { "IDirectSoundBuffer::Lock", b_Lock },
        { "IDirectSoundBuffer::Play", b_Play }, { "IDirectSoundBuffer::SetCurrentPosition", b_SetCurrentPosition },
        { "IDirectSoundBuffer::SetFormat", b_SetFormat }, { "IDirectSoundBuffer::SetVolume", b_SetVolume },
        { "IDirectSoundBuffer::SetPan", b_SetPan }, { "IDirectSoundBuffer::SetFrequency", b_SetFrequency },
        { "IDirectSoundBuffer::Stop", b_Stop }, { "IDirectSoundBuffer::Unlock", b_Unlock },
        { "IDirectSoundBuffer::Restore", b_Restore }, { 0, 0 } };
    /* IDirectSound8 adds VerifyCertification after Initialize. Distinct shim
     * names keep the two vtables' entries independent. */
    static const win32hle_shim ds8[] = {
        { "IDirectSound8::QueryInterface", hle_com_QueryInterface }, { "IDirectSound8::AddRef", hle_com_AddRef },
        { "IDirectSound8::Release", d_Release }, { "IDirectSound8::CreateSoundBuffer", d_CreateSoundBuffer8 },
        { "IDirectSound8::GetCaps", d_GetCaps }, { "IDirectSound8::DuplicateSoundBuffer", d_DuplicateSoundBuffer },
        { "IDirectSound8::SetCooperativeLevel", d_SetCooperativeLevel }, { "IDirectSound8::Compact", d_Compact },
        { "IDirectSound8::GetSpeakerConfig", d_GetSpeakerConfig }, { "IDirectSound8::SetSpeakerConfig", d_SetSpeakerConfig },
        { "IDirectSound8::Initialize", d_Initialize }, { "IDirectSound8::VerifyCertification", d_VerifyCertification },
        { 0, 0 } };
    /* IDirectSoundBuffer8 mirrors the first 21 slots, then adds three. */
    static const win32hle_shim buf8[] = {
        { "IDirectSoundBuffer8::QueryInterface", hle_com_QueryInterface }, { "IDirectSoundBuffer8::AddRef", hle_com_AddRef },
        { "IDirectSoundBuffer8::Release", b_Release }, { "IDirectSoundBuffer8::GetCaps", b_GetCaps },
        { "IDirectSoundBuffer8::GetCurrentPosition", b_GetCurrentPosition }, { "IDirectSoundBuffer8::GetFormat", b_GetFormat },
        { "IDirectSoundBuffer8::GetVolume", b_GetVolume }, { "IDirectSoundBuffer8::GetPan", b_GetPan },
        { "IDirectSoundBuffer8::GetFrequency", b_GetFrequency }, { "IDirectSoundBuffer8::GetStatus", b_GetStatus },
        { "IDirectSoundBuffer8::Initialize", b_Initialize }, { "IDirectSoundBuffer8::Lock", b_Lock },
        { "IDirectSoundBuffer8::Play", b_Play }, { "IDirectSoundBuffer8::SetCurrentPosition", b_SetCurrentPosition },
        { "IDirectSoundBuffer8::SetFormat", b_SetFormat }, { "IDirectSoundBuffer8::SetVolume", b_SetVolume },
        { "IDirectSoundBuffer8::SetPan", b_SetPan }, { "IDirectSoundBuffer8::SetFrequency", b_SetFrequency },
        { "IDirectSoundBuffer8::Stop", b_Stop }, { "IDirectSoundBuffer8::Unlock", b_Unlock },
        { "IDirectSoundBuffer8::Restore", b_Restore },
        { "IDirectSoundBuffer8::SetFX", b8_SetFX }, { "IDirectSoundBuffer8::AcquireResources", b8_AcquireResources },
        { "IDirectSoundBuffer8::GetObjectInPath", b8_GetObjectInPath }, { 0, 0 } };
    /* IDirectSoundCapture8: 6 slots. Its buffer8 adds two slots after Unlock. */
    static const win32hle_shim cap8[] = {
        { "IDirectSoundCapture8::QueryInterface", hle_com_QueryInterface }, { "IDirectSoundCapture8::AddRef", hle_com_AddRef },
        { "IDirectSoundCapture8::Release", hle_com_Release }, { "IDirectSoundCapture8::CreateCaptureBuffer", c_CreateCaptureBuffer },
        { "IDirectSoundCapture8::GetCaps", c_GetCaps }, { "IDirectSoundCapture8::Initialize", c_Initialize },
        { 0, 0 } };
    static const win32hle_shim capbuf8[] = {
        { "IDirectSoundCaptureBuffer8::QueryInterface", hle_com_QueryInterface }, { "IDirectSoundCaptureBuffer8::AddRef", hle_com_AddRef },
        { "IDirectSoundCaptureBuffer8::Release", cb_Release }, { "IDirectSoundCaptureBuffer8::GetCaps", cb_GetCaps },
        { "IDirectSoundCaptureBuffer8::GetCurrentPosition", cb_GetCurrentPosition }, { "IDirectSoundCaptureBuffer8::GetFormat", cb_GetFormat },
        { "IDirectSoundCaptureBuffer8::GetStatus", cb_GetStatus }, { "IDirectSoundCaptureBuffer8::Initialize", cb_Initialize },
        { "IDirectSoundCaptureBuffer8::Lock", cb_Lock }, { "IDirectSoundCaptureBuffer8::Start", cb_Start },
        { "IDirectSoundCaptureBuffer8::Stop", cb_Stop }, { "IDirectSoundCaptureBuffer8::Unlock", cb_Unlock },
        { "IDirectSoundCaptureBuffer8::GetObjectInPath", cb8_GetObjectInPath }, { "IDirectSoundCaptureBuffer8::GetFXStatus", cb8_GetFXStatus },
        { 0, 0 } };
    g_vt_ds = hle_com_vtable(ds);
    g_vt_buf = hle_com_vtable(buf);
    g_vt_ds8 = hle_com_vtable(ds8);
    g_vt_buf8 = hle_com_vtable(buf8);
    g_vt_cap8 = hle_com_vtable(cap8);
    g_vt_capbuf8 = hle_com_vtable(capbuf8);
}

/* IID_IDirectSound from the public DirectX SDK headers. */
static const uint8_t IID_DirectSound[16] = {
    0x83, 0xFA, 0x9A, 0x27, 0x8E, 0x49, 0xD0, 0x11,
    0x98, 0x28, 0x00, 0xC0, 0x4F, 0xC2, 0x97, 0x21
};
static void s_DirectSoundCreate(void) {                  /* (guid, &out, outer) */
    vtables();
    if (!A32(1)) RET(DSERR_INVALIDPARAM, 3);
    if (!audio_open()) { MEM32(A32(1)) = 0; RET(DSERR_NODRIVER, 3); }
    uint32_t o = hle_com_new(g_vt_ds, 12);
    hle_com_bind_iid(o, IID_DirectSound, o);
    MEM32(A32(1)) = o;
    RET(DS_OK, 3);
}
/* dsound.dll#11 / DirectSoundCreate8: IDirectSound8's vtable has one extra
 * slot (VerifyCertification) and its buffers answer IID_IDirectSoundBuffer8.
 * The guid selects a device on Windows; this host mixes into one SDL device. */
static void s_DirectSoundCreate8(void) {
    vtables();
    if (!A32(1)) RET(DSERR_INVALIDPARAM, 3);
    if (!audio_open()) { MEM32(A32(1)) = 0; RET(DSERR_NODRIVER, 3); }
    uint32_t o = hle_com_new(g_vt_ds8, 12);
    hle_com_bind_iid(o, IID_DirectSound8, o);
    MEM32(A32(1)) = o;
    RET(DS_OK, 3);
}
/* ---- driver enumeration ----
 * DSEnumCallback is `BOOL CALLBACK (LPGUID, LPSTR, LPSTR, LPVOID)`: guest stdcall,
 * four args, TRUE to continue and FALSE to stop. The strings and the GUID are
 * guest memory, and are cached here because the callback may keep them: a
 * Windows enumeration's device strings outlive the callback. Only devices SDL
 * actually reports are enumerated -- no invented "Primary Sound Driver", which
 * on a headless (dummy-driver) host would be a device that does not exist. */
#define DS_MAX_ENUM 64
typedef struct {
    uint32_t desc_a[DS_MAX_ENUM], desc_w[DS_MAX_ENUM], guid[DS_MAX_ENUM];
    uint32_t desc_hash[DS_MAX_ENUM];
    uint32_t mod_a, mod_w, mod_hash;
} ds_enum_cache;
static ds_enum_cache g_play, g_capture;                  /* one set per direction */

static uint32_t hash_text(const char *s) {
    uint32_t h = 2166136261u;
    for (; *s; s++) { h ^= (unsigned char)*s; h *= 16777619u; }
    return h;
}
static uint32_t cache_ansi(uint32_t *slot, uint32_t *hash_slot, const char *s) {
    uint32_t h = hash_text(s);
    if (*slot && *hash_slot == h) return *slot;
    uint32_t va = hle_guest_strdup(s);
    if (va) { *slot = va, *hash_slot = h; }
    return *slot;
}
static uint32_t cache_wide(uint32_t *slot, uint32_t *hash_slot, const char *s) {
    uint32_t h = hash_text(s);
    if (*slot && *hash_slot == h) return *slot;
    size_t n = strlen(s) + 1;
    if (n > 0x7FFFFFFEu / 2u) return 0;
    uint32_t need = (uint32_t)n * 2u;
    /* Reuse the guest block when a renamed device still fits in it. */
    if (!*slot || hle_heap_size(hle_ptr(*slot)) < need) {
        uint32_t va = hle_va(hle_alloc(need));
        if (!va) return 0;
        *slot = va;
    }
    uint16_t *w = (uint16_t *)hle_ptr(*slot);
    for (size_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)s[i];
    *hash_slot = h;
    return *slot;
}
static uint32_t cache_guid(uint32_t *slot, const char *name, int capture) {
    if (!*slot) {
        *slot = hle_va(hle_alloc(16));
        if (!*slot) return 0;
    }
    /* A stable GUID per (direction, SDL device name), so a caller can tell the
     * devices apart and pass one back to DirectSoundCreate as it would on
     * Windows. DirectSoundCreate ignores it: the mixer has one default device. */
    uint32_t h = 0x811C9DC5u ^ (capture ? 0x5BD1E995u : 0u);
    for (const char *p = name; *p; p++) { h ^= (unsigned char)*p; h *= 16777619u; }
    uint32_t w[4] = { h, 0, 0, 0 };
    for (int i = 1; i < 4; i++) { w[i] = h ^ (0x9E3779B9u * (uint32_t)i); w[i] ^= w[i] >> 16; w[i] *= 16777619u; }
    uint8_t *g = (uint8_t *)hle_ptr(*slot);
    memcpy(g, w, 16);
    g[7] = (uint8_t)((g[7] & 0x0F) | 0x40);              /* version 4 */
    g[8] = (uint8_t)((g[8] & 0x3F) | 0x80);              /* RFC 4122 variant */
    return *slot;
}

static uint32_t enum_devices(uint32_t cb, uint32_t ctx, int capture, int wide) {
    if (!cb) return DSERR_INVALIDPARAM;                  /* E_INVALIDARG, as Windows/Wine */
    if (!(SDL_WasInit(SDL_INIT_AUDIO) & SDL_INIT_AUDIO)) {
        if (SDL_InitSubSystem(SDL_INIT_AUDIO) != 0) {
            fprintf(stderr, "[dsound] enumerate: audio init failed (%s)\n", SDL_GetError());
            return DSERR_NODRIVER;
        }
    }
    int n = SDL_GetNumAudioDevices(capture);
    if (n < 0) n = 0;
    if (n > DS_MAX_ENUM) n = DS_MAX_ENUM;
    ds_enum_cache *c = capture ? &g_capture : &g_play;
    char module[128];
    const char *driver = SDL_GetCurrentAudioDriver();
    snprintf(module, sizeof module, "SDL2%s%s", driver && *driver ? " " : "", driver && *driver ? driver : "audio");
    for (int i = 0; i < n; i++) {
        const char *name = SDL_GetAudioDeviceName(i, capture);
        if (!name) continue;
        uint32_t a[4] = {
            cache_guid(&c->guid[i], name, capture),
            wide ? cache_wide(&c->desc_w[i], &c->desc_hash[i], name) : cache_ansi(&c->desc_a[i], &c->desc_hash[i], name),
            wide ? cache_wide(&c->mod_w, &c->mod_hash, module) : cache_ansi(&c->mod_a, &c->mod_hash, module),
            ctx
        };
        if (!hle_call_guest(cb, 4, a)) break;            /* BOOL: FALSE ends the enumeration */
    }
    return DS_OK;
}

static void s_DirectSoundEnumerateA(void) { RET(enum_devices(A32(0), A32(1), 0, 0), 2); }
static void s_DirectSoundEnumerateW(void) { RET(enum_devices(A32(0), A32(1), 0, 1), 2); }
static void s_DirectSoundCaptureEnumerateA(void) { RET(enum_devices(A32(0), A32(1), 1, 0), 2); }
static void s_DirectSoundCaptureEnumerateW(void) { RET(enum_devices(A32(0), A32(1), 1, 1), 2); }

/* ---- DirectSoundCapture8 (dsound.dll#12) ---------------------------------
 * A real capture device: SDL's capture callback fills a host ring with the
 * microphone's interleaved s16, and a capture buffer copies those frames into
 * its guest-visible WAVEMAPPED buffer as the capture cursor advances. Lock and
 * GetCurrentPosition therefore see samples actually captured on this host,
 * not a buffer that never changes. Headless SDL still supplies a capture
 * device (all zeroes), which is why no device is invented here either. */
#define CAP_FRAMES (1u << 15)
static struct {
    SDL_AudioDeviceID dev;
    int16_t *ring;                      /* CAP_FRAMES interleaved stereo frames */
    uint32_t wr;                        /* frames captured in total */
    pthread_mutex_t mtx;
} g_cap = { 0, NULL, 0, PTHREAD_MUTEX_INITIALIZER };

static void cap_mix(void *ud, Uint8 *stream, int len) {
    (void)ud;
    int frames = len / 4;
    const int16_t *s = (const int16_t *)stream;
    pthread_mutex_lock(&g_cap.mtx);
    for (int i = 0; i < frames; i++) {
        uint32_t k = (g_cap.wr + (uint32_t)i) % CAP_FRAMES;
        g_cap.ring[2 * k] = s[2 * i];
        g_cap.ring[2 * k + 1] = s[2 * i + 1];
    }
    g_cap.wr += (uint32_t)frames;
    pthread_mutex_unlock(&g_cap.mtx);
}
static int cap_open(void) {
    if (g_cap.dev) return 1;
    if (!(SDL_WasInit(SDL_INIT_AUDIO) & SDL_INIT_AUDIO) && SDL_InitSubSystem(SDL_INIT_AUDIO) != 0) return 0;
    SDL_AudioSpec want, have;
    SDL_zero(want);
    want.freq = OUT_RATE, want.format = AUDIO_S16SYS, want.channels = 2;
    want.samples = 1024, want.callback = cap_mix;
    g_cap.dev = SDL_OpenAudioDevice(NULL, 1, &want, &have, 0);
    if (!g_cap.dev) return 0;
    g_cap.ring = (int16_t *)calloc(CAP_FRAMES, 2u * sizeof(int16_t));
    if (!g_cap.ring) { SDL_CloseAudioDevice(g_cap.dev); g_cap.dev = 0; return 0; }
    SDL_PauseAudioDevice(g_cap.dev, 0);
    return 1;
}

#define MAX_CAPBUF 16
typedef struct {
    uint8_t *data;
    uint32_t bytes;
    int channels, bits, rate, block;
    int capturing, looping;
    uint32_t last_wr, cap_frames;
    pthread_mutex_t mtx;
} capbuf;
static capbuf *g_capbuf[MAX_CAPBUF];
#define CSLOT(self) MEM32((self) + 8)
static capbuf *capbuf_of(uint32_t self) { uint32_t i = CSLOT(self); return i < MAX_CAPBUF ? g_capbuf[i] : NULL; }

static void cap_sync(capbuf *b) {
    if (!g_cap.dev || !b->capturing || !b->block) return;
    uint32_t nframes = b->bytes / (uint32_t)b->block;
    if (!nframes) return;
    pthread_mutex_lock(&g_cap.mtx);
    uint32_t avail = g_cap.wr - b->last_wr;
    if (avail > nframes) avail = nframes;                /* do not overwrite unread frames */
    for (uint32_t i = 0; i < avail; i++) {
        uint32_t src = (b->last_wr + i) % CAP_FRAMES;
        uint8_t *dst = b->data + (size_t)((b->cap_frames + i) % nframes) * (size_t)b->block;
        int16_t l = g_cap.ring[2 * src], r = g_cap.ring[2 * src + 1];
        if (b->bits == 16) {
            memcpy(dst, &l, 2);
            if (b->channels > 1) memcpy(dst + 2, &r, 2);
        } else {
            dst[0] = (uint8_t)((l >> 8) + 128);
            if (b->channels > 1) dst[1] = (uint8_t)((r >> 8) + 128);
        }
    }
    b->last_wr += avail;
    b->cap_frames += avail;
    pthread_mutex_unlock(&g_cap.mtx);
}
static void cb_Release(void) {
    uint32_t self = A32(0), n = --MEM32(self + 4);
    if (!n) {
        capbuf *b = capbuf_of(self);
        g_capbuf[CSLOT(self)] = NULL;
        pthread_mutex_lock(&g_cap.mtx);
        if (b) b->capturing = 0;
        pthread_mutex_unlock(&g_cap.mtx);
        if (b) { hle_free(b->data); pthread_mutex_destroy(&b->mtx); free(b); }
        hle_com_unbind_iids(self);
        hle_com_free(self);
    }
    RET(n, 1);
}
static void cb_GetCaps(void) {
    capbuf *b = capbuf_of(A32(0));
    uint32_t c = A32(1);
    if (!b || !c || A32(2) < 16u) RET(DSERR_INVALIDPARAM, 3);
    MEM32(c) = 16, MEM32(c + 4) = 0, MEM32(c + 8) = b->bytes, MEM32(c + 12) = 0;
    RET(DS_OK, 3);
}
static void cb_GetCurrentPosition(void) {
    capbuf *b = capbuf_of(A32(0));
    if (!b || !b->block) RET(DSERR_INVALIDPARAM, 3);
    cap_sync(b);
    uint32_t nframes = b->bytes / (uint32_t)b->block;
    pthread_mutex_lock(&b->mtx);
    uint32_t capture = (uint32_t)(b->cap_frames % nframes) * (uint32_t)b->block;
    uint32_t read = b->cap_frames > nframes ? (b->cap_frames - nframes) : 0;
    pthread_mutex_unlock(&b->mtx);
    if (A32(1)) MEM32(A32(1)) = capture;
    if (A32(2)) MEM32(A32(2)) = read % b->bytes;
    RET(DS_OK, 3);
}
static void cb_GetFormat(void) {
    capbuf *b = capbuf_of(A32(0));
    uint32_t w = A32(1);
    if (!b) RET(DSERR_INVALIDPARAM, 4);
    if (w && A32(2) >= 16) {
        MEM16(w) = 1, MEM16(w + 2) = (uint16_t)b->channels, MEM32(w + 4) = (uint32_t)b->rate;
        MEM32(w + 8) = (uint32_t)(b->rate * b->block), MEM16(w + 12) = (uint16_t)b->block, MEM16(w + 14) = (uint16_t)b->bits;
        if (A32(2) >= 18) MEM16(w + 16) = 0;
    }
    if (A32(3)) MEM32(A32(3)) = 18;
    RET(DS_OK, 4);
}
static void cb_GetStatus(void) {
    capbuf *b = capbuf_of(A32(0));
    if (!b) RET(DSERR_INVALIDPARAM, 2);
    if (A32(1)) MEM32(A32(1)) = (b->capturing ? DSCBSTATUS_CAPTURING : 0u) |
                                  (b->capturing && b->looping ? DSCBSTATUS_LOOPING : 0u);
    RET(DS_OK, 2);
}
static void cb_Initialize(void) { RET(DSERR_INVALIDCALL, 3); }
static void cb_Lock(void) {
    capbuf *b = capbuf_of(A32(0));
    if (!b || !b->block) RET(DSERR_INVALIDPARAM, 8);
    cap_sync(b);
    uint32_t off = A32(1), n = A32(2), fl = A32(7);
    if (fl & DSCBLOCK_ENTIREBUFFER) off = 0, n = b->bytes;
    if (off >= b->bytes || n > b->bytes) RET(DSERR_INVALIDPARAM, 8);
    uint32_t n1 = off + n <= b->bytes ? n : b->bytes - off;
    if (A32(3)) MEM32(A32(3)) = hle_va(b->data + off);
    if (A32(4)) MEM32(A32(4)) = n1;
    if (A32(5)) MEM32(A32(5)) = n1 < n ? hle_va(b->data) : 0;
    if (A32(6)) MEM32(A32(6)) = n - n1;
    RET(DS_OK, 8);
}
static void cb_Start(void) {
    capbuf *b = capbuf_of(A32(0));
    if (!b) RET(DSERR_INVALIDPARAM, 2);
    pthread_mutex_lock(&g_cap.mtx);
    b->last_wr = g_cap.wr;
    b->cap_frames = 0;
    b->capturing = 1;
    b->looping = (A32(1) & DSCBSTART_LOOPING) != 0;
    pthread_mutex_unlock(&g_cap.mtx);
    RET(DS_OK, 2);
}
static void cb_Stop(void) {
    capbuf *b = capbuf_of(A32(0));
    if (!b) RET(DSERR_INVALIDPARAM, 1);
    pthread_mutex_lock(&g_cap.mtx); b->capturing = 0; pthread_mutex_unlock(&g_cap.mtx);
    RET(DS_OK, 1);
}
static void cb_Unlock(void) { RET(DS_OK, 5); }
static void cb8_GetObjectInPath(void) { RET(DSERR_OBJECTNOTFOUND, 5); }
static void cb8_GetFXStatus(void) { RET(DSERR_UNSUPPORTED, 3); }

static void c_CreateCaptureBuffer(void) {
    uint32_t d = A32(1), out = A32(2);
    if (!d || !out) RET(DSERR_INVALIDPARAM, 4);
    if (!cap_open()) { MEM32(out) = 0; RET(DSERR_NODRIVER, 4); }
    uint32_t bytes = MEM32(d + 8), wfx = MEM32(d + 16);
    if (!bytes) RET(DSERR_INVALIDPARAM, 4);
    capbuf *b = (capbuf *)calloc(1, sizeof *b);
    b->bytes = bytes;
    b->channels = 2, b->bits = 16, b->rate = OUT_RATE, b->block = 4;
    if (wfx) {
        b->channels = MEM16(wfx + 2), b->rate = (int)MEM32(wfx + 4);
        b->block = MEM16(wfx + 12), b->bits = MEM16(wfx + 14);
        if (MEM16(wfx) != 1 || (b->bits != 8 && b->bits != 16) ||
            b->channels < 1 || b->channels > 2 || !b->block ||
            b->block != b->channels * (b->bits / 8)) { free(b); RET(DSERR_BADFORMAT, 4); }
    }
    b->data = (uint8_t *)hle_alloc(bytes);
    pthread_mutex_init(&b->mtx, NULL);
    int slot = -1;
    for (int i = 0; i < MAX_CAPBUF; i++) if (!g_capbuf[i]) { slot = i; break; }
    if (slot < 0 || !b->data) { if (b->data) hle_free(b->data); pthread_mutex_destroy(&b->mtx); free(b); RET(DSERR_INVALIDPARAM, 4); }
    g_capbuf[slot] = b;
    uint32_t o = hle_com_new(g_vt_capbuf8, 12);
    hle_com_bind_iid(o, IID_DSCaptureBuf, o);
    hle_com_bind_iid(o, IID_DSCaptureBuf8, o);
    CSLOT(o) = (uint32_t)slot;
    MEM32(out) = o;
    RET(DS_OK, 4);
}
static void c_GetCaps(void) {
    uint32_t c = A32(1);
    if (!c || A32(2) < 16u) RET(DSERR_INVALIDPARAM, 3);
    MEM32(c) = 16, MEM32(c + 4) = 0x21u /* EMULDRIVER | MULTIPLECAPTURE */;
    MEM32(c + 8) = 0x0Fu, MEM32(c + 12) = 2;
    RET(DS_OK, 3);
}
static void c_Initialize(void) { RET(DS_OK, 2); }
static void s_DirectSoundCaptureCreate8(void) {         /* (guid, &out, outer) */
    vtables();
    if (!A32(1)) RET(DSERR_INVALIDPARAM, 3);
    if (!cap_open()) { MEM32(A32(1)) = 0; RET(DSERR_NODRIVER, 3); }
    uint32_t o = hle_com_new(g_vt_cap8, 12);
    hle_com_bind_iid(o, IID_DSCapture, o);
    MEM32(A32(1)) = o;
    RET(DS_OK, 3);
}

/* dsound.dll#9 / GetDeviceID: for an arbitrary device GUID, Windows copies it
 * to the output; for one of the four DSDEVID_Default* aliases it resolves to
 * the host's default endpoint. SDL exposes that name, so the stable GUID the
 * enumeration cache assigns to it is the honest answer here. */
static void s_GetDeviceID(void) {
    uint32_t src = A32(0), dst = A32(1);
    if (!src || !dst) RET(DSERR_INVALIDPARAM, 2);
    static const uint8_t def_play[16] = {
        0x00,0x00,0x00,0xDE,0x6D,0x9C,0xED,0x47,0xAA,0xF1,0x4D,0xDA,0x8F,0x2B,0x5C,0x03 };
    static const uint8_t def_capture[16] = {
        0x01,0x00,0x00,0xDE,0x6D,0x9C,0xED,0x47,0xAA,0xF1,0x4D,0xDA,0x8F,0x2B,0x5C,0x03 };
    static const uint8_t def_voice_play[16] = {
        0x02,0x00,0x00,0xDE,0x6D,0x9C,0xED,0x47,0xAA,0xF1,0x4D,0xDA,0x8F,0x2B,0x5C,0x03 };
    static const uint8_t def_voice_capture[16] = {
        0x03,0x00,0x00,0xDE,0x6D,0x9C,0xED,0x47,0xAA,0xF1,0x4D,0xDA,0x8F,0x2B,0x5C,0x03 };
    const uint8_t *g = hle_ptr(src);
    int capture = 0, is_default = 0;
    if (!memcmp(g, def_play, 16) || !memcmp(g, def_voice_play, 16)) is_default = 1;
    else if (!memcmp(g, def_capture, 16) || !memcmp(g, def_voice_capture, 16)) is_default = 1, capture = 1;
    if (is_default) {
        if (!(SDL_WasInit(SDL_INIT_AUDIO) & SDL_INIT_AUDIO) && SDL_InitSubSystem(SDL_INIT_AUDIO) != 0)
            RET(DSERR_NODRIVER, 2);
        const char *name = SDL_GetAudioDeviceName(0, capture);
        if (!name) RET(DSERR_NODRIVER, 2);
        ds_enum_cache *c = capture ? &g_capture : &g_play;
        uint32_t va = cache_guid(&c->guid[0], name, capture);
        if (!va) RET(DSERR_NODRIVER, 2);
        memcpy(hle_ptr(dst), hle_ptr(va), 16);
    } else {
        memcpy(hle_ptr(dst), g, 16);
    }
    RET(DS_OK, 2);
}

/* CoCreateInstance(CLSID_DirectSound): the object DirectSoundCreate makes;
 * the caller's IDirectSound::Initialize follows. */
static uint32_t ds_create(const uint8_t *iid, uint32_t *out) {
    (void)iid;
    vtables();
    if (!audio_open()) { *out = 0; return DSERR_NODRIVER; }
    *out = hle_com_new(g_vt_ds, 12);
    hle_com_bind_iid(*out, IID_DirectSound, *out);
    return DS_OK;
}
static uint32_t ds8_create(const uint8_t *iid, uint32_t *out) {
    (void)iid;
    vtables();
    if (!audio_open()) { *out = 0; return DSERR_NODRIVER; }
    *out = hle_com_new(g_vt_ds8, 12);
    hle_com_bind_iid(*out, IID_DirectSound8, *out);
    return DS_OK;
}
static uint32_t cap8_create(const uint8_t *iid, uint32_t *out) {
    (void)iid;
    vtables();
    if (!cap_open()) { *out = 0; return DSERR_NODRIVER; }
    *out = hle_com_new(g_vt_cap8, 12);
    hle_com_bind_iid(*out, IID_DSCapture, *out);
    return DS_OK;
}
void hle_dsound_register_com(void) {
    static const uint8_t CLSID_DirectSound[16] = { 0x46, 0xD9, 0xD4, 0x47, 0xE8, 0x62, 0xCF, 0x11,
                                                   0x93, 0xBC, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00 };
    static const uint8_t CLSID_DirectSound8[16] = { 0x3F, 0xCC, 0x01, 0x39, 0xB5, 0x84, 0xA4, 0x4F,
                                                    0xBA, 0x35, 0xAA, 0x81, 0x72, 0xB8, 0xA0, 0x9B };
    static const uint8_t CLSID_DirectSoundCapture8[16] = { 0x13, 0xAC, 0xBC, 0xE4, 0x99, 0x7F, 0x08, 0x49,
                                                           0x9A, 0x8E, 0x74, 0xE3, 0xBF, 0x24, 0xB6, 0xE1 };
    hle_com_register_class(CLSID_DirectSound, ds_create);
    hle_com_register_class(CLSID_DirectSound8, ds8_create);
    hle_com_register_class(CLSID_DirectSoundCapture8, cap8_create);
}

const win32hle_shim win32hle_dsound[] = {
    { "dsound.dll#1", s_DirectSoundCreate }, { "DirectSoundCreate", s_DirectSoundCreate },
    { "dsound.dll#2", s_DirectSoundEnumerateA }, { "DirectSoundEnumerateA", s_DirectSoundEnumerateA },
    /* Microsoft's dsound.dll ordinals: 3 = EnumerateW, 7/8 = capture A/W. */
    { "dsound.dll#3", s_DirectSoundEnumerateW }, { "DirectSoundEnumerateW", s_DirectSoundEnumerateW },
    { "dsound.dll#7", s_DirectSoundCaptureEnumerateA }, { "DirectSoundCaptureEnumerateA", s_DirectSoundCaptureEnumerateA },
    { "dsound.dll#8", s_DirectSoundCaptureEnumerateW }, { "DirectSoundCaptureEnumerateW", s_DirectSoundCaptureEnumerateW },
    /* Wine's dsound.spec: 9 = GetDeviceID, 11 = DirectSoundCreate8,
     * 12 = DirectSoundCaptureCreate8. TrackMania imports ordinal 12. */
    { "dsound.dll#9", s_GetDeviceID }, { "GetDeviceID", s_GetDeviceID },
    { "dsound.dll#11", s_DirectSoundCreate8 }, { "DirectSoundCreate8", s_DirectSoundCreate8 },
    { "dsound.dll#12", s_DirectSoundCaptureCreate8 }, { "DirectSoundCaptureCreate8", s_DirectSoundCaptureCreate8 },
    { 0, 0 }
};
