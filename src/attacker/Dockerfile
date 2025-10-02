FROM debian:bookworm-slim

ARG DATA_PATH="/tmp/KCData"
ARG GROUP="underlay"
ARG LOG_ENDPOINT="http://opensearch.mem:9200"
ARG NAMESPACE2="dmz"
ARG MIRRORADDR="prova.dmz.svc.cluster.local:8080"
ARG REWRITE_LINE="rewrite name auth.app.svc.cluster.local image-provider.dmz.svc.cluster.local"
ARG HOSTREGISTRY="registry:5000"
ARG LOAD_BALANCER_IP="172.18.0.201"
ARG API_SERVER="https://kind-cluster-control-plane:6443"
ARG NSPROTO="app"
ARG NSCREDS="mem"
ARG CMNAME="flagd-credentials-ui"
ARG FLAGHOST="flagd:4000"
ARG CALDERA_URL="http://caldera.dock:8888"
ARG GRPCURL_VERSION="1.9.1"
ARG SMTP_IP="172.18.0.200"

ENV DATA_PATH="$DATA_PATH" \
    GROUP="$GROUP" \
    LOG_ENDPOINT="$LOG_ENDPOINT" \
    NAMESPACE2="$NAMESPACE2" \
    MIRRORADDR="$MIRRORADDR" \
    REWRITE_LINE="$REWRITE_LINE" \
    HOSTREGISTRY="$HOSTREGISTRY" \
    LOAD_BALANCER_IP="$LOAD_BALANCER_IP" \
    API_SERVER="$API_SERVER" \
    NSPROTO="$NSPROTO" \
    NSCREDS="$NSCREDS" \
    CMNAME="$CMNAME" \
    FLAGHOST="$FLAGHOST" \
    CALDERA_URL="$CALDERA_URL" \
    GRPCURL_VERSION="$GRPCURL_VERSION" \
    SMTP_IP="$SMTP_IP"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nmap curl jq dnsutils ca-certificates \
        iproute2 wget docker.io dsniff tcpdump \
        procps unzip openssh-client sshpass netcat-openbsd && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p $DATA_PATH

RUN wget -q https://github.com/fullstorydev/grpcurl/releases/download/v${GRPCURL_VERSION}/grpcurl_${GRPCURL_VERSION}_linux_x86_64.tar.gz \
    && tar -xzf grpcurl_${GRPCURL_VERSION}_linux_x86_64.tar.gz -C /usr/local/bin \
    && rm grpcurl_${GRPCURL_VERSION}_linux_x86_64.tar.gz

COPY ./scripts /opt/caldera
COPY ./utils /utils

RUN chmod -R +x /opt/caldera

RUN update-ca-certificates

CMD ["/opt/caldera/start.sh"]