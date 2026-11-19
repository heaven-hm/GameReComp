/// Platform-agnostic "Marked Listing" design tokens derived from decompdecomp's tokens.css.
/// Shared by the native GPUI desktop GUI and future WebAssembly (WASM) frontends.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ThemeMode {
    Dark,
    Light,
}

impl ThemeMode {
    pub fn from_str(s: &str) -> Self {
        if s.eq_ignore_ascii_case("light") {
            Self::Light
        } else {
            Self::Dark
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Dark => "dark",
            Self::Light => "light",
        }
    }

    pub fn toggle(self) -> Self {
        match self {
            Self::Dark => Self::Light,
            Self::Light => Self::Dark,
        }
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
        }
    }

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
            muted: 0x9A978B,
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

    pub const fn light() -> Self {
        Self {
            dark: false,
            bg: 0xFAFAF5,
            nav: 0xF3F2EB,
            panel: 0xFAFAF5,
            bg_alt: 0xECEBE2,
            overlay: 0xFAFAF5,
            code_bg: 0xF2F1E9,
            line: 0xCCCABF,
            line_soft: 0xE2E0D6,
            faint: 0x8A887D,
            ink: 0x181712,
            ink_2: 0x4E4D44,
            muted: 0x5E5C52,
            hl: 0xF5DE2E,
            hl_hover: 0xF8E762,
            hl_ink: 0x181712,
            hl_soft: 0xF8F1BE,
            btn: 0x2C2810,
            btn_hover: 0x464019,
            btn_ink: 0xF5DE2E,
            match_fill: 0x1A8C50,
            match_ink: 0x156239,
            match_bg: 0xE0F2E7,
            match_hi: 0x88C69C,
            polished: 0x165232,
            work: 0x7A5810,
            work_bg: 0xF8F1DC,
            miss: 0x9E251B,
            miss_bg: 0xF8E4E1,
            not_c: 0xCC3F31,
            info: 0x1F5B99,
            info_bg: 0xE4EEF8,
            open_cell: 0xD8D6CA,
            tried_cell: 0xA9A69A,
            project_swatches: [0x2D62BD, 0xA8691D, 0x7B46B8, 0x1D8580, 0xB83770, 0x5E6675],
            module_colors: [
                0x9B988C, 0x2B72BD, 0xD9BD14, 0xCC3F31, 0x1A8C50, 0xCC8426, 0x844EB5, 0x208C82,
            ],
        }
    }
}
