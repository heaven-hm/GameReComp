# GUI → native macOS launch: assets-root fix

## The defect

The recompilation pipeline's `run` stage starts a rebuilt native binary with
its working directory set to `assets_dir` (`--assets-dir`, or the original
PE32's parent by default). The generated runtime reads the same root from
`RECOMP_ASSETS_DIR` or a single directory argument
(`runtime/win32hle/host.c`, `boot_embedded`):

```c
const char *env_assets = getenv("RECOMP_ASSETS_DIR");
...
if (argc == 2 && is_directory(argv[1])) { ... assets = argv[1]; }
```

The GUI's post-build launch path did not provide either. `launch_rebuilt_binary`
called `launch_rebuilt_binary_with_data(bin, None)`, which sets the working
directory to the binary's parent — `<project>/build/` — with no data root. On
macOS the native binary starts, but the guest cannot find its own data files,
so the final step of the GUI workflow (pick game → recompile → read logs →
launch native build) looked broken even when the build itself was fine.

## The fix

- `GameReCompApp::native_launch_assets_root(configured_assets_dir, pe_paths)`
  resolves the data root in the same priority order as the pipeline:
  1. an explicitly configured assets directory (`--assets-dir` in the GUI),
  2. the PE recorded in the binary's own project (`project.json` /
     `manifest.json`),
  3. the active job's original PE, the loaded project, the picked file, and
     finally the current options' PE path.
  Missing/non-directory candidates are skipped rather than passed to the
  runtime.
- `launch_rebuilt_binary` now uses that root and delegates to the existing
  `launch_rebuilt_binary_with_data`, which sets `cwd`, `RECOMP_ASSETS_DIR` and
  the accepted `[assets-dir]` argument.
- The post-build "Launch Standalone" banner and the project header's "Launch"
  button (when no explicit data folder has been configured) use the same
  inference. An explicit game-data folder chosen in "Run Options" still wins.

## Files

- `src/gui/mod.rs` — resolution helper, launch wiring, unit test.
- `src/gui/project.rs` — post-build banner uses inferred assets root.
- `docs/GUI_NATIVE_LAUNCH_ASSETS.md` — this note.

## Verification

```sh
cargo check --locked
cargo test --locked            # 35 passed (incl. native_launch_assets_root_*)
python3 -m pytest tests/test_gui_cua.py tests/test_gui_matrix.py \
    tests/test_cli.py tests/test_e2e_suite.py -q
```

A live CUA pass on the base commit confirmed the surrounding workflow: the
file picker selects `Tetris.exe`, the picked-game card appears, dry-run
streams stage events and an exit-0 toast, and the project header's Launch
button starts the native `build/Tetris` binary. The fix changes only which
data root that launch receives.

## CUA verification after the fix

Rebuilt `target/debug/game-recomp-gui`, launched it with `--repo <worktree>`,
and used CuaDriver foreground clicks (permissions already granted) to open the
`arith_calc` project (it has no `gamerecomp_ui.json`, so the old code used the
broken no-data-root path) and press its header **Launch** button. Observed:

- toast `Launched with game data … (data: …)` instead of `Launched standalone`
  — the inferred assets root is now passed to the runtime;
- the native guest ran to `ExitProcess(0)` with `0 unresolved imports`.

Before the fix, the same workflow was checked on the base commit: file picker →
picked-game card → dry-run stage events and `exit 0` toast → header `Launch`
starting `build/Tetris` (that project had an explicit data folder in its UI
metadata).

## Limits

- No full game recompilation was run; only a dry run was used in the GUI check.
- The launch check proves the data root is supplied and the process runs; it is
  not a gameplay or asset-content oracle.
- This does not claim gameplay equivalence or a verified game run.
