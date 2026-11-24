/*
 * kernel32_ext.c - the KERNEL32 file system and .ini profile shims,
 * on POSIX. Files are file descriptors behind typed handles (host_lite.c);
 * paths go through hle_host_path, so a guest's `DATA\RULES.INI` finds
 * `data/rules.ini` on a case-sensitive disk. Also the directory search
 * (FindFirstFileA), attributes, and the FILETIME/SYSTEMTIME conversions a
 * C runtime's stat and time functions are built on.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <time.h>
#include <dirent.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/mman.h>
#include "win32hle.h"

/* kernel32_crt.c's codepage-1252 conversions (it owns the mapping the ANSI
 * calls use); the W file calls share them rather than carrying a second copy. */
int hle_wide_to_ansi(const uint16_t *w, char *out, size_t n);
int hle_ansi_to_wide(const char *s, uint16_t *out, size_t n);

#ifdef __APPLE__                 /* struct stat's times by their BSD names */
#define st_mtim st_mtimespec
#define st_atim st_atimespec
#endif

#define INVALID_HANDLE  0xFFFFFFFFu
#define ERROR_FILE_NOT_FOUND    2u
#define ERROR_PATH_NOT_FOUND    3u
#define ERROR_ACCESS_DENIED     5u
#define ERROR_INVALID_HANDLE    6u
#define ERROR_NO_MORE_FILES    18u
#define ERROR_HANDLE_EOF       38u
#define ERROR_FILE_EXISTS      80u
#define ERROR_INVALID_NAME    123u      /* a path the ANSI codepage cannot carry */
#define ERROR_ALREADY_EXISTS  183u
#define ERROR_INVALID_FUNCTION  1u
#define ERROR_INVALID_PARAMETER  87u
#define ERROR_WRITE_PROTECT      19u
#define ERROR_WRITE_FAULT        29u
#define ERROR_INSUFFICIENT_BUFFER 122u
#define ERROR_INVALID_ADDRESS   487u
#define ERROR_NOT_ENOUGH_MEMORY   8u
#define ERROR_FILE_INVALID     1006u

/* CreateFileMapping's SEC_PAGE_* protection values. */
#define SEC_READONLY           0x02u
#define SEC_READWRITE          0x04u
#define SEC_WRITECOPY          0x08u
#define SEC_EXECUTE_READ       0x20u
#define SEC_EXECUTE_READWRITE  0x40u
#define SEC_EXECUTE_WRITECOPY  0x80u
#define SEC_WRITABLE_MASK (SEC_READWRITE | SEC_WRITECOPY | SEC_EXECUTE_READWRITE | SEC_EXECUTE_WRITECOPY)
#define SEC_VALID_MASK    (SEC_READONLY | SEC_WRITABLE_MASK | 0x01u /* SEC_COMMIT */ | 0x10000000u /* SEC_IMAGE */)

/* MapViewOfFile's dwDesiredAccess bits. */
#define FILE_MAP_COPY     0x0001u
#define FILE_MAP_WRITE    0x0002u
#define FILE_MAP_READ     0x0004u
#define FILE_MAP_EXECUTE  0x0020u

#define CURRENT_PROCESS_HANDLE 0xFFFFFFFFu

#define FILE_ATTRIBUTE_READONLY  0x01u
#define FILE_ATTRIBUTE_HIDDEN    0x02u
#define FILE_ATTRIBUTE_SYSTEM    0x04u
#define FILE_ATTRIBUTE_DIRECTORY 0x10u
#define FILE_ATTRIBUTE_ARCHIVE   0x20u
#define FILE_ATTRIBUTE_NORMAL    0x80u
#define FILE_ATTRIBUTE_TEMPORARY 0x100u
#define FILE_ATTRIBUTE_OFFLINE   0x1000u
#define FILE_ATTRIBUTE_NOT_CONTENT_INDEXED 0x2000u

/* Bound a scan over a NUL-terminated guest WCHAR string. The W string APIs
 * (lstrcpyW/lstrcatW/FormatMessageW/MoveFileExW) have no length argument, so an
 * unterminated or bogus pointer must not walk off the end of the guest's 4 GB.
 * Returns the length in code units (excluding the NUL), or 0 on failure. */
#define U16_MAX 0x100000u                       /* same ceiling as the Ex string helpers */
static int u16_len(uint32_t va, uint32_t *out) {
    uint32_t limit = U16_MAX, i;
    if (!va) return 0;
    if ((uint64_t)va + 2ull * limit > 0x100000000ull)
        limit = (uint32_t)((0x100000000ull - va) / 2u);
    for (i = 0; i < limit; i++)
        if (!MEM16(va + i * 2u)) { *out = i; return 1; }
    return 0;
}

static uint32_t errno_to_win(int e) {
    switch (e) {
    case ENOENT:  return ERROR_FILE_NOT_FOUND;
    case ENOTDIR: return ERROR_PATH_NOT_FOUND;
    case EEXIST:  return ERROR_FILE_EXISTS;
    case EACCES: case EPERM: case EROFS: return ERROR_ACCESS_DENIED;
    default:      return ERROR_INVALID_FUNCTION;
    }
}

/* ---- file objects ---- */
/* `name` is the guest (Windows) path the handle was opened with; GetMappedFileName
 * reports it for a view backed by this file. Pipes leave it NULL. */
typedef struct { int fd; char *name; } hfile;
static void file_closer(void *o) {
    hfile *f = (hfile *)o;
    if (f->fd > 2) close(f->fd);
    free(f->name);
    free(f);
}

/* The standard handles (kernel32_crt.c's GetStdHandle) are 0x11..0x13. */
static int fd_of(uint32_t h) {
    if (h == 0x11u) return 0;
    if (h == 0x12u) return 1;
    if (h == 0x13u) return 2;
    hfile *f = (hfile *)hle_handle_obj(h, HLE_H_FILE);
    return f ? f->fd : -1;
}

int hle_file_fd(uint32_t h) { return fd_of(h); }   /* a file HANDLE's descriptor, for a host (Bink reads the game's) */

static uint32_t new_file_named(int fd, const char *name) {
    static int closer_set;
    if (!closer_set) hle_handle_set_closer(HLE_H_FILE, file_closer), closer_set = 1;
    hfile *f = (hfile *)malloc(sizeof *f);
    f->fd = fd;
    f->name = name ? strdup(name) : NULL;
    uint32_t h = hle_handle_alloc(HLE_H_FILE, f);
    if (!h) { file_closer(f); return INVALID_HANDLE; }   /* closer releases fd and name */
    return h;
}
static uint32_t new_file(int fd) { return new_file_named(fd, NULL); }

/* Device names a Windows program may open that a host has no file for. */
/* A port is COM1-COM9 or LPT1-LPT9, the whole name before any extension.
 * Matching the prefix alone took any file named Com... for a serial port:
 * Civilization III's credits (complete_credits.txt) and WWII in the
 * Pacific's Commonwealth advisor (commonwealth_all.pcx), which it then
 * used without checking. */
static int is_port(const char *b) {
    return (!strncasecmp(b, "COM", 3) || !strncasecmp(b, "LPT", 3)) && b[3] >= '1' && b[3] <= '9' && (!b[4] || b[4] == '.');
}
static int is_device(const char *p, char *host, size_t n) {
    const char *b = p + strlen(p);
    while (b > p && b[-1] != '\\' && b[-1] != '/' && b[-1] != ':') b--;
    if (!strncmp(p, "\\\\.\\", 4) || is_port(b)) { host[0] = 0; return 1; }
    if (!strcasecmp(b, "NUL")) { snprintf(host, n, "/dev/null"); return 1; }
    if (!strcasecmp(b, "CON")) { snprintf(host, n, "/dev/tty"); return 1; }
    return 0;
}

int hle_filetrace(void) {
    static int on = -1;
    if (on < 0) on = getenv("HLE_FILETRACE") != NULL;
    return on;
}

/* CreateFile(name,access,share,sa,disp,flags,template). access GENERIC_READ
 * 0x80000000, GENERIC_WRITE 0x40000000; disp CREATE_NEW 1, CREATE_ALWAYS 2,
 * OPEN_EXISTING 3, OPEN_ALWAYS 4, TRUNCATE_EXISTING 5.  The body works on the
 * ANSI path, so CreateFileW converts its UTF-16 one and shares it (codepage
 * 1252, this layer's ANSI codepage). */
static uint32_t create_file(const char *what, const char *name, uint32_t access, uint32_t disp, uint32_t flags) {
    char path[1024];
    int exists = 0;
    if (is_device(name, path, sizeof path)) {
        if (!path[0]) { hle_set_last_error(ERROR_FILE_NOT_FOUND); return INVALID_HANDLE; }
        exists = 1;
    } else if ((access & 0x40000000u) || disp == 1 || disp == 2 || disp == 4) {
        /* a write or a create: under a mod's overlay, into the overlay (a file
         * opened to change in place is copied there first) */
        int keep = (access & 0x40000000u) && (disp == 3 || disp == 4);
        struct stat there;
        hle_host_path_for_write(name, path, sizeof path, keep);
        exists = stat(path, &there) == 0;
    } else {
        exists = hle_host_path(name, path, sizeof path);
    }
    int rd = (access & 0x80000000u) || !(access & 0x40000000u), wr = (access & 0x40000000u) != 0;
    int fl = rd && wr ? O_RDWR : wr ? O_WRONLY : O_RDONLY;
    switch (disp) {
    case 1: fl |= O_CREAT | O_EXCL; break;
    case 2: fl |= O_CREAT | O_TRUNC; break;
    case 4: fl |= O_CREAT; break;
    case 5: fl |= O_TRUNC; break;
    default: break;
    }
    struct stat st;
    if (exists && stat(path, &st) == 0 && S_ISDIR(st.st_mode) && !(flags & 0x02000000u)) {
        hle_set_last_error(ERROR_ACCESS_DENIED);    /* a directory, without FILE_FLAG_BACKUP_SEMANTICS */
        return INVALID_HANDLE;
    }
    int fd = open(path, fl | O_CLOEXEC, 0644);
    if (hle_filetrace())
        fprintf(stderr, "[file] %s \"%s\" -> %s %s\n", what, name, path, fd >= 0 ? "ok" : strerror(errno));
    if (fd < 0) { hle_set_last_error(errno_to_win(errno)); return INVALID_HANDLE; }
    hle_set_last_error(exists && (disp == 2 || disp == 4) ? ERROR_ALREADY_EXISTS : 0);
    return new_file_named(fd, name);
}
static void k_CreateFileA(void) {
    const char *name = ASTR(0);
    if (!name || !*name) { hle_set_last_error(ERROR_PATH_NOT_FOUND); RET(INVALID_HANDLE, 7); }
    RET(create_file("CreateFileA", name, A32(1), A32(4), A32(5)), 7);
}
/* The W call: the same file, named in UTF-16. */
static void k_CreateFileW(void) {
    const uint16_t *w = (const uint16_t *)APTR(0);
    char name[1024];
    if (!w || !*w) { hle_set_last_error(ERROR_PATH_NOT_FOUND); RET(INVALID_HANDLE, 7); }
    if (!hle_wide_to_ansi(w, name, sizeof name)) { hle_set_last_error(ERROR_INVALID_NAME); RET(INVALID_HANDLE, 7); }
    RET(create_file("CreateFileW", name, A32(1), A32(4), A32(5)), 7);
}

static void k_ReadFile(void) {                           /* (h,buf,n,&read,ovl) */
    int fd = fd_of(A32(0));
    uint8_t *buf = (uint8_t *)APTR(1);
    uint32_t n = A32(2), *pr = (uint32_t *)APTR(3), got = 0;
    if (pr) *pr = 0;
    if (fd < 0) { hle_set_last_error(ERROR_INVALID_HANDLE); RET(0, 5); }
    while (got < n) {
        ssize_t r = read(fd, buf + got, n - got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) break;
        got += (uint32_t)r;
    }
    if (pr) *pr = got;
    RET(1, 5);                                           /* EOF is success with 0 read */
}
static void k_WriteFile(void) {                          /* (h,buf,n,&written,ovl) */
    int fd = fd_of(A32(0));
    const uint8_t *buf = (const uint8_t *)APTR(1);
    uint32_t n = A32(2), *pw = (uint32_t *)APTR(3), put = 0;
    if (pw) *pw = 0;
    if (fd < 0) { hle_set_last_error(ERROR_INVALID_HANDLE); RET(0, 5); }
    while (put < n) {
        ssize_t r = write(fd, buf + put, n - put);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) break;
        put += (uint32_t)r;
    }
    if (pw) *pw = put;
    RET(put == n ? 1u : 0u, 5);
}
static void k_SetFilePointer(void) {                     /* (h,dist,&distHigh,method) -> new pos */
    int fd = fd_of(A32(0));
    int64_t dist = (int32_t)A32(1);
    uint32_t *hi = (uint32_t *)APTR(2), method = A32(3);
    if (hi) dist = (int64_t)(((uint64_t)*hi << 32) | A32(1));
    off_t r = fd >= 0 ? lseek(fd, (off_t)dist, method == 1 ? SEEK_CUR : method == 2 ? SEEK_END : SEEK_SET) : -1;
    if (r < 0) { hle_set_last_error(fd < 0 ? ERROR_INVALID_HANDLE : 131u /* NEGATIVE_SEEK */); RET(INVALID_HANDLE, 4); }
    if (hi) *hi = (uint32_t)((uint64_t)r >> 32);
    hle_set_last_error(0);
    RET((uint32_t)r, 4);
}
static void k_SetEndOfFile(void) {
    int fd = fd_of(A32(0));
    off_t pos = fd >= 0 ? lseek(fd, 0, SEEK_CUR) : -1;
    RET(pos >= 0 && ftruncate(fd, pos) == 0 ? 1u : 0u, 1);
}
static void k_FlushFileBuffers(void) { int fd = fd_of(A32(0)); RET(fd >= 0 ? 1u : 0u, 1); }
static void k_GetFileSize(void) {                        /* (h,&high) -> low */
    int fd = fd_of(A32(0));
    uint32_t *hi = (uint32_t *)APTR(1);
    struct stat st;
    if (fd < 0 || fstat(fd, &st) != 0) { hle_set_last_error(ERROR_INVALID_HANDLE); RET(INVALID_HANDLE, 2); }
    if (hi) *hi = (uint32_t)((uint64_t)st.st_size >> 32);
    hle_set_last_error(0);
    RET((uint32_t)st.st_size, 2);
}
/* CloseHandle: any kernel object (its type's closer runs); pseudo-handles and
 * the std handles are not objects and close successfully. */
static void k_CloseHandle(void) {
    uint32_t h = A32(0);
    if (hle_handle_close(h) || h == 0xFFFFFFFFu || h == 0xFFFFFFFEu || (h >= 0x11u && h <= 0x13u)) RET(1, 1);
    hle_set_last_error(ERROR_INVALID_HANDLE);
    RET(0, 1);
}
static void k_GetFileType(void) {
    uint32_t h = A32(0);
    if (h >= 0x11u && h <= 0x13u) RET(isatty(fd_of(h)) ? 2u : 1u, 1);   /* CHAR / DISK */
    RET(hle_handle_obj(h, HLE_H_FILE) ? 1u : 0u, 1);
}

/* ---- FILETIME (100 ns since 1601) and SYSTEMTIME ---- */
#define EPOCH_DIFF 11644473600ull
static uint64_t unix_to_ft(time_t s, long ns) { return ((uint64_t)s + EPOCH_DIFF) * 10000000ull + (uint64_t)ns / 100u; }
static void put_ft(uint32_t at, uint64_t ft) { if (at) MEM32(at) = (uint32_t)ft, MEM32(at + 4) = (uint32_t)(ft >> 32); }
static uint64_t get_ft(uint32_t at) { return at ? (uint64_t)MEM32(at) | (uint64_t)MEM32(at + 4) << 32 : 0; }

static void put_systemtime(uint32_t st, const struct tm *t, int ms) {
    uint16_t *w = (uint16_t *)hle_ptr(st);
    w[0] = (uint16_t)(t->tm_year + 1900), w[1] = (uint16_t)(t->tm_mon + 1), w[2] = (uint16_t)t->tm_wday;
    w[3] = (uint16_t)t->tm_mday, w[4] = (uint16_t)t->tm_hour, w[5] = (uint16_t)t->tm_min;
    w[6] = (uint16_t)t->tm_sec, w[7] = (uint16_t)ms;
}

/* These three return a time, so with the virtual clock on they report the
 * replay's time rather than the host's - otherwise two runs of the same input
 * still differ.  With it off they are the real clock, untouched: hle_clock
 * speaks milliseconds, and truncating the real reading to 32 bits would turn
 * "now" into 1970. */
static void k_GetSystemTime(void) {
    struct timespec ts; struct tm t;
    clock_gettime(CLOCK_REALTIME, &ts);
    long ms = ts.tv_nsec / 1000000;
    if (hle_clock_virtual()) { ts.tv_sec = (time_t)(hle_clock_ms() / 1000u); ms = (long)(hle_clock_ms() % 1000u); }
    gmtime_r(&ts.tv_sec, &t);
    put_systemtime(A32(0), &t, (int)ms);
    RETV(1);
}
static void k_GetLocalTime(void) {
    struct timespec ts; struct tm t;
    clock_gettime(CLOCK_REALTIME, &ts);
    long ms = ts.tv_nsec / 1000000;
    if (hle_clock_virtual()) { ts.tv_sec = (time_t)(hle_clock_ms() / 1000u); ms = (long)(hle_clock_ms() % 1000u); }
    localtime_r(&ts.tv_sec, &t);
    put_systemtime(A32(0), &t, (int)ms);
    RETV(1);
}
static void k_GetSystemTimeAsFileTime(void) {
    struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts);
    if (hle_clock_virtual()) {
        uint32_t ms = hle_clock_ms();
        put_ft(A32(0), unix_to_ft((time_t)(ms / 1000u), (long)(ms % 1000u) * 1000000L));
    } else {
        put_ft(A32(0), unix_to_ft(ts.tv_sec, ts.tv_nsec));
    }
    RETV(1);
}
static void k_FileTimeToSystemTime(void) {               /* (&ft, &st) */
    uint64_t ft = get_ft(A32(0));
    time_t s = (time_t)(ft / 10000000ull - EPOCH_DIFF);
    struct tm t; gmtime_r(&s, &t);
    put_systemtime(A32(1), &t, (int)(ft % 10000000ull / 10000u));
    RET(1, 2);
}
static void k_SystemTimeToFileTime(void) {               /* (&st, &ft) */
    const uint16_t *w = (const uint16_t *)APTR(0);
    struct tm t = { 0 };
    t.tm_year = w[0] - 1900, t.tm_mon = w[1] - 1, t.tm_mday = w[3];
    t.tm_hour = w[4], t.tm_min = w[5], t.tm_sec = w[6];
    time_t s = timegm(&t);
    put_ft(A32(1), unix_to_ft(s, (long)w[7] * 1000000L));
    RET(1, 2);
}
static int32_t tz_bias_s(void) {                         /* local - UTC, seconds */
    time_t now = time(NULL); struct tm t; localtime_r(&now, &t);
    return (int32_t)t.tm_gmtoff;
}
static void k_FileTimeToLocalFileTime(void) { put_ft(A32(1), get_ft(A32(0)) + (int64_t)tz_bias_s() * 10000000); RET(1, 2); }
static void k_LocalFileTimeToFileTime(void) { put_ft(A32(1), get_ft(A32(0)) - (int64_t)tz_bias_s() * 10000000); RET(1, 2); }
static void k_CompareFileTime(void) {
    uint64_t a = get_ft(A32(0)), b = get_ft(A32(1));
    RET(a < b ? 0xFFFFFFFFu : a > b ? 1u : 0u, 2);
}
static void k_DosDateTimeToFileTime(void) {              /* (date, time, &ft) */
    uint32_t d = A32(0) & 0xFFFF, tm_ = A32(1) & 0xFFFF;
    struct tm t = { 0 };
    t.tm_year = (int)(d >> 9) + 80, t.tm_mon = (int)((d >> 5) & 15) - 1, t.tm_mday = (int)(d & 31);
    t.tm_hour = (int)(tm_ >> 11), t.tm_min = (int)((tm_ >> 5) & 63), t.tm_sec = (int)(tm_ & 31) * 2;
    put_ft(A32(2), unix_to_ft(timegm(&t), 0));
    RET(1, 3);
}
static void k_FileTimeToDosDateTime(void) {              /* (&ft, &date, &time) */
    uint64_t ft = get_ft(A32(0));
    time_t s = (time_t)(ft / 10000000ull - EPOCH_DIFF);
    struct tm t; gmtime_r(&s, &t);
    if (A32(1)) MEM16(A32(1)) = (uint16_t)(((t.tm_year - 80) << 9) | ((t.tm_mon + 1) << 5) | t.tm_mday);
    if (A32(2)) MEM16(A32(2)) = (uint16_t)((t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec / 2));
    RET(1, 3);
}
static void k_GetTimeZoneInformation(void) {             /* (&TIME_ZONE_INFORMATION) 172 bytes */
    uint8_t *z = (uint8_t *)APTR(0);
    if (z) { memset(z, 0, 172); *(int32_t *)z = -tz_bias_s() / 60; }
    RET(0u, 1);                                          /* TIME_ZONE_ID_UNKNOWN: no DST rule */
}

static void k_GetFileTime(void) {                        /* (h, &create, &access, &write) */
    int fd = fd_of(A32(0));
    struct stat st;
    if (fd < 0 || fstat(fd, &st) != 0) { hle_set_last_error(ERROR_INVALID_HANDLE); RET(0, 4); }
    put_ft(A32(1), unix_to_ft(st.st_mtim.tv_sec, st.st_mtim.tv_nsec));
    put_ft(A32(2), unix_to_ft(st.st_atim.tv_sec, st.st_atim.tv_nsec));
    put_ft(A32(3), unix_to_ft(st.st_mtim.tv_sec, st.st_mtim.tv_nsec));
    RET(1, 4);
}
static void k_SetFileTime(void) {                        /* (h, &create, &access, &write) */
    int fd = fd_of(A32(0));
    struct timespec ts[2] = { { 0, UTIME_OMIT }, { 0, UTIME_OMIT } };
    for (int i = 0; i < 2; i++) {
        uint32_t at = A32(2 + i);
        if (!at) continue;
        uint64_t ft = get_ft(at);
        ts[i].tv_sec = (time_t)(ft / 10000000ull - EPOCH_DIFF), ts[i].tv_nsec = (long)(ft % 10000000ull) * 100;
    }
    RET(fd >= 0 && futimens(fd, ts) == 0 ? 1u : 0u, 4);
}

static uint32_t attrs_of(const struct stat *st) {
    uint32_t a = S_ISDIR(st->st_mode) ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
    if (!(st->st_mode & S_IWUSR)) a |= FILE_ATTRIBUTE_READONLY;
    return a;
}
static void k_GetFileInformationByHandle(void) {         /* (h, &BY_HANDLE_FILE_INFORMATION) 52 bytes */
    int fd = fd_of(A32(0));
    uint32_t o = A32(1);
    struct stat st;
    if (fd < 0 || fstat(fd, &st) != 0) { hle_set_last_error(ERROR_INVALID_HANDLE); RET(0, 2); }
    memset(hle_ptr(o), 0, 52);
    MEM32(o) = attrs_of(&st);
    put_ft(o + 4, unix_to_ft(st.st_mtim.tv_sec, st.st_mtim.tv_nsec));
    put_ft(o + 12, unix_to_ft(st.st_atim.tv_sec, st.st_atim.tv_nsec));
    put_ft(o + 20, unix_to_ft(st.st_mtim.tv_sec, st.st_mtim.tv_nsec));
    MEM32(o + 28) = (uint32_t)st.st_dev;
    MEM32(o + 32) = (uint32_t)((uint64_t)st.st_size >> 32), MEM32(o + 36) = (uint32_t)st.st_size;
    MEM32(o + 40) = (uint32_t)st.st_nlink;
    MEM32(o + 44) = (uint32_t)((uint64_t)st.st_ino >> 32), MEM32(o + 48) = (uint32_t)st.st_ino;
    RET(1, 2);
}

static void k_GetFileAttributesA(void) {
    char path[1024];
    struct stat st;
    if (!hle_host_path(ASTR(0), path, sizeof path) || stat(path, &st) != 0) {
        hle_set_last_error(ERROR_FILE_NOT_FOUND);
        RET(0xFFFFFFFFu, 1);
    }
    RET(attrs_of(&st), 1);
}
static void k_GetFileAttributesW(void) {
    char name[1024], path[1024];
    const uint16_t *w = (const uint16_t *)APTR(0);
    if (!w || !hle_wide_to_ansi(w, name, sizeof name) || !hle_host_path(name, path, sizeof path)) {
        hle_set_last_error(ERROR_FILE_NOT_FOUND);
        RET(0xFFFFFFFFu, 1);
    }
    struct stat st;
    if (stat(path, &st) != 0) { hle_set_last_error(ERROR_FILE_NOT_FOUND); RET(0xFFFFFFFFu, 1); }
    RET(attrs_of(&st), 1);
}
/* ---- SetFileAttributesA/W ----
 * SetFileAttributes replaces the file's attribute set: a requested READONLY
 * makes it unwritable, and its absence makes it writable (NORMAL means "no
 * other attributes", and is valid only on its own). The other settable Windows
 * bits are accepted but have no POSIX counterpart, so they are not stored;
 * DIRECTORY, REPARSE_POINT, SPARSE_FILE, DEVICE and the like cannot be set
 * through this API on Windows either, and are rejected like Windows does.
 * The W form takes a UTF-16 path and shares the ANSI path mapping, so a guest
 * `C:\Dir\File.TXT` lands on the same host file as CreateFileW would open. */
static uint32_t set_file_attrs(const char *name, uint32_t attrs) {
    if (!name || !*name) { hle_set_last_error(ERROR_PATH_NOT_FOUND); return 0; }
    const uint32_t settable = FILE_ATTRIBUTE_READONLY | FILE_ATTRIBUTE_HIDDEN |
        FILE_ATTRIBUTE_SYSTEM | FILE_ATTRIBUTE_ARCHIVE | FILE_ATTRIBUTE_NORMAL |
        FILE_ATTRIBUTE_TEMPORARY | FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_NOT_CONTENT_INDEXED;
    if ((attrs & ~settable) ||
        ((attrs & FILE_ATTRIBUTE_NORMAL) && (attrs & ~FILE_ATTRIBUTE_NORMAL))) {
        hle_set_last_error(ERROR_INVALID_PARAMETER);
        return 0;
    }
    char path[1024];
    if (!hle_host_path(name, path, sizeof path)) {
        hle_set_last_error(ERROR_FILE_NOT_FOUND);
        return 0;
    }
    /* A file under a mod's base is changed in the overlay, as a write to it
     * would be; keep copies the game's file there first when it has no copy. */
    hle_host_path_for_write(name, path, sizeof path, 1);

    struct stat st;
    if (stat(path, &st) != 0) {
        hle_set_last_error(errno == EACCES || errno == EPERM ? ERROR_ACCESS_DENIED : errno_to_win(errno));
        return 0;
    }
    mode_t mode = st.st_mode;
    if (attrs & FILE_ATTRIBUTE_READONLY) mode &= ~(mode_t)(S_IWUSR | S_IWGRP | S_IWOTH);
    else mode |= (mode_t)S_IWUSR;                    /* attrs_of reads the owner's write bit */
    if (mode != st.st_mode && chmod(path, mode) != 0) {
        hle_set_last_error(errno == EROFS ? ERROR_WRITE_PROTECT : errno_to_win(errno));
        return 0;
    }
    hle_set_last_error(0);
    return 1;
}
static void k_SetFileAttributesA(void) { RET(set_file_attrs(ASTR(0), A32(1)), 2); }
static void k_SetFileAttributesW(void) {
    const uint16_t *w = (const uint16_t *)APTR(0);
    char name[1024];
    if (!w || !*w) { hle_set_last_error(ERROR_PATH_NOT_FOUND); RET(0, 2); }
    if (!hle_wide_to_ansi(w, name, sizeof name)) { hle_set_last_error(ERROR_INVALID_NAME); RET(0, 2); }
    RET(set_file_attrs(name, A32(1)), 2);
}
static void k_CreateDirectoryA(void) {
    char path[1024];
    hle_host_path_for_write(ASTR(0), path, sizeof path, 0);
    if (mkdir(path, 0755) == 0) RET(1, 2);
    hle_set_last_error(errno == EEXIST ? ERROR_ALREADY_EXISTS : errno_to_win(errno));
    RET(0, 2);
}
static void k_CreateDirectoryW(void) {
    char name[1024], path[1024];
    const uint16_t *w = (const uint16_t *)APTR(0);
    if (!w || !hle_wide_to_ansi(w, name, sizeof name)) { hle_set_last_error(ERROR_INVALID_NAME); RET(0, 2); }
    hle_host_path_for_write(name, path, sizeof path, 0);
    if (mkdir(path, 0755) == 0) RET(1, 2);
    hle_set_last_error(errno == EEXIST ? ERROR_ALREADY_EXISTS : errno_to_win(errno));
    RET(0, 2);
}
static void k_RemoveDirectoryA(void) {
    char path[1024];
    hle_host_path(ASTR(0), path, sizeof path);
    if (rmdir(path) == 0) RET(1, 1);
    hle_set_last_error(errno_to_win(errno));
    RET(0, 1);
}
static void k_DeleteFileA(void) {
    char path[1024];
    if (hle_host_path(ASTR(0), path, sizeof path) && unlink(path) == 0) RET(1, 1);
    hle_set_last_error(ERROR_FILE_NOT_FOUND);
    RET(0, 1);
}
static void k_MoveFileA(void) {
    char a[1024], b[1024];
    hle_host_path(ASTR(0), a, sizeof a);
    hle_host_path(ASTR(1), b, sizeof b);
    if (rename(a, b) == 0) RET(1, 2);
    hle_set_last_error(errno_to_win(errno));
    RET(0, 2);
}
/* MoveFileExA(src, dst, flags): MOVEFILE_REPLACE_EXISTING (1) lets rename
 * clobber the destination, which it already does; MOVEFILE_COPY_ALLOWED (2)
 * across filesystems becomes a copy-then-delete, since rename(2) refuses to
 * cross a mount. MOVEFILE_DELAY_UNTIL_REBOOT (4) has no meaning here and is
 * refused rather than silently treated as an immediate move. */
static uint32_t move_file_ex(const char *src, const char *dst, uint32_t flags) {
    char a[1024], b[1024];
    if (flags & 4u) { hle_set_last_error(120u); return 0; }   /* ERROR_CALL_NOT_IMPLEMENTED */
    hle_host_path(src, a, sizeof a);
    hle_host_path(dst, b, sizeof b);
    if (rename(a, b) == 0) return 1;
    if (flags & 2u) {                                     /* COPY_ALLOWED: copy, then remove the source */
        FILE *in = fopen(a, "rb"), *out = in ? fopen(b, "wb") : NULL;
        if (in && out) {
            char buf[65536]; size_t n; int ok = 1;
            while ((n = fread(buf, 1, sizeof buf, in)) > 0) if (fwrite(buf, 1, n, out) != n) { ok = 0; break; }
            fclose(out);
            if (ok) unlink(a);
            fclose(in);
            if (ok) return 1;
        } else { if (in) fclose(in); if (out) fclose(out); }
    }
    hle_set_last_error(errno_to_win(errno));
    return 0;
}
static void k_MoveFileExA(void) { RET(move_file_ex(ASTR(0), ASTR(1), A32(2)), 3); }
/* MoveFileExW shares MoveFileExA's body after codepage-1252 conversion of both
 * UTF-16 paths, so the two forms move the same guest file with the same flags.
 * The strings are bounded-scanned first so a bad W pointer cannot run off the
 * guest address space inside hle_wide_to_ansi. */
static void k_MoveFileExW(void) {
    uint32_t existing = A32(0), replacement = A32(1), n;
    char a[1024], b[1024];
    if (!u16_len(existing, &n) || !u16_len(replacement, &n) ||
        !hle_wide_to_ansi((const uint16_t *)APTR(0), a, sizeof a) ||
        !hle_wide_to_ansi((const uint16_t *)APTR(1), b, sizeof b)) {
        hle_set_last_error(ERROR_INVALID_NAME); RET(0, 3);
    }
    RET(move_file_ex(a, b, A32(2)), 3);
}
static void k_CopyFileA(void) {                          /* (src, dst, failIfExists) */
    char a[1024], b[1024];
    int ok = 0;
    hle_host_path(ASTR(0), a, sizeof a);
    int dst_exists = hle_host_path(ASTR(1), b, sizeof b);
    hle_host_path_for_write(ASTR(1), b, sizeof b, 0);
    if (A32(2) && dst_exists) { hle_set_last_error(ERROR_FILE_EXISTS); RET(0, 3); }
    int in = open(a, O_RDONLY | O_CLOEXEC), out = in >= 0 ? open(b, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644) : -1;
    if (in >= 0 && out >= 0) {
        char buf[65536];
        ssize_t r;
        ok = 1;
        while ((r = read(in, buf, sizeof buf)) > 0)
            if (write(out, buf, (size_t)r) != r) { ok = 0; break; }
    }
    if (in >= 0) close(in);
    if (out >= 0) close(out);
    if (!ok) hle_set_last_error(ERROR_FILE_NOT_FOUND);
    RET((uint32_t)ok, 3);
}

/* ---- FindFirstFileA / FindNextFileA ---- */
/* A directory search: the matching names, read at FindFirstFileA and handed
 * out in NTFS's order, by name without case (a game's load dialog
 * preselects the first; Linux's own order is the directory's). Under a mod's
 * overlay the overlay's names come with the game's, the overlay's copy of a
 * name winning. */
typedef struct { char name[260]; int base; } found_t;
typedef struct {
    DIR *d, *base;
    char dir[1024], base_dir[1024], mask[260];
    int dots;
    int wide;                    /* the caller asked for WIN32_FIND_DATAW */
    found_t *names;
    int n, at;
} hfind;
static void find_closer(void *o) {
    hfind *f = (hfind *)o;
    if (f->d) closedir(f->d);
    if (f->base) closedir(f->base);
    free(f->names);
    free(f);
}
static int by_name(const void *a, const void *b) {
    const char *x = ((const found_t *)a)->name, *y = ((const found_t *)b)->name;
    int dx = !strcmp(x, ".") ? 0 : !strcmp(x, "..") ? 1 : 2, dy = !strcmp(y, ".") ? 0 : !strcmp(y, "..") ? 1 : 2;
    return dx != dy ? dx - dy : strcasecmp(x, y);
}

/* Windows wildcard match, without case: * any run, ? one character; "*.*"
 * matches a name with no dot too. */
static int wild(const char *m, const char *s) {
    if (!strcmp(m, "*.*") || !strcmp(m, "*")) return 1;
    for (; *m; m++, s++) {
        if (*m == '*') {
            while (m[1] == '*') m++;
            if (!m[1]) return 1;
            for (; *s; s++) if (wild(m + 1, s)) return 1;
            return !strcmp(m + 1, ".*") || !strcmp(m + 1, ".");
        }
        if (!*s) return !strcmp(m, ".*") || !strcmp(m, ".");
        if (*m != '?' && (*m | 0x20) != (*s | 0x20) && *m != *s) return 0;
    }
    return !*s;
}

/* WIN32_FIND_DATAA: attrs, 3 FILETIMEs, size high/low, 2 reserved,
 * cFileName[260], cAlternateFileName[14]: 320 bytes.  The W form is the same
 * fields with WCHAR names: cFileName at 44 (520 bytes) and cAlternateFileName
 * at 564, so 592 bytes. */
static void fill_find(uint32_t o, const char *dir, const char *name, int wide) {
    char full[1300];
    struct stat st;
    snprintf(full, sizeof full, "%s/%s", dir, name);
    memset(hle_ptr(o), 0, wide ? 592 : 320);
    if (stat(full, &st) == 0) {
        MEM32(o) = attrs_of(&st);
        put_ft(o + 4, unix_to_ft(st.st_mtim.tv_sec, st.st_mtim.tv_nsec));
        put_ft(o + 12, unix_to_ft(st.st_atim.tv_sec, st.st_atim.tv_nsec));
        put_ft(o + 20, unix_to_ft(st.st_mtim.tv_sec, st.st_mtim.tv_nsec));
        MEM32(o + 28) = (uint32_t)((uint64_t)st.st_size >> 32), MEM32(o + 32) = (uint32_t)st.st_size;
    }
    if (!wide) { strncpy((char *)hle_ptr(o + 44), name, 259); return; }
    uint16_t wname[260];
    if (hle_ansi_to_wide(name, wname, 260)) {
        size_t len = strlen(name);
        memcpy(hle_ptr(o + 44), wname, (len + 1) * sizeof(uint16_t));
    }
}

static int listed(const hfind *f, const char *name) {
    for (int i = 0; i < f->n; i++)
        if (!strcasecmp(f->names[i].name, name)) return 1;
    return 0;
}
static void list_dir(hfind *f, DIR *d, int base) {
    struct dirent *e;
    while (d && (e = readdir(d))) {
        int dot = !strcmp(e->d_name, ".") || !strcmp(e->d_name, "..");
        if ((dot && (!f->dots || base)) || !wild(f->mask, e->d_name) || (base && listed(f, e->d_name))) continue;
        if (f->n % 64 == 0) f->names = (found_t *)realloc(f->names, sizeof *f->names * (size_t)(f->n + 64));
        snprintf(f->names[f->n].name, sizeof f->names[0].name, "%s", e->d_name);
        f->names[f->n++].base = base;
    }
}
static int find_next(hfind *f, uint32_t out) {
    if (f->at >= f->n) return 0;
    found_t *e = &f->names[f->at++];
    fill_find(out, e->base ? f->base_dir : f->dir, e->name, f->wide);
    return 1;
}

/* The body of FindFirstFileA/W: `pat` is the ANSI pattern, `wide` picks the
 * WIN32_FIND_DATA the handle will fill in. */
static uint32_t find_first(const char *what, const char *pat, uint32_t out, int wide) {
    static int closer_set;
    if (!closer_set) hle_handle_set_closer(HLE_H_FIND, find_closer), closer_set = 1;
    const char *sl = pat ? pat + strlen(pat) : NULL;
    if (!pat) return INVALID_HANDLE;
    while (sl > pat && sl[-1] != '\\' && sl[-1] != '/' && sl[-1] != ':') sl--;
    hfind *f = (hfind *)calloc(1, sizeof *f);
    f->wide = wide;
    char gdir[1024];
    snprintf(gdir, sizeof gdir, "%.*s", (int)(sl - pat), pat);
    if (!gdir[0]) snprintf(gdir, sizeof gdir, ".");
    hle_host_path_base(gdir, f->dir, sizeof f->dir);
    char overlay_dir[1024];
    if (hle_overlay_of(f->dir, overlay_dir, sizeof overlay_dir) == 1 && (f->base = opendir(f->dir))) {
        snprintf(f->base_dir, sizeof f->base_dir, "%s", f->dir);
        snprintf(f->dir, sizeof f->dir, "%s", overlay_dir);
    }
    snprintf(f->mask, sizeof f->mask, "%s", sl);
    f->dots = strchr(f->mask, '*') || strchr(f->mask, '?');
    /* no wildcard: the one name, found without case */
    if (!f->dots) {
        char one[1024];
        struct stat st;
        if (hle_host_path(pat, one, sizeof one) && stat(one, &st) == 0) {
            char *b = strrchr(one, '/');                 /* the overlay's or the game's, wherever it is */
            if (b) *b = 0;
            fill_find(out, b ? (one[0] ? one : "/") : ".", b ? b + 1 : one, wide);
            uint32_t h = hle_handle_alloc(HLE_H_FIND, f);
            return h ? h : INVALID_HANDLE;
        }
        free(f);
        if (hle_filetrace()) fprintf(stderr, "[file] %s \"%s\": not found\n", what, pat);
        hle_set_last_error(ERROR_FILE_NOT_FOUND);
        return INVALID_HANDLE;
    }
    f->d = opendir(f->dir);
    list_dir(f, f->d, 0);
    list_dir(f, f->base, 1);
    if (f->n) qsort(f->names, (size_t)f->n, sizeof *f->names, by_name);
    if (!f->d || !find_next(f, out)) {
        find_closer(f);
        if (hle_filetrace()) fprintf(stderr, "[file] %s \"%s\": not found\n", what, pat);
        hle_set_last_error(ERROR_FILE_NOT_FOUND);
        return INVALID_HANDLE;
    }
    if (hle_filetrace()) fprintf(stderr, "[file] %s \"%s\" in %s\n", what, pat, f->dir);
    uint32_t h = hle_handle_alloc(HLE_H_FIND, f);
    return h ? h : INVALID_HANDLE;
}
static void k_FindFirstFileA(void) {                     /* (pattern, &data) */
    RET(find_first("FindFirstFileA", ASTR(0), A32(1), 0), 2);
}
static void k_FindFirstFileW(void) {                     /* (pattern, &data) */
    const uint16_t *w = (const uint16_t *)APTR(0);
    char pat[1024];
    if (!w || !*w) { hle_set_last_error(ERROR_PATH_NOT_FOUND); RET(INVALID_HANDLE, 2); }
    if (!hle_wide_to_ansi(w, pat, sizeof pat)) { hle_set_last_error(ERROR_INVALID_NAME); RET(INVALID_HANDLE, 2); }
    RET(find_first("FindFirstFileW", pat, A32(1), 1), 2);
}
static void k_FindNextFileA(void) {
    hfind *f = (hfind *)hle_handle_obj(A32(0), HLE_H_FIND);
    if (f && find_next(f, A32(1))) RET(1, 2);
    hle_set_last_error(ERROR_NO_MORE_FILES);
    RET(0, 2);
}
static void k_FindNextFileW(void) {
    hfind *f = (hfind *)hle_handle_obj(A32(0), HLE_H_FIND);
    if (f && find_next(f, A32(1))) RET(1, 2);
    hle_set_last_error(ERROR_NO_MORE_FILES);
    RET(0, 2);
}
static void k_FindClose(void) { RET(hle_handle_close(A32(0)) ? 1u : 0u, 1); }

/* ---- disks and volumes ---- */
static void k_GetDiskFreeSpaceA(void) {                  /* (root, &spc, &bps, &free, &total) */
    /* At most 2 GB free: a 1990s game multiplies these in 32 bits. */
    if (A32(1)) MEM32(A32(1)) = 8;
    if (A32(2)) MEM32(A32(2)) = 512;
    if (A32(3)) MEM32(A32(3)) = 0x7FFFFu;
    if (A32(4)) MEM32(A32(4)) = 0xFFFFFu;
    RET(1, 5);
}
static void k_GetVolumeInformationA(void) {              /* (root, name, nameN, &serial, &maxComp, &flags, fs, fsN) */
    char *name = ASTR(1), *fs = ASTR(6);
    if (name && A32(2)) snprintf(name, A32(2), "%s", "HLE");
    if (A32(3)) MEM32(A32(3)) = 0x1234ABCDu;
    if (A32(4)) MEM32(A32(4)) = 255;
    if (A32(5)) MEM32(A32(5)) = 0x3u;                    /* case-sensitive search, case-preserved names */
    if (fs && A32(7)) snprintf(fs, A32(7), "%s", "NTFS");
    RET(1, 8);
}
/* DRIVE_FIXED for a mapped drive (and Z:), DRIVE_NO_ROOT_DIR otherwise: so a
 * scan for a CD-ROM finds none. */
static void k_GetDriveTypeA(void) {
    const char *r = ASTR(0);
    char path[16], host[1024];
    if (!r || !*r) RET(3u, 1);
    snprintf(path, sizeof path, "%c:\\", r[0]);
    RET(hle_host_path(path, host, sizeof host) && host[0] ? 3u : 1u, 1);
}

/* ---- .ini profile: a minimal reader/writer over a flat key=value file ----
 * Enough for a game remembering settings; sections are honoured on read. */
static FILE *ini_open(const char *file, const char *mode) {
    char path[1024];
    if (!file) return NULL;
    hle_host_path(file, path, sizeof path);
    return fopen(path, mode);
}
static int ini_find_section(FILE *f, const char *section) {
    char line[512]; char want[256];
    snprintf(want, sizeof want, "[%s]", section);
    rewind(f);
    while (fgets(line, sizeof line, f))
        if (line[0] == '[' && !strncasecmp(line, want, strlen(want))) return 1;
    return 0;
}
static void k_GetPrivateProfileStringA(void) {           /* (sec,key,def,buf,size,file) */
    const char *sec = ASTR(0), *key = ASTR(1), *def = ASTR(2);
    char *buf = ASTR(3); uint32_t size = A32(4); const char *file = ASTR(5);
    const char *val = def ? def : "";
    char line[512];
    FILE *f = ini_open(file, "r");
    if (f && sec && key && ini_find_section(f, sec)) {
        size_t kl = strlen(key);
        while (fgets(line, sizeof line, f)) {
            if (line[0] == '[') break;                   /* next section */
            if (!strncasecmp(line, key, kl) && line[kl] == '=') {
                char *v = line + kl + 1, *nl = strpbrk(v, "\r\n"); if (nl) *nl = 0;
                static char out[512]; strncpy(out, v, sizeof out - 1); val = out; break;
            }
        }
    }
    if (f) fclose(f);
    if (buf && size) { strncpy(buf, val, size - 1); buf[size - 1] = 0; }
    RET(buf ? (uint32_t)strlen(buf) : 0u, 6);
}
static void k_GetPrivateProfileIntA(void) {              /* (sec,key,def,file) */
    char buf[64] = "", def[16];
    snprintf(def, sizeof def, "%d", (int)A32(2));
    const char *sec = ASTR(0), *key = ASTR(1), *file = ASTR(3);
    FILE *f = ini_open(file, "r"); char line[512]; int got = 0;
    if (f && sec && key && ini_find_section(f, sec)) {
        size_t kl = strlen(key);
        while (fgets(line, sizeof line, f)) {
            if (line[0] == '[') break;
            if (!strncasecmp(line, key, kl) && line[kl] == '=') { strncpy(buf, line + kl + 1, 63); got = 1; break; }
        }
    }
    if (f) fclose(f);
    RET((uint32_t)atoi(got ? buf : def), 4);
}
/* WritePrivateProfileStringA: append the key under a (possibly new) section;
 * the reader takes the first match. A title that rewrites keys gets the
 * in-place version when it needs it. */
static void k_WritePrivateProfileStringA(void) {         /* (sec,key,val,file) */
    const char *sec = ASTR(0), *key = ASTR(1), *val = ASTR(2), *file = ASTR(3);
    if (file && sec && key) {
        FILE *f = ini_open(file, "a");
        if (f) { fprintf(f, "[%s]\n%s=%s\n", sec, key, val ? val : ""); fclose(f); }
    }
    RET(1, 4);
}

/* ---- the Win16 _l* file family (an HFILE is our file HANDLE) ---- */
static uint32_t lopen_common(const char *name, int fl, int create) {
    char path[1024];
    hle_host_path(name, path, sizeof path);
    int fd = open(path, fl | O_CLOEXEC | (create ? (O_CREAT | O_TRUNC) : 0), 0644);
    if (hle_filetrace()) fprintf(stderr, "[file] _lopen \"%s\" -> %s %s\n", name, path, fd >= 0 ? "ok" : strerror(errno));
    if (fd < 0) { hle_set_last_error(errno_to_win(errno)); return INVALID_HANDLE; }
    return new_file(fd);
}
static void k__lopen(void) {                             /* (path, mode) OF_READ 0 / OF_WRITE 1 / OF_READWRITE 2 */
    uint32_t m = A32(1);
    RET(lopen_common(ASTR(0), m == 1 ? O_WRONLY : m == 2 ? O_RDWR : O_RDONLY, 0), 2);
}
static void k__lcreat(void) { RET(lopen_common(ASTR(0), O_RDWR, 1), 2); }   /* (path, attr) */
static void k__lread(void) {
    int fd = fd_of(A32(0)); if (fd < 0) RET(INVALID_HANDLE, 3);
    ssize_t r = read(fd, APTR(1), A32(2));
    RET(r < 0 ? INVALID_HANDLE : (uint32_t)r, 3);
}
static void k__lwrite(void) {
    int fd = fd_of(A32(0)); if (fd < 0) RET(INVALID_HANDLE, 3);
    ssize_t w = write(fd, APTR(1), A32(2));
    RET(w < 0 ? INVALID_HANDLE : (uint32_t)w, 3);
}
static void k__lclose(void) { hle_handle_close(A32(0)); RET(0, 1); }
static void k__llseek(void) {                            /* (hf, off, origin) */
    int fd = fd_of(A32(0)); if (fd < 0) RET(INVALID_HANDLE, 3);
    int o = (int)A32(2);
    off_t p = lseek(fd, (int32_t)A32(1), o == 1 ? SEEK_CUR : o == 2 ? SEEK_END : SEEK_SET);
    RET(p < 0 ? INVALID_HANDLE : (uint32_t)p, 3);
}

/* OpenFile(name, &OFSTRUCT, style): the Win16 open, still in KERNEL32. The
 * mode is the low two bits as for _lopen; OF_CREATE makes the file, OF_EXIST
 * opens and closes it, OF_DELETE removes it, OF_PARSE only fills the
 * OFSTRUCT. It answers an HFILE, or HFILE_ERROR with the error in nErrCode. */
#define OF_PARSE  0x0100u
#define OF_DELETE 0x0200u
#define OF_CREATE 0x1000u
#define OF_EXIST  0x4000u
static void k_OpenFile(void) {
    const char *name = ASTR(0);
    uint8_t *of = (uint8_t *)APTR(1);
    uint32_t style = A32(2), m = style & 3, h;
    char path[1024];
    if (of) {                                            /* cBytes, fFixedDisk, nErrCode, reserved, szPathName[128] */
        memset(of, 0, 136);
        of[0] = 136, of[1] = 1;
        snprintf((char *)of + 8, 128, "%s", name ? name : "");
    }
    if (!name) RET(INVALID_HANDLE, 3);
    if (style & OF_PARSE) RET(0, 3);
    if (style & OF_DELETE) {
        hle_host_path(name, path, sizeof path);
        RET(unlink(path) == 0 ? 1u : INVALID_HANDLE, 3);
    }
    h = lopen_common(name, (style & OF_CREATE) ? O_RDWR : m == 1 ? O_WRONLY : m == 2 ? O_RDWR : O_RDONLY,
                     (style & OF_CREATE) != 0);
    if (h == INVALID_HANDLE) {
        if (of) { uint16_t e = (uint16_t)hle_get_last_error(); memcpy(of + 2, &e, 2); }
        RET(INVALID_HANDLE, 3);
    }
    if (style & OF_EXIST) { hle_handle_close(h); RET(1, 3); }
    RET(h, 3);
}

/* GetFullPathNameA(name, buflen, buf, &filePart): a lexical absolute path (no
 * disk access). Relative names are taken against the current directory; the
 * result keeps whatever separators it had, which hle_path matches on either. */
static uint32_t full_path_lexical(const char *name, char *abs, size_t cap, char **part) {
    if (name && (name[0] == '/' || name[0] == '\\' || (name[0] && name[1] == ':')))
        snprintf(abs, cap, "%s", name);
    else { char cwd[768]; if (!getcwd(cwd, sizeof cwd)) cwd[0] = 0; snprintf(abs, cap, "%s/%s", cwd, name ? name : ""); }
    char *bs = strrchr(abs, '\\'), *fs = strrchr(abs, '/');
    *part = bs > fs ? bs : fs;
    return (uint32_t)strlen(abs);
}
static void k_GetFullPathNameA(void) {
    char abs[1024]; char *part;
    uint32_t n = full_path_lexical(ASTR(0), abs, sizeof abs, &part);
    char *buf = ASTR(2); uint32_t cap = A32(1);
    if (buf && cap) { strncpy(buf, abs, cap - 1); buf[cap - 1] = 0; }
    if (A32(3) && buf) {
        char *bs = strrchr(buf, '\\'), *fs = strrchr(buf, '/'), *p = bs > fs ? bs : fs;
        MEM32(A32(3)) = hle_va(p ? p + 1 : buf);
    }
    RET(buf ? (uint32_t)strlen(buf) : n, 4);
}
/* GetFullPathNameW: the wide form used by PE32 games. The length returned is
 * in UTF-16 code units, and the file-part pointer addresses the guest's wide
 * buffer, not the temporary ANSI string. */
static void k_GetFullPathNameW(void) {
    const uint16_t *wide = (const uint16_t *)APTR(0);
    char name[1024], abs[1024]; char *part;
    if (!wide || !hle_wide_to_ansi(wide, name, sizeof name)) { RET(0, 4); }
    uint32_t n = full_path_lexical(name, abs, sizeof abs, &part);
    uint32_t out = A32(2), cap = A32(1);
    uint32_t needed = n + 1;
    if (out && cap) {
        uint16_t *dst = (uint16_t *)hle_ptr(out);
        uint32_t i;
        for (i = 0; i < n && i < cap - 1; i++) dst[i] = (uint16_t)(unsigned char)abs[i];
        dst[i] = 0;
        if (A32(3)) MEM32(A32(3)) = out + 2u * (uint32_t)(part ? part + 1 - abs : 0);
        RET(n, 4);
    }
    if (A32(3)) MEM32(A32(3)) = 0;
    RET(needed, 4);
}

/* ---- GetShortPathNameW / GetLongPathNameW ----
 * The POSIX host has no 8.3 names, so these shims do real, deterministic path
 * text work rather than pretending a Windows filesystem exists:
 *   - Short: each component that is not already a valid 8.3 name is mangled to
 *     BASE~N.EXT (six base chars, a tilde index, three extension chars).
 *   - Long: a path with no ~N component is already long and is returned as-is.
 *     A ~N component is resolved by scanning that directory through
 *     hle_host_path() for an entry whose predicted 8.3 form matches; if none
 *     is found the call fails with ERROR_FILE_NOT_FOUND rather than returning
 *     the short text as if it had been expanded.
 * Both read the guest UTF-16 input through u16_len(), so a NULL, unterminated
 * or huge pointer fails closed instead of walking the guest address space. */
#define PATH_ANSI_MAX 1024

static int path_83_char(char c) {
    c = (char)toupper((unsigned char)c);
    return (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
           c == '$' || c == '%' || c == '\'' || c == '-' || c == '_' ||
           c == '@' || c == '~' || c == '`' || c == '!' || c == '(' ||
           c == ')' || c == '{' || c == '}' || c == '^' || c == '#' || c == '&';
}

/* Bounded copy of a NUL-terminated guest WCHAR path into an ANSI buffer. */
static int path_wide_to_ansi(uint32_t va, char *out, size_t cap) {
    uint32_t n, i;
    if (!va || !u16_len(va, &n) || n == 0 || n + 1u > cap) return 0;
    for (i = 0; i < n; i++) {
        uint16_t c = MEM16(va + i * 2u);
        if (c > 0x7Fu) return 0;               /* no honest 8.3 form for non-ASCII */
        out[i] = (char)c;
    }
    out[n] = 0;
    return 1;
}

/* Win32 size-probe / insufficient-buffer contract for a WCHAR output buffer.
 * Returns the text length (excluding NUL) on success (*wrote=1), or the
 * required size (including NUL) when out is NULL or cap is too small. */
static uint32_t path_write_wide(const char *s, uint32_t out, uint32_t cap, int *wrote) {
    uint32_t n = (uint32_t)strlen(s), need = n + 1u, i;
    *wrote = 0;
    if (!out || cap == 0) return need;
    if (cap < need) { hle_set_last_error(ERROR_INSUFFICIENT_BUFFER); return need; }
    for (i = 0; i < n; i++) MEM16(out + i * 2u) = (uint16_t)(unsigned char)s[i];
    MEM16(out + n * 2u) = 0;
    hle_set_last_error(0);
    *wrote = 1;
    return n;
}

/* Short 8.3 form of one component. *seq supplies the tilde index for mangled
 * components and advances for each one. out needs at least 16 bytes. */
static void path_short_component(const char *comp, size_t len, char *out, unsigned *seq) {
    size_t i, dot = len, nb = 0, ne = 0;
    char base[8], ext[4];
    int ok;
    if (len == 0) { out[0] = 0; return; }
    if ((len == 1 && comp[0] == '.') || (len == 2 && comp[0] == '.' && comp[1] == '.') ||
        (len == 2 && comp[1] == ':')) {        /* ".", "..", drive designator */
        memcpy(out, comp, len); out[len] = 0; return;
    }
    for (i = 1; i < len; i++) if (comp[i] == '.') dot = i;
    if (dot == len - 1) dot = len;             /* trailing dot: no extension */
    ok = (dot == len) ? (len <= 8)
                      : (dot >= 1 && dot <= 8 && len - dot - 1 >= 1 && len - dot - 1 <= 3);
    if (ok) {
        for (i = 0; i < dot; i++) if (!path_83_char(comp[i])) { ok = 0; break; }
        for (i = dot + 1; ok && i < len; i++) if (!path_83_char(comp[i])) ok = 0;
        if (ok) { memcpy(out, comp, len); out[len] = 0; return; }
    }
    for (i = 0; i < dot && nb < 6; i++) {
        char c = (char)toupper((unsigned char)comp[i]);
        if (path_83_char(c) && c != '~') base[nb++] = c;
    }
    for (i = dot + 1; i < len && ne < 3; i++) {
        char c = (char)toupper((unsigned char)comp[i]);
        if (path_83_char(c) && c != '~') ext[ne++] = c;
    }
    {
        unsigned n = *seq ? *seq : 1;
        if (n > 9) n = 9;
        (*seq)++;
        memcpy(out, base, nb); out[nb++] = '~'; out[nb++] = (char)('0' + n);
        if (ne) { out[nb++] = '.'; memcpy(out + nb, ext, ne); nb += ne; }
        out[nb] = 0;
    }
}

static void path_make_short(const char *in, char *out, size_t cap) {
    char comp[PATH_ANSI_MAX];
    size_t clen = 0, o = 0;
    unsigned seq = 0;
    const char *p = in;
    for (;;) {
        char c = *p++;
        if (c == '\\' || c == '/' || c == 0) {
            if (clen) {
                char sh[16];
                size_t sl;
                path_short_component(comp, clen, sh, &seq);
                sl = strlen(sh);
                if (sl && o + sl < cap) { memcpy(out + o, sh, sl); o += sl; }
                clen = 0;
            }
            if (c == 0) break;
            if (o + 1 < cap) out[o++] = '\\';
        } else if (clen + 1 < sizeof comp) {
            comp[clen++] = c;
        }
    }
    out[o] = 0;
}

static void k_GetShortPathNameW(void) {
    char in[PATH_ANSI_MAX], shortp[PATH_ANSI_MAX];
    int wrote;
    uint32_t r;
    if (!path_wide_to_ansi(A32(0), in, sizeof in)) {
        hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 3);
    }
    path_make_short(in, shortp, sizeof shortp);
    r = path_write_wide(shortp, A32(1), A32(2), &wrote);
    RET(r, 3);
}

/* Resolve one ~N component against the real entries of its guest directory. */
static int resolve_short_component(const char *dir_guest, const char *shortcomp,
                                   char *out, size_t cap) {
    char host[PATH_ANSI_MAX], pfx[8], ext[4];
    size_t np = 0, nx = 0;
    const char *dot, *q;
    DIR *d;
    struct dirent *e;
    int found = 0;
    if (!hle_host_path(dir_guest, host, sizeof host)) return 0;
    if (!host[0]) snprintf(host, sizeof host, "%s", "."); /* relative cwd */
    for (q = shortcomp; *q && *q != '~'; q++)
        if (np < 6 && path_83_char(*q)) pfx[np++] = (char)toupper((unsigned char)*q);
    dot = strrchr(shortcomp, '.');
    if (dot) for (q = dot + 1; *q && nx < 3; q++)
        if (path_83_char(*q)) ext[nx++] = (char)toupper((unsigned char)*q);
    d = opendir(host);
    if (!d) return 0;
    while ((e = readdir(d))) {
        char epfx[8], eext[4];
        const char *edot, *r;
        size_t k = 0, m = 0;
        if (e->d_name[0] == '.') continue;
        for (r = e->d_name; *r && *r != '.' && k < 6; r++)
            if (path_83_char(*r)) epfx[k++] = (char)toupper((unsigned char)*r);
        if (k != np || memcmp(epfx, pfx, np)) continue;
        edot = strrchr(e->d_name, '.');
        if (edot) for (r = edot + 1; *r && m < 3; r++)
            if (path_83_char(*r)) eext[m++] = (char)toupper((unsigned char)*r);
        if (nx && (m != nx || memcmp(eext, ext, nx))) continue;
        snprintf(out, cap, "%s", e->d_name);
        found = 1;
        break;
    }
    closedir(d);
    return found;
}

static int path_expand_long(const char *in, char *out, size_t cap) {
    char dir[PATH_ANSI_MAX], comp[PATH_ANSI_MAX];
    size_t clen = 0, o = 0, dlen;
    const char *p = in;
    if (!strchr(in, '~')) { snprintf(out, cap, "%s", in); return 1; }
    dir[0] = 0;
    for (;;) {
        char c = *p++;
        if (c == '\\' || c == '/' || c == 0) {
            if (clen) {
                char resolved[PATH_ANSI_MAX];
                size_t sl;
                if (memchr(comp, '~', clen)) {
                    comp[clen] = 0;
                    if (!resolve_short_component(dir, comp, resolved, sizeof resolved)) return 0;
                } else {
                    memcpy(resolved, comp, clen); resolved[clen] = 0;
                }
                sl = strlen(resolved);
                if (o + sl >= cap) return 0;
                memcpy(out + o, resolved, sl); o += sl; out[o] = 0;
                dlen = strlen(dir);
                if (dlen && dir[dlen - 1] != '\\' && dir[dlen - 1] != '/' &&
                    dlen + 1 < sizeof dir) dir[dlen++] = '\\';
                if (dlen + sl < sizeof dir) {
                    memcpy(dir + dlen, resolved, sl); dir[dlen + sl] = 0;
                }
                clen = 0;
            }
            if (c == 0) break;
            if (o + 1 < cap) out[o++] = '\\';
            out[o] = 0;
            dlen = strlen(dir);
            if (dlen + 1 < sizeof dir) { dir[dlen++] = '\\'; dir[dlen] = 0; }
        } else if (clen + 1 < sizeof comp) {
            comp[clen++] = c;
        }
    }
    return 1;
}

static void k_GetLongPathNameW(void) {
    char in[PATH_ANSI_MAX], longp[PATH_ANSI_MAX];
    int wrote;
    uint32_t r;
    if (!path_wide_to_ansi(A32(0), in, sizeof in)) {
        hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 3);
    }
    if (!path_expand_long(in, longp, sizeof longp)) {
        hle_set_last_error(ERROR_FILE_NOT_FOUND); RET(0, 3);
    }
    r = path_write_wide(longp, A32(1), A32(2), &wrote);
    RET(r, 3);
}

/* SearchPathA(path, file, ext, buflen, buf, &filePart): find a file on a search
 * path (NULL means the default: the current directory, then the Windows
 * directories). Each `;`-separated directory (or `\`-terminated run) is tried
 * in turn, with the extension appended when the name has none; the first that
 * exists is written to buf and its length returned, else 0. */
static void k_SearchPathA(void) {
    const char *path = ASTR(0), *file = ASTR(1);
    char buf[1024]; uint32_t n = 0;
    if (!file) RET(0, 6);
    int has_ext = 0;
    for (const char *p = file; *p; p++) if (*p == '.' || *p == '\\' || *p == '/') { has_ext = 1; break; }
    /* the candidate directories: the caller's, or the cwd + the notional Windows dirs */
    const char *segs[16]; int nseg = 0;
    char pathbuf[1024];
    if (path && *path) {
        snprintf(pathbuf, sizeof pathbuf, "%s", path);
        for (char *q = pathbuf, *s = q; ; q++) {
            if (*q == ';' || *q == 0) {
                char end = *q; *q = 0;
                if (nseg < 16 && *s) segs[nseg++] = s;
                *q = end;
                s = q + 1;
                if (!end) break;
            }
        }
    }
    const char *fixed[] = { "", "C:\\WINDOWS\\SYSTEM32", "C:\\WINDOWS" };
    int use_fixed = nseg == 0;
    for (int i = 0; i < (use_fixed ? 3 : nseg); i++) {
        const char *dir = use_fixed ? fixed[i] : segs[i];
        char cand[1024], host[1024];
        if (dir && *dir) snprintf(cand, sizeof cand, "%s\\%s", dir, file);
        else snprintf(cand, sizeof cand, "%s", file);
        if (!has_ext && ASTR(2)) snprintf(cand + strlen(cand), sizeof cand - strlen(cand), "%s", ASTR(2));
        if (hle_host_path(cand, host, sizeof host)) {
            snprintf(buf, sizeof buf, "%s", cand);
            n = (uint32_t)strlen(buf);
            break;
        }
    }
    if (!n) { hle_set_last_error(ERROR_FILE_NOT_FOUND); RET(0, 6); }
    uint32_t out = A32(3), outbuf = A32(4);
    if (outbuf && out) { strncpy((char *)hle_ptr(outbuf), buf, out - 1); ((char *)hle_ptr(outbuf))[out - 1] = 0; }
    if (A32(5) && outbuf) {
        char *bs = strrchr(buf, '\\'), *fs = strrchr(buf, '/'), *p = bs > fs ? bs : fs;
        MEM32(A32(5)) = outbuf + (uint32_t)((p ? p + 1 : buf) - buf);
    }
    RET(n, 6);
}

/* ---- file mappings ----
 * CreateFileMapping keeps a dup of the file descriptor (or, for
 * INVALID_HANDLE_VALUE, just a size: anonymous shared memory), a protection,
 * and an optional name. Named mappings are kept in a small process-local
 * table: names are case-insensitive like Windows kernel object names, a
 * second Create of the same name returns a *new* handle to the same object
 * with ERROR_ALREADY_EXISTS (a valid handle; the error is informational), and
 * OpenFileMapping fails with ERROR_FILE_NOT_FOUND when the name is absent.
 * The table holds one permanent anchor per name; each handle owns its own dup
 * of the descriptor, so closing one handle never frees the object behind
 * another (the host handle table runs a type's closer on every close).
 *
 * A view is an mmap over a guest-memory reservation: its base is a guest VA.
 * MapViewOfFileEx honours a caller-supplied base when it is free (64 KB
 * granularity, like Windows allocations). FlushViewOfFile msyncs a range of a
 * live view. GetMappedFileName reports the guest path the backing file was
 * opened with. Invalid handles, bad offsets/sizes, permissions and busy
 * addresses fail closed; no success is claimed that was not achieved. */
typedef struct {
    int      fd;            /* dup of the file descriptor; -1 for anonymous */
    uint64_t size;          /* maximum size of the mapping */
    uint32_t protect;       /* SEC_PAGE_* protection it was created with */
    char    *name;          /* owned, lower-cased; NULL for unnamed mappings */
    char    *file_path;     /* guest path of the backing file, when there is one */
} hmapping;

#define EX_MAX_NAMED_MAPS 64
#define EX_MAP_NAME_MAX   128
static struct { char name[EX_MAP_NAME_MAX]; hmapping *anchor; } g_named_maps[EX_MAX_NAMED_MAPS];

static void mapping_closer(void *p) {
    hmapping *m = (hmapping *)p;
    if (m->fd >= 0) close(m->fd);
    free(m->name);
    free(m->file_path);
    free(m);
}

/* An anonymous (pagefile-backed) mapping still needs real shared pages: two
 * views of one mapping must see each other's writes. An unlinked temp file is
 * a process-private shared object with no guest-visible name, which is exactly
 * what CreateFileMapping(INVALID_HANDLE_VALUE) means. */
static int anon_shared_fd(uint64_t size) {
    FILE *f = tmpfile();
    if (!f) return -1;
    int fd = fileno(f);
    int out = fd >= 0 ? dup(fd) : -1;
    fclose(f);                              /* closes fd, leaves out open */
    if (out < 0 || ftruncate(out, (off_t)size) != 0) {
        if (out >= 0) close(out);
        return -1;
    }
    return out;
}

/* A handle object is a private dup of an anchor: distinct handles to one named
 * mapping never share the object a CloseHandle would free. */
static hmapping *mapping_dup(const hmapping *src) {
    hmapping *m = (hmapping *)malloc(sizeof *m);
    if (!m) return NULL;
    m->fd = src->fd >= 0 ? dup(src->fd) : -1;
    if (src->fd >= 0 && m->fd < 0) { free(m); return NULL; }
    m->size = src->size;
    m->protect = src->protect;
    m->name = src->name ? strdup(src->name) : NULL;
    m->file_path = src->file_path ? strdup(src->file_path) : NULL;
    if ((src->name && !m->name) || (src->file_path && !m->file_path)) {
        mapping_closer(m); return NULL;
    }
    return m;
}

static hmapping *named_map_find(const char *name) {
    for (int i = 0; i < EX_MAX_NAMED_MAPS; i++)
        if (g_named_maps[i].anchor && !strcasecmp(g_named_maps[i].name, name))
            return g_named_maps[i].anchor;
    return NULL;
}
static hmapping *named_map_add(hmapping *anchor, const char *name) {
    if (strlen(name) >= EX_MAP_NAME_MAX) return NULL;
    for (int i = 0; i < EX_MAX_NAMED_MAPS; i++) {
        if (g_named_maps[i].anchor) continue;
        snprintf(g_named_maps[i].name, sizeof g_named_maps[i].name, "%s", name);
        for (char *p = g_named_maps[i].name; *p; p++) *p = (char)tolower((unsigned char)*p);
        g_named_maps[i].anchor = anchor;
        return anchor;
    }
    return NULL;
}

/* (hFile, sa, protect, sizeHigh, sizeLow, name) -> HANDLE. `name` is the ANSI
 * name (already converted from UTF-16 by the W entry point), or NULL. */
static uint32_t create_file_mapping(const char *name, uint32_t file, uint32_t protect, uint64_t size) {
    static int closer_set;
    if (!closer_set) hle_handle_set_closer(HLE_H_MAPPING, mapping_closer), closer_set = 1;
    if (name && !*name) name = NULL;                       /* "" is an unnamed mapping */
    if (!protect || (protect & ~SEC_VALID_MASK)) { hle_set_last_error(ERROR_INVALID_PARAMETER); return 0; }

    /* Opening an existing name reuses that object; the new size and file are
     * ignored, exactly as Windows CreateFileMapping does. */
    if (name) {
        hmapping *anchor = named_map_find(name);
        if (anchor) {
            hmapping *m = mapping_dup(anchor);
            if (!m) { hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
            uint32_t h = hle_handle_alloc(HLE_H_MAPPING, m);
            if (!h) { mapping_closer(m); hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
            hle_set_last_error(ERROR_ALREADY_EXISTS);
            return h;
        }
    }

    int fd = -1;
    char *file_path = NULL;
    if (file != INVALID_HANDLE) {
        int f = fd_of(file);
        struct stat st;
        if (f < 0 || fstat(f, &st) != 0) { hle_set_last_error(ERROR_INVALID_HANDLE); return 0; }
        if (!size) size = (uint64_t)st.st_size;
        fd = dup(f);
        if (fd < 0) { hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
        hfile *hf = (hfile *)hle_handle_obj(file, HLE_H_FILE);
        if (hf && hf->name) file_path = strdup(hf->name);
    }
    if (!size) {                                          /* an empty file/pagefile has nothing to map */
        if (fd >= 0) close(fd);
        free(file_path);
        hle_set_last_error(ERROR_FILE_INVALID);
        return 0;
    }
    if (fd < 0) {                                         /* anonymous: a shared, unlinked object */
        fd = anon_shared_fd(size);
        if (fd < 0) { hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
    }

    hmapping *anchor = (hmapping *)malloc(sizeof *anchor);
    if (!anchor) { if (fd >= 0) close(fd); free(file_path); hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
    anchor->fd = fd, anchor->size = size, anchor->protect = protect;
    anchor->name = NULL, anchor->file_path = file_path;
    hmapping *m = anchor;
    if (name) {
        anchor->name = (char *)malloc(EX_MAP_NAME_MAX);
        if (!anchor->name) { mapping_closer(anchor); hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
        snprintf(anchor->name, EX_MAP_NAME_MAX, "%s", name);
        for (char *p = anchor->name; *p; p++) *p = (char)tolower((unsigned char)*p);
        if (!named_map_add(anchor, anchor->name)) {       /* the name table is full */
            mapping_closer(anchor);
            hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY);
            return 0;
        }
        /* The table keeps this anchor; hand out a private dup of it. */
        m = mapping_dup(anchor);
        if (!m) { hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
    }
    uint32_t h = hle_handle_alloc(HLE_H_MAPPING, m);
    if (!h) { mapping_closer(m); hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
    hle_set_last_error(0);
    return h;
}

static void k_CreateFileMappingA(void) {                 /* (hFile, sa, protect, hi, lo, name) */
    RET(create_file_mapping(ASTR(5), A32(0), A32(2),
                            ((uint64_t)A32(3) << 32) | A32(4)), 6);
}
static void k_CreateFileMappingW(void) {                 /* UTF-16 name */
    char name[EX_MAP_NAME_MAX];
    const uint16_t *w = (const uint16_t *)APTR(5);
    if (w && !hle_wide_to_ansi(w, name, sizeof name)) { hle_set_last_error(ERROR_INVALID_NAME); RET(0, 6); }
    RET(create_file_mapping(w ? name : NULL, A32(0), A32(2),
                            ((uint64_t)A32(3) << 32) | A32(4)), 6);
}

/* (desiredAccess, inheritHandle, name) -> HANDLE. Only named mappings can be
 * opened; a write desire on a read-only mapping is denied. */
static uint32_t open_file_mapping(const char *name, uint32_t access) {
    if (!name || !*name) { hle_set_last_error(ERROR_INVALID_PARAMETER); return 0; }
    hmapping *anchor = named_map_find(name);
    if (!anchor) { hle_set_last_error(ERROR_FILE_NOT_FOUND); return 0; }
    if ((access & FILE_MAP_WRITE) && !(anchor->protect & SEC_WRITABLE_MASK)) {
        hle_set_last_error(ERROR_ACCESS_DENIED); return 0;
    }
    hmapping *m = mapping_dup(anchor);
    if (!m) { hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
    uint32_t h = hle_handle_alloc(HLE_H_MAPPING, m);
    if (!h) { mapping_closer(m); hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
    hle_set_last_error(0);
    return h;
}
static void k_OpenFileMappingA(void) { RET(open_file_mapping(ASTR(2), A32(0)), 3); }
static void k_OpenFileMappingW(void) {
    char name[EX_MAP_NAME_MAX];
    const uint16_t *w = (const uint16_t *)APTR(2);
    if (w && !hle_wide_to_ansi(w, name, sizeof name)) { hle_set_last_error(ERROR_INVALID_NAME); RET(0, 3); }
    RET(open_file_mapping(w ? name : NULL, A32(0)), 3);
}

#define MAX_VIEWS 64
static struct { uint32_t addr, len; char *file_path; } g_views[MAX_VIEWS];

/* (hMap, access, offHigh, offLow, bytes, base) -> guest VA or 0. base is 0 for
 * MapViewOfFile, a desired 64 KB-aligned guest address for MapViewOfFileEx. */
static uint32_t map_view(uint32_t hmap, uint32_t access, uint64_t off, uint32_t len, uint32_t base) {
    hmapping *m = (hmapping *)hle_handle_obj(hmap, HLE_H_MAPPING);
    if (!m) { hle_set_last_error(ERROR_INVALID_HANDLE); return 0; }
    /* Match Wine's interpretation: strip FILE_MAP_EXECUTE, then COPY is only
     * exclusive when neither READ nor WRITE was also requested; the standard
     * rights bits in FILE_MAP_ALL_ACCESS are ignored, not rejected. */
    uint32_t bits = access & ~FILE_MAP_EXECUTE;
    int copy = bits == FILE_MAP_COPY;
    if (!copy && !(bits & FILE_MAP_WRITE) && !(bits & FILE_MAP_READ)) {
        hle_set_last_error(ERROR_INVALID_PARAMETER); return 0;
    }
    if ((bits & FILE_MAP_WRITE) && !(m->protect & SEC_WRITABLE_MASK)) {
        hle_set_last_error(ERROR_ACCESS_DENIED); return 0;
    }
    if (off & 0xFFFu || off >= m->size) { hle_set_last_error(ERROR_INVALID_PARAMETER); return 0; }
    uint64_t avail = m->size - off;
    if (!len) len = avail > 0xFFFFFFFFull ? 0xFFFFFFFFu : (uint32_t)avail;
    if (len > avail) { hle_set_last_error(ERROR_INVALID_PARAMETER); return 0; }
    if (base & 0xFFFFu) { hle_set_last_error(ERROR_INVALID_PARAMETER); return 0; }

    /* the view lives in guest memory: reserve it there, then put the file over
     * it (a pagefile mapping is the reservation's own zeroed pages) */
    uint32_t va = hle_vm_reserve(base, len);
    if (!va || (base && va != (base & ~0xFFFFu))) {
        if (va) hle_vm_release(va, len);
        hle_set_last_error(base ? ERROR_INVALID_ADDRESS : ERROR_NOT_ENOUGH_MEMORY);
        return 0;
    }
    int prot = PROT_READ | (((bits & FILE_MAP_WRITE) || copy) ? PROT_WRITE : 0);
    if (m->fd >= 0 &&
        mmap(hle_ptr(va), len, prot, MAP_FIXED | (copy ? MAP_PRIVATE : MAP_SHARED), m->fd, (off_t)off) == MAP_FAILED) {
        hle_vm_release(va, len);
        hle_set_last_error(errno == EACCES ? ERROR_ACCESS_DENIED : ERROR_NOT_ENOUGH_MEMORY);
        return 0;
    }
    int slot = -1;
    for (int i = 0; i < MAX_VIEWS; i++) if (!g_views[i].addr) { slot = i; break; }
    if (slot < 0) {                                         /* out of view slots */
        hle_vm_release(va, len);
        hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY);
        return 0;
    }
    g_views[slot].addr = va;
    g_views[slot].len = len;
    g_views[slot].file_path = m->file_path ? strdup(m->file_path) : NULL;
    hle_set_last_error(0);
    return va;
}
static void k_MapViewOfFile(void) {                       /* (hMap, access, hi, lo, bytes) */
    RET(map_view(A32(0), A32(1), ((uint64_t)A32(2) << 32) | A32(3), A32(4), 0), 5);
}
static void k_MapViewOfFileEx(void) {                     /* (hMap, access, hi, lo, bytes, base) */
    RET(map_view(A32(0), A32(1), ((uint64_t)A32(2) << 32) | A32(3), A32(4), A32(5)), 6);
}
static int view_of(uint32_t addr) {
    for (int i = 0; i < MAX_VIEWS; i++)
        if (g_views[i].addr && addr >= g_views[i].addr && addr - g_views[i].addr < g_views[i].len)
            return i;
    return -1;
}
static void k_UnmapViewOfFile(void) {
    /* Windows (and Wine on Win9x compatibility) require the view's base, not
     * just any address inside it. */
    int i = -1;
    for (int k = 0; k < MAX_VIEWS; k++) if (g_views[k].addr && g_views[k].addr == A32(0)) { i = k; break; }
    if (i >= 0) {
        hle_vm_release(g_views[i].addr, g_views[i].len);
        free(g_views[i].file_path);
        memset(&g_views[i], 0, sizeof g_views[i]);
        RET(1, 1);
    }
    hle_set_last_error(ERROR_INVALID_ADDRESS);
    RET(0, 1);
}

/* FlushViewOfFile(base, bytes): 0 bytes means the whole view. msync needs a
 * page-aligned start; Windows keeps a section alive while a view exists, so
 * no file descriptor needs to be held here. */
static void k_FlushViewOfFile(void) {
    uint32_t va = A32(0), n = A32(1);
    int i = view_of(va);
    if (i < 0 || (n && n > g_views[i].len - (va - g_views[i].addr))) {
        hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 2);
    }
    uint32_t start = va & ~0xFFFu;
    uint32_t end = n ? (va + n + 0xFFFu) & ~0xFFFu : g_views[i].addr + g_views[i].len;
    uint32_t vstart = g_views[i].addr & ~0xFFFu;
    uint32_t vend = (g_views[i].addr + g_views[i].len + 0xFFFu) & ~0xFFFu;
    if (start < vstart) start = vstart;
    if (end > vend) end = vend;
    if (msync(hle_ptr(start), end - start, MS_SYNC) != 0) {
        hle_set_last_error(errno == EINVAL ? ERROR_INVALID_PARAMETER : ERROR_WRITE_FAULT);
        RET(0, 2);
    }
    hle_set_last_error(0);
    RET(1, 2);
}

/* GetMappedFileNameA/W(hProcess, addr, buffer, nSize) -> required length (no
 * NUL), 0 on failure, like Wine/psapi's copy_filename. Only the current
 * process is modelled; anonymous mappings have no name to report. */
static uint32_t mapped_file_name(uint32_t proc, uint32_t va, uint32_t dst, uint32_t n, int wide) {
    if (proc != CURRENT_PROCESS_HANDLE) { hle_set_last_error(ERROR_INVALID_HANDLE); return 0; }
    int i = view_of(va);
    if (i < 0 || !g_views[i].file_path) { hle_set_last_error(ERROR_INVALID_PARAMETER); return 0; }
    const char *path = g_views[i].file_path;
    uint32_t need = wide ? (uint32_t)(strlen(path) + 1) * 2u : (uint32_t)strlen(path) + 1u;
    if (dst && n >= need) {
        if (wide) hle_ansi_to_wide(path, (uint16_t *)hle_ptr(dst), n / 2u);
        else { memcpy(hle_ptr(dst), path, need); ((char *)hle_ptr(dst))[need - 1] = 0; }
    }
    hle_set_last_error(0);
    /* the return is a TCHAR count (characters), not a byte count, in both A and W */
    return (uint32_t)strlen(path);
}
static void k_GetMappedFileNameA(void) { RET(mapped_file_name(A32(0), A32(1), A32(2), A32(3), 0), 4); }
static void k_GetMappedFileNameW(void) { RET(mapped_file_name(A32(0), A32(1), A32(2), A32(3), 1), 4); }

/* ---- the process: one CPU of it, and no others to list ---- */
static void k_GetProcessAffinityMask(void) {             /* (hProcess, &process, &system) */
    if (A32(1)) MEM32(A32(1)) = 1;
    if (A32(2)) MEM32(A32(2)) = 1;
    RET(1, 3);
}
static void k_SetProcessAffinityMask(void) { RET(1, 2); }
static void k_CreateToolhelp32Snapshot(void) { hle_set_last_error(5); RET(INVALID_HANDLE, 2); }   /* ERROR_ACCESS_DENIED */
static void k_Process32First(void) { hle_set_last_error(18); RET(0, 2); }                     /* ERROR_NO_MORE_FILES */
static void k_OpenProcess(void) { hle_set_last_error(5); RET(0, 3); }
static void k_VirtualAllocEx(void) {                     /* (hProc, addr, size, type, prot) */
    if (A32(0) != 0xFFFFFFFFu) { hle_set_last_error(5); RET(0, 5); }
    RET(hle_virtual_alloc(A32(1), A32(2), A32(3), A32(4)), 5);
}
static void k_VirtualFreeEx(void) {                      /* (hProc, addr, size, type) */
    if (A32(0) != 0xFFFFFFFFu) { hle_set_last_error(5); RET(0, 4); }
    RET((uint32_t)hle_virtual_free(A32(1), A32(2), A32(3)), 4);
}
static void k_WriteProcessMemory(void) {                 /* (hProc, base, buf, size, &written) */
    uint32_t hp = A32(0), dst = A32(1), sz = A32(3), out = A32(4);
    const void *src = APTR(2);
    if (out) MEM32(out) = 0;
    if (hp != 0xFFFFFFFFu) { hle_set_last_error(5); RET(0, 5); }
    void *dp = dst ? hle_ptr(dst) : NULL;
    if (!dp || !src) { hle_set_last_error(87); RET(0, 5); }
    if (sz) memcpy(dp, src, sz);
    if (out) MEM32(out) = sz;
    RET(1, 5);
}
static void k_SetThreadContext(void) {                   /* (hThread, &CONTEXT): fail closed */
    hle_set_last_error(50);                              /* ERROR_NOT_SUPPORTED */
    RET(0, 2);
}

/* ---- additional filesystem, pipe, mapping, and volume shims ---- */
static void k_DeleteFileW(void) {
    const uint16_t *w = (const uint16_t *)APTR(0);
    char name[1024], path[1024];
    if (w && hle_wide_to_ansi(w, name, sizeof name) && hle_host_path(name, path, sizeof path) && unlink(path) == 0) RET(1, 1);
    hle_set_last_error(ERROR_FILE_NOT_FOUND);
    RET(0, 1);
}
static void k_FindFirstFileExW(void) {                   /* (nameW, infoLevel, &data, searchOp, filter, flags) */
    const uint16_t *w = (const uint16_t *)APTR(0);
    char pat[1024];
    if (!w || !*w) { hle_set_last_error(ERROR_PATH_NOT_FOUND); RET(INVALID_HANDLE, 6); }
    if (!hle_wide_to_ansi(w, pat, sizeof pat)) { hle_set_last_error(ERROR_INVALID_NAME); RET(INVALID_HANDLE, 6); }
    RET(find_first("FindFirstFileExW", pat, A32(2), 1), 6);
}
static uint32_t file_attrs_ex(const char *name, uint32_t out) {
    char path[1024];
    struct stat st;
    if (!name || !hle_host_path(name, path, sizeof path) || stat(path, &st) != 0) {
        hle_set_last_error(ERROR_FILE_NOT_FOUND);
        return 0;
    }
    if (out) {
        MEM32(out + 0) = attrs_of(&st);
        put_ft(out + 4, unix_to_ft(st.st_mtim.tv_sec, st.st_mtim.tv_nsec));
        put_ft(out + 12, unix_to_ft(st.st_atim.tv_sec, st.st_atim.tv_nsec));
        put_ft(out + 20, unix_to_ft(st.st_mtim.tv_sec, st.st_mtim.tv_nsec));
        MEM32(out + 28) = (uint32_t)((uint64_t)st.st_size >> 32);
        MEM32(out + 32) = (uint32_t)st.st_size;
    }
    return 1;
}
static void k_GetFileAttributesExA(void) { RET(file_attrs_ex(ASTR(0), A32(2)), 3); }
static void k_GetFileAttributesExW(void) {
    const uint16_t *w = (const uint16_t *)APTR(0);
    char name[1024];
    if (!w || !hle_wide_to_ansi(w, name, sizeof name)) { hle_set_last_error(ERROR_INVALID_NAME); RET(0, 3); }
    RET(file_attrs_ex(name, A32(2)), 3);
}
static void k_GetFileSizeEx(void) {                      /* (hFile, &LARGE_INTEGER) */
    int fd = fd_of(A32(0));
    uint32_t out = A32(1);
    struct stat st;
    if (fd < 0 || fstat(fd, &st) != 0) { hle_set_last_error(ERROR_INVALID_HANDLE); RET(0, 2); }
    uint64_t sz = (uint64_t)st.st_size;
    if (out) { MEM32(out) = (uint32_t)sz; MEM32(out + 4) = (uint32_t)(sz >> 32); }
    RET(1, 2);
}
static void k_SetFilePointerEx(void) {                 /* (hFile, distLo, distHi, &newPos, method) */
    int fd = fd_of(A32(0));
    if (fd < 0) { hle_set_last_error(ERROR_INVALID_HANDLE); RET(0, 5); }
    int64_t dist = (int64_t)((uint64_t)A32(1) | ((uint64_t)A32(2) << 32));
    int whence = A32(4) == 1 ? SEEK_CUR : A32(4) == 2 ? SEEK_END : SEEK_SET;
    off_t pos = lseek(fd, (off_t)dist, whence);
    if (pos < 0) { hle_set_last_error(errno_to_win(errno)); RET(0, 5); }
    if (A32(3)) {
        uint64_t u = (uint64_t)pos;
        MEM32(A32(3)) = (uint32_t)u;
        MEM32(A32(3) + 4) = (uint32_t)(u >> 32);
    }
    RET(1, 5);
}
static void k_SetHandleInformation(void) { RET(1, 3); }
static void k_LockFileEx(void) { RET(1, 6); }
static void k_GetDiskFreeSpaceExA(void) {                /* (dir, &avail, &total, &free) */
    uint64_t avail = 0x40000000ULL, total = 0x80000000ULL;
    if (A32(1)) { MEM32(A32(1)) = (uint32_t)avail; MEM32(A32(1) + 4) = (uint32_t)(avail >> 32); }
    if (A32(2)) { MEM32(A32(2)) = (uint32_t)total; MEM32(A32(2) + 4) = (uint32_t)(total >> 32); }
    if (A32(3)) { MEM32(A32(3)) = (uint32_t)avail; MEM32(A32(3) + 4) = (uint32_t)(avail >> 32); }
    RET(1, 4);
}
static void k_GetVolumeInformationW(void) {              /* (rootW, nameW, nameN, &serial, &maxComp, &flags, fsW, fsN) */
    uint16_t *nw = (uint16_t *)APTR(1), *fw = (uint16_t *)APTR(6);
    if (nw && A32(2)) hle_ansi_to_wide("HLE", nw, A32(2));
    if (A32(3)) MEM32(A32(3)) = 0x1234ABCDu;
    if (A32(4)) MEM32(A32(4)) = 255;
    if (A32(5)) MEM32(A32(5)) = 0x3u;
    if (fw && A32(7)) hle_ansi_to_wide("NTFS", fw, A32(7));
    RET(1, 8);
}
static void k_GetCompressedFileSizeA(void) {             /* (name, &high) */
    char path[1024];
    struct stat st;
    if (!ASTR(0) || !hle_host_path(ASTR(0), path, sizeof path) || stat(path, &st) != 0) {
        hle_set_last_error(ERROR_FILE_NOT_FOUND);
        RET(0xFFFFFFFFu, 2);
    }
    uint64_t sz = (uint64_t)st.st_size;
    if (A32(1)) MEM32(A32(1)) = (uint32_t)(sz >> 32);
    hle_set_last_error(0);
    RET((uint32_t)sz, 2);
}
static void k_FindFirstVolumeW(void)  { hle_set_last_error(ERROR_NO_MORE_FILES); RET(INVALID_HANDLE, 2); }
static void k_FindNextVolumeW(void)   { hle_set_last_error(ERROR_NO_MORE_FILES); RET(0, 3); }
static void k_FindVolumeClose(void)   { RET(1, 1); }
static void k_ReadDirectoryChangesW(void) { hle_set_last_error(120u); RET(0, 8); }
static void k_CreatePipe(void) {                         /* (&hRead, &hWrite, sa, size) */
    int fds[2];
    if (pipe(fds) != 0) { hle_set_last_error(errno_to_win(errno)); RET(0, 4); }
    uint32_t hr = new_file(fds[0]), hw = new_file(fds[1]);
    if (hr == INVALID_HANDLE || hw == INVALID_HANDLE) {
        if (hr != INVALID_HANDLE) hle_handle_close(hr); else close(fds[0]);
        if (hw != INVALID_HANDLE) hle_handle_close(hw); else close(fds[1]);
        RET(0, 4);
    }
    if (A32(0)) MEM32(A32(0)) = hr;
    if (A32(1)) MEM32(A32(1)) = hw;
    RET(1, 4);
}
static void k_CreateNamedPipeA(void) { hle_set_last_error(ERROR_ACCESS_DENIED); RET(INVALID_HANDLE, 8); }
static void k_ConnectNamedPipe(void) { hle_set_last_error(ERROR_INVALID_HANDLE); RET(0, 2); }
static void k_WaitNamedPipeA(void)   { hle_set_last_error(ERROR_FILE_NOT_FOUND); RET(0, 2); }
static void k_PeekNamedPipe(void)    { hle_set_last_error(ERROR_INVALID_HANDLE); RET(0, 6); }

/* ---- Vista+ name-based NLS: LCMapStringEx, GetLocaleInfoEx, CompareStringEx ----
 * The UTF-16, locale-name counterparts of kernel32_crt.c's LCMapStringA,
 * GetLocaleInfoA and CompareStringA. There are no NLS data files in this layer
 * and exactly one locale (en-US / LCID 0x0409, what the existing
 * GetUserDefaultLCID / GetLocaleInfoA answer), so:
 *   - NULL (LOCALE_NAME_USER_DEFAULT), "en", "en-US" and
 *     "!x-sys-default-locale" (LOCALE_NAME_SYSTEM_DEFAULT) select that locale;
 *   - L"" (LOCALE_NAME_INVARIANT) gives culture-free casing and ordinal
 *     comparison, but no locale *data* -- GetLocaleInfoEx fails for it;
 *   - any other name fails with ERROR_INVALID_PARAMETER. No locale data is
 *     invented for locales this layer cannot back.
 * Casing is Unicode *simple* mapping for ASCII, Latin-1, Greek and Cyrillic:
 * code points whose case mapping expands (e.g. U+0130) pass through unchanged.
 * A sort key here is an opaque stream of folded, big-endian UTF-16 code units
 * plus a 0 terminator; it sorts consistently with CompareStringEx below but is
 * NOT a Windows NLS weight table. Flags that need real collation data (kana /
 * width, Chinese, digits-as-numbers, SORT_STRINGSORT, LCMAP_HASH /
 * LCMAP_SORTHANDLE, version structures and sort handles) fail closed with
 * ERROR_INVALID_FLAGS / ERROR_INVALID_PARAMETER. NORM_IGNORENONSPACE ignores
 * U+0300..U+036F combining marks and NORM_IGNORESYMBOLS ignores ASCII
 * punctuation; precomposed accents are not decomposed.
 * Strings are UTF-16 *in guest memory*; counts are WCHARs (bytes for a sort
 * key). Failure is 0 with a Win32 last error: these APIs do not return
 * HRESULTs, so a caller that wants one must apply HRESULT_FROM_WIN32 itself.
 */
#define EX_ERROR_INVALID_PARAMETER   87u
#define EX_ERROR_NOT_ENOUGH_MEMORY   8u
#define EX_ERROR_INSUFFICIENT_BUFFER 122u
#define EX_ERROR_INVALID_FLAGS      1004u

#define EX_LCMAP_LOWERCASE         0x00000100u
#define EX_LCMAP_UPPERCASE         0x00000200u
#define EX_LCMAP_SORTKEY           0x00000400u
#define EX_LCMAP_BYTEREV           0x00000800u
#define EX_LCMAP_LINGUISTIC_CASING 0x01000000u
#define EX_NORM_IGNORECASE         0x00000001u
#define EX_NORM_IGNORENONSPACE     0x00000002u
#define EX_NORM_IGNORESYMBOLS      0x00000004u
#define EX_LINGUISTIC_IGNORECASE   0x00000010u
#define EX_LINGUISTIC_IGNOREDIACRITIC 0x00000020u
#define EX_NORM_LINGUISTIC_CASING  0x08000000u
#define EX_MAX_STRING 0x100000u                 /* guard for an unterminated guest string */

enum ex_locale_kind { EX_LOCALE_BAD = 0, EX_LOCALE_DEFAULT, EX_LOCALE_INVARIANT };

/* A locale name is an ASCII UTF-16 string; a non-ASCII unit cannot name one of
 * the two locales this layer knows. */
static int ex_wide_ascii(uint32_t va, char *out, size_t cap) {
    size_t i;
    if (!va) return 0;
    for (i = 0; i + 1 < cap; i++) {
        uint16_t c = MEM16(va + (uint32_t)i * 2u);
        if (!c) { out[i] = 0; return 1; }
        if (c > 0x7F) return 0;
        out[i] = (char)c;
    }
    return 0;
}
static enum ex_locale_kind ex_locale_of(uint32_t va, char *name, size_t cap) {
    if (!va) return EX_LOCALE_DEFAULT;          /* LOCALE_NAME_USER_DEFAULT */
    if (!ex_wide_ascii(va, name, cap)) return EX_LOCALE_BAD;
    if (!*name) return EX_LOCALE_INVARIANT;     /* LOCALE_NAME_INVARIANT */
    if (!strcasecmp(name, "en") || !strcasecmp(name, "en-us") ||
        !strcasecmp(name, "!x-sys-default-locale")) return EX_LOCALE_DEFAULT;
    return EX_LOCALE_BAD;
}
static int ex_wide_len(uint32_t va, uint32_t *out) {
    uint32_t i;
    for (i = 0; i < EX_MAX_STRING; i++)
        if (!MEM16(va + i * 2u)) { *out = i; return 1; }
    return 0;
}

static uint16_t ex_lower(uint16_t c) {
    if (c >= 'A' && c <= 'Z') return (uint16_t)(c + 32);
    if (c >= 0x00C0 && c <= 0x00DE && c != 0x00D7) return (uint16_t)(c + 32);
    if (c == 0x0178) return 0x00FF;
    if (c >= 0x0391 && c <= 0x03A1) return (uint16_t)(c + 32);
    if (c >= 0x03A3 && c <= 0x03AB) return (uint16_t)(c + 32);
    if (c >= 0x0410 && c <= 0x042F) return (uint16_t)(c + 32);
    if (c >= 0x0400 && c <= 0x040F) return (uint16_t)(c + 0x50);
    return c;
}
static uint16_t ex_upper(uint16_t c) {
    if (c >= 'a' && c <= 'z') return (uint16_t)(c - 32);
    if (c >= 0x00E0 && c <= 0x00FE && c != 0x00F7) return (uint16_t)(c - 32);
    if (c == 0x00FF) return 0x0178;
    if (c >= 0x03B1 && c <= 0x03C1) return (uint16_t)(c - 32);
    if (c >= 0x03C3 && c <= 0x03CB) return (uint16_t)(c - 32);
    if (c == 0x03C2) return 0x03A3;             /* final sigma */
    if (c >= 0x0430 && c <= 0x044F) return (uint16_t)(c - 32);
    if (c >= 0x0450 && c <= 0x045F) return (uint16_t)(c - 0x50);
    return c;
}
static int ex_is_letter(uint16_t c) {
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
           (c >= 0x00C0 && c <= 0x00FF && c != 0x00D7 && c != 0x00F7) ||
           (c >= 0x0391 && c <= 0x03CB) || (c >= 0x0400 && c <= 0x044F);
}
static int ex_is_alnum(uint16_t c) {
    return ex_is_letter(c) || (c >= '0' && c <= '9');
}
static int ex_is_sym(uint16_t c) {
    return c < 0x80 && ((c >= 0x21 && c <= 0x2F) || (c >= 0x3A && c <= 0x40) ||
                        (c >= 0x5B && c <= 0x60) || (c >= 0x7B && c <= 0x7E));
}
static int ex_ignore_unit(uint16_t c, uint32_t flags) {
    if ((flags & (EX_NORM_IGNORENONSPACE | EX_LINGUISTIC_IGNOREDIACRITIC)) &&
        c >= 0x0300 && c <= 0x036F) return 1;   /* combining diacritical marks */
    if ((flags & EX_NORM_IGNORESYMBOLS) && ex_is_sym(c)) return 1;
    return 0;
}
static uint16_t ex_cmp_unit(uint16_t c, uint32_t flags) {
    return (flags & (EX_NORM_IGNORECASE | EX_LINGUISTIC_IGNORECASE)) ? ex_lower(c) : c;
}

/* LCMapStringEx(locale, flags, src, cchSrc, dst, cchDest, version, reserved, sortHandle) */
static void k_LCMapStringEx(void) {
    uint32_t va = A32(0), flags = A32(1), src = A32(2), dst = A32(4);
    int n = (int)A32(3), dn = (int)A32(5);
    char name[64];
    uint32_t n_src, i, m = 0;
    enum ex_locale_kind loc;
    uint32_t known = EX_LCMAP_LOWERCASE | EX_LCMAP_UPPERCASE | EX_LCMAP_SORTKEY |
                     EX_LCMAP_BYTEREV | EX_LCMAP_LINGUISTIC_CASING | EX_NORM_IGNORECASE |
                     EX_NORM_IGNORENONSPACE | EX_NORM_IGNORESYMBOLS | EX_LINGUISTIC_IGNORECASE |
                     EX_LINGUISTIC_IGNOREDIACRITIC | EX_NORM_LINGUISTIC_CASING;
    if (!src || !n || dn < 0 || A32(6) || A32(7) || A32(8)) {
        hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 9);
    }
    if (flags & ~known) { hle_set_last_error(EX_ERROR_INVALID_FLAGS); RET(0, 9); }
    if ((flags & EX_LCMAP_SORTKEY) && (flags & (EX_LCMAP_LOWERCASE | EX_LCMAP_UPPERCASE))) {
        hle_set_last_error(EX_ERROR_INVALID_FLAGS); RET(0, 9);
    }
    if (!(flags & EX_LCMAP_SORTKEY) &&
        (flags & (EX_NORM_IGNORECASE | EX_LINGUISTIC_IGNORECASE | EX_NORM_LINGUISTIC_CASING))) {
        hle_set_last_error(EX_ERROR_INVALID_FLAGS); RET(0, 9);
    }
    loc = ex_locale_of(va, name, sizeof name);
    if (loc == EX_LOCALE_BAD) { hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 9); }
    if (n < 0) {
        if (!ex_wide_len(src, &n_src)) { hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 9); }
        n_src++;                                /* include the terminating NUL */
    } else {
        if ((uint32_t)n > EX_MAX_STRING) { hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 9); }
        n_src = (uint32_t)n;
    }
    (void)loc;                                  /* casing here is locale-independent */

    if (flags & EX_LCMAP_SORTKEY) {
        uint8_t *key = (uint8_t *)malloc((size_t)n_src * 2u + 1u);
        if (!key) { hle_set_last_error(EX_ERROR_NOT_ENOUGH_MEMORY); RET(0, 9); }
        for (i = 0; i < n_src; i++) {
            uint16_t c = MEM16(src + i * 2u);
            if (ex_ignore_unit(c, flags)) continue;
            c = ex_cmp_unit(c, flags);
            key[m++] = (uint8_t)(c >> 8);       /* big-endian code unit */
            key[m++] = (uint8_t)c;
        }
        key[m++] = 0;                           /* the opaque stream's terminator */
        if (flags & EX_LCMAP_BYTEREV)
            for (i = 0; i + 1 < m; i += 2) { uint8_t t = key[i]; key[i] = key[i + 1]; key[i + 1] = t; }
        if (!dn) { free(key); RET(m, 9); }
        if (dn < 0 || (uint32_t)dn < m || !dst) {
            free(key); hle_set_last_error(EX_ERROR_INSUFFICIENT_BUFFER); RET(0, 9);
        }
        for (i = 0; i < m; i++) MEM8(dst + i) = key[i];
        free(key);
        RET(m, 9);
    }

    {
        uint16_t *out = (uint16_t *)malloc((size_t)n_src * 2u);
        int title = (flags & (EX_LCMAP_LOWERCASE | EX_LCMAP_UPPERCASE)) ==
                    (EX_LCMAP_LOWERCASE | EX_LCMAP_UPPERCASE);
        int word_start = 1;
        if (!out) { hle_set_last_error(EX_ERROR_NOT_ENOUGH_MEMORY); RET(0, 9); }
        for (i = 0; i < n_src; i++) {
            uint16_t c = MEM16(src + i * 2u);
            if (ex_ignore_unit(c, flags)) continue;
            if (title) {
                c = ex_is_letter(c) ? (word_start ? ex_upper(c) : ex_lower(c)) : c;
                word_start = !ex_is_alnum(c);
            } else if (flags & EX_LCMAP_LOWERCASE) c = ex_lower(c);
            else if (flags & EX_LCMAP_UPPERCASE) c = ex_upper(c);
            if (flags & EX_LCMAP_BYTEREV) c = (uint16_t)((c >> 8) | (c << 8));
            out[m++] = c;
        }
        if (!dn) { free(out); RET(m, 9); }
        if ((uint32_t)dn < m || !dst) {
            free(out); hle_set_last_error(EX_ERROR_INSUFFICIENT_BUFFER); RET(0, 9);
        }
        for (i = 0; i < m; i++) MEM16(dst + i * 2u) = out[i];
        free(out);
        RET(m, 9);
    }
}

/* GetLocaleInfoEx(locale, LCType, data, cchData) */
static void k_GetLocaleInfoEx(void) {
    uint32_t va = A32(0), lctype = A32(1), data = A32(2);
    int dn = (int)A32(3), is_num = 0, hex = 0;
    char name[64], tmp[64];
    uint32_t num = 0;
    uint32_t t = lctype & 0x0FFFFFFFu;           /* NOUSEROVERRIDE/RETURN_NUMBER/etc. */
    const char *s = NULL;
    static const char *const day[] = { "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday" };
    static const char *const mon[] = { "January", "February", "March", "April", "May", "June", "July", "August",
                                       "September", "October", "November", "December" };
    if (dn < 0 || ex_locale_of(va, name, sizeof name) != EX_LOCALE_DEFAULT) {
        hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 4);
    }
    if ((lctype & 0x20000000u) && !t) {          /* RETURN_NUMBER needs an LCType */
        hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 4);
    }
    /* The one locale's values, mirroring kernel32_crt.c's GetLocaleInfoA table
     * (same LCID 0x0409), plus the name constants that Ex callers use. */
    switch (t) {
    case 0x01: case 0x09: num = 0x0409; is_num = 1; hex = 1; break; /* ILANGUAGE, IDEFAULTLANGUAGE */
    case 0x05: case 0x0A: num = 1; is_num = 1; break;              /* ICOUNTRY, IDEFAULTCOUNTRY */
    case 0x0B: num = 437; is_num = 1; break;                       /* IDEFAULTCODEPAGE */
    case 0x0D: num = 1; is_num = 1; break;                         /* IMEASURE */
    case 0x11: case 0x19: case 0x1A: num = 2; is_num = 1; break;   /* IDIGITS, ICURRDIGITS */
    case 0x12: num = 1; is_num = 1; break;                         /* ILZERO */
    case 0x1B: case 0x1C: case 0x21: case 0x22: case 0x23: case 0x24:
    case 0x25: case 0x26: case 0x27: num = 0; is_num = 1; break;
    case 0x1004: num = 1252; is_num = 1; break;                    /* IDEFAULTANSICODEPAGE */
    case 0x1009: num = 1; is_num = 1; break;                       /* ICALENDARTYPE */
    case 0x100C: num = 0; is_num = 1; break;                       /* IFIRSTDAYOFWEEK */
    default:
        if (t >= 0x2A && t <= 0x30) s = day[t - 0x2A];
        else if (t >= 0x31 && t <= 0x37) { snprintf(tmp, sizeof tmp, "%.3s", day[t - 0x31]); s = tmp; }
        else if (t >= 0x38 && t <= 0x43) s = mon[t - 0x38];
        else if (t >= 0x44 && t <= 0x4F) { snprintf(tmp, sizeof tmp, "%.3s", mon[t - 0x44]); s = tmp; }
        else switch (t) {
        case 0x02: case 0x04: case 0x1001: s = "English"; break;
        case 0x03: s = "ENU"; break;
        case 0x06: case 0x08: case 0x1002: s = "United States"; break;
        case 0x07: s = "USA"; break;
        case 0x0C: case 0x0F: case 0x17: s = ","; break;
        case 0x0E: case 0x16: s = "."; break;
        case 0x10: case 0x18: s = "3;0"; break;
        case 0x14: s = "$"; break;
        case 0x15: s = "USD"; break;
        case 0x1D: s = "/"; break;
        case 0x1E: s = ":"; break;
        case 0x1F: s = "M/d/yyyy"; break;
        case 0x20: s = "dddd, MMMM dd, yyyy"; break;
        case 0x28: s = "AM"; break;
        case 0x29: s = "PM"; break;
        case 0x50: s = ""; break;
        case 0x51: s = "-"; break;
        case 0x59: case 0x67: s = "en"; break; /* SISO639LANGNAME, ...NAME2 */
        case 0x5A: case 0x68: s = "US"; break; /* SISO3166CTRYNAME, ...NAME2 */
        case 0x5C: s = "en-US"; break;         /* SNAME */
        case 0x6D: s = "en"; break;            /* SPARENT */
        case 0x1003: s = "h:mm:ss tt"; break;  /* STIMEFORMAT */
        default: break;
        }
        break;
    }
    if (!is_num && !s) { hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 4); }
    if (lctype & 0x20000000u) {                 /* LOCALE_RETURN_NUMBER: a DWORD in 2 WCHARs */
        if (!is_num) { hle_set_last_error(EX_ERROR_INVALID_FLAGS); RET(0, 4); }
        if (!dn) RET(2, 4);
        if (dn < 2 || !data) { hle_set_last_error(EX_ERROR_INSUFFICIENT_BUFFER); RET(0, 4); }
        MEM32(data) = num;
        RET(2, 4);
    }
    if (is_num) { snprintf(tmp, sizeof tmp, hex ? "%04X" : "%u", num); s = tmp; }
    {
        uint32_t len = (uint32_t)strlen(s) + 1u, i;
        if (!dn) RET(len, 4);
        if ((uint32_t)dn < len || !data) { hle_set_last_error(EX_ERROR_INSUFFICIENT_BUFFER); RET(0, 4); }
        for (i = 0; i < len; i++) MEM16(data + i * 2u) = (uint8_t)s[i];
        RET(len, 4);
    }
}

/* CompareStringEx(locale, flags, s1, cch1, s2, cch2, version, reserved, lParam) */
static void k_CompareStringEx(void) {
    uint32_t va = A32(0), flags = A32(1), s1 = A32(2), s2 = A32(4);
    int n1 = (int)A32(3), n2 = (int)A32(5);
    uint32_t len1, len2, i = 0, j = 0;
    char name[64];
    uint32_t supported = EX_NORM_IGNORECASE | EX_NORM_IGNORENONSPACE | EX_NORM_IGNORESYMBOLS |
                         EX_LINGUISTIC_IGNORECASE | EX_LINGUISTIC_IGNOREDIACRITIC |
                         EX_NORM_LINGUISTIC_CASING;
    if (!s1 || !s2 || A32(6) || A32(7) || A32(8)) {
        hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 9);
    }
    if (flags & ~supported) { hle_set_last_error(EX_ERROR_INVALID_FLAGS); RET(0, 9); }
    if (ex_locale_of(va, name, sizeof name) == EX_LOCALE_BAD) {
        hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 9);
    }
    if (n1 < 0 && !ex_wide_len(s1, &len1)) { hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 9); }
    if (n2 < 0 && !ex_wide_len(s2, &len2)) { hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 9); }
    if (n1 >= 0) len1 = (uint32_t)n1;
    if (n2 >= 0) len2 = (uint32_t)n2;
    if (len1 > EX_MAX_STRING || len2 > EX_MAX_STRING) { hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 9); }
    for (;;) {
        uint16_t a, b;
        while (i < len1 && ex_ignore_unit(MEM16(s1 + i * 2u), flags)) i++;
        while (j < len2 && ex_ignore_unit(MEM16(s2 + j * 2u), flags)) j++;
        if (i >= len1 || j >= len2) break;
        a = ex_cmp_unit(MEM16(s1 + i * 2u), flags);
        b = ex_cmp_unit(MEM16(s2 + j * 2u), flags);
        if (a != b) RET(a < b ? 1u : 3u, 9);    /* CSTR_LESS_THAN / CSTR_GREATER_THAN */
        i++; j++;
    }
    RET(i >= len1 && j >= len2 ? 2u : (i >= len1 ? 1u : 3u), 9);
}

/* ---- locale-name bridge: Get{User,System}DefaultLocaleName, LCID <-> name ----
 * The Vista+ counterparts of the LCID-based NLS calls in kernel32_crt.c. As in
 * the Ex block above, this layer backs exactly one locale (en-US / 0x0409),
 * which is both the user and the system default, so name <-> LCID conversion
 * can be honest without an NLS data file:
 *   - "en-US" (and the case-insensitive "en") <-> 0x0409;
 *   - "" (LOCALE_NAME_INVARIANT) <-> 0x007F (LOCALE_INVARIANT);
 *   - NULL (LOCALE_NAME_USER_DEFAULT) -> the user LCID 0x0409;
 *   - "!x-sys-default-locale" (LOCALE_NAME_SYSTEM_DEFAULT) -> the system LCID 0x0409.
 * A neutral name ("en") maps to 0x0409 unless LOCALE_ALLOW_NEUTRAL_NAMES is
 * set, in which case it maps to the neutral LCID 0x0009; the reverse direction
 * does the same with "en" / "en-US". Casing of the name itself is ignored,
 * matching Windows. Every other name or LCID fails with ERROR_INVALID_PARAMETER
 * rather than inventing NLS data; unknown flag bits are ignored, as on Windows.
 * All strings are UTF-16 *in guest memory*: output is one WCHAR per code unit,
 * NUL-terminated, with a length measured in WCHARs (including that NUL), just
 * like GetLocaleInfoEx(LOCALE_SNAME). cch == 0 is a size probe and returns the
 * required length without touching the buffer; a present but small buffer
 * fails with ERROR_INSUFFICIENT_BUFFER and leaves it untouched.
 */
#define EX_LOCALE_MAX_NAME          85u      /* LOCALE_NAME_MAX_LENGTH */
#define EX_LOCALE_ALLOW_NEUTRAL_NAMES 0x08000000u
#define EX_LOCALE_NEUTRAL_EN        0x0009u   /* MAKELCID(MAKELANGID(LANG_ENGLISH, SUBLANG_NEUTRAL)) */
#define EX_LOCALE_INVARIANT         0x007Fu
#define EX_LOCALE_NEUTRAL           0x0000u   /* LOCALE_NEUTRAL -> user locale */
#define EX_LOCALE_USER_DEFAULT      0x0400u
#define EX_LOCALE_SYSTEM_DEFAULT    0x0800u
#define EX_LOCALE_EN_US             0x0409u

/* Write a NUL-terminated ASCII locale name as UTF-16 into guest memory. */
static uint32_t ex_write_utf16_name(const char *s, uint32_t dst, int cch) {
    uint32_t len = (uint32_t)strlen(s) + 1u, i;  /* includes the NUL */
    if (cch < 0) { hle_set_last_error(EX_ERROR_INVALID_PARAMETER); return 0; }
    if (len > EX_LOCALE_MAX_NAME) { hle_set_last_error(EX_ERROR_INVALID_PARAMETER); return 0; }
    if (!cch) return len;                        /* size probe, no write */
    if ((uint32_t)cch < len || !dst) { hle_set_last_error(EX_ERROR_INSUFFICIENT_BUFFER); return 0; }
    for (i = 0; i < len; i++) MEM16(dst + i * 2u) = (uint8_t)s[i];
    return len;
}

/* GetUserDefaultLocaleName(localeName, cchLocaleName) */
static void k_GetUserDefaultLocaleName(void) {
    RET(ex_write_utf16_name("en-US", A32(0), (int)A32(1)), 2);
}

/* GetSystemDefaultLocaleName(localeName, cchLocaleName) */
static void k_GetSystemDefaultLocaleName(void) {
    RET(ex_write_utf16_name("en-US", A32(0), (int)A32(1)), 2);
}

/* LCIDToLocaleName(lcid, name, cchName, dwFlags) */
static void k_LCIDToLocaleName(void) {
    uint32_t lcid = A32(0), dst = A32(1), flags = A32(3);
    int cch = (int)A32(2);
    const char *name;
    if (lcid == EX_LOCALE_INVARIANT) name = "";                       /* LOCALE_NAME_INVARIANT */
    else if (lcid == EX_LOCALE_NEUTRAL_EN) {
        /* A neutral LCID answers with its neutral name only when the caller
         * opted in; otherwise Windows substitutes the default language. */
        name = (flags & EX_LOCALE_ALLOW_NEUTRAL_NAMES) ? "en" : "en-US";
    } else if (lcid == EX_LOCALE_EN_US || lcid == EX_LOCALE_USER_DEFAULT ||
               lcid == EX_LOCALE_SYSTEM_DEFAULT || lcid == EX_LOCALE_NEUTRAL) {
        name = "en-US";
    } else {
        hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 4);
    }
    RET(ex_write_utf16_name(name, dst, cch), 4);
}

/* LocaleNameToLCID(lpName, dwFlags) */
static void k_LocaleNameToLCID(void) {
    uint32_t va = A32(0), flags = A32(1);
    char name[64];
    if (!va) RET(EX_LOCALE_EN_US, 2);          /* LOCALE_NAME_USER_DEFAULT */
    if (!ex_wide_ascii(va, name, sizeof name)) { hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 2); }
    if (!*name) RET(EX_LOCALE_INVARIANT, 2);   /* LOCALE_NAME_INVARIANT */
    if (!strcasecmp(name, "!x-sys-default-locale")) RET(EX_LOCALE_EN_US, 2); /* LOCALE_NAME_SYSTEM_DEFAULT */
    if (!strcasecmp(name, "en-us")) RET(EX_LOCALE_EN_US, 2);
    if (!strcasecmp(name, "en"))
        RET((flags & EX_LOCALE_ALLOW_NEUTRAL_NAMES) ? EX_LOCALE_NEUTRAL_EN : EX_LOCALE_EN_US, 2);
    hle_set_last_error(EX_ERROR_INVALID_PARAMETER); RET(0, 2);
}

/* ---- W-string primitives and the remaining Unicode KERNEL32 imports ----
 * These are the wide counterparts of kernel32.c's lstrcpyA/lstrcatA and
 * GetSystemDirectoryA, plus a real FROM_STRING implementation of
 * FormatMessageW. Inputs are UTF-16 in guest memory; output is UTF-16 written
 * with bounded stores, always NUL-terminated. Every walk is capped by
 * u16_len() so a bad pointer fails closed instead of reading off the block.
 *
 * Where Win32 gives the caller no size (lstrcpy/lstrcat), the ceiling is the
 * scan cap U16_MAX; an unterminated source fails with ERROR_INVALID_PARAMETER
 * rather than overrunning the guest's address space. Real Windows has no
 * bounds check there at all, so this is strictly safer, not less correct.
 */
static void k_lstrcpyW(void) {                        /* (dst, src) -> dst */
    uint32_t dst = A32(0), src = A32(1), n;
    if (!dst || !src || !u16_len(src, &n) ||
        (uint64_t)dst + (uint64_t)(n + 1u) * 2u > 0x100000000ull) {
        hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 2);
    }
    memmove(hle_ptr(dst), hle_ptr(src), ((size_t)n + 1u) * 2u); /* NUL included; overlap-safe */
    hle_set_last_error(0);
    RET(dst, 2);
}
static void k_lstrcatW(void) {                        /* (dst, src) -> dst */
    uint32_t dst = A32(0), src = A32(1), nd, ns;
    uint64_t end;
    if (!dst || !src || !u16_len(dst, &nd) || !u16_len(src, &ns)) {
        hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 2);
    }
    end = (uint64_t)nd + ns + 1u;                     /* appended text + NUL */
    if (end > U16_MAX || (uint64_t)dst + end * 2u > 0x100000000ull) {
        hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 2);
    }
    /* Start at the destination's NUL so its terminator is overwritten by the
     * source's; memmove also copes with a source overlapping the destination. */
    memmove((uint8_t *)hle_ptr(dst) + (size_t)nd * 2u, hle_ptr(src), (size_t)(ns + 1u) * 2u);
    hle_set_last_error(0);
    RET(dst, 2);
}

/* GetSystemDirectoryW(buffer, cch): the same notional system directory that
 * kernel32_crt.c's GetSystemDirectoryA, SearchPathA and module.c already use
 * for the guest's C: drive, encoded as UTF-16 with the shared ANSI helper.
 * cch is in WCHARs. Like Windows: cch == 0 is a pure size probe; a NULL or
 * too-small buffer returns the required size INCLUDING the NUL and sets
 * ERROR_INSUFFICIENT_BUFFER; success returns the length WITHOUT the NUL. */
static void k_GetSystemDirectoryW(void) {
    static const char sysdir[] = "C:\\Windows\\System32";
    const uint32_t n = (uint32_t)(sizeof sysdir - 1u);
    uint32_t dst = A32(0), cch = A32(1), i;
    uint16_t wide[sizeof sysdir];
    if (!hle_ansi_to_wide(sysdir, wide, sizeof wide / sizeof wide[0])) {
        hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 2);
    }
    if (!cch) RET(n + 1u, 2);                         /* size probe, buffer untouched */
    if (!dst || cch < n + 1u) {
        hle_set_last_error(EX_ERROR_INSUFFICIENT_BUFFER); RET(n + 1u, 2);
    }
    for (i = 0; i <= n; i++) MEM16(dst + i * 2u) = wide[i];
    hle_set_last_error(0);
    RET(n, 2);
}

/* ---- FormatMessageW: FORMAT_MESSAGE_FROM_STRING with insertions ----
 * Supported flags: FROM_STRING (required), IGNORE_INSERTS, ARGUMENT_ARRAY,
 * ALLOCATE_BUFFER and the MAX_WIDTH_MASK low byte (accepted; line rewrapping
 * is not implemented, but hard %n/%r breaks are preserved). Any other bit --
 * notably FROM_SYSTEM and FROM_HMODULE, which need message-table resources
 * this layer does not carry -- is refused with an explicit diagnostic and
 * ERROR_INVALID_FLAGS (1004), never a silent fabricated success.
 * Inserts are %1..%99, defaulting to !s! (a pointer to a guest WCHAR string);
 * !d!/!i!/!u!/!x!/!X! format the value as an integer. Width/precision ('*')
 * inserts and other specifiers are refused with a diagnostic and
 * ERROR_INVALID_PARAMETER. The output ceiling is 64 KB (ERROR_MORE_DATA),
 * matching the Win32 limit that a message buffer cannot exceed 64K bytes.
 * Under IGNORE_INSERTS the %n sequences are copied through verbatim, as MSDN
 * specifies. Standard escapes %%, %0, %n, %r, %t, %!, %<space>, %. and any
 * other nondigit after '%' (emitted without the '%') are handled.
 */
#define FM_ALLOCATE_BUFFER 0x00000100u
#define FM_IGNORE_INSERTS  0x00000200u
#define FM_FROM_STRING     0x00000400u
#define FM_FROM_HMODULE    0x00000800u
#define FM_FROM_SYSTEM     0x00001000u
#define FM_ARGUMENT_ARRAY  0x00002000u
#define FM_WIDTH_MASK      0x000000FFu
#define FM_OUT_MAX         0x8000u                   /* WCHARs == 64 KB bytes */
#define FM_ERROR_MORE_DATA 234u

static void fm_diag(const char *what, uint32_t flags) {
    static uint32_t last_flags; static const char *last_what; static int seen;
    if (seen && last_flags == flags && last_what == what) return;  /* dedup repeats */
    fprintf(stderr, "[win32hle] FormatMessageW: %s (flags 0x%08X) from guest 0x%08X\n",
            what, flags, g_cur_func);
    last_flags = flags, last_what = what, seen = 1;
}

/* Insert n's raw value. ARGUMENT_ARRAY passes an array of DWORDs directly;
 * the default passes a va_list*, whose first word is the address of the first
 * vararg (x86 stdcall). Both are guest addresses. */
static uint32_t fm_insert_value(uint32_t args, uint32_t flags, uint32_t n) {
    uint32_t base = (flags & FM_ARGUMENT_ARRAY) ? args : (args ? MEM32(args) : 0u);
    return base ? MEM32(base + (n - 1u) * 4u) : 0u;
}

static void k_FormatMessageW(void) {                  /* (flags,src,id,lang,buf,size,args) */
    uint32_t flags = A32(0), src = A32(1), buf = A32(4), nsize = A32(5), args = A32(6);
    uint32_t supported = FM_ALLOCATE_BUFFER | FM_IGNORE_INSERTS | FM_FROM_STRING |
                         FM_ARGUMENT_ARRAY | FM_WIDTH_MASK;
    uint32_t slen = 0, i = 0, len = 0;
    uint16_t out[FM_OUT_MAX];
#define FM_PUT(c) do {                                                     \
        if (len + 1u >= FM_OUT_MAX) { fm_diag("message exceeds 64 KB", flags); \
            hle_set_last_error(FM_ERROR_MORE_DATA); RET(0, 7); }           \
        out[len++] = (uint16_t)(c);                                        \
    } while (0)                       /* keep one slot for the final NUL */

    if ((flags & ~supported) || !(flags & FM_FROM_STRING)) {
        fm_diag("only FROM_STRING flags are implemented", flags);
        hle_set_last_error(EX_ERROR_INVALID_FLAGS);
        RET(0, 7);
    }
    if (!src || !u16_len(src, &slen)) { hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 7); }

    while (i < slen) {
        uint16_t c = MEM16(src + i * 2u);
        if (c != '%') { FM_PUT(c); i++; continue; }
        if (i + 1u >= slen) { FM_PUT('%'); i++; continue; }   /* trailing '%' */
        uint16_t d = MEM16(src + (i + 1u) * 2u);
        if (d == '%')     { FM_PUT('%'); i += 2u; continue; }
        if (d == '0')     { i += 2u; continue; }              /* end line, no newline */
        if (d == 'r')     { FM_PUT('\r'); i += 2u; continue; }
        if (d == 't')     { FM_PUT('\t'); i += 2u; continue; }
        if (d == 'n')     { FM_PUT('\r'); FM_PUT('\n'); i += 2u; continue; }
        if (d == '!' || d == ' ' || d == '.') { FM_PUT(d); i += 2u; continue; }
        if (d >= '1' && d <= '9') {
            uint32_t idx = 0u, j = i + 1u;
            while (j < slen) {
                uint16_t e = MEM16(src + j * 2u);
                if (e < '0' || e > '9') break;
                idx = idx * 10u + (uint32_t)(e - '0');
                if (idx > 99u) { hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 7); }
                j++;
            }
            uint16_t spec = 's';                           /* default !s! */
            if (j < slen && MEM16(src + j * 2u) == '!') {
                uint32_t k = j + 1u, s0 = k;
                while (k < slen && MEM16(src + k * 2u) != '!') k++;
                if (k >= slen || k != s0 + 1u) {
                    fm_diag("width/precision or malformed insert spec", flags);
                    hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 7);
                }
                spec = MEM16(src + s0 * 2u);
                j = k + 1u;
            }
            if (flags & FM_IGNORE_INSERTS) {                /* pass %n through unchanged */
                uint32_t q;
                FM_PUT('%');
                for (q = i + 1u; q < j; q++) FM_PUT(MEM16(src + q * 2u));
                i = j; continue;
            }
            {
                uint32_t v = fm_insert_value(args, flags, idx);
                if (spec == 's' || spec == 'S') {
                    uint32_t vn, q;
                    if (!v || !u16_len(v, &vn)) {
                        fm_diag("insert is not a terminated guest WCHAR string", flags);
                        hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 7);
                    }
                    for (q = 0; q < vn; q++) FM_PUT(MEM16(v + q * 2u));
                } else if (spec == 'd' || spec == 'i' || spec == 'u' || spec == 'x' || spec == 'X') {
                    char tmp[32], *p;
                    if (spec == 'd' || spec == 'i') snprintf(tmp, sizeof tmp, "%d", (int32_t)v);
                    else if (spec == 'u') snprintf(tmp, sizeof tmp, "%u", v);
                    else snprintf(tmp, sizeof tmp, spec == 'x' ? "%x" : "%X", v);
                    for (p = tmp; *p; p++) FM_PUT((unsigned char)*p);
                } else {
                    fm_diag("unsupported insert format specifier", flags);
                    hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 7);
                }
            }
            i = j; continue;
        }
        FM_PUT(d); i += 2u;                                /* e.g. %<space>, %. -> the char */
    }
    out[len] = 0;                                          /* NUL-terminated; len is text length */
    {
        uint32_t needed = len + 1u, k;
        if (flags & FM_ALLOCATE_BUFFER) {
            uint32_t allocn = nsize > needed ? nsize : needed;
            void *p;
            if (allocn > FM_OUT_MAX) {
                hle_set_last_error(FM_ERROR_MORE_DATA); RET(0, 7);
            }
            p = hle_alloc(allocn * 2u);
            if (!p) { hle_set_last_error(EX_ERROR_NOT_ENOUGH_MEMORY); RET(0, 7); }
            memcpy(p, out, (size_t)needed * 2u);
            if (buf) MEM32(buf) = hle_va(p);
            hle_set_last_error(0);
            RET(len, 7);
        }
        if (!buf || nsize < needed) {
            hle_set_last_error(EX_ERROR_INSUFFICIENT_BUFFER);
            RET(0, 7);
        }
        for (k = 0; k < needed; k++) MEM16(buf + k * 2u) = out[k];
        hle_set_last_error(0);
        RET(len, 7);
    }
#undef FM_PUT
}

const win32hle_shim win32hle_kernel32_ext[] = {
    { "GetProcessAffinityMask", k_GetProcessAffinityMask }, { "SetProcessAffinityMask", k_SetProcessAffinityMask },
    { "CreateToolhelp32Snapshot", k_CreateToolhelp32Snapshot }, { "Process32First", k_Process32First },
    { "Process32Next", k_Process32First }, { "Process32FirstW", k_Process32First },
    { "Process32NextW", k_Process32First }, { "Module32First", k_Process32First },
    { "Module32Next", k_Process32First }, { "Module32FirstW", k_Process32First },
    { "Module32NextW", k_Process32First }, { "OpenProcess", k_OpenProcess },
    { "VirtualAllocEx", k_VirtualAllocEx }, { "VirtualFreeEx", k_VirtualFreeEx },
    { "WriteProcessMemory", k_WriteProcessMemory }, { "SetThreadContext", k_SetThreadContext },
    { "CreateFileMappingA", k_CreateFileMappingA }, { "CreateFileMappingW", k_CreateFileMappingW },
    { "OpenFileMappingA", k_OpenFileMappingA }, { "OpenFileMappingW", k_OpenFileMappingW },
    { "MapViewOfFile", k_MapViewOfFile }, { "MapViewOfFileEx", k_MapViewOfFileEx },
    { "UnmapViewOfFile", k_UnmapViewOfFile }, { "FlushViewOfFile", k_FlushViewOfFile },
    { "GetMappedFileNameA", k_GetMappedFileNameA }, { "GetMappedFileNameW", k_GetMappedFileNameW },
    { "CreatePipe", k_CreatePipe }, { "CreateNamedPipeA", k_CreateNamedPipeA },
    { "ConnectNamedPipe", k_ConnectNamedPipe }, { "WaitNamedPipeA", k_WaitNamedPipeA },
    { "PeekNamedPipe", k_PeekNamedPipe },
    { "GetFullPathNameA", k_GetFullPathNameA }, { "GetFullPathNameW", k_GetFullPathNameW },
    { "GetShortPathNameW", k_GetShortPathNameW }, { "GetLongPathNameW", k_GetLongPathNameW },
    { "SearchPathA", k_SearchPathA },
    { "OpenFile", k_OpenFile }, { "_hread", k__lread }, { "_hwrite", k__lwrite },
    { "_lopen", k__lopen }, { "_lcreat", k__lcreat }, { "_lread", k__lread },
    { "_lwrite", k__lwrite }, { "_lclose", k__lclose }, { "_llseek", k__llseek },
    { "CreateFileA",                k_CreateFileA }, { "CreateFileW", k_CreateFileW },
    { "ReadFile",                   k_ReadFile },
    { "WriteFile",                  k_WriteFile },
    { "SetFilePointer",             k_SetFilePointer },
    { "SetFilePointerEx",           k_SetFilePointerEx },
    { "SetEndOfFile",               k_SetEndOfFile },
    { "FlushFileBuffers",           k_FlushFileBuffers },
    { "GetFileSize",                k_GetFileSize },
    { "GetFileSizeEx",              k_GetFileSizeEx },
    { "GetFileType",                k_GetFileType },
    { "SetHandleInformation",       k_SetHandleInformation },
    { "LockFileEx",                 k_LockFileEx },
    { "CloseHandle",                k_CloseHandle },
    { "GetFileTime",                k_GetFileTime },
    { "SetFileTime",                k_SetFileTime },
    { "GetFileInformationByHandle", k_GetFileInformationByHandle },
    { "GetFileAttributesA",         k_GetFileAttributesA },
    { "GetFileAttributesW",         k_GetFileAttributesW },
    { "GetFileAttributesExA",       k_GetFileAttributesExA },
    { "GetFileAttributesExW",       k_GetFileAttributesExW },
    { "SetFileAttributesA",         k_SetFileAttributesA },
    { "SetFileAttributesW",         k_SetFileAttributesW },
    { "CreateDirectoryA",           k_CreateDirectoryA },
    { "CreateDirectoryW",           k_CreateDirectoryW },
    { "RemoveDirectoryA",           k_RemoveDirectoryA },
    { "DeleteFileA",                k_DeleteFileA }, { "DeleteFileW", k_DeleteFileW },
    { "MoveFileA",                  k_MoveFileA },
    { "MoveFileExA",                k_MoveFileExA },
    { "CopyFileA",                  k_CopyFileA },
    { "FindFirstFileA",             k_FindFirstFileA }, { "FindFirstFileW", k_FindFirstFileW },
    { "FindFirstFileExW",           k_FindFirstFileExW },
    { "FindNextFileA",              k_FindNextFileA }, { "FindNextFileW", k_FindNextFileW },
    { "FindClose",                  k_FindClose },
    { "GetDiskFreeSpaceA",          k_GetDiskFreeSpaceA },
    { "GetDiskFreeSpaceW",          k_GetDiskFreeSpaceA },
    { "GetDiskFreeSpaceExA",        k_GetDiskFreeSpaceExA },
    { "GetDiskFreeSpaceExW",        k_GetDiskFreeSpaceExA },
    { "GetVolumeInformationA",      k_GetVolumeInformationA },
    { "GetVolumeInformationW",      k_GetVolumeInformationW },
    { "GetCompressedFileSizeA",     k_GetCompressedFileSizeA },
    { "FindFirstVolumeW",           k_FindFirstVolumeW },
    { "FindNextVolumeW",            k_FindNextVolumeW },
    { "FindVolumeClose",            k_FindVolumeClose },
    { "ReadDirectoryChangesW",      k_ReadDirectoryChangesW },
    { "GetDriveTypeA",              k_GetDriveTypeA },
    { "GetSystemTime",              k_GetSystemTime },
    { "GetLocalTime",               k_GetLocalTime },
    { "GetSystemTimeAsFileTime",    k_GetSystemTimeAsFileTime },
    { "FileTimeToSystemTime",       k_FileTimeToSystemTime },
    { "SystemTimeToFileTime",       k_SystemTimeToFileTime },
    { "FileTimeToLocalFileTime",    k_FileTimeToLocalFileTime },
    { "LocalFileTimeToFileTime",    k_LocalFileTimeToFileTime },
    { "CompareFileTime",            k_CompareFileTime },
    { "DosDateTimeToFileTime",      k_DosDateTimeToFileTime },
    { "FileTimeToDosDateTime",      k_FileTimeToDosDateTime },
    { "GetTimeZoneInformation",     k_GetTimeZoneInformation },
    { "GetPrivateProfileStringA",   k_GetPrivateProfileStringA },
    { "GetPrivateProfileIntA",      k_GetPrivateProfileIntA },
    { "WritePrivateProfileStringA", k_WritePrivateProfileStringA },
    { "LCMapStringEx",              k_LCMapStringEx },
    { "GetLocaleInfoEx",            k_GetLocaleInfoEx },
    { "CompareStringEx",            k_CompareStringEx },
    { "GetUserDefaultLocaleName",   k_GetUserDefaultLocaleName },
    { "GetSystemDefaultLocaleName", k_GetSystemDefaultLocaleName },
    { "LCIDToLocaleName",           k_LCIDToLocaleName },
    { "LocaleNameToLCID",           k_LocaleNameToLCID },
    { "FormatMessageW",             k_FormatMessageW },
    { "GetSystemDirectoryW",        k_GetSystemDirectoryW },
    { "MoveFileExW",                k_MoveFileExW },
    { "lstrcatW",                   k_lstrcatW },
    { "lstrcpyW",                   k_lstrcpyW },
    { 0, 0 }
};
