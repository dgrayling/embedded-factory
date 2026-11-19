"""In-process MCP tools exposed to personas (server name "fw" -> tools "mcp__fw__<name>")."""
from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from factory.tools import firmware
from factory.tools.board import board_info as _board_info, has_simulation
from factory.tools.container import Toolchain


@dataclass
class ToolContext:
    tc: Toolchain
    ws: Path
    board: str
    budgets: dict[str, int]


def _ok(payload: Any) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, default=str, indent=1)[:20000]}]}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def build_server(ctx: ToolContext):
    # Every toolchain tool reads or writes ws/build, and the model may issue tool calls in parallel
    # (two concurrent `west build`s in one build dir corrupt it), so run them one at a time.
    lock = asyncio.Lock()

    async def run(fn, *a):
        async with lock:
            return await asyncio.to_thread(fn, *a)

    @tool("board_info", "Facts about the target board: Zephyr board name, Renode platform, console UART, "
          "LED/button peripheral paths in Renode, and which peripherals the simulator models.", {})
    async def board_info(args):
        return _ok(_board_info(ctx.board))

    @tool("build", "Build the firmware with west for the target board. Returns ok plus compiler "
          "diagnostics with workspace-relative file:line.", {"pristine": bool})
    async def build(args):
        r = await run(firmware.build, ctx.tc, ctx.ws, ctx.board, bool(args.get("pristine", False)))
        return _ok(r.model_dump())

    @tool("size_report", "ROM/RAM usage of the last build compared with the requirement budgets.", {})
    async def size_report(args):
        r = await run(firmware.size_check, ctx.ws, ctx.budgets)
        return _ok({"ok": r.ok, "budgets_bytes": ctx.budgets, "violations": r.violations,
                    "regions": [x.model_dump() for x in r.regions if x.used_bytes]})

    @tool("static_analysis", "Run cppcheck over src/. Returns findings with file:line and severity.", {})
    async def static_analysis(args):
        r = await run(firmware.static_analysis, ctx.tc, ctx.ws)
        return _ok([d.model_dump() for d in r])

    @tool("run_ztest", "Build tests/ztest for the target board and run it in Renode. "
          "Returns per-case pass/fail and console output on failure.", {})
    async def run_ztest(args):
        if not has_simulation(ctx.board):
            return _err(f"{ctx.board} has no Renode model; ztest cannot run")
        r = await run(firmware.run_ztest, ctx.tc, ctx.ws, ctx.board)
        return _ok({"skipped": "no tests/ztest app"} if r is None else r.model_dump())

    @tool("simulate", "Run all Robot acceptance suites in tests/robot against the last build in Renode. "
          "Build first. Returns per-test pass/fail with failure messages.", {})
    async def simulate(args):
        if not has_simulation(ctx.board):
            return _err(f"{ctx.board} has no Renode model; simulation unavailable")
        r = await run(firmware.simulate_all, ctx.tc, ctx.ws, ctx.board)
        # Replay clips are for the dashboard; they would only crowd the persona's context.
        return _ok({"skipped": "no Robot suites"} if r is None else r.model_dump(exclude={"clips"}))

    @tool("uart_log", "Boot the last build in Renode for N seconds of simulated time and return the "
          "console UART output. Use to see what the firmware actually prints.", {"seconds": float})
    async def uart_log(args):
        if not has_simulation(ctx.board):
            return _err(f"{ctx.board} has no Renode model")
        elf = ctx.ws / "build" / "zephyr" / "zephyr.elf"
        if not elf.exists():
            return _err("no build/zephyr/zephyr.elf; call build first")
        try:
            secs = float(args.get("seconds", 3.0))
        except (TypeError, ValueError):
            secs = 3.0
        secs = min(max(secs, 0.5), 30.0) if math.isfinite(secs) else 3.0
        out = await run(firmware.uart_capture, ctx.tc, elf, ctx.board, secs)
        return _ok({"seconds": secs, "uart": out[-8000:]})

    tools = {t.name: t for t in [board_info, build, size_report, static_analysis, run_ztest, simulate, uart_log]}
    return create_sdk_mcp_server("fw", tools=list(tools.values())), tools


def mcp_names(names: list[str]) -> list[str]:
    return [f"mcp__fw__{n}" for n in names]
