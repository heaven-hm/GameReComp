pub mod home;
pub mod inspector;
pub mod pages;
pub mod project;
pub mod sidebar;
pub mod style;

use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use chrono::Local;
use gpui::{
    App, Application, Bounds, ClipboardItem, Context, Div, FocusHandle, Focusable, FontWeight,
    IntoElement, KeyDownEvent, Render, Styled, Task, TitlebarOptions, Window, WindowBounds,
    WindowOptions, div, prelude::*, px, rgb, size,
};

use crate::config::{AppConfig, ToolchainStatus, UserSettings};
use crate::model::{ProjectModel, ProjectSummary, UserNote, discover_projects};
use crate::orchestrator::default_project_dir;
use crate::pe::{PeInfo, inspect_pe};
use crate::pipeline_opts::{ALL_STAGES, PipelineOptions};
use crate::theme_tokens::{ThemeMode, ThemeTokens};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Route {
    Home,
    Project(ProjectTab),
    Agents,
    Tools,
    Settings,
    About,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ProjectTab {
    Overview,
    Functions,
    StagesBuild,
    Agents,
    Frames,
    Settings,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MapColorMode {
    Provenance,
    Module,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FnFilter {
    All,
    Verified,
    HighLevel,
    Fallback,
    Unresolved,
    Starred,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FnSort {
    Address,
    Name,
    Instructions,
    Category,
    Module,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InspectorTab {
    Source,
    Notes,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DropState {
    Idle,
    Picked,
    Busy,
    Error(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkerKind {
    Pipeline,
    Readability,
    Batch,
}

impl WorkerKind {
    pub fn label(self) -> &'static str {
        match self {
            WorkerKind::Pipeline => "Pipeline",
            WorkerKind::Readability => "Agent",
            WorkerKind::Batch => "Batch",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FocusInput {
    SidebarFilter,
    FnSearch,
    ProjectRename,
    OptPeFile,
    OptProjectDir,
    OptAssetsDir,
    OptHighlevelDir,
    OptOraclePath,
    OptTimeout,
    OptBuildTimeout,
    OptJobs,
    OptSplit,
    AgentCmd,
    AgentTargetFn,
    InspectorNote,
    SettingsProjectsRoot,
    SettingsRomLib,
}

#[derive(Debug, Clone)]
pub enum StageExecState {
    Pending,
    Running(Instant),
    Done(f32),
    Failed,
}

#[derive(Debug, Clone)]
pub struct LiveStageRow {
    pub name: &'static str,
    pub state: StageExecState,
    pub detail: String,
}

#[derive(Debug, Clone)]
pub struct ProcessedFileItem {
    pub action: &'static str,
    pub path: String,
    pub detail: String,
    pub timestamp: String,
    pub stage: String,
}

pub enum WorkerMsg {
    Line(String),
    StageStart(String),
    StageEnd(String, String, f32, String),
    FileEvent {
        action: &'static str,
        path: String,
        detail: String,
        compile_pct: Option<f32>,
    },
    Finished {
        kind: WorkerKind,
        code: i32,
        project_dir: Option<PathBuf>,
        summary: String,
    },
}

#[derive(Debug, Clone)]
pub struct ToastItem {
    pub title: String,
    pub detail: String,
    pub timestamp: String,
    pub color: u32,
    pub is_error: bool,
    pub created: Instant,
}

#[derive(Debug, Clone)]
pub struct NotifItem {
    pub title: String,
    pub detail: String,
    pub timestamp: String,
    pub color: u32,
    pub is_error: bool,
}

#[derive(Debug, Clone)]
pub struct FeedItem {
    pub timestamp: String,
    pub actor: String,
    pub message: String,
    pub is_ok: bool,
}

pub struct GameReCompApp {
    pub focus_handle: FocusHandle,
    pub focused_input: Option<FocusInput>,

    pub cfg: AppConfig,
    pub settings: UserSettings,
    pub theme: ThemeMode,
    pub last_dark_theme: ThemeMode,
    pub last_light_theme: ThemeMode,
    pub show_theme_menu: bool,
    pub route: Route,

    pub projects: Vec<ProjectSummary>,
    pub active_project_dir: Option<PathBuf>,
    pub active_project: Option<ProjectModel>,
    pub load_error: String,
    pub toolchain: ToolchainStatus,

    // Unified GameReComp CLI options (ALL input/output/stage/log/build flags!)
    pub opts: PipelineOptions,
    pub show_advanced_opts: bool,

    // Home / Dropzone state
    pub drop_state: DropState,
    pub picked_pe: PathBuf,
    pub picked_pe_info: PeInfo,
    pub picked_owned_checked: bool,

    // Running background worker & live animated stage + file telemetry tracker
    pub running_kind: Option<WorkerKind>,
    pub running_stage: String,
    pub running_started: Option<Instant>,
    pub running_project_dir: Option<PathBuf>,
    pub anim_frame: usize,
    pub current_file: String,
    pub current_task_detail: String,
    pub compile_pct: Option<f32>,
    pub processed_files: Vec<ProcessedFileItem>,
    pub last_finished_project: Option<PathBuf>,
    pub live_stages: Vec<LiveStageRow>,
    pub running_child: Arc<Mutex<Option<Child>>>,
    pub worker_rx: Option<Receiver<WorkerMsg>>,
    pub _poll_task: Option<Task<()>>,
    pub live_logs: Vec<String>,
    pub feed: Vec<FeedItem>,

    // Sidebar / popovers / notifications
    pub sidebar_filter: String,
    pub show_new_menu: bool,
    pub show_notif_panel: bool,
    pub toasts: Vec<ToastItem>,
    pub notifications: Vec<NotifItem>,
    pub unread_notifs: usize,

    // Project view state
    pub map_mode: MapColorMode,
    pub hovered_map_fn: Option<usize>,
    pub fn_search: String,
    pub fn_filter: FnFilter,
    pub fn_sort: FnSort,
    pub fn_sort_desc: bool,
    pub fn_table_limit: usize,
    pub renaming_project: bool,
    pub rename_buf: String,

    // Function Inspector Drawer state
    pub inspector_open: bool,
    pub inspector_func_idx: Option<usize>,
    pub inspector_tab: InspectorTab,
    pub inspector_expanded: bool,
    pub inspector_source: String,
    pub inspector_source_file: Option<PathBuf>,
    pub inspector_note_input: String,

    // Frames gallery
    pub selected_frame_idx: Option<usize>,

    // Agents & WASM server
    pub agent_cmd_buf: String,
    pub agent_target_fn: String,
    pub wasm_server_url: Option<String>,
    pub wasm_server_child: Arc<Mutex<Option<Child>>>,
}

impl GameReCompApp {
    pub fn new(
        cfg: AppConfig,
        initial_project: Option<&Path>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Self {
        let settings = UserSettings::load();
        let theme = ThemeMode::from_str(&settings.theme);
        let toolchain = ToolchainStatus::detect();
        let agent_cmd_buf = cfg
            .reveng_ai_cmd
            .clone()
            .unwrap_or_else(|| settings.reveng_ai_cmd.clone());

        let mut opts = PipelineOptions::default();
        opts.timeout_secs = settings.default_timeout_secs.max(5).to_string();

        let last_dark_theme = if theme.is_dark() {
            theme
        } else {
            ThemeMode::Dark
        };
        let last_light_theme = if !theme.is_dark() {
            theme
        } else {
            ThemeMode::Light
        };

        let mut app = Self {
            focus_handle: cx.focus_handle(),
            focused_input: None,
            cfg,
            settings,
            theme,
            last_dark_theme,
            last_light_theme,
            show_theme_menu: false,
            route: Route::Home,
            projects: Vec::new(),
            active_project_dir: None,
            active_project: None,
            load_error: String::new(),
            toolchain,
            opts,
            show_advanced_opts: false,
            drop_state: DropState::Idle,
            picked_pe: PathBuf::new(),
            picked_pe_info: PeInfo::default(),
            picked_owned_checked: false,
            running_kind: None,
            running_stage: "starting".to_string(),
            running_started: None,
            running_project_dir: None,
            anim_frame: 0,
            current_file: String::new(),
            current_task_detail: String::new(),
            compile_pct: None,
            processed_files: Vec::new(),
            last_finished_project: None,
            live_stages: fresh_live_stages(),
            running_child: Arc::new(Mutex::new(None)),
            worker_rx: None,
            _poll_task: None,
            live_logs: Vec::new(),
            feed: Vec::new(),
            sidebar_filter: String::new(),
            show_new_menu: false,
            show_notif_panel: false,
            toasts: Vec::new(),
            notifications: Vec::new(),
            unread_notifs: 0,
            map_mode: MapColorMode::Provenance,
            hovered_map_fn: None,
            fn_search: String::new(),
            fn_filter: FnFilter::All,
            fn_sort: FnSort::Address,
            fn_sort_desc: false,
            fn_table_limit: 200,
            renaming_project: false,
            rename_buf: String::new(),
            inspector_open: false,
            inspector_func_idx: None,
            inspector_tab: InspectorTab::Source,
            inspector_expanded: false,
            inspector_source: String::new(),
            inspector_source_file: None,
            inspector_note_input: String::new(),
            selected_frame_idx: None,
            agent_cmd_buf,
            agent_target_fn: String::new(),
            wasm_server_url: None,
            wasm_server_child: Arc::new(Mutex::new(None)),
        };

        app.refresh_projects();
        if let Some(dir) = initial_project {
            app.open_project_dir(dir.to_path_buf(), ProjectTab::Overview);
        }

        // Background ticker via AsyncWindowContext (uses try_borrow_mut under the hood,
        // so nested macOS RunLoops like file dialogs or live window resize never panic).
        let weak = cx.entity().downgrade();
        let poll_task = window.spawn(cx, async move |cx| {
            loop {
                cx.background_executor()
                    .timer(Duration::from_millis(80))
                    .await;
                if weak.upgrade().is_none() {
                    break;
                }
                let _ = weak.update(cx, |this, cx| {
                    let changed = this.poll_worker_and_timers();
                    if changed || this.running_kind.is_some() {
                        cx.notify();
                    }
                });
            }
        });
        app._poll_task = Some(poll_task);

        app
    }

    pub fn tokens(&self) -> ThemeTokens {
        ThemeTokens::for_mode(self.theme)
    }

    pub fn set_focus_input(
        &mut self,
        field: Option<FocusInput>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.focused_input = field;
        window.focus(&self.focus_handle);
        cx.notify();
    }

    pub fn projects_root(&self) -> PathBuf {
        if !self.settings.projects_root.trim().is_empty() {
            PathBuf::from(self.settings.projects_root.trim())
        } else {
            self.cfg.repo_root.join("projects")
        }
    }

    pub fn refresh_projects(&mut self) {
        let root = self.projects_root();
        let extra: Vec<PathBuf> = self
            .settings
            .custom_projects
            .iter()
            .map(PathBuf::from)
            .collect();
        self.projects = discover_projects(&root, &extra);
    }

    pub fn toggle_theme(&mut self, cx: &mut Context<Self>) {
        let next = self
            .theme
            .toggle_dark_light(self.last_dark_theme, self.last_light_theme);
        self.set_theme(next, cx);
    }

    pub fn set_theme(&mut self, mode: ThemeMode, cx: &mut Context<Self>) {
        self.theme = mode;
        if mode.is_dark() {
            self.last_dark_theme = mode;
        } else {
            self.last_light_theme = mode;
        }
        self.show_theme_menu = false;
        self.settings.theme = mode.as_str().to_string();
        let _ = self.settings.save();
        cx.notify();
    }

    pub fn reset_dropzone(&mut self) {
        self.drop_state = DropState::Idle;
        self.picked_pe = PathBuf::new();
        self.picked_pe_info = PeInfo::default();
        self.picked_owned_checked = false;
        self.opts.pe_file.clear();
        self.opts.project_dir.clear();
    }

    pub fn pick_pe_path(&mut self, path: PathBuf) {
        let info = inspect_pe(&path);
        let default_dir = default_project_dir(&self.cfg.repo_root, &path);
        self.opts.pe_file = path.display().to_string();
        self.opts.project_dir = default_dir.display().to_string();
        self.picked_pe = path;
        self.picked_pe_info = info;
        self.picked_owned_checked = false;
        self.drop_state = DropState::Picked;
        self.route = Route::Home;
    }

    pub fn browse_for_pe(&mut self, cx: &mut Context<Self>) {
        let mut dlg = rfd::FileDialog::new()
            .set_title("Choose a 32-bit Windows PE executable")
            .add_filter("Windows PE Executable", &["exe", "EXE"]);
        if !self.settings.rom_library_dir.trim().is_empty() {
            dlg = dlg.set_directory(&self.settings.rom_library_dir);
        }
        if let Some(file) = dlg.pick_file() {
            self.pick_pe_path(file);
            cx.notify();
        }
    }

    pub fn browse_for_project_folder(&mut self, cx: &mut Context<Self>) {
        if let Some(dir) = rfd::FileDialog::new()
            .set_title("Open a saved GameReComp project directory")
            .set_directory(self.projects_root())
            .pick_folder()
        {
            let dir_str = dir.display().to_string();
            if !self.settings.custom_projects.contains(&dir_str) {
                self.settings.custom_projects.push(dir_str);
                let _ = self.settings.save();
            }
            self.refresh_projects();
            self.open_project_dir(dir, ProjectTab::Overview);
            cx.notify();
        }
    }

    pub fn browse_for_batch_folder(&mut self, cx: &mut Context<Self>) {
        if let Some(dir) = rfd::FileDialog::new()
            .set_title("Choose a folder of PE32 executables to batch recompile")
            .pick_folder()
        {
            self.start_batch_worker(dir, cx);
        }
    }

    pub fn open_project_dir(&mut self, dir: PathBuf, tab: ProjectTab) {
        self.active_project_dir = Some(dir.clone());
        self.inspector_open = false;
        self.inspector_func_idx = None;
        self.selected_frame_idx = None;
        self.fn_search.clear();
        self.fn_filter = FnFilter::All;
        self.fn_table_limit = 200;
        self.renaming_project = false;

        match ProjectModel::load(&dir) {
            Ok(mut m) => {
                m.load_certified_index(&self.cfg.repo_root);
                self.opts.pe_file = m.pe_path.clone();
                self.opts.project_dir = m.dir.display().to_string();
                if m.has_wasm_build || m.target_arch == "wasm32" {
                    self.opts.set_target_wasm32();
                } else {
                    self.opts.set_target_native_host();
                }
                self.load_error.clear();
                self.active_project = Some(m);
            }
            Err(e) => {
                self.active_project = None;
                self.load_error = e;
            }
        }
        self.route = Route::Project(tab);
    }

    pub fn reload_active_project(&mut self) {
        if let Some(dir) = self.active_project_dir.clone() {
            let tab = match self.route {
                Route::Project(t) => t,
                _ => ProjectTab::Overview,
            };
            self.open_project_dir(dir, tab);
            self.refresh_projects();
        }
    }

    pub fn open_function_inspector(&mut self, idx: usize) {
        if let Some(ref model) = self.active_project {
            if let Some(row) = model.all_functions.get(idx) {
                let (src, path) = model.load_function_source(row);
                self.inspector_func_idx = Some(idx);
                self.inspector_source = src;
                self.inspector_source_file = path;
                self.inspector_tab = InspectorTab::Source;
                self.inspector_open = true;
            }
        }
    }

    pub fn step_inspector(&mut self, delta: isize) {
        let Some(ref model) = self.active_project else {
            return;
        };
        let len = model.all_functions.len();
        if len == 0 {
            return;
        }
        let cur = self.inspector_func_idx.unwrap_or(0) as isize;
        let next = (cur + delta).rem_euclid(len as isize) as usize;
        self.open_function_inspector(next);
    }

    pub fn toggle_star_function(&mut self, addr: &str) {
        if let Some(ref mut m) = self.active_project {
            if let Some(pos) = m.ui_meta.starred.iter().position(|a| a == addr) {
                m.ui_meta.starred.remove(pos);
            } else {
                m.ui_meta.starred.push(addr.to_string());
            }
            let _ = m.save_ui_meta();
        }
    }

    pub fn add_function_note(&mut self, addr: &str, text: String) {
        let ts = now_hms();
        let (ok_col, err_col) = (self.tokens().match_fill, self.tokens().not_c);
        if let Some(ref mut m) = self.active_project {
            m.ui_meta
                .notes
                .entry(addr.to_string())
                .or_default()
                .push(UserNote {
                    author: "you".to_string(),
                    timestamp: ts,
                    text,
                });
            if let Err(e) = m.save_ui_meta() {
                self.push_toast("Note save failed", &e, err_col, true);
            } else {
                self.push_toast("Note saved", &format!("Attached note to {addr}"), ok_col, false);
            }
        }
    }

    pub fn commit_project_rename(&mut self) {
        let new_name = self.rename_buf.trim().to_string();
        if !new_name.is_empty() {
            if let Some(ref mut m) = self.active_project {
                m.ui_meta.display_name = new_name;
                let _ = m.save_ui_meta();
            }
            self.refresh_projects();
        }
        self.renaming_project = false;
        self.focused_input = None;
    }

    pub fn effective_ai_cmd(&self) -> Option<String> {
        let trimmed = self.agent_cmd_buf.trim();
        if !trimmed.is_empty() {
            Some(trimmed.to_string())
        } else {
            self.cfg.reveng_ai_cmd.clone()
        }
    }

    pub fn save_agent_cmd(&mut self) {
        self.settings.reveng_ai_cmd = self.agent_cmd_buf.trim().to_string();
        self.cfg.reveng_ai_cmd = if self.settings.reveng_ai_cmd.is_empty() {
            None
        } else {
            Some(self.settings.reveng_ai_cmd.clone())
        };
        let _ = self.settings.save();
        let detail = if self.settings.reveng_ai_cmd.is_empty() {
            "Cleared REVENG_AI_CMD.".to_string()
        } else {
            self.settings.reveng_ai_cmd.clone()
        };
        self.push_toast("Agent command saved", &detail, self.tokens().match_fill, false);
    }

    pub fn start_picked_pipeline(&mut self, cx: &mut Context<Self>) {
        self.drop_state = DropState::Busy;
        let opts = self.opts.clone();
        self.spawn_pipeline_worker(opts, cx);
    }

    pub fn start_active_project_pipeline(&mut self, cx: &mut Context<Self>) {
        if self.opts.pe_file.trim().is_empty() {
            self.push_toast(
                "Missing executable",
                "Set the Original PE executable path in Settings first.",
                self.tokens().not_c,
                true,
            );
            cx.notify();
            return;
        }
        let mut opts = self.opts.clone();
        if let Some(ref dir) = self.active_project_dir {
            opts.project_dir = dir.display().to_string();
        }
        self.spawn_pipeline_worker(opts, cx);
    }

    fn spawn_pipeline_worker(&mut self, opts: PipelineOptions, cx: &mut Context<Self>) {
        if self.running_kind.is_some() {
            return;
        }
        let repo_root = self.cfg.repo_root.clone();
        let project_dir = if !opts.project_dir.trim().is_empty() {
            PathBuf::from(opts.project_dir.trim())
        } else {
            default_project_dir(&repo_root, Path::new(&opts.pe_file))
        };
        let capture = opts.capture_screenshots;
        let clean_objects = opts.clean_objects;
        let verify_standalone = opts.verify_standalone;
        let is_dry_run = opts.dry_run;
        let child_slot = Arc::clone(&self.running_child);

        let mut backend_args = opts.to_backend_args();
        // Force plain stage markers so GUI parses every `==> stage` and `ok 1.2s stage:` live!
        if !backend_args.iter().any(|a| a == "--no-progress") {
            backend_args.push("--no-progress".to_string());
        }

        let mut py_args = vec!["-m".to_string(), "tools.autorecomp.cli".to_string()];
        py_args.extend(backend_args);

        self.running_kind = Some(WorkerKind::Pipeline);
        self.running_stage = "inspect".to_string();
        self.running_started = Some(Instant::now());
        self.running_project_dir = Some(project_dir.clone());
        self.anim_frame = 0;
        self.current_file = Path::new(&opts.pe_file)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(&opts.pe_file)
            .to_string();
        self.current_task_detail = format!(
            "Inspecting PE32 headers & targeting {}",
            opts.resolved_target_summary()
        );
        self.compile_pct = None;
        self.processed_files.clear();
        self.processed_files.push(ProcessedFileItem {
            action: "PE",
            path: self.current_file.clone(),
            detail: format!("Input binary -> {}", opts.resolved_target_summary()),
            timestamp: now_hms(),
            stage: "inspect".to_string(),
        });
        self.live_stages = fresh_live_stages();
        if let Some(first) = self.live_stages.first_mut() {
            first.state = StageExecState::Running(Instant::now());
        }
        self.live_logs.clear();
        self.live_logs.push(format!("$ {}", opts.format_command_preview()));
        self.push_feed("pipeline", &format!("started {}", opts.pe_file), false);

        let (tx, rx) = mpsc::channel();
        self.worker_rx = Some(rx);
        cx.notify();

        thread::spawn(move || {
            let mut cmd = Command::new("python3");
            cmd.current_dir(&repo_root)
                .env("PYTHONPATH", &repo_root)
                .args(&py_args)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            if capture {
                let shots = project_dir.join("screenshots");
                let _ = fs::create_dir_all(&shots);
                cmd.env("RECOMP_SCREENSHOT", shots);
            }

            let code = run_streaming_child_inner(cmd, child_slot, &tx);

            if !is_dry_run && clean_objects {
                let _ = crate::tasks::clean_cmake_objects(&project_dir);
            }
            if !is_dry_run && verify_standalone && (code == 0 || code == 3) {
                if let Ok(m) = ProjectModel::load(&project_dir) {
                    if let Some(bin) = m.built_binary_path() {
                        match crate::tasks::verify_standalone_binary(&bin, 700) {
                            Ok(msg) => {
                                let _ = tx.send(WorkerMsg::Line(format!("standalone: PASS ({msg})")));
                            }
                            Err(err) => {
                                let _ = tx.send(WorkerMsg::Line(format!("standalone: FAIL ({err})")));
                            }
                        }
                    }
                }
            }

            let summary = if is_dry_run {
                "Dry-run plan printed to console".to_string()
            } else {
                match code {
                    0 => "Completed cleanly (verified pass)".to_string(),
                    3 => "Recompiled and ran (unverified / partial)".to_string(),
                    2 => "A pipeline stage failed — inspect log for details".to_string(),
                    4 => "Unsupported input or target configuration".to_string(),
                    _ => format!("Exited with status {code}"),
                }
            };
            let _ = tx.send(WorkerMsg::Finished {
                kind: WorkerKind::Pipeline,
                code,
                project_dir: if is_dry_run { None } else { Some(project_dir) },
                summary,
            });
        });
    }

    pub fn start_readability_agent(&mut self, cx: &mut Context<Self>) {
        if self.running_kind.is_some() {
            return;
        }
        let Some(dir) = self.active_project_dir.clone() else {
            return;
        };
        let repo_root = self.cfg.repo_root.clone();
        let ai_cmd = self.effective_ai_cmd();
        let target_fn = self.agent_target_fn.trim().to_string();
        let child_slot = Arc::clone(&self.running_child);

        let mut args = vec![
            "-m".to_string(),
            "tools.autorecomp.readability".to_string(),
            dir.display().to_string(),
        ];
        if !target_fn.is_empty() {
            args.push("--function".to_string());
            args.push(target_fn.clone());
        }

        self.running_kind = Some(WorkerKind::Readability);
        self.running_stage = if target_fn.is_empty() {
            "readability harness".to_string()
        } else {
            format!("function {target_fn}")
        };
        self.running_started = Some(Instant::now());
        self.live_logs.push(format!("$ python3 {}", args.join(" ")));
        self.push_feed("agent", "started readability & provenance pass", false);

        let (tx, rx) = mpsc::channel();
        self.worker_rx = Some(rx);
        cx.notify();

        thread::spawn(move || {
            let mut cmd = Command::new("python3");
            cmd.current_dir(&repo_root)
                .env("PYTHONPATH", &repo_root)
                .args(&args)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            if let Some(ai) = ai_cmd {
                cmd.env("REVENG_AI_CMD", ai);
            }
            let code = run_streaming_child_inner(cmd, child_slot, &tx);
            let _ = tx.send(WorkerMsg::Finished {
                kind: WorkerKind::Readability,
                code,
                project_dir: Some(dir),
                summary: if code == 0 {
                    "Readability pass finished cleanly".to_string()
                } else {
                    format!("Readability pass exited with status {code}")
                },
            });
        });
    }

    fn cli_binary_path(&self) -> PathBuf {
        let cli_name = if cfg!(windows) {
            "game-recomp-cli.exe"
        } else {
            "game-recomp-cli"
        };
        if let Ok(exe) = std::env::current_exe() {
            if let Some(dir) = exe.parent() {
                let sibling = dir.join(cli_name);
                if sibling.is_file() {
                    return sibling;
                }
            }
        }
        let release = self.cfg.repo_root.join("target").join("release").join(cli_name);
        if release.is_file() {
            return release;
        }
        let debug = self.cfg.repo_root.join("target").join("debug").join(cli_name);
        if debug.is_file() {
            return debug;
        }
        PathBuf::from(cli_name)
    }

    pub fn start_batch_worker(&mut self, folder: PathBuf, cx: &mut Context<Self>) {
        if self.running_kind.is_some() {
            return;
        }
        let exe = self.cli_binary_path();
        let child_slot = Arc::clone(&self.running_child);

        self.running_kind = Some(WorkerKind::Batch);
        self.running_stage = format!("scanning {}", folder.display());
        self.running_started = Some(Instant::now());
        self.live_stages = fresh_live_stages();
        self.drop_state = DropState::Busy;
        self.route = Route::Home;
        self.live_logs.clear();
        self.live_logs.push(format!(
            "$ game-recomp-cli batch {} --verify-standalone",
            folder.display()
        ));
        self.push_feed("batch", &format!("scanning {}", folder.display()), false);

        let (tx, rx) = mpsc::channel();
        self.worker_rx = Some(rx);
        cx.notify();

        thread::spawn(move || {
            let mut cmd = Command::new(exe);
            cmd.args(["batch", &folder.display().to_string(), "--verify-standalone"])
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            let code = run_streaming_child_inner(cmd, child_slot, &tx);
            let _ = tx.send(WorkerMsg::Finished {
                kind: WorkerKind::Batch,
                code,
                project_dir: None,
                summary: format!("Batch run finished (exit {code})"),
            });
        });
    }

    pub fn stop_running_worker(&mut self) {
        if let Ok(mut guard) = self.running_child.lock() {
            if let Some(ref mut child) = *guard {
                let _ = child.kill();
            }
        }
        self.push_toast(
            "Stopped",
            "Sent terminate signal to running process.",
            self.tokens().work,
            false,
        );
    }

    pub fn launch_rebuilt_binary(&mut self, bin: &Path) {
        let mut cmd = Command::new(bin);
        if let Some(parent) = bin.parent() {
            cmd.current_dir(parent);
        }
        match cmd.spawn() {
            Ok(_) => {
                self.push_toast(
                    "Launched binary",
                    &format!("Started {}", bin.display()),
                    self.tokens().match_fill,
                    false,
                );
            }
            Err(e) => {
                self.push_toast("Launch failed", &e.to_string(), self.tokens().not_c, true);
            }
        }
    }

    pub fn launch_or_open_wasm(&mut self, project_dir: &Path) {
        if let Some(ref url) = self.wasm_server_url {
            let _ = open::that(url);
            return;
        }
        let exe = self.cli_binary_path();
        let port = 8087u16;
        match Command::new(exe)
            .args([
                "serve",
                &project_dir.display().to_string(),
                "--port",
                &port.to_string(),
            ])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        {
            Ok(child) => {
                if let Ok(mut g) = self.wasm_server_child.lock() {
                    if let Some(mut prev) = g.take() {
                        let _ = prev.kill();
                    }
                    *g = Some(child);
                }
                let url = format!("http://127.0.0.1:{port}/index.html");
                self.wasm_server_url = Some(url.clone());
                let _ = open::that(&url);
                self.push_toast(
                    "WASM server started",
                    &format!("Serving at {url}"),
                    self.tokens().match_fill,
                    false,
                );
            }
            Err(e) => {
                self.push_toast("WASM serve failed", &e.to_string(), self.tokens().not_c, true);
            }
        }
    }

    pub fn report_problem(&mut self, cx: &mut Context<Self>) {
        let report = format!(
            "GameReComp v{} (GPUI)\nOS/Arch: {}/{}\nRepo: {}\nToolchain: {}\n",
            crate::cli::VERSION,
            std::env::consts::OS,
            std::env::consts::ARCH,
            self.cfg.repo_root.display(),
            self.toolchain.format_selftest_line(),
        );
        cx.write_to_clipboard(ClipboardItem::new_string(report));
        let _ = open::that("https://github.com/heaven-hm/GameReComp/issues/new");
        self.push_toast(
            "Report copied",
            "Diagnostic report copied to clipboard and issue tracker opened.",
            self.tokens().info,
            false,
        );
        cx.notify();
    }

    pub fn push_toast(&mut self, title: &str, detail: &str, color: u32, is_error: bool) {
        let ts = now_hms();
        self.toasts.push(ToastItem {
            title: title.to_string(),
            detail: detail.to_string(),
            timestamp: ts.clone(),
            color,
            is_error,
            created: Instant::now(),
        });
        if self.toasts.len() > 3 {
            self.toasts.remove(0);
        }
        self.notifications.push(NotifItem {
            title: title.to_string(),
            detail: detail.to_string(),
            timestamp: ts,
            color,
            is_error,
        });
        if self.notifications.len() > 150 {
            self.notifications.remove(0);
        }
        if !self.show_notif_panel {
            self.unread_notifs += 1;
        }
    }

    pub fn push_feed(&mut self, actor: &str, message: &str, is_ok: bool) {
        self.feed.push(FeedItem {
            timestamp: now_hms(),
            actor: actor.to_string(),
            message: message.to_string(),
            is_ok,
        });
        if self.feed.len() > 300 {
            self.feed.remove(0);
        }
    }

    fn poll_worker_and_timers(&mut self) -> bool {
        let mut changed = false;
        if self.running_kind.is_some() {
            self.anim_frame = self.anim_frame.wrapping_add(1);
            // Periodically discover newly generated C/report files in the live output directory
            if self.anim_frame % 6 == 0 {
                self.scan_live_generated_files();
            }
            changed = true;
        }
        let prev_toasts = self.toasts.len();
        self.toasts
            .retain(|t| t.created.elapsed() < Duration::from_secs(6));
        if self.toasts.len() != prev_toasts {
            changed = true;
        }

        let mut msgs = Vec::new();
        if let Some(ref rx) = self.worker_rx {
            while let Ok(msg) = rx.try_recv() {
                msgs.push(msg);
            }
        }
        if !msgs.is_empty() {
            changed = true;
        }

        for msg in msgs {
            match msg {
                WorkerMsg::StageStart(st) => {
                    self.running_stage = st.clone();
                    if st == "compile" {
                        self.compile_pct = Some(0.0);
                    }
                    self.current_task_detail = crate::pipeline_opts::STAGE_DESCRIPTIONS
                        .iter()
                        .find(|(name, _)| *name == st.as_str())
                        .map(|(_, d)| (*d).to_string())
                        .unwrap_or_else(|| format!("Running stage {st}"));
                    self.push_feed("stage", &format!("==> {st}"), false);
                    for row in &mut self.live_stages {
                        if row.name == st {
                            row.state = StageExecState::Running(Instant::now());
                        } else if let StageExecState::Running(started) = row.state {
                            row.state = StageExecState::Done(started.elapsed().as_secs_f32());
                        }
                    }
                    self.scan_live_generated_files();
                }
                WorkerMsg::StageEnd(st, status, secs, detail) => {
                    for row in &mut self.live_stages {
                        if row.name == st {
                            row.detail = detail.clone();
                            row.state = if status == "failed" {
                                StageExecState::Failed
                            } else {
                                StageExecState::Done(secs)
                            };
                        }
                    }
                    if st == "compile" && status == "ok" {
                        self.compile_pct = Some(1.0);
                    }
                    self.current_task_detail = format!("{st}: {status} ({secs:.1}s) {detail}");
                    self.push_feed(
                        &st,
                        &format!("{status} ({secs:.1}s) {detail}"),
                        status == "ok",
                    );
                    self.scan_live_generated_files();
                }
                WorkerMsg::FileEvent {
                    action,
                    path,
                    detail,
                    compile_pct,
                } => {
                    self.current_file = path.clone();
                    self.current_task_detail = detail.clone();
                    if let Some(pct) = compile_pct {
                        self.compile_pct = Some(pct);
                    }
                    let dup = self
                        .processed_files
                        .last()
                        .map(|last| last.path == path && last.action == action)
                        .unwrap_or(false);
                    if !dup {
                        self.processed_files.push(ProcessedFileItem {
                            action,
                            path,
                            detail,
                            timestamp: now_hms(),
                            stage: self.running_stage.clone(),
                        });
                        if self.processed_files.len() > 240 {
                            self.processed_files.remove(0);
                        }
                    }
                }
                WorkerMsg::Line(l) => {
                    let tr = l.trim();
                    if !tr.is_empty() && !tr.starts_with("$ ") {
                        if tr.len() <= 140 {
                            self.current_task_detail = tr.to_string();
                        }
                    }
                    if self.live_logs.len() > 2500 {
                        self.live_logs.remove(0);
                    }
                    self.live_logs.push(l);
                }
                WorkerMsg::Finished {
                    kind,
                    code,
                    project_dir,
                    summary,
                } => {
                    self.scan_live_generated_files();
                    self.running_kind = None;
                    self.running_started = None;
                    self.running_project_dir = None;
                    self.worker_rx = None;
                    self.live_logs.push(format!("[exit {code}] {summary}"));

                    let ok = code == 0 || code == 3;
                    for row in &mut self.live_stages {
                        if let StageExecState::Running(started) = row.state {
                            row.state = if ok {
                                StageExecState::Done(started.elapsed().as_secs_f32())
                            } else {
                                StageExecState::Failed
                            };
                        }
                    }

                    let col = if ok {
                        self.tokens().match_fill
                    } else {
                        self.tokens().not_c
                    };
                    self.push_feed(kind.label(), &summary, ok);
                    self.push_toast(
                        &format!("{} finished (exit {code})", kind.label()),
                        &summary,
                        col,
                        !ok,
                    );
                    self.refresh_projects();

                    match kind {
                        WorkerKind::Pipeline => {
                            if let Some(dir) = project_dir {
                                if dir.join("reports").join("report.json").is_file() {
                                    self.drop_state = DropState::Idle;
                                    self.last_finished_project = Some(dir.clone());
                                    self.open_project_dir(dir.clone(), ProjectTab::Overview);
                                    if ok && !self.opts.dry_run && self.opts.auto_launch_after_build
                                    {
                                        self.launch_active_project_after_build();
                                    }
                                } else if !ok {
                                    self.drop_state = DropState::Error(summary);
                                } else {
                                    self.drop_state = DropState::Idle;
                                }
                            } else if !ok {
                                self.drop_state = DropState::Error(summary);
                            } else {
                                self.drop_state = DropState::Picked;
                            }
                        }
                        WorkerKind::Readability => {
                            self.reload_active_project();
                        }
                        WorkerKind::Batch => {
                            self.drop_state = DropState::Idle;
                        }
                    }
                }
            }
        }
        changed
    }

    fn launch_active_project_after_build(&mut self) {
        let Some(ref model) = self.active_project else {
            return;
        };
        let dir = model.dir.clone();
        if model.has_wasm_build {
            self.launch_or_open_wasm(&dir);
        } else if let Some(bin) = model.built_binary_path() {
            self.launch_rebuilt_binary(&bin);
        }
    }

    /// Scans the live output directory for newly generated C sources, headers, reports, and
    /// compiled artifacts so the GUI shows each individual file in real time during decompilation.
    fn scan_live_generated_files(&mut self) {
        let Some(proj_dir) = self.running_project_dir.clone() else {
            return;
        };
        let watch_dirs: &[(&str, &str, &str)] = &[
            ("src/recomp/gen", "GEN", "Generated lifted C unit"),
            ("src/highlevel", "HL", "High-level typed C module"),
            ("reports", "REPORT", "Pipeline telemetry & report"),
            ("screenshots", "FRAME", "Captured framebuffer frame"),
        ];
        for (rel_sub, action, desc) in watch_dirs {
            let full = proj_dir.join(rel_sub);
            let Ok(rd) = fs::read_dir(&full) else {
                continue;
            };
            let mut entries: Vec<PathBuf> = rd.flatten().map(|e| e.path()).collect();
            entries.sort();
            for path in entries {
                if !path.is_file() {
                    continue;
                }
                let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
                    continue;
                };
                let rel_display = format!("{rel_sub}/{name}");
                if self
                    .processed_files
                    .iter()
                    .any(|item| item.path == rel_display)
                {
                    continue;
                }
                let size_str = fs::metadata(&path)
                    .map(|m| crate::cli::format_bytes(m.len()))
                    .unwrap_or_default();
                let detail = if size_str.is_empty() {
                    (*desc).to_string()
                } else {
                    format!("{desc} ({size_str})")
                };
                self.current_file = rel_display.clone();
                self.processed_files.push(ProcessedFileItem {
                    action,
                    path: rel_display,
                    detail,
                    timestamp: now_hms(),
                    stage: self.running_stage.clone(),
                });
                if self.processed_files.len() > 240 {
                    self.processed_files.remove(0);
                }
            }
        }
    }

    fn active_input_mut(&mut self) -> Option<&mut String> {
        match self.focused_input? {
            FocusInput::SidebarFilter => Some(&mut self.sidebar_filter),
            FocusInput::FnSearch => Some(&mut self.fn_search),
            FocusInput::ProjectRename => Some(&mut self.rename_buf),
            FocusInput::OptPeFile => Some(&mut self.opts.pe_file),
            FocusInput::OptProjectDir => Some(&mut self.opts.project_dir),
            FocusInput::OptAssetsDir => Some(&mut self.opts.assets_dir),
            FocusInput::OptHighlevelDir => Some(&mut self.opts.highlevel_dir),
            FocusInput::OptOraclePath => Some(&mut self.opts.oracle_path),
            FocusInput::OptTimeout => Some(&mut self.opts.timeout_secs),
            FocusInput::OptBuildTimeout => Some(&mut self.opts.build_timeout_secs),
            FocusInput::OptJobs => Some(&mut self.opts.jobs),
            FocusInput::OptSplit => Some(&mut self.opts.split),
            FocusInput::AgentCmd => Some(&mut self.agent_cmd_buf),
            FocusInput::AgentTargetFn => Some(&mut self.agent_target_fn),
            FocusInput::InspectorNote => Some(&mut self.inspector_note_input),
            FocusInput::SettingsProjectsRoot => Some(&mut self.settings.projects_root),
            FocusInput::SettingsRomLib => Some(&mut self.settings.rom_library_dir),
        }
    }

    fn handle_key_down(&mut self, event: &KeyDownEvent, _window: &mut Window, cx: &mut Context<Self>) {
        let key = event.keystroke.key.as_str();
        let mods = &event.keystroke.modifiers;

        if key == "escape" {
            if self.focused_input.is_some() {
                self.focused_input = None;
            } else if self.inspector_open {
                self.inspector_open = false;
            } else if self.show_notif_panel {
                self.show_notif_panel = false;
            } else if self.show_theme_menu {
                self.show_theme_menu = false;
            } else if self.show_new_menu {
                self.show_new_menu = false;
            }
            cx.notify();
            return;
        }

        // If a text box is focused, handle editing cleanly (backspace, enter, Cmd/Ctrl+V paste, typing)
        if let Some(focus_kind) = self.focused_input {
            if key == "backspace" {
                if let Some(buf) = self.active_input_mut() {
                    buf.pop();
                }
                cx.notify();
                return;
            }
            if key == "enter" {
                match focus_kind {
                    FocusInput::ProjectRename => self.commit_project_rename(),
                    FocusInput::AgentCmd => self.save_agent_cmd(),
                    FocusInput::InspectorNote => {
                        if let Some(idx) = self.inspector_func_idx {
                            if let Some(addr) = self
                                .active_project
                                .as_ref()
                                .and_then(|m| m.all_functions.get(idx))
                                .map(|f| f.address.clone())
                            {
                                let txt = self.inspector_note_input.trim().to_string();
                                if !txt.is_empty() {
                                    self.add_function_note(&addr, txt);
                                    self.inspector_note_input.clear();
                                }
                            }
                        }
                    }
                    _ => {
                        self.focused_input = None;
                    }
                }
                cx.notify();
                return;
            }
            if (mods.platform || mods.control) && key == "v" {
                if let Some(clip) = cx.read_from_clipboard().and_then(|c| c.text()) {
                    if let Some(buf) = self.active_input_mut() {
                        buf.push_str(clip.trim_end_matches(['\r', '\n']));
                    }
                    cx.notify();
                }
                return;
            }
            if !mods.control && !mods.platform && !mods.alt {
                if let Some(ref ch) = event.keystroke.key_char {
                    if !ch.chars().any(|c| c.is_control()) {
                        if let Some(buf) = self.active_input_mut() {
                            buf.push_str(ch);
                        }
                        cx.notify();
                    }
                }
            }
            return;
        }

        // Global navigation shortcuts when no input is focused
        if self.inspector_open {
            if key == "j" {
                self.step_inspector(1);
                cx.notify();
            } else if key == "k" {
                self.step_inspector(-1);
                cx.notify();
            }
        } else if matches!(self.route, Route::Project(ProjectTab::Functions)) && key == "/" {
            self.focused_input = Some(FocusInput::FnSearch);
            cx.notify();
        }
    }

    fn render_notif_center(&self, t: &ThemeTokens, cx: &mut Context<Self>) -> Option<Div> {
        if !self.show_notif_panel {
            return None;
        }
        let mut list = div()
            .id("notif_center_list")
            .max_h(px(380.0))
            .overflow_y_scroll()
            .flex()
            .flex_col();

        if self.notifications.is_empty() {
            list = list.child(
                div()
                    .p(px(16.0))
                    .font_family(style::FONT_SANS)
                    .text_size(px(12.5))
                    .text_color(rgb(t.muted))
                    .child("Nothing to report yet."),
            );
        } else {
            for (i, n) in self.notifications.iter().rev().enumerate() {
                list = list.child(
                    div()
                        .flex()
                        .items_start()
                        .gap(px(10.0))
                        .px(px(14.0))
                        .py(px(10.0))
                        .when(i > 0, |el| el.border_t_1().border_color(rgb(t.line_soft)))
                        .child(
                            div()
                                .mt(px(4.0))
                                .w(px(9.0))
                                .h(px(9.0))
                                .min_w(px(9.0))
                                .rounded(px(2.0))
                                .bg(rgb(n.color)),
                        )
                        .child(
                            div()
                                .flex_1()
                                .flex()
                                .flex_col()
                                .gap(px(2.0))
                                .child(
                                    div()
                                        .flex()
                                        .justify_between()
                                        .child(
                                            div()
                                                .font_family(style::FONT_SANS)
                                                .font_weight(FontWeight::BOLD)
                                                .text_size(px(12.5))
                                                .text_color(rgb(t.ink))
                                                .child(n.title.clone()),
                                        )
                                        .child(
                                            div()
                                                .font_family(style::FONT_MONO)
                                                .text_size(px(10.5))
                                                .text_color(rgb(t.muted))
                                                .child(n.timestamp.clone()),
                                        ),
                                )
                                .child(
                                    div()
                                        .font_family(style::FONT_SANS)
                                        .text_size(px(12.0))
                                        .text_color(if n.is_error {
                                            rgb(t.miss)
                                        } else {
                                            rgb(t.ink_2)
                                        })
                                        .child(n.detail.clone()),
                                ),
                        ),
                );
            }
        }

        Some(
            div()
                .absolute()
                .top(px(12.0))
                .left(px(256.0))
                .w(px(390.0))
                .rounded(px(4.0))
                .bg(rgb(t.overlay))
                .border_1()
                .border_color(rgb(t.ink))
                .shadow_lg()
                .flex()
                .flex_col()
                .child(
                    div()
                        .flex()
                        .items_center()
                        .justify_between()
                        .px(px(14.0))
                        .py(px(10.0))
                        .border_b_1()
                        .border_color(rgb(t.line_soft))
                        .child(
                            div()
                                .flex()
                                .items_baseline()
                                .gap(px(8.0))
                                .child(
                                    div()
                                        .font_family(style::FONT_SANS)
                                        .font_weight(FontWeight::BOLD)
                                        .text_size(px(14.5))
                                        .text_color(rgb(t.ink))
                                        .child("What happened"),
                                )
                                .child(
                                    div()
                                        .font_family(style::FONT_SANS)
                                        .text_size(px(11.5))
                                        .text_color(rgb(t.muted))
                                        .child("in all projects"),
                                ),
                        )
                        .child(
                            div()
                                .flex()
                                .gap(px(6.0))
                                .when(!self.notifications.is_empty(), |el| {
                                    el.child(
                                        style::ghost_btn("notif_clear_btn", t, "Clear").on_click(
                                            cx.listener(|this, _, _, cx| {
                                                this.notifications.clear();
                                                cx.notify();
                                            }),
                                        ),
                                    )
                                })
                                .child(
                                    style::ghost_btn("notif_close_btn", t, "✕").on_click(
                                        cx.listener(|this, _, _, cx| {
                                            this.show_notif_panel = false;
                                            cx.notify();
                                        }),
                                    ),
                                ),
                        ),
                )
                .child(list),
        )
    }

    fn render_toasts(&self, t: &ThemeTokens) -> Option<Div> {
        if self.toasts.is_empty() {
            return None;
        }
        let mut stack = div()
            .absolute()
            .top(px(58.0))
            .when(self.inspector_open, |el| el.left(px(264.0)))
            .when(!self.inspector_open, |el| el.right(px(20.0)))
            .w(px(360.0))
            .flex()
            .flex_col()
            .gap(px(8.0));

        for toast in &self.toasts {
            let (fill, border) = if toast.is_error {
                (t.miss_bg, t.miss)
            } else {
                (t.overlay, t.faint)
            };
            stack = stack.child(
                div()
                    .p(px(12.0))
                    .rounded(px(4.0))
                    .bg(rgb(fill))
                    .border_1()
                    .border_color(rgb(border))
                    .shadow_md()
                    .flex()
                    .items_start()
                    .gap(px(10.0))
                    .child(
                        div()
                            .mt(px(4.0))
                            .w(px(9.0))
                            .h(px(9.0))
                            .min_w(px(9.0))
                            .rounded(px(2.0))
                            .bg(rgb(toast.color)),
                    )
                    .child(
                        div()
                            .flex_1()
                            .flex()
                            .flex_col()
                            .gap(px(2.0))
                            .child(
                                div()
                                    .flex()
                                    .justify_between()
                                    .child(
                                        div()
                                            .font_family(style::FONT_SANS)
                                            .font_weight(FontWeight::BOLD)
                                            .text_size(px(12.5))
                                            .text_color(if toast.is_error {
                                                rgb(t.miss)
                                            } else {
                                                rgb(t.ink)
                                            })
                                            .child(toast.title.clone()),
                                    )
                                    .child(
                                        div()
                                            .font_family(style::FONT_MONO)
                                            .text_size(px(10.5))
                                            .text_color(rgb(t.muted))
                                            .child(toast.timestamp.clone()),
                                    ),
                            )
                            .child(
                                div()
                                    .font_family(style::FONT_MONO)
                                    .text_size(px(11.5))
                                    .text_color(if toast.is_error {
                                        rgb(t.miss)
                                    } else {
                                        rgb(t.ink_2)
                                    })
                                    .child(toast.detail.clone()),
                            ),
                    ),
            );
        }
        Some(stack)
    }
}

impl Drop for GameReCompApp {
    fn drop(&mut self) {
        if let Ok(mut g) = self.running_child.lock() {
            if let Some(ref mut c) = *g {
                let _ = c.kill();
            }
        }
        if let Ok(mut g) = self.wasm_server_child.lock() {
            if let Some(ref mut c) = *g {
                let _ = c.kill();
            }
        }
    }
}

impl Focusable for GameReCompApp {
    fn focus_handle(&self, _cx: &App) -> FocusHandle {
        self.focus_handle.clone()
    }
}

impl Render for GameReCompApp {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let t = self.tokens();
        let main_view = match self.route {
            Route::Home => home::render_home(self, &t, cx).into_any_element(),
            Route::Project(tab) => project::render_project(self, &t, tab, cx).into_any_element(),
            Route::Agents => pages::render_agents_page(self, &t, cx).into_any_element(),
            Route::Tools => pages::render_tools_page(self, &t, cx).into_any_element(),
            Route::Settings => pages::render_settings_page(self, &t, cx).into_any_element(),
            Route::About => pages::render_about_page(self, &t, cx).into_any_element(),
        };

        div()
            .id("gamerecomp_root")
            .track_focus(&self.focus_handle)
            .on_key_down(cx.listener(Self::handle_key_down))
            .relative()
            .size_full()
            .flex()
            .flex_row()
            .bg(rgb(t.bg))
            .text_color(rgb(t.ink))
            .font_family(style::FONT_SANS)
            .child(sidebar::render_sidebar(self, &t, cx))
            .child(main_view)
            .when_some(inspector::render_function_inspector(self, &t, cx), |el, d| {
                el.child(d)
            })
            .when_some(self.render_notif_center(&t, cx), |el, p| el.child(p))
            .when_some(self.render_toasts(&t), |el, st| el.child(st))
    }
}

fn fresh_live_stages() -> Vec<LiveStageRow> {
    ALL_STAGES
        .iter()
        .map(|&name| LiveStageRow {
            name,
            state: StageExecState::Pending,
            detail: String::new(),
        })
        .collect()
}

fn run_streaming_child_inner(
    mut cmd: Command,
    child_slot: Arc<Mutex<Option<Child>>>,
    tx: &mpsc::Sender<WorkerMsg>,
) -> i32 {
    match cmd.spawn() {
        Ok(mut child) => {
            let stdout = child.stdout.take();
            let stderr = child.stderr.take();
            if let Ok(mut g) = child_slot.lock() {
                *g = Some(child);
            }

            let tx_out = tx.clone();
            let t_out = stdout.map(|out| {
                thread::spawn(move || {
                    for line in BufReader::new(out).lines().map_while(Result::ok) {
                        emit_parsed_line(&tx_out, line);
                    }
                })
            });

            let tx_err = tx.clone();
            let t_err = stderr.map(|err| {
                thread::spawn(move || {
                    for line in BufReader::new(err).lines().map_while(Result::ok) {
                        emit_parsed_line(&tx_err, line);
                    }
                })
            });

            let code = loop {
                let done = {
                    let mut g = child_slot.lock().unwrap();
                    if let Some(ref mut c) = *g {
                        match c.try_wait() {
                            Ok(Some(st)) => {
                                *g = None;
                                Some(st.code().unwrap_or(1))
                            }
                            Ok(None) => None,
                            Err(_) => {
                                *g = None;
                                Some(1)
                            }
                        }
                    } else {
                        Some(1)
                    }
                };
                if let Some(c) = done {
                    break c;
                }
                thread::sleep(Duration::from_millis(40));
            };

            if let Some(h) = t_out {
                let _ = h.join();
            }
            if let Some(h) = t_err {
                let _ = h.join();
            }
            code
        }
        Err(e) => {
            let _ = tx.send(WorkerMsg::Line(format!("spawn failed: {e}")));
            1
        }
    }
}

fn emit_parsed_line(tx: &mpsc::Sender<WorkerMsg>, line: String) {
    if let Some(st) = parse_stage_start(&line) {
        let _ = tx.send(WorkerMsg::StageStart(st));
    } else if let Some((st, status, secs, detail)) = parse_stage_end(&line) {
        let _ = tx.send(WorkerMsg::StageEnd(st, status, secs, detail));
    }
    if let Some((action, path, detail, compile_pct)) = parse_file_event(&line) {
        let _ = tx.send(WorkerMsg::FileEvent {
            action,
            path,
            detail,
            compile_pct,
        });
    }
    let _ = tx.send(WorkerMsg::Line(line));
}

/// Extracts per-file compilation, linking, and generation events from pipeline & CMake output.
pub fn parse_file_event(line: &str) -> Option<(&'static str, String, String, Option<f32>)> {
    let tr = line.trim();
    if tr.is_empty() {
        return None;
    }

    // Parse optional leading CMake percentage: `[ 42%] ...`
    let mut pct: Option<f32> = None;
    let mut body = tr;
    if let Some(rest) = tr.strip_prefix('[') {
        if let Some((num_part, after)) = rest.split_once("%]") {
            if let Ok(n) = num_part.trim().parse::<f32>() {
                pct = Some((n / 100.0).clamp(0.0, 1.0));
                body = after.trim();
            }
        }
    }

    if let Some(obj) = body.strip_prefix("Building C object ") {
        let file = obj
            .trim()
            .rsplit('/')
            .next()
            .unwrap_or(obj.trim())
            .trim_end_matches(".o")
            .trim_end_matches(".obj")
            .to_string();
        let pct_str = pct
            .map(|p| format!(" ({:.0}%)", p * 100.0))
            .unwrap_or_default();
        return Some(("CC", file, format!("Compiling C translation unit{pct_str}"), pct));
    }
    if let Some(target) = body.strip_prefix("Linking C executable ") {
        let name = target.trim().to_string();
        return Some(("LINK", name, "Linking standalone executable".to_string(), pct.or(Some(1.0))));
    }
    if let Some(target) = body.strip_prefix("Linking C static library ") {
        let name = target.trim().to_string();
        return Some(("LINK", name, "Archiving runtime library".to_string(), pct));
    }

    // Scan tokens for recognizable generated/processed file paths
    for tok in body.split_whitespace() {
        let clean = tok.trim_matches(|c: char| matches!(c, '\'' | '"' | ',' | ';' | ':' | '(' | ')'));
        if clean.len() < 4 {
            continue;
        }
        let lower = clean.to_ascii_lowercase();
        let action = if lower.ends_with(".wasm") {
            Some("WASM")
        } else if lower.ends_with(".c") || lower.ends_with(".h") {
            Some("GEN")
        } else if lower.ends_with("cmakelists.txt") {
            Some("CMAKE")
        } else if lower.ends_with(".json") || lower.ends_with(".html") {
            Some("REPORT")
        } else if lower.ends_with(".png") || lower.ends_with(".bmp") {
            Some("FRAME")
        } else if lower.ends_with(".exe") {
            Some("PE")
        } else {
            None
        };
        if let Some(act) = action {
            let short = clean
                .rsplit('/')
                .take(2)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect::<Vec<_>>()
                .join("/");
            return Some((act, short, body.chars().take(110).collect(), pct));
        }
    }

    None
}

pub fn parse_stage_start(line: &str) -> Option<String> {
    let rest = line.trim().strip_prefix("==>")?.trim();
    let stage = rest.split_whitespace().next()?.trim_matches(':');
    if stage.is_empty() {
        None
    } else {
        Some(stage.to_string())
    }
}

/// Parses plain stage-end lines from `tools/autorecomp/cli.py`:
/// e.g. `    ok         1.2s  inspect: PE32 i386`
pub fn parse_stage_end(line: &str) -> Option<(String, String, f32, String)> {
    let tr = line.trim();
    let mut parts = tr.splitn(3, char::is_whitespace);
    let mark = parts.next()?.trim();
    if !matches!(mark, "ok" | "failed" | "FAIL" | "skipped") {
        return None;
    }
    let rest = parts.next()?.trim();
    let rest2 = parts.next().unwrap_or("").trim();
    let combined = format!("{rest} {rest2}");
    let combined = combined.trim();
    let mut toks = combined.split_whitespace();
    let sec_tok = toks.next()?;
    let secs = sec_tok.strip_suffix('s')?.parse::<f32>().ok()?;
    let stage_tok = toks.next()?;
    let stage = stage_tok.trim_end_matches(':').to_string();
    let detail = toks.collect::<Vec<_>>().join(" ");
    let norm_status = if mark == "FAIL" {
        "failed".to_string()
    } else {
        mark.to_string()
    };
    if ALL_STAGES.contains(&stage.as_str()) {
        Some((stage, norm_status, secs, detail))
    } else {
        None
    }
}

fn now_hms() -> String {
    Local::now().format("%H:%M:%S").to_string()
}

pub fn launch_gui(cfg: AppConfig, open_dir: Option<&Path>) -> i32 {
    let open_owned = open_dir.map(|p| p.to_path_buf());
    Application::new().run(move |cx: &mut App| {
        style::register_fonts(cx);
        let bounds = Bounds::centered(None, size(px(1380.0), px(880.0)), cx);
        let options = WindowOptions {
            window_bounds: Some(WindowBounds::Windowed(bounds)),
            titlebar: Some(TitlebarOptions {
                title: Some("GameReComp".into()),
                appears_transparent: false,
                ..Default::default()
            }),
            ..Default::default()
        };
        cx.on_window_closed(|cx| {
            if cx.windows().is_empty() {
                cx.quit();
            }
        })
        .detach();
        let cfg_clone = cfg.clone();
        let open_clone = open_owned.clone();
        if let Err(e) = cx.open_window(options, move |window, cx| {
            let view =
                cx.new(|cx| GameReCompApp::new(cfg_clone, open_clone.as_deref(), window, cx));
            let handle = view.read(cx).focus_handle.clone();
            window.focus(&handle);
            view
        }) {
            eprintln!("error: failed to open GPUI window: {e}");
            cx.quit();
        }
        cx.activate(true);
    });
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_stage_start_and_end_lines() {
        assert_eq!(
            parse_stage_start("==> inspect PE32"),
            Some("inspect".to_string())
        );
        assert_eq!(
            parse_stage_start("  ==> compile: cmake"),
            Some("compile".to_string())
        );
        assert_eq!(
            parse_stage_end("    ok         1.4s  inspect: PE32 i386"),
            Some((
                "inspect".to_string(),
                "ok".to_string(),
                1.4,
                "PE32 i386".to_string()
            ))
        );
        assert_eq!(
            parse_stage_end("    failed     0.3s  compile: link error"),
            Some((
                "compile".to_string(),
                "failed".to_string(),
                0.3,
                "link error".to_string()
            ))
        );
    }

    #[test]
    fn parses_per_file_compile_and_generation_events() {
        let (act, path, detail, pct) =
            parse_file_event("[ 42%] Building C object CMakeFiles/game.dir/src/recomp/gen/recomp_001.c.o")
                .unwrap();
        assert_eq!(act, "CC");
        assert_eq!(path, "recomp_001.c");
        assert!(detail.contains("42%"));
        assert!((pct.unwrap() - 0.42).abs() < 0.01);

        let (act2, path2, _, pct2) =
            parse_file_event("[100%] Linking C executable pe32_minimal").unwrap();
        assert_eq!(act2, "LINK");
        assert_eq!(path2, "pe32_minimal");
        assert_eq!(pct2, Some(1.0));

        let (act3, path3, _, _) =
            parse_file_event("wrote src/recomp/gen/recomp_000.c (148 functions)").unwrap();
        assert_eq!(act3, "GEN");
        assert_eq!(path3, "gen/recomp_000.c");
    }
}
