//! GameReComp core library, CLI, and GPUI desktop GUI.
//!
//! The portable core (`pe`, `pipeline_opts`, `theme_tokens`) compiles cleanly to both native
//! targets and `wasm32-unknown-unknown` (`--lib --no-default-features`).

pub mod pe;
pub mod pipeline_opts;
pub mod theme_tokens;

#[cfg(feature = "cli")]
pub mod agent;
#[cfg(feature = "cli")]
pub mod cli;
#[cfg(feature = "cli")]
pub mod config;
#[cfg(feature = "cli")]
pub mod json;
#[cfg(feature = "cli")]
pub mod logging;
#[cfg(feature = "cli")]
pub mod model;
#[cfg(feature = "cli")]
pub mod orchestrator;
#[cfg(feature = "cli")]
pub mod tasks;

#[cfg(feature = "gui")]
pub mod gui;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Inspect raw PE executable bytes (usable directly from WebAssembly browser drag-and-drop).
pub fn inspect_pe_bytes(bytes: &[u8]) -> pe::PeInfo {
    pe::inspect_pe_bytes(bytes)
}

/// Serialize the default pipeline options as JSON (useful for WASM bridge).
pub fn default_options_json() -> String {
    serde_json::to_string_pretty(&pipeline_opts::PipelineOptions::default()).unwrap_or_default()
}
