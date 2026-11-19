"""Robot Framework listener that records what the simulated board did during each test.

renode-test loads it with `--listener <this file>:<trace dir>`. The trace dir must contain a
`spec.json` written by factory.tools.firmware.simulate:
    {"uart": <path>|null, "leds": {id: path}, "buttons": {id: path}}

For every test it injects simtrace_hook.py into Renode just before emulation first starts, so the
hook sees the machine the test created, and again when the test teardown begins, to mark the end.
Output per test N: `N.jsonl` (events in virtual time). `index.json` lists each test's name, status
and message, in run order.

It lives in factory/, which the toolchain container mounts read-only, so personas cannot change it.
It must never fail or alter a test: every error is swallowed and the test result is left alone.
"""
import json
import os

from robot.libraries.BuiltIn import BuiltIn

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "simtrace_hook.py")
# Monitor commands that start emulation when a suite does not use the `Start Emulation` keyword.
_START_COMMANDS = ("start", "emulation runfor", "runfor")


class simtrace_listener:  # noqa: N801 (Robot looks up the class named after the module)
    ROBOT_LISTENER_API_VERSION = 2

    def __init__(self, outdir):
        self.outdir = outdir
        self.n = 0
        self.index = []
        self.active = self.attached = self.ended = self.busy = False
        try:
            with open(os.path.join(outdir, "spec.json")) as f:
                self.spec = json.load(f)
            with open(HOOK) as f:
                self.hook_src = f.read()
        except Exception:
            self.spec = None

    def start_test(self, name, attrs):
        self.n += 1
        self.active = self.spec is not None
        self.attached = self.ended = False

    def start_keyword(self, name, attrs):
        if not self.active or self.busy:
            return
        kw = attrs.get("kwname", "")
        if not self.attached:
            args = attrs.get("args") or []
            cmd = str(args[0]).strip().lower() if args else ""
            if kw == "Start Emulation" or (kw == "Execute Command" and cmd.startswith(_START_COMMANDS)):
                self.attached = True
                self._inject("attach")
        elif not self.ended and attrs.get("type") == "TEARDOWN":
            self.ended = True
            self._inject("end")

    def end_test(self, name, attrs):
        self.active = False
        self.index.append({"n": self.n, "name": name, "passed": attrs.get("status") == "PASS",
                           "message": attrs.get("message", ""), "traced": self.attached})
        try:
            with open(os.path.join(self.outdir, "index.json"), "w") as f:
                json.dump(self.index, f)
        except Exception:
            pass

    def _inject(self, mode):
        self.busy = True
        try:
            cfg = dict(self.spec, mode=mode, out=os.path.join(self.outdir, "%d.jsonl" % self.n))
            script = os.path.join(self.outdir, "%d-%s.py" % (self.n, mode))
            with open(script, "w") as f:
                # A JSON string literal, not a dict literal: JSON's null/true are not Python.
                f.write("import json\nSIMTRACE = json.loads(%r)\n%s" % (json.dumps(cfg), self.hook_src))
            BuiltIn().run_keyword("Execute Command", "include @" + script)
        except Exception:
            pass
        finally:
            self.busy = False
