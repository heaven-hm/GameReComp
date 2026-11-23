# S3 audit — Register mapping, host-register usage, register allocation

**Sources (read-only):** Stela at `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/stela` (pinned `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe` as supplied; the checkout has no `.git`, so the pin was taken as given and not verified locally). GameReComp at `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/integration`. No repository files were edited.

**Bottom line:** the two projects solve opposite problems with opposite strategies. Stela is an **ARM64 guest → x86-64 host** AOT/JIT translator that emits machine code and therefore must *do its own physical register allocation*, using three distinct schemes (persistent pinning, a liveness-based per-region allocator, and an LRU vector cache). GameReComp is an **x86-32 guest → C source** static lifter: it models guest registers as plain C globals (`g_eax`, `g_ecx`, …) and **delegates all host register allocation to the C compiler**, with an opt-in shadow-local mode. The concepts are transferable; the code is not (licence and direction mismatch).

---

## 1. Stela — register model

### 1.1 Guest state / frame (the fallback home)

- `crates/stela-compiler/src/state.rs:81` — `pub struct GuestState`: `x: [u64;31]`, `sp: u64`, `nzcv: u32`, `v: [[u64;2];32]`, FPCR/FPSR, exclusive-load fields, `daif`, `pending`, `unreachable`, `pan`, `anchors`, `lower_limit`, `loop_retired`, `exit_rsp`, `account`. `#[repr(C)]`.
- `crates/stela-compiler/src/x86_64.rs:238` — `fn offset(register: Register) -> Option<u32>`: guest `Xn` → `n*8`, `Sp` → 248, `Zero` → `None`.
- `crates/stela-compiler/src/x86_64.rs:247` — `fn frame(offset) -> Mem`: every frame access is `[RDI + disp32]`. **RDI is permanently the GuestState frame pointer** in emitted code; RSP is the host stack and is never used for guest state.
- `crates/stela-compiler/src/x86_64/asm.rs:108` — `Asm::guest(register) -> Option<Rm>`: the single funnel through which emitters read a guest register. Non-pinned code → `Mem::base_disp32(Rdi, slot)`; pinned code → the pinned/local host register, else a frame slot.

### 1.2 Tier A — persistent pinning across blocks (`x86_64/pinned.rs`)

The headline register map, `crates/stela-compiler/src/x86_64/pinned.rs:38`:

```rust
pub(crate) const PINNED: [(Register, Gpr); 6] = [
    (Register::Sp,   Gpr::Rbx),
    (Register::X(8), Gpr::Rsi),
    (Register::X(19),Gpr::R14),
    (Register::X(0), Gpr::R15),
    (Register::X(1), Gpr::Rbp),
    (Register::X(16),Gpr::R12),
];
```

- Rationale (module header, lines 1–19): a load+store around nearly every guest instruction is ~7 bytes each and bloats the icache; hot guest registers stay in host registers **across blocks, including chained ones**. `GuestState` holds them only outside native code: engine entry loads, return stores, and fault settlement recovers from the host capture. No block spills/fills them → nothing to reconcile at a chain.
- `pinned.rs:16-19`: host registers deliberately **not** taken as emitter scratch: `RAX, RCX, RDX, R8–R11` stay free. An emitter needing a pinned host goes through the frame via `frame_bound`.
- `pinned.rs:47-49`: `ACCOUNT_HOME` = `offset_of!(GuestState, account)`; the chain's account is pinned to **R13** in `x86_64/chain.rs:273` (`pub(crate) const ACCOUNT: Gpr = Gpr::R13;`), freed by X17 giving up its pin (X17 costs +0.04 host instructions/guest instruction when unpinned; X1 +0.18).
- `pinned.rs:62-72`: `LOCAL_HOSTS = [R8, R9, R10, R11]`, `type LocalRegisters = [Option<Register>;4]`, `host_or_local()` — short-lived extension for pure spans (Tier B below).
- Key functions/types: `host()` (52) host-reg lookup; `settle(capture, state)` (88) fault recovery of pinned regs from `HostGprSnapshot`; `settled_account()` (101); `frame_bound()` (109); `working()` (120), `take()` (125), `finish()`/`finish_with()` (139/143), `right()` (161) — the emitter contract that keeps scratch in RAX/RCX. `pinned.rs` also carries `emit_live_carry`, `unshifted_binary`, etc. (lines 181–1718).
- `pinned/span.rs`: `LocalRegisters`, `Dirty = [Option<(u8,Gpr)>;4]`, `Effects` (read/write masks over 31 GPRs + flags, `takes_r8` flag), `Span`/`Run`, `write_back()`, `reload()`, `plan()`/`plan_loop()`. A run of instructions loads locals it reads at entry, stores written ones at exit **and at every conditional branch before a side exit**; the frame slot is stale inside the run and faults recover locals from their hosts. Local-register assignment is limited to R8–R11 and only admitted for emitters that confine scratch to RAX/RCX/RDX.
- `x86_64/asm/code.rs:15-20, 214-238` — `Code` carries `pinned: bool`, `locals`, `dirty`, `vectors`; `Code::host()`, `set_pinned()`, `set_local_registers()`, `set_dirty_locals()`. `x86_64/block.rs:479-541, 691-928, 1449-1473` decides per block/spans whether to run pinned, and temporarily drops pinning (`set_pinned(false)` + `write_back`) around operations that need more host registers, then `reload` + `set_pinned(true)`.

### 1.3 Tier B — liveness-based region allocator (`x86_64/region/*`)

Used for non-pinned blocks (`block.rs:557`: `optimize_regions && !pinned …`) — mutually exclusive with Tier A per block.

- `region/encoding.rs:7-23` — `enum HostGpr` (Rax=0, Rcx, Rdx, Rbx, Rbp, Rsi, Rdi, R8…R15; **no Rsp**). Lines 50–70:
  - `RESULT = Rax`, `RIGHT = Rdx`, `FRAME = Rdi`.
  - `POOL = [Rcx, Rsi, R8, R9, R10, R11]` (caller-saved).
  - `CALLEE_POOL = [Rbx, Rbp, R12, R13, R14, R15]` — borrowed only within a non-faulting span, with native-ABI save/restore.
  - `ALLOCATABLE_SLOTS = 12`; `pool_host(index)`; `HostGpr::mask()`.
- `region/location.rs:4-39` — `GPR_SLOTS = 32`; `enum Location { Frame{byte_offset}, Host(HostGpr) }`; `FRAME_LOCATIONS` gives every guest GPR its own 8-byte frame slot; `valid()` enforces distinct pool hosts.
- `region/allocation.rs` — the actual allocator:
  - `ValueMask = u64`; `NZCV_SLOT = GPR_SLOTS`; flags are modelled as just another value (line 11, 20).
  - `ValueEffects { reads, writes }`, `LiveRanges::compute()` backward liveness (lines 58–101), `next_read()` with cyclic/loop handling (106–132), `Plan { effects, live, callee_slots, cyclic }` (134–183).
  - `Emitter::value_index`, `forget_value` (193+) — `_resident: [Option<Resident>; 12]`; `Resident { guest, dirty }` lives in `region.rs:99-103`.
  - `CANONICAL_EXIT = (1 << (GPR_SLOTS+1)) - 1` (line 14): all values live at exit, so every dirty final value is stored back at region boundaries; NZCV pressure stores/coalescing are tracked in `AllocationStats` (`region.rs:60-87`).
- `region.rs:105-133` — `Emitter` also tracks `reserved_hosts`, `pinned_hosts`, `flag_location`, `fault_table`, `callee_saves/restores` stats — i.e. a real, if small, linear-scan-style allocator with spill-to-frame and callee-save cost decisions.
- `region/analysis.rs` provides `Effects`, `next_use`, `prefix_effects`, `slot`; `region/self_loop.rs` handles resident loops with `ResidentLoopPlan`, `FrameLoop`, `prefix_with_budget`; `region/flags.rs` tracks NZCV location; `region/constant.rs` tracks constant registers.

### 1.4 Tier C — vector register cache (`x86_64/vector_cache.rs`)

- Lines 1–35: guest V registers live in **XMM8–XMM15** (8 homes) within a block; recipes keep fixed scratch **XMM0–XMM7**. Register 8–15 chosen because legacy SSE encodings can address them (XMM16–31 are EVEX-only).
- `FIRST_HOST = 8`, `HOSTS = 8`, `FIRST_DERIVED = 16`, `DERIVED_HOSTS = 16` (39–56). On AVX-512VL hosts XMM16–31 form a second "derived" bank for values reused later in a block (`Code::derived`), never stored and never needed by fault/exit recovery.
- `struct VectorCache` (67–95): `home[32]`, `owner[8]`, `dirty: u32`, `used[8]` + `clock` (**LRU spill**), `write_through`, `writing`, `derived[16]`, `derived_used[16]`, `deriving`, `fresh`.
- `dirty_homes()`/`DirtyHomes = [Option<(u8,u8)>;8]` (59–62, 131): fault sites record which guest V register only exists in which XMM8–15; `crate::block::RecoveryBinding::vector` recovers from the host's saved XMM image. `VectorCache::flush` writes the state block at side exits, terminals, unknown instructions; exits/helpers with dirty homes are refused (`Asm::refuse_dirty_homes`).
- `x86_64/region.rs` / `simd/*` also use fixed vector scratch assignments, e.g. `simd/arithmetic.rs:98` "classifier scratch XMM2/3/4/5 must not alias it".

### 1.5 Stela metadata

- **Licence:** project GPL-2.0-or-later (`Cargo.toml:65`, `README.md:102`, `LICENSE` = GPL v2, `NOTICE`). The register-relevant crate `stela-compiler` is declared **`GPL-2.0-or-later AND MIT`** (`crates/stela-compiler/Cargo.toml:5`); the `AND MIT` is for the AMX code ported from Peter Cawley (`src/amx.rs:6`, MIT notice in `LICENSES/MIT.txt`). The pinned/region/vector files carry **no per-file SPDX header** — they fall under the crate's GPL-2.0-or-later side. Built binaries have mixed terms (`NOTICE`: `stela.efi` GPL-2.0-only due to Linux; CLI GPL-3.0-or-later due to Apache-2.0 crates).
- **Dependencies for this subsystem:** Rust 2024 edition / rustc ≥1.99 (`Cargo.toml:64,67`), `serde`, `rustc_apfloat =0.2.3` (`stela-compiler/Cargo.toml:19-24`), `alloc`; x86-64 encoder is hand-written (`x86_64/asm.rs`, `asm/operands.rs` with Gpr Rax..R31 incl. APX extended bank, `asm/sse.rs`, `asm/vector_extensions.rs`). Tests need clang/LLVM for AArch64 fixtures (`xtask`). No external regalloc crate (no `regalloc2` in `Cargo.toml`).
- **Portability:** host x86-64 **only** (SSE2 baseline for XMM8–15; AVX-512VL optional for the derived bank; APX extended GPRs in the encoder). Emits native code with fixed host-register roles (`RDI` frame, `RSP` host stack, `R13` account) and a fixed native ABI assumption. No arm64-host path. The project as a whole is a UEFI bootloader + Linux/Mesa/Metal tree, but the compiler crate itself is `no_std`-capable (`default = ["std"]`, `alloc`-based).

---

## 2. GameReComp — the `g_eax`/`g_ecx` global model

### 2.1 Register file (`runtime/recomp32/recomp_types.h`)

- Lines 35–51: "x86 registers are global variables"; volatile `extern uint32_t g_eax, g_ecx, g_edx, g_esp;`, callee-saved `extern uint32_t g_ebx, g_esi, g_edi, g_ebp;`. `ebp` is global (not per-function) because MSVC `__EH_prolog` writes the **caller's** frame pointer and FPO functions pass it through. The comment at lift32.py:230-238 records the hard-won reason.
- Lines 53–59: x87 stack is global `g_st[8]`, `g_fp_top`, `g_fpu_cw` (float returns and control word cross calls).
- Lines 74–76: weak globals `g_flag_k, g_flag_a, g_flag_b, g_flag_cf` + `RECOMP_FLAGS_OUT()/IN()` — flags returned across calls.
- Lines 79, 204–205, 721, 927–928: `g_seg_*` (flat-mode), `g_fs_base/g_gs_base` (`FS_BASE`/`GS_BASE` for TIB/TEB), `g_mm[8]` (MMX), `g_xmm[8]` + `g_mxcsr` (SSE) — all global. `g_st_i64[8]` weak shadow for exact `fild/fistp qword` (999).
- Lines 98–132: two macro modes:
  - **default:** `#define eax g_eax … ebp g_ebp` inside `RECOMP_GENERATED_CODE`.
  - **opt-in `RECOMP_LOCAL_REGS`:** `l_eax … l_ebp` locals, `RECOMP_REGS_LOCALS` initialiser, `RECOMP_REGS_OUT()` / `RECOMP_REGS_IN()` write-back/reload. Comment (99–105): globals alias any guest store so the optimiser had to reload after every `MEM32`; locals whose address is never taken stay in host registers. **This is the only place host-register residency is even hinted at — the C compiler chooses the actual host registers.**
- Lines 155–161: sub-register macros `LO8/HI8/LO16`, `SET_LO8/SET_HI8/SET_LO16`.
- Lines 1182–1190, 1257–1293, 1296–1309: `RECOMP_CALL`, `RECOMP_ICALL`, `RECOMP_ITAIL` all bracket native/lifted calls with `RECOMP_FLAGS_OUT(); RECOMP_REGS_OUT(); func(); RECOMP_REGS_IN();`. `RECOMP_ENTER` (1332–1335) pulls flags back in; `RECOMP_EH_ENTER` (1357–1364) reloads regs on a guest longjmp; `RECOMP_BACKEDGE` (1379–1383) round-trips regs at thread-yield points. `CPUID` macro (1130) does the same.
- Lines 177–199: address translation (`ADDR`) is also register-pressure-sensitive — `RECOMP_FLAT_MEMORY` / `RECOMP_MEM_BASE` exist specifically so a global base does not have to be reloaded after every store.

### 2.2 Lifter side (`tools/lift/lift32.py`)

- Lines 84–103: `REG_NAMES_32 / _16 / _8L / _8H` map Capstone register IDs → plain C names (`eax`, `ax`→`eax`, `al`→`eax`, etc.); `ALL_REG_IDS`.
- `reg_name()` (171–192) adds `_st[i]`, `_seg_*`; `_fmt_read` (346–373) emits `LO16/LO8/HI8` for narrow reads, `_fmt_write` (375–398) emits `SET_LO16/SET_LO8/SET_HI8`. `_fmt_mem_addr` (400–439) routes FS/GS/ES overrides to segment-base globals.
- `Lifter` class (265) has **no register-allocation state**: `_flag_state`, `_flag_seq`, `_fp_depth`, etc. are flag/FPU bookkeeping only. `lift_instruction()`/`_lift_instruction()` (757–1048+) emit straight-line C statements into `eax`/`g_eax`. PUSHAD/POPAD (905–909) become whole-register-file macros in the header (328–338).
- `MMX_REGS`/`MMX_BINOPS` etc. (32–81) and `lift32_sse.py` emit `_mm[n]`/`_xmm[n]`, resolved to globals by header aliases (135–142).
- `FUNCTION_LOCALS` (239–246) lists only flag/FPU scratch locals; the header comment explicitly keeps `ebp` out because it is architectural state, and keeps x87 out because it is shared.

### 2.3 Generation boundaries (`tools/lift/generate.py`)

- `generate_function()` (approx. 273–422): emits `RECOMP_REGS_LOCALS` + `RECOMP_ENTER` preamble (277–278), clears static flag pairing at join points (309–315), emits `RECOMP_BACKEDGE()` on loops (361–362), and appends `RECOMP_REGS_OUT()` at every exit (415–420) using a regex that wraps each `return;`. The comment at 293–297 is the direct analogue of Stela's "no spills at chains": labelling every instruction as a join point prevents the C compiler keeping anything in registers.

### 2.4 GameReComp metadata

- **Licence:** **AGPL-3.0-only** (`LICENSE` = GNU Affero GPL v3; `NOTICE`: "Everything else … AGPL-3.0-only"; prior MIT history noted in `LICENSES/GameReComp-prior-MIT.txt`; two third-party MIT/zlib files listed in NOTICE).
- **Dependencies:** Python 3 + `capstone` (`lift32.py` imports `capstone.x86`), `lift32_sse.py`, `generate.py`, `run_lift.py`; generated C compiled by MSVC and gcc/clang; C runtime `runtime/recomp32/*.c`. Optional wasm32 path in header.
- **Portability:** high — generated C + header compile on x86-64 and arm64 hosts, Linux/macOS, MSVC and GCC/Clang; `__wasm32__` page-directory translation (181–190); non-x86 `__rdtsc()` fallback using `clock_gettime` (24–33). Host register allocation is entirely the compiler's job, so it is automatically host-architecture-agnostic.

---

## 3. Direct comparison

| Axis | Stela | GameReComp |
|---|---|---|
| Direction | ARM64 guest → x86-64 host machine code | x86-32 guest → C source (compiled ahead of time) |
| Guest register home | `GuestState` frame (`[RDI+disp]`) + host registers | C globals `g_eax…g_ebp` / macro aliases |
| Who allocates host registers | The translator itself (fixed pins + region allocator + LRU vector cache) | The C compiler; opt-in `RECOMP_LOCAL_REGS` shadows |
| Fixed assignments | SP→RBX, X8→RSI, X19→R14, X0→R15, X1→RBP, X16→R12; account→R13; frame→RDI; free scratch RAX/RCX/RDX/R8–R11; vector homes XMM8–15 | none at source level; `eax` may alias `g_eax` or `l_eax`; C compiler picks host regs |
| Cross-block residency | yes — pinned regs stay in host regs across blocks and chains | no explicit model; locals mode stays resident only within one lifted function, written back at every call/return |
| Liveness / spills | explicit (`LiveRanges`, `ValueMask`, `next_read`, LRU `used[]/clock`, spill to 8-byte frame slots) | implicit; C compiler's register allocator |
| Flags | guest NZCV modelled as a value (`NZCV_SLOT`), deferred host-capture (`defer_nzcv`), `flag_word.rs` | lazy tuple `_flag_k/_flag_a/_flag_b` + `_cf`, static setter→consumer pattern matching, weak globals across calls |
| Fault/exception interaction | every fault site records dirty host regs (`EmissionSite.locals`, `DirtyHomes`, `HostGprSnapshot`) and settlement recovers them | no faults; equivalent boundaries are calls/tail calls/longjmp/yield with `RECOMP_REGS_OUT/IN` |
| SIMD registers | 8 XMM homes + 16 AVX-512 derived bank; LRU, dirty homes, refused dirty exits | global `g_mm[8]`, `g_xmm[8]`, `g_st[8]`, no allocation |
| Host portability | x86-64 only (SSE2; optional AVX-512VL/APX) | x86-64 / arm64 / wasm32, MSVC or GCC/Clang |
| Licence of relevant code | GPL-2.0-or-later (crate dual `AND MIT` only for AMX) | AGPL-3.0-only |

---

## 4. Concrete adaptation candidates

These are **idea-level** adaptations (deliberately not verbatim ports — see licence note in §5).

1. **Pin a small hot set of guest registers to named C locals.** Stela's data point is that 6 pins (SP, X0, X1, X8, X16, X19) materially cut icache traffic, and that pinning the hottest-but-one (X1) was actually a regression (+0.18 instr/guest). GameReComp's `RECOMP_LOCAL_REGS` already gives all eight registers local shadows, but the compiler may spill them unpredictably. A targeted experiment: in `recomp_types.h` add a `RECOMP_PINNED_REGS` variant that keeps only `eax`, `ecx`, `esp` as named locals (or compiler-specific `register` / explicit intrinsics where supported) and leaves the rest as globals, with the existing `RECOMP_REGS_OUT/IN` at the macro boundaries (already emitted by `generate.py:415-420` and `recomp_types.h:1182-1309`). Reuse Stela's *measurement method* (host instructions per guest instruction on hot blocks) to choose the pin set rather than guessing.

2. **Adopt the "dirty on boundary" discipline explicitly.** Stela's `Dirty`/`DirtyHomes` tracking means a value living only in a host register is recorded at *every* exit/branch/fault site. GameReComp's equivalent is already the `RECOMP_REGS_OUT/IN` bracket, but it is all-or-nothing. A finer-grained variant would track which of the eight registers changed since entry and write back only those — directly analogous to `pinned::Dirty` — yielding less traffic at the many call/tail/longjmp/yield sites without losing correctness.

3. **Borrow the liveness formulation for a local-regs mode.** `region/allocation.rs` models NZCV as just another bit in `ValueMask` and computes `LiveRanges::compute()` backwards with `CANONICAL_EXIT` (everything live at exit). This maps cleanly onto a lifted basic block: treat the eight guest GPRs + the flag tuple as values, compute per-block liveness at generation time (Python), and emit `RECOMP_REGS_OUT` only for values live at the exit. The current generator emits it unconditionally at every return; a liveness-aware version is a mechanical change in `generate_function()` and would be especially useful for the "labelled every instruction" case (`generate.py:293-297`).

4. **Vector/SIMD caching idea.** Stela keeps V registers in XMM8–15 with LRU + dirty homes and refuses helpers/exits with dirty homes. GameReComp's `g_xmm[8]`/`g_mm[8]` are always globals. For hosts where the C compiler proves a small set of `_xmm[n]` never escapes, the analogous optimisation is to scope XMM/MMX state as locals per lifted function with the same write-back macros — but only after checking that cross-call SSE state (MXCSR, vtable calls passing values in XMM on non-Windows ABIs) is genuinely dead; Stela's "refuse dirty exits" rule is the safety property to copy.

5. **What *not* to copy:** Stela's physical register map (RDI frame, R13 account, specific XMM banks) is a consequence of emitting x86-64 machine code under a fixed ABI and would be wrong in generated C. Its region allocator (`region/*`) and hand-written encoder have no direct role in a C-lifter; only their *policies* (pin-hot-set, liveness, dirty-tracking, measurement) are transferable.

---

## 5. Licence / compliance note for any adaptation

- GameReComp is **AGPL-3.0-only**. Stela's register code files (`pinned.rs`, `pinned/span.rs`, `region/*.rs`, `vector_cache.rs`) are under the crate's **GPL-2.0-or-later** side (no per-file MIT notice; the MIT part of `GPL-2.0-or-later AND MIT` covers the AMX port only). Copying those files or close paraphrases of their implementation into GameReComp would create a licence conflict that needs legal review. Reimplementing the *ideas* (pin sets, dirty masks, LRU, liveness formulas) is the safe path; if any Stela source is to be reused verbatim, only files explicitly covered by `LICENSES/MIT.txt` (e.g. `src/amx.rs`) are candidates, and they are unrelated to registers.
- Both projects carry third-party notices that must be preserved if any code is moved (`stela/NOTICE`, `gamerecomp/NOTICE`).
