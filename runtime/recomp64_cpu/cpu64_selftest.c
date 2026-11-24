/*
 * cpu64_selftest.c - a tiny runner for one real program: arith-calc.exe.
 *
 * This is NOT a general Windows emulation layer. It implements exactly the
 * three KERNEL32 imports the real arith-calc.exe PE asks for (verified from its
 * .idata through generate64.py's recomp_imports[]), so that the generated C can
 * be linked and executed natively on Apple Silicon. Nothing here is derived
 * from an invented "hello world" binary: the PE comes from
 * ~/Downloads/recomp-apps/windows/x64/apps/arith-calc.exe.
 *
 * Usage: arith64 <path-to-arith-calc.exe>
 */
#include "cpu64.h"
#include "recomp_dispatch.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

int hle_invoke(CPU *c, uint32_t idx);
int recomp64_load_image(const char *path, CPU *c, uint64_t *entry_out);

/* Win64 integer args arrive in rcx, rdx, r8, r9 regardless of which guest
 * register the generated bodies used to stage them. */
#define ARG0(c) ((c)->rcx)
#define ARG1(c) ((c)->rdx)
#define ARG2(c) ((c)->r8)
#define ARG3(c) ((c)->r9)

int hle_invoke(CPU *c, uint32_t idx) {
    if (idx >= (uint32_t)HLE_COUNT)
        return 0;
    const hle_import_t *im = &recomp_imports[idx];

    if (!strcmp(im->name, "GetStdHandle")) {
        /* STD_INPUT_HANDLE = -10, STD_OUTPUT_HANDLE = -11,
         * STD_ERROR_HANDLE = -12. A pseudo-handle is the value itself. */
        SET64(c->rax, ARG0(c));
        return 1;
    }
    if (!strcmp(im->name, "WriteFile")) {
        uint64_t buf = ARG1(c);
        uint32_t n   = (uint32_t)ARG2(c);
        uint64_t written_ptr = ARG3(c);
        if (n) {
            if (fwrite((const void *)(uintptr_t)buf, 1, n, stdout) != n) {
                /* WriteFile returns FALSE on failure. */
                SET32(c->rax, 0);
                return 1;
            }
        }
        fflush(stdout);
        if (written_ptr)
            wr32(written_ptr, n);
        SET32(c->rax, 1);            /* TRUE */
        return 1;
    }
    if (!strcmp(im->name, "ExitProcess")) {
        fflush(stdout);
        exit((int)(uint32_t)ARG0(c));
    }
    return 0;   /* let the weak default in cpu64.c report it */
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <arith-calc.exe>\n", argv[0]);
        return 2;
    }
    static CPU cpu;
    uint64_t entry = 0;
    if (recomp64_load_image(argv[1], &cpu, &entry) != 0)
        return 3;
    fprintf(stderr, "[cpu64] image loaded: entry %#llx, delta %lld, stack %#llx\n",
            (unsigned long long)entry, (long long)g_image_delta,
            (unsigned long long)cpu.rsp);
    /* dispatch to AddressOfEntryPoint. The loader left a fake return address
     * on the guest stack; when the entry function finally `ret`s, we return
     * here and finish normally. */
    dispatch(&cpu, entry);
    fprintf(stderr, "[cpu64] guest entry returned (rip %#llx)\n",
            (unsigned long long)cpu.rip);
    return 0;
}
