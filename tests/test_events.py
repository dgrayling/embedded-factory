import json
from pathlib import Path

import pytest

from factory.events import EventLog, validate

FIXTURE = Path(__file__).parent / "fixtures" / "events" / "demo_run.jsonl"


def test_fixture_matches_python_schema():
    # The same fixture is folded by the Rust dashboard tests (dashboard/src/events.rs).
    lines = [l for l in FIXTURE.read_text().splitlines() if l.strip()]
    assert len(lines) > 20
    for line in lines:
        validate(json.loads(line))


def test_emitter_writes_valid_jsonl(tmp_path):
    log = EventLog(tmp_path / "run1")
    log.run_started("new", "stm32f4_disco", "t", max_iterations=3)
    log.stage_started("firmware_engineer", 1)
    log.gate("g1", False, "1 error", iteration=1, detail={"diagnostics": []})
    log.run_finished(False, "gave up")
    rows = [json.loads(l) for l in log.path.read_text().splitlines()]
    assert [r["type"] for r in rows] == ["run_started", "stage_started", "gate_result", "run_finished"]
    for r in rows:
        validate(r)


@pytest.mark.parametrize("bad", [
    {"ts": 1, "type": "nope"},
    {"ts": 1, "type": "gate_result", "gate": "g9", "ok": True},
    {"ts": 1, "type": "stage_started", "stage": "intern"},
    {"type": "run_finished", "ok": True},
])
def test_validate_rejects(bad):
    with pytest.raises(ValueError):
        validate(bad)
