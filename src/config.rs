use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

pub fn find_python_interpreter(repo_root: &Path) -> Option<PathBuf> {
    let repo_root = if repo_root.is_absolute() {
        repo_root.to_path_buf()
    } else {
        env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(repo_root)
    };
    let python_rel = if cfg!(windows) {
        PathBuf::from("Scripts").join("python.exe")
    } else {
        PathBuf::from("bin").join("python")
    };
    if let Some(active) = env::var_os("VIRTUAL_ENV") {
        let active_python = PathBuf::from(active).join(&python_rel);
        if is_executable_file(&active_python) {
            return Some(absolute_path(active_python));
        }
    }
    let venv_python = if cfg!(windows) {
        repo_root.join(".venv").join("Scripts").join("python.exe")
    } else {
        repo_root.join(".venv").join("bin").join("python")
    };
    if is_executable_file(&venv_python) {
        return Some(absolute_path(venv_python));
    }
    let names: &[&str] = if cfg!(windows) {
        &["python", "python3"]
    } else {
        &["python3", "python"]
    };
    names.iter().find_map(|name| find_executable(name))
}

pub fn python_interpreter(repo_root: &Path) -> PathBuf {
    find_python_interpreter(repo_root)
        .unwrap_or_else(|| PathBuf::from(if cfg!(windows) { "python" } else { "python3" }))
}

fn absolute_path(path: PathBuf) -> PathBuf {
    if path.is_absolute() {
        path
    } else {
        env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(path)
    }
}

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone)]
pub struct AppConfig {
    pub repo_root: PathBuf,
    pub corpus_env: BTreeMap<String, String>,
    pub reveng_ai_cmd: Option<String>,
    pub reveng_web_ai_cmd: Option<String>,
    pub screenshot_dir: Option<PathBuf>,
    pub assets_dir: Option<PathBuf>,
    pub default_timeout_secs: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserSettings {
    #[serde(default = "default_theme")]
    pub theme: String,
    #[serde(default = "default_ui_font")]
    pub ui_font: String,
    #[serde(default = "default_mono_font")]
    pub mono_font: String,
    #[serde(default = "default_font_scale")]
    pub font_scale: f32,
    #[serde(default = "default_font_scale")]
    pub code_font_scale: f32,
    #[serde(default)]
    pub reveng_ai_cmd: String,
    /// Path to the rev.ng binary (`REVNG_BIN` for the readability harness).
    #[serde(default)]
    pub revng_bin: String,
    /// rev.ng output directory (`REVNG_DIR` for tools that consume it).
    #[serde(default)]
    pub revng_dir: String,
    /// Base URL for the user-configured web AI provider (explicit opt-in).
    #[serde(default)]
    pub ai_web_endpoint: String,
    /// Model name passed to the web AI command template.
    #[serde(default)]
    pub ai_web_model: String,
    /// Command template for the web AI provider (`{endpoint}`/`{model}` are expanded).
    #[serde(default)]
    pub ai_web_cmd: String,
    #[serde(default)]
    pub projects_root: String,
    #[serde(default)]
    pub custom_projects: Vec<String>,
    #[serde(default)]
    pub hidden_projects: Vec<String>,
    #[serde(default = "default_timeout_u64")]
    pub default_timeout_secs: u64,
    #[serde(default = "default_max_concurrent_jobs")]
    pub max_concurrent_jobs: usize,
    #[serde(default)]
    pub rom_library_dir: String,
}

fn default_theme() -> String {
    "dark".to_string()
}

fn default_ui_font() -> String {
    "archivo".to_string()
}

fn default_mono_font() -> String {
    "ibm-plex-mono".to_string()
}

fn default_font_scale() -> f32 {
    1.0
}

fn default_timeout_u64() -> u64 {
    12
}

fn default_max_concurrent_jobs() -> usize {
    0
}

pub fn default_user_projects_dir() -> PathBuf {
    env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .map(|h| h.join("GameReComp").join("projects"))
        .unwrap_or_else(|| PathBuf::from("projects"))
}

#[derive(Debug, Clone)]
pub struct SystemResources {
    pub cpu_cores: usize,
    pub total_ram_mb: u64,
    pub recommended_parallel_games: usize,
}

impl Default for SystemResources {
    fn default() -> Self {
        Self::detect()
    }
}

impl SystemResources {
    pub fn detect() -> Self {
        let cpu_cores = std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4);
        let total_ram_mb = detect_total_ram_mb().unwrap_or(8192);
        let recommended_parallel_games = Self::compute_recommended(cpu_cores, total_ram_mb);
        Self {
            cpu_cores,
            total_ram_mb,
            recommended_parallel_games,
        }
    }

    pub fn compute_recommended(cpu_cores: usize, total_ram_mb: u64) -> usize {
        let by_ram = ((total_ram_mb / 2048).max(1)) as usize;
        let by_cpu = (cpu_cores / 2).max(1);
        by_ram.min(by_cpu).clamp(1, 8)
    }

    pub fn summary(&self) -> String {
        format!(
            "Auto: {} parallel ({:.0} GB RAM · {} cores)",
            self.recommended_parallel_games,
            (self.total_ram_mb as f64) / 1024.0,
            self.cpu_cores
        )
    }
}

fn detect_total_ram_mb() -> Option<u64> {
    #[cfg(target_os = "macos")]
    {
        let out = Command::new("sysctl")
            .args(["-n", "hw.memsize"])
            .output()
            .ok()?;
        let s = String::from_utf8_lossy(&out.stdout);
        let bytes = s.trim().parse::<u64>().ok()?;
        return Some((bytes / (1024 * 1024)).max(512));
    }
    #[cfg(target_os = "linux")]
    {
        let meminfo = fs::read_to_string("/proc/meminfo").ok()?;
        for line in meminfo.lines() {
            if let Some(rest) = line.strip_prefix("MemTotal:") {
                let kb = rest.split_whitespace().next()?.parse::<u64>().ok()?;
                return Some((kb / 1024).max(512));
            }
        }
        return None;
    }
    #[cfg(target_os = "windows")]
    {
        #[repr(C)]
        struct MemoryStatusEx {
            length: u32,
            memory_load: u32,
            total_phys: u64,
            avail_phys: u64,
            total_page_file: u64,
            avail_page_file: u64,
            total_virtual: u64,
            avail_virtual: u64,
            avail_extended_virtual: u64,
        }
        #[link(name = "kernel32")]
        extern "system" {
            fn GlobalMemoryStatusEx(status: *mut MemoryStatusEx) -> i32;
        }
        let mut status = MemoryStatusEx {
            length: std::mem::size_of::<MemoryStatusEx>() as u32,
            memory_load: 0,
            total_phys: 0,
            avail_phys: 0,
            total_page_file: 0,
            avail_page_file: 0,
            total_virtual: 0,
            avail_virtual: 0,
            avail_extended_virtual: 0,
        };
        // SAFETY: Windows fills the documented MEMORYSTATUSEX layout after its size is set.
        let ok = unsafe { GlobalMemoryStatusEx(&mut status) };
        return (ok != 0).then_some((status.total_phys / (1024 * 1024)).max(512));
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
    {
        None
    }
}

impl Default for UserSettings {
    fn default() -> Self {
        Self {
            theme: default_theme(),
            ui_font: default_ui_font(),
            mono_font: default_mono_font(),
            font_scale: default_font_scale(),
            code_font_scale: default_font_scale(),
            reveng_ai_cmd: String::new(),
            revng_bin: String::new(),
            revng_dir: String::new(),
            ai_web_endpoint: String::new(),
            ai_web_model: String::new(),
            ai_web_cmd: String::new(),
            projects_root: String::new(),
            custom_projects: Vec::new(),
            hidden_projects: Vec::new(),
            default_timeout_secs: default_timeout_u64(),
            max_concurrent_jobs: default_max_concurrent_jobs(),
            rom_library_dir: String::new(),
        }
    }
}

impl UserSettings {
    pub fn settings_path() -> Option<PathBuf> {
        let home = env::var_os("HOME")
            .or_else(|| env::var_os("USERPROFILE"))
            .map(PathBuf::from)?;
        Some(home.join(".gamerecomp").join("settings.json"))
    }

    pub fn load() -> Self {
        let Some(path) = Self::settings_path() else {
            return Self::default();
        };
        let Ok(raw) = fs::read_to_string(path) else {
            return Self::default();
        };
        serde_json::from_str(&raw).unwrap_or_default()
    }

    pub fn save(&self) -> Result<(), String> {
        let Some(path) = Self::settings_path() else {
            return Err("cannot determine home directory".to_string());
        };
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let body = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        fs::write(&path, body).map_err(|e| e.to_string())
    }
}

impl AppConfig {
    pub fn load(explicit_repo: Option<&Path>) -> Self {
        let repo_root = resolve_repo_root(explicit_repo);
        let mut corpus_env = BTreeMap::new();
        let env_file = repo_root.join("config").join("corpus.env");
        if let Ok(text) = fs::read_to_string(&env_file) {
            for (k, v) in parse_env_file(&text) {
                corpus_env.insert(k, v);
            }
        }
        for key in ["SMALL_GAMES_PATH", "LARGE_GAMES_PATH"] {
            if let Ok(val) = env::var(key) {
                if !val.trim().is_empty() {
                    corpus_env.insert(key.to_string(), val);
                }
            }
        }

        let user_settings = UserSettings::load();
        let reveng_ai_cmd = configured_command(
            env::var("REVENG_AI_CMD").ok(),
            &corpus_env,
            "REVENG_AI_CMD",
            &user_settings.reveng_ai_cmd,
        );
        let reveng_web_ai_cmd = configured_command(
            env::var("REVENG_WEB_AI_CMD").ok(),
            &corpus_env,
            "REVENG_WEB_AI_CMD",
            &user_settings.ai_web_cmd,
        );

        let screenshot_dir = env::var("RECOMP_SCREENSHOT")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .map(PathBuf::from);
        let assets_dir = env::var("RECOMP_ASSETS_DIR")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .map(PathBuf::from);
        let default_timeout_secs = env::var("GAMERECOMP_TIMEOUT")
            .ok()
            .and_then(|v| v.parse::<f64>().ok())
            .filter(|t| *t > 0.0)
            .unwrap_or(30.0);

        Self {
            repo_root,
            corpus_env,
            reveng_ai_cmd,
            reveng_web_ai_cmd,
            screenshot_dir,
            assets_dir,
            default_timeout_secs,
        }
    }
}

/// Resolve a configured command with the same precedence for local and web AI:
/// process environment, then `config/corpus.env`, then saved user settings.
fn configured_command(
    env_value: Option<String>,
    corpus_env: &BTreeMap<String, String>,
    key: &str,
    setting: &str,
) -> Option<String> {
    env_value
        .filter(|s| !s.trim().is_empty())
        .or_else(|| {
            corpus_env
                .get(key)
                .filter(|s| !s.trim().is_empty())
                .cloned()
        })
        .or_else(|| {
            let trimmed = setting.trim();
            (!trimmed.is_empty()).then(|| trimmed.to_string())
        })
}

pub fn parse_env_file(text: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let line = line.strip_prefix("export ").unwrap_or(line).trim();
        if let Some((k, v)) = line.split_once('=') {
            let key = k.trim();
            let val = v.trim().trim_matches('"').trim_matches('\'');
            if !key.is_empty() {
                out.push((key.to_string(), val.to_string()));
            }
        }
    }
    out
}

/// Locate the Python pipeline bundled inside a macOS `.app` bundle.
///
/// A bundled GUI runs from `<App>.app/Contents/MacOS/game-recomp-gui`, where the
/// repo's Python sources are shipped at `<App>.app/Contents/Resources/repo`.
/// Without this, `resolve_repo_root` falls back to `/` and every recompile
/// fails with `ModuleNotFoundError: No module named 'tools'`.
fn bundled_repo_root(exe: &Path) -> Option<PathBuf> {
    let contents = exe.parent()?.parent()?;
    let candidate = contents.join("Resources").join("repo");
    if candidate.join("tools").join("autorecomp").is_dir() {
        return Some(candidate);
    }
    None
}

pub fn resolve_repo_root(explicit_repo: Option<&Path>) -> PathBuf {
    if let Some(p) = explicit_repo {
        if !p.as_os_str().is_empty() {
            return p.to_path_buf();
        }
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(found) = bundled_repo_root(&exe) {
            return found;
        }
    }
    if let Ok(cwd) = env::current_dir() {
        if let Some(found) = walk_up_for_repo(&cwd) {
            return found;
        }
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(parent) = exe.parent() {
            if let Some(found) = walk_up_for_repo(parent) {
                return found;
            }
        }
    }
    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn walk_up_for_repo(start: &Path) -> Option<PathBuf> {
    let mut cur = Some(start);
    while let Some(dir) = cur {
        if dir.join("tools").join("autorecomp").is_dir()
            || dir
                .join("reports")
                .join("certified")
                .join("index.json")
                .is_file()
        {
            return Some(dir.to_path_buf());
        }
        cur = dir.parent();
    }
    None
}

pub fn find_executable(name: &str) -> Option<PathBuf> {
    let path_var = env::var_os("PATH")?;
    for dir in env::split_paths(&path_var) {
        let dir = if dir.as_os_str().is_empty() {
            env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
        } else if dir.is_absolute() {
            dir
        } else {
            env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join(dir)
        };
        let candidate = dir.join(name);
        if is_executable_file(&candidate) {
            return Some(candidate);
        }
        #[cfg(windows)]
        {
            let exe_cand = dir.join(format!("{name}.exe"));
            if is_executable_file(&exe_cand) {
                return Some(exe_cand);
            }
        }
    }
    None
}

#[cfg(unix)]
fn is_executable_file(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    fs::metadata(path)
        .map(|m| m.is_file() && (m.permissions().mode() & 0o111) != 0)
        .unwrap_or(false)
}

#[cfg(not(unix))]
fn is_executable_file(path: &Path) -> bool {
    path.is_file()
}

fn probe_version(bin: impl AsRef<std::ffi::OsStr>, args: &[&str]) -> String {
    Command::new(bin)
        .args(args)
        .output()
        .ok()
        .and_then(|out| {
            let txt = if out.stdout.is_empty() {
                String::from_utf8_lossy(&out.stderr).to_string()
            } else {
                String::from_utf8_lossy(&out.stdout).to_string()
            };
            txt.lines().next().map(|l| l.trim().to_string())
        })
        .unwrap_or_default()
}

#[derive(Debug, Clone)]
pub struct ToolCardInfo {
    pub name: String,
    pub category: String,
    pub description: String,
    pub installed: bool,
    pub path: String,
    pub version: String,
    pub install_hint: String,
}

#[allow(dead_code)]
#[derive(Debug, Clone)]
pub struct DetectedAgentInfo {
    pub id: String,
    pub name: String,
    pub binary: String,
    pub installed: bool,
    pub path: String,
    pub preset_cmd: String,
    pub description: String,
}

#[derive(Debug, Clone, Default)]
pub struct ToolchainStatus {
    pub present: Vec<String>,
    pub missing: Vec<String>,
    pub sdl2_present: bool,
    pub cards: Vec<ToolCardInfo>,
    pub agents: Vec<DetectedAgentInfo>,
}

impl ToolchainStatus {
    pub fn detect() -> Self {
        Self::detect_for_repo(&env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
    }

    pub fn detect_for_repo(repo_root: &Path) -> Self {
        let mut present = Vec::new();
        let mut missing = Vec::new();
        for name in ["python3", "cmake", "cc", "pkg-config"] {
            if (name == "python3" && find_python_interpreter(repo_root).is_some())
                || (name != "python3" && find_executable(name).is_some())
            {
                present.push(name.to_string());
            } else {
                missing.push(name.to_string());
            }
        }
        let sdl2_present = find_executable("pkg-config").is_some()
            && Command::new("pkg-config")
                .args(["--exists", "sdl2"])
                .status()
                .map(|s| s.success())
                .unwrap_or(false);

        let cards = detect_tool_cards(sdl2_present, repo_root);
        let agents = detect_agents();

        Self {
            present,
            missing,
            sdl2_present,
            cards,
            agents,
        }
    }

    pub fn format_selftest_line(&self) -> String {
        let present_str = if self.present.is_empty() {
            "none".to_string()
        } else {
            self.present.join(", ")
        };
        let missing_str = if self.missing.is_empty() {
            String::new()
        } else {
            format!(" | missing: {}", self.missing.join(", "))
        };
        let sdl = if self.sdl2_present {
            "present"
        } else {
            "MISSING"
        };
        format!("toolchain:    {present_str}{missing_str} | sdl2: {sdl}")
    }

    pub fn is_ready(&self) -> bool {
        self.missing.iter().all(|m| m == "pkg-config") && self.sdl2_present
    }
}

fn detect_tool_cards(sdl2_present: bool, repo_root: &Path) -> Vec<ToolCardInfo> {
    let mut cards = Vec::new();
    let specs: &[(&str, &str, &str, &str, &[&str], &str)] = &[
        (
            "Python 3",
            "RE Backend",
            "Drives PE inspection, x86 disassembly, lifting, and verification harness.",
            "python3",
            &["--version"],
            "Install Python 3.10+ (brew install python / apt install python3)",
        ),
        (
            "C Compiler (cc / clang)",
            "Native Build",
            "Compiles lifted x86 C translation units and the Win32 HLE runtime.",
            "cc",
            &["--version"],
            "Install Xcode CLT (xcode-select --install) or clang/gcc",
        ),
        (
            "CMake",
            "Native Build",
            "Configures and builds generated standalone C projects.",
            "cmake",
            &["--version"],
            "brew install cmake / apt install cmake",
        ),
        (
            "pkg-config",
            "Native Build",
            "Discovers SDL2 include and library paths for recompiled native games.",
            "pkg-config",
            &["--version"],
            "brew install pkg-config / apt install pkg-config",
        ),
        (
            "Clang (WebAssembly)",
            "WebAssembly",
            "Compiles lifted C and wasm32 Win32 HLE runtime to standalone .wasm.",
            "clang",
            &["--version"],
            "brew install llvm / apt install clang lld",
        ),
        (
            "Node.js",
            "WebAssembly",
            "Headless WebAssembly verification runner (run_wasm_node.mjs).",
            "node",
            &["--version"],
            "brew install node / nodejs.org",
        ),
    ];

    for &(name, cat, desc, bin, ver_args, hint) in specs {
        let found = if bin == "python3" {
            find_python_interpreter(repo_root)
        } else {
            find_executable(bin)
        };
        let version = if found.is_some() {
            probe_version(found.as_ref().unwrap(), ver_args)
        } else {
            String::new()
        };
        cards.push(ToolCardInfo {
            name: name.to_string(),
            category: cat.to_string(),
            description: desc.to_string(),
            installed: found.is_some(),
            path: found
                .map(|p| p.display().to_string())
                .unwrap_or_else(|| "not found in PATH".to_string()),
            version,
            install_hint: hint.to_string(),
        });
    }

    let sdl_ver = if sdl2_present {
        probe_version("pkg-config", &["--modversion", "sdl2"])
    } else {
        String::new()
    };
    cards.push(ToolCardInfo {
        name: "SDL2".to_string(),
        category: "Game Runtime".to_string(),
        description: "Windowing, 2D/D3D framebuffer presentation, input, and audio for recompiled native games.".to_string(),
        installed: sdl2_present,
        path: if sdl2_present {
            "pkg-config: sdl2".to_string()
        } else {
            "not found via pkg-config".to_string()
        },
        version: sdl_ver,
        install_hint: "brew install sdl2 / apt install libsdl2-dev".to_string(),
    });

    cards
}

fn detect_agents() -> Vec<DetectedAgentInfo> {
    let specs: &[(&str, &str, &str, &str, &str)] = &[
        (
            "claude",
            "Claude Code",
            "claude",
            "claude -p",
            "Optional Anthropic CLI agent — strictly for improving readable C source code.",
        ),
        (
            "codex",
            "Codex CLI",
            "codex",
            "codex exec -",
            "Optional OpenAI Codex CLI — strictly for improving readable C source code.",
        ),
        (
            "agy",
            "Antigravity CLI",
            "agy",
            "agy -p",
            "Optional Antigravity CLI — strictly for improving readable C source code.",
        ),
        (
            "opencode",
            "opencode",
            "opencode",
            "opencode run",
            "Optional multi-provider terminal agent — strictly for improving readable C source code.",
        ),
        (
            "cursor",
            "Cursor Agent",
            "cursor-agent",
            "cursor-agent -p",
            "Optional headless Cursor Agent — strictly for improving readable C source code.",
        ),
        (
            "ollama",
            "Ollama (Local)",
            "ollama",
            "ollama run qwen2.5-coder:14b",
            "Optional local coding model — strictly for improving readable C source code offline.",
        ),
    ];

    specs
        .iter()
        .map(|&(id, name, bin, preset, desc)| {
            let found = find_executable(bin);
            DetectedAgentInfo {
                id: id.to_string(),
                name: name.to_string(),
                binary: bin.to_string(),
                installed: found.is_some(),
                path: found
                    .map(|p| p.display().to_string())
                    .unwrap_or_else(|| "not installed".to_string()),
                preset_cmd: preset.to_string(),
                description: desc.to_string(),
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_corpus_env_lines() {
        let raw = "# comment\nexport SMALL_GAMES_PATH=\"/tmp/games\"\nREVENG_AI_CMD='claude -p'\n";
        let pairs = parse_env_file(raw);
        assert_eq!(
            pairs,
            vec![
                ("SMALL_GAMES_PATH".to_string(), "/tmp/games".to_string()),
                ("REVENG_AI_CMD".to_string(), "claude -p".to_string()),
            ]
        );
    }

    #[test]
    fn finds_bundled_repo_root_in_macos_app() {
        let base = env::temp_dir().join(format!("gamerecomp-bundle-root-{}", std::process::id()));
        let exe = base.join("Contents").join("MacOS").join("game-recomp-gui");
        let repo = base.join("Contents").join("Resources").join("repo");
        let _ = fs::create_dir_all(repo.join("tools").join("autorecomp"));
        let found = bundled_repo_root(&exe);
        assert_eq!(found.as_deref(), Some(repo.as_path()));
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn resolves_repo_root_from_cwd() {
        let cfg = AppConfig::load(None);
        assert!(cfg.repo_root.join("tools").join("autorecomp").is_dir());
    }

    #[test]
    fn computes_recommended_parallel_jobs_from_resources() {
        assert_eq!(SystemResources::compute_recommended(8, 4096), 2);
        assert_eq!(SystemResources::compute_recommended(10, 16384), 5);
        assert_eq!(SystemResources::compute_recommended(32, 65536), 8);
        assert_eq!(SystemResources::compute_recommended(1, 1024), 1);
    }

    #[test]
    fn source_improve_settings_default_to_empty_and_preserve_other_defaults() {
        let settings = UserSettings::default();
        assert!(settings.revng_bin.is_empty());
        assert!(settings.revng_dir.is_empty());
        assert!(settings.ai_web_endpoint.is_empty());
        assert!(settings.ai_web_model.is_empty());
        assert!(settings.ai_web_cmd.is_empty());
        assert_eq!(settings.theme, "dark");
        assert_eq!(settings.default_timeout_secs, 12);
    }

    #[test]
    fn parses_source_improve_settings_from_json() {
        let settings: UserSettings = serde_json::from_str(
            r#"{
                "revng_bin": "/opt/revng/bin/revng",
                "revng_dir": "/tmp/revng-out",
                "ai_web_endpoint": "https://ai.example.invalid/v1",
                "ai_web_model": "coder-web",
                "ai_web_cmd": "web-ai --url {endpoint} --model {model}"
            }"#,
        )
        .unwrap();
        assert_eq!(settings.revng_bin, "/opt/revng/bin/revng");
        assert_eq!(settings.revng_dir, "/tmp/revng-out");
        assert_eq!(settings.ai_web_endpoint, "https://ai.example.invalid/v1");
        assert_eq!(settings.ai_web_model, "coder-web");
        assert_eq!(
            settings.ai_web_cmd,
            "web-ai --url {endpoint} --model {model}"
        );
    }

    #[test]
    fn missing_source_improve_settings_serde_default_to_empty() {
        let settings: UserSettings = serde_json::from_str(r#"{"theme": "light"}"#).unwrap();
        assert_eq!(settings.theme, "light");
        assert!(settings.revng_bin.is_empty());
        assert!(settings.revng_dir.is_empty());
        assert!(settings.ai_web_endpoint.is_empty());
        assert!(settings.ai_web_model.is_empty());
        assert!(settings.ai_web_cmd.is_empty());
    }

    #[test]
    fn configured_command_prefers_env_then_corpus_then_settings() {
        let mut corpus_env = BTreeMap::new();
        corpus_env.insert(
            "REVENG_WEB_AI_CMD".to_string(),
            "corpus-web --model {model}".to_string(),
        );
        assert_eq!(
            configured_command(
                Some("env-web --model {model}".to_string()),
                &corpus_env,
                "REVENG_WEB_AI_CMD",
                "settings-web"
            ),
            Some("env-web --model {model}".to_string())
        );
        assert_eq!(
            configured_command(None, &corpus_env, "REVENG_WEB_AI_CMD", "settings-web"),
            Some("corpus-web --model {model}".to_string())
        );
        assert_eq!(
            configured_command(
                None,
                &BTreeMap::new(),
                "REVENG_WEB_AI_CMD",
                "  settings-web  "
            ),
            Some("settings-web".to_string())
        );
        assert_eq!(
            configured_command(None, &BTreeMap::new(), "REVENG_WEB_AI_CMD", "   "),
            None
        );
    }

    #[test]
    fn parses_web_ai_command_from_corpus_env_file() {
        let pairs =
            parse_env_file("export REVENG_WEB_AI_CMD='web-ai --url {endpoint} --model {model}'\n");
        assert_eq!(
            pairs,
            vec![(
                "REVENG_WEB_AI_CMD".to_string(),
                "web-ai --url {endpoint} --model {model}".to_string()
            )]
        );
    }
}
