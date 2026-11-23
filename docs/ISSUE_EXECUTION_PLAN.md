# Issue execution plan (verified session state)

This tracker records only work that has been built, tested, committed and pushed
in this session. No completion percentage is estimated. GitHub issues are not
closed unless every acceptance criterion is met; currently none have been closed
by this session.

Statuses: NOT_STARTED, IN_PROGRESS, IMPLEMENTED, BUILD_VERIFIED, TEST_VERIFIED,
INTEGRATION_VERIFIED, COMPLETED, BLOCKED, DEFERRED.

| # | Title | Status | Owner / branch | Verified commit | Build / test evidence | GitHub issue |
|---|---|---|---|---|---|---|
| 1 | Master recompilation pipeline | IN_PROGRESS | `feature/upstream-issue-1` | `808863ba73161755a188d79325a2643cebf84b28` | high-level pointer-return fix; 172 passed, 2 skipped | OPEN |
| 2 | IGI / D3D7 / DirectInput | IN_PROGRESS | `feature/upstream-issue-2` | `46dc1ce` | D3D7 FVF/PSIZE fix; 14 selftests ok; 168 passed, 2 skipped | OPEN |
| 3 | D3D8 + replay | IN_PROGRESS | `feature/upstream-issue-3` | `5b3523d4d059b0adab2463d167e1ba887cea9211` | D3D8 COM frontend; 2 targeted + 172 full tests passed | OPEN |
| 4 | Provider benchmarking | NOT_STARTED | — | — | no verified work | OPEN |
| 5 | D3D9 / DirectInput8 | IN_PROGRESS (audit only) | `feature/p10-d3d9-audit`, `feature/p10-dinput8-audit` | `3c81be6`, `4df88bc` | upstream audits pushed; no runtime implementation | OPEN |
| 6 | Legacy D3D / DirectDraw / Glide | IN_PROGRESS | `feature/upstream-issue-6` | `6fb3bb44…`, `31dbf96b…` | IDirect3D legacy interface; 174 passed, 2 skipped; 14 selftests ok | OPEN |
| 7 | D3D9Ex / DXGI / D3D10–11 | NOT_STARTED | — | — | no verified work | OPEN |
| 8 | Win16 / NE | DEFERRED | — | — | roadmap requires x86 PE32 macOS+Linux first; parser dispatch fix on `feature/upstream-issue-8` (`c75c7df1…`) is independent groundwork | OPEN |
| 9 | PE32 loader / DLL / TLS / SEH / CRT | IN_PROGRESS | `feature/agent1-pe32-loader`, `feature/upstream-issue-9`, `feature/agent-26-seh` | `ee6e966591956e3bc5d3df4630ce89b911d0d633`, `d223d8de…`, `90db86e1…` | ordinal/forwarded exports, TLS callbacks, relocation bounds, SEH; 15 selftests ok, 168 passed | OPEN |
| 10 | Audio / video | IN_PROGRESS | `feature/upstream-issue-10` | `9c37a9e5cff4231032c5b78d0e59a78a22a6d643` | real waveOut PCM path; 15 selftests ok, 170 passed | OPEN |
| 11 | Controllers / input | IN_PROGRESS | `feature/upstream-issue-11`, `feature/p10-igi-input`, `feature/dinput8-abi-fix` | `8cbc16e`, `460d631`, `c31d82f`, `8f9ef78` | DInput error codes + DIMOUSESTATE2 fixed; synthetic input fixture; selftests ok | OPEN |
| 12 | Networking | IN_PROGRESS | `feature/agent2-runtime`, `feature/upstream-issue-12` | `52674039798a33aa2aa992956024d99bf111c245`, `4276c1a1…` | blocking recv/accept + loopback fixture; 15 selftests ok, 168 passed, 8 fast-tier checks pass | OPEN |
| 13 | PE32+ / x64 | DEFERRED | — | — | roadmap prerequisite not met; no production x64 work | OPEN |
| 14 | Compatibility matrix / CI | IN_PROGRESS | `feature/ci-release-gate`, `feature/p10-matrix-tests` | `7015f401…`, `8529d6c` | CI matrix + evidence gate; matrix tests 5 passed/26 subtests; 175 full tests passed | OPEN |
| 15 | Project IGI | PARTIALLY VERIFIED | `feature/agent3-game-acceptance` | `0d4fbbd92963d39953d765f0ff0d4e4cc58c05e2` | native build+run, main menu screenshot with 3,713 colours; input/audio/gameplay not fully tested | OPEN |
| 16 | Hitman Codename 47 | BLOCKED | `feature/p10-hitman-research` | `90a80cee…` | upstream audit pushed; no original Windows files locally | OPEN |
| 17 | Max Payne | BLOCKED | `feature/p10-maxpayne-research` | `48d9511…` | upstream audit pushed; no original Windows files locally | OPEN |
| 18 | Max Payne 2 | BLOCKED | same as #17 | pending agent | no original Windows files locally | OPEN |
| 19 | Hitman 2 | BLOCKED | same as #16 | pending agent | no original Windows files locally | OPEN |
| 20 | Hitman Contracts | BLOCKED | same as #16 | pending agent | no original Windows files locally | OPEN |
| 21 | Hitman Blood Money | BLOCKED | same as #16 | pending agent | no original Windows files locally | OPEN |
| 22 | TrackMania Nations ESWC | FAIL (native execution) | `feature/p10-tmnf-eswc` | `48b85c41…` | native build PASS after disasm guard; strict boot exit 2 (64 unresolved), permissive SIGBUS; no frames; evidence in `docs/GAME_TM_ESWC_EVIDENCE.md` | OPEN |
| 23 | TrackMania Nations Forever | IN_PROGRESS | `feature/agent3-game-acceptance` | pending build | Windows PE32 binary found, SHA-256 recorded; build running in background | OPEN |
| 24 | TrackMania United Forever | BLOCKED | — | — | no original Windows files locally | OPEN |
| 25 | Multi-lifter fallback | IN_PROGRESS | `feature/upstream-issue-25` | `0ee2f988df5ba9cff5498cc61df7974cbf40db4e` | opt-in selector; 18 targeted + 188 full tests passed | OPEN |

## Verified common gates (latest)

- `bash runtime/win32hle/build_selftests.sh` → 15/15 ok on `feature/agent2-runtime`.
- `python3 -m pytest tests/ -q` → 168 passed, 2 skipped (varies slightly by branch with added tests).
- `verify.sh --fast` on `feature/ci-release-gate` → 186 passed, 2 skipped, 7 checks PASS, 1 SKIP.

## Integration checkpoints

1. All agent branches must be built and tested before merge.
2. Shared files (`host.c`, `kernel32*.c`, `win32hle.h`, `screen.c`, `dinput.c`,
   `dsound.c`) have single owners per agent; conflicts are resolved by the
   coordinator, not by overwriting.
3. Game acceptance evidence is stored in gitignored `test-results/<game>/`;
   only summaries and hashes in docs are committed.
4. #8 and #13 remain DEFERRED until x86 PE32 macOS and Linux acceptance passes.

## Coordinator checkpoint (latest)

- GUI repair branch `feature/gui-repair` at `cfaaf36b2ddadfea1324ccf42c866965c3a99a7b`: `cargo test --release` PASS, `cmake --build gui/build` PASS, `verify.sh --fast` PASS (6 checks, 1 skip), `regress --tier fast` 8 checks PASS. Build artifacts removed.
- Upstream provenance consolidation `feature/p10-provenance-docs` at `768fb9c1883f359bb6ba3d9f9580d1f6301f6a0a`.
- OpenAL audit `feature/p10-openal-audit` at `2d50432…`, Bink audit `feature/p10-bink-audit` at `91a3bc6…`.
- All listed commits were verified against `git ls-remote` by their owners.

## Integration checkpoint 2 (`feature/integration-checkpoint`)

- Branch pushed and verified at `8f9ef7827ec69a01b99360c47e45ab534e1e2974`.
- Merged: agent2 runtime, matrix tests, provenance/audit docs, mmioOpenW, DInput constants.
- `python3 -m pytest tests/ -q` → 173 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 15 selftests ok.
- GUI repair `feature/gui-repair` at `cfaaf36b…`: cargo PASS, cmake PASS,
  `verify.sh --fast` PASS, `regress --tier fast` 8 checks PASS.
- mmioOpenW `feature/mmio-openw` at `23aad3c…`: real on-disk RIFF fixture.
- No issue was closed: remaining acceptance criteria (live gameplay per game,
  Linux target, GUI agent integration) are not yet verified.

## Integration checkpoint 3 (`feature/integration-checkpoint`)

- Branch pushed and verified at `da1e0e46e5dc1a66b9e37ae2a2f2cc4c74a65553`.
- Engine branch `feature/engine-lifter` at `da1e0e4` implements CPUID on
  non-MSVC hosts with a register/mask selftest (`cpuid_selftest.c`).
- `python3 -m pytest tests/ -q` → 173 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 18 selftests ok.
- Unsupported instruction scan on IGI/Cave Story/Noiz2sa found 16 rare
  mnemonics (das, iretd, into, bound, aad, arpl, aas, int, salc, daa, insb,
  insd, fldenv, aaa, sti, lcall); most are in data regions, and none are
  claimed as supported. They remain on the unsupported list.
- Still no issue closure: game acceptance, Linux target and original-Windows
  oracle evidence remain incomplete.

## Integration checkpoint 4 (`feature/integration-checkpoint`)

- Branch pushed and verified at `a3a054cdf9706611f028065de13eee8e54405800`.
- `feature/api-coverage` (`3885dab`, `a3a054c`): machine-readable PE32 import
  auditor keyed by input SHA-256 (`tools/api_coverage.py`), 4 auditor tests,
  `docs/WIN32_API_COVERAGE.md`, implemented `GetFullPathNameW` and
  `InitializeCriticalSectionEx` with guest ABI selftests.
- `python3 -m pytest tests/ -q` → 177 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 20 selftests ok.
- Auditor on 19 game PE32s found 322 missing distinct imports overall (mostly
  game-owned DLLs/C++ runtime); 22 are Win32 system APIs, listed in the report.
- No issue closed: behavioural correctness and game acceptance remain open.

## Integration checkpoint 5 (`feature/integration-checkpoint`)

- Branch pushed and verified at `51731c28984afb0198c82e0ca75c3327e7f7767d`.
- Threading branch `feature/threading-stress` (`51731c2`) merged agent 27
  (thread/sync semantics) and agent 33 (waitable timers), and adds a 200-thread
  mutex-contention fixture: 200 threads x 40 increments = 8000 exactly, with
  correct exit codes, five consecutive deterministic runs.
- Architecture: guest threads are host pthreads serialised by the win32hle
  machine lock; waits use `hle_block_begin`/`hle_block_end`; handles are typed
  guest-facing integers, never host pthread structures.
- `python3 -m pytest tests/ -q` → 177 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 23 selftests ok.
- No game-level thread-safety claim: no original-game deterministic scenario
  was executed; Linux host testing remains unavailable.

## Integration checkpoint 6 (`feature/integration-checkpoint`)

- Branch pushed and verified at `f73f7b474f16d64c4c81d2ba8bf8fe84836da592`.
- `feature/fsreg-compat` (`f73f7b4`) merged the existing resource-bounds work
  from `feature/agent-32-resources` and adds `RegOpenKeyExW`, `RegEnumValueA/W`
  over the isolated persistent registry store with a real guest ABI selftest.
- The selftest caught and fixed an argument-index bug (type slot vs reserved slot).
- `python3 -m pytest tests/ -q` → 177 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 25 selftests ok.
- Linux host results remain UNTESTABLE here; no game-level registry/resource
  scenario was executed, so no issue was closed.

## Integration checkpoint 7 (`feature/integration-checkpoint`)

- Branch pushed and verified at `ab007b2c41929454b2e90d5e2bce62ec2b82642b`.
- `feature/com-abi` (`c12a339`): real per-thread COM initialisation state
  (S_OK/S_FALSE/RPC_E_CHANGED_MODE, NULL reserved, balanced uninitialise),
  with `com_init_selftest`; merged with the earlier shared IID/interface work.
- `feature/p10-tmnf-eswc` (`a909798`, `48b85c4`): disasm unmappable-extent guard
  and honest FAIL evidence for TrackMania Nations ESWC.
- `python3 -m pytest tests/ -q` → 177 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 27 selftests ok.
- No issue closed; COM functionality is ABI/lifecycle level only, and TM ESWC
  execution genuinely failed.

## Integration checkpoint 8 (`feature/integration-checkpoint`)

- Branch pushed and verified at `3b4213f580da9bba1bcc1b5ecb2bdcea2ffbd63b`.
- `feature/d3d9-frontend` (`3b4213f`): guest-facing D3D9 COM frontend with
  `Direct3DCreate9`, full IDirect3D9 (17) and IDirect3DDevice9 (119) vtable
  order from Wine's official `include/d3d9.h` (commit `97027f87`), real
  reference counting, IID identity checks, and honest `D3DERR_NOTAVAILABLE`
  for all rasteriser methods. Adapter/display queries use the shared display
  mode. `d3d9_selftest` checks every vtable slot and object lifecycle.
- Provenance recorded in `docs/D3D9_FRONTEND_REUSE.md`; no Wine code copied.
- `python3 -m pytest tests/ -q` with real GBX fixtures → 183 passed, 2 skipped,
  36 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 28 selftests ok.
- No renderer and no game-level D3D9 validation: rendering remains NOT IMPLEMENTED;
  no issue closed.

## Game matrix correction — TrackMania (2026-11-23)

- Rows 22 and 23 were previously marked `BLOCKED: no Windows game files`.
  Real PE32 executables were located and hashed locally, so that blocker is
  stale for those two rows. Row 24 (TM United Forever) remains BLOCKED.
- TM Nations ESWC (`822def66…`): native build PASS; execution FAIL (strict exit
  2 with 64 unresolved imports, permissive SIGBUS, no frames). Evidence kept on
  `feature/p10-tmnf-eswc` in `docs/GAME_TM_ESWC_EVIDENCE.md`.
- TM Nations Forever (`5e287e79…`): `tools.autorecomp --no-run` completed all
  stages: 128,726 functions, 0 lift errors, native 741,976,264-byte build.
  `verification.json` is `not-run`; only A0–A3 are claimed. Evidence in
  `docs/GAME_TMNF_EVIDENCE.md`.
- No acceptance level above A3 was assigned and no issue was closed: no
  launch, frame, input, audio, save/load or original-versus-native comparison
  was performed.

## Integration checkpoint 9 — 20-agent swarm (`feature/integration-checkpoint`)

- Branch pushed and verified at `bc4dae8c8eaffe5944d4a88e5b4e188c64f2a97b`;
  same commit on `feature/game-matrix-all`.
- Twenty independent agents, each in its own worktree from base `5536865`,
  with exclusive file ownership; all 20 branches pushed, reviewed and merged
  with zero conflicts. Concrete outcomes:
  - Registry `RegEnumKeyExW` (A2), COM `CoTaskMemRealloc` (A3), real Winsock
    DNS `gethostname/gethostbyname/gethostbyaddr` (A4), `DirectInput8Create`
    + full DI8 vtables (A5), DirectSound enumeration (A6), Bink header
    diagnostics + `_BinkSetSoundOnOff` (A7), `SetFileAttributesA/W` (A8),
    MSVC throw diagnostics (A9), `RtlCaptureContext` + honest unwind stubs
    (A10), Unicode user32 messages (A11), `CreateBitmapIndirect` (A12),
    `DirectDrawCreateClipper` (A13), D3D7 `PreLoad` with real GL tex upload
    (A14), D3D8 corrected `IID_IDirect3DDevice8` (A15), auditor accuracy and
    system/game-owned split (A16), matrix disk guard + resource capture
    (A17), `SHFileOperationA` (A18), `sndPlaySoundA/W` with corrected BOOL ABI
    (A19), acceptance ledger + schema (A20).
  - A1 diagnosed the Gunroar DMD exception precisely (lifter missing EH
    landing pad for a tail-call-inlined catch) and added a runtime fallback;
    the game still faults later with unresolved VA 0 / SIGBUS, so #27 stays OPEN.
- New issues: #26 (multi-GB generated C exhausts disk/memory) and #27
  (Gunroar DMD exception). Both have measured evidence and pushed work.
- Build scale: `tools/clean_generated.py` reclaimed ~7 GB; `report.json` now
  records measured generated/binary/disk/RSS numbers; matrix runner enforces
  a 4 GiB free-disk guard.
- Execution evidence: `tools/game_runner` ran 3 games one at a time from their
  own folders; Cave Story (2 frames, 640x480) and Ikachan (3 frames, 351x238)
  rendered; Gunroar hit the DMD exception. Remaining games BLOCKED (no native
  binary after SIGKILLed builds). Linux NOT TESTED; x64 explicitly unsupported.
- `python3 -m pytest tests/ -q` → 213 passed, 5 skipped, 5 subtests passed.
  `bash runtime/win32hle/build_selftests.sh` → 44 selftests ok, 0 FAIL.
- **All 27 issues remain OPEN.** No issue was closed; no 100% claim was made.

## Integration checkpoint 10 — downloaded games and issue #28 (`feature/integration-checkpoint`)

- Branch pushed and verified at `71a5d450a144371ade4b1cfdc33c6b0dc316a884`.
- Two legally redistributable PE32 games downloaded, hashed and rebuilt: OUCC
  Tetris 1.3 x86 (`024dccd5…`) and Chocolate Doom 1.3.0 win32 (`d1b9dabc…`).
  No game binary or asset committed.
- Issue #28 was filed with 19 measured missing imports from those games. All
  19 now resolve: SDL string/error/driver/cursor helpers, MessageBoxW,
  LoadLibraryW, LoadCursorW, DrawTextW, CreatePatternBrush, CreateIconIndirect,
  __initenv, Mix_SetPostMix and four MSVCRT symbols. `api_gap_selftest` covers
  them; documented limits (fscanf ENOSYS, post-mix callback not invoked from
  the audio thread, no pattern rasterisation) are explicit, not fake success.
- Final execution: Tetris 97 imports / 0 unresolved / **3 frames 310×418**;
  Chocolate Doom 166+34 imports / 0 unresolved / 0 frames because no DOOM WAD
  is supplied. Neither is at A6+ gameplay acceptance.
- GUI: deterministic selftest modes pass for all 15 game PE32 inputs; 2 project
  models pass, 1 fails closed on a missing report. Real mouse clicks are
  BLOCKED by macOS assistive access (-25211) and screencapture is blocked.
- Full suite: pytest 216 passed / 8 skipped / 24 subtests on the feature
  branch; 45 win32hle selftests ok, 0 FAIL. Integration: 213 passed / 11 skipped.
- New issues: #28 (API gaps, now import-complete but Doom frame evidence needs
  a legal WAD), #26 build scale, #27 DMD exception. **No issue closed.**

## Integration checkpoint 11 — 10-agent swarm and proof evidence

- `feature/integration-checkpoint` pushed/verified at `d19c82a226138f9d36a69346ff5cbb8af5cab6a8`.
- Ten agents (A21–A30) merged with zero conflicts, each from base `f5c55e5` with exclusive file ownership:
  - A21: NULL vtable dispatch no longer clobbers EAX and cascades into SIGBUS; precise diagnostics (root cause of Gunroar/Tumiki/Parsec47 crash cascade).
  - A22: COMCTL32 FlatSB shims for HeroCore; A23: DirectDraw7 DDCAPS offset fix for Warning Forever.
  - A24 + A29: common frame sink and SDL present wiring; Noiz2sa went from **0 frames to 3 non-blank frames (27/36/30 colours)** in a real rebuilt run.
  - A25: measured source-size budget and diagnostics; A26: locale Ex APIs; A27: SList/Rtl extras; A28: wide-string CRT exports; A30: regenerated API coverage (2,249 shims, 20 system / 293 game-owned missing).
- Proof tooling: `tools/frame_proof` decodes BMP frames and flags blank frames. It corrected the earlier Ikachan claim: all Ikachan frames are single-colour, including a 20s/10-frame capture. Only Cave Story (2/3), Noiz2sa (3/3) and Tetris (3/3) have non-blank frames, with 3–36 distinct colours. Gallery: `docs/PROOF_EVIDENCE.md` (`test-results/game-matrix/frame-proof/native-frames.html`).
- Unicorn: original bytes vs generated C agree for Tetris `0x00406370` over 4 cases (registers + named guest memory); five other candidates were faulted/refused, not counted as passes. No whole-game or Windows side-by-side oracle exists; documented explicitly.
- Verification: feature suite 229 passed / 8 skipped / 34 subtests; integration 226 passed / 11 skipped; **47 selftests ok, 0 FAIL**.
- All issues remain OPEN; no 100% or gameplay claim.

## Integration checkpoint 12 — CUA GUI/CLI tests and 10-agent doc/tool swarm

- `feature/integration-checkpoint` and `feature/game-matrix-all` pushed/verified
  at `a7833f99d9050a1893fd21a32f6cbe1a15cec5bd`.
- Real GUI interaction with the installed CUA driver (Accessibility and Screen
  Recording true under com.trycua.driver): `tools/gui_cua_test.py` performed
  **9/9 mouse clicks** (6 pixel tabs, Run Options and Open Agents AX buttons,
  sidebar Settings) with before/after screenshot SHA-256 proof; three JPEG
  screenshots committed under `docs/evidence/gui-cua/`. Destructive controls
  excluded and unit-tested. Evidence: `docs/GUI_CUA_EVIDENCE.md`.
- `tools/cli_matrix.py`: 16 CLI commands (version/about/setup/tasks/status and
  `inspect --json` over 11 PE32 files) all exit 0; header checks only.
- Ten agents (B1–B10) merged with zero conflicts: issue work ledger for all
  28 issues; 3 new legally redistributable PE32 games (PrBoom+, dhewm3,
  OpenTTD win32 with hashes); x64 OpenTTD refusal evidence; legacy-software
  legality report + 7-Zip/Notepad++/PuTTY PE checks; rev.ng/AI-assist probes;
  size-tier screenshot report; fail-closed multi-lifter selector; x64 gate;
  acceptance-doc guardrails.
- Also added `tools/app_compat_probe`: PE32 x86 native is only a
  SUPPORTED_CANDIDATE; PE32+/x64, .NET, Win16/NE and non-PE are explicit
  UNSUPPORTED classes. No "100% of Windows apps" claim.
- Verification: feature suite **328 passed, 8 skipped, 55 subtests**;
  integration **325 passed, 11 skipped**; **52 win32hle selftests ok**.
- All issues remain OPEN; no 100% or game-acceptance claim.

## Integration checkpoint 13 — AI readability E2E and app recompiles

- `feature/integration-checkpoint` and `feature/game-matrix-all` pushed/verified
  at `36d89200738fc64007ca9c7a0dc5bd7579e9b471`.
- Optional AI readability provider: `tools/ai_openrouter_llm.py` uses an API key
  only via `GAMERECOMP_AI_KEY` (never printed/logged/committed). Three unit tests.
- Real end-to-end validation on a tiny CMake fixture with a tested baseline: two
  consecutive CLI runs `outcome: complete` (rebuilt + process evidence matched);
  a GUI-driven run also `outcome: complete`. Evidence `docs/AI_READABILITY_E2E.md`.
- Defect fixed: accepted AI edits previously left the manifest digest stale,
  blocking the next run; `_update_manifest_digest()` now records it, with a test.
- Rust build fix (`ino() as u128`) allowed CLI and GUI binaries to be rebuilt
  from this worktree.
- New app recompiles: PrBoom+ (9,566 functions, 33 MB binary) and 7-Zip
  (272 functions, 3.3 MB binary) reached A0-A3; PuTTY hit the 900 s build
  timeout with 427 MB generated C; Notepad++/dhewm3/OpenTTD still header-only.
  Evidence `docs/RECOMPILE_NEW_APPS.md`; `tools/recompile_ledger` distinguishes
  native binaries from header-only checks.
- Verification: feature suite **335 passed, 8 skipped, 55 subtests**;
  integration **332 passed, 11 skipped**; **52 selftests ok**.
- All issues remain OPEN; no game/generic-app acceptance or 100% claim.
