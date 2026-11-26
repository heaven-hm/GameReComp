use gpui::{div, prelude::*, px, rgb, Context, Div, FontWeight, MouseButton, Stateful, Styled};

use super::style;
use super::{FocusInput, GameReCompApp, JobStatus, ProjectTab};
use crate::theme_tokens::{
    MonoFont, ThemeMode, ThemeTokens, UiFont, CODE_FONT_SCALE_PRESETS, UI_FONT_SCALE_PRESETS,
};

// ============================================================================
// Page: Agents & Models
// ============================================================================
pub fn render_agents_page(
    app: &GameReCompApp,
    t: &ThemeTokens,
    viewport_w: f32,
    cx: &mut Context<GameReCompApp>,
) -> Stateful<Div> {
    let mut grid = div()
        .flex()
        .flex_row()
        .flex_wrap()
        .gap(px(22.0))
        .p(px(18.0));
    for (idx, ag) in app.toolchain.agents.iter().enumerate() {
        let preset = ag.preset_cmd.clone();
        let active = app.agent_cmd_buf.trim() == ag.preset_cmd.trim();

        grid = grid.child(
            div()
                .w(px(290.0))
                .flex_grow(1.0)
                .flex()
                .flex_col()
                .gap(px(6.0))
                .pt(px(12.0))
                .border_t_3()
                .border_color(rgb(t.ink))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .justify_between()
                        .child(
                            div()
                                .font_family(t.ui_font)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(t.ui_px(15.0)))
                                .text_color(rgb(t.ink))
                                .child(ag.name.clone()),
                        )
                        .child(if ag.installed {
                            style::status_badge("✓ INSTALLED", t.match_bg, t.match_ink)
                        } else {
                            style::status_badge("NOT FOUND", t.bg_alt, t.muted)
                        }),
                )
                .child(
                    div()
                        .font_family(t.ui_font)
                        .text_size(px(t.ui_px(12.5)))
                        .text_color(rgb(t.muted))
                        .child(ag.description.clone()),
                )
                .child(
                    div()
                        .font_family(t.mono_font)
                        .text_size(px(t.ui_px(11.0)))
                        .text_color(rgb(t.ink_2))
                        .child(ag.path.clone()),
                )
                .child(div().pt(px(4.0)).child(if active {
                    style::status_badge("ACTIVE PRESET", t.hl, t.hl_ink).into_any_element()
                } else {
                    style::ghost_btn(("ag_use_preset", idx), t, "Use preset")
                        .on_click(cx.listener(move |this, _, _, cx| {
                            this.agent_cmd_buf = preset.clone();
                            this.save_agent_cmd();
                            cx.notify();
                        }))
                        .into_any_element()
                })),
        );
    }

    let ai_cmd_active = app.effective_ai_cmd();
    let policy_panel = style::panel_box(t)
        .flex_shrink_0()
        .child(
            div()
                .flex()
                .flex_wrap()
                .items_center()
                .justify_between()
                .gap(px(8.0))
                .px(px(18.0))
                .py(px(12.0))
                .border_b_1()
                .border_color(rgb(t.line_soft))
                .child(
                    div()
                        .font_family(t.ui_font)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(t.ui_px(15.5)))
                        .text_color(rgb(t.ink))
                        .child("Optional AI Policy — Readable C Source Improvement Only"),
                )
                .child(if ai_cmd_active.is_some() {
                    style::status_badge(
                        "✓ AI ENABLED · READABILITY ONLY",
                        t.match_bg,
                        t.match_ink,
                    )
                } else {
                    style::status_badge(
                        "AI DISABLED · DETERMINISTIC ONLY",
                        t.bg_alt,
                        t.muted,
                    )
                }),
        )
        .child(
            div()
                .p(px(18.0))
                .flex()
                .flex_col()
                .gap(px(6.0))
                .child(info_kv(
                    t,
                    "100% Optional",
                    "Zero AI is required for disassembly, lifting, compiling, or running any game or app.",
                ))
                .child(info_kv(
                    t,
                    "Scope",
                    "AI is ONLY used when you enable it to improve readable C source code (names, comments, clarity).",
                ))
                .child(info_kv(
                    t,
                    "Safety Gate",
                    "Every AI edit is rebuilt and re-run against baseline evidence; failed edits are automatically reverted.",
                )),
        );

    page_shell(
        "agents_page_scroll",
        t,
        viewport_w,
        "AGENTS & MODELS",
        "Optional AI readability helpers",
        "AI is strictly optional and ONLY used to improve readable C source code. Core disassembly, lifting, compilation, and execution run 100% locally without AI.",
    )
    .child(policy_panel)
    .child(
        style::panel_box(t)
            .flex_shrink_0()
            .child(
                div()
                    .flex()
                    .items_center()
                    .justify_between()
                    .px(px(18.0))
                    .py(px(12.0))
                    .border_b_1()
                    .border_color(rgb(t.line_soft))
                    .child(
                        div()
                            .flex()
                            .items_center()
                            .gap(px(8.0))
                            .child(
                                div()
                                    .font_family(t.ui_font)
                                    .font_weight(FontWeight::BOLD)
                                    .text_size(px(t.ui_px(15.5)))
                                    .text_color(rgb(t.ink))
                                    .child("Agent apps on this computer"),
                            )
                            .when(app.toolchain_loading, |el| el.child(style::loading_spinner(t))),
                    )
                    .child(
                        style::ghost_btn("ag_refresh_btn", t, "↻ Refresh").on_click(cx.listener(
                            |this, _, window, cx| {
                                this.refresh_toolchain_async(window, cx);
                            },
                        )),
                    ),
            )
            .child(grid),
    )
    .child(
        style::panel_box(t)
            .flex_shrink_0()
            .child(
                div()
                    .px(px(18.0))
                    .py(px(12.0))
                    .border_b_1()
                    .border_color(rgb(t.line_soft))
                    .font_family(t.ui_font)
                    .font_weight(FontWeight::BOLD)
                    .text_size(px(t.ui_px(15.5)))
                    .text_color(rgb(t.ink))
                    .child("Custom REVENG_AI_CMD command"),
            )
            .child(
                div()
                    .p(px(18.0))
                    .flex()
                    .flex_col()
                    .gap(px(10.0))
                    .child(
                        div()
                            .font_family(t.ui_font)
                            .text_size(px(t.ui_px(13.0)))
                            .text_color(rgb(t.muted))
                            .child("Optional: GameReComp uses REVENG_AI_CMD solely to improve readable C source code (variable names, comments, structure) and compiles + verifies every edit against baseline evidence before accepting."),
                    )
                    .child(
                        div()
                            .flex()
                            .items_center()
                            .gap(px(8.0))
                            .child(
                                style::text_input_box(
                                    "ag_cmd_input",
                                    t,
                                    &app.agent_cmd_buf,
                                    "e.g. claude -p  |  codex exec -  |  ollama run qwen2.5-coder:14b",
                                    app.focused_input == Some(FocusInput::AgentCmd),
                                    true,
                                )
                                .flex_1()
                                .min_w_0()
                                .on_click(cx.listener(|this, _, window, cx| {
                                    this.set_focus_input(Some(FocusInput::AgentCmd), window, cx);
                                })),
                            )
                            .child(
                                style::primary_btn("ag_save_cmd", t, "Save command", true)
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        this.save_agent_cmd();
                                        cx.notify();
                                    })),
                            )
                            .child(
                                style::ghost_btn("ag_clear_cmd", t, "Clear").on_click(cx.listener(
                                    |this, _, _, cx| {
                                        this.agent_cmd_buf.clear();
                                        this.save_agent_cmd();
                                        cx.notify();
                                    },
                                )),
                            ),
                    ),
            ),
    )
}

// ============================================================================
// Page: Compilers & Tools
// ============================================================================
pub fn render_tools_page(
    app: &GameReCompApp,
    t: &ThemeTokens,
    viewport_w: f32,
    cx: &mut Context<GameReCompApp>,
) -> Stateful<Div> {
    let mut grid = div().flex().flex_row().flex_wrap().gap(px(14.0));
    for card in &app.toolchain.cards {
        grid = grid.child(
            style::panel_box(t)
                .w(px(320.0))
                .flex_grow(1.0)
                .p(px(16.0))
                .gap(px(6.0))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .justify_between()
                        .child(
                            div()
                                .font_family(t.ui_font)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(t.ui_px(15.0)))
                                .text_color(rgb(t.ink))
                                .child(card.name.clone()),
                        )
                        .child(if card.installed {
                            style::status_badge("✓ READY", t.match_bg, t.match_ink)
                        } else {
                            style::status_badge("MISSING", t.miss_bg, t.miss)
                        }),
                )
                .child(style::eyebrow(t, card.category.to_uppercase()))
                .child(
                    div()
                        .font_family(t.ui_font)
                        .text_size(px(t.ui_px(12.5)))
                        .text_color(rgb(t.ink_2))
                        .child(card.description.clone()),
                )
                .when(card.installed && !card.version.is_empty(), |el| {
                    el.child(
                        div()
                            .font_family(t.mono_font)
                            .text_size(px(t.ui_px(11.0)))
                            .text_color(rgb(t.match_ink))
                            .child(card.version.clone()),
                    )
                })
                .child(
                    div()
                        .font_family(t.mono_font)
                        .text_size(px(t.ui_px(11.0)))
                        .text_color(if card.installed {
                            rgb(t.muted)
                        } else {
                            rgb(t.work)
                        })
                        .child(if card.installed {
                            card.path.clone()
                        } else {
                            card.install_hint.clone()
                        }),
                ),
        );
    }

    page_shell(
        "tools_page_scroll",
        t,
        viewport_w,
        "COMPILERS & TOOLS",
        "What builds your games",
        "GameReComp uses Python for PE32 disassembly/lifting, C/CMake + SDL2 for native builds, and Clang/Node for WebAssembly.",
    )
    .child(
        div()
            .flex_shrink_0()
            .flex()
            .items_center()
            .gap(px(10.0))
            .child(
                style::primary_btn("tools_recheck_btn", t, "↻ Re-check toolchain", true).on_click(
                    cx.listener(|this, _, window, cx| {
                        this.refresh_toolchain_async(window, cx);
                    }),
                ),
            )
            .when(app.toolchain_loading, |el| el.child(style::loading_spinner(t))),
    )
    .child(grid.flex_shrink_0())
}

// ============================================================================
// Page: Global Settings
// ============================================================================
pub fn render_settings_page(
    app: &GameReCompApp,
    t: &ThemeTokens,
    viewport_w: f32,
    cx: &mut Context<GameReCompApp>,
) -> Stateful<Div> {
    let left = style::panel_box(t)
        .flex_1()
        .min_w(px(300.0))
        .child(
            div()
                .px(px(18.0))
                .py(px(12.0))
                .border_b_1()
                .border_color(rgb(t.line_soft))
                .font_family(t.ui_font)
                .font_weight(FontWeight::BOLD)
                .text_size(px(t.ui_px(15.5)))
                .text_color(rgb(t.ink))
                .child("Appearance & Workspace"),
        )
        .child(
            div()
                .p(px(18.0))
                .flex()
                .flex_col()
                .gap(px(14.0))
                .child({
                    let mut grid = div().flex().flex_wrap().gap(px(8.0));
                    for (idx, &mode) in ThemeMode::ALL.iter().enumerate() {
                        let p = ThemeTokens::for_mode(mode);
                        let active = app.theme == mode;
                        let hover_bg = t.bg_alt;
                        let hover_border = t.ink;
                        grid = grid.child(
                            div()
                                .id(("settings_theme_card", idx))
                                .w(px(176.0))
                                .p(px(10.0))
                                .rounded(px(4.0))
                                .bg(if active {
                                    rgb(t.hl_soft)
                                } else {
                                    rgb(t.code_bg)
                                })
                                .border_1()
                                .border_color(if active {
                                    if t.dark {
                                        rgb(t.hl)
                                    } else {
                                        rgb(t.ink)
                                    }
                                } else {
                                    rgb(t.line)
                                })
                                .cursor_pointer()
                                .hover(move |s| {
                                    s.bg(rgb(hover_bg)).border_color(rgb(hover_border))
                                })
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.set_theme(mode, cx);
                                }))
                                .flex()
                                .flex_col()
                                .gap(px(6.0))
                                // Mini palette stripe (bg, nav, panel, hl, match_fill)
                                .child(
                                    div()
                                        .w_full()
                                        .h(px(14.0))
                                        .rounded(px(2.5))
                                        .overflow_hidden()
                                        .border_1()
                                        .border_color(rgb(p.line))
                                        .flex()
                                        .child(div().flex_1().h_full().bg(rgb(p.nav)))
                                        .child(div().flex_1().h_full().bg(rgb(p.bg)))
                                        .child(div().flex_1().h_full().bg(rgb(p.panel)))
                                        .child(div().flex_1().h_full().bg(rgb(p.hl)))
                                        .child(div().flex_1().h_full().bg(rgb(p.match_fill))),
                                )
                                .child(
                                    div()
                                        .flex()
                                        .items_center()
                                        .justify_between()
                                        .gap(px(4.0))
                                        .child(
                                            div()
                                                .font_family(t.ui_font)
                                                .font_weight(FontWeight::BOLD)
                                                .text_size(px(t.ui_px(12.5)))
                                                .text_color(rgb(t.ink))
                                                .child(mode.label()),
                                        )
                                        .child(
                                            div()
                                                .font_family(t.mono_font)
                                                .font_weight(FontWeight::BOLD)
                                                .text_size(px(t.ui_px(9.5)))
                                                .text_color(if active {
                                                    rgb(t.match_ink)
                                                } else {
                                                    rgb(t.muted)
                                                })
                                                .child(if active {
                                                    "✓ ACTIVE"
                                                } else if mode.is_dark() {
                                                    "DARK"
                                                } else {
                                                    "LIGHT"
                                                }),
                                        ),
                                )
                                .child(
                                    div()
                                        .font_family(t.ui_font)
                                        .text_size(px(t.ui_px(11.0)))
                                        .text_color(rgb(t.muted))
                                        .child(mode.subtitle()),
                                ),
                        );
                    }

                    div()
                        .flex()
                        .flex_col()
                        .gap(px(8.0))
                        .child(
                            div()
                                .flex()
                                .items_center()
                                .justify_between()
                                .child(style::eyebrow(t, "THEME (6 HIGH-CONTRAST PALETTES)"))
                                .child(
                                    style::ghost_btn(
                                        "set_quick_dark_light",
                                        t,
                                        if app.theme.is_dark() {
                                            "☀ Switch to Light"
                                        } else {
                                            "☾ Switch to Dark"
                                        },
                                    )
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        this.toggle_theme(cx);
                                    })),
                                ),
                        )
                        .child(grid)
                })
                .child(render_typography_settings(app, t, cx))
                .child(
                    div()
                        .flex()
                        .flex_col()
                        .gap(px(5.0))
                        .child(style::eyebrow(t, "PROJECTS ROOT DIRECTORY"))
                        .child(
                            div()
                                .flex()
                                .gap(px(8.0))
                                .child(
                                    style::text_input_box(
                                        "set_proj_root",
                                        t,
                                        &app.settings.projects_root,
                                        "default: ~/GameReComp/projects",
                                        app.focused_input == Some(FocusInput::SettingsProjectsRoot),
                                        true,
                                    )
                                    .flex_1()
                                    .min_w_0()
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.set_focus_input(
                                            Some(FocusInput::SettingsProjectsRoot),
                                            window,
                                            cx,
                                        );
                                    })),
                                )
                                .child(
                                    style::ghost_btn("set_proj_root_pick", t, "Choose…").on_click(
                                        cx.listener(|this, _, window, cx| {
                                            this.pick_folder_async(
                                                "Choose projects root folder",
                                                window,
                                                cx,
                                                |this, d, _, _| {
                                                    this.settings.projects_root =
                                                        d.display().to_string();
                                                },
                                            );
                                        }),
                                    ),
                                ),
                        ),
                )
                .child(
                    div()
                        .flex()
                        .flex_col()
                        .gap(px(5.0))
                        .child(style::eyebrow(t, "GAME / EXECUTABLE LIBRARY FOLDER"))
                        .child(
                            div()
                                .flex()
                                .gap(px(8.0))
                                .child(
                                    style::text_input_box(
                                        "set_rom_lib",
                                        t,
                                        &app.settings.rom_library_dir,
                                        "/path/to/your/pe32/games",
                                        app.focused_input == Some(FocusInput::SettingsRomLib),
                                        true,
                                    )
                                    .flex_1()
                                    .min_w_0()
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.set_focus_input(
                                            Some(FocusInput::SettingsRomLib),
                                            window,
                                            cx,
                                        );
                                    })),
                                )
                                .child(
                                    style::ghost_btn("set_rom_lib_pick", t, "Choose…").on_click(
                                        cx.listener(|this, _, window, cx| {
                                            this.pick_folder_async(
                                                "Choose game / executable library folder",
                                                window,
                                                cx,
                                                |this, d, _, _| {
                                                    this.settings.rom_library_dir =
                                                        d.display().to_string();
                                                },
                                            );
                                        }),
                                    ),
                                ),
                        ),
                )
                .child(
                    div()
                        .flex()
                        .flex_col()
                        .gap(px(6.0))
                        .child(style::eyebrow(t, "MAX PARALLEL GAMES (CONCURRENT DECOMPILATION)"))
                        .child({
                            let auto_label = format!(
                                "Auto ({})",
                                app.sys_resources.recommended_parallel_games
                            );
                            let mut row = div().flex().flex_wrap().gap(px(6.0)).child(
                                style::option_pill(
                                    ("set_max_par", 0usize),
                                    t,
                                    auto_label,
                                    app.max_concurrent_jobs == 0,
                                )
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.set_max_concurrent_jobs(0, cx);
                                })),
                            );
                            for n in [1usize, 2, 3, 4, 6, 8] {
                                let label = if n == 1 {
                                    "1 (Sequential)".to_string()
                                } else {
                                    format!("{n} Games")
                                };
                                row = row.child(
                                    style::option_pill(
                                        ("set_max_par", n),
                                        t,
                                        label,
                                        app.max_concurrent_jobs == n,
                                    )
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.set_max_concurrent_jobs(n, cx);
                                    })),
                                );
                            }
                            row
                        })
                        .child(
                            div()
                                .font_family(t.ui_font)
                                .text_size(px(t.ui_px(11.5)))
                                .text_color(rgb(t.muted))
                                .child("Auto picks optimal parallel workers from detected CPU cores & RAM; choose a number to override."),
                        ),
                )
                .child(
                    div().pt(px(4.0)).child(
                        style::primary_btn("set_save_btn", t, "Save settings", true).on_click(
                            cx.listener(|this, _, window, cx| {
                                match this.settings.save() {
                                    Ok(_) => {
                                        this.refresh_projects_async(window, cx);
                                        this.push_toast(
                                            "Settings saved",
                                            "Preferences written to ~/.gamerecomp/settings.json.",
                                            this.tokens().match_fill,
                                            false,
                                        );
                                    }
                                    Err(e) => {
                                        this.push_toast("Save failed", &e, this.tokens().not_c, true);
                                    }
                                }
                                cx.notify();
                            }),
                        ),
                    ),
                ),
        );

    let toolchain_txt = if app.toolchain_loading {
        "Detecting toolchain in background…".to_string()
    } else {
        app.toolchain.format_selftest_line()
    };

    let right = style::panel_box(t)
        .flex_1()
        .min_w(px(300.0))
        .child(
            div()
                .px(px(18.0))
                .py(px(12.0))
                .border_b_1()
                .border_color(rgb(t.line_soft))
                .font_family(t.ui_font)
                .font_weight(FontWeight::BOLD)
                .text_size(px(t.ui_px(15.5)))
                .text_color(rgb(t.ink))
                .child("This computer"),
        )
        .child(
            div()
                .p(px(18.0))
                .flex()
                .flex_col()
                .gap(px(8.0))
                .child(info_kv(
                    t,
                    "Host OS / Arch",
                    &format!("{} / {}", std::env::consts::OS, std::env::consts::ARCH),
                ))
                .child(info_kv(t, "System Resources", &app.sys_resources.summary()))
                .child(info_kv(t, "Parallel Workers", &app.max_concurrent_label()))
                .child(info_kv(
                    t,
                    "Projects Directory",
                    &app.projects_root().display().to_string(),
                ))
                .child(info_kv(
                    t,
                    "Settings File",
                    &crate::config::UserSettings::settings_path()
                        .map(|p| p.display().to_string())
                        .unwrap_or_else(|| "-".to_string()),
                ))
                .child(info_kv(t, "Toolchain", &toolchain_txt)),
        );

    page_shell(
        "settings_page_scroll",
        t,
        viewport_w,
        "SETTINGS",
        "How GameReComp works for you",
        "Customize theme, project directories, parallel workers, and your local game library.",
    )
    .child(
        div()
            .flex_shrink_0()
            .flex()
            .flex_row()
            .flex_wrap()
            .items_start()
            .gap(px(16.0))
            .child(left)
            .child(right),
    )
}

// ============================================================================
// Page: About GameReComp (Dedicated editorial About section!)
// ============================================================================
pub fn render_about_page(
    app: &GameReCompApp,
    t: &ThemeTokens,
    viewport_w: f32,
    cx: &mut Context<GameReCompApp>,
) -> Stateful<Div> {
    let arch_cards = div()
        .flex()
        .flex_row()
        .flex_wrap()
        .gap(px(16.0))
        .child(
            style::ruled_kpi(t, "TIER 1 · RUST + GPUI + CLAP")
                .child(
                    div()
                        .font_family(t.ui_font)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(t.ui_px(18.0)))
                        .text_color(rgb(t.ink))
                        .child("Native Desktop GUI & CLI"),
                )
                .child(
                    div()
                        .font_family(t.ui_font)
                        .text_size(px(t.ui_px(13.0)))
                        .text_color(rgb(t.ink_2))
                        .child("GPU-accelerated GPUI desktop interface + clap v4 CLI orchestrator, batch runner, WASM HTTP server, and PE32 inspector."),
                ),
        )
        .child(
            style::ruled_kpi(t, "TIER 2 · PYTHON PCRECOMP")
                .child(
                    div()
                        .font_family(t.ui_font)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(t.ui_px(18.0)))
                        .text_color(rgb(t.ink))
                        .child("Disassembler & Lifter"),
                )
                .child(
                    div()
                        .font_family(t.ui_font)
                        .text_size(px(t.ui_px(13.0)))
                        .text_color(rgb(t.ink_2))
                        .child("Deterministic x86 PE32 disassembly, CFG recovery, static C lifting, provenance verification, and oracle testing."),
                ),
        )
        .child(
            style::ruled_kpi(t, "TIER 3 · C + WIN32 HLE + WASM32")
                .child(
                    div()
                        .font_family(t.ui_font)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(t.ui_px(18.0)))
                        .text_color(rgb(t.ink))
                        .child("Standalone Native & Web"),
                )
                .child(
                    div()
                        .font_family(t.ui_font)
                        .text_size(px(t.ui_px(13.0)))
                        .text_color(rgb(t.ink_2))
                        .child("Self-contained C Win32 HLE runtime (SDL2 native + wasm32 browser canvas) plus WASM-compatible game_recomp Rust core library."),
                ),
        );

    let shortcuts = style::panel_box(t)
        .flex_1()
        .min_w(px(320.0))
        .child(
            div()
                .px(px(18.0))
                .py(px(12.0))
                .border_b_1()
                .border_color(rgb(t.line_soft))
                .font_family(t.ui_font)
                .font_weight(FontWeight::BOLD)
                .text_size(px(t.ui_px(15.0)))
                .text_color(rgb(t.ink))
                .child("Keyboard shortcuts"),
        )
        .child(
            div()
                .p(px(18.0))
                .flex()
                .flex_col()
                .gap(px(8.0))
                .child(info_kv(
                    t,
                    "Esc",
                    "Close Function Inspector, popovers, or blur text input",
                ))
                .child(info_kv(
                    t,
                    "J / K",
                    "Next / previous function inside Function Inspector",
                ))
                .child(info_kv(
                    t,
                    "/",
                    "Focus function search bar in Functions tab",
                ))
                .child(info_kv(t, "Cmd/Ctrl + V", "Paste into focused text field"))
                .child(info_kv(
                    t,
                    "Cmd/Ctrl + +/-/0",
                    "Increase, decrease, or reset UI font size",
                ))
                .child(info_kv(
                    t,
                    "Drag & Drop",
                    "Drop any .exe or project folder anywhere on Home",
                )),
        );

    let meta_panel = style::panel_box(t)
        .flex_1()
        .min_w(px(360.0))
        .child(
            div()
                .px(px(18.0))
                .py(px(12.0))
                .border_b_1()
                .border_color(rgb(t.line_soft))
                .font_family(t.ui_font)
                .font_weight(FontWeight::BOLD)
                .text_size(px(t.ui_px(15.0)))
                .text_color(rgb(t.ink))
                .child("Build, WASM readiness & Licenses"),
        )
        .child(
            div()
                .p(px(18.0))
                .flex()
                .flex_col()
                .gap(px(8.0))
                .child(info_kv(t, "Version", &format!("GameReComp v{}", crate::cli::VERSION)))
                .child(info_kv(
                    t,
                    "License",
                    "GNU Affero General Public License v3.0 (AGPL-3.0-only)",
                ))
                .child(info_kv(
                    t,
                    "AI Policy",
                    "Optional — ONLY used to improve readable C source code (compile- & evidence-verified)",
                ))
                .child(info_kv(t, "GUI Framework", "GPUI Kit 0.10 (longbridge/gpui-kit + gpui-component)"))
                .child(info_kv(t, "Binaries", "game-recomp-cli (CLI) & game-recomp-gui (GPUI Kit)"))
                .child(info_kv(t, "WASM Core", "game_recomp verified on wasm32-unknown-unknown"))
                .child(info_kv(t, "Typography", "Archivo (OFL-1.1) & IBM Plex Mono (OFL-1.1)"))
                .child(info_kv(t, "Host", &format!("{} / {}", std::env::consts::OS, std::env::consts::ARCH)))
                .child(info_kv(t, "Projects Loaded", &format!("{} project(s)", app.projects.len())))
                .child(
                    div()
                        .pt(px(8.0))
                        .flex()
                        .flex_wrap()
                        .gap(px(8.0))
                        .child(
                            style::ghost_btn("about_open_projects", t, "Open Projects Folder")
                                .on_click(cx.listener(|this, _, _, _| {
                                    let _ = open::that(&this.projects_root());
                                })),
                        )
                        .child(
                            style::ghost_btn("about_report_issue", t, "🐛 Report Issue").on_click(
                                cx.listener(|this, _, _, cx| {
                                    this.report_problem(cx);
                                }),
                            ),
                        ),
                ),
        );

    page_shell(
        "about_page_scroll",
        t,
        viewport_w,
        "ABOUT",
        "GameReComp",
        "Any game or app, any OS, any architecture — accepts Windows, macOS and Linux inputs across x86, x86_64 and arm64; PE32 i386 reconstruction is fail-closed to the host.",
    )
    .child(
        div()
            .flex_shrink_0()
            .flex()
            .flex_wrap()
            .items_center()
            .gap(px(14.0))
            .child(style::brand_mark(t))
            .child(style::chip_inverted(t, format!("v{}", crate::cli::VERSION)))
            .child(style::chip_outlined(
                t,
                "GPUI Kit 0.10",
                Some(t.accent_fg()),
            ))
            .child(style::chip_outlined(
                t,
                "AGPL-3.0-only",
                Some(t.info),
            ))
            .child(style::chip_outlined(t, "WASM32 READY", Some(t.match_ink))),
    )
    .child(arch_cards.flex_shrink_0())
    .child(
        div()
            .flex_shrink_0()
            .flex()
            .flex_row()
            .flex_wrap()
            .gap(px(16.0))
            .child(shortcuts)
            .child(meta_panel),
    )
}

fn render_typography_settings(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Div {
    let ui_pct = (app.font_scale * 100.0).round() as i32;
    let code_pct = (app.code_font_scale * 100.0).round() as i32;

    // UI Font family pills (each rendered in its own typeface!)
    let mut ui_font_row = div().flex().flex_wrap().gap(px(6.0));
    for (idx, &font) in UiFont::ALL.iter().enumerate() {
        let selected = app.ui_font == font;
        ui_font_row = ui_font_row.child(
            style::option_pill(("set_ui_font", idx), t, font.label(), selected)
                .font_family(font.family_name())
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.set_ui_font(font, cx);
                })),
        );
    }

    // Code / Monospace font family pills (each rendered in its own monospace typeface!)
    let mut mono_font_row = div().flex().flex_wrap().gap(px(6.0));
    for (idx, &font) in MonoFont::ALL.iter().enumerate() {
        let selected = app.mono_font == font;
        mono_font_row = mono_font_row.child(
            style::option_pill(("set_mono_font", idx), t, font.label(), selected)
                .font_family(font.family_name())
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.set_mono_font(font, cx);
                })),
        );
    }

    // UI Font Size presets + fine A- / % / A+ steppers
    let mut ui_size_row = div().flex().flex_wrap().items_center().gap(px(6.0));
    for (idx, (label, scale)) in UI_FONT_SCALE_PRESETS.iter().enumerate() {
        let selected = (app.font_scale - *scale).abs() < 0.025;
        let sc = *scale;
        ui_size_row = ui_size_row.child(
            style::option_pill(
                ("set_ui_size", idx),
                t,
                format!("{label} ({:.0}%)", sc * 100.0),
                selected,
            )
            .on_click(cx.listener(move |this, _, _, cx| {
                this.set_font_scale(sc, cx);
            })),
        );
    }
    ui_size_row = ui_size_row
        .child(
            style::ghost_btn("set_ui_dec", t, "A−").on_click(cx.listener(|this, _, _, cx| {
                this.step_font_scale(-0.05, cx);
            })),
        )
        .child(style::chip_outlined(t, format!("{ui_pct}%"), Some(t.info)))
        .child(
            style::ghost_btn("set_ui_inc", t, "A+").on_click(cx.listener(|this, _, _, cx| {
                this.step_font_scale(0.05, cx);
            })),
        );

    // Code / Source Font Size presets + fine - / % / + steppers
    let mut code_size_row = div().flex().flex_wrap().items_center().gap(px(6.0));
    for (idx, (label, scale)) in CODE_FONT_SCALE_PRESETS.iter().enumerate() {
        let selected = (app.code_font_scale - *scale).abs() < 0.025;
        let sc = *scale;
        code_size_row = code_size_row.child(
            style::option_pill(
                ("set_code_size", idx),
                t,
                format!("{label} ({:.0}%)", sc * 100.0),
                selected,
            )
            .on_click(cx.listener(move |this, _, _, cx| {
                this.set_code_font_scale(sc, cx);
            })),
        );
    }
    code_size_row = code_size_row
        .child(
            style::ghost_btn("set_code_dec", t, "−").on_click(cx.listener(|this, _, _, cx| {
                this.step_code_font_scale(-0.05, cx);
            })),
        )
        .child(style::chip_outlined(
            t,
            format!("{code_pct}%"),
            Some(t.info),
        ))
        .child(
            style::ghost_btn("set_code_inc", t, "+").on_click(cx.listener(|this, _, _, cx| {
                this.step_code_font_scale(0.05, cx);
            })),
        );

    // Live Typography Preview Box
    let preview = div()
        .p(px(12.0))
        .rounded(px(4.0))
        .bg(rgb(t.code_bg))
        .border_1()
        .border_color(rgb(t.line))
        .flex()
        .flex_col()
        .gap(px(6.0))
        .child(
            div()
                .font_family(t.ui_font)
                .font_weight(FontWeight::BOLD)
                .text_size(px(t.ui_px(15.0)))
                .text_color(rgb(t.ink))
                .child("Live Preview · Any game or app, any OS, any architecture — PE32 i386 rebuilds; other inputs report exact status."),
        )
        .child(
            div()
                .font_family(t.ui_font)
                .text_size(px(t.ui_px(12.5)))
                .text_color(rgb(t.ink_2))
                .child(format!(
                    "UI Font: {} ({ui_pct}%) · Code Font: {} ({code_pct}%)",
                    app.ui_font.label(),
                    app.mono_font.label()
                )),
        )
        .child(
            div()
                .font_family(t.mono_font)
                .text_size(px(t.code_px(12.0)))
                .text_color(rgb(t.match_ink))
                .child("int32_t sub_00401000(RecompState *s) { return hle_present_frame(s, 60); }"),
        );

    div()
        .p(px(14.0))
        .rounded(px(4.0))
        .bg(rgb(t.bg_alt))
        .border_1()
        .border_color(rgb(t.line_soft))
        .flex()
        .flex_col()
        .gap(px(12.0))
        .child(
            div()
                .flex()
                .items_center()
                .justify_between()
                .child(style::eyebrow(t, "FONTS & FONT SIZE"))
                .child(
                    style::ghost_btn("set_typo_reset", t, "Reset fonts").on_click(cx.listener(
                        |this, _, _, cx| {
                            this.reset_typography(cx);
                        },
                    )),
                ),
        )
        .child(
            div()
                .flex()
                .flex_col()
                .gap(px(5.0))
                .child(style::eyebrow(t, "UI FONT FAMILY"))
                .child(ui_font_row),
        )
        .child(
            div()
                .flex()
                .flex_col()
                .gap(px(5.0))
                .child(style::eyebrow(t, "CODE / MONOSPACE FONT"))
                .child(mono_font_row),
        )
        .child(
            div()
                .flex()
                .flex_col()
                .gap(px(5.0))
                .child(style::eyebrow(
                    t,
                    "UI FONT SIZE (SHORTCUT: CMD/CTRL + / - / 0)",
                ))
                .child(ui_size_row),
        )
        .child(
            div()
                .flex()
                .flex_col()
                .gap(px(5.0))
                .child(style::eyebrow(t, "CODE & SOURCE VIEWER FONT SIZE"))
                .child(code_size_row),
        )
        .child(preview)
}

fn page_shell(
    scroll_id: &'static str,
    t: &ThemeTokens,
    viewport_w: f32,
    eyebrow_txt: &'static str,
    title: &'static str,
    subtitle: &'static str,
) -> Stateful<Div> {
    let pad_x = if viewport_w < 1020.0 { 18.0 } else { 36.0 };
    div()
        .id(scroll_id)
        .flex_1()
        .min_w_0()
        .h_full()
        .overflow_y_scroll()
        .px(px(pad_x))
        .pt(px(28.0))
        .pb(px(56.0))
        .flex()
        .flex_col()
        .gap(px(20.0))
        .child(
            div()
                .flex_shrink_0()
                .max_w(px(820.0))
                .flex()
                .flex_col()
                .gap(px(8.0))
                .child(style::eyebrow(t, eyebrow_txt))
                .child(
                    div()
                        .font_family(t.ui_font)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(t.ui_px(34.0)))
                        .line_height(px(t.ui_px(38.0)))
                        .text_color(rgb(t.ink))
                        .child(title),
                )
                .child(
                    div()
                        .font_family(t.ui_font)
                        .text_size(px(t.ui_px(14.5)))
                        .text_color(rgb(t.muted))
                        .child(subtitle),
                ),
        )
}

fn info_kv(t: &ThemeTokens, k: &'static str, v: &str) -> Div {
    div()
        .flex()
        .items_start()
        .gap(px(10.0))
        .child(
            div()
                .w(px(130.0))
                .min_w(px(130.0))
                .font_family(t.mono_font)
                .font_weight(FontWeight::BOLD)
                .text_size(px(t.ui_px(11.5)))
                .text_color(rgb(t.muted))
                .child(k),
        )
        .child(
            div()
                .flex_1()
                .min_w_0()
                .font_family(t.mono_font)
                .text_size(px(t.ui_px(12.0)))
                .text_color(rgb(t.ink))
                .child(v.to_string()),
        )
}

// ============================================================================
// Right-Click Project CRUD + Launch Context Menu & Modals
// ============================================================================
fn ctx_menu_row(
    id: &'static str,
    t: &ThemeTokens,
    label: &'static str,
    danger: bool,
) -> Stateful<Div> {
    let bg_hover = if danger { t.miss_bg } else { t.bg_alt };
    let fg = if danger { t.miss } else { t.ink };
    div()
        .id(id)
        .w_full()
        .px(px(10.0))
        .py(px(6.0))
        .rounded(px(4.0))
        .font_family(t.ui_font)
        .font_weight(FontWeight::MEDIUM)
        .text_size(px(t.ui_px(12.5)))
        .text_color(rgb(fg))
        .cursor_pointer()
        .hover(move |s| s.bg(rgb(bg_hover)))
        .child(label)
}

pub fn render_project_context_menu(
    app: &GameReCompApp,
    t: &ThemeTokens,
    viewport_w: f32,
    viewport_h: f32,
    cx: &mut Context<GameReCompApp>,
) -> Option<Stateful<Div>> {
    let cm = app.context_menu.as_ref()?;
    let menu_w = 264.0f32;
    let menu_h = 468.0f32;
    let left = cm.x.min((viewport_w - menu_w - 12.0).max(12.0)).max(12.0);
    let top = cm.y.min((viewport_h - menu_h - 12.0).max(12.0)).max(12.0);

    let proj_job = app.job_for_project(&cm.dir);
    let active_job_id = proj_job.filter(|j| j.is_active()).map(|j| j.id);
    let is_running = proj_job.map(|j| j.is_running()).unwrap_or(false);
    let is_paused = proj_job.map(|j| j.is_paused()).unwrap_or(false);
    let cached_proj = app.projects.iter().find(|p| p.dir == cm.dir);
    let runnable = cached_proj
        .map(|p| p.has_binary || p.has_wasm)
        .unwrap_or(true);
    let issue_job_id = proj_job
        .filter(|j| matches!(j.status, JobStatus::Failed(_) | JobStatus::Stopped))
        .map(|j| j.id);

    let (status_lbl, status_bg, status_fg) = if is_running {
        ("RUNNING", t.hl, t.hl_ink)
    } else if is_paused {
        ("PAUSED", t.work_bg, t.work)
    } else if runnable {
        ("READY", t.match_bg, t.match_ink)
    } else {
        ("UNBUILT", t.bg_alt, t.muted)
    };

    let dir_open = cm.dir.clone();
    let dir_standalone = cm.dir.clone();
    let dir_data = cm.dir.clone();
    let name_data = cm.name.clone();
    let dir_opts = cm.dir.clone();
    let name_opts = cm.name.clone();
    let dir_rerun = cm.dir.clone();
    let dir_ai = cm.dir.clone();
    let dir_rename = cm.dir.clone();
    let name_rename = cm.name.clone();
    let dir_settings = cm.dir.clone();
    let dir_finder = cm.dir.clone();
    let dir_del = cm.dir.clone();
    let name_del = cm.name.clone();
    let dir_issue = cm.dir.clone();

    let mut card = div()
        .id("proj_ctx_menu_card")
        .absolute()
        .left(px(left))
        .top(px(top))
        .w(px(menu_w))
        .p(px(6.0))
        .rounded(px(6.0))
        .bg(rgb(t.overlay))
        .border_1()
        .border_color(rgb(t.line))
        .shadow_lg()
        .flex()
        .flex_col()
        .gap(px(2.0))
        .on_mouse_down(
            MouseButton::Left,
            cx.listener(|_, _, _, cx| {
                cx.stop_propagation();
            }),
        )
        .on_mouse_down(
            MouseButton::Right,
            cx.listener(|_, _, _, cx| {
                cx.stop_propagation();
            }),
        )
        .child(
            div()
                .flex()
                .items_center()
                .justify_between()
                .gap(px(6.0))
                .px(px(8.0))
                .py(px(6.0))
                .mb(px(2.0))
                .border_b_1()
                .border_color(rgb(t.line_soft))
                .child(
                    div()
                        .flex_1()
                        .min_w_0()
                        .overflow_hidden()
                        .font_family(t.ui_font)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(t.ui_px(12.5)))
                        .text_color(rgb(t.ink))
                        .whitespace_nowrap()
                        .child(cm.name.clone()),
                )
                .child(style::status_badge(status_lbl, status_bg, status_fg)),
        )
        .child(
            ctx_menu_row("ctx_open", t, "↗  Open & Inspect", false).on_click(cx.listener(
                move |this, _, window, cx| {
                    this.context_menu = None;
                    this.open_project_dir_async(dir_open.clone(), ProjectTab::Overview, window, cx);
                },
            )),
        )
        .when(runnable, |el| {
            el.child(
                ctx_menu_row("ctx_run_standalone", t, "▶  Run Standalone (Direct)", false)
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.context_menu = None;
                        this.launch_project_dir_smart(&dir_standalone, true);
                        cx.notify();
                    })),
            )
        })
        .child(
            ctx_menu_row("ctx_run_data", t, "📁  Run with Game Data Folder…", false).on_click(
                cx.listener(move |this, _, _, cx| {
                    this.open_run_game_modal(dir_data.clone(), name_data.clone(), true, cx);
                }),
            ),
        )
        .child(
            ctx_menu_row("ctx_run_opts", t, "⚙  Run Game Options…", false).on_click(cx.listener(
                move |this, _, _, cx| {
                    this.open_run_game_modal(dir_opts.clone(), name_opts.clone(), false, cx);
                },
            )),
        )
        .child(div().my(px(3.0)).h(px(1.0)).w_full().bg(rgb(t.line_soft)));

    if let Some(jid) = active_job_id {
        if is_running {
            card = card.child(
                ctx_menu_row("ctx_pause", t, "⏸  Pause Recompilation", false).on_click(
                    cx.listener(move |this, _, _, cx| {
                        this.context_menu = None;
                        this.pause_job(jid);
                        cx.notify();
                    }),
                ),
            );
        } else if is_paused {
            card = card.child(
                ctx_menu_row("ctx_resume", t, "▶  Resume Recompilation", false).on_click(
                    cx.listener(move |this, _, _, cx| {
                        this.context_menu = None;
                        this.resume_job(jid);
                        cx.notify();
                    }),
                ),
            );
        }
        card = card.child(
            ctx_menu_row("ctx_stop", t, "■  Stop Recompilation", true).on_click(cx.listener(
                move |this, _, _, cx| {
                    this.context_menu = None;
                    this.stop_job(jid);
                    cx.notify();
                },
            )),
        );
    } else {
        card = card
            .child(
                ctx_menu_row("ctx_rerun", t, "↻  Re-run Recompilation", false).on_click(
                    cx.listener(move |this, _, _, cx| {
                        this.rerun_project_by_dir(dir_rerun.clone(), cx);
                    }),
                ),
            )
            .child(
                ctx_menu_row(
                    "ctx_ai_readability",
                    t,
                    "✨  Improve C Readability (AI, optional)",
                    false,
                )
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.context_menu = None;
                    this.start_readability_agent_for_dir(dir_ai.clone(), None, cx);
                })),
            );
    }

    card = card
        .child(
            ctx_menu_row("ctx_issue", t, "🐛  Create Support Issue", false).on_click(cx.listener(
                move |this, _, window, cx| {
                    this.context_menu = None;
                    if let Some(id) = issue_job_id {
                        this.open_issue_for_job(id, cx);
                    } else if this.active_project_dir.as_ref() == Some(&dir_issue) {
                        this.open_issue_for_project(cx);
                    } else {
                        this.open_project_dir_async(
                            dir_issue.clone(),
                            ProjectTab::Overview,
                            window,
                            cx,
                        );
                        this.push_toast(
                            "Opened project",
                            "Project loaded — click Create Issue in the header to attach full report details.",
                            this.tokens().info,
                            false,
                        );
                    }
                },
            )),
        )
        .child(div().my(px(3.0)).h(px(1.0)).w_full().bg(rgb(t.line_soft)))
        .child(
            ctx_menu_row("ctx_add_new", t, "+  Add New Game (.exe)…", false).on_click(
                cx.listener(|this, _, window, cx| {
                    this.context_menu = None;
                    this.browse_for_pe(window, cx);
                }),
            ),
        )
        .child(
            ctx_menu_row("ctx_rename", t, "✎  Rename Project…", false).on_click(cx.listener(
                move |this, _, window, cx| {
                    this.open_rename_project_modal(
                        dir_rename.clone(),
                        name_rename.clone(),
                        window,
                        cx,
                    );
                },
            )),
        )
        .child(
            ctx_menu_row("ctx_settings", t, "⚙  Update Project Settings", false).on_click(
                cx.listener(move |this, _, window, cx| {
                    this.context_menu = None;
                    this.open_project_dir_async(
                        dir_settings.clone(),
                        ProjectTab::Settings,
                        window,
                        cx,
                    );
                }),
            ),
        )
        .child(
            ctx_menu_row("ctx_open_folder", t, "📂  Open Folder in File Manager", false)
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.context_menu = None;
                    let _ = open::that(&dir_finder);
                    cx.notify();
                })),
        )
        .child(div().my(px(3.0)).h(px(1.0)).w_full().bg(rgb(t.line_soft)))
        .child(
            ctx_menu_row("ctx_delete", t, "🗑  Remove / Delete Project…", true).on_click(
                cx.listener(move |this, _, _, cx| {
                    this.open_delete_project_modal(dir_del.clone(), name_del.clone(), cx);
                }),
            ),
        );

    Some(
        div()
            .id("proj_ctx_backdrop")
            .absolute()
            .top_0()
            .left_0()
            .size_full()
            .on_mouse_down(
                MouseButton::Left,
                cx.listener(|this, _, _, cx| {
                    this.close_project_context_menu(cx);
                }),
            )
            .on_mouse_down(
                MouseButton::Right,
                cx.listener(|this, _, _, cx| {
                    this.close_project_context_menu(cx);
                }),
            )
            .child(card),
    )
}

pub fn render_run_game_modal(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Option<Stateful<Div>> {
    let modal = app.run_game_modal.as_ref()?;
    let browser_only = modal.has_wasm && modal.binary_path.is_none();
    let proj_dir_wasm = modal.project_dir.clone();
    let suggested = modal.suggested_pe_dir.clone();

    let card_style = |selected: bool| {
        if selected {
            (rgb(t.hl_soft), if t.dark { rgb(t.hl) } else { rgb(t.btn) })
        } else {
            (rgb(t.code_bg), rgb(t.line))
        }
    };
    let (standalone_bg, standalone_bd) = card_style(!modal.use_data_dir);
    let (data_bg, data_bd) = card_style(modal.use_data_dir);

    let bin_note = match modal.binary_path {
        Some(ref b) => format!(
            "Executable: {}",
            super::sanitize_home_path(&b.display().to_string())
        ),
        None if modal.has_wasm => "Target ready: WebAssembly browser build".to_string(),
        None => "No native binary built yet — run the pipeline first.".to_string(),
    };

    Some(
        div()
            .id("run_game_modal_backdrop")
            .absolute()
            .top_0()
            .left_0()
            .size_full()
            .bg(style::scrim_bg())
            .flex()
            .items_center()
            .justify_center()
            .p(px(20.0))
            .on_mouse_down(
                MouseButton::Left,
                cx.listener(|this, _, _, cx| {
                    this.close_run_game_modal(cx);
                }),
            )
            .child(
                style::panel_box(t)
                    .id("run_game_modal_card")
                    .w(px(620.0))
                    .max_w(px(620.0))
                    .bg(rgb(t.overlay))
                    .border_color(rgb(t.ink))
                    .shadow_lg()
                    .p(px(20.0))
                    .flex()
                    .flex_col()
                    .gap(px(14.0))
                    .on_mouse_down(
                        MouseButton::Left,
                        cx.listener(|_, _, _, cx| {
                            cx.stop_propagation();
                        }),
                    )
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
                                    .child(style::eyebrow(t, "RUN GAME OPTIONS"))
                                    .child(
                                        div()
                                            .font_family(t.ui_font)
                                            .font_weight(FontWeight::EXTRA_BOLD)
                                            .text_size(px(t.ui_px(18.0)))
                                            .text_color(rgb(t.ink))
                                            .child(format!("Launch {}", modal.project_name)),
                                    ),
                            )
                            .child(
                                style::ghost_btn("run_modal_close", t, "✕").on_click(cx.listener(
                                    |this, _, _, cx| {
                                        this.close_run_game_modal(cx);
                                    },
                                )),
                            ),
                    )
                    .child(
                        div()
                            .font_family(t.mono_font)
                            .text_size(px(t.ui_px(11.0)))
                            .text_color(rgb(t.muted))
                            .child(bin_note),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .gap(px(8.0))
                            .child(
                                div()
                                    .id("run_mode_standalone")
                                    .p(px(12.0))
                                    .rounded(px(5.0))
                                    .bg(standalone_bg)
                                    .border_1()
                                    .border_color(standalone_bd)
                                    .cursor_pointer()
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        if let Some(ref mut m) = this.run_game_modal {
                                            m.use_data_dir = false;
                                        }
                                        cx.notify();
                                    }))
                                    .flex()
                                    .flex_col()
                                    .gap(px(3.0))
                                    .child(
                                        div()
                                            .font_family(t.ui_font)
                                            .font_weight(FontWeight::BOLD)
                                            .text_size(px(t.ui_px(13.5)))
                                            .text_color(rgb(t.ink))
                                            .child(if browser_only {
                                                "🌐 Run in Browser (WASM)"
                                            } else {
                                                "⚡ Run Standalone (Direct)"
                                            }),
                                    )
                                    .child(
                                        div()
                                            .font_family(t.ui_font)
                                            .text_size(px(t.ui_px(12.0)))
                                            .text_color(rgb(t.muted))
                                            .child(if browser_only {
                                                "Opens this project in your browser using the WebAssembly build."
                                            } else {
                                                "Launches the self-contained rebuilt binary directly using its embedded PE image (no external game folder needed)."
                                            }),
                                    ),
                            )
                            .when(modal.binary_path.is_some() || !modal.has_wasm, |el| {
                                el.child(
                                    div()
                                    .id("run_mode_data_dir")
                                    .p(px(12.0))
                                    .rounded(px(5.0))
                                    .bg(data_bg)
                                    .border_1()
                                    .border_color(data_bd)
                                    .cursor_pointer()
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        if let Some(ref mut m) = this.run_game_modal {
                                            m.use_data_dir = true;
                                        }
                                        this.set_focus_input(
                                            Some(FocusInput::RunGameDataDir),
                                            window,
                                            cx,
                                        );
                                    }))
                                    .flex()
                                    .flex_col()
                                    .gap(px(3.0))
                                    .child(
                                        div()
                                            .font_family(t.ui_font)
                                            .font_weight(FontWeight::BOLD)
                                            .text_size(px(t.ui_px(13.5)))
                                            .text_color(rgb(t.ink))
                                            .child("📁 Run with Game Data / Assets Folder"),
                                    )
                                    .child(
                                        div()
                                            .font_family(t.ui_font)
                                            .text_size(px(t.ui_px(12.0)))
                                            .text_color(rgb(t.muted))
                                            .child("Passes the selected folder via working directory, RECOMP_ASSETS_DIR, and argv[1] for games requiring external levels, textures, or saves."),
                                    ),
                            )
                            }),
                    )
                    .when(modal.use_data_dir, |el| {
                        el.child(
                            div()
                                .flex()
                                .flex_col()
                                .gap(px(6.0))
                                .child(style::eyebrow(t, "GAME DATA / ASSETS DIRECTORY"))
                                .child(
                                    div()
                                        .flex()
                                        .items_center()
                                        .gap(px(8.0))
                                        .child(
                                            style::text_input_box(
                                                "run_modal_data_input",
                                                t,
                                                &modal.data_dir,
                                                "/path/to/game/data",
                                                app.focused_input
                                                    == Some(FocusInput::RunGameDataDir),
                                                true,
                                            )
                                            .flex_1()
                                            .min_w_0()
                                            .on_click(cx.listener(|this, _, window, cx| {
                                                this.set_focus_input(
                                                    Some(FocusInput::RunGameDataDir),
                                                    window,
                                                    cx,
                                                );
                                            })),
                                        )
                                        .child(
                                            style::ghost_btn(
                                                "run_modal_pick_dir",
                                                t,
                                                "Choose Folder…",
                                            )
                                            .on_click(cx.listener(|this, _, window, cx| {
                                                this.pick_folder_async(
                                                    "Select game data / assets folder",
                                                    window,
                                                    cx,
                                                    |this, d, _, _| {
                                                        if let Some(ref mut m) =
                                                            this.run_game_modal
                                                        {
                                                            m.data_dir = d.display().to_string();
                                                            m.use_data_dir = true;
                                                        }
                                                    },
                                                );
                                            })),
                                        ),
                                )
                                .when(!suggested.is_empty(), |el2| {
                                    let sug_clone = suggested.clone();
                                    el2.child(
                                        div().flex().child(
                                            style::ghost_btn(
                                                "run_modal_use_pe_dir",
                                                t,
                                                "Use Original PE Folder",
                                            )
                                            .on_click(cx.listener(move |this, _, _, cx| {
                                                if let Some(ref mut m) = this.run_game_modal {
                                                    m.data_dir = sug_clone.clone();
                                                    m.use_data_dir = true;
                                                }
                                                cx.notify();
                                            })),
                                        ),
                                    )
                                }),
                        )
                    })
                    .when(modal.binary_path.is_some() || !modal.has_wasm, |el| {
                        el.child(
                            div()
                                .flex()
                                .flex_col()
                                .gap(px(6.0))
                                .child(style::eyebrow(t, "EXTRA COMMAND-LINE ARGUMENTS"))
                                .child(
                                    style::text_input_box(
                                        "run_modal_args_input",
                                        t,
                                        &modal.arguments,
                                        "e.g. -g 3x3:6a1a... (CLI tools)",
                                        app.focused_input
                                            == Some(FocusInput::RunGameArguments),
                                        true,
                                    )
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.set_focus_input(
                                            Some(FocusInput::RunGameArguments),
                                            window,
                                            cx,
                                        );
                                    })),
                                )
                                .child(
                                    div()
                                        .font_family(t.ui_font)
                                        .text_size(px(t.ui_px(11.0)))
                                        .text_color(rgb(t.muted))
                                        .child("Quotes and backslash escapes are supported. Saved with this project's launch options."),
                                ),
                        )
                    })
                    .child(
                        style::toggle_checkbox(
                            "run_modal_remember",
                            t,
                            modal.save_as_default,
                            "Remember launch mode & data folder for this project",
                            None,
                        )
                        .on_click(cx.listener(|this, _, _, cx| {
                            if let Some(ref mut m) = this.run_game_modal {
                                m.save_as_default = !m.save_as_default;
                            }
                            cx.notify();
                        })),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_wrap()
                            .items_center()
                            .justify_between()
                            .gap(px(8.0))
                            .pt(px(4.0))
                            .child(
                                div().when(modal.has_wasm, |el| {
                                    el.child(
                                        style::ghost_btn(
                                            "run_modal_wasm",
                                            t,
                                            "🌐 Play in Browser (WASM)",
                                        )
                                        .on_click(cx.listener(move |this, _, _, cx| {
                                            this.launch_or_open_wasm(&proj_dir_wasm);
                                            this.close_run_game_modal(cx);
                                        })),
                                    )
                                }),
                            )
                            .child(
                                div()
                                    .flex()
                                    .items_center()
                                    .gap(px(8.0))
                                    .child(
                                        style::ghost_btn("run_modal_cancel", t, "Cancel").on_click(
                                            cx.listener(|this, _, _, cx| {
                                                this.close_run_game_modal(cx);
                                            }),
                                        ),
                                    )
                                    .child(style::ghost_btn("run_modal_save", t, "Save Only")
                                        .on_click(cx.listener(|this, _, _, cx| {
                                            this.confirm_run_game_modal(false, cx);
                                        })))
                                    .child(
                                        style::primary_btn(
                                            "run_modal_launch",
                                            t,
                                            "▶ Launch Game Now",
                                            modal.binary_path.is_some() || modal.has_wasm,
                                        )
                                        .when(
                                            modal.binary_path.is_some() || modal.has_wasm,
                                            |el| {
                                                el.on_click(cx.listener(|this, _, _, cx| {
                                                    this.confirm_run_game_modal(true, cx);
                                                }))
                                            },
                                        ),
                                    ),
                            ),
                    ),
            ),
    )
}

pub fn render_rename_project_modal(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Option<Stateful<Div>> {
    let modal = app.rename_modal.as_ref()?;
    Some(
        div()
            .id("rename_modal_backdrop")
            .absolute()
            .top_0()
            .left_0()
            .size_full()
            .bg(style::scrim_bg())
            .flex()
            .items_center()
            .justify_center()
            .p(px(20.0))
            .on_mouse_down(
                MouseButton::Left,
                cx.listener(|this, _, _, cx| {
                    this.close_rename_project_modal(cx);
                }),
            )
            .child(
                style::panel_box(t)
                    .id("rename_modal_card")
                    .w(px(440.0))
                    .max_w(px(440.0))
                    .bg(rgb(t.overlay))
                    .border_color(rgb(t.ink))
                    .shadow_lg()
                    .p(px(18.0))
                    .flex()
                    .flex_col()
                    .gap(px(12.0))
                    .on_mouse_down(
                        MouseButton::Left,
                        cx.listener(|_, _, _, cx| {
                            cx.stop_propagation();
                        }),
                    )
                    .child(style::eyebrow(t, "RENAME PROJECT"))
                    .child(
                        style::text_input_box(
                            "quick_rename_input",
                            t,
                            &modal.name_buf,
                            "Display title",
                            app.focused_input == Some(FocusInput::QuickRenameProject),
                            false,
                        )
                        .on_click(cx.listener(|this, _, window, cx| {
                            this.set_focus_input(Some(FocusInput::QuickRenameProject), window, cx);
                        })),
                    )
                    .child(
                        div()
                            .flex()
                            .justify_end()
                            .gap(px(8.0))
                            .child(
                                style::ghost_btn("quick_rename_cancel", t, "Cancel").on_click(
                                    cx.listener(|this, _, _, cx| {
                                        this.close_rename_project_modal(cx);
                                    }),
                                ),
                            )
                            .child(
                                style::primary_btn("quick_rename_save", t, "Save Name", true)
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.commit_quick_rename_project(window, cx);
                                    })),
                            ),
                    ),
            ),
    )
}

pub fn render_delete_project_modal(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Option<Stateful<Div>> {
    let req = app.confirm_delete.as_ref()?;
    let path_disp = super::sanitize_home_path(&req.dir.display().to_string());
    let keep_bg = if !req.delete_from_disk {
        t.hl_soft
    } else {
        t.code_bg
    };
    let keep_bd = if !req.delete_from_disk {
        if t.dark {
            t.hl
        } else {
            t.btn
        }
    } else {
        t.line
    };
    let del_bg = if req.delete_from_disk {
        t.miss_bg
    } else {
        t.code_bg
    };
    let del_bd = if req.delete_from_disk { t.miss } else { t.line };

    Some(
        div()
            .id("delete_modal_backdrop")
            .absolute()
            .top_0()
            .left_0()
            .size_full()
            .bg(style::scrim_bg())
            .flex()
            .items_center()
            .justify_center()
            .p(px(20.0))
            .on_mouse_down(
                MouseButton::Left,
                cx.listener(|this, _, _, cx| {
                    this.close_delete_project_modal(cx);
                }),
            )
            .child(
                style::panel_box(t)
                    .id("delete_modal_card")
                    .w(px(500.0))
                    .max_w(px(500.0))
                    .bg(rgb(t.overlay))
                    .border_color(rgb(t.ink))
                    .shadow_lg()
                    .p(px(18.0))
                    .flex()
                    .flex_col()
                    .gap(px(12.0))
                    .on_mouse_down(
                        MouseButton::Left,
                        cx.listener(|_, _, _, cx| {
                            cx.stop_propagation();
                        }),
                    )
                    .child(style::eyebrow(t, "REMOVE OR DELETE PROJECT"))
                    .child(
                        div()
                            .font_family(t.ui_font)
                            .font_weight(FontWeight::EXTRA_BOLD)
                            .text_size(px(t.ui_px(17.0)))
                            .text_color(rgb(t.ink))
                            .child(format!("Remove \"{}\"?", req.name)),
                    )
                    .child(
                        div()
                            .font_family(t.mono_font)
                            .text_size(px(t.ui_px(11.0)))
                            .text_color(rgb(t.muted))
                            .child(path_disp),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .gap(px(8.0))
                            .child(
                                div()
                                    .id("del_mode_keep")
                                    .p(px(10.0))
                                    .rounded(px(4.0))
                                    .bg(rgb(keep_bg))
                                    .border_1()
                                    .border_color(rgb(keep_bd))
                                    .cursor_pointer()
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        if let Some(ref mut d) = this.confirm_delete {
                                            d.delete_from_disk = false;
                                        }
                                        cx.notify();
                                    }))
                                    .child(
                                        div()
                                            .font_family(t.ui_font)
                                            .font_weight(FontWeight::BOLD)
                                            .text_size(px(t.ui_px(13.0)))
                                            .text_color(rgb(t.ink))
                                            .child("Remove from project list only (keeps files on disk)"),
                                    ),
                            )
                            .child(
                                div()
                                    .id("del_mode_disk")
                                    .p(px(10.0))
                                    .rounded(px(4.0))
                                    .bg(rgb(del_bg))
                                    .border_1()
                                    .border_color(rgb(del_bd))
                                    .cursor_pointer()
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        if let Some(ref mut d) = this.confirm_delete {
                                            d.delete_from_disk = true;
                                        }
                                        cx.notify();
                                    }))
                                    .child(
                                        div()
                                            .font_family(t.ui_font)
                                            .font_weight(FontWeight::BOLD)
                                            .text_size(px(t.ui_px(13.0)))
                                            .text_color(rgb(t.miss))
                                            .child("Delete project folder permanently from disk (cannot be undone)"),
                                    ),
                            ),
                    )
                    .child(
                        div()
                            .flex()
                            .justify_end()
                            .gap(px(8.0))
                            .child(
                                style::ghost_btn("del_modal_cancel", t, "Cancel").on_click(
                                    cx.listener(|this, _, _, cx| {
                                        this.close_delete_project_modal(cx);
                                    }),
                                ),
                            )
                            .child(
                                style::danger_btn(
                                    "del_modal_confirm",
                                    t,
                                    if req.delete_from_disk {
                                        "Delete Permanently"
                                    } else {
                                        "Remove from List"
                                    },
                                )
                                .on_click(cx.listener(|this, _, window, cx| {
                                    this.execute_delete_project(window, cx);
                                })),
                            ),
                    ),
            ),
    )
}

pub fn render_issue_modal(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Option<Stateful<Div>> {
    if !app.show_issue_modal {
        return None;
    }
    let d = &app.issue_draft;
    let exit_chip = d
        .exit_code
        .map(|c| format!("exit {c}"))
        .unwrap_or_else(|| "preflight".to_string());

    Some(
        div()
            .id("issue_modal_backdrop")
            .absolute()
            .top_0()
            .left_0()
            .size_full()
            .bg(style::scrim_bg())
            .flex()
            .items_center()
            .justify_center()
            .p(px(20.0))
            .on_mouse_down(
                MouseButton::Left,
                cx.listener(|this, _, _, cx| {
                    this.close_issue_modal(cx);
                }),
            )
            .child(
                style::panel_box(t)
                    .id("issue_modal_card")
                    .w(px(640.0))
                    .max_w(px(640.0))
                    .max_h(px(640.0))
                    .bg(rgb(t.overlay))
                    .border_color(rgb(t.ink))
                    .shadow_lg()
                    .p(px(20.0))
                    .flex()
                    .flex_col()
                    .gap(px(12.0))
                    .on_mouse_down(
                        MouseButton::Left,
                        cx.listener(|_, _, _, cx| {
                            cx.stop_propagation();
                        }),
                    )
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
                                    .child(style::eyebrow(t, "CREATE GAME SUPPORT ISSUE"))
                                    .child(
                                        div()
                                            .font_family(t.ui_font)
                                            .font_weight(FontWeight::EXTRA_BOLD)
                                            .text_size(px(t.ui_px(18.0)))
                                            .text_color(rgb(t.ink))
                                            .child(format!(
                                                "Report support issue for {}",
                                                d.game_name
                                            )),
                                    ),
                            )
                            .child(style::ghost_btn("issue_modal_close", t, "✕").on_click(
                                cx.listener(|this, _, _, cx| {
                                    this.close_issue_modal(cx);
                                }),
                            )),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_wrap()
                            .gap(px(6.0))
                            .child(style::chip_inverted(t, d.pe_summary.clone()))
                            .child(style::chip_outlined(
                                t,
                                d.target_summary.clone(),
                                Some(t.info),
                            ))
                            .child(style::chip_outlined(
                                t,
                                format!("stage: {}", d.failed_stage),
                                Some(t.miss),
                            ))
                            .child(style::chip_outlined(t, exit_chip, Some(t.work))),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .gap(px(4.0))
                            .child(style::eyebrow(t, "ISSUE TITLE"))
                            .child(
                                style::text_input_box(
                                    "issue_title_input",
                                    t,
                                    &d.title,
                                    "[Support Request] <game>: <failure>",
                                    app.focused_input == Some(FocusInput::IssueTitle),
                                    false,
                                )
                                .on_click(cx.listener(
                                    |this, _, window, cx| {
                                        this.set_focus_input(
                                            Some(FocusInput::IssueTitle),
                                            window,
                                            cx,
                                        );
                                    },
                                )),
                            ),
                    )
                    .child(
                        div()
                            .p(px(10.0))
                            .rounded(px(4.0))
                            .bg(rgb(t.miss_bg))
                            .border_1()
                            .border_color(rgb(t.miss))
                            .font_family(t.mono_font)
                            .text_size(px(t.ui_px(11.5)))
                            .text_color(rgb(t.miss))
                            .child(d.error_summary.clone()),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .gap(px(4.0))
                            .child(style::eyebrow(t, "ADDITIONAL NOTES (OPTIONAL)"))
                            .child(
                                style::text_input_box(
                                    "issue_notes_input",
                                    t,
                                    &d.user_notes,
                                    "What happened, game version/edition, assets present…",
                                    app.focused_input == Some(FocusInput::IssueNotes),
                                    false,
                                )
                                .on_click(cx.listener(
                                    |this, _, window, cx| {
                                        this.set_focus_input(
                                            Some(FocusInput::IssueNotes),
                                            window,
                                            cx,
                                        );
                                    },
                                )),
                            ),
                    )
                    .when(!d.log_tail.trim().is_empty(), |el| {
                        el.child(
                            div()
                                .id("issue_log_preview")
                                .w_full()
                                .max_h(px(140.0))
                                .overflow_y_scroll()
                                .p(px(10.0))
                                .rounded(px(4.0))
                                .bg(rgb(t.code_bg))
                                .border_1()
                                .border_color(rgb(t.line_soft))
                                .font_family(t.mono_font)
                                .text_size(px(t.code_px(10.5)))
                                .text_color(rgb(t.muted))
                                .child(d.log_tail.clone()),
                        )
                    })
                    .child(
                        div()
                            .flex()
                            .flex_wrap()
                            .justify_end()
                            .gap(px(8.0))
                            .pt(px(4.0))
                            .child(style::ghost_btn("issue_cancel_btn", t, "Cancel").on_click(
                                cx.listener(|this, _, _, cx| {
                                    this.close_issue_modal(cx);
                                }),
                            ))
                            .child(
                                style::ghost_btn("issue_copy_md_btn", t, "Copy Markdown").on_click(
                                    cx.listener(|this, _, _, cx| {
                                        this.copy_issue_markdown(cx);
                                    }),
                                ),
                            )
                            .child(
                                style::primary_btn(
                                    "issue_submit_btn",
                                    t,
                                    "↗ Open Pre-filled GitHub Issue",
                                    true,
                                )
                                .on_click(cx.listener(
                                    |this, _, _, cx| {
                                        this.submit_issue_to_github(cx);
                                    },
                                )),
                            ),
                    ),
            ),
    )
}
