/* winsock_select_selftest.c - Winsock select() fd_set edge cases.
 *
 * Two facts from wine-10.0's ws2_32/socket.c:select are checked here: with no
 * sockets in any of the three sets the call fails WSAEINVAL (the old shim
 * returned 0 immediately), and the three fd_set pointers may alias, in which
 * case results are a union rather than a last-set-wins compacted array. The
 * input sets are snapshotted before any count is cleared to make that true.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#define SOCKET_ERROR_VALUE 0xFFFFFFFFu
#define WSAEINVAL_VALUE 10022u
#define WSAENOTSOCK_VALUE 10038u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { fprintf(stderr, "FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int n, const uint32_t *a) {
    uint32_t fn = hle_resolve(name);
    if (!fn) { fprintf(stderr, "FAIL %s did not resolve\n", name); fails++; return SOCKET_ERROR_VALUE; }
    return hle_call_guest(fn, n, a);
}
static uint32_t guest_mem(const void *p, size_t n) {
    uint32_t va = hle_va(hle_alloc((uint32_t)n));
    memcpy(hle_ptr(va), p, n);
    return va;
}
static void put_addr(uint32_t va, uint16_t port) {
    uint8_t b[16];
    memset(b, 0, sizeof b);
    b[1] = 2;
    b[2] = (uint8_t)(port >> 8); b[3] = (uint8_t)port;
    b[4] = 127; b[5] = 0; b[6] = 0; b[7] = 1;
    memcpy(hle_ptr(va), b, sizeof b);
}
static uint32_t make_set(const uint32_t *sockets, unsigned n) {
    uint32_t va = hle_va(hle_alloc(4u + 64u * 4u));
    MEM32(va) = n;
    for (unsigned i = 0; i < n; i++) MEM32(va + 4u + 4u * i) = sockets[i];
    return va;
}
static int set_has(uint32_t set, uint32_t sock) {
    uint32_t n = MEM32(set);
    for (uint32_t i = 0; i < n && i < 64; i++)
        if (MEM32(set + 4u + 4u * i) == sock) return 1;
    return 0;
}

int main(void) {
    win32hle_register(win32hle_wsock32);
    uint32_t wsadata = hle_va(hle_alloc(400));
    ok("WSAStartup", call("WSAStartup", 2, (uint32_t[]){ 0x0101u, wsadata }) == 0);
    uint32_t tv = hle_va(hle_alloc(8));
    MEM32(tv) = 0; MEM32(tv + 4) = 100000;       /* 100 ms; empty sets never wait */

    /* No sockets at all: WSAEINVAL, not a successful zero. */
    ok("select(NULL,NULL,NULL) -> SOCKET_ERROR",
       call("select", 5, (uint32_t[]){ 0, 0, 0, 0, tv }) == SOCKET_ERROR_VALUE);
    ok("last error is WSAEINVAL", call("WSAGetLastError", 0, NULL) == WSAEINVAL_VALUE);
    uint32_t empty = make_set(NULL, 0);
    ok("select(empty sets) -> SOCKET_ERROR",
       call("select", 5, (uint32_t[]){ 0, empty, empty, empty, tv }) == SOCKET_ERROR_VALUE);
    ok("last error is still WSAEINVAL", call("WSAGetLastError", 0, NULL) == WSAEINVAL_VALUE);

    /* A connected loopback pair: data from client makes the accepted socket
     * readable; both ends remain writable. */
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
    put_addr(addr, port);
    ok("connect loopback", call("connect", 3, (uint32_t[]){ client, addr, 16 }) == 0);
    uint32_t accepted = call("accept", 3, (uint32_t[]){ listener, 0, 0 });
    ok("accept", accepted != SOCKET_ERROR_VALUE);
    /* A pending connection makes the listener readable; both the listener and
     * the established client are writable. This is deterministic, unlike
     * relying on a TCP payload having crossed loopback before poll() runs. */
    uint32_t pending = call("socket", 3, (uint32_t[]){ 2, 1, 0 });
    ok("pending client socket", pending != SOCKET_ERROR_VALUE);
    put_addr(addr, port);
    ok("pending connect", call("connect", 3, (uint32_t[]){ pending, addr, 16 }) == 0);
    usleep(100000);                             /* let the pending accept become visible */

    /* The SAME fd_set is passed as readfds and writefds. Readiness: listener
     * (readable + writable), client (writable only). The union has both;
     * compacting the read result before the write pass would lose the client. */
    uint32_t both[2] = { listener, client };
    uint32_t alias = make_set(both, 2);
    uint32_t rc = call("select", 5, (uint32_t[]){ 0, alias, alias, 0, tv });
    ok("aliased select succeeds", rc != SOCKET_ERROR_VALUE);
    ok("union contains both sockets", MEM32(alias) == 2 && set_has(alias, listener) && set_has(alias, client));

    /* A separate set for read only reports the listening socket. */
    uint32_t rd = make_set(both, 2);
    rc = call("select", 5, (uint32_t[]){ 0, rd, 0, 0, tv });
    ok("read-only select finds one socket", rc == 1 && MEM32(rd) == 1 && MEM32(rd + 4) == listener);

    /* A handle outside the shim's socket range is WSAENOTSOCK. */
    uint32_t bad = make_set((uint32_t[]){ 0xAAAA0000u }, 1);
    ok("invalid socket -> SOCKET_ERROR",
       call("select", 5, (uint32_t[]){ 0, bad, 0, 0, tv }) == SOCKET_ERROR_VALUE);
    ok("last error is WSAENOTSOCK", call("WSAGetLastError", 0, NULL) == WSAENOTSOCK_VALUE);

    ok("close pending", call("closesocket", 1, &pending) == 0);
    ok("close accepted", call("closesocket", 1, &accepted) == 0);
    ok("close client", call("closesocket", 1, &client) == 0);
    ok("close listener", call("closesocket", 1, &listener) == 0);
    ok("WSACleanup", call("WSACleanup", 0, NULL) == 0);
    if (!fails) printf("winsock_select_selftest: all checks passed\n");
    return fails != 0;
}
