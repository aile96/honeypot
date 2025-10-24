FROM debian:bookworm-slim

ARG DOCKER_DAEMON="0"
ARG CALDERA_URL="http://caldera.dock:8888"
ARG GROUP="underlay"
ARG LOG_NS="mem"
ARG ATTACKED_NS="dmz"
ARG AUTH_NS="app"
ARG ATTACKERADDR="prova.dmz.svc.cluster.local"
ARG DATA_PATH="/tmp/KCData"
ARG WAIT="1"

# Building
ENV WAIT="$WAIT" \
    CALDERA_URL="$CALDERA_URL" \
    GROUP="$GROUP" \
    DATA_PATH="$DATA_PATH" \
    DOCKER_DAEMON="$DOCKER_DAEMON"

ENV LOG_NS="$LOG_NS" \
    ATTACKED_NS="$ATTACKED_NS" \
    AUTH_NS="$AUTH_NS" \
    ATTACKERADDR="$ATTACKERADDR"

ENV HOSTREGISTRY="registry:5000" \
    ARP_VICTIM="load-generator" \
    FRONTEND_PROXY_IP="172.18.0.200" \
    REGISTRY_USER="testuser" \
    REGISTRY_PASS="testpassword"

ENV CONTROL_PLANE_NODE="kind-control-plane" \
    CONTROL_PLANE_PORT="6443" \
    ATTACKER_NODE="kind-worker" \
    NSPROTO="$AUTH_NS" \
    NSCREDS="$LOG_NS"

# Install utilities and dnsmasq + python deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nmap curl jq dnsutils ca-certificates openssl \
        iproute2 wget docker.io dsniff tcpdump etcd-client \
        procps unzip openssh-client sshpass netcat-openbsd \
        dnsmasq python3 python3-pip && \
    rm -rf /var/lib/apt/lists/*

# Create data dir and logdir
RUN mkdir -p "$DATA_PATH" /opt/caldera /utils /var/log/myservices

COPY ./scripts /opt/caldera
COPY ./utils /utils

# Copy entrypoint and python server
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY dns_respond_generator.sh /usr/local/bin/dns_respond_generator.sh
COPY server_8080.py /usr/local/bin/server_8080.py

RUN chmod -R +x /opt/caldera /usr/local/bin/docker-entrypoint.sh /usr/local/bin/dns_respond_generator.sh /usr/local/bin/server_8080.py

RUN update-ca-certificates

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["/opt/caldera/start.sh"]

# Expose ports for DNS and for the HTTP server
EXPOSE 53/udp 53/tcp 8080
