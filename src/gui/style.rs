use std::borrow::Cow;
use std::time::Duration;

use gpui::{
    Animation, AnimationExt as _, AnyElement, App, Div, FontWeight, Hsla, IntoElement, Pixels,
    SharedString, Stateful, Styled, bounce, div, ease_in_out, prelude::*, pulsating_between, px,
    relative, rgb, rgba,
};

use crate::theme_tokens::ThemeTokens;

pub const FONT_SANS: &str = "Archivo";
pub const FONT_MONO: &str = "IBM Plex Mono";

pub fn register_fonts(cx: &App) {
    let fonts: Vec<Cow<'static, [u8]>> = vec![
        Cow::Borrowed(include_bytes!("../../assets/fonts/Archivo-Regular.ttf")),
        Cow::Borrowed(include_bytes!("../../assets/fonts/Archivo-SemiBold.ttf")),
        Cow::Borrowed(include_bytes!("../../assets/fonts/Archivo-Display.ttf")),
        Cow::Borrowed(include_bytes!("../../assets/fonts/IBMPlexMono-Regular.ttf")),
        Cow::Borrowed(include_bytes!("../../assets/fonts/IBMPlexMono-SemiBold.ttf")),
    ];
    let _ = cx.text_system().add_fonts(fonts);
}

pub fn c(hex: u32) -> Hsla {
    rgb(hex).into()
}

pub fn ca(hex: u32, alpha: f32) -> Hsla {
    let base: Hsla = rgb(hex).into();
    base.opacity(alpha)
}

/// `[GR] Game[ReComp]` brand mark matching `decompdecomp`'s `[dd] decomp[decomp]`.
pub fn brand_mark(t: &ThemeTokens) -> Div {
    div()
        .flex()
        .items_center()
        .gap(px(9.0))
        .child(
            div()
                .flex()
                .items_center()
                .gap(px(2.0))
                .child(
                    div()
                        .w(px(14.0))
                        .h(px(20.0))
                        .flex()
                        .items_center()
                        .justify_center()
                        .border_2()
                        .border_color(rgb(t.ink))
                        .rounded(px(1.5))
                        .font_family(FONT_MONO)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(10.5))
                        .text_color(rgb(t.ink))
                        .child("G"),
                )
                .child(
                    div()
                        .w(px(15.0))
                        .h(px(20.0))
                        .flex()
                        .items_center()
                        .justify_center()
                        .bg(rgb(t.hl))
                        .rounded(px(1.5))
                        .font_family(FONT_MONO)
                        .font_weight(FontWeight::BOLD)
                        .text_size(px(10.5))
                        .text_color(rgb(t.hl_ink))
                        .child("R"),
                ),
        )
        .child(
            div()
                .flex()
                .items_center()
                .font_family(FONT_SANS)
                .font_weight(FontWeight::EXTRA_BOLD)
                .text_size(px(15.5))
                .child(div().text_color(rgb(t.ink)).child("Game"))
                .child(
                    div()
                        .ml(px(2.0))
                        .px(px(4.0))
                        .py(px(1.0))
                        .bg(rgb(t.hl))
                        .text_color(rgb(t.hl_ink))
                        .rounded(px(1.5))
                        .child("ReComp"),
                ),
        )
}

/// Bright yellow marker highlight title matching `DEMO QUEST 64` in decompdecomp.
pub fn yellow_project_title(t: &ThemeTokens, title: &str) -> Div {
    div()
        .px(px(10.0))
        .py(px(3.0))
        .bg(rgb(t.hl))
        .text_color(rgb(t.hl_ink))
        .rounded(px(2.0))
        .font_family(FONT_SANS)
        .font_weight(FontWeight::EXTRA_BOLD)
        .text_size(px(24.0))
        .line_height(px(28.0))
        .child(title.to_uppercase())
}

pub fn eyebrow(t: &ThemeTokens, text: impl Into<SharedString>) -> Div {
    div()
        .font_family(FONT_MONO)
        .font_weight(FontWeight::SEMIBOLD)
        .text_size(px(10.5))
        .text_color(rgb(t.muted))
        .child(text.into())
}

pub fn primary_btn(
    id: impl Into<gpui::ElementId>,
    t: &ThemeTokens,
    label: impl Into<SharedString>,
    enabled: bool,
) -> Stateful<Div> {
    let base = div()
        .id(id)
        .flex()
        .items_center()
        .justify_center()
        .gap(px(7.0))
        .h(px(34.0))
        .px(px(14.0))
        .rounded(px(3.0))
        .font_family(FONT_SANS)
        .font_weight(FontWeight::SEMIBOLD)
        .text_size(px(13.0))
        .child(label.into());

    if enabled {
        base.bg(rgb(t.btn))
            .text_color(rgb(t.btn_ink))
            .cursor_pointer()
            .hover({
                let hover = t.btn_hover;
                move |s| s.bg(rgb(hover))
            })
    } else {
        base.bg(ca(t.btn, 0.38))
            .text_color(ca(t.btn_ink, 0.65))
    }
}

pub fn ghost_btn(
    id: impl Into<gpui::ElementId>,
    t: &ThemeTokens,
    label: impl Into<SharedString>,
) -> Stateful<Div> {
    let bg_alt = t.bg_alt;
    let ink = t.ink;
    div()
        .id(id)
        .flex()
        .items_center()
        .justify_center()
        .gap(px(6.0))
        .h(px(32.0))
        .px(px(12.0))
        .rounded(px(3.0))
        .bg(rgb(t.panel))
        .border_1()
        .border_color(rgb(t.faint))
        .text_color(rgb(t.ink))
        .font_family(FONT_SANS)
        .font_weight(FontWeight::SEMIBOLD)
        .text_size(px(12.5))
        .cursor_pointer()
        .hover(move |s| s.bg(rgb(bg_alt)).border_color(rgb(ink)))
        .child(label.into())
}

pub fn danger_btn(
    id: impl Into<gpui::ElementId>,
    t: &ThemeTokens,
    label: impl Into<SharedString>,
) -> Stateful<Div> {
    let miss_bg = t.miss_bg;
    let miss = t.miss;
    div()
        .id(id)
        .flex()
        .items_center()
        .justify_center()
        .gap(px(6.0))
        .h(px(32.0))
        .px(px(12.0))
        .rounded(px(3.0))
        .bg(rgb(t.panel))
        .border_1()
        .border_color(ca(t.miss, 0.6))
        .text_color(rgb(t.miss))
        .font_family(FONT_SANS)
        .font_weight(FontWeight::SEMIBOLD)
        .text_size(px(12.5))
        .cursor_pointer()
        .hover(move |s| s.bg(rgb(miss_bg)).border_color(rgb(miss)))
        .child(label.into())
}

pub fn chip_inverted(t: &ThemeTokens, text: impl Into<SharedString>) -> Div {
    div()
        .px(px(7.0))
        .py(px(3.0))
        .rounded(px(2.0))
        .bg(rgb(t.ink))
        .text_color(rgb(t.bg))
        .font_family(FONT_MONO)
        .font_weight(FontWeight::BOLD)
        .text_size(px(10.5))
        .child(text.into())
}

pub fn chip_outlined(
    t: &ThemeTokens,
    text: impl Into<SharedString>,
    accent: Option<u32>,
) -> Div {
    let fg = accent.unwrap_or(t.muted);
    let border = accent.map(|a| ca(a, 0.6)).unwrap_or_else(|| c(t.line));
    div()
        .px(px(7.0))
        .py(px(3.0))
        .rounded(px(2.0))
        .border_1()
        .border_color(border)
        .text_color(rgb(fg))
        .font_family(FONT_MONO)
        .text_size(px(11.0))
        .child(text.into())
}

pub fn status_badge(text: impl Into<SharedString>, bg: u32, fg: u32) -> Div {
    div()
        .px(px(8.0))
        .py(px(3.0))
        .rounded(px(3.0))
        .bg(rgb(bg))
        .text_color(rgb(fg))
        .font_family(FONT_MONO)
        .font_weight(FontWeight::SEMIBOLD)
        .text_size(px(10.5))
        .child(text.into())
}

/// Clickable segmented option pill (used for OS/Arch/Mode/Profile/Filters).
pub fn option_pill(
    id: impl Into<gpui::ElementId>,
    t: &ThemeTokens,
    label: impl Into<SharedString>,
    selected: bool,
) -> Stateful<Div> {
    let hover_bg = t.bg_alt;
    let base = div()
        .id(id)
        .px(px(10.0))
        .py(px(5.0))
        .rounded(px(3.0))
        .font_family(FONT_MONO)
        .font_weight(if selected {
            FontWeight::BOLD
        } else {
            FontWeight::MEDIUM
        })
        .text_size(px(11.5))
        .cursor_pointer()
        .child(label.into());

    if selected {
        base.bg(rgb(t.hl))
            .text_color(rgb(t.hl_ink))
            .border_1()
            .border_color(rgb(t.hl))
    } else {
        base.bg(rgb(t.bg_alt))
            .text_color(rgb(t.ink_2))
            .border_1()
            .border_color(rgb(t.line))
            .hover(move |s| s.bg(rgb(hover_bg)).border_color(rgb(0x8A887D)))
    }
}

/// Crisp checkbox row matching decompdecomp's `.check`.
pub fn toggle_checkbox(
    id: impl Into<gpui::ElementId>,
    t: &ThemeTokens,
    checked: bool,
    label: impl Into<SharedString>,
    hint: Option<&str>,
) -> Stateful<Div> {
    let box_el = div()
        .w(px(16.0))
        .h(px(16.0))
        .flex()
        .items_center()
        .justify_center()
        .rounded(px(2.5))
        .border_1()
        .border_color(rgb(t.ink))
        .bg(if checked { rgb(t.hl) } else { rgb(t.panel) })
        .text_color(rgb(t.hl_ink))
        .font_family(FONT_MONO)
        .font_weight(FontWeight::BOLD)
        .text_size(px(11.0))
        .child(if checked { "✓" } else { "" });

    let mut text_col = div()
        .flex()
        .flex_col()
        .gap(px(1.0))
        .child(
            div()
                .font_family(FONT_SANS)
                .font_weight(FontWeight::MEDIUM)
                .text_size(px(12.5))
                .text_color(rgb(t.ink))
                .child(label.into()),
        );
    if let Some(h) = hint {
        text_col = text_col.child(
            div()
                .font_family(FONT_SANS)
                .text_size(px(11.0))
                .text_color(rgb(t.muted))
                .child(h.to_string()),
        );
    }

    div()
        .id(id)
        .flex()
        .items_start()
        .gap(px(8.0))
        .px(px(10.0))
        .py(px(7.0))
        .rounded(px(3.0))
        .bg(rgb(t.bg_alt))
        .border_1()
        .border_color(if checked {
            ca(t.hl, 0.55)
        } else {
            c(t.line_soft)
        })
        .cursor_pointer()
        .child(div().mt(px(1.0)).child(box_el))
        .child(text_col)
}

/// Hairline bordered panel matching `.panel`.
pub fn panel_box(t: &ThemeTokens) -> Div {
    div()
        .flex()
        .flex_col()
        .bg(rgb(t.panel))
        .border_1()
        .border_color(rgb(t.line))
        .rounded(px(4.0))
}

/// Horizontal green progress bar matching `.meter`.
pub fn progress_meter(t: &ThemeTokens, height: Pixels, frac: f32, fill_hex: u32) -> Div {
    let clamped = frac.clamp(0.0, 1.0);
    div()
        .w_full()
        .h(height)
        .rounded(px(2.0))
        .bg(rgb(t.open_cell))
        .overflow_hidden()
        .child(
            div()
                .h_full()
                .w(relative(clamped))
                .bg(rgb(fill_hex))
                .rounded(px(2.0)),
        )
}

/// Smooth GPUI animated pulsing dot (used on running stages, active sidebar projects, header pill).
pub fn animated_pulse_dot(id: impl Into<gpui::ElementId>, color_hex: u32, size_px: f32) -> AnyElement {
    let col = c(color_hex);
    div()
        .w(px(size_px))
        .h(px(size_px))
        .rounded_full()
        .bg(col)
        .with_animation(
            id,
            Animation::new(Duration::from_millis(900))
                .repeat()
                .with_easing(pulsating_between(0.35, 1.0)),
            move |el, delta| el.bg(col.opacity(delta)),
        )
        .into_any_element()
}

/// Smooth GPUI animated sliding workbar (exact decompdecomp `.workbar` animation!).
pub fn animated_workbar(id: impl Into<gpui::ElementId>, t: &ThemeTokens, height_px: f32) -> AnyElement {
    let bar_col = rgb(t.hl);
    let track_col = rgb(t.open_cell);
    div()
        .w_full()
        .h(px(height_px))
        .rounded(px(2.0))
        .bg(track_col)
        .overflow_hidden()
        .relative()
        .child(
            div()
                .h_full()
                .w(relative(0.32))
                .bg(bar_col)
                .rounded(px(2.0))
                .with_animation(
                    id,
                    Animation::new(Duration::from_millis(1150))
                        .repeat()
                        .with_easing(bounce(ease_in_out)),
                    |el, delta| el.ml(relative(delta * 0.68)),
                ),
        )
        .into_any_element()
}

/// Borderless KPI column with bold 3px top rule (exact `decompdecomp` `.kpi`).
pub fn ruled_kpi(t: &ThemeTokens, label: &str) -> Div {
    div()
        .flex()
        .flex_col()
        .flex_1()
        .min_w(px(160.0))
        .border_t_3()
        .border_color(rgb(t.ink))
        .pt(px(12.0))
        .gap(px(6.0))
        .child(eyebrow(t, label.to_uppercase()))
}

/// Styled focusable text input box (keyboard handled by root GPUI view).
pub fn text_input_box(
    id: impl Into<gpui::ElementId>,
    t: &ThemeTokens,
    value: &str,
    placeholder: &str,
    focused: bool,
    mono: bool,
) -> Stateful<Div> {
    let display = if value.is_empty() && !focused {
        placeholder.to_string()
    } else if focused {
        format!("{value}│")
    } else {
        value.to_string()
    };
    let fg = if value.is_empty() && !focused {
        rgb(t.muted)
    } else {
        rgb(t.ink)
    };
    div()
        .id(id)
        .flex()
        .items_center()
        .flex_1()
        .min_w(px(80.0))
        .h(px(32.0))
        .px(px(10.0))
        .rounded(px(3.0))
        .bg(rgb(t.code_bg))
        .border_1()
        .border_color(if focused { rgb(t.hl) } else { rgb(t.line) })
        .font_family(if mono { FONT_MONO } else { FONT_SANS })
        .text_size(px(12.5))
        .text_color(fg)
        .overflow_hidden()
        .cursor_text()
        .child(display)
}

#[allow(dead_code)]
pub fn hairline(t: &ThemeTokens) -> Div {
    div().w_full().h(px(1.0)).bg(rgb(t.line_soft))
}

pub fn top_rule(t: &ThemeTokens) -> Div {
    div().w_full().h(px(2.5)).bg(rgb(t.ink))
}

/// Helper to color a C source line by simple token class matching `decompdecomp`'s `highlightC`.
pub fn c_line_color(line: &str, t: &ThemeTokens) -> Hsla {
    let tr = line.trim_start();
    if tr.starts_with("//") || tr.starts_with("/*") || tr.starts_with('*') {
        c(t.muted)
    } else if tr.starts_with('#') {
        c(t.hl)
    } else if tr.starts_with("void ")
        || tr.starts_with("int ")
        || tr.starts_with("uint")
        || tr.starts_with("int32_t ")
        || tr.starts_with("static ")
        || tr.starts_with("return ")
    {
        c(t.match_ink)
    } else if tr.contains("hle_") || tr.contains("RECOMP_") || tr.contains("0x") {
        c(t.work)
    } else {
        c(t.ink)
    }
}

pub fn scrim_bg() -> Hsla {
    rgba(0x08080699).into()
}
