//! Pure-Rust verification backend for GameReComp.
//!
//! No external crates beyond serde/serde_json for evidence serialization: SHA-256,
//! process execution, normalization and comparison are implemented here. The
//! core is portable (the comparison functions compile to wasm); process execution
//! is gated off on wasm targets.

use serde::Serialize;
use std::collections::BTreeMap;
use std::path::Path;
use std::time::{Duration, Instant};

pub const SCHEMA: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Verdict {
    Pass,
    Fail,
    Blocked,
}

/// Minimal SHA-256 (FIPS 180-4), pure Rust.
pub fn sha256(data: &[u8]) -> String {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];
    let mut h = [
        0x6a09e667u32, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ];
    let bit_len = (data.len() as u64) * 8;
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_be_bytes());
    for chunk in msg.chunks_exact(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([chunk[i * 4], chunk[i * 4 + 1], chunk[i * 4 + 2], chunk[i * 4 + 3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d) = (h[0], h[1], h[2], h[3]);
        let (mut e, mut f, mut g, mut hh) = (h[4], h[5], h[6], h[7]);
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = hh.wrapping_add(s1).wrapping_add(ch).wrapping_add(K[i]).wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g; g = f; f = e; e = d.wrapping_add(t1);
            d = c; c = b; b = a; a = t1.wrapping_add(t2);
        }
        for (x, y) in h.iter_mut().zip([a, b, c, d, e, f, g, hh]) {
            *x = x.wrapping_add(y);
        }
    }
    h.iter().map(|x| format!("{:08x}", x)).collect()
}

/// Strip launcher/Wine/GameReComp runtime chatter; keep only guest output.
pub fn normalize(text: &str) -> String {
    let prefixes = [
        "Launching ", "exit=", "msync:", "wine: failed to open", "[pe] ", "[host] ",
        "[win32hle] ", "[screen] ", "[screenshot] ",
    ];
    let mut out = Vec::new();
    for line in text.lines() {
        if prefixes.iter().any(|p| line.starts_with(p)) || is_err_line(line) {
            continue;
        }
        out.push(line);
    }
    let mut s = out.join("\n");
    if !out.is_empty() {
        s.push('\n');
    }
    s
}

fn is_err_line(line: &str) -> bool {
    let b = line.as_bytes();
    b.len() > 5
        && b.iter().take(4).all(|c| c.is_ascii_hexdigit())
        && b[4] == b':'
        && (line[5..].starts_with("err:") || line[5..].starts_with("warn:") || line[5..].starts_with("fixme:"))
}

pub fn first_difference(a: &str, b: &str) -> Option<(usize, String, String)> {
    let mut al = a.lines();
    let mut bl = b.lines();
    let mut i = 0;
    loop {
        match (al.next(), bl.next()) {
            (None, None) => return None,
            (x, y) => {
                i += 1;
                if x != y {
                    return Some((i, x.unwrap_or("<missing>").to_string(), y.unwrap_or("<missing>").to_string()));
                }
            }
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct RunResult {
    pub rc: Option<i32>,
    pub stdout: String,
    pub stderr: String,
    pub seconds: f64,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Evidence {
    pub schema: u32,
    pub kind: String,
    pub verdict: Verdict,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    pub checks: BTreeMap<String, bool>,
    pub failed_checks: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stdout_sha256: Option<BTreeMap<String, String>>,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub inputs: BTreeMap<String, String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub difference: Option<serde_json::Value>,
    pub limits: String,
}

impl Evidence {
    pub fn new(kind: &str) -> Self {
        Self {
            schema: SCHEMA,
            kind: kind.to_string(),
            verdict: Verdict::Blocked,
            reason: None,
            checks: BTreeMap::new(),
            failed_checks: Vec::new(),
            stdout_sha256: None,
            inputs: BTreeMap::new(),
            difference: None,
            limits: "Process-level parity only; no gameplay/audio/timing oracle is implied".to_string(),
        }
    }

    pub fn finish(mut self) -> Self {
        self.failed_checks = self.checks.iter().filter(|(_, v)| !**v).map(|(k, _)| k.clone()).collect();
        self.verdict = if self.checks.is_empty() {
            Verdict::Blocked
        } else if self.failed_checks.is_empty() {
            Verdict::Pass
        } else {
            Verdict::Fail
        };
        self
    }
}

/// Compare two already-executed runs; identical rules to the Python engine.
pub fn compare_runs(a: &RunResult, b: &RunResult) -> Evidence {
    let mut e = Evidence::new("process");
    if let Some(err) = &a.error {
        e.reason = Some(format!("original run failed: {}", err));
        return e;
    }
    if let Some(err) = &b.error {
        e.reason = Some(format!("rebuilt run failed: {}", err));
        return e;
    }
    if a.rc == Some(53) && b.stdout.contains("failed to open") {
        e.reason = Some("original could not be opened by the runner (exit 53)".to_string());
        return e;
    }
    let ga = normalize(&format!("{}{}", a.stdout, a.stderr));
    let gb = normalize(&format!("{}{}", b.stdout, b.stderr));
    let mut hashes = BTreeMap::new();
    hashes.insert("original".to_string(), sha256(ga.as_bytes()));
    hashes.insert("rebuilt".to_string(), sha256(gb.as_bytes()));
    e.stdout_sha256 = Some(hashes);
    e.checks.insert("exit_code".to_string(), a.rc == b.rc);
    e.checks.insert("stdout_exact".to_string(), ga == gb);
    if ga != gb {
        if let Some((line, x, y)) = first_difference(&ga, &gb) {
            e.difference = Some(serde_json::json!({"line": line, "original": x, "rebuilt": y}));
        }
    }
    e.finish()
}

/// A/B watched-value trace comparison with absolute tolerance.
pub fn compare_traces(a: &str, b: &str, tolerance: f64) -> Evidence {
    let mut e = Evidence::new("trace");
    let load = |s: &str| -> BTreeMap<String, Vec<String>> {
        let mut m = BTreeMap::new();
        for line in s.lines() {
            let p: Vec<&str> = line.split_whitespace().collect();
            if p.len() >= 3 {
                m.insert(p[0].to_string(), p[2..].iter().map(|x| x.to_string()).collect());
            }
        }
        m
    };
    let (ta, tb) = (load(a), load(b));
    let common: Vec<_> = ta.keys().filter(|k| tb.contains_key(*k)).collect();
    if common.is_empty() {
        e.reason = Some("no overlapping trace timestamps".to_string());
        return e;
    }
    let mut worst: Vec<f64> = Vec::new();
    let mut exact_bad = Vec::new();
    for k in &common {
        let key = k.as_str();
        for (i, (x, y)) in ta[key].iter().zip(&tb[key]).enumerate() {
            match (x.parse::<f64>(), y.parse::<f64>()) {
                (Ok(fx), Ok(fy)) => {
                    let d = (fx - fy).abs();
                    if i >= worst.len() { worst.resize(i + 1, 0.0); }
                    worst[i] = worst[i].max(d);
                }
                _ if x != y => exact_bad.push(serde_json::json!({"time": k, "column": i, "a": x, "b": y})),
                _ => {}
            }
        }
    }
    e.checks.insert("numeric_tolerance".to_string(), worst.iter().all(|d| *d <= tolerance));
    e.checks.insert("exact_values".to_string(), exact_bad.is_empty());
    e.difference = Some(serde_json::json!({"max_delta_per_column": worst, "exact_mismatches": exact_bad}));
    e.finish()
}

/// Hash every PNG/BMP in a directory (stable file-name order).
pub fn frame_hashes(dir: &Path) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    if let Ok(rd) = std::fs::read_dir(dir) {
        let mut files: Vec<_> = rd.filter_map(Result::ok).collect();
        files.sort_by_key(|f| f.file_name());
        for f in files {
            if let Ok(bytes) = std::fs::read(f.path()) {
                let name = f.file_name().to_string_lossy().to_string();
                if name.to_lowercase().ends_with(".bmp") || name.to_lowercase().ends_with(".png") {
                    out.insert(name, sha256(&bytes));
                }
            }
        }
    }
    out
}

#[cfg(not(target_arch = "wasm32"))]
fn split_args(s: &str) -> Vec<String> {
    // Deliberately simple: verification commands are templates, not shell scripts.
    s.split_whitespace().map(|x| x.to_string()).collect()
}

#[cfg(not(target_arch = "wasm32"))]
pub fn run_command(template: &str, timeout: Duration, stdin: &[u8]) -> RunResult {
    let args = split_args(template);
    if args.is_empty() {
        return RunResult { rc: None, stdout: String::new(), stderr: String::new(), seconds: 0.0,
                           error: Some("empty command".to_string()) };
    }
    let t0 = Instant::now();
    use std::io::Write;
    use std::process::{Command, Stdio};
    let mut child = match Command::new(&args[0]).args(&args[1..])
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn() {
        Ok(c) => c,
        Err(e) => return RunResult { rc: None, stdout: String::new(), stderr: String::new(),
                                     seconds: t0.elapsed().as_secs_f64(), error: Some(e.to_string()) },
    };
    if let Some(mut input) = child.stdin.take() {
        let _ = input.write_all(stdin);
    }
    let mut timed_out = false;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if t0.elapsed() >= timeout => { timed_out = true; let _ = child.kill(); break; }
            Ok(None) => std::thread::sleep(Duration::from_millis(20)),
            Err(e) => return RunResult { rc: None, stdout: String::new(), stderr: String::new(),
                                         seconds: t0.elapsed().as_secs_f64(), error: Some(e.to_string()) },
        }
    }
    let out = child.wait_with_output().ok();
    let secs = t0.elapsed().as_secs_f64();
    match out {
        Some(o) => RunResult {
            rc: o.status.code(),
            stdout: String::from_utf8_lossy(&o.stdout).to_string(),
            stderr: String::from_utf8_lossy(&o.stderr).to_string(),
            seconds: secs,
            error: if timed_out { Some("timeout".to_string()) } else { None },
        },
        None => RunResult { rc: None, stdout: String::new(), stderr: String::new(), seconds: secs,
                            error: Some("wait failed".to_string()) },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha256_known_vectors() {
        assert_eq!(sha256(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        assert_eq!(sha256(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }

    #[test]
    fn normalization_removes_launcher_noise() {
        let raw = "msync: up\nLaunching x.exe\nhello\n0024:err:module:bad\nexit=0 after 1s\n";
        assert_eq!(normalize(raw), "hello\n");
    }

    #[test]
    fn identical_runs_pass_different_fail() {
        let a = RunResult { rc: Some(0), stdout: "x\n".into(), stderr: String::new(), seconds: 0.0, error: None };
        let b = a.clone();
        assert_eq!(compare_runs(&a, &b).verdict, Verdict::Pass);
        let mut c = a.clone(); c.stdout = "y\n".into();
        assert_eq!(compare_runs(&a, &c).verdict, Verdict::Fail);
    }

    #[test]
    fn missing_oracle_is_blocked() {
        let a = RunResult { rc: None, stdout: String::new(), stderr: String::new(), seconds: 0.0,
                            error: Some("no binary".into()) };
        let b = a.clone();
        assert_eq!(compare_runs(&a, &b).verdict, Verdict::Blocked);
    }

    #[test]
    fn trace_tolerance_detects_drift() {
        let a = "0 n 1.000 2\n0.5 n 1.0005 2\n";
        let b = "0 n 1.000 2\n0.5 n 1.0005 3\n";
        let e = compare_traces(a, b, 0.001);
        assert_eq!(e.verdict, Verdict::Fail);
        assert_eq!(e.checks.get("numeric_tolerance"), Some(&false));
    }
}
