# S8 audit — AOT/JIT techniques, native execution and runtime performance (threading, scheduling, emitted-code invocation)

**Scope (read-only):** Stela snapshot pinned `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe`
(`/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/stela`) versus GameReComp
(`/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/integration`).
No repository was edited. Task focus: Stela's AOT/JIT + native execution/runtime-performance
mechanisms, compared with GameReComp's native compile path (`tools/autorecomp/pipeline.py`) and
runtime. Related sibling audits: S4 blocks/cache, S5 codegen, S9 benchmarks.

---

## 1. Executive summary

Stela is a **dynamic binary translator with both AOT and JIT** plus a full guest-OS runtime:

* ARM64e → x86-64 translation is a **hand-written x86 emitter** (`stela-compiler`, `no_std`,
  "unsupported instructions are compile errors; there is no interpreter fallback",
  `crates/stela-compiler/src/lib.rs:1-3`). There is no LLVM IR and no interpretation path.
* Code is specialized to the host CPU via CPUID/XGETBV feature detection (`TargetCpu`), and
  every generated artifact is bound by a content hash to source bytes, compiler-source hash,
  guest base, ABI, target CPU and memory-specialization hash. A **disk cache** publishes with
  single-flight coordination and atomic rename; an **in-memory JIT cache** installs blocks
  with an ITLB-like lookup table and patches direct exits into successor code so a dispatcher
  runs once per chain of up to 4096 blocks.
* Emitted code is invoked through several explicit System-V (`extern "sysv64"`) ABIs from an
  owned RW→RX `mmap` mapping; a hand-written asm trampoline stores the exit RSP and jumps to
  generated code.
* Execution is **SMP**: each guest PE runs its own `Machine` on its own host core (AP), sharing
  guest RAM directly; a `Hub` carries SEV/TLB/IC broadcasts, a `Kick` interrupt pulls a core
  out of native code, and serial/round-robin schedulers with measured time slices cover
  single-core mode. Idle PEs opportunistically AOT-compile successor blocks in the background.

GameReComp is a **static AOT recompiler** with no runtime translation at all:

* `tools/autorecomp/pipeline.py` is an 8-stage fail-closed orchestrator
  (`inspect → recover → translate → project → compile → run → verify → report`).
  `tools/lift/translator.py` lifts each x86 PE32 function to **register-transfer C**, split into
  byte-capped units plus a dispatch table; `stage_compile` runs `cmake --build --parallel` (or a
  `ThreadPoolExecutor` for wasm32) and `stage_run` executes the resulting standalone native
  binary.
* The runtime (`runtime/`) is C HLE/shim code: a PE loader, a Win32/DirectX shim layer, and
  guest threads mapped to host `pthread`s (`CreateThread`/`_beginthreadex`/`SDL_CreateThread`
  → `pthread_create`). There is **no JIT, no runtime code emission, no code cache, no guest-CPU
  scheduler**: the compiled game *is* the native code ("code, not mapped — we ARE the code",
  `runtime/recomp32/main.c:31-34`), and the only "threading" is host threads for guest threads.
* Performance effort in GameReComp is concentrated on **build-time** concerns: adaptive compile
  deadlines, byte-capped translation units, `--parallel` builds, source-digest-verified build
  reuse, and embedded PE images. It has no analogue of Stela's block chaining, CPU-feature
  specialization, single-flight cache publication, or idle-core precompilation.

**Bottom line for adaptation:** the transferable items are *design patterns* — content-addressed
artifact identity, single-flight + atomic publication, compiler-fingerprint binding, host-target
specialization, build parallelism/deadlines (already partly present), idle-time speculative
AOT, and safe code-patching discipline. Direct source reuse is **licence-gated** (§7 and S4 §7):
Stela is GPL-2.0-or-later (with GPL-2.0-only parts), GameReComp is AGPL-3.0-only; reimplement
ideas rather than copy code, and avoid GPL-2.0-only components entirely.

---

## 2. Sources, licence, dependencies, portability

| | **Stela** (source of techniques) | **GameReComp** (comparison target) |
|---|---|---|
| Pin / checkout | `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe`; workspace files dated 2025-11-20 | integration snapshot; files dated 2026-11-23 |
| Language / edition | Rust 2024 (`edition = "2024"`, `rust-version = "1.99"`, `Cargo.toml` [workspace.package]); hand-written x86-64 asm via `global_asm!`/`naked_asm!`; some C (`stela-linux/compat/kernel/*.c`) | Python 3.10+ orchestration (`tools/`), C/C++ runtime (`runtime/`), Rust 2021 core CLI/GUI (`Cargo.toml`) |
| Licence | Repo **GPL-2.0-or-later** (`LICENSE`, `NOTICE`); `stela-compiler` = `GPL-2.0-or-later AND MIT`; `stela-engine` = `GPL-2.0-or-later AND BSD-3-Clause`; shipped `stela.efi` = **GPL-2.0-only** (Linux kernel ports); CLI = GPL-3.0-or-later | Core **AGPL-3.0-only** (`Cargo.toml`, root `LICENSE` = AGPL-3.0); `NOTICE` + `LICENSES/` for vendored notices |
| Key crates / dirs for this audit | `crates/stela-compiler` (emitter, `TargetCpu`, `block`, `x86_64::chain`, `block_entry`), `crates/stela-engine` (`dispatch`, `execution`, `machine/engine/*`, `artifact`, `cache_flight`, `translations`, `blocks/aot`), `crates/stela-boot-core` (`fiber.rs`, `doorbell.rs`), `crates/stela-boot/src/engine/parallel.rs`, `crates/stela-cli/src/setup/translations.rs` | `tools/autorecomp/pipeline.py`, `tools/autorecomp/projectgen.py`, `tools/lift/translator.py`, `runtime/win32hle/*`, `runtime/recomp32/*`, `runtime/hybrid/*`, `docs/ARCHITECTURE.md`, `docs/HYBRID.md` |
| Build deps | Rust 1.99+, `cargo xtask`, LLVM 21/22/23 with Clang/LLD/libclang, Python 3.10+, CMake; vendored `third_party/{linux,llvm,mesa,mbedtls,xnu,libdrm,smoltcp}`; crates `serde`, `serde_json`, `libc`, `tempfile`, `rustc_apfloat`, `miniz_oxide`, `sha2` | Rust 1.85+, Python 3.10+, CMake, Clang/GCC, SDL2, optional FFmpeg/Emscripten/wasm-ld/z3, Unicorn, PCRecomp/Capstone, rev.ng (optional) |
| Host portability | Codegen targets **x86-64 host only** at runtime (`translated_callee!` = `extern "sysv64"` on x86-64, `extern "C"` elsewhere; `execution/platform/posix.rs` for macOS/Linux x86-64; `freestanding.rs` is UEFI/no-std); other arches can compile types but "never run it" (`stela-engine/src/lib.rs:12-28`) | Host native builds for Linux/macOS (and WASM32 via clang/wasm-ld); input is strictly PE32/i386, PE32+ fail-closed (`pipeline.py:327-331`); no runtime arch translation |
| Where "runtime" lives | `stela-engine` (execution core) + `stela-boot` (bare-metal SMP host) + `stela-boot-core` (fiber/doorbell/TSC); **`stela-runtime` is macOS restore/install (ASR/TSS/FDR), not the emulation runtime** | `runtime/` C tree: `win32hle` (HLE shims + PE loader), `recomp32` (register model/main), `hybrid` (lifted↔real boundary), `recomp16`/`dos32`/`nextstep`/`wasm32` variants |

---

## 3. Stela — AOT/JIT techniques

### 3.1 Compiler front end (`stela-compiler`)

| Item | Location | Detail |
|---|---|---|
| `compile(source, base, options)` | `crates/stela-compiler/src/lib.rs:218-224` | Calls `CompilationPlan::new(...).compile()`. One validated decode, then emitters selected by profile. |
| `Options { profile, target, scalable, numerical, checks, native }` | `lib.rs:91-210` | `with_scalable`, `with_numerical_features`, `with_memory_checks`, `with_native_system`; `validate()` refuses options no emitter implements. |
| `SOURCE_SHA256` | `lib.rs:32-33` | `env!("STELA_COMPILER_SOURCE_SHA256")` — build-time fingerprint of compiler sources, bound into every cache identity. |
| `NativeBlock { x86_64_code, source_map, relocations }` | `lib.rs:74-86` | `source_map` = `(GuestAddress, native_offset)` pairs for faults/debug; `relocations` are 8-byte fields holding guest addresses, so code can be moved by adding page displacement. |
| `Lifter`, `UnsupportedReason` | `lib.rs:38-72` | Unsupported semantics (host capability, numeric runtime, instruction, privileged, PAC, unresolved target, runtime-generated code) are compile errors — **no interpreter fallback**. |
| `CompilationPlan` / `Stage` | `block.rs:331-410` | Stages: `Block`, `Leaf`, `ContextLeaf`, `ContextBlock`; `new_admitted_system()` applies a word-admission policy; `source_bytes()` at :518; `compile()` at :567. |
| `CompiledBlock` | `block.rs:217-240` | `native`, `source_bytes`, `direct_exits: Vec<chain::DirectExit>`, `fault_recovery`, `self_loop`, `region`, `cold` (out-of-line cold-code range for profiling). |
| `BlockExit { next_pc, kind }` | `block.rs:210-215` | `#[repr(C)]` struct returned in RAX/RDX; `ExitKind` tags at `block.rs:60-113` (Fallthrough/Direct/Indirect/Call/Return/Trap/PAC/Clock/ThreadRegister/FloatingPoint/ScalableNumerical/System/Amx). |
| Boundaries | `block.rs:57-58` | `MAX_BLOCK_INSTRUCTIONS = 256`; engine `MAX_BLOCK_BYTES = 1024` (`block_inputs.rs`). |

### 3.2 Host-CPU specialization (`stela-compiler/src/target.rs`)

| Item | Location | Detail |
|---|---|---|
| `TargetCpu { tier: SseTier, features: u64 }` | `target.rs:19-33` | SSE tiers Baseline→Ssse3→Sse41→Sse42; ~45 independent feature bits. |
| `HostCpuid::read()` | `target.rs:71-127` | CPUID leaves 0/1/7(.0,.1)/0x24/0x29/0x8000_0001 + `_xgetbv(0)`; `avx512_on_demand` for xnu. |
| `features!` registry | `target.rs:216-391` | Canonical ordered list: Pclmulqdq, Fma, F16c, Popcnt, Bmi1/2, Aes, Sha, AtomicLoad16, Cx8/Cx16, Avx2, Avx512{f,vl,bw,cd,dq,vpopcntdq,bitalg,vbmi,vnni,bf16,fp16}, Avx-VNNI variants, Avx10.2, Vaes, Vpclmulqdq, Sha512/Sm3/Sm4, Apx/ApxNdd, LineAtomic, Doit, DIT mode. Each has a CPUID probe; vendor-specific memory-ordering rules (`Vendor`, `lines 170-203`). |
| `TargetCpu::detect()` / `probe()` | `target.rs:581-602` | `OnceLock` cached detection; `darwin_on_demand_avx512f()` uses `sysctlbyname` (:626+). |
| `TargetCpu::id()` | `target.rs:603-618` | Canonical string `"x86_64-baseline+ssse3+...+avx512f+..."` used as cache identity. |
| `X86_64_V2` | `target.rs:452-454` | `Sse42 + Popcnt + Cx8 + Cx16` baseline tier, analogous to GameReComp's host requirement. |

This is a strong contrast to GameReComp: GameReComp compiles C with host `clang`/`gcc` flags and
targets x86-64/-wasm32 generically; Stela **specializes emitted instructions to the exact host
CPU** and keys persistent artifacts on that target id.

### 3.3 Persistent AOT cache (`stela-engine`)

| Item | Location | Detail |
|---|---|---|
| `prepare_leaf`, `prepare_leaf_for_target` | `engine/src/lib.rs:148-175` | Identity = `sha256(source)`, compiler-source hash, guest base, ABI (`Features::LEAF`), target id; `compile()` then `block.native`. |
| `prepare_block`, `prepare_block_with_profile`, `prepare_block_for_target`, `prepare_block_plan` | `lib.rs:183-247` | `CompilationPlan` decodes consumed prefix; identity over prefix hash + full typed profile + memory specialization. "Profile-tagged shared AOT/JIT preparation"; PAC keys stay outside artifacts. |
| `prepare(...)` | `lib.rs:273-356` | The core **single-flight, atomic-publication** cache: key = SHA-256 of identity JSON; directory `<root>/<key>`; validate manifest + checksum + native hash; `cache_flight::Flight::join`, compile into `tempfile::tempdir_in`, write `native.bin`, `manifest.json`, `manifest.sha256.json`, then `std::fs::rename` publish. Another publisher winning the race is re-validated. Bounded reads (`bounded_file`, :77-89). |
| `cache_flight::Flight` | `engine/src/cache_flight.rs:1-65` | `static FLIGHTS: Mutex<BTreeMap<Arc<Path>, Arc<Mutex<()>>>>`; registry lock covers membership only, so unrelated keys compile/publish in parallel; `Drop` removes last participant. Go-`singleflight`-style. |
| `Identity`, `Manifest`, `validate*` | `engine/src/artifact.rs`, `artifact/prepare.rs:6-85` | `MAX_CACHE_NATIVE_BYTES = 64 MiB`, `MAX_CACHE_MANIFEST_BYTES = 64 MiB`; `validate_manifest_bytes`, `validate_preparation_metadata/native/claim`; checksums kept separate from verification claims (manifest `verified` must be false). |
| `BlockRuntime::new_aot_for_target`, `preload_artifact` | `blocks/aot.rs:82-120` | Strict AOT: a missing body is refused before any disk-cache access or code generation; `from_artifact` maps bytes and calls `serialize_instruction_execution()`. `ArtifactPreparation::{FileCache, AheadOfTime, InMemory}`. |
| `Translations` / pack format | `engine/src/translations/*`, `stela-runtime/src/translation_store.rs` | `STLTPACK` packs keyed by translator digest; header + index (32-byte entries) + checksum; `within()`/`trim()` let a boot with limited RAM read only records that fit a budget. |
| USB-store setup | `stela-cli/src/setup/translations.rs:1-72` | `stela/store/{store.cfg,XXXXXXXX.pak,spare.bin,pipeline.bin}` on FAT32; 8.3 names; bounded `MIN_PACK_BYTES = 64 MiB`, `MAX_PACK_BYTES ≈ 4 GiB - 1 MiB`, one pack kept; firmware rewrites its pack in place. |

### 3.4 Just-in-time / in-memory cache (`stela-engine/src/machine/engine`)

| Item | Location | Detail |
|---|---|---|
| Dispatch loop | `engine.rs:1-3` | "The dispatch loop: fetch translation, block cache, native execution and every exit back from native code." |
| Cache constants | `engine.rs:187-215` | `FIRST_WINDOW_BYTES = 32*4`, `FETCH_CACHE = 1024`, `FETCHED_LEAVES = 1<<21`, `SCANNED_LEAVES = 1<<16`, `JUMP_CACHE = 4096`, `CHAIN_LIMIT = 4096`, `MEMORY_RELEASE_BLOCKS = 4096`. |
| `fetch_block(pc, single)` | `compilation.rs:78+` | Fast path: `code.table.find(slot(pc,key),...)` → `Fetched::Block` with `link_direct_exit()` and `entered()`; miss walks guest page tables, then `known_variant` reuses a block compiled under the same physical frame/context. |
| Block table growth / grace | `compilation.rs:27-75` | `insert_target`, `grow_table` with `board::Grace` snapshot and `release_retired()` — RCU-style safe reclamation across PEs. |
| Chaining | `x86_64/chain.rs:1-82` | Direct exits patched with one atomic aligned 8-byte store (`PATCH_BYTES = 5` `jmp rel32`); `ChainControl` budget in host register, interrupt-epoch poll per link; `LookupTable` ITLB keyed by `(frame, fetch state, context)`, two entries per 64-byte set; paired call/return uses host `ret` for CPU return prediction; `LINK_PAGE = 4096` for link-free range; `THUNK_BYTES`, `POLLED_LINK_BODY_BYTES` sized. |
| Code-cache invalidation | `code_cache.rs:1-36` | `stale_code()` sweep (hash admitted bytes under board lock, `STALE_STEP = 128`); `grow_table`, eviction/discard in `EVICT_STEP = 32`, `STEP_VISITS = 128`, `YIELD_VISITS = 16` (bounded lock holds measured in microseconds). |
| Background AOT | `ahead.rs:1-40` | Idle PEs prepare static successors of a fetched/installed block; `DEPTH = 1` (measured: 89% of blocks that ran are ≤1 step away), `CAPACITY = 4096` same-page + `ELSEWHERE_CAPACITY = 1024`, `SEEN = 1<<14` filter; results enter the shared store after byte-hash/PC/context checks. |
| Ahead "model" | `engine/ahead/model.rs`, `stela-dev/src/compile.rs:231,316` | Offline AOT manifests `raw_arm64_block_aot` / `raw_arm64_leaf_aot` with target id and compiler hash; throughput microbenchmarks in `stela-dev/src/compile/throughput.rs`. |

---

## 4. Stela — native execution and emitted-code invocation

### 4.1 Loading a leaf for execution (`stela-engine/src/execution.rs`)

* `ExecutableLeaf` (:36-43) owns an immutable RX `platform::Mapping` + `ResolvedLeaf` and is
  deliberately **neither Send nor Sync** (`PhantomData<Rc<()>>`).
* `ExecutableLeaf::load(...)` (:53-113) re-checks dispatcher currency, translation identity,
  schema version 1, compiler-source hash, LEAF ABI, host admission (`platform::cpu::admitted`),
  `!manifest.verified`, and `0 < native_bytes <= MAX_NATIVE_BYTES (64 MiB)`; reads `native.bin`
  and verifies `sha256(bytes) == manifest.native_sha256` before mapping.
* `ExecutableLeaf::execute(...)` (:141-153) checks currency again immediately before
  `mapping.call(state)`. The safety contract (:127-140) spells out direct-RAM, non-aliasing,
  finite-return, no-signal-handler assumptions — a guest fault can terminate the host process;
  "this API does not install a signal handler or sandbox untrusted code".
* `reference.rs` (:357-364, Linux x86-64 test feature) provides bounded native fault capture for
  hardware-reference validation.

### 4.2 Code mapping and call ABIs (`execution/platform/posix.rs`)

| Function | Line | Detail |
|---|---|---|
| `ensure_supported()` | 8-12 | Every x86-64 has integer/SSE2 baseline; macOS/Linux use System-V C ABI. |
| `Mapping::new(bytes)` | 21-68 | `mmap(PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANON)` → `copy_nonoverlapping` → `mprotect(PROT_READ|PROT_EXEC)`; never RWX; `Drop` calls `munmap`. |
| `Mapping::call(state)` | 83-92 | Leaf ABI via `NativeImage::borrow(self).call(state)`. |
| `Mapping::call_context_block` | 101-113 | Context-block struct-return ABI, returns `BlockExit`. |
| `Mapping::call_context` | 122-136 | Two-arg context-leaf ABI, direct `transmute` to `unsafe extern "sysv64" fn(*mut GuestState, *mut ExecutionContext)`. |
| `Mapping::call_budgeted_block` | 166-179 | Resident-budget loop ABI with `LoopBudget` (loops stay inside generated code). |
| `Mapping::call_block` / `call_block_observed` | 184-214 | Ordinary block ABI via `stela_compiler::x86_64::block_entry::enter`. |
| `Mapping::call_amx_kernel(offset, ...)` | 220-241 | Calls a kernel inside an AMX library image. |
| `unsafe impl ExecutableStorage for Mapping` | 254-262 | `entry()` + `revalidate()`; `Error = Infallible`. |

`execution/native_image.rs` is the generic synchronous-call layer:

* `ExecutableStorage` (:14-18) — "implemented only by the POSIX RX mapping and the boot-frame
  owner"; lifetime-borrowed `NativeImage<'rx, S>` has **no raw-pointer constructor** (:46-53).
* `ReachedCall<R,E>` (:23-44) keeps the native result alongside the post-call storage check;
  `into_infallible()` for POSIX.
* `call`/`call_context_block`/`call_budgeted_block`/`call_block`/`call_amx_kernel` (:63-179) each
  `revalidate()` before and after, `transmute` the entry to the right `extern "sysv64"` signature,
  and call; `exit(u128)` (:182-188) decodes `BlockExit { next_pc = low 64, kind = high 64 }`.
* `block_entry.rs` (`crates/stela-compiler/src/x86_64/block_entry.rs:1-39`) is a hand-written
  `global_asm!` trampoline: `mov [rdi + EXIT_RSP_OFFSET], rsp; jmp rcx`, returning `u128`.
  `enter(code, state, first, second)` passes RDI=state, RSI/RDX=first/second.
* `stela-engine/src/lib.rs:15-28` (`translated_callee!` macro) declares all runtime callbacks
  emitted code calls as `extern "sysv64"` on x86-64 (fallback `extern "C"` elsewhere).
* `execution/platform/freestanding.rs:1-78` — the UEFI/no-std build **refuses every native call**
  ("the freestanding build maps no native code"); boot code therefore executes translations by
  other means/is limited, and AOT artifacts are consumed through `blocks/aot.rs` `InMemory`.

---

## 5. Stela — runtime performance: threading, scheduling, emitted-code invocation

### 5.1 Serial engine loop (`machine/engine/scheduler.rs`)

* Module doc (:1-2): "The serial run loop: device polling, stepping, native execution and block
  linking."
* `Machine::run(budget)`, `run_with_budget(RunBudget { blocks, host_ticks })`, `run_blocks`,
  `run_blocks_with_budget` (:28-134) — yield between engine steps, honor block and host-time
  deadlines, call `hub.pass(current)` each iteration, charge `step_timing` phases, and use
  `direct_next` to skip full steps after System exits (`DIRECT_RUNS = 8`).
* `PROMOTION_RUNS = 1024`, `HOT_SAMPLES = 2`, `SAMPLED_EXITS = 4096` (:6-16) — sampled cell
  promotion for hot exits (~1 lock + a few lookups per 1024 native runs).
* Budget/overrun semantics: an entered native chain or host callback may overrun the budget
  (:36-37); `CHAIN_LIMIT = 4096` bounds how late a deadline is observed (`engine.rs:206-211`).

### 5.2 Guest-PE scheduling (`machine/engine/processors.rs`)

* `QUANTUM_HZ = 500` (2 ms slice, so 7 peers hold a preempted spinlock holder ≤14 ms),
  `IDLE_POLL_HZ = 5000` (200 µs haltpoll, Linux `guest_halt_poll_ns`), `PHYSICAL_CLUSTER_MASK`
  (:60-71).
* `Peer::{Running, Off, Parked, Remote}` (:73-81); `schedule()` / `schedule_with_deadline()`
  (:282-336) — round-robin from the running PE, time slice/device/timer/deadline checks,
  `clock.end_slice`, spin-vs-idle accounting, host-counter deadlines, and returns to host on
  wait limits.

### 5.3 SMP / host threading (`machine/engine/parallel.rs`, `stela-boot/src/engine/parallel.rs`)

* `parallel.rs` module doc (:1-27): every PE runs its own `Machine` on its own host core; shared
  guest RAM is reached **directly by native code**; `SharedDevices` + `Hub` carry start/SEV/TLB/IC
  broadcast; a broadcaster waits for peers to apply maintenance; exclusives use one host CAS
  (`Host::compare_exchange_ram`); canonical-memory device access is forwarded to the home
  machine, which stops other PEs.
* `Kick` trait (:61-115) — host interrupt/doorbell to bring a core out of native code or a wait;
  `ticks`, `count`, `released`, `release_due`, `release_inline`, `signal_due`, `signal_step` for
  host work handoff.
* `Hub` (:651+): `interrupt` (:928), `wake` (:938), `wake_serving` (:969), `call_host` (:1125),
  `pass` (:1623), `serve_host` (:2161), shared clock; `idle_poll_after` (:123-131) implements
  Linux haltpoll-style adaptive polling (no poll on MWAIT doorbell hosts).
* Boot host integration: `stela-boot/src/engine/parallel.rs::spawn` (:106-143) binds each PE to an
  AP and calls `processor.spawn(Box::new(move |owner| pe.run(owner)), sender, tsc_hz)`;
  `Doorbells: Kick` (:32-93) rings an APIC doorbell/MWAIT or sends an IPI; output reports
  "PARALLEL n PEs, each on its own host core".
* Alternative cooperative host work: `stela-boot-core/src/fiber.rs` — `Fiber`, `Handle`,
  `Fiber::resume` (:118), `Handle::suspend` (:180), `unsafe extern "win64" fn switch`
  (:189-222) using `naked_asm!` + `fxsave64`/`fxrstor64` to preserve x87/SSE state across stack
  switches; used to split long host work (e.g. shader compilation) into bounded steps.
* `shared_memory/scheduler.rs` (:1-120) is a **candidate**, not admitted, scheduler for "checked
  native rounds with stopped, owner-local source services" (`run_serialized_with_budget` at
  :235); notable as design exploration, not a shipping path.

### 5.4 Profiling / timing hooks

`native_timing.rs` (`NativeInstructionSample`, `NativeTimingPhase/Work`), `step_timing.rs`
(`PATH_SCHEDULE`, `PATH_SCHEDULED`, `PATH_DEVICES`, phase charges), `metrics.rs` and
`floating_profile.rs` (`std` + `native-profile` feature), `block.rs::CompiledBlock::cold` for
out-of-line cold code guided by `--profile` host samples. GameReComp's `RECOMP_TRACE`
(`pipeline.py:982-988`) and import profile (`:1055-1059`) are much coarser: a log of dispatched
imports, not instruction/phase timing.

---

## 6. GameReComp — native compile path and runtime

### 6.1 `tools/autorecomp/pipeline.py` (1384 lines)

| Symbol | Location | Detail |
|---|---|---|
| `STAGES`, `STAGE_WEIGHTS` | `:39-45` | `inspect, recover, translate, project, compile, run, verify, report`; weights 10/10/10/10/20/15/15/10. |
| `BUILD_TIMEOUT_BASE/PER_MB/CAP`, `default_build_timeout()` | `:47-64` | 600 s + 5 s/MiB, capped 3600 s; calibrated on a 375 MiB GeneRally build (≈4.6 s/MiB with `--parallel 8`). |
| `resource_footprint` | `:67-100` | Observed generated C bytes, binary bytes, project disk, peak RSS (macOS bytes vs Linux KiB normalized), stage seconds. |
| `SHIMMED_DLLS` | `:105-120` | 50+ DLL names always answered by shims, never lifted (SDL double-implementation hazard). |
| `class Pipeline` | `:261-1384` | Constructor takes `pe_path, project_dir, assets_dir, timeout, headless, oracle, jobs, split, split_bytes, build_timeout, reuse_build, fresh_build, import_trace, highlevel_dir, target`. |
| `stage_inspect` | `:322-385` | PE32 only; `analyze_pe`, `build_iat_map`; discovers bundled companion DLLs, rebases collisions via `pe_rebase.pick_base/rebase_pe` (`:189-213`). |
| `stage_recover` | `:387-433` | `discover_functions` seeds entry + exports; merges companion DLL functions into one dispatch table. |
| `stage_translate` | `:435-537` | `generate_recomp_files(functions, iat_map, gen_dir, split_size, max_unit_bytes, highlevel, read_va)`; high-level replacements selected by PE SHA + entry address; counts `/* ERROR lifting` as UNRESOLVED; records `recomp_units.json`. |
| `_write_companion_table` | `:539-609` | Embeds bundled DLL bytes via `.incbin` (or C arrays for wasm) so no DLLs needed at run time. |
| `stage_project` | `:611-659` | `projectgen.write_project(...)`; source-provenance categories `HIGH_LEVEL_VERIFIED/UNVERIFIED`, `LOW_LEVEL_FALLBACK`, `UNRESOLVED`. |
| `stage_compile` | `:661-747` | `cmake -S -B -DCMAKE_BUILD_TYPE=Release` + `cmake --build --parallel <jobs or cpu_count>`; adaptive deadline; `_run_build_command` kills the whole process group on timeout; `--reuse-build` adopts a binary only when `manifest.binary.built_from_source_digest` equals the current `_source_digest`. |
| `_compile_wasm` | `:749-907` | clang `--target=wasm32-unknown-unknown -nostdinc -O2`, `ThreadPoolExecutor(max_workers=jobs)` per-file compile, `wasm-ld --no-entry --initial-memory=64MiB --max-memory=512MiB -z stack-size=2MiB`; emits `run_wasm_node.mjs` + `index.html`. |
| `stage_run` | `:968-1091` | Runs embedded binary from `assets_dir`; headless SDL dummy drivers; `RECOMP_TRACE=1` option; classifies `exited/timeout/crashed/boot-failed/aborted` from logs and exit codes; parses import profile with `Counter`. |
| `stage_verify` | `:1093-1172` | Process-level oracle only (exit code/stdout/stderr contains); explicit `oracle_gaps` including "no frame, input or audio comparison" and "low-level register C is not high-level reconstructed source". |
| `run_pipeline` | `:1350-1384` | Fail-closed ordering, report always runs; verify runs if a run result exists. |

### 6.2 Code generation (`tools/lift/translator.py`, `projectgen.py`)

* `translator.py::discover_functions` (:62+) — Capstone 32-bit, seeds entry + exports, recovers
  EH funclets via `eh32.aux_code_targets` / DMD handler tables.
* `MAX_UNIT_BYTES = 8 * 1024 * 1024` (:142) — byte-capped split units (env
  `RECOMP_SOURCE_UNIT_BYTES`), prevents a single gigantic translation unit (GeneRally had a
  273 MB unit). `generate_recomp_files` (:312+) emits `recomp_*.c`, `recomp_funcs.h`,
  `recomp_dispatch.c` (`recomp_dispatch_entry_t recomp_dispatch_table[]`, address-sorted), with
  `__lowlevel` names for high-level-replaced functions.
* `projectgen.py::write_project` (533-line module) copies the pinned runtime and writes
  `CMakeLists.txt`, `main.c`, `project.json`; the generated C is compiled by the host C compiler
  — **no runtime code generation**.

### 6.3 Runtime (`runtime/`)

| Item | Location | Detail |
|---|---|---|
| PE mapping / IAT binding | `runtime/win32hle/pe_loader.c` | `recomp_pe_map`, `recomp_pe_map_any`, `recomp_pe_map_mem` (embedded image), `recomp_pe_relocate`; maps sections into a flat guest address space and binds IAT to resolver addresses. |
| HLE shim dispatch | `runtime/win32hle/host.c`, `win32hle.h` | DLL registration list matching `SHIMMED_DLLS`; `[hle] <name>` trace lines consumed by `stage_run`. |
| Guest threads → host threads | `runtime/win32hle/kernel32.c:154-160` (`hle_thread_id*`, atomic `__sync_add_and_fetch`), `:701-737` (TLS via `__thread uint32_t g_tls[64]`), `sdl12.c:518-547` (`SDL_CreateThread` → `pthread_create` + trampoline, `hle_thread_id_adopt`), `msvcrt.c:2777` (`_beginthreadex`); critical sections/mutexes/events in `kernel32.c:396-618`. Selftests: `thread_stress_selftest.c` (200 threads), `threadsync_selftest.c`. |
| Lifted↔real boundary | `runtime/hybrid/hybrid.c:1-120`, `docs/HYBRID.md` | `hybrid_call_machine` marshals EAX..EDI/EBP/ESP via a per-thread block in `fs:[0x14]` (`NT_TIB.ArbitraryUserPointer`, Win64 MSVC `__asm`); reason: `__declspec(thread)` lookup would clobber EAX/ECX; supports 14 engine threads calling forwarded imports reentrantly. |
| Register model | `runtime/recomp32/main.c:13-24` | Globals `g_eax...g_esp`, segment registers, `g_mem_base`; `main.c:31-34` ".text not mapped — we ARE the code". |
| Other backends | `runtime/recomp16`, `runtime/dos32`, `runtime/nextstep`, `runtime/wasm32`, `runtime/native32` | No JIT anywhere; wasm32 replaces ELF/Mach-O `.incbin` with C byte arrays. |

**Comparison summary:** GameReComp's only "scheduling" is the OS scheduling host pthreads that
execute guest-thread code; there is no virtual time slice, no guest interrupt/TLB broadcast, no
code cache, no emitted-code trampoline. Stela's machinery is an order of magnitude more complex
because it emulates an ARM SoC with a full guest OS rather than rehosting a single PE32 program.

---

## 7. Comparison at a glance

| Dimension | Stela | GameReComp | Gap / implication |
|---|---|---|---|
| Translation model | AOT + JIT, per basic block, dynamic fetch | AOT only, per function, whole program at build time | GameReComp cannot handle runtime-generated/indirect code; Stela has `UnsupportedReason::RuntimeGeneratedCode` |
| Code representation | Hand-emitted x86-64 bytes, `NativeBlock` + source map + relocations | Register-transfer C compiled by Clang/GCC (or wasm32) | Stela controls every instruction; GameReComp inherits host compiler optimizations |
| Host specialization | CPUID/XGETBV `TargetCpu` + `id()` in cache key | Generic host `-O2`, target triple only | Stela gets AVX2/512/APX/SHA recipes per host; GameReComp does not |
| Persistent cache | Content-addressed, compiler-fingerprint-bound, single-flight, atomic rename, checksums | `--reuse-build` with source-digest manifest; no per-function cache | GameReComp has reuse but no single-flight/concurrent population or per-function identity |
| In-memory code cache | Block table + ITLB-like lookup + jump cache + chained exits + grace reclamation | None | Biggest runtime speed gap: Stela avoids a dispatcher exit per block |
| Emitted-code invocation | `mmap` RW→RX, `extern "sysv64"` ABIs, `block_entry::enter` trampoline, `BlockExit` in RAX/RDX | Direct call to compiled C functions via a sorted dispatch table | GameReComp needs no trampoline but also no patching/chaining |
| Threading | PE-per-core `Machine`, `Hub`, `Kick`, fibers, SMP doorbells | Guest threads → pthreads via HLE shims | Different problems: guest-CPU simulation vs rehosted threading |
| Scheduling | 2 ms round-robin quantum, haltpoll, deadlines, interrupt epochs, device polling | OS scheduler only; pipeline `--parallel` builds | Stela's scheduler is essential to correctness (guest OS), not just speed |
| Safety/discipline | Bounded lock steps, RCU grace, validation on every load, no sandbox | Fail-closed stages, source digest, timeouts, process-group kill | Both emphasize provenance; Stela adds execution-time safety |

---

## 8. Adaptation candidates for GameReComp (ranked)

All are **design reimplementations**, not code copies (licence §2/§7 of S4). Order = value ×
feasibility for GameReComp's static-AOT architecture.

1. **Single-flight + atomic publication for build artifacts (Stela `cache_flight.rs` +
   `lib.rs::prepare`).** GameReComp's `stage_compile`/`--reuse-build` is per-project and racy if
   two pipelines share a project. Add a per-source-digest flight registry (mutex per key, atomic
   temp-dir + rename) so concurrent runs share one compiler invocation; keep
   `built_from_source_digest` validation. Low risk, high payoff on CI/batch runs.

2. **Compiler/toolchain fingerprint in the reuse key (Stela `SOURCE_SHA256`, `Identity`).**
   GameReComp's `_source_digest` covers generated C but not compiler version/flags/CMake config;
   a compiler upgrade can silently change the binary. Extend `manifest.binary` with a toolchain
   fingerprint (`clang --version`, CMake generator, CFLAGS) and include it in the digest check —
   exactly Stela's "generated code uses a different compiler" refusal
   (`execution.rs:68-71`).

3. **Per-function content-addressed artifact cache with opportunity for incremental rebuild.**
   Stela keys each block by `(source_hash, compiler_hash, guest_base, abi, target_cpu,
   memory_specialization)`. GameReComp rebuilds every generated unit in `build/` from scratch on
   each fresh build (`pipeline.py:671-673`). A per-unit digest cache (recompile only changed
   `recomp_*.c`) would cut the 5 s/MiB build cost substantially; the already-emitted
   `recomp_units.json` and `reports/functions.json` provide the needed unit map.

4. **Host-target specialization at build time (Stela `TargetCpu`).** GameReComp already compiles
   C with host Clang/GCC; make target explicit and record it (e.g. `-march=x86-64-v2/v3`, AVX2,
   or wasm32) in the manifest, with a `--target-cpu` knob analogous to
   `prepare_block_for_target`. This turns an implicit property into a reproducible, cacheable
   one and avoids unsafe `-march=native` artifacts being reused on a different machine.

5. **Idle-time speculative precompilation (Stela `engine/ahead.rs`).** Stela's measured
   depth-1 successor lookahead (89% hit) and bounded queues (`CAPACITY 4096`) could inspire a
   GameReComp "prefetch" phase: while a game runs headless under `stage_run`, compile the next
   project/batch item's units; or, for the hybrid path, pre-link likely fallback DLL imports.
   Requires a long-lived daemon — a bigger change than 1-3.

6. **Bounded critical sections / process-group timeout (Stela `code_cache.rs` step constants +
   GameReComp's existing `_run_build_command`).** GameReComp already kills the whole build
   session group on timeout (`pipeline.py:945-966`); Stela's measured `STEP_VISITS/YIELD_VISITS`
   shows how to pick step sizes from lock-acquisition intervals. Apply this measurement method
   if GameReComp adds a shared build cache with a lock.

7. **Safe live patching discipline (`x86_64/chain.rs`) only if GameReComp adds a JIT.** Today it
   does not, so this is speculative: if a future "hot function recompilation" feature emits code,
   use Stela's single aligned 8-byte store patch points, pre-write the target, and RCU grace
   before reclaiming — never RWX.

8. **Avoid:** importing Stela's guest-CPU scheduler, TLB/IC broadcast, PAC/shadow-MMU or
   `stela-linux*`/`stela-boot-core` code. GameReComp has no guest CPU model, and those crates are
   GPL-2.0-only in places.

---

## 9. Exact index (files / functions / classes referenced)

**Stela**
- `crates/stela-compiler/src/lib.rs`: `compile`, `Options`, `NativeBlock`, `Lifter`, `UnsupportedReason`, `SOURCE_SHA256`, `Backend`.
- `crates/stela-compiler/src/block.rs`: `CompilationPlan`, `Stage`, `CompiledBlock`, `BlockExit`, `ExitKind`, `MAX_BLOCK_INSTRUCTIONS`, `decode_terminator`.
- `crates/stela-compiler/src/target.rs`: `TargetCpu` (`detect`, `id`, `supported`, `supports`, `from_cpuid`, `X86_64_V2`), `HostCpuid` (`read`), `Vendor`, `features!`, `darwin_on_demand_avx512f`.
- `crates/stela-compiler/src/x86_64/block_entry.rs`: `stela_block_enter` global asm, `enter`.
- `crates/stela-compiler/src/x86_64/chain.rs`: block chaining, `ChainControl`, `LookupTable`, `DirectExit`, `PATCH_BYTES`, `JUMP_CACHE` consumer.
- `crates/stela-engine/src/lib.rs`: `prepare_leaf`, `prepare_block*`, `prepare`, `validate`, `bounded_file`, `block_plan_identity`, `memory_specialization_sha256`, `Prepared`, `translated_callee!`.
- `crates/stela-engine/src/cache_flight.rs`: `Flight::{join, wait}`, `FLIGHTS`.
- `crates/stela-engine/src/artifact/prepare.rs`: `MAX_CACHE_NATIVE_BYTES`, `MAX_CACHE_MANIFEST_BYTES`, `validate_manifest_bytes`, `validate_preparation_{metadata,native,claim}`, `generate`.
- `crates/stela-engine/src/dispatch.rs`: `LeafDispatcher::{register, resolve, is_current, unregister}`, `ResolvedLeaf`, `DispatchIdentity`, `validate_source_map`.
- `crates/stela-engine/src/execution.rs`: `ExecutableLeaf::{load, execute}`, `MAX_NATIVE_BYTES`.
- `crates/stela-engine/src/execution/native_image.rs`: `ExecutableStorage`, `NativeImage`, `ReachedCall`, `exit`.
- `crates/stela-engine/src/execution/platform/posix.rs`: `Mapping::{new, call, call_context, call_context_block, call_budgeted_block, call_block, call_amx_kernel}`; `crates/stela-engine/src/execution/platform/freestanding.rs`.
- `crates/stela-engine/src/blocks/aot.rs`: `BlockRuntime::{new_aot_for_target, preload_artifact}`, `LoadedBlock::from_artifact`, `ArtifactPreparation`.
- `crates/stela-engine/src/machine/engine.rs`: dispatch-loop constants (`JUMP_CACHE`, `CHAIN_LIMIT`, `FETCH_CACHE`, `MEMORY_RELEASE_BLOCKS`), module list.
- `crates/stela-engine/src/machine/engine/scheduler.rs`: `Machine::{run, run_with_budget, run_blocks_with_budget, leave_run_loop}`, `PROMOTION_RUNS`, `DIRECT_RUNS`, `HOT_SAMPLES`.
- `crates/stela-engine/src/machine/engine/processors.rs`: `Peer`, `Machine::schedule`, `schedule_with_deadline`, `QUANTUM_HZ`, `IDLE_POLL_HZ`.
- `crates/stela-engine/src/machine/engine/parallel.rs`: `Kick`, `Hub`, `GuestWatch`, `idle_poll_after`, `SharedDevices`.
- `crates/stela-engine/src/machine/engine/compilation.rs`: `fetch_block`, `insert_target`, `grow_table`.
- `crates/stela-engine/src/machine/engine/code_cache.rs`: `stale_code`, `check_stale`, step constants.
- `crates/stela-engine/src/machine/engine/ahead.rs`: `Job`, `DEPTH`, `CAPACITY`, `ELSEWHERE_CAPACITY`.
- `crates/stela-engine/src/machine/engine/shared_memory/scheduler.rs`: candidate native-round scheduler (`run_serialized_with_budget`).
- `crates/stela-boot/src/engine/parallel.rs`: `Doorbells: Kick`, `spawn`, `Pe::run`.
- `crates/stela-boot-core/src/fiber.rs`: `Fiber`, `Handle`, `resume`, `suspend`, `switch`, `entry`, `main`.
- `crates/stela-cli/src/setup/translations.rs`; `crates/stela-runtime/src/translation_store.rs`; `crates/stela-engine/src/translations/pack/*`.

**GameReComp**
- `tools/autorecomp/pipeline.py`: `Pipeline`, `StageFailure`, `STAGES`, `default_build_timeout`, `resource_footprint`, `SHIMMED_DLLS`, `stage_*`, `_run_build_command`, `_compile_wasm`, `_adopt_reused_binary`, `_write_manifest`, `_source_digest`, `run_pipeline`.
- `tools/lift/translator.py`: `discover_functions`, `generate_recomp_files`, `MAX_UNIT_BYTES`, `make_read_va`.
- `tools/autorecomp/projectgen.py`: `write_project`, `default_project_dir`, `executable_name`.
- `runtime/win32hle/pe_loader.c`, `host.c`, `win32hle.h`, `kernel32.c`, `sdl12.c`, `msvcrt.c`, `thread_stress_selftest.c`, `threadsync_selftest.c`.
- `runtime/hybrid/hybrid.c` (`hybrid_call_machine`), `docs/HYBRID.md`.
- `runtime/recomp32/main.c`, `docs/ARCHITECTURE.md`.

---

## 10. Limitations / not verified

* The Stela checkout has no `.git` metadata available here, so the pin was taken from the task
  statement, not independently verified with `git rev-parse`.
* I did not build or run either project; all statements are from source inspection. Claims about
  measured constants (e.g. 89% successor hit rate, 4.6 s/MiB build rate) are the repositories'
  own documented justifications, quoted with their source locations.
* `stela-runtime` was inspected only far enough to establish it is restore/install code, not the
  emulation runtime; S1/S4 cover the wider architecture.
* The `stela-boot` freestanding path's mechanism for executing translations without RX mappings
  was not traced end-to-end (it is gated by `freestanding.rs` refusals); do not infer it can JIT.
* Licence conclusions are engineering observations, not legal advice; obtain legal review before
  any port, and never copy from GPL-2.0-only Stela components.
