# S10 synthesis — portable improvements for GameReComp from Stela + PCRecomp

**Role:** read-only synthesis agent. No repository was edited.

**Sources examined**
- **STELA** — pinned `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe`, extracted at
  `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/stela`
  (Rust workspace, GPL-2.0+, `stela-compiler`, `stela-engine`, `stela-runtime`,
  `stela-fingerprint`, `stela-contracts`, `stela-loader`, driver crates).
- **GAMERECOMP** — worktree at
  `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/integration`,
  HEAD `bc12deed22d7cd593012d1cbfe48dd96235e9d70`
  (Rust orchestrator/GUI + Python RE backend `tools/` + C runtime `runtime/`).
- **PCRECOMP** — `sp00nznet/pcrecomp` `main` via GitHub read-only web/API
  (`README.md`, `api.github.com/repos/sp00nznet/pcrecomp/contents/runtime`).

**Date:** 2026-11-23.

---

## 1. Executive summary

Stela and GameReComp solve opposite translation directions on opposite hosts,
but they share one engineering problem: *produce a translated artifact, persist
it, and never let a stale or partially validated artifact be mistaken for
evidence*. Stela has the more mature answer to that problem. PCRecomp has the
more mature answer to *finding functions and testing lifter semantics*.
GameReComp has the most mature answer to *certifying readable source* and to
*deterministic run comparison* — it borrows PCRecomp's ideas and adds a
retraction rule PCRecomp does not have.

The five improvements below are the portable, low-risk subset of Stela's
techniques that fit GameReComp's existing three-tier architecture (Rust
orchestrator → Python backend → C runtime). All are host-agnostic: pure Python,
pure Rust, or plain C11 that compiles on macOS and Linux. None requires an
arm64 guest, an x86-64 host translator, a Metal driver, or any kernel interface.

| Rank | Improvement | Stela source technique | Primary GameReComp target | Risk |
|---|---|---|---|---|
| 1 | Content-addressed, provenance-bound **stage cache** with atomic publish + single-flight | `engine::prepare`, `cache_flight::Flight`, `translations::Record::decode` | `tools/autorecomp/pipeline.py`, new `tools/autorecomp/stage_cache.py` | Low–medium |
| 2 | **Cross-platform source/tool fingerprint** (CRLF-folded, path-keyed, locked deps) | `stela-fingerprint::source_sha256_with`, `compiler/build.rs` | new `tools/source_fingerprint.py`, `pipeline.py:_source_digest` | Low |
| 3 | **Instruction → generated-C source map** with completeness validation | `translations::source_map_fits`, `artifact::validate_source_map_geometry`, `dispatch::guest_instruction_for_native_offset` | `tools/lift/lift32.py`, `translator.py`, new `tools/lift/source_map.py` | Low |
| 4 | **Differential base-relocation proof** for the lifter / companion rebasing | `translations::prove`, `probe_displacements`, `Entry::stored` | new `tools/lift/rebase_proof.py`, `pipeline.py:_rebase_companion` | Low (new tool only) |
| 5 | **Structured refusals** with named values and causes in every report | `stela-contracts::Refusal`, `refuse!`, `require!`, `Within` | `pipeline.py:StageFailure`, `src/model.rs`, `tools/resultsdb.py` | Low |

Recommended *landing order* is **2 → 1 → 3 → 4 → 5**: rank 2 is a prerequisite
for a portable rank-1 cache key, but rank 1 delivers the largest measured win.

---

## 2. Three-way technique comparison

Legend: **yes** = implemented and used; **partial** = related mechanism exists
but narrower; **no** = absent; **ahead** = this project is stronger than the
other two.

| Technique | Stela | PCRecomp | GameReComp | Portability to GameReComp |
|---|---|---|---|---|
| Persistent, content-addressed translation cache keyed on guest bytes | **yes** — `engine::prepare`, `Identity{source_sha256, compiler_source_sha256, abi, target_cpu, memory_specialization_sha256}`; directory store keyed by `sha256(identity)` | **no** — each project rebuilds its `src/recomp/gen` tree | **partial** — `--reuse-build` only adopts an existing binary after matching `built_from_source_digest` (`pipeline.py:_adopt_reused_binary`); recover/translate always rerun | **high** — pure filesystem + hashing |
| Toolchain fingerprint bound into the cache key | **yes** — `stela-fingerprint` hashes all crate sources + locked dependency closure, CRLF-folded, skips test-only modules; injected as `SOURCE_SHA256` via `build.rs` | **no** | **partial** — `_source_digest` hashes raw bytes of all `src/` files, is not CRLF-normalised, and is only used for binary provenance | **high** |
| Single publisher per cache key (no duplicate work in batches) | **yes** — `cache_flight.rs: Flight::join/wait`, lock covers membership only, unrelated keys compile in parallel | **no** | **no** | **high** |
| Bounded reads, bounded decode allocation, whole-record checksum, structural consistency pass | **yes** — `engine::bounded_file`, `translations::decode_items` (`try_reserve_exact(count.min(256))`), `Reader::count`, `Record::decode` SHA-256 trailer, `Entry::consistent` | n/a (no persisted artifacts) | **partial** — ad-hoc `json.loads` throughout, no size caps, no schema-version gate on report reads | **high** |
| Fail-closed publication ("unsupported semantics never resolve or publish a cache entry") | **yes** — `dispatch.rs` test `unsupported_semantics_never_resolve_or_publish_a_cache_entry`; `prepare` compiles *before* writing | **yes** in spirit — `gen_unresolved_stubs` aborts, `gen_win16_stubs` refuses unknown purge | **yes** — PE32+ rejected in `stage_inspect`, `UNRESOLVED` stubs abort, no success stubs | already at parity |
| Instruction/byte → emitted-code source map with monotonic geometry validation | **yes** — `NativeBlock.source_map`, `validate_source_map_geometry`, `guest_instruction_for_native_offset` | **partial** — address-named `sub_XXXXXXXX` symbols and `--calltrace`, no persisted map manifest | **no** — there is no `source_map` in generated output or reports | **high** |
| Differential relocation proof (recompile at displaced addresses, assert only known fields move) | **yes** — `translations::prove`, two `PROBES`, `CLASS_SHIFT=47`, 8-byte relocation fields | **no** — rebasing handled by the PE loader | **no** — `_rebase_companion` rebases DLLs with no independent proof | **high in mechanism, not constants** |
| Structured, machine-readable refusals with named values and nested cause | **yes** — `Refusal`, `refuse!`, `require!`, `Within::within` (≤128 bytes, `no_std`) | strings / "refuses" in prose | **partial** — `StageFailure(message, data)` dict + free-text `reason` in `verification.json` | **high** |
| Per-function differential semantic oracle | n/a (different problem) | **yes** — `difftest.py --pe --old --changed`, `difftest16`, `difftest64` (host CPU) | **ahead** — `difftest.py --project --source` plus verdict vocabulary and the `reports/retracted/` rule that rejects what the oracle cannot see | n/a — GameReComp is already strongest |
| Deterministic replay for comparison | machine restore/replay for firmware handoff (`stela-runtime::restore`), different purpose | **yes** — `runtime/replay/replay.c`, frame-keyed, FNV `shot` checkpoints, virtual time | **partial→parity** — `input_rec.c` call-order input transcript, `clock.c` virtual time, `host_lite.c:fnv1a/hle_state_fingerprint`, `divergence.py` call-cap bisection | **low value — do not rebuild** |
| Function-catalog scoring against symbols | n/a | **yes** — `score_recovery.py` splits false positives into *split* vs *invented*; PDB/MAP reference tools | **yes** — same tool inherited from PCRecomp | n/a — already present |
| Architecture coverage | arm64e→x86-64 only | x86-16/32/64, NE/LE/Mach-O | PE32 only, fail-closed | **incompatible direction** — see §3 |
| Readable-source recovery + certification | n/a | n/a | **ahead** — evidence → name → generate → certify chain with `named_semantic/named_structured/no_evidence` and compile+execution gates | n/a — GameReComp unique |

**Key finding:** GameReComp does *not* need Stela's translator. It needs Stela's
*artifact discipline*. The four concrete gaps are: no stage cache, no portable
tool fingerprint, no instruction→C source map, and no independent proof of
image-base rebasing. The fifth gap is diagnostic: failures are strings, not
structured records.

---

## 3. Explicit rejections — incompatible ARM64 / macOS-kernel-specific features

The following Stela subsystems are **deliberately excluded** from the plan.
Copying them would be wrong-direction, host-specific, or kernel-specific, and
would violate the repository rule that `src/` stays generic and reusable.

| Stela feature | Where | Why rejected |
|---|---|---|
| AArch64/arm64e decoder and 3,000+ instruction encodings; AOT/JIT translation to x86-64 | `crates/stela-compiler/src/aarch64.rs`, `x86_64.rs`, `block.rs` | GameReComp lifts **x86 → C**, not arm64 → x86-64. Guest/host directions are inverted; macOS arm64 is the *host* here. |
| Host register mapping, `extern "sysv64"` translated callees, x86-64 memory checks | `stela-engine/src/lib.rs` (`translated_callee!`), `x86_64/*` | x86-64-SysV host ABI; GameReComp emits portable C compiled for macOS arm64/Linux. |
| arm64e pointer authentication, PAC keys, `FEAT_PAuth`, tagged pointers | `stela-engine/src/pauth.rs`, `stela-contracts/src/pauth.rs`, `cpu_features.rs` | ARM64 ISA feature with no analogue in PE32 x86 code. |
| Apple Silicon SoC model, MIDR/MPIDR, CLIDR_EL1/CCSIDR_EL1 cache-ID encoding, CTR_EL0, PMU/AMU, AMX, SVE/SME/MTE | `stela-soc`, `stela-soc-data`, `stela-contracts/src/{caches,identity,cpu_features}.rs`, `stela-engine/src/amx.rs`, `scalable.rs` | ARM system registers and Apple core topology; irrelevant to generated C. |
| XNU-kernel rehosting evidence (`do_cacheid`, `vm_page_init_local_q`, `VM_PAGE_PACKED_PTR_ALIGNMENT`) | `stela-contracts/src/caches.rs` | Requires the macOS kernel internals Stela models; GameReComp has no guest kernel. |
| UEFI application, boot policy, kernel collection loading, NVRAM, DeviceTree, AIC/DART/coprocessor, trust cache, IM4P/IM4M, local policy, SEP/firmware handoff | `crates/stela-boot*`, `stela-loader/*`, `stela-formats/{img4,im4p,local_policy,boot_bundle}.rs` | Apple/iOS boot chain + UEFI firmware; no counterpart in a PE32 recompilation pipeline. |
| Metal driver: AIR→NIR shader compilation, Metal3/Apple7 families, NVIDIA GPU driver, GPU DVFS/maintenance | `stela-driver-gpu`, `stela-mesa`, `stela-gpu-maintenance` | GPU/kernel-specific; GameReComp uses SDL2 via `runtime/win32hle`. |
| Translator-store *constants*: 4096-byte fetch pages, `CLASS_SHIFT=47`, `PROBES=[0x2aaa_aaaa_c000, 0x1555_5555_4000]`, 8-byte relocation fields | `stela-engine/src/translations.rs` | The *mechanism* (two differential probes, whole-record checksum, bounded decode) is portable; these numbers are not. Re-derive for x86 image bases (§5.4). |
| `no_std` / aborting-panic freestanding runtime constraints | `stela-engine/src/lib.rs` (`compile_error!` for freestanding without abort) | GameReComp's Rust core already targets native + `wasm32-unknown-unknown`; no bare-metal target. |

Also **excluded as already solved / out of scope**, not for incompatibility:
- Frame-keyed replay (`PCRecomp runtime/replay/`): GameReComp already has
  call-order transcripts, virtual clock, FNV checkpoints and bisection — do not
  build a second replay stack (`docs/UPSTREAM_REUSE_ISSUE3.md` forbids exactly
  that kind of duplication).
- PE32+ / x86-64 lifting: PCRecomp is ahead, but adding a whole new backend to
  GameReComp is neither low-risk nor a Stela technique. Keep the fail-closed
  rejection in `pipeline.py:Pipeline.stage_inspect`.
- D3D8/D3D9, DirectInput8, DRM unpackers: separate upstream-audit work, already
  tracked in `docs/ACCEPTANCE_LEDGER.md`.

---

## 4. Baseline facts used for "measurable effect"

These come from the checked-in reports, not estimates:

- `docs/BENCHMARKS.md`: 31 inputs measured; 15 reached a binary; 5 `partial`;
  10 `launch-failed`; 16 `not-run`; **`scenario-pass` = 0**; best score 93/100.
- GeneRally: 46 units, **373.9 MiB generated C**, 2,577 functions, 4,029,924
  instructions; adaptive build deadline ≈ 600 s + 5 s/MiB → ~2,470 s.
- Cave Story Doukutsu: 3,260 functions, 472,590 instructions, 8 units,
  50.7 MiB C. `docs/ARCHITECTURE.md` records 3310 low-level / 7 high-level in
  the matrix snapshot.
- Certification: `docs/VERIFICATION.md` records 446 call-bearing bodies → 28
  multi-function closures → **1 certified**; the README states **18/955**
  scoped differential certifications in the documented experiment.
- The only existing reuse is binary adoption:
  `pipeline.py:_adopt_reused_binary` compares `built_from_source_digest`; it
  never avoids recover/translate, and `--reuse-build` compile seconds are
  reported as `-` in `tools/benchmarks.py:collect`.

---

## 5. The five improvements

### 5.1 Rank 1 — Content-addressed, provenance-bound stage cache

**Stela technique.** `stela-engine/src/lib.rs:prepare()` builds an `Identity`
(source SHA-256, compiler source SHA-256, ABI string, target CPU, memory
specialization hash), keys a directory by `sha256(identity)`, validates an
existing entry with `validate()` (manifest checksum, native checksum, source-map
geometry, identity match) before using it, compiles **before** writing, writes
into a private `tempfile::tempdir_in(&root)`, and publishes with a single
`rename`. `cache_flight.rs` gives one publisher per canonical path. A corrupt
warm entry is rejected, not silently reused (`dispatch.rs` test
`warm_lookup_rejects_corruption_instead_of_reusing_a_stale_path`).

**GameReComp gap.** `Pipeline.stage_recover` and `Pipeline.stage_translate`
always rerun recursive descent and lifting for every invocation, including the
batch paths in `tools/game_matrix.py`, `tools/cli_matrix.py` and
`tools/autorecomp/regress.py`. Only the final binary can be reused.

**Portability / risk.** Pure Python + JSON + SHA-256 + `os.replace`; works on
macOS/Linux. Additive flag: default **off** until the fingerprint of rank 2
lands. No change to generated C or runtime; warm and cold runs must produce
byte-identical source files.

**Exact target files/functions**
- **New** `tools/autorecomp/stage_cache.py`:
  - `cache_key(pe_bytes, companions, options, tool_fingerprint) -> str` —
    SHA-256 over a canonical JSON of: input PE SHA-256, each companion DLL
    SHA-256 + rebased base, `split`, `split_bytes`, `highlevel` dir digest,
    `target`, runtime-tree digest, and `tool_fingerprint` (rank 2).
  - `StageCache.lookup(key, stage)` — bounded read (`<= MAX_MANIFEST_BYTES`,
    `<= MAX_STAGE_BYTES`) of `manifest.json` + `manifest.sha256.json`; verify
    SHA-256, `schema_version`, and every cached file's own hash; return `None`
    on any mismatch.
  - `StageCache.publish(key, stage, files)` — write into a sibling temp dir,
    fsync, `os.replace(temp, final)`; on `FileExistsError`, validate the winner
    instead of overwriting it.
  - `Flight.join/wait` — process-local `dict[path, threading.Lock]` mirroring
    `cache_flight.rs`, registry lock held only for membership.
- `tools/autorecomp/pipeline.py`:
  - `Pipeline.__init__` — new params `cache_dir: Path | None`,
    `use_cache: bool`; store `self._cache`.
  - `Pipeline.stage_recover` — after computing `functions`, publish the
    serialised function catalog; before calling `discover_functions`, attempt
    lookup and deserialise.
  - `Pipeline.stage_translate` — before lifting, attempt cache lookup of the
    generated `src/recomp/gen/` set; after `generate_recomp_files`, publish.
  - `Pipeline.stage_project` — cache the pinned runtime copy + `project.json`
    separately, keyed additionally on the runtime-tree digest.
  - `_stage(...)` / `run_pipeline` — record `cache_hit` in each stage's `data`.
  - `_sha256_file` / `_source_files` / `_source_digest` — reuse from rank 2.
- `tools/autorecomp/cli.py` — add `--cache-dir DIR`, `--no-cache` near
  `--reuse-build` (line ~439); wire into `Pipeline(...)`.
- `src/orchestrator.rs` — extend `ParsedCli`, `parse_pipeline_args` (the
  pass-through arm around lines 200–215 that forwards `--reuse-build`,
  `--import-trace`, etc. into `backend_args`), and `run_pipeline_cli` so the GUI
  and CLI expose the same switch. `src/config.rs:AppConfig` may carry a
  `RECOMP_CACHE_DIR` environment default, mirroring `RECOMP_ASSETS_DIR`.
- `tools/benchmarks.py:collect/render` — add a `cache_hit` column and exclude
  warm runs from compile-time statistics exactly as `--reuse-build` is excluded.
- `tools/autorecomp/regress.py` — fast tier runs twice (cold, warm) and asserts
  identical source digests and verification outcomes.

**Expected measurable effect**
- Warm `recover` + `translate` → ≥ 90 % reduction in stage seconds on
  Doukutsu (472k instructions) and GeneRally (4.03M instructions / 373.9 MiB).
- Warm full pipeline on a 50 MiB C target: from minutes to the compile/run cost
  only; repeat matrix runs avoid re-lifting the same PE.
- Zero change in `source_digest_sha256`, binary SHA-256, or verification status
  between cold and warm runs (this is the acceptance invariant).

**Correctness risks and mitigations**
| Risk | Mitigation |
|---|---|
| Stale entry after a lifter/runtime edit | Key includes rank-2 tool fingerprint over all backend sources + locked dependency versions; a miss is a hard cache miss, never a fallback. |
| Corrupt or truncated cache file | Bounded reads; per-file SHA-256; whole-manifest checksum; `schema_version` gate; on any failure **delete and recompute**, never reuse — Stela's `warm_lookup_rejects_corruption` rule. |
| Two matrix jobs write the same key | `Flight` single-publisher + atomic `os.replace`; loser validates the winner. |
| Cache key omits an input that affects output (assets dir, oracle, `--highlevel`) | Derive key from an explicit allowlist and add a test that mutates each field and asserts a different key; unknown flags that reach the backend force a miss. |
| Cache stores absolute paths from another host | Store paths relative to the project root; rebase on restore, matching Stela's page-relative stored fields. |
| Wrong result looks like a speedup | Cold/warm source digests and binary hashes must be byte-identical; add to `regress --tier fast`. |

**Benchmark recipe**
```sh
# cold
./GameReComp <Doukutsu.exe> --no-build --no-run --fresh-cache
# warm
./GameReComp <Doukutsu.exe> --no-build --no-run
python3 -m tools.benchmarks            # cache_hit column, cold timings only
python3 -m tools.autorecomp.regress --tier fast --check
sh verify.sh --fast
```
Then repeat on GeneRally using `--stages inspect,recover,translate,project`
and compare `reports/report.json` stage `seconds` for cold vs warm.

---

### 5.2 Rank 2 — Cross-platform source/tool fingerprint

**Stela technique.** `crates/stela-fingerprint/src/lib.rs:source_sha256_with`
hashes files keyed by `/`-separated relative path, folds CRLF→LF so the same
commit produces the same digest on Windows/macOS/Linux, skips hidden files,
optionally omits `#[cfg(test)]`-only modules, and optionally folds in only the
*locked closure* of named registry dependencies. `compiler/build.rs` injects
the result as `SOURCE_SHA256`, which `engine::translations::translator_id()`
folds into the persistent-cache translator ID.

**GameReComp gap.** `pipeline.py:_source_files` hashes every file under `src/`
with no line-ending normalisation, includes generated binary blobs
(`recomp_image.S`, `recomp_image.bin`), and `_source_digest` is used only for
binary provenance. A checkout with `core.autocrlf=true` yields a different
digest on macOS vs Linux, so `--reuse-build` and any future cache key are not
portable.

**Portability / risk.** Small, self-contained Python module. Must bump the
manifest schema version and provide a migration because existing
`reports/games/regression-baseline.json` and `manifest.json` files carry the
old digest.

**Exact target files/functions**
- **New** `tools/source_fingerprint.py`:
  - `source_sha256(root: Path, inputs: list[str], *, skip_test_modules=False,
    locked=()) -> str`, ported from Stela; `normalize(data: bytes) -> bytes`
    folding `\r\n`→`\n` but preserving lone `\r`; `locked_closure(lock_text,
    packages) -> list[str]`; path sort + dedup + length prefixing so
    `"a\0b"` cannot collide with `"a"` + `"b"`.
  - `digest_backend(repo_root: Path) -> str` over
    `tools/autorecomp`, `tools/lift`, `tools/disasm`, `tools/pe`,
    `runtime/win32hle`, `runtime/recomp32`, `templates`, `Cargo.toml`,
    `Cargo.lock` locked closure — this is the value rank 1 puts in the key.
- `tools/autorecomp/pipeline.py`:
  - `_source_files` — add `normalized_sha256` alongside `sha256`; exclude
    generated blobs from the *fingerprint* while keeping them in the physical
    manifest.
  - `_source_digest` — switch to `source_fingerprint.digest_backend()` for
    the provenance field; keep the legacy raw digest as
    `source_digest_legacy_sha256` for one release.
  - `_write_manifest` — add `fingerprint_schema: 2` and `tool_fingerprint`.
  - `_adopt_reused_binary` — accept either field during migration, but record
    which one was used and never accept a *different* new-style fingerprint.
- `tools/resultsdb.py:row_for` / `tools/benchmarks.py:collect` — carry the new
  field through without changing published numbers silently.
- `src/model.rs` (`ProjectModel`) / `src/gui/mod.rs` project cards — display
  the fingerprint, not just the binary hash.

**Expected measurable effect**
- Identical `tool_fingerprint` for the same commit checked out with
  `core.autocrlf=true` on macOS and with LF on Linux.
- Editing a `tools/lift/*.py` file changes the fingerprint; editing a
  `tests/test_*.py` file does not.
- `--reuse-build` accepted across those two checkouts; previously rejected.

**Correctness risks and mitigations**
| Risk | Mitigation |
|---|---|
| Changing the digest invalidates all baselines | Schema version 2 + legacy field + one-release migration; `regress --tier games --record` after landing. |
| Normalisation hides a real byte difference (lone CR, binary file) | Fold only `\r\n`; keep raw `sha256` in the manifest for byte-exact replay; unit test `lone_carriage_returns_are_kept` ported from Stela. |
| Fingerprint too broad → every edit misses cache | Use Stela's `skip_test_modules` and locked-dependency-closure options; include only inputs that can change emitted C. |
| Fingerprint too narrow → stale cache | Explicit allowlist test: for each tool file, mutate it in a temp checkout and assert a different digest. |

**Benchmark recipe**
```sh
python3 -m pytest tests/test_source_fingerprint.py -q   # new cross-checkout fixture
python3 -m tools.resultsdb --check
python3 -m tools.autorecomp.regress --tier games --check
sh verify.sh --fast
```

---

### 5.3 Rank 3 — Instruction → generated-C source map with completeness validation

**Stela technique.** Every `NativeBlock` carries
`source_map: Vec<(GuestAddress, usize)>`; `artifact::validate_source_map_geometry`
asserts one entry per 4-byte guest instruction, guest addresses increasing by
exactly 4 from the base, native offsets strictly increasing and inside the
code, and the first offset equal to the ABI-wrapper size. `dispatch.rs`
exposes `guest_instruction_for_native_offset` for fault/debug symbolisation,
and a malformed source map fails even when the manifest checksum was
recomputed (`dispatch.rs` test
`malformed_source_maps_fail_even_with_recomputed_manifest_checksum`).

**GameReComp gap.** Generated C is address-named (`sub_XXXXXXXX`), and
`reports/functions.json` records instruction counts, but there is no persisted
mapping from each guest instruction VA to the generated C file/line/statement.
Consequences: a lifter that silently drops an instruction is invisible until a
game fails 30,000 calls deep; crash logs cannot name the guest instruction;
certification covers a hand-picked set rather than "all recovered instructions".

**Portability / risk.** Additive artifact; no change to runtime or to emitted
C semantics. Keep the map as a generated **report**, never as source that the
AI readability pass or a human edit could invalidate silently.

**Exact target files/functions**
- **New** `tools/lift/source_map.py`:
  - `emit(functions: dict, gen_dir: Path) -> dict` — for each recovered
    function, record `(guest_va, c_file, c_line, statement_index)` using the
    line writer already in `tools/lift/lift32.py`.
  - `validate_map(map, functions, gen_dir) -> list[str]` — Stela geometry
    adapted to C: one row per decoded instruction; guest VAs strictly +opcode
    length; C lines strictly increasing; first line inside the emitted body;
    every referenced file exists. Return named violations, never `bool`.
  - `coverage(map, functions) -> {mapped, expected, dropped_addresses}`.
- `tools/lift/lift32.py` — `Lifter.lift_instruction` (line 757),
  `Lifter.lift_basic_block` (2283), `Lifter.lift_function` (2363): thread an
  optional `source_map` collector that appends a row per emitted instruction.
- `tools/lift/translator.py:generate_recomp_files` (line 312) — accept and
  forward the collector; write `src/recomp/gen/source_map.json`.
- `tools/autorecomp/pipeline.py:Pipeline.stage_translate` — call
  `validate_map`; on any violation raise `StageFailure` with the named counts
  (fail closed), and add `instruction_coverage` to the stage `data`.
- `tools/lift/difftest.py` — `build_project_cases` / `oracle` may use the map
  to select exactly the generated statements for a certified address;
  `reports/certified/` gains a `source_map.json` copy so a verdict names the
  source it actually certified (addresses `docs/VERIFICATION.md`'s "generator
  reproduces that exact C" limitation without overstating it).
- `tools/benchmarks.py:collect` — add `instruction_coverage` column.

**Expected measurable effect**
- `instruction_coverage = 100 %` for all functions categorised
  `LOW_LEVEL_FALLBACK`; `dropped_addresses` empty. Any nonzero value is a new,
  concrete lifter bug report.
- Crash/ICALL logs can map a native C line back to a guest VA and vice versa;
  `guest_instruction_for_native_offset` equivalent available offline.
- Certification can state "all N instructions of address X mapped and M
  compared" instead of only listing the named observables.

**Correctness risks and mitigations**
| Risk | Mitigation |
|---|---|
| Line numbers drift when the AI readability pass rewrites source | Map is regenerated from the pre-AI tree; `docs/AI_ASSIST_TOOLING.md` gate already rebuilds/re-runs; store the map hash in `manifest.json` and invalidate if `source_digest` changes. |
| x86 variable-length instructions break the "+4" rule | Use actual `insn.size` from Capstone; validate guest addresses by monotonic increase within the function's recorded range, not a fixed stride. |
| Fused/removed flag-only instructions produce no C statement | Permit an explicit `folded_into` field, like Stela's "instruction fused into the next has the next's offset"; require a named reason. |
| Map itself becomes a new failure source for normal runs | Stage failure only on structural violations; a missing optional map is allowed with `coverage: null` until all generators emit it. |

**Benchmark recipe**
```sh
python3 -m tools.lift.difftest --project projects/cavestory-doukutsu \
    --pe <Doukutsu.exe> --address 0x0041F9E0 \
    --source reports/certified/0x0041F9E0/0x0041F9E0.c \
    --closure 0x0041F9E0,0x00419890 --cases 8
python3 -m tools.autorecomp.regress --tier fast --check
sh verify.sh --fast
```
Compare the certified-address count and `dropped_addresses` before/after.

---

### 5.4 Rank 4 — Differential base-relocation proof for the lifter

**Stela technique.** `translations::prove` compiles the same block at the
original page and at two `probe_displacements`, then asserts the three results
differ **only** in 8-byte relocation fields, exit targets, and source-map guest
addresses — each by exactly the probe displacement. `Entry::stored` records the
fields; `Entry::rebase` re-adds the page; `Entry::consistent` rechecks geometry.
The compiler must prove its relocation metadata complete before a translated
block is persisted.

**GameReComp gap.** `pipeline.py:_rebase_companion` relocates bundled DLLs when
their preferred base collides (measured case: `gconvapi.dll`), and the PE loader
relocates the main image at runtime. Nothing independently proves that lifted
C depends on the image base only through the tokens the rebaser knows about.
A stray hard-coded absolute VA would survive every current test.

**Portability / risk.** A **new offline tool only**; no change to the shipped
pipeline until it passes. Reuse the existing Unicorn difftest harness as the
semantic half. Re-derive constants for x86: probe bases separated by e.g.
`±0x00400000` within the same 32-bit image class; do **not** copy Stela's 47-bit
class shift or its probe bit patterns.

**Exact target files/functions**
- **New** `tools/lift/rebase_proof.py`:
  - `lift_at_base(pe_data, info, iat_map, base) -> dict[addr, c_text]` — reuse
    `translator.make_read_va` and `translator.discover_functions` with a
    patched `info.image_base`.
  - `base_dependent_tokens(c_text) -> list[str]` — normalise symbol names and
    allowlisted macros (`ADDR`, `MEMn`, IAT slot names, section labels); keep
    numeric literals and `0x`-prefixed VAs.
  - `prove(base_a, base_b, delta) -> {ok, unexpected, moved_tokens}` — assert
    every differing numeric token equals `old + delta` or is in the allowlist.
  - CLI mirrors `difftest.py --old --changed`: `--old-base`, `--new-base`,
    `--functions N`, `--json`.
- `tools/autorecomp/pipeline.py:_rebase_companion` — when the proof tool is
  available, record its verdict in the companion's `data`; a failing verdict
  refuses the companion rather than lifting a mis-based image.
- `tools/lift/difftest.py` — `--pe --gen` already compares original bytes vs
  lifted C; add a `--rebase-delta` mode that runs the same comparison with the
  image mapped at a different base, proving *semantic* equivalence in addition
  to the token-level proof.
- `tools/pe/test_pe_rebase.py` — extend with a synthetic image containing a
  deliberate absolute VA to show the proof fails (negative test).

**Expected measurable effect**
- 0 unexpected base-dependent tokens across a sampled N functions (start N=200
  from the `callgraph.py --leaves` list, then N=all functions for one target).
- Companion rebasing on the measured collision case gains a recorded verdict
  instead of an assumption.
- A deliberately injected hard-coded VA is detected (negative test passes).

**Correctness risks and mitigations**
| Risk | Mitigation |
|---|---|
| False positives from debug strings, PDB-derived names, or format strings containing addresses | Normalise comments/strings first; maintain an explicit allowlist with a test per entry. |
| Probe base changes section alignment / relocation table path | Use a delta that preserves section alignment; refuse to run if `image_base % alignment` changes; record the delta in the verdict. |
| Unicorn cannot map the second base | Use the same base only for token proof; semantic half uses `difftest.py` with the original image base. Report the gap rather than claiming it. |
| Tool becomes a new gate that blocks valid projects | Land as opt-in with `--rebase-proof`; only make it a hard failure after a corpus run shows zero false positives. |

**Benchmark recipe**
```sh
python3 tools/disasm/callgraph.py <exe> --leaves 200 > leaves.txt
python3 -m tools.lift.rebase_proof <exe> --functions leaves.txt \
    --old-base <base> --new-base <base+delta> --json reports/rebase_proof.json
python3 -m pytest tests/test_pe_rebase.py -q
python3 -m tools.autorecomp.regress --tier fast --check
```

---

### 5.5 Rank 5 — Structured refusals with named values and causes

**Stela technique.** `stela-contracts/src/refusal.rs`: `Refusal { what:
&'static str, fields: Vec<(&'static str, Value)>, cause: Option<Box<Refusal>> }`,
built by `refuse!("what {offset:#x}", offset)` and asserted by `require!`;
`Within::within` attaches a context cause; `Refusal` is `no_std`, ≤128 bytes,
`Display`-stable, and equality is defined on the rendered message so a value
written into the template and one appended as a named field compare equal.
Every model refusal reports *what* was refused and the *values that decided it*.

**GameReComp gap.** `pipeline.py:StageFailure(message, data)` carries a dict,
but `stage_verify` reduces outcomes to a free-text `reason` and a `status`;
`tools/resultsdb.py:row_for` copies `reason` into the public `table.md`. The
corpus cannot distinguish "PE32+ rejected", "unresolved imports", "lifter
refused encoding", "build timed out" and "crashed" without parsing prose. This
weakens exactly the evidence discipline the project otherwise champions.

**Portability / risk.** Tiny Python dataclass + a Rust mirror in the portable
core; no runtime change; additive JSON fields with a schema bump.

**Exact target files/functions**
- **New** `tools/autorecomp/refusal.py`: `Refusal(what, fields, cause)`,
  `refuse(what, **fields)`, `require(cond, what, **fields)`, `within(context)`;
  stable `to_dict()` / `to_string()`.
- `tools/autorecomp/pipeline.py`:
  - `StageFailure` — add `refusal: Refusal | None`, keep `message` for
    compatibility; `__str__` renders the refusal.
  - `Pipeline.stage_inspect` — the PE32+ rejection becomes
    `refuse!("unsupported PE input", pe_type=..., machine=...)`.
  - `Pipeline.stage_compile` — timeout/refusal records `timeout_seconds`,
    `source_bytes`, `adaptive`.
  - `Pipeline.stage_verify` — add `verification["refusal"]` for
    `launch-failed`/`trace-diverged`; keep `status` and `reason` unchanged for
    one release.
  - `Pipeline.stage_report` / `_render_html` — render the refusal table.
- `src/model.rs` (`ProjectModel` and report status types) — add an optional
  `refusal` field with `#[serde(default)]`; `src/gui/pages.rs` shows it.
- `tools/resultsdb.py:row_for` and `tools/benchmarks.py:collect` — add a
  `refusal_what` column; keep `status` as the headline.
- `docs/VERIFICATION.md` — document the vocabulary and the rule that a
  `scenario-pass` never carries a refusal.

**Expected measurable effect**
- 100 % of `StageFailure`s in a corpus run carry `what` + ≥1 named field.
- `table.md` distinguishes the four failure classes above without prose
  parsing; "launch-failed" rows can be grouped by root cause.
- No change to verification statuses or scores (this is a reporting change,
  asserted byte-for-byte against the baseline).

**Correctness risks and mitigations**
| Risk | Mitigation |
|---|---|
| Schema change breaks GUI / results DB readers | All new fields `#[serde(default)]` / `.get(...)`, keep legacy `reason` for one release, add backward-compat fixtures. |
| Refusal text becomes an unstable API | Stable `what` template + typed named fields; tests assert on `what` and field values, not the rendered string. |
| Over-refusal: tool refuses inputs it used to process | Land as additive reporting only; do not route refusals to new control flow in the same change. |

**Benchmark recipe**
```sh
python3 -m pytest tests/test_refusal.py tests/test_game_matrix.py -q
python3 -m tools.resultsdb --check
python3 -m tools.autorecomp.regress --tier games --check
sh verify.sh --fast
```
Compare the distribution of `refusal_what` values before/after on the same
corpus, and assert scores/verification statuses are unchanged.

---

## 6. Cross-cutting rules for all five changes

1. **Fail closed, never guess.** A cache miss, malformed map, or failed proof
   must produce a named refusal and recomputation/refusal — never a plausible
   result. This is Stela's strongest transferable habit and matches
   `docs/VERIFICATION.md`'s existing fail-closed rules.
2. **Evidence, not proof.** A warm cache hit, a complete source map, or a
   passing relocation proof is *tooling evidence*. None of them may be reported
   as gameplay equivalence; only `verification.json` statuses make that claim,
   and `scenario-pass` still requires an external oracle. Do not let any of
   these changes inflate `pipeline.py:Pipeline.score`.
3. **No second runtime.** Do not add a replay engine, renderer, or guest CPU
   model. Reuse `runtime/win32hle/{input_rec,clock,host_lite}.c` and
   `tools/autorecomp/divergence.py` as-is.
4. **Host-agnostic paths and config.** Use environment variables or CLI
   arguments (e.g. `RECOMP_CACHE_DIR`), never a hard-coded user path, Wine
   prefix, or key binding — consistent with both `AGENTS.md` and GameReComp's
   own `src/config.rs` pattern.
5. **CLI parity.** Any new backend flag must be added in **both**
   `tools/autorecomp/cli.py` and `src/orchestrator.rs:parse_pipeline_args`, or
   the GUI and CLI will silently diverge.
6. **No repo edits in this task.** All paths above are *targets*, not changes;
   this agent modified only the audit output file.

---

## 7. What was deliberately *not* recommended

- Porting Stela's translator, register allocator, PAC, AMX, SME/SVE, UEFI
  loader, XNU hardware model, or Metal/NIR driver — see §3.
- Adding a second deterministic-replay stack from PCRecomp's
  `runtime/replay/replay.c` — GameReComp already has call-order transcripts,
  virtual time, FNV checkpoints and bisection; the gap is an original-Windows
  oracle, which tooling cannot manufacture.
- Adding PE32+/x86-64 lifting in this batch — high value but not low risk, and
  not a Stela technique. Keep the explicit fail-closed rejection.
- Changing `docs/ACCEPTANCE_LEDGER.md` claims or publishing new gameplay
  success from any of the five changes; none produces a scenario oracle.

---

## 8. Evidence index (files actually read)

**Stela:** `README.md`, `Cargo.toml`, `crates/stela-fingerprint/src/lib.rs`,
`crates/stela-compiler/{build.rs,src/lib.rs}`,
`crates/stela-engine/src/{lib.rs,artifact.rs,cache_flight.rs,dispatch.rs,translations.rs}`,
`crates/stela-contracts/src/{refusal.rs,identity.rs,caches.rs,cpu_features.rs,crc.rs}`,
`crates/stela-runtime/src/{translation_store.rs,restore.rs}`,
`crates/stela-dev/src/{compile.rs,execute.rs}`.

**GameReComp:** `README.md`, `Cargo.toml`, `verify.sh`,
`docs/{ARCHITECTURE,PIPELINE,VERIFICATION,BENCHMARKS,SOURCE_PROVENANCE,ACCEPTANCE_LEDGER,ISSUES_AUDIT,UPSTREAM_REUSE_ISSUE3}.md`,
`src/{lib.rs,orchestrator.rs,config.rs}`,
`tools/autorecomp/{pipeline.py,cli.py,regress.py,divergence.py,rename_gen.py}`,
`tools/lift/{lift32.py,translator.py,difftest.py}`,
`tools/{benchmarks.py,resultsdb.py,recompile_ledger.py}`,
`runtime/win32hle/{input_rec.c,clock.c,host_lite.c}`.

**PCRecomp:** `README.md` (raw), `api.github.com/repos/sp00nznet/pcrecomp/contents/runtime` (tree confirmation).
