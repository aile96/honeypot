FROM python:3.12-slim@sha256:9e01bf1ae5db7649a236da7be1e94ffbbbdd7a93f867dd0d8d5720d9e1f89fab

ARG CRICTL_VERSION="v1.30.0"
ARG RCE_ENABLED="true"

ENV RCE_ENABLED="$RCE_ENABLED"

# Install sshd + tini for a clean PID1
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
 openssh-server=1:10.0p1-7 tini=0.19.0-3+b5 curl=8.14.1-2+deb13u2 containerd=1.7.24~ds1-6+deb13u1 docker.io=26.1.5+dfsg1-9+b11 unzip=6.0-29 jq=1.7.1-6+deb13u1 \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
 opentelemetry-api==1.30.0 \
 opentelemetry-sdk==1.30.0 \
 opentelemetry-exporter-otlp-proto-grpc==1.30.0

# Basic SSH hardening (pubkey only; you can adjust as needed)
RUN sed -ri 's/^#?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config \
 && sed -ri 's/^#?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
 && mkdir -p /root/.ssh

RUN curl -L https://github.com/kubernetes-sigs/cri-tools/releases/download/${CRICTL_VERSION}/crictl-${CRICTL_VERSION}-linux-amd64.tar.gz \
    | tar zx -C /usr/local/bin

# App
WORKDIR /app
COPY server.py /app/server.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Expose SSH and the TCP server (SMTP-like) on port 25
EXPOSE 4222 25

# Use tini as entrypoint and our script as CMD
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/local/bin/entrypoint.sh"]
