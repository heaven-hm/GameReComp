/*
 * pe_loader.c - see pe_loader.h. Maps a 32-bit PE into guest memory and binds its IAT
 * to resolver-provided addresses. No windows.h: that is the point.
 *
 * Every field that comes from the file is treated as untrusted. Header sizes and
 * counts are validated against the file length and the image span before any
 * memcpy, and every RVA is range-checked before it reaches hle_ptr(). A malformed
 * executable must produce a "[pe] ..." diagnostic and a nonzero return, never a
 * read past the file buffer or a write outside the mapped image.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include "pe_format.h"
#include "pe_loader.h"
#include "win32hle.h"

#define PAGE 0x1000u
#define PE32_OPT_SIZE 0xE0u    /* sizeof(pe_opt_header32): the standard PE32 optional header */
#define PE_MAX_SECTIONS 96u    /* the Windows loader's own limit */
#define PE_MAX_IMPORTS  4096u  /* sanity cap for descriptor/name walks */

static uint32_t round_up(uint32_t v, uint32_t a) { return (v + a - 1) & ~(a - 1); }

/* [rva, rva+len) must lie entirely inside the mapped image. Use before hle_ptr. */
static int rva_in_image(const pe_image *img, uint32_t rva, uint32_t len) {
    return rva < img->span && len <= img->span - rva;
}

/* A NUL-terminated string at rva must terminate before the image ends. */
static int cstr_in_image(const pe_image *img, uint32_t rva) {
    if (rva >= img->span) return 0;
    const unsigned char *p = (const unsigned char *)hle_ptr(img->base + rva);
    uint32_t left = img->span - rva;
    for (uint32_t i = 0; i < left; i++) if (!p[i]) return 1;
    return 0;
}

/* Read a whole file into a malloc'd buffer; *len gets the size. NULL on error. */
static uint8_t *slurp(const char *path, size_t *len) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return NULL;
    struct stat st;
    if (fstat(fd, &st) < 0) { close(fd); return NULL; }
    if (st.st_size < 0) { close(fd); return NULL; }
    uint8_t *buf = (uint8_t *)malloc((size_t)st.st_size);
    ssize_t n = buf ? read(fd, buf, (size_t)st.st_size) : -1;
    close(fd);
    if (n != (ssize_t)st.st_size) { free(buf); return NULL; }
    *len = (size_t)st.st_size;
    return buf;
}

void recomp_pe_relocate(uint32_t base, uint32_t reloc_rva, uint32_t reloc_size, int32_t delta) {
    if (!reloc_rva || !reloc_size || !delta) return;
    uint32_t off = 0;
    while (off < reloc_size) {
        /* A truncated block header or a block that runs past the directory is
         * not a relocation; stop instead of reading into the next section. */
        if (reloc_size - off < sizeof(pe_base_reloc)) break;
        pe_base_reloc *blk = (pe_base_reloc *)hle_ptr(base + reloc_rva + off);
        if (blk->SizeOfBlock < sizeof *blk || blk->SizeOfBlock > reloc_size - off) break;
        uint32_t nent = (blk->SizeOfBlock - (uint32_t)sizeof *blk) / 2;
        uint16_t *ent = (uint16_t *)(blk + 1);
        for (uint32_t i = 0; i < nent; i++) {
            uint32_t type = ent[i] >> 12, where = ent[i] & 0x0FFF;
            if (type == PE_REL_HIGHLOW)
                MEM32(base + blk->VirtualAddress + where) += (uint32_t)delta;
            /* type 0 (ABSOLUTE) is padding; others are unused on x86 PE32 */
        }
        off += blk->SizeOfBlock;
    }
}

static int pe_map_buffer(const char *label, const uint8_t *file, size_t flen, pe_image *img, int anywhere);

static int pe_map_at(const char *path, pe_image *img, int anywhere) {
    size_t flen;
    uint8_t *file = slurp(path, &flen);
    if (!file) { fprintf(stderr, "[pe] cannot read %s\n", path); return 1; }
    int r = pe_map_buffer(path, file, flen, img, anywhere);
    free(file);
    return r;
}
int recomp_pe_map(const char *path, pe_image *img)     { return pe_map_at(path, img, 0); }
int recomp_pe_map_any(const char *path, pe_image *img) { return pe_map_at(path, img, 1); }
/* Map an image that is already in memory -- a rebuilt binary's embedded copy of
 * the original PE -- so the user's original executable is not needed at run
 * time. The buffer must outlive the map call; sections are copied into guest
 * memory here, so it need not outlive that. */
int recomp_pe_map_mem(const uint8_t *data, size_t size, pe_image *img) {
    return pe_map_buffer("<embedded image>", data, size, img, 0);
}

static int pe_map_buffer(const char *label, const uint8_t *file, size_t flen, pe_image *img, int anywhere) {
    /* ---------------------------------------------------------------- headers
     * Everything in this block runs before hle_vm_reserve() and before any
     * memcpy, so a rejected file leaves no state behind. 64-bit arithmetic is
     * used for file offsets to make overflow impossible. */
    if (flen < sizeof(pe_dos_header)) {
        fprintf(stderr, "[pe] %s: file is shorter than a DOS header\n", label); return 1;
    }
    pe_dos_header *dos = (pe_dos_header *)file;
    if (dos->e_magic != 0x5A4D) { fprintf(stderr, "[pe] %s: not MZ\n", label); return 1; }
    uint64_t pe_off = dos->e_lfanew;
    uint64_t nt_end = pe_off + sizeof(pe_nt_headers32);
    if (pe_off < 0x40 || nt_end > flen) {
        fprintf(stderr, "[pe] %s: bad e_lfanew 0x%08X (file %zu bytes)\n",
                label, dos->e_lfanew, flen);
        return 1;
    }
    pe_nt_headers32 *nt = (pe_nt_headers32 *)(file + pe_off);
    if (nt->Signature != 0x00004550 || nt->OptionalHeader.Magic != 0x10B) {
        fprintf(stderr, "[pe] %s: not a PE32\n", label); return 1;
    }
    pe_opt_header32 *opt = &nt->OptionalHeader;

    uint16_t nsec = nt->FileHeader.NumberOfSections;
    uint16_t size_opt = nt->FileHeader.SizeOfOptionalHeader;
    if (size_opt < PE32_OPT_SIZE) {
        fprintf(stderr, "[pe] %s: optional header is %u bytes, need >= %u\n",
                label, size_opt, PE32_OPT_SIZE); return 1;
    }
    if (!nsec || nsec > PE_MAX_SECTIONS) {
        fprintf(stderr, "[pe] %s: invalid section count %u\n", label, nsec); return 1;
    }
    uint64_t sec_off = pe_off + 24u + size_opt;          /* sig(4) + file header(20) */
    uint64_t sec_end = sec_off + (uint64_t)nsec * sizeof(pe_section_header);
    if (sec_end > flen || sec_end > UINT64_C(0xFFFFFFFF)) {
        fprintf(stderr, "[pe] %s: section table extends past the file\n", label); return 1;
    }

    if (opt->SizeOfImage < PAGE || opt->SizeOfImage > UINT32_MAX - (PAGE - 1u)) {
        fprintf(stderr, "[pe] %s: invalid SizeOfImage 0x%08X\n", label, opt->SizeOfImage);
        return 1;
    }
    uint32_t span = round_up(opt->SizeOfImage, PAGE);
    uint32_t size_headers = opt->SizeOfHeaders;
    if (!size_headers || size_headers > flen || size_headers > span) {
        fprintf(stderr, "[pe] %s: invalid SizeOfHeaders 0x%08X\n", label, size_headers);
        return 1;
    }

    pe_section_header *sec = (pe_section_header *)(file + sec_off);
    for (unsigned i = 0; i < nsec; i++) {
        const pe_section_header *s = &sec[i];
        if (s->VirtualAddress > span) {
            fprintf(stderr, "[pe] %s: section %u RVA 0x%08X is past the image\n",
                    label, i, s->VirtualAddress); return 1;
        }
        uint32_t vsize = s->VirtualSize ? s->VirtualSize : s->SizeOfRawData;
        if (vsize > span - s->VirtualAddress) {
            fprintf(stderr, "[pe] %s: section %u extends past the image\n", label, i);
            return 1;
        }
        if (s->SizeOfRawData && s->PointerToRawData > flen) {
            fprintf(stderr, "[pe] %s: section %u raw data starts past the file\n", label, i);
            return 1;
        }
    }
    if (opt->AddressOfEntryPoint >= span) {
        fprintf(stderr, "[pe] %s: entry point RVA 0x%08X is outside the image\n",
                label, opt->AddressOfEntryPoint); return 1;
    }

    /* Data directories beyond NumberOfRvaAndSizes do not exist. Reading them
     * anyway would hand the loader attacker-controlled sizes from padding. */
    uint32_t ndirs = opt->NumberOfRvaAndSizes;
    if (ndirs > 16) ndirs = 16;
    const pe_data_dir *dd = opt->DataDirectory;
    pe_data_dir reloc_dir = ndirs > PE_DIR_BASERELOC ? dd[PE_DIR_BASERELOC] : (pe_data_dir){ 0, 0 };
    pe_data_dir import_dir = ndirs > PE_DIR_IMPORT ? dd[PE_DIR_IMPORT] : (pe_data_dir){ 0, 0 };
    pe_data_dir res_dir   = ndirs > PE_DIR_RESOURCE ? dd[PE_DIR_RESOURCE] : (pe_data_dir){ 0, 0 };

    /* ------------------------------------------------------------------ map */
    uint32_t image_base = opt->ImageBase;
    /* Reserve the image's VA range, fixed, so every MEM32(va) in lifted code
     * lands. A range already in use (on a flat host, its heap or a library)
     * fails the map instead of being silently replaced under it. A DLL
     * (anywhere) goes wherever there is room and is relocated. */
    uint32_t base = hle_vm_reserve(anywhere ? 0 : image_base, span);
    if (!base || (!anywhere && base != image_base)) {
        if (base) hle_vm_release(base, span);
        fprintf(stderr, "[pe] map @ 0x%08X failed (the range is in use)\n", image_base); return 1;
    }

    /* headers, then each section's raw data (the rest of VirtualSize is the
     * zero MAP_ANONYMOUS already gave us, i.e. .bss). Raw data past
     * VirtualSize is copied too: the Windows loader maps the file's raw
     * extent, and titles keep strings there (Cave Story's window class name
     * sits in .rdata slack). */
    memcpy(hle_ptr(base), file, size_headers);
    for (unsigned i = 0; i < nsec; i++) {
        uint32_t copy = sec[i].SizeOfRawData;
        uint32_t room = span - sec[i].VirtualAddress;      /* VA validated above */
        if (i + 1 < nsec && sec[i + 1].VirtualAddress > sec[i].VirtualAddress) {
            uint32_t next = sec[i + 1].VirtualAddress - sec[i].VirtualAddress;
            if (next < room) room = next;
        }
        if (copy > room) copy = room;
        if (copy > flen - sec[i].PointerToRawData)         /* PTR validated above */
            copy = (uint32_t)(flen - sec[i].PointerToRawData);
        if (copy)
            memcpy(hle_ptr(base + sec[i].VirtualAddress), file + sec[i].PointerToRawData, copy);
    }

    if (base != image_base) {
        if (!reloc_dir.VirtualAddress || !reloc_dir.Size)
            fprintf(stderr, "[pe] %s: rebased to 0x%08X but has no base relocations; "
                            "absolute addresses will be wrong\n", label, base);
        recomp_pe_relocate(base, reloc_dir.VirtualAddress, reloc_dir.Size,
                           (int32_t)(base - image_base));
    }

    img->base = base;
    img->image_base = image_base;
    img->span = span;
    img->entry = base + opt->AddressOfEntryPoint;
    img->import_rva = import_dir.VirtualAddress;
    img->import_size = import_dir.VirtualAddress && rva_in_image(img, import_dir.VirtualAddress,
                                                                 import_dir.Size)
                       ? import_dir.Size : 0;
    img->resource_rva = res_dir.VirtualAddress && rva_in_image(img, res_dir.VirtualAddress,
                                                               res_dir.Size)
                        ? res_dir.VirtualAddress : 0;
    img->resource_size = img->resource_rva ? res_dir.Size : 0;
    img->stamp = nt->FileHeader.TimeDateStamp;
    fprintf(stderr, "[pe] %s mapped at 0x%08X, span 0x%X\n", label, base, span);
    return 0;
}

int recomp_pe_bind(const pe_image *img, uint32_t (*resolve)(const char *name)) {
    return recomp_pe_bind_companions(img, resolve, NULL);
}

/* The companion-aware bind: for each descriptor, `comp_resolve(dll, name)` is
 * asked first, so an import from a bundled DLL binds to that DLL's lifted
 * export instead of a shim of the same name (or the unresolved stub). Only
 * when it answers 0 does the ordinary resolver run. */
int recomp_pe_bind_companions(const pe_image *img,
                              uint32_t (*resolve)(const char *name),
                              uint32_t (*comp_resolve)(const char *dll, const char *name)) {
    if (!img->import_rva) return 0;
    uint32_t base = img->base;

    /* Bound the walk by the directory size. Some linkers leave Size at 0 even
     * though RVA is set; then the rest of the image is the limit. A missing
     * terminator can therefore never walk off the end of guest memory. */
    uint32_t limit = img->span > img->import_rva ? img->span - img->import_rva : 0;
    if (img->import_size && img->import_size < limit) limit = img->import_size;
    if (limit < sizeof(pe_import_descriptor)) {
        fprintf(stderr, "[pe] import directory is too small to hold a descriptor\n");
        return 1;
    }
    uint8_t *start = hle_ptr(base + img->import_rva);
    uint8_t *end = start + limit;
    pe_import_descriptor *d = (pe_import_descriptor *)start;

    int unresolved = 0, bound = 0;
    for (unsigned nd = 0; (uint8_t *)d + sizeof(*d) <= end && d->Name; d++, nd++) {
        if (nd >= PE_MAX_IMPORTS) {
            fprintf(stderr, "[pe] import descriptor limit reached; table looks unterminated\n");
            break;
        }
        if (!cstr_in_image(img, d->Name)) {
            fprintf(stderr, "[pe] import DLL name RVA 0x%08X is outside the image\n", d->Name);
            unresolved++; break;
        }
        const char *dll = (const char *)hle_ptr(base + d->Name);
        uint32_t ilt_rva = d->OriginalFirstThunk ? d->OriginalFirstThunk : d->FirstThunk;
        if (!rva_in_image(img, ilt_rva, 4) || !rva_in_image(img, d->FirstThunk, 4)) {
            fprintf(stderr, "[pe] import thunk RVA out of image for %s\n", dll);
            unresolved++; continue;
        }
        uint32_t irva = ilt_rva, iarva = d->FirstThunk;
        for (unsigned ne = 0; rva_in_image(img, irva, 4) && rva_in_image(img, iarva, 4); ) {
            uint32_t thunk = MEM32(base + irva);
            if (!thunk) break;                            /* ILT terminator */
            if (++ne >= PE_MAX_IMPORTS) {
                fprintf(stderr, "[pe] import name limit reached for %s; table looks unterminated\n", dll);
                break;
            }
            uint32_t va = 0;
            if (thunk & PE_ORDINAL_FLAG) {
                /* by ordinal: win32hle keys shims by name, so the resolver is
                 * asked for "dll#N" (the DLL's name in lower case), which a
                 * module registers as an alias (dsound.dll#1 is
                 * DirectSoundCreate). The name outlives the bind: a permissive
                 * resolver keeps it for its stub. */
                char nm[96];
                size_t k = 0;
                for (; dll[k] && k < 80; k++)
                    nm[k] = (char)(dll[k] >= 'A' && dll[k] <= 'Z' ? dll[k] + 32 : dll[k]);
                snprintf(nm + k, sizeof nm - k, "#%u", thunk & 0xFFFFu);
                char *name = strdup(nm);
                va = comp_resolve ? comp_resolve(dll, name) : 0;
                if (!va) va = resolve(name);
                if (!va) fprintf(stderr, "[pe] unresolved import %s\n", name);
            } else {
                if (!rva_in_image(img, thunk, 2) || !cstr_in_image(img, thunk + 2)) {
                    fprintf(stderr, "[pe] import name RVA 0x%08X is outside the image\n", thunk);
                    unresolved++; break;
                }
                const char *name = (const char *)hle_ptr(base + thunk + 2); /* skip hint word */
                va = comp_resolve ? comp_resolve(dll, name) : 0;
                if (!va) va = resolve(name);
                if (!va) fprintf(stderr, "[pe] unresolved import %s!%s\n", dll, name);
            }
            if (va) bound++; else unresolved++;
            MEM32(base + iarva) = va;
            irva += 4; iarva += 4;
        }
    }
    fprintf(stderr, "[pe] bound %d imports, %d unresolved\n", bound, unresolved);
    return unresolved;
}
