use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Debug, Clone)]
pub struct AppConfig {
    pub repo_root: PathBuf,
    pub corpus_env: BTreeMap<String, String>,
    pub reveng_ai_cmd: Option<String>,
    pub screenshot_dir: Option<PathBuf>,
    pub assets_dir: Option<PathBuf>,
    pub default_timeout_secs: f64,
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

        let reveng_ai_cmd = env::var("REVENG_AI_CMD")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .or_else(|| corpus_env.get("REVENG_AI_CMD").cloned());

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

#[derive(Debug, Clone)]
pub struct ToolchainStatus {
    pub present: Vec<String>,
    pub missing: Vec<String>,
    pub sdl2_present: bool,
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
        Self {
            present,
            missing,
            sdl2_present,
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
        self.missing
            .iter()
            .all(|m| m == "pkg-config")
            && self.sdl2_present
    }
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
