//! HTTP API + server-sent events + embedded UI.

use std::convert::Infallible;
use std::time::Duration;

use axum::{
    Json, Router,
    extract::{Path, Request, State},
    http::{StatusCode, header},
    middleware::{self, Next},
    response::{
        IntoResponse, Response,
        sse::{Event as SseEvent, KeepAlive, Sse},
    },
    routing::get,
};
use futures::{Stream, StreamExt, stream};
use serde_json::Value;
use tokio_stream::wrappers::BroadcastStream;

use crate::store::Store;

#[derive(Clone)]
pub struct AppState {
    pub store: Store,
    pub boards: Value,
    /// A running run that has been silent this long is shown as stalled.
    pub stall_after: Duration,
}

const OVERVIEW: &str = include_str!("../ui/overview.html");
const OVERVIEW_JS: &str = include_str!("../ui/overview.js");
const OVERVIEW_CSS: &str = include_str!("../ui/overview.css");
const INDEX: &str = include_str!("../ui/index.html");
const APP_JS: &str = include_str!("../ui/app.js");
const STYLE: &str = include_str!("../ui/style.css");

/// `loopback`: the server is bound to a loopback address, so only loopback Host headers are
/// accepted. That stops DNS rebinding (a web page on evil.example re-pointing its name at
/// 127.0.0.1 and reading the dashboard from the victim's browser).
pub fn router(state: AppState, loopback: bool) -> Router {
    let r = Router::new()
        .route("/", get(|| async { asset("text/html; charset=utf-8", OVERVIEW) }))
        .route("/overview.js", get(|| async { asset("text/javascript; charset=utf-8", OVERVIEW_JS) }))
        .route("/overview.css", get(|| async { asset("text/css; charset=utf-8", OVERVIEW_CSS) }))
        .route("/runs", get(|| async { asset("text/html; charset=utf-8", INDEX) }))
        .route("/app.js", get(|| async { asset("text/javascript; charset=utf-8", APP_JS) }))
        .route("/style.css", get(|| async { asset("text/css; charset=utf-8", STYLE) }))
        .route("/api/runs", get(list_runs))
        .route("/api/runs/{id}", get(get_run))
        .route("/api/runs/{id}/state", get(get_run_state))
        .route("/api/runs/{id}/stream", get(stream_run))
        .route("/api/stream", get(stream_all))
        .route("/api/overview", get(get_overview))
        .route("/api/overview/stream", get(stream_overview))
        .route("/api/boards", get(|State(s): State<AppState>| async move { Json(s.boards) }))
        .with_state(state);
    if loopback { r.layer(middleware::from_fn(loopback_host_only)) } else { r }
}

pub fn is_loopback_host(host: &str) -> bool {
    let name = if let Some(rest) = host.strip_prefix('[') {
        // [::1]:7878
        match rest.split_once(']') {
            Some((inner, tail)) if tail.is_empty() || tail.starts_with(':') => inner,
            _ => return false,
        }
    } else {
        host.rsplit_once(':').map_or(host, |(h, port)| if port.chars().all(|c| c.is_ascii_digit()) { h } else { host })
    };
    let name = name.to_ascii_lowercase();
    name == "localhost" || name.ends_with(".localhost") || name.parse::<std::net::IpAddr>().is_ok_and(|ip| ip.is_loopback())
}

async fn loopback_host_only(req: Request, next: Next) -> Response {
    let ok = req.headers().get(header::HOST).and_then(|h| h.to_str().ok()).is_some_and(is_loopback_host);
    if ok {
        next.run(req).await
    } else {
        (StatusCode::MISDIRECTED_REQUEST, "factory-dash only answers to localhost / 127.0.0.1 / [::1]").into_response()
    }
}

fn asset(ct: &'static str, body: &'static str) -> Response {
    ([(header::CONTENT_TYPE, ct), (header::CACHE_CONTROL, "no-cache")], body).into_response()
}

async fn list_runs(State(s): State<AppState>) -> impl IntoResponse {
    Json(s.store.summaries().await)
}

async fn get_run(State(s): State<AppState>, Path(id): Path<String>) -> Response {
    match s.store.snapshot(&id).await {
        Some(snap) => Json(snap).into_response(),
        None => (StatusCode::NOT_FOUND, format!("no run named '{id}'")).into_response(),
    }
}

async fn get_run_state(State(s): State<AppState>, Path(id): Path<String>) -> Response {
    match s.store.state(&id).await {
        Some(st) => Json(st).into_response(),
        None => (StatusCode::NOT_FOUND, format!("no run named '{id}'")).into_response(),
    }
}

/// Per-run stream: a `snapshot` event, then every later event as `event`.
/// Subscribing before taking the snapshot means nothing is lost between the two;
/// events already in the snapshot are filtered out by sequence number.
async fn stream_run(
    State(s): State<AppState>,
    Path(id): Path<String>,
) -> Result<Sse<impl Stream<Item = Result<SseEvent, Infallible>>>, (StatusCode, String)> {
    let rx = s.store.subscribe();
    let snap = s
        .store
        .snapshot(&id)
        .await
        .ok_or((StatusCode::NOT_FOUND, format!("no run named '{id}'")))?;
    let next_seq = snap.events.len() as u64;

    let first = stream::once(async move {
        Ok(SseEvent::default().event("snapshot").json_data(&snap).unwrap_or_default())
    });
    let live = BroadcastStream::new(rx).filter_map(move |msg| {
        let id = id.clone();
        async move {
            match msg {
                Ok(u) if u.run_id == id && u.event.seq >= next_seq => {
                    Some(Ok(SseEvent::default().event("event").json_data(&u.event).unwrap_or_default()))
                }
                Ok(_) => None,
                // Lagged: tell the client to resync from a fresh snapshot.
                Err(_) => Some(Ok(SseEvent::default().event("resync").data("lagged"))),
            }
        }
    });
    Ok(Sse::new(first.chain(live)).keep_alive(KeepAlive::new().interval(Duration::from_secs(15))))
}

/// Global stream for the run list: emits a `runs` event (all summaries) whenever any run changes,
/// coalesced to at most a few per second.
async fn stream_all(State(s): State<AppState>) -> Sse<impl Stream<Item = Result<SseEvent, Infallible>>> {
    let store = s.store.clone();
    let initial = store.summaries().await;
    let first = stream::once(async move { Ok(SseEvent::default().event("runs").json_data(&initial).unwrap_or_default()) });

    let ticks = BroadcastStream::new(s.store.subscribe())
        .ready_chunks(256)
        .then(move |_| {
            let store = store.clone();
            async move {
                tokio::time::sleep(Duration::from_millis(250)).await;
                let runs = store.summaries().await;
                Ok(SseEvent::default().event("runs").json_data(&runs).unwrap_or_default())
            }
        });
    Sse::new(first.chain(ticks)).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)))
}

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

async fn overview_json(s: &AppState) -> crate::overview::Overview {
    s.store.overview(&s.boards, now(), s.stall_after.as_secs_f64()).await
}

async fn get_overview(State(s): State<AppState>) -> impl IntoResponse {
    Json(overview_json(&s).await)
}

/// Overview stream: an `overview` event on connect, again whenever any run changes (coalesced),
/// and at least every few seconds so runs move to "stalled" without needing a new event.
async fn stream_overview(State(s): State<AppState>) -> Sse<impl Stream<Item = Result<SseEvent, Infallible>>> {
    const HEARTBEAT: Duration = Duration::from_secs(5);
    let changes = BroadcastStream::new(s.store.subscribe()).ready_chunks(256).map(|_| ());
    let ticks = stream::unfold((), |()| async {
        tokio::time::sleep(HEARTBEAT).await;
        Some(((), ()))
    });
    let triggers = stream::once(async {}).chain(stream::select(changes, ticks));
    let out = triggers.then(move |()| {
        let s = s.clone();
        async move {
            // Let a burst of appends land before folding, so one push covers them all.
            tokio::time::sleep(Duration::from_millis(150)).await;
            let o = overview_json(&s).await;
            Ok(SseEvent::default().event("overview").json_data(&o).unwrap_or_default())
        }
    });
    Sse::new(out).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)))
}

#[cfg(test)]
mod tests {
    use super::is_loopback_host;

    #[test]
    fn host_check_accepts_only_loopback_names() {
        for h in ["localhost", "localhost:7878", "127.0.0.1:7878", "127.0.0.2", "[::1]:7878", "[::1]", "LOCALHOST:1", "dash.localhost:7878"] {
            assert!(is_loopback_host(h), "{h} should pass");
        }
        for h in ["evil.example", "evil.example:7878", "127.0.0.1.evil.example", "localhost.evil.example", "[::1]x", "10.0.0.5:7878", ""] {
            assert!(!is_loopback_host(h), "{h} should be rejected");
        }
    }
}
