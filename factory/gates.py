"""Deterministic gates G1..G5. The only thing in the factory allowed to say "passed"."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from factory.events import EventLog
from factory.schemas import GateResult
from factory.tools import firmware
from factory.tools.board import has_simulation
from factory.tools.container import Toolchain

# Cap on replay clips per sim_trace event, so one huge suite cannot bloat events.jsonl.
MAX_TRACE_CLIPS = 60


@dataclass
class GateReport:
    ok: bool
    results: list[GateResult] = field(default_factory=list)

    @property
    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.ok]

    def feedback(self) -> str:
        """Compact, structured failure description for the Debugger / Firmware Engineer."""
        lines = []
        for r in self.failures:
            lines.append(f"## {r.gate.upper()} FAILED: {r.summary}")
            d = r.detail
            for x in d.get("diagnostics", [])[:15]:
                if x.get("severity") == "error":
                    lines.append(f"- {x.get('file')}:{x.get('line')}: {x.get('message')}")
            for t in d.get("tests", []):
                if not t.get("passed"):
                    lines.append(f"- test '{t.get('name')}' failed: {t.get('message') or '(no message)'}")
            for v in d.get("violations", []):
                lines.append(f"- {v}")
            if d.get("log_tail"):
                lines.append("```\n" + d["log_tail"][-2000:] + "\n```")
        return "\n".join(lines)


def run_gates(tc: Toolchain, ws: Path, board: str, budgets: dict[str, int],
              log: EventLog | None = None, iteration: int = 0) -> GateReport:
    rep = GateReport(ok=True)

    def record(gate: str, ok: bool, summary: str, detail: dict) -> None:
        r = GateResult(gate=gate, ok=ok, summary=summary, detail=detail)
        rep.results.append(r)
        rep.ok &= ok
        if log:
            log.gate(gate, ok, summary, iteration=iteration, detail=detail)

    # G1 build: everything else needs an ELF, so stop here on failure.
    b = firmware.build(tc, ws, board, pristine=False)
    nerr = len(b.errors)
    nwarn = sum(d.severity == "warning" for d in b.diagnostics)
    record("g1", b.ok, f"{nerr} errors, {nwarn} warnings" if not b.ok or nwarn else "built cleanly",
           {"diagnostics": [d.model_dump() for d in b.diagnostics],
            **({"log_tail": b.log_tail[-2500:]} if not b.ok and not nerr else {})})
    if not b.ok:
        return rep

    # G2 size
    s = firmware.size_check(ws, budgets)
    by = firmware.regions_by_name(s.regions)
    parts = [f"{n} {by[n].used_bytes / 1024:.1f} KiB" + (f" / {budgets[n] / 1024:.0f} KiB" if n in budgets else "")
             for n in ("FLASH", "RAM") if n in by]
    record("g2", s.ok, ", ".join(parts) + (f" · {'; '.join(s.violations)}" if s.violations else ""),
           {"regions": [r.model_dump() for r in s.regions if r.used_bytes], "violations": s.violations})

    # G3-G5 only read the G1 output and write to separate places (build/cppcheck.txt, build/ztest,
    # build/robot), so they run concurrently; results are still recorded in gate order.
    sim = has_simulation(board)
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="gate") as pool:
        g3 = pool.submit(firmware.static_analysis, tc, ws)
        g4 = pool.submit(firmware.run_ztest, tc, ws, board) if sim else None
        g5 = pool.submit(firmware.simulate_all, tc, ws, board) if sim else None

        # G3 static analysis: errors fail the gate, style/warnings go to the Reviewer.
        diags = g3.result()
        nerr = sum(d.severity == "error" for d in diags)
        record("g3", nerr == 0, f"cppcheck: {nerr} errors, {len(diags) - nerr} warnings",
               {"diagnostics": [d.model_dump() for d in diags]})

        # G4 ztest (unit tests, run on the target board in Renode)
        if g4 is None:
            record("g4", True, f"skipped: {board} has no Renode model", {})
        elif (z := g4.result()) is None:
            record("g4", True, "no ztest suite (skipped)", {})
        else:
            n = sum(t.passed for t in z.tests)
            record("g4", z.ok, f"{n}/{len(z.tests)} ztest cases passed" if z.tests else "ztest did not complete",
                   {"tests": [t.model_dump() for t in z.tests], **({"log_tail": z.log_tail} if not z.ok else {})})

        # G5 Renode Robot acceptance tests
        if g5 is None:
            record("g5", True, f"skipped: {board} is build-only", {})
        elif (r := g5.result()) is None:
            record("g5", False, "no Robot suites in tests/robot", {})
        else:
            n = sum(t.passed for t in r.tests)
            record("g5", r.ok, f"{n}/{len(r.tests)} Robot tests passed",
                   {"tests": [t.model_dump() for t in r.tests], **({"log_tail": r.log_tail} if not r.ok else {})})
            if log and r.clips:
                log.sim_trace("g5", board, [c.model_dump() for c in r.clips[:MAX_TRACE_CLIPS]], iteration=iteration)
    return rep
