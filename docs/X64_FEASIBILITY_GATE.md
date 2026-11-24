# X64 feasibility gate (Issue #13)

Status: **DEFERRED — gate not open.** This document defines the measurements
that would have to exist before any PE32+/Win64 implementation work is allowed.
It is not a GO decision, it adds no x64 loader, lifter, ABI or backend, and it
does not claim that x64 games run, build, or are supported. Nothing in this file
changes the fail-closed behaviour of `tools/autorecomp`.

Date of evidence: 2026-11-23. Base commit:
`d19c82a226138f9d36a69346ff5cbb8af5cab6a8`. Worktree branch:
`feature/swarm3-b9-x64-gate`.

## 1. Where the boundary comes from

Two independent boundaries constrain Issue #13; both must be satisfied.

**Master-task boundary (Issue #1 lane).** `docs/ACCEPTANCE_LEDGER.md` records
the upstream title of #13 verbatim:

> `[P3] Implement PE32+ Win64 game pipeline and native host ABI only after
> Omill-vs-PCRecomp feasibility gate`

and its gate state:

> `A0 + roadmap gate: deferred until x86 PE32 macOS and Linux acceptance
> passes; PE32+ games NOT RUN`

**Roadmap boundary.** `docs/ISSUE_EXECUTION_PLAN.md` line 25 lists #13 as
`DEFERRED`, line 25's evidence as `roadmap prerequisite not met; no production
x64 work`, and integration checkpoint 4 (line 53) as:

> `#8 and #13 remain DEFERRED until x86 PE32 macOS and Linux acceptance passes.`

`docs/CAPABILITY_BOUNDARIES.md` lines 18–21 and `docs/MULTIARCH_RUN_ATTEMPT.md`
lines 5–26 independently confirm that the refusal is intentional and that the
PE32+ game binaries found on the earlier host were **NOT RUN / NOT TESTED** —
not PASS and not FAIL.

Therefore GO requires **both** (a) the x86 PE32 macOS **and** Linux acceptance
evidence named in the roadmap, and (b) every measurable criterion in §4 below.
Absence of either is NO-GO. This document only addresses (b).

## 2. Measured refusal — exact command and output

No x64 game binary is committed to this repository (the only checked-in PE is
`tests/fixtures/pe32-minimal.exe`, a PE32 image). To obtain a live refusal from
this exact base commit, a minimal PE32+ probe was constructed in the approved
temp directory from a valid DOS/PE layout (single `.text` section, one `ret`).
Its header was verified before the CLI was invoked:

```
$ PROBE=/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/pe64-probe.exe
$ python3 - "$PROBE" <<'PY'   # emits a 1536-byte PE32+ image
... DOS MZ, e_lfanew 0x80, PE\0\0, Machine=0x8664, sections=1,
    SizeOfOptionalHeader=0xf0, OptionalMagic=0x20b, ImageBase=0x140000000 ...
PY
size 1536 e_lfanew 0x80 sig b'PE\x00\x00'
Machine 0x8664
NumberOfSections 1
SizeOfOptionalHeader 0xf0
OptionalMagic 0x20b
```

Run A — architecture auto-detected from the header, from the worktree root:

```
$ python3 -m tools.autorecomp "$PROBE" --input-os windows --input-arch auto \
    --target-os host --target-arch host --out-mode source-only \
    --no-table --no-progress
error: unsupported input architecture: x86_64 (this pipeline reconstructs PE32 x86 guests; a 64-bit build of the same game, if one exists, would be a different binary to reconstruct -- 64-bit guest support is planned, not implemented)
EXIT=4
```

Run B — forcing the 32-bit lifter on a PE32+ header:

```
$ python3 -m tools.autorecomp "$PROBE" --input-os windows --input-arch x86 \
    --target-os host --target-arch host --out-mode source-only \
    --no-table --no-progress
error: --input-arch x86 but the binary's header says x86_64; refusing to reconstruct on a guess
EXIT=4
```

The same two refusals were recorded earlier against **real** x64 game binaries
(`opentyrian.exe`, 930,434 B; `cannonball.exe`, 534,528 B;
`pixeltetris/Pixeltetris.exe`, 1,118,208 B; `snake/snake-windows-x86_64.exe`) in
`docs/MULTIARCH_RUN_ATTEMPT.md` lines 8–20. Those files are not present in this
worktree; their refusal text is quoted here as historical evidence only and was
not re-run in this session. The live evidence is Run A/B above. The producing
code is `tools/autorecomp/cli.py` lines 134–145 (`_resolve_input`), which keys
the refusal on `Machine == 0x8664 -> x86_64`, and `src/pe.rs` lines 48–59, which
already labels `PE32+` / `x86-64` correctly. Detection is not the missing piece;
everything downstream of detection is.

## 3. Upstream evidence (what each candidate actually offers)

### PCRecomp — reachable locally, inspected, not executed here

```
$ git -C /Users/haseeb-mir/Documents/Code/pcrecomp rev-parse HEAD
a85ea970f9e0640ab6546d2ca7a01fccb132973d
$ head -1 .../pcrecomp/LICENSE
MIT License
```

At that pinned HEAD, PCRecomp ships an end-to-end **x86-64** path (line counts
from `wc -l`):

| File | Lines | Role |
|---|---:|---|
| `tools/lift/lift64_cpu.py` | 1812 | x86-64 -> C, 16 GPR + 16 XMM, zero-extending 32-bit writes, RIP-relative operands, SSE2; x87/MMX emit `RECOMP_TODO` |
| `tools/lift/generate64.py` | 688 | whole-PE x64 driver: chunked TUs, dispatch table, import map, catalog closure |
| `tools/lift/difftest64.py` | 652 | lifted C vs **host CPU** running original bytes; registers, memory, `lock` atomics |
| `tools/disasm/disasm64.py` | 562 | `.pdata`/Guard-CF/exports/relocs/SEH+funclet function recovery |
| `runtime/recomp64_cpu/cpu64.h` | 599 | 64-bit guest register/CPU contract |
| `runtime/recomp64_cpu/eh64.c` | 398 | guest C++ EH via `.pdata`/`.xdata` + `__CxxFrameHandler3` |

Its README states the 64-bit lifter came from `systemes3recomp`, where it
handled a shipping UE3 build (3.6M instructions, 92,552 functions, 0 lift
errors), and that `difftest64` needs **MSVC x64 (`ml64` + `cl`)**. It also states
that a Win64 guest on a **Win64 host** shares the calling convention, so the
loader puts real function addresses in IAT slots — i.e. PCRecomp's Win64 story
is Win64-host forwarding, **not** the Win32HLE self-answering model used by this
repository's `runtime/win32hle`. This repository already vendors the same
`runtime/recomp64_cpu/{cpu64.h,eh64.h,eh64.c}` scaffolding and
`tools/disasm/disasm64.py`; none is reachable from the `autorecomp` CLI
(`docs/BACKENDS.md` lines 24–30).

### Omill — reachable by README fetch; not built or run

`https://github.com/trailofbits/omill` returned:
`404 GET https://github.com/trailofbits/omill`. The project is
`https://github.com/binsnake/omill` (repository metadata: MIT, created
2026-02-13, 166 commits). Its README was fetched from
`https://raw.githubusercontent.com/binsnake/omill/main/README.md` and says,
verbatim:

> An LLVM pass library for lowering remill-lifted IR to recompilable native
> code. Targets Windows x64 (PE) binaries using the Win64 ABI.

> **Platform Support:** Currently only supports **Windows x64 (PE)** files
> using the Win64 ABI.

> **Not Recommended For:** Full binary lifting or recompilation (focuses on
> specific functions and their reachable targets).

Other stated facts: requires LLVM 21 and C++17/Clang 21; `omill-lift` starts
lifting from an explicit `--va` and depends on remill for e2e tests; 266 unit
tests, e2e tests require a remill build; passes cover intrinsic lowering, State
SSA promotion, CFG recovery, IAT/dispatch resolution and Win64 calling-
convention/stack-frame/signature recovery; no SysV ABI path is described.
None of this was compiled or executed for this gate — it is upstream
documentation, not a measurement on this host.

### Consequence for the gate

The comparison is not "x64 is impossible". It is: PCRecomp has a whole-image
x64 lifter plus host-CPU differential testing but a Win64-host import-forwarding
assumption; Omill has Win64-ABI/IR recovery and deobfuscation focused on single
functions, with an explicit no-full-binary-recompilation limitation. Neither
provides, on its own, this product's required combination of whole-PE lifting
**and** self-answered Win32HLE imports **and** non-Windows host execution. The
criteria below are written so that choice is decided by evidence rather than by
preference.

## 4. Measurable go/no-go criteria

All criteria are MANDATORY unless marked "informational". A criterion is PASS
only when the named artifact exists in the repository and the threshold is met.
"Not measured", "builds", and "looks right" are FAIL. No criterion may be
satisfied by quoting an upstream README alone.

### G1 — 64-bit PE loader

- **Measure:** a loader accepts a PE32+ image and exposes 64-bit fields:
  `Machine=0x8664`, optional magic `0x20b`, `ImageBase` (64-bit), `.pdata` /
  `.xdata`, `DIR64` relocations, Guard-CF table, export/import directories,
  TLS directory, and load config. It must reject malformed/overflowing RVAs
  fail-closed.
- **Pass threshold:** the existing `src/pe.rs` 64-bit labels are consumed by a
  loader that maps the probe from §2 and at least three real PE32+ titles with
  zero out-of-bounds mappings; every rejected input produces a non-zero exit
  and an explicit reason. `pe32` behaviour is unchanged (regression suite green).
- **Fail condition:** any silent fallthrough to the 32-bit loader, any guessed
  base address, any unchecked `va + size` wrap in 64-bit space.
- **Evidence:** loader unit tests + a loader report JSON per image, committed
  under `reports/`.

### G2 — x86-64 semantics

- **Measure:** instruction semantics for the instruction classes the corpus
  actually contains, checked against a reference that is not the lifter itself.
- **Pass threshold:** for every instruction class present in the corpus,
  `difftest64`-style host-CPU differential tests compare **all** GPRs, XMM
  registers, flags, affected memory bytes and `lock` atomics; zero mismatches on
  a corpus sample of at least 2,000 instructions per title plus hand-picked
  edge cases. x87/MMX occurrences are either implemented or enumerated as
  `RECOMP_TODO` with a count; no silent miscompile.
- **Fail condition:** any unenumerated `RECOMP_TODO` reached at runtime, or any
  semantic mismatch hidden by disabling the host-CPU comparison.
- **Evidence:** differential-test command, seed/corpus hashes, mismatch count
  (must be 0), and the `RECOMP_TODO` inventory. Upstream PCRecomp `difftest64`
  is the reference design, subject to G4's toolchain constraint (MSVC x64 is
  not available on this host).

### G3 — SysV / Win64 ABI split

- **Measure:** an explicit, tested split between Win64 (Microsoft x64:
  `rcx/rdx/r8/r9`, shadow space, `xmm0-3`, guest SEH/unwind) and SysV AMD64
  (argument registers, red zone, callee-saved set) at the lifter/call boundary.
- **Pass threshold:** a guest call is emitted against a declared ABI enum, not
  inferred from the host; at least two host ABIs are exercised (one Win64
  guest -> Win64 host, one Win64 guest -> POSIX/SysV host via HLE), with
  register-argument, stack-argument and floating-point-return cases covered by
  differential tests. Guest SEH unwinding (`eh64.c`) is exercised on a throwing
  guest function; the catch runs and the process does not abort.
- **Fail condition:** "guest Win64 == host Win64" used as a universal
  assumption (PCRecomp's IAT-forwarding shortcut), or a single undifferentiated
  `abi` flag in generated C.
- **Evidence:** ABI matrix test results, the generated call-site snippets, and
  a guest-exception test log.

### G4 — Host backends

- **Measure:** the generated project compiles and links on a declared host
  using a pinned toolchain, and its runtime answers imports without Wine,
  qemu or the original EXE.
- **Pass threshold:** at least one *non-Windows* backend reaches a runnable
  binary (the repository's existing `runtime/win32hle` model adapted to 64-bit
  guests, or an equivalent self-answered 64-bit HLE) with zero unresolved
  imports at startup on the acceptance corpus; the build is reproducible from
  the generated CMake project with pinned compiler/SDK versions recorded in the
  build log. A Windows/Win64-forwarding backend, if added, is measured
  separately and is not counted as satisfying non-Windows execution. Omill's
  LLVM 21/remill/MSVC-x64 requirements must be shown to build here, or the
  candidate is scored NO-GO for this criterion.
- **Fail condition:** relying on IAT slots filled with host function addresses
  on a non-Windows host; any runtime that shells out to Wine/emulator to answer
  guest code.
- **Evidence:** pinned build logs, compiler/SDK versions, unresolved-import
  counts, and an `ldd`/dependency listing proving no Wine/emulator path.

### G5 — Test corpus

- **Measure:** a fixed, hashed corpus covering PE32+ shapes rather than one
  synthetic probe.
- **Pass threshold:** at least three real PE32+ game or system images, each with
  SHA-256 recorded, spanning (a) a GUI game, (b) a C++ exception/SEH user,
  (c) a DLL/import-heavy image; plus the minimal PE32+ probe from §2 as a
  smoke test. The corpus includes the four locally found x64 titles named in
  `docs/MULTIARCH_RUN_ATTEMPT.md` if they are supplied again. Every image has
  a function catalog scored for precision/recall against a symbol source
  (`tools/disasm/disasm64.py` + `tools/pe/pdb_ref.py` design), with thresholds
  set per image before the run, not after.
- **Fail condition:** a corpus of one; scores reported without a reference;
  counting PE32+ games as "tested" when they were only inspected or refused.
- **Evidence:** corpus manifest with hashes and sizes, per-image catalog scores
  and reference provenance.

### G6 — Acceptance evidence

- **Measure:** end-to-end evidence at the same bar the x86 lane uses, not a
  build-only claim.
- **Pass threshold:** for at least one corpus title: full A0–A10 evidence per
  `docs/ACCEPTANCE_LEDGER.md`, a real `report.json`, process exit/stdout
  comparison, frame/input/audio/save-load comparison against the original
  Windows build (original-Windows oracle required), and a preserved failure
  list. Any title that fails after build is recorded FAIL with its counterexample;
  no title is counted PASS without those artifacts. Existing PE32 regression
  results (`python3 -m pytest tests/ -q`; `verify.sh --fast`) must remain green.
- **Fail condition:** "native build succeeded" presented as equivalence; a
  PASS row without an oracle; absence of a host resource plan for the observed
  SIGKILL under large builds (issue #26).
- **Evidence:** committed report summaries and hashes in `docs/`, with raw
  artifacts in gitignored `test-results/`, per integration checkpoint 3.

## 5. Decision rule

| State | Condition |
|---|---|
| **GO** | Roadmap prerequisite (x86 PE32 macOS **and** Linux acceptance) is evidenced, **and** G1–G6 all PASS with committed artifacts. Only then may an implementation branch for #13 be opened. |
| **NO-GO (current state)** | Any of G1–G6 is unmet or unmeasured, **or** the roadmap prerequisite is unmet. Today: G1–G6 unmeasured; x86 PE32 macOS/Linux acceptance not met; the CLI still refuses PE32+ (§2). Issue #13 stays DEFERRED. |
| **REVIEW** | All gates PASS except one; the missing gate and its owner are recorded, and no x64 code is merged before it closes. |

An informational, non-gating observation for the coordinator: PCRecomp's x64
tooling is already vendored in this repository but unreachable from the CLI; if
G1–G6 are later met, adopting it may be cheaper than a new lifter. That is a
hypothesis for a future gate run, not a recommendation to start work now.

## 6. Verification performed for this document

Commands run from the worktree at base `d19c82a226138f9d36a69346ff5cbb8af5cab6a8`:

```
$ git -C /Users/haseeb-mir/Documents/Code/GameReComp worktree add -b \
    feature/swarm3-b9-x64-gate \
    /private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/swarm3/b9 \
    d19c82a226138f9d36a69346ff5cbb8af5cab6a8
Preparing worktree (new branch 'feature/swarm3-b9-x64-gate')
HEAD is now at d19c82a docs: record noiz2sa non-blank frames after present fix and final proof gallery
```

PE32+ probe header check and the two CLI refusals are quoted verbatim in §2.
Upstream fetch results are quoted in §3. No source, runtime, tool or test file
was modified; the only repository change is this new document.

## 7. Limitations of this gate document

1. No committed or locally present x64 game binary was available in this
   worktree, so the live refusal was produced with a synthesized PE32+ probe;
   its header is verified above, but it is not a game and proves only the
   detection/refusal boundary.
2. Omill was assessed from its upstream README and repository metadata only;
   it was not cloned, built or executed. LLVM 21/remill/MSVC-x64 availability on
   this host was not tested.
3. PCRecomp was inspected at pinned HEAD `a85ea970…` but its x64 tools were not
   run here; upstream claims (0 lift errors, 92,552 functions, etc.) are quoted
   from its README, not independently reproduced.
4. No Linux runner exists on this host (no Docker daemon, Colima image download
   stalled, no qemu-user/kernel), so the Linux half of the roadmap prerequisite
   cannot be evidenced here; large builds are also SIGKILLed under host memory
   pressure (issue #26).
5. No original-Windows oracle is available, so G6's behavioural comparison is
   specified but not achievable in this environment.
6. Thresholds in §4 are proposed decision criteria, not measurements. Meeting
   them requires a future, separately reviewed gate run with real artifacts.
