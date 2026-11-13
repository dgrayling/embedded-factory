IMAGE ?= embedded-factory/toolchain:dev
TC    := FACTORY_IMAGE=$(IMAGE) ./scripts/tc.sh
BOARD ?= stm32f4_disco
APP   ?= templates/zephyr-app
BUILD ?= build/smoke-$(subst /,_,$(BOARD))

.PHONY: image workspace shell smoke-build sim-smoke gates dash dash-demo test clean

## Build the toolchain image (Zephyr SDK + Renode + analysers)
image:
	docker build -t $(IMAGE) docker

## One-time: create the pinned Zephyr west workspace in the factory-west volume
workspace:
	$(TC) west-init.sh

## Interactive shell inside the toolchain container
shell:
	$(TC) bash

## Build the template app for $(BOARD)
smoke-build:
	$(TC) "west build -p auto -b $(BOARD) -d $(BUILD) $(APP)"

## P0 exit criterion: build the template and pass its Renode robot tests
sim-smoke: smoke-build
	$(TC) "cd /work/$(BUILD) && renode-test -r /work/$(BUILD)/robot --variable ELF:/work/$(BUILD)/zephyr/zephyr.elf /work/$(APP)/tests/robot/smoke.robot"

## Run the deterministic gates on the template app (no LLM); results appear in the dashboard
gates:
	.venv/bin/factory gates --board $(BOARD)

## Live dashboard on http://127.0.0.1:7878 (watches runs/)
dash:
	cargo run --release --manifest-path dashboard/Cargo.toml -- serve

## Dashboard plus a replay of the recorded demo run
dash-demo:
	cargo run --release --manifest-path dashboard/Cargo.toml -- serve --demo

## All tests: Python (tool layer, event contract) and Rust (dashboard)
test:
	.venv/bin/pytest -q
	cargo test --manifest-path dashboard/Cargo.toml

clean:
	rm -rf build
