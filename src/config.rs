use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone)]
pub struct AppConfig {
    pub repo_root: PathBuf,
    pub corpus_env: BTreeMap<String, String>,
    pub reveng_ai_cmd: Option<String>,
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
    #[serde(default)]
    pub projects_root: String,
    #[serde(default)]
    pub custom_projects: Vec<String>,
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
    2
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
            projects_root: String::new(),
            custom_projects: Vec::new(),
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
        let reveng_ai_cmd = env::var("REVENG_AI_CMD")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .or_else(|| corpus_env.get("REVENG_AI_CMD").cloned())
            .or_else(|| {
                if user_settings.reveng_ai_cmd.trim().is_empty() {
                    None
                } else {
                    Some(user_settings.reveng_ai_cmd.clone())
                }
            });

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
            screenshot_dir,
            assets_dir,
            default_timeout_secs,
        }
    }
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

pub fn resolve_repo_root(explicit_repo: Option<&Path>) -> PathBuf {
    if let Some(p) = explicit_repo {
        if !p.as_os_str().is_empty() {
            return p.to_path_buf();
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
            || dir.join("reports").join("certified").join("index.json").is_file()
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

fn probe_version(bin: &str, args: &[&str]) -> String {
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

#[derive(Debug, Clone)]
pub struct ToolchainStatus {
    pub present: Vec<String>,
    pub missing: Vec<String>,
    pub sdl2_present: bool,
    pub cards: Vec<ToolCardInfo>,
    pub agents: Vec<DetectedAgentInfo>,
}

impl ToolchainStatus {
    pub fn detect() -> Self {
        let mut present = Vec::new();
        let mut missing = Vec::new();
        for name in ["python3", "cmake", "cc", "pkg-config"] {
            if find_executable(name).is_some() {
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

        let cards = detect_tool_cards(sdl2_present);
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
        let sdl = if self.sdl2_present { "present" } else { "MISSING" };
        format!("toolchain:    {present_str}{missing_str} | sdl2: {sdl}")
    }

    pub fn is_ready(&self) -> bool {
        self.missing.iter().all(|m| m == "pkg-config") && self.sdl2_present
    }
}

fn detect_tool_cards(sdl2_present: bool) -> Vec<ToolCardInfo> {
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
        let found = find_executable(bin);
        let version = if found.is_some() {
            probe_version(bin, ver_args)
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
            "Anthropic CLI agent for provenance-gated high-level C recovery.",
        ),
        (
            "codex",
            "Codex CLI",
            "codex",
            "codex exec -",
            "OpenAI Codex CLI helper for C readability and recovery passes.",
        ),
        (
            "agy",
            "Antigravity CLI",
            "agy",
            "agy -p",
            "Google Antigravity CLI helper agent.",
        ),
        (
            "opencode",
            "opencode",
            "opencode",
            "opencode run",
            "Multi-provider terminal agent (DeepSeek, Qwen, Kimi, Anthropic, OpenAI).",
        ),
        (
            "cursor",
            "Cursor Agent",
            "cursor-agent",
            "cursor-agent -p",
            "Headless Cursor Agent CLI.",
        ),
        (
            "ollama",
            "Ollama (Local)",
            "ollama",
            "ollama run qwen2.5-coder:14b",
            "Run local coding models completely offline with zero data leaving your machine.",
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
    fn resolves_repo_root_from_cwd() {
        let cfg = AppConfig::load(None);
        assert!(cfg.repo_root.join("tools").join("autorecomp").is_dir());
    }
}
