"""Runs one persona as a Claude Agent SDK session, scoped by tools and file-access hooks."""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage, TextBlock, ToolUseBlock, query,
)

from factory.events import EventLog
from factory.personas.definitions import Persona
from factory.tools.mcp import mcp_names

PROMPTS = Path(__file__).parent / "prompts"


def path_allowed(rel: str, globs: tuple[str, ...]) -> bool:
    """Glob match where patterns without '/' only match files at the workspace root."""
    for g in globs:
        if "/" not in g:
            if "/" not in rel and fnmatch.fnmatch(rel, g):
                return True
        elif fnmatch.fnmatch(rel, g):
            return True
    return False


def resolve_in_ws(ws: Path, p: str) -> str | None:
    """Workspace-relative path, or None if `p` points outside the workspace."""
    try:
        p = str(Path(p).expanduser()) if p.startswith("~") else p
        full = (ws / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()
        rel = full.relative_to(ws.resolve())
    except (ValueError, OSError):
        return None
    return rel.as_posix() if str(rel) != "." else ""


def make_guard(ws: Path, persona: Persona):
    async def guard(inp: dict, tool_use_id: str | None, ctx: Any) -> dict:
        name = inp.get("tool_name", "")
        ti = inp.get("tool_input", {}) or {}
        raw = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""

        def deny(reason: str) -> dict:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}

        # A Glob pattern can itself leave the search root ("/etc/*", "../../**"), so check it too.
        for pat_key in ("pattern", "glob") if name == "Glob" else ("glob",) if name == "Grep" else ():
            pat = str(ti.get(pat_key) or "")
            if Path(pat).is_absolute() or ".." in Path(pat).parts or pat.startswith("~"):
                return deny(f"{pat_key} {pat!r} must be relative to the workspace and must not contain '..'.")
        if not raw and name in ("Glob", "Grep"):
            return {}  # defaults to cwd, which is the workspace
        rel = resolve_in_ws(ws, raw)

        if rel is None:
            return deny(f"{raw} is outside the workspace; only files under {ws} are accessible.")
        if name in ("Write", "Edit", "MultiEdit", "NotebookEdit") and not path_allowed(rel, persona.write_globs):
            scope = ", ".join(persona.write_globs) or "nothing (read-only persona)"
            return deny(f"{rel} is outside your write scope ({scope}).")
        return {}
    return guard


@dataclass
class PersonaResult:
    ok: bool
    text: str = ""
    structured: Any = None
    session_id: str | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    error: str = ""
    denials: list[str] = field(default_factory=list)

    @property
    def usage(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cost_usd": round(self.cost_usd, 5)}


def render_system_prompt(persona: Persona, facts: dict[str, str]) -> str:
    common = (PROMPTS / "_common.md").read_text()
    own = (PROMPTS / f"{persona.name}.md").read_text()
    text = common + "\n\n" + own
    for k, v in facts.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def _summarise_args(name: str, inp: dict) -> str:
    for k in ("file_path", "path", "pattern"):
        if k in inp:
            return str(inp[k]).split("/ws/", 1)[-1]
    return json.dumps(inp)[:80] if inp else ""


async def run_persona(
    persona: Persona,
    prompt: str,
    *,
    ws: Path,
    log: EventLog,
    server: Any,
    facts: dict[str, str],
    iteration: int = 0,
    resume: str | None = None,
) -> PersonaResult:
    allowed = list(persona.builtin_tools) + mcp_names(list(persona.mcp_tools))
    options = ClaudeAgentOptions(
        model=persona.model,
        cwd=str(ws),
        system_prompt=render_system_prompt(persona, facts),
        tools=list(persona.builtin_tools),
        allowed_tools=allowed,
        mcp_servers={"fw": server} if persona.mcp_tools else {},
        permission_mode="dontAsk",
        setting_sources=[],
        effort=persona.effort,
        max_turns=persona.max_turns,
        max_budget_usd=persona.max_budget_usd,
        hooks={"PreToolUse": [HookMatcher(matcher="Read|Write|Edit|MultiEdit|NotebookEdit|Glob|Grep",
                                          hooks=[make_guard(ws, persona)])]},
        output_format={"type": "json_schema", "schema": persona.output_schema} if persona.output_schema else None,
        resume=resume,
    )
    res = PersonaResult(ok=False)
    texts: list[str] = []
    try:
        async for m in query(prompt=prompt, options=options):
            if isinstance(m, AssistantMessage):
                for b in m.content:
                    if isinstance(b, ToolUseBlock):
                        if b.name == "StructuredOutput":
                            continue
                        short = b.name.removeprefix("mcp__fw__")
                        log.tool_call(persona.name, short, _summarise_args(b.name, b.input))
                    elif isinstance(b, TextBlock) and b.text.strip():
                        texts.append(b.text.strip())
                        log.note(persona.name, b.text.strip()[:600])
            elif isinstance(m, ResultMessage):
                u = m.usage or {}
                res.input_tokens = int(u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                                       + u.get("cache_read_input_tokens", 0))
                res.output_tokens = int(u.get("output_tokens", 0))
                res.cost_usd = float(m.total_cost_usd or 0.0)
                res.session_id = m.session_id
                res.turns = m.num_turns
                res.structured = m.structured_output
                res.denials = [str(d) for d in (m.permission_denials or [])]
                res.ok = not m.is_error and (persona.output_schema is None or m.structured_output is not None)
                if m.is_error:
                    res.error = f"{m.subtype}: {m.result or ''} {m.errors or ''}".strip()
                elif persona.output_schema and m.structured_output is None:
                    res.error = "persona finished without producing its structured output"
                if m.result:
                    texts.append(m.result)
    except Exception as e:  # SDK/CLI failures must not kill the pipeline silently
        res.ok = False
        res.error = f"{type(e).__name__}: {e}"
    res.text = texts[-1] if texts else ""
    return res
