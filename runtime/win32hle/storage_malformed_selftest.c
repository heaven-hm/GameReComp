/*
 * storage_malformed_selftest.c - StgOpenStorage must reject truncated /
 * inconsistent compound files instead of dereferencing a missing chain.
 *
 * Regression: read_cfb() used to read dir+116 and memcpy from chain() results
 * without checking them; a file with a valid signature but no directory
 * chain (or no mini stream for a mini-stream stream) crashed the process.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

#define STG_E_INVALIDHEADER 0x800300FBu
#define ENDOFCHAIN 0xFFFFFFFEu
#define FREESECT   0xFFFFFFFFu

static int fails;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }

static void put16(uint8_t *b, uint32_t o, uint16_t v) { memcpy(b + o, &v, 2); }
static void put32(uint8_t *b, uint32_t o, uint32_t v) { memcpy(b + o, &v, 4); }

/* A v3 compound file header: 512-byte sectors, 64-byte mini sectors. */
static void header(uint8_t *b, uint32_t nfat, uint32_t dir0, uint32_t fat0, uint32_t nmini) {
    static const uint8_t sig[8] = { 0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1 };
    memset(b, 0, 512);
    memcpy(b, sig, 8);
    put16(b, 24, 0x3E); put16(b, 26, 3); /* minor/major */
    put16(b, 28, 0xFFFE);               /* byte order */
    put16(b, 30, 9);                    /* 512-byte sectors */
    put16(b, 32, 6);                    /* 64-byte mini sectors */
    put32(b, 40, 0);                    /* v3: 0 directory sectors in header */
    put32(b, 44, nfat);
    put32(b, 48, dir0);
    put32(b, 56, 0x1000);               /* mini stream cutoff */
    put32(b, 60, ENDOFCHAIN);           /* first mini FAT sector */
    put32(b, 64, nmini);
    put32(b, 68, ENDOFCHAIN); put32(b, 72, 0);
    if (nfat) put32(b, 76, fat0);
}

static void write_file(const char *path, const uint8_t *b, size_t n) {
    FILE *f = fopen(path, "wb");
    fwrite(b, 1, n, f);
    fclose(f);
}

static uint32_t open_storage(const char *guest_path, uint32_t out) {
    uint16_t *w = (uint16_t *)hle_alloc(256);       /* the shim reads a guest pointer */
    memset(w, 0, 256);
    for (int i = 0; guest_path[i] && i < 127; i++) w[i] = (uint8_t)guest_path[i];
    uint32_t a[6] = { hle_va(w), 0, 0x10u, 0, 0, out };
    uint32_t shim = hle_resolve("StgOpenStorage");
    if (!shim) { printf("FAIL StgOpenStorage shim not registered\n"); fails++; return 0xFFFFFFFFu; }
    return hle_call_guest(shim, 6, a);
}

int main(void) {
    win32hle_register(win32hle_kernel32);
    win32hle_register(win32hle_ole32);
    win32hle_register(win32hle_storage);
    hle_set_drive('Z', "/");
    mach_enter();

    struct { uint32_t stg; uint8_t pad[64]; } *g = hle_alloc(sizeof *g);
    uint8_t *f = (uint8_t *)calloc(1, 4096);

    /* 1. Signature only: shorter than a header. */
    memcpy(f, "\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", 8);
    write_file("/tmp/agent2-cfb-short.bin", f, 8);
    g->stg = 1;
    ok("short file rejected", open_storage("Z:\\tmp\\agent2-cfb-short.bin", hle_va(&g->stg)) == STG_E_INVALIDHEADER && g->stg == 0);

    /* 2. Valid header, no directory chain: chain() returns NULL, dlen == 0. */
    header(f, 1, ENDOFCHAIN, 2, 0);
    write_file("/tmp/agent2-cfb-nodir.bin", f, 1536);
    g->stg = 1;
    ok("missing directory chain rejected", open_storage("Z:\\tmp\\agent2-cfb-nodir.bin", hle_va(&g->stg)) == STG_E_INVALIDHEADER && g->stg == 0);

    /* 3. Directory exists, a small stream points into a missing mini stream.
     *    Directory occupies file sectors 0 and 1; FAT is at file sector 2. */
    header(f, 1, 0, 2, 0);
    uint32_t *fat = (uint32_t *)(f + 1536);
    fat[0] = 1; fat[1] = ENDOFCHAIN;
    static const uint16_t rootname[] = { 'R','o','o','t',' ','E','n','t','r','y',0 };
    uint8_t *root = f + 512;
    memset(root, 0, 128);
    memcpy(root, rootname, sizeof rootname);
    put16(root, 64, 22); root[66] = 5; root[67] = 1; put32(root, 76, 1);
    put32(root, 116, ENDOFCHAIN);
    uint8_t *child = f + 1024;
    memset(child, 0, 128);
    const uint16_t sname[] = { 'S', 0 };
    memcpy(child, sname, sizeof sname);
    put16(child, 64, 4); child[66] = 2; child[67] = 1;
    put32(child, 116, 0);                     /* mini-stream sector 0 */
    put32(child, 120, 18);                    /* size < 4096 -> mini stream */
    write_file("/tmp/agent2-cfb-nomini.bin", f, 2048);
    g->stg = 1;
    ok("missing mini stream rejected", open_storage("Z:\\tmp\\agent2-cfb-nomini.bin", hle_va(&g->stg)) == STG_E_INVALIDHEADER && g->stg == 0);

    unlink("/tmp/agent2-cfb-short.bin");
    unlink("/tmp/agent2-cfb-nodir.bin");
    unlink("/tmp/agent2-cfb-nomini.bin");
    free(f);
    mach_leave();
    if (fails == 0) printf("storage_malformed_selftest: all checks passed\n");
    return fails != 0;
}
