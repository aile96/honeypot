FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      dnsmasq iptables iproute2 procps ca-certificates gettext-base iputils-ping curl \
    && rm -rf /var/lib/apt/lists/*

# Copiamo i template (con placeholder ${VAR})
COPY dnsmasq.conf.tmpl /etc/dnsmasq.conf.tmpl
COPY dnsmasq-eth1.conf.tmpl /etc/dnsmasq-eth1.conf.tmpl

# Entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 53/udp 53/tcp
ENTRYPOINT ["/entrypoint.sh"]
