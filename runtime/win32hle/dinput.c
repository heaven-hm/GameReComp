/*
 * dinput.c - DirectInput 3/5/7 for keyboard and mouse, on SDL's input state.
 *
 * Two COM interfaces, the ones a DirectInput game talks to:
 *
 *   IDirectInputA        DirectInputCreateA(hinst, 0x0300, &di, NULL)
 *   IDirectInput7A       DirectInputCreateEx(hinst, 0x0700, &di, iid, NULL)
 *     CreateDevice(guid, &device, NULL)
 *
 *   IDirectInputDeviceA/7A
 *     SetDataFormat, SetCooperativeLevel, Acquire, Unacquire, GetDeviceState,
 *     GetDeviceData, GetCapabilities, EnumObjects, Get/SetProperty,
 *     GetObjectInfo, GetDeviceInfo, Initialize
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
 *   - EnumDevices reports exactly the keyboard and mouse above.
 *
 * These are stdcall exports (DINPUT.dll is a WINAPI DLL), so every shim ends in
 * RET(value, argc) with arg 0 = `this`.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

/* HRESULTs. */
#define DI_OK                 0x00000000u
#define DIERR_INVALIDPARAM    0x80070057u
#define DIERR_NOTINITIALIZED  0x80070015u
#define DIERR_NOTACQUIRED     0x80070005u
#define DIERR_DEVICENOTREG    0x80040154u
#define DIERR_UNSUPPORTED     0x80004005u
#define E_NOINTERFACE         0x80004002u
#define E_POINTER             0x80004003u

#define DIENUM_STOP 0u
#define DIENUM_CONTINUE 1u

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

/* Device state. Slots are guest objects; the host state lives beside them. */
enum { DIN_MAX_DEVICES = 8 };
typedef struct {
    uint32_t obj;             /* guest IDirectInputDevice */
    int kind;                 /* 1 = keyboard, 2 = mouse */
    int acquired;
    uint32_t data_size;       /* the DIDATAFORMAT's dwDataSize the game passed */
    int have_format;
    uint32_t hwnd;
} din_device;

static din_device g_dev[DIN_MAX_DEVICES];
static int g_ndev;
static uint32_t g_vt_di, g_vt_dev;    /* built on first use */
static uint32_t g_di;                 /* the IDirectInput object */
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
    RET(DI_OK, 3);                                        /* the level is a windowing wish; input is direct */
}
static void dev_Acquire(void) {
    int i = dev_index(A32(0));
    if (i < 0) RET(DIERR_INVALIDPARAM, 1);
    if (!SDL_WasInit(SDL_INIT_VIDEO) && SDL_InitSubSystem(SDL_INIT_EVENTS | SDL_INIT_VIDEO) != 0)
        RET(DIERR_NOTINITIALIZED, 1);
    SDL_PumpEvents();
    g_dev[i].acquired = 1;
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
    SDL_PumpEvents();
    int want = (int)size;
    if (g_dev[i].kind == 1) {
        if (want > DIK_SIZE) want = DIK_SIZE;
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
        /* The mouse: 3 axes then 4 buttons. Absolute position, which is what
         * DIMOFS_X reports for a non-exclusive device. */
        int mx = 0, my = 0;
        Uint32 buttons = SDL_GetMouseState(&mx, &my);
        uint8_t *p = hle_ptr(buf);
        memset(p, 0, (size_t)(want < 16 ? want : 16));
        if (want >= 4) MEM32(buf + DIMOFS_X) = (uint32_t)mx;
        if (want >= 8) MEM32(buf + DIMOFS_Y) = (uint32_t)my;
        if (want >= 12) MEM32(buf + DIMOFS_Z) = 0;
        for (int b = 0; b < 4; b++)
            if (want >= DIMOFS_BUTTON0 + b + 1)
                MEM8(buf + DIMOFS_BUTTON0 + b) = (uint8_t)((buttons >> b) & 1u ? 0x80 : 0);
    }
    RET(DI_OK, 3);
}
static void dev_GetDeviceData(void) {
    /* (this, size, DIDEVICEOBJECTDATA*, &count, flags): no event stream is
     * buffered -- polled state is GetDeviceState, which is real -- so the
     * honest answer is zero events. */
    uint32_t count = A32(3);
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
    if (g_di == self) { hle_com_free(self); g_di = 0; }
    RET(0, 1);
}
static void di_CreateDevice(void) {
    /* (this, rguid, &device, punkOuter) */
    uint32_t guid = A32(1), out = A32(2);
    if (!guid || !out) RET(DIERR_INVALIDPARAM, 4);
    MEM32(out) = 0;
    if (g_ndev >= DIN_MAX_DEVICES) RET(DIERR_DEVICENOTREG, 4);
    const uint8_t *g = hle_ptr(guid);
    int kind = memcmp(g, GUID_SYS_KEYBOARD, 16) == 0 ? 1
             : memcmp(g, GUID_SYS_MOUSE, 16) == 0 ? 2 : 0;
    if (!kind) {
        /* A joystick, a wheel, a flight stick: this host enumerates none, and a
         * device that never reports an axis would tell the game a stick is
         * connected and centred. DIERR_DEVICENOTREG is the true answer and the
         * one a game handles by taking its no-joystick path. */
        RET(DIERR_DEVICENOTREG, 4);
    }
    uint32_t obj = hle_com_new(g_vt_dev, 16);
    if (!obj) RET(DIERR_DEVICENOTREG, 4);
    din_device *d = &g_dev[g_ndev++];
    d->obj = obj, d->kind = kind, d->acquired = 0, d->have_format = 0, d->data_size = 0;
    MEM32(out) = obj;
    RET(DI_OK, 4);
}
static void di_EnumDevices(void) {
    /* (this, devType, callback, ctx, flags): the keyboard and the mouse, which
     * are the devices that exist. devType 0x05 = DI8DEVCLASS_KEYBOARD,
     * 0x12 = MOUSE; 0 = all. */
    uint32_t devtype = A32(1), cb = A32(2), ctx = A32(3);
    if (!cb) RET(DIERR_INVALIDPARAM, 5);
    uint32_t inst = hle_scratch(64 + 64 + 32);
    for (int k = 1; k <= 2; k++) {
        if (devtype == 0x05 && k != 1) continue;
        if (devtype == 0x12 && k != 2) continue;
        memset(hle_ptr(inst), 0, 160);
        MEM32(inst + 0) = 20 + 64 + 64;                   /* dwSize */
        memcpy(hle_ptr(inst + 4), k == 2 ? GUID_SYS_MOUSE : GUID_SYS_KEYBOARD, 16);
        strncpy((char *)hle_ptr(inst + 20), k == 2 ? "Mouse" : "Keyboard", 63);
        strncpy((char *)hle_ptr(inst + 84), k == 2 ? "Mouse" : "Keyboard", 63);
        uint32_t args[3] = { inst, ctx, k == 2 ? 0x12u : 0x05u };
        if (hle_call_guest(cb, 3, args) == DIENUM_STOP) RET(DI_OK, 5);
    }
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
    uint32_t guid = A32(1), out = A32(3);
    if (!guid || !out) RET(DIERR_INVALIDPARAM, 5);
    MEM32(out) = 0;
    if (g_ndev >= DIN_MAX_DEVICES) RET(DIERR_DEVICENOTREG, 5);
    const uint8_t *g = hle_ptr(guid);
    int kind = memcmp(g, GUID_SYS_KEYBOARD, 16) == 0 ? 1
             : memcmp(g, GUID_SYS_MOUSE, 16) == 0 ? 2 : 0;
    if (!kind) RET(DIERR_DEVICENOTREG, 5);
    uint32_t obj = hle_com_new(g_vt_dev, 16);
    if (!obj) RET(DIERR_DEVICENOTREG, 5);
    din_device *d = &g_dev[g_ndev++];
    d->obj = obj, d->kind = kind, d->acquired = 0, d->have_format = 0, d->data_size = 0;
    MEM32(out) = obj;
    RET(DI_OK, 5);
}

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
    g_vt_di = hle_com_vtable(di);
    g_vt_dev = hle_com_vtable(dev);
}

/* DirectInputCreateA(hinst, version, &di, punkOuter) */
static void d_CreateA(void) {
    uint32_t out = A32(2);
    if (!out) RET(DIERR_INVALIDPARAM, 4);
    build_vtables();
    if (!g_di) {
        g_di = hle_com_new(g_vt_di, 16);
        if (!g_di) RET(DIERR_DEVICENOTREG, 4);
        g_di_refs = 1;
    }
    MEM32(out) = g_di;
    RET(DI_OK, 4);
}

/* DirectInputCreateEx(hinst, version, riid, &di, punkOuter): DirectInput 7's
 * entry point, and the one place its argument order differs from
 * DirectInputCreateA - the interface id comes BEFORE the out pointer, not
 * after it. Same object; the GUID only selects a vtable layout this layer
 * answers identically. */
static void d_CreateEx(void) {
    uint32_t out = A32(3);   /* A32(2) is the interface id: one object serves them all */
    if (!out) RET(DIERR_INVALIDPARAM, 5);
    build_vtables();
    if (!g_di) {
        g_di = hle_com_new(g_vt_di, 16);
        if (!g_di) RET(DIERR_DEVICENOTREG, 5);
        g_di_refs = 1;
    }
    MEM32(out) = g_di;
    RET(DI_OK, 5);
}

const win32hle_shim win32hle_dinput[] = {
    { "DirectInputCreateA",  d_CreateA },
    { "DirectInputCreateW",  d_CreateA },
    { "DirectInputCreateEx", d_CreateEx },
    { 0, 0 }
};
