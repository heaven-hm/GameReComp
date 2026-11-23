# Real GUI mouse-click testing with CUA

The existing GUI binary (`gui/build/game-recomp-gui`) was driven with the
installed CUA driver using its real Accessibility + Screen Recording identity
(no AppleScript assistive-access workaround).

## Environment (measured)

```
cua-driver permissions status --json
  accessibility: true, screen_recording: true, source: com.trycua.driver
```

The GUI was launched on a real project (`gm-tetris`); CuaDriver's
`get_accessibility_tree` found window `GameReComp — Pick your game and turn it
to native port` with a real pid/window_id. GPUI's custom-drawn tabs are not
AX elements, so tabs were clicked by pixel using foreground CGEvent delivery;
labelled AX buttons were clicked via their `element_token`.

## Command

```
python3 -m tools.gui_cua_test --gui <gui/build/game-recomp-gui> \
  --project <project-dir> --out test-results/gui-cua
```

`tools/gui_cua_test.py` records, for every step, the before/after screenshot
SHA-256, the click coordinate or AX label, the driver's reported effect and the
AX element count. A click that does not change the screenshot is a FAIL.

## Result: 9/9 clicks passed

| Step | Target | Screenshot changed |
|---|---|---|
| functions_tab | pixel (500,252) | yes |
| stages_tab | pixel (660,252) | yes |
| agents_tab | pixel (780,252) | yes |
| frames_tab | pixel (860,252) | yes |
| settings_tab | pixel (952,252) | yes |
| overview_tab | pixel (370,252) | yes |
| run_options_button | AX label "Run Options" (modal captured, then Escape) | yes |
| open_agents_button | AX label "Open Agents" | yes |
| sidebar_settings | pixel (78,849) | yes |

All steps used `delivery_mode: foreground`. The driver reports
`effect: "unverifiable"` for generic clicks (it cannot prove intent), so the
harness verifies the effect from a fresh screenshot instead. Destructive
controls (`Run pipeline`, `Launch`, `Create Issue`) are explicitly excluded
from the manifest and guarded by a unit test.

Committed screenshot proofs: `docs/evidence/gui-cua/functions_tab.jpg`,
`run_options_button.jpg`, `sidebar_settings.jpg`. Full-resolution PNGs, logs
and `cua-results.json` are in `test-results/gui-cua/` (gitignored).

## CLI testing in the same pass

`tools/cli_matrix.py` ran `game-recomp-cli --version/about/setup/tasks`,
`status <project>` and `inspect --json` on 11 PE32 executables (16 recorded
commands, all exit 0). `inspect` is a fail-closed header check, not execution
or compatibility proof; the report states that explicitly.

## Honest limits

- This proves the controls respond to real mouse clicks and the views change.
  It does not prove game logic, rendering or input handling inside the GUI.
- Click coordinates are scaled from a reference 1568×1033 CUA screenshot; a
  very different window size may need a new reference.
- CUA's generic click has no independent postcondition, so the screenshot
  hash is the evidence; a theme-only repaint could also change the hash.
- No issue was closed on this evidence.
