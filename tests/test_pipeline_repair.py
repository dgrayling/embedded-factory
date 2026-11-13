"""Pipeline transitions for the test-repair path, with personas and gates mocked (no model, no Docker)."""
import asyncio
import json

from factory.gates import GateReport
from factory.personas.runtime import PersonaResult
from factory.pipeline import Pipeline
from factory.schemas import GateResult
from factory.tools.container import Toolchain

ROBOT = "tests/robot/acceptance.robot"
FAIL = GateReport(ok=False, results=[GateResult(gate="g5", ok=False, summary="0/1 Robot tests passed",
                                                detail={"tests": [{"name": "REQ-1", "passed": False, "message": "x"}]})])
PASS = GateReport(ok=True, results=[GateResult(gate="g5", ok=True, summary="1/1 Robot tests passed", detail={})])


def make_pipeline(tmp_path, *, gates, te_amend, auditor_approves):
    p = Pipeline(tmp_path / "run", "stm32f4_disco", max_iterations=4, tc=Toolchain())
    calls: list[str] = []
    gate_seq = iter(gates)

    async def stage(name, prompt, iteration=0, resume=None, summarise=None):
        calls.append(name)
        ws = p.ws
        s = None
        text = "done"
        if name == "analyst":
            s = {"requirements": [{"id": "REQ-1", "verify": "robot"}],
                 "budgets": {"flash_bytes": 65536, "ram_bytes": 16384}}
        elif name == "architect":
            s = {"design_markdown": "d", "modules": [{"file": "src/main.c", "purpose": "p", "interface": "i"}]}
        elif name == "test_engineer":
            if prompt.startswith("Test amendment"):
                if te_amend:
                    ws.write(ROBOT, "*** Test Cases ***\nREQ-1\n    Log  fixed\n")
                else:
                    text = "NO CHANGE: the test matches REQ-1"
            else:
                ws.write(ROBOT, "*** Test Cases ***\nREQ-1\n    Log  original\n")
        elif name == "firmware_engineer":
            ws.write("src/main.c", f"/* {prompt[:12]} */\n")
        elif name == "debugger":
            s = {"root_cause": "acceptance contradicts REQ text", "evidence": "e", "fix": "f", "blame": "test"}
        elif name == "test_auditor":
            s = {"approved": auditor_approves, "reasoning": "r", "concerns": [] if auditor_approves else ["weaker"]}
        elif name == "reviewer":
            s = {"summary": "fine", "findings": []}
        elif name == "integrator":
            s = {"report_markdown": "# report", "traceability": []}
        return PersonaResult(ok=True, text=text, structured=s)

    p.stage = stage
    p.gates = lambda iteration: next(gate_seq)
    return p, calls


def run(p):
    return asyncio.run(p.run_new("spec", "t"))


def test_approved_amendment_is_relocked_and_regated(tmp_path):
    p, calls = make_pipeline(tmp_path, gates=[FAIL, PASS], te_amend=True, auditor_approves=True)
    o = run(p)
    assert o.ok, o.reason
    assert calls.count("firmware_engineer") == 1          # re-gated without another firmware round
    assert calls.index("test_auditor") < calls.index("reviewer")
    assert "Log  fixed" in p.ws.read(ROBOT)
    assert p.ws.tests_tampered() == []                    # new content is what is locked now
    rec = json.loads(p.ws.read(".factory/test-amendment-1.json"))
    assert rec["approved"] and rec["files"] == [ROBOT] and "fixed" in rec["diff"]


def test_rejected_amendments_are_reverted_then_escalate(tmp_path):
    p, calls = make_pipeline(tmp_path, gates=[FAIL] * 4, te_amend=True, auditor_approves=False)
    o = run(p)
    assert not o.ok and o.reason.startswith("escalated")
    assert calls.count("test_auditor") == 2
    assert "Log  original" in p.ws.read(ROBOT)
    assert p.ws.tests_tampered() == []


def test_no_change_reply_goes_back_to_firmware(tmp_path):
    p, calls = make_pipeline(tmp_path, gates=[FAIL, PASS], te_amend=False, auditor_approves=True)
    o = run(p)
    assert o.ok, o.reason
    assert "test_auditor" not in calls
    assert calls.count("firmware_engineer") == 2
    assert "Log  original" in p.ws.read(ROBOT)

