use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::config::ToolchainStatus;

#[derive(Debug, Clone, Default)]
pub struct Stage {
    pub name: String,
    pub status: String,
    pub detail: String,
}

#[allow(dead_code)]
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
    pub size: u64,
    pub blocks: u64,
    pub instructions: u64,
    pub calls: u64,
    pub is_thunk: bool,
    pub is_certified: bool,
    pub is_high_level: bool,
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

#[allow(dead_code)]
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
pub struct InspectInfo {
    pub present: bool,
    pub pe_type: String,
    pub machine: String,
    pub subsystem: String,
    pub image_base: String,
    pub entry_point: String,
    pub linker_version: String,
    pub file_size: u64,
    pub sha256: String,
    pub import_dlls: Vec<String>,
    pub import_count: usize,
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

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ProjectUiMeta {
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub starred: Vec<String>,
    #[serde(default)]
    pub notes: BTreeMap<String, Vec<UserNote>>,
    #[serde(default)]
    pub agent_instructions: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserNote {
    pub author: String,
    pub timestamp: String,
    pub text: String,
}

#[allow(dead_code)]
#[derive(Debug, Clone)]
pub struct ProjectSummary {
    pub dir: PathBuf,
    pub name: String,
    pub pe_name: String,
    pub target_label: String,
    pub score: i64,
    pub verification: String,
    pub total_functions: usize,
    pub verified_functions: usize,
    pub has_wasm: bool,
    pub has_binary: bool,
    pub build_passed: bool,
    pub load_ok: bool,
    pub color_idx: usize,
}

#[derive(Debug, Clone, Default)]
pub struct ProjectModel {
    pub dir: PathBuf,
    pub name: String,
    pub original_exe: String,
    pub runtime: String,
    pub target_os: String,
    pub target_arch: String,
    pub pe_path: String,
    pub score: i64,
    pub verification: String,
    pub stages: Vec<Stage>,
    /// High-level / provenance-tracked functions from project.json (preserves --selftest contract).
    pub functions: Vec<FunctionRow>,
    /// Complete function inventory (recovered functions from reports/functions.json overlaid with high_level & certified).
    pub all_functions: Vec<FunctionRow>,
    pub total_instructions: u64,
    pub total_thunks: usize,
    pub lift_errors: usize,
    pub certified: Vec<CertifiedRow>,
    pub build: BuildInfo,
    pub inspect: InspectInfo,
    pub frames: Vec<PathBuf>,
    pub frames_dir: Option<PathBuf>,
    pub has_html_report: bool,
    pub has_wasm_build: bool,
    pub diag: ReportDiagnostics,
    pub ui_meta: ProjectUiMeta,
}

fn read_json_value(path: &Path) -> Option<Value> {
    let text = fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

impl ProjectModel {
    pub fn load(dir: &Path) -> Result<Self, String> {
        if !dir.is_dir() {
            return Err(format!("{} is not a directory", dir.display()));
        }

        let report_path = dir.join("reports").join("report.json");
        let report = read_json_value(&report_path).ok_or_else(|| {
            "no reports/report.json -- run the pipeline over this directory first".to_string()
        })?;

        let folder_name = dir
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("project")
            .to_string();

        let mut model = Self {
            dir: dir.to_path_buf(),
            name: folder_name,
            runtime: "win32hle".to_string(),
            target_os: "host".to_string(),
            target_arch: "host".to_string(),
            score: report.get("score").and_then(|v| v.as_i64()).unwrap_or(0),
            verification: report
                .get("outcome")
                .and_then(|o| o.get("status"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            has_html_report: dir.join("reports").join("report.html").is_file(),
            has_wasm_build: dir.join("build").join("index.html").is_file()
                || dir.join("index.html").is_file(),
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
                                p.extension()
                                    .and_then(|x| x.to_str())
                                    .map(|s| s.to_ascii_lowercase())
                                    .as_deref(),
                                Some("png") | Some("bmp")
                            )
                    })
                    .collect();
                paths.sort();
                model.frames = paths;
            }
        }

        // Load manifest.json.
        if let Some(manifest) = read_json_value(&dir.join("manifest.json")) {
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
            if let Some(bin) = manifest.get("binary").filter(|v| v.is_object()) {
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
                model.build.binary_size = bin.get("size").and_then(|v| v.as_u64()).unwrap_or(0);
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

        // Load reports/inspect.json.
        if let Some(insp) = read_json_value(&dir.join("reports").join("inspect.json")) {
            model.inspect.present = true;
            model.inspect.pe_type = insp
                .get("pe_type")
                .and_then(|v| v.as_str())
                .unwrap_or("PE32")
                .to_string();
            model.inspect.machine = insp
                .get("machine")
                .and_then(|v| v.as_str())
                .unwrap_or("i386")
                .to_string();
            model.inspect.subsystem = insp
                .get("subsystem")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            model.inspect.image_base = insp
                .get("image_base")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            model.inspect.entry_point = insp
                .get("entry_point")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            model.inspect.linker_version = insp
                .get("linker_version")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            model.inspect.file_size = insp.get("size").and_then(|v| v.as_u64()).unwrap_or(0);
            model.inspect.sha256 = insp
                .get("sha256")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            model.inspect.import_count = insp
                .get("import_count")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;
            if let Some(dlls) = insp.get("import_dlls").and_then(|v| v.as_array()) {
                let mut uniq = Vec::new();
                for d in dlls {
                    if let Some(s) = d.as_str() {
                        if !uniq.iter().any(|x: &String| x.eq_ignore_ascii_case(s)) {
                            uniq.push(s.to_string());
                        }
                    }
                }
                model.inspect.import_dlls = uniq;
            }
        }

        // Load project.json.
        if let Some(project) = read_json_value(&dir.join("project.json")) {
            if let Some(n) = project.get("name").and_then(|v| v.as_str()) {
                if !n.is_empty() {
                    model.name = n.to_string();
                }
            }
            if let Some(o) = project.get("original_executable").and_then(|v| v.as_str()) {
                model.original_exe = o.to_string();
            }
            if let Some(r) = project.get("runtime").and_then(|v| v.as_str()) {
                model.runtime = r.to_string();
            }
            if let Some(t) = project.get("target") {
                if let Some(os) = t.get("os").and_then(|v| v.as_str()) {
                    model.target_os = os.to_string();
                }
                if let Some(arch) = t.get("arch").and_then(|v| v.as_str()) {
                    model.target_arch = arch.to_string();
                }
            }
            if let Some(pe_obj) = project.get("pe") {
                if model.pe_path.is_empty() {
                    model.pe_path = pe_obj
                        .get("path")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                }
                if !model.inspect.present {
                    model.inspect.pe_type = pe_obj
                        .get("pe_type")
                        .and_then(|v| v.as_str())
                        .unwrap_or("PE32")
                        .to_string();
                    model.inspect.machine = pe_obj
                        .get("machine")
                        .and_then(|v| v.as_str())
                        .unwrap_or("i386")
                        .to_string();
                    model.inspect.image_base = pe_obj
                        .get("image_base")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    model.inspect.entry_point = pe_obj
                        .get("entry_point")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                }
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
                        is_high_level: true,
                        ..Default::default()
                    });
                }
            }
        }

        // Load full recovered function inventory from reports/functions.json for the Function Map!
        let mut hl_by_addr: HashMap<String, FunctionRow> = HashMap::new();
        for f in &model.functions {
            hl_by_addr.insert(f.address.to_ascii_lowercase(), f.clone());
        }

        if let Some(fj) = read_json_value(&dir.join("reports").join("functions.json")) {
            model.total_instructions = fj.get("instructions").and_then(|v| v.as_u64()).unwrap_or(0);
            model.total_thunks = fj.get("thunks").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
            model.lift_errors = fj
                .get("lift_errors")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;
            if let Some(arr) = fj.get("functions").and_then(|v| v.as_array()) {
                let mut seen = HashSet::new();
                for f in arr {
                    let address = f.get("address").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    let key = address.to_ascii_lowercase();
                    seen.insert(key.clone());

                    let name = f.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    let size = f.get("size").and_then(|v| v.as_u64()).unwrap_or(0);
                    let blocks = f.get("blocks").and_then(|v| v.as_u64()).unwrap_or(0);
                    let instructions = f.get("instructions").and_then(|v| v.as_u64()).unwrap_or(0);
                    let calls = f.get("calls").and_then(|v| v.as_u64()).unwrap_or(0);
                    let is_thunk = f.get("is_thunk").and_then(|v| v.as_bool()).unwrap_or(false);
                    let source_unit = f
                        .get("source_unit")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let category = f
                        .get("category")
                        .and_then(|v| v.as_str())
                        .unwrap_or("LOW_LEVEL_FALLBACK")
                        .to_string();

                    if let Some(mut hl) = hl_by_addr.remove(&key) {
                        hl.size = size;
                        hl.blocks = blocks;
                        hl.instructions = instructions;
                        hl.calls = calls;
                        hl.is_thunk = is_thunk;
                        if hl.source_path.is_empty() && !source_unit.is_empty() {
                            hl.source_path = source_unit;
                        }
                        model.all_functions.push(hl);
                    } else {
                        let file = Path::new(&source_unit)
                            .file_name()
                            .and_then(|s| s.to_str())
                            .unwrap_or("")
                            .to_string();
                        model.all_functions.push(FunctionRow {
                            address,
                            symbol: name,
                            category,
                            file,
                            source_path: source_unit,
                            size,
                            blocks,
                            instructions,
                            calls,
                            is_thunk,
                            ..Default::default()
                        });
                    }
                }
                for (_, leftover) in hl_by_addr {
                    if !seen.contains(&leftover.address.to_ascii_lowercase()) {
                        model.all_functions.push(leftover);
                    }
                }
            }
        }

        if model.all_functions.is_empty() {
            model.all_functions = model.functions.clone();
        }

        // Load UI sidecar metadata (custom display name, starred functions, user notes).
        if let Ok(raw) = fs::read_to_string(dir.join("gamerecomp_ui.json")) {
            if let Ok(meta) = serde_json::from_str::<ProjectUiMeta>(&raw) {
                model.ui_meta = meta;
            }
        }

        Ok(model)
    }

    pub fn save_ui_meta(&self) -> Result<(), String> {
        let body = serde_json::to_string_pretty(&self.ui_meta).map_err(|e| e.to_string())?;
        fs::write(self.dir.join("gamerecomp_ui.json"), body).map_err(|e| e.to_string())
    }

    pub fn display_title(&self) -> &str {
        if !self.ui_meta.display_name.trim().is_empty() {
            self.ui_meta.display_name.trim()
        } else {
            &self.name
        }
    }

    pub fn load_certified_index(&mut self, repo_root: &Path) {
        self.certified.clear();
        let idx_path = repo_root.join("reports").join("certified").join("index.json");
        let Some(idx) = read_json_value(&idx_path) else {
            return;
        };
        let Some(rows) = idx.get("rows").and_then(|v| v.as_array()) else {
            return;
        };
        let mut cert_addrs = HashSet::new();
        for r in rows {
            let addr = r.get("address").and_then(|v| v.as_str()).unwrap_or("").to_string();
            cert_addrs.insert(addr.to_ascii_lowercase());
            self.certified.push(CertifiedRow {
                address: addr,
                verdict: r.get("verdict").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                compares: r.get("compares").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                named_memory: r
                    .get("named_memory")
                    .and_then(|v| v.as_array())
                    .map(|a| a.len())
                    .unwrap_or(0),
                cases: r.get("cases").and_then(|v| v.as_i64()).unwrap_or(0),
                module: r.get("module").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                verdict_json: r
                    .get("verdict_json")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            });
        }
        // Mark matching functions as certified only if project has high-level functions matching these addresses.
        let is_cavestory = self
            .name
            .to_ascii_lowercase()
            .contains("doukutsu")
            || self.name.to_ascii_lowercase().contains("cavestory");
        for f in &mut self.all_functions {
            if f.category == "HIGH_LEVEL_VERIFIED"
                || (is_cavestory && cert_addrs.contains(&f.address.to_ascii_lowercase()))
            {
                f.is_certified = true;
            }
        }
    }

    pub fn built_binary_path(&self) -> Option<PathBuf> {
        if !self.build.binary_path.is_empty() {
            let p = PathBuf::from(&self.build.binary_path);
            if p.is_file() {
                return Some(p);
            }
            let rel = self.dir.join(&self.build.binary_path);
            if rel.is_file() {
                return Some(rel);
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

    pub fn load_function_source(&self, row: &FunctionRow) -> (String, Option<PathBuf>) {
        if row.source_path.is_empty() {
            return (
                format!(
                    "// No generated C source file recorded for {} ({}).\n// Run the pipeline with --out-mode full or check src/recomp/gen/.",
                    row.address, row.symbol
                ),
                None,
            );
        }

        let full = self.dir.join(&row.source_path);
        let Ok(content) = fs::read_to_string(&full) else {
            return (
                format!(
                    "// Source file {} is not present on disk (may have been cleaned by --clean-objects).\n// Re-run with --stages translate,project to regenerate C units.",
                    row.source_path
                ),
                Some(full),
            );
        };

        // If it's a standalone high-level file (src/highlevel/<addr>.c), return the full file.
        if row.source_path.starts_with("src/highlevel/") || content.len() < 12_000 {
            return (content, Some(full));
        }

        // Otherwise extract the exact function body from the combined recomp_XXXX.c translation unit.
        let needle_sym = format!("{}(", row.symbol);
        let addr_clean = row.address.trim_start_matches("0x").trim_start_matches("0X");
        let needle_sub = format!("sub_{addr_clean}(");

        let lines: Vec<&str> = content.lines().collect();
        let start_idx = lines.iter().position(|l| {
            (!l.starts_with(' ') && !l.starts_with('\t'))
                && (l.contains(&needle_sym) || l.contains(&needle_sub))
                && !l.trim_end().ends_with(';')
        });

        if let Some(start) = start_idx {
            let begin = start.saturating_sub(2);
            let mut depth = 0i32;
            let mut opened = false;
            let mut end = lines.len().min(start + 220);
            for (i, line) in lines.iter().enumerate().skip(start) {
                for ch in line.chars() {
                    if ch == '{' {
                        depth += 1;
                        opened = true;
                    } else if ch == '}' {
                        depth -= 1;
                    }
                }
                if opened && depth <= 0 {
                    end = i + 1;
                    break;
                }
            }
            let snippet = lines[begin..end].join("\n");
            return (snippet, Some(full));
        }

        let preview: String = lines.into_iter().take(160).collect::<Vec<_>>().join("\n");
        (preview, Some(full))
    }

    pub fn stage_ok_count(&self) -> usize {
        self.stages.iter().filter(|s| s.status == "ok").count()
    }

    pub fn verified_count(&self) -> usize {
        self.all_functions
            .iter()
            .filter(|f| f.category == "HIGH_LEVEL_VERIFIED" || f.is_certified)
            .count()
    }

    pub fn high_level_count(&self) -> usize {
        self.all_functions
            .iter()
            .filter(|f| f.category.starts_with("HIGH_LEVEL_"))
            .count()
    }

    pub fn unresolved_count(&self) -> usize {
        self.all_functions
            .iter()
            .filter(|f| f.category == "UNRESOLVED")
            .count()
    }
}

pub fn discover_projects(projects_root: &Path, extra_dirs: &[PathBuf]) -> Vec<ProjectSummary> {
    let mut dirs = Vec::new();
    let mut seen = HashSet::new();

    if let Ok(rd) = fs::read_dir(projects_root) {
        let mut children: Vec<PathBuf> = rd
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect();
        children.sort();
        for c in children {
            let key = fs::canonicalize(&c).unwrap_or_else(|_| c.clone());
            if seen.insert(key) {
                dirs.push(c);
            }
        }
    }

    for extra in extra_dirs {
        if extra.is_dir() {
            let key = fs::canonicalize(extra).unwrap_or_else(|_| extra.clone());
            if seen.insert(key) {
                dirs.push(extra.clone());
            }
        }
    }

    dirs.into_iter()
        .enumerate()
        .map(|(idx, dir)| {
            let fallback_name = dir
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("project")
                .to_string();
            match ProjectModel::load(&dir) {
                Ok(m) => {
                    let target_label = if m.has_wasm_build || m.target_arch == "wasm32" {
                        "WASM32".to_string()
                    } else {
                        "PE32".to_string()
                    };
                    let pe_name = if !m.original_exe.is_empty() {
                        m.original_exe.clone()
                    } else {
                        Path::new(&m.pe_path)
                            .file_name()
                            .and_then(|s| s.to_str())
                            .unwrap_or("")
                            .to_string()
                    };
                    ProjectSummary {
                        dir: m.dir.clone(),
                        name: m.display_title().to_string(),
                        pe_name,
                        target_label,
                        score: m.score,
                        verification: m.verification.clone(),
                        total_functions: m.all_functions.len(),
                        verified_functions: m.verified_count(),
                        has_wasm: m.has_wasm_build,
                        has_binary: m.built_binary_path().is_some(),
                        build_passed: m.diag.build_passed,
                        load_ok: true,
                        color_idx: idx % 6,
                    }
                }
                Err(_) => ProjectSummary {
                    dir,
                    name: fallback_name,
                    pe_name: String::new(),
                    target_label: "UNBUILT".to_string(),
                    score: 0,
                    verification: "unbuilt".to_string(),
                    total_functions: 0,
                    verified_functions: 0,
                    has_wasm: false,
                    has_binary: false,
                    build_passed: false,
                    load_ok: false,
                    color_idx: idx % 6,
                },
            }
        })
        .collect()
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
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("gamerecomp-model-test-{nanos}"));
        fs::create_dir_all(dir.join("reports")).unwrap();
        fs::write(
            dir.join("reports").join("report.json"),
            r#"{"score":93,"outcome":{"status":"partial"},"stages":[{"name":"inspect","status":"ok","data":{"detail":"PE32"}},{"name":"compile","status":"ok","data":{}}]}"#,
        )
        .unwrap();
        fs::write(
            dir.join("reports").join("functions.json"),
            r#"{"count":2,"thunks":1,"instructions":42,"lift_errors":0,"functions":[{"address":"0x00401000","name":"sub_00401000","size":32,"blocks":2,"instructions":10,"is_thunk":false,"calls":1,"source_unit":"src/recomp/gen/recomp_0000.c","category":"LOW_LEVEL_FALLBACK"},{"address":"0x00401040","name":"sub_00401040","size":6,"blocks":1,"instructions":1,"is_thunk":true,"calls":0,"source_unit":"src/recomp/gen/recomp_0000.c","category":"LOW_LEVEL_FALLBACK"}]}"#,
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
        assert_eq!(m.all_functions.len(), 2);
        assert_eq!(m.total_instructions, 42);
        assert_eq!(m.total_thunks, 1);
        let _ = fs::remove_dir_all(&dir);
    }
}
