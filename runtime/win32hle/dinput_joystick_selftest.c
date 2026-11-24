/* dinput_joystick_selftest.c - honest joystick enumeration/device ABI.
 *
 * Every call goes through hle_call_guest on a real import VA, and the shims are
 * driven with a synthetic guest stack so each stdcall purge is counted (ret +
 * argc words) rather than assumed. Enumeration callbacks are registered shims,
 * so the native -> guest -> native argument passing is exercised too.
 *
 * The no-device path is forced with RECOMP_DI_NO_JOYSTICK=1, a diagnostic
 * override that only ever REMOVES devices: the shim must report zero joystick
 * callbacks and the documented DIERR codes, not a fabricated centred stick.
 * With the override off, the test compares against SDL_NumJoysticks() so a host
 * that really has a stick is reported, not assumed absent. No force feedback,
 * XInput, HID or Raw Input is claimed or tested here.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

/* Minimal-core link: weak host stubs, overridden by the strong definitions in
 * user32.c / screen.c / kernel32.c when the full core is linked. They give the
 * mouse poll a neutral sample; the joystick path under test never uses them. */
__attribute__((weak)) void hle_screen_mouse(int *x, int *y) {
    if (x) *x = 0;
    if (y) *y = 0;
}
__attribute__((weak)) unsigned hle_input_mouse_buttons(void) { return 0; }
__attribute__((weak)) void hle_exit(int code) { exit(code); }
__attribute__((weak)) uint32_t hle_thread_id(void) { return 1; }

#define DI_OK_VALUE             0x00000000u
#define DI_NOTATTACHED_VALUE    0x00000001u
#define DIERR_INVALIDPARAM      0x80070057u
#define DIERR_NOTACQUIRED       0x8007000Cu
#define DIERR_DEVICENOTREG      0x80040154u
#define DIERR_UNSUPPORTED       0x80004001u
#define DIENUM_CONTINUE         1u

#define DIDEVTYPE_JOYSTICK      4u
#define DI8DEVTYPE_JOYSTICK     0x14u
#define DI8DEVCLASS_GAMECTRL    4u
#define DI8DEVCLASS_ALL         0u
#define DIEDFL_FORCEFEEDBACK    0x100u
#define DIDC_FORCEFEEDBACK      0x100u

enum { DX3_SIZE = 560, DX3_DEVTYPE = 36, DX3_NAME = 40,
       DI8_SIZE = 580, DI8_DEVTYPE = 36, DI8_NAME = 40,
       DIPROPHEADER_SIZE = 16, DIPROPDWORD_SIZE = 20,
       DIPROP_JOYSTICKID = 15, DIPROP_GUIDANDPATH = 12, DIPROP_VIDPID = 24,
       DIPH_DEVICE = 0, ALLOC = 700, SENTINEL = 0x5Au, MAX_CB = 4 };

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { fprintf(stderr, "FAIL %s\n", what); fails++; }
}
static uint32_t slot(uint32_t iface, unsigned i) { return MEM32(MEM32(iface) + 4u * i); }
static uint32_t guest_bytes(const uint8_t *p, uint32_t n) {
    uint32_t va = hle_va(hle_alloc(n));
    memcpy(hle_ptr(va), p, n);
    return va;
}
static uint32_t u32(void) { return hle_va(hle_alloc(4)); }

/* Call a shim through a synthetic guest stack; hle_call_guest saves/restores
 * g_esp and would hide a wrong callee purge. */
static uint32_t invoke(uint32_t fn, int argc, const uint32_t *args, const char *what) {
    recomp_func_t f = fn ? recomp_lookup_import(fn) : 0;
    if (!f) { ok(what, 0); return 0xFFFFFFFFu; }
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t stack = hle_va(hle_alloc(64));
    g_esp = stack + 32;
    for (int i = argc - 1; i >= 0; --i) { g_esp -= 4; MEM32(g_esp) = args[i]; }
    g_esp -= 4; MEM32(g_esp) = RECOMP_RETADDR;
    uint32_t esp_in = g_esp;
    f();
    uint32_t words = (g_esp - esp_in) / 4u;
    uint32_t r = g_eax;
    g_esp = saved_esp; g_eax = saved_eax;
    ok(what, words == (uint32_t)argc + 1u);
    return r;
}

/* Enumeration callback context lives in guest memory: count, sizes, devtypes
 * and the first 16 name bytes of each instance. Exactly two stdcall args. */
enum { CB_COUNT = 0, CB_SIZES = 4, CB_TYPES = 4 + 4 * MAX_CB,
       CB_NAMES = 4 + 8 * MAX_CB, CB_STRIDE = 16 };
static void cb_record(void) {
    uint32_t inst = A32(0), ctx = A32(1);
    if (ctx) {
        uint32_t n = MEM32(ctx);
        if (n < MAX_CB) {
            MEM32(ctx + CB_SIZES + 4u * n) = MEM32(inst);
            MEM32(ctx + CB_TYPES + 4u * n) = MEM32(inst + DI8_DEVTYPE);
            for (unsigned k = 0; k < CB_STRIDE - 1; k++)
                MEM8(ctx + CB_NAMES + CB_STRIDE * n + k) = MEM8(inst + DI8_NAME + k);
            MEM8(ctx + CB_NAMES + CB_STRIDE * n + CB_STRIDE - 1u) = 0;
            MEM32(ctx) = n + 1;
        }
    }
    RET(DIENUM_CONTINUE, 2);
}
static const win32hle_shim cb_shims[] = {
    { "DiJoyTest::Record", cb_record },
    { 0, 0 }
};

static const uint8_t guid_kbd[16] = {
    0x61,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,0xBF,0xC7,0x44,0x45,0x53,0x54,0,0 };
static const uint8_t guid_mouse[16] = {
    0x60,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,0xBF,0xC7,0x44,0x45,0x53,0x54,0,0 };
static const uint8_t guid_joy[16] = {
    0x70,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,0xBF,0xC7,0x44,0x45,0x53,0x54,0,0 };
static const uint8_t guid_fake[16] = {
    0xAB,0xAB,0xAB,0xAB,0xAB,0xAB,0xAB,0xAB,0xAB,0xAB,0xAB,0xAB,0xAB,0xAB,0xAB,0xAB };
static const uint8_t iid_di8_a[16] = {
    0x30,0x80,0x79,0xBF,0x3A,0x48,0xA2,0x4D,0xAA,0x99,0x5D,0x64,0xED,0x36,0x97,0x00 };
static const uint8_t iid_di8_w[16] = {
    0x31,0x80,0x79,0xBF,0x3A,0x48,0xA2,0x4D,0xAA,0x99,0x5D,0x64,0xED,0x36,0x97,0x00 };

static uint32_t create_legacy(uint32_t out, const char *what) {
    return invoke(hle_resolve("DirectInputCreateA"), 4,
                  (uint32_t[]){ 0x1234u, 0x0700u, out, 0 }, what);
}
static uint32_t create8(uint32_t iid, uint32_t out, const char *what) {
    return invoke(hle_resolve("DirectInput8Create"), 5,
                  (uint32_t[]){ 0x1234u, 0x0800u, iid, out, 0 }, what);
}
static uint32_t create_device(uint32_t di, uint32_t guid, uint32_t out, const char *what) {
    return invoke(slot(di, 3), 4, (uint32_t[]){ di, guid, out, 0 }, what);
}
static uint32_t enumerate(uint32_t di, uint32_t fn_slot, uint32_t type,
                          uint32_t cb, uint32_t ctx, uint32_t flags, const char *what) {
    return invoke(slot(di, fn_slot), 5,
                  (uint32_t[]){ di, type, cb, ctx, flags }, what);
}
/* Fill a guest block with a sentinel; bytes at/after `from` must stay that way
 * if the shim honours the guest's declared size. */
static uint32_t guarded(uint32_t n) {
    uint32_t va = hle_va(hle_alloc(n));
    memset(hle_ptr(va), SENTINEL, n);
    return va;
}
static int untouched_from(uint32_t va, uint32_t from, uint32_t n) {
    const uint8_t *p = hle_ptr(va);
    for (uint32_t k = from; k < n; k++) if (p[k] != SENTINEL) return 0;
    return 1;
}
static int all_slots(uint32_t iface, unsigned n) {
    for (unsigned i = 0; i < n; i++) if (!slot(iface, i)) return 0;
    return 1;
}

int main(void) {
    win32hle_register(win32hle_dinput);
    win32hle_register(cb_shims);
    mach_enter();

    /* Force a headless no-device host; this override never adds devices. */
    setenv("RECOMP_DI_NO_JOYSTICK", "1", 1);
    uint32_t g_kbd = guest_bytes(guid_kbd, 16), g_mouse = guest_bytes(guid_mouse, 16);
    uint32_t g_joy = guest_bytes(guid_joy, 16), g_fake = guest_bytes(guid_fake, 16);
    uint32_t iid = guest_bytes(iid_di8_a, 16), cb = hle_resolve("DiJoyTest::Record");
    uint32_t out = u32();
    ok("callback resolves", cb != 0);

    uint32_t di7_out = u32(), di8_out = u32();
    ok("legacy DI created", create_legacy(di7_out, "DirectInputCreateA pops four args") == DI_OK_VALUE);
    ok("DI8 created", create8(iid, di8_out, "DirectInput8Create pops five args") == DI_OK_VALUE);
    uint32_t di7 = MEM32(di7_out), di8 = MEM32(di8_out);
    if (!di7 || !di8 || !cb) { mach_leave(); return 1; }

    /* ---- no-device enumeration ---- */
    uint32_t ctx = hle_va(hle_alloc(128));
    const uint32_t filters[][2] = {
        { DIDEVTYPE_JOYSTICK, 0 }, { DI8DEVTYPE_JOYSTICK, 0 },
        { DI8DEVCLASS_GAMECTRL, 0 }
    };
    for (unsigned i = 0; i < sizeof filters / sizeof filters[0]; i++) {
        memset(hle_ptr(ctx), 0, 128);
        ok("legacy joystick filter -> DI_OK, zero callbacks",
           enumerate(di7, 4, filters[i][0], cb, ctx, filters[i][1],
                     "legacy EnumDevices pops five args") == DI_OK_VALUE && MEM32(ctx) == 0);
    }
    memset(hle_ptr(ctx), 0, 128);
    ok("DI8 GAMECTRL class -> zero callbacks",
       enumerate(di8, 4, DI8DEVCLASS_GAMECTRL, cb, ctx, 0,
                 "DI8 EnumDevices pops five args") == DI_OK_VALUE && MEM32(ctx) == 0);
    memset(hle_ptr(ctx), 0, 128);
    ok("DI8 joystick type -> zero callbacks",
       enumerate(di8, 4, DI8DEVTYPE_JOYSTICK, cb, ctx, 0,
                 "DI8 EnumDevices(joystick type)") == DI_OK_VALUE && MEM32(ctx) == 0);
    memset(hle_ptr(ctx), 0, 128);
    ok("no fake joystick in ALL enumeration",
       enumerate(di8, 4, DI8DEVCLASS_ALL, cb, ctx, 0,
                 "DI8 EnumDevices(ALL)") == DI_OK_VALUE && MEM32(ctx) == 2 &&
       MEM32(ctx + CB_TYPES) == 0x0212u && MEM32(ctx + CB_TYPES + 4u) == 0x0413u &&
       MEM32(ctx + CB_SIZES) == DI8_SIZE && MEM32(ctx + CB_SIZES + 4u) == DI8_SIZE);
    memset(hle_ptr(ctx), 0, 128);
    ok("force-feedback-only filter -> zero callbacks",
       enumerate(di7, 4, 0, cb, ctx, DIEDFL_FORCEFEEDBACK,
                 "legacy EnumDevices(FF)") == DI_OK_VALUE && MEM32(ctx) == 0);

    /* ---- documented DIERR codes instead of a fabricated device ---- */
    MEM32(out) = 0xDEADBEEFu;
    ok("legacy CreateDevice(joystick) -> DIERR_DEVICENOTREG, out cleared",
       create_device(di7, g_joy, out, "legacy CreateDevice pops four args") == DIERR_DEVICENOTREG &&
       MEM32(out) == 0);
    MEM32(out) = 0xDEADBEEFu;
    ok("DI8 CreateDevice(joystick) -> DIERR_DEVICENOTREG, out cleared",
       create_device(di8, g_joy, out, "DI8 CreateDevice pops four args") == DIERR_DEVICENOTREG &&
       MEM32(out) == 0);
    MEM32(out) = 0xDEADBEEFu;
    ok("unknown GUID -> DIERR_DEVICENOTREG (no invented device)",
       create_device(di8, g_fake, out, "CreateDevice(unknown)") == DIERR_DEVICENOTREG && MEM32(out) == 0);
    ok("legacy GetDeviceStatus(joystick) -> DIERR_DEVICENOTREG",
       invoke(slot(di7, 5), 2, (uint32_t[]){ di7, g_joy },
              "legacy GetDeviceStatus pops two args") == DIERR_DEVICENOTREG);
    ok("DI8 GetDeviceStatus(joystick) -> DI_NOTATTACHED",
       invoke(slot(di8, 5), 2, (uint32_t[]){ di8, g_joy },
              "DI8 GetDeviceStatus pops two args") == DI_NOTATTACHED_VALUE);
    MEM32(out) = 0xDEADBEEFu;
    ok("DI8 FindDevice(joystick) -> DIERR_DEVICENOTREG, out cleared",
       invoke(slot(di8, 8), 4, (uint32_t[]){ di8, g_joy, 0, out },
              "FindDevice pops four args") == DIERR_DEVICENOTREG && MEM32(out) == 0);

    /* ---- bounded writes on the devices that really exist ---- */
    uint32_t mout = u32(), kout = u32();
    ok("mouse device created", create_device(di8, g_mouse, mout,
       "CreateDevice(mouse)") == DI_OK_VALUE && MEM32(mout));
    ok("keyboard device created", create_device(di7, g_kbd, kout,
       "CreateDevice(keyboard)") == DI_OK_VALUE && MEM32(kout));
    uint32_t mouse = MEM32(mout), kbd = MEM32(kout);
    if (!mouse || !kbd) { mach_leave(); return 1; }
    uint32_t di8w_out = u32(), wdev_out = u32();
    ok("DI8 W object created", create8(guest_bytes(iid_di8_w, 16), di8w_out,
       "DirectInput8Create(W) pops five args") == DI_OK_VALUE && MEM32(di8w_out));
    ok("W keyboard device created", create_device(MEM32(di8w_out), g_kbd, wdev_out,
       "W CreateDevice pops four args") == DI_OK_VALUE && MEM32(wdev_out));
    uint32_t di8w = MEM32(di8w_out), wdev = MEM32(wdev_out);

    /* GetDeviceInfo: a short dwSize must not be filled past its end; a full
     * struct must carry the A name and the real device GUID. */
    uint32_t info = guarded(ALLOC);
    MEM32(info) = 8;
    ok("GetDeviceInfo dwSize=8 bounded",
       invoke(slot(mouse, 15), 2, (uint32_t[]){ mouse, info },
              "GetDeviceInfo(short) pops two args") == DI_OK_VALUE &&
       untouched_from(info, 8, ALLOC));
    MEM32(info) = 0x5A5A5A5Au;
    memset(hle_ptr(info), SENTINEL, ALLOC);
    MEM32(info) = DI8_SIZE;
    ok("GetDeviceInfo full struct bounded",
       invoke(slot(mouse, 15), 2, (uint32_t[]){ mouse, info },
              "GetDeviceInfo(full)") == DI_OK_VALUE &&
       untouched_from(info, DI8_SIZE, ALLOC) &&
       !memcmp(hle_ptr(info + 4), guid_mouse, 16) &&
       !strcmp((char *)hle_ptr(info + DI8_NAME), "Mouse") &&
       MEM32(info + DI8_DEVTYPE) == 0x0212u);

    /* The W layout has 260-WCHAR names: 1100 bytes total and the second name
     * at +560, not +300. A guest asking for that size sees both names and no
     * byte past the struct changes. */
    if (wdev) {
        uint32_t winfo = guarded(1200);
        MEM32(winfo) = 1100;
        ok("W GetDeviceInfo writes UTF-16 names, bounded",
           invoke(slot(wdev, 15), 2, (uint32_t[]){ wdev, winfo },
                  "W GetDeviceInfo pops two args") == DI_OK_VALUE &&
           untouched_from(winfo, 1100, 1200) &&
           MEM16(winfo + DI8_NAME) == 'K' && MEM16(winfo + 56) == 0 &&
           MEM16(winfo + 560) == 'K' && MEM16(winfo + 576) == 0);
    }

    /* GetCapabilities: DIDC_FORCEFEEDBACK is never set, and a short struct is
     * not written past. */
    uint32_t caps = guarded(64);
    MEM32(caps) = 8;
    ok("GetCapabilities dwSize=8 bounded",
       invoke(slot(mouse, 3), 2, (uint32_t[]){ mouse, caps },
              "GetCapabilities(short) pops two args") == DI_OK_VALUE &&
       untouched_from(caps, 8, 64));
    memset(hle_ptr(caps), SENTINEL, 64);
    MEM32(caps) = 44;
    ok("GetCapabilities full struct bounded, no FF flag",
       invoke(slot(mouse, 3), 2, (uint32_t[]){ mouse, caps },
              "GetCapabilities(full)") == DI_OK_VALUE &&
       untouched_from(caps, 44, 64) &&
       (MEM32(caps + 4) & DIDC_FORCEFEEDBACK) == 0);

    /* GetDeviceState: the seat-belt is the size passed by the guest, even when
     * it is larger than DIMOUSESTATE2 or as small as one byte. */
    uint32_t buf = guarded(64);
    ok("GetDeviceState before Acquire -> DIERR_NOTACQUIRED",
       invoke(slot(mouse, 9), 3, (uint32_t[]){ mouse, 20, buf },
              "GetDeviceState(unacquired) pops three args") == DIERR_NOTACQUIRED);
    ok("Acquire", invoke(slot(mouse, 7), 1, &mouse, "Acquire pops one arg") == DI_OK_VALUE);
    memset(hle_ptr(buf), SENTINEL, 64);
    ok("GetDeviceState size=2 bounded",
       invoke(slot(mouse, 9), 3, (uint32_t[]){ mouse, 2, buf },
              "GetDeviceState(2)") == DI_OK_VALUE && untouched_from(buf, 2, 64));
    memset(hle_ptr(buf), SENTINEL, 64);
    ok("GetDeviceState size=20 bounded",
       invoke(slot(mouse, 9), 3, (uint32_t[]){ mouse, 20, buf },
              "GetDeviceState(20)") == DI_OK_VALUE && untouched_from(buf, 20, 64));
    memset(hle_ptr(buf), SENTINEL, 64);
    ok("GetDeviceState size=64 clamps to 20",
       invoke(slot(mouse, 9), 3, (uint32_t[]){ mouse, 64, buf },
              "GetDeviceState(64)") == DI_OK_VALUE && untouched_from(buf, 20, 64));

    /* Joystick-only properties stay unsupported on a real device: no VID/PID,
     * HID path or joystick id is invented, and the header is untouched. */
    uint32_t prop = guarded(ALLOC);
    MEM32(prop) = DIPROPDWORD_SIZE;
    MEM32(prop + 4) = DIPROPHEADER_SIZE;
    MEM32(prop + 8) = 0;
    MEM32(prop + 12) = DIPH_DEVICE;
    ok("JOYSTICKID -> DIERR_UNSUPPORTED, no write",
       invoke(slot(mouse, 5), 3, (uint32_t[]){ mouse, DIPROP_JOYSTICKID, prop },
              "GetProperty(JOYSTICKID) pops three args") == DIERR_UNSUPPORTED &&
       untouched_from(prop, DIPROPHEADER_SIZE, DIPROPDWORD_SIZE));
    ok("GUIDANDPATH -> DIERR_UNSUPPORTED, no write",
       invoke(slot(mouse, 5), 3, (uint32_t[]){ mouse, DIPROP_GUIDANDPATH, prop },
              "GetProperty(GUIDANDPATH)") == DIERR_UNSUPPORTED &&
       untouched_from(prop, DIPROPHEADER_SIZE, DIPROPDWORD_SIZE));
    ok("VIDPID -> DIERR_UNSUPPORTED, no write",
       invoke(slot(mouse, 5), 3, (uint32_t[]){ mouse, DIPROP_VIDPID, prop },
              "GetProperty(VIDPID)") == DIERR_UNSUPPORTED &&
       untouched_from(prop, DIPROPHEADER_SIZE, DIPROPDWORD_SIZE));

    /* ---- with the override off, agree with SDL instead of guessing ---- */
    unsetenv("RECOMP_DI_NO_JOYSTICK");
    if (!SDL_WasInit(SDL_INIT_JOYSTICK)) SDL_InitSubSystem(SDL_INIT_JOYSTICK);
    int host_joy = SDL_NumJoysticks();
    if (host_joy < 0) host_joy = 0;
    if (host_joy > 8) host_joy = 8;
    memset(hle_ptr(ctx), 0, 128);
    ok("real enumeration matches SDL joystick count",
       enumerate(di8, 4, DI8DEVTYPE_JOYSTICK, cb, ctx, 0,
                 "DI8 EnumDevices(real joystick count)") == DI_OK_VALUE &&
       (int)MEM32(ctx) == host_joy);
    MEM32(out) = 0xDEADBEEFu;
    uint32_t real_hr = create_device(di8, g_joy, out, "CreateDevice(real joystick path)");
    ok("CreateDevice(joystick) agrees with SDL enumeration",
       host_joy ? real_hr == DI_OK_VALUE && MEM32(out) != 0
                : real_hr == DIERR_DEVICENOTREG && MEM32(out) == 0);
    if (host_joy && MEM32(out)) {
        uint32_t joydev = MEM32(out);
        ok("joystick device vtable has 32 non-null slots", all_slots(joydev, 32));
        ok("joystick Release", invoke(slot(joydev, 2), 1, &joydev,
           "joystick Release pops one arg") == 0);
    }
    printf("dinput_joystick_selftest: SDL reports %d joystick(s); hardware path %s\n",
           host_joy, host_joy ? "exercised" : "NOT TESTED (no device on this host)");

    ok("mouse Release", invoke(slot(mouse, 2), 1, &mouse, "mouse Release") == 0);
    ok("keyboard Release", invoke(slot(kbd, 2), 1, &kbd, "keyboard Release") == 0);
    if (wdev) ok("W device Release", invoke(slot(wdev, 2), 1, &wdev,
                 "W device Release") == 0);
    if (di8w) ok("DI8 W Release", invoke(slot(di8w, 2), 1, &di8w,
                 "DI8 W Release") == 0);
    ok("DI8 Release", invoke(slot(di8, 2), 1, &di8, "DI8 Release") == 0);
    ok("legacy DI Release", invoke(slot(di7, 2), 1, &di7, "legacy DI Release") == 0);

    mach_leave();
    printf("dinput_joystick_selftest: %s\n", fails ? "FAILED" : "all checks passed");
    return fails != 0;
}
