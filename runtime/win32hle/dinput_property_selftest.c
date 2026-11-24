/* dinput_property_selftest.c - IDirectInputDevice GetProperty ABI.
 *
 * Drives slot 5 of both the DX3/5/7 and the DI8 device vtables the way a guest
 * does. DIPROP_* are MAKEDIPROP integers (1..26), not GUID addresses, so a
 * real pointer must be ignored instead of dereferenced. Every call is made
 * with a synthetic guest stack so the stdcall purge is counted, not inferred,
 * and every DIPROPHEADER sits in a larger guest block filled with a sentinel:
 * a short or wrong-shaped header must be rejected without writing a byte past
 * the dwSize the guest declared.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#define DI_OK_VALUE 0u
#define DIERR_INVALIDPARAM_VALUE 0x80070057u
#define DIERR_UNSUPPORTED_VALUE 0x80004001u

enum { DIPROP_BUFFERSIZE = 1, DIPROP_GRANULARITY = 3,
       DIPROP_GUIDANDPATH = 12, DIPROP_INSTANCENAME = 13,
       DIPROP_PRODUCTNAME = 14, DIPROP_JOYSTICKID = 15,
       DIPROP_SCANCODE = 23, DIPROP_VIDPID = 24 };
enum { DIPH_DEVICE = 0, DIPH_BYOFFSET = 1, DIPROPDWORD_SIZE = 20,
       DIPROPSTRING_SIZE = 536, ALLOC = 600, SENTINEL = 0xA5u };

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

/* Call a shim with a synthetic guest stack and count the words it pops.
 * hle_call_guest saves/restores the register file and would hide a bad purge. */
static uint32_t invoke(uint32_t fn, int argc, const uint32_t *args, const char *what) {
    recomp_func_t f = fn ? recomp_lookup_import(fn) : 0;
    if (!f) { ok(what, 0); return 0xFFFFFFFFu; }
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t scratch = hle_va(hle_alloc(64));            /* ret + up to 7 args */
    g_esp = scratch + 32;
    for (int i = argc - 1; i >= 0; --i) { g_esp -= 4; MEM32(g_esp) = args[i]; }
    g_esp -= 4; MEM32(g_esp) = RECOMP_RETADDR;
    uint32_t esp_in = g_esp;
    f();
    uint32_t words = (g_esp - esp_in) / 4u;
    uint32_t r = g_eax;                                  /* not `eax`: that is a macro for g_eax */
    g_esp = saved_esp; g_eax = saved_eax;
    ok(what, words == (uint32_t)argc + 1u);              /* ret + exactly argc */
    return r;
}

/* The guest's DIPROPHEADER plus room past dwSize for the guard check. */
static void prop_reset(uint32_t va, uint32_t size, uint32_t how, uint32_t obj) {
    memset(hle_ptr(va), SENTINEL, ALLOC);
    MEM32(va) = size; MEM32(va + 4) = 16; MEM32(va + 8) = obj; MEM32(va + 12) = how;
}
static uint32_t prop_new(uint32_t size, uint32_t how, uint32_t obj) {
    uint32_t va = hle_va(hle_alloc(ALLOC));
    prop_reset(va, size, how, obj);
    return va;
}
static int untouched(uint32_t va, uint32_t from) {
    const uint8_t *p = hle_ptr(va);
    for (uint32_t k = from; k < ALLOC; k++) if (p[k] != SENTINEL) return 0;
    return 1;
}
static uint32_t query(uint32_t dev, uint32_t prop, uint32_t hdr, const char *what) {
    return invoke(slot(dev, 5), 3, (uint32_t[]){ dev, prop, hdr }, what);
}

static const uint8_t iid_di8_a[16] = {
    0x30,0x80,0x79,0xBF,0x3A,0x48,0xA2,0x4D,0xAA,0x99,0x5D,0x64,0xED,0x36,0x97,0x00 };
static const uint8_t iid_di8_w[16] = {
    0x31,0x80,0x79,0xBF,0x3A,0x48,0xA2,0x4D,0xAA,0x99,0x5D,0x64,0xED,0x36,0x97,0x00 };
static const uint8_t mouse_guid[16] = {
    0x60,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,0xBF,0xC7,0x44,0x45,0x53,0x54,0,0 };
static const uint8_t keyboard_guid[16] = {
    0x61,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,0xBF,0xC7,0x44,0x45,0x53,0x54,0,0 };

static uint32_t create8(uint32_t iid_va, const char *what) {
    uint32_t create = hle_resolve("DirectInput8Create"), out = hle_va(hle_alloc(4));
    uint32_t r = invoke(create, 5,
        (uint32_t[]){ 0x1234u, 0x0800u, iid_va, out, 0 }, what);
    ok("DirectInput8Create -> DI_OK", r == DI_OK_VALUE);
    return MEM32(out);
}
static uint32_t create_device(uint32_t di, uint32_t guid, const char *what) {
    uint32_t out = hle_va(hle_alloc(4));
    uint32_t r = invoke(slot(di, 3), 4, (uint32_t[]){ di, guid, out, 0 }, what);
    ok(what, r == DI_OK_VALUE && MEM32(out) != 0);
    return MEM32(out);
}

int main(void) {
    win32hle_register(win32hle_dinput);
    mach_enter();

    uint32_t guid_mouse = guest_bytes(mouse_guid, 16), guid_kbd = guest_bytes(keyboard_guid, 16);
    uint32_t di = create8(guest_bytes(iid_di8_a, 16), "DirectInput8Create A pops five args");
    uint32_t mouse = create_device(di, guid_mouse, "CreateDevice(mouse) pops four args");
    uint32_t kbd = create_device(di, guid_kbd, "CreateDevice(keyboard) pops four args");
    if (!di || !mouse || !kbd) return 1;

    /* A real GUID pointer is not a MAKEDIPROP integer: Wine answers DI_OK and
     * writes nothing. The old shim dereferenced A32(1) as a property "type". */
    uint32_t hdr = prop_new(DIPROPDWORD_SIZE, DIPH_DEVICE, 0);
    ok("real GUID pointer -> DI_OK, no write",
       query(mouse, 0x00810000u, hdr, "GetProperty pops three args") == DI_OK_VALUE &&
       untouched(hdr, 16));
    ok("null header -> DIERR_INVALIDPARAM",
       invoke(slot(mouse, 5), 3, (uint32_t[]){ mouse, DIPROP_BUFFERSIZE, 0 },
              "GetProperty(null header) pops three args") == DIERR_INVALIDPARAM_VALUE);

    MEM32(hdr + 4) = 12;                                 /* wrong dwHeaderSize */
    ok("bad dwHeaderSize -> DIERR_INVALIDPARAM, no write",
       query(mouse, DIPROP_BUFFERSIZE, hdr, "GetProperty(bad header size)") == DIERR_INVALIDPARAM_VALUE &&
       untouched(hdr, 16));
    MEM32(hdr + 4) = 16;
    MEM32(hdr) = 8;                                      /* shorter than DIPROPHEADER */
    ok("dwSize < DIPROPHEADER -> DIERR_INVALIDPARAM, no write",
       query(mouse, DIPROP_BUFFERSIZE, hdr, "GetProperty(short header)") == DIERR_INVALIDPARAM_VALUE &&
       untouched(hdr, 16));

    /* BUFFERSIZE is the queue this host really has: none. GetDeviceData
     * reports zero events, so zero is the truthful answer. */
    MEM32(hdr) = DIPROPDWORD_SIZE;
    ok("BUFFERSIZE -> DI_OK, value 0",
       query(mouse, DIPROP_BUFFERSIZE, hdr, "GetProperty(BUFFERSIZE)") == DI_OK_VALUE &&
       MEM32(hdr + 16) == 0 && untouched(hdr, DIPROPDWORD_SIZE));
    prop_reset(hdr, 16, DIPH_DEVICE, 0);
    ok("short DIPROPDWORD -> DIERR_INVALIDPARAM, no write",
       query(mouse, DIPROP_BUFFERSIZE, hdr, "GetProperty(short DIPROPDWORD)") == DIERR_INVALIDPARAM_VALUE &&
       untouched(hdr, 16));
    prop_reset(hdr, DIPROPDWORD_SIZE, DIPH_BYOFFSET, 0);
    ok("BUFFERSIZE by offset -> DIERR_INVALIDPARAM, no write",
       query(mouse, DIPROP_BUFFERSIZE, hdr, "GetProperty(BUFFERSIZE by offset)") == DIERR_INVALIDPARAM_VALUE &&
       untouched(hdr, 16));

    /* Granularity comes from the units GetDeviceState actually writes. */
    hdr = prop_new(DIPROPDWORD_SIZE, DIPH_BYOFFSET, 0);  /* DIMOFS_X */
    ok("mouse X granularity -> 1", query(mouse, DIPROP_GRANULARITY, hdr,
       "GetProperty(GRANULARITY X)") == DI_OK_VALUE && MEM32(hdr + 16) == 1);
    MEM32(hdr + 8) = 4;                                  /* DIMOFS_Y */
    ok("mouse Y granularity -> 1", query(mouse, DIPROP_GRANULARITY, hdr,
       "GetProperty(GRANULARITY Y)") == DI_OK_VALUE && MEM32(hdr + 16) == 1);
    prop_reset(hdr, DIPROPDWORD_SIZE, DIPH_BYOFFSET, 8);   /* DIMOFS_Z: never reported */
    ok("mouse wheel granularity -> unsupported", query(mouse, DIPROP_GRANULARITY, hdr,
       "GetProperty(GRANULARITY Z)") == DIERR_UNSUPPORTED_VALUE && untouched(hdr, 16));
    MEM32(hdr + 8) = 0x100u;                             /* no such object */
    ok("bad mouse object -> unsupported", query(mouse, DIPROP_GRANULARITY, hdr,
       "GetProperty(GRANULARITY bad offset)") == DIERR_UNSUPPORTED_VALUE);
    MEM32(hdr + 12) = DIPH_DEVICE;
    ok("GRANULARITY by device -> DIERR_INVALIDPARAM",
       query(mouse, DIPROP_GRANULARITY, hdr, "GetProperty(GRANULARITY by device)") == DIERR_INVALIDPARAM_VALUE);
    ok("keyboard has no axes -> unsupported", query(kbd, DIPROP_GRANULARITY, hdr,
       "GetProperty(keyboard GRANULARITY)") == DIERR_UNSUPPORTED_VALUE);

    /* Key scan codes are the set-1 make codes already used by the 256-byte
     * keyboard state, with Windows' 0xE0 prefix for extended keys. */
    hdr = prop_new(DIPROPDWORD_SIZE, DIPH_BYOFFSET, 0x1E); /* DIK_A */
    ok("key scan code 0x1E -> 0x1E", query(kbd, DIPROP_SCANCODE, hdr,
       "GetProperty(SCANCODE A)") == DI_OK_VALUE && MEM32(hdr + 16) == 0x1Eu);
    MEM32(hdr + 8) = 0xCB;                               /* DIK_LEFT, extended */
    ok("extended scan code 0xCB -> 0x4BE0", query(kbd, DIPROP_SCANCODE, hdr,
       "GetProperty(SCANCODE left)") == DI_OK_VALUE && MEM32(hdr + 16) == 0x4BE0u);
    MEM32(hdr + 8) = 0xC5;                               /* DIK_PAUSE */
    ok("DIK_PAUSE -> scan code 0x45", query(kbd, DIPROP_SCANCODE, hdr,
       "GetProperty(SCANCODE pause)") == DI_OK_VALUE && MEM32(hdr + 16) == 0x45u);
    prop_reset(hdr, DIPROPDWORD_SIZE, DIPH_BYOFFSET, 0);
    ok("object 0 -> DIERR_INVALIDPARAM, no write", query(kbd, DIPROP_SCANCODE, hdr,
       "GetProperty(SCANCODE object 0)") == DIERR_INVALIDPARAM_VALUE && untouched(hdr, 16));
    ok("mouse has no keys -> unsupported", query(mouse, DIPROP_SCANCODE, hdr,
       "GetProperty(mouse SCANCODE)") == DIERR_UNSUPPORTED_VALUE);

    /* DIPROPSTRING: the full 536-byte struct is required and the guard past
     * dwSize stays untouched. The A/W flag comes from the creating facade. */
    hdr = prop_new(DIPROPSTRING_SIZE, DIPH_DEVICE, 0);
    ok("ANSI instance name -> Mouse", query(mouse, DIPROP_INSTANCENAME, hdr,
       "GetProperty(INSTANCENAME A)") == DI_OK_VALUE &&
       !strcmp((char *)hle_ptr(hdr + 16), "Mouse") && untouched(hdr, DIPROPSTRING_SIZE));
    ok("ANSI product name -> Keyboard", query(kbd, DIPROP_PRODUCTNAME, hdr,
       "GetProperty(PRODUCTNAME A)") == DI_OK_VALUE &&
       !strcmp((char *)hle_ptr(hdr + 16), "Keyboard") && untouched(hdr, DIPROPSTRING_SIZE));
    prop_reset(hdr, DIPROPDWORD_SIZE, DIPH_DEVICE, 0);
    ok("short DIPROPSTRING -> DIERR_INVALIDPARAM, no write",
       query(mouse, DIPROP_INSTANCENAME, hdr, "GetProperty(short DIPROPSTRING)") == DIERR_INVALIDPARAM_VALUE &&
       untouched(hdr, 16));
    prop_reset(hdr, DIPROPSTRING_SIZE, DIPH_BYOFFSET, 0);
    ok("INSTANCENAME by offset -> DIERR_INVALIDPARAM, no write",
       query(mouse, DIPROP_INSTANCENAME, hdr, "GetProperty(INSTANCENAME by offset)") == DIERR_INVALIDPARAM_VALUE &&
       untouched(hdr, 16));

    uint32_t diw = create8(guest_bytes(iid_di8_w, 16), "DirectInput8Create W pops five args");
    uint32_t kbdw = create_device(diw, guid_kbd, "W CreateDevice pops four args");
    hdr = prop_new(DIPROPSTRING_SIZE, DIPH_DEVICE, 0);
    ok("wide instance name is UTF-16", query(kbdw, DIPROP_INSTANCENAME, hdr,
       "GetProperty(INSTANCENAME W)") == DI_OK_VALUE &&
       MEM16(hdr + 16) == 'K' && MEM16(hdr + 18) == 'e' && MEM16(hdr + 20) == 'y' &&
       MEM16(hdr + 22) == 'b' && MEM16(hdr + 24) == 'o' && MEM16(hdr + 26) == 'a' &&
       MEM16(hdr + 28) == 'r' && MEM16(hdr + 30) == 'd' && MEM16(hdr + 32) == 0 &&
       untouched(hdr, DIPROPSTRING_SIZE));

    /* Properties this host cannot answer truthfully say so. */
    hdr = prop_new(DIPROPDWORD_SIZE, DIPH_DEVICE, 0);
    ok("JOYSTICKID -> unsupported", query(mouse, DIPROP_JOYSTICKID, hdr,
       "GetProperty(JOYSTICKID)") == DIERR_UNSUPPORTED_VALUE && untouched(hdr, 16));
    ok("GUIDANDPATH -> unsupported", query(mouse, DIPROP_GUIDANDPATH, hdr,
       "GetProperty(GUIDANDPATH)") == DIERR_UNSUPPORTED_VALUE && untouched(hdr, 16));
    ok("VIDPID -> unsupported", query(mouse, DIPROP_VIDPID, hdr,
       "GetProperty(VIDPID)") == DIERR_UNSUPPORTED_VALUE && untouched(hdr, 16));
    ok("unknown DIPROP -> unsupported", query(mouse, 99, hdr,
       "GetProperty(unknown)") == DIERR_UNSUPPORTED_VALUE && untouched(hdr, 16));

    /* The legacy facade shares the same device table and property code. */
    uint32_t create_legacy = hle_resolve("DirectInputCreateA"), out = hle_va(hle_alloc(4));
    ok("DirectInputCreateA pops four args", invoke(create_legacy, 4,
       (uint32_t[]){ 0x1234u, 0x0700u, out, 0 }, "DirectInputCreateA") == DI_OK_VALUE);
    uint32_t di7 = MEM32(out);
    uint32_t kb7 = create_device(di7, guid_kbd, "legacy CreateDevice pops four args");
    hdr = prop_new(DIPROPDWORD_SIZE, DIPH_BYOFFSET, 0x1E);
    ok("legacy keyboard scan code -> 0x1E", query(kb7, DIPROP_SCANCODE, hdr,
       "legacy GetProperty(SCANCODE)") == DI_OK_VALUE && MEM32(hdr + 16) == 0x1Eu);

    ok("release legacy keyboard", invoke(slot(kb7, 2), 1, &kb7, "legacy device Release") == 0);
    ok("release legacy DI", invoke(slot(di7, 2), 1, &di7, "legacy DI Release") == 0);
    ok("release W keyboard", invoke(slot(kbdw, 2), 1, &kbdw, "W device Release") == 0);
    ok("release W DI", invoke(slot(diw, 2), 1, &diw, "W DI Release") == 0);
    ok("release mouse", invoke(slot(mouse, 2), 1, &mouse, "mouse Release") == 0);
    ok("release keyboard", invoke(slot(kbd, 2), 1, &kbd, "keyboard Release") == 0);
    ok("release DI", invoke(slot(di, 2), 1, &di, "DI Release") == 0);

    mach_leave();
    printf("dinput_property_selftest: %s\n", fails ? "FAILED" : "all checks passed");
    return fails != 0;
}
