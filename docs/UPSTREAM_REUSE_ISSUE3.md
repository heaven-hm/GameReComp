# Upstream reuse audit — issue #3 (D3D8 frontend + deterministic replay design)

Branch: `feature/upstream-issue-3` (worktree `…/opencode/upstream/issue3`).
Scope: D3D8 feasibility only. No gameplay, renderer, or original-vs-native
playability claim is made here.

## 1. Issue and ownership

`gh issue view 3 --repo heaven-hm/GameReComp --json body` — the issue assigns
this branch "D3D8 fixed-function/guest COM feasibility" plus deterministic
replay concepts, explicitly forbids a second renderer / browser runtime, and
requires API-conformance evidence rather than playability claims. Other
agents own D3D7 (#2), D3D9 (#5), D3D1–6 (#6), D3D9Ex/10–11 (#7) and fixtures
(#14). Only these files were changed here:

* `runtime/win32hle/d3d8.c` (new)
* `tests/test_d3d8.py` (new)
* `docs/UPSTREAM_REUSE_ISSUE3.md` (this file)

`host.c`, `d3d7.c`, `ddraw.c`, `win32hle.h` and other agents' files were
inspected read-only and were not modified.

## 2. Upstream provenance (pinned)

Repository: <https://github.com/swampodsarkar321/IGI-WEB> (default branch
`main`). Pinned commit: **`0a7472644f77e7287b3e478a06c47a47beae1e7e`**
("Fix IGI 2 branding, add Vercel deploy config + SEO (robots/sitemap/meta)",
SM TV, 2026-10-26T07:31:45Z). Licence: **MIT License**, Copyright (c) 2026
Dhaiwat Pandya (`LICENSE` at that commit). Access: available; no
`BLOCKED_BY_UPSTREAM_ACCESS`.

Files inspected (all at the pinned SHA, via `gh api …/contents/$f?ref=$SHA`):

| Path | Lines | Value for this issue |
|---|---:|---|
| `host/d3d8.c` | 978 | Guest-visible COM method order/arg counts for `IDirect3D8` (16 slots) and `IDirect3DDevice8` (97 slots); `w_Direct3DCreate8` export shape; host-side device state |
| `host/d3d8.h` | 93 | Resource/device object model, format constants, `DevState` (host side, not copied) |
| `host/com.c`, `host/com.h` | 107 | Guest COM object/vtable dispatch concept (different implementation from GameReComp's `hle_com_*`) |
| `host/sched.c` | 152 | Deterministic scheduler design |
| `host/render_rec.c`, `host/render.h` | 574 | Command recording / replay split |
| `host/netplay.c` | 291 | Lockstep input replay + periodic state hashes |
| `tests/difftest.py` | 316 | Original-x86-vs-recompiled differential test idea |
| `README.md` | — | Claimed D3D8→WebGPU translation, deterministic scheduler and lockstep design |

### D3D8 API semantics observed

* `Direct3DCreate8(UINT)` creates an `IDirect3D8` COM object. The host builds
  a vtable of named methods in the exact public D3D8 ABI order: 16 slots for
  `IDirect3D8` (`QueryInterface … GetAdapterMonitor, CreateDevice`) and 97 for
  `IDirect3DDevice8` (`TestCooperativeLevel … DrawTriPatch, DeletePatch`).
* The upstream host implements a full state-tracking renderer behind that
  interface: textures/surfaces/buffers with versioned guest memory, vertex and
  pixel shaders (fixed function lowered to WGSL), a command recorder for the
  game thread and a WebGPU executor for the page thread. These are host-side
  rendering concerns, not part of a guest COM ABI.
* Unsupported upstream methods return `S_OK` by default (`host/com.c`'s
  `NULL` impl log-once path), which hides missing functionality. GameReComp's
  frontend instead answers `D3DERR_NOTAVAILABLE` (0x8876086A).

### Deterministic replay design observed (reuse as concept only)

* `sched.c`: one participant runs until it blocks; a virtual clock advances
  to the earliest deadline when all are blocked, so host thread timing cannot
  leak into the simulation.
* `render_rec.c`: the game thread records self-contained commands; a separate
  executor runs them (immediately in native, per animation frame in browser).
* `netplay.c`: `Present` is the frame boundary; inputs are applied together on
  a fixed delay, and a hash of guest memory is exchanged every 60 frames to
  detect divergence.
* `difftest.py`: same random inputs for original x86 (Unicorn) and recompiled
  C, comparing registers and every byte of touched memory.

Decision: **do not port** these modules into native `win32hle`. The existing
GameReComp input pump/recorder and Unicorn harnesses remain authoritative;
this issue's contribution is the D3D8 guest ABI surface, with the above
recorded as the design reference for a later, scoped replay/bisection change.

## 3. Reuse versus copy

| Item | Disposition |
|---|---|
| D3D8 vtable order and argument counts | **Independent implementation of a public ABI contract.** The names/order are the DirectX 8 SDK interface; they were cross-checked against the pinned upstream tables and encoded in X-macros in `d3d8.c`. No upstream C text was copied. |
| `Direct3DCreate8` export shape | Implemented locally with the HLE stdcall macros (`RET(eax, argc)`); upstream's host `com_new`/`W32` code not used. |
| D3D8→WebGPU renderer, WGSL shaders, render command buffer, browser DOM/WASM memory, game engine code | **Rejected.** Would duplicate a renderer and violate the issue's explicit constraint. |
| IGI-WEB deterministic scheduler / lockstep / difftest | **Design reference only** (section 2); no source copied, no second scheduler added. |
| Shared OpenGL presentation path | **Reused, not duplicated.** `d3d7.c`/`ddraw.c` remain read-only owners of the SDL GL context and frame pump; `d3d8.c` only asks `hle_dd_mode()` for the shared display geometry. It never opens a window, binds GL, or presents a frame. |

## 4. What `runtime/win32hle/d3d8.c` provides

* `win32hle_d3d8[]` export table with `Direct3DCreate8`. Correct SDK-version
  check (`D3D_SDK_VERSION == 220`); wrong versions return NULL, as the real
  DLL does. A successful call returns a fresh object with one reference.
* `IDirect3D8`: 16-slot vtable. `QueryInterface` (IUnknown + IDirect3D8 only),
  `AddRef`, `Release`, `GetAdapterCount`, `GetAdapterModeCount`,
  `EnumAdapterModes`, `GetAdapterDisplayMode` are implemented; all nine other
  slots return `D3DERR_NOTAVAILABLE` with a correct stdcall purge.
* `IDirect3DDevice8`: 97-slot vtable in public ABI order. Only `QueryInterface`
  (IUnknown + IDirect3DDevice8), `AddRef` and `Release` are implemented; the
  other 94 methods — including `Present`, `Clear`, `DrawPrimitive*`, resource
  creation and shaders — return `D3DERR_NOTAVAILABLE`.
* Reference counting: `AddRef`/`Release` at object+4, `Release` frees at zero;
  `QueryInterface` with a wrong IID returns `E_NOINTERFACE` and clears the out
  pointer, NULL iid/out returns `E_POINTER`.
* The device object exists so a guest can inspect the second vtable; it owns
  no rasteriser. This is a COM frontend for feasibility/API fixtures, not a
  playable D3D8 device.

### Registration lines still needed (not applied: file ownership)

Add to `runtime/win32hle/win32hle.h`, next to the other module externs:

```c
extern const win32hle_shim win32hle_d3d8[];   /* Direct3DCreate8 (d3d8.c) */
```

Add to `runtime/win32hle/host.c`, in the startup registration block
(near `win32hle_register(win32hle_d3d7)` — note D3D7 is reached via ddraw):

```c
win32hle_register(win32hle_d3d8);
```

No other files need changing. `build_selftests.sh` picks up `d3d8.c`
automatically via `CORE=$(ls *.c | grep -v _selftest.c)`.

## 5. Verification evidence

Commands run from the worktree root (macOS, `cc`/clang, SDL2 present):

```sh
python3 -m pytest tests/test_d3d8.py -q        # 2 passed in 0.54s
python3 -m pytest -q                           # 172 passed, 2 skipped in 3.12s
(cd runtime/win32hle && ./build_selftests.sh)  # all 14 selftests ok
git status --short                             # only the 3 owned files
```

`tests/test_d3d8.py` compiles `d3d8.c` against a minimal fake HLE header and
drives all 113 vtable slots: it checks both vtable orders, per-method stdcall
stack purges, SDK-version handling, refcounts, IID discrimination,
`E_POINTER`/`E_NOINTERFACE`/`D3DERR_INVALIDCALL`/`D3DERR_NOTAVAILABLE`, and
that every unsupported method leaves refcounts unchanged. A separate static
check rejects foreign-runtime identifiers in code (comments may mention the
rejected designs).

Build artifacts (`__pycache__`, harness temp dirs, upstream scratch) were
removed after the runs; the worktree contains only the three owned files.

Status: **PASS** for the D3D8 guest COM frontend and its regression suite.
**No gameplay claim** — rendering, presentation and deterministic replay are
out of scope for this change.
