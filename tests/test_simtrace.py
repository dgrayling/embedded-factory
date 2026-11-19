"""Simulation tracer: parsing the Renode-side trace files into replay clips, and the event contract."""
import json

import pytest

from factory.events import EventLog, validate
from factory.tools.firmware import simtrace_spec
from factory.tools.parsers import MAX_CLIP_EVENTS, parse_sim_trace


def _write_trace(d, index, traces):
    (d / "index.json").write_text(json.dumps(index))
    for n, recs in traces.items():
        (d / f"{n}.jsonl").write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in recs))


def _tx(t, text):
    return [{"k": "tx", "c": b, "t": t} for b in text.encode()]


def test_parses_clips_rebased_with_uart_lines(tmp_path):
    _write_trace(tmp_path, [
        {"n": 1, "name": "REQ-1 Boots", "passed": True, "message": "", "traced": True},
        {"n": 2, "name": "REQ-2 Press", "passed": False, "message": "boom", "traced": True},
        {"n": 3, "name": "No emulation", "passed": True, "message": "", "traced": False},
    ], {
        1: [{"k": "start", "t": 10.0}, {"k": "led", "id": "led0", "v": 0, "t": 10.0},
            *_tx(10.001, "hello\r\n"), {"k": "led", "id": "led0", "v": 1, "t": 10.5},
            "{not json", *_tx(10.6, "partial"), {"k": "end", "t": 11.25}],
        2: [{"k": "start", "t": 0.0}, {"k": "error", "msg": "peripheral not found: sysbus.x"},
            {"k": "button", "id": "sw0", "v": 1, "t": 0.3}, {"k": "button", "id": "sw0", "v": 0, "t": 0.30001}],
    })
    c1, c2, c3 = parse_sim_trace(tmp_path, "acceptance")

    assert (c1.suite, c1.test, c1.passed, c1.duration) == ("acceptance", "REQ-1 Boots", True, 1.25)
    assert [(e.t, e.kind, e.value) for e in c1.events] == [
        (0.0, "led", 0), (0.001, "uart", "hello"), (0.5, "led", 1), (0.6, "uart", "partial")]

    assert (c2.passed, c2.message, c2.errors) == (False, "boom", ["peripheral not found: sysbus.x"])
    assert [e.value for e in c2.events] == [1, 0]
    assert c2.duration == pytest.approx(0.30001)

    assert c3.events == [] and c3.test == "No emulation"


def test_missing_or_corrupt_index_gives_no_clips(tmp_path):
    assert parse_sim_trace(tmp_path, "s") == []
    (tmp_path / "index.json").write_text("{oops")
    assert parse_sim_trace(tmp_path, "s") == []


def test_event_cap_marks_clip_truncated(tmp_path):
    recs = [{"k": "start", "t": 0}] + [{"k": "led", "id": "led0", "v": i % 2, "t": i * 1e-3}
                                       for i in range(MAX_CLIP_EVENTS + 50)]
    _write_trace(tmp_path, [{"n": 1, "name": "spam", "passed": True, "traced": True}], {1: recs})
    (clip,) = parse_sim_trace(tmp_path, "s")
    assert clip.truncated and len(clip.events) == MAX_CLIP_EVENTS
    assert clip.duration == pytest.approx((MAX_CLIP_EVENTS + 49) * 1e-3)


def test_spec_comes_from_board_registry():
    assert simtrace_spec("stm32f4_disco") == {
        "uart": "sysbus.usart2", "leds": {"led0": "sysbus.gpioPortD.UserLED"},
        "buttons": {"sw0": "sysbus.gpioPortA.UserButton"}}
    assert simtrace_spec("hifive1")["buttons"] == {}


def test_sim_trace_event_contract(tmp_path):
    log = EventLog(tmp_path)
    ev = log.sim_trace("g5", "stm32f4_disco", [{"test": "t", "events": []}], iteration=2)
    validate(json.loads(log.path.read_text()))
    assert ev["iteration"] == 2
    with pytest.raises(ValueError):
        validate({"ts": 1, "type": "sim_trace", "gate": "g9", "board": "b", "clips": []})
    with pytest.raises(ValueError):
        validate({"ts": 1, "type": "sim_trace", "gate": "g5", "board": "b", "clips": {}})
