"""factory CLI.

  factory new    --spec spec.md --board stm32f4_disco     spec -> verified firmware
  factory change --repo PATH --request cr.md --board ...   change request on an existing repo
  factory bench  [--specs benchmarks/specs] --board ...    run a spec suite, summarise results
  factory gates  --board ... [--app DIR]                   deterministic gates only (no LLM)
  factory clean  [--older-than DAYS] [--runs] [--dry-run]   free disk space under runs/
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import typer

from factory import housekeeping
from factory.events import EventLog
from factory.gates import run_gates
from factory.pipeline import Pipeline, RunOutcome, new_run_dir
from factory.tools.board import board_info
from factory.tools.container import REPO_ROOT, Toolchain

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Embedded firmware factory: LLM personas + deterministic gates + Renode."""


def _title(spec: str, fallback: str) -> str:
    for line in spec.splitlines():
        if line.strip().startswith("#"):
            return line.strip("# ").strip()[:80]
    return fallback


def _k(n: int) -> str:
    if n >= 1e6:
        return f"{n / 1e6:.2f}M"
    return f"{n / 1e3:.0f}k" if n >= 1e3 else str(n)


COST_HELP = ("Stop when the SDK-reported cost reaches this (USD at API list prices; on a Claude "
             "subscription it is a quota measure, not a charge)")


def _print_outcome(o: RunOutcome) -> None:
    typer.echo(f"\n{'PASSED' if o.ok else 'FAILED'}: {o.reason}")
    typer.echo(f"run dir: {o.run_dir}")
    typer.echo(f"tokens: {_k(o.input_tokens)} in / {_k(o.output_tokens)} out   "
               f"API-equivalent cost: ${o.cost_usd:.2f}   duration: {o.duration_s / 60:.1f} min")
    if (o.run_dir / "report.md").exists():
        typer.echo(f"report: {o.run_dir / 'report.md'}")


@app.command()
def new(
    spec: Path = typer.Option(..., exists=True, readable=True, help="Markdown spec"),
    board: str = typer.Option("stm32f4_disco"),
    max_iterations: int = typer.Option(6),
    max_cost: float = typer.Option(30.0, help=COST_HELP),
) -> None:
    """Produce new firmware from a spec."""
    board_info(board)
    text = spec.read_text()
    title = _title(text, spec.stem)
    run = new_run_dir(f"{spec.stem}-{board}")
    typer.echo(f"run dir: {run}  (watch it live with `make dash`)")
    p = Pipeline(run, board, max_iterations=max_iterations, max_cost_usd=max_cost)
    o = asyncio.run(p.run_new(text, title))
    _print_outcome(o)
    raise typer.Exit(0 if o.ok else 1)


@app.command()
def change(
    repo: Path = typer.Option(..., exists=True, file_okay=False, help="Existing Zephyr app directory"),
    request: Path = typer.Option(..., exists=True, readable=True, help="Markdown change request"),
    board: str = typer.Option("stm32f4_disco"),
    max_iterations: int = typer.Option(6),
    max_cost: float = typer.Option(30.0, help=COST_HELP),
) -> None:
    """Apply a change request to an existing firmware repo."""
    board_info(board)
    text = request.read_text()
    run = new_run_dir(f"change-{repo.name}-{board}")
    typer.echo(f"run dir: {run}")
    p = Pipeline(run, board, max_iterations=max_iterations, max_cost_usd=max_cost)
    o = asyncio.run(p.run_change(repo.resolve(), text, _title(text, f"Change {repo.name}")))
    _print_outcome(o)
    raise typer.Exit(0 if o.ok else 1)


@app.command()
def bench(
    specs: Path = typer.Option(REPO_ROOT / "benchmarks" / "specs", exists=True, file_okay=False),
    board: str = typer.Option("stm32f4_disco"),
    only: str = typer.Option("", help="Comma-separated spec name prefixes to run"),
    max_iterations: int = typer.Option(6),
    max_cost: float = typer.Option(20.0, help="Per-spec cap. " + COST_HELP),
) -> None:
    """Run the benchmark spec suite and write a summary."""
    files = sorted(specs.glob("*.md"))
    if only:
        prefixes = [p.strip() for p in only.split(",") if p.strip()]
        files = [f for f in files if any(f.name.startswith(p) for p in prefixes)]
    out_dir = REPO_ROOT / "benchmarks" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rows: list[dict] = []
    md: list[str] = []
    for f in files:
        text = f.read_text()
        run = new_run_dir(f"bench-{f.stem}-{board}")
        typer.echo(f"\n=== {f.name} -> {run.name}")
        o = asyncio.run(Pipeline(run, board, max_iterations=max_iterations, max_cost_usd=max_cost)
                        .run_new(text, _title(text, f.stem)))
        _print_outcome(o)
        rows.append({"spec": f.name, "ok": o.ok, "reason": o.reason, "iterations": o.iterations,
                     "cost_usd": o.cost_usd, "input_tokens": o.input_tokens,
                     "output_tokens": o.output_tokens, "minutes": round(o.duration_s / 60, 1), "run": run.name})
        md = _write_bench(out_dir, board, stamp, rows)   # after every spec, so an abort keeps finished rows
    typer.echo("\n" + "\n".join(md))


def _write_bench(out_dir: Path, board: str, stamp: str, rows: list[dict]) -> list[str]:
    (out_dir / f"bench-{board}-{stamp}.json").write_text(json.dumps(rows, indent=2))
    md = [f"# Benchmark {stamp} on {board}", "", "| spec | result | iterations | tokens in / out | API-equiv. cost | minutes | reason |",
          "|---|---|---|---|---|---|---|"]
    md += [f"| {r['spec']} | {'pass' if r['ok'] else 'FAIL'} | {r['iterations']} | "
           f"{_k(r['input_tokens'])} / {_k(r['output_tokens'])} | ${r['cost_usd']:.2f} | "
           f"{r['minutes']} | {r['reason'].replace('|', '/')} |" for r in rows]
    passed = sum(r["ok"] for r in rows)
    md += ["", f"**{passed}/{len(rows)} passed**, API-equivalent cost ${sum(r['cost_usd'] for r in rows):.2f}, "
               f"mean iterations {sum(r['iterations'] for r in rows) / max(len(rows), 1):.1f}"]
    md += ["", "_Cost is what the SDK reports at API list prices. Runs on a Claude subscription "
           "consume plan usage instead of being billed per token._"]
    (out_dir / f"bench-{board}-{stamp}.md").write_text("\n".join(md) + "\n")
    return md


@app.command()
def gates(
    board: str = typer.Option("stm32f4_disco"),
    app_dir: Path = typer.Option(REPO_ROOT / "templates" / "zephyr-app", "--app"),
    flash_budget: int = typer.Option(64 * 1024),
    ram_budget: int = typer.Option(16 * 1024),
) -> None:
    """Run the deterministic gates on an app directory (no LLM)."""
    board_info(board)
    run = new_run_dir(f"gates-{board}")
    ws = run / "ws"
    shutil.copytree(app_dir, ws, ignore=shutil.ignore_patterns("build"))
    log = EventLog(run)
    log.run_started("gates", board, f"Gate check: {app_dir.name} on {board}", max_iterations=1)
    typer.echo(f"run dir: {run}")
    rep = run_gates(Toolchain(), ws, board, {"FLASH": flash_budget, "RAM": ram_budget}, log=log, iteration=1)
    for r in rep.results:
        typer.echo(f"{r.gate.upper()}: {'PASS' if r.ok else 'FAIL'}  {r.summary}")
    if not rep.ok:
        typer.echo(rep.feedback())
    log.run_finished(rep.ok, "all gates green" if rep.ok else "gate failures")
    raise typer.Exit(0 if rep.ok else 1)


@app.command()
def clean(
    older_than: float = typer.Option(0, help="Only touch runs whose last event is at least this many days old"),
    runs: bool = typer.Option(False, "--runs", help="Delete whole run directories, not just build trees"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would be removed and stop"),
    root: Path = typer.Option(REPO_ROOT / "runs", hidden=True),
) -> None:
    """Free disk space under runs/. By default removes only the regenerable build trees (ws/build*),
    keeping logs, ledgers, reports and workspaces. Runs that may still be in progress are skipped."""
    targets, skipped = housekeeping.plan(root, older_than_days=older_than, whole_runs=runs)
    for name in skipped:
        typer.echo(f"skip   {name} (no run_finished, active in the last hour)")
    for t in targets:
        typer.echo(f"{'would remove' if dry_run else 'remove'}  {t.path.relative_to(root)}  ({housekeeping.human(t.bytes)})")
    total = sum(t.bytes for t in targets)
    if not targets:
        typer.echo("nothing to remove")
    elif dry_run:
        typer.echo(f"{len(targets)} item(s), {housekeeping.human(total)} would be freed")
    else:
        freed = housekeeping.remove(targets, root)
        typer.echo(f"freed {housekeeping.human(freed)} from {len(targets)} item(s)")


if __name__ == "__main__":
    app()
