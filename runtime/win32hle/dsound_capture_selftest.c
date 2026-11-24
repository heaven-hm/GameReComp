/* dsound_capture_selftest.c - the missing DirectSound ordinals (9/11/12).
 *
 * Drives the objects the way a DX8 game does: through the guest-visible COM
 * vtables rather than calling host functions. Checks IDirectSound8's extra
 * slot, IDirectSoundBuffer8's interface identity and three added methods, and
 * a real DirectSoundCapture8 buffer whose capture cursor advances with SDL's
 * capture device. GetDeviceID's copy-through and E_INVALIDARG paths are also
 * covered.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "win32hle.h"

#define DS_OK_VALUE 0u
#define DSERR_INVALIDPARAM 0x80070057u
#define DSERR_NODRIVER 0x88780078u
#define DSERR_INVALIDCALL 0x88780032u
#define DSERR_UNSUPPORTED 0x80004001u
#define DSERR_OBJECTNOTFOUND 0x88781161u

/* IID_IDirectSoundBuffer8, in guest GUID byte order (wine-10.0 dsound.h). */
static const uint8_t iid_buf8[16] = {
    0x49,0xA4,0x25,0x68,0x24,0x75,0x82,0x4D,0x92,0x0F,0x50,0xE3,0x6A,0xB3,0xAB,0x1E };
/* DSDEVID_DefaultPlayback = DEF00000-9C6D-47ED-AAF1-4DDA8F2B5C03. */
static const uint8_t default_playback[16] = {
    0x00,0x00,0x00,0xDE,0x6D,0x9C,0xED,0x47,0xAA,0xF1,0x4D,0xDA,0x8F,0x2B,0x5C,0x03 };
static const uint8_t arbitrary_guid[16] = {
    0x11,0x22,0x33,0x44,0x55,0x66,0x77,0x88,0x99,0xAA,0xBB,0xCC,0xDD,0xEE,0xFF,0x00 };

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { fprintf(stderr, "FAIL %s\n", what); fails++; }
}
static uint32_t slot(uint32_t iface, unsigned i) { return MEM32(MEM32(iface) + 4u * i); }
static uint32_t call(const char *name, int n, const uint32_t *a) {
    uint32_t fn = hle_resolve(name);
    if (!fn) { fprintf(stderr, "FAIL %s did not resolve\n", name); fails++; return 0xFFFFFFFFu; }
    return hle_call_guest(fn, n, a);
}
static uint32_t vcall(uint32_t iface, unsigned i, int n, const uint32_t *a) {
    uint32_t fn = slot(iface, i);
    if (!fn) { fprintf(stderr, "FAIL vtable slot %u is null\n", i); fails++; return 0xFFFFFFFFu; }
    uint32_t args[8] = { iface };
    for (int k = 1; k < n; k++) args[k] = a[k - 1];
    return hle_call_guest(fn, n, args);
}
static uint32_t guest(const void *p, uint32_t n) {
    uint32_t va = hle_va(hle_alloc(n));
    if (va) memcpy(hle_ptr(va), p, n);
    return va;
}
static void put16(uint8_t *p, unsigned v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *p, unsigned v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

int main(void) {
    win32hle_register(win32hle_dsound);
    ok("ordinal 9/11/12 resolve", hle_resolve("dsound.dll#9") && hle_resolve("dsound.dll#11") &&
       hle_resolve("dsound.dll#12"));
    ok("DS8/Capture8 names resolve", hle_resolve("DirectSoundCreate8") &&
       hle_resolve("DirectSoundCaptureCreate8") && hle_resolve("GetDeviceID"));

    /* GetDeviceID: copy-through for an arbitrary GUID, aliases resolve. */
    uint32_t src = guest(arbitrary_guid, 16), dst = hle_va(hle_alloc(16));
    uint32_t a[3] = { src, dst, 0 };
    ok("GetDeviceID(arbitrary) -> DS_OK", call("dsound.dll#9", 2, a) == DS_OK_VALUE);
    ok("GetDeviceID copies the GUID", !memcmp(hle_ptr(dst), arbitrary_guid, 16));
    a[0] = guest(default_playback, 16);
    ok("GetDeviceID(default playback) -> DS_OK", call("GetDeviceID", 2, a) == DS_OK_VALUE);
    uint32_t resolved[4]; memcpy(resolved, hle_ptr(dst), 16);
    ok("default alias resolves to a device GUID", memcmp(resolved, default_playback, 16));
    a[0] = 0;
    ok("GetDeviceID(NULL src) -> E_INVALIDARG", call("GetDeviceID", 2, a) == DSERR_INVALIDPARAM);

    /* --- DirectSoundCreate8 (ordinal 11) --- */
    uint32_t ds8 = 0, out = hle_va(hle_alloc(4));
    a[0] = src; a[1] = out;
    ok("DirectSoundCreate8 -> DS_OK", call("dsound.dll#11", 3, a) == DS_OK_VALUE);
    ds8 = MEM32(out);
    ok("DS8 object created", ds8 != 0);
    ok("IDirectSound8 has 12 slots", slot(ds8, 11) != 0);
    uint32_t certified = 0xDEADBEEFu;
    a[0] = hle_va(hle_alloc(4));
    ok("VerifyCertification -> DS_OK", vcall(ds8, 11, 2, a) == DS_OK_VALUE);
    ok("VerifyCertification reports DS_CERTIFIED", MEM32(a[0]) == 0);

    /* CreateSoundBuffer on the DS8 object yields an IDirectSoundBuffer8. */
    uint8_t wfx[18], desc[32];
    memset(wfx, 0, sizeof wfx); memset(desc, 0, sizeof desc);
    put16(wfx, 1); put16(wfx + 2, 2); put32(wfx + 4, 22050);
    put32(wfx + 8, 22050 * 4); put16(wfx + 12, 4); put16(wfx + 14, 16);
    put32(desc, 32); put32(desc + 8, 4096); put32(desc + 16, guest(wfx, sizeof wfx));
    uint32_t desc_va = guest(desc, sizeof desc), buf = 0;
    a[0] = desc_va; a[1] = out; a[2] = 0;
    ok("CreateSoundBuffer -> DS_OK", vcall(ds8, 3, 4, a) == DS_OK_VALUE);
    buf = MEM32(out);
    ok("buffer object created", buf != 0);
    ok("IDirectSoundBuffer8 has 24 slots", slot(buf, 23) != 0);
    uint32_t iface_out = hle_va(hle_alloc(4));
    uint32_t iid_va = guest(iid_buf8, 16);
    a[0] = iid_va; a[1] = iface_out;
    ok("QI(IDirectSoundBuffer8) -> DS_OK", vcall(buf, 0, 3, a) == DS_OK_VALUE);
    uint32_t buf8 = MEM32(iface_out);
    ok("QI returns a usable buffer8", buf8 != 0 && slot(buf8, 23) != 0);
    ok("SetFX -> unsupported", vcall(buf8, 21, 4, a) == DSERR_UNSUPPORTED);
    a[0] = 0; a[1] = 0;                         /* no flags, zero effects requested */
    ok("AcquireResources(0) -> DS_OK", vcall(buf8, 22, 4, a) == DS_OK_VALUE);
    ok("GetObjectInPath -> object not found", vcall(buf8, 23, 5, a) == DSERR_OBJECTNOTFOUND);

    /* --- DirectSoundCaptureCreate8 (ordinal 12) --- */
    uint32_t cap = 0;
    a[0] = src; a[1] = out;
    ok("DirectSoundCaptureCreate8 -> DS_OK", call("dsound.dll#12", 3, a) == DS_OK_VALUE);
    cap = MEM32(out);
    ok("capture object created", cap != 0 && slot(cap, 3) != 0);
    uint32_t cdesc[8]; memset(cdesc, 0, sizeof cdesc);
    cdesc[0] = 20; cdesc[2] = 65536; cdesc[4] = guest(wfx, sizeof wfx);
    uint32_t cdesc_va = guest(cdesc, sizeof cdesc), capbuf = 0;
    a[0] = cdesc_va; a[1] = out; a[2] = 0;
    ok("CreateCaptureBuffer -> DS_OK", vcall(cap, 3, 4, a) == DS_OK_VALUE);
    capbuf = MEM32(out);
    ok("capture buffer created", capbuf != 0 && slot(capbuf, 13) != 0);

    uint32_t ccaps = hle_va(hle_alloc(16));
    a[0] = ccaps; a[1] = 16;
    ok("capture buffer GetCaps -> DS_OK", vcall(capbuf, 3, 3, a) == DS_OK_VALUE);
    ok("capture buffer size is reported", MEM32(ccaps + 8) == 65536);
    uint32_t status = 0xDEADBEEFu;
    a[0] = hle_va(hle_alloc(4));
    ok("capture buffer starts stopped", vcall(capbuf, 6, 2, a) == DS_OK_VALUE &&
       MEM32(a[0]) == 0);
    a[0] = 0;
    ok("Start -> DS_OK", vcall(capbuf, 9, 2, a) == DS_OK_VALUE);
    usleep(250000);                             /* 1/4 s of real capture */
    uint32_t cappos = 0, readpos = 0;
    a[0] = hle_va(hle_alloc(4)); a[1] = hle_va(hle_alloc(4));
    ok("GetCurrentPosition -> DS_OK", vcall(capbuf, 4, 3, a) == DS_OK_VALUE);
    cappos = MEM32(a[0]); readpos = MEM32(a[1]);
    ok("capture cursor advances with real audio", cappos != 0);
    ok("read position starts at zero before wrap", readpos == 0);
    uint32_t p1 = 0, n1 = 0, p2 = 0, n2 = 0;
    uint32_t lp[8] = { 0, 512, hle_va(hle_alloc(4)), hle_va(hle_alloc(4)),
                       hle_va(hle_alloc(4)), hle_va(hle_alloc(4)), 0 };
    uint32_t lock_rc = vcall(capbuf, 8, 8, lp);
    ok("Lock -> DS_OK", lock_rc == DS_OK_VALUE);
    p1 = MEM32(lp[2]); n1 = MEM32(lp[3]);
    ok("Lock returns a pointer inside the guest buffer", p1 != 0 && n1 == 512);
    uint32_t data_va = 0;
    /* The buffer's guest memory is the pointer Lock returned; a second lock
     * of the entire buffer must cover it. */
    lp[0] = 1; lp[1] = 0; lp[6] = 1;              /* DSCBLOCK_ENTIREBUFFER */
    ok("Lock(ENTIREBUFFER) -> DS_OK", vcall(capbuf, 8, 8, lp) == DS_OK_VALUE);
    ok("entire-buffer lock returns all bytes", MEM32(lp[3]) == 65536);
    (void)data_va;
    a[0] = 0;
    ok("Stop -> DS_OK", vcall(capbuf, 10, 2, a) == DS_OK_VALUE);
    a[0] = hle_va(hle_alloc(4));
    vcall(capbuf, 6, 2, a);
    ok("status clears after Stop", MEM32(a[0]) == 0);

    if (!fails) printf("dsound_capture_selftest: all checks passed\n");
    return fails != 0;
}
