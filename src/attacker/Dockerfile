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
ARG KC0101="sleep 1"
ARG KC0102="sleep 1"
ARG KC0103="sleep 1"
ARG KC0104="sleep 1"
ARG KC0105="sleep 1"
ARG KC0106="sleep 1"
ARG KC0107="sleep 1"
ARG KC0108="sleep 1"

# Building
ENV WAIT="$WAIT" \
    CALDERA_URL="$CALDERA_URL" \
    GROUP="$GROUP" \
    DATA_PATH="$DATA_PATH" \
    DOCKER_DAEMON="$DOCKER_DAEMON" \
    KC0101="$KC0101" \
    KC0102="$KC0102" \
    KC0103="$KC0103" \
    KC0104="$KC0104" \
    KC0105="$KC0105" \
    KC0106="$KC0106" \
    KC0107="$KC0107" \
    KC0108="$KC0108"

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
    K8S_IMAGE="registry.k8s.io/kube-apiserver:v1.30.0" \
    NSPROTO="$AUTH_NS" \
    NSCREDS="$LOG_NS" \
    NSPAYMT="pay" \
    NSDATA="dat"

# Install utilities and dnsmasq + python deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nmap=7.93+dfsg1-1 curl=7.88.1-10+deb12u14 jq=1.6-2.1+deb12u1 dnsutils=1:9.18.44-1~deb12u1 ca-certificates=20230311+deb12u1 openssl=3.0.18-1~deb12u2 \
        iproute2=6.1.0-3 wget=1.21.3-1+deb12u1 docker.io=20.10.24+dfsg1-1+deb12u1+b3 dsniff=2.4b1+debian-31 tcpdump=4.99.3-1 etcd-client=3.4.23-4+b4 \
        procps=2:4.0.2-3 unzip=6.0-28 openssh-client=1:9.2p1-2+deb12u7 sshpass=1.09-1+b1 netcat-openbsd=1.219-1 \
        dnsmasq=2.90-4~deb12u1 python3=3.11.2-1+b1 python3-pip=23.0.1+dfsg-1 && \
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
