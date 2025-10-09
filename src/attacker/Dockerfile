FROM debian:bookworm-slim

ARG DOCKER_DAEMON="0"
ARG CALDERA_URL="http://caldera.dock:8888"
ARG GROUP="underlay"
ARG LOG_NS="mem"
ARG ATTACKED_NS="dmz"
ARG AUTH_NS="app"
ARG MIRRORADDR="prova.dmz.svc.cluster.local:8080"

ENV DATA_PATH="/tmp/KCData" \
    DOCKER_DAEMON="$DOCKER_DAEMON"

ENV GROUP="$GROUP" \
    WAIT="1" \
    CALDERA_URL="$CALDERA_URL" \
    LOG_NS="$LOG_NS" \
    ATTACKED_NS="$ATTACKED_NS" \
    AUTH_NS="$AUTH_NS" \
    MIRRORADDR="$MIRRORADDR"

ENV HOSTREGISTRY="registry:5000" \
    ARP_VICTIM="load-generator" \
    LOAD_BALANCER_IP="172.18.0.200"

ENV API_SERVER="https://kind-cluster-control-plane:6443" \
    NSPROTO="$AUTH_NS" \
    NSCREDS="$LOG_NS" \
    GRPCURL_VERSION="$GRPCURL_VERSION" \
    SMTP_IP="$SMTP_IP"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nmap curl jq dnsutils ca-certificates openssl \
        iproute2 wget docker.io dsniff tcpdump etcd-client \
        procps unzip openssh-client sshpass netcat-openbsd && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p "$DATA_PATH"

COPY ./scripts /opt/caldera
COPY ./utils /utils

RUN chmod -R +x /opt/caldera

RUN update-ca-certificates

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["/opt/caldera/start.sh"]