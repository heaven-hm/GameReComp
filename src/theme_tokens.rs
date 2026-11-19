/// Platform-agnostic "Marked Listing" design tokens derived from decompdecomp's tokens.css,
/// extended with 6 high-contrast themes (Dark & Light) shared by the native GPUI desktop GUI
/// (`game-recomp-gui`) and future WebAssembly (`wasm32-unknown-unknown`) frontends.

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ThemeMode {
    Dark,
    Light,
    Nord,
    Gruvbox,
    Catppuccin,
    SolarizedLight,
}

impl ThemeMode {
    pub const ALL: &'static [ThemeMode] = &[
        ThemeMode::Dark,
        ThemeMode::Light,
        ThemeMode::Nord,
        ThemeMode::Gruvbox,
        ThemeMode::Catppuccin,
        ThemeMode::SolarizedLight,
    ];

    pub fn from_str(s: &str) -> Self {
        match s.trim().to_ascii_lowercase().as_str() {
            "light" | "paper" => Self::Light,
            "nord" => Self::Nord,
            "gruvbox" => Self::Gruvbox,
            "catppuccin" | "mocha" => Self::Catppuccin,
            "solarized-light" | "solarized_light" | "solarized" => Self::SolarizedLight,
            _ => Self::Dark,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Dark => "dark",
            Self::Light => "light",
            Self::Nord => "nord",
            Self::Gruvbox => "gruvbox",
            Self::Catppuccin => "catppuccin",
            Self::SolarizedLight => "solarized-light",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Dark => "Marked Dark",
            Self::Light => "Marked Light",
            Self::Nord => "Nord Frost",
            Self::Gruvbox => "Gruvbox Warm",
            Self::Catppuccin => "Catppuccin Mocha",
            Self::SolarizedLight => "Solarized Paper",
        }
    }

    pub fn subtitle(self) -> &'static str {
        match self {
            Self::Dark => "Editorial charcoal & yellow marker",
            Self::Light => "High-contrast paper & deep ink",
            Self::Nord => "Arctic slate & frost cyan",
            Self::Gruvbox => "Warm retro terminal & gold",
            Self::Catppuccin => "Midnight indigo & lavender",
            Self::SolarizedLight => "Warm parchment & deep teal ink",
        }
    }

    pub const fn is_dark(self) -> bool {
        !matches!(self, Self::Light | Self::SolarizedLight)
    }

    /// Quick Dark <-> Light switch:
    /// - Any dark theme toggles to high-contrast `Light`.
    /// - Any light theme toggles to `Dark` (or remembers the caller's last dark theme).
    pub fn toggle_dark_light(self, last_dark: ThemeMode, last_light: ThemeMode) -> Self {
        if self.is_dark() {
            if last_light.is_dark() {
                Self::Light
            } else {
                last_light
            }
        } else if last_dark.is_dark() {
            last_dark
        } else {
            Self::Dark
        }
    }

    pub fn toggle(self) -> Self {
        self.toggle_dark_light(Self::Dark, Self::Light)
    }
}

/// RGB hex colors (`0xRRGGBB`) for zero-dependency cross-platform (GPUI + WASM) theming.
#[derive(Debug, Clone, Copy)]
pub struct ThemeTokens {
    pub dark: bool,
    pub bg: u32,
    pub nav: u32,
    pub panel: u32,
    pub bg_alt: u32,
    pub overlay: u32,
    pub code_bg: u32,
    pub line: u32,
    pub line_soft: u32,
    pub faint: u32,
    pub ink: u32,
    pub ink_2: u32,
    pub muted: u32,
    pub hl: u32,
    pub hl_hover: u32,
    pub hl_ink: u32,
    pub hl_soft: u32,
    pub btn: u32,
    pub btn_hover: u32,
    pub btn_ink: u32,
    pub match_fill: u32,
    pub match_ink: u32,
    pub match_bg: u32,
    pub match_hi: u32,
    pub polished: u32,
    pub work: u32,
    pub work_bg: u32,
    pub miss: u32,
    pub miss_bg: u32,
    pub not_c: u32,
    pub info: u32,
    pub info_bg: u32,
    pub open_cell: u32,
    pub tried_cell: u32,
    pub project_swatches: [u32; 6],
    pub module_colors: [u32; 8],
}

impl ThemeTokens {
    pub const fn for_mode(mode: ThemeMode) -> Self {
        match mode {
            ThemeMode::Dark => Self::dark(),
            ThemeMode::Light => Self::light(),
            ThemeMode::Nord => Self::nord(),
            ThemeMode::Gruvbox => Self::gruvbox(),
            ThemeMode::Catppuccin => Self::catppuccin(),
            ThemeMode::SolarizedLight => Self::solarized_light(),
        }
    }

    /// 1. Marked Dark — decompdecomp editorial charcoal + yellow highlighter.
    pub const fn dark() -> Self {
        Self {
            dark: true,
            bg: 0x12110D,
            nav: 0x0E0D0A,
            panel: 0x181712,
            bg_alt: 0x21201A,
            overlay: 0x1F1E18,
            code_bg: 0x100F0B,
            line: 0x36342C,
            line_soft: 0x26251F,
            faint: 0x636156,
            ink: 0xEEEDE6,
            ink_2: 0xC2C0B5,
            muted: 0x9E9B8F,
            hl: 0xF5DE2E,
            hl_hover: 0xF8E762,
            hl_ink: 0x12110D,
            hl_soft: 0x312D15,
            btn: 0xF5DE2E,
            btn_hover: 0xF8E762,
            btn_ink: 0x12110D,
            match_fill: 0x1F9D5B,
            match_ink: 0x7BE0A6,
            match_bg: 0x183022,
            match_hi: 0x3B5A2E,
            polished: 0x88C89E,
            work: 0xE6BF64,
            work_bg: 0x342B17,
            miss: 0xED8F82,
            miss_bg: 0x3A1E1B,
            not_c: 0xCF4638,
            info: 0x8BBDF2,
            info_bg: 0x1A2A3B,
            open_cell: 0x323129,
            tried_cell: 0x474539,
            project_swatches: [0x689DF5, 0xD49A52, 0xB482EC, 0x5BB8B4, 0xE078A8, 0x9EA5B2],
            module_colors: [
                0x4A483D, 0x4E8DD6, 0xF5DE2E, 0xCF4638, 0x1F9D5B, 0xD49A52, 0x9E68C8, 0x3FA69B,
            ],
        }
    }

    /// 2. Marked Light — high-contrast warm editorial paper with WCAG AA/AAA ink & borders.
    pub const fn light() -> Self {
        Self {
            dark: false,
            bg: 0xFCFCF7,
            nav: 0xF1F0E7,
            panel: 0xFFFFFF,
            bg_alt: 0xEAE8DC,
            overlay: 0xFFFFFF,
            code_bg: 0xF4F3EB,
            line: 0xBDBAA9,
            line_soft: 0xD9D6C8,
            faint: 0x6E6C60,
            ink: 0x14130F,
            ink_2: 0x2F2E27,
            muted: 0x525046,
            hl: 0xF5DE2E,
            hl_hover: 0xE8CF1A,
            hl_ink: 0x14130F,
            hl_soft: 0xFBF2B5,
            btn: 0x1A1810,
            btn_hover: 0x333020,
            btn_ink: 0xF5DE2E,
            match_fill: 0x157F47,
            match_ink: 0x0F5A32,
            match_bg: 0xDCF3E5,
            match_hi: 0x6DB98A,
            polished: 0x114D2D,
            work: 0x7A5108,
            work_bg: 0xFAF0D2,
            miss: 0xA61E14,
            miss_bg: 0xFCE3E0,
            not_c: 0xB82E22,
            info: 0x164E8A,
            info_bg: 0xDFECF9,
            open_cell: 0xD4D1C3,
            tried_cell: 0x9E9A8B,
            project_swatches: [0x2155B0, 0x9E5E12, 0x6F38AD, 0x157873, 0xA82862, 0x4E5666],
            module_colors: [
                0x8C897B, 0x2162A8, 0xC7A90C, 0xB82E22, 0x157F47, 0xBA7214, 0x753DA6, 0x177A71,
            ],
        }
    }

    /// 3. Nord Frost — Arctic dark slate with high-contrast frost cyan & snow storm ink.
    pub const fn nord() -> Self {
        Self {
            dark: true,
            bg: 0x1E2430,
            nav: 0x171C26,
            panel: 0x242B38,
            bg_alt: 0x2E3647,
            overlay: 0x2A3242,
            code_bg: 0x191F2A,
            line: 0x3E495E,
            line_soft: 0x2E3748,
            faint: 0x6C7A96,
            ink: 0xECEFF4,
            ink_2: 0xD0D8E5,
            muted: 0x9AA8C0,
            hl: 0x88C0D0,
            hl_hover: 0x9DD0DE,
            hl_ink: 0x141A23,
            hl_soft: 0x243A45,
            btn: 0x88C0D0,
            btn_hover: 0x9DD0DE,
            btn_ink: 0x141A23,
            match_fill: 0x63B381,
            match_ink: 0xA3E0B8,
            match_bg: 0x1E362A,
            match_hi: 0x446B53,
            polished: 0xA3BE8C,
            work: 0xEBCB8B,
            work_bg: 0x3B3422,
            miss: 0xF08B94,
            miss_bg: 0x3F2227,
            not_c: 0xD95B66,
            info: 0x81A1C1,
            info_bg: 0x223045,
            open_cell: 0x354054,
            tried_cell: 0x4B5973,
            project_swatches: [0x81A1C1, 0xEBCB8B, 0xB48EAD, 0x88C0D0, 0xD08770, 0xA3BE8C],
            module_colors: [
                0x4C566A, 0x5E81AC, 0x88C0D0, 0xBF616A, 0xA3BE8C, 0xEBCB8B, 0xB48EAD, 0x8FBCBB,
            ],
        }
    }

    /// 4. Gruvbox Warm — Retro dark espresso with warm amber-gold marker & cream ink.
    pub const fn gruvbox() -> Self {
        Self {
            dark: true,
            bg: 0x1D2021,
            nav: 0x161819,
            panel: 0x262524,
            bg_alt: 0x32302F,
            overlay: 0x2E2C2B,
            code_bg: 0x181A1B,
            line: 0x4E4944,
            line_soft: 0x3A3634,
            faint: 0x7C6F64,
            ink: 0xFBF1C7,
            ink_2: 0xD5C4A1,
            muted: 0xA89984,
            hl: 0xFABD2F,
            hl_hover: 0xFCCB56,
            hl_ink: 0x1D2021,
            hl_soft: 0x3C3118,
            btn: 0xFABD2F,
            btn_hover: 0xFCCB56,
            btn_ink: 0x1D2021,
            match_fill: 0x8EC07C,
            match_ink: 0xB8BB26,
            match_bg: 0x24301E,
            match_hi: 0x536634,
            polished: 0x8EC07C,
            work: 0xFE8019,
            work_bg: 0x3A2311,
            miss: 0xFC7462,
            miss_bg: 0x3B1A17,
            not_c: 0xCC241D,
            info: 0x83A598,
            info_bg: 0x1E2F2C,
            open_cell: 0x3C3836,
            tried_cell: 0x504945,
            project_swatches: [0x83A598, 0xFE8019, 0xD3869B, 0x8EC07C, 0xFABD2F, 0xB8BB26],
            module_colors: [
                0x665C54, 0x458588, 0xFABD2F, 0xCC241D, 0x98971A, 0xD65D0E, 0xB16286, 0x689D6A,
            ],
        }
    }

    /// 5. Catppuccin Mocha — Deep midnight indigo with crisp lavender accent.
    pub const fn catppuccin() -> Self {
        Self {
            dark: true,
            bg: 0x1E1E2E,
            nav: 0x181825,
            panel: 0x252538,
            bg_alt: 0x313244,
            overlay: 0x2B2C3E,
            code_bg: 0x161622,
            line: 0x45475A,
            line_soft: 0x313244,
            faint: 0x7F849C,
            ink: 0xCDD6F4,
            ink_2: 0xBAC2DE,
            muted: 0xA6ADC8,
            hl: 0xCBA6F7,
            hl_hover: 0xD9BEFA,
            hl_ink: 0x11111B,
            hl_soft: 0x332946,
            btn: 0xCBA6F7,
            btn_hover: 0xD9BEFA,
            btn_ink: 0x11111B,
            match_fill: 0x40A02B,
            match_ink: 0xA6E3A1,
            match_bg: 0x1E3326,
            match_hi: 0x477355,
            polished: 0x94E2D5,
            work: 0xF9E2AF,
            work_bg: 0x3B3423,
            miss: 0xF38BA8,
            miss_bg: 0x3F212D,
            not_c: 0xE64553,
            info: 0x89B4FA,
            info_bg: 0x1E2C45,
            open_cell: 0x383A4E,
            tried_cell: 0x4E516A,
            project_swatches: [0x89B4FA, 0xFAB387, 0xCBA6F7, 0x94E2D5, 0xF5C2E7, 0xA6E3A1],
            module_colors: [
                0x585B70, 0x89B4FA, 0xF9E2AF, 0xF38BA8, 0xA6E3A1, 0xFAB387, 0xCBA6F7, 0x94E2D5,
            ],
        }
    }

    /// 6. Solarized Paper — High-contrast warm parchment light theme with deep teal ink.
    pub const fn solarized_light() -> Self {
        Self {
            dark: false,
            bg: 0xFDF6E3,
            nav: 0xEEE8D5,
            panel: 0xFFFDF5,
            bg_alt: 0xE6DFCB,
            overlay: 0xFFFDF5,
            code_bg: 0xF5EEDB,
            line: 0xBFB79F,
            line_soft: 0xDCD5C0,
            faint: 0x657B83,
            ink: 0x073642,
            ink_2: 0x1B4B57,
            muted: 0x486068,
            hl: 0xF2C94C,
            hl_hover: 0xE6B933,
            hl_ink: 0x073642,
            hl_soft: 0xF9EAC0,
            btn: 0x073642,
            btn_hover: 0x134E5E,
            btn_ink: 0xFDF6E3,
            match_fill: 0x26853F,
            match_ink: 0x1A622D,
            match_bg: 0xDDF0DF,
            match_hi: 0x78BA86,
            polished: 0x196962,
            work: 0x8A5600,
            work_bg: 0xF7EBD0,
            miss: 0xB3221D,
            miss_bg: 0xF9DFDC,
            not_c: 0xCB3126,
            info: 0x165F94,
            info_bg: 0xDCEAF5,
            open_cell: 0xD3CCB8,
            tried_cell: 0x9FA7A6,
            project_swatches: [0x1B6CA8, 0xB35A00, 0x6C4FA8, 0x177A74, 0xB52B65, 0x586E75],
            module_colors: [
                0x839496, 0x268BD2, 0xB58900, 0xDC322F, 0x859900, 0xCB4B16, 0x6C71C4, 0x2AA198,
            ],
        }
    }
}

/// Compute WCAG 2.1 contrast ratio between two `0xRRGGBB` colors (range 1.0..=21.0).
pub fn contrast_ratio(fg: u32, bg: u32) -> f64 {
    fn rel_lum(rgb: u32) -> f64 {
        fn lin(ch: u32) -> f64 {
            let s = (ch as f64) / 255.0;
            if s <= 0.03928 {
                s / 12.92
            } else {
                ((s + 0.055) / 1.055).powf(2.4)
            }
        }
        let r = lin((rgb >> 16) & 0xFF);
        let g = lin((rgb >> 8) & 0xFF);
        let b = lin(rgb & 0xFF);
        0.2126 * r + 0.7152 * g + 0.0722 * b
    }
    let l1 = rel_lum(fg);
    let l2 = rel_lum(bg);
    let (hi, lo) = if l1 >= l2 { (l1, l2) } else { (l2, l1) };
    (hi + 0.05) / (lo + 0.05)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn has_at_least_five_themes_with_roundtrip_names() {
        assert!(ThemeMode::ALL.len() >= 5);
        for &mode in ThemeMode::ALL {
            assert_eq!(ThemeMode::from_str(mode.as_str()), mode);
            let t = ThemeTokens::for_mode(mode);
            assert_eq!(t.dark, mode.is_dark());
        }
    }

    #[test]
    fn all_themes_meet_wcag_aa_contrast() {
        for &mode in ThemeMode::ALL {
            let t = ThemeTokens::for_mode(mode);
            let name = mode.label();
            assert!(
                contrast_ratio(t.ink, t.bg) >= 7.0,
                "{name}: ink/bg contrast too low ({:.2})",
                contrast_ratio(t.ink, t.bg)
            );
            assert!(
                contrast_ratio(t.ink_2, t.bg) >= 5.5,
                "{name}: ink_2/bg contrast too low ({:.2})",
                contrast_ratio(t.ink_2, t.bg)
            );
            assert!(
                contrast_ratio(t.muted, t.bg) >= 4.5,
                "{name}: muted/bg contrast too low ({:.2})",
                contrast_ratio(t.muted, t.bg)
            );
            assert!(
                contrast_ratio(t.hl_ink, t.hl) >= 7.0,
                "{name}: hl_ink/hl contrast too low ({:.2})",
                contrast_ratio(t.hl_ink, t.hl)
            );
            assert!(
                contrast_ratio(t.btn_ink, t.btn) >= 7.0,
                "{name}: btn_ink/btn contrast too low ({:.2})",
                contrast_ratio(t.btn_ink, t.btn)
            );
            assert!(
                contrast_ratio(t.match_ink, t.match_bg) >= 4.5,
                "{name}: match_ink/match_bg contrast too low ({:.2})",
                contrast_ratio(t.match_ink, t.match_bg)
            );
            assert!(
                contrast_ratio(t.miss, t.miss_bg) >= 4.5,
                "{name}: miss/miss_bg contrast too low ({:.2})",
                contrast_ratio(t.miss, t.miss_bg)
            );
            assert!(
                contrast_ratio(t.work, t.work_bg) >= 4.5,
                "{name}: work/work_bg contrast too low ({:.2})",
                contrast_ratio(t.work, t.work_bg)
            );
            assert!(
                contrast_ratio(t.info, t.info_bg) >= 4.5,
                "{name}: info/info_bg contrast too low ({:.2})",
                contrast_ratio(t.info, t.info_bg)
            );
        }
    }

    #[test]
    fn toggles_cleanly_between_dark_and_light() {
        assert!(!ThemeMode::Dark.toggle().is_dark());
        assert!(ThemeMode::Light.toggle().is_dark());
        assert_eq!(
            ThemeMode::Nord.toggle_dark_light(ThemeMode::Nord, ThemeMode::SolarizedLight),
            ThemeMode::SolarizedLight
        );
        assert_eq!(
            ThemeMode::SolarizedLight.toggle_dark_light(ThemeMode::Nord, ThemeMode::SolarizedLight),
            ThemeMode::Nord
        );
    }
}
