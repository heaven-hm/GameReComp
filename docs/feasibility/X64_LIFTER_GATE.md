# X64 lifter feasibility gate (Issue #148)

Status: **NO-GO / DEFERRED — this gate is open and unmet.** This document is a
*reproducible feasibility procedure*, not a support claim. It defines the exact
commands, licence checks, benchmark protocol and go/no-go checklist that must be
satisfied before any PE32+ / Win64 lifter, loader, ABI or backend work is allowed
for issues #13, #133, #134 and #148. It adds **no** x64 loader, lifter, runtime,
ABI or backend, and it does **not** claim that any x64 game builds, runs or is
supported. Nothing here changes the fail-closed behaviour of `tools/autorecomp`.

Date of evidence: 2026-11-24. Base branch: `feature/allissues-7-x64`. Repo:
`heaven-hm/GameReComp`. This file is intentionally owned by one worktree; no
source, runtime, tool or test other than `tests/test_x64_gate.py` is changed by
this gate package.

## 1. Scope and fail-closed refusal boundary

The CLI already *detects* x64 correctly; the missing capability is everything
downstream of detection. The producing code is `tools/autorecomp/cli.py`
(`_MACHINE_ARCH_NAMES[0x8664] -> "x86_64"`, `_resolve_input`) and the existing
64-bit labels in `src/pe.rs`. The two named refusals that this gate pins down are
verbatim:

- `unsupported input architecture: x86_64 (this pipeline reconstructs PE32 x86 guests; a 64-bit build of the same game, if one exists, would be a different binary to reconstruct -- 64-bit guest support is planned, not implemented)`
- `--input-arch x86 but the binary's header says x86_64; refusing to reconstruct on a guess`

A passing gate does not remove these refusals by itself. GO is only possible when
every checkbox in §5 is evidenced by committed artifacts **and** the roadmap
prerequisite (x86 PE32 macOS **and** Linux acceptance) is met. Either missing
means NO-GO. The exact commands that reproduce today's refusal are in §7.

## 2. Inspect local upstream checkouts (exact commands)

These commands are **read-only**. Run them from this repository root. They are
written so a missing checkout yields an explicit `NOT PRESENT` line rather than
a silent success. No upstream source is copied by this gate.

```sh
# 0. Resolve locations (override with env vars; the literal paths are the
#    observed developer-host defaults recorded in docs/UPSTREAM_REUSE_AGENT27.md
#    and docs/BINK_UPSTREAM_AUDIT.md -- they are inputs, not repo requirements).
PCRECOMP_DIR="${PCRECOMP_DIR:-/Users/haseeb-mir/Documents/Code/pcrecomp}"
OMILL_DIR="${OMILL_DIR:-/Users/haseeb-mir/Documents/Code/omill}"

# 1. PCRecomp (sp00nznet/pcrecomp) -- expected PRESENT, MIT.
if [ -d "$PCRECOMP_DIR/.git" ]; then
  echo "PCRECOMP: PRESENT"
  git -C "$PCRECOMP_DIR" rev-parse HEAD
  git -C "$PCRECOMP_DIR" status --short --branch | head -5
  head -3 "$PCRECOMP_DIR/LICENSE"
  # Whole-image x64 lifter + host-CPU differential reference, line counts:
  wc -l \
    "$PCRECOMP_DIR/tools/lift/lift64_cpu.py" \
    "$PCRECOMP_DIR/tools/lift/generate64.py" \
    "$PCRECOMP_DIR/tools/lift/difftest64.py" \
    "$PCRECOMP_DIR/tools/disasm/disasm64.py" \
    "$PCRECOMP_DIR/runtime/recomp64_cpu/cpu64.h" \
    "$PCRECOMP_DIR/runtime/recomp64_cpu/eh64.c" \
    "$PCRECOMP_DIR/runtime/recomp64_cpu/eh64.h"
else
  echo "PCRECOMP: NOT PRESENT (set PCRECOMP_DIR) -- criterion U1 cannot be measured"
fi

# 2. Omill (binsnake/omill, MIT) -- optional; expected NOT PRESENT on this host.
if [ -d "$OMILL_DIR/.git" ]; then
  echo "OMILL: PRESENT"
  git -C "$OMILL_DIR" rev-parse HEAD
  head -3 "$OMILL_DIR/LICENSE"
  grep -RniE 'Win64|PE32\+|LLVM 21|remill|full binary|not recommended' \
    "$OMILL_DIR/README.md" | head -30
else
  echo "OMILL: NOT PRESENT -- no local checkout; do not infer capability from README"
fi

# 3. What this repository already vendors (verify, do not assume):
git ls-files tools/disasm/disasm64.py runtime/recomp64_cpu
grep -n 'x86-64\|PE32+' src/pe.rs | head -20
```

Measured result on 2026-11-24 (this host, **inspected only — none of the x64
tools were executed**):

| Item | Result |
| --- | --- |
| PCRecomp checkout | PRESENT at `a85ea970f9e0640ab6546d2ca7a01fccb132973d`, clean |
| PCRecomp `LICENSE` | `MIT License`, `Copyright (c) 2026 sp00nz` |
| `lift64_cpu.py` | 1812 lines — x86-64 → C, 16 GPR + 16 XMM, RIP-relative, SSE2; x87/MMX emit `RECOMP_TODO` |
| `generate64.py` | 688 lines — whole-PE x64 driver, chunked TUs, dispatch table, import map |
| `difftest64.py` | 652 lines — lifted C vs **host CPU** running original bytes |
| `disasm64.py` | 562 lines — `.pdata`/Guard-CF/exports/relocs/SEH+funclet recovery |
| `runtime/recomp64_cpu/{cpu64.h,eh64.c,eh64.h}` | 599 / 398 / 70 lines — vendored here too |
| Omill local checkout | **NOT PRESENT** (no directory at the probed paths) |

Consequence: PCRecomp has a whole-image x64 lifter plus host-CPU differential
testing, but its Win64 story is IAT forwarding on a Win64 host. Omill (assessed
previously from upstream README only, MIT metadata) targets Win64 PE with an
explicit "not recommended for full binary lifting/recompilation" limitation and
requires LLVM 21 + remill for e2e tests. Neither, as inspected, supplies this
product's required combination of whole-PE lifting **and** self-answered
Win32HLE imports **and** non-Windows host execution. That is a reason to measure,
not a verdict to copy.

## 3. Licence constraints

| Component | Licence | Constraint for a future GO |
| --- | --- | --- |
| GameReComp | AGPL-3.0-only (`LICENSE`, `docs/LICENSES.md`) | Any adopted upstream code must be AGPL-compatible; derivative notices must be preserved. |
| PCRecomp (`sp00nznet/pcrecomp`) | MIT, Copyright (c) 2026 sp00nz | MIT is AGPL-compatible, but the copyright + permission notice must be added to `NOTICE` / `docs/LICENSES.md` for any copied code. This gate copied none. |
| Omill (`binsnake/omill`) | MIT (upstream metadata only — **not verified locally**) | Same notice duty; additionally LLVM 21/Clang/remill toolchain licences must be reviewed if built. Not verified here. |
| Stela design notes (`docs/stela-audit/`) | GPL-2.0-or-later | Design notes only; no Stela source or identifiers may be copied into implementation. |
| Game binaries / assets | Proprietary | Never commit a PE image here. Record SHA-256 + size + lawful source only; analysis requires the user's rights (README "Responsible Use"). |

A criterion that depends on code whose licence or notice duty is unresolved is
**FAIL**, not "informational".

## 4. Benchmark a representative x64 function through a candidate lifter

Issue #148 asks for a representative x64 function benchmarked through a candidate
lifter with exact commands and hashes. This section defines that protocol; it is
**NOT RUN** in this gate package (no x64 lifter is wired into the CLI, and the
host has no MSVC x64 `ml64`/`cl`). A future gate run must produce all artifacts
below or the benchmark criterion stays FAIL.

```sh
# A. Freeze inputs BEFORE the run (hashes are the gate identity).
shasum -a 256 "$PROBE_X64_EXE" "$REPRESENTATIVE_X64_EXE" | tee reports/x64_benchmark_inputs.sha256

# B. Function catalog from the x64 disassembler (candidate: PCRecomp design).
python3 "$PCRECOMP_DIR/tools/disasm/disasm64.py" "$REPRESENTATIVE_X64_EXE" \
  --out reports/x64_benchmark_catalog.txt

# C. Lift the catalog with the candidate lifter (never wired into this CLI).
python3 "$PCRECOMP_DIR/tools/lift/generate64.py" "$REPRESENTATIVE_X64_EXE" \
  reports/x64_benchmark_catalog.txt --out-dir test-results/x64_benchmark

# D. Differential test against the host CPU (NOT a claim about this product).
#    Requires MSVC x64 (ml64 + cl); absent on this host => criterion UNMEASURED.
python3 "$PCRECOMP_DIR/tools/lift/difftest64.py" "$REPRESENTATIVE_X64_EXE" \
  reports/x64_benchmark_catalog.txt --count 2000 \
  --report reports/x64_benchmark_difftest.json
```

Required committed evidence: the SHA-256 manifest, the catalog, the generated C
unit for the chosen function, the differential JSON with `mismatches: 0`, the
exact candidate HEAD, and the toolchain versions. `--count 2000` is the minimum
sample from `docs/X64_FEASIBILITY_GATE.md` §G2. A lifter that is only quoted from
a README, or a diff test that was not executed, does not satisfy this section.

## 5. Go/no-go checklist

All items are MANDATORY and currently **unchecked**. PASS requires a named
artifact in the repository; "not measured", "builds", and "looks right" are FAIL.

- [ ] **R0 — Roadmap prerequisite.** x86 PE32 macOS **and** Linux acceptance evidence recorded in `docs/ACCEPTANCE_LEDGER.md`. (Unmet here: no Linux runner; see §8.)
- [ ] **U1 — Upstream inspection executed.** §2 commands run with outputs committed, including pinned HEADs; PCRecomp MIT notice duty recorded. (Inspected, outputs in §2; not committed as a run artifact.)
- [ ] **U2 — Omill assessed from a local checkout or recorded as unavailable.** If available, licence/toolchain verified; if not, no capability claimed from README. (NOT PRESENT — recorded, not a PASS.)
- [ ] **B1 — Representative x64 function benchmarked.** §4 protocol executed, hashes + `mismatches: 0` committed. (NOT RUN.)
- [ ] **G1 — 64-bit PE loader.** PE32+ fields consumed fail-closed; probe + ≥3 real images mapped with zero OOB; `pe32` regressions green.
- [ ] **G2 — x86-64 semantics.** Host-CPU differential over ≥2000 instructions/title, all GPR/XMM/flags/memory/lock atomics; `RECOMP_TODO` inventory enumerated.
- [ ] **G3 — Win64/SysV ABI split.** Declared ABI enum, ≥2 host ABIs exercised, guest SEH unwinding tested.
- [ ] **G4 — Non-Windows host backend.** Generated project builds and runs with zero unresolved imports, no Wine/qemu/emulator; pinned toolchain log committed.
- [ ] **G5 — Test corpus.** ≥3 real PE32+ images with SHA-256, function-catalog precision/recall vs a symbol source.
- [ ] **G6 — Acceptance evidence.** Full A0–A10 with original-Windows oracle for ≥1 title; `python3 -m pytest tests/ -q` and `sh verify.sh --fast` green.
- [ ] **N1 — No support claim without a native binary.** §6 rule honoured: refusal auditor returns `REFUSED` / `MISSING_NATIVE_BINARY` until a built binary exists.

| State | Condition |
| --- | --- |
| **GO** | R0, U1, U2, B1, G1–G6 and N1 all satisfied with committed artifacts. Only then may an implementation branch for #13 be opened. |
| **REVIEW** | All but one item satisfied; the missing item and owner recorded; no x64 code merged before it closes. |
| **NO-GO (current state)** | R0, B1, G1–G6 unmet; U2 unavailable; N1 enforced by `tests/test_x64_gate.py`. Issue #148 stays OPEN; #13/#133/#134 stay DEFERRED. |

## 6. No x64 support claim without a built and executed native binary

**Rule (verbatim, pinned by `tests/test_x64_gate.py`): "no x64 support claim may be made without a built and executed native binary".**

The CLI refuses x64 input before any build (§7), so this pipeline cannot emit a
native x64 binary at all today. Independently, `tools/autorecomp/refusals.py`
(`collect_refusals`) refuses any project with `MISSING_NATIVE_BINARY > 0`. A
manifest that merely *names* an x64 binary, a generated C unit, or a README
sentence is not evidence: until a native binary exists on disk **and** was
executed under the oracle in G6, the verdict is `REFUSED`. The deterministic
test proves both halves of this: the auditor refuses a binary-less x64 project,
and the CLI still refuses the x64 input even when a binary file is present.

## 7. Reproduce the refusal (exact commands and expected output)

From the repository root, with the probe path supplied by the caller (no x64
binary is committed to this repository):

```sh
python3 -m tools.autorecomp "$PROBE_X64_EXE" --input-os windows --input-arch auto \
  --target-os host --target-arch host --out-mode source-only --no-table --no-progress
# error: unsupported input architecture: x86_64 (this pipeline reconstructs PE32 x86 guests; a 64-bit build of the same game, if one exists, would be a different binary to reconstruct -- 64-bit guest support is planned, not implemented)
# EXIT=4

python3 -m tools.autorecomp "$PROBE_X64_EXE" --input-os windows --input-arch x86 \
  --target-os host --target-arch host --out-mode source-only --no-table --no-progress
# error: --input-arch x86 but the binary's header says x86_64; refusing to reconstruct on a guess
# EXIT=4
```

`tests/test_x64_gate.py` synthesizes a minimal PE32+ header in a temp directory,
runs these two commands and asserts exit code `4` and the two named strings, so
the refusal is reproducible without any game binary.

## 8. Limits of this gate

1. No x64 lifter was built, wired or run; no x64 game binary is committed. The probe is a synthesized header and proves only detection/refusal.
2. PCRecomp was inspected at pinned HEAD `a85ea970…`; its x64 tools were **not** executed. README claims (0 lift errors, 92,552 functions, etc.) are quoted, not reproduced.
3. No Omill checkout is present; its licence and toolchain were not locally verified.
4. No Linux runner or original-Windows oracle is available here, so R0 and G6 cannot be evidenced in this environment.
5. MSVC x64 (`ml64`/`cl`) is absent, so the §4 host-CPU differential benchmark is specified but not runnable here.
6. Every checkbox in §5 is a proposed decision criterion, not a measurement. Meeting them requires a future, separately reviewed gate run with real artifacts.
