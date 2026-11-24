#![cfg(feature = "gui")]

//! Regression tests for the GUI pipeline workflow.
//!
//! A dry-run started from the Home screen reuses the shared `PipelineOptions`.
//! Before the fix in `gui::prepare_active_project_options`, its `dry_run`
//! flag leaked into a later "Run pipeline" from a project, so the backend
//! only printed a plan: no stages streamed, no binary was built and there was
//! nothing to launch. These tests keep that path honest without needing a
//! running GPUI window.

use std::path::Path;

use game_recomp::gui::prepare_active_project_options;
use game_recomp::pipeline_opts::PipelineOptions;

#[test]
fn project_run_pipeline_clears_stale_dry_run_flag() {
    let mut opts = PipelineOptions::default();
    opts.pe_file = "/games/demo.exe".to_string();
    opts.project_dir = "projects/old-dir".to_string();
    // State left behind after the user clicked "Dry-run plan" on Home.
    opts.dry_run = true;

    let started = prepare_active_project_options(&mut opts, Some(Path::new("projects/demo")));

    assert!(started, "a configured PE must be allowed to start");
    assert!(
        !opts.dry_run,
        "Run pipeline must perform a real recompile, not a dry run"
    );
    assert_eq!(
        opts.project_dir, "projects/demo",
        "the opened project directory must be used"
    );
    let args = opts.to_backend_args();
    assert!(
        !args.iter().any(|arg| arg == "--dry-run"),
        "the backend command must not contain --dry-run: {args:?}"
    );
}

#[test]
fn project_run_pipeline_keeps_explicit_project_dir_when_none_given() {
    let mut opts = PipelineOptions::default();
    opts.pe_file = "demo.exe".to_string();
    opts.project_dir = "projects/demo".to_string();
    opts.dry_run = true;

    assert!(prepare_active_project_options(&mut opts, None));
    assert!(!opts.dry_run);
    assert_eq!(opts.project_dir, "projects/demo");
}

#[test]
fn project_run_pipeline_requires_an_original_pe() {
    let mut opts = PipelineOptions::default();
    opts.pe_file = "   ".to_string();
    opts.dry_run = true;

    assert!(
        !prepare_active_project_options(&mut opts, Some(Path::new("projects/demo"))),
        "a project without an original PE cannot be recompiled"
    );
}
