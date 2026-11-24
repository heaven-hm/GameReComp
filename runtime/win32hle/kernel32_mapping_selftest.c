/*
 * kernel32_mapping_selftest.c - GR2 file-mapping coverage.
 *
 * Drives the new KERNEL32 file-mapping surface through hle_call_guest:
 * anonymous and file-backed mappings, A/W names, named mappings shared by
 * Create/Open (including ERROR_ALREADY_EXISTS and handle independence),
 * MapViewOfFileEx fixed bases, FlushViewOfFile, GetMappedFileNameA/W, and the
 * honest error paths. Headless, no game, per-process temp files.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include "win32hle.h"

#define INVALID_HANDLE     0xFFFFFFFFu
#define PAGE_READONLY      0x02u
#define PAGE_READWRITE     0x04u
#define FILE_MAP_READ      0x0004u
#define FILE_MAP_WRITE     0x0002u
#define FILE_MAP_ALL_ACCESS 0x000F001Fu
#define ERROR_INVALID_HANDLE 6u
#define ERROR_INVALID_PARAMETER 87u
#define ERROR_FILE_NOT_FOUND 2u
#define ERROR_ACCESS_DENIED 5u
#define ERROR_ALREADY_EXISTS 183u
#define ERROR_INVALID_ADDRESS 487u
#define OPEN_EXISTING 3u
#define GENERIC_READ  0x80000000u
#define GENERIC_WRITE 0x40000000u

static int fails;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }

/* host_lite.c references these; the selftest never dispatches lifted code. */
static void noop_guest(void) { }
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0x00401000u, noop_guest } };
const uint32_t recomp_dispatch_count = 1;

/* shim VAs */
static uint32_t V_Close, V_CreateFileA, V_CFMA, V_CFMW, V_OFMA, V_OFMWA,
                V_MapV, V_MapVEx, V_Unmap, V_Flush, V_GMFNA, V_GMFNW, V_GLastErr;

static uint32_t call(uint32_t va, int n, const uint32_t *a) { return hle_call_guest(va, n, a); }
#define C1(va,a) call(va,1,(uint32_t[]){ (uint32_t)(a) })
#define C2(va,a,b) call(va,2,(uint32_t[]){ (uint32_t)(a),(uint32_t)(b) })
#define C3(va,a,b,c) call(va,3,(uint32_t[]){ (uint32_t)(a),(uint32_t)(b),(uint32_t)(c) })
#define C4(va,a,b,c,d) call(va,4,(uint32_t[]){ (uint32_t)(a),(uint32_t)(b),(uint32_t)(c),(uint32_t)(d) })
#define C5(va,a,b,c,d,e) call(va,5,(uint32_t[]){ (uint32_t)(a),(uint32_t)(b),(uint32_t)(c),(uint32_t)(d),(uint32_t)(e) })
#define C6(va,a,b,c,d,e,f) call(va,6,(uint32_t[]){ (uint32_t)(a),(uint32_t)(b),(uint32_t)(c),(uint32_t)(d),(uint32_t)(e),(uint32_t)(f) })

static char tmpdir[160];
static uint32_t g_guest_path;              /* "C:\\gr2map.dat" in guest memory */
static const char *host_file;

static void write_host_file(const char *path, const char *data, size_t n) {
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) { (void)!write(fd, data, n); close(fd); }
}

int main(void) {
    snprintf(tmpdir, sizeof tmpdir, "/tmp/gr2map_%ld", (long)getpid());
    mkdir(tmpdir, 0700);
    host_file = NULL;
    {
        static char p[256];
        snprintf(p, sizeof p, "%s/gr2map.dat", tmpdir);
        host_file = p;
    }
    write_host_file(host_file, "GR2MAPDATA", 10);
    hle_set_drive('C', tmpdir);
    g_guest_path = hle_guest_strdup("C:\\gr2map.dat");

    win32hle_register(win32hle_kernel32);
    win32hle_register(win32hle_kernel32_ext);
    V_Close   = hle_resolve("CloseHandle");
    V_CreateFileA = hle_resolve("CreateFileA");
    V_CFMA = hle_resolve("CreateFileMappingA");
    V_CFMW = hle_resolve("CreateFileMappingW");
    V_OFMA = hle_resolve("OpenFileMappingA");
    V_OFMWA = hle_resolve("OpenFileMappingW");
    V_MapV = hle_resolve("MapViewOfFile");
    V_MapVEx = hle_resolve("MapViewOfFileEx");
    V_Unmap = hle_resolve("UnmapViewOfFile");
    V_Flush = hle_resolve("FlushViewOfFile");
    V_GMFNA = hle_resolve("GetMappedFileNameA");
    V_GMFNW = hle_resolve("GetMappedFileNameW");
    V_GLastErr = hle_resolve("GetLastError");
    ok("all new file-mapping shims resolve",
       V_Close && V_CreateFileA && V_CFMA && V_CFMW && V_OFMA && V_OFMWA &&
       V_MapV && V_MapVEx && V_Unmap && V_Flush && V_GMFNA && V_GMFNW && V_GLastErr);

    /* ---- 1. anonymous mapping: create, map, write, read back, unmap ---- */
    {
        uint32_t m = C6(V_CFMA, INVALID_HANDLE, 0, PAGE_READWRITE, 0, 4096, 0);
        ok("CreateFileMappingA anonymous succeeds", m != 0);
        uint32_t v = C5(V_MapV, m, FILE_MAP_WRITE, 0, 0, 4096);
        ok("MapViewOfFile on anonymous mapping succeeds", v != 0);
        if (v) {
            MEM32(v) = 0x12345678u;
            ok("anonymous view is writable and reads back", MEM32(v) == 0x12345678u);
            ok("FlushViewOfFile on an anonymous view succeeds", C2(V_Flush, v, 4096) == 1);
            ok("UnmapViewOfFile succeeds", C1(V_Unmap, v) == 1);
            ok("UnmapViewOfFile of an unmapped address fails honestly", C1(V_Unmap, v) == 0 &&
               hle_get_last_error() == ERROR_INVALID_ADDRESS);
        }
        ok("CloseHandle on mapping succeeds", C1(V_Close, m) == 1);
    }

    /* ---- 2. read-only mapping denies write access; bad handles/params fail ---- */
    {
        uint32_t m = C6(V_CFMA, INVALID_HANDLE, 0, PAGE_READONLY, 0, 4096, 0);
        ok("CreateFileMappingA read-only succeeds", m != 0);
        ok("FILE_MAP_WRITE on a read-only mapping is denied",
           C5(V_MapV, m, FILE_MAP_WRITE, 0, 0, 4096) == 0 && hle_get_last_error() == ERROR_ACCESS_DENIED);
        ok("a read view of a read-only mapping works",
           C5(V_MapV, m, FILE_MAP_READ, 0, 0, 4096) != 0);
        ok("invalid mapping handle fails with ERROR_INVALID_HANDLE",
           C5(V_MapV, 0x7F0u, FILE_MAP_READ, 0, 0, 4096) == 0 && hle_get_last_error() == ERROR_INVALID_HANDLE);
        ok("offset at/after the mapping size fails with ERROR_INVALID_PARAMETER",
           C5(V_MapV, m, FILE_MAP_READ, 0, 4096, 16) == 0 && hle_get_last_error() == ERROR_INVALID_PARAMETER);
        ok("a view longer than the mapping fails with ERROR_INVALID_PARAMETER",
           C5(V_MapV, m, FILE_MAP_READ, 0, 0, 8192) == 0 && hle_get_last_error() == ERROR_INVALID_PARAMETER);
        ok("CreateFileMappingA with an invalid protection fails",
           C6(V_CFMA, INVALID_HANDLE, 0, 0xFFFFu, 0, 4096, 0) == 0 && hle_get_last_error() == ERROR_INVALID_PARAMETER);
        ok("CloseHandle on read-only mapping succeeds", C1(V_Close, m) == 1);
    }

    /* ---- 3. file-backed mapping: write-through, flush, name, errors ---- */
    {
        uint32_t fh = C5(V_CreateFileA, g_guest_path, GENERIC_READ | GENERIC_WRITE, 0, 0, OPEN_EXISTING);
        ok("CreateFileA opens the backing file", fh != 0 && fh != INVALID_HANDLE);
        uint32_t m = C6(V_CFMA, fh, 0, PAGE_READWRITE, 0, 0, 0);   /* size 0: use the file's size */
        ok("CreateFileMappingA file-backed (size 0) succeeds", m != 0);
        ok("CloseHandle on the file handle succeeds", C1(V_Close, fh) == 1);
        uint32_t v = C5(V_MapV, m, FILE_MAP_ALL_ACCESS, 0, 0, 0);  /* len 0: to the end */
        ok("MapViewOfFile with len 0 maps the whole file", v != 0);
        if (v) {
            char got[11] = {0};
            memcpy(got, hle_ptr(v), 10);
            ok("file-backed view sees the file's contents", !strcmp(got, "GR2MAPDATA"));
            memcpy(hle_ptr(v), "GR2WRITTEN", 10);
            ok("FlushViewOfFile on the writable file view succeeds", C2(V_Flush, v, 0) == 1);
            char on_disk[11] = {0};
            int fd = open(host_file, O_RDONLY);
            if (fd >= 0) { (void)!read(fd, on_disk, 10); close(fd); }
            ok("flushed bytes are visible in the file", !strcmp(on_disk, "GR2WRITTEN"));
            ok("FlushViewOfFile with an out-of-view address fails",
               C2(V_Flush, v + 0x10000u, 16) == 0 && hle_get_last_error() == ERROR_INVALID_PARAMETER);
            uint32_t buf = hle_va(hle_alloc(128));
            uint32_t need = C4(V_GMFNA, 0xFFFFFFFFu, v, buf, 128);
            ok("GetMappedFileNameA returns the guest path",
               need == (uint32_t)strlen("C:\\gr2map.dat") && !strcmp((char *)hle_ptr(buf), "C:\\gr2map.dat"));
            ok("GetMappedFileNameA on an anonymous mapping fails", C4(V_GMFNA, 0xFFFFFFFFu, 0, buf, 128) == 0);
            ok("GetMappedFileNameA with a foreign process fails", C4(V_GMFNA, 0x1234u, v, buf, 128) == 0);
            uint32_t wbuf = hle_va(hle_alloc(256));
            uint32_t wneed = C4(V_GMFNW, 0xFFFFFFFFu, v, wbuf, 256);
            ok("GetMappedFileNameW returns a UTF-16 path",
               wneed == (uint32_t)strlen("C:\\gr2map.dat") && ((uint16_t *)hle_ptr(wbuf))[0] == 'C');
        }
        ok("UnmapViewOfFile on the file view succeeds", C1(V_Unmap, v) == 1);
        ok("CloseHandle on the file mapping succeeds", C1(V_Close, m) == 1);
    }

    /* ---- 4. named mappings: A/W creation, case-insensitive open, independence ---- */
    {
        uint32_t wn = hle_va(hle_alloc(128));
        hle_ansi_to_wide("GR2\\SharedMap", (uint16_t *)hle_ptr(wn), 64);
        uint32_t m1 = C6(V_CFMW, INVALID_HANDLE, 0, PAGE_READWRITE, 0, 4096, wn);
        ok("CreateFileMappingW with a name succeeds", m1 != 0);
        /* Opening the same name with different case must find the same object. */
        uint32_t an = hle_guest_strdup("gr2\\sharedmap");
        uint32_t m2 = C3(V_OFMA, FILE_MAP_ALL_ACCESS, 0, an);
        ok("OpenFileMappingA finds the name case-insensitively", m2 != 0);
        ok("a second CreateFileMappingW returns a handle with ERROR_ALREADY_EXISTS",
           C6(V_CFMW, INVALID_HANDLE, 0, PAGE_READWRITE, 0, 4096, wn) != 0 && hle_get_last_error() == ERROR_ALREADY_EXISTS);
        uint32_t m3 = C3(V_OFMWA, FILE_MAP_ALL_ACCESS, 0, wn);
        ok("OpenFileMappingW finds the name too", m3 != 0);
        ok("OpenFileMappingA with a missing name fails with ERROR_FILE_NOT_FOUND",
           C3(V_OFMA, FILE_MAP_READ, 0, hle_guest_strdup("GR2\\NoSuchMap")) == 0 &&
           hle_get_last_error() == ERROR_FILE_NOT_FOUND);
        ok("OpenFileMappingA with a NULL name fails with ERROR_INVALID_PARAMETER",
           C3(V_OFMA, FILE_MAP_READ, 0, 0) == 0 && hle_get_last_error() == ERROR_INVALID_PARAMETER);

        uint32_t v1 = C5(V_MapV, m1, FILE_MAP_WRITE, 0, 0, 4096);
        uint32_t v2 = C5(V_MapV, m2, FILE_MAP_READ, 0, 0, 4096);
        ok("views of both handles map", v1 != 0 && v2 != 0);
        if (v1 && v2) {
            MEM32(v1) = 0xCAFEBABEu;
            ok("handles to one named mapping share the same pages", MEM32(v2) == 0xCAFEBABEu);
            C1(V_Unmap, v1); C1(V_Unmap, v2);
        }
        /* Closing one handle must not free the object behind the others. */
        ok("CloseHandle on the creating handle succeeds", C1(V_Close, m1) == 1);
        uint32_t v3 = C5(V_MapV, m2, FILE_MAP_READ, 0, 0, 4096);
        ok("the opened handle still works after the creator closes", v3 != 0 && MEM32(v3) == 0xCAFEBABEu);
        if (v3) C1(V_Unmap, v3);
        ok("CloseHandle on the other handles succeeds", C1(V_Close, m2) == 1 && C1(V_Close, m3) == 1);
    }

    /* ---- 5. MapViewOfFileEx honours a free base; rejects busy/misaligned ---- */
    {
        uint32_t m = C6(V_CFMA, INVALID_HANDLE, 0, PAGE_READWRITE, 0, 4096, 0);
        ok("mapping for MapViewOfFileEx created", m != 0);
        uint32_t scratch = hle_vm_reserve(0, 0x100000u);
        uint32_t want = (scratch + 0x20000u) & ~0xFFFFu;
        hle_vm_release(scratch, 0x100000u);
        uint32_t v = C6(V_MapVEx, m, FILE_MAP_WRITE, 0, 0, 4096, want);
        ok("MapViewOfFileEx maps at the requested free base", v == want);
        if (v) { MEM32(v) = 0xFEEDFACEu; C1(V_Unmap, v); }
        ok("MapViewOfFileEx rejects a misaligned base",
           C6(V_MapVEx, m, FILE_MAP_WRITE, 0, 0, 4096, 0x1234u) == 0 &&
           hle_get_last_error() == ERROR_INVALID_PARAMETER);
        uint32_t busy = hle_vm_reserve(0, 0x20000u);
        ok("MapViewOfFileEx rejects a busy base with ERROR_INVALID_ADDRESS",
           C6(V_MapVEx, m, FILE_MAP_WRITE, 0, 0, 4096, busy) == 0 &&
           hle_get_last_error() == ERROR_INVALID_ADDRESS);
        hle_vm_release(busy, 0x20000u);
        ok("CloseHandle on the Ex mapping succeeds", C1(V_Close, m) == 1);
    }

    if (fails == 0) printf("kernel32_mapping_selftest: all checks passed\n");
    return fails != 0;
}
