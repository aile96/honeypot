FROM registry:5000/checkout:2.0.2 AS app_orig

# Stage 2: runtime con Envoy
FROM envoyproxy/envoy:v1.31-latest

# tools minimi
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates jq bash coreutils gettext-base \
 && rm -rf /var/lib/apt/lists/*

# workspace
RUN mkdir -p /usr/src/app /etc/envoy /etc/envoy/lua /logs

# binario app
COPY --from=app_orig /usr/src/app/checkout /usr/src/app/checkout

# config Envoy (template) + Lua + entrypoint
COPY envoy.yaml.tpl /etc/envoy/envoy.yaml.tpl
COPY lua/capture.lua /etc/envoy/lua/capture.lua
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# env configurabili a runtime
ENV LISTEN_PORT=8080 \
    APP_PORT=18080 \
    SHADOW_ADDR="frontend-proxy.dmz" \
    SHADOW_PORT=8080 \
    PATH_LOG="/malicious" \
    MAX_BODY_BYTES=65536

EXPOSE 8080
CMD ["/entrypoint.sh"]