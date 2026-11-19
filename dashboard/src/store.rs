//! Watches a runs directory and keeps a folded state per run.
//!
//! Polling (rather than OS file notifications) keeps this portable and robust against the
//! orchestrator writing from inside Docker bind mounts, where fs events are unreliable.

use std::collections::HashMap;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use tokio::sync::{RwLock, broadcast};

use crate::events::{RunState, RunStatus, Seq, parse_line};
use crate::overview::{self, Activity, Overview, RunView};

pub const EVENTS_FILE: &str = "events.jsonl";

#[derive(Debug, Clone)]
pub struct Update {
    pub run_id: String,
    pub event: Seq,
}

struct Entry {
    offset: u64,
    partial: String,
    events: Vec<Seq>,
    state: RunState,
    activity: Activity,
    skipped_lines: u64,
}

struct Appended {
    id: String,
    new_offset: u64,
    chunk: String,
    truncated: bool,
}

impl Entry {
    fn new(id: &str) -> Self {
        Self { offset: 0, partial: String::new(), events: Vec::new(), state: RunState::new(id), activity: Activity::default(), skipped_lines: 0 }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct RunSummary {
    pub id: String,
    pub title: String,
    pub board: String,
    pub mode: String,
    pub status: RunStatus,
    pub iteration: u32,
    pub max_iterations: Option<u32>,
    pub active_stage: Option<String>,
    pub started_ts: Option<f64>,
    pub last_ts: Option<f64>,
    pub cost_usd: f64,
    pub sim_traces: u32,
}

#[derive(Debug, Clone, Serialize)]
pub struct Snapshot {
    pub state: RunState,
    pub events: Vec<Seq>,
    pub skipped_lines: u64,
}

#[derive(Clone)]
pub struct Store {
    root: PathBuf,
    runs: Arc<RwLock<HashMap<String, Entry>>>,
    tx: broadcast::Sender<Update>,
}

impl Store {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        let (tx, _) = broadcast::channel(4096);
        Self { root: root.into(), runs: Arc::default(), tx }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn subscribe(&self) -> broadcast::Receiver<Update> {
        self.tx.subscribe()
    }

    pub async fn summaries(&self) -> Vec<RunSummary> {
        let runs = self.runs.read().await;
        let mut out: Vec<RunSummary> = runs
            .values()
            .map(|e| {
                let s = &e.state;
                RunSummary {
                    id: s.id.clone(),
                    title: s.title.clone(),
                    board: s.board.clone(),
                    mode: s.mode.clone(),
                    status: s.status,
                    iteration: s.iteration,
                    max_iterations: s.max_iterations,
                    active_stage: s.active_stage.clone(),
                    started_ts: s.started_ts,
                    last_ts: s.last_ts,
                    cost_usd: s.usage.cost_usd,
                    sim_traces: s.sim_traces,
                }
            })
            .collect();
        out.sort_by(|a, b| b.started_ts.partial_cmp(&a.started_ts).unwrap_or(std::cmp::Ordering::Equal));
        out
    }

    pub async fn snapshot(&self, id: &str) -> Option<Snapshot> {
        let runs = self.runs.read().await;
        runs.get(id).map(|e| Snapshot { state: e.state.clone(), events: e.events.clone(), skipped_lines: e.skipped_lines })
    }

    /// Factory-wide overview at `now` (unix seconds).
    pub async fn overview(&self, boards: &serde_json::Value, now: f64, stall_after_s: f64) -> Overview {
        let runs = self.runs.read().await;
        let views: Vec<RunView> =
            runs.values().map(|e| RunView { state: &e.state, activity: &e.activity, events: &e.events }).collect();
        overview::build(&views, boards, now, stall_after_s)
    }

    /// Only the folded state of one run (no event history): what the UI refreshes after live events.
    pub async fn state(&self, id: &str) -> Option<RunState> {
        self.runs.read().await.get(id).map(|e| e.state.clone())
    }

    /// Scan once: discover run dirs and ingest any bytes appended since the last scan.
    /// All filesystem work happens in a single blocking task, and the write lock is taken
    /// only when something was appended.
    pub async fn scan(&self) -> anyhow::Result<()> {
        let root = self.root.clone();
        let offsets: HashMap<String, u64> =
            self.runs.read().await.iter().map(|(id, e)| (id.clone(), e.offset)).collect();
        let reads = tokio::task::spawn_blocking(move || -> std::io::Result<Vec<Appended>> {
            let mut out = Vec::new();
            for (id, path) in list_run_dirs(&root)? {
                let offset = offsets.get(&id).copied().unwrap_or(0);
                match read_from(&path, offset) {
                    Ok(Some((new_offset, chunk, truncated))) => out.push(Appended { id, new_offset, chunk, truncated }),
                    Ok(None) => {}
                    // One unreadable run (e.g. deleted mid-scan) must not stop the others from updating.
                    Err(e) => tracing::debug!("skipping {}: {e}", path.display()),
                }
            }
            Ok(out)
        })
        .await??;
        if reads.is_empty() {
            return Ok(());
        }

        let mut runs = self.runs.write().await;
        for Appended { id, new_offset, chunk, truncated } in reads {
            let entry = runs.entry(id.clone()).or_insert_with(|| Entry::new(&id));
            if truncated {
                *entry = Entry::new(&id);
            }
            let mut buf = std::mem::take(&mut entry.partial);
            buf.push_str(&chunk);
            entry.offset = new_offset;

            // Keep an incomplete trailing line for the next scan.
            let complete_upto = buf.rfind('\n').map(|i| i + 1).unwrap_or(0);
            entry.partial = buf[complete_upto..].to_string();
            for line in buf[..complete_upto].lines() {
                if line.trim().is_empty() {
                    continue;
                }
                match parse_line(line) {
                    Some(ev) => {
                        entry.state.apply(&ev);
                        entry.activity.apply(&ev);
                        let seq = Seq { seq: entry.events.len() as u64, event: ev };
                        entry.events.push(seq.clone());
                        let _ = self.tx.send(Update { run_id: id.clone(), event: seq });
                    }
                    None => entry.skipped_lines += 1,
                }
            }
        }
        Ok(())
    }

    pub fn spawn_poller(self, every: Duration) -> tokio::task::JoinHandle<()> {
        tokio::spawn(async move {
            let mut tick = tokio::time::interval(every);
            loop {
                tick.tick().await;
                if let Err(e) = self.scan().await {
                    tracing::warn!("scan failed: {e:#}");
                }
            }
        })
    }
}

fn list_run_dirs(root: &Path) -> std::io::Result<Vec<(String, PathBuf)>> {
    let mut out = Vec::new();
    let rd = match std::fs::read_dir(root) {
        Ok(rd) => rd,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(out),
        Err(e) => return Err(e),
    };
    for ent in rd.flatten() {
        let p = ent.path().join(EVENTS_FILE);
        if p.is_file()
            && let Some(name) = ent.file_name().to_str() {
                out.push((name.to_string(), p));
            }
    }
    Ok(out)
}

/// Returns (new_offset, appended_text, truncated) or None if nothing changed.
fn read_from(path: &Path, offset: u64) -> std::io::Result<Option<(u64, String, bool)>> {
    let len = std::fs::metadata(path)?.len();
    if len == offset {
        return Ok(None);
    }
    let (start, truncated) = if len < offset { (0, true) } else { (offset, false) };
    let mut f = std::fs::File::open(path)?;
    f.seek(SeekFrom::Start(start))?;
    let mut bytes = Vec::with_capacity((len - start) as usize);
    f.take(len - start).read_to_end(&mut bytes)?;
    let valid = match std::str::from_utf8(&bytes) {
        Ok(_) => bytes.len(),
        // Incomplete character at the end: the writer is mid-write, so re-read it next time.
        Err(e) if e.error_len().is_none() => e.valid_up_to(),
        // Invalid bytes elsewhere: decode lossily. Holding them back would stall this run forever.
        Err(_) => bytes.len(),
    };
    let text = String::from_utf8_lossy(&bytes[..valid]).into_owned();
    Ok(Some((start + valid as u64, text, truncated)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[tokio::test]
    async fn tails_appends_and_partial_lines() {
        let dir = tempfile::tempdir().unwrap();
        let run = dir.path().join("r1");
        std::fs::create_dir(&run).unwrap();
        let path = run.join(EVENTS_FILE);
        let store = Store::new(dir.path());
        let mut rx = store.subscribe();

        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(f, r#"{{"ts":1,"type":"run_started","mode":"new","board":"stm32f4_disco","title":"t"}}"#).unwrap();
        write!(f, r#"{{"ts":2,"type":"stage_started","sta"#).unwrap(); // half a line
        f.flush().unwrap();
        store.scan().await.unwrap();

        let snap = store.snapshot("r1").await.unwrap();
        assert_eq!(snap.events.len(), 1);
        assert_eq!(rx.recv().await.unwrap().event.seq, 0);

        writeln!(f, r#"ge":"analyst","iteration":0}}"#).unwrap();
        writeln!(f, "garbage").unwrap();
        f.flush().unwrap();
        store.scan().await.unwrap();

        let snap = store.snapshot("r1").await.unwrap();
        assert_eq!(snap.events.len(), 2);
        assert_eq!(snap.skipped_lines, 1);
        assert_eq!(snap.state.active_stage.as_deref(), Some("analyst"));
        assert_eq!(store.summaries().await[0].board, "stm32f4_disco");
    }

    #[tokio::test]
    async fn invalid_utf8_mid_file_does_not_stall_tailing() {
        let dir = tempfile::tempdir().unwrap();
        let run = dir.path().join("r1");
        std::fs::create_dir(&run).unwrap();
        let mut f = std::fs::File::create(run.join(EVENTS_FILE)).unwrap();
        f.write_all(b"\xff\xfe garbage\n").unwrap();
        writeln!(f, r#"{{"ts":1,"type":"run_started","mode":"new","board":"b","title":"t"}}"#).unwrap();
        f.flush().unwrap();
        let store = Store::new(dir.path());
        store.scan().await.unwrap();
        let snap = store.snapshot("r1").await.unwrap();
        assert_eq!((snap.events.len(), snap.skipped_lines), (1, 1));
        assert_eq!(store.state("r1").await.unwrap().board, "b");

        // A multi-byte character split across two writes is held back, not mangled.
        let note = r#"{"ts":2,"type":"note","stage":"analyst","text":"µ"}"#.as_bytes();
        let cut = note.iter().position(|&b| b == 0xC2).unwrap() + 1;
        f.write_all(&note[..cut]).unwrap();
        f.flush().unwrap();
        store.scan().await.unwrap();
        f.write_all(&note[cut..]).unwrap();
        f.write_all(b"\n").unwrap();
        f.flush().unwrap();
        store.scan().await.unwrap();
        let snap = store.snapshot("r1").await.unwrap();
        assert_eq!(snap.events.len(), 2);
        assert!(matches!(&snap.events[1].event.kind, crate::events::EventKind::Note { text, .. } if text == "µ"));
    }

    #[tokio::test]
    async fn scan_feeds_the_overview_activity_fold() {
        let dir = tempfile::tempdir().unwrap();
        let run = dir.path().join("r1");
        std::fs::create_dir(&run).unwrap();
        std::fs::write(
            run.join(EVENTS_FILE),
            concat!(
                r#"{"ts":100,"type":"run_started","mode":"new","board":"stm32f4_disco","title":"t"}"#, "\n",
                r#"{"ts":101,"type":"stage_started","stage":"analyst","iteration":0}"#, "\n",
                r#"{"ts":102,"type":"tool_call","stage":"analyst","tool":"Read","args":"spec.md"}"#, "\n",
            ),
        )
        .unwrap();
        let store = Store::new(dir.path());
        store.scan().await.unwrap();
        let o = store.overview(&serde_json::Value::Null, 110.0, 600.0).await;
        let r = &o.active[0];
        assert_eq!(r.stage_since, Some(101.0));
        assert_eq!(r.tool_calls, 1);
        assert_eq!(r.last_action.as_ref().map(|a| a.text.as_str()), Some("Read(spec.md)"));
    }

    #[tokio::test]
    async fn missing_root_is_empty_not_error() {
        let store = Store::new("/definitely/not/here");
        store.scan().await.unwrap();
        assert!(store.summaries().await.is_empty());
    }
}
