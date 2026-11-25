# Source Improvement GUI — design

## Goal

Give the Inspector's source view one explicit "Improve source code" control with a
method dropdown: `rev.ng`, `AI (local)`, and `AI (web)`. Settings get explicit
locations/configuration for each method. Every method remains gated by the
existing compile-and-rerun readability proof; the GUI never labels an unproven
source edit as PASS.

## Architecture

- `src/gui/inspector.rs`: replace the single AI button with a method selector
  (inline menu, matching the sidebar's `show_new_menu` pattern), a target display,
  and a Run button. The selected method is held in `AppState`.
- `src/gui/mod.rs`: add `SourceImproveMethod` and `source_improve_method` state,
  plus `start_source_improve(method)` that routes:
  - `rev.ng` → the deterministic rev.ng provider path (`readability.py --providers revng` when available),
  - `AI (local)` → the configured local command in `REVENG_AI_CMD`,
  - `AI (web)` → the configured web endpoint/model command (explicit opt-in only).
  All routes reuse the existing gated `tools/autorecomp/readability.py`: AI output
  is accepted only after compile and baseline re-run; otherwise the job reports FAIL.
- `src/config.rs`: extend `UserSettings` with `revng_bin`, `revng_dir`,
  `ai_web_endpoint`, `ai_web_model`, `ai_web_cmd`. Keep `reveng_ai_cmd` for local AI.
- `src/gui/pages.rs`: add these fields to Global Settings with focus inputs, so
  paths and commands can be set in the app instead of by hand-editing env vars.
- `src/pipeline_opts.rs`: expose the method to the CLI preview as
  `--readability-providers <all|revng|ai>` (existing CLI convention); default unchanged.

## Error handling

- Missing rev.ng → toast pointing to Settings, no fallback to AI.
- Missing local/web AI config → toast pointing to Settings.
- A method that produces output failing compile/run → FAIL with the raw log kept
  in the job record; no PASS.

## Testing

- Unit tests for config defaults/parsing of the new fields and CLI flag rendering.
- Cargo check/test.
- CUA smoke test of the GUI (open Inspector source view, dropdown, settings fields)
  with screenshots recorded under `evidence/CUA/`.

## Boundaries

No changes to lifting/compilation correctness, no new AI provider implementation:
the web option invokes a user-configured command, it does not embed a service SDK.
