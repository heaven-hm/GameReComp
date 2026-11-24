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
 * What is real: the read-only device properties (GetProperty) answer from this
 * device table and the same SDL state the poll uses -- the buffer size is the
 * zero events GetDeviceData really has, mouse granularity is the pixel units
 * GetDeviceState really reports, key scan codes come from the DIK codes the
 * keyboard state uses, and the instance/product names are the ones EnumDevices
 * and GetDeviceInfo report. Writes to per-object properties are still hints.
 *
 * Joysticks come from the SAME SDL joystick state SDL 1.2's SDL_NumJoysticks /
 * SDL_JoystickOpen already use; this is not a second input backend. When SDL
 * enumerates a stick, EnumDevices reports it with a real DIDEVICEINSTANCE
 * (identity derived from SDL's device GUID, name from SDL), CreateDevice opens
 * that SDL device and GetDeviceState fills the standard DIJOYSTATE axes/buttons
 * and a centred POV, bounded by the guest's dwDataSize. When SDL enumerates
 * NONE, nothing is invented: the joystick is absent from EnumDevices, and
 * CreateDevice/FindDevice answer DIERR_DEVICENOTREG, DI8 GetDeviceStatus
 * answers DI_NOTATTACHED and the legacy one DIERR_DEVICENOTREG, all of which
 * a game handles by taking its no-joystick path. RECOMP_DI_NO_JOYSTICK=1 is a
 * diagnostic override that forces this truthful no-device path headlessly.
 *
 * What is absent, and named as such rather than faked:
 *   - Force feedback, effects, and writable device properties. The joystick's
 *     guidFFDriver is zero, DIDC_FORCEFEEDBACK is never set, and every FF slot
 *     answers DIERR_UNSUPPORTED. There is no XInput/HID/Raw Input facade here.
 *   - Per-object axis ranges/dead-zones and custom DIDATAFORMAT object offsets:
 *     the guest's 32-bit offsets are not reinterpreted; the standard DIJOYSTATE
 *     layout is filled and its unused fields are zero. DIPROP_RANGE,
 *     DIPROP_DEADZONE, DIPROP_JOYSTICKID, DIPROP_GUIDANDPATH and DIPROP_VIDPID
 *     answer DIERR_UNSUPPORTED rather than a plausible-looking value.
 *   - Action mapping (BuildActionMap/SetActionMap/GetImageInfo,
 *     EnumDevicesBySemantics, ConfigureDevices) and a control panel: the DI8
 *     slots exist and return DIERR_UNSUPPORTED, never a fake success or a
 *     null function pointer a guest could jump through.
 *   - EnumDevices reports the keyboard, the mouse, and only the joysticks SDL
 *     actually enumerates.
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
 * names. The ANSI one is 560 bytes; the wide one doubles both names, so it is
 * 1080. The second name starts at 300 (A) or 560 (W) -- writing it at 300 for
 * W would land inside the first WCHAR name. */
enum { DIDEVICEINSTANCE_DX3_SIZE = 560, DIDEVICEINSTANCE_DX3_W_SIZE = 1080,
       DIDEVICEINSTANCE_DX3_DEVTYPE = 36, DIDEVICEINSTANCE_DX3_NAME = 40,
       DIDEVICEINSTANCE_DX3_PRODUCT = 300, DIDEVICEINSTANCE_DX3_W_PRODUCT = 560 };

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
#define DI8DEVTYPE_JOYSTICK   0x14u
#define DI8DEVTYPE_SUPPLEMENTAL 0x1Cu
#define DI8DEVTYPEMOUSE_TRADITIONAL 2u
#define DI8DEVTYPEKEYBOARD_PCENH    4u
/* DIDEVTYPEJOYSTICK_TRADITIONAL / DI8DEVTYPEJOYSTICK_STANDARD are both 2. */
#define DIJOY_SUBTYPE         2u
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
 * GUID + 2 WORDs: 580 bytes (A) or 1100 (W). */
enum { DIDEVICEINSTANCE_SIZE = 580, DIDEVICEINSTANCE_W_SIZE = 1100,
       DIDEVICEINSTANCE_DEVTYPE = 36, DIDEVICEINSTANCE_NAME = 40,
       DIDEVICEINSTANCE_PRODUCT = 300, DIDEVICEINSTANCE_W_PRODUCT = 560 };

/* Bit a device reports in GetDeviceData's dwOfs / the DIDEVICEOBJECTDATA stream.
 * The keyboard is 256 bytes of DIK_* codes; the mouse is 3 axes then 4
 * buttons. */
enum { DIK_SIZE = 256, DIMOFS_X = 0, DIMOFS_Y = 4, DIMOFS_Z = 8,
       DIMOFS_BUTTON0 = 12 };
/* DIJOYSTATE/DIJOYSTATE2 x86 offsets (wine-10.0 dinput.h): six LONG axes, two
 * LONG sliders, four DWORD POVs, then the button bytes. We fill the first eight
 * LONGs and 32 buttons; velocity/acceleration/force stay zero. The whole struct
 * is 272 bytes, so a guest asking for DIJOYSTATE2 still gets a bounded copy. */
enum { DIJOFS_X = 0, DIJOFS_POV0 = 32, DIJOFS_BUTTON0 = 48,
       DIN_JOY_AXES = 8, DIN_JOY_BUTTONS = 32, DIJOYSTATE2_SIZE = 272 };
#define DI_POV_CENTERED 0xFFFFFFFFu

/* DIPROP_* are not real GUIDs. dinput.h's MAKEDIPROP(n) casts the small
 * integer n to a GUID *pointer*, so a guest passes n (1..26) in rguidProp; it
 * is never an address and must not be dereferenced. Wine treats any value
 * under 0x10000 as a DIPROP and quietly ignores anything else. */
enum { DIPROP_BUFFERSIZE = 1, DIPROP_GRANULARITY = 3,
       DIPROP_GUIDANDPATH = 12, DIPROP_INSTANCENAME = 13,
       DIPROP_PRODUCTNAME = 14, DIPROP_JOYSTICKID = 15,
       DIPROP_SCANCODE = 23, DIPROP_VIDPID = 24 };
/* DIPROPHEADER, DIPROPDWORD and DIPROPSTRING (header + WCHAR[260]) on x86.
 * A property is only written when the guest's dwSize covers its whole struct;
 * a short struct is rejected without touching a single byte past dwSize. */
enum { DIPROPHEADER_SIZE = 16, DIPROPDWORD_SIZE = 20,
       DIPROPSTRING_SIZE = 16 + 260 * 2 };
enum { DIPH_DEVICE = 0, DIPH_BYOFFSET = 1 };

/* GUIDs as the guest passes them: 16 bytes, little-endian first three fields.
 * Only the ones a game asks for matter here. */
static const uint8_t GUID_SYS_KEYBOARD[16] = {
    0x61, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
    0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00 };
static const uint8_t GUID_SYS_MOUSE[16] = {
    0x60, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
    0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00 };

/* GUID_Joystick (wine-10.0 dinput.h: 6F1D2B70-D5A0-11CF-BFC7...); the GUID a
 * game passes to CreateDevice when it wants "the joystick" without enumerating.
 * Per-instance GUIDs keep the 0x70 tag and carry SDL's 128-bit device GUID so
 * they are stable across calls and distinguish two identical physical sticks. */
static const uint8_t GUID_SYS_JOYSTICK[16] = {
     0x70, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
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
    int kind;                 /* 1 = keyboard, 2 = mouse, 3 = SDL joystick */
    int acquired;
    uint32_t data_size;       /* the DIDATAFORMAT's dwDataSize the game passed */
    int have_format;
    uint32_t hwnd;
    uint32_t coop;            /* the DISCL_* flags the game set */
    int32_t last_x, last_y;   /* previous absolute sample, for exclusive relative axes */
    int have_last;
    int wide;                 /* A/W encoding for string properties */
    int di8;                  /* which facade made the device (dwDevType numbering) */
    /* Joystick-only host state. The SDL pointer stays host-side: the guest
     * object itself is a 16-byte guest allocation and cannot hold it. */
    SDL_Joystick *joy;
    int joy_index;            /* SDL_NumJoysticks index, -1 for other devices */
    int joy_axes, joy_buttons;/* clamped to what GetDeviceState really fills */
    uint8_t inst_guid[16];    /* the instance GUID handed to the guest */
    char name[64];            /* copy of SDL's name, valid after SDL closes it */
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

/* ---- SDL joystick state (the same state SDL 1.2's joystick shims use) ----
 * Enumeration is pull-based: SDL owns the device list, this layer never caches
 * a fabricated one. A failed subsystem init or a host with no stick is simply
 * zero devices, which every entry point below reports as an absent device. */
static int di_joy_disabled(void) {
    /* Headless/diagnostic override: present fewer (never fake) devices. */
    const char *s = getenv("RECOMP_DI_NO_JOYSTICK");
    return s && *s && strcmp(s, "0") != 0;
}
static int di_joy_count(void) {
    if (di_joy_disabled()) return 0;
    if (!SDL_WasInit(SDL_INIT_JOYSTICK))
        if (SDL_InitSubSystem(SDL_INIT_JOYSTICK) != 0) return 0;
    int n = SDL_NumJoysticks();
    return n > 0 ? (n < DIN_MAX_DEVICES ? n : DIN_MAX_DEVICES) : 0;
}
/* Stable instance GUID for an SDL index. SDL's 128-bit device GUID supplies
 * bytes 4..14, byte 15 disambiguates two identical sticks, and the 0x70/2B/1D
 * prefix keeps the GUID in the GUID_Joystick family. */
static void di_joy_guid(uint8_t out[16], int index) {
    SDL_JoystickGUID g = SDL_JoystickGetDeviceGUID(index);
    memcpy(out, GUID_SYS_JOYSTICK, 16);
    memcpy(out + 4, &g, 12);
    out[15] = (uint8_t)index;
}
/* Resolve the GUID a guest got from EnumDevices/FindDevice, or the generic
 * GUID_Joystick, back to an SDL index. -1 means "not connected": no device is
 * created from a stale or invented GUID. */
static int di_joy_index(const uint8_t *g) {
    if (!g) return -1;
    int n = di_joy_count();
    if (!memcmp(g, GUID_SYS_JOYSTICK, 16)) return n > 0 ? 0 : -1;
    for (int i = 0; i < n; i++) {
        uint8_t want[16];
        di_joy_guid(want, i);
        if (!memcmp(g, want, 16)) return i;
    }
    return -1;
}
static void di_joy_name(int index, char out[64]) {
    const char *n = SDL_JoystickNameForIndex(index);
    if (!n) n = "Joystick";
    snprintf(out, 64, "%s", n);
}

/* One device factory for every facade. `vt` selects the interface layout the
 * guest will index (DX3/5/7's 29-slot device or DI8's 32-slot one); the state
 * and every method behind it are the same SDL-backed implementation. All the
 * host-side fields are cleared here so a freed slot reused by a later device
 * cannot inherit a previous device's window/handedness/relative-axis origin. */
static uint32_t make_device(uint32_t guid, uint32_t vt, uint32_t out, int wide) {
    if (!guid || !out) return DIERR_INVALIDPARAM;
    MEM32(out) = 0;
    if (g_ndev >= DIN_MAX_DEVICES) return DIERR_DEVICENOTREG;
    const uint8_t *g = hle_ptr(guid);
    int kind = memcmp(g, GUID_SYS_KEYBOARD, 16) == 0 ? 1
             : memcmp(g, GUID_SYS_MOUSE, 16) == 0 ? 2 : 0;
    int joy_index = -1;
    SDL_Joystick *joy = NULL;
    if (!kind) {
        /* An unknown GUID is not a device. Only a GUID SDL really enumerated
         * (or GUID_Joystick with a stick connected) may make a joystick; a
         * device that never reports an axis would tell the game a stick is
         * connected and centred. DIERR_DEVICENOTREG is the true no-stick answer. */
        joy_index = di_joy_index(g);
        if (joy_index < 0) return DIERR_DEVICENOTREG;
        joy = SDL_JoystickOpen(joy_index);
        if (!joy) return DIERR_DEVICENOTREG;
        kind = 3;
    }
    uint32_t obj = hle_com_new(vt, 16);
    if (!obj) {
        if (joy) SDL_JoystickClose(joy);
        return DIERR_DEVICENOTREG;
    }
    din_device *d = &g_dev[g_ndev++];
    memset(d, 0, sizeof *d);
    d->obj = obj;
    d->kind = kind;
    d->wide = wide != 0;
    d->di8 = (vt == g_vt_dev8);
    if (kind == 3) {
        d->joy = joy;
        d->joy_index = joy_index;
        int na = SDL_JoystickNumAxes(joy), nb = SDL_JoystickNumButtons(joy);
        d->joy_axes = na > DIN_JOY_AXES ? DIN_JOY_AXES : na;
        d->joy_buttons = nb > DIN_JOY_BUTTONS ? DIN_JOY_BUTTONS : nb;
        if (d->joy_axes < 0) d->joy_axes = 0;
        if (d->joy_buttons < 0) d->joy_buttons = 0;
        di_joy_guid(d->inst_guid, joy_index);
        di_joy_name(joy_index, d->name);
    }
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
    /* DIDEVCAPS is filled from what the device really is: a keyboard sends 256
     * keys, a mouse 3 axes and 4 buttons, a joystick only the axes and buttons
     * SDL actually reports. Nothing beyond the guest's dwSize is touched, and
     * the FF flags are never set (there is no force feedback on this host). */
    uint32_t caps = A32(1);
    if (!caps) RET(E_POINTER, 2);
    int i = dev_index(A32(0));
    uint32_t n = MEM32(caps);
    if (n > 44) n = 44;
    if (n > 4) memset(hle_ptr(caps + 4), 0, n - 4);
    if (n >= 4) {
        MEM32(caps) = 44;                                 /* the real struct size */
        MEM32(caps + 4) = 0x00000001;                     /* DIDC_ATTACHED only */
    }
    uint32_t type = 0, axes = 0, buttons = 0;
    if (i >= 0) {
        const din_device *d = &g_dev[i];
        if (d->kind == 1) type = d->di8 ? DI8DEVTYPE_KEYBOARD : DIDEVTYPE_KEYBOARD;
        else if (d->kind == 2) {
            type = d->di8 ? DI8DEVTYPE_MOUSE : DIDEVTYPE_MOUSE;
            axes = 3; buttons = 4;
        } else if (d->kind == 3) {
            type = d->di8 ? DI8DEVTYPE_JOYSTICK : DIDEVTYPE_JOYSTICK;
            type |= DIJOY_SUBTYPE << 8;
            axes = (uint32_t)d->joy_axes;
            buttons = (uint32_t)d->joy_buttons;
        }
    }
    if (type && n >= 12) MEM32(caps + 8) = type;
    if (n >= 16) MEM32(caps + 12) = axes;
    if (n >= 24) MEM32(caps + 20) = buttons;
    RET(DI_OK, 2);
}
static void dev_EnumObjects(void) {
    /* A callback that is never called reports no objects. That is true for the
     * keyboard (a game reads its 256-byte array, not the object list), the
     * mouse, and the joystick: this layer fills the standard DIJOYSTATE layout
     * rather than a per-object descriptor table, so claiming objects here would
     * be a lie. A game falls back to the data format it passed. */
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
    if (g_dev[i].kind == 3 && !SDL_WasInit(SDL_INIT_JOYSTICK) &&
        SDL_InitSubSystem(SDL_INIT_JOYSTICK) != 0) RET(DIERR_NOTINITIALIZED, 1);
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
    } else if (g_dev[i].kind == 3) {
        if (want > DIJOYSTATE2_SIZE) want = DIJOYSTATE2_SIZE;
    } else if (want > 20) {
        want = 20;                       /* DIMOUSESTATE2: three axes and eight buttons */
    }
    /* What the guest is handed, so a replay hands back the same bytes. The
     * recorded value is the shim's answer, not the device's state: at replay
     * time SDL is not consulted at all, which is what makes the transcript
     * device-neutral and lets a recorder on another machine produce the same
     * file. */
    const char *what = g_dev[i].kind == 1 ? "dikeys"
                     : g_dev[i].kind == 3 ? "dijoystate" : "dimouse";
    if (want > 0 && hle_input_replaying() && hle_input_replay(what, hle_ptr(buf), (size_t)want))
        RET(DI_OK, 3);
    SDL_PumpEvents();
    if (g_dev[i].kind == 3) SDL_JoystickUpdate();
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
    } else if (g_dev[i].kind == 3) {
        /* The joystick: standard DIJOYSTATE offsets, filled only from the SDL
         * device this object opened. A POV is centred rather than a fake "up"
         * when no hat is reported; the unused velocity/acceleration/force tail
         * is zero. Every write is gated by the guest's dwDataSize AND by this
         * struct's own layout, so a short buffer cannot be overrun. */
        din_device *d = &g_dev[i];
        memset(hle_ptr(buf), 0, (size_t)want);
        for (int a = 0; a < DIN_JOY_AXES && a < d->joy_axes; a++) {
            uint32_t off = DIJOFS_X + 4u * (uint32_t)a;
            if (want >= (int)off + 4) {
                MEM32(buf + off) = (uint32_t)SDL_JoystickGetAxis(d->joy, a);
            }
        }
        for (int p = 0; p < 4; p++) {
            uint32_t off = DIJOFS_POV0 + 4u * (uint32_t)p;
            if (want >= (int)off + 4) MEM32(buf + off) = DI_POV_CENTERED;
        }
        if (want >= (int)DIJOFS_POV0 + 4 && SDL_JoystickNumHats(d->joy) > 0) {
            /* SDL hat bits (1=up,2=right,4=down,8=left) map to DirectInput's
             * hundredths of a degree; impossible combinations stay centred. */
            static const uint32_t pov[16] = {
                DI_POV_CENTERED, 0u, 9000u, 4500u, 18000u, DI_POV_CENTERED,
                13500u, DI_POV_CENTERED, 27000u, 31500u, DI_POV_CENTERED,
                DI_POV_CENTERED, 22500u, DI_POV_CENTERED, DI_POV_CENTERED,
                DI_POV_CENTERED };
            uint8_t h = SDL_JoystickGetHat(d->joy, 0);
            if (h < 16) MEM32(buf + DIJOFS_POV0) = pov[h];
        }
        for (int b = 0; b < DIN_JOY_BUTTONS && b < d->joy_buttons; b++) {
            uint32_t off = DIJOFS_BUTTON0 + (uint32_t)b;
            if (want >= (int)off + 1)
                MEM8(buf + off) = SDL_JoystickGetButton(d->joy, b) ? 0x80 : 0;
        }
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
/* Write a NUL-terminated name into a DIPROPSTRING's WCHAR[MAX_PATH] in the
 * encoding this device's facade selected. The caller has already checked the
 * guest's dwSize, so the write stays inside the struct the guest allocated. */
static void prop_string(uint32_t out, const char *s, int wide) {
    size_t n = strlen(s);
    if (wide) {
        for (size_t k = 0; k < n; k++)
            MEM16(out + 2u * (uint32_t)k) = (uint16_t)(unsigned char)s[k];
        MEM16(out + 2u * (uint32_t)n) = 0;
    } else {
        memcpy(hle_ptr(out), s, n);
        MEM8(out + (uint32_t)n) = 0;
    }
}
static void dev_GetProperty(void) {
    /* (this, rguidProp, DIPROPHEADER*). rguidProp is a MAKEDIPROP integer, not
     * a GUID address: Wine ignores values >= 0x10000 and answers DI_OK without
     * writing, which a guest passing a real GUID still safely survives.
     *
     * Only properties this host can answer truthfully are supported. Every
     * value below comes from the same device table and SDL state the polling
     * path uses, and every write is bounded by the guest's dwSize. Unsupported
     * properties answer DIERR_UNSUPPORTED instead of a plausible-looking zero. */
    uint32_t prop = A32(1), hdr = A32(2);
    if (!hdr) RET(DIERR_INVALIDPARAM, 3);
    if ((prop >> 16) != 0) RET(DI_OK, 3);
    if (MEM32(hdr) < DIPROPHEADER_SIZE ||
        MEM32(hdr + 4) != DIPROPHEADER_SIZE) RET(DIERR_INVALIDPARAM, 3);
    int i = dev_index(A32(0));
    if (i < 0) RET(DIERR_INVALIDPARAM, 3);

    uint32_t size = MEM32(hdr), how = MEM32(hdr + 12), obj = MEM32(hdr + 8);
    uint32_t data = hdr + DIPROPHEADER_SIZE;             /* after DIPROPHEADER */
    switch (prop) {
    case DIPROP_BUFFERSIZE:
        if (size != DIPROPDWORD_SIZE || how != DIPH_DEVICE || obj)
            RET(DIERR_INVALIDPARAM, 3);
        /* No event queue is allocated: GetDeviceData always reports zero
         * events, so the buffer size this device really has is zero. */
        MEM32(data) = 0;
        RET(DI_OK, 3);
    case DIPROP_GRANULARITY:
        if (g_dev[i].kind != 2) RET(DIERR_UNSUPPORTED, 3); /* keyboard/joystick ranges not claimed */
        if (size != DIPROPDWORD_SIZE || how != DIPH_BYOFFSET)
            RET(DIERR_INVALIDPARAM, 3);
        /* The mouse axes are reported in whole presenter pixels by
         * GetDeviceState. The wheel (Z) is never reported, so it has no
         * granularity to claim. */
        if (obj != DIMOFS_X && obj != DIMOFS_Y) RET(DIERR_UNSUPPORTED, 3);
        MEM32(data) = 1;
        RET(DI_OK, 3);
    case DIPROP_SCANCODE:
        if (g_dev[i].kind != 1) RET(DIERR_UNSUPPORTED, 3); /* mouse has no keys */
        if (size != DIPROPDWORD_SIZE || how != DIPH_BYOFFSET ||
            !obj || obj >= DIK_SIZE) RET(DIERR_INVALIDPARAM, 3);
        /* DIK_* are set-1 make codes; Windows reports extended keys (the 0x80
         * bit in the DIK code) as a 0xE0 prefix followed by the base code. */
        MEM32(data) = (obj & 0x80u) ? ((obj & 0x7fu) << 8) | 0xE0u : obj;
        if (obj == 0xC5u) MEM32(data) = 0x45;             /* DIK_PAUSE */
        RET(DI_OK, 3);
    case DIPROP_INSTANCENAME:
    case DIPROP_PRODUCTNAME:
        if (size != DIPROPSTRING_SIZE || how != DIPH_DEVICE || obj)
            RET(DIERR_INVALIDPARAM, 3);
        prop_string(data, g_dev[i].kind == 2 ? "Mouse"
                         : g_dev[i].kind == 3 ? g_dev[i].name : "Keyboard",
                    g_dev[i].wide);
        RET(DI_OK, 3);
    case DIPROP_JOYSTICKID:
    case DIPROP_GUIDANDPATH:
    case DIPROP_VIDPID:
        RET(DIERR_UNSUPPORTED, 3);  /* no HID path or VID/PID claim, for any device */
    default:
        RET(DIERR_UNSUPPORTED, 3);
    }
}
static void dev_SetProperty(void) {
    RET(DI_OK, 3);                                        /* ranges and dead-zones are hints; polling ignores them */
}
static void dev_GetObjectInfo(void) {
    RET(DIERR_UNSUPPORTED, 4);                            /* no object list to describe (EnumObjects reports none) */
}
/* Write one MAX_PATH name field, staying inside the guest's declared dwSize.
 * A and W name fields are 260 bytes and 260 WCHARs respectively; a short struct
 * gets no write at all rather than a write past its end. */
static void di_info_name(uint32_t info, uint32_t off, uint32_t limit,
                         const char *s, int wide) {
    if (off >= limit) return;
    uint32_t room = limit - off, cap = wide ? room / 2u : room;
    if (!cap) return;
    if (cap > 260) cap = 260;
    size_t len = strlen(s);
    uint32_t n = len < cap - 1u ? (uint32_t)len : cap - 1u;
    if (wide) {
        for (uint32_t k = 0; k < n; k++)
            MEM16(info + off + 2u * k) = (uint16_t)(unsigned char)s[k];
        MEM16(info + off + 2u * n) = 0;
    } else {
        memcpy(hle_ptr(info + off), s, n);
        MEM8(info + off + n) = 0;
    }
}
static void dev_GetDeviceInfo(void) {
    /* DIDEVICEINSTANCE: the GUID and names the device really has. A short
     * dwSize is honoured -- no bytes beyond it are touched -- and guidFFDriver
     * stays zero because this host has no force feedback. */
    uint32_t info = A32(1);
    if (!info) RET(E_POINTER, 2);
    int i = dev_index(A32(0));
    uint32_t size = MEM32(info);
    uint32_t n = size < DIDEVICEINSTANCE_SIZE ? size : DIDEVICEINSTANCE_SIZE;
    if (n > 4) memset(hle_ptr(info + 4), 0, n - 4);
    if (i >= 0) {
        const din_device *d = &g_dev[i];
        const uint8_t *g;
        const char *name;
        uint32_t type, subtype;
        if (d->kind == 2) {
            g = GUID_SYS_MOUSE; name = "Mouse";
            type = d->di8 ? DI8DEVTYPE_MOUSE : DIDEVTYPE_MOUSE;
            subtype = DI8DEVTYPEMOUSE_TRADITIONAL;
        } else if (d->kind == 3) {
            g = d->inst_guid; name = d->name;
            type = d->di8 ? DI8DEVTYPE_JOYSTICK : DIDEVTYPE_JOYSTICK;
            subtype = DIJOY_SUBTYPE;
        } else {
            g = GUID_SYS_KEYBOARD; name = "Keyboard";
            type = d->di8 ? DI8DEVTYPE_KEYBOARD : DIDEVTYPE_KEYBOARD;
            subtype = DI8DEVTYPEKEYBOARD_PCENH;
        }
        if (n >= 20) memcpy(hle_ptr(info + 4), g, 16);
        if (n >= 40) MEM32(info + DIDEVICEINSTANCE_DEVTYPE) = type | (subtype << 8);
        di_info_name(info, DIDEVICEINSTANCE_NAME, n, name, d->wide);
        di_info_name(info, d->wide ? DIDEVICEINSTANCE_W_PRODUCT
                                   : DIDEVICEINSTANCE_PRODUCT, n, name, d->wide);
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
    if (g_dev[i].joy) { SDL_JoystickClose(g_dev[i].joy); g_dev[i].joy = NULL; }
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
    if (g_dev[i].joy) SDL_JoystickUpdate();
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
    RET(make_device(A32(1), g_vt_dev, A32(2),
                    (int)MEM32(A32(0) + DI_HOST_UNICODE)), 4);
}
/* Which legacy/DI8 filter wants a device with these two type codes? Keyboard
 * and mouse use the same numbers in both number spaces; joystick is 4 (legacy)
 * or 0x14 (DI8), and the game-control class selects it. */
static int di_legacy_want(uint32_t devtype, uint32_t legacy, uint32_t di8) {
    if (!devtype) return 1;
    if (devtype <= DIDEVTYPE_JOYSTICK) return devtype == legacy;
    if (devtype <= DI8DEVCLASS_GAMECTRL)
        return devtype == DI8DEVCLASS_ALL ||
               (legacy == DIDEVTYPE_KEYBOARD && devtype == DI8DEVCLASS_KEYBOARD) ||
               (legacy == DIDEVTYPE_MOUSE && devtype == DI8DEVCLASS_POINTER) ||
               (legacy == DIDEVTYPE_JOYSTICK && devtype == DI8DEVCLASS_GAMECTRL);
    if (devtype >= DI8DEVTYPE_DEVICE && devtype <= DI8DEVTYPE_SUPPLEMENTAL)
        return devtype == di8;
    return 0;
}
/* Fill one DIDEVICEINSTANCE_DX3A/W in scratch and invoke a two-arg guest
 * callback. A/W name fields are capped at MAX_PATH by di_info_name, so a long
 * SDL joystick name cannot run past the instance the guest is handed. */
static uint32_t di_legacy_enum_one(uint32_t cb, uint32_t ctx, const uint8_t *g,
                                   const char *name, uint32_t type, uint32_t subtype,
                                   int wide) {
    uint32_t size = wide ? DIDEVICEINSTANCE_DX3_W_SIZE : DIDEVICEINSTANCE_DX3_SIZE;
    uint32_t inst = hle_scratch(size);
    memset(hle_ptr(inst), 0, size);
    MEM32(inst) = size;
    memcpy(hle_ptr(inst + 4), g, 16);                 /* guidInstance */
    memcpy(hle_ptr(inst + 20), g, 16);                /* guidProduct */
    MEM32(inst + DIDEVICEINSTANCE_DX3_DEVTYPE) = type | (subtype << 8);
    di_info_name(inst, DIDEVICEINSTANCE_DX3_NAME, size, name, wide);
    di_info_name(inst, wide ? DIDEVICEINSTANCE_DX3_W_PRODUCT
                            : DIDEVICEINSTANCE_DX3_PRODUCT, size, name, wide);
    uint32_t args[2] = { inst, ctx };
    return hle_call_guest(cb, 2, args);
}
static void di_EnumDevices(void) {
    /* (this, devType, callback, ctx, flags).
     *
     * The guest callback is LPDIENUMDEVICESCALLBACK: exactly TWO stdcall args
     * (instance, context). A third argument left an extra dword on the guest
     * stack for a real callback to clean up, and the old 160-byte instance put
     * the names where guidProduct and dwDevType belong. Fill the real
     * DIDEVICEINSTANCE_DX3A/W (560 ANSI, 1080 wide) and push two args.
     *
     * devType accepts the legacy DIDEVTYPE_* codes as well as the DI8 class
     * and type codes that newer callers pass through this same vtable; both
     * number spaces agree for keyboard and mouse (2 = mouse/pointer, 3 =
     * keyboard), while a joystick is 4 / 0x14. Force-feedback-only enumeration
     * finds no device: none of them has a guidFFDriver. */
    uint32_t devtype = A32(1), cb = A32(2), ctx = A32(3), flags = A32(4);
    if (!cb) RET(DIERR_INVALIDPARAM, 5);
    int wide = (int)MEM32(A32(0) + DI_HOST_UNICODE);
    if (flags & DIEDFL_FORCEFEEDBACK) RET(DI_OK, 5);

    uint32_t mark = hle_scratch_mark();
    int stopped = 0;
    for (int k = 1; k <= 2 && !stopped; k++) {            /* 1 = keyboard, 2 = mouse */
        uint32_t legacy = k == 1 ? DIDEVTYPE_KEYBOARD : DIDEVTYPE_MOUSE;
        uint32_t di8 = k == 1 ? DI8DEVTYPE_KEYBOARD : DI8DEVTYPE_MOUSE;
        if (!di_legacy_want(devtype, legacy, di8)) continue;
        const uint8_t *g = k == 2 ? GUID_SYS_MOUSE : GUID_SYS_KEYBOARD;
        uint32_t subtype = k == 2 ? DIDEVTYPEMOUSE_TRADITIONAL : DIDEVTYPEKEYBOARD_PCENH;
        stopped = di_legacy_enum_one(cb, ctx, g, k == 2 ? "Mouse" : "Keyboard",
                                     legacy, subtype, wide) == DIENUM_STOP;
    }
    /* Joysticks exist only as long as SDL enumerates them. No stick means no
     * callback at all, which is the honest no-device path a DX7 game expects. */
    if (!stopped && di_legacy_want(devtype, DIDEVTYPE_JOYSTICK, DI8DEVTYPE_JOYSTICK)) {
        int n = di_joy_count();
        for (int j = 0; j < n && !stopped; j++) {
            uint8_t guid[16];
            char name[64];
            di_joy_guid(guid, j);
            di_joy_name(j, name);
            stopped = di_legacy_enum_one(cb, ctx, guid, name,
                                         DIDEVTYPE_JOYSTICK, DIJOY_SUBTYPE, wide) == DIENUM_STOP;
        }
    }
    hle_scratch_release(mark);
    RET(DI_OK, 5);
}
static void di_GetDeviceStatus(void) {
    uint32_t guid = A32(1);
    if (!guid) RET(DIERR_INVALIDPARAM, 2);
    const uint8_t *g = hle_ptr(guid);
    int known = memcmp(g, GUID_SYS_KEYBOARD, 16) == 0 || memcmp(g, GUID_SYS_MOUSE, 16) == 0 ||
                di_joy_index(g) >= 0;
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
    RET(make_device(A32(1), g_vt_dev, A32(3),
                    (int)MEM32(A32(0) + DI_HOST_UNICODE)), 5);
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
    RET(make_device(A32(1), g_vt_dev8, A32(2),
                    (int)MEM32(A32(0) + DI8_HOST_UNICODE)), 4);
}
static void di8_GetDeviceStatus(void) {
    uint32_t guid = A32(1);
    if (!guid) RET(E_POINTER, 2);
    const uint8_t *g = hle_ptr(guid);
    int known = memcmp(g, GUID_SYS_KEYBOARD, 16) == 0 || memcmp(g, GUID_SYS_MOUSE, 16) == 0 ||
                di_joy_index(g) >= 0;
    RET(known ? DI_OK : DI_NOTATTACHED, 2);
}
static void di8_RunControlPanel(void) {
    RET(DIERR_UNSUPPORTED, 3);                            /* no control panel on this host */
}

/* Fill one DIDEVICEINSTANCEA/W and invoke a guest callback. The callback takes
 * exactly two stdcall args (instance, context); dwDevType is a field of the
 * instance, not a third argument. DI8 instances report DI8DEVTYPE_* (mouse
 * 0x12, keyboard 0x13, joystick 0x14) with the subtype in the high byte, and a
 * zero guidFFDriver because no device here has force feedback. */
static uint32_t di8_enum_one(uint32_t cb, uint32_t ctx, const uint8_t *g,
                             const char *name, uint32_t devtype,
                             int wide, uint32_t filter) {
    uint32_t size = wide ? DIDEVICEINSTANCE_W_SIZE : DIDEVICEINSTANCE_SIZE;
    uint32_t inst = hle_scratch(size);
    memset(hle_ptr(inst), 0, size);
    MEM32(inst) = size;
    memcpy(hle_ptr(inst + 4), g, 16);
    memcpy(hle_ptr(inst + 20), g, 16);
    MEM32(inst + DIDEVICEINSTANCE_DEVTYPE) = devtype;
    di_info_name(inst, DIDEVICEINSTANCE_NAME, size, name, wide);
    di_info_name(inst, wide ? DIDEVICEINSTANCE_W_PRODUCT
                            : DIDEVICEINSTANCE_PRODUCT, size, name, wide);
    /* A specific DI8DEVTYPE_* filter is checked against the low byte, as in
     * Wine's try_enum_device. Class filters are handled by the caller. */
    if (filter > DI8DEVCLASS_GAMECTRL && (devtype & 0xffu) != filter) return DIENUM_CONTINUE;
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
    /* A caller asking only for force-feedback devices gets none: every device
     * here reports a null guidFFDriver. */
    if (flags & DIEDFL_FORCEFEEDBACK) RET(DI_OK, 5);

    int want_mouse = 0, want_kbd = 0, want_joy = 0;
    if (type <= DI8DEVCLASS_GAMECTRL) {                  /* a device *class* */
        want_mouse = (type == DI8DEVCLASS_ALL || type == DI8DEVCLASS_POINTER);
        want_kbd   = (type == DI8DEVCLASS_ALL || type == DI8DEVCLASS_KEYBOARD);
        want_joy   = (type == DI8DEVCLASS_ALL || type == DI8DEVCLASS_GAMECTRL);
    } else {                                             /* a specific type */
        want_mouse = (type == DI8DEVTYPE_MOUSE);
        want_kbd   = (type == DI8DEVTYPE_KEYBOARD);
        want_joy   = (type == DI8DEVTYPE_JOYSTICK);
    }
    int wide = (int)MEM32(A32(0) + DI8_HOST_UNICODE);
    uint32_t mark = hle_scratch_mark();
    int stopped = 0;
    if (want_mouse)
        stopped = di8_enum_one(cb, ctx, GUID_SYS_MOUSE, "Mouse",
                  DI8DEVTYPE_MOUSE | (DI8DEVTYPEMOUSE_TRADITIONAL << 8), wide, type) != DIENUM_CONTINUE;
    if (!stopped && want_kbd)
        stopped = di8_enum_one(cb, ctx, GUID_SYS_KEYBOARD, "Keyboard",
                  DI8DEVTYPE_KEYBOARD | (DI8DEVTYPEKEYBOARD_PCENH << 8), wide, type) != DIENUM_CONTINUE;
    /* Only SDL's joysticks are enumerated; with none connected the callback is
     * never invoked for a game-controller filter. */
    if (!stopped && want_joy) {
        int n = di_joy_count();
        for (int j = 0; j < n && !stopped; j++) {
            uint8_t guid[16];
            char name[64];
            di_joy_guid(guid, j);
            di_joy_name(j, name);
            stopped = di8_enum_one(cb, ctx, guid, name,
                      DI8DEVTYPE_JOYSTICK | (DIJOY_SUBTYPE << 8), wide, type) != DIENUM_CONTINUE;
        }
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
    int joy = di_joy_index(g);
    if (memcmp(g, GUID_SYS_KEYBOARD, 16) && memcmp(g, GUID_SYS_MOUSE, 16) && joy < 0)
        RET(DIERR_DEVICENOTREG, 4);
    /* A joystick has one concrete instance GUID derived from SDL; the generic
     * GUID_Joystick resolves to the first one, never to an invented GUID. */
    if (joy >= 0) di_joy_guid((uint8_t *)hle_ptr(out), joy);
    else memcpy(hle_ptr(out), g, 16);
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
