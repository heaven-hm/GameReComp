/* RETRACTED MODULE, reconstructed exactly as the generator emitted it before the
 * host-VA rule. Kept as evidence: the oracle certifies it, and the project refuses
 * it. `var_0` is a host uint8_t used as a guest address. */
#include <stdint.h>
#include "win32hle.h"
/* address: 0x004258B0   symbol: store_block_0049e654   [named_structured] */
uint32_t function_004258B0(uint8_t argument_0) {
    uint8_t var_0 = argument_0;
    return MEM32((((MEM8((var_0 & 0xFFFFFFFFULL)) << 2ULL) + 0x004A5B48U) & 0xFFFFFFFFULL));
}
void sub_004258B0(void) {
    uint32_t argument_0 = MEM32(g_esp + 4u);
    uint32_t result = function_004258B0(argument_0);
    RETC(result);
}
