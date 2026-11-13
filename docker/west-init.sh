#!/usr/bin/env bash
# Initialise (once) a pinned Zephyr west workspace in /west (a named docker volume).
# Only the HAL modules needed for the factory's boards are fetched.
set -euo pipefail

if [ -f /west/.west/config ]; then
  echo "west workspace already initialised"
  exit 0
fi

cd /west
west init -m https://github.com/zephyrproject-rtos/zephyr --mr "${ZEPHYR_VERSION}" .
west config manifest.project-filter -- '-.*,+cmsis,+cmsis_6,+picolibc,+hal_stm32,+hal_nordic,+hal_rpi_pico'
west update --narrow --fetch-opt=--depth=1
west zephyr-export
echo "west workspace ready at /west (zephyr ${ZEPHYR_VERSION})"
