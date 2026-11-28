# Issue closure gate

- Gate: `CLOSABLE_ONLY_WITH_EXECUTED_ORACLE_AND_VERIFIED_ASSETS`
- Generator: `tools/issue_closure_gate.py` (schema 1)
- Issues: `/tmp/issues-open.json`
- Policy: `/tmp/closure-policy.json`
- Manifests: 

**Overall: OPEN** -- this tool reports CLOSABLE or OPEN only; it does not close issues.

| Verdict | Count |
|---|---:|
| CLOSABLE | 0 |
| OPEN | 46 |

## Findings

| Issue | Reason codes | Details |
|---|---|---|
| #1 | NO_MANIFEST | no evidence manifest supplied for issue 1 |
| #2 | NO_MANIFEST | no evidence manifest supplied for issue 2 |
| #3 | NO_MANIFEST | no evidence manifest supplied for issue 3 |
| #4 | NO_MANIFEST | no evidence manifest supplied for issue 4 |
| #5 | NO_MANIFEST | no evidence manifest supplied for issue 5 |
| #6 | NO_MANIFEST | no evidence manifest supplied for issue 6 |
| #7 | NO_MANIFEST | no evidence manifest supplied for issue 7 |
| #8 | NO_MANIFEST | no evidence manifest supplied for issue 8 |
| #9 | NO_MANIFEST | no evidence manifest supplied for issue 9 |
| #10 | NO_MANIFEST | no evidence manifest supplied for issue 10 |
| #11 | NO_MANIFEST | no evidence manifest supplied for issue 11 |
| #12 | NO_MANIFEST | no evidence manifest supplied for issue 12 |
| #13 | NO_MANIFEST | no evidence manifest supplied for issue 13 |
| #14 | NO_MANIFEST | no evidence manifest supplied for issue 14 |
| #15 | NO_MANIFEST | no evidence manifest supplied for issue 15 |
| #16 | NO_MANIFEST | no evidence manifest supplied for issue 16 |
| #17 | NO_MANIFEST | no evidence manifest supplied for issue 17 |
| #18 | NO_MANIFEST | no evidence manifest supplied for issue 18 |
| #19 | NO_MANIFEST | no evidence manifest supplied for issue 19 |
| #20 | NO_MANIFEST | no evidence manifest supplied for issue 20 |
| #21 | NO_MANIFEST | no evidence manifest supplied for issue 21 |
| #22 | NO_MANIFEST | no evidence manifest supplied for issue 22 |
| #23 | NO_MANIFEST | no evidence manifest supplied for issue 23 |
| #24 | NO_MANIFEST | no evidence manifest supplied for issue 24 |
| #25 | NO_MANIFEST | no evidence manifest supplied for issue 25 |
| #26 | NO_MANIFEST | no evidence manifest supplied for issue 26 |
| #27 | NO_MANIFEST | no evidence manifest supplied for issue 27 |
| #28 | NO_MANIFEST | no evidence manifest supplied for issue 28 |
| #130 | NO_MANIFEST | no evidence manifest supplied for issue 130 |
| #131 | NO_MANIFEST | no evidence manifest supplied for issue 131 |
| #132 | NO_MANIFEST | no evidence manifest supplied for issue 132 |
| #133 | NO_MANIFEST | no evidence manifest supplied for issue 133 |
| #134 | NO_MANIFEST | no evidence manifest supplied for issue 134 |
| #135 | NO_MANIFEST | no evidence manifest supplied for issue 135 |
| #136 | NO_MANIFEST | no evidence manifest supplied for issue 136 |
| #137 | NO_MANIFEST | no evidence manifest supplied for issue 137 |
| #138 | NO_MANIFEST | no evidence manifest supplied for issue 138 |
| #141 | NO_MANIFEST | no evidence manifest supplied for issue 141 |
| #142 | NO_MANIFEST | no evidence manifest supplied for issue 142 |
| #144 | NO_MANIFEST | no evidence manifest supplied for issue 144 |
| #145 | NO_MANIFEST | no evidence manifest supplied for issue 145 |
| #146 | NO_MANIFEST | no evidence manifest supplied for issue 146 |
| #147 | NO_MANIFEST | no evidence manifest supplied for issue 147 |
| #148 | NO_MANIFEST | no evidence manifest supplied for issue 148 |
| #149 | NO_MANIFEST | no evidence manifest supplied for issue 149 |
| #171 | NO_MANIFEST | no evidence manifest supplied for issue 171 |

## Issues

| # | Title | GitHub state | Verdict | Reason codes | Manifest | SHA-256 |
|---|---|---|---|---|---|---|
| #1 | MASTER: Autonomous PCRecomp + rev.ng NON-AI-first readable C/C++, optional AI, verified native games & GUI | OPEN | OPEN | NO_MANIFEST | — | — |
| #2 | Implement IGI-recomp compatibility reuse: Direct3D7→OpenGL, DirectInput→SDL2, Win32 runtime and verified games | OPEN | OPEN | NO_MANIFEST | — | — |
| #3 | Audit Project-IGI-Awesome recomp projects: reuse replay, verification & D3D8 gaps without duplicate runtimes | OPEN | OPEN | NO_MANIFEST | — | — |
| #4 | Evaluate PC decompilers for GameReComp: Anvil overlap, Omill x64, Anvill/Rellic source benchmark—integrate only verified gaps | OPEN | OPEN | NO_MANIFEST | — | — |
| #5 | Reuse real PC game decomp projects WITHOUT duplicating PCRecomp: D3D9/DInput8 feasibility, replay and mod-file HLE audit | OPEN | OPEN | NO_MANIFEST | — | — |
| #6 | [P2] Legacy Windows 3D: Direct3D 1–6, DirectDraw 7 and 3dfx Glide compatibility without duplicating D3D7 work | OPEN | OPEN | NO_MANIFEST | — | — |
| #7 | [P3] Post-D3D9 Windows graphics: D3D9Ex, Direct3D 10/11, DXGI, shaders and D3DX dependency gates | OPEN | OPEN | NO_MANIFEST | — | — |
| #8 | [P3] Windows 3.x / Win16 NE games: wire existing PCRecomp 16-bit lifter into GameReComp's autonomous pipeline | OPEN | OPEN | NO_MANIFEST | — | — |
| #9 | [P1] PE32 multi-module game execution: DLL code lifting, TLS/SEH/C++ EH, CRT and cross-module ABI | OPEN | OPEN | NO_MANIFEST | — | — |
| #10 | [P2] Legacy Windows game audio/video: DirectSound3D, DirectMusic, WinMM MCI/MIDI, DirectShow, codecs and middleware | OPEN | OPEN | NO_MANIFEST | — | — |
| #11 | [P2] Game controller and raw input compatibility: DirectInput joystick/FFB, DirectInput8, XInput, HID and Raw Input | OPEN | OPEN | NO_MANIFEST | — | — |
| #12 | [P2] Legacy multiplayer networking: Winsock 2, DirectPlay/IPX, LAN discovery and deterministic network verification | OPEN | OPEN | NO_MANIFEST | — | — |
| #13 | [P3] Implement PE32+ Win64 game pipeline and native host ABI only after Omill-vs-PCRecomp feasibility gate | OPEN | OPEN | NO_MANIFEST | — | — |
| #14 | [P0] Compatibility coverage inventory: automated game/API matrix, synthetic fixtures, CI regression and honest evidence gating | OPEN | OPEN | NO_MANIFEST | — | — |
| #15 | [Game target P0] Project I.G.I. (2000, Windows PC): independently verified native Mission 1 using shared D3D7 HLE | OPEN | OPEN | NO_MANIFEST | — | — |
| #16 | [Game target P1] Hitman: Codename 47 (2000): PE32 multi-DLL Glacier reconstruction and first verified mission | OPEN | OPEN | NO_MANIFEST | — | — |
| #17 | [Game target P1] Max Payne (2001, original Windows PC): MAX-FX D3D8, DLL lifecycle, bullet-time gameplay oracle | OPEN | OPEN | NO_MANIFEST | — | — |
| #18 | [Game target P1] Max Payne 2: The Fall of Max Payne (2003, Windows PC): reuse MAX-FX/D3D8 and verify physics | OPEN | OPEN | NO_MANIFEST | — | — |
| #19 | [Game target P2] Hitman 2: Silent Assassin (2002, Windows PC): Glacier multi-DLL + D3D8/OpenGL native mission | OPEN | OPEN | NO_MANIFEST | — | — |
| #20 | [Game target P2] Hitman: Contracts (2004, Windows PC): reusable Glacier renderer path, multi-DLL reconstruction and verified mission | OPEN | OPEN | NO_MANIFEST | — | — |
| #21 | [Game target P2] Hitman: Blood Money (2006, Windows PC): D3D9 Glacier native executable and independently verified mission | OPEN | OPEN | NO_MANIFEST | — | — |
| #22 | [Game target P3] TrackMania Nations ESWC (2006 Windows PC): GameBox D3D9 race verification; protected-build gate | OPEN | OPEN | NO_MANIFEST | — | — |
| #23 | [Game target P2] TrackMania Nations Forever (2008): TmForever.map-guided GameBox PE32 recompilation and deterministic race | OPEN | OPEN | NO_MANIFEST | — | — |
| #24 | [Game target P3] TrackMania United Forever (2008): extend verified Nations Forever D3D9/GameBox pipeline across environments | OPEN | OPEN | NO_MANIFEST | — | — |
| #25 | Add fail-closed per-function multi-lifter fallback using differential testing | OPEN | OPEN | NO_MANIFEST | — | — |
| #26 | [BUILD SCALE] Generated C trees are multi-GB and exhaust host disk/memory during full-corpus rebuilds | OPEN | OPEN | NO_MANIFEST | — | — |
| #27 | [RUNTIME EH] Gunroar terminates on unhandled DMD exception 0xE0440001 despite eh32.c throw support | OPEN | OPEN | NO_MANIFEST | — | — |
| #28 | [API GAP] 19 unresolved imports from two newly downloaded PE32 games (Tetris, Chocolate Doom) | OPEN | OPEN | NO_MANIFEST | — | — |
| #130 | [P0] 10,000-game compatibility stress corpus with fail-closed evidence | OPEN | OPEN | NO_MANIFEST | — | — |
| #131 | [P3] Windows PE32 x86/x64 to Linux native target support | OPEN | OPEN | NO_MANIFEST | — | — |
| #132 | [P3] macOS Mach-O to Windows x86/x64 reverse porting feasibility | OPEN | OPEN | NO_MANIFEST | — | — |
| #133 | [OS BRIDGE] Windows PE32 x86/x64 games to native macOS arm64 | OPEN | OPEN | NO_MANIFEST | — | — |
| #134 | [OS BRIDGE] Windows PE32 x86/x64 apps to native Linux | OPEN | OPEN | NO_MANIFEST | — | — |
| #135 | [OS BRIDGE] macOS Mach-O apps to native Windows x86/x64 | OPEN | OPEN | NO_MANIFEST | — | — |
| #136 | [OS BRIDGE] macOS Mach-O apps to native Linux | OPEN | OPEN | NO_MANIFEST | — | — |
| #137 | [OS BRIDGE] Linux ELF apps to native Windows x86/x64 | OPEN | OPEN | NO_MANIFEST | — | — |
| #138 | [OS BRIDGE] Linux ELF apps to native macOS arm64 | OPEN | OPEN | NO_MANIFEST | — | — |
| #141 | [HARNESS] 10,000-game batch runner with fail-closed per-game evidence | OPEN | OPEN | NO_MANIFEST | — | — |
| #142 | [CLI] ELF/Mach-O/.NET refusal fixture corpus and CLI exit-code tests | OPEN | OPEN | NO_MANIFEST | — | — |
| #144 | [CI] Linux host execution for the pipeline and refusal tests | OPEN | OPEN | NO_MANIFEST | — | — |
| #145 | [ORACLE] Windows-side scenario capture tooling for A10 evidence | OPEN | OPEN | NO_MANIFEST | — | — |
| #146 | [AUDIO] In-game A8 audio acceptance fixture and verification | OPEN | OPEN | NO_MANIFEST | — | — |
| #147 | [INPUT] Deterministic input-pump scenario harness | OPEN | OPEN | NO_MANIFEST | — | — |
| #148 | [LIFT] x64 feasibility gate evidence package | OPEN | OPEN | NO_MANIFEST | — | — |
| #149 | [DOCS] Per-OS capability matrix generated from evidence | OPEN | OPEN | NO_MANIFEST | — | — |
| #171 | [TOOLING] Frida cannot attach on this macOS ARM64 host — dual-engine proof blocked | OPEN | OPEN | NO_MANIFEST | — | — |

## Reproduction command

`python3 -m tools.issue_closure_gate --issues /tmp/issues-open.json --policy /tmp/closure-policy.json --manifest-list /tmp/no-manifests.json --out closure-audit.md --json closure-audit.json`

## Limits

- The tool only reports CLOSABLE or OPEN. It never closes, reopens, comments on or edits any issue, calls gh, or uses the network; it reads only the CLI-supplied files and writes only the two CLI-supplied report paths.
- CLOSABLE means the supplied evidence satisfies the gate, not that an issue was or will be closed by this tool. No GitHub auto-closing keyword is emitted in any report or on stdout/stderr.
- An executed-oracle record is accepted only when 'executed' is true, a non-empty command is present and at least one artifact exists with a matching SHA-256 recomputed from its bytes. Flags and documentation are never treated as an oracle.
- Every asset required by the policy must exist with a SHA-256 that matches the policy claim, recomputed from disk; every required verification must be present and exactly the JSON boolean true; and no critical API gap for the issue may be unresolved.
- Each manifest must carry a sha256 claim over its canonical JSON with the claim field excluded. Editing any other manifest field without resealing that claim is detected as MANIFEST_SHA256_MISMATCH.
- When policy.expected_issues is supplied, every expected issue must appear in --issues or it is reported OPEN with MISSING_EXPECTED_ISSUE; an issue outside that set is OPEN with UNEXPECTED_ISSUE.
- Reports are byte-stable: no clock, environment value or machine-dependent ordering is used. The same argv and the same input bytes yield the same two reports.
