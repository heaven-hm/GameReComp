# S5 audit — Optimization passes and code-generation techniques that reduce generated code / improve compiler output

**Sources (read-only):** Stela at `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/stela` (supplied pin `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe`; the snapshot has no `.git`, so the pin is taken as given and was **not** verifiable locally). GameReComp at `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/integration` (worktree of `/Users/haseeb-mir/Documents/Code/GameReComp`, HEAD `bc12deed22d7cd593012d1cbfe48dd96235e9d70`). **No file in either repository was edited**; the only file written by this audit is this report.

**Companion reports:** register residency/allocation is covered in `s3-registers.md`, block/dispatch/cache in `s4-blocks-cache.md`, branches/indirect dispatch in `s6-controlflow.md`. This report concentrates on *passes and emitters that make the output smaller or better*, and on what a Python lifter emitting C can actually borrow.

---

## 1. Bottom line

Stela is an **AOT/JIT ARM64 → x86-64 machine-code translator**; "compiler output" for it is literally a byte buffer, so it has a large family of explicit code-reducing passes: dead flag-capture removal, adjacency fusions, pair/triple instruction recipes, segmented check elimination, masked-base reuse, cost-gated region admission, and hot/cold splitting. Stela *is* its own backend, so it must also do physical register allocation and indirect-branch patching itself.

GameReComp is an **x86-32 → C static lifter** (`tools/lift/lift32.py` + `tools/lift/generate.py`). It emits one or a few C statements per x86 instruction into a global-register C model and **delegates allocation, CSE and dead-code elimination to the host C compiler**. Its own "optimization" is deliberately narrow: pattern-match a flag *setter* to its *consumer*, keep a lazy flag tuple in function locals, avoid unnecessary labels/join points, and keep indirect jumps in a local `switch`. That is a sound design, but it leaves three concrete gaps that Stela's passes address directly: (a) flag state is published unconditionally at every setter, (b) adjacent instructions are never fused, and (c) address/base computations and safety checks are recomputed per instruction even inside one straight-line stretch. Those three gaps are the **top portable techniques** in §5.

**Licence constraint:** Stela's own code is GPL-2.0-or-later (`README.md:102`, `NOTICE:1-19`, root `Cargo.toml` `license = "GPL-2.0-or-later"`); `crates/stela-compiler/Cargo.toml` declares `license = "GPL-2.0-or-later AND MIT"`, and third-party code in the tree has its own terms. GameReComp is AGPL-3.0-or-later (`LICENSE` AGPL-3.0, `NOTICE`). **Port the ideas, not the source.** An independently written algorithm with the same behaviour is not a derivative work; copying the Rust structure or comments into a GPL-incompatible context would be. Keep an attribution note even for idea-level reuse.

---

## 2. Licence, dependencies, portability (exact)

| | Stela (compiler slice) | GameReComp (lifter slice) |
|---|---|---|
| Licence | `README.md:102` "Stela's code is GPL-2.0-or-later"; `NOTICE:1-19` (per-binary GPL-2.0-only / GPL-3.0-or-later notes); `Cargo.toml:53-54` workspace `license = "GPL-2.0-or-later"`; `crates/stela-compiler/Cargo.toml:7` `license = "GPL-2.0-or-later AND MIT"`; `LICENSE` = GPLv2 text | `LICENSE` = GNU AGPL v3 (19 Nov 2007); `NOTICE` "everything else … AGPL-3.0 … Code published earlier under MIT keeps its original notice in LICENSES/GameReComp-prior-MIT.txt"; per-file notices in `LICENSES/` |
| Language / build | Rust 2024, `rust-version = "1.99"` (`Cargo.toml:50-56`), `#![no_std]` compiler (`crates/stela-compiler/src/lib.rs:3`); workspace builds LLVM/Mesa/Linux from source (`README.md:82-95`) | Python 3.10+ (`README.md:298`), `capstone` Python bindings (`lift32.py:17-29`), `unicorn` for differential tests (`README.md:301`); generated C built by CMake/MSVC/clang |
| Direct deps of the audited code | `serde`, `rustc_apfloat = "=0.2.3"`, build-dep `stela-fingerprint` (`crates/stela-compiler/Cargo.toml:15-21`); `SOURCE_SHA256` binds cached code to compiler sources (`lib.rs:32-33`) | stdlib only (`re`, `dataclasses`, `typing`) + `capstone`; C runtime headers `runtime/recomp32/recomp_types.h` |
| Output | Native x86-64 bytes + source map + relocations (`lib.rs:74-86`), installed in an executable arena | C source text (`void name(void) { … }`, `L_XXXXXXXX:` labels, `RECOMP_*` macros), compiled by the host toolchain |
| Portability of the *implementation* | Low: emits x86-64 (SSE2/AVX2/AVX-512/BMI2/AES-NI gates), assumes 64-bit host, pinned host registers RAX/RBX/…/R15, `LAHF`, `PEXT` — see `x86_64/block.rs:574-601`, `x86_64/pinned.rs:38-49` | High: portable C + Python; the macros have fallbacks (e.g. `RECOMP_LOCAL_REGS` opt-in, `recomp_types.h:98-121`) and the same lifter feeds native32/WASM backends |
| Portability of the *techniques* | High when expressed as IR analysis + emission decisions: liveness, fusion, cost gating, check elimination, label discipline are architecture-neutral | Same — all three top techniques below can live in Python and emit ordinary C |

---

## 3. Stela — inventory of code-reducing / output-improving passes

All paths below are relative to the Stela snapshot root. "Portable" means expressible in a Python lifter that emits C.

| # | Technique | Exact file / function | Effect | Portable? |
|---|---|---|---|---|
| 1 | Lazy NZCV + dead-capture NOP-ing | `x86_64/flag_word.rs:91-125` `dead_from`/`defines`; `:127-177` `captured_tail`/`drop_capture`; `:185-196` `capture`; `x86_64/block.rs:745-761`, `1474-1483`, `2132-2152` `deferral_pays` | Removes the ~3-instruction LAHF/SETO/store sequence when flags are overwritten before any reader; keeps flags in host flags across adjacent setters/readers | **Yes (high)** |
| 2 | ALU + branch flag reuse | `x86_64/block.rs:1984-2020` `alu_zero_branch`; gate at `:563-568`, `native_branch_after` `:576-595` | A flagless ADD whose result is immediately tested by `BranchZero` emits no compare — host ZF from the ADD is reused | Yes |
| 3 | ADR(P)+ADD address folding | `x86_64/block.rs:489-512` `fold_address_pair`; call `:1225-1246` | Two guest instructions → one `MOVABS` + store (plus NOP to keep the source-map interval) | **Yes (high)** |
| 4 | Bit-reverse + CLZ → CTZ | `x86_64/block.rs:1249-1277` | Two guest instructions → one host TZCNT/BSF-class operation | Yes |
| 5 | Divide + remainder co-emission | `x86_64/block.rs:1087-1095` → `x86_64/integer_math::emit_divide_remainder` | One `idiv` produces both quotient and remainder instead of two divides | Yes |
| 6 | Compare-loads / load-store pair recipes | `x86_64/block.rs:1041-1086` (`memory_recipe::CompareLoads`), `:1039-1059` (`register_offset::emit_load_store`) | Fuses a load with its compare/second access; shares addressing and checks | Yes |
| 7 | SIMD group fusion | `x86_64/block.rs:596-601, 943-1020`; `x86_64/simd/sha256.rs` `HashGroup::recognize/emit`; `x86_64/simd/aes/packed_rounds.rs:17+`; `x86_64/simd/aes.rs::emit_round_pair`; `x86_64/simd/rearrange.rs::{emit_reverse_dword_run,emit_single_table_run}`; `x86_64/simd/precision.rs::emit_half_widen_pair` | 2/4 AES rounds, SHA-256 round groups, repeated table/reverse shuffles, half-widen pairs emit as one host sequence; gated on target features (`Aes`, `Vaes`, `Ssse3`) and DIT | Partially (maps to vector intrinsics in C) |
| 8 | Carry-run fusion | `x86_64/block.rs:1021-1038` → `x86_64/pinned::emit_carry_run` | A run of `AddSubCarry` emits one flag-capture and a tight chain | Yes |
| 9 | Byte-sum/prefix-loop recognition | `x86_64/byte_prefix.rs:1-...` `Plan::new` / `Plan::emit` | Recognises a 16-byte sum + prefix-sum recurrence with a branch back and emits a vectorised recurrence instead of 50+ scalar instructions | Yes |
| 10 | Segmented check elimination | `x86_64/block.rs:458-472` `segmented`; used `:635-648` (guest range) and `:645-648` (stack alignment), `:649-672` for loop copies | Re-runs the check analysis on each stretch between join/landing points; redundant range/alignment checks inside a segment disappear. Loop copies are analysed on a tripled body so later copies inherit the first's checks | **Yes (high)** |
| 11 | Masked-base reuse | `x86_64/masked_base.rs:1-27` (module doc), `:32-47` (`SLOTS`, `DELTA_LIMIT = 1<<30`, `LOOKAHEAD = 32`, `Plan`), `Tracker::plan` `:316`, `Tracker::after` `:350`; `x86_64/block.rs:712-720, 1484-1486, 1549` | A checked address stays in a slot register; later accesses through the same base with small immediate deltas reuse it, skipping their own check | **Yes (high)** |
| 12 | Vector/derived-register cache | `x86_64/vector_cache.rs:1-30`, `FIRST_HOST = 8` (XMM8–15), `FIRST_DERIVED = 16` (AVX-512VL derived bank), `flush_vectors` `:382`, `forget_vectors` `:392`, `settle_vectors` `:406`; `x86_64/block.rs:740-744, 825-856, 1490` | Guest V registers stay in host XMMs within a block; state written only at exits/faults/unknown instructions. Derived constants (lane masks) are reused too | Yes (see `s3-registers.md`) |
| 13 | Persistent guest-register pinning | `x86_64/pinned.rs:1-19, 38-49` (`PINNED`: SP→RBX, X8→RSI, X19→R14, X0→R15, X1→RBP, X16→R12), `write_back` `:603`, `reload` `:619`; `pinned/span.rs` | No load/store pair around each guest instruction; no spill/fill at a chain boundary | Yes (see `s3-registers.md`) |
| 14 | Liveness-based region allocation with **cost gate** | `x86_64/region.rs:60-87` (`AllocationStats`), `:1192` `emit_allocated_prefix`, `:1234` `emit_clean_load_prefix_with`, `:1380-1390` `admits_allocated`/`admits_ordinary`; `x86_64/region/analysis.rs:312` `next_use`, `:326` `prefix_effects` | Guest values live in host registers over a region; the candidate is **rejected unless strictly fewer bytes and no more host instructions than the baseline** | **Yes (high)** |
| 15 | Hot/cold layout and self-loop duplication | `x86_64/block.rs:2440-2540` (`OutOfLine`, `Split`, `Place::Inline`/`Place::OutOfLine`, `split.inline_end`), `2498-2521` `code.truncate(split.inline_end)`; `x86_64/region/self_loop.rs:19-59` `FrameLoop`; `x86_64/block.rs:649-672, 731-735` | Hot path stays inline; side exits, fault paths and PAC/slow paths move out of line while preserving source-map offsets. Self-loops duplicate the body (2–3 copies) to hoist checks/run inside one block | Yes |
| 16 | Bounded direct chaining + jump cache | `x86_64/chain.rs:1-...`, `JUMP_CACHE_ENTRIES = 256` `:1172`, `JUMP_ENTRY_BYTES = 64` `:1175`, `fill_jump_cache` `:4080`, `link` `:3079`, `lookup_dispatcher` `:3417`, `patch_word` `:2842` | Removes dispatcher round-trips on hot exits; a 2-way-set cache line per PC, budgeted so interrupts/preemption still happen at block boundaries | Yes (as computed-goto + direct-mapped cache in C; see `s4`/`s6`) |
| 17 | Constant/special-case recognition | `x86_64/region/constant.rs`; `x86_64/zero_block.rs:1-...` (`UNROLLED_BYTES = 128`); `x86_64/byte_prefix.rs` | Constants tracked so loads/stores can be folded; DC ZVA unrolled for small blocks | Yes |

### 3.1 Lazy flags — the clearest "reduce emitted code" pass

`x86_64/flag_word.rs:1-18` states the cost model: forming an Arm NZCV word takes nine host instructions if done naively, three if captured from LAHF/SETO. The pass has four parts:

1. **`dead_from(instructions, start)`** (`:97`) scans forward: the flags are dead if the next instruction touching them *defines all four* without reading (`defines`, `:111`, admits flag-setting `AddSub`/`Logical` forms and flagless `MoveWide`/`Bitfield`/`PcRelative`/`Nop`) and no instruction in between can read flags, fault, exit or leave the frame. It is deliberately conservative — never dead past a block's last instruction.
2. **Deferral** — `x86_64/block.rs:745-751` keeps a `pending: Option<bool>` (the borrow polarity) and `1460-1470` passes `defer_flags: lazy_flags && !frame_bound && deferral_pays(...)`. `deferral_pays` (`:2132-2152`) walks adjacent `reads_host_flags` instructions (conditional branches and flag-preserving selects, `reads_host_flags` `:2157-2165`); deferral pays when no conditional branch intervenes or when the flags are dead at the end of that run.
3. **Dead-capture removal** — if the word is dead after the setter, `x86_64/block.rs:1474-1483` finds the emitter's capture tail (`captured_tail`, `flag_word.rs:132`) and `drop_capture` (`:157`) overwrites LAHF+SETO+store with same-length long NOPs so every recorded offset, fixup and join stays valid. That is a genuine post-emission peephole.
4. **Algebraic packing** — `PACK_MULTIPLIER`/`UNPACK_MULTIPLIER` (`flag_word.rs:34-51`, `pack`/`unpack` `:61-71`) move NZCV between Arm's nibble at bits 31:28 and the compact word (N=15, Z=14, C=8, V=0) with one `imul` each, because no partial products share a bit. `host_condition` (`:304`) derives branches directly from the host flags while they are still live.

Also note `alu_zero_branch` (`block.rs:1984-2020`): a single flagless ADD followed by a `BranchZero` on the same register reuses the ADD's host ZF, and a W32 result is accepted because a W write zero-extends.

### 3.2 Fusion plus a cost gate — how Stela avoids "optimizations that lose"

The block emitter (`x86_64/block.rs:779-1490`) is effectively a greedy superoptimizer over a fixed menu of recipes, in priority order: SHA-256 groups (`943`), AES/VAES round pairs (`956-981`), SSSE3 shuffle runs (`982-1009`), half-widen pairs (`1010-1020`), carry runs (`1021`), load/store pairs (`1041`), compare-loads (`1060`), divide+remainder (`1087`), region allocation (`1098`), clean-load regions (`1129`), address-pair fold (`1225`), reverse+CLZ (`1249`). Each recipe checks adjacency (`pc + 4 == next.pc`), register identities and no-join (`!landing[cursor+1]`) before consuming 2–4 instructions, and advances `source_map` for every consumed PC with a NOP where no host bytes were produced.

The **admission gate** is the portable gem: `x86_64/region.rs:1380-1390`:

```rust
pub fn admits_allocated(region: &OptimizedRegion, baseline_code: &Code) -> bool {
    region.consumed_instructions >= 2
        && region.code.len() < baseline_code.len()
        && region.stats.native_instructions <= baseline_code.instructions()
}
```

The emitter builds the candidate in a private `Code` and a baseline by re-emitting the same instructions normally (`block.rs:1098-1128`, `1129-1219`), then keeps the optimized bytes only if they are **strictly smaller** and do not raise the host instruction count. This makes every downstream recipe safe to add: a bad recipe simply loses the gate.

### 3.3 Check elimination and address reuse inside straight-line stretches

`segmented` (`block.rs:458-472`) re-runs `guest_range::checks` / `stack_alignment::checks` on each stretch delimited by `landing[]` (join points from self-loops and regions), treating each as a fresh block. `checks_at` (`:675-683`) then consults the per-row result. For self-loops, the analysis is run over the body **three times** (`:649-672`) so the checks needed by later copies are known when emitting the first — checks that only the second/third copy would need can be dropped there.

`masked_base.rs` goes further: a checked access leaves the masked guest address in RAX; `Tracker::plan` (`:316`) decides `Keep(slot)` or `Reuse{host, delta}`, using R11 then R10 (`SLOTS`, `:32`), a 2^30 delta bound (`:34`) and a 32-instruction lookahead (`:37`). Later accesses emit `[slot + delta]`, so their own range check disappears. The module doc (`:1-27`) carries the safety argument: the kept value is a *register*, not a frame slot a mispredicted load could read stale, and every derived address stays within the unreachable region or the guest's native range.

### 3.4 Indirect dispatch (cross-reference)

Stela's `chain.rs` removes the dispatcher from hot transfers: direct exits are patch points (`patch_word`, `:2842`), `link` (`:3079`) installs a thunk into the successor, and `lookup_dispatcher` (`:3417`) + `fill_jump_cache` (`:4080`) serve non-static successors through a 256-entry, 64-byte-entry, 2-way-set jump cache with an ITLB (`chain.rs:1-...`, `1172-1193`). The budget counter `ACCOUNT` lives in R13 (`chain.rs:273`, `pinned.rs:38-49`) so a chain still yields on interrupts. Full treatment in `s4-blocks-cache.md` and `s6-controlflow.md`; it is a runtime optimization, not a code-size one, so it is not in the top three below.

---

## 4. GameReComp baseline — what `lift32.py` emits today

### 4.1 Exact functions and what they do

| Function (`tools/lift/lift32.py`) | Role |
|---|---|
| `FUNCTION_LOCALS` `:218-246` | Contract for the function preamble: `_fpu_cmp`, `_cf`, `_df` (direction), `_flag_k/_flag_a/_flag_b`, `_itail_tgt`. Consumed by `generate.py:275`. |
| `Lifter.__init__` `:265-320` | Options include `precise_sbb`, `precise_carry`, `patch_sites`, `reloc`, `dos`, `call_pop`, `computed_ret`; state `_flag_state`, `_flag_seq`, `_label`, `_jump_targets`. |
| `_fmt_read` `:346-373` / `_fmt_write` `:375-398` | One C expression per operand: `LO16/LO8/HI8`, `SET_LO16/SET_LO8/SET_HI8`, globals via `recomp_types.h:106-130`. |
| `_fmt_mem_addr` `:400-439` | Rebuilds every effective address as `base + index*scale + disp` each time; FS/GS/ES overrides call `FS_BASE/GS_BASE/ES_BASE`. No reuse of a previously computed address. |
| `_shift_count` `:490-508` | Masks the count to 5 bits (avoids C UB from over-wide shifts) and folds a known immediate into the emitted constant. `_shift_out_cf` `:510` and `_shift_flags` `:540` specialise CF/OF. |
| `_flag_capture` `:561-602` | Snapshots a setter's operands into `_flag_a/_flag_b` and records `self._flag_state`; **narrow operands are left-aligned** to bit 31 so one 32-bit flag path serves all widths without a width argument. |
| `_make_condition` `:604-755` | The setter→consumer peephole: `CMP_*`/`TEST_*` macros from `recomp_types.h:348-374`, constant folding after `test` (`jb→0`, `jae→1`), IN C/DEC CF preservation, NEG/SBB branchless-select idiom, PARITY rebuild, and `recomp_cond_cf` runtime fallback when `_flag_state is None`. |
| `lift_instruction` `:757-767` | Wraps `_lift_instruction` and **always** appends `_flag_k = <kind>;` whenever flags were written (with `FK_NARROW` for narrow widths). |
| `_lift_instruction` `:769-2197` | Straight one-instruction-to-statement(s) mapping for the whole x86-32 ISA; no cross-instruction fusion. |
| `_computed_jump` `:2309-2323` | Indirect jump → a `switch (_jt)` over `(jump_targets ∩ labels)`, capped to real arms because listing every label made a 500-function file 5 MB → 20 MB and crashed the compiler. |
| `lift_function` `:2363-2459` | Emits blocks in address order; carries `_flag_state` across a fallthrough from a conditional jcc (`:2414-2440`) but resets at a real branch target; SEH tail adds a second `_itail_tgt` switch. |

`tools/lift/generate.py::lift_function_linear` (`:246-422`) adds the C-level decisions: `FUNCTION_LOCALS` preamble (`:275-283`), `label_all` only when indirect targets are unknown (`:287-300` — "labelling every instruction makes each one a join point, and the optimiser can keep nothing in registers across any of them"), join-point flag resets (`:309-315`), gap fallthrough (`:317-368`), and a local `_ljump` switch for indirect tails (`:384-407`).

`runtime/recomp32/recomp_types.h` supplies the runtime half: condition macros (`:340-377`), `recomp_cond`/`recomp_cond_cf` (`:432-535`), the opt-in `RECOMP_LOCAL_REGS` shadow locals (`:98-121`), and `RECOMP_ITAIL` (`:1295-1309`), which on **every** indirect tail writes two trace-ring entries and tries `recomp_lookup_manual` → `recomp_lookup` (binary search, `:1159`) → `recomp_lookup_import` before saving/restoring flags and registers.

### 4.2 What the host C compiler already handles (so Stela techniques that would be redundant)

- Dead local assignments and CSE *inside* a function — but `_flag_*` are read by `RECOMP_FLAGS_OUT()` at every call/tail-call/return (`recomp_types.h:75-76, 1305`), so dead flag writes **are** observable and cannot be DCE'd. Liveness must be done in the lifter.
- Register allocation inside one function with `RECOMP_LOCAL_REGS` — already an opt-in approximation of Stela's `pinned.rs`, but it writes back at every call/exit and reloads after, unlike Stela's no-spill pinning across chains.
- Algebraic identities on a single expression — but **not** recognition of a two-instruction x86 idiom that needs architectural knowledge (e.g. `r = ~x; clz(r)` → `ctz(x)`).

### 4.3 Comparison matrix

| Axis | Stela | GameReComp | Gap for GameReComp |
|---|---|---|---|
| Flag state | Derived NZCV word packed in one multiply; deferred in host flags; dead captures NOP'd | Lazy `_flag_k/_flag_a/_flag_b/_cf` tuple + static setter/consumer pairing; written at every setter | No liveness; tuple published even when dead |
| Flag correctness at joins | `pending` borrow carried across host-flag readers; word stored before fault/exit | `_flag_state` kept only on direct fallthrough from a jcc; else `recomp_cond_cf(kind,…)` | Comparable; Stela's `dead_from` is the missing analysis half |
| Multi-instruction fusion | 17+ recipes with source-map bookkeeping and adjacency/register guards | One x86 instruction → one C statement (SSE/MMX delegated to `lift32_sse.py` but still per instruction) | No peephole fusion, no cost gate |
| Safety checks | Segmented per stretch; masked-base reuse of already-checked addresses | Every `MEM*` recomputes address; range checks live in runtime macros | Repeated address/check work |
| Cost control | `admits_allocated`: strictly fewer bytes and no more instructions | No measurement; emission is unconditional | A bad "optimization" can only ever grow the C |
| Labels / layout | Hot inline, cold out-of-line; joins explicitly tracked | `label_all` only when needed; local `switch` limited to real arms | Already good; could add hot/cold hints |
| Dispatch | Budgeted chaining + 256-entry jump cache | `RECOMP_ITAIL` → 3 lookups + flag/register save-restore | Covered in `s4`/`s6`, not a codegen gap |

---

## 5. The three highest-value portable techniques

Ranked by (benefit to generated C) × (ease of safe implementation in Python) ÷ (overlap with work already done in this codebase). Each is an independently implementable algorithm; **do not copy Stela source**.

### 5.1 Flag-state liveness + deferred/dead publication

**Why it is #1.** Every flag-setting instruction in `lift32.py` currently emits `_flag_a = …; _flag_b = …;` (`_flag_capture`, `:561`) plus `_flag_k = …;` (`lift_instruction`, `:762-766`), and add/sub/cmp/neg also emit `_cf = …;` (`:940, 952, 982`). These locals are read by `RECOMP_FLAGS_OUT()` at each call/tail-call/return, so the C compiler cannot drop them. A liveness pass removes whole statement groups from generated C with zero semantic risk when done at setter granularity.

**Stela evidence.** `x86_64/flag_word.rs:91-125` `dead_from`/`defines`; `x86_64/block.rs:745-761` `pending`/`dead_from`, `1474-1483` dead-capture drop, `2132-2165` `deferral_pays`/`reads_host_flags`; `1984-2020` `alu_zero_branch`.

**Concrete GameReComp implementation.**
1. In `generate.py::lift_function_linear` / `Lifter.lift_function`, after leaders, jump targets and EH targets are known, compute a backward "flags live" bit per instruction over the recovered CFG: a setter kills (then generates) live-ness; a jcc/setcc/cmovcc/adc/sbb/pushfd/flags-consuming helper generates it; a call/tail-call/exit generates it (because `RECOMP_FLAGS_OUT` publishes); a faulting/exiting instruction is a barrier. Reuse the already-computed `targets`/`leaders` sets instead of rebuilding a CFG.
2. In `Lifter.lift_instruction` (`:757`), skip `_flag_capture`, `_flag_k = …` and `_cf = …` when the setter's flags are dead. Keep emitting the instruction's own result and any *local* carry needed by an immediately following `adc`/`sbb` (that is a separate, tighter liveness question — leave `precise_carry` semantics untouched).
3. Keep the existing `_make_condition` static pairing and `recomp_cond_cf` fallback exactly as-is; this pass only removes writes, so it cannot change a condition's value.
4. Extend the existing fallthrough carry (`generate.py:309-315`, `lift32.py:2414-2440`) to a proper "flags live across this label" query: when a jcc's flags stay live into the next block, preserve `_flag_state` instead of resetting it. That is the GameReComp analogue of Stela's `pending` borrow.
5. Optional peephole: when a flagless ALU result is immediately consumed by a zero-test jcc on the same register, emit the branch directly from the computed C expression (`if ((a + b) == 0) …`) rather than through a fresh `CMP_EQ`.

**Cost/risk.** Low: analysis is per function, no runtime state, no ABI change. Verify with existing difftest corpus (`tools/lift/difftest.py`) and `FLAGS_OUT` selftests; compare generated C byte counts before/after.

### 5.2 Peephole fusion of adjacent lifted instructions, guarded by a cost gate

**Why it is #2.** GameReComp deliberately emits one statement per instruction; even trivial two-instruction idioms survive into C. Stela's emitter shows how to consume 2–4 adjacent instructions with a recipe that checks PC adjacency, destination/source identity and landings, and — crucially — keeps the *baseline* and only adopts the recipe if it is strictly smaller.

**Stela evidence.** `x86_64/block.rs:489-512` `fold_address_pair` (ADR+ADD → one MOV), `:1984-2020` `alu_zero_branch`, `:1249-1277` reverse+CLZ→CTZ, `:1087-1095` divide+remainder, `:1041-1086` compare-loads, `:943-1020` SIMD group fusion; `x86_64/region.rs:1380-1390` `admits_allocated` (strictly fewer bytes **and** no more host instructions); `x86_64/block.rs:1098-1128` baseline-vs-candidate construction; source-map NOPs at `:1232-1235, 1260-1264`.

**Concrete GameReComp implementation.**
1. Add a `fuse_window(instructions, i) -> (count, [C statements]) | None` hook called from `lift_basic_block` (`lift32.py:2283`) and `generate.py:363` before the per-instruction `lift_instruction`.
2. First recipes (all pure x86-32, all guardable by PC adjacency and `insn.address not in targets/leaders`):
   - `mov r, imm32; add r, imm32` (or `sub`) → one immediate, with arithmetic folded in Python; also handles the ADR+ADD analogue (`lea r,[disp]; add r,imm` → `lea r,[disp+imm]`).
   - `not r; bsr r2, r` → `bsf`/count-trailing-zeros expression when the destination aliases permit.
   - `div`/`idiv` followed by a use of both `eax` and `edx` — already one C statement in `lift32.py`, so instead fuse `cdq; idiv` into a single signed-division expression with explicit divide-by-zero/overflow checks preserved.
   - `cmp/test` immediately followed by `setcc`/`cmovcc` → reuse `_make_condition` output directly rather than re-deriving from `_flag_*` (only when no intervening writer).
3. **Admission gate in Python:** lift the window twice — once with the current per-instruction path and once with the recipe — and keep the recipe only if `(number of emitted C statements, total token count) < baseline` lexicographically. This is the direct analogue of `admits_allocated` and makes recipes fail-soft.
4. Keep the address comment for every consumed PC (Stela's source-map NOPs, `block.rs:1232-1235`) so debuggers/difftest can still attribute the fused statement to all original PCs.
5. Never fuse across a label, a call, a possibly-faulting access with a distinct fault site, or an EH resume point. These are exactly the conditions Stela checks with `landing[]`, `frame_bound` and `checks_at`.

**Cost/risk.** Medium (many recipes, but each is small and independently gated). `difftest.py` already provides differential oracles; `translator.py`/`generate.py` can report the before/after statement count so the gate's effect is measurable.

### 5.3 Straight-line-segment check elimination and masked-base / effective-address reuse

**Why it is #3.** GameReComp rebuilds every effective address in `_fmt_mem_addr` (`:400-439`) and relies on runtime `MEM8/16/32/64` macros for safety. Stela shows that inside one join-free stretch you can (a) analyse checks once per stretch and (b) keep one checked masked base in a register and derive later addresses from it by immediate deltas. Both are pure analysis + emission changes in C: compute the address once into a local, reuse it, and drop the now-redundant check.

**Stela evidence.** `x86_64/block.rs:458-472` `segmented` (+ uses at `:635-648`, `:649-672`, `:675-683`), `x86_64/masked_base.rs:1-47, 316, 350`, `x86_64/block.rs:712-720, 1484-1486`; complementary label discipline in `tools/lift/generate.py:287-300` ("labelling every instruction makes each one a join point, and the optimiser can keep nothing in registers across any of them").

**Concrete GameReComp implementation.**
1. Split each emitted C body into **segments** delimited by labels/join points (exactly the `leaders`/`targets`/`eh` sets `generate.py` already computes). Run any per-instruction analysis (range-check need, stack-alignment check, address-base tracking) once per segment, not once per function and not globally. This mirrors `segmented`.
2. Within a segment, track up to two "kept bases": after an access whose address is a plain `reg + disp`, emit `uint32_t _baseN = reg + disp;` and use `_baseN + delta` for later accesses whose base is the same guest register and whose delta is within a bounded range (Stela uses 2^30; for C a conservative 64 KiB is plenty). Guard with a short lookahead (Stela: 32 instructions) so a kept base is only created when a reuse actually follows.
3. Drop the redundant range/alignment-check call for the derived accesses **only when** the kept access already checked the address and the delta cannot leave the checked native range — keep the conservative condition if the codebase's `guest_range` model is not available; in that case keep the check but still reuse the computed address expression (this alone removes arithmetic from generated C and helps the host compiler's CSE).
4. Reset all kept bases and flag state at every join, call, tail call, and EH landing, matching Stela's `landing[cursor]` handling (`block.rs:825-864`).
5. Keep `generate.py`'s existing limited-label rule; it is the C-side prerequisite for both this pass and `RECOMP_LOCAL_REGS` to work. Optionally annotate hot blocks with `__builtin_expect`/`likely` to capture Stela's hot/cold split benefit without moving code.

**Cost/risk.** Medium: must be conservative about aliasing (a guest store through another pointer may change the base register's *value* only if it writes that register — guest registers are C globals/locals, so track writes to them), and about FS/GS/ES overrides (route through the existing `FS_BASE`/`GS_BASE` logic, never reuse a flat base across a segment override).

### 5.4 Why not the other Stela techniques as top three

- **Guest-register pinning / vector cache:** high value, but `s3-registers.md` already documents it and GameReComp has an opt-in `RECOMP_LOCAL_REGS` shadow (§4.2). Extend that rather than re-invent it.
- **Block chaining / jump cache:** high runtime value, but it is a dispatch concern covered in `s4`/`s6` and does not reduce generated code.
- **SIMD group fusion:** valuable for crypto/texture loops, but target-specific and lower-frequency in the x86-32 game corpus; the generic fusions in §5.2 deliver the same idea first.

---

## 6. Secondary portable ideas (worth a follow-up)

1. **Cost-gated region allocation with liveness** (`region/allocation.rs`, `region/analysis.rs:312`, `region.rs:1380`): if `RECOMP_LOCAL_REGS` is enabled, apply backward liveness to decide which of the eight guest registers deserve shadow locals *per function*, and only keep the shadow variant if it reduces statement count. Stela's allocator treats NZCV as just another value — the same trick would unify flag liveness (§5.1) with register liveness.
2. **Self-loop specialisation** (`region/self_loop.rs:19-59`, `block.rs:649-672`): detect a one-block guest loop, emit a C `for`/`do-while` with a bounded iteration counter and hoisted checks instead of one `goto` per iteration; keep `RECOMP_BACKEDGE()` for preemption at the loop latch.
3. **Hot/cold split with compiler hints** (`block.rs:2440-2540`): mark unlikely side exits with `likely`/`unlikely` or move them to `__attribute__((cold)) noinline` helpers; this is the C equivalent of Stela's `Place::OutOfLine`.
4. **Constant tracking** (`region/constant.rs`): Stela tracks which guest registers hold compile-time constants; a Python side-table over `Lifter` could fold `mov r,imm` chains and avoid emitting the initial store when the constant is already known.
5. **Zero/DC-ZVA-style unrolling** (`zero_block.rs`, `UNROLLED_BYTES = 128`): for `rep stos`/`rep movs`, choose an unrolled fixed-count C sequence for small counts and a loop for large counts, instead of always calling a helper.
6. **Direct-mapped jump cache in `RECOMP_ITAIL`** (`chain.rs:1172, 4080`): a 256-entry `guest_pc → recomp_func_t` array checked with one compare and one shift before the three lookup calls. Cheap, pure C, and independent of the codegen work above (see `s4`/`s6`).

---

## 7. Exact file / function index (quick reference)

**Stela** (all under `crates/stela-compiler/src/`):
- `lib.rs:1-33` — AOT ARM64→x86-64, `no_std`, `SOURCE_SHA256`.
- `x86_64/block.rs:458-472` `segmented`; `:489-512` `fold_address_pair`; `:563-601` fusion gates; `:635-761` check/loop/vector/lazy-flag setup; `:779-1490` main emission loop (fusions at `:943-1277`); `:1474-1483` dead capture drop; `:1984-2020` `alu_zero_branch`; `:2132-2165` `deferral_pays`/`reads_host_flags`; `:2437-2540` hot/cold split.
- `x86_64/flag_word.rs:1-18, 34-51, 61-196, 304-327` — NZCV packing, `capture_in_rax`, `dead_from`, `defines`, `captured_tail`, `drop_capture`, `capture`, `host_condition`.
- `x86_64/region.rs:60-87, 1192, 1234, 1380-1390`; `x86_64/region/analysis.rs:11, 312, 326`; `x86_64/region/allocation.rs:1-14`; `x86_64/region/self_loop.rs:19-59`.
- `x86_64/masked_base.rs:1-47, 316, 350`; `x86_64/vector_cache.rs:1-30, 382-406`; `x86_64/pinned.rs:1-19, 38-49, 603, 619`.
- `x86_64/chain.rs:1-..., 273, 1172-1193, 2728, 2842, 3079, 3417, 4080`; `x86_64/byte_prefix.rs:1-...`; `x86_64/zero_block.rs:1-...`.
- Licence/build: `README.md:82-102`; `NOTICE:1-19`; `Cargo.toml:50-56`; `crates/stela-compiler/Cargo.toml:1-21`.

**GameReComp**:
- `tools/lift/lift32.py:1-8` (design), `:106-155` (condition tables), `:218-246` (`FUNCTION_LOCALS`), `:346-465` (operand/address formatting), `:490-560` (shift handling), `:561-755` (`_flag_capture`, `_make_condition`), `:757-767` (`lift_instruction`), `:769-2197` (`_lift_instruction`), `:2283-2323` (block/indirect jump), `:2363-2459` (`lift_function`).
- `tools/lift/generate.py:246-422` (`lift_function_linear`), especially `:275`, `:287-300`, `:309-315`, `:317-368`, `:384-422`.
- `runtime/recomp32/recomp_types.h:75-76` (flag out/in), `:94-130` (register locals/globals), `:340-535` (conditions), `:1159-1161`, `:1182`, `:1295-1309` (`RECOMP_ITAIL`).
- Licence: `LICENSE` (AGPL-3.0), `NOTICE`.

---

## 8. Verification / scope notes

- Both source trees were read with `read`/`grep`/`sed` only. `git -C … status` was not used to modify anything; no build or formatter was run against either repository.
- The Stela snapshot has no `.git`, so the supplied pin `140ffaf…` could not be confirmed from local metadata; line numbers are from the supplied snapshot as-is. GameReComp's HEAD was read as `bc12deed…`.
- Claims about Stela behaviour are drawn from module docs and implementation in the files cited. Claims about GameReComp behaviour are drawn from the cited Python functions and the `recomp_types.h` macros. No performance numbers are asserted — the cost models quoted (nine vs three host instructions; 5 MB → 20 MB; 2^30 delta) are the projects' own documented rationale, not measurements made in this audit.
- The licence discussion is an engineering note, not legal advice. Confirm with the project's legal owner before any code-level reuse.
