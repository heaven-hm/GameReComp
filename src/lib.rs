//! Platform-independent GameReComp core library.
//!
//! Compiles cleanly to both native targets (`aarch64-apple-darwin`, `x86_64-unknown-linux-gnu`,
//! `x86_64-pc-windows-msvc`) AND `wasm32-unknown-unknown` so the exact same PE32 header
//! inspector, CLI option builder, stage planner, and "Marked Listing" design tokens power both
//! the native GPUI desktop application and future WebAssembly web builds.

pub mod pe;
pub mod pipeline_opts;
pub mod theme_tokens;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Inspect raw PE executable bytes (usable directly from WebAssembly browser drag-and-drop).
pub fn inspect_pe_bytes(bytes: &[u8]) -> pe::PeInfo {
    pe::inspect_pe_bytes(bytes)
}

/// Serialize the default pipeline options + design tokens as JSON (useful for WASM bridge).
pub fn default_options_json() -> String {
    serde_json::to_string_pretty(&pipeline_opts::PipelineOptions::default()).unwrap_or_default()
}
