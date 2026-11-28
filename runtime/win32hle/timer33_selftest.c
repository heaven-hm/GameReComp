/* timer33_selftest.c - waitable timers on the shared handle/wait manager. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <time.h>
#include "win32hle.h"

#define TIMEOUT 2000u
#define WAIT_TIMEOUT_VALUE 258u
#define WAIT_FAILED_VALUE 0xFFFFFFFFu

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int n, const uint32_t *a) {
    return hle_call_guest(hle_resolve(name), n, a);
}
static uint32_t make_timer(int manual) {
    return call("CreateWaitableTimerA", 3, (uint32_t[]){ 0, (uint32_t)manual, 0 });
}
static void set_relative(uint32_t timer, int ms, uint32_t period) {
    uint32_t li = hle_va(hle_alloc(8));
    uint64_t units = (uint64_t)ms * 10000u;
    MEM32(li) = (uint32_t)(0u - (uint32_t)units);
    MEM32(li + 4u) = 0xFFFFFFFFu;
    call("SetWaitableTimer", 6, (uint32_t[]){ timer, li, period, 0, 0, 0 });
}
static uint32_t wait_timer(uint32_t timer, uint32_t ms) {
    return call("WaitForSingleObjectEx", 3, (uint32_t[]){ timer, ms, 0 });
}

int main(void) {
    win32hle_register(win32hle_kernel32);
    win32hle_register(win32hle_kernel32_ext);   /* CloseHandle */
    mach_enter();
    static const char *names[] = {
        "CreateWaitableTimerA", "CreateWaitableTimerW", "SetWaitableTimer",
        "CancelWaitableTimer", "WaitForSingleObjectEx", "WaitForMultipleObjectsEx",
        "SleepEx", "CloseHandle", "CreateEventA", "SetEvent"
    };
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; i++) {
        uint32_t va = hle_resolve(names[i]);
        if (!va) { printf("FAIL missing shim %s\n", names[i]); fails++; }
    }

    /* manual-reset one-shot with cancellation */
    uint32_t manual = make_timer(1);
    ok("manual timer created", manual != 0 && hle_handle_type(manual) == HLE_H_TIMER);
    set_relative(manual, 20, 0);
    ok("manual timer signals", wait_timer(manual, TIMEOUT) == 0);
    ok("manual timer stays signalled", wait_timer(manual, 0) == 0);
    ok("cancel succeeds", call("CancelWaitableTimer", 1, &manual) == 1);
    ok("cancel clears signal", wait_timer(manual, 0) == WAIT_TIMEOUT_VALUE);
    ok("close timer", call("CloseHandle", 1, &manual) == 1);

    /* auto-reset one-shot */
    uint32_t once = make_timer(0);
    set_relative(once, 20, 0);
    ok("one-shot signals", wait_timer(once, TIMEOUT) == 0);
    ok("one-shot consumed", wait_timer(once, 0) == WAIT_TIMEOUT_VALUE);
    call("CloseHandle", 1, &once);

    /* periodic auto-reset timer */
    uint32_t periodic = make_timer(0);
    set_relative(periodic, 20, 20);
    for (int i = 0; i < 3; i++) ok("periodic timer fires", wait_timer(periodic, TIMEOUT) == 0);
    call("CancelWaitableTimer", 1, &periodic);
    ok("periodic timer cancelled", wait_timer(periodic, 0) == WAIT_TIMEOUT_VALUE);
    call("CloseHandle", 1, &periodic);

    /* absolute FILETIME deadline (now + 50ms) */
    uint32_t abs = make_timer(0);
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    uint64_t ft = (uint64_t)ts.tv_sec * 10000000ull + (uint64_t)ts.tv_nsec / 100ull +
                  116444736000000000ull + 500000ull;
    uint32_t li = hle_va(hle_alloc(8));
    MEM32(li) = (uint32_t)ft;
    MEM32(li + 4u) = (uint32_t)(ft >> 32);
    ok("SetWaitableTimer(absolute)", call("SetWaitableTimer", 6,
       (uint32_t[]){ abs, li, 0, 0, 0, 0 }) == 1);
    ok("absolute timer signals", wait_timer(abs, TIMEOUT) == 0);
    call("CloseHandle", 1, &abs);

    /* timer and event in one wait; the event is signalled first */
    uint32_t ev = call("CreateEventA", 4, (uint32_t[]){ 0, 1, 0, 0 });
    uint32_t t2 = make_timer(0);
    uint32_t arr = hle_va(hle_alloc(8));
    MEM32(arr) = t2; MEM32(arr + 4u) = ev;
    call("SetEvent", 1, &ev);
    ok("multi-object wait returns event index", call("WaitForMultipleObjectsEx", 5,
       (uint32_t[]){ 2, arr, 0, TIMEOUT, 0 }) == 1);
    call("ResetEvent", 1, &ev);                 /* leave only the timer signaled */
    set_relative(t2, 20, 0);
    ok("multi-object wait returns timer index", call("WaitForMultipleObjectsEx", 5,
       (uint32_t[]){ 2, arr, 0, TIMEOUT, 0 }) == 0);
    call("CloseHandle", 1, &ev);
    call("CloseHandle", 1, &t2);

    /* alertable waits have no APC queue, but must not fake WAIT_IO_COMPLETION */
    ok("SleepEx returns timeout status", call("SleepEx", 2, (uint32_t[]){ 0, 1 }) == 0);
    ok("SetWaitableTimer rejects invalid handle", call("SetWaitableTimer", 6,
       (uint32_t[]){ 0x1234u, li, 0, 0, 0, 0 }) == 0);
    ok("timer handle no longer valid", hle_handle_type(t2) == 0);

    mach_leave();
    printf("timer33_selftest: all checks passed\n");
    return fails != 0;
}
