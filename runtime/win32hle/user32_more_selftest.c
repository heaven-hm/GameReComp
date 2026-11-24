/*
 * user32_more_selftest.c - the additional Unicode text/class shims
 * (GetClassNameW, GetDlgItemTextW, GetClassInfoW) over the existing user32
 * window tree, with no game binary and no display.
 *
 * Every API is called through hle_call_guest on a resolved shim VA, so the
 * guest ABI is exercised the way a lift drives it: stdcall arguments on the
 * guest stack, the shim's RET popping them, and a guest window procedure
 * reached through call_proc/hle_call_guest. The checks cover the 2-byte WCHAR
 * guest buffers, the exact 40-byte WNDCLASSW layout, truncation/termination,
 * the two UTF-16 string fields in the class struct, and honest failures on
 * bad handles and null pointers (no writes outside the offered buffers).
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

/* Per-function prologue the lifter emits, so RECOMP_ENTER has its flag locals. */
#define GUEST_PROLOGUE(va)                                                   \
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE;                            \
    uint32_t _flag_a = 0, _flag_b = 0; RECOMP_ENTER(va)

#define WS_CHILD      0x40000000u
#define VA_WNDPROC    0x00405000u

static uint32_t VA_DEFPROC;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(uint32_t fn, int n, const uint32_t *a) { return hle_call_guest(fn, n, a); }

/* A registered class's window procedure, called through hle_call_guest by the
 * shims. It forwards everything to DefWindowProcW (the same built-in handler
 * the A forms reach), which owns the window's text; returning it also proves a
 * guest procedure can be nested inside a shim call. */
static void guest_wndproc(void) {
    GUEST_PROLOGUE(VA_WNDPROC);
    uint32_t h = MEM32(esp + 4), m = MEM32(esp + 8), w = MEM32(esp + 12), l = MEM32(esp + 16);
    uint32_t a[4] = { h, m, w, l };
    eax = hle_call_guest(VA_DEFPROC, 4, a);
    esp += 4u + 16u;                            /* pop synthetic ret + 4 stdcall args */
}

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { VA_WNDPROC, guest_wndproc } };
const uint32_t recomp_dispatch_count = 1;

/* Compare a guest WCHAR buffer against a host UTF-16 literal. */
static int wide_eq(uint32_t va, const uint16_t *lit) {
    const uint16_t *w = (const uint16_t *)hle_ptr(va);
    for (size_t i = 0; lit[i]; i++) if (w[i] != lit[i]) return 0;
    return 1;
}

int main(void) {
    win32hle_register(win32hle_user32);
    mach_enter();

    uint32_t va_reg  = hle_resolve("RegisterClassW");
    uint32_t va_create = hle_resolve("CreateWindowExW");
    uint32_t va_settext = hle_resolve("SetWindowTextW");
    uint32_t va_defproc = hle_resolve("DefWindowProcW");
    uint32_t va_gcn = hle_resolve("GetClassNameW");
    uint32_t va_gdit = hle_resolve("GetDlgItemTextW");
    uint32_t va_gci = hle_resolve("GetClassInfoW");

    ok("GetClassNameW resolves", va_gcn != 0);
    ok("GetDlgItemTextW resolves", va_gdit != 0);
    ok("GetClassInfoW resolves", va_gci != 0);
    VA_DEFPROC = va_defproc;
    ok("DefWindowProcW resolves", va_defproc != 0);

    /* Register a Unicode class with a wide name and a wide menu name. */
    static const uint16_t cls_name[] = { 'C','l','s','W','T','e','s','t',0 };
    static const uint16_t menu_name[] = { 'M','e','n','u','W',0 };
    uint32_t cls_va = hle_va(hle_alloc(sizeof cls_name));
    uint32_t menu_va = hle_va(hle_alloc(sizeof menu_name));
    memcpy(hle_ptr(cls_va), cls_name, sizeof cls_name);
    memcpy(hle_ptr(menu_va), menu_name, sizeof menu_name);

    uint32_t wc = hle_va(hle_alloc(48));                     /* WNDCLASSW + guard */
    memset(hle_ptr(wc), 0, 48);
    MEM32(wc) = 0;                                           /* style */
    MEM32(wc + 4) = VA_WNDPROC;                              /* lpfnWndProc */
    MEM32(wc + 16) = 0x00400000u;                            /* hInstance */
    MEM32(wc + 32) = menu_va;                                /* lpszMenuName (UTF-16) */
    MEM32(wc + 36) = cls_va;                                 /* lpszClassName (UTF-16) */
    uint32_t atom = call(va_reg, 1, &wc);
    ok("RegisterClassW returns a class atom", atom >= 0xC000u);

    uint32_t hwnd = call(va_create, 12, (uint32_t[]){
        0, atom, 0, 0, 0, 0, 100, 100, 0, 0, 0x00400000u, 0 });
    ok("CreateWindowExW made a window", hwnd != 0);

    /* ---- GetClassNameW ---- */
    uint32_t buf = hle_va(hle_alloc(64));                    /* 32 WCHARs */
    memset(hle_ptr(buf), 0x5A, 64);
    uint32_t r = call(va_gcn, 3, (uint32_t[]){ hwnd, buf, 16 });
    ok("GetClassNameW returns the class-name length", r == 8);
    ok("GetClassNameW wrote the UTF-16 class name", wide_eq(buf, cls_name));
    ok("GetClassNameW terminated the name",
       ((uint16_t *)hle_ptr(buf))[8] == 0);
    ok("GetClassNameW did not write past count",
       ((uint8_t *)hle_ptr(buf))[32] == 0x5A);

    memset(hle_ptr(buf), 0x5A, 64);
    r = call(va_gcn, 3, (uint32_t[]){ hwnd, buf, 4 });       /* 3 chars + null */
    ok("GetClassNameW truncates to count-1 chars", r == 3);
    ok("GetClassNameW's truncated name starts with 'Cls'",
       ((uint16_t *)hle_ptr(buf))[0] == 'C' &&
       ((uint16_t *)hle_ptr(buf))[1] == 'l' &&
       ((uint16_t *)hle_ptr(buf))[2] == 's' &&
       ((uint16_t *)hle_ptr(buf))[3] == 0);
    ok("GetClassNameW left the guard intact after truncation",
       ((uint8_t *)hle_ptr(buf))[8] == 0x5A);

    memset(hle_ptr(buf), 0x5A, 64);
    r = call(va_gcn, 3, (uint32_t[]){ 0, buf, 16 });
    ok("GetClassNameW rejects a null HWND", r == 0 && ((uint16_t *)hle_ptr(buf))[0] == 0);
    ok("GetClassNameW touched no other cell on failure",
       ((uint8_t *)hle_ptr(buf))[2] == 0x5A);
    ok("GetClassNameW rejects a null buffer", call(va_gcn, 3, (uint32_t[]){ hwnd, 0, 16 }) == 0);
    ok("GetClassNameW rejects a zero count", call(va_gcn, 3, (uint32_t[]){ hwnd, buf, 0 }) == 0);

    /* ---- GetDlgItemTextW (a child control with an id) ---- */
    uint32_t child = call(va_create, 12, (uint32_t[]){
        0, atom, 0, WS_CHILD, 0, 0, 50, 20, hwnd, 100, 0x00400000u, 0 });
    ok("the child control was created", child != 0);

    static const uint16_t ctl_text[] = { 'W','i','d','g','e','t',0 };
    uint32_t text_va = hle_va(hle_alloc(sizeof ctl_text));
    memcpy(hle_ptr(text_va), ctl_text, sizeof ctl_text);
    r = call(va_settext, 2, (uint32_t[]){ child, text_va });
    ok("SetWindowTextW set the control's text", r == 1);

    memset(hle_ptr(buf), 0x5A, 64);
    r = call(va_gdit, 4, (uint32_t[]){ hwnd, 100, buf, 16 });
    ok("GetDlgItemTextW returns the text length", r == 6);
    ok("GetDlgItemTextW wrote the UTF-16 text", wide_eq(buf, ctl_text));
    ok("GetDlgItemTextW terminated the text", ((uint16_t *)hle_ptr(buf))[6] == 0);
    ok("GetDlgItemTextW did not write past count",
       ((uint8_t *)hle_ptr(buf))[32] == 0x5A);

    memset(hle_ptr(buf), 0x5A, 64);
    r = call(va_gdit, 4, (uint32_t[]){ hwnd, 100, buf, 3 });
    ok("GetDlgItemTextW truncates to count-1 chars", r == 2);
    ok("GetDlgItemTextW's truncated text is 'Wi' plus null",
       ((uint16_t *)hle_ptr(buf))[0] == 'W' &&
       ((uint16_t *)hle_ptr(buf))[1] == 'i' &&
       ((uint16_t *)hle_ptr(buf))[2] == 0);

    r = call(va_gdit, 4, (uint32_t[]){ hwnd, 999, buf, 16 });
    ok("GetDlgItemTextW rejects a missing item", r == 0 && ((uint16_t *)hle_ptr(buf))[0] == 0);
    ok("GetDlgItemTextW rejects a null buffer", call(va_gdit, 4, (uint32_t[]){ hwnd, 100, 0, 16 }) == 0);
    ok("GetDlgItemTextW rejects a zero count", call(va_gdit, 4, (uint32_t[]){ hwnd, 100, buf, 0 }) == 0);

    /* ---- GetClassInfoW (the 40-byte WNDCLASSW) ---- */
    uint32_t wci = hle_va(hle_alloc(48));                    /* WNDCLASSW + guard */
    memset(hle_ptr(wci), 0x33, 48);
    r = call(va_gci, 3, (uint32_t[]){ 0x00400000u, cls_va, wci });
    ok("GetClassInfoW succeeds for a registered class", r == 1);
    ok("WNDCLASSW.lpfnWndProc", MEM32(wci + 4) == VA_WNDPROC);
    ok("WNDCLASSW.hInstance", MEM32(wci + 16) == 0x00400000u);
    ok("WNDCLASSW.lpszClassName is the caller's wide name", MEM32(wci + 36) == cls_va);
    ok("the returned class name is UTF-16",
       ((uint16_t *)hle_ptr(MEM32(wci + 36)))[0] == 'C' &&
       ((uint16_t *)hle_ptr(MEM32(wci + 36)))[1] == 'l');
    uint32_t mp = MEM32(wci + 32);
    ok("WNDCLASSW.lpszMenuName is a UTF-16 copy", mp != 0 && wide_eq(mp, menu_name));
    ok("GetClassInfoW wrote exactly 40 bytes", ((uint8_t *)hle_ptr(wci))[40] == 0x33);

    static const uint16_t unknown[] = { 'N','o','P','e',0 };
    uint32_t unk_va = hle_va(hle_alloc(sizeof unknown));
    memcpy(hle_ptr(unk_va), unknown, sizeof unknown);
    ok("GetClassInfoW rejects an unregistered wide name",
       call(va_gci, 3, (uint32_t[]){ 0, unk_va, wci }) == 0);
    ok("GetClassInfoW rejects a null name", call(va_gci, 3, (uint32_t[]){ 0, 0, wci }) == 0);
    ok("GetClassInfoW rejects a null struct", call(va_gci, 3, (uint32_t[]){ 0, cls_va, 0 }) == 0);

    mach_leave();
    if (fails == 0) printf("user32_more_selftest: all checks passed\n");
    return fails != 0;
}
