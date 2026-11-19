# Desktop GUI

**Status: implemented in Rust + SDL2/SDL2_ttf (`src/gui.rs`).** Both the
`./GameReComp` CLI and the desktop GUI compile into a single native Rust binary
(`target/release/gamerecomp`, staged at `gui/build/gamerecomp` by
`gui/CMakeLists.txt`), sharing `AppConfig`, `ProjectModel`, `pe::inspect_pe`,
and `orchestrator` directly without Python/Qt/webview dependencies.

The GUI is a native window over the same orchestration backend and `reports/`
contract as the CLI: every run spawns `./GameReComp` / the `autorecomp`
pipeline and reads produced `project.json` and `reports/*.json` files rather
than maintaining a second interpretation of pipeline results.

## Stack decision

**Rust (`edition = "2021"`, zero third-party Cargo crates) + SDL2 / SDL2_ttf**
(`build.rs` discovers `sdl2` and `SDL2_ttf` via `pkg-config`):

- Matches the 3-tier architecture: Rust for GUI, CLI, orchestration, tasks,
  configuration, logging, reporting, and agent integration (`src/*.rs`); Python
  for PCRecomp reverse engineering (`tools/`); C/C++ for Win32 HLE and generated
  code (`runtime/`, generated `src/recomp/gen/`).
- No Python UI framework, Qt installation, web service, or browser webview is
  required.
- AI-assisted readability remains optional and goes through `REVENG_AI_CMD` via
  `./GameReComp readability <project>` or `./GameReComp agent <project>`
  (see `src/agent.rs` and [SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md)).

## Contract with the CLI

| GUI action | Backing invocation / file |
|---|---|
| Run the pipeline | `./GameReComp <input.exe> --project-dir <dir> [--timeout N] [--oracle JSON]` |
| Resume an existing project | the same command against the existing project directory |
| Readability pass | `./GameReComp readability <project>` |
| Stage progress | the `==> <stage>` markers the CLI writes to stdout/stderr |
| Stage results | `<project>/reports/run.json` |
| Function inventory and provenance | `<project>/reports/functions.json` |
| Verification status | `<project>/reports/verification.json` |
| Human-readable report | `<project>/reports/report.html` |
| Readability outcome | `<project>/reports/readability.json`, `readability.html` |
| Rebuilt executable | the path recorded in `reports/run.json` |
| Evidence hashes | `manifest.json` (input SHA-256, source digest, binary SHA-256) |

Exit codes are the CLI's, unchanged: `0` verified scenario pass, `3` ran but
unverified, `2` a stage failed. The GUI shows the code; it does not reinterpret
it.

Progress shown must come from observed stage markers and elapsed time. If the
pipeline has not reported a stage, the stage is not shown as running or
complete. No animated "processing" substitute for real progress.

## Screens

1. **Project wizard** — browse for the original `.exe`, validate that it is a
   PE and which architecture (PE32 accepted, PE32+ rejected with the same
   fail-closed message as the CLI), choose the project directory, optional
   timeout and oracle JSON. Shows the asset-ownership reminder: the tool does
   not download or redistribute game files, and derived output carries the
   original program's rights.
2. **Run dashboard** — the stage graph (inspect → recover → translate →
   project → compile → run → verify → report) drawn from observed markers,
   with counts per stage, elapsed time, exit status and a cancel button that
   terminates the child process. Log panes mirror the CLI's stdout/stderr
   streams separately, as the run report does.
3. **Source explorer** — function tree from `reports/functions.json`,
   searchable by address, name and block; each function shows its provenance
   category (`LOW_LEVEL_FALLBACK`, `UNRESOLVED`, and the reserved
   `HIGH_LEVEL_*`) and its generated C unit. Category totals come from
   `project.json`; the explorer never computes its own.
4. **Build & run** — the compile log, binary path, binary SHA-256, source
   digest and the exact build invocation, all as recorded in the run report.
5. **Verification dashboard** — the recorded status, compared observables
   (exit code, stdout/stderr assertions, fatal markers), oracle gaps and the
   artifact hashes that tie the run to the source tree.
6. **Project management** — list projects under a working directory, show each
   one's latest status from its report files, and open a project in the other
   screens. Import/export is the project directory itself; no database.

## Packaging and tests

- Built cleanly offline with `cargo build --release` or staged into
  `gui/build/gamerecomp` via `cmake -S gui -B gui/build && cmake --build gui/build`.
- Headless verification runs without opening a window via:
  - `gui/build/gamerecomp --selftest <project_dir> --repo .` (loads `project.json`
    and `reports/` using `ProjectModel` and validates stage/provenance/build hashes)
  - `gui/build/gamerecomp --selftest-pe <pe_file>` (validates PE32 vs PE32+
    inspection using `src/pe.rs`, matching CLI fail-closed rules).
- Interactive launch: `gui/build/gamerecomp [-o <project_dir>]` or
  `./GameReComp --gui [-o <project_dir>]`.

## Implementation status

| Piece | Status |
|---|---|
| Rust + SDL2/SDL2_ttf stack (`src/gui.rs`, `build.rs`) | implemented |
| Headless GUI & PE selftests (`--selftest`, `--selftest-pe`) | implemented (verified in `verify.sh`) |
| Project model loader & provenance parity (`src/model.rs`, `src/json.rs`) | implemented |
| Interactive PE selector, stage view, function explorer & live runner | implemented in `src/gui.rs` (`sdl_ui`) |
| CLI parity / report consistency | shared `ProjectModel` & `pe::inspect_pe` |
