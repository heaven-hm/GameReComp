# S7 audit — guest memory access: Stela vs GameReComp

**Date:** 2026-11-23
**Sources (read-only, no edits made):**
- `STELA` = `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/stela`, pinned `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe` (the snapshot has no `.git`, so the pin could not be re-verified from git metadata; taken as given by the task).
- `GAMERECOMP` = `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/integration`.

**Method:** static source reading only. No builds, no runtime harness, no repo modifications. Line numbers refer to the files in those two snapshots.

---

## 1. Executive summary

The two projects sit at opposite ends of the fidelity/complexity trade-off for guest memory:

| | Stela (arm64 guest → x86-64 host) | GameReComp (x86 guest → C on host) |
|---|---|---|
| Primary access | generated host machine code with an **explicit range check before every (elided) access**, then a plain host load/store into guest-mapped RAM | C `volatile T*` dereference through a VA→host-address macro; **no per-access bounds check** |
| Fault model | typed, recoverable: translation / permission / alignment / tag-check / stack-alignment statuses, recovery sites, delayed destination commits | host SIGSEGV/SEH only; `crash_report.c` prints state and then **continues the fault to die** |
| Non-ordinary access | typed memory-service callback carrying width, flags (WRITE, NON_TEMPORAL, ATOMIC, ORDERED, EXCLUSIVE, ASIMD, PAIR, SVE/SME), memory order and guest PC | inlined plain C; `lock`/`movntq`/`mfence`/`prefetch` are stripped or emitted as comments because "the runtime runs lifted code under one machine lock" |
| Address calculation | 64-bit guest VAs, canonical/native-range check, optional per-page top-byte strip, optional software-TLB translation helper, data-dependent masked-base reuse | 32-bit guest VA arithmetic in C, `ADDR()` selects flat / +`g_mem_base` / fixed base / wasm 64 KiB segment table |
| Aliasing/ordering control | pinned host registers with explicit clobbers, dirty/homes recovery metadata, semantic spans | C `volatile` forces each access; `RECOMP_LOCAL_REGS` exists precisely because globals alias any guest store and must be reloaded after every `MEM32` write |

**Bottom line:** Stela's *ideas* (parse-time range-check elision, speculation-safe failed-check masking, descriptor-carrying requests, staged commit) are the valuable adaptation candidates for GameReComp. Stela's *code* is GPL-2.0-or-later (crate also tagged MIT) and x86-64-machine-code-specific; GameReComp is AGPL-3.0-only, so copying source needs legal review — re-implementing the algorithms in the lifter is the safer route. GameReComp's own model is internally consistent for a single-threaded guest under a global machine lock, but it has no answer for unmapped/protected guest addresses, non-temporal/ordering hints, or host-thread races.

---

## 2. Stela — exact files, functions, and mechanisms

Paths below are relative to `STELA/crates/stela-compiler/src/` unless stated otherwise.

### 2.1 The three lowering paths

| Path | Entry points | When used |
|---|---|---|
| **Direct ordinary RAM** | `x86_64.rs::memory` (line 393) and `x86_64.rs::memory_pair` (line 1042) → `memory_recipe.rs::scalar` (614) / `pair` (825) | Process/default profiles: guest VA is a native address; `MemoryChecks::DEFAULT` has `guest_range: false`, `alignment: false`, `stack_alignment: true` (x86_64.rs:466-477) |
| **Checked / memory-service callback** | `block/memory.rs::CheckedPlan::new/emit` (125/196) → `block/memory/pointer.rs::CheckedOperation::emit` (79) → `x86_64/memory_store/checked_scalar.rs`, `checked_pair.rs`, `x86_64/simd/memory_checked.rs`; ABI in `memory_context/callback.rs` | "checked memory" specialization selected from process controls; all scalable/exclusive/shared-memory accesses |
| **Translated (software TLB)** | `MemoryChecks::with_translation` (x86_64.rs:509), `RangeCheck::emit_translated` (753); placement in `STELA/crates/stela-engine/src/machine/mmu/placement.rs` | Guest address not natively covered; helper returns host address in RAX, with a 2-byte access descriptor placed after the call |

The **memory-service ABI** is defined in `context/memory.rs`:
- constants at lines 15-23 (`MEMORY_CONTEXT_VERSION = 2`, `MEMORY_REQUEST_BYTES = 32`, field offsets);
- `flags` module at lines 25-45: `WRITE`, `TAG_CHECKED`, `NON_TEMPORAL`, `SME`, `SVE_PREDICATED`, `ASIMD`, `PAIR`, `ATOMIC`, `ORDERED`, `EXCLUSIVE`;
- `MemoryRequest` (233-241): `{address: u64, data: *mut u8, bytes: u32, flags: u32, pc: u64}`;
- `MemoryCallback` (263-271): `extern "sysv64"` on x86-64, `extern "C"` elsewhere;
- `MemoryContextView` (275-290): version, bytes, callback, opaque, `fault_kind`, `fault_access`, `fault_address`, `fault_pc`, `completed_bytes`, `reserved_tail`;
- `MemoryStatus` (183-229): Success / Translation / Permission / Alignment / TagCheck / Unsupported / InvalidRequest / Domain / StackAlignment;
- `MemoryOrder` in `context/memory/operation.rs:12-20`: Relaxed / AcquireRcpc / AcquireRcsc / Release / AcquireRelease;
- `MemoryRequirements::merge` (context/memory.rs:110-157) refuses incompatible SP/address/pointer-arithmetic specializations in one body.

The call sequence is emitted in `x86_64/memory_context/callback.rs`:
- `begin` (41) clears fault_kind/completed_bytes;
- `request` (61) writes the 32-byte request onto a private stack slot (address, staging pointer, length, flags, guest PC);
- `call` (107) sets MXCSR to the power-on value `0x1f80`, calls `[memory_view + MEMORY_CALLBACK_OFFSET]`, restores the caller's full MXCSR. It preserves RDI/R15 and clobbers other caller-saved GPRs and XMMs. **No callback address is embedded in native code.**
- `branch_failed` (130) / `fault` (138) / `address::return_if_failed` (address.rs:32) handle the outcome.

### 2.2 Address calculation

**Base + immediate / register offset:**
- `x86_64.rs::address_add` (1007-1028): RAX = base + disp8/disp32 via `LEA`, mod 2^64; pre/post-index leaves pending writeback in RDX; zero-disp offset emits nothing.
- `x86_64/memory_context/address.rs::immediate` (42), `immediate_with_checks` (51), `register_offset` (85), `register_offset_with_checks` (98), `check_sp` (15). The checked forms apply FEAT_CPA2 `AddressAdd` marking after arithmetic via `x86_64/pointer_checks.rs` and keep RDX/R8 intact across the check.
- `x86_64/register_offset.rs::index_add` handles extend/shift forms.

**Guest-range check (bounds):**
- `x86_64.rs::RangeCheck` (573-858). `emit_base` (679), `compare`/`test` (700), `mask` (706), `emit` (718), `emit_translated` (753).
  - Five-level: `shift 48, bias 1, bound 1` — bits 63:48 all clear or all set.
  - Four-level: `shift 41, bias = 64 - slices, bound = 127 - slices` — guest gets `[0,2^47)` and `[U,2^64)`; Stela's window and an unmapped guard live in the slices below `U` (`stela-engine/src/machine/mmu/placement.rs:1-78`).
  - Emitted shape (x86_64.rs:546-572): `mov rcx,rax; sar rcx,shift; add ecx,bias; cmp ecx,bound; jbe ok; ud2; ok: mov rcx,masked; cmova rax,rcx`. The **CMOVA is data-operand-independent**: on a mispredicted branch, a failed check still replaces RAX with an unmapped address (zero-extended `ECX`) before the guest access, so speculation cannot reach Stela's own window. Pinned code uses `cmov` to `GuestState::unreachable` (`unreachable_if`, 923).
- `x86_64.rs::range_check` (954) strips the top byte first (if tagged), then emits the plain or translated check. `guest_range_check` (965) and `address_checks` (975) add SCTLR_ELx.A alignment (`alignment_test`, 917; `test al,unit-1; jz; ud2`).
- **Check elision:** `x86_64/guest_range.rs::checks` (37) marks which accesses need their own range check. An immediate-offset access through a base already checked in the block needs none, provided the base has only moved by immediates; the conservative bound is 2^35 from the checked access, and `after` (64-193) forgets bases on loads/unknown operations. Rationale in the module header (lines 1-18). `MAX_BLOCK_INSTRUCTIONS = 256` (`block.rs:58`), so the 1,024-instruction bound used in the comment is conservative.
- **Masked-base reuse:** `x86_64/masked_base.rs` (header 1-25, `Plan` 41, `Tracker::plan` 316, `after` 350). A checked access that will be reused keeps its masked RAX in slot R11/R10 (`SLOTS`, 31); later accesses through the same base use `[slot + delta]`. `DELTA_LIMIT = 1 << 30` (34), `LOOKAHEAD = 32` (37). Kept addresses live in registers, never frame slots (speculation could reload a stale slot). Loop back edges are handled by `carriers`/`carried` (272/288).
- **Top-byte ignore:** `x86_64/top_byte.rs::TopByte` (30-100). `Kept/Lower/Upper/Both` implement TCR_ELx.TBI0/TBI1 `AddrTop`; emitted as 2 (`shl rax,8; sar rax,8`) or 4 instructions. Selected per fetch page by the engine once a page sees a tagged access (module comment 1-6).
- **FEAT_CPA2 pointer marking:** `x86_64/pointer_checks.rs::emit` (14-47) and `x86_64/checked_pointer.rs::emit` (10-96). Uses bit 54 as a detection bit = NOT bit 55 of the base, preserving result[53:0] and base[63:55]; multiplication overflow is captured from IMUL's OF into R8. Branchless. `context/memory/address.rs::AddressAddChecks::apply` (22-29) mirrors it on the Rust side.

### 2.3 Generated loads and stores

- `x86_64/memory_recipe.rs::scalar_load` (591-612): `movzx`/`movsx`/`movsxd`/`mov_load` chosen by (bytes, signed, width). Every load is wrapped in `Code::guest_access` (`x86_64/asm/code.rs:273-299`), which records a recovery `EmissionSite` covering exactly one guest memory instruction.
- `memory_store.rs::emit` / `emit_to` (45/56): `mov_store` for 1/2/4/8 bytes, `movd`/`movq`/`movdqu` for XMM sources. Module comment (1-4): a store crossing a page is one host store because x86 faults before writing either page; **assumes a protection granule ≥ 4 KiB**.
- Pairs: `memory_recipe.rs::pair_alignment` (727), `quadword_pair_store/load` (760/770), `quadword_pair_{store,load}_direct` (786/807). A 16-byte-aligned LDP/STP of X registers becomes one `MOVDQA`, relied on for FEAT_LSE2 single-copy atomicity; tested alignment uses `test at,15` + two paths.
- Fast paths in `direct` (135) and `reused` (210) turn `[base+disp]` into a single host instruction when pinned and checks permit; `direct_writeback`/`reused_writeback` (174/236) commit writeback only after the access.
- **Commit discipline:** `memory_recipe.rs::scalar` (614-709) and `pair` (825-1050) stage results in scratch GPRs and write guest destinations only after all faultable element accesses succeed (lines 666-694, 957-1035); writeback is last (697-708, 1038-1049). `CompareLoads` (342-565) fuses `ldr; ldr; cmp` subject to strict check/alias constraints.
- Fault tails: `alignment_tail` (x86_64.rs:928) emits UD2, or in pinned code a `cmov` of RAX to `GuestState::unreachable`. `Code::place_failures` (asm/code.rs:195) places one UD2 per failed check with its recovery site.

### 2.4 Licence, dependencies, portability

- Workspace licence: GPL-2.0-or-later (`STELA/Cargo.toml`, `LICENSE`, `NOTICE`). **`stela-compiler/Cargo.toml` declares `license = "GPL-2.0-or-later AND MIT"`**; no per-file SPDX headers were found in the memory files. Shipping programs have mixed licences (`NOTICE` lines 10-21).
- `stela-compiler/Cargo.toml` dependencies: `serde` (alloc/derive, std feature), `rustc_apfloat`, build-dep `stela-fingerprint`. No `libc`. Crate is `no_std`-capable (`default = ["std"]`). The engine uses `libc` (`mmap`/`munmap`) and platform crates.
- Portability: **only x86-64 hosts run translated code** (`context/memory.rs:268-271`). The generated code is hard-wired to x86-64 GPRs (RAX/RCX/RDX/RDI/R8/R10/R11/R15), LEA/CMOV/UD2/MOVDQA/MXCSR. Rust-side model types compile on other targets. Fixed `sysv64` callback ABI, 64-bit guest VAs, 4 KiB protection granule, and a 2 TiB-slice placement model are all x86-64-specific.
- Notable correctness risks *within Stela* (observations, not verdicts):
  1. Speculation safety depends on `GuestState::unreachable` and the guard slice being genuinely unmapped by every root; a mapping change that makes either reachable breaks the CMOVA defence.
  2. `memory_store.rs` assumes ≥4 KiB protection granule for cross-page store atomicity; MMIO or finer-grained host mappings would need a different lowering.
  3. `callback::call` saves/restores MXCSR only; x87 control word and the full FP tag state are the guest frame's responsibility, and all XMMs are caller-clobbered — a future family must spill via `dirty_homes` or it will lose vector state.
  4. `MemoryRequest` is private staging; `context/memory.rs:231-232` warns reads may mutate staging before a later part fails, so every family must delay destination commits. This is enforced by convention and per-family code, not one central checker.
  5. ABI version/layout checks are strict and defensive (`MemoryContextView::has_supported_header` 293, tests 459-489), which is good, but a stale compiler/engine pair simply refuses rather than degrading — expected, worth noting.
  6. `checked_pointer.rs:13-16` emits a bare `nop` for discarded CPA results; correctness relies on compile-time feature/control admission having happened earlier (`CheckedOperation::new`, `block/memory/pointer.rs:53-67`), a subtle invariant.

---

## 3. GameReComp — the comparison targets

### 3.1 `runtime/recomp32/recomp_types.h`

- `ADDR(va)` (lines 177-199): four build modes — `RECOMP_FLAT_MEMORY` (`(uintptr_t)(uint32_t)(va)`), `__wasm32__` (`recomp_wasm_va`, 186-190, via `g_wasm_seg_base[va>>16]` with **raw `va` fallback when the segment base is zero**), `RECOMP_MEM_BASE` (constant host base), default `(uintptr_t)(uint32_t)(va) + g_mem_base`. `g_mem_base` is `ptrdiff_t` (175). Segment overrides: `FS_BASE`/`GS_BASE` (204-207), `ES_BASE` for DOS (213-214).
- `MEM8/16/32/64/F/D` (229-234): `*(volatile T *)ADDR(addr)`. `volatile` is the **only** memory-side semantic: it prevents elision/reordering of the access itself but provides no bounds, atomicity, ordering, or non-temporal behaviour.
- `MEMSET32`/`MEMSET16` (237-247) use **non-volatile** `uint32_t*`/`uint16_t*` loops, so they do not carry the same alias/ordering guarantees as `MEM*`.
- `recomp_rep_movs` (256-265): memmove fast path with overlap check, `memcpy` per element in the overlapping case — models x86 element-by-element copy, as documented (249-255).
- `PUSH32`/`POP32_VAL`/`POP16_VAL` (276-326): operand evaluated before SP changes; the 16-bit forms preserve the upper half of EBP (comment 304-314).
- `RECOMP_LOCAL_REGS` (98-121): locals stay in host registers; **comment 102-105 explicitly says globals can alias any guest store, so the optimiser had to reload them after every `MEM32` write** — a direct consequence of the volatile-pointer model.
- `recomp_addr_readable` (1192-1201): only checks the wasm segment table; on every other target it is `(void)va; return 1`. It is used for indirect-call thunk detection (1266-1269), not for `MEM*` accesses.
- `RECOMP_BACKEDGE`/`recomp_yield_hook` (1374-1383): guest threads are cooperatively serialised; the counter is deliberately unsynchronised because only the lock holder runs lifted code.

### 3.2 `tools/lift/lift32.py`

- `_fmt_mem_addr` (400-439): emits `base + index * scale + disp` as a C expression from Capstone operands; handles relocation/patch displacement, negative disp as `(-0xN)`, and `FS:`/`GS:`/`ES:` overrides via `FS_BASE`/`GS_BASE`/`ES_BASE`.
- `_fmt_mem_read` (441-452) and `_fmt_mem_write` (454-465): map width 1/2/4/8 to `MEM8/16/32/64`; fallback is `MEM32` for any other size. `_fmt_lea` (467-469) reuses `_fmt_mem_addr` with **no access**.
- `_mm_read`/`_mm_write` (322-336) map 64-bit MMX memory operands to `MEM64`.
- String ops (1414-1444) emit direct `MEMn(edi) = MEMn(esi)` / `SET_LO8(eax, MEM8(esi))` sequences.
- `lock` prefix stripped at 792-797 with the explicit comment that semantics are exact because "the runtime runs lifted code under one machine lock" (same rationale at 1873-1877 for `xadd`/`cmpxchg`).
- `movntq`, `sfence`/`lfence`/`mfence`, `prefetch*` (2167-2175) are emitted as plain moves or comments — the non-temporal hint and ordering are discarded.
- `RECOMP_GENERATED_CODE` output therefore has **no width/flags descriptor, no bounds check, and no fault path** at a memory site.

### 3.3 GameReComp correctness/portability risks specific to memory

1. **No guest-address validation.** Any wild guest pointer becomes a host dereference; `crash_report.c::veh_handler` (58-90) only reports `EXCEPTION_ACCESS_VIOLATION` and returns `EXCEPTION_CONTINUE_SEARCH`. Guest page faults/protection faults cannot be delivered to lifted code, so self-modifying or deliberately invalid accesses diverge from real x86.
2. **`volatile` is not atomic or ordered.** It is sufficient only because of the global machine lock. Host-side threads (DSound mixer, video/input locks in `runtime/win32hle/*`) touch guest memory outside that discipline; a host DMA-like writer can race a volatile guest access with no defined ordering. Stela's `MemoryOrder`/exclusive monitor has no counterpart here.
3. **Alignment UB.** `*(volatile uint32_t*)` asserts 4-byte alignment to the C abstract machine; `MEM64`/`MEMD` assert 8-byte alignment. x86 hardware tolerates unaligned, but the C is UB and LTO/UBSan or a strict-alignment host (e.g. 32-bit ARM) can break it. Stela emits explicit `movzx`/`mov_load`/`movdqu` with no assumed guest alignment (and only checks it when SCTLR_ELx.A is set).
4. **wasm fallback hole.** `recomp_wasm_va` returns the raw `va` when `g_wasm_seg_base[va>>16] == 0` (recomp_types.h:186-190), so an unmapped guest VA aliases low linear memory rather than faulting; `recomp_addr_readable` mirrors that, but only the indirect-call thunk consults it.
5. **Address arithmetic is 32-bit C.** `base + index*scale + disp` wraps as `uint32_t`, which matches x86, but there is no signed-overflow/canonicality concept and `FS_BASE + (addr)` also wraps at 32 bits, so a segment base above 4 GiB is unrepresentable.
6. **Non-volatile bulk helpers.** `MEMSET32`/`MEMSET16` can be reordered relative to nearby `MEM*` accesses by the optimiser; `recomp_rep_movs`' overlap fast path is correct by its check, but `memcpy` per element still assumes no aliasing beyond that case.
7. **`MEM32` reload tax.** The `RECOMP_LOCAL_REGS` comment shows every guest store forces a reload of host-resident register copies; Stela solves the analogous problem with explicit clobbers/dirty tracking and recovery sites rather than relying on the compiler's alias analysis of volatile pointers.

---

## 4. Adaptation candidates (ranked, idea-level)

1. **Range-check elision in the Python lifter (highest value, lowest risk).**
   Port `guest_range.rs::checks` as a per-function dataflow pass over the lifted instructions: mark the first memory access through each base register and clear the base on any load/unknown write; emit `RECOMP_CHECK_ADDR(expr, size, write)` only at marked sites. GameReComp's 32-bit flat mapping reduces `RangeCheck` to a one-sided `cmp va, guest_limit` (or a single table lookup), and the guest limit is known at build time. No new runtime dependency; the check can be a weak inline helper so existing targets keep their behaviour. This closes risk 1 above without touching the emitted `MEM*` expression shape.

2. **Speculation-safe failed-check masking.**
   If range checks are added, copy the *principle* of `RangeCheck::mask`: on failure, replace the address with a value guaranteed unmapped by the host, before the volatile access. In C this needs care: a plain `if (bad) return fault();` can be speculatively bypassed by the CPU. A helper that computes `addr = bad ? unmapped_addr : addr` with a data dependency (or an architecture-specific barrier) is closer to Stela's `CMOVA`. Do not copy Stela's machine code verbatim; re-derive for the target host.

3. **Typed request descriptor for the cases GameReComp currently flattens.**
   Introduce (only where needed) a small `recomp_mem_request {va, size, flags, pc}` plus one runtime entry point, initially used for `lock`-prefixed accesses, `movntq`, and fences. Since GameReComp already assumes a single machine lock, the flags can start with just `WRITE | NON_TEMPORAL | ATOMIC`; this restores observability of access intent (section 3.3 risks 2 and 3) and gives a hook for MMIO/DMA later. Keep the fast `MEM*` path for ordinary accesses so generated code size does not regress.

4. **Staged commit discipline for multi-part operations.**
   Adopt Stela's rule "do not write any guest destination or base until every part of the access has succeeded" for `rep movs/stos`, `pushad/popad`, FPU/MMX memory forms, and the 16-bit stack helpers. This is cheap C-level restructuring and makes a future fault-injection recovery path possible.

5. **Strict generated-code ↔ runtime ABI versioning.**
   Stela refuses mismatched `MEMORY_CONTEXT_VERSION`/layouts. GameReComp's interface is only a C header; adding a version/layout assertion (and a `__attribute__((weak))` hook) would catch stale lifted objects early, especially when changing `ADDR()` modes.

6. **Register-alias discipline instead of volatile reloads.**
   If the `RECOMP_LOCAL_REGS` reload cost matters, study Stela's `pinned.rs` / `memory_recipe.rs` `clobbers`/`dirty`/`homes` and `guest_access` spans rather than copying them. A C-lifter analogue would require the generator to track which locals are live across each `MEM*` site — a larger change, and risky given the current reliance on C volatile for correctness.

**Not applicable to GameReComp (x86 guest):** `TopByte` TBI strip, FEAT_CPA2 pointer marking, LSE2 16-byte pair atomics, and Arm SP/alignment checks. These should be explicitly excluded from any porting effort.

---

## 5. Licence and dependency constraints for any adaptation

- **Stela:** code is GPL-2.0-or-later; `stela-compiler` additionally declares `AND MIT`. GNU AGPL-3.0-only is GameReComp's licence. Combining GPL-2.0-or-later code into an AGPL-3.0 work is theoretically possible (GPL-2.0-or-later → GPL-3.0 → AGPL §13 combined work) but requires preserving all notices, and the "AND MIT" expression means both licence sets apply to the same file, not a free choice. **Legal review required before copying any Stela source text.** Re-implementing the algorithms from the described behaviour, with no copied expression, avoids this; the algorithmic ideas themselves are not copyrightable.
- **GameReComp:** AGPL-3.0-only; existing third-party notices must be preserved (`NOTICE`: zlib blast Python port, igi-recomp MIT shim; `LICENSES/GameReComp-prior-MIT.txt`). No new third-party dependency is needed for candidates 1-6.
- **Dependencies of the relevant Stela code:** serde, rustc_apfloat, stela-fingerprint (build), libc only in the engine/mapping layer. None of these need to enter GameReComp.
- **Portability:** all Stela memory emission is x86-64-only; any adaptation must be expressed in GameReComp's target language (C macros/helpers + Python lifter) and validated on its supported hosts (x86-64, arm64 via the non-x86 `__rdtsc` fallback, wasm32, 32-bit/DOS targets).
