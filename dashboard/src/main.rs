//! factory-dash: live dashboard for embedded-factory runs.
//!
//!   factory-dash serve [--runs runs] [--port 7878] [--demo] [--stall-after 600]
//!   factory-dash replay [--runs runs] [--speed 1.0]
//!   factory-dash check runs/<id>/events.jsonl

mod demo;
mod events;
mod overview;
mod store;
mod web;

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use anyhow::Context;
use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "factory-dash", version, about = "Live dashboard for embedded-factory runs")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Serve the dashboard and watch a runs directory.
    Serve {
        #[arg(long, default_value = "runs")]
        runs: PathBuf,
        #[arg(long, default_value = "factory/boards.yaml")]
        boards: PathBuf,
        #[arg(long, default_value_t = 7878)]
        port: u16,
        #[arg(long, default_value = "127.0.0.1")]
        host: String,
        /// Also start a replay of the recorded demo run.
        #[arg(long)]
        demo: bool,
        /// Replay speed multiplier for --demo.
        #[arg(long, default_value_t = 1.0)]
        speed: f64,
        /// Poll interval in milliseconds.
        #[arg(long, default_value_t = 300)]
        poll_ms: u64,
        /// Seconds without a new event before an unfinished run is shown as stalled.
        #[arg(long, default_value_t = 600)]
        stall_after: u64,
    },
    /// Replay the recorded demo run into the runs directory (no server).
    Replay {
        #[arg(long, default_value = "runs")]
        runs: PathBuf,
        #[arg(long, default_value_t = 1.0)]
        speed: f64,
    },
    /// Validate an events.jsonl file against the event schema and print the folded result.
    Check { file: PathBuf },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();

    match Cli::parse().cmd {
        Cmd::Serve { runs, boards, port, host, demo, speed, poll_ms, stall_after } => {
            let boards = load_boards(&boards);
            let store = store::Store::new(&runs);
            store.scan().await.context("initial scan")?;
            store.clone().spawn_poller(Duration::from_millis(poll_ms.max(50)));

            if demo {
                let dir = demo::new_demo_dir(&runs)?;
                tracing::info!("replaying demo run into {}", dir.display());
                tokio::spawn(async move {
                    if let Err(e) = demo::replay(dir, speed).await {
                        tracing::error!("demo replay failed: {e:#}");
                    }
                });
            }

            let addr: SocketAddr = format!("{host}:{port}").parse().context("bad --host/--port")?;
            let listener = tokio::net::TcpListener::bind(addr).await.with_context(|| format!("bind {addr}"))?;
            tracing::info!("factory-dash on http://{addr}  (watching {})", store.root().display());
            let state = web::AppState { store, boards, stall_after: Duration::from_secs(stall_after.max(1)) };
            axum::serve(listener, web::router(state, addr.ip().is_loopback()))
                .with_graceful_shutdown(async {
                    let _ = tokio::signal::ctrl_c().await;
                })
                .await?;
        }
        Cmd::Replay { runs, speed } => {
            let dir = demo::new_demo_dir(&runs)?;
            println!("replaying into {}", dir.display());
            demo::replay(dir, speed).await?;
        }
        Cmd::Check { file } => {
            let text = std::fs::read_to_string(&file).with_context(|| format!("read {}", file.display()))?;
            let mut st = events::RunState::new(file.parent().and_then(|p| p.file_name()).and_then(|s| s.to_str()).unwrap_or("run"));
            let (mut ok, mut bad) = (0, 0);
            for (i, line) in text.lines().enumerate() {
                if line.trim().is_empty() {
                    continue;
                }
                match events::parse_line(line) {
                    Some(ev) if ev.kind == events::EventKind::Unknown => {
                        eprintln!("line {}: unknown event type", i + 1);
                        bad += 1;
                    }
                    Some(ev) => {
                        st.apply(&ev);
                        ok += 1;
                    }
                    None => {
                        eprintln!("line {}: not a valid event", i + 1);
                        bad += 1;
                    }
                }
            }
            println!("{ok} valid, {bad} invalid");
            println!("{}", serde_json::to_string_pretty(&st)?);
            if bad > 0 {
                std::process::exit(1);
            }
        }
    }
    Ok(())
}

fn load_boards(path: &PathBuf) -> serde_json::Value {
    match std::fs::read_to_string(path) {
        Ok(text) => match serde_yaml::from_str::<serde_json::Value>(&text) {
            Ok(v) => v,
            Err(e) => {
                tracing::warn!("could not parse {}: {e}", path.display());
                serde_json::Value::Null
            }
        },
        Err(_) => {
            tracing::warn!("board registry {} not found; board panel will be empty", path.display());
            serde_json::Value::Null
        }
    }
}
