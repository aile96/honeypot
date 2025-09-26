# Immagine base
FROM python:3.12-slim

# ===== Build-time args (parametrizzabili da Skaffold) =====
ARG DEFAULT_GROUP="cluster"
ARG DEFAULT_ADV_NAME="KC1 – Safe Mining Emulation"
ARG DEFAULT_CALDERA_URL="http://caldera.dock:8888"
ARG DEFAULT_ADV_LIST="KC1 – Image@cluster, KC2 – WiFi@underlay, KC3 – FlagATT@outside, KC4 – CRSocket@outside"
ARG DEFAULT_PLANNER="atomic"
ARG DEFAULT_OP_NAME_PREFIX="kc"
ARG DEFAULT_CALDERA_KEY="ADMIN123"

# ===== Dipendenze di sistema minime =====
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl iproute2 \
 && rm -rf /var/lib/apt/lists/*

# ===== App =====
WORKDIR /app
COPY auto_starter.py /app/auto_starter.py
COPY entrypoint.sh   /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# ===== Default ENV (possono essere override a runtime) =====
ENV PYTHONUNBUFFERED=1 \
    GROUP="${DEFAULT_GROUP}" \
    ADV_NAME="${DEFAULT_ADV_NAME}" \
    CALDERA_URL="${DEFAULT_CALDERA_URL}" \
    ADV_LIST="${DEFAULT_ADV_LIST}" \
    PLANNER="${DEFAULT_PLANNER}" \
    OP_NAME_PREFIX="${DEFAULT_OP_NAME_PREFIX}" \
    CALDERA_KEY="${DEFAULT_CALDERA_KEY}"

# ===== Healthcheck verso Caldera (opzionale) =====
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=10 \
  CMD curl -fsS "$CALDERA_URL" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
