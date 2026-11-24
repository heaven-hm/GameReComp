/* Minimal adapter that lets ne_lift.py output build against recomp16/cpu.h.
 *
 * ne_lift.py emits `#include "segments.h"`; the whole-module pipeline generates
 * that header (prototypes + TRACE_FN) from runtime/win16/cpu.h. This smoke test
 * builds ONE leaf function, so it only needs TRACE_FN to exist. Everything
 * else -- CPU, flags_add8, flags_logic16, mem_write8/16, SEG_OFF -- is the real
 * runtime/recomp16/cpu.h, unmodified.
 */
#ifndef RECOMP_WIN16_NATIVE_SMOKE_ADAPTER_H
#define RECOMP_WIN16_NATIVE_SMOKE_ADAPTER_H

#ifndef TRACE_FN
#define TRACE_FN(name) ((void)0)
#endif

#include "recomp16/cpu.h"

#endif /* RECOMP_WIN16_NATIVE_SMOKE_ADAPTER_H */
