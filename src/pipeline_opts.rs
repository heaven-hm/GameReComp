use serde::{Deserialize, Serialize};

pub const ALL_STAGES: [&str; 8] = [
    "inspect",
    "recover",
    "translate",
    "project",
    "compile",
    "run",
    "verify",
    "report",
];

pub const STAGE_DESCRIPTIONS: [(&str, &str); 8] = [
    ("inspect", "Validate PE32 header, imports, entry point & SHA-256"),
    ("recover", "Disassemble x86 code sections & recover function boundaries"),
    ("translate", "Lift x86 instructions into portable C translation units"),
    ("project", "Generate CMakeLists.txt, Win32 HLE runtime & project.json"),
    ("compile", "Compile native host binary or WebAssembly wasm32 bundle"),
    ("run", "Execute rebuilt binary under timeout & collect traces/frames"),
    ("verify", "Evaluate oracle assertions, boot health & verification score"),
    ("report", "Emit report.json, report.html, functions.json & manifest.json"),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AppProfile {
    Game,
    DesktopApp,
    ConsoleApp,
}

impl AppProfile {
    pub fn label(self) -> &'static str {
        match self {
            Self::Game => "Game (SDL2 / Graphics)",
            Self::DesktopApp => "Desktop App (Win32 GUI)",
            Self::ConsoleApp => "Console App (Headless)",
        }
    }

    pub fn short(self) -> &'static str {
        match self {
            Self::Game => "Game",
            Self::DesktopApp => "Desktop App",
            Self::ConsoleApp => "Console App",
        }
    }

    pub fn description(self) -> &'static str {
        match self {
            Self::Game => "Optimized for 2D/3D Win32 games with framebuffer capture, audio & input.",
            Self::DesktopApp => "Standard Windows GUI applications with GDI/User32 windowing.",
            Self::ConsoleApp => "Headless console utilities verified via stdout/stderr & exit code.",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OutMode {
    Full,
    BinaryOnly,
    SourceOnly,
    CustomStages,
}

impl OutMode {
    pub fn label(self) -> &'static str {
        match self {
            Self::Full => "Full (source + binary + run + verify)",
            Self::BinaryOnly => "Binary only (build, skip run)",
            Self::SourceOnly => "Source only (C project, no compile)",
            Self::CustomStages => "Custom stages",
        }
    }

    pub fn short(self) -> &'static str {
        match self {
            Self::Full => "Full",
            Self::BinaryOnly => "Binary",
            Self::SourceOnly => "Source",
            Self::CustomStages => "Custom",
        }
    }

    pub fn cli_value(self) -> Option<&'static str> {
        match self {
            Self::Full => Some("full"),
            Self::BinaryOnly => Some("binary-only"),
            Self::SourceOnly => Some("source-only"),
            Self::CustomStages => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PipelineOptions {
    pub pe_file: String,
    pub project_dir: String,
    pub assets_dir: String,
    pub highlevel_dir: String,
    pub oracle_path: String,
    pub app_profile: AppProfile,

    pub input_os: String,
    pub input_arch: String,
    pub target_os: String,
    pub target_arch: String,
    pub out_mode: OutMode,
    pub selected_stages: [bool; 8],

    pub timeout_secs: String,
    pub build_timeout_secs: String,
    pub jobs: String,
    pub split: String,
    pub split_bytes: String,

    pub enable_logs: bool,
    pub quiet: bool,
    pub enable_progress: bool,
    pub json_output: bool,
    pub update_table: bool,

    pub interactive: bool,
    pub reuse_build: bool,
    pub fresh_build: bool,
    pub import_trace: bool,
    pub no_build: bool,
    pub no_run: bool,
    pub dry_run: bool,
    pub clean_objects: bool,
    pub verify_standalone: bool,
    pub capture_screenshots: bool,
}

impl Default for PipelineOptions {
    fn default() -> Self {
        Self {
            pe_file: String::new(),
            project_dir: String::new(),
            assets_dir: String::new(),
            highlevel_dir: String::new(),
            oracle_path: String::new(),
            app_profile: AppProfile::Game,
            input_os: "auto".to_string(),
            input_arch: "auto".to_string(),
            target_os: "host".to_string(),
            target_arch: "host".to_string(),
            out_mode: OutMode::Full,
            selected_stages: [true; 8],
            timeout_secs: "12".to_string(),
            build_timeout_secs: String::new(),
            jobs: String::new(),
            split: "1000".to_string(),
            split_bytes: String::new(),
            enable_logs: true,
            quiet: false,
            enable_progress: true,
            json_output: false,
            update_table: false,
            interactive: false,
            reuse_build: false,
            fresh_build: false,
            import_trace: false,
            no_build: false,
            no_run: false,
            dry_run: false,
            clean_objects: false,
            verify_standalone: true,
            capture_screenshots: false,
        }
    }
}

impl PipelineOptions {
    pub fn apply_profile(&mut self, profile: AppProfile) {
        self.app_profile = profile;
        match profile {
            AppProfile::Game => {
                self.capture_screenshots = true;
                self.timeout_secs = "12".to_string();
                self.verify_standalone = true;
            }
            AppProfile::DesktopApp => {
                self.capture_screenshots = true;
                self.timeout_secs = "10".to_string();
                self.verify_standalone = true;
            }
            AppProfile::ConsoleApp => {
                self.capture_screenshots = false;
                self.interactive = false;
                self.timeout_secs = "15".to_string();
                self.verify_standalone = true;
            }
        }
    }

    pub fn is_wasm_target(&self) -> bool {
        self.target_os == "web"
            || self.target_os == "wasm"
            || self.target_arch == "wasm32"
            || self.target_arch == "wasm"
    }

    pub fn set_target_native_host(&mut self) {
        self.target_os = "host".to_string();
        self.target_arch = "host".to_string();
    }

    pub fn set_target_wasm32(&mut self) {
        self.target_os = "web".to_string();
        self.target_arch = "wasm32".to_string();
    }

    pub fn effective_stages(&self) -> Vec<&'static str> {
        match self.out_mode {
            OutMode::Full => ALL_STAGES.to_vec(),
            OutMode::BinaryOnly => vec![
                "inspect",
                "recover",
                "translate",
                "project",
                "compile",
                "report",
            ],
            OutMode::SourceOnly => vec!["inspect", "recover", "translate", "project", "report"],
            OutMode::CustomStages => {
                let mut v: Vec<&'static str> = ALL_STAGES
                    .iter()
                    .enumerate()
                    .filter(|(i, _)| self.selected_stages[*i])
                    .map(|(_, s)| *s)
                    .collect();
                if !v.contains(&"report") {
                    v.push("report");
                }
                v
            }
        }
    }

    /// Build exact CLI arguments for `tools.autorecomp.cli` (and `./GameReComp`).
    pub fn to_backend_args(&self) -> Vec<String> {
        let mut args = Vec::new();
        if !self.pe_file.trim().is_empty() {
            args.push(self.pe_file.trim().to_string());
        }
        if !self.project_dir.trim().is_empty() {
            args.push("--project-dir".to_string());
            args.push(self.project_dir.trim().to_string());
        }
        if !self.assets_dir.trim().is_empty() {
            args.push("--assets-dir".to_string());
            args.push(self.assets_dir.trim().to_string());
        }
        if !self.highlevel_dir.trim().is_empty() {
            args.push("--highlevel".to_string());
            args.push(self.highlevel_dir.trim().to_string());
        }
        if !self.oracle_path.trim().is_empty() {
            args.push("--oracle".to_string());
            args.push(self.oracle_path.trim().to_string());
        }

        // Input / Output identity
        args.push("--input-os".to_string());
        args.push(self.input_os.clone());
        args.push("--input-arch".to_string());
        args.push(self.input_arch.clone());
        args.push("--target-os".to_string());
        args.push(self.target_os.clone());
        args.push("--target-arch".to_string());
        args.push(self.target_arch.clone());

        // Output mode or explicit stage list
        match self.out_mode {
            OutMode::CustomStages => {
                let list = self.effective_stages().join(",");
                args.push("--stages".to_string());
                args.push(list);
            }
            mode => {
                if let Some(val) = mode.cli_value() {
                    args.push("--out-mode".to_string());
                    args.push(val.to_string());
                }
            }
        }

        if !self.timeout_secs.trim().is_empty() {
            args.push("--timeout".to_string());
            args.push(self.timeout_secs.trim().to_string());
        }
        if !self.build_timeout_secs.trim().is_empty() {
            args.push("--build-timeout".to_string());
            args.push(self.build_timeout_secs.trim().to_string());
        }
        if !self.jobs.trim().is_empty() {
            args.push("--jobs".to_string());
            args.push(self.jobs.trim().to_string());
        }
        if !self.split.trim().is_empty() && self.split.trim() != "1000" {
            args.push("--split".to_string());
            args.push(self.split.trim().to_string());
        }
        if !self.split_bytes.trim().is_empty() {
            args.push("--split-bytes".to_string());
            args.push(self.split_bytes.trim().to_string());
        }

        if self.interactive {
            args.push("--interactive".to_string());
        }
        if self.reuse_build {
            args.push("--reuse-build".to_string());
        }
        if self.fresh_build {
            args.push("--fresh-build".to_string());
        }
        if self.import_trace {
            args.push("--import-trace".to_string());
        }
        if self.no_build {
            args.push("--no-build".to_string());
        }
        if self.no_run {
            args.push("--no-run".to_string());
        }
        if self.dry_run {
            args.push("--dry-run".to_string());
        }
        if !self.update_table {
            args.push("--no-table".to_string());
        }
        if !self.enable_progress {
            args.push("--no-progress".to_string());
        }
        if self.enable_logs && !self.quiet {
            args.push("--verbose".to_string());
        }
        if self.quiet {
            args.push("--quiet".to_string());
        }
        if self.json_output {
            args.push("--json".to_string());
        }

        args
    }

    /// Full command preview including Rust-orchestrator flags (--clean-objects, --verify-standalone).
    pub fn format_command_preview(&self) -> String {
        let mut parts = Vec::new();
        if self.capture_screenshots {
            parts.push("RECOMP_SCREENSHOT=<proj>/screenshots".to_string());
        }
        parts.push("game-recomp-cli".to_string());
        for a in self.to_backend_args() {
            if a.contains(' ') {
                parts.push(format!("\"{a}\""));
            } else {
                parts.push(a);
            }
        }
        if self.clean_objects {
            parts.push("--clean-objects".to_string());
        }
        if self.verify_standalone {
            parts.push("--verify-standalone".to_string());
        }
        parts.join(" ")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_full_cli_args_and_preview() {
        let mut opts = PipelineOptions::default();
        opts.pe_file = "/games/demo.exe".to_string();
        opts.project_dir = "projects/demo".to_string();
        opts.set_target_wasm32();
        opts.fresh_build = true;
        opts.import_trace = true;
        opts.capture_screenshots = true;

        let args = opts.to_backend_args();
        assert!(args.contains(&"--target-os".to_string()));
        assert!(args.contains(&"web".to_string()));
        assert!(args.contains(&"wasm32".to_string()));
        assert!(args.contains(&"--fresh-build".to_string()));
        assert!(args.contains(&"--import-trace".to_string()));

        let preview = opts.format_command_preview();
        assert!(preview.contains("game-recomp-cli"));
        assert!(preview.contains("--verify-standalone"));
        assert!(preview.contains("RECOMP_SCREENSHOT="));
    }

    #[test]
    fn custom_stages_always_keep_report() {
        let mut opts = PipelineOptions::default();
        opts.out_mode = OutMode::CustomStages;
        opts.selected_stages = [true, true, false, false, false, false, false, false];
        let st = opts.effective_stages();
        assert_eq!(st, vec!["inspect", "recover", "report"]);
    }
}
