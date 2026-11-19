# Desktop GUI & Rust CLI

**Status: implemented in Rust + GPUI (`src/gui/`) and clap v4 (`src/cli.rs`), with a WASM-ready shared core (`gamerecomp_core` in `src/lib.rs`).** Both the `./GameReComp` CLI and the GPU-accelerated desktop GUI compile into a single native Rust binary (`target/release/gamerecomp`, staged at `gui/build/gamerecomp` by `gui/CMakeLists.txt`), sharing `pe`, `pipeline_opts`, `theme_tokens`, `AppConfig`, `ProjectModel`, and `orchestrator` directly with zero Python UI/Qt/WebView dependencies.

## Architecture & Stack

- **Desktop GUI**: **GPUI (`gpui 0.2.2`, `runtime_shaders`)** — Zed's GPU-accelerated Rust UI framework (Metal on macOS, Vulkan/Blade on Linux, DirectX on Windows) styled with the **"Marked Listing"** dark/light design system (embedded OFL `Archivo` and `IBM Plex Mono` fonts).
- **Rust CLI**: **`clap` v4 + `owo-colors` + `indicatif`** — structured subcommands (`gui`, `open`, `inspect`, `status`, `setup`, `about`, `run`, `batch`, `serve`, `tasks`, `readability`, `agent`) plus 100% backward-compatible direct invocation (`./GameReComp <game.exe> [options]`).
- **WASM-ready shared core (`gamerecomp_core`)**: `src/lib.rs` (`pe`, `pipeline_opts`, `theme_tokens`) compiles cleanly to `wasm32-unknown-unknown` (`cargo check --target wasm32-unknown-unknown --lib --no-default-features`) so the exact PE32 byte inspector, CLI option builder, and theme tokens can be reused directly in future WebAssembly web ports.

## Views & Capabilities

1. **Sidebar (248px)** — `[GR] GameReComp` brand mark, notification center bell with unread badge, full-width `+ New project` action menu, filterable `PROJECTS` list with colored swatches, animated pulsing working dots and progress meters, plus navigation to `Agents & models`, `Compilers & tools`, `Settings`, `About GameReComp`, `Report a problem`, light/dark theme switcher, and quit.
2. **Home / New Project** — Hero overview, native drag-and-drop PE32 dropzone (`ExternalPaths`) & file browser (`rfd`), fail-closed PE32 inspection card, and complete **GameReComp CLI Parameter Configurator**:
   - Application profile (`Game`, `Desktop App`, `Console App`)
   - Input OS/Arch (`auto`, `windows`, `x86`) and Output OS/Arch/Machine (`host`, `macos`, `linux`, `windows`, `web/wasm32`)
   - Output mode (`Full`, `Binary only`, `Source only`, `Custom stages` with all 8 stage pills)
   - Output folder (`--project-dir`), Assets dir (`--assets-dir`), Highlevel dir (`--highlevel`), Oracle JSON (`--oracle`)
   - Logging & output controls (`--verbose`, `--quiet`, `--no-progress`, `--json`, `--no-table`, `--dry-run`)
   - Build & runtime knobs (`--timeout`, `--build-timeout`, `--jobs`, `--split`, `--interactive`, `--fresh-build`, `--reuse-build`, `--import-trace`, `--clean-objects`, `--verify-standalone`, `RECOMP_SCREENSHOT`)
   - Live copyable `./GameReComp ...` command preview bar.
3. **Animated Live Stage Stepper** — Real-time GPUI animations (`with_animation`, pulsing dots, sliding workbar) showing all 8 stages (`inspect → recover → translate → project → compile → run → verify → report`), active step elapsed time, completion badges, and trailing logs.
4. **Project Workspace** — Sticky header with chips, yellow-highlighted uppercase project title + inline rename, Launch / Serve WASM / Report HTML / Run pipeline actions, progress meter, and 6 tabs:
   - **Overview**: Interactive color-coded Function Map with live hover readout & click-to-inspect, 4 ruled KPI columns, Next-step callout, Runtime summary, and bottom panels.
   - **Functions**: Searchable (`/`), filterable, sortable table of all recovered & high-level functions with starring (`★`).
   - **Stages & Build**: Stage breakdown, manifest SHA-256 verification, and live colorized console.
   - **Agents**: Readability & provenance worker (`REVENG_AI_CMD`), timestamped activity feed, and certified verdicts.
   - **Frames**: Native GPUI image viewer for captured PNG/BMP screenshots.
   - **Settings**: Full project CLI options editor + project metadata.
5. **Slide-over Function Inspector Drawer** — Right-aligned expandable drawer (`Esc` closes, `J`/`K` steps next/prev) with syntax-colored C source, provenance evidence, copy/open actions, and persistent per-function notes.
6. **Global Pages** — `Agents & models`, `Compilers & tools`, `Settings`, and `About GameReComp` (architecture, WASM readiness, keyboard shortcuts, licenses, and diagnostics).

## Building & Verification

```sh
# 1. Verify WASM-compatible core library
cargo check --target wasm32-unknown-unknown --lib --no-default-features

# 2. Run Rust unit tests (core + CLI + GPUI helpers)
cargo test --release

# 3. Build release binary and stage gui/build/gamerecomp
cmake -S gui -B gui/build && cmake --build gui/build

# 4. Run full verification gate
sh verify.sh --fast

# 5. Launch native GPUI desktop GUI
./GameReComp gui
```
