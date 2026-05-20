# HTTP mirroring sidecar based on NGINX
FROM nginx:1.25-alpine@sha256:516475cc129da42866742567714ddc681e5eed7b9ee0b9e9c015e464b4221a00

# envsubst for templating
RUN apk add --no-cache bash=5.2.21-r0 gettext=0.22.3-r0

# Copy template and entrypoint
COPY nginx.conf.tmpl /etc/nginx/templates/nginx.conf.tmpl
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Expose the sidecar port (entry point for app requests)
EXPOSE 8080

# Variables with sensible defaults (can be overridden in Pod env:)
# PRIMARY_IP:PORT  e.g. 10.10.10.5:8080
# MIRROR_HOST:PORT e.g. mirror.svc.cluster.local:8081
ENV PRIMARY_ADDR="127.0.0.1:8080" \
    MIRROR_ADDR="prova:8080" \
    CONNECT_TIMEOUT=2s \
    READ_TIMEOUT=60s \
    SEND_TIMEOUT=60s

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
