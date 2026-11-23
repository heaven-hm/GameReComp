/*
 * dsound_enumerate_selftest.c - the four DirectSound enumeration shims, driven
 * from a synthetic lifted guest callback. Checks the guest stdcall ABI (four
 * args in order, BOOL return honoured, context round-tripped), the strings and
 * GUIDs the callback receives against SDL's own device list, the W strings,
 * the capture direction, and the E_INVALIDARG on a NULL callback.
 *
 * Headless: SDL_AUDIODRIVER=dummy still reports one playback and one capture
 * device, which is what the shims must enumerate. No real hardware needed.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

#define CB_VA 0x00401000u
#define MAX_SEEN 64
#define DS_OK_VALUE        0x00000000u
#define DSERR_INVALIDPARAM 0x80070057u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

/* The lifter's prologue for a lifted function. */
#define GUEST_PROLOGUE(va) \
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0; RECOMP_ENTER(va)

static int g_calls, g_stop_after, g_wide;
static char seen_a[MAX_SEEN][256];
static uint16_t seen_w[MAX_SEEN][256];
static char seen_mod_a[256];
static uint16_t seen_mod_w[256];
static uint32_t seen_guid[MAX_SEEN], seen_ctx[MAX_SEEN];

/* DSEnumCallback: BOOL CALLBACK(LPGUID, LPSTR, LPSTR, LPVOID), stdcall. Reads
 * its four args off the guest stack, records what the host handed it, returns
 * FALSE after g_stop_after calls to prove the shim uses the callback result. */
static void enum_cb(void) {
    GUEST_PROLOGUE(CB_VA);
    uint32_t guid = MEM32(esp + 4), desc = MEM32(esp + 8);
    uint32_t module = MEM32(esp + 12), ctx = MEM32(esp + 16);
    int i = g_calls < MAX_SEEN ? g_calls : MAX_SEEN - 1;
    g_calls++;
    seen_guid[i] = guid;
    seen_ctx[i] = ctx;
    if (g_wide) {
        const uint16_t *w = (const uint16_t *)hle_ptr(desc);
        int k = 0;
        if (w) for (; k < 255 && w[k]; k++) seen_w[i][k] = w[k];
        seen_w[i][k] = 0;
        const uint16_t *m = (const uint16_t *)hle_ptr(module);
        int j = 0;
        if (m) for (; j < 255 && m[j]; j++) seen_mod_w[j] = m[j];
        seen_mod_w[j] = 0;
    } else {
        const char *s = (const char *)hle_ptr(desc);
        strncpy(seen_a[i], s ? s : "", sizeof seen_a[i] - 1);
        seen_a[i][sizeof seen_a[i] - 1] = 0;
        const char *m = (const char *)hle_ptr(module);
        strncpy(seen_mod_a, m ? m : "", sizeof seen_mod_a - 1);
        seen_mod_a[sizeof seen_mod_a - 1] = 0;
    }
    eax = (g_stop_after && g_calls >= g_stop_after) ? 0u : 1u;
    esp += 4 + 4u * 4u;                         /* callee pops ret + 4 args */
}

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { CB_VA, enum_cb } };
const uint32_t recomp_dispatch_count = 1;

static void wide_to_narrow(const uint16_t *w, char *out, size_t n) {
    size_t i = 0;
    if (!w || !n) { if (n) out[0] = 0; return; }
    for (; i + 1 < n && w[i]; i++) out[i] = (char)(w[i] & 0xFFu);
    out[i] = 0;
}

static int expected_count(int capture) {
    if (SDL_InitSubSystem(SDL_INIT_AUDIO) != 0 && !(SDL_WasInit(SDL_INIT_AUDIO) & SDL_INIT_AUDIO)) return -1;
    int n = SDL_GetNumAudioDevices(capture);
    return n < 0 ? 0 : (n > MAX_SEEN ? MAX_SEEN : n);
}

static void check(const char *label, uint32_t fn, int capture, int wide) {
    char what[160];
    int want = expected_count(capture);
    ok("audio subsystem available", want >= 0);
    if (want < 0) return;

    g_calls = 0; g_stop_after = 0; g_wide = wide;
    uint32_t ctx = 0xC0FFEEu;
    uint32_t r = hle_call_guest(fn, 2, (uint32_t[]){ CB_VA, ctx });
    snprintf(what, sizeof what, "%s returns DS_OK", label);
    ok(what, r == DS_OK_VALUE);
    snprintf(what, sizeof what, "%s visits exactly SDL's devices", label);
    ok(what, g_calls == want);

    for (int i = 0; i < g_calls && i < want; i++) {
        const char *name = SDL_GetAudioDeviceName(i, capture);
        char got[256], mod[256];
        if (wide) {
            wide_to_narrow(seen_w[i], got, sizeof got);
            wide_to_narrow(seen_mod_w, mod, sizeof mod);
        } else {
            snprintf(got, sizeof got, "%s", seen_a[i]);
            snprintf(mod, sizeof mod, "%s", seen_mod_a);
        }
        snprintf(what, sizeof what, "%s device %d description is SDL's name", label, i);
        ok(what, name && !strcmp(got, name));
        snprintf(what, sizeof what, "%s device %d has a guest GUID and module", label, i);
        ok(what, seen_guid[i] != 0 && mod[0] != 0);
        snprintf(what, sizeof what, "%s device %d context round-trips", label, i);
        ok(what, seen_ctx[i] == ctx);
    }
    if (want > 1) {
        snprintf(what, sizeof what, "%s gives each device a distinct GUID", label);
        ok(what, seen_guid[0] != seen_guid[1]);
    }

    /* BOOL FALSE from the callback must end the enumeration, but the call
     * still succeeds -- that is the Windows contract, not an error. */
    if (want > 0) {
        g_calls = 0; g_stop_after = 1;
        r = hle_call_guest(fn, 2, (uint32_t[]){ CB_VA, ctx });
        snprintf(what, sizeof what, "%s stops on a FALSE callback", label);
        ok(what, r == DS_OK_VALUE && g_calls == 1);
    }
    snprintf(what, sizeof what, "%s rejects a NULL callback with E_INVALIDARG", label);
    ok(what, hle_call_guest(fn, 2, (uint32_t[]){ 0, ctx }) == DSERR_INVALIDPARAM);
}

int main(void) {
    win32hle_register(win32hle_dsound);
    uint32_t a = hle_resolve("DirectSoundEnumerateA");
    uint32_t w = hle_resolve("DirectSoundEnumerateW");
    uint32_t ca = hle_resolve("DirectSoundCaptureEnumerateA");
    uint32_t cw = hle_resolve("DirectSoundCaptureEnumerateW");
    ok("all four enumeration shims resolve", a && w && ca && cw);
    uint32_t o2 = hle_resolve("dsound.dll#2"), o3 = hle_resolve("dsound.dll#3");
    uint32_t o7 = hle_resolve("dsound.dll#7"), o8 = hle_resolve("dsound.dll#8");
    ok("dsound.dll ordinals 2/3/7/8 resolve", o2 && o3 && o7 && o8);

    mach_enter();
    check("DirectSoundEnumerateA", a, 0, 0);
    check("DirectSoundEnumerateW", w, 0, 1);
    check("DirectSoundCaptureEnumerateA", ca, 1, 0);
    check("DirectSoundCaptureEnumerateW", cw, 1, 1);

    /* The ordinal aliases are real entry points, not just names. */
    int want = expected_count(0);
    g_calls = 0; g_stop_after = 0; g_wide = 0;
    ok("dsound.dll#2 is DirectSoundEnumerateA",
       hle_call_guest(o2, 2, (uint32_t[]){ CB_VA, 7u }) == DS_OK_VALUE && g_calls == want &&
       (want == 0 || seen_ctx[0] == 7u));
    g_calls = 0; g_wide = 1;
    ok("dsound.dll#3 is DirectSoundEnumerateW",
       hle_call_guest(o3, 2, (uint32_t[]){ CB_VA, 8u }) == DS_OK_VALUE && g_calls == want &&
       (want == 0 || seen_ctx[0] == 8u));
    g_calls = 0; g_wide = 0;
    ok("dsound.dll#7 is DirectSoundCaptureEnumerateA", hle_call_guest(o7, 2, (uint32_t[]){ CB_VA, 0u }) == DS_OK_VALUE);
    g_calls = 0; g_wide = 1;
    ok("dsound.dll#8 is DirectSoundCaptureEnumerateW", hle_call_guest(o8, 2, (uint32_t[]){ CB_VA, 0u }) == DS_OK_VALUE);
    mach_leave();

    if (fails == 0) printf("dsound_enumerate_selftest: all checks passed\n");
    return fails != 0;
}
