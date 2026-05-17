FROM debian:bookworm-slim@sha256:98f4b71de414932439ac6ac690d7060df1f27161073c5036a7553723881bffbe

ARG DOCKER_DAEMON="0"
ARG CALDERA_URL="http://caldera.dock:8888"
ARG GROUP="underlay"
ARG LOG_NS="mem"
ARG ATTACKED_NS="dmz"
ARG AUTH_NS="app"
ARG ATTACKERADDR="prova.dmz.svc.cluster.local"
ARG DATA_PATH="/tmp/KCData"
ARG WAIT="1"
ARG ATTACKER_START_SCRIPT="/opt/caldera/start.py"
ARG ATTACKER_EXECUTION_SCRIPT="wait.sh"

# Building
ENV WAIT="$WAIT" \
    CALDERA_URL="$CALDERA_URL" \
    GROUP="$GROUP" \
    DATA_PATH="$DATA_PATH" \
    DOCKER_DAEMON="$DOCKER_DAEMON" \
    ATTACKER_START_SCRIPT="$ATTACKER_START_SCRIPT" \
    ATTACKER_EXECUTION_DIR="/opt/execution" \
    ATTACKER_EXECUTION_SCRIPT="$ATTACKER_EXECUTION_SCRIPT"

ENV LOG_NS="$LOG_NS" \
    ATTACKED_NS="$ATTACKED_NS" \
    AUTH_NS="$AUTH_NS" \
    ATTACKERADDR="$ATTACKERADDR"

ENV REGISTRY_NAME="registry" \
    REGISTRY_PORT="5000" \
    ARP_VICTIM="load-generator" \
    FRONTEND_PROXY_IP="172.18.0.200" \
    REGISTRY_USER="testuser" \
    REGISTRY_PASS="testpassword"

ENV CONTROL_PLANE_NODE="kind-control-plane" \
    CONTROL_PLANE_PORT="6443" \
    K8S_IMAGE="registry.k8s.io/kube-apiserver:v1.30.0" \
    NSPROTO="$AUTH_NS" \
    NSCREDS="$LOG_NS" \
    NSPAYMT="pay" \
    NSDATA="dat"

ENV ATTACKER_LIB_DIR="/opt/attacker-lib" \
    PYTHONPATH="/opt/attacker-lib"

# Install utilities and dnsmasq + python deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nmap=7.93+dfsg1-1 curl=7.88.1-10+deb12u14 jq=1.6-2.1+deb12u1 dnsutils=1:9.18.47-1~deb12u1 ca-certificates=20230311+deb12u1 openssl=3.0.19-1~deb12u2 \
        iproute2=6.1.0-3 wget=1.21.3-1+deb12u1 docker.io=20.10.24+dfsg1-1+deb12u1+b6 dsniff=2.4b1+debian-31 tcpdump=4.99.3-1 etcd-client=3.4.23-4+b4 \
        procps=2:4.0.2-3 unzip=6.0-28 openssh-client=1:9.2p1-2+deb12u7 sshpass=1.09-1+b1 netcat-openbsd=1.219-1 \
        dnsmasq=2.90-4~deb12u2 socat xxd python3=3.11.2-1+b1 python3-pip=23.0.1+dfsg-1 && \
    rm -rf /var/lib/apt/lists/*

# Create data dir and logdir
RUN mkdir -p "$DATA_PATH" /opt/caldera /opt/execution /opt/attacker-lib /utils /usr/local/bin/services /var/log/myservices

COPY ./scripts /opt/caldera
COPY ./execution /opt/execution
COPY ./lib /opt/attacker-lib
COPY ./utils /utils
COPY ./services /usr/local/bin/services

# Copy entrypoint
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod -R +x /opt/caldera /opt/execution /opt/attacker-lib /usr/local/bin/services /usr/local/bin/docker-entrypoint.sh

RUN update-ca-certificates

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Expose ports for DNS and for the HTTP server
EXPOSE 53/udp 53/tcp 8080
