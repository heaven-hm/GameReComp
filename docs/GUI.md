# Desktop GUI (`game-recomp-gui`) & Rust CLI (`game-recomp-cli`)

**Status: implemented in Rust with two clean binaries — `game-recomp-cli` (clap v4 CLI) and `game-recomp-gui` (GPUI desktop GUI) — backed by the WASM-ready `game_recomp` core library (`src/lib.rs`).**

## Binaries & Stack

- **`game-recomp-gui`** (`src/bin/game-recomp-gui.rs`, `src/gui/`): **GPUI (`gpui 0.2.2`, `runtime_shaders`)** — Zed's GPU-accelerated Rust UI framework (Metal on macOS, Vulkan/Blade on Linux, DirectX on Windows) styled with the **"Marked Listing"** dark/light design system (embedded OFL `Archivo` and `IBM Plex Mono` fonts).
- **`game-recomp-cli`** (`src/bin/game-recomp-cli.rs`, `src/cli.rs`): **`clap` v4 + `owo-colors` + `indicatif`** — structured subcommands (`gui`, `open`, `inspect`, `status`, `setup`, `about`, `run`, `batch`, `serve`, `tasks`, `readability`, `agent`) plus direct invocation (`game-recomp-cli <game.exe> [options]`). Can also be built standalone without GPUI (`--no-default-features --features cli`).
- **WASM-ready core (`game_recomp`)**: `src/lib.rs` (`pe`, `pipeline_opts`, `theme_tokens`) compiles cleanly to `wasm32-unknown-unknown` (`cargo check --target wasm32-unknown-unknown --lib --no-default-features`).

## GUI Views & Capabilities

1. **Sidebar (248px)** — `[GR] GameReComp` brand mark, notification center bell with unread badge, full-width `+ New project` action menu, filterable `PROJECTS` list with colored swatches, animated pulsing working dots and progress meters, plus navigation to `Agents & models`, `Compilers & tools`, `Settings`, `About GameReComp`, `Report a problem`, light/dark theme switcher, and quit.
2. **Home / New Project** — Hero overview, native drag-and-drop PE32 dropzone (`ExternalPaths`) & file browser (`rfd`), fail-closed PE32 inspection card, and complete **GameReComp CLI Parameter Configurator** (App/Game/Console profile, Input/Output OS & Arch/Machine including `web/wasm32`, output modes & custom 8-stage toggles, project/assets/highlevel/oracle dirs, timeouts/jobs/split, all logging & build flags, and live copyable `game-recomp-cli ...` preview).
3. **Animated Live Stage Stepper** — Real-time GPUI animations (`with_animation`, pulsing dots, sliding workbar) showing all 8 stages (`inspect → recover → translate → project → compile → run → verify → report`), active step elapsed time, completion badges, and trailing logs.
4. **Project Workspace** — Sticky header with inline rename, Launch / Serve WASM / Report HTML / Run / Folder actions, progress meter, and 6 tabs (`Overview` with interactive Function Map, `Functions`, `Stages & Build`, `Agents`, `Frames`, `Settings`).
5. **Slide-over Function Inspector Drawer** — Right-aligned expandable drawer (`Esc`, `J`/`K`) with syntax-colored C source, provenance evidence, copy/open actions, and persistent per-function notes.
6. **Global Pages** — `Agents & models`, `Compilers & tools`, `Settings`, and `About GameReComp`.

## Building & Running

```sh
# 1. Verify WASM-compatible core library
cargo check --target wasm32-unknown-unknown --lib --no-default-features

# 2. Run Rust unit tests
cargo test

# 3. Build both binaries (`target/release/game-recomp-cli` & `target/release/game-recomp-gui`)
cargo build --release

# Or build CLI only (without GPUI):
cargo build --release --bin game-recomp-cli --no-default-features --features cli

# Or build & stage via CMake (`gui/build/game-recomp-gui`, `gui/build/game-recomp-cli`, `gui/build/gamerecomp`):
cmake -S gui -B gui/build && cmake --build gui/build

# 4. Run
./target/release/game-recomp-gui
./target/release/game-recomp-cli --help
```
