/* rev.ng's independent reading of 0x0040BA58, the
 * address msvc_rand_next replaces. Not a proof and not compared
 * automatically: it is the other provider's opinion, recorded so
 * the two can be read side by side. */
_ABI(Microsoft_x86_cdecl)
generic32_t function_0x40ba58_Code_x86(void) {
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 4944ULL) = *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 4944ULL) * 214013U + 2531011U;
    return *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 4944ULL) >> 16U & 0x7FFFU;
}
