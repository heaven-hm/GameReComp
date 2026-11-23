# AG-C graphics frontend audit — provenance

Scope: `runtime/win32hle/ddraw.c`, `d3d7.c`, `d3d8.c`, `d3d9.c` and the shared
presentation path (`present.c`, `screen.c`). Worktree `feature/maxcomp-c-graphics`
based on `cb4dc0441199af0fc15d0100a565bd8e444a228e`.

## What was consulted

| Source | Exact revision / locator | What it establishes |
|---|---|---|
| `wine-mirror/wine`, `include/d3d9.h` | commit `97027f879ba9a36dbe594720d4710818536e5433` (already pinned in the header comment of `d3d9.c` and in `docs/D3D9_FRONTEND_REUSE.md`) | `IDirect3D9` method order and signatures. In particular `GetAdapterModeCount(UINT Adapter, D3DFORMAT Format)` and `EnumAdapterModes(UINT Adapter, D3DFORMAT Format, UINT Mode, D3DDISPLAYMODE *pMode)` — both have one more argument than their D3D8 counterparts. |
| `microsoft/DirectX-Headers`, `include/dxguids/dxguids.h` | `main`, git blob `dfe7def1074e3d87578ff3aeb488d4bb184b6f4c` (`https://raw.githubusercontent.com/microsoft/DirectX-Headers/main/include/dxguids/dxguids.h`) | The Microsoft header collection's public IID/GUID conventions and its explicit "IIDs are stable and cannot change" statement. This collection does not ship the legacy `d3d9.h`/`ddraw.h`, so the D3D9 vtable layout was taken from the Wine header above; no header source was copied into the runtime. |
| Repo selftests | `runtime/win32hle/d3d9_selftest.c`, `d3d8_abi_selftest.c`, `tests/test_d3d8.py` | Existing ABI fixtures: vtable names/slots, IUnknown HRESULTs, device creation, shared display mode. |
| Repo audit docs | `docs/TEN_HOUR_AUDIT_RECOVERY_FINAL.md`, `docs/D3D9_FRONTEND_REUSE.md`, `docs/D3D9_UPSTREAM_AUDIT.md` | Prior fixes already cover D3D8/D3D9 module registration (`host.c`) and DirectDraw7 `DDCAPS.ddsCaps`; those are not re-fixed here. |

## Defect found (D3D9 adapter/mode discovery)

`d3d9.c` implemented the D3D9 object's mode queries as if they were the D3D8
ones:

1. `GetAdapterModeCount` used `RET(..., 2)` after reading only the `Adapter`
   argument. The D3D9 signature has two arguments after `this` (`Adapter`,
   `Format`), so the callee must pop **three** words (synthetic return address
   plus two arguments). Every call leaked one word of guest stack. A D3D9
   title's display-mode enumeration loop drifts ESP by 4 bytes per call and
   eventually corrupts its own frame.
2. `EnumAdapterModes` checked `A32(2) != 0` and returned `D3DERR_INVALIDCALL`.
   In D3D9 `A32(2)` is the requested `D3DFORMAT`, not the mode index; real
   callers pass `D3DFMT_X8R8G8B8` (22) or `D3DFMT_R5G6B5` (23), so **every**
   mode-enumeration call failed and the guest saw no display modes at all. The
   actual mode index (`A32(3)`) was never validated.

D3D8's `EnumAdapterModes(Adapter, Mode, pMode)` genuinely has no `Format`
slot, which is why copying its validation into D3D9 was wrong. `ddraw.c`,
`d3d7.c`, `d3d8.c` and the shared presentation path were audited and left
unchanged: their registration and caps defects were already repaired in the
base revision.

## Correction

In `runtime/win32hle/d3d9.c`:

- `d3d9_d3d_get_mode_count` now pops three words and accepts `D3DFMT_UNKNOWN`
  (0) or the one format the shared display reports.
- `d3d9_d3d_enum_modes` validates the real D3D9 slots (`Adapter` at `A32(1)`,
  `Mode` at `A32(3)`), accepts `D3DFMT_UNKNOWN` or the reported format, and
  still pops five words on every path.
- A shared `d3d9_display_format()` helper keeps `GetAdapterDisplayMode`,
  `GetAdapterModeCount` and `EnumAdapterModes` consistent with `hle_dd_mode()`.

In `runtime/win32hle/d3d9_selftest.c`:

- Mode queries are now called with their full D3D9 argument lists.
- A deterministic `shim_pops()` helper dispatches the registered shim directly
  with a synthetic guest stack, so a wrong stdcall purge is visible in `g_esp`
  (normal `hle_call_guest` restores registers and hides it). It asserts 4 words
  for `GetAdapterModeCount` and 6 for both the success and failure paths of
  `EnumAdapterModes`.
- IID/vtable/refcount coverage and all 17 + 119 slot checks are unchanged.

## Evidence

- Before the fix (original `d3d9.c`, new selftest):
  `FAIL GetAdapterModeCount pops ret + 3 args`, `FAIL EnumAdapterModes(real format)`,
  `FAIL enumerated mode format`; exit 1.
- After the fix: `d3d9_selftest: all checks passed`; exit 0.
- Build/run command (macOS, no CMake/Rust build):
  ```sh
  cd runtime/win32hle
  sh -c 'CORE=$(ls *.c | grep -v _selftest.c); cc -O1 -Wall \
    -Wno-unused-function -Wno-unused-but-set-variable -I../recomp32 -I. \
    $CORE d3d9_selftest.c $(pkg-config --cflags --libs sdl2 SDL2_ttf) \
    -lpthread -lm -o /tmp/d3d9_selftest'
  SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy /tmp/d3d9_selftest
  ```

## Limits

- This is an ABI/frontend correction only. `d3d9.c` still has no rasteriser:
  device methods return `D3DERR_NOTAVAILABLE`, and no game rendering is claimed.
- One virtual adapter and one mode are reported, in the format derived from
  `hle_dd_mode()`; unusual formats are honestly refused.
- No live game or Wine run was performed. The evidence is a deterministic
  selftest against the public D3D9 ABI, not gameplay validation.
- `host.c` / `win32hle.h` were not touched; module registration remains the
  lead's responsibility.
