use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

pub fn unix_timestamp_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

pub fn log_to_file(log_path: &Path, component: &str, message: &str) {
    if let Some(parent) = log_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(log_path) {
        let ts = unix_timestamp_secs();
        let _ = writeln!(f, "[{ts}] [{component}] {message}");
    }
}

pub fn log_project_event(project_dir: &Path, component: &str, message: &str) {
    let path = project_dir.join("logs").join("orchestrator.log");
    log_to_file(&path, component, message);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_project_log_line() {
        let dir = std::env::temp_dir().join(format!("gamerecomp-log-{}", unix_timestamp_secs()));
        log_project_event(&dir, "orchestrator", "started test");
        let content = fs::read_to_string(dir.join("logs").join("orchestrator.log")).unwrap();
        assert!(content.contains("[orchestrator] started test"));
        let _ = fs::remove_dir_all(&dir);
    }
}
