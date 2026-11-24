# Stela → GameReComp port: integrated implementation and measured results

## Source audit

- **Exact Stela commit**: `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe` (downloaded from codeload at that SHA; no commit switching).
- Stela direction: **AArch64 guest → x86-64 host** (AOT+JIT). GameReComp direction: **x86-32 PE32 → portable C → native macOS arm64**. The guest decoder and host emitter are mirrors, so they were **not** ported.
- Full ten-audit reports are in `docs/stela-audit/`. They record exact files/functions, dependencies and licences.

Key Stela sources inspected (from the audits):
- `stela-compiler/build.rs`, `stela-fingerprint`, `stela-engine/src/artifact.rs`, `stela-engine/src/cache_flight.rs` — compiler-source fingerprint, content-addressed artifact identity, atomic/protected publication and single-flight.
- `stela-compiler/src/lib.rs` (`compile`, `Lifter`, `Options`, `SOURCE_SHA256`), `block.rs` (`CompilationPlan`, `Terminator`, `ExitKind`), `aarch64.rs`, `x86_64.rs`.
- `stela-compiler/src/state.rs`, `context.rs` — versioned ABI and layout assertions.
- `stela-engine/src/lib.rs` (`prepare`), `xtask/src/oracles.rs`, `xtask/src/oracles/manifest.json` — benchmark provenance, input hashing and host identity.
- Licence: Stela workspace GPL-2.0-or-later; compiler crate `GPL-2.0-or-later AND MIT`; engine `GPL-2.0-or-later AND BSD-3-Clause`. **No Stela source was copied.** The ported technique is algorithmic and reimplemented in original Python under GameReComp's AGPL-3.0.

## Implemented

`tools/autorecomp/lift_cache.py` + wiring in `tools/autorecomp/pipeline.py`:

1. **Fingerprint-bound cache identity**: input PE bytes, all companion DLL bytes, lifting/translation source files (`tools/lift`, `tools/pe`, `tools/disasm`, `pipeline.py`, `lift_cache.py`, `highlevel.py`), options (`split`, `split_bytes`) and typed high-level module contents. Any change produces a new key.
2. **Content-addressed recover cache**: pickled `functions` + `iat_map` + companion function counts, restored only on exact fingerprint match.
3. **Content-addressed translate cache**: generated `recomp_*.c`, headers, units manifest and dispatch, published atomically with `os.replace` and single-flight semantics (an existing entry is never overwritten).
4. **Fail-closed restore**: corrupt entries are ignored and the normal path runs. Cache is opt-in (`RECOMP_LIFT_CACHE=1`) with configurable `RECOMP_LIFT_CACHE_DIR`; default is off, so baseline behavior is unchanged.
5. Stage reports record `"cache": "hit|miss"`; logs say whether recover/translate came from cache.

`tools/stela_bench.py` / `tools/stela_compare.py` (earlier commits) supply the reproducible benchmark and measured percentages; `tests/test_stela_bench.py` covers them.

## Benchmark (real pipeline, same input)

Input: `dxIka.exe` (PE32 x86), SHA-256 from the report; `source-only`, `--jobs 1`, host darwin/arm64, same python/CMake settings. Three runs with a fresh cache directory:

| Run | Wall | Recover | Translate | rc |
|---|---|---|---|---|
| Baseline (`RECOMP_LIFT_CACHE=0`) | 9.81 s | 8.80 s | 0.66 s | 0 |
| Cold cache (`=1`, empty cache) | 7.73 s | 6.75 s | 0.67 s | 0 |
| Warm cache (`=1`, second run) | 0.97 s | 0.48 s | 0.10 s | 0 |

Calculated from `tools/stela_compare.py --before base.json --after warm.json`:

- wall time **-90.11%**
- recover **-94.55%**
- translate **-84.85%**
- generated source bytes **10,280,014 → 10,280,014 (0.0% change)**

Cold-cache time varies with host load; it is not used as the optimized claim.

## Correctness

- Generated C files from baseline and warm runs: **4 files, byte-identical** (SHA-256 over name+content: `4f60123570d9a287d764f1a01b5d92f581cdc62477505dfd90f962` for both).
- The only differing generated file is `recomp_image.S`, whose later stage embeds the absolute path of its own project directory; it is regenerated after the cached translate stage.
- `python3 -m pytest tests/ -q` → **399 passed, 11 skipped, 26 subtests passed**.
- `tests/test_lift_cache.py` verifies fingerprint sensitivity to input/tool/options, corrupt-entry fallback, atomic gen-dir restore and opt-in default.

## Rejected techniques (with reasons)

- Stela's AArch64 decoder, x86-64 byte emitter, host register pinning/LRU vector cache, PAC/SVE/AMX, Apple SoC cache IDs, XNU/UEFI boot internals: wrong guest/host architecture or kernel-specific; incompatible with GameReComp's C-emitter, cross-platform design.
- Copying GPL-2.0-only Stela code into AGPL-3.0-only GameReComp: licence conflict; only methodology was reimplemented.
- JIT-only coherence/epoch patching and page-table tricks: no JIT in GameReComp; not applicable.
- Per-call-site inline caches and source maps: valuable but need separate lifecycle work; not included in this measured change.

## Remaining opportunities

1. Apply the same fingerprint/atomic-publish pattern to the build tree (incremental object cache).
2. Machine-readable source map from generated C back to guest instructions (Stela `NativeBlock.source_map` pattern).
3. Structured refusals with named causes across lifter reports.
4. Per-callsite indirect-call cache using the existing `icall` trace ring (JIT-derived; AOT adaptation required).
5. CPU-feature-aware artifact identity for host specialization.

No issue was closed, and no gameplay/100% claim is made.
