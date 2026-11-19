mod agent;
mod config;
mod gui;
mod json;
mod logging;
mod model;
mod orchestrator;
mod pe;
mod tasks;

use std::env;
use std::path::{Path, PathBuf};

use config::AppConfig;

const VERSION: &str = "0.0.1";

fn print_help() {
    println!(
        "GameReComp {VERSION} (Rust application, GUI & orchestrator)\n\
         \n\
         Usage:\n\
           GameReComp <pe_file> [options]        Recompile a PE32 executable to native or wasm32\n\
           GameReComp serve <project_dir>        Serve a built WebAssembly app over local HTTP\n\
           GameReComp batch <path_or_dir...>     Run batch recompilation & standalone checks\n\
           GameReComp tasks [--project-root DIR] List reconstructed project statuses\n\
           GameReComp readability <project_dir>  Run source readability & provenance harness\n\
           GameReComp agent <project_dir>        Drive AI-assisted recovery via REVENG_AI_CMD\n\
           GameReComp --gui [-o DIR]             Launch native Rust desktop GUI\n\
           GameReComp --selftest DIR [--repo R]  Headless GUI model check\n\
           GameReComp --selftest-pe FILE         Headless PE header check\n\
           GameReComp --list-stages              Print pipeline stages\n\
           GameReComp --version                  Print version\n\
           GameReComp --help                     Show this message"
    );
}

fn flag_value(args: &[String], names: &[&str]) -> Option<String> {
    let mut i = 0;
    while i < args.len() {
        for &n in names {
            if args[i] == n && i + 1 < args.len() {
                return Some(args[i + 1].clone());
            }
            if let Some(rest) = args[i].strip_prefix(n) {
                if let Some(val) = rest.strip_prefix('=') {
                    return Some(val.to_string());
                }
            }
        }
        i += 1;
    }
    None
}

fn main() {
    let raw_args: Vec<String> = env::args().skip(1).collect();
    let invoked_name = env::args()
        .next()
        .and_then(|s| Path::new(&s).file_name().map(|n| n.to_string_lossy().to_string()))
        .unwrap_or_default();

    if raw_args.iter().any(|a| a == "-v" || a == "--version") {
        println!("GameReComp {VERSION}");
        std::process::exit(0);
    }
    if raw_args.iter().any(|a| a == "-h" || a == "--help") {
        print_help();
        std::process::exit(0);
    }

    let explicit_repo = flag_value(&raw_args, &["--repo"]).map(PathBuf::from);
    let cfg = AppConfig::load(explicit_repo.as_deref());

    if let Some(dir) = flag_value(&raw_args, &["--selftest"]) {
        std::process::exit(model::run_selftest(Path::new(&dir), &cfg.repo_root));
    }

    if let Some(pe_file) = flag_value(&raw_args, &["--selftest-pe"]) {
        std::process::exit(pe::run_selftest_pe(Path::new(&pe_file)));
    }

    let open_dir = flag_value(&raw_args, &["-o", "--open"]).map(PathBuf::from);
    let want_gui = raw_args.iter().any(|a| a == "--gui")
        || open_dir.is_some()
        || (raw_args.is_empty() && invoked_name.eq_ignore_ascii_case("gamerecomp") && !env::args().next().unwrap_or_default().ends_with("GameReComp"));

    if want_gui {
        std::process::exit(gui::launch_gui(cfg, open_dir.as_deref()));
    }

    if raw_args.is_empty() {
        print_help();
        std::process::exit(0);
    }

    let code = match raw_args[0].as_str() {
        "batch" => tasks::run_batch_command(&cfg, &raw_args[1..]),
        "serve" => tasks::run_serve_command(&cfg, &raw_args[1..]),
        "tasks" => tasks::run_tasks_command(&cfg, &raw_args[1..]),
        "readability" => agent::run_readability(&cfg, &raw_args[1..]),
        "agent" => agent::run_agent_command(&cfg, &raw_args[1..]),
        _ => orchestrator::run_pipeline_cli(&cfg, &raw_args),
    };
    std::process::exit(code);
}
