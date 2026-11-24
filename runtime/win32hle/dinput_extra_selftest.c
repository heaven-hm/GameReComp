/* dinput_extra_selftest.c - DirectInput8 facade and its COM ABI.
 *
 * Drives the shims the way a guest does: through hle_call_guest on the import
 * VA, with COM calls through the vtable the guest object exposes. The one
 * callback EnumDevices invokes is itself a registered shim, so the argument
 * push/cleanup on both sides of the native -> guest -> native call is checked
 * by the runtime, not bypassed with a direct C call.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#define DIENUM_STOP 0u
#define DIENUM_CONTINUE 1u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(uint32_t fn, int n, const uint32_t *a) { return hle_call_guest(fn, n, a); }
static uint32_t slot(uint32_t iface, unsigned i) { return MEM32(MEM32(iface) + 4u * i); }
static uint32_t guest_bytes(const uint8_t *p, uint32_t n) {
    uint32_t va = hle_va(hle_alloc(n));
    memcpy(hle_ptr(va), p, n);
    return va;
}

/* GUIDs/IIDs in the byte order a guest passes them (wine-10.0 dinput.h). */
static const uint8_t iid_di8_a[16] = {
    0x30,0x80,0x79,0xBF,0x3A,0x48,0xA2,0x4D,0xAA,0x99,0x5D,0x64,0xED,0x36,0x97,0x00 };
static const uint8_t iid_di8_w[16] = {
    0x31,0x80,0x79,0xBF,0x3A,0x48,0xA2,0x4D,0xAA,0x99,0x5D,0x64,0xED,0x36,0x97,0x00 };
static const uint8_t iid_unknown[16] = {
    0,0,0,0,0,0,0,0,0xC0,0,0,0,0,0,0,0x46 };
static const uint8_t iid_di7_a[16] = {   /* must be rejected by DirectInput8Create */
    0x84,0xB6,0x4C,0x9A,0x6D,0x23,0xD3,0x11,0x8E,0x9D,0x00,0xC0,0x4F,0x68,0x44,0xAE };
static const uint8_t mouse_guid[16] = {
    0x60,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,0xBF,0xC7,0x44,0x45,0x53,0x54,0x00,0x00 };
static const uint8_t keyboard_guid[16] = {
    0x61,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,0xBF,0xC7,0x44,0x45,0x53,0x54,0x00,0x00 };
static const uint8_t joystick_guid[16] = {
    0x70,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,0xBF,0xC7,0x44,0x45,0x53,0x54,0x00,0x00 };

/* EnumDevices callback context (guest memory): count, devtypes[4], sizes[4],
 * ANSI names[4][16]. The callback gets exactly two stdcall args. */
enum { MAX_CB = 4, CB_TYPES = 4, CB_SIZES = 20, CB_NAMES = 36, CB_STRIDE = 16 };
static void cb_record(void) {
    uint32_t inst = A32(0), ctx = A32(1);
    if (ctx) {
        uint32_t n = MEM32(ctx);
        if (n < MAX_CB) {
            MEM32(ctx + CB_TYPES + 4u * n) = MEM32(inst + 36u);  /* dwDevType */
            MEM32(ctx + CB_SIZES + 4u * n) = MEM32(inst);        /* dwSize */
            for (unsigned i = 0; i < CB_STRIDE - 1; i++)
                MEM8(ctx + CB_NAMES + CB_STRIDE * n + i) = MEM8(inst + 40u + i);
            MEM8(ctx + CB_NAMES + CB_STRIDE * n + CB_STRIDE - 1u) = 0;
            MEM32(ctx) = n + 1;
        }
    }
    RET(DIENUM_CONTINUE, 2);
}
static void cb_stop(void) {
    uint32_t ctx = A32(1);
    if (ctx) MEM32(ctx)++;
    RET(DIENUM_STOP, 2);
}
/* W callback: devtype at +0, six UTF-16 name code units at +4. */
static void cb_wide(void) {
    uint32_t inst = A32(0), ctx = A32(1);
    if (ctx) {
        MEM32(ctx) = MEM32(inst + 36u);
        for (unsigned i = 0; i < 6; i++)
            MEM16(ctx + 4u + 2u * i) = MEM16(inst + 40u + 2u * i);
    }
    RET(DIENUM_CONTINUE, 2);
}
static const win32hle_shim cb_shims[] = {
    { "Di8Test::EnumRecord", cb_record },
    { "Di8Test::EnumStop", cb_stop },
    { "Di8Test::EnumWide", cb_wide },
    { 0, 0 }
};

int main(void) {
    win32hle_register(win32hle_dinput);
    win32hle_register(cb_shims);
    mach_enter();

    uint32_t create8 = hle_resolve("DirectInput8Create");
    ok("DirectInput8Create resolves", create8 != 0);
    uint32_t diid_a = guest_bytes(iid_di8_a, 16), diid_w = guest_bytes(iid_di8_w, 16);
    uint32_t iid_unk = guest_bytes(iid_unknown, 16), iid_bad = guest_bytes(iid_di7_a, 16);
    uint32_t guid_mouse = guest_bytes(mouse_guid, 16), guid_kbd = guest_bytes(keyboard_guid, 16);
    uint32_t guid_joy = guest_bytes(joystick_guid, 16);
    uint32_t out = hle_va(hle_alloc(4));

    /* ---- export validation and Wine's HRESULT/order parity ---- */
    ok("null out -> E_POINTER",
       call(create8, 5, (uint32_t[]){ 0x1234u, 0x0800u, diid_a, 0, 0 }) == 0x80004003u);
    MEM32(out) = 0xDEADBEEFu;
    ok("DX7 IID -> DIERR_NOINTERFACE and cleared out",
       call(create8, 5, (uint32_t[]){ 0x1234u, 0x0800u, iid_bad, out, 0 }) == 0x80004002u &&
       MEM32(out) == 0);
    MEM32(out) = 0xDEADBEEFu;
    ok("null hinst -> DIERR_INVALIDPARAM",
       call(create8, 5, (uint32_t[]){ 0, 0x0800u, diid_a, out, 0 }) == 0x80070057u &&
       MEM32(out) == 0);
    MEM32(out) = 0xDEADBEEFu;
    ok("version 0 -> DIERR_NOTINITIALIZED",
       call(create8, 5, (uint32_t[]){ 0x1234u, 0, diid_a, out, 0 }) == 0x80070015u &&
       MEM32(out) == 0);
    ok("version 0x0700 -> DIERR_BETADIRECTINPUTVERSION",
       call(create8, 5, (uint32_t[]){ 0x1234u, 0x0700u, diid_a, out, 0 }) == 0x80070481u);
    ok("version 0x0900 -> DIERR_OLDDIRECTINPUTVERSION",
       call(create8, 5, (uint32_t[]){ 0x1234u, 0x0900u, diid_a, out, 0 }) == 0x8007047Eu);
    MEM32(out) = 0xDEADBEEFu;
    ok("outer -> cleared out with DI_OK (Wine parity)",
       call(create8, 5, (uint32_t[]){ 0x1234u, 0x0800u, diid_a, out, 0x99u }) == 0 &&
       MEM32(out) == 0);

    ok("IID_IDirectInput8A -> success",
       call(create8, 5, (uint32_t[]){ 0x1234u, 0x0800u, diid_a, out, 0 }) == 0 && MEM32(out) != 0);
    uint32_t di = MEM32(out);
    int all = 1;
    for (unsigned i = 0; i < 11; i++) if (!slot(di, i)) all = 0;
    ok("IDirectInput8 vtable: 11 non-null slots", all);

    /* Slots 9/10 distinguish DI8 from DI7's 10-slot table: calling them with
     * their real argument counts must reach the unsupported-method shims. */
    ok("slot 9 = EnumDevicesBySemantics -> DIERR_UNSUPPORTED",
       call(slot(di, 9), 6, (uint32_t[]){ di, 0, 0, 0, 0, 0 }) == 0x80004001u);
    ok("slot 10 = ConfigureDevices -> DIERR_UNSUPPORTED",
       call(slot(di, 10), 5, (uint32_t[]){ di, 0, 0, 0, 0 }) == 0x80004001u);

    /* ---- QueryInterface / FindDevice ---- */
    uint32_t qiout = hle_va(hle_alloc(4));
    ok("QI(IID_IUnknown) -> same object, DI_OK",
       call(slot(di, 0), 3, (uint32_t[]){ di, iid_unk, qiout }) == 0 && MEM32(qiout) == di);
    ok("QI AddRef", MEM32(di + 4) == 2);
    ok("Release QI reference", call(slot(di, 2), 1, &di) == 1);
    MEM32(qiout) = 0xDEADBEEFu;
    ok("QI(DX7 IID) -> DIERR_NOINTERFACE and cleared out",
       call(slot(di, 0), 3, (uint32_t[]){ di, iid_bad, qiout }) == 0x80004002u && MEM32(qiout) == 0);

    uint32_t fguid = hle_va(hle_alloc(16));
    ok("FindDevice(known) -> self GUID",
       call(slot(di, 8), 4, (uint32_t[]){ di, guid_kbd, 0, fguid }) == 0 &&
       memcmp(hle_ptr(fguid), keyboard_guid, 16) == 0);
    ok("FindDevice(joystick) -> DIERR_DEVICENOTREG",
       call(slot(di, 8), 4, (uint32_t[]){ di, guid_joy, 0, fguid }) == 0x80040154u);

    /* ---- EnumDevices through a real guest callback ---- */
    uint32_t cb = hle_resolve("Di8Test::EnumRecord"), stopcb = hle_resolve("Di8Test::EnumStop");
    uint32_t ctx = hle_va(hle_alloc(128));
    ok("EnumDevices callbacks resolve", cb != 0 && stopcb != 0);
    memset(hle_ptr(ctx), 0, 128);
    ok("EnumDevices(ALL) -> DI_OK", call(slot(di, 4), 5,
       (uint32_t[]){ di, 0, cb, ctx, 0 }) == 0);
    ok("ALL enumerates mouse then keyboard", MEM32(ctx) == 2 &&
       MEM32(ctx + CB_TYPES) == 0x0212u && MEM32(ctx + CB_TYPES + 4u) == 0x0413u);
    ok("DIDEVICEINSTANCE dwSize is 580",
       MEM32(ctx + CB_SIZES) == 580 && MEM32(ctx + CB_SIZES + 4u) == 580);
    ok("instance names are Mouse/Keyboard",
       !strcmp((char *)hle_ptr(ctx + CB_NAMES), "Mouse") &&
       !strcmp((char *)hle_ptr(ctx + CB_NAMES + CB_STRIDE), "Keyboard"));

    memset(hle_ptr(ctx), 0, 128);
    ok("EnumDevices(KEYBOARD class) -> one keyboard", call(slot(di, 4), 5,
       (uint32_t[]){ di, 3, cb, ctx, 0 }) == 0 && MEM32(ctx) == 1 &&
       MEM32(ctx + CB_TYPES) == 0x0413u);
    memset(hle_ptr(ctx), 0, 128);
    ok("EnumDevices(POINTER class) -> one mouse", call(slot(di, 4), 5,
       (uint32_t[]){ di, 2, cb, ctx, 0 }) == 0 && MEM32(ctx) == 1 &&
       MEM32(ctx + CB_TYPES) == 0x0212u);
    memset(hle_ptr(ctx), 0, 128);
    ok("EnumDevices(GAMECTRL class) -> none", call(slot(di, 4), 5,
       (uint32_t[]){ di, 4, cb, ctx, 0 }) == 0 && MEM32(ctx) == 0);
    memset(hle_ptr(ctx), 0, 128);
    ok("EnumDevices(DI8DEVTYPE_MOUSE) -> one mouse", call(slot(di, 4), 5,
       (uint32_t[]){ di, 0x12u, cb, ctx, 0 }) == 0 && MEM32(ctx) == 1 &&
       MEM32(ctx + CB_TYPES) == 0x0212u);
    memset(hle_ptr(ctx), 0, 128);
    ok("EnumDevices(DIENUM_STOP) stops after first", call(slot(di, 4), 5,
       (uint32_t[]){ di, 0, stopcb, ctx, 0 }) == 0 && MEM32(ctx) == 1);
    ok("EnumDevices(invalid type) -> DIERR_INVALIDPARAM", call(slot(di, 4), 5,
       (uint32_t[]){ di, 0x10u, cb, ctx, 0 }) == 0x80070057u);
    ok("EnumDevices(invalid flags) -> DIERR_INVALIDPARAM", call(slot(di, 4), 5,
       (uint32_t[]){ di, 0, cb, ctx, 0x80000000u }) == 0x80070057u);
    ok("EnumDevices(null callback) -> DIERR_INVALIDPARAM", call(slot(di, 4), 5,
       (uint32_t[]){ di, 0, 0, ctx, 0 }) == 0x80070057u);

    /* ---- W facade: same slot layout, UTF-16 instance name ---- */
    uint32_t outw = hle_va(hle_alloc(4));
    ok("IID_IDirectInput8W -> success",
       call(create8, 5, (uint32_t[]){ 0x1234u, 0x0800u, diid_w, outw, 0 }) == 0 && MEM32(outw) != 0);
    uint32_t diw = MEM32(outw);
    uint32_t wctx = hle_va(hle_alloc(32)), wcb = hle_resolve("Di8Test::EnumWide");
    memset(hle_ptr(wctx), 0, 32);
    ok("W EnumDevices(KEYBOARD) -> DI_OK", call(slot(diw, 4), 5,
       (uint32_t[]){ diw, 3, wcb, wctx, 0 }) == 0 && MEM32(wctx) == 0x0413u);
    ok("W instance name is UTF-16 Keyboard",
       MEM16(wctx + 4) == 'K' && MEM16(wctx + 6) == 'e' && MEM16(wctx + 8) == 'y' &&
       MEM16(wctx + 10) == 'b' && MEM16(wctx + 12) == 'o' && MEM16(wctx + 14) == 'a');

    /* ---- IDirectInputDevice8: vtable, state, acquire/unacquire ---- */
    uint32_t devout = hle_va(hle_alloc(4));
    ok("CreateDevice(mouse) -> DI_OK", call(slot(di, 3), 4,
       (uint32_t[]){ di, guid_mouse, devout, 0 }) == 0 && MEM32(devout) != 0);
    uint32_t dev = MEM32(devout);
    all = 1;
    for (unsigned i = 0; i < 32; i++) if (!slot(dev, i)) all = 0;
    ok("IDirectInputDevice8 vtable: 32 non-null slots", all);
    ok("BuildActionMap -> DIERR_UNSUPPORTED",
       call(slot(dev, 29), 4, (uint32_t[]){ dev, 0, 0, 0 }) == 0x80004001u);
    ok("SetActionMap -> DIERR_UNSUPPORTED",
       call(slot(dev, 30), 4, (uint32_t[]){ dev, 0, 0, 0 }) == 0x80004001u);
    ok("GetImageInfo -> DIERR_UNSUPPORTED",
       call(slot(dev, 31), 2, (uint32_t[]){ dev, 0 }) == 0x80004001u);

    uint32_t buf = hle_va(hle_alloc(24));
    ok("GetDeviceState before Acquire -> DIERR_NOTACQUIRED",
       call(slot(dev, 9), 3, (uint32_t[]){ dev, 20, buf }) == 0x8007000Cu);
    uint32_t fmt = hle_va(hle_alloc(20));
    memset(hle_ptr(fmt), 0, 20);
    MEM32(fmt) = 20;                                       /* DIMOUSESTATE2 */
    ok("SetDataFormat 20 bytes", call(slot(dev, 11), 2, (uint32_t[]){ dev, fmt }) == 0);
    ok("Acquire", call(slot(dev, 7), 1, &dev) == 0);
    ok("Poll", call(slot(dev, 25), 1, &dev) == 0);
    ok("GetDeviceState 20 bytes", call(slot(dev, 9), 3, (uint32_t[]){ dev, 20, buf }) == 0);
    ok("mouse state initialized", MEM32(buf) == 0 && MEM8(buf + 19u) == 0);
    ok("Unacquire", call(slot(dev, 8), 1, &dev) == 0);
    ok("GetDeviceState after Unacquire -> DIERR_NOTACQUIRED",
       call(slot(dev, 9), 3, (uint32_t[]){ dev, 20, buf }) == 0x8007000Cu);
    ok("device Release", call(slot(dev, 2), 1, &dev) == 0);

    ok("W object Release", call(slot(diw, 2), 1, &diw) == 0);
    ok("A object Release", call(slot(di, 2), 1, &di) == 0);

    mach_leave();
    printf("dinput_extra_selftest: %s\n", fails ? "FAILED" : "all checks passed");
    return fails != 0;
}
