use gpui::{Context, Div, FontWeight, Styled, div, prelude::*, px, rgb};

use super::style::{self, FONT_MONO, FONT_SANS};
use super::{FocusInput, GameReCompApp, ProjectTab, Route};
use crate::theme_tokens::{ThemeMode, ThemeTokens};

pub fn render_sidebar(app: &GameReCompApp, t: &ThemeTokens, cx: &mut Context<GameReCompApp>) -> Div {
    let mut nav = div()
        .w(px(248.0))
        .min_w(px(248.0))
        .h_full()
        .flex()
        .flex_col()
        .bg(rgb(t.nav))
        .border_r_1()
        .border_color(rgb(t.line_soft))
        .px(px(12.0))
        .pt(px(14.0))
        .pb(px(10.0))
        .gap(px(6.0));

    // Top Row: Brand mark + Notification Bell with yellow unread count
    let bell_btn = div()
        .id("notif_bell_btn")
        .relative()
        .w(px(30.0))
        .h(px(28.0))
        .flex()
        .items_center()
        .justify_center()
        .rounded(px(3.0))
        .cursor_pointer()
        .bg(if app.show_notif_panel {
            rgb(t.bg_alt)
        } else {
            rgb(t.nav)
        })
        .hover({
            let bg_alt = t.bg_alt;
            move |s| s.bg(rgb(bg_alt))
        })
        .text_size(px(14.0))
        .text_color(if app.unread_notifs > 0 || app.show_notif_panel {
            rgb(t.ink)
        } else {
            rgb(t.muted)
        })
        .on_click(cx.listener(|this, _, _, cx| {
            this.show_notif_panel = !this.show_notif_panel;
            if this.show_notif_panel {
                this.unread_notifs = 0;
            }
            cx.notify();
        }))
        .child("🔔")
        .when(app.unread_notifs > 0, |el| {
            let count_txt = if app.unread_notifs > 99 {
                "99+".to_string()
            } else {
                app.unread_notifs.to_string()
            };
            el.child(
                div()
                    .absolute()
                    .top(px(-2.0))
                    .right(px(-3.0))
                    .px(px(4.0))
                    .min_w(px(16.0))
                    .h(px(15.0))
                    .flex()
                    .items_center()
                    .justify_center()
                    .rounded_full()
                    .bg(rgb(t.hl))
                    .text_color(rgb(t.hl_ink))
                    .font_family(FONT_MONO)
                    .font_weight(FontWeight::BOLD)
                    .text_size(px(9.5))
                    .child(count_txt),
            )
        });

    let top_row = div()
        .flex()
        .items_center()
        .justify_between()
        .px(px(4.0))
        .pb(px(6.0))
        .child(
            div()
                .id("brand_home_btn")
                .cursor_pointer()
                .on_click(cx.listener(|this, _, _, cx| {
                    this.route = Route::Home;
                    this.show_new_menu = false;
                    cx.notify();
                }))
                .child(style::brand_mark(t)),
        )
        .child(bell_btn);

    nav = nav.child(top_row);

    // Full-width bright yellow "+ New project" CTA button
    let new_btn = div()
        .id("nav_new_project_btn")
        .w_full()
        .h(px(38.0))
        .px(px(12.0))
        .flex()
        .items_center()
        .gap(px(8.0))
        .rounded(px(3.0))
        .bg(rgb(t.btn))
        .text_color(rgb(t.btn_ink))
        .font_family(FONT_SANS)
        .font_weight(FontWeight::BOLD)
        .text_size(px(13.5))
        .cursor_pointer()
        .hover({
            let h = t.btn_hover;
            move |s| s.bg(rgb(h))
        })
        .on_click(cx.listener(|this, _, _, cx| {
            this.show_new_menu = !this.show_new_menu;
            cx.notify();
        }))
        .child("+")
        .child("New project");

    nav = nav.child(new_btn);

    // Inline dropdown menu for "+ New project"
    if app.show_new_menu {
        let menu = div()
            .flex()
            .flex_col()
            .gap(px(2.0))
            .p(px(5.0))
            .rounded(px(4.0))
            .bg(rgb(t.panel))
            .border_1()
            .border_color(rgb(t.ink))
            .child(
                menu_item(
                    "new_menu_file",
                    t,
                    "Open a game or app (.exe)",
                    "32-bit Windows PE executable you own",
                )
                .on_click(cx.listener(|this, _, _, cx| {
                    this.show_new_menu = false;
                    this.route = Route::Home;
                    this.browse_for_pe(cx);
                })),
            )
            .child(
                menu_item(
                    "new_menu_restore",
                    t,
                    "Open a saved project",
                    "existing folder with project.json",
                )
                .on_click(cx.listener(|this, _, _, cx| {
                    this.show_new_menu = false;
                    this.browse_for_project_folder(cx);
                })),
            )
            .child(
                menu_item(
                    "new_menu_batch",
                    t,
                    "Batch recompile folder",
                    "scan a folder of .exe games or apps",
                )
                .on_click(cx.listener(|this, _, _, cx| {
                    this.show_new_menu = false;
                    this.browse_for_batch_folder(cx);
                })),
            )
            .child(
                menu_item(
                    "new_menu_home",
                    t,
                    "Configure new project",
                    "all CLI options, OS/Arch & dropzone",
                )
                .on_click(cx.listener(|this, _, _, cx| {
                    this.show_new_menu = false;
                    this.reset_dropzone();
                    this.route = Route::Home;
                    cx.notify();
                })),
            );
        nav = nav.child(menu);
    }

    // PROJECTS section header
    nav = nav.child(
        div()
            .flex()
            .items_center()
            .justify_between()
            .px(px(6.0))
            .pt(px(10.0))
            .child(style::eyebrow(t, "PROJECTS"))
            .child(
                div()
                    .font_family(FONT_MONO)
                    .text_size(px(10.5))
                    .text_color(rgb(t.muted))
                    .child(format!("{}", app.projects.len())),
            ),
    );

    // Optional project filter box
    if app.projects.len() >= 5 {
        let focused = app.focused_input == Some(FocusInput::SidebarFilter);
        nav = nav.child(
            style::text_input_box(
                "sidebar_filter_input",
                t,
                &app.sidebar_filter,
                "Filter projects…",
                focused,
                false,
            )
            .on_click(cx.listener(|this, _, window, cx| {
                this.set_focus_input(Some(FocusInput::SidebarFilter), window, cx);
            })),
        );
    }

    // Scrollable project list
    let flt = app.sidebar_filter.trim().to_ascii_lowercase();
    let mut proj_list = div()
        .id("sidebar_projects_scroll")
        .flex_1()
        .min_h(px(60.0))
        .overflow_y_scroll()
        .flex()
        .flex_col()
        .gap(px(3.0));

    let filtered: Vec<_> = app
        .projects
        .iter()
        .filter(|pr| {
            flt.is_empty()
                || pr.name.to_ascii_lowercase().contains(&flt)
                || pr.pe_name.to_ascii_lowercase().contains(&flt)
                || pr.target_label.to_ascii_lowercase().contains(&flt)
        })
        .cloned()
        .collect();

    if app.projects.is_empty() {
        proj_list = proj_list.child(
            div()
                .px(px(8.0))
                .py(px(4.0))
                .font_family(FONT_SANS)
                .text_size(px(12.5))
                .text_color(rgb(t.muted))
                .child("None yet: drop in a game."),
        );
    } else if filtered.is_empty() {
        proj_list = proj_list.child(
            div()
                .px(px(8.0))
                .py(px(4.0))
                .font_family(FONT_SANS)
                .text_size(px(12.5))
                .text_color(rgb(t.muted))
                .child("No project matches."),
        );
    } else {
        for (i, proj) in filtered.into_iter().enumerate() {
            let is_active = matches!(app.route, Route::Project(_))
                && app.active_project_dir.as_ref() == Some(&proj.dir);
            let is_running = app.running_kind.is_some()
                && app.active_project_dir.as_ref() == Some(&proj.dir);
            let sw_hex = t.project_swatches[proj.color_idx % t.project_swatches.len()];
            let pct = (proj.score.clamp(0, 100) as f32) / 100.0;
            let proj_dir = proj.dir.clone();

            let card = div()
                .id(("nav_proj_card", i))
                .flex()
                .flex_col()
                .gap(px(5.0))
                .px(px(9.0))
                .py(px(8.0))
                .rounded(px(3.0))
                .cursor_pointer()
                .bg(if is_active {
                    rgb(t.panel)
                } else {
                    rgb(t.nav)
                })
                .border_1()
                .border_color(if is_active {
                    rgb(t.faint)
                } else {
                    rgb(t.nav)
                })
                .hover({
                    let bg_alt = t.bg_alt;
                    move |s| s.bg(rgb(bg_alt))
                })
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.open_project_dir(proj_dir.clone(), ProjectTab::Overview);
                    cx.notify();
                }))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(7.0))
                        .child(div().w(px(8.0)).h(px(8.0)).rounded(px(2.0)).bg(rgb(sw_hex)))
                        .child(
                            div()
                                .flex_1()
                                .overflow_hidden()
                                .font_family(FONT_SANS)
                                .font_weight(FontWeight::SEMIBOLD)
                                .text_size(px(13.0))
                                .text_color(rgb(t.ink))
                                .child(truncate(&proj.name, 20)),
                        )
                        .when(is_running, |el| {
                            el.child(style::animated_pulse_dot(("nav_run_dot", i), t.hl, 8.0))
                        }),
                )
                .child(
                    div()
                        .flex()
                        .items_center()
                        .justify_between()
                        .font_family(FONT_MONO)
                        .text_size(px(10.5))
                        .text_color(if proj.load_ok {
                            rgb(t.muted)
                        } else {
                            rgb(t.miss)
                        })
                        .child(proj.target_label.clone())
                        .child(if proj.load_ok {
                            format!("{}%", proj.score.clamp(0, 100))
                        } else {
                            "unbuilt".to_string()
                        }),
                )
                .child(style::progress_meter(
                    t,
                    px(3.0),
                    if proj.load_ok { pct } else { 0.0 },
                    t.match_fill,
                ));

            proj_list = proj_list.child(card);
        }
    }

    nav = nav.child(proj_list);

    // Bottom utility nav (nav-foot)
    let mut foot = div()
        .flex()
        .flex_col()
        .gap(px(2.0))
        .pt(px(8.0))
        .border_t_1()
        .border_color(rgb(t.line_soft))
        .child(
            nav_link(
                "nav_agents",
                t,
                "Agents & models",
                "◈",
                matches!(app.route, Route::Agents),
            )
            .on_click(cx.listener(|this, _, _, cx| {
                this.route = Route::Agents;
                cx.notify();
            })),
        )
        .child(
            nav_link(
                "nav_tools",
                t,
                "Compilers & tools",
                "⚙",
                matches!(app.route, Route::Tools),
            )
            .on_click(cx.listener(|this, _, _, cx| {
                this.toolchain = crate::config::ToolchainStatus::detect();
                this.route = Route::Tools;
                cx.notify();
            })),
        )
        .child(
            nav_link(
                "nav_settings",
                t,
                "Settings",
                "☰",
                matches!(app.route, Route::Settings),
            )
            .on_click(cx.listener(|this, _, _, cx| {
                this.route = Route::Settings;
                cx.notify();
            })),
        )
        .child(
            nav_link(
                "nav_about",
                t,
                "About GameReComp",
                "ⓘ",
                matches!(app.route, Route::About),
            )
            .on_click(cx.listener(|this, _, _, cx| {
                this.route = Route::About;
                cx.notify();
            })),
        )
        .child(
            nav_link("nav_report", t, "Report a problem", "⚑", false).on_click(cx.listener(
                |this, _, _, cx| {
                    this.report_problem(cx);
                },
            )),
        );

    // Inline 6-theme switcher drawer above the footer when opened
    if app.show_theme_menu {
        let mut picker = div()
            .mt(px(4.0))
            .mb(px(4.0))
            .p(px(6.0))
            .bg(rgb(t.overlay))
            .border_1()
            .border_color(rgb(t.line))
            .rounded(px(4.0))
            .flex()
            .flex_col()
            .gap(px(3.0))
            .child(
                div()
                    .px(px(6.0))
                    .py(px(3.0))
                    .flex()
                    .items_center()
                    .justify_between()
                    .child(style::eyebrow(t, "THEMES (6)"))
                    .child(
                        div()
                            .id("theme_menu_close")
                            .px(px(4.0))
                            .font_family(FONT_MONO)
                            .text_size(px(11.0))
                            .text_color(rgb(t.muted))
                            .cursor_pointer()
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.show_theme_menu = false;
                                cx.notify();
                            }))
                            .child("✕"),
                    ),
            );

        for (idx, &mode) in ThemeMode::ALL.iter().enumerate() {
            let preview = ThemeTokens::for_mode(mode);
            let active = app.theme == mode;
            let bg_alt = t.bg_alt;
            let row = div()
                .id(("sidebar_theme_opt", idx))
                .px(px(7.0))
                .py(px(6.0))
                .rounded(px(3.0))
                .flex()
                .items_center()
                .justify_between()
                .gap(px(6.0))
                .cursor_pointer()
                .when(active, |el| {
                    el.bg(rgb(t.hl))
                        .text_color(rgb(t.hl_ink))
                        .font_weight(FontWeight::BOLD)
                })
                .when(!active, |el| {
                    el.text_color(rgb(t.ink_2))
                        .hover(move |s| s.bg(rgb(bg_alt)))
                })
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.set_theme(mode, cx);
                }))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(7.0))
                        .child(
                            div()
                                .flex()
                                .items_center()
                                .gap(px(2.0))
                                .child(
                                    div()
                                        .w(px(9.0))
                                        .h(px(9.0))
                                        .rounded(px(2.0))
                                        .bg(rgb(preview.bg))
                                        .border_1()
                                        .border_color(rgb(preview.line)),
                                )
                                .child(
                                    div()
                                        .w(px(9.0))
                                        .h(px(9.0))
                                        .rounded(px(2.0))
                                        .bg(rgb(preview.hl)),
                                ),
                        )
                        .child(
                            div()
                                .font_family(FONT_SANS)
                                .text_size(px(12.0))
                                .child(mode.label()),
                        ),
                )
                .child(
                    div()
                        .font_family(FONT_MONO)
                        .text_size(px(9.5))
                        .text_color(if active {
                            rgb(t.hl_ink)
                        } else {
                            rgb(t.muted)
                        })
                        .child(if active {
                            "✓"
                        } else if mode.is_dark() {
                            "DARK"
                        } else {
                            "LIGHT"
                        }),
                );
            picker = picker.child(row);
        }
        foot = foot.child(picker);
    }

    // Dedicated Dark / Light quick toggle button + 6-Theme Picker button
    let dark_light_label = if app.theme.is_dark() {
        "☀ Light"
    } else {
        "☾ Dark"
    };
    let toggle_bg = t.panel;
    let toggle_hover = t.bg_alt;
    let toggle_ink = t.ink;

    foot = foot
        .child(
            div()
                .flex()
                .items_center()
                .gap(px(5.0))
                .px(px(4.0))
                .pt(px(6.0))
                .child(
                    div()
                        .id("nav_dark_light_switch")
                        .flex_1()
                        .h(px(28.0))
                        .px(px(8.0))
                        .flex()
                        .items_center()
                        .justify_center()
                        .gap(px(5.0))
                        .rounded(px(3.0))
                        .bg(rgb(toggle_bg))
                        .border_1()
                        .border_color(rgb(t.line))
                        .font_family(FONT_MONO)
                        .font_weight(FontWeight::SEMIBOLD)
                        .text_size(px(11.0))
                        .text_color(rgb(t.ink))
                        .cursor_pointer()
                        .hover(move |s| s.bg(rgb(toggle_hover)).border_color(rgb(toggle_ink)))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.toggle_theme(cx);
                        }))
                        .child(dark_light_label),
                )
                .child(
                    div()
                        .id("nav_themes_picker_btn")
                        .h(px(28.0))
                        .px(px(8.0))
                        .flex()
                        .items_center()
                        .justify_center()
                        .gap(px(4.0))
                        .rounded(px(3.0))
                        .bg(if app.show_theme_menu {
                            rgb(t.hl)
                        } else {
                            rgb(toggle_bg)
                        })
                        .border_1()
                        .border_color(if app.show_theme_menu {
                            if t.dark {
                                rgb(t.hl)
                            } else {
                                rgb(t.ink)
                            }
                        } else {
                            rgb(t.line)
                        })
                        .font_family(FONT_MONO)
                        .font_weight(FontWeight::SEMIBOLD)
                        .text_size(px(11.0))
                        .text_color(if app.show_theme_menu {
                            rgb(t.hl_ink)
                        } else {
                            rgb(t.ink_2)
                        })
                        .cursor_pointer()
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.show_theme_menu = !this.show_theme_menu;
                            this.show_new_menu = false;
                            cx.notify();
                        }))
                        .child("◈ Themes"),
                ),
        )
        .child(
            // Bottom meta row: version + active theme + refresh + quit
            div()
                .flex()
                .items_center()
                .justify_between()
                .px(px(6.0))
                .pt(px(5.0))
                .child(
                    div()
                        .font_family(FONT_MONO)
                        .text_size(px(10.5))
                        .text_color(rgb(t.muted))
                        .child(format!("v{} · {}", crate::cli::VERSION, app.theme.label())),
                )
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(4.0))
                        .child(
                            small_icon_btn("nav_refresh_btn", t, "↻").on_click(cx.listener(
                                |this, _, _, cx| {
                                    this.refresh_projects();
                                    cx.notify();
                                },
                            )),
                        )
                        .child(
                            small_icon_btn("nav_quit_btn", t, "⏻").on_click(cx.listener(
                                |_, _, _, cx| {
                                    cx.quit();
                                },
                            )),
                        ),
                ),
        );

    nav.child(foot)
}

fn menu_item(
    id: &'static str,
    t: &ThemeTokens,
    title: &'static str,
    sub: &'static str,
) -> gpui::Stateful<Div> {
    let hover_bg = t.bg_alt;
    div()
        .id(id)
        .flex()
        .flex_col()
        .gap(px(1.0))
        .px(px(9.0))
        .py(px(7.0))
        .rounded(px(3.0))
        .cursor_pointer()
        .hover(move |s| s.bg(rgb(hover_bg)))
        .child(
            div()
                .font_family(FONT_SANS)
                .font_weight(FontWeight::BOLD)
                .text_size(px(13.0))
                .text_color(rgb(t.ink))
                .child(title),
        )
        .child(
            div()
                .font_family(FONT_SANS)
                .text_size(px(11.5))
                .text_color(rgb(t.muted))
                .child(sub),
        )
}

fn nav_link(
    id: &'static str,
    t: &ThemeTokens,
    label: &'static str,
    glyph: &'static str,
    active: bool,
) -> gpui::Stateful<Div> {
    let base = div()
        .id(id)
        .w_full()
        .h(px(32.0))
        .px(px(9.0))
        .flex()
        .items_center()
        .gap(px(9.0))
        .rounded(px(3.0))
        .font_family(FONT_SANS)
        .font_size_13()
        .cursor_pointer();

    if active {
        base.bg(rgb(t.hl))
            .text_color(rgb(t.hl_ink))
            .font_weight(FontWeight::SEMIBOLD)
            .child(div().text_size(px(13.0)).child(glyph))
            .child(label)
    } else {
        let bg_alt = t.bg_alt;
        let ink = t.ink;
        base.text_color(rgb(t.ink_2))
            .font_weight(FontWeight::MEDIUM)
            .hover(move |s| s.bg(rgb(bg_alt)).text_color(rgb(ink)))
            .child(div().text_size(px(13.0)).text_color(rgb(t.muted)).child(glyph))
            .child(label)
    }
}

trait FontSizeHelper: Styled + Sized {
    fn font_size_13(self) -> Self {
        self.text_size(px(13.0))
    }
}

impl FontSizeHelper for gpui::Stateful<Div> {}

fn small_icon_btn(id: &'static str, t: &ThemeTokens, glyph: &'static str) -> gpui::Stateful<Div> {
    let bg_alt = t.bg_alt;
    let ink = t.ink;
    div()
        .id(id)
        .w(px(26.0))
        .h(px(26.0))
        .flex()
        .items_center()
        .justify_center()
        .rounded(px(3.0))
        .text_size(px(14.0))
        .text_color(rgb(t.muted))
        .cursor_pointer()
        .hover(move |s| s.bg(rgb(bg_alt)).text_color(rgb(ink)))
        .child(glyph)
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
