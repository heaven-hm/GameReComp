pub mod home;
pub mod inspector;
pub mod pages;
pub mod project;
pub mod sidebar;
pub mod style;

use std::fs;
use std::io::{self, BufRead, BufReader};
#[cfg(unix)]
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use chrono::Local;
use gpui::{
    div, prelude::*, px, rgb, size, App, Bounds, ClipboardItem, Context, Div, FocusHandle,
    Focusable, FontWeight, IntoElement, KeyDownEvent, PathPromptOptions, Pixels, Point, Render,
    Styled, Task, TitlebarOptions, Window, WindowBounds, WindowOptions,
};

use crate::config::{
    default_user_projects_dir, AppConfig, SystemResources, ToolchainStatus, UserSettings,
};
use crate::model::{
    discover_projects, find_project_binary, load_ui_meta, project_has_wasm, read_project_pe_path,
    save_ui_meta_at, ProjectModel, ProjectSummary, UserNote,
};
use crate::orchestrator::default_project_dir;
use crate::pe::{inspect_pe, PeInfo};
use crate::pipeline_opts::{PipelineOptions, ALL_STAGES};
use crate::theme_tokens::{clamp_font_scale, MonoFont, ThemeMode, ThemeTokens, UiFont};

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

/// Which provider the Inspector's "Improve source code" control should run.
///
/// All three routes go through `tools/autorecomp/readability.py`, which only
/// accepts an edit after a rebuild and a re-run against the recorded baseline.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SourceImproveMethod {
    /// Deterministic provider; no AI command is required.
    #[default]
    Revng,
    /// AI provider configured by `REVENG_AI_CMD` (local CLI/model).
    AiLocal,
    /// AI provider configured by `REVENG_WEB_AI_CMD` (explicit opt-in only).
    AiWeb,
}

impl SourceImproveMethod {
    pub fn label(self) -> &'static str {
        match self {
            Self::Revng => "rev.ng (deterministic)",
            Self::AiLocal => "AI (local)",
            Self::AiWeb => "AI (web)",
        }
    }

    pub fn short_label(self) -> &'static str {
        match self {
            Self::Revng => "rev.ng",
            Self::AiLocal => "AI (local)",
            Self::AiWeb => "AI (web)",
        }
    }

    /// The `--providers` value understood by `readability.py`.
    pub fn providers_arg(self) -> &'static str {
        match self {
            Self::Revng => "revng",
            Self::AiLocal | Self::AiWeb => "ai",
        }
    }
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
    SettingsRevngBin,
    SettingsRevngDir,
    SettingsAiWebEndpoint,
    SettingsAiWebModel,
    SettingsAiWebCmd,
    IssueTitle,
    IssueNotes,
    RunGameDataDir,
    QuickRenameProject,
}

#[derive(Debug, Clone)]
pub struct ProjectContextMenu {
    pub dir: PathBuf,
    pub name: String,
    pub x: f32,
    pub y: f32,
}

#[derive(Debug, Clone)]
pub struct ConfirmDeleteProject {
    pub dir: PathBuf,
    pub name: String,
    pub delete_from_disk: bool,
}

#[derive(Debug, Clone)]
pub struct RenameProjectModal {
    pub dir: PathBuf,
    pub name_buf: String,
}

#[derive(Debug, Clone)]
pub struct RunGameModal {
    pub project_dir: PathBuf,
    pub project_name: String,
    pub binary_path: Option<PathBuf>,
    pub has_wasm: bool,
    pub use_data_dir: bool,
    pub data_dir: String,
    pub suggested_pe_dir: String,
    pub save_as_default: bool,
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JobStatus {
    Running,
    Paused,
    Done(i32),
    Failed(i32),
    Stopped,
}

#[derive(Debug, Clone)]
pub struct ProcessedFileItem {
    pub action: &'static str,
    pub path: String,
    pub detail: String,
    pub timestamp: String,
    pub stage: String,
}

pub struct ActiveJob {
    pub id: u64,
    pub kind: WorkerKind,
    pub name: String,
    pub pe_path: String,
    pub pe_summary: String,
    pub target_label: String,
    pub project_dir: Option<PathBuf>,
    pub built_binary: Option<PathBuf>,
    pub has_wasm: bool,
    pub failure_summary: String,
    pub auto_launch_after_build: bool,
    pub auto_ai_readability: bool,
    pub is_dry_run: bool,
    pub status: JobStatus,
    pub stage: String,
    pub started: Instant,
    pub paused_at: Option<Instant>,
    pub paused_total: Duration,
    pub finished_secs: Option<f32>,
    pub current_file: String,
    pub current_task_detail: String,
    pub compile_pct: Option<f32>,
    pub processed_files: Vec<ProcessedFileItem>,
    pub live_stages: Vec<LiveStageRow>,
    pub live_logs: Vec<String>,
    pub child: Arc<Mutex<Option<Child>>>,
    pub cancel_requested: Arc<AtomicBool>,
    pub pgid: Arc<Mutex<Option<u32>>>,
    pub rx: Option<Receiver<WorkerMsg>>,
}

impl ActiveJob {
    pub fn is_active(&self) -> bool {
        matches!(self.status, JobStatus::Running | JobStatus::Paused)
    }

    pub fn is_running(&self) -> bool {
        self.status == JobStatus::Running
    }

    pub fn is_paused(&self) -> bool {
        self.status == JobStatus::Paused
    }

    pub fn elapsed_secs(&self) -> f32 {
        if let Some(done) = self.finished_secs {
            return done;
        }
        let extra_pause = self
            .paused_at
            .map(|p| p.elapsed())
            .unwrap_or(Duration::ZERO);
        let active_dur = self
            .started
            .elapsed()
            .saturating_sub(self.paused_total + extra_pause);
        active_dur.as_secs_f32()
    }

    pub fn progress_fraction(&self) -> f32 {
        if matches!(self.status, JobStatus::Done(_)) {
            return 1.0;
        }
        let done_count = self
            .live_stages
            .iter()
            .filter(|s| matches!(s.state, StageExecState::Done(_)))
            .count();
        let bonus = self.compile_pct.unwrap_or(0.35).clamp(0.0, 0.95);
        ((done_count as f32 + bonus) / (ALL_STAGES.len() as f32)).clamp(0.02, 1.0)
    }
}

#[derive(Debug, Clone, Default)]
pub struct IssueDraft {
    pub game_name: String,
    pub title: String,
    pub pe_summary: String,
    pub target_summary: String,
    pub failed_stage: String,
    pub exit_code: Option<i32>,
    pub error_summary: String,
    pub log_tail: String,
    pub user_notes: String,
}

#[derive(Debug, Clone)]
pub struct QueuedJob {
    pub id: u64,
    pub name: String,
    pub pe_path: String,
    pub target_label: String,
    pub opts: PipelineOptions,
}

#[derive(Debug, Clone)]
pub struct QueuedBatch {
    pub id: u64,
    pub folder: PathBuf,
}

pub struct PendingProjectDelete {
    dir: PathBuf,
    name: String,
    canon: PathBuf,
    identity: FileIdentity,
    custom_aliases: Vec<String>,
    selected_is_symlink: bool,
}

#[derive(Clone, Copy)]
struct FileIdentity {
    volume: u64,
    file: u128,
    symlink: bool,
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
        built_binary: Option<PathBuf>,
        has_wasm: bool,
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
    pub ui_font: UiFont,
    pub mono_font: MonoFont,
    pub font_scale: f32,
    pub code_font_scale: f32,
    pub show_theme_menu: bool,
    pub route: Route,

    pub projects: Vec<ProjectSummary>,
    pub projects_loading: bool,
    pub projects_refresh_generation: u64,
    pub project_load_generation: u64,
    pub toolchain_generation: u64,
    pub deleting_projects: Vec<PathBuf>,
    pub pending_project_deletions: Vec<PendingProjectDelete>,
    pub active_project_dir: Option<PathBuf>,
    pub active_project: Option<ProjectModel>,
    pub load_error: String,
    pub toolchain: ToolchainStatus,
    pub toolchain_loading: bool,
    pub sys_resources: SystemResources,

    // Unified GameReComp CLI options (ALL input/output/stage/log/build flags!)
    pub opts: PipelineOptions,
    pub show_advanced_opts: bool,

    // Home / Dropzone state
    pub drop_state: DropState,
    pub picked_pe: PathBuf,
    pub picked_pe_info: PeInfo,
    pub picked_owned_checked: bool,

    // Multi-game parallel decompilation & queue state
    pub jobs: Vec<ActiveJob>,
    pub queued_pipelines: Vec<QueuedJob>,
    pub queued_batches: Vec<QueuedBatch>,
    pub selected_job_id: Option<u64>,
    pub next_job_id: u64,
    /// `0` = Auto (system RAM/CPU resources), `1..=8` = manual override.
    pub max_concurrent_jobs: usize,
    pub queue_paused: bool,
    pub anim_frame: usize,
    pub last_finished_project: Option<PathBuf>,
    pub _poll_task: Option<Task<()>>,
    pub feed: Vec<FeedItem>,

    // Sidebar / popovers / notifications / modals
    pub sidebar_filter: String,
    pub sidebar_collapsed: bool,
    pub show_new_menu: bool,
    pub show_notif_panel: bool,
    pub show_issue_modal: bool,
    pub issue_draft: IssueDraft,
    pub context_menu: Option<ProjectContextMenu>,
    pub confirm_delete: Option<ConfirmDeleteProject>,
    pub rename_modal: Option<RenameProjectModal>,
    pub run_game_modal: Option<RunGameModal>,
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

    // "Improve source code" method selector (Inspector source view)
    pub source_improve_method: SourceImproveMethod,
    pub show_source_improve_menu: bool,

    // Frames gallery
    pub selected_frame_idx: Option<usize>,

    // Agents & WASM server
    pub agent_cmd_buf: String,
    pub agent_target_fn: String,
    pub wasm_server_url: Option<String>,
    pub wasm_server_dir: Option<PathBuf>,
    pub wasm_server_ready_file: Option<PathBuf>,
    pub wasm_server_child: Arc<Mutex<Option<Child>>>,
    pub wasm_server_generation: Arc<AtomicU64>,
    pub wasm_server_open_lock: Arc<Mutex<()>>,
    pub wasm_server_ready: Arc<AtomicBool>,
    pub wasm_server_startup_error: Arc<Mutex<Option<(u64, String, Option<PathBuf>)>>>,
}

/// Prepare the shared pipeline options for a real (non-dry-run) "Run pipeline"
/// start from the project workspace.
///
/// The Home screen reuses `App::opts` for both "Start recompiling" and
/// "Dry-run plan". A dry-run used to leave `dry_run = true` behind, so a later
/// "Run pipeline" on an opened project silently printed a plan and produced no
/// binary, logs or launchable build. Clearing it here makes every real start
/// explicit and keeps the backend command free of `--dry-run`.
///
/// Returns `false` when no original PE is configured, in which case the caller
/// should show the "Missing executable" guidance instead of starting a job.
pub fn prepare_active_project_options(
    opts: &mut PipelineOptions,
    project_dir: Option<&Path>,
) -> bool {
    if opts.pe_file.trim().is_empty() {
        return false;
    }
    opts.dry_run = false;
    if let Some(dir) = project_dir {
        opts.project_dir = dir.display().to_string();
    }
    true
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
        let ui_font = UiFont::from_str(&settings.ui_font);
        let mono_font = MonoFont::from_str(&settings.mono_font);
        let font_scale = clamp_font_scale(settings.font_scale);
        let code_font_scale = clamp_font_scale(settings.code_font_scale);
        let sys_resources = SystemResources::detect();
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

        let max_concurrent_jobs = if settings.max_concurrent_jobs == 0 {
            0
        } else {
            settings.max_concurrent_jobs.clamp(1, 8)
        };
        let mut app = Self {
            focus_handle: cx.focus_handle(),
            focused_input: None,
            cfg,
            settings,
            theme,
            last_dark_theme,
            last_light_theme,
            ui_font,
            mono_font,
            font_scale,
            code_font_scale,
            show_theme_menu: false,
            route: Route::Home,
            projects: Vec::new(),
            projects_loading: true,
            projects_refresh_generation: 0,
            project_load_generation: 0,
            toolchain_generation: 0,
            deleting_projects: Vec::new(),
            pending_project_deletions: Vec::new(),
            active_project_dir: None,
            active_project: None,
            load_error: String::new(),
            toolchain: ToolchainStatus::default(),
            toolchain_loading: false,
            sys_resources,
            opts,
            show_advanced_opts: false,
            drop_state: DropState::Idle,
            picked_pe: PathBuf::new(),
            picked_pe_info: PeInfo::default(),
            picked_owned_checked: false,
            jobs: Vec::new(),
            queued_pipelines: Vec::new(),
            queued_batches: Vec::new(),
            selected_job_id: None,
            next_job_id: 1,
            max_concurrent_jobs,
            queue_paused: false,
            anim_frame: 0,
            last_finished_project: None,
            _poll_task: None,
            feed: Vec::new(),
            sidebar_filter: String::new(),
            sidebar_collapsed: false,
            show_new_menu: false,
            show_notif_panel: false,
            show_issue_modal: false,
            issue_draft: IssueDraft::default(),
            context_menu: None,
            confirm_delete: None,
            rename_modal: None,
            run_game_modal: None,
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
            source_improve_method: SourceImproveMethod::default(),
            show_source_improve_menu: false,
            selected_frame_idx: None,
            agent_cmd_buf,
            agent_target_fn: String::new(),
            wasm_server_url: None,
            wasm_server_dir: None,
            wasm_server_ready_file: None,
            wasm_server_child: Arc::new(Mutex::new(None)),
            wasm_server_generation: Arc::new(AtomicU64::new(0)),
            wasm_server_open_lock: Arc::new(Mutex::new(())),
            wasm_server_ready: Arc::new(AtomicBool::new(false)),
            wasm_server_startup_error: Arc::new(Mutex::new(None)),
        };

        // Sync GPUI Kit theme and launch non-blocking background scans
        style::sync_gpui_kit_theme(&app.tokens(), cx);
        app.refresh_projects_async(window, cx);
        app.refresh_toolchain_async(window, cx);
        if let Some(dir) = initial_project {
            app.open_project_dir(dir.to_path_buf(), ProjectTab::Overview);
        }

        // Background ticker via AsyncWindowContext (uses try_borrow_mut under the hood,
        // so nested macOS RunLoops like file dialogs or live window resize never panic).
        let weak = cx.entity().downgrade();
        let poll_task = window.spawn(cx, async move |cx| loop {
            cx.background_executor()
                .timer(Duration::from_millis(80))
                .await;
            if weak.upgrade().is_none() {
                break;
            }
            let _ = cx.update(|window, app| {
                if let Some(ent) = weak.upgrade() {
                    ent.update(app, |this, cx| {
                        let changed = this.poll_worker_and_timers(window, cx);
                        if changed || this.has_running_jobs() {
                            cx.notify();
                        }
                    });
                }
            });
        });
        app._poll_task = Some(poll_task);

        app
    }

    pub fn tokens(&self) -> ThemeTokens {
        ThemeTokens::for_appearance(
            self.theme,
            self.ui_font,
            self.mono_font,
            self.font_scale,
            self.code_font_scale,
        )
    }

    pub fn set_ui_font(&mut self, font: UiFont, cx: &mut Context<Self>) {
        self.ui_font = font;
        self.settings.ui_font = font.as_str().to_string();
        let _ = self.settings.save();
        cx.notify();
    }

    pub fn set_mono_font(&mut self, font: MonoFont, cx: &mut Context<Self>) {
        self.mono_font = font;
        self.settings.mono_font = font.as_str().to_string();
        let _ = self.settings.save();
        cx.notify();
    }

    pub fn set_font_scale(&mut self, scale: f32, cx: &mut Context<Self>) {
        let clamped = clamp_font_scale(scale);
        self.font_scale = (clamped * 100.0).round() / 100.0;
        self.settings.font_scale = self.font_scale;
        let _ = self.settings.save();
        cx.notify();
    }

    pub fn step_font_scale(&mut self, delta: f32, cx: &mut Context<Self>) {
        self.set_font_scale(self.font_scale + delta, cx);
    }

    pub fn set_code_font_scale(&mut self, scale: f32, cx: &mut Context<Self>) {
        let clamped = clamp_font_scale(scale);
        self.code_font_scale = (clamped * 100.0).round() / 100.0;
        self.settings.code_font_scale = self.code_font_scale;
        let _ = self.settings.save();
        cx.notify();
    }

    pub fn step_code_font_scale(&mut self, delta: f32, cx: &mut Context<Self>) {
        self.set_code_font_scale(self.code_font_scale + delta, cx);
    }

    pub fn reset_typography(&mut self, cx: &mut Context<Self>) {
        self.ui_font = UiFont::Archivo;
        self.mono_font = MonoFont::IbmPlexMono;
        self.font_scale = 1.0;
        self.code_font_scale = 1.0;
        self.settings.ui_font = self.ui_font.as_str().to_string();
        self.settings.mono_font = self.mono_font.as_str().to_string();
        self.settings.font_scale = 1.0;
        self.settings.code_font_scale = 1.0;
        let _ = self.settings.save();
        cx.notify();
    }

    pub fn active_running_count(&self) -> usize {
        self.jobs.iter().filter(|j| j.is_running()).count()
    }

    pub fn active_paused_count(&self) -> usize {
        self.jobs.iter().filter(|j| j.is_paused()).count()
    }

    pub fn active_slot_count(&self) -> usize {
        if self
            .jobs
            .iter()
            .any(|job| job.is_active() && job.kind == WorkerKind::Batch)
        {
            self.effective_max_concurrent_jobs()
        } else {
            self.jobs.iter().filter(|j| j.is_active()).count()
        }
    }

    pub fn effective_max_concurrent_jobs(&self) -> usize {
        if self.max_concurrent_jobs == 0 {
            self.sys_resources.recommended_parallel_games.clamp(1, 8)
        } else {
            self.max_concurrent_jobs.clamp(1, 8)
        }
    }

    pub fn max_concurrent_label(&self) -> String {
        if self.max_concurrent_jobs == 0 {
            format!("Auto ({})", self.effective_max_concurrent_jobs())
        } else {
            format!("{}", self.max_concurrent_jobs)
        }
    }

    pub fn has_running_jobs(&self) -> bool {
        self.jobs.iter().any(|j| j.is_running())
    }

    pub fn has_active_jobs(&self) -> bool {
        self.jobs.iter().any(|j| j.is_active())
    }

    pub fn is_project_running(&self, dir: &Path) -> bool {
        self.jobs
            .iter()
            .any(|j| j.is_active() && j.project_dir.as_deref() == Some(dir))
    }

    pub fn focused_job(&self) -> Option<&ActiveJob> {
        if let Some(id) = self.selected_job_id {
            if let Some(j) = self.jobs.iter().find(|j| j.id == id) {
                return Some(j);
            }
        }
        self.jobs
            .iter()
            .find(|j| j.is_running())
            .or_else(|| self.jobs.iter().find(|j| j.is_paused()))
            .or_else(|| self.jobs.last())
    }

    pub fn job_for_project(&self, dir: &Path) -> Option<&ActiveJob> {
        self.jobs
            .iter()
            .rev()
            .find(|j| j.project_dir.as_deref() == Some(dir))
    }

    pub fn set_max_concurrent_jobs(&mut self, n: usize, cx: &mut Context<Self>) {
        self.max_concurrent_jobs = if n == 0 { 0 } else { n.clamp(1, 8) };
        self.settings.max_concurrent_jobs = self.max_concurrent_jobs;
        let _ = self.settings.save();
        self.drain_queued_pipelines();
        cx.notify();
    }

    pub fn browse_for_multiple_pes(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let rx = cx.prompt_for_paths(PathPromptOptions {
            files: true,
            directories: false,
            multiple: true,
            prompt: Some("Select game/app executables (.exe, Mach-O, ELF) - unsupported formats are refused with a clear reason".into()),
        });
        let weak = cx.entity().downgrade();
        window
            .spawn(cx, async move |cx| {
                if let Ok(Ok(Some(files))) = rx.await {
                    let selected_count = files.len();
                    let exes: Vec<PathBuf> = files
                        .into_iter()
                        .filter(|p| {
                            p.is_file()
                                && p.is_file().then(|| inspect_pe(p)).is_some_and(|info| {
                                    info.ok && info.pe32_x86
                                })
                        })
                        .collect();
                    let accepted_count = exes.len();
                    let rejected_count = selected_count.saturating_sub(accepted_count);
                    if !exes.is_empty() {
                        let _ = weak.update(cx, |this, cx| {
                            if exes.len() == 1 && !this.has_active_jobs() && this.jobs.is_empty() {
                                this.pick_pe_path(exes[0].clone());
                            } else {
                                this.enqueue_multiple_pes(exes);
                            }
                            cx.notify();
                        });
                    } else if rejected_count > 0 {
                        let _ = weak.update(cx, |this, cx| {
                            this.push_toast(
                                "No executable selected",
                                &format!("Skipped {rejected_count} item(s); unsupported formats are refused by the backend with a named reason."),
                                this.tokens().not_c,
                                true,
                            );
                            cx.notify();
                        });
                    }
                    if rejected_count > 0 && accepted_count > 0 {
                        let _ = weak.update(cx, |this, cx| {
                            this.push_toast(
                                "Some files skipped",
                                &format!("Skipped {rejected_count} invalid or unsupported executable(s)."),
                                this.tokens().info,
                                false,
                            );
                            cx.notify();
                        });
                    }
                }
            })
            .detach();
    }

    pub fn default_project_dir_for(&self, pe: &Path) -> PathBuf {
        let slug = default_project_dir(&self.cfg.repo_root, pe)
            .file_name()
            .map(|n| n.to_os_string())
            .unwrap_or_else(|| std::ffi::OsString::from("game"));
        self.projects_root().join(slug)
    }

    pub fn enqueue_multiple_pes(&mut self, paths: Vec<PathBuf>) {
        let mut added = 0usize;
        let mut skipped: Vec<String> = Vec::new();
        for path in paths {
            if !path.is_file() {
                continue;
            }
            let name = path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("file")
                .to_string();
            let info = inspect_pe(&path);
            if !info.ok || !info.pe32_x86 {
                skipped.push(name);
                continue;
            }
            let mut opts = self.opts.clone();
            opts.pe_file = path.display().to_string();
            opts.project_dir = self.default_project_dir_for(&path).display().to_string();
            opts.dry_run = false;
            if self.enqueue_or_start_pipeline_inner(opts) {
                added += 1;
            }
        }
        self.drop_state = DropState::Idle;
        if added > 0 {
            let msg = format!(
                "Added {added} game(s) · running {}/{}",
                self.active_running_count(),
                self.effective_max_concurrent_jobs()
            );
            let ok_col = self.tokens().match_fill;
            self.push_toast("Multi-game queue updated", &msg, ok_col, false);
        }
        if !skipped.is_empty() {
            let err_col = self.tokens().miss;
            self.push_toast(
                "Skipped unsupported files",
                &format!("Not PE32 i386: {}", skipped.join(", ")),
                err_col,
                true,
            );
        }
    }

    pub fn set_focus_input(
        &mut self,
        field: Option<FocusInput>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.focused_input = field;
        window.focus(&self.focus_handle, cx);
        cx.notify();
    }

    pub fn projects_root(&self) -> PathBuf {
        if !self.settings.projects_root.trim().is_empty() {
            PathBuf::from(self.settings.projects_root.trim())
        } else {
            let repo_projects = self.cfg.repo_root.join("projects");
            if repo_projects.is_dir() {
                repo_projects
            } else {
                default_user_projects_dir()
            }
        }
    }

    fn filter_hidden_projects(
        items: Vec<ProjectSummary>,
        hidden: &[String],
    ) -> Vec<ProjectSummary> {
        if hidden.is_empty() {
            return items;
        }
        let hidden_canon: Vec<PathBuf> = hidden
            .iter()
            .filter(|s| !s.trim().is_empty())
            .map(|s| {
                let p = PathBuf::from(s.trim());
                fs::canonicalize(&p).unwrap_or(p)
            })
            .collect();
        items
            .into_iter()
            .filter(|proj| {
                let c = fs::canonicalize(&proj.dir).unwrap_or_else(|_| proj.dir.clone());
                let raw = proj.dir.display().to_string();
                !hidden.iter().any(|h| h == &raw) && !hidden_canon.iter().any(|hc| hc == &c)
            })
            .collect()
    }

    pub fn unhide_project_dir(&mut self, dir: &Path) {
        let dir_str = dir.display().to_string();
        let canon = fs::canonicalize(dir).unwrap_or_else(|_| dir.to_path_buf());
        let before = self.settings.hidden_projects.len();
        self.settings.hidden_projects.retain(|h| {
            let hp = PathBuf::from(h);
            h != &dir_str && fs::canonicalize(&hp).unwrap_or(hp) != canon
        });
        if self.settings.hidden_projects.len() != before {
            let _ = self.settings.save();
        }
    }

    pub fn refresh_projects(&mut self) {
        self.projects_refresh_generation = self.projects_refresh_generation.wrapping_add(1);
        let root = self.projects_root();
        let extra: Vec<PathBuf> = self
            .settings
            .custom_projects
            .iter()
            .map(PathBuf::from)
            .collect();
        let items = discover_projects(&root, &extra);
        self.projects = Self::filter_hidden_projects(items, &self.settings.hidden_projects);
        self.projects_loading = false;
    }

    pub fn refresh_projects_async(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.projects_refresh_generation = self.projects_refresh_generation.wrapping_add(1);
        let generation = self.projects_refresh_generation;
        self.projects_loading = true;
        let root = self.projects_root();
        let extra: Vec<PathBuf> = self
            .settings
            .custom_projects
            .iter()
            .map(PathBuf::from)
            .collect();
        let hidden = self.settings.hidden_projects.clone();
        let bg = cx.background_executor().spawn(async move {
            let items = discover_projects(&root, &extra);
            Self::filter_hidden_projects(items, &hidden)
        });
        let weak = cx.entity().downgrade();
        window
            .spawn(cx, async move |cx| {
                let items = bg.await;
                let _ = weak.update(cx, |this, cx| {
                    if this.projects_refresh_generation == generation {
                        this.projects = items;
                        this.projects_loading = false;
                    }
                    cx.notify();
                });
            })
            .detach();
    }

    pub fn refresh_toolchain_async(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.toolchain_generation = self.toolchain_generation.wrapping_add(1);
        let generation = self.toolchain_generation;
        self.toolchain_loading = true;
        let repo_root = self.cfg.repo_root.clone();
        let bg = cx
            .background_executor()
            .spawn(async move { ToolchainStatus::detect_for_repo(&repo_root) });
        let weak = cx.entity().downgrade();
        window
            .spawn(cx, async move |cx| {
                let tc = bg.await;
                let _ = weak.update(cx, |this, cx| {
                    if this.toolchain_generation == generation {
                        this.toolchain = tc;
                        this.toolchain_loading = false;
                    }
                    cx.notify();
                });
            })
            .detach();
    }

    pub fn pick_folder_async(
        &mut self,
        prompt: &'static str,
        window: &mut Window,
        cx: &mut Context<Self>,
        on_pick: impl FnOnce(&mut GameReCompApp, PathBuf, &mut Window, &mut Context<Self>) + 'static,
    ) {
        let rx = cx.prompt_for_paths(PathPromptOptions {
            files: false,
            directories: true,
            multiple: false,
            prompt: Some(prompt.into()),
        });
        let weak = cx.entity().downgrade();
        window
            .spawn(cx, async move |cx| {
                if let Ok(Ok(Some(mut dirs))) = rx.await {
                    if let Some(dir) = dirs.pop() {
                        let _ = cx.update(|window, app| {
                            if let Some(ent) = weak.upgrade() {
                                ent.update(app, |this, cx| {
                                    on_pick(this, dir, window, cx);
                                    cx.notify();
                                });
                            }
                        });
                    }
                }
            })
            .detach();
    }

    pub fn pick_file_async(
        &mut self,
        prompt: &'static str,
        window: &mut Window,
        cx: &mut Context<Self>,
        on_pick: impl FnOnce(&mut GameReCompApp, PathBuf, &mut Window, &mut Context<Self>) + 'static,
    ) {
        let rx = cx.prompt_for_paths(PathPromptOptions {
            files: true,
            directories: false,
            multiple: false,
            prompt: Some(prompt.into()),
        });
        let weak = cx.entity().downgrade();
        window
            .spawn(cx, async move |cx| {
                if let Ok(Ok(Some(mut files))) = rx.await {
                    if let Some(file) = files.pop() {
                        let _ = cx.update(|window, app| {
                            if let Some(ent) = weak.upgrade() {
                                ent.update(app, |this, cx| {
                                    on_pick(this, file, window, cx);
                                    cx.notify();
                                });
                            }
                        });
                    }
                }
            })
            .detach();
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
        let tok = self.tokens();
        style::sync_gpui_kit_theme(&tok, cx);
        cx.notify();
    }

    pub fn reset_dropzone(&mut self) {
        self.drop_state = DropState::Idle;
        self.picked_pe = PathBuf::new();
        self.picked_pe_info = PeInfo::default();
        self.picked_owned_checked = false;
        self.opts.pe_file.clear();
        self.opts.project_dir.clear();
        // A "Dry-run plan" must not leak into the next real recompile. Every
        // real start path clears this explicitly, but clearing it here too
        // keeps the shared options object from carrying plan-only state
        // across a pick -> open project -> Run pipeline flow.
        self.opts.dry_run = false;
    }

    pub fn pick_pe_path(&mut self, path: PathBuf) {
        let info = inspect_pe(&path);
        let display_path = crate::pe::resolve_app_bundle(&path);
        let default_dir = self.default_project_dir_for(&path);
        self.opts.pe_file = display_path.display().to_string();
        self.opts.project_dir = default_dir.display().to_string();
        self.picked_pe = path;
        self.picked_pe_info = info;
        self.picked_owned_checked = false;
        self.drop_state = DropState::Picked;
        self.route = Route::Home;
    }

    pub fn browse_for_pe(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let rx = cx.prompt_for_paths(PathPromptOptions {
            files: true,
            directories: false,
            multiple: false,
            prompt: Some("Choose a game/app executable (.exe, Mach-O, ELF) - unsupported formats are refused with a clear reason".into()),
        });
        let weak = cx.entity().downgrade();
        window
            .spawn(cx, async move |cx| {
                if let Ok(Ok(Some(mut files))) = rx.await {
                    if let Some(file) = files.pop() {
                        let _ = weak.update(cx, |this, cx| {
                            this.pick_pe_path(file);
                            cx.notify();
                        });
                    }
                }
            })
            .detach();
    }

    pub fn browse_for_project_folder(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.pick_folder_async(
            "Open a saved GameReComp project directory",
            window,
            cx,
            |this, dir, window, cx| {
                let dir_str = dir.display().to_string();
                this.unhide_project_dir(&dir);
                if !this.settings.custom_projects.contains(&dir_str) {
                    this.settings.custom_projects.push(dir_str);
                    let _ = this.settings.save();
                }
                this.refresh_projects_async(window, cx);
                this.open_project_dir_async(dir, ProjectTab::Overview, window, cx);
            },
        );
    }

    pub fn browse_for_batch_folder(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.pick_folder_async(
            "Choose a folder of PE32 executables to batch recompile",
            window,
            cx,
            |this, dir, _window, cx| {
                this.start_batch_worker(dir, cx);
            },
        );
    }

    fn apply_loaded_project(&mut self, result: Result<ProjectModel, String>) {
        match result {
            Ok(m) => {
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
    }

    pub fn open_project_dir(&mut self, dir: PathBuf, tab: ProjectTab) {
        self.project_load_generation = self.project_load_generation.wrapping_add(1);
        self.active_project_dir = Some(dir.clone());
        self.inspector_open = false;
        self.inspector_func_idx = None;
        self.selected_frame_idx = None;
        self.fn_search.clear();
        self.fn_filter = FnFilter::All;
        self.fn_table_limit = 200;
        self.renaming_project = false;

        let res = ProjectModel::load(&dir).map(|mut m| {
            m.load_certified_index(&self.cfg.repo_root);
            m
        });
        self.apply_loaded_project(res);
        self.route = Route::Project(tab);
    }

    pub fn open_project_dir_async(
        &mut self,
        dir: PathBuf,
        tab: ProjectTab,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.project_load_generation = self.project_load_generation.wrapping_add(1);
        let load_generation = self.project_load_generation;
        self.active_project_dir = Some(dir.clone());
        self.inspector_open = false;
        self.inspector_func_idx = None;
        self.selected_frame_idx = None;
        self.fn_search.clear();
        self.fn_filter = FnFilter::All;
        self.fn_table_limit = 200;
        self.renaming_project = false;
        self.active_project = None;
        self.load_error.clear();
        self.route = Route::Project(tab);
        cx.notify();

        let repo_root = self.cfg.repo_root.clone();
        let target_dir = dir.clone();
        let bg = cx.background_executor().spawn(async move {
            ProjectModel::load(&target_dir).map(|mut m| {
                m.load_certified_index(&repo_root);
                m
            })
        });
        let weak = cx.entity().downgrade();
        window
            .spawn(cx, async move |cx| {
                let res = bg.await;
                let _ = weak.update(cx, |this, cx| {
                    if this.project_load_generation == load_generation
                        && this.active_project_dir.as_ref() == Some(&dir)
                        && matches!(this.route, Route::Project(_))
                    {
                        this.apply_loaded_project(res);
                        cx.notify();
                    }
                });
            })
            .detach();
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

    pub fn reload_active_project_async(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(dir) = self.active_project_dir.clone() {
            let tab = match self.route {
                Route::Project(t) => t,
                _ => ProjectTab::Overview,
            };
            self.open_project_dir_async(dir, tab, window, cx);
            self.refresh_projects_async(window, cx);
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
                self.push_toast(
                    "Note saved",
                    &format!("Attached note to {addr}"),
                    ok_col,
                    false,
                );
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

    /// Command used for the explicit "AI (web)" route. The saved template may
    /// contain `{endpoint}` / `{model}` placeholders; `{input}` / `{output}`
    /// are left untouched for `readability.py` to fill in.
    pub fn effective_web_ai_cmd(&self) -> Option<String> {
        let template = {
            let trimmed = self.settings.ai_web_cmd.trim();
            if !trimmed.is_empty() {
                trimmed.to_string()
            } else {
                self.cfg
                    .reveng_web_ai_cmd
                    .clone()
                    .unwrap_or_default()
                    .trim()
                    .to_string()
            }
        };
        if template.is_empty() {
            return None;
        }
        let endpoint = self.settings.ai_web_endpoint.trim();
        let model = self.settings.ai_web_model.trim();
        if (template.contains("{endpoint}") && endpoint.is_empty())
            || (template.contains("{model}") && model.is_empty())
        {
            return None;
        }
        Some(
            template
                .replace("{endpoint}", endpoint)
                .replace("{model}", model),
        )
    }

    pub fn save_source_improve_settings(&mut self) {
        let _ = self.settings.save();
        self.push_toast(
            "Source improvement settings saved",
            &format!(
                "rev.ng: {} · web AI: {}",
                if self.settings.revng_bin.trim().is_empty() {
                    "not configured"
                } else {
                    self.settings.revng_bin.trim()
                },
                if self.settings.ai_web_cmd.trim().is_empty() {
                    "not configured"
                } else {
                    "configured"
                }
            ),
            self.tokens().match_fill,
            false,
        );
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
        self.push_toast(
            "Agent command saved",
            &detail,
            self.tokens().match_fill,
            false,
        );
    }

    fn alloc_job_id(&mut self) -> u64 {
        let id = self.next_job_id;
        self.next_job_id += 1;
        id
    }

    pub fn start_picked_pipeline(&mut self, cx: &mut Context<Self>) {
        let opts = self.opts.clone();
        self.enqueue_or_start_pipeline(opts, cx);
        self.reset_dropzone();
        cx.notify();
    }

    pub fn start_active_project_pipeline(&mut self, cx: &mut Context<Self>) {
        let mut opts = self.opts.clone();
        if !prepare_active_project_options(&mut opts, self.active_project_dir.as_deref()) {
            self.push_toast(
                "Missing executable",
                "Set the Original PE executable path in Settings first.",
                self.tokens().not_c,
                true,
            );
            cx.notify();
            return;
        }
        self.enqueue_or_start_pipeline(opts, cx);
    }

    pub fn enqueue_or_start_pipeline(&mut self, opts: PipelineOptions, cx: &mut Context<Self>) {
        self.enqueue_or_start_pipeline_inner(opts);
        cx.notify();
    }

    fn enqueue_or_start_pipeline_inner(&mut self, opts: PipelineOptions) -> bool {
        let project_dir = if !opts.project_dir.trim().is_empty() {
            PathBuf::from(opts.project_dir.trim())
        } else {
            self.default_project_dir_for(Path::new(&opts.pe_file))
        };
        let project_identity = normalized_path(&project_dir);
        if self
            .deleting_projects
            .iter()
            .any(|path| path == &project_identity)
        {
            self.push_toast(
                "Project deletion in progress",
                "Wait for the current delete operation to finish before starting this project.",
                self.tokens().info,
                false,
            );
            return false;
        }
        if !opts.dry_run {
            self.unhide_project_dir(&project_dir);
        }

        // Guard against duplicate concurrent runs targeting the exact same project directory
        let already_active = self
            .jobs
            .iter()
            .any(|j| j.is_active() && j.project_dir.as_ref() == Some(&project_dir))
            || self.queued_pipelines.iter().any(|q| {
                let qdir = if !q.opts.project_dir.trim().is_empty() {
                    PathBuf::from(q.opts.project_dir.trim())
                } else {
                    self.default_project_dir_for(Path::new(&q.opts.pe_file))
                };
                qdir == project_dir
            });
        if already_active {
            let info_col = self.tokens().info;
            let name = project_dir
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("project")
                .to_string();
            self.push_toast(
                "Already active or queued",
                &format!("{name} is already decompiling or waiting in queue."),
                info_col,
                false,
            );
            return false;
        }

        if !self.queue_paused
            && self.queued_batches.is_empty()
            && self.active_slot_count() < self.effective_max_concurrent_jobs()
        {
            self.spawn_pipeline_job(opts);
        } else {
            let id = self.alloc_job_id();
            let name = Path::new(&opts.pe_file)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("game.exe")
                .to_string();
            let target_label = opts.resolved_target_summary();
            let pe_path = opts.pe_file.clone();
            self.queued_pipelines.push(QueuedJob {
                id,
                name: name.clone(),
                pe_path,
                target_label,
                opts,
            });
            let work_col = self.tokens().work;
            self.push_toast(
                "Queued for parallel decompilation",
                &format!(
                    "{name} added to queue ({} waiting)",
                    self.queued_pipelines.len()
                ),
                work_col,
                false,
            );
        }
        true
    }

    fn drain_queued_pipelines(&mut self) {
        if self.queue_paused || !self.queued_batches.is_empty() {
            return;
        }
        let limit = self.effective_max_concurrent_jobs();
        while self.active_slot_count() < limit && !self.queued_pipelines.is_empty() {
            let next = self.queued_pipelines.remove(0);
            self.spawn_pipeline_job(next.opts);
        }
    }

    fn drain_queued_batches(&mut self, cx: &mut Context<Self>) {
        if self.queue_paused
            || !self.deleting_projects.is_empty()
            || self.jobs.iter().any(ActiveJob::is_active)
        {
            return;
        }
        if !self.queued_batches.is_empty() {
            let next = self.queued_batches.remove(0);
            self.start_batch_worker_inner(next.folder, cx, true);
        }
    }

    fn spawn_pipeline_job(&mut self, opts: PipelineOptions) {
        let id = self.alloc_job_id();
        let repo_root = self.cfg.repo_root.clone();
        let project_dir = if !opts.project_dir.trim().is_empty() {
            PathBuf::from(opts.project_dir.trim())
        } else {
            self.default_project_dir_for(Path::new(&opts.pe_file))
        };
        if !opts.dry_run {
            self.unhide_project_dir(&project_dir);
        }
        let capture = opts.capture_screenshots;
        let clean_objects = opts.clean_objects;
        let verify_standalone = opts.verify_standalone;
        let auto_launch_after_build = opts.auto_launch_after_build;
        let auto_ai_readability = opts.improve_readability_with_ai;
        let is_dry_run = opts.dry_run;
        let child_slot: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(None));
        let child_for_thread = Arc::clone(&child_slot);
        let cancel_requested = Arc::new(AtomicBool::new(false));
        let cancel_for_thread = Arc::clone(&cancel_requested);
        let pgid_slot: Arc<Mutex<Option<u32>>> = Arc::new(Mutex::new(None));
        let pgid_for_thread = Arc::clone(&pgid_slot);

        let pe_info = inspect_pe(Path::new(&opts.pe_file));
        let pe_summary = if pe_info.ok {
            format!(
                "{} {} (base 0x{:08X})",
                pe_info.magic, pe_info.machine, pe_info.image_base
            )
        } else {
            String::new()
        };

        let mut backend_args = opts.to_backend_args();
        if !backend_args.iter().any(|a| a == "--no-progress") {
            backend_args.push("--no-progress".to_string());
        }

        let mut py_args = vec!["-m".to_string(), "tools.autorecomp.cli".to_string()];
        py_args.extend(backend_args);

        let file_name = Path::new(&opts.pe_file)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(&opts.pe_file)
            .to_string();
        let target_label = opts.resolved_target_summary();

        let mut live_stages = fresh_live_stages();
        if let Some(first) = live_stages.first_mut() {
            first.state = StageExecState::Running(Instant::now());
        }

        let (tx, rx) = mpsc::channel();
        let job = ActiveJob {
            id,
            kind: WorkerKind::Pipeline,
            name: file_name.clone(),
            pe_path: opts.pe_file.clone(),
            pe_summary,
            target_label: target_label.clone(),
            project_dir: Some(project_dir.clone()),
            built_binary: None,
            has_wasm: false,
            failure_summary: String::new(),
            auto_launch_after_build,
            auto_ai_readability,
            is_dry_run,
            status: JobStatus::Running,
            stage: "inspect".to_string(),
            started: Instant::now(),
            paused_at: None,
            paused_total: Duration::ZERO,
            finished_secs: None,
            current_file: file_name.clone(),
            current_task_detail: format!("Inspecting PE32 headers & targeting {target_label}"),
            compile_pct: None,
            processed_files: vec![ProcessedFileItem {
                action: "PE",
                path: file_name.clone(),
                detail: format!("Input binary -> {target_label}"),
                timestamp: now_hms(),
                stage: "inspect".to_string(),
            }],
            live_stages,
            live_logs: vec![format!("$ {}", opts.format_command_preview())],
            child: child_slot,
            cancel_requested,
            pgid: pgid_slot,
            rx: Some(rx),
        };
        self.jobs.push(job);
        if self.selected_job_id.is_none() || self.jobs.len() == 1 {
            self.selected_job_id = Some(id);
        }
        self.push_feed(
            "pipeline",
            &format!("started {file_name} -> {target_label}"),
            false,
        );

        thread::spawn(move || {
            let mut cmd = Command::new(crate::config::python_interpreter(&repo_root));
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

            let code = run_streaming_child_inner(
                cmd,
                child_for_thread,
                pgid_for_thread,
                cancel_for_thread,
                &tx,
            );

            if !is_dry_run && clean_objects {
                let _ = crate::tasks::clean_cmake_objects(&project_dir);
            }
            let (built_binary, has_wasm) = if !is_dry_run {
                let wasm_ok = project_dir.join("build").join("index.html").is_file()
                    || project_dir.join("index.html").is_file();
                let bin = ProjectModel::load(&project_dir)
                    .ok()
                    .and_then(|m| m.built_binary_path());
                if verify_standalone && (code == 0 || code == 3) {
                    if let Some(ref b) = bin {
                        match crate::tasks::verify_standalone_binary(b, 700) {
                            Ok(msg) => {
                                let _ =
                                    tx.send(WorkerMsg::Line(format!("standalone: PASS ({msg})")));
                            }
                            Err(err) => {
                                let _ =
                                    tx.send(WorkerMsg::Line(format!("standalone: FAIL ({err})")));
                            }
                        }
                    }
                }
                (bin, wasm_ok)
            } else {
                (None, false)
            };

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
                built_binary,
                has_wasm,
                summary,
            });
        });
    }

    pub fn start_readability_agent(&mut self, cx: &mut Context<Self>) {
        let Some(dir) = self.active_project_dir.clone() else {
            return;
        };
        let target_fn = self.agent_target_fn.trim().to_string();
        let target = if target_fn.is_empty() {
            None
        } else {
            Some(target_fn)
        };
        self.start_readability_agent_for_dir(self.source_improve_method, dir, target, cx);
    }

    /// Start a gated source-improvement pass for one project directory.
    ///
    /// Every method uses the same harness: a baseline must already be built and
    /// run, every edit is rebuilt and re-run, and failures are reverted. Only
    /// the provider (`--providers`) and its configured command change.
    pub fn start_readability_agent_for_dir(
        &mut self,
        method: SourceImproveMethod,
        dir: PathBuf,
        target_fn: Option<String>,
        cx: &mut Context<Self>,
    ) {
        let dir_key = normalized_path(&dir);
        if self.deleting_projects.contains(&dir_key)
            || self
                .pending_project_deletions
                .iter()
                .any(|pending| pending.dir == dir || pending.canon == dir_key)
        {
            self.push_toast(
                "Project deletion in progress",
                "Wait for the project deletion to finish before starting readability work.",
                self.tokens().info,
                false,
            );
            cx.notify();
            return;
        }
        let revng_bin = self.settings.revng_bin.trim().to_string();
        let revng_dir = self.settings.revng_dir.trim().to_string();
        let (ai_cmd, web_cmd): (Option<String>, Option<String>) = match method {
            SourceImproveMethod::Revng => {
                let revng_available = !revng_bin.is_empty()
                    || std::env::var_os("REVNG_BIN").is_some_and(|v| !v.is_empty())
                    || std::env::var_os("REVNG_DECOMPILE_CMD").is_some_and(|v| !v.is_empty())
                    || self.cfg.corpus_env.contains_key("REVNG_BIN")
                    || self.cfg.corpus_env.contains_key("REVNG_DECOMPILE_CMD")
                    || crate::config::find_executable("revng").is_some();
                if !revng_available {
                    self.push_toast(
                        "rev.ng not configured",
                        "Set the rev.ng binary and output directory in Settings → Source improvement. No AI fallback will be used.",
                        self.tokens().work,
                        false,
                    );
                    self.route = Route::Settings;
                    cx.notify();
                    return;
                }
                (None, None)
            }
            SourceImproveMethod::AiLocal => {
                let Some(cmd) = self.effective_ai_cmd() else {
                    self.push_toast(
                        "Optional AI agent not configured",
                        "AI is optional and only improves readable C source code. Choose a preset or set REVENG_AI_CMD in Agents & models first.",
                        self.tokens().work,
                        false,
                    );
                    self.route = Route::Agents;
                    cx.notify();
                    return;
                };
                (Some(cmd), None)
            }
            SourceImproveMethod::AiWeb => {
                let Some(cmd) = self.effective_web_ai_cmd() else {
                    self.push_toast(
                        "Web AI not configured",
                        "Set REVENG_WEB_AI_CMD (and any {endpoint}/{model} values) under Settings → Source improvement. Web AI is explicit opt-in only.",
                        self.tokens().work,
                        false,
                    );
                    self.route = Route::Settings;
                    cx.notify();
                    return;
                };
                (None, Some(cmd))
            }
        };
        if self.jobs.iter().any(|j| {
            j.is_active()
                && j.kind == WorkerKind::Readability
                && j.project_dir.as_ref() == Some(&dir)
        }) {
            self.push_toast(
                "AI readability already running",
                "An optional C readability pass is already running for this project.",
                self.tokens().info,
                false,
            );
            cx.notify();
            return;
        }
        let id = self.alloc_job_id();
        let repo_root = self.cfg.repo_root.clone();
        let target_fn = target_fn
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());
        let child_slot: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(None));
        let child_for_thread = Arc::clone(&child_slot);
        let pgid_slot: Arc<Mutex<Option<u32>>> = Arc::new(Mutex::new(None));
        let pgid_for_thread = Arc::clone(&pgid_slot);

        let mut args = vec![
            "-m".to_string(),
            "tools.autorecomp.readability".to_string(),
            dir.display().to_string(),
            "--providers".to_string(),
            method.providers_arg().to_string(),
        ];
        if let Some(ref f) = target_fn {
            args.push("--function".to_string());
            args.push(f.clone());
        }

        let method_label = method.short_label();
        let stage = match target_fn {
            Some(ref f) => format!("C readability [{method_label}] ({f})"),
            None => format!("C source readability [{method_label}]"),
        };
        let proj_name = dir
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("project")
            .to_string();
        let command_prefix = match method {
            SourceImproveMethod::Revng => format!("# source improve: {method_label}"),
            SourceImproveMethod::AiLocal => {
                format!(
                    "# source improve: {method_label}\n$ REVENG_AI_CMD=\"{}\"",
                    ai_cmd.clone().unwrap_or_default()
                )
            }
            SourceImproveMethod::AiWeb => {
                format!("# source improve: {method_label}\n$ REVENG_WEB_AI_CMD=\"{}\"\n$ REVENG_AI_CMD=\"{}\"", web_cmd.clone().unwrap_or_default(), web_cmd.clone().unwrap_or_default())
            }
        };

        let (tx, rx) = mpsc::channel();
        let cancel_requested = Arc::new(AtomicBool::new(false));
        let cancel_for_thread = Arc::clone(&cancel_requested);
        self.jobs.push(ActiveJob {
            id,
            kind: WorkerKind::Readability,
            name: format!("Readability [{method_label}]: {proj_name}"),
            pe_path: String::new(),
            pe_summary: String::new(),
            target_label: stage.clone(),
            project_dir: Some(dir.clone()),
            built_binary: None,
            has_wasm: false,
            failure_summary: String::new(),
            auto_launch_after_build: false,
            auto_ai_readability: false,
            is_dry_run: false,
            status: JobStatus::Running,
            stage,
            started: Instant::now(),
            paused_at: None,
            paused_total: Duration::ZERO,
            finished_secs: None,
            current_file: String::new(),
            current_task_detail: format!(
                "{method_label} pass — improving readable C source code (compile- & evidence-gated)"
            ),
            compile_pct: None,
            processed_files: Vec::new(),
            live_stages: fresh_live_stages(),
            live_logs: vec![format!("{command_prefix}\n$ python3 {}", args.join(" "))],
            child: child_slot,
            cancel_requested,
            pgid: pgid_slot,
            rx: Some(rx),
        });
        self.selected_job_id = Some(id);
        self.push_feed(
            "agent",
            &format!("started {method_label} readability pass on {proj_name}"),
            false,
        );
        cx.notify();

        thread::spawn(move || {
            let mut cmd = Command::new(crate::config::python_interpreter(&repo_root));
            cmd.current_dir(&repo_root)
                .env("PYTHONPATH", &repo_root)
                .args(&args)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            // The readability harness reads REVENG_AI_CMD for every AI route;
            // the web route just supplies its own user-configured command.
            if let Some(cmd_text) = ai_cmd {
                cmd.env("REVENG_AI_CMD", cmd_text);
            }
            if let Some(cmd_text) = web_cmd {
                cmd.env("REVENG_WEB_AI_CMD", &cmd_text)
                    .env("REVENG_AI_CMD", cmd_text);
            }
            if method == SourceImproveMethod::Revng {
                if !revng_bin.is_empty() {
                    cmd.env("REVNG_BIN", &revng_bin);
                }
                if !revng_dir.is_empty() {
                    cmd.env("REVNG_DIR", &revng_dir);
                }
            }
            let code = run_streaming_child_inner(
                cmd,
                child_for_thread,
                pgid_for_thread,
                cancel_for_thread,
                &tx,
            );
            let _ = tx.send(WorkerMsg::Finished {
                kind: WorkerKind::Readability,
                code,
                project_dir: Some(dir),
                built_binary: None,
                has_wasm: false,
                summary: if code == 0 {
                    format!("{method_label} readability pass finished; see reports/readability.html for the gated outcome")
                } else {
                    format!("{method_label} readability pass exited with status {code} (check reports/readability.html)")
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
        let release = self
            .cfg
            .repo_root
            .join("target")
            .join("release")
            .join(cli_name);
        if release.is_file() {
            return release;
        }
        let debug = self
            .cfg
            .repo_root
            .join("target")
            .join("debug")
            .join(cli_name);
        if debug.is_file() {
            return debug;
        }
        PathBuf::from(cli_name)
    }

    pub fn start_batch_worker(&mut self, folder: PathBuf, cx: &mut Context<Self>) {
        self.start_batch_worker_inner(folder, cx, false);
    }

    fn start_batch_worker_inner(
        &mut self,
        folder: PathBuf,
        cx: &mut Context<Self>,
        from_queue: bool,
    ) {
        let folder_id = normalized_path(&folder);
        let already_active = self.jobs.iter().any(|job| {
            job.is_active()
                && job.kind == WorkerKind::Batch
                && normalized_path(Path::new(&job.pe_path)) == folder_id
        });
        let already_queued = self
            .queued_batches
            .iter()
            .any(|queued| normalized_path(&queued.folder) == folder_id);
        if already_active || (!from_queue && already_queued) {
            self.push_toast(
                "Batch already active or queued",
                "This folder already has a batch run in progress or waiting.",
                self.tokens().info,
                false,
            );
            cx.notify();
            return;
        }
        if !from_queue
            && (self.queue_paused
                || !self.deleting_projects.is_empty()
                || !self.queued_batches.is_empty()
                || self.jobs.iter().any(ActiveJob::is_active))
        {
            if !already_queued {
                let id = self.alloc_job_id();
                self.queued_batches.push(QueuedBatch {
                    id,
                    folder: folder.clone(),
                });
            }
            self.push_toast(
                "Batch queued",
                "Batch processing will start when other work finishes and the queue is resumed.",
                self.tokens().work,
                false,
            );
            cx.notify();
            return;
        }
        let id = self.alloc_job_id();
        let exe = self.cli_binary_path();
        let parallel = self.effective_max_concurrent_jobs().max(1);
        let child_slot: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(None));
        let child_for_thread = Arc::clone(&child_slot);
        let cancel_requested = Arc::new(AtomicBool::new(false));
        let cancel_for_thread = Arc::clone(&cancel_requested);
        let pgid_slot: Arc<Mutex<Option<u32>>> = Arc::new(Mutex::new(None));
        let pgid_for_thread = Arc::clone(&pgid_slot);
        let folder_name = folder
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("batch")
            .to_string();

        let (tx, rx) = mpsc::channel();
        self.jobs.push(ActiveJob {
            id,
            kind: WorkerKind::Batch,
            name: format!("Batch: {folder_name}"),
            pe_path: folder.display().to_string(),
            pe_summary: String::new(),
            target_label: format!("{parallel} workers"),
            project_dir: None,
            built_binary: None,
            has_wasm: false,
            failure_summary: String::new(),
            auto_launch_after_build: false,
            auto_ai_readability: false,
            is_dry_run: false,
            status: JobStatus::Running,
            stage: format!("scanning {}", folder.display()),
            started: Instant::now(),
            paused_at: None,
            paused_total: Duration::ZERO,
            finished_secs: None,
            current_file: String::new(),
            current_task_detail: format!("Running batch with {parallel} parallel workers"),
            compile_pct: None,
            processed_files: Vec::new(),
            live_stages: fresh_live_stages(),
            live_logs: vec![format!(
                "$ game-recomp-cli batch {} --parallel {parallel} --verify-standalone",
                folder.display()
            )],
            child: child_slot,
            cancel_requested,
            pgid: pgid_slot,
            rx: Some(rx),
        });
        self.selected_job_id = Some(id);
        self.drop_state = DropState::Idle;
        self.push_feed(
            "batch",
            &format!("scanning {} (parallel={parallel})", folder.display()),
            false,
        );
        cx.notify();

        thread::spawn(move || {
            let mut cmd = Command::new(exe);
            cmd.args([
                "batch",
                &folder.display().to_string(),
                "--parallel",
                &parallel.to_string(),
                "--verify-standalone",
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
            let code = run_streaming_child_inner(
                cmd,
                child_for_thread,
                pgid_for_thread,
                cancel_for_thread,
                &tx,
            );
            let _ = tx.send(WorkerMsg::Finished {
                kind: WorkerKind::Batch,
                code,
                project_dir: None,
                built_binary: None,
                has_wasm: false,
                summary: format!("Batch run finished (exit {code})"),
            });
        });
    }

    fn signal_process_group(job: &ActiveJob, sig: &str) -> bool {
        #[cfg(unix)]
        {
            if let Ok(guard) = job.pgid.lock() {
                if let Some(pgid) = *guard {
                    return Command::new("kill")
                        .args([sig, &format!("-{pgid}")])
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .status()
                        .map(|status| status.success())
                        .unwrap_or(false);
                }
            }
            false
        }
        #[cfg(not(unix))]
        {
            let _ = (job, sig);
            false
        }
    }

    pub fn pause_job(&mut self, job_id: u64) {
        #[cfg(not(unix))]
        {
            let _ = job_id;
            self.push_toast(
                "Pause unsupported on Windows",
                "Process-group pause/resume requires macOS or Linux.",
                self.tokens().info,
                false,
            );
            return;
        }
        #[cfg(unix)]
        {
            let mut paused_name: Option<String> = None;
            let mut signal_failed = false;
            if let Some(job) = self.jobs.iter_mut().find(|j| j.id == job_id) {
                if job.is_running() {
                    if Self::signal_process_group(job, "-STOP") {
                        job.status = JobStatus::Paused;
                        job.paused_at = Some(Instant::now());
                        job.current_task_detail = "Paused by user (SIGSTOP)".to_string();
                        job.live_logs.push("[paused by user]".to_string());
                        paused_name = Some(job.name.clone());
                    } else {
                        signal_failed = true;
                    }
                }
            }
            if let Some(name) = paused_name {
                self.push_feed("pipeline", &format!("paused {name}"), false);
                self.push_toast(
                    "Recompilation paused",
                    &format!("Paused {name} and its child compilers."),
                    self.tokens().work,
                    false,
                );
            }
            if signal_failed {
                self.push_toast(
                    "Pause not available yet",
                    "The worker process group is still starting. Try pausing again in a moment.",
                    self.tokens().info,
                    false,
                );
            }
        }
    }

    pub fn resume_job(&mut self, job_id: u64) {
        let mut resumed_name: Option<String> = None;
        if let Some(job) = self.jobs.iter_mut().find(|j| j.id == job_id) {
            if job.is_paused() {
                if Self::signal_process_group(job, "-CONT") {
                    if let Some(at) = job.paused_at.take() {
                        job.paused_total += at.elapsed();
                    }
                    job.status = JobStatus::Running;
                    job.current_task_detail = format!("Resumed stage {}", job.stage);
                    job.live_logs.push("[resumed by user]".to_string());
                    resumed_name = Some(job.name.clone());
                }
            }
        }
        if let Some(name) = resumed_name {
            self.push_feed("pipeline", &format!("resumed {name}"), false);
            self.push_toast(
                "Recompilation resumed",
                &format!("Resumed {name}."),
                self.tokens().match_fill,
                false,
            );
        }
    }

    pub fn pause_all_jobs(&mut self) {
        #[cfg(not(unix))]
        {
            self.queue_paused = true;
            self.push_toast(
                "Queue paused",
                "Queued jobs held; per-process pause requires macOS or Linux.",
                self.tokens().work,
                false,
            );
            return;
        }
        #[cfg(unix)]
        {
            self.queue_paused = true;
            let mut count = 0usize;
            for job in &mut self.jobs {
                if job.is_running() {
                    if !Self::signal_process_group(job, "-STOP") {
                        continue;
                    }
                    job.status = JobStatus::Paused;
                    job.paused_at = Some(Instant::now());
                    job.current_task_detail = "Paused by user (SIGSTOP)".to_string();
                    job.live_logs.push("[paused by user]".to_string());
                    count += 1;
                }
            }
            self.push_toast(
                "Paused recompilation",
                &format!("Paused {count} running job(s) and held queue."),
                self.tokens().work,
                false,
            );
        }
    }

    pub fn resume_all_jobs(&mut self, cx: &mut Context<Self>) {
        self.queue_paused = false;
        let mut count = 0usize;
        for job in &mut self.jobs {
            if job.is_paused() {
                if !Self::signal_process_group(job, "-CONT") {
                    continue;
                }
                if let Some(at) = job.paused_at.take() {
                    job.paused_total += at.elapsed();
                }
                job.status = JobStatus::Running;
                job.current_task_detail = format!("Resumed stage {}", job.stage);
                job.live_logs.push("[resumed by user]".to_string());
                count += 1;
            }
        }
        self.drain_queued_pipelines();
        self.drain_queued_batches(cx);
        self.push_toast(
            "Resumed recompilation",
            &format!("Resumed {count} paused job(s) and queue."),
            self.tokens().match_fill,
            false,
        );
    }

    pub fn stop_job(&mut self, job_id: u64) {
        if let Some(job) = self.jobs.iter_mut().find(|j| j.id == job_id) {
            job.cancel_requested.store(true, Ordering::SeqCst);
            Self::signal_process_group(job, "-CONT");
            Self::signal_process_group(job, "-KILL");
            #[cfg(unix)]
            if let Ok(mut guard) = job.child.lock() {
                if let Some(ref mut child) = *guard {
                    let _ = child.kill();
                }
            }
            if job.is_active() {
                job.live_logs.push("[stop requested]".to_string());
            }
        }
        self.push_toast(
            "Stop requested",
            "The worker will leave the queue after its process exits.",
            self.tokens().work,
            false,
        );
        self.drain_queued_pipelines();
    }

    pub fn stop_all_jobs(&mut self) {
        self.queue_paused = false;
        for job in &mut self.jobs {
            if job.is_active() {
                Self::signal_process_group(job, "-CONT");
                job.cancel_requested.store(true, Ordering::SeqCst);
                Self::signal_process_group(job, "-KILL");
                #[cfg(unix)]
                if let Ok(mut guard) = job.child.lock() {
                    if let Some(ref mut child) = *guard {
                        let _ = child.kill();
                    }
                }
                job.live_logs.push("[stop requested]".to_string());
            }
        }
        let cleared_q = self.queued_pipelines.len() + self.queued_batches.len();
        self.queued_pipelines.clear();
        self.queued_batches.clear();
        self.push_toast(
            "Stopped all jobs",
            &format!("Stopped active jobs and cleared {cleared_q} queued item(s)."),
            self.tokens().work,
            false,
        );
    }

    pub fn remove_queued_job(&mut self, job_id: u64) {
        self.queued_pipelines.retain(|q| q.id != job_id);
    }

    pub fn remove_queued_batch(&mut self, id: u64) {
        self.queued_batches.retain(|queued| queued.id != id);
    }

    pub fn clear_finished_jobs(&mut self) {
        self.jobs.retain(|j| j.is_active());
        if let Some(sel) = self.selected_job_id {
            if !self.jobs.iter().any(|j| j.id == sel) {
                self.selected_job_id = self.jobs.first().map(|j| j.id);
            }
        }
    }

    pub fn clear_job_logs(&mut self, job_id: u64) {
        if let Some(j) = self.jobs.iter_mut().find(|j| j.id == job_id) {
            j.live_logs.clear();
        }
    }

    /// Resolve the game-data root for a native launch when the user has not
    /// picked one explicitly.
    ///
    /// The Python `run` stage starts the rebuilt binary with `cwd` set to
    /// `--assets-dir` (or the original PE's parent by default), and the runtime
    /// reads the same root from `RECOMP_ASSETS_DIR` or a single directory
    /// argument. Without one, `boot_embedded(NULL, ...)` leaves the guest
    /// unable to find its own data files, so a launch from `project/build/`
    /// looks like a failed native build even though the binary is fine.
    /// Prefer an explicitly configured assets dir, then the PE paths the GUI
    /// already knows about (picked file, active job, loaded project).
    pub fn native_launch_assets_root<'a>(
        configured_assets_dir: &str,
        pe_paths: impl IntoIterator<Item = &'a Path>,
    ) -> Option<PathBuf> {
        let configured = Path::new(configured_assets_dir.trim());
        if !configured.as_os_str().is_empty() && configured.is_dir() {
            return Some(normalized_path(configured));
        }
        for pe in pe_paths {
            let Some(parent) = pe.parent() else {
                continue;
            };
            if parent.as_os_str().is_empty() {
                continue;
            }
            let root = normalized_path(parent);
            if root.is_dir() {
                return Some(root);
            }
        }
        None
    }

    pub fn launch_rebuilt_binary(&mut self, bin: &Path) {
        let project_dir = bin.parent().and_then(Path::parent);
        let project_pe = project_dir.and_then(read_project_pe_path);
        let job_pe = project_dir.and_then(|dir| {
            self.jobs
                .iter()
                .rev()
                .find(|job| job.project_dir.as_deref() == Some(dir))
                .map(|job| Path::new(job.pe_path.as_str()))
        });
        let mut pe_paths: Vec<&Path> = Vec::new();
        // The binary's own project is the most reliable hint; a stale picked
        // file or an unrelated active job must not win over it.
        if let Some(pe) = project_pe.as_deref() {
            pe_paths.push(Path::new(pe));
        }
        if let Some(pe) = job_pe {
            pe_paths.push(pe);
        }
        if let Some(pe) = self.active_project.as_ref() {
            pe_paths.push(Path::new(pe.pe_path.trim()));
        }
        pe_paths.push(self.picked_pe.as_path());
        pe_paths.push(Path::new(self.opts.pe_file.trim()));
        let assets_root = Self::native_launch_assets_root(&self.opts.assets_dir, pe_paths);
        self.launch_rebuilt_binary_with_data(bin, assets_root.as_deref());
    }

    pub fn launch_rebuilt_binary_with_data(&mut self, bin: &Path, data_dir: Option<&Path>) {
        if !bin.is_file() {
            self.push_toast(
                "Missing executable",
                &format!("Built binary not found: {}", bin.display()),
                self.tokens().not_c,
                true,
            );
            return;
        }
        let bin = fs::canonicalize(bin).unwrap_or_else(|_| bin.to_path_buf());
        let mut cmd = Command::new(&bin);
        if let Some(dir) = data_dir {
            if !dir.is_dir() {
                self.push_toast(
                    "Invalid game data folder",
                    &format!("Directory does not exist: {}", dir.display()),
                    self.tokens().not_c,
                    true,
                );
                return;
            }
            let dir = fs::canonicalize(dir).unwrap_or_else(|_| dir.to_path_buf());
            cmd.current_dir(&dir)
                .env("RECOMP_ASSETS_DIR", &dir)
                .arg(&dir);
        } else if let Some(parent) = bin.parent() {
            cmd.current_dir(parent);
        }
        match cmd.spawn() {
            Ok(_) => {
                if let Some(dir) = data_dir {
                    self.push_toast(
                        "Launched with game data",
                        &format!("{} (data: {})", bin.display(), dir.display()),
                        self.tokens().match_fill,
                        false,
                    );
                } else {
                    self.push_toast(
                        "Launched standalone",
                        &format!("Started {}", bin.display()),
                        self.tokens().match_fill,
                        false,
                    );
                }
            }
            Err(e) => {
                self.push_toast("Launch failed", &e.to_string(), self.tokens().not_c, true);
            }
        }
    }

    pub fn launch_project_dir_smart(&mut self, dir: &Path, force_standalone: bool) {
        let meta = load_ui_meta(dir);
        let has_wasm = project_has_wasm(dir);
        if !force_standalone && has_wasm && crate::model::manifest_prefers_wasm(dir) {
            self.launch_or_open_wasm(dir);
            return;
        }
        let bin = find_project_binary(dir);
        if let Some(b) = bin {
            if !force_standalone
                && meta.launch_with_data_dir
                && !meta.game_data_dir.trim().is_empty()
            {
                let data_path = normalized_path(Path::new(meta.game_data_dir.trim()));
                self.launch_rebuilt_binary_with_data(&b, Some(&data_path));
            } else {
                // No explicit data folder: infer the original PE's directory
                // instead of starting the guest in project/build/, where it
                // cannot find its own data files.
                self.launch_rebuilt_binary(&b);
            }
        } else if has_wasm {
            self.launch_or_open_wasm(dir);
        } else {
            self.push_toast(
                "No built executable",
                "Run the recompilation pipeline first to generate a runnable binary.",
                self.tokens().work,
                false,
            );
        }
    }

    pub fn open_run_game_modal(
        &mut self,
        dir: PathBuf,
        name: String,
        preselect_data_dir: bool,
        cx: &mut Context<Self>,
    ) {
        let meta = load_ui_meta(&dir);
        let binary_path = find_project_binary(&dir);
        let has_wasm = project_has_wasm(&dir);
        let suggested_pe_dir = read_project_pe_path(&dir)
            .and_then(|pe| PathBuf::from(pe).parent().map(|p| p.to_path_buf()))
            .filter(|p| p.is_dir())
            .map(|p| normalized_path(&p).display().to_string())
            .unwrap_or_default();
        let data_dir = if !meta.game_data_dir.trim().is_empty() {
            meta.game_data_dir.clone()
        } else {
            suggested_pe_dir.clone()
        };
        let use_data_dir = (binary_path.is_some() || !has_wasm)
            && (preselect_data_dir || meta.launch_with_data_dir);
        self.context_menu = None;
        self.run_game_modal = Some(RunGameModal {
            project_dir: dir,
            project_name: name,
            binary_path,
            has_wasm,
            use_data_dir,
            data_dir,
            suggested_pe_dir,
            save_as_default: true,
        });
        if use_data_dir {
            self.focused_input = Some(FocusInput::RunGameDataDir);
        }
        cx.notify();
    }

    pub fn confirm_run_game_modal(&mut self, launch_now: bool, cx: &mut Context<Self>) {
        let Some(modal) = self.run_game_modal.clone() else {
            return;
        };
        let trimmed_data = modal.data_dir.trim().to_string();
        let resolved_data = if trimmed_data.is_empty() {
            if modal.use_data_dir && launch_now {
                self.push_toast(
                    "Select a valid game folder",
                    "Choose an existing game data/assets directory before launching.",
                    self.tokens().not_c,
                    true,
                );
                cx.notify();
                return;
            }
            None
        } else {
            let candidate = normalized_path(Path::new(&trimmed_data));
            if modal.use_data_dir && launch_now && !candidate.is_dir() {
                self.push_toast(
                    "Select a valid game folder",
                    "Choose an existing game data/assets directory before launching.",
                    self.tokens().not_c,
                    true,
                );
                cx.notify();
                return;
            }
            Some(candidate)
        };
        if modal.use_data_dir && modal.has_wasm && modal.binary_path.is_none() {
            self.push_toast(
                "Game data folder unavailable",
                "Data-folder launch is supported for native builds; this project currently has a browser build.",
                self.tokens().not_c,
                true,
            );
            cx.notify();
            return;
        }
        if modal.save_as_default || !launch_now {
            let mut meta = load_ui_meta(&modal.project_dir);
            meta.game_data_dir = resolved_data
                .as_ref()
                .map(|p| p.display().to_string())
                .unwrap_or_default();
            if modal.binary_path.is_some() || !modal.has_wasm {
                meta.launch_with_data_dir = modal.use_data_dir;
            }
            if let Err(error) = save_ui_meta_at(&modal.project_dir, &meta) {
                self.push_toast(
                    "Could not save launch options",
                    &error,
                    self.tokens().not_c,
                    true,
                );
                if !launch_now {
                    cx.notify();
                    return;
                }
            } else if self.active_project_dir.as_ref() == Some(&modal.project_dir) {
                if let Some(ref mut proj) = self.active_project {
                    proj.ui_meta.game_data_dir = resolved_data
                        .as_ref()
                        .map(|p| p.display().to_string())
                        .unwrap_or_default();
                    if modal.binary_path.is_some() || !modal.has_wasm {
                        proj.ui_meta.launch_with_data_dir = modal.use_data_dir;
                    }
                }
            }
            if !launch_now {
                let browser_only = modal.has_wasm && modal.binary_path.is_none();
                self.push_toast(
                    "Run options saved",
                    if browser_only {
                        "Saved the available game data folder settings. Browser launch remains the default."
                    } else if modal.use_data_dir {
                        "Saved default launch mode: Game Data Folder."
                    } else {
                        "Saved default launch mode: Standalone Direct."
                    },
                    self.tokens().match_fill,
                    false,
                );
            }
        }
        if launch_now {
            if modal.use_data_dir {
                if let Some(ref bin) = modal.binary_path {
                    self.launch_rebuilt_binary_with_data(bin, resolved_data.as_deref());
                } else if modal.has_wasm {
                    self.launch_or_open_wasm(&modal.project_dir);
                } else {
                    self.push_toast(
                        "No built executable",
                        "Build this project before launching.",
                        self.tokens().work,
                        false,
                    );
                }
            } else if let Some(ref bin) = modal.binary_path {
                self.launch_rebuilt_binary_with_data(bin, None);
            } else if modal.has_wasm {
                self.launch_or_open_wasm(&modal.project_dir);
            } else {
                self.push_toast(
                    "No built executable",
                    "Build this project before launching.",
                    self.tokens().work,
                    false,
                );
            }
        }
        self.run_game_modal = None;
        if self.focused_input == Some(FocusInput::RunGameDataDir) {
            self.focused_input = None;
        }
        cx.notify();
    }

    pub fn close_run_game_modal(&mut self, cx: &mut Context<Self>) {
        self.run_game_modal = None;
        if self.focused_input == Some(FocusInput::RunGameDataDir) {
            self.focused_input = None;
        }
        cx.notify();
    }

    pub fn open_project_context_menu(
        &mut self,
        dir: PathBuf,
        name: String,
        pos: Point<Pixels>,
        cx: &mut Context<Self>,
    ) {
        self.show_new_menu = false;
        self.show_theme_menu = false;
        self.show_notif_panel = false;
        self.context_menu = Some(ProjectContextMenu {
            dir,
            name,
            x: pos.x.into(),
            y: pos.y.into(),
        });
        cx.notify();
    }

    pub fn close_project_context_menu(&mut self, cx: &mut Context<Self>) {
        self.context_menu = None;
        cx.notify();
    }

    pub fn open_rename_project_modal(
        &mut self,
        dir: PathBuf,
        current_name: String,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.context_menu = None;
        self.rename_modal = Some(RenameProjectModal {
            dir,
            name_buf: current_name,
        });
        self.set_focus_input(Some(FocusInput::QuickRenameProject), window, cx);
    }

    pub fn commit_quick_rename_project(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(modal) = self.rename_modal.take() else {
            return;
        };
        let trimmed = modal.name_buf.trim().to_string();
        if !trimmed.is_empty() {
            let mut meta = load_ui_meta(&modal.dir);
            meta.display_name = trimmed.clone();
            match save_ui_meta_at(&modal.dir, &meta) {
                Ok(_) => {
                    if self.active_project_dir.as_ref() == Some(&modal.dir) {
                        if let Some(ref mut m) = self.active_project {
                            m.ui_meta.display_name = trimmed.clone();
                        }
                    }
                    self.push_toast(
                        "Project renamed",
                        &format!("Updated project title to \"{trimmed}\"."),
                        self.tokens().match_fill,
                        false,
                    );
                }
                Err(e) => {
                    self.push_toast("Rename failed", &e, self.tokens().not_c, true);
                }
            }
            self.refresh_projects_async(window, cx);
        }
        if self.focused_input == Some(FocusInput::QuickRenameProject) {
            self.focused_input = None;
        }
        cx.notify();
    }

    pub fn close_rename_project_modal(&mut self, cx: &mut Context<Self>) {
        self.rename_modal = None;
        if self.focused_input == Some(FocusInput::QuickRenameProject) {
            self.focused_input = None;
        }
        cx.notify();
    }

    pub fn open_delete_project_modal(
        &mut self,
        dir: PathBuf,
        name: String,
        cx: &mut Context<Self>,
    ) {
        self.context_menu = None;
        self.confirm_delete = Some(ConfirmDeleteProject {
            dir,
            name,
            delete_from_disk: false,
        });
        cx.notify();
    }

    pub fn close_delete_project_modal(&mut self, cx: &mut Context<Self>) {
        self.confirm_delete = None;
        cx.notify();
    }

    pub fn execute_delete_project(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(req) = self.confirm_delete.take() else {
            return;
        };
        let dir = req.dir.clone();
        let name = req.name.clone();
        let canon = normalized_path(&dir);
        if req.delete_from_disk && self.deleting_projects.contains(&canon) {
            self.push_toast(
                "Project deletion already running",
                "This project is already being removed from disk.",
                self.tokens().info,
                false,
            );
            cx.notify();
            return;
        }
        if req.delete_from_disk
            && !(dir.join("project.json").is_file()
                || valid_project_manifest(&dir)
                || dir.join("reports/report.json").is_file()
                || dir.join("reports/functions.json").is_file()
                || dir.join("reports/inspect.json").is_file())
        {
            self.push_toast(
                "Refused unsafe delete",
                "This folder has no GameReComp project marker, so it was left untouched.",
                self.tokens().not_c,
                true,
            );
            cx.notify();
            return;
        }
        if req.delete_from_disk {
            let root = self.projects_root();
            let root_canon = fs::canonicalize(&root).unwrap_or(root);
            let repo_canon = fs::canonicalize(&self.cfg.repo_root)
                .unwrap_or_else(|_| self.cfg.repo_root.clone());
            let home_canon = std::env::var_os("HOME")
                .or_else(|| std::env::var_os("USERPROFILE"))
                .map(PathBuf::from)
                .map(|p| fs::canonicalize(&p).unwrap_or(p));
            let safe = !dir.as_os_str().is_empty()
                && canon != Path::new("/")
                && canon.components().count() >= 3
                && canon != root_canon
                && canon != repo_canon
                && home_canon.as_ref().map(|h| h != &canon).unwrap_or(true);
            if !safe {
                self.push_toast(
                    "Refused unsafe delete",
                    "Safety check blocked deleting a root or home directory.",
                    self.tokens().not_c,
                    true,
                );
                cx.notify();
                return;
            }
        }
        let selected_is_symlink = fs::symlink_metadata(&dir)
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(false);
        let delete_identity = if req.delete_from_disk {
            match file_identity(&dir) {
                Ok(identity) => Some(identity),
                Err(error) => {
                    self.push_toast(
                        "Could not verify project directory",
                        &error.to_string(),
                        self.tokens().not_c,
                        true,
                    );
                    cx.notify();
                    return;
                }
            }
        } else {
            None
        };
        let queued = std::mem::take(&mut self.queued_pipelines);
        self.queued_pipelines = queued
            .into_iter()
            .filter(|q| {
                let qdir = if !q.opts.project_dir.trim().is_empty() {
                    PathBuf::from(q.opts.project_dir.trim())
                } else {
                    self.default_project_dir_for(Path::new(&q.opts.pe_file))
                };
                if selected_is_symlink {
                    qdir != dir
                } else {
                    normalized_path(&qdir) != canon
                }
            })
            .collect();
        if selected_is_symlink {
            self.queued_batches.retain(|queued| queued.folder != dir);
        } else {
            self.queued_batches
                .retain(|queued| normalized_path(&queued.folder) != canon);
        }
        let active_ids: Vec<u64> = self
            .jobs
            .iter()
            .filter(|j| {
                j.is_active()
                    && j.project_dir
                        .as_ref()
                        .map(|p| {
                            if selected_is_symlink {
                                p == &dir
                            } else {
                                normalized_path(p) == canon
                            }
                        })
                        .unwrap_or(false)
            })
            .map(|j| j.id)
            .collect();
        for jid in active_ids {
            self.stop_job(jid);
        }

        let dir_str = dir.display().to_string();
        let custom_aliases: Vec<String> = self
            .settings
            .custom_projects
            .iter()
            .filter(|entry| {
                if selected_is_symlink {
                    Path::new(entry) == dir.as_path()
                } else {
                    normalized_path(Path::new(entry)) == canon
                }
            })
            .cloned()
            .collect();

        let active_points_to_deleted = self.active_project_dir.as_ref().map(|active| {
            if selected_is_symlink {
                active == &dir
            } else {
                normalized_path(active) == canon
            }
        }) == Some(true);
        if active_points_to_deleted {
            self.project_load_generation = self.project_load_generation.wrapping_add(1);
            self.active_project_dir = None;
            self.active_project = None;
            self.load_error.clear();
            self.inspector_open = false;
            self.route = Route::Home;
        }

        if req.delete_from_disk {
            self.deleting_projects.push(canon.clone());
            self.pending_project_deletions.push(PendingProjectDelete {
                dir,
                name,
                canon,
                identity: delete_identity.expect("identity recorded for disk deletion"),
                custom_aliases,
                selected_is_symlink,
            });
            self.dispatch_pending_project_deletions(window, cx);
        } else {
            if !self.settings.hidden_projects.contains(&dir_str) {
                self.settings.hidden_projects.push(dir_str);
            }
            let _ = self.settings.save();
            self.push_toast(
                "Removed from project list",
                &format!("Hid {name} from the sidebar (files kept on disk)."),
                self.tokens().info,
                false,
            );
            self.refresh_projects_async(window, cx);
        }
        cx.notify();
    }

    fn dispatch_pending_project_deletions(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let mut index = 0;
        while index < self.pending_project_deletions.len() {
            let pending = &self.pending_project_deletions[index];
            let blocked = self.jobs.iter().any(|job| {
                if !job.is_active() {
                    return false;
                }
                if job.kind == WorkerKind::Batch {
                    return true;
                }
                job.project_dir.as_ref().is_some_and(|path| {
                    if pending.selected_is_symlink {
                        path == &pending.dir
                    } else {
                        normalized_path(path) == pending.canon
                    }
                })
            });
            if blocked {
                index += 1;
                continue;
            }
            let pending = self.pending_project_deletions.remove(index);
            let path_to_remove = pending.dir.clone();
            let expected_identity = pending.identity;
            let bg = cx.background_executor().spawn(async move {
                remove_project_path(&path_to_remove, expected_identity)
                    .map_err(|error| error.to_string())
            });
            let weak = cx.entity().downgrade();
            window
                .spawn(cx, async move |cx| {
                    let result = bg.await;
                    let _ = cx.update(|window, app| {
                        if let Some(entity) = weak.upgrade() {
                            entity.update(app, |this, cx| {
                                match result {
                                    Ok(()) => {
                                        this.settings.custom_projects.retain(|candidate| {
                                            !pending.custom_aliases.contains(candidate)
                                        });
                                        if let Err(error) = this.settings.save() {
                                            this.push_toast(
                                                "Project deleted; settings save failed",
                                                &error.to_string(),
                                                this.tokens().not_c,
                                                true,
                                            );
                                        }
                                        this.push_toast(
                                            "Project deleted from disk",
                                            &format!("Deleted {} permanently.", pending.name),
                                            this.tokens().work,
                                            false,
                                        );
                                    }
                                    Err(error) => this.push_toast(
                                        "Delete failed",
                                        &error,
                                        this.tokens().not_c,
                                        true,
                                    ),
                                }
                                this.deleting_projects.retain(|path| path != &pending.canon);
                                this.refresh_projects_async(window, cx);
                                this.dispatch_pending_project_deletions(window, cx);
                                cx.notify();
                            });
                        }
                    });
                })
                .detach();
        }
    }

    pub fn rerun_project_by_dir(&mut self, dir: PathBuf, cx: &mut Context<Self>) {
        self.context_menu = None;
        let pe_opt = if self.active_project_dir.as_ref() == Some(&dir) {
            self.active_project
                .as_ref()
                .map(|m| m.pe_path.clone())
                .filter(|s| !s.trim().is_empty())
        } else {
            None
        }
        .or_else(|| read_project_pe_path(&dir));

        if let Some(pe) = pe_opt {
            let mut opts = self.opts.clone();
            opts.pe_file = pe;
            opts.project_dir = dir.display().to_string();
            opts.dry_run = false;
            self.enqueue_or_start_pipeline(opts, cx);
        } else {
            self.push_toast(
                "Executable path not found",
                "Open Project Settings to set the original PE executable path.",
                self.tokens().not_c,
                true,
            );
            cx.notify();
        }
    }

    pub fn launch_or_open_wasm(&mut self, project_dir: &Path) {
        let requested_dir =
            fs::canonicalize(project_dir).unwrap_or_else(|_| project_dir.to_path_buf());
        if self.wasm_server_dir.as_ref() == Some(&requested_dir) {
            let status = self
                .wasm_server_child
                .lock()
                .ok()
                .and_then(|mut server| server.as_mut().map(|child| child.try_wait()));
            if matches!(status, Some(Ok(None))) {
                if self.wasm_server_ready.load(Ordering::SeqCst) {
                    if let Some(ref url) = self.wasm_server_url {
                        let _ = open::that(url);
                    }
                }
                return;
            }
            if let Ok(mut server) = self.wasm_server_child.lock() {
                server.take();
            }
            if let Some(path) = self.wasm_server_ready_file.take() {
                let _ = fs::remove_file(path);
            }
            self.wasm_server_url = None;
            self.wasm_server_dir = None;
        }
        let exe = self.cli_binary_path();
        let port = 8087u16;
        let index_path = if project_dir.join("index.html").is_file() {
            project_dir.join("index.html")
        } else {
            project_dir.join("build/index.html")
        };
        if let Err(error) = fs::metadata(&index_path) {
            self.push_toast(
                "WASM app is missing",
                &error.to_string(),
                self.tokens().not_c,
                true,
            );
            return;
        }
        if self.wasm_server_dir.is_some() {
            if let Ok(_open_guard) = self.wasm_server_open_lock.lock() {
                self.wasm_server_generation.fetch_add(1, Ordering::SeqCst);
            }
            self.wasm_server_ready.store(false, Ordering::SeqCst);
            if let Ok(mut server) = self.wasm_server_child.lock() {
                if let Some(mut child) = server.take() {
                    stop_wasm_server(&mut child);
                }
            }
            if let Some(path) = self.wasm_server_ready_file.take() {
                let _ = fs::remove_file(path);
            }
            self.wasm_server_url = None;
            self.wasm_server_dir = None;
        }
        let generation = self.wasm_server_generation.fetch_add(1, Ordering::SeqCst) + 1;
        self.wasm_server_ready.store(false, Ordering::SeqCst);
        if let Ok(mut error) = self.wasm_server_startup_error.lock() {
            *error = None;
        }
        let ready_file = std::env::temp_dir().join(format!(
            "gamerecomp-wasm-ready-{}-{generation}-{}.tmp",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        let _ = fs::remove_file(&ready_file);
        let mut command = Command::new(exe);
        command.args([
            "serve",
            &project_dir.display().to_string(),
            "--port",
            &port.to_string(),
        ]);
        command.arg("--ready-file").arg(&ready_file);
        command.stdout(Stdio::null()).stderr(Stdio::null());
        #[cfg(unix)]
        command.process_group(0);
        match command.spawn() {
            Ok(child) => {
                if let Ok(mut g) = self.wasm_server_child.lock() {
                    if let Some(mut prev) = g.take() {
                        stop_wasm_server(&mut prev);
                    }
                    *g = Some(child);
                }
                let url = format!("http://127.0.0.1:{port}/index.html");
                self.wasm_server_url = Some(url.clone());
                self.wasm_server_dir = Some(requested_dir);
                let server_child = Arc::clone(&self.wasm_server_child);
                let generation_state = Arc::clone(&self.wasm_server_generation);
                let open_lock = Arc::clone(&self.wasm_server_open_lock);
                let ready_state = Arc::clone(&self.wasm_server_ready);
                let startup_error = Arc::clone(&self.wasm_server_startup_error);
                let watch_ready_file = Some(ready_file);
                self.wasm_server_ready_file = watch_ready_file.clone();
                let browser_url = url.clone();
                let failed_ready_file = watch_ready_file.clone();
                thread::spawn(move || {
                    for _ in 0..50 {
                        if generation_state.load(Ordering::SeqCst) != generation {
                            return;
                        }
                        let is_ready = watch_ready_file.as_ref().is_some_and(|path| path.is_file());
                        if is_ready {
                            if let Ok(_open_guard) = open_lock.lock() {
                                if generation_state.load(Ordering::SeqCst) == generation {
                                    ready_state.store(true, Ordering::SeqCst);
                                    let _ = open::that(&browser_url);
                                }
                            }
                            if let Some(path) = watch_ready_file.as_ref() {
                                let _ = fs::remove_file(path);
                            }
                            return;
                        }
                        let exited = server_child
                            .lock()
                            .ok()
                            .and_then(|mut server| server.as_mut().map(|child| child.try_wait()))
                            .map(|status| matches!(status, Ok(Some(_))))
                            .unwrap_or(true);
                        if exited {
                            if generation_state.load(Ordering::SeqCst) == generation {
                                if let Ok(mut error) = startup_error.lock() {
                                    if generation_state.load(Ordering::SeqCst) == generation {
                                        *error = Some((
                                            generation,
                                            "The web server exited before binding its port.".into(),
                                            failed_ready_file,
                                        ));
                                    }
                                }
                            }
                            return;
                        }
                        thread::sleep(Duration::from_millis(100));
                    }
                    if generation_state.load(Ordering::SeqCst) == generation {
                        if let Ok(mut error) = startup_error.lock() {
                            if generation_state.load(Ordering::SeqCst) == generation {
                                *error = Some((generation, "The web server did not report a successful bind within five seconds.".into(), failed_ready_file));
                            }
                        }
                    }
                });
                self.push_toast(
                    "WASM server started",
                    &format!("Serving at {url}"),
                    self.tokens().match_fill,
                    false,
                );
            }
            Err(e) => {
                self.push_toast(
                    "WASM serve failed",
                    &e.to_string(),
                    self.tokens().not_c,
                    true,
                );
            }
        }
    }

    pub fn open_issue_for_job(&mut self, job_id: u64, cx: &mut Context<Self>) {
        let Some(job) = self.jobs.iter().find(|j| j.id == job_id) else {
            return;
        };
        let failed_stage = job
            .live_stages
            .iter()
            .find(|s| matches!(s.state, StageExecState::Failed))
            .map(|s| s.name.to_string())
            .unwrap_or_else(|| job.stage.clone());
        let exit_code = match job.status {
            JobStatus::Failed(c) => Some(c),
            _ => None,
        };
        let err_raw = if !job.failure_summary.is_empty() {
            job.failure_summary.clone()
        } else {
            job.current_task_detail.clone()
        };
        let tail: String = job
            .live_logs
            .iter()
            .rev()
            .take(25)
            .cloned()
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .map(|l| sanitize_home_path(&l))
            .collect::<Vec<_>>()
            .join("\n");
        let pe_summary = if job.pe_summary.trim().is_empty() {
            "PE32 i386".to_string()
        } else {
            job.pe_summary.clone()
        };
        let err_sanitized = sanitize_home_path(&err_raw);
        let title_hint = if !failed_stage.is_empty() {
            format!("failed in stage {failed_stage}")
        } else if let Some(c) = exit_code {
            format!("exited with code {c}")
        } else {
            "support request".to_string()
        };
        self.issue_draft = IssueDraft {
            game_name: job.name.clone(),
            title: format!("[Support Request] {}: {title_hint}", job.name),
            pe_summary,
            target_summary: job.target_label.clone(),
            failed_stage,
            exit_code,
            error_summary: err_sanitized,
            log_tail: tail,
            user_notes: String::new(),
        };
        self.show_issue_modal = true;
        self.focused_input = Some(FocusInput::IssueTitle);
        cx.notify();
    }

    pub fn open_issue_for_picked_pe(&mut self, cx: &mut Context<Self>) {
        let game_name = self
            .picked_pe
            .file_name()
            .and_then(|n| n.to_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("game.exe")
            .to_string();
        let pe_summary = if !self.picked_pe_info.magic.is_empty() {
            format!(
                "{} {} (base 0x{:08X})",
                self.picked_pe_info.magic,
                self.picked_pe_info.machine,
                self.picked_pe_info.image_base
            )
        } else {
            "Unknown executable".to_string()
        };
        let err = if !self.picked_pe_info.error.trim().is_empty() {
            sanitize_home_path(&self.picked_pe_info.error)
        } else if !self.load_error.trim().is_empty() {
            sanitize_home_path(&self.load_error)
        } else {
            "Executable was rejected by PE32 preflight check.".to_string()
        };
        self.issue_draft = IssueDraft {
            game_name: game_name.clone(),
            title: format!("[Support Request] {game_name}: unsupported PE / preflight failure"),
            pe_summary,
            target_summary: self.opts.resolved_target_summary(),
            failed_stage: "inspect".to_string(),
            exit_code: None,
            error_summary: err,
            log_tail: String::new(),
            user_notes: String::new(),
        };
        self.show_issue_modal = true;
        self.focused_input = Some(FocusInput::IssueTitle);
        cx.notify();
    }

    pub fn open_issue_for_project(&mut self, cx: &mut Context<Self>) {
        if let Some(dir) = self.active_project_dir.clone() {
            if let Some(job) = self.job_for_project(&dir) {
                if matches!(job.status, JobStatus::Failed(_) | JobStatus::Stopped) {
                    let id = job.id;
                    self.open_issue_for_job(id, cx);
                    return;
                }
            }
        }
        let (game_name, pe_summary, target_summary, failed_stage, err_summary, tail) = if let Some(
            ref m,
        ) =
            self.active_project
        {
            let stage = m
                .stages
                .iter()
                .find(|s| {
                    s.status.eq_ignore_ascii_case("failed") || s.status.eq_ignore_ascii_case("fail")
                })
                .map(|s| s.name.clone())
                .unwrap_or_else(|| "verify".to_string());
            let err = if !self.load_error.trim().is_empty() {
                sanitize_home_path(&self.load_error)
            } else {
                format!(
                        "Verification '{}' (score {}%, {}/{} verified, build_passed={}, boot_passed={}, unresolved_imports={})",
                        m.verification,
                        m.score,
                        m.verified_count(),
                        m.all_functions.len(),
                        m.diag.build_passed,
                        m.diag.boot_passed,
                        m.diag.unresolved_import_count
                    )
            };
            let stage_log: String = m
                .stages
                .iter()
                .map(|s| sanitize_home_path(&format!("{}: {} ({})", s.name, s.status, s.detail)))
                .collect::<Vec<_>>()
                .join("\n");
            (
                m.display_title().to_string(),
                format!(
                    "{} {} · {} functions · {} imports",
                    if m.inspect.pe_type.is_empty() {
                        "PE32"
                    } else {
                        &m.inspect.pe_type
                    },
                    if m.inspect.machine.is_empty() {
                        "x86"
                    } else {
                        &m.inspect.machine
                    },
                    m.all_functions.len(),
                    m.inspect.import_count
                ),
                format!("{}/{}", m.target_os, m.target_arch),
                stage,
                err,
                stage_log,
            )
        } else {
            let name = self
                .active_project_dir
                .as_ref()
                .and_then(|d| d.file_name())
                .and_then(|n| n.to_str())
                .unwrap_or("project")
                .to_string();
            (
                name,
                "Unknown PE".to_string(),
                self.opts.resolved_target_summary(),
                "load".to_string(),
                sanitize_home_path(&self.load_error),
                String::new(),
            )
        };
        self.issue_draft = IssueDraft {
            game_name: game_name.clone(),
            title: format!("[Support Request] {game_name}: {failed_stage} issue"),
            pe_summary,
            target_summary,
            failed_stage,
            exit_code: None,
            error_summary: err_summary,
            log_tail: tail,
            user_notes: String::new(),
        };
        self.show_issue_modal = true;
        self.focused_input = Some(FocusInput::IssueTitle);
        cx.notify();
    }

    pub fn close_issue_modal(&mut self, cx: &mut Context<Self>) {
        self.show_issue_modal = false;
        if matches!(
            self.focused_input,
            Some(FocusInput::IssueTitle | FocusInput::IssueNotes)
        ) {
            self.focused_input = None;
        }
        cx.notify();
    }

    pub fn format_issue_markdown(&self) -> String {
        let d = &self.issue_draft;
        let exit_str = d
            .exit_code
            .map(|c| c.to_string())
            .unwrap_or_else(|| "n/a".to_string());
        let mut md = format!(
            "## Game Support Request\n\
             - **Game / Executable:** `{}`\n\
             - **PE Summary:** {}\n\
             - **Target Output:** {}\n\
             - **Host OS / Arch:** `{}/{}`\n\
             - **System Resources:** {}\n\
             - **Failed Stage:** `{}` (exit `{}`)\n\
             - **GameReComp Version:** `v{}`\n\n\
             ### Error Summary\n{}\n",
            d.game_name,
            d.pe_summary,
            d.target_summary,
            std::env::consts::OS,
            std::env::consts::ARCH,
            self.sys_resources.summary(),
            d.failed_stage,
            exit_str,
            crate::cli::VERSION,
            d.error_summary.trim(),
        );
        if !d.user_notes.trim().is_empty() {
            md.push_str("\n### Additional Notes\n");
            md.push_str(d.user_notes.trim());
            md.push('\n');
        }
        if !d.log_tail.trim().is_empty() {
            md.push_str("\n### Sanitized Log Tail\n```text\n");
            md.push_str(d.log_tail.trim());
            md.push_str("\n```\n");
        }
        md.push_str("\n### Toolchain\n```text\n");
        md.push_str(&sanitize_home_path(&self.toolchain.format_selftest_line()));
        md.push_str("\n```\n");
        md
    }

    pub fn copy_issue_markdown(&mut self, cx: &mut Context<Self>) {
        let md = self.format_issue_markdown();
        cx.write_to_clipboard(ClipboardItem::new_string(md));
        self.push_toast(
            "Issue markdown copied",
            "Full sanitized diagnostic markdown copied to clipboard.",
            self.tokens().match_fill,
            false,
        );
        cx.notify();
    }

    pub fn submit_issue_to_github(&mut self, cx: &mut Context<Self>) {
        let md = self.format_issue_markdown();
        cx.write_to_clipboard(ClipboardItem::new_string(md.clone()));
        let title = if self.issue_draft.title.trim().is_empty() {
            format!("[Support Request] {}", self.issue_draft.game_name)
        } else {
            self.issue_draft.title.trim().to_string()
        };
        let body_for_url = if md.chars().count() > 1800 {
            let truncated: String = md.chars().take(1700).collect();
            format!("{truncated}\n\n*(Full sanitized log copied to clipboard — paste if needed.)*")
        } else {
            md
        };
        let url = format!(
            "https://github.com/heaven-hm/GameReComp/issues/new?title={}&body={}",
            percent_encode_query(&title),
            percent_encode_query(&body_for_url),
        );
        let _ = open::that(url);
        self.push_toast(
            "Opened GitHub issue draft",
            "Pre-filled GitHub issue opened and full markdown copied to clipboard.",
            self.tokens().info,
            false,
        );
        self.close_issue_modal(cx);
    }

    pub fn report_problem(&mut self, cx: &mut Context<Self>) {
        let report = format!(
            "GameReComp v{} (GPUI)\nOS/Arch: {}/{}\nResources: {}\nProjects Dir: {}\nToolchain: {}\n",
            crate::cli::VERSION,
            std::env::consts::OS,
            std::env::consts::ARCH,
            self.sys_resources.summary(),
            sanitize_home_path(&self.projects_root().display().to_string()),
            sanitize_home_path(&self.toolchain.format_selftest_line()),
        );
        cx.write_to_clipboard(ClipboardItem::new_string(report.clone()));
        let url = format!(
            "https://github.com/heaven-hm/GameReComp/issues/new?title={}&body={}",
            percent_encode_query("[Bug Report] GameReComp"),
            percent_encode_query(&report),
        );
        let _ = open::that(url);
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

    fn poll_worker_and_timers(&mut self, window: &mut Window, cx: &mut Context<Self>) -> bool {
        let mut changed = false;
        let wasm_startup_error = self
            .wasm_server_startup_error
            .lock()
            .ok()
            .and_then(|mut error| error.take());
        if let Some((generation, error, ready_file)) = wasm_startup_error {
            if generation != self.wasm_server_generation.load(Ordering::SeqCst) {
                if let Some(path) = ready_file {
                    let _ = fs::remove_file(path);
                }
                return changed;
            }
            self.wasm_server_generation.fetch_add(1, Ordering::SeqCst);
            self.wasm_server_ready.store(false, Ordering::SeqCst);
            if let Ok(mut server) = self.wasm_server_child.lock() {
                if let Some(mut child) = server.take() {
                    stop_wasm_server(&mut child);
                }
            }
            if let Some(path) = ready_file {
                let _ = fs::remove_file(path);
            }
            self.wasm_server_ready_file = None;
            self.wasm_server_url = None;
            self.wasm_server_dir = None;
            self.push_toast(
                "WASM server failed to start",
                &error,
                self.tokens().not_c,
                true,
            );
            changed = true;
        }
        let running = self.has_running_jobs();
        if running {
            self.anim_frame = self.anim_frame.wrapping_add(1);
            changed = true;
        }
        let scan_now = running && self.anim_frame % 15 == 0;

        let prev_toasts = self.toasts.len();
        self.toasts
            .retain(|t| t.created.elapsed() < Duration::from_secs(6));
        if self.toasts.len() != prev_toasts {
            changed = true;
        }

        struct FinishedEvent {
            job_id: u64,
            kind: WorkerKind,
            code: i32,
            project_dir: Option<PathBuf>,
            built_binary: Option<PathBuf>,
            has_wasm: bool,
            auto_launch: bool,
            auto_ai_readability: bool,
            cancelled: bool,
            is_dry_run: bool,
            name: String,
            summary: String,
        }

        let mut feed_events: Vec<(String, String, bool)> = Vec::new();
        let mut finished_events: Vec<FinishedEvent> = Vec::new();

        for job in &mut self.jobs {
            if scan_now && job.is_running() {
                scan_job_generated_files(job);
            }
            let mut msgs = Vec::new();
            if let Some(ref rx) = job.rx {
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
                        job.stage = st.clone();
                        if st == "compile" {
                            job.compile_pct = Some(0.0);
                        }
                        job.current_task_detail = crate::pipeline_opts::STAGE_DESCRIPTIONS
                            .iter()
                            .find(|(name, _)| *name == st.as_str())
                            .map(|(_, d)| (*d).to_string())
                            .unwrap_or_else(|| format!("Running stage {st}"));
                        feed_events.push((job.name.clone(), format!("==> {st}"), false));
                        for row in &mut job.live_stages {
                            if row.name == st {
                                row.state = StageExecState::Running(Instant::now());
                            } else if let StageExecState::Running(started) = row.state {
                                row.state = StageExecState::Done(started.elapsed().as_secs_f32());
                            }
                        }
                        scan_job_generated_files(job);
                    }
                    WorkerMsg::StageEnd(st, status, secs, detail) => {
                        for row in &mut job.live_stages {
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
                            job.compile_pct = Some(1.0);
                        }
                        job.current_task_detail = format!("{st}: {status} ({secs:.1}s) {detail}");
                        feed_events.push((
                            format!("{} · {st}", job.name),
                            format!("{status} ({secs:.1}s) {detail}"),
                            status == "ok",
                        ));
                        scan_job_generated_files(job);
                    }
                    WorkerMsg::FileEvent {
                        action,
                        path,
                        detail,
                        compile_pct,
                    } => {
                        job.current_file = path.clone();
                        job.current_task_detail = detail.clone();
                        if let Some(pct) = compile_pct {
                            job.compile_pct = Some(pct);
                        }
                        let dup = job
                            .processed_files
                            .last()
                            .map(|last| last.path == path && last.action == action)
                            .unwrap_or(false);
                        if !dup {
                            job.processed_files.push(ProcessedFileItem {
                                action,
                                path,
                                detail,
                                timestamp: now_hms(),
                                stage: job.stage.clone(),
                            });
                            if job.processed_files.len() > 240 {
                                job.processed_files.remove(0);
                            }
                        }
                    }
                    WorkerMsg::Line(l) => {
                        let tr = l.trim();
                        if !tr.is_empty() && !tr.starts_with("$ ") && tr.len() <= 140 {
                            job.current_task_detail = tr.to_string();
                        }
                        if job.live_logs.len() > 2500 {
                            job.live_logs.remove(0);
                        }
                        job.live_logs.push(l);
                    }
                    WorkerMsg::Finished {
                        kind,
                        code,
                        project_dir,
                        built_binary,
                        has_wasm,
                        summary,
                    } => {
                        scan_job_generated_files(job);
                        let elapsed = job.elapsed_secs();
                        job.finished_secs = Some(elapsed);
                        job.paused_at = None;
                        job.rx = None;
                        job.built_binary = built_binary.clone();
                        job.has_wasm = has_wasm;
                        let ok = code == 0 || code == 3;
                        if !ok {
                            job.failure_summary = summary.clone();
                        }
                        let stopped = job.cancel_requested.load(Ordering::SeqCst);
                        if stopped {
                            job.status = JobStatus::Stopped;
                            job.live_logs.push("[stopped by user]".to_string());
                        } else {
                            job.status = if ok {
                                JobStatus::Done(code)
                            } else {
                                JobStatus::Failed(code)
                            };
                        }
                        job.live_logs.push(format!("[exit {code}] {summary}"));
                        for row in &mut job.live_stages {
                            if let StageExecState::Running(started) = row.state {
                                row.state = if ok {
                                    StageExecState::Done(started.elapsed().as_secs_f32())
                                } else {
                                    StageExecState::Failed
                                };
                            }
                        }
                        if ok {
                            job.compile_pct = Some(1.0);
                        }
                        if job.project_dir.is_none() {
                            job.project_dir = project_dir.clone();
                        }
                        finished_events.push(FinishedEvent {
                            job_id: job.id,
                            kind,
                            code,
                            project_dir,
                            built_binary,
                            has_wasm,
                            auto_launch: job.auto_launch_after_build && !stopped,
                            auto_ai_readability: job.auto_ai_readability && !stopped,
                            cancelled: stopped,
                            is_dry_run: job.is_dry_run,
                            name: job.name.clone(),
                            summary,
                        });
                    }
                }
            }
        }

        for (actor, msg, is_ok) in feed_events {
            self.push_feed(&actor, &msg, is_ok);
        }

        if !finished_events.is_empty() {
            self.refresh_projects_async(window, cx);
        }

        for ev in finished_events {
            let ok = ev.code == 0 || ev.code == 3;
            let project_deleting = ev.project_dir.as_ref().is_some_and(|dir| {
                let key = normalized_path(dir);
                self.deleting_projects.contains(&key)
                    || self
                        .pending_project_deletions
                        .iter()
                        .any(|pending| pending.dir == *dir || pending.canon == key)
            });
            let col = if ev.cancelled {
                self.tokens().info
            } else if ok {
                self.tokens().match_fill
            } else {
                self.tokens().not_c
            };
            let feed_summary = if ev.cancelled {
                format!("{} stopped", ev.name)
            } else {
                ev.summary.clone()
            };
            self.push_feed(&ev.name, &feed_summary, !ev.cancelled && ok);
            self.push_toast(
                &if ev.cancelled {
                    format!("{} stopped", ev.name)
                } else {
                    format!("{} finished (exit {})", ev.name, ev.code)
                },
                &feed_summary,
                col,
                !ev.cancelled && !ok,
            );

            match ev.kind {
                WorkerKind::Pipeline => {
                    if let Some(dir) = ev.project_dir {
                        if ev.built_binary.is_some()
                            || ev.has_wasm
                            || dir.join("reports").join("report.json").is_file()
                        {
                            if !ev.cancelled && !project_deleting {
                                self.last_finished_project = Some(dir.clone());
                            }
                            if !ev.cancelled
                                && !project_deleting
                                && self.active_project_dir.as_ref() == Some(&dir)
                            {
                                self.open_project_dir_async(
                                    dir.clone(),
                                    ProjectTab::Overview,
                                    window,
                                    cx,
                                );
                            } else if !ev.cancelled
                                && !project_deleting
                                && self.jobs.len() == 1
                                && self.queued_pipelines.is_empty()
                                && matches!(self.route, Route::Home)
                                && matches!(self.drop_state, DropState::Idle | DropState::Busy)
                            {
                                self.open_project_dir_async(
                                    dir.clone(),
                                    ProjectTab::Overview,
                                    window,
                                    cx,
                                );
                            }
                            if ok && !ev.cancelled && !ev.is_dry_run && ev.auto_launch {
                                if ev.has_wasm {
                                    self.launch_or_open_wasm(&dir);
                                } else if let Some(ref bin) = ev.built_binary {
                                    self.launch_rebuilt_binary(bin);
                                }
                            }
                            if ok && !ev.cancelled && !ev.is_dry_run && ev.auto_ai_readability {
                                if self.effective_ai_cmd().is_some() {
                                    self.start_readability_agent_for_dir(
                                        SourceImproveMethod::AiLocal,
                                        dir.clone(),
                                        None,
                                        cx,
                                    );
                                } else {
                                    self.push_toast(
                                        "Skipped optional AI readability",
                                        "Build finished! Configure an AI CLI in Agents & models to run the optional C readability pass.",
                                        self.tokens().info,
                                        false,
                                    );
                                }
                            }
                        }
                    }
                }
                WorkerKind::Readability => {
                    self.reload_active_project_async(window, cx);
                }
                WorkerKind::Batch => {}
            }
            let _ = ev.job_id;
        }

        self.dispatch_pending_project_deletions(window, cx);

        // Promote next queued game(s) immediately when parallel slots free up (unless queue paused)
        if !self.queue_paused {
            self.drain_queued_pipelines();
            self.drain_queued_batches(cx);
        }
        changed
    }

    pub fn launch_project_dir_after_build(&mut self, dir: &Path) {
        let Ok(model) = ProjectModel::load(dir) else {
            return;
        };
        if model.has_wasm_build {
            self.launch_or_open_wasm(dir);
        } else if let Some(bin) = model.built_binary_path() {
            self.launch_rebuilt_binary(&bin);
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
            FocusInput::SettingsRevngBin => Some(&mut self.settings.revng_bin),
            FocusInput::SettingsRevngDir => Some(&mut self.settings.revng_dir),
            FocusInput::SettingsAiWebEndpoint => Some(&mut self.settings.ai_web_endpoint),
            FocusInput::SettingsAiWebModel => Some(&mut self.settings.ai_web_model),
            FocusInput::SettingsAiWebCmd => Some(&mut self.settings.ai_web_cmd),
            FocusInput::IssueTitle => Some(&mut self.issue_draft.title),
            FocusInput::IssueNotes => Some(&mut self.issue_draft.user_notes),
            FocusInput::RunGameDataDir => self.run_game_modal.as_mut().map(|m| &mut m.data_dir),
            FocusInput::QuickRenameProject => self.rename_modal.as_mut().map(|m| &mut m.name_buf),
        }
    }

    fn handle_key_down(
        &mut self,
        event: &KeyDownEvent,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let key = event.keystroke.key.as_str();
        let mods = &event.keystroke.modifiers;

        // Global font zoom shortcuts: Cmd/Ctrl + '+', '-', '0'
        if mods.platform || mods.control {
            if key == "=" || key == "+" {
                self.step_font_scale(0.05, cx);
                return;
            }
            if key == "-" {
                self.step_font_scale(-0.05, cx);
                return;
            }
            if key == "0" {
                self.set_font_scale(1.0, cx);
                return;
            }
        }

        if key == "escape" {
            if self.focused_input.is_some() {
                self.focused_input = None;
            } else if self.context_menu.is_some() {
                self.context_menu = None;
            } else if self.run_game_modal.is_some() {
                self.run_game_modal = None;
            } else if self.rename_modal.is_some() {
                self.rename_modal = None;
            } else if self.confirm_delete.is_some() {
                self.confirm_delete = None;
            } else if self.show_issue_modal {
                self.show_issue_modal = false;
            } else if self.inspector_open {
                self.inspector_open = false;
            } else if self.show_notif_panel {
                self.show_notif_panel = false;
            } else if self.show_theme_menu {
                self.show_theme_menu = false;
            } else if self.show_new_menu {
                self.show_new_menu = false;
            } else if self.show_source_improve_menu {
                self.show_source_improve_menu = false;
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
                    FocusInput::QuickRenameProject => self.commit_quick_rename_project(window, cx),
                    FocusInput::AgentCmd => self.save_agent_cmd(),
                    FocusInput::SettingsRevngBin
                    | FocusInput::SettingsRevngDir
                    | FocusInput::SettingsAiWebEndpoint
                    | FocusInput::SettingsAiWebModel
                    | FocusInput::SettingsAiWebCmd => self.save_source_improve_settings(),
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

    fn render_notif_center(
        &self,
        t: &ThemeTokens,
        viewport_w: f32,
        cx: &mut Context<Self>,
    ) -> Option<Div> {
        if !self.show_notif_panel {
            return None;
        }
        let sidebar_w = if self.sidebar_collapsed || viewport_w < 920.0 {
            60.0
        } else if viewport_w < 1140.0 {
            220.0
        } else {
            256.0
        };
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
                    .font_family(t.ui_font)
                    .text_size(px(t.ui_px(12.5)))
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
                                                .font_family(t.ui_font)
                                                .font_weight(FontWeight::BOLD)
                                                .text_size(px(t.ui_px(12.5)))
                                                .text_color(rgb(t.ink))
                                                .child(n.title.clone()),
                                        )
                                        .child(
                                            div()
                                                .font_family(t.mono_font)
                                                .text_size(px(t.ui_px(10.5)))
                                                .text_color(rgb(t.muted))
                                                .child(n.timestamp.clone()),
                                        ),
                                )
                                .child(
                                    div()
                                        .font_family(t.ui_font)
                                        .text_size(px(t.ui_px(12.0)))
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
                .left(px(sidebar_w))
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
                                        .font_family(t.ui_font)
                                        .font_weight(FontWeight::BOLD)
                                        .text_size(px(t.ui_px(14.5)))
                                        .text_color(rgb(t.ink))
                                        .child("What happened"),
                                )
                                .child(
                                    div()
                                        .font_family(t.ui_font)
                                        .text_size(px(t.ui_px(11.5)))
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
                                .child(style::ghost_btn("notif_close_btn", t, "✕").on_click(
                                    cx.listener(|this, _, _, cx| {
                                        this.show_notif_panel = false;
                                        cx.notify();
                                    }),
                                )),
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
                                            .font_family(t.ui_font)
                                            .font_weight(FontWeight::BOLD)
                                            .text_size(px(t.ui_px(12.5)))
                                            .text_color(if toast.is_error {
                                                rgb(t.miss)
                                            } else {
                                                rgb(t.ink)
                                            })
                                            .child(toast.title.clone()),
                                    )
                                    .child(
                                        div()
                                            .font_family(t.mono_font)
                                            .text_size(px(t.ui_px(10.5)))
                                            .text_color(rgb(t.muted))
                                            .child(toast.timestamp.clone()),
                                    ),
                            )
                            .child(
                                div()
                                    .font_family(t.mono_font)
                                    .text_size(px(t.ui_px(11.5)))
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
        if let Ok(_open_guard) = self.wasm_server_open_lock.lock() {
            self.wasm_server_generation.fetch_add(1, Ordering::SeqCst);
        }
        self.wasm_server_ready.store(false, Ordering::SeqCst);
        for job in &self.jobs {
            Self::signal_process_group(job, "-CONT");
            Self::signal_process_group(job, "-KILL");
            if let Ok(mut g) = job.child.lock() {
                if let Some(ref mut c) = *g {
                    let _ = c.kill();
                }
            }
        }
        if let Ok(mut g) = self.wasm_server_child.lock() {
            if let Some(mut child) = g.take() {
                stop_wasm_server(&mut child);
            }
        }
        if let Some(path) = self.wasm_server_ready_file.take() {
            let _ = fs::remove_file(path);
        }
    }
}

fn stop_wasm_server(child: &mut Child) {
    let pid = child.id().to_string();
    #[cfg(unix)]
    {
        let _ = Command::new("kill")
            .args(["-TERM", &format!("-{pid}")])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &pid, "/T", "/F"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

/// Scans an active job's live output directory for newly generated C sources, headers, reports,
/// and compiled artifacts so every concurrent job shows each individual file in real time.
fn scan_job_generated_files(job: &mut ActiveJob) {
    let Some(proj_dir) = job.project_dir.clone() else {
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
            if job
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
            job.current_file = rel_display.clone();
            job.processed_files.push(ProcessedFileItem {
                action,
                path: rel_display,
                detail,
                timestamp: now_hms(),
                stage: job.stage.clone(),
            });
            if job.processed_files.len() > 240 {
                job.processed_files.remove(0);
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
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let t = self.tokens();
        let vp = window.viewport_size();
        let viewport_w: f32 = vp.width.into();
        let viewport_h: f32 = vp.height.into();
        let main_view = match self.route {
            Route::Home => home::render_home(self, &t, viewport_w, cx).into_any_element(),
            Route::Project(tab) => {
                project::render_project(self, &t, tab, viewport_w, cx).into_any_element()
            }
            Route::Agents => pages::render_agents_page(self, &t, viewport_w, cx).into_any_element(),
            Route::Tools => pages::render_tools_page(self, &t, viewport_w, cx).into_any_element(),
            Route::Settings => {
                pages::render_settings_page(self, &t, viewport_w, cx).into_any_element()
            }
            Route::About => pages::render_about_page(self, &t, viewport_w, cx).into_any_element(),
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
            .font_family(t.ui_font)
            .child(sidebar::render_sidebar(self, &t, viewport_w, cx))
            .child(main_view)
            .when_some(
                inspector::render_function_inspector(self, &t, cx),
                |el, d| el.child(d),
            )
            .when_some(self.render_notif_center(&t, viewport_w, cx), |el, p| {
                el.child(p)
            })
            .when_some(self.render_toasts(&t), |el, st| el.child(st))
            .when_some(
                pages::render_project_context_menu(self, &t, viewport_w, viewport_h, cx),
                |el, cm| el.child(cm),
            )
            .when_some(pages::render_run_game_modal(self, &t, cx), |el, m| {
                el.child(m)
            })
            .when_some(pages::render_rename_project_modal(self, &t, cx), |el, m| {
                el.child(m)
            })
            .when_some(pages::render_delete_project_modal(self, &t, cx), |el, m| {
                el.child(m)
            })
            .when_some(pages::render_issue_modal(self, &t, cx), |el, m| el.child(m))
    }
}

fn normalized_path(path: &Path) -> PathBuf {
    fs::canonicalize(path).unwrap_or_else(|_| {
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join(path)
        }
    })
}

fn valid_project_manifest(dir: &Path) -> bool {
    fs::read_to_string(dir.join("manifest.json"))
        .ok()
        .and_then(|contents| serde_json::from_str::<serde_json::Value>(&contents).ok())
        .is_some_and(|manifest| manifest.get("binary").is_some() || manifest.get("pe").is_some())
}

fn file_identity(path: &Path) -> io::Result<FileIdentity> {
    let metadata = fs::symlink_metadata(path)?;
    #[cfg(unix)]
    let (volume, file) = {
        use std::os::unix::fs::MetadataExt;
        (metadata.dev(), u128::from(metadata.ino()))
    };
    #[cfg(windows)]
    let (volume, file) = {
        // `MetadataExt::{volume_serial_number,file_index}` are still unstable on
        // stable Rust (windows_by_handle). Fall back to a weaker identity on
        // Windows; symlink/type checks still guard against obvious swaps.
        let _ = metadata;
        (0u64, 0u128)
    };
    #[cfg(not(any(unix, windows)))]
    let (volume, file) = (metadata.len(), 0);
    Ok(FileIdentity {
        volume,
        file,
        symlink: metadata.file_type().is_symlink(),
    })
}

fn remove_project_path(path: &Path, expected: FileIdentity) -> io::Result<()> {
    let actual = file_identity(path)?;
    if actual.volume != expected.volume
        || actual.file != expected.file
        || actual.symlink != expected.symlink
    {
        return Err(io::Error::other(
            "project path changed after deletion was approved; replacement was left untouched",
        ));
    }
    if actual.symlink {
        fs::remove_file(path)
    } else {
        fs::remove_dir_all(path)
    }
}

fn has_exe_extension(path: &Path) -> bool {
    path.extension()
        .and_then(|extension| extension.to_str())
        .map(|extension| extension.eq_ignore_ascii_case("exe"))
        .unwrap_or(false)
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
    pgid_slot: Arc<Mutex<Option<u32>>>,
    cancel_requested: Arc<AtomicBool>,
    tx: &mpsc::Sender<WorkerMsg>,
) -> i32 {
    if cancel_requested.load(Ordering::SeqCst) {
        return 1;
    }
    #[cfg(unix)]
    {
        cmd.process_group(0);
    }
    match cmd.spawn() {
        Ok(mut child) => {
            let pid = child.id();
            if let Ok(mut pg) = pgid_slot.lock() {
                *pg = Some(pid);
            }
            let stdout = child.stdout.take();
            let stderr = child.stderr.take();
            if let Ok(mut g) = child_slot.lock() {
                *g = Some(child);
            }
            let mut cancellation_sent = false;
            if cancel_requested.load(Ordering::SeqCst) {
                if let Ok(mut guard) = child_slot.lock() {
                    if let Some(child) = guard.as_mut() {
                        terminate_worker_process(child, pid);
                    }
                }
                cancellation_sent = true;
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
                if !cancellation_sent && cancel_requested.load(Ordering::SeqCst) {
                    if let Ok(mut guard) = child_slot.lock() {
                        if let Some(child) = guard.as_mut() {
                            terminate_worker_process(child, pid);
                        }
                    }
                    cancellation_sent = true;
                }
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
            if let Ok(mut pgid) = pgid_slot.lock() {
                *pgid = None;
            }
            code
        }
        Err(e) => {
            let _ = tx.send(WorkerMsg::Line(format!("spawn failed: {e}")));
            1
        }
    }
}

fn terminate_worker_process(child: &mut Child, pid: u32) {
    #[cfg(unix)]
    {
        let _ = Command::new("kill")
            .args(["-KILL", "--", &format!("-{pid}")])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    let _ = child.kill();
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
        return Some((
            "CC",
            file,
            format!("Compiling C translation unit{pct_str}"),
            pct,
        ));
    }
    if let Some(target) = body.strip_prefix("Linking C executable ") {
        let name = target.trim().to_string();
        return Some((
            "LINK",
            name,
            "Linking standalone executable".to_string(),
            pct.or(Some(1.0)),
        ));
    }
    if let Some(target) = body.strip_prefix("Linking C static library ") {
        let name = target.trim().to_string();
        return Some(("LINK", name, "Archiving runtime library".to_string(), pct));
    }

    // Scan tokens for recognizable generated/processed file paths
    for tok in body.split_whitespace() {
        let clean =
            tok.trim_matches(|c: char| matches!(c, '\'' | '"' | ',' | ';' | ':' | '(' | ')'));
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

pub fn sanitize_home_path(s: &str) -> String {
    let mut out = s.to_string();
    for var in ["HOME", "USERPROFILE"] {
        if let Ok(home) = std::env::var(var) {
            let trimmed = home.trim_end_matches(['/', '\\']);
            if trimmed.len() > 2 && out.contains(trimmed) {
                out = out.replace(trimmed, "~");
            }
        }
    }
    out
}

pub fn percent_encode_query(s: &str) -> String {
    use std::fmt::Write as _;
    let mut out = String::with_capacity(s.len());
    for b in s.as_bytes() {
        match *b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*b as char);
            }
            _ => {
                let _ = write!(out, "%{b:02X}");
            }
        }
    }
    out
}

pub fn launch_gui(cfg: AppConfig, open_dir: Option<&Path>) -> i32 {
    let open_owned = open_dir.map(|p| p.to_path_buf());
    gpui::application()
        .with_assets(gpui::assets::Assets)
        .run(move |cx: &mut App| {
            gpui::init(cx);
            style::register_fonts(cx);
            let bounds = Bounds::centered(None, size(px(1380.0), px(880.0)), cx);
            let app_icon = Arc::new(
                image::load_from_memory(include_bytes!("../../assets/gamerecomp-app-icon-128.png"))
                    .expect("embedded GameRecomp app icon")
                    .to_rgba8(),
            );
            let options = WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                window_min_size: Some(size(px(860.0), px(580.0))),
                titlebar: Some(TitlebarOptions {
                    title: Some("GameReComp — Pick your game and turn it to native port".into()),
                    appears_transparent: false,
                    ..Default::default()
                }),
                icon: Some(app_icon),
                ..Default::default()
            };
            cx.on_window_closed(|cx, _id| {
                if cx.windows().is_empty() {
                    cx.quit();
                }
            })
            .detach();
            let cfg_clone = cfg.clone();
            let open_clone = open_owned.clone();
            if let Err(e) = gpui::open_window(options, cx, move |window, cx| {
                let view =
                    cx.new(|cx| GameReCompApp::new(cfg_clone, open_clone.as_deref(), window, cx));
                let handle = view.read(cx).focus_handle.clone();
                window.focus(&handle, cx);
                view
            }) {
                eprintln!("error: failed to open GPUI Kit window: {e}");
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
    fn native_launch_assets_root_prefers_configured_dir_then_pe_parent() {
        let unique = format!(
            "gr-launch-assets-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        );
        let root = std::env::temp_dir().join(unique);
        let game_data = root.join("game data");
        let pe_dir = root.join("original");
        fs::create_dir_all(&game_data).unwrap();
        fs::create_dir_all(&pe_dir).unwrap();
        let pe = pe_dir.join("game.exe");
        fs::write(&pe, b"not a real PE").unwrap();

        // No configured dir: the original PE's parent is the assets root.
        assert_eq!(
            GameReCompApp::native_launch_assets_root("", [pe.as_path()]),
            Some(fs::canonicalize(&pe_dir).unwrap())
        );
        // An explicitly configured assets dir wins over the PE parent.
        assert_eq!(
            GameReCompApp::native_launch_assets_root(game_data.to_str().unwrap(), [pe.as_path()]),
            Some(fs::canonicalize(&game_data).unwrap())
        );
        // Stale paths are skipped instead of producing an invalid launch cwd.
        assert_eq!(
            GameReCompApp::native_launch_assets_root(
                root.join("missing").to_str().unwrap(),
                [Path::new("/definitely/not/a/game.exe"), pe.as_path()]
            ),
            Some(fs::canonicalize(&pe_dir).unwrap())
        );
        assert_eq!(
            GameReCompApp::native_launch_assets_root("", [Path::new("game.exe")]),
            None
        );
        fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn executable_picker_filter_is_case_insensitive() {
        assert!(has_exe_extension(Path::new("game.EXE")));
        assert!(!has_exe_extension(Path::new("game.dll")));
        assert!(!has_exe_extension(Path::new("game")));
    }

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
        let (act, path, detail, pct) = parse_file_event(
            "[ 42%] Building C object CMakeFiles/game.dir/src/recomp/gen/recomp_001.c.o",
        )
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

    #[test]
    fn sanitizes_home_paths_and_percent_encodes_query() {
        if let Ok(home) = std::env::var("HOME") {
            let trimmed = home.trim_end_matches('/');
            if trimmed.len() > 2 {
                let sample = format!("{trimmed}/Games/cube.exe failed");
                assert_eq!(sanitize_home_path(&sample), "~/Games/cube.exe failed");
            }
        }
        assert_eq!(percent_encode_query("Hi [1]!"), "Hi%20%5B1%5D%21");
    }
}
