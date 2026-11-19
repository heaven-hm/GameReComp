use std::path::PathBuf;
use std::process::{Command, Stdio};

use crate::config::AppConfig;
use crate::logging::log_to_file;

fn prepare_readability_args(
    default_ai_cmd: Option<&str>,
    args: &[String],
) -> (Option<String>, Option<PathBuf>, Vec<String>) {
    let mut project_dir: Option<PathBuf> = None;
    let mut ai_cmd = default_ai_cmd.map(|s| s.to_string());
    let mut forwarded = Vec::with_capacity(args.len());

    let mut i = 0;
    while i < args.len() {
        if (args[i] == "--cmd" || args[i] == "--ai-cmd") && i + 1 < args.len() {
            ai_cmd = Some(args[i + 1].clone());
            i += 2;
            continue;
        }
        if let Some(val) = args[i]
            .strip_prefix("--cmd=")
            .or_else(|| args[i].strip_prefix("--ai-cmd="))
        {
            ai_cmd = Some(val.to_string());
            i += 1;
            continue;
        }
        if !args[i].starts_with('-') && project_dir.is_none() {
            project_dir = Some(PathBuf::from(&args[i]));
        }
        forwarded.push(args[i].clone());
        i += 1;
    }

    (ai_cmd, project_dir, forwarded)
}

pub fn run_readability(cfg: &AppConfig, args: &[String]) -> i32 {
    let (ai_cmd, project_dir, forwarded) =
        prepare_readability_args(cfg.reveng_ai_cmd.as_deref(), args);

    if let Some(ref pdir) = project_dir {
        let log_path = pdir.join("logs").join("ai-agent.log");
        let cmd_desc = ai_cmd.as_deref().unwrap_or("<none>");
        log_to_file(
            &log_path,
            "agent",
            &format!("invoking optional C readability harness (REVENG_AI_CMD={cmd_desc})"),
        );
    }

    let mut cmd = Command::new("python3");
    cmd.current_dir(&cfg.repo_root)
        .env("PYTHONPATH", &cfg.repo_root)
        .args(["-m", "tools.autorecomp.readability"])
        .args(&forwarded)
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
            "Usage: game-recomp-cli agent <project_dir> [--cmd <ai_command>] [--function <addr>] [readability options]\n\
             \n\
             Optional AI readability pass — AI is strictly optional and ONLY used to improve readable C source code\n\
             (variable/function naming, comments, structure). Every edit is compile-checked and re-run against\n\
             baseline execution evidence; failed edits are automatically reverted."
        );
        return if args.is_empty() { 2 } else { 0 };
    }

    let (ai_cmd, _proj, mut forwarded) =
        prepare_readability_args(cfg.reveng_ai_cmd.as_deref(), args);
    if !forwarded
        .iter()
        .any(|a| a == "--providers" || a.starts_with("--providers="))
    {
        forwarded.push("--providers".to_string());
        forwarded.push("ai".to_string());
    }

    let mut local_cfg = cfg.clone();
    local_cfg.reveng_ai_cmd = ai_cmd;
    run_readability(&local_cfg, &forwarded)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_cmd_flag_and_tracks_project_dir() {
        let args: Vec<String> = vec![
            "/tmp/proj",
            "--cmd",
            "claude -p",
            "--function",
            "0x00402020",
        ]
        .into_iter()
        .map(String::from)
        .collect();
        let (cmd, proj, fwd) = prepare_readability_args(None, &args);
        assert_eq!(cmd.as_deref(), Some("claude -p"));
        assert_eq!(proj, Some(PathBuf::from("/tmp/proj")));
        assert_eq!(fwd, vec!["/tmp/proj", "--function", "0x00402020"]);
    }
}
