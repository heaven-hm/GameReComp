# Issue status — evidence-backed triage (2026-11-24)

All 28 issues remain **OPEN**. None is closed here: the project rule is that an
issue closes only on **100% verified acceptance**, and no original-Windows
oracle, proprietary game assets, or local Linux runner exists in this
environment. This document records what was actually measured, what is still
missing, and the exact commands/evidence behind each statement.

## Verification baseline

Repository state: `develop` head `0c785784e2488fde65854b81d4df8894cf786e6c`
(GameReComp) and `main` head `9e6e08618378dc3c328cf85a012ac7ea57ad2aa9`
(SDK). All PRs in both repositories are merged or closed; no pull request is
open.

Commands run on the merged tree and their results:

```
python3 -m pytest tests/ -q                  -> 524 passed, 11 skipped, 26 subtests
bash runtime/win32hle/build_selftests.sh     -> 71 ok
python3 tests/diff/differential_gate.py      -> DIFF-GATE PASS: 7 check(s) passed
python3 -m tools.autorecomp.regress --tier fast -> no regressions (8/8 checks)
```

Fresh PE32 native-macOS runs (current runtime, originals hashed before use):

```
python3 -m tools.corpus_acceptance ... --out <evidence-dir> --timeout 12
-> PASS 0, PARTIAL 2, BUILD ONLY 0, FAIL 2
   PARTIAL: noiz2sa (3 proven frames), tetris (3 proven frames)
   FAIL: 7zip (single-colour frames), wf (single-colour frames)
```

Evidence directory (CLI-supplied, outside the repository):
`/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/fresh-evidence/acceptance.json`
and `acceptance.md`. Fresh project directories are under
`.../opencode/fresh/{7zip,wf,tetris,noiz2sa,doom}`.

One stale-evidence caveat is essential: the earlier 8-pair corpus report used
native binaries built before the runtime fixes merged on 2026-11-23. Those
unresolved-import counts are **historical**, not evidence about the current
source. Every import count quoted below comes from a native rebuilt with the
current `develop` tree.

## Status vocabulary

- **PASS** — full acceptance including a real Windows oracle. Not claimed anywhere below.
- **PARTIAL** — real render evidence or recorded process evidence; not gameplay equivalence.
- **BUILD ONLY** — compiled/hashed, no run.
- **FAIL** — ran, but no accepted evidence.
- **BLOCKED** — missing required input or platform.
- **NOT TESTED** — no local runner or oracle.

## Per-issue triage

### #1 MASTER — autonomous readable recompilation and verified games
Ongoing programme issue. Verification gates are enforced (fail-closed corpus
ladder, differential gate, refusal diagnostics), but the 100% goal is not met:
no Windows oracle, no gameplay equivalence. Status: **NOT TESTED** for final
acceptance; OPEN.

### #2 IGI-recomp compatibility reuse
Shared D3D7/DirectInput/SDL2 HLE paths exist and the Stela audit records the
methodology without copying GPL source. A prior front-buffer presentation
experiment produced 100% black frames and was discarded; that negative result
is recorded in `docs/UPSTREAM_CODE_PROVENANCE.md`. Status: **FAIL** for IGI
frames; OPEN.

### #3 Audit Project-IGI-Awesome recomp projects
Provenance audits recorded in `docs/`; no duplicate runtime was introduced.
No replay or mission verification is possible without the original assets.
Status: **BLOCKED**; OPEN.

### #4 Evaluate PC decompilers (Anvil/Omill/Rellic)
Static audits and feasibility notes exist; no decompiler was integrated
without a passing gate. Status: **NOT TESTED** for the feasibility gate; OPEN.

### #5 Reuse real PC decomp projects without duplicating PCRecomp
Feasibility audits exist; D3D9/DInput8 frontends reuse the existing shared
presentation path. No game-level verification. Status: **PARTIAL** (audit
only); OPEN.

### #6 Legacy Direct3D 1–6, DirectDraw 7, Glide
D3D7 and DDraw7 frontends are registered. A real defect was fixed in
`ddraw.c`: `IDirectDraw7::CreateSurface` now uses `DDSURFACEDESC2` offsets
(108/76/40/68) instead of the legacy 108-byte-less offsets, verified by
`ddraw7_create_selftest.c`. A rebuilt `wf.exe` binds 147 imports with 0
unresolved and no longer shows the DirectDraw7 MessageBox, but its captured
frames are single-colour, so it is **FAIL** for render evidence. Glide is not
implemented. OPEN.

### #7 Post-D3D9 graphics (D3D9Ex, D3D10/11, DXGI, shaders, D3DX)
Explicitly **DEFERRED**. D3D8/D3D9 guest COM frontends exist but are
frontend-only and use the shared presenter; no D3D10/11/DXGI work. OPEN.

### #8 Windows 3.x / Win16 NE
Explicitly **DEFERRED**; the 16-bit lifter is not wired into the autonomous
pipeline. OPEN.

### #9 PE32 multi-module execution (DLL lifting, TLS/SEH/C++ EH, CRT)
Delay imports are discovered by `tools/pe/pe_analyze.py` and bound at runtime;
forwarded exports resolve by name/ordinal; TLS callbacks now dispatch before
the entry point (PR #105). MSVCRT data symbols (`_acmdln`, `_adjust_fdiv`,
`_controlfp`, `_except_handler3`, `_XcptFilter`) are registered. Full
cross-module execution and SEH remain unsupported, so acceptance is not met.
Status: **PARTIAL**; OPEN.

### #10 Audio/video compatibility (DirectSound3D, DirectMusic, MIDI, codecs)
`dsound.c` runs PCM through the existing SDL audio stream; 25 WINMM MIDI shims
were added with honest `MMSYSERR_BADDEVICEID` returns and a one-shot
diagnostic (`winmm_midi_selftest.c`). Bink/video uses existing ffmpeg-backed
HLE. No audio-output equivalence measurement exists. Status: **PARTIAL**;
OPEN.

### #11 Input compatibility (joystick/FFB, DirectInput8, XInput, HID, Raw Input)
DirectInput 3/5/7 keyboard/mouse HLE exists; upstream PCRecomp mouse-edge
mapping was adapted with attribution and a selftest. Joystick reports
unplugged; FFB/XInput/HID/Raw Input are not implemented. Status: **PARTIAL**;
OPEN.

### #12 Legacy multiplayer networking
Winsock loopback, DNS and socket shims exist with selftests. No deterministic
network verification and no DirectPlay/IPX/LAN discovery. Status: **PARTIAL**;
OPEN.

### #13 PE32+ Win64 pipeline
Explicitly **DEFERRED** pending the Omill-vs-PCRecomp feasibility gate. x64
inputs are refused with a diagnostic, not silently accepted. OPEN.

### #14 Compatibility coverage inventory
`tools/corpus_acceptance.py`, `tools/corpus_report.py`, the fail-closed
evidence ladder, `docs/PE32_COVERAGE.md` and `docs/LINUX_PLATFORM_STATUS.md`
are implemented and tested. A machine-readable matrix exists, but there is no
Windows oracle to promote any row to PASS. Status: **PARTIAL**; OPEN.

### #15 Project I.G.I. Mission 1
Original game assets are absent locally; the last recorded native frames were
100% black. Status: **BLOCKED**; OPEN.

### #16 Hitman: Codename 47
Original game assets absent. Status: **BLOCKED**; OPEN.

### #17 Max Payne
Original game assets absent. Status: **BLOCKED**; OPEN.

### #18 Max Payne 2
Original game assets absent. Status: **BLOCKED**; OPEN.

### #19 Hitman 2: Silent Assassin
Original game assets absent. Status: **BLOCKED**; OPEN.

### #20 Hitman: Contracts
Original game assets absent. Status: **BLOCKED**; OPEN.

### #21 Hitman: Blood Money
Original game assets absent. Status: **BLOCKED**; OPEN.

### #22 TrackMania Nations ESWC
Original game assets absent; earlier work produced build/execution evidence
only, never a verified race. Status: **BLOCKED**; OPEN.

### #23 TrackMania Nations Forever
Original game assets absent; build/execution evidence only. Status:
**BLOCKED**; OPEN.

### #24 TrackMania United Forever
Original game assets absent. Status: **BLOCKED**; OPEN.

### #25 Fail-closed per-function multi-lifter fallback
`tests/diff/differential_gate.py` is a real differential gate: it drives
`run_unicorn_function`, mutates flags and named memory, and feeds verdicts to
`multilifter.select()`; it reports 7/7 PASS and detects seeded historical
defects. It is wired into `verify.sh --differential` and the documented CI
config. This is one synthetic function/eight entry states, not a complete
per-function fallback across the corpus. Status: **PARTIAL**; OPEN.

### #26 Build scale / multi-GB generated C
`tools/autorecomp/translator.py` has byte-capped units and a measured manifest;
`tools/autorecomp/pipeline.py` now offers an opt-in wasm32 build cache keyed on
header bytes (verified by invocation counts, no wall-time claim). A full
multi-game corpus rebuild has not been run; disk exhaustion was observed during
parallel builds. Status: **PARTIAL**; OPEN.

### #27 Gunroar DMD exception
`eh32.c` supports the D throw path and earlier swarm work added diagnostics,
but no minimal DMD throw/catch fixture demonstrated that a caught exception
resumes guest execution and renders frames. **Re-measured on `develop`
`630fa91` (2026-11-24):** the original `RaiseException(0xE0440001)` no longer
appears. A fresh rebuild of `gr.exe` (SHA-256
`6ea800ee918547ca1953a24bebdcd07c29cef4ada617d7321a7dc3701e825636`) binds 136
imports with 0 unresolved, but with `SDL_VIDEODRIVER=cocoa` it now dies on
three null vtable calls — `ICALL: null target from 0x0040942C`, `0x00409324`
and `0x00419D38` — followed by
`[win32hle] fatal signal 10 at 0x7001000003 (guest VA 0x01000003) in lifted
function 0x00419D38`. In the generated C, `sub_00419D38` dereferences a
pointer chain (`eax = MEM32(eax+0x2C); ecx = MEM32(eax)`) and calls
`MEM32(ecx+0x2C)` with a NULL vtable. No frames are produced. This is a
different, still-unfixed manifestation, so acceptance is not met. Status:
**FAIL**; OPEN.

### #28 Nineteen unresolved imports (Tetris, Chocolate Doom)
All 19 named shims are registered in the current tree. Two natives were
rebuilt from the originals with the current runtime:
- `tetris-win-32-en.exe` -> 0 unresolved imports; 3 proven non-blank frames (PARTIAL).
- `chocolate-doom.exe` -> 166 imports bound, **0 unresolved**; no frame because
  a legally supplied WAD is absent (FAIL/BLOCKED for frame evidence).

The shim-semantics selftests and `docs/NEW_GAME_DOWNLOADS.md` update required by
the acceptance criteria are not yet complete, and Chocolate Doom frame evidence
needs an oracle WAD. Status: **PARTIAL**; OPEN.

## Why nothing was closed

Closing any of these issues now would require claiming one of: a Windows
oracle comparison, gameplay equivalence, a legally supplied proprietary game,
a working Linux runner, or full PE32+/Win16/D3D11 support. None of those
exists here. Every issue therefore stays OPEN with the evidence above, and
this document is the single place to re-check the exact commands and results
before a future closure decision.
