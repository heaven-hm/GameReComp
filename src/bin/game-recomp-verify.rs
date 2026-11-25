//! Pure-Rust verification backend CLI: process / trace / frames evidence.
use clap::{Parser, Subcommand};
use game_recomp::verify::{compare_runs, compare_traces, frame_hashes, run_command, sha256, Evidence, Verdict};
use std::path::PathBuf;
use std::time::Duration;

#[derive(Parser)]
#[command(name = "game-recomp-verify", version, about = "Fail-closed differential verification engine (pure Rust)")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Run original vs rebuilt with identical args/stdin; compare byte-for-byte.
    Process {
        original: PathBuf,
        rebuilt: PathBuf,
        #[arg(long, default_value = "highball run {exe}")]
        original_cmd: String,
        #[arg(long, default_value = "{exe}")]
        rebuilt_cmd: String,
        #[arg(long)]
        stdin_file: Option<PathBuf>,
        #[arg(long, default_value_t = 30.0)]
        timeout: f64,
        #[arg(long)]
        out: PathBuf,
    },
    /// Compare two watched-value traces within an absolute tolerance.
    Trace {
        a: PathBuf,
        b: PathBuf,
        #[arg(long, default_value_t = 0.001)]
        tolerance: f64,
        #[arg(long)]
        out: PathBuf,
    },
    /// Compare captured frame sets by SHA-256.
    Frames {
        a: PathBuf,
        b: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
}

fn write_out(out: &PathBuf, e: &Evidence) {
    if let Some(p) = out.parent() {
        let _ = std::fs::create_dir_all(p);
    }
    let text = serde_json::to_string_pretty(e).expect("serialize evidence");
    std::fs::write(out, format!("{}\n", text)).expect("write evidence");
    println!("{}", text);
}

fn main() {
    let cli = Cli::parse();
    let (out, evidence) = match cli.cmd {
        Cmd::Process { original, rebuilt, original_cmd, rebuilt_cmd, stdin_file, timeout, out } => {
            let mut e = Evidence::new("process");
            if !original.is_file() {
                e.reason = Some(format!("original not found: {}", original.display()));
            } else if !rebuilt.is_file() {
                e.reason = Some(format!("rebuilt not found: {}", rebuilt.display()));
            } else {
                e.inputs.insert("original".into(), sha256(&std::fs::read(&original).unwrap_or_default()));
                e.inputs.insert("rebuilt".into(), sha256(&std::fs::read(&rebuilt).unwrap_or_default()));
                let stdin = stdin_file.as_ref().and_then(|p| std::fs::read(p).ok()).unwrap_or_default();
                let a_cmd = original_cmd.replace("{exe}", &original.to_string_lossy());
                let b_cmd = rebuilt_cmd.replace("{exe}", &rebuilt.to_string_lossy());
                let t = Duration::from_secs_f64(timeout.max(0.1));
                let a = run_command(&a_cmd, t, &stdin);
                let b = run_command(&b_cmd, t, &stdin);
                e = compare_runs(&a, &b);
                e.inputs.insert("original".into(), sha256(&std::fs::read(&original).unwrap_or_default()));
                e.inputs.insert("rebuilt".into(), sha256(&std::fs::read(&rebuilt).unwrap_or_default()));
            }
            (out, e)
        }
        Cmd::Trace { a, b, tolerance, out } => {
            let e = match (std::fs::read_to_string(&a), std::fs::read_to_string(&b)) {
                (Ok(x), Ok(y)) => {
                    let mut ev = compare_traces(&x, &y, tolerance);
                    ev.inputs.insert("a".into(), sha256(x.as_bytes()));
                    ev.inputs.insert("b".into(), sha256(y.as_bytes()));
                    ev
                }
                _ => {
                    let mut ev = Evidence::new("trace");
                    ev.reason = Some("trace file unreadable".into());
                    ev
                }
            };
            (out, e)
        }
        Cmd::Frames { a, b, out } => {
            let ha = frame_hashes(&a);
            let hb = frame_hashes(&b);
            let mut e = Evidence::new("frames");
            e.checks.insert("frames_present".into(), !ha.is_empty() && !hb.is_empty());
            e.checks.insert("frames_exact".into(), !ha.is_empty() && ha == hb);
            e.difference = Some(serde_json::json!({"original": ha, "rebuilt": hb}));
            e = e.finish();
            (out, e)
        }
    };
    write_out(&out, &evidence);
    if evidence.verdict != Verdict::Pass {
        std::process::exit(1);
    }
}
