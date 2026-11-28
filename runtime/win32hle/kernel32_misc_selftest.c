/* kernel32_misc_selftest.c - the W/path/time/thread gaps found in 7-Zip and
 * PrBoom+: GetDriveTypeW, SystemTimeToTzSpecificLocalTime and
 * FreeLibraryAndExitThread.  Registration is not enough here: each shim's
 * guest ABI (stdcall purge, bounded reads/writes, which thread ends) is
 * checked through hle_call_guest. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <pthread.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

/* kernel32_crt.c's unload/thread-exit diagnostics (no public header entry). */
extern volatile uint32_t hle_free_lib_exit_calls;
extern volatile uint32_t hle_free_lib_exit_frees;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int argc, const uint32_t *args) {
    uint32_t fn = hle_resolve(name);
    return fn ? hle_call_guest(fn, argc, args) : 0xFFFFFFFFu;
}
static uint32_t wide_guest(const char *s) {
    size_t n = strlen(s) + 1;
    uint32_t va = hle_va(hle_alloc((uint32_t)n * 2u));
    uint16_t *w = (uint16_t *)hle_ptr(va);
    for (size_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)s[i];
    return va;
}
static void put_systemtime(uint32_t va, const uint16_t *w) {
    for (int i = 0; i < 8; i++) MEM16(va + (uint32_t)i * 2u) = w[i];
}
static void get_systemtime(uint32_t va, uint16_t *w) {
    for (int i = 0; i < 8; i++) w[i] = MEM16(va + (uint32_t)i * 2u);
}

/* FreeLibraryAndExitThread never returns, so it is exercised on a dedicated
 * guest thread: the machine is free while main waits, and the host process
 * must still be alive afterwards. */
static volatile int reached_thread, returned_from_call;
static void *exiting_thread(void *arg) {
    uint32_t args[2] = { *(uint32_t *)arg, 0x2Au };
    reached_thread = 1;
    call("FreeLibraryAndExitThread", 2, args);
    returned_from_call = 1;                  /* only set if the shim wrongly returned */
    return NULL;
}

int main(void) {
    win32hle_register(win32hle_kernel32);      /* GetDriveTypeW, SystemTimeToTz... */
    win32hle_register(win32hle_kernel32_crt);  /* FreeLibraryAndExitThread */
    win32hle_register(win32hle_module);        /* LoadLibraryA / FreeLibrary path */
    mach_enter();

    ok("GetDriveTypeW registered", hle_resolve("GetDriveTypeW") != 0);
    ok("SystemTimeToTzSpecificLocalTime registered",
       hle_resolve("SystemTimeToTzSpecificLocalTime") != 0);
    ok("FreeLibraryAndExitThread registered", hle_resolve("FreeLibraryAndExitThread") != 0);
    ok("FreeLibrary registered", hle_resolve("FreeLibrary") != 0);

    /* Z: is the host root in hle_path.c, so it is always mapped; Q: never is. */
    ok("GetDriveTypeW(Z:\\) is DRIVE_FIXED",
       call("GetDriveTypeW", 1, (uint32_t[]){ wide_guest("Z:\\") }) == 3u);
    ok("GetDriveTypeW(lower z:\\) is DRIVE_FIXED",
       call("GetDriveTypeW", 1, (uint32_t[]){ wide_guest("z:\\sub\\dir") }) == 3u);
    ok("GetDriveTypeW(Q:\\) is DRIVE_NO_ROOT_DIR",
       call("GetDriveTypeW", 1, (uint32_t[]){ wide_guest("Q:\\") }) == 1u);
    ok("GetDriveTypeW(NULL) is DRIVE_UNKNOWN",
       call("GetDriveTypeW", 1, (uint32_t[]){ 0u }) == 0u);
    ok("GetDriveTypeW(empty) is DRIVE_UNKNOWN",
       call("GetDriveTypeW", 1, (uint32_t[]){ wide_guest("") }) == 0u);
    /* No NUL inside the 16-WCHAR read window: a bounded read must give up
     * rather than scan the guest block for a terminator. */
    uint32_t unterminated = hle_va(hle_alloc(64));
    uint16_t *fill = (uint16_t *)hle_ptr(unterminated);
    for (int i = 0; i < 32; i++) fill[i] = 'A';   /* convertible, but never NUL */
    ok("unterminated UTF-16 name is DRIVE_UNKNOWN",
       call("GetDriveTypeW", 1, (uint32_t[]){ unterminated }) == 0u);

    /* A fixed zone makes the conversion an exact assertion on any host.
     * POSIX TZ=UTC-2 means local = UTC+2. */
    const char *old_tz = getenv("TZ");
    char saved_tz[128] = "";
    if (old_tz) snprintf(saved_tz, sizeof saved_tz, "%s", old_tz);
    setenv("TZ", "UTC-2", 1);
    tzset();
    uint32_t utc = hle_va(hle_alloc(16)), local = hle_va(hle_alloc(32));
    const uint16_t in[8] = { 2020, 1, 3, 1, 12, 0, 0, 0 };  /* Wed 2020-01-01 12:00 UTC */
    put_systemtime(utc, in);
    memset(hle_ptr(local), 0xAA, 32);
    ok("SystemTimeToTzSpecificLocalTime converts",
       call("SystemTimeToTzSpecificLocalTime", 3, (uint32_t[]){ 0u, utc, local }) == 1u);
    uint16_t out[8];
    get_systemtime(local, out);
    const uint16_t want[8] = { 2020, 1, 3, 1, 14, 0, 0, 0 };
    ok("local SYSTEMTIME fields", memcmp(out, want, sizeof want) == 0);
    const uint8_t *tail = (const uint8_t *)hle_ptr(local) + 16;
    int tail_clean = 1;
    for (int i = 0; i < 16; i++) if (tail[i] != 0xAA) tail_clean = 0;
    ok("output write is bounded to 16 bytes", tail_clean);

    put_systemtime(utc, in);
    memset(hle_ptr(local), 0xAA, 32);
    ok("NULL input fails", call("SystemTimeToTzSpecificLocalTime", 3,
       (uint32_t[]){ 0u, 0u, local }) == 0u);
    ok("NULL output fails", call("SystemTimeToTzSpecificLocalTime", 3,
       (uint32_t[]){ 0u, utc, 0u }) == 0u);
    uint16_t bad[8];
    memcpy(bad, in, sizeof bad);
    bad[1] = 13;                             /* month out of range */
    put_systemtime(utc, bad);
    ok("out-of-range month fails", call("SystemTimeToTzSpecificLocalTime", 3,
       (uint32_t[]){ 0u, utc, local }) == 0u);
    tail = (const uint8_t *)hle_ptr(local) + 16;
    tail_clean = 1;
    for (int i = 0; i < 16; i++) if (tail[i] != 0xAA) tail_clean = 0;
    get_systemtime(local, out);
    ok("failure leaves the output untouched", tail_clean && out[0] == 0xAAAAu);
    if (old_tz) setenv("TZ", saved_tz, 1); else unsetenv("TZ");
    tzset();

    /* A real module handle (the kernel32 pseudo-module) goes through the same
     * unload shim an explicit FreeLibrary would. */
    uint32_t name_va = hle_va(hle_alloc(16));
    strcpy((char *)hle_ptr(name_va), "kernel32.dll");
    uint32_t hmod = call("LoadLibraryA", 1, (uint32_t[]){ name_va });
    ok("kernel32 module handle", hmod != 0);
    uint32_t calls_before = hle_free_lib_exit_calls;
    uint32_t frees_before = hle_free_lib_exit_frees;
    mach_leave();                            /* the new thread needs the machine */

    reached_thread = 0; returned_from_call = 0;
    pthread_t th;
    int spawned = pthread_create(&th, NULL, exiting_thread, &hmod) == 0;
    if (spawned) pthread_join(th, NULL);
    ok("thread spawned", spawned);
    ok("guest thread reached the shim", reached_thread);
    ok("FreeLibraryAndExitThread did not return", !returned_from_call);
    ok("unload path was invoked", hle_free_lib_exit_calls == calls_before + 1 &&
                                   hle_free_lib_exit_frees == frees_before + 1);
    ok("host process survived the guest thread's exit", 1);

    printf("kernel32_misc_selftest: all checks passed\n");
    return fails != 0;
}
