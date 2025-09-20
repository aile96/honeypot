FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nmap curl jq dnsutils ca-certificates \
        iproute2 wget docker.io dsniff \
        tcpdump procps unzip && \
    rm -rf /var/lib/apt/lists/*

ENV CALDERA_URL="http://caldera.dock:8888" \
    GROUP="underlay" \
    GRPCURL_VERSION=1.9.1

RUN wget -q https://github.com/fullstorydev/grpcurl/releases/download/v${GRPCURL_VERSION}/grpcurl_${GRPCURL_VERSION}_linux_x86_64.tar.gz \
    && tar -xzf grpcurl_${GRPCURL_VERSION}_linux_x86_64.tar.gz -C /usr/local/bin \
    && rm grpcurl_${GRPCURL_VERSION}_linux_x86_64.tar.gz

COPY ./scripts /opt/caldera
COPY ./utils /utils

RUN chmod -R +x /opt/caldera

CMD ["/opt/caldera/start.sh"]