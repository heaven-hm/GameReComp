/*
 * host.c - the full win32hle host driver. See host.h. Maps a PE, binds its IAT
 * to the shims, and runs its entry point as lifted code. Portable C: no asm, no
 * OS exception handler (win32hle.h explains why neither is needed).
 */
#define _GNU_SOURCE
#ifndef _XOPEN_SOURCE
#define _XOPEN_SOURCE 700                     /* macOS: ucontext_t for the fault PC */
#endif
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <signal.h>
#include <unistd.h>
#ifdef __APPLE__
#include <ucontext.h>
#include <mach-o/dyld.h>
#elif defined(__linux__)
#include <ucontext.h>
#endif
#include "win32hle.h"
#include "pe_loader.h"
#include "host.h"

static pe_image g_img;
static int      g_booted;

/* A fault in lifted code is a bad guest address, and without a debugger the
 * only useful fact is which function and which address. Report both, then let
 * the default action proceed so the exit status still says what happened. */
static void fault_report(int sig, siginfo_t *info, void *ctx) {
    char buf[260];
    uintptr_t a = (uintptr_t)info->si_addr;
    uintptr_t base = (uintptr_t)g_mem_base;
    uintptr_t pc = 0;
#if defined(__APPLE__)
    if (ctx) {
        ucontext_t *uc = (ucontext_t *)ctx;
#if defined(__aarch64__)
        pc = (uintptr_t)uc->uc_mcontext->__ss.__pc;
#else
        pc = (uintptr_t)uc->uc_mcontext->__ss.__rip;
#endif
        pc -= (uintptr_t)_dyld_get_image_vmaddr_slide(0);   /* link-time address, as nm prints it */
    }
#elif defined(__linux__)
    if (ctx) pc = (uintptr_t)((ucontext_t *)ctx)->uc_mcontext.gregs[REG_RIP];
#endif
    int n;
    if (a >= base && a - base <= 0xFFFFFFFFu)
        n = snprintf(buf, sizeof buf,
                     "\n[win32hle] fatal signal %d at %p (guest VA 0x%08X) in lifted function 0x%08X, esp 0x%08X, pc %p\n",
                     sig, info->si_addr, (unsigned)(a - base), g_cur_func, g_esp, (void *)pc);
    else
        n = snprintf(buf, sizeof buf,
                     "\n[win32hle] fatal signal %d at %p (outside guest memory) in lifted function 0x%08X, esp 0x%08X, pc %p\n",
                     sig, info->si_addr, g_cur_func, g_esp, (void *)pc);
    if (n > 0) write(2, buf, (size_t)n);
    signal(sig, SIG_DFL);
    raise(sig);
}

void recomp_host_init(void) {
    struct sigaction sa;
    sa.sa_sigaction = fault_report;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_SIGINFO | SA_RESETHAND;
    sigaction(SIGBUS, &sa, NULL);
    sigaction(SIGSEGV, &sa, NULL);
    win32hle_register(win32hle_kernel32);
    win32hle_register(win32hle_kernel32_ext);
    win32hle_register(win32hle_kernel32_crt);
    win32hle_register(win32hle_module);
    win32hle_register(win32hle_gdidc);
    win32hle_register(win32hle_gdi32);
    win32hle_register(win32hle_user32);
    win32hle_register(win32hle_winmm);
    win32hle_register(win32hle_avifil32);
    win32hle_register(win32hle_msvfw32);                 /* the AVI decompressor API */
    win32hle_register(win32hle_lz32);
    win32hle_register(win32hle_wsock32);
    win32hle_register(win32hle_ole32);
    win32hle_register(win32hle_storage);
    win32hle_register(win32hle_advapi32);
    win32hle_register(win32hle_dsound);
    win32hle_register(win32hle_ddraw);
    win32hle_register(win32hle_sdl12);
    win32hle_register(win32hle_sdlmix);
    win32hle_register(win32hle_gl32);
    win32hle_register(win32hle_msvcrt);
    win32hle_register(win32hle_kernel32_atom);
    win32hle_register(win32hle_dinput);
    win32hle_register(win32hle_bink);
    win32hle_register(win32hle_mss);
    hle_dsound_register_com();
}

/* Weak default: no data imports until msvcrt.c is linked in, which is when
 * the five msvcrt data names stop being reported unresolved. A weak
 * definition, not a weak declaration -- a declaration with no definition
 * links as 0 on ELF but breaks the Mach-O link. */
__attribute__((weak)) uint32_t hle_msvcrt_data(const char *name) {
    (void)name;
    return 0;
}

/* A DATA import's guest address, or 0. */
static uint32_t data_import(const char *name) {
    return hle_msvcrt_data(name);
}

/* The IAT resolver for a real bind: a data import first (its slot must hold the
 * object's own address), then a function shim. Both the strict and the
 * permissive boot go through here, so a data import resolves the same way
 * either way. */
static uint32_t hle_resolve_import(const char *name) {
    uint32_t data = data_import(name);
    return data ? data : hle_resolve(name);
}

/* Permissive bind: data import, then a shim, then a self-naming stub. The
 * data step has to come first -- a stub VA in a data slot is a pointer into
 * the shim registry, which the guest would dereference. */
static uint32_t hle_resolve_import_or_stub(const char *name) {
    uint32_t data = data_import(name);
    if (data) return data;
    return hle_resolve_or_stub(name);
}

/* A Windows shortcut starts a game in its own folder with the install drive as
 * C:; titles resolve data relative to the current directory (`.\YMBE.AFP`).
 * Mirror that instead of inheriting whatever directory the launcher happened
 * to use. */
static void enter_image_directory(const char *image) {
    char dir[1024];
    snprintf(dir, sizeof dir, "%s", image);
    char *slash = strrchr(dir, '/');
    if (!slash) return;
    if (slash == dir) slash[1] = 0; else *slash = 0;
    hle_set_drive('C', dir);
    if (chdir(dir) != 0)
        fprintf(stderr, "[host] cannot enter %s: %s\n", dir, strerror(errno));
}

static int boot(const char *path, uint32_t (*resolve)(const char *)) {
    char image[1024];
    if (!realpath(path, image)) { fprintf(stderr, "[host] cannot find %s: %s\n", path, strerror(errno)); return 1; }
    enter_image_directory(image);
    if (recomp_pe_map(image, &g_img) != 0) return 1;
    char guest[1024];
    hle_guest_path(image, guest, sizeof guest);
    hle_module_add(guest, g_img.base, g_img.resource_rva, g_img.resource_size, 1);
    int unresolved = recomp_pe_bind(&g_img, resolve);
    g_booted = 1;
    fprintf(stderr, "[host] %s booted: entry 0x%08X, %d unresolved imports\n",
            image, g_img.entry, unresolved);
    return unresolved;
}

int recomp_host_boot(const char *path) {
    return boot(path, hle_resolve_import) ? 2 : 0;  /* fail if anything unresolved */
}
int recomp_host_boot_permissive(const char *path) {
    boot(path, hle_resolve_import_or_stub);         /* unresolved -> self-naming stub */
    return g_booted ? 0 : 1;
}

uint32_t recomp_host_entry(void) { return g_booted ? g_img.entry : 0; }

uint32_t recomp_host_run(void) {
    if (!g_booted) { hle_fatal("recomp_host_run before boot"); }
    /* The CRT entry takes no arguments; it reads the command line itself
     * (GetCommandLineA). Run it on this thread's guest stack. */
    uint32_t args[1];
    return hle_call_guest(g_img.entry, 0, args);
}

/* The guest was started by a Windows shortcut: its own exe, then the
 * arguments the user gave this launcher. GetCommandLineA and the CRT's argv
 * must answer that, not the recompiler's path. The image is always on C: at
 * its own directory (enter_image_directory), so the guest exe is C:\<name>. */
static void set_guest_arguments(int argc, char **argv) {
    static char image[512], line[2048], pool[2048];
    static char *vec[256];
    const char *base = strrchr(argv[1], '/');
    base = base ? base + 1 : argv[1];
    snprintf(image, sizeof image, "C:\\%s", base);
    int n = snprintf(line, sizeof line, "\"%s\"", image), vn = 0, pn = 0;
    vec[vn++] = image;
    for (int i = 2; i < argc && vn < 255; i++) {
        const char *a = argv[i];
        int quote = (*a == 0 || strpbrk(a, " \t\"") != NULL);
        char *copy = pool + pn;
        if (quote && pn + 1 < (int)sizeof pool) pool[pn++] = '"';
        for (; *a && pn < (int)sizeof pool - 2; a++) {
            if (*a == '"' && quote && pn < (int)sizeof pool - 3) pool[pn++] = '\\';
            pool[pn++] = *a;
        }
        if (quote && pn < (int)sizeof pool - 1) pool[pn++] = '"';
        pool[pn++] = 0;
        vec[vn++] = copy;
        if (n < (int)sizeof line - 1) n += snprintf(line + n, sizeof line - n, " %s", copy);
    }
    hle_set_command_line(line);
    hle_set_argv(vn, vec);
}

int recomp_host_main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <image.exe> [args...]\n", argv[0]); return 64; }
    /* RECOMP_TRACE=1 logs every import dispatch. Without a hook the only way
     * to see which shim a guest reaches was to recompile the layer, which is
     * what made the last audit report "no shim-level runtime trace" as a
     * method limit. */
    {
        const char *trace = getenv("RECOMP_TRACE");
        win32hle_trace = trace && *trace && *trace != '0';
    }
    recomp_host_init();
    set_guest_arguments(argc, argv);
    int rc = recomp_host_boot(argv[1]);
    if (rc) return rc;
    return (int)recomp_host_run();
}
