use std::fs::File;
use std::io::Read;
use std::path::Path;

#[derive(Debug, Clone, Default)]
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

pub fn inspect_pe(path: &Path) -> PeInfo {
    let mut info = PeInfo::default();
    let Ok(mut f) = File::open(path) else {
        info.error = "cannot read the file".to_string();
        return info;
    };
    let size = f.metadata().map(|m| m.len()).unwrap_or(0);
    if size < 0x40 {
        info.error = "cannot read the file".to_string();
        return info;
    }
    let mut head = vec![0u8; 0x400];
    let n = match f.read(&mut head) {
        Ok(n) => n,
        Err(_) => {
            info.error = "cannot read the file".to_string();
            return info;
        }
    };
    head.truncate(n);
    if head.len() < 0x40 || head[0] != b'M' || head[1] != b'Z' {
        info.error = "no MZ signature -- this is not a DOS/Windows executable".to_string();
        return info;
    }
    let pe_off = u32_le(&head, 0x3c) as usize;
    if pe_off < 0x40 || pe_off + 0x18 > head.len() {
        info.error = "the PE header offset points outside the first 1 KB -- likely not a PE, or a truncated file".to_string();
        return info;
    }
    if &head[pe_off..pe_off + 4] != b"PE\0\0" {
        info.error = "no PE\\0\\0 signature at the offset the DOS header names".to_string();
        return info;
    }

    let machine = u16_le(&head, pe_off + 4);
    let opt_off = pe_off + 24;
    let opt_magic = u16_le(&head, opt_off);

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
        u32_le(&head, opt_off + 24) as u64
    } else {
        u32_le(&head, opt_off + 28) as u64
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
    if info.pe32_x86 { 0 } else { 2 }
}

pub fn resolve_input(path: &Path, input_os: &str, input_arch: &str) -> Result<(String, String), String> {
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
        return Err(format!("declared --input-os {input_os}, but PE header says {det_os}"));
    }
    if input_arch != "auto" && input_arch != det_arch {
        return Err(format!("declared --input-arch {input_arch}, but PE header says {det_arch}"));
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
    let norm_os = if target_os == "wasm" { "web" } else { target_os };
    if (norm_os == "web" && (target_arch == "host" || target_arch == "wasm32" || target_arch == "wasm"))
        || ((target_arch == "wasm32" || target_arch == "wasm") && (norm_os == "host" || norm_os == "web"))
    {
        return Ok(("web".to_string(), "wasm32".to_string()));
    }
    let want_os = if norm_os == "host" { &host_os } else { norm_os };
    let want_arch = if target_arch == "host" { &host_arch } else { target_arch };
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
    }
}
