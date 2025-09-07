FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nmap curl jq dnsutils ca-certificates \
        iproute2 wget docker.io && \
    rm -rf /var/lib/apt/lists/*

ENV CALDERA_URL="http://caldera.dock:8888" \
    GROUP="underlay"

COPY ./scripts /opt/caldera
COPY ./utils /utils

RUN chmod -R +x /opt/caldera

CMD ["/opt/caldera/start.sh"]