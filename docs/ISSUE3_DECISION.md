# Issue #3 — evidence-based decision: no additional code justified

Issue #3's Definition of Done allows a recorded decision that no additional
code is justified, provided it is evidence-based. This document records that
decision. It is not a gameplay, source-fidelity or completeness claim.

## Evidence reviewed (all paths exist on develop)

| Path | What it establishes |
|---|---|
| `runtime/win32hle/dinput.c` | DirectInput keyboard/mouse path already present and registered; no duplicate device stack added. |
| `tools/real_input_pump_proof.py` | Deterministic input replay/bisection capability already merged for issue #147; no duplicate replay framework needed. |
| `tools/issue25_evidence.py` | Fail-closed per-function provider selection with differential gates already merged. |
| `tools/graphics_defer_matrix.py` | D3D9Ex/D3D10/11 feasibility DEFER already published with a reproducible matrix (issue #7). |
| `docs/DECOMPILER_DECISION.md` | Evidence-based DEFER for the six decompiler names; no tool integrated without gates. |
| `docs/DECOMP_REUSE_DECISION.md` | Upstream reuse dispositions with pinned commits and licences; no game-specific engine code copied. |
| `docs/ISSUE_28_PROOF.md` | Real import-binding and rendered-frame evidence for freely redistributable games. |
| `docs/FRIDA_RESIGN_ATTACH_EVIDENCE.md` | Real Frida attach after ad-hoc re-sign; no system security setting changed. |

## Decision

- **D3D8**: no owned PE32 game in the corpus requires D3D8; #7 already records a DEFER for later D3D APIs with a reproducible cost/compatibility matrix. No WebGPU/DOM renderer was transplanted.
- **Replay/divergence**: the generic input-pump verifier and the fail-closed per-function differential gate already cover the reusable pieces; no second framework is justified.
- **Runtime duplication**: DirectInput is present and registered; the decompiler and reuse decisions reject duplicate backends with explicit reasons and pinned references.
- **Verdict**: `NO_ADDITIONAL_CODE_JUSTIFIED` on the evidence above. New code would duplicate existing, tested infrastructure without a measured gap.

## Limits

No original-vs-native gameplay scenario is claimed here. A licensed D3D8 game with a golden frame, or a measured gap in the existing replay gate, would reopen the need for implementation. No 100% PASS or gameplay equivalence is claimed.
