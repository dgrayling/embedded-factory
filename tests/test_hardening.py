"""Regression tests for the integrity and robustness fixes (no model, no Docker)."""
import asyncio
import json
import subprocess

from test_guard import _decide
from test_pipeline_repair import PASS, ROBOT, make_pipeline

import factory.pipeline as pipeline_mod
from factory.gates import GateReport
from factory.personas.definitions import PERSONAS
from factory.personas.runtime import make_guard
from factory.pipeline import BudgetExceeded, new_run_dir
from factory.tools import container
from factory.workspace import Workspace


def test_guard_rejects_patterns_that_escape_the_workspace(tmp_path):
    g = make_guard(tmp_path, PERSONAS["reviewer"])
    assert _decide(g, "Glob", pattern="/Users/someone/.ssh/*") == "deny"
    assert _decide(g, "Glob", pattern="../../**/*.pem") == "deny"
    assert _decide(g, "Glob", pattern="~/.aws/*") == "deny"
    assert _decide(g, "Grep", pattern="x", glob="../*") == "deny"
    assert _decide(g, "Read", file_path="~/.ssh/id_rsa") == "deny"
    assert _decide(g, "Glob", pattern="src/**/*.c") == "allow"
    assert _decide(g, "Grep", pattern="..", glob="*.c") == "allow"   # '..' in a regex is fine


def _locked_ws(tmp_path) -> Workspace:
    ws = Workspace(tmp_path / "ws")
    ws.path.mkdir()
    ws.init()
    ws.write(ROBOT, "*** Test Cases ***\nA\n    Log  x\n")
    ws.commit("tests")
    ws.lock_tests()
    ws.commit("lock")
    return ws


def test_rewritten_lock_file_does_not_hide_tampering(tmp_path):
    ws = _locked_ws(tmp_path)
    ws.write(ROBOT, "weakened\n")
    # Something with write access to the workspace (e.g. a build step) re-locks the weakened test.
    forged = {"commit": ws.head(), "files": {ROBOT: "0" * 64}}
    ws.lock_file.write_text(json.dumps(forged))
    assert ws.tests_tampered() == [ROBOT]
    ws.restore_tests()
    assert "Log  x" in ws.read(ROBOT)
    assert json.loads(ws.lock_file.read_text())["files"] != forged["files"]


def test_tests_modified_during_gate_run_invalidate_the_result(tmp_path, monkeypatch):
    p, _ = make_pipeline(tmp_path, gates=[], te_amend=False, auditor_approves=True)
    (tmp_path / "x").mkdir()
    ws = _locked_ws(tmp_path / "x")
    p.ws = ws

    def fake_run_gates(*a, **k):
        ws.write(ROBOT, "*** Test Cases ***\nA\n    No Operation\n")   # written by persona CMake during G1
        return GateReport(ok=True, results=[r.model_copy(deep=True) for r in PASS.results])

    monkeypatch.setattr(pipeline_mod, "run_gates", fake_run_gates)
    rep = pipeline_mod.Pipeline.gates(p, 1)
    assert not rep.ok
    assert [r.gate for r in rep.failures] == ["g5"] and "invalid" in rep.failures[0].summary
    assert "Log  x" in ws.read(ROBOT)
    assert ws.tests_tampered() == []


def test_cost_cap_at_integrator_keeps_a_passing_verdict(tmp_path):
    p, _ = make_pipeline(tmp_path, gates=[PASS], te_amend=False, auditor_approves=True)
    inner = p.stage

    async def stage(name, prompt, **kw):
        if name == "integrator":
            raise BudgetExceeded("cap reached")
        return await inner(name, prompt, **kw)

    p.stage = stage
    o = asyncio.run(p.run_new("spec", "t"))
    assert o.ok, o.reason
    assert o.iterations == 1
    report = (o.run_dir / "report.md").read_text()
    assert "Integrator did not run" in report and "all gates green" in report


def test_new_run_dir_does_not_collide(tmp_path):
    a = new_run_dir("same", root=tmp_path)
    b = new_run_dir("same", root=tmp_path)
    assert a != b and a.exists() and b.exists()


def test_exec_enforces_timeout_inside_the_container(monkeypatch):
    seen = []

    def fake_run(argv, **kw):
        seen.append((argv, kw))
        return subprocess.CompletedProcess(argv, 124, "partial", "")

    monkeypatch.setattr(container.subprocess, "run", fake_run)
    monkeypatch.setattr(container.Toolchain, "ensure_running", lambda self: None)
    r = container.Toolchain().exec("west build", timeout=60)
    argv, kw = seen[-1]
    assert argv[argv.index("timeout"):argv.index("bash")] == ["timeout", "-k", "10", "60"]
    assert kw["timeout"] > 60
    assert not r.ok and "timed out" in r.output


def test_quote_handles_hostile_file_names():
    assert container.q("tests/robot/a;rm -rf ~.robot") == "'tests/robot/a;rm -rf ~.robot'"
