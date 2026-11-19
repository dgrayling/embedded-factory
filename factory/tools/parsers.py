"""Pure parsers for toolchain output. No container or LLM dependencies, so fully unit-testable."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from factory.schemas import Diagnostic, MemRegion, RobotTest, SimClip, SimEvent

_GCC_RE = re.compile(
    r"^(?P<file>[^:\s][^:]*):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<sev>fatal error|error|warning|note):\s*(?P<msg>.*)$"
)
_LD_UNDEF_RE = re.compile(r"undefined reference to [`'](?P<sym>[^'`]+)'")
_CMAKE_ERR_RE = re.compile(r"^CMake Error(?: at (?P<file>[^:]+):(?P<line>\d+))?.*$")


def parse_gcc_diagnostics(log: str, root_prefix: str | None = None) -> list[Diagnostic]:
    """Extract compiler / linker / CMake diagnostics from a west build log.

    Duplicates (same file/line/message) are collapsed. If ``root_prefix`` is given, it is
    stripped from file paths so personas see workspace-relative paths.
    """
    out: list[Diagnostic] = []
    seen: set[tuple] = set()

    def add(d: Diagnostic) -> None:
        key = (d.file, d.line, d.severity, d.message)
        if key not in seen:
            seen.add(key)
            out.append(d)

    for raw in log.splitlines():
        line = raw.rstrip()
        m = _GCC_RE.match(line)
        if m:
            sev = "error" if m["sev"] in ("error", "fatal error") else m["sev"]
            f = m["file"]
            if root_prefix and f.startswith(root_prefix):
                f = f[len(root_prefix):].lstrip("/")
            add(Diagnostic(
                file=f, line=int(m["line"]),
                column=int(m["col"]) if m["col"] else None,
                severity=sev, message=m["msg"].strip(),
            ))
            continue
        m = _LD_UNDEF_RE.search(line)
        if m:
            add(Diagnostic(severity="error", message=f"linker: undefined reference to '{m['sym']}'"))
            continue
        m = _CMAKE_ERR_RE.match(line)
        if m:
            add(Diagnostic(
                file=m["file"], line=int(m["line"]) if m["line"] else None,
                severity="error", message=line,
            ))
    return out


_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
_MEM_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_]+):\s+(?P<used>[\d.]+)\s*(?P<uu>[KMG]?B)\s+"
    r"(?P<size>[\d.]+)\s*(?P<su>[KMG]?B)\s+(?P<pct>[\d.]+)%"
)


def parse_memory_report(log: str) -> list[MemRegion]:
    """Parse the linker 'Memory region / Used Size / Region Size' table Zephyr prints."""
    regions: dict[str, MemRegion] = {}
    in_table = False
    for line in log.splitlines():
        if "Memory region" in line and "Used Size" in line:
            in_table = True
            regions.clear()  # keep only the last table in the log
            continue
        if in_table:
            m = _MEM_RE.match(line)
            if m:
                regions[m["name"]] = MemRegion(
                    name=m["name"],
                    used_bytes=int(float(m["used"]) * _UNITS[m["uu"]]),
                    size_bytes=int(float(m["size"]) * _UNITS[m["su"]]),
                )
            elif line.strip():
                in_table = False
    return list(regions.values())


def parse_robot_output(xml_text: str) -> list[RobotTest]:
    """Parse a Robot Framework output.xml into per-test results."""
    root = ET.fromstring(xml_text)
    results: list[RobotTest] = []

    def walk(suite: ET.Element, prefix: str) -> None:
        name = suite.get("name", "")
        full = f"{prefix}.{name}" if prefix else name
        for test in suite.findall("test"):
            status = test.find("status")
            passed = status is not None and status.get("status") == "PASS"
            msg = (status.text or "").strip() if status is not None else ""
            results.append(RobotTest(name=test.get("name", ""), suite=full, passed=passed, message=msg))
        for child in suite.findall("suite"):
            walk(child, full)

    top = root.find("suite")
    if top is not None:
        walk(top, "")
    return results


_ZT_CASE = re.compile(r"^\s*(?P<st>PASS|FAIL|SKIP) - (?P<name>\w+) in [\d.]+ seconds")
_ZT_SUITE = re.compile(r"^Running TESTSUITE (?P<suite>\w+)")


def parse_ztest_output(text: str) -> tuple[list[RobotTest], str]:
    """Parse ztest console output. Returns (cases, verdict) with verdict one of
    'passed', 'failed', 'incomplete' (no PROJECT EXECUTION line: crash, hang or timeout)."""
    cases: list[RobotTest] = []
    suite = ""
    pending_assert = ""
    for line in text.splitlines():
        m = _ZT_SUITE.match(line)
        if m:
            suite = m["suite"]
            continue
        if "Assertion failed" in line or "ASSERTION FAIL" in line:
            pending_assert = line.strip()
        m = _ZT_CASE.match(line)
        if m:
            if m["st"] == "SKIP":
                continue
            passed = m["st"] == "PASS"
            cases.append(RobotTest(name=m["name"], suite=suite, passed=passed,
                                   message="" if passed else pending_assert))
            pending_assert = ""
    if "PROJECT EXECUTION SUCCESSFUL" in text:
        verdict = "passed"
    elif "PROJECT EXECUTION FAILED" in text:
        verdict = "failed"
    else:
        verdict = "incomplete"
    return cases, verdict


MAX_CLIP_EVENTS = 3000
MAX_UART_LINE = 240


def parse_sim_trace(trace_dir: Path, suite: str) -> list[SimClip]:
    """Turn the simulation tracer's output (factory/renode/simtrace_listener.py) into clips.

    Times are rebased to the moment the tracer attached. Console bytes are joined into lines,
    each stamped with the time its newline was sent. Malformed lines are skipped: a damaged trace
    loses detail but never breaks the gate that produced it."""
    trace_dir = Path(trace_dir)
    try:
        index = json.loads((trace_dir / "index.json").read_text())
    except (OSError, ValueError):
        return []
    clips = []
    for entry in index if isinstance(index, list) else []:
        if not isinstance(entry, dict):
            continue
        clip = SimClip(suite=suite, test=str(entry.get("name", "")), passed=bool(entry.get("passed")),
                       message=str(entry.get("message", ""))[:500])
        path = trace_dir / f"{entry.get('n')}.jsonl"
        if entry.get("traced") and path.is_file():
            _fill_clip(clip, path.read_text(errors="replace").splitlines())
        clips.append(clip)
    return clips


def _fill_clip(clip: SimClip, lines: list[str]) -> None:
    t0: float | None = None
    end = last = last_tx = 0.0
    line_buf = bytearray()

    def add(t: float, kind: str, ident: str, value) -> None:
        if len(clip.events) >= MAX_CLIP_EVENTS:
            clip.truncated = True
            return
        clip.events.append(SimEvent(t=round(t, 6), kind=kind, id=ident, value=value))

    def flush_uart(t: float) -> None:
        text = line_buf.decode("utf-8", errors="replace").rstrip("\r")
        line_buf.clear()
        add(t, "uart", "uart", text[:MAX_UART_LINE])

    for raw in lines:
        try:
            rec = json.loads(raw)
            k, t = rec.get("k"), rec.get("t")
        except (ValueError, AttributeError):
            continue
        if k == "error":
            clip.errors.append(str(rec.get("msg", ""))[:300])
            continue
        if not isinstance(t, (int, float)):
            continue
        if t0 is None:
            t0 = float(t)
        t = max(0.0, float(t) - t0)
        last = max(last, t)
        if k == "end":
            end = t
        elif k in ("led", "button") and isinstance(rec.get("id"), str):
            add(t, k, rec["id"], 1 if rec.get("v") else 0)
        elif k == "tx" and isinstance(rec.get("c"), int):
            c = rec["c"] & 0xFF
            last_tx = t
            if c == 0x0A:
                flush_uart(t)
            elif len(line_buf) < MAX_UART_LINE * 4:
                line_buf.append(c)
    if line_buf:
        flush_uart(last_tx)
    clip.duration = round(max(end, last), 6)
