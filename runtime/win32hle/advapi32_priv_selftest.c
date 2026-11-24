/* advapi32_priv_selftest.c - privilege LUID lookup, token privilege
 * adjustment and SHGetFolderPathW, driven through the registered guest ABI.
 *
 * Focused selftest: only the win32hle core plus advapi32.c and the two helper
 * modules it needs are linked (see the build command recorded in the commit
 * message / report). No live game, Windows oracle or 100% claim is implied. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#define ERROR_INVALID_HANDLE    6u
#define ERROR_SUCCESS           0u
#define ERROR_INSUFFICIENT_BUFFER 122u
#define ERROR_NOT_ALL_ASSIGNED  1300u
#define ERROR_NO_SUCH_PRIVILEGE 1313u
#define ERROR_INVALID_PARAMETER 87u
#define E_INVALIDARG            0x80070057u
#define SE_PRIVILEGE_ENABLED    0x0002u
#define SE_PRIVILEGE_REMOVED    0x0004u
#define TOKEN                   0xCAFE0001u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int argc, const uint32_t *args) {
    uint32_t va = hle_resolve(name);
    return va ? hle_call_guest(va, argc, args) : 0xFFFFFFFFu;
}
/* hle_call_guest restores the register file, so it hides a wrong stdcall
 * purge. Drive the shim directly with a synthetic guest stack and count the
 * words it pops, exactly like d3d9_selftest's shim_pops. */
static uint32_t shim_pops(const char *name, int argc, const uint32_t *args,
                          uint32_t *out_eax) {
    uint32_t va = hle_resolve(name);
    recomp_func_t fn = va ? recomp_lookup_import(va) : 0;
    if (!fn) return 0xFFFFFFFFu;
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t scratch = hle_va(hle_alloc(64));        /* ret + up to 7 args */
    g_esp = scratch + 32;
    for (int i = argc - 1; i >= 0; --i) { g_esp -= 4; MEM32(g_esp) = args[i]; }
    g_esp -= 4; MEM32(g_esp) = RECOMP_RETADDR;
    uint32_t esp_in = g_esp;
    fn();
    uint32_t words = (g_esp - esp_in) / 4u;
    if (out_eax) *out_eax = g_eax;
    g_esp = saved_esp; g_eax = saved_eax;
    return words;
}
static uint32_t wide_guest(const char *s) {
    size_t n = strlen(s) + 1;
    uint32_t va = hle_va(hle_alloc((uint32_t)n * 2u));
    uint16_t *w = (uint16_t *)hle_ptr(va);
    for (size_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)s[i];
    return va;
}
static void narrow(const uint16_t *w, char *out, size_t cap) {
    size_t i = 0;
    for (; w[i] && i + 1 < cap; i++) out[i] = (char)w[i];
    out[i] = 0;
}
/* Build a TOKEN_PRIVILEGES in guest memory with one LUID + attributes. */
static uint32_t token_privs(uint32_t luid_low, uint32_t attrs) {
    uint32_t va = hle_va(hle_alloc(16));
    MEM32(va) = 1;                                   /* PrivilegeCount */
    MEM32(va + 4u) = luid_low;                       /* LUID.LowPart */
    MEM32(va + 8u) = 0;                              /* LUID.HighPart */
    MEM32(va + 12u) = attrs;                         /* Attributes */
    return va;
}

int main(void) {
    win32hle_register(win32hle_advapi32);
    mach_enter();

    /* --- registration --- */
    ok("LookupPrivilegeValueW registered", hle_resolve("LookupPrivilegeValueW") != 0);
    ok("AdjustTokenPrivileges registered", hle_resolve("AdjustTokenPrivileges") != 0);
    ok("SHGetFolderPathW registered", hle_resolve("SHGetFolderPathW") != 0);

    /* --- LookupPrivilegeValueW --- */
    uint32_t luid = hle_va(hle_alloc(8));
    uint32_t nDebug = wide_guest("SeDebugPrivilege");
    ok("SeDebugPrivilege -> LUID 20", call("LookupPrivilegeValueW", 3,
       (uint32_t[]){ 0, nDebug, luid }) == 1 && MEM32(luid) == 20u && MEM32(luid + 4u) == 0u);
    ok("case-insensitive name", call("LookupPrivilegeValueW", 3,
       (uint32_t[]){ 0, wide_guest("sebackupprivilege"), luid }) == 1 && MEM32(luid) == 17u);
    ok("SeRestorePrivilege -> LUID 18", call("LookupPrivilegeValueW", 3,
       (uint32_t[]){ 0, wide_guest("SeRestorePrivilege"), luid }) == 1 && MEM32(luid) == 18u);
    ok("SeShutdownPrivilege -> LUID 19", call("LookupPrivilegeValueW", 3,
       (uint32_t[]){ 0, wide_guest("SeShutdownPrivilege"), luid }) == 1 && MEM32(luid) == 19u);
    ok("SeChangeNotifyPrivilege -> LUID 23", call("LookupPrivilegeValueW", 3,
       (uint32_t[]){ 0, wide_guest("SeChangeNotifyPrivilege"), luid }) == 1 && MEM32(luid) == 23u);
    hle_set_last_error(0);
    ok("unknown name fails", call("LookupPrivilegeValueW", 3,
       (uint32_t[]){ 0, wide_guest("SeMakeSandwichPrivilege"), luid }) == 0);
    ok("unknown name -> ERROR_NO_SUCH_PRIVILEGE", hle_get_last_error() == ERROR_NO_SUCH_PRIVILEGE);
    hle_set_last_error(0);
    ok("NULL name fails", call("LookupPrivilegeValueW", 3,
       (uint32_t[]){ 0, 0, luid }) == 0);
    ok("NULL name -> ERROR_INVALID_PARAMETER", hle_get_last_error() == ERROR_INVALID_PARAMETER);
    hle_set_last_error(0);
    ok("NULL LUID fails", call("LookupPrivilegeValueW", 3,
       (uint32_t[]){ 0, nDebug, 0 }) == 0);
    ok("NULL LUID -> ERROR_INVALID_PARAMETER", hle_get_last_error() == ERROR_INVALID_PARAMETER);
    {
        uint32_t eax, words = shim_pops("LookupPrivilegeValueW", 3,
            (uint32_t[]){ 0, nDebug, luid }, &eax);
        ok("LookupPrivilegeValueW pops ret + 3 args", words == 4u);
        ok("LookupPrivilegeValueW purge keeps eax", eax == 1u);
    }

    /* --- AdjustTokenPrivileges --- */
    uint32_t newst = token_privs(20u, SE_PRIVILEGE_ENABLED);
    uint32_t prev = hle_va(hle_alloc(16));
    uint32_t retlen = hle_va(hle_alloc(4));
    MEM32(prev) = 0xDEADBEEFu;
    MEM32(retlen) = 0;
    ok("enable privilege -> TRUE", call("AdjustTokenPrivileges", 6,
       (uint32_t[]){ TOKEN, 0, newst, 16u, prev, retlen }) == 1);
    ok("ReturnLength written", MEM32(retlen) == 16u);
    ok("PreviousState count copied", MEM32(prev) == 1u);
    ok("PreviousState LUID copied", MEM32(prev + 4u) == 20u && MEM32(prev + 8u) == 0u);
    ok("PreviousState attributes are disabled", MEM32(prev + 12u) == 0u);
    ok("no real change -> ERROR_NOT_ALL_ASSIGNED", hle_get_last_error() == ERROR_NOT_ALL_ASSIGNED);
    {
        uint32_t eax, words = shim_pops("AdjustTokenPrivileges", 6,
            (uint32_t[]){ TOKEN, 0, newst, 16u, prev, retlen }, &eax);
        ok("AdjustTokenPrivileges pops ret + 6 args", words == 7u);
        ok("AdjustTokenPrivileges purge keeps eax", eax == 1u);
    }
    hle_set_last_error(0);
    ok("bad token fails", call("AdjustTokenPrivileges", 6,
       (uint32_t[]){ 0x12345678u, 0, newst, 16u, prev, retlen }) == 0);
    ok("bad token -> ERROR_INVALID_HANDLE", hle_get_last_error() == ERROR_INVALID_HANDLE);
    hle_set_last_error(0);
    ok("NULL NewState fails", call("AdjustTokenPrivileges", 6,
       (uint32_t[]){ TOKEN, 0, 0, 16u, 0, 0 }) == 0);
    ok("NULL NewState -> ERROR_INVALID_PARAMETER", hle_get_last_error() == ERROR_INVALID_PARAMETER);
    hle_set_last_error(0);
    MEM32(retlen) = 0;
    ok("small PreviousState fails", call("AdjustTokenPrivileges", 6,
       (uint32_t[]){ TOKEN, 0, newst, 4u, prev, retlen }) == 0);
    ok("small PreviousState -> ERROR_INSUFFICIENT_BUFFER", hle_get_last_error() == ERROR_INSUFFICIENT_BUFFER);
    ok("small PreviousState still writes ReturnLength", MEM32(retlen) == 16u);
    hle_set_last_error(0);
    MEM32(retlen) = 0;
    ok("DisableAllPrivileges -> TRUE", call("AdjustTokenPrivileges", 6,
       (uint32_t[]){ TOKEN, 1, 0, 16u, prev, retlen }) == 1);
    ok("DisableAllPrivileges ReturnLength is 4", MEM32(retlen) == 4u);
    ok("DisableAllPrivileges -> ERROR_NOT_ALL_ASSIGNED", hle_get_last_error() == ERROR_NOT_ALL_ASSIGNED);
    hle_set_last_error(0);
    {
        uint32_t query = hle_va(hle_alloc(4));
        MEM32(query) = 0;                            /* zero privileges: query only */
        ok("zero-privilege query -> TRUE", call("AdjustTokenPrivileges", 6,
           (uint32_t[]){ TOKEN, 0, query, 4u, 0, 0 }) == 1);
        ok("zero-privilege query -> ERROR_SUCCESS", hle_get_last_error() == ERROR_SUCCESS);
    }

    /* --- SHGetFolderPathW --- */
    uint32_t path = hle_va(hle_alloc(520));
    char result[600];
    ok("CSIDL_WINDOWS -> S_OK", call("SHGetFolderPathW", 5,
       (uint32_t[]){ 0, 0x24u, 0, 0, path }) == 0);
    narrow((const uint16_t *)hle_ptr(path), result, sizeof result);
    ok("CSIDL_WINDOWS path", strcmp(result, "C:\\WINDOWS") == 0);
    ok("CSIDL_SYSTEM -> S_OK", call("SHGetFolderPathW", 5,
       (uint32_t[]){ 0, 0x25u, 0, 0, path }) == 0);
    narrow((const uint16_t *)hle_ptr(path), result, sizeof result);
    ok("CSIDL_SYSTEM path", strcmp(result, "C:\\WINDOWS\\SYSTEM32") == 0);
    ok("CSIDL_PROGRAM_FILES -> S_OK", call("SHGetFolderPathW", 5,
       (uint32_t[]){ 0, 0x26u, 0, 0, path }) == 0);
    narrow((const uint16_t *)hle_ptr(path), result, sizeof result);
    ok("CSIDL_PROGRAM_FILES path", strcmp(result, "C:\\Program Files") == 0);
    ok("CSIDL_PERSONAL -> S_OK", call("SHGetFolderPathW", 5,
       (uint32_t[]){ 0, 0x05u, 0, 0, path }) == 0);
    narrow((const uint16_t *)hle_ptr(path), result, sizeof result);
    ok("CSIDL_PERSONAL path", strcmp(result, "C:\\Documents and Settings\\User\\My Documents") == 0);
    ok("CSIDL_DESKTOP -> S_OK", call("SHGetFolderPathW", 5,
       (uint32_t[]){ 0, 0x00u, 0, 0, path }) == 0);
    narrow((const uint16_t *)hle_ptr(path), result, sizeof result);
    ok("CSIDL_DESKTOP path", strcmp(result, "C:\\Documents and Settings\\User\\Desktop") == 0);
    ok("CSIDL_LOCAL_APPDATA -> S_OK", call("SHGetFolderPathW", 5,
       (uint32_t[]){ 0, 0x1Cu, 0, 0, path }) == 0);
    narrow((const uint16_t *)hle_ptr(path), result, sizeof result);
    ok("CSIDL_LOCAL_APPDATA path", strcmp(result, "C:\\Documents and Settings\\User\\Local Settings\\Application Data") == 0);
    ok("CSIDL_FLAG_CREATE is masked", call("SHGetFolderPathW", 5,
       (uint32_t[]){ 0, 0x8005u, 0, 0, path }) == 0);
    narrow((const uint16_t *)hle_ptr(path), result, sizeof result);
    ok("flagged CSIDL still maps", strcmp(result, "C:\\Documents and Settings\\User\\My Documents") == 0);
    ok("unsupported CSIDL -> E_INVALIDARG", call("SHGetFolderPathW", 5,
       (uint32_t[]){ 0, 0x00FEu, 0, 0, path }) == E_INVALIDARG);
    ok("invalid SHGFP type -> E_INVALIDARG", call("SHGetFolderPathW", 5,
       (uint32_t[]){ 0, 0x24u, 0, 2u, path }) == E_INVALIDARG);
    ok("NULL output -> E_INVALIDARG", call("SHGetFolderPathW", 5,
       (uint32_t[]){ 0, 0x24u, 0, 0, 0 }) == E_INVALIDARG);
    {
        uint32_t eax, words = shim_pops("SHGetFolderPathW", 5,
            (uint32_t[]){ 0, 0x24u, 0, 0, path }, &eax);
        ok("SHGetFolderPathW pops ret + 5 args", words == 6u);
        ok("SHGetFolderPathW purge keeps eax", eax == 0u);
    }

    mach_leave();
    if (fails == 0) printf("advapi32_priv_selftest: all checks passed\n");
    return fails != 0;
}
