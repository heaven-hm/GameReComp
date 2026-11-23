# S9 — Stela profiling / benchmarking audit

**Audit agent:** S9 (read-only sources).
**Sources:** `STELA` = `/private/var/folders/.../opencode/stela`, pinned at
`140ffaf716e2fa8a2f13d55880ca0fc4521df6fe`; `GAMERECOMP` =
`/private/var/folders/.../opencode/integration`. Neither tree was edited.
**Date:** 2026-11-23.
**Deliverable:** reproducible benchmark methodology that can be adapted to
GameReComp (recompile time, generated code size, compile time, runtime), with
exact files, licence and dependencies, plus a concrete plan.

---

## 1. Executive summary

Stela has a **large, disciplined, opt-in measurement culture**, not a Criterion
suite. There is **no `criterion`/`divan`/`iai` dependency anywhere** (verified:
no match in any `Cargo.toml`). Timing is done with `std::time::Instant` inside
ignored `#[test]` functions and `examples/`, emitting machine-readable stdout
(JSON or stable key/value lines) or files. A single `manifest.json` catalogues
529 input-backed "oracle" tests, of which ~100 are timing/size/density
measurements marked `kind: "manual"` ("run on request", often "run in release
on a quiet host").

Four patterns are directly transferable to GameReComp:

1. **Input binding + identity**: every benchmark names its input through an
   env var (`STELA_COMPILE_BENCH`, `FETCH_IMAGE`, `STELA_PACK_SAMPLE`,
   `DENSITY_IN`, `STELA_AEA_BENCH_IPSW`, ...); the harness records path, byte
   length and SHA-256, plus git revision, dirty-tree flag, host OS/arch and a
   compiler-source digest. No input is committed.
2. **Cold / cached / warm staging** measured separately (`block_dispatch_benchmark`).
3. **Per-unit normalisation** (ns per guest instruction, µs per block, host
   bytes per instruction, encoded bytes per entry) and **phase decomposition**
   (`fetch_phase_costs`: admit / decode / emit / stored-form / relocate).
4. **Deterministic comparison, not just timing**: cross-backend outcome tables
   (`cpu_reference`), single-stepped host instruction counts
   (`executed_paths`), weighted code-density dumps (`density.rs`), and an
   explicit *known differences* ledger with written reasons.

License caveat: Stela's own benchmark code is **GPL-2.0-or-later**; GameReComp
is **AGPL-3.0-only**. **Adapt the methodology; do not copy code verbatim**
without a licence review (§6).

---

## 2. Inventory (exact files)

All paths are relative to STELA unless noted.

### 2.1 Orchestration and provenance

| File | Role |
|---|---|
| `xtask/src/oracles.rs` | `cargo xtask oracles`: declares `list/only/output/timeout_secs`; builds test binaries with `cargo test --all-features --locked --no-run --message-format=json`; discovers ignored tests via `--list --ignored`; runs one test with `--ignored --exact --test-threads=1`; enforces per-test timeout through `tools::wait`; writes `.stela/oracles/<unix-ns>/report.json`. |
| `xtask/src/oracles/manifest.json` | 529 entries. Fields: `package`, `test`, `source`, `kind` (`oracle` / `isolation` / `manual`), `inputs`, `values`, `outputs`, `programs`, `reason`, `precondition`. Env vars must be `STELA_[A-Z0-9_]+`. ~100 `manual` entries are benchmarks. |
| `xtask/src/oracles/selector.rs` | `Precondition` / `canonical_reason`; enforces that each `#[ignore = "..."]` literal matches the manifest reason (a generated-metadata consistency test at `oracles.rs:670-728`). |
| `xtask/src/tools.rs` | Process helpers: `root()`, `cargo()` (uses the launching `$CARGO`), `mac_host()` (`STELA_MAC`), `binding()` → `{path, bytes, sha256}`, `wait()`/`stop()` timeout+kill. |
| `xtask/src/cpu_reference.rs` | `cargo xtask cpu-reference [--record|--recorded|--only ...]`. Runs the same A64 cases on an Apple Mac over ssh and through Stela, compares canonical outcomes, writes a ledger under `.stela/reference/cpu`, and can regenerate `apple-m1-pro.tsv` with the host identity line. |
| `xtask/src/cpu_reference/{corpus.rs,record.rs,stela.rs,runner.c,tests.rs,apple-m1-pro.tsv}` | Case corpus, 1184-byte input / 1064-byte output wire format, canonical "only what changed" outcome formatter, Stela back end, arm64 C runner, unit tests, and the committed 1350-line reference table. |
| `crates/stela-compiler/build.rs` + `src/lib.rs:33` | `SOURCE_SHA256`: fingerprint of all non-test compiler sources + locked `rustc_apfloat` version; embedded in binaries and printed by cost tests. This binds a measurement to the exact compiler that produced it. |

### 2.2 Recompile / compile / code-size benchmarks (most relevant to GameReComp)

| File / test | What it measures | Output & method |
|---|---|---|
| `crates/stela-engine/examples/block_dispatch_benchmark.rs` (`cargo run --release -p stela-engine --example block_dispatch_benchmark`) | **Cold compile**, **disk-cache load**, **warm dispatch**. | Asserts `!cfg!(debug_assertions)`. 7 trials × 1000 runs of a 3-instruction translated counting loop at loop counts `[1, 16, 1024]`; sorts per-run values and takes the median; prints JSON with `cold_compile_ns`, `disk_cache_load_ns`, `warm[{loop_count, median_ns_per_run, median_ns_per_block}]`, `compiler_source_sha256`. Correctness of the loop state is asserted on every run. |
| `crates/stela-engine/src/machine/engine/stored/tests.rs:922-1057` — `compile_bench` (`STELA_COMPILE_BENCH`, `STELA_COMPILE_OFFSET`, `STELA_COMPILE_BYTES`, default 4 MiB) | **Compile time + generated code size + stored-form/encode/decode cost over real code**. | Sweeps a raw code image, compiles every block, counts `instructions`, `compiled.native.x86_64_code.len()`. Prints `host_bytes_per_instruction`, `compile_us_per_block`, `store_us_per_block`, `encode_us_per_entry`, `decode_us_per_entry`, `encoded_bytes_per_entry`, `retained_bytes_per_entry`, plus correctness counters (`differed`, `provable_unstorable`). Uses `system_preparation(TargetCpu::detect())`. |
| `crates/stela-engine/src/machine/engine/fetch_costs.rs:295-371` — `fetch_phase_costs` (`FETCH_IMAGE`, `FETCH_OFFSET`, `FETCH_BYTES`, `FETCH_BATCH`, `FETCH_POLLUTE`, `FETCH_PROFILE`) | **Phase-decomposed compile pipeline**: admit / decode / emit / stored-form / relocate, plus wall time and a digest of the image. | Warms compiler tables on a 256 KiB prefix before measuring; optional cache polluter; optional Linux sampler (`FETCH_PROFILE`); prints **ns per guest instruction** per phase and `compile-total`. |
| `crates/stela-engine/src/machine/engine/fetch_costs.rs:379-457` — `pack_record_compression` | **Generated-code size / on-disk footprint** per 4 KiB page record, at DEFLATE levels 1 and 6. | Compiles every block of an 8 MiB image, groups entries into per-page records, reports `bytes_per_record`, `ratio`, `deflate_us_per_record`, `inflate_us_per_record`. |
| `crates/stela-engine/src/machine/engine/fetch_costs.rs:466+` — `stored_entry_bytes` | **Runtime memory footprint** of stored entries and their recovery rows over real code. | Mean bytes retained, packed rows, unpacked vectors. |
| `crates/stela-engine/src/translations/pack/codec.rs:230-299` — `codec_costs` (`STELA_PACK_SAMPLE`) | **Pack size + decode time** comparing Stela's LZ with DEFLATE-1 against raw parse. | Reads every 8th record (`step_by(8)`) to keep a few-hundred-MB pack on disk; reports bytes/percent and ns per record, with `assert_eq!` correctness on every decompression. |
| `crates/stela-engine/src/machine/engine/tests/reinstall_cost.rs:121-212` — `reinstall_cost` (`REINSTALL_WINDOW`, `REINSTALL_ROUNDS`, `REINSTALL_KEPT`) | **Recompile-vs-reuse**: cost of reinstalling a kept translated form when the code window is too small, by outcome kind (Found/Compiled/Rebased/Other). | Two warm-up rounds then `rounds` measured rounds; per-kind `ns_per_fetch`; eviction/kept/rebase counters; optional sampler. |
| `crates/stela-engine/src/machine/engine/density.rs:16-108` — `density_dump`, `chain_dump` (`DENSITY_IN`/`DENSITY_OUT`) | **Generated code density**, weighted by an input weight column. | Input lines are `hex-PC weight hex-bytes`; output lines carry source bytes, hex native code, source map, or the refusal reason; `chain_dump` additionally emits link/cell/probe/stamped/lookup tail sizes. Explicitly "run by hand", emits hex for *offline counting*. |
| `crates/stela-compiler/src/x86_64/executed_paths.rs` (`DENSITY_IN`/`DENSITY_OUT`, `REGIONS=0`) + `census.rs` | **Dynamic host instruction count** of translated blocks. | Forked child, Linux x86 trap flag (`EFLAGS |= 0x100`), SIGTRAP/SIGINFO handler records RIP offsets into a 2^20 trace, per-block `B` lines, per-guest-instruction `S` lines, `C` histogram, `T` weighted host-instructions-per-guest-instruction by cause. Uses `mprotect`, `alarm`, pipes; guest registers point into a scratch arena so loads stay mapped. |
| `crates/stela-compiler/src/x86_64/executed_paths.rs` — `allocator_bound_census` | Same corpus, system- vs process-mode code for the allocator bound. | Manual oracle entry. |
| `crates/stela-engine/tests/regions.rs` — `regions_time_their_programs` | Runtime of each region program vs isolated blocks. | Manual; "release build on a quiet host". |
| `crates/stela-cli/src/restore/aea/benchmark.rs:24-119` — `native_stream_authentication_benchmark` | Native decryption throughput on external, inventoried inputs. | Verifies source size, BuildManifest digest, AEA header digest and final plaintext SHA-384 against the inventory before timing; hashes plaintext into a `Sink` and **discards it**; prints JSON with `seconds`, `plaintext_bytes_per_second`, input digests, `plaintext_retained: false`. |

### 2.3 Other runtime / cost benchmarks (pattern reference)

`crates/stela-engine/src/machine/engine/{compression.rs,block_index.rs,pointer.rs,pointer/native_tests.rs,pointer/cost.rs,tests.rs,tests/exits.rs,tests/table_base.rs,tests/process_switching.rs,tests/retire_cost.rs,tests/sharing.rs,tests/lock_path.rs,memory/../system_ram/tests.rs,parallel/tests.rs}`, `crates/stela-compiler/src/{wkdm/codec.rs,x86_64/block/flag_cost_tests.rs,x86_64/chain/tests/{costs.rs,paired.rs,transfers.rs},x86_64/exit_boundary_tests.rs,x86_64/amx_routine/tests.rs,x86_64/amx_transfer/tests.rs,x86_64/simd/{conversion_costs.rs,sm4/tests/costs.rs,sve/integer_bits/tests/costs.rs},amx/native.rs}`, `crates/stela-driver-gpu/src/metal/service/tests/{gate.rs,commands.rs}`, `crates/stela-soc/src/{ans/tests.rs,cpm.rs,dcp/iomfb/tests.rs,machine/tests.rs,dwc3/xhci/slot.rs,scanout.rs}`. All are `#[ignore = "manual: opt-in"]` and catalogued in the manifest.

### 2.4 Host determinism

`crates/stela-boot-core/src/host_performance.rs` (930 lines) is the environmental control: after ExitBootServices it asks each core for full performance via Intel HWP / AMD CPPC / EIST / P-states, clears on-demand clock modulation and sets energy/performance bias to performance, while leaving power/thermal limits and turbo-disable switches alone. It records a `Tally` and prints a `HOST PERFORMANCE` / `HOST POWER` summary. Benchmark docs repeatedly require "a quiet host", "run in release", and "isolated pinned execution"; `sve_merge_costs` and `sm4_costs` demand actual GFNI/AVX-512 support, `sm4_costs` requires "isolated pinned execution on request".

---

## 3. Reproducible methodology — the patterns worth stealing

1. **Opt-in, never in CI by accident.** Every measurement is `#[ignore = "manual: opt-in"]` (or a standalone example) and the oracle manifest is the single source of truth. A unit test (`oracles.rs:670`) fails if a new ignored test is not catalogued, or if its ignore reason no longer matches the manifest. `cargo test` stays fast and non-flaky.
2. **Input identity, not hard-coded paths.** Required inputs are env vars; the runner records `kind` (`path`/`value`/`output`/`program`), byte count and SHA-256 for each; values are hashed too. Outputs must not already exist. The AEA benchmark additionally verifies the input's own manifest/header digests *before* timing so a wrong input cannot produce a plausible number.
3. **Provenance in every report.** git `HEAD`, `git status --porcelain --untracked-files=no` (dirty flag), `started_unix_seconds`, host OS/arch, `schema` version, per-test `elapsed_ms`, stdout/stderr logs, and `SOURCE_SHA256` for compiler-touching tests. `apple-m1-pro.tsv` records machine identity (`sysctl machdep.cpu.brand_string hw.model`, `sw_vers`) in its header.
4. **Release-only guard.** `block_dispatch_benchmark` fails fast with `ensure!(!cfg!(debug_assertions), "build this benchmark with --release")`. Manifest reasons repeat "run in release".
5. **Warm-up then median, not a single sample.** `block_dispatch_benchmark`: warm the runtime, then 7 trials × 1000 runs, sort with `f64::total_cmp`, take the median. `fetch_phase_costs` warms compiler tables on a 256 KiB prefix. `reinstall_cost` does two unmeasured rounds. `sm4_costs` uses paired raw binaries per vector length (dependent vs independent) to expose measurement artifacts.
6. **Report per unit of work.** ns/block, ns/guest-instruction, µs/entry, bytes/instruction, bytes/record, MiB/s. This makes results comparable across input sizes and hosts.
7. **Separate the phases.** `fetch_phase_costs` and `compile_bench` split admit / decode / emit / store / relocate / encode / decode; `block_dispatch_benchmark` splits cold compile / disk-cache load / warm dispatch. A regression can be attributed to a stage.
8. **Separate cold, cached and warm.** Disk-cache load is its own number; "reused build" is never reported as a zero-time compile (GameReComp's `tools/benchmarks.py` already follows this rule for `--reuse-build`).
9. **Instruction counts as a complementary metric.** `executed_paths.rs` counts host instructions with the trap flag in a forked child, avoiding clock noise entirely; `retain cost` tests pair timing with "single-stepped host instruction counts". This is the right way to detect code-size regressions that timing hides.
10. **Weights and refusals.** `DENSITY_IN` lines carry a weight so aggregate density is representative; every refused block is emitted with a reason, so the denominator is honest. `compile_bench` reports `refused` and correctness counters.
11. **Cross-backend oracle tables and a known-differences ledger.** `cpu-reference --record` writes a canonical "only changed registers/buffer bytes" outcome per case; `--recorded` rechecks without the Mac; divergences are split into `known` (with a written reason, e.g. "arm64 process: keys disabled on the Mac") and `unknown` (fails the task). A per-run ledger is written under `.stela/reference/cpu`.
12. **Timeouts and process control.** `Oracles.timeout_secs` (default 300) with `tools::wait` polling `try_wait` every 20 ms then `kill`; GameReComp already uses an adaptive compile deadline and its own process group — keep that and add the oracle-style per-case deadline.

---

## 4. Mapping to GameReComp's four concerns

| Concern | Closest Stela exemplar | Transferable metric |
|---|---|---|
| **Recompile time** | `reinstall_cost`, `fetch_phase_costs`, `block_dispatch_benchmark` cold/disk/warm | ns or µs per recompiled block/function; separate cold, cache-hit, reinstall/rebase; per-phase decomposition |
| **Generated code size** | `compile_bench` `host_bytes_per_instruction`, `density_dump`, `chain_dump`, `executed_paths` | bytes per guest instruction/IR op; per-function and per-project totals; weighted density; tail/link overhead |
| **Compile time** | `compile_bench` `compile_us_per_block`, `fetch_phase_costs`, `convert/routine cost` tests | wall seconds and µs/unit; median over trials; input-size normalisation; toolchain/compiler SHA |
| **Runtime** | `block_dispatch_benchmark` warm loop, `regions_time_their_programs`, `executed_paths` | median ns per run / per block, and single-stepped instruction counts; warm-up + trials |

GameReComp already has the *reporting* layer: `tools/benchmarks.py` renders
`docs/BENCHMARKS.md` from `reports/games/results.json` + per-project
`report.json`, with stage metrics `recover.{count,instructions}`,
`translate.{source_units,source_bytes}`, `compile.{seconds,reused,binary}` and
honest `-` for unmeasured/reused values. What it lacks is the *microbenchmark
harness* and the provenance/identity discipline described above.

---

## 5. Concrete benchmark plan for GameReComp

### Phase 0 — guardrails (no behaviour change)
- Keep `docs/BENCHMARKS.md` as the published table; add a companion
  `docs/BENCHMARK_METHOD.md` generated from the new harness (never hand-written
  numbers).
- Preserve the existing honesty rules: unmeasured = `-`, `--reuse-build`
  excluded from timing statistics, verification status independent of score.

### Phase 1 — manifest + runner (adapt `xtask/src/oracles.rs`)
- Add `tools/bench_manifest.json` (analogous to `oracles/manifest.json`) with
  fields `name, command, kind (micro|macro|oracle), inputs[], values[],
  outputs[], reason, timeout_secs, min_trials`.
- Add `tools/bench_run.py` (or a `xtask`-style Rust binary) that:
  - requires `GAMEREC_*` env vars for every input, records
    `{path, bytes, sha256}` and rejects missing/unchanged outputs;
  - refuses to run debug builds for timing cases;
  - records `git rev-parse HEAD`, dirty flag, OS/arch, Python/rustc/clang and
    CMake/compiler versions, and the digest of the recompiler sources;
  - sets a per-case deadline and runs each case in its own process group;
  - writes `reports/bench/<unix-ns>/report.json` (schema-versioned) with
    stdout/stderr logs, never overwriting an existing report.
- Add a consistency test (`tests/test_bench_manifest.py`) that fails when a
  benchmark exists without a manifest entry or when a declared input is unset.

### Phase 2 — the four metric families
1. **Recompile time** — new example/harness over a fixed corpus of recompiled
   functions (legal, user-supplied binaries only, via `GAMEREC_CORPUS`):
   report `cold_recompile_ms`, `cache_hit_ms`, `reinstall_ms`, and
   `us_per_function`/`ns_per_instruction`, with 7 trials × warm-up and median;
   assert the recompiled output is byte-identical to the expected recompile
   digest before timing (Stela's AEA-before-timing verification pattern).
2. **Generated code size** — extend `tools/size_tier_report.py` / add
   `tools/code_density.py`: per-function emitted bytes, `bytes_per_guest_instruction`,
   per-project totals, and a `DENSITY_OUT`-style machine-readable dump
   (`pc weight source_bytes hex_bytes source_map`) with refusals counted.
   Optionally add a single-step/trap-flag instruction-count oracle for x86
   hosts (adapt the *idea* of `executed_paths.rs`, not the code).
3. **Compile time** — already captured as `compile.seconds`; add
   `us_per_source_MiB`, median over repeated compiles of the *same* source
   (with a clean temp build dir), and attach the exact toolchain/source digest.
   Record the reused-build case separately, never as zero.
4. **Runtime** — add a deterministic in-process workload (a fixed loop/
   scenario) with warm-up + 7×N median, reporting `median_ns_per_run` and
   `ns_per_emulated_instruction`, plus a cross-run *instruction count* so a
   timing change can be classified as code-quality vs frequency.

### Phase 3 — determinism and comparison
- Add `tools/bench_oracle.py` + a committed reference table, following
  `cpu-reference`: canonical outcome per case (only changed registers/memory,
  or signal), `--record`/`--recorded` modes, host identity in the header, and
  a **known-differences ledger** where each accepted divergence has a written
  reason. Unknown divergence fails the run.
- Optionally adopt the Stela host-performance idea in documentation: require a
  quiet host, fixed performance policy where available, release build, and
  pinning; record the measured host state in the report.

### Phase 4 — publication
- `python -m tools.benchmarks` continues to render the macro table; add
  `python -m tools.bench_run --render` to append a "Microbenchmarks" section
  from `reports/bench/.../report.json`, with input SHA, source digest, host
  identity, trial count and median, and a warning that numbers are not
  comparable across different hosts/digests.

### Acceptance criteria
- `cargo test`/`pytest` never runs a timing case by default.
- Every published number has: input SHA-256, source/toolchain digest, host OS/
  arch, git revision, dirty flag, trial count, unit, and a timestamp.
- Reused builds and unmeasured cases remain `-`.
- A known-good input replayed on the same host/digest reproduces the median
  within a declared tolerance; otherwise the harness flags it (do not silently
  publish).
- No game binary or proprietary input is committed; all paths come from env
  vars.

---

## 6. Licence and dependencies

- **Stela licence:** GPL-2.0-or-later (`Cargo.toml:65`, `README.md:102`,
  `LICENSE` = GNU GPL v2 text). `NOTICE` states Stela's own code is
  GPL-2.0-or-later but some shipped programs containing Linux-derived code are
  GPL-2.0-only; third-party texts are in `LICENSES/` and `NOTICE`.
- **GameReComp licence:** AGPL-3.0-only (`Cargo.toml:5`, `LICENSE` = GNU AGPL
  v3). GPL-2.0-or-later permits choosing GPL-3.0, which is compatible with
  AGPL-3.0, but **copying benchmark source verbatim still needs a review** —
  especially of any file carrying a GPL-2.0-only or third-party header. The
  safe path is to re-implement the *methods and JSON schemas* in original
  AGPL-3.0 code and cite the source/pin in comments.
- **Benchmark dependencies (Stela):** no benchmarking crate. Workspace deps
  used by the harness/benchmarks: `anyhow`, `clap` (derive), `serde`,
  `serde_json`, `sha2`, `hex`, `libc` (0.2), `tempfile` (3), `miniz_oxide`
  (DEFLATE for pack tests), `plist` (1), `zip` (4, deflate), `sha1`, `object`,
  `quick-xml`, `smoltcp`; `stela-fingerprint` for source digests; `clang` /
  `llvm-nm` / ssh to a Mac for the CPU reference; Linux `perf`-style sampling
  via a local implementation keyed by `FETCH_PROFILE`. Rust edition 2024,
  toolchain pinned in `rust-toolchain.toml`.
- **GameReComp already has:** Python 3, `statistics`, `json`, `pathlib` in
  `tools/benchmarks.py`, so Phase 1–2 can be pure Python with no new runtime
  dependency.

---

## 7. Limitations / what Stela does not provide

- No Criterion-style statistical analysis (no confidence intervals, no
  outlier rejection beyond median-of-trials), no automatic regression
  threshold, no CI benchmark job — every measurement is manual and its docs
  rely on "a quiet host".
- No published performance numbers in Stela's Markdown (verified: no matching
  benchmark docs); results live in ignored `.stela/...` reports or stdout.
- Determinism aids are host-specific: the trap-flag instruction counter is
  Linux x86; the native reference is macOS/arm64 over ssh; the P-state request
  is x86 firmware code. GameReComp should implement equivalents for its own
  host, not assume portability.
- The `cpu_reference` table is a *semantic* oracle (correctness), not a timing
  benchmark; its value is the provenance/ledger discipline.
- No license-compatible code drop: methodology transfer only (§6).

---

## Appendix A — exact file/line index

- `xtask/src/oracles.rs:18-32` (CLI), `:63-98` (report schema), `:107-164`
  (run/report), `:166-237` (collect), `:292-342` (readiness), `:344-386`
  (input identity), `:388-464` (binary build/discovery), `:490-534` (single
  test run), `:670-728` (manifest consistency test).
- `xtask/src/oracles/manifest.json`: 529 entries; benchmark entries listed in
  §2.2–2.3; compile-related test lines 4876 (`compile_bench`), 4884/4892
  (`density_dump`/`chain_dump`), 4932 (`codec_costs`), 5108/5116/5148
  (`fetch_phase_costs`, pack compression, `stored_entry_bytes`), 5164
  (`reinstall_cost`).
- `xtask/src/tools.rs:95-121` (`binding`, `wait`, `stop`).
- `crates/stela-compiler/build.rs:1-17`, `src/lib.rs:33` (`SOURCE_SHA256`).
- `crates/stela-engine/examples/block_dispatch_benchmark.rs:1-94`.
- `crates/stela-engine/src/machine/engine/stored/tests.rs:922-1057`.
- `crates/stela-engine/src/machine/engine/fetch_costs.rs:293-371, 379-457, 466+`.
- `crates/stela-engine/src/translations/pack/codec.rs:230-299`.
- `crates/stela-engine/src/machine/engine/density.rs:1-112`.
- `crates/stela-engine/src/machine/engine/tests/reinstall_cost.rs:121-212`.
- `crates/stela-compiler/src/x86_64/executed_paths.rs:1-140+` and `census.rs`.
- `crates/stela-cli/src/restore/aea/benchmark.rs:24-119`.
- `xtask/src/cpu_reference.rs:1-249+`, `cpu_reference/record.rs:1-208`,
  `cpu_reference/apple-m1-pro.tsv:1-3` (host identity header).
- `crates/stela-boot-core/src/host_performance.rs:1-931`.
- GameReComp side: `tools/benchmarks.py:1-252`, `docs/BENCHMARKS.md`,
  `reports/games/results.json`, `tools/size_tier_report.py`,
  `tools/recompile_ledger.py`.

**No source repository was modified during this audit.**
