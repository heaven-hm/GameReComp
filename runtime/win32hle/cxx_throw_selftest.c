/*
 * cxx_throw_selftest.c - verifies the unsupported path of the guest MSVC C++
 * throw entry point (_CxxThrowException in msvcrt.c).
 *
 * A real guest throw is dispatched by eh32.c: hle_eh_cxx_throw longjmps to a
 * matching lifted catch and never returns. This test deliberately leaves no
 * registration record, so dispatch must decline and the shim must report an
 * explicit failure and stop -- it must never fake a catch by returning as if
 * the exception had been handled. The shim calls hle_exit(3), so each case is
 * observed in a forked child whose stderr is captured on a pipe.
 *
 * The precise diagnostic is checked too: the exception object, the ThrowInfo,
 * and - when the guest's ThrowInfo is well formed - the RTTI type name and
 * catchable-type count decoded from guest memory. No game binary is needed.
 */
#define RECOMP_GENERATED_CODE       /* esp/eax alias the lifted register file */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
#include "win32hle.h"

/* No lifted functions are dispatched in this test (the throw is declined). */
const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

/* msvcrt.c's shim table. */
extern const win32hle_shim win32hle_msvcrt[];

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

static uint32_t V_cxx_throw;

/* Guest memory holding a well-formed MSVC throw:
 *   ThrowInfo.pCatchableTypeArray -> CatchableTypeArray[1] -> CatchableType
 *   -> TypeDescriptor.name = ".?AVGuestError@@"
 * All pointers are guest VAs from hle_alloc, so every read the diagnostic does
 * is a real guest-memory read rather than a host address. */
static uint32_t g_obj, g_info, g_arr, g_ct, g_td;
static const char k_mangled[] = ".?AVGuestError@@";

static void build_guest_throw(void) {
    g_obj  = hle_va(hle_alloc(16));
    g_td   = hle_va(hle_alloc(64));
    g_ct   = hle_va(hle_alloc(32));
    g_arr  = hle_va(hle_alloc(16));
    g_info = hle_va(hle_alloc(16));
    memcpy(hle_ptr(g_td + 12u), k_mangled, sizeof k_mangled);  /* name at +12 */
    MEM32(g_ct + 4u)  = g_td;                 /* CatchableType.pType */
    MEM32(g_arr)      = 1u;                   /* nCatchableTypes */
    MEM32(g_arr + 4u) = g_ct;                 /* arrayOfCatchableTypes[0] */
    MEM32(g_info + 12u) = g_arr;              /* ThrowInfo.pCatchableTypeArray */
    ok("guest throw metadata allocated", g_obj && g_info && g_arr && g_ct && g_td);
}

/* Run _CxxThrowException(obj, info) in a child and return its captured stderr
 * and exit status. The shim must exit(3), not return to the guest. */
static size_t run_throw(uint32_t obj, uint32_t info, char *out, size_t cap,
                        int *status) {
    /* RECOMP_ICALL publishes these flag locals as it enters the shim. */
    uint32_t _cf = 0, _flag_k = FK_NONE, _flag_a = 0, _flag_b = 0;
    (void)_cf; (void)_flag_k; (void)_flag_a; (void)_flag_b;
    fflush(NULL);
    int fds[2];
    out[0] = '\0';
    *status = -1;
    if (pipe(fds) != 0) { ok("pipe() for the throw test", 0); return 0; }
    pid_t pid = fork();
    if (pid == 0) {
        close(fds[0]);
        dup2(fds[1], 2);                       /* capture the diagnostic */
        close(fds[1]);
        mach_enter();                          /* guest TIB/stack for this thread */
        PUSH32(esp, info);                     /* cdecl: push arg1, then arg0 */
        PUSH32(esp, obj);
        RECOMP_ICALL(V_cxx_throw);
        mach_leave();
        _exit(99);                             /* the shim must never return */
    }
    close(fds[1]);
    size_t n = 0;
    ssize_t got;
    while (n + 1 < cap && (got = read(fds[0], out + n, cap - 1 - n)) > 0) n += (size_t)got;
    if (got < 0) n = 0;
    out[n] = '\0';
    close(fds[0]);
    int st = 0;
    waitpid(pid, &st, 0);
    *status = st;
    return n;
}

int main(void) {
    win32hle_register(win32hle_msvcrt);
    V_cxx_throw = hle_resolve("_CxxThrowException");
    ok("_CxxThrowException shim resolves", V_cxx_throw != 0);
    build_guest_throw();

    char msg[512];
    int st;

    /* 1. Well-formed ThrowInfo, but no lifted checkpoint registered: dispatch
     * must decline, so the shim diagnoses the throw and exits 3. */
    run_throw(g_obj, g_info, msg, sizeof msg, &st);
    ok("unhandled throw exits with code 3", WIFEXITED(st) && WEXITSTATUS(st) == 3);
    {
        char buf[64];
        snprintf(buf, sizeof buf, "0x%08X", g_info);
        ok("diagnostic prints the ThrowInfo VA", strstr(msg, buf) != NULL);
        snprintf(buf, sizeof buf, "0x%08X", g_obj);
        ok("diagnostic prints the exception object VA", strstr(msg, buf) != NULL);
    }
    ok("diagnostic decodes the thrown RTTI type", strstr(msg, k_mangled) != NULL);
    ok("diagnostic reports the catchable type count", strstr(msg, "1 catchable type") != NULL);
    ok("diagnostic gives the reason (no compatible guest handler)",
       strstr(msg, "no compatible guest handler") != NULL);

    /* 2. No ThrowInfo at all: still an honest explicit failure, and the reason
     * says the type could not be read rather than inventing one. */
    run_throw(g_obj, 0u, msg, sizeof msg, &st);
    ok("throw with no ThrowInfo also exits with code 3",
       WIFEXITED(st) && WEXITSTATUS(st) == 3);
    ok("throw with no ThrowInfo reports an unreadable type",
       strstr(msg, "throw type unreadable") != NULL &&
       strstr(msg, "no compatible guest handler") != NULL);

    if (fails == 0) printf("cxx_throw_selftest: all checks passed\n");
    return fails != 0;
}
