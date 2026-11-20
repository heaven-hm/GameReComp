use std::env;
use std::path::PathBuf;

use game_recomp::cli::{run_selftest, run_selftest_pe};
use game_recomp::config::AppConfig;
use game_recomp::gui::launch_gui;

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let mut repo_override: Option<PathBuf> = None;
    let mut open_dir: Option<PathBuf> = None;
    let mut selftest_dir: Option<PathBuf> = None;
    let mut selftest_pe: Option<PathBuf> = None;

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-h" | "--help" => {
                println!(
                    "game-recomp-gui {}\n\nUsage:\n  game-recomp-gui [PROJECT_DIR]\n  game-recomp-gui -o <PROJECT_DIR> [--repo <REPO_DIR>]\n  game-recomp-gui --selftest <PROJECT_DIR> [--repo <REPO_DIR>]\n  game-recomp-gui --selftest-pe <EXE_FILE>",
                    game_recomp::VERSION
                );
                std::process::exit(0);
            }
            "-V" | "--version" | "-v" => {
                println!("game-recomp-gui {}", game_recomp::VERSION);
                std::process::exit(0);
            }
            "--repo" if i + 1 < args.len() => {
                repo_override = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            "-o" | "--open" if i + 1 < args.len() => {
                open_dir = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            "--selftest" if i + 1 < args.len() => {
                selftest_dir = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            "--selftest-pe" if i + 1 < args.len() => {
                selftest_pe = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            other if !other.starts_with('-') && open_dir.is_none() => {
                open_dir = Some(PathBuf::from(other));
                i += 1;
            }
            _ => {
                i += 1;
            }
        }
    }

    let cfg = AppConfig::load(repo_override.as_deref());
    let code = if let Some(dir) = selftest_dir {
        run_selftest(&dir, &cfg.repo_root)
    } else if let Some(pe) = selftest_pe {
        run_selftest_pe(&pe)
    } else {
        launch_gui(cfg, open_dir.as_deref())
    };
    std::process::exit(code);
}
