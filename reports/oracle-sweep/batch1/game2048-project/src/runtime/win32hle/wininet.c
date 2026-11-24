/*
 * wininet.c - WININET (Win32 Internet / HTTP) with no network backend.
 *
 * A program imports WININET for one narrow thing: an online check. A resource
 * converter asks whether a newer version exists; a game asks for a patch
 * manifest. Neither is the program's job, and this layer has no HTTP client
 * to make the request through.
 *
 * So the split follows what WinINET genuinely does:
 *   - InternetOpenA only *opens a session object* - it contacts nothing - so
 *     it succeeds and returns a handle, exactly as Win32 does offline.
 *   - InternetOpenUrlA/InternetReadFile/InternetSetFilePointer are the calls
 *     that would touch the network. They fail, and set the standard
 *     "cannot connect" error, because there is nothing to connect to. The
 *     program's own error path (the one a machine with no internet takes)
 *     runs, which is the honest result.
 *   - InternetCloseHandle releases the session.
 *
 * What this deliberately does NOT do is fake a response: an update check that
 * reads a made-up page would be worse than one that says "no network".
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

/* WinINET error codes (from wininet.h), the ones a failed request reports. */
#define ERROR_INTERNET_OUT_OF_HANDLES        12001u
#define ERROR_INTERNET_TIMEOUT               12002u
#define ERROR_INTERNET_CANNOT_CONNECT        12029u
#define ERROR_INTERNET_CONNECTION_RESET      12031u

/* A session: what InternetOpenA returns. It holds the agent string only, so
 * the handle is a real object (a game may open several) rather than a canned
 * constant. */
typedef struct { char agent[128]; } inet_session;
#define MAX_SESSIONS 64
static inet_session g_sess[MAX_SESSIONS];
static int g_sess_used[MAX_SESSIONS];

static inet_session *sess_of(uint32_t h) {
    uint32_t i = h - 0x58000000u;
    return h >= 0x58000000u && i < MAX_SESSIONS && g_sess_used[i] ? &g_sess[i] : NULL;
}

/* InternetOpenA(agent, accessType, proxy, proxyBypass, flags) -> HINTERNET.
 * No request is made here, so this is the one that succeeds. */
static void i_InternetOpenA(void) {
    const char *agent = ASTR(0);
    for (int i = 0; i < MAX_SESSIONS; i++)
        if (!g_sess_used[i]) {
            g_sess_used[i] = 1;
            snprintf(g_sess[i].agent, sizeof g_sess[i].agent, "%s", agent ? agent : "");
            RET(0x58000000u + (uint32_t)i, 5);
        }
    hle_set_last_error(ERROR_INTERNET_OUT_OF_HANDLES);
    RET(0, 5);
}
/* InternetOpenUrlA(session, url, headers, headersLen, flags, context) -> the
 * request handle. A request needs the network, so it fails with the code a
 * machine with no connectivity gets. */
static void i_InternetOpenUrlA(void) {
    (void)A32(0);
    hle_set_last_error(ERROR_INTERNET_CANNOT_CONNECT);
    RET(0, 6);
}
/* InternetReadFile(request, buffer, toRead, &read): reads from a request that
 * could not be opened, so nothing is read. */
static void i_InternetReadFile(void) {
    if (A32(3)) MEM32(A32(3)) = 0;
    hle_set_last_error(ERROR_INTERNET_CONNECTION_RESET);
    RET(0, 4);
}
/* InternetSetFilePointer(request, distance, &high, method, context): seek
 * within a request. No request exists, so it fails (0xFFFFFFFF). */
static void i_InternetSetFilePointer(void) {
    (void)A32(3);
    hle_set_last_error(ERROR_INTERNET_CONNECTION_RESET);
    RET(0xFFFFFFFFu, 5);
}
/* InternetCloseHandle(handle): release a session (or a request, which is
 * already absent). */
static void i_InternetCloseHandle(void) {
    uint32_t h = A32(0);
    inet_session *s = sess_of(h);
    if (s) { s->agent[0] = 0; g_sess_used[h - 0x58000000u] = 0; RET(1, 1); }
    RET(1, 1);                                   /* closing an absent request is not an error */
}

const win32hle_shim win32hle_wininet[] = {
    { "InternetOpenA",          i_InternetOpenA },
    { "InternetOpenUrlA",       i_InternetOpenUrlA },
    { "InternetReadFile",       i_InternetReadFile },
    { "InternetSetFilePointer", i_InternetSetFilePointer },
    { "InternetCloseHandle",    i_InternetCloseHandle },
    { 0, 0 }
};
