# S1 — Stela compiler architecture & translation pipeline audit

**Read-only audit.** No repository was edited. All paths below are relative to
`$STELA` (`/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/stela`)
unless prefixed `$GAMERECOMP`
(`/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/integration`).

## 0. Provenance / pin verification

- Target pin: `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe`, fetched from codeload.
- The tree has **no `.git` directory**; it is a plain codeload tarball extraction,
  so there is no local ref to switch and none was switched.
- Integrity evidence consistent with a single pinned download: all 5,386
  non-`third_party/` files carry the identical mtime `2026-11-20 17:57`;
  `Cargo.toml`, `Cargo.lock`, `LICENSE`, `NOTICE`, `README.md`, `crates/`,
  `xtask/`, `third_party/` (submodule pointers + patches) are all present.
- Workspace identity: `repository = https://github.com/tylertms/Stela`,
  `version = 0.5.0`, `edition = 2024`, `rust-version = 1.99`
  (`Cargo.toml:62-67`), toolchain pinned to `1.99.1` (`rust-toolchain.toml`).

## 1. Executive summary — direction mismatch (critical)

Stela is **not an x86 recompiler**. It is the mirror image of GameReComp:

| | Guest (input ISA) | Host / output | Pipeline kind |
|---|---|---|---|
| **Stela** | **AArch64 / arm64e** (Apple M2 Mac mini persona; macOS guest) | **x86-64 native machine code** | AOT + JIT dynamic binary translation, UEFI boot layer |
| **GameReComp** | **x86-32 PE32** (`IGI.EXE`, 115 apps in matrix) | **C source → native macOS arm64 binary** | Static recompilation (PCRecomp / rev.ng + runtime shims) |

Evidence: `README.md:1-12` ("translation-based Apple Silicon layer for x86-64
PCs… arm64-to-x86-64 translator… AOT and JIT compiler translates arm64e code to
x86-64"); `crates/stela-compiler/src/lib.rs:1-3` ("Ahead-of-time translation of
ARM64 code to native x86-64. Unsupported instructions are compile errors; there
is no interpreter fallback").

**Consequence:** Stela's *front end* (AArch64 decoder) is useless for GameReComp,
and its *back end* (x86-64 byte emitter) targets the wrong host. What *is*
portable is the architecture-neutral skeleton around them, the cache/provenance
discipline, and the verification methodology. Concrete candidates in §9.

## 2. Workspace crate map

From `Cargo.toml:1-60` (28 members, 3 excluded: `stela-boot`, `stela-mesa`,
`third_party/smoltcp`). Licence column is the crate's `Cargo.toml` `license`
field; `license.workspace = true` means GPL-2.0-or-later.

| Crate | Role in translation | Licence | Portability to x86→native |
|---|---|---|---|
| **stela-compiler** | `no_std` AArch64→x86-64 AOT compiler (decoder, IR, analysis, emitter) | **GPL-2.0-or-later AND MIT** | Skeleton portable; ISA ends are not (§9) |
| **stela-engine** | AOT/JIT orchestration, block runtime, dispatcher, cache artifacts, translation store | GPL-2.0-or-later AND BSD-3-Clause | **High** — cache identity/integrity/dispatch patterns |
| stela-cli | Host installer / update / log CLI (`main.rs`) — packages `stela.efi`, does not translate | GPL-2.0-or-later | Low |
| stela-loader | macOS kernel/AUXKC boot loading, device tree, chained fixups | GPL-2.0-or-later | None (guest-OS boot specific) |
| stela-boot | UEFI application; live engine, persistent translation store, JIT miss install | GPL-2.0-or-later AND GPL-2.0-only AND BSD-2-Clause | Low (UEFI), store design interesting |
| stela-boot-core | Boot services, SMP, memory, PCIe, handoff | GPL-2.0-or-later AND GPL-2.0-only | None |
| stela-runtime | PC/FAT32/GPT/storage/net/TLS restore platform | GPL-2.0-or-later | None |
| stela-dev | Research CLI: `compile`, `execute`, images, loader | GPL-2.0-or-later | Medium — AOT command/driver pattern |
| stela-fingerprint | Build-time SHA-256 of compiler sources + locked registry deps | GPL-2.0-or-later | **High** — compiler-version cache binding |
| stela-formats | APFS, code signature, IMG4, LZFSE, payload, journal, SHA-256 | GPL-2.0-or-later | Low (used by cache integrity) |
| stela-contracts | `require!`/`refuse!` fail-closed invariants | GPL-2.0-or-later AND BSD-3-Clause | Medium — fail-closed style |
| stela-c-build, stela-linux, stela-driver*, stela-soc*, stela-tls, stela-crypto | Linux kernel/Mesa/LLVM build + ported drivers + TLS/crypto | mixed (GPL-2.0-only components) | None |
| stela-gpu-maintenance, xtask | macOS companion app; build/test/reference tasks | GPL-2.0-or-later | Medium — `cpu-reference` oracle workflow |
| third_party/ | linux, mesa, llvm, libdrm, xnu submodules + patches | per-upstream (see NOTICE) | None |

## 3. Translation pipeline — stages and data flow

```
   AArch64 bytes (guest region / leaf source)
            │  source: &[u8], base: GuestAddress, Options
            ▼
  [1] DECODE / LIFT   aarch64::Decoder::lift  (Lifter trait)
            │   Vec<Instruction>{ pc, operation: Operation }
            ▼
  [2] ANALYZE         block::analysis::{parse_with, analyze_parsed}
            │   ParsedBlock{instructions, terminal, next_pc, source_bytes, region}
            │   BlockAnalysis{static_successors, dynamic_edge, trap, ...}
            ▼
  [3] PLAN            block::CompilationPlan::{new, block}
            │   validate options + target; select Stage{Block|Leaf|ContextLeaf|ContextBlock}
            │   memory::CheckedPlan, ResidentLoopPlan; identity = source+compiler+target+abi
            ▼
  [4] EMIT            x86_64::Emitter / block::{emit_ordinary_for_target,
            │   emit_system_mode_looped, ...}; asm::Code byte buffer + fixups
            ▼
  [5] LINK/RELOCATE   patch branch fixups; record relocations; source_map
            │   NativeBlock{x86_64_code, source_map, relocations}
            │   CompiledBlock{native, source_bytes, direct_exits, fault_recovery, ...}
            ▼
  [6] ARTIFACT/CACHE  stela-engine: manifest.json + manifest.sha256.json + native.bin
            │   Identity{source_sha256, compiler_source_sha256, guest_base, abi, target_cpu, ...}
            ▼
  [7] LOAD/DISPATCH   BlockRuntime::load -> platform::Mapping (mmap/RWX)
            │   run_inner: call_block -> BlockExit{next_pc, kind} -> dispatcher
            ▼
   Native x86-64 execution; exits return to engine (no guest-PC jumps)
```

### Stage details (exact files / functions)

1. **Lift / decode** — `crates/stela-compiler/src/aarch64.rs`
   - `pub struct Decoder` (`:387`), `impl Lifter for Decoder` (`:440`, `lift(pc, word)`).
   - `pub trait Lifter` (`lib.rs:68-72`): `lift(&self, pc, encoding: u32) -> Result<Instruction, UnsupportedReason>` — architecture-neutral seam.
   - `Operation` enum (`:227-367`, ~40 variants: integer/math, memory, atomic, ordered, SIMD, FP, PAC, AMX, WKDM-adjacent system…), `Instruction{p pc, operation}` (`:382`).
   - Dispatch cascade through ~30 per-family decoders (`atomic`, `bitfield`, `checked_pointer`, `simd`, `sve`, `sme`, `floating`, `system`, …), all `mod`ulated at `:3-29`.
   - Fail-closed taxonomy: `UnsupportedReason` (`lib.rs:38-65`) incl. `HostCapabilityRequired`, `NumericalRuntimeRequired`, `UnresolvedDirectTarget`, `RuntimeGeneratedCode`, `PrivilegedOperation`.

2. **Analysis** — `crates/stela-compiler/src/block/analysis.rs`
   - `ParsedBlock` (`:17`), `BlockAnalysisError` (`:28`), `BlockAnalysis` (`:82`), `StaticSuccessor`/`DynamicEdge` (`:64-80`).
   - `parse_with`, `analyze_parsed`, `analyze_and_plan` (`block.rs:313`), `source_bytes_with_profile`.
   - Block terminator model `Terminator` (`block.rs:257-277`), `decode_terminator` (`:279`), `ExitKind` (`:60-78`, 13 kinds: Fallthrough/Direct/Indirect/Call/Return/Trap/PAC/Clock/ThreadRegister/FloatingPoint/ScalableNumerical/System/Amx). `MAX_BLOCK_INSTRUCTIONS = 256` (`:58`).

3. **Plan / options / profiles**
   - `Options` (`lib.rs:92-210`): `BlockProfile`, `TargetCpu`, optional `ScalableProfile`, numerical features, `MemoryChecks`, `NativeSystem`; `validate()` refuses impossible combos.
   - `BlockProfile` / `Features` (`block/profile.rs`, `block/features.rs`): ORDINARY, LEAF, CONTEXT, SYSTEM_MODE, FULL_SYSTEM, RESIDENT_LOOPS, CLOCK, THREAD_REGISTERS, POINTER_AUTHENTICATION; `abi()` yields strings like `x86_64-sysv-guest-state-v15-` (`state.rs:206`).
   - `CompilationPlan` (`block.rs:331-703`): `new` (`:381`) picks one of four `Stage`s; `block()` (`:422`) does target validation + `CheckedPlan`/`ResidentLoopPlan`; `memory_requirements()` (`:529`); `compile_impl()` (`:580`) selects emitter path; `compile_profiled()` (`:572`).
   - Top-level convenience: `pub fn compile(source, base, options)` (`lib.rs:218-224`) = `CompilationPlan::new(...).compile()`.

4. **Emit (x86-64 host)**
   - `pub struct Emitter` (`x86_64.rs:13`), `impl Backend<Instruction> for Emitter` (`:1561`), `emit_for_target` (`:1567-1607`): validates target, builds `Emitted{x86_64_code: asm::Code, source_map}`, calls `emit_instruction` (`:1348`), patches fixups.
   - Per-operation emit dispatch `emit_instruction` (`x86_64.rs:1348-1559`) → dozens of `emit` modules (`integer_math`, `bitfield`, `simd/*`, `sve/*`, `memory*`, `atomic`, `flags`, `pinned`, `vector_cache`, `chain`, `recovery`, …).
   - Block-level emitters: `x86_64/block.rs` — `emit_ordinary_for_target`, `emit_system_mode_looped`, `emit_resident_fallback_for_target`, `successor_exits`, `align_exits`; self-loops, region layout, chainable exits.
   - Typed encoder: `x86_64/asm/*` (`code.rs:7 Code` with `bytes`, `instructions`, `sites`, `relocations`, `pinned`, `vector cache`; `operands.rs`, `control.rs`, `memory.rs`, `prefixes.rs`, `sse.rs`, `vector_extensions.rs`, `apx.rs`). `x86_64/isa.rs:58-80` `catalog!` macro builds `SimdOpcode` enum with `requires() -> TargetCpu` and encoded bytes — one table drives encoding *and* feature gating.
   - Capability model: `target.rs` — `TargetCpu` (explicit feature bits + nested SSE tier), `HostCpuid::read()` (CPUID/XGETBV, `:74-127`), `Feature` set incl. AVX/AVX-512/APX/CX8/CX16/AtomicLoad16/PCLMULQDQ; `validate_target*` (`x86_64.rs:14-58`) refuses before emission.

5. **Output / ABI**
   - `NativeBlock{x86_64_code: Vec<u8>, source_map: Vec<(GuestAddress, usize)>, relocations: Option<Vec<u32>>}` (`lib.rs:74-86`).
   - `CompiledBlock` (`block.rs:217-255`): adds `source_bytes`, `direct_exits` (chain patches), `fault_recovery`, `self_loop`, `region`, `cold` (profiled hot/cold split).
   - `GuestState` (`state.rs:79-129`, `repr(C)`, 920 bytes): 31×X + SP + NZCV + 32×V + FPCR/FPSR + exclusive pair + DAIF/PAN + `Anchors`, `lower_limit`, `loop_retired`, `exit_rsp`, `account`. Entry ABI: RDI = `GuestState*`, RSI = `ExecutionContext*`; System V, `BlockExit function(GuestState*)` (`block.rs:3-6`).
   - `ExecutionContext`/`ScalableStateView` (`context.rs:26-71`): pointers to memory service, scalable state, system service; emitter preserves context in R15. `Anchors`/`Place` (`state.rs:5-69`): code refers to PE services by *anchor + offset*, never a concrete PE address (supports multi-PE / relocatability).
   - `block/recovery/*`: `FaultRecovery`, `RecoverySite`, spills, retirement, qualification — native fault → guest-precision mapping.

6. **Artifact / cache** — `crates/stela-engine`
   - `artifact.rs`: `Identity` (schema_version, source_sha256, **compiler_source_sha256**, guest_base, abi, target_cpu, memory_specialization_sha256), `Manifest` (native_sha256, native_bytes, source_map, verified, memory_requirements), `validate_native_snapshot`, `validate_source_map`.
   - `lib.rs:70 compiler_source_sha256()` ← `stela_compiler::SOURCE_SHA256` (`lib.rs:33`, generated by `crates/stela-compiler/build.rs` via `stela-fingerprint`, which hashes all non-test sources + locked `rustc_apfloat`).
   - `lib.rs:149 prepare_leaf`, `:213 prepare_block_for_target`, `:234 prepare_block_plan`, `:251 block_plan_identity`. Cache directory keyed by SHA-256 of identity JSON; files `manifest.json`, `manifest.sha256.json`, `native.bin`.
   - `blocks/discovery.rs:95 prepare_snapshot_attempt`: analyze → plan → cache lookup → compile on miss → verify source map.
   - `blocks/aot.rs`: `ArtifactPreparation::{FileCache, AheadOfTime, InMemory}`; `preload_artifact` (`:118`) validates before mapping; strict AOT refuses misses.
   - `cache_flight.rs`: per-key mutex so concurrent misses publish once.
   - `artifact/prepare.rs`: `generate(identity, requirements, compile_closure)`.

7. **Dispatch / execute** — `crates/stela-engine`
   - `blocks.rs`: `BlockRuntime` (`:73`), `register_code` (`:149`), `load` (`:197`), `run`/`run_with_system`/`run_inner` (`:242`/`:341`/`:367`). `run_inner` loop: PC→region lookup→loaded-block memory hit or cache/compile miss→generation check→`mapping.call_block()`→interpret `BlockExit`→continue/exit.
   - `dispatch.rs`: `LeafDispatcher`, `DispatchIdentity`, `ResolvedLeaf`, `guest_instruction_for_native_offset` (native offset → guest PC via source map).
   - `execution/native_image.rs`, `execution/platform/{posix,freestanding}` — mapping/RWX entry.
   - Boot-time mirror: `crates/stela-boot/src/engine/translations.rs` (journaled FAT32 pack store, shared across PEs), `.../engine/host/translated.rs`, `.../engine/large_blocks.rs`.

8. **Tooling / entry points**
   - `crates/stela-dev/src/main.rs` + `src/compile.rs`: `CompileBlock` CLI (`--cpu-target`, `--guest-base`, `--guest-profile`, …) — AOT artifact generator / research driver.
   - `xtask/src/{dist,cpu_reference,sysregs,check}.rs`: `cargo xtask dist`, on-real-CPU oracle corpus, syscall/register codegen.
   - `crates/stela-cli/src/main.rs`: installer only (writes `stela.efi` to USB), `args.rs`, `boot.rs`, `restore/*`.

## 4. Dependencies

- Workspace (`Cargo.toml:69-93`): `anyhow`, `clap` (derive), `serde` (no_std + alloc/derive), `serde_json`, `rustc_apfloat =0.2.3` (floating point emulation used by emitters), `sha2`, `hex`, `libc`, `tempfile`, plus path deps between crates.
- `stela-compiler` direct: `serde`, `rustc_apfloat`; build-dep `stela-fingerprint`. Everything else is workspace-internal.
- `stela-engine`: `serde`, `serde_json`, `stela-formats` (sha256), `stela-contracts`, `stela-compiler`; `alloc` with optional `std` feature; `no_std`-capable.
- External systems: LLVM 21–23/Clang/LLD/libclang; vendored `third_party/{linux,mesa,llvm,libdrm,xnu}` submodules; `smoltcp` patched in-tree. `rustc_apfloat` is deliberately part of the compiler fingerprint because it computes emitted values.

## 5. Licensing (from Cargo.toml / LICENSE / NOTICE)

- Root workspace: **GPL-2.0-or-later** (`Cargo.toml:65`); full text in `LICENSE` (GPL v2).
- `stela-compiler`: **GPL-2.0-or-later AND MIT** (`crates/stela-compiler/Cargo.toml:5`) — SPDX `AND` means *both* obligations apply if copied. No per-file SPDX headers in compiler sources.
- `stela-engine`: **GPL-2.0-or-later AND BSD-3-Clause**; `stela-fingerprint`, `stela-formats` etc. inherit GPL-2.0-or-later.
- `NOTICE:1-30`: Stela's own code GPL-2.0-or-later; per-file SPDX overrides; shipped programs have compound licensing (`stela.efi` GPL-2.0-only due to Linux; CLI GPL-3.0-or-later due to Apache-2.0 crates). `LICENSES/` holds 21 texts (MIT, BSD-3-Clause, Apache-2.0, GPL-2.0-only, GPL-3.0-or-later, MPL-2.0, …).
- **Implication:** any direct code reuse into GameReComp is copyleft (GPL-2.0+ at minimum; compiler files also MIT). Pure *design/pattern* reuse avoids licence inheritance. If any Stela code is lifted, GameReComp must ship corresponding source under GPL and retain `LICENSE`, `NOTICE`, `LICENSES/`.

## 6. Per-piece portability assessment

| Piece | Exact location | Portable? | Why |
|---|---|---|---|
| `Lifter` trait + `Instruction`/`Operation` IR | `lib.rs:68-72`, `aarch64.rs:227-385` | **Architecture-neutral pattern, not payload** | Keep trait + fail-closed `UnsupportedReason`; replace ARM `Operation` variants with x86-32 semantics |
| `CompilationPlan`, analysis, `Options`/`BlockProfile`, `compile()` | `block.rs:331-703`, `block/analysis.rs`, `lib.rs:92-224` | **High (skeleton)** | No ISA-specific code in the orchestration; swap decoder + emitter |
| AArch64 `Decoder` + 30 sub-decoders | `aarch64.rs` + `aarch64/**` | **No** | Guest ISA is arm64e |
| `x86_64::Emitter`, `emit_instruction`, `x86_64/**` byte emitters | `x86_64.rs`, `x86_64/**` | **No as code; medium as pattern** | Emits x86-64 bytes; GameReComp emits C for arm64. Typed operand + opcode-catalog design is portable |
| `TargetCpu`/`HostCpuid` feature gating | `target.rs`, `x86_64/isa.rs`, `x86_64.rs:14-58` | **High (concept)** | "Every emitted construct declares required host features; validate before emit; bind to output identity" maps directly to C intrinsics/arch flags |
| Artifact identity + integrity + cache | `stela-engine/artifact.rs`, `artifact/prepare.rs`, `blocks/discovery.rs`, `stela-compiler/build.rs` | **Very high** | Direct analogue of GameReComp's recompile ledger / provenance; compiler-source fingerprint is the missing piece |
| `GuestState`/`ExecutionContext`/`Anchors` ABI | `state.rs`, `context.rs`, `block/recovery/*` | Medium | Guest-state-ABI + exit-boundary idea maps to `runtime/native32`, `runtime/recomp32_cpu` shims; layout is ARM-specific |
| Dispatch loop + block exits | `stela-engine/blocks.rs`, `dispatch.rs`, `cache_flight.rs` | Medium | Useful if GameReComp adds JIT/patching; static C pipeline has no runtime dispatcher |
| Verification: native execution harness, oracle corpus, `cpu-reference` | `x86_64/native.rs`, `simd/*/tests/oracle.rs`, `xtask/src/cpu_reference.rs`, `testing.rs` | **High (methodology)** | Matches GameReComp's evidence-first ethos ("fail closed instead of guessing") |
| Boot/loader/drivers/formats/TLS/crypto | `stela-boot*`, `stela-loader`, `stela-runtime`, `stela-driver*`, `stela-formats`, `stela-tls` | None | Apple-hardware boot stack, irrelevant to PE32 recompilation |

## 7. Key file paths (manifest)

```
$STELA/Cargo.toml                                   workspace, members, licence
$STELA/README.md                                    direction + feature summary
$STELA/NOTICE, LICENSE, LICENSES/                   licensing
$STELA/crates/stela-compiler/Cargo.toml             "GPL-2.0-or-later AND MIT"
$STELA/crates/stela-compiler/build.rs               compiler-source SHA fingerprint
$STELA/crates/stela-compiler/src/lib.rs             compile(), Lifter, Options, NativeBlock, SOURCE_SHA256
$STELA/crates/stela-compiler/src/aarch64.rs         Decoder, Operation, Instruction, lift()
$STELA/crates/stela-compiler/src/block.rs           CompilationPlan, Terminator, ExitKind, CompiledBlock, stages
$STELA/crates/stela-compiler/src/block/analysis.rs  parse/analyze, successors, BlockAnalysis
$STELA/crates/stela-compiler/src/context.rs         ExecutionContext, ScalableStateView
$STELA/crates/stela-compiler/src/state.rs           GuestState (920-byte ABI), Anchors, Place
$STELA/crates/stela-compiler/src/target.rs          TargetCpu, HostCpuid, feature detection
$STELA/crates/stela-compiler/src/x86_64.rs          Emitter::emit_for_target, emit_instruction, validate_target
$STELA/crates/stela-compiler/src/x86_64/block.rs    block emitters, chaining, self-loops
$STELA/crates/stela-compiler/src/x86_64/asm/code.rs Code buffer, relocations, emission sites
$STELA/crates/stela-compiler/src/x86_64/isa.rs      SimdOpcode catalog + requires()
$STELA/crates/stela-compiler/src/x86_64/native.rs   host execution harness (tests)
$STELA/crates/stela-fingerprint/src/lib.rs          emit_with() hashing
$STELA/crates/stela-engine/src/lib.rs               prepare_leaf/block, block_plan_identity
$STELA/crates/stela-engine/src/artifact.rs          Identity, Manifest, integrity checks
$STELA/crates/stela-engine/src/blocks.rs            BlockRuntime::run_inner (dispatch loop)
$STELA/crates/stela-engine/src/blocks/discovery.rs  prepare_snapshot_attempt (cache/compile miss)
$STELA/crates/stela-engine/src/blocks/aot.rs        AOT/in-memory/file-cache preparation
$STELA/crates/stela-engine/src/dispatch.rs          LeafDispatcher, source-map lookup
$STELA/crates/stela-engine/src/cache_flight.rs      single-publisher miss dedup
$STELA/crates/stela-dev/src/compile.rs              AOT compile CLI
$STELA/crates/stela-boot/src/engine/translations.rs persistent UEFI translation store
$STELA/xtask/src/{dist,cpu_reference,sysregs}.rs    build + on-CPU oracle tooling
```

## 8. Honourable mentions (not in top 3)

- **Fail-closed diagnostics**: `UnsupportedReason` + `stela-contracts::{require,refuse}` + `CompileError::at_instruction` give source-address-anchored, typed refusal reasons — directly matches GameReComp's "fail-closed instead of guessing" policy and could enrich its rejection reporting.
- **Anchors/Place indirection**: emitted code never stores a PE pointer; it stores `(anchor, offset)`. Transferable to GameReComp's shim layer if multiple contexts/hosts must share generated C.
- **Cold-code profiling**: `CompiledBlock.cold`, `compile_profiled`, `block/profile.rs` move slow paths out of line based on host samples — analogous to PGO for generated C.

## 9. Top 3 concrete adaptation candidates for GameReComp

### #1 — Content-addressed, compiler-fingerprinted artifact cache + integrity envelope (highest value, lowest risk)
**Source:** `stela-compiler/build.rs` + `stela-fingerprint` → `SOURCE_SHA256`;
`stela-engine/artifact.rs` (`Identity`, `Manifest`, `validate_native_snapshot`,
`validate_source_map`), `lib.rs:251 block_plan_identity`, `artifact/prepare.rs`,
`blocks/discovery.rs:95`, `cache_flight.rs`.
**Adaptation:** GameReComp already keeps `recompile_ledger`, `reports/` and
`table.md` for 115 PE32 apps. Add a canonical identity for every generated C
unit: `sha256(exact source prefix) + sha256(generator sources/config/lockfile) +
guest_base + output ABI + target arch/flags`. Store manifest + checksum beside
each generated unit; verify *before* building/executing; refuse stale outputs
instead of rebuilding blindly. Mirror `cache_flight.rs` so concurrent per-function
recompiles publish once. This is pure design reuse (no GPL code copy) and
directly improves reproducibility/provenance claims in `table.md`.

### #2 — Architecture-neutral front-end skeleton with typed IR and fail-closed refusals
**Source:** `lib.rs:68 Lifter`, `lib.rs:38 UnsupportedReason`,
`block.rs:331 CompilationPlan`/`:580 compile_impl`, `block/analysis.rs`,
`Options`/`BlockProfile`, `Backend<I>` trait (`lib.rs:226`).
**Adaptation:** Extract the *shape* — `decode → ParsedBlock + analysis → Plan →
Backend.emit` — and re-target it: implement `Lifter` for x86-32 (GameReComp's
existing `tools/lift`, `tools/autorecomp`, `tools/disasm` already produce the
semantics), define an x86 `Operation` IR, and implement `Backend` with a **C
emitter** instead of `Emitter`'s byte emitter. Reuse `UnsupportedReason` as the
diagnostic vocabulary so unsupported x86 encodings (e.g. SEH, exotic FPU) become
typed, PC-anchored refusals rather than silent bad C. This also gives a differential
oracle: run the same IR through Stela-style block semantics vs the existing static
C path during verification.

### #3 — CPU-feature-gated emission catalog + reference-driven verification
**Source:** `x86_64/isa.rs:58-80` (`catalog!`, `SimdOpcode::requires()`),
`target.rs` (`TargetCpu`, `HostCpuid::read`), `x86_64.rs:14-58 validate_target*`,
`x86_64/native.rs` (execute emitted code on real host),
`simd/{sm3,sm4}/tests/oracle.rs`, `xtask/src/cpu_reference.rs`.
**Adaptation:** For arm64-native output, every generated intrinsic/ASM construct
should declare a `required target feature` from one table; validate before
emission and record the resolved feature set in the artifact identity from #1
(so, e.g., a NEON/FP16/SVE choice is reproducible per host). Adopt Stela's
evidence pattern — a checked-in corpus of encodings executed on real CPUs with an
independent oracle — to harden GameReComp's `docs/`, `tests/` and game-matrix
verification, replacing "compiles and launches" rows with semantic equivalence
checks. The `catalog!` macro style (one declarative table drives encoding,
mnemonic, feature gate and tests) is especially cheap to reimplement in Python/C++.

## 10. Caveats / limits of this audit

- Read-only source inspection; no builds, no test runs, no injection/live-game work.
- Pin verified only by codeload-extraction metadata (uniform mtimes, no `.git`); no
  upstream network check was performed, per the no-switch/no-edit constraint.
- Stela is pre-release ("slow, may not boot on supported hardware", `README.md:13-16`);
  its architecture is informative, not a guarantee of maturity.
- Licence conclusions are from manifest/notice files, not legal advice.
