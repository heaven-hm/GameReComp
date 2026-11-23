# Issue work ledger (#1–#28): work done, tests, remaining acceptance gates

This ledger is a **status readout, not a completion claim**. Every issue
`#1`–`#28` is **OPEN** on GitHub; none was closed by this work, and none is
recorded here as complete, merged, or accepted. Evidence below is copied from
the committed docs and from the read-only `gh` query recorded in
"Verification". A build, a unit test, a boot, or a single frame is never an
issue-closure event.

## Provenance and verification inputs

| Field | Value |
|---|---|
| Repo / remote | `heaven-hm/GameReComp` (worktree created at the task-supplied base commit) |
| GitHub query (read-only) | `gh issue list --state all --limit 40` and `gh issue list --state all --limit 40 --json number,title,state,stateReason,closedAt` |
| Verification time (UTC) | `2026-11-23T06:58:30Z` |
| GitHub state observed | **28 rows (#1–#28), all `OPEN`, `closedAt: null` for every row, zero closures** |
| Source docs | `docs/ISSUE_EXECUTION_PLAN.md`, `docs/ACCEPTANCE_LEDGER.md`, `docs/ISSUES_AUDIT.md`, `docs/GAME_MATRIX.md`, `docs/GAME_MATRIX_METHODOLOGY.md`, `docs/GAME_ACCEPTANCE_TRACKER.md`, `docs/GAME_EXECUTION_EVIDENCE.md`, `docs/ALL_GAMES_MACOS_EVIDENCE.md`, `docs/PROOF_EVIDENCE.md`, `docs/GAME_TM_ESWC_EVIDENCE.md`, `docs/NEW_GAME_DOWNLOADS.md`, `docs/CAPABILITY_BOUNDARIES.md`, `docs/MULTIARCH_RUN_ATTEMPT.md`, `docs/DISPLAY_SIZING_EVIDENCE.md`, `docs/VERIFICATION.md` |
| Upstream ledger base (doc-sourced) | `5536865a7c93a691947e4c84eaa484ac5d736cd8` — recorded in `docs/ACCEPTANCE_LEDGER.md` as the unmodified source base for the 25-row ledger |
| Scope note | `#26`, `#27`, `#28` were opened after the 25-row execution plan; they are out of that plan's scope but are in scope for this ledger |

## Classification and gate vocabulary

- **WORKED+TESTED** — concrete implementation/docs committed with build and/or
  unit-test evidence (A3* only); no game-level oracle. The `*` means
  build/unit/CI evidence, **not** a game acceptance result.
- **PARTIAL** — some runtime/menu/frame evidence recorded (A4–A6), or an
  audit-only result explicitly marked as such; still no full scenario oracle.
- **BLOCKED** — an external prerequisite is absent (original Windows files,
  legal WAD, Linux runner) or the documented run reached a measured FAIL.
- **DEFERRED** — roadmap-gated by design: **#8** and **#13** only.
- **NOT_STARTED** — no verified work or committed evidence exists.

Gates A0–A10 (from `docs/GAME_MATRIX_METHODOLOGY.md` and
`docs/ACCEPTANCE_LEDGER.md`): A0 inspect, A1 recover, A2 translate+project,
A3 compile, A4 process runtime, A5 menu, A6 graphics/frames, A7 input, A8
audio, A9 save/load, A10 original-vs-native differential. A0–A3 are
stage/build results; A4 is process-level only; A5–A10 require independent
scenario evidence. `PARTIAL`, `NOT TESTED`, `BLOCKED` and `UNTESTABLE` are
never `PASS`.

## Summary table (machine-scannable)

Commit strings quoted here are **only** those already present in the committed
docs (see "Verification"). `#26`–`#28` cite docs only because no commit hash for
them is quoted in the source docs.

| # | GitHub state | Class | Quoted commit(s) (doc-sourced) | Evidence docs | Highest gate | Concrete work + what was tested | Exact remaining acceptance gate |
|---|---|---|---|---|---|---|---|
| 1 | OPEN | WORKED+TESTED | `808863ba73161755a188d79325a2643cebf84b28` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER | A3* | High-level pointer-return fix; `pytest tests/ -q` 172 passed, 2 skipped | A4–A10: no end-to-end verified native game run under the master pipeline; unit tests are not an oracle |
| 2 | OPEN | WORKED+TESTED | `46dc1ce` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; UPSTREAM_CODE_PROVENANCE | A3* | D3D7 FVF/PSIZE fix from igi-recomp `278eaf63…`; 14 C selftests ok, 168 passed, 2 skipped | A6/A7 in-game: D3D7 graphics and DirectInput not verified in actual gameplay (IGI input NOT TESTED) |
| 3 | OPEN | WORKED+TESTED | `5b3523d4d059b0adab2463d167e1ba887cea9211` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER | A3* | D3D8 guest COM frontend + provenance/regression tests; 2 targeted + 172 full tests passed | A4–A10: replay/verification reuse not demonstrated against a real D3D8 game |
| 4 | OPEN | NOT_STARTED | — | ISSUES_AUDIT; ISSUE_EXECUTION_PLAN | none | No verified work; audit notes `revng.py`/`hlverify.py`/`highlevel.py` as foundation only | A0: formal Anvill/Rellic/Omill-vs-rev.ng benchmark report with committed results |
| 5 | OPEN | PARTIAL (audit only) | `3c81be6`, `4df88bc` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; D3D9/DINPUT8_UPSTREAM_AUDIT | none (audit) | Upstream D3D9/DirectInput8 audits pushed; no runtime implementation | A0/A3: no `d3d9.dll`/`dinput8.dll` runtime build or run evidence; audits are documentation |
| 6 | OPEN | WORKED+TESTED | `6fb3bb44…`, `31dbf96b…` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER | A3* | IDirect3D legacy interface + DirectDrawCreateClipper shim work; 174 passed, 2 skipped; 14 selftests ok | A4–A10: no in-game verification for legacy D3D 1–6 / DirectDraw / Glide |
| 7 | OPEN | NOT_STARTED | — | ISSUES_AUDIT; ISSUE_EXECUTION_PLAN | none | No verified work; `d3d9ex`/`d3d10`/`d3d11`/`dxgi`/`d3dx` absent from `runtime/win32hle/` | A0: no implementation, build or run evidence for post-D3D9 graphics |
| 8 | OPEN | DEFERRED | `c75c7df1…` (independent groundwork) | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; ISSUES_AUDIT | none | Standalone Win16 NE tooling exists (`tools/ne/`, `runtime/win16/`); parser dispatch fix is independent groundwork, not wired to `tools/autorecomp/pipeline.py` | A0 + roadmap gate: deferred until x86 PE32 macOS **and Linux** acceptance passes; NE pipeline not wired or end-to-end tested |
| 9 | OPEN | WORKED+TESTED | `ee6e966591956e3bc5d3df4630ce89b911d0d633`, `d223d8de…`, `90db86e1…` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; ISSUES_AUDIT | A3* | PE32 loader/module exports, TLS callbacks, relocation bounds, SEH/EH work; 15 selftests ok, 168 passed | A4–A10: arbitrary dynamic `LoadLibrary` DLL lifting, guest SEH unwinding and MSVC/D exceptions not verified end-to-end |
| 10 | OPEN | WORKED+TESTED | `9c37a9e5cff4231032c5b78d0e59a78a22a6d643` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER | A3* | Real waveOut PCM path; 15 selftests ok, 170 passed | A8: no in-game audio acceptance; IGI audio is only PARTIAL (dummy device); MCI/MIDI/DirectShow unverified |
| 11 | OPEN | WORKED+TESTED | `8cbc16e`, `460d631`, `c31d82f` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER | A3* | Wheel/focus fixes plus synthetic input fixture; 15 selftests ok, 170 passed | A7: input not verified in-game with a real device/scenario; IGI input NOT TESTED |
| 12 | OPEN | WORKED+TESTED | `52674039798a33aa2aa992956024d99bf111c245`, `4276c1a1…` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER | A3* | Blocking recv/accept + loopback fixture; 15 selftests ok, 168 passed, 8 fast-tier checks pass | A4/A9: deterministic verification in a real multiplayer game with an oracle not demonstrated (loopback fixture only); Winsock 2/DirectPlay absent |
| 13 | OPEN | DEFERRED | — | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; CAPABILITY_BOUNDARIES; MULTIARCH_RUN_ATTEMPT | none | PE32+ detection fail-closed; `tests/test_capability_boundaries.py` 3/3 proves x64 and Linux refusals; no x64 loader/lifter/ABI added | A0 + roadmap/Omill-vs-PCRecomp feasibility gate: deferred until x86 PE32 macOS and Linux acceptance passes; PE32+ games NOT RUN |
| 14 | OPEN | WORKED+TESTED | `7015f401…`, `8529d6c` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; GAME_MATRIX_METHODOLOGY | A3* | CI matrix + evidence gate, API coverage auditor, one-at-a-time runner and frame-proof; matrix tests 5 passed/26 subtests, 175 full tests; `verify.sh --fast` 186 passed/2 skipped, 7 checks PASS/1 SKIP | A4–A10 corpus-wide: no green full-corpus matrix and no per-game A5–A10 gating across macOS/Linux |
| 15 | OPEN | PARTIAL | `0d4fbbd92963d39953d765f0ff0d4e4cc58c05e2` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; GAME_MATRIX; GAME_ACCEPTANCE_TRACKER | A6 | Native build+run; 7,325 fns, 128/128 imports; v1.0 main menu 3,713 colours; process launched, exited 1 | A7/A8/A9/A10: input NOT TESTED, audio PARTIAL (dummy), gameplay menu only, save/load + Mission 1 original-vs-native oracle absent |
| 16 | OPEN | BLOCKED | `90a80cee…` (audit only) | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; HITMAN_UPSTREAM_AUDIT | none | Upstream Glacier audit pushed; no original Windows game files locally | A0: owned Windows binary absent; all gates A0–A10 unmet |
| 17 | OPEN | BLOCKED | `48d9511…` (audit only) | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; MAXPAYNE_UPSTREAM_AUDIT | none | Upstream MAX-FX/D3D8 audit pushed; no original Windows game files locally | A0: owned Windows binary absent; all gates A0–A10 unmet |
| 18 | OPEN | BLOCKED | — (plan: pending agent) | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER | none | No game-specific work; depends on #3/#9/#17 | A0: owned Windows binary absent; all gates A0–A10 unmet |
| 19 | OPEN | BLOCKED | — (plan: pending agent) | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER | none | No game-specific work; depends on #3/#9/#16 | A0: owned Windows binary absent; all gates A0–A10 unmet |
| 20 | OPEN | BLOCKED | — (plan: pending agent) | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER | none | No game-specific work; depends on #3/#9/#16 | A0: owned Windows binary absent; all gates A0–A10 unmet |
| 21 | OPEN | BLOCKED | — (plan: pending agent) | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER | none | No game-specific work; depends on #5/#9/#16 | A0: owned Windows binary absent; all gates A0–A10 unmet |
| 22 | OPEN | PARTIAL | — (plan: pending agent; no hash quoted) | ACCEPTANCE_LEDGER; GAME_TM_ESWC_EVIDENCE; GAME_MATRIX (conflict) | A3 (build) | PE32 identity SHA-256 `822def66…`, 259 imports/15 DLLs; one-line `disasm32` guard → 85,216 fns, 1.13 GB C, 0 lift errors, arm64 Mach-O linked; strict boot 195 bound/64 unresolved, permissive SIGBUS at guest VA `0x7E00003C`, 0 frames over 5 runs | A4/A6/A10: boot without 64 unresolved imports (notably `Direct3DCreate9`, `DirectInput8Create`, dsound ordinal 12), diagnose `0xACA0B0` indirect call, then reach a frame and a race oracle |
| 23 | OPEN | BLOCKED (plan conflict) | — (plan: pending build; no hash quoted) | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; GAME_MATRIX | A0/A1 at most | Plan says PE32 binary found/SHA-256 recorded; `docs/GAME_MATRIX.md` and ledger record BLOCKED (no Windows game files, only a native macOS app) — conflict preserved, no build claimed | A3 then A4–A10: compiled native binary plus deterministic race replay; `TmForever.map` ingestion and D3D9/DInput8 not demonstrated |
| 24 | OPEN | BLOCKED | — | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; GAME_MATRIX | none | No game-specific work; only a matrix-row correction is referenced in history, not a game build | A0: distinct owned Windows binary absent; all gates A0–A10 unmet |
| 25 | OPEN | WORKED+TESTED | `0ee2f988df5ba9cff5498cc61df7974cbf40db4e` | ISSUE_EXECUTION_PLAN; ACCEPTANCE_LEDGER; ISSUES_AUDIT | A3* | Opt-in per-function multi-lifter selector + certification foundation (18 certified Cave Story fns); 18 targeted + 188 full tests passed | A4–A10: no game-level differential evidence end-to-end; selector is opt-in and not exercised in a verified game run |
| 26 | OPEN | WORKED+TESTED (mitigation) | — (no commit hash in source docs) | MULTIARCH_RUN_ATTEMPT; CAPABILITY_BOUNDARIES; ACCEPTANCE_LEDGER; ALL_GAMES_MACOS_EVIDENCE | A3* | Measured 137/SIGKILL during parallel generated-C builds; ikachan 10.28 MB C, 3.03 MB binary, 20.97 MB project, 144 MB peak RSS; resource/disk guard + fail-safe cleanup; `tools.game_matrix --parallel 1 --jobs 1` and capability tests 3/3 | A3 full-corpus rebuild with multi-GB C trees under host memory/disk budget; then A4–A10 runs — remaining games still have no native binary |
| 27 | OPEN | PARTIAL | `5536865a7c93a691947e4c84eaa484ac5d736cd8` (doc-sourced base; precise DMD-throw diagnostic) | GAME_EXECUTION_EVIDENCE; ALL_GAMES_MACOS_EVIDENCE; CAPABILITY_BOUNDARIES; ACCEPTANCE_LEDGER | A4 (boot only) | Gunroar rebuild: 136 imports bound, 0 unresolved, boots to entry `0x0042D49C`; `RaiseException(0xE0440001)` reported unhandled; later SIGBUS at indirect-call VAs; DMD catch-frame dispatch groundwork | A6/A10: dispatch DMD catch frame with no EH checkpoint so Gunroar reaches a rendered frame and a scenario diff; latest runs still 0 frames, verdict FAIL |
| 28 | OPEN | PARTIAL | — (no commit hash in source docs) | NEW_GAME_DOWNLOADS; PROOF_EVIDENCE; ALL_GAMES_MACOS_EVIDENCE; DISPLAY_SIZING_EVIDENCE | A6 (Tetris frames) | Closed 19 newly surfaced imports (W-forms, SDL 1.2 helpers, MSVCRT, `Mix_SetPostMix` recorded-not-invoked); Tetris 97 bound/0 unresolved, 3 non-blank 310×418 frames (4/5/5 colours); Chocolate Doom 166+34 bound/0 unresolved, exit 255, no WAD; `3a99d9b` window-size/fullscreen fix verified at 640×480 and 1680×1050 | A5–A10 gameplay oracle for Tetris; legal DOOM WAD fixture for Chocolate Doom; frame/input/audio/save-load comparison — import binding and a few frames are not gameplay |

## Machine-readable ledger

```json
{
  "schema_version": 1,
  "document": "docs/ISSUE_WORK_LEDGER.md",
  "readout_only": true,
  "sources": {
    "plan": "docs/ISSUE_EXECUTION_PLAN.md",
    "upstream_ledger": "docs/ACCEPTANCE_LEDGER.md",
    "upstream_ledger_base_commit": "5536865a7c93a691947e4c84eaa484ac5d736cd8",
    "matrix": "docs/GAME_MATRIX.md",
    "methodology": "docs/GAME_MATRIX_METHODOLOGY.md",
    "proof": "docs/PROOF_EVIDENCE.md"
  },
  "verification": {
    "query": "gh issue list --state all --limit 40",
    "json_query": "gh issue list --state all --limit 40 --json number,title,state,stateReason,closedAt",
    "at_utc": "2026-11-23T06:58:30Z",
    "rows": 28,
    "all_open": true,
    "closure_count": 0,
    "complete_count": 0,
    "deferred": [8, 13]
  },
  "class_counts": {
    "WORKED+TESTED": 11,
    "PARTIAL": 5,
    "BLOCKED": 8,
    "DEFERRED": 2,
    "NOT_STARTED": 2
  },
  "issues": [
    {"number": 1, "title": "MASTER: Autonomous PCRecomp + rev.ng NON-AI-first readable C/C++, optional AI, verified native games & GUI", "github_state": "OPEN", "classification": "WORKED+TESTED", "commits": ["808863ba73161755a188d79325a2643cebf84b28"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md"], "work_done": "High-level pointer-return fix on feature/upstream-issue-1; autonomous PE32->C->CMake->native pipeline, GUI/CLI/WASM core and optional AI readability are in develop per docs/ISSUES_AUDIT.md.", "tested": "pytest tests/ -q: 172 passed, 2 skipped (unit/build only).", "highest_gate": "A3", "unit_only": true, "remaining_gate": "A4-A10: no end-to-end verified native game run under the master pipeline; unit tests are not an oracle.", "complete": false},
    {"number": 2, "title": "Implement IGI-recomp compatibility reuse: Direct3D7->OpenGL, DirectInput->SDL2, Win32 runtime and verified games", "github_state": "OPEN", "classification": "WORKED+TESTED", "commits": ["46dc1ce"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/UPSTREAM_CODE_PROVENANCE.md"], "work_done": "D3D7 FVF stride table adapted from igi-recomp commit 278eaf6394f7f42c7de6cffef2cf982154f59899 with added D3DFVF_PSIZE term; shared d3d7.c/dinput.c/ddraw.c/dsound.c runtime.", "tested": "14 C selftests ok; 168 passed, 2 skipped.", "highest_gate": "A3", "unit_only": true, "remaining_gate": "A6/A7 in-game: D3D7 graphics and DirectInput not verified in actual gameplay (IGI input NOT TESTED).", "complete": false},
    {"number": 3, "title": "Audit Project-IGI-Awesome recomp projects: reuse replay, verification & D3D8 gaps without duplicate runtimes", "github_state": "OPEN", "classification": "WORKED+TESTED", "commits": ["5b3523d4d059b0adab2463d167e1ba887cea9211"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md"], "work_done": "D3D8 minimal guest COM frontend plus provenance and regression tests on feature/upstream-issue-3; trace/fingerprint and divergence tooling exist per ISSUES_AUDIT.", "tested": "2 targeted tests + 172 full tests passed.", "highest_gate": "A3", "unit_only": true, "remaining_gate": "A4-A10: D3D8 replay/verification reuse not demonstrated against a real D3D8 game.", "complete": false},
    {"number": 4, "title": "Evaluate PC decompilers for GameReComp: Anvil overlap, Omill x64, Anvill/Rellic source benchmark—integrate only verified gaps", "github_state": "OPEN", "classification": "NOT_STARTED", "commits": [], "evidence_docs": ["docs/ISSUES_AUDIT.md", "docs/ISSUE_EXECUTION_PLAN.md"], "work_done": "No verified work. ISSUES_AUDIT lists revng.py/hlverify.py/highlevel.py/recomp64_cpu as foundation only.", "tested": "No committed benchmark test result.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0: formal Anvill/Rellic/Omill-vs-rev.ng benchmark report with committed results.", "complete": false},
    {"number": 5, "title": "Reuse real PC game decomp projects WITHOUT duplicating PCRecomp: D3D9/DInput8 feasibility, replay and mod-file HLE audit", "github_state": "OPEN", "classification": "PARTIAL", "commits": ["3c81be6", "4df88bc"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/D3D9_UPSTREAM_AUDIT.md", "docs/DINPUT8_UPSTREAM_AUDIT.md"], "work_done": "Upstream D3D9 and DirectInput8 semantics audits pushed on feature/p10-d3d9-audit and feature/p10-dinput8-audit; no runtime shim implementation.", "tested": "Audit only; no build or run evidence.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0/A3: no d3d9.dll/dinput8.dll runtime build or run evidence; audits are documentation.", "complete": false},
    {"number": 6, "title": "[P2] Legacy Windows 3D: Direct3D 1–6, DirectDraw 7 and 3dfx Glide compatibility without duplicating D3D7 work", "github_state": "OPEN", "classification": "WORKED+TESTED", "commits": ["6fb3bb44", "31dbf96b"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md"], "work_done": "IDirect3D legacy interface and DirectDrawCreateClipper export work on feature/upstream-issue-6; ddraw.c 1-7 surfaces, clippers, palettes per ISSUES_AUDIT.", "tested": "174 passed, 2 skipped; 14 C selftests ok.", "highest_gate": "A3", "unit_only": true, "remaining_gate": "A4-A10: no in-game verification for D3D 1-6 / DirectDraw / 3dfx Glide titles.", "complete": false},
    {"number": 7, "title": "[P3] Post-D3D9 Windows graphics: D3D9Ex, Direct3D 10/11, DXGI, shaders and D3DX dependency gates", "github_state": "OPEN", "classification": "NOT_STARTED", "commits": [], "evidence_docs": ["docs/ISSUES_AUDIT.md", "docs/ISSUE_EXECUTION_PLAN.md"], "work_done": "No verified work; d3d9ex/d3d10/d3d11/dxgi/d3dx are absent from runtime/win32hle.", "tested": "No committed test result.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0: no implementation, build or run evidence for post-D3D9 graphics.", "complete": false},
    {"number": 8, "title": "[P3] Windows 3.x / Win16 NE games: wire existing PCRecomp 16-bit lifter into GameReComp's autonomous pipeline", "github_state": "OPEN", "classification": "DEFERRED", "commits": ["c75c7df1"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/ISSUES_AUDIT.md"], "work_done": "Standalone Win16 NE tooling (tools/ne/, runtime/win16/) inherited from PCRecomp; parser dispatch fix is independent groundwork and not wired into tools/autorecomp/pipeline.py (which fails closed on non-PE32).", "tested": "No end-to-end Win16 game scenario; no committed pipeline run.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0 + roadmap gate: deferred until x86 PE32 macOS and Linux acceptance passes; NE pipeline not wired or end-to-end tested.", "complete": false},
    {"number": 9, "title": "[P1] PE32 multi-module game execution: DLL code lifting, TLS/SEH/C++ EH, CRT and cross-module ABI", "github_state": "OPEN", "classification": "WORKED+TESTED", "commits": ["ee6e966591956e3bc5d3df4630ce89b911d0d633", "d223d8de", "90db86e1"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/ISSUES_AUDIT.md"], "work_done": "Ordinal/forwarded exports, TLS callbacks, relocation bounds, static companion DLL mapping (e.g. bulletml.dll 25 exports), SEH/EH32 C++ throw and CRT work across feature/agent1-pe32-loader, feature/upstream-issue-9, feature/agent-26-seh.", "tested": "15 C selftests ok; 168 passed, 2 skipped.", "highest_gate": "A3", "unit_only": true, "remaining_gate": "A4-A10: arbitrary dynamic LoadLibrary DLL lifting, guest SEH unwinding and MSVC/Borland/D exceptions not verified end-to-end.", "complete": false},
    {"number": 10, "title": "[P2] Legacy Windows game audio/video: DirectSound3D, DirectMusic, WinMM MCI/MIDI, DirectShow, codecs and middleware", "github_state": "OPEN", "classification": "WORKED+TESTED", "commits": ["9c37a9e5cff4231032c5b78d0e59a78a22a6d643"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md"], "work_done": "Real waveOut PCM path on feature/upstream-issue-10; dsound.c/bink.c/mss.c/winmm.c infrastructure per ISSUES_AUDIT.", "tested": "15 C selftests ok; 170 passed, 2 skipped.", "highest_gate": "A3", "unit_only": true, "remaining_gate": "A8: no in-game audio acceptance; IGI audio only PARTIAL (dummy device); MCI/MIDI/DirectSound3D/DirectShow unproven.", "complete": false},
    {"number": 11, "title": "[P2] Game controller and raw input compatibility: DirectInput joystick/FFB, DirectInput8, XInput, HID and Raw Input", "github_state": "OPEN", "classification": "WORKED+TESTED", "commits": ["8cbc16e", "460d631", "c31d82f"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md"], "work_done": "Wheel/focus fixes plus synthetic input fixture; dinput.c keyboard/mouse on SDL2 and a DirectInput8 facade per upstream provenance records.", "tested": "15 C selftests ok; 170 passed, 2 skipped.", "highest_gate": "A3", "unit_only": true, "remaining_gate": "A7: joystick/FFB/XInput/HID/RawInput not verified in-game; IGI input NOT TESTED.", "complete": false},
    {"number": 12, "title": "[P2] Legacy multiplayer networking: Winsock 2, DirectPlay/IPX, LAN discovery and deterministic network verification", "github_state": "OPEN", "classification": "WORKED+TESTED", "commits": ["52674039798a33aa2aa992956024d99bf111c245", "4276c1a1"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/ISSUES_AUDIT.md"], "work_done": "Blocking recv/accept semantics and loopback fixture; wsock32.c Winsock 1.1 + IPX-over-UDP wire framing.", "tested": "15 C selftests ok; 168 passed, 2 skipped; 8 fast-tier checks pass.", "highest_gate": "A3", "unit_only": true, "remaining_gate": "A4/A9: deterministic network verification in a real multiplayer game with an oracle not demonstrated; Winsock 2 overlapped I/O and DirectPlay absent.", "complete": false},
    {"number": 13, "title": "[P3] Implement PE32+ Win64 game pipeline and native host ABI only after Omill-vs-PCRecomp feasibility gate", "github_state": "OPEN", "classification": "DEFERRED", "commits": [], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/CAPABILITY_BOUNDARIES.md", "docs/MULTIARCH_RUN_ATTEMPT.md"], "work_done": "PE32+ detection fails closed; runtime/recomp64_cpu scaffolding exists; no x64 loader, lifter or host ABI added.", "tested": "tests/test_capability_boundaries.py 3/3: x64 refused, forced-arch refused, Linux cross-target refused; PE32+ games NOT RUN.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0 + roadmap/Omill-vs-PCRecomp gate: deferred until x86 PE32 macOS and Linux acceptance passes.", "complete": false},
    {"number": 14, "title": "[P0] Compatibility coverage inventory: automated game/API matrix, synthetic fixtures, CI regression and honest evidence gating", "github_state": "OPEN", "classification": "WORKED+TESTED", "commits": ["7015f401", "8529d6c"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/GAME_MATRIX.md", "docs/GAME_MATRIX_METHODOLOGY.md"], "work_done": "CI matrix + evidence gate, API coverage auditor (schema 1.1.0, 2249 registered shims), one-at-a-time game runner and frame-proof tooling, fail-closed A0-A10 ledger.", "tested": "Matrix tests 5 passed/26 subtests; 175 full tests passed; verify.sh --fast 186 passed/2 skipped, 7 checks PASS/1 SKIP.", "highest_gate": "A3", "unit_only": true, "remaining_gate": "A4-A10 corpus-wide: no green full-corpus game matrix and no per-game A5-A10 evidence gating across macOS/Linux.", "complete": false},
    {"number": 15, "title": "[Game target P0] Project I.G.I. (2000, Windows PC): independently verified native Mission 1 using shared D3D7 HLE", "github_state": "OPEN", "classification": "PARTIAL", "commits": ["0d4fbbd92963d39953d765f0ff0d4e4cc58c05e2"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/GAME_MATRIX.md", "docs/GAME_ACCEPTANCE_TRACKER.md"], "work_done": "Native build+run on feature/agent3-game-acceptance: 7325 fns recovered, 128/128 imports bound, v1.0 main menu renders with 3713 distinct colours; SHA-256 dea34892... recorded.", "tested": "Native macOS arm64 build and run; process launched and exited 1; menu screenshot counted non-blank. Earlier black-frame verdict corrected.", "highest_gate": "A6", "unit_only": false, "remaining_gate": "A7/A8/A9/A10: input NOT TESTED, audio PARTIAL (dummy device), gameplay PARTIAL (menu only), save/load and Mission 1 original-vs-native oracle absent.", "complete": false},
    {"number": 16, "title": "[Game target P1] Hitman: Codename 47 (2000): PE32 multi-DLL Glacier reconstruction and first verified mission", "github_state": "OPEN", "classification": "BLOCKED", "commits": ["90a80cee"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/HITMAN_UPSTREAM_AUDIT.md"], "work_done": "Upstream Glacier/re47/ReHitman audit pushed on feature/p10-hitman-research; no original Windows game files locally.", "tested": "Audit only; no native build or run.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0: owned Windows binary absent; all gates A0-A10 unmet.", "complete": false},
    {"number": 17, "title": "[Game target P1] Max Payne (2001, original Windows PC): MAX-FX D3D8, DLL lifecycle, bullet-time gameplay oracle", "github_state": "OPEN", "classification": "BLOCKED", "commits": ["48d9511"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/MAXPAYNE_UPSTREAM_AUDIT.md"], "work_done": "Upstream phobos665/payne-recomp MAX-FX/D3D8 audit pushed on feature/p10-maxpayne-research; no original Windows game files locally.", "tested": "Audit only; no native build or run.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0: owned Windows binary absent; all gates A0-A10 unmet.", "complete": false},
    {"number": 18, "title": "[Game target P1] Max Payne 2: The Fall of Max Payne (2003, Windows PC): reuse MAX-FX/D3D8 and verify physics", "github_state": "OPEN", "classification": "BLOCKED", "commits": [], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md"], "work_done": "No game-specific work; plan marks pending agent and BLOCKED on missing original Windows files.", "tested": "No build or run.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0: owned Windows binary absent; all gates A0-A10 unmet.", "complete": false},
    {"number": 19, "title": "[Game target P2] Hitman 2: Silent Assassin (2002, Windows PC): Glacier multi-DLL + D3D8/OpenGL native mission", "github_state": "OPEN", "classification": "BLOCKED", "commits": [], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md"], "work_done": "No game-specific work; depends on #3/#9/#16.", "tested": "No build or run.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0: owned Windows binary absent; all gates A0-A10 unmet.", "complete": false},
    {"number": 20, "title": "[Game target P2] Hitman: Contracts (2004, Windows PC): reusable Glacier renderer path, multi-DLL reconstruction and verified mission", "github_state": "OPEN", "classification": "BLOCKED", "commits": [], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md"], "work_done": "No game-specific work; depends on #3/#9/#16.", "tested": "No build or run.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0: owned Windows binary absent; all gates A0-A10 unmet.", "complete": false},
    {"number": 21, "title": "[Game target P2] Hitman: Blood Money (2006, Windows PC): D3D9 Glacier native executable and independently verified mission", "github_state": "OPEN", "classification": "BLOCKED", "commits": [], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md"], "work_done": "No game-specific work; depends on #5/#9/#16.", "tested": "No build or run.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0: owned Windows binary absent; all gates A0-A10 unmet.", "complete": false},
    {"number": 22, "title": "[Game target P3] TrackMania Nations ESWC (2006 Windows PC): GameBox D3D9 race verification; protected-build gate", "github_state": "OPEN", "classification": "PARTIAL", "commits": [], "evidence_docs": ["docs/ACCEPTANCE_LEDGER.md", "docs/GAME_TM_ESWC_EVIDENCE.md", "docs/GAME_MATRIX.md"], "work_done": "PE32 identity SHA-256 822def6627f020035ef0819cf9f85493e952a2efb450d23770b330311ad8109d, 259 imports/15 DLLs; one-line disasm32.py guard fixed recover; native arm64 build linked (85,216 functions, 1.13 GB C, 0 lift errors, 340,894,096-byte Mach-O).", "tested": "disasm32.py --selftest OK; strict runs: 195 bound/64 unresolved, 0 frames; permissive runs: SIGBUS at guest VA 0x7E00003C in sub_00410338, 0 frames in all five capture attempts.", "highest_gate": "A3", "unit_only": false, "remaining_gate": "A4/A6/A10: boot without 64 unresolved imports (Direct3DCreate9, DirectInput8Create, dsound ordinal 12), diagnose 0xACA0B0 indirect call, reach a frame and a race oracle.", "complete": false},
    {"number": 23, "title": "[Game target P2] TrackMania Nations Forever (2008): TmForever.map-guided GameBox PE32 recompilation and deterministic race", "github_state": "OPEN", "classification": "BLOCKED", "commits": [], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/GAME_MATRIX.md"], "work_done": "Source conflict preserved: plan records PE32 binary found/hashed and build pending; GAME_MATRIX.md and ledger record BLOCKED (no Windows game files, only a native macOS app). No native build or race result is claimed.", "tested": "No committed native build or deterministic-race run.", "highest_gate": "A0", "unit_only": false, "remaining_gate": "A3 then A4-A10: compiled native binary plus deterministic race replay; TmForever.map ingestion and D3D9/DInput8 not demonstrated.", "complete": false},
    {"number": 24, "title": "[Game target P3] TrackMania United Forever (2008): extend verified Nations Forever D3D9/GameBox pipeline across environments", "github_state": "OPEN", "classification": "BLOCKED", "commits": [], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/GAME_MATRIX.md"], "work_done": "No game-specific work; only a matrix row correction is referenced in history. Depends on #5/#11/#23.", "tested": "No build or run.", "highest_gate": null, "unit_only": false, "remaining_gate": "A0: distinct owned Windows binary absent; all gates A0-A10 unmet.", "complete": false},
    {"number": 25, "title": "Add fail-closed per-function multi-lifter fallback using differential testing", "github_state": "OPEN", "classification": "WORKED+TESTED", "commits": ["0ee2f988df5ba9cff5498cc61df7974cbf40db4e"], "evidence_docs": ["docs/ISSUE_EXECUTION_PLAN.md", "docs/ACCEPTANCE_LEDGER.md", "docs/ISSUES_AUDIT.md"], "work_done": "Opt-in per-function multi-lifter selector on feature/upstream-issue-25 over highlevel.py/hlverify.py/readability.py and 18 certified Cave Story functions in reports/certified/index.json.", "tested": "18 targeted tests + 188 full tests passed.", "highest_gate": "A3", "unit_only": true, "remaining_gate": "A4-A10: no game-level differential evidence end-to-end; selector is opt-in and not exercised in a verified game run.", "complete": false},
    {"number": 26, "title": "[BUILD SCALE] Generated C trees are multi-GB and exhaust host disk/memory during full-corpus rebuilds", "github_state": "OPEN", "classification": "WORKED+TESTED", "commits": [], "evidence_docs": ["docs/MULTIARCH_RUN_ATTEMPT.md", "docs/CAPABILITY_BOUNDARIES.md", "docs/ACCEPTANCE_LEDGER.md", "docs/ALL_GAMES_MACOS_EVIDENCE.md"], "work_done": "Measured exit 137/SIGKILL on parallel generated-C builds (ikachan 10.28 MB C, 3.03 MB binary, 20.97 MB project, 144 MB peak RSS); resource/disk guard and fail-safe cleanup mitigations recorded; one-at-a-time --parallel 1 --jobs 1 driver adopted.", "tested": "tools.game_matrix --parallel 1 --jobs 1 attempted; tests/test_capability_boundaries.py 3/3 pass; several larger games still have no native binary.", "highest_gate": "A3", "unit_only": true, "remaining_gate": "A3 full-corpus rebuild with multi-GB C trees under host memory/disk budget; then A4-A10 game runs — remaining games still have no native binary.", "complete": false},
    {"number": 27, "title": "[RUNTIME EH] Gunroar terminates on unhandled DMD exception 0xE0440001 despite eh32.c throw support", "github_state": "OPEN", "classification": "PARTIAL", "commits": ["5536865a7c93a691947e4c84eaa484ac5d736cd8"], "evidence_docs": ["docs/GAME_EXECUTION_EVIDENCE.md", "docs/ALL_GAMES_MACOS_EVIDENCE.md", "docs/CAPABILITY_BOUNDARIES.md", "docs/ACCEPTANCE_LEDGER.md"], "work_done": "DMD throw not-dispatched diagnosis made precise; eh32 dispatch groundwork for catch frames without EH checkpoints; docs record the exact RaiseException(0xE0440001) refusal.", "tested": "Gunroar native binary SHA-256 c4a1117c...; 136 imports bound, 0 unresolved; boots to entry 0x0042D49C then exits 3 with unhandled DMD exception; later runs SIGBUS at indirect-call VAs, 0 frames.", "highest_gate": "A4", "unit_only": false, "remaining_gate": "A6/A10: dispatch DMD catch frame with no EH checkpoint so Gunroar reaches a rendered frame and a scenario diff; latest runs still 0 frames, verdict FAIL.", "complete": false},
    {"number": 28, "title": "[API GAP] 19 unresolved imports from two newly downloaded PE32 games (Tetris, Chocolate Doom)", "github_state": "OPEN", "classification": "PARTIAL", "commits": [], "evidence_docs": ["docs/NEW_GAME_DOWNLOADS.md", "docs/PROOF_EVIDENCE.md", "docs/ALL_GAMES_MACOS_EVIDENCE.md", "docs/DISPLAY_SIZING_EVIDENCE.md"], "work_done": "All 19 measured imports implemented or registered: USER32/GDI32 W-variants, SDL 1.2 helpers/cursor trio, MSVCRT _snprintf/vprintf/_flsbuf/fscanf, WSACancelBlockingCall, Mix_SetPostMix (recorded, deliberately never invoked). Display sizing/fullscreen fixed in 3a99d9b.", "tested": "Tetris 97 bound/0 unresolved, 3 non-blank 310x418 frames (4/5/5 colours), alive 10 s; Chocolate Doom 166+34 bound/0 unresolved, exit 255 (no WAD); frame_proof colour check and 640x480/1680x1050 window verification.", "highest_gate": "A6", "unit_only": false, "remaining_gate": "A5-A10 gameplay oracle for Tetris; legal DOOM WAD fixture for Chocolate Doom; frame/input/audio/save-load comparison — import binding and a few frames are not gameplay.", "complete": false}
  ]
}
```

## Verification

1. **GitHub state** — `gh issue list --state all --limit 40` returned 28 rows,
   `#1`–`#28`; the `--json` form confirmed `state: OPEN` for all 28,
   `closedAt: null` for all 28, and `closure_count: 0`. No issue was closed.
2. **Quoted commit strings** — every commit hash quoted above was checked with a
   substring scan over the pre-existing `docs/*.md` files. All 26 quoted
   strings are present in committed docs (none was invented for this ledger):

   | Commit string | Pre-existing doc(s) where it appears |
   |---|---|
   | `808863ba73161755a188d79325a2643cebf84b28` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `46dc1ce` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md`, `UPSTREAM_CODE_PROVENANCE.md` |
   | `278eaf6394f7f42c7de6cffef2cf982154f59899` (upstream igi-recomp D3D7 source) | `UPSTREAM_CODE_PROVENANCE.md`, `IGI_RECOMP_REUSE.md`, `BINK_UPSTREAM_AUDIT.md` |
   | `5b3523d4d059b0adab2463d167e1ba887cea9211` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `3c81be6` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `4df88bc` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `6fb3bb44` (plan: `6fb3bb44…`) | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `31dbf96b` (plan: `31dbf96b…`) | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `c75c7df1` (plan: `c75c7df1…`) | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `ee6e966591956e3bc5d3df4630ce89b911d0d633` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `d223d8de` (plan: `d223d8de…`) | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `90db86e1` (plan: `90db86e1…`) | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `9c37a9e5cff4231032c5b78d0e59a78a22a6d643` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `8cbc16e` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `460d631` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `c31d82f` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `52674039798a33aa2aa992956024d99bf111c245` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `4276c1a1` (plan: `4276c1a1…`) | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `7015f401` (plan: `7015f401…`) | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `8529d6c` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `0d4fbbd92963d39953d765f0ff0d4e4cc58c05e2` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `90a80cee` (plan: `90a80cee…`) | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `48d9511` (plan: `48d9511…`) | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `0ee2f988df5ba9cff5498cc61df7974cbf40db4e` | `ISSUE_EXECUTION_PLAN.md`, `ACCEPTANCE_LEDGER.md` |
   | `5536865a7c93a691947e4c84eaa484ac5d736cd8` | `ACCEPTANCE_LEDGER.md` (upstream ledger source base) |
   | `3a99d9b` (prose, display fix) | `ALL_GAMES_MACOS_EVIDENCE.md`, `DISPLAY_SIZING_EVIDENCE.md` |

   No commit hash is quoted for `#4`, `#7`, `#13`, `#18`–`#24`, `#26` or
   `#28`: the source docs supply no hash for those rows, so their work is
   evidenced by document paths and measured results instead of invented
   citations. Hex strings used as file SHA-256 values (for example
   `024dccd5…` for OUCC Tetris or `822def66…` for TrackMania ESWC) are file
   hashes from the evidence docs, not commit strings.

3. **Machine-readability** — the JSON block above was parsed with
   `python3 -m json.tool` after extraction; it contains 28 issue objects with
   `github_state: OPEN` and `complete: false` on every row.

## Limitations

- This is a documentation-only change: no runtime, build, or game test was run
  by this ledger; all test numbers are quoted from the cited docs.
- `docs/ISSUE_EXECUTION_PLAN.md` and `docs/ACCEPTANCE_LEDGER.md` disagree with
  `docs/GAME_MATRIX.md` on the availability of the TrackMania Windows binaries
  (#22/#23). Both readings are preserved; neither is silently resolved.
- Issue #28's `Mix_SetPostMix` is recorded but never invoked from the host
  audio thread and `fscanf` fails with ENOSYS — documented limits, not success.
- No original-Windows gameplay oracle exists in this environment, so A10 is
  unmet for every game target. No issue may be closed on this evidence.
