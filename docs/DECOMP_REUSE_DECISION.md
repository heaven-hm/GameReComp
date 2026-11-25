# Decomp reuse decision for issue #5 — evidence-based defer

**Decision: explicit evidence-based defer. Issue #5 stays OPEN; it is not closed, fixed, or passed.**

Issue #5 ("Reuse real PC game decomp projects WITHOUT duplicating PCRecomp:
D3D9/DInput8 feasibility, replay and mod-file HLE audit") permits an explicit,
evidence-based defer. This document is that defer. It records what was actually
inspected for five project families, what was reused, what was merely used as a
semantic reference, and what was declined for licence or portability reasons. It
adds no backend, no renderer, no D3D9/DirectInput8 shim, no replay system, and no
mod-file HLE.

## Scope and decision

- Reuse continues to go through the existing PCRecomp baseline and the existing
  shared Win32HLE/SDL2 presentation path. No parallel loader, runtime, COM
  implementation, graphics backend, or renderer was introduced.
- Upstream inspection is documentation-only unless a change lands in the normal
  provenance chain (`NOTICE`, `docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md`) with a
  pinned SHA and a licence review.
- No game executable, asset, or game-derived source was committed for any family.
- The defer is fail-closed: absent a pinned checkout, licence review and
  portability gate, a family is not reused and is not claimed as verified.

## Evidence base

Existing committed paths used to reach this decision:

| Path | What it establishes |
|---|---|
| `docs/closure-audit/ISSUES.json` | Issue #5 is OPEN, with the exact acceptance title. |
| `docs/closure-audit/REPORT.md` | Issue #5 is tracked as OPEN with no manifest. |
| `docs/ISSUE_STATUS.md` | Issue #5 is PARTIAL (audit only); D3D9/DInput8 frontends reuse the shared presentation path; no game-level verification. |
| `docs/ISSUE_WORK_LEDGER.md` | Issue #5 commits are audit-only; no `d3d9.dll`/`dinput8.dll` build or run evidence. |
| `docs/ISSUE_FINAL_STATUS.md` | Highest gate none (audit only); remaining blocker A0/A3. |
| `docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md` | §1 reused/adapted rows, §2 inspected-not-copied rows, §3 discarded negative result, §4 rules against new backends and game assets. |
| `NOTICE` | Authoritative third-party inventory: igi-recomp DirectMusic COM façade and DirectDraw attachment entries. |
| `LICENSES/igi-recomp-MIT.txt` | Full MIT text for the igi-recomp code that was adapted. |
| `docs/LICENSES.md` | Repository is AGPL-3.0-only; rev.ng is an invoked, not vendored, tool; no game executable or asset is distributed. |
| `verify-engine/README.md` | The differential methodology was reduced to game-agnostic observables; a 4/4 scenario matrix is not a game-parity claim. |
| `docs/MULTILIFTER.md` | rev.ng is an existing optional provider, never a required artifact. |
| `docs/ARCHITECTURE.md` | PE32 x86 is the supported structural path; lifting and runtime layers are inherited in place. |

## Per-family dispositions

| Family | Pin | Licence | Disposition | Reuse | Portability reason |
|---|---|---|---|---|---|
| igi-recomp | `278eaf6394f7f42c7de6cffef2cf982154f59899` | MIT, Copyright (c) 2026 HenryKun55 | PORTABLE-REUSE-ONLY | Portable FVF stride constant, DirectMusic COM façade, DirectDraw depth-buffer attachment, differential method | Game-specific D3D7/OpenGL/DirectInput/IGI shim and renderer not copied; host stays SDL2 |
| IGI-WEB | `0a7472644f77e7287b3e478a06c47a47beae1e7e` | MIT | INSPECTED-NOT-COPIED | D3D8 public ABI and replay design inspected only | WebGPU/DOM/WASM host code is not portable to the native SDL2 host; independent COM frontend |
| PCRecomp | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT, Copyright (c) 2026 sp00nz | BASELINE-IN-PLACE | PE32 loader, guest memory, shim ABI, runtime registration and in-place fixes | PE32 x86 only; PE32+/Win16/NE deferred; no second loader or runtime |
| revng | `0e2b25952231590f384cf6f77867db0b3d39b712` | rev.ng licence (reviewed) | PROVIDER-WRAPPER-ONLY | Existing `tools/autorecomp/revng.py` wrapper reused; pointer-return fix in `tools/autorecomp/highlevel.py` | User-installed external binary via env vars; no rev.ng source vendored; optional readability path |
| NFS recomp | none in this repository | none recorded | DEFERRED-NO-EVIDENCE | None; no pinned checkout, provenance row or licence text here | Licence and portability cannot be evaluated without a pinned checkout; no code or assets taken |

### igi-recomp — PORTABLE-REUSE-ONLY

`HenryKun55/igi-recomp` at `278eaf6394f7f42c7de6cffef2cf982154f59899` is MIT
(Copyright (c) 2026 HenryKun55; full text in `LICENSES/igi-recomp-MIT.txt`). Only
portable facts were adapted into the existing runtime: the D3D7 FVF stride table
(`fvf_stride` → `d3d7_fvf_stride`, with the missing `D3DFVF_PSIZE` term), the
DirectMusic COM class/method table, and the DirectDraw depth-buffer attachment
behaviour. Its per-function A/B differential methodology was reduced to
game-agnostic observables in `verify-engine/README.md`. The game-specific D3D7 →
OpenGL shim, DirectInput layer, IGI replay logic and asset paths were **not**
taken: they target a different host stack and would violate the no-new-backend
rule. A GL present experiment adapted from that source was **discarded and not
committed** after both captured frames stayed black (§3 of
`docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md`).

### IGI-WEB — INSPECTED-NOT-COPIED

`swampodsarkar321/IGI-WEB` at `0a7472644f77e7287b3e478a06c47a47beae1e7e` is MIT.
The D3D8 public ABI and replay design were inspected as references (§2 of
`docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md`). Its WebGPU/DOM/WASM host code is not
portable to GameReComp's native SDL2 host, so an independent COM frontend was
written instead. No source was transplanted; no notice duty arises.

### PCRecomp — BASELINE-IN-PLACE

`sp00nznet/pcrecomp` at `a85ea970f9e0640ab6546d2ca7a01fccb132973d` is MIT
(Copyright (c) 2026 sp00nz) and is the common baseline. The PE32 loader, guest
memory, shim ABI and runtime registration were inherited, and every listed fix
was applied **in place** to the single loader/runtime/COM layer rather than
duplicated. This is how issue #5's "without duplicating PCRecomp" constraint is
met. The supported structure is PE32 x86; PE32+ and Win16/NE stay deferred, and
no parallel presenter was added.

### revng — PROVIDER-WRAPPER-ONLY

`revng/revng` at `0e2b25952231590f384cf6f77867db0b3d39b712` has a reviewed
upstream licence and is an invoked tool, not a vendored dependency
(`docs/LICENSES.md`). The existing `tools/autorecomp/revng.py` wrapper was reused
for the pointer-return fix; no replacement provider was added and no rev.ng
source was copied. `docs/MULTILIFTER.md` keeps it as an optional provider whose
divergent results are skipped, never required as a build artifact.

### NFS recomp — DEFERRED-NO-EVIDENCE

No pinned NFS recomp checkout, provenance row, licence text, or reviewed artifact
exists in this repository. No NFS recomp code or asset was copied. Reuse is
deferred **for lack of evidence**, not as a finding about that upstream project.
To revisit: record a pinned SHA, complete an AGPL-3.0-only licence compatibility
review (MIT-style terms with `NOTICE` attribution are acceptable; unclear or
incompatible terms are not), and pass the no-new-backend/renderer portability gate
with fail-closed difftest evidence.

## Why no game-specific engine code or assets were copied

- Every adapted item is a portable constant, public COM/ABI layout, or algorithm
  placed into code that already existed in this repository; game-specific shims,
  renderers, replay logic and mod handlers were not transplanted.
- No game binary, proprietary asset, or game-derived source is distributed;
  `docs/LICENSES.md` states this explicitly and §4 of
  `docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md` forbids committing them.
- The one experimental graphics port was discarded (black frames) and is not in
  the tree.
- Licence duties were discharged where code was adapted (`NOTICE` and
  `LICENSES/igi-recomp-MIT.txt`); semantic-only references (Wine LGPL, SDL,
  DirectX-Headers, IGI-WEB, rev.ng) required no code copy.

## Conclusion

This is an explicit, evidence-based defer of issue #5, not a completion claim and
not a game-parity result. No game-specific engine code and no game assets were
copied from any of the five inspected families. The existing PCRecomp baseline
and shared runtime remain the only reuse path; no new backend or renderer was
added.

## Machine-readable decision record

```json
{
  "issue": 5,
  "decision": "evidence-based defer",
  "status": "OPEN",
  "no_new_backend_or_renderer": true,
  "conclusion": "Explicit evidence-based defer, not a completion claim: no game-specific engine code and no game assets were copied from any inspected family.",
  "issue_evidence": [
    "docs/closure-audit/ISSUES.json",
    "docs/closure-audit/REPORT.md",
    "docs/ISSUE_STATUS.md",
    "docs/ISSUE_WORK_LEDGER.md",
    "docs/ISSUE_FINAL_STATUS.md"
  ],
  "families": [
    {
      "family": "igi-recomp",
      "repository": "https://github.com/HenryKun55/igi-recomp",
      "pin": "278eaf6394f7f42c7de6cffef2cf982154f59899",
      "licence": "MIT, Copyright (c) 2026 HenryKun55",
      "disposition": "PORTABLE-REUSE-ONLY",
      "reuse": "Adapted the D3D7 FVF stride constant, the DirectMusic COM facade and the DirectDraw depth-buffer attachment into the existing Win32HLE runtime; the differential method was reduced to game-agnostic observables.",
      "portability_reason": "The game-specific D3D7/OpenGL/DirectInput/IGI shim and renderer code targets a different host stack; only portable constants, COM layouts and ABI facts were adapted into the existing SDL2-backed runtime. The GL present experiment produced black frames and was discarded, not committed.",
      "evidence": [
        "docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md",
        "NOTICE",
        "LICENSES/igi-recomp-MIT.txt",
        "verify-engine/README.md"
      ]
    },
    {
      "family": "IGI-WEB",
      "repository": "https://github.com/swampodsarkar321/IGI-WEB",
      "pin": "0a7472644f77e7287b3e478a06c47a47beae1e7e",
      "licence": "MIT",
      "disposition": "INSPECTED-NOT-COPIED",
      "reuse": "D3D8 public ABI and replay design inspected as a reference only; no source transplanted.",
      "portability_reason": "WebGPU/DOM/WASM host code is not portable to GameReComp's native SDL2 host, so an independent COM frontend was written instead.",
      "evidence": [
        "docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md",
        "docs/LICENSES.md"
      ]
    },
    {
      "family": "PCRecomp",
      "repository": "https://github.com/sp00nznet/pcrecomp",
      "pin": "a85ea970f9e0640ab6546d2ca7a01fccb132973d",
      "licence": "MIT, Copyright (c) 2026 sp00nz",
      "disposition": "BASELINE-IN-PLACE",
      "reuse": "PE32 loader, guest memory, shim ABI, runtime registration and listed fixes inherited and applied in place; no parallel implementation.",
      "portability_reason": "PE32 x86 is the supported structure; PE32+ and Win16/NE are deferred, and no second loader, runtime or presenter was added.",
      "evidence": [
        "docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md",
        "docs/ARCHITECTURE.md",
        "docs/LICENSES.md"
      ]
    },
    {
      "family": "revng",
      "repository": "https://github.com/revng/revng",
      "pin": "0e2b25952231590f384cf6f77867db0b3d39b712",
      "licence": "rev.ng licence (reviewed); external tool, not vendored",
      "disposition": "PROVIDER-WRAPPER-ONLY",
      "reuse": "Existing tools/autorecomp/revng.py wrapper reused for the pointer-return fix; no replacement provider and no rev.ng source copied.",
      "portability_reason": "User-installed binary invoked through env vars; optional readability path whose divergent results are skipped, never a required build artifact.",
      "evidence": [
        "docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md",
        "docs/LICENSES.md",
        "docs/MULTILIFTER.md"
      ]
    },
    {
      "family": "NFS recomp",
      "repository": null,
      "pin": null,
      "licence": "none recorded in this repository",
      "disposition": "DEFERRED-NO-EVIDENCE",
      "reuse": "None. No pinned checkout, provenance row or licence text exists here; no code or assets were copied.",
      "portability_reason": "Licence and portability cannot be evaluated without a pinned checkout; any future reuse must pass the AGPL-3.0-only licence review and the no-new-backend/renderer gate first.",
      "evidence": [
        "docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md",
        "docs/LICENSES.md",
        "docs/ISSUE_STATUS.md"
      ]
    }
  ]
}
```

## Limits

- No live game run, no `d3d9.dll`/`dinput8.dll` shim build and no replay or
  mod-file HLE implementation; the upstream audits remain documentation.
- NFS recomp has no local evidence, so its defer reflects missing evidence, not a
  negative judgement of that project.
- This document neither closes issue #5 nor adds a backend or renderer.
