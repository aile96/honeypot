# Sidecar di mirroring HTTP basato su NGINX
FROM nginx:1.25-alpine

# envsubst per templating
RUN apk add --no-cache bash gettext

# Copia template e entrypoint
COPY nginx.conf.tmpl /etc/nginx/templates/nginx.conf.tmpl
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Esponi la porta del sidecar (ingresso richieste dall'app)
EXPOSE 8080

# Variabili con default sensati (puoi override in Pod env:)
# PRIMARY_IP:PORT  es. 10.10.10.5:8080
# MIRROR_HOST:PORT es. mirror.svc.cluster.local:8081
ENV PRIMARY_ADDR="127.0.0.1:8080" \
    MIRROR_ADDR="prova:8080" \
    CONNECT_TIMEOUT=2s \
    READ_TIMEOUT=60s \
    SEND_TIMEOUT=60s

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
