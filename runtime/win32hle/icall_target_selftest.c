/*
 * icall_target_selftest.c - an unresolved indirect call is a guest-visible
 * no-op, and a NULL target is named as NULL.
 *
 * D/DMD games (Gunroar gr.exe, Tumiki Fighters tf.exe, Parsec47 p47.exe) call
 * virtual methods as `mov eax,[this+field]; mov edx,[eax]; call [edx+slot]`.
 * When the object was never constructed, [edx+slot] is NULL and the runtime
 * used to print `ICALL: unresolved VA 0x00000000` and then `eax = 0`. On this
 * ABI `this` travels in EAX, so zeroing it turned one bad virtual call into a
 * NULL dereference in the caller (SIGBUS in Gunroar). This test pins both
 * halves of the correction: precise diagnostics, and no register clobber.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>
#include "win32hle.h"

#define ENTRY_VA      0x00401000u
#define CALLEE_VA     0x00402000u
/* Inside a guest page this test reserves: the dispatch macro's MSVC-thunk
 * heuristic reads MEM8(target), so an unresolved VA must be mapped memory. */
#define PROBE_BASE    0x00400000u
#define UNLIFTED_VA   0x00409000u

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

static uint32_t g_target;         /* the indirect target for this run */
static uint32_t g_this_in;        /* EAX on entry: DMD's `this` */
static uint32_t g_eax_out, g_ecx_out, g_edx_out, g_esp_out, g_ebx_out;
static unsigned g_calls;
static uint32_t g_seen_this;

/* A lifted caller. The four flag locals and RECOMP_ENTER are the standard
 * generated prologue; the register values it leaves for the guest are what
 * this test inspects. */
static void entry(void) {
    uint32_t _cf = 0, _flag_k = FK_NONE, _flag_a = 0, _flag_b = 0;
    RECOMP_ENTER(ENTRY_VA);
    eax = g_this_in;
    ecx = 0xC0FFEEu;
    edx = 0xDEADBEEFu;
    ebx = 0x12345678u;
    RECOMP_ICALL(g_target);
    g_eax_out = eax;
    g_ecx_out = ecx;
    g_edx_out = edx;
    g_esp_out = esp;
    g_ebx_out = ebx;
    RECOMP_FLAGS_OUT();
}

/* A lifted callee using the Borland/DMD register ABI: `this` arrives in EAX.
 * It consumes the dummy return address a recompiled call pushed, as a lifted
 * `ret` would. */
static void callee(void) {
    uint32_t _cf = 0, _flag_k = FK_NONE, _flag_a = 0, _flag_b = 0;
    RECOMP_ENTER(CALLEE_VA);
    g_calls++;
    g_seen_this = eax;
    esp += 4u;                       /* the callee's `ret` */
    eax = 0xCAFEBABEu;
    RECOMP_FLAGS_OUT();
}

const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { ENTRY_VA, entry },
    { CALLEE_VA, callee },
};
const uint32_t recomp_dispatch_count = 2;

/* Capture stderr without a path: fd 2 is duplicated, pointed at a tmpfile,
 * then restored. fprintf(stderr, ...) writes straight to fd 2. */
static FILE *g_diag;
static int g_saved_fd;
static char g_diag_text[8192];

static void diag_begin(void) {
    fflush(stderr);
    g_saved_fd = dup(2);
    g_diag = tmpfile();
    if (g_saved_fd < 0 || !g_diag) { ok("stderr capture set up", 0); return; }
    dup2(fileno(g_diag), 2);
}
static void diag_end(void) {
    if (g_saved_fd >= 0) { dup2(g_saved_fd, 2); close(g_saved_fd); g_saved_fd = -1; }
    size_t n = 0;
    if (g_diag) {
        fflush(stderr);
        rewind(g_diag);
        n = fread(g_diag_text, 1, sizeof(g_diag_text) - 1, g_diag);
        fclose(g_diag);
        g_diag = NULL;
    }
    g_diag_text[n] = '\0';
}
static int diag_has(const char *s) { return strstr(g_diag_text, s) != NULL; }
static int diag_count(const char *s) {
    int n = 0;
    for (const char *p = g_diag_text; (p = strstr(p, s)) != NULL; p++)
        n++;
    return n;
}

static uint32_t stack_top;

static void run(uint32_t target) {
    g_target = target;
    mach_enter();                 /* loads the thread's register file first */
    esp = stack_top;              /* then point guest ESP at our scratch stack */
    entry();
    mach_leave();
}

int main(void) {
    stack_top = hle_va(hle_alloc(0x1000)) + 0x800u;
    ok("guest stack allocated", stack_top > 0x800u);
    ok("probe page reserved", hle_vm_reserve(PROBE_BASE, 0x10000u) == PROBE_BASE);

    /* A target that IS in the dispatch table still dispatches: the guard must
     * not turn successful virtual calls into no-ops. */
    g_this_in = 0xAABBCCDDu;       /* `this` on this ABI */
    run(CALLEE_VA);
    ok("a known target still dispatches", g_calls == 1);
    ok("the callee received `this` in EAX", g_seen_this == 0xAABBCCDDu);
    ok("the callee's return value is in EAX", g_eax_out == 0xCAFEBABEu);
    ok("the call balanced the stack", g_esp_out == stack_top);

    /* NULL: no call, no register clobber, and a diagnostic that names it as
     * NULL rather than as a recoverable VA. Repeat it to check the dedup. */
    g_this_in = 0x11223344u;
    diag_begin();
    for (int i = 0; i < 25; i++) run(0u);
    diag_end();
    ok("a NULL target never calls anything", g_calls == 1);
    ok("EAX (`this` on this ABI) survives a NULL target", g_eax_out == 0x11223344u);
    ok("ECX survives a NULL target", g_ecx_out == 0xC0FFEEu);
    ok("EDX survives a NULL target", g_edx_out == 0xDEADBEEFu);
    ok("EBX survives a NULL target", g_ebx_out == 0x12345678u);
    ok("ESP is not shifted by a miss", g_esp_out == stack_top);
    ok("the diagnostic names the NULL target", diag_has("ICALL: null target from 0x00401000"));
    ok("the diagnostic explains why", diag_has("NULL vtable/interface pointer"));
    ok("the diagnostic no longer looks like a recoverable VA",
       !diag_has("ICALL: unresolved VA 0x00000000"));
    ok("identical misses are reported once", diag_count("ICALL: null target") == 1);

    /* A non-NULL target with no dispatch entry is still a no-op, still keeps
     * the registers, and keeps the greppable format seed_from_log parses. */
    g_this_in = 0x55667788u;
    diag_begin();
    for (int i = 0; i < 25; i++) run(UNLIFTED_VA);
    diag_end();
    ok("an unrecovered target never calls anything", g_calls == 1);
    ok("EAX survives an unrecovered target", g_eax_out == 0x55667788u);
    ok("ECX survives an unrecovered target", g_ecx_out == 0xC0FFEEu);
    ok("ESP is not shifted by an unrecovered miss", g_esp_out == stack_top);
    ok("the existing diagnostic format is kept",
       diag_has("ICALL: unresolved VA 0x00409000 from 0x00401000"));
    ok("identical unrecovered misses are reported once",
       diag_count("ICALL: unresolved VA 0x00409000") == 1);

    if (fails == 0) printf("icall_target_selftest: all checks passed\n");
    return fails != 0;
}
