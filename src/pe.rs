use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PeInfo {
    pub ok: bool,
    pub error: String,
    pub machine: String,
    pub magic: String,
    pub pe32_x86: bool,
    pub image_base: u64,
    pub input_os: Option<String>,
    pub input_arch: Option<String>,
}

fn validate_dos_header(size: u64, dos_head: &[u8]) -> Result<usize, String> {
    if size < 0x40 || dos_head.len() < 0x40 {
        return Err("file is shorter than a DOS header -- this is not a PE executable".to_string());
    }
    if dos_head[0] != b'M' || dos_head[1] != b'Z' {
        return Err("no MZ signature -- this is not a DOS/Windows executable".to_string());
    }
    let pe_off = u32_le(dos_head, 0x3c) as u64;
    if pe_off < 0x40 || pe_off.saturating_add(0x40) > size {
        return Err(
            "the PE header offset points outside the file -- likely not a PE, or a truncated file"
                .to_string(),
        );
    }
    Ok(pe_off as usize)
}

fn parse_pe_header_block(pe_head: &[u8]) -> PeInfo {
    let mut info = PeInfo::default();
    if pe_head.len() < 0x40 || &pe_head[..4] != b"PE\0\0" {
        info.error = "no PE\\0\\0 signature at the offset the DOS header names".to_string();
        return info;
    }
    let machine = u16_le(pe_head, 4);
    let opt_off = 24;
    let opt_magic = u16_le(pe_head, opt_off);

    info.machine = match machine {
        0x014c => "i386".to_string(),
        0x8664 => "x86-64".to_string(),
        0x01c4 => "ARMv7".to_string(),
        0xaa64 => "ARM64".to_string(),
        other => format!("unknown (0x{other:04x})"),
    };
    info.magic = if opt_magic == 0x20b {
        "PE32+".to_string()
    } else {
        "PE32".to_string()
    };
    info.pe32_x86 = machine == 0x014c && opt_magic == 0x10b;
    // PE32+ stores ImageBase as a 64-bit field at opt_off + 24; PE32 stores a
    // 32-bit field at opt_off + 28. Reading only four bytes for PE32+ silently
    // truncates standard x64 bases such as 0x140000000 to 0x40000000.
    info.image_base = if opt_magic == 0x20b {
        u64_le(pe_head, opt_off + 24)
    } else {
        u32_le(pe_head, opt_off + 28) as u64
    };
    info.input_os = Some("windows".to_string());
    info.input_arch = Some(match machine {
        0x014c => "x86".to_string(),
        0x8664 => "x86_64".to_string(),
        0xaa64 => "arm64".to_string(),
        _ => info.machine.clone(),
    });
    info.ok = true;
    info
}

/// Pure byte-slice PE header inspector (works on native and wasm32-unknown-unknown).
pub fn inspect_pe_bytes(data: &[u8]) -> PeInfo {
    if let Some(native) = native_format_info(data) {
        return native;
    }
    match validate_dos_header(data.len() as u64, data) {
        Ok(pe_off) => parse_pe_header_block(&data[pe_off..pe_off + 0x40]),
        Err(err) => PeInfo {
            error: err,
            ..PeInfo::default()
        },
    }
}

fn u16_le(b: &[u8], off: usize) -> u16 {
    if off + 2 <= b.len() {
        u16::from_le_bytes([b[off], b[off + 1]])
    } else {
        0
    }
}

fn u32_le(b: &[u8], off: usize) -> u32 {
    if off + 4 <= b.len() {
        u32::from_le_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
    } else {
        0
    }
}

fn u64_le(b: &[u8], off: usize) -> u64 {
    if off + 8 <= b.len() {
        u64::from_le_bytes([
            b[off],
            b[off + 1],
            b[off + 2],
            b[off + 3],
            b[off + 4],
            b[off + 5],
            b[off + 6],
            b[off + 7],
        ])
    } else {
        0
    }
}

/// Drop XML comments so a commented-out key cannot imitate a real one.
fn strip_plist_comments(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(start) = rest.find("<!--") {
        out.push_str(&rest[..start]);
        match rest[start + 4..].find("-->") {
            Some(end) => rest = &rest[start + 4 + end + 3..],
            // An unterminated comment invalidates everything after it; keep
            // only the text before it so no later key can be picked up.
            None => return out,
        }
    }
    out.push_str(rest);
    out
}

/// Read the `<string>` value belonging to a specific `<key>` in an XML plist.
///
/// The value search stops at the next `<key>` or `</dict>` so a string of a
/// later entry can never be attributed to the key we asked for.
fn plist_string_for_key(text: &str, wanted: &str) -> Result<String, String> {
    const KEY_OPEN: &str = "<key>";
    const KEY_CLOSE: &str = "</key>";
    const STRING_OPEN: &str = "<string>";
    const STRING_CLOSE: &str = "</string>";

    let text = strip_plist_comments(text);
    let mut search = 0usize;
    let mut matched = false;
    while let Some(rel) = text[search..].find(KEY_OPEN) {
        let key_start = search + rel + KEY_OPEN.len();
        let Some(rel_end) = text[key_start..].find(KEY_CLOSE) else {
            break;
        };
        let key_end = key_start + rel_end;
        let after_key = key_end + KEY_CLOSE.len();
        if text[key_start..key_end].trim() != wanted {
            search = after_key;
            continue;
        }
        if matched {
            return Err(format!("duplicate <key>{wanted}</key> in Info.plist"));
        }
        matched = true;

        let rest = &text[after_key..];
        let bound = [rest.find(KEY_OPEN), rest.find("</dict>")]
            .into_iter()
            .flatten()
            .min()
            .unwrap_or(rest.len());
        let value_section = &rest[..bound];
        let Some(rel_open) = value_section.find(STRING_OPEN) else {
            return Err(format!(
                "<key>{wanted}</key> has no <string> value in Info.plist"
            ));
        };
        let value_start = rel_open + STRING_OPEN.len();
        let Some(rel_close) = value_section[value_start..].find(STRING_CLOSE) else {
            return Err(format!(
                "unterminated <string> value for <key>{wanted}</key> in Info.plist"
            ));
        };
        let name = value_section[value_start..value_start + rel_close].trim();
        if name.is_empty() || name.contains(['<', '>', '&', '\0']) {
            return Err(format!(
                "invalid <string> value for <key>{wanted}</key> in Info.plist"
            ));
        }
        return Ok(name.to_string());
    }
    Err(format!("no <key>{wanted}</key> in Info.plist"))
}

/// A bundle executable must be one relative filename component. Anything
/// else could escape `Contents/MacOS/` when joined onto the bundle path.
fn is_safe_bundle_executable(name: &str) -> bool {
    let mut components = Path::new(name).components();
    !name.contains(['\\', '\0'])
        && matches!(components.next(), Some(Component::Normal(_)))
        && components.next().is_none()
}

/// Resolve a macOS `.app` bundle directory to its Mach-O executable, returning
/// a diagnostic instead of an untrusted path.
fn resolve_app_bundle_checked(path: &Path) -> Result<PathBuf, String> {
    if !path.is_dir() || path.extension().and_then(|e| e.to_str()) != Some("app") {
        return Ok(path.to_path_buf());
    }
    let plist = path.join("Contents").join("Info.plist");
    let text = std::fs::read_to_string(&plist)
        .map_err(|err| format!("cannot read {}: {err}", plist.display()))?;
    let name = plist_string_for_key(&text, "CFBundleExecutable")?;
    if !is_safe_bundle_executable(&name) {
        return Err(format!(
            "unsafe CFBundleExecutable {name:?} in {} -- expected a single relative filename",
            plist.display()
        ));
    }
    Ok(path.join("Contents").join("MacOS").join(name))
}

/// Resolve a macOS `.app` bundle directory to its Mach-O executable.
/// Returns the original path when it is not an app bundle, or when the bundle
/// cannot be resolved safely (with a diagnostic on stderr).
pub fn resolve_app_bundle(path: &Path) -> PathBuf {
    match resolve_app_bundle_checked(path) {
        Ok(resolved) => resolved,
        Err(err) => {
            eprintln!(
                "warning: cannot resolve app bundle {} safely: {err}",
                path.display()
            );
            path.to_path_buf()
        }
    }
}

/// Identify Mach-O / ELF headers before the PE header check so the GUI can
/// explain a macOS/Linux input instead of reporting a misleading PE error.
fn native_format_info(bytes: &[u8]) -> Option<PeInfo> {
    const MACHO_MAGICS: [(&[u8], &str); 6] = [
        (b"\xfe\xed\xfa\xce", "Mach-O 32-bit big-endian"),
        (b"\xce\xfa\xed\xfe", "Mach-O 32-bit little-endian"),
        (b"\xfe\xed\xfa\xcf", "Mach-O 64-bit big-endian"),
        (b"\xcf\xfa\xed\xfe", "Mach-O 64-bit little-endian"),
        (b"\xca\xfe\xba\xbe", "Mach-O universal"),
        (b"\xbe\xba\xfe\xca", "Mach-O universal"),
    ];
    for (magic, label) in MACHO_MAGICS {
        if bytes.starts_with(magic) {
            let mut info = PeInfo {
                magic: label.to_string(),
                machine: "arm64 or x86_64".to_string(),
                input_os: Some("macos".to_string()),
                input_arch: Some("host".to_string()),
                ..PeInfo::default()
            };
            info.error = "macOS Mach-O input: PE32 lifting does not apply; use the verified source-level port".to_string();
            return Some(info);
        }
    }
    if bytes.starts_with(b"\x7fELF") {
        let mut info = PeInfo {
            magic: "ELF".to_string(),
            machine: "64-bit or 32-bit".to_string(),
            input_os: Some("linux".to_string()),
            input_arch: Some("host".to_string()),
            ..PeInfo::default()
        };
        info.error =
            "Linux ELF input: PE32 lifting does not apply; use the verified source-level port"
                .to_string();
        return Some(info);
    }
    None
}

pub fn inspect_pe(path: &Path) -> PeInfo {
    let mut info = PeInfo::default();
    let resolved = match resolve_app_bundle_checked(path) {
        Ok(resolved) => resolved,
        Err(err) => {
            info.error = err;
            return info;
        }
    };
    let Ok(mut f) = File::open(&resolved) else {
        info.error = "cannot read the file".to_string();
        return info;
    };
    let size = f.metadata().map(|m| m.len()).unwrap_or(0);
    if size < 4 {
        info.error = "file is too small to identify -- this is not a PE executable".to_string();
        return info;
    }
    let mut head = [0u8; 0x40];
    if f.read_exact(&mut head).is_err() {
        info.error = "cannot read the file".to_string();
        return info;
    }
    if let Some(native) = native_format_info(&head) {
        return native;
    }
    let dos_head = head;
    let pe_off = match validate_dos_header(size, &dos_head) {
        Ok(off) => off,
        Err(err) => {
            info.error = err;
            return info;
        }
    };
    let mut pe_head = [0u8; 0x40];
    if f.seek(SeekFrom::Start(pe_off as u64)).is_err() || f.read_exact(&mut pe_head).is_err() {
        info.error = "cannot read the PE header".to_string();
        return info;
    }
    parse_pe_header_block(&pe_head)
}

pub fn run_selftest_pe(path: &Path) -> i32 {
    let info = inspect_pe(path);
    if !info.ok {
        println!("PE: not usable: {}", info.error);
        return 1;
    }
    let suffix = if info.pe32_x86 {
        " -- supported target"
    } else {
        " -- NOT a supported target"
    };
    println!(
        "PE: {} {} image base 0x{:x}{suffix}",
        info.magic, info.machine, info.image_base
    );
    if info.pe32_x86 {
        0
    } else {
        2
    }
}

pub fn resolve_input(
    path: &Path,
    input_os: &str,
    input_arch: &str,
) -> Result<(String, String), String> {
    if !path.is_file() {
        return Err(format!("input executable not found: {}", path.display()));
    }
    let info = inspect_pe(path);
    if !info.ok {
        return Err(format!("input is not a valid PE: {}", info.error));
    }
    let det_os = info.input_os.unwrap_or_else(|| "windows".to_string());
    let det_arch = info.input_arch.unwrap_or_else(|| "x86".to_string());

    if input_os != "auto" && input_os != det_os {
        return Err(format!(
            "declared --input-os {input_os}, but PE header says {det_os}"
        ));
    }
    if input_arch != "auto" && input_arch != det_arch {
        return Err(format!(
            "declared --input-arch {input_arch}, but PE header says {det_arch}"
        ));
    }
    if !info.pe32_x86 {
        return Err(format!(
            "unsupported input format: {} {} (only PE32 i386 is supported today)",
            info.magic, info.machine
        ));
    }
    Ok((det_os, det_arch))
}

pub fn host_identity() -> Result<(String, String), String> {
    let os = match std::env::consts::OS {
        "macos" => "macos",
        "linux" => "linux",
        "windows" => "windows",
        other => return Err(format!("unsupported build host OS: {other}")),
    };
    let arch = match std::env::consts::ARCH {
        "aarch64" => "arm64",
        "x86_64" => "x86_64",
        "x86" => "x86",
        other => return Err(format!("unsupported build host arch: {other}")),
    };
    Ok((os.to_string(), arch.to_string()))
}

pub fn resolve_target(target_os: &str, target_arch: &str) -> Result<(String, String), String> {
    let (host_os, host_arch) = host_identity()?;
    let norm_os = match target_os {
        "wasm" => "web",
        "auto" => "host",
        other => other,
    };
    let norm_arch = match target_arch {
        "auto" => "host",
        other => other,
    };
    if (norm_os == "web" && (norm_arch == "host" || norm_arch == "wasm32" || norm_arch == "wasm"))
        || ((norm_arch == "wasm32" || norm_arch == "wasm")
            && (norm_os == "host" || norm_os == "web"))
    {
        return Ok(("web".to_string(), "wasm32".to_string()));
    }
    let want_os = if norm_os == "host" { &host_os } else { norm_os };
    let want_arch = if norm_arch == "host" {
        &host_arch
    } else {
        norm_arch
    };
    if want_os != host_os || want_arch != host_arch {
        return Err(format!(
            "cross-target builds are not supported ({want_os}/{want_arch} requested on {host_os}/{host_arch} host)"
        ));
    }
    Ok((want_os.to_string(), want_arch.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn inspects_pe32_minimal_fixture() {
        let p = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join("pe32-minimal.exe");
        let info = inspect_pe(&p);
        assert!(info.ok);
        assert_eq!(info.magic, "PE32");
        assert_eq!(info.machine, "i386");
        assert!(info.pe32_x86);
        assert_eq!(info.image_base, 0x400000);

        let raw = std::fs::read(&p).unwrap();
        let from_bytes = inspect_pe_bytes(&raw);
        assert!(from_bytes.ok);
        assert_eq!(from_bytes.magic, info.magic);
        assert_eq!(from_bytes.image_base, info.image_base);

        let bad = inspect_pe_bytes(b"not a pe binary");
        assert!(!bad.ok);
    }

    #[test]
    fn resolves_ghostty_app_bundle_and_detects_macho() {
        let app = PathBuf::from("/Applications/Ghostty.app");
        if !app.is_dir() {
            return;
        }
        let exe = resolve_app_bundle(&app);
        assert!(exe.ends_with("Contents/MacOS/ghostty"));
        let info = inspect_pe(&app);
        assert_eq!(info.input_os.as_deref(), Some("macos"));
        assert!(info.magic.contains("Mach-O"));
        assert!(!info.ok);
    }

    #[test]
    fn detects_elf_input() {
        let info = inspect_pe_bytes(b"\x7fELF\x02\x01\x01");
        assert_eq!(info.input_os.as_deref(), Some("linux"));
        assert_eq!(info.magic, "ELF");
        assert!(!info.ok);
    }

    #[test]
    fn rejects_cross_target() {
        let (host_os, _) = host_identity().unwrap();
        let other = if host_os == "macos" { "linux" } else { "macos" };
        let err = resolve_target(other, "host").unwrap_err();
        assert!(err.contains("cross-target"));
    }

    #[test]
    fn resolves_web_wasm32_target() {
        assert_eq!(
            resolve_target("web", "wasm32").unwrap(),
            ("web".to_string(), "wasm32".to_string())
        );
        assert_eq!(
            resolve_target("wasm", "host").unwrap(),
            ("web".to_string(), "wasm32".to_string())
        );
        let (host_os, host_arch) = host_identity().unwrap();
        assert_eq!(
            resolve_target("auto", "auto").unwrap(),
            (host_os, host_arch)
        );
    }

    /// A throwaway `.app` bundle with a caller-supplied Info.plist.
    struct FakeApp {
        root: PathBuf,
        app: PathBuf,
    }

    impl FakeApp {
        fn new(label: &str, plist: &str) -> Self {
            use std::sync::atomic::{AtomicU64, Ordering};
            static NEXT: AtomicU64 = AtomicU64::new(0);
            let unique = NEXT.fetch_add(1, Ordering::Relaxed);
            let root = std::env::temp_dir().join(format!(
                "game-recomp-pe-test-{label}-{}-{unique}",
                std::process::id()
            ));
            let _ = std::fs::remove_dir_all(&root);
            let app = root.join("Demo.app");
            std::fs::create_dir_all(app.join("Contents").join("MacOS")).unwrap();
            std::fs::write(app.join("Contents").join("Info.plist"), plist).unwrap();
            Self { root, app }
        }
    }

    impl Drop for FakeApp {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    fn plist_with_executable(value: &str) -> String {
        format!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
             <plist version=\"1.0\"><dict>\n\
             <key>CFBundleExecutable</key><string>{value}</string>\n\
             </dict></plist>"
        )
    }

    #[test]
    fn reads_full_64bit_image_base_for_pe32plus() {
        // Minimal PE32+ header: MZ -> e_lfanew 0x40 -> PE\0\0 -> optional
        // header with magic 0x20b and a 64-bit ImageBase at opt_off + 24.
        let mut bytes = vec![0u8; 0x80];
        bytes[0..2].copy_from_slice(b"MZ");
        bytes[0x3c..0x40].copy_from_slice(&0x40u32.to_le_bytes());
        bytes[0x40..0x44].copy_from_slice(b"PE\0\0");
        bytes[0x44..0x46].copy_from_slice(&0x8664u16.to_le_bytes());
        let opt = 0x40 + 24;
        bytes[opt..opt + 2].copy_from_slice(&0x20bu16.to_le_bytes());
        bytes[opt + 24..opt + 32].copy_from_slice(&0x140000000u64.to_le_bytes());

        let info = inspect_pe_bytes(&bytes);
        assert!(info.ok, "{}", info.error);
        assert_eq!(info.magic, "PE32+");
        assert_eq!(info.machine, "x86-64");
        assert_eq!(info.image_base, 0x140000000);

        // A base whose high half is nonzero but low half is not the classic
        // 0x40000000 must also survive intact.
        bytes[opt + 24..opt + 32].copy_from_slice(&0x180000000u64.to_le_bytes());
        assert_eq!(inspect_pe_bytes(&bytes).image_base, 0x180000000);

        // PE32 still reads the 32-bit field at opt_off + 28.
        bytes[opt..opt + 2].copy_from_slice(&0x10bu16.to_le_bytes());
        bytes[opt + 24..opt + 28].copy_from_slice(&0u32.to_le_bytes());
        bytes[opt + 28..opt + 32].copy_from_slice(&0x400000u32.to_le_bytes());
        assert_eq!(inspect_pe_bytes(&bytes).image_base, 0x400000);
    }

    #[test]
    fn rejects_unsafe_bundle_executable_names() {
        for evil in [
            "/bin/sh",
            "../../../outside",
            "Contents/../../evil",
            "sub/tool",
            "..\\outside",
            ".",
            "..",
            "",
        ] {
            let bundle = FakeApp::new("unsafe", &plist_with_executable(evil));
            let err = resolve_app_bundle_checked(&bundle.app).unwrap_err();
            assert!(err.contains("CFBundleExecutable"), "{err}");
            // Public API and file inspection must both fail closed.
            assert_eq!(resolve_app_bundle(&bundle.app), bundle.app);
            let info = inspect_pe(&bundle.app);
            assert!(!info.ok, "{evil} unexpectedly resolved");
            assert!(info.error.contains("CFBundleExecutable"), "{}", info.error);
        }
    }

    #[test]
    fn binds_plist_value_to_its_own_key() {
        // The old code took the first <string> anywhere after the key, so a
        // non-string entry followed by another key's string caused traversal.
        let plist = "<?xml version=\"1.0\"?>\n\
            <plist version=\"1.0\"><dict>\n\
            <key>CFBundleExecutable</key>\n\
            <integer>7</integer>\n\
            <key>CFBundleName</key>\n\
            <string>../../../outside</string>\n\
            </dict></plist>";
        let bundle = FakeApp::new("key-confusion", plist);
        let err = resolve_app_bundle_checked(&bundle.app).unwrap_err();
        assert!(err.contains("CFBundleExecutable"), "{err}");
        assert_eq!(resolve_app_bundle(&bundle.app), bundle.app);

        // A commented-out decoy key must not win over the real one.
        let plist = "<?xml version=\"1.0\"?>\n\
            <plist version=\"1.0\"><dict>\n\
            <!-- <key>CFBundleExecutable</key><string>../../../outside</string> -->\n\
            <key>CFBundleName</key><string>Demo</string>\n\
            <key>CFBundleExecutable</key><string>demo-bin</string>\n\
            <key>CFBundleIdentifier</key><string>com.example.demo</string>\n\
            </dict></plist>";
        let bundle = FakeApp::new("comment-decoy", plist);
        assert_eq!(
            resolve_app_bundle_checked(&bundle.app).unwrap(),
            bundle.app.join("Contents").join("MacOS").join("demo-bin")
        );

        // A later key's string must not replace this key's own value.
        let plist = "<?xml version=\"1.0\"?>\n\
            <plist version=\"1.0\"><dict>\n\
            <key>CFBundleExecutable</key><string>demo-bin</string>\n\
            <key>CFBundleName</key><string>evil-name</string>\n\
            </dict></plist>";
        let bundle = FakeApp::new("later-key", plist);
        assert_eq!(
            resolve_app_bundle(&bundle.app),
            bundle.app.join("Contents").join("MacOS").join("demo-bin")
        );
    }
}
