/* wsock32_server_selftest.c - Winsock's server-side ABI against a real host client.
 *
 * The other Winsock fixtures drive both ends through the same guest shims, so
 * two bugs in one shim can agree with each other. This one binds/listens/accepts
 * through the guest ABI (hle_call_guest, synthetic stdcall stack) and connects
 * with a real POSIX socket in the host process. It checks the exact words a
 * callee pops, the sockaddr bytes written into guest memory, that a short
 * namelen neither writes nor leaves a doubt about the needed size, and the
 * WSA errors the server calls document. No live game or outside network.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

/* Minimal-core builds link only the socket layer and the machine spine; these
 * three hosts live in kernel32.c/user32.c in the full layer. Weak definitions
 * let either core link, with the real implementations winning in full-core. */
__attribute__((weak)) void hle_exit(int code) { (void)code; }
__attribute__((weak)) uint32_t hle_thread_id(void) { return 1; }
__attribute__((weak)) void hle_post_message(uint32_t h, uint32_t m, uint32_t w, uint32_t l) {
    (void)h; (void)m; (void)w; (void)l;
}

#define SOCKET_ERROR_VALUE   0xFFFFFFFFu
#define INVALID_SOCKET_VALUE 0xFFFFFFFFu
#define WSAEFAULT_VALUE      10014u
#define WSAEINVAL_VALUE      10022u
#define WSAENOTSOCK_VALUE    10038u
#define WSAEWOULDBLOCK_VALUE 10035u
#define WSAENOTCONN_VALUE    10057u
#define WSAESHUTDOWN_VALUE   10058u
#define SOMAXCONN_VALUE      0x7FFFFFFFu
#define SD_SEND_VALUE        1u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int n, const uint32_t *a) {
    return hle_call_guest(hle_resolve(name), n, a);
}

/* hle_call_guest restores the register file, which hides a wrong stdcall purge.
 * Call the registered shim body directly with a synthetic guest stack, like the
 * lifted caller does, and count the words it removes (ret + argc*4). */
static uint32_t shim_pops(const char *name, int argc, const uint32_t *args,
                          uint32_t *out_eax) {
    uint32_t va = hle_resolve(name);
    recomp_func_t fn = va ? recomp_lookup_import(va) : 0;
    if (!fn) return 0xFFFFFFFFu;
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t scratch = hle_va(hle_alloc(128));           /* ret + up to 9 args + slack */
    g_esp = scratch + 64;
    for (int i = argc - 1; i >= 0; --i) { g_esp -= 4; MEM32(g_esp) = args[i]; }
    g_esp -= 4; MEM32(g_esp) = RECOMP_RETADDR;
    uint32_t esp_in = g_esp;
    fn();
    uint32_t words = (g_esp - esp_in) / 4u;
    if (out_eax) *out_eax = g_eax;
    g_esp = saved_esp; g_eax = saved_eax;
    return words;
}

static uint32_t guest_bytes(const void *p, size_t n) {
    uint32_t va = hle_va(hle_alloc((uint32_t)n));
    memcpy(hle_ptr(va), p, n);
    return va;
}
static uint32_t guest_u32(uint32_t v) {
    uint32_t va = hle_va(hle_alloc(4));
    MEM32(va) = v;
    return va;
}
static void put_addr(uint32_t va, uint16_t port) {
    uint8_t b[16];
    memset(b, 0, sizeof b);
    b[0] = 2;                                           /* AF_INET, Winsock layout */
    b[2] = (uint8_t)(port >> 8); b[3] = (uint8_t)port;  /* sockaddr port is network order */
    b[4] = 127; b[5] = 0; b[6] = 0; b[7] = 1;           /* 127.0.0.1 */
    memcpy(hle_ptr(va), b, sizeof b);
}
static uint16_t guest_port(uint32_t va) {
    return (uint16_t)((MEM8(va + 2u) << 8) | MEM8(va + 3u));
}
static int canary_intact(uint32_t va, uint8_t byte, size_t n) {
    const uint8_t *p = (const uint8_t *)hle_ptr(va);
    for (size_t i = 0; i < n; i++) if (p[i] != byte) return 0;
    return 1;
}

/* A genuine host-side TCP client, not another guest of the same shims. */
static int host_connect(uint16_t port, uint16_t *local_port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    struct sockaddr_in a;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, (struct sockaddr *)&a, sizeof a) != 0) { close(fd); return -1; }
    if (local_port) {
        socklen_t l = sizeof a;
        struct sockaddr_in self;
        if (getsockname(fd, (struct sockaddr *)&self, &l) != 0) { close(fd); return -1; }
        *local_port = ntohs(self.sin_port);
    }
    return fd;
}

int main(void) {
    setbuf(stdout, NULL);
    win32hle_register(win32hle_wsock32);
    mach_enter();

    /* Imports arrive by name and by ordinal; the server subset must be present
     * under both, or a real game's IAT will miss it. */
    static const char *names[] = {
        "accept", "wsock32.dll#1", "bind", "wsock32.dll#2", "listen",
        "wsock32.dll#13", "getsockname", "wsock32.dll#6", "getpeername",
        "wsock32.dll#5", "shutdown", "wsock32.dll#22", "WSAStartup",
        "WSAGetLastError", "socket", "closesocket",
    };
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; i++)
        if (!hle_resolve(names[i])) { printf("FAIL missing shim %s\n", names[i]); fails++; }

    uint32_t wsadata = hle_va(hle_alloc(400));
    ok("WSAStartup", call("WSAStartup", 2, (uint32_t[]){ 0x0202u, wsadata }) == 0);

    /* ---- 1. Guest server: socket/bind/getsockname/listen ---- */
    uint32_t listener = call("socket", 3, (uint32_t[]){ 2, 1, 0 });
    ok("listener socket", listener != INVALID_SOCKET_VALUE);
    uint32_t addr = hle_va(hle_alloc(16));
    put_addr(addr, 0);                                  /* ephemeral loopback port */
    ok("bind loopback", call("bind", 3, (uint32_t[]){ listener, addr, 16 }) == 0);
    ok("bind bad socket", call("bind", 3, (uint32_t[]){ 0x1234u, addr, 16 }) == SOCKET_ERROR_VALUE);
    ok("bind bad socket -> WSAENOTSOCK", call("WSAGetLastError", 0, NULL) == WSAENOTSOCK_VALUE);
    ok("bind short namelen", call("bind", 3, (uint32_t[]){ listener, addr, 8 }) == SOCKET_ERROR_VALUE);
    ok("bind short namelen -> WSAEFAULT", call("WSAGetLastError", 0, NULL) == WSAEFAULT_VALUE);
    ok("bind null address", call("bind", 3, (uint32_t[]){ listener, 0, 16 }) == SOCKET_ERROR_VALUE);

    uint32_t bound = hle_va(hle_alloc(16)), alen = guest_u32(16);
    ok("getsockname", call("getsockname", 3,
        (uint32_t[]){ listener, bound, alen }) == 0);
    ok("getsockname writes length", MEM32(alen) == 16);
    uint16_t port = guest_port(bound);
    ok("nonzero bound port", port != 0);
    ok("getsockname bad socket", call("getsockname", 3,
        (uint32_t[]){ 0x1234u, bound, alen }) == SOCKET_ERROR_VALUE);
    ok("getsockname bad socket -> WSAENOTSOCK",
        call("WSAGetLastError", 0, NULL) == WSAENOTSOCK_VALUE);

    /* A short buffer must report the size it needs and write no guest bytes. */
    uint32_t small = hle_va(hle_alloc(16));
    memset(hle_ptr(small), 0xA5, 16);
    MEM32(alen) = 8;
    ok("short getsockname fails", call("getsockname", 3,
        (uint32_t[]){ listener, small, alen }) == SOCKET_ERROR_VALUE);
    ok("short getsockname -> WSAEFAULT", call("WSAGetLastError", 0, NULL) == WSAEFAULT_VALUE);
    ok("short getsockname needs 16", MEM32(alen) == 16);
    ok("short getsockname wrote nothing", canary_intact(small, 0xA5, 16));

    /* SOMAXCONN is a legal Winsock backlog; the host kernel caps it. */
    ok("listen SOMAXCONN", call("listen", 2, (uint32_t[]){ listener, SOMAXCONN_VALUE }) == 0);
    ok("listen bad socket", call("listen", 2, (uint32_t[]){ 0x1234u, 8 }) == SOCKET_ERROR_VALUE);
    ok("listen bad socket -> WSAENOTSOCK", call("WSAGetLastError", 0, NULL) == WSAENOTSOCK_VALUE);

    /* ---- 2. A short accept buffer rejects the write without scribbling. ---- */
    int client_short = host_connect(port, NULL);
    ok("host client for short accept", client_short >= 0);
    uint32_t acc_addr = hle_va(hle_alloc(16)), acc_len = guest_u32(8);
    memset(hle_ptr(acc_addr), 0x5A, 16);
    ok("short accept fails", call("accept", 3,
        (uint32_t[]){ listener, acc_addr, acc_len }) == INVALID_SOCKET_VALUE);
    ok("short accept -> WSAEFAULT", call("WSAGetLastError", 0, NULL) == WSAEFAULT_VALUE);
    ok("short accept needs 16", MEM32(acc_len) == 16);
    ok("short accept wrote nothing", canary_intact(acc_addr, 0x5A, 16));
    if (client_short >= 0) close(client_short);

    ok("accept addr without len fails", call("accept", 3,
        (uint32_t[]){ listener, acc_addr, 0 }) == INVALID_SOCKET_VALUE);
    ok("accept addr without len -> WSAEFAULT",
        call("WSAGetLastError", 0, NULL) == WSAEFAULT_VALUE);
    ok("accept bad socket fails", call("accept", 3,
        (uint32_t[]){ 0x1234u, 0, 0 }) == INVALID_SOCKET_VALUE);
    ok("accept bad socket -> WSAENOTSOCK", call("WSAGetLastError", 0, NULL) == WSAENOTSOCK_VALUE);

    /* ---- 3. Real host client, guest accept: inspect the peer it reported. ---- */
    uint16_t client_port = 0;
    int client = host_connect(port, &client_port);
    ok("host client connect", client >= 0);
    MEM32(acc_len) = 16;
    uint32_t accepted = call("accept", 3, (uint32_t[]){ listener, acc_addr, acc_len });
    ok("guest accept of host client", accepted != INVALID_SOCKET_VALUE);
    ok("accept writes length", MEM32(acc_len) == 16);
    ok("accept family AF_INET", MEM8(acc_addr) == 2);
    ok("accept reports host peer port", guest_port(acc_addr) == client_port);

    MEM32(alen) = 16;
    ok("getsockname of accepted", call("getsockname", 3,
        (uint32_t[]){ accepted, bound, alen }) == 0 && guest_port(bound) == port);
    uint32_t peer = hle_va(hle_alloc(16)), peer_len = guest_u32(16);
    ok("getpeername of accepted", call("getpeername", 3,
        (uint32_t[]){ accepted, peer, peer_len }) == 0);
    ok("getpeername writes length", MEM32(peer_len) == 16);
    ok("getpeername reports host peer", guest_port(peer) == client_port);

    memset(hle_ptr(peer), 0x3C, 16);
    MEM32(peer_len) = 4;
    ok("short getpeername fails", call("getpeername", 3,
        (uint32_t[]){ accepted, peer, peer_len }) == SOCKET_ERROR_VALUE);
    ok("short getpeername -> WSAEFAULT", call("WSAGetLastError", 0, NULL) == WSAEFAULT_VALUE);
    ok("short getpeername needs 16", MEM32(peer_len) == 16);
    ok("short getpeername wrote nothing", canary_intact(peer, 0x3C, 16));

    ok("getpeername on listener fails", call("getpeername", 3,
        (uint32_t[]){ listener, peer, peer_len }) == SOCKET_ERROR_VALUE);
    ok("getpeername on listener -> WSAENOTCONN",
        call("WSAGetLastError", 0, NULL) == WSAENOTCONN_VALUE);

    /* ---- 4. Real payload in both directions over the accepted connection. ---- */
    const char ping[4] = { 'p', 'i', 'n', 'g' }, pong[4] = { 'p', 'o', 'n', 'g' };
    uint32_t pong_va = guest_bytes(pong, sizeof pong);
    uint32_t buf = hle_va(hle_alloc(16));
    ok("host sends ping", client >= 0 && send(client, ping, 4, 0) == 4);
    ok("guest recv ping", call("recv", 4, (uint32_t[]){ accepted, buf, 4, 0 }) == 4);
    ok("guest got ping", memcmp(hle_ptr(buf), ping, 4) == 0);
    ok("guest sends pong", call("send", 4, (uint32_t[]){ accepted, pong_va, 4, 0 }) == 4);
    char host_buf[8];
    ok("host recv pong", client >= 0 && recv(client, host_buf, 4, 0) == 4);
    ok("host got pong", memcmp(host_buf, pong, 4) == 0);

    /* ---- 5. shutdown's documented errors and a real FIN. ---- */
    ok("shutdown SD_SEND", call("shutdown", 2,
        (uint32_t[]){ accepted, SD_SEND_VALUE }) == 0);
    char fin[4];
    ok("host sees FIN", client >= 0 && recv(client, fin, sizeof fin, 0) == 0);
    ok("guest send after shutdown fails", call("send", 4,
        (uint32_t[]){ accepted, pong_va, 4, 0 }) == SOCKET_ERROR_VALUE);
    ok("send after shutdown -> WSAESHUTDOWN",
        call("WSAGetLastError", 0, NULL) == WSAESHUTDOWN_VALUE);
    ok("bad shutdown how", call("shutdown", 2,
        (uint32_t[]){ accepted, 3 }) == SOCKET_ERROR_VALUE);
    ok("bad shutdown how -> WSAEINVAL", call("WSAGetLastError", 0, NULL) == WSAEINVAL_VALUE);
    ok("shutdown bad socket", call("shutdown", 2,
        (uint32_t[]){ 0x1234u, SD_SEND_VALUE }) == SOCKET_ERROR_VALUE);
    ok("shutdown bad socket -> WSAENOTSOCK",
        call("WSAGetLastError", 0, NULL) == WSAENOTSOCK_VALUE);

    /* ---- 6. Every server shim pops exactly its stdcall argument words. ---- */
    uint32_t eax = 0;
    ok("bind pops 3", shim_pops("bind", 3,
        (uint32_t[]){ 0x1234u, addr, 16 }, &eax) == 4 && eax == SOCKET_ERROR_VALUE);
    ok("listen pops 2", shim_pops("listen", 2,
        (uint32_t[]){ 0x1234u, 8 }, &eax) == 3 && eax == SOCKET_ERROR_VALUE);
    ok("getsockname pops 3", shim_pops("getsockname", 3,
        (uint32_t[]){ 0x1234u, bound, 16 }, &eax) == 4 && eax == SOCKET_ERROR_VALUE);
    ok("getpeername pops 3", shim_pops("getpeername", 3,
        (uint32_t[]){ 0x1234u, peer, 16 }, &eax) == 4 && eax == SOCKET_ERROR_VALUE);
    ok("shutdown pops 2", shim_pops("shutdown", 2,
        (uint32_t[]){ 0x1234u, SD_SEND_VALUE }, &eax) == 3 && eax == SOCKET_ERROR_VALUE);
    ok("accept error pops 3", shim_pops("accept", 3,
        (uint32_t[]){ 0x1234u, 0, 0 }, &eax) == 4 && eax == INVALID_SOCKET_VALUE);

    /* A success-path accept purge needs a real pending host connection. */
    int client_pops = host_connect(port, NULL);
    ok("host client for accept purge", client_pops >= 0);
    ok("accept success pops 3", shim_pops("accept", 3,
        (uint32_t[]){ listener, 0, 0 }, &eax) == 4 && eax != INVALID_SOCKET_VALUE);
    if (eax != INVALID_SOCKET_VALUE) {
        uint32_t temp = eax;
        ok("close accept-purge socket", call("closesocket", 1, &temp) == 0);
    }
    if (client_pops >= 0) close(client_pops);

    /* ---- 7. Non-blocking listener reports WSAEWOULDBLOCK, then accepts. ---- */
    uint32_t one = guest_u32(1);
    ok("FIONBIO on listener", call("ioctlsocket", 3,
        (uint32_t[]){ listener, 0x8004667Eu, one }) == 0);
    ok("idle nonblocking accept fails", call("accept", 3,
        (uint32_t[]){ listener, 0, 0 }) == INVALID_SOCKET_VALUE);
    ok("idle accept -> WSAEWOULDBLOCK",
        call("WSAGetLastError", 0, NULL) == WSAEWOULDBLOCK_VALUE);

    /* ---- 8. Cleanup. ---- */
    ok("close accepted", call("closesocket", 1, &accepted) == 0);
    if (client >= 0) close(client);
    ok("close listener", call("closesocket", 1, &listener) == 0);
    ok("WSACleanup", call("WSACleanup", 0, NULL) == 0);

    mach_leave();
    printf("wsock32_server_selftest: %s\n", fails ? "FAILED" : "all checks passed");
    return fails != 0;
}
