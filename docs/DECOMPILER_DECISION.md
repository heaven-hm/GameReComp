# Decompiler evaluation decision (issue #4): evidence-based DEFER

Status: **decision doc only, issue remains OPEN**. No decompiler is integrated,
no upstream source is vendored, and no external licence text or GPL/AGPL code is
copied into this repository. This document applies the project's fail-closed
evidence rule to the six names in the evaluation scope and records one verdict
per tool. `DEFER` means *not integrated, pending measured gates*; it is not a
claim that the tool is bad, and it is not a gate result in its own right.

## Scope and method

Issue #4's title names "Anvil overlap, Omill x64, Anvill/Rellic source
benchmark" (`docs/closure-audit/ISSUES.json`, `docs/ISSUE_FINAL_STATUS.md`,
`docs/ISSUE_WORK_LEDGER.md`). This decision evaluates the six requested names:
**Anvil, Omill, Anvill, Rellic, McSema, RetDec**. Only local, reproducible
artifacts are treated as evidence:

- `tools/revng_probe.py` — read-only PATH detector with a per-tool registry of
  upstream role, licence claim and adoption caveat. A hit is a detection, not
  a fidelity result. Reproduce with `python3 -m tools.revng_probe --compact`.
- `tests/test_revng_probe.py` — deterministic tests for that detector.
- `docs/feasibility/X64_LIFTER_GATE.md` — Omill assessment and the U2 gate.
- `docs/ISSUE_FINAL_STATUS.md` and `docs/ISSUE_WORK_LEDGER.md` — A0 benchmark
  and the three acceptance criteria (`anvill-rellic-benchmark`,
  `omill-feasibility`, `decision-doc`), all recorded as unmet.
- `docs/ISSUE_STATUS.md` — #4 recorded as "NOT TESTED" for the feasibility gate.
- `docs/closure-audit/ISSUES.json` — the verbatim issue #4 title.
- Historical audit `docs/REVNG_INTEGRATION_AUDIT.md`, pruned in commit
  `93b9765`; still reproducible with
  `git show 93b9765^:docs/REVNG_INTEGRATION_AUDIT.md`. It is cited as history,
  not as a file present at HEAD.

No tool was cloned, built, executed or imported for this decision. The absence
of a local checkout is recorded as an absence, never converted into a capability
claim. A README is never accepted as a measurement.

## Gates that must be met before *any* integration

| Gate | Requirement | State on this evidence |
|---|---|---|
| G1 provenance / licence | Exact upstream commit pinned; licence terms reviewed against project policy; copyleft components kept as external processes, never linked or vendored | Not established for any tool |
| G2 availability | Tool reachable from a pinned path/container in the deterministic test workflow; library-only installs detected separately | Core tools not on PATH per `tools/revng_probe.py`; Omill checkout not present per `docs/feasibility/X64_LIFTER_GATE.md` |
| G3 A0 benchmark | Committed, reproducible control-flow/struct-recovery comparison against the existing rev.ng provider | `docs/ISSUE_WORK_LEDGER.md`: `anvill-rellic-benchmark` met=false |
| G4 harness parity | Fixture parity evidence through the harness before behaviour-changing work; no live-game claim from a build or emulator alone | No fixture parity artifact committed for any surveyed tool |
| G5 platform order | x86 PE32 macOS/Linux acceptance first; Win64 work behind the separate feasibility gate | `docs/ISSUE_FINAL_STATUS.md` #13 deferred; PE32+ games not run |
| G6 scope identity | The named tool must be identifiable in a local artifact; an unnamed or alias-only entry cannot be benchmarked | Fails for Anvil, McSema and RetDec (see below) |

All gates are mandatory. A missing gate means `DEFER`; it never means
`INTEGRATE`, and a gate name is not itself a result.

## Decision matrix (machine readable)

```json
{
  "schema_version": 1,
  "issue": 4,
  "decision": "DEFER_ALL",
  "integrated_tools": [],
  "tools": [
    {
      "tool": "Anvil",
      "verdict": "DEFER",
      "gates_met": [],
      "reason": "No distinct tool named Anvil appears in any local audit; docs/closure-audit/ISSUES.json uses 'Anvil overlap' only in the issue title, while tools/revng_probe.py registers 'anvill' (a different name) and docs/ISSUE_STATUS.md records #4 as NOT TESTED for the feasibility gate. With no identifiable artifact there is nothing to benchmark, so gate G6 fails.",
      "artifacts": ["docs/closure-audit/ISSUES.json", "tools/revng_probe.py", "docs/ISSUE_STATUS.md"]
    },
    {
      "tool": "Omill",
      "verdict": "DEFER",
      "gates_met": [],
      "reason": "docs/feasibility/X64_LIFTER_GATE.md records the local checkout as NOT PRESENT and the U2 item as recorded-unavailable rather than a gate result; capability was taken from an upstream README fetch only, and the tool is Win64-only with an explicit no-full-binary-recompilation limitation. docs/ISSUE_WORK_LEDGER.md records omill-feasibility met=false and docs/ISSUE_FINAL_STATUS.md leaves the A0 benchmark unmet, so gates G1, G2, G3 and G5 fail.",
      "artifacts": ["docs/feasibility/X64_LIFTER_GATE.md", "docs/ISSUE_WORK_LEDGER.md", "docs/ISSUE_FINAL_STATUS.md"]
    },
    {
      "tool": "Anvill",
      "verdict": "DEFER",
      "gates_met": [],
      "reason": "tools/revng_probe.py records AGPL-3.0 with a network-use clause, a master branch that consumes protobuf specs from an in-development closed-source Ghidra plugin, an unsupported anvill-specify-bitcode helper, and no binary on PATH; tests/test_revng_probe.py checks those registry facts deterministically. docs/ISSUE_WORK_LEDGER.md records anvill-rellic-benchmark met=false and decision-doc met=false, so gates G1, G2, G3 and G4 fail.",
      "artifacts": ["tools/revng_probe.py", "tests/test_revng_probe.py", "docs/ISSUE_WORK_LEDGER.md"]
    },
    {
      "tool": "Rellic",
      "verdict": "DEFER",
      "gates_met": [],
      "reason": "tools/revng_probe.py records a strict LLVM 16 and Z3 4.7.1+ coupling, requires bitcode from the matching LLVM version, and reports no rellic-decomp binary on PATH; tests/test_revng_probe.py confirms those facts without invoking a tool. docs/ISSUE_FINAL_STATUS.md leaves the A0 benchmark unmet, so gates G2, G3 and G4 fail.",
      "artifacts": ["tools/revng_probe.py", "tests/test_revng_probe.py", "docs/ISSUE_FINAL_STATUS.md"]
    },
    {
      "tool": "McSema",
      "verdict": "DEFER",
      "gates_met": [],
      "reason": "No local audit, checkout, probe entry or benchmark artifact mentions McSema: tools/revng_probe.py has no such entry and git log --all -S McSema --oneline is empty. docs/ISSUE_WORK_LEDGER.md records decision-doc met=false and docs/ISSUE_STATUS.md records #4 as NOT TESTED; there is no committed control-flow/struct comparison, so gates G1, G2, G3 and G6 fail.",
      "artifacts": ["docs/ISSUE_WORK_LEDGER.md", "docs/ISSUE_STATUS.md", "tools/revng_probe.py"]
    },
    {
      "tool": "RetDec",
      "verdict": "DEFER",
      "gates_met": [],
      "reason": "No local audit, checkout, probe entry or benchmark artifact mentions RetDec: tools/revng_probe.py has no such entry and git log --all -S RetDec --oneline is empty. docs/ISSUE_FINAL_STATUS.md leaves the A0 benchmark unmet and docs/ISSUE_STATUS.md marks #4 NOT TESTED, so gates G1, G2, G3 and G6 fail.",
      "artifacts": ["docs/ISSUE_FINAL_STATUS.md", "docs/ISSUE_STATUS.md", "tools/revng_probe.py"]
    }
  ]
}
```

## Verdict notes

### Anvil — DEFER

The issue title in `docs/closure-audit/ISSUES.json` says "Anvil overlap", but no
local audit defines a tool by that name. The detector in `tools/revng_probe.py`
registers `anvill` (Anvill) and no `anvil`. Treating the two as one tool would
invent provenance, so this entry stays DEFER under G6 rather than being silently
merged into Anvill.

### Omill — DEFER

`docs/feasibility/X64_LIFTER_GATE.md` is the controlling local audit. It records
the `binsnake/omill` checkout as NOT PRESENT on this host, states that capability
must not be inferred from the fetched README, and records U2 as unavailable
rather than a gate result. It also records that Omill targets Win64 PE with an
explicit no-full-binary-recompilation limitation, which does not supply this
project's combination of whole-PE lifting, self-answered Win32HLE imports and
non-Windows host execution. `docs/ISSUE_WORK_LEDGER.md` records
`omill-feasibility` as met=false. No binary was built or run.

### Anvill — DEFER

`tools/revng_probe.py` (and its deterministic tests in
`tests/test_revng_probe.py`) records AGPL-3.0 with a network-use clause, a master
branch whose protobuf specs come from an in-development closed-source Ghidra
plugin, an `anvill-specify-bitcode` helper marked unsupported, and no binary on
PATH here. The pruned `docs/REVNG_INTEGRATION_AUDIT.md` reached the same finding
and recommended keeping the probe advisory. `docs/ISSUE_WORK_LEDGER.md` records
both `anvill-rellic-benchmark` and `decision-doc` as unmet.

### Rellic — DEFER

`tools/revng_probe.py` records Apache-2.0 but a hard LLVM 16 / Z3 4.7.1+
coupling and a matching-LLVM bitcode requirement, plus no `rellic-decomp` on
PATH. The A0 benchmark in `docs/ISSUE_FINAL_STATUS.md` is unmet, so there is no
committed control-flow/struct comparison to justify adoption.

### McSema — DEFER

There is no local evidence at all: the tool does not occur in the current tree
(`tools/revng_probe.py`) and `git log --all -S McSema --oneline` is empty.
Without a pinned artifact, licence review, benchmark or parity fixture, gates
G1/G2/G3/G6 are unmet. No capability or licence claim is made from memory or
from an external page.

### RetDec — DEFER

Same evidentiary position as McSema: no entry in the local probe registry, no
local audit, and `git log --all -S RetDec --oneline` is empty.
`docs/ISSUE_STATUS.md` records #4 as NOT TESTED for the feasibility gate and
`docs/ISSUE_FINAL_STATUS.md` leaves A0 unmet. DEFER, with no integration.

## Reproduce the evidence

```sh
python3 -m tools.revng_probe --compact
python3 -m pytest tests/test_revng_probe.py tests/test_decompiler_decision.py -q
git show 93b9765^:docs/REVNG_INTEGRATION_AUDIT.md
git log --all -S McSema --oneline
git log --all -S RetDec --oneline
```

## Limits

- This is a decision to **not integrate yet**, not a benchmark. The A0
  control-flow/struct-recovery report remains the open blocker.
- Detection is not fidelity: a found binary proves only that a binary exists.
  A missing binary does not prove a library-only install is absent (Remill).
- Licence strings recorded in the probe are upstream claims, not legal advice,
  and no copyleft component was linked or vendored here.
- The historical audit file was pruned from HEAD; it is cited via an exact git
  command so the claim stays reproducible without restoring it.
- The issue stays OPEN. Nothing in this doc closes an issue or claims a
  verified game result from a probe, a build, or an emulator.
