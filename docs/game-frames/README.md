# Real game frames (not GUI screenshots)

These images are captured from the rebuilt game binary itself via
`RECOMP_SCREENSHOT` — the guest's framebuffer, not the GameRecomp UI.

| File | Source | Evidence |
| --- | --- | --- |
| `igi/level7-intro-frame0039.png` | `IGI <data-dir> window level7`, frame 39 of 40 | 759 colours, helicopter/snow/trees intro. `docs/igi-levels/level7-fixed-final/run.log` |
| `igi/level5-splash.png` | IGI level 5 boot | Project IGI splash with loading bar |
| `igi/level5-gameplay.png` | IGI level 5, frame 30 | 2,535 colours, in-game view |

GUI captures (buttons, percentages, project views) live in `docs/cua-e2e/` and
`docs/verification-engine/` and are **not** game proof. Earlier black frames from
the broken startup-shim build were deleted; the current binary renders level 7
correctly again after commit `b5b42f1b`.
