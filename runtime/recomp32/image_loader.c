/*
 * image_loader.c - generic PE image loader for the recomp32 runtime.
 * See image_loader.h. Forged on the Fury3 (1995) recompilation.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "image_loader.h"

#pragma pack(push, 1)
typedef struct {
    char     name[8];
    uint32_t vsize;   /* VirtualSize        */
    uint32_t vaddr;   /* VirtualAddress (RVA)*/
    uint32_t rsize;   /* SizeOfRawData      */
    uint32_t roff;    /* PointerToRawData   */
    uint32_t reloc_ptr, lineno_ptr;
    uint16_t nreloc, nlineno;
    uint32_t chr;     /* Characteristics    */
} recomp_sechdr_t;
#pragma pack(pop)

/* Offsets/extents used below. Sizes from the file are untrusted; all the
 * arithmetic that combines them is done in uint64_t before it is checked
 * against the actual file size. */
#define RECOMP_DOS_LFANEW_OFF 0x3Cu
#define RECOMP_NT_BYTES       24u  /* signature (4) + COFF file header (20) */
#define RECOMP_OPT_MIN        64u  /* reaches SizeOfHeaders at optional +60 */

uint32_t recomp_load_image(const char* path, uint32_t image_base) {
    FILE* f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "[loader] cannot open %s\n", path); return 0; }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz <= 0x40) { fclose(f); return 0; }
    uint8_t* buf = (uint8_t*)malloc((size_t)sz);
    if (!buf) { fclose(f); return 0; }
    if (fread(buf, 1, (size_t)sz, f) != (size_t)sz) { fclose(f); free(buf); return 0; }
    fclose(f);

    if (buf[0] != 'M' || buf[1] != 'Z') { free(buf); fprintf(stderr, "[loader] not MZ\n"); return 0; }
    uint32_t e_lfanew = *(uint32_t*)(buf + RECOMP_DOS_LFANEW_OFF);
    /* No pointer derived from e_lfanew may be formed until it is proven to
     * leave room for the NT header and the shortest optional header we read. */
    if (e_lfanew < 0x40u ||
        (uint64_t)e_lfanew + RECOMP_NT_BYTES + RECOMP_OPT_MIN > (uint64_t)sz) {
        fprintf(stderr, "[loader] bad e_lfanew 0x%08X for file of %ld bytes\n",
                e_lfanew, sz);
        free(buf);
        return 0;
    }
    uint8_t* nt = buf + e_lfanew;
    if (nt[0] != 'P' || nt[1] != 'E') { free(buf); fprintf(stderr, "[loader] not PE\n"); return 0; }

    uint16_t nsec   = *(uint16_t*)(nt + 6);
    uint16_t opt_sz = *(uint16_t*)(nt + 20);
    if (opt_sz < RECOMP_OPT_MIN) {
        fprintf(stderr, "[loader] optional header size %u < %u\n",
                (unsigned)opt_sz, (unsigned)RECOMP_OPT_MIN);
        free(buf); return 0;
    }
    uint64_t opt_off = (uint64_t)e_lfanew + RECOMP_NT_BYTES;
    if ((uint64_t)opt_sz > (uint64_t)sz - opt_off) {
        fprintf(stderr, "[loader] optional header truncated: %u bytes at offset %llu of %ld\n",
                (unsigned)opt_sz, (unsigned long long)opt_off, sz);
        free(buf); return 0;
    }
    uint64_t sec_off = opt_off + opt_sz;
    uint64_t sec_bytes = (uint64_t)nsec * (uint64_t)sizeof(recomp_sechdr_t);
    if (sec_bytes > (uint64_t)sz - sec_off) {
        fprintf(stderr, "[loader] section table truncated: %u sections (%llu bytes) at offset %llu of %ld\n",
                (unsigned)nsec, (unsigned long long)sec_bytes,
                (unsigned long long)sec_off, sz);
        free(buf); return 0;
    }
    recomp_sechdr_t* sec = (recomp_sechdr_t*)(buf + (size_t)sec_off);

    /* vaddr + size and the page round-up can each exceed 32 bits on a hostile
     * header; the loader's result type cannot represent such a span, so reject
     * it rather than wrapping to 0 (or past image_base + span). */
    uint64_t max_end = 0;
    for (uint32_t i = 0; i < nsec; i++) {
        uint64_t vs = sec[i].vsize ? (uint64_t)sec[i].vsize : (uint64_t)sec[i].rsize;
        uint64_t end = (uint64_t)sec[i].vaddr + vs;
        if (end > max_end) max_end = end;
    }
    uint64_t span64 = (max_end + 0xFFFu) & ~(uint64_t)0xFFFu;
    if (span64 == 0 || span64 > 0xFFFFFFFFu ||
        (uint64_t)image_base + span64 > 0x100000000ULL) {
        fprintf(stderr, "[loader] image span overflow: max_end 0x%016llX span 0x%016llX base 0x%08X\n",
                (unsigned long long)max_end, (unsigned long long)span64, image_base);
        free(buf); return 0;
    }
    uint32_t span = (uint32_t)span64;

    void* view = VirtualAlloc((void*)(uintptr_t)image_base, span,
                              MEM_RESERVE | MEM_COMMIT, PAGE_EXECUTE_READWRITE);
    if (!view) {
        /* Already reserved for us -- a launcher that held the range through
         * loader init (see the MSVC-host case) leaves it reserved, and
         * MEM_RESERVE over an existing reservation fails. Commit into it. */
        view = VirtualAlloc((void*)(uintptr_t)image_base, span,
                            MEM_COMMIT, PAGE_EXECUTE_READWRITE);
    }
    if (!view) {
        fprintf(stderr, "[loader] VirtualAlloc fixed @ 0x%08X failed (link host at a high base)\n",
                image_base);
        free(buf);
        return 0;
    }
    memset(view, 0, span);  /* zeroes .bss */

    /* The headers, too. Windows maps SizeOfHeaders bytes at the image base, and
     * code does read them back -- walking your own import directory to find an
     * IAT slot by name needs e_lfanew to be there. Copying only sections leaves
     * that whole region zeroed. */
    {
        uint32_t hdr = *(uint32_t*)(nt + 24 + 60);   /* OptionalHeader.SizeOfHeaders */
        if (hdr > (uint32_t)sz) {
            fprintf(stderr, "[loader] SizeOfHeaders 0x%08X exceeds file size 0x%08lX; clamped\n",
                    hdr, (unsigned long)sz);
            hdr = (uint32_t)sz;
        }
        if (hdr > span) {
            fprintf(stderr, "[loader] SizeOfHeaders 0x%08X exceeds span 0x%08X; clamped\n",
                    hdr, span);
            hdr = span;
        }
        memcpy(view, buf, hdr);
    }

    for (uint32_t i = 0; i < nsec; i++) {
        /* "Uninitialized" is PointerToRawData == 0 -- no file backing -- not
         * SizeOfRawData == 0. MSVC happens to set both to 0 for .bss, so the
         * size test worked by coincidence; Watcom does not. It puts the *memory*
         * size in SizeOfRawData and leaves PointerToRawData 0, so on a Watcom
         * binary the size test treats .bss as initialized and copies
         * SizeOfRawData bytes from file offset 0 -- for Nocturne, 42 MB read out
         * of a 1.9 MB buffer, over a .bss that is supposed to stay zeroed. */
        if (sec[i].roff == 0) continue;
        uint32_t va = image_base + sec[i].vaddr;
        uint32_t n  = sec[i].rsize;
        /* Windows maps a section's file data up to its VirtualSize rounded up
         * to the page, not to the VirtualSize itself: bytes past it in the last
         * page come from the file. Tiberian Sun's Game.exe keeps a patch's code
         * there (0x006C9A10, past .text's 0x006C9A0D), and cut at VirtualSize
         * it ran as zeroes, `add [eax], al`, and faulted. */
        if (sec[i].vsize) {
            uint64_t rounded = ((uint64_t)sec[i].vsize + 0xFFFu) & ~(uint64_t)0xFFFu;
            if (rounded < (uint64_t)n) n = (uint32_t)rounded;
        }
        /* Never read past what the file actually holds: these headers come from
         * disk and a truncated or hostile one must not turn into an overread. */
        if (sec[i].roff >= (uint32_t)sz) {
            fprintf(stderr, "[loader] section %u raw offset 0x%08X outside file; skipped\n",
                    i, sec[i].roff);
            continue;
        }
        if (n > (uint32_t)sz - sec[i].roff) {
            fprintf(stderr, "[loader] section %u raw data truncated to %u bytes\n",
                    i, (uint32_t)sz - sec[i].roff);
            n = (uint32_t)sz - sec[i].roff;
        }
        if (sec[i].vaddr >= span) {
            fprintf(stderr, "[loader] section %u VA 0x%08X outside span 0x%08X; skipped\n",
                    i, sec[i].vaddr, span);
            continue;
        }
        if (n > span - sec[i].vaddr) {
            fprintf(stderr, "[loader] section %u data truncated to %u bytes\n",
                    i, span - sec[i].vaddr);
            n = span - sec[i].vaddr;
        }
        memcpy((void*)(uintptr_t)va, buf + sec[i].roff, n);
    }
    free(buf);
    return span;
}
