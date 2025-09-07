# Immagine base
FROM python:3.12-slim

# ===== Build-time args (parametrizzabili da Skaffold) =====
ARG DEFAULT_GROUP="cluster"
ARG DEFAULT_ADV_NAME="KC1 – Safe Mining Emulation"
ARG DEFAULT_CALDERA_URL="http://localhost:8888"
# UID non-root
ARG UID=10001

# ===== Dipendenze di sistema minime =====
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# ===== Utente non-root =====
RUN useradd -r -u ${UID} -m appuser

# ===== App =====
WORKDIR /app
COPY auto_starter.py /app/auto_starter.py
COPY entrypoint.sh   /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# ===== Default ENV (possono essere override a runtime) =====
ENV PYTHONUNBUFFERED=1 \
    GROUP="${DEFAULT_GROUP}" \
    ADV_NAME="${DEFAULT_ADV_NAME}" \
    CALDERA_URL="${DEFAULT_CALDERA_URL}"

# ===== Healthcheck verso Caldera (opzionale) =====
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=10 \
  CMD curl -fsS "$CALDERA_URL" || exit 1

USER appuser
ENTRYPOINT ["/app/entrypoint.sh"]
