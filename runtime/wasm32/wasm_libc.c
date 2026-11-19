#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>
#include <ctype.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <errno.h>
#include "win32hle.h"

/* ============================================================
 * Host math imports (via JS Math.*) + C fallbacks
 * ============================================================ */
__attribute__((import_module("env"), import_name("wasm_sin")))   double wasm_js_sin(double);
__attribute__((import_module("env"), import_name("wasm_cos")))   double wasm_js_cos(double);
__attribute__((import_module("env"), import_name("wasm_tan")))   double wasm_js_tan(double);
__attribute__((import_module("env"), import_name("wasm_asin")))  double wasm_js_asin(double);
__attribute__((import_module("env"), import_name("wasm_acos")))  double wasm_js_acos(double);
__attribute__((import_module("env"), import_name("wasm_atan")))  double wasm_js_atan(double);
__attribute__((import_module("env"), import_name("wasm_atan2"))) double wasm_js_atan2(double, double);
__attribute__((import_module("env"), import_name("wasm_pow")))   double wasm_js_pow(double, double);
__attribute__((import_module("env"), import_name("wasm_exp")))   double wasm_js_exp(double);
__attribute__((import_module("env"), import_name("wasm_log")))   double wasm_js_log(double);

double sin(double x)            { return wasm_js_sin(x); }
double cos(double x)            { return wasm_js_cos(x); }
double tan(double x)            { return wasm_js_tan(x); }
double asin(double x)           { return wasm_js_asin(x); }
double acos(double x)           { return wasm_js_acos(x); }
double atan(double x)           { return wasm_js_atan(x); }
double atan2(double y, double x){ return wasm_js_atan2(y, x); }
double pow(double x, double y)  { return wasm_js_pow(x, y); }
double exp(double x)            { return wasm_js_exp(x); }
double log(double x)            { return wasm_js_log(x); }
double log2(double x)           { return wasm_js_log(x) / 0.69314718055994530942; }
double log10(double x)          { return wasm_js_log(x) / 2.30258509299404568402; }
double fmod(double x, double y) { return y == 0.0 ? NAN : x - trunc(x / y) * y; }

double ldexp(double x, int e) {
    if (x == 0.0 || isnan(x) || isinf(x)) return x;
    while (e > 500) { x *= 3.2733906078961419e150; e -= 500; }
    while (e < -500) { x *= 3.0549363634996047e-151; e += 500; }
    uint64_t scale_bits = (uint64_t)(1023 + e) << 52;
    double scale;
    memcpy(&scale, &scale_bits, 8);
    return x * scale;
}

double frexp(double x, int *e) {
    if (x == 0.0 || isnan(x) || isinf(x)) {
        if (e) *e = 0;
        return x;
    }
    uint64_t bits;
    memcpy(&bits, &x, 8);
    int exp_raw = (int)((bits >> 52) & 0x7FFu);
    if (exp_raw == 0) {
        x = frexp(x * ldexp(1.0, 54), e);
        if (e) *e -= 54;
        return x;
    }
    if (e) *e = exp_raw - 1022;
    bits = (bits & 0x800FFFFFFFFFFFFFULL) | (1022ULL << 52);
    memcpy(&x, &bits, 8);
    return x;
}

/* ============================================================
 * Linear Memory Allocator & 64 KiB Segment Translation
 * ============================================================ */
extern unsigned char __heap_base;
uint32_t g_wasm_seg_base[65536];
static uintptr_t g_brk;

void *wasm_alloc_aligned(size_t size, size_t align) {
    if (align < 16) align = 16;
    if (!g_brk) {
        uintptr_t base = (uintptr_t)&__heap_base;
        g_brk = (base + 65535u) & ~(uintptr_t)65535u;
    }
    uintptr_t start = (g_brk + (align - 1u)) & ~(align - 1u);
    uintptr_t end = start + (size ? size : 16u);
    uintptr_t cur_bytes = (uintptr_t)__builtin_wasm_memory_size(0) * 65536u;
    if (end > cur_bytes) {
        size_t need_pages = (end - cur_bytes + 65535u) / 65536u;
        if (__builtin_wasm_memory_grow(0, need_pages) < 0) return NULL;
    }
    g_brk = end;
    memset((void *)start, 0, size);
    return (void *)start;
}

void wasm_map_guest_region(uint32_t guest_va, uint32_t span) {
    uint32_t va_start = guest_va & ~0xFFFFu;
    uint32_t va_end = (guest_va + span + 0xFFFFu) & ~0xFFFFu;
    uint32_t total = va_end - va_start;
    if (!total) return;
    /* Already mapped? Ensure any gaps get covered */
    uint32_t first_idx = va_start >> 16;
    uint32_t count = total >> 16;
    int all_mapped = 1;
    for (uint32_t i = 0; i < count; i++) {
        if (!g_wasm_seg_base[first_idx + i]) { all_mapped = 0; break; }
    }
    if (all_mapped) return;
    uint8_t *phys = (uint8_t *)wasm_alloc_aligned(total, 65536u);
    if (!phys) return;
    uintptr_t base_addr = (uintptr_t)phys;
    for (uint32_t i = 0; i < count; i++) {
        if (!g_wasm_seg_base[first_idx + i]) {
            g_wasm_seg_base[first_idx + i] = (uint32_t)(base_addr + i * 65536u);
        }
    }
}

/* ============================================================
 * Freestanding Memory, String, Heap & Printf Helpers
 * ============================================================ */
void *memcpy(void *dst, const void *src, size_t n) {
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    while (n--) *d++ = *s++;
    return dst;
}

void *memmove(void *dst, const void *src, size_t n) {
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    if (d < s) {
        while (n--) *d++ = *s++;
    } else if (d > s) {
        d += n;
        s += n;
        while (n--) *--d = *--s;
    }
    return dst;
}

void *memset(void *dst, int c, size_t n) {
    uint8_t *d = (uint8_t *)dst;
    uint8_t v = (uint8_t)c;
    while (n--) *d++ = v;
    return dst;
}

int memcmp(const void *a, const void *b, size_t n) {
    const uint8_t *pa = (const uint8_t *)a, *pb = (const uint8_t *)b;
    for (size_t i = 0; i < n; i++) {
        if (pa[i] != pb[i]) return (int)pa[i] - (int)pb[i];
    }
    return 0;
}

void *memchr(const void *s, int c, size_t n) {
    const uint8_t *p = (const uint8_t *)s;
    for (size_t i = 0; i < n; i++) {
        if (p[i] == (uint8_t)c) return (void *)(p + i);
    }
    return NULL;
}

size_t strlen(const char *s) {
    size_t n = 0;
    if (!s) return 0;
    while (s[n]) n++;
    return n;
}

char *strcpy(char *dst, const char *src) {
    char *r = dst;
    while ((*dst++ = *src++) != 0) {}
    return r;
}

char *strncpy(char *dst, const char *src, size_t n) {
    size_t i = 0;
    for (; i < n && src[i]; i++) dst[i] = src[i];
    for (; i < n; i++) dst[i] = 0;
    return dst;
}

char *strcat(char *dst, const char *src) {
    strcpy(dst + strlen(dst), src);
    return dst;
}

char *strncat(char *dst, const char *src, size_t n) {
    size_t dlen = strlen(dst), i = 0;
    while (i < n && src[i]) { dst[dlen + i] = src[i]; i++; }
    dst[dlen + i] = 0;
    return dst;
}

int strcmp(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}

int strncmp(const char *a, const char *b, size_t n) {
    for (size_t i = 0; i < n; i++) {
        unsigned char ca = (unsigned char)a[i], cb = (unsigned char)b[i];
        if (ca != cb) return ca - cb;
        if (!ca) return 0;
    }
    return 0;
}

int strcasecmp(const char *a, const char *b) {
    while (*a && tolower((unsigned char)*a) == tolower((unsigned char)*b)) { a++; b++; }
    return tolower((unsigned char)*a) - tolower((unsigned char)*b);
}

int strncasecmp(const char *a, const char *b, size_t n) {
    for (size_t i = 0; i < n; i++) {
        unsigned char ca = (unsigned char)tolower((unsigned char)a[i]);
        unsigned char cb = (unsigned char)tolower((unsigned char)b[i]);
        if (ca != cb) return ca - cb;
        if (!a[i]) return 0;
    }
    return 0;
}

char *strchr(const char *s, int c) {
    while (*s) {
        if (*s == (char)c) return (char *)s;
        s++;
    }
    return c == 0 ? (char *)s : NULL;
}

char *strrchr(const char *s, int c) {
    const char *last = NULL;
    while (*s) {
        if (*s == (char)c) last = s;
        s++;
    }
    return c == 0 ? (char *)s : (char *)last;
}

char *strstr(const char *h, const char *n) {
    if (!*n) return (char *)h;
    size_t nl = strlen(n);
    while (*h) {
        if (!strncmp(h, n, nl)) return (char *)h;
        h++;
    }
    return NULL;
}

size_t strcspn(const char *s, const char *rej) {
    size_t i = 0;
    while (s[i] && !strchr(rej, s[i])) i++;
    return i;
}

size_t strspn(const char *s, const char *acc) {
    size_t i = 0;
    while (s[i] && strchr(acc, s[i])) i++;
    return i;
}

/* Simple headered allocator so realloc/free work cleanly */
typedef struct {
    size_t size;
    size_t pad;
} wasm_hdr_t;

void *malloc(size_t size) {
    if (!size) size = 1;
    wasm_hdr_t *h = (wasm_hdr_t *)wasm_alloc_aligned(sizeof(wasm_hdr_t) + size, 16u);
    if (!h) return NULL;
    h->size = size;
    return (void *)(h + 1);
}

void *calloc(size_t nmemb, size_t size) {
    size_t total = nmemb * size;
    void *p = malloc(total ? total : 1);
    if (p) memset(p, 0, total);
    return p;
}

void *realloc(void *ptr, size_t size) {
    if (!ptr) return malloc(size);
    if (!size) { free(ptr); return NULL; }
    wasm_hdr_t *h = ((wasm_hdr_t *)ptr) - 1;
    if (h->size >= size) return ptr;
    void *np = malloc(size);
    if (!np) return NULL;
    memcpy(np, ptr, h->size);
    return np;
}

void free(void *ptr) {
    (void)ptr;
}

int errno = 0;
static struct _wasm_file { int fd; } g_f0 = {0}, g_f1 = {1}, g_f2 = {2};
FILE *stdin  = &g_f0;
FILE *stdout = &g_f1;
FILE *stderr = &g_f2;

int abs(int x) { return x < 0 ? -x : x; }

long strtol(const char *s, char **endp, int base) {
    const char *p = s;
    while (isspace((unsigned char)*p)) p++;
    int neg = 0;
    if (*p == '-') { neg = 1; p++; }
    else if (*p == '+') p++;
    if (base == 0) {
        if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) { base = 16; p += 2; }
        else if (p[0] == '0') base = 8;
        else base = 10;
    } else if (base == 16 && p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) {
        p += 2;
    }
    unsigned long val = 0;
    const char *start = p;
    while (*p) {
        int d = -1;
        if (isdigit((unsigned char)*p)) d = *p - '0';
        else if (*p >= 'a' && *p <= 'z') d = *p - 'a' + 10;
        else if (*p >= 'A' && *p <= 'Z') d = *p - 'A' + 10;
        if (d < 0 || d >= base) break;
        val = val * (unsigned long)base + (unsigned long)d;
        p++;
    }
    if (endp) *endp = (char *)(p == start ? s : p);
    return neg ? -(long)val : (long)val;
}

unsigned long strtoul(const char *s, char **endp, int base) {
    return (unsigned long)strtol(s, endp, base);
}

int atoi(const char *s) { return s ? (int)strtol(s, NULL, 10) : 0; }
long atol(const char *s) { return s ? strtol(s, NULL, 10) : 0L; }

double strtod(const char *s, char **endp) {
    const char *p = s;
    while (isspace((unsigned char)*p)) p++;
    int neg = 0;
    if (*p == '-') { neg = 1; p++; }
    else if (*p == '+') p++;
    double v = 0.0;
    const char *start = p;
    while (isdigit((unsigned char)*p)) { v = v * 10.0 + (*p - '0'); p++; }
    if (*p == '.') {
        p++;
        double frac = 0.1;
        while (isdigit((unsigned char)*p)) { v += (*p - '0') * frac; frac *= 0.1; p++; }
    }
    if (*p == 'e' || *p == 'E') {
        p++;
        int eneg = 0;
        if (*p == '-') { eneg = 1; p++; } else if (*p == '+') p++;
        int e = 0;
        while (isdigit((unsigned char)*p)) { e = e * 10 + (*p - '0'); p++; }
        double scale = 1.0;
        while (e-- > 0) scale *= 10.0;
        v = eneg ? (v / scale) : (v * scale);
    }
    if (endp) *endp = (char *)(p == start ? s : p);
    return neg ? -v : v;
}

double atof(const char *s) { return s ? strtod(s, NULL) : 0.0; }

static unsigned int g_rand_state = 1u;
void srand(unsigned int seed) { g_rand_state = seed; }
int rand(void) {
    g_rand_state = g_rand_state * 1103515245u + 12345u;
    return (int)((g_rand_state >> 16) & 0x7FFFu);
}

static void swap_bytes(uint8_t *a, uint8_t *b, size_t n) {
    while (n--) { uint8_t t = *a; *a++ = *b; *b++ = t; }
}

void qsort(void *base, size_t nmemb, size_t size, int (*compar)(const void *, const void *)) {
    uint8_t *arr = (uint8_t *)base;
    if (nmemb < 2) return;
    for (size_t i = 1; i < nmemb; i++) {
        size_t j = i;
        while (j > 0 && compar(arr + (j - 1) * size, arr + j * size) > 0) {
            swap_bytes(arr + (j - 1) * size, arr + j * size, size);
            j--;
        }
    }
}

void *bsearch(const void *key, const void *base, size_t nmemb, size_t size,
              int (*compar)(const void *, const void *)) {
    const uint8_t *arr = (const uint8_t *)base;
    size_t lo = 0, hi = nmemb;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        const void *elem = arr + mid * size;
        int c = compar(key, elem);
        if (c == 0) return (void *)elem;
        if (c < 0) hi = mid; else lo = mid + 1;
    }
    return NULL;
}

void abort(void) { hle_exit(3); }
void exit(int status) { hle_exit(status); }

/* Formatted output */
typedef struct { char *buf; size_t cap, len; } fmt_sink_t;
static void sink_ch(fmt_sink_t *s, char c) {
    if (s->buf && s->len + 1 < s->cap) s->buf[s->len] = c;
    s->len++;
}
static void sink_str(fmt_sink_t *s, const char *p, size_t n, int width, int left, char pad) {
    if (!left) for (int i = (int)n; i < width; i++) sink_ch(s, pad);
    for (size_t i = 0; i < n; i++) sink_ch(s, p[i]);
    if (left) for (int i = (int)n; i < width; i++) sink_ch(s, ' ');
}

int vsnprintf(char *buf, size_t size, const char *fmt, va_list ap) {
    fmt_sink_t s = { buf, size, 0 };
    for (const char *p = fmt; p && *p; p++) {
        if (*p != '%') { sink_ch(&s, *p); continue; }
        p++;
        int left = 0, zero = 0, width = 0, is_long = 0, prec = -1;
        while (*p == '-' || *p == '0' || *p == '+' || *p == ' ') {
            if (*p == '-') left = 1;
            if (*p == '0') zero = 1;
            p++;
        }
        while (isdigit((unsigned char)*p)) { width = width * 10 + (*p - '0'); p++; }
        if (*p == '.') {
            p++; prec = 0;
            while (isdigit((unsigned char)*p)) { prec = prec * 10 + (*p - '0'); p++; }
        }
        if (*p == 'l') { is_long = 1; p++; if (*p == 'l') { is_long = 2; p++; } }
        else if (*p == 'z') { is_long = 1; p++; }
        char conv = *p;
        if (!conv) break;
        if (conv == '%') { sink_ch(&s, '%'); continue; }
        if (conv == 'c') {
            char ch = (char)va_arg(ap, int);
            sink_str(&s, &ch, 1, width, left, ' ');
        } else if (conv == 's') {
            const char *str = va_arg(ap, const char *);
            if (!str) str = "(null)";
            size_t len = strlen(str);
            if (prec >= 0 && (size_t)prec < len) len = (size_t)prec;
            sink_str(&s, str, len, width, left, ' ');
        } else if (conv == 'd' || conv == 'i' || conv == 'u' || conv == 'x' || conv == 'X' || conv == 'p') {
            uint64_t uv = 0; int neg = 0;
            if (conv == 'p') { uv = (uintptr_t)va_arg(ap, void *); conv = 'x'; }
            else if (is_long == 2) {
                int64_t sv = va_arg(ap, int64_t);
                if ((conv == 'd' || conv == 'i') && sv < 0) { neg = 1; uv = (uint64_t)(-sv); }
                else uv = (uint64_t)sv;
            } else {
                int32_t sv = va_arg(ap, int32_t);
                if ((conv == 'd' || conv == 'i') && sv < 0) { neg = 1; uv = (uint64_t)(-(int64_t)sv); }
                else uv = (uint32_t)sv;
            }
            unsigned base = (conv == 'x' || conv == 'X') ? 16u : 10u;
            const char *digs = (conv == 'X') ? "0123456789ABCDEF" : "0123456789abcdef";
            char tmp[32]; int k = 0;
            do { tmp[k++] = digs[uv % base]; uv /= base; } while (uv);
            if (neg) tmp[k++] = '-';
            for (int i = 0; i < k / 2; i++) { char t = tmp[i]; tmp[i] = tmp[k - 1 - i]; tmp[k - 1 - i] = t; }
            sink_str(&s, tmp, (size_t)k, width, left, zero ? '0' : ' ');
        } else if (conv == 'f' || conv == 'g') {
            double dv = va_arg(ap, double);
            if (dv < 0) { sink_ch(&s, '-'); dv = -dv; }
            int p_cnt = (prec >= 0) ? prec : 2;
            uint64_t ip = (uint64_t)dv;
            double frac = dv - (double)ip;
            char tmp[32]; int k = 0;
            do { tmp[k++] = (char)('0' + (ip % 10)); ip /= 10; } while (ip);
            for (int i = 0; i < k / 2; i++) { char t = tmp[i]; tmp[i] = tmp[k - 1 - i]; tmp[k - 1 - i] = t; }
            sink_str(&s, tmp, (size_t)k, 0, 1, ' ');
            if (p_cnt > 0) {
                sink_ch(&s, '.');
                for (int i = 0; i < p_cnt; i++) {
                    frac *= 10.0;
                    int d = (int)frac;
                    if (d > 9) d = 9;
                    sink_ch(&s, (char)('0' + d));
                    frac -= d;
                }
            }
        } else {
            sink_ch(&s, '%');
            sink_ch(&s, conv);
        }
    }
    if (s.buf && s.cap) s.buf[s.len < s.cap ? s.len : s.cap - 1] = 0;
    return (int)s.len;
}

int snprintf(char *buf, size_t size, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); int r = vsnprintf(buf, size, fmt, ap); va_end(ap); return r;
}
int sprintf(char *buf, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); int r = vsnprintf(buf, 65536u, fmt, ap); va_end(ap); return r;
}

int vfprintf(FILE *stream, const char *fmt, va_list ap) {
    char buf[2048];
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    if (n > 0) {
        int out_n = n < (int)sizeof buf ? n : (int)sizeof buf - 1;
        wasm_host_log(stream == stderr ? 2 : 1, buf, out_n);
    }
    return n;
}
int fprintf(FILE *stream, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); int n = vfprintf(stream, fmt, ap); va_end(ap); return n;
}
int printf(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); int n = vfprintf(stdout, fmt, ap); va_end(ap); return n;
}
int puts(const char *s) {
    if (s) wasm_host_log(1, s, (int)strlen(s));
    wasm_host_log(1, "\n", 1);
    return 0;
}
int fputs(const char *s, FILE *stream) {
    if (s) wasm_host_log(stream == stderr ? 2 : 1, s, (int)strlen(s));
    return 0;
}
int fputc(int c, FILE *stream) {
    char ch = (char)c;
    wasm_host_log(stream == stderr ? 2 : 1, &ch, 1);
    return c;
}
int putchar(int c) { return fputc(c, stdout); }
int fflush(FILE *stream) { (void)stream; return 0; }

void hle_logf(int stream, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stream == 2 ? stderr : stdout, fmt, ap);
    va_end(ap);
}

int clock_gettime(int clk_id, struct timespec *tp) {
    (void)clk_id;
    if (!tp) return -1;
    double ms = wasm_host_now_ms();
    tp->tv_sec = (long)(ms / 1000.0);
    tp->tv_nsec = (long)((ms - (double)tp->tv_sec * 1000.0) * 1000000.0);
    return 0;
}

time_t time(time_t *tloc) {
    time_t t = 1760000000L + (time_t)(wasm_host_now_ms() / 1000.0);
    if (tloc) *tloc = t;
    return t;
}
