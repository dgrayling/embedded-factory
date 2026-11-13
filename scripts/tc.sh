#!/usr/bin/env bash
# Run a command inside the factory toolchain container.
# The repo is mounted at /work and the west workspace volume at /west.
set -euo pipefail
IMAGE="${FACTORY_IMAGE:-embedded-factory/toolchain:dev}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TTY_FLAGS=""
[ -t 0 ] && [ -t 1 ] && TTY_FLAGS="-it"
exec docker run --rm ${TTY_FLAGS} \
  -v factory-west:/west \
  -v "${ROOT}:/work" \
  -w /work \
  "${IMAGE}" bash -lc "$*"
