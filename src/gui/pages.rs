use gpui::{Context, Div, FontWeight, Stateful, Styled, div, prelude::*, px, rgb};

use super::style;
use super::{FocusInput, GameReCompApp};
use crate::theme_tokens::{
    CODE_FONT_SCALE_PRESETS, MonoFont, ThemeMode, ThemeTokens, UI_FONT_SCALE_PRESETS, UiFont,
};

// ============================================================================
// Page: Agents & Models
// ============================================================================
pub fn render_agents_page(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Stateful<Div> {
    let mut grid = div().flex().flex_row().flex_wrap().gap(px(22.0)).p(px(18.0));
    for (idx, ag) in app.toolchain.agents.iter().enumerate() {
        let preset = ag.preset_cmd.clone();
        let active = app.agent_cmd_buf.trim() == ag.preset_cmd.trim();

        grid = grid.child(
            div()
                .w(px(290.0))
                .flex_grow()
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

    page_shell(
        "agents_page_scroll",
        t,
        "AGENTS & MODELS",
        "Your AI agents",
        "Helper agents are optional and drive GameReComp's provenance-gated readability harness via REVENG_AI_CMD.",
    )
    .child(
        style::panel_box(t)
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
                            .font_family(t.ui_font)
                            .font_weight(FontWeight::BOLD)
                            .text_size(px(t.ui_px(15.5)))
                            .text_color(rgb(t.ink))
                            .child("Agent apps on this computer"),
                    )
                    .child(
                        style::ghost_btn("ag_refresh_btn", t, "↻ Refresh").on_click(cx.listener(
                            |this, _, _, cx| {
                                this.toolchain = crate::config::ToolchainStatus::detect();
                                cx.notify();
                            },
                        )),
                    ),
            )
            .child(grid),
    )
    .child(
        style::panel_box(t)
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
                            .child("GameReComp sends recovery prompts on stdin and validates proposed C against the oracle before promoting provenance."),
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
    cx: &mut Context<GameReCompApp>,
) -> Stateful<Div> {
    let mut grid = div().flex().flex_row().flex_wrap().gap(px(14.0));
    for card in &app.toolchain.cards {
        grid = grid.child(
            style::panel_box(t)
                .w(px(320.0))
                .flex_grow()
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
        "COMPILERS & TOOLS",
        "What builds your games",
        "GameReComp uses Python for PE32 disassembly/lifting, C/CMake + SDL2 for native builds, and Clang/Node for WebAssembly.",
    )
    .child(
        div().child(
            style::primary_btn("tools_recheck_btn", t, "↻ Re-check toolchain", true).on_click(
                cx.listener(|this, _, _, cx| {
                    this.toolchain = crate::config::ToolchainStatus::detect();
                    let line = this.toolchain.format_selftest_line();
                    this.push_toast("Toolchain checked", &line, this.tokens().match_fill, false);
                    cx.notify();
                }),
            ),
        ),
    )
    .child(grid)
}

// ============================================================================
// Page: Global Settings
// ============================================================================
pub fn render_settings_page(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Stateful<Div> {
    let left = style::panel_box(t)
        .flex_1()
        .min_w(px(380.0))
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
                                        "default: <repo>/projects",
                                        app.focused_input == Some(FocusInput::SettingsProjectsRoot),
                                        true,
                                    )
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
                                        cx.listener(|this, _, _, cx| {
                                            if let Some(d) = rfd::FileDialog::new().pick_folder() {
                                                this.settings.projects_root =
                                                    d.display().to_string();
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
                                        cx.listener(|this, _, _, cx| {
                                            if let Some(d) = rfd::FileDialog::new().pick_folder() {
                                                this.settings.rom_library_dir =
                                                    d.display().to_string();
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
                        .flex_col()
                        .gap(px(6.0))
                        .child(style::eyebrow(t, "MAX PARALLEL GAMES (CONCURRENT DECOMPILATION)"))
                        .child({
                            let mut row = div().flex().flex_wrap().gap(px(6.0));
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
                                .child("How many games can decompile & compile simultaneously before extra picks wait in queue."),
                        ),
                )
                .child(
                    div().pt(px(4.0)).child(
                        style::primary_btn("set_save_btn", t, "Save settings", true).on_click(
                            cx.listener(|this, _, _, cx| {
                                match this.settings.save() {
                                    Ok(_) => {
                                        this.refresh_projects();
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

    let right = style::panel_box(t)
        .flex_1()
        .min_w(px(340.0))
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
                .child(info_kv(
                    t,
                    "Repository Root",
                    &app.cfg.repo_root.display().to_string(),
                ))
                .child(info_kv(
                    t,
                    "Settings File",
                    &crate::config::UserSettings::settings_path()
                        .map(|p| p.display().to_string())
                        .unwrap_or_else(|| "-".to_string()),
                ))
                .child(info_kv(
                    t,
                    "Toolchain",
                    &app.toolchain.format_selftest_line(),
                )),
        );

    page_shell(
        "settings_page_scroll",
        t,
        "SETTINGS",
        "How GameReComp works for you",
        "Customize theme, project directories, and your local game library.",
    )
    .child(
        div()
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
                .child(info_kv(t, "Esc", "Close Function Inspector, popovers, or blur text input"))
                .child(info_kv(t, "J / K", "Next / previous function inside Function Inspector"))
                .child(info_kv(t, "/", "Focus function search bar in Functions tab"))
                .child(info_kv(t, "Cmd/Ctrl + V", "Paste into focused text field"))
                .child(info_kv(t, "Cmd/Ctrl + +/-/0", "Increase, decrease, or reset UI font size"))
                .child(info_kv(t, "Drag & Drop", "Drop any .exe or project folder anywhere on Home")),
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
                .child(info_kv(t, "GUI Framework", "GPUI 0.2.2 (Zed GPU-accelerated Rust UI)"))
                .child(info_kv(t, "Binaries", "game-recomp-cli (CLI) & game-recomp-gui (GPUI)"))
                .child(info_kv(t, "WASM Core", "game_recomp verified on wasm32-unknown-unknown"))
                .child(info_kv(t, "Typography", "Archivo (OFL-1.1) & IBM Plex Mono (OFL-1.1)"))
                .child(info_kv(t, "Host", &format!("{} / {}", std::env::consts::OS, std::env::consts::ARCH)))
                .child(info_kv(t, "Projects Loaded", &format!("{} project(s)", app.projects.len())))
                .child(
                    div().pt(px(8.0)).flex().gap(px(8.0)).child(
                        style::ghost_btn("about_open_repo", t, "Open Repo Folder").on_click(
                            cx.listener(|this, _, _, _| {
                                let _ = open::that(&this.cfg.repo_root);
                            }),
                        ),
                    ),
                ),
        );

    page_shell(
        "about_page_scroll",
        t,
        "ABOUT",
        "GameReComp",
        "Pick your game and turn it to native port — static PE32 x86 recompilation to native executables and WebAssembly, built in Rust with GPUI.",
    )
    .child(
        div()
            .flex()
            .items_center()
            .gap(px(14.0))
            .child(style::brand_mark(t))
            .child(style::chip_inverted(t, format!("v{}", crate::cli::VERSION)))
            .child(style::chip_outlined(
                t,
                "GPUI 0.2.2",
                Some(if t.dark { t.hl } else { t.info }),
            ))
            .child(style::chip_outlined(t, "WASM32 READY", Some(t.match_ink))),
    )
    .child(arch_cards)
    .child(
        div()
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
        .child(style::chip_outlined(t, format!("{code_pct}%"), Some(t.info)))
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
                .child("Live Preview · Pick your game and turn it to native port."),
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
                .child(style::eyebrow(t, "UI FONT SIZE (SHORTCUT: CMD/CTRL + / - / 0)"))
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
    eyebrow_txt: &'static str,
    title: &'static str,
    subtitle: &'static str,
) -> Stateful<Div> {
    div()
        .id(scroll_id)
        .flex_1()
        .h_full()
        .overflow_y_scroll()
        .px(px(36.0))
        .pt(px(28.0))
        .pb(px(56.0))
        .flex()
        .flex_col()
        .gap(px(20.0))
        .child(
            div()
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
                        .line_height(px(38.0))
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
                .font_family(t.mono_font)
                .text_size(px(t.ui_px(12.0)))
                .text_color(rgb(t.ink))
                .child(v.to_string()),
        )
}
