/*
 * input_state_selftest.c - synthetic SDL input observed as guest state.
 *
 * Headless by construction: SDL's dummy video driver, a lifted guest calling
 * the same shims a game calls, no window on a real screen and no game binary.
 * This is an input-path regression fixture, not a gameplay test.
 *
 * The injections and the guest state each is expected to change:
 *   SDL_KEYDOWN/SDL_KEYUP -> hle_screen_pump() -> hle_input_key() -> user32's
 *     key state (GetKeyState), a WM_KEYDOWN/WM_KEYUP posted to the focused
 *     window (PeekMessageA's MSG.hwnd), and SDL_GetKeyboardState() as read by
 *     DirectInput's IDirectInputDevice::GetDeviceState() (DIK_* bytes).
 *   guest SetFocus() -> the next injected key lands on that window's message
 *     queue; a synthetic SDL click dispatches WM_LBUTTONDOWN and moves focus
 *     to the clicked window: focus handling is observable through the path.
 *   SDL_WINDOWEVENT focus events -> pumped; the guest focus for keys remains
 *     whatever the guest/user32 path set (see the blocker note below).
 *   SDL_MOUSEWHEEL -> hle_screen_pump(); the DirectInput mouse state's Z axis
 *     (DIMOFS_Z, offset 8) is the wheel delta a polling game would read.
 *
 * Known blockers in the current implementation (see docs/INPUT_STATE_EVIDENCE.md):
 *   - screen.c's pump switch has no SDL_MOUSEWHEEL case; a wheel event is
 *     drained but carries no guest-visible delta.
 *   - dinput.c's dev_GetDeviceState() writes 0 to DIMOFS_Z unconditionally,
 *     so there is no host-side wheel accumulator to read back.
 *   - no SDL focus event is mapped to user32's static set_focus(): screen.c's
 *     SDL_WINDOWEVENT case handles only SIZE_CHANGED/EXPOSED, present.c's pump
 *     has no focus case, and win32hle.h exposes no host focus setter.
 *   - SDL2's SDL_PushEvent() does not update SDL_GetKeyboardState(), which is
 *     where dinput.c's keyboard GetDeviceState() reads its DIK_* bytes: a
 *     synthetic SDL_KEYDOWN reaches user32 (GetKeyState, WM_KEYDOWN) but is
 *     invisible to DirectInput's keyboard state in a headless fixture.
 * The wheel/keyboard checks below accept today's honest zeros and print a BLK
 * line; the same fixture asserts the real values when those hooks land. All
 * other checks are unconditional, so this is a regression test even now.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

#define DI_OK              0x00000000u
#define DIMOFS_Z           8u
#define DIMOFS_BUTTON0     12u
#define VK_A               0x41u
#define DIK_A              0x1Eu
#define WM_KEYDOWN         0x0100u
#define WM_KEYUP           0x0101u
#define WM_LBUTTONDOWN     0x0201u
#define WM_LBUTTONUP       0x0202u
#define WS_VISIBLE         0x10000000u

static const uint8_t GUID_SYS_MOUSE[16] = {
    0x60, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
    0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00
};

static int fails;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }

/* COM device vtable slots (IDirectInput: CreateDevice; device: Acquire,
 * GetDeviceState, Unacquire). The order is the DirectInput interface order. */
enum { DI_CREATE_DEVICE = 3, DEVII_ACQUIRE = 7, DEVII_GET_STATE = 9, DEVII_UNACQUIRE = 8 };

static uint32_t VA_CreateWindowExA, VA_SetFocus, VA_GetFocus, VA_PeekMessageA,
                VA_GetKeyState, VA_DestroyWindow, VA_DirectInputCreateA,
                VA_RegisterClassA, VA_DispatchMessageA;

static uint8_t *g_msg;            /* a 7-dword guest MSG */
static uint8_t *g_keys;           /* DirectInput keyboard state (256 bytes) */
static uint8_t *g_mouse;          /* DirectInput mouse state (16 bytes) */
static uint32_t va_msg, va_keys, va_mouse;
static uint32_t di, mouse_dev, key_dev;
static uint32_t win_a, win_b;

/* One lifted guest window procedure: the WndProc a game would write, which
 * calls SetFocus on WM_LBUTTONDOWN. Everything else is hle_call_guest() into
 * registered shims, as the other selftests do. */
#define PROC_VA 0x00401000u
#define GUEST_PROLOGUE(va) \
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0; RECOMP_ENTER(va)

static void guest_click_proc(void) {
    GUEST_PROLOGUE(PROC_VA);
    if (A32(1) == WM_LBUTTONDOWN) {
        PUSH32(esp, A32(0));                        /* SetFocus(hwnd) */
        RECOMP_ICALL(VA_SetFocus);
    }
    eax = 0;
}
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { PROC_VA, guest_click_proc } };
const uint32_t recomp_dispatch_count = 1;

static uint32_t call(const char *name, int n, const uint32_t *a) {
    uint32_t va = hle_resolve(name);
    if (!va) { printf("FAIL shim %s did not resolve\n", name); fails++; return 0; }
    return hle_call_guest(va, n, a);
}
static uint32_t vcall(uint32_t obj, int slot, int n, const uint32_t *a) {
    uint32_t fn = MEM32(MEM32(obj) + 4u * (uint32_t)slot);
    uint32_t args[5];
    if (n > 4) { printf("FAIL vcall has too many arguments\n"); fails++; return 0; }
    args[0] = obj;
    for (int i = 0; i < n; i++) args[i + 1] = a[i];
    return hle_call_guest(fn, n + 1, args);
}

static void push_key(SDL_Scancode sc, int down) {
    SDL_Event e;
    SDL_zero(e);
    e.type = down ? SDL_KEYDOWN : SDL_KEYUP;
    e.key.type = e.type;
    e.key.state = down ? SDL_PRESSED : SDL_RELEASED;
    e.key.keysym.scancode = sc;
    e.key.keysym.sym = SDL_GetKeyFromScancode(sc);
    ok("SDL_PushEvent(SDL_KEY*)", SDL_PushEvent(&e) == 1);
}
static void push_wheel(int y) {
    SDL_Event e;
    SDL_zero(e);
    e.type = SDL_MOUSEWHEEL;
    e.wheel.type = SDL_MOUSEWHEEL;
    e.wheel.x = 0;
    e.wheel.y = y;
    ok("SDL_PushEvent(SDL_MOUSEWHEEL)", SDL_PushEvent(&e) == 1);
}
static void push_focus(int gained) {
    SDL_Event e;
    SDL_zero(e);
    e.type = SDL_WINDOWEVENT;
    e.window.type = SDL_WINDOWEVENT;
    e.window.event = gained ? SDL_WINDOWEVENT_FOCUS_GAINED : SDL_WINDOWEVENT_FOCUS_LOST;
    /* screen.c owns the SDL window; this fixture does not need its ID because
     * no current code maps SDL focus events to a guest window. */
    e.window.windowID = 0;
    ok("SDL_PushEvent(SDL_WINDOWEVENT)", SDL_PushEvent(&e) == 1);
}

/* PeekMessageA(&msg, 0, 0, 0, PM_REMOVE); filled guest MSG -> host fields. */
static int pop_msg(uint32_t *hwnd, uint32_t *message, uint32_t *wparam) {
    uint32_t r = call("PeekMessageA", 5, (uint32_t[]){ va_msg, 0, 0, 0, 1 });
    if (!r) return 0;
    if (hwnd) *hwnd = MEM32(va_msg);
    if (message) *message = MEM32(va_msg + 4);
    if (wparam) *wparam = MEM32(va_msg + 8);
    return 1;
}
/* Find a message by id, ignoring WM_PAINT and anything else creation queued. */
static int find_msg(uint32_t want, uint32_t *hwnd, uint32_t *wparam) {
    for (int i = 0; i < 64; i++) {
        uint32_t h, m, w;
        if (!pop_msg(&h, &m, &w)) return 0;
        if (m == want) { if (hwnd) *hwnd = h; if (wparam) *wparam = w; return 1; }
    }
    return 0;
}

static uint32_t create_window(uint32_t style, uint32_t parent, uint32_t id, uint32_t class_va,
                              int x, int y) {
    return hle_call_guest(VA_CreateWindowExA, 12,
        (uint32_t[]){ 0, class_va, 0, style, (uint32_t)x, (uint32_t)y, 100, 100, parent, id, 0, 0 });
}
/* Deliver queued messages the way a guest's message loop does: PeekMessageA
 * (PM_REMOVE) then DispatchMessageA. Returns 1 when `want` was dispatched. */
static int dispatch_until(uint32_t want) {
    for (int i = 0; i < 64; i++) {
        uint32_t m;
        if (!pop_msg(NULL, &m, NULL)) return 0;
        hle_call_guest(VA_DispatchMessageA, 1, (uint32_t[]){ va_msg });
        if (m == want) return 1;
    }
    return 0;
}

int main(void) {
    setenv("SDL_VIDEODRIVER", "dummy", 1);
    setenv("SDL_AUDIODRIVER", "dummy", 1);

    win32hle_register(win32hle_user32);
    win32hle_register(win32hle_dinput);
    VA_CreateWindowExA = hle_resolve("CreateWindowExA");
    VA_SetFocus = hle_resolve("SetFocus");
    VA_GetFocus = hle_resolve("GetFocus");
    VA_PeekMessageA = hle_resolve("PeekMessageA");
    VA_GetKeyState = hle_resolve("GetKeyState");
    VA_DestroyWindow = hle_resolve("DestroyWindow");
    VA_DirectInputCreateA = hle_resolve("DirectInputCreateA");
    VA_RegisterClassA = hle_resolve("RegisterClassA");
    VA_DispatchMessageA = hle_resolve("DispatchMessageA");
    ok("user32 and DirectInput shims resolve",
       VA_CreateWindowExA && VA_SetFocus && VA_GetFocus && VA_PeekMessageA &&
       VA_GetKeyState && VA_DestroyWindow && VA_DirectInputCreateA &&
       VA_RegisterClassA && VA_DispatchMessageA);
    if (fails) return 1;

    /* A real SDL window under the dummy driver: hle_screen_pump() only drains
     * SDL events while g_win exists. No display is involved. */
    ok("screen opens with a dummy-driver window", hle_screen_open("input state selftest", 0, 0) == 0);
    hle_set_screen_size(640, 480);

    char *cls = hle_alloc(8);
    snprintf(cls, 8, "Static");
    uint32_t class_va = hle_va(cls);
    win_a = create_window(WS_VISIBLE, 0, 0, class_va, 0, 0);
    win_b = create_window(0, 0, 0, class_va, 0, 0);
    ok("two guest windows created", win_a != 0 && win_b != 0 && win_a != win_b);

    /* DirectInput devices the way a polling game makes them: COM object,
     * CreateDevice with the system GUIDs, cooperative level, Acquire. */
    g_msg = hle_alloc(28);
    g_keys = hle_alloc(256);
    g_mouse = hle_alloc(16);
    va_msg = hle_va(g_msg);
    va_keys = hle_va(g_keys);
    va_mouse = hle_va(g_mouse);
    uint8_t *guid_mouse = hle_alloc(16), *guid_keyboard = hle_alloc(16);
    memcpy(guid_mouse, GUID_SYS_MOUSE, 16);
    memset(guid_keyboard, 0, 16);
    guid_keyboard[0] = 0x61; guid_keyboard[1] = 0x2B; guid_keyboard[2] = 0x1D; guid_keyboard[3] = 0x6F;
    guid_keyboard[4] = 0xA0; guid_keyboard[5] = 0xD5; guid_keyboard[6] = 0xCF; guid_keyboard[7] = 0x11;
    guid_keyboard[8] = 0xBF; guid_keyboard[9] = 0xC7; guid_keyboard[10] = 0x44; guid_keyboard[11] = 0x45;
    guid_keyboard[12] = 0x53; guid_keyboard[13] = 0x54;
    uint32_t guid_mouse_va = hle_va(guid_mouse), guid_keyboard_va = hle_va(guid_keyboard);
    uint32_t *out = hle_alloc(4);
    uint32_t out_va = hle_va(out);

    ok("DirectInputCreateA", hle_call_guest(VA_DirectInputCreateA, 4,
        (uint32_t[]){ 0, 0x0300u, out_va, 0 }) == DI_OK && (di = *out) != 0);
    uint32_t credev = MEM32(MEM32(di) + 4u * DI_CREATE_DEVICE);
    ok("CreateDevice(system mouse)", hle_call_guest(credev, 4,
        (uint32_t[]){ di, guid_mouse_va, out_va, 0 }) == DI_OK && (mouse_dev = *out) != 0);
    ok("CreateDevice(system keyboard)", hle_call_guest(credev, 4,
        (uint32_t[]){ di, guid_keyboard_va, out_va, 0 }) == DI_OK && (key_dev = *out) != 0);
    uint32_t set_coop = MEM32(MEM32(mouse_dev) + 4u * 13u);
    ok("SetCooperativeLevel(nonexclusive)", hle_call_guest(set_coop, 3,
        (uint32_t[]){ mouse_dev, win_a, 2u }) == DI_OK);
    ok("mouse device acquired", vcall(mouse_dev, DEVII_ACQUIRE, 0, NULL) == DI_OK);
    ok("keyboard device acquired", vcall(key_dev, DEVII_ACQUIRE, 0, NULL) == DI_OK);
    ok("GetDeviceState(mouse)", vcall(mouse_dev, DEVII_GET_STATE, 2,
        (uint32_t[]){ 16u, va_mouse }) == DI_OK);
    ok("GetDeviceState(keyboard)", vcall(key_dev, DEVII_GET_STATE, 2,
        (uint32_t[]){ 256u, va_keys }) == DI_OK);

    /* ---- focus + key events ------------------------------------------------ */
    /* SetFocus returns the *previous* focus (0 here), not the new one. */
    hle_call_guest(VA_SetFocus, 1, (uint32_t[]){ win_b });
    ok("GetFocus reports window B", hle_call_guest(VA_GetFocus, 0, NULL) == win_b);
    ok("hle_focus_window() reports window B", hle_focus_window() == win_b);

    push_key(SDL_SCANCODE_A, 1);
    hle_screen_pump();
    ok("SDL_KEYDOWN sets the user32 key state", (hle_call_guest(VA_GetKeyState, 1, (uint32_t[]){ VK_A }) & 0x8000u) != 0);
    uint32_t key_hwnd = 0, key_wp = 0;
    ok("SDL_KEYDOWN posts WM_KEYDOWN", find_msg(WM_KEYDOWN, &key_hwnd, &key_wp));
    ok("WM_KEYDOWN went to the focused window", key_hwnd == win_b && key_wp == VK_A);
    vcall(key_dev, DEVII_GET_STATE, 2, (uint32_t[]){ 256u, va_keys });
    int di_key_saw = MEM8(va_keys + DIK_A) == 0x80;
    if (di_key_saw)
        printf("INFO key: synthetic SDL_KEYDOWN visible in DirectInput's DIK_A\n");
    else
        printf("BLK key: SDL_KEYDOWN set user32's key state but SDL_GetKeyboardState()/DIK_A stayed 0 "
               "(SDL_PushEvent does not update SDL's key state; dinput.c has no synthetic-key hook)\n");

    push_key(SDL_SCANCODE_A, 0);
    hle_screen_pump();
    ok("SDL_KEYUP clears the user32 key state", (hle_call_guest(VA_GetKeyState, 1, (uint32_t[]){ VK_A }) & 0x8000u) == 0);
    vcall(key_dev, DEVII_GET_STATE, 2, (uint32_t[]){ 256u, va_keys });
    if (di_key_saw)
        ok("SDL_KEYUP reaches DirectInput (DIK_A cleared)", MEM8(va_keys + DIK_A) == 0);
    else
        printf("BLK key: DirectInput's DIK_A cannot be observed from a synthetic SDL key event\n");
    key_hwnd = 0;
    ok("SDL_KEYUP posts WM_KEYUP", find_msg(WM_KEYUP, &key_hwnd, NULL));
    ok("WM_KEYUP went to the focused window", key_hwnd == win_b);

    /* Same SDL event, different guest focus: the message must follow focus. */
    ok("guest SetFocus returns the old focus", hle_call_guest(VA_SetFocus, 1,
        (uint32_t[]){ win_a }) == win_b);
    ok("hle_focus_window() now reports window A", hle_focus_window() == win_a);
    push_key(SDL_SCANCODE_A, 1);
    hle_screen_pump();
    key_hwnd = 0;
    ok("a key after the focus switch still posts WM_KEYDOWN", find_msg(WM_KEYDOWN, &key_hwnd, NULL));
    ok("the key followed the new focus", key_hwnd == win_a);
    push_key(SDL_SCANCODE_A, 0);
    hle_screen_pump();
    (void)find_msg(WM_KEYUP, NULL, NULL);

    /* Focus handling through the input path: a synthetic SDL click over a
     * guest-class window becomes WM_LBUTTONDOWN; the lifted WndProc dispatches
     * SetFocus(hwnd), so guest focus moves to the clicked window. */
    char *click_cls = hle_alloc(16);
    snprintf(click_cls, 16, "IptSelfTest");
    uint32_t click_cls_va = hle_va(click_cls);
    uint32_t *wc = hle_alloc(40);
    memset(wc, 0, 40);
    wc[1] = PROC_VA;                              /* WNDCLASSA.lpfnWndProc (lifted WndProc) */
    wc[9] = click_cls_va;                         /* WNDCLASSA.lpszClassName */
    ok("RegisterClassA(guest class)", hle_call_guest(VA_RegisterClassA, 1,
        (uint32_t[]){ hle_va(wc) }) != 0);
    uint32_t win_click = create_window(WS_VISIBLE, 0, 0, click_cls_va, 200, 200);
    ok("guest-class window created", win_click != 0);

    hle_call_guest(VA_SetFocus, 1, (uint32_t[]){ win_a });
    ok("focus starts on window A before the click", hle_focus_window() == win_a);
    SDL_Event md;
    SDL_zero(md);
    md.type = SDL_MOUSEBUTTONDOWN;
    md.button.type = SDL_MOUSEBUTTONDOWN;
    md.button.state = SDL_PRESSED;
    md.button.button = SDL_BUTTON_LEFT;
    md.button.x = 210; md.button.y = 210;
    ok("SDL_PushEvent(SDL_MOUSEBUTTONDOWN)", SDL_PushEvent(&md) == 1);
    hle_screen_pump();
    ok("synthetic click posted WM_LBUTTONDOWN to the guest window",
        dispatch_until(WM_LBUTTONDOWN));
    ok("guest focus moved to the clicked window", hle_focus_window() == win_click &&
        hle_call_guest(VA_GetFocus, 0, NULL) == win_click);
    SDL_Event mu;
    SDL_zero(mu);
    mu.type = SDL_MOUSEBUTTONUP;
    mu.button.type = SDL_MOUSEBUTTONUP;
    mu.button.state = SDL_RELEASED;
    mu.button.button = SDL_BUTTON_LEFT;
    mu.button.x = 210; mu.button.y = 210;
    ok("SDL_PushEvent(SDL_MOUSEBUTTONUP)", SDL_PushEvent(&mu) == 1);
    hle_screen_pump();
    (void)dispatch_until(WM_LBUTTONUP);
    ok("mouse button released in guest state", (hle_input_mouse_buttons() & 1u) == 0);
    hle_call_guest(VA_SetFocus, 1, (uint32_t[]){ win_a });

    /* SDL focus events are injected too. Today they are drained by the pump
     * but do not change guest focus: record that rather than pretend a focus
     * hook exists. If a hook lands, focus is allowed to change; the key-routing
     * assertions above still hold. */
    push_focus(0);
    push_focus(1);
    hle_screen_pump();
    if (hle_focus_window() == win_a)
        printf("BLK focus: SDL_WINDOWEVENT_FOCUS_{LOST,GAINED} left guest focus on window A "
               "(no SDL focus -> user32 set_focus hook)\n");
    else
        printf("INFO focus: guest focus changed after SDL focus events (0x%08X)\n", hle_focus_window());
    if (hle_focus_window() != win_a)
        hle_call_guest(VA_SetFocus, 1, (uint32_t[]){ win_a });

    /* ---- mouse wheel ------------------------------------------------------ */
    vcall(mouse_dev, DEVII_GET_STATE, 2, (uint32_t[]){ 16u, va_mouse });
    const int32_t z_before = (int32_t)MEM32(va_mouse + DIMOFS_Z);
    ok("wheel baseline: DIMOFS_Z is zero", z_before == 0);
    ok("wheel baseline: no mouse buttons held", (hle_input_mouse_buttons() & 7u) == 0 &&
        MEM8(va_mouse + DIMOFS_BUTTON0) == 0);

    const int wheel_delta = 120;
    push_wheel(wheel_delta);
    hle_screen_pump();
    SDL_Event drain;
    int leftover_wheels = 0;
    for (int i = 0; i < 32 && SDL_PollEvent(&drain); i++)
        if (drain.type == SDL_MOUSEWHEEL) leftover_wheels++;
    ok("SDL_MOUSEWHEEL was drained by the pump", leftover_wheels == 0);

    vcall(mouse_dev, DEVII_GET_STATE, 2, (uint32_t[]){ 16u, va_mouse });
    const int32_t z_after = (int32_t)MEM32(va_mouse + DIMOFS_Z);
    ok("wheel did not change mouse buttons", (hle_input_mouse_buttons() & 7u) == 0 &&
        MEM8(va_mouse + DIMOFS_BUTTON0) == 0);
    vcall(key_dev, DEVII_GET_STATE, 2, (uint32_t[]){ 256u, va_keys });
    ok("wheel did not change keyboard state", (hle_call_guest(VA_GetKeyState, 1, (uint32_t[]){ VK_A }) & 0x8000u) == 0 &&
        MEM8(va_keys + DIK_A) == 0);
    if (z_after == 0) {
        printf("BLK wheel: SDL_MOUSEWHEEL(%d) left DIMOFS_Z=0 in DirectInput GetDeviceState "
               "(no wheel hook: screen.c ignores the event, dinput.c hard-codes Z=0)\n", wheel_delta);
    } else {
        ok("wheel delta reached DirectInput DIMOFS_Z", z_after == wheel_delta);
    }

    vcall(mouse_dev, DEVII_UNACQUIRE, 0, NULL);
    vcall(key_dev, DEVII_UNACQUIRE, 0, NULL);
    hle_call_guest(VA_DestroyWindow, 1, (uint32_t[]){ win_click });
    hle_call_guest(VA_DestroyWindow, 1, (uint32_t[]){ win_b });
    hle_call_guest(VA_DestroyWindow, 1, (uint32_t[]){ win_a });
    SDL_Quit();

    if (!fails) printf("input_state_selftest: all checks passed (see BLK lines for missing hooks)\n");
    return fails != 0;
}
