"""Structured results passed between tools, gates and personas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Diagnostic(BaseModel):
    file: str | None = None
    line: int | None = None
    column: int | None = None
    severity: Literal["error", "warning", "note"]
    message: str


class BuildResult(BaseModel):
    ok: bool
    board: str
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    log_tail: str = ""

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]


class MemRegion(BaseModel):
    name: str
    used_bytes: int
    size_bytes: int

    @property
    def percent(self) -> float:
        return 100.0 * self.used_bytes / self.size_bytes if self.size_bytes else 0.0


class SizeResult(BaseModel):
    ok: bool
    regions: list[MemRegion]
    violations: list[str] = Field(default_factory=list)


class RobotTest(BaseModel):
    name: str
    suite: str
    passed: bool
    message: str = ""


class SimEvent(BaseModel):
    """One observable change on the simulated board, at `t` seconds of virtual time since the
    tracer attached. led/button: value 1 = on/pressed, 0 = off/released. uart: value is one line."""
    t: float
    kind: Literal["led", "button", "uart"]
    id: str
    value: int | str


class SimClip(BaseModel):
    """What the board did during one Robot test, for replay in the dashboard."""
    suite: str
    test: str
    passed: bool
    message: str = ""
    duration: float = 0.0
    events: list[SimEvent] = Field(default_factory=list)
    truncated: bool = False
    errors: list[str] = Field(default_factory=list)


class SimResult(BaseModel):
    ok: bool
    tests: list[RobotTest]
    log_tail: str = ""
    # Replay traces for the dashboard. Not shown to personas (see tools/mcp.py).
    clips: list[SimClip] = Field(default_factory=list)

    @property
    def failed(self) -> list[RobotTest]:
        return [t for t in self.tests if not t.passed]


class GateResult(BaseModel):
    gate: str
    ok: bool
    summary: str
    detail: dict = Field(default_factory=dict)
