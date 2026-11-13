# embedded-factory

An LLM persona pipeline that produces Zephyr firmware and only accepts it once it builds, fits
its memory budget and passes behavioural tests in Renode simulation.

| Part | Language | Role |
|---|---|---|
| `factory/` | Python | Orchestrator, personas (Claude Agent SDK), gate tools, event emitter |
| `dashboard/` (`factory-dash`) | Rust | Live dashboard: tails `runs/*/events.jsonl`, streams state over SSE, single binary with the UI embedded |
| `docker/` | Dockerfile | Toolchain image: Zephyr SDK + west + twister, Renode 1.17 + renode-test, cppcheck |

Python stays where the Agent SDK lives. Rust does the long-running, concurrent part (file tailing,
state folding, fan-out to browsers). The two never call each other. Their only contract is the
append-only event log.

## Quick start

```sh
make image        # build the toolchain image (large: ~35 GB, includes every Zephyr toolchain)
make workspace    # one-time pinned Zephyr v4.5.0 checkout into the factory-west volume
make sim-smoke    # P0 check: build for stm32f4_disco, run Robot tests in Renode

python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
make gates        # run real gates on the template app, emitting events
make dash         # http://127.0.0.1:7878  (overview; per-run inspector at /runs)
make dash-demo    # same, plus a replay of a recorded 3-iteration run
make test         # pytest + cargo test
```

## Dashboard

`make dash` serves two views from one binary:

- **Overview** (`/`): what the whole factory is doing right now. One card per unfinished run
  shows the current persona or gate, time in that step, a pipeline progress strip, the iteration
  count, cost so far and the latest tool call or note. Below the cards: a live activity feed across
  all runs, persona load (who is busy, sessions, wall time, cost), board lanes, pipeline runs finished
  per day, first-try pass rate per gate, and recent results.
- **Runs** (`/runs#<run-id>`): the per-run inspector, with the pipeline graph, timeline and gate detail.

A run with no `run_finished` that has been silent longer than `--stall-after` seconds (default 600)
is shown as **stalled**: the process most likely crashed, was killed or ran out of quota. It doesn't
count as busy. When bound to a loopback address, the server only answers requests whose `Host` is
`localhost`, `*.localhost`, `127.0.0.1` or `[::1]`, which blocks DNS-rebinding reads.
The overview's data is also available as JSON at `/api/overview` and as SSE at `/api/overview/stream`.

## Running the factory

```sh
factory new    --spec benchmarks/specs/01_blinky.md --board stm32f4_disco
factory change --repo benchmarks/repos/zephyr-blinky \
               --request benchmarks/changes/blinky_add_button_pause.md --board stm32f4_disco
factory bench  --board stm32f4_disco [--only 01,02]   # writes benchmarks/results/bench-*.md
factory gates  --board nrf52840dk --app path/to/app    # gates only, no model calls
factory clean  [--older-than 7] [--runs] [--dry-run]  # free disk space under runs/
```

`factory clean` (or `make clean-runs`) removes only the regenerable build trees (`ws/build*`) by
default, so logs, ledgers, reports and workspaces stay and the dashboard still shows every run.
`--runs` deletes whole run directories instead. A run with no `run_finished` that was active in the
last hour is always skipped.

Each run gets `runs/<timestamp>-<slug>/` with `events.jsonl` (what the dashboard shows),
`ledger.jsonl` (tokens and cost per persona call), `report.md` and the workspace `ws/`. The workspace is a
git repo with one commit per step, and its `.factory/` folder holds requirements, design, locked
test hashes, debugger diagnoses and the review.

**Pipeline.** Analyst → Architect → Test Engineer → tests locked → then a loop of Firmware
Engineer → gates G1–G5 → Debugger, until all gates pass → Reviewer (any high finding sends the
run back) → Integrator. In change mode, an Archaeologist maps the repo first, and the baseline
gate results become "known failures" that do not count against the change.
When the Debugger blames a locked test, the Test Engineer may amend it (or reply `NO CHANGE`).
A Test Auditor then approves the diff only if it fixes a test that contradicts the requirement text
without weakening coverage. Approved amendments are re-locked and the gates re-run; rejected ones are
reverted. There are at most 2 amendments per run, each saved as `.factory/test-amendment-N.json`.
A run escalates instead of looping when the Debugger blames the environment twice in a row (or the
tests, once amendments are rejected or used up), when it reaches the iteration cap, or when it
reaches the cost cap.

**Isolation.** Personas have no shell. Their file tools are confined to the workspace, and the write
scope is set per persona. Builds still execute persona-written CMake, so the toolchain container
mounts the repo and the `factory-west` volume read-only, and only `runs/` is writable. The locked
test hashes are kept in memory and re-checked after every gate run. If tests changed during a build,
they are restored and that iteration's G4/G5 results are discarded. The container is recreated
automatically when its mount configuration changes.

**About the cost figures.** Every `$` amount (CLI, ledger, bench reports, dashboard, `--max-cost`) is
the `total_cost_usd` the Agent SDK reports, which is priced at API list rates. With no
`ANTHROPIC_API_KEY` set, the SDK uses your Claude Code subscription login. Runs then use up
plan usage (and can hit the 5-hour limit) instead of being billed per token, so read the figure as a
consistent measure of how heavy a run was, not as money spent.

| Gate | Check |
|---|---|
| G1 | `west build` with no errors |
| G2 | FLASH/RAM within the Analyst's budget (capped at 75% of the board's memory) |
| G3 | cppcheck reports no errors (warnings go to the Reviewer) |
| G4 | ztest suite in `tests/ztest`, built for the target and run **in Renode** |
| G5 | Renode Robot suites in `tests/robot` |

## Boards

| Board | Zephyr target | Status |
|---|---|---|
| STM32F4 Discovery | `stm32f4_disco` | primary: G1–G5 |
| nRF52840 DK | `nrf52840dk/nrf52840` | G1–G5 (LED and button wired in `factory/renode/nrf52840dk.repl`) |
| SiFive HiFive1 | `hifive1` | G1–G5 (no button; only 16 KiB RAM) |
| RP2040 Pico | `rpi_pico` | build-only: Renode 1.17 has no RP2040 model, so G4/G5 are skipped and reported as skipped |

## Event contract

`runs/<id>/events.jsonl`: one JSON object per line, each with `ts` (unix seconds) and `type`.

| type | required fields | optional |
|---|---|---|
| `run_started` | `mode`, `board`, `title` | `max_iterations` |
| `stage_started` | `stage` | `iteration` |
| `stage_finished` | `stage`, `ok` | `iteration`, `summary`, `usage{input_tokens,output_tokens,cost_usd}`, `detail` |
| `gate_result` | `gate` (`g1`..`g5`), `ok` | `iteration`, `summary`, `detail` |
| `tool_call` | `stage`, `tool` | `args` |
| `note` | `stage`, `text` | |
| `artifact_written` | `stage`, `path` | |
| `run_finished` | `ok` | `reason` |

`detail` shapes the dashboard renders specially: `diagnostics[]` (file/line/severity/message),
`tests[]` (Robot results), `regions[]` (memory), `findings[]` (review).

The schema's source of truth is `dashboard/src/events.rs`. `factory/events.py` mirrors it, and
both test suites validate `tests/fixtures/events/demo_run.jsonl`.
`factory-dash check <file>` validates any log.

## Simulation notes

- `factory/renode/stm32f4_disco.repl` overrides Renode's 72 MHz SysTick with the 168 MHz that
  Zephyr configures. Without it, every `k_msleep()` runs 2.33× too slow in simulation and timing
  tests fail for reasons unrelated to the firmware.
- The same file re-adds `gpioPortA pin 0 -> exti@0`. Renode's `stm32f4.repl` later re-declares
  PA0's outputs as timer inputs only, which silently drops the EXTI route. PA0 is the user
  button, so without the fix button interrupts never fire. (This was found by a change run that
  escalated with the Debugger correctly blaming the environment.)
- `factory/renode/hifive1.repl` clocks the CLINT at 32768 Hz, as Zephyr expects. The nRF52840's
  RTC needs no override.
- On the nRF52840 model, the first button press after boot is lost: the GPIO model ignores input
  while the pin is disconnected and doesn't re-read it when the firmware connects the pin. This is
  recorded as a `sim_quirks` entry in `factory/boards.yaml`, which every persona sees, and the
  Test Engineer primes the button once before the first real press.
- Don't run two Renode simulations at once: renode-test uses a fixed port.
- renode-test 1.17 writes `robot_output.xml`, `logs/` and `snapshots/` into its working
  directory, so `simulate()` runs it from `build/robot/`.
