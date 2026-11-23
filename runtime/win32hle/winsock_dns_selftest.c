/* winsock_dns_selftest.c - real Winsock name resolution through the HLE shims.
 *
 * Calls the DNS shims exactly as lifted guest code does (hle_call_guest with a
 * resolved shim VA), reads the returned hostent out of guest memory, and
 * checks that failures return NULL with an honest WSAGetLastError code -
 * never a fabricated "success" hostent. All names used here are local or in
 * the RFC 2606 .invalid reserve, so the test needs no live internet service.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

/* Ownership rule: this selftest declares its externs locally and does not
 * change any shared header. (The signatures match win32hle.h.) */
extern const win32hle_shim win32hle_wsock32[];
extern int win32hle_register(const win32hle_shim *shims);
extern uint32_t hle_resolve(const char *name);
extern uint32_t hle_call_guest(uint32_t va, int nargs, const uint32_t *args);
extern void *hle_alloc(uint32_t n);
extern void mach_enter(void);
extern void mach_leave(void);

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#define SOCKET_ERROR_VALUE       0xFFFFFFFFu
#define WSAEFAULT_VALUE          10014u
#define WSAEINVAL_VALUE          10022u
#define WSAHOST_NOT_FOUND_VALUE  11001u
#define WSATRY_AGAIN_VALUE       11002u
#define WSANO_RECOVERY_VALUE     11003u
#define AF_INET_WIN              2u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int n, const uint32_t *a) {
    uint32_t va = hle_resolve(name);
    if (!va) { printf("FAIL missing shim %s\n", name); fails++; return 0; }
    return hle_call_guest(va, n, a);
}
static uint32_t guest_mem(const void *p, size_t n) {
    uint32_t va = hle_va(hle_alloc((uint32_t)n));
    if (va) memcpy(hle_ptr(va), p, n);
    return va;
}
static const char *gstr(uint32_t va) { return va ? (const char *)hle_ptr(va) : ""; }

/* Verify the Winsock hostent layout in guest memory: a 16-byte header, a
 * non-empty name, an empty NULL-terminated alias list, and an addr_list whose
 * first entry is a guest pointer to the expected network-order address. */
static void check_hostent(uint32_t he, uint16_t type, uint16_t len,
                          const uint8_t *addr, const char *label) {
    if (!he) { printf("FAIL %s: NULL hostent\n", label); fails++; return; }
    uint32_t name = MEM32(he + 0), aliases = MEM32(he + 4), list = MEM32(he + 12);
    char msg[128];
    snprintf(msg, sizeof msg, "%s: h_name", label);      ok(msg, name != 0 && gstr(name)[0] != '\0');
    snprintf(msg, sizeof msg, "%s: h_aliases", label);   ok(msg, aliases != 0 && MEM32(aliases) == 0);
    snprintf(msg, sizeof msg, "%s: h_addrtype", label);  ok(msg, MEM16(he + 8) == type);
    snprintf(msg, sizeof msg, "%s: h_length", label);    ok(msg, MEM16(he + 10) == len);
    snprintf(msg, sizeof msg, "%s: h_addr_list", label); ok(msg, list != 0);
    if (!list) return;
    uint32_t first = MEM32(list);
    snprintf(msg, sizeof msg, "%s: first address", label);
    ok(msg, first != 0 && memcmp(hle_ptr(first), addr, len) == 0);
    unsigned slots = 0;                       /* a name can have several addresses */
    while (slots < 16 && MEM32(list + 4u * slots)) slots++;
    snprintf(msg, sizeof msg, "%s: addr_list terminator", label);
    ok(msg, slots > 0 && slots < 16);
}

int main(void) {
    setbuf(stdout, NULL);
    win32hle_register(win32hle_wsock32);
    mach_enter();

    static const char *names[] = { "WSAStartup", "WSACleanup", "WSAGetLastError",
            "WSASetLastError", "gethostname", "gethostbyname", "gethostbyaddr",
            "wsock32.dll#51", "wsock32.dll#52", "wsock32.dll#57", "wsock32.dll#111" };
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; i++)
        if (!hle_resolve(names[i])) { printf("FAIL missing shim %s\n", names[i]); fails++; }

    uint32_t wsadata = hle_va(hle_alloc(400));
    ok("WSAStartup", call("WSAStartup", 2, (uint32_t[]){ 0x0101u, wsadata }) == 0);

    /* --- gethostname: a real host name on success, WSAEFAULT on a bad buffer --- */
    uint32_t hnbuf = hle_va(hle_alloc(256));
    call("WSASetLastError", 1, (uint32_t[]){ 0 });
    ok("gethostname", call("gethostname", 2, (uint32_t[]){ hnbuf, 256 }) == 0);
    ok("gethostname returned a name", gstr(hnbuf)[0] != '\0');
    ok("gethostname clears last error", call("WSAGetLastError", 0, NULL) == 0);
    ok("gethostname(NULL) fails", call("gethostname", 2, (uint32_t[]){ 0, 256 }) == SOCKET_ERROR_VALUE);
    ok("gethostname reports WSAEFAULT", call("WSAGetLastError", 0, NULL) == WSAEFAULT_VALUE);

    /* --- gethostbyname("localhost"): real IPv4 loopback in a guest hostent --- */
    static const uint8_t loopback[4] = { 127, 0, 0, 1 };
    const char *localhost = "localhost";
    uint32_t local_va = guest_mem(localhost, strlen(localhost) + 1);
    call("WSASetLastError", 1, (uint32_t[]){ 0 });
    uint32_t he = call("gethostbyname", 1, (uint32_t[]){ local_va });
    ok("gethostbyname(localhost) resolved", he != 0);
    if (he) {
        check_hostent(he, AF_INET_WIN, 4, loopback, "localhost");
        const char *canon = gstr(MEM32(he));
        ok("localhost canonical name", strstr(canon, "localhost") != NULL ||
           strstr(canon, "127.0.0.1") != NULL);
    }
    ok("gethostbyname clears last error", call("WSAGetLastError", 0, NULL) == 0);

    /* --- gethostbyname(unresolvable): NULL + a real resolver error, no fake --- */
    const char *bad = "a4-no-such-host-swarm.invalid";
    uint32_t bad_va = guest_mem(bad, strlen(bad) + 1);
    call("WSASetLastError", 1, (uint32_t[]){ 0 });
    ok("gethostbyname(unresolvable) fails", call("gethostbyname", 1, (uint32_t[]){ bad_va }) == 0);
    uint32_t dns_err = call("WSAGetLastError", 0, NULL);
    ok("honest DNS failure code", dns_err == WSAHOST_NOT_FOUND_VALUE ||
       dns_err == WSATRY_AGAIN_VALUE || dns_err == WSANO_RECOVERY_VALUE);
    if (dns_err != WSAHOST_NOT_FOUND_VALUE && dns_err != WSATRY_AGAIN_VALUE &&
        dns_err != WSANO_RECOVERY_VALUE)
        printf("FAIL unexpected DNS error code %u\n", dns_err);

    call("WSASetLastError", 1, (uint32_t[]){ 0 });
    ok("gethostbyname(NULL) fails", call("gethostbyname", 1, (uint32_t[]){ 0 }) == 0);
    ok("gethostbyname(NULL) reports WSAHOST_NOT_FOUND",
        call("WSAGetLastError", 0, NULL) == WSAHOST_NOT_FOUND_VALUE);

    /* --- gethostbyaddr: real reverse lookup and an honest bad-input error --- */
    uint32_t loop_va = guest_mem(loopback, sizeof loopback);
    call("WSASetLastError", 1, (uint32_t[]){ 0 });
    uint32_t rev = call("gethostbyaddr", 3, (uint32_t[]){ loop_va, 4, AF_INET_WIN });
    if (rev) {
        check_hostent(rev, AF_INET_WIN, 4, loopback, "reverse loopback");
        ok("gethostbyaddr clears last error", call("WSAGetLastError", 0, NULL) == 0);
    } else {
        uint32_t rev_err = call("WSAGetLastError", 0, NULL);
        /* A host without a PTR for 127.0.0.1 is legitimate; it must still fail
         * with a resolver error rather than return a made-up name. */
        ok("gethostbyaddr failure is honest", rev_err == WSAHOST_NOT_FOUND_VALUE ||
           rev_err == WSATRY_AGAIN_VALUE || rev_err == WSANO_RECOVERY_VALUE);
        printf("note: host has no PTR for 127.0.0.1 (error %u)\n", rev_err);
    }
    call("WSASetLastError", 1, (uint32_t[]){ 0 });
    ok("gethostbyaddr(len 0) fails", call("gethostbyaddr", 3, (uint32_t[]){ loop_va, 0, AF_INET_WIN }) == 0);
    ok("gethostbyaddr reports WSAEINVAL", call("WSAGetLastError", 0, NULL) == WSAEINVAL_VALUE);

    ok("WSACleanup", call("WSACleanup", 0, NULL) == 0);
    mach_leave();
    printf("winsock_dns_selftest: %s\n", fails ? "FAILURES" : "all checks passed");
    return fails != 0;
}
