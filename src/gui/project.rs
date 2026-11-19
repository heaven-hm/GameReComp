use std::collections::HashMap;

use gpui::{
    ClipboardItem, Context, Div, FontWeight, Styled, div, img, prelude::*, px, relative, rgb,
};

use super::home::{render_advanced_cli_options, render_live_stage_stepper};
use super::style::{self, FONT_MONO, FONT_SANS};
use super::{FnFilter, FnSort, FocusInput, GameReCompApp, MapColorMode, ProjectTab, Route, WorkerKind};
use crate::model::{FunctionRow, ProjectModel};
use crate::pipeline_opts::AppProfile;
use crate::theme_tokens::ThemeTokens;

pub fn render_project(
    app: &GameReCompApp,
    t: &ThemeTokens,
    tab: ProjectTab,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let Some(model) = app.active_project.as_ref() else {
        return div()
            .flex_1()
            .h_full()
            .p(px(36.0))
            .flex()
            .flex_col()
            .gap(px(12.0))
            .child(
                div()
                    .font_family(FONT_SANS)
                    .font_weight(FontWeight::EXTRA_BOLD)
                    .text_size(px(24.0))
                    .text_color(rgb(t.ink))
                    .child("No project loaded"),
            )
            .when(!app.load_error.is_empty(), |el| {
                el.child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(13.5))
                        .text_color(rgb(t.miss))
                        .child(app.load_error.clone()),
                )
            })
            .child(
                div().child(
                    style::primary_btn("back_home_empty", t, "Back to New Project", true).on_click(
                        cx.listener(|this, _, _, cx| {
                            this.route = Route::Home;
                            cx.notify();
                        }),
                    ),
                ),
            );
    };

    let body = match tab {
        ProjectTab::Overview => render_overview_tab(app, model, t, cx),
        ProjectTab::Functions => render_functions_tab(app, model, t, cx),
        ProjectTab::StagesBuild => render_stages_build_tab(app, model, t, cx),
        ProjectTab::Agents => render_agents_tab(app, model, t, cx),
        ProjectTab::Frames => render_frames_tab(app, model, t, cx),
        ProjectTab::Settings => render_project_settings_tab(app, model, t, cx),
    };

    div()
        .flex_1()
        .h_full()
        .flex()
        .flex_col()
        .overflow_hidden()
        .child(render_project_header(app, model, t, tab, cx))
        .child(
            div()
                .id(("project_tab_scroll", tab as usize))
                .flex_1()
                .overflow_y_scroll()
                .px(px(32.0))
                .pt(px(20.0))
                .pb(px(56.0))
                .child(div().w_full().max_w(px(1420.0)).child(body)),
        )
}

fn render_project_header(
    app: &GameReCompApp,
    model: &ProjectModel,
    t: &ThemeTokens,
    active_tab: ProjectTab,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let pe_chip = format!(
        "{} {}",
        if model.inspect.pe_type.is_empty() {
            "PE32"
        } else {
            &model.inspect.pe_type
        },
        if model.inspect.machine.is_empty() {
            "x86"
        } else {
            &model.inspect.machine
        }
    );
    let target_chip = if model.has_wasm_build || model.target_arch == "wasm32" {
        "web/wasm32".to_string()
    } else {
        format!("{}/{}", model.target_os, model.target_arch)
    };

    let mut chips = div()
        .flex()
        .flex_wrap()
        .items_center()
        .gap(px(6.0))
        .child(style::chip_inverted(t, pe_chip));
    if !model.inspect.subsystem.is_empty() {
        chips = chips.child(style::chip_outlined(t, model.inspect.subsystem.clone(), None));
    }
    if !model.inspect.image_base.is_empty() {
        chips = chips.child(style::chip_outlined(t, model.inspect.image_base.clone(), None));
    }
    chips = chips
        .child(style::chip_outlined(t, model.runtime.clone(), None))
        .child(style::chip_outlined(t, target_chip, None));
    if !model.verification.is_empty() {
        let acc = match model.verification.as_str() {
            "verified" | "pass" | "scenario-pass" => Some(t.match_ink),
            "partial" | "boot-passed" => Some(t.work),
            _ => Some(t.miss),
        };
        chips = chips.child(style::chip_outlined(t, model.verification.clone(), acc));
    }

    // Title row (yellow marker title or inline rename input)
    let title_row = if app.renaming_project {
        div()
            .flex()
            .items_center()
            .gap(px(8.0))
            .child(
                div().w(px(260.0)).child(
                    style::text_input_box(
                        "proj_rename_input",
                        t,
                        &app.rename_buf,
                        "Project title",
                        app.focused_input == Some(FocusInput::ProjectRename),
                        false,
                    )
                    .on_click(cx.listener(|this, _, window, cx| {
                        this.set_focus_input(Some(FocusInput::ProjectRename), window, cx);
                    })),
                ),
            )
            .child(
                style::primary_btn("proj_rename_save", t, "Save", true).on_click(cx.listener(
                    |this, _, _, cx| {
                        this.commit_project_rename();
                        cx.notify();
                    },
                )),
            )
            .child(
                style::ghost_btn("proj_rename_cancel", t, "Cancel").on_click(cx.listener(
                    |this, _, _, cx| {
                        this.renaming_project = false;
                        cx.notify();
                    },
                )),
            )
    } else {
        let cur_title = model.display_title().to_string();
        div()
            .flex()
            .items_center()
            .gap(px(8.0))
            .child(style::yellow_project_title(t, &cur_title))
            .child(
                div()
                    .id("proj_rename_btn")
                    .px(px(6.0))
                    .py(px(4.0))
                    .rounded(px(3.0))
                    .text_size(px(15.0))
                    .text_color(rgb(t.muted))
                    .cursor_pointer()
                    .hover({
                        let ink = t.ink;
                        move |s| s.text_color(rgb(ink))
                    })
                    .on_click(cx.listener(move |this, _, window, cx| {
                        this.rename_buf = cur_title.clone();
                        this.renaming_project = true;
                        this.set_focus_input(Some(FocusInput::ProjectRename), window, cx);
                    }))
                    .child("✎"),
            )
    };

    // Right actions row
    let mut actions = div().flex().flex_wrap().items_center().gap(px(8.0));

    if let Some(kind) = app.running_kind {
        actions = actions
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(7.0))
                    .h(px(32.0))
                    .px(px(12.0))
                    .rounded(px(3.0))
                    .bg(rgb(t.hl))
                    .text_color(rgb(t.hl_ink))
                    .font_family(FONT_MONO)
                    .font_weight(FontWeight::BOLD)
                    .text_size(px(12.0))
                    .child(style::animated_pulse_dot("hdr_working_dot", t.hl_ink, 7.0))
                    .child(format!("{}: {}", kind.label(), app.running_stage)),
            )
            .child(
                style::danger_btn("hdr_stop_btn", t, "■ Stop").on_click(cx.listener(
                    |this, _, _, cx| {
                        this.stop_running_worker();
                        cx.notify();
                    },
                )),
            );
    } else {
        if model.has_wasm_build {
            let dir = model.dir.clone();
            let lbl = if app.wasm_server_url.is_some() {
                "🌐 Open WASM"
            } else {
                "🌐 Serve WASM"
            };
            actions = actions.child(style::ghost_btn("hdr_wasm_btn", t, lbl).on_click(
                cx.listener(move |this, _, _, cx| {
                    this.launch_or_open_wasm(&dir);
                    cx.notify();
                }),
            ));
        } else if let Some(bin) = model.built_binary_path() {
            actions = actions.child(style::ghost_btn("hdr_launch_btn", t, "▶ Launch").on_click(
                cx.listener(move |this, _, _, cx| {
                    this.launch_rebuilt_binary(&bin);
                    cx.notify();
                }),
            ));
        }

        if model.has_html_report {
            let html = model.dir.join("reports").join("report.html");
            actions = actions.child(style::ghost_btn("hdr_report_btn", t, "↗ Report").on_click(
                cx.listener(move |_, _, _, _| {
                    let _ = open::that(&html);
                }),
            ));
        }

        actions = actions.child(
            style::primary_btn("hdr_run_btn", t, "▶  Run pipeline", true).on_click(cx.listener(
                |this, _, _, cx| {
                    this.start_active_project_pipeline(cx);
                },
            )),
        );
    }

    let proj_dir = model.dir.clone();
    actions = actions.child(style::ghost_btn("hdr_dir_btn", t, "📁").on_click(cx.listener(
        move |_, _, _, _| {
            let _ = open::that(&proj_dir);
        },
    )));

    // Progress meter row
    let pct = (model.score.clamp(0, 100) as f32) / 100.0;
    let digest_str = if !model.build.present {
        "no manifest"
    } else if model.build.digests_match {
        "digests match"
    } else {
        "source modified"
    };
    let progress_row = div()
        .flex()
        .flex_wrap()
        .items_center()
        .gap(px(12.0))
        .mt(px(12.0))
        .child(
            div()
                .w(px(300.0))
                .child(style::progress_meter(t, px(8.0), pct, t.match_fill)),
        )
        .child(
            div()
                .flex()
                .items_baseline()
                .gap(px(4.0))
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(16.0))
                        .text_color(rgb(t.ink))
                        .child(format!("{}%", model.score.clamp(0, 100))),
                )
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(12.5))
                        .text_color(rgb(t.ink_2))
                        .child("score"),
                ),
        )
        .child(
            div()
                .font_family(FONT_MONO)
                .text_size(px(11.5))
                .text_color(rgb(t.muted))
                .child(format!(
                    "{} functions  ·  {} verified  ·  {}/{} stages  ·  {}",
                    model.all_functions.len(),
                    model.verified_count(),
                    model.stage_ok_count(),
                    model.stages.len(),
                    digest_str
                )),
        );

    // Sub-tabs row
    let tabs = [
        (ProjectTab::Overview, "Overview".to_string()),
        (
            ProjectTab::Functions,
            format!("Functions  {}", model.all_functions.len()),
        ),
        (ProjectTab::StagesBuild, "Stages & Build".to_string()),
        (ProjectTab::Agents, "Agents".to_string()),
        (
            ProjectTab::Frames,
            if model.frames.is_empty() {
                "Frames".to_string()
            } else {
                format!("Frames  {}", model.frames.len())
            },
        ),
        (ProjectTab::Settings, "Settings".to_string()),
    ];

    let mut tab_bar = div().flex().items_center().gap(px(4.0)).mt(px(10.0));
    for (tb, label) in tabs {
        let on = tb == active_tab;
        tab_bar = tab_bar.child(
            div()
                .id(("ptab", tb as usize))
                .px(px(12.0))
                .h(px(36.0))
                .flex()
                .items_center()
                .border_b_2()
                .border_color(if on { rgb(t.ink) } else { rgb(t.bg) })
                .font_family(FONT_SANS)
                .font_weight(if on {
                    FontWeight::BOLD
                } else {
                    FontWeight::MEDIUM
                })
                .text_size(px(13.5))
                .text_color(if on { rgb(t.ink) } else { rgb(t.muted) })
                .cursor_pointer()
                .hover({
                    let ink = t.ink;
                    move |s| s.text_color(rgb(ink))
                })
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.route = Route::Project(tb);
                    cx.notify();
                }))
                .child(label),
        );
    }

    div()
        .w_full()
        .bg(rgb(t.bg))
        .border_b_1()
        .border_color(rgb(t.line_soft))
        .px(px(32.0))
        .pt(px(18.0))
        .flex()
        .flex_col()
        .child(
            div()
                .flex()
                .flex_wrap()
                .items_start()
                .justify_between()
                .gap(px(14.0))
                .child(div().flex().flex_col().gap(px(7.0)).child(chips).child(title_row))
                .child(actions),
        )
        .child(progress_row)
        .child(tab_bar)
}

// ============================================================================
// Tab 1: Overview (Live Animated Stepper + Function Map + 4 Ruled KPIs + Next + Bottom 2-col)
// ============================================================================
fn render_overview_tab(
    app: &GameReCompApp,
    model: &ProjectModel,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let mut col = div().flex().flex_col().gap(px(22.0));

    // When a pipeline/agent job is actively running, display the animated live stage stepper prominently!
    if app.running_kind.is_some() {
        col = col.child(
            style::panel_box(t)
                .border_color(if t.dark { rgb(t.hl) } else { rgb(t.btn) })
                .p(px(16.0))
                .child(render_live_stage_stepper(app, t, cx)),
        );
    } else if app.last_finished_project.as_deref() == Some(model.dir.as_path()) {
        // Post-decompile ready-to-play action banner!
        let bin_opt = model.built_binary_path();
        let has_wasm = model.has_wasm_build;
        let proj_dir_wasm = model.dir.clone();
        let proj_dir_open = model.dir.clone();
        let mut actions = div().flex().flex_wrap().items_center().gap(px(8.0));
        if let Some(bin) = bin_opt {
            actions = actions.child(
                style::primary_btn("post_launch_game_btn", t, "▶  Launch Game Now", true)
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.launch_rebuilt_binary(&bin);
                        cx.notify();
                    })),
            );
        }
        if has_wasm {
            actions = actions.child(
                style::primary_btn("post_launch_wasm_btn", t, "🌐  Play in Browser (WASM)", true)
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.launch_or_open_wasm(&proj_dir_wasm);
                        cx.notify();
                    })),
            );
        }
        actions = actions
            .child(
                style::ghost_btn("post_open_dir_btn", t, "Open Output Folder").on_click(
                    cx.listener(move |_, _, _, _| {
                        let _ = open::that(&proj_dir_open);
                    }),
                ),
            )
            .child(
                style::ghost_btn("post_dismiss_btn", t, "Dismiss").on_click(cx.listener(
                    |this, _, _, cx| {
                        this.last_finished_project = None;
                        cx.notify();
                    },
                )),
            );

        col = col.child(
            div()
                .w_full()
                .p(px(16.0))
                .rounded(px(4.0))
                .bg(rgb(t.match_bg))
                .border_1()
                .border_color(rgb(t.match_fill))
                .flex()
                .flex_wrap()
                .items_center()
                .justify_between()
                .gap(px(12.0))
                .child(
                    div()
                        .flex()
                        .flex_col()
                        .gap(px(3.0))
                        .child(
                            div()
                                .flex()
                                .items_center()
                                .gap(px(8.0))
                                .child(style::status_badge("✓ READY TO PLAY", t.match_fill, t.bg))
                                .child(
                                    div()
                                        .font_family(FONT_SANS)
                                        .font_weight(FontWeight::EXTRA_BOLD)
                                        .text_size(px(15.5))
                                        .text_color(rgb(t.match_ink))
                                        .child("Decompilation & build finished!"),
                                ),
                        )
                        .child(
                            div()
                                .font_family(FONT_SANS)
                                .text_size(px(12.5))
                                .text_color(rgb(t.ink_2))
                                .child(format!(
                                    "{} file(s) processed · score {}/100 · click Launch to run your rebuilt game.",
                                    app.processed_files.len(),
                                    model.score
                                )),
                        ),
                )
                .child(actions),
        );
    }

    // -------------------------------------------------------- Function Map Panel
    let map_header = div()
        .flex()
        .flex_wrap()
        .items_center()
        .justify_between()
        .gap(px(10.0))
        .px(px(16.0))
        .py(px(12.0))
        .border_b_1()
        .border_color(rgb(t.line_soft))
        .child(
            div()
                .font_family(FONT_SANS)
                .font_weight(FontWeight::BOLD)
                .text_size(px(15.0))
                .text_color(rgb(t.ink))
                .child("Function map"),
        )
        .child(
            div()
                .flex()
                .flex_wrap()
                .items_center()
                .gap(px(10.0))
                .child(
                    div()
                        .flex()
                        .gap(px(4.0))
                        .p(px(2.0))
                        .rounded(px(3.0))
                        .bg(rgb(t.bg_alt))
                        .border_1()
                        .border_color(rgb(t.line_soft))
                        .child(
                            style::option_pill(
                                "map_mode_prov",
                                t,
                                "Provenance",
                                app.map_mode == MapColorMode::Provenance,
                            )
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.map_mode = MapColorMode::Provenance;
                                cx.notify();
                            })),
                        )
                        .child(
                            style::option_pill(
                                "map_mode_mod",
                                t,
                                "Module",
                                app.map_mode == MapColorMode::Module,
                            )
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.map_mode = MapColorMode::Module;
                                cx.notify();
                            })),
                        ),
                )
                .child(legend_swatch(t, t.match_fill, "verified"))
                .child(legend_swatch(t, t.polished, "high-level"))
                .child(legend_swatch(t, t.match_hi, "lifted"))
                .child(legend_swatch(t, t.tried_cell, "thunk"))
                .child(legend_swatch(t, t.not_c, "unresolved"))
                .child(
                    style::ghost_btn("map_reload_btn", t, "↻ Reload").on_click(cx.listener(
                        |this, _, _, cx| {
                            this.reload_active_project();
                            cx.notify();
                        },
                    )),
                ),
        );

    let map_panel = style::panel_box(t)
        .child(map_header)
        .child(
            div()
                .p(px(16.0))
                .flex()
                .flex_col()
                .gap(px(10.0))
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(12.5))
                        .text_color(rgb(t.muted))
                        .child("Every square is a recovered function in address order. Click any square to open its C source and provenance drawer."),
                )
                .child(render_function_map_squares(app, model, t, cx)),
        );

    col = col.child(map_panel);

    // -------------------------------------------------------- 4 Ruled KPI Columns
    let total_fns = model.all_functions.len();
    let verified_fns = model.verified_count();
    let hl_fns = model.high_level_count();
    let unres_fns = model.unresolved_count();
    let lifted_fns = total_fns.saturating_sub(hl_fns + unres_fns);

    // Stacked bar proportions
    let tot_f = (total_fns.max(1)) as f32;
    let stacked_bar = div()
        .w_full()
        .h(px(8.0))
        .rounded(px(2.0))
        .bg(rgb(t.open_cell))
        .overflow_hidden()
        .flex()
        .child(
            div()
                .h_full()
                .w(relative((verified_fns as f32) / tot_f))
                .bg(rgb(t.match_fill)),
        )
        .child(
            div()
                .h_full()
                .w(relative((hl_fns.saturating_sub(verified_fns) as f32) / tot_f))
                .bg(rgb(t.polished)),
        )
        .child(
            div()
                .h_full()
                .w(relative((lifted_fns as f32) / tot_f))
                .bg(rgb(t.match_hi)),
        )
        .child(
            div()
                .h_full()
                .w(relative((unres_fns as f32) / tot_f))
                .bg(rgb(t.not_c)),
        );

    // 8 vertical stage mini-bars
    let mut stage_bars = div().w_full().h(px(26.0)).flex().items_end().gap(px(4.0));
    for i in 0..8 {
        let st = model.stages.get(i).map(|s| s.status.as_str()).unwrap_or("pending");
        let (h_frac, bar_col) = match st {
            "ok" => (1.0, t.match_fill),
            "running" => (0.72, t.hl),
            "failed" => (0.85, t.not_c),
            _ => (0.24, t.open_cell),
        };
        stage_bars = stage_bars.child(
            div()
                .flex_1()
                .h(relative(h_frac))
                .rounded(px(1.5))
                .bg(rgb(bar_col)),
        );
    }

    let kpi_row = div()
        .w_full()
        .flex()
        .flex_row()
        .flex_wrap()
        .gap(px(26.0))
        .child(
            style::ruled_kpi(t, "VERIFICATION SCORE")
                .child(
                    div()
                        .flex()
                        .items_baseline()
                        .gap(px(4.0))
                        .child(
                            div()
                                .font_family(FONT_SANS)
                                .font_weight(FontWeight::EXTRA_BOLD)
                                .text_size(px(42.0))
                                .line_height(px(42.0))
                                .text_color(rgb(t.ink))
                                .child(format!("{}", model.score)),
                        )
                        .child(
                            div()
                                .font_family(FONT_MONO)
                                .text_size(px(14.0))
                                .text_color(rgb(t.muted))
                                .child("/100"),
                        ),
                )
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(12.0))
                        .text_color(rgb(t.muted))
                        .child(format!(
                            "outcome: {} · {}",
                            if model.verification.is_empty() {
                                "-"
                            } else {
                                &model.verification
                            },
                            if model.diag.strict_boot_passed {
                                "strict boot OK"
                            } else if model.diag.boot_passed {
                                "boot passed"
                            } else {
                                "not boot-verified"
                            }
                        )),
                ),
        )
        .child(
            style::ruled_kpi(t, "FUNCTIONS")
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(28.0))
                        .text_color(rgb(t.ink))
                        .child(format!("{verified_fns} / {total_fns}")),
                )
                .child(stacked_bar)
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(12.0))
                        .text_color(rgb(t.muted))
                        .child(format!(
                            "{lifted_fns} lifted · {} thunks · {} insns",
                            model.total_thunks, model.total_instructions
                        )),
                ),
        )
        .child(
            style::ruled_kpi(t, "PIPELINE STAGES")
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(28.0))
                        .text_color(rgb(t.ink))
                        .child(format!(
                            "{} / {}",
                            model.stage_ok_count(),
                            model.stages.len().max(8)
                        )),
                )
                .child(stage_bars)
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(12.0))
                        .text_color(rgb(t.muted))
                        .child(format!(
                            "{} unresolved imports · {} unhandled EIPs",
                            model.diag.unresolved_import_count, model.diag.unhandled_eip_count
                        )),
                ),
        )
        .child(
            style::ruled_kpi(t, "BUILD & CERTIFIED")
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(28.0))
                        .text_color(rgb(t.ink))
                        .child(if model.build.present && model.build.binary_size > 0 {
                            crate::cli::format_bytes(model.build.binary_size)
                        } else if model.build.present {
                            format!("{} C files", model.build.source_files)
                        } else {
                            "–".to_string()
                        }),
                )
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(12.0))
                        .text_color(if model.build.digests_match {
                            rgb(t.match_ink)
                        } else {
                            rgb(t.muted)
                        })
                        .child(if !model.build.present {
                            "No manifest recorded"
                        } else if model.build.digests_match {
                            "✓ Source & binary digests match"
                        } else {
                            "Source modified since last build"
                        }),
                )
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(12.0))
                        .text_color(rgb(t.muted))
                        .child(format!(
                            "{} certified verdicts · {} frames",
                            model.certified.len(),
                            model.frames.len()
                        )),
                ),
        );

    col = col.child(kpi_row);

    // -------------------------------------------------------- Next Step Callout Banner
    let mut stage_checks = div().flex().flex_wrap().gap(px(12.0));
    for st in &model.stages {
        let (sym, col_hex) = match st.status.as_str() {
            "ok" => ("✓", t.match_ink),
            "failed" => ("✗", t.miss),
            _ => ("○", t.muted),
        };
        stage_checks = stage_checks.child(
            div()
                .font_family(FONT_MONO)
                .text_size(px(11.5))
                .text_color(rgb(col_hex))
                .child(format!("{sym} {}", st.name)),
        );
    }

    let next_box = div()
        .w_full()
        .p(px(16.0))
        .rounded(px(4.0))
        .bg(rgb(t.panel))
        .border_1()
        .border_color(rgb(t.ink))
        .flex()
        .flex_wrap()
        .items_center()
        .justify_between()
        .gap(px(12.0))
        .child(
            div()
                .flex()
                .flex_col()
                .gap(px(6.0))
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(15.0))
                        .text_color(rgb(t.ink))
                        .child(if !model.diag.build_passed {
                            "Next: run the pipeline to compile this project"
                        } else if verified_fns == 0 {
                            "Next: polish & verify functions with AI readability agents"
                        } else {
                            "Ready: launch the rebuilt binary or configure WebAssembly export"
                        }),
                )
                .child(stage_checks),
        )
        .child(
            style::primary_btn(
                "next_cta_btn",
                t,
                if !model.diag.build_passed {
                    "Run pipeline"
                } else {
                    "Open Agents"
                },
                true,
            )
            .on_click(cx.listener(|this, _, _, cx| {
                let built = this
                    .active_project
                    .as_ref()
                    .map(|m| m.diag.build_passed)
                    .unwrap_or(false);
                if !built {
                    this.start_active_project_pipeline(cx);
                } else {
                    this.route = Route::Project(ProjectTab::Agents);
                    cx.notify();
                }
            })),
        );

    col = col.child(next_box);

    // -------------------------------------------------------- Runtime One-liner
    col = col.child(
        div()
            .w_full()
            .pt(px(10.0))
            .border_t_1()
            .border_color(rgb(t.line))
            .flex()
            .items_center()
            .justify_between()
            .gap(px(12.0))
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(10.0))
                    .child(style::eyebrow(t, "RUNTIME"))
                    .child(
                        div()
                            .font_family(FONT_SANS)
                            .text_size(px(13.5))
                            .text_color(rgb(t.ink_2))
                            .child(format!(
                                "{} · {} source file(s) · {} imported DLL(s) ({} IAT entries)",
                                model.runtime,
                                model.build.source_files,
                                model.inspect.import_dlls.len(),
                                model.inspect.import_count
                            )),
                    ),
            )
            .child(
                div()
                    .id("runtime_details_link")
                    .font_family(FONT_SANS)
                    .font_weight(FontWeight::SEMIBOLD)
                    .text_size(px(12.5))
                    .text_color(rgb(t.hl))
                    .underline()
                    .cursor_pointer()
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.route = Route::Project(ProjectTab::StagesBuild);
                        cx.notify();
                    }))
                    .child("Details"),
            ),
    );

    // -------------------------------------------------------- Bottom 2-column panels
    let mut picks: Vec<(usize, &FunctionRow)> = model.all_functions.iter().enumerate().collect();
    picks.sort_by(|a, b| {
        let rank = |f: &FunctionRow| -> u8 {
            if f.category == "HIGH_LEVEL_VERIFIED" || f.is_certified {
                0
            } else if f.category.starts_with("HIGH_LEVEL_") {
                1
            } else if !f.is_thunk {
                2
            } else {
                3
            }
        };
        rank(a.1)
            .cmp(&rank(b.1))
            .then_with(|| b.1.instructions.cmp(&a.1.instructions))
    });

    let mut fn_panel_rows = div().flex().flex_col();
    for (row_i, (orig_idx, f)) in picks.into_iter().take(8).enumerate() {
        let dot_hex = function_color_hex(f, t);
        fn_panel_rows = fn_panel_rows.child(
            div()
                .id(("ov_fn_row", row_i))
                .flex()
                .items_center()
                .justify_between()
                .px(px(16.0))
                .py(px(8.0))
                .when(row_i > 0, |el| el.border_t_1().border_color(rgb(t.line_soft)))
                .cursor_pointer()
                .hover({
                    let bg_alt = t.bg_alt;
                    move |s| s.bg(rgb(bg_alt))
                })
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.open_function_inspector(orig_idx);
                    cx.notify();
                }))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(8.0))
                        .child(div().w(px(8.0)).h(px(8.0)).rounded(px(2.0)).bg(rgb(dot_hex)))
                        .child(
                            div()
                                .font_family(FONT_MONO)
                                .text_size(px(12.5))
                                .text_color(rgb(t.ink))
                                .child(f.symbol.clone()),
                        ),
                )
                .child(
                    div()
                        .font_family(FONT_MONO)
                        .text_size(px(11.0))
                        .text_color(rgb(t.muted))
                        .child(format!("{} · {} insns", f.address, f.instructions)),
                ),
        );
    }

    let mut st_panel_rows = div().flex().flex_col();
    for (i, st) in model.stages.iter().enumerate() {
        let (bg, fg) = match st.status.as_str() {
            "ok" => (t.match_bg, t.match_ink),
            "failed" => (t.miss_bg, t.miss),
            _ => (t.bg_alt, t.muted),
        };
        st_panel_rows = st_panel_rows.child(
            div()
                .flex()
                .items_center()
                .justify_between()
                .gap(px(8.0))
                .px(px(16.0))
                .py(px(8.0))
                .when(i > 0, |el| el.border_t_1().border_color(rgb(t.line_soft)))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(8.0))
                        .child(
                            div()
                                .w(px(82.0))
                                .font_family(FONT_MONO)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(12.0))
                                .text_color(rgb(t.ink))
                                .child(st.name.clone()),
                        )
                        .child(style::status_badge(st.status.clone(), bg, fg)),
                )
                .child(
                    div()
                        .font_family(FONT_MONO)
                        .text_size(px(11.0))
                        .text_color(rgb(t.muted))
                        .child(truncate(&st.detail, 36)),
                ),
        );
    }

    let bottom_cols = div()
        .w_full()
        .flex()
        .flex_row()
        .flex_wrap()
        .gap(px(16.0))
        .child(
            style::panel_box(t)
                .flex_1()
                .min_w(px(360.0))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .justify_between()
                        .px(px(16.0))
                        .py(px(11.0))
                        .border_b_1()
                        .border_color(rgb(t.line_soft))
                        .child(
                            div()
                                .font_family(FONT_SANS)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(14.5))
                                .text_color(rgb(t.ink))
                                .child("Highlighted functions"),
                        )
                        .child(
                            div()
                                .id("ov_all_fns_link")
                                .font_family(FONT_SANS)
                                .font_weight(FontWeight::SEMIBOLD)
                                .text_size(px(12.0))
                                .text_color(rgb(t.hl))
                                .underline()
                                .cursor_pointer()
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.route = Route::Project(ProjectTab::Functions);
                                    cx.notify();
                                }))
                                .child("All functions"),
                        ),
                )
                .child(fn_panel_rows),
        )
        .child(
            style::panel_box(t)
                .flex_1()
                .min_w(px(360.0))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .justify_between()
                        .px(px(16.0))
                        .py(px(11.0))
                        .border_b_1()
                        .border_color(rgb(t.line_soft))
                        .child(
                            div()
                                .font_family(FONT_SANS)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(14.5))
                                .text_color(rgb(t.ink))
                                .child("Pipeline stages"),
                        )
                        .child(
                            div()
                                .id("ov_build_link")
                                .font_family(FONT_SANS)
                                .font_weight(FontWeight::SEMIBOLD)
                                .text_size(px(12.0))
                                .text_color(rgb(t.hl))
                                .underline()
                                .cursor_pointer()
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.route = Route::Project(ProjectTab::StagesBuild);
                                    cx.notify();
                                }))
                                .child("Build & log"),
                        ),
                )
                .child(st_panel_rows),
        );

    col.child(bottom_cols)
}

/// Interactive Function Map rendered as crisp rounded GPUI flex-wrap tiles!
fn render_function_map_squares(
    app: &GameReCompApp,
    model: &ProjectModel,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let n = model.all_functions.len();
    if n == 0 {
        return div()
            .font_family(FONT_MONO)
            .text_size(px(12.0))
            .text_color(rgb(t.muted))
            .child("No functions recovered yet. Click Run pipeline to analyze this executable.");
    }

    let max_tiles = 600usize;
    let shown = n.min(max_tiles);
    let tile_sz = if shown <= 120 {
        22.0
    } else if shown <= 320 {
        16.0
    } else {
        12.0
    };

    let mut mod_map: HashMap<&str, usize> = HashMap::new();
    if app.map_mode == MapColorMode::Module {
        for f in &model.all_functions[..shown] {
            let next_id = mod_map.len();
            mod_map.entry(f.file.as_str()).or_insert(next_id);
        }
    }

    let mut grid = div().w_full().flex().flex_wrap().gap(px(3.0));
    for i in 0..shown {
        let f = &model.all_functions[i];
        let fill_hex = if app.map_mode == MapColorMode::Module {
            let mi = *mod_map.get(f.file.as_str()).unwrap_or(&0);
            t.module_colors[mi % t.module_colors.len()]
        } else {
            function_color_hex(f, t)
        };
        let is_selected = app.inspector_open && app.inspector_func_idx == Some(i);
        let ink = t.ink;

        grid = grid.child(
            div()
                .id(("fn_map_sq", i))
                .w(px(tile_sz))
                .h(px(tile_sz))
                .rounded(px(2.5))
                .bg(rgb(fill_hex))
                .when(is_selected, |el| el.border_2().border_color(rgb(t.hl)))
                .cursor_pointer()
                .hover(move |s| s.border_2().border_color(rgb(ink)))
                .on_hover(cx.listener(move |this, hovered: &bool, _, cx| {
                    if *hovered {
                        this.hovered_map_fn = Some(i);
                        cx.notify();
                    } else if this.hovered_map_fn == Some(i) {
                        this.hovered_map_fn = None;
                        cx.notify();
                    }
                }))
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.open_function_inspector(i);
                    cx.notify();
                })),
        );
    }

    let hover_bar = if let Some(hi) = app.hovered_map_fn.and_then(|i| model.all_functions.get(i)) {
        div()
            .px(px(10.0))
            .py(px(6.0))
            .rounded(px(3.0))
            .bg(rgb(t.code_bg))
            .border_1()
            .border_color(rgb(t.line))
            .flex()
            .flex_wrap()
            .items_center()
            .gap(px(10.0))
            .child(
                div()
                    .font_family(FONT_MONO)
                    .font_weight(FontWeight::BOLD)
                    .text_size(px(12.0))
                    .text_color(rgb(t.hl))
                    .child(hi.symbol.clone()),
            )
            .child(
                div()
                    .font_family(FONT_MONO)
                    .text_size(px(11.5))
                    .text_color(rgb(t.ink_2))
                    .child(format!(
                        "{} · {} insns · {} blocks · {} calls · {}",
                        hi.address, hi.instructions, hi.blocks, hi.calls, hi.category
                    )),
            )
            .when(!hi.file.is_empty(), |el| {
                el.child(
                    div()
                        .font_family(FONT_MONO)
                        .text_size(px(11.0))
                        .text_color(rgb(t.muted))
                        .child(hi.file.clone()),
                )
            })
    } else {
        div()
            .font_family(FONT_MONO)
            .text_size(px(11.0))
            .text_color(rgb(t.muted))
            .child("Hover any square for function address & stats · click to open Inspector")
    };

    let mut wrap = div().flex().flex_col().gap(px(8.0)).child(grid).child(hover_bar);
    if n > max_tiles {
        wrap = wrap.child(
            div()
                .font_family(FONT_MONO)
                .text_size(px(11.0))
                .text_color(rgb(t.muted))
                .child(format!(
                    "Showing first {max_tiles} of {n} functions on map. Open the Functions tab to filter & search all {n}."
                )),
        );
    }
    wrap
}

// ============================================================================
// Tab 2: Functions
// ============================================================================
fn render_functions_tab(
    app: &GameReCompApp,
    model: &ProjectModel,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let q = app.fn_search.trim().to_ascii_lowercase();
    let mut rows: Vec<(usize, &FunctionRow)> = model
        .all_functions
        .iter()
        .enumerate()
        .filter(|(_, f)| {
            let pass_flt = match app.fn_filter {
                FnFilter::All => true,
                FnFilter::Verified => f.category == "HIGH_LEVEL_VERIFIED" || f.is_certified,
                FnFilter::HighLevel => f.category.starts_with("HIGH_LEVEL_"),
                FnFilter::Fallback => f.category == "LOW_LEVEL_FALLBACK" && !f.is_thunk,
                FnFilter::Unresolved => f.category == "UNRESOLVED",
                FnFilter::Starred => model.ui_meta.starred.contains(&f.address),
            };
            if !pass_flt {
                return false;
            }
            q.is_empty()
                || f.address.to_ascii_lowercase().contains(&q)
                || f.symbol.to_ascii_lowercase().contains(&q)
                || f.file.to_ascii_lowercase().contains(&q)
                || f.category.to_ascii_lowercase().contains(&q)
        })
        .collect();

    rows.sort_by(|a, b| {
        let ord = match app.fn_sort {
            FnSort::Address => a.1.address.cmp(&b.1.address),
            FnSort::Name => a.1.symbol.cmp(&b.1.symbol),
            FnSort::Instructions => a.1.instructions.cmp(&b.1.instructions),
            FnSort::Category => a.1.category.cmp(&b.1.category),
            FnSort::Module => a.1.file.cmp(&b.1.file),
        };
        if app.fn_sort_desc { ord.reverse() } else { ord }
    });

    let limit = app.fn_table_limit.min(rows.len());

    let filters = [
        (FnFilter::All, format!("All {}", model.all_functions.len())),
        (
            FnFilter::Verified,
            format!("Verified {}", model.verified_count()),
        ),
        (
            FnFilter::HighLevel,
            format!("High-level {}", model.high_level_count()),
        ),
        (FnFilter::Fallback, "Lifted".to_string()),
        (
            FnFilter::Unresolved,
            format!("Unresolved {}", model.unresolved_count()),
        ),
        (
            FnFilter::Starred,
            format!("★ {}", model.ui_meta.starred.len()),
        ),
    ];

    let mut flt_bar = div().flex().flex_wrap().gap(px(4.0));
    for (flt, lbl) in filters {
        flt_bar = flt_bar.child(
            style::option_pill(("fn_flt", flt as usize), t, lbl, app.fn_filter == flt).on_click(
                cx.listener(move |this, _, _, cx| {
                    this.fn_filter = flt;
                    cx.notify();
                }),
            ),
        );
    }

    let toolbar = div()
        .flex()
        .flex_wrap()
        .items_center()
        .gap(px(10.0))
        .px(px(16.0))
        .py(px(12.0))
        .border_b_1()
        .border_color(rgb(t.line_soft))
        .child(
            div().w(px(300.0)).child(
                style::text_input_box(
                    "fn_search_input",
                    t,
                    &app.fn_search,
                    "Find a function, address or module  ( / )",
                    app.focused_input == Some(FocusInput::FnSearch),
                    true,
                )
                .on_click(cx.listener(|this, _, window, cx| {
                    this.set_focus_input(Some(FocusInput::FnSearch), window, cx);
                })),
            ),
        )
        .child(flt_bar);

    let thead = div()
        .flex()
        .items_center()
        .px(px(16.0))
        .py(px(9.0))
        .border_b_1()
        .border_color(rgb(t.line_soft))
        .child(
            div()
                .w(px(30.0))
                .font_family(FONT_MONO)
                .font_weight(FontWeight::BOLD)
                .text_size(px(11.5))
                .text_color(rgb(t.work))
                .child("★"),
        )
        .child(sort_col("th_addr", app, t, "ADDRESS", FnSort::Address, 115.0, cx))
        .child(sort_col("th_name", app, t, "FUNCTION", FnSort::Name, 260.0, cx))
        .child(sort_col("th_insns", app, t, "INSNS", FnSort::Instructions, 80.0, cx))
        .child(sort_col("th_mod", app, t, "MODULE", FnSort::Module, 175.0, cx))
        .child(sort_col("th_cat", app, t, "PROVENANCE / STATUS", FnSort::Category, 180.0, cx));

    let mut tbody = div().flex().flex_col();
    if rows.is_empty() {
        tbody = tbody.child(
            div()
                .p(px(28.0))
                .flex()
                .justify_center()
                .font_family(FONT_SANS)
                .text_size(px(13.0))
                .text_color(rgb(t.muted))
                .child("No matching functions."),
        );
    } else {
        for (ri, &(orig_idx, f)) in rows[..limit].iter().enumerate() {
            let is_sel = app.inspector_open && app.inspector_func_idx == Some(orig_idx);
            let is_star = model.ui_meta.starred.contains(&f.address);
            let addr_star = f.address.clone();
            let (badge_txt, bg, fg) = category_badge(f, t);

            tbody = tbody.child(
                div()
                    .id(("fn_tbl_row", ri))
                    .flex()
                    .items_center()
                    .px(px(16.0))
                    .py(px(7.0))
                    .border_b_1()
                    .border_color(rgb(t.line_soft))
                    .bg(if is_sel { rgb(t.match_bg) } else { rgb(t.panel) })
                    .cursor_pointer()
                    .hover({
                        let bg_alt = t.bg_alt;
                        move |s| s.bg(rgb(bg_alt))
                    })
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.open_function_inspector(orig_idx);
                        cx.notify();
                    }))
                    .child(
                        div()
                            .id(("fn_star_btn", ri))
                            .w(px(30.0))
                            .text_size(px(14.0))
                            .text_color(if is_star { rgb(t.work) } else { rgb(t.muted) })
                            .cursor_pointer()
                            .on_click(cx.listener(move |this, _, _, cx| {
                                cx.stop_propagation();
                                this.toggle_star_function(&addr_star);
                                cx.notify();
                            }))
                            .child(if is_star { "★" } else { "☆" }),
                    )
                    .child(
                        div()
                            .w(px(115.0))
                            .font_family(FONT_MONO)
                            .text_size(px(12.0))
                            .text_color(rgb(t.muted))
                            .child(f.address.clone()),
                    )
                    .child(
                        div()
                            .w(px(260.0))
                            .font_family(FONT_MONO)
                            .text_size(px(12.5))
                            .text_color(rgb(t.ink))
                            .child(truncate(&f.symbol, 30)),
                    )
                    .child(
                        div()
                            .w(px(80.0))
                            .font_family(FONT_MONO)
                            .text_size(px(12.0))
                            .text_color(rgb(t.ink_2))
                            .child(format!("{}", f.instructions)),
                    )
                    .child(
                        div()
                            .w(px(175.0))
                            .font_family(FONT_MONO)
                            .text_size(px(11.5))
                            .text_color(rgb(t.muted))
                            .child(truncate(&f.file, 22)),
                    )
                    .child(style::status_badge(badge_txt, bg, fg)),
            );
        }
    }

    let foot = div()
        .flex()
        .items_center()
        .justify_between()
        .px(px(16.0))
        .py(px(10.0))
        .child(
            div()
                .font_family(FONT_MONO)
                .text_size(px(11.5))
                .text_color(rgb(t.muted))
                .child(format!("Showing {limit} of {} functions", rows.len())),
        )
        .when(limit < rows.len(), |el| {
            el.child(
                style::ghost_btn("fn_show_more", t, "Show 200 more").on_click(cx.listener(
                    |this, _, _, cx| {
                        this.fn_table_limit += 200;
                        cx.notify();
                    },
                )),
            )
        });

    style::panel_box(t)
        .child(toolbar)
        .child(thead)
        .child(tbody)
        .child(foot)
}

// ============================================================================
// Tab 3: Stages & Build
// ============================================================================
fn render_stages_build_tab(
    app: &GameReCompApp,
    model: &ProjectModel,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let mut wrap = div().flex().flex_col().gap(px(18.0));

    if app.running_kind.is_some() {
        wrap = wrap.child(
            style::panel_box(t)
                .border_color(rgb(t.hl))
                .p(px(16.0))
                .child(render_live_stage_stepper(app, t, cx)),
        );
    }

    // Left: Stage results + diagnostics | Right: Build manifest hashes
    let mut stages_body = div().p(px(16.0)).flex().flex_col().gap(px(8.0));
    for st in &model.stages {
        let (bg, fg) = match st.status.as_str() {
            "ok" => (t.match_bg, t.match_ink),
            "failed" => (t.miss_bg, t.miss),
            _ => (t.bg_alt, t.muted),
        };
        stages_body = stages_body.child(
            div()
                .flex()
                .items_center()
                .gap(px(10.0))
                .child(
                    div()
                        .w(px(90.0))
                        .font_family(FONT_MONO)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(12.5))
                        .text_color(rgb(t.ink))
                        .child(st.name.clone()),
                )
                .child(style::status_badge(st.status.clone(), bg, fg))
                .child(
                    div()
                        .font_family(FONT_MONO)
                        .text_size(px(11.5))
                        .text_color(rgb(t.muted))
                        .child(st.detail.clone()),
                ),
        );
    }
    stages_body = stages_body.child(
        div()
            .pt(px(8.0))
            .border_t_1()
            .border_color(rgb(t.line_soft))
            .font_family(FONT_MONO)
            .text_size(px(11.0))
            .text_color(rgb(t.ink_2))
            .child(format!(
                "build={} · boot={} · strict={} · unresolved_imports={} · unhandled_eips={}",
                model.diag.build_passed,
                model.diag.boot_passed,
                model.diag.strict_boot_passed,
                model.diag.unresolved_import_count,
                model.diag.unhandled_eip_count,
            )),
    );

    let b = &model.build;
    let build_body = if !b.present {
        div()
            .p(px(16.0))
            .font_family(FONT_SANS)
            .text_size(px(13.0))
            .text_color(rgb(t.muted))
            .child("No manifest.json recorded for this project yet.")
    } else {
        div()
            .p(px(16.0))
            .flex()
            .flex_col()
            .gap(px(6.0))
            .child(kv_line(t, "Sources built", &format!("{} file(s)", b.source_files)))
            .child(kv_line(t, "Source SHA-256", &b.source_digest))
            .child(kv_line(
                t,
                "Binary path",
                if b.binary_path.is_empty() {
                    "none recorded"
                } else {
                    &b.binary_path
                },
            ))
            .child(kv_line(t, "Binary SHA-256", &b.binary_sha256))
            .child(kv_line(t, "Binary size", &crate::cli::format_bytes(b.binary_size)))
            .child(kv_line(t, "Built from", &b.built_from_digest))
    };

    let top_cols = div()
        .w_full()
        .flex()
        .flex_row()
        .flex_wrap()
        .gap(px(16.0))
        .child(
            style::panel_box(t)
                .flex_1()
                .min_w(px(360.0))
                .child(panel_hdr(t, "Pipeline stages", None))
                .child(stages_body),
        )
        .child(
            style::panel_box(t)
                .flex_1()
                .min_w(px(360.0))
                .child(panel_hdr(
                    t,
                    "Build & evidence hashes",
                    if b.present {
                        Some(if b.digests_match {
                            ("DIGESTS MATCH", t.match_bg, t.match_ink)
                        } else {
                            ("DIGESTS DIFFER", t.work_bg, t.work)
                        })
                    } else {
                        None
                    },
                ))
                .child(build_body),
        );

    // Live Pipeline Console
    let logs_copy = app.live_logs.join("\n");
    let mut console_lines = div()
        .id("stages_console_scroll")
        .w_full()
        .max_h(px(320.0))
        .overflow_y_scroll()
        .p(px(14.0))
        .bg(rgb(t.code_bg))
        .flex()
        .flex_col()
        .gap(px(2.0));

    if app.live_logs.is_empty() {
        console_lines = console_lines.child(
            div()
                .font_family(FONT_MONO)
                .text_size(px(12.0))
                .text_color(rgb(t.muted))
                .child("No live run output yet. Click Run pipeline in the project header to stream stages here."),
        );
    } else {
        for line in app.live_logs.iter().rev().take(200).rev() {
            let col = if line.starts_with("==>") || line.starts_with("$ ") {
                rgb(t.hl)
            } else if line.contains("error") || line.contains("FAIL") {
                rgb(t.miss)
            } else if line.contains("PASS") || line.contains("verified") {
                rgb(t.match_ink)
            } else {
                rgb(t.ink_2)
            };
            console_lines = console_lines.child(
                div()
                    .font_family(FONT_MONO)
                    .text_size(px(11.5))
                    .text_color(col)
                    .child(line.clone()),
            );
        }
    }

    let console_panel = style::panel_box(t)
        .child(
            div()
                .flex()
                .items_center()
                .justify_between()
                .px(px(16.0))
                .py(px(10.0))
                .border_b_1()
                .border_color(rgb(t.line_soft))
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(14.5))
                        .text_color(rgb(t.ink))
                        .child("Live pipeline & runner console"),
                )
                .child(
                    div()
                        .flex()
                        .gap(px(8.0))
                        .child(style::ghost_btn("log_copy_btn", t, "Copy log").on_click(
                            cx.listener(move |this, _, _, cx| {
                                cx.write_to_clipboard(ClipboardItem::new_string(logs_copy.clone()));
                                this.push_toast(
                                    "Copied",
                                    "Pipeline output copied to clipboard.",
                                    this.tokens().info,
                                    false,
                                );
                                cx.notify();
                            }),
                        ))
                        .child(style::ghost_btn("log_clear_btn", t, "Clear").on_click(
                            cx.listener(|this, _, _, cx| {
                                this.live_logs.clear();
                                cx.notify();
                            }),
                        )),
                ),
        )
        .child(console_lines);

    wrap.child(top_cols).child(console_panel)
}

// ============================================================================
// Tab 4: Agents
// ============================================================================
fn render_agents_tab(
    app: &GameReCompApp,
    model: &ProjectModel,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let ai_cmd = app.effective_ai_cmd();
    let agent_running = app.running_kind == Some(WorkerKind::Readability);

    let left_agents = style::panel_box(t)
        .flex_1()
        .min_w(px(360.0))
        .child(
            div()
                .flex()
                .items_center()
                .justify_between()
                .px(px(16.0))
                .py(px(11.0))
                .border_b_1()
                .border_color(rgb(t.line_soft))
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(15.0))
                        .text_color(rgb(t.ink))
                        .child("Your agents here"),
                )
                .child(if agent_running {
                    style::danger_btn("ag_stop_btn", t, "■ Stop").on_click(cx.listener(
                        |this, _, _, cx| {
                            this.stop_running_worker();
                            cx.notify();
                        },
                    ))
                } else {
                    style::primary_btn("ag_start_btn", t, "▶ Run readability agent", true)
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.start_readability_agent(cx);
                        }))
                }),
        )
        .child(
            div()
                .p(px(16.0))
                .flex()
                .flex_col()
                .gap(px(12.0))
                .when(agent_running, |el| {
                    el.child(
                        div()
                            .p(px(12.0))
                            .rounded(px(4.0))
                            .bg(rgb(t.hl_soft))
                            .border_1()
                            .border_color(rgb(t.hl))
                            .flex()
                            .flex_col()
                            .gap(px(6.0))
                            .child(
                                div()
                                    .flex()
                                    .items_center()
                                    .gap(px(8.0))
                                    .child(style::animated_pulse_dot("ag_card_pulse", t.hl, 8.0))
                                    .child(
                                        div()
                                            .font_family(FONT_SANS)
                                            .font_weight(FontWeight::BOLD)
                                            .text_size(px(14.0))
                                            .text_color(rgb(t.ink))
                                            .child("Readability & Provenance Agent #1"),
                                    ),
                            )
                            .child(style::animated_workbar("ag_workbar", t, 4.0))
                            .child(
                                div()
                                    .font_family(FONT_MONO)
                                    .text_size(px(11.5))
                                    .text_color(rgb(t.ink_2))
                                    .child(format!("Working: {}", app.running_stage)),
                            ),
                    )
                })
                .when(!agent_running, |el| {
                    el.child(
                        div()
                            .font_family(FONT_SANS)
                            .text_size(px(13.0))
                            .text_color(rgb(t.muted))
                            .child("No agent works on this game right now. Configure REVENG_AI_CMD in Agents & models and press Run readability agent."),
                    )
                })
                .child(kv_line(
                    t,
                    "REVENG_AI_CMD",
                    ai_cmd.as_deref().unwrap_or("<not configured>"),
                ))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(8.0))
                        .child(style::eyebrow(t, "TARGET FUNCTION:"))
                        .child(
                            style::text_input_box(
                                "ag_target_fn_input",
                                t,
                                &app.agent_target_fn,
                                "optional e.g. 0x00402020",
                                app.focused_input == Some(FocusInput::AgentTargetFn),
                                true,
                            )
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.set_focus_input(Some(FocusInput::AgentTargetFn), window, cx);
                            })),
                        ),
                ),
        );

    let mut feed_rows = div()
        .id("ag_feed_scroll")
        .max_h(px(280.0))
        .overflow_y_scroll()
        .p(px(12.0))
        .flex()
        .flex_col()
        .gap(px(4.0));

    if app.feed.is_empty() {
        feed_rows = feed_rows.child(
            div()
                .font_family(FONT_SANS)
                .text_size(px(12.5))
                .text_color(rgb(t.muted))
                .child("Live pipeline stages and agent events appear here, newest first."),
        );
    } else {
        for (i, ev) in app.feed.iter().rev().enumerate() {
            feed_rows = feed_rows.child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(8.0))
                    .px(px(8.0))
                    .py(px(5.0))
                    .rounded(px(2.0))
                    .bg(if i == 0 && ev.is_ok {
                        rgb(t.hl_soft)
                    } else {
                        rgb(t.panel)
                    })
                    .child(
                        div()
                            .font_family(FONT_MONO)
                            .text_size(px(11.0))
                            .text_color(rgb(t.muted))
                            .child(ev.timestamp.clone()),
                    )
                    .child(
                        div()
                            .font_family(FONT_SANS)
                            .font_weight(FontWeight::BOLD)
                            .text_size(px(12.5))
                            .text_color(if ev.is_ok {
                                rgb(t.match_ink)
                            } else {
                                rgb(t.ink)
                            })
                            .child(ev.actor.clone()),
                    )
                    .child(
                        div()
                            .font_family(FONT_SANS)
                            .text_size(px(12.5))
                            .text_color(rgb(t.ink_2))
                            .child(ev.message.clone()),
                    ),
            );
        }
    }

    let right_feed = style::panel_box(t)
        .flex_1()
        .min_w(px(360.0))
        .child(panel_hdr(t, "What's happening", None))
        .child(feed_rows);

    let mut cert_rows = div()
        .id("ag_cert_scroll")
        .max_h(px(220.0))
        .overflow_y_scroll()
        .p(px(12.0))
        .flex()
        .flex_col()
        .gap(px(6.0));
    if model.certified.is_empty() {
        cert_rows = cert_rows.child(
            div()
                .font_family(FONT_SANS)
                .text_size(px(12.5))
                .text_color(rgb(t.muted))
                .child("No certified verdicts indexed."),
        );
    } else {
        for r in &model.certified {
            cert_rows = cert_rows.child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(8.0))
                    .child(
                        div()
                            .font_family(FONT_MONO)
                            .font_weight(FontWeight::BOLD)
                            .text_size(px(12.0))
                            .text_color(rgb(t.ink))
                            .child(r.address.clone()),
                    )
                    .child(style::status_badge(r.verdict.clone(), t.match_bg, t.match_ink))
                    .child(
                        div()
                            .font_family(FONT_MONO)
                            .text_size(px(11.0))
                            .text_color(rgb(t.muted))
                            .child(format!("{} cases · {}", r.cases, r.module)),
                    ),
            );
        }
    }

    div()
        .flex()
        .flex_col()
        .gap(px(18.0))
        .child(
            div()
                .flex()
                .flex_row()
                .flex_wrap()
                .gap(px(16.0))
                .child(left_agents)
                .child(right_feed),
        )
        .child(
            style::panel_box(t)
                .child(panel_hdr(
                    t,
                    &format!("Certified verdicts ({})", model.certified.len()),
                    None,
                ))
                .child(cert_rows),
        )
}

// ============================================================================
// Tab 5: Frames (Native GPUI img() Screenshot Gallery)
// ============================================================================
fn render_frames_tab(
    app: &GameReCompApp,
    model: &ProjectModel,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    if model.frames.is_empty() {
        return style::panel_box(t).p(px(40.0)).items_center().gap(px(10.0)).child(
            div()
                .font_family(FONT_SANS)
                .font_weight(FontWeight::EXTRA_BOLD)
                .text_size(px(18.0))
                .text_color(rgb(t.ink))
                .child("No captured frames yet"),
        ).child(
            div()
                .font_family(FONT_SANS)
                .text_size(px(13.0))
                .text_color(rgb(t.muted))
                .child("Enable Capture screenshots when running the pipeline to inspect rendered game frames here."),
        ).child(
            style::primary_btn("frames_run_cap", t, "▶ Run pipeline with screenshots", true)
                .on_click(cx.listener(|this, _, _, cx| {
                    this.opts.capture_screenshots = true;
                    this.start_active_project_pipeline(cx);
                })),
        );
    }

    let sel_idx = app.selected_frame_idx.unwrap_or(0).min(model.frames.len() - 1);
    let sel_path = model.frames[sel_idx].clone();

    let mut list = div()
        .w(px(210.0))
        .flex()
        .flex_col()
        .gap(px(4.0))
        .child(style::eyebrow(t, format!("FRAMES ({})", model.frames.len())));

    for (i, fp) in model.frames.iter().enumerate() {
        let name = fp
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("frame")
            .to_string();
        list = list.child(
            style::option_pill(("frame_pick", i), t, name, i == sel_idx).on_click(cx.listener(
                move |this, _, _, cx| {
                    this.selected_frame_idx = Some(i);
                    cx.notify();
                },
            )),
        );
    }

    style::panel_box(t).p(px(18.0)).child(
        div()
            .flex()
            .flex_row()
            .gap(px(20.0))
            .child(list)
            .child(
                div()
                    .flex_1()
                    .flex()
                    .flex_col()
                    .gap(px(8.0))
                    .child(
                        div()
                            .font_family(FONT_MONO)
                            .text_size(px(11.5))
                            .text_color(rgb(t.muted))
                            .child(sel_path.display().to_string()),
                    )
                    .child(
                        div()
                            .p(px(8.0))
                            .rounded(px(4.0))
                            .bg(rgb(t.code_bg))
                            .border_1()
                            .border_color(rgb(t.line_soft))
                            .child(img(sel_path).max_w(px(720.0)).max_h(px(480.0))),
                    ),
            ),
    )
}

// ============================================================================
// Tab 6: Project Settings (ALL CLI Options + About This Project)
// ============================================================================
fn render_project_settings_tab(
    app: &GameReCompApp,
    model: &ProjectModel,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let opts = &app.opts;
    let cmd_preview = opts.format_command_preview();
    let cmd_copy = cmd_preview.clone();
    let dir_open = model.dir.clone();

    let left = style::panel_box(t)
        .flex_1()
        .min_w(px(440.0))
        .child(panel_hdr(t, "Recompilation & CLI parameters", None))
        .child(
            div()
                .p(px(16.0))
                .flex()
                .flex_col()
                .gap(px(12.0))
                .child(
                    div()
                        .flex()
                        .flex_col()
                        .gap(px(4.0))
                        .child(style::eyebrow(t, "ORIGINAL PE EXECUTABLE"))
                        .child(
                            div()
                                .flex()
                                .gap(px(8.0))
                                .child(
                                    style::text_input_box(
                                        "proj_pe_input",
                                        t,
                                        &opts.pe_file,
                                        "/path/to/game.exe",
                                        app.focused_input == Some(FocusInput::OptPeFile),
                                        true,
                                    )
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.set_focus_input(
                                            Some(FocusInput::OptPeFile),
                                            window,
                                            cx,
                                        );
                                    })),
                                )
                                .child(
                                    style::ghost_btn("proj_pe_browse", t, "Browse…").on_click(
                                        cx.listener(|this, _, _, cx| {
                                            if let Some(f) = rfd::FileDialog::new()
                                                .add_filter("Windows PE", &["exe", "EXE"])
                                                .pick_file()
                                            {
                                                this.opts.pe_file = f.display().to_string();
                                                cx.notify();
                                            }
                                        }),
                                    ),
                                ),
                        ),
                )
                .child(
                    div()
                        .flex()
                        .flex_wrap()
                        .gap(px(6.0))
                        .children(
                            [
                                AppProfile::Game,
                                AppProfile::DesktopApp,
                                AppProfile::ConsoleApp,
                            ]
                            .into_iter()
                            .enumerate()
                            .map(|(i, pr)| {
                                style::option_pill(
                                    ("ps_prof", i),
                                    t,
                                    pr.label(),
                                    opts.app_profile == pr,
                                )
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.opts.apply_profile(pr);
                                    cx.notify();
                                }))
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
                            style::option_pill(
                                "ps_tgt_auto",
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
                                "ps_tgt_wasm",
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
                    style::toggle_checkbox(
                        "ps_auto_launch_game",
                        t,
                        opts.auto_launch_after_build,
                        "▶  Run game automatically right after decompiling finishes",
                        Some("Starts the rebuilt game executable or opens the WebAssembly player automatically when the build completes."),
                    )
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.opts.auto_launch_after_build = !this.opts.auto_launch_after_build;
                        cx.notify();
                    })),
                )
                .child(render_advanced_cli_options(app, t, cx))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(8.0))
                        .p(px(10.0))
                        .rounded(px(3.0))
                        .bg(rgb(t.code_bg))
                        .border_1()
                        .border_color(rgb(t.line_soft))
                        .child(
                            div()
                                .flex_1()
                                .font_family(FONT_MONO)
                                .text_size(px(11.0))
                                .text_color(rgb(t.ink_2))
                                .child(cmd_preview),
                        )
                        .child(
                            style::ghost_btn("ps_copy_cmd", t, "Copy CLI").on_click(cx.listener(
                                move |this, _, _, cx| {
                                    cx.write_to_clipboard(ClipboardItem::new_string(
                                        cmd_copy.clone(),
                                    ));
                                    this.push_toast(
                                        "CLI command copied",
                                        "Copied equivalent ./GameReComp command.",
                                        this.tokens().info,
                                        false,
                                    );
                                    cx.notify();
                                },
                            )),
                        ),
                )
                .child(
                    style::primary_btn("ps_rerun_btn", t, "▶  Re-run pipeline with these options", true)
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.start_active_project_pipeline(cx);
                        })),
                ),
        );

    let right = style::panel_box(t)
        .flex_1()
        .min_w(px(340.0))
        .child(panel_hdr(t, "About this project", None))
        .child(
            div()
                .p(px(16.0))
                .flex()
                .flex_col()
                .gap(px(8.0))
                .child(kv_line(t, "Project dir", &model.dir.display().to_string()))
                .child(kv_line(t, "Original PE", &model.pe_path))
                .child(kv_line(t, "Entry point", &model.inspect.entry_point))
                .child(kv_line(t, "PE SHA-256", &model.inspect.sha256))
                .child(kv_line(t, "Linker", &model.inspect.linker_version))
                .child(
                    div()
                        .pt(px(10.0))
                        .flex()
                        .gap(px(8.0))
                        .child(
                            style::ghost_btn("ps_open_dir", t, "Open folder").on_click(
                                cx.listener(move |_, _, _, _| {
                                    let _ = open::that(&dir_open);
                                }),
                            ),
                        )
                        .child(
                            style::ghost_btn("ps_reload", t, "Reload from disk").on_click(
                                cx.listener(|this, _, _, cx| {
                                    this.reload_active_project();
                                    cx.notify();
                                }),
                            ),
                        ),
                ),
        );

    div()
        .w_full()
        .flex()
        .flex_row()
        .flex_wrap()
        .items_start()
        .gap(px(16.0))
        .child(left)
        .child(right)
}

// ============================================================================
// Helpers
// ============================================================================
fn panel_hdr(t: &ThemeTokens, title: &str, badge: Option<(&str, u32, u32)>) -> Div {
    div()
        .flex()
        .items_center()
        .justify_between()
        .px(px(16.0))
        .py(px(11.0))
        .border_b_1()
        .border_color(rgb(t.line_soft))
        .child(
            div()
                .font_family(FONT_SANS)
                .font_weight(FontWeight::BOLD)
                .text_size(px(14.5))
                .text_color(rgb(t.ink))
                .child(title.to_string()),
        )
        .when_some(badge, |el, (txt, bg, fg)| {
            el.child(style::status_badge(txt.to_string(), bg, fg))
        })
}

fn legend_swatch(t: &ThemeTokens, hex: u32, label: &'static str) -> Div {
    div()
        .flex()
        .items_center()
        .gap(px(5.0))
        .child(div().w(px(9.0)).h(px(9.0)).rounded(px(2.0)).bg(rgb(hex)))
        .child(
            div()
                .font_family(FONT_SANS)
                .text_size(px(11.5))
                .text_color(rgb(t.muted))
                .child(label),
        )
}

fn sort_col(
    id: &'static str,
    app: &GameReCompApp,
    t: &ThemeTokens,
    label: &'static str,
    sort: FnSort,
    w: f32,
    cx: &mut Context<GameReCompApp>,
) -> gpui::Stateful<Div> {
    let active = app.fn_sort == sort;
    let txt = if active {
        if app.fn_sort_desc {
            format!("{label} ↓")
        } else {
            format!("{label} ↑")
        }
    } else {
        label.to_string()
    };
    div()
        .id(id)
        .w(px(w))
        .font_family(FONT_MONO)
        .font_weight(FontWeight::BOLD)
        .text_size(px(10.5))
        .text_color(if active { rgb(t.ink) } else { rgb(t.muted) })
        .cursor_pointer()
        .on_click(cx.listener(move |this, _, _, cx| {
            if this.fn_sort == sort {
                this.fn_sort_desc = !this.fn_sort_desc;
            } else {
                this.fn_sort = sort;
                this.fn_sort_desc = false;
            }
            cx.notify();
        }))
        .child(txt)
}

fn function_color_hex(f: &FunctionRow, t: &ThemeTokens) -> u32 {
    if f.category == "HIGH_LEVEL_VERIFIED" || f.is_certified {
        t.match_fill
    } else if f.category.starts_with("HIGH_LEVEL_") {
        t.polished
    } else if f.category == "UNRESOLVED" {
        t.not_c
    } else if f.is_thunk {
        t.tried_cell
    } else if f.instructions > 0 {
        t.match_hi
    } else {
        t.open_cell
    }
}

fn category_badge(f: &FunctionRow, t: &ThemeTokens) -> (&'static str, u32, u32) {
    if f.category == "HIGH_LEVEL_VERIFIED" || f.is_certified {
        ("VERIFIED", t.match_bg, t.match_ink)
    } else if f.category.starts_with("HIGH_LEVEL_") {
        ("HIGH-LEVEL", t.match_bg, t.polished)
    } else if f.category == "UNRESOLVED" {
        ("UNRESOLVED", t.miss_bg, t.miss)
    } else if f.is_thunk {
        ("THUNK", t.bg_alt, t.muted)
    } else {
        ("LIFTED", t.bg_alt, t.ink_2)
    }
}

fn kv_line(t: &ThemeTokens, k: &'static str, v: &str) -> Div {
    div()
        .flex()
        .items_start()
        .gap(px(10.0))
        .child(
            div()
                .w(px(120.0))
                .min_w(px(120.0))
                .font_family(FONT_MONO)
                .text_size(px(11.5))
                .text_color(rgb(t.muted))
                .child(k),
        )
        .child(
            div()
                .flex_1()
                .font_family(FONT_MONO)
                .text_size(px(11.5))
                .text_color(rgb(t.ink))
                .child(if v.is_empty() {
                    "-".to_string()
                } else {
                    v.to_string()
                }),
        )
}

fn truncate(s: &str, max_chars: usize) -> String {
    let mut it = s.chars();
    let head: String = it.by_ref().take(max_chars).collect();
    if it.next().is_some() && max_chars > 1 {
        let mut out: String = head.chars().take(max_chars - 1).collect();
        out.push('…');
        out
    } else {
        head
    }
}
