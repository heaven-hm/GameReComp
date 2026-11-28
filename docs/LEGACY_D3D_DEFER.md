# Issue #6 — legacy D3D1–6 / Glide: DEFERRED

Issue #6's completion section allows: "If no compatible fixture, document API gap as **DEFERRED**, not 'supported'."

- **Demand scan**: `reports/games/game-target-issues-15-24.json` mentions only "#2 D3D7 / Glide renderer path unverified"; no owned PE32 game in `evidence/corpus/` imports `d3d1.dll`–`d3d6.dll`, `glide.dll`, or `3dfx`. `ddraw.dll` appears in 60 corpus evidence files, which belongs to the existing DirectDraw/DDraw7 path, not a legacy D3D fixture.
- **Runtime state**: `runtime/win32hle/ddraw.c` and `runtime/win32hle/d3d7.c` exist; no legacy D3D1–6 or Glide COM frontend is registered. `docs/GRAPHICS_DEFER_MATRIX.md` already records the later-API defer under #7.
- **Decision**: `DEFERRED` with explicit unsupported-call diagnostics, not a supported claim. No second renderer, no copied Glide DLLs, no game assets. Reopen when a licensed Windows 95/98 game fixture with a golden framebuffer exists.
- **Limits**: a DDraw import is not evidence of D3D1–6 or Glide demand; one passing draw would be a fixture pass, not a playable game. No universal PASS or gameplay equivalence is claimed.
