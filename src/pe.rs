use std::fs;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

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
    info.image_base = if opt_magic == 0x20b {
        u32_le(pe_head, opt_off + 24) as u64
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

/// Resolve a macOS `.app` bundle directory to its Mach-O executable.
pub fn resolve_app_bundle(path: &Path) -> PathBuf {
    let is_app = path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case("app"));
    if !path.is_dir() || !is_app {
        return path.to_path_buf();
    }
    let plist = path.join("Contents").join("Info.plist");
    let text = std::fs::read_to_string(&plist).unwrap_or_default();
    let mut name = None;
    if let Some(pos) = text.find("<key>CFBundleExecutable</key>") {
        let tail = &text[pos..];
        if let (Some(a), Some(b)) = (tail.find("<string>"), tail.find("</string>")) {
            if a < b {
                name = Some(tail[a + 8..b].trim().to_string());
            }
        }
    }
    let name = name.unwrap_or_else(|| {
        path.file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("Contents")
            .to_string()
    });
    let macos = path.join("Contents").join("MacOS");
    let candidate = macos.join(&name);
    if candidate.is_file() {
        return candidate;
    }
    // Binary Info.plist or a renamed executable: use the first real file.
    if let Ok(entries) = fs::read_dir(&macos) {
        if let Some(first) = entries.filter_map(Result::ok)
            .map(|e| e.path())
            .find(|p| p.is_file())
        {
            return first;
        }
    }
    macos.join(name)
}

/// Detect Mach-O / ELF headers so a macOS/Linux input gets an honest reason.
fn native_format_info(bytes: &[u8]) -> Option<PeInfo> {
    const MACHO_MAGICS: [(&[u8], &str); 8] = [
        (b"\xfe\xed\xfa\xce", "Mach-O 32-bit big-endian"),
        (b"\xce\xfa\xed\xfe", "Mach-O 32-bit little-endian"),
        (b"\xfe\xed\xfa\xcf", "Mach-O 64-bit big-endian"),
        (b"\xcf\xfa\xed\xfe", "Mach-O 64-bit little-endian"),
        (b"\xca\xfe\xba\xbe", "Mach-O universal"),
        (b"\xbe\xba\xfe\xca", "Mach-O universal"),
        (b"\xca\xfe\xba\xbf", "Mach-O universal 64-bit"),
        (b"\xbf\xba\xfe\xca", "Mach-O universal 64-bit"),
    ];
    for (magic, label) in MACHO_MAGICS {
        if bytes.starts_with(magic) {
            return Some(PeInfo {
                magic: label.to_string(),
                machine: "arm64 or x86_64".to_string(),
                input_os: Some("macos".to_string()),
                input_arch: Some("host".to_string()),
                error: "macOS Mach-O input: PE32 lifting does not apply; use the verified source-level port".to_string(),
                ..PeInfo::default()
            });
        }
    }
    if bytes.starts_with(b"\x7fELF") {
        return Some(PeInfo {
            magic: "ELF".to_string(),
            machine: "64-bit or 32-bit".to_string(),
            input_os: Some("linux".to_string()),
            input_arch: Some("host".to_string()),
            error: "Linux ELF input: PE32 lifting does not apply; use the verified source-level port".to_string(),
            ..PeInfo::default()
        });
    }
    None
}

pub fn inspect_pe(path: &Path) -> PeInfo {
    let mut info = PeInfo::default();
    let resolved = resolve_app_bundle(path);
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
    if f.read_exact(&mut head[..4]).is_err() {
        info.error = "cannot read the file".to_string();
        return info;
    }
    if let Some(native) = native_format_info(&head[..4]) {
        return native;
    }
    if f.read_exact(&mut head[4..]).is_err() {
        info.error = "cannot read the file".to_string();
        return info;
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
            "input accepted, but reconstruction is PE32 i386 only: {} {} (64-bit guests are a different binary and are not reconstructed by this backend)",
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
    fn resolves_macos_app_bundle_to_macho() {
        let app = PathBuf::from("/System/Applications/Calculator.app");
        if !app.is_dir() { return; }
        let exe = resolve_app_bundle(&app);
        assert!(exe.ends_with("Contents/MacOS/Calculator"));
        let info = inspect_pe(&app);
        assert_eq!(info.input_os.as_deref(), Some("macos"));
        assert!(info.magic.contains("Mach-O"));
        assert!(!info.ok);
    }

    #[test]
    fn detects_elf_bytes_without_pe_header() {
        let info = inspect_pe_bytes(b"\x7fELF\x02\x01\x01");
        assert_eq!(info.input_os.as_deref(), Some("linux"));
        assert_eq!(info.magic, "ELF");
        assert!(!info.ok);
    }

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
}
