/* wsock32_ioctl_selftest.c - Winsock 2 WSAIoctl on the HLE socket layer.
 *
 * WSAIoctl was missing: a game could not switch non-blocking mode, read the
 * pending byte count, test the out-of-band mark or obtain the broadcast
 * address without reaching for the undocumented host socket. This drives the
 * real nine-argument stdcall shim with guest-memory buffers and checks both
 * the values it writes and the words it pops. No live game or Internet access.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#define SOCKET_ERROR_VALUE   0xFFFFFFFFu
#define WSAEFAULT_VALUE      10014u
#define WSAEINVAL_VALUE      10022u
#define WSAEWOULDBLOCK_VALUE 10035u
#define WSAENOTSOCK_VALUE    10038u
#define WSAEOPNOTSUPP_VALUE  10045u
#define FIONBIO_CODE         0x8004667Eu
#define FIONREAD_CODE        0x4004667Fu
#define SIOCATMARK_CODE      0x40047307u
#define SIO_GET_BROADCAST_ADDRESS_CODE 0x4004740Au

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

static uint32_t call_ws(const char *name, int n, const uint32_t *a) {
    uint32_t va = hle_resolve(name);
    return va ? hle_call_guest(va, n, a) : SOCKET_ERROR_VALUE;
}

/* hle_call_guest restores the register file, hiding a wrong stdcall purge.
 * Call the registered shim body directly with a synthetic guest stack, as the
 * lifted caller does, and count the words it removes. */
static uint32_t shim_pops(const char *name, int argc, const uint32_t *args,
                          uint32_t *out_eax) {
    uint32_t va = hle_resolve(name);
    recomp_func_t fn = va ? recomp_lookup_import(va) : 0;
    if (!fn) return 0xFFFFFFFFu;
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t scratch = hle_va(hle_alloc(128));        /* ret + 9 args */
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

static uint32_t guest_u32(uint32_t v) {
    uint32_t va = hle_va(hle_alloc(4));
    MEM32(va) = v;
    return va;
}
static void put_addr(uint32_t va, uint16_t port) {
    uint8_t b[16];
    memset(b, 0, sizeof b);
    b[0] = 2;                                         /* AF_INET on Windows */
    b[2] = (uint8_t)(port >> 8); b[3] = (uint8_t)port;
    b[4] = 127; b[5] = 0; b[6] = 0; b[7] = 1;         /* 127.0.0.1 */
    memcpy(hle_ptr(va), b, sizeof b);
}

int main(void) {
    setbuf(stdout, NULL);
    win32hle_register(win32hle_wsock32);
    mach_enter();

    ok("WSAIoctl registered", hle_resolve("WSAIoctl") != 0);
    uint32_t wsadata = hle_va(hle_alloc(400));
    ok("WSAStartup", call_ws("WSAStartup", 2,
        (uint32_t[]){ 0x0202u, wsadata }) == 0);

    /* A UDP socket bound to an ephemeral loopback port needs no peer process. */
    uint32_t s = call_ws("socket", 3, (uint32_t[]){ 2, 2, 0 });
    ok("UDP socket", s != SOCKET_ERROR_VALUE);
    uint32_t addr = hle_va(hle_alloc(16)), alen = guest_u32(16);
    put_addr(addr, 0);
    ok("bind loopback", call_ws("bind", 3, (uint32_t[]){ s, addr, 16 }) == 0);
    uint32_t bound = hle_va(hle_alloc(16));
    MEM32(alen) = 16;
    ok("getsockname", call_ws("getsockname", 3,
        (uint32_t[]){ s, bound, alen }) == 0);
    uint16_t port = (uint16_t)((MEM8(bound + 2u) << 8) | MEM8(bound + 3u));
    ok("nonzero port", port != 0);

    uint32_t in1 = guest_u32(1), out4 = guest_u32(0xDEADBEEFu);
    uint32_t ret4 = guest_u32(0xFEEDFACEu);
    uint32_t a[9] = { s, FIONBIO_CODE, in1, 4, 0, 0, ret4, 0, 0 };

    /* 1. Bad socket, unsupported code and unsupported overlapped I/O. */
    a[0] = 0xBAADF00Du;
    ok("bad socket fails", call_ws("WSAIoctl", 9, a) == SOCKET_ERROR_VALUE);
    ok("bad socket -> WSAENOTSOCK",
        call_ws("WSAGetLastError", 0, NULL) == WSAENOTSOCK_VALUE);
    a[0] = s; a[1] = 0x12345678u;
    ok("unsupported code fails", call_ws("WSAIoctl", 9, a) == SOCKET_ERROR_VALUE);
    ok("unsupported code -> WSAEOPNOTSUPP",
        call_ws("WSAGetLastError", 0, NULL) == WSAEOPNOTSUPP_VALUE);
    a[1] = FIONBIO_CODE; a[7] = guest_u32(0);
    ok("overlapped rejected", call_ws("WSAIoctl", 9, a) == SOCKET_ERROR_VALUE);
    ok("overlapped -> WSAEINVAL",
        call_ws("WSAGetLastError", 0, NULL) == WSAEINVAL_VALUE);
    a[7] = 0;

    /* 2. FIONBIO really changes the socket's blocking mode. */
    MEM32(in1) = 1; MEM32(ret4) = 0xFEEDFACEu;
    ok("FIONBIO enable", call_ws("WSAIoctl", 9, a) == 0);
    ok("FIONBIO reports zero bytes", MEM32(ret4) == 0);
    uint32_t buf = hle_va(hle_alloc(16));
    ok("empty nonblocking recv fails",
        call_ws("recv", 4, (uint32_t[]){ s, buf, 8, 0 }) == SOCKET_ERROR_VALUE);
    ok("empty nonblocking recv -> WSAEWOULDBLOCK",
        call_ws("WSAGetLastError", 0, NULL) == WSAEWOULDBLOCK_VALUE);
    a[3] = 2; MEM32(ret4) = 0xFEEDFACEu;
    ok("short FIONBIO input fails",
        call_ws("WSAIoctl", 9, a) == SOCKET_ERROR_VALUE);
    ok("short FIONBIO input -> WSAEFAULT",
        call_ws("WSAGetLastError", 0, NULL) == WSAEFAULT_VALUE);
    ok("short FIONBIO returns needed size", MEM32(ret4) == 4);
    a[3] = 4; MEM32(in1) = 0;
    ok("FIONBIO disable", call_ws("WSAIoctl", 9, a) == 0);

    /* 3. FIONREAD counts a datagram waiting in the socket. */
    uint32_t msg = guest_u32(0x11223344u);
    put_addr(addr, port);
    ok("sendto self", call_ws("sendto", 6,
        (uint32_t[]){ s, msg, 4, 0, addr, 16 }) == 4);
    /* Loopback delivery is not instantaneous on every host: wait for the
     * datagram through the guest select() before asking how many bytes are
     * pending. */
    uint32_t rset = hle_va(hle_alloc(4u + 64u * 4u));
    MEM32(rset) = 1; MEM32(rset + 4u) = s;
    uint32_t tv = hle_va(hle_alloc(8));
    MEM32(tv) = 0; MEM32(tv + 4u) = 100000;
    ok("datagram arrived", call_ws("select", 5,
        (uint32_t[]){ s + 1, rset, 0, 0, tv }) == 1);
    a[1] = FIONREAD_CODE; a[2] = 0; a[3] = 0;
    a[4] = out4; a[5] = 4; a[6] = ret4;
    MEM32(out4) = 0xDEADBEEFu; MEM32(ret4) = 0xFEEDFACEu;
    ok("FIONREAD", call_ws("WSAIoctl", 9, a) == 0);
    ok("FIONREAD sees four bytes", MEM32(out4) == 4);
    ok("FIONREAD reports four bytes", MEM32(ret4) == 4);
    a[5] = 2; MEM32(ret4) = 0xFEEDFACEu;
    ok("short FIONREAD output fails",
        call_ws("WSAIoctl", 9, a) == SOCKET_ERROR_VALUE);
    ok("short FIONREAD -> WSAEFAULT",
        call_ws("WSAGetLastError", 0, NULL) == WSAEFAULT_VALUE);
    ok("short FIONREAD returns needed size", MEM32(ret4) == 4);
    a[5] = 4;
    ok("recvfrom drains datagram", call_ws("recvfrom", 6,
        (uint32_t[]){ s, buf, 16, 0, 0, 0 }) == 4);

    /* 4. SIOCATMARK: this socket has no urgent byte pending. */
    a[1] = SIOCATMARK_CODE;
    MEM32(out4) = 0xDEADBEEFu; MEM32(ret4) = 0xFEEDFACEu;
    ok("SIOCATMARK", call_ws("WSAIoctl", 9, a) == 0);
    ok("no mark pending", MEM32(out4) == 0);
    ok("SIOCATMARK reports four bytes", MEM32(ret4) == 4);

    /* 5. SIO_GET_BROADCAST_ADDRESS writes a guest sockaddr_in. */
    uint32_t bc = hle_va(hle_alloc(16));
    memset(hle_ptr(bc), 0xCC, 16);
    a[1] = SIO_GET_BROADCAST_ADDRESS_CODE;
    a[4] = bc; a[5] = 16; MEM32(ret4) = 0xFEEDFACEu;
    ok("broadcast address", call_ws("WSAIoctl", 9, a) == 0);
    ok("broadcast reports 16 bytes", MEM32(ret4) == 16);
    ok("broadcast family AF_INET", MEM8(bc) == 2);
    ok("broadcast port zero", MEM16(bc + 2u) == 0);
    ok("broadcast is 255.255.255.255",
        MEM8(bc + 4u) == 255 && MEM8(bc + 5u) == 255 &&
        MEM8(bc + 6u) == 255 && MEM8(bc + 7u) == 255);
    uint32_t small = hle_va(hle_alloc(8));
    a[4] = small; a[5] = 8; MEM32(ret4) = 0xFEEDFACEu;
    ok("small broadcast buffer fails",
        call_ws("WSAIoctl", 9, a) == SOCKET_ERROR_VALUE);
    ok("small broadcast -> WSAEFAULT",
        call_ws("WSAGetLastError", 0, NULL) == WSAEFAULT_VALUE);
    ok("small broadcast returns needed size", MEM32(ret4) == 16);

    /* 6. The nine arguments are popped by the callee, not the caller. */
    a[4] = bc; a[5] = 16;
    uint32_t eax = 0xFFFFFFFFu;
    ok("WSAIoctl pops ret + 9 args", shim_pops("WSAIoctl", 9, a, &eax) == 10u);
    ok("purge keeps the return value", eax == 0);

    ok("closesocket", call_ws("closesocket", 1, &s) == 0);
    ok("WSACleanup", call_ws("WSACleanup", 0, NULL) == 0);
    mach_leave();
    printf("wsock32_ioctl_selftest: %s\n", fails ? "FAILED" : "all checks passed");
    return fails != 0;
}
