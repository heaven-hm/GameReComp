/*
 * win64_runner.c - native ARM64 runner for generate64.py trees of real PE32+
 * console executables, using the bounded UCRT shims in win64_shims.c.
 *
 *   win64 <path-to-real.exe> [guest-arg ...]
 *
 * The guest argv is deliberately minimal: these console programs do not use
 * it, and inventing a richer one would make the comparison with the original
 * under Wine/Unicorn depend on our guess rather than on the binary.
 */
#include "cpu64.h"
#include "recomp_dispatch.h"

#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

extern int recomp64_load_image(const char *path, CPU *c, uint64_t *entry_out);

/* ---- async-signal-safe fault reporting ---------------------------------
 * A guest fault can land while a host shim (win64_shims.c) holds a stdio
 * lock, or while the allocator holds a heap lock.  fprintf/fflush would
 * deadlock there, so the handler uses only write(2) and _exit(2).  All text
 * is static const; the only mutable state the handler reuses is sig_atomic_t.
 */
static CPU guest_cpu;                              /* guest architectural state */
static volatile CPU * const g_fault_cpu = &guest_cpu; /* link-time constant ptr */
static volatile sig_atomic_t g_fault_reporting;     /* guards re-entrant faults */

static const char FAULT_SIGNAL[] = "[fault] signal 0x";
static const char FAULT_ADDR[]   = " addr=0x";
static const char FAULT_RIP[]    = ": guest rip=0x";
static const char FAULT_RSP[]    = " rsp=0x";
static const char FAULT_RAX[]    = " rax=0x";
static const char FAULT_RBX[]    = " rbx=0x";
static const char FAULT_RCX[]    = " rcx=0x";
static const char FAULT_RDX[]    = " rdx=0x";
static const char FAULT_RSI[]    = " rsi=0x";
static const char FAULT_RDI[]    = " rdi=0x";
static const char FAULT_EOL[]    = "\n";

/* write(2) is async-signal-safe; retry EINTR/short writes, never use stdio. */
static void fault_emit(const char *s, size_t n) {
    while (n != 0) {
        ssize_t w = write(STDERR_FILENO, s, n);
        if (w <= 0)
            return; /* nothing further we can safely do from a handler */
        s += w;
        n -= (size_t)w;
    }
}

/* Own itoa: hex nybbles into a stack buffer, no stdio/malloc/locale locks. */
static void fault_hex(uint64_t v, int width) {
    char buf[16];
    int pos = 16;
    do {
        buf[--pos] = "0123456789abcdef"[v & 0xfu];
        v >>= 4;
    } while (v != 0);
    while (pos > 16 - width)
        buf[--pos] = '0';
    fault_emit(buf + pos, (size_t)(16 - pos));
}

static void guest_fault(int sig, siginfo_t *si, void *ctx) {
    (void)ctx;
    if (g_fault_reporting)
        _exit(128 + sig);
    g_fault_reporting = 1;

    volatile CPU * const c = g_fault_cpu;
    fault_emit(FAULT_SIGNAL, sizeof FAULT_SIGNAL - 1);
    fault_hex((uint64_t)(unsigned int)sig, 1);
    fault_emit(FAULT_ADDR, sizeof FAULT_ADDR - 1);
    fault_hex(si ? (uint64_t)(uintptr_t)si->si_addr : 0, 16);
    fault_emit(FAULT_RIP, sizeof FAULT_RIP - 1);
    fault_hex((uint64_t)c->rip, 16);
    fault_emit(FAULT_RSP, sizeof FAULT_RSP - 1);
    fault_hex((uint64_t)c->rsp, 16);
    fault_emit(FAULT_RAX, sizeof FAULT_RAX - 1);
    fault_hex((uint64_t)c->rax, 16);
    fault_emit(FAULT_RBX, sizeof FAULT_RBX - 1);
    fault_hex((uint64_t)c->rbx, 16);
    fault_emit(FAULT_RCX, sizeof FAULT_RCX - 1);
    fault_hex((uint64_t)c->rcx, 16);
    fault_emit(FAULT_RDX, sizeof FAULT_RDX - 1);
    fault_hex((uint64_t)c->rdx, 16);
    fault_emit(FAULT_RSI, sizeof FAULT_RSI - 1);
    fault_hex((uint64_t)c->rsi, 16);
    fault_emit(FAULT_RDI, sizeof FAULT_RDI - 1);
    fault_hex((uint64_t)c->rdi, 16);
    fault_emit(FAULT_EOL, sizeof FAULT_EOL - 1);
    _exit(128 + sig);
}

/* x87 is not modelled. MSVC CRT startup issues exactly one `fninit` to clear
 * the x87 state; no other x87 instruction exists in this program. Treating
 * that one op as a no-op is bounded and visible. Any OTHER unmodelled op still
 * aborts with its exact VA and lifter text - no silent success. */
void x64_todo(uint64_t va, const char *text) {
    if (strstr(text, "fninit")) {
        if (getenv("X64_SHIM_TRACE"))
            fprintf(stderr, "[shim] ignored x87 fninit at %#llx\n",
                    (unsigned long long)va);
        return;
    }
    fprintf(stderr, "cpu64: RECOMP_TODO at %#llx: %s\n",
            (unsigned long long)va, text);
    abort();
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <real.exe>\n", argv[0]);
        return 2;
    }
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = guest_fault;
    sa.sa_flags = SA_SIGINFO;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
    uint64_t entry = 0;
    if (recomp64_load_image(argv[1], &guest_cpu, &entry) != 0)
        return 3;
    dispatch(&guest_cpu, entry);
    fflush(stdout);
    return 0;
}
