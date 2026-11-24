/*
 * user32_msg_selftest.c - the Unicode message-loop shims (GetMessageW,
 * PostMessageW, SendMessageW, RegisterClassExW) over the existing user32
 * queue, with no game binary and no display.
 *
 * Every API is called through hle_call_guest on a resolved shim VA, so the
 * guest ABI is exercised the same way a lift drives it: stdcall arguments on
 * the guest stack, the shim's RET popping them, and a guest window procedure
 * reached through call_proc/hle_call_guest. Messages are posted both from the
 * host (hle_post_message) and from the guest (PostMessageW); the checks cover
 * the guest MSG layout, the BOOL/count return values, message filters, the
 * WM_QUIT result, peek/remove, and the one text-bearing case (WM_SETTEXT).
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

#ifndef WM_QUIT
#define WM_QUIT 0x0012u
#endif
#define WM_SETTEXT      0x000Cu
#define WM_KEYDOWN      0x0100u
#define WM_USER         0x0400u
#define MSG_CUSTOM      (WM_USER + 0x1234u)
#define MSG_FILTERED    (WM_USER + 0x005u)
#define MSG_PEEK        (WM_USER + 0x009u)

#define VA_WNDPROC      0x00405000u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(uint32_t fn, int n, const uint32_t *a) { return hle_call_guest(fn, n, a); }

/* The guest window procedure call_proc dispatches to. It records its four
 * stdcall args and, for WM_SETTEXT, copies the ANSI lParam before the shim
 * frees its temporary. Returns 7 so a caller can prove the result propagates. */
static uint32_t g_proc_calls, g_proc_msg, g_proc_w, g_proc_l;
static char g_last_text[64];
static void guest_wndproc(void) {
    GUEST_PROLOGUE(VA_WNDPROC);
    uint32_t h = MEM32(esp + 4), m = MEM32(esp + 8), w = MEM32(esp + 12), l = MEM32(esp + 16);
    (void)h;
    g_proc_calls++;
    g_proc_msg = m, g_proc_w = w, g_proc_l = l;
    if (m == WM_SETTEXT && l) snprintf(g_last_text, sizeof g_last_text, "%s", (const char *)hle_ptr(l));
    eax = 7;
    esp += 4u + 16u;                            /* pop synthetic ret + 4 stdcall args */
}

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { VA_WNDPROC, guest_wndproc } };
const uint32_t recomp_dispatch_count = 1;

int main(void) {
    win32hle_register(win32hle_user32);
    mach_enter();

    uint32_t va_get = hle_resolve("GetMessageW");
    uint32_t va_peek = hle_resolve("PeekMessageW");
    uint32_t va_post = hle_resolve("PostMessageW");
    uint32_t va_send = hle_resolve("SendMessageW");
    uint32_t va_reg = hle_resolve("RegisterClassExW");
    uint32_t va_create = hle_resolve("CreateWindowExW");
    uint32_t va_dispatch = hle_resolve("DispatchMessageW");

    ok("GetMessageW resolves", va_get != 0);
    ok("PeekMessageW resolves", va_peek != 0);
    ok("PostMessageW resolves", va_post != 0);
    ok("SendMessageW resolves", va_send != 0);
    ok("RegisterClassExW resolves", va_reg != 0);
    ok("CreateWindowExW resolves", va_create != 0);
    ok("DispatchMessageW resolves", va_dispatch != 0);

    /* A 28-byte guest MSG plus a guard word: hwnd, message, wParam, lParam,
     * time, x, y. The guard proves the shim writes exactly the guest layout. */
    uint32_t msg = hle_va(hle_alloc(32));
    memset(hle_ptr(msg), 0xAA, 32);

    /* 1. Host-posted message, retrieved by the guest's GetMessageW. */
    hle_post_message(0x1234u, WM_KEYDOWN, 0x41u, 0x1u);
    uint32_t r = call(va_get, 4, (uint32_t[]){ msg, 0, 0, 0 });
    ok("GetMessageW returns TRUE for a posted message", r == 1);
    ok("MSG.hwnd", MEM32(msg) == 0x1234u);
    ok("MSG.message", MEM32(msg + 4) == WM_KEYDOWN);
    ok("MSG.wParam", MEM32(msg + 8) == 0x41u);
    ok("MSG.lParam", MEM32(msg + 12) == 0x1u);
    ok("guest MSG layout is 28 bytes", MEM32(msg + 28) == 0xAAAAAAAAu);

    /* 2. Min/max filters: a non-matching message stays queued. */
    memset(hle_ptr(msg), 0, 28);
    hle_post_message(0, 0x7FFFu, 11, 22);
    hle_post_message(0, MSG_FILTERED, 33, 44);
    r = call(va_get, 4, (uint32_t[]){ msg, 0, WM_USER, WM_USER + 0xFFu });
    ok("GetMessageW respects the message filter",
       r == 1 && MEM32(msg + 4) == MSG_FILTERED && MEM32(msg + 8) == 33);
    r = call(va_get, 4, (uint32_t[]){ msg, 0, 0, 0 });
    ok("a filtered-out message stays in the queue",
       r == 1 && MEM32(msg + 4) == 0x7FFFu && MEM32(msg + 8) == 11);

    /* 3. PostMessageW from the guest lands on the same queue. */
    r = call(va_post, 4, (uint32_t[]){ 0, MSG_CUSTOM, 0x12345678u, 0xDEADBEEFu });
    ok("PostMessageW returns TRUE", r == 1);
    r = call(va_get, 4, (uint32_t[]){ msg, 0, 0, 0 });
    ok("PostMessageW message read by GetMessageW",
       r == 1 && MEM32(msg + 4) == MSG_CUSTOM &&
       MEM32(msg + 8) == 0x12345678u && MEM32(msg + 12) == 0xDEADBEEFu);

    /* 4. PeekMessageW: PM_NOREMOVE leaves it, PM_REMOVE takes it. */
    hle_post_message(0, MSG_PEEK, 1, 2);
    r = call(va_peek, 5, (uint32_t[]){ msg, 0, 0, 0, 0 });
    ok("PeekMessageW(PM_NOREMOVE) sees the message",
       r == 1 && MEM32(msg + 4) == MSG_PEEK);
    r = call(va_peek, 5, (uint32_t[]){ msg, 0, 0, 0, 1 });
    ok("PeekMessageW(PM_REMOVE) takes the message",
       r == 1 && MEM32(msg + 4) == MSG_PEEK);
    r = call(va_peek, 5, (uint32_t[]){ msg, 0, 0, 0, 1 });
    ok("PeekMessageW sees an empty queue afterwards", r == 0);

    /* 5. WM_QUIT: GetMessageW returns FALSE with the code in MSG.wParam. */
    r = call(va_post, 4, (uint32_t[]){ 0, WM_QUIT, 7, 0 });
    ok("PostMessageW(WM_QUIT) returns TRUE", r == 1);
    r = call(va_get, 4, (uint32_t[]){ msg, 0, 0, 0 });
    ok("GetMessageW returns FALSE for WM_QUIT", r == 0);
    ok("WM_QUIT is reported in the MSG",
       MEM32(msg + 4) == WM_QUIT && MEM32(msg + 8) == 7);

    /* 6. A Unicode class and window, then SendMessageW/DispatchMessageW reach
     * the guest window procedure with the right arguments and result. */
    static const uint16_t class_name[] = { 'M','s','g','T','e','s','t',0 };
    uint32_t name_va = hle_va(hle_alloc(sizeof class_name));
    memcpy(hle_ptr(name_va), class_name, sizeof class_name);
    uint32_t wc = hle_va(hle_alloc(48));                 /* WNDCLASSEXW */
    memset(hle_ptr(wc), 0, 48);
    MEM32(wc) = 48;                                      /* cbSize */
    MEM32(wc + 8) = VA_WNDPROC;                          /* lpfnWndProc */
    MEM32(wc + 20) = 0x00400000u;                        /* hInstance */
    MEM32(wc + 40) = name_va;                            /* lpszClassName (UTF-16) */
    uint32_t atom = call(va_reg, 1, &wc);
    ok("RegisterClassExW returns a class atom", atom >= 0xC000u);

    uint32_t hwnd = call(va_create, 12, (uint32_t[]){
        0, atom, 0, 0, 0, 0, 0, 0, 0, 0, 0x00400000u, 0 });
    ok("CreateWindowExW made a window", hwnd != 0);

    g_proc_calls = 0;
    r = call(va_send, 4, (uint32_t[]){ hwnd, MSG_CUSTOM, 0xA1u, 0xB2u });
    ok("SendMessageW returns the window procedure's result", r == 7);
    ok("SendMessageW reached the guest window procedure once", g_proc_calls == 1);
    ok("SendMessageW passed msg/wParam/lParam",
       g_proc_msg == MSG_CUSTOM && g_proc_w == 0xA1u && g_proc_l == 0xB2u);

    /* WM_SETTEXT carries UTF-16 in lParam; the shim must hand the proc ANSI. */
    static const uint16_t text[] = { 'H','i',0 };
    uint32_t text_va = hle_va(hle_alloc(sizeof text));
    memcpy(hle_ptr(text_va), text, sizeof text);
    g_proc_calls = 0;
    g_last_text[0] = 0;
    r = call(va_send, 4, (uint32_t[]){ hwnd, WM_SETTEXT, 0, text_va });
    ok("SendMessageW(WM_SETTEXT) returns the wndproc result", r == 7);
    ok("SendMessageW(WM_SETTEXT) reached the wndproc", g_proc_calls == 1);
    ok("SendMessageW(WM_SETTEXT) converted UTF-16 to ANSI",
       strcmp(g_last_text, "Hi") == 0);

    memset(hle_ptr(msg), 0, 28);
    MEM32(msg) = hwnd, MEM32(msg + 4) = MSG_CUSTOM + 1u, MEM32(msg + 8) = 1, MEM32(msg + 12) = 2;
    g_proc_calls = 0;
    r = call(va_dispatch, 1, &msg);
    ok("DispatchMessageW returns the window procedure's result", r == 7);
    ok("DispatchMessageW reached the wndproc with the MSG's message",
       g_proc_calls == 1 && g_proc_msg == MSG_CUSTOM + 1u);

    mach_leave();
    if (fails == 0) printf("user32_msg_selftest: all checks passed\n");
    return fails != 0;
}
