use gpui::{Context, Div, FontWeight, Stateful, Styled, div, prelude::*, px, rgb};

use super::style::{self, FONT_MONO, FONT_SANS};
use super::{FocusInput, GameReCompApp};
use crate::theme_tokens::{ThemeMode, ThemeTokens};

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
                                .font_family(FONT_SANS)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(15.0))
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
                        .font_family(FONT_SANS)
                        .text_size(px(12.5))
                        .text_color(rgb(t.muted))
                        .child(ag.description.clone()),
                )
                .child(
                    div()
                        .font_family(FONT_MONO)
                        .text_size(px(11.0))
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
                            .font_family(FONT_SANS)
                            .font_weight(FontWeight::BOLD)
                            .text_size(px(15.5))
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
                    .font_family(FONT_SANS)
                    .font_weight(FontWeight::BOLD)
                    .text_size(px(15.5))
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
                            .font_family(FONT_SANS)
                            .text_size(px(13.0))
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
                                .font_family(FONT_SANS)
                                .font_weight(FontWeight::BOLD)
                                .text_size(px(15.0))
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
                        .font_family(FONT_SANS)
                        .text_size(px(12.5))
                        .text_color(rgb(t.ink_2))
                        .child(card.description.clone()),
                )
                .when(card.installed && !card.version.is_empty(), |el| {
                    el.child(
                        div()
                            .font_family(FONT_MONO)
                            .text_size(px(11.0))
                            .text_color(rgb(t.match_ink))
                            .child(card.version.clone()),
                    )
                })
                .child(
                    div()
                        .font_family(FONT_MONO)
                        .text_size(px(11.0))
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
                .font_family(FONT_SANS)
                .font_weight(FontWeight::BOLD)
                .text_size(px(15.5))
                .text_color(rgb(t.ink))
                .child("Appearance & Workspace"),
        )
        .child(
            div()
                .p(px(18.0))
                .flex()
                .flex_col()
                .gap(px(14.0))
                .child(
                    div()
                        .flex()
                        .flex_col()
                        .gap(px(6.0))
                        .child(style::eyebrow(t, "THEME"))
                        .child(
                            div()
                                .flex()
                                .gap(px(8.0))
                                .child(
                                    style::option_pill(
                                        "set_theme_dark",
                                        t,
                                        "Dark (Marked Listing)",
                                        app.theme == ThemeMode::Dark,
                                    )
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        this.set_theme(ThemeMode::Dark, cx);
                                    })),
                                )
                                .child(
                                    style::option_pill(
                                        "set_theme_light",
                                        t,
                                        "Light (Paper)",
                                        app.theme == ThemeMode::Light,
                                    )
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        this.set_theme(ThemeMode::Light, cx);
                                    })),
                                ),
                        ),
                )
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
                .font_family(FONT_SANS)
                .font_weight(FontWeight::BOLD)
                .text_size(px(15.5))
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
                        .font_family(FONT_SANS)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(18.0))
                        .text_color(rgb(t.ink))
                        .child("Native Desktop GUI & CLI"),
                )
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(13.0))
                        .text_color(rgb(t.ink_2))
                        .child("GPU-accelerated GPUI desktop interface + clap v4 CLI orchestrator, batch runner, WASM HTTP server, and PE32 inspector."),
                ),
        )
        .child(
            style::ruled_kpi(t, "TIER 2 · PYTHON PCRECOMP")
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(18.0))
                        .text_color(rgb(t.ink))
                        .child("Disassembler & Lifter"),
                )
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(13.0))
                        .text_color(rgb(t.ink_2))
                        .child("Deterministic x86 PE32 disassembly, CFG recovery, static C lifting, provenance verification, and oracle testing."),
                ),
        )
        .child(
            style::ruled_kpi(t, "TIER 3 · C + WIN32 HLE + WASM32")
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(18.0))
                        .text_color(rgb(t.ink))
                        .child("Standalone Native & Web"),
                )
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(13.0))
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
                .font_family(FONT_SANS)
                .font_weight(FontWeight::BOLD)
                .text_size(px(15.0))
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
                .font_family(FONT_SANS)
                .font_weight(FontWeight::BOLD)
                .text_size(px(15.0))
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
        "Static PE32 x86 recompilation to portable C, native executables, and WebAssembly — built in Rust with GPUI.",
    )
    .child(
        div()
            .flex()
            .items_center()
            .gap(px(14.0))
            .child(style::brand_mark(t))
            .child(style::chip_inverted(t, format!("v{}", crate::cli::VERSION)))
            .child(style::chip_outlined(t, "GPUI 0.2.2", Some(t.hl)))
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
                        .font_family(FONT_SANS)
                        .font_weight(FontWeight::EXTRA_BOLD)
                        .text_size(px(34.0))
                        .line_height(px(38.0))
                        .text_color(rgb(t.ink))
                        .child(title),
                )
                .child(
                    div()
                        .font_family(FONT_SANS)
                        .text_size(px(14.5))
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
                .font_family(FONT_MONO)
                .font_weight(FontWeight::BOLD)
                .text_size(px(11.5))
                .text_color(rgb(t.muted))
                .child(k),
        )
        .child(
            div()
                .flex_1()
                .font_family(FONT_MONO)
                .text_size(px(12.0))
                .text_color(rgb(t.ink))
                .child(v.to_string()),
        )
}
