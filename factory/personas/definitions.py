"""Persona definitions: tools, write scope, budget and structured output per persona."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_MODEL = "claude-opus-5-5"


@dataclass(frozen=True)
class Persona:
    name: str
    builtin_tools: tuple[str, ...]
    mcp_tools: tuple[str, ...] = ()
    write_globs: tuple[str, ...] = ()
    effort: str = "medium"
    max_turns: int = 30
    max_budget_usd: float = 2.0
    output_schema: dict[str, Any] | None = None
    model: str = DEFAULT_MODEL


READ = ("Read", "Glob", "Grep")
WRITE = READ + ("Write", "Edit")

REQUIREMENTS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "requirements", "budgets", "assumptions"],
    "properties": {
        "title": {"type": "string"},
        "requirements": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "text", "acceptance", "verify"],
            "properties": {
                "id": {"type": "string"},
                "text": {"type": "string"},
                "acceptance": {"type": "string"},
                "verify": {"type": "string", "enum": ["robot", "ztest", "review"]},
            }}},
        "budgets": {"type": "object", "additionalProperties": False,
                    "required": ["flash_bytes", "ram_bytes"],
                    "properties": {"flash_bytes": {"type": "integer"}, "ram_bytes": {"type": "integer"}}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}

DESIGN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["design_markdown", "modules"],
    "properties": {
        "design_markdown": {"type": "string"},
        "modules": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["file", "purpose", "interface"],
            "properties": {"file": {"type": "string"}, "purpose": {"type": "string"},
                           "interface": {"type": "string"}}}},
    },
}

DIAGNOSIS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["root_cause", "evidence", "fix", "blame"],
    "properties": {
        "root_cause": {"type": "string"},
        "evidence": {"type": "string"},
        "fix": {"type": "string"},
        "blame": {"type": "string", "enum": ["firmware", "test", "environment"],
                  "description": "Where the defect is. 'test' sends the test back to the Test Engineer, "
                                 "whose amendment a Test Auditor must approve; 'environment' twice escalates "
                                 "to a human. The Firmware Engineer cannot change tests."},
    },
}

AUDIT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["approved", "reasoning", "concerns"],
    "properties": {
        "approved": {"type": "boolean"},
        "reasoning": {"type": "string"},
        "concerns": {"type": "array", "items": {"type": "string"}},
    },
}

REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "findings"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["severity", "file", "line", "message", "requirement"],
            "properties": {
                "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                "file": {"type": "string"}, "line": {"type": "integer"},
                "message": {"type": "string"},
                "requirement": {"type": "string", "description": "REQ id this relates to, or ''"},
            }}},
    },
}

REPORT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["report_markdown", "traceability"],
    "properties": {
        "report_markdown": {"type": "string"},
        "traceability": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["requirement", "verified_by", "status"],
            "properties": {
                "requirement": {"type": "string"},
                "verified_by": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string", "enum": ["verified", "partially", "unverified"]},
            }}},
    },
}

REPO_MAP_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["repo_map_markdown", "board_guess", "has_robot_tests", "has_ztest"],
    "properties": {
        "repo_map_markdown": {"type": "string"},
        "board_guess": {"type": "string"},
        "has_robot_tests": {"type": "boolean"},
        "has_ztest": {"type": "boolean"},
    },
}

FW_GLOBS = ("src/**", "include/**", "boards/**", "dts/**", "prj.conf", "CMakeLists.txt",
            "Kconfig", "app.overlay", "*.overlay", "*.conf")

PERSONAS: dict[str, Persona] = {
    "archaeologist": Persona("archaeologist", READ, ("board_info",), effort="medium", max_turns=25,
                             output_schema=REPO_MAP_SCHEMA),
    "analyst": Persona("analyst", READ, ("board_info",), effort="medium", max_turns=12,
                       output_schema=REQUIREMENTS_SCHEMA),
    "architect": Persona("architect", READ, ("board_info",), effort="high", max_turns=15,
                         output_schema=DESIGN_SCHEMA),
    "test_engineer": Persona("test_engineer", WRITE, ("board_info",), write_globs=("tests/**",),
                             effort="high", max_turns=30, max_budget_usd=3.0),
    "firmware_engineer": Persona("firmware_engineer", WRITE,
                                 ("board_info", "build", "size_report", "static_analysis",
                                  "run_ztest", "simulate", "uart_log"),
                                 write_globs=FW_GLOBS, effort="high", max_turns=60, max_budget_usd=4.0),
    "debugger": Persona("debugger", READ, ("board_info", "build", "uart_log", "run_ztest", "simulate"),
                        effort="high", max_turns=25, output_schema=DIAGNOSIS_SCHEMA),
    "test_auditor": Persona("test_auditor", READ, ("board_info",), effort="high", max_turns=15,
                            output_schema=AUDIT_SCHEMA),
    "reviewer": Persona("reviewer", READ, ("static_analysis", "board_info"), effort="high", max_turns=20,
                        output_schema=REVIEW_SCHEMA),
    "integrator": Persona("integrator", READ, (), effort="low", max_turns=12, output_schema=REPORT_SCHEMA),
}
