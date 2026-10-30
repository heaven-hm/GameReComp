/*
 * High-level replacements for two Ikachan functions.
 *
 * These are the readable form of what the mechanical bodies at the same
 * addresses do. They are compiled into the same executable as those bodies, and
 * the dispatch table calls these. Every constant here is in the original
 * instruction stream; nothing is guessed.
 *
 * Field and function names are chosen to be readable and are documented in
 * module.json with what the evidence does and does not establish. Where the
 * evidence does not reach, the comment says so rather than inventing a name.
 */
#include "win32hle.h"

/* ---- 0x0040BA58 -------------------------------------------------------
 * The Microsoft C runtime's linear congruential generator, one step.
 *
 *   state = state * 0x343FD + 0x269EC3;      (0x0040BA5D, 0x0040BA63)
 *   return (state >> 16) & 0x7FFF;           (0x0040BA6D, 0x0040BA70)
 *
 * The state lives in the image's own global at 0x413350, which is why this is
 * a function over guest memory rather than over a local: the game reads and
 * writes that word elsewhere. The shift is arithmetic in the original (sar);
 * masking to 15 bits afterwards makes the two equivalent for the returned
 * value, so the logical shift here is not a behaviour change.
 */
uint32_t msvc_rand_next(void) {
    const uint32_t MSVC_LCG_MULTIPLIER = 0x000343FDu;
    const uint32_t MSVC_LCG_INCREMENT  = 0x00269EC3u;
    const uint32_t MSVC_RAND_BITS      = 0x7FFFu;

    uint32_t state = MEM32(0x00413350u);
    state = state * MSVC_LCG_MULTIPLIER + MSVC_LCG_INCREMENT;
    MEM32(0x00413350u) = state;

    return (state >> 16) & MSVC_RAND_BITS;
}

/* ---- 0x00401000 -------------------------------------------------------
 * Reset the global configuration block at 0x413BE0.
 *
 * The original is 22 stores of constants into one contiguous block, with no
 * input, no branch and no read. The struct below is that block: the field
 * widths and offsets come from the store instructions, and each value is the
 * constant the original stored. Two callers call this as a setup step before
 * using the block.
 *
 * The names are positional, not semantic: the evidence establishes the shape
 * of the block, not the meaning of its fields. Where a shape is visible it is
 * named for what it is -- a pair of 16.16 fixed-point values at +0x18 and
 * +0x28, four 16.16 fractions at +0x30 -- and where it is not, the field says
 * so. Renaming these to plausible game concepts would be invention.
 */
typedef struct {
    uint8_t  byte_00;            /* +0x00  = 1                        */
    uint16_t word_02;            /* +0x02  = 15                       */
    uint16_t word_04;            /* +0x04  = 0                        */
    uint8_t  byte_06;            /* +0x06  = 0                        */
    uint8_t  byte_07;            /* +0x07  = 0                        */
    uint16_t word_08;            /* +0x08  = 1000                     */
    uint16_t word_0A;            /* +0x0A  = 1001                     */
    uint16_t word_0C;            /* +0x0C  = 0                        */
    uint8_t  byte_0E;            /* +0x0E  = 0                        */
    uint8_t  byte_0F;            /* +0x0F  = 0                        */
    uint16_t word_10;            /* +0x10  = 0                        */
    uint8_t  byte_12;            /* +0x12  = 0                        */
    uint8_t  pad_13[5];          /* +0x13..+0x17 untouched by the original */
    int32_t  point_a_x_fixed16;  /* +0x18  = 0x0020C000 (32.75)       */
    int32_t  point_a_y_fixed16;  /* +0x1C  = 0x00168000 (22.5)        */
    int32_t  point_b_x_fixed16;  /* +0x20  = 0                        */
    int32_t  point_b_y_fixed16;  /* +0x24  = 0                        */
    int32_t  point_c_x_fixed16;  /* +0x28  = 0x00218000 (33.5)        */
    int32_t  point_c_y_fixed16;  /* +0x2C  = 0x0016C000 (22.75)       */
    int32_t  ratio_30_fixed16;   /* +0x30  = 0x00004000 (0.25)        */
    int32_t  ratio_34_fixed16;   /* +0x34  = 0x00000800 (0.125)       */
    int32_t  ratio_38_fixed16;   /* +0x38  = 0x0000C000 (0.75)        */
    int32_t  ratio_3C_fixed16;   /* +0x3C  = 0x00005800 (0.34375)     */
} GlobalConfiguration;           /* 0x40 bytes, at 0x00413BE0 */

void reset_global_configuration(void) {
    GlobalConfiguration *config = (GlobalConfiguration *)hle_ptr(0x00413BE0u);

    config->byte_00           = 1;
    config->word_02           = 15;
    config->word_04           = 0;
    config->byte_06           = 0;
    config->byte_07           = 0;
    config->word_08           = 1000;
    config->word_0A           = 1001;
    config->word_0C           = 0;
    config->byte_0E           = 0;
    config->byte_0F           = 0;
    config->word_10           = 0;
    config->byte_12           = 0;
    config->point_a_x_fixed16 = 0x0020C000;
    config->point_a_y_fixed16 = 0x00168000;
    config->point_b_x_fixed16 = 0;
    config->point_b_y_fixed16 = 0;
    config->point_c_x_fixed16 = 0x00218000;
    config->point_c_y_fixed16 = 0x0016C000;
    config->ratio_30_fixed16  = 0x00004000;
    config->ratio_34_fixed16  = 0x00000800;
    config->ratio_38_fixed16  = 0x0000C000;
    config->ratio_3C_fixed16  = 0x00005800;
}
