# Debian slim + Samba and smbclient (for healthcheck)
FROM debian:stable-slim@sha256:4448d44b91bf4a13cb1b4e02d9d5f87ed40621d6e33f0ae7b6ddf71d57e29364

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Europe/Rome

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        samba=2:4.22.6+dfsg-0+deb13u1 smbclient=2:4.22.6+dfsg-0+deb13u1 tini=0.19.0-3+b5 ca-certificates=20250419 tzdata=2025b-4+deb13u1 procps=2:4.0.4-9 netcat-openbsd=1.229-1 gettext-base=0.23.1-2 iproute2=6.15.0-1 && \
    rm -rf /var/lib/apt/lists/*

# Standard directories: /data (config, state) and /share (content)
RUN mkdir -p /data /share /run/samba
RUN chown -R 10001:10001 /share /data
RUN chmod 0775 /data /share

# Copy template and entrypoint
COPY smb.conf.tmpl /etc/samba/smb.conf.tmpl
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Disable NetBIOS (nmbd), use only smbd/445
EXPOSE 445/tcp

# Start via tini + entrypoint
ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]
CMD ["smbd","--foreground","--no-process-group","--configfile=/etc/samba/smb.conf","--debug-stdout"]
