/*
 * comctl32.c - COMCTL32's flat scroll bar entry points.
 *
 * A program that wants flat scroll bars normally does not import them
 * statically: it loads COMCTL32 and asks GetProcAddress for "FlatSB_*", so
 * that it still runs on Windows versions without them. That is exactly what
 * HeroCore gm5 does, and a host with no answer for those names makes it exit
 * before it draws anything. Registering the names is the fix; what the calls
 * then do has to be truthful.
 *
 * There is no flat scroll bar widget here - no subclass, no custom drawing.
 * Microsoft's documented rule is that when flat scroll bars have not been
 * initialized for a window, the FlatSB APIs defer to the matching standard
 * USER32 APIs. That is what this module does: every FlatSB_* call validates
 * its arguments and forwards to the USER32 shim of the same name, so the
 * answer is the scroll state the guest's own SetScrollInfo/SetScrollPos calls
 * actually produced. No scroll bar is invented and no value is fabricated.
 *
 * FlatSB_GetScrollProp is different: it has no USER32 counterpart, and a
 * window in this host has never been through InitializeFlatSB. Windows (and
 * Wine's implementation) answer FALSE for such a window. We do the same, and
 * leave the caller's output untouched; we never claim a flat style we cannot
 * draw. Likewise SB_CTL (a stand-alone scroll bar control) is not modelled by
 * the USER32 layer, so it is refused instead of being answered with another
 * bar's state.
 *
 * comctl32_selftest.c checks the GetProcAddress path, the delegation (a value
 * stored with SetScrollInfo is read back through FlatSB_GetScrollInfo), and
 * the documented failure answers.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

/* Win32 error codes (winerror.h). */
#define ERROR_INVALID_PARAMETER     87u
#define ERROR_CALL_NOT_IMPLEMENTED 120u

/* SB_* (winuser.h). SB_BOTH is only legal for Enable/ShowScrollBar. */
#define SB_HORZ 0u
#define SB_VERT 1u
#define SB_CTL  2u
#define SB_BOTH 3u

/* ESB_* arrow flags (winuser.h): 0 = both on, 3 = both off. */
#define ESB_DISABLE_BOTH 3u

/* SCROLLINFO (winuser.h), x86 layout: cbSize, fMask, nMin, nMax, nPage,
 * nPos, nTrackPos - 28 bytes. SIF_ALL = range|page|pos|trackpos. */
#define SCROLLINFO_SIZE 28u
#define SIF_ALL 0x17u

/* WSB_PROP_* indices (commctrl.h) accepted by FlatSB_GetScrollProp. */
#define WSB_PROP_MASK 0xFFFu

/* Forward to a USER32 shim through its import VA. hle_call_guest enters shims
 * by their synthetic VA just as it enters lifted code, so the standard scroll
 * bar functions see the same guest stack and guest SCROLLINFO the guest gave
 * us. If the shim is not registered, fail loudly rather than making up data. */
static int call_std(const char *name, int n, const uint32_t *args, uint32_t *out) {
    uint32_t va = hle_resolve(name);
    if (!va) {
        hle_set_last_error(ERROR_CALL_NOT_IMPLEMENTED);
        return 0;
    }
    *out = hle_call_guest(va, n, args);
    return 1;
}

static int bar_ok(uint32_t bar, int allow_both) {
    return bar == SB_HORZ || bar == SB_VERT || (allow_both && bar == SB_BOTH);
}

static void c_FlatSB_EnableScrollBar(void) {         /* (hwnd, bar, arrows) -> BOOL */
    uint32_t hwnd = A32(0), bar = A32(1), arrows = A32(2), r = 0;
    if (!hle_is_window(hwnd) || !bar_ok(bar, 1) || arrows > ESB_DISABLE_BOTH) {
        hle_set_last_error(ERROR_INVALID_PARAMETER);
        RET(0, 3);
    }
    uint32_t a[3] = { hwnd, bar, arrows };
    RET(call_std("EnableScrollBar", 3, a, &r) ? r : 0, 3);
}

static void c_FlatSB_ShowScrollBar(void) {           /* (hwnd, bar, show) -> BOOL */
    uint32_t hwnd = A32(0), bar = A32(1), show = A32(2), r = 0;
    if (!hle_is_window(hwnd) || !bar_ok(bar, 1)) {
        hle_set_last_error(ERROR_INVALID_PARAMETER);
        RET(0, 3);
    }
    uint32_t a[3] = { hwnd, bar, show };
    RET(call_std("ShowScrollBar", 3, a, &r) ? r : 0, 3);
}

static void c_FlatSB_GetScrollPos(void) {            /* (hwnd, bar) -> int */
    uint32_t hwnd = A32(0), bar = A32(1), r = 0;
    if (!hle_is_window(hwnd) || !bar_ok(bar, 0)) {
        hle_set_last_error(ERROR_INVALID_PARAMETER);
        RET(0, 2);                                   /* GetScrollPos fails with 0 */
    }
    uint32_t a[2] = { hwnd, bar };
    RET(call_std("GetScrollPos", 2, a, &r) ? r : 0, 2);
}

static void c_FlatSB_GetScrollInfo(void) {           /* (hwnd, bar, &si) -> BOOL */
    uint32_t hwnd = A32(0), bar = A32(1), si = A32(2), r = 0;
    if (!hle_is_window(hwnd) || !bar_ok(bar, 0) || !si || MEM32(si) < SCROLLINFO_SIZE) {
        hle_set_last_error(ERROR_INVALID_PARAMETER);
        RET(0, 3);
    }
    uint32_t a[3] = { hwnd, bar, si };
    RET(call_std("GetScrollInfo", 3, a, &r) ? r : 0, 3);
}

static void c_FlatSB_GetScrollRange(void) {          /* (hwnd, bar, &min, &max) -> BOOL */
    uint32_t hwnd = A32(0), bar = A32(1), lo = A32(2), hi = A32(3), r = 0;
    if (!hle_is_window(hwnd) || !bar_ok(bar, 0) || !lo || !hi) {
        hle_set_last_error(ERROR_INVALID_PARAMETER);
        RET(0, 4);
    }
    uint32_t a[4] = { hwnd, bar, lo, hi };
    RET(call_std("GetScrollRange", 4, a, &r) ? r : 0, 4);
}

static void c_FlatSB_SetScrollPos(void) {            /* (hwnd, bar, pos, redraw) -> int */
    uint32_t hwnd = A32(0), bar = A32(1), pos = A32(2), redraw = A32(3), r = 0;
    if (!hle_is_window(hwnd) || !bar_ok(bar, 0)) {
        hle_set_last_error(ERROR_INVALID_PARAMETER);
        RET(0, 4);
    }
    uint32_t a[4] = { hwnd, bar, pos, redraw };
    RET(call_std("SetScrollPos", 4, a, &r) ? r : 0, 4);
}

static void c_FlatSB_SetScrollInfo(void) {           /* (hwnd, bar, &si, redraw) -> int */
    uint32_t hwnd = A32(0), bar = A32(1), si = A32(2), redraw = A32(3), r = 0;
    if (!hle_is_window(hwnd) || !bar_ok(bar, 0) || !si || MEM32(si) < SCROLLINFO_SIZE) {
        hle_set_last_error(ERROR_INVALID_PARAMETER);
        RET(0, 4);
    }
    uint32_t a[4] = { hwnd, bar, si, redraw };
    RET(call_std("SetScrollInfo", 4, a, &r) ? r : 0, 4);
}

static void c_FlatSB_SetScrollRange(void) {          /* (hwnd, bar, min, max, redraw) -> int */
    uint32_t hwnd = A32(0), bar = A32(1), lo = A32(2), hi = A32(3), redraw = A32(4), r = 0;
    if (!hle_is_window(hwnd) || !bar_ok(bar, 0)) {
        hle_set_last_error(ERROR_INVALID_PARAMETER);
        RET(0, 5);
    }
    uint32_t a[5] = { hwnd, bar, lo, hi, redraw };
    RET(call_std("SetScrollRange", 5, a, &r) ? r : 0, 5);
}

static void c_FlatSB_GetScrollProp(void) {           /* (hwnd, index, &value) -> BOOL */
    uint32_t hwnd = A32(0), index = A32(1), out = A32(2);
    /* No window here has been initialized for flat scroll bars, so there is
     * no flat-scroll property to return. Windows/Wine answer FALSE in this
     * case and leave *lpiValue alone; do exactly that. A malformed index or
     * output pointer is an invalid parameter, not a property. */
    if (!hle_is_window(hwnd) || !out || (index & ~WSB_PROP_MASK)) {
        hle_set_last_error(ERROR_INVALID_PARAMETER);
        RET(0, 3);
    }
    RET(0, 3);
}

const win32hle_shim win32hle_comctl32[] = {
    { "FlatSB_EnableScrollBar", c_FlatSB_EnableScrollBar },
    { "FlatSB_GetScrollInfo",   c_FlatSB_GetScrollInfo },
    { "FlatSB_GetScrollPos",    c_FlatSB_GetScrollPos },
    { "FlatSB_GetScrollProp",   c_FlatSB_GetScrollProp },
    { "FlatSB_GetScrollRange",  c_FlatSB_GetScrollRange },
    { "FlatSB_ShowScrollBar",   c_FlatSB_ShowScrollBar },
    { "FlatSB_SetScrollInfo",   c_FlatSB_SetScrollInfo },
    { "FlatSB_SetScrollPos",    c_FlatSB_SetScrollPos },
    { "FlatSB_SetScrollRange",  c_FlatSB_SetScrollRange },
    { 0, 0 }
};
