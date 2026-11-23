/* winsock_more_selftest.c - Winsock event objects on the HLE shims.
 *
 * WSAEventSelect used to drop its hEventObject, leaving games that waited on
 * WSAEVENTs stuck. This exercises the real path: create event, select it on a
 * socket, receive a network event, enumerate it, then wait on several events.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <arpa/inet.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#define SOCKET_ERROR_VALUE 0xFFFFFFFFu
#define WSA_WAIT_FAILED    0xFFFFFFFFu
#define WSA_WAIT_TIMEOUT   258u
#define WSA_WAIT_EVENT_0   0u
#define WSAEINVAL_VALUE    10022u
#define FD_READ_VALUE      0x01u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int n, const uint32_t *a) {
    return hle_call_guest(hle_resolve(name), n, a);
}
static void put_addr(uint32_t va, uint16_t port) {
    uint8_t b[16];
    memset(b, 0, sizeof b);
    b[0] = 2;                                      /* AF_INET */
    b[2] = (uint8_t)(port >> 8); b[3] = (uint8_t)port;
    b[4] = 127; b[5] = 0; b[6] = 0; b[7] = 1;      /* 127.0.0.1 */
    memcpy(hle_ptr(va), b, sizeof b);
}

int main(void) {
    setbuf(stdout, NULL);
    win32hle_register(win32hle_wsock32);
    mach_enter();
    static const char *names[] = {
        "WSAStartup", "WSACleanup", "socket", "bind", "listen", "getsockname",
        "connect", "accept", "send", "recv", "closesocket", "WSAGetLastError",
        "WSAEventSelect", "WSAEnumNetworkEvents", "WSACreateEvent",
        "WSACloseEvent", "WSASetEvent", "WSAResetEvent",
        "WSAWaitForMultipleEvents",
    };
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; i++)
        if (!hle_resolve(names[i])) { printf("FAIL missing shim %s\n", names[i]); fails++; }

    uint32_t wsadata = hle_va(hle_alloc(400));
    ok("WSAStartup", call("WSAStartup", 2, (uint32_t[]){ 0x0101u, wsadata }) == 0);

    /* 1. WSACreateEvent hands out an opaque handle; bad handles are errors. */
    uint32_t ev = call("WSACreateEvent", 0, NULL);
    ok("WSACreateEvent token", ev != 0 && ev != SOCKET_ERROR_VALUE);
    ok("WSACloseEvent", call("WSACloseEvent", 1, &ev) == 1);
    ok("double close fails", call("WSACloseEvent", 1, &ev) == 0);
    ok("double close error", call("WSAGetLastError", 0, NULL) == WSAEINVAL_VALUE);
    uint32_t bad = 0xDEADu;
    ok("WSASetEvent(bad) fails", call("WSASetEvent", 1, &bad) == 0);
    ok("WSAResetEvent(bad) fails", call("WSAResetEvent", 1, &bad) == 0);
    uint32_t badset = hle_va(hle_alloc(4));
    MEM32(badset) = bad;
    ok("wait on bad handle fails",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 1, badset, 0, 0, 0 }) == WSA_WAIT_FAILED);
    ok("wait on bad handle error", call("WSAGetLastError", 0, NULL) == WSAEINVAL_VALUE);
    ok("wait with zero events fails",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 0, badset, 0, 0, 0 }) == WSA_WAIT_FAILED);

    /* 2. A selected network event signals the event until it is enumerated. */
    uint32_t ev2 = call("WSACreateEvent", 0, NULL);
    ok("ev2 created", ev2 != 0);
    uint32_t waitset = hle_va(hle_alloc(4));
    MEM32(waitset) = ev2;

    uint32_t listener = call("socket", 3, (uint32_t[]){ 2, 1, 0 });
    ok("listener socket", listener != SOCKET_ERROR_VALUE);
    uint32_t addr = hle_va(hle_alloc(16));
    put_addr(addr, 0);
    ok("bind ephemeral", call("bind", 3, (uint32_t[]){ listener, addr, 16 }) == 0);
    uint32_t bound = hle_va(hle_alloc(16)), alen = hle_va(hle_alloc(4));
    MEM32(alen) = 16;
    ok("getsockname", call("getsockname", 3, (uint32_t[]){ listener, bound, alen }) == 0);
    uint16_t port = (uint16_t)((MEM8(bound + 2u) << 8) | MEM8(bound + 3u));
    ok("nonzero port", port != 0);
    ok("listen", call("listen", 2, (uint32_t[]){ listener, 1 }) == 0);

    uint32_t client = call("socket", 3, (uint32_t[]){ 2, 1, 0 });
    ok("client socket", client != SOCKET_ERROR_VALUE);
    put_addr(addr, port);
    ok("connect loopback", call("connect", 3, (uint32_t[]){ client, addr, 16 }) == 0);
    uint32_t accepted = call("accept", 3, (uint32_t[]){ listener, 0, 0 });
    ok("accept", accepted != SOCKET_ERROR_VALUE);

    ok("WSAEventSelect bad event fails",
       call("WSAEventSelect", 3, (uint32_t[]){ client, bad, FD_READ_VALUE }) == SOCKET_ERROR_VALUE);
    ok("WSAEventSelect bad event error", call("WSAGetLastError", 0, NULL) == WSAEINVAL_VALUE);
    ok("WSAEventSelect FD_READ",
       call("WSAEventSelect", 3, (uint32_t[]){ client, ev2, FD_READ_VALUE }) == 0);

    ok("idle event times out",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 1, waitset, 0, 0, 0 }) == WSA_WAIT_TIMEOUT);
    uint32_t msg = hle_va(hle_alloc(8));
    MEM32(msg) = 0x11223344u;
    ok("send", call("send", 4, (uint32_t[]){ accepted, msg, 4, 0 }) == 4);
    ok("readable event signalled",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 1, waitset, 0, 1000, 0 }) == WSA_WAIT_EVENT_0);
    uint32_t out = hle_va(hle_alloc(44));                 /* WSANETWORKEVENTS */
    ok("WSAEnumNetworkEvents",
       call("WSAEnumNetworkEvents", 3, (uint32_t[]){ client, ev2, out }) == 0);
    ok("FD_READ reported", (MEM32(out) & FD_READ_VALUE) != 0);
    uint32_t buf = hle_va(hle_alloc(8));
    ok("recv", call("recv", 4, (uint32_t[]){ client, buf, 4, 0 }) == 4);
    ok("enumeration resets event",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 1, waitset, 0, 0, 0 }) == WSA_WAIT_TIMEOUT);
    ok("WSASetEvent", call("WSASetEvent", 1, &ev2) == 1);
    ok("manually signalled event waits",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 1, waitset, 0, 0, 0 }) == WSA_WAIT_EVENT_0);
    ok("WSAResetEvent", call("WSAResetEvent", 1, &ev2) == 1);
    ok("manually reset event times out",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 1, waitset, 0, 0, 0 }) == WSA_WAIT_TIMEOUT);

    /* 3. Wait-all and wait-any over two manual-reset events. */
    uint32_t ev3 = call("WSACreateEvent", 0, NULL);
    ok("ev3 created", ev3 != 0);
    uint32_t set2 = hle_va(hle_alloc(8));
    MEM32(set2) = ev2; MEM32(set2 + 4u) = ev3;
    ok("WSASetEvent ev2", call("WSASetEvent", 1, &ev2) == 1);
    ok("waitall with one signaled times out",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 2, set2, 1, 0, 0 }) == WSA_WAIT_TIMEOUT);
    ok("WSASetEvent ev3", call("WSASetEvent", 1, &ev3) == 1);
    ok("waitall with both signaled returns 0",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 2, set2, 1, 0, 0 }) == WSA_WAIT_EVENT_0);
    ok("waitany picks first",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 2, set2, 0, 0, 0 }) == 0u);
    ok("WSAResetEvent ev2", call("WSAResetEvent", 1, &ev2) == 1);
    ok("waitany picks second",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 2, set2, 0, 0, 0 }) == 1u);
    ok("waitall after reset times out",
       call("WSAWaitForMultipleEvents", 5, (uint32_t[]){ 2, set2, 1, 0, 0 }) == WSA_WAIT_TIMEOUT);

    ok("close ev2", call("WSACloseEvent", 1, &ev2) == 1);
    ok("close ev3", call("WSACloseEvent", 1, &ev3) == 1);
    ok("close accepted", call("closesocket", 1, &accepted) == 0);
    ok("close client", call("closesocket", 1, &client) == 0);
    ok("close listener", call("closesocket", 1, &listener) == 0);
    ok("WSACleanup", call("WSACleanup", 0, NULL) == 0);

    mach_leave();
    printf("winsock_more_selftest: %s\n", fails ? "FAILED" : "all checks passed");
    return fails != 0;
}
