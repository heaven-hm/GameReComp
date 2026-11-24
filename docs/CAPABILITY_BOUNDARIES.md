# What is actually proven — capability boundaries

This document deliberately does **not** claim that all x86 software, or any
x64 software, converts 100% to macOS/Linux. It records what was measured, and
makes the refusals permanent regression tests.

## Refusals proven by tests

`tests/test_capability_boundaries.py` passes 3/3 with caller-supplied paths:

1. A PE32+ x64 input is refused:
   `error: unsupported input architecture: x86_64 ... 64-bit guest support is planned, not implemented`
2. Forcing `--input-arch x86` on an x64 header is refused:
   `refusing to reconstruct on a guess`
3. A Linux cross-target on the macOS host is refused:
   `cross-target build requested (linux/x86_64) ... compiles with the host toolchain only`

These are fail-closed behaviours, not oversights. Issue #13 (PE32+/x64) stays
DEFERRED by the roadmap boundary; this work did not add an x64 loader, lifter or
host ABI. Linux execution has no runner on this host (no Docker daemon; Colima
image download stalled; no qemu-user/kernel), so no Linux PASS row exists.

## Measured coverage on this corpus (not a percentage claim)

- **15 PE32 game EXEs** pass the GUI's `--selftest-pe` inspection mode (exit 0).
  This proves PE parsing, not execution.
- **API inventory**: 2,227 registered shims; 20 missing Win32 system imports;
  294 game-owned DLL imports; separate counting rules (schema 1.1.0).
  Registration is not behavioural verification.
- **Native builds**: small games build with 0 lift errors (Cave Story, Ikachan,
  Gunroar, Tumiki Fighters, Warning Forever, Parsec47, Chocolate Doom, OUCC
  Tetris, and others in the build loop). Build success is **A3**, not gameplay.
- **Execution with logs/frames**: Cave Story 2 frames (640×480) and Ikachan
  3 frames (351×238) stayed alive 12 s → **PARTIAL** (A4/A5-ish, no oracle).
  Gunroar: DMD exception then later SIGBUS → FAIL (#27). Tumiki Fighters:
  unresolved indirect calls, 0 frames. Newly downloaded Tetris: 7 unresolved
  imports, 0 frames. Chocolate Doom: 12 unresolved imports, 0 frames, plus no
  WAD. These are concrete counterexamples to any "100% conversion" claim.
- **GUI**: deterministic selftest modes pass for all 15 games; real mouse
  clicking is BLOCKED by macOS assistive access (-25211) and screencapture is
  blocked, so no click-behaviour claim.

## Why a 100% proof cannot be produced here

1. The corpus is finite and the missing-import inventory is non-zero.
2. Real games fail after a successful native build (exceptions, unresolved
   indirect calls, missing imports), so compile success is not equivalence.
3. No original-Windows oracle is available, so A10 (original-vs-native
   behaviour) is unverifiable for any title.
4. x64 and Linux target conversion are explicitly refused by the tool, and
   adding them is a separate roadmap item (#13).
5. Host resources SIGKILL larger builds (#26), so even building the whole
   corpus is not currently possible, let alone proving it.

## What would be required

For each game and platform: every import bound, A0–A10 evidence captured,
frame/input/audio/save-load comparisons against the original Windows build,
and a stable Linux runner. Until then the honest statement is: **partial,
measured coverage with documented failures**, not 100%.
