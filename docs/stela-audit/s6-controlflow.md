# S6 audit — Stela control flow vs. GameReComp `tools/lift/lift32.py` / `RECOMP_ICALL`

**Status:** read-only static audit. Nothing was edited in either source tree; no builds or tests were run.
**Sources:** STELA pinned at `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe`
(`/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/stela`);
GAMERECOMP snapshot at `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/integration`.

---

## 1. Executive summary

The two projects solve the same problem at opposite ends of the design space.

| | **Stela** | **GameReComp** |
|---|---|---|
| Translation target | AArch64 → native x86-64 machine code, AOT **and JIT** | x86-32 → **C source**, compiled AOT by the host C compiler |
| Translation unit | translation block / region, ≤ 256 guest instructions, ends at first transfer | whole C function, with C labels for basic blocks |
| Direct branch | emitted exit stub, then *patched at runtime* into a direct jump to already-translated successor (branch elimination / chaining) | `goto L_xxxxxxxx`; leave the function as `RECOMP_ITAIL(va); return;` |
| Register-indirect branch / call / return | dynamic exit to engine with target in `RAX` + typed word in `EDX`; runtime resolves via ITLB + block table + per-site jump cache, or paired-frame host `ret` | `RECOMP_ICALL(expr)` / `RECOMP_ITAIL(expr)` macro: linear-ish binary search in a sorted VA→function table, then a host `call`; `RECOMP_RET_JUMP` alias for `ITAIL` |
| "Why" | a system-mode emulator that must preserve guest PC/flags/NZCV, TLB contexts, BTYPE, interrupts and self-modifying code, and cannot afford a dispatcher round-trip per block | a static recompiler of 1990s PE games; the C compiler does block layout and most branch prediction; correctness of game semantics dominates |

The portability / licence gap matters: Stela's control-flow machinery is deeply tied to a 64-bit x86 host ABI, `global_asm!`, aligned 8-byte self-patching and a per-PE `ChainControl`. GameReComp cannot copy it wholesale, but several *ideas* (per-site indirect-call cache with generation validation, typed exit kind, hot-exit promotion, stale-code hashing) map cleanly onto its C runtime. These are listed in §7.

---

## 2. Stela — exact files, types and functions

### 2.1 Licence, dependencies, portability

* Workspace licence: **GPL-2.0-or-later** (`Cargo.toml:62-68`, `README.md:102`, `LICENSE`, `NOTICE`). Per-crate nuances: `crates/stela-compiler/Cargo.toml` declares `GPL-2.0-or-later AND MIT`; `crates/stela-engine/Cargo.toml` declares `GPL-2.0-or-later AND BSD-3-Clause`. `LICENSES/` holds 20 upstream licence texts (MIT, BSD-3-Clause, Apache-2.0-with-LLVM-exception, …). Treat all control-flow code as copyleft; prefer technique reimplementation over verbatim copying (see §8).
* Rust 2024 edition, `rust-version = "1.99"` (`Cargo.toml:64,67`). `stela-compiler/src/lib.rs` is `#![no_std]` + `extern crate alloc`; deps are `serde` (alloc/derive), `rustc_apfloat = 0.2.3`, `stela-fingerprint` (build). `stela-engine` adds `serde_json`, `miniz_oxide`, optional `libc`/`tempfile` and the sibling `stela-*` crates. No third-party JIT/decode library for x86-64 emission — `crates/stela-compiler/src/x86_64/asm.rs` is an in-house encoder.
* Host portability of the control-flow core: **x86-64 only**. `x86_64/block_entry.rs` uses `core::arch::global_asm!` with a `#[cfg(target_os = "macos")]` `_stela_block_enter` variant and a SysV `extern "sysv64"` signature; `chain::LinkGate::Pext` requires BMI2 + fast PDEP, `Jrcxz` is the portable fallback. Code installs at 8-byte aligned addresses so patch points are one aligned 8-byte word. Tests for native chaining are gated `target_arch = "x86_64"` + `target_os = "macos"|"linux"`. Full product build additionally needs LLVM 21–23/Clang/LLD/libclang and a UEFI/NVIDIA environment (`README.md:82-95`).
* There is **no Windows-host path** for the 64-bit translator in this snapshot; GameReComp's Windows host is not a target of this code.

### 2.2 Block model and exit vocabulary (`crates/stela-compiler/src/block.rs`, `block/analysis.rs`)

* `pub enum ExitKind` (`block.rs:60-78`) — 13 tagged values encoded in the exit word: `Fallthrough=0, Direct=1, Indirect=2, Call=3, Return=4, Trap=5, PointerAuthentication=6, Clock=7, ThreadRegister=8, FloatingPoint=9, ScalableNumerical=10, System=11, Amx=12`. `TryFrom<u64>` / `InvalidExitKind` at `block.rs:80-113`.
* `pub(crate) enum Terminator` (`block.rs:257-277`): `Branch(Operation)`, `DirectCall(GuestAddress)`, `Register{source: Register, kind: ExitKind}`, `Trap`, `System`, `PointerAuthentication`, `Clock`, `ThreadRegister`, `FloatingPoint`, `Amx`. `decode_terminator` (`block.rs:279-309`) recognises `BR` (`0xd61f0000` → `Indirect`), `BLR` (`0xd63f0000` → `Call`), `RET` (`0xd65f0000` → `Return`) and `BL` (`0x94…` → `DirectCall`); register 31 is mapped to `Register::Zero`.
* `MAX_BLOCK_INSTRUCTIONS = 256` (`block.rs:57-58`).
* `BlockAnalysis` (`block/analysis.rs:81-93`) records `static_successors: Vec<StaticSuccessor>` (kinds at `:53-62`: `Direct`, `ConditionalTarget`, `ConditionalFallthrough`, `CallTarget`, `CallContinuation`, `Fallthrough`) and `dynamic_edge: Option<DynamicEdge>` (`:68-80`, kinds `Branch|Call|Return`, plus the architectural source register). `analyze_parsed` (`:109-244`) is the single place that decides which edges are static (chainable) and which are runtime-resolved. Note `:120-137`: a region's branches into itself are **not** successors.
* `CompiledBlock` (`block.rs:217-240`) carries `direct_exits: Vec<chain::DirectExit>` (the chainable exit sites), `self_loop`, `region`, and `cold: (u32,u32)` — the out-of-line hot/cold split computed from host samples.
* `struct BlockExit { next_pc: u64, kind: u64 }` (`block.rs:209-215`) — the ABI word the native exit hands back to the engine.

### 2.3 Branch lowering (`crates/stela-compiler/src/x86_64/block.rs`, `x86_64/chain.rs`)

* Module doc `x86_64/block.rs:1`: *"Block control transfers: every branch returns to the dispatcher."* (unless chained).
* `emit_terminal` (`x86_64/block.rs:2757-2878`) is the switch over `Terminator`:
  * `Branch(Branch{target})` → `direct_exit(code, target, ExitKind::Direct)` (`:2864`).
  * `Branch(BranchCondition|BranchZero|BranchBit)` → `branch_condition_with(...)` + `conditional_exit(...)` (`:2865-2871`), producing a fallthrough exit and a taken exit.
  * `DirectCall(target)` → store LR (X30), `paired_exit(...)` with a `Call` direct exit (`:2851-2857`).
  * `Register{source, kind}` → `load_unless_held(...)` into `RAX`, then `register_transfer(...)` (`:2859-2862`).
  * everything else (Trap/System/PAC/Clock/FPU/AMX) → `direct_exit` with its own `ExitKind` at the **instruction** PC.
* `direct_exit` / `direct_exit_with` (`x86_64/block.rs:67-81`): emits an optional empty link slot, an exit patch point, `guest_imm64(RAX, target)`, then `chain::word_return(kind)`. `conditional_exit` at `:83-95`.
* `exit_kind` / `exit_kind_with` (`:20-31`) emit the **dynamic** exit: patch point + `MOV EDX, word` + return; used for `BR`/`BLR`/`RET` where the target is already in `RAX`.
* `register_transfer` (`:2883-2898`): for `BLR`, copies target out of `RAX` before overwriting LR, stores X30, then a paired `Call` exit; for `BR`/`RET`, just `exit_kind`. `paired_exit` (`:2905-2920`) calls `chain::paired_call` when linking is enabled, emits the call exit, binds the continuation and emits a `Fallthrough` exit with `continuation_word` (all `ran` instructions marked skipped — `:2922-2929`).
* `terminal_exits` / `terminal_exits_before` (`:2643-2742`) *rediscover* emitted direct exits by scanning the final bytes with `chain::is_direct_exit`, which is how the compiler reports chainable sites to the engine.
* `chain.rs` byte-level ABI (module doc `:1-81`, constants `:98-168`):
  * `DIRECT_EXIT_BYTES = 10 + DYNAMIC_EXIT_BYTES`; `JUMP_BYTES = PATCH_BYTES = 5`; `EXIT_RETURN_BYTES = 8`.
  * `EXIT_RETURN` (`:143-146`) = `mov rsp,[rdi+exit_rsp]; ret` — every exit restores the host stack pointer before `ret`, so chained code can leave words on the host stack.
  * `EXIT_RSP_STORE` (`:150-155`), used by the entry wrapper in `block_entry.rs`.
  * `RET_ROOM = NOP5 = 0f 1f 44 00 00` — the unlinked patch point.
  * `direct_exit`/`direct_exit_padded` (`:2728-2753`): `[pad | NOP5 | pad] [movabs rax,target] [mov edx,word] [EXIT_RETURN]`. `exit_point` (`:2757-2774`) and `word_return` (`:2777-2792`) build the padding/ending; `NOPS` at `:2816-2822`.
  * `ExitWord` (`:320-350`) packs `kind` + `ran`/`skipped` into `EDX`; `ExitWord::split` is used by the engine.

### 2.4 Branch elimination: chaining and live patching (`x86_64/chain.rs`, engine `scheduler.rs`, `code_cache.rs`)

* Doc `:1-41`: a block's direct exit to a static successor is redirected through a thunk straight into the successor's code, so *"the dispatcher runs once for a chain of blocks"*.
* `ChainControl` (`:352-419`; layout asserted 184 bytes at `:466`) is the per-PE host structure holding `block`, `hits`, `linked`, `account`, quiet/interrupt epochs, table/ITLB/stamps addresses, frame room, pair counts.
* `Account` / `Quiet` (`:471-602`) is a 64-bit packed counter: low 16 bits = transfers left, high 16 bits = instructions retired; `MAX_BUDGET`, `QUIET`. Native code carries it in host register `R13` (`ACCOUNT`) and updates it with **flag-neutral** instructions only: `link_checks` (`:2667-2689`), `count` (`:2693-2695`, `lea`), `stop_check` (`:2641-2655`, `test`), `spend_transfer` (`:2700-2704`).
* `LinkGate` (`:2609-2634`): `Jrcxz` (`mov ecx,[rdx]; jrcxz` twice — portable) or `Pext` (`pext ecx,r13d,[rdx]; jrcxz`, requires BMI2/fast PDEP); `for_target(TargetCpu)` picks. Reason documented at `:2611-2613`: avoid `LOOP` (7 uops on Intel).
* Inline link slots: `link_slot` (`:3007-3023`) reserves `LINK_BYTES=37` or `POLLED_LINK_BYTES=41` before an exit only for `Fallthrough|Direct|Call` exits whose target is in the same 4 KiB page; backward targets get the interruption poll. `empty_link` (`:3027-3035`) fills the slot with a `jmp` over INT3s. `link` (`:3079-3112`) + `SlotLink` (`:3116-3133`) write the body past the point first, then the 5-byte point (`LINKED_SLOT`, `mov rdx,[rdi+anchors+8*Control]`). `thunk` (`:3143-3182`) is the out-of-line equivalent for cross-page/exhausted-slot exits; `link_body` (`:3198-3214`) is shared.
* Atomic patching: `patchable(point)` (`:2827-2829`) requires the 5-byte instruction to fit in one aligned 8-byte word (`point % 8 <= 3`); `patch_pad` (`:2832-2837`), `patch_word` (`:2842-2848`), `direct_patch`/`dynamic_patch`/`slot_patch` (`:2850-2874`), `unlinked` (`:3060-3067`). Writer publishes a single aligned 64-bit store; a concurrent core sees either the whole old instruction or the whole new one. Construction-time patches use plain writes (`patch_native` in the engine); live unlinks use `unlink_points`.
* Engine side:
  * `machine/engine/scheduler.rs:915-933` — `link()`: when the target block exists, build `chain::link(...)`, write `linking.rest`, then `patch_point(linking.point)`. Otherwise `:935-964` installs a `chain::thunk(...)` in a fresh arena slot.
  * `machine/engine/scheduler.rs:572-654` — after `host.execute(...)`, exits are classified: `Fallthrough|Direct|Call` with `btype==0` → `link(...)`; `Return|Indirect|Call` → `link_dynamic(...)`.
  * `machine/engine/code_cache.rs:1400-1420, 1461, 1627, 1670` — eviction/unlink path calls `chain::unlinked(...)` and patches the points back; `:1430-1470` `cut_links_into` cuts links to evicted PCs.
  * `machine/engine/chain_control.rs:1-388` — `Storage` (`UnsafeCell<ChainControl>`, `unsafe impl Send/Sync` justified by *single local writer + atomic epoch*), `Owner::begin/finish/acknowledge_interrupt/interrupt_moved`, monotonic interrupt epoch (`chain::interrupt`/`acknowledge` at `chain.rs:504-553`) so a polled link takes the slow exit when mail is pending.
  * `block.rs:240-246` and `error.rs:184-185,219,349,442-445,513` — supporting metadata/errors (`UnexpectedRegisterBranch`, `UnexpectedMemoryBranch`, `NativeBranchOverflow`, `UnresolvedIndirectTarget`).

### 2.5 Indirect-call / branch dispatch (`chain.rs`, engine `scheduler.rs`, `fetch.rs`)

* `LookupEntry` (`:604-621`, asserted 32 bytes at `:995`) = `{pc, key, entry, block, targets, …}` with atomics in each slot (`Slot::load/store`, `:639-691`). `block_key(physical, state)` (`:964`), `key_frame` (`:969`). `LookupTable` (`:1345-1720`) is the global per-PC/per-key block table with 2 entries per cache line, salting by context (`itlb_salt`, `salt`), growth/mirroring, `find`, `slot`, `reader`, `clear`, `link_cell`, `link_hot_cell`.
* **ITLB** (instruction TLB): `chain.rs:184-190` (14K default entries), `TableReader::find/lookup` (`:854-913`). A probe translates the target's 4 KiB page in the current lookup context before touching the table (module doc `:52-68`).
* **Jump cache**: `chain.rs:1172-1236, 3915-4129`. 256 entries × 64 B (`JUMP_CACHE_ENTRIES`, `JUMP_ENTRY_BYTES`, fields `JUMP_PC/CODE/BLOCK/TARGETS/CONTEXT/EPOCH`). `lookup_tail` / `lookup_tail_code` (`:3936-4065`) compare PC, context, ITLB drop epoch and BTYPE acceptance; on a hit they `count`, bump `HITS`, set `BLOCK` (with BTYPE in bits 33:32) and `jmp [entry.code]`. `fill_jump_cache` (`:4080-4129`) fills the entry after a table probe, using `xchg` (locked `xchg [rdx+JUMP_CACHE],r11`) to publish the PC last and invalidate safely. `forget_jumps` / `forget_all_jumps` (`:1209-1239`) drop entries on TLBI/context change.
* `lookup_dispatcher(btype)` (`chain.rs`; engine caches one per BTYPE at `scheduler.rs:1517-1531`), `probe_and_tail` (`scheduler.rs:1469-1515`): one shared probe per BTYPE per arena part, but **one tail per exit site** so the host can predict each indirect jump separately (module doc `:43-50`).
* `cell_tail` (`chain.rs:4131+`) and `stamped_tail` / `stamped_branch_tail`: a direct exit out of its page gets a dedicated cell (`LookupTable::link_cell`) so its probe needs no hash; hot cells are promoted to stamped tails by `Machine::promote_hot_exits` (`scheduler.rs:1337-1412`, `HOT_SAMPLES`, `PROMOTION_RUNS=1024` at `:9`).
* **Paired calls / returns** (module doc `:75-81`): `paired_call` pushes a host frame with target and contexts; `paired_return`/`paired_tail` (`chain.rs:3980-4010`) let `RET` take a native `ret` served by the CPU's return-address stack instead of a probe + indirect jump. Tested in `chain/tests/paired.rs`, `chain/tests/predicted.rs`.
* **BTYPE / guarded pages**: `engine/fetch.rs:516-533` `indirect_branch_type(pc, register, link)` and `:581-582` (BR/BLR dispatch); `engine.rs:512-525` `dynamic_branch(word)` derives `ExitKind` and BTYPE from the guest word (BR via X16/X17 → 0b01/0b11; BLR → 0b10; RET → 0). `LookupEntry::targets` (META_TARGETS, `chain.rs:621-630`) enforces the target's accepted BTYPE like an Arm BTI check (module doc `:70-73`, `lookup_tail_code:4048-4051`).
* Not a guest switch table: `x86_64/table_switch.rs` (462 lines) and `engine/table_switches.rs` are about **TTBR0 address-space/root-set switching** (`Table`, `Entry`, `Sets`, `switch_thunk`), not language-level `switch` dispatch. Important to avoid mislabelling in adaptation discussions.

### 2.6 Region formation and loop budgets (branch elimination inside a translation unit)

* `block/region.rs:1-20` doc: regions let a system-mode block run past its branches so both sides of `B.cond`/`CBZ`/`TBZ` and multi-block loops stay in one translation. `region::form` (`:65-149`) does a worklist reachability scan from the entry, keeps only reached words, and **accepts the region only if some branch goes back inside it** (`:95-118`); otherwise it falls back to `block_at` (`:48-58`). A self-loop is preferred over a region when `SelfLoop::admit` accepts (`:72-81`). Hard limits: one page, ≤ `MAX_BLOCK_INSTRUCTIONS`, one terminal.
* `x86_64/block/edges.rs:1-20` doc + `struct Region` (`:34-49`) and `Region::plan` (`:51-102`) compute `internal[]`, `landing[]`, `reach[]`, `inner_loop()` (`:177-193`). `edges::edge` (`:216-265`) emits **host `jmp`s** for intra-region edges: forward edges adjust `GuestState::loop_retired` (L) and defer the jump to a landing, back edges add the re-executed rows and compare against `region.limit(to)` from `MAX_BLOCK_INSTRUCTIONS`; at the bound they write back locals, call `settle` and jump to a normal direct exit so the chain's poll still runs. `settle` (`:269-276`) moves L into the chain retired count.
* `block/loop_budget.rs` (30 lines): `#[repr(C)] struct LoopBudget { remaining: u64 }` with `new`, `remaining`, `consumed(initial)`; `REMAINING_OFFSET`. Used by `x86_64/native.rs:97-100 run_with_budget` and tested in `region/self_loop.rs:252-295`. This is the *self-loop* counterpart to the chain `Account`.

### 2.7 Profiling / hot layout and the leaf dispatcher

* `x86_64/executed_paths.rs:1-19`: research tool — forks a child, single-steps with the x86 trap flag, records which host instructions actually execute per guest instruction; emits `B/S/C/T` records; used to justify hot/cold splitting (`CompiledBlock.cold`) and cost work. Not a runtime component.
* `engine/dispatch.rs:1-165`: `LeafDispatcher` — a **different** dispatcher for standalone leaf translations. `DispatchIdentity{address_space, mapping_generation, translation}`, `ResolvedLeaf`, `RegisteredLeaf`, `BTreeMap<u64, RegisteredLeaf>`, `register`/`unregister`/`resolve`/`is_current`. Revalidates SHA-256 and source map on every lookup, invalidates stale handles by generation, and explicitly documents (`:150-152`) that a multi-thread executor would need a separate lifetime guard. `execution.rs:39-141` `ExecutableLeaf::load/execute` maps RX pages and runs the leaf.
* AOT code discovery: `engine/blocks/discovery.rs`, `blocks/aot.rs`, `blocks.rs`, `translations/*`; regions tests in `engine/tests/regions.rs` and `chain_account.rs` assert that fused regions/chained runs end in exactly the same state as linked single blocks.

---

## 3. GameReComp — exact files and functions

### 3.1 Licence and dependencies

* `LICENSE`: **GNU AGPL v3.0 or later**; `NOTICE`: "Everything else is licensed under the GNU Affero General Public License v3.0 or later-compatible terms (AGPL-3.0-only, see LICENSE)"; exceptions: `LICENSES/GameReComp-prior-MIT.txt`, `LICENSES/blast-zlib.txt`, `LICENSES/igi-recomp-MIT.txt`.
* Lifter: Python 3 (`tools/lift/lift32.py` imports `capstone.x86`), plus `lift32_sse.py`, `generate.py`, `recover.py`, `name_lift.py`, `eh32.py`, `multilifter.py`. Runtime: C (`runtime/recomp32/recomp_types.h`, `main.c`), host C compilers (MSVC/GCC/Clang) and SDL/win32 HLE layers. Guest x86-32, typical image base `0x00400000`.

### 3.2 Branch lowering in `tools/lift/lift32.py`

* `Lifter.__init__` (`:265-320`): `iat_map`, `func_names`, `lifted`, `call_pop`, `computed_ret`, `dos`, `patch_sites`; per-function sets `_labels` (block starts) and `_jump_targets` (switch arms), set in `lift_function_linear` (`:2391-2394`). `FUNCTION_LOCALS` (`:239-246`) declares `_itail_tgt`, `_flag_*`, `_cf`, `_df`.
* `Lifter.lift_instruction` control-flow cases (`:1502-1602`):
  * `call` to `call_pop` target (`:1503-1509`): `PUSH32(esp, return_va); RECOMP_FLAGS_OUT(); RECOMP_ITAIL(target); return;` — a jump that keeps a fabricated return address (get-EIP idiom).
  * `call` direct (`:1511-1524`): IAT hit or unlifted target → `RECOMP_ICALL(0xVA)`; known name → `RECOMP_CALL(recomp_<name>)`; otherwise `RECOMP_CALL(sub_<VA>)`. Always followed by `RECOMP_FLAGS_IN()` (`:1542`).
  * `call` indirect (`:1526-1540`): `RECOMP_ICALL(MEM32(addr))`, `RECOMP_ICALL(reg)`, far `call m16:32` pushes `_seg_cs` first; unresolved → `RECOMP_ICALL(0)`.
  * `ret`/`retn` (`:1545-1561`): `RECOMP_FLAGS_OUT(); esp += 4 (+ imm); return;`; in DOS/`computed_ret` mode, `if (MEM32(esp) != RECOMP_RETADDR) { ... RECOMP_RET_JUMP(_rt); return; }` (`:1555-1556`) so `push label; ret` becomes a computed jump. `retf` at `:1563-1570`.
  * `jmp` (`:1572-1592`): target inside `_labels` → `goto L_%08X`; target outside → `RECOMP_ITAIL(0xVA); return;`; indirect → `_computed_jump(expr, comment)`; unresolved → `RECOMP_ITAIL(0)`.
  * conditional jcc (`:1594-1602`): `if (cond) goto L_...` or `if (cond) { RECOMP_ITAIL(va); return; }`; indirect jcc produces a placeholder comment. Condition generation `_make_condition` (`:604-767`) pairs jcc with the statically known flag setter (`_flag_k/_flag_a/_flag_b/_cf`) — the C analogue of Stela's NZCV-liveness work.
* `Lifter._computed_jump` (`:2309-2323`): emits `switch (_jt) { case 0xVA: goto L_VA; ... default: RECOMP_ITAIL(_jt); return; }`. Arms are `sorted(self._jump_targets & self._labels)` — the comment at `:2312-2314` records that listing all labels was quadratic (5 MB → 20 MB and compiler crashes in a 500-function file). This is GameReComp's *static* indirect-branch table, recovered ahead of time.
* `Lifter._has_computed_ret` (`:2325+`), SEH `_itail_tgt` local dispatch (`:2445-2459`): `switch (_itail_tgt) { case ...: goto L_...; default: RECOMP_EH_LEAVE(); RECOMP_ITAIL(_itail_tgt); return; }`.
* `generate.py`: `find_entries`, `linear_disassemble_function` (`:103-220`) find leaders and range-checked switch tables; `_jump_table` (`:446-493`) reads arms of `jmp dword ptr [reg*4 + table]`, allows up to three leading bad entries, then stops, and scans up to 64 dwords *below* the displacement for negative indices. `true_extent` (`:494+`) follows indirect tail calls. `lift_function_linear` (`:246-425`) emits labels, inserts `RECOMP_BACKEDGE()` before backward branches (`:358-363`), handles `push label; jmp func` as a call (`:348-357`) and gaps/int3 as fallthrough or `ITAIL` (`:365-380`). `write_dispatch` (`:1028-1037`) emits `recomp_dispatch_table[]` sorted by VA.
* `tools/macho/survey.py:69-73` `import_map()` builds `{branch-table VA: (lib,symbol)}` so sites lift to `RECOMP_ICALL(va)`; `tools/lift/name_lift.py:64` `IMPORT_CALL = re.compile(r'RECOMP_ICALL\(MEM32\(0x([0-9A-F]+)\)\)')` annotates call sites with resolved DLL/symbol names.

### 3.3 Runtime dispatch in `runtime/recomp32/`

* `recomp_types.h:75-76` — `RECOMP_FLAGS_OUT/IN()` exchange the lazy flag state (`_flag_k/_flag_a/_flag_b/_cf`) with globals.
* `RECOMP_CALL(func)` (`:1182-1189`): saves `g_cur_func`, `PUSH32(esp, RECOMP_RETADDR)`, `FLAGS_OUT/REGS_OUT`, calls, `REGS_IN`, restores `g_cur_func`.
* `RECOMP_ICALL(target_va)` (`:1256-1293`): ring-buffer trace (`g_icall_trace`, `g_icall_from`), then `recomp_lookup_manual(va)` → `recomp_lookup(va)` → `recomp_lookup_import(va)`; an MSVC thunk-pattern special case at `:1266-1273` (`E8 … 59 E9 …` → resolves the real target and sets `ecx`); on hit, `PUSH32(esp, RECOMP_RETADDR); FLAGS_OUT(); REGS_OUT(); _fn(); REGS_IN();` with caller restoration; on **miss, a guest-visible no-op** — a deduplicated diagnostic (`recomp_icall_report_miss`, `:1220-1235`) and no register or stack change (the comment at `:1283-1290` explains why `esp += 4`/`eax = 0` were removed).
* `RECOMP_ITAIL(target_va)` (`:1295-1309`): same lookup chain, `FLAGS_OUT/REGS_OUT; _fn(); REGS_IN()`, but no pushed return address; `va == RECOMP_RETADDR` is treated as a return (setjmp); otherwise `recomp_itail_report_miss`. `RECOMP_RET_JUMP(target_va) = RECOMP_ITAIL(target_va)` (`:1313`).
* `main.c:104-123` `recomp_lookup(va)`: **binary search** over `recomp_dispatch_table[]` sorted by address; `recomp_lookup_manual` scans a tiny override array (`:126-136`); `recomp_lookup_import` scans `g_import_bridges` (`:142+`). `generate.py:1028-1037` produces the sorted table and `recomp_funcs.h`.
* `recomp_types.h:1375-1384` `RECOMP_BACKEDGE()`: increments `g_backedges`, and every `RECOMP_YIELD_EVERY = 0x10000` taken backward edges spills registers and calls `recomp_yield_hook()` — the AOT analogue of Stela's polling loop budget.

---

## 4. Direct comparison of the four requested axes

### 4.1 Control-flow handling
Stela *never* emits a native branch out of a translated block for a guest control transfer: every block ends in a typed exit stub (`direct_exit` for static targets, `exit_kind` for register targets), with PC in `RAX` and `(kind, ran/skipped)` in `EDX` and a uniform `EXIT_RETURN`. GameReComp emits ordinary C `goto`/`if` inside a function and only exports at function boundaries; the host C compiler owns block layout, fallthrough and machine-level branches.

### 4.2 Branch optimisation
Stela: (a) **region formation** fuses multi-block loops into one native translation with internal host jumps and an L register (`block/region.rs`, `x86_64/block/edges.rs`); (b) **self-loops** hoist checks and run iterations natively with `LoopBudget`; (c) **hot/cold splitting** guided by trap-flag samples (`CompiledBlock.cold`, `executed_paths.rs`); (d) flag-neutral link accounting so NZCV can stay in host flags across links. GameReComp: (a) C compiler does register allocation, branch layout and simple block scheduling; (b) `_computed_jump` restricts the C `switch` to known arms; (c) `RECOMP_BACKEDGE` inserts periodic yield points; no runtime layout changes.

### 4.3 Branch elimination
Stela eliminates the dispatcher round-trip by *runtime patching*: same-page static successors get an inline 37/41-byte link slot; others get an out-of-line thunk; both are installed with one aligned 8-byte store (`patch_word`), reverted on eviction (`unlinked`), gated by a transfer budget (`Account`) and an interrupt epoch. Cross-page and dynamic exits use lookup tails, a per-site jump cache and stamped cells. GameReComp has no runtime patching; the closest elimination is `goto L_...` for intra-function branches and the direct `RECOMP_CALL(sub_X)` for statically known calls (which becomes a real host `call` instead of a table lookup).

### 4.4 Indirect-call / dispatch techniques
Stela: multi-tier — ITLB (context-tagged page translation) → global block table (PC+physical frame+state key) → per-site jump cache → stamped tails; plus paired host frames for RET, BTYPE validation, per-site tails for branch prediction, and generation/stamp invalidation on TLBI/code change. GameReComp: one uniform runtime macro (`RECOMP_ICALL`/`ITAIL`) over a sorted static table with binary search, manual and import override tables, a trace ring, and a conservative no-op on miss; switch tables are recovered AOT and become C `switch` statements.

---

## 5. Correctness / safety properties worth noting

* Stela's live patch protocol (aligned 8-byte writes, write body before point, never rewrite a linked body, grace periods before code reuse) is documented at `chain.rs:27-41` and exercised by `chain/tests/patching.rs`, `shared.rs`, `stamped.rs`, `differential.rs`.
* Stela's stale-code defence: engine `Block.source_hash` (FNV-1a over admitted bytes), `STALE_CHECKS = 1024`, `StaleCode` report (`engine.rs:534-599`); `LeafDispatcher` additionally re-verifies SHA-256 on every lookup. GameReComp has `patch_sites` for known self-modifying immediates but no general hash-based invalidation.
* GameReComp's `RECOMP_ICALL` miss-is-a-no-op rule (`recomp_types.h:1283-1290`) is a deliberate semantic choice; any cache added must preserve it.
* GameReComp's `RECOMP_FLAGS_IN/OUT`, `RECOMP_REGS_IN/OUT` and `g_cur_func` save/restore are the equivalent of Stela's exit word + pinned frame; they must remain around any new dispatch fast path.

---

## 6. Exact file/type index (quick reference)

| Topic | Stela (path : line) | GameReComp (path : line) |
|---|---|---|
| Exit kind enum | `stela-compiler/src/block.rs:60-78` `ExitKind` | implicit in `RECOMP_ICALL` vs `RECOMP_ITAIL` vs `RECOMP_CALL` (`recomp_types.h:1182,1256,1295`) |
| Terminator / decoder | `block.rs:257-309` `Terminator`, `decode_terminator` | `tools/lift/lift32.py:1502-1602` `lift_instruction` control-flow cases |
| Direct branch | `x86_64/block.rs:67-95,2863-2871` `direct_exit`, `conditional_exit` | `lift32.py:1579-1600` `goto L_...` |
| Indirect branch | `x86_64/block.rs:2859-2898` `register_transfer` + `x86_64/chain.rs:3915-4129` `lookup_tail`/`fill_jump_cache` | `lift32.py:2309-2323` `_computed_jump` + `recomp_types.h:1295` `RECOMP_ITAIL` |
| Indirect call | `chain.rs` lookup dispatcher + paired call (`engine/scheduler.rs:1469-1531`) | `lift32.py:1525-1540` + `recomp_types.h:1256-1293` `RECOMP_ICALL`; `main.c:107` `recomp_lookup` |
| Return | `chain.rs:3980-4010` `paired_tail`, `engine/fetch.rs:519-533` | `lift32.py:1545-1570` + `RECOMP_RET_JUMP` (`recomp_types.h:1313`) |
| Link patching | `chain.rs:2824-2874, 3001-3182`; `engine/scheduler.rs:915-964`; `code_cache.rs:1400-1470` | n/a (AOT C) |
| Region / fused branches | `block/region.rs:65-149`; `x86_64/block/edges.rs:34-265` | one C function + labels (`generate.py:246-425`) |
| Loop bound / yield | `block/loop_budget.rs:1-30`; `chain::Account` `chain.rs:555-602` | `RECOMP_BACKEDGE()` `recomp_types.h:1375-1384` |
| Jump table discovery | n/a (guest is AArch64) | `generate.py:446-493` `_jump_table`; `lift32.py:2309-2323` |
| Dispatch registry | `engine/dispatch.rs:61-165` `LeafDispatcher`; `chain.rs:1345-1720` `LookupTable` | `generate.py:1028-1037` dispatch table; `main.c:107-123` binary search |
| Hot/cold & profiling | `x86_64/executed_paths.rs:1-19`; `CompiledBlock.cold` `block.rs:235-239`; `scheduler.rs:1355-1412` `promote_hot_exits` | none |

---

## 7. Adaptation candidates for GameReComp (ranked by risk/benefit)

All items are *techniques*, not code copies. None require a JIT.

1. **Per-callsite inline cache in `RECOMP_ICALL` (high value, moderate risk).** Stela's jump cache (`chain.rs:1172-1236`, `lookup_tail_code:4013-4065`, `fill_jump_cache:4080-4129`) shows the pattern: a small direct-mapped array keyed by target VA, tagged with a *generation/epoch* that is bumped whenever translation state changes, with a locked publication of the tag last. In GameReComp the static dispatch table never changes at runtime in normal runs, so a 1-entry per macro-site cache (`static uint32_t last_va; static recomp_func_t last_fn;`) guarded by `if (_va == last_va)` replaces the binary search with one compare. Must keep the `RECOMP_FLAGS_OUT/REGS_OUT` order, the `RECOMP_RETADDR` push, the MSVC thunk special case, and the miss-is-no-op rule. Global invalidation (`recomp_invalidate_icaches()`) needed only for code-reload/variable-table paths.

2. **Typed exit kind for indirect transfers (low risk).** Stela's `ExitKind`/BTYPE (`block.rs:60-78`, `engine.rs:512-525`) makes call vs tail vs return explicit. `RECOMP_ITAIL` currently overloads `va == RECOMP_RETADDR` to detect a return (`recomp_types.h:1306-1307`). Adding explicit `RECOMP_ITAIL_KIND(va, kind)` or separate `RECOMP_IRET` macros for computed returns would make the SETJMP case deliberate and open the door to kind-specific caches/statistics without changing generated C semantics.

3. **Indirect-jump arm validation at runtime (low risk).** GameReComp's `_computed_jump` `default:` currently tail-dispatches an arbitrary computed value. Stela validates BTYPE acceptance against the entered block (`chain.rs:4048-4051`, `LookupEntry::targets`). Adding a debug/checked build assertion that the default `_jt` is one of the recovered `jump_targets` (or logging a distinct reason) would catch bad recovery earlier, mirroring `recomp_itail_report_miss` deduplication.

4. **Hot-exit promotion for `RECOMP_ITAIL` sites (medium value, moderate risk).** Stela's `promote_hot_exits` (`scheduler.rs:1337-1412`) counts samples per exit site and only after `HOT_SAMPLES` promotes it to a stamped tail. GameReComp can reuse the existing `g_icall_trace`/`g_icall_from` ring (`:1257-1261`) as the sampling source and, after N hits from one caller+target pair, emit/patch a C-level fast path (e.g. a generated `switch` case or a per-site cached function pointer in the same chunk of C). This needs a code-generation change, not a runtime binary patch; treat it as an optional second phase.

5. **Stale-code detection (medium value, low risk).** Stela hashes admitted guest bytes (`engine.rs:534-599 source_hash`, `LeafDispatcher` SHA-256 revalidation at `dispatch.rs:128-148`). GameReComp's `patch_sites`/`_patched_imm` (`:297-300`) handles only known self-modifying immediates. Adding an optional FNV-1a over each lifted function's original bytes and re-checking before *dispatch* (cheap for the once-per-function case) would close the same gap; keep it opt-in for games known to smoke-test code.

6. **Loop/budget parity (low value — mostly already present).** `RECOMP_BACKEDGE` (`recomp_types.h:1375-1384`) already implements Stela's "don't let a native loop starve the host" idea with `RECOMP_YIELD_EVERY = 0x10000`. The only worthwhile refinement is Stela's *native counter in a register* idea: in GameReComp the C compiler already keeps `g_backedges` in memory; making it a `register`/TLS hot variable or lowering the yield interval for games with cooperative threads would be a measurable tweak.

7. **Region fusion and profile-guided layout (low value for AOT C).** Stela's regions and hot/cold split (`block/region.rs`, `executed_paths.rs`, `CompiledBlock.cold`) exist because the native code is generated by hand. In GameReComp the C compiler already does this with `goto`, basic-block reordering and `PGO`. If desired, add `__builtin_expect`/`[[likely]]` hints or `-fprofile-generate/use` to the generated C rather than emulating region formation.

8. **Atomic patching / thunks / paired host frames — not applicable.** The aligned-8-byte patch protocol, link slots, ITLB, jump cache tails and paired-frame `ret` prediction depend on Stela owning machine-code emission and a per-PE control block. They would only become relevant if GameReComp gained a JIT; record them as future-option, not an adaptation candidate.

---

## 8. Licence / reuse constraints for any adaptation

* Stela control-flow files are under the workspace GPL-2.0-or-later (`stela-compiler` also declares MIT, `stela-engine` also declares BSD-3-Clause **in addition to** GPL-2.0-or-later via `AND`). GameReComp is AGPL-3.0-or-later. Do not copy Stela source text into GameReComp without a per-file licence review; AGPLv3 compatibility of GPLv2-or-later material is generally workable but must be checked, especially for the `LICENSES/`-covered upstreams. The recommendations in §7 are deliberately behavioural descriptions, so a clean-room implementation avoids the issue entirely.
* Conversely, GameReComp's `RECOMP_ICALL`/`_computed_jump` design is AGPL; nothing in this report proposes moving it into Stela.

---

## 9. Scope / verification limits

* All findings are from static reading of the pinned trees; line numbers refer to those exact snapshots.
* No build, test, harness or live-game validation was performed for this audit (read-only mandate). Where a claim is about measured cost (e.g. `chain/tests/costs.rs`, `executed_paths.rs`), it is reported as the project's own documented rationale, not independently verified here.
* `x86_64/table_switch.rs` was confirmed to be TTBR0/page-table switching, not guest switch/case dispatch; GameReComp's switch/case handling lives in `generate.py::_jump_table` and `lift32.py::_computed_jump`.
