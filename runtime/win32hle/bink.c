/*
 * bink.c - BINKW32 (RAD's Bink movie player, the 1.x API) on ffmpeg's Bink
 * decoder.
 *
 * A game links binkw32.dll and calls it by its decorated names
 * (_BinkOpen@8, ...); the DLL's code is not lifted, so its API is answered
 * here: libavformat reads the .bik (a file name, or with BINKFILEHANDLE a
 * file handle the game positioned itself, at a movie inside an archive),
 * libavcodec decodes its video and audio, BinkCopyToBuffer converts a frame
 * into the game's surface, and the sound goes to the DirectSound mixer as a
 * stream (dsound.c), started with the movie and paced by its frame rate.
 *
 * The handle the game gets is a BINK struct whose leading fields are
 * Bink's own (Width, Height, Frames, FrameNum, LastFrameNum, FrameRate,
 * FrameRateDiv, ...): games read them directly. Without HLE_WITH_FFMPEG,
 * BinkOpen fails, as with a movie that is not there.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include "win32hle.h"

#define BINKFILEHANDLE 0x00800000u

/* BinkGetError's string. It used to be a single global pointer with no version,
 * so two different messages stored in the same buffer would be strdup'd only
 * once. The generation makes every change visible to the guest. */
static char g_error_buf[256];
static const char *g_error = "";
static uint32_t g_error_gen;
static void set_error(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_error_buf, sizeof g_error_buf, fmt, ap);
    va_end(ap);
    g_error = g_error_buf;
    g_error_gen++;
}
static void k_GetError(void) {                           /* a copy in guest memory, made once per message */
    static const char *copied_from;
    static uint32_t copied_gen, va;
    if (copied_from != g_error || copied_gen != g_error_gen) {
        va = hle_guest_strdup(g_error);
        copied_from = g_error, copied_gen = g_error_gen;
    }
    RET(va, 0);
}
static void k_SetSoundSystem(void) { RET(1, 2); }        /* (open, param): DirectSound, which is ours */
static void k_OpenDirectSound(void) { RET(0, 1); }
/* BinkDDSurfaceType(surface): the BINKSURFACE type of a DirectDraw surface */
static void k_DDSurfaceType(void) {
    uint8_t *px;
    int w, h, pitch, bpp;
    if (!hle_dd_surface_pixels(A32(0), &px, &w, &h, &pitch, &bpp)) RET(0xFFFFFFFFu, 1);
    RET(bpp == 16 ? 10u /* 565 */ : bpp == 32 ? 3u /* 32 */ : bpp == 24 ? 1u : 0u, 1);
}

/* ---- Bink file-header probe (shared by the stub and FFmpeg paths) ----
 * The leading bytes of a Bink file are (all little-endian):
 *   0..3   signature "BIK"+revision, or "KB2"+revision (Bink 2)
 *   4..7   file size - 8
 *   8..11  frame count
 *   12..15 largest frame size
 *   16..19 unused
 *   20..23 width
 *   24..27 height
 *   28..31 fps numerator
 *   32..35 fps denominator
 * (libavformat/bink.c's probe and read_header; a Smush-wrapped movie repeats a
 * 512-byte block until the BIK signature.)  Checking these fields here gives a
 * truncated or mislabelled file a specific message even on a host with no
 * decoder. It is metadata validation only: a header that passes is NOT evidence
 * that any frame can be decoded, and no playback success is claimed.
 */
#define BINK_PROBE_HDR_BYTES 36u
#define BINK_SMUSH_BLOCK     512u
#define BINK_MAX_FRAMES      1000000u
#define BINK_MAX_WIDTH       7680u
#define BINK_MAX_HEIGHT      4800u

typedef struct {
    uint32_t width, height, frames, fps_num, fps_den;
    uint32_t declared_size;                          /* bytes the header claims */
    uint32_t header_off;                             /* BIK inside a Smush block */
} bink_probe_info_t;

enum {
    BINK_PROBE_OK = 0,
    BINK_PROBE_TRUNCATED_HEADER,
    BINK_PROBE_NOT_BINK,
    BINK_PROBE_BINK2_UNSUPPORTED,
    BINK_PROBE_BAD_SIZE,
    BINK_PROBE_TRUNCATED_BODY,
    BINK_PROBE_BAD_FRAMES,
    BINK_PROBE_BAD_FRAME_SIZE,
    BINK_PROBE_BAD_DIMENSIONS,
    BINK_PROBE_BAD_FPS
};

static uint32_t probe_rd32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static int probe_signature(const uint8_t *h, uint32_t n, uint32_t *off) {
    *off = 0;
    if (n < 3) return 0;
    if (n >= 4 && !memcmp(h, "SMUS", 4)) {
        for (uint32_t o = BINK_SMUSH_BLOCK; o + 4 <= n && o <= BINK_SMUSH_BLOCK * 16u; o += BINK_SMUSH_BLOCK)
            if (!memcmp(h + o, "BIK", 3) || !memcmp(h + o, "KB2", 3)) { *off = o; return 1; }
        return 0;
    }
    return !memcmp(h, "BIK", 3) || !memcmp(h, "KB2", 3);
}

/* n: bytes available in h. file_size: actual bytes, or 0 when unknown. */
int hle_bink_probe(const uint8_t *h, uint32_t n, uint64_t file_size, bink_probe_info_t *out) {
    uint32_t off;
    if (!h || !probe_signature(h, n, &off)) return BINK_PROBE_NOT_BINK;
    if (n < off || n - off < BINK_PROBE_HDR_BYTES) return BINK_PROBE_TRUNCATED_HEADER;
    const uint8_t *b = h + off;
    if (!memcmp(b, "KB2", 3)) return BINK_PROBE_BINK2_UNSUPPORTED; /* FFmpeg: "Bink 2 video is not implemented" */

    uint32_t declared = probe_rd32(b + 4) + 8u;
    if (declared < BINK_PROBE_HDR_BYTES) return BINK_PROBE_BAD_SIZE;
    if (file_size && declared > file_size) return BINK_PROBE_TRUNCATED_BODY;

    uint32_t frames = probe_rd32(b + 8), largest = probe_rd32(b + 12);
    uint32_t width = probe_rd32(b + 20), height = probe_rd32(b + 24);
    uint32_t fps_num = probe_rd32(b + 28), fps_den = probe_rd32(b + 32);
    if (!frames || frames > BINK_MAX_FRAMES) return BINK_PROBE_BAD_FRAMES;
    if (largest > declared) return BINK_PROBE_BAD_FRAME_SIZE;
    if (!width || width > BINK_MAX_WIDTH || !height || height > BINK_MAX_HEIGHT) return BINK_PROBE_BAD_DIMENSIONS;
    if (!fps_num || !fps_den) return BINK_PROBE_BAD_FPS;

    if (out) {
        out->width = width, out->height = height, out->frames = frames;
        out->fps_num = fps_num, out->fps_den = fps_den;
        out->declared_size = declared, out->header_off = off;
    }
    return BINK_PROBE_OK;
}

const char *hle_bink_probe_error(int st) {
    switch (st) {
    case BINK_PROBE_TRUNCATED_HEADER:    return "truncated Bink header (fewer than 36 bytes)";
    case BINK_PROBE_NOT_BINK:            return "not a Bink movie (no BIK signature)";
    case BINK_PROBE_BINK2_UNSUPPORTED:   return "unsupported Bink 2 (KB2) movie: no video decoder";
    case BINK_PROBE_BAD_SIZE:            return "invalid Bink header: declared movie size is smaller than the header";
    case BINK_PROBE_TRUNCATED_BODY:      return "truncated Bink movie: file is shorter than the size in its header";
    case BINK_PROBE_BAD_FRAMES:          return "invalid Bink header: frame count is 0 or over 1000000";
    case BINK_PROBE_BAD_FRAME_SIZE:      return "invalid Bink header: largest frame exceeds the declared movie size";
    case BINK_PROBE_BAD_DIMENSIONS:      return "invalid Bink header: video dimensions are 0 or over 7680x4800";
    case BINK_PROBE_BAD_FPS:             return "invalid Bink header: frame rate has a zero numerator or denominator";
    default:                             return "invalid Bink header";
    }
}

/* pread, so probing a BINKFILEHANDLE does not move the game's file position. */
static int probe_fd(int fd, bink_probe_info_t *info) {
    uint8_t hdr[BINK_SMUSH_BLOCK * 4u + BINK_PROBE_HDR_BYTES + 8u] = { 0 };
    off_t cur = lseek(fd, 0, SEEK_CUR);
    off_t end = lseek(fd, 0, SEEK_END);
    if (cur >= 0) lseek(fd, cur, SEEK_SET);
    if (cur < 0 || end < cur) return BINK_PROBE_TRUNCATED_HEADER;
    uint64_t span = (uint64_t)(end - cur);
    size_t want = span < sizeof hdr ? (size_t)span : sizeof hdr;
    ssize_t got = pread(fd, hdr, want, cur);
    return hle_bink_probe(hdr, got > 0 ? (uint32_t)got : 0u, span, info);
}

#ifndef HLE_WITH_FFMPEG
/* No decoder here, but a file can still be inspected. Distinguish "not found"
 * and "malformed/truncated/unsupported" from "valid movie, no decoder", and
 * keep BinkOpen failing in every case: never report a playback we cannot do. */
static void k_Open(void) {                               /* (name or handle, flags) */
    uint32_t flags = A32(1);
    const char *name = (flags & BINKFILEHANDLE) ? NULL : (A32(0) ? ASTR(0) : NULL);
    int fd = -1, owned = 0;
    if (flags & BINKFILEHANDLE) {
        fd = hle_file_fd(A32(0));
    } else {
        char path[1024];
        if (name && hle_host_path(name, path, sizeof path)) fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0) {
            fprintf(stderr, "[bink] BinkOpen: movie not found: %s\n", name ? name : "(null)");
            set_error("cannot open the movie: file not found");
            RET(0, 2);
        }
        owned = 1;
    }
    if (fd < 0) { set_error("cannot open the movie"); RET(0, 2); }
    bink_probe_info_t info;
    int st = probe_fd(fd, &info);
    const char *label = name ? name : "<handle>";
    if (st != BINK_PROBE_OK) {
        fprintf(stderr, "[bink] %s: %s\n", label, hle_bink_probe_error(st));
        set_error("%s", hle_bink_probe_error(st));
        if (owned) close(fd);
        RET(0, 2);
    }
    if (owned) close(fd);
    fprintf(stderr, "[bink] %s: %ux%u, %u frames at %u/%u fps; no movie decoder on this host\n",
            label, info.width, info.height, info.frames, info.fps_num, info.fps_den);
    set_error("no movie decoder on this host");
    RET(0, 2);
}
static void k_ret0_1(void) { RET(0, 1); }
static void k_ret0_2(void) { RET(0, 2); }
static void k_ret0_3(void) { RET(0, 3); }
static void k_ret0_7(void) { RET(0, 7); }
/* BinkWait: nonzero means "not time to decode yet". Returning 0 would tell a
 * game to decode a frame that can never arrive. */
static void k_wait_stub(void) { RET(1, 1); }
/* BinkSetSoundOnOff@8: the real API returns the previous setting. With no
 * movie there is no soundtrack, so sound is off and stays off. */
static void k_SetSoundOnOff(void) {
    fprintf(stderr, "[bink] BinkSetSoundOnOff: no movie decoder on this host (sound stays off)\n");
    RET(0, 2);
}
/* BinkBuffer* / BinkGetRects / BinkSetIOSize: keep the stub table's export
 * names in step with the FFmpeg table so a no-FFmpeg build does not turn a
 * graceful movie failure into an unresolved-import boot failure. */
static void k_buffer_open_stub(void) {
    fprintf(stderr, "[bink] BinkBufferOpen: no movie decoder on this host\n");
    RET(0, 4);
}
static void k_buffer_lock_stub(void) { RET(0, 1); }
static void k_buffer_unlock_stub(void) { RET(0, 1); }
static void k_buffer_offset_stub(void) { RET(0, 3); }
static void k_buffer_blit_stub(void) { RETV(3); }
static void k_getrects_stub(void) { RET(0, 2); }
const win32hle_shim win32hle_bink[] = {
    { "_BinkOpen@8", k_Open }, { "_BinkClose@4", k_ret0_1 }, { "_BinkDoFrame@4", k_ret0_1 },
    { "_BinkNextFrame@4", k_ret0_1 }, { "_BinkWait@4", k_wait_stub }, { "_BinkCopyToBuffer@28", k_ret0_7 },
    { "_BinkGoto@12", k_ret0_3 }, { "_BinkPause@8", k_ret0_2 }, { "_BinkSetVolume@8", k_ret0_2 },
    { "_BinkSetSoundOnOff@8", k_SetSoundOnOff },
    { "_BinkGetError@0", k_GetError }, { "_BinkSetSoundSystem@8", k_SetSoundSystem },
    { "_BinkOpenDirectSound@4", k_OpenDirectSound }, { "_BinkDDSurfaceType@4", k_DDSurfaceType },
    { "_BinkBufferOpen@16", k_buffer_open_stub }, { "_BinkBufferClose@4", k_ret0_1 },
    { "_BinkBufferLock@4", k_buffer_lock_stub }, { "_BinkBufferUnlock@4", k_buffer_unlock_stub },
    { "_BinkBufferSetOffset@12", k_buffer_offset_stub }, { "_BinkBufferBlit@12", k_buffer_blit_stub },
    { "_BinkGetRects@8", k_getrects_stub }, { "_BinkSetIOSize@4", k_ret0_1 },
    { 0, 0 } };
#else
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/imgutils.h>
#include <libswresample/swresample.h>

/* The guest-visible struct: Bink's leading fields, then ours, out of sight. */
typedef struct {
    uint32_t Width, Height, Frames, FrameNum, LastFrameNum, FrameRate, FrameRateDiv, ReadError, OpenFlags, BinkType,
             Size, FrameSize, SndSize;
    uint32_t pad[64];
    /* ours */
    int fd;
    int64_t base;                 /* where the movie starts in the file */
    AVFormatContext *fmt;
    AVIOContext *io;
    AVCodecContext *vdec, *adec;
    int vs, as;
    AVFrame *frame, *aframe;
    AVPacket *pkt;
    SwrContext *swr;
    int stream;                   /* the sound's mixer stream */
    int paused, have_frame, done, sound_on;
    uint32_t start_ms, paused_at;
    float volume;
    uint32_t magic;               /* last, in the host-only part of the struct */
} bink_t;

#define BINK_HANDLE_MAGIC 0x42494E4Bu                    /* "BINK" */

/* A bad guest handle used to fault the host. Report it through BinkGetError
 * instead, so a game passing a stale handle gets a diagnostic, not a crash. */
static bink_t *valid_bink(uint32_t va, const char *api) {
    bink_t *b = (bink_t *)hle_ptr(va);
    if (!b || b->magic != BINK_HANDLE_MAGIC) {
        fprintf(stderr, "[bink] %s: invalid Bink handle 0x%08x\n", api, va);
        set_error("invalid Bink handle");
        return NULL;
    }
    return b;
}

static int io_read(void *opaque, uint8_t *buf, int n) {
    bink_t *b = (bink_t *)opaque;
    ssize_t r = read(b->fd, buf, (size_t)n);
    return r > 0 ? (int)r : AVERROR_EOF;
}
static int64_t io_seek(void *opaque, int64_t off, int whence) {
    bink_t *b = (bink_t *)opaque;
    if (whence == AVSEEK_SIZE) return -1;
    whence &= ~AVSEEK_FORCE;
    if (whence == SEEK_SET) off += b->base;
    off_t r = lseek(b->fd, (off_t)off, whence);
    return r < 0 ? -1 : (int64_t)r - b->base;
}

static void close_bink(bink_t *b) {
    b->magic = 0;                                        /* any later use is a stale handle */
    if (b->stream >= 0) hle_audio_stream_close(b->stream);
    if (b->swr) swr_free(&b->swr);
    if (b->vdec) avcodec_free_context(&b->vdec);
    if (b->adec) avcodec_free_context(&b->adec);
    if (b->fmt) avformat_close_input(&b->fmt);
    if (b->io) { av_freep(&b->io->buffer); avio_context_free(&b->io); }
    av_frame_free(&b->frame);
    av_frame_free(&b->aframe);
    av_packet_free(&b->pkt);
    if (b->fd >= 0 && !(b->OpenFlags & BINKFILEHANDLE)) close(b->fd);
    hle_free(b);
}

static void push_audio(bink_t *b) {
    if (b->stream < 0 || !b->swr) return;
    int16_t out[8192 * 2];
    int n = swr_convert(b->swr, (uint8_t **)&(uint8_t *){ (uint8_t *)out }, 8192, (const uint8_t **)b->aframe->extended_data,
                        b->aframe->nb_samples);
    if (n > 0) hle_audio_stream_push(b->stream, out, n);
}

/* Read packets until the next video frame is decoded; sound met on the way
 * goes to the mixer. */
static int decode_frame(bink_t *b) {
    for (;;) {
        int r = avcodec_receive_frame(b->vdec, b->frame);
        if (r == 0) return 1;
        if (r != AVERROR(EAGAIN)) return 0;
        if (av_read_frame(b->fmt, b->pkt) < 0) { avcodec_send_packet(b->vdec, NULL); b->done = 1; continue; }
        if (b->pkt->stream_index == b->vs) avcodec_send_packet(b->vdec, b->pkt);
        else if (b->adec && b->pkt->stream_index == b->as && avcodec_send_packet(b->adec, b->pkt) == 0)
            while (avcodec_receive_frame(b->adec, b->aframe) == 0) push_audio(b);
        av_packet_unref(b->pkt);
        if (b->done) return 0;
    }
}

static void k_Open(void) {                               /* (name or handle, flags) */
    uint32_t flags = A32(1);
    const char *name = (flags & BINKFILEHANDLE) ? NULL : (A32(0) ? ASTR(0) : NULL);
    bink_t *b = (bink_t *)hle_alloc(sizeof *b);          /* the guest reads its leading fields */
    b->stream = -1, b->volume = 1.0f, b->fd = -1, b->magic = BINK_HANDLE_MAGIC;
    if (flags & BINKFILEHANDLE) {
        b->fd = hle_file_fd(A32(0));
        b->base = b->fd >= 0 ? lseek(b->fd, 0, SEEK_CUR) : 0;
    } else {
        char path[1024];
        if (!name || !hle_host_path(name, path, sizeof path)) {
            fprintf(stderr, "[bink] BinkOpen: movie not found: %s\n", name ? name : "(null)");
            set_error("cannot open the movie: file not found");
            hle_free(b);
            RET(0, 2);
        }
        b->fd = open(path, O_RDONLY | O_CLOEXEC);
    }
    b->OpenFlags = flags;
    const char *label = name ? name : "<handle>";
    if (b->fd < 0) {
        fprintf(stderr, "[bink] %s: cannot open the movie\n", label);
        set_error("cannot open the movie");
        hle_free(b);
        RET(0, 2);
    }
    bink_probe_info_t pi;
    int st = probe_fd(b->fd, &pi);
    if (st != BINK_PROBE_OK) {
        fprintf(stderr, "[bink] %s: %s\n", label, hle_bink_probe_error(st));
        set_error("%s", hle_bink_probe_error(st));
        close_bink(b);
        RET(0, 2);
    }
    b->io = avio_alloc_context((uint8_t *)av_malloc(65536), 65536, 0, b, io_read, NULL, io_seek);
    b->fmt = avformat_alloc_context();
    b->fmt->pb = b->io;
    const AVInputFormat *bik = av_find_input_format("bink");
    if (avformat_open_input(&b->fmt, NULL, bik, NULL) < 0 || avformat_find_stream_info(b->fmt, NULL) < 0) {
        fprintf(stderr, "[bink] %s: libavformat could not read the movie\n", label);
        set_error("not a Bink movie");
        b->fmt = NULL;
        close_bink(b);
        RET(0, 2);
    }
    b->vs = av_find_best_stream(b->fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    b->as = av_find_best_stream(b->fmt, AVMEDIA_TYPE_AUDIO, -1, -1, NULL, 0);
    if (b->vs < 0) { set_error("no video in the movie"); close_bink(b); RET(0, 2); }
    AVStream *v = b->fmt->streams[b->vs];
    const AVCodec *vc = avcodec_find_decoder(v->codecpar->codec_id);
    if (!vc) {                                           /* check before alloc, not after */
        fprintf(stderr, "[bink] %s: no Bink video decoder on this host\n", label);
        set_error("no Bink video decoder");
        close_bink(b);
        RET(0, 2);
    }
    b->vdec = avcodec_alloc_context3(vc);
    avcodec_parameters_to_context(b->vdec, v->codecpar);
    if (avcodec_open2(b->vdec, vc, NULL) < 0) {
        set_error("no Bink video decoder");
        close_bink(b);
        RET(0, 2);
    }
    if (b->as >= 0) {
        AVStream *a = b->fmt->streams[b->as];
        const AVCodec *ac = avcodec_find_decoder(a->codecpar->codec_id);
        b->adec = ac ? avcodec_alloc_context3(ac) : NULL;
        if (b->adec) {
            avcodec_parameters_to_context(b->adec, a->codecpar);
            if (avcodec_open2(b->adec, ac, NULL) < 0) avcodec_free_context(&b->adec);
        }
        if (b->adec) {
            int ch = b->adec->ch_layout.nb_channels > 1 ? 2 : 1;
            AVChannelLayout out = ch == 2 ? (AVChannelLayout)AV_CHANNEL_LAYOUT_STEREO : (AVChannelLayout)AV_CHANNEL_LAYOUT_MONO;
            swr_alloc_set_opts2(&b->swr, &out, AV_SAMPLE_FMT_S16, b->adec->sample_rate, &b->adec->ch_layout,
                                b->adec->sample_fmt, b->adec->sample_rate, 0, NULL);
            if (b->swr && swr_init(b->swr) == 0) b->stream = hle_audio_stream_open(b->adec->sample_rate, ch);
        }
        /* Continue muted, but say so: a handle without sound is not "with sound". */
        if (b->stream < 0)
            fprintf(stderr, "[bink] %s: no audio decoder or mixer stream; movie will be muted\n", label);
    }
    b->sound_on = b->stream >= 0;
    b->frame = av_frame_alloc(), b->aframe = av_frame_alloc(), b->pkt = av_packet_alloc();
    b->Width = (uint32_t)v->codecpar->width, b->Height = (uint32_t)v->codecpar->height;
    b->Frames = v->nb_frames > 0 ? (uint32_t)v->nb_frames : (uint32_t)v->duration;
    b->FrameRate = (uint32_t)v->avg_frame_rate.num, b->FrameRateDiv = (uint32_t)(v->avg_frame_rate.den ? v->avg_frame_rate.den : 1);
    if (!b->FrameRate) b->FrameRate = 15, b->FrameRateDiv = 1;
    b->FrameNum = 1, b->LastFrameNum = 0;
    b->start_ms = hle_ticks_ms();
    fprintf(stderr, "[bink] %ux%u, %u frames at %u/%u fps%s%s\n", b->Width, b->Height, b->Frames, b->FrameRate,
            b->FrameRateDiv, b->stream >= 0 ? ", with sound" : "", b->stream >= 0 ? "" : " (muted)");
    g_error = "", g_error_gen++;                         /* a later GetError must not see a stale failure */
    RETP(b, 2);
}
static void k_Close(void) {
    bink_t *b = valid_bink(A32(0), "BinkClose");
    if (b) close_bink(b);
    RETV(1);
}

static void k_DoFrame(void) {                            /* decode the current frame */
    bink_t *b = valid_bink(A32(0), "BinkDoFrame");
    if (!b) RET(0, 1);
    b->have_frame = decode_frame(b);
    b->LastFrameNum = b->FrameNum;
    RET(0, 1);
}
static void k_NextFrame(void) {
    bink_t *b = valid_bink(A32(0), "BinkNextFrame");
    if (!b) RETV(1);
    if (b->FrameNum < b->Frames) b->FrameNum++;
    RETV(1);
}
/* BinkWait: nonzero while it is not yet time for the next frame. */
static void k_Wait(void) {
    bink_t *b = valid_bink(A32(0), "BinkWait");
    if (!b) RET(1, 1);                                   /* invalid: wait, never fake a ready frame */
    if (b->paused) RET(1, 1);
    uint64_t due = (uint64_t)(b->FrameNum - 1) * 1000u * b->FrameRateDiv / b->FrameRate;
    RET(hle_ticks_ms() - b->start_ms < due ? 1u : 0u, 1);
}
static void k_Pause(void) {                              /* (bink, pause) -> the state */
    bink_t *b = valid_bink(A32(0), "BinkPause");
    if (!b) RET(0, 2);
    int p = A32(1) != 0;
    if (p && !b->paused) b->paused_at = hle_ticks_ms();
    if (!p && b->paused) b->start_ms += hle_ticks_ms() - b->paused_at;
    b->paused = p;
    if (b->stream >= 0) hle_audio_stream_gain(b->stream, (p || !b->sound_on) ? 0.0f : b->volume);
    RET((uint32_t)p, 2);
}
static void k_SetVolume(void) {                          /* (bink, volume) 32768 is full */
    bink_t *b = valid_bink(A32(0), "BinkSetVolume");
    if (!b) RETV(2);
    b->volume = (float)(int32_t)A32(1) / 32768.0f;
    if (b->volume > 1.0f) b->volume = 1.0f;
    if (b->stream >= 0 && !b->paused && b->sound_on) hle_audio_stream_gain(b->stream, b->volume);
    RETV(2);
}
/* BinkSetSoundOnOff(bink, onoff) -> previous state. One of the Bink 1.x entry
 * points the shim table lacked. A movie with no usable soundtrack reports its
 * sound as off; this never makes a muted handle claim to be audible. */
static void k_SetSoundOnOff(void) {
    bink_t *b = valid_bink(A32(0), "BinkSetSoundOnOff");
    if (!b) RET(0, 2);
    uint32_t prev = b->sound_on ? 1u : 0u;
    b->sound_on = b->stream >= 0 && A32(1) != 0;
    if (!b->sound_on && b->stream >= 0) hle_audio_stream_gain(b->stream, 0.0f);
    else if (b->sound_on && !b->paused) hle_audio_stream_gain(b->stream, b->volume);
    RET(prev, 2);
}
static void k_Goto(void) {                               /* (bink, frame, flags): back to the start, then forward */
    bink_t *b = valid_bink(A32(0), "BinkGoto");
    if (!b) RETV(3);
    uint32_t to = A32(1) ? A32(1) : 1;
    av_seek_frame(b->fmt, b->vs, 0, AVSEEK_FLAG_BACKWARD | AVSEEK_FLAG_BYTE);
    avcodec_flush_buffers(b->vdec);
    b->done = 0, b->FrameNum = 1;
    while (b->FrameNum < to && decode_frame(b)) b->FrameNum++;
    b->start_ms = hle_ticks_ms() - (uint32_t)((uint64_t)(b->FrameNum - 1) * 1000u * b->FrameRateDiv / b->FrameRate);
    RETV(3);
}

/* BinkCopyToBuffer(bink, dest, pitch, height, x, y, flags): the decoded frame
 * into a buffer of the type flags' low bits name (565, 555, 32-bit). */
static void k_CopyToBuffer(void) {
    bink_t *b = valid_bink(A32(0), "BinkCopyToBuffer");
    if (!b) RET(0, 7);
    uint8_t *dst = (uint8_t *)APTR(1);
    int pitch = (int)A32(2), dh = (int)A32(3), dx = (int)A32(4), dy = (int)A32(5), type = (int)(A32(6) & 0xF);
    AVFrame *f = b->frame;
    if (!b->have_frame || !dst || !f || f->format != AV_PIX_FMT_YUV420P) RET(0, 7);
    if (type != 10 && type != 9 && type != 3 && type != 5)
        fprintf(stderr, "[bink] BinkCopyToBuffer: unsupported BINKSURFACE type %d (no pixels written)\n", type);
    int w = f->width, h = f->height;
    for (int y = 0; y < h && dy + y < dh; y++) {
        const uint8_t *Y = f->data[0] + y * f->linesize[0], *U = f->data[1] + (y / 2) * f->linesize[1], *V = f->data[2] + (y / 2) * f->linesize[2];
        uint8_t *row = dst + (size_t)(dy + y) * (size_t)pitch;
        for (int x = 0; x < w; x++) {
            int c = Y[x] - 16, d = U[x / 2] - 128, e = V[x / 2] - 128;
            int r = (298 * c + 409 * e + 128) >> 8, g = (298 * c - 100 * d - 208 * e + 128) >> 8, bb = (298 * c + 516 * d + 128) >> 8;
            r = r < 0 ? 0 : r > 255 ? 255 : r, g = g < 0 ? 0 : g > 255 ? 255 : g, bb = bb < 0 ? 0 : bb > 255 ? 255 : bb;
            int X = dx + x;
            if (type == 10) ((uint16_t *)row)[X] = (uint16_t)((r >> 3) << 11 | (g >> 2) << 5 | bb >> 3);
            else if (type == 9) ((uint16_t *)row)[X] = (uint16_t)((r >> 3) << 10 | (g >> 3) << 5 | bb >> 3);
            else if (type == 3 || type == 5) ((uint32_t *)row)[X] = 0xFF000000u | (uint32_t)r << 16 | (uint32_t)g << 8 | (uint32_t)bb;
        }
    }
    RET(0, 7);
}

/* ---- BinkBuffer: where a GDI game has Bink put the picture ----
 * The game locks the buffer, has BinkCopyToBuffer fill it at its
 * SurfaceType, and blits it to its window at the offset it set: here a
 * 32-bit buffer, blitted onto the window surface (gdidc.c). The leading
 * fields are Bink's BINKBUFFER, which games read. */
typedef struct {
    uint32_t Width, Height, WindowWidth, WindowHeight, SurfaceType, Buffer;
    int32_t BufferPitch;
    uint32_t ClientOffsetX, ClientOffsetY, ScreenWidth, ScreenHeight, ScreenDepth;
    uint32_t pad[48];
    int destx, desty;                                    /* ours */
} binkbuf_t;
static void k_BufferOpen(void) {                         /* (hwnd, width, height, flags) */
    binkbuf_t *b = (binkbuf_t *)hle_alloc(sizeof *b);   /* the game reads its fields */
    int sw, sh;
    const uint8_t *px; int pitch, bpp;
    b->Width = b->WindowWidth = A32(1), b->Height = b->WindowHeight = A32(2);
    b->SurfaceType = 3;                                  /* BINKSURFACE32 */
    b->BufferPitch = (int32_t)(b->Width * 4);
    b->Buffer = hle_va(hle_alloc((b->Width * b->Height + 1) * 4u));
    if (hle_gdi_window_frame(&px, &sw, &sh, &pitch, &bpp, NULL)) b->ScreenWidth = (uint32_t)sw, b->ScreenHeight = (uint32_t)sh, b->ScreenDepth = (uint32_t)bpp;
    RETP(b, 4);
}
static void k_BufferClose(void) {
    binkbuf_t *b = (binkbuf_t *)APTR(0);
    if (b) hle_free(hle_ptr(b->Buffer)), hle_free(b);
    RETV(1);
}
static void k_BufferLock(void) { RET(A32(0) != 0, 1); }
static void k_BufferUnlock(void) { RET(1, 1); }
static void k_BufferSetOffset(void) {                    /* (buf, x, y) */
    binkbuf_t *b = (binkbuf_t *)APTR(0);
    if (b) b->destx = (int)A32(1), b->desty = (int)A32(2);
    RET(1, 3);
}
static void k_BufferBlit(void) {                         /* (buf, rects, n): the whole picture, whatever changed */
    binkbuf_t *b = (binkbuf_t *)APTR(0);
    const uint8_t *px;
    int sw, sh, pitch, bpp;
    if (b && hle_gdi_window_frame(&px, &sw, &sh, &pitch, &bpp, NULL) && bpp == 32) {
        for (int y = 0; y < (int)b->Height; y++) {
            int ty = b->desty + y;
            if (ty < 0 || ty >= sh) continue;
            for (int x = 0; x < (int)b->Width; x++) {
                int tx = b->destx + x;
                if (tx >= 0 && tx < sw)
                    ((uint32_t *)(px + (size_t)ty * pitch))[tx] = ((const uint32_t *)hle_ptr(b->Buffer))[(size_t)y * b->Width + x] & 0xFFFFFFu;
            }
        }
        hle_gdi_window_touched();
    }
    RETV(3);
}
static void k_GetRects(void) { RET(1, 2); }              /* (bink, flags): one rectangle, the whole frame */
static void k_SetIOSize(void) { RETV(1); }

const win32hle_shim win32hle_bink[] = {
    { "_BinkBufferOpen@16", k_BufferOpen }, { "_BinkBufferClose@4", k_BufferClose },
    { "_BinkBufferLock@4", k_BufferLock }, { "_BinkBufferUnlock@4", k_BufferUnlock },
    { "_BinkBufferSetOffset@12", k_BufferSetOffset }, { "_BinkBufferBlit@12", k_BufferBlit },
    { "_BinkGetRects@8", k_GetRects }, { "_BinkSetIOSize@4", k_SetIOSize },
    { "_BinkOpen@8", k_Open }, { "_BinkClose@4", k_Close }, { "_BinkDoFrame@4", k_DoFrame },
    { "_BinkNextFrame@4", k_NextFrame }, { "_BinkWait@4", k_Wait }, { "_BinkCopyToBuffer@28", k_CopyToBuffer },
    { "_BinkGoto@12", k_Goto }, { "_BinkPause@8", k_Pause }, { "_BinkSetVolume@8", k_SetVolume },
    { "_BinkSetSoundOnOff@8", k_SetSoundOnOff },
    { "_BinkGetError@0", k_GetError }, { "_BinkSetSoundSystem@8", k_SetSoundSystem },
    { "_BinkOpenDirectSound@4", k_OpenDirectSound }, { "_BinkDDSurfaceType@4", k_DDSurfaceType },
    { 0, 0 } };
#endif
