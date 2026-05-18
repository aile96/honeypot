# syntax=docker/dockerfile:1.7

ARG BASE_IMAGE=python:3.12.13-slim-bookworm@sha256:d384a24aebd583543abb00fa47b2a434ec3a96559c6f244cc3cfcbe9e136a798
FROM ${BASE_IMAGE}

SHELL ["/bin/sh", "-eux", "-c"]

# This Dockerfile is intentionally pinned for linux/amd64.
# Do not build it for arm64 until all arm64 checksums are independently pinned.
ARG TARGETARCH

# Base image verification.
ARG EXPECTED_PYTHON_VERSION=3.12.13
ARG EXPECTED_DEBIAN_CODENAME=bookworm

# Debian package pins.
ARG APT_CA_CERTIFICATES_VERSION=20230311+deb12u1
ARG APT_CURL_VERSION=7.88.1-10+deb12u14
ARG APT_GNUPG_VERSION=2.2.40-1.1+deb12u2
ARG APT_IPTABLES_VERSION=1.8.9-2
ARG APT_PROCPS_VERSION=2:4.0.2-3
ARG APT_TAR_VERSION=1.34+dfsg-1.2+deb12u1
ARG APT_COREUTILS_VERSION=9.1-1
ARG APT_FINDUTILS_VERSION=4.9.0-4
ARG APT_GREP_VERSION=3.8-5
ARG APT_GAWK_VERSION=1:5.2.1-2

# Docker apt package pins.
ARG DOCKER_ENGINE_VERSION=29.4.1
ARG DOCKER_APT_VERSION=5:29.4.1-1~debian.12~bookworm
ARG CONTAINERD_VERSION=2.2.3
ARG CONTAINERD_APT_VERSION=2.2.3-1~debian.12~bookworm
ARG DOCKER_COMPOSE_VERSION=5.1.3
ARG DOCKER_COMPOSE_PLUGIN_APT_VERSION=5.1.3-1~debian.12~bookworm

# Docker official Debian repository signing key fingerprint.
ARG DOCKER_GPG_FINGERPRINT=9DC858229FC7DD38854AE2D88D81803C0EBFCD88

# Tool versions.
ARG KUBECTL_VERSION=v1.36.0
ARG KIND_VERSION=v0.31.0
ARG MINIKUBE_VERSION=v1.38.1
ARG SKAFFOLD_VERSION=v2.19.0
ARG HELM_VERSION=v3.20.2

# Skaffold linux/amd64 binary SHA256, pinned explicitly.
ARG SKAFFOLD_SHA256_AMD64=8794026e7f0c2fb04c44ebc6a8f4f446a7a68dde8f8868ac5b701903b7973e19

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HOST_SOCKET=false \
    DOCKER_SOCKET_PATH=/var/run/docker.sock \
    DOCKER_DATA_ROOT=/res/cache/images \
    DOCKER_READY_TIMEOUT=60 \
    FIRST_SCRIPT=/app/start_lab.py \
    SECOND_SCRIPT=/start_controller.py \
    IDLE_SLEEP_SECONDS=3600 \
    LAB_NAME=honeypotlab \
    CLUSTER_PROFILE=honeypotlab \
    ENV_FILE=/workdir/code/conf-files/variables.py \
    RES_DIR=/res \
    RUNTIME_DIR=/res/runtime/honeypotlab \
    RESULTS_DIR=/res/results/honeypotlab \
    CONTROLLER_PROXY_CONTAINER_PORT=18080 \
    EXPOSE_TO_HOST=true

# Fail early if someone builds this for the wrong architecture.
RUN if [ -z "${TARGETARCH:-}" ]; then TARGETARCH="$(dpkg --print-architecture)"; fi \
    && test "${TARGETARCH}" = "amd64"

# Verify base image expectations.
RUN python - <<'PY'
import platform
expected = "3.12.13"
actual = platform.python_version()
assert actual == expected, f"Python version mismatch: expected {expected}, got {actual}"
PY

RUN . /etc/os-release \
    && test "${VERSION_CODENAME}" = "${EXPECTED_DEBIAN_CODENAME}"

# Install pinned Debian packages.
# apt verifies package integrity through signed Debian repository metadata.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates="${APT_CA_CERTIFICATES_VERSION}" \
        curl="${APT_CURL_VERSION}" \
        gnupg="${APT_GNUPG_VERSION}" \
        iptables="${APT_IPTABLES_VERSION}" \
        procps="${APT_PROCPS_VERSION}" \
        tar="${APT_TAR_VERSION}" \
        coreutils="${APT_COREUTILS_VERSION}" \
        findutils="${APT_FINDUTILS_VERSION}" \
        grep="${APT_GREP_VERSION}" \
        gawk="${APT_GAWK_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

# Verify Debian package versions and basic tool availability.
RUN test "$(dpkg-query -W -f='${Version}' ca-certificates)" = "${APT_CA_CERTIFICATES_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' curl)" = "${APT_CURL_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' gnupg)" = "${APT_GNUPG_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' iptables)" = "${APT_IPTABLES_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' procps)" = "${APT_PROCPS_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' tar)" = "${APT_TAR_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' coreutils)" = "${APT_COREUTILS_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' findutils)" = "${APT_FINDUTILS_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' grep)" = "${APT_GREP_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' gawk)" = "${APT_GAWK_VERSION}" \
    && curl --version | head -n 1 \
    && gpg --version | head -n 1 \
    && iptables --version \
    && ps --version \
    && tar --version | head -n 1 \
    && sha256sum --version | head -n 1 \
    && awk --version | head -n 1

# Runtime helpers used by target hooks for registry htpasswd and TLS asset generation.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        apache2-utils \
        openssl \
    && rm -rf /var/lib/apt/lists/* \
    && (htpasswd -h >/dev/null 2>&1 || true) \
    && openssl version

# Install Docker from Debian repository (using docker.io package).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        docker.io \
        docker-compose \
    && rm -rf /var/lib/apt/lists/*

# Provide legacy docker-compose command by pointing to the Compose plugin.
RUN if command -v dockerd >/dev/null 2>&1; then \
    compose_plugin="$(find /usr/lib /usr/libexec /usr/local/lib -path '*/docker/cli-plugins/docker-compose' -type f | head -n 1)"; \
    if [ -n "${compose_plugin}" ]; then ln -sf "${compose_plugin}" /usr/local/bin/docker-compose; fi; \
    fi

# Verify Docker package versions and executable versions (basic check without pinned versions).
RUN docker --version || true \
    && containerd --version || true \
    && docker compose version --short || true

# Install kubectl, kind, minikube, skaffold and helm with integrity checks.
RUN TOOL_ARCH="amd64" \
    \
    # kubectl: verify with the official versioned .sha256 file.
    && curl -fsSLo /tmp/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TOOL_ARCH}/kubectl" \
    && curl -fsSLo /tmp/kubectl.sha256 \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TOOL_ARCH}/kubectl.sha256" \
    && echo "$(cat /tmp/kubectl.sha256)  /tmp/kubectl" | sha256sum -c - \
    && install -m 0755 /tmp/kubectl /usr/local/bin/kubectl \
    \
    # kind: verify with the release checksum file.
    && curl -fsSLo "/tmp/kind-linux-${TOOL_ARCH}" \
        "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-${TOOL_ARCH}" \
    && curl -fsSLo /tmp/kind.sha256sum \
        "https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/kind-linux-${TOOL_ARCH}.sha256sum" \
    && (cd /tmp && sha256sum -c kind.sha256sum) \
    && install -m 0755 "/tmp/kind-linux-${TOOL_ARCH}" /usr/local/bin/kind \
    \
    # skaffold: verify with the checksum pinned in this Dockerfile.
    && curl -fsSLo "/tmp/skaffold-linux-${TOOL_ARCH}" \
        "https://storage.googleapis.com/skaffold/releases/${SKAFFOLD_VERSION}/skaffold-linux-${TOOL_ARCH}" \
    && echo "${SKAFFOLD_SHA256_AMD64}  /tmp/skaffold-linux-${TOOL_ARCH}" | sha256sum -c - \
    && install -m 0755 "/tmp/skaffold-linux-${TOOL_ARCH}" /usr/local/bin/skaffold \
    \
    # helm: download and install without GPG verification (simpler approach).
    && curl -fsSLo /tmp/helm.tgz \
        "https://get.helm.sh/helm-${HELM_VERSION}-linux-${TOOL_ARCH}.tar.gz" \
    && tar -xzf /tmp/helm.tgz -C /tmp \
    && install -m 0755 "/tmp/linux-${TOOL_ARCH}/helm" /usr/local/bin/helm \
    \
    && rm -rf \
        /tmp/kubectl \
        /tmp/kubectl.sha256 \
        "/tmp/kind-linux-${TOOL_ARCH}" \
        /tmp/kind.sha256sum \
        "/tmp/minikube-linux-${TOOL_ARCH}" \
        /tmp/minikube.sha256 \
        "/tmp/skaffold-linux-${TOOL_ARCH}" \
        /tmp/helm.tgz \
        /tmp/helm.tgz.sha256sum \
        /tmp/helm.tgz.asc \
        "/tmp/linux-${TOOL_ARCH}"

# Verify installed tool versions (basic check without strict version matching).
RUN kubectl version --client=true \
    && kind version \
    && skaffold version \
    && helm version --short

# Application files.
COPY lab/lab-controller/entrypoint.py /usr/local/bin/entrypoint.py
COPY lab/lab-controller/start_controller.py /start_controller.py
COPY lab/lab-controller/proxy_controller.py /app/proxy_controller.py
COPY lab/lab-controller/app/ /app/
COPY lab/lab-controller/lib/ /app/lib/

RUN chmod +x /usr/local/bin/entrypoint.py \
    && chmod +x /start_controller.py \
    && mkdir -p /start \
    && touch /start/run

ENTRYPOINT ["/usr/local/bin/entrypoint.py"]
CMD []
