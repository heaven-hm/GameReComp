/* Native macOS ARM64 harness for one real-NE lifted function.
 *
 * Sets up an x86-16 CPU struct exactly as Unicorn is set up in
 * test_native_smoke.py: all segment registers zero (so an NE/Win16 flat
 * address equals the offset), deterministic memory, fixed GPRs/flags. Calls
 * the lifted function and prints every architectural field the reference
 * compares. No emulator, no Wine, no 16-bit host: this is a normal arm64
 * Mach-O built by clang from the lifted C and the recomp16 runtime header.
 */
#include "win16_adapter.h"

#include <stdio.h>
#include <string.h>

extern void seg002_2F2E(CPU *cpu);

static unsigned char g_mem[MEM_SIZE];

int main(void)
{
    CPU cpu;
    uint32_t crc = 0;
    unsigned i;

    memset(&cpu, 0, sizeof cpu);
    memset(g_mem, 0, sizeof g_mem);
    for (i = 0; i < MEM_SIZE; i++)
        g_mem[i] = (unsigned char)((i * 7 + 13) & 0xFF);
    /* Mirror every byte the Unicorn reference writes outside the function's
     * own stores, so the whole-memory CRC is directly comparable: the 25 x86
     * bytes at 0x30000 (CS=0x3000, IP=0), NOP padding and an HLT at +0x20,
     * and a near-return address on the stack pointing at that HLT. */
    static const unsigned char code[] = {
        0xa3, 0x58, 0xf5, 0x89, 0x16, 0x62, 0xf5, 0x88, 0x0e, 0x57, 0xf5,
        0xc6, 0x06, 0x56, 0xf5, 0x01, 0x33, 0xc0, 0xa3, 0x5c, 0xf5, 0x89,
        0x16, 0x5e, 0xf5, 0xc3
    };
    memcpy(g_mem + 0x30000, code, sizeof code);
    memset(g_mem + 0x30000 + sizeof code, 0x90, 0x20 - sizeof code);
    g_mem[0x30020] = 0xF4;
    g_mem[0xFF00] = 0x20;
    g_mem[0xFF01] = 0x00;

    cpu.mem = g_mem;
    cpu.ds = cpu.es = cpu.ss = 0;   /* flat: SEG_OFF(0, off) == off */
    cpu.ax = 0x1234;
    cpu.bx = 0x2222;
    cpu.cx = 0x00EF;                /* CL = 0xEF */
    cpu.dx = 0xABCD;
    cpu.sp = 0xFF00;
    cpu.bp = 0x3333;
    cpu.si = 0x4444;
    cpu.di = 0x5555;
    cpu.flags = 0x0202;             /* reserved bit 1 + IF */

    seg002_2F2E(&cpu);

    for (i = 0; i < MEM_SIZE; i += 4) {
        uint32_t w;
        memcpy(&w, g_mem + i, 4);   /* alignment-safe load */
        crc = crc * 16777619u ^ w;  /* 32-bit FNV-1a, as in difftest16.py */
    }

    printf("ax=%04X bx=%04X cx=%04X dx=%04X sp=%04X bp=%04X si=%04X di=%04X "
           "flags=%04X\n",
           cpu.ax, cpu.bx, cpu.cx, cpu.dx, cpu.sp, cpu.bp, cpu.si, cpu.di,
           cpu.flags);
    printf("F552=%04X F556=%02X F557=%02X F558=%04X F55C=%04X F55E=%04X "
           "crc=%08X\n",
           mem_read16(&cpu, cpu.ds, 0xF552), mem_read8(&cpu, cpu.ds, 0xF556),
           mem_read8(&cpu, cpu.ds, 0xF557), mem_read16(&cpu, cpu.ds, 0xF558),
           mem_read16(&cpu, cpu.ds, 0xF55C), mem_read16(&cpu, cpu.ds, 0xF55E),
           crc);
    return 0;
}
