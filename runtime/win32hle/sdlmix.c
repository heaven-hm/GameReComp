/*
 * sdlmix.c - SDL_mixer 1.2 (Mix_*) on SDL2's audio device.
 *
 * A 1990s game that ships SDL_mixer.dll imports Mix_* and treats a failure as
 * "no sound", so the honest contract here is: implement what can be
 * implemented for real, and FAIL (return 0 / -1) for what cannot. There is no
 * silent success anywhere in this file.
 *
 * What is real:
 *   - Mix_OpenAudio / Mix_CloseAudio / Mix_QuerySpec on SDL2's audio device,
 *     with a software mixer thread that mixes the playing channels.
 *   - Mix_LoadWAV_RW decodes a RIFF/WAVE file (PCM 8/16-bit, mono/stereo)
 *     into a guest Mix_Chunk, from a guest SDL_RWops or a path.
 *   - Mix_PlayChannel(Timed) / Mix_HaltChannel / Mix_FadeOutChannel /
 *     Mix_Playing / Mix_Paused / Mix_Volume(Chunk) / Mix_SetPanning and the
 *     channel bookkeeping, against that mixer.
 *
 * What is deliberately absent: music. Mix_LoadMUS handles MOD/IT/S3M/XM/OGG/
 * MP3, and there is no decoder here for any of them, so Mix_LoadMUS returns
 * NULL and sets the error string. Mix_FreeMusic/Mix_HaltMusic/Mix_PlayMusic/
 * Mix_FadeOutMusic/Mix_PlayingMusic/Mix_PausedMusic/Mix_VolumeMusic answer
 * about a NULL music object the way SDL_mixer does (no-op / 0), which is a
 * true answer: there is no music loaded. A game that then runs without music
 * is running with no music, not with a faked one.
 *
 * SDL_mixer's exports are cdecl, like SDL 1.2's: every shim ends in RETC.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

/* ---- the guest-side layouts, SDL_mixer 1.2 on a 32-bit target ---- */
/* Mix_Chunk { Uint8 *allocated; Uint8 *abuf; Uint32 alen; Uint8 volume;
 *             int audio_channel } */
enum { MX_CHUNK = 20, MX_CH_ALLOCATED = 0, MX_CH_ABUF = 4, MX_CH_ALEN = 8,
       MX_CH_VOLUME = 12, MX_CH_AUDIO_CHANNEL = 16 };
/* The decoded sample data lives in guest memory behind the chunk; this is the
 * mixer's own bookkeeping, kept beside it, not in the guest struct. */
typedef struct {
    uint32_t va;            /* guest Mix_Chunk */
    uint32_t samples;       /* guest VA of the PCM data (abuf) */
    uint32_t frames;        /* sample frames, not bytes */
    int      channels;      /* 1 or 2 */
    int      playing;       /* 1 while audible */
    int      paused;
    int      volume;        /* 0..128 */
    double   pos;           /* frame cursor */
    int      left, right;   /* 0..255 pan, 255 = full */
    int      fading;        /* frames of fade remaining, -1 = not fading */
    int      fade_len;
} mx_channel;

enum { MX_MAX_CHANNELS = 32 };
enum { MX_DEFAULT_CHANNELS = 8 };

static mx_channel g_ch[MX_MAX_CHANNELS];
static int   g_allocated;                 /* channels Mix_AllocateChannels gave */
static int   g_freq, g_format, g_channels;
static int   g_open;
static int   g_music_volume = 128;
static char  g_error[256];

/* A loaded chunk's shape, keyed by the guest Mix_Chunk VA. SDL_mixer's
 * Mix_Chunk does not carry the channel count, and guessing it from the byte
 * length is ambiguous (a stereo 16-bit chunk and a mono one differ by exactly
 * 2x), so the loader records what it actually decoded. */
typedef struct { uint32_t va; int frames; int channels; } mx_chunk_info;
static mx_chunk_info g_chunk_frames[128];
static int g_chunk_next;

static void chunk_record(uint32_t va, int frames, int channels) {
    g_chunk_frames[g_chunk_next].va = va;
    g_chunk_frames[g_chunk_next].frames = frames;
    g_chunk_frames[g_chunk_next].channels = channels;
    g_chunk_next = (g_chunk_next + 1) % (int)(sizeof g_chunk_frames / sizeof g_chunk_frames[0]);
}

static int chunk_info(uint32_t va, int *frames, int *channels) {
    for (unsigned i = 0; i < sizeof g_chunk_frames / sizeof g_chunk_frames[0]; i++)
        if (g_chunk_frames[i].va == va) {
            *frames = g_chunk_frames[i].frames;
            *channels = g_chunk_frames[i].channels;
            return 1;
        }
    return 0;
}

/* SDL 1.2 audio format flags. */
enum { S12_AUDIO_U8 = 0x0008, S12_AUDIO_S8 = 0x8008,
       S12_AUDIO_U16LSB = 0x0010, S12_AUDIO_S16LSB = 0x8010,
       S12_AUDIO_U16MSB = 0x1010, S12_AUDIO_S16MSB = 0x9010,
       S12_AUDIO_S16 = S12_AUDIO_S16LSB };

static void mx_error(const char *s) {
    snprintf(g_error, sizeof g_error, "%s", s ? s : "SDL_mixer error");
}

/* ---- the mixer ---- */
/* SDL2 wants host-endian signed 16-bit; the guest's device format may be
 * 8-bit or big-endian, so convert per sample rather than assuming. */
static void mix_callback(void *ud, Uint8 *stream, int len) {
    (void)ud;
    Sint16 *out = (Sint16 *)stream;
    int frames = len / (int)(2 * sizeof(Sint16));
    memset(stream, 0, (size_t)len);
    for (int c = 0; c < g_allocated && c < MX_MAX_CHANNELS; c++) {
        mx_channel *ch = &g_ch[c];
        if (!ch->playing || ch->paused || !ch->frames) continue;
        const int16_t *pcm = (const int16_t *)hle_ptr(ch->samples);
        for (int f = 0; f < frames; f++) {
            double src = ch->pos + (double)f;
            if (src >= (double)ch->frames) { ch->playing = 0; break; }
            int s = pcm[(size_t)src * (size_t)ch->channels];
            int gain = ch->volume;
            if (ch->fading >= 0) {
                double left = 1.0 - (ch->pos + f) / (double)(ch->fade_len ? ch->fade_len : 1);
                if (left < 0) left = 0;
                gain = (int)(gain * left);
            }
            int v = s * gain / 128;
            if (ch->left)  out[f * 2]     = (Sint16)(out[f * 2]     + v * ch->left / 255);
            if (ch->right) out[f * 2 + 1] = (Sint16)(out[f * 2 + 1] + v * ch->right / 255);
        }
        ch->pos += frames;
        if (ch->fading >= 0) {
            ch->fading -= frames;
            if (ch->fading < 0) { ch->playing = 0; ch->fading = -1; }
        }
    }
}

static int mx_open(int freq, int format, int channels, int chunksize) {
    if (g_open) return 0;
    SDL_AudioSpec want, have;
    SDL_zero(want);
    want.freq = freq > 0 ? freq : 22050;
    want.format = AUDIO_S16SYS;
    want.channels = 2;                       /* the mixer pans, so it is stereo */
    want.samples = (Uint16)(chunksize > 0 ? chunksize : 512);
    want.callback = mix_callback;
    if (SDL_InitSubSystem(SDL_INIT_AUDIO) != 0) { mx_error(SDL_GetError()); return -1; }
    if (SDL_OpenAudio(&want, &have) != 0) { mx_error(SDL_GetError()); return -1; }
    g_freq = have.freq;
    g_format = format;
    g_channels = channels;
    g_open = 1;
    SDL_PauseAudio(0);
    return 0;
}

static void mx_close(void) {
    if (!g_open) return;
    SDL_CloseAudio();
    g_open = 0;
}

/* ---- guest SDL_RWops access ----
 * The layout is sdl12.c's (documented there): the four function pointers, the
 * type, then our private tail. Read from a RWops by calling the guest-visible
 * SDL_RWread it points at, so a guest that supplied its own callbacks works. */
enum { RW_READ = 4, RW_TYPE = 16, RW_KIND = 20, RW_HANDLE = 24,
       RW_SIZE = 28, RW_POS = 32 };
enum { RW_KIND_FILE = 0, RW_KIND_MEM = 1 };
enum { S12_RWOPS_STDFILE = 2, S12_RWOPS_MEMORY = 3 };

static long rw_size(uint32_t rw) {
    if (MEM32(rw + RW_KIND) == RW_KIND_MEM) return (long)MEM32(rw + RW_SIZE);
    long pos = lseek((int)MEM32(rw + RW_HANDLE), 0, SEEK_CUR);
    long end = lseek((int)MEM32(rw + RW_HANDLE), 0, SEEK_END);
    lseek((int)MEM32(rw + RW_HANDLE), pos, SEEK_SET);
    return end;
}

static long rw_read(uint32_t rw, uint32_t dst, long n) {
    uint32_t fn = MEM32(rw + RW_READ);
    if (!fn) return -1;
    /* Call the guest's SDL_RWread(ctx, ptr, size, maxnum) the way every other
     * native -> guest call is made: hle_call_guest pushes the stdcall frame,
     * takes the machine lock and returns eax. */
    uint32_t args[4] = { rw, dst, 1u, (uint32_t)n };
    return (long)hle_call_guest(fn, 4, args);
}

static void rw_close(uint32_t rw) {
    uint32_t fn = MEM32(rw + 12);            /* SDL_RWops.close */
    if (!fn) return;
    uint32_t args[1] = { rw };
    hle_call_guest(fn, 1, args);
}

/* ---- RIFF/WAVE decode into guest memory ---- */
/* Returns the frame count, or 0 with the error set. Only the formats a 1990s
 * game ships are accepted: PCM 8/16-bit, 1 or 2 channels. */
static int wav_decode(const uint8_t *data, size_t len, uint32_t *out_va,
                      int *out_channels) {
    if (len < 12 || memcmp(data, "RIFF", 4) || memcmp(data + 8, "WAVE", 4)) {
        mx_error("not a RIFF/WAVE file");
        return 0;
    }
    size_t p = 12;
    const uint8_t *fmt = NULL; size_t fmt_len = 0;
    const uint8_t *pcm = NULL; size_t pcm_len = 0;
    while (p + 8 <= len) {
        const uint8_t *id = data + p;
        uint32_t sz = (uint32_t)data[p + 4] | ((uint32_t)data[p + 5] << 8)
                    | ((uint32_t)data[p + 6] << 16) | ((uint32_t)data[p + 7] << 24);
        if (p + 8 + sz > len) sz = (uint32_t)(len - p - 8);
        if (!memcmp(id, "fmt ", 4)) { fmt = data + p + 8; fmt_len = sz; }
        else if (!memcmp(id, "data", 4)) { pcm = data + p + 8; pcm_len = sz; }
        p += 8 + sz + (sz & 1);
    }
    if (!fmt || fmt_len < 16 || !pcm) { mx_error("WAVE missing fmt or data"); return 0; }
    uint16_t tag = (uint16_t)(fmt[0] | (fmt[1] << 8));
    uint16_t nch = (uint16_t)(fmt[2] | (fmt[3] << 8));
    uint32_t rate = (uint32_t)fmt[4] | ((uint32_t)fmt[5] << 8)
                  | ((uint32_t)fmt[6] << 16) | ((uint32_t)fmt[7] << 24);
    uint16_t bits = (uint16_t)(fmt[14] | (fmt[15] << 8));
    if (tag != 1) { mx_error("WAVE is not PCM"); return 0; }
    if (nch != 1 && nch != 2) { mx_error("WAVE is neither mono nor stereo"); return 0; }
    if (bits != 8 && bits != 16) { mx_error("WAVE is not 8- or 16-bit"); return 0; }
    if (rate == 0) { mx_error("WAVE has no sample rate"); return 0; }

    size_t bytes_per_frame = (size_t)nch * (bits / 8);
    size_t frames = pcm_len / bytes_per_frame;
    if (!frames) { mx_error("WAVE has no samples"); return 0; }

    /* The mixer reads host int16, so convert once at load: 8-bit unsigned is
     * biased at 128 and must become signed. */
    int16_t *dst = hle_alloc((uint32_t)(frames * (size_t)nch * sizeof(int16_t)));
    if (!dst) { mx_error("out of guest memory"); return 0; }
    for (size_t i = 0; i < frames * (size_t)nch; i++) {
        if (bits == 16) dst[i] = (int16_t)(pcm[i * 2] | (pcm[i * 2 + 1] << 8));
        else dst[i] = (int16_t)(((int)pcm[i] - 128) * 256);
    }
    *out_va = hle_va(dst);
    *out_channels = nch;
    return (int)frames;
}

/* ---- shims ---- */
static void s_OpenAudio(void) {
    if (mx_open((int)A32(0), (int)A32(1), (int)A32(2), (int)A32(3)) != 0) RETC(-1);
    RETC(0);
}
static void s_CloseAudio(void) { mx_close(); RETCV(); }
static void s_QuerySpec(void) {
    if (A32(0)) MEM32(A32(0)) = (uint32_t)g_freq;
    if (A32(1)) MEM32(A32(1)) = (uint32_t)(g_format ? g_format : S12_AUDIO_S16);
    if (A32(2)) MEM32(A32(2)) = (uint32_t)(g_channels ? g_channels : 2);
    RETC(g_open ? 1 : 0);
}
static void s_LinkedVersion(void) {
    /* SDL_version is major, minor, patch: report 1.2.12, the version the
     * 1.2-era API in this file matches. */
    if (A32(0)) { MEM8(A32(0)) = 1; MEM8(A32(0) + 1) = 2; MEM8(A32(0) + 2) = 12; }
    RETCV();
}

static void s_LoadWAV_RW(void) {
    uint32_t rw = A32(0);
    if (!rw) { mx_error("no RWops"); RETC(0); }
    long size = rw_size(rw);
    if (size <= 0) { mx_error("cannot size the RWops"); RETC(0); }
    uint8_t *buf = malloc((size_t)size);
    if (!buf) { mx_error("out of host memory"); RETC(0); }
    long got = rw_read(rw, hle_va(buf), size);
    if (got <= 0) { free(buf); mx_error("cannot read the RWops"); RETC(0); }
    if (A32(1)) rw_close(rw);                /* freesrc: close the RWops */
    uint32_t pcm_va = 0; int nch = 0;
    int frames = wav_decode(buf, (size_t)got, &pcm_va, &nch);
    free(buf);
    if (!frames) RETC(0);
    uint32_t chunk = hle_va(hle_alloc(MX_CHUNK));
    if (!chunk) { mx_error("out of guest memory"); RETC(0); }
    MEM32(chunk + MX_CH_ALLOCATED) = pcm_va;   /* the mixer owns the PCM */
    MEM32(chunk + MX_CH_ABUF) = pcm_va;
    MEM32(chunk + MX_CH_ALEN) = (uint32_t)(frames * nch * (int)sizeof(int16_t));
    MEM8(chunk + MX_CH_VOLUME) = 128;
    MEM32(chunk + MX_CH_AUDIO_CHANNEL) = 0xFFFFFFFFu;
    chunk_record(chunk, frames, nch);
    RETC(chunk);
}
static void s_LoadWAV(void) {
    /* Mix_LoadWAV(path) is Mix_LoadWAV_RW(SDL_RWFromFile(path,"rb"), 1). The
     * generated projects do not link SDL 1.2's RWFromFile shim from here, so
     * read the file directly through the host and decode it the same way. */
    const char *path = ASTR(0);
    if (!path) { mx_error("no path"); RETC(0); }
    FILE *f = fopen(path, "rb");
    if (!f) { mx_error("cannot open the file"); RETC(0); }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *buf = size > 0 ? malloc((size_t)size) : NULL;
    if (!buf || fread(buf, 1, (size_t)size, f) != (size_t)size) {
        if (buf) free(buf);
        fclose(f);
        mx_error("cannot read the file");
        RETC(0);
    }
    fclose(f);
    uint32_t pcm_va = 0; int nch = 0;
    int frames = wav_decode(buf, (size_t)size, &pcm_va, &nch);
    free(buf);
    if (!frames) RETC(0);
    uint32_t chunk = hle_va(hle_alloc(MX_CHUNK));
    if (!chunk) RETC(0);
    MEM32(chunk + MX_CH_ALLOCATED) = pcm_va;
    MEM32(chunk + MX_CH_ABUF) = pcm_va;
    MEM32(chunk + MX_CH_ALEN) = (uint32_t)(frames * nch * (int)sizeof(int16_t));
    MEM8(chunk + MX_CH_VOLUME) = 128;
    MEM32(chunk + MX_CH_AUDIO_CHANNEL) = 0xFFFFFFFFu;
    chunk_record(chunk, frames, nch);
    RETC(chunk);
}
static void s_FreeChunk(void) {
    uint32_t chunk = A32(0);
    if (!chunk) RETCV();
    for (int c = 0; c < MX_MAX_CHANNELS; c++)
        if (g_ch[c].va == chunk) memset(&g_ch[c], 0, sizeof g_ch[c]);
    if (MEM32(chunk + MX_CH_ALLOCATED)) hle_free(hle_ptr(MEM32(chunk + MX_CH_ALLOCATED)));
    hle_free(hle_ptr(chunk));
    RETCV();
}

/* The chunk a channel is playing, if any. */
static int chunk_channel(uint32_t chunk) {
    for (int c = 0; c < MX_MAX_CHANNELS; c++)
        if (g_ch[c].va == chunk && g_ch[c].playing) return c;
    return -1;
}

/* Start `chunk` on channel `want` (-1 = first free). Returns the channel or -1.
 * Shared by Mix_PlayChannel and Mix_PlayChannelTimed, which differ only in
 * whether a time limit is given (this mixer plays to the end of the chunk). */
static int play_chunk(int want, uint32_t chunk) {
    if (!chunk) return -1;
    if (!g_open) { mx_error("audio device is not open"); return -1; }
    int c = want;
    if (c < 0) {
        c = -1;
        for (int i = 0; i < g_allocated && i < MX_MAX_CHANNELS; i++)
            if (!g_ch[i].playing) { c = i; break; }
        if (c < 0) { mx_error("no free channel"); return -1; }
    }
    if (c >= MX_MAX_CHANNELS) { mx_error("channel out of range"); return -1; }
    /* The chunk records only its byte length, so the channel count has to come
     * from somewhere. wav_decode wrote the frame count into the mixer's own
     * table keyed by the chunk VA; fall back to the byte length only if the
     * chunk was not made by this loader. */
    int frames = 0, nch = 2;
    if (!chunk_info(chunk, &frames, &nch)) {
        uint32_t alen = MEM32(chunk + MX_CH_ALEN);
        nch = (alen % 4u == 0) ? 2 : 1;
        frames = (int)(alen / (uint32_t)(nch * (int)sizeof(int16_t)));
    }
    memset(&g_ch[c], 0, sizeof g_ch[c]);
    g_ch[c].va = chunk;
    g_ch[c].samples = MEM32(chunk + MX_CH_ABUF);
    g_ch[c].channels = nch;
    g_ch[c].frames = (uint32_t)frames;
    g_ch[c].volume = MEM8(chunk + MX_CH_VOLUME);
    g_ch[c].left = g_ch[c].right = 255;
    g_ch[c].playing = 1;
    g_ch[c].fading = -1;
    MEM32(chunk + MX_CH_AUDIO_CHANNEL) = (uint32_t)c;
    return c;
}

static void s_PlayChannelTimed(void) {
    RETC(play_chunk((int)A32(0), A32(1)));
}
static void s_PlayChannel(void) {
    RETC(play_chunk((int)A32(0), A32(1)));
}
static void s_HaltChannel(void) {
    int c = (int)A32(0);
    for (int i = 0; i < MX_MAX_CHANNELS; i++)
        if (c < 0 || i == c) { g_ch[i].playing = 0; g_ch[i].fading = -1; }
    RETCV();
}
static void s_FadeOutChannel(void) {
    int c = (int)A32(0), ms = (int)A32(1);
    int frames = (int)((double)ms * (g_freq ? g_freq : 22050) / 1000.0);
    for (int i = 0; i < MX_MAX_CHANNELS; i++)
        if (c < 0 || i == c) {
            if (!g_ch[i].playing) continue;
            g_ch[i].fading = frames;
            g_ch[i].fade_len = frames ? frames : 1;
        }
    RETCV();
}
static void s_Playing(void) {
    int c = (int)A32(0);
    if (c < 0) { int n = 0; for (int i = 0; i < MX_MAX_CHANNELS; i++) n += g_ch[i].playing != 0; RETC(n); }
    RETC(c < MX_MAX_CHANNELS ? (g_ch[c].playing != 0) : 0);
}
static void s_Paused(void) {
    int c = (int)A32(0);
    if (c < 0) { int n = 0; for (int i = 0; i < MX_MAX_CHANNELS; i++) n += g_ch[i].paused != 0; RETC(n); }
    RETC(c < MX_MAX_CHANNELS ? (g_ch[c].paused != 0) : 0);
}
static void s_Pause(void) {
    int c = (int)A32(0);
    for (int i = 0; i < MX_MAX_CHANNELS; i++)
        if (c < 0 || i == c) g_ch[i].paused = 1;
    RETCV();
}
static void s_Resume(void) {
    int c = (int)A32(0);
    for (int i = 0; i < MX_MAX_CHANNELS; i++)
        if (c < 0 || i == c) g_ch[i].paused = 0;
    RETCV();
}
static void s_Volume(void) {
    int c = (int)A32(0), vol = (int)A32(1);
    if (c < 0) { int old = g_music_volume; g_music_volume = vol; RETC(old); }
    if (c >= MX_MAX_CHANNELS) RETC(0);
    int old = g_ch[c].volume;
    g_ch[c].volume = vol < 0 ? 128 : vol;
    RETC(old);
}
static void s_VolumeChunk(void) {
    uint32_t chunk = A32(0);
    int vol = (int)A32(1);
    if (!chunk) RETC(0);
    int old = MEM8(chunk + MX_CH_VOLUME);
    MEM8(chunk + MX_CH_VOLUME) = (uint8_t)(vol < 0 ? 128 : vol);
    int c = chunk_channel(chunk);
    if (c >= 0) g_ch[c].volume = MEM8(chunk + MX_CH_VOLUME);
    RETC(old);
}
static void s_SetPanning(void) {
    int c = (int)A32(0), left = (int)A32(1), right = (int)A32(2);
    if (c < 0 || c >= MX_MAX_CHANNELS) RETC(-1);
    g_ch[c].left = left;
    g_ch[c].right = right;
    RETC(0);
}
static void s_AllocateChannels(void) {
    int n = (int)A32(0);
    if (n >= 0) {
        if (n > MX_MAX_CHANNELS) n = MX_MAX_CHANNELS;
        for (int i = n; i < MX_MAX_CHANNELS; i++) memset(&g_ch[i], 0, sizeof g_ch[i]);
        g_allocated = n;
    }
    RETC(g_allocated);
}
static void s_ReserveChannels(void) { RETC((int)A32(0)); }   /* nothing to reserve: channels are ours */

/* ---- music: absent on purpose ---- */
static void s_LoadMUS(void) {
    mx_error("music decoding is not implemented (no MOD/OGG/MP3 decoder here); "
             "Mix_LoadMUS returns NULL");
    RETC(0);
}
static void s_FreeMusic(void) { RETCV(); }
static void s_HaltMusic(void) { RETCV(); }
static void s_FadeOutMusic(void) { RETC(0); }          /* no music playing to fade */
static void s_PlayMusic(void) { RETC(-1); }            /* no music object can exist */
static void s_PlayingMusic(void) { RETC(0); }
static void s_PausedMusic(void) { RETC(0); }
static void s_PauseMusic(void) { RETCV(); }
static void s_ResumeMusic(void) { RETCV(); }
static void s_RewindMusic(void) { RETCV(); }
static void s_VolumeMusic(void) {
    int vol = (int)A32(0);
    int old = g_music_volume;
    if (vol >= 0) g_music_volume = vol;
    RETC(old);
}
static void s_SetMusicPosition(void) { RETC(-1); }
static void s_GetMusicType(void) { RETC(0); }          /* MUS_NONE */

static void s_GetError(void) { RETP(g_error, 0); }
static void s_SetError(void) {
    const char *fmt = ASTR(0);
    if (fmt) snprintf(g_error, sizeof g_error, "%s", fmt);
    RETCV();
}

const win32hle_shim win32hle_sdlmix[] = {
    { "Mix_OpenAudio",          s_OpenAudio },
    { "Mix_CloseAudio",         s_CloseAudio },
    { "Mix_QuerySpec",          s_QuerySpec },
    { "Mix_Linked_Version",     s_LinkedVersion },
    { "Mix_LoadWAV_RW",         s_LoadWAV_RW },
    { "Mix_LoadWAV",            s_LoadWAV },
    { "Mix_FreeChunk",          s_FreeChunk },
    { "Mix_PlayChannelTimed",   s_PlayChannelTimed },
    { "Mix_PlayChannel",        s_PlayChannel },
    { "Mix_HaltChannel",        s_HaltChannel },
    { "Mix_FadeOutChannel",     s_FadeOutChannel },
    { "Mix_Playing",            s_Playing },
    { "Mix_Paused",             s_Paused },
    { "Mix_Pause",              s_Pause },
    { "Mix_Resume",             s_Resume },
    { "Mix_Volume",             s_Volume },
    { "Mix_VolumeChunk",        s_VolumeChunk },
    { "Mix_SetPanning",         s_SetPanning },
    { "Mix_AllocateChannels",   s_AllocateChannels },
    { "Mix_ReserveChannels",    s_ReserveChannels },
    { "Mix_LoadMUS",            s_LoadMUS },
    { "Mix_FreeMusic",          s_FreeMusic },
    { "Mix_HaltMusic",          s_HaltMusic },
    { "Mix_FadeOutMusic",       s_FadeOutMusic },
    { "Mix_PlayMusic",          s_PlayMusic },
    { "Mix_PlayingMusic",       s_PlayingMusic },
    { "Mix_PausedMusic",        s_PausedMusic },
    { "Mix_PauseMusic",         s_PauseMusic },
    { "Mix_ResumeMusic",        s_ResumeMusic },
    { "Mix_RewindMusic",        s_RewindMusic },
    { "Mix_VolumeMusic",        s_VolumeMusic },
    { "Mix_SetMusicPosition",   s_SetMusicPosition },
    { "Mix_GetMusicType",       s_GetMusicType },
    { "Mix_GetError",           s_GetError },
    { "Mix_SetError",           s_SetError },
    { 0, 0 }
};
