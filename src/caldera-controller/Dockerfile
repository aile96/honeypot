# Immagine base
FROM python:3.12-slim@sha256:9e01bf1ae5db7649a236da7be1e94ffbbbdd7a93f867dd0d8d5720d9e1f89fab

# ===== Build-time args =====
ARG ATT_OUT="attacker"
ARG ATT_IN="deploy/test-image"
ARG ATT_NS="tst"
ARG DEFAULT_GROUP="cluster"
ARG DEFAULT_ADV_NAME="KC1 – Safe Mining Emulation"
ARG DEFAULT_CALDERA_URL="http://caldera.dock:8888"
ARG DEFAULT_ADV_LIST="KC1 – Image@cluster, KC2 – WiFi@outside, KC3 – FlagATT@outside, KC4 – CRSocket@outside, KC5 – Certificate@outside, KC6 – Etcd@outside"
ARG DEFAULT_PLANNER="atomic"
ARG DEFAULT_OP_NAME_PREFIX="kc"
ARG DEFAULT_CALDERA_KEY="ADMIN123"
ARG KUBECTL_VERSION="v1.35.1"
ARG KUBECTL_SHA256="36e2f4ac66259232341dd7866952d64a958846470f6a9a6a813b9117bd965207"

# ===== Minimum system dependencies =====
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates=20250419 curl=8.14.1-2+deb13u2 iproute2=6.15.0-1 docker.io=26.1.5+dfsg1-9+b11 docker-cli=26.1.5+dfsg1-9+b11 \
 && rm -rf /var/lib/apt/lists/*
ENV DOCKER_HOST=unix:///var/run/docker.sock

# ===== App =====
WORKDIR /app
COPY auto_starter.py /app/auto_starter.py
COPY entrypoint.sh   /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# ===== Install kubectl =====
RUN curl -fsSLo /usr/local/bin/kubectl \
      https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl \
 && echo "${KUBECTL_SHA256}  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod +x /usr/local/bin/kubectl

# ===== Default ENV =====
ENV PYTHONUNBUFFERED=1 \
    GROUP="${DEFAULT_GROUP}" \
    ADV_NAME="${DEFAULT_ADV_NAME}" \
    CALDERA_URL="${DEFAULT_CALDERA_URL}" \
    ADV_LIST="${DEFAULT_ADV_LIST}" \
    PLANNER="${DEFAULT_PLANNER}" \
    OP_NAME_PREFIX="${DEFAULT_OP_NAME_PREFIX}" \
    CALDERA_KEY="${DEFAULT_CALDERA_KEY}" \
    ATT_OUT="$ATT_OUT" \
    ATT_IN="$ATT_IN" \
    ATT_NS="$ATT_NS"

RUN mkdir -p /scripts /results

# ===== Healthcheck to Caldera =====
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=10 \
  CMD curl -fsS "$CALDERA_URL" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
