/*
 * msvcrt.c - the MSVCRT (MSVC C runtime) import surface, on the host's libc.
 *
 * An MSVC-built game imports its C and C++ runtime from msvcrt.dll: the startup
 * (__getmainargs, __set_app_type, _initterm), the heap (malloc/free), stdio
 * (fopen/fprintf/fwrite, _iob), the ctype tables, errno, the atexit chain,
 * rand, the math entry points, and the string/memory routines. This module
 * answers those imports for real. Nothing here reports success it did not
 * achieve: a name that cannot be answered truthfully is left out of the shim
 * table, so the loader keeps naming it instead of binding a lie.
 *
 * Two things are MSVC-specific and are laid out here rather than forwarded:
 *
 *  - A guest FILE* is a pointer to a guest _iobuf, 32 bytes on x86 MSVC:
 *    ptr, cnt, base, flag, file, charbuf, bufsiz, tmpfname. Its `file` field
 *    is this module's stream slot number, which is also the CRT descriptor the
 *    guest passes to _write/_fileno/_setmode/_fdopen. The host FILE* lives in
 *    the slot table and is never stored in guest memory. `_iob` is a
 *    guest-memory array of three _iobufs (stdin/stdout/stderr), so &_iob[1]
 *    (stdout) is a real address a game can hand back to fprintf.
 *    The ptr/cnt/base buffering fields are not maintained: the host stream
 *    owns its buffer, so the guest must not read those as a window on it.
 *  - The ctype table is the MSVC one: 257 unsigned shorts, entry 0 for EOF,
 *    with _pctype pointing at entry 1 so _pctype[-1] is the EOF row and
 *    _pctype[c] is character c's flags. The flags are the documented MSVC
 *    bits, built once from the host's ctype functions.
 *
 * msvcrt exports are cdecl: every shim ends in RETC(eax), which pops only the
 * synthetic return address and leaves the arguments for the lifted caller.
 * A shim returning a double pushes it on the shared lifted x87 stack (fp_push),
 * as a cdecl function returning a double would.
 *
 * The five *data* exports (_iob, _pctype, _ctype, _fmode, __mb_cur_max) cannot
 * be answered by a function shim: the loader writes a shim's VA into the IAT
 * and the guest reads that slot as the object's address, so a function VA there
 * would be a silent lie. hle_msvcrt_data() gives the host's resolver the guest
 * address of each object instead; anything else returns 0 and stays unresolved.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <locale.h>
#include <fenv.h>
#include <math.h>
#include <time.h>
#include <unistd.h>
#include <dirent.h>
#include <pthread.h>
#include <sys/stat.h>
#ifdef __APPLE__
#include <crt_externs.h>          /* _NSGetArgc/_NSGetArgv: the host's own argv */
#else
extern char **environ;
#endif
#include "win32hle.h"

#ifdef __APPLE__                   /* struct stat's times by their BSD names */
#define st_mtim st_mtimespec
#define st_atim st_atimespec
#endif

/* ---- MSVC constants this module has to lay out in guest memory ---- */
#define IOBUF   32                 /* sizeof(struct _iobuf) on MSVC x86 */
#define NIOB    3                  /* _iob: stdin, stdout, stderr */
#define IOB_PTR   0                /* char *_ptr    */
#define IOB_CNT   4                /* int   _cnt    */
#define IOB_BASE  8                /* char *_base   */
#define IOB_FLAG 12                /* int   _flag   */
#define IOB_FILE 16                /* int   _file   */
#define IOB_CHARBUF 20             /* int   _charbuf */
#define IOB_BUFSIZ  24             /* int   _bufsiz  */
#define IOB_TMPFNAME 28            /* char *_tmpfname */

#define IOYOURBUF 0x0100
#define IOMYBUF   0x0008
#define IONBF     0x0004
#define IOLBF     0x0040
#define IOFBF     0x0000
#define IORW      0x0080
#define IOREAD    0x0001
#define IOWRITE   0x0002
#define IOEOF     0x0010
#define IOERR     0x0020

#define CTYPE_ENTRIES 257          /* entry 0 is EOF's row; _pctype is entry 1 */
#define CT_UPPER   0x0001
#define CT_LOWER   0x0002
#define CT_DIGIT   0x0004
#define CT_SPACE   0x0008
#define CT_PUNCT   0x0010
#define CT_CONTROL 0x0020
#define CT_BLANK   0x0040
#define CT_HEX     0x0080
#define CT_ALPHA   0x0100
#define CT_LEADBYTE 0x8000

#define O_TEXT   0x4000
#define O_BINARY 0x8000

#define MS_MAXSTREAM 128
#define MS_MAXATEXIT 64
#define MS_NSIG 32
#define MS_BUFSIZ 512             /* MSVC's BUFSIZ */

/* _findfirst's handle type. A private value: HLE_H_FIND's closer belongs to
 * kernel32_ext.c's FindFirstFileA (it frees its own struct), and HLE_H_OTHER is
 * the shared bucket another module may claim, so reusing either would let
 * CloseHandle run the wrong closer over this object. */
#define HLE_H_MSFIND 0x4D46

/* ================================================== the guest's CRT objects
 * Everything the guest reaches through an IAT slot or a returned pointer lives
 * in guest memory: a host pointer in guest memory is meaningless to lifted
 * code. Built once, on first use (or on the first data-symbol lookup, which is
 * the loader's bind). */

/* One slot per open stream, declared here because _iob's three _iobufs point
 * into it (the helpers are in the streams section below). The slot number is
 * the CRT descriptor the guest sees in _iobuf.file and passes to
 * _write/_fileno/_setmode/_fdopen; the host FILE* and descriptor stay here. */
typedef struct {
    FILE    *fp;        /* the host stream (NULL: the slot is free) */
    int      fd;        /* the host descriptor behind it */
    int      own;       /* we opened it: fclose it and recycle its _iobuf */
    int      mode;      /* _setmode's mode: recorded, see c_setmode */
    uint32_t iobuf;     /* the guest _iobuf, recycled after fclose */
    pthread_mutex_t lock;   /* _lock_file/_unlock_file take this for real */
} ms_stream;
static ms_stream g_stream[MS_MAXSTREAM];

static uint32_t g_iob_va;         /* _iob: three _iobufs */
static uint32_t g_ctype_va;       /* _ctype: the 257-entry flag table */
static uint32_t g_pctype_va;      /* _pctype: the cell holding the table + 1 */
static uint32_t g_fmode_va;       /* int _fmode */
static uint32_t g_mbmax_va;       /* int __mb_cur_max */
static uint32_t g_errno_va;       /* int errno */
static uint32_t g_environ_va;     /* char **_environ */
static uint32_t g_argc;
static uint32_t g_argv_va;
static uint32_t g_envp_va;
static uint32_t g_argc_va;        /* the int cell __p___argc hands out */
static uint32_t g_argv_cell_va;   /* the char** cell __p___argv hands out */
static uint32_t g_usermatherr;    /* the __setusermatherr callback, recorded */
/* the UCRT (api-ms-win-crt-*): the CRT's own onexit table, the argv mode the
 * startup asked for, and the FP control word _controlfp_s reports */
static uint32_t g_onexit_table, g_onexit_first, g_onexit_last, g_onexit_end;
static uint32_t g_argv_mode;
static uint32_t g_fp_control = 0x0009001Fu;   /* _PC_53 | _RC_NEAR | _MCW_EM, the MSVC default */
#define UCRT_MAX_TLS_CB 16
static uint32_t g_tls_cb[UCRT_MAX_TLS_CB];    /* _register_thread_local_exe_atexit_callback */
static int g_tls_cb_n;
static uint32_t g_cmdline_va;                 /* _get_narrow_winmain_command_line */
static uint32_t g_commode_va;                 /* int __p__commode */
static uint32_t g_new_mode, g_thread_locale;  /* _set_new_mode, _configthreadlocale */
static char **g_host_argv;                    /* this process's own arguments */
static int g_host_argc;
static char *const *g_argv_override;          /* the guest's own argv, when the host supplied one */
static int g_argv_override_n;
static int  ms_atexit_add(uint32_t fn);       /* defined with the exit path below */
static int      g_app_type;       /* __set_app_type */

/* The environment __getmainargs reports, matching what kernel32_crt.c's
 * GetEnvironmentStrings hands the guest: a small Windows-like block, not the
 * host's own (the guest is a Windows program and the host's variables are not
 * its business). */
static char *const g_env_words[] = {
    (char *)"PATH=C:\\WINDOWS\\SYSTEM32;C:\\WINDOWS",
    (char *)"WINDIR=C:\\WINDOWS",
    (char *)"TEMP=C:\\WINDOWS\\TEMP",
};

/* The host's own command line, as __getmainargs reports it. macOS keeps it in
 * the dynamic linker's block, Linux in /proc; with neither, the program's own
 * path is all there is. */
static char **ms_host_argv(int *argc) {
    static char **v;
    static int n;
    if (!v) {
#ifdef __APPLE__
        n = *_NSGetArgc(), v = *_NSGetArgv();
#elif defined(__linux__)
        static char *words[256];
        static char buf[8192];
        FILE *f = fopen("/proc/self/cmdline", "rb");
        size_t got = f ? fread(buf, 1, sizeof buf - 1, f) : 0;
        if (f) fclose(f);
        buf[got] = 0;
        for (size_t i = 0; i < got && n < 255; ) {
            words[n++] = buf + i;
            i += strlen(buf + i) + 1;
        }
        if (n) v = words;
#endif
        if (!v) {
            static char *one[2];
            static char path[1024];
            one[0] = hle_exe_path(path, sizeof path) ? path : (char *)"game.exe";
            one[1] = NULL;
            v = one, n = 1;
        }
    }
    *argc = n;
    return v;
}

/* A NULL-terminated guest char* array over host strings. */
static uint32_t ms_guest_vec(char *const *v, int n) {
    uint32_t arr = hle_va(hle_alloc((uint32_t)(n + 1) * 4));
    if (!arr) return 0;
    for (int i = 0; i < n; i++) MEM32(arr + 4u * (uint32_t)i) = hle_guest_strdup(v[i] ? v[i] : "");
    MEM32(arr + 4u * (uint32_t)n) = 0;
    return arr;
}

static void ms_build_ctype(void) {
    uint16_t *t = (uint16_t *)hle_ptr(g_ctype_va);
    for (int c = 0; c < 256; c++) {
        uint16_t f = 0;
        if (isupper(c)) f |= CT_UPPER | CT_ALPHA;
        if (islower(c)) f |= CT_LOWER | CT_ALPHA;
        if (isdigit(c)) f |= CT_DIGIT | CT_HEX;
        if (isspace(c)) f |= CT_SPACE;
        if (ispunct(c)) f |= CT_PUNCT;
        if (iscntrl(c)) f |= CT_CONTROL;
        if (isblank(c)) f |= CT_BLANK;
        if (isxdigit(c) && !isdigit(c)) f |= CT_HEX;
        t[1 + c] = f;
    }
    t[0] = 0;                                          /* the EOF row */
}

static void g_stream_init(void);   /* the std stream slots; defined with the table below */

static void ms_init_once(void) {
    g_stream_init();
    /* _iob: three _iobufs the guest can take the address of. */
    void *iob = hle_alloc(NIOB * IOBUF);
    if (!iob) hle_fatal("no guest memory for _iob");
    g_iob_va = hle_va(iob);
    for (int i = 0; i < NIOB; i++) {
        uint32_t va = g_iob_va + (uint32_t)i * IOBUF;
        g_stream[i].iobuf = va;
        MEM32(va + IOB_FLAG) = i == 0 ? (IOREAD | IOMYBUF) : (IOWRITE | IOMYBUF);
        MEM32(va + IOB_FILE) = (uint32_t)i;
        MEM32(va + IOB_BUFSIZ) = MS_BUFSIZ;
    }
    /* the ctype table and the _pctype cell that points one entry in */
    void *ct = hle_alloc(CTYPE_ENTRIES * 2);
    if (!ct) hle_fatal("no guest memory for the ctype table");
    g_ctype_va = hle_va(ct);
    ms_build_ctype();
    g_pctype_va = hle_va(hle_alloc(4));
    MEM32(g_pctype_va) = g_ctype_va + 2;
    g_fmode_va = hle_va(hle_alloc(4));
    MEM32(g_fmode_va) = O_TEXT;
    g_commode_va = hle_va(hle_alloc(4));
    MEM32(g_commode_va) = 0;                           /* __p__commode */
    g_mbmax_va = hle_va(hle_alloc(4));
    MEM32(g_mbmax_va) = 1;                             /* a single-byte locale */
    g_errno_va = hle_va(hle_alloc(4));
    g_environ_va = hle_va(hle_alloc(4));
    g_argc_va = hle_va(hle_alloc(4));
    g_argv_cell_va = hle_va(hle_alloc(4));

    int argc = 0;
    char **argv = ms_host_argv(&argc);
    if (g_argv_override) { argc = g_argv_override_n; argv = (char **)g_argv_override; }
    g_argc = (uint32_t)argc;
    g_host_argv = argv;
    g_host_argc = argc;
    g_argv_va = ms_guest_vec(argv, argc);
    g_envp_va = ms_guest_vec(g_env_words, (int)(sizeof g_env_words / sizeof g_env_words[0]));
    MEM32(g_environ_va) = g_envp_va;
    MEM32(g_argc_va) = g_argc;
    MEM32(g_argv_cell_va) = g_argv_va;
}

static pthread_once_t g_once = PTHREAD_ONCE_INIT;
static void ms_init(void) { pthread_once(&g_once, ms_init_once); }

/* The host launcher knows what a Windows process of this title would have been
 * started with: the guest exe first, then the user's arguments. Without this
 * the CRT would report the recompiler's own path as argv[0]. Call before the
 * guest entry runs (the vectors are built on first CRT use). */
void hle_set_argv(int argc, char *const *argv) {
    g_argv_override = argv;
    g_argv_override_n = argc;
}

/* ================================================================= errno
 * MSVC's errno values are the POSIX numbers for every code this module can
 * raise (ENOENT 2, EIO 5, EBADF 9, EACCES 13, EEXIST 17, EINVAL 22, ENOSPC 28,
 * ERANGE 34...), so the host's errno is copied through. */
static void ms_seterrno(int e) {
    ms_init();
    if (g_errno_va) MEM32(g_errno_va) = (uint32_t)e;
}
static void c_errno(void)   { ms_init(); RETC(g_errno_va); }
static void c_p_errno(void) { ms_init(); RETC(g_errno_va); }

/* ============================================================== streams
 * One slot per open stream. The slot number is the CRT descriptor the guest
 * sees in _iobuf.file and passes to _write/_fileno/_setmode/_fdopen; the host
 * FILE* and descriptor stay in this table. The table is declared up with the
 * CRT objects because _iob's three _iobufs point into it. */
static uint32_t  g_iobuf_free[MS_MAXSTREAM];
static int       g_iobuf_free_n;

static void g_stream_init(void) {
    g_stream[0].fp = stdin,  g_stream[0].fd = 0;
    g_stream[1].fp = stdout, g_stream[1].fd = 1;
    g_stream[2].fp = stderr, g_stream[2].fd = 2;
    for (int i = 0; i < 3; i++) g_stream[i].mode = O_TEXT;
    for (int i = 0; i < MS_MAXSTREAM; i++) pthread_mutex_init(&g_stream[i].lock, NULL);
    for (int i = 3; i < MS_MAXSTREAM; i++) g_stream[i].fd = -1;
}

/* A guest FILE* is one of the _iob entries or an _iobuf this layer allocated,
 * and every one of those is registered on its stream, so the lookup never has
 * to dereference the pointer the guest passed.  The old fallback read
 * va->_file, which turned a bad FILE* from a game bug into a host crash; a
 * pointer that is not one of ours is now simply not a stream (EBADF). */
static ms_stream *ms_slot_of_va(uint32_t va) {
    if (!va) return NULL;
    if (g_iob_va && va >= g_iob_va && va < g_iob_va + NIOB * IOBUF) {
        uint32_t off = va - g_iob_va;
        if (off % IOBUF) return NULL;
        return g_stream[off / IOBUF].fp ? &g_stream[off / IOBUF] : NULL;
    }
    for (int i = NIOB; i < MS_MAXSTREAM; i++)
        if (g_stream[i].iobuf == va) return g_stream[i].fp ? &g_stream[i] : NULL;
    return NULL;
}
static int ms_index(const ms_stream *s) { return (int)(s - g_stream); }

static uint32_t ms_iobuf_flag(const char *mode) {
    uint32_t flag = IOMYBUF;
    if (mode) {
        if (mode[0] == 'r') flag |= IOREAD;
        if (mode[0] == 'w' || mode[0] == 'a') flag |= IOWRITE;
        if (strchr(mode, '+')) flag |= IORW | IOREAD | IOWRITE;
    }
    return flag;
}
static uint32_t ms_iobuf_new(int slot, const char *mode) {
    uint32_t va;
    if (g_iobuf_free_n > 0) va = g_iobuf_free[--g_iobuf_free_n];
    else {
        void *p = hle_heap_alloc(IOBUF, 1);
        if (!p) return 0;
        va = hle_va(p);
    }
    memset(hle_ptr(va), 0, IOBUF);
    MEM32(va + IOB_FLAG) = ms_iobuf_flag(mode);
    MEM32(va + IOB_FILE) = (uint32_t)slot;
    MEM32(va + IOB_BUFSIZ) = MS_BUFSIZ;
    return va;
}
/* freopen keeps the stream's FILE* (and so its _iobuf): only the mode changes. */
static void ms_iobuf_reset(ms_stream *s, const char *mode) {
    uint32_t va = s->iobuf;
    if (!va) return;
    MEM32(va + IOB_FLAG) = ms_iobuf_flag(mode);
    MEM32(va + IOB_FILE) = (uint32_t)ms_index(s);
}

static int ms_slot_alloc(FILE *fp, int fd, int own) {
    for (int i = 3; i < MS_MAXSTREAM; i++)
        if (!g_stream[i].fp && g_stream[i].fd < 0) {
            g_stream[i].fp = fp, g_stream[i].fd = fd, g_stream[i].own = own;
            g_stream[i].mode = O_TEXT, g_stream[i].iobuf = 0;
            return i;
        }
    return -1;
}
static void ms_slot_free(ms_stream *s) {
    uint32_t va = s->iobuf;
    s->fp = NULL, s->fd = -1, s->own = 0, s->iobuf = 0;
    if (va && (va < g_iob_va || va >= g_iob_va + NIOB * IOBUF) && g_iobuf_free_n < MS_MAXSTREAM)
        g_iobuf_free[g_iobuf_free_n++] = va;
}
/* Keep _flag's EOF/ERR bits in step with the host stream, so a guest that
 * tests feof/ferror on the struct sees the truth. */
static void ms_note_state(ms_stream *s) {
    uint32_t va = s->iobuf;
    if (!va) return;
    uint32_t f = MEM32(va + IOB_FLAG);
    if (feof(s->fp)) f |= IOEOF; else f &= ~(uint32_t)IOEOF;
    if (ferror(s->fp)) f |= IOERR; else f &= ~(uint32_t)IOERR;
    MEM32(va + IOB_FLAG) = f;
}
static FILE *ms_stdout(void) { return g_stream[1].fp; }

/* ---- guest path and mode ---- */
static int ms_host_path(const char *guest, const char *mode, char *out, size_t n) {
    int plus = strchr(mode, '+') != NULL;
    if (mode[0] == 'r' && !plus) return hle_host_path(guest, out, n);       /* a plain read: the overlay's copy */
    return hle_host_path_for_write(guest, out, n, plus);                     /* a write (or r+): the mod's copy */
}
/* MSVC's mode string as the host's: 't' is dropped (POSIX does no text
 * translation - see the note in c_setmode), and '+' precedes 'b'. */
static void ms_host_mode(const char *mode, char *out, size_t n) {
    size_t k = 0;
    if (n < 4) { if (n) out[0] = 0; return; }
    out[k++] = mode[0] == 'w' || mode[0] == 'a' ? mode[0] : 'r';
    if (strchr(mode, '+')) out[k++] = '+';
    if (strchr(mode, 'b')) out[k++] = 'b';
    out[k] = 0;
}

/* ================================================================ stdio */
static void c_fopen(void) {                          /* (path, mode) */
    ms_init();
    const char *path = ASTR(0), *mode = ASTR(1);
    if (!path || !mode) { ms_seterrno(EINVAL); RETC(0); }
    char host[1024], hm[8];
    ms_host_path(path, mode, host, sizeof host);
    ms_host_mode(mode, hm, sizeof hm);
    FILE *fp = fopen(host, hm);
    if (!fp) { ms_seterrno(errno); RETC(0); }
    int slot = ms_slot_alloc(fp, fileno(fp), 1);
    if (slot < 0) { fclose(fp); ms_seterrno(EMFILE); RETC(0); }
    uint32_t va = ms_iobuf_new(slot, mode);
    if (!va) { fclose(fp); ms_slot_free(&g_stream[slot]); ms_seterrno(ENOMEM); RETC(0); }
    g_stream[slot].iobuf = va;
    RETC(va);
}

static void c_fclose(void) {                         /* (stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int r;
    if (s->own) {
        r = fclose(s->fp);
    } else {
        /* A std stream: the guest's fclose detaches it, but the host's own
         * descriptor stays open, so the runtime's logging still works. */
        r = fflush(s->fp);
    }
    if (r != 0) ms_seterrno(errno);
    ms_slot_free(s);
    RETC(r == 0 ? 0u : 0xFFFFFFFFu);
}

static void c_fflush(void) {                         /* (stream, may be NULL) */
    ms_init();
    uint32_t va = A32(0);
    if (!va) RETC(fflush(NULL) == 0 ? 0u : 0xFFFFFFFFu);
    ms_stream *s = ms_slot_of_va(va);
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int r = fflush(s->fp);
    if (r != 0) ms_seterrno(errno);
    RETC(r == 0 ? 0u : 0xFFFFFFFFu);
}

static void c_freopen(void) {                        /* (path, mode, stream) */
    ms_init();
    uint32_t va = A32(2);
    ms_stream *s = ms_slot_of_va(va);
    if (!s) { ms_seterrno(EBADF); RETC(0); }
    const char *path = ASTR(0), *mode = ASTR(1);
    if (!path) RETC(va);                             /* freopen(NULL, mode, f): the stream, its mode reset */
    if (!mode) { ms_seterrno(EINVAL); RETC(0); }
    char host[1024], hm[8];
    ms_host_path(path, mode, host, sizeof host);
    ms_host_mode(mode, hm, sizeof hm);
    FILE *fp = freopen(host, hm, s->fp);
    if (!fp) { ms_seterrno(errno); RETC(0); }
    s->fd = fileno(fp);
    ms_iobuf_reset(s, mode);
    RETC(va);
}

static void c_fwrite(void) {                         /* (buf, size, count, stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(3));
    if (!s) { ms_seterrno(EBADF); RETC(0); }
    uint32_t size = A32(1), count = A32(2);
    size_t n = fwrite(APTR(0), size, count, s->fp);
    if (n < count) ms_seterrno(errno);
    ms_note_state(s);
    RETC((uint32_t)n);
}

static void c_fread(void) {                          /* (buf, size, count, stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(3));
    if (!s) { ms_seterrno(EBADF); RETC(0); }
    uint32_t size = A32(1), count = A32(2);
    size_t n = fread(APTR(0), size, count, s->fp);
    if (n < count) ms_seterrno(errno);
    ms_note_state(s);
    RETC((uint32_t)n);
}

static void c_fgetc(void) {                          /* (stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int c = fgetc(s->fp);
    ms_note_state(s);
    RETC((uint32_t)(int32_t)c);
}
static void c_getc(void) { c_fgetc(); }              /* same contract */

static void c_fputc(void) {                          /* (c, stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(1));
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int c = fputc((int)A32(0), s->fp);
    if (c == EOF) ms_seterrno(errno);
    RETC((uint32_t)(int32_t)c);
}

static void c_ungetc(void) {                         /* (c, stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(1));
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int c = ungetc((int)A32(0), s->fp);
    RETC((uint32_t)(int32_t)c);
}

static void c_fgets(void) {                          /* (buf, n, stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(2));
    if (!s) { ms_seterrno(EBADF); RETC(0); }
    char *r = fgets(ASTR(0), (int)A32(1), s->fp);
    ms_note_state(s);
    RETC(r ? A32(0) : 0u);
}

static void c_fputs(void) {                          /* (s, stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(1));
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int r = fputs(ASTR(0), s->fp);
    RETC(r >= 0 ? 0u : 0xFFFFFFFFu);
}

static void c_fseek(void) {                          /* (stream, offset, whence) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int r = fseek(s->fp, (long)(int32_t)A32(1), (int)A32(2));
    if (r != 0) ms_seterrno(errno);
    ms_note_state(s);
    RETC(r == 0 ? 0u : 0xFFFFFFFFu);
}

static void c_ftell(void) {                          /* (stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    long r = ftell(s->fp);
    if (r < 0) ms_seterrno(errno);
    RETC((uint32_t)(int32_t)r);
}

static void c_setbuf(void) {                         /* (stream, buf or NULL) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    if (!s) RETCV();
    uint32_t buf = A32(1);
    /* The guest's buffer is guest memory, which is host memory: handing it to
     * the host stream is exactly what MSVC's setbuf does with it. */
    if (!buf) setvbuf(s->fp, NULL, _IONBF, 0);
    else setvbuf(s->fp, (char *)hle_ptr(buf), _IOFBF, MS_BUFSIZ);
    RETCV();
}

static void c_setvbuf(void) {                        /* (stream, buf, mode, size) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    uint32_t buf = A32(1), mode = A32(2);
    int host_mode = mode == IONBF ? _IONBF : mode == IOLBF ? _IOLBF : _IOFBF;
    int r = setvbuf(s->fp, buf ? (char *)hle_ptr(buf) : NULL, host_mode, (size_t)A32(3));
    if (r != 0) ms_seterrno(errno);
    RETC(r == 0 ? 0u : 0xFFFFFFFFu);
}

/* ---- the formatted-output family ----
 * The guest's format and its arguments are guest memory, so the text is built
 * with hle_guest_format (%s is a VA) and then handed to the host stream. */
#define MS_FMTBUF 8192
static __thread char t_fmt[MS_FMTBUF];

static void c_printf(void) {                         /* (fmt, ...) */
    ms_init();
    const char *fmt = ASTR(0);
    FILE *out = ms_stdout();
    if (!fmt || !out) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    int n = hle_guest_format(t_fmt, sizeof t_fmt, fmt, g_esp + 4u + 4u);
    size_t w = fwrite(t_fmt, 1, (size_t)n, out);
    RETC(w == (size_t)n ? (uint32_t)n : 0xFFFFFFFFu);
}

static void c_fprintf(void) {                        /* (stream, fmt, ...) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    const char *fmt = ASTR(1);
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    if (!fmt) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    int n = hle_guest_format(t_fmt, sizeof t_fmt, fmt, g_esp + 4u + 8u);
    size_t w = fwrite(t_fmt, 1, (size_t)n, s->fp);
    RETC(w == (size_t)n ? (uint32_t)n : 0xFFFFFFFFu);
}

static void c_sprintf(void) {                        /* (buf, fmt, ...) */
    ms_init();
    char *buf = ASTR(0);
    const char *fmt = ASTR(1);
    if (!buf || !fmt) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    int n = hle_guest_format(t_fmt, sizeof t_fmt, fmt, g_esp + 4u + 8u);
    memcpy(buf, t_fmt, (size_t)n + 1);
    RETC((uint32_t)n);
}

static void c_puts(void) {                           /* (s) */
    ms_init();
    const char *s = ASTR(0);
    FILE *out = ms_stdout();
    if (!s || !out) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    int r = fputs(s, out);
    if (r >= 0) r = fputc('\n', out);
    RETC(r >= 0 ? 0u : 0xFFFFFFFFu);
}

/* ---- sscanf ----
 * The guest's input, format and output pointers are all guest memory. Each
 * conversion is run by the host's sscanf into a local, with "%n" appended so
 * the characters consumed are known, and the local is then stored through the
 * guest's pointer at the width MSVC's conversion has (long is 32-bit there,
 * hence the ll host spec and the explicit store size). */
static void ms_store(uint32_t dst, uint64_t bits, int size) {
    switch (size) {
    case 1: MEM8(dst)  = (uint8_t)bits; break;
    case 2: MEM16(dst) = (uint16_t)bits; break;
    case 8: MEM32(dst) = (uint32_t)bits, MEM32(dst + 4) = (uint32_t)(bits >> 32); break;
    default: MEM32(dst) = (uint32_t)bits; break;
    }
}

static void c_sscanf(void) {                         /* (src, fmt, ...) */
    ms_init();
    const char *s = ASTR(0), *f = ASTR(1);
    if (!s || !f) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    uint32_t args = g_esp + 4u + 8u;
    const char *p = s;
    int assigned = 0;
    while (*f) {
        if (isspace((unsigned char)*f)) {            /* whitespace in the format: any run */
            while (isspace((unsigned char)*f)) f++;
            while (isspace((unsigned char)*p)) p++;
            continue;
        }
        if (*f != '%') {
            if (*p != *f) break;
            p++, f++;
            continue;
        }
        if (f[1] == '%') {
            if (*p != '%') break;
            p++, f += 2;
            continue;
        }
        char w[16] = "";                             /* the width, as written */
        int wi = 0;
        f++;
        int suppress = 0;
        if (*f == '*') suppress = 1, f++;
        while (*f >= '0' && *f <= '9' && wi < 12) w[wi++] = *f++;
        w[wi] = 0;
        int size = 4;                                /* the guest store's width */
        if (f[0] == 'h') { f++; if (*f == 'h') f++, size = 1; else size = 2; }
        else if (f[0] == 'l') { f++; if (*f == 'l') f++; }
        else if (f[0] == 'L') f++;
        else if (f[0] == 'I' && f[1] == '6' && f[2] == '4') f += 3, size = 8;
        else if (f[0] == 'I' && f[1] == '3' && f[2] == '2') f += 3;
        char conv = *f ? *f++ : 0;
        char spec[96];
        int n = -1;                                  /* %n: set only once the conversion matched */
        switch (conv) {
        case 'd': case 'i': case 'u': case 'o': case 'x': case 'X': case 'p': {
            int uns = conv != 'd' && conv != 'i';
            char c2 = conv == 'p' ? 'x' : conv;
            snprintf(spec, sizeof spec, "%%%s%sll%c%%n", suppress ? "*" : "", w, c2);
            long long sv = 0;
            unsigned long long uv = 0;
            if (uns) { if (suppress) sscanf(p, spec, &n); else sscanf(p, spec, &uv, &n); }
            else     { if (suppress) sscanf(p, spec, &n); else sscanf(p, spec, &sv, &n); }
            if (n >= 0 && !suppress) {
                uint32_t dst = MEM32(args);
                args += 4;
                ms_store(dst, uns ? (uint64_t)uv : (uint64_t)sv, size);
                assigned++;
            }
            break;
        }
        case 'e': case 'E': case 'f': case 'F': case 'g': case 'G': {
            double d = 0;
            snprintf(spec, sizeof spec, "%%%s%sl%c%%n", suppress ? "*" : "", w, conv);
            if (suppress) sscanf(p, spec, &n); else sscanf(p, spec, &d, &n);
            if (n >= 0 && !suppress) {
                uint32_t dst = MEM32(args);
                args += 4;
                uint64_t bits;
                memcpy(&bits, &d, 8);
                ms_store(dst, bits, 8);
                assigned++;
            }
            break;
        }
        case 's': {
            char tmp[1024];
            snprintf(spec, sizeof spec, "%%%s%ss%%n", suppress ? "*" : "", w);
            if (suppress) sscanf(p, spec, &n);
            else if (sscanf(p, spec, tmp, &n) == 1) {
                uint32_t dst = MEM32(args);
                args += 4;
                memcpy(hle_ptr(dst), tmp, strlen(tmp) + 1);
                assigned++;
            }
            break;
        }
        case 'c': {
            char tmp[256];
            int cw = w[0] ? atoi(w) : 1;
            if (cw > (int)sizeof tmp) cw = (int)sizeof tmp;
            snprintf(spec, sizeof spec, "%%%s%sc%%n", suppress ? "*" : "", w);
            if (suppress) sscanf(p, spec, &n);
            else {
                sscanf(p, spec, tmp, &n);
                if (n >= 0) {
                    uint32_t dst = MEM32(args);
                    args += 4;
                    memcpy(hle_ptr(dst), tmp, (size_t)cw);
                    assigned++;
                }
            }
            break;
        }
        case '[': {
            char scanset[64];
            size_t k = 0;
            if (*f == '^') scanset[k++] = *f++;
            if (*f == ']') scanset[k++] = *f++;
            while (*f && *f != ']' && k < sizeof scanset - 1) scanset[k++] = *f++;
            scanset[k] = 0;
            if (*f == ']') f++;
            char tmp[1024];
            snprintf(spec, sizeof spec, "%%%s%s[%s]%%n", suppress ? "*" : "", w, scanset);
            if (suppress) sscanf(p, spec, &n);
            else if (sscanf(p, spec, tmp, &n) == 1) {
                uint32_t dst = MEM32(args);
                args += 4;
                memcpy(hle_ptr(dst), tmp, strlen(tmp) + 1);
                assigned++;
            }
            break;
        }
        case 'n':
            MEM32(MEM32(args)) = (uint32_t)(p - s);
            args += 4;
            n = 0;                                   /* %n reads nothing: keep going */
            break;
        default:
            /* A conversion this module does not read (a wide-character one, or
             * a typo): stop where the host would have, and say so. */
            fprintf(stderr, "[win32hle] sscanf: unsupported conversion '%%%c'\n", conv ? conv : '?');
            RETC((uint32_t)(int32_t)(assigned ? assigned : 0xFFFFFFFFu));
        }
        if (n < 0) break;                            /* the input did not match here */
        p += n;
    }
    if (assigned == 0 && *p == 0) RETC(0xFFFFFFFFu);  /* input ran out before the first conversion */
    RETC((uint32_t)assigned);
}

/* ---- the low-level descriptor calls ---- */
static void c_fileno(void) {                         /* (stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    RETC((uint32_t)ms_index(s));                     /* the CRT descriptor is the slot number */
}

static void c_fdopen(void) {                         /* (fd, mode) */
    ms_init();
    int fd = (int)A32(0);
    const char *mode = ASTR(1);
    if (!mode || fd < 0 || fd >= MS_MAXSTREAM || !g_stream[fd].fp) { ms_seterrno(EBADF); RETC(0); }
    /* The descriptor already names a stream: hand back a _iobuf for that same
     * slot (MSVC's _fdopen wraps a descriptor in a new stream). The two share
     * the underlying host stream, so closing one closes both. */
    uint32_t va = ms_iobuf_new(fd, mode);
    if (!va) { ms_seterrno(ENOMEM); RETC(0); }
    RETC(va);
}

static void c_setmode(void) {                        /* (fd, mode) */
    ms_init();
    int fd = (int)A32(0);
    if (fd < 0 || fd >= MS_MAXSTREAM || !g_stream[fd].fp) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    /* The mode is recorded and returned, and _O_TEXT/_O_BINARY are accepted,
     * but no CRLF translation is applied: the host's streams are byte streams.
     * A game that reads a CRLF text file with fgets sees the \r. That is a
     * documented gap, not a claimed success. */
    int old = g_stream[fd].mode;
    g_stream[fd].mode = (int)A32(1);
    RETC((uint32_t)old);
}

static void c_write(void) {                          /* (fd, buf, count) */
    ms_init();
    int fd = (int)A32(0);
    if (fd < 0 || fd >= MS_MAXSTREAM || !g_stream[fd].fp) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    ms_stream *s = &g_stream[fd];
    if (s->fp) fflush(s->fp);                        /* the raw write must land after anything buffered */
    ssize_t w = write(s->fd, APTR(1), (size_t)A32(2));
    if (w < 0) ms_seterrno(errno);
    RETC((uint32_t)w);
}

static void c_getw(void) {                           /* (stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int v = 0;
    if (fread(&v, 4, 1, s->fp) != 1) { ms_note_state(s); RETC(0xFFFFFFFFu); }
    RETC((uint32_t)v);
}

static void c_putw(void) {                           /* (v, stream) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(1));
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int v = (int)A32(0);
    if (fwrite(&v, 4, 1, s->fp) != 1) { ms_note_state(s); RETC(0xFFFFFFFFu); }
    RETC((uint32_t)v);
}

static void c_remove(void) {                         /* (path) */
    ms_init();
    const char *p = ASTR(0);
    if (!p) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    char host[1024];
    hle_host_path(p, host, sizeof host);
    if (unlink(host) == 0) RETC(0);
    ms_seterrno(errno);
    RETC(0xFFFFFFFFu);
}

static void c_fullpath(void) {                       /* (absPath or NULL, relPath, maxLength) */
    ms_init();
    const char *rel = ASTR(1);
    if (!rel) { ms_seterrno(EINVAL); RETC(0); }
    char host[1024], guest[1024];
    hle_host_path(rel, host, sizeof host);
    hle_guest_path(host, guest, sizeof guest);
    size_t len = strlen(guest) + 1;
    char *abs = ASTR(0);
    if (!abs) {                                      /* MSVC allocates the buffer with malloc */
        char *p = (char *)hle_heap_alloc((uint32_t)len, 0);
        if (!p) { ms_seterrno(ENOMEM); RETC(0); }
        memcpy(p, guest, len);
        RETC(hle_va(p));
    }
    if (len > A32(2)) { ms_seterrno(ERANGE); RETC(0); }
    memcpy(abs, guest, len);
    RETC(hle_va(abs));
}

/* ================================================== _findfirst/_findnext */
typedef struct {
    char     name[260];
    uint32_t attrib, size, t_create, t_access, t_write;
} ms_findent;
typedef struct { ms_findent *e; int n, at; } ms_find;

static int ms_wild(const char *pat, const char *name) {
    if (!strcmp(pat, "*.*")) pat = "*";              /* Windows: *.* is every name */
    const char *p = pat, *n = name, *star = NULL, *ss = NULL;
    while (*n) {
        if (*p == '?' || tolower((unsigned char)*p) == tolower((unsigned char)*n)) p++, n++;
        else if (*p == '*') star = p++, ss = n;
        else if (star) p = star + 1, n = ++ss;
        else return 0;
    }
    while (*p == '*') p++;
    return *p == 0;
}
static int ms_by_name(const void *a, const void *b) {
    return strcasecmp(((const ms_findent *)a)->name, ((const ms_findent *)b)->name);
}
static void ms_fill_find(uint32_t out, const ms_findent *e) {
    if (!out) return;
    MEM32(out + 0) = e->attrib;
    MEM32(out + 4) = e->t_create;
    MEM32(out + 8) = e->t_access;
    MEM32(out + 12) = e->t_write;
    MEM32(out + 16) = e->size;
    memcpy(hle_ptr(out + 20), e->name, strlen(e->name) + 1);
}

static void c_findfirst(void) {                      /* (filespec, &finddata) -> handle */
    ms_init();
    const char *pat = ASTR(0);
    uint32_t out = A32(1);
    if (!pat) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    const char *sl = pat + strlen(pat);
    while (sl > pat && sl[-1] != '\\' && sl[-1] != '/' && sl[-1] != ':') sl--;
    char gdir[1024];
    snprintf(gdir, sizeof gdir, "%.*s", (int)(sl - pat), pat);
    if (!gdir[0]) snprintf(gdir, sizeof gdir, ".");
    char dir[1024];
    hle_host_path(gdir, dir, sizeof dir);
    DIR *d = opendir(dir);
    if (!d) { ms_seterrno(ENOENT); RETC(0xFFFFFFFFu); }
    ms_find *f = (ms_find *)calloc(1, sizeof *f);
    struct dirent *de;
    while (f && (de = readdir(d))) {
        if (!strcmp(de->d_name, ".") || !strcmp(de->d_name, "..")) continue;
        if (!ms_wild(sl, de->d_name)) continue;
        char full[2048];
        snprintf(full, sizeof full, "%s/%s", dir, de->d_name);
        struct stat st;
        if (stat(full, &st) != 0) continue;
        if (f->n % 64 == 0) {
            ms_findent *g = (ms_findent *)realloc(f->e, sizeof *g * (size_t)(f->n + 64));
            if (!g) break;
            f->e = g;
        }
        ms_findent *e = &f->e[f->n++];
        memset(e, 0, sizeof *e);
        snprintf(e->name, sizeof e->name, "%s", de->d_name);
        e->attrib = S_ISDIR(st.st_mode) ? 0x10u : 0x20u;              /* _A_SUBDIR / _A_ARCH */
        if (!(st.st_mode & S_IWUSR)) e->attrib |= 0x01u;              /* _A_RDONLY */
        e->size = (uint32_t)st.st_size;
#ifdef __APPLE__
        e->t_create = (uint32_t)st.st_birthtimespec.tv_sec;
#else
        e->t_create = (uint32_t)st.st_ctime;
#endif
        e->t_access = (uint32_t)st.st_atim.tv_sec;
        e->t_write = (uint32_t)st.st_mtim.tv_sec;
    }
    closedir(d);
    if (!f || !f->n) {
        if (f) free(f->e), free(f);
        ms_seterrno(ENOENT);
        RETC(0xFFFFFFFFu);
    }
    qsort(f->e, (size_t)f->n, sizeof *f->e, ms_by_name);
    uint32_t h = hle_handle_alloc(HLE_H_MSFIND, f);
    if (!h) { free(f->e), free(f); ms_seterrno(EMFILE); RETC(0xFFFFFFFFu); }
    ms_fill_find(out, &f->e[0]);
    f->at = 1;
    RETC(h);
}

static void c_findnext(void) {                       /* (handle, &finddata) */
    ms_init();
    ms_find *f = (ms_find *)hle_handle_obj(A32(0), HLE_H_MSFIND);
    if (!f) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    if (f->at >= f->n) { ms_seterrno(ENOENT); RETC(0xFFFFFFFFu); }
    ms_fill_find(A32(1), &f->e[f->at++]);
    RETC(0);
}

static void c_findclose(void) {                      /* (handle) */
    ms_init();
    uint32_t h = A32(0);
    ms_find *f = (ms_find *)hle_handle_obj(h, HLE_H_MSFIND);
    if (!f) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    free(f->e), free(f);
    hle_handle_close(h);                             /* no closer is registered for this type */
    RETC(0);
}

/* ================================================================= heap
 * The guest's malloc blocks live in the guest heap (guest_mem.c), so they are
 * the same blocks HeapFree/GlobalFree take, and a guest VA never points at a
 * host malloc. */
static void c_malloc(void) {
    ms_init();
    void *p = hle_heap_alloc(A32(0), 0);
    if (!p) { ms_seterrno(ENOMEM); RETC(0); }
    RETC(hle_va(p));
}
static void c_calloc(void) {                         /* (n, size) */
    ms_init();
    uint32_t n = A32(0), sz = A32(1);
    if (sz && n > 0xFFFFFFFFu / sz) { ms_seterrno(ENOMEM); RETC(0); }
    void *p = hle_heap_alloc(n * sz, 1);
    if (!p) { ms_seterrno(ENOMEM); RETC(0); }
    RETC(hle_va(p));
}
static void c_free(void) { ms_init(); hle_free(APTR(0)); RETCV(); }
static void c_realloc(void) {                        /* (p, n) */
    ms_init();
    void *p = APTR(0);
    if (p && !hle_heap_ok(p)) { ms_seterrno(EINVAL); RETC(0); }
    void *q = hle_heap_realloc(p, A32(1), 0);
    if (!q) { ms_seterrno(ENOMEM); RETC(0); }
    RETC(hle_va(q));
}
static void c_msize(void) {                          /* (p) */
    ms_init();
    void *p = APTR(0);
    RETC(p && hle_heap_ok(p) ? hle_heap_size(p) : 0xFFFFFFFFu);
}
static void c_strdup(void) {                         /* (s) */
    ms_init();
    const char *s = ASTR(0);
    if (!s) { ms_seterrno(EINVAL); RETC(0); }
    size_t n = strlen(s) + 1;
    char *p = (char *)hle_heap_alloc((uint32_t)n, 0);
    if (!p) { ms_seterrno(ENOMEM); RETC(0); }
    memcpy(p, s, n);
    RETC(hle_va(p));
}

/* ===================================================== string and memory
 * Straight forwards to the host: the guest's strings are host memory in the
 * guest block, so the host's own routines read and write them in place. */
static void c_memcpy(void)  { memcpy(APTR(0), APTR(1), A32(2)); RETC(A32(0)); }
static void c_memmove(void) { memmove(APTR(0), APTR(1), A32(2)); RETC(A32(0)); }
static void c_memset(void)  { memset(APTR(0), (int)A32(1), A32(2)); RETC(A32(0)); }
static void c_memchr(void)  { void *p = memchr(APTR(0), (int)A32(1), A32(2)); RETC(p ? hle_va(p) : 0u); }
static void c_memcmp(void)  { RETC((uint32_t)(int32_t)memcmp(APTR(0), APTR(1), A32(2))); }
static void c_strlen(void)  { RETC((uint32_t)strlen(ASTR(0))); }
static void c_strcpy(void)  { RETC(hle_va(strcpy(ASTR(0), ASTR(1)))); }
static void c_strncpy(void) { RETC(hle_va(strncpy(ASTR(0), ASTR(1), A32(2)))); }
static void c_strcat(void)  { RETC(hle_va(strcat(ASTR(0), ASTR(1)))); }
static void c_strncat(void) { RETC(hle_va(strncat(ASTR(0), ASTR(1), A32(2)))); }
static void c_strcmp(void)  { RETC((uint32_t)(int32_t)strcmp(ASTR(0), ASTR(1))); }
static void c_strncmp(void) { RETC((uint32_t)(int32_t)strncmp(ASTR(0), ASTR(1), A32(2))); }
static void c_stricmp(void) { RETC((uint32_t)(int32_t)strcasecmp(ASTR(0), ASTR(1))); }
static void c_strnicmp(void){ RETC((uint32_t)(int32_t)strncasecmp(ASTR(0), ASTR(1), A32(2))); }
static void c_strchr(void)  { RETC(hle_va(strchr(ASTR(0), (int)A32(1)))); }
static void c_strrchr(void) { char *p = strrchr(ASTR(0), (int)A32(1)); RETC(p ? hle_va(p) : 0u); }
static void c_strstr(void)  { char *p = strstr(ASTR(0), ASTR(1)); RETC(p ? hle_va(p) : 0u); }
static void c_strcoll(void) { RETC((uint32_t)(int32_t)strcoll(ASTR(0), ASTR(1))); }
static void c_strxfrm(void) { RETC((uint32_t)strxfrm(ASTR(0), ASTR(1), A32(2))); }
static void c_tolower(void) { RETC((uint32_t)(int32_t)tolower((int)A32(0))); }
static void c_toupper(void) { RETC((uint32_t)(int32_t)toupper((int)A32(0))); }

/* ================================================================ ctype */
static void c_isctype(void) {                        /* (c, type) */
    ms_init();
    int c = (int)A32(0), type = (int)A32(1);
    if (c < -1 || c > 255) RETC(0);
    RETC((uint32_t)(((const uint16_t *)hle_ptr(g_ctype_va))[c + 1] & (uint32_t)type));
}
static void c_isspace(void) {                        /* (c) */
    ms_init();
    int c = (int)A32(0);
    if (c < -1 || c > 255) RETC(0);
    RETC((uint32_t)(((const uint16_t *)hle_ptr(g_ctype_va))[c + 1] & CT_SPACE));
}

/* ========================================================== conversions */
static void c_atoi(void) {
    ms_init();
    const char *s = ASTR(0);
    RETC(s ? (uint32_t)(int32_t)atoi(s) : 0u);
}
static void c_strtol(void) {                         /* (s, end, base) */
    ms_init();
    const char *s = ASTR(0);
    if (!s) { ms_seterrno(EINVAL); RETC(0); }
    char *e = NULL;
    errno = 0;
    long long v = strtoll(s, &e, (int)A32(2));
    if (A32(1)) MEM32(A32(1)) = e ? hle_va(e) : 0;
    if (errno == ERANGE || v > INT32_MAX || v < INT32_MIN) {     /* MSVC's long is 32-bit */
        ms_seterrno(ERANGE);
        v = v < 0 ? INT32_MIN : INT32_MAX;
    }
    RETC((uint32_t)(int32_t)v);
}
static void c_strtoul(void) {                        /* (s, end, base) */
    ms_init();
    const char *s = ASTR(0);
    if (!s) { ms_seterrno(EINVAL); RETC(0); }
    char *e = NULL;
    errno = 0;
    unsigned long long v = strtoull(s, &e, (int)A32(2));
    if (A32(1)) MEM32(A32(1)) = e ? hle_va(e) : 0;
    if (errno == ERANGE || v > UINT32_MAX) {
        ms_seterrno(ERANGE);
        v = UINT32_MAX;
    }
    RETC((uint32_t)v);
}
static void c_strtod(void) {                         /* (s, end) */
    ms_init();
    const char *s = ASTR(0);
    if (!s) { ms_seterrno(EINVAL); RETC(0); }
    char *e = NULL;
    double d = strtod(s, &e);
    if (A32(1)) MEM32(A32(1)) = e ? hle_va(e) : 0;
    fp_push_impl(g_st, &g_fp_top, d);
    RETC(0);
}

/* ================================================================= rand
 * MSVC's own generator, not the host's: holdrand = holdrand * 214013 + 2531011,
 * result (holdrand >> 16) & 0x7FFF. A game that replays a demo or seeds a world
 * depends on the exact sequence. */
static uint32_t g_rand = 1;
static void c_srand(void) { ms_init(); g_rand = A32(0); RETCV(); }
static void c_rand(void) {
    ms_init();
    g_rand = g_rand * 214013u + 2531011u;
    RETC((g_rand >> 16) & 0x7FFFu);
}

/* ================================================================= math
 * A cdecl double argument is eight bytes on the lifted stack; the result goes
 * back on the lifted x87 stack, as a real cdecl double return does. */
static double ms_argd(int i) {
    uint64_t bits = (uint64_t)A32(i) | ((uint64_t)A32(i + 1) << 32);
    double d;
    memcpy(&d, &bits, 8);
    return d;
}
static void c_sin(void)     { ms_init(); fp_push_impl(g_st, &g_fp_top, sin(ms_argd(0))); RETC(0); }
static void c_cos(void)     { ms_init(); fp_push_impl(g_st, &g_fp_top, cos(ms_argd(0))); RETC(0); }
static void c_sqrt(void)    { ms_init(); fp_push_impl(g_st, &g_fp_top, sqrt(ms_argd(0))); RETC(0); }
static void c_fabs(void)    { ms_init(); fp_push_impl(g_st, &g_fp_top, fabs(ms_argd(0))); RETC(0); }
static void c_floor(void)   { ms_init(); fp_push_impl(g_st, &g_fp_top, floor(ms_argd(0))); RETC(0); }
static void c_pow(void)     { ms_init(); fp_push_impl(g_st, &g_fp_top, pow(ms_argd(0), ms_argd(2))); RETC(0); }
static void c_atan2(void)   { ms_init(); fp_push_impl(g_st, &g_fp_top, atan2(ms_argd(0), ms_argd(2))); RETC(0); }
static void c_finite(void)  { ms_init(); RETC(isfinite(ms_argd(0)) ? 1u : 0u); }

/* ============================================================ time/locale */
static void c_time(void) {                           /* (time_t *t or NULL) */
    ms_init();
    /* the virtual clock when it is on, the real one otherwise */
    time_t now = hle_clock_virtual() ? (time_t)(hle_clock_ms() / 1000u) : time(NULL);
    if (A32(0)) MEM32(A32(0)) = (uint32_t)now;       /* MSVC's time_t is 32-bit */
    RETC((uint32_t)now);
}
static void c_strftime(void) {                       /* (buf, max, fmt, tm) */
    ms_init();
    char *buf = ASTR(0);
    const char *fmt = ASTR(2);
    const struct tm *tmv = (const struct tm *)APTR(3);
    if (!buf || !fmt || !tmv) { ms_seterrno(EINVAL); RETC(0); }
    /* MSVC's struct tm is the nine ints POSIX's is, in the same order. */
    RETC((uint32_t)strftime(buf, (size_t)A32(1), fmt, tmv));
}
static void c_setlocale(void) {                      /* (category, locale) */
    ms_init();
    /* MSVC's LC_* numbers, mapped to the host's (they differ). */
    static const int cat[6] = { LC_ALL, LC_COLLATE, LC_CTYPE, LC_MONETARY, LC_NUMERIC, LC_TIME };
    int c = (int)A32(0);
    if (c < 0 || c > 5) { ms_seterrno(EINVAL); RETC(0); }
    char *r = setlocale(cat[c], ASTR(1));
    if (!r) RETC(0);
    RETC(hle_guest_strdup(r));                       /* the guest keeps the name: give it its own copy */
}

/* =============================================================== startup */
static void c_getmainargs(void) {                    /* (argc*, argv***, envp***, wildcards, startupinfo*) */
    ms_init();
    if (A32(0)) MEM32(A32(0)) = g_argc;
    if (A32(1)) MEM32(A32(1)) = g_argv_va;
    if (A32(2)) MEM32(A32(2)) = g_envp_va;
    if (A32(4)) MEM32(A32(4)) = 0;                   /* _startupinfo.newmode */
    RETCV();
}
static void c_p_environ(void) { ms_init(); RETC(g_environ_va); }
static void c_set_app_type(void) { ms_init(); g_app_type = (int)A32(0); RETCV(); }
static void c_setusermatherr(void) {
    ms_init();
    /* Recorded, never called: a math error in the host libm does not run the
     * guest's handler. Documented gap, not a claimed install. */
    g_usermatherr = A32(0);
    RETCV();
}
static void c_p_iob(void)     { ms_init(); RETC(g_iob_va); }
static void c_iob_func(void)  { ms_init(); RETC(g_iob_va); }
static void c_p_pctype(void)  { ms_init(); RETC(g_pctype_va); }
static void c_p_fmode(void)   { ms_init(); RETC(g_fmode_va); }
static void c_p_mbmax(void)   { ms_init(); RETC(g_mbmax_va); }
static void c_p_argc(void)    { ms_init(); RETC(g_argc_va); }
static void c_p_argv(void)    { ms_init(); RETC(g_argv_cell_va); }

/* The CRT's static-initialiser runner: each non-NULL entry is a guest function
 * taking no arguments. */
static void c_initterm(void) {                       /* (begin, end) */
    ms_init();
    for (uint32_t p = A32(0); p && p + 4 <= A32(1); p += 4) {
        uint32_t fn = MEM32(p);
        if (fn) {
            uint32_t a[1] = { 0 };
            hle_call_guest(fn, 0, a);
        }
    }
    RETCV();
}
static void c_initterm_e(void) {                     /* (begin, end) -> nonzero if one failed */
    ms_init();
    for (uint32_t p = A32(0); p && p + 4 <= A32(1); p += 4) {
        uint32_t fn = MEM32(p);
        if (fn) {
            uint32_t a[1] = { 0 };
            uint32_t r = hle_call_guest(fn, 0, a);
            if (r) RETC(r);
        }
    }
    RETC(0);
}

/* ============================================ the UCRT (api-ms-win-crt-*)
 * A game built against the Windows 10 SDK imports the same C runtime through
 * the API sets (api-ms-win-crt-runtime-l1-1-0.dll and friends) instead of
 * msvcrt.dll.  The functions behind those names ARE the UCRT, which is the
 * same CRT this file already implements, so the ones that exist above are the
 * same shims - the loader resolves by name, so _initterm, _errno, exit, _cexit
 * and friends already answer an api-ms-win import.  Only the startup calls the
 * UCRT added (the onexit tables, the argv/environment setup, the invalid
 * parameter handlers) are new, and they are here rather than in a second file
 * because they run against the same atexit chain and the same process state. */

/* The UCRT's onexit table: _first, _last, _end, three guest pointers. */
enum { UCRT_ONEXIT_MAX = 256 };
static uint32_t g_onexit_first, g_onexit_last, g_onexit_end;   /* the CRT's own table */

static void u_initialize_onexit_table(void) {        /* (_onexit_table_t *table) -> 0 */
    ms_init();
    uint32_t t = A32(0);
    if (!t) RETC(-1);
    uint32_t first = hle_va(hle_alloc(UCRT_ONEXIT_MAX * 4));
    if (!first) RETC(-1);
    MEM32(t) = first;                                /* _first */
    MEM32(t + 4) = first;                            /* _last  */
    MEM32(t + 8) = first + UCRT_ONEXIT_MAX * 4;      /* _end   */
    g_onexit_table = t;                              /* the CRT's own table, run at exit */
    g_onexit_first = first;
    g_onexit_last = first;
    g_onexit_end = first + UCRT_ONEXIT_MAX * 4;
    RETC(0);
}
static void u_register_onexit_function(void) {       /* (table, fn) -> 0, or -1 when full */
    ms_init();
    uint32_t t = A32(0), fn = A32(1);
    if (!t || !fn) RETC(-1);
    uint32_t last = MEM32(t + 4), end = MEM32(t + 8);
    if (!last || last + 4 > end) RETC(-1);           /* a table that was never initialised, or full */
    MEM32(last) = fn;
    MEM32(t + 4) = last + 4;
    RETC(0);
}
/* The table's callbacks, last registered first, and the thread-local callbacks
 * (which Windows would run at thread exit - see the note on them below). */
static void ms_run_onexit_table(void) {
    if (g_onexit_table) {
        uint32_t last = MEM32(g_onexit_table + 4);
        while (last > g_onexit_first) {
            last -= 4;
            uint32_t fn = MEM32(last);
            if (fn) { uint32_t a[1] = { 0 }; hle_call_guest(fn, 0, a); }
        }
        MEM32(g_onexit_table + 4) = g_onexit_first;
    }
    while (g_tls_cb_n > 0) {
        uint32_t fn = g_tls_cb[--g_tls_cb_n];
        if (fn) { uint32_t a[1] = { 0 }; hle_call_guest(fn, 0, a); }
    }
}

/* The rest of the UCRT surface a Windows 10 SDK build imports, module by
 * module.  The _libm_sse2_*_precise names are the CRT's double-precision sin,
 * cos, sqrt, atan, pow and log10 - the host libm IS the double-precision
 * implementation, so these forward to it and return through the same lifted x87
 * stack as sin/cos above. */
static void u_libm_sin(void)    { ms_init(); fp_push_impl(g_st, &g_fp_top, sin(ms_argd(0))); RETC(0); }
static void u_libm_cos(void)    { ms_init(); fp_push_impl(g_st, &g_fp_top, cos(ms_argd(0))); RETC(0); }
static void u_libm_sqrt(void)   { ms_init(); fp_push_impl(g_st, &g_fp_top, sqrt(ms_argd(0))); RETC(0); }
static void u_libm_atan(void)   { ms_init(); fp_push_impl(g_st, &g_fp_top, atan(ms_argd(0))); RETC(0); }
static void u_libm_log10(void)  { ms_init(); fp_push_impl(g_st, &g_fp_top, log10(ms_argd(0))); RETC(0); }
static void u_libm_pow(void)    { ms_init(); fp_push_impl(g_st, &g_fp_top, pow(ms_argd(0), ms_argd(2))); RETC(0); }
/* _except1(code, ...): the x87 math exception entry.  With no handler installed
 * the UCRT's default reports and ends the process, and this layer has no
 * _set_math_errhandling to install one, so that is what it does rather than
 * returning a made-up value for a computation that raised. */
static void u_except1(void) {
    fprintf(stderr, "[msvcrt] _except1: floating-point exception 0x%X with no handler\n", A32(0));
    hle_exit(3);
}
/* _callnewh(size): run the new handler; none is installed here (there is no
 * _set_new_handler in this layer), so the allocation failed and the answer is 0. */
static void u_callnewh(void) { ms_init(); RETC(0); }
/* _set_new_mode(mode): whether malloc calls the new handler on failure.
 * Recorded and reported back; nothing here allocates through that path. */
static void u_set_new_mode(void) { ms_init(); uint32_t was = g_new_mode; g_new_mode = A32(0); RETC(was); }
/* _configthreadlocale(mode): the UCRT's per-thread locale switch.  Recorded
 * (this layer's locale is process-wide, see setlocale) and the previous setting
 * reported, which is what the caller uses to restore it. */
static void u_configthreadlocale(void) { ms_init(); uint32_t was = g_thread_locale; g_thread_locale = A32(0); RETC(was); }
/* __p__commode: the "commit mode" int the CRT keeps for streams.  A real guest
 * cell, like __p__fmode; nothing here buffers to disk behind the guest's back. */
static void u_p_commode(void) { ms_init(); RETC(g_commode_va); }
/* _set_fmode(mode): set the default file translation mode. */
static void u_set_fmode(void) { ms_init(); MEM32(g_fmode_va) = A32(0); RETC(0); }

/* ---- the rest of the api-ms-win-crt modules ----
 * string/time/convert/utility/file-system calls a Windows 10 SDK build imports
 * from the other API sets.  Each is the CRT function it names, on the host's
 * libc, with the guest's path translated the way every other file call in this
 * layer translates it. */
static void u_atof(void) { ms_init(); fp_push_impl(g_st, &g_fp_top, atof(ASTR(0) ? ASTR(0) : "0")); RETC(0); }
static void u_chdir(void) {
    ms_init();
    char path[1024];
    if (!ASTR(0) || !hle_host_path(ASTR(0), path, sizeof path) || chdir(path) != 0) { ms_seterrno(ENOENT); RETC(0xFFFFFFFFu); }
    RETC(0);
}
static void u_mkdir(void) {
    ms_init();
    char path[1024];
    /* the write helper FILLS the host path; its return says whether the path
     * already existed, which for a directory about to be created it does not. */
    if (!ASTR(0)) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    hle_host_path_for_write(ASTR(0), path, sizeof path, 0);
    if (mkdir(path, 0777) != 0) { ms_seterrno(errno); RETC(0xFFFFFFFFu); }
    RETC(0);
}
static void u_unlink(void) {
    ms_init();
    char path[1024];
    if (!ASTR(0) || !hle_host_path(ASTR(0), path, sizeof path) || unlink(path) != 0) { ms_seterrno(ENOENT); RETC(0xFFFFFFFFu); }
    RETC(0);
}
/* _lock_file/_unlock_file take the stream's own mutex.  The stream operations
 * above run on the guest thread under the machine lock, so they never contend
 * for it - a game that locks around its own calls gets the exclusion it asked
 * for, which is what these are for. */
static void u_lock_file(void) {
    ms_init();
    ms_stream *st = ms_slot_of_va(A32(0));
    if (!st) { ms_seterrno(EBADF); RETCV(); }
    pthread_mutex_lock(&st->lock);
    RETCV();
}
static void u_unlock_file(void) {
    ms_init();
    ms_stream *st = ms_slot_of_va(A32(0));
    if (!st) { ms_seterrno(EBADF); RETCV(); }
    pthread_mutex_unlock(&st->lock);
    RETCV();
}
/* _fseeki64(stream, offset, whence): the 64-bit seek. */
static void u_fseeki64(void) {
    ms_init();
    ms_stream *st = ms_slot_of_va(A32(0));
    if (!st) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int64_t off = (int64_t)((uint64_t)A32(1) | ((uint64_t)A32(2) << 32));
    RETC(fseeko(st->fp, (off_t)off, (int)A32(3)) == 0 ? 0u : 0xFFFFFFFFu);
}
/* _time64(&t): the 64-bit time, in edx:eax and in the caller's cell. */
static void u_time64(void) {
    ms_init();
    int64_t now = (int64_t)(hle_clock_virtual() ? (time_t)(hle_clock_ms() / 1000u) : time(NULL));
    if (A32(0)) { MEM32(A32(0)) = (uint32_t)now; MEM32(A32(0) + 4) = (uint32_t)((uint64_t)now >> 32); }
    g_eax = (uint32_t)now;
    g_edx = (uint32_t)((uint64_t)now >> 32);
    g_esp += 4u;
    return;
}
/* _get_stream_buffer_pointers(stream, &base, &ptr, &cnt): the guest _iobuf's
 * own fields, which is where this layer keeps them (they are not used for
 * buffering - the host stream owns its buffer, as msvcrt.c's header says). */
static void u_get_stream_buffer_pointers(void) {
    ms_init();
    ms_stream *st = ms_slot_of_va(A32(0));
    if (!st) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    if (A32(1)) MEM32(A32(1)) = MEM32(st->iobuf + IOB_BASE);
    if (A32(2)) MEM32(A32(2)) = MEM32(st->iobuf + IOB_PTR);
    if (A32(3)) MEM32(A32(3)) = (uint32_t)(int32_t)MEM32(st->iobuf + IOB_CNT);
    RETC(0);
}
/* __stdio_common_vsprintf(options, buffer, bufsize, format, locale, argptr):
 * the UCRT's own printf engine, where argptr IS a guest va_list - a pointer
 * into the guest stack, which is exactly what hle_guest_format reads.  options
 * is a 64-bit mask on the stack (two words), and the two the CRT sets for a
 * plain vsprintf are the ones that matter here: the secure variant (which
 * returns -1 for an overlong result) and the _s variant (same, with a buffer
 * size).  Both are honoured by the size check below. */
static void u_stdio_common_vsprintf(void) {
    ms_init();
    /* options is the first 64-bit argument: words 0 and 1 */
    uint32_t buffer = A32(2), bufsize = A32(3);
    const char *fmt = (const char *)hle_ptr(A32(4));
    uint32_t args = A32(6);
    char *out = (char *)hle_ptr(buffer);
    if (!out || !fmt) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    int n = hle_guest_format(t_fmt, sizeof t_fmt, fmt, args);
    if (bufsize && (uint32_t)n >= bufsize) { memcpy(out, t_fmt, bufsize - 1); out[bufsize - 1] = 0; RETC(0xFFFFFFFFu); }
    memcpy(out, t_fmt, (size_t)n + 1);
    RETC((uint32_t)n);
}

/* ---- vcruntime140 ----
 * The MSVC C++ runtime is a deliberate non-goal (see README: msvcp140 and the
 * exception machinery need guest SEH and a C++ library ABI).  These five are
 * not that: they are the small CRT helpers a vcruntime140-linked build calls
 * around its C++ objects, and each one can be answered for real.
 *
 * __std_exception_data is { const char *_What; char _DoFree; } - 8 bytes, the
 * pointer at 0 and the flag at 4 - and the copy/destroy pair is exactly the
 * MSVC runtime's: copy the what-string when the source owns it, free it when
 * the destination owns it.  (Wine's include/msvcrt/vcruntime_exception.h and
 * the std::exception bodies that use them are the reference.)
 *
 * __telemetry_main_invoke_trigger/return_trigger take one argument (the
 * binary's call site pushes one and pops it: entry0 at 0x00410942 in
 * TrackEditor.exe) and do nothing here, because there is no telemetry backend
 * to trigger - the CRT's own path is a no-op with telemetry off. */
static void u_std_exception_copy(void) {                 /* (const src, dst) */
    ms_init();
    uint32_t src = A32(0), dst = A32(1);
    if (!src || !dst) RETCV();
    uint32_t what = MEM32(src);
    MEM32(dst) = what;
    MEM8(dst + 4) = 0;
    if (MEM8(src + 4) && what) {                         /* the source owns it: take a copy */
        const char *p = (const char *)hle_ptr(what);
        MEM32(dst) = hle_guest_strdup(p);
        MEM8(dst + 4) = MEM32(dst) ? 1 : 0;
    }
    RETCV();
}
static void u_std_exception_destroy(void) {              /* (data) */
    ms_init();
    uint32_t d = A32(0);
    if (!d) RETCV();
    if (MEM8(d + 4) && MEM32(d)) hle_free(hle_ptr(MEM32(d)));
    MEM32(d) = 0;
    MEM8(d + 4) = 0;
    RETCV();
}
static void u_telemetry_trigger(void) { ms_init(); RETCV(); }   /* no telemetry backend to trigger */
/* __vcrt_InitializeCriticalSectionEx(cs, spinCount, flags): the same guest
 * CRITICAL_SECTION kernel32.c initialises (24 bytes, owner at 0, count at 4). */
static void u_vcrt_InitializeCriticalSectionEx(void) {
    ms_init();
    uint32_t cs = A32(0);
    if (!cs) RETC(0);
    memset(hle_ptr(cs), 0, 24);
    MEM32(cs + 4) = 0xFFFFFFFFu;
    RETC(1);
}

/* _crt_atexit: the CRT's own atexit, the same chain msvcrt's atexit uses. */
static void u_crt_atexit(void) {
    ms_init();
    RETC(ms_atexit_add(A32(0)) ? 0u : 1u);
}
/* _configure_narrow_argv(mode): the CRT records whether to parse the command
 * line itself.  This layer always has the arguments (ms_init builds argc/argv
 * from the host's), so the mode is recorded and the answer is success. */
static void u_configure_narrow_argv(void) { ms_init(); g_argv_mode = (int)A32(0); RETC(0); }
/* _initialize_narrow_environment(): the UCRT sets up `environ`.  msvcrt.c keeps
 * the environment block the same way __getmainargs does, so this is a no-op
 * that answers 0 - the environment really is initialised by then. */
static void u_initialize_narrow_environment(void) { ms_init(); RETC(0); }
/* _get_narrow_winmain_command_line(): the command line WinMain is given. */
static void u_get_narrow_winmain_command_line(void) {
    ms_init();
    /* The command line WinMain would be given: this process's own, as a guest
     * string (the same source GetCommandLineA answers from). */
    if (!g_cmdline_va) {
        char line[1024];
        int n = 0;
        for (int i = 1; i < g_host_argc && n < (int)sizeof line - 2; i++) {
            if (i > 1) line[n++] = ' ';
            line[n++] = '"';
            for (const char *p = g_host_argv[i]; *p && n < (int)sizeof line - 3; p++) line[n++] = *p;
            line[n++] = '"';
        }
        line[n] = 0;
        g_cmdline_va = hle_guest_strdup(line);
    }
    RETC(g_cmdline_va);
}
/* _controlfp_s(&current, new, mask): the FP control word.  The rounding bits
 * are applied to the host (fesetround), because that is the FP the lifted code
 * actually executes on; the x87 precision-control bits have no host equivalent
 * on SSE/ARM and are recorded in the returned word rather than applied - a
 * documented difference, not a claim that they took effect. */
static void u_controlfp_s(void) {
    ms_init();
    uint32_t cur = A32(0), newv = A32(1), mask = A32(2);
    if (cur) MEM32(cur) = g_fp_control;
    if (mask) {
        uint32_t v = (g_fp_control & ~mask) | (newv & mask);
        g_fp_control = v;
        int r = FE_TONEAREST;
        if ((v & 0x300u) == 0x000u) r = FE_TONEAREST;        /* _RC_NEAR */
        else if ((v & 0x300u) == 0x100u) r = FE_DOWNWARD;    /* _RC_DOWN */
        else if ((v & 0x300u) == 0x200u) r = FE_UPWARD;      /* _RC_UP   */
        else r = FE_TOWARDZERO;                              /* _RC_CHOP */
        fesetround(r);
    }
    RETC(0);
}
/* _seh_filter_exe: what the CRT's exception filter decides for an exception the
 * guest did not handle.  This layer has no guest SEH (RaiseException ends the
 * run), so nothing routes an exception here; answering EXCEPTION_CONTINUE_SEARCH
 * (0) is what the UCRT does for a code it has no special case for. */
static void u_seh_filter_exe(void) { RETC(0); }
/* The UCRT's invalid-parameter handlers: with no handler installed (this layer
 * has no _set_invalid_parameter_handler) the default one reports and terminates
 * the process, so these do exactly that rather than returning as if the
 * parameter had been acceptable. */
static void u_invalid_parameter_noinfo(void) {
    fprintf(stderr, "[msvcrt] invalid parameter passed to a CRT function\n");
    hle_exit(3);
}
static void u_invalid_parameter_noinfo_noreturn(void) { u_invalid_parameter_noinfo(); }
/* terminate(): C++ std::terminate with no handler ends the process. */
static void u_terminate(void) {
    fprintf(stderr, "[msvcrt] std::terminate called\n");
    hle_exit(3);
}
/* _register_thread_local_exe_atexit_callback(cb): the UCRT runs these when the
 * thread exits.  This layer's guest threads end inside hle_call_guest, so the
 * callbacks are run at process exit instead - the callback really runs, at a
 * later point than Windows would, and the difference is stated here rather than
 * hidden. */
static void u_register_thread_local_exe_atexit_callback(void) {
    ms_init();
    uint32_t cb = A32(0);
    if (cb && g_tls_cb_n < UCRT_MAX_TLS_CB) g_tls_cb[g_tls_cb_n++] = cb;
    RETCV();
}

/* ======================================================= exit and atexit */
static uint32_t g_atexit[MS_MAXATEXIT];
static int g_atexit_n;
static int g_exit_ran;

static void ms_run_onexit_table(void);
static void ms_run_atexit(void) {
    if (g_exit_ran) return;
    g_exit_ran = 1;
    while (g_atexit_n > 0) {                         /* last registered, first run */
        uint32_t fn = g_atexit[--g_atexit_n];
        if (fn) {
            uint32_t a[1] = { 0 };
            hle_call_guest(fn, 0, a);
        }
    }
    /* The UCRT's onexit table (see the api-ms-win-crt section below): its
     * entries are the C++ static destructors a UCRT startup registered, and
     * they run the same way round as atexit's.  Declared here because this is
     * the exit path; defined with the rest of the UCRT state. */
    ms_run_onexit_table();
}
static int ms_atexit_add(uint32_t fn) {
    if (!fn || g_atexit_n >= MS_MAXATEXIT) return 0;
    g_atexit[g_atexit_n++] = fn;
    return 1;
}
static void c_atexit(void) {                         /* (fn) -> 0 ok, nonzero failed */
    ms_init();
    RETC(ms_atexit_add(A32(0)) ? 0u : 1u);
}
static void c_onexit(void) {                         /* (fn) -> fn, or NULL */
    ms_init();
    uint32_t fn = A32(0);
    RETC(ms_atexit_add(fn) ? fn : 0u);
}
static void c_exit(void) {                           /* (code) */
    ms_init();
    int code = (int)A32(0);
    ms_run_atexit();
    fflush(NULL);
    hle_exit(code);
}
static void c_cexit(void) {                          /* the exit handlers, without leaving */
    ms_init();
    ms_run_atexit();
    fflush(NULL);
    RETCV();
}
/* _exit leaves at once: no atexit handlers (unlike exit), the host streams
 * flushed so what the guest wrote is not lost on the way out. */
static void c_uexit(void) {                          /* (code) */
    ms_init();
    fflush(NULL);
    hle_exit((int)A32(0));
}
static void ms_abort_now(void) {
    fprintf(stderr, "abnormal program termination\n");
    fflush(NULL);
    hle_exit(3);                                     /* the host's ExitProcess path, then _exit(3) */
}
static void c_abort(void) { ms_init(); ms_abort_now(); }
static void c_assert(void) {                         /* (expr, file, line) */
    ms_init();
    fprintf(stderr, "Assertion failed: %s, file %s, line %u\n",
            ASTR(0) ? ASTR(0) : "?", ASTR(1) ? ASTR(1) : "?", A32(2));
    ms_abort_now();
}

/* =============================================================== signal
 * The handler is recorded and returned by later signal() calls, and SIG_DFL /
 * SIG_IGN are stored as the guest passed them, but no host signal is forwarded
 * into lifted code: a handler that runs on the host's signal stack could take
 * the machine lock while the running guest thread holds it, and there is no
 * safe place to re-enter the lifted model from. A guest that installs a SIGINT
 * handler to save on Ctrl-C therefore does not get it. That is a real gap,
 * documented here rather than papered over with a fake install. */
static uint32_t g_sig[MS_NSIG];
static void c_signal(void) {                         /* (sig, handler) -> the previous handler */
    ms_init();
    uint32_t sig = A32(0), h = A32(1);
    if (sig == 0 || sig >= MS_NSIG) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }   /* SIG_ERR */
    uint32_t old = g_sig[sig];
    g_sig[sig] = h;
    RETC(old);                                       /* 0 is SIG_DFL, 1 SIG_IGN */
}

/* ======================================================= data exports
 * The loader writes this address into the IAT slot for a data import, so the
 * guest reads the object itself and not a shim's code. */
uint32_t hle_msvcrt_data(const char *name) {
    if (!name) return 0;
    ms_init();
    if (!strcmp(name, "_iob"))         return g_iob_va;
    if (!strcmp(name, "_pctype"))      return g_pctype_va;   /* the cell holding the table pointer */
    if (!strcmp(name, "_ctype"))       return g_ctype_va;    /* the table itself */
    if (!strcmp(name, "_fmode"))       return g_fmode_va;
    if (!strcmp(name, "__mb_cur_max")) return g_mbmax_va;
    return 0;
}

/* ============================================================ the shims */
const win32hle_shim win32hle_msvcrt[] = {
    /* startup */
    /* The api-ms-win-crt-runtime-l1-1-0 names that are the same functions the
     * msvcrt names above already answer (_initterm, _initterm_e, _errno, _cexit,
     * _exit, exit, __set_app_type/_set_app_type) resolve to those shims: the
     * loader looks a name up across the whole registry, so one implementation
     * serves both import spellings.  The rest are the UCRT's startup calls. */
    { "_libm_sse2_sin_precise",       u_libm_sin },
    { "_libm_sse2_cos_precise",       u_libm_cos },
    { "_libm_sse2_sqrt_precise",      u_libm_sqrt },
    { "_libm_sse2_atan_precise",      u_libm_atan },
    { "_libm_sse2_log10_precise",     u_libm_log10 },
    { "_libm_sse2_pow_precise",       u_libm_pow },
    { "_except1",                     u_except1 },
    { "_callnewh",                    u_callnewh },
    { "_set_new_mode",                u_set_new_mode },
    { "_configthreadlocale",          u_configthreadlocale },
    { "__p__commode",                 u_p_commode },
    { "_set_fmode",                   u_set_fmode },
    { "atof",                         u_atof },
    { "_chdir",                       u_chdir },
    { "_mkdir",                       u_mkdir },
    { "_unlink",                      u_unlink },
    { "_lock_file",                   u_lock_file },
    { "_unlock_file",                 u_unlock_file },
    { "_fseeki64",                    u_fseeki64 },
    { "_time64",                      u_time64 },
    { "_get_stream_buffer_pointers",  u_get_stream_buffer_pointers },
    { "__stdio_common_vsprintf",      u_stdio_common_vsprintf },
    { "__std_exception_copy",         u_std_exception_copy },
    { "__std_exception_destroy",      u_std_exception_destroy },
    { "__telemetry_main_invoke_trigger", u_telemetry_trigger },
    { "__telemetry_main_return_trigger", u_telemetry_trigger },
    { "__vcrt_InitializeCriticalSectionEx", u_vcrt_InitializeCriticalSectionEx },
    { "_initialize_onexit_table",     u_initialize_onexit_table },
    { "_register_onexit_function",    u_register_onexit_function },
    { "_crt_atexit",                  u_crt_atexit },
    { "_configure_narrow_argv",       u_configure_narrow_argv },
    { "_initialize_narrow_environment", u_initialize_narrow_environment },
    { "_get_narrow_winmain_command_line", u_get_narrow_winmain_command_line },
    { "_controlfp_s",                 u_controlfp_s },
    { "_seh_filter_exe",              u_seh_filter_exe },
    { "_invalid_parameter_noinfo",    u_invalid_parameter_noinfo },
    { "_invalid_parameter_noinfo_noreturn", u_invalid_parameter_noinfo_noreturn },
    { "_register_thread_local_exe_atexit_callback", u_register_thread_local_exe_atexit_callback },
    { "_set_app_type",                c_set_app_type },
    { "_c_exit",                      c_cexit },
    { "terminate",                    u_terminate },
    { "__getmainargs",                c_getmainargs },
    { "__set_app_type",               c_set_app_type },
    { "__setusermatherr",             c_setusermatherr },
    { "__p__environ",                 c_p_environ },
    { "__p__iob",                     c_p_iob },
    { "__iob_func",                   c_iob_func },
    { "__p__pctype",                  c_p_pctype },
    { "__p__fmode",                   c_p_fmode },
    { "__p___mb_cur_max",             c_p_mbmax },
    { "__p___argc",                   c_p_argc },
    { "__p___argv",                   c_p_argv },
    { "_initterm",                    c_initterm },
    { "_initterm_e",                  c_initterm_e },
    /* errno */
    { "_errno",                       c_errno },
    { "__p__errno",                   c_p_errno },
    /* stdio */
    { "fopen",                        c_fopen },
    { "fclose",                       c_fclose },
    { "fflush",                       c_fflush },
    { "freopen",                      c_freopen },
    { "fread",                        c_fread },
    { "fwrite",                       c_fwrite },
    { "fgetc",                        c_fgetc },
    { "fputc",                        c_fputc },
    { "getc",                         c_getc },
    { "ungetc",                       c_ungetc },
    { "fgets",                        c_fgets },
    { "fputs",                        c_fputs },
    { "fseek",                        c_fseek },
    { "ftell",                        c_ftell },
    { "setbuf",                       c_setbuf },
    { "setvbuf",                      c_setvbuf },
    { "printf",                       c_printf },
    { "fprintf",                      c_fprintf },
    { "sprintf",                      c_sprintf },
    { "puts",                         c_puts },
    { "sscanf",                       c_sscanf },
    { "_fileno",                      c_fileno },
    { "_fdopen",                      c_fdopen },
    { "_setmode",                     c_setmode },
    { "_write",                       c_write },
    { "_getw",                        c_getw },
    { "_putw",                        c_putw },
    { "remove",                       c_remove },
    { "_fullpath",                    c_fullpath },
    /* directory search */
    { "_findfirst",                   c_findfirst },
    { "_findnext",                    c_findnext },
    { "_findclose",                   c_findclose },
    /* heap */
    { "malloc",                       c_malloc },
    { "calloc",                       c_calloc },
    { "realloc",                      c_realloc },
    { "free",                         c_free },
    { "_msize",                       c_msize },
    { "_strdup",                      c_strdup },
    /* string and memory */
    { "memcpy",                       c_memcpy },
    { "memmove",                      c_memmove },
    { "memset",                       c_memset },
    { "memchr",                       c_memchr },
    { "memcmp",                       c_memcmp },
    { "strlen",                       c_strlen },
    { "strcpy",                       c_strcpy },
    { "strncpy",                      c_strncpy },
    { "strcat",                       c_strcat },
    { "strncat",                      c_strncat },
    { "strcmp",                       c_strcmp },
    { "strncmp",                      c_strncmp },
    { "_stricmp",                     c_stricmp },
    { "_strnicmp",                    c_strnicmp },
    { "strchr",                       c_strchr },
    { "strrchr",                      c_strrchr },
    { "strstr",                       c_strstr },
    { "strcoll",                      c_strcoll },
    { "strxfrm",                      c_strxfrm },
    { "tolower",                      c_tolower },
    { "toupper",                      c_toupper },
    /* ctype */
    { "_isctype",                     c_isctype },
    { "isspace",                      c_isspace },
    /* conversions */
    { "atoi",                         c_atoi },
    { "strtol",                       c_strtol },
    { "strtoul",                      c_strtoul },
    { "strtod",                       c_strtod },
    /* rand */
    { "rand",                         c_rand },
    { "srand",                        c_srand },
    /* math */
    { "sin",                          c_sin },
    { "cos",                          c_cos },
    { "sqrt",                         c_sqrt },
    { "fabs",                         c_fabs },
    { "floor",                        c_floor },
    { "pow",                          c_pow },
    { "atan2",                        c_atan2 },
    { "_finite",                      c_finite },
    /* time and locale */
    { "time",                         c_time },
    { "strftime",                     c_strftime },
    { "setlocale",                    c_setlocale },
    /* exit */
    { "exit",                         c_exit },
    { "_exit",                        c_uexit },
    { "_cexit",                       c_cexit },
    { "atexit",                       c_atexit },
    { "_onexit",                      c_onexit },
    { "abort",                        c_abort },
    { "_assert",                      c_assert },
    { "signal",                       c_signal },
    { 0, 0 }
};
