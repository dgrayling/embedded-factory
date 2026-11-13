//! Replays the recorded demo run into a runs directory in (scaled) real time, so the
//! dashboard can be exercised before the real pipeline exists.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::Value;

const DEMO: &str = include_str!("../../tests/fixtures/events/demo_run.jsonl");

pub fn new_demo_dir(runs_root: &Path) -> std::io::Result<PathBuf> {
    let stamp = chrono_like_stamp();
    let dir = runs_root.join(format!("demo-{stamp}"));
    std::fs::create_dir_all(&dir)?;
    Ok(dir)
}

/// Writes each fixture event with `ts` rebased to now, sleeping `delta / speed` between lines.
pub async fn replay(dir: PathBuf, speed: f64) -> anyhow::Result<()> {
    let speed = if speed > 0.0 { speed } else { 1.0 };
    let path = dir.join(crate::store::EVENTS_FILE);
    let mut file = std::fs::OpenOptions::new().create(true).append(true).open(&path)?;
    let start = now();
    let mut prev_ts = 0.0;
    for line in DEMO.lines().filter(|l| !l.trim().is_empty()) {
        let mut v: Value = serde_json::from_str(line)?;
        let ts = v["ts"].as_f64().unwrap_or(prev_ts);
        let wait = ((ts - prev_ts) / speed).max(0.0);
        prev_ts = ts;
        tokio::time::sleep(Duration::from_secs_f64(wait)).await;
        v["ts"] = Value::from(start + ts / speed);
        writeln!(file, "{}", serde_json::to_string(&v)?)?;
        file.flush()?;
    }
    tracing::info!("demo replay finished: {}", path.display());
    Ok(())
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn chrono_like_stamp() -> String {
    // Seconds since epoch is unique enough for demo dirs and sorts correctly.
    format!("{}", now() as u64)
}
