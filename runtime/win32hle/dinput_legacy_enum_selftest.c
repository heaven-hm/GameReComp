/* dinput_legacy_enum_selftest.c - IDirectInput (DX3/5/7) EnumDevices ABI.
 *
 * The DI8 facade already has its own selftest. This one drives the legacy
 * vtable the way a DX7 game does: a real guest callback (two stdcall args,
 * callee-pops) receiving a DIDEVICEINSTANCE_DX3A/W, with class/type filters
 * and the A/W name encoding selected by DirectInputCreateA/W. It also checks
 * that the guest stack is balanced across the native -> guest -> native call.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

#define CB_VA 0x00401100u
#define DI_OK_VALUE 0u
#define DIERR_INVALIDPARAM_VALUE 0x80070057u
#define DIENUM_STOP 0u
#define DIENUM_CONTINUE 1u

#define DIDEVTYPE_KEYBOARD 3u
#define DI8DEVCLASS_POINTER 2u
#define DI8DEVCLASS_KEYBOARD 3u
#define DI8DEVTYPE_KEYBOARD 0x13u
#define DIEDFL_FORCEFEEDBACK 0x100u

enum { INST_SIZE = 560, DX3_DEVTYPE = 36, DX3_NAME = 40, DX3_PRODUCT = 300,
       MAX_SEEN = 4 };

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { fprintf(stderr, "FAIL %s\n", what); fails++; }
}

/* Fields the synthetic callback copies out of each instance. */
static struct {
    int n;
    uint32_t size[MAX_SEEN], devtype[MAX_SEEN];
    uint8_t guid[MAX_SEEN][16];
    char name[MAX_SEEN][64], product[MAX_SEEN][64];
} seen;

static int stop_after;
static void enum_cb(void) {
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE;
    uint32_t _flag_a = 0, _flag_b = 0; RECOMP_ENTER(CB_VA);
    uint32_t inst = A32(0), ctx = A32(1);
    (void)ctx;
    int i = seen.n < MAX_SEEN ? seen.n++ : MAX_SEEN - 1;
    seen.size[i] = MEM32(inst);
    seen.devtype[i] = MEM32(inst + DX3_DEVTYPE);
    for (int k = 0; k < 16; k++) seen.guid[i][k] = MEM8(inst + 4u + (uint32_t)k);
    /* Copy a fixed window: the early NUL bytes are the A/W distinction. */
    for (int k = 0; k < 16; k++) {
        seen.name[i][k] = (char)MEM8(inst + DX3_NAME + (uint32_t)k);
        seen.product[i][k] = (char)MEM8(inst + DX3_PRODUCT + (uint32_t)k);
    }
    seen.name[i][16] = seen.product[i][16] = 0;
    eax = (stop_after && seen.n >= stop_after) ? DIENUM_STOP : DIENUM_CONTINUE;
    esp += 4u + 8u;                     /* LPDIENUMDEVICESCALLBACK: ret + 2 args */
}

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { CB_VA, enum_cb } };
const uint32_t recomp_dispatch_count = 1;

static const uint8_t kbd_guid[16] = {
    0x61,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,0xBF,0xC7,0x44,0x45,0x53,0x54,0,0 };
static const uint8_t mouse_guid[16] = {
    0x60,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,0xBF,0xC7,0x44,0x45,0x53,0x54,0,0 };

static uint32_t vslot(uint32_t iface, unsigned i) { return MEM32(MEM32(iface) + 4u * i); }
static uint32_t guest_u32(void) { return hle_va(hle_alloc(4)); }

static uint32_t create(const char *name, uint32_t out_va) {
    uint32_t fn = hle_resolve(name);
    ok(name, fn != 0);
    uint32_t args[4] = { 1u, 0x0300u, out_va, 0u };
    mach_enter();
    uint32_t sp = g_esp;
    uint32_t r = hle_call_guest(fn, 4, args);
    ok("Create pops its four stdcall args", g_esp == sp);
    mach_leave();
    ok("Create returns DI_OK", r == DI_OK_VALUE);
    return MEM32(out_va);
}

/* Call slot 4 of the legacy IDirectInput vtable with a guest callback. */
static void enumerate(uint32_t self, uint32_t type, uint32_t flags) {
    uint32_t fn = vslot(self, 4);
    ok("EnumDevices slot is non-null", fn != 0);
    if (!fn) return;
    memset(&seen, 0, sizeof seen);
    uint32_t ctx = 0x1234u;
    uint32_t args[5] = { self, type, CB_VA, ctx, flags };
    mach_enter();
    uint32_t sp = g_esp;
    uint32_t r = hle_call_guest(fn, 5, args);
    ok("EnumDevices pops its five stdcall args", g_esp == sp);
    mach_leave();
    ok("EnumDevices returns DI_OK", r == DI_OK_VALUE);
}

int main(void) {
    win32hle_register(win32hle_dinput);
    uint32_t out_a = guest_u32(), out_w = guest_u32();
    uint32_t di_a = create("DirectInputCreateA", out_a);
    uint32_t di_w = create("DirectInputCreateW", out_w);
    ok("A and W get distinct objects", di_a && di_w && di_a != di_w);
    if (!di_a || !di_w) return 1;

    /* All devices: a well-formed DX3 instance per device, keyboard first. */
    enumerate(di_a, 0, 0);
    ok("all-devices filter visits two devices", seen.n == 2);
    if (seen.n == 2) {
        ok("instance dwSize is DIDEVICEINSTANCE_DX3", seen.size[0] == INST_SIZE && seen.size[1] == INST_SIZE);
        ok("first device is the keyboard", seen.devtype[0] == (3u | (4u << 8)) &&
           !memcmp(seen.guid[0], kbd_guid, 16) && !strcmp(seen.name[0], "Keyboard"));
        ok("second device is the mouse", seen.devtype[1] == (2u | (2u << 8)) &&
           !memcmp(seen.guid[1], mouse_guid, 16) && !strcmp(seen.name[1], "Mouse"));
        ok("product names are filled at their own offset", !strcmp(seen.product[0], "Keyboard") &&
           !strcmp(seen.product[1], "Mouse"));
    }

    /* The legacy DIDEVTYPE_* codes and the DI8 class/type codes both arrive
     * through this vtable; neither may be mistaken for the other. */
    enumerate(di_a, 3, 0);
    ok("DIDEVTYPE_KEYBOARD -> keyboard only", seen.n == 1 && !strcmp(seen.name[0], "Keyboard"));
    enumerate(di_a, DI8DEVCLASS_POINTER, 0);
    ok("DI8DEVCLASS_POINTER -> mouse only", seen.n == 1 && !strcmp(seen.name[0], "Mouse"));
    enumerate(di_a, DI8DEVCLASS_KEYBOARD, 0);
    ok("DI8DEVCLASS_KEYBOARD -> keyboard only", seen.n == 1 && !strcmp(seen.name[0], "Keyboard"));
    enumerate(di_a, DI8DEVTYPE_KEYBOARD, 0);
    ok("DI8DEVTYPE_KEYBOARD -> keyboard only", seen.n == 1 && !strcmp(seen.name[0], "Keyboard"));
    enumerate(di_a, DIEDFL_FORCEFEEDBACK, 0);
    ok("force-feedback-only filter -> no devices", seen.n == 0);

    /* A FALSE callback ends the enumeration without failing the call. */
    stop_after = 1;
    enumerate(di_a, 0, 0);
    ok("DIENUM_STOP stops after one callback", seen.n == 1);
    stop_after = 0;

    /* DirectInputCreateW's instance names are UTF-16; reading them as bytes
     * still shows every other byte zero. */
    enumerate(di_w, DI8DEVCLASS_KEYBOARD, 0);
    ok("W instance name is UTF-16", seen.n == 1 && seen.name[0][0] == 'K' &&
       seen.name[0][1] == 0 && seen.name[0][2] == 'e' && seen.name[0][3] == 0);

    /* A null callback is still E_INVALIDARG. */
    uint32_t fn = vslot(di_a, 4);
    uint32_t args[5] = { di_a, 0, 0, 0, 0 };
    ok("null callback -> E_INVALIDARG", hle_call_guest(fn, 5, args) == DIERR_INVALIDPARAM_VALUE);

    if (!fails) printf("dinput_legacy_enum_selftest: all checks passed\n");
    return fails != 0;
}
