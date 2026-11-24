/*
 * pe_validation_selftest.c - regression tests for PE32 header/section validation.
 *
 * The loader must reject malformed executables with a diagnostic instead of
 * reading past the file buffer or writing outside the mapped image. Each case
 * starts from a real (minimal) PE32 and corrupts exactly one field. The last
 * cases map untouched images to prove validation does not reject valid PEs.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "pe_format.h"
#include "pe_loader.h"
#include "win32hle.h"

/* host_lite's recomp_lookup needs a dispatch table; no guest code runs here. */
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;

#define NT_OFF     0x40u
#define SEC_RVA    0x1000u
#define SEC_FOFF   0x200u
#define FILE_SZ    0x400u
#define SPAN       0x2000u
#define OPT_OFF    (NT_OFF + 24u)
#define SH_OFF     (NT_OFF + 24u + 0xE0u)
#define BASE_A     0x00400000u
#define BASE_B     0x00500000u

static int fails;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }

static uint8_t *build_valid(uint32_t image_base) {
    uint8_t *img = (uint8_t *)calloc(1, FILE_SZ);
    pe_dos_header *dos = (pe_dos_header *)img;
    dos->e_magic = 0x5A4D;
    dos->e_lfanew = NT_OFF;

    pe_nt_headers32 *nt = (pe_nt_headers32 *)(img + NT_OFF);
    nt->Signature = 0x00004550;
    nt->FileHeader.Machine = 0x014C;
    nt->FileHeader.NumberOfSections = 1;
    nt->FileHeader.SizeOfOptionalHeader = 0xE0;

    pe_opt_header32 *opt = &nt->OptionalHeader;
    opt->Magic = 0x10B;
    opt->AddressOfEntryPoint = SEC_RVA;
    opt->ImageBase = image_base;
    opt->SectionAlignment = 0x1000;
    opt->FileAlignment = 0x200;
    opt->SizeOfImage = SPAN;
    opt->SizeOfHeaders = SEC_FOFF;
    opt->NumberOfRvaAndSizes = 16;

    pe_section_header *sh = (pe_section_header *)(img + SH_OFF);
    memcpy(sh->Name, ".data", 5);
    sh->VirtualSize = 0x200;
    sh->VirtualAddress = SEC_RVA;
    sh->SizeOfRawData = 0x200;
    sh->PointerToRawData = SEC_FOFF;
    sh->Characteristics = 0xC0000040u;

    uint32_t marker = 0xCAFEBABEu;
    memcpy(img + SEC_FOFF, &marker, 4);
    return img;
}

static void put32(uint8_t *img, uint32_t off, uint32_t v) { memcpy(img + off, &v, 4); }
static void put16(uint8_t *img, uint32_t off, uint16_t v) { memcpy(img + off, &v, 2); }

static const char *tmp_path(const char *tag) {
    static char path[256];
    snprintf(path, sizeof path, "%s/agent1-pe-val-%ld-%s.bin",
             getenv("TMPDIR") ? getenv("TMPDIR") : "/tmp", (long)getpid(), tag);
    return path;
}

/* Write len bytes and assert recomp_pe_map rejects the image. */
static int rejects(const char *tag, const uint8_t *img, size_t len) {
    const char *path = tmp_path(tag);
    FILE *f = fopen(path, "wb");
    if (!f) { printf("FAIL cannot create %s\n", path); fails++; return 0; }
    fwrite(img, 1, len, f);
    fclose(f);
    pe_image out;
    memset(&out, 0, sizeof out);
    int rc = recomp_pe_map(path, &out);
    unlink(path);
    if (rc == 0 && out.base) hle_vm_release(out.base, out.span); /* don't hold the VA range on a false pass */
    return rc != 0;
}

int main(void) {
    uint8_t *img = build_valid(BASE_A);

    ok("short DOS header rejected", rejects("short", img, 0x30));
    ok("bad MZ rejected", (img[0] = 'N', rejects("mz", img, FILE_SZ)));
    img[0] = 'M';

    put32(img, 0x3C, 0xFFFFFFF0u);
    ok("overflowing e_lfanew rejected", rejects("lfanew-huge", img, FILE_SZ));
    put32(img, 0x3C, 0x20u);
    ok("e_lfanew inside DOS header rejected", rejects("lfanew-small", img, FILE_SZ));
    put32(img, 0x3C, NT_OFF);

    put16(img, NT_OFF + 20u, 0x80);                /* FileHeader.SizeOfOptionalHeader */
    ok("small optional header rejected", rejects("sizeopt", img, FILE_SZ));
    put16(img, NT_OFF + 20u, 0xE0);

    put16(img, NT_OFF + 6u, 0);                   /* NumberOfSections = 0 */
    ok("zero sections rejected", rejects("nsec0", img, FILE_SZ));
    put16(img, NT_OFF + 6u, 200);                 /* > PE_MAX_SECTIONS */
    ok("excessive sections rejected", rejects("nsec200", img, FILE_SZ));
    put16(img, NT_OFF + 6u, 1);

    /* Move the NT headers near EOF so the section table runs past the file.
     * e_lfanew + sizeof(nt) == 0x3F8 still fits; + one section header == 0x420
     * does not. */
    uint8_t *shifted = (uint8_t *)calloc(1, FILE_SZ);
    memcpy(shifted, img, FILE_SZ);
    memcpy(shifted + 0x300, shifted + NT_OFF, sizeof(pe_nt_headers32));
    put32(shifted, 0x3C, 0x300);
    ok("section table past EOF rejected", rejects("sectab", shifted, FILE_SZ));
    free(shifted);

    put32(img, OPT_OFF + 60u, 0x1000);            /* SizeOfHeaders > file */
    ok("oversized headers rejected", rejects("hdrbig", img, FILE_SZ));
    put32(img, OPT_OFF + 60u, SEC_FOFF);

    put32(img, OPT_OFF + 56u, 0);                 /* SizeOfImage = 0 */
    ok("zero SizeOfImage rejected", rejects("img0", img, FILE_SZ));
    put32(img, OPT_OFF + 56u, 0xFFFFFFF0u);       /* round_up overflow */
    ok("overflowing SizeOfImage rejected", rejects("imgovf", img, FILE_SZ));
    put32(img, OPT_OFF + 56u, SPAN);

    put32(img, SH_OFF + 12u, 0x3000);             /* section VA > span */
    ok("section VA past image rejected", rejects("secva", img, FILE_SZ));
    put32(img, SH_OFF + 12u, SEC_RVA);

    put32(img, SH_OFF + 8u, 0x2000);              /* VA + VirtualSize > span */
    ok("section size past image rejected", rejects("secsize", img, FILE_SZ));
    put32(img, SH_OFF + 8u, 0x200);

    put32(img, SH_OFF + 20u, 0x500);              /* raw data ptr > file */
    ok("section raw ptr past EOF rejected", rejects("secptr", img, FILE_SZ));
    put32(img, SH_OFF + 20u, SEC_FOFF);

    put32(img, OPT_OFF + 16u, SPAN);              /* entry RVA == span */
    ok("entry point outside image rejected", rejects("entry", img, FILE_SZ));
    put32(img, OPT_OFF + 16u, SEC_RVA);

    /* A valid image with no data directories must still map (the loader must
     * not read DataDirectory entries beyond NumberOfRvaAndSizes). */
    put32(img, OPT_OFF + 92u, 0);                 /* NumberOfRvaAndSizes = 0 */
    pe_image nodir;
    const char *p0 = tmp_path("nodir");
    FILE *f0 = fopen(p0, "wb");
    fwrite(img, 1, FILE_SZ, f0); fclose(f0);
    memset(&nodir, 0, sizeof nodir);
    ok("no-data-directory PE maps", recomp_pe_map(p0, &nodir) == 0);
    if (nodir.base) hle_vm_release(nodir.base, nodir.span);
    unlink(p0);
    put32(img, OPT_OFF + 92u, 16);

    /* Untouched control: validation must accept a real PE and map its section. */
    const char *p1 = tmp_path("valid");
    f0 = fopen(p1, "wb");
    fwrite(img, 1, FILE_SZ, f0); fclose(f0);
    pe_image good;
    memset(&good, 0, sizeof good);
    ok("valid PE maps", recomp_pe_map(p1, &good) == 0);
    ok("mapped at ImageBase", good.base == BASE_A);
    ok("entry point VA", good.entry == BASE_A + SEC_RVA);
    ok("section data mapped", MEM32(good.base + SEC_RVA) == 0xCAFEBABEu);

    /* A relocation block that claims more bytes than the directory contains
     * must be ignored, not applied to an adjacent dword. */
    uint32_t before = MEM32(good.base + SEC_RVA);
    MEM32(good.base + SEC_RVA + 0x100u) = SEC_RVA;      /* block Page RVA */
    MEM32(good.base + SEC_RVA + 0x104u) = 10;           /* SizeOfBlock = 10 */
    MEM16(good.base + SEC_RVA + 0x108u) = (uint16_t)0x3000u; /* + page+0 marker */
    recomp_pe_relocate(good.base, SEC_RVA + 0x100u, 4, 0x1000);
    ok("truncated reloc block ignored", MEM32(good.base + SEC_RVA) == before);

    if (good.base) hle_vm_release(good.base, good.span);
    unlink(p1);
    free(img);

    if (fails == 0) printf("pe_validation_selftest: all checks passed\n");
    return fails != 0;
}
