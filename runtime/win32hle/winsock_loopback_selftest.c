/* winsock_loopback_selftest.c - real host loopback through the Win32 shims. */
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
#define FIONBIO_VALUE 0x8004667Eu
#define WSAEWOULDBLOCK_VALUE 10035u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int n, const uint32_t *a) {
    return hle_call_guest(hle_resolve(name), n, a);
}
static uint32_t guest_mem(const void *p, size_t n) {
    uint32_t va = hle_va(hle_alloc((uint32_t)n));
    memcpy(hle_ptr(va), p, n);
    return va;
}
static void put_addr(uint32_t va, uint16_t port) {
    uint8_t b[16];
    memset(b, 0, sizeof b);
    b[1] = 2;                                  /* AF_INET */
    b[2] = (uint8_t)(port >> 8); b[3] = (uint8_t)port;
    b[4] = 127; b[5] = 0; b[6] = 0; b[7] = 1;  /* 127.0.0.1 */
    memcpy(hle_ptr(va), b, sizeof b);
}

int main(void) {
    setbuf(stdout, NULL);
    win32hle_register(win32hle_wsock32);
    mach_enter();
    static const char *names[] = { "WSAStartup", "WSACleanup", "socket", "bind",
            "listen", "getsockname", "connect", "accept", "send", "recv",
            "ioctlsocket", "closesocket", "WSAGetLastError" };
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; i++)
        if (!hle_resolve(names[i])) { printf("FAIL missing shim %s\n", names[i]); fails++; }
    uint32_t wsadata = hle_va(hle_alloc(400));
    ok("WSAStartup", call("WSAStartup", 2, (uint32_t[]){ 0x0101u, wsadata }) == 0);

    uint32_t listener = call("socket", 3, (uint32_t[]){ 2, 1, 0 });
    ok("listener socket", listener != SOCKET_ERROR_VALUE);
    uint32_t addr = hle_va(hle_alloc(16));
    put_addr(addr, 0);
    ok("bind ephemeral", call("bind", 3, (uint32_t[]){ listener, addr, 16 }) == 0);
    uint32_t bound = hle_va(hle_alloc(16)), alen = hle_va(hle_alloc(4));
    MEM32(alen) = 16;
    ok("getsockname", call("getsockname", 3, (uint32_t[]){ listener, bound, alen }) == 0);
    uint16_t port = (uint16_t)((MEM8(bound + 2u) << 8) | MEM8(bound + 3u)); /* sockaddr port is network order */
    ok("nonzero port", port != 0);
    ok("listen", call("listen", 2, (uint32_t[]){ listener, 1 }) == 0);

    uint32_t client = call("socket", 3, (uint32_t[]){ 2, 1, 0 });
    ok("client socket", client != SOCKET_ERROR_VALUE);
    put_addr(addr, port);
    ok("connect loopback", call("connect", 3, (uint32_t[]){ client, addr, 16 }) == 0);
    uint32_t accepted = call("accept", 3, (uint32_t[]){ listener, 0, 0 });
    ok("accept", accepted != SOCKET_ERROR_VALUE);

    const char *ping = "ping", *pong = "pong";
    uint32_t ping_va = guest_mem(ping, 5), pong_va = guest_mem(pong, 5);
    uint32_t buf = hle_va(hle_alloc(16));
    for (int i = 0; i < 3; i++) {
        ok("send ping", call("send", 4, (uint32_t[]){ client, ping_va, 4, 0 }) == 4);
        ok("blocking recv ping", call("recv", 4, (uint32_t[]){ accepted, buf, 4, 0 }) == 4);
        ok("ping bytes", memcmp(hle_ptr(buf), ping, 4) == 0);
        ok("send pong", call("send", 4, (uint32_t[]){ accepted, pong_va, 4, 0 }) == 4);
        ok("blocking recv pong", call("recv", 4, (uint32_t[]){ client, buf, 4, 0 }) == 4);
        ok("pong bytes", memcmp(hle_ptr(buf), pong, 4) == 0);
    }

    uint32_t one = hle_va(hle_alloc(4));
    MEM32(one) = 1;
    ok("FIONBIO", call("ioctlsocket", 3, (uint32_t[]){ client, FIONBIO_VALUE, one }) == 0);
    ok("nonblocking recv fails", call("recv", 4, (uint32_t[]){ client, buf, 4, 0 }) == SOCKET_ERROR_VALUE);
    ok("WSAEWOULDBLOCK reported", call("WSAGetLastError", 0, NULL) == WSAEWOULDBLOCK_VALUE);
    ok("invalid handle recv fails", call("recv", 4, (uint32_t[]){ 0x1234u, buf, 4, 0 }) == SOCKET_ERROR_VALUE);

    ok("close accepted", call("closesocket", 1, &accepted) == 0);
    ok("close client", call("closesocket", 1, &client) == 0);
    ok("close listener", call("closesocket", 1, &listener) == 0);
    ok("WSACleanup", call("WSACleanup", 0, NULL) == 0);

    mach_leave();
    printf("winsock_loopback_selftest: all checks passed\n");
    return fails != 0;
}
