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
#include <strings.h>
#include <sys/stat.h>
#ifdef __APPLE__
#include <ucontext.h>
#include <mach-o/dyld.h>
#elif defined(__linux__)
#include <ucontext.h>
#endif
#include "win32hle.h"
#include "pe_loader.h"
#include "pe_format.h"
#include "host.h"

static pe_image g_img;
static int      g_booted;

/* main.c's image loader sets these for the static-TLS template; make_tib (in
 * host_lite.c) copies them into each thread's block. */
extern uint32_t g_tls_raw_start, g_tls_raw_end, g_tls_zero_fill, g_tls_index;

/* Read the image's TLS directory and publish the template for make_tib, and
 * write the module's _tls_index cell. Windows' loader gives every module a TLS
 * slot; with one main image the index is 0. A module with no TLS directory
 * leaves the template empty, which make_tib reads as "no block". */
static void setup_static_tls(const pe_image *img) {
    g_tls_raw_start = g_tls_raw_end = g_tls_zero_fill = g_tls_index = 0;
    /* The mapped image starts with the DOS header, so the NT headers are at
     * base + e_lfanew (not base). */
    pe_dos_header *dos = (pe_dos_header *)hle_ptr(img->base);
    if (dos->e_magic != 0x5A4D) return;
    pe_nt_headers32 *nt = (pe_nt_headers32 *)hle_ptr(img->base + (uint32_t)dos->e_lfanew);
    pe_opt_header32 *opt = &nt->OptionalHeader;
    if (opt->NumberOfRvaAndSizes <= PE_DIR_TLS) return;
    pe_data_dir d = opt->DataDirectory[PE_DIR_TLS];
    if (!d.VirtualAddress || !d.Size) return;
    pe_tls_dir32 *tls = (pe_tls_dir32 *)hle_ptr(img->base + d.VirtualAddress);
    uint32_t start = tls->StartAddressOfRawData;
    uint32_t end = tls->EndAddressOfRawData;
    if (end <= start) return;
    g_tls_raw_start = start;
    g_tls_raw_end = end;
    g_tls_zero_fill = tls->SizeOfZeroFill;
    g_tls_index = 0;                          /* one image -> slot 0 */
    if (tls->AddressOfIndex) MEM32(tls->AddressOfIndex) = g_tls_index;
    fprintf(stderr, "[pe] static TLS: template 0x%08X..0x%08X (+%u zero), index %u\n",
            start, end, g_tls_zero_fill, g_tls_index);
}

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
    win32hle_register(win32hle_wininet);
    hle_dsound_register_com();
    hle_user32_enable_desktop_window(1);
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

/* Weak defaults for the generated bundled-DLL table. A project with no
 * companions does not define these; one with them does, and the linker picks
 * the generated definitions. Weak *definitions* rather than declarations,
 * for the same reason as hle_msvcrt_data above (Mach-O links a bare extern as
 * address 0). */
__attribute__((weak)) const recomp_companion_t *const recomp_companion_list = NULL;
__attribute__((weak)) const uint32_t recomp_companion_count = 0;

/* The rebuilt binary's embedded copy of the original PE (recomp_image.c/.S in
 * the generated project). Weak *definitions*, like companion_list above: a
 * project that does not embed an image keeps these defaults, and a project
 * that does links the generated strong definitions over them. The flag says
 * which, so the map is never attempted against the 1-byte placeholder. */
__attribute__((weak)) const unsigned char recomp_original_image[1] = { 0 };
__attribute__((weak)) const unsigned long recomp_original_image_size = 0;
__attribute__((weak)) const char recomp_original_name[1] = { 0 };
__attribute__((weak)) const int recomp_has_embedded_image = 0;

static int have_embedded_image(void) { return recomp_has_embedded_image; }

/* ---- bundled DLLs -------------------------------------------------------
 *
 * The pipeline reconstructs a bundled game DLL's functions into the same
 * dispatch table as the game, at the DLL's preferred ImageBase, and generates
 * recomp_companion_list (win32hle.h). Boot maps each DLL's data at that base
 * so the lifted code's MEM32(va) references land, then the IAT binder answers
 * the game's imports from the DLL's exports -- the export VA IS the lifted
 * function's VA, so the call runs the reconstructed code.
 *
 * The DLL's own import table is bound the same way the game's is: its IAT
 * slots hold raw RVAs until something writes them, and its lifted code calls
 * through those slots. */
static uint32_t hle_resolve_import(const char *name);
static uint32_t companion_import(const char *dll, const char *name);

static int map_companions(void) {
    if (!recomp_companion_count) return 0;
    /* Two passes: map (reserve the VAs of) every companion first, then bind and
     * run DllMain. A companion's DllMain allocates through the guest heap,
     * whose first-fit allocator starts at 0x10000000; mapping in one loop ran
     * each DllMain before reserving the next companion's base, and the heap
     * could take it. Each image is either embedded in this binary (image !=
     * NULL) or, for a build that does not embed companions, read from
     * `load`/beside the game. */
    enum { MAX_COMP = 32 };
    static pe_image imgs[MAX_COMP];
    if (recomp_companion_count > MAX_COMP) {
        fprintf(stderr, "[pe] too many companions (%u)\n", recomp_companion_count);
        return 1;
    }
    for (uint32_t i = 0; i < recomp_companion_count; i++) {
        const recomp_companion_t *c = &recomp_companion_list[i];
        int rc;
        if (c->image && c->image_size) {
            rc = recomp_pe_map_mem(c->image, c->image_size, &imgs[i]);
        } else {
            char host[1200];
            if (c->load && c->load[0]) snprintf(host, sizeof host, "%s", c->load);
            else if (!hle_host_path(c->dll, host, sizeof host)) {
                fprintf(stderr, "[pe] companion %s: not found beside the game\n", c->dll);
                return 1;
            }
            rc = recomp_pe_map(host, &imgs[i]);
        }
        /* The mapped image's base must be its preferred one: the dispatch
         * table entries and every generated address are absolute for it. */
        if (rc != 0 || imgs[i].base != c->base) {
            fprintf(stderr, "[pe] companion %s: cannot map at 0x%08X\n", c->dll, c->base);
            return 1;
        }
        hle_module_add(c->dll, imgs[i].base, imgs[i].resource_rva, imgs[i].resource_size, 0);
    }
    for (uint32_t i = 0; i < recomp_companion_count; i++) {
        const recomp_companion_t *c = &recomp_companion_list[i];
        pe_image img = imgs[i];
        /* The DLL's own imports (its C runtime and kernel32 calls) are bound
         * exactly like the game's: its IAT slots are the raw RVAs from the
         * file until something writes them, and its lifted code calls them
         * through the IAT like anyone else's. */
        int dll_unresolved = recomp_pe_bind_companions(&img, hle_resolve_import,
                                                       companion_import);
        if (dll_unresolved)
            fprintf(stderr, "[pe] companion %s: %d unresolved imports\n",
                    c->dll, dll_unresolved);
        /* Run DllMain(DLL_PROCESS_ATTACH), the way the Windows loader does.
         * A DLL's CRT static initialisers live behind its entry point -- the
         * global std::map/string/singletons its exported functions assume
         * exist are constructed there. Skipping this left every such global
         * null: BulletML's parser crashed reading a map head that was still
         * zero because DllMain had never run. The entry is lifted like any
         * other function; call it with (hinstDLL, DLL_PROCESS_ATTACH, 0). */
        uint32_t attach[3] = { img.base, 1u /* DLL_PROCESS_ATTACH */, 0u };
        uint32_t ok = hle_call_guest(img.entry, 3, attach);
        if (!ok)
            fprintf(stderr, "[pe] companion %s: DllMain(DLL_PROCESS_ATTACH) returned 0\n",
                    c->dll);
        fprintf(stderr, "[pe] companion %s mapped at 0x%08X (%u exports)\n",
                c->dll, img.base, c->export_count);
    }
    return 0;
}

/* The import a bundled DLL answers, or 0. */
static uint32_t companion_import(const char *dll, const char *name) {
    if (!recomp_companion_count || !dll || !name) return 0;
    /* Base names only: the import table carries a path, the companion table a
     * bare file name. */
    const char *d = dll, *n = name;
    for (const char *p = dll; *p; p++) if (*p == '\\' || *p == '/') d = p + 1;
    for (uint32_t i = 0; i < recomp_companion_count; i++) {
        const recomp_companion_t *c = &recomp_companion_list[i];
        const char *cb = c->dll;
        for (const char *p = c->dll; *p; p++) if (*p == '\\' || *p == '/') cb = p + 1;
        size_t k = 0;
        while (d[k] && cb[k] && (d[k] == cb[k] || (d[k] | 32) == (cb[k] | 32))) k++;
        if (d[k] || cb[k]) continue;                     /* not this companion */
        /* An ordinal import ("dll#N") has no name to match by. */
        if (n[0] == '#') return 0;
        for (uint32_t e = 0; e < c->export_count; e++)
            if (c->exports[e].name && !strcmp(c->exports[e].name, n))
                return c->exports[e].va;
        return 0;
    }
    return 0;
}

/* Declared in win32hle.h; the binder-facing wrapper around the table lookup. */
uint32_t recomp_companion_resolve(const char *dll, const char *name) {
    return companion_import(dll, name);
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

static int finish_boot(const char *log_name, const char *guest_name,
                       uint32_t (*resolve)(const char *)) {
    hle_module_add(guest_name, g_img.base, g_img.resource_rva, g_img.resource_size, 1);
    /* static TLS: the template a thread's block is copied from (host_lite.c
     * make_tib). Must run before the first lifted call, which is when a
     * thread's TIB is built. */
    setup_static_tls(&g_img);
    /* Bundled DLLs first: their images must be at their preferred base before
     * the main IAT is bound, because a bound slot holds an address inside one
     * of them. A companion that will not map is a boot failure in strict mode
     * (its code is what the game calls); permissive mode carries on so the
     * run still reports the first thing the guest reaches. */
    int comp_ok = (map_companions() == 0);
    int unresolved = recomp_pe_bind_companions(&g_img, resolve,
                                               comp_ok ? companion_import : NULL);
    g_booted = 1;
    fprintf(stderr, "[host] %s booted: entry 0x%08X, %d unresolved imports\n",
            log_name, g_img.entry, unresolved);
    return unresolved;
}

static int boot(const char *path, uint32_t (*resolve)(const char *)) {
    char image[1024];
    if (!realpath(path, image)) { fprintf(stderr, "[host] cannot find %s: %s\n", path, strerror(errno)); return -1; }
    enter_image_directory(image);
    if (recomp_pe_map(image, &g_img) != 0) return -1;
    char guest[1024];
    hle_guest_path(image, guest, sizeof guest);
    return finish_boot(image, guest, resolve);
}

/* Boot from the image embedded in this binary: the original executable is not
 * needed. `assets_dir`, when given, is where the game's own data files live; it
 * becomes the working directory (and the C: drive), exactly as running beside
 * the original exe did. Companions (a bundled DLL the game ships) are still
 * resolved relative to that directory -- they are the game's files. */
static int boot_embedded(const char *assets_dir, uint32_t (*resolve)(const char *)) {
    char dir[1024];
    if (assets_dir && *assets_dir && realpath(assets_dir, dir)) {
        hle_set_drive('C', dir);
        if (chdir(dir) != 0)
            fprintf(stderr, "[host] cannot enter %s: %s\n", dir, strerror(errno));
    } else if (getcwd(dir, sizeof dir)) {
        /* No assets argument: the working directory is where the game was
         * launched, the way running beside the original exe worked. Map it to
         * C:, as enter_image_directory does for the file-based boot. */
        hle_set_drive('C', dir);
    }
    size_t n = (size_t)recomp_original_image_size;
    if (recomp_pe_map_mem(recomp_original_image, n, &g_img) != 0) return -1;
    char guest[512];
    const char *name = recomp_original_name[0] ? recomp_original_name : "game.exe";
    snprintf(guest, sizeof guest, "C:\\%s", name);
    return finish_boot(guest, guest, resolve);
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
static void set_guest_arguments(const char *exe_name, int argc, char **argv, int first) {
    static char image[512], line[2048], pool[2048];
    static char *vec[256];
    snprintf(image, sizeof image, "C:\\%s", exe_name ? exe_name : "game.exe");
    int n = snprintf(line, sizeof line, "\"%s\"", image), vn = 0, pn = 0;
    vec[vn++] = image;
    for (int i = first; i < argc && vn < 255; i++) {
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

/* Whether `path` names an existing directory (the optional assets argument of
 * an embedded run). */
static int is_directory(const char *path) {
    struct stat st;
    return path && stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

/* Whether `path` is an existing file ending in .exe -- the original executable,
 * offered out of habit or by a test harness even though it is embedded. */
static int is_exe_file(const char *path) {
    struct stat st;
    if (!path || stat(path, &st) != 0 || !S_ISREG(st.st_mode)) return 0;
    size_t n = strlen(path);
    return n > 4 && (path[n-4] == '.') &&
           (path[n-3] | 0x20) == 'e' && (path[n-2] | 0x20) == 'x' && (path[n-1] | 0x20) == 'e';
}

/* The directory containing `path`, into `out`; returns out. */
static const char *parent_dir(const char *path, char *out, size_t n) {
    snprintf(out, n, "%s", path ? path : "");
    char *sl = strrchr(out, '/');
    if (sl) { if (sl == out) sl[1] = 0; else *sl = 0; }
    else snprintf(out, n, ".");
    return out;
}

/* The base name of the original executable the rebuilt binary was made from:
 * the embedded name when present, else the path's last component. */
static const char *original_basename(const char *path) {
    if (have_embedded_image() && recomp_original_name[0])
        return recomp_original_name;
    if (!path) return "game.exe";
    const char *b = strrchr(path, '/');
    return b ? b + 1 : path;
}

static const char *path_basename(const char *path) {
    if (!path) return "";
    const char *b = strrchr(path, '/');
    return b ? b + 1 : path;
}

int recomp_host_main(int argc, char **argv) {
    /* RECOMP_TRACE=1 logs every import dispatch. Without a hook the only way
     * to see which shim a guest reaches was to recompile the layer, which is
     * what made the last audit report "no shim-level runtime trace" as a
     * method limit. */
    {
        const char *trace = getenv("RECOMP_TRACE");
        win32hle_trace = trace && *trace && *trace != '0';
    }
    const char *perm = getenv("RECOMP_PERMISSIVE");
    int permissive = perm && *perm && *perm != '0';
    uint32_t (*resolve)(const char *) =
        permissive ? hle_resolve_import_or_stub : hle_resolve_import;   /* forward-declared below */

    recomp_host_init();

    if (have_embedded_image()) {
        /* The original executable is embedded: it is not needed at run time.
         * RECOMP_ASSETS_DIR, or a single directory argument (argc == 2), names
         * where the game's own data files live; when argv[1] names the
         * original .exe (a harness, or an old invocation) its directory is used
         * instead and it is not passed to the guest. Anything else is a guest
         * argument so multi-argument CLI invocations keep their arguments. */
        const char *env_assets = getenv("RECOMP_ASSETS_DIR");
        const char *assets = (env_assets && *env_assets) ? env_assets : NULL;
        char dirbuf[1024];
        int first = 1;
        if (argc > 1) {
            if (is_exe_file(argv[1]) &&
                (!recomp_original_name[0] || !strcasecmp(path_basename(argv[1]), recomp_original_name))) {
                if (!assets) assets = parent_dir(argv[1], dirbuf, sizeof dirbuf);
                first = 2;
            } else if (argc == 2 && is_directory(argv[1])) {
                if (!assets) assets = argv[1];
                first = 2;
            }
        }
        set_guest_arguments(original_basename(NULL), argc, argv, first);
        int unresolved = boot_embedded(assets, resolve);
        if (unresolved < 0) return 1;                      /* map failed */
        if (unresolved && !permissive) return 2;
        return (int)recomp_host_run();
    }

    if (argc < 2) {
        fprintf(stderr,
                "usage: %s <original-game.exe> [args...]\n"
                "The rebuilt binary carries the game's reconstructed code; this\n"
                "one was built without an embedded image, so the original Windows\n"
                "executable still supplies its data. Point it at the game file.\n",
                argv[0]);
        return 64;
    }
    set_guest_arguments(original_basename(argv[1]), argc, argv, 2);
    int unresolved = boot(argv[1], resolve);
    if (unresolved < 0) return 1;
    if (unresolved && !permissive) return 2;
    return (int)recomp_host_run();
}
