use std::path::{Path, PathBuf};
use std::process::Command;

use clap::{Parser, Subcommand};
use owo_colors::OwoColorize;

use crate::agent;
use crate::config::{AppConfig, ToolchainStatus, UserSettings};
pub use crate::model::run_selftest;
use crate::model::{discover_projects, ProjectModel};
use crate::orchestrator;
use crate::pe::inspect_pe;
pub use crate::pe::run_selftest_pe;
use crate::tasks;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Parser, Debug)]
#[command(
    name = "game-recomp-cli",
    version = VERSION,
    about = "GameReComp CLI — Pick your game and turn it to native port",
    long_about = "Pick your game and turn it to native port. GameReComp turns Windows PE32 x86 games you own into standalone native C or WebAssembly builds with provenance tracking, verification, and optional AI readability agents.",
    disable_help_subcommand = false,
    args_conflicts_with_subcommands = true
)]
pub struct Cli {
    /// Launch the native Rust desktop GUI
    #[arg(long)]
    pub gui: bool,

    /// Open a project directory in the desktop GUI
    #[arg(short = 'o', long = "open", value_name = "DIR")]
    pub open: Option<PathBuf>,

    /// Override repository root directory
    #[arg(long = "repo", value_name = "DIR", global = true)]
    pub repo: Option<PathBuf>,

    /// Headless GUI model self-test against a project directory
    #[arg(long = "selftest", value_name = "DIR", hide = true)]
    pub selftest: Option<PathBuf>,

    /// Headless PE header self-test against an executable
    #[arg(long = "selftest-pe", value_name = "FILE", hide = true)]
    pub selftest_pe: Option<PathBuf>,

    /// Print pipeline stages and exit
    #[arg(long = "list-stages")]
    pub list_stages: bool,

    #[command(subcommand)]
    pub command: Option<Commands>,
}

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// Launch the native Rust desktop GUI
    Gui {
        /// Optional project directory to open immediately
        #[arg(value_name = "PROJECT_DIR")]
        project_dir: Option<PathBuf>,
    },
    /// Open a project directory directly in the desktop GUI
    Open {
        /// Project directory containing project.json / reports/
        #[arg(value_name = "PROJECT_DIR")]
        project_dir: PathBuf,
    },
    /// Inspect a Windows PE executable header (fail-closed PE32 check)
    Inspect {
        /// Path to the .exe file to inspect
        #[arg(value_name = "PE_FILE")]
        pe_file: PathBuf,
        /// Output machine-readable JSON
        #[arg(long)]
        json: bool,
    },
    /// Show recompilation status, scores, and progress for one or all projects
    Status {
        /// Optional project directory (omit to summarize all projects)
        #[arg(value_name = "PROJECT_DIR")]
        project_dir: Option<PathBuf>,
    },
    /// Check compilers, SDL2, WebAssembly toolchain, and installed AI CLI agents
    Setup,
    /// Show GameReComp version, 3-tier GPUI/WASM architecture, and license info
    About,
    /// Recompile a PE32 executable to native or WebAssembly
    Run {
        /// Arguments passed to the pipeline orchestrator (<pe_file> [options])
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Run batch recompilation & standalone checks across files or directories
    Batch {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Serve a built WebAssembly app over local HTTP (COOP/COEP enabled)
    Serve {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// List reconstructed project statuses in tabular format
    Tasks {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Run source readability & provenance harness on a project
    Readability {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Drive AI-assisted recovery via REVENG_AI_CMD
    Agent {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
}

fn brand_banner() -> String {
    format!(
        "{}{}  {}",
        "Game".bold(),
        "ReComp".black().on_bright_yellow().bold(),
        format!("v{VERSION}").dimmed()
    )
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

fn is_known_subcommand(s: &str) -> bool {
    matches!(
        s,
        "gui"
            | "open"
            | "inspect"
            | "status"
            | "setup"
            | "about"
            | "run"
            | "batch"
            | "serve"
            | "tasks"
            | "readability"
            | "agent"
            | "help"
    )
}

pub fn dispatch(raw_args: &[String]) -> i32 {
    // Fast-path compatibility for direct pipeline invocations:
    //   game-recomp-cli <pe_file> [pipeline flags...]
    if let Some(first) = raw_args.first() {
        let is_legacy_flag = matches!(
            first.as_str(),
            "-h" | "--help"
                | "-v"
                | "-V"
                | "--version"
                | "--gui"
                | "-o"
                | "--open"
                | "--repo"
                | "--selftest"
                | "--selftest-pe"
                | "--list-stages"
        );
        if !is_known_subcommand(first) && !is_legacy_flag {
            // Direct pipeline invocation: game-recomp-cli <pe_file> [options]
            let explicit_repo = flag_value(raw_args, &["--repo"]).map(PathBuf::from);
            let cfg = AppConfig::load(explicit_repo.as_deref());
            let filtered: Vec<String> = strip_repo_flag(raw_args);
            return orchestrator::run_pipeline_cli(&cfg, &filtered);
        }
    }

    // Support `-v` in addition to clap's `-V` / `--version`.
    if raw_args.len() == 1 && raw_args[0] == "-v" {
        println!("game-recomp-cli {VERSION}");
        return 0;
    }

    // Zero arguments on `game-recomp-cli` prints the crisp CLI overview.
    if raw_args.is_empty() {
        let cfg = AppConfig::load(None);
        print_styled_overview(&cfg);
        return 0;
    }

    let mut argv = Vec::with_capacity(raw_args.len() + 1);
    argv.push("game-recomp-cli".to_string());
    argv.extend_from_slice(raw_args);

    let cli = match Cli::try_parse_from(&argv) {
        Ok(c) => c,
        Err(err) => {
            let _ = err.print();
            return err.exit_code();
        }
    };

    let cfg = AppConfig::load(cli.repo.as_deref());

    if let Some(dir) = cli.selftest {
        return run_selftest(&dir, &cfg.repo_root);
    }
    if let Some(pe_file) = cli.selftest_pe {
        return run_selftest_pe(&pe_file);
    }
    if cli.list_stages {
        for s in orchestrator::STAGES {
            println!("{s}");
        }
        return 0;
    }
    if cli.gui || cli.open.is_some() {
        return launch_gui_binary(&cfg, cli.open.as_deref());
    }

    match cli.command {
        Some(Commands::Gui { project_dir }) => launch_gui_binary(&cfg, project_dir.as_deref()),
        Some(Commands::Open { project_dir }) => launch_gui_binary(&cfg, Some(&project_dir)),
        Some(Commands::Inspect { pe_file, json }) => run_inspect_cmd(&pe_file, json),
        Some(Commands::Status { project_dir }) => run_status_cmd(&cfg, project_dir.as_deref()),
        Some(Commands::Setup) => run_setup_cmd(&cfg),
        Some(Commands::About) => run_about_cmd(&cfg),
        Some(Commands::Run { args }) => orchestrator::run_pipeline_cli(&cfg, &args),
        Some(Commands::Batch { args }) => tasks::run_batch_command(&cfg, &args),
        Some(Commands::Serve { args }) => tasks::run_serve_command(&cfg, &args),
        Some(Commands::Tasks { args }) => tasks::run_tasks_command(&cfg, &args),
        Some(Commands::Readability { args }) => agent::run_readability(&cfg, &args),
        Some(Commands::Agent { args }) => agent::run_agent_command(&cfg, &args),
        None => {
            print_styled_overview(&cfg);
            0
        }
    }
}

fn strip_repo_flag(args: &[String]) -> Vec<String> {
    let mut out = Vec::with_capacity(args.len());
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--repo" && i + 1 < args.len() {
            i += 2;
        } else if args[i].starts_with("--repo=") {
            i += 1;
        } else {
            out.push(args[i].clone());
            i += 1;
        }
    }
    out
}

fn launch_gui_binary(cfg: &AppConfig, open_dir: Option<&Path>) -> i32 {
    let gui_name = if cfg!(windows) {
        "game-recomp-gui.exe"
    } else {
        "game-recomp-gui"
    };

    let mut candidates = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join(gui_name));
        }
    }
    candidates.push(cfg.repo_root.join("target").join("release").join(gui_name));
    candidates.push(cfg.repo_root.join("target").join("debug").join(gui_name));
    candidates.push(PathBuf::from(gui_name));

    for bin in candidates {
        if bin.is_file() || bin == Path::new(gui_name) {
            let mut cmd = Command::new(&bin);
            cmd.arg("--repo").arg(&cfg.repo_root);
            if let Some(dir) = open_dir {
                cmd.arg("--open").arg(dir);
            }
            if let Ok(status) = cmd.status() {
                return status.code().unwrap_or(0);
            }
        }
    }

    #[cfg(feature = "gui")]
    {
        return crate::gui::launch_gui(cfg.clone(), open_dir);
    }

    #[cfg(not(feature = "gui"))]
    {
        eprintln!("error: `game-recomp-gui` binary not found. Build it with `cargo build --release --bin game-recomp-gui`.");
        1
    }
}

fn print_styled_overview(cfg: &AppConfig) {
    println!();
    println!("  {}", brand_banner());
    println!(
        "  {}",
        "Pick your game and turn it to native port.".dimmed()
    );
    println!();
    println!("  {}", "COMMANDS".bright_yellow().bold());
    let rows = [
        ("game-recomp-gui [project_dir]", "Open the native Rust GPUI desktop GUI"),
        ("game-recomp-cli <game.exe> [opts]", "Recompile a PE32 executable to native or wasm32"),
        ("game-recomp-cli inspect <game.exe>", "Inspect PE headers and verify PE32 x86 support"),
        ("game-recomp-cli status [project]", "Show project scores, stages, and build health"),
        ("game-recomp-cli setup", "Check compilers, SDL2, wasm32 tools, and AI agents"),
        ("game-recomp-cli about", "Show architecture, WASM readiness & license info"),
        ("game-recomp-cli serve <project>", "Serve a built WebAssembly app over local HTTP"),
        ("game-recomp-cli batch <paths...>", "Batch recompile & verify standalone binaries"),
        ("game-recomp-cli tasks", "List all reconstructed project statuses"),
        ("game-recomp-cli readability <dir>", "Run source readability & provenance harness"),
        ("game-recomp-cli agent <dir>", "Drive AI-assisted recovery via REVENG_AI_CMD"),
    ];
    for (cmd, desc) in rows {
        println!("    {:<36} {}", cmd.bold(), desc.dimmed());
    }
    println!();
    println!(
        "  {} {}   {}",
        "Workspace:".dimmed(),
        cfg.repo_root.display(),
        "Run `game-recomp-cli --help` for all options.".dimmed()
    );
    println!();
}

pub fn run_inspect_cmd(pe_file: &Path, json_out: bool) -> i32 {
    let info = inspect_pe(pe_file);
    let size = std::fs::metadata(pe_file).map(|m| m.len()).unwrap_or(0);

    if json_out {
        let payload = serde_json::json!({
            "path": pe_file.display().to_string(),
            "ok": info.ok,
            "error": info.error,
            "magic": info.magic,
            "machine": info.machine,
            "pe32_x86": info.pe32_x86,
            "image_base": format!("0x{:08X}", info.image_base),
            "size": size,
            "input_os": info.input_os,
            "input_arch": info.input_arch,
        });
        println!("{}", serde_json::to_string_pretty(&payload).unwrap_or_default());
        return if !info.ok {
            1
        } else if info.pe32_x86 {
            0
        } else {
            2
        };
    }

    println!();
    println!("  {}", brand_banner());
    let fname = pe_file
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("executable");
    println!("  {} {}", "PE INSPECT".dimmed(), fname.bold());
    println!("  {}", "─".repeat(62).dimmed());

    if !info.ok {
        println!("  {} {}", "NOT USABLE".red().bold(), info.error);
        println!();
        return 1;
    }

    let verdict = if info.pe32_x86 {
        " SUPPORTED PE32 x86 ".black().on_bright_green().bold().to_string()
    } else {
        " UNSUPPORTED TARGET ".white().on_red().bold().to_string()
    };

    println!("  {:<16} {}", "Status".dimmed(), verdict);
    println!(
        "  {:<16} {} {}",
        "Format".dimmed(),
        info.magic.bold(),
        info.machine
    );
    println!(
        "  {:<16} 0x{:08X}",
        "Image Base".dimmed(),
        info.image_base
    );
    println!(
        "  {:<16} {}",
        "File Size".dimmed(),
        format_bytes(size)
    );
    println!(
        "  {:<16} {}",
        "Path".dimmed(),
        pe_file.display().to_string().dimmed()
    );
    println!();

    if info.pe32_x86 { 0 } else { 2 }
}

pub fn run_status_cmd(cfg: &AppConfig, project_dir: Option<&Path>) -> i32 {
    println!();
    println!("  {}", brand_banner());

    if let Some(dir) = project_dir {
        let mut model = match ProjectModel::load(dir) {
            Ok(m) => m,
            Err(e) => {
                eprintln!("  {} {e}", "error:".red().bold());
                return 1;
            }
        };
        model.load_certified_index(&cfg.repo_root);
        print_project_detail(&model);
        return 0;
    }

    let settings = UserSettings::load();
    let projects_root = if !settings.projects_root.trim().is_empty() {
        PathBuf::from(&settings.projects_root)
    } else {
        cfg.repo_root.join("projects")
    };
    let extra: Vec<PathBuf> = settings.custom_projects.iter().map(PathBuf::from).collect();
    let summaries = discover_projects(&projects_root, &extra);

    if summaries.is_empty() {
        println!(
            "  {}",
            "No projects yet. Run `game-recomp-cli <game.exe>` or `game-recomp-gui` to start."
                .dimmed()
        );
        println!();
        return 0;
    }

    println!(
        "  {} {}",
        "PROJECTS".dimmed(),
        format!("({} found in {})", summaries.len(), projects_root.display()).dimmed()
    );
    println!("  {}", "─".repeat(84).dimmed());
    println!(
        "  {:<24} {:<8} {:>7}  {:<14} {:>10}  {}",
        "PROJECT".dimmed(),
        "TARGET".dimmed(),
        "SCORE".dimmed(),
        "OUTCOME".dimmed(),
        "FUNCTIONS".dimmed(),
        "BUILD".dimmed()
    );

    for s in &summaries {
        let score_str = if s.load_ok {
            format!("{:>3}/100", s.score)
        } else {
            "   -   ".to_string()
        };
        let build_badge = if !s.load_ok {
            "unbuilt".dimmed().to_string()
        } else if s.build_passed && s.has_binary {
            "✓ binary".green().to_string()
        } else if s.has_wasm {
            "✓ wasm32".green().to_string()
        } else if s.build_passed {
            "✓ built".green().to_string()
        } else {
            "✗ failed".red().to_string()
        };

        let outcome_col = match s.verification.as_str() {
            "verified" | "pass" => s.verification.green().bold().to_string(),
            "partial" | "boot-passed" => s.verification.bright_yellow().to_string(),
            "unbuilt" => s.verification.dimmed().to_string(),
            _ => s.verification.red().to_string(),
        };

        println!(
            "  {:<24} {:<8} {:>7}  {:<14} {:>10}  {}",
            truncate_str(&s.name, 24).bold(),
            s.target_label.dimmed(),
            score_str,
            outcome_col,
            s.total_functions,
            build_badge
        );
    }
    println!();
    0
}

fn print_project_detail(m: &ProjectModel) {
    let title = format!(" {} ", m.display_title().to_uppercase());
    println!();
    println!("  {}", title.black().on_bright_yellow().bold());
    println!(
        "  {} · {} {} · runtime {} · target {}/{}",
        m.original_exe.as_str().bold(),
        m.inspect.pe_type,
        m.inspect.machine,
        m.runtime,
        m.target_os,
        m.target_arch
    );
    println!("  {}", "─".repeat(72).dimmed());

    let bar_len = 28usize;
    let filled = ((m.score.clamp(0, 100) as usize) * bar_len) / 100;
    let bar = format!(
        "{}{}",
        "█".repeat(filled).green(),
        "░".repeat(bar_len - filled).dimmed()
    );
    println!(
        "  {}  {}/100  ({})",
        bar,
        m.score.to_string().bold(),
        if m.verification.is_empty() {
            "unknown"
        } else {
            &m.verification
        }
    );
    println!();

    println!(
        "  {:<16} {} recovered · {} verified · {} high-level · {} thunks",
        "Functions".dimmed(),
        m.all_functions.len().to_string().bold(),
        m.verified_count().to_string().green(),
        m.high_level_count(),
        m.total_thunks
    );
    println!(
        "  {:<16} {}/{} stages passed",
        "Pipeline".dimmed(),
        m.stage_ok_count().to_string().green().bold(),
        m.stages.len()
    );
    for st in &m.stages {
        let icon = match st.status.as_str() {
            "ok" => "✓".green().to_string(),
            "failed" => "✗".red().to_string(),
            _ => "○".dimmed().to_string(),
        };
        println!("    {} {:<12} {}", icon, st.name, st.detail.dimmed());
    }
    println!();
    if m.build.present {
        let match_str = if m.build.digests_match {
            "digests MATCH".green().bold().to_string()
        } else {
            "digests differ".yellow().to_string()
        };
        println!(
            "  {:<16} {} ({}, {})",
            "Build".dimmed(),
            if m.build.binary_path.is_empty() {
                "sources only"
            } else {
                &m.build.binary_path
            },
            format_bytes(m.build.binary_size),
            match_str
        );
    }
    println!(
        "  {:<16} {} captured frame(s)",
        "Screenshots".dimmed(),
        m.frames.len()
    );
    println!();
}

pub fn run_setup_cmd(cfg: &AppConfig) -> i32 {
    let tc = ToolchainStatus::detect();
    println!();
    println!("  {}", brand_banner());
    println!(
        "  {} {}",
        "SETUP & TOOLCHAIN".bright_yellow().bold(),
        format!("({}/{})", std::env::consts::OS, std::env::consts::ARCH).dimmed()
    );
    println!("  {}", "─".repeat(72).dimmed());

    println!();
    println!("  {}", "COMPILERS & RUNTIME".dimmed());
    for card in &tc.cards {
        let mark = if card.installed {
            "✓".green().bold().to_string()
        } else {
            "✗".red().bold().to_string()
        };
        let detail = if card.installed {
            if card.version.is_empty() {
                card.path.clone()
            } else {
                format!("{} ({})", card.version, card.path)
            }
        } else {
            format!("missing — {}", card.install_hint)
        };
        println!(
            "    {} {:<24} {:<13} {}",
            mark,
            card.name.bold(),
            card.category.dimmed(),
            truncate_str(&detail, 64).dimmed()
        );
    }

    println!();
    println!("  {}", "AI HELPER AGENTS (OPTIONAL)".dimmed());
    for ag in &tc.agents {
        let mark = if ag.installed {
            "✓".green().bold().to_string()
        } else {
            "○".dimmed().to_string()
        };
        let detail = if ag.installed {
            format!("{}  (preset: {})", ag.path, ag.preset_cmd)
        } else {
            "not installed".to_string()
        };
        println!("    {} {:<24} {}", mark, ag.name.bold(), detail.dimmed());
    }

    println!();
    println!(
        "  {:<16} {}",
        "Repo root".dimmed(),
        cfg.repo_root.display()
    );
    println!(
        "  {:<16} {}",
        "REVENG_AI_CMD".dimmed(),
        cfg.reveng_ai_cmd.as_deref().unwrap_or("<not configured>")
    );
    println!();
    if tc.is_ready() {
        println!(
            "  {} Ready to recompile PE32 games natively.",
            "✓".green().bold()
        );
    } else {
        println!(
            "  {} Some native build tools are missing (see hints above).",
            "!".bright_yellow().bold()
        );
    }
    println!();
    0
}

pub fn run_about_cmd(cfg: &AppConfig) -> i32 {
    println!();
    println!("  {}", brand_banner());
    println!(
        "  {}",
        "Pick your game and turn it to native port."
            .dimmed()
    );
    println!("  {}", "─".repeat(72).dimmed());
    println!(
        "  {:<18} {}",
        "GUI Framework".dimmed(),
        "GPUI 0.2.2 (Zed GPU-accelerated Rust UI)".bold()
    );
    println!(
        "  {:<18} {}",
        "CLI Framework".dimmed(),
        "clap v4 + owo-colors + indicatif"
    );
    println!(
        "  {:<18} {}",
        "Binaries".dimmed(),
        "game-recomp-cli (CLI) · game-recomp-gui (GPUI Desktop)"
    );
    println!(
        "  {:<18} {}",
        "WASM Core".dimmed(),
        "game_recomp (wasm32-unknown-unknown compatible)".green()
    );
    println!(
        "  {:<18} {}",
        "Typography".dimmed(),
        "Archivo & IBM Plex Mono (SIL Open Font License 1.1)"
    );
    println!(
        "  {:<18} {}/{}",
        "Host Platform".dimmed(),
        std::env::consts::OS,
        std::env::consts::ARCH
    );
    println!(
        "  {:<18} {}",
        "Workspace".dimmed(),
        cfg.repo_root.display()
    );
    println!();
    println!("  {}", "PIPELINE OPTIONS".bright_yellow().bold());
    println!(
        "    {}",
        "--input-os auto|windows  --input-arch auto|x86  --target-os host|macos|linux|windows|web"
            .dimmed()
    );
    println!(
        "    {}",
        "--target-arch host|arm64|x86_64|wasm32  --out-mode full|binary-only|source-only"
            .dimmed()
    );
    println!(
        "    {}",
        "--profile game|desktop|console  --project-dir DIR  --assets-dir DIR  --oracle FILE"
            .dimmed()
    );
    println!(
        "    {}",
        "--stages A,B,...  --skip-stages A,B,...  --timeout N  --build-timeout N  --jobs N"
            .dimmed()
    );
    println!(
        "    {}",
        "--verbose  --quiet  --json  --dry-run  --interactive  --fresh-build  --reuse-build"
            .dimmed()
    );
    println!(
        "    {}",
        "--import-trace  --clean-objects  --verify-standalone  --capture-screenshots"
            .dimmed()
    );
    println!();
    0
}

pub fn format_bytes(bytes: u64) -> String {
    if bytes == 0 {
        return "0 B".to_string();
    }
    const UNITS: [&str; 4] = ["B", "KB", "MB", "GB"];
    let mut val = bytes as f64;
    let mut idx = 0;
    while val >= 1024.0 && idx < UNITS.len() - 1 {
        val /= 1024.0;
        idx += 1;
    }
    if idx == 0 {
        format!("{bytes} B")
    } else {
        format!("{val:.1} {}", UNITS[idx])
    }
}

fn truncate_str(s: &str, max_chars: usize) -> String {
    let mut chars = s.chars();
    let taken: String = chars.by_ref().take(max_chars).collect();
    if chars.next().is_some() && max_chars > 1 {
        let mut out: String = taken.chars().take(max_chars - 1).collect();
        out.push('…');
        out
    } else {
        taken
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_subcommands_and_flags() {
        let parsed = Cli::try_parse_from(["GameReComp", "setup"]).unwrap();
        assert!(matches!(parsed.command, Some(Commands::Setup)));

        let parsed_gui = Cli::try_parse_from(["GameReComp", "--gui", "-o", "/tmp/proj"]).unwrap();
        assert!(parsed_gui.gui);
        assert_eq!(parsed_gui.open, Some(PathBuf::from("/tmp/proj")));

        let parsed_inspect =
            Cli::try_parse_from(["GameReComp", "inspect", "game.exe", "--json"]).unwrap();
        assert!(matches!(
            parsed_inspect.command,
            Some(Commands::Inspect { json: true, .. })
        ));

        let parsed_about = Cli::try_parse_from(["GameReComp", "about"]).unwrap();
        assert!(matches!(parsed_about.command, Some(Commands::About)));
    }

    #[test]
    fn formats_byte_sizes_human_readably() {
        assert_eq!(format_bytes(512), "512 B");
        assert_eq!(format_bytes(2048), "2.0 KB");
        assert_eq!(format_bytes(3 * 1024 * 1024), "3.0 MB");
    }
}
