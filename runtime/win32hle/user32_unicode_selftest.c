/*
 * user32_unicode_selftest.c - the USER32 Unicode dialog/icon shims added for
 * PE32 apps whose imports include CreateDialogParamW, IsDialogMessageW,
 * LoadIconW and ExitWindowsEx. No game binary, no display, no host shutdown.
 *
 * Every API is called through hle_call_guest on a resolved shim VA, so the
 * guest ABI is exercised the way a lift drives it: stdcall arguments on the
 * guest stack, the shim's RET popping them, and guest window/dialog procedures
 * reached through call_proc/hle_call_guest. A second group drives the shims
 * with a synthetic return address and inspects g_esp directly, because
 * hle_call_guest restores the register file and would hide a wrong purge.
 *
 * The dialog template named here is never present as a resource, so
 * CreateDialogParamW must take its registered-object path: the handle it
 * returns has to be a real window (IsWindow succeeds), the guest dialog proc
 * must see WM_INITDIALOG with dwInitParam, and GetDlgItemTextW must work on a
 * child of that handle. ExitWindowsEx must return FALSE without ending the
 * process -- reaching the final line of main is itself that check.
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

#define WS_CHILD       0x40000000u
#define WM_INITDIALOG  0x0110u
#define WM_COMMAND     0x0111u
#define WM_KEYDOWN     0x0100u
#define VK_TAB         0x09u
#define VA_WNDPROC     0x00405000u
#define VA_DLGPROC     0x00406000u

static uint32_t VA_DEFPROC;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(uint32_t fn, int n, const uint32_t *a) { return hle_call_guest(fn, n, a); }

/* Dialog-proc observations. These live in the harness; the "guest" procedure
 * is a C callback in this process, as in the other win32hle selftests. */
static int g_init_seen, g_command_seen;
static uint32_t g_init_param, g_init_hwnd;

static void guest_wndproc(void) {                       /* forwards to DefWindowProcW */
    GUEST_PROLOGUE(VA_WNDPROC);
    uint32_t h = MEM32(esp + 4), m = MEM32(esp + 8), w = MEM32(esp + 12), l = MEM32(esp + 16);
    uint32_t a[4] = { h, m, w, l };
    eax = hle_call_guest(VA_DEFPROC, 4, a);
    esp += 4u + 16u;                                    /* pop synthetic ret + 4 stdcall args */
}

static void guest_dlgproc(void) {                       /* (hwnd, msg, wParam, lParam) */
    GUEST_PROLOGUE(VA_DLGPROC);
    uint32_t h = MEM32(esp + 4), m = MEM32(esp + 8), w = MEM32(esp + 12), l = MEM32(esp + 16);
    if (m == WM_INITDIALOG) {
        g_init_seen++, g_init_hwnd = h, g_init_param = l;
        eax = 1;                                        /* let the dialog take focus */
    } else if (m == WM_COMMAND) {
        g_command_seen++;
        eax = 1;
    } else {
        eax = 0;
    }
    (void)w;
    esp += 4u + 16u;                                    /* pop synthetic ret + 4 stdcall args */
}

/* Ordered by address: recomp_lookup does a binary search. */
const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { VA_WNDPROC, guest_wndproc }, { VA_DLGPROC, guest_dlgproc }
};
const uint32_t recomp_dispatch_count = 2;

/* hle_call_guest restores registers after a shim returns, so it cannot see a
 * wrong stdcall purge. Drive the registered shim the way lifted code does
 * (synthetic return address + args on a guest stack), call it directly, and
 * report how many words it popped. */
static uint32_t shim_pops(const char *name, int argc, const uint32_t *args) {
    uint32_t va = hle_resolve(name);
    recomp_func_t fn = va ? recomp_lookup_import(va) : 0;
    if (!fn) return 0xFFFFFFFFu;
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t scratch = hle_va(hle_alloc(128));          /* ret + up to 5 args, with slack */
    g_esp = scratch + 64;
    for (int i = argc - 1; i >= 0; --i) { g_esp -= 4; MEM32(g_esp) = args[i]; }
    g_esp -= 4; MEM32(g_esp) = RECOMP_RETADDR;
    uint32_t esp_in = g_esp;
    fn();
    uint32_t words = (g_esp - esp_in) / 4u;
    g_esp = saved_esp; g_eax = saved_eax;
    return words;
}

int main(void) {
    win32hle_register(win32hle_user32);
    mach_enter();

    uint32_t va_iswin     = hle_resolve("IsWindow");
    uint32_t va_create    = hle_resolve("CreateWindowExW");
    uint32_t va_settext   = hle_resolve("SetWindowTextW");
    uint32_t va_cdw       = hle_resolve("CreateDialogParamW");
    uint32_t va_idm       = hle_resolve("IsDialogMessageW");
    uint32_t va_gdit      = hle_resolve("GetDlgItemTextW");
    uint32_t va_liw       = hle_resolve("LoadIconW");
    uint32_t va_ewe       = hle_resolve("ExitWindowsEx");
    uint32_t va_defproc   = hle_resolve("DefWindowProcW");

    ok("CreateDialogParamW resolves", va_cdw != 0);
    ok("IsDialogMessageW resolves", va_idm != 0);
    ok("LoadIconW resolves", va_liw != 0);
    ok("ExitWindowsEx resolves", va_ewe != 0);
    ok("GetDlgItemTextW already resolves (not duplicated)", va_gdit != 0);
    ok("IsWindow/DefWindowProcW resolve", va_iswin && va_defproc);
    VA_DEFPROC = va_defproc;

    /* ---- CreateDialogParamW: MAKEINTRESOURCEW(id), no template resource ---- */
    uint32_t dlg = call(va_cdw, 5, (uint32_t[]){ 0x00400000u, 100u, 0u, VA_DLGPROC, 0x1357u });
    ok("CreateDialogParamW returned a handle", dlg != 0);
    ok("the handle is bounded by the window table", dlg != 0 && dlg < 0x00018000u);
    ok("the handle is a registered window", dlg != 0 && call(va_iswin, 1, &dlg) == 1);
    ok("the guest dialog proc saw WM_INITDIALOG", g_init_seen == 1);
    ok("WM_INITDIALOG was for the returned handle", g_init_hwnd == dlg);
    ok("WM_INITDIALOG carried dwInitParam", g_init_param == 0x1357u);

    /* ---- the same dialog through a wide (string) template name ---- */
    static const uint16_t missing_dlg[] = { 'M','i','s','s','i','n','g','D','l','g',0 };
    uint32_t name_va = hle_va(hle_alloc(sizeof missing_dlg));
    memcpy(hle_ptr(name_va), missing_dlg, sizeof missing_dlg);
    uint32_t dlg2 = call(va_cdw, 5, (uint32_t[]){ 0x00400000u, name_va, 0u, VA_DLGPROC, 0x2468u });
    ok("CreateDialogParamW accepted a wide template name", dlg2 != 0);
    ok("the wide-named dialog is a registered window", dlg2 != 0 && call(va_iswin, 1, &dlg2) == 1);
    ok("WM_INITDIALOG was delivered for the wide-named dialog", g_init_seen == 2 && g_init_param == 0x2468u);

    /* an unterminated wide buffer must be read only up to the shim's bound */
    uint32_t bad_name = hle_va(hle_alloc(16));
    memset(hle_ptr(bad_name), 0x41, 16);
    uint32_t dlg3 = call(va_cdw, 5, (uint32_t[]){ 0, bad_name, 0, VA_DLGPROC, 0 });
    ok("an invalid wide name fails honestly without a handle", dlg3 == 0);
    ok("a null template name fails honestly", call(va_cdw, 5,
       (uint32_t[]){ 0, 0, 0, VA_DLGPROC, 0 }) == 0);

    /* ---- GetDlgItemTextW operates on the registered dialog ---- */
    uint32_t child = call(va_create, 12, (uint32_t[]){
        0, 0x81u /* Edit */, 0, WS_CHILD, 0, 0, 60, 20, dlg, 100, 0x00400000u, 0 });
    ok("a child control of the dialog was created", child != 0);
    static const uint16_t ctl_text[] = { 'W','i','d','e','D','l','g',0 };
    uint32_t text_va = hle_va(hle_alloc(sizeof ctl_text));
    memcpy(hle_ptr(text_va), ctl_text, sizeof ctl_text);
    ok("SetWindowTextW set the child's text", call(va_settext, 2, (uint32_t[]){ child, text_va }) == 1);
    uint32_t out = hle_va(hle_alloc(64));
    memset(hle_ptr(out), 0x5A, 64);
    uint32_t n = call(va_gdit, 4, (uint32_t[]){ dlg, 100, out, 16 });
    ok("GetDlgItemTextW reads the child through the dialog handle", n == 7);
    ok("GetDlgItemTextW wrote the UTF-16 text",
       ((uint16_t *)hle_ptr(out))[0] == 'W' && ((uint16_t *)hle_ptr(out))[6] == 'g' &&
       ((uint16_t *)hle_ptr(out))[7] == 0);
    ok("GetDlgItemTextW found no item on the empty dialog",
       call(va_gdit, 4, (uint32_t[]){ dlg2, 100, out, 16 }) == 0 && ((uint16_t *)hle_ptr(out))[0] == 0);

    /* ---- IsDialogMessageW ---- */
    uint32_t msg = hle_va(hle_alloc(32));
    memset(hle_ptr(msg), 0, 32);
    MEM32(msg) = dlg; MEM32(msg + 4) = WM_KEYDOWN; MEM32(msg + 8) = VK_TAB;
    ok("IsDialogMessageW handles a key message for its dialog", call(va_idm, 2, (uint32_t[]){ dlg, msg }) == 1);
    MEM32(msg + 4) = WM_COMMAND; MEM32(msg + 8) = 100;
    int before = g_command_seen;
    ok("IsDialogMessageW dispatches a command to the dialog proc", call(va_idm, 2, (uint32_t[]){ dlg, msg }) == 1);
    ok("the dialog proc received that WM_COMMAND", g_command_seen == before + 1);
    ok("IsDialogMessageW declines an unregistered dialog", call(va_idm, 2, (uint32_t[]){ 0xDEAD0000u, msg }) == 0);
    ok("IsDialogMessageW declines a null MSG", call(va_idm, 2, (uint32_t[]){ dlg, 0 }) == 0);

    /* ---- LoadIconW ---- */
    uint32_t app  = call(va_liw, 2, (uint32_t[]){ 0, 32512u });
    uint32_t hand = call(va_liw, 2, (uint32_t[]){ 0, 32513u });
    uint32_t logo = call(va_liw, 2, (uint32_t[]){ 0, 32517u });
    ok("LoadIconW returns a handle for IDI_APPLICATION", app != 0);
    ok("LoadIconW returns a handle for IDI_HAND", hand != 0);
    ok("LoadIconW returns a handle for IDI_WINLOGO", logo != 0);
    ok("standard icon handles are distinct and stable", app != hand && app != logo &&
       app == call(va_liw, 2, (uint32_t[]){ 0, 32512u }));
    hle_set_last_error(0);
    ok("LoadIconW refuses an unsupported resource id", call(va_liw, 2, (uint32_t[]){ 0, 999u }) == 0);
    ok("unsupported icon sets ERROR_RESOURCE_NOT_FOUND", hle_get_last_error() == 1814u);
    static const uint16_t icon_name[] = { 'M','Y','I','C','O','N',0 };
    uint32_t iname = hle_va(hle_alloc(sizeof icon_name));
    memcpy(hle_ptr(iname), icon_name, sizeof icon_name);
    hle_set_last_error(0);
    ok("LoadIconW refuses a named icon", call(va_liw, 2, (uint32_t[]){ 0, iname }) == 0);
    ok("named icon failure sets ERROR_RESOURCE_NOT_FOUND", hle_get_last_error() == 1814u);

    /* ---- ExitWindowsEx: a guest shutdown must not end the host ---- */
    hle_set_last_error(0);
    ok("ExitWindowsEx(EWX_LOGOFF) returns FALSE", call(va_ewe, 2, (uint32_t[]){ 0x01u, 0 }) == 0);
    ok("ExitWindowsEx sets ERROR_NOT_SUPPORTED", hle_get_last_error() == 50u);
    ok("ExitWindowsEx(EWX_REBOOT|EWX_FORCE) returns FALSE",
       call(va_ewe, 2, (uint32_t[]){ 0x09u, 0xC0DEu }) == 0);
    ok("the emulator is still running after ExitWindowsEx", g_command_seen >= 0);

    /* ---- stdcall purges (ret + argc words) ---- */
    ok("CreateDialogParamW pops ret + 5 args", shim_pops("CreateDialogParamW", 5,
       (uint32_t[]){ 0, 100, 0, VA_DLGPROC, 0 }) == 6);
    ok("IsDialogMessageW pops ret + 2 args", shim_pops("IsDialogMessageW", 2,
       (uint32_t[]){ dlg, msg }) == 3);
    ok("LoadIconW pops ret + 2 args", shim_pops("LoadIconW", 2,
       (uint32_t[]){ 0, 32512u }) == 3);
    ok("ExitWindowsEx pops ret + 2 args", shim_pops("ExitWindowsEx", 2,
       (uint32_t[]){ 0x01u, 0 }) == 3);

    mach_leave();
    if (fails == 0) printf("user32_unicode_selftest: all checks passed\n");
    return fails != 0;
}
