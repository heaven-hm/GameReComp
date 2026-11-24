/*
 * win64_shims.c - bounded Win64 UCRT/KERNEL32 import shims for native ARM64
 * recompilations of real PE32+ console executables.
 *
 * generate64.py leaves every import open: the loader writes HLE_SENTINEL(i)
 * into each IAT slot and cpu64.c routes a call through it to hle_invoke(c,i).
 * This file is a runner-supplied strong hle_invoke for the handful of imports
 * the small MSVC console programs in the corpus use. It is NOT a general
 * Windows emulation layer: it implements the observed surface, and reports
 * (rather than guesses at) anything outside it.
 *
 * Guest addresses are host addresses here. The PE is mapped at its preferred
 * 0x140000000 base on Apple Silicon, so g_image_delta is 0 and GVA(x)==x;
 * values handed to us in registers are already GVA-adjusted by generated code.
 *
 * Win64 ABI recap used below: args 0..3 in rcx/rdx/r8/r9, a 32-byte shadow
 * space, varargs from arg 5 at [rsp+40] when c->rsp points at the return
 * address (the state hle_invoke is called in for a `call` import).
 */
#include "cpu64.h"
#include "recomp_dispatch.h"

#include <ctype.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define ARG0(c) ((c)->rcx)
#define ARG1(c) ((c)->rdx)
#define ARG2(c) ((c)->r8)
#define ARG3(c) ((c)->r9)

static int g_trace = -1;
static int trace_on(void) {
    if (g_trace < 0) g_trace = getenv("X64_SHIM_TRACE") != NULL;
    return g_trace;
}

/* ---- the FILE objects __acrt_iob_func hands out -------------------------- */
/* A plausible _iobuf (48 bytes on Win64); only .file is read by the shims. */
typedef struct {
    uint64_t _ptr;
    int32_t  _cnt;
    uint64_t _base;
    int32_t  _flag;
    int32_t  _file;
    int32_t  _charbuf;
    int32_t  _bufsiz;
    uint64_t _tmpfname;
} guest_iobuf;

static guest_iobuf g_iob[3];
static FILE *g_host_stream[3];

static void iob_init(void) {
    static int done;
    if (done) return;
    done = 1;
    g_host_stream[0] = stdin;   /* not a constant initializer on macOS */
    g_host_stream[1] = stdout;
    g_host_stream[2] = stderr;
    memset(g_iob, 0, sizeof g_iob);
    g_iob[0]._file = 0; g_iob[0]._flag = 0x01;   /* _FREAD */
    g_iob[1]._file = 1; g_iob[1]._flag = 0x02;   /* _FWRITE */
    g_iob[2]._file = 2; g_iob[2]._flag = 0x02;
}

static int stream_index(uint64_t p) {
    iob_init();
    for (int i = 0; i < 3; i++)
        if (p == (uint64_t)(uintptr_t)&g_iob[i]) return i;
    return -1;
}

static FILE *stream_of(uint64_t p) {
    int i = stream_index(p);
    return i < 0 ? stdout : g_host_stream[i];
}

/* Windows CRT console streams are text mode: every '\n' the guest formats is
 * written as CRLF. Host stdout is binary, so translate here. Bytes the guest
 * itself wrote as CRLF pass through unchanged (no doubled '\r'). */
static void out_write(FILE *f, const char *s, size_t n) {
    for (size_t i = 0; i < n; i++) {
        if (s[i] == '\n' && (i == 0 || s[i - 1] != '\r')) fputc('\r', f);
        fputc(s[i], f);
    }
}

/* ---- guest memory helpers ------------------------------------------------ */
static void guest_str(uint64_t va, char *out, size_t cap) {
    size_t n = 0;
    if (!va) { out[0] = 0; return; }
    while (n + 1 < cap) {
        char ch = (char)rd8(va + n);
        out[n++] = ch;
        if (!ch) break;
    }
    out[n] = 0;
}

/* The 5th argument to __stdio_common_vfprintf/vfscanf is a Win64 va_list:
 * a POINTER into the caller's stack-argument area, not the first vararg.
 * The generated CRT wrapper builds it with `lea rax,[rsp+0x58]` and forwards
 * that pointer at [rsp+0x20] before the call. */
static uint64_t va_list_ptr(CPU *c, int tail) {
    return rd64(c->rsp + (uint64_t)(tail ? 32 : 40));
}
static uint64_t vararg(uint64_t va, int i) {
    return rd64(va + 8 * (uint64_t)i);
}

/* Rewrite an MSVC conversion specifier into one host printf/scanf understands.
 * Win64 `long` is 32-bit; macOS LP64 `long` is 64-bit. MSVC's %I64d is %lld. */
static void host_spec(const char *fmt, const char *conv, char *out, size_t cap) {
    const char *p = fmt;
    size_t n = 0;
    int have_l = 0;
    while (p < conv && n + 2 < cap) {
        if (p + 2 < conv && p[0] == 'I' && p[1] == '6' && p + 2 < conv && p[2] == '4') {
            out[n++] = 'l'; out[n++] = 'l'; p += 3; continue;
        }
        if (*p == 'l') {
            if (p + 1 < conv && p[1] == 'l') { out[n++] = 'l'; out[n++] = 'l'; p += 2; continue; }
            have_l = 1; p++; continue;              /* guest long == int */
        }
        out[n++] = *p++;
    }
    (void)have_l;
    if (*conv && n + 2 < cap) out[n++] = *conv;   /* the conversion letter only */
    out[n] = 0;
}

/* ---- __stdio_common_vfprintf -------------------------------------------- */
/* unsigned __int64 options, FILE *stream, const char *format, locale, ...  */
static int vfmt(CPU *c, FILE *out, int tail) {
    uint64_t fmt_va = ARG2(c);
    char fmt[2048];
    guest_str(fmt_va, fmt, sizeof fmt);
    uint64_t va = va_list_ptr(c, tail);
    if (trace_on()) {
        fprintf(stderr, "[shim] vfprintf opts=%llx stream=%llx fmt=%s va=%llx",
                (unsigned long long)ARG0(c), (unsigned long long)ARG1(c), fmt,
                (unsigned long long)va);
        for (int k = 0; k < 4; k++) fprintf(stderr, " a%d=%llx", k,
                (unsigned long long)vararg(va, k));
        fprintf(stderr, "\n");
    }
    int ai = 0, wrote = 0;
    /* Format into a host buffer, then emit through the CRLF translator. */
#define EMIT(...) do {                                                      \
        char _b[1024];                                                      \
        int _n = snprintf(_b, sizeof _b, __VA_ARGS__);                      \
        if (_n > 0) { out_write(out, _b, (size_t)_n); wrote += _n; }         \
    } while (0)
    const char *p = fmt;
    while (*p) {
        if (*p != '%') { char b[1] = { *p++ }; out_write(out, b, 1); wrote++; continue; }
        if (p[1] == '%') { char b[1] = { '%' }; out_write(out, b, 1); wrote++; p += 2; continue; }
        const char *conv = p + 1;
        while (*conv && !strchr("diouxXcseEfFgGaApn%", *conv)) conv++;
        if (!*conv) { char b[1] = { '%' }; out_write(out, b, 1); wrote++; p++; continue; }
        char spec[64];
        host_spec(p, conv, spec, sizeof spec);
        char ch = (char)*conv;
        if (ch == 's') {
            char s[4096];
            guest_str(vararg(va, ai++), s, sizeof s);
            EMIT(spec, s);
        } else if (ch == 'c') {
            EMIT(spec, (int)(unsigned char)vararg(va, ai++));
        } else if (ch == 'f' || ch == 'e' || ch == 'g' || ch == 'F' || ch == 'E' || ch == 'G' || ch == 'a' || ch == 'A') {
            uint64_t bits = vararg(va, ai++);
            double d;
            memcpy(&d, &bits, 8);   /* Win64 duplicates FP varargs in the va area */
            EMIT(spec, d);
        } else if (ch == 'p') {
            EMIT("%llX", (unsigned long long)vararg(va, ai++));
        } else if (ch == 'n') {
            uint64_t dst = vararg(va, ai++);
            wr32(dst, (uint32_t)wrote);
        } else {
            /* All integer widths: fetch a 64-bit slot, then pass the width the
             * specifier asks for. lp64 `long` was already dropped by host_spec. */
            uint64_t v = vararg(va, ai++);
            const char *q = spec;
            int is_unsigned = strchr("uoxX", ch) != NULL;
            int sz = 8;
            for (; q && *q; q++) {
                if (*q == 'h') sz = (q[1] == 'h') ? 1 : 2;
                else if (*q == 'l') sz = (q[1] == 'l') ? 8 : 4;
                else if (*q == 'z' || *q == 'I') sz = 8;
            }
            if (is_unsigned) {
                uint64_t m = sz == 1 ? 0xffu : sz == 2 ? 0xffffu : sz == 4 ? 0xffffffffu : ~(uint64_t)0;
                v &= m;
                if (sz == 1) EMIT(spec, (unsigned int)(unsigned char)v);
                else if (sz == 2) EMIT(spec, (unsigned int)(unsigned short)v);
                else if (sz == 4) EMIT(spec, (unsigned int)(uint32_t)v);
                else EMIT(spec, v);
            } else {
                int64_t sv = (int64_t)v;
                if (sz == 1) sv = (int8_t)v;
                else if (sz == 2) sv = (int16_t)v;
                else if (sz == 4) sv = (int32_t)v;
                if (sz == 8) EMIT(spec, sv);
                else EMIT(spec, (int)sv);
            }
        }
        p = conv + 1;
    }
#undef EMIT
    fflush(out);
    return wrote;
}

/* ---- __stdio_common_vfscanf --------------------------------------------- */
static int vscan(CPU *c, FILE *in, int tail) {
    uint64_t fmt_va = ARG2(c);
    char fmt[2048];
    guest_str(fmt_va, fmt, sizeof fmt);
    if (trace_on())
        fprintf(stderr, "[shim] vfscanf opts=%llx stream=%llx fmt=%s\n",
                (unsigned long long)ARG0(c), (unsigned long long)ARG1(c), fmt);
    int ai = 0, got = 0;
    uint64_t va = va_list_ptr(c, tail);
    const char *p = fmt;
    while (*p) {
        if (isspace((unsigned char)*p)) { while (isspace((unsigned char)*p)) p++; continue; }
        if (*p != '%') { int ch = fgetc(in); if (ch != *p) return got; p++; continue; }
        if (p[1] == '%') { if (fgetc(in) != '%') return got; p += 2; continue; }
        const char *conv = p + 1;
        while (*conv && !strchr("diouxXceEfFgGaAs[", *conv)) conv++;
        if (!*conv) return got;
        char spec[64];
        host_spec(p, conv, spec, sizeof spec);
        char ch = (char)*conv;
        int suppress = 0;
        const char *q = spec;
        while (q && *q == '*') { suppress = 1; q++; }
        uint64_t dst = vararg(va, ai);
        (void)dst;
        int n = 0;
        if (ch == 'd' || ch == 'i' || ch == 'u' || ch == 'x' || ch == 'X' || ch == 'o') {
            long long v = 0;
            if (ch == 'u' || ch == 'x' || ch == 'X' || ch == 'o') {
                unsigned long long uv = 0;
                n = fscanf(in, spec, &uv);
                v = (long long)uv;
            } else n = fscanf(in, spec, &v);
            if (n == 1 && !suppress) {
                const char *s = spec;
                int sz = 4;
                while (s && *s) { if (*s == 'h') sz = (s[1] == 'h') ? 1 : 2; else if (*s == 'l') sz = (s[1] == 'l') ? 8 : 4; s++; }
                if (sz == 1) wr8(dst, (uint8_t)v);
                else if (sz == 2) wr16(dst, (uint16_t)v);
                else if (sz == 4) wr32(dst, (uint32_t)v);
                else wr64(dst, (uint64_t)v);
            }
        } else if (ch == 'c') {
            char v = 0; n = fscanf(in, spec, &v);
            if (n == 1 && !suppress) wr8(dst, (uint8_t)v);
        } else if (ch == 's') {
            char v[4096]; n = fscanf(in, spec, v);
            if (n == 1 && !suppress) for (size_t k = 0; k <= strlen(v); k++) wr8(dst + k, (uint8_t)v[k]);
        } else if (ch == 'f' || ch == 'e' || ch == 'g' || ch == 'F' || ch == 'E' || ch == 'G') {
            double v = 0; n = fscanf(in, spec, &v);
            if (n == 1 && !suppress) wr64(dst, *(uint64_t *)&v);
        } else {
            /* %[set]: read with the same spec into a buffer, then copy out. */
            char v[4096]; n = fscanf(in, spec, v);
            if (n == 1 && !suppress) for (size_t k = 0; k <= strlen(v); k++) wr8(dst + k, (uint8_t)v[k]);
        }
        if (n != 1) return got;
        got++;
        ai++;
        p = conv + 1;
    }
    return got;
}

/* ---- misc CRT data cells ------------------------------------------------- */
static int32_t g_argc_cell = 1;
static char     g_progname[] = "recomp64.exe";
static char    *g_argv_array[2];
static char   **g_argv_cell;
static char    *g_environ[1];
static int32_t g_commode, g_fmode = 0x40;
static int32_t g_tls[64];
static uint32_t g_onexit[64];
static int g_onexit_n;

static void data_init(void) {
    static int done;
    if (done) return;
    done = 1;
    iob_init();
    g_argv_array[0] = g_progname;
    g_argv_array[1] = NULL;
    g_argv_cell = g_argv_array;   /* __p___argv returns &this char** cell */
}

/* ---- the strong hle_invoke ---------------------------------------------- */
int win64_hle_invoke(CPU *c, uint32_t idx) {
    data_init();
    if (idx >= (uint32_t)HLE_COUNT) return 0;
    const char *name = recomp_imports[idx].name;
    uint64_t a0 = ARG0(c), a1 = ARG1(c), a2 = ARG2(c), a3 = ARG3(c);
    if (trace_on())
        fprintf(stderr, "[shim] import#%u %s rcx=%llx rdx=%llx r8=%llx r9=%llx ret=%llx\n",
                idx, name, (unsigned long long)a0, (unsigned long long)a1,
                (unsigned long long)a2, (unsigned long long)a3,
                (unsigned long long)rd64(c->rsp));
    int tail = 0;
    /* Indirect `jmp [__imp_X]` imports arrive without a pushed return address.
     * Detect the common tail-call set explicitly; ExitProcess never returns. */
    if (!strcmp(name, "ExitProcess") || !strcmp(name, "_exit") ||
        !strcmp(name, "exit") || !strcmp(name, "abort")) tail = 1;

#define IS(n) (!strcmp(name, n))

    /* ---- process lifetime ---- */
    if (IS("ExitProcess") || IS("_exit") || IS("exit")) {
        int rc = (int)(uint32_t)a0;
        fflush(stdout); fflush(stderr);
        exit(rc);
    }
    if (IS("abort")) { fflush(stdout); fflush(stderr); abort(); }
    if (IS("_cexit")) { fflush(stdout); fflush(stderr); return 1; }

    /* ---- startup / no-ops ---- */
    if (IS("DeleteCriticalSection") || IS("EnterCriticalSection") ||
        IS("LeaveCriticalSection") || IS("InitializeCriticalSection") ||
        IS("Sleep") || IS("_configthreadlocale") || IS("__setusermatherr") ||
        IS("_set_invalid_parameter_handler") || IS("_set_app_type") ||
        IS("_configure_narrow_argv") || IS("_initialize_narrow_environment") ||
        IS("setvbuf") || IS("__C_specific_handler")) {
        return 1;
    }
    if (IS("GetLastError")) { SET32(c->rax, 0); return 1; }
    if (IS("SetLastError")) { return 1; }
    if (IS("SetUnhandledExceptionFilter")) { SET64(c->rax, 0); return 1; }
    if (IS("signal")) { SET64(c->rax, 0); return 1; }
    if (IS("TlsGetValue")) { SET64(c->rax, g_tls[(a0 & 0x3f)]); return 1; }
    if (IS("TlsSetValue")) { g_tls[(a0 & 0x3f)] = a1; SET32(c->rax, 1); return 1; }
    if (IS("TlsAlloc")) { SET32(c->rax, 3); return 1; }
    if (IS("TlsFree")) { SET32(c->rax, 1); return 1; }
    if (IS("VirtualProtect")) { if (a3) wr32(a3, (uint32_t)a2); SET32(c->rax, 1); return 1; }
    if (IS("VirtualQuery")) { SET64(c->rax, 0x30); return 1; }
    if (IS("_set_new_mode")) { SET32(c->rax, 0); return 1; }
    if (IS("__p___argc")) { SET64(c->rax, (uint64_t)(uintptr_t)&g_argc_cell); return 1; }
    if (IS("__p___argv")) { SET64(c->rax, (uint64_t)(uintptr_t)&g_argv_cell); return 1; }
    if (IS("__p__environ")) { SET64(c->rax, (uint64_t)(uintptr_t)g_environ); return 1; }
    if (IS("__p__commode")) { SET64(c->rax, (uint64_t)(uintptr_t)&g_commode); return 1; }
    if (IS("__p__fmode")) { SET64(c->rax, (uint64_t)(uintptr_t)&g_fmode); return 1; }
    if (IS("__acrt_iob_func")) { SET64(c->rax, (uint64_t)(uintptr_t)&g_iob[a0 % 3]); return 1; }
    if (IS("_crt_atexit")) {
        if (g_onexit_n < 64) g_onexit[g_onexit_n++] = (uint32_t)a0;
        SET32(c->rax, 0); return 1;
    }
    if (IS("_initterm") || IS("_initterm_e")) {
        /* Call the guest initialisers through the same dispatch table as any
         * other guest function. The table stores original guest VAs. */
        uint64_t p = a0, end = a1;
        while (p + 8 <= end) {
            uint64_t fn = rd64(p);
            p += 8;
            if (!fn) continue;
            dispatch(c, fn);
        }
        SET32(c->rax, 0);
        return 1;
    }

    /* ---- heap ---- */
    if (IS("malloc")) { SET64(c->rax, (uint64_t)(uintptr_t)malloc((size_t)a0)); return 1; }
    if (IS("calloc")) { SET64(c->rax, (uint64_t)(uintptr_t)calloc((size_t)a0, (size_t)a1)); return 1; }
    if (IS("realloc")) { SET64(c->rax, (uint64_t)(uintptr_t)realloc((void *)(uintptr_t)a0, (size_t)a1)); return 1; }
    if (IS("free")) { free((void *)(uintptr_t)a0); return 1; }

    /* ---- string / conversion ---- */
    if (IS("strlen")) { SET64(c->rax, strlen((const char *)(uintptr_t)a0)); return 1; }
    if (IS("strncmp")) { SET32(c->rax, strncmp((const char *)(uintptr_t)a0, (const char *)(uintptr_t)a1, (size_t)a2)); return 1; }
    if (IS("strcmp")) { SET32(c->rax, strcmp((const char *)(uintptr_t)a0, (const char *)(uintptr_t)a1)); return 1; }
    if (IS("strchr")) { SET64(c->rax, (uint64_t)(uintptr_t)strchr((const char *)(uintptr_t)a0, (int)a1)); return 1; }
    if (IS("strrchr")) { SET64(c->rax, (uint64_t)(uintptr_t)strrchr((const char *)(uintptr_t)a0, (int)a1)); return 1; }
    if (IS("memcpy")) { SET64(c->rax, (uint64_t)memcpy((void *)(uintptr_t)a0, (const void *)(uintptr_t)a1, (size_t)a2)); return 1; }
    if (IS("memmove")) { SET64(c->rax, (uint64_t)memmove((void *)(uintptr_t)a0, (const void *)(uintptr_t)a1, (size_t)a2)); return 1; }
    if (IS("memset")) { SET64(c->rax, (uint64_t)memset((void *)(uintptr_t)a0, (int)a1, (size_t)a2)); return 1; }
    if (IS("memcmp")) { SET32(c->rax, memcmp((const void *)(uintptr_t)a0, (const void *)(uintptr_t)a1, (size_t)a2)); return 1; }
    if (IS("toupper")) { SET32(c->rax, toupper((int)a0)); return 1; }
    if (IS("tolower")) { SET32(c->rax, tolower((int)a0)); return 1; }
    if (IS("atoi")) { SET32(c->rax, atoi((const char *)(uintptr_t)a0)); return 1; }
    if (IS("atol")) { SET64(c->rax, atol((const char *)(uintptr_t)a0)); return 1; }
    if (IS("rand")) { SET32(c->rax, rand()); return 1; }
    if (IS("srand")) { srand((unsigned)a0); return 1; }
    if (IS("_time64")) { SET64(c->rax, (uint64_t)time(NULL)); return 1; }

    /* ---- stdio ---- */
    if (IS("__stdio_common_vfprintf")) { SET32(c->rax, vfmt(c, stream_of(a1), tail)); return 1; }
    if (IS("__stdio_common_vfscanf")) { SET32(c->rax, vscan(c, stream_of(a1), tail)); return 1; }
    if (IS("puts")) {
        const char *s = (const char *)(uintptr_t)a0;
        out_write(stdout, s, strlen(s));
        out_write(stdout, "\n", 1);
        fflush(stdout);
        SET32(c->rax, 0);
        return 1;
    }
    if (IS("putchar")) {
        char b[1] = { (char)a0 };
        out_write(stdout, b, 1);
        fflush(stdout);
        SET32(c->rax, (int)(unsigned char)a0);
        return 1;
    }
    if (IS("getchar")) { SET32(c->rax, getchar()); return 1; }
    if (IS("fgets")) {
        char *dst = (char *)(uintptr_t)a0;
        int n = (int)a1;
        FILE *in = stream_of(a2);
        int i = 0;
        if (n > 1 && in) {
            while (i < n - 1) {
                int ch = fgetc(in);
                if (ch == EOF) break;
                dst[i++] = (char)ch;
                if (ch == '\n') break;
            }
            dst[i] = 0;
        }
        SET64(c->rax, i ? (uint64_t)(uintptr_t)dst : 0);
        return 1;
    }
    if (IS("fflush")) { SET32(c->rax, fflush(stream_of(a0))); return 1; }
    if (IS("fwrite")) {
        size_t n = (size_t)a1 * (size_t)a2;
        out_write(stdout, (const char *)(uintptr_t)a0, n);
        fflush(stdout);
        SET64(c->rax, (size_t)a2);
        return 1;
    }
    if (IS("fputc")) {
        char b[1] = { (char)a0 };
        out_write(stdout, b, 1);
        SET32(c->rax, (int)(unsigned char)a0);
        return 1;
    }

    if (trace_on())
        fprintf(stderr, "[shim] UNMODELLED import %s!%s (slot %u)\n",
                recomp_imports[idx].dll, name, idx);
    return 0;  /* weak default in cpu64.c reports and aborts */
}

int hle_invoke(CPU *c, uint32_t idx) {
    return win64_hle_invoke(c, idx);
}
