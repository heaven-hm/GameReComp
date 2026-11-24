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

static volatile CPU *g_fault_cpu;
static void guest_fault(int sig, siginfo_t *si, void *ctx) {
    (void)ctx;
    fprintf(stderr, "[fault] signal %d addr=%p: guest rip=%#llx rsp=%#llx "
            "rax=%#llx rbx=%#llx rcx=%#llx rdx=%#llx rsi=%#llx rdi=%#llx\n",
            sig, si ? si->si_addr : NULL,
            (unsigned long long)g_fault_cpu->rip,
            (unsigned long long)g_fault_cpu->rsp,
            (unsigned long long)g_fault_cpu->rax,
            (unsigned long long)g_fault_cpu->rbx,
            (unsigned long long)g_fault_cpu->rcx,
            (unsigned long long)g_fault_cpu->rdx,
            (unsigned long long)g_fault_cpu->rsi,
            (unsigned long long)g_fault_cpu->rdi);
    fflush(stderr);
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
    static CPU cpu;
    g_fault_cpu = &cpu;
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = guest_fault;
    sa.sa_flags = SA_SIGINFO;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
    uint64_t entry = 0;
    if (recomp64_load_image(argv[1], &cpu, &entry) != 0)
        return 3;
    dispatch(&cpu, entry);
    fflush(stdout);
    return 0;
}
