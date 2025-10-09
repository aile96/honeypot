# Debian slim + Samba e smbclient (per healthcheck)
FROM debian:stable-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Europe/Rome

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        samba smbclient tini ca-certificates tzdata procps netcat-openbsd gettext-base iproute2 && \
    rm -rf /var/lib/apt/lists/*

# Dir standard: /data (config, state) e /share (contenuti)
RUN mkdir -p /data /share /run/samba
RUN chown -R 10001:10001 /share /data
RUN chmod 0775 /data /share

# Copia template e entrypoint
COPY smb.conf.tmpl /etc/samba/smb.conf.tmpl
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Evita NetBIOS (nmbd), usiamo solo smbd/445
EXPOSE 445/tcp

# Avvia tramite tini + entrypoint
ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]
CMD ["smbd","--foreground","--no-process-group","--configfile=/etc/samba/smb.conf","--debug-stdout"]
