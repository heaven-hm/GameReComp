/* dinput_constants_selftest.c - Win32 DI error codes and mouse state size. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(uint32_t fn, int n, const uint32_t *a) { return hle_call_guest(fn, n, a); }
static uint32_t slot(uint32_t iface, unsigned i) { return MEM32(MEM32(iface) + 4u * i); }

int main(void) {
    win32hle_register(win32hle_dinput);
    mach_enter();
    static const uint8_t mouse_guid[16] = {
        0x60,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,
        0xBF,0xC7,0x44,0x45,0x53,0x54,0x00,0x00
    };
    uint32_t guid = hle_va(hle_alloc(16));
    memcpy(hle_ptr(guid), mouse_guid, 16);
    uint32_t di_out = hle_va(hle_alloc(4)), dev_out = hle_va(hle_alloc(4));
    uint32_t create = hle_resolve("DirectInputCreateA");
    ok("DirectInputCreateA resolves", create != 0);
    ok("DirectInput object", call(create, 4, (uint32_t[]){ 0, 0x0700u, di_out, 0 }) == 0);
    uint32_t di = MEM32(di_out);
    ok("CreateDevice", call(slot(di, 3), 4, (uint32_t[]){ di, guid, dev_out, 0 }) == 0);
    uint32_t dev = MEM32(dev_out);
    ok("mouse device object", dev != 0);

    uint32_t buf = hle_va(hle_alloc(24));
    ok("GetDeviceState before Acquire -> DIERR_NOTACQUIRED",
       call(slot(dev, 9), 3, (uint32_t[]){ dev, 20, buf }) == 0x8007000Cu);
    ok("GetObjectInfo -> DIERR_UNSUPPORTED",
       call(slot(dev, 14), 4, (uint32_t[]){ dev, 0, 0, 0 }) == 0x80004001u);

    /* DIMOUSESTATE2: three LONG axes and eight button bytes = 20 bytes. */
    uint32_t fmt = hle_va(hle_alloc(20));
    memset(hle_ptr(fmt), 0, 20);
    MEM32(fmt) = 20;
    ok("SetDataFormat 20 bytes", call(slot(dev, 11), 2, (uint32_t[]){ dev, fmt }) == 0);
    ok("Acquire", call(slot(dev, 7), 1, &dev) == 0);
    ok("GetDeviceState 20 bytes", call(slot(dev, 9), 3, (uint32_t[]){ dev, 20, buf }) == 0);
    ok("state buffer initialized", MEM32(buf) == 0 && MEM8(buf + 19u) == 0);
    ok("oversized request clamped and safe",
       call(slot(dev, 9), 3, (uint32_t[]){ dev, 24, buf }) == 0);
    ok("Release", call(slot(dev, 2), 1, &dev) == 0);

    mach_leave();
    printf("dinput_constants_selftest: all checks passed\n");
    return fails != 0;
}
