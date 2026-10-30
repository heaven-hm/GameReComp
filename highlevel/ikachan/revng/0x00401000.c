/* rev.ng's independent reading of 0x00401000, the
 * address reset_global_configuration replaces. Not a proof and not compared
 * automatically: it is the other provider's opinion, recorded so
 * the two can be read side by side. */
_ABI(Microsoft_x86_cdecl)
void function_0x401000_Code_x86(void) {
    *(generic8_t *) (generic32_t) ((generic32_t) &segment_2 + 7136ULL) = 1;
    *(generic16_t *) (generic32_t) ((generic32_t) &segment_2 + 7138ULL) = 15;
    *(generic16_t *) (generic32_t) ((generic32_t) &segment_2 + 7140ULL) = 0;
    *(generic8_t *) (generic32_t) ((generic32_t) &segment_2 + 7142ULL) = 0;
    *(generic8_t *) (generic32_t) ((generic32_t) &segment_2 + 7143ULL) = 0;
    *(generic16_t *) (generic32_t) ((generic32_t) &segment_2 + 7144ULL) = 1000;
    *(generic16_t *) (generic32_t) ((generic32_t) &segment_2 + 7146ULL) = 1001;
    *(generic16_t *) (generic32_t) ((generic32_t) &segment_2 + 7148ULL) = 0;
    *(generic8_t *) (generic32_t) ((generic32_t) &segment_2 + 0x1BEEULL) = 0;
    *(generic8_t *) (generic32_t) ((generic32_t) &segment_2 + 7151ULL) = 0;
    *(generic16_t *) (generic32_t) ((generic32_t) &segment_2 + 7152ULL) = 0;
    *(generic8_t *) (generic32_t) ((generic32_t) &segment_2 + 7154ULL) = 0;
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 7160ULL) = 0x20C000U;
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 7164ULL) = 0x168000U;
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 0x1C00ULL) = 0U;
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 7172ULL) = 0U;
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 7176ULL) = 0x218000U;
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 0x1C0CULL) = 1490944U;
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 0x1C10ULL) = 0x4000U;
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 7188ULL) = 0x800U;
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 0x1C18ULL) = 0xC000U;
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 0x1C1CULL) = 22528U;
}
