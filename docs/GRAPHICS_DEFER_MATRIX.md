# Issue #7 — D3D9Ex / D3D10 / D3D11 DEFER matrix

Issue #7 accepts a published DEFER with a reproducible cost/compatibility
matrix when game demand and a GPU backend do not justify D3D10/11.

- **Demand**: corpus scan (`reports/local-games-inventory.json`,
  `reports/all-windows-binaries.json`, `reports/games/game-target-issues-15-24.json`)
  finds d3d10/d3d11/dxgi only as Wine system DLL paths and notes, not as imports
  of an owned PE32 game. D3D9 itself has no HLE backend yet.
- **Cost**: guest COM object registry, shader bytecode translation with an
  independent licence, DXGI swapchain and a versioned host renderer are all
  absent from `runtime/win32hle` and `tools/autorecomp/projectgen.py`.
- **Compatibility**: no renderer fixture, no licensed later-era game scenario,
  no original-vs-native frame evidence.
- **Verdict**: **DEFER**. Reproduce with
  `python3 tools/graphics_defer_matrix.py --evidence proof/graphics/d3d10-defer-evidence.json --json-out /tmp/out.json`.

No universal support, rendering PASS, or gameplay equivalence is claimed.
