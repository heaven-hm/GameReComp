use std::process::Command;

fn main() {
    println!("cargo:rustc-check-cfg=cfg(have_sdl2)");
    println!("cargo:rustc-check-cfg=cfg(have_sdl2_ttf)");
    println!("cargo:rerun-if-env-changed=PKG_CONFIG_PATH");
    println!("cargo:rerun-if-changed=build.rs");

    let output = Command::new("pkg-config")
        .args(["--libs", "sdl2", "SDL2_ttf"])
        .output();

    let Ok(out) = output else {
        return;
    };
    if !out.status.success() {
        return;
    }

    let flags = String::from_utf8_lossy(&out.stdout);
    let mut has_sdl2 = false;
    let mut has_ttf = false;

    for tok in flags.split_whitespace() {
        if let Some(dir) = tok.strip_prefix("-L") {
            if !dir.is_empty() {
                println!("cargo:rustc-link-search=native={dir}");
            }
        } else if let Some(lib) = tok.strip_prefix("-l") {
            // Skip SDL2main because Rust defines its own main().
            if lib == "SDL2main" || lib.is_empty() {
                continue;
            }
            if lib == "SDL2" {
                has_sdl2 = true;
            } else if lib == "SDL2_ttf" {
                has_ttf = true;
            }
            println!("cargo:rustc-link-lib={lib}");
        }
    }

    if has_sdl2 {
        println!("cargo:rustc-cfg=have_sdl2");
        if has_ttf {
            println!("cargo:rustc-cfg=have_sdl2_ttf");
        }
    }
}
