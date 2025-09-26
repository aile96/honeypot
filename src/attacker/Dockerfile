FROM debian:bookworm-slim

ARG CALDERA_URL="http://caldera.dock:8888"
ARG GROUP="underlay"
ARG GRPCURL_VERSION=1.9.1
ARG LOAD_BALANCER_IP=172.18.0.201
ARG SMTP_IP=172.18.0.200

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nmap curl jq dnsutils ca-certificates \
        iproute2 wget docker.io dsniff tcpdump \
        procps unzip openssh-client sshpass netcat-openbsd && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /analysis

ENV CALDERA_URL="$CALDERA_URL" \
    GROUP="$GROUP" \
    GRPCURL_VERSION="$GRPCURL_VERSION" \
    SMTP_IP="$SMTP_IP" \
    LOAD_BALANCER_IP="$LOAD_BALANCER_IP"

RUN wget -q https://github.com/fullstorydev/grpcurl/releases/download/v${GRPCURL_VERSION}/grpcurl_${GRPCURL_VERSION}_linux_x86_64.tar.gz \
    && tar -xzf grpcurl_${GRPCURL_VERSION}_linux_x86_64.tar.gz -C /usr/local/bin \
    && rm grpcurl_${GRPCURL_VERSION}_linux_x86_64.tar.gz

COPY ./scripts /opt/caldera
COPY ./utils /utils

RUN chmod -R +x /opt/caldera

RUN update-ca-certificates

CMD ["/opt/caldera/start.sh"]