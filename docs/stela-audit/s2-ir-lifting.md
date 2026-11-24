# S2 audit — Stela intermediate representations, instruction translation, x86 semantics

**Mode:** read-only source inspection. No repository was edited, no build/test run.
**Sources:** Stela snapshot at `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/stela`
(task pin `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe`; the extracted snapshot has no `.git`, so the pin
could not be re-verified locally); GameReComp snapshot at
`/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/integration`
(`git rev-parse HEAD` = `bc12deed22d7cd593012d1cbfe48dd96235e9d70`).

## Executive summary

Stela and GameReComp solve the same class of problem at opposite ends of the portability spectrum:

| | Stela | GameReComp |
|---|---|---|
| Guest ISA | **AArch64 / arm64e** (`crates/stela-compiler/src/aarch64.rs`) | **x86-32** (`tools/lift/lift32.py`, `lift32_cpu.py`, `lift32_sse.py`, `lift16.py`) |
| Host / output | **x86-64 native machine code**, hand-built typed binary encoder (`x86_64/asm.rs`) | **portable C** text, compiled for x86-64 / arm64 / wasm32 (`runtime/recomp32/recomp_types.h`) |
| Semantic IR | **Typed Rust enums**: `Instruction { pc, operation: Operation }` with typed operand enums per family | **Capstone `Instruction` → C strings**; thin `disasm32.Instruction` / `generate.LinearInstruction` wrappers; flag semantics encoded as `FK_*` kind + captured operands at runtime |
| Error policy | Unsupported instruction ⇒ **compile error with source context**, no interpreter fallback | Per-lifter fallbacks (`???`, `/* flag from ... */`, `CMP_O = 0`), with **fail-closed function-level promotion** in `multilifter.py` |
| State contract | `#[repr(C)] GuestState` in RDI, versioned `ExecutionContext`, `offset_of!` layout tests | Global `g_eax…g_edi`, `g_st`, `_mm`, `_xmm`, `ADDR()`/`MEM*()` macros, `RECOMP_REGS_*`, `RECOMP_FLAGS_*` |

**Therefore:** no guest-ISA semantics (x86 decoding, flag derivation, x87/MMX/SSE behaviour) can be
transplanted from Stela — Stela does not implement x86 *guest* semantics at all. What *is* transferable
is the **engineering structure around the translator**: typed IR boundary, structured errors, versioned
ABI, provenance manifests, capability validation, source maps, CFG edge taxonomy, and reference-model
testing. GameReComp already leads in two areas (lazy flag model, fail-closed multi-lifter promotion);
those should be kept, not replaced.

---

## 1. Stela: exact files, functions and data model

All paths below are relative to the Stela snapshot root.

### 1.1 Translator front end (decode → typed semantic ops)

* `crates/stela-compiler/src/lib.rs:68` — `pub trait Lifter { type Instruction; fn lift(&self, pc: GuestAddress, encoding: u32) -> Result<Self::Instruction, UnsupportedReason>; }`.
  The trait's doc comment (`lib.rs:67`) states an explicit invariant: *"A lifted instruction must describe architectural effects, not just syntax"*.
* `crates/stela-compiler/src/aarch64.rs:227` — `pub enum Operation` (~100 typed variants: `IntegerMath`, `Memory`, `Atomic`, `FloatingPoint`, `Simd`, `Synchronization`, branches, `Return`, …).
  `crates/stela-compiler/src/aarch64.rs:382` — `pub struct Instruction { pub pc: GuestAddress, pub operation: Operation }`.
  Each family has a typed operand decoder in its own module, e.g. `aarch64/integer_math.rs::decode`, `aarch64/simd/arithmetic.rs::decode`, `aarch64/flags.rs::decode` (line 98). Many return `Option<Result<Op, Unsupported>>`, separating "not my class" from "reserved/undecodable".
* `impl Lifter for Decoder` — `aarch64.rs:440` onward: one long, ordered if/else chain of family decoders. Unknown words become `UnsupportedReason::Instruction { address, encoding }`. There is deliberately no interpreter fallback.
* `crates/stela-compiler/src/aarch64/floating.rs:1-40` — guest FP semantics in software via `rustc_apfloat::{Float, FloatConvert, Round, Status, StatusAnd}` and `ieee::{Double, Half, Quad, Single}`; explicit `FPCR_*`/`FPSR_*` masks, `Outcome { bits, exceptions }` so the caller owns sticky exception state.
  `aarch64/floating/features.rs:20` — `NumericalFeatures { full_a64, ebf16, rpres }` decoded from guest ID registers, explicitly "independent of host CPU features" (line 1).
  `aarch64/flags.rs:1-100` — `condition_holds`, `Operand::{InvertCarry,RotateMask,Set,ArmToExternal,ExternalToArm,Read,Write}`; algebraic flag transformations with exhaustive table tests in `x86_64/flags.rs` tests.

### 1.2 Block analysis and IR-to-backend planning

* `crates/stela-compiler/src/block/analysis.rs:28` — `BlockAnalysisError { pc, encoding, kind }`; `:55` `SuccessorKind`, `:70` `DynamicEdgeKind`, `:82` `BlockAnalysis { source_bytes, terminator_pc, static_successors, dynamic_edge, trap, system_instruction, … }`.
  `:97 pub fn analyze` and `:101 analyze_with_profile` share the parser used by AOT and JIT.
  `parse_admitted` (`:276` onward) consumes at most `MAX_BLOCK_INSTRUCTIONS = 256` (`block.rs:58`) and stops at the first transfer/trap/runtime exit; dynamic register transfers are classified, not guessed. Static call continuations are explicitly "candidates, since the callee may not return" (`:88-90`).
* `crates/stela-compiler/src/block.rs:62` — `#[repr(u64)] enum ExitKind { Fallthrough, Direct, Indirect, Call, Return, Trap, PointerAuthentication, Clock, ThreadRegister, FloatingPoint, ScalableNumerical, System, Amx }` — the native-to-dispatcher exit contract.
  `:218 pub struct CompiledBlock { native, source_bytes, direct_exits, fault_recovery, self_loop, region, cold }`.
  `:258 pub(crate) enum Terminator { Branch, DirectCall, Register, Trap, System, PointerAuthentication, Clock, ThreadRegister, FloatingPoint, Amx }`.
  `:331 pub struct CompilationPlan`; `:166 Options::validate` rejects option combinations with no emitter; `:580 fn compile_impl` dispatches to checked / resident-loop / ordinary / system-mode emission and constructs `CompiledBlock`.
  `:313 pub fn analyze_and_plan`; the `CompiledBlock` fields (`:218`) are consumed by the engine for chaining and fault recovery.
* `crates/stela-compiler/src/lib.rs:76` — `NativeBlock { x86_64_code: Vec<u8>, source_map: Vec<(GuestAddress, usize)>, relocations: Option<Vec<u32>> }`. `source_map` is pushed once per guest instruction in `x86_64.rs:1567` (`Emitter::emit_for_target`) and in `x86_64/block.rs:1232+`; relocations are the offsets of embedded 8-byte guest-address fields, so a block can be moved by adding the page displacement (`Code::into_parts`, `x86_64/asm/code.rs:304`).

### 1.3 Back end / x86 semantics

* `crates/stela-compiler/src/x86_64.rs:1` — "RDI holds the guest state pointer throughout"; `:73 struct Emitted { x86_64_code: asm::Code, source_map }` with `:79 fn into_native`.
  `:17 validate_target` / `:26 validate_target_with` reject privileged operations or require host features (e.g. `AtomicLoad16`, `Cx16`) **before emission**.
  `:1562 impl Backend<Instruction> for Emitter`, `emit_for_target` validates each instruction, refuses numerical ops without a runtime, records source map, emits.
* `crates/stela-compiler/src/x86_64/asm.rs:1-30` — typed x86-64 encoder; "operands are typed registers and memory; operand-size prefixes, REX, VEX, ModRM, SIB and displacements are derived from them, and `Asm` counts every instruction it emits." `Asm` (`:30`) also tracks fault sites, pinned locals, vector cache, guest-vector guards and relocation sites. `x86_64/asm/code.rs:7` `Code` carries bytes + instruction count + `EmissionSite`s + relocations across every `Asm` built over it.
* `crates/stela-compiler/src/x86_64/flags.rs:1` — "FlagM/FlagM2 on the in-memory guest NZCV"; explicit table-driven conversions with exhaustive tests (`external_table_matches_all_architectural_flag_inputs`). This is **x86-host implementation of AArch64 flags**, not x86 guest flags.
* `crates/stela-compiler/src/block/recovery.rs:16-210` — `RecoverySource::{HostRegister, NativeSpill}`, `FaultRecovery { start, end, bindings, retirement, site }`, `FaultRecovery::site_at` / `::apply` validate the *entire* recovery settlement before mutating guest state, rejecting conflicting bindings, missing spills and inconsistent retirement points. Fault intervals come from `Asm::guest_access` / `guest_check` / fault sites in `asm.rs:110-200`.
* `crates/stela-compiler/src/target.rs:20` — `TargetCpu` bitfield of explicit host capabilities, `HostCpuid`, vendor policy, `TargetCpu::detect`, stable string IDs used for cache identity. `x86_64/isa.rs` catalogues opcode minimum targets.
* `crates/stela-compiler/src/error.rs:17-110` — `InstructionContext { address, encoding }`, `CompileError { kind, instruction }`, `CompileErrorKind` (~100 variants) and `From<UnsupportedReason>` preserving source context. `lib.rs:39` `UnsupportedReason` distinguishes `HostCapabilityRequired`, `NumericalRuntimeRequired`, `Instruction`, `PrivilegedOperation`, `PointerAuthentication`, unresolved targets, `RuntimeGeneratedCode`.

### 1.4 Runtime contract, provenance and testing

* `crates/stela-compiler/src/state.rs:81` — `#[repr(C)] GuestState` (x[31], sp, nzcv, v[32][2], fpcr/fpsr, exclusive state, daif/pending, anchors, limits, exit stack, account). `state.rs:204` (`layout_matches_native_abi`) asserts `size_of == 920`, exact `offset_of!` values and ABI string prefix `x86_64-sysv-guest-state-v15-`. Host-only fields (`Anchors`, `ExitStack`, `Limit`) deliberately implement `PartialEq` as "any two compare equal" so they cannot leak into guest comparisons.
* `crates/stela-compiler/src/context.rs:28` — `ExecutionContext { version, bytes, memory, scalable, system }`, `EXECUTION_CONTEXT_VERSION`, `has_supported_header()`; `ScalableStateView` layout similarly asserted.
* `crates/stela-compiler/build.rs` — fingerprints all non-test compiler sources plus the locked `rustc_apfloat` version into `STELA_COMPILER_SOURCE_SHA256`.
* `crates/stela-dev/src/compile.rs:231-243` — AOT manifest: `schema_version`, `kind`, `target_cpu`, `source_bytes`, `source_sha256`, `abi` (from `BlockProfile::abi()`), `native_sha256`, `native_bytes`, `source_map`, `compiler_source_sha256`, and **`"verified": false`** by default. `:315-329` emits the same for leaf preparation.
* `crates/stela-compiler/src/x86_64/modeled.rs:1-10` — test-only SIGILL model of host instructions the machine lacks, with a test that each model matches the real instruction wherever the host has it (Intel SDE in CI). Linux-specific; the *pattern* is portable, the code is not.

---

## 2. GameReComp counterpart (for comparison)

| Concern | GameReComp location | Notes |
|---|---|---|
| Lifter | `tools/lift/lift32.py:265 class Lifter`, `:757 lift_instruction`, `:769 _lift_instruction` | Capstone mnemonics → C statements; `FUNCTION_LOCALS` (`:239`) is the emitter/runtime local-variable contract; `Lifter(...)` options include `precise_sbb`, `precise_carry`, `patch_sites`, `reloc`, `dos`, `call_pop`, `computed_ret`, `iat_map`, `lifted` |
| Flag model | `lift32.py:561 _flag_capture`, `:604 _make_condition`; `runtime/recomp32/recomp_types.h:393-700` (`FK_*`, `CC_*`, `recomp_cond`, `recomp_cond_cf`, `recomp_carry`, `recomp_eflags`) | Lazy kind + two left-aligned operands + `_cf`; handles narrow widths via `FK_NARROW`; more sophisticated than Stela's flag work for x86 guest semantics |
| Function emission | `tools/lift/generate.py:44 LinearInstruction`, `:246 lift_function_linear` | Labels, join points, gap handling, EH resume labels; C text with `/* 0xVA: ... */` comments |
| CFG / functions | `tools/disasm/disasm32.py:119 Instruction`, `:176 BasicBlock`, `:189 Function`, `:260 Disassembler`; `tools/lift/eh32.py` | `entry_kind` distinguishes real starts from aliases; EH aux targets |
| Runtime state | `runtime/recomp32/recomp_types.h:43-160` | globals, local-register mode, x87/MMX/SSE aliases, `LO8/HI8/LO16` masks, `ADDR`/`MEM*`, `FS_BASE`, etc. |
| Verification | `tools/lift/difftest.py:81 Case`, `tools/lift/multilifter.py` | Unicorn reference, named flags, `undef`/`known` divergence annotations; fail-closed `PROMOTE/FAILED/BLOCKED` selection |
| Provenance | `tools/autorecomp/pipeline.py:1212` (`source_digest_sha256` in `manifest.json`), `docs/SOURCE_PROVENANCE.md`, `tools/acceptance_schema.json` | Already has manifest + ledger discipline; Stela's manifest ideas should extend this, not duplicate it |

---

## 3. Licence and dependency record

### Stela
* Workspace licence **`GPL-2.0-or-later`** (`Cargo.toml [workspace.package]`, `LICENSE`, `NOTICE`).
* **`crates/stela-compiler/Cargo.toml`: `license = "GPL-2.0-or-later AND MIT"`** — the MIT alternative applies to the compiler crate itself. `stela-fingerprint`, `stela-dev`, `stela-formats` etc. are workspace-licensed (`GPL-2.0-or-later`); the shipped CLI binary is distributed under GPL-3.0-or-later (`crates/stela-cli/Cargo.toml` comment, `NOTICE`). `third_party/{linux,llvm,mesa,xnu,libdrm,mbedtls,smoltcp}` carry their own terms; none are needed for the translation ideas in this report.
* Compiler dependencies: `serde` (no_std, `alloc`,`derive`), **`rustc_apfloat = "=0.2.3"`** (locked `0.2.3+llvm-462a31f5a5ab`, deps `bitflags`, `smallvec` in `Cargo.lock:1812`), build-dependency `stela-fingerprint` (uses `sha2`). Edition 2024, `rust-version = "1.99"`, `#![no_std]`.
* Dev tooling: `stela-dev` (`anyhow`, `clap`), `xtask`; LLVM/Clang needed only for the full product build.

### GameReComp
* Repo licence **`AGPL-3.0-only`** (`Cargo.toml`, `LICENSE`, `NOTICE`); `tools/lift/*` and `runtime/recomp32/recomp_types.h` have no per-file SPDX header, so repo default applies. Some earlier files retain an MIT notice (`LICENSES/GameReComp-prior-MIT.txt`).
* Python tooling imports **Capstone** (`capstone`, `capstone.x86`) and, for `difftest.py`, **Unicorn** (`unicorn`, `unicorn.x86_const`) plus an external C compiler (`$RECOMP_CC` or gcc/clang/cc). `recomp_types.h` is portable C (MSVC and GCC/Clang, with non-x86 `__rdtsc` fallback using `clock_gettime`).

### Licence-safe boundary
Do **not** copy Stela source text. The compiler crate's MIT option plus the "ideas not expression" rule make architectural patterns safe to reimplement, but the workspace default is GPL-2.0-or-later and the shipped binaries GPL-2.0-only/GPL-3.0-or-later, while GameReComp is AGPL-3.0-only. Reimplement patterns independently in Python/C, cite the concept, and keep GameReComp's own notices.

---

## 4. Portable techniques (architecture-independent patterns)

1. **Typing the semantic boundary.** Decode into an explicit, typed operation ("architectural effects, not syntax") before any emission. Stela: `Lifter::Instruction`, `Operation`, `Instruction`. Portable benefit: one place to define unsupported semantics, reusable for multiple back ends and for static analysis.
2. **Structured failure with source context and fail-closed defaults.** `UnsupportedReason` distinguishes *why* a translation is impossible and carries guest address/encoding; `CompileError` attaches instruction context. The compiler has no "emit something plausible" path. Portable to a Python lifter as a `LiftError(kind, va, raw_bytes, module)`.
3. **Versioned ABI + layout assertions.** `ExecutionContext.version/bytes`, `GuestState` `offset_of!` tests, `BlockProfile::abi()` strings embedded in manifests. Portable to `recomp_types.h` via a version constant and `_Static_assert`s on type sizes/offsets where applicable.
4. **Provenance manifest binding inputs → outputs → verification.** Stela binds source SHA, native SHA, target CPU, ABI, compiler-source SHA and `verified:false`. Portable: extend GameReComp's existing `manifest.json` with lifter option IDs, input PE digest, generated-source digests and explicit verification status.
5. **Capability/profile validation before emission.** `Options::validate`, `validate_target`, `TargetCpu` features and `BlockProfile` features stop unsupported combinations at planning time, not at runtime or when the generated C is compiled.
6. **Source maps, relocation records and emission-site metadata.** `NativeBlock.source_map`, `relocations`, `EmissionSite { start, end, site, source }`, `FaultRecovery` with validated bindings. Portable analogue: a machine-readable guest-VA → generated file/line map plus lists of absolute guest VAs/patch sites emitted into each source unit.
7. **CFG edge taxonomy shared by translator and runtime.** `StaticSuccessor` (direct / conditional target / fallthrough / call target / call continuation) vs `DynamicEdge` (branch/call/return by register). Call continuations are "candidates". Portable: use this taxonomy to drive GameReComp's EH resume and alias-label decisions explicitly.
8. **Reference-model differential testing with named observables and exhaustive small-domain enumeration.** Stela exhaustively checks its flag-conversion tables over all inputs and checks software FP against hardware via `rustc_apfloat`. GameReComp already has the stronger architecture-level version (Unicorn + named flags + `known` divergences); the portable addition is *exhaustive enumeration of algebraic helpers* and *software oracles independent of the host*.
9. **Single source of truth for emitter contracts.** Stela's `asm` derives encodings from typed operands; GameReComp's `FUNCTION_LOCALS` already does this for per-function locals. Generalise the pattern: any list the emitter relies on (locals, globals, macros, option flags) should be declared once and consumed by drivers, not hand-copied.
10. **Separating guest feature declarations from host capabilities.** `NumericalFeatures` (guest) vs `TargetCpu` (host) prevents translating guest semantics into "whatever this host does". For GameReComp this maps to separating x87/SSE control-word semantics from the compiler target's native FP behaviour.

---

## 5. Top adaptation candidates for GameReComp (ranked)

All are *reimplementations of patterns* in GameReComp's own Python/C, not code imports.

| # | Candidate | Stela pattern | GameReComp touch points | Value / cost / risk |
|---|---|---|---|---|
| 1 | **Central `LiftError` with guest source context; fail closed on unknown mnemonics/operands** | `UnsupportedReason`, `CompileError`, `InstructionContext` | `tools/lift/lift32.py` (`_lift_instruction`, `_fmt_read`, `_fmt_write`), `lift16.py`, `lift32_sse.py`, `generate.py`, `runtime/recomp32/recomp_types.h` (the `CMP_O`/`CMP_P` TODOs); feed `multilifter.py` `BLOCKED` | **High / low-med.** Replaces silent `???`, `/* flag from ... */` and `CMP_O = 0`/`CMP_P = 0` TODOs with reportable causes. Keep a small explicit allowlist of known imprecisions in `difftest.Case.known`. |
| 2 | **Validated `LiftOptions` with a stable option ID included in reports and generated manifests** | `Options`, `Options::validate`, `TargetCpu::id`, `BlockProfile::abi` | `Lifter.__init__` options in `lift32.py`, difftest JSON, `tools/autorecomp/pipeline.py` `manifest.json` | **High / low.** Makes `precise_sbb`/`precise_carry`/`dos`/`computed_ret` combinations reproducible; multilifter reports then name *which* configuration was promoted. |
| 3 | **ABI version + compile-time layout/contract assertions in `recomp_types.h`** | `EXECUTION_CONTEXT_VERSION`, `has_supported_header`, `offset_of!` tests | `runtime/recomp32/recomp_types.h` (register globals, `g_flag_*`, `g_st`, `g_mm`, `g_xmm`, `RECOMP_*` macros); generated unit prologue in `generate.py` | **High / low.** Detects drift when a lifter starts relying on a new global/local or a 64-bit host changes layout; record the version in the manifest. |
| 4 | **Machine-readable source map and relocation/patch-site ledger for generated C** | `NativeBlock.source_map`, `relocations`, `EmissionSite.source` | `tools/lift/generate.py:246 lift_function_linear` already emits `/* 0xVA */` comments; extend its manifest with VA → file/line, `patch_sites`, relocated immediates/displacements | **High / med.** Improves crash triage, EH resume accuracy and rebase safety; reuses existing `Lifter.reloc` and `patch_sites` rather than adding new semantics. |
| 5 | **Exhaustive helper-table tests and host-independent FP oracle in `difftest.py`** | `x86_64/flags.rs` exhaustive table test; `aarch64/floating.rs` software model | `tools/lift/difftest.py` `Case`, `runtime/recomp32/recomp_types.h` `recomp_cond`/`recomp_eflags`/x87 helpers | **Med / low-med.** Keep Unicorn as the primary oracle; add a Python `fractions`/`Decimal` model for x87 precision/rounding edge cases so results do not depend on the host FPU. Do **not** switch generated C to software FP. |
| 6 | **Typed semantic op dataclasses between Capstone and the C string emitter** | `aarch64::Operation` + typed operand enums | new `tools/lift/ops.py`; `lift32.py`, `lift16.py`, `lift32_cpu.py` consume/produce it | **Med / high.** Enables shared semantics across lifters and static flag/edge analysis. Do it **incrementally** (new families only); the existing string emitter is battle-tested. |
| 7 | **Explicit CFG edge taxonomy for EH and alias handling** | `StaticSuccessor`, `DynamicEdge`, "call continuation is a candidate" | `tools/disasm/disasm32.py` `BasicBlock`/`Function`, `tools/lift/eh32.py`, `generate.py` labels/join points | **Med / med.** Makes resume-point and alias-label decisions explicit and testable rather than implicit in label-all heuristics. |
| 8 | **Manifest-level verification status** (`verified:false` until difftest/multilifter evidence exists) | Stela AOT manifest | existing `tools/autorecomp/pipeline.py` manifest, `docs/SOURCE_PROVENANCE.md` categories, `tools/acceptance_schema.json` | **Med / low.** GameReComp already has provenance categories; add the explicit "generated ≠ verified" flag to the per-function/per-unit record so evidence cannot be inferred from generated text. |

### Explicitly *do not* adapt
* Stela's `x86_64/asm.rs` typed binary encoder, REX/VEX/ModRM/SIB/prefix derivation, APX/AMX/SVE/SME/PAC support — all specific to emitting x86-64 machine code for an AArch64 guest.
* `aarch64/` decoders, `GuestState` NZCV field layout, `flags.rs` AXFLAG/XAFLAG tables — wrong guest ISA.
* `x86_64/modeled.rs` SIGILL handler — Linux host-specific test machinery (the *idea* of a software model for missing host instructions is portable; the implementation is not).
* Any Stela source text: see licence boundary above.

---

## 6. Confidence and limitations

* Static source inspection only; the two snapshots were not built, so no claim is made about runtime behaviour of either project.
* The Stela pin hash is taken from the task statement; the extracted tree contains no `.git`, so it could not be verified from the snapshot. File contents correspond to README "Stela v0.5.0", Rust 2024/1.99 toolchain.
* Line numbers are from the pinned snapshots and may drift in later revisions.
