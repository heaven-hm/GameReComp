/* thread_stress_selftest.c - 200 guest threads contending on a mutex. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include "win32hle.h"

#define WORKER_VA 0x00404000u
#define NTHREADS 200u
#define ITERS 40u

static void stress_worker(void);
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { WORKER_VA, stress_worker } };
const uint32_t recomp_dispatch_count = 1;

static int fails;
static uint32_t VA_CreateThread, VA_WaitOne, VA_ReleaseMutex, VA_GetExitCode, VA_Close;
static uint32_t g_state;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static void stress_worker(void) {
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0;
    RECOMP_ENTER(WORKER_VA);
    uint32_t mutex = MEM32(g_state + 4u);
    for (uint32_t i = 0; i < ITERS; i++) {
        (void)hle_call_guest(VA_WaitOne, 1, &mutex);
        MEM32(g_state)++;
        (void)hle_call_guest(VA_ReleaseMutex, 1, &mutex);
    }
    eax = 77u;
}

int main(void) {
    win32hle_register(win32hle_kernel32);
    win32hle_register(win32hle_kernel32_crt);
    win32hle_register(win32hle_kernel32_ext);
    mach_enter();
    VA_CreateThread = hle_resolve("CreateThread");
    VA_WaitOne = hle_resolve("WaitForSingleObject");
    VA_ReleaseMutex = hle_resolve("ReleaseMutex");
    VA_GetExitCode = hle_resolve("GetExitCodeThread");
    VA_Close = hle_resolve("CloseHandle");
    ok("shims resolve", VA_CreateThread && VA_WaitOne && VA_ReleaseMutex &&
       VA_GetExitCode && VA_Close);

    g_state = hle_va(hle_alloc(12));
    MEM32(g_state) = 0;
    uint32_t mutex = hle_call_guest(hle_resolve("CreateMutexA"), 3,
                                    (uint32_t[]){ 0, 0, 0 });
    ok("mutex created", mutex != 0);
    MEM32(g_state + 4u) = mutex;

    uint32_t handles = hle_va(hle_alloc(NTHREADS * 4u));
    uint32_t created = 0;
    for (uint32_t i = 0; i < NTHREADS; i++) {
        uint32_t h = hle_call_guest(VA_CreateThread, 6,
            (uint32_t[]){ 0, 0, WORKER_VA, g_state, 0, 0u });
        if (!h || h == 0xFFFFFFFFu) break;
        MEM32(handles + 4u * i) = h;
        created++;
    }
    ok("all threads created", created == NTHREADS);

    uint32_t waited = 0;
    for (uint32_t i = 0; i < created; i++) {
        uint32_t h = MEM32(handles + 4u * i);
        if (hle_call_guest(VA_WaitOne, 2, (uint32_t[]){ h, 10000u }) == 0) waited++;
        uint32_t code_va = hle_va(hle_alloc(4));
        (void)hle_call_guest(VA_GetExitCode, 2, (uint32_t[]){ h, code_va });
        if (MEM32(code_va) != 77u) ok("thread exit code", 0);
        hle_call_guest(VA_Close, 1, &h);
    }
    ok("all threads waited", waited == created);
    ok("mutex-protected counter", MEM32(g_state) == created * ITERS);
    hle_call_guest(VA_Close, 1, &mutex);

    mach_leave();
    printf("thread_stress_selftest: %u threads, counter=%u\n", created, MEM32(g_state));
    return fails != 0;
}
