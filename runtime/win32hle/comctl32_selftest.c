/*
 * comctl32_selftest.c - the HeroCore gm5 COMCTL32 path, end to end.
 *
 * 1. LoadLibraryA("comctl32.dll") + GetProcAddress for each of the four names
 *    the game asks for: this is the exact call that used to log "not answered"
 *    and exit 3. An unknown name must still be unanswered, so the check is
 *    meaningful.
 * 2. The flat-scrollbar shims must answer with the state the guest actually
 *    set through USER32 (delegation), not an invented scrollbar: SetScrollInfo
 *    --> FlatSB_GetScrollPos / FlatSB_GetScrollInfo / FlatSB_GetScrollRange.
 * 3. Invalid hwnd, bar index, arrow flags, SCROLLINFO size and output pointer
 *    must produce the documented failure value, and GetScrollProp must answer
 *    FALSE for a window that was never initialized for flat scroll bars.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

#define SB_HORZ 0u
#define SB_VERT 1u
#define SB_CTL  2u
#define SB_BOTH 3u
#define ESB_DISABLE_BOTH 3u
#define SIF_ALL 0x17u
#define SIF_POS 0x4u
#define WSB_PROP_HSTYLE 0x200u
#define ERROR_INVALID_PARAMETER 87u

static int fails;
static void ok(const char *what, int cond) { if (!cond) { printf("FAIL %s\n", what); fails++; } }

/* No lifted code in this test: hle_call_guest enters the shims by their import
 * VAs, which is how the generated build reaches them. */
const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static uint32_t call(const char *name, int n, const uint32_t *a) {
    return hle_call_guest(hle_resolve(name), n, a);
}
static uint32_t guest_str(const char *s) {
    char *p = (char *)hle_alloc((uint32_t)strlen(s) + 1u);
    strcpy(p, s);
    return hle_va(p);
}

int main(void) {
    recomp_host_init();                              /* registers user32, module and comctl32 */

    /* --- the exact failing path from the gm5 log --- */
    uint32_t comctl = call("LoadLibraryA", 1, (uint32_t[]){ guest_str("comctl32.dll") });
    ok("LoadLibraryA(comctl32.dll)", comctl != 0);
    static const char *const flat[] = {
        "FlatSB_EnableScrollBar", "FlatSB_GetScrollInfo",
        "FlatSB_GetScrollPos", "FlatSB_GetScrollProp"
    };
    for (unsigned i = 0; i < sizeof flat / sizeof *flat; i++) {
        uint32_t va = call("GetProcAddress", 2, (uint32_t[]){ comctl, guest_str(flat[i]) });
        ok(flat[i], va != 0 && va == hle_resolve(flat[i]));
    }
    ok("an unknown export is still unanswered",
       call("GetProcAddress", 2, (uint32_t[]){ comctl, guest_str("FlatSB_NotAnExport") }) == 0);

    /* --- a window with real scroll state --- */
    uint32_t hwnd = call("CreateWindowExA", 12, (uint32_t[]){
        0, guest_str("Static"), 0, 0, 0, 0, 120, 120, 0, 0, 0, 0 });
    ok("CreateWindowExA", hle_is_window(hwnd));

    uint32_t si_va = hle_va(hle_alloc(28));
    uint32_t *si = (uint32_t *)hle_ptr(si_va);
    si[0] = 28; si[1] = SIF_ALL; si[2] = 0; si[3] = 100; si[4] = 10; si[5] = 7; si[6] = 9;
    ok("SetScrollInfo seeds the state", call("SetScrollInfo", 4,
       (uint32_t[]){ hwnd, SB_VERT, si_va, 0 }) == 7);

    /* --- delegation: the FlatSB getters see what the guest stored --- */
    ok("FlatSB_GetScrollPos reads the stored position", call("FlatSB_GetScrollPos", 2,
       (uint32_t[]){ hwnd, SB_VERT }) == 7);
    memset(si, 0, 28); si[0] = 28; si[1] = SIF_ALL;
    ok("FlatSB_GetScrollInfo succeeds", call("FlatSB_GetScrollInfo", 3,
       (uint32_t[]){ hwnd, SB_VERT, si_va }) == 1);
    ok("SCROLLINFO range/page/pos/track read back",
       si[2] == 0 && si[3] == 100 && si[4] == 10 && si[5] == 7 && si[6] == 9);
    memset(si, 0, 28); si[0] = 28; si[1] = SIF_POS;
    call("FlatSB_GetScrollInfo", 3, (uint32_t[]){ hwnd, SB_VERT, si_va });
    ok("fMask selects only nPos", si[2] == 0 && si[3] == 0 && si[4] == 0 && si[5] == 7 && si[6] == 0);

    uint32_t *lo = (uint32_t *)hle_alloc(4), *hi = (uint32_t *)hle_alloc(4);
    *lo = *hi = 0xDEADBEEFu;
    ok("FlatSB_GetScrollRange succeeds", call("FlatSB_GetScrollRange", 4,
       (uint32_t[]){ hwnd, SB_VERT, hle_va(lo), hle_va(hi) }) == 1);
    ok("range read back", *lo == 0 && *hi == 100);

    /* --- sibling setters feed the same state, so the getters are not canned --- */
    call("FlatSB_SetScrollRange", 5, (uint32_t[]){ hwnd, SB_HORZ, 0, 50, 0 });
    ok("FlatSB_SetScrollPos returns the old position", call("FlatSB_SetScrollPos", 4,
       (uint32_t[]){ hwnd, SB_HORZ, 13, 0 }) == 0);
    ok("FlatSB_GetScrollPos sees the new position", call("FlatSB_GetScrollPos", 2,
       (uint32_t[]){ hwnd, SB_HORZ }) == 13);
    ok("FlatSB_EnableScrollBar accepts a valid call", call("FlatSB_EnableScrollBar", 3,
       (uint32_t[]){ hwnd, SB_BOTH, ESB_DISABLE_BOTH }) == 1);
    ok("FlatSB_ShowScrollBar accepts a valid call", call("FlatSB_ShowScrollBar", 3,
       (uint32_t[]){ hwnd, SB_VERT, 1 }) == 1);

    /* --- GetScrollProp: flat scroll bars were never initialized here --- */
    uint32_t *prop = (uint32_t *)hle_alloc(4);
    *prop = 0x13572468u;
    ok("FlatSB_GetScrollProp answers FALSE (no flat bars)", call("FlatSB_GetScrollProp", 3,
       (uint32_t[]){ hwnd, WSB_PROP_HSTYLE, hle_va(prop) }) == 0);
    ok("GetScrollProp leaves the output untouched", *prop == 0x13572468u);

    /* --- documented failures for bad arguments --- */
    ok("EnableScrollBar: invalid hwnd", call("FlatSB_EnableScrollBar", 3,
       (uint32_t[]){ 0x1234u, SB_BOTH, 0 }) == 0);
    ok("EnableScrollBar: invalid bar", call("FlatSB_EnableScrollBar", 3,
       (uint32_t[]){ hwnd, 42, 0 }) == 0);
    ok("EnableScrollBar: invalid arrows", call("FlatSB_EnableScrollBar", 3,
       (uint32_t[]){ hwnd, SB_BOTH, 42 }) == 0);
    ok("GetScrollPos: invalid hwnd", call("FlatSB_GetScrollPos", 2,
       (uint32_t[]){ 0x1234u, SB_VERT }) == 0);
    ok("GetScrollPos: SB_CTL not modelled", call("FlatSB_GetScrollPos", 2,
       (uint32_t[]){ hwnd, SB_CTL }) == 0);
    ok("GetScrollInfo: invalid hwnd", call("FlatSB_GetScrollInfo", 3,
       (uint32_t[]){ 0x1234u, SB_VERT, si_va }) == 0);
    ok("GetScrollInfo: invalid bar", call("FlatSB_GetScrollInfo", 3,
       (uint32_t[]){ hwnd, SB_BOTH, si_va }) == 0);
    ok("GetScrollInfo: NULL SCROLLINFO", call("FlatSB_GetScrollInfo", 3,
       (uint32_t[]){ hwnd, SB_VERT, 0 }) == 0);
    uint32_t small_va = hle_va(hle_alloc(4));
    MEM32(small_va) = 4;
    ok("GetScrollInfo: cbSize too small", call("FlatSB_GetScrollInfo", 3,
       (uint32_t[]){ hwnd, SB_VERT, small_va }) == 0);
    ok("the failure sets ERROR_INVALID_PARAMETER", hle_get_last_error() == ERROR_INVALID_PARAMETER);
    ok("GetScrollProp: invalid hwnd", call("FlatSB_GetScrollProp", 3,
       (uint32_t[]){ 0x1234u, WSB_PROP_HSTYLE, hle_va(prop) }) == 0);
    ok("GetScrollProp: NULL output", call("FlatSB_GetScrollProp", 3,
       (uint32_t[]){ hwnd, WSB_PROP_HSTYLE, 0 }) == 0);
    ok("GetScrollProp: invalid index", call("FlatSB_GetScrollProp", 3,
       (uint32_t[]){ hwnd, 0x2000u, hle_va(prop) }) == 0);

    printf("comctl32_selftest: %s\n", fails ? "FAIL" : "PASS");
    return fails != 0;
}
