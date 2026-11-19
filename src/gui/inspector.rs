use gpui::{
    ClipboardItem, Context, Div, FontWeight, Stateful, Styled, div, prelude::*, px, relative, rgb,
};

use super::style;
use super::{FocusInput, GameReCompApp, InspectorTab};
use crate::theme_tokens::ThemeTokens;

pub fn render_function_inspector(
    app: &GameReCompApp,
    t: &ThemeTokens,
    cx: &mut Context<GameReCompApp>,
) -> Option<Stateful<Div>> {
    if !app.inspector_open {
        return None;
    }
    let idx = app.inspector_func_idx?;
    let model = app.active_project.as_ref()?;
    let func = model.all_functions.get(idx)?;

    let is_star = model.ui_meta.starred.contains(&func.address);
    let addr_for_star = func.address.clone();
    let addr_for_agent = func.address.clone();
    let addr_for_note = func.address.clone();
    let sym_for_copy = func.symbol.clone();
    let src_for_copy = app.inspector_source.clone();
    let src_file_open = app.inspector_source_file.clone();

    let (badge_bg, badge_fg) = if func.category == "HIGH_LEVEL_VERIFIED" || func.is_certified {
        (t.match_fill, t.bg)
    } else if func.category == "UNRESOLVED" {
        (t.miss_bg, t.miss)
    } else {
        (t.bg_alt, t.ink_2)
    };

    let meta = format!(
        "{}  ·  {} bytes  ·  {} instructions  ·  {} blocks{}",
        func.address,
        func.size,
        func.instructions,
        func.blocks,
        if func.file.is_empty() {
            String::new()
        } else {
            format!("  ·  {}", func.file)
        }
    );

    let header = div()
        .flex()
        .items_start()
        .justify_between()
        .gap(px(14.0))
        .px(px(24.0))
        .py(px(18.0))
        .border_b_1()
        .border_color(rgb(t.line_soft))
        .child(
            div()
                .flex()
                .flex_col()
                .gap(px(4.0))
                .child(style::eyebrow(t, meta))
                .child(
                    div()
                        .font_family(t.mono_font)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(t.ui_px(20.0)))
                        .text_color(rgb(t.ink))
                        .child(func.symbol.clone()),
                ),
        )
        .child(
            div()
                .flex()
                .items_center()
                .gap(px(6.0))
                .child(style::status_badge(func.category.clone(), badge_bg, badge_fg))
                .child(
                    style::option_pill(
                        "insp_star_btn",
                        t,
                        if is_star { "★ Starred" } else { "☆ Star" },
                        is_star,
                    )
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.toggle_star_function(&addr_for_star);
                        cx.notify();
                    })),
                )
                .child(
                    style::ghost_btn(
                        "insp_expand_btn",
                        t,
                        if app.inspector_expanded { "⤡" } else { "⤢" },
                    )
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.inspector_expanded = !this.inspector_expanded;
                        cx.notify();
                    })),
                )
                .child(
                    style::ghost_btn("insp_prev_btn", t, "‹").on_click(cx.listener(
                        |this, _, _, cx| {
                            this.step_inspector(-1);
                            cx.notify();
                        },
                    )),
                )
                .child(
                    style::ghost_btn("insp_next_btn", t, "›").on_click(cx.listener(
                        |this, _, _, cx| {
                            this.step_inspector(1);
                            cx.notify();
                        },
                    )),
                )
                .child(
                    style::ghost_btn("insp_close_btn", t, "✕").on_click(cx.listener(
                        |this, _, _, cx| {
                            this.inspector_open = false;
                            cx.notify();
                        },
                    )),
                ),
        );

    let subtabs = div()
        .flex()
        .items_center()
        .justify_between()
        .px(px(24.0))
        .py(px(10.0))
        .border_b_1()
        .border_color(rgb(t.line_soft))
        .child(
            div()
                .flex()
                .gap(px(6.0))
                .child(
                    style::option_pill(
                        "insp_tab_src",
                        t,
                        "Source & Provenance",
                        app.inspector_tab == InspectorTab::Source,
                    )
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.inspector_tab = InspectorTab::Source;
                        cx.notify();
                    })),
                )
                .child(
                    style::option_pill(
                        "insp_tab_notes",
                        t,
                        "Notes & Hints",
                        app.inspector_tab == InspectorTab::Notes,
                    )
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.inspector_tab = InspectorTab::Notes;
                        cx.notify();
                    })),
                ),
        )
        .child(
            div()
                .flex()
                .gap(px(6.0))
                .child(style::ghost_btn("insp_copy_c", t, "Copy C").on_click(cx.listener(
                    move |this, _, _, cx| {
                        cx.write_to_clipboard(ClipboardItem::new_string(src_for_copy.clone()));
                        this.push_toast(
                            "Copied C",
                            &format!("Copied source for {sym_for_copy}"),
                            this.tokens().info,
                            false,
                        );
                        cx.notify();
                    },
                )))
                .when_some(src_file_open.filter(|p| p.is_file()), |el, path| {
                    el.child(style::ghost_btn("insp_open_file", t, "Open file").on_click(
                        cx.listener(move |_, _, _, _| {
                            let _ = open::that(&path);
                        }),
                    ))
                })
                .child(
                    style::ghost_btn("insp_run_agent", t, "Run agent on fn").on_click(cx.listener(
                        move |this, _, _, cx| {
                            this.agent_target_fn = addr_for_agent.clone();
                            this.start_readability_agent(cx);
                        },
                    )),
                ),
        );

    let body = match app.inspector_tab {
        InspectorTab::Source => {
            let mut prov = div()
                .p(px(12.0))
                .rounded(px(3.0))
                .bg(rgb(t.code_bg))
                .border_1()
                .border_color(rgb(t.line_soft))
                .flex()
                .flex_col()
                .gap(px(4.0))
                .child(
                    div()
                        .font_family(t.mono_font)
                        .text_size(px(t.ui_px(12.0)))
                        .text_color(rgb(t.ink))
                        .child(format!(
                            "category: {}   ·   source_unit: {}",
                            func.category,
                            if func.source_path.is_empty() {
                                "(none)"
                            } else {
                                &func.source_path
                            }
                        )),
                );

            if !func.verified_by.is_empty() {
                prov = prov.child(
                    div()
                        .font_family(t.mono_font)
                        .text_size(px(t.ui_px(11.5)))
                        .text_color(rgb(t.match_ink))
                        .child(format!("verified_by: {}", func.verified_by)),
                );
            }
            if !func.not_proven.is_empty() {
                prov = prov.child(
                    div()
                        .font_family(t.mono_font)
                        .text_size(px(t.ui_px(11.5)))
                        .text_color(rgb(t.work))
                        .child(format!("not_proven: {}", func.not_proven)),
                );
            }
            for ev in &func.evidence {
                prov = prov.child(
                    div()
                        .font_family(t.mono_font)
                        .text_size(px(t.ui_px(11.5)))
                        .text_color(rgb(t.muted))
                        .child(format!("• {ev}")),
                );
            }

            let mut code_box = div()
                .p(px(14.0))
                .rounded(px(4.0))
                .bg(rgb(t.code_bg))
                .border_1()
                .border_color(rgb(t.line_soft))
                .flex()
                .flex_col()
                .gap(px(1.5));

            for (ln_i, line) in app.inspector_source.lines().take(320).enumerate() {
                let col = style::c_line_color(line, t);
                code_box = code_box.child(
                    div()
                        .flex()
                        .gap(px(12.0))
                        .child(
                            div()
                                .w(px(34.0))
                                .min_w(px(34.0))
                                .font_family(t.mono_font)
                                .text_size(px(t.code_px(11.0)))
                                .text_color(rgb(t.muted))
                                .child(format!("{:>4}", ln_i + 1)),
                        )
                        .child(
                            div()
                                .font_family(t.mono_font)
                                .text_size(px(t.code_px(12.0)))
                                .text_color(col)
                                .child(line.to_string()),
                        ),
                );
            }

            div()
                .flex()
                .flex_col()
                .gap(px(14.0))
                .child(style::top_rule(t))
                .child(style::eyebrow(t, "PROVENANCE & VERIFICATION"))
                .child(prov)
                .child(style::top_rule(t))
                .child(style::eyebrow(t, "GENERATED / RECOVERED C SOURCE"))
                .child(code_box)
        }
        InspectorTab::Notes => {
            let notes = model
                .ui_meta
                .notes
                .get(&func.address)
                .cloned()
                .unwrap_or_default();

            let mut notes_list = div().flex().flex_col().gap(px(8.0));
            if notes.is_empty() {
                notes_list = notes_list.child(
                    div()
                        .font_family(t.ui_font)
                        .text_size(px(t.ui_px(13.0)))
                        .text_color(rgb(t.muted))
                        .child("No notes recorded for this function yet."),
                );
            } else {
                for n in &notes {
                    notes_list = notes_list.child(
                        div()
                            .p(px(10.0))
                            .rounded(px(3.0))
                            .bg(rgb(t.code_bg))
                            .border_1()
                            .border_color(rgb(t.line_soft))
                            .flex()
                            .flex_col()
                            .gap(px(3.0))
                            .child(
                                div()
                                    .font_family(t.mono_font)
                                    .text_size(px(t.ui_px(10.5)))
                                    .text_color(rgb(t.muted))
                                    .child(format!("{}  ·  {}", n.author, n.timestamp)),
                            )
                            .child(
                                div()
                                    .font_family(t.ui_font)
                                    .text_size(px(t.ui_px(13.0)))
                                    .text_color(rgb(t.ink))
                                    .child(n.text.clone()),
                            ),
                    );
                }
            }

            div()
                .flex()
                .flex_col()
                .gap(px(12.0))
                .child(style::top_rule(t))
                .child(style::eyebrow(t, "YOUR NOTES ON THIS FUNCTION"))
                .child(notes_list)
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(8.0))
                        .child(
                            style::text_input_box(
                                "insp_note_input",
                                t,
                                &app.inspector_note_input,
                                "Type a note for yourself or AI agents and press Enter…",
                                app.focused_input == Some(FocusInput::InspectorNote),
                                false,
                            )
                            .flex_1()
                            .min_w_0()
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.set_focus_input(Some(FocusInput::InspectorNote), window, cx);
                            })),
                        )
                        .child(
                            style::primary_btn("insp_add_note_btn", t, "Add note", true).on_click(
                                cx.listener(move |this, _, _, cx| {
                                    let txt = this.inspector_note_input.trim().to_string();
                                    if !txt.is_empty() {
                                        this.add_function_note(&addr_for_note, txt);
                                        this.inspector_note_input.clear();
                                        cx.notify();
                                    }
                                }),
                            ),
                        ),
                )
        }
    };

    let drawer = div()
        .id("inspector_drawer_panel")
        .w(if app.inspector_expanded {
            relative(1.0)
        } else {
            relative(0.62)
        })
        .min_w(px(540.0))
        .h_full()
        .bg(rgb(t.bg))
        .border_l_1()
        .border_color(rgb(t.line))
        .flex()
        .flex_col()
        .on_click(cx.listener(|_, _, _, cx| {
            cx.stop_propagation();
        }))
        .child(header)
        .child(subtabs)
        .child(
            div()
                .id("inspector_body_scroll")
                .flex_1()
                .overflow_y_scroll()
                .p(px(24.0))
                .child(body),
        );

    Some(
        div()
            .id("inspector_scrim_overlay")
            .absolute()
            .inset_0()
            .bg(style::scrim_bg())
            .flex()
            .justify_end()
            .on_click(cx.listener(|this, _, _, cx| {
                this.inspector_open = false;
                cx.notify();
            }))
            .child(drawer),
    )
}
