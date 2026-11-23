/*
 * pe_loader.c - see pe_loader.h. Maps a 32-bit PE into guest memory and binds its IAT
 * to resolver-provided addresses. No windows.h: that is the point.
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
static uint32_t round_up(uint32_t v, uint32_t a) { return (v + a - 1) & ~(a - 1); }

/* Read a whole file into a malloc'd buffer; *len gets the size. NULL on error. */
static uint8_t *slurp(const char *path, size_t *len) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return NULL;
    struct stat st;
    if (fstat(fd, &st) < 0) { close(fd); return NULL; }
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
        pe_base_reloc *blk = (pe_base_reloc *)hle_ptr(base + reloc_rva + off);
        if (blk->SizeOfBlock < sizeof *blk) break;
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
    pe_dos_header *dos = (pe_dos_header *)file;
    if (flen < sizeof *dos || dos->e_magic != 0x5A4D) { fprintf(stderr, "[pe] not MZ\n"); return 1; }
    if ((size_t)dos->e_lfanew + sizeof(pe_nt_headers32) > flen) { fprintf(stderr, "[pe] bad e_lfanew\n"); return 1; }
    pe_nt_headers32 *nt = (pe_nt_headers32 *)(file + dos->e_lfanew);
    if (nt->Signature != 0x00004550 || nt->OptionalHeader.Magic != 0x10B) {
        fprintf(stderr, "[pe] not a PE32\n"); return 1;
    }
    pe_opt_header32 *opt = &nt->OptionalHeader;
    uint32_t image_base = opt->ImageBase;
    uint32_t span = round_up(opt->SizeOfImage, PAGE);

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
    memcpy(hle_ptr(base), file, opt->SizeOfHeaders);
    pe_section_header *sec = (pe_section_header *)((uint8_t *)&nt->OptionalHeader + nt->FileHeader.SizeOfOptionalHeader);
    for (int i = 0; i < nt->FileHeader.NumberOfSections; i++) {
        uint32_t copy = sec[i].SizeOfRawData;
        uint32_t room = span - sec[i].VirtualAddress;
        if (i + 1 < nt->FileHeader.NumberOfSections) {
            uint32_t next = sec[i + 1].VirtualAddress - sec[i].VirtualAddress;
            if (next < room) room = next;
        }
        if (copy > room) copy = room;
        if ((size_t)sec[i].PointerToRawData + copy > flen)
            copy = (uint32_t)(flen - sec[i].PointerToRawData);
        if (copy && (size_t)sec[i].PointerToRawData <= flen)
            memcpy(hle_ptr(base + sec[i].VirtualAddress), file + sec[i].PointerToRawData, copy);
    }

    if (base != image_base) {
        pe_data_dir r = opt->DataDirectory[PE_DIR_BASERELOC];
        recomp_pe_relocate(base, r.VirtualAddress, r.Size, (int32_t)(base - image_base));
    }

    img->base = base;
    img->image_base = image_base;
    img->span = span;
    img->entry = base + opt->AddressOfEntryPoint;
    img->import_rva = opt->DataDirectory[PE_DIR_IMPORT].VirtualAddress;
    img->delay_import_rva = opt->NumberOfRvaAndSizes > PE_DIR_DELAY_IMPORT
                            ? opt->DataDirectory[PE_DIR_DELAY_IMPORT].VirtualAddress : 0;
    img->resource_rva = opt->DataDirectory[PE_DIR_RESOURCE].VirtualAddress;
    img->resource_size = opt->DataDirectory[PE_DIR_RESOURCE].Size;
    img->stamp = nt->FileHeader.TimeDateStamp;
    fprintf(stderr, "[pe] %s mapped at 0x%08X, span 0x%X\n", label, base, span);
    return 0;
}

int recomp_pe_bind(const pe_image *img, uint32_t (*resolve)(const char *name)) {
    return recomp_pe_bind_companions(img, resolve, NULL);
}

/* Resolve one thunk through the same chain for ordinary and delay imports.
 * `name_va` is the address of a hint/name pair; by-ordinal thunks carry the
 * ordinal in the low 16 bits. A bundled DLL's lifted export is preferred over
 * a shim of the same name; only when it does not answer does the shim run. */
static uint32_t bind_thunk(const char *dll, uint32_t name_va,
                           uint32_t (*resolve)(const char *name),
                           uint32_t (*comp_resolve)(const char *dll, const char *name)) {
    if (name_va & PE_ORDINAL_FLAG) {
        /* win32hle keys shims by name, so the resolver is asked for "dll#N"
         * (lower-cased DLL name), which a module registers as an alias
         * (dsound.dll#1 is DirectSoundCreate). The name outlives the bind:
         * a permissive resolver keeps it for its stub. */
        char nm[96];
        size_t k = 0;
        for (; dll[k] && k < 80; k++) nm[k] = (char)(dll[k] >= 'A' && dll[k] <= 'Z' ? dll[k] + 32 : dll[k]);
        snprintf(nm + k, sizeof nm - k, "#%u", name_va & 0xFFFFu);
        char *name = strdup(nm);
        uint32_t va = comp_resolve ? comp_resolve(dll, name) : 0;
        if (!va) va = resolve(name);
        if (!va) fprintf(stderr, "[pe] unresolved import %s\n", name);
        return va;
    }
    const char *name = (const char *)hle_ptr(name_va + 2); /* skip hint word */
    uint32_t va = comp_resolve ? comp_resolve(dll, name) : 0;
    if (!va) va = resolve(name);
    if (!va) fprintf(stderr, "[pe] unresolved import %s!%s\n", dll, name);
    return va;
}

/* Delay imports (data directory 13): the IAT slots hold the address of a
 * __delayLoadHelper thunk until first use in the original image. Static
 * recompilation has no such helper and no lazy-load step, so bind these slots
 * eagerly at boot through the very same resolver chain as ordinary imports.
 * Without this, a recompiled call through a delay slot dispatches to guest
 * code that was never lifted (or, permissively, to a silent no-op). */
static int bind_delay_imports(const pe_image *img,
                              uint32_t (*resolve)(const char *name),
                              uint32_t (*comp_resolve)(const char *dll, const char *name),
                              int *bound, int *unresolved) {
    if (!img->delay_import_rva) return 0;
    uint32_t base = img->base, span = img->span, image_base = img->image_base;
    uint32_t dva = base + img->delay_import_rva;
    const uint32_t limit = base + span;
    int slots = 0;
    while (dva + sizeof(pe_delay_import_descriptor) <= limit) {
        const pe_delay_import_descriptor *d = (const pe_delay_import_descriptor *)hle_ptr(dva);
        if (!(d->szName || d->pIAT || d->pINT)) break;   /* terminator */
        int rva_based = (d->grAttrs & PE_DELAY_ATTR_RVA) != 0;
        uint32_t name_rva = d->szName, iat_rva = d->pIAT, int_rva = d->pINT;
        if (!rva_based) {                               /* old linkers: VAs, not RVAs */
            if (name_rva >= image_base) name_rva -= image_base;
            if (iat_rva >= image_base) iat_rva -= image_base;
            if (int_rva >= image_base) int_rva -= image_base;
        }
        if (!name_rva || name_rva >= span || !iat_rva || iat_rva >= span) break;
        const char *dll = (const char *)hle_ptr(base + name_rva);
        uint32_t lookup = int_rva ? int_rva : iat_rva;  /* some omit the INT */
        if (lookup >= span) break;
        uint32_t ilt_va = base + lookup, iat_va = base + iat_rva;
        while (ilt_va + 4 <= limit && iat_va + 4 <= limit) {
            uint32_t thunk = MEM32(ilt_va);
            if (!thunk) break;
            int by_ordinal = (thunk & PE_ORDINAL_FLAG) != 0;
            uint32_t slot = thunk;
            if (!rva_based && !by_ordinal && slot >= image_base) slot -= image_base;
            if (!by_ordinal && slot >= span) break;
            uint32_t va = bind_thunk(dll, by_ordinal ? thunk : base + slot,
                                     resolve, comp_resolve);
            MEM32(iat_va) = va;
            if (va) (*bound)++; else (*unresolved)++;
            slots++;
            ilt_va += 4, iat_va += 4;
        }
        dva += sizeof *d;
    }
    return slots;
}

/* The companion-aware bind: each descriptor's `comp_resolve(dll, name)` is
 * asked first, so an import from a bundled DLL binds to that DLL's lifted
 * export instead of a shim of the same name (or the unresolved stub). Both the
 * ordinary and the delay-import directory go through this one chain. */
int recomp_pe_bind_companions(const pe_image *img,
                             uint32_t (*resolve)(const char *name),
                             uint32_t (*comp_resolve)(const char *dll, const char *name)) {
    uint32_t base = img->base;
    int unresolved = 0, bound = 0;
    if (img->import_rva) {
        const uint32_t limit = base + img->span;
        uint32_t dva = base + img->import_rva;
        while (dva + sizeof(pe_import_descriptor) <= limit) {
            const pe_import_descriptor *d = (const pe_import_descriptor *)hle_ptr(dva);
            if (!d->Name) break;
            uint32_t name_off = d->Name < img->span ? d->Name : 0;
            uint32_t ilt_off = d->OriginalFirstThunk ? d->OriginalFirstThunk : d->FirstThunk;
            if (!name_off || ilt_off >= img->span || d->FirstThunk >= img->span) break;
            const char *dll = (const char *)hle_ptr(base + name_off);
            uint32_t ilt_va = base + ilt_off, iat_va = base + d->FirstThunk;
            while (ilt_va + 4 <= limit && iat_va + 4 <= limit) {
                uint32_t thunk = MEM32(ilt_va);
                if (!thunk) break;
                int by_ordinal = (thunk & PE_ORDINAL_FLAG) != 0;
                if (!by_ordinal && thunk >= img->span) break;
                uint32_t va = bind_thunk(dll, by_ordinal ? thunk : base + thunk,
                                         resolve, comp_resolve);
                MEM32(iat_va) = va;
                if (va) bound++; else unresolved++;
                ilt_va += 4, iat_va += 4;
            }
            dva += sizeof *d;
        }
    }
    int delay = bind_delay_imports(img, resolve, comp_resolve, &bound, &unresolved);
    fprintf(stderr, "[pe] bound %d imports (%d delay), %d unresolved\n", bound, delay, unresolved);
    return unresolved;
}
