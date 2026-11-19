"""Gate tools: build, size check, static analysis, ztest-in-Renode, Renode Robot suites.

Each function runs in the toolchain container and returns a structured result.
Workspace layout (``ws``): Zephyr app at the root, ``tests/robot/*.robot``,
optional ztest app at ``tests/ztest`` (its own CMakeLists.txt / prj.conf).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from factory.schemas import BuildResult, Diagnostic, MemRegion, SimResult, SizeResult
from factory.tools.board import board_info
from factory.tools.container import REPO_ROOT, Toolchain, q
from factory.tools.parsers import (
    parse_gcc_diagnostics, parse_memory_report, parse_robot_output, parse_sim_trace, parse_ztest_output,
)

LOG_TAIL = 4000
SIMTRACE_LISTENER = REPO_ROOT / "factory" / "renode" / "simtrace_listener.py"
REGION_ALIASES = {"FLASH": ("ROM", "rom", "FLASH_CODE")}


def _tail(s: str, n: int = LOG_TAIL) -> str:
    return s[-n:]


def _west_build(tc: Toolchain, src: Path, build_dir: Path, board: str, pristine: bool) -> BuildResult:
    info = board_info(board)
    res = tc.exec(
        f"west build -p {'always' if pristine else 'auto'} -b {q(info['zephyr_board'])} "
        f"-d {q(tc.to_container(build_dir))} {q(tc.to_container(src))}"
    )
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "factory-build.log").write_text(res.output)
    # The linker prints its memory table only when it relinks. A no-op rebuild (e.g. G1 right after the
    # Firmware Engineer built the same code) prints nothing, so keep the last table in its own file;
    # any relink overwrites it, so it always describes the current zephyr.elf.
    if res.ok and parse_memory_report(res.output):
        (build_dir / "memory-report.txt").write_text(res.output)
    # Strip the workspace prefix so personas see paths relative to their workspace root.
    ws_root = tc.to_container(src if src.name != "ztest" else src.parent.parent)
    return BuildResult(
        ok=res.ok, board=board,
        diagnostics=parse_gcc_diagnostics(res.output, root_prefix=ws_root),
        log_tail="" if res.ok else _tail(res.output),
    )


def build(tc: Toolchain, app_dir: Path, board: str, pristine: bool = False) -> BuildResult:
    return _west_build(tc, Path(app_dir), Path(app_dir) / "build", board, pristine)


def regions_by_name(regions: list[MemRegion]) -> dict[str, MemRegion]:
    """Regions keyed by name, with canonical aliases added: linker scripts name the code region
    differently per architecture (FLASH on Cortex-M, ROM on FE310)."""
    by_name = {r.name: r for r in regions}
    for canonical, aliases in REGION_ALIASES.items():
        for alias in aliases:
            if canonical not in by_name and alias in by_name:
                by_name[canonical] = by_name[alias]
    return by_name


def size_check(app_dir: Path, budgets: dict[str, int] | None = None) -> SizeResult:
    """Check memory usage from the last build log against byte budgets, e.g. {"FLASH": 65536}."""
    log_path = Path(app_dir) / "build" / "memory-report.txt"
    log = log_path.read_text() if log_path.exists() else ""
    regions = parse_memory_report(log)
    violations = []
    by_name = regions_by_name(regions)
    for name, limit in (budgets or {}).items():
        r = by_name.get(name)
        if r is None:
            violations.append(f"{name}: region not found in memory report")
        elif r.used_bytes > limit:
            violations.append(f"{name}: {r.used_bytes} B used > budget {limit} B")
    if not regions:
        violations.append("no memory report in build log (did G1 run?)")
    return SizeResult(ok=not violations, regions=regions, violations=violations)


def static_analysis(tc: Toolchain, app_dir: Path) -> list[Diagnostic]:
    app = tc.to_container(app_dir)
    out = q(f"{app}/build/cppcheck.txt")
    tmpl = "{file}:{line}:{column}: {severity}: {message} [{id}]"
    # Clear the previous report first: if cppcheck cannot run, a stale file must not be read as current.
    res = tc.exec(
        f"mkdir -p {q(app + '/build')} && rm -f {out} && "
        f"cppcheck --quiet --enable=warning,style,performance,portability --inline-suppr "
        f"--suppress=missingIncludeSystem --suppress=missingInclude --suppress=unusedFunction "
        f"--template={q(tmpl)} --output-file={out} {q(app + '/src')}; "
        f"cat {out}"
    )
    text = res.output
    for sev in ("style", "performance", "portability", "information"):
        text = text.replace(f": {sev}: ", ": warning: ")
    return parse_gcc_diagnostics(text, root_prefix=app)


def uart_capture(tc: Toolchain, elf: Path, board: str, seconds: float = 3.0) -> str:
    """Boot an ELF in Renode headless for `seconds` of virtual time and return console UART output."""
    info = board_info(board)
    if not info.get("renode_platform"):
        raise ValueError(f"{board} has no Renode model")
    out = elf.parent / "uart.txt"
    out.unlink(missing_ok=True)
    script = (
        f"mach create; machine LoadPlatformDescription {info['renode_platform']}; "
        f"sysbus LoadELF @{tc.to_container(elf)}; "
        f"{info['console_uart']} CreateFileBackend @{tc.to_container(out)} true; "
        f'emulation RunFor "{seconds}"; quit'
    )
    tc.exec(f"cd /tmp && timeout {int(seconds * 20) + 60} renode --disable-gui --plain --console "
            f"-e {q(script)} >/dev/null 2>&1")
    return out.read_text(errors="replace") if out.exists() else ""


def run_ztest(tc: Toolchain, app_dir: Path, board: str, seconds: float = 10.0) -> SimResult | None:
    """Build tests/ztest for the target board and run it in Renode. None if there is no ztest app."""
    src = Path(app_dir) / "tests" / "ztest"
    if not (src / "CMakeLists.txt").exists():
        return None
    b = _west_build(tc, src, Path(app_dir) / "build" / "ztest", board, pristine=False)
    if not b.ok:
        errs = "; ".join(f"{d.file}:{d.line}: {d.message}" for d in b.errors[:5]) or "see log"
        return SimResult(ok=False, tests=[], log_tail=f"ztest build failed: {errs}\n{b.log_tail[-1500:]}")
    out = uart_capture(tc, Path(app_dir) / "build" / "ztest" / "zephyr" / "zephyr.elf", board, seconds)
    cases, verdict = parse_ztest_output(out)
    return SimResult(ok=verdict == "passed" and bool(cases), tests=cases,
                     log_tail="" if verdict == "passed" else f"verdict={verdict}\n{_tail(out, 2500)}")


def robot_board_vars(board: str) -> str:
    """Board-specific Robot variables, so one suite runs on any simulated board:
    ${PLATFORM}, ${UART}, ${LED} (led0) and ${BUTTON} (sw0) when the board has them."""
    info = board_info(board)
    pairs = {
        "PLATFORM": info.get("renode_platform"),
        "UART": info.get("console_uart"),
        "LED": (info.get("leds") or {}).get("led0"),
        "BUTTON": (info.get("buttons") or {}).get("sw0"),
    }
    return "".join(f"--variable {q(k + ':' + v)} " for k, v in pairs.items() if v)


def simtrace_spec(board: str) -> dict:
    """Which peripherals the simulation tracer records: the board's console UART, LEDs and buttons."""
    info = board_info(board)
    return {"uart": info.get("console_uart"), "leds": dict(info.get("leds") or {}),
            "buttons": dict(info.get("buttons") or {})}


def simulate(tc: Toolchain, app_dir: Path, suite: Path, board: str, timeout: float = 600) -> SimResult:
    app = tc.to_container(app_dir)
    # renode-test 1.17 writes robot_output.xml, logs/ and snapshots/ into its cwd (it does not
    # honour -r for these), so run it from a dedicated, freshly cleared results directory.
    host_out = Path(app_dir) / "build" / "robot"
    if host_out.exists():
        shutil.rmtree(host_out)
    host_out.mkdir(parents=True)
    # The tracer listener records LED/button/UART activity per test for the dashboard's replay.
    # It only observes: it cannot change a test's outcome.
    trace_dir = host_out / "simtrace"
    trace_dir.mkdir()
    (trace_dir / "spec.json").write_text(json.dumps(simtrace_spec(board)))
    listener = f"{tc.to_container(SIMTRACE_LISTENER)}:{tc.to_container(trace_dir)}"
    res = tc.exec(
        f"cd {q(app + '/build/robot')} && renode-test --listener {q(listener)} "
        f"--variable {q('ELF:' + app + '/build/zephyr/zephyr.elf')} "
        f"{robot_board_vars(board)}{q(tc.to_container(suite))}",
        timeout=timeout,
    )
    xml_path = host_out / "robot_output.xml"
    tests = parse_robot_output(xml_path.read_text()) if xml_path.exists() else []
    ok = res.ok and bool(tests) and all(t.passed for t in tests)
    return SimResult(ok=ok, tests=tests, log_tail="" if ok else _tail(res.output),
                     clips=parse_sim_trace(trace_dir, suite.stem))


def simulate_all(tc: Toolchain, app_dir: Path, board: str) -> SimResult | None:
    """Run every tests/robot/*.robot suite; None if there are none."""
    suites = sorted((Path(app_dir) / "tests" / "robot").glob("*.robot"))
    if not suites:
        return None
    tests, tails, clips, ok = [], [], [], True
    for s in suites:
        r = simulate(tc, app_dir, s, board)
        tests += r.tests
        clips += r.clips
        ok &= r.ok
        if r.log_tail:
            tails.append(f"[{s.name}]\n{r.log_tail[-1500:]}")
    return SimResult(ok=ok, tests=tests, log_tail="\n".join(tails), clips=clips)
