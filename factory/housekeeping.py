"""Disk cleanup for runs/.

Most of a run's size is its Zephyr build tree (`ws/build`, plus `ws/build-*` for ztest builds), which
can be regenerated from the workspace. By default only those are removed; the event log, ledger,
report and the workspace git history stay, so the dashboard still shows every run.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

# A run without run_finished whose log changed this recently may still be going: never touch it.
ACTIVE_WINDOW_S = 3600


@dataclass
class Target:
    path: Path
    bytes: int
    run: str


def dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def is_finished(run: Path) -> bool:
    log = run / "events.jsonl"
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line).get("type") == "run_finished"
        except (json.JSONDecodeError, AttributeError):
            return False
    return False


def last_activity(run: Path) -> float:
    log = run / "events.jsonl"
    try:
        return log.stat().st_mtime
    except OSError:
        return run.stat().st_mtime


def plan(root: Path, *, older_than_days: float = 0, whole_runs: bool = False,
         now: float | None = None) -> tuple[list[Target], list[str]]:
    """What would be removed, plus the names of runs skipped because they may still be active."""
    now = time.time() if now is None else now
    targets: list[Target] = []
    skipped: list[str] = []
    if not root.is_dir():
        return targets, skipped
    for run in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        age = now - last_activity(run)
        if not is_finished(run) and age < ACTIVE_WINDOW_S:
            skipped.append(run.name)
            continue
        if age < older_than_days * 86400:
            continue
        if whole_runs:
            targets.append(Target(run, dir_size(run), run.name))
            continue
        ws = run / "ws"
        if ws.is_dir():
            for b in sorted(ws.glob("build*")):
                if b.is_dir() and not b.is_symlink():
                    targets.append(Target(b, dir_size(b), run.name))
    return targets, skipped


def remove(targets: list[Target], root: Path) -> int:
    root = root.resolve()
    freed = 0
    for t in targets:
        p = t.path.resolve()
        if root not in p.parents:   # never follow anything out of runs/
            continue
        shutil.rmtree(p, ignore_errors=True)
        freed += t.bytes
    return freed


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
