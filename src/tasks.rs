use std::collections::{HashSet, VecDeque};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use crate::config::AppConfig;
use crate::json::JsonValue;
use crate::logging::unix_timestamp_secs;
use crate::model::ProjectModel;
use crate::orchestrator::{default_project_dir, run_pipeline_cli, EXIT_OK, EXIT_UNVERIFIED};
use crate::pe::inspect_pe;

#[derive(Debug, Clone)]
pub struct BatchTaskResult {
    pub exe_name: String,
    pub project_dir: PathBuf,
    pub exit_code: i32,
    pub build_passed: bool,
    pub boot_passed: bool,
    pub strict_boot_passed: bool,
    pub unresolved_imports: i64,
    pub unhandled_eips: i64,
    pub crash_signal: Option<String>,
    pub standalone_ok: bool,
    pub standalone_note: String,
    pub elapsed_secs: f64,
}

pub fn collect_exes(inputs: &[String]) -> Vec<PathBuf> {
    let mut exes = Vec::new();
    let mut seen_files = HashSet::new();
    let mut seen_dirs = HashSet::new();
    for raw in inputs {
        collect_exes_from_path(
            &PathBuf::from(raw),
            &mut exes,
            &mut seen_files,
            &mut seen_dirs,
        );
    }
    exes
}

fn collect_exes_from_path(
    path: &Path,
    exes: &mut Vec<PathBuf>,
    seen_files: &mut HashSet<PathBuf>,
    seen_dirs: &mut HashSet<PathBuf>,
) {
    if path.is_file() {
        if !path
            .extension()
            .and_then(|e| e.to_str())
            .map(|s| s.eq_ignore_ascii_case("exe"))
            .unwrap_or(false)
        {
            return;
        }
        if let Ok(canonical) = fs::canonicalize(path) {
            if seen_files.insert(canonical) {
                exes.push(path.to_path_buf());
            }
        }
        return;
    }

    if !path.is_dir() {
        return;
    }
    let Ok(canonical_dir) = fs::canonicalize(path) else {
        return;
    };
    if !seen_dirs.insert(canonical_dir) {
        return;
    }
    let Ok(entries) = fs::read_dir(path) else {
        return;
    };
    let mut children: Vec<PathBuf> = entries
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .collect();
    children.sort();
    for child in children {
        collect_exes_from_path(&child, exes, seen_files, seen_dirs);
    }
}

fn batch_project_dir(repo_root: &Path, pe_path: &Path) -> PathBuf {
    let canonical = fs::canonicalize(pe_path).unwrap_or_else(|_| pe_path.to_path_buf());
    let hash = canonical
        .to_string_lossy()
        .bytes()
        .fold(0x811c9dc5_u32, |hash, byte| {
            (hash ^ byte as u32).wrapping_mul(0x01000193)
        });
    let base = default_project_dir(repo_root, &canonical);
    base.with_file_name(format!(
        "{}_{hash:08x}",
        base.file_name().unwrap().to_string_lossy()
    ))
}

pub fn clean_cmake_objects(project_dir: &Path) -> std::io::Result<()> {
    let obj_dir = project_dir.join("build").join("CMakeFiles");
    if obj_dir.exists() {
        fs::remove_dir_all(obj_dir)?;
    }
    let keep_sources =
        project_dir.file_name().and_then(|s| s.to_str()) == Some("cavestory-doukutsu");
    if !keep_sources && project_dir.join("reports").join("report.json").is_file() {
        let gen_dir = project_dir.join("src").join("recomp").join("gen");
        if gen_dir.exists() {
            fs::remove_dir_all(gen_dir)?;
        }
        let img_c = project_dir.join("src").join("image_data.c");
        if img_c.is_file() {
            fs::remove_file(img_c)?;
        }
        let runtime_dir = project_dir.join("src").join("runtime");
        if runtime_dir.exists() {
            fs::remove_dir_all(runtime_dir)?;
        }
    }
    Ok(())
}

pub fn available_disk_mb(path: &Path) -> Option<u64> {
    let out = Command::new("df").args(["-m"]).arg(path).output().ok()?;
    if !out.status.success() {
        return None;
    }
    let txt = String::from_utf8_lossy(&out.stdout);
    let line = txt.lines().nth(1)?;
    let cols: Vec<&str> = line.split_whitespace().collect();
    cols.get(3).and_then(|v| v.parse::<u64>().ok())
}

/// Executes a rebuilt binary standalone from /tmp with no arguments, verifying
/// that it does not require the original .exe or companion .dlls on disk and
/// either exits cleanly or stays running stably past `timeout_ms` (GUI apps).
pub fn verify_standalone_binary(binary: &Path, timeout_ms: u64) -> Result<String, String> {
    if !binary.is_file() {
        return Err(format!("missing binary at {}", binary.display()));
    }
    let tmp = std::env::temp_dir();
    let mut child = Command::new(binary)
        .current_dir(&tmp)
        .env("SDL_VIDEODRIVER", "dummy")
        .env("SDL_AUDIODRIVER", "dummy")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn failed: {e}"))?;

    let start = Instant::now();
    let deadline = Duration::from_millis(timeout_ms);
    let mut exited = None;

    while start.elapsed() < deadline {
        match child.try_wait() {
            Ok(Some(status)) => {
                exited = Some(status);
                break;
            }
            Ok(None) => thread::sleep(Duration::from_millis(25)),
            Err(e) => return Err(format!("wait error: {e}")),
        }
    }

    let survived_to_timeout = exited.is_none();
    if survived_to_timeout {
        let _ = child.kill();
        let _ = child.wait();
    }

    let mut stderr_buf = String::new();
    if let Some(mut err) = child.stderr.take() {
        let _ = err.read_to_string(&mut stderr_buf);
    }

    if stderr_buf.contains("UNRESOLVED") || stderr_buf.contains("boot-failed") {
        let snippet: String = stderr_buf.lines().take(3).collect::<Vec<_>>().join(" | ");
        return Err(format!("boot failure in standalone run: {snippet}"));
    }
    if stderr_buf.contains("hle_fatal") || stderr_buf.contains("unhandled EIP") {
        let snippet: String = stderr_buf
            .lines()
            .filter(|l| l.contains("hle_fatal") || l.contains("unhandled"))
            .take(2)
            .collect::<Vec<_>>()
            .join(" | ");
        return Err(format!("runtime trap in standalone run: {snippet}"));
    }

    if let Some(status) = exited {
        #[cfg(unix)]
        {
            use std::os::unix::process::ExitStatusExt;
            if let Some(sig) = status.signal() {
                return Err(format!("crashed with signal {sig}"));
            }
        }
        let code = status.code().unwrap_or(-1);
        if code == 2 {
            let snippet: String = stderr_buf.lines().take(2).collect::<Vec<_>>().join(" | ");
            return Err(format!("exited 2 (boot failure): {snippet}"));
        }
        return Ok(format!(
            "exited {code} in {}ms",
            start.elapsed().as_millis()
        ));
    }

    Ok(format!("ran cleanly for {timeout_ms}ms headless"))
}

#[derive(Debug, Clone)]
struct BatchArgs {
    inputs: Vec<String>,
    timeout: String,
    parallel: usize,
    clean_objects: bool,
    verify_standalone: bool,
    skip_existing: bool,
    limit: Option<usize>,
    offset: usize,
}

fn parse_batch_args(args: &[String]) -> Result<BatchArgs, String> {
    let mut parsed = BatchArgs {
        inputs: Vec::new(),
        timeout: "10".to_string(),
        parallel: 1,
        clean_objects: false,
        verify_standalone: false,
        skip_existing: false,
        limit: None,
        offset: 0,
    };

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--timeout" => {
                if let Some(v) = args.get(i + 1) {
                    parsed.timeout = v.clone();
                    i += 2;
                } else {
                    return Err("error: --timeout requires a value".to_string());
                }
            }
            "--parallel" | "-p" => {
                if let Some(v) = args.get(i + 1).and_then(|x| x.parse::<usize>().ok()) {
                    parsed.parallel = v.clamp(1, 16);
                    i += 2;
                } else {
                    return Err("error: --parallel requires an integer (1..=16)".to_string());
                }
            }
            "--clean-objects" => {
                parsed.clean_objects = true;
                i += 1;
            }
            "--verify-standalone" => {
                parsed.verify_standalone = true;
                i += 1;
            }
            "--skip-existing" => {
                parsed.skip_existing = true;
                i += 1;
            }
            "--limit" => {
                if let Some(v) = args.get(i + 1).and_then(|x| x.parse::<usize>().ok()) {
                    parsed.limit = Some(v);
                    i += 2;
                } else {
                    return Err("error: --limit requires an integer".to_string());
                }
            }
            "--offset" => {
                if let Some(v) = args.get(i + 1).and_then(|x| x.parse::<usize>().ok()) {
                    parsed.offset = v;
                    i += 2;
                } else {
                    return Err("error: --offset requires an integer".to_string());
                }
            }
            other => {
                parsed.inputs.push(other.to_string());
                i += 1;
            }
        }
    }
    Ok(parsed)
}

fn run_single_batch_item(
    cfg: &AppConfig,
    exe: &Path,
    idx: usize,
    total: usize,
    opts: &BatchArgs,
) -> Result<Option<BatchTaskResult>, i32> {
    let exe_name = exe
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown.exe")
        .to_string();
    let proj_dir = batch_project_dir(&cfg.repo_root, exe);

    if let Some(avail) = available_disk_mb(&cfg.repo_root) {
        if avail < 1536 {
            eprintln!(
                "STOP: available disk space ({avail} MiB) dropped below 1536 MiB threshold before {exe_name}"
            );
            return Err(2);
        }
    }

    if opts.skip_existing {
        if let Ok(m) = ProjectModel::load(&proj_dir) {
            if m.diag.build_passed
                && m.diag.strict_boot_passed
                && m.diag.unresolved_import_count == 0
                && m.built_binary_path().is_some()
            {
                if opts.clean_objects {
                    let _ = clean_cmake_objects(&proj_dir);
                }
                println!("[{}/{}] SKIP (already passing): {exe_name}", idx + 1, total);
                return Ok(None);
            }
        }
    }

    println!("[{}/{}] RUN {exe_name}", idx + 1, total);
    let t0 = Instant::now();
    let mut run_args = vec![
        exe.to_string_lossy().to_string(),
        "--project-dir".to_string(),
        proj_dir.to_string_lossy().to_string(),
        "--input-os".to_string(),
        "auto".to_string(),
        "--input-arch".to_string(),
        "auto".to_string(),
        "--target-os".to_string(),
        "auto".to_string(),
        "--target-arch".to_string(),
        "auto".to_string(),
        "--timeout".to_string(),
        opts.timeout.clone(),
        "--no-progress".to_string(),
        "--no-table".to_string(),
    ];
    if opts.clean_objects {
        run_args.push("--clean-objects".to_string());
    }

    let code = run_pipeline_cli(cfg, &run_args);
    if opts.clean_objects {
        let _ = clean_cmake_objects(&proj_dir);
    }

    let model = ProjectModel::load(&proj_dir).ok();
    let (standalone_ok, standalone_note) = if opts.verify_standalone {
        match model.as_ref().and_then(|m| m.built_binary_path()) {
            Some(bin) => match verify_standalone_binary(&bin, 600) {
                Ok(msg) => (true, msg),
                Err(err) => (false, err),
            },
            None => (false, "no built binary".to_string()),
        }
    } else {
        (true, "not requested".to_string())
    };

    let diag = model.as_ref().map(|m| m.diag.clone()).unwrap_or_default();
    let res = BatchTaskResult {
        exe_name: exe_name.clone(),
        project_dir: proj_dir,
        exit_code: code,
        build_passed: diag.build_passed,
        boot_passed: diag.boot_passed,
        strict_boot_passed: diag.strict_boot_passed,
        unresolved_imports: diag.unresolved_import_count,
        unhandled_eips: diag.unhandled_eip_count,
        crash_signal: diag.crash_signal,
        standalone_ok,
        standalone_note: standalone_note.clone(),
        elapsed_secs: t0.elapsed().as_secs_f64(),
    };

    let status_tag = if (res.exit_code == EXIT_OK || res.exit_code == EXIT_UNVERIFIED)
        && res.build_passed
        && res.strict_boot_passed
        && res.unresolved_imports == 0
        && res.unhandled_eips == 0
        && res.crash_signal.is_none()
        && res.standalone_ok
    {
        "PASS"
    } else {
        "FAIL"
    };
    println!(
        "  => {status_tag} {exe_name}: rc={} build={} boot={} strict={} unresolved={} eip={} crash={:?} standalone={} ({}) [{:.1}s]",
        res.exit_code,
        res.build_passed,
        res.boot_passed,
        res.strict_boot_passed,
        res.unresolved_imports,
        res.unhandled_eips,
        res.crash_signal,
        res.standalone_ok,
        res.standalone_note,
        res.elapsed_secs
    );
    Ok(Some(res))
}

pub fn run_batch_command(cfg: &AppConfig, args: &[String]) -> i32 {
    if args.is_empty() || args.iter().any(|a| a == "-h" || a == "--help") {
        println!(
            "Usage: game-recomp-cli batch <path_or_dir...> [--parallel N] [--timeout N] [--clean-objects] [--verify-standalone] [--limit N] [--offset N] [--skip-existing]\n\
             Recursively discovers inputs; unsupported/non-PE32-i386 executables are skipped."
        );
        return if args.is_empty() { 2 } else { 0 };
    }

    let parsed = match parse_batch_args(args) {
        Ok(p) => p,
        Err(msg) => {
            eprintln!("{msg}");
            return 2;
        }
    };

    let candidates = collect_exes(&parsed.inputs);
    if candidates.is_empty() {
        eprintln!("error: no .exe files found in inputs");
        return 2;
    }

    let wanted = parsed.limit.unwrap_or(usize::MAX);
    let mut slice: Vec<PathBuf> = Vec::new();
    let mut supported_seen = 0;
    let mut unsupported_count = 0;
    let mut inspection_errors = 0;
    for exe in candidates {
        let info = inspect_pe(&exe);
        if !info.ok
            && (info.error == "cannot read the file" || info.error == "cannot read the PE header")
        {
            eprintln!("warning: cannot inspect {}: {}", exe.display(), info.error);
            inspection_errors += 1;
            continue;
        }
        if !info.pe32_x86 {
            unsupported_count += 1;
            continue;
        }
        if supported_seen >= parsed.offset {
            slice.push(exe);
            if slice.len() == wanted {
                break;
            }
        }
        supported_seen += 1;
    }
    if slice.is_empty() && supported_seen == 0 {
        println!(
            "batch: no supported PE32 i386 executable(s); skipped_unsupported={unsupported_count}, inspection_errors={inspection_errors}"
        );
        return if inspection_errors == 0 { 0 } else { 2 };
    }
    if slice.is_empty() {
        eprintln!(
            "error: offset {} is past the discovered supported PE32 i386 inputs",
            parsed.offset
        );
        return 2;
    }

    println!(
        "batch: processing {} supported PE32 i386 executable(s) (parallel={}, offset={}, clean_objects={}, verify_standalone={}, skipped_unsupported={}, inspection_errors={})",
        slice.len(),
        parsed.parallel,
        parsed.offset,
        parsed.clean_objects,
        parsed.verify_standalone,
        unsupported_count,
        inspection_errors
    );

    let total = slice.len();
    let results: Vec<BatchTaskResult> = if parsed.parallel <= 1 || total <= 1 {
        let mut seq = Vec::with_capacity(total);
        for (idx, exe) in slice.iter().enumerate() {
            match run_single_batch_item(cfg, exe, idx, total, &parsed) {
                Ok(Some(res)) => seq.push(res),
                Ok(None) => {}
                Err(code) => return code,
            }
        }
        seq
    } else {
        let queue: Mutex<VecDeque<(usize, PathBuf)>> =
            Mutex::new(slice.into_iter().enumerate().collect());
        let collected: Mutex<Vec<(usize, BatchTaskResult)>> = Mutex::new(Vec::with_capacity(total));
        let aborted = AtomicBool::new(false);
        let workers = parsed.parallel.min(total);

        thread::scope(|s| {
            for _ in 0..workers {
                let q_ref = &queue;
                let c_ref = &collected;
                let a_ref = &aborted;
                let p_ref = &parsed;
                s.spawn(move || {
                    while !a_ref.load(Ordering::Relaxed) {
                        let next = {
                            let mut g = q_ref.lock().unwrap();
                            g.pop_front()
                        };
                        let Some((idx, exe)) = next else {
                            break;
                        };
                        match run_single_batch_item(cfg, &exe, idx, total, p_ref) {
                            Ok(Some(res)) => {
                                let mut cg = c_ref.lock().unwrap();
                                cg.push((idx, res));
                            }
                            Ok(None) => {}
                            Err(_) => {
                                a_ref.store(true, Ordering::Relaxed);
                                break;
                            }
                        }
                    }
                });
            }
        });

        if aborted.load(Ordering::Relaxed) {
            return 2;
        }
        let mut pairs = collected.into_inner().unwrap();
        pairs.sort_by_key(|(idx, _)| *idx);
        pairs.into_iter().map(|(_, r)| r).collect()
    };

    // Record batch projects, sync matrix, and write structured Rust batch report
    let mut gargs: Vec<String> = vec!["-m".into(), "tools.gamesdb".into(), "--matrix".into()];
    for r in &results {
        if r.project_dir.join("reports/report.json").is_file() {
            gargs.push("--record".into());
            gargs.push(r.project_dir.to_string_lossy().into());
        }
    }
    let _ = Command::new(crate::config::python_interpreter(&cfg.repo_root))
        .current_dir(&cfg.repo_root)
        .env("PYTHONPATH", &cfg.repo_root)
        .args(&gargs)
        .status();

    let json_rows: Vec<JsonValue> = results
        .iter()
        .map(|r| {
            JsonValue::Object(vec![
                ("exe".into(), JsonValue::Str(r.exe_name.clone())),
                (
                    "project_dir".into(),
                    JsonValue::Str(r.project_dir.to_string_lossy().into()),
                ),
                ("exit_code".into(), JsonValue::Number(r.exit_code as f64)),
                ("build_passed".into(), JsonValue::Bool(r.build_passed)),
                ("boot_passed".into(), JsonValue::Bool(r.boot_passed)),
                (
                    "strict_boot_passed".into(),
                    JsonValue::Bool(r.strict_boot_passed),
                ),
                (
                    "unresolved_imports".into(),
                    JsonValue::Number(r.unresolved_imports as f64),
                ),
                (
                    "unhandled_eips".into(),
                    JsonValue::Number(r.unhandled_eips as f64),
                ),
                ("standalone_ok".into(), JsonValue::Bool(r.standalone_ok)),
                ("elapsed_secs".into(), JsonValue::Number(r.elapsed_secs)),
            ])
        })
        .collect();
    let report_obj = JsonValue::Object(vec![
        (
            "timestamp".into(),
            JsonValue::Number(unix_timestamp_secs() as f64),
        ),
        ("total".into(), JsonValue::Number(results.len() as f64)),
        ("results".into(), JsonValue::Array(json_rows)),
    ]);
    let report_dir = cfg.repo_root.join("reports");
    let _ = fs::create_dir_all(&report_dir);
    let _ = fs::write(
        report_dir.join("batch_last.json"),
        report_obj.to_json_string(),
    );

    let failed = results
        .iter()
        .filter(|r| {
            !(r.exit_code == EXIT_OK || r.exit_code == EXIT_UNVERIFIED)
                || !r.build_passed
                || !r.strict_boot_passed
                || r.unresolved_imports > 0
                || r.unhandled_eips > 0
                || r.crash_signal.is_some()
                || !r.standalone_ok
        })
        .collect::<Vec<_>>();

    println!(
        "\nbatch summary: {}/{} passed ({} failed)",
        results.len() - failed.len(),
        results.len(),
        failed.len()
    );
    for f in &failed {
        println!(
            "  FAILED: {} -> build={} boot={} strict={} unresolved={} eip={} crash={:?} standalone={} ({})",
            f.exe_name,
            f.build_passed,
            f.boot_passed,
            f.strict_boot_passed,
            f.unresolved_imports,
            f.unhandled_eips,
            f.crash_signal,
            f.standalone_ok,
            f.standalone_note
        );
    }

    if failed.is_empty() {
        0
    } else {
        2
    }
}

pub fn run_serve_command(cfg: &AppConfig, args: &[String]) -> i32 {
    if args.is_empty() || args.iter().any(|a| a == "-h" || a == "--help") {
        println!(
            "Usage: GameReComp serve <project_dir_or_build_dir> [--port 8080] [--ready-file PATH]"
        );
        return if args.is_empty() { 2 } else { 0 };
    }
    let mut dir: Option<PathBuf> = None;
    let mut ready_file: Option<PathBuf> = None;
    let mut port = "8080".to_string();
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--port" || args[i] == "-p" {
            if let Some(v) = args.get(i + 1) {
                port = v.clone();
                i += 2;
            } else {
                eprintln!("error: --port requires a value");
                return 2;
            }
        } else if args[i] == "--ready-file" {
            if let Some(value) = args.get(i + 1).filter(|value| !value.starts_with('-')) {
                ready_file = Some(PathBuf::from(value));
                i += 2;
            } else {
                eprintln!("error: --ready-file requires a path");
                return 2;
            }
        } else if dir.is_none() {
            dir = Some(PathBuf::from(&args[i]));
            i += 1;
        } else {
            eprintln!("error: unexpected argument: {}", args[i]);
            return 2;
        }
    }
    let Some(base) = dir else {
        eprintln!("error: project directory is required");
        return 2;
    };
    let serve_dir = if base.join("index.html").is_file() {
        base
    } else if base.join("build").join("index.html").is_file() {
        base.join("build")
    } else {
        eprintln!("error: no build/index.html found in {} (build with --target-os web --target-arch wasm32 first)", base.display());
        return 2;
    };
    println!(
        "Serving WebAssembly app at http://127.0.0.1:{port}/index.html (dir: {})",
        serve_dir.display()
    );
    println!("Press Ctrl+C to stop.");
    let py = concat!(
        "import http.server, socketserver, functools, sys, os, signal, errno, stat, secrets, ctypes\n",
        "class CoopHandler(http.server.SimpleHTTPRequestHandler):\n",
        "    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, '.wasm': 'application/wasm'}\n",
        "    def end_headers(self):\n",
        "        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')\n",
        "        self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')\n",
        "        self.send_header('Cache-Control', 'no-store')\n",
        "        super().end_headers()\n",
        "handler = functools.partial(CoopHandler, directory=sys.argv[1])\n",
        "socketserver.TCPServer.allow_reuse_address = True\n",
        "def stop_server(_signum, _frame): raise SystemExit(0)\n",
        "def process_alive(pid):\n",
        "    if os.name != 'nt':\n",
        "        try: os.kill(pid, 0)\n",
        "        except OSError as error: return error.errno != errno.ESRCH\n",
        "        return True\n",
        "    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)\n",
        "    kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)\n",
        "    kernel32.OpenProcess.restype = ctypes.c_void_p\n",
        "    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)\n",
        "    handle = kernel32.OpenProcess(0x1000, False, pid)\n",
        "    if not handle: return ctypes.get_last_error() == 5\n",
        "    kernel32.CloseHandle(handle)\n",
        "    return True\n",
        "signal.signal(signal.SIGTERM, stop_server)\n",
        "def read_marker(path):\n",
        "    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))\n",
        "    try:\n",
        "        info = os.fstat(fd)\n",
        "        if not stat.S_ISREG(info.st_mode): return None\n",
        "        return info, os.read(fd, 256).decode('ascii', 'ignore').strip()\n",
        "    finally: os.close(fd)\n",
        "def remove_stale_marker(path):\n",
        "    try: info, marker = read_marker(path)\n",
        "    except FileNotFoundError: return\n",
        "    if not info or not marker.startswith('GameReComp-ready-v1 '): raise FileExistsError(path)\n",
        "    try: owner = int(marker.split()[1])\n",
        "    except (IndexError, ValueError): raise FileExistsError(path)\n",
        "    if process_alive(owner): raise FileExistsError(path)\n",
        "    current = os.stat(path, follow_symlinks=False)\n",
        "    if (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino): os.unlink(path)\n",
        "if len(sys.argv) > 3: remove_stale_marker(sys.argv[3])\n",
        "marker_owner = None\n",
        "with socketserver.TCPServer(('127.0.0.1', int(sys.argv[2])), handler) as httpd:\n",
        "    marker_temp = None\n",
        "    try:\n",
        "        if len(sys.argv) > 3:\n",
        "            marker_owner = f'GameReComp-ready-v1 {os.getpid()} {secrets.token_hex(16)}'\n",
        "            marker_temp = sys.argv[3] + '.' + secrets.token_hex(16) + '.tmp'\n",
        "            fd = os.open(marker_temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)\n",
        "            with os.fdopen(fd, 'w', encoding='ascii') as marker:\n",
        "                marker.write(marker_owner); marker.flush(); os.fsync(marker.fileno())\n",
        "            os.link(marker_temp, sys.argv[3])\n",
        "            os.unlink(marker_temp); marker_temp = None\n",
        "        httpd.serve_forever()\n",
        "    finally:\n",
        "        if marker_temp:\n",
        "            try: os.unlink(marker_temp)\n",
        "            except FileNotFoundError: pass\n",
        "        if len(sys.argv) > 3 and marker_owner:\n",
        "            try: info, marker = read_marker(sys.argv[3])\n",
        "            except FileNotFoundError: info, marker = None, None\n",
        "            if info and marker == marker_owner:\n",
        "                try: current = os.stat(sys.argv[3], follow_symlinks=False)\n",
        "                except FileNotFoundError: pass\n",
        "                else:\n",
        "                    if (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino): os.unlink(sys.argv[3])\n",
    );
    let mut command = Command::new(crate::config::python_interpreter(&cfg.repo_root));
    command.arg("-c").arg(py).arg(&serve_dir).arg(&port);
    if let Some(path) = ready_file.as_ref() {
        command.arg(path);
    }
    match command
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()
    {
        Ok(st) => st.code().unwrap_or(0),
        Err(e) => {
            eprintln!("error: failed to start HTTP server: {e}");
            1
        }
    }
}

pub fn run_tasks_command(cfg: &AppConfig, args: &[String]) -> i32 {
    let mut projects_root = cfg.repo_root.join("projects");
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--project-root" && i + 1 < args.len() {
            projects_root = PathBuf::from(&args[i + 1]);
            i += 2;
        } else {
            i += 1;
        }
    }

    let Ok(rd) = fs::read_dir(&projects_root) else {
        println!("no projects directory at {}", projects_root.display());
        return 0;
    };

    let mut dirs: Vec<PathBuf> = rd
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    dirs.sort();

    println!(
        "{:<24} {:>5}  {:<12} {:<5} {:<6} {:<6} {}",
        "Project", "Score", "Outcome", "Build", "Strict", "Bin", "Source PE"
    );
    println!("{}", "-".repeat(90));
    for d in dirs {
        let name = d.file_name().and_then(|s| s.to_str()).unwrap_or("");
        match ProjectModel::load(&d) {
            Ok(m) => {
                let bin_ok = if m.built_binary_path().is_some() {
                    "yes"
                } else {
                    "no"
                };
                println!(
                    "{:<24} {:>5}  {:<12} {:<5} {:<6} {:<6} {}",
                    name,
                    m.score,
                    if m.verification.is_empty() {
                        "-"
                    } else {
                        &m.verification
                    },
                    if m.diag.build_passed { "ok" } else { "fail" },
                    if m.diag.strict_boot_passed {
                        "ok"
                    } else {
                        "fail"
                    },
                    bin_ok,
                    m.pe_path
                );
            }
            Err(_) => {
                println!(
                    "{:<24} {:>5}  {:<12} {:<5} {:<6} {:<6} -",
                    name, "-", "unbuilt", "-", "-", "-"
                );
            }
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn collects_and_sorts_exes_from_dir() {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("gamerecomp-tasks-{nanos}"));
        fs::create_dir_all(dir.join("nested")).unwrap();
        fs::write(dir.join("b.exe"), b"MZ").unwrap();
        fs::write(dir.join("a.EXE"), b"MZ").unwrap();
        fs::write(dir.join("nested").join("c.exe"), b"MZ").unwrap();
        fs::write(dir.join("notes.txt"), b"hi").unwrap();
        let found = collect_exes(&[dir.to_string_lossy().to_string()]);
        assert_eq!(found.len(), 3);
        assert!(found[0].ends_with("a.EXE"));
        assert!(found[1].ends_with("b.exe"));
        assert!(found[2].ends_with("c.exe"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn classifies_short_files_as_unsupported_not_unreadable() {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let file = std::env::temp_dir().join(format!("gamerecomp-short-{nanos}.exe"));
        fs::write(&file, b"MZ").unwrap();
        let info = inspect_pe(&file);
        assert!(!info.ok);
        assert_ne!(info.error, "cannot read the file");
        let _ = fs::remove_file(file);
    }

    #[cfg(unix)]
    #[test]
    fn deduplicates_file_aliases_and_stops_directory_loops() {
        use std::os::unix::fs::symlink;

        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("gamerecomp-task-links-{nanos}"));
        fs::create_dir_all(&dir).unwrap();
        let game = dir.join("game.exe");
        fs::write(&game, b"MZ").unwrap();
        symlink(&game, dir.join("game-alias.exe")).unwrap();
        symlink(&dir, dir.join("loop")).unwrap();

        let found = collect_exes(&[dir.to_string_lossy().to_string()]);
        assert_eq!(found, vec![dir.join("game-alias.exe")]);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn gives_same_stem_executables_distinct_batch_projects() {
        let root = Path::new("/tmp/gamerecomp-projects");
        assert_ne!(
            batch_project_dir(root, Path::new("/tmp/one/game.exe")),
            batch_project_dir(root, Path::new("/tmp/two/game.exe")),
        );
    }

    #[test]
    fn parses_parallel_batch_flags_and_clamps() {
        let args: Vec<String> = vec![
            "/tmp/games",
            "--parallel",
            "4",
            "--timeout",
            "20",
            "--verify-standalone",
            "--clean-objects",
        ]
        .into_iter()
        .map(String::from)
        .collect();
        let parsed = parse_batch_args(&args).unwrap();
        assert_eq!(parsed.parallel, 4);
        assert_eq!(parsed.timeout, "20");
        assert!(parsed.verify_standalone);
        assert!(parsed.clean_objects);

        let clamped: Vec<String> = vec!["/tmp/games", "-p", "99"]
            .into_iter()
            .map(String::from)
            .collect();
        assert_eq!(parse_batch_args(&clamped).unwrap().parallel, 16);

        let zero_clamped: Vec<String> = vec!["/tmp/games", "--parallel", "0"]
            .into_iter()
            .map(String::from)
            .collect();
        assert_eq!(parse_batch_args(&zero_clamped).unwrap().parallel, 1);
    }
}
