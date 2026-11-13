"""Append-only run event log (runs/<id>/events.jsonl).

The schema's source of truth is `dashboard/src/events.rs`; this module mirrors it.
Both sides are tested against tests/fixtures/events/demo_run.jsonl.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# type -> required fields (beyond ts/type). Keep in sync with dashboard/src/events.rs.
SCHEMA: dict[str, tuple[str, ...]] = {
    "run_started": ("mode", "board", "title"),
    "stage_started": ("stage",),
    "stage_finished": ("stage", "ok"),
    "gate_result": ("gate", "ok"),
    "tool_call": ("stage", "tool"),
    "note": ("stage", "text"),
    "artifact_written": ("stage", "path"),
    "run_finished": ("ok",),
}

STAGES = {
    "archaeologist", "analyst", "architect", "test_engineer", "lock_tests",
    "firmware_engineer", "debugger", "test_auditor", "reviewer", "integrator",
}
GATES = {"g1", "g2", "g3", "g4", "g5"}


def validate(event: dict[str, Any]) -> None:
    t = event.get("type")
    if t not in SCHEMA:
        raise ValueError(f"unknown event type {t!r}")
    if not isinstance(event.get("ts"), (int, float)):
        raise ValueError(f"{t}: missing numeric ts")
    missing = [f for f in SCHEMA[t] if f not in event]
    if missing:
        raise ValueError(f"{t}: missing fields {missing}")
    if "stage" in event and event["stage"] not in STAGES:
        raise ValueError(f"{t}: unknown stage {event['stage']!r}")
    if t == "gate_result" and str(event["gate"]).lower() not in GATES:
        raise ValueError(f"gate_result: unknown gate {event['gate']!r}")


class EventLog:
    FILENAME = "events.jsonl"

    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / self.FILENAME
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, type: str, **fields: Any) -> dict[str, Any]:
        event = {"ts": time.time(), "type": type, **fields}
        validate(event)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=_jsonable) + "\n")
        return event

    # Convenience wrappers -------------------------------------------------------------
    def run_started(self, mode: str, board: str, title: str, max_iterations: int | None = None):
        return self.emit("run_started", mode=mode, board=board, title=title, max_iterations=max_iterations)

    def stage_started(self, stage: str, iteration: int = 0):
        return self.emit("stage_started", stage=stage, iteration=iteration)

    def stage_finished(self, stage: str, ok: bool, summary: str = "", iteration: int = 0,
                       usage: dict | None = None, detail: Any = None):
        extra = {k: v for k, v in {"usage": usage, "detail": detail}.items() if v is not None}
        return self.emit("stage_finished", stage=stage, iteration=iteration, ok=ok, summary=summary, **extra)

    def gate(self, gate: str, ok: bool, summary: str, iteration: int = 0, detail: Any = None):
        return self.emit("gate_result", gate=gate, iteration=iteration, ok=ok, summary=summary,
                         detail=detail if detail is not None else {})

    def tool_call(self, stage: str, tool: str, args: str = ""):
        return self.emit("tool_call", stage=stage, tool=tool, args=args)

    def note(self, stage: str, text: str):
        return self.emit("note", stage=stage, text=text)

    def artifact(self, stage: str, path: str):
        return self.emit("artifact_written", stage=stage, path=path)

    def run_finished(self, ok: bool, reason: str = ""):
        return self.emit("run_finished", ok=ok, reason=reason)


def _jsonable(o: Any) -> Any:
    if hasattr(o, "model_dump"):
        return o.model_dump()
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")
