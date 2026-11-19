use std::fs;
use std::path::{Path, PathBuf};

use crate::config::ToolchainStatus;
use crate::json::JsonValue;

#[derive(Debug, Clone, Default)]
pub struct Stage {
    pub name: String,
    pub status: String,
    pub detail: String,
}

#[derive(Debug, Clone, Default)]
pub struct FunctionRow {
    pub address: String,
    pub symbol: String,
    pub category: String,
    pub file: String,
    pub source_path: String,
    pub verified_by: String,
    pub not_proven: String,
    pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct BuildInfo {
    pub present: bool,
    pub binary_path: String,
    pub binary_sha256: String,
    pub binary_size: u64,
    pub build_command: String,
    pub source_files: usize,
    pub source_digest: String,
    pub built_from_digest: String,
    pub digests_match: bool,
}

#[derive(Debug, Clone, Default)]
pub struct CertifiedRow {
    pub address: String,
    pub verdict: String,
    pub compares: String,
    pub named_memory: usize,
    pub cases: i64,
    pub module: String,
    pub verdict_json: String,
}

#[derive(Debug, Clone, Default)]
pub struct ReportDiagnostics {
    pub build_passed: bool,
    pub boot_passed: bool,
    pub strict_boot_passed: bool,
    pub unresolved_import_count: i64,
    pub unhandled_eip_count: i64,
    pub crash_signal: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct ProjectModel {
    pub dir: PathBuf,
    pub pe_path: String,
    pub score: i64,
    pub verification: String,
    pub stages: Vec<Stage>,
    pub functions: Vec<FunctionRow>,
    pub certified: Vec<CertifiedRow>,
    pub build: BuildInfo,
    pub frames: Vec<PathBuf>,
    pub frames_dir: Option<PathBuf>,
    pub diag: ReportDiagnostics,
}

fn read_json_object(path: &Path) -> Option<JsonValue> {
    let text = fs::read_to_string(path).ok()?;
    let val = JsonValue::parse(&text).ok()?;
    if val.as_object().is_some() {
        Some(val)
    } else {
        None
    }
}

impl ProjectModel {
    pub fn load(dir: &Path) -> Result<Self, String> {
        if !dir.is_dir() {
            return Err(format!("{} is not a directory", dir.display()));
        }

        let report_path = dir.join("reports").join("report.json");
        let report = read_json_object(&report_path).ok_or_else(|| {
            "no reports/report.json -- run the pipeline over this directory first".to_string()
        })?;

        let mut model = Self {
            dir: dir.to_path_buf(),
            score: report.get("score").and_then(|v| v.as_i64()).unwrap_or(0),
            verification: report
                .get("outcome")
                .and_then(|o| o.get("status"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            ..Default::default()
        };

        if let Some(stages_arr) = report.get("stages").and_then(|v| v.as_array()) {
            for s in stages_arr {
                let name = s.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let status = s.get("status").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let data = s.get("data");
                let mut detail = data
                    .and_then(|d| d.get("detail"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if detail.is_empty() {
                    if let Some(sc) = data.and_then(|d| d.get("score")).and_then(|v| v.as_i64()) {
                        detail = format!("score {sc}");
                    }
                }
                if name == "compile" && status == "ok" {
                    model.diag.build_passed = true;
                }
                if name == "run" {
                    if let Some(d) = data {
                        if let Some(u) = d.get("unresolved_import_count").and_then(|v| v.as_i64()) {
                            model.diag.unresolved_import_count = u;
                        }
                        if let Some(e) = d.get("unhandled_eip_count").and_then(|v| v.as_i64()) {
                            model.diag.unhandled_eip_count = e;
                        }
                        if let Some(bp) = d.get("boot_passed").and_then(|v| v.as_bool()) {
                            model.diag.boot_passed = bp;
                        } else if status == "ok" && model.diag.unresolved_import_count == 0 {
                            model.diag.boot_passed = true;
                        }
                        if let Some(sp) = d.get("strict_boot_passed").and_then(|v| v.as_bool()) {
                            model.diag.strict_boot_passed = sp;
                        } else {
                            model.diag.strict_boot_passed = model.diag.boot_passed;
                        }
                        model.diag.crash_signal = d
                            .get("crash_signal")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string());
                    }
                }
                model.stages.push(Stage { name, status, detail });
            }
        }

        if let Some(m) = report.get("metrics") {
            if let Some(bp) = m.get("build_passed").and_then(|v| v.as_bool()) {
                model.diag.build_passed = bp;
            }
            if let Some(bp) = m.get("boot_passed").and_then(|v| v.as_bool()) {
                model.diag.boot_passed = bp;
            }
            if let Some(sp) = m.get("strict_boot_passed").and_then(|v| v.as_bool()) {
                model.diag.strict_boot_passed = sp;
            }
            if let Some(u) = m.get("unresolved_import_count").and_then(|v| v.as_i64()) {
                model.diag.unresolved_import_count = u;
            }
            if let Some(e) = m.get("unhandled_eip_count").and_then(|v| v.as_i64()) {
                model.diag.unhandled_eip_count = e;
            }
            if model.diag.crash_signal.is_none() {
                model.diag.crash_signal = m
                    .get("crash_signal")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
            }
        }
        if model.verification == "launch-failed" {
            model.diag.boot_passed = false;
            model.diag.strict_boot_passed = false;
        }

        // Load screenshots if captured.
        let shots_dir = dir.join("screenshots");
        if shots_dir.is_dir() {
            model.frames_dir = Some(shots_dir.clone());
            if let Ok(rd) = fs::read_dir(&shots_dir) {
                let mut paths: Vec<PathBuf> = rd
                    .filter_map(|e| e.ok())
                    .map(|e| e.path())
                    .filter(|p| {
                        p.is_file()
                            && matches!(
                                p.extension().and_then(|x| x.to_str()).map(|s| s.to_ascii_lowercase()).as_deref(),
                                Some("png") | Some("bmp")
                            )
                    })
                    .collect();
                paths.sort();
                model.frames = paths;
            }
        }

        // Load manifest.json.
        if let Some(manifest) = read_json_object(&dir.join("manifest.json")) {
            model.pe_path = manifest
                .get("pe")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            model.build.present = true;
            model.build.source_digest = manifest
                .get("source_digest_sha256")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            model.build.source_files = manifest
                .get("source_files")
                .and_then(|v| v.as_array())
                .map(|a| a.len())
                .unwrap_or(0);
            if let Some(bin) = manifest.get("binary").filter(|v| v.as_object().is_some()) {
                model.build.binary_path = bin
                    .get("path")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                model.build.binary_sha256 = bin
                    .get("sha256")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                model.build.binary_size = bin
                    .get("size")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
                model.build.build_command = bin
                    .get("build_command")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                model.build.built_from_digest = bin
                    .get("built_from_source_digest")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                model.build.digests_match = !model.build.source_digest.is_empty()
                    && model.build.source_digest == model.build.built_from_digest;
            }
        }

        // Load project.json.
        if let Some(project) = read_json_object(&dir.join("project.json")) {
            if model.pe_path.is_empty() {
                model.pe_path = project
                    .get("pe")
                    .and_then(|p| p.get("path"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
            }
            if let Some(funcs) = project.get("high_level_functions").and_then(|v| v.as_array()) {
                for f in funcs {
                    let address = f.get("address").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    let symbol = f.get("symbol").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    let category = f.get("category").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    let file = f.get("module").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    let prov = f.get("provenance");
                    let verified_by = prov
                        .and_then(|p| p.get("verified_by"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let not_proven = prov
                        .and_then(|p| p.get("not_proven"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let evidence = prov
                        .and_then(|p| p.get("evidence"))
                        .and_then(|v| v.as_array())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                                .collect()
                        })
                        .unwrap_or_default();
                    let rel = format!("src/highlevel/{address}.c");
                    let source_path = if dir.join(&rel).is_file() { rel } else { String::new() };
                    model.functions.push(FunctionRow {
                        address,
                        symbol,
                        category,
                        file,
                        source_path,
                        verified_by,
                        not_proven,
                        evidence,
                    });
                }
            }
        }

        Ok(model)
    }

    pub fn load_certified_index(&mut self, repo_root: &Path) {
        self.certified.clear();
        let idx_path = repo_root.join("reports").join("certified").join("index.json");
        let Some(idx) = read_json_object(&idx_path) else {
            return;
        };
        let Some(rows) = idx.get("rows").and_then(|v| v.as_array()) else {
            return;
        };
        for r in rows {
            self.certified.push(CertifiedRow {
                address: r.get("address").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                verdict: r.get("verdict").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                compares: r.get("compares").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                named_memory: r
                    .get("named_memory")
                    .and_then(|v| v.as_array())
                    .map(|a| a.len())
                    .unwrap_or(0),
                cases: r.get("cases").and_then(|v| v.as_i64()).unwrap_or(0),
                module: r.get("module").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                verdict_json: r.get("verdict_json").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            });
        }
    }

    pub fn built_binary_path(&self) -> Option<PathBuf> {
        if !self.build.binary_path.is_empty() {
            let p = PathBuf::from(&self.build.binary_path);
            if p.is_file() {
                return Some(p);
            }
        }
        let stem = self.dir.file_name()?.to_str()?;
        let fallback = self.dir.join("build").join(stem);
        if fallback.is_file() {
            Some(fallback)
        } else {
            None
        }
    }
}

pub fn run_selftest(dir: &Path, repo_root: &Path) -> i32 {
    let mut model = match ProjectModel::load(dir) {
        Ok(m) => m,
        Err(err) => {
            println!("SELFTEST FAIL: {err}");
            return 1;
        }
    };
    model.load_certified_index(repo_root);
    println!("project:      {}", model.dir.display());
    println!("score:        {}", model.score);
    println!("verification: {}", model.verification);
    println!("stages:       {}", model.stages.len());
    for s in &model.stages {
        println!("  {:<12}{}", s.name, s.status);
    }
    println!("functions:    {}", model.functions.len());
    for f in &model.functions {
        println!("  {}  {}  {}", f.address, f.category, f.symbol);
    }
    println!("certified:    {}", model.certified.len());
    let verified = model
        .functions
        .iter()
        .filter(|f| f.category == "HIGH_LEVEL_VERIFIED" && !f.verified_by.is_empty())
        .count();
    println!("verified-by:  {verified}");
    if !model.build.present {
        println!("build:        no manifest.json");
    } else if model.build.binary_path.is_empty() {
        println!("build:        sources only, no binary recorded");
    } else if model.build.digests_match {
        println!("build:        digests MATCH (binary built from this source)");
    } else {
        println!("build:        DIGESTS DIFFER");
    }
    let frames_suffix = match &model.frames_dir {
        Some(d) => format!(" in {}", d.display()),
        None => " (no screenshots directory)".to_string(),
    };
    println!("frames:       {}{}", model.frames.len(), frames_suffix);
    println!("{}", ToolchainStatus::detect().format_selftest_line());
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn loads_project_model_from_synthetic_dir() {
        let nanos = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let dir = std::env::temp_dir().join(format!("gamerecomp-model-test-{nanos}"));
        fs::create_dir_all(dir.join("reports")).unwrap();
        fs::write(
            dir.join("reports").join("report.json"),
            r#"{"score":93,"outcome":{"status":"partial"},"stages":[{"name":"inspect","status":"ok","data":{"detail":"PE32"}},{"name":"compile","status":"ok","data":{}}]}"#,
        )
        .unwrap();
        fs::write(
            dir.join("manifest.json"),
            r#"{"pe":"game.exe","source_digest_sha256":"abc","source_files":["a.c"],"binary":{"path":"/tmp/game","sha256":"def","size":123,"build_command":"cmake","built_from_source_digest":"abc"}}"#,
        )
        .unwrap();
        let m = ProjectModel::load(&dir).unwrap();
        assert_eq!(m.score, 93);
        assert_eq!(m.verification, "partial");
        assert!(m.build.digests_match);
        assert!(m.diag.build_passed);
        let _ = fs::remove_dir_all(&dir);
    }
}
