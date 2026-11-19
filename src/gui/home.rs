use gpui::{
    ClipboardItem, Context, Div, ExternalPaths, FontWeight, Stateful, Styled, div, prelude::*, px,
    rgb,
};

use super::style;
use super::{ActiveJob, DropState, FocusInput, GameReCompApp, JobStatus, ProjectTab, StageExecState};
use crate::model::ProjectModel;
use crate::pipeline_opts::{ALL_STAGES, AppProfile, OutMode, STAGE_DESCRIPTIONS};
use crate::theme_tokens::ThemeTokens;

pub fn render_home(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Stateful<Div> {
    let mut content = div()
        .id("home_scroll_view")
        .flex_1()
        .h_full()
        .overflow_y_scroll()
        .px(px(36.0))
        .pt(px(28.0))
        .pb(px(56.0))
        .flex()
        .flex_col()
        .gap(px(22.0));

    // ------------------------------------------------------------------ Hero 2-column row
    let hero = div()
        .w_full()
        .max_w(px(1340.0))
        .flex()
        .flex_row()
        .flex_wrap()
        .items_start()
        .gap(px(36.0))
        .child(render_hero_copy(t))
        .child(render_dropzone_card(app, t, cx));

    content = content.child(hero);

    // ------------------------------------------------------------------ Multi-Game Concurrent Queue Panel
    if !app.jobs.is_empty() || !app.queued_pipelines.is_empty() {
        content = content.child(render_active_jobs_panel(app, t, cx));
    }

    // ------------------------------------------------------------------ Privacy shield notice
    content = content.child(
        div()
            .max_w(px(1340.0))
            .flex()
            .items_center()
            .gap(px(9.0))
            .child(
                div()
                    .font_family(t.mono_font)
                    .font_weight(FontWeight::BOLD)
                    .text_size(px(t.ui_px(14.0)))
                    .text_color(rgb(t.match_fill))
                    .child("🛡"),
            )
            .child(
                div()
                    .font_family(t.ui_font)
                    .text_size(px(t.ui_px(13.0)))
                    .text_color(rgb(t.muted))
                    .child("Your game never leaves this computer. Recompilation, builds, and verification run locally; AI readability only runs when you explicitly configure an agent."),
            ),
    );

    // ------------------------------------------------------------------ 3 Ruled Start Cards
    let sample_desc = if let Some(first) = app.projects.first() {
        format!(
            "Jump into {} ({}% score · {} functions) to explore the live Function Map & Inspector.",
            first.name, first.score, first.total_functions
        )
    } else {
        "Load tests/fixtures/pe32-minimal.exe to test the full PE32 inspection & option flow."
            .to_string()
    };

    let sample_title = if app.projects.is_empty() {
        "Try the PE32 fixture"
    } else {
        "Explore a project"
    };

    let starts = div()
        .w_full()
        .max_w(px(1340.0))
        .flex()
        .flex_row()
        .flex_wrap()
        .gap(px(14.0))
        .child(
            start_card(
                "start_batch",
                t,
                true,
                "Batch recompile a folder",
                "Scan a folder of Windows PE32 games or apps, recompile each, and verify standalone runs.",
            )
            .on_click(cx.listener(|this, _, _, cx| {
                this.browse_for_batch_folder(cx);
            })),
        )
        .child(
            start_card(
                "start_open_saved",
                t,
                false,
                "Open a saved project",
                "Load an existing GameReComp directory with project.json, reports, functions, and frames.",
            )
            .on_click(cx.listener(|this, _, _, cx| {
                this.browse_for_project_folder(cx);
            })),
        )
        .child(
            start_card("start_sample", t, false, sample_title, &sample_desc).on_click(cx.listener(
                |this, _, _, cx| {
                    if let Some(first) = this.projects.first().cloned() {
                        this.open_project_dir(first.dir, ProjectTab::Overview);
                    } else {
                        let fixture = this
                            .cfg
                            .repo_root
                            .join("tests")
                            .join("fixtures")
                            .join("pe32-minimal.exe");
                        this.pick_pe_path(fixture);
                    }
                    cx.notify();
                },
            )),
        );

    content = content.child(starts);

    // ------------------------------------------------------------------ Your Projects Grid
    if !app.projects.is_empty() {
        let mut grid = div()
            .w_full()
            .flex()
            .flex_row()
            .flex_wrap()
            .gap(px(14.0));

        for (idx, proj) in app.projects.iter().enumerate() {
            let dir = proj.dir.clone();
            let sw_hex = t.project_swatches[proj.color_idx % t.project_swatches.len()];
            let pct = (proj.score.clamp(0, 100) as f32) / 100.0;
            let meta = if proj.load_ok {
                format!(
                    "{} · {} functions · {}",
                    proj.target_label,
                    proj.total_functions,
                    if proj.verification.is_empty() {
                        "built"
                    } else {
                        &proj.verification
                    }
                )
            } else {
                "Not built yet — click to open".to_string()
            };

            let card = div()
                .id(("home_proj_card", idx))
                .w(px(300.0))
                .flex_grow()
                .flex()
                .flex_col()
                .gap(px(10.0))
                .p(px(18.0))
                .rounded(px(4.0))
                .bg(rgb(t.panel))
                .border_1()
                .border_color(rgb(t.line))
                .cursor_pointer()
                .hover({
                    let ink = t.ink;
                    move |s| s.border_color(rgb(ink))
                })
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.open_project_dir(dir.clone(), ProjectTab::Overview);
                    cx.notify();
                }))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(8.0))
                        .child(div().w(px(9.0)).h(px(9.0)).rounded(px(2.0)).bg(rgb(sw_hex)))
                        .child(
                            div()
                                .font_family(t.ui_font)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(t.ui_px(15.5)))
                                .text_color(rgb(t.ink))
                                .child(proj.name.clone()),
                        ),
                )
                .child(
                    div()
                        .font_family(t.mono_font)
                        .text_size(px(t.ui_px(11.5)))
                        .text_color(rgb(t.muted))
                        .child(meta),
                )
                .child(style::progress_meter(
                    t,
                    px(6.0),
                    if proj.load_ok { pct } else { 0.0 },
                    t.match_fill,
                ))
                .child(
                    div()
                        .font_family(t.mono_font)
                        .font_weight(FontWeight::SEMIBOLD)
                        .text_size(px(t.ui_px(11.5)))
                        .text_color(rgb(t.ink_2))
                        .child(if proj.load_ok {
                            format!("{} / 100 score", proj.score)
                        } else {
                            "unbuilt".to_string()
                        }),
                );
            grid = grid.child(card);
        }

        let recent_section = div()
            .w_full()
            .max_w(px(1340.0))
            .mt(px(14.0))
            .pt(px(22.0))
            .border_t_3()
            .border_color(rgb(t.ink))
            .flex()
            .flex_col()
            .gap(px(14.0))
            .child(
                div()
                    .font_family(t.ui_font)
                    .font_weight(FontWeight::EXTRA_BOLD)
                    .text_size(px(t.ui_px(26.0)))
                    .text_color(rgb(t.ink))
                    .child("Your projects"),
            )
            .child(grid);

        content = content.child(recent_section);
    }

    content
}

fn render_hero_copy(t: &ThemeTokens) -> Div {
    let steps = [
        (
            "1",
            "Drop your game or app",
            "GameReComp inspects the PE32 header, imports, and entry point fail-closed",
        ),
        (
            "2",
            "Choose target & options",
            "Configure OS/Arch, Native or WebAssembly, Game/App profile, stages, and logs",
        ),
        (
            "3",
            "Watch live & inspect",
            "Track every animated stage in real time, explore the Function Map, or run AI agents",
        ),
    ];

    let mut step_list = div().flex().flex_col().gap(px(11.0)).mt(px(10.0));
    for (num, title, desc) in steps {
        step_list = step_list.child(
            div()
                .flex()
                .items_start()
                .gap(px(12.0))
                .child(
                    div()
                        .w(px(24.0))
                        .h(px(24.0))
                        .min_w(px(24.0))
                        .flex()
                        .items_center()
                        .justify_center()
                        .rounded(px(3.0))
                        .bg(rgb(t.ink))
                        .text_color(rgb(t.bg))
                        .font_family(t.mono_font)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(t.ui_px(12.0)))
                        .child(num),
                )
                .child(
                    div()
                        .flex()
                        .flex_col()
                        .gap(px(1.0))
                        .child(
                            div()
                                .font_family(t.ui_font)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(t.ui_px(14.0)))
                                .text_color(rgb(t.ink))
                                .child(title),
                        )
                        .child(
                            div()
                                .font_family(t.ui_font)
                                .text_size(px(t.ui_px(13.0)))
                                .text_color(rgb(t.muted))
                                .child(desc),
                        ),
                ),
        );
    }

    div()
        .flex_1()
        .min_w(px(360.0))
        .max_w(px(560.0))
        .flex()
        .flex_col()
        .gap(px(12.0))
        .pt(px(8.0))
        .child(style::eyebrow(t, "GAMERECOMP · NATIVE PORT STUDIO"))
        .child(
            div()
                .font_family(t.ui_font)
                .font_weight(FontWeight::EXTRA_BOLD)
                .text_size(px(t.ui_px(44.0)))
                .line_height(px(46.0))
                .text_color(rgb(t.ink))
                .child("Pick your game\nand turn it to\nnative port."),
        )
        .child(
            div()
                .font_family(t.ui_font)
                .text_size(px(t.ui_px(15.0)))
                .line_height(px(23.0))
                .text_color(rgb(t.ink_2))
                .child("Turn 32-bit Windows games and apps into standalone native ports for macOS, Linux, Windows, or WebAssembly — with automatic machine detection, live per-file animations, and one-click game launch."),
        )
        .child(step_list)
}

fn render_dropzone_card(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let mut card = div()
        .id("pe_dropzone")
        .flex_1()
        .min_w(px(440.0))
        .min_h(px(360.0))
        .p(px(24.0))
        .rounded(px(4.0))
        .bg(rgb(t.panel))
        .border_2()
        .border_dashed()
        .border_color(rgb(t.line))
        .flex()
        .flex_col()
        .justify_center()
        .on_drop::<ExternalPaths>(cx.listener(|this, paths: &ExternalPaths, _, cx| {
            let dropped = paths.paths();
            if dropped.len() > 1 {
                let files: Vec<std::path::PathBuf> =
                    dropped.iter().filter(|p| p.is_file()).cloned().collect();
                if !files.is_empty() {
                    this.enqueue_multiple_pes(files);
                } else if let Some(first_dir) = dropped.iter().find(|p| p.is_dir()).cloned() {
                    this.start_batch_worker(first_dir, cx);
                }
                cx.notify();
            } else if let Some(first) = dropped.first().cloned() {
                if first.is_dir() {
                    if first.join("project.json").is_file()
                        || first.join("reports").join("report.json").is_file()
                    {
                        this.open_project_dir(first, ProjectTab::Overview);
                    } else {
                        this.start_batch_worker(first, cx);
                    }
                } else {
                    this.pick_pe_path(first);
                }
                cx.notify();
            }
        }));

    card = match &app.drop_state {
        DropState::Idle => card.child(render_drop_idle(t, cx)),
        DropState::Picked => card.child(render_drop_picked(app, t, cx)),
        DropState::Busy => card.child(render_drop_busy(app, t, cx)),
        DropState::Error(err) => card.child(render_drop_error(app, t, err, cx)),
    };

    div().flex_1().min_w(px(440.0)).child(card)
}

fn render_drop_idle(t: &ThemeTokens, cx: &mut Context<GameReCompApp>) -> Div {
    let cart = div()
        .w(px(92.0))
        .h(px(68.0))
        .rounded(px(6.0))
        .bg(rgb(t.bg_alt))
        .border_1()
        .border_color(rgb(t.line))
        .flex()
        .flex_col()
        .items_center()
        .justify_center()
        .gap(px(4.0))
        .child(
            div()
                .w(px(62.0))
                .h(px(26.0))
                .rounded(px(2.0))
                .bg(rgb(t.panel))
                .border_1()
                .border_color(rgb(t.line))
                .flex()
                .items_center()
                .justify_center()
                .font_family(t.mono_font)
                .font_weight(FontWeight::BOLD)
                .text_size(px(t.ui_px(11.0)))
                .text_color(rgb(t.muted))
                .child("PE32"),
        );

    let plat_row = div()
        .flex()
        .flex_wrap()
        .justify_center()
        .gap(px(6.0))
        .mt(px(10.0))
        .child(plat_chip(t, "★ PE32 x86", true))
        .child(plat_chip(t, "macOS", false))
        .child(plat_chip(t, "Linux", false))
        .child(plat_chip(t, "Windows", false))
        .child(plat_chip(t, "WebAssembly", false));

    div()
        .w_full()
        .flex()
        .flex_col()
        .items_center()
        .gap(px(10.0))
        .py(px(18.0))
        .child(cart)
        .child(
            div()
                .mt(px(6.0))
                .font_family(t.ui_font)
                .font_weight(FontWeight::EXTRA_BOLD)
                .text_size(px(t.ui_px(20.0)))
                .text_color(rgb(t.ink))
                .child("Drop a game or app here"),
        )
        .child(
            div()
                .flex()
                .flex_wrap()
                .items_center()
                .justify_center()
                .gap(px(6.0))
                .font_family(t.ui_font)
                .text_size(px(t.ui_px(13.0)))
                .text_color(rgb(t.muted))
                .child("32-bit Windows PE (.exe)  ·")
                .child(
                    div()
                        .id("drop_choose_file_link")
                        .text_color(if t.dark { rgb(t.hl) } else { rgb(t.info) })
                        .font_weight(FontWeight::SEMIBOLD)
                        .underline()
                        .cursor_pointer()
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.browse_for_pe(cx);
                        }))
                        .child("choose a file"),
                )
                .child("·")
                .child(
                    div()
                        .id("drop_choose_multi_link")
                        .px(px(8.0))
                        .py(px(3.0))
                        .rounded(px(3.0))
                        .bg(rgb(t.bg_alt))
                        .border_1()
                        .border_color(rgb(t.line))
                        .font_family(t.mono_font)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(t.ui_px(11.5)))
                        .text_color(rgb(t.ink))
                        .cursor_pointer()
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.browse_for_multiple_pes(cx);
                        }))
                        .child("+ Select multiple games…"),
                ),
        )
        .child(
            div()
                .font_family(t.ui_font)
                .text_size(px(t.ui_px(11.5)))
                .text_color(rgb(t.muted))
                .child("Tip: Drop multiple .exe files at once to decompile several games in parallel."),
        )
        .child(plat_row)
        .child(
            div()
                .flex()
                .items_center()
                .gap(px(4.0))
                .mt(px(4.0))
                .font_family(t.ui_font)
                .text_size(px(t.ui_px(11.5)))
                .text_color(rgb(t.muted))
                .child(
                    div()
                        .text_color(rgb(t.match_fill))
                        .font_weight(FontWeight::BOLD)
                        .child("★"),
                )
                .child("recommended: PE32 i386 native & wasm32 · PE32+ fails closed"),
        )
        .child(
            div()
                .id("drop_open_dir_link")
                .mt(px(8.0))
                .font_family(t.ui_font)
                .font_weight(FontWeight::SEMIBOLD)
                .text_size(px(t.ui_px(12.0)))
                .text_color(rgb(t.hl))
                .underline()
                .cursor_pointer()
                .on_click(cx.listener(|this, _, _, cx| {
                    this.browse_for_project_folder(cx);
                }))
                .child("Already have a project folder? Open an existing directory"),
        )
}

/// Complete, user-friendly GameReComp Pipeline Configurator (exposes ALL CLI options cleanly!).
fn render_drop_picked(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let fname = app
        .picked_pe
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("executable.exe")
        .to_string();
    let stem = app
        .picked_pe
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("Game")
        .to_string();
    let info = &app.picked_pe_info;
    let opts = &app.opts;
    let sz = std::fs::metadata(&app.picked_pe)
        .map(|m| crate::cli::format_bytes(m.len()))
        .unwrap_or_else(|_| "-".to_string());

    let (status_txt, status_border, status_fg) = if !info.ok {
        (format!("Not usable: {}", info.error), t.not_c, t.miss)
    } else if info.pe32_x86 {
        (
            "PE32 i386 verified — supported target for native & WebAssembly recompilation."
                .to_string(),
            t.match_fill,
            t.match_ink,
        )
    } else {
        (
            format!(
                "{} {} is NOT supported yet (fail-closed: only PE32 i386 is accepted).",
                info.magic, info.machine
            ),
            t.not_c,
            t.miss,
        )
    };

    let can_start = info.ok && info.pe32_x86 && app.picked_owned_checked;

    let mut box_div = div().w_full().flex().flex_col().gap(px(12.0));

    // Header: PE card + facts
    box_div = box_div
        .child(
            div()
                .flex()
                .items_center()
                .justify_between()
                .child(
                    div()
                        .flex()
                        .flex_col()
                        .gap(px(2.0))
                        .child(style::eyebrow(t, fname))
                        .child(
                            div()
                                .font_family(t.ui_font)
                                .font_weight(FontWeight::EXTRA_BOLD)
                                .text_size(px(t.ui_px(22.0)))
                                .text_color(rgb(t.ink))
                                .child(stem),
                        ),
                )
                .child(
                    style::ghost_btn("change_pe_btn", t, "Change file…").on_click(cx.listener(
                        |this, _, _, cx| {
                            this.browse_for_pe(cx);
                        },
                    )),
                ),
        )
        .child(
            div()
                .flex()
                .flex_wrap()
                .gap(px(22.0))
                .child(fact(t, "FORMAT", &format!("{} {}", info.magic, info.machine)))
                .child(fact(t, "IMAGE BASE", &format!("0x{:08X}", info.image_base)))
                .child(fact(t, "FILE SIZE", &sz)),
        )
        .child(
            div()
                .px(px(10.0))
                .py(px(7.0))
                .rounded(px(3.0))
                .bg(rgb(t.bg_alt))
                .border_1()
                .border_color(rgb(status_border))
                .font_family(t.ui_font)
                .text_size(px(t.ui_px(12.5)))
                .text_color(rgb(status_fg))
                .child(status_txt),
        );

    // 1. Profile selector: Select Game vs Desktop App vs Console App
    box_div = box_div.child(
        div()
            .flex()
            .flex_col()
            .gap(px(5.0))
            .child(style::eyebrow(t, "SELECT APPLICATION TYPE"))
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .gap(px(6.0))
                    .child(
                        style::option_pill(
                            "prof_game",
                            t,
                            AppProfile::Game.label(),
                            opts.app_profile == AppProfile::Game,
                        )
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.opts.apply_profile(AppProfile::Game);
                            cx.notify();
                        })),
                    )
                    .child(
                        style::option_pill(
                            "prof_desktop",
                            t,
                            AppProfile::DesktopApp.label(),
                            opts.app_profile == AppProfile::DesktopApp,
                        )
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.opts.apply_profile(AppProfile::DesktopApp);
                            cx.notify();
                        })),
                    )
                    .child(
                        style::option_pill(
                            "prof_console",
                            t,
                            AppProfile::ConsoleApp.label(),
                            opts.app_profile == AppProfile::ConsoleApp,
                        )
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.opts.apply_profile(AppProfile::ConsoleApp);
                            cx.notify();
                        })),
                    ),
            )
            .child(
                div()
                    .font_family(t.ui_font)
                    .text_size(px(t.ui_px(11.5)))
                    .text_color(rgb(t.muted))
                    .child(opts.app_profile.description()),
            ),
    );

    // 2. Output Machine & Target OS / Arch (with Auto Detect!)
    let detected_summary = opts.resolved_target_summary();
    box_div = box_div.child(
        div()
            .flex()
            .flex_col()
            .gap(px(6.0))
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .items_center()
                    .justify_between()
                    .gap(px(6.0))
                    .child(style::eyebrow(t, "OUTPUT MACHINE & TARGET"))
                    .child(style::status_badge(
                        detected_summary,
                        t.info_bg,
                        t.info,
                    )),
            )
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .gap(px(6.0))
                    .child(
                        style::option_pill(
                            "tgt_auto",
                            t,
                            format!(
                                "✦ Auto (Detect: {}/{})",
                                crate::pipeline_opts::detected_host_os(),
                                crate::pipeline_opts::detected_host_arch()
                            ),
                            opts.is_auto_target(),
                        )
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.opts.set_target_auto();
                            cx.notify();
                        })),
                    )
                    .child(
                        style::option_pill(
                            "tgt_wasm",
                            t,
                            "WebAssembly (web/wasm32)",
                            opts.is_wasm_target(),
                        )
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.opts.set_target_wasm32();
                            cx.notify();
                        })),
                    ),
            )
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .items_center()
                    .gap(px(6.0))
                    .child(
                        div()
                            .font_family(t.mono_font)
                            .text_size(px(t.ui_px(11.0)))
                            .text_color(rgb(t.muted))
                            .child("Output OS:"),
                    )
                    .children(
                        ["auto", "host", "macos", "linux", "windows", "web"]
                            .into_iter()
                            .enumerate()
                            .map(|(idx, os_val)| {
                                let selected = opts.target_os == os_val;
                                style::option_pill(("out_os", idx), t, os_val, selected).on_click(
                                    cx.listener(move |this, _, _, cx| {
                                        this.opts.target_os = os_val.to_string();
                                        if os_val == "web" {
                                            this.opts.target_arch = "wasm32".to_string();
                                        } else if os_val == "auto" && this.opts.target_arch == "wasm32" {
                                            this.opts.target_arch = "auto".to_string();
                                        }
                                        cx.notify();
                                    }),
                                )
                            }),
                    ),
            )
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .items_center()
                    .gap(px(6.0))
                    .child(
                        div()
                            .font_family(t.mono_font)
                            .text_size(px(t.ui_px(11.0)))
                            .text_color(rgb(t.muted))
                            .child("Output Arch:"),
                    )
                    .children(
                        ["auto", "host", "arm64", "x86_64", "wasm32"]
                            .into_iter()
                            .enumerate()
                            .map(|(idx, arch_val)| {
                                let selected = opts.target_arch == arch_val;
                                style::option_pill(("out_arch", idx), t, arch_val, selected)
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.opts.target_arch = arch_val.to_string();
                                        if arch_val == "wasm32" {
                                            this.opts.target_os = "web".to_string();
                                        } else if arch_val == "auto" && this.opts.target_os == "web" {
                                            this.opts.target_os = "auto".to_string();
                                        }
                                        cx.notify();
                                    }))
                            }),
                    ),
            ),
    );

    // 3. Output Folder (`--project-dir`)
    box_div = box_div.child(
        div()
            .flex()
            .flex_col()
            .gap(px(5.0))
            .child(style::eyebrow(t, "OUTPUT PROJECT FOLDER (--project-dir)"))
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(8.0))
                    .child(
                        style::text_input_box(
                            "opts_proj_dir",
                            t,
                            &opts.project_dir,
                            "projects/<game_name>",
                            app.focused_input == Some(FocusInput::OptProjectDir),
                            true,
                        )
                        .on_click(cx.listener(|this, _, window, cx| {
                            this.set_focus_input(Some(FocusInput::OptProjectDir), window, cx);
                        })),
                    )
                    .child(
                        style::ghost_btn("browse_proj_dir_btn", t, "Choose…").on_click(
                            cx.listener(|this, _, _, cx| {
                                if let Some(d) = rfd::FileDialog::new().pick_folder() {
                                    this.opts.project_dir = d.display().to_string();
                                    cx.notify();
                                }
                            }),
                        ),
                    ),
            ),
    );

    // 4. Quick Toggles: Enable Logs, Screenshots, Verify Standalone, Advanced Options expander
    box_div = box_div.child(
        div()
            .flex()
            .flex_wrap()
            .gap(px(8.0))
            .child(
                style::toggle_checkbox(
                    "opt_enable_logs",
                    t,
                    opts.enable_logs,
                    "Enable verbose logs",
                    None,
                )
                .on_click(cx.listener(|this, _, _, cx| {
                    this.opts.enable_logs = !this.opts.enable_logs;
                    if this.opts.enable_logs {
                        this.opts.quiet = false;
                    }
                    cx.notify();
                })),
            )
            .child(
                style::toggle_checkbox(
                    "opt_capture_frames",
                    t,
                    opts.capture_screenshots,
                    "Capture screenshots",
                    None,
                )
                .on_click(cx.listener(|this, _, _, cx| {
                    this.opts.capture_screenshots = !this.opts.capture_screenshots;
                    cx.notify();
                })),
            )
            .child(
                style::toggle_checkbox(
                    "opt_verify_standalone",
                    t,
                    opts.verify_standalone,
                    "Verify standalone",
                    None,
                )
                .on_click(cx.listener(|this, _, _, cx| {
                    this.opts.verify_standalone = !this.opts.verify_standalone;
                    cx.notify();
                })),
            ),
    );

    // Toggle All Advanced CLI Parameters drawer inline
    let adv_lbl = if app.show_advanced_opts {
        "▾ Hide all CLI parameters (Input OS/Arch, Stages, Assets, Oracle, Build flags)"
    } else {
        "▸ Show all CLI parameters (Input OS/Arch, Stages, Assets, Oracle, Build flags)"
    };
    box_div = box_div.child(
        div()
            .id("toggle_adv_cli_opts")
            .px(px(10.0))
            .py(px(6.0))
            .rounded(px(3.0))
            .bg(rgb(t.bg_alt))
            .border_1()
            .border_color(rgb(t.line_soft))
            .font_family(t.mono_font)
            .font_weight(FontWeight::SEMIBOLD)
            .text_size(px(t.ui_px(11.5)))
            .text_color(rgb(t.hl))
            .cursor_pointer()
            .on_click(cx.listener(|this, _, _, cx| {
                this.show_advanced_opts = !this.show_advanced_opts;
                cx.notify();
            }))
            .child(adv_lbl),
    );

    if app.show_advanced_opts {
        box_div = box_div.child(render_advanced_cli_options(app, t, cx));
    }

    // Live CLI Command Preview Bar
    let cmd_preview = opts.format_command_preview();
    let cmd_for_copy = cmd_preview.clone();
    box_div = box_div.child(
        div()
            .flex()
            .flex_col()
            .gap(px(4.0))
            .child(style::eyebrow(t, "EQUIVALENT CLI COMMAND"))
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(8.0))
                    .px(px(10.0))
                    .py(px(8.0))
                    .rounded(px(3.0))
                    .bg(rgb(t.code_bg))
                    .border_1()
                    .border_color(rgb(t.line_soft))
                    .child(
                        div()
                            .flex_1()
                            .font_family(t.mono_font)
                            .text_size(px(t.ui_px(11.0)))
                            .text_color(rgb(t.ink_2))
                            .child(cmd_preview),
                    )
                    .child(
                        style::ghost_btn("copy_cli_preview", t, "Copy").on_click(cx.listener(
                            move |this, _, _, cx| {
                                cx.write_to_clipboard(ClipboardItem::new_string(
                                    cmd_for_copy.clone(),
                                ));
                                this.push_toast(
                                    "CLI command copied",
                                    "Ready to paste into your terminal.",
                                    this.tokens().info,
                                    false,
                                );
                                cx.notify();
                            },
                        )),
                    ),
            ),
    );

    // Post-Decompile Launch Option
    box_div = box_div.child(
        style::toggle_checkbox(
            "opt_auto_launch_game",
            t,
            opts.auto_launch_after_build,
            "▶  Run game automatically right after decompiling finishes",
            Some("Launches the rebuilt native executable immediately (or opens the local WebAssembly player in your browser for web/wasm32)."),
        )
        .on_click(cx.listener(|this, _, _, cx| {
            this.opts.auto_launch_after_build = !this.opts.auto_launch_after_build;
            cx.notify();
        })),
    );

    // Parallel concurrency selector
    let active_now = app.active_running_count();
    let mut par_row = div()
        .flex()
        .flex_wrap()
        .items_center()
        .gap(px(6.0))
        .child(style::eyebrow(
            t,
            format!("PARALLEL GAMES ({active_now}/{} ACTIVE)", app.max_concurrent_jobs),
        ));
    for n in [1usize, 2, 3, 4, 6, 8] {
        par_row = par_row.child(
            style::option_pill(
                ("pick_parallel", n),
                t,
                format!("{n}"),
                app.max_concurrent_jobs == n,
            )
            .on_click(cx.listener(move |this, _, _, cx| {
                this.set_max_concurrent_jobs(n, cx);
            })),
        );
    }
    box_div = box_div.child(par_row);

    // Ownership Confirmation Checkbox
    box_div = box_div.child(
        style::toggle_checkbox(
            "opt_own_confirm",
            t,
            app.picked_owned_checked,
            "This is my own legally obtained copy of this executable.",
            Some("GameReComp does not download or redistribute game data; derived output carries the original program's rights."),
        )
        .on_click(cx.listener(|this, _, _, cx| {
            this.picked_owned_checked = !this.picked_owned_checked;
            cx.notify();
        })),
    );

    let start_label = if active_now == 0 {
        "▶  Start recompiling"
    } else if active_now < app.max_concurrent_jobs {
        "▶  Start in parallel"
    } else {
        "+  Add to parallel queue"
    };

    // Action buttons: Start recompiling / Add more / Dry-run / Cancel
    box_div = box_div.child(
        div()
            .flex()
            .flex_wrap()
            .items_center()
            .gap(px(10.0))
            .pt(px(2.0))
            .child(
                style::primary_btn("start_recomp_btn", t, start_label, can_start).when(
                    can_start,
                    |el| {
                        el.on_click(cx.listener(|this, _, _, cx| {
                            this.opts.dry_run = false;
                            this.start_picked_pipeline(cx);
                        }))
                    },
                ),
            )
            .child(
                style::ghost_btn("add_more_games_pick_btn", t, "+ Add multiple games…")
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.browse_for_multiple_pes(cx);
                    })),
            )
            .child(
                style::ghost_btn("dry_run_btn", t, "Dry-run plan").on_click(cx.listener(
                    |this, _, _, cx| {
                        if this.picked_pe_info.ok && this.picked_pe_info.pe32_x86 {
                            this.opts.dry_run = true;
                            this.start_picked_pipeline(cx);
                        }
                    },
                )),
            )
            .child(
                style::ghost_btn("cancel_pick_btn", t, "Cancel").on_click(cx.listener(
                    |this, _, _, cx| {
                        this.reset_dropzone();
                        cx.notify();
                    },
                )),
            ),
    );

    box_div
}

/// Shared panel rendering EVERY remaining GameReComp CLI parameter cleanly (reused in Home & Project Settings).
pub fn render_advanced_cli_options(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let opts = &app.opts;

    let mut stage_pills = div().flex().flex_wrap().gap(px(5.0));
    for (i, st_name) in ALL_STAGES.iter().enumerate() {
        let on = opts.selected_stages[i];
        stage_pills = stage_pills.child(
            style::option_pill(("stage_chk", i), t, *st_name, on).on_click(cx.listener(
                move |this, _, _, cx| {
                    this.opts.out_mode = OutMode::CustomStages;
                    if *st_name != "report" {
                        this.opts.selected_stages[i] = !this.opts.selected_stages[i];
                    }
                    cx.notify();
                },
            )),
        );
    }

    div()
        .flex()
        .flex_col()
        .gap(px(10.0))
        .p(px(12.0))
        .rounded(px(4.0))
        .bg(rgb(t.code_bg))
        .border_1()
        .border_color(rgb(t.line_soft))
        // Input OS & Arch
        .child(
            div()
                .flex()
                .flex_wrap()
                .items_center()
                .gap(px(8.0))
                .child(style::eyebrow(t, "INPUT OS:"))
                .children(["auto", "windows"].into_iter().enumerate().map(|(i, v)| {
                    style::option_pill(("in_os", i), t, v, opts.input_os == v).on_click(
                        cx.listener(move |this, _, _, cx| {
                            this.opts.input_os = v.to_string();
                            cx.notify();
                        }),
                    )
                }))
                .child(div().w(px(12.0)))
                .child(style::eyebrow(t, "INPUT ARCH:"))
                .children(["auto", "x86"].into_iter().enumerate().map(|(i, v)| {
                    style::option_pill(("in_arch", i), t, v, opts.input_arch == v).on_click(
                        cx.listener(move |this, _, _, cx| {
                            this.opts.input_arch = v.to_string();
                            cx.notify();
                        }),
                    )
                })),
        )
        // Output mode (`--out-mode`)
        .child(
            div()
                .flex()
                .flex_col()
                .gap(px(5.0))
                .child(style::eyebrow(t, "OUTPUT MODE (--out-mode / --stages)"))
                .child(
                    div()
                        .flex()
                        .flex_wrap()
                        .gap(px(5.0))
                        .children(
                            [
                                OutMode::Full,
                                OutMode::BinaryOnly,
                                OutMode::SourceOnly,
                                OutMode::CustomStages,
                            ]
                            .into_iter()
                            .enumerate()
                            .map(|(i, m)| {
                                style::option_pill(("out_mode", i), t, m.short(), opts.out_mode == m)
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.opts.out_mode = m;
                                        cx.notify();
                                    }))
                            }),
                        ),
                )
                .when(opts.out_mode == OutMode::CustomStages, |el| {
                    el.child(stage_pills)
                }),
        )
        // Directories: Assets, Highlevel, Oracle
        .child(
            div()
                .flex()
                .flex_col()
                .gap(px(6.0))
                .child(path_row(
                    "opt_assets",
                    t,
                    "Assets dir",
                    &opts.assets_dir,
                    "default: executable folder",
                    app.focused_input == Some(FocusInput::OptAssetsDir),
                    cx.listener(|this, _, window, cx| {
                        this.set_focus_input(Some(FocusInput::OptAssetsDir), window, cx);
                    }),
                    cx.listener(|this, _, _, cx| {
                        if let Some(d) = rfd::FileDialog::new().pick_folder() {
                            this.opts.assets_dir = d.display().to_string();
                            cx.notify();
                        }
                    }),
                ))
                .child(path_row(
                    "opt_highlevel",
                    t,
                    "Highlevel",
                    &opts.highlevel_dir,
                    "default: repo highlevel/",
                    app.focused_input == Some(FocusInput::OptHighlevelDir),
                    cx.listener(|this, _, window, cx| {
                        this.set_focus_input(Some(FocusInput::OptHighlevelDir), window, cx);
                    }),
                    cx.listener(|this, _, _, cx| {
                        if let Some(d) = rfd::FileDialog::new().pick_folder() {
                            this.opts.highlevel_dir = d.display().to_string();
                            cx.notify();
                        }
                    }),
                ))
                .child(path_row(
                    "opt_oracle",
                    t,
                    "Oracle JSON",
                    &opts.oracle_path,
                    "optional process-level oracle.json",
                    app.focused_input == Some(FocusInput::OptOraclePath),
                    cx.listener(|this, _, window, cx| {
                        this.set_focus_input(Some(FocusInput::OptOraclePath), window, cx);
                    }),
                    cx.listener(|this, _, _, cx| {
                        if let Some(f) = rfd::FileDialog::new()
                            .add_filter("JSON", &["json"])
                            .pick_file()
                        {
                            this.opts.oracle_path = f.display().to_string();
                            cx.notify();
                        }
                    }),
                )),
        )
        // Numeric parameters: Timeout, Build timeout, Jobs, Split
        .child(
            div()
                .flex()
                .flex_wrap()
                .gap(px(8.0))
                .child(num_field(
                    "opt_timeout",
                    t,
                    "Run timeout (s)",
                    &opts.timeout_secs,
                    "12",
                    app.focused_input == Some(FocusInput::OptTimeout),
                    cx.listener(|this, _, window, cx| {
                        this.set_focus_input(Some(FocusInput::OptTimeout), window, cx);
                    }),
                ))
                .child(num_field(
                    "opt_build_timeout",
                    t,
                    "Build timeout (s)",
                    &opts.build_timeout_secs,
                    "auto",
                    app.focused_input == Some(FocusInput::OptBuildTimeout),
                    cx.listener(|this, _, window, cx| {
                        this.set_focus_input(Some(FocusInput::OptBuildTimeout), window, cx);
                    }),
                ))
                .child(num_field(
                    "opt_jobs",
                    t,
                    "Compile jobs",
                    &opts.jobs,
                    "auto",
                    app.focused_input == Some(FocusInput::OptJobs),
                    cx.listener(|this, _, window, cx| {
                        this.set_focus_input(Some(FocusInput::OptJobs), window, cx);
                    }),
                ))
                .child(num_field(
                    "opt_split",
                    t,
                    "Split funcs",
                    &opts.split,
                    "1000",
                    app.focused_input == Some(FocusInput::OptSplit),
                    cx.listener(|this, _, window, cx| {
                        this.set_focus_input(Some(FocusInput::OptSplit), window, cx);
                    }),
                )),
        )
        // All boolean CLI flags
        .child(
            div()
                .flex()
                .flex_wrap()
                .gap(px(6.0))
                .child(
                    style::option_pill("flg_inter", t, "--interactive (SDL window)", opts.interactive)
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.opts.interactive = !this.opts.interactive;
                            cx.notify();
                        })),
                )
                .child(
                    style::option_pill("flg_fresh", t, "--fresh-build", opts.fresh_build).on_click(
                        cx.listener(|this, _, _, cx| {
                            this.opts.fresh_build = !this.opts.fresh_build;
                            cx.notify();
                        }),
                    ),
                )
                .child(
                    style::option_pill("flg_reuse", t, "--reuse-build", opts.reuse_build).on_click(
                        cx.listener(|this, _, _, cx| {
                            this.opts.reuse_build = !this.opts.reuse_build;
                            cx.notify();
                        }),
                    ),
                )
                .child(
                    style::option_pill("flg_trace", t, "--import-trace", opts.import_trace)
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.opts.import_trace = !this.opts.import_trace;
                            cx.notify();
                        })),
                )
                .child(
                    style::option_pill("flg_clean", t, "--clean-objects", opts.clean_objects)
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.opts.clean_objects = !this.opts.clean_objects;
                            cx.notify();
                        })),
                )
                .child(
                    style::option_pill("flg_nobuild", t, "--no-build", opts.no_build).on_click(
                        cx.listener(|this, _, _, cx| {
                            this.opts.no_build = !this.opts.no_build;
                            cx.notify();
                        }),
                    ),
                )
                .child(
                    style::option_pill("flg_norun", t, "--no-run", opts.no_run).on_click(
                        cx.listener(|this, _, _, cx| {
                            this.opts.no_run = !this.opts.no_run;
                            cx.notify();
                        }),
                    ),
                )
                .child(
                    style::option_pill("flg_table", t, "Update table.md", opts.update_table)
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.opts.update_table = !this.opts.update_table;
                            cx.notify();
                        })),
                )
                .child(
                    style::option_pill("flg_json", t, "--json", opts.json_output).on_click(
                        cx.listener(|this, _, _, cx| {
                            this.opts.json_output = !this.opts.json_output;
                            cx.notify();
                        }),
                    ),
                )
                .child(
                    style::option_pill("flg_quiet", t, "--quiet", opts.quiet).on_click(
                        cx.listener(|this, _, _, cx| {
                            this.opts.quiet = !this.opts.quiet;
                            if this.opts.quiet {
                                this.opts.enable_logs = false;
                            }
                            cx.notify();
                        }),
                    ),
                ),
        )
}

/// Multi-Game Parallel Decompilation Queue Panel: runs and monitors multiple games simultaneously!
pub fn render_active_jobs_panel(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let running_count = app.active_running_count();
    let queued_count = app.queued_pipelines.len();
    let done_count = app.jobs.len().saturating_sub(running_count);
    const SPINNER: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
    let spin_glyph = SPINNER[app.anim_frame % SPINNER.len()];

    // Concurrency selector row
    let mut par_pills = div().flex().items_center().gap(px(4.0)).child(
        div()
            .font_family(t.mono_font)
            .text_size(px(t.ui_px(10.5)))
            .text_color(rgb(t.muted))
            .child("Max parallel:"),
    );
    for n in [1usize, 2, 3, 4, 6, 8] {
        par_pills = par_pills.child(
            style::option_pill(
                ("queue_parallel", n),
                t,
                format!("{n}"),
                app.max_concurrent_jobs == n,
            )
            .on_click(cx.listener(move |this, _, _, cx| {
                this.set_max_concurrent_jobs(n, cx);
            })),
        );
    }

    let mut hdr_actions = div()
        .flex()
        .flex_wrap()
        .items_center()
        .gap(px(8.0))
        .child(par_pills)
        .child(
            style::ghost_btn("queue_add_games_btn", t, "+ Add More Games…").on_click(
                cx.listener(|this, _, _, cx| {
                    this.browse_for_multiple_pes(cx);
                }),
            ),
        );

    if done_count > 0 {
        hdr_actions = hdr_actions.child(
            style::ghost_btn("queue_clear_done_btn", t, "Clear Done").on_click(cx.listener(
                |this, _, _, cx| {
                    this.clear_finished_jobs();
                    cx.notify();
                },
            )),
        );
    }
    if running_count > 0 || queued_count > 0 {
        hdr_actions = hdr_actions.child(
            style::danger_btn("queue_stop_all_btn", t, "■ Stop All").on_click(cx.listener(
                |this, _, _, cx| {
                    this.stop_all_jobs();
                    cx.notify();
                },
            )),
        );
    }

    let mut cards_grid = div().flex().flex_wrap().gap(px(10.0)).w_full();
    let focused_id = app.focused_job().map(|j| j.id);

    for job in &app.jobs {
        let job_id = job.id;
        let is_selected = focused_id == Some(job_id);
        let pct = (job.progress_fraction() * 100.0).round() as usize;
        let elapsed = format!("{:.1}s", job.elapsed_secs());

        let (badge_el, bar_col) = match job.status {
            JobStatus::Running => (
                div()
                    .flex()
                    .items_center()
                    .gap(px(5.0))
                    .child(style::animated_pulse_dot(
                        ("job_card_dot", job_id as usize),
                        t.hl,
                        7.0,
                    ))
                    .child(style::status_badge(
                        format!("{spin_glyph} {}", job.stage.to_uppercase()),
                        t.hl,
                        t.hl_ink,
                    ))
                    .into_any_element(),
                t.hl,
            ),
            JobStatus::Done(_) => (
                style::status_badge("✓ DONE", t.match_bg, t.match_ink).into_any_element(),
                t.match_fill,
            ),
            JobStatus::Failed(c) => (
                style::status_badge(format!("✗ ERR {c}"), t.miss_bg, t.miss).into_any_element(),
                t.not_c,
            ),
            JobStatus::Stopped => (
                style::status_badge("■ STOPPED", t.work_bg, t.work).into_any_element(),
                t.work,
            ),
        };

        let mut quick_actions = div().flex().items_center().gap(px(6.0));
        if job.is_running() {
            quick_actions = quick_actions.child(
                style::danger_btn(("job_stop", job_id as usize), t, "■ Stop").on_click(
                    cx.listener(move |this, _, _, cx| {
                        this.stop_job(job_id);
                        cx.notify();
                    }),
                ),
            );
        } else if let Some(ref pdir) = job.project_dir {
            if let Ok(m) = ProjectModel::load(pdir) {
                let pdir_open = pdir.clone();
                if m.has_wasm_build {
                    let pdir_w = pdir.clone();
                    quick_actions = quick_actions.child(
                        style::primary_btn(("job_wasm", job_id as usize), t, "🌐 Play WASM", true)
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.launch_or_open_wasm(&pdir_w);
                                cx.notify();
                            })),
                    );
                } else if let Some(bin) = m.built_binary_path() {
                    quick_actions = quick_actions.child(
                        style::primary_btn(("job_launch", job_id as usize), t, "▶ Launch Game", true)
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.launch_rebuilt_binary(&bin);
                                cx.notify();
                            })),
                    );
                }
                quick_actions = quick_actions.child(
                    style::ghost_btn(("job_open", job_id as usize), t, "Inspect").on_click(
                        cx.listener(move |this, _, _, cx| {
                            this.open_project_dir(pdir_open.clone(), ProjectTab::Overview);
                            cx.notify();
                        }),
                    ),
                );
            }
        }

        let hover_bg = t.bg_alt;
        let card = div()
            .id(("job_card", job_id as usize))
            .w(px(300.0))
            .flex_grow()
            .p(px(12.0))
            .rounded(px(4.0))
            .bg(if is_selected { rgb(t.bg_alt) } else { rgb(t.panel) })
            .border_1()
            .border_color(if is_selected {
                if t.dark { rgb(t.hl) } else { rgb(t.btn) }
            } else {
                rgb(t.line)
            })
            .cursor_pointer()
            .hover(move |s| s.bg(rgb(hover_bg)))
            .on_click(cx.listener(move |this, _, _, cx| {
                this.selected_job_id = Some(job_id);
                cx.notify();
            }))
            .flex()
            .flex_col()
            .gap(px(7.0))
            .child(
                div()
                    .flex()
                    .items_center()
                    .justify_between()
                    .gap(px(6.0))
                    .child(
                        div()
                            .font_family(t.ui_font)
                            .font_weight(FontWeight::EXTRA_BOLD)
                            .text_size(px(t.ui_px(14.0)))
                            .text_color(rgb(t.ink))
                            .child(job.name.clone()),
                    )
                    .child(badge_el),
            )
            .child(
                div()
                    .font_family(t.mono_font)
                    .text_size(px(t.ui_px(10.5)))
                    .text_color(rgb(t.muted))
                    .child(format!(
                        "{} · {} files · {pct}% · {elapsed}",
                        job.target_label,
                        job.processed_files.len()
                    )),
            )
            .child(style::progress_meter(
                t,
                px(5.0),
                job.progress_fraction(),
                bar_col,
            ))
            .when(!job.current_file.is_empty(), |el| {
                el.child(
                    div()
                        .font_family(t.mono_font)
                        .text_size(px(t.ui_px(11.0)))
                        .text_color(rgb(t.ink_2))
                        .child(format!("file: {}", job.current_file)),
                )
            })
            .child(quick_actions);

        cards_grid = cards_grid.child(card);
    }

    for q in &app.queued_pipelines {
        let q_id = q.id;
        cards_grid = cards_grid.child(
            div()
                .id(("queued_card", q_id as usize))
                .w(px(260.0))
                .flex_grow()
                .p(px(12.0))
                .rounded(px(4.0))
                .bg(rgb(t.code_bg))
                .border_1()
                .border_color(rgb(t.line_soft))
                .flex()
                .flex_col()
                .gap(px(6.0))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .justify_between()
                        .gap(px(6.0))
                        .child(
                            div()
                                .font_family(t.ui_font)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(t.ui_px(13.5)))
                                .text_color(rgb(t.ink_2))
                                .child(q.name.clone()),
                        )
                        .child(style::status_badge("QUEUED", t.bg_alt, t.muted)),
                )
                .child(
                    div()
                        .font_family(t.mono_font)
                        .text_size(px(t.ui_px(10.5)))
                        .text_color(rgb(t.muted))
                        .child(q.target_label.clone()),
                )
                .child(
                    style::ghost_btn(("cancel_queued", q_id as usize), t, "Remove from queue")
                        .on_click(cx.listener(move |this, _, _, cx| {
                            this.remove_queued_job(q_id);
                            cx.notify();
                        })),
                ),
        );
    }

    let mut panel = style::panel_box(t)
        .w_full()
        .max_w(px(1340.0))
        .border_color(if running_count > 0 {
            if t.dark { rgb(t.hl) } else { rgb(t.btn) }
        } else {
            rgb(t.line)
        })
        .p(px(16.0))
        .flex()
        .flex_col()
        .gap(px(14.0))
        .child(
            div()
                .flex()
                .flex_wrap()
                .items_center()
                .justify_between()
                .gap(px(10.0))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(8.0))
                        .when(running_count > 0, |el| {
                            el.child(style::animated_pulse_dot("queue_hdr_pulse", t.hl, 9.0))
                        })
                        .child(style::eyebrow(t, "MULTI-GAME DECOMPILATION QUEUE"))
                        .child(style::status_badge(
                            format!("{running_count} running · {queued_count} queued · {done_count} finished"),
                            if running_count > 0 { t.hl_soft } else { t.bg_alt },
                            if running_count > 0 { t.ink } else { t.muted },
                        )),
                )
                .child(hdr_actions),
        )
        .child(cards_grid);

    if let Some(focused) = app.focused_job() {
        let tail_start = focused.live_logs.len().saturating_sub(12);
        let mut log_box = div()
            .id(("focused_job_logs", focused.id as usize))
            .w_full()
            .max_h(px(130.0))
            .overflow_y_scroll()
            .p(px(10.0))
            .rounded(px(3.0))
            .bg(rgb(t.code_bg))
            .border_1()
            .border_color(rgb(t.line_soft))
            .flex()
            .flex_col()
            .gap(px(2.0));
        for line in &focused.live_logs[tail_start..] {
            log_box = log_box.child(
                div()
                    .font_family(t.mono_font)
                    .text_size(px(t.code_px(11.0)))
                    .text_color(if line.starts_with("==>") {
                        rgb(t.hl)
                    } else {
                        rgb(t.muted)
                    })
                    .child(line.clone()),
            );
        }

        panel = panel.child(
            div()
                .pt(px(10.0))
                .border_t_1()
                .border_color(rgb(t.line_soft))
                .flex()
                .flex_col()
                .gap(px(10.0))
                .child(render_live_stage_stepper(focused, app.anim_frame, t, cx))
                .child(log_box),
        );
    }

    panel
}

/// Animated live pipeline stepper + active file spotlight + per-file telemetry stream for a job!
pub fn render_live_stage_stepper(
    job: &ActiveJob,
    anim_frame: usize,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    const SPINNER: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
    let spin_glyph = if job.is_running() {
        SPINNER[anim_frame % SPINNER.len()]
    } else if matches!(job.status, JobStatus::Done(_)) {
        "✓"
    } else {
        "■"
    };

    let elapsed_total = format!("{:.1}s", job.elapsed_secs());
    let frac = job.progress_fraction();
    let pct_int = (frac * 100.0).round() as usize;
    let jid = job.id as usize;

    // Animated 6-bar equalizer activity indicator driven by anim_frame
    let eq_heights = [
        [6.0, 12.0, 18.0, 10.0, 15.0, 8.0],
        [10.0, 18.0, 11.0, 16.0, 9.0, 14.0],
        [16.0, 11.0, 7.0, 18.0, 13.0, 10.0],
        [11.0, 7.0, 15.0, 12.0, 18.0, 16.0],
        [7.0, 14.0, 18.0, 8.0, 11.0, 12.0],
        [14.0, 16.0, 9.0, 14.0, 7.0, 18.0],
    ];
    let bars = if job.is_running() {
        eq_heights[(anim_frame / 2) % eq_heights.len()]
    } else {
        [6.0, 6.0, 6.0, 6.0, 6.0, 6.0]
    };
    let mut eq = div().flex().items_end().gap(px(2.5)).h(px(18.0));
    for (b_idx, h_val) in bars.into_iter().enumerate() {
        let col = if b_idx % 2 == 0 {
            if t.dark { t.hl } else { t.btn }
        } else {
            t.match_fill
        };
        eq = eq.child(
            div()
                .w(px(3.0))
                .h(px(h_val))
                .rounded(px(1.5))
                .bg(rgb(col)),
        );
    }

    // Active File Spotlight Card (shows exact file + live sub-step being processed!)
    let active_file_name = if job.current_file.is_empty() {
        format!("stage: {}", job.stage)
    } else {
        job.current_file.clone()
    };
    let active_detail = if job.current_task_detail.is_empty() {
        "Preparing decompilation task...".to_string()
    } else {
        job.current_task_detail.clone()
    };
    let active_spotlight = div()
        .w_full()
        .px(px(12.0))
        .py(px(10.0))
        .rounded(px(4.0))
        .bg(rgb(t.code_bg))
        .border_1()
        .border_color(if t.dark { rgb(t.hl) } else { rgb(t.btn) })
        .flex()
        .flex_col()
        .gap(px(5.0))
        .child(
            div()
                .flex()
                .flex_wrap()
                .items_center()
                .justify_between()
                .gap(px(8.0))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(8.0))
                        .when(job.is_running(), |el| {
                            el.child(style::animated_pulse_dot(
                                ("active_file_dot", jid),
                                t.match_fill,
                                8.0,
                            ))
                        })
                        .child(style::status_badge("ACTIVE FILE", t.hl, t.hl_ink))
                        .child(
                            div()
                                .font_family(t.mono_font)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(t.ui_px(12.5)))
                                .text_color(rgb(t.ink))
                                .child(active_file_name),
                        ),
                )
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(8.0))
                        .child(style::chip_outlined(
                            t,
                            format!("{} file(s) touched", job.processed_files.len()),
                            Some(t.info),
                        ))
                        .child(style::chip_outlined(
                            t,
                            format!("{pct_int}%"),
                            Some(t.match_ink),
                        )),
                ),
        )
        .child(
            div()
                .flex()
                .items_center()
                .gap(px(8.0))
                .child(
                    div()
                        .font_family(t.mono_font)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(t.ui_px(12.0)))
                        .text_color(if t.dark { rgb(t.hl) } else { rgb(t.info) })
                        .child(spin_glyph),
                )
                .child(
                    div()
                        .font_family(t.mono_font)
                        .text_size(px(t.ui_px(11.5)))
                        .text_color(rgb(t.ink_2))
                        .child(active_detail),
                ),
        );

    let mut rows = div().flex().flex_col().gap(px(5.0)).w_full();
    for (i, (stage_name, desc)) in STAGE_DESCRIPTIONS.iter().enumerate() {
        let st = job.live_stages.get(i);
        let state = st.map(|s| &s.state).unwrap_or(&StageExecState::Pending);
        let detail = st.map(|s| s.detail.as_str()).unwrap_or("");

        let (border_col, bg_col, badge_el) = match state {
            StageExecState::Done(secs) => (
                rgb(t.line_soft),
                rgb(t.bg_alt),
                div()
                    .flex()
                    .items_center()
                    .gap(px(6.0))
                    .child(style::status_badge("✓ DONE", t.match_bg, t.match_ink))
                    .child(
                        div()
                            .font_family(t.mono_font)
                            .text_size(px(t.ui_px(10.5)))
                            .text_color(rgb(t.muted))
                            .child(format!("{secs:.1}s")),
                    )
                    .into_any_element(),
            ),
            StageExecState::Running(started) => (
                if t.dark { rgb(t.hl) } else { rgb(t.btn) },
                rgb(t.hl_soft),
                div()
                    .flex()
                    .items_center()
                    .gap(px(6.0))
                    .child(style::animated_pulse_dot(
                        ("stage_pulse", jid * 16 + i),
                        t.match_fill,
                        8.0,
                    ))
                    .child(style::status_badge(
                        format!("{spin_glyph} RUNNING"),
                        t.hl,
                        t.hl_ink,
                    ))
                    .child(
                        div()
                            .font_family(t.mono_font)
                            .font_weight(FontWeight::BOLD)
                            .text_size(px(t.ui_px(10.5)))
                            .text_color(rgb(t.ink))
                            .child(format!("{:.1}s", started.elapsed().as_secs_f32())),
                    )
                    .into_any_element(),
            ),
            StageExecState::Failed => (
                rgb(t.not_c),
                rgb(t.miss_bg),
                style::status_badge("✗ FAILED", t.miss_bg, t.miss).into_any_element(),
            ),
            StageExecState::Pending => (
                rgb(t.line_soft),
                rgb(t.panel),
                div()
                    .font_family(t.mono_font)
                    .text_size(px(t.ui_px(10.5)))
                    .text_color(rgb(t.muted))
                    .child(format!("{}/8", i + 1))
                    .into_any_element(),
            ),
        };

        let sub_text = if !detail.is_empty() {
            detail.to_string()
        } else if matches!(state, StageExecState::Running(_)) && !job.current_file.is_empty() {
            format!("{}  ·  {}", desc, job.current_file)
        } else {
            (*desc).to_string()
        };

        rows = rows.child(
            div()
                .flex()
                .items_center()
                .justify_between()
                .gap(px(10.0))
                .px(px(10.0))
                .py(px(7.0))
                .rounded(px(3.0))
                .bg(bg_col)
                .border_1()
                .border_color(border_col)
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(10.0))
                        .child(
                            div()
                                .w(px(78.0))
                                .font_family(t.mono_font)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(t.ui_px(12.0)))
                                .text_color(rgb(t.ink))
                                .child(*stage_name),
                        )
                        .child(
                            div()
                                .font_family(t.ui_font)
                                .text_size(px(t.ui_px(12.0)))
                                .text_color(rgb(t.ink_2))
                                .child(sub_text),
                        ),
                )
                .child(badge_el),
        );
    }

    // Per-file live activity stream showing every generated/compiled/verified file
    let mut files_box = div()
        .w_full()
        .p(px(10.0))
        .rounded(px(4.0))
        .bg(rgb(t.code_bg))
        .border_1()
        .border_color(rgb(t.line_soft))
        .flex()
        .flex_col()
        .gap(px(5.0))
        .child(
            div()
                .flex()
                .items_center()
                .justify_between()
                .child(style::eyebrow(
                    t,
                    format!("LIVE FILE TELEMETRY ({} FILES)", job.processed_files.len()),
                ))
                .child(
                    div()
                        .font_family(t.mono_font)
                        .text_size(px(t.ui_px(10.5)))
                        .text_color(rgb(t.muted))
                        .child("newest first"),
                ),
        );

    if job.processed_files.is_empty() {
        files_box = files_box.child(
            div()
                .font_family(t.mono_font)
                .text_size(px(t.ui_px(11.0)))
                .text_color(rgb(t.muted))
                .child("Waiting for first generated / compiled file event…"),
        );
    } else {
        for (idx, item) in job.processed_files.iter().rev().take(8).enumerate() {
            let (badge_bg, badge_fg) = match item.action {
                "CC" | "LINK" => (t.info_bg, t.info),
                "GEN" | "HL" => (t.match_bg, t.match_ink),
                "WASM" => (t.hl_soft, t.ink),
                "FRAME" | "REPORT" => (t.work_bg, t.work),
                _ => (t.bg_alt, t.ink_2),
            };
            files_box = files_box.child(
                div()
                    .flex()
                    .items_center()
                    .justify_between()
                    .gap(px(8.0))
                    .px(px(8.0))
                    .py(px(4.0))
                    .rounded(px(3.0))
                    .when(idx == 0, |el| el.bg(rgb(t.hl_soft)))
                    .child(
                        div()
                            .flex()
                            .items_center()
                            .gap(px(8.0))
                            .child(style::status_badge(item.action, badge_bg, badge_fg))
                            .child(
                                div()
                                    .font_family(t.mono_font)
                                    .font_weight(if idx == 0 {
                                        FontWeight::BOLD
                                    } else {
                                        FontWeight::MEDIUM
                                    })
                                    .text_size(px(t.ui_px(11.5)))
                                    .text_color(rgb(t.ink))
                                    .child(item.path.clone()),
                            )
                            .child(
                                div()
                                    .font_family(t.ui_font)
                                    .text_size(px(t.ui_px(11.0)))
                                    .text_color(rgb(t.muted))
                                    .child(item.detail.clone()),
                            ),
                    )
                    .child(
                        div()
                            .font_family(t.mono_font)
                            .text_size(px(t.ui_px(10.0)))
                            .text_color(rgb(t.muted))
                            .child(item.timestamp.clone()),
                    ),
            );
        }
    }

    let job_id_stop = job.id;
    let is_running = job.is_running();
    div()
        .w_full()
        .flex()
        .flex_col()
        .gap(px(10.0))
        .child(
            div()
                .flex()
                .items_center()
                .justify_between()
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(10.0))
                        .child(eq)
                        .child(
                            div()
                                .font_family(t.ui_font)
                                .font_weight(FontWeight::EXTRA_BOLD)
                                .text_size(px(t.ui_px(17.5)))
                                .text_color(rgb(t.ink))
                                .child(format!(
                                    "{spin_glyph}  {} · {} ({})",
                                    job.name,
                                    job.kind.label(),
                                    job.stage
                                )),
                        ),
                )
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(8.0))
                        .child(
                            div()
                                .font_family(t.mono_font)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(t.ui_px(12.0)))
                                .text_color(rgb(t.ink))
                                .child(elapsed_total),
                        )
                        .when(is_running, |el| {
                            el.child(
                                style::danger_btn(("stop_stepper_btn", jid), t, "■ Stop").on_click(
                                    cx.listener(move |this, _, _, cx| {
                                        this.stop_job(job_id_stop);
                                        cx.notify();
                                    }),
                                ),
                            )
                        }),
                ),
        )
        .child(style::progress_meter(t, px(7.0), frac, t.match_fill))
        .when(is_running, |el| {
            el.child(style::animated_workbar(("busy_shimmer_bar", jid), t, 4.0))
        })
        .child(active_spotlight)
        .child(rows)
        .child(files_box)
}

fn render_drop_busy(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let mut col = div().w_full().flex().flex_col().gap(px(10.0));
    if let Some(job) = app.focused_job() {
        col = col.child(render_live_stage_stepper(job, app.anim_frame, t, cx));
    } else {
        col = col.child(
            div()
                .font_family(t.ui_font)
                .text_size(px(t.ui_px(13.0)))
                .text_color(rgb(t.muted))
                .child("No active decompilation job."),
        );
    }
    col
}

fn render_drop_error(
    app: &GameReCompApp,
    t: &ThemeTokens,
    err: &str,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let logs_joined = app
        .focused_job()
        .map(|j| j.live_logs.join("\n"))
        .unwrap_or_default();
    div()
        .w_full()
        .flex()
        .flex_col()
        .gap(px(12.0))
        .child(
            div()
                .font_family(t.ui_font)
                .font_weight(FontWeight::EXTRA_BOLD)
                .text_size(px(t.ui_px(19.0)))
                .text_color(rgb(t.ink))
                .child("Couldn't complete recompilation"),
        )
        .child(
            div()
                .font_family(t.ui_font)
                .text_size(px(t.ui_px(13.0)))
                .text_color(rgb(t.miss))
                .child(err.to_string()),
        )
        .child(
            div()
                .flex()
                .gap(px(8.0))
                .child(
                    style::primary_btn("err_try_again", t, "Try another file", true).on_click(
                        cx.listener(|this, _, _, cx| {
                            this.reset_dropzone();
                            cx.notify();
                        }),
                    ),
                )
                .child(
                    style::ghost_btn("err_copy_log", t, "Copy log").on_click(cx.listener(
                        move |this, _, _, cx| {
                            cx.write_to_clipboard(ClipboardItem::new_string(logs_joined.clone()));
                            this.push_toast(
                                "Log copied",
                                "Pipeline log copied to clipboard.",
                                this.tokens().info,
                                false,
                            );
                            cx.notify();
                        },
                    )),
                ),
        )
}

fn start_card(
    id: &'static str,
    t: &ThemeTokens,
    accent: bool,
    title: &'static str,
    desc: &str,
) -> gpui::Stateful<Div> {
    let ink = t.ink;
    div()
        .id(id)
        .flex_1()
        .min_w(px(260.0))
        .flex()
        .flex_col()
        .gap(px(6.0))
        .p(px(16.0))
        .rounded(px(4.0))
        .bg(rgb(t.panel))
        .border_1()
        .border_color(rgb(t.line))
        .border_t_3()
        .border_color(if accent { rgb(t.hl) } else { rgb(t.ink) })
        .cursor_pointer()
        .hover(move |s| s.border_color(rgb(ink)))
        .child(
            div()
                .font_family(t.ui_font)
                .font_weight(FontWeight::EXTRA_BOLD)
                .text_size(px(t.ui_px(15.5)))
                .text_color(rgb(t.ink))
                .child(title),
        )
        .child(
            div()
                .font_family(t.ui_font)
                .text_size(px(t.ui_px(12.5)))
                .line_height(px(18.0))
                .text_color(rgb(t.muted))
                .child(desc.to_string()),
        )
}

fn plat_chip(t: &ThemeTokens, label: &'static str, rec: bool) -> Div {
    div()
        .px(px(8.0))
        .py(px(4.0))
        .rounded(px(2.0))
        .bg(rgb(t.bg_alt))
        .border_1()
        .border_color(if rec {
            rgb(t.match_fill)
        } else {
            rgb(t.line)
        })
        .font_family(t.mono_font)
        .font_weight(FontWeight::BOLD)
        .text_size(px(t.ui_px(10.5)))
        .text_color(if rec { rgb(t.ink) } else { rgb(t.muted) })
        .child(label)
}

fn fact(t: &ThemeTokens, k: &'static str, v: &str) -> Div {
    div()
        .flex()
        .flex_col()
        .gap(px(2.0))
        .child(style::eyebrow(t, k))
        .child(
            div()
                .font_family(t.mono_font)
                .font_weight(FontWeight::SEMIBOLD)
                .text_size(px(t.ui_px(13.0)))
                .text_color(rgb(t.ink))
                .child(v.to_string()),
        )
}

#[allow(clippy::too_many_arguments)]
fn path_row(
    id: &'static str,
    t: &ThemeTokens,
    label: &'static str,
    val: &str,
    hint: &'static str,
    focused: bool,
    on_focus: impl Fn(&gpui::ClickEvent, &mut gpui::Window, &mut gpui::App) + 'static,
    on_browse: impl Fn(&gpui::ClickEvent, &mut gpui::Window, &mut gpui::App) + 'static,
) -> Div {
    div()
        .flex()
        .items_center()
        .gap(px(8.0))
        .child(
            div()
                .w(px(86.0))
                .font_family(t.mono_font)
                .text_size(px(t.ui_px(11.0)))
                .text_color(rgb(t.muted))
                .child(label),
        )
        .child(style::text_input_box(id, t, val, hint, focused, true).on_click(on_focus))
        .child(style::ghost_btn((id, 1usize), t, "…").on_click(on_browse))
}

fn num_field(
    id: &'static str,
    t: &ThemeTokens,
    label: &'static str,
    val: &str,
    hint: &'static str,
    focused: bool,
    on_focus: impl Fn(&gpui::ClickEvent, &mut gpui::Window, &mut gpui::App) + 'static,
) -> Div {
    div()
        .w(px(118.0))
        .flex()
        .flex_col()
        .gap(px(3.0))
        .child(
            div()
                .font_family(t.mono_font)
                .text_size(px(t.ui_px(10.0)))
                .text_color(rgb(t.muted))
                .child(label),
        )
        .child(style::text_input_box(id, t, val, hint, focused, true).on_click(on_focus))
}
