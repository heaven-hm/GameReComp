# Game-target issues #15–#24 — evidence-backed status (2026-11-25)

Repo head: `5495e66` (`origin/develop`). Machine-readable profile:
`reports/games/game-target-issues-15-24.json`.

**Rule:** no issue is closed here. Every issue's acceptance ladder ends at an
independently verified **original-Windows playable scenario** vs the native
rebuild; none has one. Statuses below are measured, not optimistic.

## 1. Input inventory (user-owned paths only; no proprietary file committed)

| Title | Input | SHA-256 (12) | PE / imports | Status |
|---|---|---|---|---|
| I.G.I. (#15) | `igi.exe` | `dea34892d417` | PE32 / 128 | PRESENT |
| Hitman 1–4 (#16, #19–#21) | — | — | — | **MISSING_INPUT** |
| Max Payne 1/2 (#17–#18) | — | — | — | **MISSING_INPUT** |
| TrackMania ESWC (#22) | `TmNationsESWC.exe` | `822def6627f0` | PE32 / 259 | PRESENT, identity unverified |
| TrackMania Forever (#23) | `TmForever.exe` | `5e287e79a07c` | PE32 / 416 | PRESENT |
| TrackMania Forever map (#23) | `TmForever.map` | `558c6f7105a4` | 14,752,330 B | PRESENT, **validated** |
| TrackMania United Forever (#24) | — | — | — | **MISSING_INPUT** |

The only Hitman/Max Payne "hits" on disk were unrelated `System.dll` files in
other games' directories; they are not inputs for these issues.

## 2. Status by issue

| Issue | Rung reached | Status | Concrete blocker |
|---|---|---|---|
| #15 I.G.I. | binary lift + partial native run | **PARTIAL** | texture abort fixed, but no original Mission 1 input/frame oracle |
| #16 Hitman C47 | none | **MISSING_INPUT** | no owned EXE/DLL set; #9 multi-DLL execution absent |
| #17 Max Payne | none | **MISSING_INPUT** | no PC EXE; #3 D3D8 absent |
| #18 Max Payne 2 | none | **MISSING_INPUT** | no PC EXE; #3 D3D8 absent |
| #19 Hitman 2 | none | **MISSING_INPUT** | no EXE; #3/#9 absent |
| #20 Hitman Contracts | none | **MISSING_INPUT** | no EXE; #3/#9 absent |
| #21 Blood Money | none | **MISSING_INPUT** | no EXE; #5 D3D9 absent |
| #22 TM ESWC | input inventory only | **BLOCKED** (identity + D3D9) | PE timestamp `1558652719` (2019-05-23) does not match a 2006 retail build; StarForce 3.07 documented; compile halted at 46% before any binary |
| #23 TM Forever | inventory + map identity + 118,469 names | **PARTIAL** | #5 D3D9/shader backend absent; no offline-race oracle |
| #24 TM United | none | **MISSING_INPUT** | no EXE; depends on #23 |

## 3. New measured progress on #23 (map identity)

Issue #23 requires verifying the `.map` describes the actual PE *before* using
its names. Measured:

- `TmForever.map`: `Timestamp is 4d431494 (Fri Jan 28 20:10:12 2011)`,
  `Preferred load address is 00400000`.
- `TmForever.exe`: `TimeDateStamp = 0x4d431494` (= 1296241812),
  `ImageBase = 0x00400000`, sections `.text/.rdata/.data/.rsrc/.reloc/.edata`.
- `python3 tools/pe/map_names.py resolve <map> <exe> -o names.json`:
  `entry point matches: 0x0040351F`, **118,469 symbols** (75,287 functions,
  118,255 library symbols), **106,319 distinct addresses**. The importer already
  exists in `tools/pe/map_names.py` with a fail-closed identity check, so no new
  game-specific importer is needed; the deliverable is satisfied by reuse.

`names.json` (7.8 MB, SHA-256 `2e98cffe…`) was written outside the repository —
it is derived symbol metadata, not shipped game code. The map's names alone are
not evidence of gameplay; they are fed through normal `tools/autorecomp`
metadata when a D3D9 backend and a race fixture exist.

## 4. Shared blockers (must be solved in their owning issues)

- **#5 D3D9 / D3DX / shaders**: absent in `runtime/win32hle`; gates #21–#24.
- **#3 D3D8**: absent; gates #17–#20.
- **#9 multi-module execution**: `module.c` maps resource DLLs but does not run
  guest DLL code; gates #16–#21.
- **Original-Windows oracle + deterministic input/frame/audio harness**: no
  scenario capture exists on this host (see #145/#146/#147/#171). This is why
  no game can reach `PASS`, regardless of import binding or frame count.

## 5. Next actions, strictly ordered

1. User supplies authorised PC binaries/assets for #16–#21 and #24 (all
   currently `MISSING_INPUT`).
2. Implement shared backend gaps in their owning issues (#5, #3, #9) — never
   per-game shims in `runtime/win32hle`.
3. For #23, integrate the validated map names via existing metadata, then build
   the offline Stadium checkpoint/timer fixture (user-owned track).
4. For #15, capture matching original Mission 1 input+frame trace; the existing
   texture fix (`evidence/agent-p2-igi-texture/`) is retained as a narrow FIXED
   result, not a game-level PASS.
5. For #22, verify the edition/protection identity of the local ESWC executable
   before any further lifting; do not treat the 2019-timestamp build as the 2006
   retail release.

No issue was closed. No DRM circumvention, proprietary asset download, or
success-shaped API stub was introduced.
