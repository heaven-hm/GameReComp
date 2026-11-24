/*
 * avifil32.c - Video-for-Windows: the AVI container, and the MSVFW32
 * decompressor entry points (avifil32.dll and msvfw32.dll).
 *
 * This is container work, not game work: an AVI is a RIFF file, so the parser
 * here reads 'hdrl'/'strl'/'strf'/'movi'/'idx1' and hands a game back the
 * compressed frames it asks for, exactly as Windows does. Decoding piggybacks
 * on the same libavcodec path bink.c uses (HLE_WITH_FFMPEG); without it a codec
 * is reported unavailable rather than pretended to work.
 *
 * The game drives playback itself: AVIStreamRead hands it a compressed frame,
 * ICDecompress turns it into a DIB, and the game blits that DIB with its own
 * DirectDraw code. Nothing here knows which game is running.
 *
 * AVI container layout and the AVIERR/ICERR result codes follow the public
 * vfw.h/avifil32 documentation. IGI-recomp's avifil32.c (HenryKun55, MIT) was
 * consulted for the same format; this is a rewrite, not a copy.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "win32hle.h"

/* AVI error family (vfw.h). */
#define AVIERR_OK          0u
#define AVIERR_BADHANDLE   0x80044064u
#define AVIERR_FILEOPEN    0x80044067u
#define AVIERR_NODATA      0x80044071u
/* Codec-manager results (msvideo.h). */
#define ICERR_OK           0u
#define ICERR_UNSUPPORTED  0xFFFFFFFFu
#define ICERR_BADFORMAT    0xFFFFFFFDu

#define MAX_AVI_FILES   16
#define MAX_AVI_CODECS  4

typedef struct {
    uint32_t offset;                    /* into `data`, at the frame bytes */
    uint32_t size;
} avi_index_t;

typedef struct {
    uint8_t *data;                      /* the whole file, read once */
    size_t size;
    uint32_t handle;                    /* guest-visible file handle */
    uint32_t stream;                    /* guest-visible stream handle */
    uint32_t width, height, frames;
    uint32_t scale, rate;               /* frames per second = rate/scale */
    uint32_t handler;                   /* stream handler fourcc: the codec */
    uint8_t strf[64];                   /* BITMAPINFOHEADER for the stream */
    uint32_t strf_size;
    avi_index_t *index;
    uint32_t index_count;
} avi_file_t;

static avi_file_t *g_avi[MAX_AVI_FILES];
/* The last frame handed out, so ICDecompress can size the packet exactly. */
static uint32_t g_last_buf, g_last_size;

static uint32_t rd32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static avi_file_t *file_for(uint32_t handle) {
    for (int i = 0; i < MAX_AVI_FILES; i++)
        if (g_avi[i] && (g_avi[i]->handle == handle || g_avi[i]->stream == handle)) return g_avi[i];
    return NULL;
}


/* Walk the RIFF tree: headers, the 'movi' base and the 'idx1' index. Depth is
 * bounded (RIFF nesting is shallow) and every chunk is bounds-checked, so a
 * malformed or truncated file is rejected instead of read past its end. */
static int parse_avi(avi_file_t *f) {
    const uint8_t *d = f->data;
    size_t n = f->size;
    if (n < 12 || memcmp(d, "RIFF", 4) || memcmp(d + 8, "AVI ", 4)) return -1;

    size_t list_end[4];
    int depth = 0;
    size_t off = 12, movi = 0;
    const uint8_t *idx1 = NULL;
    uint32_t idx1_size = 0;
    int have_video = 0;
    list_end[0] = n;

    while (depth >= 0) {
        if (off + 8 > list_end[depth]) { off = list_end[depth]; depth--; continue; }
        const uint8_t *c = d + off;
        uint32_t sz = rd32(c + 4);
        if ((size_t)sz > n || off + 8u + (size_t)sz > n) break;   /* truncated: stop */
        size_t next = off + 8u + (size_t)sz + (sz & 1u);

        if (!memcmp(c, "LIST", 4)) {
            if (sz >= 4 && !memcmp(c + 8, "movi", 4)) {
                movi = off + 8;                                   /* idx1 offsets are relative to here */
                off = next;
            } else if (depth < 3 && sz >= 4) {
                list_end[++depth] = next < n ? next : n;
                off += 12;
            } else {
                off = next;
            }
            continue;
        }
        if (sz >= 40 && !memcmp(c, "avih", 4)) {
            f->width = rd32(c + 8 + 32);
            f->height = rd32(c + 8 + 36);
            f->frames = rd32(c + 8 + 16);
        } else if (sz >= 36 && !memcmp(c, "strh", 4) && !memcmp(c + 8, "vids", 4) && !have_video) {
            have_video = 1;
            f->handler = rd32(c + 12);
            f->scale = rd32(c + 8 + 20);
            f->rate = rd32(c + 8 + 24);
            f->frames = rd32(c + 8 + 32);
        } else if (sz >= 16 && !memcmp(c, "strf", 4) && have_video && !f->strf_size) {
            f->strf_size = sz < sizeof f->strf ? sz : (uint32_t)sizeof f->strf;
            memcpy(f->strf, c + 8, f->strf_size);
            if (f->strf_size >= 12) {                             /* BITMAPINFOHEADER: trust it for geometry */
                f->width = rd32(f->strf + 4);
                f->height = rd32(f->strf + 8) & 0x7FFFFFFFu;
            }
        } else if (sz >= 16 && !memcmp(c, "idx1", 4)) {
            idx1 = c + 8;
            idx1_size = sz;
        }
        off = next;
    }
    if (!movi || !idx1 || !f->strf_size) return -1;

    uint32_t entries = idx1_size / 16u;
    f->index = calloc(entries ? entries : 1u, sizeof *f->index);
    if (!f->index) return -1;
    /* idx1 offsets are relative to the 'movi' fourcc. Some writers break that
     * and use file offsets; decide once, from the first entry. */
    size_t base = movi;
    if (entries) {
        uint32_t first = rd32(idx1 + 8);
        if (first >= movi && first + 4u <= n && !memcmp(d + first, idx1, 4)) base = 0;
    }
    for (uint32_t i = 0; i < entries; i++) {
        const uint8_t *e = idx1 + (size_t)i * 16u;
        if (e[0] != '0' || e[1] != '0' || (e[2] != 'd' && e[2] != 'D')) continue;   /* stream 00 video */
        size_t abs = base + rd32(e + 8);
        uint32_t s = rd32(e + 12);
        if (abs + 8u + (size_t)s > n) continue;
        f->index[f->index_count].offset = (uint32_t)(abs + 8);    /* past "NNdb" */
        f->index[f->index_count].size = s;
        f->index_count++;
    }
    return f->index_count ? 0 : -1;
}

/* ---- avifil32 ---------------------------------------------------------- */

static void avi_file_init(void) { RETV(0); }
static void avi_file_exit(void) { RETV(0); }

static void avi_file_open(void) {                          /* (path, mode, handler, &out) */
    uint32_t out = A32(3);
    if (!out) RET(AVIERR_BADHANDLE, 4);
    MEM32(out) = 0;
    if (!A32(0)) RET(AVIERR_FILEOPEN, 4);

    char path[1024];
    if (!hle_host_path(ASTR(0), path, sizeof path)) {
        fprintf(stderr, "[avi] AVIFileOpenA(%s): not found\n", ASTR(0));
        RET(AVIERR_FILEOPEN, 4);
    }
    FILE *fp = fopen(path, "rb");
    if (!fp) RET(AVIERR_FILEOPEN, 4);

    int slot = -1;
    for (int i = 0; i < MAX_AVI_FILES && slot < 0; i++) if (!g_avi[i]) slot = i;
    if (slot < 0) { fclose(fp); RET(AVIERR_FILEOPEN, 4); }

    avi_file_t *f = calloc(1, sizeof *f);
    fseek(fp, 0, SEEK_END);
    long sz = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    if (sz <= 0 || (uint64_t)sz > 0x20000000ull) { fclose(fp); free(f); RET(AVIERR_FILEOPEN, 4); }
    f->size = (size_t)sz;
    f->data = malloc(f->size);
    int bad = !f->data || fread(f->data, 1, f->size, fp) != f->size;
    fclose(fp);
    if (bad || parse_avi(f) != 0) {
        free(f->data);
        free(f->index);
        free(f);
        fprintf(stderr, "[avi] AVIFileOpenA(%s): not a readable AVI\n", ASTR(0));
        RET(AVIERR_FILEOPEN, 4);
    }
    f->handle = hle_va(hle_alloc(16));
    f->stream = hle_va(hle_alloc(16));
    g_avi[slot] = f;
    fprintf(stderr, "[avi] %s: %ux%u, %u frames, %u/%u fps, codec %.4s\n", ASTR(0),
            f->width, f->height, f->index_count, f->rate, f->scale, (const char *)&f->handler);
    MEM32(out) = f->handle;
    RET(AVIERR_OK, 4);
}

static void avi_file_info(void) {                          /* (handle, &AVIFILEINFO, size) */
    avi_file_t *f = file_for(A32(0));
    uint32_t p = A32(1), size = A32(2);
    if (!f) RET(AVIERR_BADHANDLE, 3);
    if (p && size) memset(hle_ptr(p), 0, size);
    if (p && size >= 44) {
        MEM32(p + 12) = 1;                                 /* dwStreams */
        MEM32(p + 20) = f->width;
        MEM32(p + 24) = f->height;
        MEM32(p + 28) = f->scale;
        MEM32(p + 32) = f->rate;
        MEM32(p + 36) = f->index_count;                    /* dwLength */
    }
    RET(AVIERR_OK, 3);
}

static void avi_file_get_stream(void) {                    /* (handle, &stream, fccType, lParam) */
    avi_file_t *f = file_for(A32(0));
    uint32_t out = A32(1), type = A32(2);
    if (out) MEM32(out) = 0;
    /* 'vids' or 0 (the first stream); this backend exposes the video stream. */
    if (!f || (type && type != 0x73646976u) || A32(3) != 0) RET(f ? AVIERR_NODATA : AVIERR_BADHANDLE, 4);
    MEM32(out) = f->stream;
    RET(AVIERR_OK, 4);
}

static void avi_file_release(void) {
    avi_file_t *f = file_for(A32(0));
    if (f) {
        for (int i = 0; i < MAX_AVI_FILES; i++) if (g_avi[i] == f) g_avi[i] = NULL;
        hle_free(hle_ptr(f->handle));
        hle_free(hle_ptr(f->stream));
        free(f->data);
        free(f->index);
        free(f);
    }
    RET(AVIERR_OK, 1);
}

static void avi_stream_info(void) {                        /* (stream, &AVISTREAMINFO, size) */
    avi_file_t *f = file_for(A32(0));
    uint32_t p = A32(1), size = A32(2);
    if (!f) RET(AVIERR_BADHANDLE, 3);
    if (p && size) memset(hle_ptr(p), 0, size);
    if (p && size >= 68) {
        MEM32(p + 0) = 0x73646976u;                        /* fccType 'vids' */
        MEM32(p + 4) = f->handler;
        MEM32(p + 20) = f->scale;
        MEM32(p + 24) = f->rate;
        MEM32(p + 28) = 0;                                 /* dwStart */
        MEM32(p + 32) = f->index_count;                    /* dwLength */
        MEM32(p + 40) = 0x20000u;                          /* dwSuggestedBufferSize */
        MEM32(p + 60) = f->width;                          /* rcFrame */
        MEM32(p + 64) = f->height;
    }
    RET(AVIERR_OK, 3);
}

static void avi_stream_read_format(void) {                 /* (stream, pos, fmt, &size) */
    avi_file_t *f = file_for(A32(0));
    uint32_t fmt = A32(2), cb = A32(3);
    if (!f) { if (cb) MEM32(cb) = 0; RET(AVIERR_BADHANDLE, 4); }
    if (fmt && cb && MEM32(cb) >= f->strf_size) memcpy(hle_ptr(fmt), f->strf, f->strf_size);
    if (cb) MEM32(cb) = f->strf_size;
    RET(AVIERR_OK, 4);
}

static void avi_stream_read(void) {                        /* (stream, start, samples, buf, cb, &bytes, &samples) */
    avi_file_t *f = file_for(A32(0));
    uint32_t start = A32(1), buf = A32(3), cb = A32(4), pbytes = A32(5), psamples = A32(6);
    if (pbytes) MEM32(pbytes) = 0;
    if (psamples) MEM32(psamples) = 0;
    if (!f) RET(AVIERR_BADHANDLE, 7);
    if (start >= f->index_count) RET(AVIERR_NODATA, 7);
    const avi_index_t *e = &f->index[start];
    if (buf) {
        uint32_t n = e->size < cb ? e->size : cb;
        if (n) memcpy(hle_ptr(buf), f->data + e->offset, n);
        g_last_buf = buf;
        g_last_size = n;
    }
    if (pbytes) MEM32(pbytes) = e->size;
    if (psamples) MEM32(psamples) = 1;
    RET(AVIERR_OK, 7);
}


static void avi_stream_release(void) { RET(AVIERR_OK, 1); }

/* ---- msvfw32: the installable decompressor ----------------------------- *
 * ICLocate opens a decoder for a codec fourcc; ICDecompress then converts one
 * compressed frame into the DIB the game asked for. Both are generic: the
 * fourcc is mapped to a libavcodec id and nothing else is assumed. */

#ifndef HLE_WITH_FFMPEG
/* No decoder on this host: say so, and never claim a frame was produced. */
static void ic_locate(void) {
    const uint8_t *bi = A32(2) ? (const uint8_t *)hle_ptr(A32(2)) : NULL;
    if (bi) fprintf(stderr, "[msvfw32] ICLocate(%.4s): no decoder backend on this host\n", (const char *)bi + 16);
    RET(0, 5);
}
static void ic_send_message(void) { RET(ICERR_UNSUPPORTED, 4); }
static void ic_close(void) { RET(ICERR_OK, 1); }
static void ic_decompress(void) { RET(ICERR_UNSUPPORTED, 6); }
#else
#include <libavcodec/avcodec.h>
#include <libavutil/imgutils.h>

typedef struct {
    uint32_t handle;                    /* guest-visible HIC */
    uint32_t fourcc;
    int width, height;
    AVCodecContext *dec;
    AVFrame *frame;
    AVPacket *pkt;
} avi_codec_t;

static avi_codec_t g_codec[MAX_AVI_CODECS];
#define HIC_BASE 0x00E1C000u

static enum AVCodecID codec_for(uint32_t fourcc) {
    char f[5];
    for (int i = 0; i < 4; i++) {
        char ch = (char)((fourcc >> (8 * i)) & 0xFF);
        f[i] = (ch >= 'a' && ch <= 'z') ? (char)(ch - 32) : ch;
    }
    f[4] = 0;
    /* The Indeo family, which is what AVI-era cutscenes actually use. */
    if (!strcmp(f, "IV50")) return AV_CODEC_ID_INDEO5;
    if (!strcmp(f, "IV41")) return AV_CODEC_ID_INDEO4;
    if (!strcmp(f, "IV32") || !strcmp(f, "IV31")) return AV_CODEC_ID_INDEO3;
    return AV_CODEC_ID_NONE;
}

static avi_codec_t *codec_for_handle(uint32_t h) {
    for (int i = 0; i < MAX_AVI_CODECS; i++)
        if (g_codec[i].dec && g_codec[i].handle == h) return &g_codec[i];
    return NULL;
}




static void ic_locate(void) {                              /* (type, handler, biIn, biOut, flags) */
    const uint8_t *bi = A32(2) ? (const uint8_t *)hle_ptr(A32(2)) : NULL;
    uint32_t fourcc = bi ? rd32(bi + 16) : A32(1);          /* biCompression */
    int w = bi ? (int)rd32(bi + 4) : 0, h = bi ? (int)(rd32(bi + 8) & 0x7FFFFFFFu) : 0;
    if (fourcc == 0) RET(0, 5);                             /* BI_RGB needs no decompressor */

    /* An already-open codec for the same fourcc is reused, as Windows does. */
    for (int i = 0; i < MAX_AVI_CODECS; i++)
        if (g_codec[i].dec && g_codec[i].fourcc == fourcc) RET(g_codec[i].handle, 5);

    enum AVCodecID id = codec_for(fourcc);
    const AVCodec *codec = id == AV_CODEC_ID_NONE ? NULL : avcodec_find_decoder(id);
    if (!codec) {
        fprintf(stderr, "[msvfw32] ICLocate(%.4s): no libavcodec decoder\n", (const char *)&fourcc);
        RET(0, 5);
    }
    for (int i = 0; i < MAX_AVI_CODECS; i++) {
        if (g_codec[i].dec) continue;
        avi_codec_t *k = &g_codec[i];
        memset(k, 0, sizeof *k);
        k->dec = avcodec_alloc_context3(codec);
        if (!k->dec) break;
        k->dec->width = w > 0 ? w : 320;
        k->dec->height = h > 0 ? h : 240;
        k->dec->codec_tag = fourcc;
        if (avcodec_open2(k->dec, codec, NULL) < 0) { avcodec_free_context(&k->dec); break; }
        k->frame = av_frame_alloc();
        k->pkt = av_packet_alloc();
        if (!k->frame || !k->pkt) {
            avcodec_free_context(&k->dec);
            av_frame_free(&k->frame);
            av_packet_free(&k->pkt);
            break;
        }
        k->fourcc = fourcc;
        k->width = k->dec->width;
        k->height = k->dec->height;
        k->handle = HIC_BASE + (uint32_t)i;
        fprintf(stderr, "[msvfw32] ICLocate(%.4s %dx%d): %s\n", (const char *)&fourcc, w, h, codec->name);
        RET(k->handle, 5);
    }
    RET(0, 5);
}

static void ic_close(void) {
    avi_codec_t *k = codec_for_handle(A32(0));
    if (k) {
        avcodec_free_context(&k->dec);
        av_frame_free(&k->frame);
        av_packet_free(&k->pkt);
        memset(k, 0, sizeof *k);
    }
    RET(ICERR_OK, 1);
}

static void ic_send_message(void) {                        /* (hic, msg, p1, p2) */
    avi_codec_t *k = codec_for_handle(A32(0));
    uint32_t msg = A32(1), p1 = A32(2), p2 = A32(3);
    if (msg == 0x400Au) {                                  /* ICM_DECOMPRESS_GET_FORMAT */
        if (!p2) RET(40, 4);                               /* size query */
        memset(hle_ptr(p2), 0, 40);
        MEM32(p2) = 40;
        if (p1 && k) {
            MEM32(p2 + 4) = (uint32_t)k->width;
            MEM32(p2 + 8) = (uint32_t)k->height;
        }
        MEM16(p2 + 12) = 1;                                /* biPlanes */
        MEM16(p2 + 14) = 32;                               /* biBitCount */
        RET(ICERR_OK, 4);
    }
    RET(ICERR_UNSUPPORTED, 4);
}

/* ICDecompress(hic, flags, biIn, data, biOut, bits): decode one frame into the
 * game's DIB. A packet that yields no picture leaves the previous one in place,
 * which is what the codec manager does for a dropped frame. */
static void ic_decompress(void) {
    avi_codec_t *k = codec_for_handle(A32(0));
    uint32_t bi_out = A32(4), bits = A32(5), data = A32(3), bi_in = A32(2);
    if (!k || !data || !bi_out || !bits) RET(ICERR_BADFORMAT, 6);

    uint32_t size = data == g_last_buf ? g_last_size : (bi_in ? rd32(hle_ptr(bi_in) + 20) : 0);
    if (!size) RET(ICERR_BADFORMAT, 6);

    int got = 0;
    av_packet_unref(k->pkt);
    k->pkt->data = (uint8_t *)hle_ptr(data);
    k->pkt->size = (int)size;
    if (avcodec_send_packet(k->dec, k->pkt) == 0) {
        int r = avcodec_receive_frame(k->dec, k->frame);
        if (r == 0) got = 1;
        else if (r != AVERROR(EAGAIN)) avcodec_flush_buffers(k->dec);
    }
    if (!got) RET(ICERR_OK, 6);

    const uint8_t *obi = (const uint8_t *)hle_ptr(bi_out);
    int w = (int)rd32(obi + 4);
    int32_t hh = (int32_t)rd32(obi + 8);
    int top_down = hh < 0, h = top_down ? -hh : hh;
    uint32_t bpp = MEM16(A32(4) + 14);
    if (w <= 0 || h <= 0) RET(ICERR_BADFORMAT, 6);

    uint32_t rm = 0x7C00u, gm = 0x03E0u, bm = 0x001Fu;      /* 16-bit BI_RGB is 5:5:5 */
    if (rd32(obi + 16) == 3u) {                            /* BI_BITFIELDS */
        rm = rd32(obi + 40);
        gm = rd32(obi + 44);
        bm = rd32(obi + 48);
    }
    int rs = __builtin_ctz(rm), gs = __builtin_ctz(gm), bs = __builtin_ctz(bm);
    int rb = __builtin_popcount(rm), gb = __builtin_popcount(gm), bb = __builtin_popcount(bm);

    const uint8_t *src = k->frame->data[0];
    int sw = k->frame->width, sh = k->frame->height;
    int cpp = (k->frame->format == AV_PIX_FMT_BGRA || k->frame->format == AV_PIX_FMT_RGBA) ? 4 : 3;
    uint32_t pitch = (((uint32_t)w * bpp / 8u) + 3u) & ~3u;
    uint8_t *dst = (uint8_t *)hle_ptr(bits);

    for (int y = 0; y < h; y++) {
        int sy = sh ? (int)((int64_t)y * sh / h) : y;
        const uint8_t *srow = src + (size_t)sy * k->frame->linesize[0];
        uint32_t row = top_down ? (uint32_t)y : (uint32_t)(h - 1 - y);
        uint8_t *drow = dst + (size_t)row * pitch;
        for (int x = 0; x < w; x++) {
            int sx = sw ? (int)((int64_t)x * sw / w) : x;
            const uint8_t *p = srow + (size_t)sx * cpp;
            uint32_t R = p[2], G = p[1], B = p[0];
            if (bpp == 32u) {
                uint8_t *o = drow + (size_t)x * 4;
                o[0] = (uint8_t)B; o[1] = (uint8_t)G; o[2] = (uint8_t)R; o[3] = 0xFF;
            } else if (bpp == 24u) {
                uint8_t *o = drow + (size_t)x * 3;
                o[0] = (uint8_t)B; o[1] = (uint8_t)G; o[2] = (uint8_t)R;
            } else if (bpp == 16u) {
                uint32_t v = ((R >> (8 - rb)) << rs) | ((G >> (8 - gb)) << gs) | ((B >> (8 - bb)) << bs);
                drow[x * 2] = (uint8_t)v;
                drow[x * 2 + 1] = (uint8_t)(v >> 8);
            }
        }
    }
    RET(ICERR_OK, 6);
}
#endif

const win32hle_shim win32hle_avifil32[] = {
    {"AVIFileOpenA",avi_file_open}, {"AVIFileGetStream",avi_file_get_stream},
    {"AVIFileInfoA",avi_file_info}, {"AVIFileInit",avi_file_init},
    {"AVIFileExit",avi_file_exit}, {"AVIFileRelease",avi_file_release},
    {"AVIStreamInfoA",avi_stream_info}, {"AVIStreamRelease",avi_stream_release},
    {"AVIStreamReadFormat",avi_stream_read_format}, {"AVIStreamRead",avi_stream_read},
    {0,0}
};

/* msvfw32.dll is a separate module on Windows, but the decompressor shares the
 * frame hand-off with AVIStreamRead above, so both are registered from here. */
const win32hle_shim win32hle_msvfw32[] = {
    {"ICLocate",ic_locate}, {"ICSendMessage",ic_send_message},
    {"ICClose",ic_close}, {"ICDecompress",ic_decompress}, {0,0}
};
