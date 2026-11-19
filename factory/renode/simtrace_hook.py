# Renode-side half of the simulation tracer. Renode runs this with `include @file`, inside its
# IronPython 2.7 engine, so it must stay Python 2 compatible (no f-strings, no nonlocal).
#
# simtrace_listener.py writes a copy of this file per test with one line prepended:
#   SIMTRACE = json.loads('{"mode": "attach" | "end", "out": <jsonl path>, "uart": <path>|null,
#                           "leds": {id: path}, "buttons": {id: path}}')
# "attach" subscribes to the board's LED, button and console UART events on the newest machine
# and appends one JSON line per event, stamped with the machine's virtual time in seconds.
# "end" appends the virtual time at which the test body finished.
#
# Nothing here may raise: a failing `include` would fail the Robot test it was injected into,
# and the tracer must never change a gate verdict. Errors are written to the trace instead.
import json


def _simtrace_run(cfg):
    out = open(cfg["out"], "a")

    def write(rec):
        out.write(json.dumps(rec) + "\n")
        out.flush()

    try:
        machines = list(emulationManager.CurrentEmulation.Machines)  # noqa: F821 (Renode global)
        if not machines:
            write({"k": "error", "msg": "no machine to trace"})
            return
        m = machines[-1]

        def now():
            return round(m.ElapsedVirtualTime.TimeElapsed.TotalSeconds, 7)

        if cfg["mode"] == "end":
            write({"k": "end", "t": now()})
            return

        names = {}
        for reg in m.GetRegisteredPeripherals():
            ok, name = m.TryGetAnyName(reg.Peripheral)
            if ok:
                names[name] = reg.Peripheral

        def emit(rec):
            rec["t"] = now()
            write(rec)

        def lookup(path):
            p = names.get(path)
            if p is None:
                write({"k": "error", "msg": "peripheral not found: " + path})
            return p

        emit({"k": "start"})

        def on_led(ident):
            return lambda led, state: emit({"k": "led", "id": ident, "v": 1 if state else 0})

        def on_button(ident):
            return lambda state: emit({"k": "button", "id": ident, "v": 1 if state else 0})

        for ident, path in sorted(cfg.get("leds", {}).items()):
            p = lookup(path)
            if p is not None:
                emit({"k": "led", "id": ident, "v": 1 if p.State else 0})
                p.StateChanged += on_led(ident)
        for ident, path in sorted(cfg.get("buttons", {}).items()):
            p = lookup(path)
            if p is not None:
                emit({"k": "button", "id": ident, "v": 1 if p.Pressed else 0})
                p.StateChanged += on_button(ident)
        if cfg.get("uart"):
            p = lookup(cfg["uart"])
            if p is not None:
                p.CharReceived += lambda c: emit({"k": "tx", "c": int(c)})
    except Exception as e:  # noqa: BLE001 (see the module comment)
        try:
            write({"k": "error", "msg": "%s: %s" % (type(e).__name__, e)})
        except Exception:
            pass


try:
    _simtrace_run(SIMTRACE)  # noqa: F821 (prepended by the listener)
except Exception:
    pass
