# Hitman upstream audit

Audit date: 2026-11-23
Scope: `https://github.com/0danny/re47` and `https://github.com/ReGlacier/ReHitman`,
inspected read-only with `gh`/GitHub REST API. No upstream source was copied into
this repository, and no upstream build was performed.

## Verdict

**Status: BLOCKED — no Hitman Windows game files are present in this workspace.**

| Gate | Status | Evidence |
|---|---|---|
| Retail Hitman PE32/PE32+ executable or DLL available locally | **BLOCKED** | Filesystem audit below; only unrelated test binaries exist |
| Licensed/owned game files supplied via path or env var | **BLOCKED** | No `data/` directory, no user-supplied path, no Wine prefix |
| Upstream source inspected | PASS | Exact SHAs and file paths recorded below |
| Upstream licence grants code reuse | **BLOCKED** | Neither repository contains a `LICENSE`/`COPYING` file; both `/license` API lookups return 404 |
| Upstream source portable to this macOS host | **BLOCKED (as-is)** | Both projects are Windows/x86 + MSVC/DirectX only |
| Live-game or binary verification possible | **BLOCKED** | Depends on the two blockers above |

Consequence: Hitman targets #16, #19, #20 and #21 remain `NOT TESTED` /
`BLOCKED: no Windows game files in workspace` in `docs/GAME_MATRIX.md`. This
audit does not change that verdict; it only records what upstream material
exists and why it cannot yet be used.

## 1. Local game-file verification

Commands run (working directory: worktree root):

```
find . -type f \( -iname "*.exe" -o -iname "*hitman*" -o -iname "*bloodmoney*" -o -iname "glacier*.dll" \) | grep -v '/.git/'
find /Applications "$HOME/Applications" "$HOME/.wine" "$HOME/Library/Application Support" /Volumes -maxdepth 4 -iname '*hitman*'
which wine wine64
ls -la data/
```

| Check | Result |
|---|---|
| Hitman executable in worktree (tracked or untracked) | none |
| Hitman/BloodMoney/Glacier-named DLL in worktree | none |
| `Hitman.exe`, `HitmanBloodMoney.exe`, `System.dll`, `RenderD3D*.dll` anywhere under `/Users/haseeb-mir/Documents/Code/GameReComp` | none |
| Hitman installation under `/Applications`, `~/Applications`, `~/.wine`, `~/Library/Application Support`, `/Volumes` | none |
| Wine on `PATH` | `wine not found`, `wine64 not found` |
| Local `data/` game-asset directory | does not exist |

The only PE images found are unrelated fixtures:
`tests/fixtures/pe32-minimal.exe`, `.revng-in/Doukutsu.exe` and
`highlevel/doukutsu/.revng-project/Doukutsu.exe`. None is a Hitman binary and
none may be substituted for one.

This matches the repository's own acceptance records: `docs/GAME_MATRIX.md`
rows 16, 19, 20 and 21 already mark Hitman 1/2/Contracts/Blood Money as
blocked for want of Windows game files.

## 2. Upstream A — `0danny/re47` (Hitman: Codename 47, 2000)

### Repository identity and version

| Field | Value |
|---|---|
| Description | "Hitman Codename 47 (2000) decompilation project." |
| Default branch | `main` |
| HEAD commit inspected | `e5a15bb85a38417e84466d50584131d137910fe0` — "Add note to README", 2026-07-29T08:15:46Z |
| Last push / repo update | 2026-07-29T08:15:47Z / 2026-11-18T20:48:27Z |
| Archived | yes (README: "THIS PROJECT IS NO LONGER BEING WORKED ON") |
| Created | 2024-09-12 |
| Releases / tags | none |
| Language bytes | C++ 387,413; CMake 7,194; Batch 466 |
| Licence file in tree | **none** (tree search for `LICENSE`/`COPYING`/`NOTICE`: empty) |
| GitHub detected licence | none (`repos/0danny/re47/license` → 404) |

README-declared progress: 10,055 lines decompiled. `hitman.exe`,
`directplay.dll`, `globals.dll`, `systemprobedll.dll` 100%; `system.dll` 50%;
`renderopengl.dll`, `enginedata.dll`, `hitmandlc.dlc` 0%; `locale.dll`,
`renderd3d.dll`, `render3dfx.dll`, `sound.dll` untouched.

### Exact supporting source files (paths at HEAD `e5a15bb…`)

| Path | Role for a Hitman 1 reconstruction | Last commit touching file |
|---|---|---|
| `src_main/hitman/main.cpp` | `WinMain`, mutex `Hitboy`, `LoadLibraryA("System.dll")`, `@<ini>` argument, signal handlers → describes the required PE entry/DLL load order | `5c4643de6119560b763f7f0c3ec6918b4ad1c74a`, 2024-11-11 |
| `src_main/system/zsysinterface.h`, `src_main/system/zsysinterface.cpp` | `g_pSysInterface` with `m_soundDLLBase`/`m_drawDLLBase` — engine-to-DLL surface | same tree, HEAD `e5a15bb…` |
| `src_main/zstdlib/zdynamicloader.cpp`, `.h` | `ZDynamicLoader` (`CrashFree`) used by the above for dynamic module loading | `7b8c425c4144b0f21f7605aae501975822412eba`, 2024-12-22 |
| `dll_defs/system.def`, `directplay.def`, `globals.def`, `systemprobe.def` | exported-symbol contracts of the four 100% modules | `system.def`: `3677e9e5cdc201cd737efc807dd6cce861148e58`, 2024-10-05 |
| `src_main/zstdlib/reftab/*`, `trees/*`, `zstdlib/input/*`, `zstdlib/zconsole/*` | Glacier "Z" STL reconstructing the engine's containers/input/console | same tree, HEAD `e5a15bb…` |
| `src_main/zstdlib/fszip.cpp`, `.h`, `cfastlookup2.cpp`, `zmalloc.cpp` | retail archive/compression and allocator semantics | same tree, HEAD `e5a15bb…` |
| `src_main/directplay/*`, `src_main/systemprobe/*`, `src_main/system/*`, `src_main/globals/*`, `src_main/drawing/zcounter.cpp` | the decompiled modules named in the README | same tree, HEAD `e5a15bb…` |
| `src_testing/impl_swap/*` (incl. `menu/mods/freecam.cpp`) | testing-only injected DLL/resource swap harness, not game code | same tree, HEAD `e5a15bb…` |
| `CMakeLists.txt`, `build.bat`, `resources/hitman.rc`, `resources/hitman_icon.ico` | build glue reproducing the Windows targets | same tree, HEAD `e5a15bb…` |

In-tree third-party material (inspection only, do not transplant):
`external/include/directx7/*.h` (D3D7/DDraw/DInput/DSound/DPlay headers),
`external/include/glide3/*.h`, `external/include/minhook.h`,
`external/include/zlib/*.c` (old zlib), and prebuilt Windows import libraries
in `external/libs/*.lib` (`d3dx.lib`, `ddraw.lib`, `glide3x.lib`,
`minhook_x86.lib`, …).

### Licence and reuse terms

No licence file and no detected SPDX licence. The README disclaimer states:

> "This project is for educational and game preservation purposes only and is
> not for commercial use. The repository does not contain any game assets/retail
> resources or proprietary code from the original game. The files built using
> this repository may only be utilized with assets provided by ownership of
> Hitman Codename 47 (2000). This project is in no way associated with or
> endorsed by IO Interactive."

That is a usage restriction, not a grant to copy the reconstructed source into
GameReComp. Treat this repository as **inspection-only** until the authors
grant a licence. No re47 file has been copied into this worktree.

### Portability

Not portable as-is to the macOS host:

- `build.bat` requires `VS6_BIN_PATH`, calls `VCVARS32.BAT`, generates NMake
  makefiles; README requires the portable Visual Studio 6 SP2 toolchain.
- `CMakeLists.txt` sets `/W3 /GX /D WIN32 /D _WINDOWS`, `/machine:I386`,
  `project(HitmanC47 C CXX RC)`, `.def` exports and a Win32 resource script.
- `src_main/hitman/main.cpp` uses `windows.h`, `WinMain`, `HINSTANCE`,
  `CreateMutexA`, `LoadLibraryA`, `CreateFileA`.
- Dependencies are Win32-only: DirectX 7, Glide3, MinHook x86, prebuilt `.lib`.
- Reverse engineering assumed IDA Pro; GOG (DRM-free) copy of the retail game
  is the reconstruction reference (retail has SafeDisc v2.0). No debug symbols.

Useful as *sem evidence only*: module names, export lists, load order, `@ini`
startup convention and Glacier container behaviour. Any GameReComp
implementation must be independently written and run in the existing
PE32/win32hle harness.

## 3. Upstream B — `ReGlacier/ReHitman` (Hitman: Blood Money, 2006)

### Repository identity and version

| Field | Value |
|---|---|
| Description | "Hitman Gen 1 Reverse Engineering Project"; README: attempt to reverse "Hitman: Blood Money" for a Mafia 2-style multiplayer |
| Default branch | `main` (also `dev/*` branches and `rehitman-website`) |
| HEAD commit inspected | `dbed330a4bd83ace9429715477bf97cc2e965950` — "Fix SceneViewer tree collapsing and missing ImGui::End() in TEST window (#11)", 2026-05-23T13:54:53Z |
| Last push / repo update | 2026-10-06T15:24:08Z / 2026-11-08T09:10:41Z |
| Archived | no |
| Created | 2020-10-04 |
| Target build | last IOI release, executable string `blood-build3-20060616-26123`, `HitmanBloodMoney.exe` |
| Language bytes | C++ 942,888; CMake 4,453 |
| Licence file in tree | **none** |
| GitHub detected licence | none (`repos/ReGlacier/ReHitman/license` → 404) |

Releases (version evidence):

| Version | Date | Asset |
|---|---|---|
| `v1.1.0` ("Test release") | 2026-06-07 | `ReHitman-v1.1.0.zip`, 266,883 bytes |
| `1.0.35` ("Bugfix for previous release") | 2021-02-21 | `ReHitman.zip`, 308,218 bytes |
| `1.0.2` ("First public release") | 2020-11-30 | `ReHitman.zip`, 304,768 bytes |

Release/install description: unpack `NVCPL.dll` and `ReHitman.dll` into the
game folder and launch `HitmanBloodMoney.exe`. These are injector mods, **not**
a playable game binary — the retail executable and assets are still required.

### Exact supporting source files (paths at HEAD `dbed330…`)

Sample per-file last-commit SHA for the three representative files below:
`761e24698e18ab17a38ef4bdb61b37cdab62821d`, 2023-04-04 — the same commit that
last touched the Glacier/BloodMoney headers in the inspected tree.

| Path | Role | Last commit touching file |
|---|---|---|
| `ReHitman/DLL/source/ReHitmanDLL.cpp`, `ReHitman/DLL/source/ReHitman.cpp` | injected DLL entry (`DllMain` → worker thread → `ReHitman::Core::EntryPoint`) | `761e24698e18ab17a38ef4bdb61b37cdab62821d` |
| `ReHitman/Glacier/include/Glacier/ZRenderWintelD3D.h` (~24 KB), `ZRenderDrawD3D.h`, `ZRenderEntryGeomD3D.h`, `ZDirect3D9Device*`-adjacent types | Glacier 1 renderer ABI (D3D9), the "reverse the rendering API" goal | `761e24698e18ab17a38ef4bdb61b37cdab62821d` |
| `ReHitman/Glacier/include/Glacier/CCom.h` (~109 KB), `Glacier.h`, `GlacierFWD.h`, `Win32FWD.h` | Win32/COM-facing engine declarations | same tree, HEAD `dbed330…` |
| `ReHitman/Glacier/include/Glacier/ZSTL/*` (`REFTAB.h`, `LINKREFTAB.h`, `ZMath.h`, `MYSTR.h`, `ZRTTI.h`, …), `Glacier/source/ZSTL/ZHashBase.cpp` | Glacier container/RTTI/math vocabulary; overlaps re47's Z STL | same tree, HEAD `dbed330…` |
| `ReHitman/Glacier/include/Glacier/ZSysFileWintel.h`, `ZSysInputWintel.h`, `ZSysInterfaceWintel.h`, `Glacier/source/G1ConfigurationService.cpp` | file/input/configuration service surface | same tree, HEAD `dbed330…` |
| `ReHitman/Glacier/source/{CCom.cpp,CConfiguration.cpp,ZEngineDataBase.cpp,ZCameraSpace.cpp,Geom/ZGEOM.cpp,Fysix/CRigidBody.cpp}` | concrete Glacier engine reconstructions | same tree, HEAD `dbed330…` |
| `ReHitman/BloodMoney/include/BloodMoney/Engine/ZDirect3DDevice.h`, `ZHM3Camera*.h`, `BloodMoney/source/Delegates/DX9Delegate.cpp` | Blood Money-specific D3D9/camera/delegate layer | header: `761e24698e18ab17a38ef4bdb61b37cdab62821d` |
| `ReHitman/BloodMoney/source/Patches/All/ZDirect3D9DevicePatches.cpp`, `CommonPatches.cpp`, `RenderScenePatch.cpp` | runtime patch samples for the retail DLL | same tree, HEAD `dbed330…` |
| `ReHitman/Modules/HF/include/HF/HackingFramework.hpp`, `Modules/ImGui/*`, `NVCPL/Source/NVCPL.cpp` | hooking/ImGui/NVCPL side-loader support modules | same tree, HEAD `dbed330…` |
| `ReHitman/CMakeLists.txt`, `.github/workflows/build.yml` | build/CI evidence for portability | same tree, HEAD `dbed330…` |

Submodules (not vendored): `fmt`, `spdlog`, `ocornut/imgui`, `google/googletest`.

### Licence and reuse terms

No `LICENSE`/`COPYING`/`NOTICE` anywhere in the default-branch tree, no GitHub
detected licence, and the README contains no licence statement (only credits
and a Discord link). **Unlicensed — inspection-only.** Do not import its
headers or source into GameReComp without an explicit grant from the authors.

### Portability

Not portable as-is:

- `ReHitman/ReHitman/CMakeLists.txt` sets C++20, `_CRT_SECURE_NO_WARNINGS`,
  `CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS`, builds `ReHitman.dll` and links D3D9/
  ImGui-DX9 targets; `ReHitman/CMakeLists.txt` carries the TODO
  "Check that we will compile only under x86!".
- `ReHitman/DLL/source/ReHitmanDLL.cpp` includes `<Windows.h>` and has
  `#ifndef _WIN32 / #error "Supported only x86"`.
- `ReHitman/Glacier/include/Glacier/Win32FWD.h` and the `Wintel`-named types
  encode Win32 ABI assumptions.
- `.github/workflows/build.yml` runs `windows-2025-vs2026` with
  `cmake -A Win32 -G "Visual Studio 18 2026"`, uses MSBuild, and packages
  `ReHitman.dll` + `NVCPL.dll` artifacts.
- It is a runtime injector requiring the retail `HitmanBloodMoney.exe`; it is
  not a standalone recompilation and supplies no game files.

Useful as *ABI/class-layout evidence only*: Glacier 1 type names, D3D9 render
wrapper shape, Blood Money build string and patch points.

## 4. Reuse decision

- No source file, header, `.def`, `.lib` or binary from either repository was
  copied into this worktree. This document is the only changed file.
- Neither upstream repository solves the actual blocker: both assume you
  already own the retail Hitman binaries and assets. They are references for
  symbol names, module boundaries and ABI layout, not a source of game files.
- Because neither project is licensed, even a future copy requires an explicit
  licence from the respective authors. Until then, any GameReComp work inspired
  by them must be independently implemented against the repository's own
  PE32/win32hle harness and recorded as "inspected, not copied" in
  `docs/UPSTREAM_CODE_PROVENANCE.md` if used.

## 5. Conditions to unblock

1. Supply the retail Windows game files through an environment variable or
   command-line path (never commit them; see repository `AGENTS.md`): Hitman:
   Codename 47 (GOG's DRM-free build is the upstream reference) for issue #16,
   and/or `HitmanBloodMoney.exe` build `blood-build3-20060616-26123` for
   issue #21. Hitman 2/Contracts need their own executables.
2. Confirm the licence/ownership of those files and the intended verification
   workflow (native PE32 load through the win32hle runtime, with multi-DLL
   loading and D3D7/D3D9 shims as applicable).
3. Obtain explicit upstream licences before transplanting code; otherwise keep
   using upstream only as semantic references.
4. Re-run this audit's local-file checks and the game acceptance harness; only
   then change the `docs/GAME_MATRIX.md` verdicts away from BLOCKED.

## Appendix — exact commands used

```
gh repo view 0danny/re47 --json name,description,licenseInfo,defaultBranchRef,createdAt,updatedAt,pushedAt,isArchived,url
gh repo view ReGlacier/ReHitman --json name,description,licenseInfo,defaultBranchRef,createdAt,updatedAt,pushedAt,isArchived,url
gh api repos/{repo}/commits/main --jq '{sha,date,message,author}'
gh api repos/{repo}/git/trees/main?recursive=1 --jq '.tree[] | "\(.type)\t\(.size // 0)\t\(.path)"'
gh api repos/{repo}/license --jq '.license.spdx_id'        # both → 404
gh api repos/{repo}/git/trees/main?recursive=1 --jq '.tree[] | select(.path|test("LICEN[SC]E|COPYING|NOTICE";"i")) | .path'   # both → empty
gh api repos/{repo}/contents/CMakeLists.txt --jq '.content' | base64 -d
gh api repos/{repo}/readme --jq '.content' | base64 -d
gh api repos/ReGlacier/ReHitman/releases --jq '.[] | {name,tag_name,published_at,assets}'
gh api "repos/{repo}/commits?path=<full tree path>&per_page=1" --jq '.[0] | [.sha,.commit.committer.date] | @tsv'
```
