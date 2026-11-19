use std::path::PathBuf;
use std::process::{Command, Stdio};

use crate::config::AppConfig;
use crate::logging::log_to_file;

pub fn run_readability(cfg: &AppConfig, args: &[String]) -> i32 {
    let mut project_dir: Option<PathBuf> = None;
    let mut ai_cmd = cfg.reveng_ai_cmd.clone();

    let mut i = 0;
    while i < args.len() {
        if (args[i] == "--cmd" || args[i] == "--ai-cmd") && i + 1 < args.len() {
            ai_cmd = Some(args[i + 1].clone());
            i += 2;
            continue;
        }
        if !args[i].starts_with('-') && project_dir.is_none() {
            project_dir = Some(PathBuf::from(&args[i]));
        }
        i += 1;
    }

    if let Some(ref pdir) = project_dir {
        let log_path = pdir.join("logs").join("ai-agent.log");
        let cmd_desc = ai_cmd.as_deref().unwrap_or("<none>");
        log_to_file(
            &log_path,
            "agent",
            &format!("invoking readability harness (REVENG_AI_CMD={cmd_desc})"),
        );
    }

    let mut cmd = Command::new("python3");
    cmd.current_dir(&cfg.repo_root)
        .env("PYTHONPATH", &cfg.repo_root)
        .args(["-m", "tools.autorecomp.readability"])
        .args(args)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    if let Some(ref ai) = ai_cmd {
        cmd.env("REVENG_AI_CMD", ai);
    }

    match cmd.status() {
        Ok(s) => s.code().unwrap_or(1),
        Err(e) => {
            eprintln!("error: failed to start AI readability harness: {e}");
            2
        }
    }
}

pub fn run_agent_command(cfg: &AppConfig, args: &[String]) -> i32 {
    if args.is_empty() || args.iter().any(|a| a == "-h" || a == "--help") {
        println!(
            "Usage: GameReComp agent <project_dir> [--cmd <ai_command>] [--function <addr>] [readability options]\n\
             \n\
             Drives AI-assisted source recovery via REVENG_AI_CMD while enforcing provenance and oracle gating."
        );
        return if args.is_empty() { 2 } else { 0 };
    }

    // Strip Rust-specific aliases before forwarding to the readability harness.
    let mut forwarded = Vec::with_capacity(args.len());
    let mut override_cmd: Option<String> = None;
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--cmd" && i + 1 < args.len() {
            override_cmd = Some(args[i + 1].clone());
            i += 2;
        } else {
            forwarded.push(args[i].clone());
            i += 1;
        }
    }
    let mut local_cfg = cfg.clone();
    if override_cmd.is_some() {
        local_cfg.reveng_ai_cmd = override_cmd;
    }
    run_readability(&local_cfg, &forwarded)
}
