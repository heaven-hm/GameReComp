/* winmm_mci_selftest.c - the MCI waveaudio subset driven exactly as a lifted
 * guest drives it: stdcall calls through a guest shim driver, guest command /
 * return strings, and a real WAV in the guest's file namespace.
 *
 * Focused build (small, for a shared disk): only winmm.c and the four spine
 * files it needs are linked. WINMM_MCI_SELFTEST_MINIMAL supplies the audio
 * stream functions that live in dsound.c in a whole-layer build; they *record*
 * pushes / gains / opens / closes so behaviour is asserted deterministically
 * without depending on real-time audio. winmm.c itself is the real file, and
 * all calls go through its registered stdcall shims.
 *
 *   cd runtime/win32hle
 *   TMPDIR=/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode \
 *   cc -O1 -Wall -Wno-unused-function -Wno-unused-but-set-variable \
 *      -DWINMM_MCI_SELFTEST_MINIMAL -I../recomp32 -I. \
 *      winmm.c host_lite.c guest_mem.c clock.c hle_path.c \
 *      winmm_mci_selftest.c -lpthread -lm -o $TMPDIR/winmm_mci_selftest
 *   SDL_AUDIODRIVER=dummy $TMPDIR/winmm_mci_selftest
 *
 * Proves: registration; an exact stdcall purge for every MCI shim; bounded
 * command / reply strings and honest errors for unsupported input; a real open
 * that decodes WAV and reports the device id; status/length/position; volume
 * reaching the mixer's gain; PCM actually pushed to the audio stream; pause /
 * resume / seek / stop changing state; natural completion; and close freeing
 * the device so the alias can be reused.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include "win32hle.h"

#define DRV_VA 0x00402800u                    /* guest code calling the shims */
#define GUEST_PROLOGUE(va) \
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0; RECOMP_ENTER(va)

enum {
    MCI_NO_ERROR = 0u,
    MCI_INVALID_DEVICE_ID = 257u,
    MCI_UNRECOGNIZED_KEYWORD = 259u,
    MCI_UNRECOGNIZED_COMMAND = 261u,
    MCI_HARDWARE = 262u,
    MCI_OUT_OF_MEMORY = 264u,
    MCI_PARAM_OVERFLOW = 268u,
    MCI_MISSING_STRING_ARG = 269u,
    MCI_BAD_INTEGER = 270u,
    MCI_UNSUPPORTED_FUNCTION = 274u,
    MCI_FILE_NOT_FOUND = 275u,
    MCI_OUTOFRANGE = 282u,
    MCI_DUPLICATE_ALIAS = 289u
};
#define WAV_NAME "winmm_mci_selftest_tone.wav"
#define TEST_FRAMES 4410u                     /* 100 ms at 44.1 kHz */
#define TEST_RATE  44100u
#define TEST_MS    100u
#define MCI_CMD_MAX 1024u

/* --- audio-stream recorder (stands in for dsound.c's mixer in this build) --- */
#ifdef WINMM_MCI_SELFTEST_MINIMAL
static int g_streams_opened, g_streams_closed, g_gain_calls;
static int g_queued;                          /* controlled by the test */
static int g_stall_after_push;                /* hold the feed after one chunk */
static uint32_t g_push_calls, g_pushed_frames, g_last_rate, g_last_channels;
static int16_t g_first_sample;
static int g_gain_milli = -1;                 /* gain * 1000, atomic */
#endif

#ifdef WINMM_MCI_SELFTEST_MINIMAL
void hle_exit(int code) { (void)code; fprintf(stderr, "hle_exit\n"); abort(); }
uint32_t hle_thread_id(void) { return 1; }
uint32_t hle_ticks_ms(void) { return hle_clock_ms(); }
void hle_set_event(uint32_t h) { (void)h; }
void hle_post_message(uint32_t h, uint32_t m, uint32_t w, uint32_t l) {
    (void)h; (void)m; (void)w; (void)l;
}
int hle_wide_to_ansi(const uint16_t *w, char *out, size_t n) {
    size_t i;
    if (!w || !n) return 0;
    for (i = 0; i + 1 < n && w[i]; i++) out[i] = (char)w[i];
    out[i] = 0;
    return 1;
}
int hle_audio_stream_open(int rate, int channels) {
    __atomic_fetch_add(&g_streams_opened, 1, __ATOMIC_SEQ_CST);
    g_last_rate = (uint32_t)rate; g_last_channels = (uint32_t)channels;
    return 0;                                 /* one recorder stream */
}
void hle_audio_stream_push(int id, const int16_t *pcm, int frames) {
    (void)id;
    __atomic_fetch_add(&g_push_calls, 1, __ATOMIC_SEQ_CST);
    __atomic_fetch_add(&g_pushed_frames, (uint32_t)frames, __ATOMIC_SEQ_CST);
    if (frames > 0) g_first_sample = pcm[0];
    if (g_stall_after_push) __atomic_store_n(&g_queued, 32769, __ATOMIC_SEQ_CST);
}
int hle_audio_stream_queued(int id) {
    (void)id;
    return __atomic_load_n(&g_queued, __ATOMIC_SEQ_CST);
}
void hle_audio_stream_gain(int id, float gain) {
    (void)id;
    __atomic_store_n(&g_gain_milli, (int)(gain * 1000.0f), __ATOMIC_SEQ_CST);
    __atomic_fetch_add(&g_gain_calls, 1, __ATOMIC_SEQ_CST);
}
void hle_audio_stream_close(int id) {
    (void)id;
    __atomic_fetch_add(&g_streams_closed, 1, __ATOMIC_SEQ_CST);
}
#endif

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
    else printf("ok   %s\n", what);
}

/* One lifted driver for every shim: push the stdcall args in reverse, do an
 * indirect call through the resolved VA, save eax, restore the stack. */
static uint32_t g_target, g_nargs, g_args[6], g_rc;
static void guest_call_shim(void) {
    GUEST_PROLOGUE(DRV_VA);
    for (int i = (int)g_nargs - 1; i >= 0; i--) PUSH32(esp, g_args[i]);
    RECOMP_ICALL(g_target);
    g_rc = eax;
    esp += 4u * g_nargs;                       /* callee popped its own frame */
}
const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { DRV_VA, guest_call_shim }
};
const uint32_t recomp_dispatch_count = 1;

static uint32_t call(const char *name, uint32_t n, const uint32_t *args) {
    uint32_t fn = hle_resolve(name);
    if (!fn) { printf("FAIL %s did not resolve\n", name); fails++; return 0xFFFFFFFFu; }
    g_target = fn; g_nargs = n;
    for (uint32_t i = 0; i < n; i++) g_args[i] = args[i];
    hle_call_guest(DRV_VA, 0, NULL);
    return g_rc;
}
/* hle_call_guest restores the register file, so it cannot see a wrong stdcall
 * purge. Drive the shim directly with a synthetic return address and count the
 * words it popped (1 + argc for a correct stdcall). */
static uint32_t shim_pops(const char *name, int argc, const uint32_t *args, uint32_t *out_eax) {
    uint32_t va = hle_resolve(name);
    recomp_func_t fn = va ? recomp_lookup_import(va) : 0;
    if (!fn) return 0xFFFFFFFFu;
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t scratch = hle_va(hle_alloc(128));
    int i;
    g_esp = scratch + 64;
    for (i = argc - 1; i >= 0; --i) { g_esp -= 4; MEM32(g_esp) = args[i]; }
    g_esp -= 4; MEM32(g_esp) = RECOMP_RETADDR;
    uint32_t esp_in = g_esp;
    fn();
    uint32_t words = (g_esp - esp_in) / 4u;
    if (out_eax) *out_eax = g_eax;
    g_esp = saved_esp; g_eax = saved_eax;
    return words;
}

static uint32_t guest_str(const char *s) {
    uint32_t va = hle_va(hle_alloc((uint32_t)strlen(s) + 1));
    if (va) memcpy(hle_ptr(va), s, strlen(s) + 1);
    return va;
}
static uint32_t send_str(uint32_t cmd_va, uint32_t ret_va, uint32_t cb) {
    return call("mciSendStringA", 4, (uint32_t[]){ cmd_va, ret_va, cb, 0 });
}
static uint32_t cmd(const char *s, uint32_t ret_va, uint32_t cb) {
    uint32_t va = guest_str(s);
    return va ? send_str(va, ret_va, cb) : 0xFFFFFFFEu;
}
static void expect_reply(const char *buf, const char *want) {
    ok(buf ? buf : "(null)", buf && !strcmp(buf, want));
}
static uint32_t num(const char *s) {
    char *end;
    unsigned long v = strtoul(s ? s : "", &end, 10);
    return (uint32_t)((*s && !*end) ? v : 0xFFFFFFFFu);
}
static void write_wav(void) {
    uint8_t wav[44 + TEST_FRAMES * 4u];
    uint32_t i;
    FILE *f = fopen(WAV_NAME, "wb");
    memset(wav, 0, sizeof wav);
    memcpy(wav, "RIFF", 4);
    wav[4] = (uint8_t)(sizeof wav - 8); wav[5] = (uint8_t)((sizeof wav - 8) >> 8);
    wav[6] = (uint8_t)((sizeof wav - 8) >> 16); wav[7] = (uint8_t)((sizeof wav - 8) >> 24);
    memcpy(wav + 8, "WAVE", 4);
    memcpy(wav + 12, "fmt ", 4);
    wav[16] = 16;                              /* fmt chunk size */
    wav[20] = 1;                               /* PCM */
    wav[22] = 2;                               /* stereo */
    wav[24] = (uint8_t)TEST_RATE; wav[25] = (uint8_t)(TEST_RATE >> 8);
    wav[26] = (uint8_t)(TEST_RATE >> 16); wav[27] = (uint8_t)(TEST_RATE >> 24);
    wav[28] = (uint8_t)(TEST_RATE * 4u); wav[29] = (uint8_t)((TEST_RATE * 4u) >> 8);
    wav[30] = (uint8_t)((TEST_RATE * 4u) >> 16); wav[31] = (uint8_t)((TEST_RATE * 4u) >> 24);
    wav[32] = 4;                               /* block align */
    wav[34] = 16;                              /* bits */
    memcpy(wav + 36, "data", 4);
    wav[40] = (uint8_t)(TEST_FRAMES * 4u); wav[41] = (uint8_t)((TEST_FRAMES * 4u) >> 8);
    wav[42] = (uint8_t)((TEST_FRAMES * 4u) >> 16); wav[43] = (uint8_t)((TEST_FRAMES * 4u) >> 24);
    for (i = 0; i < TEST_FRAMES; i++) {
        int16_t l = (i & 1u) ? 12000 : -12000;
        memcpy(wav + 44 + 4u * i, &l, 2);
        memcpy(wav + 46 + 4u * i, &l, 2);
    }
    if (f) { fwrite(wav, 1, sizeof wav, f); fclose(f); }
    ok("fixture WAV written", f != NULL);
}

int main(void) {
    uint32_t ret = hle_va(hle_alloc(128));
    char *reply = (char *)hle_ptr(ret);
    uint32_t a[4], eax;
    uint32_t id;
    static const char *names[] = { "mciSendStringA", "mciGetErrorStringA", "mciSendCommandA" };

    win32hle_register(win32hle_winmm);
    write_wav();
    ok("return buffer allocated", ret != 0);
    if (!ret) return 1;

    /* Registration. */
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; i++)
        ok(names[i], hle_resolve(names[i]) != 0);

    /* Exact stdcall purge: 1 return word + argc argument words. */
    a[0] = a[1] = a[2] = a[3] = 0;
    ok("mciSendStringA pops 5 words", shim_pops("mciSendStringA", 4, a, &eax) == 5);
    ok("mciGetErrorStringA pops 4 words", shim_pops("mciGetErrorStringA", 3, a, &eax) == 4);
    ok("mciSendCommandA pops 5 words", shim_pops("mciSendCommandA", 4, a, &eax) == 5);

    /* Honest errors before anything is open. */
    ok("status on unknown device -> INVALID_DEVICE_ID",
       cmd("status nosuch mode", ret, 128) == MCI_INVALID_DEVICE_ID);
    ok("unknown command -> UNRECOGNIZED_COMMAND",
       cmd("frobnicate tone", ret, 128) == MCI_UNRECOGNIZED_COMMAND);
    ok("missing file -> FILE_NOT_FOUND",
       cmd("open does_not_exist_xyz.wav type waveaudio alias missing", ret, 128) == MCI_FILE_NOT_FOUND);
    ok("unsupported type -> UNSUPPORTED_FUNCTION",
       cmd("open " WAV_NAME " type avivideo alias avi", ret, 128) == MCI_UNSUPPORTED_FUNCTION);
    ok("unknown open keyword -> UNRECOGNIZED_KEYWORD",
       cmd("open " WAV_NAME " bogus 1", ret, 128) == MCI_UNRECOGNIZED_KEYWORD);
    {
        char *long_cmd = (char *)malloc(MCI_CMD_MAX + 64);
        memset(long_cmd, 'x', MCI_CMD_MAX + 63);
        long_cmd[MCI_CMD_MAX + 63] = 0;
        long_cmd[0] = 'o'; long_cmd[1] = 'p'; long_cmd[2] = 'e'; long_cmd[3] = 'n'; long_cmd[4] = ' ';
        uint32_t lv = guest_str(long_cmd);
        ok("over-long command -> PARAM_OVERFLOW", send_str(lv, ret, 128) == MCI_PARAM_OVERFLOW);
        free(long_cmd);
    }

    /* Real open: decode the WAV and return the decimal device id. */
    memset(reply, 'Z', 128);
    ok("open waveaudio succeeded",
       cmd("open " WAV_NAME " type waveaudio alias tone", ret, 128) == MCI_NO_ERROR);
    ok("open returned a device id string", num(reply) >= 1 && num(reply) <= 8);
    id = num(reply);
    ok("open reply is bounded and NUL-terminated", reply[128 - 1] == 'Z' && strlen(reply) < 8);
    ok("duplicate alias refused",
       cmd("open " WAV_NAME " alias tone", ret, 128) == MCI_DUPLICATE_ALIAS);

    /* Status on the open-but-stopped device. */
    ok("status mode -> stopped", cmd("status tone mode", ret, 128) == MCI_NO_ERROR);
    expect_reply(reply, "stopped");
    ok("status length succeeded", cmd("status tone length", ret, 128) == MCI_NO_ERROR);
    expect_reply(reply, "100");                 /* 4410 frames @ 44.1 kHz */
    ok("status position at open -> 0", cmd("status tone position", ret, 128) == MCI_NO_ERROR);
    expect_reply(reply, "0");
    ok("unknown status item -> UNRECOGNIZED_KEYWORD",
       cmd("status tone thermostat", ret, 128) == MCI_UNRECOGNIZED_KEYWORD);

    /* Volume: 0..1000 maps to the stream's scalar gain. status volume keeps
     * the setting observable through the guest ABI in either build. */
    ok("set volume 500 succeeded", cmd("set tone volume 500", ret, 128) == MCI_NO_ERROR);
    ok("status volume -> 500", cmd("status tone volume", ret, 128) == MCI_NO_ERROR);
    expect_reply(reply, "500");
    ok("set volume out of range -> OUTOFRANGE",
       cmd("set tone volume 1001", ret, 128) == MCI_OUTOFRANGE);
    ok("set volume bad integer -> BAD_INTEGER",
       cmd("set tone volume loud", ret, 128) == MCI_BAD_INTEGER);

    /* Play. In the focused build the recorder holds the feed after one chunk
     * so the state transitions are deterministic; in the whole-layer build the
     * real SDL mixer runs and we observe frames consumed through status
     * position instead. */
#ifdef WINMM_MCI_SELFTEST_MINIMAL
    __atomic_store_n(&g_queued, 0, __ATOMIC_SEQ_CST);
    g_stall_after_push = 1;
#endif
    ok("play succeeded", cmd("play tone", ret, 128) == MCI_NO_ERROR);
    usleep(20000);
#ifdef WINMM_MCI_SELFTEST_MINIMAL
    ok("a fresh stream was opened", g_streams_opened == 1);
    ok("the WAV format reached the stream", g_last_rate == TEST_RATE && g_last_channels == 2);
    ok("PCM was pushed into the mixer stream", g_push_calls > 0 && g_pushed_frames > 0);
    ok("the pushed samples are the decoded WAV", g_first_sample == 12000 || g_first_sample == -12000);
    ok("volume gain was applied on play", g_gain_milli == 500);
    uint32_t pushed_while_playing = g_pushed_frames;
#else
    ok("real audio stream consumed frames", cmd("status tone position", ret, 128) == MCI_NO_ERROR);
    ok("position advanced from the audio clock", num(reply) > 0 && num(reply) <= TEST_MS);
    printf("real mixer position after play: %s ms\n", reply);
#endif
    ok("status mode -> playing", cmd("status tone mode", ret, 128) == MCI_NO_ERROR);
    expect_reply(reply, "playing");

    /* Pause mutes and holds; resume restores the state and the setting. */
    ok("pause succeeded", cmd("pause tone", ret, 128) == MCI_NO_ERROR);
#ifdef WINMM_MCI_SELFTEST_MINIMAL
    ok("pause muted the stream", g_gain_milli == 0);
#endif
    ok("status mode -> paused", cmd("status tone mode", ret, 128) == MCI_NO_ERROR);
    expect_reply(reply, "paused");
    usleep(10000);
#ifdef WINMM_MCI_SELFTEST_MINIMAL
    ok("paused feed stopped pushing", g_pushed_frames == pushed_while_playing);
#endif
    ok("resume succeeded", cmd("resume tone", ret, 128) == MCI_NO_ERROR);
#ifdef WINMM_MCI_SELFTEST_MINIMAL
    ok("resume restored the volume gain", g_gain_milli == 500);
#endif
    ok("status volume survives pause/resume", cmd("status tone volume", ret, 128) == MCI_NO_ERROR);
    expect_reply(reply, "500");
    ok("status mode -> playing after resume", cmd("status tone mode", ret, 128) == MCI_NO_ERROR);
    expect_reply(reply, "playing");

    /* Seek to the midpoint is a real reposition (the feed restarts from that
     * frame), then stop closes the stream and reports a bounded position. */
#ifdef WINMM_MCI_SELFTEST_MINIMAL
    __atomic_store_n(&g_queued, 0, __ATOMIC_SEQ_CST);
#endif
    ok("seek to 50 ms succeeded", cmd("seek tone to 50", ret, 128) == MCI_NO_ERROR);
    usleep(20000);
#ifdef WINMM_MCI_SELFTEST_MINIMAL
    ok("seek pushed more PCM", g_pushed_frames > pushed_while_playing);
    g_stall_after_push = 0;
    __atomic_store_n(&g_queued, 1, __ATOMIC_SEQ_CST);
#else
    ok("seek position is within the WAV", cmd("status tone position", ret, 128) == MCI_NO_ERROR);
    ok("seek position <= length", num(reply) <= TEST_MS);
#endif
    ok("stop succeeded", cmd("stop tone", ret, 128) == MCI_NO_ERROR);
    ok("status mode -> stopped after stop", cmd("status tone mode", ret, 128) == MCI_NO_ERROR);
    expect_reply(reply, "stopped");
    ok("status position is within the WAV", cmd("status tone position", ret, 128) == MCI_NO_ERROR);
    ok("stop position <= length", num(reply) <= TEST_MS);

    /* Play again with an empty queue so the feed drains and completes on its
     * own; position then equals the full length. */
#ifdef WINMM_MCI_SELFTEST_MINIMAL
    __atomic_store_n(&g_queued, 0, __ATOMIC_SEQ_CST);
#endif
    ok("replay succeeded", cmd("play tone", ret, 128) == MCI_NO_ERROR);
    for (int i = 0; i < 3000; i++) {
        cmd("status tone mode", ret, 128);
        if (reply && !strcmp(reply, "stopped")) break;
        usleep(1000);
    }
    ok("play completed naturally", reply && !strcmp(reply, "stopped"));
    ok("status position -> full length", cmd("status tone position", ret, 128) == MCI_NO_ERROR);
    expect_reply(reply, "100");
#ifdef WINMM_MCI_SELFTEST_MINIMAL
    ok("stream closed on natural completion", g_streams_closed >= 2);
#endif

    /* Close releases the device: the alias can be reused, and a stale name is
     * now an invalid device id. */
    ok("close succeeded", cmd("close tone", ret, 128) == MCI_NO_ERROR);
    ok("closed alias -> INVALID_DEVICE_ID",
       cmd("status tone mode", ret, 128) == MCI_INVALID_DEVICE_ID);
    ok("alias reusable after close",
       cmd("open " WAV_NAME " type waveaudio alias tone", ret, 128) == MCI_NO_ERROR);
    ok("device id still parses", num(reply) == id);
    ok("close again succeeded", cmd("close tone", ret, 128) == MCI_NO_ERROR);

    /* mciGetErrorStringA works without a device, bounded by cchReturn. */
    ok("mciGetErrorStringA -> TRUE",
       call("mciGetErrorStringA", 3, (uint32_t[]){ MCI_FILE_NOT_FOUND, ret, 128 }) == 1);
    ok("error text is meaningful", reply && strstr(reply, "File"));
    memset(reply, 'X', 16);
    ok("mciGetErrorStringA short buffer -> TRUE",
       call("mciGetErrorStringA", 3, (uint32_t[]){ MCI_FILE_NOT_FOUND, ret, 1 }) == 1);
    ok("short error text is NUL terminated", reply[0] == 0 && reply[1] == 'X');
    ok("mciGetErrorStringA with no buffer -> FALSE",
       call("mciGetErrorStringA", 3, (uint32_t[]){ MCI_NO_ERROR, 0, 0 }) == 0);

    /* The message form stays honestly unsupported. */
    ok("mciSendCommandA -> UNSUPPORTED_FUNCTION",
       call("mciSendCommandA", 4, (uint32_t[]){ 0, 0, 0, 0 }) == MCI_UNSUPPORTED_FUNCTION);

    unlink(WAV_NAME);
    printf("winmm_mci_selftest: %s\n", fails ? "FAILURES" : "all checks passed");
    return fails != 0;
}
