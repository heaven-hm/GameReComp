# Existing GUI testing with the full game set

The GUI was **not** rebuilt or replaced. The shipped binary
`gui/build/game-recomp-gui` exposes two deterministic modes that drive the
same logic as its buttons:

- `--selftest-pe <exe>` — PE inspection used by the game picker.
- `--selftest <project> --repo <root>` — project model (stages, score, build
  digest, frames, toolchain) used by the home cards and run button.

`tools/gui_matrix.py` runs those modes and records the exact command, exit code
and output for every input. Evidence: `test-results/gui-matrix/gui-summary.json`.

## Results actually observed

- **15 PE32 games inspected: all exit 0.** Corpus games (Cave Story,
  GeneRally, Gunroar, both HeroCore builds, Ikachan, Noiz2sa, Parsec47,
  RRootage, Torus Trooper, Tumiki Fighters, Warning Forever), IGI `igi.exe`,
  and the two newly downloaded games (OUCC Tetris, Chocolate Doom). Each row is
  keyed by the input SHA-256.
- **Project model: 2 PASS, 1 honest FAIL.**
  - `gm-tetris`: score 70, stages inspect/recover/translate/project/compile ok,
    `build: digests MATCH`, toolchain line reports python3/cmake/cc/pkg-config
    and SDL2 present. Exit 0.
  - `gm-doom`: identical result. Exit 0.
  - `gm-tf`: exit 1, `SELFTEST FAIL: no reports/report.json -- run the pipeline
    over this directory first`. This is correct fail-closed behaviour for a
    project whose pipeline was killed before the report stage; it is not a GUI
    defect and is not counted as a pass.

## Real mouse/button interaction — BLOCKED on this host

The GUI does launch and own a window:
```
window: "GameReComp — Pick your game and turn it to native port"
position 150,67  size 1380x909
```
However, automated clicking could not be performed:
```
screencapture -x → could not create image from display
osascript System Events "click at {800, 500}"
  → osascript is not allowed assistive access. (-25211)
```
The accessibility tree exposes only three unnamed buttons (window controls);
GPUI's custom-drawn cards are not exposed as named AX buttons. Therefore no
claim is made about mouse-click behaviour. The deterministic selftest modes
above are the verified substitute for the same underlying workflows.

## Conclusion

GUI workflow logic passes for all 15 game PE32 inputs and for all complete
project directories. Interactive mouse testing is BLOCKED by macOS
permissions, with the exact errors above. No issue is closed on this evidence.
