/* winmm_midi_selftest.c - the no-device MIDI mapper subset, driven as a guest.
 *
 * Focused build (small, for a shared disk): only winmm.c and the four spine
 * files it needs are linked; WINMM_MIDI_SELFTEST_MINIMAL supplies the few
 * symbols that live in dsound.c/kernel32.c in the full build. The same file
 * also links cleanly with the whole layer under build_selftests.sh (no macro):
 * the stubs are compiled only in the focused build.
 *
 *   cd runtime/win32hle
 *   TMPDIR=/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode \
 *   cc -O1 -Wall -Wno-unused-function -Wno-unused-but-set-variable \
 *      -DWINMM_MIDI_SELFTEST_MINIMAL -I../recomp32 -I. \
 *      winmm.c host_lite.c guest_mem.c clock.c hle_path.c \
 *      winmm_midi_selftest.c -lpthread -lm -o $TMPDIR/winmm_midi_selftest
 *   SDL_AUDIODRIVER=dummy $TMPDIR/winmm_midi_selftest
 *
 * Checks: zero-device enumeration; MMSYSERR_BADDEVICEID for every operation;
 * bounded caps writes; no fabricated handle; stdcall purge for each shim;
 * working error-text helpers; exactly one diagnostic for the whole family.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#ifdef WINMM_MIDI_SELFTEST_MINIMAL
/* Symbols winmm.c references for paths this selftest never exercises. They
 * are the real definitions in dsound.c / kernel32.c in a whole-layer build;
 * here they keep the focused link small without overriding those definitions. */
void hle_exit(int code) { (void)code; fprintf(stderr, "hle_exit\n"); abort(); }
uint32_t hle_thread_id(void) { return 1; }
uint32_t hle_ticks_ms(void) { return hle_clock_ms(); }
void hle_set_event(uint32_t h) { (void)h; }
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

enum { MMSYS_NOERROR = 0u, MMSYS_BADDEVICEID = 2u };
enum { OUT_CAPS_A = 52, IN_CAPS_A = 44 };

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
    else printf("ok   %s\n", what);
}
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
/* Every registered name, its documented argument count, and whether it is a
 * failing device call (those must all report MMSYSERR_BADDEVICEID). */
static const struct { const char *name; int argc; int fails; } kApi[] = {
    { "midiOutGetNumDevs",      0, 0 }, { "midiInGetNumDevs",       0, 0 },
    { "midiOutGetDevCapsA",     3, 1 }, { "midiInGetDevCapsA",      3, 1 },
    { "midiOutOpen",            5, 1 }, { "midiInOpen",             5, 1 },
    { "midiOutClose",           1, 1 }, { "midiInClose",            1, 1 },
    { "midiOutShortMsg",        2, 1 }, { "midiOutLongMsg",         3, 1 },
    { "midiOutReset",           1, 1 }, { "midiOutGetVolume",       2, 1 },
    { "midiOutSetVolume",       2, 1 }, { "midiOutPrepareHeader",   3, 1 },
    { "midiOutUnprepareHeader", 3, 1 }, { "midiInPrepareHeader",    3, 1 },
    { "midiInUnprepareHeader",  3, 1 }, { "midiInAddBuffer",        3, 1 },
    { "midiInStart",            1, 1 }, { "midiInStop",             1, 1 },
    { "midiInReset",            1, 1 }, { "midiOutGetID",           2, 1 },
    { "midiInGetID",            2, 1 }, { "midiOutGetErrorTextA",   3, 0 },
    { "midiInGetErrorTextA",    3, 0 },
};
#define NAPI ((int)(sizeof kApi / sizeof kApi[0]))

static void diag_begin(int *saved_fd, char *path, size_t path_n) {
    const char *tmp = getenv("TMPDIR");
    snprintf(path, path_n, "%s/winmm_midi_selftest_diag.txt", tmp && *tmp ? tmp : "/tmp");
    fflush(stderr);
    *saved_fd = dup(2);
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd >= 0) dup2(fd, 2);
    if (fd >= 0 && fd != 2) close(fd);
}
static void diag_end(int saved_fd, const char *path) {
    fflush(stderr);
    if (saved_fd >= 0) { dup2(saved_fd, 2); close(saved_fd); }
}
static int diag_count(const char *path, const char *needle) {
    FILE *f = fopen(path, "rb");
    char line[512];
    int n = 0;
    if (!f) return -1;
    while (fgets(line, sizeof line, f))
        if (strstr(line, needle)) n++;
    fclose(f);
    return n;
}

int main(void) {
    int saved_fd, i;
    char diag_path[1024];
    uint32_t zero[1] = { 0 };

    win32hle_register(win32hle_winmm);
    mach_enter();

    for (i = 0; i < NAPI; i++) ok(kApi[i].name, hle_resolve(kApi[i].name) != 0);

    /* Enumeration is honest: no device is reported, and nothing is logged. */
    ok("midiOutGetNumDevs -> 0", call("midiOutGetNumDevs", 0, zero) == 0);
    ok("midiInGetNumDevs -> 0", call("midiInGetNumDevs", 0, zero) == 0);

    diag_begin(&saved_fd, diag_path, sizeof diag_path);

    /* caps: error, bounded zeroing of the real ANSI struct only. */
    {
        uint32_t caps_mem = hle_va(hle_alloc(128));
        unsigned char *caps = (unsigned char *)hle_ptr(caps_mem);
        uint32_t a[3];
        ok("caps guest buffer", caps_mem != 0);
        if (caps_mem) {
            memset(caps, 0xAA, 128);
            a[0] = 0; a[1] = caps_mem; a[2] = OUT_CAPS_A;
            ok("midiOutGetDevCapsA -> BADDEVICEID", call("midiOutGetDevCapsA", 3, a) == MMSYS_BADDEVICEID);
            ok("caps zeroed within cb", caps[0] == 0 && caps[OUT_CAPS_A - 1] == 0);
            ok("caps write bounded by struct size", caps[OUT_CAPS_A] == 0xAA && caps[127] == 0xAA);

            memset(caps, 0xAA, 128);
            a[0] = 999; a[1] = caps_mem; a[2] = 4;
            ok("unknown device id -> BADDEVICEID", call("midiOutGetDevCapsA", 3, a) == MMSYS_BADDEVICEID);
            ok("tiny cb zeroes only cb bytes", caps[0] == 0 && caps[3] == 0 && caps[4] == 0xAA);

            a[0] = 0; a[1] = 0; a[2] = OUT_CAPS_A;
            ok("NULL caps -> BADDEVICEID (no crash)", call("midiOutGetDevCapsA", 3, a) == MMSYS_BADDEVICEID);

            memset(caps, 0xAA, 128);
            a[0] = 0; a[1] = caps_mem; a[2] = IN_CAPS_A;
            ok("midiInGetDevCapsA -> BADDEVICEID", call("midiInGetDevCapsA", 3, a) == MMSYS_BADDEVICEID);
            ok("in-caps zeroed, bounded", caps[0] == 0 && caps[IN_CAPS_A - 1] == 0 && caps[IN_CAPS_A] == 0xAA);
        }
    }

    /* No fake handle is left behind by a failed open. */
    {
        uint32_t h = hle_va(hle_alloc(4)), a[5];
        ok("handle guest buffer", h != 0);
        if (h) {
            MEM32(h) = 0xDEADBEEFu;
            a[0] = h; a[1] = 0; a[2] = 0; a[3] = 0; a[4] = 0;
            ok("midiOutOpen -> BADDEVICEID", call("midiOutOpen", 5, a) == MMSYS_BADDEVICEID);
            ok("out open leaves no handle", MEM32(h) == 0);
            MEM32(h) = 0xDEADBEEFu;
            ok("midiInOpen -> BADDEVICEID", call("midiInOpen", 5, a) == MMSYS_BADDEVICEID);
            ok("in open leaves no handle", MEM32(h) == 0);
        }
    }

    /* Every remaining device call reports the same documented error. */
    {
        uint32_t a[5] = { 0, 0, 0, 0, 0 };
        for (i = 0; i < NAPI; i++) {
            if (!kApi[i].fails) continue;
            if (!strcmp(kApi[i].name, "midiOutGetDevCapsA") || !strcmp(kApi[i].name, "midiInGetDevCapsA") ||
                !strcmp(kApi[i].name, "midiOutOpen") || !strcmp(kApi[i].name, "midiInOpen")) continue;
            ok(kApi[i].name, call(kApi[i].name, kApi[i].argc, a) == MMSYS_BADDEVICEID);
        }
    }

    /* Error text needs no device: it formats the code, with a NUL always. */
    {
        uint32_t buf = hle_va(hle_alloc(64)), a[3];
        char *s = (char *)hle_ptr(buf);
        ok("error text guest buffer", buf != 0);
        if (buf) {
            a[0] = MMSYS_BADDEVICEID; a[1] = buf; a[2] = 32;
            ok("midiOutGetErrorTextA -> NOERROR", call("midiOutGetErrorTextA", 3, a) == MMSYS_NOERROR);
            ok("error text is meaningful", s[0] && strstr(s, "device") != NULL);
            a[0] = MMSYS_BADDEVICEID; a[1] = buf; a[2] = 1;
            memset(s, 'X', 8);
            ok("midiInGetErrorTextA -> NOERROR", call("midiInGetErrorTextA", 3, a) == MMSYS_NOERROR);
            ok("short cb still NUL-terminated", s[0] == 0 && s[1] == 'X');
        }
    }

    diag_end(saved_fd, diag_path);
    ok("exactly one no-MIDI diagnostic", diag_count(diag_path, "[winmm] MIDI:") == 1);
    {
        FILE *f = fopen(diag_path, "rb");
        char line[512];
        if (f && fgets(line, sizeof line, f)) {
            printf("diag: %s", line);
            ok("diagnostic names first failing API", strstr(line, "midiOutGetDevCapsA") != NULL);
        } else {
            ok("diagnostic readable", 0);
        }
        if (f) fclose(f);
        unlink(diag_path);
    }

    /* stdcall purge: 1 return word + argc argument words for every shim. */
    {
        uint32_t a[5] = { 0, 0, 0, 0, 0 }, eax;
        for (i = 0; i < NAPI; i++) {
            char what[96];
            snprintf(what, sizeof what, "%s pops %d words", kApi[i].name, 1 + kApi[i].argc);
            ok(what, shim_pops(kApi[i].name, kApi[i].argc, a, &eax) == (uint32_t)(1 + kApi[i].argc));
        }
    }

    mach_leave();
    printf("winmm_midi_selftest: %s\n", fails ? "FAILURES" : "all checks passed");
    return fails != 0;
}
