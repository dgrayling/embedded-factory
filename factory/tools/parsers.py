"""Pure parsers for toolchain output. No container or LLM dependencies, so fully unit-testable."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from factory.schemas import Diagnostic, MemRegion, RobotTest

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
