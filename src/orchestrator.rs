use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use indicatif::{ProgressBar, ProgressStyle};
use owo_colors::OwoColorize;

use crate::config::AppConfig;
use crate::logging::log_project_event;
use crate::model::ProjectModel;
use crate::pe::{resolve_input, resolve_target};
use crate::tasks::{clean_cmake_objects, verify_standalone_binary};

pub const EXIT_OK: i32 = 0;
pub const EXIT_STAGE_FAILED: i32 = 2;
pub const EXIT_UNVERIFIED: i32 = 3;
pub const EXIT_UNSUPPORTED: i32 = 4;

pub const STAGES: [&str; 8] = [
    "inspect",
    "recover",
    "translate",
    "project",
    "compile",
    "run",
    "verify",
    "report",
];

#[derive(Debug, Clone)]
pub struct ParsedCli {
    pub pe_file: Option<String>,
    pub project_dir: Option<PathBuf>,
    pub out_mode: Option<String>,
    pub stages: Option<Vec<String>>,
    pub skip_stages: Option<Vec<String>>,
    pub list_stages: bool,
    pub input_os: String,
    pub input_arch: String,
    pub target_os: String,
    pub target_arch: String,
    pub dry_run: bool,
    pub no_table: bool,
    pub no_progress: bool,
    pub quiet: bool,
    pub json: bool,
    pub clean_objects: bool,
    pub verify_standalone: bool,
    pub capture_screenshots: bool,
    pub ai_readability: bool,
    pub profile: Option<String>,
    pub backend_args: Vec<String>,
}

impl Default for ParsedCli {
    fn default() -> Self {
        Self {
            pe_file: None,
            project_dir: None,
            out_mode: None,
            stages: None,
            skip_stages: None,
            list_stages: false,
            input_os: "auto".to_string(),
            input_arch: "auto".to_string(),
            target_os: "host".to_string(),
            target_arch: "host".to_string(),
            dry_run: false,
            no_table: false,
            no_progress: false,
            quiet: false,
            json: false,
            clean_objects: false,
            verify_standalone: false,
            capture_screenshots: false,
            ai_readability: false,
            profile: None,
            backend_args: Vec::new(),
        }
    }
}

pub fn executable_name(pe_path: &Path) -> String {
    let stem = pe_path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("game");
    let mut safe: String = stem
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    if safe.is_empty() || safe.as_bytes()[0].is_ascii_digit() {
        safe = format!("game_{safe}");
    }
    safe
}

pub fn default_project_dir(repo_root: &Path, pe_path: &Path) -> PathBuf {
    repo_root.join("projects").join(executable_name(pe_path))
}

fn split_stages_csv(s: &str) -> Vec<String> {
    s.split(',')
        .map(|x| x.trim().to_string())
        .filter(|x| !x.is_empty())
        .collect()
}

pub fn parse_pipeline_args(args: &[String]) -> Result<ParsedCli, String> {
    let mut out = ParsedCli::default();
    let mut i = 0;
    while i < args.len() {
        let a = &args[i];
        match a.as_str() {
            "--list-stages" => {
                out.list_stages = true;
                i += 1;
            }
            "--dry-run" => {
                out.dry_run = true;
                i += 1;
            }
            "--clean-objects" => {
                out.clean_objects = true;
                i += 1;
            }
            "--verify-standalone" => {
                out.verify_standalone = true;
                i += 1;
            }
            "--capture-screenshots" => {
                out.capture_screenshots = true;
                i += 1;
            }
            "--ai-readability" => {
                out.ai_readability = true;
                i += 1;
            }
            "--profile" => {
                let val = args
                    .get(i + 1)
                    .ok_or("--profile requires a value (game|desktop|console)")?;
                match val.as_str() {
                    "game" | "desktop" | "console" => {
                        out.profile = Some(val.clone());
                        if val == "game" || val == "desktop" {
                            out.capture_screenshots = true;
                        }
                    }
                    other => {
                        return Err(format!(
                            "unknown --profile: {other} (expected game, desktop, or console)"
                        ))
                    }
                }
                i += 2;
            }
            "--no-table" => {
                out.no_table = true;
                out.backend_args.push(a.clone());
                i += 1;
            }
            "--no-progress" => {
                out.no_progress = true;
                out.backend_args.push(a.clone());
                i += 1;
            }
            "--quiet" => {
                out.quiet = true;
                out.backend_args.push(a.clone());
                i += 1;
            }
            "--json" => {
                out.json = true;
                out.backend_args.push(a.clone());
                i += 1;
            }
            "--project-dir" => {
                let val = args.get(i + 1).ok_or("--project-dir requires a value")?;
                out.project_dir = Some(PathBuf::from(val));
                out.backend_args.push(a.clone());
                out.backend_args.push(val.clone());
                i += 2;
            }
            "--out-mode" => {
                let val = args.get(i + 1).ok_or("--out-mode requires a value")?;
                out.out_mode = Some(val.clone());
                out.backend_args.push(a.clone());
                out.backend_args.push(val.clone());
                i += 2;
            }
            "--stages" => {
                let val = args.get(i + 1).ok_or("--stages requires a value")?;
                out.stages = Some(split_stages_csv(val));
                out.backend_args.push(a.clone());
                out.backend_args.push(val.clone());
                i += 2;
            }
            "--skip-stages" => {
                let val = args.get(i + 1).ok_or("--skip-stages requires a value")?;
                out.skip_stages = Some(split_stages_csv(val));
                out.backend_args.push(a.clone());
                out.backend_args.push(val.clone());
                i += 2;
            }
            "--input-os" | "--in-os" => {
                let val = args.get(i + 1).ok_or("--input-os requires a value")?;
                out.input_os = val.clone();
                out.backend_args.push(a.clone());
                out.backend_args.push(val.clone());
                i += 2;
            }
            "--input-arch" | "--in-arch" => {
                let val = args.get(i + 1).ok_or("--input-arch requires a value")?;
                out.input_arch = val.clone();
                out.backend_args.push(a.clone());
                out.backend_args.push(val.clone());
                i += 2;
            }
            "--target-os" | "--output-os" | "--out-os" => {
                let val = args.get(i + 1).ok_or("--target-os requires a value")?;
                out.target_os = val.clone();
                out.backend_args.push(a.clone());
                out.backend_args.push(val.clone());
                i += 2;
            }
            "--target-arch" | "--output-arch" | "--out-arch" => {
                let val = args.get(i + 1).ok_or("--target-arch requires a value")?;
                out.target_arch = val.clone();
                out.backend_args.push(a.clone());
                out.backend_args.push(val.clone());
                i += 2;
            }
            "--assets-dir" | "--timeout" | "--oracle" | "--jobs" | "--split" | "--split-bytes"
            | "--build-timeout" | "--highlevel" => {
                let val = args
                    .get(i + 1)
                    .ok_or_else(|| format!("{a} requires a value"))?;
                out.backend_args.push(a.clone());
                out.backend_args.push(val.clone());
                i += 2;
            }
            "--interactive" | "--reuse-build" | "--import-trace" | "--fresh-build"
            | "--no-build" | "--no-run" | "--verbose" => {
                out.backend_args.push(a.clone());
                i += 1;
            }
            flag if flag.starts_with("--") => {
                if let Some((k, _)) = flag.split_once('=') {
                    match k {
                        "--project-dir" => {
                            out.project_dir = Some(PathBuf::from(&flag[k.len() + 1..]))
                        }
                        "--out-mode" => out.out_mode = Some(flag[k.len() + 1..].to_string()),
                        "--stages" => out.stages = Some(split_stages_csv(&flag[k.len() + 1..])),
                        "--skip-stages" => {
                            out.skip_stages = Some(split_stages_csv(&flag[k.len() + 1..]))
                        }
                        "--input-os" | "--in-os" => out.input_os = flag[k.len() + 1..].to_string(),
                        "--input-arch" | "--in-arch" => {
                            out.input_arch = flag[k.len() + 1..].to_string()
                        }
                        "--target-os" | "--output-os" | "--out-os" => {
                            out.target_os = flag[k.len() + 1..].to_string()
                        }
                        "--target-arch" | "--output-arch" | "--out-arch" => {
                            out.target_arch = flag[k.len() + 1..].to_string()
                        }
                        _ => {}
                    }
                    out.backend_args.push(a.clone());
                    i += 1;
                } else {
                    return Err(format!("unknown option: {flag}"));
                }
            }
            pos => {
                if out.pe_file.is_none() {
                    out.pe_file = Some(pos.to_string());
                    out.backend_args.push(pos.to_string());
                    i += 1;
                } else {
                    return Err(format!("unexpected extra positional argument: {pos}"));
                }
            }
        }
    }
    Ok(out)
}

pub fn resolve_stages(parsed: &ParsedCli) -> Result<Vec<String>, String> {
    if parsed.out_mode.is_some() && parsed.stages.is_some() {
        return Err("--out-mode and --stages are mutually exclusive".to_string());
    }

    let base: Vec<&str> = match parsed.out_mode.as_deref() {
        None | Some("both") | Some("full") => STAGES.to_vec(),
        Some("source") | Some("source-only") => {
            vec!["inspect", "recover", "translate", "project"]
        }
        Some("binary") | Some("binary-only") => {
            vec![
                "inspect",
                "recover",
                "translate",
                "project",
                "compile",
                "report",
            ]
        }
        Some(other) => return Err(format!("unknown --out-mode: {other}")),
    };

    let mut selected: Vec<String> = if let Some(ref explicit) = parsed.stages {
        for st in explicit {
            if !STAGES.contains(&st.as_str()) {
                return Err(format!("unknown stage: {st}"));
            }
        }
        STAGES
            .iter()
            .filter(|st| explicit.iter().any(|x| x == *st))
            .map(|s| s.to_string())
            .collect()
    } else {
        base.into_iter().map(|s| s.to_string()).collect()
    };

    if let Some(ref skip) = parsed.skip_stages {
        for st in skip {
            if !STAGES.contains(&st.as_str()) {
                return Err(format!("unknown stage in --skip-stages: {st}"));
            }
            if st == "report" {
                return Err("stage 'report' cannot be skipped".to_string());
            }
        }
        selected.retain(|s| !skip.iter().any(|x| x == s));
    }

    Ok(selected)
}

pub fn run_pipeline_cli(cfg: &AppConfig, raw_args: &[String]) -> i32 {
    let parsed = match parse_pipeline_args(raw_args) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("error: {e}");
            return EXIT_STAGE_FAILED;
        }
    };

    if parsed.list_stages {
        for s in STAGES {
            println!("{s}");
        }
        return EXIT_OK;
    }

    let stages = match resolve_stages(&parsed) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("error: {e}");
            return EXIT_STAGE_FAILED;
        }
    };

    let Some(ref pe_arg) = parsed.pe_file else {
        eprintln!("error: no input PE executable provided");
        return EXIT_STAGE_FAILED;
    };
    let pe_path = PathBuf::from(pe_arg);

    let (target_os, target_arch) = match resolve_target(&parsed.target_os, &parsed.target_arch) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("error: {e}");
            return EXIT_UNSUPPORTED;
        }
    };

    let (in_os, in_arch) = match resolve_input(&pe_path, &parsed.input_os, &parsed.input_arch) {
        Ok(i) => i,
        Err(e) => {
            eprintln!("error: {e}");
            return EXIT_STAGE_FAILED;
        }
    };

    if !parsed.quiet && !parsed.json {
        let mut omitted = Vec::new();
        if parsed.input_os == "auto" {
            omitted.push("--input-os");
        }
        if parsed.input_arch == "auto" {
            omitted.push("--input-arch");
        }
        if parsed.target_os == "host" {
            omitted.push("--target-os");
        }
        if parsed.target_arch == "host" {
            omitted.push("--target-arch");
        }
        if !omitted.is_empty() {
            eprintln!(
                "note: input/output identity not fully specified (omitted: {}); assuming {in_os}/{in_arch} -> {target_os}/{target_arch}",
                omitted.join(", ")
            );
        }
    }

    let proj_dir = parsed
        .project_dir
        .clone()
        .unwrap_or_else(|| default_project_dir(&cfg.repo_root, &pe_path));

    if parsed.dry_run {
        println!("plan:");
        println!("  input:   {} ({in_os}/{in_arch})", pe_path.display());
        println!("  target:  {target_os}/{target_arch}");
        println!("  project: {}", proj_dir.display());
        println!("  stages:  {}", stages.join(", "));
        if parsed.ai_readability {
            println!("  ai_opt:  optional C source readability pass enabled (compile- & evidence-verified)");
        }
        return EXIT_OK;
    }

    let started = Instant::now();
    if proj_dir.is_dir() {
        log_project_event(
            &proj_dir,
            "orchestrator",
            &format!("starting pipeline for {}", pe_path.display()),
        );
    }

    // Drive Python RE/lifting/compile backend while keeping orchestration in Rust.
    let mut cmd = Command::new(crate::config::python_interpreter(&cfg.repo_root));
    cmd.current_dir(&cfg.repo_root)
        .env("PYTHONPATH", &cfg.repo_root)
        .args(["-m", "tools.autorecomp.cli"])
        .args(&parsed.backend_args);

    for (k, v) in &cfg.corpus_env {
        cmd.env(k, v);
    }
    if let Some(ref sdir) = cfg.screenshot_dir {
        cmd.env("RECOMP_SCREENSHOT", sdir);
    } else if parsed.capture_screenshots {
        let shots = proj_dir.join("screenshots");
        let _ = fs::create_dir_all(&shots);
        cmd.env("RECOMP_SCREENSHOT", shots);
    }
    if let Some(ref adir) = cfg.assets_dir {
        cmd.env("RECOMP_ASSETS_DIR", adir);
    }
    if !parsed
        .backend_args
        .iter()
        .any(|a| a == "--timeout" || a.starts_with("--timeout="))
    {
        cmd.args(["--timeout", &format!("{}", cfg.default_timeout_secs)]);
    }

    let use_spinner = !parsed.quiet && !parsed.json && !parsed.no_progress;

    let exit_code = if use_spinner {
        let title = pe_path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("executable");
        eprintln!(
            "  {}{}  {} {} -> {}/{}",
            "Game".bold(),
            "ReComp".black().on_bright_yellow().bold(),
            title.bold(),
            format!("({in_os}/{in_arch})").dimmed(),
            target_os,
            target_arch
        );

        let pb = ProgressBar::new_spinner();
        pb.set_style(
            ProgressStyle::with_template("  {spinner:.yellow} {msg}  [{elapsed_precise}]")
                .unwrap_or_else(|_| ProgressStyle::default_spinner()),
        );
        pb.enable_steady_tick(Duration::from_millis(90));
        pb.set_message("stage: inspect");

        cmd.stdin(Stdio::inherit())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        match cmd.spawn() {
            Ok(mut child) => {
                let pb_err = pb.clone();
                let err_handle = child.stderr.take().map(|err| {
                    std::thread::spawn(move || {
                        for line in BufReader::new(err).lines().map_while(Result::ok) {
                            if let Some(rest) = line.trim().strip_prefix("==>") {
                                pb_err.set_message(format!("stage: {}", rest.trim()));
                            }
                            pb_err.println(line);
                        }
                    })
                });
                if let Some(out) = child.stdout.take() {
                    for line in BufReader::new(out).lines().map_while(Result::ok) {
                        if let Some(rest) = line.trim().strip_prefix("==>") {
                            pb.set_message(format!("stage: {}", rest.trim()));
                        }
                        pb.println(line);
                    }
                }
                if let Some(h) = err_handle {
                    let _ = h.join();
                }
                let code = child
                    .wait()
                    .ok()
                    .and_then(|st| st.code())
                    .unwrap_or(EXIT_STAGE_FAILED);
                if code == EXIT_OK || code == EXIT_UNVERIFIED {
                    pb.finish_with_message(format!(
                        "{} finished (exit {code})",
                        "✓".green().bold()
                    ));
                } else {
                    pb.finish_with_message(format!("{} failed (exit {code})", "✗".red().bold()));
                }
                code
            }
            Err(e) => {
                pb.finish_and_clear();
                eprintln!("error: failed to launch Python backend: {e}");
                EXIT_STAGE_FAILED
            }
        }
    } else {
        cmd.stdin(Stdio::inherit())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit());

        match cmd.status() {
            Ok(st) => st.code().unwrap_or(EXIT_STAGE_FAILED),
            Err(e) => {
                eprintln!("error: failed to launch Python backend: {e}");
                EXIT_STAGE_FAILED
            }
        }
    };

    let elapsed = started.elapsed().as_secs_f64();
    if proj_dir.is_dir() {
        log_project_event(
            &proj_dir,
            "orchestrator",
            &format!("backend finished with exit={exit_code} in {elapsed:.2}s"),
        );
    }

    if parsed.clean_objects {
        let _ = clean_cmake_objects(&proj_dir);
    }

    if parsed.verify_standalone && (exit_code == EXIT_OK || exit_code == EXIT_UNVERIFIED) {
        if let Ok(model) = ProjectModel::load(&proj_dir) {
            if let Some(bin) = model.built_binary_path() {
                match verify_standalone_binary(&bin, 700) {
                    Ok(msg) => {
                        if !parsed.quiet && !parsed.json {
                            eprintln!("standalone:   OK ({msg})");
                        }
                        log_project_event(&proj_dir, "verify-standalone", &format!("PASS: {msg}"));
                    }
                    Err(err) => {
                        eprintln!("standalone:   FAIL ({err})");
                        log_project_event(&proj_dir, "verify-standalone", &format!("FAIL: {err}"));
                        return EXIT_STAGE_FAILED;
                    }
                }
            }
        }
    }

    if !parsed.no_table {
        let _ = fs::create_dir_all(cfg.repo_root.join("reports").join("games"));
    }

    if parsed.ai_readability && (exit_code == EXIT_OK || exit_code == EXIT_UNVERIFIED) {
        if cfg.reveng_ai_cmd.is_some() {
            log_project_event(
                &proj_dir,
                "orchestrator",
                "running optional AI readable-source-code pass",
            );
            if !parsed.quiet && !parsed.json {
                eprintln!("ai-readability: running optional C source readability pass (compile- & evidence-verified)...");
            }
            let _ = crate::agent::run_readability(
                cfg,
                &[
                    proj_dir.display().to_string(),
                    "--providers".to_string(),
                    "ai".to_string(),
                ],
            );
        } else if !parsed.quiet && !parsed.json {
            eprintln!(
                "note: --ai-readability skipped because REVENG_AI_CMD is not configured. AI is strictly optional and only used to improve readable C source code."
            );
        }
    }

    exit_code
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_stage_modes_and_skips() {
        let mut p = ParsedCli::default();
        p.out_mode = Some("binary".to_string());
        let st = resolve_stages(&p).unwrap();
        assert!(st.contains(&"compile".to_string()));
        assert!(!st.contains(&"run".to_string()));

        p.out_mode = None;
        p.stages = Some(vec!["report".to_string(), "inspect".to_string()]);
        assert_eq!(resolve_stages(&p).unwrap(), vec!["inspect", "report"]);

        p.skip_stages = Some(vec!["report".to_string()]);
        assert!(resolve_stages(&p).is_err());
    }

    #[test]
    fn generates_cmake_safe_project_name() {
        assert_eq!(
            executable_name(Path::new("/tmp/putty-w32.exe")),
            "putty_w32"
        );
        assert_eq!(executable_name(Path::new("/tmp/123app.exe")), "game_123app");
    }

    #[test]
    fn parses_profile_and_screenshot_flags() {
        let args: Vec<String> = vec![
            "game.exe",
            "--profile",
            "console",
            "--target-os",
            "web",
            "--target-arch",
            "wasm32",
            "--capture-screenshots",
            "--dry-run",
        ]
        .into_iter()
        .map(String::from)
        .collect();
        let parsed = parse_pipeline_args(&args).unwrap();
        assert_eq!(parsed.profile.as_deref(), Some("console"));
        assert_eq!(parsed.target_os, "web");
        assert_eq!(parsed.target_arch, "wasm32");
        assert!(parsed.capture_screenshots);
        assert!(parsed.dry_run);
        assert!(!parsed.ai_readability);
    }

    #[test]
    fn parses_optional_ai_readability_flag() {
        let args: Vec<String> = vec!["game.exe", "--ai-readability"]
            .into_iter()
            .map(String::from)
            .collect();
        let parsed = parse_pipeline_args(&args).unwrap();
        assert!(parsed.ai_readability);
    }
}
