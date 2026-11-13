"""The factory pipeline: a deterministic state machine that drives personas and gates.

  new:    spec -> Analyst -> Architect -> Test Engineer -> lock -> [FW -> gates -> Debugger]* -> Reviewer -> Integrator
          (Debugger blames a test -> Test Engineer amends -> Test Auditor approves -> re-lock -> re-run gates)
  change: repo -> baseline gates + Archaeologist -> Analyst -> Architect -> Test Engineer (new tests only)
          -> lock -> loop as above, with baseline-passing tests protected
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from factory.events import EventLog
from factory.gates import GateReport, run_gates
from factory.personas.definitions import PERSONAS
from factory.personas.runtime import PersonaResult, run_persona
from factory.schemas import GateResult
from factory.tools.board import board_info, has_simulation
from factory.tools.container import REPO_ROOT, Toolchain
from factory.tools.mcp import ToolContext, build_server
from factory.workspace import FACTORY_DIR, Workspace

ZEPHYR_VERSION = "v4.5.0"
NEW_APP_TEMPLATE = REPO_ROOT / "templates" / "new-app"


MAX_TEST_REPAIRS = 2


class BudgetExceeded(Exception):
    pass


@dataclass
class RunOutcome:
    ok: bool
    reason: str
    iterations: int
    cost_usd: float          # API-list-price equivalent reported by the SDK, not a charge on a subscription
    run_dir: Path
    duration_s: float = 0.0
    history: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


def new_run_dir(slug: str, root: Path | None = None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in slug.lower())[:40].strip("-")
    base = (root or REPO_ROOT / "runs") / f"{stamp}-{safe}"
    d, n = base, 2
    while True:
        try:
            d.mkdir(parents=True)
            return d
        except FileExistsError:   # two runs with the same slug in the same second
            d, n = base.with_name(f"{base.name}-{n}"), n + 1


class Pipeline:
    def __init__(self, run_dir: Path, board: str, *, max_iterations: int = 6, max_cost_usd: float = 30.0,
                 tc: Toolchain | None = None):
        board_info(board)
        self.run_dir = Path(run_dir)
        self.board = board
        self.max_iterations = max_iterations
        self.max_cost = max_cost_usd
        self.ws = Workspace(self.run_dir / "ws")
        self.log = EventLog(self.run_dir)
        self.tc = tc or Toolchain()
        self.budgets: dict[str, int] = {"FLASH": 64 * 1024, "RAM": 16 * 1024}
        self.ctx = ToolContext(self.tc, self.ws.path, board, self.budgets)
        self.server, _ = build_server(self.ctx)
        self.cost = 0.0
        self.tokens_in = 0
        self.tokens_out = 0
        self.history: list[str] = []
        self.requirements: dict = {}
        self.known_failing: set[str] = set()   # change mode: tests already failing in the baseline
        self.started = time.time()
        self.last_gates: GateReport | None = None
        self.review: dict = {}
        self.iteration = 0

    # ---------------------------------------------------------------------------------
    @property
    def facts(self) -> dict[str, str]:
        info = board_info(self.board)
        return {
            "board": self.board,
            "zephyr_version": ZEPHYR_VERSION,
            "board_facts": "\n".join("  " + line for line in yaml.safe_dump(info, sort_keys=False).splitlines()),
            "renode_platform": str(info.get("renode_platform") or "(none: build-only board)"),
            "console_uart": str(info.get("console_uart") or "(none)"),
            "led0": str((info.get("leds") or {}).get("led0") or "(none: board has no led0 in Renode)"),
            "sw0": str((info.get("buttons") or {}).get("sw0") or "(none: board has no user button)"),
        }

    async def stage(self, name: str, prompt: str, iteration: int = 0, resume: str | None = None,
                    summarise=None) -> PersonaResult:
        if self.cost >= self.max_cost:
            raise BudgetExceeded(f"API-equivalent cost ${self.cost:.2f} reached the ${self.max_cost:.2f} cap")
        self.log.stage_started(name, iteration)
        r = await run_persona(PERSONAS[name], prompt, ws=self.ws.path, log=self.log, server=self.server,
                              facts=self.facts, iteration=iteration, resume=resume)
        self.cost += r.cost_usd
        self.tokens_in += r.input_tokens
        self.tokens_out += r.output_tokens
        if r.ok:
            summary = summarise(r) if summarise else (r.text.splitlines()[0][:240] if r.text else "done")
        else:
            summary = f"persona error: {r.error[:200]}"
            if any(k in (r.error or "") for k in ("hit your", "usage limit", "rate limit")):
                self.log.stage_finished(name, False, summary, iteration=iteration, usage=r.usage)
                raise BudgetExceeded(f"Claude usage limit reached during {name}: {r.error[-160:]}")
        detail = r.structured if isinstance(r.structured, dict) and name == "reviewer" else None
        self.log.stage_finished(name, r.ok, summary, iteration=iteration, usage=r.usage, detail=detail)
        with (self.run_dir / "ledger.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "stage": name, "iteration": iteration, **r.usage,
                                "turns": r.turns}) + "\n")
        return r

    def artifact(self, stage: str, rel: str, content: str) -> None:
        self.ws.write(rel, content)
        self.log.artifact(stage, rel)

    def gates(self, iteration: int) -> GateReport:
        rep = run_gates(self.tc, self.ws.path, self.board, self.budgets, log=None, iteration=iteration)
        if self.known_failing:
            for r in rep.results:
                if r.gate in ("g4", "g5") and not r.ok and r.detail.get("tests"):
                    new_fail = [t for t in r.detail["tests"] if not t["passed"] and t["name"] not in self.known_failing]
                    if not new_fail:
                        r.ok = True
                        r.summary += f" (only known baseline failures: {len(self.known_failing)})"
            rep.ok = all(r.ok for r in rep.results)
        # The build runs persona-written CMake that can write to the workspace, so check the lock again
        # after the gates: test results only count if they came from the locked tests.
        tampered = self.ws.tests_tampered()
        if tampered:
            self.ws.restore_tests()
            msg = f"locked tests were modified during the gate run and have been restored: {', '.join(tampered)}"
            self.log.note("firmware_engineer", msg)
            for r in rep.results:
                if r.gate in ("g4", "g5"):
                    r.ok = False
                    r.summary = f"invalid: {msg}"[:300]
            if not any(r.gate in ("g4", "g5") for r in rep.results):
                rep.results.append(GateResult(gate="g5", ok=False, summary=f"invalid: {msg}"[:300]))
            rep.ok = False
        for r in rep.results:
            self.log.gate(r.gate, r.ok, r.summary, iteration=iteration, detail=r.detail)
        self.last_gates = rep
        return rep

    # ---------------------------------------------------------------------------------
    async def run_new(self, spec: str, title: str) -> RunOutcome:
        self.log.run_started("new", self.board, title, max_iterations=self.max_iterations)
        shutil.copytree(NEW_APP_TEMPLATE, self.ws.path)
        self.ws.init()
        self.artifact("analyst", f"{FACTORY_DIR}/spec.md", spec)
        self.ws.commit("factory: spec")
        return await self._run(change_mode=False)

    async def run_change(self, repo: Path, request: str, title: str) -> RunOutcome:
        self.log.run_started("change", self.board, title, max_iterations=self.max_iterations)
        shutil.copytree(repo, self.ws.path, ignore=shutil.ignore_patterns("build", "twister-out*"))
        self.ws.init()
        self.artifact("archaeologist", f"{FACTORY_DIR}/spec.md", request)
        self.ws.commit("factory: change request")
        return await self._run(change_mode=True)

    async def _run(self, change_mode: bool) -> RunOutcome:
        try:
            ok, reason = await self._pipeline(change_mode)
        except BudgetExceeded as e:
            ok, reason = False, str(e)
        except Exception as e:  # keep the event log consistent even on crashes
            ok, reason = False, f"factory error: {type(e).__name__}: {e}"
            self.log.note("integrator", reason)
        self.ws.commit(f"factory: run finished ({'ok' if ok else 'failed'})")
        self.log.run_finished(ok, reason)
        return RunOutcome(ok, reason, self.iteration, round(self.cost, 4), self.run_dir, time.time() - self.started, self.history,
                          self.tokens_in, self.tokens_out)

    async def _pipeline(self, change_mode: bool) -> tuple[bool, str]:
        baseline_tests: list[str] = []
        if change_mode:
            baseline_tests = self.ws.files("tests")
            base = self.gates(iteration=0)
            for r in base.results:
                for t in r.detail.get("tests", []):
                    if not t["passed"]:
                        self.known_failing.add(t["name"])
            self.history.append(f"baseline: {'all gates green' if base.ok else base.feedback()[:300]}")
            r = await self.stage("archaeologist",
                                 "Map this repository for the change request in .factory/spec.md. "
                                 f"Baseline gate results:\n{json.dumps([g.model_dump() for g in base.results], default=str)[:6000]}",
                                 summarise=lambda r: f"board guess {r.structured['board_guess']}; "
                                                     f"robot tests: {r.structured['has_robot_tests']}, ztest: {r.structured['has_ztest']}")
            if not r.ok:
                return False, "archaeologist failed"
            self.artifact("archaeologist", f"{FACTORY_DIR}/repo_map.md", r.structured["repo_map_markdown"])
            self.ws.commit("factory: repo map")

        # Analyst ----------------------------------------------------------------------
        mode_hint = ("This is a CHANGE REQUEST on an existing repo; see .factory/repo_map.md. Requirements cover "
                     "the change only." if change_mode else "Write requirements for new firmware.")
        r = await self.stage("analyst", f"{mode_hint} The spec is in .factory/spec.md.",
                             summarise=lambda r: f"{len(r.structured['requirements'])} requirements: " +
                             ", ".join(f"{q['id']} ({q['verify']})" for q in r.structured["requirements"])[:200])
        if not r.ok:
            return False, "analyst failed"
        self.requirements = r.structured
        b = self.requirements["budgets"]
        self.budgets.update({"FLASH": int(b["flash_bytes"]), "RAM": int(b["ram_bytes"])})
        self.artifact("analyst", f"{FACTORY_DIR}/requirements.yaml", yaml.safe_dump(self.requirements, sort_keys=False))
        self.ws.commit("factory: requirements")

        # Architect --------------------------------------------------------------------
        r = await self.stage("architect",
                             "Design the firmware for .factory/requirements.yaml"
                             + (" as a change to the existing code (see .factory/repo_map.md); keep existing "
                                "behaviour and console lines intact." if change_mode else "."),
                             summarise=lambda r: f"{len(r.structured['modules'])} modules: " +
                             ", ".join(m["file"] for m in r.structured["modules"])[:200])
        if not r.ok:
            return False, "architect failed"
        d = r.structured
        design = d["design_markdown"] + "\n\n## Module interfaces\n\n" + "\n\n".join(
            f"### {m['file']}\n{m['purpose']}\n\n```c\n{m['interface']}\n```" for m in d["modules"])
        self.artifact("architect", f"{FACTORY_DIR}/design.md", design)
        self.ws.commit("factory: design")

        # Test Engineer ----------------------------------------------------------------
        sim = has_simulation(self.board)
        needs_robot = sim and any(q["verify"] == "robot" for q in self.requirements["requirements"])
        te_prompt = ("Write the tests for .factory/requirements.yaml following .factory/design.md."
                     + (" Existing test files must not be modified; add new files only "
                        f"(existing: {', '.join(baseline_tests) or 'none'})." if change_mode else "")
                     + ("" if sim else " This board has no simulator: write ztest only for pure logic; no Robot suites."))
        r = await self.stage("test_engineer", te_prompt)
        if change_mode:
            touched = set(self.ws.git("diff", "--name-only", "HEAD", check=False).splitlines())
            changed = [f for f in baseline_tests if f in touched or not (self.ws.path / f).exists()]
            if changed:
                self.ws.git("checkout", "HEAD", "--", *changed)
                self.log.note("test_engineer", f"Reverted edits to existing tests: {', '.join(changed)}")
        robot = list((self.ws.path / "tests" / "robot").glob("*.robot"))
        if r.ok and needs_robot and not robot:
            r = await self.stage("test_engineer", "No Robot suite was found in tests/robot. Requirements marked "
                                 "`verify: robot` need one. Write tests/robot/acceptance.robot now.", resume=r.session_id)
        if not r.ok:
            return False, "test engineer failed"
        for f in self.ws.files("tests"):
            self.log.artifact("test_engineer", f)
        self.ws.commit("factory: acceptance tests")

        self.log.stage_started("lock_tests")
        hashes, combined = self.ws.lock_tests()
        fw_base = self.ws.lock_commit   # firmware diff base for the Reviewer
        self.ws.commit("factory: lock tests")
        self.log.stage_finished("lock_tests", True, f"Locked {len(hashes)} files, sha256 {combined[:4]}…{combined[-4:]}")

        # Build / verify loop ----------------------------------------------------------
        feedback = ""
        fw_session: str | None = None
        review_rounds = 0
        blame_streak = 0
        test_repairs = 0
        for it in range(1, self.max_iterations + 1):
            self.iteration = it
            prompt = (f"Iteration {it}. Implement the firmware so all gates pass."
                      if it == 1 else f"Iteration {it}. The previous attempt did not pass. Fix it.\n\n{feedback}")
            fw = await self.stage("firmware_engineer", prompt, iteration=it, resume=fw_session,
                                  summarise=lambda r: (r.text.splitlines()[0] if r.text else "done")[:240])
            fw_session = fw.session_id or fw_session
            tampered = self.ws.tests_tampered()
            if tampered:
                self.ws.restore_tests()
                self.log.note("firmware_engineer", f"Locked tests were modified and have been restored: {', '.join(tampered)}")
            self.ws.commit(f"factory: firmware iteration {it}")

            rep = self.gates(it)
            while not rep.ok:   # repeats only when an approved test amendment triggers a re-gate
                failed = ", ".join(f"{g.gate.upper()} ({g.summary})" for g in rep.failures)
                self.history.append(f"iteration {it}: failed {failed}")
                if it == self.max_iterations:
                    break
                dbg = await self.stage(
                    "debugger", f"Gate failure in iteration {it}:\n\n{rep.feedback()}\n\nFind the root cause.",
                    iteration=it, summarise=lambda r: f"[{r.structured['blame']}] {r.structured['root_cause']}"[:240])
                if not dbg.ok:
                    feedback = f"Gate failures:\n{rep.feedback()}"
                    break
                s = dbg.structured
                name = f"{FACTORY_DIR}/diagnosis-{it}.json"
                n = 2
                while (self.ws.path / name).exists():
                    name, n = f"{FACTORY_DIR}/diagnosis-{it}-{n}.json", n + 1
                self.artifact("debugger", name, json.dumps(s, indent=2))
                diagnosis = f"Root cause: {s['root_cause']}\nEvidence: {s['evidence']}\nFix: {s['fix']}"
                feedback = f"Gate failures:\n{rep.feedback()}\n\nDebugger diagnosis ({s['blame']}):\n{diagnosis}"

                if s["blame"] == "test" and test_repairs < MAX_TEST_REPAIRS:
                    test_repairs += 1
                    verdict, note = await self.repair_tests(it, test_repairs, rep, s, baseline_tests, change_mode)
                    if verdict == "amended":
                        blame_streak = 0
                        rep = self.gates(it)
                        continue
                    if verdict == "kept":   # the Test Engineer stands by the test: treat it as a firmware defect
                        blame_streak = 0
                        feedback += f"\n\nThe Test Engineer reviewed the test and kept it unchanged:\n{note}"
                        break
                    blame_streak += 1      # rejected by the auditor
                    feedback += f"\n\nA proposed test amendment was rejected by the Test Auditor:\n{note}"
                else:
                    blame_streak = blame_streak + 1 if s["blame"] != "firmware" else 0
                if blame_streak >= 2:
                    self.history.append(f"iteration {it}: escalated ({s['blame']}). {diagnosis}")
                    await self.integrate(it)
                    return False, f"escalated: debugger attributes the failure to the {s['blame']} twice: {s['root_cause'][:200]}"
                break
            if not rep.ok:
                if it == self.max_iterations:
                    break
                continue

            self.history.append(f"iteration {it}: all gates green")
            rv = await self.stage("reviewer",
                                  "All gates are green. Review the firmware. Diff of firmware changes since the tests were locked:\n"
                                  + self.ws.diff(fw_base,
                                                 ["src", "include", "prj.conf", "boards", "CMakeLists.txt"], 20000),
                                  iteration=it,
                                  summarise=lambda r: r.structured["summary"][:240])
            self.review = rv.structured or {}
            if rv.ok:
                self.artifact("reviewer", f"{FACTORY_DIR}/review.json", json.dumps(self.review, indent=2))
            high = [f for f in self.review.get("findings", []) if f["severity"] == "high"]
            if high and review_rounds < 2 and it < self.max_iterations:
                review_rounds += 1
                self.history.append(f"iteration {it}: reviewer raised {len(high)} high-severity findings")
                feedback = "Gates passed, but the reviewer found high-severity defects. Fix them without breaking tests:\n" + \
                    "\n".join(f"- {f['file']}:{f['line']}: {f['message']}" for f in high)
                continue
            await self.integrate(it)
            if high:
                return True, f"all gates green after {it} iterations; {len(high)} high-severity review findings remain"
            return True, f"all gates green after {it} iteration{'s' if it > 1 else ''}"

        await self.integrate(self.max_iterations)
        return False, f"gates still failing after {self.max_iterations} iterations"

    async def repair_tests(self, it: int, n: int, rep: GateReport, diag: dict, baseline_tests: list[str],
                           change_mode: bool) -> tuple[str, str]:
        """Debugger blamed a locked test: Test Engineer amends it, Test Auditor approves or rejects.
        Returns ("amended" | "kept" | "rejected", explanation)."""
        lock_commit = self.ws.lock_commit
        te = await self.stage(
            "test_engineer",
            f"Test amendment {n}. The Debugger blames a locked test for this gate failure:\n\n{rep.feedback()}\n\n"
            f"Diagnosis:\nRoot cause: {diag['root_cause']}\nEvidence: {diag['evidence']}\nSuggested test fix: {diag['fix']}\n\n"
            "Amend only what is wrong, guided by the requirement text in .factory/requirements.yaml, or reply "
            "`NO CHANGE:` with your reasons if the test is correct. A Test Auditor will review your diff."
            + (f" Pre-existing tests must not be modified: {', '.join(baseline_tests)}." if change_mode and baseline_tests else ""),
            iteration=it, summarise=lambda r: (r.text.strip().splitlines() or ["done"])[0][:240])
        if change_mode and baseline_tests:
            touched = set(self.ws.git("diff", "--name-only", "HEAD", check=False).splitlines())
            reverted = [f for f in baseline_tests if f in touched or not (self.ws.path / f).exists()]
            if reverted:
                self.ws.git("checkout", "HEAD", "--", *reverted)
                self.log.note("test_engineer", f"Reverted edits to pre-existing tests: {', '.join(reverted)}")
        changed = self.ws.tests_tampered()
        if not te.ok or not changed:
            self.ws.restore_tests()
            why = te.text.strip()[-1500:] if te.ok else f"test engineer failed: {te.error[:200]}"
            self.history.append(f"iteration {it}: debugger blamed the tests; test engineer kept them")
            return "kept", why

        self.ws.git("add", "-N", "tests")   # so new files show up in the diff
        diff = self.ws.git("diff", lock_commit, "--", "tests", check=False)[:20000]
        self.ws.git("reset", "-q", "--", "tests", check=False)
        au = await self.stage(
            "test_auditor",
            f"Proposed amendment {n} to the locked tests (diff against the locked version):\n\n```diff\n{diff}\n```\n\n"
            f"Debugger diagnosis that prompted it:\nRoot cause: {diag['root_cause']}\nEvidence: {diag['evidence']}\n"
            f"Suggested fix: {diag['fix']}\n\nTest Engineer's note:\n{te.text.strip()[-2000:]}",
            iteration=it,
            summarise=lambda r: f"{'approved' if r.structured['approved'] else 'rejected'}: {r.structured['reasoning']}"[:240])
        approved = bool(au.ok and au.structured and au.structured["approved"])
        reasoning = au.structured["reasoning"] if au.ok and au.structured else f"auditor failed: {au.error[:200]}"
        concerns = au.structured.get("concerns", []) if au.ok and au.structured else []
        self.artifact("test_auditor", f"{FACTORY_DIR}/test-amendment-{n}.json", json.dumps(
            {"iteration": it, "approved": approved, "files": changed, "diagnosis": diag,
             "reasoning": reasoning, "concerns": concerns, "diff": diff}, indent=2))
        if not approved:
            self.ws.restore_tests()
            self.ws.commit(f"factory: test amendment {n} rejected")
            self.history.append(f"iteration {it}: test amendment {n} rejected by the auditor: {reasoning[:300]}")
            return "rejected", reasoning + "".join(f"\n- {c}" for c in concerns)

        self.ws.commit(f"factory: test amendment {n} ({', '.join(changed)})")
        self.log.stage_started("lock_tests", it)
        hashes, combined = self.ws.lock_tests()
        self.ws.commit(f"factory: re-lock tests after amendment {n}")
        self.log.stage_finished("lock_tests", True, f"Re-locked {len(hashes)} files after audited amendment {n}, "
                                f"sha256 {combined[:4]}…{combined[-4:]}", iteration=it)
        self.history.append(f"iteration {it}: test amendment {n} approved ({', '.join(changed)}): {reasoning[:300]}")
        return "amended", reasoning

    async def integrate(self, it: int) -> None:
        """Write the run report. Never changes the run's verdict: if the Integrator cannot run (cost cap,
        usage limit, persona error), a short deterministic report is written instead."""
        gates = [g.model_dump() for g in self.last_gates.results] if self.last_gates else []
        for g in gates:  # keep the prompt compact
            g["detail"] = {k: v for k, v in g["detail"].items() if k != "log_tail"}
        try:
            r = await self.stage("integrator",
                                 "Write the run report.\n\nRequirements:\n" + yaml.safe_dump(self.requirements, sort_keys=False)
                                 + "\nLatest gate results:\n" + json.dumps(gates, default=str)[:12000]
                                 + "\n\nReview:\n" + json.dumps(self.review)[:6000]
                                 + "\n\nIteration history:\n" + "\n".join(self.history)
                                 + f"\n\nBoard: {self.board}. API-equivalent model cost so far: ${self.cost:.2f}.",
                                 iteration=it,
                                 summarise=lambda r: "{}/{} requirements verified".format(
                                     sum(t["status"] == "verified" for t in r.structured["traceability"]),
                                     len(r.structured["traceability"])))
        except BudgetExceeded as e:
            self.log.note("integrator", f"Integrator skipped: {e}")
            r = None
        if r is not None and r.ok:
            report, trace = r.structured["report_markdown"], r.structured["traceability"]
        else:
            report = self._fallback_report(gates)
            trace = None
        self.artifact("integrator", f"{FACTORY_DIR}/report.md", report)
        (self.run_dir / "report.md").write_text(report)
        if trace is not None:
            (self.run_dir / "traceability.json").write_text(json.dumps(trace, indent=2))

    def _fallback_report(self, gates: list[dict]) -> str:
        lines = [f"# Run report: {self.board}", "",
                 "_The Integrator did not run, so this report was generated without a traceability matrix._", "",
                 "## Gates", ""]
        lines += [f"- {g['gate'].upper()}: {'pass' if g['ok'] else 'FAIL'}: {g['summary']}" for g in gates] or ["- not run"]
        lines += ["", "## Iteration history", ""] + [f"- {h}" for h in self.history]
        lines += ["", f"API-equivalent model cost: ${self.cost:.2f}"]
        return "\n".join(lines) + "\n"
