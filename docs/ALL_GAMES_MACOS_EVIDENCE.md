# All available games executed on macOS — final evidence table

Every native binary that exists in this workspace was run **one at a time**,
from its own game folder, with Cocoa video, screenshot capture and a 12-second
limit. Source of truth: `test-results/game-matrix/execution/summary.json` and
the per-game `run.log` + `screenshots/`. The runner is `tools/game_runner`
(unit-tested), and every row carries the native binary's SHA-256.

| Game | Exit | Frames | Verdict / exact blocker |
|---|---|---|---|
| Cave Story | timeout (alive) | 3 × 640×480 | **PARTIAL** — frames 1/2 non-blank (5 and 3 distinct colours); frame 0 blank. No original oracle |
| Ikachan | timeout (alive) | 3 × 351×238 | **FAIL (visual)** — all 3 frames single-colour; a 20 s / 10-frame capture also produced 10 blank frames |
| OUCC Tetris (downloaded) | timeout (alive) | 3 × 310×418 | **PARTIAL** — 0 unresolved imports; 3 non-blank frames (4/5/5 distinct colours) |
| Gunroar | SIGBUS (-10) | 0 | FAIL — unresolved indirect calls VA 0 at 0x0040942C/0x00409324/0x00419D38 (#27) |
| HeroCore gm5 | 3 | 0 | FAIL — missing ComCtl32 GetProcAddress shims |
| Tumiki Fighters | timeout (alive) | 0 | FAIL — unresolved indirect calls VA 0 / VA 0xFF |
| Warning Forever | 0 | 0 | FAIL — MessageBox: DirectDraw7 init failed |
| Parsec47 | timeout (alive) | 0 | FAIL — unresolved indirect calls, no frames |
| RRootage | timeout (alive) | 0 | FAIL — no frames captured |
| Noiz2sa | timeout (alive) | 3 × 640×480 | **PARTIAL (fixed)** — 0 frames on the base runtime; 3 non-blank frames (27/36/30 colours) after A24's common frame sink + A29's SDL present wiring |
| Chocolate Doom (downloaded) | 255 | 0 | BLOCKED on WAD — 0 unresolved imports, but no DOOM.WAD supplied |

## What this proves

- 11 games were executed on macOS with logs and hashes. 3 produced screenshot
  files, but `tools/frame_proof` (24-bpp BMP decode + distinct-colour count)
  showed Cave Story frame 0 and **all** Ikachan frames are single-colour/blank;
  a longer 20 s capture of Ikachan produced 10 more blank frames. Only Cave
  Story (2 of 3) and Tetris (3 of 3) have non-blank frames, and even those have
  only 3–5 distinct colours, so they are evidence of *some* rendering, not of
  a correct picture or gameplay.
- Tetris (a newly downloaded Windows PE32 game) reached 0 unresolved imports
  and non-blank frames — a real native conversion of Windows software to
  macOS at the boot/render level, not a gameplay acceptance claim.
- 8 games have precise, measured blockers (listed above). They are not
  counted as PASS, and no issue was closed on this evidence.
- The remaining corpus games (GeneRally, HeroCore gm7, IGI variants, both
  TrackMania titles) have no native binary: their builds timed out or were
  SIGKILLed by host resources (#26), or the original files are absent.

## Display size / fullscreen

Fixed in `3a99d9b` and verified on the rebuilt Tetris binary:
`RECOMP_WINDOW_SIZE=640x480` → `[screen] window 640x480 fullscreen=0`;
`RECOMP_FULLSCREEN=1` → `[screen] window 1680x1050 fullscreen=1`.
Guest `SetDisplayMode` now resizes the host window to the game's mode. See
`docs/DISPLAY_SIZING_EVIDENCE.md`.

## Linux and x64

Not done, and not claimed:
- `--target-os linux` is refused on this host (cross-target builds are not
  supported); no Docker daemon, Colima image download stalled, no qemu-user
  or Linux kernel. The Linux driver `tools/linux_matrix.py` exists but emits
  no PASS rows without a runner.
- x64 is explicitly refused by the pipeline (`unsupported input architecture:
  x86_64`); issue #13 remains DEFERRED. Both are permanent regression tests in
  `tests/test_capability_boundaries.py`.

"Move to Linux once all Windows x86 games run" cannot be satisfied yet: 8 of
the 11 executed games still fail or render no frames, and several games have
no binary at all. The honest next step is fixing those measured blockers,
then obtaining a Linux runner.
