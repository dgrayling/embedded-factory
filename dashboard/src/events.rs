//! Event contract between the Python orchestrator (writer) and the dashboard (reader).
//!
//! Each run directory holds an append-only `events.jsonl`; one JSON object per line with a
//! `ts` (unix seconds) and a `type` tag. This module is the source of truth for the schema;
//! `factory/events.py` mirrors it and both sides test against
//! `tests/fixtures/events/demo_run.jsonl`.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Usage {
    #[serde(default)]
    pub input_tokens: u64,
    #[serde(default)]
    pub output_tokens: u64,
    #[serde(default)]
    pub cost_usd: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum EventKind {
    RunStarted {
        mode: String,
        board: String,
        title: String,
        #[serde(default)]
        max_iterations: Option<u32>,
    },
    StageStarted {
        stage: String,
        #[serde(default)]
        iteration: u32,
    },
    StageFinished {
        stage: String,
        #[serde(default)]
        iteration: u32,
        ok: bool,
        #[serde(default)]
        summary: String,
        #[serde(default)]
        usage: Option<Usage>,
        #[serde(default)]
        detail: Value,
    },
    GateResult {
        gate: String,
        #[serde(default)]
        iteration: u32,
        ok: bool,
        #[serde(default)]
        summary: String,
        #[serde(default)]
        detail: Value,
    },
    ToolCall {
        stage: String,
        tool: String,
        #[serde(default)]
        args: String,
    },
    Note {
        stage: String,
        text: String,
    },
    ArtifactWritten {
        stage: String,
        path: String,
    },
    RunFinished {
        ok: bool,
        #[serde(default)]
        reason: String,
    },
    /// Per-test recordings of the simulated board (LED, button and console activity in virtual
    /// time), emitted after a Renode gate. The dashboard folds only a count; `/sim` replays the clips.
    SimTrace {
        gate: String,
        #[serde(default)]
        iteration: u32,
        board: String,
        clips: Value,
    },
    /// Forward compatibility: newer writers may emit types this build doesn't know.
    #[serde(other)]
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Event {
    pub ts: f64,
    #[serde(flatten)]
    pub kind: EventKind,
}

/// An event as stored by the dashboard: its position in the run's log plus the payload.
#[derive(Debug, Clone, Serialize)]
pub struct Seq {
    pub seq: u64,
    #[serde(flatten)]
    pub event: Event,
}

pub fn parse_line(line: &str) -> Option<Event> {
    let line = line.trim();
    if line.is_empty() {
        return None;
    }
    serde_json::from_str(line).ok()
}

// ---------------------------------------------------------------------------------------
// Folded run state
// ---------------------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq, Default)]
#[serde(rename_all = "snake_case")]
pub enum NodeStatus {
    #[default]
    Idle,
    Running,
    Pass,
    Fail,
    /// Result from an earlier iteration; superseded once the Firmware Engineer runs again.
    Stale,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct NodeState {
    pub status: NodeStatus,
    /// How many times this node has run (persona sessions or gate evaluations).
    pub runs: u32,
    pub fails: u32,
    pub iteration: u32,
    pub summary: String,
    pub detail: Value,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    Running,
    Passed,
    Failed,
}

#[derive(Debug, Clone, Serialize)]
pub struct RunState {
    pub id: String,
    pub title: String,
    pub mode: String,
    pub board: String,
    pub status: RunStatus,
    pub reason: String,
    pub started_ts: Option<f64>,
    pub last_ts: Option<f64>,
    pub finished_ts: Option<f64>,
    pub iteration: u32,
    pub max_iterations: Option<u32>,
    pub active_stage: Option<String>,
    pub nodes: BTreeMap<String, NodeState>,
    pub usage: Usage,
    pub usage_by_stage: BTreeMap<String, Usage>,
    pub artifacts: Vec<String>,
    pub events: u64,
    /// Number of `sim_trace` events: recordings available for 3D replay.
    pub sim_traces: u32,
}

pub const GATES: [&str; 5] = ["g1", "g2", "g3", "g4", "g5"];

impl RunState {
    pub fn new(id: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            title: String::new(),
            mode: String::new(),
            board: String::new(),
            status: RunStatus::Running,
            reason: String::new(),
            started_ts: None,
            last_ts: None,
            finished_ts: None,
            iteration: 0,
            max_iterations: None,
            active_stage: None,
            nodes: BTreeMap::new(),
            usage: Usage::default(),
            usage_by_stage: BTreeMap::new(),
            artifacts: Vec::new(),
            events: 0,
            sim_traces: 0,
        }
    }

    pub fn apply(&mut self, ev: &Event) {
        self.events += 1;
        self.last_ts = Some(ev.ts);
        match &ev.kind {
            EventKind::RunStarted { mode, board, title, max_iterations } => {
                self.mode = mode.clone();
                self.board = board.clone();
                self.title = title.clone();
                self.max_iterations = *max_iterations;
                self.started_ts = Some(ev.ts);
            }
            EventKind::StageStarted { stage, iteration } => {
                if stage == "firmware_engineer" && *iteration > self.iteration {
                    // New fix iteration: previous gate verdicts no longer describe the code.
                    for g in GATES {
                        if let Some(n) = self.nodes.get_mut(g)
                            && matches!(n.status, NodeStatus::Pass | NodeStatus::Fail) {
                                n.status = NodeStatus::Stale;
                            }
                    }
                }
                self.iteration = self.iteration.max(*iteration);
                let n = self.nodes.entry(stage.clone()).or_default();
                n.status = NodeStatus::Running;
                n.iteration = *iteration;
                self.active_stage = Some(stage.clone());
            }
            EventKind::StageFinished { stage, iteration, ok, summary, usage, detail } => {
                let n = self.nodes.entry(stage.clone()).or_default();
                n.status = if *ok { NodeStatus::Pass } else { NodeStatus::Fail };
                n.runs += 1;
                n.fails += u32::from(!*ok);
                n.iteration = *iteration;
                n.summary = summary.clone();
                if !detail.is_null() {
                    n.detail = detail.clone();
                }
                if let Some(u) = usage {
                    add_usage(&mut self.usage, u);
                    add_usage(self.usage_by_stage.entry(stage.clone()).or_default(), u);
                }
                if self.active_stage.as_deref() == Some(stage) {
                    self.active_stage = None;
                }
            }
            EventKind::GateResult { gate, iteration, ok, summary, detail } => {
                let key = gate.to_ascii_lowercase();
                self.iteration = self.iteration.max(*iteration);
                let n = self.nodes.entry(key.clone()).or_default();
                n.status = if *ok { NodeStatus::Pass } else { NodeStatus::Fail };
                n.runs += 1;
                n.fails += u32::from(!*ok);
                n.iteration = *iteration;
                n.summary = summary.clone();
                n.detail = detail.clone();
                self.active_stage = Some(key);
            }
            EventKind::ArtifactWritten { path, .. } => {
                if !self.artifacts.contains(path) {
                    self.artifacts.push(path.clone());
                }
            }
            EventKind::RunFinished { ok, reason } => {
                self.status = if *ok { RunStatus::Passed } else { RunStatus::Failed };
                self.reason = reason.clone();
                self.finished_ts = Some(ev.ts);
                self.active_stage = None;
            }
            EventKind::SimTrace { .. } => self.sim_traces += 1,
            EventKind::ToolCall { .. } | EventKind::Note { .. } | EventKind::Unknown => {}
        }
    }
}

fn add_usage(acc: &mut Usage, u: &Usage) {
    acc.input_tokens += u.input_tokens;
    acc.output_tokens += u.output_tokens;
    acc.cost_usd += u.cost_usd;
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURE: &str = include_str!("../../tests/fixtures/events/demo_run.jsonl");

    fn fold(src: &str) -> RunState {
        let mut st = RunState::new("demo");
        for line in src.lines() {
            let ev = parse_line(line).unwrap_or_else(|| panic!("fixture line failed to parse: {line}"));
            assert_ne!(ev.kind, EventKind::Unknown, "fixture uses an unknown event type: {line}");
            st.apply(&ev);
        }
        st
    }

    #[test]
    fn fixture_folds_to_passed_run() {
        let st = fold(FIXTURE);
        assert_eq!(st.status, RunStatus::Passed);
        assert_eq!(st.iteration, 3);
        assert_eq!(st.board, "stm32f4_disco");
        assert_eq!(st.nodes["g1"].runs, 3);
        assert_eq!(st.nodes["g1"].fails, 1);
        assert_eq!(st.nodes["g5"].fails, 1);
        assert_eq!(st.nodes["g5"].status, NodeStatus::Pass);
        assert_eq!(st.nodes["debugger"].runs, 2);
        assert!(st.artifacts.iter().any(|a| a.ends_with("report.md")));
        assert!((st.usage.cost_usd - 0.473).abs() < 1e-9, "cost was {}", st.usage.cost_usd);
        assert_eq!(st.usage_by_stage["firmware_engineer"].input_tokens, 8200 + 9100 + 9800);
        assert!(st.active_stage.is_none());
        assert_eq!(st.sim_traces, 2);
    }

    #[test]
    fn new_fw_iteration_marks_gates_stale() {
        let mut st = RunState::new("x");
        for l in [
            r#"{"ts":1,"type":"stage_started","stage":"firmware_engineer","iteration":1}"#,
            r#"{"ts":2,"type":"gate_result","gate":"G1","iteration":1,"ok":false,"summary":"err"}"#,
            r#"{"ts":3,"type":"stage_started","stage":"firmware_engineer","iteration":2}"#,
        ] {
            st.apply(&parse_line(l).unwrap());
        }
        assert_eq!(st.nodes["g1"].status, NodeStatus::Stale);
        assert_eq!(st.iteration, 2);
    }

    #[test]
    fn unknown_types_and_garbage_are_tolerated() {
        assert_eq!(parse_line(r#"{"ts":1,"type":"future_thing","x":1}"#).unwrap().kind, EventKind::Unknown);
        assert!(parse_line("{not json").is_none());
        assert!(parse_line("   ").is_none());
    }
}
