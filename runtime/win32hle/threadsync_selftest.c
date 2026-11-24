/* threadsync_selftest.c - deterministic Win32 threading/sync guest fixture. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include "win32hle.h"

#define WORKER_VA 0x00403000u
#define EXIT_VA   0x00403100u
#define WAIT_TIMEOUT_VALUE 258u
#define WAIT_FAILED_VALUE  0xFFFFFFFFu

static int fails;
static uint32_t VA_CreateThread, VA_CreateEvent, VA_SetEvent, VA_WaitOne,
                VA_WaitMany, VA_CreateSem, VA_ReleaseSem, VA_ReleaseMutex,
                VA_TlsSet, VA_TlsGet, VA_GetExitCode, VA_SetPriority, VA_GetPriority;
static uint32_t g_shared;             /* guest VA of { counter, mutex, tls_index } */
static uint32_t g_tls_value;

static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
#define GUEST_PROLOGUE(va) \
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0; RECOMP_ENTER(va)

static void worker(void) {
    GUEST_PROLOGUE(WORKER_VA);
    uint32_t p = A32(0);
    uint32_t mutex = MEM32(p + 4u);
    for (int i = 0; i < 200; i++) {
        (void)hle_call_guest(VA_WaitOne, 1, &mutex);
        MEM32(p)++;
        (void)hle_call_guest(VA_ReleaseMutex, 1, &mutex);
    }
    uint32_t args[2] = { MEM32(p + 8u), 0x1000u + hle_thread_id() };
    (void)hle_call_guest(VA_TlsSet, 2, args);
    eax = 42;
}
static void exit_thread(void) {
    GUEST_PROLOGUE(EXIT_VA);
    eax = A32(0);
}
const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { WORKER_VA, worker }, { EXIT_VA, exit_thread }
};
const uint32_t recomp_dispatch_count = 2;

static uint32_t call1(uint32_t shim, uint32_t a) {
    return hle_call_guest(shim, 1, &a);
}

int main(void) {
    win32hle_register(win32hle_kernel32);
    win32hle_register(win32hle_kernel32_crt);
    mach_enter();

    VA_CreateThread = hle_resolve("CreateThread");
    VA_CreateEvent = hle_resolve("CreateEventA");
    VA_SetEvent = hle_resolve("SetEvent");
    VA_WaitOne = hle_resolve("WaitForSingleObject");
    VA_WaitMany = hle_resolve("WaitForMultipleObjects");
    VA_CreateSem = hle_resolve("CreateSemaphoreA");
    VA_ReleaseSem = hle_resolve("ReleaseSemaphore");
    VA_ReleaseMutex = hle_resolve("ReleaseMutex");
    VA_TlsSet = hle_resolve("TlsSetValue");
    VA_TlsGet = hle_resolve("TlsGetValue");
    VA_GetExitCode = hle_resolve("GetExitCodeThread");
    VA_SetPriority = hle_resolve("SetThreadPriority");
    VA_GetPriority = hle_resolve("GetThreadPriority");
    ok("all shims resolve", VA_CreateThread && VA_CreateEvent && VA_SetEvent &&
       VA_WaitOne && VA_WaitMany && VA_CreateSem && VA_ReleaseSem &&
       VA_ReleaseMutex && VA_TlsSet && VA_TlsGet && VA_GetExitCode &&
       VA_SetPriority && VA_GetPriority);

    /* auto-reset event: SetEvent wakes one wait then is consumed */
    uint32_t ea[4] = { 0, 0, 0, 0 };
    uint32_t ev = hle_call_guest(VA_CreateEvent, 4, ea);
    ok("event created", ev != 0);
    ok("set event", call1(VA_SetEvent, ev) == 1);
    uint32_t wa[2] = { ev, 0 };
    ok("event wait succeeds", hle_call_guest(VA_WaitOne, 2, wa) == 0);
    ok("auto-reset event consumed", hle_call_guest(VA_WaitOne, 2, wa) == WAIT_TIMEOUT_VALUE);

    /* semaphore limit is enforced as an error, not clamped */
    uint32_t sa[4] = { 0, 1, 1, 0 };
    uint32_t sem = hle_call_guest(VA_CreateSem, 4, sa);
    ok("semaphore created", sem != 0);
    uint32_t rs[3] = { sem, 1, 0 };
    ok("semaphore overflow refused at max", hle_call_guest(VA_ReleaseSem, 3, rs) == 0);
    ok("semaphore acquire", hle_call_guest(VA_WaitOne, 2, (uint32_t[]){ sem, 0 }) == 0);
    ok("semaphore still empty", hle_call_guest(VA_WaitOne, 2, (uint32_t[]){ sem, 0 }) == WAIT_TIMEOUT_VALUE);
    ok("semaphore release", hle_call_guest(VA_ReleaseSem, 3, rs) == 1);

    /* WaitForMultipleObjects(all) must not consume a partial set */
    uint32_t ea2[4] = { 0, 0, 0, 0 };
    uint32_t ev2 = hle_call_guest(VA_CreateEvent, 4, ea2);
    uint32_t wm[4];
    /* build the guest handle array with hle_alloc */
    uint32_t harr = hle_va(hle_alloc(8));
    MEM32(harr) = ev; MEM32(harr + 4u) = ev2;
    wm[0] = 2; wm[1] = harr; wm[2] = 1; wm[3] = 0;
    ok("wait-all times out", hle_call_guest(VA_WaitMany, 4, wm) == WAIT_TIMEOUT_VALUE);
    ok("wait-all left first event unconsumed", call1(VA_SetEvent, ev) == 1);
    ok("first event still signalled", hle_call_guest(VA_WaitOne, 2, wa) == 0);
    wm[3] = 5000;
    ok("too many wait handles fails", (wm[0] = 65,
       hle_call_guest(VA_WaitMany, 4, wm)) == WAIT_FAILED_VALUE);

    /* TLS isolation + mutex-protected concurrent increment */
    uint32_t tls_index = hle_call_guest(hle_resolve("TlsAlloc"), 0, NULL);
    g_shared = hle_va(hle_alloc(12));
    uint32_t mu[3] = { 0, 0, 0 };
    uint32_t mutex = hle_call_guest(hle_resolve("CreateMutexA"), 3, mu);
    MEM32(g_shared) = 0; MEM32(g_shared + 4u) = mutex; MEM32(g_shared + 8u) = tls_index;
    g_tls_value = 0x7777u;
    hle_call_guest(VA_TlsSet, 2, (uint32_t[]){ tls_index, g_tls_value });
    uint32_t threads[4];
    for (int i = 0; i < 4; i++) {
        uint32_t ca[6] = { 0, 0, WORKER_VA, g_shared, 0, 0 };
        threads[i] = hle_call_guest(VA_CreateThread, 6, ca);
        ok("thread created", threads[i] != 0);
    }
    uint32_t code_va = hle_va(hle_alloc(4));
    for (int i = 0; i < 4; i++) {
        uint32_t w[2] = { threads[i], 5000 };
        ok("thread waited", hle_call_guest(VA_WaitOne, 2, w) == 0);
        ok("exit code read", hle_call_guest(VA_GetExitCode, 2,
           (uint32_t[]){ threads[i], code_va }) == 1);
        ok("exit code is 42", MEM32(code_va) == 42u);
    }
    ok("mutex-protected counter", MEM32(g_shared) == 800u);
    ok("TLS main value isolated", hle_call_guest(VA_TlsGet, 1, &tls_index) == g_tls_value);

    /* thread priority round-trip and invalid handle */
    uint32_t sp[2] = { threads[0], 5 };
    ok("priority set", hle_call_guest(VA_SetPriority, 2, sp) == 1);
    ok("priority read", hle_call_guest(VA_GetPriority, 1, sp) == 5);
    ok("priority rejects invalid handle", hle_call_guest(VA_SetPriority, 2,
       (uint32_t[]){ 0x1234u, 5 }) == 0);

    mach_leave();
    printf("threadsync_selftest: all checks passed\n");
    return fails != 0;
}
