# Upstream code provenance

This repository is a port/fork of PCRecomp. Every task in this session was
required to inspect upstream implementations before changing code. This file
records what was actually inspected, what was reused, and what was not.

## Reused code (copied or adapted)

| Component | Upstream repo | Upstream path / commit | Destination | Change | Licence |
|---|---|---|---|---|---|
| PE32 loader, guest memory, shim ABI, runtime registration | `https://github.com/sp00nznet/pcrecomp` | `runtime/win32hle/*`, local checkout HEAD `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | `runtime/win32hle/*` | baseline inherited; individual fixes were made in place | MIT, Copyright (c) 2026 sp00nz |
| D3D7 FVF stride table | `https://github.com/HenryKun55/igi-recomp` | `shim/src/d3d7.c`, commit `278eaf6394f7f42c7de6cffef2cf982154f59899` | `runtime/win32hle/d3d7.c` (branch `feature/upstream-issue-2`, commit `46dc1ce`) | adapted `fvf_stride` into `d3d7_fvf_stride`, added missing D3DFVF_PSIZE term | MIT, Copyright (c) 2026 HenryKun55 |
| CRT new-handler semantics | `https://github.com/sp00nznet/pcrecomp` | `runtime/win32hle/msvcrt.c`, same local HEAD | `runtime/win32hle/msvcrt.c` (branch `feature/agent-31-msvcrt`) | `_callnewh` stub replaced with real handler dispatch; new shims added | MIT |
| PE32 validation ideas | `https://github.com/sp00nznet/pcrecomp` | `runtime/win32hle/pe_loader.c`, same local HEAD | `runtime/win32hle/pe_loader.c` (branch `feature/agent-35-pe-validation`) | bounds checks added to existing loader, no second parser | MIT |
| Resource directory model | `https://github.com/sp00nznet/pcrecomp` | `runtime/win32hle/module.c`, same local HEAD | `runtime/win32hle/module.c` (branch `feature/agent-32-resources`) | validation added to existing tree walk | MIT |
| High-level adapter pointer return | `https://github.com/sp00nznet/pcrecomp` + rev.ng | existing bridge, local PCRecomp HEAD above | `tools/autorecomp/highlevel.py` (branch `feature/upstream-issue-1`, commit `808863ba`) | `RETP` used for pointer returns | MIT / rev.ng licence reviewed |

## Inspected but not copied (semantic references only)

| Upstream | Commit / version | Purpose | Reason no code copied |
|---|---|---|---|
| `https://github.com/wine-mirror/wine` | master, LGPL | Win32 message, COM, CRT, timer, resource, environment semantics | LGPL licence; independently written implementations preserving the same guest ABI |
| `https://github.com/libsdl-org/SDL` | 2.32.74 (local) | native window/event/audio host backend | already used as the host backend through existing `screen.c`/`sdl12.c` |
| `https://github.com/microsoft/DirectX-Headers` | master | public IID/GUID/vtable layouts | headers only, no runtime code needed |
| `https://github.com/revng/revng` | `0e2b25952231590f384cf6f77867db0b3d39b712` | provider discovery and candidate generation | existing `tools/autorecomp/revng.py` reused; no replacement provider |
| `https://github.com/swampodsarkar321/IGI-WEB` | `0a7472644f77e7287b3e478a06c47a47beae1e7e`, MIT | D3D8 public ABI and replay design | WebGPU/DOM/WASM host code not portable; independently written COM frontend |
| LLVM `llvm/lib/Object`, LIEF | master | PE validation strategy | Apache/LLVM and MIT references only; validation added to existing loader |
| `https://github.com/HenryKun55/igi-recomp` | `278eaf63…` | IGI front-buffer presentation (`shim/src/ddraw.c:front_buffer_show` → `render_blit` + `render_present`, `shim/src/render_gl.c:render_present`) | Adapted experimentally into GameReComp; see negative result below; change discarded |

## Negative result: IGI front-buffer presentation

Upstream IGI presents a front-buffer Blt even while a D3D7 device is active.
GameReComp suppresses its software presenter in that mode. The upstream
`front_buffer_show` behaviour was adapted into `ddraw.c:mark()` plus a new
`hle_screen_present_primary_gl()` helper, and the IGI PE32 was rebuilt and run
with a real Cocoa GL context. Result: both captured frames remained 100% black
(`PIL` distinct-colour count 1, extrema `(0,0)-(0,0)`), identical to the
unmodified build. The change was therefore **not committed**; it is not a fix.
The blocker remains open and is recorded in `test-results/game-igi/`.

## Rules applied

- No new PE loader, graphics/audio/input backend, CRT, COM implementation or
  decompiler was written where existing upstream or repository code existed.
- Platform-specific Vita/Android/WebGPU code was not transplanted.
- PE32+ and Win16/NE work remains deferred under the project roadmap.
- No game binaries, proprietary assets or copyrighted data were committed.
