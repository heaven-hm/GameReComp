# Full game-matrix methodology

## Goal and non-goal

This run attempts to exercise every locally available **Windows x86 PE32 game**
through the real GameReComp pipeline on macOS, and through a native Linux build
where a Linux runner exists. It does **not** claim 100% compatibility: acceptance
levels A0–A10 require evidence, and the absence of an original-Windows oracle,
missing game files, and missing host runners are reported as such rather than
counted as passes.

## Inputs

PE32 x86 is detected from the file itself (`MZ`, `PE\0\0`, `IMAGE_FILE_MACHINE_I386`).
Every result is keyed by the input's SHA-256. Files that are PE32+ x64
(`opentyrian.exe`, `cannonball.exe`, `pixeltetris.exe`, `snake-windows-x86_64.exe`)
are outside this assignment's PE32 x86 roadmap boundary and are excluded, not
silently counted as tested.

Tooling executables bundled with games (config tools, editors, injectors,
decompilers) are likewise not game acceptance targets.

## macOS driver

`python3 -m tools.game_matrix --exe ... --out <dir> --parallel 1` invokes
`tools.autorecomp` with explicit guest/host identity and the host SDL drivers:

```
python3 -m tools.autorecomp <exe> --project-dir <dir> --assets-dir <game-dir> \
  --timeout 8 --no-table --input-os windows --input-arch x86 \
  --target-os host --target-arch host --jobs <n>
```

All eight stages (inspect, recover, translate, project, compile, run, verify,
report) are executed. The tool's own `report.json` is the source of truth for
stage status, score, outcome and process exit code. `--parallel 1` is required
on this machine: four concurrent generated-project builds exhausted memory and
the run was SIGKILLed (exit 137), which is recorded as a harness defect, not a
game result.

## Linux driver

GameReComp's `autorecomp` refuses cross-OS targets on macOS, and this host has
no Docker daemon by default. `tools.linux_matrix` therefore builds the
**generated project** in a Linux container (Colima/Docker) with SDL2 headers and
runs the resulting ELF with dummy video/audio drivers. This is the Linux host
compile+run path, not Wine and not an emulator. If no Linux runner is available
the driver returns a nonzero status and emits no Linux PASS rows.

## Acceptance mapping

- A0/A1/A2/A3: inspect/recover/translate+project/compile stage results.
- A4: process launches and stays alive (or exits cleanly) in the run stage.
- A5–A10: require menu, graphics, input, audio, save/load and original-vs-native
  comparison evidence; a bare compile or a bare process exit is never counted.

`verification: partial` with `exit=1` means the binary launched and exited with
code 1 and no oracle was supplied. That is recorded verbatim; it is not a PASS.

## Reproducibility

All raw logs and machine-readable reports live under `test-results/game-matrix/`
(gitignored). The committed artifact is this methodology plus the drivers and
their unit tests; no game binary, asset or generated 3 GB source tree is committed.
