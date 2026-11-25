//! Pure-Rust verification backend for GameReComp.
//!
//! No external crates beyond serde/serde_json for evidence serialization: SHA-256,
//! process execution, normalization and comparison are implemented here. The
//! core is portable (the comparison functions compile to wasm); process execution
//! is gated off on wasm targets.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::Path;
use std::time::{Duration, Instant};

pub const SCHEMA: u32 = 1;
pub const MAX_CAPTURE_BYTES: usize = 1 << 20;

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
        "Launching ", "exit=", "Arguments: ", "msync:", "wine: failed to open", "[pe] ", "[host] ",
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
    // Two empty outputs are not evidence: treat as BLOCKED, never PASS.
    if ga.trim().is_empty() && gb.trim().is_empty() {
        e.reason = Some("no guest output observed on either side (vacuous comparison)".into());
        return e;
    }
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
    run_command_with(template, timeout, stdin, &BTreeMap::new())
}

#[cfg(not(target_arch = "wasm32"))]
static RUN_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Bounded capture: never let a misbehaving guest fill host memory with a
/// multi-GB log. Keep the head and tail; the middle is explicitly marked.
fn bounded_capture(path: &Path) -> String {
    use std::io::{Read, Seek, SeekFrom};
    let mut out = String::new();
    if let Ok(mut f) = std::fs::File::open(path) {
        let len = f.metadata().map(|m| m.len()).unwrap_or(0) as usize;
        let half = MAX_CAPTURE_BYTES / 2;
        let head = len.min(half);
        let mut buf = vec![0u8; head];
        let _ = f.read_exact(&mut buf);
        out.push_str(&String::from_utf8_lossy(&buf));
        if len > head {
            let tail = len.min(half);
            if f.seek(SeekFrom::End(-(tail as i64))).is_ok() {
                let mut b = vec![0u8; tail];
                let _ = f.read_exact(&mut b);
                out.push_str("\n...[bounded by verify core]...\n");
                out.push_str(&String::from_utf8_lossy(&b));
            }
        }
    }
    let _ = std::fs::remove_file(path);
    out
}

pub fn run_command_with(template: &str, timeout: Duration, stdin: &[u8],
                        env: &BTreeMap<String, String>) -> RunResult {
    let args = split_args(template);
    if args.is_empty() {
        return RunResult { rc: None, stdout: String::new(), stderr: String::new(), seconds: 0.0,
                           error: Some("empty command".to_string()) };
    }
    let t0 = Instant::now();
    use std::io::Write;
    use std::process::{Command, Stdio};
    use std::sync::atomic::Ordering;
    let id = RUN_COUNTER.fetch_add(1, Ordering::Relaxed);
    let tmp = std::env::temp_dir();
    let op = tmp.join(format!("gcrec-v-out-{}-{}", std::process::id(), id));
    let ep = tmp.join(format!("gcrec-v-err-{}-{}", std::process::id(), id));
    let of = std::fs::OpenOptions::new().write(true).create_new(true).open(&op);
    let ef = std::fs::OpenOptions::new().write(true).create_new(true).open(&ep);
    let (of, ef) = match (of, ef) {
        (Ok(x), Ok(y)) => (x, y),
        _ => return RunResult { rc: None, stdout: String::new(), stderr: String::new(),
                                seconds: t0.elapsed().as_secs_f64(), error: Some("temp capture failed".into()) },
    };
    let mut child = match Command::new(&args[0]).args(&args[1..]).envs(env)
        .stdin(Stdio::piped()).stdout(Stdio::from(of)).stderr(Stdio::from(ef)).spawn() {
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
            Ok(None) => std::thread::sleep(Duration::from_millis(10)),
            Err(e) => return RunResult { rc: None, stdout: String::new(), stderr: String::new(),
                                         seconds: t0.elapsed().as_secs_f64(), error: Some(e.to_string()) },
        }
    }
    let rc = if timed_out { let _ = child.wait(); Some(124) }
             else { child.wait().ok().and_then(|s| s.code()) };
    let secs = t0.elapsed().as_secs_f64();
    RunResult { rc, stdout: bounded_capture(&op), stderr: bounded_capture(&ep), seconds: secs,
                error: if timed_out { Some("timeout".into()) } else { None } }
}

/// Fail-closed validation of untrusted scenario data. No shell is involved,
/// but oversized/embedded-newline inputs are rejected before any process starts.
pub fn validate_scenario(s: &Scenario) -> Result<(), String> {
    if s.name.is_empty() || s.name.len() > 128 || s.name.contains('\0') || s.name.contains('\n') {
        return Err("invalid scenario name".into());
    }
    if s.stdin_hex.len() > 2 * MAX_CAPTURE_BYTES {
        return Err("stdin exceeds 1 MiB".into());
    }
    if !s.stdin_hex.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err("stdin_hex is not hex".into());
    }
    if s.args.len() > 64 || s.args.iter().any(|a| a.len() > 4096 || a.contains('\0') || a.contains('\n')) {
        return Err("invalid arguments".into());
    }
    if s.env.len() > 32 || s.env.iter().any(|(k, v)| k.len() > 256 || v.len() > 4096
        || k.contains('\0') || v.contains('\0') || k.contains('\n') || v.contains('\n')) {
        return Err("invalid environment".into());
    }
    if !(0.1..=3600.0).contains(&s.timeout) {
        return Err("timeout out of range".into());
    }
    Ok(())
}

/// One deterministic verification scenario: identical input to both sides.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Scenario {
    pub name: String,
    #[serde(default)]
    pub args: Vec<String>,
    /// Hex-encoded stdin bytes (empty when absent).
    #[serde(default)]
    pub stdin_hex: String,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    #[serde(default = "default_timeout")]
    pub timeout: f64,
    #[serde(default = "default_retries")]
    pub retries: u8,
}

fn default_timeout() -> f64 { 30.0 }
fn default_retries() -> u8 { 2 }

impl Scenario {
    pub fn stdin(&self) -> Vec<u8> {
        let h = self.stdin_hex.as_bytes();
        let mut out = Vec::new();
        for pair in h.chunks(2) {
            if pair.len() == 2 {
                if let Ok(v) = u8::from_str_radix(std::str::from_utf8(pair).unwrap_or("00"), 16) {
                    out.push(v);
                }
            }
        }
        out
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct ScenarioEvidence {
    pub name: String,
    pub input_sha256: String,
    pub args_sha256: String,
    pub attempts: u8,
    pub evidence: Evidence,
}

#[derive(Debug, Clone, Serialize)]
pub struct AdapterReport {
    pub name: String,
    pub verdict: Verdict,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct MatrixReport {
    pub schema: u32,
    pub kind: &'static str,
    pub aggregate: Verdict,
    pub scenarios: Vec<ScenarioEvidence>,
    pub adapters: Vec<AdapterReport>,
    pub coverage: BTreeMap<String, usize>,
    pub limits: String,
}

/// Fail-closed aggregation: one FAIL wins; otherwise any BLOCKED wins;
/// PASS only when every scenario and every adapter passed.
pub fn aggregate_verdict(v: &[Verdict]) -> Verdict {
    if v.iter().any(|x| *x == Verdict::Fail) { Verdict::Fail }
    else if v.iter().any(|x| *x == Verdict::Blocked) || v.is_empty() { Verdict::Blocked }
    else { Verdict::Pass }
}

#[cfg(not(target_arch = "wasm32"))]
pub fn verify_matrix(original: &Path, rebuilt: &Path, original_cmd: &str,
                     rebuilt_cmd: &str, scenarios: &[Scenario]) -> MatrixReport {
    let mut rows = Vec::new();
    for s in scenarios {
        if let Err(reason) = validate_scenario(s) {
            let mut ev = Evidence::new(&format!("scenario/{}", s.name));
            ev.reason = Some(format!("rejected untrusted scenario: {}", reason));
            rows.push(ScenarioEvidence { name: s.name.clone(), input_sha256: sha256(s.stdin_hex.as_bytes()),
                args_sha256: sha256(s.args.join(" ").as_bytes()), attempts: 0, evidence: ev });
            continue;
        }
        let stdin = s.stdin();
        let a_cmd = format!("{} {}", original_cmd.replace("{exe}", &original.to_string_lossy()), s.args.join(" "));
        let b_cmd = format!("{} {}", rebuilt_cmd.replace("{exe}", &rebuilt.to_string_lossy()), s.args.join(" "));
        let t = Duration::from_secs_f64(s.timeout.max(0.1));
        let mut a = run_command_with(&a_cmd, t, &stdin, &s.env);
        let mut attempts = 1u8;
        while attempts <= s.retries.max(1) && (a.rc != Some(0) || (a.stdout.is_empty() && a.stderr.is_empty())) {
            a = run_command_with(&a_cmd, t, &stdin, &s.env);
            attempts += 1;
        }
        let b = run_command_with(&b_cmd, t, &stdin, &s.env);
        let mut e = compare_runs(&a, &b);
        e.kind = format!("scenario/{}", s.name);
        e.limits = "Per-scenario input bytes are identical on both sides; no scope beyond that scenario".into();
        rows.push(ScenarioEvidence {
            name: s.name.clone(),
            input_sha256: sha256(&stdin),
            args_sha256: sha256(s.args.join(" ").as_bytes()),
            attempts,
            evidence: e,
        });
    }
    let mut coverage = BTreeMap::new();
    coverage.insert("scenarios".into(), rows.len());
    coverage.insert("passed".into(), rows.iter().filter(|r| r.evidence.verdict == Verdict::Pass).count());
    coverage.insert("failed".into(), rows.iter().filter(|r| r.evidence.verdict == Verdict::Fail).count());
    coverage.insert("blocked".into(), rows.iter().filter(|r| r.evidence.verdict == Verdict::Blocked).count());
    let all: Vec<Verdict> = rows.iter().map(|r| r.evidence.verdict).collect();
    MatrixReport {
        schema: SCHEMA,
        kind: "matrix",
        aggregate: aggregate_verdict(&all),
        scenarios: rows,
        adapters: Vec::new(),
        coverage,
        limits: "Multi-input process parity; adapters add Unicorn/Frida evidence where available".into(),
    }
}

/// Unicorn adapter: reads tools/lift/difftest.py --json and classifies verdicts.
pub fn unicorn_adapter(report_path: &Path) -> AdapterReport {
    let text = std::fs::read_to_string(report_path).unwrap_or_default();
    let json: serde_json::Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(_) => return AdapterReport { name: "unicorn".into(), verdict: Verdict::Blocked,
                                         detail: "difftest report missing or unparseable".into() },
    };
    let rows = json.get("addresses").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    if rows.is_empty() {
        return AdapterReport { name: "unicorn".into(), verdict: Verdict::Blocked,
                               detail: "no per-function oracle results".into() };
    }
    let ok = rows.iter().filter(|r| matches!(r.get("verdict").and_then(|v| v.as_str()),
        Some("agree") | Some("agree-return") | Some("PASS") | Some("ok"))).count();
    let fault = rows.iter().filter(|r| matches!(r.get("verdict").and_then(|v| v.as_str()),
        Some("original-faulted") | Some("unmapped") | Some("error"))).count();
    let verdict = if ok == rows.len() { Verdict::Pass }
        else if fault > 0 && ok + fault == rows.len() { Verdict::Blocked }
        else { Verdict::Fail };
    AdapterReport { name: "unicorn".into(), verdict,
        detail: format!("{} agree, {} oracle-fault, {} disagree of {} addresses", ok, fault, rows.len() - ok - fault, rows.len()) }
}

/// Frida adapter: live attach is attempted by the supplied probe command.
/// On this macOS host it is expected to be BLOCKED (SIP/dyld); the exact
/// error text is preserved verbatim. No synthetic traces are ever produced.
#[cfg(not(target_arch = "wasm32"))]
pub fn frida_adapter(probe_cmd: &str) -> AdapterReport {
    let r = run_command(probe_cmd, Duration::from_secs(40), b"");
    let text = format!("{}{}", r.stdout, r.stderr);
    let dry_run_only = probe_cmd.contains("--dry-run");
    let blocked = dry_run_only || text.contains("PermissionDenied") || text.contains("NotSupported")
        || text.contains("unable to access") || text.contains("probing dyld")
        || r.error.is_some();
    let verdict = if blocked { Verdict::Blocked }
        else if r.rc == Some(0) && text.contains("frida") { Verdict::Pass }
        else { Verdict::Blocked };
    let detail = if dry_run_only {
        "dry-run probe only — no live Frida attach evidence (never synthesized)".to_string()
    } else {
        text.lines().find(|l| l.contains("PermissionDenied") || l.contains("NotSupported")
            || l.contains("unable to access") || l.contains("dyld") || l.contains("frida"))
            .unwrap_or_else(|| text.lines().find(|l| !l.trim().is_empty()).unwrap_or("no probe output"))
            .to_string()
            .chars().take(600).collect()
    };
    AdapterReport { name: "frida".into(), verdict, detail }
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
    fn output_capture_is_bounded() {
        let cmd = format!("python3 -c print('x'*{})", MAX_CAPTURE_BYTES * 3);
        let r = run_command(&cmd, Duration::from_secs(20), b"");
        assert_eq!(r.rc, Some(0));
        assert!(r.stdout.len() <= MAX_CAPTURE_BYTES + 64, "captured {} bytes", r.stdout.len());
        assert!(r.stdout.contains("bounded by verify core"));
    }

    #[test]
    fn empty_output_is_not_a_pass() {
        let a = RunResult { rc: Some(0), stdout: "Launching x\nexit=0 after 1s\n".into(),
                            stderr: String::new(), seconds: 0.0, error: None };
        let b = a.clone();
        assert_eq!(compare_runs(&a, &b).verdict, Verdict::Blocked);
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

    #[test]
    fn fail_closed_aggregation() {
        assert_eq!(aggregate_verdict(&[]), Verdict::Blocked);
        assert_eq!(aggregate_verdict(&[Verdict::Pass, Verdict::Blocked]), Verdict::Blocked);
        assert_eq!(aggregate_verdict(&[Verdict::Pass, Verdict::Fail]), Verdict::Fail);
        assert_eq!(aggregate_verdict(&[Verdict::Pass, Verdict::Pass]), Verdict::Pass);
    }

    #[test]
    fn untrusted_scenarios_are_rejected() {
        let mut s: Scenario = serde_json::from_str(r#"{"name":"ok","stdin_hex":"330a"}"#).unwrap();
        assert!(validate_scenario(&s).is_ok());
        s.stdin_hex = "zz".into();
        assert!(validate_scenario(&s).is_err());
        s.stdin_hex = "330a".into();
        s.env.insert("BAD".into(), "line\nbreak".into());
        assert!(validate_scenario(&s).is_err());
    }

    #[test]
    fn scenario_hex_stdin_decodes() {
        let s: Scenario = serde_json::from_str(r#"{"name":"x","stdin_hex":"330a","args":["--v"]}"#).unwrap();
        assert_eq!(s.stdin(), b"3\n");
        assert_eq!(s.args, vec!["--v"]);
    }

    #[test]
    fn unicorn_adapter_classifies_oracle_faults() {
        let d = std::env::temp_dir().join("verify-unicorn-test.json");
        std::fs::write(&d, r#"{"addresses":[{"address":"0x1","verdict":"agree"},{"address":"0x2","verdict":"original-faulted"}]}"#).unwrap();
        assert_eq!(unicorn_adapter(&d).verdict, Verdict::Blocked);
        std::fs::remove_file(d).ok();
    }
}
