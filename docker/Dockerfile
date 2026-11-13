# Toolchain image for the embedded factory: Zephyr SDK + west + twister (from Zephyr CI)
# plus Renode, renode-test (Robot Framework) and static analysis tools.
FROM ghcr.io/zephyrproject-rtos/ci:v0.30.0

ARG RENODE_VERSION=1.17.0
ARG TARGETARCH

USER root

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates cppcheck clang-tidy \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH}" in \
      arm64) f="renode-${RENODE_VERSION}.linux-arm64-portable.tar.gz" ;; \
      amd64) f="renode-${RENODE_VERSION}.linux-portable.tar.gz" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    mkdir -p /opt/renode; \
    curl -fsSL "https://github.com/renode/renode/releases/download/v${RENODE_VERSION}/${f}" \
      | tar xz -C /opt/renode --strip-components=1; \
    pip3 install --no-cache-dir -r /opt/renode/tests/requirements.txt

ENV PATH=/opt/renode:${PATH} \
    ZEPHYR_BASE=/west/zephyr \
    ZEPHYR_VERSION=v4.5.0

COPY west-init.sh /usr/local/bin/west-init.sh
RUN chmod +x /usr/local/bin/west-init.sh

WORKDIR /work
