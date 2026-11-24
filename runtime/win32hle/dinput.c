/*
 * dinput.c - DirectInput 3/5/7 and DirectInput 8 for keyboard and mouse, on
 * SDL's input state.
 *
 * Three COM facades, the ones a DirectInput game talks to:
 *
 *   IDirectInputA        DirectInputCreateA(hinst, 0x0300, &di, NULL)
 *   IDirectInput7A       DirectInputCreateEx(hinst, 0x0700, &di, iid, NULL)
 *   IDirectInput8A/W     DirectInput8Create(hinst, 0x0800, iid, &di, NULL)
 *     CreateDevice(guid, &device, NULL)
 *
 *   IDirectInputDeviceA/7A/8A
 *     SetDataFormat, SetCooperativeLevel, Acquire, Unacquire, GetDeviceState,
 *     GetDeviceData, GetCapabilities, EnumObjects, Get/SetProperty,
 *     GetObjectInfo, GetDeviceInfo, Initialize
 *
 * DirectInput8 is a thin ABI front-end, not a second input stack: it has its
 * own 11-slot IDirectInput8 and 32-slot IDirectInputDevice8 vtables (the
 * layout a guest indexes by byte offset) but shares every device, every SDL
 * sample and the input record/replay path with the DX3/5/7 facades. A and W
 * are distinguished by the IID passed to DirectInput8Create and only change
 * the encoding of the names in DIDEVICEINSTANCE; the slot layout is the same.
 *
 * What is real: the keyboard's 256-byte key state and the mouse's three axes
 * and buttons, read from SDL's live input state at GetDeviceState time, so a
 * game polling a device sees what the player is actually pressing. The
 * data-format structure the game passes in is kept and its dwDataSize honoured
 * for the buffer length, so a game that uses only the first 32 key bytes and
 * one that uses all 256 both get a correctly sized answer.
 *
 * What is absent, and named as such rather than faked:
 *   - Force feedback, effects, and device properties beyond the read-only ones.
 *   - Joystick devices. DirectInput's joystick path needs the DIDATAFORMAT the
 *     game passes plus per-axis ranges it then reads back through properties;
 *     answering it with a device that never reports an axis would make a game
 *     believe a stick is centred and connected. CreateDevice for a joystick
 *     returns DIERR_DEVICENOTREG so the game takes its no-joystick path, which
 *     is the truth on a host with no stick enumerated.
 *   - Action mapping (BuildActionMap/SetActionMap/GetImageInfo,
 *     EnumDevicesBySemantics, ConfigureDevices) and a control panel: the DI8
 *     slots exist and return DIERR_UNSUPPORTED, never a fake success or a
 *     null function pointer a guest could jump through.
 *   - EnumDevices reports exactly the keyboard and mouse above.
 *
 * These are stdcall exports (DINPUT.dll is a WINAPI DLL), so every shim ends in
 * RET(value, argc) with arg 0 = `this` for COM methods.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

/* input_rec.c: record the device state this shim returns, and replay it. */
int  hle_input_recording(void);
int  hle_input_replaying(void);
void hle_input_record(const char *what, const void *data, size_t n);
int  hle_input_replay(const char *what, void *data, size_t n);

/* HRESULTs. */
#define DI_OK                 0x00000000u
#define DI_NOTATTACHED        0x00000001u  /* S_FALSE, Wine dinput.h */
#define DIERR_INVALIDPARAM    0x80070057u
#define DIERR_NOTINITIALIZED  0x80070015u
#define DIERR_NOTACQUIRED     0x8007000Cu  /* ERROR_INVALID_ACCESS, Wine dinput.h */
#define DIERR_DEVICENOTREG    0x80040154u
#define DIERR_UNSUPPORTED     0x80004001u  /* E_NOTIMPL, Wine dinput.h */
#define E_NOINTERFACE         0x80004002u
#define DIERR_NOINTERFACE     E_NOINTERFACE  /* Wine aliases DIERR_ to E_NOINTERFACE */
#define E_POINTER             0x80004003u
#define E_OUTOFMEMORY         0x8007000Eu
/* DirectInput8Create's version gate (wine-10.0 dinput.h): ERROR_RMODE_APP and
 * ERROR_OLD_WIN_VERSION in FACILITY_WIN32. */
#define DIERR_BETADIRECTINPUTVERSION 0x80070481u
#define DIERR_OLDDIRECTINPUTVERSION  0x8007047Eu

#define DIENUM_STOP 0u
#define DIENUM_CONTINUE 1u

/* Legacy DIDEVTYPE_* codes (wine-10.0 include/dinput.h). IDirectInput (DX3/5/7)
 * callers filter on these, and DIDEVICEINSTANCE_DX3.dwDevType carries one of
 * them: 0x12/0x13 are the *DI8* type codes and must not be used here. */
#define DIDEVTYPE_DEVICE   1u
#define DIDEVTYPE_MOUSE    2u
#define DIDEVTYPE_KEYBOARD 3u
#define DIDEVTYPE_JOYSTICK 4u
#define DIDEVTYPEMOUSE_TRADITIONAL   2u
#define DIDEVTYPEKEYBOARD_PCENH      4u
/* DIDEVICEINSTANCE_DX3A/W: dwSize, two GUIDs, dwDevType and two MAX_PATH
 * names. 4+16+16+4+260+260 = 560 bytes on x86; the A and W layouts match. */
enum { DIDEVICEINSTANCE_DX3_SIZE = 560, DIDEVICEINSTANCE_DX3_DEVTYPE = 36,
       DIDEVICEINSTANCE_DX3_NAME = 40, DIDEVICEINSTANCE_DX3_PRODUCT = 300 };

/* DI8 device classes/type codes used by IDirectInput8::EnumDevices and the
 * DIDEVICEINSTANCE.dwDevType it hands the callback. Values from wine-10.0's
 * include/dinput.h, not the legacy DI7 DIDEVTYPE_* numbers. */
#define DI8DEVCLASS_ALL       0u
#define DI8DEVCLASS_POINTER   2u
#define DI8DEVCLASS_KEYBOARD  3u
#define DI8DEVCLASS_GAMECTRL  4u
#define DI8DEVTYPE_DEVICE     0x11u
#define DI8DEVTYPE_MOUSE      0x12u
#define DI8DEVTYPE_KEYBOARD   0x13u
#define DI8DEVTYPE_SUPPLEMENTAL 0x1Cu
#define DI8DEVTYPEMOUSE_TRADITIONAL 2u
#define DI8DEVTYPEKEYBOARD_PCENH    4u
/* DIEDFL_*: which enumeration flags are legal. */
#define DIEDFL_ATTACHEDONLY   0x00000001u
#define DIEDFL_FORCEFEEDBACK  0x00000100u
#define DIEDFL_INCLUDEALIASES 0x00010000u
#define DIEDFL_INCLUDEPHANTOMS 0x00020000u
#define DIEDFL_INCLUDEHIDDEN  0x00040000u
#define DIEDFL_VALID (DIEDFL_ATTACHEDONLY | DIEDFL_FORCEFEEDBACK | \
                      DIEDFL_INCLUDEALIASES | DIEDFL_INCLUDEPHANTOMS | \
                      DIEDFL_INCLUDEHIDDEN)
/* DIDEVICEINSTANCEA/W: dwSize + 2 GUIDs + dwDevType + 2 x MAX_PATH names +
 * GUID + 2 WORDs. The A and W layouts are the same 580 bytes on x86. */
enum { DIDEVICEINSTANCE_SIZE = 580, DIDEVICEINSTANCE_DEVTYPE = 36,
       DIDEVICEINSTANCE_NAME = 40, DIDEVICEINSTANCE_PRODUCT = 300 };

/* Bit a device reports in GetDeviceData's dwOfs / the DIDEVICEOBJECTDATA stream.
 * The keyboard is 256 bytes of DIK_* codes; the mouse is 3 axes then 4
 * buttons. */
enum { DIK_SIZE = 256, DIMOFS_X = 0, DIMOFS_Y = 4, DIMOFS_Z = 8,
       DIMOFS_BUTTON0 = 12 };

/* GUIDs as the guest passes them: 16 bytes, little-endian first three fields.
 * Only the ones a game asks for matter here. */
static const uint8_t GUID_SYS_KEYBOARD[16] = {
    0x61, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
    0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00 };
static const uint8_t GUID_SYS_MOUSE[16] = {
    0x60, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
    0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00 };

/* IIDs DirectInput8Create accepts, as the guest passes them (GUID byte order).
 * IID_IDirectInput8A = BF798030-483A-4DA2-AA99-5D64ED369700,
 * IID_IDirectInput8W = BF798031-... and IID_IUnknown = 00000000-...-C0...46.
 * Every DX3/5/7 IID and CLSID_DirectInput8 is deliberately NOT in this list:
 * Wine's DirectInput8Create answers DIERR_NOINTERFACE for those. */
static const uint8_t IID_DI8_A[16] = {
    0x30, 0x80, 0x79, 0xBF, 0x3A, 0x48, 0xA2, 0x4D,
    0xAA, 0x99, 0x5D, 0x64, 0xED, 0x36, 0x97, 0x00 };
static const uint8_t IID_DI8_W[16] = {
    0x31, 0x80, 0x79, 0xBF, 0x3A, 0x48, 0xA2, 0x4D,
    0xAA, 0x99, 0x5D, 0x64, 0xED, 0x36, 0x97, 0x00 };
static const uint8_t IID_UNKNOWN[16] = {
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46 };

/* Device state. Slots are guest objects; the host state lives beside them. */
enum { DIN_MAX_DEVICES = 8 };
typedef struct {
    uint32_t obj;             /* guest IDirectInputDevice */
    int kind;                 /* 1 = keyboard, 2 = mouse */
    int acquired;
    uint32_t data_size;       /* the DIDATAFORMAT's dwDataSize the game passed */
    int have_format;
    uint32_t hwnd;
    uint32_t coop;            /* the DISCL_* flags the game set */
    int32_t last_x, last_y;   /* previous absolute sample, for exclusive relative axes */
    int have_last;
} din_device;

static din_device g_dev[DIN_MAX_DEVICES];
static int g_ndev;
static uint32_t g_vt_di, g_vt_dev;    /* built on first use, DX3/5/7 layout */
static uint32_t g_vt_di8, g_vt_dev8;  /* DI8 layouts: 11 and 32 slots */
/* One IDirectInput object per A/W string encoding, as on Windows: the encoding
 * a caller selected must not change because a later Create asked for the other
 * one. +8 in the object is the A/W flag read by the legacy EnumDevices. */
enum { DI_HOST_UNICODE = 8 };
static uint32_t g_di[2];              /* [0] = ANSI, [1] = wide */
static uint32_t g_di_refs;

/* SDL scancode -> DIK_* code. The DIK set is the PC set-1 make codes, and SDL's
 * scancodes are also set-1-derived, so the identity holds except where SDL
 * renames a key; the handful Windows reports differently are mapped below. */
static int dik_from_scancode(SDL_Scancode sc) {
    int c = (int)sc;
    switch (sc) {
    case SDL_SCANCODE_ESCAPE:        return 0x01;
    case SDL_SCANCODE_RETURN:        return 0x1C;
    case SDL_SCANCODE_BACKSPACE:     return 0x0E;
    case SDL_SCANCODE_TAB:           return 0x0F;
    case SDL_SCANCODE_LSHIFT:        return 0x2A;
    case SDL_SCANCODE_RSHIFT:        return 0x36;
    case SDL_SCANCODE_LCTRL:         return 0x1D;
    case SDL_SCANCODE_RCTRL:         return 0x9D;
    case SDL_SCANCODE_LALT:          return 0x38;
    case SDL_SCANCODE_RALT:          return 0xB8;
    case SDL_SCANCODE_SPACE:         return 0x39;
    case SDL_SCANCODE_CAPSLOCK:      return 0x3A;
    case SDL_SCANCODE_LEFT:          return 0xCB;
    case SDL_SCANCODE_RIGHT:         return 0xCD;
    case SDL_SCANCODE_UP:            return 0xC8;
    case SDL_SCANCODE_DOWN:          return 0xD0;
    case SDL_SCANCODE_INSERT:        return 0xD2;
    case SDL_SCANCODE_DELETE:        return 0xD3;
    case SDL_SCANCODE_HOME:          return 0xC7;
    case SDL_SCANCODE_END:           return 0xCF;
    case SDL_SCANCODE_PAGEUP:        return 0xC9;
    case SDL_SCANCODE_PAGEDOWN:      return 0xD1;
    case SDL_SCANCODE_F1:  return 0x3B;  case SDL_SCANCODE_F2:  return 0x3C;
    case SDL_SCANCODE_F3:  return 0x3D;  case SDL_SCANCODE_F4:  return 0x3E;
    case SDL_SCANCODE_F5:  return 0x3F;  case SDL_SCANCODE_F6:  return 0x40;
    case SDL_SCANCODE_F7:  return 0x41;  case SDL_SCANCODE_F8:  return 0x42;
    case SDL_SCANCODE_F9:  return 0x43;  case SDL_SCANCODE_F10: return 0x44;
    case SDL_SCANCODE_F11: return 0x57;  case SDL_SCANCODE_F12: return 0x58;
    default: break;
    }
    /* Letters and digits already agree with the DIK set. */
    if (sc >= SDL_SCANCODE_A && sc <= SDL_SCANCODE_Z) return c - SDL_SCANCODE_A + 0x1E;
    if (sc >= SDL_SCANCODE_1 && sc <= SDL_SCANCODE_0) {
        static const int d[10] = { 0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0A,0x0B };
        return d[sc - SDL_SCANCODE_1];
    }
    if (sc >= SDL_SCANCODE_KP_1 && sc <= SDL_SCANCODE_KP_0) {
        static const int d[10] = { 0x4F,0x50,0x51,0x4B,0x4C,0x4D,0x47,0x48,0x49,0x52 };
        return d[sc - SDL_SCANCODE_KP_1];
    }
    return -1;
}

static int dev_index(uint32_t obj) {
    for (int i = 0; i < g_ndev; i++) if (g_dev[i].obj == obj) return i;
    return -1;
}

/* One device factory for every facade. `vt` selects the interface layout the
 * guest will index (DX3/5/7's 29-slot device or DI8's 32-slot one); the state
 * and every method behind it are the same SDL-backed implementation. All the
 * host-side fields are cleared here so a freed slot reused by a later device
 * cannot inherit a previous device's window/handedness/relative-axis origin. */
static uint32_t make_device(uint32_t guid, uint32_t vt, uint32_t out) {
    if (!guid || !out) return DIERR_INVALIDPARAM;
    MEM32(out) = 0;
    if (g_ndev >= DIN_MAX_DEVICES) return DIERR_DEVICENOTREG;
    const uint8_t *g = hle_ptr(guid);
    int kind = memcmp(g, GUID_SYS_KEYBOARD, 16) == 0 ? 1
             : memcmp(g, GUID_SYS_MOUSE, 16) == 0 ? 2 : 0;
    if (!kind) {
        /* A joystick, a wheel, a flight stick: this host enumerates none, and a
         * device that never reports an axis would tell the game a stick is
         * connected and centred. DIERR_DEVICENOTREG is the true answer and the
         * one a game handles by taking its no-joystick path. */
        return DIERR_DEVICENOTREG;
    }
    uint32_t obj = hle_com_new(vt, 16);
    if (!obj) return DIERR_DEVICENOTREG;
    din_device *d = &g_dev[g_ndev++];
    memset(d, 0, sizeof *d);
    d->obj = obj;
    d->kind = kind;
    MEM32(out) = obj;
    return DI_OK;
}

/* ---- IDirectInputDevice ---- */
static void dev_Release(void);
static void dev_QueryInterface(void) {
    /* (this, iid, &out): the only interfaces are ours, so asking for one of
     * them returns this object again -- with the AddRef the COM contract
     * requires. Without it the caller's Release frees an object it is still
     * using: Cave Story's Doukutsu does exactly QueryInterface-then-Release on
     * its keyboard device, and freeing it there crashed the game at startup. */
    uint32_t self = A32(0), out = A32(2);
    if (!out) RET(E_POINTER, 3);
    MEM32(out) = self;
    MEM32(self + 4)++;
    RET(DI_OK, 3);
}
static void dev_GetCapabilities(void) {
    /* DIDEVICEOBJECTINSTANCE/DIDEVCAPS is filled from what the device really
     * is: a keyboard sends 256 keys, a mouse 3 axes and 4 buttons. */
    uint32_t caps = A32(1);
    if (!caps) RET(E_POINTER, 2);
    int i = dev_index(A32(0));
    memset(hle_ptr(caps), 0, MEM32(caps + 0) >= 44 ? 44 : 44);
    MEM32(caps + 0) = 44;                                 /* dwSize */
    MEM32(caps + 4) = 0x00000001;                         /* dwFlags: DIDC_ATTACHED */
    if (i >= 0 && g_dev[i].kind == 2) MEM32(caps + 20) = 4;   /* dwButtons */
    RET(DI_OK, 2);
}
static void dev_EnumObjects(void) {
    /* A callback that is never called reports no objects. That is true for the
     * keyboard (a game reads its 256-byte array, not the object list) and for
     * the mouse; a game that enumerates to discover the layout falls back to
     * the data format it passed, which is what actually defines the buffer. */
    RET(DI_OK, 4);
}
static void dev_SetDataFormat(void) {
    /* (this, DIDATAFORMAT*): keep dwDataSize (offset 0) so GetDeviceState can
     * bound the copy by the size the game expects. */
    int i = dev_index(A32(0));
    uint32_t fmt = A32(1);
    if (i < 0 || !fmt) RET(DIERR_INVALIDPARAM, 2);
    g_dev[i].data_size = MEM32(fmt + 0);
    g_dev[i].have_format = 1;
    RET(DI_OK, 2);
}
static void dev_SetCooperativeLevel(void) {
    int i = dev_index(A32(0));
    if (i < 0) RET(DIERR_INVALIDPARAM, 3);
    g_dev[i].hwnd = A32(1);
    g_dev[i].coop = A32(2);
    g_dev[i].have_last = 0;
    if (getenv("RECOMP_DI_DIAG")) fprintf(stderr, "[di] SetCooperativeLevel kind=%d flags=%08x\n", g_dev[i].kind, A32(2));
    RET(DI_OK, 3);                                        /* the level is a windowing wish; input is direct */
}
static void dev_Acquire(void) {
    int i = dev_index(A32(0));
    if (i < 0) RET(DIERR_INVALIDPARAM, 1);
    if (!SDL_WasInit(SDL_INIT_VIDEO) && SDL_InitSubSystem(SDL_INIT_EVENTS | SDL_INIT_VIDEO) != 0)
        RET(DIERR_NOTINITIALIZED, 1);
    SDL_PumpEvents();
    g_dev[i].acquired = 1;
    g_dev[i].have_last = 0;
    RET(DI_OK, 1);
}
static void dev_Unacquire(void) {
    int i = dev_index(A32(0));
    if (i >= 0) g_dev[i].acquired = 0;
    RET(DI_OK, 1);
}
static void dev_GetDeviceState(void) {
    int i = dev_index(A32(0));
    uint32_t size = A32(1), buf = A32(2);
    if (i < 0 || !buf) RET(DIERR_INVALIDPARAM, 3);
    if (!g_dev[i].acquired) RET(DIERR_NOTACQUIRED, 3);
    int want = (int)size;
    if (want < 0) want = 0;
    if (g_dev[i].kind == 1) {
        if (want > DIK_SIZE) want = DIK_SIZE;
    } else if (want > 20) {
        want = 20;                       /* DIMOUSESTATE2: three axes and eight buttons */
    }
    /* What the guest is handed, so a replay hands back the same bytes. The
     * recorded value is the shim's answer, not the device's state: at replay
     * time SDL is not consulted at all, which is what makes the transcript
     * device-neutral and lets a recorder on another machine produce the same
     * file. */
    const char *what = g_dev[i].kind == 1 ? "dikeys" : "dimouse";
    if (want > 0 && hle_input_replaying() && hle_input_replay(what, hle_ptr(buf), (size_t)want))
        RET(DI_OK, 3);
    SDL_PumpEvents();
    if (g_dev[i].kind == 1) {
        memset(hle_ptr(buf), 0, (size_t)want);
        const Uint8 *keys = SDL_GetKeyboardState(NULL);
        int n = 0;
        for (int sc = 0; sc < SDL_NUM_SCANCODES; sc++) {
            if (!keys[sc]) continue;
            int dik = dik_from_scancode((SDL_Scancode)sc);
            if (dik > 0 && dik < DIK_SIZE && dik < want) { MEM8(buf + dik) = 0x80; n++; }
        }
        (void)n;
    } else {
        /* The mouse: 3 axes then 4 buttons. A nonexclusive device reports the
         * pointer's absolute position in the game's pixels (the presenter
         * scales the virtual screen into the window); an exclusive one -- what
         * a fullscreen game sets, and what DirectInput documents as relative --
         * reports the movement since the previous poll. Reporting absolute
         * values to an exclusive reader moves its cursor by those values on
         * every poll. Buttons are the async Win32 state either way, so clicks
         * injected through hle_input_mouse count the same as real ones. */
        int mx = 0, my = 0;
        hle_screen_mouse(&mx, &my);
        unsigned buttons = hle_input_mouse_buttons();
        int x = mx, y = my;
        if (g_dev[i].coop & 0x1u) {                       /* DISCL_EXCLUSIVE */
            if (!g_dev[i].have_last) {
                g_dev[i].last_x = mx, g_dev[i].last_y = my, g_dev[i].have_last = 1;
            }
            x = mx - g_dev[i].last_x;
            y = my - g_dev[i].last_y;
            g_dev[i].last_x = mx, g_dev[i].last_y = my;
        }
        uint8_t *p = hle_ptr(buf);
        memset(p, 0, (size_t)(want < 20 ? want : 20));
        if (want >= 4) MEM32(buf + DIMOFS_X) = (uint32_t)x;
        if (want >= 8) MEM32(buf + DIMOFS_Y) = (uint32_t)y;
        if (want >= 12) MEM32(buf + DIMOFS_Z) = 0;
        for (int b = 0; b < 8; b++)
            if (want >= DIMOFS_BUTTON0 + b + 1)
                MEM8(buf + DIMOFS_BUTTON0 + b) = (uint8_t)((buttons >> b) & 1u ? 0x80 : 0);
        if (getenv("RECOMP_DI_DIAG")) { static int n; if ((n++ % 30) == 0 || buttons) fprintf(stderr,"[di] state kind=%d want=%d coop=%u x=%d y=%d buttons=%u t=%u\n",g_dev[i].kind,g_dev[i].coop,want,x,y,buttons,SDL_GetTicks()); }
    }
    if (want > 0 && hle_input_recording()) hle_input_record(what, hle_ptr(buf), (size_t)want);
    RET(DI_OK, 3);
}
static void dev_GetDeviceData(void) {
    /* (this, size, DIDEVICEOBJECTDATA*, &count, flags): no event stream is
     * buffered -- polled state is GetDeviceState, which is real -- so the
     * honest answer is zero events. */
    uint32_t count = A32(3);
    if (getenv("RECOMP_DI_DIAG")) fprintf(stderr, "[di] GetDeviceData kind=%d size=%u count_in=%u flags=%08x\n",
        dev_index(A32(0)) >= 0 ? g_dev[dev_index(A32(0))].kind : -1, A32(1), count ? MEM32(count) : 0, A32(4));
    if (count) MEM32(count) = 0;
    RET(DI_OK, 5);
}
static void dev_GetProperty(void) {
    /* (this, rguidProp, DIPROPHEADER*): only the read-only identity properties
     * are answered, from what the device is. Anything else is unsupported
     * rather than a zeroed struct a game would trust. */
    uint32_t hdr = A32(2);
    if (!hdr) RET(DIERR_INVALIDPARAM, 3);
    uint32_t size = MEM32(hdr + 0), type = MEM32(hdr + 12);
    (void)type;
    uint32_t after = hdr + 16;                            /* past DIPROPHEADER */
    int i = dev_index(A32(0));
    if ((after + 4) <= hdr + size) {
        MEM32(after) = (type == 1 /* DIPROP_RANGE */) ? 0u
                       : (i >= 0 && g_dev[i].kind == 2) ? 1u : 0u;
    }
    RET(DI_OK, 3);
}
static void dev_SetProperty(void) {
    RET(DI_OK, 3);                                        /* ranges and dead-zones are hints; polling ignores them */
}
static void dev_GetObjectInfo(void) {
    RET(DIERR_UNSUPPORTED, 4);                            /* no object list to describe (EnumObjects reports none) */
}
static void dev_GetDeviceInfo(void) {
    /* DIDEVICEINSTANCE: the GUID and the instance name a device really has. */
    uint32_t info = A32(1);
    if (!info) RET(E_POINTER, 2);
    int i = dev_index(A32(0));
    uint32_t size = MEM32(info + 0);
    if (size > 4) memset(hle_ptr(info + 4), 0, (size_t)(size - 4));
    if (i >= 0) {
        const uint8_t *g = g_dev[i].kind == 2 ? GUID_SYS_MOUSE : GUID_SYS_KEYBOARD;
        const char *name = g_dev[i].kind == 2 ? "Mouse" : "Keyboard";
        memcpy(hle_ptr(info + 4), g, 16);
        memcpy(hle_ptr(info + 20), name, strlen(name) + 1);
        strncpy((char *)hle_ptr(info + 20 + 64), name, 63);   /* tszProductName */
    }
    RET(DI_OK, 2);
}
static void dev_Initialize(void) { RET(DIERR_UNSUPPORTED, 4); }
static void dev_RunControlPanel(void) { RET(DIERR_UNSUPPORTED, 3); }
static void dev_Release(void) {
    uint32_t self = A32(0);
    int i = dev_index(self);
    if (i < 0) RET(0, 1);
    /* The object carries its own reference count at +4 (hle_com_new), and a
     * QueryInterface handed out an extra one. Free only at zero, then keep the
     * slots dense: the last device closes the gap. */
    uint32_t n = MEM32(self + 4) ? --MEM32(self + 4) : 0;
    if (n) RET(n, 1);
    hle_com_free(self);
    g_dev[i] = g_dev[--g_ndev];
    RET(0, 1);
}

/* ---- IDirectInputDevice2/7 additions ----
 * A game that asks for IDirectInputDevice7 gets a pointer whose vtable has to
 * have ALL of the interface's slots, because it indexes them by offset and
 * cannot know how long the table is. Cave Story's Doukutsu calls slot 0x64
 * (Poll) once per frame: with a table that stopped at Initialize the read ran
 * past the end into unrelated guest memory and it called whatever it found
 * (VA 0x80), every frame, forever.
 *
 * So the force-feedback half is present and answers what a keyboard and a mouse
 * truthfully are: no effects, no feedback state, nothing to escape to. Windows
 * returns DIERR_UNSUPPORTED for a device with no force feedback, and
 * DIERR_NOTSUPPORTED is what a caller checks for -- it does not make the game
 * think a rumble motor exists. */
static void dev_CreateEffect(void) { RET(DIERR_UNSUPPORTED, 6); }
static void dev_EnumEffects(void) { RET(DI_OK, 4); }          /* enumerates none */
static void dev_GetEffectInfo(void) { RET(DIERR_UNSUPPORTED, 3); }
static void dev_GetForceFeedbackState(void) { RET(DIERR_UNSUPPORTED, 2); }
static void dev_SendForceFeedbackCommand(void) { RET(DIERR_UNSUPPORTED, 2); }
static void dev_EnumCreatedEffectObjects(void) { RET(DI_OK, 4); }   /* there are none */
static void dev_Escape(void) { RET(DIERR_UNSUPPORTED, 3); }   /* no driver-specific escape */
/* Poll re-reads the device. For a keyboard and a mouse there is no hardware
 * buffer to drain, so it is the same state GetDeviceState returns; the call is
 * real, it just has nothing extra to do. */
static void dev_Poll(void) {
    int i = dev_index(A32(0));
    if (i < 0) RET(DIERR_INVALIDPARAM, 1);
    if (!g_dev[i].acquired) RET(DIERR_NOTACQUIRED, 1);
    SDL_PumpEvents();
    RET(DI_OK, 1);
}
static void dev_SendDeviceData(void) { RET(DI_OK, 4); }       /* no output report to send */
static void dev_EnumEffectsInFile(void) { RET(DI_OK, 5); }    /* no effects in the file */
static void dev_WriteEffectToFile(void) { RET(DIERR_UNSUPPORTED, 5); }

/* ---- IDirectInput ---- */
static void di_QueryInterface(void) {
    uint32_t self = A32(0), out = A32(2);
    if (!out) RET(E_POINTER, 3);
    MEM32(out) = self;
    MEM32(self + 4)++;
    g_di_refs++;
    RET(DI_OK, 3);
}
static void di_AddRef(void) { g_di_refs++; RET(++MEM32(A32(0) + 4), 1); }
static void di_Release(void) {
    uint32_t self = A32(0);
    uint32_t n = MEM32(self + 4) ? --MEM32(self + 4) : 0;
    if (n) RET(n, 1);
    if (g_di_refs) g_di_refs--;
    for (int k = 0; k < 2; k++)
        if (g_di[k] == self) { hle_com_free(self); g_di[k] = 0; break; }
    RET(0, 1);
}
static void di_CreateDevice(void) {
    /* (this, rguid, &device, punkOuter) */
    RET(make_device(A32(1), g_vt_dev, A32(2)), 4);
}
static void di_legacy_name(uint32_t inst, uint32_t off, const char *s, int wide) {
    size_t n = strlen(s) + 1;
    if (!wide) {
        memcpy(hle_ptr(inst + off), s, n);
    } else {
        for (size_t i = 0; i < n; i++)
            MEM16(inst + off + 2u * (uint32_t)i) = (uint16_t)(unsigned char)s[i];
    }
}
static void di_EnumDevices(void) {
    /* (this, devType, callback, ctx, flags).
     *
     * The guest callback is LPDIENUMDEVICESCALLBACK: exactly TWO stdcall args
     * (instance, context). A third argument left an extra dword on the guest
     * stack for a real callback to clean up, and the old 160-byte instance put
     * the names where guidProduct and dwDevType belong. Fill the real
     * DIDEVICEINSTANCE_DX3A/W (560 bytes) and push two args, as the ABI says.
     *
     * devType accepts the legacy DIDEVTYPE_* codes as well as the DI8 class
     * and type codes that newer callers pass through this same vtable; both
     * number spaces agree for keyboard and mouse (2 = mouse/pointer, 3 =
     * keyboard). Force-feedback-only enumeration finds neither device. */
    uint32_t devtype = A32(1), cb = A32(2), ctx = A32(3), flags = A32(4);
    if (!cb) RET(DIERR_INVALIDPARAM, 5);
    int wide = (int)MEM32(A32(0) + DI_HOST_UNICODE);
    if (flags & DIEDFL_FORCEFEEDBACK) RET(DI_OK, 5);

    uint32_t mark = hle_scratch_mark();
    for (int k = 1; k <= 2; k++) {                        /* 1 = keyboard, 2 = mouse */
        uint32_t legacy = k == 1 ? DIDEVTYPE_KEYBOARD : DIDEVTYPE_MOUSE;
        uint32_t di8 = k == 1 ? DI8DEVTYPE_KEYBOARD : DI8DEVTYPE_MOUSE;
        int want;
        if (devtype == 0) want = 1;
        else if (devtype <= DIDEVTYPE_JOYSTICK) want = (devtype == legacy);
        else if (devtype <= DI8DEVCLASS_GAMECTRL)
            want = (devtype == DI8DEVCLASS_ALL ||
                    (devtype == DI8DEVCLASS_KEYBOARD && k == 1) ||
                    (devtype == DI8DEVCLASS_POINTER && k == 2));
        else if (devtype >= DI8DEVTYPE_DEVICE && devtype <= DI8DEVTYPE_SUPPLEMENTAL)
            want = (devtype == di8);
        else want = 0;
        if (!want) continue;

        uint32_t inst = hle_scratch(DIDEVICEINSTANCE_DX3_SIZE);
        memset(hle_ptr(inst), 0, DIDEVICEINSTANCE_DX3_SIZE);
        MEM32(inst) = DIDEVICEINSTANCE_DX3_SIZE;
        const uint8_t *g = k == 2 ? GUID_SYS_MOUSE : GUID_SYS_KEYBOARD;
        memcpy(hle_ptr(inst + 4), g, 16);                 /* guidInstance */
        memcpy(hle_ptr(inst + 20), g, 16);                /* guidProduct */
        uint32_t subtype = k == 2 ? DIDEVTYPEMOUSE_TRADITIONAL : DIDEVTYPEKEYBOARD_PCENH;
        MEM32(inst + DIDEVICEINSTANCE_DX3_DEVTYPE) = legacy | (subtype << 8);
        const char *name = k == 2 ? "Mouse" : "Keyboard";
        di_legacy_name(inst, DIDEVICEINSTANCE_DX3_NAME, name, wide);
        di_legacy_name(inst, DIDEVICEINSTANCE_DX3_PRODUCT, name, wide);
        uint32_t args[2] = { inst, ctx };
        if (hle_call_guest(cb, 2, args) == DIENUM_STOP) break;
    }
    hle_scratch_release(mark);
    RET(DI_OK, 5);
}
static void di_GetDeviceStatus(void) {
    uint32_t guid = A32(1);
    if (!guid) RET(DIERR_INVALIDPARAM, 2);
    const uint8_t *g = hle_ptr(guid);
    int known = memcmp(g, GUID_SYS_KEYBOARD, 16) == 0 || memcmp(g, GUID_SYS_MOUSE, 16) == 0;
    RET(known ? DI_OK : DIERR_DEVICENOTREG, 2);
}
static void di_Initialize(void) { RET(DI_OK, 4); }
static void di_RunControlPanel(void) { RET(DIERR_UNSUPPORTED, 3); }

/* IDirectInput7 adds these after IDirectInput's Initialize (slot 8 onward).
 * A title holding IDirectInput7 calls +0x24, i.e. CreateDeviceEx: with no
 * entry there the slot is a null function pointer and the call is a wild
 * jump, so the method is present even where it only mirrors CreateDevice. */
static void di_CreateDeviceEx(void) {                     /* (this, rguid, riid, &out, punkOuter) */
    /* DirectInput 7's variant: the interface id names the device layout. This
     * layer serves one layout, so it is the same device CreateDevice makes. */
    RET(make_device(A32(1), g_vt_dev, A32(3)), 5);
}

/* ---- IDirectInput8 ----
 * A separate 11-slot vtable over the same keyboard/mouse devices. The host
 * state at +8 is the A/W selection (0 = ANSI, 1 = wide); +12 is the version
 * set by Initialize, which the guest can also call through slot 7. A DI8
 * object is made per DirectInput8Create call, like Windows, and dies when the
 * guest releases it. */
enum { DI8_HOST_UNICODE = 8, DI8_HOST_VERSION = 12 };

static uint32_t di8_initialize_impl(uint32_t self, uint32_t hinst, uint32_t version) {
    /* wine-10.0 dinput8_Initialize: hinst first, then version zero, then the
     * two version gates. The order decides which HRESULT a bad call sees. */
    if (!hinst) return DIERR_INVALIDPARAM;
    if (!version) return DIERR_NOTINITIALIZED;
    if (version < 0x0800u) return DIERR_BETADIRECTINPUTVERSION;
    if (version > 0x0800u) return DIERR_OLDDIRECTINPUTVERSION;
    if (!MEM32(self + DI8_HOST_VERSION)) MEM32(self + DI8_HOST_VERSION) = version;
    return DI_OK;
}
static void di8_Initialize(void) {
    RET(di8_initialize_impl(A32(0), A32(1), A32(2)), 3);
}
static void di8_QueryInterface(void) {
    uint32_t self = A32(0), iid = A32(1), out = A32(2);
    if (!iid || !out) RET(E_POINTER, 3);
    MEM32(out) = 0;
    const uint8_t *want = hle_ptr(iid);
    if (!memcmp(want, IID_DI8_A, 16))       MEM32(self + DI8_HOST_UNICODE) = 0;
    else if (!memcmp(want, IID_DI8_W, 16))  MEM32(self + DI8_HOST_UNICODE) = 1;
    else if (memcmp(want, IID_UNKNOWN, 16)) RET(DIERR_NOINTERFACE, 3);
    /* One object serves both A and W: they differ only in the name encoding
     * filled into DIDEVICEINSTANCE, selected by the byte above. IUnknown keeps
     * whichever encoding the caller last selected. */
    MEM32(out) = self;
    MEM32(self + 4)++;
    RET(DI_OK, 3);
}
static void di8_AddRef(void) { RET(++MEM32(A32(0) + 4), 1); }
static void di8_Release(void) {
    uint32_t self = A32(0);
    uint32_t n = MEM32(self + 4) ? --MEM32(self + 4) : 0;
    if (n) RET(n, 1);
    hle_com_free(self);
    RET(0, 1);
}
static void di8_CreateDevice(void) {
    /* (this, rguid, &device, punkOuter) */
    if (!MEM32(A32(0) + DI8_HOST_VERSION)) RET(DIERR_NOTINITIALIZED, 4);
    RET(make_device(A32(1), g_vt_dev8, A32(2)), 4);
}
static void di8_GetDeviceStatus(void) {
    uint32_t guid = A32(1);
    if (!guid) RET(E_POINTER, 2);
    const uint8_t *g = hle_ptr(guid);
    int known = memcmp(g, GUID_SYS_KEYBOARD, 16) == 0 || memcmp(g, GUID_SYS_MOUSE, 16) == 0;
    RET(known ? DI_OK : DI_NOTATTACHED, 2);
}
static void di8_RunControlPanel(void) {
    RET(DIERR_UNSUPPORTED, 3);                            /* no control panel on this host */
}

/* Fill one DIDEVICEINSTANCEA/W and invoke a guest callback. The callback takes
 * exactly two stdcall args (instance, context); dwDevType is a field of the
 * instance, not a third argument. DI8 instances report DI8DEVTYPE_* (mouse
 * 0x12, keyboard 0x13) with the traditional/PC-enhanced subtype in the high
 * byte, and a zero guidFFDriver because neither device has force feedback. */
static uint32_t di8_enum_one(uint32_t cb, uint32_t ctx, int kind, int wide, uint32_t type) {
    uint32_t inst = hle_scratch(DIDEVICEINSTANCE_SIZE);
    memset(hle_ptr(inst), 0, DIDEVICEINSTANCE_SIZE);
    MEM32(inst) = DIDEVICEINSTANCE_SIZE;
    const uint8_t *g = kind == 2 ? GUID_SYS_MOUSE : GUID_SYS_KEYBOARD;
    memcpy(hle_ptr(inst + 4), g, 16);
    memcpy(hle_ptr(inst + 20), g, 16);
    uint32_t devtype = kind == 2
        ? (DI8DEVTYPE_MOUSE | (DI8DEVTYPEMOUSE_TRADITIONAL << 8))
        : (DI8DEVTYPE_KEYBOARD | (DI8DEVTYPEKEYBOARD_PCENH << 8));
    MEM32(inst + DIDEVICEINSTANCE_DEVTYPE) = devtype;
    const char *name = kind == 2 ? "Mouse" : "Keyboard";
    size_t nlen = strlen(name) + 1;
    if (wide) {
        for (size_t i = 0; i < nlen; i++)
            MEM16(inst + DIDEVICEINSTANCE_NAME + 2u * (uint32_t)i) = (uint16_t)(uint8_t)name[i];
        memcpy(hle_ptr(inst + DIDEVICEINSTANCE_PRODUCT),
               hle_ptr(inst + DIDEVICEINSTANCE_NAME), 2u * (uint32_t)nlen);
    } else {
        memcpy(hle_ptr(inst + DIDEVICEINSTANCE_NAME), name, nlen);
        memcpy(hle_ptr(inst + DIDEVICEINSTANCE_PRODUCT), name, nlen);
    }
    /* A specific DI8DEVTYPE_* filter is checked against the low byte, as in
     * Wine's try_enum_device. Class filters are handled by the caller. */
    if (type > DI8DEVCLASS_GAMECTRL && (devtype & 0xffu) != type) return DIENUM_CONTINUE;
    uint32_t args[2] = { inst, ctx };
    return hle_call_guest(cb, 2, args);
}
static void di8_EnumDevices(void) {
    /* (this, devType, callback, context, flags) */
    uint32_t type = A32(1), cb = A32(2), ctx = A32(3), flags = A32(4);
    if (!cb) RET(DIERR_INVALIDPARAM, 5);
    if ((type > DI8DEVCLASS_GAMECTRL && type < DI8DEVTYPE_DEVICE) ||
        type > DI8DEVTYPE_SUPPLEMENTAL) RET(DIERR_INVALIDPARAM, 5);
    if (flags & ~DIEDFL_VALID) RET(DIERR_INVALIDPARAM, 5);
    if (!MEM32(A32(0) + DI8_HOST_VERSION)) RET(DIERR_NOTINITIALIZED, 5);
    /* A caller asking only for force-feedback devices gets none: both real
     * devices report a null guidFFDriver. */
    if (flags & DIEDFL_FORCEFEEDBACK) RET(DI_OK, 5);

    int want_mouse = 0, want_kbd = 0;
    if (type <= DI8DEVCLASS_GAMECTRL) {                  /* a device *class* */
        want_mouse = (type == DI8DEVCLASS_ALL || type == DI8DEVCLASS_POINTER);
        want_kbd   = (type == DI8DEVCLASS_ALL || type == DI8DEVCLASS_KEYBOARD);
        /* DI8DEVCLASS_GAMECTRL (and DEVICE): none enumerated on this host. */
    } else {                                             /* a specific type */
        want_mouse = (type == DI8DEVTYPE_MOUSE);
        want_kbd   = (type == DI8DEVTYPE_KEYBOARD);
    }
    uint32_t mark = hle_scratch_mark();
    if (want_mouse && di8_enum_one(cb, ctx, 2,
            (int)MEM32(A32(0) + DI8_HOST_UNICODE), type) != DIENUM_CONTINUE) {
        hle_scratch_release(mark);
        RET(DI_OK, 5);
    }
    if (want_kbd && di8_enum_one(cb, ctx, 1,
            (int)MEM32(A32(0) + DI8_HOST_UNICODE), type) != DIENUM_CONTINUE) {
        hle_scratch_release(mark);
        RET(DI_OK, 5);
    }
    hle_scratch_release(mark);
    RET(DI_OK, 5);
}
static void di8_FindDevice(void) {
    /* (this, rguid, name, &instance_guid). This host enumerates exactly one
     * instance per system device and its instance GUID is the system GUID, so
     * a known device GUID resolves to itself; there is no second instance a
     * name could select. Anything else is DIERR_DEVICENOTREG, not a fake GUID. */
    uint32_t guid = A32(1), out = A32(3);
    if (!guid || !out) RET(E_POINTER, 4);
    MEM32(out) = 0;
    const uint8_t *g = hle_ptr(guid);
    if (memcmp(g, GUID_SYS_KEYBOARD, 16) && memcmp(g, GUID_SYS_MOUSE, 16))
        RET(DIERR_DEVICENOTREG, 4);
    memcpy(hle_ptr(out), g, 16);
    RET(DI_OK, 4);
}
static void di8_EnumDevicesBySemantics(void) { RET(DIERR_UNSUPPORTED, 6); }
static void di8_ConfigureDevices(void) { RET(DIERR_UNSUPPORTED, 5); }

/* IDirectInputDevice8 adds three slots after WriteEffectToFile. Action mapping
 * and image info need a mapping UI and a driver image database this host has
 * neither of; report E_NOTIMPL (DIERR_UNSUPPORTED) from real, non-null shims. */
static void dev8_BuildActionMap(void)  { RET(DIERR_UNSUPPORTED, 4); }
static void dev8_SetActionMap(void)    { RET(DIERR_UNSUPPORTED, 4); }
static void dev8_GetImageInfo(void)    { RET(DIERR_UNSUPPORTED, 2); }

/* ---- vtables and the entry points ---- */
static void build_vtables(void) {
    if (g_vt_di) return;
    static const win32hle_shim di[] = {
        { "IDirectInput::QueryInterface", di_QueryInterface },
        { "IDirectInput::AddRef", di_AddRef },
        { "IDirectInput::Release", di_Release },
        { "IDirectInput::CreateDevice", di_CreateDevice },
        { "IDirectInput::EnumDevices", di_EnumDevices },
        { "IDirectInput::GetDeviceStatus", di_GetDeviceStatus },
        { "IDirectInput::RunControlPanel", di_RunControlPanel },
        { "IDirectInput::Initialize", di_Initialize },
        { "IDirectInput7::CreateDeviceEx", di_CreateDeviceEx },
        { "IDirectInput7::EnumDevicesBySemantics", di_CreateDeviceEx },   /* placeholder probe */
        { 0, 0 }
    };
    static const win32hle_shim dev[] = {
        { "IDirectInputDevice::QueryInterface", dev_QueryInterface },
        { "IDirectInputDevice::AddRef", hle_com_AddRef },
        { "IDirectInputDevice::Release", dev_Release },
        { "IDirectInputDevice::GetCapabilities", dev_GetCapabilities },
        { "IDirectInputDevice::EnumObjects", dev_EnumObjects },
        { "IDirectInputDevice::GetProperty", dev_GetProperty },
        { "IDirectInputDevice::SetProperty", dev_SetProperty },
        { "IDirectInputDevice::Acquire", dev_Acquire },
        { "IDirectInputDevice::Unacquire", dev_Unacquire },
        { "IDirectInputDevice::GetDeviceState", dev_GetDeviceState },
        { "IDirectInputDevice::GetDeviceData", dev_GetDeviceData },
        { "IDirectInputDevice::SetDataFormat", dev_SetDataFormat },
        { "IDirectInputDevice::SetEventNotification", dev_SetProperty },
        { "IDirectInputDevice::SetCooperativeLevel", dev_SetCooperativeLevel },
        { "IDirectInputDevice::GetObjectInfo", dev_GetObjectInfo },
        { "IDirectInputDevice::GetDeviceInfo", dev_GetDeviceInfo },
        { "IDirectInputDevice::RunControlPanel", dev_RunControlPanel },
        { "IDirectInputDevice::Initialize", dev_Initialize },
        /* IDirectInputDevice2/7. The order is the interface: a caller indexes
         * by offset, so these must follow Initialize and must all be present. */
        { "IDirectInputDevice::CreateEffect", dev_CreateEffect },
        { "IDirectInputDevice::EnumEffects", dev_EnumEffects },
        { "IDirectInputDevice::GetEffectInfo", dev_GetEffectInfo },
        { "IDirectInputDevice::GetForceFeedbackState", dev_GetForceFeedbackState },
        { "IDirectInputDevice::SendForceFeedbackCommand", dev_SendForceFeedbackCommand },
        { "IDirectInputDevice::EnumCreatedEffectObjects", dev_EnumCreatedEffectObjects },
        { "IDirectInputDevice::Escape", dev_Escape },
        { "IDirectInputDevice::Poll", dev_Poll },
        { "IDirectInputDevice::SendDeviceData", dev_SendDeviceData },
        { "IDirectInputDevice::EnumEffectsInFile", dev_EnumEffectsInFile },
        { "IDirectInputDevice::WriteEffectToFile", dev_WriteEffectToFile },
        { 0, 0 }
    };
    static const win32hle_shim di8[] = {
        /* IDirectInput8A/W: 11 slots (wine-10.0 dinput8_vtbl). Note there is NO
         * CreateDeviceEx here - slot 9 is EnumDevicesBySemantics. */
        { "IDirectInput8::QueryInterface", di8_QueryInterface },
        { "IDirectInput8::AddRef", di8_AddRef },
        { "IDirectInput8::Release", di8_Release },
        { "IDirectInput8::CreateDevice", di8_CreateDevice },
        { "IDirectInput8::EnumDevices", di8_EnumDevices },
        { "IDirectInput8::GetDeviceStatus", di8_GetDeviceStatus },
        { "IDirectInput8::RunControlPanel", di8_RunControlPanel },
        { "IDirectInput8::Initialize", di8_Initialize },
        { "IDirectInput8::FindDevice", di8_FindDevice },
        { "IDirectInput8::EnumDevicesBySemantics", di8_EnumDevicesBySemantics },
        { "IDirectInput8::ConfigureDevices", di8_ConfigureDevices },
        { 0, 0 }
    };
    /* The first 29 entries mirror dev[] exactly; the last three are the DI8
     * additions. Distinct shim names keep the two resolved vtable entries
     * independent even where they run the same method. */
    static const win32hle_shim dev8[] = {
        { "IDirectInputDevice8::QueryInterface", dev_QueryInterface },
        { "IDirectInputDevice8::AddRef", hle_com_AddRef },
        { "IDirectInputDevice8::Release", dev_Release },
        { "IDirectInputDevice8::GetCapabilities", dev_GetCapabilities },
        { "IDirectInputDevice8::EnumObjects", dev_EnumObjects },
        { "IDirectInputDevice8::GetProperty", dev_GetProperty },
        { "IDirectInputDevice8::SetProperty", dev_SetProperty },
        { "IDirectInputDevice8::Acquire", dev_Acquire },
        { "IDirectInputDevice8::Unacquire", dev_Unacquire },
        { "IDirectInputDevice8::GetDeviceState", dev_GetDeviceState },
        { "IDirectInputDevice8::GetDeviceData", dev_GetDeviceData },
        { "IDirectInputDevice8::SetDataFormat", dev_SetDataFormat },
        { "IDirectInputDevice8::SetEventNotification", dev_SetProperty },
        { "IDirectInputDevice8::SetCooperativeLevel", dev_SetCooperativeLevel },
        { "IDirectInputDevice8::GetObjectInfo", dev_GetObjectInfo },
        { "IDirectInputDevice8::GetDeviceInfo", dev_GetDeviceInfo },
        { "IDirectInputDevice8::RunControlPanel", dev_RunControlPanel },
        { "IDirectInputDevice8::Initialize", dev_Initialize },
        { "IDirectInputDevice8::CreateEffect", dev_CreateEffect },
        { "IDirectInputDevice8::EnumEffects", dev_EnumEffects },
        { "IDirectInputDevice8::GetEffectInfo", dev_GetEffectInfo },
        { "IDirectInputDevice8::GetForceFeedbackState", dev_GetForceFeedbackState },
        { "IDirectInputDevice8::SendForceFeedbackCommand", dev_SendForceFeedbackCommand },
        { "IDirectInputDevice8::EnumCreatedEffectObjects", dev_EnumCreatedEffectObjects },
        { "IDirectInputDevice8::Escape", dev_Escape },
        { "IDirectInputDevice8::Poll", dev_Poll },
        { "IDirectInputDevice8::SendDeviceData", dev_SendDeviceData },
        { "IDirectInputDevice8::EnumEffectsInFile", dev_EnumEffectsInFile },
        { "IDirectInputDevice8::WriteEffectToFile", dev_WriteEffectToFile },
        { "IDirectInputDevice8::BuildActionMap", dev8_BuildActionMap },
        { "IDirectInputDevice8::SetActionMap", dev8_SetActionMap },
        { "IDirectInputDevice8::GetImageInfo", dev8_GetImageInfo },
        { 0, 0 }
    };
    g_vt_di = hle_com_vtable(di);
    g_vt_dev = hle_com_vtable(dev);
    g_vt_di8 = hle_com_vtable(di8);
    g_vt_dev8 = hle_com_vtable(dev8);
}

/* One IDirectInput object per A/W encoding (the names EnumDevices reports are
 * the only difference). The object's +8 slot carries the A/W flag. */
static uint32_t di_create_impl(uint32_t out, int wide) {
    if (!out) return DIERR_INVALIDPARAM;
    build_vtables();
    uint32_t *slot = &g_di[wide != 0];
    if (!*slot) {
        *slot = hle_com_new(g_vt_di, 16);
        if (!*slot) return DIERR_DEVICENOTREG;
        MEM32(*slot + 4) = 1;
        g_di_refs++;
    }
    MEM32(*slot + DI_HOST_UNICODE) = (uint32_t)wide;
    MEM32(out) = *slot;
    return DI_OK;
}

/* DirectInputCreateA/W(hinst, version, &di, punkOuter) */
static void d_CreateA(void) { RET(di_create_impl(A32(2), 0), 4); }
static void d_CreateW(void) { RET(di_create_impl(A32(2), 1), 4); }

/* DirectInputCreateEx(hinst, version, riid, &di, punkOuter): DirectInput 7's
 * entry point, and the one place its argument order differs from
 * DirectInputCreateA - the interface id comes BEFORE the out pointer, not
 * after it. Same object; the GUID only selects a vtable layout this layer
 * answers identically. */
static void d_CreateEx(void) {
    uint32_t iid = A32(2), out = A32(3);
    /* DirectInputCreateEx takes IID_IDirectInput7W (…685) for the wide layout.
     * The byte differs from the A IID at offset 0 only. */
    int wide = iid && MEM8(iid) == 0x85;
    RET(di_create_impl(out, wide), 5);
}

/* DirectInput8Create(hinst, version, riid, &di, outer): the ONLY public export
 * of DINPUT8.dll (wine-10.0 dinput8.spec). The validation order and HRESULTs
 * match wine-10.0 dinput.c:937: a null out pointer is E_POINTER, a non-DI8 IID
 * is DIERR_NOINTERFACE with *out cleared, a non-null outer clears *out but
 * returns the previous QueryInterface result (DI_OK), and Initialize's gate
 * supplies the version error codes. */
static void d_Create8(void) {
    uint32_t iid = A32(2), out = A32(3), outer = A32(4);
    if (!out) RET(E_POINTER, 5);
    MEM32(out) = 0;
    if (!iid) RET(DIERR_NOINTERFACE, 5);
    const uint8_t *want = hle_ptr(iid);
    int wide;
    if (!memcmp(want, IID_DI8_A, 16))      wide = 0;
    else if (!memcmp(want, IID_DI8_W, 16)) wide = 1;
    else if (!memcmp(want, IID_UNKNOWN, 16)) wide = 0;
    else RET(DIERR_NOINTERFACE, 5);

    build_vtables();
    uint32_t obj = hle_com_new(g_vt_di8, 16);
    if (!obj) RET(E_OUTOFMEMORY, 5);
    MEM32(obj + DI8_HOST_UNICODE) = (uint32_t)wide;
    MEM32(obj + DI8_HOST_VERSION) = 0;
    uint32_t hr = di8_initialize_impl(obj, A32(0), A32(1));
    if (hr == DI_OK && !outer) {
        MEM32(out) = obj;
        RET(DI_OK, 5);
    }
    /* Outer aggregation and a failed Initialize both hand back no object; a
     * rejected outer keeps the DI_OK that QueryInterface would have returned. */
    hle_com_free(obj);
    RET(hr, 5);
}

const win32hle_shim win32hle_dinput[] = {
    { "DirectInputCreateA",  d_CreateA },
    { "DirectInputCreateW",  d_CreateW },
    { "DirectInputCreateEx", d_CreateEx },
    { "DirectInput8Create",  d_Create8 },
    { 0, 0 }
};
