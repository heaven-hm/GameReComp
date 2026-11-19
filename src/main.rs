mod agent;
mod cli;
mod config;
mod gui;
mod json;
mod logging;
mod model;
mod orchestrator;
mod tasks;

pub use gamerecomp_core::{pe, pipeline_opts, theme_tokens};

use std::env;

fn main() {
    let invoked = env::args().next().unwrap_or_default();
    let raw_args: Vec<String> = env::args().skip(1).collect();
    let code = cli::dispatch(&raw_args, &invoked);
    std::process::exit(code);
}
