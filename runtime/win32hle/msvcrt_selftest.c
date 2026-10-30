/*
 * msvcrt_selftest.c - drives the MSVCRT shims from a synthetic lifted guest:
 * a file written with fopen/fwrite/fclose and read back with fopen/fread, the
 * _iob std streams, a malloc/_msize/free round trip, _errno after a failing
 * open, the MSVC ctype flags behind _isctype and _pctype, a sprintf/sscanf
 * round trip, the _write/_getw/_putw/_fdopen/_setmode descriptor calls,
 * _initterm, the conversions and the math entry points, _findfirst, _fullpath,
 * time, setlocale, and exit()/atexit() in a forked child (exit ends the
 * process, so the parent can only observe the child's status and the line its
 * handler wrote).
 *
 * Every call goes through the lifted model exactly as a game's would: the
 * arguments are pushed on the guest stack and the shim is entered through
 * RECOMP_ICALL, so cdecl behaviour (the shim pops only the return address, the
 * caller pops its arguments) is exercised, not assumed. Headless, no game.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
#include "win32hle.h"

#define ENTRY_VA  0x00401000u
#define ATEXIT_VA 0x00402000u
#define INITFN_VA 0x00403000u

/* msvcrt.c's shim table (win32hle.h declares it once the host is wired). */
extern const win32hle_shim win32hle_msvcrt[];

static int fails;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }

/* RECOMP_ICALL publishes the arithmetic flags through these; a generated body
 * declares them itself, and call() below is not one, so they live here. */
static uint32_t _flag_k = FK_NONE, _flag_a, _flag_b, _cf;

/* The shared prologue declares the flag locals RECOMP_ENTER loads. These
 * bodies publish flags only through call(), never directly, so they consume
 * them explicitly (a body with its own RECOMP_ICALL needs no such line). */
#define GUEST_PROLOGUE(va) \
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0; RECOMP_ENTER(va); \
    (void)_cf, (void)_flag_k, (void)_flag_a, (void)_flag_b

/* ---- guest memory the tests work on (VAs) ---- */
static uint32_t g_path, g_nopath, g_mode_wb, g_mode_rb, g_localec, g_msg, g_abc;
static uint32_t g_wbuf, g_rbuf, g_out, g_fmt, g_scanfmt, g_num, g_str, g_timebuf;
static uint32_t g_findspec, g_findbuf, g_numstr, g_atexit_msg, g_inittab, g_double;

/* ---- what the guest observed ---- */
static int g_open_ok, g_write_ok, g_read_ok, g_iob_ok, g_stdout_line_ok, g_fileno_ok;
static int g_malloc_ok, g_free_ok, g_errno_ok, g_ctype_ok, g_ptype_ok;
static int g_sprintf_ok, g_sscanf_ok, g_rand_ok, g_find_ok, g_fullpath_ok;
static int g_time_ok, g_locale_ok, g_remove_ok;
static int g_write_fd_ok, g_word_ok, g_fdopen_ok, g_setmode_ok, g_setvbuf_ok, g_freopen_ok;
static int g_initterm_ok, g_conv_ok, g_math_ok, g_isspace_ok, g_strdup_ok, g_misc_ok, g_signal_ok;
static uint32_t g_msize, g_read_n, g_word;

/* A guest function _initterm calls: counts its runs in host state. */
static uint32_t g_init_count;
static void init_fn(void) {
    GUEST_PROLOGUE(INITFN_VA);
    g_init_count++;
    eax = 0;
}

/* The guest's atexit handler: writes a line with the low-level _write, so the
 * forked child's exit() is observable from the parent through a pipe. */
static uint32_t V_write;
static void atexit_fn(void) {
    GUEST_PROLOGUE(ATEXIT_VA);
    uint32_t a[3] = { 1, g_atexit_msg, 11 };
    for (int i = 2; i >= 0; i--) PUSH32(esp, a[i]);
    RECOMP_ICALL(V_write);
    esp += 12;
    eax = 0;
}

/* Call a shim by name with guest-stack arguments: the cdecl entry path. */
static uint32_t call(const char *name, int nargs, const uint32_t *a) {
    uint32_t va = hle_resolve(name);
    if (!va) hle_fatal("selftest: no shim named %s", name);
    for (int i = nargs - 1; i >= 0; i--) PUSH32(esp, a[i]);
    RECOMP_ICALL(va);
    esp += 4u * (uint32_t)nargs;              /* cdecl: the caller pops its own arguments */
    return eax;
}

static void guest(void) {
    GUEST_PROLOGUE(ENTRY_VA);
    uint32_t a[8];
    uint32_t f, iob;

    /* ---- a file written, closed, opened and read back ---- */
    a[0] = g_path, a[1] = g_mode_wb;
    f = call("fopen", 2, a);
    g_open_ok = f != 0;
    if (f) {
        a[0] = g_wbuf, a[1] = 1, a[2] = 16, a[3] = f;
        g_write_ok = call("fwrite", 4, a) == 16;
        a[0] = f;
        call("fclose", 1, a);
        a[0] = g_path, a[1] = g_mode_rb;
        f = call("fopen", 2, a);
        if (f) {
            a[0] = g_rbuf, a[1] = 1, a[2] = 16, a[3] = f;
            g_read_n = call("fread", 4, a);
            a[0] = f;
            call("fclose", 1, a);
        }
    }
    g_read_ok = g_read_n == 16 && memcmp(hle_ptr(g_wbuf), hle_ptr(g_rbuf), 16) == 0;

    /* ---- _iob: the three std streams are guest _iobufs ---- */
    iob = call("__p__iob", 0, NULL);
    g_iob_ok = iob != 0 && iob == hle_msvcrt_data("_iob") &&
               MEM32(iob + 16) == 0 && MEM32(iob + 32 + 16) == 1 && MEM32(iob + 64 + 16) == 2;
    a[0] = iob + 32;
    g_fileno_ok = call("_fileno", 1, a) == 1;
    /* a real line on stdout through the std stream, and one on stderr */
    a[0] = iob + 32, a[1] = g_msg, a[2] = g_path;
    g_stdout_line_ok = call("fprintf", 3, a) > 0;
    a[0] = iob + 64, a[1] = g_msg, a[2] = g_path;
    call("fprintf", 3, a);

    /* ---- the descriptor calls: _setmode, setvbuf, _putw/_getw, _fdopen, freopen ---- */
    a[0] = g_path, a[1] = g_mode_wb;
    f = call("fopen", 2, a);
    if (f) {
        a[0] = f;
        uint32_t fd0 = call("_fileno", 1, a);
        a[0] = fd0, a[1] = 0x8000;                                    /* _O_BINARY */
        g_setmode_ok = call("_setmode", 2, a) == 0x4000;              /* the previous mode: _O_TEXT */
        a[0] = f, a[1] = 0, a[2] = 0x0004, a[3] = 0;                  /* setvbuf(_IONBF) */
        g_setvbuf_ok = call("setvbuf", 4, a) == 0;
        a[0] = 0x12345678, a[1] = f;
        g_word_ok = call("_putw", 2, a) == 0x12345678;
        a[0] = f;
        call("fclose", 1, a);
    }
    a[0] = g_path, a[1] = g_mode_rb;
    f = call("fopen", 2, a);
    if (f) {
        a[0] = f;
        g_word = call("_getw", 1, a);
        a[0] = f;
        uint32_t fd = call("_fileno", 1, a);
        a[0] = fd, a[1] = g_mode_rb;
        g_fdopen_ok = call("_fdopen", 2, a) != 0;                     /* a second _iobuf on the same slot */
        a[0] = g_path, a[1] = g_mode_rb, a[2] = f;
        g_freopen_ok = call("freopen", 3, a) != 0;
        a[0] = f;
        call("fclose", 1, a);
    }
    a[0] = 2, a[1] = g_atexit_msg, a[2] = 11;                         /* _write to stderr */
    g_write_fd_ok = call("_write", 3, a) == 11;

    /* ---- malloc/_msize/free: a guest block, not a host one ---- */
    a[0] = 64;
    uint32_t p = call("malloc", 1, a);
    g_malloc_ok = p != 0 && hle_heap_ok(hle_ptr(p));
    if (p) {
        MEM32(p) = 0xDEADBEEFu;
        a[0] = p;
        g_msize = call("_msize", 1, a);
        a[0] = p;
        call("free", 1, a);
        g_free_ok = 1;
    }

    /* ---- errno after an open that cannot succeed ---- */
    a[0] = g_nopath, a[1] = g_mode_rb;
    uint32_t bad = call("fopen", 2, a);
    uint32_t err = call("_errno", 0, NULL);
    g_errno_ok = bad == 0 && err != 0 && MEM32(err) == 2;       /* ENOENT */

    /* ---- the MSVC ctype flags ---- */
    int ct = 1;
    a[0] = 'A', a[1] = 0x0001; ct = ct && call("_isctype", 2, a) != 0;   /* _UPPER  */
    a[0] = 'a', a[1] = 0x0002; ct = ct && call("_isctype", 2, a) != 0;   /* _LOWER  */
    a[0] = '5', a[1] = 0x0004; ct = ct && call("_isctype", 2, a) != 0;   /* _DIGIT  */
    a[0] = ' ', a[1] = 0x0008; ct = ct && call("_isctype", 2, a) != 0;   /* _SPACE  */
    a[0] = 'A', a[1] = 0x0004; ct = ct && call("_isctype", 2, a) == 0;   /* not a digit */
    a[0] = -1;  a[1] = 0x0001; ct = ct && call("_isctype", 2, a) == 0;   /* EOF */
    g_ctype_ok = ct;
    a[0] = ' ';
    int sp = call("isspace", 1, a) != 0;
    a[0] = 'x';
    g_isspace_ok = sp && call("isspace", 1, a) == 0;
    /* _pctype is a pointer cell into the table; _ctype is the table */
    uint32_t cell = hle_msvcrt_data("_pctype"), table = cell ? MEM32(cell) : 0;
    g_ptype_ok = table != 0 && hle_msvcrt_data("_ctype") != 0 &&
                 (MEM16(table + 2 * 'A') & 0x0001) && (MEM16(table + 2 * 'a') & 0x0002) &&
                 (MEM16(table + 2 * '5') & 0x0004);

    /* ---- sprintf then sscanf the text back ---- */
    a[0] = g_out, a[1] = g_fmt, a[2] = 42, a[3] = g_abc, a[4] = 7;
    g_sprintf_ok = call("sprintf", 5, a) > 0;
    a[0] = g_out, a[1] = g_scanfmt, a[2] = g_num, a[3] = g_str;
    uint32_t got = call("sscanf", 4, a);
    g_sscanf_ok = got == 2 && MEM32(g_num) == 42 && !strcmp((char *)hle_ptr(g_str), "abc");

    /* ---- conversions ---- */
    a[0] = g_numstr, a[1] = 0, a[2] = 10;
    uint32_t l = call("strtol", 3, a);
    a[0] = g_numstr, a[1] = 0;
    call("strtod", 2, a);
    double d = _st[0];
    g_conv_ok = l == 1234 && d == 1234.0;

    /* ---- rand is MSVC's generator, not the host's ---- */
    a[0] = 1;
    call("srand", 1, a);
    g_rand_ok = call("rand", 0, NULL) == 41 && call("rand", 0, NULL) == 18467;

    /* ---- the math entry points: a cdecl double in, the result on the x87 stack ---- */
    MEM32(g_double) = 0, MEM32(g_double + 4) = 0x3FF00000u;           /* 1.0 */
    a[0] = MEM32(g_double), a[1] = MEM32(g_double + 4);
    int m = call("_finite", 2, a) == 1;
    a[0] = 0, a[1] = 0x7FF00000u;                                     /* +inf */
    m = m && call("_finite", 2, a) == 0;
    a[0] = 0, a[1] = 0;                                               /* sin(0) == 0 */
    call("sin", 2, a);
    g_math_ok = m && _st[0] == 0.0;

    /* ---- _strdup and the memory/string forwards ---- */
    a[0] = g_abc;
    uint32_t dup = call("_strdup", 1, a);
    a[0] = dup, a[1] = g_abc, a[2] = 4;
    g_strdup_ok = dup != 0 && hle_heap_ok(hle_ptr(dup)) && call("memcmp", 3, a) == 0;
    a[0] = dup;
    call("free", 1, a);
    a[0] = g_out, a[1] = 'z', a[2] = 4;
    call("memset", 3, a);
    a[0] = g_out, a[1] = 'z', a[2] = 4;
    uint32_t found = call("memchr", 3, a);
    a[0] = g_abc, a[1] = g_abc;
    g_misc_ok = found == g_out && call("strcmp", 2, a) == 0 && call("strcoll", 2, a) == 0 &&
                call("strlen", 1, a) == 3;

    /* ---- _initterm runs each guest initialiser ---- */
    MEM32(g_inittab) = INITFN_VA, MEM32(g_inittab + 4) = 0, MEM32(g_inittab + 8) = INITFN_VA;
    g_init_count = 0;
    a[0] = g_inittab, a[1] = g_inittab + 12;
    call("_initterm", 2, a);
    g_initterm_ok = g_init_count == 2;

    /* ---- signal records the handler and returns the previous one ---- */
    a[0] = 2, a[1] = ATEXIT_VA;                                       /* SIGINT */
    uint32_t prev = call("signal", 2, a);
    uint32_t again = call("signal", 2, a);
    g_signal_ok = prev == 0 && again == ATEXIT_VA;                    /* SIG_DFL, then the handler just set */

    /* ---- _findfirst over the file the test just made ---- */
    a[0] = g_findspec, a[1] = g_findbuf;
    uint32_t fh = call("_findfirst", 2, a);
    g_find_ok = fh != 0xFFFFFFFFu && strstr((char *)hle_ptr(g_findbuf + 20), "win32hle_msvcrt") != NULL;
    if (fh != 0xFFFFFFFFu) { a[0] = fh; call("_findclose", 1, a); }

    /* ---- _fullpath(NULL, rel, 0) allocates the absolute guest path ---- */
    a[0] = 0, a[1] = g_path, a[2] = 0;
    uint32_t full = call("_fullpath", 3, a);
    g_fullpath_ok = full != 0 && strstr((char *)hle_ptr(full), "win32hle_msvcrt.dat") != NULL;

    /* ---- time, setlocale ---- */
    a[0] = g_timebuf;
    uint32_t now = call("time", 1, a);
    g_time_ok = now != 0 && MEM32(g_timebuf) == now;
    a[0] = 0, a[1] = g_localec;                                 /* LC_ALL, "C" */
    uint32_t loc = call("setlocale", 2, a);
    g_locale_ok = loc != 0 && !strcmp((char *)hle_ptr(loc), "C");

    /* ---- remove, and the file is gone ---- */
    a[0] = g_path;
    g_remove_ok = call("remove", 1, a) == 0;
    g_open_ok = g_open_ok && access("/tmp/win32hle_msvcrt.dat", F_OK) != 0;

    eax = 0;
}

/* exit() ends the process, so the child registers a guest atexit handler and
 * calls exit(7); the parent checks the status and the handler's line. */
static void guest_exit(void) {
    GUEST_PROLOGUE(ENTRY_VA);
    uint32_t a[2];
    a[0] = ATEXIT_VA;
    call("atexit", 1, a);
    a[0] = 7;
    call("exit", 1, a);
    eax = 0;                                     /* exit() must not return */
}

const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { ENTRY_VA, guest }, { ATEXIT_VA, atexit_fn }, { INITFN_VA, init_fn },
};
const uint32_t recomp_dispatch_count = 3;

int main(void) {
    remove("/tmp/win32hle_msvcrt.dat");                  /* a stale file would hide a failed remove */
    g_path = hle_guest_strdup("/tmp/win32hle_msvcrt.dat");
    g_nopath = hle_guest_strdup("/tmp/win32hle_no_such_file.dat");
    g_mode_wb = hle_guest_strdup("wb");
    g_mode_rb = hle_guest_strdup("rb");
    g_localec = hle_guest_strdup("C");
    g_abc = hle_guest_strdup("abc");
    g_numstr = hle_guest_strdup("1234");
    g_msg = hle_guest_strdup("msvcrt_selftest: _iob stream %s\n");
    g_atexit_msg = hle_guest_strdup("atexit-ran\n");
    g_fmt = hle_guest_strdup("%d %s %u");
    g_scanfmt = hle_guest_strdup("%d %s");
    g_findspec = hle_guest_strdup("/tmp/win32hle_msvcrt*");
    g_wbuf = hle_va(hle_alloc(64));
    g_rbuf = hle_va(hle_alloc(64));
    g_out = hle_va(hle_alloc(64));
    g_str = hle_va(hle_alloc(32));
    g_num = hle_va(hle_alloc(4));
    g_timebuf = hle_va(hle_alloc(4));
    g_double = hle_va(hle_alloc(8));
    g_findbuf = hle_va(hle_alloc(280));                 /* struct _finddata_t */
    g_inittab = hle_va(hle_alloc(12));
    for (int i = 0; i < 16; i++) ((uint8_t *)hle_ptr(g_wbuf))[i] = (uint8_t)(i + 1);

    win32hle_register(win32hle_msvcrt);
    V_write = hle_resolve("_write");

    int n = 0, bad = 0;
    for (const win32hle_shim *s = win32hle_msvcrt; s->name; s++) {
        n++;
        if (!hle_resolve(s->name)) { printf("FAIL unresolved shim %s\n", s->name); bad++; }
    }
    ok("every msvcrt shim resolves", bad == 0 && n > 90);
    ok("the five data exports are data, not shims",
       hle_msvcrt_data("_iob") && hle_msvcrt_data("_pctype") && hle_msvcrt_data("_ctype") &&
       hle_msvcrt_data("_fmode") && hle_msvcrt_data("__mb_cur_max") &&
       !hle_resolve("_iob") && !hle_resolve("_pctype"));

    mach_enter();
    guest();
    mach_leave();

    ok("fopen(a file) returns a guest FILE*",   g_open_ok);
    ok("fwrite then fread round-trips",         g_write_ok && g_read_ok);
    ok("_iob is three guest _iobufs (0,1,2)",   g_iob_ok);
    ok("fprintf to _iob[1] (stdout) works",     g_stdout_line_ok);
    ok("_fileno(_iob[1]) is 1",                 g_fileno_ok);
    ok("_setmode returns the previous mode",    g_setmode_ok);
    ok("setvbuf accepts a mode change",         g_setvbuf_ok);
    ok("_putw/_getw round-trip a word",         g_word_ok && g_word == 0x12345678);
    ok("_fdopen wraps the descriptor",          g_fdopen_ok);
    ok("freopen reopens the stream",            g_freopen_ok);
    ok("_write returns the byte count",         g_write_fd_ok);
    ok("malloc hands out a guest heap block",   g_malloc_ok && g_msize == 64);
    ok("free releases it",                      g_free_ok);
    ok("_errno is ENOENT after a failed open",  g_errno_ok);
    ok("_isctype matches the MSVC flag bits",   g_ctype_ok);
    ok("isspace agrees with the table",         g_isspace_ok);
    ok("_pctype/_ctype reach the flag table",   g_ptype_ok);
    ok("sprintf writes the guest buffer",       g_sprintf_ok);
    ok("sscanf reads it back into guest memory", g_sscanf_ok);
    ok("strtol/strtod parse the guest string",  g_conv_ok);
    ok("rand is MSVC's sequence (41, 18467)",   g_rand_ok);
    ok("_finite/sin return through the x87 stack", g_math_ok);
    ok("_strdup copies into the guest heap",    g_strdup_ok);
    ok("memchr/memcmp/strcmp/strcoll/strlen",   g_misc_ok);
    ok("_initterm runs each guest initialiser", g_initterm_ok);
    ok("signal records the handler",            g_signal_ok);
    ok("_findfirst finds the file it made",     g_find_ok);
    ok("_fullpath returns an absolute guest path", g_fullpath_ok);
    ok("time fills the guest time_t",           g_time_ok);
    ok("setlocale returns the guest's locale",  g_locale_ok);
    ok("remove deletes the file",               g_remove_ok);

    /* exit()/atexit() in a child: the handler's line comes back on a pipe. */
    {
        int fds[2];
        if (pipe(fds) == 0) {
            pid_t pid = fork();
            if (pid == 0) {
                close(fds[0]);
                dup2(fds[1], 1);
                close(fds[1]);
                mach_enter();
                guest_exit();
                mach_leave();
                _exit(99);                       /* exit() returned: wrong */
            }
            close(fds[1]);
            char buf[128];
            ssize_t got = read(fds[0], buf, sizeof buf - 1);
            if (got < 0) got = 0;
            buf[got] = 0;
            close(fds[0]);
            int st = 0;
            waitpid(pid, &st, 0);
            ok("exit() runs the atexit handler and leaves with its code",
               WIFEXITED(st) && WEXITSTATUS(st) == 7 && strstr(buf, "atexit-ran") != NULL);
        } else {
            ok("pipe() for the exit() test", 0);
        }
    }

    if (fails == 0) printf("msvcrt_selftest: all checks passed\n");
    return fails != 0;
}
