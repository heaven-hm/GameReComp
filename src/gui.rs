use std::path::Path;

use crate::config::AppConfig;

#[cfg(have_sdl2)]
mod sdl_ui {
    use std::collections::HashMap;
    use std::ffi::{CStr, CString};
    use std::fs;
    use std::io::{BufRead, BufReader};
    use std::os::raw::{c_char, c_int, c_void};
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};
    use std::sync::mpsc::{self, Receiver};
    use std::thread;

    use crate::config::{AppConfig, ToolchainStatus};
    use crate::model::ProjectModel;
    use crate::pe::inspect_pe;

    const SDL_INIT_VIDEO: u32 = 0x0000_0020;
    const SDL_WINDOWPOS_CENTERED: c_int = 0x2FFF_0000;
    const SDL_WINDOW_SHOWN: u32 = 0x0000_0004;
    const SDL_WINDOW_RESIZABLE: u32 = 0x0000_0020;
    const SDL_RENDERER_ACCELERATED: u32 = 0x0000_0002;
    const SDL_RENDERER_SOFTWARE: u32 = 0x0000_0001;

    const SDL_QUIT: u32 = 0x100;
    const SDL_WINDOWEVENT: u32 = 0x200;
    const SDL_KEYDOWN: u32 = 0x300;
    const SDL_TEXTINPUT: u32 = 0x303;
    const SDL_MOUSEBUTTONDOWN: u32 = 0x401;
    const SDL_MOUSEWHEEL: u32 = 0x403;

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct SdlRect {
        x: c_int,
        y: c_int,
        w: c_int,
        h: c_int,
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct SdlColor {
        r: u8,
        g: u8,
        b: u8,
        a: u8,
    }

    #[repr(C)]
    struct SdlSurfacePartial {
        _flags: u32,
        _format: *const c_void,
        w: c_int,
        h: c_int,
    }

    extern "C" {
        fn SDL_Init(flags: u32) -> c_int;
        fn SDL_Quit();
        fn SDL_CreateWindow(
            title: *const c_char,
            x: c_int,
            y: c_int,
            w: c_int,
            h: c_int,
            flags: u32,
        ) -> *mut c_void;
        fn SDL_DestroyWindow(window: *mut c_void);
        fn SDL_CreateRenderer(window: *mut c_void, index: c_int, flags: u32) -> *mut c_void;
        fn SDL_DestroyRenderer(renderer: *mut c_void);
        fn SDL_GetWindowSize(window: *mut c_void, w: *mut c_int, h: *mut c_int);
        fn SDL_SetRenderDrawColor(renderer: *mut c_void, r: u8, g: u8, b: u8, a: u8) -> c_int;
        fn SDL_RenderClear(renderer: *mut c_void) -> c_int;
        fn SDL_RenderFillRect(renderer: *mut c_void, rect: *const SdlRect) -> c_int;
        fn SDL_RenderDrawRect(renderer: *mut c_void, rect: *const SdlRect) -> c_int;
        fn SDL_RenderCopy(
            renderer: *mut c_void,
            texture: *mut c_void,
            srcrect: *const SdlRect,
            dstrect: *const SdlRect,
        ) -> c_int;
        fn SDL_RenderPresent(renderer: *mut c_void);
        fn SDL_CreateTextureFromSurface(
            renderer: *mut c_void,
            surface: *mut SdlSurfacePartial,
        ) -> *mut c_void;
        fn SDL_DestroyTexture(texture: *mut c_void);
        fn SDL_FreeSurface(surface: *mut SdlSurfacePartial);
        fn SDL_RWFromFile(file: *const c_char, mode: *const c_char) -> *mut c_void;
        fn SDL_LoadBMP_RW(src: *mut c_void, freesrc: c_int) -> *mut SdlSurfacePartial;
        fn SDL_PollEvent(event: *mut [u8; 64]) -> c_int;
        fn SDL_Delay(ms: u32);
        fn SDL_StartTextInput();
        fn SDL_StopTextInput();
        fn SDL_GetError() -> *const c_char;
    }

    #[cfg(have_sdl2_ttf)]
    extern "C" {
        fn TTF_Init() -> c_int;
        fn TTF_Quit();
        fn TTF_OpenFont(file: *const c_char, ptsize: c_int) -> *mut c_void;
        fn TTF_CloseFont(font: *mut c_void);
        fn TTF_RenderUTF8_Blended(
            font: *mut c_void,
            text: *const c_char,
            fg: SdlColor,
        ) -> *mut SdlSurfacePartial;
    }

    #[derive(Clone, Copy, PartialEq, Eq)]
    enum Tab {
        Stages,
        Source,
        Certified,
        Build,
        Frames,
        Log,
    }

    #[derive(Clone, Copy, PartialEq, Eq)]
    enum FocusField {
        None,
        ProjectDir,
        PePath,
        Search,
    }

    enum WorkerMsg {
        Line(String),
        Finished(i32),
    }

    struct TexEntry {
        tex: *mut c_void,
        w: i32,
        h: i32,
    }

    struct GuiApp {
        cfg: AppConfig,
        renderer: *mut c_void,
        #[cfg(have_sdl2_ttf)]
        font: *mut c_void,
        tex_cache: HashMap<String, TexEntry>,
        project_dir: String,
        pe_path: String,
        search_filter: String,
        focus: FocusField,
        tab: Tab,
        capture_frames: bool,
        model: Option<ProjectModel>,
        load_error: String,
        pe_status: String,
        pe_ok: bool,
        toolchain_line: String,
        toolchain_ready: bool,
        selected_func: Option<usize>,
        func_source: String,
        selected_frame: Option<usize>,
        scroll: i32,
        log_lines: Vec<String>,
        worker_rx: Option<Receiver<WorkerMsg>>,
        running: bool,
        status_text: String,
        click_zones: Vec<(SdlRect, ClickAction)>,
    }

    #[derive(Clone, Copy)]
    enum ClickAction {
        Focus(FocusField),
        Reload,
        RunPipeline,
        ToggleCapture,
        SelectTab(Tab),
        SelectFunc(usize),
        SelectFrame(usize),
    }

    impl GuiApp {
        fn new(cfg: AppConfig, renderer: *mut c_void, initial_project: Option<&Path>) -> Self {
            #[cfg(have_sdl2_ttf)]
            let font = open_ui_font(14);

            let project_dir = initial_project
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_else(|| {
                    cfg.repo_root
                        .join("projects")
                        .join("cavestory-doukutsu")
                        .to_string_lossy()
                        .to_string()
                });
            let tc = ToolchainStatus::detect();
            let toolchain_line = tc.format_selftest_line();
            let toolchain_ready = tc.is_ready();

            let mut app = Self {
                cfg,
                renderer,
                #[cfg(have_sdl2_ttf)]
                font,
                tex_cache: HashMap::new(),
                project_dir,
                pe_path: String::new(),
                search_filter: String::new(),
                focus: FocusField::None,
                tab: Tab::Stages,
                capture_frames: false,
                model: None,
                load_error: String::new(),
                pe_status: "no executable selected".to_string(),
                pe_ok: true,
                toolchain_line,
                toolchain_ready,
                selected_func: None,
                func_source: String::new(),
                selected_frame: None,
                scroll: 0,
                log_lines: vec!["GameReComp Rust GUI ready.".to_string()],
                worker_rx: None,
                running: false,
                status_text: "ready".to_string(),
                click_zones: Vec::new(),
            };
            app.reload_project();
            app
        }

        fn reload_project(&mut self) {
            let p = PathBuf::from(self.project_dir.trim());
            match ProjectModel::load(&p) {
                Ok(mut m) => {
                    m.load_certified_index(&self.cfg.repo_root);
                    if self.pe_path.is_empty() && !m.pe_path.is_empty() {
                        self.pe_path = m.pe_path.clone();
                    }
                    self.status_text = format!(
                        "score {}/100 | {} | {} function(s) | {} certified",
                        m.score,
                        if m.verification.is_empty() { "-" } else { &m.verification },
                        m.functions.len(),
                        m.certified.len()
                    );
                    self.model = Some(m);
                    self.load_error.clear();
                }
                Err(e) => {
                    self.model = None;
                    self.load_error = e;
                    self.status_text = "no project loaded".to_string();
                }
            }
            self.validate_pe();
            self.selected_func = None;
            self.func_source.clear();
            self.selected_frame = None;
            self.scroll = 0;
        }

        fn validate_pe(&mut self) {
            let pe = self.pe_path.trim();
            if pe.is_empty() {
                self.pe_status = "no executable selected".to_string();
                self.pe_ok = true;
                return;
            }
            let info = inspect_pe(Path::new(pe));
            if !info.ok {
                self.pe_status = format!("not usable: {}", info.error);
                self.pe_ok = false;
            } else if info.pe32_x86 {
                self.pe_status = format!(
                    "{} {} image base 0x{:08X} -- supported target",
                    info.magic, info.machine, info.image_base
                );
                self.pe_ok = true;
            } else {
                self.pe_status = format!("{} {} -- NOT a supported target", info.magic, info.machine);
                self.pe_ok = false;
            }
        }

        fn start_pipeline(&mut self) {
            if self.running {
                return;
            }
            let pe = self.pe_path.trim().to_string();
            let dir = self.project_dir.trim().to_string();
            if pe.is_empty() || dir.is_empty() {
                self.status_text = "both executable and project directory are required".to_string();
                return;
            }
            self.running = true;
            self.tab = Tab::Log;
            self.log_lines.clear();
            self.log_lines
                .push(format!("$ ./GameReComp {pe} --project-dir {dir} --timeout 12 --no-table"));
            self.status_text = "running pipeline...".to_string();

            let repo_root = self.cfg.repo_root.clone();
            let capture = self.capture_frames;
            let (tx, rx) = mpsc::channel();
            self.worker_rx = Some(rx);

            thread::spawn(move || {
                let mut cmd = Command::new("python3");
                cmd.current_dir(&repo_root)
                    .env("PYTHONPATH", &repo_root)
                    .args([
                        "-m",
                        "tools.autorecomp.cli",
                        &pe,
                        "--project-dir",
                        &dir,
                        "--timeout",
                        "12",
                        "--no-table",
                        "--no-progress",
                    ])
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped());

                if capture {
                    let shots = PathBuf::from(&dir).join("screenshots");
                    let _ = fs::create_dir_all(&shots);
                    cmd.env("RECOMP_SCREENSHOT", shots);
                }

                match cmd.spawn() {
                    Ok(mut child) => {
                        if let Some(out) = child.stdout.take() {
                            let tx2 = tx.clone();
                            thread::spawn(move || {
                                for line in BufReader::new(out).lines().flatten() {
                                    let _ = tx2.send(WorkerMsg::Line(line));
                                }
                            });
                        }
                        if let Some(err) = child.stderr.take() {
                            let tx3 = tx.clone();
                            thread::spawn(move || {
                                for line in BufReader::new(err).lines().flatten() {
                                    let _ = tx3.send(WorkerMsg::Line(line));
                                }
                            });
                        }
                        let code = child.wait().ok().and_then(|s| s.code()).unwrap_or(1);
                        let _ = tx.send(WorkerMsg::Finished(code));
                    }
                    Err(e) => {
                        let _ = tx.send(WorkerMsg::Line(format!("spawn failed: {e}")));
                        let _ = tx.send(WorkerMsg::Finished(1));
                    }
                }
            });
        }

        fn poll_worker(&mut self) {
            let mut finished = None;
            if let Some(ref rx) = self.worker_rx {
                while let Ok(msg) = rx.try_recv() {
                    match msg {
                        WorkerMsg::Line(l) => {
                            if self.log_lines.len() > 2000 {
                                self.log_lines.remove(0);
                            }
                            self.log_lines.push(l);
                        }
                        WorkerMsg::Finished(code) => finished = Some(code),
                    }
                }
            }
            if let Some(code) = finished {
                self.running = false;
                self.worker_rx = None;
                self.log_lines.push(format!("[exit {code}]"));
                self.reload_project();
            }
        }

        fn fill_rect(&self, r: SdlRect, col: (u8, u8, u8, u8)) {
            unsafe {
                SDL_SetRenderDrawColor(self.renderer, col.0, col.1, col.2, col.3);
                SDL_RenderFillRect(self.renderer, &r);
            }
        }

        fn stroke_rect(&self, r: SdlRect, col: (u8, u8, u8, u8)) {
            unsafe {
                SDL_SetRenderDrawColor(self.renderer, col.0, col.1, col.2, col.3);
                SDL_RenderDrawRect(self.renderer, &r);
            }
        }

        fn draw_text(&mut self, x: i32, y: i32, text: &str, col: (u8, u8, u8)) {
            if text.is_empty() {
                return;
            }
            #[cfg(have_sdl2_ttf)]
            if !self.font.is_null() {
                let key = format!("{:02x}{:02x}{:02x}:{text}", col.0, col.1, col.2);
                if !self.tex_cache.contains_key(&key) {
                    if self.tex_cache.len() > 1500 {
                        for (_, v) in self.tex_cache.drain() {
                            unsafe { SDL_DestroyTexture(v.tex) };
                        }
                    }
                    if let Ok(cs) = CString::new(text.replace('\0', " ")) {
                        let surf = unsafe {
                            TTF_RenderUTF8_Blended(
                                self.font,
                                cs.as_ptr(),
                                SdlColor {
                                    r: col.0,
                                    g: col.1,
                                    b: col.2,
                                    a: 255,
                                },
                            )
                        };
                        if !surf.is_null() {
                            let (w, h) = unsafe { ((*surf).w, (*surf).h) };
                            let tex = unsafe { SDL_CreateTextureFromSurface(self.renderer, surf) };
                            unsafe { SDL_FreeSurface(surf) };
                            if !tex.is_null() {
                                self.tex_cache.insert(key.clone(), TexEntry { tex, w, h });
                            }
                        }
                    }
                }
                if let Some(entry) = self.tex_cache.get(&key) {
                    let dst = SdlRect {
                        x,
                        y,
                        w: entry.w,
                        h: entry.h,
                    };
                    unsafe {
                        SDL_RenderCopy(self.renderer, entry.tex, std::ptr::null(), &dst);
                    }
                    return;
                }
            }
            // Fallback minimal glyph indicator when no TTF font is available
            let _ = (x, y, col);
        }

        fn handle_click(&mut self, mx: i32, my: i32) {
            let mut act = None;
            for (rect, a) in &self.click_zones {
                if mx >= rect.x && mx <= rect.x + rect.w && my >= rect.y && my <= rect.y + rect.h {
                    act = Some(*a);
                }
            }
            if let Some(a) = act {
                match a {
                    ClickAction::Focus(f) => self.focus = f,
                    ClickAction::Reload => self.reload_project(),
                    ClickAction::RunPipeline => self.start_pipeline(),
                    ClickAction::ToggleCapture => self.capture_frames = !self.capture_frames,
                    ClickAction::SelectTab(t) => {
                        self.tab = t;
                        self.scroll = 0;
                    }
                    ClickAction::SelectFunc(idx) => {
                        self.selected_func = Some(idx);
                        self.func_source.clear();
                        if let Some(ref m) = self.model {
                            if let Some(row) = m.functions.get(idx) {
                                let mut prov = format!(
                                    "// address: {} | symbol: {} | category: {} | module: {}\n",
                                    row.address, row.symbol, row.category, row.file
                                );
                                if !row.verified_by.is_empty() {
                                    prov.push_str(&format!("// verified_by: {}\n", row.verified_by));
                                }
                                if !row.not_proven.is_empty() {
                                    prov.push_str(&format!("// not_proven: {}\n", row.not_proven));
                                }
                                if !row.evidence.is_empty() {
                                    prov.push_str(&format!("// evidence: {}\n", row.evidence.join("; ")));
                                }
                                let body = if !row.source_path.is_empty() {
                                    fs::read_to_string(m.dir.join(&row.source_path))
                                        .unwrap_or_else(|_| "cannot read source".to_string())
                                } else {
                                    format!("no generated source delivered for {} ({})", row.address, row.symbol)
                                };
                                self.func_source = format!("{prov}\n{body}");
                            }
                        }
                    }
                    ClickAction::SelectFrame(idx) => {
                        self.selected_frame = Some(idx);
                    }
                }
            } else {
                self.focus = FocusField::None;
            }
        }

        fn render(&mut self, win_w: i32, win_h: i32) {
            self.click_zones.clear();
            unsafe {
                SDL_SetRenderDrawColor(self.renderer, 245, 246, 248, 255);
                SDL_RenderClear(self.renderer);
            }

            // Header panel
            let hdr = SdlRect { x: 12, y: 10, w: win_w - 24, h: 162 };
            self.fill_rect(hdr, (255, 255, 255, 255));
            self.stroke_rect(hdr, (210, 214, 220, 255));

            self.draw_text(24, 18, "Project directory:", (50, 55, 65));
            let proj_box = SdlRect { x: 165, y: 16, w: win_w - 300, h: 24 };
            self.fill_rect(proj_box, (250, 251, 253, 255));
            self.stroke_rect(
                proj_box,
                if self.focus == FocusField::ProjectDir { (45, 110, 210, 255) } else { (190, 195, 205, 255) },
            );
            let proj_disp = self.project_dir.clone();
            self.draw_text(172, 20, &proj_disp, (20, 24, 30));
            self.click_zones.push((proj_box, ClickAction::Focus(FocusField::ProjectDir)));

            let reload_btn = SdlRect { x: win_w - 125, y: 16, w: 96, h: 24 };
            self.fill_rect(reload_btn, (230, 234, 240, 255));
            self.stroke_rect(reload_btn, (175, 180, 190, 255));
            self.draw_text(reload_btn.x + 24, reload_btn.y + 4, "Reload", (30, 35, 45));
            self.click_zones.push((reload_btn, ClickAction::Reload));

            self.draw_text(24, 48, "Original PE:", (50, 55, 65));
            let pe_box = SdlRect { x: 165, y: 46, w: win_w - 195, h: 24 };
            self.fill_rect(pe_box, (250, 251, 253, 255));
            self.stroke_rect(
                pe_box,
                if self.focus == FocusField::PePath { (45, 110, 210, 255) } else { (190, 195, 205, 255) },
            );
            let pe_disp = self.pe_path.clone();
            self.draw_text(172, 50, &pe_disp, (20, 24, 30));
            self.click_zones.push((pe_box, ClickAction::Focus(FocusField::PePath)));

            let pe_col = if self.pe_ok { (46, 125, 50) } else { (198, 40, 40) };
            let pe_status = self.pe_status.clone();
            self.draw_text(165, 76, &pe_status, pe_col);

            let run_btn = SdlRect { x: 165, y: 100, w: 124, h: 26 };
            self.fill_rect(run_btn, if self.running { (180, 185, 195, 255) } else { (38, 102, 202, 255) });
            self.draw_text(
                run_btn.x + 18,
                run_btn.y + 5,
                if self.running { "Running..." } else { "Run pipeline" },
                (255, 255, 255),
            );
            self.click_zones.push((run_btn, ClickAction::RunPipeline));

            let cap_box = SdlRect { x: 304, y: 105, w: 16, h: 16 };
            self.fill_rect(cap_box, if self.capture_frames { (46, 125, 50, 255) } else { (255, 255, 255, 255) });
            self.stroke_rect(cap_box, (140, 145, 155, 255));
            self.draw_text(326, 104, "capture frames", (45, 50, 60));
            self.click_zones.push((
                SdlRect { x: 304, y: 102, w: 135, h: 22 },
                ClickAction::ToggleCapture,
            ));

            let status_copy = if self.load_error.is_empty() {
                self.status_text.clone()
            } else {
                self.load_error.clone()
            };
            self.draw_text(455, 104, &status_copy, (40, 45, 55));

            let tc_col = if self.toolchain_ready { (46, 125, 50) } else { (161, 92, 0) };
            let tc_line = self.toolchain_line.clone();
            self.draw_text(24, 138, &tc_line, tc_col);

            // Tabs
            let tabs = [
                ("Stages", Tab::Stages),
                ("Source explorer", Tab::Source),
                ("Certified", Tab::Certified),
                ("Build", Tab::Build),
                ("Frames", Tab::Frames),
                ("Log", Tab::Log),
            ];
            let mut tx = 14;
            for (label, t) in tabs {
                let active = self.tab == t;
                let w = (label.len() as i32) * 9 + 26;
                let r = SdlRect { x: tx, y: 182, w, h: 28 };
                self.fill_rect(r, if active { (255, 255, 255, 255) } else { (228, 232, 238, 255) });
                self.stroke_rect(r, (195, 200, 208, 255));
                self.draw_text(tx + 12, 187, label, if active { (20, 25, 35) } else { (75, 82, 94) });
                self.click_zones.push((r, ClickAction::SelectTab(t)));
                tx += w + 4;
            }

            let body = SdlRect { x: 12, y: 210, w: win_w - 24, h: win_h - 242 };
            self.fill_rect(body, (255, 255, 255, 255));
            self.stroke_rect(body, (195, 200, 208, 255));

            self.render_tab_body(body);

            // Status bar
            let sb = SdlRect { x: 12, y: win_h - 26, w: win_w - 24, h: 20 };
            self.fill_rect(sb, (236, 239, 244, 255));
            let sline = format!(
                "{} | Rust GUI (SDL2) + Python RE Backend + C Runtime",
                self.status_text
            );
            self.draw_text(18, win_h - 24, &sline, (65, 72, 84));

            unsafe { SDL_RenderPresent(self.renderer); }
        }

        fn render_tab_body(&mut self, body: SdlRect) {
            let max_rows = ((body.h - 32) / 22).max(1) as usize;
            let start = self.scroll.max(0) as usize;
            match self.tab {
                Tab::Stages => {
                    self.draw_text(body.x + 14, body.y + 10, "Stage              Status      Detail", (80, 88, 100));
                    let stages = self.model.as_ref().map(|m| m.stages.clone()).unwrap_or_default();
                    for (idx, s) in stages.iter().skip(start).take(max_rows).enumerate() {
                        let y = body.y + 36 + (idx as i32) * 22;
                        let line = format!("{:<18} {:<11} {}", s.name, s.status, s.detail);
                        let col = if s.status == "ok" {
                            (46, 125, 50)
                        } else if s.status == "failed" {
                            (198, 40, 40)
                        } else {
                            (60, 65, 75)
                        };
                        self.draw_text(body.x + 14, y, &line, col);
                    }
                }
                Tab::Source => {
                    let sbox = SdlRect { x: body.x + 12, y: body.y + 10, w: 360, h: 22 };
                    self.fill_rect(sbox, (248, 250, 252, 255));
                    self.stroke_rect(
                        sbox,
                        if self.focus == FocusField::Search { (45, 110, 210, 255) } else { (190, 195, 205, 255) },
                    );
                    let ftxt = if self.search_filter.is_empty() {
                        "filter by address or symbol...".to_string()
                    } else {
                        self.search_filter.clone()
                    };
                    self.draw_text(sbox.x + 8, sbox.y + 3, &ftxt, (60, 68, 80));
                    self.click_zones.push((sbox, ClickAction::Focus(FocusField::Search)));

                    let funcs = self.model.as_ref().map(|m| m.functions.clone()).unwrap_or_default();
                    let flt = self.search_filter.to_ascii_lowercase();
                    let filtered: Vec<(usize, _)> = funcs
                        .iter()
                        .enumerate()
                        .filter(|(_, f)| {
                            flt.is_empty()
                                || f.address.to_ascii_lowercase().contains(&flt)
                                || f.symbol.to_ascii_lowercase().contains(&flt)
                        })
                        .collect();
                    for (row_i, (orig_idx, f)) in filtered.iter().skip(start).take(max_rows.saturating_sub(1)).enumerate() {
                        let y = body.y + 42 + (row_i as i32) * 22;
                        let r = SdlRect { x: body.x + 12, y, w: 460, h: 20 };
                        if self.selected_func == Some(*orig_idx) {
                            self.fill_rect(r, (224, 236, 252, 255));
                        }
                        let txt = format!("{}  {:<22}  {}", f.address, f.category, f.symbol);
                        self.draw_text(body.x + 16, y + 2, &txt, (30, 35, 45));
                        self.click_zones.push((r, ClickAction::SelectFunc(*orig_idx)));
                    }

                    let src_x = body.x + 485;
                    let lines: Vec<String> = self.func_source.lines().take(max_rows).map(|s| s.to_string()).collect();
                    for (i, line) in lines.iter().enumerate() {
                        self.draw_text(src_x, body.y + 14 + (i as i32) * 20, line, (35, 40, 48));
                    }
                }
                Tab::Certified => {
                    let rows = self.model.as_ref().map(|m| m.certified.clone()).unwrap_or_default();
                    self.draw_text(body.x + 14, body.y + 10, "Address      Verdict        Mem   Cases  Compares                       Module", (80, 88, 100));
                    for (idx, r) in rows.iter().skip(start).take(max_rows).enumerate() {
                        let y = body.y + 36 + (idx as i32) * 22;
                        let line = format!(
                            "{:<12} {:<14} {:<5} {:<6} {:<30} {} ({})",
                            r.address, r.verdict, r.named_memory, r.cases, r.compares, r.module, r.verdict_json
                        );
                        self.draw_text(body.x + 14, y, &line, (35, 40, 50));
                    }
                }
                Tab::Build => {
                    if let Some(ref m) = self.model {
                        let b = &m.build;
                        let lines = if !b.present {
                            vec!["no manifest.json -- this project has no recorded build.".to_string()]
                        } else {
                            vec![
                                format!("sources built:  {} file(s)", b.source_files),
                                format!("source digest:  {}", b.source_digest),
                                format!("binary:         {}", if b.binary_path.is_empty() { "none recorded" } else { &b.binary_path }),
                                format!("binary sha256:  {}", b.binary_sha256),
                                format!("binary size:    {} bytes", b.binary_size),
                                format!("built from:     {}", b.built_from_digest),
                                format!("digests match:  {}", if b.digests_match { "YES" } else { "NO" }),
                                format!("build command:  {}", b.build_command),
                            ]
                        };
                        for (i, l) in lines.iter().enumerate() {
                            self.draw_text(body.x + 14, body.y + 16 + (i as i32) * 24, l, (30, 35, 45));
                        }
                    }
                }
                Tab::Frames => {
                    let frames = self.model.as_ref().map(|m| m.frames.clone()).unwrap_or_default();
                    if frames.is_empty() {
                        self.draw_text(body.x + 16, body.y + 16, "No captured frames in screenshots/.", (90, 96, 108));
                    } else {
                        for (i, fp) in frames.iter().skip(start).take(max_rows).enumerate() {
                            let y = body.y + 14 + (i as i32) * 22;
                            let r = SdlRect { x: body.x + 12, y, w: 220, h: 20 };
                            let name = fp.file_name().and_then(|s| s.to_str()).unwrap_or("");
                            if self.selected_frame == Some(i + start) {
                                self.fill_rect(r, (224, 236, 252, 255));
                            }
                            self.draw_text(r.x + 6, y + 2, name, (30, 35, 45));
                            self.click_zones.push((r, ClickAction::SelectFrame(i + start)));
                        }
                        if let Some(sel) = self.selected_frame.and_then(|i| frames.get(i)) {
                            if sel.extension().and_then(|e| e.to_str()).map(|x| x.eq_ignore_ascii_case("bmp")).unwrap_or(false) {
                                if let (Ok(cp), Ok(cm)) = (CString::new(sel.to_string_lossy().as_ref()), CString::new("rb")) {
                                    unsafe {
                                        let rw = SDL_RWFromFile(cp.as_ptr(), cm.as_ptr());
                                        if !rw.is_null() {
                                            let surf = SDL_LoadBMP_RW(rw, 1);
                                            if !surf.is_null() {
                                                let tex = SDL_CreateTextureFromSurface(self.renderer, surf);
                                                SDL_FreeSurface(surf);
                                                if !tex.is_null() {
                                                    let dst = SdlRect {
                                                        x: body.x + 250,
                                                        y: body.y + 16,
                                                        w: (body.w - 270).min(640),
                                                        h: (body.h - 32).min(480),
                                                    };
                                                    SDL_RenderCopy(self.renderer, tex, std::ptr::null(), &dst);
                                                    SDL_DestroyTexture(tex);
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                Tab::Log => {
                    let lines = self.log_lines.clone();
                    let skip = lines.len().saturating_sub(max_rows);
                    for (i, line) in lines.iter().skip(skip).enumerate() {
                        self.draw_text(body.x + 14, body.y + 12 + (i as i32) * 20, line, (35, 40, 48));
                    }
                }
            }
        }
    }

    #[cfg(have_sdl2_ttf)]
    fn open_ui_font(ptsize: i32) -> *mut c_void {
        for candidate in [
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/SFNSMono.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ] {
            if Path::new(candidate).is_file() {
                if let Ok(cs) = CString::new(candidate) {
                    let f = unsafe { TTF_OpenFont(cs.as_ptr(), ptsize) };
                    if !f.is_null() {
                        return f;
                    }
                }
            }
        }
        std::ptr::null_mut()
    }

    pub fn launch(cfg: AppConfig, open_dir: Option<&Path>) -> i32 {
        unsafe {
            if SDL_Init(SDL_INIT_VIDEO) != 0 {
                let err = CStr::from_ptr(SDL_GetError()).to_string_lossy();
                eprintln!("SDL_Init failed: {err}");
                return 1;
            }
            #[cfg(have_sdl2_ttf)]
            let ttf_ok = TTF_Init() == 0;

            let title = CString::new("GameReComp").unwrap();
            let win = SDL_CreateWindow(
                title.as_ptr(),
                SDL_WINDOWPOS_CENTERED,
                SDL_WINDOWPOS_CENTERED,
                1180,
                760,
                SDL_WINDOW_SHOWN | SDL_WINDOW_RESIZABLE,
            );
            if win.is_null() {
                SDL_Quit();
                return 1;
            }
            let mut ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED);
            if ren.is_null() {
                ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
            }
            if ren.is_null() {
                SDL_DestroyWindow(win);
                SDL_Quit();
                return 1;
            }

            SDL_StartTextInput();
            let mut app = GuiApp::new(cfg, ren, open_dir);
            let mut quit = false;
            let mut ev = [0u8; 64];

            while !quit {
                while SDL_PollEvent(&mut ev) != 0 {
                    let etype = u32::from_ne_bytes([ev[0], ev[1], ev[2], ev[3]]);
                    match etype {
                        SDL_QUIT => quit = true,
                        SDL_MOUSEBUTTONDOWN => {
                            let x = i32::from_ne_bytes([ev[20], ev[21], ev[22], ev[23]]);
                            let y = i32::from_ne_bytes([ev[24], ev[25], ev[26], ev[27]]);
                            app.handle_click(x, y);
                        }
                        SDL_MOUSEWHEEL => {
                            let wy = i32::from_ne_bytes([ev[20], ev[21], ev[22], ev[23]]);
                            app.scroll = (app.scroll - wy).max(0);
                        }
                        SDL_KEYDOWN => {
                            let sym = i32::from_ne_bytes([ev[20], ev[21], ev[22], ev[23]]);
                            if sym == 8 {
                                // Backspace
                                match app.focus {
                                    FocusField::ProjectDir => { app.project_dir.pop(); }
                                    FocusField::PePath => { app.pe_path.pop(); app.validate_pe(); }
                                    FocusField::Search => { app.search_filter.pop(); }
                                    FocusField::None => {}
                                }
                            } else if sym == 13 && app.focus == FocusField::ProjectDir {
                                app.reload_project();
                            } else if sym == 27 {
                                app.focus = FocusField::None;
                            }
                        }
                        SDL_TEXTINPUT => {
                            if let Ok(cstr) = CStr::from_bytes_until_nul(&ev[12..44]) {
                                if let Ok(txt) = cstr.to_str() {
                                    match app.focus {
                                        FocusField::ProjectDir => app.project_dir.push_str(txt),
                                        FocusField::PePath => {
                                            app.pe_path.push_str(txt);
                                            app.validate_pe();
                                        }
                                        FocusField::Search => app.search_filter.push_str(txt),
                                        FocusField::None => {}
                                    }
                                }
                            }
                        }
                        SDL_WINDOWEVENT => {}
                        _ => {}
                    }
                }

                app.poll_worker();
                let mut w = 1180;
                let mut h = 760;
                SDL_GetWindowSize(win, &mut w, &mut h);
                app.render(w, h);
                SDL_Delay(16);
            }

            SDL_StopTextInput();
            for (_, v) in app.tex_cache.drain() {
                SDL_DestroyTexture(v.tex);
            }
            #[cfg(have_sdl2_ttf)]
            if !app.font.is_null() {
                TTF_CloseFont(app.font);
            }
            #[cfg(have_sdl2_ttf)]
            if ttf_ok {
                TTF_Quit();
            }
            SDL_DestroyRenderer(ren);
            SDL_DestroyWindow(win);
            SDL_Quit();
        }
        0
    }
}

pub fn launch_gui(cfg: AppConfig, open_dir: Option<&Path>) -> i32 {
    #[cfg(have_sdl2)]
    {
        sdl_ui::launch(cfg, open_dir)
    }
    #[cfg(not(have_sdl2))]
    {
        let _ = (cfg, open_dir);
        eprintln!("error: SDL2 was not detected at build time; use CLI mode or --selftest");
        1
    }
}
