# Max Payne upstream audit — `phobos665/payne-recomp`

Audit date: 2026-11-23. Scope: inspect the upstream repository named in the
GameReComp Max Payne work items (#17/#18), record exact source paths and SHAs,
licence position, reusable MAX-FX/D3D8 findings and version caveats, and check
whether Max Payne executables exist locally. This document is an **audit of
upstream claims**, not a claim of local gameplay or parity.

## 1. Repository identity and pinned revisions

All upstream facts below were read with `gh api` / raw file fetches against the
pinned commit, not inferred from a local clone of the project.

| Item | Value |
| --- | --- |
| Upstream | <https://github.com/phobos665/payne-recomp> (public, **not** a fork) |
| Description | "Recompilation for Max Payne (Xbox)" |
| Default branch | `main` |
| Audited HEAD (commit) | `fbca3c4aa36adae6ba747104f6475a6d5d81cb85` — "chore(): pin DirectXbox-Recomp main a316171; mouse-look notes (parked) (#10)", authored 2026-11-02T20:39:48Z |
| Parent commit | `b9cbbc06e9a72412f1e0b96f8958783db54c765d` (tag `v0.4`) |
| Git tree of HEAD | `2bf779e089c8d63b24a1d9b1a37e073244b28222` (from the commit object; the recursive tree endpoint reports the ref SHA, so use the commit object's tree SHA) |
| Other branches | `chore/pin-toolkit-main` `d7e4120d39bfc263f4eec021d0b8d88921b816be`, `chore/sync-toolkit-nov` `f78127a4e5d37fe063d828ac22a62706c7fac879`, `feat/relift-toolkit-main` `1ca6f1a168ef66c038e53554954f3b461937e309` |
| Tags / releases | `v0.1` `4b9748a9eee26ac9620aa5f9f78322c2fe7aff7c` (2026-10-10), `v0.2` `cdb6c5d2bb2f25f8862c3e41cc23ed4a248a4612` (2026-10-10), `v0.3` `3db6ba294ec732c06d4f90685de7d5a8efa47a8c` (2026-10-19), `v0.4` `b9cbbc06e9a72412f1e0b96f8958783db54c765d` (2026-10-25) |
| Language / size | C; ~109 KB repository (no game code or data) |
| Submodule pin | `xboxrecomp` → commit `a316171d67341c9931da8883ec9ab61411790398` of <https://github.com/phobos665/DirectXbox-Recomp> ("fix(vulkan): declare t10 so the display resolve gets a pipeline (#116)", 2026-11-02), declared in `.gitmodules` |

Note: the same author also has a `phobos665/payne-recomp` entry in their user
repo list with a later `updated_at`; the repository metadata and HEAD above
are authoritative for the audited commit. `pushed_at` was 2026-11-02T20:39:51Z.

## 2. Exact source paths at `fbca3c4` (blob SHAs)

| Path (upstream) | Blob SHA | What it contains |
| --- | --- | --- |
| `README.md` | `7b9b6a3faf7300d4ca9b359a710c56b1790c2742` | Status table, build/play instructions, Widescreen, "A different disc", Legal |
| `CLAUDE.md` | `461a7ba10b311ddc6b9c4bd00aa0e26fb4c3fafe` | Title table, toolkit history, bug findings, pin note, developer mode, unattended runs |
| `CMakeLists.txt` | `69c605573cf40c61174cf1003a8ef5d6467fc912` | C/C++ project, generated sources, `recomp_title_widescreen(HOR_PLUS)`, launcher |
| `scripts/build.py` | `4b0497a644931fab9a37bf5f35d303a7d5f67d99` | XBE SHA-1 gate, lift + build orchestration, `config/xdk_symbols.json` copy |
| `config/seeds.json` | `d9018613dde5b7ec44f6353560b963f25545b105` | 11 function-entry seeds for the PAL XBE |
| `config/xdk_symbols.json` | `98fa8ea52c1fb41c630a33e2e08442a9872dad99` | 344 symbols: 157 D3D8, 129 DSOUND, 53 XAPILIB, 5 XGRAPHC |
| `src/main.c` | `195bbf07e7093b4edca2a517e2d162eb57e96eb9` | Generated-source host/template entry point |
| `src/recomp_manual.c` | `9d1860ab6f2b3b000f1d3c25db2743fb3b05eaff` | Hand-written overrides: widescreen camera, 2D-layer test, mouse dead zone |
| `docs/mouse-look.md` | `be20ebb37e7e37726251d538862fa1f8d9091e9e` | Static, parked investigation of mouse look (not implemented) |
| `.gitmodules` | `fde49c235c5dd3e15511b3430d23cc35f7060c7e` | Declares `xboxrecomp` submodule URL |
| `.gitignore` | `61ad0a27ee2140479388b22c8eac1de5f7dbf5d3` | Excludes game folder and generated `src/recomp/` |

Generated code (`src/recomp/gen/*.c`) is deliberately **not** committed: the
build lifts it from the user's own disc.

### Toolkit paths referenced by the upstream project (at pin `a316171`)

| Toolkit path (`phobos665/DirectXbox-Recomp`) | Blob SHA | Relevance |
| --- | --- | --- |
| `CLAUDE.md` | `5b2597b3b49250e077131416a729d41a14598ae4` | Toolkit architecture, debug table, switches (upstream tells readers to read it too) |
| `src/d3d/d3d8_xbox.h` | `b5aafbd6ea9ae5d961ef10435dcb236c4f906e28` | Declares `xbox_D3D8SetVerticalZoom`, `xbox_D3D8SetWideFramesAuto`, `xbox_D3D8SetScreenSpaceTest`, `xbox_D3D8ClaimHorPlus` |
| `src/input/input_bindings.c` | `d72aba91a6240ba31c3f3b157585ba69658780b0` | `recomp_mouse_move`, `look_axes`, `look_shape`, `add_look` |
| `docs/technical/cpp-exceptions.md` | `244f9e23dc996009599a2c64bdfa6583cfb999f5` | "Catching" — catching title C++ throws from guest EH tables |
| `docs/technical/input-binding.md` | `8a4ddeb6655b95c1d142416fb9a728f46c00c556` | "The mouse" — documented toolkit mouse mode (proposed, not implemented) |
| `docs/technical/widescreen-and-resolution.md` | `5e2a14926fe4a4639db9ae21f9e6a0d800844abd` | Hor+ / resolution design |
| `src/hle/d3d8_capture.h`, `src/replay/d3d8_replay.c` | `b10b28b041bb6668a4d6a2b40b7ac997702ed881`, `05e1fb01db100925382871feebed75c8cf49badf` | Frame capture and replay tooling referenced by upstream bug reports |

Other relevant toolkit files present at the pin (12-char SHAs):
`src/d3d/d3d8_states.c` `42cb7ca99b8e`, `src/hle/hle_d3d8_texture.c`
`fb4e43a66cce`, `src/hle/hle_d3d8.c` `053f19deadec`, `src/d3d/d3d8_movie.c`
`c1edb8f65e3f`, `src/d3d/d3d8_device.c` `027de023f255`.

## 3. Licence position

* **`payne-recomp` itself has no licence file.** `gh api
  repos/phobos665/payne-recomp/license` returns HTTP 404 and the repository's
  `license` field is `null`. Treat the docs and hand-written overrides as
  **all-rights-reserved**: no grant to copy source into GameReComp. The audit
  below records observations and ideas only; no upstream code is vendored.
* `README.md` "Legal": the user must own a copy of the game; do not share
  `game/`, `src/recomp/` (translated game code) or `build/`; the repository
  and toolkit contain no game code or assets. This is a strong indicator that
  the project is a *tooling + notes* repository, not a redistributable game.
* **The toolkit is GPL-3.0.** `phobos665/DirectXbox-Recomp` is a fork of
  `sp00nznet/xboxrecomp` and carries a GPL-3.0 `LICENSE`
  (`f288702d2fa16d3cdf0035b15a9fcbc552cd88e7`). Its `LICENSE.upstream-MIT`
  (`78282757baaab014afb0969574c47cf0ffedbbb0`) preserves MIT copyright
  "Copyright (c) 2026 sp00nz", and `LICENSES/README.md`
  (`5c53eebbb1dd5b3e99bac09ef7a053836535e0e8`) documents LGPL-2.1-or-later
  third-party files (MCPX APU sources and `src/nv2a/nv2a_regs.h`, from xemu).
  The upstream parent `sp00nznet/xboxrecomp` is reported as MIT. Practical
  consequence: **any reuse of the toolkit's D3D8 implementation triggers at
  least GPL-3.0 obligations from the fork, with MIT/LGPL-2.1 components in
  the tree**; attribute accordingly and do not assume the parent's MIT alone.
* MAX-FX itself is Remedy's proprietary engine (proprietary licence); no
  engine source or licence is present upstream. External background:
  PCGamingWiki lists MAX-FX and Direct3D 8 (`d3d8.dll`) for the PC game;
  Wikipedia/GameSpot describe MAX-FX as Remedy's in-house engine. None of
  this grants source access.

## 4. MAX-FX / D3D8 reusable findings

**Framing caveat first.** GameReComp issues #17/#18 concern the **Windows PC**
game and its MAX-FX/D3D8 (`d3d8.dll` COM) path. `payne-recomp` recompiles the
**Xbox PAL** build: XDK 4134, *plain D3D8 (not LTCG)*, static Xbox D3D8
symbols at fixed addresses, Bink movies. The Xbox and PC builds are different
binaries with different ABIs and addresses. Therefore these findings are
**engine-behaviour observations and test techniques**, not drop-in PC MAX-FX
offsets, PC `IDirect3DDevice8` shims, or proof of PC D3D8 parity.

Reusable items, with upstream evidence:

1. **Honour `TEXTURETRANSFORMFLAGS = DISABLE` for stage texture matrices.**
   The game leaves an environment-map matrix on stage 1 with transform flags
   disabled; the toolkit applied it anyway, doubling/flipping every base
   texture and making decals sample wrong lightmap texels (solid-black floor
   litter, over-bright graffiti). Evidence: `CLAUDE.md` "Toolkit bugs this
   game found"; relevant toolkit files `src/d3d/d3d8_states.c`,
   `src/hle/hle_d3d8_texture.c`. Reuse: gate transform application on state
   flags in any fixed-function D3D8 backend, and test lightmap/decal sampling
   as a distinct regression case.
2. **Dynamic video textures must be re-read even when the bind is unchanged.**
   The Bink player binds one 640x480 texture and rewrites its texels every
   frame; a renderer that only inspects texels on bind shows black movies.
   Evidence: `CLAUDE.md` "Movies were black"; toolkit `src/d3d/d3d8_movie.c`.
   Reuse: applies to any Bink-driven MAX-FX title, PC or Xbox.
3. **Frame capture + draw replay is the practical D3D8 oracle.** Upstream used
   `RECOMP_D3D8_CAPTURE`, then replay `--list-draws` and `--no-scene --draws N`
   to isolate the draw painting a region, replaying at the capture's
   `RECOMP_RES_SCALE`. Critical trap: the game never clears colour, so without
   `--no-scene` every partial replay shows the previous frame and looks
   complete. Evidence: `CLAUDE.md` "Found with a frame capture". Reuse: adopt
   the same capture/replay discipline for GameReComp D3D8 validation; do not
   treat a visually complete replay as evidence without `--no-scene`.
4. **Widescreen without a 16:9 game mode (Hor+).** The game never requests a
   widescreen flag; the fix widens the camera's field of view by 4/3 *in the
   tangent* through `sub_000D5DA0`, then crops added rows via
   `xbox_D3D8SetVerticalZoom`, marks 2D quads (HUD, menus, novel, text) with
   `xbox_D3D8SetScreenSpaceTest`, and shows full-frame 4:3 content (menus,
   loading, movies) between bars via `xbox_D3D8SetWideFramesAuto`. Evidence:
   `CLAUDE.md` "Widescreen", `src/recomp_manual.c`, `CMakeLists.txt`
   (`recomp_title_widescreen(HOR_PLUS)`), toolkit
   `docs/technical/widescreen-and-resolution.md`. Reuse: the pattern is
   engine-agnostic; the specific hooks and open issues (HUD still centred,
   novel panels, sniper scope, Drug Trip) are Xbox-specific and unresolved.
5. **Host-thread isolation and console memory reporting.** A 1-in-10
   start-up crash was *not* a host-thread race: the toolkit answered XAPI
   `GlobalMemoryStatus` with host free memory, and on macOS
   `GlobalMemoryStatusEx` was uninitialised stack. The game's particle factory
   requires >3.5 MB free and then dereferences a NULL. Fix: report half the
   console's 64 MB, always. A separate heap-lock bug had host and guest
   allocations colliding at `0x00F80000`. Evidence: `CLAUDE.md`
   "Start-up crashes". Reuse: architectural rule for any HLE host — never
   leak host heap state or host memory statistics into the guest, and lock
   shared allocators.
6. **Catch the title's own C++ exceptions via guest EH tables.** A level
   object in *Rooftops and Alleys 3* throws from `sub_002387A0`; without catch
   support execution resumed in the wrong function and faulted at
   `0x74000000`. The toolkit added catching from the title's EH tables.
   Evidence: `CLAUDE.md` (crash section), toolkit
   `docs/technical/cpp-exceptions.md`. Reuse: directly relevant to GameReComp
   issue #9 (dynamic multi-DLL C++/SEH); note this is Xbox EH tables, not
   Windows MSVC SEH across `e2mfc.dll` etc.
7. **Audio-adjacent, but reusable for the same engine family.** DirectSound
   stream format caches keyed by address must include the format (all disc
   sounds are Xbox ADPCM tag `0x69`, with mono and stereo lines sharing
   addresses); Bink's ~115 KB sound ring needed a service thread on its own
   clock rather than only feeding on game calls. Evidence: `CLAUDE.md`
   issue #3 notes. Reuse: relevant when validating MAX-FX cutscene audio, but
   not a D3D8 ABI finding.
8. **Input shaping and mouse look (parked, static only).** The mouse arrives
   as a right-stick speed; the shape is `rate = (1.5·s + 0.5)·(0.8·x)³` with
   constants at `0x3B9048`, `0x3B8D80`, `0x3D459C`; full deflection is 1250
   counts/s at sensitivity 80 and faster movement is clamped; auto-aim
   discards shaped look below ~85% near a target. Per-frame turn is written
   as `+0x25 = LookX·π·dt`, `+0x29 = LookY·π·dt` in `sub_0002E6D0`. Evidence:
   `docs/mouse-look.md`. Reuse: useful input-model insight, **but** the
   proposed direct-mouse fix is explicitly *not implemented, not run, not
   measured*; bullet-time behaviour is only "thought to" match PC Max Payne.
   Do not present it as a verified PC parity result.
9. **Regression harness design.** Developer mode is enabled by any file named
   `T:\EnableDeveloperMode.txt` in `game/TDATA/54540003/`, exposing 67
   "Preview Levels"; upstream validated each for 48 s with scripted input
   (`RECOMP_INPUT_SEQ`) and reported 68/68 clean runs on `a316171`, 60 fps in
   isolated levels. Evidence: `CLAUDE.md`, `README.md`. Reuse: a concrete
   scenario-oracle template for Max Payne, but it is an **upstream-authored
   run on the author's own disc**, not independently reproduced here.

## 5. Version caveats

* **Xbox PAL only.** Title ID `54540003`, certificate region `0x4`,
  `default.xbe` SHA-1 `3250db85c3b51d670436ae240f87d2490fd29509`, entry point
  `0x000BD2F5`, XDK 4134 plain D3D8 (not LTCG), Bink movies. PC MAX-FX
  (`d3d8.dll`) is a different build; none of the Xbox addresses, seeds,
  vtable slots or symbol offsets transfer to the PC target.
* **NTSC is explicitly unsupported.** Same title ID, different XBE; the
  toolkit's old NTSC seeds landed mid-instruction in the PAL binary. Other
  regions need their own `config/seeds.json` and `config/xdk_symbols.json`.
  `scripts/build.py` computes the XBE SHA-1 and **only warns** on mismatch —
  it does not refuse to lift, so a wrong disc can be relifted silently
  wrong.
* **The D3D8 replacement lift depends on a side file.** The toolkit lifts D3D8
  replacements only when `default_xdk_symbols.json` sits beside the XBE;
  `scripts/build.py` copies `config/xdk_symbols.json` there. No generated
  sources are committed; a review of this repo alone cannot reproduce a
  lift.
* **Toolkit pin discipline.** Upstream warns to keep the pin on a commit of
  toolkit `main`; feature-branch commits disappear when squash-merged. Pin
  `a316171` fixed a Vulkan display-resolve regression (#116); the previous
  pin `d7ee74e` produced a black window on every Vulkan host. Windows was
  last checked on an **older** toolkit, so current-build Windows parity is
  not established by the README.
* **Unverified scope, in upstream's own words:** no level played to its end,
  no save loading, no credits, no attract movies, sound not re-checked by
  ear; mouse-look work is static and parked. Widescreen open items include
  centred HUD, graphic-novel panels, sniper scope and Drug Trip distortion.
* **Legal/asset boundary:** user-supplied disc required; generated code,
  `game/`, and build output must not be distributed. Our audit reproduces no
  build and imports no code or data.

## 6. Local Max Payne executable verification

Checks run on this macOS host (2026-11-23):

* `find /Users/haseeb-mir/Documents/Code -maxdepth 6` for `*maxpayne*`,
  `*max_payne*`, `maxpayne*.exe`, `default.xbe` → only the audit worktree
  paths themselves; **no game binary**.
* `find /Users/haseeb-mir -maxdepth 8` (excluding caches/node_modules/.git)
  for `*.exe`, `*.xbe`, `*.iso` filtered for `payne|max|default.xbe` → only
  unrelated `MaxElementDiv.exe` in an old iCloud archive.
* `mdfind -name 'Max Payne'`, `mdfind -name 'maxpayne'`, `mdfind -name
  'default.xbe'` → no Max Payne executable; only symbol JSON files
  (`fci-recomp/re/symbols/default.xbe.json`) for an unrelated project.
* Executable inventory of the GameReComp workspace: `Doukutsu.exe` (fixture
  copies) and `pe32-minimal.exe` — **not** Max Payne; no `maxpayne.exe`,
  `e2mfc.dll`, or retail `default.xbe`.
* No mounted game volumes beyond the system/TimeMachine volumes.

**Conclusion:** there are no local Max Payne Windows executables, MAX-FX DLLs,
or Xbox game discs/XBEs. We cannot run the upstream harness, verify its
68/68 claim, or derive PC D3D8 binary evidence locally. All upstream runtime
claims remain author-reported.

## 7. Applicability to GameReComp (#17/#18) and next steps

* Adopt findings 1–3 and 5–6 as **design/test requirements** for the generic
  D3D8 backend and C++/SEH work; they are toolchain lessons backed by
  recorded upstream debugging, not proof of PC MAX-FX parity.
* Do **not** treat this repo as a PC MAX-FX source, an `e2mfc.dll` loader
  model, or a source of Windows game offsets. Its value is the list of
  rendering/audio failure modes and a scenario-harness template.
* Any future use of toolkit code must respect the GPL-3.0 fork licence plus
  MIT/LGPL-2.1 notices; the `payne-recomp` docs/overrides have no reuse grant.
* Required before closing #17/#18: an owned PC executable + disc, a `d3d8.dll`
  COM bridge, multi-DLL C++/SEH execution, and an original-Windows replay
  oracle. None of those preconditions are satisfied by this audit.
