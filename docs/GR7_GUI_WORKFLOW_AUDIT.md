# GR7 audit — macOS GUI/CLI pipeline workflow

Scope: `src/gui/*.rs`, `src/cli.rs`, new tests/docs only. `pipeline.py` is owned
by GR8 and was not modified. Base: `cb4dc0441199af0fc15d0100a565bd8e444a228e`.

## Workflow traced

1. **Select game** — `browse_for_pe` / drag-and-drop call `pick_pe_path`, which
   runs fail-closed `inspect_pe` and fills the shared `PipelineOptions`.
2. **Analyze** — the pick card shows the PE verdict; `inspect` is the first
   pipeline stage and its `==> inspect` line is parsed by the GUI stepper.
3. **Start recompile** — Home's "Start recompiling" calls `start_picked_pipeline`;
   a project's "Run pipeline" calls `start_active_project_pipeline`.
4. **Logs/errors** — `run_streaming_child_inner` pipes stdout/stderr into
   `WorkerMsg::Line`, drained by the 80 ms `poll_worker_and_timers` ticker into
   `ActiveJob::live_logs`, rendered on Home and on the project Stages tab.
5. **Launch native build** — the finished job records `built_binary`, and the
   "Launch" / "Run" actions call `launch_project_dir_smart` →
   `launch_rebuilt_binary_with_data`.

The Python backend was exercised separately on `tests/fixtures/pe32-minimal.exe`
with the exact GUI-generated argument shape: all 8 stages completed, a native
`build/pe32_minimal` binary was produced, and the process exited 0 (score 93,
`partial`). The defect below is therefore in GUI state handling, not in the
backend.

## Concrete blocker fixed

`PipelineOptions` is shared between the Home pick card and the project
workspace. "Dry-run plan" sets `opts.dry_run = true` before starting a job.
`reset_dropzone()` cleared the picked PE path and project dir but **not**
`dry_run`, and `start_active_project_pipeline()` reused the same options
unchanged. Result: after any dry-run on Home, opening a game project and
clicking "Run pipeline" appended `--dry-run` to the backend command. The GUI
showed a "completed" job, but no stages were executed, no log output beyond the
plan was produced, no binary was built and "Launch" never appeared — a silent
blocker for the select → recompile → logs → launch path.

Fix (in `src/gui/mod.rs`):

- `prepare_active_project_options()` now clears `dry_run` for every real
  "Run pipeline" start and re-applies the opened project directory.
- `reset_dropzone()` also clears `dry_run` so plan-only state cannot survive a
  pick → open-project transition.

Regression coverage: `tests/gui_pipeline_workflow.rs` asserts that a stale
dry-run flag is removed, that the backend argument list has no `--dry-run`,
that an explicit project dir is preserved, and that a missing PE still refuses
to start.

## Limits

- The fix is verified with `cargo check --all-targets` and `cargo test`; it does
  not claim a live end-to-end native game run.
- CUA accessibility/screen permissions were granted locally, but the CUA
  driver saw a GUI process owned by a different worktree/session, so no CUA
  click evidence or live-GUI claim is made here. Verification is build/test
  plus a direct backend run on the PE32 fixture.
