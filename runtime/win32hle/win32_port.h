/*
 * win32_port.h - the POSIX-to-Win32 bridge for source-level cross-builds.
 *
 * GameReComp's win32hle runtime is written for a POSIX host (ucontext fault
 * reporting, mmap-backed guest memory, dlopen'd host GL, poll, statvfs).  When
 * the *generated project* is recompiled with MinGW-w64 for a Windows PE32,
 * those host facilities do not exist.  This header supplies the small subset
 * that a console build actually needs, implemented on top of real Win32 APIs
 * in win32_port.c.  It is never compiled on POSIX hosts.
 *
 * SCOPE (honest): console apps only.  The SDL/DDraw/DSound/DInput/OpenGL shims
 * (sdl12.c, sdl2.c, sdlmix.c, screen.c, present.c, d3d*.c, ddraw.c, dsound.c,
 * dinput.c, gl32.c, bink.c, mss.c, ...) are NOT part of a Windows console
 * build: MinGW-w64 has no SDL2 headers here, and a console program does not
 * call them.  The build tool omits those files and registers empty shim
 * tables for them (see win32_port.c and RECOMP_CONSOLE_ONLY).  GUI games are
 * therefore BLOCKED, not silently downgraded.  Signal-based fault reporting
 * is also unavailable: see the guard in host.c.
 *
 * Include order: this header includes winsock2.h before windows.h so the
 * poll() shim can use WSAPoll without the classic winsock.h conflict.
 */
#ifndef WIN32HLE_WIN32_PORT_H
#define WIN32HLE_WIN32_PORT_H

#ifdef _WIN32

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN 1
#endif
#ifndef _CRT_RAND_S
#define _CRT_RAND_S 1                 /* rand_s(), for arc4random replacements */
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <errno.h>
#include <direct.h>                   /* _mkdir, _getcwd (declared before macros) */
#include <fcntl.h>
#include <io.h>
#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ mmap */
/* sys/mman.h is absent on MinGW.  Guest memory is a flat 32-bit address
 * space, so MAP_FIXED asks for that exact VA (VirtualAlloc at an occupied
 * address fails, which is the NOREPLACE semantics the runtime wants). */
#define PROT_NONE   0x00
#define PROT_READ   0x01
#define PROT_WRITE  0x02
#define PROT_EXEC   0x04
#define MAP_SHARED  0x01
#define MAP_PRIVATE 0x02
#define MAP_FIXED   0x10
#define MAP_ANONYMOUS 0x20
#define MAP_NORESERVE 0x4000
#define MAP_FIXED_NOREPLACE 0x100000
#define MAP_FAILED  ((void *)(intptr_t)-1)
#define MADV_DONTNEED 4

void *win32_port_mmap(void *addr, size_t len, int prot, int flags, int fd, off_t off);
int   win32_port_munmap(void *addr, size_t len);
int   win32_port_madvise(void *addr, size_t len, int advice);
int   win32_port_getpagesize(void);
#define mmap(a,l,p,f,d,o)  win32_port_mmap((a),(l),(p),(f),(d),(o))
#define munmap(a,l)        win32_port_munmap((a),(l))
#define madvise(a,l,x)     win32_port_madvise((a),(l),(x))
#define getpagesize()      win32_port_getpagesize()

/* ---------------------------------------------------------------- dlfcn */
/* gl32.c asks the host GLU by its usual Unix names.  A console build does not
 * call it; these wrappers still translate the common names so that linking a
 * project which happens to reference them does not create a fake symbol. */
#define RTLD_LAZY   0x00001
#define RTLD_NOW    0x00002
#define RTLD_GLOBAL 0x00100
#define RTLD_LOCAL  0x00000
void *win32_port_dlopen(const char *path, int mode);
void *win32_port_dlsym(void *handle, const char *name);
int   win32_port_dlclose(void *handle);
const char *win32_port_dlerror(void);
#define dlopen(p,m)   win32_port_dlopen((p),(m))
#define dlsym(h,n)    win32_port_dlsym((h),(n))
#define dlclose(h)    win32_port_dlclose((h))
#define dlerror()     win32_port_dlerror()

/* ----------------------------------------------------------------- poll */
/* winsock2.h already defines struct pollfd and the POLL* constants with
 * WSAPoll's values; the shim passes them straight through. */
int win32_port_poll(struct pollfd *fds, unsigned long nfds, int timeout);
#define poll(f,n,t) win32_port_poll((f),(n),(t))

/* -------------------------------------------------------------- statvfs */
struct win32_port_statvfs {
    unsigned long f_bsize, f_frsize;
    uint64_t f_blocks, f_bfree, f_bavail;
    uint64_t f_files, f_ffree, f_favail;
    unsigned long f_fsid, f_flag, f_namemax;
};
int win32_port_statvfs(const char *path, struct win32_port_statvfs *buf);
#define statvfs    win32_port_statvfs
#define statvfs_t  struct win32_port_statvfs

/* ------------------------------------------------------- other POSIX bits */
/* MinGW's <fcntl.h> has no O_CLOEXEC: CRT fds are not inherited by children
 * created with the Win32 APIs used here, so the flag is a no-op. */
#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif
unsigned int win32_port_arc4random(void);
void         win32_port_arc4random_buf(void *buf, size_t len);
#define arc4random()     win32_port_arc4random()
#define arc4random_buf(b,n) win32_port_arc4random_buf((b),(n))
int    win32_port_lstat(const char *path, struct stat *st);
int    win32_port_readlink(const char *path, char *buf, size_t len);
char  *win32_port_realpath(const char *path, char *out);
struct tm *win32_port_localtime_r(const time_t *t, struct tm *out);
long   win32_port_pread(int fd, void *buf, unsigned int len, off_t off);
#define lstat(p,s)          win32_port_lstat((p),(s))
#define readlink(p,b,n)     win32_port_readlink((p),(b),(n))
#define realpath(p,o)       win32_port_realpath((p),(o))
#define localtime_r(t,o)    win32_port_localtime_r((t),(o))
#define pread(fd,b,n,o)     win32_port_pread((fd),(b),(n),(o))
#define mkdir(p,m)          _mkdir(p)
#define pipe(f)             _pipe((f), 4096, _O_BINARY)
/* fork() has no Win32 equivalent: WinExec fails explicitly rather than
 * pretending to spawn.  timegm/gmtime_r map to the CRT's UTC helpers. */
#ifndef _PID_T_
typedef int pid_t;
#endif
int    win32_port_fork(void);
time_t win32_port_timegm(struct tm *tm);
struct tm *win32_port_gmtime_r(const time_t *t, struct tm *out);
#define fork()              win32_port_fork()
#define timegm(t)           win32_port_timegm((t))
#define gmtime_r(t,o)       win32_port_gmtime_r((t),(o))

/* ------------------------------------------------- console-only shim tables
 * The GUI/audio modules are not linked into a console PE.  Their registration
 * arrays still have to satisfy host.c's references, so define empty ones. */
#ifdef RECOMP_CONSOLE_ONLY
/* Implemented in win32_port.c (which includes win32hle.h for the real
 * win32hle_shim type).  host.c still calls them, so the symbols must exist. */
void hle_dsound_register_com(void);
void hle_user32_enable_desktop_window(int);
#endif /* RECOMP_CONSOLE_ONLY */

#ifdef __cplusplus
}
#endif
#endif /* _WIN32 */
#endif /* WIN32HLE_WIN32_PORT_H */
