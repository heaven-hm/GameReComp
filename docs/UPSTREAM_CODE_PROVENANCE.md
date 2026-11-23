
## Upstream PCRecomp mouse-edge mapping (PR #55)



- **GameReComp base before this change:** `develop` at
  `f32b4168f8c4485f183177d1ac63a959d1a201ad` (working tree had unrelated
  uncommitted issue #14 tooling; it was left untouched).
- **Upstream repository:** `https://github.com/sp00nznet/pcrecomp` (MIT,
  Copyright (c) 2026 sp00nz).
- **Local upstream before fetch:** `a85ea970f9e0640ab6546d2ca7a01fccb132973d`.
- **Latest inspected upstream commit:** `a67767bed6ee1085fc6614f34c80ebfc25908f72`
  (`win32hle screen: the mouse reaches the picture's right and bottom edges (#195)`,
  Chris Pressland).

## Imported/adapted change

| Field | Value |
|---|---|
| Upstream commit | `a67767bed6ee1085fc6614f34c80ebfc25908f72` |
| Original source | `runtime/win32hle/screen.c`, `to_game()` |
| Destination | `runtime/win32hle/screen.c`, new `hle_screen_map_point()` + `to_game()` wrapper |
| Method | Adapted, not blind-copied: GameReComp's `g_dst` is computed from renderer pixels, while SDL events arrive in window points. The fix keeps both coordinate spaces in a pure helper so the edge rule can be tested without a display. |
| Licence / attribution | MIT; upstream author and commit recorded in the source comment and this file |
| Dependencies | existing SDL2 window/renderer code only; no new dependency |
| Regression test | `runtime/win32hle/screen_map_selftest.c` (last point reaches right/bottom edges, first point stays 0, past-edge clamps, 1x path) |
| Tests run | `bash runtime/win32hle/build_selftests.sh` → all selftests ok including `screen_map_selftest.c`; `python3 -m pytest tests/ -q` → 168 passed, 2 skipped |

## Preserved GameReComp functionality

No PCRecomp component was deleted, renamed or replaced. The existing PE32
loader, lifter, pipeline, generated C, runtime shims, CLI and all existing tests
are unchanged by this integration. PE32+ and Win16/NE remain deferred as required
by the roadmap. No unrelated upstream commits (Windows XP/Vita/Win16 work) were
merged because they target different guest generations or host backends.

## Other upstream inspection in this session

Previous task branches (already committed on their own branches) adapted the
D3D7 FVF stride from `HenryKun55/igi-recomp` `278eaf63…` (MIT), reused
`sp00nznet/pcrecomp` msvcrt semantics for the new-handler path, hardened the
existing PE loader/resource walker, and used Wine/DirectX-Headers/SDL as
semantic references only (LGPL code was not copied). See those branches'
commit messages for details; no game binaries or proprietary assets were added.
