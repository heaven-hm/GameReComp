# Upstream compatibility reuse audit

This audit records the reusable DirectDraw/Direct3D 7 and DirectMusic compatibility work reviewed for issue #2. It does not claim that the upstream game is independently verified by GameReComp.

## Pinned source

- Repository: [HenryKun55/igi-recomp](https://github.com/HenryKun55/igi-recomp)
- Commit reviewed: `278eaf6394f7f42c7de6cffef2cf982154f59899`
- License: MIT, copyright HenryKun55 (2026). The license text is preserved at [LICENSES/igi-recomp-MIT.txt](../LICENSES/igi-recomp-MIT.txt).
- Relevant files inspected: `shim/src/ddraw.c`, `shim/src/d3d7.c`, `shim/src/render_gl.c`, `shim/src/render_backend.c`, `shim/src/render_queue.c`, `shim/src/ole32.c`, and `shim/include/ddraw_priv.h`.

## Runtime boundary and reuse decisions

GameReComp keeps its existing PE loader, PCRecomp dispatch, guest memory, `hle_ptr`/`hle_va`, Win32HLE COM objects, project generator, and SDL screen. Upstream's guest memory, engine state, render thread, game-fix layer, and recompiler are not imported.

| Compatibility area | Existing GameReComp path | Reuse/adaptation in this branch |
|---|---|---|
| DirectDraw 1–3 surfaces | `runtime/win32hle/ddraw.c`: system-memory surfaces, blit/flip, palette and clipper handling | Preserve the software surface model; add DirectDraw4/7 vtables and Surface4/7 slots and present primary pixels through the shared SDL OpenGL context. |
| Direct3D 7 | Previously absent; `gl32.c` only forwarded OpenGL exports | `runtime/win32hle/d3d7.c` adapts fixed-function device operations to the existing guest ABI and SDL-owned OpenGL context. The upstream renderer was inspected for behavior, but not copied wholesale because it depends on upstream-specific engine state, render queues, guest-memory helpers, and game-fix hooks. |
| DirectMusic class activation | Existing `ole32.c` returned class-not-registered when no host class matched | Adapt the generic `CLSID_DirectMusic` COM façade and interface method layout from upstream `shim/src/ole32.c` to GameReComp's COM ABI. Device/playback methods explicitly return `E_NOTIMPL`; this is a compatibility façade, not a DirectMusic audio implementation. |
| OpenGL window lifetime | Existing SDL screen owned an SDL renderer | Extend the same screen/window to own an OpenGL context for D3D7, and use that context to present DirectDraw primary pixels. No second context or game-specific renderer is introduced. |

## Evidence limits

Upstream source is evidence that these compatibility paths exist in that repository, not proof of GameReComp playability. The local IGI executable is used only as an integration target. Its report and runtime trace are kept in the user-provided IGI directory; an exit code alone does not establish a playable scenario or correct rendered frames.
