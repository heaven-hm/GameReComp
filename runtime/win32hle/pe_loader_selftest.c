/*
 * pe_loader_selftest.c - builds a minimal but real PE32 in memory (one section,
 * two KERNEL32 imports, one HIGHLOW base relocation), writes it to a temp file,
 * then maps / binds / relocates it and checks each step. Proves the loader with
 * no game binary. Flat, build -m32 -no-pie so the image's ImageBase
 * (0x00400000) is free to map fixed; based, it is free in guest memory.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pe_format.h"
#include "pe_loader.h"
#include "win32hle.h"

/* No guest is run here, but host_lite's recomp_lookup references the dispatch
 * table the lifted program would define; an empty one satisfies the link.
 * hle_exit/hle_thread_id normally come from kernel32.c, which this single
 * loader-target build deliberately does not link. */
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { 0, 0 } };
const uint32_t recomp_dispatch_count = 0;
/* Weak so the full selftest build (which links kernel32.c) still wins. */
__attribute__((weak)) void hle_exit(int code) { (void)code; }
__attribute__((weak)) uint32_t hle_thread_id(void) { return 1u; }

static int fails;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }

#define IMAGE_BASE 0x00400000u
#define SEC_RVA    0x1000u
#define SEC_FOFF   0x200u
#define FILE_SZ    0x800u

/* RVAs laid out inside the one section (offset = RVA - SEC_RVA). */
#define RVA_MARKER   0x1000u   /* a known dword, to prove the section mapped   */
#define RVA_RELTGT   0x1004u   /* an absolute VA, target of the base reloc     */
#define RVA_IMPDESC  0x1010u   /* import descriptors (one + null)              */
#define RVA_ILT      0x1040u   /* import name table                            */
#define RVA_IAT      0x1050u   /* import address table (bind writes here)      */
#define RVA_NAME0    0x1060u   /* hint+"GetTickCount"                          */
#define RVA_NAME1    0x1080u   /* hint+"HeapAlloc"                             */
#define RVA_DLLNAME  0x10A0u   /* "KERNEL32.dll"                               */
#define RVA_RELOC    0x1100u   /* base reloc block                             */
/* Delay imports: one companion DLL (by name + by ordinal) and one shim DLL. */
#define RVA_DDELAY   0x1300u   /* four ImgDelayDescr records (last is null)     */
#define RVA_DINT0    0x1420u   /* companion INT: name, ordinal, null            */
#define RVA_DIAT0    0x1430u   /* companion IAT (bound eagerly at boot)         */
#define RVA_DINT1    0x1440u   /* shim INT: name, null                          */
#define RVA_DIAT1    0x1450u   /* shim IAT                                      */
#define RVA_DINT2    0x1460u   /* old-style (VA fields) INT: name VA, null      */
#define RVA_DIAT2    0x1470u   /* old-style IAT                                 */
#define RVA_DNAME0   0x1480u   /* hint + "MessageBoxA"                          */
#define RVA_DDLL0    0x14A0u   /* "GAME.dll"                                    */
#define RVA_DDLL1    0x14C0u   /* "KERNEL32.dll"                                */
#define RVA_DDLL2    0x14E0u   /* "KERNEL32.dll" (VA-based descriptor)          */
#define COMP_FN      0x7F123456u
#define COMP_ORD     0x7F123460u

static void put32(uint8_t *sec, uint32_t rva, uint32_t v) { memcpy(sec + (rva - SEC_RVA), &v, 4); }
static void put16(uint8_t *sec, uint32_t rva, uint16_t v) { memcpy(sec + (rva - SEC_RVA), &v, 2); }
static void puts_(uint8_t *sec, uint32_t rva, const char *s) { strcpy((char *)sec + (rva - SEC_RVA), s); }

static void build_pe(const char *path) {
    uint8_t *img = (uint8_t *)calloc(1, FILE_SZ);

    pe_dos_header *dos = (pe_dos_header *)img;
    dos->e_magic = 0x5A4D;
    dos->e_lfanew = 0x40;

    pe_nt_headers32 *nt = (pe_nt_headers32 *)(img + 0x40);
    nt->Signature = 0x00004550;
    nt->FileHeader.Machine = 0x14C;                 /* i386 */
    nt->FileHeader.NumberOfSections = 1;
    nt->FileHeader.SizeOfOptionalHeader = sizeof(pe_opt_header32);
    pe_opt_header32 *opt = &nt->OptionalHeader;
    opt->Magic = 0x10B;
    opt->AddressOfEntryPoint = SEC_RVA;             /* pretend code starts here */
    opt->ImageBase = IMAGE_BASE;
    opt->SectionAlignment = 0x1000;
    opt->FileAlignment = 0x200;
    opt->SizeOfImage = 0x2000;                      /* headers page + one section page */
    opt->SizeOfHeaders = SEC_FOFF;
    opt->NumberOfRvaAndSizes = 16;
    opt->DataDirectory[PE_DIR_IMPORT].VirtualAddress = RVA_IMPDESC;
    opt->DataDirectory[PE_DIR_IMPORT].Size = 2 * sizeof(pe_import_descriptor);
    opt->DataDirectory[PE_DIR_DELAY_IMPORT].VirtualAddress = RVA_DDELAY;
    opt->DataDirectory[PE_DIR_DELAY_IMPORT].Size = 4 * sizeof(pe_delay_import_descriptor);
    opt->DataDirectory[PE_DIR_BASERELOC].VirtualAddress = RVA_RELOC;
    opt->DataDirectory[PE_DIR_BASERELOC].Size = 10;

    pe_section_header *sh = (pe_section_header *)(img + 0x40 + sizeof(pe_nt_headers32));
    memcpy(sh->Name, ".data", 5);
    sh->VirtualSize = 0x600;
    sh->VirtualAddress = SEC_RVA;
    sh->SizeOfRawData = 0x600;
    sh->PointerToRawData = SEC_FOFF;
    sh->Characteristics = 0xC0000040u;              /* initialised data, r/w */

    uint8_t *sec = img + SEC_FOFF;                  /* section raw data */
    put32(sec, RVA_MARKER, 0xCAFEBABEu);
    put32(sec, RVA_RELTGT, 0x00401000u);            /* an in-image absolute VA */

    pe_import_descriptor *d = (pe_import_descriptor *)(sec + (RVA_IMPDESC - SEC_RVA));
    d[0].OriginalFirstThunk = RVA_ILT;
    d[0].Name = RVA_DLLNAME;
    d[0].FirstThunk = RVA_IAT;
    /* d[1] stays zero: the terminator */

    put32(sec, RVA_ILT + 0, RVA_NAME0);
    put32(sec, RVA_ILT + 4, RVA_NAME1);
    put32(sec, RVA_ILT + 8, 0);
    put32(sec, RVA_IAT + 0, RVA_NAME0);             /* loader overwrites [0],[1] */
    put32(sec, RVA_IAT + 4, RVA_NAME1);
    put32(sec, RVA_IAT + 8, 0);
    put16(sec, RVA_NAME0, 0); puts_(sec, RVA_NAME0 + 2, "GetTickCount");
    put16(sec, RVA_NAME1, 0); puts_(sec, RVA_NAME1 + 2, "HeapAlloc");
    puts_(sec, RVA_DLLNAME, "KERNEL32.dll");

    /* one HIGHLOW reloc for the dword at page+0x004 (== RVA_RELTGT) */
    put32(sec, RVA_RELOC + 0, SEC_RVA);             /* block VirtualAddress (page) */
    put32(sec, RVA_RELOC + 4, 10);                  /* SizeOfBlock = 8 + one entry */
    put16(sec, RVA_RELOC + 8, (uint16_t)((PE_REL_HIGHLOW << 12) | 0x004));

    /* RVA-based delay descriptors: a bundled DLL (name + ordinal) whose
     * exports a companion resolver answers, and a system DLL that falls
     * through to the ordinary shim resolver. The IAT slots start out holding
     * fake stub RVAs -- the binder must overwrite them. */
    pe_delay_import_descriptor *dd = (void *)(sec + (RVA_DDELAY - SEC_RVA));
    dd[0].grAttrs = PE_DELAY_ATTR_RVA;
    dd[0].szName = RVA_DDLL0;
    dd[0].pIAT = RVA_DIAT0;
    dd[0].pINT = RVA_DINT0;
    dd[1].grAttrs = PE_DELAY_ATTR_RVA;
    dd[1].szName = RVA_DDLL1;
    dd[1].pIAT = RVA_DIAT1;
    dd[1].pINT = RVA_DINT1;
    /* Old linkers stored VAs, not RVAs, and set grAttrs to 0. */
    dd[2].grAttrs = 0;
    dd[2].szName = IMAGE_BASE + RVA_DDLL2;
    dd[2].pIAT = IMAGE_BASE + RVA_DIAT2;
    dd[2].pINT = IMAGE_BASE + RVA_DINT2;
    /* dd[3] stays zero: the terminator */

    put32(sec, RVA_DINT0, RVA_DNAME0);
    put32(sec, RVA_DINT0 + 4, 0x80000000u | 0x2Au);
    put32(sec, RVA_DIAT0, 0x1234u);            /* stale __delayLoadHelper stub */
    put32(sec, RVA_DIAT0 + 4, 0x1238u);
    put32(sec, RVA_DINT1, RVA_NAME0);          /* share GetTickCount's hint/name */
    put32(sec, RVA_DIAT1, 0x123Cu);
    put32(sec, RVA_DINT2, IMAGE_BASE + RVA_NAME0);
    put32(sec, RVA_DIAT2, 0x1240u);
    put16(sec, RVA_DNAME0, 0); puts_(sec, RVA_DNAME0 + 2, "MessageBoxA");
    puts_(sec, RVA_DDLL0, "GAME.dll");
    puts_(sec, RVA_DDLL1, "KERNEL32.dll");
    puts_(sec, RVA_DDLL2, "KERNEL32.dll");

    FILE *f = fopen(path, "wb");
    fwrite(img, 1, FILE_SZ, f);
    fclose(f);
    free(img);
}

/* The loader only needs shim *names*, not their implementations: keep this
 * selftest independent of kernel32.c so it builds as a single small target. */
static void s_GetTickCount(void) { }
static void s_HeapAlloc(void) { }
static const win32hle_shim test_shims[] = {
    { "GetTickCount", s_GetTickCount }, { "HeapAlloc", s_HeapAlloc }, { 0, 0 }
};

/* A bundled DLL's lifted exports, answering only the GAME.dll delay imports. */
static uint32_t test_companion(const char *dll, const char *name) {
    if (!strcmp(dll, "GAME.dll") && !strcmp(name, "MessageBoxA")) return COMP_FN;
    if (!strcmp(dll, "GAME.dll") && !strcmp(name, "game.dll#42")) return COMP_ORD;
    return 0;
}

int main(void) {
    win32hle_register(test_shims);                   /* so hle_resolve answers */
    const char *path = "/tmp/win32hle_mini_pe.bin";
    build_pe(path);

    pe_image img;
    ok("recomp_pe_map succeeds", recomp_pe_map(path, &img) == 0);
    ok("mapped at ImageBase",    img.base == IMAGE_BASE);
    ok("entry point VA",         img.entry == IMAGE_BASE + SEC_RVA);
    ok("section data at its VA",  MEM32(img.base + RVA_MARKER) == 0xCAFEBABEu);

    int unresolved = recomp_pe_bind_companions(&img, hle_resolve, test_companion);
    ok("all imports (incl. delay) resolved", unresolved == 0);
    uint32_t *iat = (uint32_t *)hle_ptr(img.base + RVA_IAT);
    ok("IAT[0] -> GetTickCount shim", iat[0] == hle_resolve("GetTickCount") && iat[0] != 0);
    ok("IAT[1] -> HeapAlloc shim",    iat[1] == hle_resolve("HeapAlloc")    && iat[1] != 0);

    /* The delay IAT slots held fake stub RVAs before the bind. They must now
     * hold a lifted companion export (by name and by ordinal) and a shim. */
    uint32_t *di0 = (uint32_t *)hle_ptr(img.base + RVA_DIAT0);
    uint32_t *di1 = (uint32_t *)hle_ptr(img.base + RVA_DIAT1);
    ok("delay IAT[0] -> companion export",     di0[0] == COMP_FN);
    ok("delay IAT[1] -> companion ordinal",     di0[1] == COMP_ORD);
    ok("delay IAT[2] -> GetTickCount shim",     di1[0] == hle_resolve("GetTickCount"));
    uint32_t *di2 = (uint32_t *)hle_ptr(img.base + RVA_DIAT2);
    ok("old-style VA delay IAT -> GetTickCount", di2[0] == hle_resolve("GetTickCount"));

    /* relocate the image +0x100000 and confirm the HIGHLOW fixup moved the
     * absolute VA by exactly the delta. */
    uint32_t before = MEM32(img.base + RVA_RELTGT);
    recomp_pe_relocate(img.base, RVA_RELOC, 10, 0x00100000);
    uint32_t after = MEM32(img.base + RVA_RELTGT);
    ok("base reloc applied the delta", before == 0x00401000u && after == 0x00501000u);

    if (fails == 0) printf("pe_loader_selftest: all checks passed\n");
    return fails != 0;
}
