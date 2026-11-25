# Issue #133 — acceptance evidence (Windows PE32 x86 games → macOS arm64)

Acceptance has three items. Each is met by merged, hash-backed evidence.

1. **PE32 x86 game builds and launches on macOS arm64 with logs and frames.**
   `proof/issue-28/issue28-proof.json` records the exact original binary
   (`d1b9dabc...`), 0 lift errors, 166 EXE + 34 companion `SDL_net.dll` imports
   bound with **0 unresolved**, and 3 non-blank 256x200 frames (3,468 colours)
   captured by `tools.game_runner` with a legal Freedoom WAD. The rebuilt native
   binary SHA-256 is `c4c000a3...`. `docs/ISSUE_28_PROOF.md` and
   `proof/ports/igi-native-port.json` add further real native runs.

2. **x64 feasibility gate documented with a verified decision.**
   `docs/feasibility/X64_LIFTER_GATE.md` states `NO-GO / DEFERRED — this gate is
   open and unmet`, with the exact U/B/G/N gates. `tools/win64_gate_summary.py`
   recomputes hashes from supplied files and refuses to emit SUPPORTED from
   flags or a doc alone.

3. **No fake success for refused architectures.**
   `tools/autorecomp/cli.py::_resolve_target` refuses cross-target builds;
   PE32+ x64 is refused by the lift path and labelled DEFERRED, never PASS.

**Limits**: this is scoped PE32 x86 evidence plus a documented x64 decision —
not universal Windows-app support, not gameplay equivalence, and no
original-Windows frame oracle. No universal PASS claim is made.
