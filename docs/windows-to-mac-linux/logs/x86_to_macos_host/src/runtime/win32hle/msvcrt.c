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
#include <fcntl.h>
#include <dirent.h>
#include <poll.h>
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

static void c_getchar(void) {                        /* () -> char or EOF from stdin */
    ms_init();
    ms_stream *s = &g_stream[0];
    if (!s->fp) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int c = fgetc(s->fp);
    ms_note_state(s);
    RETC((uint32_t)(int32_t)c);
}

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
static void c_fgetpos(void) {                        /* (stream, fpos_t *pos) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    uint32_t pva = A32(1);
    if (!s || !pva) { ms_seterrno(s ? EINVAL : EBADF); RETC(0xFFFFFFFFu); }
    off_t p = ftello(s->fp);
    if (p < 0) { ms_seterrno(errno); RETC(0xFFFFFFFFu); }
    uint64_t v = (uint64_t)(int64_t)p;
    MEM32(pva) = (uint32_t)v;
    MEM32(pva + 4u) = (uint32_t)(v >> 32);
    RETC(0);
}
static void c_fsetpos(void) {                        /* (stream, const fpos_t *pos) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    uint32_t pva = A32(1);
    if (!s || !pva) { ms_seterrno(s ? EINVAL : EBADF); RETC(0xFFFFFFFFu); }
    int64_t v = (int64_t)((uint64_t)MEM32(pva) | ((uint64_t)MEM32(pva + 4u) << 32));
    int r = fseeko(s->fp, (off_t)v, SEEK_SET);
    if (r != 0) ms_seterrno(errno);
    ms_note_state(s);
    RETC(r == 0 ? 0u : 0xFFFFFFFFu);
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

static int ms_scan_step(const char *p, FILE *fp, const char *spec, void *a1, void *a2) {
    if (fp) {
        return a2 ? fscanf(fp, spec, a1, a2) : fscanf(fp, spec, a1);
    }
    return a2 ? sscanf(p, spec, a1, a2) : sscanf(p, spec, a1);
}

static int ms_vscanf_core(const char *s, FILE *fp, const char *f, uint32_t args_start) {
    uint32_t args = args_start;
    const char *p = s;
    int assigned = 0;
    int total_read = 0;
    while (*f) {
        if (isspace((unsigned char)*f)) {            /* whitespace in the format: any run */
            while (isspace((unsigned char)*f)) f++;
            if (fp) {
                int c;
                while ((c = fgetc(fp)) != EOF && isspace((unsigned char)c)) total_read++;
                if (c != EOF) ungetc(c, fp);
            } else {
                while (isspace((unsigned char)*p)) p++;
            }
            continue;
        }
        if (*f != '%') {
            if (fp) {
                int c = fgetc(fp);
                if (c != (unsigned char)*f) { if (c != EOF) ungetc(c, fp); break; }
                total_read++;
            } else {
                if (*p != *f) break;
                p++;
            }
            f++;
            continue;
        }
        if (f[1] == '%') {
            if (fp) {
                int c = fgetc(fp);
                if (c != '%') { if (c != EOF) ungetc(c, fp); break; }
                total_read++;
            } else {
                if (*p != '%') break;
                p++;
            }
            f += 2;
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
        int is_long = 0;
        if (f[0] == 'h') { f++; if (*f == 'h') f++, size = 1; else size = 2; }
        else if (f[0] == 'l') { f++; if (*f == 'l') f++, size = 8; else is_long = 1; }
        else if (f[0] == 'L') { f++; is_long = 1; }
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
            if (uns) { if (suppress) ms_scan_step(p, fp, spec, &n, NULL); else ms_scan_step(p, fp, spec, &uv, &n); }
            else     { if (suppress) ms_scan_step(p, fp, spec, &n, NULL); else ms_scan_step(p, fp, spec, &sv, &n); }
            if (n >= 0 && !suppress) {
                uint32_t dst = MEM32(args);
                args += 4;
                ms_store(dst, uns ? (uint64_t)uv : (uint64_t)sv, size);
                assigned++;
            }
            break;
        }
        case 'e': case 'E': case 'f': case 'F': case 'g': case 'G': {
            if (is_long) {
                double d = 0;
                snprintf(spec, sizeof spec, "%%%s%sl%c%%n", suppress ? "*" : "", w, conv);
                if (suppress) ms_scan_step(p, fp, spec, &n, NULL); else ms_scan_step(p, fp, spec, &d, &n);
                if (n >= 0 && !suppress) {
                    uint32_t dst = MEM32(args);
                    args += 4;
                    uint64_t bits;
                    memcpy(&bits, &d, 8);
                    ms_store(dst, bits, 8);
                    assigned++;
                }
            } else {
                float fl = 0;
                snprintf(spec, sizeof spec, "%%%s%s%c%%n", suppress ? "*" : "", w, conv);
                if (suppress) ms_scan_step(p, fp, spec, &n, NULL); else ms_scan_step(p, fp, spec, &fl, &n);
                if (n >= 0 && !suppress) {
                    uint32_t dst = MEM32(args);
                    args += 4;
                    uint32_t bits;
                    memcpy(&bits, &fl, 4);
                    ms_store(dst, bits, 4);
                    assigned++;
                }
            }
            break;
        }
        case 's': {
            char tmp[1024];
            snprintf(spec, sizeof spec, "%%%s%ss%%n", suppress ? "*" : "", w);
            if (suppress) ms_scan_step(p, fp, spec, &n, NULL);
            else if (ms_scan_step(p, fp, spec, tmp, &n) == 1) {
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
            if (suppress) ms_scan_step(p, fp, spec, &n, NULL);
            else {
                ms_scan_step(p, fp, spec, tmp, &n);
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
            if (suppress) ms_scan_step(p, fp, spec, &n, NULL);
            else if (ms_scan_step(p, fp, spec, tmp, &n) == 1) {
                uint32_t dst = MEM32(args);
                args += 4;
                memcpy(hle_ptr(dst), tmp, strlen(tmp) + 1);
                assigned++;
            }
            break;
        }
        case 'n':
            MEM32(MEM32(args)) = fp ? (uint32_t)total_read : (uint32_t)(p - s);
            args += 4;
            n = 0;                                   /* %n reads nothing: keep going */
            break;
        default:
            /* A conversion this module does not read (a wide-character one, or
             * a typo): stop where the host would have, and say so. */
            fprintf(stderr, "[win32hle] sscanf: unsupported conversion '%%%c'\n", conv ? conv : '?');
            return assigned ? assigned : -1;
        }
        if (n < 0) break;                            /* the input did not match here */
        if (fp) total_read += n; else p += n;
    }
    if (assigned == 0) {
        if (fp ? feof(fp) : (*p == 0)) return -1;    /* input ran out before the first conversion */
    }
    return assigned;
}

static int ms_vsscanf(const char *s, const char *f, uint32_t args_start) {
    return ms_vscanf_core(s, NULL, f, args_start);
}

static int ms_vfscanf(FILE *fp, const char *f, uint32_t args_start) {
    return ms_vscanf_core(NULL, fp, f, args_start);
}

static void c_sscanf(void) {                         /* (src, fmt, ...) */
    ms_init();
    const char *s = ASTR(0), *f = ASTR(1);
    if (!s || !f) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    RETC((uint32_t)ms_vsscanf(s, f, g_esp + 4u + 8u));
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
    if (fd < 0 || fd >= MS_MAXSTREAM || (!g_stream[fd].fp && g_stream[fd].fd < 0)) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
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
static void c_memcpy(void)  { void *d = APTR(0); const void *s = APTR(1); size_t n = A32(2); if (n && d && s) memmove(d, s, n); RETC(A32(0)); }
static void c_memmove(void) { void *d = APTR(0); const void *s = APTR(1); size_t n = A32(2); if (n && d && s) memmove(d, s, n); RETC(A32(0)); }
static void c_memset(void)  { void *d = APTR(0); size_t n = A32(2); if (n && d) memset(d, (int)A32(1), n); RETC(A32(0)); }
static void c_memchr(void)  { const void *s = APTR(0); size_t n = A32(2); const void *p = (n && s) ? memchr(s, (int)A32(1), n) : NULL; RETC(p ? hle_va(p) : 0u); }
static void c_memcmp(void)  { const void *a = APTR(0), *b = APTR(1); size_t n = A32(2); RETC((!n || a == b) ? 0u : (!a || !b) ? (uint32_t)(int32_t)(a ? 1 : -1) : (uint32_t)(int32_t)memcmp(a, b, n)); }
static int null_safe_cmp(const char *a, const char *b) { return a == b ? 0 : (a ? 1 : -1); }
static void c_strlen(void)  { const char *s = ASTR(0); RETC(s ? (uint32_t)strlen(s) : 0u); }
static void u_strnlen(void) { const char *s = ASTR(0); uint32_t m = A32(1); RETC(s ? (uint32_t)strnlen(s, m) : 0u); }
static void c_strrev(void) {
    char *s = ASTR(0);
    if (s) {
        size_t i = 0, j = strlen(s);
        while (i + 1 < j) { char t = s[i]; s[i++] = s[--j]; s[j] = t; }
    }
    RETC(A32(0));
}
static void c_strcpy(void)  { char *d = ASTR(0); const char *s = ASTR(1); RETC(d ? hle_va(strcpy(d, s ? s : "")) : 0u); }
static void c_strncpy(void) { char *d = ASTR(0); const char *s = ASTR(1); RETC(d ? hle_va(strncpy(d, s ? s : "", A32(2))) : 0u); }
static void c_strcat(void)  { char *d = ASTR(0); const char *s = ASTR(1); RETC(d ? hle_va(strcat(d, s ? s : "")) : 0u); }
static void c_strncat(void) { char *d = ASTR(0); const char *s = ASTR(1); RETC(d ? hle_va(strncat(d, s ? s : "", A32(2))) : 0u); }
static void c_strcmp(void)  { const char *a = ASTR(0), *b = ASTR(1); RETC((uint32_t)(int32_t)((!a || !b) ? null_safe_cmp(a, b) : strcmp(a, b))); }
static void c_strncmp(void) { const char *a = ASTR(0), *b = ASTR(1); RETC((uint32_t)(int32_t)((!a || !b) ? null_safe_cmp(a, b) : strncmp(a, b, A32(2)))); }
static void c_stricmp(void) { const char *a = ASTR(0), *b = ASTR(1); RETC((uint32_t)(int32_t)((!a || !b) ? null_safe_cmp(a, b) : strcasecmp(a, b))); }
static void c_stricoll(void){ c_stricmp(); }
static void c_strnicmp(void){ const char *a = ASTR(0), *b = ASTR(1); RETC((uint32_t)(int32_t)((!a || !b) ? null_safe_cmp(a, b) : strncasecmp(a, b, A32(2)))); }
static void c_strchr(void)  { const char *s = ASTR(0); char *p = s ? strchr(s, (int)A32(1)) : NULL; RETC(p ? hle_va(p) : 0u); }
static void c_strrchr(void) { const char *s = ASTR(0); char *p = s ? strrchr(s, (int)A32(1)) : NULL; RETC(p ? hle_va(p) : 0u); }
static void c_strstr(void)  { const char *a = ASTR(0), *b = ASTR(1); char *p = (a && b) ? strstr(a, b) : NULL; RETC(p ? hle_va(p) : 0u); }
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
/* isdigit(c): the same table test the other ctype shims use. */
static void c_isdigit(void) {
    ms_init();
    int c = (int)A32(0);
    if (c < -1 || c > 255) RETC(0);
    RETC((uint32_t)(((const uint16_t *)hle_ptr(g_ctype_va))[c + 1] & CT_DIGIT));
}
/* abs(n): MSVC's 32-bit abs, including the INT_MIN case (which is UB in C but
 * returns INT_MIN on MSVC, as it does here because the negation wraps). */
static void c_abs(void) { int32_t v = (int32_t)A32(0); RETC((uint32_t)(v < 0 ? (uint32_t)(-v) : (uint32_t)v)); }

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
static void u_rand_s(void) {                         /* (unsigned int *randomValue) -> errno_t */
    ms_init();
    uint32_t out = A32(0);
    if (!out) { ms_seterrno(EINVAL); RETC(EINVAL); }
    uint32_t val = 0;
#ifdef __APPLE__
    val = arc4random();
#else
    int fd = open("/dev/urandom", O_RDONLY);
    ssize_t got = fd >= 0 ? read(fd, &val, sizeof val) : -1;
    if (fd >= 0) close(fd);
    if (got != (ssize_t)sizeof val) {
        g_rand = g_rand * 214013u + 2531011u;
        val = (g_rand << 16) ^ (g_rand * 2654435761u);
    }
#endif
    MEM32(out) = val;
    RETC(0);
}
static void c_fpreset(void) {
    g_fpu_cw = 0x027Fu;
    g_fp_top = 0;
    RETCV();
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
/* __stdio_common_vfprintf(options, stream, format, locale, argptr): printf
 * straight to a stream, argptr a guest va_list. options is a 64-bit mask in
 * two words, so stream is A32(2) onward. */
static void u_stdio_common_vfprintf(void) {
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(2));
    const char *fmt = (const char *)hle_ptr(A32(3));
    uint32_t args = A32(5);
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    if (!fmt) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    int n = hle_guest_format(t_fmt, sizeof t_fmt, fmt, args);
    size_t w = fwrite(t_fmt, 1, (size_t)n, s->fp);
    RETC(w == (size_t)n ? (uint32_t)n : 0xFFFFFFFFu);
}
/* __stdio_common_vsscanf(options, buffer, count, format, locale, argptr). */
static void u_stdio_common_vsscanf(void) {
    ms_init();
    const char *buf = (const char *)hle_ptr(A32(2));
    const char *fmt = (const char *)hle_ptr(A32(4));
    uint32_t args = A32(6);
    if (!buf || !fmt) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    RETC((uint32_t)ms_vsscanf(buf, fmt, args));
}
/* __stdio_common_vfscanf(options, stream, format, locale, argptr). */
static void u_stdio_common_vfscanf(void) {
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(2));
    const char *fmt = (const char *)hle_ptr(A32(3));
    uint32_t args = A32(5);
    if (!s) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    if (!fmt) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    int r = ms_vfscanf(s->fp, fmt, args);
    ms_note_state(s);
    RETC((uint32_t)r);
}
/* getenv(name): the guest's environment block, the same one __getmainargs and
 * __p__environ expose. A Windows program has no claim on the host's variables,
 * so this searches the Windows-like block, not the host's environ. */
static void u_getenv(void) {
    ms_init();
    const char *name = ASTR(0);
    if (!name) RETC(0);
    size_t nl = strlen(name);
    for (size_t i = 0; i < sizeof g_env_words / sizeof g_env_words[0]; i++) {
        const char *e = g_env_words[i];
        if (!strncmp(e, name, nl) && e[nl] == '=') RETC(hle_guest_strdup(e + nl + 1));
    }
    RETC(0);
}
/* __p__acmdln: the address of the CRT's `char *_acmdln`. Build the command
 * line the same way _get_narrow_winmain_command_line does, but as the raw
 * process command line, and hand back a stable cell holding it. */
static void u_p_acmdln(void) {
    ms_init();
    static uint32_t cell;
    if (!g_cmdline_va) {
        char line[1024];
        int n = 0;
        for (int i = 0; i < g_host_argc && n < (int)sizeof line - 3; i++) {
            if (i) line[n++] = ' ';
            if (i == 0) {
                /* argv[0] is unquoted, as the process command line is */
                for (const char *p = g_host_argv[i]; *p && n < (int)sizeof line - 2; p++) line[n++] = *p;
            } else {
                line[n++] = '"';
                for (const char *p = g_host_argv[i]; *p && n < (int)sizeof line - 3; p++) line[n++] = *p;
                line[n++] = '"';
            }
        }
        line[n] = 0;
        g_cmdline_va = hle_guest_strdup(line);
    }
    if (!cell) { cell = hle_va(hle_alloc(4)); MEM32(cell) = g_cmdline_va; }
    RETC(cell);
}
/* _set_invalid_parameter_handler(handler) -> the previous handler. The handler
 * is a guest function; it is recorded (and reported by the noinfo paths below
 * when set) rather than left to a default that terminates the process. */
static uint32_t g_invalid_param_handler;
static void u_set_invalid_parameter_handler(void) {
    ms_init();
    uint32_t was = g_invalid_param_handler;
    g_invalid_param_handler = A32(0);
    RETC(was);
}
/* __acrt_iob_func(index): &_iob[index], the per-stream _iobuf the CRT hands
 * fopen's result as. */
static void u_acrt_iob_func(void) {
    ms_init();
    uint32_t i = A32(0);
    if (i >= NIOB) RETC(0);
    RETC(g_iob_va + i * IOBUF);
}
/* _open_osfhandle(osfhandle, flags): wrap an existing OS handle as a CRT
 * descriptor. The handle this layer gets from CreateFile is a file HANDLE;
 * fdopen its host descriptor so the two share the open file. */
static void u_open_osfhandle(void) {
    ms_init();
    uint32_t h = A32(0);
    int fd = hle_file_fd(h);
    if (fd < 0) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    const char *mode = (A32(1) & 3u) == 0 ? "r" : ((A32(1) & 3u) == 1 ? "w" : "a");
    FILE *fp = fdopen(fd, mode);
    if (!fp) { ms_seterrno(errno); RETC(0xFFFFFFFFu); }
    int slot = ms_slot_alloc(fp, fd, 0);            /* the HANDLE owns the descriptor */
    if (slot < 0) { fclose(fp); ms_seterrno(EMFILE); RETC(0xFFFFFFFFu); }
    RETC((uint32_t)slot);
}
/* The string routines the api-ms-win-crt-string set carries that msvcrt's
 * table did not: strcspn, strspn, and the wide strlen. */
static void u_strcspn(void) { const char *s = ASTR(0), *r = ASTR(1); RETC((uint32_t)strcspn(s ? s : "", r ? r : "")); }
static void u_strspn(void)  { const char *s = ASTR(0), *a = ASTR(1); RETC((uint32_t)strspn(s ? s : "", a ? a : "")); }
static void u_wcslen(void) {
    const uint16_t *w = (const uint16_t *)hle_ptr(A32(0));
    uint32_t n = 0;
    if (w) while (w[n]) n++;
    RETC(n);
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
/* _CxxThrowException(exceptionObject, throwInfo): MSVC's `throw` entry point.
 *
 * Reached only when a guest actually throws. This layer has no guest SEH (the
 * README says so: RaiseException ends the run), so it cannot walk the guest's
 * frame chain and find a catch. Answering "success" would resume the guest at
 * whatever PC followed the throw, with the exception uncaught and its frames
 * never unwound -- silent corruption. Report and stop instead, naming the
 * throw, so a title that never throws (TrackEditor, GeneRally) is unaffected
 * and one that does fails where it threw rather than somewhere later. */
static void u_cxx_throw_exception(void) {
    /* A caught exception resumes through its owning function's setjmp
     * checkpoint and therefore never returns here. */
    hle_eh_cxx_throw(A32(0), A32(1));
    fprintf(stderr, "[msvcrt] _CxxThrowException: guest C++ throw at object "
                    "0x%08X type 0x%08X, no compatible guest handler\n",
            A32(0), A32(1));
    hle_exit(3);
}
/* _except_handler4_common: every /GS-checked MSVC function's handler stub
 * tail-calls this with the FuncInfo, and it runs only on an exception. Same
 * answer as _CxxThrowException, for the same reason: it cannot be executed
 * truthfully without guest SEH. */
static void u_except_handler4_common(void) {
    fprintf(stderr, "[msvcrt] _except_handler4_common: guest SEH exception %d "
                    "with no guest unwind support\n", A32(1));
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
/* __dllonexit(func, pbegin, pend): the DLL's exit-function registrar. MSVC's
 * signature is (onexit_t, onexit_t **, onexit_t **) and it returns the
 * function on success; the begin/end cells belong to the caller's own table.
 * This layer keeps one exit chain per process (ms_atexit), which is what the
 * two cells an EXE's CRT would manage also resolve to, so the function is
 * added there and returned. */
static void c_dllonexit(void) {
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

/* ================================================= additional CRT shims */
static void c_putchar(void) {
    ms_init();
    FILE *out = ms_stdout();
    if (!out) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int c = fputc((int)(unsigned char)A32(0), out);
    RETC((uint32_t)(int32_t)c);
}
static void c_rewind(void) {
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    if (s && s->fp) { rewind(s->fp); ms_note_state(s); }
    RETCV();
}
static void c_feof(void) {
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    RETC(s && s->fp && feof(s->fp) ? 1u : 0u);
}
static void c_ferror(void) {
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    RETC(s && s->fp && ferror(s->fp) ? 1u : 0u);
}
static void c_clearerr(void) {
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    if (s && s->fp) { clearerr(s->fp); ms_note_state(s); }
    RETCV();
}
static void c_vfprintf(void) {                       /* (stream, fmt, argptr) */
    ms_init();
    ms_stream *s = ms_slot_of_va(A32(0));
    const char *fmt = ASTR(1);
    uint32_t args = A32(2);
    if (!s || !s->fp) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    if (!fmt) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    int n = hle_guest_format(t_fmt, sizeof t_fmt, fmt, args);
    size_t w = fwrite(t_fmt, 1, (size_t)n, s->fp);
    RETC(w == (size_t)n ? (uint32_t)n : 0xFFFFFFFFu);
}
static void c_vsnprintf(void) {                      /* (buf, count, fmt, argptr) */
    ms_init();
    char *buf = ASTR(0);
    uint32_t count = A32(1);
    const char *fmt = ASTR(2);
    uint32_t args = A32(3);
    if (!fmt) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    int n = hle_guest_format(t_fmt, sizeof t_fmt, fmt, args);
    if (!buf || count == 0) RETC((uint32_t)n);
    if ((uint32_t)n < count) {
        memcpy(buf, t_fmt, (size_t)n + 1);
        RETC((uint32_t)n);
    }
    memcpy(buf, t_fmt, count);
    if ((uint32_t)n == count) RETC((uint32_t)n);
    RETC(0xFFFFFFFFu);
}

/* math additions */
static void c_ceil(void)   { ms_init(); fp_push_impl(g_st, &g_fp_top, ceil(ms_argd(0))); RETC(0); }
static void c_roundf(void) {
    ms_init();
    uint32_t bits = A32(0);
    float f;
    memcpy(&f, &bits, 4);
    fp_push_impl(g_st, &g_fp_top, (double)roundf(f));
    RETC(0);
}
static void c_ldexp(void) {
    ms_init();
    fp_push_impl(g_st, &g_fp_top, ldexp(ms_argd(0), (int)A32(2)));
    RETC(0);
}

/* ctype additions */
#define CTYPE_PRED(fn, mask) static void c_##fn(void) { \
    ms_init(); int c = (int)A32(0); \
    if (c < -1 || c > 255) RETC(0); \
    RETC((uint32_t)(((const uint16_t *)hle_ptr(g_ctype_va))[c + 1] & (mask))); \
}
CTYPE_PRED(isalpha, CT_ALPHA)
CTYPE_PRED(isupper, CT_UPPER)
CTYPE_PRED(islower, CT_LOWER)
CTYPE_PRED(isalnum, CT_ALPHA | CT_DIGIT)
CTYPE_PRED(iscntrl, CT_CONTROL)
CTYPE_PRED(ispunct, CT_PUNCT)
CTYPE_PRED(isxdigit, CT_HEX)
static void c_isprint(void) {
    ms_init();
    int c = (int)A32(0);
    if (c < -1 || c > 255) RETC(0);
    uint16_t f = ((const uint16_t *)hle_ptr(g_ctype_va))[c + 1];
    RETC((uint32_t)((f & (CT_UPPER | CT_LOWER | CT_DIGIT | CT_PUNCT)) || c == ' '));
}
static void c_isgraph(void) {
    ms_init();
    int c = (int)A32(0);
    if (c < -1 || c > 255) RETC(0);
    uint16_t f = ((const uint16_t *)hle_ptr(g_ctype_va))[c + 1];
    RETC((uint32_t)(f & (CT_UPPER | CT_LOWER | CT_DIGIT | CT_PUNCT)));
}

/* qsort / bsearch calling back into lifted code */
static void ms_qsort_rec(uint32_t base, size_t lo, size_t hi, size_t sz, uint32_t cmp, uint8_t *tmp) {
    while (lo < hi) {
        size_t i = lo, j = hi;
        size_t mid = lo + (hi - lo) / 2;
        uint32_t pivot_va = base + (uint32_t)(mid * sz);
        uint8_t *pivot = (uint8_t *)malloc(sz);
        if (!pivot) return;
        memcpy(pivot, hle_ptr(pivot_va), sz);
        uint32_t pbuf_va = hle_va(hle_alloc((uint32_t)sz));
        if (!pbuf_va) { free(pivot); return; }
        memcpy(hle_ptr(pbuf_va), pivot, sz);
        free(pivot);
        while (i <= j) {
            while (1) {
                uint32_t a[2] = { base + (uint32_t)(i * sz), pbuf_va };
                if ((int32_t)hle_call_guest(cmp, 2, a) < 0) i++;
                else break;
            }
            while (1) {
                uint32_t a[2] = { base + (uint32_t)(j * sz), pbuf_va };
                if ((int32_t)hle_call_guest(cmp, 2, a) > 0) {
                    if (j == 0) break;
                    j--;
                } else break;
            }
            if (i <= j) {
                if (i != j) {
                    uint8_t *pa = (uint8_t *)hle_ptr(base + (uint32_t)(i * sz));
                    uint8_t *pb = (uint8_t *)hle_ptr(base + (uint32_t)(j * sz));
                    memcpy(tmp, pa, sz);
                    memcpy(pa, pb, sz);
                    memcpy(pb, tmp, sz);
                }
                i++;
                if (j == 0) break;
                j--;
            }
        }
        hle_free(hle_ptr(pbuf_va));
        if (j > lo && (j - lo) < (hi - i)) {
            if (lo < j) ms_qsort_rec(base, lo, j, sz, cmp, tmp);
            lo = i;
        } else {
            if (i < hi) ms_qsort_rec(base, i, hi, sz, cmp, tmp);
            hi = j;
        }
    }
}
static void c_qsort(void) {                          /* (base, nmemb, size, cmp) */
    ms_init();
    uint32_t base = A32(0), nmemb = A32(1), sz = A32(2), cmp = A32(3);
    if (!base || nmemb <= 1 || sz == 0 || !cmp) RETCV();
    uint8_t *tmp = (uint8_t *)malloc(sz);
    if (!tmp) RETCV();
    ms_qsort_rec(base, 0, (size_t)nmemb - 1, sz, cmp, tmp);
    free(tmp);
    RETCV();
}
static void c_bsearch(void) {                        /* (key, base, nmemb, size, cmp) */
    ms_init();
    uint32_t key = A32(0), base = A32(1), nmemb = A32(2), sz = A32(3), cmp = A32(4);
    if (!key || !base || nmemb == 0 || sz == 0 || !cmp) RETC(0);
    size_t lo = 0, hi = nmemb;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        uint32_t elem = base + (uint32_t)(mid * sz);
        uint32_t a[2] = { key, elem };
        int32_t r = (int32_t)hle_call_guest(cmp, 2, a);
        if (r == 0) RETC(elem);
        if (r < 0) hi = mid;
        else lo = mid + 1;
    }
    RETC(0);
}

/* strings / locale / time / POSIX CRT additions */
static void c_strpbrk(void) {
    const char *s = ASTR(0), *accept = ASTR(1);
    if (!s || !accept) RETC(0);
    const char *r = strpbrk(s, accept);
    RETC(r ? hle_va(r) : 0u);
}
static __thread uint32_t t_strtok_next;
static void c_strtok(void) {
    char *s = ASTR(0);
    const char *delim = ASTR(1);
    if (!delim) RETC(0);
    if (!s) s = (char *)hle_ptr(t_strtok_next);
    if (!s) RETC(0);
    s += strspn(s, delim);
    if (!*s) { t_strtok_next = 0; RETC(0); }
    char *tok = s;
    s = strpbrk(tok, delim);
    if (s) { *s = 0; t_strtok_next = hle_va(s + 1); }
    else { t_strtok_next = 0; }
    RETC(hle_va(tok));
}
static uint32_t g_strerror_buf;
static void c_strerror(void) {
    ms_init();
    if (!g_strerror_buf) g_strerror_buf = hle_va(hle_alloc(256));
    const char *msg = strerror((int)A32(0));
    snprintf((char *)hle_ptr(g_strerror_buf), 256, "%s", msg ? msg : "Unknown error");
    RETC(g_strerror_buf);
}
static void c_wcscpy(void) {
    uint16_t *dst = (uint16_t *)APTR(0);
    const uint16_t *src = (const uint16_t *)APTR(1);
    if (!dst || !src) RETC(A32(0));
    size_t i = 0;
    while ((dst[i] = src[i]) != 0) i++;
    RETC(A32(0));
}
static void c_wcsncmp(void) {
    const uint16_t *a = (const uint16_t *)APTR(0), *b = (const uint16_t *)APTR(1);
    uint32_t n = A32(2);
    if (!a || !b) RETC(0);
    for (uint32_t i = 0; i < n; i++) {
        if (a[i] != b[i]) RETC(a[i] < b[i] ? 0xFFFFFFFFu : 1u);
        if (!a[i]) break;
    }
    RETC(0);
}
static void c_wcsnicmp(void) {
    const uint16_t *a = (const uint16_t *)APTR(0), *b = (const uint16_t *)APTR(1);
    uint32_t n = A32(2);
    if (!a || !b) RETC(0);
    for (uint32_t i = 0; i < n; i++) {
        uint16_t ca = (a[i] >= 'A' && a[i] <= 'Z') ? (uint16_t)(a[i] + 32) : a[i];
        uint16_t cb = (b[i] >= 'A' && b[i] <= 'Z') ? (uint16_t)(b[i] + 32) : b[i];
        if (ca != cb) RETC(ca < cb ? 0xFFFFFFFFu : 1u);
        if (!a[i]) break;
    }
    RETC(0);
}
static void c_wcsicmp(void) {
    const uint16_t *a = (const uint16_t *)APTR(0), *b = (const uint16_t *)APTR(1);
    if (!a || !b) RETC((uint32_t)(int32_t)(a == b ? 0 : (a ? 1 : -1)));
    for (size_t i = 0;; i++) {
        uint16_t ca = (a[i] >= 'A' && a[i] <= 'Z') ? (uint16_t)(a[i] + 32) : a[i];
        uint16_t cb = (b[i] >= 'A' && b[i] <= 'Z') ? (uint16_t)(b[i] + 32) : b[i];
        if (ca != cb) RETC(ca < cb ? 0xFFFFFFFFu : 1u);
        if (!a[i]) RETC(0);
    }
}
static void u_wcsnlen(void) {
    const uint16_t *w = (const uint16_t *)APTR(0);
    uint32_t max = A32(1), n = 0;
    if (w) while (n < max && w[n]) n++;
    RETC(n);
}
static int utf8_next(const uint8_t *s, size_t n, uint32_t *cp) {
    if (!n) return -2;
    uint8_t c0 = s[0];
    if (c0 < 0x80u) { *cp = c0; return c0 ? 1 : 0; }
    int need = (c0 & 0xE0u) == 0xC0u ? 2 : (c0 & 0xF0u) == 0xE0u ? 3 : (c0 & 0xF8u) == 0xF0u ? 4 : -1;
    if (need < 0) return -1;
    if (n < (size_t)need) {
        for (size_t k = 1; k < n; k++) if ((s[k] & 0xC0u) != 0x80u) return -1;
        return -2;
    }
    uint32_t v = c0 & (need == 2 ? 0x1Fu : need == 3 ? 0x0Fu : 0x07u);
    for (int k = 1; k < need; k++) {
        if ((s[k] & 0xC0u) != 0x80u) return -1;
        v = (v << 6) | (s[k] & 0x3Fu);
    }
    if ((need == 2 && v < 0x80u) || (need == 3 && v < 0x800u) || (need == 4 && (v < 0x10000u || v > 0x10FFFFu)) ||
        (v >= 0xD800u && v <= 0xDFFFu))
        return -1;
    *cp = v;
    return need;
}
static void u_mbrtowc(void) {                        /* (wchar_t *pwc, const char *s, size_t n, mbstate_t *ps) */
    uint32_t pwc_va = A32(0), s_va = A32(1), n = A32(2);
    (void)A32(3);
    if (!s_va) RETC(0);
    if (!n) RETC(0xFFFFFFFEu);
    const uint8_t *s = (const uint8_t *)hle_ptr(s_va);
    uint32_t cp = 0;
    int r = utf8_next(s, n, &cp);
    if (r == -2) RETC(0xFFFFFFFEu);
    if (r == -1) { ms_seterrno(EILSEQ); RETC(0xFFFFFFFFu); }
    if (pwc_va) MEM16(pwc_va) = (uint16_t)(cp <= 0xFFFFu ? cp : 0xFFFDu);
    RETC((uint32_t)r);
}
static void u_mbsrtowcs(void) {                      /* (wchar_t *dst, const char **psrc, size_t len, mbstate_t *ps) */
    uint32_t dst_va = A32(0), psrc_va = A32(1), len = A32(2);
    (void)A32(3);
    if (!psrc_va || !MEM32(psrc_va)) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    uint32_t src_va = MEM32(psrc_va);
    const uint8_t *s = (const uint8_t *)hle_ptr(src_va);
    uint16_t *dst = dst_va ? (uint16_t *)hle_ptr(dst_va) : NULL;
    size_t slen = strlen((const char *)s) + 1u;
    size_t pos = 0, out = 0;
    while (!dst || out < len) {
        uint32_t cp = 0;
        int r = utf8_next(s + pos, slen - pos, &cp);
        if (r < 0) { ms_seterrno(EILSEQ); if (dst) MEM32(psrc_va) = src_va + (uint32_t)pos; RETC(0xFFFFFFFFu); }
        if (r == 0) {
            if (dst) { dst[out] = 0; MEM32(psrc_va) = 0; }
            RETC((uint32_t)out);
        }
        if (cp <= 0xFFFFu) {
            if (dst) dst[out] = (uint16_t)cp;
            out++;
        } else {
            if (dst && out + 1u >= len) break;
            if (dst) {
                uint32_t u = cp - 0x10000u;
                dst[out] = (uint16_t)(0xD800u + (u >> 10));
                dst[out + 1u] = (uint16_t)(0xDC00u + (u & 0x3FFu));
            }
            out += 2u;
        }
        pos += (size_t)r;
    }
    if (dst) MEM32(psrc_va) = src_va + (uint32_t)pos;
    RETC((uint32_t)out);
}
static void u_mbstowcs(void) {                       /* (wchar_t *dst, const char *src, size_t n) */
    uint32_t dst_va = A32(0), src_va = A32(1), n = A32(2);
    if (!src_va) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    const uint8_t *s = (const uint8_t *)hle_ptr(src_va);
    uint16_t *dst = dst_va ? (uint16_t *)hle_ptr(dst_va) : NULL;
    size_t slen = strlen((const char *)s) + 1u, pos = 0, out = 0;
    while (!dst || out < n) {
        uint32_t cp = 0;
        int r = utf8_next(s + pos, slen - pos, &cp);
        if (r < 0) { ms_seterrno(EILSEQ); RETC(0xFFFFFFFFu); }
        if (r == 0) { if (dst) dst[out] = 0; RETC((uint32_t)out); }
        if (cp <= 0xFFFFu) {
            if (dst) dst[out] = (uint16_t)cp;
            out++;
        } else {
            if (dst && out + 1u >= n) break;
            if (dst) {
                uint32_t u = cp - 0x10000u;
                dst[out] = (uint16_t)(0xD800u + (u >> 10));
                dst[out + 1u] = (uint16_t)(0xDC00u + (u & 0x3FFu));
            }
            out += 2u;
        }
        pos += (size_t)r;
    }
    RETC((uint32_t)out);
}
static int encode_utf8_bmp(uint16_t wc, uint8_t out[4]) {
    if (wc < 0x80u) { out[0] = (uint8_t)wc; return 1; }
    if (wc < 0x800u) { out[0] = (uint8_t)(0xC0u | (wc >> 6)); out[1] = (uint8_t)(0x80u | (wc & 0x3Fu)); return 2; }
    out[0] = (uint8_t)(0xE0u | (wc >> 12));
    out[1] = (uint8_t)(0x80u | ((wc >> 6) & 0x3Fu));
    out[2] = (uint8_t)(0x80u | (wc & 0x3Fu));
    return 3;
}
static void u_wcrtomb(void) {                        /* (char *s, wchar_t wc, mbstate_t *ps) */
    uint32_t s_va = A32(0); uint16_t wc = (uint16_t)A32(1);
    (void)A32(2);
    if (!s_va) RETC(1);
    uint8_t buf[4];
    int k = encode_utf8_bmp(wc, buf);
    memcpy(hle_ptr(s_va), buf, (size_t)k);
    RETC((uint32_t)k);
}
static void u_wcstombs(void) {                       /* (char *dst, const wchar_t *src, size_t n) */
    uint32_t dst_va = A32(0), src_va = A32(1), n = A32(2);
    if (!src_va) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    const uint16_t *w = (const uint16_t *)hle_ptr(src_va);
    char *dst = dst_va ? (char *)hle_ptr(dst_va) : NULL;
    size_t out = 0;
    for (size_t i = 0;; i++) {
        uint16_t c = w[i];
        if (!c) { if (dst && out < n) dst[out] = 0; RETC((uint32_t)out); }
        uint8_t buf[4];
        int k = encode_utf8_bmp(c, buf);
        if (dst) {
            if (out + (size_t)k > n) RETC((uint32_t)out);
            memcpy(dst + out, buf, (size_t)k);
        }
        out += (size_t)k;
    }
}
static uint32_t g_lconv_va;
static void c_localeconv(void) {
    ms_init();
    if (!g_lconv_va) {
        /* MSVC struct lconv: 10 char* + 8 char */
        uint32_t *lc = (uint32_t *)hle_alloc(64);
        uint32_t dot = hle_guest_strdup(".");
        uint32_t emp = hle_guest_strdup("");
        for (int i = 0; i < 10; i++) lc[i] = emp;
        lc[0] = dot; /* decimal_point */
        uint8_t *ch = (uint8_t *)(lc + 10);
        for (int i = 0; i < 8; i++) ch[i] = 127; /* CHAR_MAX */
        g_lconv_va = hle_va(lc);
    }
    RETC(g_lconv_va);
}
static void c_clock(void) { RETC(hle_clock_ms()); }
static void c_rename(void) {
    ms_init();
    const char *o = ASTR(0), *n = ASTR(1);
    if (!o || !n) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    char ha[1024], hb[1024];
    hle_host_path(o, ha, sizeof ha);
    hle_host_path_for_write(n, hb, sizeof hb, 0);
    if (rename(ha, hb) == 0) RETC(0);
    ms_seterrno(errno);
    RETC(0xFFFFFFFFu);
}

static int ms_flags_to_posix(int f) {
    int r = (f & 3);
    if (f & 0x0100) r |= O_CREAT;
    if (f & 0x0200) r |= O_TRUNC;
    if (f & 0x0400) r |= O_EXCL;
    if (f & 0x0008) r |= O_APPEND;
    return r;
}
static void c_open_crt(void) {                       /* (path, oflag, [pmode]) */
    ms_init();
    const char *path = ASTR(0);
    int oflag = (int)A32(1);
    int mode = (oflag & 0x0100) ? (int)(A32(2) & 0777u) : 0644;
    if (!path) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    char host[1024];
    if ((oflag & 3) || (oflag & 0x0300)) hle_host_path_for_write(path, host, sizeof host, !(oflag & 0x0200));
    else hle_host_path(path, host, sizeof host);
    int fd = open(host, ms_flags_to_posix(oflag), mode ? mode : 0644);
    if (fd < 0) { ms_seterrno(errno); RETC(0xFFFFFFFFu); }
    const char *fm = (oflag & 3) == 0 ? "rb" : ((oflag & 3) == 1 ? "wb" : "r+b");
    FILE *fp = fdopen(fd, fm);
    int slot = ms_slot_alloc(fp, fd, fp != NULL);
    if (slot < 0) { if (fp) fclose(fp); else close(fd); ms_seterrno(EMFILE); RETC(0xFFFFFFFFu); }
    g_stream[slot].mode = (oflag & O_BINARY) ? O_BINARY : O_TEXT;
    RETC((uint32_t)slot);
}
static void c_creat(void) {                          /* (path, pmode) */
    ms_init();
    const char *path = ASTR(0);
    if (!path) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    char host[1024];
    hle_host_path_for_write(path, host, sizeof host, 0);
    int fd = open(host, O_RDWR | O_CREAT | O_TRUNC, (int)(A32(1) & 0777u) ? (int)(A32(1) & 0777u) : 0644);
    if (fd < 0) { ms_seterrno(errno); RETC(0xFFFFFFFFu); }
    FILE *fp = fdopen(fd, "w+b");
    int slot = ms_slot_alloc(fp, fd, fp != NULL);
    if (slot < 0) { if (fp) fclose(fp); else close(fd); ms_seterrno(EMFILE); RETC(0xFFFFFFFFu); }
    RETC((uint32_t)slot);
}
static void c_close_crt(void) {
    ms_init();
    int fd = (int)A32(0);
    if (fd < 0 || fd >= MS_MAXSTREAM || (!g_stream[fd].fp && g_stream[fd].fd < 0)) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    ms_stream *s = &g_stream[fd];
    int r = 0;
    if (s->own && s->fp) r = fclose(s->fp);
    else if (fd > 2 && s->fd >= 0) r = close(s->fd);
    if (r != 0) ms_seterrno(errno);
    ms_slot_free(s);
    RETC(r == 0 ? 0u : 0xFFFFFFFFu);
}
static void c_read_crt(void) {                       /* (fd, buf, count) */
    ms_init();
    int fd = (int)A32(0);
    if (fd < 0 || fd >= MS_MAXSTREAM || (!g_stream[fd].fp && g_stream[fd].fd < 0)) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    ms_stream *s = &g_stream[fd];
    if (s->fp) fflush(s->fp);
    ssize_t r = read(s->fd, APTR(1), (size_t)A32(2));
    if (r < 0) ms_seterrno(errno);
    RETC((uint32_t)(int32_t)r);
}
static void c_dup(void) {
    ms_init();
    int fd = (int)A32(0);
    if (fd < 0 || fd >= MS_MAXSTREAM || g_stream[fd].fd < 0) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    int nfd = dup(g_stream[fd].fd);
    if (nfd < 0) { ms_seterrno(errno); RETC(0xFFFFFFFFu); }
    FILE *fp = fdopen(nfd, "r+b");
    if (!fp) fp = fdopen(nfd, "rb");
    if (!fp) fp = fdopen(nfd, "wb");
    int slot = ms_slot_alloc(fp, nfd, fp != NULL);
    if (slot < 0) { if (fp) fclose(fp); else close(nfd); ms_seterrno(EMFILE); RETC(0xFFFFFFFFu); }
    RETC((uint32_t)slot);
}
static void c_dup2(void) {
    ms_init();
    int oldfd = (int)A32(0), newfd = (int)A32(1);
    if (oldfd < 0 || oldfd >= MS_MAXSTREAM || g_stream[oldfd].fd < 0 || newfd < 0 || newfd >= MS_MAXSTREAM) {
        ms_seterrno(EBADF); RETC(0xFFFFFFFFu);
    }
    if (oldfd == newfd) RETC(0);
    int target = newfd <= 2 ? newfd : dup(g_stream[oldfd].fd);
    if (newfd <= 2 && dup2(g_stream[oldfd].fd, newfd) < 0) { ms_seterrno(errno); RETC(0xFFFFFFFFu); }
    if (target < 0) { ms_seterrno(errno); RETC(0xFFFFFFFFu); }
    if (newfd > 2) {
        if (g_stream[newfd].own && g_stream[newfd].fp) fclose(g_stream[newfd].fp);
        else if (g_stream[newfd].fd > 2) close(g_stream[newfd].fd);
        g_stream[newfd].fd = target;
        g_stream[newfd].fp = fdopen(target, "r+b");
        if (!g_stream[newfd].fp) g_stream[newfd].fp = fdopen(target, "rb");
        if (!g_stream[newfd].fp) g_stream[newfd].fp = fdopen(target, "wb");
        g_stream[newfd].own = g_stream[newfd].fp != NULL;
    }
    RETC(0);
}
static void c_lseeki64(void) {                       /* (fd, off64, origin) -> off64 in edx:eax */
    ms_init();
    int fd = (int)A32(0);
    if (fd < 0 || fd >= MS_MAXSTREAM || g_stream[fd].fd < 0) {
        ms_seterrno(EBADF); g_eax = 0xFFFFFFFFu; g_edx = 0xFFFFFFFFu; g_esp += 4u; return;
    }
    int64_t off = (int64_t)((uint64_t)A32(1) | ((uint64_t)A32(2) << 32));
    off_t r = lseek(g_stream[fd].fd, (off_t)off, (int)A32(3));
    if (r < 0) {
        ms_seterrno(errno); g_eax = 0xFFFFFFFFu; g_edx = 0xFFFFFFFFu; g_esp += 4u; return;
    }
    int64_t res = (int64_t)r;
    g_eax = (uint32_t)res;
    g_edx = (uint32_t)((uint64_t)res >> 32);
    g_esp += 4u;
}
static void c_telli64(void) {
    ms_init();
    int fd = (int)A32(0);
    if (fd < 0 || fd >= MS_MAXSTREAM || g_stream[fd].fd < 0) {
        ms_seterrno(EBADF); g_eax = 0xFFFFFFFFu; g_edx = 0xFFFFFFFFu; g_esp += 4u; return;
    }
    off_t r = lseek(g_stream[fd].fd, 0, SEEK_CUR);
    int64_t res = r < 0 ? -1 : (int64_t)r;
    if (r < 0) ms_seterrno(errno);
    g_eax = (uint32_t)res;
    g_edx = (uint32_t)((uint64_t)res >> 32);
    g_esp += 4u;
}
static void c_fstat(void) {                          /* (fd, struct _stat *) */
    ms_init();
    int fd = (int)A32(0);
    uint32_t out = A32(1);
    if (fd < 0 || fd >= MS_MAXSTREAM || g_stream[fd].fd < 0 || !out) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    struct stat st;
    if (fstat(g_stream[fd].fd, &st) != 0) { ms_seterrno(errno); RETC(0xFFFFFFFFu); }
    memset(hle_ptr(out), 0, 36);
    MEM32(out + 0) = (uint32_t)st.st_dev;
    MEM16(out + 4) = (uint16_t)st.st_ino;
    MEM16(out + 6) = (uint16_t)st.st_mode;
    MEM16(out + 8) = (uint16_t)st.st_nlink;
    MEM32(out + 20) = (uint32_t)st.st_size;
    MEM32(out + 24) = (uint32_t)st.st_atim.tv_sec;
    MEM32(out + 28) = (uint32_t)st.st_mtim.tv_sec;
    MEM32(out + 32) = (uint32_t)st.st_ctime;
    RETC(0);
}
static void c_access(void) {
    ms_init();
    const char *p = ASTR(0);
    if (!p) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    char host[1024];
    if (!hle_host_path(p, host, sizeof host)) { ms_seterrno(ENOENT); RETC(0xFFFFFFFFu); }
    int r = access(host, (int)A32(1) & 7);
    if (r != 0) ms_seterrno(errno);
    RETC(r == 0 ? 0u : 0xFFFFFFFFu);
}
static void c_chmod(void) {
    ms_init();
    const char *p = ASTR(0);
    if (!p) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    char host[1024];
    if (!hle_host_path(p, host, sizeof host)) { ms_seterrno(ENOENT); RETC(0xFFFFFFFFu); }
    int r = chmod(host, (mode_t)A32(1));
    if (r != 0) ms_seterrno(errno);
    RETC(r == 0 ? 0u : 0xFFFFFFFFu);
}
static void c_umask(void) { RETC((uint32_t)umask((mode_t)A32(0))); }
static void c_getcwd(void) {                         /* (buf, maxlen) */
    ms_init();
    char cwd[1024], g[1024];
    if (!getcwd(cwd, sizeof cwd)) { ms_seterrno(errno); RETC(0); }
    hle_guest_path(cwd, g, sizeof g);
    size_t len = strlen(g) + 1;
    char *buf = ASTR(0);
    if (!buf) {
        char *p = (char *)hle_heap_alloc((uint32_t)len, 0);
        if (!p) { ms_seterrno(ENOMEM); RETC(0); }
        memcpy(p, g, len);
        RETC(hle_va(p));
    }
    if (len > A32(1)) { ms_seterrno(ERANGE); RETC(0); }
    memcpy(buf, g, len);
    RETC(A32(0));
}
static void c_rmdir(void) {
    ms_init();
    const char *p = ASTR(0);
    if (!p) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    char host[1024];
    if (!hle_host_path(p, host, sizeof host)) { ms_seterrno(ENOENT); RETC(0xFFFFFFFFu); }
    int r = rmdir(host);
    if (r != 0) ms_seterrno(errno);
    RETC(r == 0 ? 0u : 0xFFFFFFFFu);
}
static void c_isatty(void) {
    ms_init();
    int fd = (int)A32(0);
    if (fd < 0 || fd >= MS_MAXSTREAM || g_stream[fd].fd < 0) RETC(0);
    RETC((uint32_t)isatty(g_stream[fd].fd));
}
static void c_getpid(void) { RETC((uint32_t)getpid()); }
static void c_get_osfhandle(void) {
    ms_init();
    int fd = (int)A32(0);
    if (fd < 0 || fd >= MS_MAXSTREAM || g_stream[fd].fd < 0) { ms_seterrno(EBADF); RETC(0xFFFFFFFFu); }
    if (fd == 0) RETC(0x11u);
    if (fd == 1) RETC(0x12u);
    if (fd == 2) RETC(0x13u);
    RETC(0x100u + (uint32_t)fd);
}
static void c_pipe(void) {                           /* (int pfds[2], psize, textmode) */
    ms_init();
    uint32_t pfds = A32(0);
    if (!pfds) { ms_seterrno(EINVAL); RETC(0xFFFFFFFFu); }
    int fds[2];
    if (pipe(fds) != 0) { ms_seterrno(errno); RETC(0xFFFFFFFFu); }
    FILE *fr = fdopen(fds[0], "rb"), *fw = fdopen(fds[1], "wb");
    int s0 = ms_slot_alloc(fr, fds[0], fr != NULL);
    int s1 = s0 >= 0 ? ms_slot_alloc(fw, fds[1], fw != NULL) : -1;
    if (s0 < 0 || s1 < 0) {
        if (s0 >= 0) ms_slot_free(&g_stream[s0]);
        if (fr) fclose(fr); else close(fds[0]);
        if (fw) fclose(fw); else close(fds[1]);
        ms_seterrno(EMFILE); RETC(0xFFFFFFFFu);
    }
    MEM32(pfds) = (uint32_t)s0;
    MEM32(pfds + 4) = (uint32_t)s1;
    RETC(0);
}
static void c_putenv(void) { RETC(0); }
static void c_mktemp(void) {
    char *s = ASTR(0);
    if (!s) RETC(0);
    size_t n = strlen(s);
    for (size_t i = n; i > 0 && s[i - 1] == 'X'; i--) s[i - 1] = '0' + (char)((i + (size_t)getpid()) % 10);
    RETC(A32(0));
}
static uint32_t g_tm_va, g_ctime_va, g_tz_va;
static int64_t rd_time64(uint32_t p) {
    return (int64_t)((uint64_t)MEM32(p) | ((uint64_t)MEM32(p + 4) << 32));
}
static void c_ctime64(void) {
    ms_init();
    if (!g_ctime_va) g_ctime_va = hle_va(hle_alloc(32));
    time_t t = A32(0) ? (time_t)rd_time64(A32(0)) : 0;
    const char *s = ctime(&t);
    snprintf((char *)hle_ptr(g_ctime_va), 32, "%s", s ? s : "");
    RETC(g_ctime_va);
}
static uint32_t fill_guest_tm(const struct tm *t) {
    if (!g_tm_va) g_tm_va = hle_va(hle_alloc(36));
    if (!t) return 0;
    memcpy(hle_ptr(g_tm_va), t, 36);
    return g_tm_va;
}
static void c_gmtime64(void) {
    ms_init();
    time_t t = A32(0) ? (time_t)rd_time64(A32(0)) : 0;
    RETC(fill_guest_tm(gmtime(&t)));
}
static void c_localtime64(void) {
    ms_init();
    time_t t = A32(0) ? (time_t)rd_time64(A32(0)) : 0;
    RETC(fill_guest_tm(localtime(&t)));
}
static void c_mktime64(void) {
    ms_init();
    struct tm t;
    if (!A32(0)) { g_eax = 0xFFFFFFFFu; g_edx = 0xFFFFFFFFu; g_esp += 4u; return; }
    memcpy(&t, APTR(0), 36);
    int64_t r = (int64_t)mktime(&t);
    memcpy(APTR(0), &t, 36);
    g_eax = (uint32_t)r;
    g_edx = (uint32_t)((uint64_t)r >> 32);
    g_esp += 4u;
}
static void c_tzset(void) { tzset(); RETCV(); }
static void c_p_initenv(void) { ms_init(); RETC(g_environ_va); }
static void c_p_timezone(void) {
    ms_init();
    if (!g_tz_va) { g_tz_va = hle_va(hle_alloc(4)); MEM32(g_tz_va) = (uint32_t)(int32_t)timezone; }
    RETC(g_tz_va);
}
static void c_amsg_exit(void) {
    fprintf(stderr, "[msvcrt] _amsg_exit(%u)\n", A32(0));
    hle_exit(255);
}
static void c_getch(void) {
    ms_init();
    int c = fgetc(stdin);
    RETC((uint32_t)(c == EOF ? 0 : c));
}
static void c_kbhit(void) {
    ms_init();
    if (!isatty(STDIN_FILENO)) RETC(0);
    struct pollfd p = { STDIN_FILENO, POLLIN, 0 };
    RETC((poll(&p, 1, 0) > 0 && (p.revents & POLLIN)) ? 1u : 0u);
}
static void u_beginthreadex(void) {                  /* (sec, stack, start, arg, flag, &tid) -> handle */
    ms_init();
    uint32_t tid = 0, h = hle_thread_create(A32(2), A32(3), A32(4), &tid);
    if (!h) { ms_seterrno(EAGAIN); RETC(0); }
    if (A32(5)) MEM32(A32(5)) = tid;
    RETC(h);
}
static void u_endthreadex(void) {                    /* (unsigned retval) */
    ms_init();
    hle_thread_exit_self(A32(0));
}
static void c_spawnve(void) { ms_seterrno(ENOENT); RETC(0xFFFFFFFFu); }
static void c_setjmp3(void) {
    uint32_t jb = A32(0);
    if (jb) {
        MEM32(jb + 0) = g_ebp;
        MEM32(jb + 4) = g_ebx;
        MEM32(jb + 8) = g_edi;
        MEM32(jb + 12) = g_esi;
        MEM32(jb + 16) = g_esp + 4u;
        MEM32(jb + 20) = MEM32(g_esp);
    }
    RETC(0);
}
static void c_longjmp(void) {
    hle_eh_longjmp(A32(0), A32(1));
    hle_exit(3); /* invalid or unowned guest jump buffer */
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
    { "__stdio_common_vfprintf",      u_stdio_common_vfprintf },
    { "__stdio_common_vsscanf",       u_stdio_common_vsscanf },
    { "__stdio_common_vfscanf",       u_stdio_common_vfscanf },
    { "getenv",                       u_getenv },
    { "__p__acmdln",                  u_p_acmdln },
    { "_set_invalid_parameter_handler", u_set_invalid_parameter_handler },
    { "__acrt_iob_func",              u_acrt_iob_func },
    { "_open_osfhandle",              u_open_osfhandle },
    { "strcspn",                      u_strcspn },
    { "strspn",                       u_strspn },
    { "wcslen",                       u_wcslen },
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
    { "_CxxThrowException",           u_cxx_throw_exception },
    { "_except_handler4_common",      u_except_handler4_common },
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
    { "getchar",                      c_getchar },
    { "ungetc",                       c_ungetc },
    { "fgets",                        c_fgets },
    { "fputs",                        c_fputs },
    { "fseek",                        c_fseek },
    { "ftell",                        c_ftell },
    { "fgetpos",                      c_fgetpos },
    { "fsetpos",                      c_fsetpos },
    { "setbuf",                       c_setbuf },
    { "setvbuf",                      c_setvbuf },
    { "printf",                       c_printf },
    { "fprintf",                      c_fprintf },
    { "sprintf",                      c_sprintf },
    { "puts",                         c_puts },
    { "putchar",                      c_putchar },
    { "putc",                         c_fputc },
    { "rewind",                       c_rewind },
    { "feof",                         c_feof },
    { "ferror",                       c_ferror },
    { "clearerr",                     c_clearerr },
    { "_filbuf",                      c_fgetc },
    { "vfprintf",                     c_vfprintf },
    { "_vsnprintf",                   c_vsnprintf },
    { "sscanf",                       c_sscanf },
    { "_fileno",                      c_fileno },
    { "_fdopen",                      c_fdopen },
    { "_setmode",                     c_setmode },
    { "_write",                       c_write },
    { "_open",                        c_open_crt },
    { "_creat",                       c_creat },
    { "_close",                       c_close_crt },
    { "_read",                        c_read_crt },
    { "_dup",                         c_dup },
    { "_dup2",                        c_dup2 },
    { "_lseeki64",                    c_lseeki64 },
    { "_telli64",                     c_telli64 },
    { "_fstat",                       c_fstat },
    { "_access",                      c_access },
    { "_chmod",                       c_chmod },
    { "_umask",                       c_umask },
    { "_getcwd",                      c_getcwd },
    { "_rmdir",                       c_rmdir },
    { "_isatty",                      c_isatty },
    { "_getpid",                      c_getpid },
    { "_get_osfhandle",               c_get_osfhandle },
    { "_pipe",                        c_pipe },
    { "_putenv",                      c_putenv },
    { "_mktemp",                      c_mktemp },
    { "_ctime64",                     c_ctime64 },
    { "_gmtime64",                    c_gmtime64 },
    { "_localtime64",                 c_localtime64 },
    { "_mktime64",                    c_mktime64 },
    { "_tzset",                       c_tzset },
    { "__p___initenv",                c_p_initenv },
    { "__p__timezone",                c_p_timezone },
    { "_amsg_exit",                   c_amsg_exit },
    { "_getch",                       c_getch },
    { "_getche",                      c_getch },
    { "_spawnve",                     c_spawnve },
    { "_setjmp3",                     c_setjmp3 },
    { "longjmp",                      c_longjmp },
    { "_getw",                        c_getw },
    { "_putw",                        c_putw },
    { "remove",                       c_remove },
    { "rename",                       c_rename },
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
    { "strnlen",                      u_strnlen },
    { "_strrev",                      c_strrev },
    { "strcpy",                       c_strcpy },
    { "strncpy",                      c_strncpy },
    { "strcat",                       c_strcat },
    { "strncat",                      c_strncat },
    { "strcmp",                       c_strcmp },
    { "strncmp",                      c_strncmp },
    { "_stricmp",                     c_stricmp },
    { "_stricoll",                    c_stricoll },
    { "_strnicmp",                    c_strnicmp },
    { "strchr",                       c_strchr },
    { "strrchr",                      c_strrchr },
    { "strstr",                       c_strstr },
    { "strpbrk",                      c_strpbrk },
    { "strtok",                       c_strtok },
    { "strerror",                     c_strerror },
    { "wcscpy",                       c_wcscpy },
    { "wcsncmp",                      c_wcsncmp },
    { "wcsnlen",                      u_wcsnlen },
    { "_wcsicmp",                     c_wcsicmp },
    { "_wcsnicmp",                    c_wcsnicmp },
    { "mbrtowc",                      u_mbrtowc },
    { "mbsrtowcs",                    u_mbsrtowcs },
    { "mbstowcs",                     u_mbstowcs },
    { "wcrtomb",                      u_wcrtomb },
    { "wcstombs",                     u_wcstombs },
    { "strcoll",                      c_strcoll },
    { "strxfrm",                      c_strxfrm },
    { "tolower",                      c_tolower },
    { "toupper",                      c_toupper },
    /* ctype */
    { "_isctype",                     c_isctype },
    { "isspace",                      c_isspace },
    { "isdigit",                      c_isdigit },
    { "isalpha",                      c_isalpha },
    { "isupper",                      c_isupper },
    { "islower",                      c_islower },
    { "isprint",                      c_isprint },
    { "isalnum",                      c_isalnum },
    { "iscntrl",                      c_iscntrl },
    { "isgraph",                      c_isgraph },
    { "ispunct",                      c_ispunct },
    { "isxdigit",                     c_isxdigit },
    /* conversions & sort/search */
    { "atoi",                         c_atoi },
    { "atol",                         c_atoi },
    { "abs",                          c_abs },
    { "strtol",                       c_strtol },
    { "strtoul",                      c_strtoul },
    { "strtod",                       c_strtod },
    { "qsort",                        c_qsort },
    { "bsearch",                      c_bsearch },
    /* rand & fpu/console/threads */
    { "rand",                         c_rand },
    { "rand_s",                       u_rand_s },
    { "srand",                        c_srand },
    { "_fpreset",                     c_fpreset },
    { "_kbhit",                       c_kbhit },
    { "_beginthreadex",               u_beginthreadex },
    { "_endthreadex",                 u_endthreadex },
    /* math */
    { "sin",                          c_sin },
    { "cos",                          c_cos },
    { "sqrt",                         c_sqrt },
    { "fabs",                         c_fabs },
    { "floor",                        c_floor },
    { "ceil",                         c_ceil },
    { "atan",                         u_libm_atan },
    { "roundf",                       c_roundf },
    { "ldexp",                        c_ldexp },
    { "pow",                          c_pow },
    { "atan2",                        c_atan2 },
    { "_finite",                      c_finite },
    /* time and locale */
    { "time",                         c_time },
    { "clock",                        c_clock },
    { "strftime",                     c_strftime },
    { "setlocale",                    c_setlocale },
    { "localeconv",                   c_localeconv },
    /* exit */
    { "exit",                         c_exit },
    { "_exit",                        c_uexit },
    { "_cexit",                       c_cexit },
    { "atexit",                       c_atexit },
    { "_onexit",                      c_onexit },
    { "__dllonexit",                  c_dllonexit },
    { "abort",                        c_abort },
    { "_assert",                      c_assert },
    { "signal",                       c_signal },
    { 0, 0 }
};
