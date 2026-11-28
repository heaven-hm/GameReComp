/*
 * win32_port.c - real Win32 implementations of the POSIX facilities the
 * win32hle console runtime uses.  See win32_port.h for scope and limits.
 *
 * Nothing here returns a fabricated result to make a build pass: mmap is
 * VirtualAlloc/MapViewOfFileEx, dlopen is LoadLibraryA, poll is WSAPoll,
 * statvfs is GetDiskFreeSpaceExA.  Calls that have no meaningful Win32
 * equivalent (readlink of a POSIX symlink) fail with ENOSYS and are reported.
 */
#include "win32_port.h"

#ifdef _WIN32

#include "win32hle.h"                 /* win32hle_shim, for the empty tables */
#include <direct.h>
#include <sys/stat.h>

/* ------------------------------------------------------------------ mmap */

/* File-backed views must be unmapped with UnmapViewOfFile rather than
 * VirtualFree.  The runtime maps only a handful of PE sections, so a small
 * fixed table is enough; an overflow is an explicit failure, not a leak. */
#define MAX_VIEWS 256
static struct {
    void *addr;
    size_t len;
} g_views[MAX_VIEWS];
static int g_nviews;

static DWORD prot_to_page(int prot, int file_backed) {
    if (prot & PROT_EXEC)
        return (prot & PROT_WRITE) ? PAGE_EXECUTE_READWRITE : PAGE_EXECUTE_READ;
    if (prot & PROT_WRITE) return PAGE_READWRITE;
    if (prot & PROT_READ)  return PAGE_READONLY;
    /* Win32 has no PROT_NONE file view; PAGE_NOACCESS is anonymous-only. */
    return file_backed ? PAGE_READONLY : PAGE_NOACCESS;
}

void *win32_port_mmap(void *addr, size_t len, int prot, int flags, int fd, off_t off) {
    if (!len) return MAP_FAILED;
    if (fd == -1) {
        DWORD page = prot_to_page(prot, 0);
        void *p;
        if (addr) {
            /* MAP_FIXED: the guest asked for this exact VA.  Reserve+commit
             * there (fails if occupied, matching MAP_FIXED_NOREPLACE), or, if
             * the range is already one of ours, change its protection. */
            DWORD old = 0;
            p = VirtualAlloc(addr, len, MEM_RESERVE | MEM_COMMIT, page);
            if (!p && VirtualProtect(addr, len, page, &old)) p = addr;
            (void)flags;
            return p ? p : MAP_FAILED;
        }
        p = VirtualAlloc(NULL, len, MEM_RESERVE | MEM_COMMIT, page);
        return p ? p : MAP_FAILED;
    }

    /* MapViewOfFileEx: a real file-backed view, shared or private as asked. */
    HANDLE fh = (HANDLE)_get_osfhandle(fd);
    if (fh == INVALID_HANDLE_VALUE) return MAP_FAILED;
    HANDLE map = CreateFileMappingA(fh, NULL,
                                   (prot & PROT_WRITE) ? PAGE_READWRITE : PAGE_READONLY,
                                   0, 0, NULL);
    if (!map) return MAP_FAILED;
    DWORD acc = (prot & PROT_WRITE) ? FILE_MAP_WRITE : FILE_MAP_READ;
    if (prot & PROT_EXEC) acc |= FILE_MAP_EXECUTE;
    uint64_t o = (uint64_t)(uint32_t)off;
    void *p = MapViewOfFileEx(map, acc, (DWORD)(o >> 32), (DWORD)o, len, addr);
    CloseHandle(map);
    if (!p) return MAP_FAILED;
    if (g_nviews >= MAX_VIEWS) {
        UnmapViewOfFile(p);
        return MAP_FAILED;
    }
    g_views[g_nviews].addr = p;
    g_views[g_nviews].len = len;
    g_nviews++;
    return p;
}

int win32_port_munmap(void *addr, size_t len) {
    if (!addr) return -1;
    for (int i = 0; i < g_nviews; i++) {
        if (g_views[i].addr == addr) {
            int ok = UnmapViewOfFile(addr) != 0;
            g_views[i] = g_views[--g_nviews];
            return ok ? 0 : -1;
        }
    }
    /* MEM_RELEASE needs the reservation base and a zero size; a partial unmap
     * (rare in this runtime) decommits instead. */
    if (VirtualFree(addr, 0, MEM_RELEASE)) return 0;
    if (VirtualFree(addr, len, MEM_DECOMMIT)) return 0;
    return -1;
}

int win32_port_madvise(void *addr, size_t len, int advice) {
    if (advice != MADV_DONTNEED) return 0;
    for (int i = 0; i < g_nviews; i++)
        if (g_views[i].addr == addr) return 0;   /* a file view cannot discard */
    DWORD old = 0;
    if (!VirtualProtect(addr, len, PAGE_READWRITE, &old)) return -1;
    SecureZeroMemory(addr, len);
    VirtualProtect(addr, len, old, &old);
    return 0;
}

int win32_port_getpagesize(void) {
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    return (int)si.dwPageSize;
}

/* ---------------------------------------------------------------- dlfcn */

static const char *host_lib_name(const char *name) {
    static char buf[260];
    if (!name || !*name) return NULL;
    if (!_strnicmp(name, "libGLU", 6) || !_strnicmp(name, "glu", 3)) return "GLU32.DLL";
    if (!_strnicmp(name, "libGL", 5) || !_strnicmp(name, "opengl", 6)) return "OPENGL32.DLL";
    if (strstr(name, "SDL2") || !_strnicmp(name, "libSDL", 6)) return "SDL2.dll";
    if (strchr(name, '.') || strchr(name, '\\') || strchr(name, '/')) return name;
    snprintf(buf, sizeof buf, "%s.dll", name);
    return buf;
}

void *win32_port_dlopen(const char *path, int mode) {
    (void)mode;
    const char *name = host_lib_name(path);
    if (!name) return NULL;
    HMODULE h = LoadLibraryA(name);
    return h ? (void *)h : NULL;
}
void *win32_port_dlsym(void *handle, const char *name) {
    if (!handle || !name) return NULL;
    FARPROC p = GetProcAddress((HMODULE)handle, name);
    return p ? (void *)p : NULL;
}
int win32_port_dlclose(void *handle) {
    return handle && FreeLibrary((HMODULE)handle) ? 0 : -1;
}
const char *win32_port_dlerror(void) {
    static char msg[96];
    snprintf(msg, sizeof msg, "LoadLibrary/GetProcAddress failed (GetLastError=%lu)",
             (unsigned long)GetLastError());
    return msg;
}

/* ----------------------------------------------------------------- poll */

int win32_port_poll(struct pollfd *fds, unsigned long nfds, int timeout) {
    if (!fds) { errno = EINVAL; return -1; }
    WSAPOLLFD *w = (WSAPOLLFD *)calloc(nfds ? nfds : 1, sizeof *w);
    if (!w) { errno = ENOMEM; return -1; }
    for (unsigned long i = 0; i < nfds; i++) {
        intptr_t osf = _get_osfhandle((int)fds[i].fd);
        if (osf == INVALID_HANDLE_VALUE) {
            w[i].fd = INVALID_SOCKET;
            fds[i].revents = POLLNVAL;
        } else {
            w[i].fd = (SOCKET)osf;
            w[i].events = fds[i].events;
        }
    }
    int r = WSAPoll(w, (ULONG)nfds, timeout);
    if (r > 0) {
        for (unsigned long i = 0; i < nfds; i++)
            fds[i].revents = w[i].revents;
    }
    free(w);
    return r;
}

/* -------------------------------------------------------------- statvfs */

int win32_port_statvfs(const char *path, struct win32_port_statvfs *buf) {
    if (!buf) { errno = EINVAL; return -1; }
    const char *p = (path && *path) ? path : ".";
    ULARGE_INTEGER avail, total, total_free;
    if (!GetDiskFreeSpaceExA(p, &avail, &total, &total_free)) {
        errno = ENOENT;
        return -1;
    }
    DWORD spc = 0, bps = 0, free_cl = 0, total_cl = 0;
    if (!GetDiskFreeSpaceA(p, &spc, &bps, &free_cl, &total_cl)) {
        errno = ENOENT;
        return -1;
    }
    memset(buf, 0, sizeof *buf);
    buf->f_bsize = bps;
    buf->f_frsize = bps * spc;
    uint64_t unit = buf->f_frsize ? buf->f_frsize : 1;
    buf->f_blocks = total.QuadPart / unit;
    buf->f_bfree = total_free.QuadPart / unit;
    buf->f_bavail = avail.QuadPart / unit;
    buf->f_namemax = 255;
    return 0;
}

/* ------------------------------------------------------- other POSIX bits */

unsigned int win32_port_arc4random(void) {
    unsigned int v = 0;
    if (rand_s(&v) != 0) return (unsigned int)rand();  /* documented fallback */
    return v;
}
void win32_port_arc4random_buf(void *buf, size_t len) {
    unsigned char *p = (unsigned char *)buf;
    while (len >= 4) {
        unsigned int v = win32_port_arc4random();
        memcpy(p, &v, 4);
        p += 4; len -= 4;
    }
    if (len) {
        unsigned int v = win32_port_arc4random();
        memcpy(p, &v, len);
    }
}

int win32_port_lstat(const char *path, struct stat *st) {
    return stat(path, st);    /* Windows reparse points are not POSIX symlinks */
}
int win32_port_readlink(const char *path, char *buf, size_t len) {
    (void)path; (void)buf; (void)len;
    errno = ENOSYS;           /* honest: no POSIX symlink target on NTFS here */
    return -1;
}
char *win32_port_realpath(const char *path, char *out) {
    if (!path) { errno = EINVAL; return NULL; }
    if (!out) {
        /* POSIX realpath(path, NULL) allocates; hle_path.c keeps the pointer. */
        out = (char *)malloc(_MAX_PATH);
        if (!out) { errno = ENOMEM; return NULL; }
    }
    if (!_fullpath(out, path, _MAX_PATH)) return NULL;
    return out;
}
struct tm *win32_port_localtime_r(const time_t *t, struct tm *out) {
    if (!t || !out) { errno = EINVAL; return NULL; }
    return localtime_s(out, t) == 0 ? out : NULL;
}
long win32_port_pread(int fd, void *buf, unsigned int len, off_t off) {
    long saved = _lseek(fd, 0, SEEK_CUR);
    if (saved < 0) return -1;
    if (_lseek(fd, off, SEEK_SET) < 0) return -1;
    long n = _read(fd, buf, len);
    _lseek(fd, saved, SEEK_SET);
    return n;
}
int win32_port_fork(void) {
    errno = ENOSYS;                   /* WinExec cannot fork: report, don't fake */
    return -1;
}
time_t win32_port_timegm(struct tm *tm) { return _mkgmtime(tm); }
struct tm *win32_port_gmtime_r(const time_t *t, struct tm *out) {
    if (!t || !out) { errno = EINVAL; return NULL; }
    return gmtime_s(out, t) == 0 ? out : NULL;
}

/* -------------------------------------------------- console HLE helpers
 * These helpers normally live in modules the console build omits (clock.c,
 * kernel32_crt.c, msvcrt.c, kernel32_ext.c).  They are small, and providing
 * them here keeps the embedded-image boot and the kernel32 console shims
 * working without pulling in the whole POSIX-side runtime. */
static int  g_port_argc;
static char **g_port_argv;
void hle_set_argv(int argc, char *const *argv) {
    g_port_argc = argc;
    g_port_argv = (char **)argv;
}
int hle_ansi_to_wide(const char *s, uint16_t *out, size_t n) {
    if (!s || !out || !n) return 0;
    int r = MultiByteToWideChar(CP_ACP, 0, s, -1, (LPWSTR)out, (int)n);
    return r > 0 ? r : 0;
}
int hle_wide_to_ansi(const uint16_t *w, char *out, size_t n) {
    if (!w || !out || !n) return 0;
    int r = WideCharToMultiByte(CP_ACP, 0, (LPCWSTR)w, -1, out, (int)n, NULL, NULL);
    return r > 0 ? r : 0;
}
uint32_t hle_clock_ms(void) { return (uint32_t)GetTickCount(); }
uint32_t hle_clock_sleep(uint32_t ms) { Sleep(ms); return 0; }
int hle_clock_virtual(void) { return 0; }   /* no virtual clock in this build */
void hle_clock_config(char *buf, size_t n) {
    if (buf && n) snprintf(buf, n, "host");
}
void hle_clock_reset(void) { /* nothing to reset */ }
uint64_t hle_clock_us(void) { return (uint64_t)hle_clock_ms() * 1000ull; }
int hle_file_fd(uint32_t h) {
    if (h == 0x11u) return 0;
    if (h == 0x12u) return 1;
    if (h == 0x13u) return 2;
    /* kernel32_ext.c's hfile is { int fd; char *name; }, registered as
     * HLE_H_FILE.  No file views are created in this console subset. */
    void *o = hle_handle_obj(h, 1 /* HLE_H_FILE */);
    return o ? *(int *)o : -1;
}
int hle_thread_done(uint32_t h) { (void)h; return 0; }

/* ------------------------------------------------- console-only shim tables
 * These modules are excluded from a console PE (no SDL2 headers/libs for
 * MinGW, and a console guest never calls them).  host.c still registers them,
 * so keep its calls valid with empty, NULL-terminated tables. */
#ifdef RECOMP_CONSOLE_ONLY
/* Modules that are not needed by a simple console guest are also omitted;
 * arith-calc imports only ExitProcess/GetStdHandle/WriteFile (kernel32.c). */
/* No companion DLLs in the console subset (host.c declares these extern on
 * _WIN32 so the embedded image is actually linked). */
const recomp_companion_t *const recomp_companion_list = NULL;
const uint32_t recomp_companion_count = 0;
/* The two console imports a CLI guest actually uses.  They are the only
 * pieces of kernel32_ext.c/kernel32_crt.c needed here; the rest of those
 * POSIX-heavy modules stays out of the Windows build. */
static void console_GetStdHandle(void) {
    uint32_t n = A32(0);
    RET(n == 0xFFFFFFF6u ? 0x11u : n == 0xFFFFFFF5u ? 0x12u : 0x13u, 1);
}
static void console_WriteFile(void) {
    static int binary_set;
    uint32_t h = A32(0);
    int fd = (h == 0x11u) ? 0 : (h == 0x12u) ? 1 : (h == 0x13u) ? 2 : -1;
    const uint8_t *buf = (const uint8_t *)APTR(1);
    uint32_t n = A32(2), *pw = (uint32_t *)APTR(3), put = 0;
    if (pw) *pw = 0;
    if (fd < 0 || !buf) RET(0u, 5);
    /* The guest already emits CRLF (the original PE is a Windows console
     * program).  CRT text mode would turn each \n into another \r\n, so the
     * Windows build would print \r\r\n.  Binary mode makes its bytes match
     * the native run exactly. */
    if (!binary_set && fd >= 0 && fd <= 2) {
        _setmode(fd, _O_BINARY);
        binary_set = 1;
    }
    while (put < n) {
        int r = _write(fd, buf + put, n - put);
        if (r <= 0) break;
        put += (uint32_t)r;
    }
    if (pw) *pw = put;
    RET(put == n ? 1u : 0u, 5);
}
const win32hle_shim win32hle_kernel32_ext[] = {
    { "WriteFile", console_WriteFile }, { NULL, NULL }
};
const win32hle_shim win32hle_kernel32_crt[] = {
    { "GetStdHandle", console_GetStdHandle }, { NULL, NULL }
};
const win32hle_shim win32hle_kernel32_atom[] = { { NULL, NULL } };
const win32hle_shim win32hle_msvcrt[]    = { { NULL, NULL } };
const win32hle_shim win32hle_advapi32[]  = { { NULL, NULL } };
const win32hle_shim win32hle_storage[]   = { { NULL, NULL } };
const win32hle_shim win32hle_ole32[]     = { { NULL, NULL } };
const win32hle_shim win32hle_lz32[]      = { { NULL, NULL } };
const win32hle_shim win32hle_wininet[]   = { { NULL, NULL } };
const win32hle_shim win32hle_comctl32[]  = { { NULL, NULL } };
const win32hle_shim win32hle_gdidc[]    = { { NULL, NULL } };
const win32hle_shim win32hle_gdi32[]    = { { NULL, NULL } };
const win32hle_shim win32hle_user32[]   = { { NULL, NULL } };
const win32hle_shim win32hle_winmm[]    = { { NULL, NULL } };
const win32hle_shim win32hle_avifil32[] = { { NULL, NULL } };
const win32hle_shim win32hle_msvfw32[]  = { { NULL, NULL } };
const win32hle_shim win32hle_dsound[]   = { { NULL, NULL } };
const win32hle_shim win32hle_ddraw[]    = { { NULL, NULL } };
const win32hle_shim win32hle_d3d7[]     = { { NULL, NULL } };
const win32hle_shim win32hle_sdl12[]    = { { NULL, NULL } };
const win32hle_shim win32hle_sdl2[]     = { { NULL, NULL } };
const win32hle_shim win32hle_sdlmix[]   = { { NULL, NULL } };
const win32hle_shim win32hle_gl32[]     = { { NULL, NULL } };
const win32hle_shim win32hle_dinput[]   = { { NULL, NULL } };
const win32hle_shim win32hle_bink[]     = { { NULL, NULL } };
const win32hle_shim win32hle_mss[]      = { { NULL, NULL } };
const win32hle_shim win32hle_wsock32[]  = { { NULL, NULL } };
const win32hle_shim win32hle_d3d8[]     = { { NULL, NULL } };
const win32hle_shim win32hle_d3d9[]     = { { NULL, NULL } };
void hle_dsound_register_com(void) { /* no COM audio in a console build */ }
void hle_user32_enable_desktop_window(int enable) { (void)enable; }
#endif /* RECOMP_CONSOLE_ONLY */

#endif /* _WIN32 */

/* Keep this translation unit non-empty on POSIX hosts (the project generator
 * copies every runtime/*.c into every project, including native builds). */
typedef int win32_port_not_a_windows_build;
