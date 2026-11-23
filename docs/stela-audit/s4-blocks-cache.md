# S4 audit — translation blocks, block lookup/dispatch, translation caching & invalidation

**Scope (read-only):** Stela snapshot pinned `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe`
(`/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/stela`) vs GameReComp
(`/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/integration`).
No repository was edited. Focus files requested: `tools/lift/translator.py` and generated
`recomp_dispatch.c` (the latter is generated, not checked in; generators are
`translator.py` and `tools/lift/generate.py`).

---

## 1. Executive summary

Stela is a **dynamic binary translator / JIT** (guest ARM64 code translated to host x86-64,
plus AArch64/SME models) with a four-layer block cache and a page/shard-indexed,
generation-checked block dispatcher. GameReComp is a **static AOT recompiler**:
each discovered guest *function* becomes one C function, and a single immutable, sorted
`{address, func}` table is searched with a binary search at runtime. GameReComp has **no
runtime translation cache and no cache invalidation at all**; "invalidation" is only a
full rebuild of generated sources.

The most transferable Stela ideas are therefore **build-time cache identity/fingerprinting,
content-addressed artifacts, atomic publication, and provenance/manifest validation** —
not the JIT lookup data structures. Direct source reuse is **licence-gated** (see §7):
Stela's engine is GPL-2.0-or-later / GPL-2.0-only in parts, GameReComp is AGPL-3.0-only;
recommend reimplementation of algorithms, not copying of code.

---

## 2. Stela — translation blocks

### Compiler-side unit (`stela-compiler`)

| Item | Location | Notes |
|---|---|---|
| Block translation entry ABI | `crates/stela-compiler/src/block.rs` (module doc, lines 1–6) | `BlockExit function(GuestState *state)`. Emitted code **never jumps to a guest PC** — every control-flow change exits to the dispatcher. SVC/BRK exit at a synchronous-trap boundary. |
| Unit size bound | `block.rs::MAX_BLOCK_INSTRUCTIONS = 256` (line 58) | Max input window; terminator may consume a shorter prefix. `MAX_BLOCK_BYTES = 256*4 = 1 KiB` (`engine/block_inputs.rs:15`). |
| Exit taxonomy | `block.rs::ExitKind` (lines 60–78) | Fallthrough, Direct, Indirect, Call, Return, Trap, PointerAuthentication, Clock, ThreadRegister, FloatingPoint, ScalableNumerical, System, Amx. Serialized as a `u64` tag in `BlockExit { next_pc, kind }` (`repr(C)`, lines 209–215). |
| Block analysis | `block.rs::{analyze, analyze_with_profile, source_bytes, StaticSuccessor, DynamicEdge, BlockAnalysis}` (re-exported from `block/analysis.rs`, lines 24–27) | Computes admitted input, terminator, static successors, dynamic edges. |
| Compiled result | `block.rs::CompiledBlock` (lines 217+) | `native: NativeBlock`, `source_bytes`, `direct_exits: Vec<DirectExit>`, `fault_recovery: Vec<FaultRecovery>`, loop budget, etc. |
| Profile / context identity | `block.rs::{BlockProfile, Features}` (`block/features.rs`), `BlockProfile::abi()` | The ABI string, plus source hashes, forms the translator id (§4). |

### Engine-side installed block (`stela-engine`)

| Item | Location | Notes |
|---|---|---|
| `struct Block` | `crates/stela-engine/src/machine/engine.rs:449–479` | Fields: `pc`, `physical`, `code: NativeCode`, `instructions: u16`, `variant: VariantId`, `successors`, `dynamic_site`, `landing`, `terminal`, `source_hash` (FNV-1a of admitted bytes, `source_hash()` at :538), `installer` (PE id), `part` (arena part), `device_stub`. Size asserted `<= 72` bytes (:482) — "millions in all". |
| Key of an installed block | `(pc, physical, variant)` | `physical` is guest-physical; `pc` disambiguates multiple mappings/aliases of one frame; `variant: VariantId(u32)` (:694) is a compile-context variant. |
| Fetch geometry | `machine/engine/block_inputs.rs:12–15` | `PROFILE = BlockProfile::new(Features::SYSTEM_MODE)`, `INSTRUCTION = 4`, `FETCH_PAGE = 4096`, `MAX_BLOCK_BYTES = 1024`. |
| Block chaining | `x86_64::chain::{DirectExit, LookupTable, TableReader}` used in `board.rs:46`, `code_cache.rs` | Direct exits to static successors are patched (`chain::patch_word`, `patch_point`) so hot paths chain without returning to the interpreter — a threaded-code-style dispatch optimization. |

### Higher-level block executors

- `stela-engine/src/blocks.rs::BlockRuntime` (lines 53–82) — hosted, serialized
  (`PhantomData<Rc<()>>`, `&mut` for registration/resolution/execution) registration and
  execution of "leaf" blocks. `register_code()` (149) and `unregister_code()` (181) advance a
  generation even for byte-identical replacement; `load()` (197) records `disk_cache_hits` vs
  `compiled_blocks` in `RunOutcome`.
- `stela-engine/src/dispatch.rs::LeafDispatcher` (65–165) — another process-local registry
  with the same generation/invalidation discipline (§5).

---

## 3. Stela — block lookup / dispatch

Three cooperating mechanisms, all in `stela-engine`:

1. **Page-indexed block index with lock-free fast path**
   - `machine/engine/block_index.rs::BlockIndex` (70–388): installed blocks grouped by
     **physical page**, each page's `Entry { offset: u16, tag: u16, slot: u32 }` (44–49) sorted
     by offset; `tag` is a 16-bit hash of the PC (`pc_tag()`, 59–61) so aliases of one frame at
     different PCs mostly differ in the tag. Direct-mapped `front` array of 256
     `(frame, entries)` pairs (`FRONT`, `front_slot`, `page_of`, 24–26, 108–120) makes repeat
     lookups O(1) before touching the `BTreeMap`.
   - `BlockIndex::get()` (136–151) narrows by offset, then tag, then compares exact
     `pc` and `variant` — so the key is effectively `(physical page, page offset, PC tag, pc, variant)`.
   - `BlockIndex::insert/forget/forget_marked/forget_page/clear/sweep` (210–361) keep the
     index, finder and sweeps consistent; `shard_of(physical)` (34–36) hashes frames over
     `SHARDS = 64` locks (`SHARD_BITS`, 28–30); `at()` (124–132) uses `partition_point` binary search.
2. **Lock-free `Finder`** (block_index.rs:390–523): `heads: [AtomicU32]` hash buckets (one per
   16 slots, clamped 2^10..2^20), `next`/`listed` per-slot atomic arrays, `partial` and `pages`
    counters. `find()` (453–477) walks at most `FIND_STEPS = 32` links without the board lock;
   returns `None` (=> "ask the locked index") when partial or too long. `link`/`unlink` (481–522)
   are writer-only under the shard lock. A slot is reused only after its grace (§5), so a reader
   inside its critical section never sees its slot rejoin another list (doc 394–401).
   - Unified entry: `board.rs::Code::find(pc, physical, variant)` (880–893) = `finder.find()`
     first, falling back to `page_at(...).index.get()` under the shard lock.
   - Scaling test `many_aliases_of_one_frame_are_found_without_the_lock` (block_index.rs:663–701)
     verifies 1024 aliases of one frame are all found lock-free (fix for an old build that
     shared one list and fell back after 32 aliases).
3. **Block table + lookup dispatchers + jump cache** (`board.rs::Cache` 373–388):
   `targets: LookupTable` (compiler `x86_64::chain`), `dispatchers: Vec<(u32, [Option<u64>; 4])>`,
   all mutated under the block-table lock (`CodeLock<Cache>`). `LockSite` enum (530–580) counts
   `Fetch, Install, Table, Link, Dynamic, Evict, Invalidate, Discard, ...` — note the explicit
   **`Invalidate` site**. Per-machine *jump cache* entries carry a generation
   (`code_cache.rs::jump_generation()`, 1589–1593) and are invalidated without a per-page tag
   ("The jump cache holds no generation per page", 682).

**No global function-address dispatch table exists in Stela.** Dispatch is a fetch-driven,
context-aware block lookup keyed by *physical page + PC + variant*, not by a static list of
guest function entry addresses. The x86-64 engine's emitted code handles control flow by
returning `BlockExit` to the runtime; chaining patches are the only direct transfers.

---

## 4. Stela — translation caching (four layers)

### Layer A — hosted leaf/block file cache (directory per artifact)
- `stela-engine/src/lib.rs::prepare()` (274–348): cache key `sha256(serde_json(identity))`
  where `Identity` includes `schema_version`, `source_sha256`, `compiler_source_sha256`,
  `guest_base`, `abi`, `target_cpu`, optional `memory_specialization_sha256`.
  Each artifact is a directory `{key}/` containing `native.bin`, `manifest.json`,
  `manifest.sha256.json`; on hit `validate()` (123–146) re-checks manifest checksum and
  preparation metadata/native bytes before use. Publication is **compile-to-temp then
  `rename()`** (318–332) with `std::fs::rename`, so entries are atomic.
- `stela-engine/src/cache_flight.rs::{FLIGHTS, Flight::join/wait, Drop}` (1–65): per-directory
  `Mutex<()>` "single-flight" gate so one publisher compiles per canonical key while unrelated
  keys compile in parallel; poison-tolerant (`PoisonError::into_inner`).
- `LeafDispatcher::resolve()` (dispatch.rs:128–148) revalidates persisted checksums on **every**
  lookup, including disk hits, and checks source-map integrity (`validate_source_map()`, 167–204).
- Bounds: `MAX_CACHE_NATIVE_BYTES = MAX_CACHE_MANIFEST_BYTES = 64 MiB`
  (`artifact/prepare.rs:6–7`).
- `stela-engine/src/blocks.rs::BlockRuntime` uses the same artifact cache for AOT/leaf blocks
  (`aot.rs::ArtifactPreparation::FileCache`).

### Layer B — persistent page-record translation store (`stela-engine/src/translations.rs`, `translations/pack.rs`, `translations/writer.rs`)
- Identity: `translator_id()` (translations.rs:71–86) = SHA-256 over `RECORD_MAGIC`, `SCHEMA = 5`,
  compiler `BlockProfile::abi()`, `stela_compiler::SOURCE_SHA256` and
  `ENGINE_SOURCE_SHA256`. The latter is produced by `crates/stela-engine/build.rs`, which
  fingerprints exactly the 9 files that decide generated code (`TRANSLATION_INPUTS`:
  `build.rs`, `machine/engine/block_inputs.rs`, `prepare.rs`, `stored.rs`, `translations.rs`,
  `translations/`, `stela-contracts/src/chip.rs`, `chip/`, `cpu_features.rs`), skipping test
  modules. A unit test (`translations/tests.rs:765–800`) guards that those files name no other
  engine module — i.e. the cache-invalidating dependency set is explicit and enforced.
- Record key = SHA-256 of the **4096-byte guest fetch page** (`PAGE_BYTES = 4096`, line 33);
  each `Entry` within a page is keyed by `(offset: u16, context: Digest)` (310–330), where
  `context` is the compile context digest. `Record::{find, insert, merge}` (617–679) use a
  hybrid scan (<=32 entries) / binary search (`partition_point`) strategy; encode/decode
  (681–815) with magic, schema, translator id, key, length, and SHA-256 trailer.
- Relocation: native code is persisted with guest-address fields stored as `value - page`;
  `Entry::rebase()` (367–410) / `relocate()` (434–440) add the page back on install.
  `prove()` (1003–1098), driven by `probe_displacements()` (986–1001), **recompiles the block
  at two displaced pages** and verifies the fields that move are exactly the recorded
  relocation offsets. `stored_entry()` (machine/engine/stored.rs:50–95) checks every entry in
  test/debug builds, 1-in-`CHECK_INTERVAL = 1024` under `native-profile`, and trusts the
  compiler in release; failures are counted (`EntryChecks`) and never stored.
- Container: `translations/pack.rs` — `stela.efi` pack (`MAGIC "STLTPACK"`, schema 1/2,
  counted variants), a journal of pack segments + used-key lists, one per boot. Records no
  boot used for `MAX_UNUSED_BOOTS = 3` consecutive boots are dropped at merge; `MAX_RECORDS =
  1<<22`, `MAX_SEGMENTS = 8`, `MAX_USED_LISTS = 8`, `MAX_USED_KEYS = 1<<17`. Header/index SHA-256,
  per-record trailer checksum, and index age/use bytes.
- In-memory decoded cache: `pack.rs::DecodedCache` (2463–2555) — **LRU by retained bytes**
  (`BTreeMap<Digest, (Arc<StoredRecord>, usize, u64)>` + `uses: BTreeMap<u64, Digest>`), default
  `DECODED_BYTES = 8 MiB` (74–76), one record at most 1/8 of the bound; `hold()` keeps `Weak`
  refs so a record another machine still holds is not decoded twice; `insert()`/`shrink()`
  return evictions so callers drop them outside the store lock. `Store::set_bounds()` (2934–2938)
  dynamically resizes it. `StoredRecord` (translations/stored.rs:14–177) keeps encoded parts
  plus a sorted index and decodes **one entry on demand** (`find()`, 124–132).
- Shared same-process prepared blocks: `machine/engine/stored.rs::{Emission, SharedTranslation,
  Looked, Translations trait, Offer, StoreEvent}` (97–337). `Translations::{record, lookup,
  prepared, offer, refilled}` is the store↔engine contract: `Looked::{Found, Absent, Awaited}`
  distinguishes "truly missing" from "being read", so a miss page is not re-hashed every fetch.
  `SharedTranslation::matches()` (183–197) re-validates key, pc, context, page, admitted bytes
  and source-map first entry before reuse. `SharedEntry`/`accept_shared()` in pack.rs (2381,
  3006) bound the shared cache by capacity.
- Other bounds: `ADDED_BYTES = 64 MiB`, `MISS_SLOTS = 4096`, `READ_BYTES = 1 MiB`,
  `KEPT_PAGES`/`BOUND` for rebase entries and known keys.

### Layer C — native code arena (executable code cache)
- `machine/engine/board.rs::{Board, Code, Ring, Slots, Records, Arena, Grace, Cache, Shard,
  Retirement, PageRetirement}` (module doc 1–32). Code lives in arena parts; `Ring` has young
  and tenured generations, `Slots` recycles slot numbers only after grace, `Records` are per-slot
  lock-free records. `CodeSlots` (`code_slots.rs`) maps arena parts to block slots for bounded
  reclamation, falling back to a linear scan if the optional index cannot represent a range.
- `code_cache.rs::{install_native, retire_part, retire_steps, retire_step, unindex, cut_links_into,
  retire_blocks, finish_retirement, wait_grace, evict_code, detach, discard_code, discard_blocks}`
  (783–1777). Retirement is **incremental and bounded** ("a page's blocks go together", one
  bounded step per install), with `STEP_VISITS`-sized work; `reclaim_code/reclaim_leaves/
  reclaim_for_memory` (700–755) reclaim by ring age or memory pressure. Host `IC IVAU` cache
  maintenance is modelled (`tlb_maintained`, `fetch_epoch`, `sync_host_regime`).

### Layer D — per-machine fetch/translation caches
- `code_cache.rs::{forget_spaces, forget_asid, forget_contexts, forget_fetches, forget_lookups,
  controls_changed, layout_changed, permissions_changed, translations_changed, fetch_permissions_changed,
  invalidate_code_page, invalidate_posted_code_page, forget_code_page_here, forget_page_caches}`
  (236–698) — per-PE page records, direct-exit waits, known-key slots and jump-cache entries, all
  tagged by generation/epoch.

---

## 5. Stela — cache invalidation & coherence

| Mechanism | Location | Rule |
|---|---|---|
| Generation bump on source replacement | `lib.rs::register_code` (blocks.rs:149–179), `dispatch.rs::register` (87–119) | Re-registering the same base **always** increments `next_generation`, even with identical bytes; old `LoadedBlock`s / handles are dropped or become stale. Exhaustion is a refusal, never a wrap. |
| Handle validation | `dispatch.rs::is_current()` (153–164) | Requires same issuer `Arc`, same address space, same mapping generation, same source SHA-256. `resolve()` re-validates persisted checksums (128–148). A stale handle is inspectable but never a live target. |
| Code-page invalidation | `code_cache.rs::invalidate_code_page()` (639–666) | Under shard lock: mark each block gone (`Gone::Invalidated`), unlink sources, `blocks.advance_epoch()`, forget table targets for the frame, `index.forget_page()`, then drop this PE's page/jump caches. `invalidate_posted_code_page()` (674–676) covers another PE publishing a block compiled from stale bytes. |
| Cross-PE quiescence | `board.rs::Grace` (66–160), `code_cache.rs::wait_grace` (1542) | A slot/code range is reused only once every PE was outside its section at a snapshot instant or has left since; no PE waits while inside a section (deadlock-free). `code_generation += 1` and `code_invalidations += 1` on a local invalidation (682–684). |
| Translator fingerprint | `translations.rs::translator_id()` + `stela-engine/build.rs` | Any change to compiler ABI/source or the 9 fingerprinted engine files changes `translator` digest, so all stored records fail the `decode` translator check (translations.rs:782–784; pack.rs `prefix_bytes`, 269–282). |
| Content checksums & identity checks | `Record::decode_checked` (764–815), `StoredRecord::add` (stored.rs:57–120), `lib.rs::validate` (123–146), `pack.rs` header/index hashes | Magic, schema, translator id, page key, length, whole-record SHA-256 trailer, per-entry `consistent()`, manifest checksum. `decode_checked(check=false)` skips only **already-performed** checks on immutable bytes, never bounds checks. |
| Page-relative relocation + proof | `Entry::rebase/relocate`, `prove()` | A stored template is only installable if admitted bytes match exactly and the address-dependent fields are provably the recorded relocations; otherwise it is not stored. |
| TTL / usage eviction in pack | `pack.rs::MAX_UNUSED_BOOTS = 3`, index age byte | Records unused for 3 boots are dropped from the next merged pack; hot records are laid out in first-use order. |
| LRU eviction | `DecodedCache::insert/shrink` (2515–2554), `DECODED_BYTES = 8 MiB` | Least-recently-used decoded records are evicted by retained bytes; evictees are returned for dropping outside the lock. |
| Epoch counts | `board.rs::Code::epoch`, `CodeReport::forget_all` (659), `code_cache::jump_generation` | A "forget every block" event bumps the board epoch, invalidating all per-PE jump-cache generations at once. |

---

## 6. GameReComp — function units and dispatch (the comparison target)

### Function units
- `tools/lift/translator.py::generate_recomp_files()` (312–478) — discovers functions
  (`discover_functions()`, 70–126; seeds = PE entry point + exports + EH-recovered funclets),
  lifts each via `Lifter.lift_function()` (from `tools/lift/lift32.py`), and writes them into
  `recomp_%04d.c` units. Splitting is by **function count** (`split_size`, default 1000) **or a
  byte cap** (`MAX_UNIT_BYTES = 8 MiB`, `SOURCE_UNIT_BYTES_ENV = "RECOMP_SOURCE_UNIT_BYTES"`,
  `resolve_unit_byte_cap()`, `build_source_budget()`, 142–293). A single function larger than
  the cap is emitted whole and reported (`single_oversized_function`) — never truncated.
  Emits `recomp_units.json` (measured `os.path.getsize` per unit), `recomp_funcs.h` and
  `recomp_dispatch.c`. `tools/lift/generate.py` is the faster linear-sweep variant with the
  same outputs (dispatch at 1028–1036).
- High-level replacement: `translator.py:382–419` — a replaced function's mechanical body is
  still emitted as `<name>__lowlevel` and compiled; the dispatch entry points at the
  high-level replacement symbol. This is the closest analogue to Stela's **variant** concept.
- Lifting failures emit an `abort()` stub rather than an empty function (404–413) — fail-loud,
  analogous in spirit to Stela's "unsupported semantics never resolve or publish a cache entry"
  (`dispatch.rs` test, 315–326).

### Generated `recomp_dispatch.c` (generator: translator.py:465–477 / generate.py:1028–1036)
```c
/* Auto-generated dispatch table - DO NOT EDIT */
#include "recomp_types.h"
#include "recomp_funcs.h"
const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { 0x00401000u, sub_00401000 }, ...   /* sorted by guest VA */
};
const uint32_t recomp_dispatch_count = N;
```
- Entry type: `recomp_types.h:84–92` — `typedef void (*recomp_func_t)(void);`
  `struct { uint32_t address; recomp_func_t func; } recomp_dispatch_entry_t;`
- Lookup: `runtime/recomp32/main.c::recomp_lookup()` (107–123) — **plain binary search** over
  the sorted table; returns `NULL` on misses. `recomp_lookup_manual()` (131–138, sentinel array,
  count 0) and `recomp_lookup_import()` (145–152, `MAX_IMPORT_BRIDGES = 256`) are linear scans.
  Same binary-search pattern in `runtime/nextstep/ns_core.c:269–272`; DOS32 holds a `dispatch`
  pointer defaulting to `recomp_dispatch_table` (`runtime/dos32/dos32.c:247, 1553`); Win16 uses a
  weak `recomp_dispatch_lookup()` (`runtime/win16/recomp_stubs.c:74–95`).
- Properties: immutable, process-lifetime, one entry per guest function address, no context,
  no variants in the table itself, no generation, no cache, no invalidation, no checksum at
  runtime. The entire table is regenerated and relinked whenever any function changes.

### GameReComp caching / invalidation
- **No runtime translation cache.** PE/SHA evidence exists only in offline test tooling
  (`tools/lift/difftest.py:1736–1891` records `pe_sha256` / `bytes_sha256`) and build budget
  reports (`recomp_units.json`, `source_budget.txt`). Any code change → full regeneration.
- `.pytest_cache/` and `__pycache__/` are incidental Python caches, not translation caches.

---

## 7. Licence, dependencies, portability

| Property | Stela (relevant crates) | GameReComp |
|---|---|---|
| Licence | Repo: **GPL-2.0-or-later** (`LICENSE`, `NOTICE`). `stela-engine` = `GPL-2.0-or-later AND BSD-3-Clause`; `stela-compiler` = `GPL-2.0-or-later AND MIT`. Shipped `stela.efi` is **GPL-2.0-only** (includes Linux kernel ports) — avoid entirely. CLI links Apache-2.0 → GPL-3.0-or-later. | **AGPL-3.0-only** (`LICENSE`, `NOTICE`); a few prior-MIT files (LICENSES/GameReComp-prior-MIT.txt) and vendored zlib/MIT notices. |
| Consequence | Copying Stela engine/compiler source into GameReComp is **not licence-clean** (GPL-2.0-or-later vs AGPL-3.0-only; AGPL §13 combination is nuanced). Treat designs as prior art and **reimplement**; obtain legal review before any verbatim port. Never lift code from `stela-linux*`, `stela-boot-core` or driver crates (GPL-2.0-only). | — |
| Key dependencies | `stela-engine`: `serde`, `serde_json`, `hex`, `miniz_oxide`, `tempfile`/`libc` (std), sibling crates `stela-compiler`, `stela-contracts`, `stela-crypto`, `stela-formats`; build-dep `stela-fingerprint`. `stela-compiler`: serde, sibling compiler support. | Python 3 + `capstone` (disassembly), generated C compiled by host toolchain; runtime C uses Win32/POSIX host APIs. |
| Portability | Core crates are `no_std + alloc` capable (`#[cfg(not(feature = "std"))] use alloc::{...}`) with platform shims (`execution/platform/{posix,freestanding,unsupported}.rs`, `platform/sync/freestanding.rs`). However **native execution is host-specific**: host x86-64 code generation, W^X mappings, `host_memory` cfg (POSIX only), IC IVAU/TLB modelling. The persistent pack/file cache needs `std`/filesystem or the UEFI `stela.efi` container. | Python tooling and generated C run on Linux/macOS (per AGENTS.md, macOS verification with CMake/CTest); Win32 DLL targets via `tools/build-win32-dll.sh`. Inherently source-portable. |

---

## 8. Side-by-side mapping

| Concern | Stela | GameReComp |
|---|---|---|
| Unit of translation | Basic block, <=256 instructions / 1 KiB, ends at control flow; `(physical, pc, variant)` key | Whole guest function; one C function per address; address is the only key |
| Dispatch structure | Fetch-driven: lock-free `Finder` hash → sharded `BlockIndex` (page → offset → PC tag → exact pc/variant); plus block table, lookup dispatchers, jump cache and patched direct exits | Static sorted `recomp_dispatch_table[]`, binary search `recomp_lookup()`, plus small linear manual/import tables |
| Cache identity | SHA-256 page key + translator id (9-file source fingerprint) + context digest + exact admitted-input bytes | None (implicit: whole regeneration) |
| Caching layers | 4: directory artifact cache, persistent page-record pack + decoded LRU, native arena, per-PE fetch/jump caches | None at runtime |
| Invalidation | Generations, `is_current`, per-page invalidate, grace/quiescence, epochs, usage TTL, checksums, `prove()` | Full rebuild; highlevel/manual overrides replace entries at generation time |
| Failure policy | Unsupported semantics never publish a cache entry; corruption → refuse, never reuse | Lift error → `abort()` stub printed to stderr; build fails loudly |

---

## 9. Concrete adaptation candidates (ideas to reimplement, not copy)

Ranked by fit with GameReComp's static-AOT design and the AGENTS.md constraints
(`src/` generic, demos hold menu items, no hard-coded user paths, verify before claiming success).

1. **Content-addressed lifted-unit cache + translator fingerprint** (highest value).
   *Stela source:* `lib.rs::prepare()`/`validate()` (274–348, 123–146), `translator_id()` and
   `build.rs::TRANSLATION_INPUTS`, `cache_flight.rs::Flight`.
   *Adaptation:* in `tools/lift/translator.py::generate_recomp_files()` / `generate.py`,
   compute a fingerprint \(F = H(\text{lift32.py, translator.py/generate.py, options, ABI header})\)
   once, and per function \(k = H(F \,\|\, \text{admitted function bytes} \,\|\, \text{flags})\).
   Store each lifted body under `k` with a small JSON manifest (schema, F, k, function VA,
   byte length, tool version); on a hit validate the manifest checksum **and** that the function
   bytes still hash to k; publish via compile-to-temp + atomic `os.replace`. Serialize only
   per-key (single-flight) so unaffected functions lift in parallel. Keep the existing
   `--split-bytes` budget logic on the *emitted* units (measured `os.path.getsize`), so the
   cache does not change unit boundaries. Reimplement; do not vendor Stela code.
2. **Generated-output provenance manifest / stale-build detection** (low risk, high hygiene).
   *Stela source:* checksums + `translator` field in `Record::encode/decode`, `Differences`
   in `difftest.py` already record `pe_sha256`.
   *Adaptation:* write `recomp_manifest.json` next to `recomp_dispatch.c` containing PE SHA-256
   (already computed offline in difftest), translator fingerprint, per-unit byte counts, and
   per-dispatch-entry function hash; make `verify.sh`/CMake check the manifest before consuming
   generated sources. This replaces Stela's "generation + checksum + identity" triple with a
   build-time equivalent and directly serves the 100%-fidelity evidence trail.
3. **Variant-aware dispatch entries** (targeted).
   *Stela source:* `VariantId`, `Entry.context`, `highlevel` replacement in `translator.py:382–419`.
   *Adaptation:* when a high-level replacement exists, keep the mechanical body compiled but make
   the dispatch entry a small struct with `{ address, primary, lowlevel, digest }` instead of
   emitting a separate `__lowlevel` declaration only. This makes the fallback selectable at
   runtime for differential testing (matching Stela's "same key, different compile context"
   model) without changing the binary-search lookup by address. Must update `recomp_types.h`,
   `generate.py`, `generate64.py`, `runtime/recomp32/main.c` and the readability gates test
   (`tests/test_readability.py:66`).
4. **Direct-mapped/perfect-hash dispatch instead of binary search** (optional performance).
   *Stela source:* `BlockIndex::front` direct-mapped page cache (256 entries) and `Finder` hash
   buckets with bounded probe length.
   *Adaptation:* since GameReComp's table is immutable, a generated **minimal perfect hash** or a
   direct-mapped table indexed by `(VA >> 2) & mask` with a small overflow list would beat
   binary search (482-entry search cited in `reports/audit-2026-10-30.md:430–447`). This is a
   generator-only change (`translator.py:465–477`, `generate.py:1028–1036`) plus one runtime
   lookup function. Keep `recomp_lookup()` as the compatibility wrapper.
5. **Block-level (not function-level) dispatch for problem functions** (major, selective).
   *Stela source:* `ExitKind` returning to dispatcher + patched `DirectExit` chaining
   (`block.rs:1–6`, `x86_64/chain`, `code_cache.rs::patch_native`).
   *Adaptation:* for functions with unreachable/computed-jump regions where whole-function lifting
   is fragile, emit multiple C entry points keyed by guest basic-block start and add a secondary
   dispatch probe on the block PC. Only do this behind a per-function opt-in; it changes the
   source-quality contract (one C function per guest function) and is not a general win.
6. **Position-independent generated code via page-relative relocation + proof** (long-term).
   *Stela source:* `Entry::rebase`, `Entry::relocate`, `prove()`, `probe_displacements()`.
   *Adaptation:* replace hard-coded absolute guest VAs in lifted C with `g_image_base + offset`
   (or a table-driven relocation pass) so a different image base needs no relift. Reuse the
   existing PE analysis (`pe_analyze`) and difftest to prove that the relocated fields are
   exactly those that move. High effort; only justified if multiple image bases are targeted.

**Explicitly NOT recommended for adaptation:** Stela's multi-PE grace/quiescence, ring
retirement, IC IVAU/TLB epoch machinery and x86-64 W^X arena — they solve a runtime JIT
coherence problem GameReComp does not have. Also avoid any GPL-2.0-only Stela component.

---

## 10. Verification / evidence notes

- All findings are from static reading of read-only snapshots; no build, run or game injection
  was performed. Any claim of behavioural equivalence needs the GameReComp harness
  (`ctest`, `tools/lift/difftest.py`, Unicorn harness per AGENTS.md) before being asserted.
- Generated `recomp_dispatch.c` is not checked into the GameReComp tree; its exact emitted form
  is taken from the two generators (`translator.py:465–477`, `generate.py:1028–1036`) and the
  runtime consumer (`runtime/recomp32/main.c:107–123`, `recomp_types.h:84–92`).
- `git rev-parse` in the Stela snapshot returned no value (snapshot has no workable `.git`);
  the pin `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe` is as supplied in the task brief.
