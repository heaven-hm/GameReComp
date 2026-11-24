# Final issue status ledger (#1–#28): honest readout, not a closure report

This is a **final status readout**, not a completion claim. It was assembled from
a fresh, read-only `gh` query against `heaven-hm/GameReComp` and from the
committed planning/matrix docs. All **28** issues (#1–#28) are **OPEN** on GitHub;
`closedAt` is `null` for every row. **No issue is closed, fixed, resolved, merged
or accepted by this ledger, and no issue was closed by the work it describes.**
This ledger does not claim 100% completion of any issue, and no completion
percentage is estimated.

A build, a unit test, a process boot, a menu frame or an upstream audit is
**evidence of a stage**, never an issue-closure event. Every row below records
the highest evidence gate actually reached, the exact remaining blocker, and a
reproducible light verification command. No row claims a game-level oracle.

## Provenance and verification inputs

| Field | Value |
|---|---|
| Repo / remote | `heaven-hm/GameReComp` (`origin`: `https://github.com/heaven-hm/GameReComp.git`) |
| Worktree base commit | `c3c943028031d08386e70c70ab189619e6ae25c0` |
| Live GitHub query (read-only) | `gh issue list --state all --limit 40` |
| Live GitHub JSON query (read-only) | `gh issue list --state all --limit 40 --json number,title,state,stateReason,closedAt` |
| Query time (UTC) | `2026-11-23T15:25:27Z` |
| GitHub state observed | **28 rows (#1–#28), all `OPEN`, `closedAt: null`, `stateReason: ""`, zero closures** |
| Primary source docs | `docs/ISSUE_EXECUTION_PLAN.md`, `docs/GAME_MATRIX.md` |
| Corroborating docs | `docs/ISSUES_AUDIT.md`, `docs/ISSUE_WORK_LEDGER.md`, `docs/GAME_MATRIX_METHODOLOGY.md`, `docs/ACCEPTANCE_LEDGER.md`, `docs/VERIFICATION.md` |
| Scope note | #26–#28 were opened after the 25-row `ISSUE_EXECUTION_PLAN.md`; they are out of that plan's table but are in scope here |

The live query was the source of truth for the GitHub-state column. The plan and
matrix were the source of truth for evidence gates and blockers; where they
disagree (notably the TrackMania Windows-binary availability for #22/#23), the
conflict is preserved rather than silently resolved.

## Evidence-gate vocabulary

Gates A0–A10 come from `docs/GAME_MATRIX_METHODOLOGY.md`:

- **A0** inspect, **A1** recover, **A2** translate + project, **A3** compile.
- **A4** process launches and stays alive (or exits cleanly) — process-level only.
- **A5** menu, **A6** graphics/frames, **A7** input, **A8** audio, **A9** save/load,
  **A10** original-vs-native differential.

A0–A3 are stage/build results; A4 is a process-level result; A5–A10 require
independent scenario evidence. **`PARTIAL`, `NOT TESTED`, `BLOCKED` and
`UNTESTABLE` are never `PASS`.** A bare compile or a bare process exit is not a
game acceptance result. In the table, `(unit/build only)`, `(audit only)`,
`(boot only)`, `(menu only)`, `(frames only)` and `(deferred)` explicitly limit
what each gate means. No row reaches A10.

## Final status table

The verification commands below are *light, reproducible checks of the recorded
evidence boundary*. They confirm what exists and what fails closed; they do not
run an original-Windows oracle and therefore cannot close an issue. Commands
that need local game assets are deliberately replaced by the corresponding
fail-closed doc/tool test here.

| # | Issue (short) | GitHub state | Highest evidence gate | Exact remaining blocker | Verification command |
|---|---|---|---|---|---|
| 1 | MASTER: autonomous PCRecomp/rev.ng pipeline + GUI | OPEN | A3 (unit/build only) | A4–A10: no end-to-end verified native game run under the master pipeline; unit tests are not a game oracle | `python3 -m pytest tests/ -q` |
| 2 | IGI-recomp reuse: D3D7/OpenGL, DInput/SDL2, verified games | OPEN | A3 (unit/build only) | A6/A7 in-game: D3D7 graphics and DirectInput not verified in actual gameplay (IGI input NOT TESTED) | `sh runtime/win32hle/build_selftests.sh && python3 -m pytest tests/ -q` |
| 3 | Project-IGI-Awesome audit: replay, verification, D3D8 gaps | OPEN | A3 (unit/build only) | A4–A10: D3D8 replay/verification reuse not demonstrated against a real D3D8 game | `python3 -m pytest tests/test_d3d8.py -q` |
| 4 | Decompiler evaluation: Anvil/Omill/Anvill-Rellic benchmark | OPEN | none | A0: formal benchmark report comparing Anvill/Rellic/Omill with rev.ng on control-flow/struct recovery, with committed results | `python3 -m pytest tests/test_revng_probe.py tests/test_revng_provider.py -q` |
| 5 | Reuse real PC decomp projects: D3D9/DInput8 feasibility | OPEN | none (audit only) | A0/A3: no `d3d9.dll`/`dinput8.dll` runtime build or run evidence; upstream audits are documentation only | `python3 -m pytest tests/test_acceptance_docs.py -q` |
| 6 | Legacy D3D 1–6, DirectDraw 7, 3dfx Glide | OPEN | A3 (unit/build only) | A4–A10: no in-game verification for D3D 1–6 / DirectDraw / 3dfx Glide titles | `sh runtime/win32hle/build_selftests.sh` |
| 7 | Post-D3D9 graphics: D3D9Ex, D3D10/11, DXGI, shaders, D3DX | OPEN | none | A0: no implementation, build or run evidence; `d3d9ex`/`d3d10`/`d3d11`/`dxgi`/`d3dx` absent from `runtime/win32hle/` | `python3 -m pytest tests/test_capability_boundaries.py -q` |
| 8 | Win16 / Windows 3.x NE pipeline wiring | OPEN | none (deferred) | A0 + roadmap gate: deferred until x86 PE32 macOS and Linux acceptance passes; NE lifecycle not wired into `tools/autorecomp/pipeline.py` or end-to-end tested | `python3 -m pytest tests/test_capability_boundaries.py -q` |
| 9 | PE32 multi-module: DLL lifting, TLS/SEH/C++ EH, CRT, cross-module ABI | OPEN | A3 (unit/build only) | A4–A10: arbitrary dynamic `LoadLibrary` DLL lifting, guest SEH unwinding and MSVC/Borland/D exceptions not verified end-to-end | `sh runtime/win32hle/build_selftests.sh` |
| 10 | Audio/video: DS3D, DirectMusic, WinMM MCI/MIDI, DirectShow, codecs | OPEN | A3 (unit/build only) | A8: no in-game audio acceptance; IGI audio only PARTIAL (dummy device); MCI/MIDI/DirectSound3D/DirectShow unverified | `sh runtime/win32hle/build_selftests.sh` |
| 11 | Controllers/raw input: DInput FFB, DInput8, XInput, HID, Raw Input | OPEN | A3 (unit/build only) | A7: joystick/FFB/XInput/HID/RawInput not verified in-game; IGI input NOT TESTED | `sh runtime/win32hle/build_selftests.sh` |
| 12 | Networking: Winsock 2, DirectPlay/IPX, LAN, deterministic verification | OPEN | A3 (unit/build only) | A4/A9: deterministic verification in a real multiplayer game with an oracle not demonstrated (loopback fixture only); Winsock 2 overlapped I/O and DirectPlay absent | `sh runtime/win32hle/build_selftests.sh` |
| 13 | PE32+ / Win64 pipeline and host ABI after feasibility gate | OPEN | none (deferred) | A0 + roadmap/Omill-vs-PCRecomp gate: deferred until x86 PE32 macOS and Linux acceptance passes; PE32+ games NOT RUN; x64 detection only fails closed | `python3 -m pytest tests/test_capability_boundaries.py -q` |
| 14 | Compatibility matrix, synthetic fixtures, CI regression, evidence gating | OPEN | A3 (unit/build only) | A4–A10 corpus-wide: no green full-corpus matrix and no per-game A5–A10 gating across macOS/Linux | `sh verify.sh --fast && python3 -m pytest tests/test_game_matrix.py -q` |
| 15 | Project I.G.I.: independently verified native Mission 1 | OPEN | A6 (menu only) | A7/A8/A9/A10: input NOT TESTED, audio PARTIAL (dummy device), gameplay PARTIAL (menu only), save/load and Mission 1 original-vs-native oracle absent | `python3 -m pytest tests/test_game_evidence.py -q` |
| 16 | Hitman: Codename 47 — PE32 multi-DLL Glacier + first mission | OPEN | none | A0: owned original Windows binary absent; upstream audit only; all gates A0–A10 unmet | `python3 -m pytest tests/test_acceptance_docs.py -q` |
| 17 | Max Payne — MAX-FX D3D8, DLL lifecycle, bullet-time oracle | OPEN | none | A0: owned original Windows binary absent; upstream audit only; all gates A0–A10 unmet | `python3 -m pytest tests/test_acceptance_docs.py -q` |
| 18 | Max Payne 2 — MAX-FX/D3D8 physics and mission verification | OPEN | none | A0: owned original Windows binary absent; depends on #3/#9/#17; all gates A0–A10 unmet | `python3 -m pytest tests/test_acceptance_docs.py -q` |
| 19 | Hitman 2: Silent Assassin — Glacier multi-DLL + D3D8/OpenGL | OPEN | none | A0: owned original Windows binary absent; depends on #3/#9/#16; all gates A0–A10 unmet | `python3 -m pytest tests/test_acceptance_docs.py -q` |
| 20 | Hitman: Contracts — Glacier renderer + multi-DLL | OPEN | none | A0: owned original Windows binary absent; depends on #3/#9/#16; all gates A0–A10 unmet | `python3 -m pytest tests/test_acceptance_docs.py -q` |
| 21 | Hitman: Blood Money — D3D9 Glacier native + verified mission | OPEN | none | A0: owned original Windows binary absent; depends on #5/#9/#16; all gates A0–A10 unmet | `python3 -m pytest tests/test_acceptance_docs.py -q` |
| 22 | TrackMania Nations ESWC — GameBox D3D9 race + protection gate | OPEN | A3 (build only) | A4/A6/A10: boot without 64 unresolved imports (`Direct3DCreate9`, `DirectInput8Create`, dsound ordinal 12), diagnose the `0xACA0B0` indirect call, then reach a frame and a race oracle | `python3 -m pytest tests/test_game_evidence.py -q` |
| 23 | TrackMania Nations Forever — `.map`-guided GameBox + race | OPEN | A0 (identity only) | A3 then A4–A10: source conflict preserved (plan says binary found/hash recorded, matrix says BLOCKED); no committed native build, `.map` ingestion, D3D9/DInput8 or deterministic race replay | `python3 -m pytest tests/test_game_evidence.py -q` |
| 24 | TrackMania United Forever — extended GameBox environments | OPEN | none | A0: distinct owned original Windows binary absent; depends on #5/#11/#23; all gates A0–A10 unmet | `python3 -m pytest tests/test_acceptance_docs.py -q` |
| 25 | Fail-closed per-function multi-lifter fallback via differential testing | OPEN | A3 (unit/build only) | A4–A10: no game-level differential evidence end-to-end; selector is opt-in and not exercised in a verified game run | `python3 -m pytest tests/test_multilifter.py tests/test_per_function_oracle.py -q` |
| 26 | Build scale: multi-GB generated C trees exhaust host disk/memory | OPEN | A3 (unit/build only) | A3 full-corpus rebuild with multi-GB C trees under host memory/disk budget, then A4–A10 runs; mitigation is resource guards + `--parallel 1 --jobs 1`, not a completed rebuild | `python3 -m pytest tests/test_resource_footprint.py tests/test_capability_boundaries.py -q` |
| 27 | Runtime EH: Gunroar unhandled DMD exception `0xE0440001` | OPEN | A4 (boot only) | A6/A10: dispatch DMD catch frame with no EH checkpoint so Gunroar reaches a rendered frame and a scenario diff; latest runs still 0 frames, verdict FAIL | `sh runtime/win32hle/build_selftests.sh && python3 -m pytest tests/test_game_evidence.py -q` |
| 28 | API gap: 19 unresolved imports (Tetris, Chocolate Doom) | OPEN | A6 (frames only) | A5–A10 gameplay oracle for Tetris; legal DOOM WAD fixture for Chocolate Doom; input/audio/save-load comparison — import binding and a few frames are not gameplay | `python3 -m pytest tests/test_frame_proof.py tests/test_game_evidence.py -q` |

## Machine-readable ledger

<!-- BEGIN MACHINE-READABLE LEDGER -->
```json
{
  "schema_version": 1,
  "document": "docs/ISSUE_FINAL_STATUS.md",
  "readout_only": true,
  "sources": {
    "plan": "docs/ISSUE_EXECUTION_PLAN.md",
    "matrix": "docs/GAME_MATRIX.md",
    "methodology": "docs/GAME_MATRIX_METHODOLOGY.md",
    "work_ledger": "docs/ISSUE_WORK_LEDGER.md"
  },
  "verification": {
    "state_query": "gh issue list --state all --limit 40",
    "json_query": "gh issue list --state all --limit 40 --json number,title,state,stateReason,closedAt",
    "at_utc": "2026-11-23T15:25:27Z",
    "base_commit": "c3c943028031d08386e70c70ab189619e6ae25c0",
    "rows": 28,
    "all_open": true,
    "closure_count": 0,
    "complete_count": 0,
    "a10_count": 0
  },
  "gate_vocabulary": {
    "A0": "inspect",
    "A1": "recover",
    "A2": "translate + project",
    "A3": "compile",
    "A4": "process launch only",
    "A5": "menu",
    "A6": "graphics/frames",
    "A7": "input",
    "A8": "audio",
    "A9": "save/load",
    "A10": "original-vs-native differential (unmet for every issue)"
  },
  "issues": [
    {"number": 1, "title": "MASTER: Autonomous PCRecomp + rev.ng NON-AI-first readable C/C++, optional AI, verified native games & GUI", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (unit/build only)", "remaining_blocker": "A4-A10: no end-to-end verified native game run under the master pipeline; unit tests are not a game oracle", "verification_command": "python3 -m pytest tests/ -q"},
    {"number": 2, "title": "Implement IGI-recomp compatibility reuse: Direct3D7->OpenGL, DirectInput->SDL2, Win32 runtime and verified games", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (unit/build only)", "remaining_blocker": "A6/A7 in-game: D3D7 graphics and DirectInput not verified in actual gameplay (IGI input NOT TESTED)", "verification_command": "sh runtime/win32hle/build_selftests.sh && python3 -m pytest tests/ -q"},
    {"number": 3, "title": "Audit Project-IGI-Awesome recomp projects: reuse replay, verification & D3D8 gaps without duplicate runtimes", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (unit/build only)", "remaining_blocker": "A4-A10: D3D8 replay/verification reuse not demonstrated against a real D3D8 game", "verification_command": "python3 -m pytest tests/test_d3d8.py -q"},
    {"number": 4, "title": "Evaluate PC decompilers for GameReComp: Anvil overlap, Omill x64, Anvill/Rellic source benchmark—integrate only verified gaps", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none", "remaining_blocker": "A0: formal benchmark report comparing Anvill/Rellic/Omill with rev.ng on control-flow/struct recovery, with committed results", "verification_command": "python3 -m pytest tests/test_revng_probe.py tests/test_revng_provider.py -q"},
    {"number": 5, "title": "Reuse real PC game decomp projects WITHOUT duplicating PCRecomp: D3D9/DInput8 feasibility, replay and mod-file HLE audit", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none (audit only)", "remaining_blocker": "A0/A3: no d3d9.dll/dinput8.dll runtime build or run evidence; upstream audits are documentation only", "verification_command": "python3 -m pytest tests/test_acceptance_docs.py -q"},
    {"number": 6, "title": "[P2] Legacy Windows 3D: Direct3D 1–6, DirectDraw 7 and 3dfx Glide compatibility without duplicating D3D7 work", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (unit/build only)", "remaining_blocker": "A4-A10: no in-game verification for D3D 1-6 / DirectDraw / 3dfx Glide titles", "verification_command": "sh runtime/win32hle/build_selftests.sh"},
    {"number": 7, "title": "[P3] Post-D3D9 Windows graphics: D3D9Ex, Direct3D 10/11, DXGI, shaders and D3DX dependency gates", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none", "remaining_blocker": "A0: no implementation, build or run evidence; d3d9ex/d3d10/d3d11/dxgi/d3dx absent from runtime/win32hle/", "verification_command": "python3 -m pytest tests/test_capability_boundaries.py -q"},
    {"number": 8, "title": "[P3] Windows 3.x / Win16 NE games: wire existing PCRecomp 16-bit lifter into GameReComp's autonomous pipeline", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none (deferred)", "remaining_blocker": "A0 + roadmap gate: deferred until x86 PE32 macOS and Linux acceptance passes; NE lifecycle not wired into tools/autorecomp/pipeline.py or end-to-end tested", "verification_command": "python3 -m pytest tests/test_capability_boundaries.py -q"},
    {"number": 9, "title": "[P1] PE32 multi-module game execution: DLL code lifting, TLS/SEH/C++ EH, CRT and cross-module ABI", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (unit/build only)", "remaining_blocker": "A4-A10: arbitrary dynamic LoadLibrary DLL lifting, guest SEH unwinding and MSVC/Borland/D exceptions not verified end-to-end", "verification_command": "sh runtime/win32hle/build_selftests.sh"},
    {"number": 10, "title": "[P2] Legacy Windows game audio/video: DirectSound3D, DirectMusic, WinMM MCI/MIDI, DirectShow, codecs and middleware", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (unit/build only)", "remaining_blocker": "A8: no in-game audio acceptance; IGI audio only PARTIAL (dummy device); MCI/MIDI/DirectSound3D/DirectShow unverified", "verification_command": "sh runtime/win32hle/build_selftests.sh"},
    {"number": 11, "title": "[P2] Game controller and raw input compatibility: DirectInput joystick/FFB, DirectInput8, XInput, HID and Raw Input", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (unit/build only)", "remaining_blocker": "A7: joystick/FFB/XInput/HID/RawInput not verified in-game; IGI input NOT TESTED", "verification_command": "sh runtime/win32hle/build_selftests.sh"},
    {"number": 12, "title": "[P2] Legacy multiplayer networking: Winsock 2, DirectPlay/IPX, LAN discovery and deterministic network verification", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (unit/build only)", "remaining_blocker": "A4/A9: deterministic verification in a real multiplayer game with an oracle not demonstrated (loopback fixture only); Winsock 2 overlapped I/O and DirectPlay absent", "verification_command": "sh runtime/win32hle/build_selftests.sh"},
    {"number": 13, "title": "[P3] Implement PE32+ Win64 game pipeline and native host ABI only after Omill-vs-PCRecomp feasibility gate", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none (deferred)", "remaining_blocker": "A0 + roadmap/Omill-vs-PCRecomp gate: deferred until x86 PE32 macOS and Linux acceptance passes; PE32+ games NOT RUN; x64 detection only fails closed", "verification_command": "python3 -m pytest tests/test_capability_boundaries.py -q"},
    {"number": 14, "title": "[P0] Compatibility coverage inventory: automated game/API matrix, synthetic fixtures, CI regression and honest evidence gating", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (unit/build only)", "remaining_blocker": "A4-A10 corpus-wide: no green full-corpus matrix and no per-game A5-A10 gating across macOS/Linux", "verification_command": "sh verify.sh --fast && python3 -m pytest tests/test_game_matrix.py -q"},
    {"number": 15, "title": "[Game target P0] Project I.G.I. (2000, Windows PC): independently verified native Mission 1 using shared D3D7 HLE", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A6 (menu only)", "remaining_blocker": "A7/A8/A9/A10: input NOT TESTED, audio PARTIAL (dummy device), gameplay PARTIAL (menu only), save/load and Mission 1 original-vs-native oracle absent", "verification_command": "python3 -m pytest tests/test_game_evidence.py -q"},
    {"number": 16, "title": "[Game target P1] Hitman: Codename 47 (2000): PE32 multi-DLL Glacier reconstruction and first verified mission", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none", "remaining_blocker": "A0: owned original Windows binary absent; upstream audit only; all gates A0-A10 unmet", "verification_command": "python3 -m pytest tests/test_acceptance_docs.py -q"},
    {"number": 17, "title": "[Game target P1] Max Payne (2001, original Windows PC): MAX-FX D3D8, DLL lifecycle, bullet-time gameplay oracle", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none", "remaining_blocker": "A0: owned original Windows binary absent; upstream audit only; all gates A0-A10 unmet", "verification_command": "python3 -m pytest tests/test_acceptance_docs.py -q"},
    {"number": 18, "title": "[Game target P1] Max Payne 2: The Fall of Max Payne (2003, Windows PC): reuse MAX-FX/D3D8 and verify physics", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none", "remaining_blocker": "A0: owned original Windows binary absent; depends on #3/#9/#17; all gates A0-A10 unmet", "verification_command": "python3 -m pytest tests/test_acceptance_docs.py -q"},
    {"number": 19, "title": "[Game target P2] Hitman 2: Silent Assassin (2002, Windows PC): Glacier multi-DLL + D3D8/OpenGL native mission", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none", "remaining_blocker": "A0: owned original Windows binary absent; depends on #3/#9/#16; all gates A0-A10 unmet", "verification_command": "python3 -m pytest tests/test_acceptance_docs.py -q"},
    {"number": 20, "title": "[Game target P2] Hitman: Contracts (2004, Windows PC): reusable Glacier renderer path, multi-DLL reconstruction and verified mission", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none", "remaining_blocker": "A0: owned original Windows binary absent; depends on #3/#9/#16; all gates A0-A10 unmet", "verification_command": "python3 -m pytest tests/test_acceptance_docs.py -q"},
    {"number": 21, "title": "[Game target P2] Hitman: Blood Money (2006, Windows PC): D3D9 Glacier native executable and independently verified mission", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none", "remaining_blocker": "A0: owned original Windows binary absent; depends on #5/#9/#16; all gates A0-A10 unmet", "verification_command": "python3 -m pytest tests/test_acceptance_docs.py -q"},
    {"number": 22, "title": "[Game target P3] TrackMania Nations ESWC (2006 Windows PC): GameBox D3D9 race verification; protected-build gate", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (build only)", "remaining_blocker": "A4/A6/A10: boot without 64 unresolved imports (Direct3DCreate9, DirectInput8Create, dsound ordinal 12), diagnose the 0xACA0B0 indirect call, then reach a frame and a race oracle", "verification_command": "python3 -m pytest tests/test_game_evidence.py -q"},
    {"number": 23, "title": "[Game target P2] TrackMania Nations Forever (2008): TmForever.map-guided GameBox PE32 recompilation and deterministic race", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A0 (identity only)", "remaining_blocker": "A3 then A4-A10: source conflict preserved (plan says binary found/hash recorded, matrix says BLOCKED); no committed native build, .map ingestion, D3D9/DInput8 or deterministic race replay", "verification_command": "python3 -m pytest tests/test_game_evidence.py -q"},
    {"number": 24, "title": "[Game target P3] TrackMania United Forever (2008): extend verified Nations Forever D3D9/GameBox pipeline across environments", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "none", "remaining_blocker": "A0: distinct owned original Windows binary absent; depends on #5/#11/#23; all gates A0-A10 unmet", "verification_command": "python3 -m pytest tests/test_acceptance_docs.py -q"},
    {"number": 25, "title": "Add fail-closed per-function multi-lifter fallback using differential testing", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (unit/build only)", "remaining_blocker": "A4-A10: no game-level differential evidence end-to-end; selector is opt-in and not exercised in a verified game run", "verification_command": "python3 -m pytest tests/test_multilifter.py tests/test_per_function_oracle.py -q"},
    {"number": 26, "title": "[BUILD SCALE] Generated C trees are multi-GB and exhaust host disk/memory during full-corpus rebuilds", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A3 (unit/build only)", "remaining_blocker": "A3 full-corpus rebuild with multi-GB C trees under host memory/disk budget, then A4-A10 runs; mitigation is resource guards + --parallel 1 --jobs 1, not a completed rebuild", "verification_command": "python3 -m pytest tests/test_resource_footprint.py tests/test_capability_boundaries.py -q"},
    {"number": 27, "title": "[RUNTIME EH] Gunroar terminates on unhandled DMD exception 0xE0440001 despite eh32.c throw support", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A4 (boot only)", "remaining_blocker": "A6/A10: dispatch DMD catch frame with no EH checkpoint so Gunroar reaches a rendered frame and a scenario diff; latest runs still 0 frames, verdict FAIL", "verification_command": "sh runtime/win32hle/build_selftests.sh && python3 -m pytest tests/test_game_evidence.py -q"},
    {"number": 28, "title": "[API GAP] 19 unresolved imports from two newly downloaded PE32 games (Tetris, Chocolate Doom)", "github_state": "OPEN", "closed_at": null, "complete": false, "highest_gate": "A6 (frames only)", "remaining_blocker": "A5-A10 gameplay oracle for Tetris; legal DOOM WAD fixture for Chocolate Doom; input/audio/save-load comparison - import binding and a few frames are not gameplay", "verification_command": "python3 -m pytest tests/test_frame_proof.py tests/test_game_evidence.py -q"}
  ]
}
```
<!-- END MACHINE-READABLE LEDGER -->

## Verification

1. **GitHub state** — `gh issue list --state all --limit 40` returned 28 rows,
   #1–#28. The `--json number,title,state,stateReason,closedAt` form returned
   `state: OPEN`, `stateReason: ""` and `closedAt: null` for all 28 rows;
   `closure_count: 0`. No issue was closed by this work.
2. **Self-guard test** — `python3 -m pytest tests/test_issue_final_status.py -q`
   parses the machine-readable block and the markdown table, and fails if any
   row claims `CLOSED`/`complete`, reaches A10, omits a gate/blocker/verification
   command, or asserts an unnegated perfect-completion claim.
3. **No live oracle claimed** — the verification commands are light checks of
   doc/tool boundaries. None of them runs an original-Windows game, so none can
   convert a PARTIAL/build result into an accepted issue.

## Limits

- This is a documentation-only change: no runtime, build or game acceptance test
  was run to produce this ledger; all gate descriptions are quoted from the
  cited committed docs.
- `docs/ISSUE_EXECUTION_PLAN.md` and `docs/GAME_MATRIX.md` still disagree on the
  TrackMania Windows-binary availability for #22/#23. Both readings are
  preserved; no build or race result is assumed.
- No original-Windows gameplay oracle exists in this environment, so A10 is
  unmet for every game target and a build, boot or frame is never closure.
- GitHub state is a point-in-time readout at the timestamp above; a fresh query
  is required before any closure decision.
