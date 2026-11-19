//! Factory-wide overview: what every run is doing right now, plus aggregate health.
//!
//! Two pieces:
//! - [`Activity`] is a second per-run fold, kept next to [`RunState`] in the store. It tracks
//!   things the per-run inspector doesn't need: when the current stage started, the latest
//!   action (tool call, note, verdict), and wall time per persona.
//! - [`build`] turns all runs into one [`Overview`] payload. It's computed on request, which
//!   is cheap: everything is already folded and in memory.

use std::collections::BTreeMap;

use serde::Serialize;
use serde_json::Value;

use crate::events::{Event, EventKind, GATES, NodeStatus, RunState, RunStatus, Seq};

/// Pipeline order, used to sort persona rows and to draw the per-run progress strip.
pub const STAGE_ORDER: [&str; 10] = [
    "archaeologist", "analyst", "architect", "test_engineer", "lock_tests",
    "firmware_engineer", "debugger", "test_auditor", "reviewer", "integrator",
];

const TEXT_MAX: usize = 180;
const FEED_PER_RUN: usize = 40;
const FEED_MAX: usize = 80;
const RECENT_MAX: usize = 15;
const DAY: f64 = 86_400.0;

// ---------------------------------------------------------------------------------------
// Per-run activity fold
// ---------------------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct Action {
    pub ts: f64,
    /// Event type (`tool_call`, `note`, `gate_result`, ...).
    pub kind: String,
    /// Stage or gate key; empty for run-level events.
    pub stage: String,
    pub text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ok: Option<bool>,
}

#[derive(Debug, Clone, Default)]
pub struct Activity {
    /// When the currently active stage (or most recent gate verdict) began.
    pub stage_since: Option<f64>,
    pub last_action: Option<Action>,
    /// Wall-clock seconds per stage, summed over finished sessions.
    pub stage_secs: BTreeMap<String, f64>,
    open: BTreeMap<String, f64>,
    /// Most recently finished stage; tells "gates running" apart from other gaps between stages.
    pub last_stage: Option<String>,
    pub tool_calls: u32,
    /// Verdict of each gate's first evaluation in this run.
    pub gate_first: BTreeMap<String, bool>,
}

impl Activity {
    pub fn apply(&mut self, ev: &Event) {
        if let Some(a) = action(ev) {
            self.last_action = Some(a);
        }
        match &ev.kind {
            EventKind::StageStarted { stage, .. } => {
                self.open.insert(stage.clone(), ev.ts);
                self.stage_since = Some(ev.ts);
            }
            EventKind::StageFinished { stage, .. } => {
                if let Some(t0) = self.open.remove(stage) {
                    *self.stage_secs.entry(stage.clone()).or_default() += (ev.ts - t0).max(0.0);
                }
                self.last_stage = Some(stage.clone());
                self.stage_since = Some(ev.ts);
            }
            EventKind::GateResult { gate, ok, .. } => {
                self.gate_first.entry(gate.to_ascii_lowercase()).or_insert(*ok);
                self.stage_since = Some(ev.ts);
            }
            EventKind::ToolCall { .. } => self.tool_calls += 1,
            EventKind::RunFinished { .. } => {
                self.open.clear();
                self.stage_since = None;
            }
            _ => {}
        }
    }
}

/// One-line description of an event, as shown in the feed and as a run's "last action".
pub fn action(ev: &Event) -> Option<Action> {
    let (stage, text, ok) = match &ev.kind {
        EventKind::RunStarted { title, board, .. } => (String::new(), format!("Run started: {title} on {board}"), None),
        EventKind::StageStarted { stage, iteration } => (
            stage.clone(),
            if *iteration > 0 { format!("Started (iteration {iteration})") } else { "Started".into() },
            None,
        ),
        EventKind::StageFinished { stage, ok, summary, .. } => (stage.clone(), summary.clone(), Some(*ok)),
        EventKind::GateResult { gate, ok, summary, .. } => (gate.to_ascii_lowercase(), summary.clone(), Some(*ok)),
        EventKind::ToolCall { stage, tool, args } => {
            (stage.clone(), if args.is_empty() { tool.clone() } else { format!("{tool}({args})") }, None)
        }
        EventKind::Note { stage, text } => (stage.clone(), text.clone(), None),
        EventKind::ArtifactWritten { stage, path } => (stage.clone(), format!("Wrote {path}"), None),
        EventKind::RunFinished { ok, reason } => (String::new(), reason.clone(), Some(*ok)),
        // A recording, not an action: the gate_result just before it already says what happened.
        EventKind::SimTrace { .. } | EventKind::Unknown => return None,
    };
    Some(Action { ts: ev.ts, kind: kind_name(&ev.kind).into(), stage, text: clip(&text), ok })
}

fn kind_name(k: &EventKind) -> &'static str {
    match k {
        EventKind::RunStarted { .. } => "run_started",
        EventKind::StageStarted { .. } => "stage_started",
        EventKind::StageFinished { .. } => "stage_finished",
        EventKind::GateResult { .. } => "gate_result",
        EventKind::ToolCall { .. } => "tool_call",
        EventKind::Note { .. } => "note",
        EventKind::ArtifactWritten { .. } => "artifact_written",
        EventKind::RunFinished { .. } => "run_finished",
        EventKind::SimTrace { .. } => "sim_trace",
        EventKind::Unknown => "unknown",
    }
}

/// First line only, markdown emphasis stripped, cut to TEXT_MAX chars on a char boundary.
fn clip(s: &str) -> String {
    let line = s.lines().map(str::trim).find(|l| !l.is_empty()).unwrap_or("");
    let line = line.replace("**", "");
    if line.chars().count() <= TEXT_MAX {
        return line;
    }
    let mut out: String = line.chars().take(TEXT_MAX - 1).collect();
    out.push('…');
    out
}

// ---------------------------------------------------------------------------------------
// Overview payload
// ---------------------------------------------------------------------------------------

/// Read-only view of one run, assembled by the store.
pub struct RunView<'a> {
    pub state: &'a RunState,
    pub activity: &'a Activity,
    pub events: &'a [Seq],
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Liveness {
    /// Running and wrote an event recently.
    Active,
    /// No `run_finished`, and silent for longer than the stall threshold (crashed, killed,
    /// quota exhausted, or a very long simulation).
    Stalled,
    Passed,
    Failed,
}

#[derive(Debug, Clone, Serialize)]
pub struct Overview {
    pub now: f64,
    pub stall_after_s: f64,
    pub totals: Totals,
    /// Unfinished runs, active first, then stalled; newest first within each.
    pub active: Vec<ActiveRun>,
    pub personas: Vec<PersonaStat>,
    pub gates: Vec<GateStat>,
    pub boards: Vec<BoardStat>,
    pub recent: Vec<FinishedRun>,
    pub feed: Vec<FeedItem>,
    /// Every finished run's end time and verdict, for the throughput chart.
    pub outcomes: Vec<Outcome>,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct Totals {
    pub runs: u32,
    pub active: u32,
    pub stalled: u32,
    pub passed: u32,
    pub failed: u32,
    /// Pass rate over finished pipeline runs (`new`/`change`), excluding gate-only checks.
    pub pipeline_finished: u32,
    pub pipeline_passed: u32,
    pub cost_usd: f64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub runs_24h: u32,
    pub passed_24h: u32,
    pub failed_24h: u32,
    pub cost_24h: f64,
    pub tokens_24h: u64,
    pub median_duration_s: Option<f64>,
    pub mean_iterations: Option<f64>,
    pub last_activity_ts: Option<f64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ActiveRun {
    pub id: String,
    pub title: String,
    pub board: String,
    pub mode: String,
    pub liveness: Liveness,
    pub iteration: u32,
    pub max_iterations: Option<u32>,
    pub started_ts: Option<f64>,
    pub last_ts: Option<f64>,
    pub active_stage: Option<String>,
    pub stage_since: Option<f64>,
    pub last_stage: Option<String>,
    pub last_action: Option<Action>,
    pub cost_usd: f64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub tool_calls: u32,
    pub nodes: BTreeMap<String, NodeStatus>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PersonaStat {
    pub stage: String,
    /// Active runs currently inside this stage.
    pub busy_now: u32,
    pub sessions: u32,
    pub fails: u32,
    pub secs: f64,
    pub cost_usd: f64,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct GateStat {
    pub gate: String,
    pub evals: u32,
    pub fails: u32,
    /// Runs that evaluated this gate at least once.
    pub runs: u32,
    /// Runs whose first evaluation of this gate passed.
    pub first_try_pass: u32,
}

#[derive(Debug, Clone, Serialize)]
pub struct BoardStat {
    pub board: String,
    pub name: String,
    /// Has a Renode platform, i.e. can run G4/G5.
    pub simulated: bool,
    pub status: String,
    pub runs: u32,
    pub passed: u32,
    pub failed: u32,
    pub active: u32,
    pub stalled: u32,
    pub last: Option<BoardLast>,
}

#[derive(Debug, Clone, Serialize)]
pub struct BoardLast {
    pub id: String,
    pub title: String,
    pub liveness: Liveness,
    pub ts: Option<f64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct FinishedRun {
    pub id: String,
    pub title: String,
    pub board: String,
    pub mode: String,
    pub ok: bool,
    pub reason: String,
    pub started_ts: Option<f64>,
    pub finished_ts: Option<f64>,
    pub iteration: u32,
    pub cost_usd: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct FeedItem {
    pub run_id: String,
    pub title: String,
    pub board: String,
    #[serde(flatten)]
    pub action: Action,
}

#[derive(Debug, Clone, Serialize)]
pub struct Outcome {
    pub ts: f64,
    pub ok: bool,
    pub mode: String,
}

pub fn liveness(s: &RunState, now: f64, stall_after_s: f64) -> Liveness {
    match s.status {
        RunStatus::Passed => Liveness::Passed,
        RunStatus::Failed => Liveness::Failed,
        RunStatus::Running => {
            let last = s.last_ts.or(s.started_ts).unwrap_or(0.0);
            if now - last > stall_after_s { Liveness::Stalled } else { Liveness::Active }
        }
    }
}

fn stage_rank(k: &str) -> usize {
    STAGE_ORDER.iter().position(|s| *s == k).unwrap_or(STAGE_ORDER.len())
}

pub fn build(runs: &[RunView], boards: &Value, now: f64, stall_after_s: f64) -> Overview {
    let mut t = Totals::default();
    let mut active = Vec::new();
    let mut recent = Vec::new();
    let mut outcomes = Vec::new();
    let mut feed = Vec::new();
    let mut durations = Vec::new();
    let mut iterations = Vec::new();
    let mut personas: BTreeMap<String, PersonaStat> = BTreeMap::new();
    let mut gates: BTreeMap<String, GateStat> = GATES
        .iter()
        .map(|g| (g.to_string(), GateStat { gate: g.to_string(), evals: 0, fails: 0, runs: 0, first_try_pass: 0 }))
        .collect();
    let mut board_stats: BTreeMap<String, BoardStat> = BTreeMap::new();

    // Seed boards from the registry so idle boards still get a lane.
    if let Some(map) = boards.as_object() {
        for (k, b) in map {
            board_stats.insert(k.clone(), new_board(k, Some(b)));
        }
    }

    for v in runs {
        let s = v.state;
        let live = liveness(s, now, stall_after_s);
        let recent_24h = s.last_ts.is_some_and(|ts| now - ts <= DAY);
        let tokens = s.usage.input_tokens + s.usage.output_tokens;

        t.runs += 1;
        t.cost_usd += s.usage.cost_usd;
        t.input_tokens += s.usage.input_tokens;
        t.output_tokens += s.usage.output_tokens;
        t.last_activity_ts = max_opt(t.last_activity_ts, s.last_ts);
        if recent_24h {
            t.runs_24h += 1;
            t.cost_24h += s.usage.cost_usd;
            t.tokens_24h += tokens;
        }

        let board = board_stats.entry(s.board.clone()).or_insert_with(|| new_board(&s.board, None));
        board.runs += 1;
        let newer = match &board.last {
            None => true,
            Some(l) => s.last_ts.unwrap_or(0.0) > l.ts.unwrap_or(0.0),
        };
        if newer {
            board.last = Some(BoardLast { id: s.id.clone(), title: s.title.clone(), liveness: live, ts: s.last_ts });
        }

        match live {
            Liveness::Active | Liveness::Stalled => {
                if live == Liveness::Active {
                    t.active += 1;
                    board.active += 1;
                } else {
                    t.stalled += 1;
                    board.stalled += 1;
                }
                active.push(ActiveRun {
                    id: s.id.clone(),
                    title: s.title.clone(),
                    board: s.board.clone(),
                    mode: s.mode.clone(),
                    liveness: live,
                    iteration: s.iteration,
                    max_iterations: s.max_iterations,
                    started_ts: s.started_ts,
                    last_ts: s.last_ts,
                    active_stage: s.active_stage.clone(),
                    stage_since: v.activity.stage_since.or(s.started_ts),
                    last_stage: v.activity.last_stage.clone(),
                    last_action: v.activity.last_action.clone(),
                    cost_usd: s.usage.cost_usd,
                    input_tokens: s.usage.input_tokens,
                    output_tokens: s.usage.output_tokens,
                    tool_calls: v.activity.tool_calls,
                    nodes: s.nodes.iter().map(|(k, n)| (k.clone(), n.status)).collect(),
                });
            }
            Liveness::Passed | Liveness::Failed => {
                let ok = live == Liveness::Passed;
                if ok {
                    t.passed += 1;
                    board.passed += 1;
                } else {
                    t.failed += 1;
                    board.failed += 1;
                }
                if recent_24h {
                    if ok { t.passed_24h += 1 } else { t.failed_24h += 1 }
                }
                if s.mode != "gates" {
                    t.pipeline_finished += 1;
                    t.pipeline_passed += u32::from(ok);
                    iterations.push(f64::from(s.iteration));
                }
                if let (Some(a), Some(b)) = (s.started_ts, s.finished_ts) {
                    durations.push(b - a);
                }
                let end = s.finished_ts.or(s.last_ts).unwrap_or(0.0);
                outcomes.push(Outcome { ts: end, ok, mode: s.mode.clone() });
                recent.push(FinishedRun {
                    id: s.id.clone(),
                    title: s.title.clone(),
                    board: s.board.clone(),
                    mode: s.mode.clone(),
                    ok,
                    reason: clip(&s.reason),
                    started_ts: s.started_ts,
                    finished_ts: s.finished_ts,
                    iteration: s.iteration,
                    cost_usd: s.usage.cost_usd,
                });
            }
        }

        // Personas: sessions/fails from the folded nodes, time from the activity fold.
        for (k, n) in &s.nodes {
            if GATES.contains(&k.as_str()) {
                continue;
            }
            let p = personas.entry(k.clone()).or_insert_with(|| new_persona(k));
            p.sessions += n.runs;
            p.fails += n.fails;
        }
        for (k, secs) in &v.activity.stage_secs {
            personas.entry(k.clone()).or_insert_with(|| new_persona(k)).secs += secs;
        }
        for (k, u) in &s.usage_by_stage {
            let p = personas.entry(k.clone()).or_insert_with(|| new_persona(k));
            p.cost_usd += u.cost_usd;
            p.input_tokens += u.input_tokens;
            p.output_tokens += u.output_tokens;
        }
        if live == Liveness::Active
            && let Some(stage) = &s.active_stage
            && !GATES.contains(&stage.as_str()) {
                personas.entry(stage.clone()).or_insert_with(|| new_persona(stage)).busy_now += 1;
            }

        for g in GATES {
            if let Some(n) = s.nodes.get(g) {
                let gs = gates.get_mut(g).expect("seeded");
                gs.evals += n.runs;
                gs.fails += n.fails;
                gs.runs += u32::from(n.runs > 0);
                gs.first_try_pass += u32::from(v.activity.gate_first.get(g) == Some(&true));
            }
        }

        let tail = v.events.len().saturating_sub(FEED_PER_RUN);
        for e in &v.events[tail..] {
            if let Some(a) = action(&e.event) {
                feed.push(FeedItem { run_id: s.id.clone(), title: s.title.clone(), board: s.board.clone(), action: a });
            }
        }
    }

    t.median_duration_s = median(&mut durations);
    t.mean_iterations = (!iterations.is_empty()).then(|| iterations.iter().sum::<f64>() / iterations.len() as f64);

    active.sort_by(|a, b| {
        (a.liveness != Liveness::Active)
            .cmp(&(b.liveness != Liveness::Active))
            .then(cmp_desc(a.started_ts, b.started_ts))
    });
    recent.sort_by(|a, b| cmp_desc(a.finished_ts, b.finished_ts));
    recent.truncate(RECENT_MAX);
    feed.sort_by(|a, b| b.action.ts.total_cmp(&a.action.ts));
    feed.truncate(FEED_MAX);
    outcomes.sort_by(|a, b| a.ts.total_cmp(&b.ts));

    let mut personas: Vec<PersonaStat> = personas.into_values().collect();
    personas.sort_by_key(|p| stage_rank(&p.stage));

    Overview {
        now,
        stall_after_s,
        totals: t,
        active,
        personas,
        gates: gates.into_values().collect(),
        boards: board_stats.into_values().collect(),
        recent,
        feed,
        outcomes,
    }
}

fn new_persona(stage: &str) -> PersonaStat {
    PersonaStat {
        stage: stage.to_string(),
        busy_now: 0,
        sessions: 0,
        fails: 0,
        secs: 0.0,
        cost_usd: 0.0,
        input_tokens: 0,
        output_tokens: 0,
    }
}

fn new_board(key: &str, reg: Option<&Value>) -> BoardStat {
    let str_of = |f: &str| reg.and_then(|b| b.get(f)).and_then(Value::as_str).map(str::to_string);
    BoardStat {
        board: key.to_string(),
        name: str_of("name").unwrap_or_else(|| key.to_string()),
        simulated: str_of("renode_platform").is_some_and(|p| !p.is_empty()),
        status: str_of("status").unwrap_or_default(),
        runs: 0,
        passed: 0,
        failed: 0,
        active: 0,
        stalled: 0,
        last: None,
    }
}

fn max_opt(a: Option<f64>, b: Option<f64>) -> Option<f64> {
    match (a, b) {
        (Some(x), Some(y)) => Some(x.max(y)),
        (x, None) => x,
        (None, y) => y,
    }
}

fn cmp_desc(a: Option<f64>, b: Option<f64>) -> std::cmp::Ordering {
    b.unwrap_or(0.0).total_cmp(&a.unwrap_or(0.0))
}

fn median(v: &mut [f64]) -> Option<f64> {
    if v.is_empty() {
        return None;
    }
    v.sort_by(f64::total_cmp);
    let m = v.len() / 2;
    Some(if v.len() % 2 == 0 { (v[m - 1] + v[m]) / 2.0 } else { v[m] })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::parse_line;

    const FIXTURE: &str = include_str!("../../tests/fixtures/events/demo_run.jsonl");

    struct Owned {
        state: RunState,
        activity: Activity,
        events: Vec<Seq>,
    }

    fn fold(id: &str, lines: &[String]) -> Owned {
        let mut o = Owned { state: RunState::new(id), activity: Activity::default(), events: Vec::new() };
        for l in lines {
            let ev = parse_line(l).unwrap();
            o.state.apply(&ev);
            o.activity.apply(&ev);
            o.events.push(Seq { seq: o.events.len() as u64, event: ev });
        }
        o
    }

    fn views(runs: &[Owned]) -> Vec<RunView<'_>> {
        runs.iter().map(|o| RunView { state: &o.state, activity: &o.activity, events: &o.events }).collect()
    }

    fn lines(src: &str) -> Vec<String> {
        src.lines().filter(|l| !l.trim().is_empty()).map(String::from).collect()
    }

    #[test]
    fn finished_fixture_counts_as_passed_with_persona_time() {
        let runs = [fold("demo", &lines(FIXTURE))];
        let o = build(&views(&runs), &Value::Null, 1e9, 600.0);
        assert_eq!(o.totals.runs, 1);
        assert_eq!(o.totals.passed, 1);
        assert_eq!(o.totals.pipeline_passed, 1);
        assert!(o.active.is_empty());
        assert_eq!(o.recent.len(), 1);
        let fw = o.personas.iter().find(|p| p.stage == "firmware_engineer").unwrap();
        assert_eq!(fw.sessions, 3);
        assert!(fw.secs > 0.0);
        // Personas come out in pipeline order.
        let ranks: Vec<usize> = o.personas.iter().map(|p| stage_rank(&p.stage)).collect();
        assert!(ranks.windows(2).all(|w| w[0] <= w[1]), "{ranks:?}");
        let g1 = o.gates.iter().find(|g| g.gate == "g1").unwrap();
        assert_eq!((g1.evals, g1.fails, g1.runs, g1.first_try_pass), (3, 1, 1, 0));
        assert!(!o.feed.is_empty());
        assert!(o.feed.windows(2).all(|w| w[0].action.ts >= w[1].action.ts));
    }

    #[test]
    fn unfinished_runs_are_active_or_stalled_by_silence() {
        let mk = |ts0: f64| {
            vec![
                format!(r#"{{"ts":{ts0},"type":"run_started","mode":"new","board":"nrf52840dk","title":"t"}}"#),
                format!(r#"{{"ts":{},"type":"stage_started","stage":"analyst","iteration":0}}"#, ts0 + 1.0),
                format!(r#"{{"ts":{},"type":"tool_call","stage":"analyst","tool":"Read","args":".factory/spec.md"}}"#, ts0 + 2.0),
            ]
        };
        let runs = [fold("fresh", &mk(1000.0)), fold("old", &mk(10.0))];
        let boards: Value = serde_json::json!({"nrf52840dk": {"name": "nRF", "renode_platform": "@x"}, "rpi_pico": {"name": "Pico"}});
        let o = build(&views(&runs), &boards, 1100.0, 600.0);
        assert_eq!((o.totals.active, o.totals.stalled), (1, 1));
        assert_eq!(o.active[0].id, "fresh");
        assert_eq!(o.active[0].liveness, Liveness::Active);
        assert_eq!(o.active[1].liveness, Liveness::Stalled);
        assert_eq!(o.active[0].stage_since, Some(1001.0));
        assert_eq!(o.active[0].last_action.as_ref().unwrap().text, "Read(.factory/spec.md)");
        let analyst = o.personas.iter().find(|p| p.stage == "analyst").unwrap();
        assert_eq!(analyst.busy_now, 1, "stalled runs don't count as busy");
        let pico = o.boards.iter().find(|b| b.board == "rpi_pico").unwrap();
        assert_eq!((pico.runs, pico.simulated), (0, false));
        let nrf = o.boards.iter().find(|b| b.board == "nrf52840dk").unwrap();
        assert_eq!((nrf.active, nrf.stalled, nrf.simulated), (1, 1, true));
    }

    #[test]
    fn clip_takes_first_line_and_bounds_length() {
        assert_eq!(clip("\n  **Hello** world\nsecond"), "Hello world");
        let long = "é".repeat(500);
        let c = clip(&long);
        assert_eq!(c.chars().count(), TEXT_MAX);
        assert!(c.ends_with('…'));
    }
}
