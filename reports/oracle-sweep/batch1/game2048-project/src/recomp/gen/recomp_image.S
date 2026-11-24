/* Auto-generated: the original PE, embedded so the rebuilt binary runs without
 * it. .incbin reads the copy beside this file, by absolute path (the assembler
 * runs from the build directory). The symbol carries the platform's C prefix. */
#if defined(__APPLE__)
#define SYM(x) _##x
    .section __DATA,__const
#else
#define SYM(x) x
    .section .rodata
#endif
    .p2align 4
    .globl SYM(recomp_original_image)
SYM(recomp_original_image):
    .incbin "/Users/haseeb-mir/.local/share/opencode/worktree/1b47e2/issue-work/reports/oracle-sweep/batch1/game2048-project/src/recomp/gen/recomp_image.bin"
