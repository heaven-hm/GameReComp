/* Lifted from a REAL Win16 NE executable.
 *
 *   source: SHIPEDIT.EXE (OpenTyrian 2.0 data set, shareware)
 *   SHA-256: 9be5f168ee0e0743c0e9a541b8620820b6d53b6d8458fdc8d3c50727ee9c3181
 *   NE header at 0x0E3E, module name "SHIPEDIT", linker 6.1, target_os=1
 *   (Windows), flags 0x030A, imports KERNEL/KERNEL/RTM
 *   NE segment 2, function at offset 0x2F2E, 25 bytes:
 *     a3 58 f5 89 16 62 f5 88 0e 57 f5 c6 06 56 f5 01 33 c0 a3 5c f5
 *     89 16 5e f5 c3
 *
 *   generated with: python tools/lift/ne_lift.py SHIPEDIT.EXE --seg 2
 *   (function seg002_2F2E; the body below is verbatim except that the
 *   ne_lift-emitted `#include "segments.h"` is replaced with the small
 *   adapter header, so this one leaf can build without the whole-module tree)
 *
 * This is a leaf: it does no calls and no imports. It stores AX/DX/CL to
 * absolute DGROUP offsets, writes a flag byte, zeroes AX (xor), stores AX/DX
 * again, and returns. Golden-tested against the original 25 bytes under
 * Unicorn x86-16 by test_native_smoke.py.
 */
#include "win16_adapter.h"

void seg002_2F2E(CPU *cpu)
{
    TRACE_FN("seg002_2F2E");
    RECOMP_ENTER("seg002_2F2E");
    mem_write16(cpu, cpu->ds, 0xF558, (uint16_t)(cpu->ax)); /* mov word ds:[0xF558], ax */
    mem_write16(cpu, cpu->ds, 0xF562, (uint16_t)(cpu->dx)); /* mov word ds:[0xF562], dx */
    mem_write8(cpu, cpu->ds, 0xF557, (uint8_t)(cpu->cl)); /* mov byte ds:[0xF557], cl */
    mem_write8(cpu, cpu->ds, 0xF556, (uint8_t)(0x1)); /* mov byte ds:[0xF556], 0x1 */
    { uint16_t _r = cpu->ax ^ cpu->ax; flags_logic16(cpu, _r); cpu->ax = (uint16_t)(_r); } /* xor ax, ax */
    mem_write16(cpu, cpu->ds, 0xF55C, (uint16_t)(cpu->ax)); /* mov word ds:[0xF55C], ax */
    mem_write16(cpu, cpu->ds, 0xF55E, (uint16_t)(cpu->dx)); /* mov word ds:[0xF55E], dx */
    cpu->sp += 2; return;                    /* ret */
}
